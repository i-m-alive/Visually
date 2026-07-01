"""add brainwave_user_profiles: per-project Brainwave staff identity mapping

Each row maps a Visually user (email) to their identity inside the Brainwave
Redshift database. Stores the name as it appears in ownership columns (clientadvisor,
qualifiername, placementspecialist, relationshipmanager) and the numeric qualifierid
so agents can filter data to the logged-in user's records.

Revision ID: 016
Revises: 015
Create Date: 2026-07-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brainwave_user_profiles",
        sa.Column("id",             sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id",     sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_email",     sa.String(255), nullable=False),
        # Role label: qualifying_specialist | client_advisor | placement_specialist
        #             | relationship_manager | vp | admin
        sa.Column("brainwave_role", sa.String(64),  nullable=False),
        # Name exactly as stored in Brainwave DB ownership columns:
        #   staging.bullhorn_core_job_order.clientadvisor
        #   staging.bullhorn_core_job_order.placementspecialist
        #   staging.bullhorn_core_placement.relationshipmanager
        #   public.mv_interview_notes.qualifiername
        sa.Column("db_name",        sa.String(255), nullable=True),
        # bqp_user.userid — used for bqp_interview.qualifierid integer FK joins
        sa.Column("qualifier_id",   sa.Integer,     nullable=True),
        # True for the developer / admins who can impersonate any role via
        # the X-Impersonate-Role request header
        sa.Column("can_impersonate", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at",      sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("project_id", "user_email", name="uq_brainwave_profile_project_email"),
    )


def downgrade() -> None:
    op.drop_table("brainwave_user_profiles")
