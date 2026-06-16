"""query chat history tables (sessions + message tree)

Revision ID: 012
Revises: 011
Create Date: 2026-06-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "query_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default="New chat"),
        sa.Column("active_leaf_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_query_sessions_project_user", "query_sessions", ["project_id", "user_id"])

    op.create_table(
        "query_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("query_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("result", JSONB, nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_query_messages_session", "query_messages", ["session_id"])


def downgrade() -> None:
    op.drop_index("idx_query_messages_session", "query_messages")
    op.drop_table("query_messages")
    op.drop_index("idx_query_sessions_project_user", "query_sessions")
    op.drop_table("query_sessions")
