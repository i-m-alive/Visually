import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from shared.database import Base


class SnapshotSchedule(Base):
    __tablename__ = "snapshot_schedules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    share_token_id = Column(UUID(as_uuid=True), ForeignKey("canvas_share_tokens.id", ondelete="CASCADE"), nullable=True)
    created_by = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False)
    frequency = Column(String(20), default="daily")
    day_of_week = Column(Integer, nullable=True)
    hour_utc = Column(Integer, default=8)
    timezone = Column(String(50), default="UTC")
    include_ai_summary = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    last_sent_at = Column(DateTime, nullable=True)
    next_send_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
