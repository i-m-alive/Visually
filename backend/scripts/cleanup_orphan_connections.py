"""One-off cleanup: remove database connections nothing references anymore.

Imports used to leave their connection behind when a report was deleted, so the
platform DB accumulated duplicate orphan connections (the repeated "wahve" redshift
rows in the connection picker). The delete endpoints now clean up going forward;
this script clears the orphans that already exist.

An "orphan" here is a connection that NO widget binds to AND NO dashboard's
layout_config.connection_id points at — i.e. it cannot be reached by any report, so
removing it is safe regardless of how it was created.

Usage (run from backend/, against the target DB):

    # point at the deployed platform DB
    export DATABASE_URL='postgresql+asyncpg://USER:PASS@HOST:5432/visually_platform'

    # 1) DRY RUN — just list what would be removed (default, deletes nothing)
    python -m scripts.cleanup_orphan_connections

    # 2) APPLY — actually delete the orphans listed above
    python -m scripts.cleanup_orphan_connections --apply

    # optional: restrict to specific db types, e.g. only offline + redshift
    python -m scripts.cleanup_orphan_connections --apply --types vly_offline,redshift
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

# Works both as `python -m scripts.cleanup_orphan_connections` and direct execution.
try:
    from shared.database import AsyncSessionLocal, DATABASE_URL
except ModuleNotFoundError:  # pragma: no cover - allow `python scripts/...py`
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared.database import AsyncSessionLocal, DATABASE_URL


FIND_ORPHANS = text(
    """
    SELECT c.id, c.name, c.db_type, c.host, c.database_name, c.project_id, c.created_at
    FROM database_connections c
    WHERE NOT EXISTS (
        SELECT 1 FROM widgets w WHERE w.connection_id = c.id
    )
    AND NOT EXISTS (
        SELECT 1 FROM dashboards d
        WHERE d.layout_config ->> 'connection_id' = c.id::text
    )
    ORDER BY c.db_type, c.created_at
    """
)


async def _delete_connection(session, cid) -> None:
    """Clear the FK rows that have no ON DELETE CASCADE, then the connection.

    Order matters: schema_change_alerts reference schema_snapshots, so the alerts
    must go before the snapshots. schema_metadata + schema_change_alerts.connection_id
    cascade automatically; vly_offline_tables already cascaded with their dashboard.
    """
    await session.execute(
        text("DELETE FROM schema_change_alerts WHERE connection_id = :id"), {"id": cid}
    )
    await session.execute(
        text("DELETE FROM schema_snapshots WHERE connection_id = :id"), {"id": cid}
    )
    await session.execute(
        text("UPDATE query_history SET connection_id = NULL WHERE connection_id = :id"),
        {"id": cid},
    )
    await session.execute(
        text("DELETE FROM database_connections WHERE id = :id"), {"id": cid}
    )


async def main() -> None:
    apply = "--apply" in sys.argv
    types: set[str] | None = None
    for i, a in enumerate(sys.argv):
        if a == "--types" and i + 1 < len(sys.argv):
            types = {t.strip() for t in sys.argv[i + 1].split(",") if t.strip()}

    masked = DATABASE_URL
    if "@" in masked:  # hide credentials in the printed banner
        masked = masked.split("@", 1)[0].rsplit(":", 1)[0] + ":***@" + masked.split("@", 1)[1]
    print(f"DB: {masked}")
    print(f"Mode: {'APPLY (will delete)' if apply else 'DRY RUN (no changes)'}")
    if types:
        print(f"Restricted to db_type in: {sorted(types)}")
    print("-" * 72)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(FIND_ORPHANS)).all()
        if types is not None:
            rows = [r for r in rows if str(r.db_type) in types]

        if not rows:
            print("No orphan connections found — nothing to do.")
            return

        by_type: dict[str, int] = {}
        for r in rows:
            by_type[str(r.db_type)] = by_type.get(str(r.db_type), 0) + 1
            loc = r.host or ""
            if r.database_name:
                loc = f"{loc}/{r.database_name}" if loc else r.database_name
            print(f"  [{r.db_type:<12}] {str(r.id)}  {r.name}  {('· ' + loc) if loc else ''}")

        print("-" * 72)
        print(f"Orphans found: {len(rows)}  (" + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())) + ")")

        if not apply:
            print("\nDRY RUN — nothing deleted. Re-run with --apply to remove these.")
            return

        removed = 0
        for r in rows:
            try:
                await _delete_connection(session, r.id)
                await session.commit()  # one transaction per connection
                removed += 1
            except Exception as exc:  # noqa: BLE001
                await session.rollback()
                print(f"  ! skipped {r.id} ({r.name}): {exc}")
        print(f"\nDeleted {removed}/{len(rows)} orphan connection(s).")


if __name__ == "__main__":
    asyncio.run(main())
