"""Tier-5 Power BI parity endpoints.

Features:
- Row-Level Security policies (CRUD)
- Scheduled refresh (cron schedule stored in layout_config.refresh_schedule)
- Manual refresh-now
- Calculated measures (stored in layout_config.measures)
- Drilldown SQL generation (AI-assisted)
"""
import asyncio
import os
import re as _re
import uuid
from datetime import datetime
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
from shared.models.tier5 import RLSPolicy
from shared.security import decode_token
from agent_service.utils.http_clients import call_query_executor

router = APIRouter(tags=["tier5"])

bearer_scheme = HTTPBearer(auto_error=False)
DEV_MODE = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")


# ─── Auth helper ──────────────────────────────────────────────────────────────

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


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _inject_rls(sql: str, clauses: list[str]) -> str:
    if not clauses:
        return sql
    sql = sql.rstrip(";")
    combined = " AND ".join(f"({c})" for c in clauses)
    has_where = bool(_re.search(r"\bWHERE\b", sql, _re.IGNORECASE))
    connector = " AND " if has_where else " WHERE "
    return sql + connector + combined


async def _get_dashboard(dashboard_id: str, db: AsyncSession) -> Dashboard:
    result = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
    dash = result.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dash


# ═══════════════════════════════════════════════════════════════════════════════
# RLS POLICIES
# ═══════════════════════════════════════════════════════════════════════════════

class RLSPolicyCreate(BaseModel):
    name: str
    clause: str
    user_id: Optional[str] = None  # None = catch-all
    is_active: bool = True


class RLSPolicyUpdate(BaseModel):
    name: Optional[str] = None
    clause: Optional[str] = None
    user_id: Optional[str] = None
    is_active: Optional[bool] = None


def _policy_dict(p: RLSPolicy) -> dict:
    return {
        "id": str(p.id),
        "dashboard_id": str(p.dashboard_id),
        "user_id": str(p.user_id) if p.user_id else None,
        "name": p.name,
        "clause": p.clause,
        "is_active": p.is_active,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


@router.get("/dashboards/{dashboard_id}/rls-policies")
async def list_rls_policies(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RLSPolicy).where(RLSPolicy.dashboard_id == uuid.UUID(dashboard_id))
        .order_by(RLSPolicy.created_at.desc())
    )
    return {"policies": [_policy_dict(p) for p in result.scalars().all()]}


@router.post("/dashboards/{dashboard_id}/rls-policies", status_code=201)
async def create_rls_policy(
    dashboard_id: str,
    body: RLSPolicyCreate,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_dashboard(dashboard_id, db)
    policy = RLSPolicy(
        id=uuid.uuid4(),
        dashboard_id=uuid.UUID(dashboard_id),
        user_id=uuid.UUID(body.user_id) if body.user_id else None,
        name=body.name,
        clause=body.clause,
        is_active=body.is_active,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return _policy_dict(policy)


@router.put("/dashboards/{dashboard_id}/rls-policies/{policy_id}")
async def update_rls_policy(
    dashboard_id: str,
    policy_id: str,
    body: RLSPolicyUpdate,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RLSPolicy).where(
            RLSPolicy.id == uuid.UUID(policy_id),
            RLSPolicy.dashboard_id == uuid.UUID(dashboard_id),
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    if body.name is not None:
        policy.name = body.name
    if body.clause is not None:
        policy.clause = body.clause
    if body.user_id is not None:
        policy.user_id = uuid.UUID(body.user_id) if body.user_id != "null" else None
    if body.is_active is not None:
        policy.is_active = body.is_active
    policy.updated_at = datetime.utcnow()
    await db.commit()
    return _policy_dict(policy)


@router.delete("/dashboards/{dashboard_id}/rls-policies/{policy_id}")
async def delete_rls_policy(
    dashboard_id: str,
    policy_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RLSPolicy).where(
            RLSPolicy.id == uuid.UUID(policy_id),
            RLSPolicy.dashboard_id == uuid.UUID(dashboard_id),
        )
    )
    policy = result.scalar_one_or_none()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(policy)
    await db.commit()
    return {"deleted": policy_id}


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULED REFRESH
# ═══════════════════════════════════════════════════════════════════════════════

class RefreshSchedulePatch(BaseModel):
    cron: Optional[str] = None        # e.g. "0 8 * * 1-5" = weekdays 8am
    enabled: bool = True
    timezone: str = "UTC"


@router.get("/dashboards/{dashboard_id}/refresh-schedule")
async def get_refresh_schedule(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    dash = await _get_dashboard(dashboard_id, db)
    lc = dash.layout_config or {}
    return {"schedule": lc.get("refresh_schedule", {"enabled": False, "cron": None, "timezone": "UTC"})}


@router.patch("/dashboards/{dashboard_id}/refresh-schedule")
async def set_refresh_schedule(
    dashboard_id: str,
    body: RefreshSchedulePatch,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    dash = await _get_dashboard(dashboard_id, db)
    lc = dict(dash.layout_config or {})
    schedule = {
        "enabled": body.enabled,
        "cron": body.cron,
        "timezone": body.timezone,
    }
    lc["refresh_schedule"] = schedule
    dash.layout_config = lc
    dash.updated_at = datetime.utcnow()
    await db.commit()
    # Poke the scheduler to reload jobs
    try:
        from agent_service.scheduler import reload_job_for_dashboard
        reload_job_for_dashboard(dashboard_id, schedule)
    except Exception:
        pass
    return {"schedule": schedule}


@router.post("/dashboards/{dashboard_id}/refresh-now")
async def refresh_now(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """Re-run all widget SQL for this dashboard, save fresh chart_data."""
    from agent_service.scheduler import run_dashboard_refresh
    summary = await run_dashboard_refresh(dashboard_id)
    return {"status": "refreshed", "dashboard_id": dashboard_id, **summary}


# ═══════════════════════════════════════════════════════════════════════════════
# CALCULATED MEASURES
# ═══════════════════════════════════════════════════════════════════════════════

class MeasureCreate(BaseModel):
    name: str                     # identifier, e.g. "profit_margin"
    label: str                    # display label, e.g. "Profit Margin %"
    expression: str               # SQL formula, e.g. "SUM(profit) / SUM(revenue) * 100"
    format: Optional[str] = None  # "percent" | "currency" | "number"


@router.get("/dashboards/{dashboard_id}/measures")
async def list_measures(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    dash = await _get_dashboard(dashboard_id, db)
    lc = dash.layout_config or {}
    return {"measures": lc.get("measures", [])}


@router.post("/dashboards/{dashboard_id}/measures", status_code=201)
async def create_measure(
    dashboard_id: str,
    body: MeasureCreate,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    dash = await _get_dashboard(dashboard_id, db)
    lc = dict(dash.layout_config or {})
    measures: list = list(lc.get("measures", []))
    # Upsert: replace existing measure with same name
    measures = [m for m in measures if m.get("name") != body.name]
    measures.append({
        "name": body.name,
        "label": body.label,
        "expression": body.expression,
        "format": body.format or "number",
    })
    lc["measures"] = measures
    dash.layout_config = lc
    dash.updated_at = datetime.utcnow()
    await db.commit()
    return {"measures": measures}


@router.delete("/dashboards/{dashboard_id}/measures/{measure_name}")
async def delete_measure(
    dashboard_id: str,
    measure_name: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    dash = await _get_dashboard(dashboard_id, db)
    lc = dict(dash.layout_config or {})
    measures = [m for m in lc.get("measures", []) if m.get("name") != measure_name]
    lc["measures"] = measures
    dash.layout_config = lc
    dash.updated_at = datetime.utcnow()
    await db.commit()
    return {"deleted": measure_name, "measures": measures}


class MeasureAIRequest(BaseModel):
    description: str


@router.post("/dashboards/{dashboard_id}/measures/generate")
async def generate_measure(
    dashboard_id: str,
    body: MeasureAIRequest,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """Use AI to suggest a SQL expression for a calculated measure."""
    import json
    from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL

    prompt = (
        f"Generate a SQL aggregate expression for this calculated measure: \"{body.description}\".\n"
        "Reply with ONLY a JSON object in this exact format, no extra text:\n"
        '{"name": "snake_case_name", "label": "Display Label", "expression": "SQL expression here", "format": "number|percent|currency"}'
    )
    resp = await bedrock_invoke_with_history(
        model_id=BEDROCK_SONNET_MODEL,
        system_prompt="You are a SQL expert. Return only the JSON object requested, no extra text.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=256,
    )
    text = resp.get("text", "") if isinstance(resp, dict) else str(resp)
    m = _re.search(r'\{.*\}', text, _re.DOTALL)
    if not m:
        raise HTTPException(status_code=502, detail="AI did not return valid JSON")
    try:
        result = json.loads(m.group())
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="AI returned malformed JSON")
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# DRILLDOWN
# ═══════════════════════════════════════════════════════════════════════════════

class DrilldownRequest(BaseModel):
    widget_id: str
    drill_column: str    # column that was clicked
    drill_value: str     # value that was clicked
    connection_id: Optional[str] = None


@router.post("/dashboards/{dashboard_id}/drilldown")
async def generate_drilldown(
    dashboard_id: str,
    body: DrilldownRequest,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """AI generates a child SQL for drilldown and executes it immediately."""
    from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL

    result = await db.execute(
        select(WidgetModel).where(WidgetModel.id == uuid.UUID(body.widget_id))
    )
    widget = result.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    parent_sql = widget.base_sql or widget.sql_query or ""
    conn_id = body.connection_id or (str(widget.connection_id) if widget.connection_id else None)
    if not conn_id:
        raise HTTPException(status_code=400, detail="No connection_id available for drilldown")

    prompt = (
        f"You are a SQL expert. Given this parent query:\n\n{parent_sql}\n\n"
        f"The user clicked on '{body.drill_column}' = '{body.drill_value}'.\n"
        "Generate a drilldown SQL query that shows the detail breakdown behind this data point. "
        "Add a WHERE clause filtering on the clicked value and SELECT more granular columns. "
        "Return ONLY the SQL query, no explanation, no markdown code fences."
    )
    resp = await bedrock_invoke_with_history(
        model_id=BEDROCK_SONNET_MODEL,
        system_prompt="You are a SQL expert. Return only the SQL query, no markdown, no explanation.",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    child_sql = (resp.get("text", "") if isinstance(resp, dict) else str(resp)).strip()
    # Strip markdown fences if AI wraps output
    child_sql = _re.sub(r'^```sql\s*', '', child_sql, flags=_re.IGNORECASE)
    child_sql = _re.sub(r'^```\s*', '', child_sql)
    child_sql = _re.sub(r'\s*```$', '', child_sql).strip()

    exec_result = await call_query_executor(conn_id, child_sql, row_limit=500)
    if exec_result.get("error"):
        raise HTTPException(status_code=502, detail=f"Drilldown query failed: {exec_result['error']}")

    return {
        "widget_id": body.widget_id,
        "drill_column": body.drill_column,
        "drill_value": body.drill_value,
        "child_sql": child_sql,
        "chart_data": {
            "rows": exec_result.get("rows", []),
            "columns": exec_result.get("columns", []),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# REQUERY WITH RLS
# ═══════════════════════════════════════════════════════════════════════════════

class RequeryWithRLSRequest(BaseModel):
    filters: dict = {}


def _inject_filters_into_sql(base_sql: str, filters: dict) -> str:
    """Lightweight copy of main.py's filter injector for use inside this router."""
    active = {col: vals for col, vals in filters.items() if vals}
    if not active:
        return base_sql
    modified = base_sql.rstrip(";")
    append_clauses: list[str] = []
    for col, vals in active.items():
        safe_col = _re.sub(r"[^\w.]", "", col)
        if isinstance(vals, dict) and "start" in vals and "end" in vals:
            start = str(vals["start"]).replace("'", "''")
            end   = str(vals["end"]).replace("'", "''")
            append_clauses.append(f"{safe_col} BETWEEN '{start}' AND '{end}'")
        else:
            if not isinstance(vals, list):
                vals = [vals]
            safe_vals = [str(v).replace("'", "''") for v in vals]
            if len(safe_vals) == 1:
                append_clauses.append(f"{safe_col} = '{safe_vals[0]}'")
            else:
                vals_sql = ", ".join(f"'{v}'" for v in safe_vals)
                append_clauses.append(f"{safe_col} IN ({vals_sql})")
    if append_clauses:
        has_where = bool(_re.search(r"\bWHERE\b", modified, _re.IGNORECASE))
        connector = " AND " if has_where else " WHERE "
        modified += connector + " AND ".join(append_clauses)
    return modified


@router.post("/dashboards/{dashboard_id}/requery-rls")
async def requery_with_rls(
    dashboard_id: str,
    body: RequeryWithRLSRequest,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    """Like /requery but injects active RLS WHERE clauses for the current user."""
    # Collect active RLS clauses: user-specific policies first, then catch-all
    rls_result = await db.execute(
        select(RLSPolicy).where(
            RLSPolicy.dashboard_id == uuid.UUID(dashboard_id),
            RLSPolicy.is_active == True,
        )
    )
    all_policies = rls_result.scalars().all()
    user_policies = [p for p in all_policies if p.user_id == current_user.id]
    catchall_policies = [p for p in all_policies if p.user_id is None]
    active_clauses = [p.clause for p in (user_policies or catchall_policies)]

    widgets_result = await db.execute(
        select(WidgetModel).where(WidgetModel.dashboard_id == uuid.UUID(dashboard_id))
    )
    widgets = widgets_result.scalars().all()

    async def _exec(w: WidgetModel) -> dict:
        sql = w.base_sql or w.sql_query
        if not sql:
            return {"widget_id": str(w.id), "chart_data": w.chart_data}
        sql = _inject_filters_into_sql(sql, body.filters)
        sql = _inject_rls(sql, active_clauses)
        conn_id = str(w.connection_id) if w.connection_id else None
        if not conn_id:
            return {"widget_id": str(w.id), "chart_data": w.chart_data}
        try:
            res = await call_query_executor(conn_id, sql, row_limit=500)
            if res and not res.get("error"):
                return {"widget_id": str(w.id), "chart_data": {"rows": res["rows"], "columns": res["columns"]}}
        except Exception as exc:
            print(f"[requery-rls] widget {w.id} failed: {exc}", flush=True)
        return {"widget_id": str(w.id), "chart_data": w.chart_data}

    results = await asyncio.gather(*[_exec(w) for w in widgets], return_exceptions=True)
    widget_data = [r for r in results if isinstance(r, dict)]
    return {"dashboard_id": dashboard_id, "widgets": widget_data}
