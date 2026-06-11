"""
intelligence.py — Intelligence data + analysis endpoints

  POST /dashboards/{id}/intelligence-data
      Executes every widget's sql_query in parallel and returns fresh rows +
      columns for each widget so the frontend intelligence agent has real data
      instead of stale cached snapshots.

  POST /intelligence/analyze
      Dedicated Bedrock endpoint for the intelligence report.  Bypasses the
      chart-creation chat system prompt entirely so the model can focus on
      JSON generation with a 32 768-token output budget.

  GET  /dashboards/{id}/schema-context
      Returns table/column metadata for every table referenced in this
      dashboard's widget SQL queries so the AI prompt includes DDL context.
"""

import asyncio
import json
import re
import uuid
import os
import time
from typing import Optional

import boto3
import httpx
from botocore.config import Config as BotocoreConfig
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.models.database_connections import DatabaseConnection
from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
from shared.bedrock_client import BEDROCK_SONNET_MODEL, _BEDROCK_EXECUTOR

# Intelligence calls use a larger output budget than normal chart calls.
# Override via INTELLIGENCE_MAX_TOKENS env var if the model supports more.
_INTELLIGENCE_MAX_TOKENS = int(os.getenv("INTELLIGENCE_MAX_TOKENS", "16384"))

# Dedicated Bedrock config for intelligence calls — 5 min read timeout
# to accommodate large JSON responses (16 384 tokens ≈ 160 s at ~100 tok/s)
_INTEL_BEDROCK_CONFIG = BotocoreConfig(
    connect_timeout=10,
    read_timeout=300,
    retries={"max_attempts": 2},
)


def _get_intel_bedrock_client():
    access_key    = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key    = os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")
    region        = os.getenv("AWS_REGION", "us-east-1")
    kwargs: dict = {
        "service_name": "bedrock-runtime",
        "region_name": region,
        "config": _INTEL_BEDROCK_CONFIG,
    }
    if access_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key
    if session_token:
        kwargs["aws_session_token"] = session_token
    return boto3.client(**kwargs)


async def _intel_bedrock_invoke(
    system_prompt: str,
    user_message: str,
    max_tokens: int = _INTELLIGENCE_MAX_TOKENS,
    temperature: float = 0.3,
) -> str:
    """Bedrock invoke with 300 s read timeout — used ONLY by /intelligence/analyze."""
    def _invoke():
        client = _get_intel_bedrock_client()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_message},
                # Prefill forces the model to continue from "{" — eliminates markdown
                # fences and ensures the response is always raw JSON from the first char.
                {"role": "assistant", "content": "{"},
            ],
        }
        t0 = time.time()
        print(
            f"[intel-bedrock] → invoke  model={BEDROCK_SONNET_MODEL}"
            f"  max_tokens={max_tokens}  prompt_len={len(user_message)}",
            flush=True,
        )
        response = client.invoke_model(
            modelId=BEDROCK_SONNET_MODEL,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        result = json.loads(response["body"].read())
        content = result.get("content") or []
        # Prepend the prefill character — Bedrock returns only the continuation
        raw = "{" + (content[0].get("text", "") if content else "")
        usage = result.get("usage", {})
        stop_reason = result.get("stop_reason", "unknown")
        print(
            f"[intel-bedrock] ← done  {time.time()-t0:.1f}s"
            f"  stop_reason={stop_reason}"
            f"  in={usage.get('input_tokens','?')}  out={usage.get('output_tokens','?')}"
            f"  response_len={len(raw)}"
            f"  first500={raw[:500]!r}",
            flush=True,
        )
        if stop_reason == "max_tokens":
            print(
                f"[intel-bedrock] ⚠ TRUNCATED — response hit max_tokens={max_tokens}."
                f" Set INTELLIGENCE_MAX_TOKENS env var to increase the budget.",
                flush=True,
            )
        return raw

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_BEDROCK_EXECUTOR, _invoke)

router = APIRouter(tags=["intelligence"])

# ─── /intelligence/analyze ───────────────────────────────────────────────────

_INTELLIGENCE_SYSTEM_PROMPT = """You are a senior executive intelligence analyst. Your ONLY task is to analyze the provided business data and return a single valid JSON object.

CRITICAL RULES:
- Output ONLY raw JSON — no markdown fences (no ```), no explanation, no text before or after
- Never use sql_execute blocks or dashboard_action blocks
- Base every insight, number, and chart data row on the ACTUAL DATA provided in the prompt
- Use the exact column names, table descriptions, and sample values from the schema context
- When the prompt says "respond with only raw JSON", do exactly that — raw JSON, nothing else"""


class AnalyzeRequest(BaseModel):
    prompt: str
    canvas_name: Optional[str] = None


@router.post("/intelligence/analyze")
async def intelligence_analyze(body: AnalyzeRequest):
    """
    Dedicated intelligence analysis endpoint.

    Uses a dedicated Bedrock client with read_timeout=300 s so large JSON
    responses are never cut off mid-generation.  max_tokens=16 384 keeps the
    response well within what the model produces in <160 s.
    """
    t0 = time.time()
    print(
        f"[intelligence/analyze] START  prompt_len={len(body.prompt)}  canvas={body.canvas_name or '?'}",
        flush=True,
    )
    try:
        raw = await _intel_bedrock_invoke(
            system_prompt=_INTELLIGENCE_SYSTEM_PROMPT,
            user_message=body.prompt,
            temperature=0.3,
        )
        print(
            f"[intelligence/analyze] OK  response_len={len(raw)}  elapsed={time.time()-t0:.1f}s",
            flush=True,
        )
        return {"text": raw}
    except Exception as exc:
        print(f"[intelligence/analyze] FAILED after {time.time()-t0:.1f}s: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"Bedrock call failed: {exc}")


# ─── /dashboards/{id}/schema-context ────────────────────────────────────────

_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+((?:[a-zA-Z_][a-zA-Z0-9_]*\.)?[a-zA-Z_][a-zA-Z0-9_]*)',
    re.IGNORECASE,
)


def _extract_table_names(sql: str) -> set[str]:
    """Pull bare table names (strip schema prefix) from a SQL statement."""
    names: set[str] = set()
    for m in _TABLE_RE.finditer(sql):
        full = m.group(1)
        bare = full.split(".")[-1].lower()
        names.add(bare)
    return names


@router.get("/dashboards/{dashboard_id}/schema-context")
async def get_intelligence_schema_context(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return table/column metadata for every table referenced in this dashboard's
    widget SQL queries.  The intelligence agent includes this in the Bedrock
    prompt so the model understands the underlying database structure.
    """
    try:
        dash_uuid = uuid.UUID(dashboard_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dashboard_id")

    # --- load widgets ---
    dash_result = await db.execute(select(Dashboard).where(Dashboard.id == dash_uuid))
    dash = dash_result.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widgets_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == dash_uuid)
    )
    widgets = widgets_result.scalars().all()

    # --- collect table names from all widget SQL queries ---
    table_names: set[str] = set()
    for w in widgets:
        if w.sql_query:
            table_names.update(_extract_table_names(w.sql_query))

    if not table_names:
        return {"tables": [], "message": "No SQL queries found on widgets"}

    # --- find the active database connection for this project ---
    conn_ids: set[uuid.UUID] = {w.connection_id for w in widgets if w.connection_id}
    connection_id: Optional[uuid.UUID] = None
    if conn_ids:
        connection_id = next(iter(conn_ids))
    else:
        proj_conn = await db.execute(
            select(DatabaseConnection)
            .where(DatabaseConnection.project_id == dash.project_id)
            .where(DatabaseConnection.is_active == True)
            .limit(1)
        )
        pc = proj_conn.scalar_one_or_none()
        if pc:
            connection_id = pc.id

    if not connection_id:
        return {"tables": [], "message": "No database connection found"}

    # --- fetch table metadata ---
    tbl_rows = (await db.execute(
        select(SchemaTableMetadata)
        .where(SchemaTableMetadata.connection_id == connection_id)
        .where(SchemaTableMetadata.table_name.in_(list(table_names)))
    )).scalars().all()

    # Fallback: if exact match fails (qualified names), try LIKE matching
    if not tbl_rows and table_names:
        from sqlalchemy import or_
        conditions = [
            SchemaTableMetadata.table_name.ilike(f"%{t}") for t in table_names
        ]
        tbl_rows = (await db.execute(
            select(SchemaTableMetadata)
            .where(SchemaTableMetadata.connection_id == connection_id)
            .where(or_(*conditions))
        )).scalars().all()

    if not tbl_rows:
        return {"tables": [], "referenced_tables": sorted(table_names), "message": "No metadata found — run a schema crawl first"}

    # --- fetch column metadata for found tables ---
    found_table_names = [t.table_name for t in tbl_rows]
    col_rows = (await db.execute(
        select(SchemaColumnMetadata)
        .where(SchemaColumnMetadata.connection_id == connection_id)
        .where(SchemaColumnMetadata.table_name.in_(found_table_names))
        .order_by(SchemaColumnMetadata.table_name, SchemaColumnMetadata.column_name)
    )).scalars().all()

    cols_by_table: dict[str, list] = {}
    for c in col_rows:
        cols_by_table.setdefault(c.table_name, []).append({
            "name": c.column_name,
            "business_name": c.business_name,
            "description": c.description,
            "type": c.semantic_type,
            "is_metric": c.is_kpi_metric,
            "is_dimension": c.is_dimension,
            "fk_target": f"{c.fk_target_table}.{c.fk_target_column}" if c.fk_target_table else None,
            "examples": (c.example_values or [])[:5],
        })

    tables = [
        {
            "name": t.table_name,
            "business_name": t.business_name,
            "description": t.description,
            "grain": t.grain,
            "is_fact": t.is_fact_table,
            "key_metrics": t.key_metric_cols or [],
            "key_dimensions": t.key_dimension_cols or [],
            "key_dates": t.key_date_cols or [],
            "columns": cols_by_table.get(t.table_name, []),
        }
        for t in tbl_rows
    ]

    print(
        f"[intelligence/schema-context] dashboard={dashboard_id[:8]}  "
        f"tables_referenced={len(table_names)}  tables_found={len(tables)}",
        flush=True,
    )

    return {
        "tables": tables,
        "referenced_tables": sorted(table_names),
    }

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
_ROW_LIMIT = 500          # rows per widget — enough for all 18 skills
_QUERY_TIMEOUT = 20.0     # seconds per widget query

# Common date column name patterns (ORDER matters — more specific first)
_DATE_COL_RE = re.compile(
    r'\b(created_at|updated_at|order_date|transaction_date|event_date|'
    r'sale_date|invoice_date|due_date|started_at|ended_at|reported_at|'
    r'modified_at|purchase_date|ship_date|delivery_date|'
    r'date|timestamp|period|month|year|week|day)\b',
    re.IGNORECASE,
)


class IntelligenceRequest(BaseModel):
    date_from: Optional[str] = None   # "YYYY-MM-DD"
    date_to: Optional[str] = None     # "YYYY-MM-DD"


def _inject_date_range(sql: str, date_from: str, date_to: str) -> str:
    """
    Try to inject a date range WHERE clause into the SQL.

    Strategy:
    1. Find the first date-like column name referenced in the SQL.
    2. Insert  AND <col> BETWEEN '<from>' AND '<to>'  before the first
       ORDER BY / GROUP BY / HAVING / LIMIT clause (or append to end).
    3. If no date column is detected, return the original SQL unchanged.
    """
    match = _DATE_COL_RE.search(sql)
    if not match:
        return sql

    date_col = match.group(1)
    date_clause = f"{date_col} BETWEEN '{date_from}' AND '{date_to}'"

    # Position to insert: before the first ORDER/GROUP/HAVING/LIMIT
    end_match = re.search(
        r'\b(ORDER\s+BY|GROUP\s+BY|HAVING|LIMIT)\b',
        sql, re.IGNORECASE,
    )
    if end_match:
        pos = end_match.start()
        before = sql[:pos]
        after = sql[pos:]
        has_where = bool(re.search(r'\bWHERE\b', before, re.IGNORECASE))
        connector = 'AND' if has_where else 'WHERE'
        return f"{before} {connector} {date_clause} {after}"
    else:
        has_where = bool(re.search(r'\bWHERE\b', sql, re.IGNORECASE))
        connector = 'AND' if has_where else 'WHERE'
        return f"{sql} {connector} {date_clause}"


async def _run_widget_sql(
    connection_id: str,
    widget_id: str,
    sql: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Execute one widget's SQL via the query executor. Returns a result dict."""
    # Optionally inject date range filter
    working_sql = sql
    if date_from and date_to:
        working_sql = _inject_date_range(working_sql, date_from, date_to)

    # Strip any existing LIMIT and cap at _ROW_LIMIT so we always get full data
    cleaned = re.sub(r'\bLIMIT\s+\d+\b', '', working_sql, flags=re.IGNORECASE).rstrip('; ')
    capped_sql = f"{cleaned} LIMIT {_ROW_LIMIT}"

    try:
        async with httpx.AsyncClient(timeout=_QUERY_TIMEOUT) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={
                    "connection_id": connection_id,
                    "sql": capped_sql,
                    "row_limit": _ROW_LIMIT,
                    "timeout_seconds": int(_QUERY_TIMEOUT),
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                rows: list[dict] = data.get("rows") or []
                columns: list[str] = data.get("columns") or []
                # Build labels / values from first two columns for chart compat
                labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
                values: list = []
                if len(columns) > 1:
                    values = [r.get(columns[1]) for r in rows]
                elif len(columns) == 1:
                    values = [r.get(columns[0]) for r in rows]
                print(
                    f"[intelligence-data] widget={widget_id[:8]}  rows={len(rows)}  cols={len(columns)}",
                    flush=True,
                )
                return {
                    "widget_id": widget_id,
                    "ok": True,
                    "rows": rows,
                    "columns": columns,
                    "labels": labels,
                    "values": values,
                }
            error_msg = f"executor HTTP {resp.status_code}"
    except Exception as exc:
        error_msg = str(exc)[:200]

    print(
        f"[intelligence-data] widget={widget_id[:8]} FAILED: {error_msg}",
        flush=True,
    )
    return {"widget_id": widget_id, "ok": False, "error": error_msg}


@router.post("/dashboards/{dashboard_id}/intelligence-data")
async def get_intelligence_data(
    dashboard_id: str,
    body: Optional[IntelligenceRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Execute every widget's sql_query in parallel and return fresh chart_data.
    Called by the intelligence page before running the AI agent so all 18
    statistical skills operate on real, current data instead of stale DB cache.

    Optional body:
      { "date_from": "2024-01-01", "date_to": "2024-06-30" }
    """
    date_from = body.date_from if body else None
    date_to = body.date_to if body else None

    # --- load dashboard + widgets ---
    try:
        dash_uuid = uuid.UUID(dashboard_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dashboard_id")

    dash_result = await db.execute(select(Dashboard).where(Dashboard.id == dash_uuid))
    dash = dash_result.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widgets_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == dash_uuid)
    )
    widgets = widgets_result.scalars().all()

    if not widgets:
        return {"widget_data": []}

    # --- resolve connection per widget (fall back to project-level connection) ---
    conn_ids: set[uuid.UUID] = set()
    for w in widgets:
        if w.connection_id:
            conn_ids.add(w.connection_id)

    conn_map: dict[str, str] = {}
    if conn_ids:
        conns_result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id.in_(conn_ids))
        )
        for c in conns_result.scalars().all():
            conn_map[str(c.id)] = str(c.id)

    project_conn_id: Optional[str] = None
    if any(w.connection_id is None for w in widgets):
        proj_result = await db.execute(
            select(DatabaseConnection)
            .where(DatabaseConnection.project_id == dash.project_id)
            .where(DatabaseConnection.is_active == True)
            .limit(1)
        )
        pc = proj_result.scalar_one_or_none()
        if pc:
            project_conn_id = str(pc.id)

    # --- build task list ---
    tasks = []
    skipped = []
    for w in widgets:
        if not w.sql_query:
            skipped.append({"widget_id": str(w.id), "ok": False, "error": "no sql_query"})
            continue
        conn_id = conn_map.get(str(w.connection_id)) if w.connection_id else project_conn_id
        if not conn_id:
            skipped.append({"widget_id": str(w.id), "ok": False, "error": "no connection"})
            continue
        tasks.append(_run_widget_sql(conn_id, str(w.id), w.sql_query, date_from, date_to))

    print(
        f"[intelligence-data] dashboard={dashboard_id[:8]}  "
        f"executing={len(tasks)}  skipped={len(skipped)}"
        + (f"  date_range={date_from}→{date_to}" if date_from else ""),
        flush=True,
    )

    # --- run all SQL queries in parallel ---
    results = await asyncio.gather(*tasks) if tasks else []

    return {"widget_data": list(results) + skipped}
