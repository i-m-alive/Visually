"""
Export router — triggers HTML/PDF/PNG dashboard exports and exposes job status
and file download endpoints.
"""
import os
import uuid
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db, AsyncSessionLocal
from shared.file_storage import download_file
from shared.models.dashboards import Dashboard
from shared.models.pipeline_jobs import PipelineJob
from shared.models.phase4 import ExportJob
from shared.models.users import User
from shared.security import decode_token

router = APIRouter(tags=["exports"])

bearer_scheme = HTTPBearer(auto_error=False)

DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")
EXPORT_SERVICE_URL = os.getenv("EXPORT_SERVICE_URL", "http://localhost:8005")


# ─── Pydantic models ──────────────────────────────────────────────────────────

class ExportTriggerRequest(BaseModel):
    dashboard_id: str
    project_id: str
    export_type: str = "html"          # html | pdf | png
    theme: str = "frost"
    include_chat: bool = True
    token_expiry_days: int = 30


class ExportJobResponse(BaseModel):
    export_job_id: str
    pipeline_job_id: str
    status: str
    export_type: str
    created_at: str


class ExportStatusResponse(BaseModel):
    export_job_id: str
    status: str
    export_type: str
    theme: Optional[str]
    download_url: Optional[str]
    file_size_bytes: Optional[int]
    error_message: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]


# ─── Auth helper ──────────────────────────────────────────────────────────────

async def _get_user_id(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> str:
    if DEV_MODE:
        # In dev mode return a stable dev user, creating it if needed
        dev_uuid = uuid.UUID(DEV_USER_ID)
        result = await db.execute(select(User).where(User.id == dev_uuid))
        user = result.scalar_one_or_none()
        if not user:
            from shared.security import hash_password
            user = User(
                id=dev_uuid,
                email=os.getenv("DEV_USER_EMAIL", "dev@visually.local"),
                hashed_password=hash_password("dev-password"),
                full_name="Dev User",
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(user)
            await db.commit()
        return DEV_USER_ID

    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload["sub"]


# ─── POST /export/trigger ─────────────────────────────────────────────────────

@router.post("/export/trigger", response_model=ExportJobResponse, status_code=202)
async def trigger_export(
    req: ExportTriggerRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    """
    Create an ExportJob + a tracking PipelineJob and kick off the export pipeline
    in a background task.
    """
    # Verify dashboard exists
    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(req.dashboard_id))
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Create ExportJob
    export_job = ExportJob(
        id=uuid.uuid4(),
        dashboard_id=uuid.UUID(req.dashboard_id),
        project_id=uuid.UUID(req.project_id),
        user_id=uuid.UUID(user_id),
        export_type=req.export_type,
        status="pending",
        theme=req.theme,
        include_chat=req.include_chat,
        token_expiry_days=req.token_expiry_days,
        created_at=datetime.utcnow(),
    )
    db.add(export_job)

    # Create a PipelineJob for tracking
    pipeline_job = PipelineJob(
        id=uuid.uuid4(),
        project_id=uuid.UUID(req.project_id),
        user_id=uuid.UUID(user_id),
        job_type="EXPORT",
        status="pending",
        input_payload={
            "export_job_id": str(export_job.id),
            "dashboard_id": req.dashboard_id,
            "export_type": req.export_type,
            "theme": req.theme,
        },
        created_at=datetime.utcnow(),
    )
    db.add(pipeline_job)
    await db.commit()
    await db.refresh(export_job)
    await db.refresh(pipeline_job)

    background_tasks.add_task(
        _run_export_bg,
        str(export_job.id),
        str(pipeline_job.id),
        req,
        user_id,
    )

    return ExportJobResponse(
        export_job_id=str(export_job.id),
        pipeline_job_id=str(pipeline_job.id),
        status="pending",
        export_type=req.export_type,
        created_at=export_job.created_at.isoformat(),
    )


# ─── Background task ──────────────────────────────────────────────────────────

async def _run_export_bg(
    export_job_id: str,
    pipeline_job_id: str,
    req: ExportTriggerRequest,
    user_id: str,
) -> None:
    """Background task: runs the export orchestration pipeline."""
    from agent_service.agents.orchestrator import Orchestrator
    from shared.redis_client import get_redis

    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        orchestrator = Orchestrator()
        try:
            await orchestrator.trigger_export(
                export_job_id=export_job_id,
                pipeline_job_id=pipeline_job_id,
                dashboard_id=req.dashboard_id,
                project_id=req.project_id,
                user_id=user_id,
                export_type=req.export_type,
                theme=req.theme,
                include_chat=req.include_chat,
                token_expiry_days=req.token_expiry_days,
                redis=redis,
                db=db,
            )
        except Exception as exc:
            # Mark both jobs failed
            async with AsyncSessionLocal() as err_db:
                ej_result = await err_db.execute(
                    select(ExportJob).where(ExportJob.id == uuid.UUID(export_job_id))
                )
                ej = ej_result.scalar_one_or_none()
                if ej:
                    ej.status = "failed"
                    ej.error_message = str(exc)
                    ej.completed_at = datetime.utcnow()

                pj_result = await err_db.execute(
                    select(PipelineJob).where(PipelineJob.id == uuid.UUID(pipeline_job_id))
                )
                pj = pj_result.scalar_one_or_none()
                if pj:
                    pj.status = "failed"
                    pj.error_message = str(exc)
                    pj.completed_at = datetime.utcnow()

                await err_db.commit()
    if redis is not None:
        await redis.aclose()


# ─── GET /export/jobs/{job_id} ────────────────────────────────────────────────

@router.get("/export/jobs/{job_id}", response_model=ExportStatusResponse)
async def get_export_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    """Return the current status and metadata of an export job."""
    result = await db.execute(
        select(ExportJob).where(ExportJob.id == uuid.UUID(job_id))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    return ExportStatusResponse(
        export_job_id=str(job.id),
        status=job.status,
        export_type=job.export_type,
        theme=job.theme,
        download_url=job.download_url,
        file_size_bytes=job.file_size_bytes,
        error_message=job.error_message,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


# ─── GET /export/jobs/{job_id}/download ──────────────────────────────────────

@router.get("/export/jobs/{job_id}/download")
async def download_export(
    job_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(_get_user_id),
):
    """
    Stream the exported file back to the client.
    Works for both local filesystem and S3/MinIO storage.
    """
    result = await db.execute(
        select(ExportJob).where(ExportJob.id == uuid.UUID(job_id))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found")

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Export job is not ready (status={job.status})",
        )

    if not job.s3_key:
        raise HTTPException(status_code=404, detail="No file associated with this export job")

    try:
        file_bytes = await download_file(job.s3_key)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Export file not found in storage")

    ext = job.export_type.lower()
    media_type_map = {
        "html": "text/html; charset=utf-8",
        "pdf": "application/pdf",
        "png": "image/png",
    }
    media_type = media_type_map.get(ext, "application/octet-stream")
    filename = f"dashboard-export-{job_id[:8]}.{ext}"

    return Response(
        content=file_bytes,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(file_bytes)),
            "Cache-Control": "no-cache",
        },
    )
