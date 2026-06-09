import uuid
import os
from datetime import datetime
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.users import User
from shared.models.projects import Project
from shared.models.project_members import ProjectMember, MemberRole
from shared.models.database_connections import DatabaseConnection, DbType
from shared.models.schema_snapshots import SchemaSnapshot
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

    import time
    start = time.monotonic()
    try:
        if conn.db_type.value in ("postgresql", "redshift"):
            import asyncpg
            pg_conn = await asyncpg.connect(
                host=conn.host, port=conn.port or 5432,
                database=conn.database_name, user=conn.username,
                password=password,
                command_timeout=10,
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
