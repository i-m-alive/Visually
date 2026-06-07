"""
Export token utilities — generate, store, and validate short-lived bearer tokens
that allow anonymous access to the AI chat panel embedded in HTML exports.
"""
import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.models.phase4 import ExportToken


def generate_export_token() -> str:
    """Generate a cryptographically secure random token (32 bytes → 64 hex chars)."""
    return secrets.token_hex(32)


def _hash_token(raw_token: str) -> str:
    """SHA-256 hash of the raw token — this is what we store in the DB."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def create_export_token(
    db: AsyncSession,
    export_job_id: uuid.UUID,
    project_id: uuid.UUID,
    expiry_days: int = 30,
    scopes: list[str] | None = None,
) -> tuple[str, ExportToken]:
    """
    Create and persist an ExportToken.

    Returns
    -------
    (raw_token, ExportToken)
        raw_token is the plain-text token to embed in the HTML file.
        ExportToken is the persisted ORM object (token_hash stored, not plain text).
    """
    if scopes is None:
        scopes = ["chat:read"]

    raw_token = generate_export_token()
    token_hash = _hash_token(raw_token)
    expires_at = datetime.utcnow() + timedelta(days=expiry_days)

    export_token = ExportToken(
        id=uuid.uuid4(),
        export_job_id=export_job_id,
        project_id=project_id,
        token_hash=token_hash,
        scopes=scopes,
        expires_at=expires_at,
        is_revoked=False,
        created_at=datetime.utcnow(),
    )
    db.add(export_token)
    await db.commit()
    await db.refresh(export_token)

    return raw_token, export_token


async def validate_export_token(
    db: AsyncSession,
    raw_token: str,
    required_scope: str = "chat:read",
) -> ExportToken | None:
    """
    Validate a raw export token submitted by a client.

    Returns the ExportToken ORM object if valid, or None if invalid/expired/revoked.
    Updates last_used_at on success.
    """
    if not raw_token:
        return None

    token_hash = _hash_token(raw_token)

    result = await db.execute(
        select(ExportToken).where(ExportToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()

    if token is None:
        return None

    if token.is_revoked:
        return None

    if token.expires_at < datetime.utcnow():
        return None

    if required_scope and required_scope not in (token.scopes or []):
        return None

    # Update last_used_at
    token.last_used_at = datetime.utcnow()
    await db.commit()

    return token
