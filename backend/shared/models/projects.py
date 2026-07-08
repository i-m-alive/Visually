import uuid
from datetime import datetime
from sqlalchemy import String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from shared.database import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Which vertical this project's agent behaviors are tuned for — drives which
    # persona/skill-agents/access-gate apply ("recruitment" | "finance" | "generic").
    # Defaults to "recruitment" so existing projects keep today's behavior unchanged.
    domain: Mapped[str] = mapped_column(String(20), nullable=False, default="recruitment", server_default="recruitment")
    # True once a user has explicitly set `domain` (via the connection settings page
    # or the Query Chat toggle). While False, schema_crawler's post-crawl heuristic
    # (shared.domain_detection) is free to auto-update `domain`; once True, the
    # heuristic never overwrites the user's explicit choice.
    domain_is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    owner = relationship("User", back_populates="projects", foreign_keys=[owner_id])
    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")
    connections = relationship("DatabaseConnection", back_populates="project", cascade="all, delete-orphan")
    dashboards = relationship("Dashboard", back_populates="project", cascade="all, delete-orphan")
