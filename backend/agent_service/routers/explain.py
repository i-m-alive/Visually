"""Explain-this-point: right-click a data point → "why is this value what it is?"

Deterministic decomposition (no LLM-written SQL): the widget's own query is
wrapped as a subquery and re-aggregated by other candidate dimensions, filtered
to the clicked value, plus an unfiltered baseline for share-of-total. The LLM
only writes the narrative from the numbers.
"""
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget as WidgetModel
from shared.models.users import User
from shared.security import decode_token
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL
from agent_service.utils.http_clients import call_query_executor

router = APIRouter(tags=["explain"])

bearer_scheme = HTTPBearer(auto_error=False)
DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")


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


class ExplainRequest(BaseModel):
    widget_id: str
    column: str
    value: str
    metric_column: Optional[str] = None


def _strip_order_limit(sql: str) -> str:
    """Remove trailing ORDER BY / LIMIT so the query can be wrapped as a subquery."""
    s = sql.rstrip().rstrip(";")
    s = re.sub(r"\s+LIMIT\s+\d+\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+ORDER\s+BY\s+[^)]*$", "", s, flags=re.IGNORECASE)
    return s


def _safe_ident(name: str) -> str:
    return re.sub(r"[^\w ]", "", name or "")


@router.post("/dashboards/{dashboard_id}/explain-point")
async def explain_point(
    dashboard_id: str,
    body: ExplainRequest,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    wr = await db.execute(select(WidgetModel).where(WidgetModel.id == uuid.UUID(body.widget_id)))
    widget = wr.scalar_one_or_none()
    if not widget or str(widget.dashboard_id) != dashboard_id:
        raise HTTPException(status_code=404, detail="Widget not found")
    base_sql = widget.base_sql or widget.sql_query
    if not base_sql:
        raise HTTPException(status_code=400, detail="Widget has no SQL to decompose")

    conn_id = str(widget.connection_id) if widget.connection_id else None
    if not conn_id:
        dr = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
        dash = dr.scalar_one_or_none()
        lc = (dash.layout_config or {}) if dash else {}
        conn_id = str(lc["connection_id"]) if lc.get("connection_id") else None
        if not conn_id and dash:
            from shared.models.database_connections import DatabaseConnection
            pc = await db.execute(select(DatabaseConnection).where(
                DatabaseConnection.project_id == dash.project_id,
                DatabaseConnection.is_active == True).limit(1))  # noqa: E712
            pcobj = pc.scalar_one_or_none()
            conn_id = str(pcobj.id) if pcobj else None
    if not conn_id:
        raise HTTPException(status_code=400, detail="No database connection available")

    inner = _strip_order_limit(base_sql)
    col = _safe_ident(body.column)
    val = str(body.value).replace("'", "''")

    # Candidate dimensions = other string-ish columns in the widget's result
    cd = widget.chart_data if isinstance(widget.chart_data, dict) else {}
    rows = cd.get("rows") or []
    all_cols = cd.get("columns") or (list(rows[0].keys()) if rows else [])
    sample = rows[0] if rows else {}
    metric_col = _safe_ident(body.metric_column) if body.metric_column else next(
        (c for c in all_cols if isinstance(sample.get(c), (int, float)) and c != col), None)
    dims = [c for c in all_cols
            if c != col and c != metric_col and not isinstance(sample.get(c), (int, float))][:3]

    agg = f'SUM("{metric_col}")' if metric_col else "COUNT(*)"
    breakdowns: list = []
    sql_used: list = []

    # Baseline: total for the clicked slice + overall total
    slice_sql = f'SELECT {agg} AS total FROM ({inner}) t WHERE t."{col}" = \'{val}\''
    total_sql = f"SELECT {agg} AS total FROM ({inner}) t"
    slice_total = overall_total = None
    for label, q in (("slice", slice_sql), ("overall", total_sql)):
        sql_used.append(q)
        try:
            res = await call_query_executor(conn_id, q, row_limit=5)
            r = (res.get("rows") or [{}])[0]
            v = list(r.values())[0] if r else None
            if label == "slice":
                slice_total = v
            else:
                overall_total = v
        except Exception as exc:
            print(f"[explain] baseline query failed: {exc}", flush=True)

    # Decompose the slice by each candidate dimension
    for d in dims:
        ds = _safe_ident(d)
        q = (f'SELECT t."{ds}" AS segment, {agg} AS value FROM ({inner}) t '
             f'WHERE t."{col}" = \'{val}\' GROUP BY 1 ORDER BY 2 DESC LIMIT 8')
        sql_used.append(q)
        try:
            res = await call_query_executor(conn_id, q, row_limit=20)
            if not res.get("error") and res.get("rows"):
                breakdowns.append({"dimension": d, "rows": res["rows"]})
        except Exception as exc:
            print(f"[explain] breakdown by {d} failed: {exc}", flush=True)

    # Narrative from the numbers (LLM writes prose, never SQL)
    evidence = {
        "clicked": {body.column: body.value},
        "slice_total": slice_total,
        "overall_total": overall_total,
        "share_of_total": (round(slice_total / overall_total * 100, 1)
                           if isinstance(slice_total, (int, float)) and isinstance(overall_total, (int, float)) and overall_total
                           else None),
        "breakdowns": breakdowns,
    }
    explanation = ""
    try:
        import json as _json
        explanation = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=("You explain a single data point on a chart in 2-4 plain sentences. "
                           "Lead with the biggest driver, cite concrete numbers and shares. No preamble."),
            user_message=(f"Widget: '{widget.title}'. The user clicked {body.column} = {body.value} "
                          f"and asked why. Evidence: {_json.dumps(evidence, default=str)[:4000]}"),
            max_tokens=400,
            temperature=0.2,
        )
    except Exception as exc:
        explanation = f"Analysis complete (narrative unavailable: {exc})."

    return {
        "widget_id": body.widget_id,
        "column": body.column,
        "value": body.value,
        "explanation": explanation.strip(),
        "evidence": evidence,
        "sql_used": sql_used,
    }
