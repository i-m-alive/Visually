import uuid
from datetime import datetime
from sqlalchemy import String, Integer, Boolean, Text, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base
import enum


class DbType(str, enum.Enum):
    postgresql = "postgresql"
    mysql = "mysql"
    sqlite = "sqlite"
    bigquery = "bigquery"
    snowflake = "snowflake"
    redshift = "redshift"
    mssql = "mssql"
    rest_api = "rest_api"
    csv = "csv"


class DatabaseConnection(Base):
    __tablename__ = "database_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    db_type: Mapped[DbType] = mapped_column(SAEnum(DbType, native_enum=False), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    database_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    ssl_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    connection_options: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="connections")
    schema_snapshots = relationship("SchemaSnapshot", back_populates="connection")
