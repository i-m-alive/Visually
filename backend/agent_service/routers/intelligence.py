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
import hashlib
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

from shared.redis_client import get_redis

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.models.database_connections import DatabaseConnection
from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
from shared.bedrock_client import BEDROCK_SONNET_MODEL, _BEDROCK_EXECUTOR

# Direct SQL execution — bypasses the HTTP query executor service entirely.
# Works because agent_service/main.py adds backend/ to sys.path and loads .env
# (so the correct DATABASE_URL / ENCRYPTION_KEY are already in the environment).
# Each driver import is isolated so a missing optional package (redshift_connector,
# aiomysql) does not prevent the postgres driver from loading.
try:
    from shared.encryption import decrypt as _decrypt
    _DECRYPT_AVAILABLE = True
except ImportError as _ie:
    _DECRYPT_AVAILABLE = False
    print(f"[intelligence] ⚠ encryption unavailable ({_ie})", flush=True)

try:
    from query_executor.drivers.postgres import execute_postgres as _direct_postgres
    _POSTGRES_DIRECT = True
except ImportError as _ie:
    _POSTGRES_DIRECT = False
    print(f"[intelligence] ⚠ postgres direct driver unavailable ({_ie})", flush=True)

try:
    from query_executor.drivers.mysql import execute_mysql as _direct_mysql
    _MYSQL_DIRECT = True
except ImportError:
    _MYSQL_DIRECT = False

try:
    from query_executor.drivers.redshift import execute_redshift as _direct_redshift
    _REDSHIFT_DIRECT = True
except ImportError:
    _REDSHIFT_DIRECT = False

_DIRECT_EXECUTION = _DECRYPT_AVAILABLE and (_POSTGRES_DIRECT or _MYSQL_DIRECT or _REDSHIFT_DIRECT)
print(
    f"[intelligence] direct execution: enabled={_DIRECT_EXECUTION}"
    f"  decrypt={_DECRYPT_AVAILABLE}"
    f"  postgres={_POSTGRES_DIRECT}  mysql={_MYSQL_DIRECT}  redshift={_REDSHIFT_DIRECT}",
    flush=True,
)

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
    # Reload .env every call so rotating STS session tokens take effect
    # without restarting the backend process.
    # Explicit absolute path — find_dotenv can silently miss on Windows.
    try:
        from dotenv import load_dotenv as _ld
        import os as _os
        _env = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', '..', '.env')
        if _os.path.exists(_env):
            _ld(_env, override=True)
    except ImportError:
        pass
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
    force: bool = False  # bypass cache and run fresh Bedrock call


# Cache TTL and lock timeout for intelligence results
_INTEL_CACHE_TTL  = int(os.getenv("INTELLIGENCE_CACHE_TTL",  "1800"))  # 30 min result cache
# Lock TTL must stay BELOW _INTEL_LOCK_WAIT so an orphaned lock (e.g. left by a
# killed/--reload'd process) self-expires before a waiter gives up — letting the
# waiter take over fast instead of hanging the full wait window. A *live* holder
# keeps its lock fresh via _heartbeat_lock(), so this short TTL never cuts off a
# healthy run even though Bedrock can take ~160 s.
_INTEL_LOCK_TTL       = int(os.getenv("INTELLIGENCE_LOCK_TTL",       "120"))  # < LOCK_WAIT; refreshed by heartbeat
_INTEL_LOCK_HEARTBEAT = float(os.getenv("INTELLIGENCE_LOCK_HEARTBEAT", "30.0"))  # re-extend lock TTL this often while computing
_INTEL_LOCK_POLL  = float(os.getenv("INTELLIGENCE_LOCK_POLL", "2.0"))  # poll interval when waiting
_INTEL_LOCK_WAIT  = int(os.getenv("INTELLIGENCE_LOCK_WAIT",   "300"))  # max seconds to wait for a peer


async def _heartbeat_lock(redis, lock_key: str) -> None:
    """Keep a held lock fresh while we compute so a live run never looks orphaned.

    Re-extends the lock's TTL every _INTEL_LOCK_HEARTBEAT seconds. If the process
    dies, the heartbeat stops and the short TTL lapses within one interval, so a
    waiting peer can take over quickly."""
    try:
        while True:
            await asyncio.sleep(_INTEL_LOCK_HEARTBEAT)
            try:
                await redis.expire(lock_key, _INTEL_LOCK_TTL)
            except Exception:
                return  # Redis unavailable — stop heartbeating, lock will lapse
    except asyncio.CancelledError:
        return


@router.post("/intelligence/analyze")
async def intelligence_analyze(body: AnalyzeRequest, redis=Depends(get_redis)):
    """
    Dedicated intelligence analysis endpoint with Redis result cache and
    request coalescing.

    - First request for a given prompt hash: runs Bedrock, stores result in Redis (30 min TTL)
    - Concurrent duplicate requests: wait for the first to finish and return the cached result
    - Subsequent requests within TTL: return cached result immediately (no Bedrock call)
    """
    # Stable key = SHA-256 of the exact prompt (includes all widget data)
    prompt_hash = hashlib.sha256(body.prompt.encode()).hexdigest()[:24]
    cache_key = f"intel:result:{prompt_hash}"
    lock_key  = f"intel:lock:{prompt_hash}"
    canvas    = body.canvas_name or "?"

    # ── 1. Fast-path: cached result (skipped when force=True) ───────────────
    if not body.force:
        try:
            cached = await redis.get(cache_key)
            if cached:
                raw = cached.decode() if isinstance(cached, bytes) else cached
                print(
                    f"[intelligence/analyze] CACHE HIT  hash={prompt_hash[:8]}"
                    f"  canvas={canvas}  len={len(raw)}",
                    flush=True,
                )
                return {"text": raw}
        except Exception as _re:
            print(f"[intelligence/analyze] Redis read failed (non-fatal): {_re}", flush=True)
    else:
        print(
            f"[intelligence/analyze] FORCE REFRESH  hash={prompt_hash[:8]}"
            f"  canvas={canvas}  — deleting cached result",
            flush=True,
        )
        try:
            await redis.delete(cache_key)
        except Exception:
            pass

    # ── 2. Try to acquire lock (prevent thundering herd) ─────────────────────
    lock_acquired = False
    try:
        lock_acquired = await redis.set(lock_key, "1", nx=True, ex=_INTEL_LOCK_TTL)
    except Exception as _re:
        print(f"[intelligence/analyze] Redis lock failed (non-fatal): {_re}", flush=True)
        lock_acquired = True  # treat as acquired so we proceed

    if not lock_acquired:
        # Another instance is already computing — wait for cached result
        t_wait = time.time()
        print(
            f"[intelligence/analyze] WAITING for peer  hash={prompt_hash[:8]}"
            f"  canvas={canvas}",
            flush=True,
        )
        while time.time() - t_wait < _INTEL_LOCK_WAIT:
            await asyncio.sleep(_INTEL_LOCK_POLL)
            try:
                cached = await redis.get(cache_key)
                if cached:
                    raw = cached.decode() if isinstance(cached, bytes) else cached
                    print(
                        f"[intelligence/analyze] PEER RESULT  waited={time.time()-t_wait:.1f}s"
                        f"  hash={prompt_hash[:8]}  canvas={canvas}",
                        flush=True,
                    )
                    return {"text": raw}
                # Check if lock is gone (peer failed) — take over
                lock_exists = await redis.exists(lock_key)
                if not lock_exists:
                    lock_acquired = await redis.set(lock_key, "1", nx=True, ex=_INTEL_LOCK_TTL)
                    if lock_acquired:
                        print(f"[intelligence/analyze] TOOK OVER after peer failure  hash={prompt_hash[:8]}", flush=True)
                        break
            except Exception:
                break  # Redis unavailable — fall through and compute
        else:
            # Timed out waiting — fall through and compute anyway
            print(f"[intelligence/analyze] WAIT TIMEOUT  hash={prompt_hash[:8]}, computing independently", flush=True)

    # ── 3. Compute via Bedrock ────────────────────────────────────────────────
    t0 = time.time()
    print(
        f"[intelligence/analyze] START  prompt_len={len(body.prompt)}  canvas={canvas}"
        f"  hash={prompt_hash[:8]}",
        flush=True,
    )
    # Keep the lock fresh for the duration of a live run (only if we own it).
    heartbeat_task = (
        asyncio.create_task(_heartbeat_lock(redis, lock_key)) if lock_acquired else None
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
        # Store result in cache
        try:
            await redis.set(cache_key, raw.encode() if isinstance(raw, str) else raw, ex=_INTEL_CACHE_TTL)
        except Exception as _re:
            print(f"[intelligence/analyze] Redis write failed (non-fatal): {_re}", flush=True)
        return {"text": raw}
    except Exception as exc:
        print(f"[intelligence/analyze] FAILED after {time.time()-t0:.1f}s: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(status_code=502, detail=f"Bedrock call failed: {exc}")
    finally:
        # Stop the heartbeat before releasing the lock.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
        # Release lock whether we succeeded or failed
        try:
            if lock_acquired:
                await redis.delete(lock_key)
        except Exception:
            pass


# ─── /dashboards/{id}/schema-context ────────────────────────────────────────

_TABLE_RE = re.compile(
    r'\b(?:FROM|JOIN)\s+((?:[a-zA-Z_][a-zA-Z0-9_]*\.)?[a-zA-Z_][a-zA-Z0-9_]*)',
    re.IGNORECASE,
)


# CTE names defined via `WITH x AS (...)` (incl. chained `), y AS (...)`) — these
# appear after FROM/JOIN but are NOT real tables, so exclude them.
_CTE_RE = re.compile(r'\b([a-zA-Z_]\w*)\s+AS\s*\(', re.IGNORECASE)


def _extract_table_names(sql: str) -> set[str]:
    """Pull bare table names (strip schema prefix) from a SQL statement,
    excluding CTE names defined in the same query."""
    sql = sql or ""
    cte = {m.lower() for m in _CTE_RE.findall(sql)}
    names: set[str] = set()
    for m in _TABLE_RE.finditer(sql):
        bare = m.group(1).split(".")[-1].lower()
        if bare in cte:
            continue
        names.add(bare)
    return names


def _extract_qualified_table_names(sql: str) -> list[str]:
    """Like _extract_table_names but KEEPS the schema-qualified form
    (e.g. 'staging.orders'), excluding CTE names. Needed for live discovery
    sampling — a schema-qualified DB rejects bare table names."""
    sql = sql or ""
    cte = {m.lower() for m in _CTE_RE.findall(sql)}
    out: list[str] = []
    seen: set[str] = set()
    for m in _TABLE_RE.finditer(sql):
        name = m.group(1)
        if name.split(".")[-1].lower() in cte or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    return out


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

    # --- prefer the enriched schema cache (same warm path as the chat copilot) ---
    # It folds in LLM table semantics, the SQL-confirmed FK graph, and example values,
    # and is instantly warm after a .vly import (no crawl/LLM). Falls back to the raw
    # metadata rows below when no snapshot/cache is available or no table matches.
    try:
        enriched_tables = await _tables_from_enriched_cache(db, connection_id, table_names)
    except Exception as exc:
        enriched_tables = None
        print(f"[intelligence/schema-context] enriched path failed (non-fatal): {exc}", flush=True)
    if enriched_tables:
        print(
            f"[intelligence/schema-context] dashboard={dashboard_id[:8]}  "
            f"tables_referenced={len(table_names)}  tables_found={len(enriched_tables)}  "
            f"source=enriched_cache",
            flush=True,
        )
        return {
            "tables": enriched_tables,
            "referenced_tables": sorted(table_names),
            "source": "enriched_cache",
        }

    # --- fetch table metadata (raw-row fallback) ---
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
        "source": "metadata_rows",
    }


def _project_enriched_tables(enriched, table_names: set) -> list:
    """
    Project an EnrichedSchema down to just the report's referenced tables, in the EXACT
    shape the intelligence prompt builder (frontend buildSchemaContextBlock) expects.

    Preserves the report-table prioritization (only tables in widget SQL) and recovers
    per-column FK targets from the relationship graph's edge conditions.
    """
    ref_full = {t.lower() for t in table_names}
    ref_bare = {t.split(".")[-1].lower() for t in table_names}

    # column → fk_target map, parsed from relationship-graph edge conditions
    # ("a.col = b.tcol"). Both directions are recorded so either endpoint resolves.
    fk_map: dict = {}
    edges = getattr(enriched.relationship_graph, "edges", {}) or {}
    seen_conditions: set = set()
    for _src, neighbors in edges.items():
        for _tgt, cond in (neighbors or {}).items():
            if not cond or cond in seen_conditions:
                continue
            seen_conditions.add(cond)
            m = re.match(r"\s*([\w.]+)\.(\w+)\s*=\s*([\w.]+)\.(\w+)\s*", cond)
            if not m:
                continue
            lt, lc, rt, rc = m.groups()
            fk_map.setdefault(lt, {}).setdefault(lc, f"{rt}.{rc}")
            fk_map.setdefault(rt, {}).setdefault(rc, f"{lt}.{lc}")

    out: list = []
    for ct in (enriched.compact_tables or []):
        qname = ct.get("name") or ""
        if not (qname.lower() in ref_full or qname.split(".")[-1].lower() in ref_bare):
            continue
        sem = (enriched.table_semantics or {}).get(qname, {}) or {}
        tbl_fk = fk_map.get(qname, {})

        columns: list = []
        for c in (ct.get("columns") or []):
            cname = c.get("name") or ""
            st = c.get("semantic_type")
            top_values = (c.get("stats") or {}).get("top_values") or []
            examples: list = []
            for row in top_values:
                if isinstance(row, dict):
                    examples.extend(row.values())
                else:
                    examples.append(row)
            columns.append({
                "name": cname,
                "business_name": None,
                "description": c.get("description"),
                "type": st,
                "is_metric": st == "metric",
                "is_dimension": st == "dimension",
                "fk_target": tbl_fk.get(cname),
                "examples": examples[:5],
            })

        out.append({
            "name": qname,
            "business_name": sem.get("business_name"),
            "description": sem.get("purpose") or ct.get("description"),
            "grain": sem.get("grain"),
            "is_fact": sem.get("is_fact_table"),
            "key_metrics": sem.get("key_metric_cols") or [],
            "key_dimensions": sem.get("key_dimension_cols") or [],
            "key_dates": sem.get("key_date_cols") or [],
            "columns": columns,
        })
    return out


async def _tables_from_enriched_cache(db: AsyncSession, connection_id, table_names: set):
    """
    Build report-scoped schema context from the warm enriched cache (get_or_build).
    Returns a list of table dicts, or None when there is no snapshot/cache or no
    referenced table matches — in which case the caller falls back to raw metadata rows.
    """
    from shared.models.schema_snapshots import SchemaSnapshot
    from agent_service.agents import schema_cache as _sc

    snap = (await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == connection_id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not snap or not snap.schema_document:
        return None

    conn = (await db.execute(
        select(DatabaseConnection).where(DatabaseConnection.id == connection_id)
    )).scalar_one_or_none()
    db_type = "postgresql"
    if conn is not None:
        db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)

    enriched = await _sc.get_or_build(str(connection_id), snap.schema_document, db_type)
    if not enriched or not getattr(enriched, "compact_tables", None):
        return None

    projected = _project_enriched_tables(enriched, table_names)
    return projected or None


QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
_ROW_LIMIT = 500          # rows per widget — enough for all 18 skills
_QUERY_TIMEOUT = 40.0     # seconds per widget query (warm cluster; pre-warm wakes it first)
# Cap simultaneous widget queries. The executor opens one DB connection per
# request, so firing all N at once (a 30+ widget dashboard) saturates the pool
# and the slow ones time out. Run a bounded batch instead — env-overridable.
_INTEL_QUERY_CONCURRENCY = int(os.getenv("INTELLIGENCE_QUERY_CONCURRENCY", "6"))

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
    force: bool = False               # bypass the cached result and re-query live


# Cached intelligence-data lives this long. Running 32 live aggregation queries
# on a small Redshift Serverless cluster takes minutes; caching the assembled
# result means only the FIRST load (or an explicit force-refresh) pays that cost,
# every subsequent page view / re-open is instant. Env-overridable.
_INTEL_DATA_CACHE_TTL = int(os.getenv("INTELLIGENCE_DATA_CACHE_TTL", "900"))  # 15 min


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


def _build_result(widget_id: str, rows: list, columns: list) -> dict:
    """Build a normalised success result dict from raw rows/columns."""
    labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
    values: list = []
    if len(columns) > 1:
        values = [r.get(columns[1]) for r in rows]
    elif len(columns) == 1:
        values = [r.get(columns[0]) for r in rows]
    return {
        "widget_id": widget_id,
        "ok": True,
        "rows": rows,
        "columns": columns,
        "labels": labels,
        "values": values,
    }


async def _execute_direct(conn: "DatabaseConnection", widget_id: str, sql: str) -> dict:
    """Execute SQL in-process using the connection object — no HTTP round-trip."""
    password = ""
    if conn.encrypted_password:
        try:
            password = _decrypt(conn.encrypted_password)
        except Exception as exc:
            return {"widget_id": widget_id, "ok": False, "error": f"decrypt failed: {exc}"}

    db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)

    try:
        if db_type == "postgresql":
            if not _POSTGRES_DIRECT:
                return {"widget_id": widget_id, "ok": False, "error": "postgres direct driver not available"}
            result = await _direct_postgres(
                host=conn.host or "localhost",
                port=conn.port or 5432,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                sql=sql,
                timeout_seconds=int(_QUERY_TIMEOUT),
                row_limit=_ROW_LIMIT,
                ssl=conn.ssl_enabled,
            )
        elif db_type == "redshift":
            if not _REDSHIFT_DIRECT:
                return {"widget_id": widget_id, "ok": False, "error": "redshift direct driver not available"}
            iam_role_arn = None
            if conn.connection_options and isinstance(conn.connection_options, dict):
                iam_role_arn = conn.connection_options.get("iam_role_arn")
            result = await _direct_redshift(
                host=conn.host or "localhost",
                port=conn.port or 5439,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                ssl=conn.ssl_enabled,
                sql=sql,
                iam_role_arn=iam_role_arn,
                row_limit=_ROW_LIMIT,
            )
        elif db_type == "mysql":
            if not _MYSQL_DIRECT:
                return {"widget_id": widget_id, "ok": False, "error": "mysql direct driver not available"}
            result = await _direct_mysql(
                host=conn.host or "localhost",
                port=conn.port or 3306,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                sql=sql,
                timeout_seconds=int(_QUERY_TIMEOUT),
                row_limit=_ROW_LIMIT,
                ssl=conn.ssl_enabled,
            )
        else:
            return {"widget_id": widget_id, "ok": False, "error": f"Unsupported db_type: {db_type}"}

        exec_error = result.get("error")
        rows: list[dict] = result.get("rows") or []
        columns: list[str] = result.get("columns") or []
        if exec_error:
            print(f"[intelligence-data] widget={widget_id[:8]} DIRECT_ERROR: {exec_error}", flush=True)
            return {"widget_id": widget_id, "ok": False, "error": exec_error}

        sample = rows[0] if rows else {}
        print(
            f"[intelligence-data] widget={widget_id[:8]}  rows={len(rows)}  cols={len(columns)}  mode=direct"
            f"  col_names={columns}"
            f"  sample_row={dict(list(sample.items())[:5])}",
            flush=True,
        )
        return _build_result(widget_id, rows, columns)

    except Exception as exc:
        print(f"[intelligence-data] widget={widget_id[:8]} DIRECT_EXCEPTION: {exc}", flush=True)
        return {"widget_id": widget_id, "ok": False, "error": str(exc)[:200]}


async def _run_widget_sql(
    connection_id: str,
    widget_id: str,
    sql: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    conn_obj: Optional["DatabaseConnection"] = None,
) -> dict:
    """Execute one widget's SQL. Uses direct in-process driver when conn_obj is
    supplied (preferred); falls back to HTTP query executor otherwise."""
    # Optionally inject date range filter
    working_sql = sql
    if date_from and date_to:
        working_sql = _inject_date_range(working_sql, date_from, date_to)

    # Strip any existing LIMIT and cap at _ROW_LIMIT so we always get full data
    cleaned = re.sub(r'\bLIMIT\s+\d+\b', '', working_sql, flags=re.IGNORECASE).rstrip('; ')
    capped_sql = f"{cleaned} LIMIT {_ROW_LIMIT}"

    # ── Direct execution (preferred) ──────────────────────────────────────────
    if conn_obj is not None and _DIRECT_EXECUTION:
        return await _execute_direct(conn_obj, widget_id, capped_sql)

    # ── HTTP fallback ─────────────────────────────────────────────────────────
    # Use a slightly longer client timeout than the executor timeout so the
    # executor has time to return its own error before the client gives up.
    http_timeout = _QUERY_TIMEOUT + 10
    try:
        async with httpx.AsyncClient(timeout=http_timeout) as client:
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
                exec_error = data.get("error")
                rows: list[dict] = data.get("rows") or []
                columns: list[str] = data.get("columns") or []
                if exec_error:
                    print(
                        f"[intelligence-data] widget={widget_id[:8]} EXEC_ERROR: {exec_error}",
                        flush=True,
                    )
                    return {"widget_id": widget_id, "ok": False, "error": exec_error}
                sample = rows[0] if rows else {}
                print(
                    f"[intelligence-data] widget={widget_id[:8]}  rows={len(rows)}  cols={len(columns)}  mode=http"
                    f"  col_names={columns}"
                    f"  sample_row={dict(list(sample.items())[:5])}",
                    flush=True,
                )
                return _build_result(widget_id, rows, columns)
            error_msg = f"executor HTTP {resp.status_code}"
    except TimeoutError:
        error_msg = f"HTTP executor timed out after {http_timeout}s"
    except Exception as exc:
        error_msg = repr(exc)[:200] or type(exc).__name__

    print(f"[intelligence-data] widget={widget_id[:8]} FAILED: {error_msg}", flush=True)
    return {"widget_id": widget_id, "ok": False, "error": error_msg}


def _conn_db_type(conn) -> str:
    if conn is None:
        return ""
    return conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)


async def _run_widget_offline(
    db: AsyncSession,
    dashboard_id: str,
    widget_id: str,
    sql: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Execute one widget's SQL against the bundled DuckDB snapshot (offline mode)."""
    from shared.offline_store import execute_offline_sql
    working_sql = sql
    if date_from and date_to:
        working_sql = _inject_date_range(working_sql, date_from, date_to)
    cleaned = re.sub(r'\bLIMIT\s+\d+\b', '', working_sql, flags=re.IGNORECASE).rstrip('; ')
    capped_sql = f"{cleaned} LIMIT {_ROW_LIMIT}"
    res = await execute_offline_sql(db, dashboard_id, capped_sql, _ROW_LIMIT)
    if res.get("error"):
        print(f"[intelligence-data] widget={widget_id[:8]} OFFLINE_ERROR: {res['error']}", flush=True)
        return {"widget_id": widget_id, "ok": False, "error": res["error"]}
    rows = res.get("rows") or []
    columns = res.get("columns") or []
    print(f"[intelligence-data] widget={widget_id[:8]}  rows={len(rows)}  cols={len(columns)}  mode=offline", flush=True)
    return _build_result(widget_id, rows, columns)


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
    force = bool(body.force) if body else False

    # --- cache check (Redis) ---
    # Assembling this result runs ~32 live Redshift queries (minutes on a cold
    # Serverless cluster). Cache the assembled payload so only the first load /
    # explicit force-refresh pays that cost; repeat opens are instant.
    cache_key = f"intel_data:{dashboard_id}:{date_from or '*'}:{date_to or '*'}"
    _redis = await get_redis()
    if _redis is not None and not force:
        try:
            cached = await _redis.get(cache_key)
            if cached:
                print(f"[intelligence-data] cache HIT {cache_key}", flush=True)
                return json.loads(cached)
        except Exception as exc:  # noqa: BLE001
            print(f"[intelligence-data] cache read failed (non-fatal): {exc}", flush=True)

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

    # Store full DatabaseConnection objects so _run_widget_sql can execute directly
    conn_obj_map: dict[str, "DatabaseConnection"] = {}
    if conn_ids:
        conns_result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id.in_(conn_ids))
        )
        for c in conns_result.scalars().all():
            conn_obj_map[str(c.id)] = c

    project_conn_id: Optional[str] = None
    project_conn_obj: Optional["DatabaseConnection"] = None
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
            project_conn_obj = pc

    # --- build task list (keep widget reference for chart_data fallback) ---
    tasks = []
    task_widgets: list = []  # parallel list — widget[i] corresponds to tasks[i]
    skipped = []
    for w in widgets:
        if not w.sql_query:
            skipped.append({"widget_id": str(w.id), "ok": False, "error": "no sql_query"})
            continue
        conn_id = str(w.connection_id) if w.connection_id else project_conn_id
        conn_obj = conn_obj_map.get(conn_id) if conn_id else project_conn_obj
        if not conn_id:
            skipped.append({"widget_id": str(w.id), "ok": False, "error": "no connection"})
            continue
        if _conn_db_type(conn_obj) == "vly_offline":
            tasks.append(_run_widget_offline(db, dashboard_id, str(w.id), w.sql_query, date_from, date_to))
        else:
            tasks.append(_run_widget_sql(conn_id, str(w.id), w.sql_query, date_from, date_to, conn_obj))
        task_widgets.append(w)

    print(
        f"[intelligence-data] dashboard={dashboard_id[:8]}  "
        f"executing={len(tasks)}  skipped={len(skipped)}  concurrency={_INTEL_QUERY_CONCURRENCY}"
        + (f"  date_range={date_from}→{date_to}" if date_from else ""),
        flush=True,
    )

    # --- offline mode: warm the DuckDB snapshot once so the fan-out hits cache ---
    # (Building it inside concurrent tasks would share the request's DB session
    # unsafely; one warmup builds + caches it, after which queries never touch the
    # session.) Skips the Redshift live pre-warm below entirely.
    offline_any = any(
        _conn_db_type(c) == "vly_offline"
        for c in list(conn_obj_map.values()) + ([project_conn_obj] if project_conn_obj else [])
    )
    if offline_any and tasks:
        try:
            from shared.offline_store import execute_offline_sql as _warm_offline
            await _warm_offline(db, dashboard_id, "SELECT 1", 1)
            print(f"[intelligence-data] offline store warmed dashboard={dashboard_id[:8]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[intelligence-data] offline warm failed (non-fatal): {exc}", flush=True)

    # --- pre-warm the database before fan-out (live mode only) ---
    # Redshift Serverless auto-pauses; the first connect after idle takes 60–120s.
    # Without this, all N widget queries race a cold cluster and every one hits the
    # per-query timeout (the ReadTimeout storm seen in the logs). One warm-up SELECT 1
    # (with a long timeout) wakes it, so the real batch runs against a warm cluster.
    warm_conn_id = next((str(w.connection_id) for w in widgets if w.connection_id), project_conn_id)
    if warm_conn_id and tasks and not offline_any:
        try:
            async with httpx.AsyncClient(timeout=130.0) as client:
                await client.post(
                    f"{QUERY_EXECUTOR_URL}/execute",
                    json={"connection_id": warm_conn_id, "sql": "SELECT 1",
                          "row_limit": 1, "timeout_seconds": 125},
                )
            print(f"[intelligence-data] pre-warm OK conn={warm_conn_id[:8]}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[intelligence-data] pre-warm failed (non-fatal): {exc}", flush=True)

    # --- run SQL queries with bounded concurrency ---
    # Coroutines are lazy: building the list above didn't start them; each only
    # runs once awaited inside the semaphore, so at most N execute at a time.
    sem = asyncio.Semaphore(_INTEL_QUERY_CONCURRENCY)

    async def _bounded(coro):
        async with sem:
            return await coro

    results = (
        list(await asyncio.gather(*[_bounded(t) for t in tasks])) if tasks else []
    )

    # --- fallback: substitute stored chart_data for widgets that returned 0 rows ---
    fallback_count = 0
    for i, res in enumerate(results):
        if res.get("ok") and len(res.get("rows") or []) == 0:
            w = task_widgets[i]
            cd = w.chart_data or {}
            fb_rows: list[dict] = cd.get("rows") or []
            fb_cols: list[str] = cd.get("columns") or []
            if fb_rows and fb_cols:
                results[i] = {
                    "widget_id": res["widget_id"],
                    "ok": True,
                    "rows": fb_rows,
                    "columns": fb_cols,
                    "labels": cd.get("labels") or [],
                    "values": cd.get("values") or [],
                    "source": "chart_data_cache",
                }
                fallback_count += 1
        elif not res.get("ok"):
            # Also try chart_data fallback for failed SQL
            w = task_widgets[i]
            cd = w.chart_data or {}
            fb_rows = cd.get("rows") or []
            fb_cols = cd.get("columns") or []
            if fb_rows and fb_cols:
                results[i] = {
                    "widget_id": res["widget_id"],
                    "ok": True,
                    "rows": fb_rows,
                    "columns": fb_cols,
                    "labels": cd.get("labels") or [],
                    "values": cd.get("values") or [],
                    "source": "chart_data_cache",
                }
                fallback_count += 1

    if fallback_count:
        print(
            f"[intelligence-data] chart_data fallback applied to {fallback_count} widget(s)",
            flush=True,
        )

    payload = {"widget_data": results + skipped}

    # Cache the assembled result so repeat opens skip the live-query cost. Only
    # cache when at least one widget returned real (non-fallback) live data, so we
    # never pin a page of pure cached-fallback values as if it were fresh.
    has_live = any(r.get("ok") and r.get("source") != "chart_data_cache" for r in results)
    if _redis is not None and has_live:
        try:
            await _redis.set(cache_key, json.dumps(payload, default=str), ex=_INTEL_DATA_CACHE_TTL)
            print(f"[intelligence-data] cached {cache_key} ttl={_INTEL_DATA_CACHE_TTL}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[intelligence-data] cache write failed (non-fatal): {exc}", flush=True)

    return payload


# ─── /dashboards/{id}/intelligence-discovery ─────────────────────────────────
# Discovery stage: mine the report's underlying tables for NEW insights beyond the
# existing widgets. Samples each table, asks the LLM to propose analytical queries,
# executes them (DuckDB offline OR live DB), and returns ready-to-chart datasets.
# The frontend appends these as synthetic widgets so they flow through the normal
# orchestrator and surface as extra charts/sections.

_DISCOVERY_TTL = int(os.getenv("INTELLIGENCE_DISCOVERY_TTL", "1800"))
_DISCOVERY_TABLE_CAP = int(os.getenv("INTELLIGENCE_DISCOVERY_TABLE_CAP", "8"))
_DISCOVERY_ROW_CAP = int(os.getenv("INTELLIGENCE_DISCOVERY_ROW_CAP", "200"))
_DISCOVERY_CONCURRENCY = int(os.getenv("INTELLIGENCE_DISCOVERY_CONCURRENCY", "4"))
# Proposal JSON for 6 queries of non-trivial SQL needs headroom; 2048 truncated it.
_DISCOVERY_MAX_TOKENS = int(os.getenv("INTELLIGENCE_DISCOVERY_MAX_TOKENS", "4096"))

_DISCOVERY_SYSTEM_PROMPT = """You are a senior data analyst exploring a database to surface NEW business insights that are NOT already shown in an existing report. You will be given the report's tables (columns + sample rows), the dialect, and the titles of widgets that already exist.

Propose analytical queries that reveal fresh, decision-useful findings (trends, segments, concentrations, ratios, outliers, cohorts) the existing widgets do NOT already cover. Each query MUST:
- be a SINGLE read-only statement starting with SELECT or WITH (no INSERT/UPDATE/DELETE/DDL, no semicolons, no multiple statements),
- aggregate/group so it returns at most ~50 rows (use GROUP BY, COUNT, SUM, AVG, etc.),
- reference only the given tables and columns,
- be valid in the stated DIALECT.

Do NOT duplicate an existing widget's angle. Output ONLY raw JSON:
{"discoveries":[{"title":"<short insight title>","chart_type":"bar|line|area|pie|donut|table","sql":"<one SELECT/WITH query>","why":"<one line: the business question it answers>"}]}"""

_DISCOVERY_VALID_CHARTS = {"bar", "line", "area", "pie", "donut", "table"}


def _extract_json_obj(text: str):
    """Tolerant JSON-object extraction from model output."""
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\n?|```$", "", s, flags=re.MULTILINE).strip()
    for cand in (s, re.sub(r",(\s*[}\]])", r"\1", s)):
        try:
            return json.loads(cand)
        except Exception:
            pass
    # balanced-brace scan
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(re.sub(r",(\s*[}\]])", r"\1", s[start:i + 1]))
                except Exception:
                    start = -1
    return None


def _is_safe_select(sql: str) -> bool:
    s = (sql or "").strip().rstrip(";").strip()
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return False
    if ";" in s:
        return False
    banned = ("insert ", "update ", "delete ", "drop ", "alter ", "create ",
              "truncate ", "grant ", "revoke ", "attach ", "copy ", "pragma ", " into ")
    return not any(b in low for b in banned)


async def _discovery_exec(db, offline: bool, dashboard_id: str, conn_id: Optional[str], sql: str, cap: int) -> dict:
    """Run a read query in the dashboard's mode (offline DuckDB or live executor)."""
    if offline:
        from shared.offline_store import execute_offline_sql
        return await execute_offline_sql(db, dashboard_id, sql, cap)
    if not conn_id:
        return {"rows": [], "columns": [], "error": "no connection"}
    from agent_service.utils.http_clients import call_query_executor
    return await call_query_executor(conn_id, sql, row_limit=cap, timeout_seconds=40)


class DiscoveryRequest(BaseModel):
    max_queries: int = 6
    force: bool = False


@router.post("/dashboards/{dashboard_id}/intelligence-discovery")
async def intelligence_discovery(
    dashboard_id: str,
    body: Optional[DiscoveryRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """Propose + execute NEW analytical queries against the report's tables and
    return chart-ready datasets ('discovered widgets')."""
    max_queries = max(1, min(10, body.max_queries if body else 6))
    force = bool(body.force) if body else False

    try:
        dash_uuid = uuid.UUID(dashboard_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dashboard_id")

    dash = (await db.execute(select(Dashboard).where(Dashboard.id == dash_uuid))).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    widgets = (await db.execute(select(Widget).where(Widget.dashboard_id == dash_uuid))).scalars().all()

    # ── resolve connection + mode ───────────────────────────────────────────
    conn_obj = None
    conn_ids = {w.connection_id for w in widgets if w.connection_id}
    if conn_ids:
        conn_obj = (await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == next(iter(conn_ids)))
        )).scalar_one_or_none()
    if conn_obj is None:
        lc_conn = (dash.layout_config or {}).get("connection_id") if isinstance(dash.layout_config, dict) else None
        if lc_conn:
            try:
                conn_obj = (await db.execute(
                    select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(str(lc_conn)))
                )).scalar_one_or_none()
            except ValueError:
                conn_obj = None
    if conn_obj is None:
        pc = (await db.execute(
            select(DatabaseConnection)
            .where(DatabaseConnection.project_id == dash.project_id)
            .where(DatabaseConnection.is_active == True)  # noqa: E712
            .limit(1)
        )).scalar_one_or_none()
        conn_obj = pc
    if conn_obj is None:
        return {"discoveries": [], "message": "No connection or offline data for discovery"}

    offline = _conn_db_type(conn_obj) == "vly_offline"
    conn_id = str(conn_obj.id)
    dialect = "duckdb" if offline else (conn_obj.db_type.value if hasattr(conn_obj.db_type, "value") else str(conn_obj.db_type))

    # ── table list ───────────────────────────────────────────────────────────
    # Offline: use the AUTHORITATIVE bundled table names (no phantom CTE/alias
    # names, exact schema-qualified form). Live: extract from widget SQL.
    table_names: list[str] = []
    if offline:
        from shared.models.vly_offline import VlyOfflineTable
        recs = (await db.execute(
            select(VlyOfflineTable.table_name).where(VlyOfflineTable.dashboard_id == dash_uuid)
        )).all()
        table_names = [r[0] for r in recs][:_DISCOVERY_TABLE_CAP]
    else:
        # Live: keep schema-qualified names — a schema-qualified DB (e.g. Redshift
        # with a 'staging' schema) rejects bare table names, so sampling would fail.
        seen: set = set()
        for w in widgets:
            for t in _extract_qualified_table_names(w.sql_query or w.base_sql or ""):
                if t.lower() not in seen:
                    seen.add(t.lower())
                    table_names.append(t)
        table_names = table_names[:_DISCOVERY_TABLE_CAP]
    if not table_names:
        return {"discoveries": [], "message": "No tables available for discovery"}

    # ── cache check ──────────────────────────────────────────────────────────
    redis = await get_redis()
    titles = sorted((w.title or "") for w in widgets)
    cache_seed = dashboard_id + "|" + "|".join(table_names) + "|" + "|".join(titles) + f"|{max_queries}|{dialect}"
    cache_key = f"intel:discovery:{hashlib.sha256(cache_seed.encode()).hexdigest()[:24]}"
    if redis is not None and not force:
        try:
            cached = await redis.get(cache_key)
            if cached:
                print(f"[intel-discovery] CACHE HIT dashboard={dashboard_id[:8]}", flush=True)
                return json.loads(cached)
        except Exception:
            pass

    # ── warm offline store once (so concurrent samples hit cache, not db) ────
    if offline:
        try:
            await _discovery_exec(db, True, dashboard_id, conn_id, "SELECT 1", 1)
        except Exception:
            pass

    # ── sample each table ────────────────────────────────────────────────────
    schema_blocks: list[str] = []
    for t in table_names:
        res = await _discovery_exec(db, offline, dashboard_id, conn_id, f"SELECT * FROM {t} LIMIT 3", 3)
        if res.get("error"):
            continue
        cols = (res.get("columns") or [])[:40]
        rows = res.get("rows") or []
        sample = [{c: r.get(c) for c in cols} for r in rows[:3]]
        schema_blocks.append(
            f"TABLE {t}\n  columns: {', '.join(cols)}\n  sample_rows: {json.dumps(sample, default=str)[:1500]}"
        )
    if not schema_blocks:
        print(f"[intel-discovery] dashboard={dashboard_id[:8]}  offline={offline}  could not sample any of: {table_names}", flush=True)
        return {"discoveries": [], "message": "Could not sample any tables"}

    existing = ", ".join(f'"{t}"' for t in titles if t) or "(none)"
    user_msg = (
        f"DIALECT: {dialect}\n\n"
        f"TABLES:\n" + "\n\n".join(schema_blocks) +
        f"\n\nEXISTING REPORT WIDGETS (do NOT duplicate these angles):\n{existing}\n\n"
        f"Propose up to {max_queries} NEW analytical queries as specified."
    )

    print(f"[intel-discovery] dashboard={dashboard_id[:8]}  offline={offline}  tables={len(schema_blocks)}  max_q={max_queries}", flush=True)
    try:
        raw = await _intel_bedrock_invoke(_DISCOVERY_SYSTEM_PROMPT, user_msg, max_tokens=_DISCOVERY_MAX_TOKENS, temperature=0.5)
    except Exception as exc:  # noqa: BLE001
        print(f"[intel-discovery] LLM failed: {exc}", flush=True)
        return {"discoveries": [], "message": f"LLM failed: {exc}"}

    parsed = _extract_json_obj(raw) or {}
    proposals = parsed.get("discoveries") or []
    proposals = [p for p in proposals if isinstance(p, dict) and _is_safe_select(p.get("sql", ""))][:max_queries]
    print(f"[intel-discovery] proposals={len(proposals)} (after safety filter)", flush=True)

    # ── execute proposals (bounded concurrency) ──────────────────────────────
    sem = asyncio.Semaphore(_DISCOVERY_CONCURRENCY)

    async def _run(p: dict) -> Optional[dict]:
        async with sem:
            res = await _discovery_exec(db, offline, dashboard_id, conn_id, p["sql"], _DISCOVERY_ROW_CAP)
        if res.get("error") or not (res.get("rows")):
            if res.get("error"):
                print(f"[intel-discovery] '{p.get('title')}' exec failed: {str(res['error'])[:120]}", flush=True)
            return None
        rows = res.get("rows") or []
        columns = res.get("columns") or []
        labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
        values = ([r.get(columns[1]) for r in rows] if len(columns) > 1
                  else [r.get(columns[0]) for r in rows] if columns else [])
        ct = str(p.get("chart_type", "bar")).lower()
        if ct not in _DISCOVERY_VALID_CHARTS:
            ct = "bar"
        return {
            "title": str(p.get("title") or "Discovered insight")[:120],
            "chart_type": ct,
            "sql": p["sql"],
            "why": str(p.get("why") or "")[:240],
            "chart_data": {"rows": rows, "columns": columns, "labels": labels, "values": values},
        }

    executed = [d for d in await asyncio.gather(*[_run(p) for p in proposals]) if d]
    print(f"[intel-discovery] executed OK={len(executed)}/{len(proposals)} dashboard={dashboard_id[:8]}", flush=True)

    out = {"discoveries": executed}
    if redis is not None and executed:
        try:
            await redis.set(cache_key, json.dumps(out, default=str), ex=_DISCOVERY_TTL)
        except Exception:
            pass
    return out
