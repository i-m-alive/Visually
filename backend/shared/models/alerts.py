"""Agent alerts: watch a widget's data and notify with an explanation."""
import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Text, ForeignKey, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class AlertRule(Base):
    """A plain-language alert condition on a widget, evaluated on a schedule.

    condition_text is what the user typed ("tell me if daily applications drop
    below 5"); rule is the agent-parsed machine form:
      {"type": "threshold", "column": "applications", "op": "<", "value": 5}
      {"type": "anomaly", "sigma": 2}
    """
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False
    )
    widget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("widgets.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    condition_text: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cadence_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="inapp")  # inapp | email
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
