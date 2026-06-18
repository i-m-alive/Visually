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
from shared.models.database_connections import DatabaseConnection
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from agent_service.agents.intelligence_chat_agent import (
    IntelligenceChatAgent,
    _is_data_query_request,
    _is_chart_creation_request,
)
import agent_service.agents.schema_cache as _schema_cache
from shared.bedrock_client import bedrock_invoke_stream, bedrock_invoke, BEDROCK_SONNET_MODEL

router = APIRouter(tags=["intelligence-chat"])
_agent = IntelligenceChatAgent()

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

    return {
        "session_id": session_id,
        "history": history,
        "schema_doc": schema_doc,
        "enriched": enriched,
        "dashboard_widgets": dashboard_widgets,
        "dashboard_pages": dashboard_pages,
        "priority_tables": priority_tables,
        "connection_id": effective_connection_id,
        "model_pref": effective_model_pref,
    }


async def _execute_and_build_chart(
    sql_spec: dict, connection_id: Optional[str]
) -> tuple[Optional[dict], Optional[str]]:
    """Run a sql_execute spec and build the inline_chart payload.
    Returns (inline_chart | None, warning_text | None)."""
    if sql_spec.get("sql"):
        print(f"[intel_chat] generated SQL: {sql_spec['sql'][:400]}", flush=True)
    if not connection_id:
        print("[intel_chat] ⚠ no connection available to execute SQL", flush=True)
        return None, ("\n\n⚠️ I couldn't run the query: no active database connection "
                      "is available for this report.")
    if not sql_spec.get("sql"):
        return None, None

    exec_result = await _execute_sql(connection_id, sql_spec["sql"])
    if exec_result.get("error"):
        print(f"[intel_chat] ⚠ sql exec error: {str(exec_result['error'])[:200]} — attempting self-correct", flush=True)
        fixed_sql = await _self_correct_sql(connection_id, sql_spec["sql"], str(exec_result["error"]))
        if fixed_sql and fixed_sql.strip() != (sql_spec["sql"] or "").strip():
            retry = await _execute_sql(connection_id, fixed_sql)
            if not retry.get("error"):
                print(f"[intel_chat] ✓ self-corrected SQL ran: {fixed_sql[:160]}", flush=True)
                sql_spec = {**sql_spec, "sql": fixed_sql}
                exec_result = retry
            else:
                print(f"[intel_chat] ✗ self-correct retry still failed: {str(retry.get('error'))[:160]}", flush=True)
        if exec_result.get("error"):
            return None, f"\n\n⚠️ The query failed to run: {exec_result['error']}"
    if not exec_result.get("rows"):
        print(f"[intel_chat] ⚠ sql returned 0 rows  sql={sql_spec['sql'][:160]}", flush=True)
        return None, ("\n\n⚠️ The query ran but returned no rows — there may be no matching "
                      "data, or a name/date filter didn't match. Try rephrasing or broadening it.")

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
    return inline_chart, None


def _no_sql_note() -> str:
    return ("\n\n⚠️ I wasn't able to generate the query for that. Try rephrasing, or name "
            "the table/columns you'd like me to use.")


@router.post("/intelligence/chat", response_model=IntelChatResponse)
async def intel_chat(
    req: IntelChatRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    ctx = await _collect_chat_context(req, db, redis)

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
    )

    sql_spec = result.get("sql_to_execute")
    print(
        f"[intel_chat] agent done  text_len={len(result.get('text', ''))}  "
        f"sql_returned={'yes' if sql_spec else 'no'}  "
        f"action={'yes' if result.get('dashboard_action') else 'no'}",
        flush=True,
    )

    inline_chart = None
    if sql_spec:
        inline_chart, warning = await _execute_and_build_chart(sql_spec, ctx["connection_id"])
        if warning:
            result["text"] = (result.get("text") or "").rstrip() + warning
    elif _is_data_query_request(req.message) or _is_chart_creation_request(req.message):
        result["text"] = (result.get("text") or "").rstrip() + _no_sql_note()
        print("[intel_chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

    updated_history = ctx["history"] + [
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": result["text"]},
    ]
    await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis)
    print(f"[intel_chat] ✔ turn complete  session={ctx['session_id'][:8]}  turns={len(updated_history) // 2}", flush=True)

    return IntelChatResponse(
        session_id=ctx["session_id"],
        text=result["text"],
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
    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    # Setup (DB access + prompt build) must happen here, before the generator, while
    # the request-scoped DB session is alive. If anything throws, surface the REAL
    # reason (logged with a traceback, and streamed as an error event) instead of a
    # bare 500 that the UI can only render as "something went wrong".
    try:
        ctx = await _collect_chat_context(req, db, redis)
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

        if sql_spec:
            inline_chart, warning = await _execute_and_build_chart(sql_spec, ctx["connection_id"])
            if warning:
                final_text = (final_text or "").rstrip() + warning
                yield _sse({"type": "text", "delta": warning})
            if inline_chart:
                yield _sse({"type": "chart", "chart": inline_chart})
        elif _is_data_query_request(req.message) or _is_chart_creation_request(req.message):
            note = _no_sql_note()
            final_text = (final_text or "").rstrip() + note
            yield _sse({"type": "text", "delta": note})
            print("[intel_chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

        if parsed.get("dashboard_action"):
            yield _sse({"type": "action", "action": parsed["dashboard_action"]})

        updated_history = ctx["history"] + [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": final_text},
        ]
        await IntelligenceChatAgent.save_history(ctx["session_id"], updated_history, redis)
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


async def _self_correct_sql(connection_id: str, failed_sql: str, error_msg: str) -> Optional[str]:
    """When a generated query references columns that don't exist, fetch the REAL
    columns of the tables it used and ask the model to rewrite using only those."""
    tables: list[str] = []
    for name in _FROM_JOIN_RE.findall(failed_sql or ""):
        n = name.strip()
        if "." in n and n not in tables:
            tables.append(n)
    col_lines: list[str] = []
    for t in tables[:6]:
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
