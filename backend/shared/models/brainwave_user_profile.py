import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID
from shared.database import Base


class BrainwaveUserProfile(Base):
    """Platform-level record mapping a Visually user (email) to their Brainwave identity.

    Scoped by user_email only — no project_id, no connection_id.
    A Brainwave employee is a Brainwave employee regardless of which Visually
    project they're working in or whether they have a project at all (end_user role).

    db_name must match exactly how the person's name appears in Brainwave ownership
    columns: clientadvisor, qualifiername, placementspecialist, relationshipmanager.
    qualifier_id is bqp_user.userid for integer-FK joins (bqp_interview.qualifierid).
    """
    __tablename__ = "brainwave_user_profiles"
    __table_args__ = (
        UniqueConstraint("user_email", name="uq_brainwave_profile_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    # qualifying_specialist | client_advisor | placement_specialist
    # | relationship_manager | vp | admin
    brainwave_role: Mapped[str] = mapped_column(String(64), nullable=False)
    # Name exactly as stored in Brainwave DB ownership columns
    db_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # bqp_user.userid for bqp_interview.qualifierid integer-FK joins
    qualifier_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True for developers/admins who can use X-Impersonate-Role header
    can_impersonate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Email of the admin who granted access (audit trail)
    added_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
