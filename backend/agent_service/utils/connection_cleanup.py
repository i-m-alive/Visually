"""Remove database connections that existed only to serve a deleted dashboard.

When a report is imported, the platform often creates a connection just for it:
  • an "Offline (imported tables)" vly_offline connection (always 1:1 with its
    dashboard), or
  • an auto-created live connection (analyst connect-on-import flow), tagged with
    connection_options.auto_created = True.

Deleting the report used to leave these behind, so the project's connection list
slowly filled with duplicate orphans. This helper deletes such connections when the
dashboard goes away — but only when nothing else still references them, and never
touches a manually-managed project connection (no auto_created marker).
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import select, delete as sa_delete, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession


def _as_uuid(v) -> uuid.UUID | None:
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        return None


async def cleanup_orphaned_connections(
    db: AsyncSession,
    dashboard_id: uuid.UUID,
    candidate_conn_ids: Iterable,
) -> list[str]:
    """Delete connections that existed only for the now-deleted dashboard.

    Call AFTER the dashboard + its widgets are deleted and flushed, so this
    dashboard no longer counts as a live reference. Does NOT commit — the caller
    commits the whole delete as one transaction.

    Returns the string ids of the connections that were removed.
    """
    from shared.models.database_connections import DatabaseConnection, DbType
    from shared.models.widgets import Widget as WidgetModel
    from shared.models.dashboards import Dashboard
    from shared.models.schema_snapshots import SchemaSnapshot
    from shared.models.phase2 import SchemaChangeAlert, QueryHistory

    # Build the candidate set: ids passed in (from the dashboard's widgets/layout)
    # plus any vly_offline connection explicitly bound to this dashboard.
    candidates: set[uuid.UUID] = set()
    for cid in candidate_conn_ids:
        u = _as_uuid(cid)
        if u is not None:
            candidates.add(u)

    offline_bound = (
        await db.execute(
            select(DatabaseConnection.id).where(
                DatabaseConnection.db_type == DbType.vly_offline,
                DatabaseConnection.connection_options["dashboard_id"].astext == str(dashboard_id),
            )
        )
    ).scalars().all()
    candidates.update(offline_bound)

    deleted: list[str] = []
    for cid in candidates:
        conn = (
            await db.execute(select(DatabaseConnection).where(DatabaseConnection.id == cid))
        ).scalar_one_or_none()
        if conn is None:
            continue

        opts = conn.connection_options or {}
        is_offline = conn.db_type == DbType.vly_offline
        is_auto = bool(opts.get("auto_created"))
        # Only ever auto-remove connections the platform created on the user's behalf.
        if not is_offline and not is_auto:
            continue

        # Still bound to another dashboard's widget? keep it.
        still_used_widget = (
            await db.execute(
                select(WidgetModel.id).where(WidgetModel.connection_id == cid).limit(1)
            )
        ).first()
        if still_used_widget:
            continue

        # Still referenced by another dashboard's layout_config.connection_id? keep it.
        still_used_layout = (
            await db.execute(
                select(Dashboard.id)
                .where(Dashboard.layout_config["connection_id"].astext == str(cid))
                .limit(1)
            )
        ).first()
        if still_used_layout:
            continue

        # Safe to remove. Clear the FK rows that have no ON DELETE CASCADE first
        # (schema_change_alerts → schema_snapshots ordering matters), then the row.
        # schema_metadata + schema_change_alerts.connection_id cascade automatically.
        await db.execute(
            sa_delete(SchemaChangeAlert)
            .where(SchemaChangeAlert.connection_id == cid)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            sa_delete(SchemaSnapshot)
            .where(SchemaSnapshot.connection_id == cid)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            sa_update(QueryHistory)
            .where(QueryHistory.connection_id == cid)
            .values(connection_id=None)
            .execution_options(synchronize_session=False)
        )
        await db.delete(conn)
        deleted.append(str(cid))

    return deleted
