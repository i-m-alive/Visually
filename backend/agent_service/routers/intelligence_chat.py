"""Intelligence Report Copilot router — intelligence_chat.py

FORKED FROM agent_service/routers/chat.py on 2026-06-18.

Serves ONLY the "Report Copilot" on the intelligence page. The Canvas Assistant
keeps using /agent/chat and /agent/chat/stream (chat.py). This router is a
self-contained copy so the Report Copilot pipeline can evolve independently.

Endpoints:
  POST   /intelligence/chat            — blocking
  POST   /intelligence/chat/stream     — SSE streaming
  DELETE /intelligence/chat/{session}  — clear conversation history

Shared infrastructure (imported, NOT forked):
  • shared.bedrock_client, shared.redis_client, shared.database, shared.models
  • agent_service.agents.schema_cache  (enriched schema cache)
  • query_executor / render_service     (via httpx)

Every log line is prefixed [intel_chat] so you can confirm — by grepping the
service logs — that the Report Copilot is exercising THIS forked path and not
the canvas chat router.
"""
import uuid
import json
import re
import os
import traceback
import types
from dataclasses import dataclass
from typing import Optional
import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from shared.database import get_db
from shared.redis_client import get_redis
from shared.models.schema_snapshots import SchemaSnapshot
from shared.models.database_connections import DatabaseConnection, DbType
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from agent_service.agents.intelligence_chat_agent import (
    IntelligenceChatAgent,
    _is_data_query_request,
    _is_chart_creation_request,
)
import agent_service.agents.schema_cache as _schema_cache
from shared.bedrock_client import (
    bedrock_invoke_stream, bedrock_invoke, BEDROCK_SONNET_MODEL,
    start_token_tracking, format_token_log,
)
from agent_service.agents.intent_parser import parse_intent
from agent_service.agents.nl_schema_router import route_query

router = APIRouter(tags=["intelligence-chat"])
_agent = IntelligenceChatAgent()

from agent_service.agents.root_cause_agent import RootCauseAgent  # noqa: E402
_root_cause = RootCauseAgent()

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
RENDER_SERVICE_URL = os.getenv("RENDER_SERVICE_URL", "http://localhost:3001")

print("[intel_chat] router loaded — Report Copilot endpoints registered "
      "(/intelligence/chat, /intelligence/chat/stream)", flush=True)


class IntelChatRequest(BaseModel):
    message: str
    project_id: str
    dashboard_id: Optional[str] = None
    session_id: Optional[str] = None
    connection_id: Optional[str] = None
    active_page_id: Optional[str] = None
    model_preference: Optional[str] = None  # 'opus' for deeper analysis
    # "report"  → schema scoped to the report's tables + 2-hop FK neighbours (default)
    # "database" → full enriched schema (query anything in the DB)
    scope: Optional[str] = "report"


class IntelChatResponse(BaseModel):
    session_id: str
    text: str
    inline_chart: Optional[dict] = None
    dashboard_action: Optional[dict] = None
    turn_count: int


async def _fetch_priority_columns_doc(connection_id: str, priority_tables: set) -> dict:
    """Layer 2: fetch real column names for priority tables from information_schema.
    Only called when the enriched schema cache is absent (cold start / missing crawl).
    Caps at 8 tables to avoid per-turn latency blowing up."""
    doc: dict = {}
    for tbl in list(priority_tables)[:8]:
        schema_part, _, table_part = tbl.rpartition('.')
        if not table_part:
            table_part = tbl
            schema_part = 'public'
        sql = (
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            f"WHERE table_name = '{table_part}'"
            + (f" AND table_schema = '{schema_part}'" if schema_part else "")
            + " ORDER BY ordinal_position LIMIT 80"
        )
        res = await _execute_sql(connection_id, sql)
        if res.get("error") or not res.get("rows"):
            continue
        cols = [str(r["column_name"]) for r in res["rows"] if r.get("column_name")]
        if cols:
            doc[tbl] = {"columns": cols, "used_by_widgets": [], "source": "live_fetch"}
    return doc


async def _load_report_tables_metadata(dashboard_id: str, db: AsyncSession) -> dict:
    """Layer 3 (read): load the _tables_metadata that was stored alongside the
    saved IntelligenceReport. Returns {} when none was persisted yet."""
    if not dashboard_id:
        return {}
    try:
        from shared.models.intelligence_report import IntelligenceReport
        row = (await db.execute(
            select(IntelligenceReport)
            .where(IntelligenceReport.dashboard_id == uuid.UUID(dashboard_id))
        )).scalar_one_or_none()
        if row and isinstance(row.analysis, dict):
            meta = row.analysis.get("_tables_metadata")
            if isinstance(meta, dict) and meta:
                return meta
    except Exception as exc:
        print(f"[intel_chat] ⚠ _load_report_tables_metadata failed (non-fatal): {exc}", flush=True)
    return {}


async def _collect_chat_context(req: "IntelChatRequest", db: AsyncSession, redis) -> dict:
    """Resolve everything a Report-Copilot turn needs: session, history, schema,
    enrichment, widgets, pages, priority tables, connection, model preference.
    All DB access lives here so the streaming generator never touches the session."""
    session_id = req.session_id or str(uuid.uuid4())
    print(
        f"[intel_chat] ▶ turn  session={session_id[:8]}  project={req.project_id[:8]}  "
        f"dashboard={'yes' if req.dashboard_id else 'no'}  scope={req.scope or 'report'}  "
        f"model_pref={req.model_preference or 'default'}  msg_len={len(req.message)}",
        flush=True,
    )

    history = await IntelligenceChatAgent.load_history(session_id, redis)
    memory = await IntelligenceChatAgent.load_memory(session_id, redis)
    schema_doc, connection_id_for_schema, db_type = await _get_schema_context(req.project_id, db)
    effective_connection_id = req.connection_id or connection_id_for_schema

    if not effective_connection_id and req.dashboard_id:
        fallback_conn_id = await _get_dashboard_connection_id(req.dashboard_id, db)
        if fallback_conn_id:
            effective_connection_id = fallback_conn_id
            if not schema_doc:
                schema_doc, db_type = await _get_schema_for_connection(fallback_conn_id, db)
            print(f"[intel_chat] using dashboard fallback connection={fallback_conn_id[:8]}", flush=True)

    enriched = None
    if schema_doc and effective_connection_id:
        try:
            enriched = await _schema_cache.get_or_build(effective_connection_id, schema_doc, db_type)
        except Exception as _e:
            print(f"[intel_chat] ⚠ schema enrichment failed (non-fatal): {_e}", flush=True)

    dashboard_widgets: list[dict] = []
    dashboard_pages: list[dict] = []
    if req.dashboard_id:
        dashboard_widgets, dashboard_pages = await _get_dashboard_widgets_and_pages(req.dashboard_id, db)
        sql_widget_count = sum(1 for w in dashboard_widgets if w.get("sql_query"))
        print(
            f"[intel_chat] dashboard loaded  widgets={len(dashboard_widgets)}  "
            f"with_sql={sql_widget_count}  pages={len(dashboard_pages)}",
            flush=True,
        )

    priority_tables = _extract_priority_tables(dashboard_widgets)

    effective_model_pref = req.model_preference
    if not effective_model_pref and len(req.message) > 8000:
        effective_model_pref = "opus"
        print(f"[intel_chat] auto-upgraded to opus (msg_len={len(req.message)} > 8000)", flush=True)

    # ── Build verified_tables_doc (Layers 2 + 3) ────────────────────────────────
    # Base: widget → table mapping from SQL (always, no DB needed).
    widget_table_map: dict = {}
    for w in dashboard_widgets:
        sql = w.get("sql_query") or ""
        if not sql:
            continue
        tbls = re.findall(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', sql, re.IGNORECASE)
        ctes = {m.lower() for m in re.findall(r'\b([a-zA-Z_]\w*)\s+AS\s*\(', sql, re.IGNORECASE)}
        for t in tbls:
            if t.split('.')[-1].lower() in ctes:
                continue
            entry = widget_table_map.setdefault(t, {"used_by_widgets": [], "columns": [], "source": "widget_sql"})
            if w.get("title") and w["title"] not in entry["used_by_widgets"]:
                entry["used_by_widgets"].append(w["title"])

    # Layer 2: add column info from enriched schema when available.
    if enriched and enriched.compact_tables and widget_table_map:
        compact_idx = {ct.get("name", "").lower(): ct for ct in enriched.compact_tables if ct.get("name")}
        for tbl, entry in widget_table_map.items():
            ct = compact_idx.get(tbl.lower()) or compact_idx.get(tbl.split(".")[-1].lower())
            if ct:
                cols = [c.get("name", "") for c in (ct.get("columns") or []) if c.get("name")]
                if cols:
                    entry["columns"] = cols
                    entry["source"] = "enriched_schema"

    # Layer 2 fallback: if enrichment absent, fetch live columns for priority tables.
    if not enriched and priority_tables and effective_connection_id and widget_table_map:
        missing = {t for t in widget_table_map if not widget_table_map[t].get("columns")}
        if missing:
            live = await _fetch_priority_columns_doc(effective_connection_id, missing)
            for t, info in live.items():
                if t in widget_table_map:
                    widget_table_map[t]["columns"] = info["columns"]
                    widget_table_map[t]["source"] = "live_fetch"
                else:
                    widget_table_map[t] = info

    # Layer 3: override with saved report metadata (most trusted — verified at report-gen time).
    report_meta = await _load_report_tables_metadata(req.dashboard_id or "", db)
    for tbl, info in report_meta.items():
        if tbl in widget_table_map:
            # preserve used_by_widgets from widget parse; update columns + source from metadata
            widget_table_map[tbl]["columns"] = info.get("columns") or widget_table_map[tbl].get("columns") or []
            widget_table_map[tbl]["source"] = "report_metadata"
        else:
            widget_table_map[tbl] = {**info, "source": "report_metadata"}

    verified_tables_doc = {t: v for t, v in widget_table_map.items() if v.get("columns") or v.get("used_by_widgets")}
    if verified_tables_doc:
        print(
            f"[intel_chat] verified_tables_doc: {len(verified_tables_doc)} table(s)  "
            f"sources={set(v.get('source','?') for v in verified_tables_doc.values())}",
            flush=True,
        )

    # ── NL2SQL two-stage pipeline (feeds GraphRAG scoping in report mode) ───────
    resolved_context = None
    _parsed_intent = None
    if enriched and req.message.strip():
        try:
            _parsed_intent = await parse_intent(req.message)
            if _parsed_intent.needs_sql:
                resolved_context = route_query(_parsed_intent, enriched, req.message)
                print(
                    f"[intel_chat] NL2SQL resolved  tables={resolved_context.relevant_tables[:4]}"
                    f"  entities={len(resolved_context.entity_resolutions)}"
                    f"  scores={len(resolved_context.table_scores)}",
                    flush=True,
                )
        except Exception as _nl2sql_err:
            print(f"[intel_chat] ⚠ NL2SQL pipeline failed (non-fatal): {_nl2sql_err}", flush=True)

    # ── Full graph-RAG retrieval (database scope only) ───────────────────────
    # graph_rag_retriever.retrieve() produces richer TableCandidate objects with
    # column-level hints (metric/dimension/date), entity filter hints, and verified
    # FK join paths — far more useful for SQL generation than nl_schema_router alone.
    retrieved_graphrag = None
    if (req.scope or "report") == "database" and enriched and _parsed_intent:
        try:
            from agent_service.agents.graph_rag_retriever import retrieve as _graphrag_retrieve
            retrieved_graphrag = _graphrag_retrieve(
                req.message, _parsed_intent, enriched, top_k=8,
                history_tables=list(priority_tables) if priority_tables else None,
            )
            print(
                f"[intel_chat] graph-RAG (fulldb)  "
                f"tables={retrieved_graphrag.primary_tables[:4]}  "
                f"confidence={retrieved_graphrag.confidence:.2f}  "
                f"candidates={len(retrieved_graphrag.candidates)}  "
                f"needs_join={retrieved_graphrag.needs_join}",
                flush=True,
            )
        except Exception as _rag_err:
            print(f"[intel_chat] ⚠ graph-RAG retrieval failed (non-fatal): {_rag_err}", flush=True)

    # ── Connection check for full-DB mode ────────────────────────────────────
    # For scope=database, a live connection is required.  Offline connections
    # (vly_offline) cannot serve arbitrary DB queries — only their embedded tables.
    is_offline = bool(effective_connection_id) and await _is_offline_connection(effective_connection_id, db)
    if is_offline:
        print(f"[intel_chat] offline canvas detected  connection={effective_connection_id[:8] if effective_connection_id else 'none'}", flush=True)

    return {
        "session_id": session_id,
        "history": history,
        "memory": memory,
        "schema_doc": schema_doc,
        "enriched": enriched,
        "dashboard_widgets": dashboard_widgets,
        "dashboard_pages": dashboard_pages,
        "priority_tables": priority_tables,
        "connection_id": effective_connection_id,
        "db_type": db_type,
        "model_pref": effective_model_pref,
        "verified_tables_doc": verified_tables_doc,
        "resolved_context": resolved_context,
        "retrieved_graphrag": retrieved_graphrag,
        "is_offline": is_offline,
    }


async def _is_offline_connection(connection_id: Optional[str], db: AsyncSession) -> bool:
    """Return True when connection_id belongs to a synthetic vly_offline connection."""
    if not connection_id:
        return False
    try:
        conn_r = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id))
        )
        conn = conn_r.scalar_one_or_none()
        if not conn:
            return False
        db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
        return db_type == "vly_offline"
    except Exception:
        return False


def _validate_sql_safety(sql: str) -> Optional[str]:
    """Thin wrapper kept for any callers outside this module.
    Delegates to intel_guardrails for the full layer stack."""
    from agent_service.agents.intel_guardrails import apply_fulldb_guardrails
    gr = apply_fulldb_guardrails(sql, allowed_tables=None, mode="database")
    return gr.error if not gr.safe else None


def _check_table_scope(sql: str, allowed_tables: set[str]) -> Optional[str]:
    """Thin wrapper kept for any callers outside this module.
    Returns a warning string or None; does NOT block execution."""
    if not allowed_tables:
        return None
    from agent_service.agents.intel_guardrails import apply_fulldb_guardrails
    gr = apply_fulldb_guardrails(sql, allowed_tables=allowed_tables, mode="database")
    return gr.warning if gr.safe else None


@dataclass
class ChartExecResult:
    """Result of executing a sql_execute spec — richer than a plain (chart, warning)
    tuple so callers can persist sql/error/low_confidence into conversation history
    for later root-cause follow-ups (see _try_root_cause_followup)."""
    inline_chart: Optional[dict] = None
    warning: Optional[str] = None
    sql: Optional[str] = None            # final SQL actually run/attempted, or None
    error: Optional[str] = None          # execution error text, or None
    low_confidence: bool = False


async def _execute_and_build_chart(
    sql_spec: dict,
    connection_id: Optional[str],
    db: Optional[AsyncSession] = None,
    dashboard_id: Optional[str] = None,
    priority_tables: Optional[set] = None,
    enriched=None,
) -> ChartExecResult:
    """Run a sql_execute spec and build the inline_chart payload.
    Routes to offline DuckDB when the connection is a vly_offline synthetic one.
    Narration (result_narrator) is NOT called here — callers do that after this
    returns, so streaming callers can yield the chart immediately without waiting
    on an extra narration round-trip."""
    if sql_spec.get("sql"):
        print(f"[intel_chat] generated SQL: {sql_spec['sql'][:400]}", flush=True)

    # ── Guardrails: full layer stack (safety + scope + row-limit) ─────────────
    from agent_service.agents.intel_guardrails import apply_fulldb_guardrails
    _gr = apply_fulldb_guardrails(
        sql_spec.get("sql") or "",
        allowed_tables=priority_tables if priority_tables else None,
        mode="report" if priority_tables else "database",
    )
    if not _gr.safe:
        print(f"[intel_chat] ⚠ SQL guardrail blocked: {_gr.error}", flush=True)
        return ChartExecResult(warning=f"\n\n⚠️ {_gr.error}", sql=sql_spec.get("sql"), error=_gr.error)
    if _gr.warning:
        print(f"[intel_chat] ⚠ SQL guardrail warning: {_gr.warning}", flush=True)
    # Row-limit-capped is benign (not a correctness signal); out-of-scope-table is.
    _low_confidence = "outside the allowed scope" in (_gr.warning or "")
    # Use the cleaned + row-limited SQL from guardrails.
    sql_spec = {**sql_spec, "sql": _gr.sql}

    # ── Pre-execution: column existence check against schema cache ────────────
    # Catches hallucinated column names before a DB round-trip.
    # Qualified refs (table.col) are auto-corrected via fuzzy matching when possible.
    # Unqualified refs (bare col) that can't be resolved are logged for diagnosis.
    if enriched and getattr(enriched, "compact_tables", None) and sql_spec.get("sql"):
        from agent_service.agents.sql_utils import (
            verify_all_column_refs, fuzzy_fix_column_names,
        )
        _col_errs = verify_all_column_refs(
            sql_spec["sql"], enriched.compact_tables,
            candidate_tables=list(priority_tables) if priority_tables else None,
        )
        if _col_errs:
            _fixed_sql, _corrections = fuzzy_fix_column_names(
                sql_spec["sql"], enriched.compact_tables,
                candidate_tables=list(priority_tables) if priority_tables else None,
            )
            if _corrections:
                print(f"[intel_chat] pre-exec col fix: {_corrections}", flush=True)
                sql_spec = {**sql_spec, "sql": _fixed_sql}
            else:
                print(f"[intel_chat] ⚠ pre-exec col issues (unfixable): {_col_errs[:3]}", flush=True)

    if not connection_id:
        print("[intel_chat] ⚠ no connection available to execute SQL", flush=True)
        _msg = "no active database connection is available for this report."
        return ChartExecResult(
            warning=f"\n\n⚠️ I couldn't run the query: {_msg}", sql=sql_spec.get("sql"), error=_msg,
        )
    if not sql_spec.get("sql"):
        return ChartExecResult()

    # Offline routing: vly_offline connections use DuckDB + Parquet instead of the live DB.
    from shared.offline_store import execute_offline_sql as _exec_offline
    is_offline = db is not None and await _is_offline_connection(connection_id, db)
    if is_offline:
        if not dashboard_id:
            return ChartExecResult(
                warning="\n\n⚠️ Offline query failed: dashboard context unavailable.",
                sql=sql_spec.get("sql"), error="dashboard context unavailable",
            )
        exec_result = await _exec_offline(db, dashboard_id, sql_spec["sql"])
        print(
            f"[intel_chat] offline SQL  dashboard={dashboard_id[:8]}  "
            f"rows={exec_result.get('row_count', 0)}  err={exec_result.get('error', 'none')[:80] if exec_result.get('error') else 'none'}",
            flush=True,
        )
    else:
        exec_result = await _execute_sql(connection_id, sql_spec["sql"])
    if exec_result.get("error"):
        print(f"[intel_chat] ⚠ sql exec error: {str(exec_result['error'])[:200]} — attempting self-correct", flush=True)
        # For offline mode, skip live-column-lookup self-correct (no live DB); retry without it.
        # Pass enriched cache so self-correct can look up columns without a live DB call.
        # Also works for offline mode: column hints come from the cache, not information_schema.
        if not is_offline:
            fixed_sql = await _self_correct_sql(
                connection_id, sql_spec["sql"], str(exec_result["error"]), enriched=enriched,
            )
        else:
            fixed_sql = await _self_correct_sql(
                connection_id, sql_spec["sql"], str(exec_result["error"]), enriched=enriched,
            ) if enriched else None
        if fixed_sql and fixed_sql.strip() != (sql_spec["sql"] or "").strip():
            retry = (
                await _exec_offline(db, dashboard_id, fixed_sql)
                if is_offline else await _execute_sql(connection_id, fixed_sql)
            )
            if not retry.get("error"):
                print(f"[intel_chat] ✓ self-corrected SQL ran: {fixed_sql[:160]}", flush=True)
                sql_spec = {**sql_spec, "sql": fixed_sql}
                exec_result = retry
                _low_confidence = True
            else:
                print(f"[intel_chat] ✗ self-correct retry still failed: {str(retry.get('error'))[:160]}", flush=True)
        if exec_result.get("error"):
            return ChartExecResult(
                warning=f"\n\n⚠️ The query failed to run: {exec_result['error']}",
                sql=sql_spec.get("sql"), error=str(exec_result["error"]), low_confidence=True,
            )
    if not exec_result.get("rows"):
        print(f"[intel_chat] ⚠ sql returned 0 rows  sql={sql_spec['sql'][:160]}", flush=True)
        return ChartExecResult(
            warning=("\n\n⚠️ The query ran but returned no rows — there may be no matching "
                     "data, or a name/date filter didn't match. Try rephrasing or broadening it."),
            sql=sql_spec.get("sql"), error="Query returned 0 rows", low_confidence=True,
        )

    # ── Post-execution sanity: NULL aggregate + all-zero values ─────────────
    # These pass SQL execution but silently produce a meaningless chart.
    _post_columns = exec_result.get("columns", [])
    _post_rows = exec_result.get("rows", [])
    if _post_rows and _post_columns:
        from agent_service.agents.sql_utils import check_result_sanity
        _san_ok, _san_msg = check_result_sanity(
            _post_rows, _post_columns, sql_spec.get("chart_type", ""), sql_spec["sql"]
        )
        if not _san_ok and _san_msg:
            print(f"[intel_chat] ⚠ post-exec sanity: {_san_msg[:200]}", flush=True)
            # Attempt self-correct with the sanity failure as the error message.
            _san_fixed = await _self_correct_sql(
                connection_id, sql_spec["sql"], _san_msg, enriched=enriched,
            ) if connection_id else None
            if _san_fixed and _san_fixed.strip() != sql_spec["sql"].strip():
                _san_retry = (
                    await _exec_offline(db, dashboard_id, _san_fixed)
                    if is_offline else await _execute_sql(connection_id, _san_fixed)
                )
                if not _san_retry.get("error") and _san_retry.get("rows"):
                    print(f"[intel_chat] ✓ sanity self-correct succeeded", flush=True)
                    sql_spec = {**sql_spec, "sql": _san_fixed}
                    exec_result = _san_retry
                    _post_columns = exec_result.get("columns", [])
                    _post_rows = exec_result.get("rows", [])
                    _low_confidence = True

    render_result = await _render_chart(sql_spec, exec_result["rows"])
    columns = exec_result.get("columns", [])
    rows = exec_result.get("rows", [])
    labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
    if len(columns) > 1:
        values = [r.get(columns[1]) for r in rows]
    elif len(columns) == 1:
        values = [r.get(columns[0]) for r in rows]
    else:
        values = []
    inline_chart = {
        "chart_type": sql_spec.get("chart_type", "table"),
        "title": sql_spec.get("title", "Chart"),
        "x_axis_label": sql_spec.get("x_label", columns[0] if columns else "x"),
        "y_axis_label": sql_spec.get("y_label", columns[1] if len(columns) > 1 else "y"),
        "chart_data": {"rows": rows, "columns": columns, "labels": labels, "values": values},
        "sql": sql_spec["sql"],
        "image_base64": render_result.get("image_base64"),
    }
    print(f"[intel_chat] ✓ chart built  type={inline_chart['chart_type']}  rows={len(rows)}  cols={len(columns)}", flush=True)
    return ChartExecResult(inline_chart=inline_chart, sql=sql_spec["sql"], low_confidence=_low_confidence)


async def _narrate_chart_result(user_text: str, sql_spec: dict, inline_chart: dict) -> Optional[str]:
    """Post-execution, data-grounded narration of a successfully built chart.
    Returns None (non-fatal) if narration fails or produces nothing — callers
    must treat this as optional, matching result_narrator.py's own fail-open design."""
    try:
        from agent_service.agents.result_narrator import narrate, verify_narrative_grounding
        qp = types.SimpleNamespace(
            chart_type=sql_spec.get("chart_type"), title=sql_spec.get("title"),
        )
        narrative = await narrate(user_text, qp, inline_chart["chart_data"], output_mode="chart")
        if not narrative:
            return None
        grounded, corrected = await verify_narrative_grounding(narrative, inline_chart["chart_data"])
        if not grounded and corrected:
            narrative = corrected
        return narrative
    except Exception as exc:  # noqa: BLE001
        print(f"[intel_chat] narration failed (non-fatal): {exc}", flush=True)
        return None


def _no_sql_note() -> str:
    return ("\n\n⚠️ I wasn't able to generate the query for that. Try rephrasing, or name "
            "the table/columns you'd like me to use.")


def _wants_root_cause_followup(user_text: str, history: list) -> Optional[dict]:
    """Gate for the root-cause short-circuit: only fires when the user's phrasing
    matches ROOT_CAUSE_FOLLOWUP_PATTERN AND the immediately-prior turn actually
    recorded a failure/low-confidence flag. Returns that prior turn, or None."""
    if not history:
        return None
    last_turn = history[-1]
    if not (last_turn.get("role") == "assistant" and (last_turn.get("error") or last_turn.get("low_confidence"))):
        return None
    from agent_service.agents.sql_utils import ROOT_CAUSE_FOLLOWUP_PATTERN
    if not ROOT_CAUSE_FOLLOWUP_PATTERN.search(user_text or ""):
        return None
    return last_turn


async def _try_root_cause_followup(
    user_text: str,
    last_turn: dict,
    connection_id: Optional[str],
    db_type: str,
    enriched,
    priority_tables: Optional[set] = None,
    db: Optional[AsyncSession] = None,
    dashboard_id: Optional[str] = None,
) -> Optional[dict]:
    """Diagnose "why did that fail?" against the actual prior failure, instead of
    blindly re-guessing. Returns a full response dict (inline_chart/text + narrative
    + exec metadata) or None if diagnosis produces nothing usable.

    db/dashboard_id are threaded through so a corrected offline (vly_offline) query
    still routes to DuckDB via _execute_and_build_chart's existing offline branch."""
    from agent_service.agents.sql_utils import root_cause_schema_context
    failed_sql = last_turn.get("sql") or ""
    tables: list[str] = []
    if failed_sql:
        for m in _FROM_JOIN_RE.findall(failed_sql):
            n = m.strip()
            if n and n not in tables:
                tables.append(n)
    rc_ctx = root_cause_schema_context(enriched, tables)
    try:
        diagnosis = await _root_cause.diagnose(
            user_text=user_text,
            failed_sql=failed_sql,
            problem=last_turn.get("error") or (
                "The previous result was flagged low-confidence — it may not "
                "have accurately answered the question."
            ),
            db_type=db_type or "postgresql",
            tables_context=rc_ctx,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[intel_chat] root-cause diagnose failed (non-fatal): {exc}", flush=True)
        return None

    print(f"[intel_chat] root_cause_diagnosis fired  root_cause={diagnosis.root_cause}  has_fix={bool(diagnosis.fixed_sql)}", flush=True)

    if diagnosis.fixed_sql:
        # Re-run through the normal execution path so the corrected SQL inherits
        # offline routing, guardrails, and sanity checks instead of running
        # agent-generated SQL unchecked.
        exec_result = await _execute_and_build_chart(
            {"sql": diagnosis.fixed_sql, "chart_type": "table", "title": "Corrected result"},
            connection_id, db=db, dashboard_id=dashboard_id,
            priority_tables=priority_tables, enriched=enriched,
        )
        if exec_result.inline_chart:
            narrative = await _narrate_chart_result(user_text, {"sql": diagnosis.fixed_sql}, exec_result.inline_chart)
            text = f"{diagnosis.explanation} I corrected the query — here's the result."
            return {
                "text": text, "inline_chart": exec_result.inline_chart,
                "narrative": narrative, "sql": exec_result.sql,
                "error": None, "low_confidence": True,
            }

    return {
        "text": diagnosis.explanation, "inline_chart": None,
        "narrative": None, "sql": failed_sql or None,
        "error": None, "low_confidence": True,
    }


def _build_history_turn(user_text: str, final_text: str, narrative: Optional[str],
                         exec_result: Optional["ChartExecResult"]) -> tuple[dict, dict]:
    """Build the (user, assistant) turn pair to persist. Concatenates the model's
    pre-execution prose with the post-execution grounded narrative (when both exist
    and differ) rather than replacing one with the other — the prose can carry
    acknowledgment/drill-down framing the narrator never sees, while the narrative
    is what makes a later "why is this happening" follow-up data-grounded."""
    if final_text and narrative and narrative not in final_text:
        content = final_text.rstrip() + "\n\n" + narrative
    else:
        content = narrative or final_text
    assistant_turn = {"role": "assistant", "content": content}
    if exec_result:
        if exec_result.sql:
            assistant_turn["sql"] = exec_result.sql
        if exec_result.error:
            assistant_turn["error"] = exec_result.error
        if exec_result.low_confidence:
            assistant_turn["low_confidence"] = True
    return {"role": "user", "content": user_text}, assistant_turn


@router.post("/intelligence/chat", response_model=IntelChatResponse)
async def intel_chat(
    req: IntelChatRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    start_token_tracking()
    ctx = await _collect_chat_context(req, db, redis)

    # ── Root-cause follow-up short-circuit ────────────────────────────────────
    # "why did that fail?" against an actually-failed prior turn skips the normal
    # LLM-generation flow entirely and diagnoses against what really went wrong.
    _rc_last_turn = _wants_root_cause_followup(req.message, ctx["history"])
    if _rc_last_turn is not None:
        rc = await _try_root_cause_followup(
            req.message, _rc_last_turn, ctx["connection_id"], ctx.get("db_type", "postgresql"),
            ctx.get("enriched"), priority_tables=ctx.get("priority_tables"),
            db=db, dashboard_id=req.dashboard_id,
        )
        if rc is not None:
            user_turn, assistant_turn = _build_history_turn(
                req.message, rc["text"], rc.get("narrative"),
                ChartExecResult(sql=rc.get("sql"), error=rc.get("error"), low_confidence=rc.get("low_confidence", False)),
            )
            updated_history = ctx["history"] + [user_turn, assistant_turn]
            new_memory = IntelligenceChatAgent.distill_memory(ctx["memory"], req.message)
            await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis, memory=new_memory)
            return IntelChatResponse(
                session_id=ctx["session_id"], text=assistant_turn["content"],
                inline_chart=rc.get("inline_chart"), dashboard_action=None,
                turn_count=len(updated_history) // 2,
            )

    result = await _agent.respond(
        message=req.message,
        conversation_history=ctx["history"],
        schema_doc=ctx["schema_doc"],
        dashboard_widgets=ctx["dashboard_widgets"],
        dashboard_pages=ctx["dashboard_pages"],
        active_page_id=req.active_page_id,
        priority_tables=ctx["priority_tables"],
        enriched_schema=ctx["enriched"],
        model_override=ctx["model_pref"],
        connection_id=ctx["connection_id"],
        scope=req.scope or "report",
        conversation_memory=ctx["memory"],
        verified_tables_doc=ctx.get("verified_tables_doc"),
        resolved_context=ctx.get("resolved_context"),
        retrieved_graphrag=ctx.get("retrieved_graphrag"),
    )

    sql_spec = result.get("sql_to_execute")
    print(
        f"[intel_chat] agent done  text_len={len(result.get('text', ''))}  "
        f"sql_returned={'yes' if sql_spec else 'no'}  "
        f"action={'yes' if result.get('dashboard_action') else 'no'}",
        flush=True,
    )

    inline_chart = None
    narrative = None
    exec_result: Optional[ChartExecResult] = None
    if sql_spec:
        exec_result = await _execute_and_build_chart(
            sql_spec, ctx["connection_id"],
            db=db, dashboard_id=req.dashboard_id,
            priority_tables=ctx.get("priority_tables"),
            enriched=ctx.get("enriched"),
        )
        inline_chart = exec_result.inline_chart
        if exec_result.warning:
            result["text"] = (result.get("text") or "").rstrip() + exec_result.warning
        if inline_chart:
            narrative = await _narrate_chart_result(req.message, sql_spec, inline_chart)
    elif _is_data_query_request(req.message) or _is_chart_creation_request(req.message):
        result["text"] = (result.get("text") or "").rstrip() + _no_sql_note()
        print("[intel_chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

    user_turn, assistant_turn = _build_history_turn(req.message, result["text"], narrative, exec_result)
    updated_history = ctx["history"] + [user_turn, assistant_turn]
    new_memory = IntelligenceChatAgent.distill_memory(ctx["memory"], req.message)
    await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis, memory=new_memory)

    _scope_label = f"intel/{req.scope or 'report'}"
    _tok_log = format_token_log(_scope_label, ctx["session_id"])
    if _tok_log:
        print(_tok_log, flush=True)
    print(f"[intel_chat] ✔ turn complete  session={ctx['session_id'][:8]}  turns={len(updated_history) // 2}", flush=True)

    return IntelChatResponse(
        session_id=ctx["session_id"],
        text=assistant_turn["content"],
        inline_chart=inline_chart,
        dashboard_action=result.get("dashboard_action"),
        turn_count=len(updated_history) // 2,
    )


@router.post("/intelligence/chat/stream")
async def intel_chat_stream(
    req: IntelChatRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Streaming variant of /intelligence/chat (Server-Sent Events).
    Event types: text, chart, action, error, done. All DB access happens up
    front in _collect_chat_context, so the generator only touches Redis + httpx."""
    start_token_tracking()
    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    # Setup (DB access + prompt build) must happen here, before the generator, while
    # the request-scoped DB session is alive. If anything throws, surface the REAL
    # reason (logged with a traceback, and streamed as an error event) instead of a
    # bare 500 that the UI can only render as "something went wrong".
    try:
        ctx = await _collect_chat_context(req, db, redis)

        # ── Root-cause follow-up short-circuit ────────────────────────────────
        # Skips the normal LLM-generation flow entirely — diagnoses against the
        # actually-recorded failure instead of blindly re-guessing.
        _rc_last_turn = _wants_root_cause_followup(req.message, ctx["history"])
        if _rc_last_turn is not None:
            async def _rc_event_gen():
                rc = await _try_root_cause_followup(
                    req.message, _rc_last_turn, ctx["connection_id"], ctx.get("db_type", "postgresql"),
                    ctx.get("enriched"), priority_tables=ctx.get("priority_tables"),
                    db=db, dashboard_id=req.dashboard_id,
                )
                if rc is None:
                    yield _sse({"type": "error", "message": "I couldn't diagnose the previous failure."})
                    yield _sse({"type": "done", "session_id": ctx["session_id"], "turn_count": len(ctx["history"]) // 2})
                    return
                user_turn, assistant_turn = _build_history_turn(
                    req.message, rc["text"], rc.get("narrative"),
                    ChartExecResult(sql=rc.get("sql"), error=rc.get("error"), low_confidence=rc.get("low_confidence", False)),
                )
                yield _sse({"type": "text", "delta": assistant_turn["content"]})
                if rc.get("inline_chart"):
                    yield _sse({"type": "chart", "chart": rc["inline_chart"]})
                updated_history = ctx["history"] + [user_turn, assistant_turn]
                new_memory = IntelligenceChatAgent.distill_memory(ctx["memory"], req.message)
                await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis, memory=new_memory)
                yield _sse({"type": "done", "session_id": ctx["session_id"], "turn_count": len(updated_history) // 2})

            return StreamingResponse(
                _rc_event_gen(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        system_blocks, messages, model_id, max_tokens = _agent.prepare(
            message=req.message,
            conversation_history=ctx["history"],
            schema_doc=ctx["schema_doc"],
            dashboard_widgets=ctx["dashboard_widgets"],
            dashboard_pages=ctx["dashboard_pages"],
            active_page_id=req.active_page_id,
            priority_tables=ctx["priority_tables"],
            enriched_schema=ctx["enriched"],
            model_override=ctx["model_pref"],
            connection_id=ctx["connection_id"],
            scope=req.scope or "report",
            conversation_memory=ctx["memory"],
            verified_tables_doc=ctx.get("verified_tables_doc"),
            resolved_context=ctx.get("resolved_context"),
            retrieved_graphrag=ctx.get("retrieved_graphrag"),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[intel_chat] ✗ stream setup failed: {exc!r}", flush=True)
        traceback.print_exc()
        detail = f"{type(exc).__name__}: {exc}"

        async def _err_gen():
            yield _sse({"type": "error", "message": f"Copilot setup failed — {detail}"})
            yield _sse({"type": "done", "session_id": req.session_id or "", "turn_count": 0})

        return StreamingResponse(
            _err_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    async def event_gen():
        raw = ""
        prose_emitted = 0
        fence_found = False
        errored = False

        async for kind, payload in bedrock_invoke_stream(
            model_id, system_blocks, messages, max_tokens, 0.3
        ):
            if kind == "text":
                raw += payload
                if not fence_found:
                    idx = raw.find("```")
                    if idx != -1:
                        fence_found = True
                        safe = raw[:idx]
                    else:
                        safe = raw[:-2] if len(raw) > 2 else ""
                    if len(safe) > prose_emitted:
                        yield _sse({"type": "text", "delta": safe[prose_emitted:]})
                        prose_emitted = len(safe)
            elif kind == "error":
                errored = True
                print(f"[intel_chat] ⚠ stream error: {str(payload)[:200]}", flush=True)
                yield _sse({"type": "error", "message": payload})

        if errored:
            yield _sse({"type": "done", "session_id": ctx["session_id"],
                        "turn_count": len(ctx["history"]) // 2})
            return

        if not fence_found and len(raw) > prose_emitted:
            yield _sse({"type": "text", "delta": raw[prose_emitted:]})

        print(
            f"[intel_chat] stream parsed  response_len={len(raw)}  "
            f"has_sql={'yes' if '```sql_execute' in raw else 'no'}",
            flush=True,
        )

        parsed = _agent.parse_raw(raw)
        sql_spec = parsed["sql_to_execute"]

        if not parsed["sqls_to_execute"] and (
            _is_data_query_request(req.message) or _is_chart_creation_request(req.message)
        ):
            retry_sqls = await _agent.retry_for_sql(req.message, ctx["history"], system_blocks)
            if retry_sqls:
                sql_spec = retry_sqls[0]

        final_text = parsed["text"]
        narrative = None
        exec_result: Optional[ChartExecResult] = None

        if sql_spec:
            exec_result = await _execute_and_build_chart(
                sql_spec, ctx["connection_id"],
                db=db if ctx.get("is_offline") else None,
                dashboard_id=req.dashboard_id,
                priority_tables=ctx.get("priority_tables"),
                enriched=ctx.get("enriched"),
            )
            inline_chart = exec_result.inline_chart
            if exec_result.warning:
                final_text = (final_text or "").rstrip() + exec_result.warning
                yield _sse({"type": "text", "delta": exec_result.warning})
            if inline_chart:
                # Yield the chart FIRST — narration below adds a Sonnet + Haiku
                # round-trip and must never delay the chart the user is waiting on.
                yield _sse({"type": "chart", "chart": inline_chart})
                narrative = await _narrate_chart_result(req.message, sql_spec, inline_chart)
                if narrative:
                    yield _sse({"type": "text", "delta": "\n\n" + narrative})
        elif _is_data_query_request(req.message) or _is_chart_creation_request(req.message):
            note = _no_sql_note()
            final_text = (final_text or "").rstrip() + note
            yield _sse({"type": "text", "delta": note})
            print("[intel_chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

        if parsed.get("dashboard_action"):
            yield _sse({"type": "action", "action": parsed["dashboard_action"]})

        user_turn, assistant_turn = _build_history_turn(req.message, final_text, narrative, exec_result)
        updated_history = ctx["history"] + [user_turn, assistant_turn]
        new_memory = IntelligenceChatAgent.distill_memory(ctx["memory"], req.message)
        await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis, memory=new_memory)

        _tok_log = format_token_log(f"intel/{req.scope or 'report'}(stream)", ctx["session_id"])
        if _tok_log:
            print(_tok_log, flush=True)
        print(f"[intel_chat] ✔ stream complete  session={ctx['session_id'][:8]}  turns={len(updated_history) // 2}", flush=True)

        yield _sse({"type": "done", "session_id": ctx["session_id"],
                    "turn_count": len(updated_history) // 2})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/intelligence/chat/{session_id}")
async def clear_intel_chat(
    session_id: str,
    redis=Depends(get_redis),
):
    await IntelligenceChatAgent.clear_history(session_id, redis)
    print(f"[intel_chat] cleared history  session={session_id[:8]}", flush=True)
    return {"status": "cleared", "session_id": session_id}


@router.get("/intelligence/chat/connection-status")
async def intel_connection_status(
    project_id: Optional[str] = None,
    dashboard_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return whether a live database connection is available for the intelligence copilot.

    The frontend uses this to decide whether to show the Full DB mode toggle:
      - has_live_connection=True  → show the toggle (user can switch to database scope)
      - is_offline=True           → hide the toggle (offline canvas, no live DB)
      - has_live_connection=False → show a "connect your database" prompt instead

    Rules:
      • An active, non-offline DatabaseConnection on the project → full DB mode available.
      • A vly_offline synthetic connection → full DB mode NOT available.
      • No connection at all → full DB mode NOT available.
    """
    has_live = False
    conn_id: Optional[str] = None
    is_offline_flag = False

    if project_id:
        try:
            project_uuid = uuid.UUID(project_id)
            conn_r = await db.execute(
                select(DatabaseConnection)
                .where(DatabaseConnection.project_id == project_uuid)
                .where(DatabaseConnection.is_active == True)
                .limit(1)
            )
            conn = conn_r.scalar_one_or_none()
            if conn:
                db_type_val = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
                if db_type_val == "vly_offline":
                    is_offline_flag = True
                else:
                    has_live = True
                    conn_id = str(conn.id)
        except Exception as exc:
            print(f"[intel_chat] ⚠ connection-status project lookup failed: {exc}", flush=True)

    if not has_live and dashboard_id:
        try:
            fallback = await _get_dashboard_connection_id(dashboard_id, db)
            if fallback:
                offline = await _is_offline_connection(fallback, db)
                if offline:
                    is_offline_flag = True
                else:
                    has_live = True
                    conn_id = fallback
        except Exception as exc:
            print(f"[intel_chat] ⚠ connection-status dashboard fallback failed: {exc}", flush=True)

    print(
        f"[intel_chat] connection-status  project={project_id or 'none'}  "
        f"has_live={has_live}  is_offline={is_offline_flag}",
        flush=True,
    )
    return {
        "has_live_connection": has_live,
        "connection_id": conn_id,
        "is_offline": is_offline_flag,
        "full_db_mode_available": has_live,
    }


# ── helpers (forked copy from chat.py — kept local so this path is standalone) ──

async def _get_schema_context(
    project_id: str, db: AsyncSession
) -> tuple[dict, str, str]:
    """Returns (schema_doc, connection_id_str, db_type_str)."""
    if not project_id:
        return {}, "", "postgresql"
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        return {}, "", "postgresql"
    conn_result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.project_id == project_uuid)
        .where(DatabaseConnection.is_active == True)
        .limit(1)
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        return {}, "", "postgresql"
    connection_id_str = str(conn.id)
    db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
    snap_result = await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    schema_doc = snapshot.schema_document if snapshot else {}
    return schema_doc, connection_id_str, db_type


async def _get_dashboard_widgets_and_pages(
    dashboard_id: str, db: AsyncSession
) -> tuple[list[dict], list[dict]]:
    """Return (widgets_with_page_id, pages_array) for the full canvas."""
    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dash = dash_result.scalar_one_or_none()
    pages: list[dict] = (dash.layout_config or {}).get("pages", []) if dash else []

    result = await db.execute(
        select(Widget).where(Widget.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets = result.scalars().all()
    widget_list = [
        {
            "id": str(w.id),
            "title": w.title,
            "chart_type": w.chart_type,
            "sql_query": w.sql_query,
            "chart_data": w.chart_data,
            "page_id": (w.config or {}).get("page_id"),
            "connection_id": str(w.connection_id) if w.connection_id else None,
        }
        for w in widgets
    ]
    return widget_list, pages


async def _get_dashboard_connection_id(dashboard_id: str, db: AsyncSession) -> str | None:
    """Fallback connection resolution for imported canvases whose project has no connection."""
    try:
        dash_r = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
        dash = dash_r.scalar_one_or_none()
        if dash:
            lc_conn = (dash.layout_config or {}).get("connection_id")
            if lc_conn:
                return str(lc_conn)
        wid_r = await db.execute(
            select(Widget.connection_id)
            .where(Widget.dashboard_id == uuid.UUID(dashboard_id))
            .where(Widget.connection_id.isnot(None))
            .limit(1)
        )
        conn = wid_r.scalar_one_or_none()
        return str(conn) if conn else None
    except Exception:
        return None


async def _get_schema_for_connection(conn_id_str: str, db: AsyncSession) -> tuple[dict, str]:
    """Load schema_doc and db_type for a known connection UUID."""
    try:
        conn_r = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(conn_id_str))
        )
        conn = conn_r.scalar_one_or_none()
        if not conn:
            return {}, "postgresql"
        db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
        snap_r = await db.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.connection_id == conn.id)
            .order_by(SchemaSnapshot.version.desc())
            .limit(1)
        )
        snapshot = snap_r.scalar_one_or_none()
        return (snapshot.schema_document if snapshot else {}), db_type
    except Exception:
        return {}, "postgresql"


def _extract_priority_tables(widgets: list[dict]) -> set[str]:
    """Extract table names referenced in existing widget SQL queries."""
    tables: set[str] = set()
    for w in widgets:
        sql = w.get("sql_query") or ""
        if not sql:
            continue
        from_tables = re.findall(r"\bFROM\s+([\w.]+)", sql, re.IGNORECASE)
        join_tables = re.findall(r"\bJOIN\s+([\w.]+)", sql, re.IGNORECASE)
        tables.update(t.lower() for t in from_tables + join_tables)
    return tables


_FROM_JOIN_RE = re.compile(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', re.IGNORECASE)


async def _fetch_table_columns(connection_id: str, schema: str, table: str) -> list[str]:
    """Look up the REAL column names of a table/view straight from the database."""
    sql = (
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
        "ORDER BY ordinal_position"
    )
    res = await _execute_sql(connection_id, sql)
    if res.get("error"):
        return []
    return [str(r.get("column_name")) for r in (res.get("rows") or []) if r.get("column_name")]


async def _self_correct_sql(
    connection_id: str,
    failed_sql: str,
    error_msg: str,
    enriched=None,
) -> Optional[str]:
    """When a generated query references columns that don't exist, look up the REAL
    columns (from the enriched schema cache first, live DB as fallback) and ask the
    model to rewrite using only those columns."""
    tables: list[str] = []
    for name in _FROM_JOIN_RE.findall(failed_sql or ""):
        n = name.strip()
        # Previously filtered to "." in n only, silently dropping unqualified table names.
        # Now collect all FROM/JOIN targets regardless of schema qualification.
        if n and n not in tables:
            tables.append(n)
    col_lines: list[str] = []

    # Primary: use the enriched schema cache — instant, no DB round-trip needed.
    if enriched and getattr(enriched, "compact_tables", None):
        _ct_idx: dict[str, dict] = {}
        for t in enriched.compact_tables:
            tn = (t.get("name") or "").lower()
            if tn:
                _ct_idx[tn] = t
                _ct_idx[tn.split(".")[-1]] = t
        for tname in tables[:8]:
            ct = _ct_idx.get(tname.lower()) or _ct_idx.get(tname.split(".")[-1].lower())
            if ct:
                cols = [c.get("name") for c in (ct.get("columns") or []) if c.get("name")]
                if cols:
                    col_lines.append(f'{ct.get("name")} has ONLY these columns: {", ".join(cols[:40])}')

    # Fallback: live information_schema lookup for schema-qualified tables not in cache.
    if not col_lines:
        for t in tables[:6]:
            if "." in t:
                schema, _, table = t.partition(".")
                cols = await _fetch_table_columns(connection_id, schema, table)
                if cols:
                    col_lines.append(f'{t} has ONLY these columns: {", ".join(cols)}')

    if not col_lines:
        return None

    system = (
        "You fix broken SQL. The query failed because it referenced a column that does "
        "not exist on the table it was used with (a common mistake is borrowing a column "
        "from a different table). Using ONLY the real columns listed, rewrite the query so "
        "it runs and preserves the user's intent as closely as the available columns allow. "
        "It must remain a single read-only SELECT/WITH statement. Respond with ONLY the "
        "corrected SQL — no markdown, no commentary."
    )
    user = (
        f"The SQL failed with error: {error_msg}\n\n"
        f"FAILED SQL:\n{failed_sql}\n\n"
        f"REAL SCHEMA (authoritative — use only these columns):\n" + "\n".join(col_lines) +
        "\n\nReturn only the corrected SQL."
    )
    try:
        out = await bedrock_invoke(BEDROCK_SONNET_MODEL, system, user, max_tokens=1024, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        print(f"[intel_chat] self-correct LLM call failed: {exc}", flush=True)
        return None
    s = (out or "").strip()
    s = re.sub(r'^```(?:sql)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s).strip()
    m = re.search(r'\b(WITH|SELECT)\b', s, re.IGNORECASE)
    if m:
        s = s[m.start():].rstrip().rstrip(';')
    return s or None


async def _execute_sql(connection_id: str, sql: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=130.0) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={"connection_id": connection_id, "sql": sql, "row_limit": 1000, "timeout_seconds": 120},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"error": f"Executor {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


async def _render_chart(sql_spec: dict, rows: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{RENDER_SERVICE_URL}/render",
                json={
                    "query_plan": {
                        "chart_type": sql_spec.get("chart_type", "table"),
                        "x_axis_label": sql_spec.get("x_label", "x"),
                        "y_axis_label": sql_spec.get("y_label", "y"),
                        "title": sql_spec.get("title", "Chart"),
                    },
                    "rows": rows,
                },
            )
            return resp.json() if resp.status_code == 200 else {}
    except Exception:
        return {}
