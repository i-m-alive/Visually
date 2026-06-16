import uuid
import json
import re
import os
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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
from shared.encryption import decrypt
from shared.export_tokens import validate_export_token
from agent_service.agents.chat_agent import (
    ChatAgent,
    _is_data_query_request,
    _is_chart_creation_request,
)
import agent_service.agents.schema_cache as _schema_cache
from shared.bedrock_client import bedrock_invoke_stream

router = APIRouter(tags=["chat"])
_agent = ChatAgent()

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
RENDER_SERVICE_URL = os.getenv("RENDER_SERVICE_URL", "http://localhost:3001")


class ChatRequest(BaseModel):
    message: str
    project_id: str
    dashboard_id: Optional[str] = None
    session_id: Optional[str] = None
    connection_id: Optional[str] = None
    active_page_id: Optional[str] = None
    model_preference: Optional[str] = None  # 'opus' for deeper analysis


class ChatResponse(BaseModel):
    session_id: str
    text: str
    inline_chart: Optional[dict] = None
    dashboard_action: Optional[dict] = None
    turn_count: int


async def _collect_chat_context(req: "ChatRequest", db: AsyncSession, redis) -> dict:
    """Resolve everything a chat turn needs: session, history, schema, enrichment,
    widgets, pages, priority tables, the effective connection, and model preference.
    Shared by the blocking /agent/chat and the streaming /agent/chat/stream paths.
    All DB access lives here so the streaming generator never touches the session."""
    session_id = req.session_id or str(uuid.uuid4())
    print(
        f"[chat] session={session_id[:8]}  project={req.project_id[:8]}  "
        f"dashboard={'yes' if req.dashboard_id else 'no'}  "
        f"model_pref={req.model_preference or 'default'}  msg_len={len(req.message)}",
        flush=True,
    )

    history = await ChatAgent.load_history(session_id, redis)
    schema_doc, connection_id_for_schema, db_type = await _get_schema_context(req.project_id, db)
    effective_connection_id = req.connection_id or connection_id_for_schema

    # Fallback for imported canvases whose project has no DatabaseConnection.
    if not effective_connection_id and req.dashboard_id:
        fallback_conn_id = await _get_dashboard_connection_id(req.dashboard_id, db)
        if fallback_conn_id:
            effective_connection_id = fallback_conn_id
            if not schema_doc:
                schema_doc, db_type = await _get_schema_for_connection(fallback_conn_id, db)
            print(f"[chat] using dashboard fallback connection={fallback_conn_id[:8]}", flush=True)

    enriched = None
    if schema_doc and effective_connection_id:
        try:
            enriched = await _schema_cache.get_or_build(effective_connection_id, schema_doc, db_type)
        except Exception as _e:
            print(f"[chat] ⚠ schema enrichment failed (non-fatal): {_e}", flush=True)

    dashboard_widgets: list[dict] = []
    dashboard_pages: list[dict] = []
    if req.dashboard_id:
        dashboard_widgets, dashboard_pages = await _get_dashboard_widgets_and_pages(req.dashboard_id, db)
        sql_widget_count = sum(1 for w in dashboard_widgets if w.get("sql_query"))
        print(
            f"[chat] dashboard loaded  widgets={len(dashboard_widgets)}  "
            f"with_sql={sql_widget_count}  pages={len(dashboard_pages)}",
            flush=True,
        )

    priority_tables = _extract_priority_tables(dashboard_widgets)

    effective_model_pref = req.model_preference
    if not effective_model_pref and len(req.message) > 8000:
        effective_model_pref = "opus"
        print(f"[chat] auto-upgraded to opus (msg_len={len(req.message)} > 8000)", flush=True)

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
    Returns (inline_chart | None, warning_text | None). The warning is appended to
    the assistant text so a failed/empty query never produces a silent blank."""
    if sql_spec.get("sql"):
        print(f"[chat] generated SQL: {sql_spec['sql'][:400]}", flush=True)
    if not connection_id:
        print("[chat] ⚠ no connection available to execute SQL", flush=True)
        return None, ("\n\n⚠️ I couldn't run the query: no active database connection "
                      "is available for this report.")
    if not sql_spec.get("sql"):
        return None, None

    exec_result = await _execute_sql(connection_id, sql_spec["sql"])
    if exec_result.get("error"):
        print(f"[chat] ⚠ sql exec error: {str(exec_result['error'])[:200]}", flush=True)
        return None, f"\n\n⚠️ The query failed to run: {exec_result['error']}"
    if not exec_result.get("rows"):
        print(f"[chat] ⚠ sql returned 0 rows  sql={sql_spec['sql'][:160]}", flush=True)
        return None, ("\n\n⚠️ The query ran but returned no rows — there may be no matching "
                      "data, or a name/date filter didn't match. Try rephrasing or broadening it.")

    render_result = await _render_chart(sql_spec, exec_result["rows"])
    columns = exec_result.get("columns", [])
    rows = exec_result.get("rows", [])
    labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
    # Single-column results (KPI scalars) → that column is the value;
    # multi-column → column[0] is the label, column[1] is the value.
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
    return inline_chart, None


def _no_sql_note() -> str:
    return ("\n\n⚠️ I wasn't able to generate the query for that. Try rephrasing, or name "
            "the table/columns you'd like me to use.")


@router.post("/agent/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
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
    )

    sql_spec = result.get("sql_to_execute")
    print(
        f"[chat] agent done  text_len={len(result.get('text', ''))}  "
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
        print("[chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

    updated_history = ctx["history"] + [
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": result["text"]},
    ]
    await ChatAgent.save_history(ctx["session_id"], updated_history, redis)

    return ChatResponse(
        session_id=ctx["session_id"],
        text=result["text"],
        inline_chart=inline_chart,
        dashboard_action=result.get("dashboard_action"),
        turn_count=len(updated_history) // 2,
    )


@router.post("/agent/chat/stream")
async def chat_stream(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    """Streaming variant of /agent/chat (Server-Sent Events).

    Streams the assistant's prose as it is generated, then runs the sql_execute
    block server-side and emits a final `chart` event. Event types: `text`
    (incremental prose delta), `chart` (rendered inline chart), `action`
    (dashboard action), `error`, and `done`. All DB access happens up front in
    _collect_chat_context, so the generator only touches Redis + httpx."""
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
    )

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

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
                # Stream prose only up to the first code fence — the sql_execute
                # block is parsed and executed server-side, never shown as raw text.
                if not fence_found:
                    idx = raw.find("```")
                    if idx != -1:
                        fence_found = True
                        safe = raw[:idx]
                    else:
                        safe = raw[:-2] if len(raw) > 2 else ""  # hold back a partial fence
                    if len(safe) > prose_emitted:
                        yield _sse({"type": "text", "delta": safe[prose_emitted:]})
                        prose_emitted = len(safe)
            elif kind == "error":
                errored = True
                yield _sse({"type": "error", "message": payload})

        if errored:
            yield _sse({"type": "done", "session_id": ctx["session_id"],
                        "turn_count": len(ctx["history"]) // 2})
            return

        # No fence at all → flush whatever prose remains.
        if not fence_found and len(raw) > prose_emitted:
            yield _sse({"type": "text", "delta": raw[prose_emitted:]})

        print(
            f"[chat] stream parsed  response_len={len(raw)}  "
            f"has_sql={'yes' if '```sql_execute' in raw else 'no'}",
            flush=True,
        )

        parsed = _agent.parse_raw(raw)
        sql_spec = parsed["sql_to_execute"]

        # Silent retry when a data/chart request produced no sql block.
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
            print("[chat] ⚠ data/chart request but no sql_execute block produced", flush=True)

        if parsed.get("dashboard_action"):
            yield _sse({"type": "action", "action": parsed["dashboard_action"]})

        updated_history = ctx["history"] + [
            {"role": "user", "content": req.message},
            {"role": "assistant", "content": final_text},
        ]
        await ChatAgent.save_history(ctx["session_id"], updated_history, redis)

        yield _sse({"type": "done", "session_id": ctx["session_id"],
                    "turn_count": len(updated_history) // 2})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── Export Chat endpoint ─────────────────────────────────────────────────────

class ExportChatRequest(BaseModel):
    message: str
    history: list[dict] = []


class ExportChatResponse(BaseModel):
    text: str
    session_id: str


@router.post("/agent/export-chat", response_model=ExportChatResponse)
async def export_chat(
    req: ExportChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Stateless chat endpoint for embedded AI panel in HTML exports.
    Authenticates via a short-lived export token passed as a Bearer token.
    Dashboard modification actions are disabled — read-only mode only.
    """
    # Extract bearer token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    raw_token = auth_header[len("Bearer "):].strip()
    export_token_record = await validate_export_token(db, raw_token, required_scope="chat:read")
    if not export_token_record:
        raise HTTPException(status_code=401, detail="Invalid, expired, or revoked export token")

    # Load schema for this export's project so the AI has context
    export_enriched = None
    export_schema_doc = {}
    export_conn_id = None
    try:
        export_schema_doc, export_conn_id, export_db_type = await _get_schema_context(
            str(export_token_record.project_id), db
        )
        if export_schema_doc and export_conn_id:
            export_enriched = await _schema_cache.get_or_build(
                export_conn_id, export_schema_doc, export_db_type
            )
    except Exception as _e:
        print(f"[export-chat] ⚠ schema enrichment failed (non-fatal): {_e}", flush=True)

    # Respond using ChatAgent in read-only mode (no dashboard actions)
    result = await _agent.respond(
        message=req.message,
        conversation_history=req.history[-20:],
        schema_doc=export_schema_doc,
        dashboard_widgets=[],
        enriched_schema=export_enriched,
        connection_id=export_conn_id,
    )

    # Strip any dashboard actions — export chat is read-only
    text = result.get("text", "")
    session_id = str(export_token_record.id)

    return ExportChatResponse(text=text, session_id=session_id)


@router.delete("/agent/chat/{session_id}")
async def clear_chat(
    session_id: str,
    redis=Depends(get_redis),
):
    await ChatAgent.clear_history(session_id, redis)
    return {"status": "cleared", "session_id": session_id}


# ── helpers ──────────────────────────────────────────────────────────────────

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
    """
    Fallback connection resolution for imported canvases whose project has no connection.
    Checks layout_config.connection_id first, then the first widget with a connection_id.
    """
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


async def _get_primary_connection(project_id: str, db: AsyncSession):
    result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.project_id == uuid.UUID(project_id))
        .where(DatabaseConnection.is_active == True)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _execute_sql(connection_id: str, sql: str) -> dict:
    # Redshift Serverless can take 60–120s to wake from idle, and heavy aggregations
    # (e.g. SUM over a multi-year timesheet table) run well past 20s. The old 20s
    # query timeout cancelled those mid-flight — the executor returned 0 rows, which
    # the chat reported as "no matching data" even though the data was there. Give it
    # a real budget so the query completes instead of being killed.
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
