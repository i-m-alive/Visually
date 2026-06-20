import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Boolean, LargeBinary, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB

from shared.database import Base


class VlyOfflineTable(Base):
    """One raw source table bundled inside an imported .vly archive.

    When a canvas is imported WITHOUT a live database connection (offline mode),
    the full table data referenced by the report's widget SQL is unpacked from
    the .vly and persisted here as Parquet bytes. The OfflineStore materializes
    these rows into an in-process DuckDB database so the existing widget / agent
    SQL runs unchanged against the imported snapshot instead of a live DB.

    parquet_bytes holds the table inline (platform-DB blob storage). To move to
    external object storage later, populate `blob_uri` instead and leave
    parquet_bytes null — OfflineStore reads whichever is present, so the swap is
    a one-function change.
    """

    __tablename__ = "vly_offline_tables"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dashboard_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Table name exactly as referenced in widget SQL (may be schema-qualified, e.g. "public.orders").
    table_name: Mapped[str] = mapped_column(String(512), nullable=False)
    # [{"name": "...", "type": "..."}] describing each column.
    columns_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    parquet_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    blob_uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # future external storage
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_dialect: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
