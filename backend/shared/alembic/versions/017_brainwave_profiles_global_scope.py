"""brainwave_user_profiles: drop project_id, make user_email the global unique key

The original design scoped profiles per-project which broke for end_user-role
accounts (analysts who can't create projects) and for builders who share the same
Redshift connection across different projects. The profile is now a platform-level
record keyed purely by user_email — it travels with the person, not the project.

Revision ID: 017
Revises: 016
Create Date: 2026-07-01
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old compound unique constraint and FK before removing the column
    op.drop_constraint(
        "uq_brainwave_profile_project_email",
        "brainwave_user_profiles",
        type_="unique",
    )
    op.drop_constraint(
        "brainwave_user_profiles_project_id_fkey",
        "brainwave_user_profiles",
        type_="foreignkey",
    )
    op.drop_column("brainwave_user_profiles", "project_id")

    # user_email is now the sole unique key (platform-wide)
    op.create_unique_constraint(
        "uq_brainwave_profile_email",
        "brainwave_user_profiles",
        ["user_email"],
    )

    # Audit trail — who granted this access
    op.add_column(
        "brainwave_user_profiles",
        sa.Column("added_by", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("brainwave_user_profiles", "added_by")
    op.drop_constraint("uq_brainwave_profile_email", "brainwave_user_profiles", type_="unique")
    op.add_column(
        "brainwave_user_profiles",
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_brainwave_profile_project_email",
        "brainwave_user_profiles",
        ["project_id", "user_email"],
    )
