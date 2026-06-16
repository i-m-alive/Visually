"""
End-user specific endpoints — actions available to analyst/end_user role accounts.
"""
import hashlib
import io
import json
import secrets
import uuid
import zipfile
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models.users import User
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.models.sharing import CanvasShareToken
from shared.security import decode_token

router = APIRouter(tags=["end-user"])

bearer_scheme = HTTPBearer(auto_error=False)

import os as _os
DEV_MODE    = _os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = _os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")


async def _get_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if DEV_MODE and credentials is None:
        from shared.security import hash_password as _hp
        dev_id = uuid.UUID(DEV_USER_ID)
        result = await db.execute(select(User).where(User.id == dev_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(id=dev_id, email="dev@visually.local",
                        hashed_password=_hp("dev-password"), full_name="Dev User",
                        is_active=True, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
            db.add(user)
            await db.commit()
        return user
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def _get_or_create_personal_project(current_user: User, db: AsyncSession):
    """The analyst UI has no 'projects' — every imported report and any DB connection
    lives in this single auto-created personal project, invisibly."""
    from shared.models.projects import Project
    from shared.models.project_members import ProjectMember, MemberRole

    proj_result = await db.execute(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .where(Project.name == "Imported Reports")
    )
    personal_project = proj_result.scalar_one_or_none()
    if personal_project:
        return personal_project

    personal_project = Project(
        id=uuid.uuid4(),
        owner_id=current_user.id,
        name="Imported Reports",
        description="Auto-created for .vly reports you import",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(personal_project)
    await db.flush()
    db.add(ProjectMember(
        id=uuid.uuid4(),
        project_id=personal_project.id,
        user_id=current_user.id,
        role=MemberRole.owner,
        joined_at=datetime.utcnow(),
    ))
    await db.flush()
    return personal_project


# ── POST /end-user/connections ────────────────────────────────────────────────

class EndUserConnectionRequest(BaseModel):
    db_type: str
    host: str
    database_name: str
    username: str
    password: Optional[str] = None
    port: Optional[int] = None
    name: Optional[str] = None
    ssl_enabled: bool = False
    iam_role_arn: Optional[str] = None   # Redshift IAM auth (optional)


@router.post("/end-user/connections", status_code=201)
async def end_user_create_connection(
    body: EndUserConnectionRequest,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """Create + test a database connection in the analyst's personal project.

    No project picker needed — the project is implicit. The password is Fernet-encrypted
    (never stored raw). The connection is verified with a trivial query before we keep it,
    so a canvas is only ever bound to a connection that actually works.
    """
    from shared.models.database_connections import DatabaseConnection, DbType
    from shared.encryption import encrypt
    from agent_service.utils.http_clients import call_query_executor

    try:
        db_type_enum = DbType(body.db_type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unsupported database type: {body.db_type}")

    personal_project = await _get_or_create_personal_project(current_user, db)

    # Redshift IAM auth (when supplied) is carried in connection_options, where the
    # query-executor router looks for it.
    connection_options = {"iam_role_arn": body.iam_role_arn} if body.iam_role_arn else None

    conn = DatabaseConnection(
        id=uuid.uuid4(),
        project_id=personal_project.id,
        name=body.name or f"{body.database_name} @ {body.host}",
        db_type=db_type_enum,
        host=body.host,
        port=body.port,
        database_name=body.database_name,
        username=body.username,
        encrypted_password=encrypt(body.password) if body.password else None,
        ssl_enabled=body.ssl_enabled,
        connection_options=connection_options,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(conn)
    await db.commit()
    await db.refresh(conn)

    # Verify it actually connects — a dead connection is worse than none.
    # Redshift Serverless auto-pauses and can take 60–120s to wake on the first
    # connect, so give the verify query a generous timeout for it; fast engines
    # (postgres/mysql) return immediately and are unaffected.
    _verify_timeout = 130 if body.db_type == "redshift" else 30
    test = await call_query_executor(
        str(conn.id), "SELECT 1", row_limit=1, timeout_seconds=_verify_timeout,
    )
    if test.get("error"):
        await db.delete(conn)
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Could not connect to the database: {test['error']}")

    print(f"[end-user] created connection {str(conn.id)[:8]} for user {str(current_user.id)[:8]}", flush=True)
    return {
        "connection_id": str(conn.id),
        "project_id": str(personal_project.id),
        "name": conn.name,
        "ok": True,
    }


# ── POST /end-user/import-vly ─────────────────────────────────────────────────

@router.post("/end-user/import-vly", status_code=201)
async def end_user_import_vly(
    file: UploadFile = File(...),
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Import a .vly file into the current user's personal workspace (no project needed).
    Returns a 7-day analyst share token so the frontend can navigate directly to
    /share/canvas/{token} and open the full analyst workspace immediately.
    """
    from shared.models.projects import Project
    from shared.models.project_members import ProjectMember, MemberRole
    from shared.models.database_connections import DatabaseConnection

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw), "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid .vly file (not a ZIP archive)")

    names = zf.namelist()
    if "canvas.json" not in names or "meta.json" not in names:
        raise HTTPException(status_code=400, detail="Invalid .vly file (missing canvas.json or meta.json)")

    try:
        canvas_doc: dict   = json.loads(zf.read("canvas.json"))
        meta_doc: dict     = json.loads(zf.read("meta.json"))
        queries_doc: dict  = json.loads(zf.read("queries.json"))  if "queries.json"  in names else {}
        schema_enriched    = json.loads(zf.read("schema_enriched.json")) if "schema_enriched.json" in names else {}
        ai_context_doc     = json.loads(zf.read("ai_context.json"))      if "ai_context.json"      in names else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed JSON in .vly: {exc}")

    # ── Find or create personal "Imported Reports" project ─────────────────────
    proj_result = await db.execute(
        select(Project)
        .where(Project.owner_id == current_user.id)
        .where(Project.name == "Imported Reports")
    )
    personal_project = proj_result.scalar_one_or_none()

    if not personal_project:
        personal_project = Project(
            id=uuid.uuid4(),
            owner_id=current_user.id,
            name="Imported Reports",
            description="Auto-created for .vly reports you import",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(personal_project)
        await db.flush()
        db.add(ProjectMember(
            id=uuid.uuid4(),
            project_id=personal_project.id,
            user_id=current_user.id,
            role=MemberRole.owner,
            joined_at=datetime.utcnow(),
        ))
        await db.flush()

    # ── Create Dashboard ────────────────────────────────────────────────────────
    canvas_name = canvas_doc.get("name") or meta_doc.get("canvas_name") or "Imported Report"
    layout_cfg  = canvas_doc.get("layout_config") or {"pages": canvas_doc.get("pages", [])}
    now_iso     = datetime.utcnow().isoformat()

    # Embed schema + AI context for warm-start, plus import provenance
    if schema_enriched:
        layout_cfg["embedded_schema"] = schema_enriched
    if ai_context_doc:
        layout_cfg["ai_context"] = ai_context_doc
    # Try to find a matching live connection in any of the user's projects.
    # This lets the copilot run live SQL on imported canvases without Phase-1 encryption.
    matched_conn_id: uuid.UUID | None = None
    hint = meta_doc.get("connection_hint") or {}
    if hint:
        layout_cfg["connection_hint"] = hint
        hint_host = hint.get("host", "")
        hint_db   = hint.get("database", "")
        if hint_host and hint_db:
            mc_result = await db.execute(
                select(DatabaseConnection)
                .join(Project, Project.id == DatabaseConnection.project_id)
                .where(Project.owner_id == current_user.id)
                .where(DatabaseConnection.host == hint_host)
                .where(DatabaseConnection.database_name == hint_db)
                .where(DatabaseConnection.is_active == True)
                .limit(1)
            )
            mc = mc_result.scalar_one_or_none()
            if mc:
                matched_conn_id = mc.id
                layout_cfg["connection_id"] = str(mc.id)

    # Provenance flags — used by shared-with-me and the dashboard card UI
    layout_cfg["is_imported"]     = True
    layout_cfg["imported_at"]     = now_iso
    layout_cfg["imported_by"]     = current_user.email
    layout_cfg["original_name"]   = canvas_name
    layout_cfg["export_source"]   = {
        "exported_at":       meta_doc.get("exported_at", ""),
        "exported_by_email": meta_doc.get("exported_by_email", ""),
        "vly_version":       meta_doc.get("vly_version", ""),
    }
    # Flag whether intelligence analysis was bundled in the archive
    intel_doc: dict = {}
    try:
        if "intelligence.json" in names:
            intel_doc = json.loads(zf.read("intelligence.json"))
    except Exception:
        pass
    layout_cfg["has_intelligence"] = bool(intel_doc.get("analysis"))

    new_dash = Dashboard(
        id=uuid.uuid4(),
        project_id=personal_project.id,
        name=canvas_name,
        description=canvas_doc.get("description") or "",
        theme=canvas_doc.get("theme", "frost"),
        layout_config=layout_cfg,
        filter_config=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(new_dash)
    await db.flush()

    # ── Create permanent CanvasCollaborator row ─────────────────────────────────
    # This is what makes the canvas appear in GET /dashboards/shared-with-me
    # permanently — without this the canvas was invisible after the share token expired.
    from shared.models.sharing import CanvasCollaborator as _CC
    db.add(_CC(
        id=uuid.uuid4(),
        dashboard_id=new_dash.id,
        user_id=current_user.id,
        invited_by=current_user.id,
        role="editor",
        created_at=datetime.utcnow(),
    ))

    # ── Create Widgets with cached chart_data ───────────────────────────────────
    widget_defs: list[dict] = canvas_doc.get("widgets", [])
    for w_def in widget_defs:
        old_id    = w_def.get("id", "")
        data_file = w_def.get("data_file", f"data/{old_id}.json")
        chart_data: dict = {"rows": [], "columns": []}
        if data_file in names:
            try:
                payload    = json.loads(zf.read(data_file))
                chart_data = payload.get("chart_data", chart_data)
            except Exception:
                pass

        q_info = queries_doc.get(old_id, {})
        db.add(Widget(
            id=uuid.uuid4(),
            dashboard_id=new_dash.id,
            title=w_def.get("title", "Chart"),
            widget_type=w_def.get("widget_type", "chart"),
            chart_type=w_def.get("chart_type", "bar"),
            sql_query=q_info.get("sql")     or None,
            base_sql=q_info.get("base_sql") or None,
            connection_id=matched_conn_id,   # linked when host+db matched an existing connection
            position_x=w_def.get("position_x", 0),
            position_y=w_def.get("position_y", 0),
            width=w_def.get("width", 6),
            height=w_def.get("height", 6),
            config=w_def.get("config") or {},
            chart_data=chart_data,
            filterable_columns=w_def.get("filterable_columns") or [],
            validation_score=w_def.get("validation_score"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))

    await db.commit()
    await db.refresh(new_dash)
    zf.close()

    # ── Issue a long-lived analyst share token (90 days) ───────────────────────
    # The canvas now lives permanently in "My Reports" via CanvasCollaborator,
    # but we also issue a token for the immediate /share/canvas/{token} redirect.
    raw_tok    = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_tok.encode()).hexdigest()
    expires    = datetime.utcnow() + timedelta(days=90)
    db.add(CanvasShareToken(
        id=uuid.uuid4(),
        dashboard_id=new_dash.id,
        token_hash=token_hash,
        mode="live",
        label="vly-import",
        expires_at=expires,
        is_revoked=False,
        created_by=current_user.id,
    ))
    await db.commit()

    return {
        "dashboard_id":      str(new_dash.id),
        "name":              new_dash.name,
        "widget_count":      len(widget_defs),
        "token":             raw_tok,
        "connection_hint":   meta_doc.get("connection_hint", {}),
        "has_intelligence":  layout_cfg["has_intelligence"],
        "expires_at":        expires.isoformat(),
        "saved_permanently": True,   # canvas is now in My Reports via CanvasCollaborator
    }


@router.delete("/end-user/reports/{dashboard_id}")
async def delete_my_report(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a report from the analyst's dashboard, with correct semantics:

    - Imported canvas (the analyst OWNS its personal project) → fully delete the
      dashboard + widgets (DB cascades collaborators / share tokens / annotations).
    - Shared-with-me report (owned by a builder) → only remove the analyst's
      CanvasCollaborator link, leaving the builder's dashboard intact.

    Never lets an analyst delete a builder's shared dashboard.
    """
    from shared.models.projects import Project
    from shared.models.sharing import CanvasCollaborator
    from shared.models.chat_sessions import ChatSession
    from sqlalchemy import delete as sa_delete, update as sa_update

    try:
        did = uuid.UUID(dashboard_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid dashboard id")

    dash = (await db.execute(select(Dashboard).where(Dashboard.id == did))).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Report not found")

    proj = (await db.execute(select(Project).where(Project.id == dash.project_id))).scalar_one_or_none()
    is_owner = bool(proj and proj.owner_id == current_user.id)

    if is_owner:
        # Owned (imported) → full delete. Null nullable FKs, drop widgets, then the
        # dashboard (collaborators/tokens/annotations/bookmarks cascade in the DB).
        await db.execute(
            sa_update(ChatSession).where(ChatSession.dashboard_id == did)
            .values(dashboard_id=None).execution_options(synchronize_session=False)
        )
        await db.execute(
            sa_delete(Widget).where(Widget.dashboard_id == did)
            .execution_options(synchronize_session=False)
        )
        await db.delete(dash)
        await db.commit()
        return {"deleted": True, "mode": "deleted", "dashboard_id": dashboard_id}

    # Shared with me → remove only my collaborator link.
    collab = (await db.execute(
        select(CanvasCollaborator).where(
            CanvasCollaborator.dashboard_id == did,
            CanvasCollaborator.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if collab:
        await db.delete(collab)
        await db.commit()
        return {"deleted": True, "mode": "removed_from_list", "dashboard_id": dashboard_id}

    raise HTTPException(status_code=403, detail="You don't have permission to delete this report")
