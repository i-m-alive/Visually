import uuid
from datetime import datetime
from sqlalchemy import String, Boolean, Float, Integer, Text, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class SchemaTableMetadata(Base):
    __tablename__ = "schema_table_metadata"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    table_name: Mapped[str] = mapped_column(String(512), nullable=False)   # qualified: "staging.table"
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    grain: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_fact_table: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    use_for: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    never_use_for: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    key_metric_cols: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    key_dimension_cols: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    key_date_cols: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    generation_method: Mapped[str] = mapped_column(
        String(64), default="llm_sample_rows", nullable=False
    )  # "llm_sample_rows" | "llm_columns_only" | "manual"
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SchemaColumnMetadata(Base):
    __tablename__ = "schema_column_metadata"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("database_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    table_name: Mapped[str] = mapped_column(String(512), nullable=False)
    column_name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # pk | fk | metric | dimension | date | identifier | text | flag
    fk_target_table: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fk_target_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fk_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fk_confirmation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    example_values: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_kpi_metric: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_dimension: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_filter_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    generation_method: Mapped[str] = mapped_column(
        String(64), default="llm_sample_rows", nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
