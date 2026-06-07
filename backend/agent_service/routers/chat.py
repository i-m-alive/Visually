import uuid
import json
import os
from typing import Optional
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
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
from agent_service.agents.chat_agent import ChatAgent

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


class ChatResponse(BaseModel):
    session_id: str
    text: str
    inline_chart: Optional[dict] = None
    dashboard_action: Optional[dict] = None
    turn_count: int


@router.post("/agent/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    session_id = req.session_id or str(uuid.uuid4())

    # Load conversation history from Redis
    history = await ChatAgent.load_history(session_id, redis)

    # Load schema snapshot for this project
    schema_doc = await _get_project_schema(req.project_id, db)

    # Load dashboard widgets if dashboard_id provided
    dashboard_widgets = []
    if req.dashboard_id:
        dashboard_widgets = await _get_dashboard_widgets(req.dashboard_id, db)

    # Call Chat Agent
    result = await _agent.respond(
        message=req.message,
        conversation_history=history,
        schema_doc=schema_doc,
        dashboard_widgets=dashboard_widgets,
    )

    # Execute inline SQL if agent returned one
    inline_chart = None
    if result["sql_to_execute"]:
        sql_spec = result["sql_to_execute"]
        connection_id = req.connection_id
        if not connection_id:
            conn = await _get_primary_connection(req.project_id, db)
            if conn:
                connection_id = str(conn.id)

        if connection_id and sql_spec.get("sql"):
            exec_result = await _execute_sql(connection_id, sql_spec["sql"])
            if not exec_result.get("error") and exec_result.get("rows"):
                render_result = await _render_chart(sql_spec, exec_result["rows"])
                columns = exec_result.get("columns", [])
                rows = exec_result.get("rows", [])
                labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
                values = [r.get(columns[1]) for r in rows] if len(columns) > 1 else []
                inline_chart = {
                    "chart_type": sql_spec.get("chart_type", "table"),
                    "title": sql_spec.get("title", "Chart"),
                    "x_axis_label": sql_spec.get("x_label", columns[0] if columns else "x"),
                    "y_axis_label": sql_spec.get("y_label", columns[1] if len(columns) > 1 else "y"),
                    "chart_data": {
                        "rows": rows,
                        "columns": columns,
                        "labels": labels,
                        "values": values,
                    },
                    "sql": sql_spec["sql"],
                    "image_base64": render_result.get("image_base64"),
                }

    # Save updated conversation history
    updated_history = history + [
        {"role": "user", "content": req.message},
        {"role": "assistant", "content": result["text"]},
    ]
    await ChatAgent.save_history(session_id, updated_history, redis)

    return ChatResponse(
        session_id=session_id,
        text=result["text"],
        inline_chart=inline_chart,
        dashboard_action=result.get("dashboard_action"),
        turn_count=len(updated_history) // 2,
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

    # Respond using ChatAgent in read-only mode (no dashboard actions)
    result = await _agent.respond(
        message=req.message,
        conversation_history=req.history[-20:],
        schema_doc={},
        dashboard_widgets=[],
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

async def _get_project_schema(project_id: str, db: AsyncSession) -> dict:
    conn_result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.project_id == uuid.UUID(project_id))
        .where(DatabaseConnection.is_active == True)
        .limit(1)
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        return {}
    snap_result = await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    return snapshot.schema_document if snapshot else {}


async def _get_dashboard_widgets(dashboard_id: str, db: AsyncSession) -> list[dict]:
    result = await db.execute(
        select(Widget).where(Widget.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets = result.scalars().all()
    return [
        {
            "id": str(w.id),
            "title": w.title,
            "chart_type": w.chart_type,
            "sql_query": w.sql_query,
            "chart_data": w.chart_data,
        }
        for w in widgets
    ]


async def _get_primary_connection(project_id: str, db: AsyncSession):
    result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.project_id == uuid.UUID(project_id))
        .where(DatabaseConnection.is_active == True)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _execute_sql(connection_id: str, sql: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={"connection_id": connection_id, "sql": sql, "row_limit": 1000, "timeout_seconds": 20},
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
