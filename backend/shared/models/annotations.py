import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Float, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from shared.database import Base


class DashboardAnnotation(Base):
    __tablename__ = "dashboard_annotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    widget_id = Column(UUID(as_uuid=True), ForeignKey("widgets.id", ondelete="SET NULL"), nullable=True, index=True)
    created_by = Column(String(128), nullable=False)
    author_name = Column(String(255), nullable=True, default="Anonymous")
    content = Column(Text, nullable=False)
    x_percent = Column(Float, nullable=True)
    y_percent = Column(Float, nullable=True)
    color = Column(String(20), default="#3B82F6")
    is_resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
