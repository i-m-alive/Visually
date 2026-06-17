"""add username (User ID) column to users

Adds a unique `username` login identifier so users can sign in with either
their email or their username. Existing rows are backfilled from the local
part of their email address (the text before "@"), with a numeric suffix
appended on collision. Uniqueness is enforced case-insensitively to match
the application's login lookup.

Revision ID: 013
Revises: 012
Create Date: 2026-06-17 00:00:00.000000
"""
from typing import Sequence, Union
import re

from alembic import op
import sqlalchemy as sa


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _slugify(local_part: str) -> str:
    """Turn an email local-part into a safe username base."""
    base = (local_part or "").strip().lower()
    # Keep it conservative: letters, digits, dot, underscore, hyphen.
    base = re.sub(r"[^a-z0-9._-]", "", base)
    return base or "user"


def upgrade() -> None:
    # 1. Add the column as nullable so existing rows survive.
    op.add_column("users", sa.Column("username", sa.String(100), nullable=True))

    # 2. Backfill from email local-part, ensuring case-insensitive uniqueness.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, email FROM users ORDER BY created_at")).fetchall()

    used: set[str] = set()
    for row in rows:
        base = _slugify((row.email or "").split("@")[0])
        candidate = base
        n = 1
        while candidate.lower() in used:
            n += 1
            candidate = f"{base}{n}"
        used.add(candidate.lower())
        conn.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :id"),
            {"u": candidate, "id": row.id},
        )

    # 3. Lock it down: NOT NULL + unique index.
    op.alter_column("users", "username", existing_type=sa.String(100), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
