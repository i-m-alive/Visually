"""add vly_offline_tables: bundled raw tables for offline (no-DB) imported canvases

Revision ID: 014
Revises: 013
Create Date: 2026-06-20
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vly_offline_tables',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dashboard_id', UUID(as_uuid=True), sa.ForeignKey('dashboards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('table_name', sa.String(512), nullable=False),
        sa.Column('columns_json', JSONB, nullable=True),
        sa.Column('parquet_bytes', sa.LargeBinary, nullable=True),
        sa.Column('blob_uri', sa.String(1024), nullable=True),
        sa.Column('row_count', sa.Integer, server_default='0', nullable=False),
        sa.Column('truncated', sa.Boolean, server_default='false', nullable=False),
        sa.Column('source_dialect', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_vly_offline_tables_dashboard_id', 'vly_offline_tables', ['dashboard_id'])


def downgrade() -> None:
    op.drop_table('vly_offline_tables')
