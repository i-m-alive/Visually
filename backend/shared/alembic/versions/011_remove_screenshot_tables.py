"""remove screenshot replication tables (feature removed)

Drops the four phase-3 screenshot-replication tables. The feature and all of its
code (vision agent, schema matcher, screenshot router, etc.) were removed, so the
tables are dead. User accounts / projects / dashboards live in other tables and are
untouched.

Revision ID: 011
Revises: 010
Create Date: 2026-06-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop in FK-dependency order; CASCADE + IF EXISTS make this idempotent and
    # safe regardless of which indexes/constraints exist.
    op.execute("DROP TABLE IF EXISTS hint_queue CASCADE")
    op.execute("DROP TABLE IF EXISTS chart_replication_states CASCADE")
    op.execute("DROP TABLE IF EXISTS uploaded_files CASCADE")
    op.execute("DROP TABLE IF EXISTS screenshot_jobs CASCADE")


def downgrade() -> None:
    # Recreate the original phase-3 schema (mirror of migration 003).
    op.create_table(
        "screenshot_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("uploaded_files", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("chart_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("result_dashboard_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dashboards.id"), nullable=True),
        sa.Column("total_charts", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("confirmed_charts", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_screenshot_jobs_project", "screenshot_jobs", ["project_id"])

    op.create_table(
        "chart_replication_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("screenshot_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chart_id", sa.String(50), nullable=False),
        sa.Column("chart_spec", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_sql", sa.Text(), nullable=True),
        sa.Column("validation_score", sa.Float(), nullable=True),
        sa.Column("validation_details", postgresql.JSONB(), nullable=True),
        sa.Column("retry_feedback", sa.Text(), nullable=True),
        sa.Column("hint_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("hint_options", postgresql.JSONB(), nullable=True),
        sa.Column("hint_response", sa.Text(), nullable=True),
        sa.Column("widget_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("widgets.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_chart_replication_job", "chart_replication_states", ["job_id"])
    op.create_unique_constraint("uq_chart_replication", "chart_replication_states", ["job_id", "chart_id"])

    op.create_table(
        "uploaded_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("s3_key", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "hint_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("screenshot_jobs.id"), nullable=False),
        sa.Column("chart_replication_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chart_replication_states.id"), nullable=False),
        sa.Column("hint_type", sa.String(30), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=False),
        sa.Column("is_shown", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_answered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("user_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
