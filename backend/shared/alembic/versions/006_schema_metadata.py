"""Add schema_table_metadata and schema_column_metadata tables

Revision ID: 006
Revises: 005
Create Date: 2024-01-06 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schema_table_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_snapshot_version", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.String(512), nullable=False),
        sa.Column("business_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("grain", sa.Text(), nullable=True),
        sa.Column("is_fact_table", sa.Boolean(), nullable=True),
        sa.Column("use_for", postgresql.JSONB(), nullable=True),
        sa.Column("never_use_for", postgresql.JSONB(), nullable=True),
        sa.Column("key_metric_cols", postgresql.JSONB(), nullable=True),
        sa.Column("key_dimension_cols", postgresql.JSONB(), nullable=True),
        sa.Column("key_date_cols", postgresql.JSONB(), nullable=True),
        sa.Column("generation_method", sa.String(64), nullable=False, server_default="llm_sample_rows"),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schema_table_metadata_connection_id", "schema_table_metadata", ["connection_id"])

    op.create_table(
        "schema_column_metadata",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("database_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_snapshot_version", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.String(512), nullable=False),
        sa.Column("column_name", sa.String(255), nullable=False),
        sa.Column("business_name", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("semantic_type", sa.String(32), nullable=True),
        sa.Column("fk_target_table", sa.String(512), nullable=True),
        sa.Column("fk_target_column", sa.String(255), nullable=True),
        sa.Column("fk_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fk_confirmation_score", sa.Float(), nullable=True),
        sa.Column("example_values", postgresql.JSONB(), nullable=True),
        sa.Column("is_kpi_metric", sa.Boolean(), nullable=True),
        sa.Column("is_dimension", sa.Boolean(), nullable=True),
        sa.Column("is_filter_eligible", sa.Boolean(), nullable=True),
        sa.Column("generation_method", sa.String(64), nullable=False, server_default="llm_sample_rows"),
        sa.Column("generated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_schema_column_metadata_connection_id", "schema_column_metadata", ["connection_id"])


def downgrade() -> None:
    op.drop_index("ix_schema_column_metadata_connection_id", table_name="schema_column_metadata")
    op.drop_table("schema_column_metadata")
    op.drop_index("ix_schema_table_metadata_connection_id", table_name="schema_table_metadata")
    op.drop_table("schema_table_metadata")
