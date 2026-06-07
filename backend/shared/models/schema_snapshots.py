import uuid
from datetime import datetime
from sqlalchemy import Integer, Float, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class SchemaSnapshot(Base):
    __tablename__ = "schema_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("database_connections.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_document: Mapped[dict] = mapped_column(JSONB, nullable=False)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    crawl_duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    connection = relationship("DatabaseConnection", back_populates="schema_snapshots")
