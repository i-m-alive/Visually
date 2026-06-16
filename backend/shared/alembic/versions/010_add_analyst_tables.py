"""add analyst tables: annotations, bookmarks, snapshot_schedules

Revision ID: 010
Revises: 009
Create Date: 2026-06-10
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dashboard_annotations',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dashboard_id', UUID(as_uuid=True), sa.ForeignKey('dashboards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('widget_id', UUID(as_uuid=True), sa.ForeignKey('widgets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('author_name', sa.String(255), nullable=True),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('x_percent', sa.Float, nullable=True),
        sa.Column('y_percent', sa.Float, nullable=True),
        sa.Column('color', sa.String(20), server_default='#3B82F6'),
        sa.Column('is_resolved', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_dashboard_annotations_dashboard_id', 'dashboard_annotations', ['dashboard_id'])
    op.create_index('ix_dashboard_annotations_widget_id', 'dashboard_annotations', ['widget_id'])

    op.create_table(
        'dashboard_bookmarks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dashboard_id', UUID(as_uuid=True), sa.ForeignKey('dashboards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.String(128), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('filter_state', JSONB, server_default='{}'),
        sa.Column('page_index', sa.Integer, server_default='0'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_dashboard_bookmarks_dashboard_id', 'dashboard_bookmarks', ['dashboard_id'])

    op.create_table(
        'snapshot_schedules',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dashboard_id', UUID(as_uuid=True), sa.ForeignKey('dashboards.id', ondelete='CASCADE'), nullable=False),
        sa.Column('share_token_id', UUID(as_uuid=True), sa.ForeignKey('canvas_share_tokens.id', ondelete='CASCADE'), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('frequency', sa.String(20), server_default='daily'),
        sa.Column('day_of_week', sa.Integer, nullable=True),
        sa.Column('hour_utc', sa.Integer, server_default='8'),
        sa.Column('timezone', sa.String(50), server_default='UTC'),
        sa.Column('include_ai_summary', sa.Boolean, server_default='true'),
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('last_sent_at', sa.DateTime, nullable=True),
        sa.Column('next_send_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_snapshot_schedules_dashboard_id', 'snapshot_schedules', ['dashboard_id'])


def downgrade() -> None:
    op.drop_table('snapshot_schedules')
    op.drop_table('dashboard_bookmarks')
    op.drop_table('dashboard_annotations')
