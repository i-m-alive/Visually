"""
Public / anonymous canvas endpoints — no Bearer token required.

GET  /public/canvas/{token}          → returns canvas + widget data (no SQL exposed)
POST /public/canvas/{token}/refresh  → re-runs SQL for live-mode canvases, returns fresh data
"""
import hashlib
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.sharing import CanvasShareToken
from shared.models.widgets import Widget

router = APIRouter(tags=["public"])


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _resolve_token(raw_token: str, db: AsyncSession) -> CanvasShareToken:
    """Validate a raw share token and return the DB row."""
    result = await db.execute(
        select(CanvasShareToken).where(
            CanvasShareToken.token_hash == _hash(raw_token),
            CanvasShareToken.is_revoked == False,
        )
    )
    token_obj = result.scalar_one_or_none()
    if not token_obj:
        raise HTTPException(status_code=404, detail="Share link not found or has been revoked")
    if token_obj.expires_at and token_obj.expires_at < datetime.utcnow():
        raise HTTPException(status_code=410, detail="Share link has expired")

    # Update access tracking
    token_obj.last_used_at = datetime.utcnow()
    token_obj.access_count = (token_obj.access_count or 0) + 1
    await db.commit()

    return token_obj


# ── GET /public/canvas/{token} ────────────────────────────────────────────────

@router.get("/public/canvas/{raw_token}")
async def get_public_canvas(
    raw_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Return canvas layout + cached widget data for an anonymous viewer.

    SQL queries and raw connection details are never returned to the public.
    For live-mode canvases the caller should POST /public/canvas/{token}/refresh
    to get fresh data (server proxies the query on their behalf).
    """
    token_obj = await _resolve_token(raw_token, db)

    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == token_obj.dashboard_id)
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widget_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == dashboard.id)
    )
    widgets = list(widget_result.scalars().all())

    layout_cfg = dashboard.layout_config or {}

    return {
        "id": str(dashboard.id),
        "name": dashboard.name,
        "theme": dashboard.theme,
        "layout_config": layout_cfg,
        "pages": layout_cfg.get("pages", []),
        "filter_config": dashboard.filter_config or [],
        "share_mode": token_obj.mode,
        "widgets": [
            {
                "id": str(w.id),
                "title": w.title,
                "chart_type": w.chart_type,
                "position_x": w.position_x,
                "position_y": w.position_y,
                "width": w.width,
                "height": w.height,
                "config": w.config or {},
                "filterable_columns": w.filterable_columns or [],
                "chart_data": w.chart_data or {"rows": [], "columns": []},
                # sql_query intentionally omitted — never expose raw SQL publicly
                "connection_id": None,
            }
            for w in widgets
        ],
    }


# ── POST /public/canvas/{token}/refresh ───────────────────────────────────────

@router.post("/public/canvas/{raw_token}/refresh")
async def refresh_public_canvas(
    raw_token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Re-execute all widget SQL queries (live mode only) and return fresh chart_data.

    The server owns the DB connection — the anonymous viewer never touches the DB.
    This is the core of "live share": your server proxies on behalf of the viewer.
    """
    token_obj = await _resolve_token(raw_token, db)

    if token_obj.mode != "live":
        raise HTTPException(
            status_code=400,
            detail="This share link is in snapshot mode — live refresh is not available",
        )

    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == token_obj.dashboard_id)
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widget_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == dashboard.id)
    )
    widgets = list(widget_result.scalars().all())

    from agent_service.utils.http_clients import call_query_executor
    import asyncio

    async def _refresh_widget(w: Widget) -> dict:
        sql = w.base_sql or w.sql_query
        if not sql or not w.connection_id:
            return {"widget_id": str(w.id), "chart_data": w.chart_data}
        try:
            result = await call_query_executor(str(w.connection_id), sql, row_limit=500)
            if result and not result.get("error"):
                return {
                    "widget_id": str(w.id),
                    "chart_data": {
                        "rows": result.get("rows", []),
                        "columns": result.get("columns", []),
                    },
                }
        except Exception as exc:
            print(f"[public-refresh] widget {w.id} failed: {exc}", flush=True)
        return {"widget_id": str(w.id), "chart_data": w.chart_data}

    results = await asyncio.gather(*[_refresh_widget(w) for w in widgets], return_exceptions=True)
    return {
        "dashboard_id": str(dashboard.id),
        "refreshed_at": datetime.utcnow().isoformat(),
        "widgets": [r for r in results if isinstance(r, dict)],
    }
