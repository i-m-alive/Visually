"""
Brainwave user profile management — platform-level (no project_id scope).

A Brainwave employee is identified by their Visually email, regardless of which
project they're in or whether they have one at all (end_user / analyst role).

Endpoints:
  GET  /brainwave-profiles/me         — logged-in user's own profile (no params)
  PUT  /brainwave-profiles            — create/update a profile (admin only)
  GET  /brainwave-profiles/users      — all Visually users + profile status (admin only)

"Admin" = the requesting user has can_impersonate = True in their own profile,
OR DEV_MODE is enabled (lets the developer bootstrap before their profile exists).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.security import decode_token
from shared.models.users import User
from shared.models.brainwave_user_profile import BrainwaveUserProfile

router  = APIRouter(prefix="/brainwave-profiles", tags=["brainwave-profiles"])
_bearer = HTTPBearer(auto_error=False)
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"


# ── Auth dependency ───────────────────────────────────────────────────────────

async def _current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        if DEV_MODE:
            u = (await db.execute(select(User).order_by(User.created_at).limit(1))).scalar_one_or_none()
            if u:
                return u
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(credentials.credentials)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    user = (
        await db.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    ).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ── Admin gate ────────────────────────────────────────────────────────────────

async def _require_admin(user: User, db: AsyncSession) -> None:
    """Raise 403 unless this user has can_impersonate = True in their profile.

    DEV_MODE bypasses this so the developer can bootstrap before their own
    profile row exists.
    """
    if DEV_MODE:
        return
    profile = (
        await db.execute(
            select(BrainwaveUserProfile)
            .where(BrainwaveUserProfile.user_email == user.email)
        )
    ).scalar_one_or_none()
    if profile and profile.can_impersonate:
        return
    raise HTTPException(status_code=403, detail="Admin access required")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ProfileUpsertRequest(BaseModel):
    user_email:      str
    brainwave_role:  str
    db_name:         Optional[str] = None
    qualifier_id:    Optional[int] = None
    can_impersonate: bool = False


class ProfileResponse(BaseModel):
    id:              str
    user_email:      str
    brainwave_role:  str
    db_name:         Optional[str]
    qualifier_id:    Optional[int]
    can_impersonate: bool
    added_by:        Optional[str]
    created_at:      str


class PlatformUserRow(BaseModel):
    user_id:         str
    email:           str
    full_name:       str
    visually_role:   str          # builder | end_user
    brainwave_role:  Optional[str]
    db_name:         Optional[str]
    qualifier_id:    Optional[int]
    can_impersonate: bool
    has_profile:     bool


def _to_response(p: BrainwaveUserProfile) -> ProfileResponse:
    return ProfileResponse(
        id=str(p.id),
        user_email=p.user_email,
        brainwave_role=p.brainwave_role,
        db_name=p.db_name,
        qualifier_id=p.qualifier_id,
        can_impersonate=p.can_impersonate,
        added_by=p.added_by,
        created_at=p.created_at.isoformat(),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/me", response_model=Optional[ProfileResponse])
async def get_my_profile(
    user: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's Brainwave profile, or null if not configured."""
    profile = (
        await db.execute(
            select(BrainwaveUserProfile)
            .where(BrainwaveUserProfile.user_email == user.email)
        )
    ).scalar_one_or_none()
    return _to_response(profile) if profile else None


@router.put("", response_model=ProfileResponse)
async def upsert_profile(
    req: ProfileUpsertRequest,
    user: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a Brainwave profile. Admin only (can_impersonate = True)."""
    await _require_admin(user, db)

    existing = (
        await db.execute(
            select(BrainwaveUserProfile)
            .where(BrainwaveUserProfile.user_email == req.user_email)
        )
    ).scalar_one_or_none()

    if existing:
        existing.brainwave_role  = req.brainwave_role
        existing.db_name         = req.db_name
        existing.qualifier_id    = req.qualifier_id
        existing.can_impersonate = req.can_impersonate
        existing.added_by        = user.email
        await db.commit()
        await db.refresh(existing)
        return _to_response(existing)

    new_profile = BrainwaveUserProfile(
        id=uuid.uuid4(),
        user_email=req.user_email,
        brainwave_role=req.brainwave_role,
        db_name=req.db_name,
        qualifier_id=req.qualifier_id,
        can_impersonate=req.can_impersonate,
        added_by=user.email,
        created_at=datetime.utcnow(),
    )
    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)
    return _to_response(new_profile)


@router.get("/users", response_model=list[PlatformUserRow])
async def list_all_users(
    user: User = Depends(_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return ALL Visually platform users merged with their Brainwave profile.

    Shows every registered account regardless of project membership or role,
    so the admin can grant Brainwave access to end_user-role analysts who
    can't create projects, builders with separate projects, and anyone else.
    Admin only.
    """
    await _require_admin(user, db)

    all_users = (
        await db.execute(select(User).order_by(User.full_name))
    ).scalars().all()

    profiles = (
        await db.execute(select(BrainwaveUserProfile))
    ).scalars().all()
    profile_map = {p.user_email: p for p in profiles}

    existing_emails = {u.email for u in all_users}
    rows = []
    for u in all_users:
        p = profile_map.get(u.email)
        rows.append(PlatformUserRow(
            user_id=str(u.id),
            email=u.email,
            full_name=u.full_name or u.email,
            visually_role=u.role if hasattr(u, "role") else "builder",
            brainwave_role=p.brainwave_role if p else None,
            db_name=p.db_name if p else None,
            qualifier_id=p.qualifier_id if p else None,
            can_impersonate=p.can_impersonate if p else False,
            has_profile=p is not None,
        ))
    # Pre-registered profiles: email in brainwave_user_profiles but not yet in users
    for p in profiles:
        if p.user_email not in existing_emails:
            rows.append(PlatformUserRow(
                user_id="",
                email=p.user_email,
                full_name=p.user_email,
                visually_role="pending",
                brainwave_role=p.brainwave_role,
                db_name=p.db_name,
                qualifier_id=p.qualifier_id,
                can_impersonate=p.can_impersonate,
                has_profile=True,
            ))
    return rows
