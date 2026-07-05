"""Agent alerts: plain-language conditions on widget data, evaluated on a schedule.

The user types the condition ("tell me if daily applications drop below 5");
an LLM parses it into a machine rule; the scheduler evaluates it and, on
trigger, generates a natural-language explanation (see scheduler.evaluate_alerts).
"""
import json
import os
import re
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
from shared.models.alerts import AlertRule
from shared.models.users import User
from shared.security import decode_token
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

router = APIRouter(tags=["alerts"])

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


_PARSE_PROMPT = """You convert a plain-language data alert condition into strict JSON.
Return ONLY one of these shapes (no prose, no fences):
{"type": "threshold", "column": "<metric column or empty>", "op": "<|<=|>|>=|=", "value": <number>}
{"type": "anomaly", "sigma": 2}
Use "anomaly" when the user wants "unusual"/"anomaly"/"something weird" detection
or when no clear numeric threshold is stated."""


def _fallback_parse(text: str) -> dict:
    m = re.search(r"(below|under|less than|drops? below|<)\s*\$?(\d+(?:\.\d+)?)", text.lower())
    if m:
        return {"type": "threshold", "column": "", "op": "<", "value": float(m.group(2))}
    m = re.search(r"(above|over|more than|exceeds?|>)\s*\$?(\d+(?:\.\d+)?)", text.lower())
    if m:
        return {"type": "threshold", "column": "", "op": ">", "value": float(m.group(2))}
    return {"type": "anomaly", "sigma": 2}


async def _parse_condition(condition_text: str) -> dict:
    try:
        raw = await bedrock_invoke(
            model_id=BEDROCK_HAIKU_MODEL,
            system_prompt=_PARSE_PROMPT,
            user_message=condition_text,
            max_tokens=200,
            temperature=0.0,
        )
        raw = re.sub(r"^```[a-z]*\n?|```$", "", raw.strip()).strip()
        rule = json.loads(raw)
        if rule.get("type") in ("threshold", "anomaly"):
            return rule
    except Exception as exc:
        print(f"[alerts] condition parse failed, using fallback: {exc}", flush=True)
    return _fallback_parse(condition_text)


async def _own_dashboard(dashboard_id: str, db: AsyncSession) -> Dashboard:
    result = await db.execute(select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id)))
    dash = result.scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dash


class AlertCreate(BaseModel):
    name: str
    condition_text: str
    widget_id: Optional[str] = None
    cadence_minutes: int = 60
    channel: str = "inapp"
    email: Optional[str] = None


class AlertPatch(BaseModel):
    name: Optional[str] = None
    condition_text: Optional[str] = None
    cadence_minutes: Optional[int] = None
    channel: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = None


def _alert_dict(a: AlertRule) -> dict:
    return {
        "id": str(a.id),
        "dashboard_id": str(a.dashboard_id),
        "widget_id": str(a.widget_id) if a.widget_id else None,
        "name": a.name,
        "condition_text": a.condition_text,
        "rule": a.rule,
        "cadence_minutes": a.cadence_minutes,
        "channel": a.channel,
        "email": a.email,
        "is_active": a.is_active,
        "last_evaluated_at": a.last_evaluated_at.isoformat() if a.last_evaluated_at else None,
        "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None,
        "last_result": a.last_result,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.post("/dashboards/{dashboard_id}/alerts")
async def create_alert(
    dashboard_id: str,
    body: AlertCreate,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    await _own_dashboard(dashboard_id, db)
    rule = await _parse_condition(body.condition_text)
    alert = AlertRule(
        dashboard_id=uuid.UUID(dashboard_id),
        widget_id=uuid.UUID(body.widget_id) if body.widget_id else None,
        name=body.name,
        condition_text=body.condition_text,
        rule=rule,
        cadence_minutes=max(5, body.cadence_minutes),
        channel=body.channel if body.channel in ("inapp", "email") else "inapp",
        email=body.email,
        created_by=current_user.id,
    )
    db.add(alert)
    await db.commit()
    await db.refresh(alert)
    return _alert_dict(alert)


@router.get("/dashboards/{dashboard_id}/alerts")
async def list_alerts(
    dashboard_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    await _own_dashboard(dashboard_id, db)
    result = await db.execute(
        select(AlertRule).where(AlertRule.dashboard_id == uuid.UUID(dashboard_id))
        .order_by(AlertRule.created_at.desc())
    )
    return {"alerts": [_alert_dict(a) for a in result.scalars().all()]}


@router.patch("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    body: AlertPatch,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == uuid.UUID(alert_id)))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if body.name is not None:
        alert.name = body.name
    if body.condition_text is not None and body.condition_text != alert.condition_text:
        alert.condition_text = body.condition_text
        alert.rule = await _parse_condition(body.condition_text)
    if body.cadence_minutes is not None:
        alert.cadence_minutes = max(5, body.cadence_minutes)
    if body.channel is not None and body.channel in ("inapp", "email"):
        alert.channel = body.channel
    if body.email is not None:
        alert.email = body.email
    if body.is_active is not None:
        alert.is_active = body.is_active
    await db.commit()
    await db.refresh(alert)
    return _alert_dict(alert)


@router.delete("/alerts/{alert_id}")
async def delete_alert(
    alert_id: str,
    current_user: User = Depends(_get_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == uuid.UUID(alert_id)))
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.delete(alert)
    await db.commit()
    return {"deleted": alert_id}
