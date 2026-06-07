"""Add filter_config to dashboards and base_sql/filterable_columns to widgets

Revision ID: 005
Revises: 004
Create Date: 2024-01-05 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dashboards", sa.Column("filter_config", postgresql.JSONB(), nullable=True))
    op.add_column("widgets", sa.Column("base_sql", sa.Text(), nullable=True))
    op.add_column("widgets", sa.Column("filterable_columns", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("dashboards", "filter_config")
    op.drop_column("widgets", "base_sql")
    op.drop_column("widgets", "filterable_columns")
