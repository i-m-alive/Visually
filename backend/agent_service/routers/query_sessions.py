"""Persistent chat history for the Query feature (ChatGPT/Claude-style).

CRUD over query_sessions + a message TREE (query_messages.parent_id). Editing a
past message = posting a new message with the same parent_id (a sibling) →
alternate branch. session.active_leaf_id marks the active branch's leaf.
"""
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from shared.security import decode_token
from shared.models.users import User
from shared.models.query_chat import QuerySession, QueryMessage

router = APIRouter(prefix="/query/sessions", tags=["query-sessions"])
_bearer = HTTPBearer(auto_error=False)
DEV_MODE = os.getenv("DEV_MODE", "false").lower() == "true"


async def _current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Mirrors main.get_current_user: validate a real JWT; fall back to the dev
    # user only when no token is present and DEV_MODE is on.
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
    user = (await db.execute(select(User).where(User.id == uuid.UUID(payload.get("sub"))))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def _owned_session(sid: str, user: User, db: AsyncSession) -> QuerySession:
    try:
        sess = (await db.execute(select(QuerySession).where(QuerySession.id == uuid.UUID(sid)))).scalar_one_or_none()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session id")
    if not sess or sess.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


def _msg_dict(m: QueryMessage) -> dict:
    return {
        "id": str(m.id),
        "parent_id": str(m.parent_id) if m.parent_id else None,
        "role": m.role,
        "content": m.content,
        "result": m.result,
        "job_id": m.job_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


# ── request bodies ───────────────────────────────────────────────────────────
class CreateSession(BaseModel):
    project_id: str
    title: Optional[str] = None


class UpdateSession(BaseModel):
    title: Optional[str] = None
    active_leaf_id: Optional[str] = None


class CreateMessage(BaseModel):
    role: str                       # "user" | "assistant"
    content: str = ""
    parent_id: Optional[str] = None
    result: Optional[dict] = None
    job_id: Optional[str] = None


# ── endpoints ────────────────────────────────────────────────────────────────
@router.get("")
async def list_sessions(project_id: str, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project_id")
    rows = (await db.execute(
        select(QuerySession)
        .where(QuerySession.project_id == pid, QuerySession.user_id == user.id)
        .order_by(QuerySession.updated_at.desc())
    )).scalars().all()
    counts = dict((sid, n) for sid, n in (await db.execute(
        select(QueryMessage.session_id, func.count(QueryMessage.id)).group_by(QueryMessage.session_id)
    )).all())
    return [
        {
            "id": str(s.id), "title": s.title,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "message_count": int(counts.get(s.id, 0)),
        }
        for s in rows
    ]


@router.post("")
async def create_session(req: CreateSession, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    try:
        pid = uuid.UUID(req.project_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid project_id")
    s = QuerySession(id=uuid.uuid4(), project_id=pid, user_id=user.id, title=(req.title or "New chat"))
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return {"id": str(s.id), "title": s.title, "active_leaf_id": None, "messages": []}


@router.get("/{sid}")
async def get_session(sid: str, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    sess = await _owned_session(sid, user, db)
    msgs = (await db.execute(
        select(QueryMessage).where(QueryMessage.session_id == sess.id).order_by(QueryMessage.created_at)
    )).scalars().all()
    return {
        "id": str(sess.id),
        "title": sess.title,
        "active_leaf_id": str(sess.active_leaf_id) if sess.active_leaf_id else None,
        "messages": [_msg_dict(m) for m in msgs],
    }


@router.patch("/{sid}")
async def update_session(sid: str, req: UpdateSession, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    sess = await _owned_session(sid, user, db)
    if req.title is not None:
        sess.title = req.title.strip()[:255] or "New chat"
    if req.active_leaf_id is not None:
        sess.active_leaf_id = uuid.UUID(req.active_leaf_id) if req.active_leaf_id else None
    sess.updated_at = datetime.utcnow()
    await db.commit()
    return {"id": str(sess.id), "title": sess.title,
            "active_leaf_id": str(sess.active_leaf_id) if sess.active_leaf_id else None}


@router.delete("/{sid}")
async def delete_session(sid: str, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    sess = await _owned_session(sid, user, db)
    await db.execute(sa_delete(QueryMessage).where(QueryMessage.session_id == sess.id))
    await db.execute(sa_delete(QuerySession).where(QuerySession.id == sess.id))
    await db.commit()
    return {"status": "deleted", "id": sid}


@router.post("/{sid}/messages")
async def add_message(sid: str, req: CreateMessage, user: User = Depends(_current_user), db: AsyncSession = Depends(get_db)):
    sess = await _owned_session(sid, user, db)
    parent_uuid = None
    if req.parent_id:
        try:
            parent_uuid = uuid.UUID(req.parent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid parent_id")
    m = QueryMessage(
        id=uuid.uuid4(), session_id=sess.id, parent_id=parent_uuid,
        role=req.role, content=req.content or "", result=req.result, job_id=req.job_id,
    )
    db.add(m)
    # Active branch now ends at this new message.
    sess.active_leaf_id = m.id
    sess.updated_at = datetime.utcnow()
    # Auto-title from the first user message.
    if req.role == "user" and (not sess.title or sess.title == "New chat"):
        sess.title = (req.content or "New chat").strip()[:60] or "New chat"
    await db.commit()
    await db.refresh(m)
    return _msg_dict(m)
