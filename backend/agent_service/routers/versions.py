"""Dashboard version history: snapshots, restore, and prose diffs.

Wires up the previously-dead DashboardVersion table (shared/models/phase2.py).
Snapshots capture name/theme/layout_config/widgets; restore reconciles widgets
back to the snapshot (snapshotting current state first, so restore is undoable).
"""
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget as WidgetModel
from shared.models.phase2 import DashboardVersion
from shared.models.users import User
from shared.security import decode_token

router = APIRouter(tags=["versions"])

bearer_scheme = HTTPBearer(auto_error=False)
DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")

_MAX_VERSIONS = 50
_DEBOUNCE_MINUTES = 10


async def _get_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if DEV_MODE and credentials is None:
        result = await db.execute(select(User).where(User.id == uuid.UUID(DEV_USER_ID)))
        user = result.scalar_one_or_none()
        if user:
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


async def _build_snapshot(dashboard_id: uuid.UUID, db: AsyncSession) -> dict:
    dr = await db.execute(select(Dashboard).where(Dashboard.id == dashboard_id))
    dash = dr.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    wr = await db.execute(select(WidgetModel).where(WidgetModel.dashboard_id == dashboard_id))
    widgets = wr.scalars().all()
    return {
        "name": dash.name,
        "theme": dash.theme,
        "layout_config": dash.layout_config or {},
        "widgets": [
            {
                "id": str(w.id),
                "title": w.title,
                "chart_type": w.chart_type,
                "widget_type": w.widget_type,
                "sql_query": w.sql_query,
                "connection_id": str(w.connection_id) if w.connection_id else None,
                "position_x": w.position_x, "position_y": w.position_y,
                "width": w.width, "height": w.height,
                "config": w.config or {},
                "chart_data": w.chart_data,
            }
            for w in widgets
        ],
    }


def _snapshot_hash(snap: dict) -> str:
    # Exclude chart_data from the hash — data refreshes shouldn't create versions
    slim = {**snap, "widgets": [{k: v for k, v in w.items() if k != "chart_data"}
                                for w in snap["widgets"]]}
    return hashlib.sha256(json.dumps(slim, sort_keys=True, default=str).encode()).hexdigest()


async def create_snapshot(dashboard_id: uuid.UUID, db: AsyncSession,
                          user_id: Optional[uuid.UUID] = None,
                          summary: Optional[str] = None,
                          force: bool = False) -> Optional[DashboardVersion]:
    """Snapshot with debounce: skip if the latest version is recent AND identical."""
    snap = await _build_snapshot(dashboard_id, db)
    new_hash = _snapshot_hash(snap)
    latest_r = await db.execute(
        select(DashboardVersion).where(DashboardVersion.dashboard_id == dashboard_id)
        .order_by(DashboardVersion.version_number.desc()).limit(1)
    )
    latest = latest_r.scalar_one_or_none()
    if latest and not force:
        recent = datetime.utcnow() - latest.created_at < timedelta(minutes=_DEBOUNCE_MINUTES)
        if recent and (latest.snapshot or {}).get("_hash") == new_hash:
            return latest
    snap["_hash"] = new_hash
    version = DashboardVersion(
        dashboard_id=dashboard_id,
        version_number=(latest.version_number + 1) if latest else 1,
        snapshot=snap,
        change_summary=summary,
        created_by=user_id,
    )
    db.add(version)
    # Evict beyond the cap
    old_r = await db.execute(
        select(DashboardVersion.id).where(DashboardVersion.dashboard_id == dashboard_id)
        .order_by(DashboardVersion.version_number.desc()).offset(_MAX_VERSIONS - 1)
    )
    old_ids = [row[0] for row in old_r.all()]
    if old_ids:
        await db.execute(sa_delete(DashboardVersion).where(DashboardVersion.id.in_(old_ids)))
    await db.commit()
    await db.refresh(version)
    return version


def _version_meta(v: DashboardVersion) -> dict:
    snap = v.snapshot or {}
    return {
        "id": str(v.id),
        "version_number": v.version_number,
        "created_at": v.created_at.isoformat() if v.created_at else None,
        "change_summary": v.change_summary,
        "widget_count": len(snap.get("widgets", [])),
        "name": snap.get("name"),
        "theme": snap.get("theme"),
    }


@router.post("/dashboards/{dashboard_id}/versions/snapshot")
async def snapshot_dashboard(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    v = await create_snapshot(uuid.UUID(dashboard_id), db, current_user.id, summary="Manual snapshot")
    return _version_meta(v)


@router.get("/dashboards/{dashboard_id}/versions")
async def list_versions(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(
        select(DashboardVersion).where(DashboardVersion.dashboard_id == uuid.UUID(dashboard_id))
        .order_by(DashboardVersion.version_number.desc())
    )
    return {"versions": [_version_meta(v) for v in r.scalars().all()]}


@router.get("/dashboards/{dashboard_id}/versions/{version_id}")
async def get_version(
    dashboard_id: str, version_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(DashboardVersion).where(DashboardVersion.id == uuid.UUID(version_id)))
    v = r.scalar_one_or_none()
    if not v or str(v.dashboard_id) != dashboard_id:
        raise HTTPException(status_code=404, detail="Version not found")
    return {**_version_meta(v), "snapshot": v.snapshot}


def _diff_snapshots(old: dict, new: dict) -> list:
    """Structural changes between two snapshots (old → new)."""
    changes: list = []
    ow = {w["id"]: w for w in old.get("widgets", [])}
    nw = {w["id"]: w for w in new.get("widgets", [])}
    for wid, w in nw.items():
        if wid not in ow:
            changes.append(f"added widget '{w.get('title')}' ({w.get('chart_type')})")
    for wid, w in ow.items():
        if wid not in nw:
            changes.append(f"removed widget '{w.get('title')}'")
    for wid in set(ow) & set(nw):
        o, n = ow[wid], nw[wid]
        if o.get("title") != n.get("title"):
            changes.append(f"renamed '{o.get('title')}' → '{n.get('title')}'")
        if o.get("chart_type") != n.get("chart_type"):
            changes.append(f"'{n.get('title')}' changed type {o.get('chart_type')} → {n.get('chart_type')}")
        if o.get("sql_query") != n.get("sql_query"):
            changes.append(f"'{n.get('title')}' SQL changed")
        if (o.get("position_x"), o.get("position_y"), o.get("width"), o.get("height")) != \
           (n.get("position_x"), n.get("position_y"), n.get("width"), n.get("height")):
            changes.append(f"'{n.get('title')}' moved/resized")
    if old.get("theme") != new.get("theme"):
        changes.append(f"theme changed {old.get('theme')} → {new.get('theme')}")
    op = [p.get("name") for p in (old.get("layout_config") or {}).get("pages", [])]
    np_ = [p.get("name") for p in (new.get("layout_config") or {}).get("pages", [])]
    if op != np_:
        changes.append(f"pages changed {op} → {np_}")
    return changes


@router.get("/dashboards/{dashboard_id}/versions/{version_id}/diff")
async def diff_version(
    dashboard_id: str, version_id: str,
    against: str = "current",
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    r = await db.execute(select(DashboardVersion).where(DashboardVersion.id == uuid.UUID(version_id)))
    v = r.scalar_one_or_none()
    if not v or str(v.dashboard_id) != dashboard_id:
        raise HTTPException(status_code=404, detail="Version not found")
    if against == "current":
        other = await _build_snapshot(uuid.UUID(dashboard_id), db)
    else:
        r2 = await db.execute(select(DashboardVersion).where(DashboardVersion.id == uuid.UUID(against)))
        v2 = r2.scalar_one_or_none()
        if not v2:
            raise HTTPException(status_code=404, detail="Comparison version not found")
        other = v2.snapshot or {}
    changes = _diff_snapshots(v.snapshot or {}, other)
    prose = "No structural changes." if not changes else "; ".join(changes[:12])
    if changes:
        try:
            from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
            prose = await bedrock_invoke(
                model_id=BEDROCK_HAIKU_MODEL,
                system_prompt="Rewrite this dashboard change list as a 1-3 sentence friendly changelog. No preamble.",
                user_message="; ".join(changes[:20]),
                max_tokens=200, temperature=0.2,
            )
        except Exception:
            pass
    return {"changes": changes, "prose": prose}


@router.post("/dashboards/{dashboard_id}/versions/{version_id}/restore")
async def restore_version(
    dashboard_id: str, version_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    did = uuid.UUID(dashboard_id)
    r = await db.execute(select(DashboardVersion).where(DashboardVersion.id == uuid.UUID(version_id)))
    v = r.scalar_one_or_none()
    if not v or v.dashboard_id != did:
        raise HTTPException(status_code=404, detail="Version not found")
    # Snapshot the CURRENT state first — restore itself must be undoable
    await create_snapshot(did, db, current_user.id, summary="Auto-snapshot before restore", force=True)

    snap = v.snapshot or {}
    dr = await db.execute(select(Dashboard).where(Dashboard.id == did))
    dash = dr.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    dash.name = snap.get("name") or dash.name
    dash.theme = snap.get("theme") or dash.theme
    dash.layout_config = snap.get("layout_config") or dash.layout_config

    wr = await db.execute(select(WidgetModel).where(WidgetModel.dashboard_id == did))
    current = {str(w.id): w for w in wr.scalars().all()}
    snap_widgets = {w["id"]: w for w in snap.get("widgets", [])}

    # Update / recreate widgets from the snapshot
    for wid, sw in snap_widgets.items():
        if wid in current:
            w = current[wid]
            w.title = sw.get("title")
            w.chart_type = sw.get("chart_type")
            w.sql_query = sw.get("sql_query")
            w.position_x, w.position_y = sw.get("position_x"), sw.get("position_y")
            w.width, w.height = sw.get("width"), sw.get("height")
            w.config = sw.get("config") or {}
            if sw.get("chart_data") is not None:
                w.chart_data = sw["chart_data"]
        else:
            db.add(WidgetModel(
                id=uuid.UUID(wid),
                dashboard_id=did,
                title=sw.get("title") or "Restored widget",
                widget_type=sw.get("widget_type") or "chart",
                chart_type=sw.get("chart_type"),
                sql_query=sw.get("sql_query"),
                connection_id=uuid.UUID(sw["connection_id"]) if sw.get("connection_id") else None,
                position_x=sw.get("position_x") or 0,
                position_y=sw.get("position_y") or 0,
                width=sw.get("width") or 6,
                height=sw.get("height") or 6,
                config=sw.get("config") or {},
                chart_data=sw.get("chart_data"),
            ))
    # Delete widgets that don't exist in the snapshot
    for wid, w in current.items():
        if wid not in snap_widgets:
            await db.delete(w)
    await db.commit()
    return {"restored": version_id, "version_number": v.version_number}
