"""
Share-link and collaborator management endpoints.

Share links
-----------
POST   /dashboards/{id}/shares           → create a new share token
GET    /dashboards/{id}/shares           → list active tokens
DELETE /dashboards/{id}/shares/{tok_id} → revoke a token

Collaborators
-------------
POST   /dashboards/{id}/collaborators           → invite by email
GET    /dashboards/{id}/collaborators           → list
DELETE /dashboards/{id}/collaborators/{user_id} → remove
"""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.models.dashboards import Dashboard
from shared.models.users import User
from shared.models.sharing import CanvasShareToken, CanvasCollaborator
from shared.security import decode_token

router = APIRouter(tags=["share"])

bearer_scheme = HTTPBearer(auto_error=False)

DEV_MODE    = os.getenv("DEV_MODE", "").lower() in ("true", "1", "yes")
DEV_USER_ID = os.getenv("DEV_USER_ID", "00000000-0000-0000-0000-000000000001")

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")


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


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ShareCreateRequest(BaseModel):
    mode: str = "live"          # "live" | "snapshot"
    label: Optional[str] = None
    expires_days: Optional[int] = 30  # None = never expires


class CollaboratorInvite(BaseModel):
    email: str
    role: str = "viewer"        # "viewer" | "editor"


# ── Share token endpoints ─────────────────────────────────────────────────────

@router.post("/dashboards/{dashboard_id}/shares", status_code=201)
async def create_share(
    dashboard_id: str,
    body: ShareCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Create a new share token and return the public URL."""
    dash = (await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    raw_token = secrets.token_hex(32)
    expires_at = (
        datetime.utcnow() + timedelta(days=body.expires_days)
        if body.expires_days else None
    )

    token_obj = CanvasShareToken(
        id=uuid.uuid4(),
        dashboard_id=uuid.UUID(dashboard_id),
        created_by=current_user.id,
        token_hash=_hash(raw_token),
        mode=body.mode,
        label=body.label,
        expires_at=expires_at,
        created_at=datetime.utcnow(),
    )
    db.add(token_obj)
    await db.commit()
    await db.refresh(token_obj)

    share_url   = f"{FRONTEND_URL}/share/canvas/{raw_token}"
    embed_url   = f"{FRONTEND_URL}/embed/canvas/{raw_token}"

    return {
        "id": str(token_obj.id),
        "token": raw_token,           # only returned once — store it client-side
        "share_url": share_url,
        "embed_url": embed_url,
        "mode": token_obj.mode,
        "label": token_obj.label,
        "expires_at": token_obj.expires_at.isoformat() if token_obj.expires_at else None,
        "created_at": token_obj.created_at.isoformat(),
    }


@router.get("/dashboards/{dashboard_id}/shares")
async def list_shares(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """List all active (non-revoked) share tokens for a canvas."""
    result = await db.execute(
        select(CanvasShareToken).where(
            CanvasShareToken.dashboard_id == uuid.UUID(dashboard_id),
            CanvasShareToken.is_revoked == False,
        ).order_by(CanvasShareToken.created_at.desc())
    )
    tokens = result.scalars().all()
    return {
        "shares": [
            {
                "id": str(t.id),
                "mode": t.mode,
                "label": t.label,
                "access_count": t.access_count,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "created_at": t.created_at.isoformat(),
                # Note: raw token is NOT returned here for security
                "share_url": f"{FRONTEND_URL}/share/canvas/[token]",
                "embed_url": f"{FRONTEND_URL}/embed/canvas/[token]",
            }
            for t in tokens
        ]
    }


@router.delete("/dashboards/{dashboard_id}/shares/{token_id}")
async def revoke_share(
    dashboard_id: str,
    token_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Revoke (disable) a share token — link stops working immediately."""
    result = await db.execute(
        select(CanvasShareToken).where(
            CanvasShareToken.id == uuid.UUID(token_id),
            CanvasShareToken.dashboard_id == uuid.UUID(dashboard_id),
        )
    )
    token_obj = result.scalar_one_or_none()
    if not token_obj:
        raise HTTPException(status_code=404, detail="Share token not found")
    token_obj.is_revoked = True
    await db.commit()
    return {"revoked": token_id}


# ── Collaborator endpoints ────────────────────────────────────────────────────

@router.post("/dashboards/{dashboard_id}/collaborators", status_code=201)
async def add_collaborator(
    dashboard_id: str,
    body: CollaboratorInvite,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Invite a registered user (by email) as a canvas collaborator."""
    dash = (await db.execute(
        select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
    )).scalar_one_or_none()
    if not dash:
        raise HTTPException(status_code=404, detail="Dashboard not found")

    invite_user = (await db.execute(
        select(User).where(User.email == body.email, User.is_active == True)
    )).scalar_one_or_none()
    if not invite_user:
        raise HTTPException(status_code=404, detail=f"No active user found with email {body.email}")

    if str(invite_user.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Cannot invite yourself")

    # Upsert: if already a collaborator, update the role
    existing = (await db.execute(
        select(CanvasCollaborator).where(
            CanvasCollaborator.dashboard_id == uuid.UUID(dashboard_id),
            CanvasCollaborator.user_id == invite_user.id,
        )
    )).scalar_one_or_none()

    if existing:
        existing.role = body.role
        await db.commit()
        await db.refresh(existing)
        collab = existing
    else:
        collab = CanvasCollaborator(
            id=uuid.uuid4(),
            dashboard_id=uuid.UUID(dashboard_id),
            user_id=invite_user.id,
            invited_by=current_user.id,
            role=body.role,
            created_at=datetime.utcnow(),
        )
        db.add(collab)
        await db.commit()
        await db.refresh(collab)

    return {
        "id": str(collab.id),
        "dashboard_id": dashboard_id,
        "user_id": str(invite_user.id),
        "email": invite_user.email,
        "full_name": invite_user.full_name,
        "role": collab.role,
        "created_at": collab.created_at.isoformat(),
    }


@router.get("/dashboards/{dashboard_id}/collaborators")
async def list_collaborators(
    dashboard_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """List all collaborators on a canvas."""
    result = await db.execute(
        select(CanvasCollaborator, User).join(
            User, CanvasCollaborator.user_id == User.id
        ).where(CanvasCollaborator.dashboard_id == uuid.UUID(dashboard_id))
        .order_by(CanvasCollaborator.created_at)
    )
    rows = result.all()
    return {
        "collaborators": [
            {
                "id": str(collab.id),
                "user_id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "role": collab.role,
                "created_at": collab.created_at.isoformat(),
            }
            for collab, user in rows
        ]
    }


@router.delete("/dashboards/{dashboard_id}/collaborators/{user_id}")
async def remove_collaborator(
    dashboard_id: str,
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(_get_user),
):
    """Remove a collaborator from a canvas."""
    result = await db.execute(
        select(CanvasCollaborator).where(
            CanvasCollaborator.dashboard_id == uuid.UUID(dashboard_id),
            CanvasCollaborator.user_id == uuid.UUID(user_id),
        )
    )
    collab = result.scalar_one_or_none()
    if not collab:
        raise HTTPException(status_code=404, detail="Collaborator not found")
    await db.delete(collab)
    await db.commit()
    return {"removed": user_id}
