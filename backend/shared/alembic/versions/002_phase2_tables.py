"""phase2 tables and widget columns

Revision ID: 002
Revises: 001
Create Date: 2024-01-02 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New columns on widgets
    op.add_column("widgets", sa.Column("validation_status", sa.String(20), nullable=True, server_default="confirmed"))
    op.add_column("widgets", sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("widgets", sa.Column("last_refreshed_at", sa.DateTime(), nullable=True))
    op.add_column("widgets", sa.Column("chart_data", postgresql.JSONB(), nullable=True))

    op.create_table(
        "dashboard_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dashboard_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_dashboard_versions_dashboard_id", "dashboard_versions", ["dashboard_id"])

    op.create_table(
        "schema_change_alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("database_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("old_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schema_snapshots.id"), nullable=True),
        sa.Column("new_snapshot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schema_snapshots.id"), nullable=True),
        sa.Column("diff_summary", postgresql.JSONB(), nullable=False),
        sa.Column("breaking_changes", postgresql.JSONB(), nullable=True),
        sa.Column("affected_widget_ids", postgresql.JSONB(), nullable=True),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column("is_acknowledged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_schema_change_alerts_connection_id", "schema_change_alerts", ["connection_id"])

    op.create_table(
        "query_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("database_connections.id"), nullable=True),
        sa.Column("table_name", sa.String(255), nullable=True),
        sa.Column("chart_type", sa.String(50), nullable=True),
        sa.Column("sql_hash", sa.String(64), nullable=True),
        sa.Column("was_successful", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("idx_query_history_project_table", "query_history", ["project_id", "table_name"])


def downgrade() -> None:
    op.drop_index("idx_query_history_project_table", "query_history")
    op.drop_table("query_history")
    op.drop_index("idx_schema_change_alerts_connection_id", "schema_change_alerts")
    op.drop_table("schema_change_alerts")
    op.drop_index("idx_dashboard_versions_dashboard_id", "dashboard_versions")
    op.drop_table("dashboard_versions")
    op.drop_column("widgets", "chart_data")
    op.drop_column("widgets", "last_refreshed_at")
    op.drop_column("widgets", "retry_count")
    op.drop_column("widgets", "validation_status")
