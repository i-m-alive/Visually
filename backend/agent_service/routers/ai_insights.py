"""
AI Insights router — powers end-user AI features:
  POST /dashboards/{id}/ai-summary   → natural language summary of the report
  POST /dashboards/{id}/ai-insight   → one-liner insight for a single widget
  POST /dashboards/{id}/ai-anomalies → anomaly scan across all KPI widgets
"""
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.users import User
from shared.models.widgets import Widget
from shared.security import decode_token

router = APIRouter(tags=["ai_insights"])
bearer_scheme = HTTPBearer(auto_error=False)

DEV_MODE    = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")

BEDROCK_REGION = os.getenv("BEDROCK_REGION", os.getenv("AWS_REGION", "us-east-1"))
BEDROCK_MODEL  = (
    os.getenv("BEDROCK_MODEL_ID")
    or os.getenv("BEDROCK_SONNET_MODEL_ID")
    or "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)


async def _get_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # DEV_MODE: fall back to dev user only when no JWT is present.
    # A real JWT always wins so each user sees their own data.
    if DEV_MODE and credentials is None:
        from shared.security import hash_password
        from datetime import datetime
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
    result = await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def _call_bedrock(prompt: str) -> str:
    """Call Bedrock Claude and return the text response."""
    try:
        import boto3, json as _json
        client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        body = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = client.invoke_model(modelId=BEDROCK_MODEL, body=body)
        result = _json.loads(response["body"].read())
        return result["content"][0]["text"].strip()
    except Exception as e:
        return f"AI service unavailable: {e}"


def _widget_summary(w: Widget) -> str:
    cd = w.chart_data or {}
    rows = cd.get("rows", [])
    cols = cd.get("columns", [])
    preview = str(rows[:3]) if rows else "(no data)"
    return f'Title: "{w.title}" | Type: {w.chart_type} | Columns: {cols} | Sample rows: {preview}'


# ── POST /dashboards/{id}/ai-summary ─────────────────────────────────────────

@router.post("/dashboards/{dashboard_id}/ai-summary")
async def ai_summary(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    dash_res = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
    dashboard = dash_res.scalar_one_or_none()
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    widget_res = await db.execute(select(Widget).where(Widget.dashboard_id == uuid.UUID(dashboard_id)))
    widgets = list(widget_res.scalars().all())

    if not widgets:
        return {"summary": "This report has no charts yet."}

    widget_descs = "\n".join(f"- {_widget_summary(w)}" for w in widgets[:12])
    prompt = (
        f'You are a business intelligence analyst. The report is called "{dashboard.name}".\n\n'
        f"It contains these charts:\n{widget_descs}\n\n"
        "Write a 2–3 sentence plain-English summary of what this report shows and its key takeaways. "
        "Be concise and non-technical. Do not mention chart types or column names directly."
    )
    summary = _call_bedrock(prompt)
    return {"summary": summary}


# ── POST /dashboards/{id}/ai-insight ─────────────────────────────────────────

class InsightRequest(BaseModel):
    widget_id: str


@router.post("/dashboards/{dashboard_id}/ai-insight")
async def ai_insight(
    dashboard_id: str,
    req: InsightRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    widget_res = await db.execute(
        select(Widget).where(
            Widget.id == uuid.UUID(req.widget_id),
            Widget.dashboard_id == uuid.UUID(dashboard_id),
        )
    )
    widget = widget_res.scalar_one_or_none()
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")

    desc = _widget_summary(widget)
    prompt = (
        f"You are a data analyst. Here is a chart:\n{desc}\n\n"
        "Write ONE sentence (max 20 words) describing the most important insight from this data. "
        "Be specific about numbers if available. Start with the key finding directly."
    )
    insight = _call_bedrock(prompt)
    return {"insight": insight, "widget_id": req.widget_id}


# ── POST /dashboards/{id}/ai-anomalies ───────────────────────────────────────

@router.post("/dashboards/{dashboard_id}/ai-anomalies")
async def ai_anomalies(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    widget_res = await db.execute(select(Widget).where(Widget.dashboard_id == uuid.UUID(dashboard_id)))
    widgets = list(widget_res.scalars().all())

    kpi_types = {"kpi", "kpi_card", "metric", "scorecard"}
    kpis = [w for w in widgets if (w.chart_type or "").lower() in kpi_types or "kpi" in (w.title or "").lower()]

    if not kpis:
        return {"anomalies": []}

    kpi_descs = "\n".join(f"- {_widget_summary(w)}" for w in kpis[:8])
    prompt = (
        "You are a business analyst monitoring KPI metrics. Here are the current KPI values:\n"
        f"{kpi_descs}\n\n"
        "For each KPI that seems unusual, abnormal, or worth flagging, output a JSON array like:\n"
        '[{"widget_id": "<id>", "severity": "warning|critical", "message": "<short 10-word explanation>"}]\n'
        "Only include genuinely unusual values. If everything looks normal, return [].\n"
        "Return ONLY the JSON array, nothing else."
    )
    raw = _call_bedrock(prompt)

    import json, re
    anomalies = []
    try:
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            anomalies = json.loads(match.group())
    except Exception:
        pass

    # Map back original widget_ids (the prompt used titles, not IDs)
    widget_map = {w.title: str(w.id) for w in kpis}
    for a in anomalies:
        if a.get("widget_id") not in {str(w.id) for w in kpis}:
            # Try to match by title
            matched = next((wid for title, wid in widget_map.items() if title in a.get("widget_id", "")), None)
            if matched:
                a["widget_id"] = matched

    return {"anomalies": anomalies}
