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
    layout_cfg = canvas_doc.get("layout_config") or {"pages": canvas_doc.get("pages", [])}

    # Embed schema context so the AI chat warm-starts without a DB round-trip.
    # _get_schema_context in analyst.py will look for layout_config["embedded_schema"].
    if schema_enriched:
        layout_cfg["embedded_schema"] = schema_enriched
    if ai_context_doc:
        layout_cfg["ai_context"] = ai_context_doc
    if meta_doc.get("connection_hint"):
        layout_cfg["connection_hint"] = meta_doc["connection_hint"]

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

    # ── Create Widgets with cached chart_data ───────────────────────────────────
    widget_defs: list[dict] = canvas_doc.get("widgets", [])
    for w_def in widget_defs:
        old_id = w_def.get("id", "")
        data_file = w_def.get("data_file", f"data/{old_id}.json")
        chart_data: dict = {"rows": [], "columns": []}
        if data_file in names:
            try:
                payload = json.loads(zf.read(data_file))
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
            sql_query=q_info.get("sql") or None,
            base_sql=q_info.get("base_sql") or None,
            connection_id=None,
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

    # ── Issue a 7-day analyst share token ───────────────────────────────────────
    raw_tok = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_tok.encode()).hexdigest()
    expires = datetime.utcnow() + timedelta(days=7)
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
        "dashboard_id": str(new_dash.id),
        "name": new_dash.name,
        "widget_count": len(widget_defs),
        "token": raw_tok,
        "connection_hint": meta_doc.get("connection_hint", {}),
        "expires_at": expires.isoformat(),
    }
