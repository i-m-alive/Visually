"""
.vly export / import router.

A .vly file is a ZIP archive (renamed extension) containing:
    canvas.json    – all pages, widget layout + configs
    meta.json      – name, version, connection fingerprint, exported_at
    queries.json   – every SQL query keyed by widget_id (for AI context)
    schema.json    – table/column hints used in the report
    data/          – cached chart_data per widget (JSON, not CSV — keeps types)

On import the archive is extracted in-memory, a new Dashboard + Widgets are
created and the cached data is restored immediately so the canvas opens even
without a live DB connection.  If the importer has a DB connection whose host +
database_name matches the fingerprint in meta.json it is auto-linked.
"""
import io
import json
import os
import uuid
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.database_connections import DatabaseConnection
from shared.models.users import User
from shared.models.widgets import Widget
from shared.security import decode_token

router = APIRouter(tags=["vly"])

bearer_scheme = HTTPBearer(auto_error=False)

DEV_MODE    = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")

VLY_VERSION = "1.0"


# ── auth helper ───────────────────────────────────────────────────────────────

async def _get_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if DEV_MODE and credentials is None:
        from shared.security import hash_password
        dev_id = uuid.UUID(DEV_USER_ID)
        result = await db.execute(select(User).where(User.id == dev_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                id=dev_id,
                email=os.getenv("DEV_USER_EMAIL", "dev@visually.local"),
                hashed_password=hash_password("dev-password"),
                full_name="Dev User",
                is_active=True,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_table_names(sql: str) -> list[str]:
    """Best-effort extraction of table names from a SQL query."""
    import re
    # Matches FROM/JOIN <name> patterns
    return list({
        m.lower() for m in re.findall(
            r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)',
            sql or '',
            re.IGNORECASE,
        )
    })


def _build_schema_hints(widgets: list[Widget]) -> list[dict]:
    """Build a deduplicated list of table+column hints from widget SQL."""
    import re
    tables: dict[str, set] = {}
    for w in widgets:
        sql = w.sql_query or w.base_sql or ''
        if not sql:
            continue
        for table in _extract_table_names(sql):
            tables.setdefault(table, set())
        # pull SELECT column names (rough extraction)
        sel = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if sel:
            raw = sel.group(1)
            for col in re.split(r',', raw):
                col = col.strip()
                alias_m = re.search(r'\bAS\s+([a-zA-Z_]\w*)', col, re.IGNORECASE)
                name = alias_m.group(1) if alias_m else col.split('.')[-1].strip()
                for tbl in _extract_table_names(sql):
                    tables.setdefault(tbl, set()).add(name)

    return [
        {"table": tbl, "columns": sorted(cols)}
        for tbl, cols in sorted(tables.items())
    ]


# ── GET /dashboards/{id}/export-vly ───────────────────────────────────────────

@router.get("/dashboards/{dashboard_id}/export-vly")
async def export_vly(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Stream a .vly archive for the given canvas."""
    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widget_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets: list[Widget] = list(widget_result.scalars().all())

    # Pull connection metadata (no credentials)
    conn_hint: dict = {}
    sample_conn_id = next((w.connection_id for w in widgets if w.connection_id), None)
    if sample_conn_id:
        conn_result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == sample_conn_id)
        )
        conn = conn_result.scalar_one_or_none()
        if conn:
            conn_hint = {
                "db_type": conn.db_type.value,
                "host": conn.host,
                "port": conn.port,
                "database_name": conn.database_name,
                "username": conn.username,
            }

    layout_cfg = dashboard.layout_config or {}
    pages = layout_cfg.get("pages", [])

    # ── canvas.json ──────────────────────────────────────────────────────────
    canvas_doc = {
        "version": VLY_VERSION,
        "name": dashboard.name,
        "description": dashboard.description or "",
        "theme": dashboard.theme,
        "layout_config": layout_cfg,
        "pages": pages,
        "widgets": [
            {
                "id": str(w.id),
                "title": w.title,
                "chart_type": w.chart_type or "bar",
                "widget_type": w.widget_type,
                "position_x": w.position_x,
                "position_y": w.position_y,
                "width": w.width,
                "height": w.height,
                "config": w.config or {},
                "validation_score": w.validation_score,
                "filterable_columns": w.filterable_columns or [],
                "connection_id": str(w.connection_id) if w.connection_id else None,
                "data_file": f"data/{str(w.id)}.json",
            }
            for w in widgets
        ],
    }

    # ── meta.json ─────────────────────────────────────────────────────────────
    meta_doc = {
        "vly_version": VLY_VERSION,
        "canvas_name": dashboard.name,
        "canvas_id": str(dashboard.id),
        "project_id": str(dashboard.project_id),
        "exported_at": datetime.utcnow().isoformat(),
        "exported_by": str(current_user.id),
        "widget_count": len(widgets),
        "page_count": len(pages),
        "connection_hint": conn_hint,
    }

    # ── queries.json ──────────────────────────────────────────────────────────
    queries_doc = {
        str(w.id): {
            "title": w.title,
            "sql": w.sql_query or "",
            "base_sql": w.base_sql or "",
            "tables_used": _extract_table_names(w.sql_query or w.base_sql or ""),
        }
        for w in widgets
    }

    # ── schema.json ───────────────────────────────────────────────────────────
    schema_doc = {
        "tables": _build_schema_hints(widgets),
        "filter_config": dashboard.filter_config or [],
    }

    # ── data/<widget_id>.json per widget ─────────────────────────────────────
    data_files: list[tuple[str, bytes]] = []
    for w in widgets:
        payload = {
            "widget_id": str(w.id),
            "title": w.title,
            "chart_type": w.chart_type,
            "chart_data": w.chart_data or {"rows": [], "columns": []},
            "exported_at": datetime.utcnow().isoformat(),
        }
        data_files.append((
            f"data/{str(w.id)}.json",
            json.dumps(payload, ensure_ascii=False, default=str).encode(),
        ))

    # ── build ZIP in memory ───────────────────────────────────────────────────
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("canvas.json", json.dumps(canvas_doc, ensure_ascii=False, indent=2, default=str))
        zf.writestr("meta.json",   json.dumps(meta_doc,   ensure_ascii=False, indent=2, default=str))
        zf.writestr("queries.json", json.dumps(queries_doc, ensure_ascii=False, indent=2, default=str))
        zf.writestr("schema.json", json.dumps(schema_doc, ensure_ascii=False, indent=2, default=str))
        for path, content in data_files:
            zf.writestr(path, content)

    buf.seek(0)
    raw = buf.read()

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in dashboard.name)[:64]
    filename = f"{safe_name}.vly"

    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(raw)),
            "Cache-Control": "no-cache",
        },
    )


# ── POST /dashboards/import-vly ───────────────────────────────────────────────

@router.post("/dashboards/import-vly", status_code=201)
async def import_vly(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    connection_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """
    Import a .vly archive into the given project.

    If connection_id is provided (or auto-matched by host+db fingerprint),
    widgets are linked to that connection so live data is available immediately.
    Returns the new dashboard id so the frontend can navigate to it.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        buf = io.BytesIO(raw)
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid .vly file (not a valid ZIP archive)")

    names = zf.namelist()
    if "canvas.json" not in names or "meta.json" not in names:
        raise HTTPException(status_code=400, detail="Invalid .vly file (missing canvas.json or meta.json)")

    try:
        canvas_doc: dict  = json.loads(zf.read("canvas.json"))
        meta_doc: dict    = json.loads(zf.read("meta.json"))
        queries_doc: dict = json.loads(zf.read("queries.json")) if "queries.json" in names else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed JSON in .vly: {exc}")

    # ── resolve connection_id ──────────────────────────────────────────────────
    resolved_conn_id: Optional[uuid.UUID] = None

    if connection_id:
        resolved_conn_id = uuid.UUID(connection_id)
    else:
        # Try to auto-match by fingerprint
        hint = meta_doc.get("connection_hint", {})
        if hint.get("host") and hint.get("database_name"):
            conn_result = await db.execute(
                select(DatabaseConnection).where(
                    DatabaseConnection.project_id == uuid.UUID(project_id),
                    DatabaseConnection.host == hint["host"],
                    DatabaseConnection.database_name == hint["database_name"],
                    DatabaseConnection.is_active == True,
                ).limit(1)
            )
            matched = conn_result.scalar_one_or_none()
            if matched:
                resolved_conn_id = matched.id

    # ── create Dashboard ──────────────────────────────────────────────────────
    canvas_name = canvas_doc.get("name") or meta_doc.get("canvas_name") or "Imported Canvas"
    new_dash = Dashboard(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        name=f"{canvas_name} (imported)",
        description=canvas_doc.get("description") or "",
        theme=canvas_doc.get("theme", "frost"),
        layout_config=canvas_doc.get("layout_config") or {"pages": canvas_doc.get("pages", [])},
        filter_config=None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(new_dash)
    await db.flush()

    # ── create Widgets ────────────────────────────────────────────────────────
    widget_defs: list[dict] = canvas_doc.get("widgets", [])
    old_to_new: dict[str, str] = {}  # original widget id → new widget id

    for w_def in widget_defs:
        old_id = w_def.get("id", "")
        new_id = uuid.uuid4()
        old_to_new[old_id] = str(new_id)

        # Load cached data from archive
        data_file = w_def.get("data_file", f"data/{old_id}.json")
        chart_data: dict = {"rows": [], "columns": []}
        if data_file in names:
            try:
                payload = json.loads(zf.read(data_file))
                chart_data = payload.get("chart_data", chart_data)
            except Exception:
                pass

        # Resolve SQL from queries.json
        q_info = queries_doc.get(old_id, {})
        sql_query = q_info.get("sql") or None
        base_sql  = q_info.get("base_sql") or None

        # Config — preserve page_id mapping (pages were restored in layout_config above)
        config = w_def.get("config") or {}

        new_widget = Widget(
            id=new_id,
            dashboard_id=new_dash.id,
            title=w_def.get("title", "Chart"),
            widget_type=w_def.get("widget_type", "chart"),
            chart_type=w_def.get("chart_type", "bar"),
            sql_query=sql_query,
            base_sql=base_sql,
            connection_id=resolved_conn_id,
            position_x=w_def.get("position_x", 0),
            position_y=w_def.get("position_y", 0),
            width=w_def.get("width", 6),
            height=w_def.get("height", 6),
            config=config,
            chart_data=chart_data,
            filterable_columns=w_def.get("filterable_columns") or [],
            validation_score=w_def.get("validation_score"),
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(new_widget)

    await db.commit()
    await db.refresh(new_dash)

    zf.close()

    return {
        "dashboard_id": str(new_dash.id),
        "name": new_dash.name,
        "widget_count": len(widget_defs),
        "connection_linked": resolved_conn_id is not None,
        "connection_id": str(resolved_conn_id) if resolved_conn_id else None,
        "project_id": project_id,
        "original_name": canvas_doc.get("name"),
    }
