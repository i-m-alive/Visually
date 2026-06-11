"""
intelligence.py — POST /dashboards/{id}/intelligence-data

Executes every widget's sql_query in parallel and returns fresh rows +
columns for each widget so the frontend intelligence agent has real data
instead of stale cached snapshots.

Optional body: { date_from: "YYYY-MM-DD", date_to: "YYYY-MM-DD" }
When provided, a WHERE clause is injected into each widget's SQL to
filter the time window before the rows are returned.
"""

import asyncio
import re
import uuid
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.models.database_connections import DatabaseConnection

router = APIRouter(tags=["intelligence"])

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
