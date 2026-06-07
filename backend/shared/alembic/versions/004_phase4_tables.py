"""phase4 export tables

Revision ID: 004
Revises: 003
Create Date: 2024-01-04 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "export_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dashboard_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("export_type", sa.String(20), nullable=False, server_default="html"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("theme", sa.String(30), nullable=True, server_default="frost"),
        sa.Column("include_chat", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("token_expiry_days", sa.Integer(), nullable=True, server_default="30"),
        sa.Column("s3_key", sa.String(500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_export_jobs_dashboard", "export_jobs", ["dashboard_id"])
    op.create_index("idx_export_jobs_project", "export_jobs", ["project_id"])

    op.create_table(
        "export_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("export_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("export_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("scopes", postgresql.JSONB(), nullable=False, server_default='["chat:read"]'),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_export_tokens_hash", "export_tokens", ["token_hash"])
    op.create_index("idx_export_tokens_project", "export_tokens", ["project_id"])

    op.create_table(
        "export_chat_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("export_job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("export_jobs.id"), nullable=True),
        sa.Column("export_token_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("export_tokens.id"), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("messages", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("export_chat_sessions")
    op.drop_index("idx_export_tokens_project")
    op.drop_index("idx_export_tokens_hash")
    op.drop_table("export_tokens")
    op.drop_index("idx_export_jobs_project")
    op.drop_index("idx_export_jobs_dashboard")
    op.drop_table("export_jobs")
