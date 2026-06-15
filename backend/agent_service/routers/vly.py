"""
.vly export / import router  (v2.1)

A .vly file is a ZIP archive (renamed extension) with the following layout:

    _vly_signature          – proprietary marker (first entry, identifies format)
    README.md               – human-readable guide for analysts / other tools
    manifest.json           – file index with descriptions (machine-readable)
    meta.json               – name, version, connection fingerprint, export info
    canvas.json             – all pages, widget layout + configs
    queries.json            – every SQL query keyed by widget_id
    schema.json             – table/column hints extracted from SQL
    schema_enriched.json    – full DDL snapshot for used tables  (when available)
    ai_context.json         – report-level AI hints for fast AI warm-start
    intelligence.json       – AI analysis: KPIs, sections, morning brief (when bundled)
    data/<widget_id>.json   – cached chart_data per widget  (Visually format, with types)
    data_flat/<title>.csv   – same data as flat CSV  (openable in Excel / Tableau / Looker)

Import: extracts in-memory, creates Dashboard + Widgets, restores cached data.
Connection auto-linked when host+database_name fingerprint matches.
"""
import csv
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
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

VLY_VERSION   = "2.1"
VLY_MIME_TYPE = "application/vnd.visually.canvas+zip"
VLY_MAGIC     = f"VISUALLY_CANVAS_ARCHIVE\nFORMAT_VERSION={VLY_VERSION}\nCREATED_BY=Visually\n"


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
    return list({
        m.lower() for m in re.findall(
            r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)',
            sql or '',
            re.IGNORECASE,
        )
    })


def _build_schema_hints(widgets: list[Widget]) -> list[dict]:
    tables: dict[str, set] = {}
    for w in widgets:
        sql = w.sql_query or w.base_sql or ''
        if not sql:
            continue
        for table in _extract_table_names(sql):
            tables.setdefault(table, set())
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


def _safe_filename(title: str, max_len: int = 48) -> str:
    """Convert a widget title to a safe filename (no UUID, human-readable)."""
    s = re.sub(r'[^\w\s-]', '', title or 'widget').strip()
    s = re.sub(r'[\s]+', '_', s)
    return s[:max_len] or 'widget'


def _rows_to_csv(rows: list, columns: list) -> str:
    """Convert chart_data rows+columns to a CSV string."""
    if not rows:
        header = ",".join(str(c) for c in columns)
        return header + "\n" if header else ""
    fieldnames = columns if columns else list(rows[0].keys()) if rows else []
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction='ignore', lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _dsn_template(conn_hint: dict) -> str:
    db_type = conn_hint.get("db_type", "")
    host    = conn_hint.get("host", "<host>")
    port    = conn_hint.get("port", "")
    db_name = conn_hint.get("database_name", "<database>")
    user    = conn_hint.get("username", "<user>")
    port_str = f":{port}" if port else ""
    if db_type == "redshift":
        return f"redshift+redshift_connector://{user}:<password>@{host}{port_str}/{db_name}"
    if db_type == "postgresql":
        return f"postgresql://{user}:<password>@{host}{port_str}/{db_name}"
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:<password>@{host}{port_str}/{db_name}"
    if db_type == "bigquery":
        return f"bigquery://{db_name}"
    if db_type in ("mssql", "sqlserver"):
        return f"mssql+pyodbc://{user}:<password>@{host}{port_str}/{db_name}"
    if db_type == "snowflake":
        return f"snowflake://{user}:<password>@{host}/{db_name}"
    return f"{db_type}://{user}:<password>@{host}{port_str}/{db_name}"


# ── GET /dashboards/{id}/export-vly ───────────────────────────────────────────

@router.get("/dashboards/{dashboard_id}/export-vly")
async def export_vly(
    dashboard_id: str,
    intelligence: Optional[str] = Query(None, description="JSON-encoded intelligence analysis from the frontend"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """
    Stream a .vly archive for the given canvas.

    Optional ?intelligence=<json> query param bundles the AI analysis
    (passed from the Intelligence page frontend).
    """
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

    # ── connection metadata (no password) ────────────────────────────────────
    conn_hint: dict = {}
    sample_conn_id = next((w.connection_id for w in widgets if w.connection_id), None)
    if sample_conn_id:
        conn_result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == sample_conn_id)
        )
        conn = conn_result.scalar_one_or_none()
        if conn:
            conn_hint = {
                "db_type":       conn.db_type.value,
                "host":          conn.host,
                "port":          conn.port,
                "database_name": conn.database_name,
                "username":      conn.username,
            }

    layout_cfg = dashboard.layout_config or {}
    pages      = layout_cfg.get("pages", [])
    now_iso    = datetime.utcnow().isoformat()

    # ── canvas.json ──────────────────────────────────────────────────────────
    canvas_doc = {
        "version":        VLY_VERSION,
        "name":           dashboard.name,
        "description":    dashboard.description or "",
        "theme":          dashboard.theme,
        "layout_config":  layout_cfg,
        "pages":          pages,
        "widgets": [
            {
                "id":                 str(w.id),
                "title":              w.title,
                "chart_type":         w.chart_type or "bar",
                "widget_type":        w.widget_type,
                "position_x":         w.position_x,
                "position_y":         w.position_y,
                "width":              w.width,
                "height":             w.height,
                "config":             w.config or {},
                "validation_score":   w.validation_score,
                "filterable_columns": w.filterable_columns or [],
                "connection_id":      str(w.connection_id) if w.connection_id else None,
                "data_file":          f"data/{str(w.id)}.json",
                "csv_file":           f"data_flat/{_safe_filename(w.title)}.csv",
            }
            for w in widgets
        ],
    }

    # ── meta.json ─────────────────────────────────────────────────────────────
    meta_doc = {
        "vly_version":        VLY_VERSION,
        "format":             "visually-canvas-archive",
        "mime_type":          VLY_MIME_TYPE,
        "canvas_name":        dashboard.name,
        "canvas_id":          str(dashboard.id),
        "project_id":         str(dashboard.project_id),
        "exported_at":        now_iso,
        "exported_by_id":     str(current_user.id),
        "exported_by_email":  current_user.email,
        "widget_count":       len(widgets),
        "page_count":         len(pages),
        "connection_hint":    conn_hint,
        "dsn_template":       _dsn_template(conn_hint) if conn_hint else None,
    }

    # ── queries.json ──────────────────────────────────────────────────────────
    queries_doc = {
        str(w.id): {
            "title":       w.title,
            "sql":         w.sql_query or "",
            "base_sql":    w.base_sql or "",
            "tables_used": _extract_table_names(w.sql_query or w.base_sql or ""),
        }
        for w in widgets
    }

    # ── schema.json ───────────────────────────────────────────────────────────
    schema_hints = _build_schema_hints(widgets)
    schema_doc = {
        "tables":        schema_hints,
        "filter_config": dashboard.filter_config or [],
    }

    # ── schema_enriched.json ──────────────────────────────────────────────────
    schema_enriched_doc: dict = {}
    used_tables_lower: set[str] = set()
    for hint in schema_hints:
        t = hint.get("table", "")
        if t:
            used_tables_lower.add(t.lower())
            used_tables_lower.add(t.split(".")[-1].lower())

    if sample_conn_id and used_tables_lower:
        from shared.models.schema_snapshots import SchemaSnapshot
        snap_result = await db.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.connection_id == sample_conn_id)
            .order_by(SchemaSnapshot.version.desc())
            .limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap and snap.schema_document:
            full_schema = snap.schema_document
            all_tables: dict = full_schema.get("tables", {}) if isinstance(full_schema, dict) else {}
            filtered_tables = {
                tbl: info for tbl, info in all_tables.items()
                if tbl.lower() in used_tables_lower or tbl.split(".")[-1].lower() in used_tables_lower
            }
            schema_enriched_doc = {
                "db_type":               conn_hint.get("db_type", ""),
                "snapshot_version":      snap.version,
                "snapshot_captured_at":  snap.created_at.isoformat() if snap.created_at else None,
                "tables":                filtered_tables,
                "prioritized": {
                    "tables": sorted(used_tables_lower),
                    "columns_by_widget": {
                        str(w.id): _extract_table_names(w.sql_query or w.base_sql or "")
                        for w in widgets
                    },
                },
            }

    # ── ai_context.json ───────────────────────────────────────────────────────
    ai_context_doc = {
        "report_name":        dashboard.name,
        "report_description": dashboard.description or "",
        "db_type":            conn_hint.get("db_type", "unknown"),
        "host":               conn_hint.get("host", ""),
        "database":           conn_hint.get("database_name", ""),
        "used_tables":        sorted(used_tables_lower),
        "widgets": [
            {
                "id":               str(w.id),
                "title":            w.title,
                "chart_type":       w.chart_type,
                "tables_used":      _extract_table_names(w.sql_query or w.base_sql or ""),
                "columns_selected": schema_hints[i]["columns"] if i < len(schema_hints) else [],
            }
            for i, w in enumerate(widgets)
        ],
        "filter_columns": [
            f.get("column") for f in (dashboard.filter_config or [])
            if isinstance(f, dict) and f.get("column")
        ],
    }

    # ── intelligence.json — AI analysis (bundled from frontend or empty shell) ─
    intelligence_doc: dict = {
        "vly_version":       VLY_VERSION,
        "canvas_name":       dashboard.name,
        "bundled_at":        now_iso,
        "source":            "none",
        "note":              "Open in Visually Intelligence page to generate AI analysis.",
        "analysis":          None,
    }
    if intelligence:
        try:
            parsed_intel = json.loads(intelligence)
            intelligence_doc.update({
                "source":   "frontend_export",
                "note":     "AI analysis bundled at export time from Visually Intelligence page.",
                "analysis": parsed_intel,
            })
        except (json.JSONDecodeError, TypeError):
            pass  # malformed — keep the empty shell

    # ── data/<widget_id>.json  +  data_flat/<title>.csv ───────────────────────
    data_files:  list[tuple[str, bytes]] = []
    csv_files:   list[tuple[str, bytes]] = []

    for w in widgets:
        chart_data = w.chart_data or {"rows": [], "columns": []}

        # JSON (Visually native — preserves types, used on re-import)
        payload = {
            "widget_id":   str(w.id),
            "title":       w.title,
            "chart_type":  w.chart_type,
            "chart_data":  chart_data,
            "exported_at": now_iso,
        }
        data_files.append((
            f"data/{str(w.id)}.json",
            json.dumps(payload, ensure_ascii=False, default=str).encode(),
        ))

        # CSV (flat — openable directly in Excel / Tableau / Looker / any BI tool)
        rows    = chart_data.get("rows", []) if isinstance(chart_data, dict) else []
        columns = chart_data.get("columns", []) if isinstance(chart_data, dict) else []
        csv_content = _rows_to_csv(rows, columns)
        if csv_content:
            safe_title = _safe_filename(w.title or str(w.id))
            csv_files.append((
                f"data_flat/{safe_title}.csv",
                csv_content.encode("utf-8"),
            ))

    # ── README.md — analyst-friendly guide ───────────────────────────────────
    widget_list = "\n".join(
        f"| {w.title} | {w.chart_type or 'chart'} | data_flat/{_safe_filename(w.title)}.csv |"
        for w in widgets
    )
    readme_content = f"""# {dashboard.name} — Visually Canvas Export (.vly)

## What is a .vly file?
A `.vly` file is a **Visually Canvas Archive** — a self-contained bundle of a
data canvas including all visualisations, SQL queries, schema context, cached
data, and an AI intelligence report.  It is a renamed ZIP archive.

---

## How to open this file in other tools

### Excel / Google Sheets
Rename `{dashboard.name}.vly` → `{dashboard.name}.zip`, open it, and use any
file from the **`data_flat/`** folder.  Each CSV corresponds to one chart widget.

### Tableau / Power BI / Looker
Connect directly to the CSV files in `data_flat/` as a flat-file data source.

### Python / Pandas
```python
import zipfile, pandas as pd
with zipfile.ZipFile("{dashboard.name}.vly") as z:
    df = pd.read_csv(z.open("data_flat/YOUR_WIDGET.csv"))
```

### Re-import into Visually
Use the Canvas → Import button and select this `.vly` file.

---

## File Contents

| File | Purpose |
|------|---------|
| `_vly_signature` | Format identifier (do not delete) |
| `manifest.json` | Machine-readable file index |
| `meta.json` | Export metadata + DB connection fingerprint |
| `canvas.json` | Widget layout, configs, pages, theme |
| `queries.json` | All SQL queries keyed by widget ID |
| `schema.json` | Table/column hints extracted from SQL |
| `schema_enriched.json` | Full DDL snapshot for used tables |
| `ai_context.json` | AI-ready report context for warm-start |
| `intelligence.json` | AI analysis: KPIs, sections, morning brief |
| `data/<id>.json` | Cached chart data (Visually native format) |
| `data_flat/<title>.csv` | **Flat CSV — use this in other tools** |

---

## Widgets ({len(widgets)} total)

| Widget | Type | CSV File |
|--------|------|----------|
{widget_list}

---

## Connection Info
- **Database type:** {conn_hint.get('db_type', 'N/A')}
- **Host:** {conn_hint.get('host', 'N/A')}
- **Database:** {conn_hint.get('database_name', 'N/A')}
- **DSN template:** `{_dsn_template(conn_hint) if conn_hint else 'N/A'}`

---

Exported from **Visually** · {now_iso} · Format v{VLY_VERSION}
"""

    # ── manifest.json — machine-readable file index ───────────────────────────
    all_data_entries = [
        {"path": f"data/{str(w.id)}.json",  "type": "widget_data_json", "widget_id": str(w.id), "widget_title": w.title}
        for w in widgets
    ]
    all_csv_entries = [
        {"path": f"data_flat/{_safe_filename(w.title or str(w.id))}.csv", "type": "widget_data_csv", "widget_id": str(w.id), "widget_title": w.title}
        for w in widgets
        if any(w.chart_data and isinstance(w.chart_data, dict) and w.chart_data.get("rows") for _ in [None])
    ]
    manifest_doc = {
        "vly_version":  VLY_VERSION,
        "format":       "visually-canvas-archive",
        "mime_type":    VLY_MIME_TYPE,
        "canvas_name":  dashboard.name,
        "exported_at":  now_iso,
        "files": [
            {"path": "_vly_signature",      "type": "signature",          "required": True,  "description": "Format identifier"},
            {"path": "README.md",           "type": "readme",             "required": False, "description": "Human-readable guide"},
            {"path": "manifest.json",       "type": "manifest",           "required": False, "description": "This file — machine-readable index"},
            {"path": "meta.json",           "type": "metadata",           "required": True,  "description": "Export metadata and DB fingerprint"},
            {"path": "canvas.json",         "type": "canvas",             "required": True,  "description": "Widget layout, pages, configs"},
            {"path": "queries.json",        "type": "queries",            "required": False, "description": "SQL queries per widget"},
            {"path": "schema.json",         "type": "schema_hints",       "required": False, "description": "Table/column hints from SQL"},
            {"path": "schema_enriched.json","type": "schema_full",        "required": False, "description": "Full DDL snapshot for used tables"},
            {"path": "ai_context.json",     "type": "ai_context",         "required": False, "description": "AI warm-start context"},
            {"path": "intelligence.json",   "type": "intelligence",       "required": False, "description": "AI analysis: KPIs, sections, brief"},
            *all_data_entries,
            *all_csv_entries,
        ],
    }

    # ── assemble ZIP ──────────────────────────────────────────────────────────
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        # Signature must be first so format can be identified by reading entry #0
        zf.writestr("_vly_signature",    VLY_MAGIC)
        zf.writestr("README.md",         readme_content)
        zf.writestr("manifest.json",     json.dumps(manifest_doc,     ensure_ascii=False, indent=2, default=str))
        zf.writestr("meta.json",         json.dumps(meta_doc,         ensure_ascii=False, indent=2, default=str))
        zf.writestr("canvas.json",       json.dumps(canvas_doc,       ensure_ascii=False, indent=2, default=str))
        zf.writestr("queries.json",      json.dumps(queries_doc,      ensure_ascii=False, indent=2, default=str))
        zf.writestr("schema.json",       json.dumps(schema_doc,       ensure_ascii=False, indent=2, default=str))
        zf.writestr("ai_context.json",   json.dumps(ai_context_doc,   ensure_ascii=False, indent=2, default=str))
        zf.writestr("intelligence.json", json.dumps(intelligence_doc,  ensure_ascii=False, indent=2, default=str))
        if schema_enriched_doc:
            zf.writestr("schema_enriched.json", json.dumps(schema_enriched_doc, ensure_ascii=False, indent=2, default=str))
        for path, content in data_files:
            zf.writestr(path, content)
        for path, content in csv_files:
            zf.writestr(path, content)

    buf.seek(0)
    raw = buf.read()

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in dashboard.name)[:64]
    filename  = f"{safe_name}.vly"

    return Response(
        content=raw,
        media_type=VLY_MIME_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length":      str(len(raw)),
            "Cache-Control":       "no-cache",
            "X-Vly-Version":       VLY_VERSION,
            "X-Vly-Canvas":        dashboard.name,
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

    - Accepts v1.x and v2.x archives (canvas.json + meta.json required).
    - Restores cached widget data immediately (offline-capable).
    - Auto-links connection when host+database_name fingerprint matches.
    - Bundles intelligence.json back onto the dashboard description if present.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        buf = io.BytesIO(raw)
        zf  = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid .vly file (not a valid ZIP archive)")

    names = zf.namelist()
    if "canvas.json" not in names or "meta.json" not in names:
        raise HTTPException(status_code=400, detail="Invalid .vly file (missing canvas.json or meta.json)")

    try:
        canvas_doc:  dict = json.loads(zf.read("canvas.json"))
        meta_doc:    dict = json.loads(zf.read("meta.json"))
        queries_doc: dict = json.loads(zf.read("queries.json")) if "queries.json" in names else {}
        intel_doc:   dict = json.loads(zf.read("intelligence.json")) if "intelligence.json" in names else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Malformed JSON in .vly: {exc}")

    # ── resolve connection ─────────────────────────────────────────────────────
    resolved_conn_id: Optional[uuid.UUID] = None

    if connection_id:
        resolved_conn_id = uuid.UUID(connection_id)
    else:
        hint = meta_doc.get("connection_hint", {})
        if hint.get("host") and hint.get("database_name"):
            conn_result = await db.execute(
                select(DatabaseConnection).where(
                    DatabaseConnection.project_id == uuid.UUID(project_id),
                    DatabaseConnection.host        == hint["host"],
                    DatabaseConnection.database_name == hint["database_name"],
                    DatabaseConnection.is_active   == True,
                ).limit(1)
            )
            matched = conn_result.scalar_one_or_none()
            if matched:
                resolved_conn_id = matched.id

    # ── create Dashboard ──────────────────────────────────────────────────────
    canvas_name = canvas_doc.get("name") or meta_doc.get("canvas_name") or "Imported Canvas"
    # Embed intelligence summary into description when bundled
    description = canvas_doc.get("description") or ""
    intel_note  = ""
    if intel_doc.get("analysis"):
        morning = (intel_doc["analysis"] or {}).get("morning_brief", "")
        if morning:
            intel_note = f"\n\n[AI Brief] {morning[:300]}{'…' if len(morning) > 300 else ''}"

    new_dash = Dashboard(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        name=f"{canvas_name} (imported)",
        description=(description + intel_note).strip(),
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

    for w_def in widget_defs:
        old_id   = w_def.get("id", "")
        new_id   = uuid.uuid4()

        # Prefer native JSON data (preserves types); fall back to CSV
        data_file  = w_def.get("data_file", f"data/{old_id}.json")
        chart_data: dict = {"rows": [], "columns": []}
        if data_file in names:
            try:
                payload    = json.loads(zf.read(data_file))
                chart_data = payload.get("chart_data", chart_data)
            except Exception:
                pass

        q_info    = queries_doc.get(old_id, {})
        sql_query = q_info.get("sql")   or None
        base_sql  = q_info.get("base_sql") or None
        config    = w_def.get("config") or {}

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
        "dashboard_id":       str(new_dash.id),
        "name":               new_dash.name,
        "widget_count":       len(widget_defs),
        "connection_linked":  resolved_conn_id is not None,
        "connection_id":      str(resolved_conn_id) if resolved_conn_id else None,
        "project_id":         project_id,
        "original_name":      canvas_doc.get("name"),
        "intelligence_bundled": bool(intel_doc.get("analysis")),
    }
