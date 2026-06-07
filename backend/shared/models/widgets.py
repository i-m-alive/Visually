import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class Widget(Base):
    __tablename__ = "widgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dashboards.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    widget_type: Mapped[str] = mapped_column(String(50), nullable=False)
    chart_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sql_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    connection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("database_connections.id"), nullable=True)
    position_x: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    position_y: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    chart_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    dashboard = relationship("Dashboard", back_populates="widgets")
