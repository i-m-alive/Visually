import uuid
from datetime import datetime

from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from shared.database import Base


class IntelligenceReport(Base):
    """The saved AI intelligence report for a dashboard (one latest per dashboard).

    The intelligence page used to cache its generated analysis only in the browser's
    localStorage, so it silently regenerated on a different device, after a data
    clear, or when the payload exceeded the localStorage quota. This persists the
    analysis server-side so Save/retrieve is durable and consistent across devices,
    and the report is only rebuilt when the user explicitly Syncs or Regenerates.
    """

    __tablename__ = "intelligence_reports"

    # One row per dashboard — the latest saved report (upserted).
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # The full ExecutiveAnalysis JSON the frontend renders.
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # The data freshness this report was built on (dashboard last_synced/regenerated
    # stamp at save time) — lets the UI tell when it might be stale, without auto-rebuilding.
    data_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
