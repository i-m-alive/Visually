"""
Analyst endpoints — authenticated by share token, no Bearer required.
All data access is proxied through the server's stored DB connection.
"""
import csv
import io
import hashlib
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.redis_client import get_redis
from shared.models.dashboards import Dashboard
from shared.models.sharing import CanvasShareToken
from shared.models.widgets import Widget
from shared.models.database_connections import DatabaseConnection
from shared.models.schema_snapshots import SchemaSnapshot
from shared.models.annotations import DashboardAnnotation
from shared.models.bookmarks import DashboardBookmark
from shared.models.snapshot_schedules import SnapshotSchedule
from agent_service.utils.http_clients import call_query_executor, call_schema_crawler

router = APIRouter(tags=["analyst"])

# ── Schema freshness thresholds ───────────────────────────────────────────────
_SCHEMA_LIVE_MIN   =  10   # < 10 min  → "live"
_SCHEMA_RECENT_MIN =  60   # < 60 min  → "recent"
                           # ≥ 60 min  → "cached"  (triggers background refresh)


async def _get_schema_context(
    dashboard,
    widgets: list,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
) -> tuple[dict, str, Optional[float], Optional[str], str]:
    """
    Returns (schema_doc, schema_source, snapshot_age_min, conn_id_str, db_type).

    schema_source:
      "live"     — snapshot < _SCHEMA_LIVE_MIN min old
      "recent"   — snapshot _SCHEMA_LIVE_MIN–_SCHEMA_RECENT_MIN min old
      "cached"   — snapshot ≥ _SCHEMA_RECENT_MIN min old (background refresh triggered)
      "embedded" — from .vly import, no live snapshot available
      "none"     — no schema at all
    """
    conn = await _get_connection_for_dashboard(dashboard, db)
    conn_id_str: Optional[str] = str(conn.id) if conn else None
    db_type = "redshift"
    schema_doc: dict = {}
    schema_source = "none"
    snapshot_age_min: Optional[float] = None

    if conn:
        db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
        snap_result = await db.execute(
            select(SchemaSnapshot).where(SchemaSnapshot.connection_id == conn.id)
            .order_by(SchemaSnapshot.version.desc()).limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap and snap.schema_document:
            schema_doc = snap.schema_document
            age = (datetime.utcnow() - snap.created_at).total_seconds() / 60
            snapshot_age_min = round(age, 1)
            if age < _SCHEMA_LIVE_MIN:
                schema_source = "live"
            elif age < _SCHEMA_RECENT_MIN:
                schema_source = "recent"
            else:
                schema_source = "cached"
                background_tasks.add_task(
                    call_schema_crawler, conn_id_str, str(dashboard.project_id)
                )

    # Fallback: embedded schema from .vly import
    if not schema_doc:
        embedded = (dashboard.layout_config or {}).get("embedded_schema", {})
        if embedded:
            schema_doc = embedded
            schema_source = "embedded"
            if conn_id_str:
                # Has a connection but no snapshot yet — kick off first crawl
                background_tasks.add_task(
                    call_schema_crawler, conn_id_str, str(dashboard.project_id)
                )

    return schema_doc, schema_source, snapshot_age_min, conn_id_str, db_type


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _resolve_token(raw_token: str, db: AsyncSession) -> CanvasShareToken:
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
    return token_obj


async def _get_canvas_and_token(raw_token: str, db: AsyncSession):
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
    return dashboard, token_obj, widgets


async def _get_connection_for_dashboard(dashboard: Dashboard, db: AsyncSession) -> Optional[DatabaseConnection]:
    conn_result = await db.execute(
        select(DatabaseConnection)
        .where(DatabaseConnection.project_id == dashboard.project_id)
        .where(DatabaseConnection.is_active == True)
        .limit(1)
    )
    return conn_result.scalar_one_or_none()


def _build_filter_clause(filters: list) -> str:
    clauses = []
    for f in filters:
        col = f.get("column", "") if isinstance(f, dict) else getattr(f, "column", "")
        op = (f.get("operator", "=") if isinstance(f, dict) else getattr(f, "operator", "=")).lower()
        val = f.get("value", "") if isinstance(f, dict) else getattr(f, "value", "")
        if not col or not re.match(r'^[\w.]+$', col):
            continue
        op_map = {"=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<=",
                  "like": "ILIKE", "ilike": "ILIKE", "in": "IN", "between": "BETWEEN"}
        safe_op = op_map.get(op, "=")
        if safe_op == "IN":
            if isinstance(val, (list, tuple)):
                vals = ", ".join(f"'{str(v).replace(chr(39), chr(39)*2)}'" for v in val)
                clauses.append(f"{col} IN ({vals})")
        elif safe_op == "BETWEEN":
            if isinstance(val, (list, tuple)) and len(val) == 2:
                lo = str(val[0]).replace("'", "''")
                hi = str(val[1]).replace("'", "''")
                clauses.append(f"{col} BETWEEN '{lo}' AND '{hi}'")
        elif isinstance(val, str):
            clauses.append(f"{col} {safe_op} '{val.replace(chr(39), chr(39)*2)}'")
        elif val is None:
            clauses.append(f"{col} IS NULL")
        else:
            clauses.append(f"{col} {safe_op} {val}")
    return " AND ".join(clauses)


def _apply_filters_to_sql(base_sql: str, filter_clause: str) -> str:
    if not filter_clause:
        return base_sql
    return f"SELECT * FROM ({base_sql}) AS _filtered WHERE {filter_clause}"


def _extract_tables_from_sql(sql: str) -> set:
    if not sql:
        return set()
    tables = set()
    for t in re.findall(r'\bFROM\s+([\w.]+)', sql, re.IGNORECASE) + re.findall(r'\bJOIN\s+([\w.]+)', sql, re.IGNORECASE):
        tables.add(t.lower())
        tables.add(t.split('.')[-1].lower())
    return tables


def _get_allowed_tables(widgets: list) -> set:
    tables = set()
    for w in widgets:
        tables.update(_extract_tables_from_sql(w.base_sql or w.sql_query or ""))
    return tables


def _extract_schema_names(raw_schema: dict) -> set:
    """Extract all table/view names from schema_doc — handles both list and dict formats."""
    names: set = set()
    if not isinstance(raw_schema, dict):
        return names
    tables = raw_schema.get("tables", [])
    if isinstance(tables, list):
        for entry in tables:
            if isinstance(entry, dict):
                name = (entry.get("name") or "").lower()
                schema = (entry.get("schema") or "").lower()
                if name:
                    names.add(name)
                    if schema:
                        names.add(f"{schema}.{name}")
    elif isinstance(tables, dict):
        for k in tables:
            names.add(k.lower())
            names.add(k.split(".")[-1].lower())
    return names


class FilterItem(BaseModel):
    column: str
    operator: str = "="
    value: Any = None


class WidgetDataRequest(BaseModel):
    filters: List[FilterItem] = []


class AnalystChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class AdHocQueryRequest(BaseModel):
    sql: str


class DrilldownRequest(BaseModel):
    x_column: str
    x_value: str
    filters: List[FilterItem] = []


class AnnotationCreate(BaseModel):
    widget_id: Optional[str] = None
    content: str
    author_name: Optional[str] = "Anonymous"
    x_percent: Optional[float] = None
    y_percent: Optional[float] = None
    color: str = "#3B82F6"


class BookmarkCreate(BaseModel):
    name: str
    description: Optional[str] = None
    filter_state: dict = {}
    page_index: int = 0


class ScheduleCreate(BaseModel):
    email: str
    frequency: str = "daily"
    day_of_week: Optional[int] = None
    hour_utc: int = 8
    timezone: str = "UTC"
    include_ai_summary: bool = True


# ── 1. Live widget data ───────────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/widgets/{widget_id}/data")
async def get_widget_data_live(
    raw_token: str, widget_id: str, req: WidgetDataRequest, db: AsyncSession = Depends(get_db),
):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    widget = next((w for w in widgets if str(w.id) == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    base_sql = widget.base_sql or widget.sql_query
    if not base_sql:
        return {"rows": [], "columns": [], "row_count": 0, "widget_id": widget_id}
    conn_id = str(widget.connection_id) if widget.connection_id else None
    if not conn_id:
        conn = await _get_connection_for_dashboard(dashboard, db)
        if conn:
            conn_id = str(conn.id)
    if not conn_id:
        raise HTTPException(status_code=400, detail="No database connection configured")
    filter_clause = _build_filter_clause([f.dict() for f in req.filters])
    sql = _apply_filters_to_sql(base_sql, filter_clause)
    result = await call_query_executor(conn_id, sql, row_limit=10000)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {
        "widget_id": widget_id,
        "rows": result.get("rows", []),
        "columns": result.get("columns", []),
        "row_count": result.get("row_count", 0),
        "duration_ms": result.get("duration_ms"),
    }


# ── 1b. Slicer distinct values ───────────────────────────────────────────────

@router.get("/analyst/canvas/{raw_token}/widgets/{widget_id}/slicer-values")
async def get_slicer_values(
    raw_token: str, widget_id: str, db: AsyncSession = Depends(get_db),
):
    """Return the distinct values for a slicer widget so the frontend can populate its dropdown/checkbox list."""
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    widget = next((w for w in widgets if str(w.id) == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    conn_id = str(widget.connection_id) if widget.connection_id else None
    if not conn_id:
        conn = await _get_connection_for_dashboard(dashboard, db)
        if conn:
            conn_id = str(conn.id)
    if not conn_id:
        return {"values": []}

    # If the widget has a sql_query use it directly (already a DISTINCT query);
    # otherwise synthesise one from the slicer_column + slicer_table config.
    base_sql = widget.sql_query or widget.base_sql
    if not base_sql:
        cfg = widget.config or {}
        slicer_col   = cfg.get("slicer_column", "")
        slicer_table = cfg.get("slicer_table", "")
        if not slicer_col or not slicer_table:
            return {"values": []}
        # Validate identifiers (letters, digits, underscores, dots, schema-qualified)
        import re as _re
        if not _re.match(r'^[\w.]+$', slicer_col) or not _re.match(r'^[\w.]+$', slicer_table):
            return {"values": []}
        base_sql = (
            f"SELECT DISTINCT {slicer_col} AS _val "
            f"FROM {slicer_table} "
            f"WHERE {slicer_col} IS NOT NULL AND CAST({slicer_col} AS TEXT) != '' "
            f"ORDER BY 1 LIMIT 300"
        )

    result = await call_query_executor(conn_id, base_sql, row_limit=300)
    if result.get("error"):
        return {"values": [], "error": result["error"]}

    rows = result.get("rows", [])
    columns = result.get("columns", [])
    if not rows or not columns:
        return {"values": []}

    values = [str(r.get(columns[0], "")) for r in rows if r.get(columns[0]) is not None]
    return {"values": values}


# ── 2. Scoped AI chat ─────────────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/chat")
async def analyst_chat(
    raw_token: str,
    req: AnalystChatRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    # Report Copilot, share-token context: this surface is the share-link
    # rendering of the intelligence-page copilot, so it runs on the forked
    # IntelligenceChatAgent (NOT the canvas ChatAgent). Conversations live in
    # the intel_chat:history: Redis namespace and log under [intel_chat].
    from agent_service.agents.intelligence_chat_agent import IntelligenceChatAgent
    import agent_service.agents.schema_cache as _schema_cache

    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    session_id = req.session_id or str(uuid.uuid4())
    _agent = IntelligenceChatAgent()
    history = await IntelligenceChatAgent.load_history(session_id, redis)
    print(
        f"[intel_chat] ▶ analyst(share-token) turn  session={session_id[:8]}  "
        f"widgets={len(widgets)}  msg_len={len(req.message)}",
        flush=True,
    )
    allowed_tables = _get_allowed_tables(widgets)

    # ── Warm-start schema (Option C hybrid) ───────────────────────────────────
    # Uses cached/embedded schema for the first response (fast), while optionally
    # triggering a background schema refresh when the snapshot is stale.
    raw_schema, schema_source, snapshot_age_min, conn_id_str, db_type = \
        await _get_schema_context(dashboard, widgets, db, background_tasks)

    # Expand allowed_tables: any table/view in the crawled schema is accessible —
    # not just tables that appear in existing widget SQL. This lets the AI query
    # views like customer_billable_hours_yoy_view even if no widget uses them yet.
    allowed_tables = allowed_tables | _extract_schema_names(raw_schema)

    schema_doc = raw_schema if isinstance(raw_schema, dict) else {}

    enriched = None
    if schema_doc and conn_id_str:
        try:
            enriched = await _schema_cache.get_or_build(conn_id_str, schema_doc, db_type)
        except Exception as e:
            print(f"[analyst-chat] schema enrichment failed (non-fatal): {e}", flush=True)

    dashboard_widgets = [
        {"id": str(w.id), "title": w.title, "chart_type": w.chart_type,
         "sql_query": w.sql_query, "chart_data": w.chart_data,
         "page_id": (w.config or {}).get("page_id")}
        for w in widgets
    ]

    result = await _agent.respond(
        message=req.message,
        conversation_history=history,
        schema_doc=schema_doc,
        dashboard_widgets=dashboard_widgets,
        dashboard_pages=(dashboard.layout_config or {}).get("pages", []),
        enriched_schema=enriched,
    )

    async def _exec_spec(spec: dict) -> dict | None:
        sql = spec.get("sql", "")
        if not sql or not conn_id_str:
            return None
        used = _extract_tables_from_sql(sql)
        if allowed_tables and not used.issubset(allowed_tables):
            return None
        exec_result = await call_query_executor(conn_id_str, sql, row_limit=1000)
        if exec_result.get("error") or not exec_result.get("rows"):
            return None
        rows = exec_result.get("rows", [])
        cols = exec_result.get("columns", [])
        labels = [str(r.get(cols[0], "")) for r in rows] if cols else []
        values = [r.get(cols[1]) for r in rows] if len(cols) > 1 else [r.get(cols[0]) for r in rows]
        cfg: dict = {
            "chart_type": spec.get("chart_type", "table"),
            "title": spec.get("title", "Chart"),
            "x_axis_label": spec.get("x_label", cols[0] if cols else "x"),
            "y_axis_label": spec.get("y_label", cols[1] if len(cols) > 1 else "y"),
            "chart_data": {"rows": rows, "columns": cols, "labels": labels, "values": values},
            "sql": sql,
        }
        if spec.get("slicer_type"):
            cfg["slicer_type"] = spec["slicer_type"]
        if spec.get("slicer_column"):
            cfg["slicer_column"] = spec["slicer_column"]
        return cfg

    specs = result.get("sqls_to_execute") or ([result["sql_to_execute"]] if result.get("sql_to_execute") else [])
    inline_charts: list[dict] = []
    for spec in specs:
        chart = await _exec_spec(spec)
        if chart:
            inline_charts.append(chart)

    inline_chart = inline_charts[0] if inline_charts else None

    updated_history = history + [{"role": "user", "content": req.message}, {"role": "assistant", "content": result["text"]}]
    await IntelligenceChatAgent.save_history(session_id, updated_history, redis)
    print(
        f"[intel_chat] ✔ analyst(share-token) complete  session={session_id[:8]}  "
        f"charts={len(inline_charts)}  turns={len(updated_history) // 2}",
        flush=True,
    )

    return {
        "session_id": session_id,
        "text": result["text"],
        "inline_chart": inline_chart,
        "inline_charts": inline_charts,
        "turn_count": len(updated_history) // 2,
        "schema_source": schema_source,
        "schema_age_minutes": snapshot_age_min,
    }


# ── 3. Schema browser ─────────────────────────────────────────────────────────

@router.get("/analyst/canvas/{raw_token}/schema")
async def get_canvas_schema(raw_token: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    conn = await _get_connection_for_dashboard(dashboard, db)
    schema_doc: dict = {}
    if conn:
        snap_result = await db.execute(
            select(SchemaSnapshot).where(SchemaSnapshot.connection_id == conn.id)
            .order_by(SchemaSnapshot.version.desc()).limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap:
            schema_doc = snap.schema_document or {}

    tables = []
    raw_tables = schema_doc.get("tables", []) if isinstance(schema_doc, dict) else []
    if isinstance(raw_tables, list):
        for entry in raw_tables:
            if not isinstance(entry, dict):
                continue
            tname = entry.get("name", "")
            columns = [
                {
                    "name": c.get("name", ""),
                    "type": c.get("type", "unknown"),
                    "nullable": c.get("is_nullable", True),
                    "sample_values": [],
                }
                for c in entry.get("columns", [])
                if isinstance(c, dict)
            ]
            tables.append({"name": tname, "columns": columns, "column_count": len(columns)})
    elif isinstance(raw_tables, dict):
        for table_name, table_info in raw_tables.items():
            columns = []
            if isinstance(table_info, dict):
                for col_name, col_info in table_info.get("columns", {}).items():
                    columns.append({
                        "name": col_name,
                        "type": col_info.get("type", "unknown") if isinstance(col_info, dict) else str(col_info),
                        "nullable": col_info.get("nullable", True) if isinstance(col_info, dict) else True,
                        "sample_values": col_info.get("sample_values", []) if isinstance(col_info, dict) else [],
                    })
            tables.append({"name": table_name, "columns": columns, "column_count": len(columns)})

    return {"tables": tables, "total": len(tables)}


# ── 4. Table preview ──────────────────────────────────────────────────────────

@router.get("/analyst/canvas/{raw_token}/schema/{table_name}/preview")
async def preview_table(
    raw_token: str, table_name: str, limit: int = Query(default=100, le=500), db: AsyncSession = Depends(get_db),
):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    if not re.match(r'^[\w.]+$', table_name):
        raise HTTPException(status_code=400, detail="Invalid table name")
    conn = await _get_connection_for_dashboard(dashboard, db)
    if not conn:
        raise HTTPException(status_code=400, detail="No database connection configured")
    result = await call_query_executor(str(conn.id), f"SELECT * FROM {table_name} LIMIT {limit}", row_limit=limit)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"table": table_name, "rows": result.get("rows", []), "columns": result.get("columns", []), "row_count": result.get("row_count", 0)}


# ── 5. Ad-hoc sandboxed query ─────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/query")
async def sandbox_query(raw_token: str, req: AdHocQueryRequest, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    conn = await _get_connection_for_dashboard(dashboard, db)
    allowed_tables = _get_allowed_tables(widgets)
    if conn:
        snap_result = await db.execute(
            select(SchemaSnapshot).where(SchemaSnapshot.connection_id == conn.id)
            .order_by(SchemaSnapshot.version.desc()).limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap and snap.schema_document:
            allowed_tables = allowed_tables | _extract_schema_names(snap.schema_document)
    stripped = req.sql.strip().upper().lstrip("(")
    if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
        raise HTTPException(status_code=400, detail="Only SELECT / WITH queries are allowed")
    used = _extract_tables_from_sql(req.sql)
    if allowed_tables:
        disallowed = used - allowed_tables
        if disallowed:
            raise HTTPException(status_code=403, detail=f"Query references tables not in this canvas: {', '.join(sorted(disallowed))}")
    if not conn:
        raise HTTPException(status_code=400, detail="No database connection configured")
    result = await call_query_executor(str(conn.id), req.sql, row_limit=5000)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"rows": result.get("rows", []), "columns": result.get("columns", []), "row_count": result.get("row_count", 0), "duration_ms": result.get("duration_ms")}


# ── 6. Drill-down ─────────────────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/widgets/{widget_id}/drilldown")
async def widget_drilldown(raw_token: str, widget_id: str, req: DrilldownRequest, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    widget = next((w for w in widgets if str(w.id) == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    base_sql = widget.base_sql or widget.sql_query
    if not base_sql:
        return {"rows": [], "columns": [], "widget_id": widget_id}
    conn_id = str(widget.connection_id) if widget.connection_id else None
    if not conn_id:
        conn = await _get_connection_for_dashboard(dashboard, db)
        if conn:
            conn_id = str(conn.id)
    if not conn_id:
        raise HTTPException(status_code=400, detail="No database connection configured")
    all_filters = [{"column": req.x_column, "operator": "=", "value": req.x_value}] + [f.dict() for f in req.filters]
    filter_clause = _build_filter_clause(all_filters)
    drill_sql = f"SELECT * FROM ({base_sql}) AS _drilled WHERE {filter_clause} LIMIT 1000"
    result = await call_query_executor(conn_id, drill_sql, row_limit=1000)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return {"widget_id": widget_id, "drill_column": req.x_column, "drill_value": req.x_value, "rows": result.get("rows", []), "columns": result.get("columns", []), "row_count": result.get("row_count", 0)}


# ── 7. CSV export ─────────────────────────────────────────────────────────────

@router.get("/analyst/canvas/{raw_token}/widgets/{widget_id}/export/csv")
async def export_widget_csv(raw_token: str, widget_id: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, widgets = await _get_canvas_and_token(raw_token, db)
    widget = next((w for w in widgets if str(w.id) == widget_id), None)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    rows: list = []
    columns: list = []
    base_sql = widget.base_sql or widget.sql_query
    if base_sql:
        conn_id = str(widget.connection_id) if widget.connection_id else None
        if not conn_id:
            conn = await _get_connection_for_dashboard(dashboard, db)
            if conn:
                conn_id = str(conn.id)
        if conn_id:
            result = await call_query_executor(conn_id, base_sql, row_limit=50000)
            if not result.get("error"):
                rows = result.get("rows", [])
                columns = result.get("columns", [])
    if not rows and widget.chart_data:
        rows = widget.chart_data.get("rows", [])
        columns = widget.chart_data.get("columns", [])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    filename = f"{(widget.title or 'export').replace(' ', '_')}.csv"
    return StreamingResponse(io.StringIO(output.getvalue()), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ── 8. Annotations ────────────────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/annotations", status_code=201)
async def create_annotation(raw_token: str, req: AnnotationCreate, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    annotation = DashboardAnnotation(
        id=uuid.uuid4(), dashboard_id=dashboard.id,
        widget_id=uuid.UUID(req.widget_id) if req.widget_id else None,
        created_by=_hash(raw_token), author_name=req.author_name or "Anonymous",
        content=req.content, x_percent=req.x_percent, y_percent=req.y_percent, color=req.color,
    )
    db.add(annotation)
    await db.commit()
    await db.refresh(annotation)
    return {"id": str(annotation.id), "widget_id": req.widget_id, "content": annotation.content,
            "author_name": annotation.author_name, "color": annotation.color,
            "x_percent": annotation.x_percent, "y_percent": annotation.y_percent,
            "is_resolved": annotation.is_resolved, "created_at": annotation.created_at.isoformat()}


@router.get("/analyst/canvas/{raw_token}/annotations")
async def list_annotations(raw_token: str, widget_id: Optional[str] = Query(default=None), db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    q = select(DashboardAnnotation).where(DashboardAnnotation.dashboard_id == dashboard.id, DashboardAnnotation.is_resolved == False)
    if widget_id:
        q = q.where(DashboardAnnotation.widget_id == uuid.UUID(widget_id))
    result = await db.execute(q.order_by(DashboardAnnotation.created_at.desc()))
    return {"annotations": [{"id": str(a.id), "widget_id": str(a.widget_id) if a.widget_id else None,
                              "content": a.content, "author_name": a.author_name, "color": a.color,
                              "x_percent": a.x_percent, "y_percent": a.y_percent, "is_resolved": a.is_resolved,
                              "created_at": a.created_at.isoformat()} for a in result.scalars().all()]}


@router.patch("/analyst/canvas/{raw_token}/annotations/{annotation_id}/resolve")
async def resolve_annotation(raw_token: str, annotation_id: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(DashboardAnnotation).where(DashboardAnnotation.id == uuid.UUID(annotation_id), DashboardAnnotation.dashboard_id == dashboard.id))
    annotation = result.scalar_one_or_none()
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found")
    annotation.is_resolved = True
    annotation.updated_at = datetime.utcnow()
    await db.commit()
    return {"resolved": annotation_id}


@router.delete("/analyst/canvas/{raw_token}/annotations/{annotation_id}")
async def delete_annotation(raw_token: str, annotation_id: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(DashboardAnnotation).where(DashboardAnnotation.id == uuid.UUID(annotation_id), DashboardAnnotation.dashboard_id == dashboard.id, DashboardAnnotation.created_by == _hash(raw_token)))
    annotation = result.scalar_one_or_none()
    if not annotation:
        raise HTTPException(status_code=404, detail="Annotation not found or not yours")
    await db.delete(annotation)
    await db.commit()
    return {"deleted": annotation_id}


# ── 9. Bookmarks ─────────────────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/bookmarks", status_code=201)
async def create_bookmark(raw_token: str, req: BookmarkCreate, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    bookmark = DashboardBookmark(id=uuid.uuid4(), dashboard_id=dashboard.id, created_by=_hash(raw_token),
                                  name=req.name, description=req.description, filter_state=req.filter_state, page_index=req.page_index)
    db.add(bookmark)
    await db.commit()
    await db.refresh(bookmark)
    return {"id": str(bookmark.id), "name": bookmark.name, "description": bookmark.description,
            "filter_state": bookmark.filter_state, "page_index": bookmark.page_index, "created_at": bookmark.created_at.isoformat()}


@router.get("/analyst/canvas/{raw_token}/bookmarks")
async def list_bookmarks(raw_token: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(DashboardBookmark).where(DashboardBookmark.dashboard_id == dashboard.id, DashboardBookmark.created_by == _hash(raw_token)).order_by(DashboardBookmark.created_at.desc()))
    return {"bookmarks": [{"id": str(b.id), "name": b.name, "description": b.description,
                           "filter_state": b.filter_state, "page_index": b.page_index, "created_at": b.created_at.isoformat()} for b in result.scalars().all()]}


@router.delete("/analyst/canvas/{raw_token}/bookmarks/{bookmark_id}")
async def delete_bookmark(raw_token: str, bookmark_id: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(DashboardBookmark).where(DashboardBookmark.id == uuid.UUID(bookmark_id), DashboardBookmark.dashboard_id == dashboard.id, DashboardBookmark.created_by == _hash(raw_token)))
    bookmark = result.scalar_one_or_none()
    if not bookmark:
        raise HTTPException(status_code=404, detail="Bookmark not found")
    await db.delete(bookmark)
    await db.commit()
    return {"deleted": bookmark_id}


# ── 10. Scheduled snapshots ───────────────────────────────────────────────────

@router.post("/analyst/canvas/{raw_token}/schedules", status_code=201)
async def create_schedule(raw_token: str, req: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    now = datetime.utcnow()
    if req.frequency == "daily":
        next_send = now.replace(hour=req.hour_utc, minute=0, second=0, microsecond=0)
        if next_send <= now:
            next_send += timedelta(days=1)
    elif req.frequency == "weekly":
        days_ahead = (req.day_of_week or 0) - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_send = (now + timedelta(days=days_ahead)).replace(hour=req.hour_utc, minute=0, second=0, microsecond=0)
    else:
        import calendar
        _, last_day = calendar.monthrange(now.year, now.month)
        next_send = (now.replace(day=last_day) + timedelta(days=2)).replace(day=1, hour=req.hour_utc, minute=0, second=0, microsecond=0)
    schedule = SnapshotSchedule(id=uuid.uuid4(), dashboard_id=dashboard.id, share_token_id=token_obj.id,
                                 created_by=req.email, email=req.email, frequency=req.frequency,
                                 day_of_week=req.day_of_week, hour_utc=req.hour_utc, timezone=req.timezone,
                                 include_ai_summary=req.include_ai_summary, next_send_at=next_send)
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    return {"id": str(schedule.id), "email": schedule.email, "frequency": schedule.frequency,
            "next_send_at": schedule.next_send_at.isoformat() if schedule.next_send_at else None,
            "is_active": schedule.is_active, "created_at": schedule.created_at.isoformat()}


@router.get("/analyst/canvas/{raw_token}/schedules")
async def list_schedules(raw_token: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(SnapshotSchedule).where(SnapshotSchedule.dashboard_id == dashboard.id, SnapshotSchedule.share_token_id == token_obj.id).order_by(SnapshotSchedule.created_at.desc()))
    return {"schedules": [{"id": str(s.id), "email": s.email, "frequency": s.frequency, "day_of_week": s.day_of_week,
                           "hour_utc": s.hour_utc, "timezone": s.timezone, "include_ai_summary": s.include_ai_summary,
                           "is_active": s.is_active, "last_sent_at": s.last_sent_at.isoformat() if s.last_sent_at else None,
                           "next_send_at": s.next_send_at.isoformat() if s.next_send_at else None,
                           "created_at": s.created_at.isoformat()} for s in result.scalars().all()]}


@router.delete("/analyst/canvas/{raw_token}/schedules/{schedule_id}")
async def delete_schedule(raw_token: str, schedule_id: str, db: AsyncSession = Depends(get_db)):
    dashboard, token_obj, _ = await _get_canvas_and_token(raw_token, db)
    result = await db.execute(select(SnapshotSchedule).where(SnapshotSchedule.id == uuid.UUID(schedule_id), SnapshotSchedule.dashboard_id == dashboard.id, SnapshotSchedule.share_token_id == token_obj.id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await db.delete(schedule)
    await db.commit()
    return {"deleted": schedule_id}
