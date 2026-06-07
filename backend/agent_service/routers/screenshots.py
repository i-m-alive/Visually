"""
Screenshot replication router.
POST /screenshot/upload  — upload screenshots + kick off pipeline
GET  /screenshot/jobs/{job_id}  — poll status
POST /screenshot/jobs/{job_id}/hint  — submit user hint response
GET  /screenshot/jobs/{job_id}/charts — chart replication state list
"""
import uuid
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import redis.asyncio as aioredis

from shared.database import get_db
from shared.redis_client import get_redis, publish_pipeline_event
from shared.file_storage import upload_file
from shared.models.pipeline_jobs import PipelineJob
from shared.models.database_connections import DatabaseConnection
from shared.models.phase3 import ScreenshotJob, ChartReplicationState, HintQueueEntry
from shared.security import decode_token
from shared.models.users import User

router = APIRouter(tags=["screenshots"])


_DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
_DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")


async def _resolve_user_id(request: Request, db: AsyncSession) -> uuid.UUID:
    """Extract user from Authorization header. In DEV_MODE returns the dev user."""
    if _DEV_MODE:
        return uuid.UUID(_DEV_USER_ID)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = decode_token(auth[7:])
            user_id = payload.get("sub")
            if user_id:
                return uuid.UUID(user_id)
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="Authentication required")

ALLOWED_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB per file
MAX_FILES = 5


class HintRequest(BaseModel):
    hint_id: str
    response: str


# ─── Upload ──────────────────────────────────────────────────────────────────

@router.post("/screenshot/upload")
async def upload_screenshots(
    request: Request,
    background_tasks: BackgroundTasks,
    project_id: str = Form(...),
    connection_id: Optional[str] = Form(None),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Upload screenshot files and start the replication pipeline."""
    user_id = await _resolve_user_id(request, db)

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files per upload")

    # Resolve connection if not provided
    if not connection_id:
        conn_result = await db.execute(
            select(DatabaseConnection)
            .where(DatabaseConnection.project_id == uuid.UUID(project_id))
            .where(DatabaseConnection.is_active == True)
            .limit(1)
        )
        conn = conn_result.scalar_one_or_none()
        if not conn:
            raise HTTPException(status_code=400, detail="No active database connection found for this project")
        connection_id = str(conn.id)

    # Validate + upload files
    uploaded = []
    stored_files = []
    for f in files:
        if f.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {f.content_type}")
        data = await f.read()
        if len(data) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail=f"File {f.filename} exceeds 20 MB limit")
        storage = await upload_file(data, f.filename or "screenshot.png", f.content_type or "image/png", project_id)
        uploaded.append({
            "filename": f.filename,
            "s3_key": storage["s3_key"],
            "size_bytes": storage["size_bytes"],
            "mime_type": f.content_type,
        })
        stored_files.append({"bytes": data, "filename": f.filename, "mime_type": f.content_type})

    # Create ScreenshotJob
    screenshot_job = ScreenshotJob(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        user_id=user_id,
        status="pending",
        uploaded_files=uploaded,
        total_charts=0,
        confirmed_charts=0,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(screenshot_job)

    # Create PipelineJob for WebSocket tracking
    job_id = str(uuid.uuid4())
    pipeline_job = PipelineJob(
        id=uuid.UUID(job_id),
        project_id=uuid.UUID(project_id),
        user_id=user_id,
        job_type="SCREENSHOT",
        status="pending",
        input_payload={"screenshot_job_id": str(screenshot_job.id), "connection_id": connection_id},
        created_at=datetime.utcnow(),
    )
    db.add(pipeline_job)
    await db.commit()
    await db.refresh(screenshot_job)

    # Launch pipeline in background
    background_tasks.add_task(
        _run_screenshot_pipeline_bg,
        job_id=job_id,
        screenshot_job_id=str(screenshot_job.id),
        stored_files=stored_files,
        project_id=project_id,
        user_id=str(user_id),
        connection_id=connection_id,
    )

    return {
        "job_id": job_id,
        "screenshot_job_id": str(screenshot_job.id),
        "status": "pending",
        "file_count": len(uploaded),
    }


async def _run_screenshot_pipeline_bg(
    job_id: str,
    screenshot_job_id: str,
    stored_files: list[dict],
    project_id: str,
    user_id: str,
    connection_id: str,
):
    """Background task: run screenshot pipeline."""
    from shared.database import AsyncSessionLocal
    from agent_service.agents.orchestrator import Orchestrator
    orch = Orchestrator()
    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        try:
            await orch.run_screenshot_pipeline(
                job_id=job_id,
                screenshot_job_id=screenshot_job_id,
                uploaded_images=stored_files,
                project_id=project_id,
                user_id=user_id,
                connection_id=connection_id,
                redis=redis,
                db=db,
            )
            # Mark pipeline job complete
            from sqlalchemy import select as _select
            from shared.models.pipeline_jobs import PipelineJob as _PJ
            r = await db.execute(_select(_PJ).where(_PJ.id == uuid.UUID(job_id)))
            pj = r.scalar_one_or_none()
            if pj:
                pj.status = "completed"
                pj.completed_at = datetime.utcnow()
                await db.commit()
        except Exception as e:
            # Rollback before publishing the error event so the connection is
            # returned to the pool clean — avoids "manually started transaction"
            # errors on subsequent status-poll requests.
            try:
                await db.rollback()
            except Exception:
                pass
            await publish_pipeline_event(redis, job_id, {
                "type": "pipeline.error", "job_id": job_id,
                "message": str(e), "recoverable": False,
            })
    await redis.aclose()


# ─── Status ──────────────────────────────────────────────────────────────────

@router.get("/screenshot/jobs/{job_id}")
async def get_screenshot_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get screenshot job status and chart replication states."""
    import traceback as _tb

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid job_id format: {job_id}")

    try:
        result = await db.execute(
            select(PipelineJob).where(PipelineJob.id == job_uuid)
        )
        pipeline_job = result.scalar_one_or_none()
    except Exception as exc:
        print(f"[status:{job_id}] DB error querying PipelineJob: {exc}", flush=True)
        print(_tb.format_exc(), flush=True)
        raise HTTPException(status_code=500, detail=f"DB error: {exc}")

    if not pipeline_job:
        raise HTTPException(status_code=404, detail="Job not found")

    screenshot_job_id = (pipeline_job.input_payload or {}).get("screenshot_job_id")
    chart_states = []
    screenshot_job_data = None

    if screenshot_job_id:
        try:
            sj_uuid = uuid.UUID(screenshot_job_id)
            sj_result = await db.execute(
                select(ScreenshotJob).where(ScreenshotJob.id == sj_uuid)
            )
            sj = sj_result.scalar_one_or_none()
            if sj:
                screenshot_job_data = {
                    "id": str(sj.id),
                    "status": sj.status,
                    "total_charts": sj.total_charts,
                    "confirmed_charts": sj.confirmed_charts,
                    "result_dashboard_id": str(sj.result_dashboard_id) if sj.result_dashboard_id else None,
                    "error_message": sj.error_message,
                }
        except Exception as exc:
            print(f"[status:{job_id}] DB error querying ScreenshotJob: {exc}", flush=True)
            print(_tb.format_exc(), flush=True)
            # Non-fatal — return partial data without screenshot_job

        try:
            states_result = await db.execute(
                select(ChartReplicationState).where(
                    ChartReplicationState.job_id == uuid.UUID(screenshot_job_id)
                )
            )
            for state in states_result.scalars().all():
                chart_states.append({
                    "chart_id": state.chart_id,
                    "status": state.status,
                    "attempt_count": state.attempt_count,
                    "validation_score": state.validation_score,
                    "hint_requested": state.hint_requested,
                    "hint_options": getattr(state, "hint_options", None),
                    "current_sql": state.current_sql,
                })
        except Exception as exc:
            print(f"[status:{job_id}] DB error querying ChartReplicationState: {exc}", flush=True)
            print(_tb.format_exc(), flush=True)
            # Non-fatal — return empty chart_states

    return {
        "job_id": job_id,
        "pipeline_status": pipeline_job.status,
        "job_type": pipeline_job.job_type,
        "screenshot_job": screenshot_job_data,
        "chart_states": chart_states,
        "created_at": pipeline_job.created_at.isoformat() if pipeline_job.created_at else None,
    }


# ─── Hint ────────────────────────────────────────────────────────────────────

@router.post("/screenshot/jobs/{job_id}/hint")
async def submit_hint(
    job_id: str,
    req: HintRequest,
    db: AsyncSession = Depends(get_db),
):
    """Submit user's hint response for a blocked chart replication attempt."""
    from agent_service.agents.orchestrator import _hint_events, _hint_responses

    hint_id = req.hint_id
    _hint_responses[hint_id] = req.response

    if hint_id in _hint_events:
        _hint_events[hint_id].set()
        return {"status": "accepted", "hint_id": hint_id}

    # If event already expired (user responded after timeout), just store in DB
    try:
        hq_result = await db.execute(
            select(HintQueueEntry).where(
                HintQueueEntry.options["hint_id"].astext == hint_id
            ).limit(1)
        )
        entry = hq_result.scalar_one_or_none()
        if entry:
            entry.is_answered = True
            entry.user_response = req.response
            await db.commit()
    except Exception:
        pass

    return {"status": "stored", "hint_id": hint_id, "note": "Hint will apply on next retry"}


# ─── Schema Cache API ────────────────────────────────────────────────────────

@router.get("/schema-cache/{connection_id}/export")
async def export_schema_cache(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Export the cached EnrichedSchema for a connection as a downloadable JSON string.
    Useful for committing / sharing pre-computed schema caches to speed up cold starts.
    Returns 404 when no cached schema is available.
    """
    from agent_service.agents.schema_cache import export_cache_json
    from fastapi.responses import Response

    json_str = export_cache_json(connection_id)
    if json_str is None:
        raise HTTPException(status_code=404, detail="No cached schema found for this connection. Run a replication first to warm the cache.")

    return Response(
        content=json_str,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="schema_cache_{connection_id}.json"',
        },
    )


@router.post("/schema-cache/{connection_id}/import")
async def import_schema_cache(
    connection_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Import a previously exported schema cache JSON into the in-process cache.
    Eliminates cold-start schema enrichment cost — the next replication uses
    the imported cache immediately.
    """
    from agent_service.agents.schema_cache import import_cache_json

    try:
        json_bytes = await file.read()
        json_str = json_bytes.decode("utf-8")
        enriched = import_cache_json(connection_id, json_str)
        return {
            "status": "imported",
            "connection_id": connection_id,
            "tables": len(enriched.schema_doc.get("tables", [])),
            "db_type": enriched.db_type,
        }
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid schema cache JSON: {exc}")


@router.delete("/schema-cache/{connection_id}")
async def invalidate_schema_cache(
    connection_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Invalidate (clear) the cached schema for a connection.
    Call this after a schema migration to force re-enrichment on the next replication.
    """
    from agent_service.agents.schema_cache import invalidate

    invalidate(connection_id)
    return {"status": "invalidated", "connection_id": connection_id}
