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
from typing import Any, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
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

SCHEMA_CRAWLER_URL = os.getenv("SCHEMA_CRAWLER_URL", "http://localhost:8003")


async def _crawl_and_refresh(
    dashboard_id: str,
    connection_id: str,
    project_id: str,
    do_crawl: bool,
    do_refresh: bool,
) -> None:
    """Background follow-up for bind / import.

    Crawling the schema and re-running every widget's SQL can take minutes on a
    cold backend (scale-to-zero executor + Redshift Serverless wake + dozens of
    widget queries). Doing it inline made the HTTP request exceed the Azure
    Container Apps gateway timeout — the aborted response carried no CORS headers,
    so the browser reported a misleading "blocked by CORS policy" error.

    The bind/import itself is already committed before this runs, so the canvas is
    usable immediately; live data fills in here and lazily when the canvas opens.
    """
    if do_crawl:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                await client.post(
                    f"{SCHEMA_CRAWLER_URL}/crawl",
                    json={"connection_id": connection_id, "project_id": project_id},
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[bg-followup] crawl failed (non-fatal): {exc}", flush=True)
    if do_refresh:
        try:
            from agent_service.scheduler import run_dashboard_refresh
            summary = await run_dashboard_refresh(dashboard_id)
            print(f"[bg-followup] refresh done dashboard={dashboard_id[:8]}: {summary}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[bg-followup] refresh failed (non-fatal): {exc}", flush=True)


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
                username=os.getenv("DEV_USER_EMAIL", "dev@visually.local").split("@")[0],
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


# ── export-vly (GET + POST) ───────────────────────────────────────────────────

class ExportVlyBody(BaseModel):
    intelligence: Optional[Any] = None
    # Embed the baked schema cache + metadata so the importing environment skips the
    # cold build (crawl + LLM enrichment). Set False to exclude DB values from the file.
    include_schema_cache: bool = True


@router.get("/dashboards/{dashboard_id}/export-vly")
async def export_vly(
    dashboard_id: str,
    intelligence: Optional[str] = Query(None, description="JSON-encoded intelligence analysis"),
    include_schema_cache: bool = Query(
        True,
        description="Embed baked schema cache + metadata so import skips the cold build",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Stream a .vly archive (GET — for the small / no-intelligence case)."""
    return await _generate_vly(
        dashboard_id, intelligence, db, current_user, include_schema_cache
    )


@router.post("/dashboards/{dashboard_id}/export-vly")
async def export_vly_post(
    dashboard_id: str,
    body: ExportVlyBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Stream a .vly archive, bundling the AI analysis from the request BODY.

    The intelligence report can be tens of KB, which overflows the URL length limit
    if passed as a query string — so the frontend POSTs it here instead.
    """
    intel_str = json.dumps(body.intelligence, default=str) if body.intelligence is not None else None
    return await _generate_vly(
        dashboard_id, intel_str, db, current_user, body.include_schema_cache
    )


async def _generate_vly(
    dashboard_id: str,
    intelligence: Optional[str],   # JSON string (or None)
    db: AsyncSession,
    current_user: User,
    include_schema_cache: bool = True,
):
    """Build and stream the .vly archive for a canvas."""
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
            # crawlers emit tables as a LIST ([{name, schema, columns,...}]); tolerate a
            # dict shape too for forward/backward compatibility.
            raw_tables = full_schema.get("tables", []) if isinstance(full_schema, dict) else []
            if isinstance(raw_tables, dict):
                table_items = list(raw_tables.items())
            else:
                table_items = [
                    (
                        (f"{t.get('schema')}.{t.get('name')}" if t.get("schema") else t.get("name", "")),
                        t,
                    )
                    for t in raw_tables
                    if isinstance(t, dict) and t.get("name")
                ]
            filtered_tables = {
                tbl: info for tbl, info in table_items
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

    # ── schema_cache.json + schema_metadata.json ──────────────────────────────
    # Embed the fully-baked enriched schema cache (column map, LLM disambiguation,
    # table semantics, example values, confirmed FK graph) AND the durable DB
    # metadata rows. On import this lets the AI copilot skip the cold build entirely
    # (no crawl, no LLM) — it is warm before the user types their first question.
    schema_cache_doc: dict = {}
    schema_metadata_doc: dict = {}
    if include_schema_cache and sample_conn_id:
        try:
            from shared.models.schema_snapshots import SchemaSnapshot
            from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
            from agent_service.agents import schema_cache as _sc

            cache_snap_result = await db.execute(
                select(SchemaSnapshot)
                .where(SchemaSnapshot.connection_id == sample_conn_id)
                .order_by(SchemaSnapshot.version.desc())
                .limit(1)
            )
            cache_snap = cache_snap_result.scalar_one_or_none()

            if cache_snap and cache_snap.schema_document:
                db_type_str = conn_hint.get("db_type") or "postgresql"
                enriched_json = await _sc.export_cache_for_connection(
                    str(sample_conn_id), cache_snap.schema_document, db_type_str
                )
                if enriched_json:
                    schema_cache_doc = {
                        "vly_version":           VLY_VERSION,
                        "cache_format_version":  1,
                        "db_type":               db_type_str,
                        "schema_hash":           _sc.compute_schema_hash(cache_snap.schema_document),
                        "snapshot_version":      cache_snap.version,
                        "connection_fingerprint": conn_hint,
                        "exported_at":           now_iso,
                        # nested object (human-readable in the zip); re-serialized on import
                        "enriched":              json.loads(enriched_json),
                    }

                # Approach C — durable LLM metadata rows (warms future re-builds too)
                tbl_meta_rows = (await db.execute(
                    select(SchemaTableMetadata)
                    .where(SchemaTableMetadata.connection_id == sample_conn_id)
                )).scalars().all()
                col_meta_rows = (await db.execute(
                    select(SchemaColumnMetadata)
                    .where(SchemaColumnMetadata.connection_id == sample_conn_id)
                )).scalars().all()

                if tbl_meta_rows or col_meta_rows:
                    schema_metadata_doc = {
                        "vly_version":          VLY_VERSION,
                        "cache_format_version": 1,
                        "snapshot_version":     cache_snap.version,
                        "exported_at":          now_iso,
                        "tables": [
                            {
                                "schema_snapshot_version": r.schema_snapshot_version,
                                "table_name":              r.table_name,
                                "business_name":           r.business_name,
                                "description":             r.description,
                                "grain":                   r.grain,
                                "is_fact_table":           r.is_fact_table,
                                "use_for":                 r.use_for,
                                "never_use_for":           r.never_use_for,
                                "key_metric_cols":         r.key_metric_cols,
                                "key_dimension_cols":      r.key_dimension_cols,
                                "key_date_cols":           r.key_date_cols,
                                "generation_method":       r.generation_method,
                            }
                            for r in tbl_meta_rows
                        ],
                        "columns": [
                            {
                                "schema_snapshot_version": r.schema_snapshot_version,
                                "table_name":              r.table_name,
                                "column_name":             r.column_name,
                                "business_name":           r.business_name,
                                "description":             r.description,
                                "semantic_type":           r.semantic_type,
                                "fk_target_table":         r.fk_target_table,
                                "fk_target_column":        r.fk_target_column,
                                "fk_confirmed":            r.fk_confirmed,
                                "fk_confirmation_score":   r.fk_confirmation_score,
                                "example_values":          r.example_values,
                                "is_kpi_metric":           r.is_kpi_metric,
                                "is_dimension":            r.is_dimension,
                                "is_filter_eligible":      r.is_filter_eligible,
                                "generation_method":       r.generation_method,
                            }
                            for r in col_meta_rows
                        ],
                    }
                print(
                    f"[vly-export] embedded schema cache "
                    f"(cache={'yes' if schema_cache_doc else 'no'}, "
                    f"tbl_meta={len(tbl_meta_rows)}, col_meta={len(col_meta_rows)})",
                    flush=True,
                )
        except Exception as exc:
            print(f"[vly-export] schema cache embed failed (non-fatal): {exc}", flush=True)
            schema_cache_doc = {}
            schema_metadata_doc = {}

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
| `schema_cache.json` | Baked enriched schema cache — lets the AI copilot skip the cold build on import |
| `schema_metadata.json` | LLM-generated table/column metadata (semantics, FKs, example values) |
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
            *([{"path": "schema_cache.json",   "type": "schema_cache",    "required": False, "description": "Baked enriched schema cache — import skips the cold build"}] if schema_cache_doc else []),
            *([{"path": "schema_metadata.json","type": "schema_metadata", "required": False, "description": "LLM-generated table/column metadata rows"}] if schema_metadata_doc else []),
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
        if schema_cache_doc:
            zf.writestr("schema_cache.json", json.dumps(schema_cache_doc, ensure_ascii=False, indent=2, default=str))
        if schema_metadata_doc:
            zf.writestr("schema_metadata.json", json.dumps(schema_metadata_doc, ensure_ascii=False, indent=2, default=str))
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
    background_tasks: BackgroundTasks,
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

    imported_layout = dict(canvas_doc.get("layout_config") or {"pages": canvas_doc.get("pages", [])})
    # Persist the connection on the layout too — the chat copilot resolves a
    # dashboard's connection from layout_config.connection_id (or a widget's), so
    # storing it here keeps live data working even if a widget loses its binding.
    if resolved_conn_id:
        imported_layout["connection_id"] = str(resolved_conn_id)

    new_dash = Dashboard(
        id=uuid.uuid4(),
        project_id=uuid.UUID(project_id),
        name=f"{canvas_name} (imported)",
        description=(description + intel_note).strip(),
        theme=canvas_doc.get("theme", "frost"),
        layout_config=imported_layout,
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

    # ── warm-start the AI copilots from the embedded schema cache + metadata ───
    # If the .vly carries a baked schema cache and/or LLM metadata rows and we
    # resolved a live connection, restore them so both the chat copilot (enriched
    # cache) and the intelligence page (metadata rows) are warm with ZERO cold build
    # (no crawl, no LLM). Safe & idempotent — see _restore_schema_warmstart.
    cache_warmstarted = False
    if resolved_conn_id and ("schema_cache.json" in names or "schema_metadata.json" in names):
        try:
            cache_warmstarted = await _restore_schema_warmstart(
                db, resolved_conn_id, zf, names
            )
        except Exception as exc:
            print(f"[import-vly] schema cache warm-start failed (non-fatal): {exc}", flush=True)

    zf.close()

    # Re-run every widget's SQL against the live connection so cached snapshots
    # (esp. KPI widgets whose query returned 0 rows / empty chart_data at export)
    # are replaced with live data. Done in the BACKGROUND — running it inline made
    # the request exceed the gateway timeout (cold executor + Redshift wake + many
    # widget queries). Cached data shows immediately; live data fills in after.
    refresh_scheduled = False
    if resolved_conn_id:
        background_tasks.add_task(
            _crawl_and_refresh,
            str(new_dash.id), str(resolved_conn_id), project_id,
            False,  # crawl handled by the embedded-cache warm-start above
            True,   # refresh widgets with live data
        )
        refresh_scheduled = True

    return {
        "dashboard_id":       str(new_dash.id),
        "name":               new_dash.name,
        "widget_count":       len(widget_defs),
        "connection_linked":  resolved_conn_id is not None,
        "connection_id":      str(resolved_conn_id) if resolved_conn_id else None,
        "project_id":         project_id,
        "original_name":      canvas_doc.get("name"),
        "intelligence_bundled": bool(intel_doc.get("analysis")),
        "refreshed":          0,
        "refresh_status":     "in_progress" if refresh_scheduled else "skipped",
        "schema_cache_warmstarted": cache_warmstarted,
    }


async def _restore_metadata_rows(
    db: AsyncSession,
    resolved_conn_id: uuid.UUID,
    zf: zipfile.ZipFile,
    names: list[str],
    default_version: int,
) -> int:
    """
    Insert the embedded LLM metadata rows (schema_metadata.json) for this connection.
    These are what the Intelligence page reads directly (SchemaTableMetadata /
    SchemaColumnMetadata), so restoring them warms the analyst's briefing without a crawl.

    Caller is responsible for ensuring no rows already exist for this connection
    (this function does NOT dedupe). Returns the number of table-rows inserted
    (0 if nothing was embedded / unreadable). Does not commit.
    """
    if "schema_metadata.json" not in names:
        return 0
    from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata
    try:
        meta_doc = json.loads(zf.read("schema_metadata.json"))
    except Exception as exc:
        print(f"[import-vly] schema_metadata.json unreadable (non-fatal): {exc}", flush=True)
        return 0

    tbl_defs = meta_doc.get("tables", []) or []
    col_defs = meta_doc.get("columns", []) or []

    for t in tbl_defs:
        db.add(SchemaTableMetadata(
            id=uuid.uuid4(),
            connection_id=resolved_conn_id,
            schema_snapshot_version=int(t.get("schema_snapshot_version") or default_version),
            table_name=t.get("table_name") or "",
            business_name=t.get("business_name"),
            description=t.get("description"),
            grain=t.get("grain"),
            is_fact_table=t.get("is_fact_table"),
            use_for=t.get("use_for"),
            never_use_for=t.get("never_use_for"),
            key_metric_cols=t.get("key_metric_cols"),
            key_dimension_cols=t.get("key_dimension_cols"),
            key_date_cols=t.get("key_date_cols"),
            generation_method=t.get("generation_method") or "llm_sample_rows",
            generated_at=datetime.utcnow(),
        ))
    for c in col_defs:
        db.add(SchemaColumnMetadata(
            id=uuid.uuid4(),
            connection_id=resolved_conn_id,
            schema_snapshot_version=int(c.get("schema_snapshot_version") or default_version),
            table_name=c.get("table_name") or "",
            column_name=c.get("column_name") or "",
            business_name=c.get("business_name"),
            description=c.get("description"),
            semantic_type=c.get("semantic_type"),
            fk_target_table=c.get("fk_target_table"),
            fk_target_column=c.get("fk_target_column"),
            fk_confirmed=bool(c.get("fk_confirmed")),
            fk_confirmation_score=c.get("fk_confirmation_score"),
            example_values=c.get("example_values"),
            is_kpi_metric=c.get("is_kpi_metric"),
            is_dimension=c.get("is_dimension"),
            is_filter_eligible=c.get("is_filter_eligible"),
            generation_method=c.get("generation_method") or "llm_sample_rows",
            generated_at=datetime.utcnow(),
        ))
    return len(tbl_defs)


async def _restore_schema_warmstart(
    db: AsyncSession,
    resolved_conn_id: uuid.UUID,
    zf: zipfile.ZipFile,
    names: list[str],
) -> bool:
    """
    Restore the embedded schema cache + LLM metadata for a freshly-imported connection
    so both AI surfaces skip the cold build (crawl + LLM enrichment):
      • chat copilot       → the enriched cache (schema_cache.json)
      • intelligence page  → the metadata rows  (schema_metadata.json)

    Returns True if anything useful was restored, False otherwise.

    Three states (the gate is the presence of METADATA ROWS, not just a snapshot —
    a snapshot can exist while metadata extraction failed, leaving intelligence cold):

      1. No snapshot yet                → full warm-start: write snapshot, restore
                                           metadata rows, install enriched cache.
      2. Snapshot exists, no metadata   → backfill ONLY the metadata rows (don't touch
                                           the existing snapshot/cache — they belong to
                                           that connection's own crawl and key under a
                                           different schema_hash).
      3. Snapshot AND metadata present  → already warm → no-op.
    """
    from shared.models.schema_snapshots import SchemaSnapshot
    from shared.models.schema_metadata import SchemaTableMetadata
    from agent_service.agents import schema_cache as _sc

    existing_snap = (await db.execute(
        select(SchemaSnapshot)
        .where(SchemaSnapshot.connection_id == resolved_conn_id)
        .order_by(SchemaSnapshot.version.desc())
        .limit(1)
    )).scalar_one_or_none()

    has_metadata = (await db.execute(
        select(SchemaTableMetadata.id)
        .where(SchemaTableMetadata.connection_id == resolved_conn_id)
        .limit(1)
    )).first() is not None

    # ── State 3: already fully warm ───────────────────────────────────────────
    if existing_snap is not None and has_metadata:
        print(
            f"[import-vly] connection {str(resolved_conn_id)[:8]} already warm "
            f"(snapshot + metadata rows) — skipping",
            flush=True,
        )
        return False

    # ── State 2: snapshot exists but metadata rows are missing → backfill only ─
    if existing_snap is not None and not has_metadata:
        restored = await _restore_metadata_rows(
            db, resolved_conn_id, zf, names, default_version=existing_snap.version
        )
        if restored:
            await db.commit()
            print(
                f"[import-vly] ✓ backfilled {restored} table metadata row(s) for existing "
                f"snapshot  connection={str(resolved_conn_id)[:8]} (intelligence page warm)",
                flush=True,
            )
            return True
        print(
            f"[import-vly] connection {str(resolved_conn_id)[:8]} has a snapshot but no "
            f"embedded metadata to backfill — leaving as-is",
            flush=True,
        )
        return False

    # ── State 1: no snapshot → full warm-start ────────────────────────────────
    if "schema_cache.json" not in names:
        # No baked enriched cache. Without a snapshot the chat copilot can't resolve a
        # schema_doc anyway, so the most we can do is restore the metadata rows that the
        # intelligence page reads directly.
        restored = await _restore_metadata_rows(
            db, resolved_conn_id, zf, names, default_version=1
        )
        if restored:
            await db.commit()
            print(
                f"[import-vly] ✓ restored {restored} table metadata row(s) (no enriched "
                f"cache embedded)  connection={str(resolved_conn_id)[:8]}",
                flush=True,
            )
            return True
        return False

    try:
        cache_doc = json.loads(zf.read("schema_cache.json"))
    except Exception as exc:
        print(f"[import-vly] schema_cache.json unreadable: {exc}", flush=True)
        return False

    if cache_doc.get("cache_format_version") != 1:
        print(
            f"[import-vly] unsupported cache_format_version="
            f"{cache_doc.get('cache_format_version')} — skipping warm-start",
            flush=True,
        )
        return False

    enriched_obj = cache_doc.get("enriched") or {}
    schema_document = enriched_obj.get("schema_doc") or {}
    if not schema_document.get("tables"):
        print("[import-vly] embedded cache has no schema_doc tables — skipping", flush=True)
        return False

    tables = schema_document.get("tables") or []
    table_count = len(tables) if isinstance(tables, (list, dict)) else 0
    snap_version = int(cache_doc.get("snapshot_version") or 1)

    # 1) SchemaSnapshot — so chat's _get_schema_context() finds the schema and
    #    get_or_build() computes a hash that matches the installed cache key.
    snap = SchemaSnapshot(
        id=uuid.uuid4(),
        connection_id=resolved_conn_id,
        version=snap_version,
        schema_document=schema_document,
        table_count=table_count,
        crawl_duration_seconds=None,
        created_at=datetime.utcnow(),
    )
    db.add(snap)

    # 2) Restore LLM metadata rows (Approach C) — warms the intelligence page.
    await _restore_metadata_rows(db, resolved_conn_id, zf, names, default_version=snap_version)

    await db.commit()

    # 3) Install the baked enriched cache across L1 / filesystem / Redis, re-keyed
    #    onto the new connection. Hash derives from schema_document → matches step 1.
    enriched_json = json.dumps(enriched_obj, ensure_ascii=False, default=str)
    schema_hash = await _sc.install_imported_cache(str(resolved_conn_id), enriched_json)
    print(
        f"[import-vly] ✓ warm-started copilot from embedded cache  "
        f"connection={str(resolved_conn_id)[:8]}  hash={schema_hash}  tables={table_count}",
        flush=True,
    )
    return True


# ── POST /dashboards/{id}/bind-connection ─────────────────────────────────────

class BindConnectionRequest(BaseModel):
    connection_id: str
    crawl:   bool = True   # crawl the bound DB's schema so the AI copilot is schema-aware
    refresh: bool = True   # re-run every widget's SQL to replace cached data with live data


@router.post("/dashboards/{dashboard_id}/bind-connection")
async def bind_connection(
    dashboard_id: str,
    body: BindConnectionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Bind a live database connection to a canvas (typically one just imported from a
    .vly with no auto-matched connection). This is what turns a cached snapshot into a
    live, AI-queryable report:

      1. sets connection_id on EVERY widget + on the dashboard layout_config,
      2. (optional) crawls the bound DB's schema so the copilot/query-gen are schema-aware,
      3. (optional) re-runs every widget's SQL to swap cached data for live results.

    Steps 2 and 3 are best-effort — binding always succeeds so the canvas is at least
    connected; a failed crawl/refresh is reported in the response, not fatal.
    """
    dash_result = await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )
    dashboard = dash_result.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    try:
        conn_uuid = uuid.UUID(body.connection_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid connection_id")

    # The connection must belong to the same project as the dashboard.
    conn_result = await db.execute(
        select(DatabaseConnection).where(
            DatabaseConnection.id == conn_uuid,
            DatabaseConnection.project_id == dashboard.project_id,
        )
    )
    conn = conn_result.scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found in this project")

    # 1 — bind every widget + the layout_config
    widget_result = await db.execute(
        select(Widget).where(Widget.dashboard_id == dashboard.id)
    )
    widgets = list(widget_result.scalars().all())
    for w in widgets:
        w.connection_id = conn_uuid
        w.updated_at = datetime.utcnow()

    lc = dict(dashboard.layout_config or {})
    lc["connection_id"] = str(conn_uuid)
    dashboard.layout_config = lc
    dashboard.updated_at = datetime.utcnow()
    await db.commit()

    # 2 & 3 — crawl schema + refresh widgets in the BACKGROUND. These take minutes
    # on a cold backend; running them inline blew past the gateway timeout and the
    # aborted response surfaced in the browser as a (misleading) CORS error. The
    # binding above is already committed, so the canvas is live-bound immediately;
    # schema + data fill in asynchronously (and lazily when the canvas opens).
    if body.crawl or body.refresh:
        background_tasks.add_task(
            _crawl_and_refresh,
            dashboard_id, str(conn_uuid), str(dashboard.project_id),
            body.crawl, body.refresh,
        )

    print(
        f"[bind-connection] dashboard={dashboard_id[:8]}  connection={str(conn_uuid)[:8]}  "
        f"widgets={len(widgets)}  crawl={body.crawl}  refresh={body.refresh}  (background)",
        flush=True,
    )

    return {
        "status":          "bound",
        "dashboard_id":    dashboard_id,
        "connection_id":   str(conn_uuid),
        "widgets_bound":   len(widgets),
        "crawl_triggered": body.crawl,
        "refreshed":       False,
        "refresh_status":  "in_progress" if body.refresh else "skipped",
    }
