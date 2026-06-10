"""canvas share tokens and collaborators

Revision ID: 007
Revises: 006
Create Date: 2026-06-09 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canvas_share_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dashboard_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("mode", sa.String(20), nullable=False, server_default="live"),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_cst_dashboard", "canvas_share_tokens", ["dashboard_id"])
    op.create_index("idx_cst_token_hash", "canvas_share_tokens", ["token_hash"])

    op.create_table(
        "canvas_collaborators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dashboard_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("idx_cc_dashboard", "canvas_collaborators", ["dashboard_id"])
    op.create_index("idx_cc_user", "canvas_collaborators", ["user_id"])
    op.create_unique_constraint(
        "uq_canvas_collaborator", "canvas_collaborators", ["dashboard_id", "user_id"]
    )


def downgrade() -> None:
    op.drop_table("canvas_collaborators")
    op.drop_index("idx_cst_token_hash")
    op.drop_index("idx_cst_dashboard")
    op.drop_table("canvas_share_tokens")
