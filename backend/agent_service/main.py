import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load visually/.env before any module-level os.getenv() calls fire
_env_file = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
if os.path.exists(_env_file):
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_env_file, override=True)

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, BackgroundTasks, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete

from shared.database import get_db
from shared.redis_client import get_redis
from shared.models.users import User
from shared.models.refresh_tokens import RefreshToken
from shared.models.pipeline_jobs import PipelineJob
from shared.models.database_connections import DatabaseConnection
from shared.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from shared.schemas.auth import RegisterRequest, LoginRequest, TokenResponse, RefreshRequest, UserResponse
from agent_service.services.ws_manager import manager, redis_listener
from agent_service.agents.orchestrator import Orchestrator
from agent_service.agents.intent_classifier import IntentClassifier as QuickClassifier
from agent_service.routers import chat as chat_module
from agent_service.routers import exports as exports_module
from agent_service.routers import query_sessions as query_sessions_module
from agent_service.routers import vly as vly_module
from agent_service.routers import share as share_module
from agent_service.routers import public_canvas as public_canvas_module
from agent_service.routers import tier5 as tier5_module
from agent_service.routers import ai_insights as ai_insights_module
from agent_service.routers import analyst as analyst_module
from agent_service.routers import end_user as end_user_module
from agent_service.routers import intelligence as intelligence_module

from contextlib import asynccontextmanager

async def _backfill_project_members():
    """Ensure every project has a ProjectMember row for its owner.
    Runs once at startup to fix projects created before this constraint was enforced."""
    from shared.database import AsyncSessionLocal
    from shared.models.projects import Project
    from shared.models.project_members import ProjectMember, MemberRole
    async with AsyncSessionLocal() as db:
        try:
            projects = (await db.execute(select(Project))).scalars().all()
            for project in projects:
                existing = (await db.execute(
                    select(ProjectMember)
                    .where(ProjectMember.project_id == project.id)
                    .where(ProjectMember.user_id == project.owner_id)
                )).scalar_one_or_none()
                if not existing:
                    db.add(ProjectMember(
                        id=uuid.uuid4(),
                        project_id=project.id,
                        user_id=project.owner_id,
                        role=MemberRole.owner,
                        joined_at=project.created_at or datetime.utcnow(),
                    ))
            await db.commit()
        except Exception as e:
            print(f"[startup] ProjectMember backfill warning: {e}")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from agent_service.scheduler import start_scheduler, stop_scheduler
    await _backfill_project_members()
    start_scheduler()
    yield
    stop_scheduler()

app = FastAPI(title="Visually Agent Service", version="2.0.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    # Expose download headers so the browser JS can read the server-supplied filename
    expose_headers=["Content-Disposition", "X-Vly-Version", "X-Vly-Canvas"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Sanitize validation errors — strip non-JSON-serializable values (e.g. UploadFile)
    from the 'input' field so jsonable_encoder never tries to encode file objects."""
    errors = []
    for error in exc.errors():
        sanitized = {k: v for k, v in error.items() if k != "input"}
        errors.append(sanitized)
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def add_cors_on_error(request, call_next):
    import traceback  # noqa: PLC0415
    try:
        response = await call_next(request)
    except Exception as exc:
        traceback.print_exc()
        response = JSONResponse({"detail": str(exc)}, status_code=500)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "*"
    return response

bearer_scheme = HTTPBearer(auto_error=False)

app.include_router(chat_module.router)
app.include_router(exports_module.router)
app.include_router(query_sessions_module.router)
app.include_router(vly_module.router)
app.include_router(share_module.router)
app.include_router(public_canvas_module.router)
app.include_router(tier5_module.router)
app.include_router(ai_insights_module.router)
app.include_router(analyst_module.router)
app.include_router(end_user_module.router)
app.include_router(intelligence_module.router)

DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")
DEV_USER_EMAIL = os.getenv("DEV_USER_EMAIL", "dev@visually.local")


def _hash_rt(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _get_or_create_dev_user(db: AsyncSession) -> User:
    """Return the dev user, creating it in the DB on first run."""
    dev_id = uuid.UUID(DEV_USER_ID)
    result = await db.execute(select(User).where(User.id == dev_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(
            id=dev_id,
            email=DEV_USER_EMAIL,
            hashed_password=hash_password("dev-password"),
            full_name="Dev User",
            is_active=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # DEV_MODE: only use the dev user when no JWT is present (direct API testing).
    # When a real JWT is provided (browser sessions), always validate it so each
    # registered user gets their own isolated data.
    if DEV_MODE and credentials is None:
        return await _get_or_create_dev_user(db)
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")
    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ─── AUTH ROUTES ────────────────────────────────────────────────────────────

@app.post("/auth/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")
    role = getattr(req, "role", "builder") or "builder"
    user = User(
        id=uuid.uuid4(), email=req.email,
        hashed_password=hash_password(req.password),
        full_name=req.full_name,
        role=role,
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id), "role": user.role})
    db.add(RefreshToken(
        id=uuid.uuid4(), user_id=user.id, token_hash=_hash_rt(refresh_token),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        created_at=datetime.utcnow(),
    ))
    await db.commit()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token,
                        user_id=str(user.id), email=user.email, full_name=user.full_name, role=user.role)


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive")
    access_token = create_access_token({"sub": str(user.id), "role": user.role})
    refresh_token = create_refresh_token({"sub": str(user.id), "role": user.role})
    db.add(RefreshToken(
        id=uuid.uuid4(), user_id=user.id, token_hash=_hash_rt(refresh_token),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        created_at=datetime.utcnow(),
    ))
    await db.commit()
    return TokenResponse(access_token=access_token, refresh_token=refresh_token,
                        user_id=str(user.id), email=user.email, full_name=user.full_name, role=user.role)


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = decode_token(req.refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type")
    token_hash = _hash_rt(req.refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if not stored or stored.is_revoked or stored.expires_at < datetime.utcnow():
        raise HTTPException(status_code=401, detail="Refresh token expired or revoked")
    stored.is_revoked = True
    user_id = payload["sub"]
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    new_access = create_access_token({"sub": str(user.id), "role": user.role})
    new_refresh = create_refresh_token({"sub": str(user.id), "role": user.role})
    db.add(RefreshToken(
        id=uuid.uuid4(), user_id=user.id, token_hash=_hash_rt(new_refresh),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        created_at=datetime.utcnow(),
    ))
    await db.commit()
    return TokenResponse(access_token=new_access, refresh_token=new_refresh,
                        user_id=str(user.id), email=user.email, full_name=user.full_name, role=user.role)


@app.get("/auth/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=str(current_user.id), email=current_user.email,
                       full_name=current_user.full_name, is_active=current_user.is_active,
                       role=current_user.role)


class UpdateMeRequest(BaseModel):
    full_name: str | None = None
    role: str | None = None


@app.patch("/auth/me", response_model=UserResponse)
async def update_me(
    req: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if req.full_name is not None:
        current_user.full_name = req.full_name.strip() or current_user.full_name
    if req.role is not None and req.role in ("builder", "end_user"):
        current_user.role = req.role
    current_user.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(current_user)
    return UserResponse(id=str(current_user.id), email=current_user.email,
                       full_name=current_user.full_name, is_active=current_user.is_active,
                       role=current_user.role)


# ─── PROJECT ROUTES ──────────────────────────────────────────────────────────

from shared.models.projects import Project
from shared.models.project_members import ProjectMember, MemberRole
from shared.models.database_connections import DbType
from shared.models.schema_snapshots import SchemaSnapshot
from shared.encryption import encrypt, decrypt
from shared.schemas.projects import (
    ProjectCreate, ProjectResponse, ConnectionCreate, ConnectionResponse, ConnectionTestResult,
)
import httpx

SCHEMA_CRAWLER_URL = os.getenv("SCHEMA_CRAWLER_URL", "http://localhost:8003")


@app.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(req: ProjectCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    project = Project(id=uuid.uuid4(), name=req.name, description=req.description,
                     owner_id=current_user.id, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(project)
    db.add(ProjectMember(id=uuid.uuid4(), project_id=project.id, user_id=current_user.id,
                        role=MemberRole.owner, joined_at=datetime.utcnow()))
    await db.commit()
    await db.refresh(project)
    return ProjectResponse(id=str(project.id), name=project.name, description=project.description,
                          owner_id=str(project.owner_id), created_at=project.created_at.isoformat())


@app.get("/projects", response_model=list[ProjectResponse])
async def list_projects(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from sqlalchemy import or_
    result = await db.execute(
        select(Project)
        .outerjoin(ProjectMember,
                   (ProjectMember.project_id == Project.id) &
                   (ProjectMember.user_id == current_user.id))
        .where(
            or_(Project.owner_id == current_user.id,
                ProjectMember.user_id == current_user.id)
        )
        .distinct()
        .order_by(Project.created_at.desc())
    )
    return [ProjectResponse(id=str(p.id), name=p.name, description=p.description,
                           owner_id=str(p.owner_id), created_at=p.created_at.isoformat())
            for p in result.scalars().all()]


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    from sqlalchemy import or_
    access = await db.execute(
        select(Project)
        .outerjoin(ProjectMember,
                   (ProjectMember.project_id == Project.id) &
                   (ProjectMember.user_id == current_user.id))
        .where(Project.id == project.id)
        .where(or_(Project.owner_id == current_user.id,
                   ProjectMember.user_id == current_user.id))
    )
    if not access.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")
    return ProjectResponse(id=str(project.id), name=project.name, description=project.description,
                          owner_id=str(project.owner_id), created_at=project.created_at.isoformat())


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if str(project.owner_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not authorized to delete this project")
    # Collect dashboards and widgets for this project
    dash_rows = await db.execute(
        select(Dashboard.id).where(Dashboard.project_id == project.id)
    )
    dashboard_ids = [r[0] for r in dash_rows.all()]

    if dashboard_ids:
        widget_rows = await db.execute(
            select(WidgetModel.id).where(WidgetModel.dashboard_id.in_(dashboard_ids))
        )
        widget_ids = [r[0] for r in widget_rows.all()]

        # NULL out dashboard references
        await db.execute(
            sa_update(ChatSession)
            .where(ChatSession.dashboard_id.in_(dashboard_ids))
            .values(dashboard_id=None)
            .execution_options(synchronize_session=False)
        )

        # Delete widgets then dashboards
        await db.execute(
            sa_delete(WidgetModel)
            .where(WidgetModel.dashboard_id.in_(dashboard_ids))
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            sa_delete(Dashboard)
            .where(Dashboard.id.in_(dashboard_ids))
            .execution_options(synchronize_session=False)
        )

    await db.delete(project)
    await db.commit()
    return {"deleted": project_id}


@app.post("/projects/{project_id}/connections", response_model=ConnectionResponse, status_code=201)
async def add_connection(project_id: str, req: ConnectionCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
    encrypted_pw = encrypt(req.password) if req.password else None
    conn = DatabaseConnection(id=uuid.uuid4(), project_id=uuid.UUID(project_id), name=req.name,
                             db_type=DbType(req.db_type), host=req.host, port=req.port,
                             database_name=req.database_name, username=req.username,
                             encrypted_password=encrypted_pw, ssl_enabled=req.ssl_enabled,
                             connection_options=req.connection_options,
                             created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(conn)
    await db.commit()
    await db.refresh(conn)
    return ConnectionResponse(id=str(conn.id), project_id=str(conn.project_id), name=conn.name,
                             db_type=conn.db_type.value, host=conn.host, port=conn.port,
                             database_name=conn.database_name, username=conn.username,
                             ssl_enabled=conn.ssl_enabled, is_active=conn.is_active,
                             last_tested_at=conn.last_tested_at.isoformat() if conn.last_tested_at else None,
                             created_at=conn.created_at.isoformat())


@app.post("/projects/{project_id}/connections/{conn_id}/test", response_model=ConnectionTestResult)
async def test_connection(project_id: str, conn_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseConnection).where(
        DatabaseConnection.id == uuid.UUID(conn_id), DatabaseConnection.project_id == uuid.UUID(project_id)))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    password = decrypt(conn.encrypted_password) if conn.encrypted_password else ""
    import time
    start = time.monotonic()
    try:
        if conn.db_type.value == "redshift":
            import redshift_connector, os as _os
            _is_serverless = "redshift-serverless" in (conn.host or "")
            conn_kwargs: dict = {
                "host": conn.host, "port": conn.port or 5439,
                "database": conn.database_name or "", "ssl": True, "timeout": 15,
            }
            if _is_serverless:
                conn_kwargs["is_serverless"] = True
            if not password:
                conn_kwargs["iam"] = True
                conn_kwargs["aws_access_key_id"] = _os.getenv("AWS_ACCESS_KEY_ID", "")
                conn_kwargs["aws_secret_access_key"] = _os.getenv("AWS_SECRET_ACCESS_KEY", "")
                tok = _os.getenv("AWS_SESSION_TOKEN", "")
                if tok:
                    conn_kwargs["aws_session_token"] = tok
                conn_kwargs["database_user"] = conn.username or "awsuser"
            else:
                conn_kwargs["user"] = conn.username or ""
                conn_kwargs["password"] = password
            import asyncio as _asyncio
            def _sync_test():
                rc = redshift_connector.connect(**conn_kwargs)
                rc.close()
            await _asyncio.get_event_loop().run_in_executor(None, _sync_test)
        elif conn.db_type.value == "postgresql":
            import asyncpg
            pg_conn = await asyncpg.connect(host=conn.host, port=conn.port or 5432,
                database=conn.database_name, user=conn.username, password=password, command_timeout=10)
            await pg_conn.close()
        elif conn.db_type.value == "mysql":
            import aiomysql
            my_conn = await aiomysql.connect(host=conn.host, port=conn.port or 3306,
                db=conn.database_name, user=conn.username, password=password, connect_timeout=10)
            my_conn.close()
        else:
            return ConnectionTestResult(success=False, message=f"Cannot test {conn.db_type.value} yet")
        latency = (time.monotonic() - start) * 1000
        conn.last_tested_at = datetime.utcnow()
        await db.commit()
        return ConnectionTestResult(success=True, message="Connection successful", latency_ms=round(latency, 1))
    except Exception as e:
        print(f"[connection-test] {conn.db_type.value} test failed: {e}")
        return ConnectionTestResult(success=False, message=str(e))


@app.post("/projects/{project_id}/schema/crawl")
async def trigger_schema_crawl(project_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DatabaseConnection).where(
        DatabaseConnection.project_id == uuid.UUID(project_id), DatabaseConnection.is_active == True).limit(1))
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found for this project")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{SCHEMA_CRAWLER_URL}/crawl",
                json={"connection_id": str(conn.id), "project_id": project_id})
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Schema crawler unavailable: {e}")


@app.get("/projects/{project_id}/schema/crawl/{job_id}")
async def get_crawl_status(project_id: str, job_id: str, current_user: User = Depends(get_current_user)):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{SCHEMA_CRAWLER_URL}/crawl/{job_id}")
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Schema crawler unavailable: {e}")


@app.get("/projects/{project_id}/schema")
async def get_schema(project_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    conn_result = await db.execute(select(DatabaseConnection).where(
        DatabaseConnection.project_id == uuid.UUID(project_id), DatabaseConnection.is_active == True).limit(1))
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found")
    snap_result = await db.execute(select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.version.desc()).limit(1))
    snapshot = snap_result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="No schema snapshot found. Please crawl first.")
    return {"connection_id": str(conn.id), "snapshot_id": str(snapshot.id),
            "version": snapshot.version, "created_at": snapshot.created_at.isoformat(),
            "schema": snapshot.schema_document}


@app.get("/projects/{project_id}/schema/metadata")
async def get_schema_metadata(project_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
    conn_result = await db.execute(select(DatabaseConnection).where(
        DatabaseConnection.project_id == uuid.UUID(project_id), DatabaseConnection.is_active == True).limit(1))
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found")

    tbl_rows = (await db.execute(
        select(SchemaTableMetadata)
        .where(SchemaTableMetadata.connection_id == conn.id)
        .order_by(SchemaTableMetadata.table_name)
    )).scalars().all()

    if not tbl_rows:
        raise HTTPException(status_code=404, detail="Metadata not extracted yet — trigger a crawl and wait ~1 minute.")

    col_rows = (await db.execute(
        select(SchemaColumnMetadata)
        .where(SchemaColumnMetadata.connection_id == conn.id)
        .order_by(SchemaColumnMetadata.table_name, SchemaColumnMetadata.column_name)
    )).scalars().all()

    cols_by_table: dict[str, list] = {}
    for c in col_rows:
        cols_by_table.setdefault(c.table_name, []).append({
            "column_name": c.column_name,
            "business_name": c.business_name,
            "description": c.description,
            "semantic_type": c.semantic_type,
            "fk_target_table": c.fk_target_table,
            "fk_target_column": c.fk_target_column,
            "fk_confirmed": c.fk_confirmed,
            "example_values": c.example_values or [],
            "is_kpi_metric": c.is_kpi_metric,
            "is_dimension": c.is_dimension,
            "is_filter_eligible": c.is_filter_eligible,
        })

    tables = [
        {
            "table_name": t.table_name,
            "business_name": t.business_name,
            "description": t.description,
            "grain": t.grain,
            "is_fact_table": t.is_fact_table,
            "use_for": t.use_for or [],
            "never_use_for": t.never_use_for or [],
            "key_metric_cols": t.key_metric_cols or [],
            "key_dimension_cols": t.key_dimension_cols or [],
            "key_date_cols": t.key_date_cols or [],
            "generated_at": t.generated_at.isoformat() if t.generated_at else None,
            "columns": cols_by_table.get(t.table_name, []),
        }
        for t in tbl_rows
    ]
    return {"connection_id": str(conn.id), "tables": tables}


# ─── AGENT ROUTES ────────────────────────────────────────────────────────────

class IntentSubmitRequest(BaseModel):
    text: str
    project_id: str
    connection_id: Optional[str] = None


_orchestrator = Orchestrator()
_quick_classifier = QuickClassifier()


async def _run_pipeline(job_id: str, user_text: str, project_id: str, user_id: str, connection_id: str, job_type: str):
    from shared.database import AsyncSessionLocal
    redis = await get_redis()
    async with AsyncSessionLocal() as db:
        if job_type == "DASHBOARD":
            await _orchestrator.run_dashboard_pipeline(
                job_id=job_id, user_text=user_text, project_id=project_id,
                user_id=user_id, connection_id=connection_id, redis=redis, db=db,
            )
        else:
            await _orchestrator.run_single_viz_pipeline(
                job_id=job_id, user_text=user_text, project_id=project_id,
                user_id=user_id, connection_id=connection_id, redis=redis, db=db,
            )
    if redis is not None:
        await redis.aclose()


@app.post("/agent/intent")
async def submit_intent(
    req: IntentSubmitRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    connection_id = req.connection_id
    if not connection_id:
        conn_result = await db.execute(select(DatabaseConnection).where(
            DatabaseConnection.project_id == uuid.UUID(req.project_id),
            DatabaseConnection.is_active == True).limit(1))
        conn = conn_result.scalar_one_or_none()
        if not conn:
            raise HTTPException(status_code=400, detail="No active database connection. Please add a connection and crawl first.")
        connection_id = str(conn.id)

    # Pre-classify to decide pipeline type
    try:
        intent_preview = await _quick_classifier.classify(req.text)
        job_type = intent_preview.intent_type  # SINGLE_VIZ | DASHBOARD | FOLLOWUP
    except Exception:
        job_type = "SINGLE_VIZ"

    job_id = str(uuid.uuid4())
    job = PipelineJob(
        id=uuid.UUID(job_id), project_id=uuid.UUID(req.project_id), user_id=current_user.id,
        job_type=job_type, status="pending",
        input_payload={"text": req.text, "connection_id": connection_id},
        created_at=datetime.utcnow(),
    )
    db.add(job)
    await db.commit()

    background_tasks.add_task(_run_pipeline, job_id, req.text, req.project_id, str(current_user.id), connection_id, job_type)
    return {"job_id": job_id, "status": "pending", "job_type": job_type}


@app.get("/agent/jobs/{job_id}")
async def get_job(job_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PipelineJob).where(PipelineJob.id == uuid.UUID(job_id)))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": str(job.id), "status": job.status, "job_type": job.job_type,
        "result": job.result_payload, "error": job.error_message,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


@app.post("/agent/jobs/{job_id}/hint")
async def submit_hint(job_id: str, hint: dict, current_user: User = Depends(get_current_user)):
    return {"status": "hint_received", "job_id": job_id, "note": "Follow-up hints available in Phase 3"}


@app.websocket("/agent/stream/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    redis = await get_redis()
    await manager.connect(job_id, websocket)
    listener_task = asyncio.create_task(redis_listener(redis, job_id)) if redis is not None else None
    try:
        while True:
            try:
                await websocket.receive_text()
            except Exception:
                break
    finally:
        manager.disconnect(job_id, websocket)
        if listener_task is not None:
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        if redis is not None:
            await redis.aclose()


# ─── DASHBOARD ROUTES ────────────────────────────────────────────────────────

from sqlalchemy import update as sa_update, func
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget as WidgetModel
from shared.models.chat_sessions import ChatSession
from pydantic import BaseModel as _BM


class DashboardPatch(_BM):
    theme: str | None = None
    name: str | None = None
    description: str | None = None
    layout_config: dict | None = None


@app.get("/dashboards")
async def list_dashboards(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Verify the calling user has access to this project
    from sqlalchemy import or_
    proj_check = await db.execute(
        select(Project)
        .outerjoin(ProjectMember,
                   (ProjectMember.project_id == Project.id) &
                   (ProjectMember.user_id == current_user.id))
        .where(Project.id == uuid.UUID(project_id))
        .where(or_(Project.owner_id == current_user.id,
                   ProjectMember.user_id == current_user.id))
    )
    if not proj_check.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    result = await db.execute(
        select(Dashboard)
        .where(Dashboard.project_id == uuid.UUID(project_id))
        .where(Dashboard.is_archived == False)
        .order_by(Dashboard.updated_at.desc())
    )
    dashboards = result.scalars().all()
    if not dashboards:
        return {"dashboards": []}

    dash_ids = [d.id for d in dashboards]
    wc_result = await db.execute(
        select(WidgetModel.dashboard_id, func.count(WidgetModel.id).label("cnt"))
        .where(WidgetModel.dashboard_id.in_(dash_ids))
        .group_by(WidgetModel.dashboard_id)
    )
    widget_counts = {str(row.dashboard_id): row.cnt for row in wc_result.all()}

    return {
        "dashboards": [
            {
                "id": str(d.id),
                "name": d.name,
                "description": d.description,
                "theme": d.theme or "frost",
                "project_id": str(d.project_id),
                "created_at": d.created_at.isoformat(),
                "updated_at": (d.updated_at or d.created_at).isoformat(),
                "widget_count": widget_counts.get(str(d.id), 0),
                "has_schedule": bool(d.layout_config and d.layout_config.get("schedule_enabled")),
            }
            for d in dashboards
        ]
    }


@app.get("/dashboards/all")
async def list_all_dashboards(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return non-archived dashboards scoped to projects the current user owns or is a member of."""
    from shared.models.projects import Project
    from sqlalchemy import or_
    result = await db.execute(
        select(Dashboard, Project.name.label("project_name"))
        .join(Project, Dashboard.project_id == Project.id)
        .outerjoin(ProjectMember,
                   (ProjectMember.project_id == Project.id) &
                   (ProjectMember.user_id == current_user.id))
        .where(or_(Project.owner_id == current_user.id,
                   ProjectMember.user_id == current_user.id))
        .where(Dashboard.is_archived == False)
        .distinct()
        .order_by(Dashboard.updated_at.desc())
    )
    rows = result.all()
    if not rows:
        return {"dashboards": []}

    dash_ids = [d.id for d, _ in rows]
    wc_result = await db.execute(
        select(WidgetModel.dashboard_id, func.count(WidgetModel.id).label("cnt"))
        .where(WidgetModel.dashboard_id.in_(dash_ids))
        .group_by(WidgetModel.dashboard_id)
    )
    widget_counts = {str(row.dashboard_id): row.cnt for row in wc_result.all()}

    return {
        "dashboards": [
            {
                "id": str(d.id),
                "name": d.name,
                "description": d.description,
                "theme": d.theme or "frost",
                "project_name": project_name,
                "project_id": str(d.project_id),
                "created_at": d.created_at.isoformat(),
                "updated_at": (d.updated_at or d.created_at).isoformat(),
                "widget_count": widget_counts.get(str(d.id), 0),
                "has_schedule": bool(d.layout_config and d.layout_config.get("schedule_enabled")),
            }
            for d, project_name in rows
        ]
    }


@app.get("/dashboards/shared-with-me")
async def list_shared_with_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return dashboards explicitly shared with the current user via CanvasCollaborator."""
    from shared.models.sharing import CanvasCollaborator
    from shared.models.projects import Project

    result = await db.execute(
        select(Dashboard, Project.name.label("project_name"))
        .join(CanvasCollaborator, CanvasCollaborator.dashboard_id == Dashboard.id)
        .join(Project, Dashboard.project_id == Project.id)
        .where(CanvasCollaborator.user_id == current_user.id)
        .where(Dashboard.is_archived == False)
        .distinct()
        .order_by(Dashboard.updated_at.desc())
    )
    rows = result.all()

    if not rows:
        return {"dashboards": []}

    dash_ids = [d.id for d, _ in rows]
    wc_result = await db.execute(
        select(WidgetModel.dashboard_id, func.count(WidgetModel.id).label("cnt"))
        .where(WidgetModel.dashboard_id.in_(dash_ids))
        .group_by(WidgetModel.dashboard_id)
    )
    widget_counts = {str(row.dashboard_id): row.cnt for row in wc_result.all()}

    return {
        "dashboards": [
            {
                "id":               str(d.id),
                "name":             d.name,
                "description":      d.description,
                "theme":            d.theme or "frost",
                "project_name":     project_name,
                "project_id":       str(d.project_id),
                "created_at":       d.created_at.isoformat(),
                "updated_at":       (d.updated_at or d.created_at).isoformat(),
                "widget_count":     widget_counts.get(str(d.id), 0),
                "has_schedule":     bool(d.layout_config and d.layout_config.get("schedule_enabled")),
                # Import provenance — populated by end-user/import-vly
                "is_imported":      bool(d.layout_config and d.layout_config.get("is_imported")),
                "imported_at":      (d.layout_config or {}).get("imported_at"),
                "imported_by":      (d.layout_config or {}).get("imported_by"),
                "has_intelligence": bool(d.layout_config and d.layout_config.get("has_intelligence")),
                "connection_hint":  (d.layout_config or {}).get("connection_hint", {}),
            }
            for d, project_name in rows
        ]
    }


@app.post("/dashboards/{dashboard_id}/analyst-token")
async def get_analyst_token(
    dashboard_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Issue a short-lived live analyst token for a dashboard the user has access to.
    The caller can then use all /analyst/canvas/{token}/... endpoints without re-auth."""
    import secrets
    import hashlib as _hl
    from datetime import timedelta as _td
    from shared.models.sharing import CanvasCollaborator, CanvasShareToken
    from sqlalchemy import or_ as _or

    dash_id = uuid.UUID(dashboard_id)

    # Access check: CanvasCollaborator OR project member/owner
    collab_result = await db.execute(
        select(CanvasCollaborator)
        .where(CanvasCollaborator.dashboard_id == dash_id)
        .where(CanvasCollaborator.user_id == current_user.id)
    )
    has_access = collab_result.scalar_one_or_none() is not None

    if not has_access:
        dash_result = await db.execute(select(Dashboard).where(Dashboard.id == dash_id))
        dashboard = dash_result.scalar_one_or_none()
        if not dashboard:
            raise HTTPException(status_code=404, detail="Dashboard not found")
        proj_result = await db.execute(
            select(Project)
            .outerjoin(ProjectMember,
                       (ProjectMember.project_id == Project.id) &
                       (ProjectMember.user_id == current_user.id))
            .where(Project.id == dashboard.project_id)
            .where(_or(Project.owner_id == current_user.id, ProjectMember.user_id == current_user.id))
        )
        if not proj_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="Access denied")

    raw = secrets.token_urlsafe(32)
    token_hash = _hl.sha256(raw.encode()).hexdigest()
    expires = datetime.utcnow() + _td(hours=24)

    new_token = CanvasShareToken(
        id=uuid.uuid4(),
        dashboard_id=dash_id,
        token_hash=token_hash,
        mode="live",
        label="analyst-session",
        expires_at=expires,
        is_revoked=False,
        created_by=current_user.id,
    )
    db.add(new_token)
    await db.commit()

    return {"token": raw, "expires_at": expires.isoformat()}


@app.get("/dashboards/{dashboard_id}")
async def get_dashboard(
    dashboard_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    from sqlalchemy import or_
    proj_access = await db.execute(
        select(Project)
        .outerjoin(ProjectMember,
                   (ProjectMember.project_id == Project.id) &
                   (ProjectMember.user_id == current_user.id))
        .where(Project.id == dashboard.project_id)
        .where(or_(Project.owner_id == current_user.id,
                   ProjectMember.user_id == current_user.id))
    )
    if not proj_access.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Access denied")

    widgets_result = await db.execute(
        select(WidgetModel).where(WidgetModel.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets = widgets_result.scalars().all()

    layout_cfg = dashboard.layout_config or {}
    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "description": dashboard.description,
        "theme": dashboard.theme,
        "project_id": str(dashboard.project_id),
        "created_at": dashboard.created_at.isoformat(),
        "filter_config": dashboard.filter_config or [],
        "report_title": layout_cfg.get("report_title"),
        "page_tabs": layout_cfg.get("page_tabs", []),
        "colour_theme": layout_cfg.get("colour_theme"),
        "pages": layout_cfg.get("pages", []),
        "layout_config": layout_cfg,
        "widgets": [
            {
                "id": str(w.id),
                "title": w.title,
                "chart_type": w.chart_type,
                "sql_query": w.sql_query,
                "base_sql": w.base_sql,
                "filterable_columns": w.filterable_columns or [],
                "width": w.width,
                "height": w.height,
                "position_x": w.position_x,
                "position_y": w.position_y,
                "config": w.config,
                "chart_data": w.chart_data,
                "validation_score": w.validation_score,
                "connection_id": str(w.connection_id) if w.connection_id else None,
            }
            for w in widgets
        ],
    }


@app.post("/dashboards/{dashboard_id}/duplicate")
async def duplicate_dashboard(
    dashboard_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Clone a canvas — copies dashboard metadata and all widgets."""
    src_res = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
    src = src_res.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    new_dash = Dashboard(
        name=f"{src.name} (Copy)",
        description=src.description,
        theme=src.theme,
        project_id=src.project_id,
        layout_config=src.layout_config,
        filter_config=src.filter_config,
    )
    db.add(new_dash)
    await db.flush()

    widgets_res = await db.execute(select(WidgetModel).where(WidgetModel.dashboard_id == src.id))
    for w in widgets_res.scalars().all():
        new_w = WidgetModel(
            dashboard_id=new_dash.id,
            title=w.title,
            chart_type=w.chart_type,
            sql_query=w.sql_query,
            base_sql=w.base_sql,
            chart_data=w.chart_data,
            config=w.config,
            width=w.width,
            height=w.height,
            position_x=w.position_x,
            position_y=w.position_y,
            connection_id=w.connection_id,
        )
        db.add(new_w)

    await db.commit()
    return {"id": str(new_dash.id), "name": new_dash.name}


@app.patch("/dashboards/{dashboard_id}")
async def update_dashboard(
    dashboard_id: str,
    patch: DashboardPatch,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    if patch.theme is not None:
        dashboard.theme = patch.theme
    if patch.name is not None:
        dashboard.name = patch.name
    if patch.description is not None:
        dashboard.description = patch.description
    if patch.layout_config is not None:
        existing = dict(dashboard.layout_config or {})
        existing.update(patch.layout_config)
        dashboard.layout_config = existing
    dashboard.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": str(dashboard.id), "name": dashboard.name, "theme": dashboard.theme}


class FilterConfigPatch(_BM):
    filter_config: list = []


@app.patch("/dashboards/{dashboard_id}/filter-config")
async def update_filter_config(
    dashboard_id: str,
    body: FilterConfigPatch,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Replace the filter_config list for a dashboard (used when slicers are edited in the UI)."""
    result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    dashboard.filter_config = body.filter_config
    dashboard.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": dashboard_id, "filter_config": dashboard.filter_config}


@app.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(
    dashboard_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    # Collect widget IDs so we can null out references to them
    widget_rows = await db.execute(
        select(WidgetModel.id).where(WidgetModel.dashboard_id == dashboard.id)
    )
    widget_ids = [r[0] for r in widget_rows.all()]

    # NULL out nullable FK references to this dashboard
    await db.execute(
        sa_update(ChatSession)
        .where(ChatSession.dashboard_id == dashboard.id)
        .values(dashboard_id=None)
        .execution_options(synchronize_session=False)
    )

    # Now safe to delete widgets then the dashboard
    if widget_ids:
        await db.execute(
            sa_delete(WidgetModel)
            .where(WidgetModel.dashboard_id == dashboard.id)
            .execution_options(synchronize_session=False)
        )
    await db.delete(dashboard)
    await db.commit()
    return {"deleted": dashboard_id}


import re as _re


def _inject_filters_into_sql(base_sql: str, filters: dict) -> str:
    """Apply active filters to a base SQL query.

    Supports three value shapes:
    - list of strings/ints  → single-value = or multi-value IN
    - {"start": ..., "end": ...} dict → BETWEEN (date range)

    Date range filters REPLACE any existing condition for the same column in
    base_sql instead of appending — prevents conflicting date conditions when
    the user changes the date range on a dashboard whose SQL was already generated
    with a hard-coded date WHERE clause.
    """
    active = {col: vals for col, vals in filters.items() if vals}
    if not active:
        return base_sql

    modified = base_sql.rstrip(";")
    append_clauses: list[str] = []

    for col, vals in active.items():
        safe_col = _re.sub(r"[^\w.]", "", col)
        if isinstance(vals, dict) and "start" in vals and "end" in vals:
            start = str(vals["start"]).replace("'", "''")
            end   = str(vals["end"]).replace("'", "''")
            new_clause = f"{safe_col} BETWEEN '{start}' AND '{end}'"
            # Try to replace an existing condition for this column (avoids stacking)
            replaced = False
            esc = _re.escape(safe_col)
            for pat in [
                rf"(?:AND\s+)?{esc}\s+BETWEEN\s+'[^']*'\s+AND\s+'[^']*'",
                rf"(?:AND\s+)?{esc}\s*>=\s*'[^']*'(?:\s+AND\s+{esc}\s*<=\s*'[^']*')?",
                rf"(?:AND\s+)?{esc}\s*<=\s*'[^']*'(?:\s+AND\s+{esc}\s*>=\s*'[^']*')?",
            ]:
                new_s, n = _re.subn(pat, new_clause, modified, flags=_re.IGNORECASE)
                if n:
                    modified = new_s
                    replaced = True
                    break
            if not replaced:
                append_clauses.append(new_clause)
        else:
            if not isinstance(vals, list):
                vals = [vals]
            safe_vals = [str(v).replace("'", "''") for v in vals]
            if len(safe_vals) == 1:
                append_clauses.append(f"{safe_col} = '{safe_vals[0]}'")
            else:
                vals_sql = ", ".join(f"'{v}'" for v in safe_vals)
                append_clauses.append(f"{safe_col} IN ({vals_sql})")

    if append_clauses:
        has_where = bool(_re.search(r"\bWHERE\b", modified, _re.IGNORECASE))
        connector = " AND " if has_where else " WHERE "
        modified += connector + " AND ".join(append_clauses)

    return modified


class RequeryRequest(_BM):
    filters: dict = {}


@app.post("/dashboards/{dashboard_id}/requery")
async def requery_dashboard(
    dashboard_id: str,
    body: RequeryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-execute all widget queries with the supplied filter WHERE clauses."""
    from agent_service.utils.http_clients import call_query_executor

    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widgets_result = await db.execute(
        select(WidgetModel).where(WidgetModel.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets = widgets_result.scalars().all()

    async def _exec_widget(w: WidgetModel) -> dict:
        sql = w.base_sql or w.sql_query
        if not sql:
            return {"widget_id": str(w.id), "chart_data": w.chart_data}
        filtered_sql = _inject_filters_into_sql(sql, body.filters)
        conn_id = str(w.connection_id) if w.connection_id else None
        if not conn_id:
            return {"widget_id": str(w.id), "chart_data": w.chart_data}
        try:
            result = await call_query_executor(conn_id, filtered_sql, row_limit=500)
            if result and not result.get("error"):
                rows = result.get("rows", [])
                columns = result.get("columns", [])
                return {
                    "widget_id": str(w.id),
                    "chart_data": {"rows": rows, "columns": columns},
                }
        except Exception as exc:
            print(f"[requery] widget {w.id} failed: {exc}", flush=True)
        return {"widget_id": str(w.id), "chart_data": w.chart_data}

    results = await asyncio.gather(*[_exec_widget(w) for w in widgets], return_exceptions=True)
    widget_data = [r for r in results if isinstance(r, dict)]
    return {"dashboard_id": dashboard_id, "widgets": widget_data}


class DashboardCreate(_BM):
    project_id: str
    name: str
    description: str = ""


class LayoutItem(_BM):
    widget_id: str
    x: int
    y: int
    w: int
    h: int


class LayoutUpdate(_BM):
    items: list[LayoutItem]


class WidgetPatch(_BM):
    title: str | None = None
    chart_type: str | None = None
    config: dict | None = None


class WidgetCreate(_BM):
    title: str
    chart_type: str = "bar"
    sql_query: str | None = None
    chart_data: dict | None = None
    config: dict | None = None
    width: int = 6
    height: int = 4
    position_x: int = 0
    position_y: int = 0
    connection_id: str | None = None


@app.post("/dashboards")
async def create_dashboard(
    body: DashboardCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    new_dash = Dashboard(
        project_id=uuid.UUID(body.project_id),
        name=body.name,
        description=body.description,
        theme="frost",
    )
    db.add(new_dash)
    await db.commit()
    await db.refresh(new_dash)
    return {
        "id": str(new_dash.id),
        "name": new_dash.name,
        "description": new_dash.description,
        "theme": new_dash.theme,
        "project_id": str(new_dash.project_id),
        "created_at": new_dash.created_at.isoformat(),
    }


@app.patch("/dashboards/{dashboard_id}/layout")
async def update_layout(
    dashboard_id: str,
    body: LayoutUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    for item in body.items:
        result = await db.execute(
            select(WidgetModel).where(WidgetModel.id == uuid.UUID(item.widget_id))
        )
        widget = result.scalar_one_or_none()
        if widget:
            widget.position_x = item.x
            widget.position_y = item.y
            widget.width = item.w
            widget.height = item.h
            widget.updated_at = datetime.utcnow()
    await db.commit()
    return {"updated": len(body.items)}


@app.patch("/widgets/{widget_id}")
async def update_widget(
    widget_id: str,
    body: WidgetPatch,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WidgetModel).where(WidgetModel.id == uuid.UUID(widget_id))
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    if body.title is not None:
        widget.title = body.title
    if body.chart_type is not None:
        widget.chart_type = body.chart_type
    if body.config is not None:
        widget.config = {**(widget.config or {}), **body.config}
    widget.updated_at = datetime.utcnow()
    await db.commit()
    return {
        "id": widget_id,
        "title": widget.title,
        "chart_type": widget.chart_type,
        "config": widget.config,
    }


@app.delete("/widgets/{widget_id}")
async def delete_widget(
    widget_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(WidgetModel).where(WidgetModel.id == uuid.UUID(widget_id))
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    await db.delete(widget)
    await db.commit()
    return {"deleted": widget_id}


@app.post("/dashboards/{dashboard_id}/widgets")
async def add_widget(
    dashboard_id: str,
    body: WidgetCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    new_widget = WidgetModel(
        dashboard_id=uuid.UUID(dashboard_id),
        title=body.title,
        widget_type="chart",
        chart_type=body.chart_type,
        sql_query=body.sql_query,
        chart_data=body.chart_data,
        config=body.config,
        width=body.width,
        height=body.height,
        position_x=body.position_x,
        position_y=body.position_y,
        connection_id=uuid.UUID(body.connection_id) if body.connection_id else None,
    )
    db.add(new_widget)
    await db.commit()
    await db.refresh(new_widget)
    return {
        "id": str(new_widget.id),
        "title": new_widget.title,
        "chart_type": new_widget.chart_type,
        "width": new_widget.width,
        "height": new_widget.height,
        "position_x": new_widget.position_x,
        "position_y": new_widget.position_y,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
