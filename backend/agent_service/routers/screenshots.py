"""
Screenshot replication router.
POST /screenshot/upload  — upload screenshots + kick off pipeline
GET  /screenshot/jobs/{job_id}  — poll status
POST /screenshot/jobs/{job_id}/hint  — submit user hint response
GET  /screenshot/jobs/{job_id}/charts — chart replication state list
"""
import uuid
import os
import json
from datetime import datetime
from typing import Optional, List

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

PBIT_MIME_TYPES = {
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.ms-powerbi.template",
}
MAX_PBIT_SIZE = 50 * 1024 * 1024   # 50 MB

CSV_MIME_TYPES = {"text/csv", "application/csv", "text/plain", "text/x-csv", "application/octet-stream"}
MAX_CSV_SIZE = 50 * 1024 * 1024   # 50 MB per CSV
MAX_CSV_FILES = 10

# Context document upload (Mode 3 — Guided Replication)
CONTEXT_DOC_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/msword",                   # .doc
    "application/vnd.ms-powerpoint",        # .ppt
    "text/plain",
    "application/octet-stream",             # generic fallback (some browsers send this)
}
MAX_CONTEXT_DOC_SIZE = 10 * 1024 * 1024   # 10 MB per context document
MAX_CONTEXT_DOC_FILES = 3


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
    mode: str = Form("db"),
    user_table_hints: Optional[str] = Form(None),
    user_context: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    """Upload screenshot files and start the replication pipeline.

    mode="db"  — (default) AI auto-matches tables from the connected database.
    mode="csv" — no live DB needed; csv_files are used as the data source via DuckDB.

    user_table_hints   — JSON-encoded list of table names (e.g. '["staging.jobs"]')
                         that override schema matching and are tried first (Mode 2).
    user_context       — free-text description of the screenshot (Mode 3 — Guided Replication).
                         e.g. "Active placements by employment type for Q1 2024"
    pbit_file          — (form field, not FastAPI param) .pbit Power BI Template file.
                         Provides ground-truth field bindings, DAX measures, and relationships.
                         Highest-priority context source — overrides spec_reader and schema inference.
    user_column_hints  — (form field) JSON-encoded list of per-table column selections.
                         Format: [{"table": "staging.x", "dimension": "col1", "metric": "col2",
                                   "date": "col3", "group_by": "col4"}]
                         Overrides schema_matcher key_columns; combined with user_table_hints.
    """
    user_id = await _resolve_user_id(request, db)

    # Validate project_id early so we return 400 instead of 500
    try:
        project_uuid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid project_id: {project_id!r} is not a valid UUID")

    # Parse user table hints sent as a JSON string in form data
    hints: list[str] = []
    if user_table_hints:
        try:
            parsed = json.loads(user_table_hints)
            if isinstance(parsed, list):
                hints = [str(t) for t in parsed if t]
        except Exception:
            pass

    # Normalise user_context — strip whitespace, default to empty string
    ctx: str = (user_context or "").strip()

    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_FILES} files per upload")

    # Parse csv_files and context_files from the cached form data directly — avoids
    # Pydantic v2 coercion failure when exactly one file is uploaded.
    # Use multi_items() instead of getlist() for broadest Starlette compatibility.
    from starlette.datastructures import UploadFile as _StarletteUploadFile
    _form = await request.form()
    _form_keys = [k for k, _ in _form.multi_items()]
    print(f"[upload_screenshots] form keys: {_form_keys}", flush=True)
    csv_files: List[UploadFile] = [
        v for k, v in _form.multi_items()
        if k == "csv_files" and isinstance(v, _StarletteUploadFile)
    ]
    context_doc_files: List[UploadFile] = [
        v for k, v in _form.multi_items()
        if k == "context_files" and isinstance(v, _StarletteUploadFile)
    ]
    pbit_file_uploads: List[UploadFile] = [
        v for k, v in _form.multi_items()
        if k == "pbit_file" and isinstance(v, _StarletteUploadFile)
    ]
    print(
        f"[upload_screenshots] csv_files={len(csv_files)}  context_docs={len(context_doc_files)}"
        f"  pbit_files={len(pbit_file_uploads)}",
        flush=True,
    )

    # Extract text from context documents and merge with typed user_context
    if context_doc_files:
        if len(context_doc_files) > MAX_CONTEXT_DOC_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum {MAX_CONTEXT_DOC_FILES} context document files allowed",
            )
        from agent_service.utils.context_doc_extractor import extract_text as _extract_text, merge_context as _merge_context
        doc_extracts: list[tuple[str, str]] = []
        for cf in context_doc_files:
            cf_bytes = await cf.read()
            if len(cf_bytes) > MAX_CONTEXT_DOC_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"Context document '{cf.filename}' exceeds the 10 MB limit",
                )
            extracted = _extract_text(cf.filename or "document", cf_bytes)
            if extracted:
                doc_extracts.append((cf.filename or "document", extracted))
                print(
                    f"[upload_screenshots] extracted {len(extracted)} chars from '{cf.filename}'",
                    flush=True,
                )
            else:
                print(
                    f"[upload_screenshots] ⚠ no text extracted from '{cf.filename}' (unsupported or empty)",
                    flush=True,
                )
        ctx = _merge_context(ctx, doc_extracts)

    # Read PBIT file (Power BI Template) — optional; enables ground-truth field binding injection
    pbit_bytes: Optional[bytes] = None
    if pbit_file_uploads:
        pf = pbit_file_uploads[0]
        pf_bytes = await pf.read()
        if len(pf_bytes) > MAX_PBIT_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"PBIT file '{pf.filename}' exceeds the 50 MB limit",
            )
        pbit_bytes = pf_bytes
        print(
            f"[upload_screenshots] PBIT file received: '{pf.filename}'  size={len(pf_bytes)} bytes",
            flush=True,
        )

    # Parse user column hints (per-table column selections sent as JSON string)
    # Format: [{"table": "staging.x", "dimension": "col1", "metric": "col2", "date": "col3"}]
    user_col_hints: list = []
    _raw_col_hints = _form.get("user_column_hints", "")
    if _raw_col_hints:
        try:
            parsed_col_hints = json.loads(_raw_col_hints)
            if isinstance(parsed_col_hints, list):
                user_col_hints = [h for h in parsed_col_hints if isinstance(h, dict)]
        except Exception:
            pass

    # Validate and read CSV files for CSV mode
    csv_data: list[dict] = []
    if mode == "csv":
        incoming_csvs: list[UploadFile] = csv_files
        if not incoming_csvs:
            raise HTTPException(status_code=400, detail="CSV mode requires at least one CSV file")
        if len(incoming_csvs) > MAX_CSV_FILES:
            raise HTTPException(status_code=400, detail=f"Maximum {MAX_CSV_FILES} CSV files per upload")
        for cf in incoming_csvs:
            cf_bytes = await cf.read()
            if len(cf_bytes) > MAX_CSV_SIZE:
                raise HTTPException(
                    status_code=400,
                    detail=f"CSV file '{cf.filename}' exceeds the 50 MB limit",
                )
            csv_data.append({"filename": cf.filename or "data.csv", "bytes": cf_bytes})

    # Resolve connection_id — CSV mode needs no live DB connection
    if mode != "csv":
        if not connection_id:
            conn_result = await db.execute(
                select(DatabaseConnection)
                .where(DatabaseConnection.project_id == project_uuid)
                .where(DatabaseConnection.is_active == True)
                .limit(1)
            )
            conn = conn_result.scalar_one_or_none()
            if not conn:
                raise HTTPException(status_code=400, detail="No active database connection found for this project")
            connection_id = str(conn.id)
    else:
        # Sentinel value — orchestrator replaces it with "csv_session:/tmp/csv_{job_id}"
        connection_id = "csv_mode"

    # Validate + upload files
    uploaded = []
    stored_files = []
    for f in files:
        print(f"[upload_screenshots] screenshot file: {f.filename!r} content_type={f.content_type!r}", flush=True)
        if f.content_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {f.content_type!r} (allowed: image/png, image/jpeg, image/webp, image/gif)")
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
        project_id=project_uuid,
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
        project_id=project_uuid,
        user_id=user_id,
        job_type="SCREENSHOT",
        status="pending",
        input_payload={
            "screenshot_job_id": str(screenshot_job.id),
            "connection_id": connection_id,
            "mode": mode,
            "user_table_hints": hints,
            "user_context": ctx,
        },
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
        mode=mode,
        user_table_hints=hints,
        csv_data=csv_data,
        user_context=ctx,
        pbit_bytes=pbit_bytes,
        user_column_hints=user_col_hints,
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
    mode: str = "db",
    user_table_hints: list[str] = [],
    csv_data: list[dict] = [],
    user_context: str = "",
    pbit_bytes: Optional[bytes] = None,
    user_column_hints: list[dict] = [],
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
                mode=mode,
                user_table_hints=user_table_hints,
                csv_data=csv_data,
                user_context=user_context,
                pbit_bytes=pbit_bytes,
                user_column_hints=user_column_hints,
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
