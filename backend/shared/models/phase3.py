import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, Boolean, BigInteger, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class ScreenshotJob(Base):
    __tablename__ = "screenshot_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    uploaded_files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    chart_manifest: Mapped[dict] = mapped_column(JSONB, nullable=True)
    result_dashboard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dashboards.id"), nullable=True)
    total_charts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmed_charts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ChartReplicationState(Base):
    __tablename__ = "chart_replication_states"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("screenshot_jobs.id", ondelete="CASCADE"), nullable=False)
    chart_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chart_spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_sql: Mapped[str] = mapped_column(Text, nullable=True)
    validation_score: Mapped[float] = mapped_column(Float, nullable=True)
    validation_details: Mapped[dict] = mapped_column(JSONB, nullable=True)
    retry_feedback: Mapped[str] = mapped_column(Text, nullable=True)
    hint_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hint_options: Mapped[dict] = mapped_column(JSONB, nullable=True)
    hint_response: Mapped[str] = mapped_column(Text, nullable=True)
    widget_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("widgets.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class HintQueueEntry(Base):
    __tablename__ = "hint_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("screenshot_jobs.id"), nullable=False)
    chart_replication_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chart_replication_states.id"), nullable=False)
    hint_type: Mapped[str] = mapped_column(String(30), nullable=False)
    options: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_shown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    user_response: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
