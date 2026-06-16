import uuid
from datetime import datetime
from sqlalchemy import Column, String, Text, Integer, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class DashboardBookmark(Base):
    __tablename__ = "dashboard_bookmarks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id = Column(UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by = Column(String(128), nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    filter_state = Column(JSONB, default=dict)
    page_index = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
