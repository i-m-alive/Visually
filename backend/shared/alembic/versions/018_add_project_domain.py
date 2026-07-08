"""add domain column to projects

Revision ID: 018
Revises: 017
Create Date: 2026-07-08 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "domain",
            sa.String(20),
            nullable=False,
            server_default="recruitment",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "domain")
