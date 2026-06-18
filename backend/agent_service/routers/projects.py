import uuid
import os
from datetime import datetime
import httpx
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.users import User
from shared.models.projects import Project
from shared.models.project_members import ProjectMember, MemberRole
from shared.models.database_connections import DatabaseConnection, DbType
from shared.models.schema_snapshots import SchemaSnapshot
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
from shared.encryption import encrypt, decrypt
from shared.schemas.projects import (
    ProjectCreate, ProjectResponse,
    ConnectionCreate, ConnectionResponse, ConnectionTestResult,
)
from shared.security import decode_token
import uuid as _uuid

async def get_current_user(
    credentials=None,
    db=None,
) -> "User":  # type: ignore
    raise NotImplementedError("Use main.py get_current_user instead")

router = APIRouter(prefix="/projects", tags=["projects"])

SCHEMA_CRAWLER_URL = os.getenv("SCHEMA_CRAWLER_URL", "http://localhost:8003")


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    req: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(
        id=uuid.uuid4(),
        name=req.name,
        description=req.description,
        owner_id=current_user.id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(project)

    member = ProjectMember(
        id=uuid.uuid4(),
        project_id=project.id,
        user_id=current_user.id,
        role=MemberRole.owner,
        joined_at=datetime.utcnow(),
    )
    db.add(member)
    await db.commit()
    await db.refresh(project)

    return ProjectResponse(
        id=str(project.id),
        name=project.name,
        description=project.description,
        owner_id=str(project.owner_id),
        created_at=project.created_at.isoformat(),
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project).where(Project.owner_id == current_user.id).order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    return [
        ProjectResponse(
            id=str(p.id), name=p.name, description=p.description,
            owner_id=str(p.owner_id), created_at=p.created_at.isoformat(),
        )
        for p in projects
    ]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(
        id=str(project.id), name=project.name, description=project.description,
        owner_id=str(project.owner_id), created_at=project.created_at.isoformat(),
    )


@router.post("/{project_id}/connections", response_model=ConnectionResponse, status_code=201)
async def add_connection(
    project_id: str,
    req: ConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.id == uuid.UUID(project_id)))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    encrypted_pw = encrypt(req.password) if req.password else None

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        name=req.name,
        db_type=DbType(req.db_type),
        host=req.host,
        port=req.port,
        database_name=req.database_name,
        username=req.username,
        encrypted_password=encrypted_pw,
        ssl_enabled=req.ssl_enabled,
        connection_options=req.connection_options,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    return ConnectionResponse(
        id=str(conn.id), project_id=str(conn.project_id), name=conn.name,
        db_type=conn.db_type.value, host=conn.host, port=conn.port,
        database_name=conn.database_name, username=conn.username,
        ssl_enabled=conn.ssl_enabled, is_active=conn.is_active,
        last_tested_at=conn.last_tested_at.isoformat() if conn.last_tested_at else None,
        created_at=conn.created_at.isoformat(),
    )


@router.get("/{project_id}/connections", response_model=list[ConnectionResponse])
async def list_connections(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.project_id == uuid.UUID(project_id),
        ).order_by(DatabaseConnection.created_at.asc())
    )
    conns = result.scalars().all()
    return [
        ConnectionResponse(
            id=str(c.id), project_id=str(c.project_id), name=c.name,
            db_type=c.db_type.value, host=c.host, port=c.port,
            database_name=c.database_name, username=c.username,
            ssl_enabled=c.ssl_enabled, is_active=c.is_active,
            last_tested_at=c.last_tested_at.isoformat() if c.last_tested_at else None,
            created_at=c.created_at.isoformat(),
        )
        for c in conns
    ]


@router.patch("/{project_id}/connections/{conn_id}", response_model=ConnectionResponse)
async def update_connection(
    project_id: str,
    conn_id: str,
    req: ConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing database connection (e.g. to fix a username or password typo)."""
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == uuid.UUID(conn_id),
            DatabaseConnection.project_id == uuid.UUID(project_id),
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    conn.name = req.name
    conn.db_type = DbType(req.db_type)
    conn.host = req.host
    conn.port = req.port
    conn.database_name = req.database_name
    conn.username = req.username
    if req.password is not None:
        # empty string = clear password (switch to IAM auth); non-empty = update password
        conn.encrypted_password = encrypt(req.password) if req.password else None
    conn.ssl_enabled = req.ssl_enabled
    conn.connection_options = req.connection_options
    conn.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(conn)
    return ConnectionResponse(
        id=str(conn.id), project_id=str(conn.project_id), name=conn.name,
        db_type=conn.db_type.value, host=conn.host, port=conn.port,
        database_name=conn.database_name, username=conn.username,
        ssl_enabled=conn.ssl_enabled, is_active=conn.is_active,
        last_tested_at=conn.last_tested_at.isoformat() if conn.last_tested_at else None,
        created_at=conn.created_at.isoformat(),
    )


@router.post("/{project_id}/connections/{conn_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    project_id: str,
    conn_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == uuid.UUID(conn_id),
            DatabaseConnection.project_id == uuid.UUID(project_id),
        )
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")

    password = ""
    if conn.encrypted_password:
        password = decrypt(conn.encrypted_password)

    import time, asyncio as _asyncio
    start = time.monotonic()
    try:
        if conn.db_type.value == "redshift":
            import redshift_connector as _rsc, os as _os
            _host = conn.host or ""
            _is_serverless = "redshift-serverless" in _host
            # Serverless workgroups auto-pause; allow 90s for them to wake up
            _timeout = 90 if _is_serverless else 20
            conn_kwargs: dict = {
                "host": _host, "port": conn.port or 5439,
                "database": conn.database_name or "", "ssl": True, "timeout": _timeout,
            }
            if _is_serverless:
                conn_kwargs["is_serverless"] = True
                # Host format: <workgroup>.<account>.<region>.redshift-serverless.amazonaws.com
                _parts = _host.split(".")
                if len(_parts) >= 3:
                    conn_kwargs["region"] = _parts[2]
                if _parts:
                    conn_kwargs["serverless_work_group"] = _parts[0]
            if not password:
                try:
                    from dotenv import load_dotenv as _ld
                    _env = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', '..', '..', '.env')
                    if _os.path.exists(_env):
                        _ld(_env, override=True)
                except ImportError:
                    pass
                _ak = _os.getenv("AWS_ACCESS_KEY_ID", "")
                _sk = _os.getenv("AWS_SECRET_ACCESS_KEY", "")
                _tok = _os.getenv("AWS_SESSION_TOKEN", "")
                _region = conn_kwargs.get("region", "us-east-1")
                # Pre-validate credentials — expired STS tokens cause a confusing fallback error
                try:
                    import boto3 as _boto3
                    _sts = _boto3.client("sts", aws_access_key_id=_ak, aws_secret_access_key=_sk,
                                         aws_session_token=_tok or None, region_name=_region)
                    _sts.get_caller_identity()
                except Exception as _ce:
                    raise Exception(
                        f"AWS credentials invalid or expired: {_ce}. "
                        "Run: aws sts get-session-token  then update .env and restart."
                    )
                conn_kwargs["iam"] = True
                conn_kwargs["aws_access_key_id"] = _ak
                conn_kwargs["aws_secret_access_key"] = _sk
                if _tok:
                    conn_kwargs["aws_session_token"] = _tok
                conn_kwargs["database_user"] = conn.username or "awsuser"
            else:
                conn_kwargs["user"] = conn.username or ""
                conn_kwargs["password"] = password
            def _sync_test():
                rc = _rsc.connect(**conn_kwargs)
                rc.close()
            await _asyncio.get_event_loop().run_in_executor(None, _sync_test)
        elif conn.db_type.value == "postgresql":
            import asyncpg
            pg_conn = await asyncpg.connect(
                host=conn.host, port=conn.port or 5432,
                database=conn.database_name, user=conn.username,
                password=password, command_timeout=10,
            )
            await pg_conn.close()
        elif conn.db_type.value == "mysql":
            import aiomysql
            my_conn = await aiomysql.connect(
                host=conn.host, port=conn.port or 3306,
                db=conn.database_name, user=conn.username,
                password=password, connect_timeout=10,
            )
            my_conn.close()
        else:
            return ConnectionTestResult(success=False, message=f"Cannot test {conn.db_type.value} connections yet")

        latency = (time.monotonic() - start) * 1000
        conn.last_tested_at = datetime.utcnow()
        await db.commit()
        return ConnectionTestResult(success=True, message="Connection successful", latency_ms=round(latency, 1))

    except Exception as e:
        return ConnectionTestResult(success=False, message=str(e))


@router.post("/{project_id}/schema/crawl")
async def trigger_schema_crawl(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid project ID: {project_id!r}")
    result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.project_id == uuid.UUID(project_id),
            DatabaseConnection.is_active == True,
        ).limit(1)
    )
    conn = result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found for this project")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SCHEMA_CRAWLER_URL}/crawl",
                json={"connection_id": str(conn.id), "project_id": project_id},
            )
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Schema crawler unavailable: {e}")


@router.get("/{project_id}/schema")
async def get_schema(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid project ID: {project_id!r}")
    conn_result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.project_id == uuid.UUID(project_id),
            DatabaseConnection.is_active == True,
        ).limit(1)
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found")

    snap_result = await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == conn.id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="No schema snapshot found. Please crawl first.")

    return {
        "connection_id": str(conn.id),
        "snapshot_id": str(snapshot.id),
        "version": snapshot.version,
        "created_at": snapshot.created_at.isoformat(),
        "schema": snapshot.schema_document,
    }


async def _resolve_connection_id_for_tables(
    project_id: str,
    connection_id: Optional[str],
    dashboard_id: Optional[str],
    db: AsyncSession,
) -> Optional[uuid.UUID]:
    """Resolve which connection's schema to list, mirroring the chat's resolution:
    explicit connection_id → project's active connection → the dashboard's bound
    connection (layout_config.connection_id, then any widget). Imported canvases
    often have no project-level connection, only a dashboard/widget binding —
    hence the dashboard fallback (without it this 404s for imported reports)."""
    # 1) explicit connection_id (the canvas panel already knows it from its widgets)
    if connection_id:
        try:
            return uuid.UUID(connection_id)
        except ValueError:
            pass
    # 2) project's active connection
    try:
        proj_uuid = uuid.UUID(project_id)
    except ValueError:
        return None
    conn = (await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.project_id == proj_uuid,
            DatabaseConnection.is_active == True,
        ).limit(1)
    )).scalar_one_or_none()
    if conn:
        return conn.id
    # 3) dashboard fallback (imported canvases bind the connection here)
    if dashboard_id:
        try:
            dash_uuid = uuid.UUID(dashboard_id)
        except ValueError:
            return None
        dash = (await db.execute(
            select(Dashboard).where(Dashboard.id == dash_uuid)
        )).scalar_one_or_none()
        if dash:
            lc_conn = (dash.layout_config or {}).get("connection_id")
            if lc_conn:
                try:
                    return uuid.UUID(str(lc_conn))
                except ValueError:
                    pass
        wid_conn = (await db.execute(
            select(Widget.connection_id)
            .where(Widget.dashboard_id == dash_uuid)
            .where(Widget.connection_id.isnot(None))
            .limit(1)
        )).scalar_one_or_none()
        if wid_conn:
            return wid_conn
    return None


@router.get("/{project_id}/schema/tables")
async def get_schema_tables(
    project_id: str,
    connection_id: Optional[str] = Query(None),
    dashboard_id: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Lightweight table-name list for pickers (e.g. the Canvas Assistant's
    'Selected tables' mode). Reads the latest cached snapshot but returns ONLY
    table names + column counts — a few KB, versus the multi-MB full
    schema_document returned by GET /schema. No crawl, no live DB hit.

    Resolves the connection via connection_id → project active → dashboard binding,
    so it works for imported canvases that have no project-level connection."""
    conn_id = await _resolve_connection_id_for_tables(project_id, connection_id, dashboard_id, db)
    print(
        f"[schema-tables] project={project_id[:8]} conn_param={'y' if connection_id else 'n'} "
        f"dash_param={'y' if dashboard_id else 'n'} → resolved_conn={str(conn_id)[:8] if conn_id else None}",
        flush=True,
    )
    if not conn_id:
        raise HTTPException(status_code=404, detail="No connection found for this project/canvas")

    snap_result = await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == conn_id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )
    snapshot = snap_result.scalar_one_or_none()

    out: list[dict] = []
    version = 0
    if snapshot and snapshot.schema_document:
        version = snapshot.version
        raw = snapshot.schema_document or {}
        raw_tables = raw.get("tables", []) if isinstance(raw, dict) else []
        if isinstance(raw_tables, dict):
            for name, t in raw_tables.items():
                out.append({"name": name, "columns": len((t or {}).get("columns") or [])})
        else:
            for t in raw_tables:
                if isinstance(t, dict) and t.get("name"):
                    name = f"{t['schema']}.{t['name']}" if t.get("schema") else t["name"]
                    out.append({"name": name, "columns": len(t.get("columns") or [])})
        print(f"[schema-tables] from snapshot v{version}  tables={len(out)}", flush=True)
    else:
        # No durable snapshot (common for imported .vly canvases) → read the warm
        # enriched cache the copilot already uses.
        from agent_service.agents import schema_cache as _sc
        cached = await _sc.get_cached_table_names(str(conn_id))
        if cached:
            out = cached
            print(f"[schema-tables] from enriched cache  tables={len(out)}", flush=True)
        else:
            print(f"[schema-tables] no snapshot AND no enriched cache for conn={str(conn_id)[:8]}", flush=True)
            raise HTTPException(
                status_code=404,
                detail="No schema available for this connection yet. Crawl the schema first.",
            )

    out.sort(key=lambda x: x["name"].lower())
    return {"tables": out, "total": len(out), "version": version}


@router.get("/{project_id}/schema/metadata")
async def get_schema_metadata(
    project_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns AI-extracted metadata for this project's schema.
    Written by the background metadata_extractor after every crawl.
    Returns 404 when no metadata has been extracted yet.
    """
    conn_result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.project_id == uuid.UUID(project_id),
            DatabaseConnection.is_active == True,
        ).limit(1)
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="No active connection found")

    tbl_rows = (await db.execute(
        select(SchemaTableMetadata)
        .where(SchemaTableMetadata.connection_id == conn.id)
        .order_by(SchemaTableMetadata.table_name)
    )).scalars().all()

    if not tbl_rows:
        raise HTTPException(
            status_code=404,
            detail="Metadata not extracted yet — trigger a crawl and wait ~1 minute for background extraction to finish.",
        )

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
            "generation_method": t.generation_method,
            "generated_at": t.generated_at.isoformat() if t.generated_at else None,
            "columns": cols_by_table.get(t.table_name, []),
        }
        for t in tbl_rows
    ]

    return {
        "connection_id": str(conn.id),
        "total_tables": len(tables),
        "tables": tables,
    }
