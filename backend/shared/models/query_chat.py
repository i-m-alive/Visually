"""Persistent chat history for the Query feature (ChatGPT/Claude-style).

Messages form a TREE per session (parent_id). Editing a past user message creates
a sibling under the same parent → an alternate branch; regenerating an assistant
reply creates an assistant sibling. The session's active_leaf_id marks which
leaf the "current" conversation path ends at; the visible path is the chain of
ancestors of that leaf. Sibling counts at any node drive the < 1/2 > version UI.
"""
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID, JSONB
from shared.database import Base


class QuerySession(Base):
    __tablename__ = "query_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="New chat", nullable=False)
    # The leaf message of the currently-active branch (plain UUID; no FK to avoid a
    # circular dependency with query_messages).
    active_leaf_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class QueryMessage(Base):
    __tablename__ = "query_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("query_sessions.id", ondelete="CASCADE"), nullable=False)
    # Tree parent (NULL for the root message). Plain UUID — app enforces integrity;
    # session-level CASCADE handles deletion of the whole tree.
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # For assistant messages: the full result payload (output_mode, chart_data,
    # narrative, sql, score, …). Null for user messages.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
