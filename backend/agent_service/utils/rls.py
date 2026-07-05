"""
Row-Level Security enforcement helpers, shared by every data-refresh path.

RLS policies were previously applied ONLY on the owner-invoked
/dashboards/{id}/requery-rls endpoint — anonymous share-link refreshes and
scheduled cron refreshes ran unfiltered SQL. These helpers close that gap.

On paths with no authenticated user (public tokens, scheduler), only the
CATCH-ALL policies (user_id IS NULL) can apply — they represent the baseline
restriction every viewer must get.
"""
import re
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def inject_rls(sql: str, clauses: list) -> str:
    """Append active RLS WHERE clauses to a SQL statement."""
    if not clauses or not sql:
        return sql
    sql = sql.rstrip(";")
    combined = " AND ".join(f"({c})" for c in clauses)
    has_where = bool(re.search(r"\bWHERE\b", sql, re.IGNORECASE))
    connector = " AND " if has_where else " WHERE "
    return sql + connector + combined


async def fetch_rls_clauses(
    db: AsyncSession,
    dashboard_id,
    user_id=None,
) -> list:
    """
    Active RLS clauses for a dashboard. With a user_id, that user's policies
    take precedence; otherwise (anonymous/scheduled context) the catch-all
    policies (user_id IS NULL) apply.
    Never raises — RLS lookup failure returns [] so refresh still works,
    but the failure is logged loudly.
    """
    try:
        from shared.models.tier5 import RLSPolicy
        did = dashboard_id if isinstance(dashboard_id, uuid.UUID) else uuid.UUID(str(dashboard_id))
        result = await db.execute(
            select(RLSPolicy).where(
                RLSPolicy.dashboard_id == did,
                RLSPolicy.is_active == True,  # noqa: E712
            )
        )
        policies = result.scalars().all()
        if user_id is not None:
            mine = [p.clause for p in policies if p.user_id == user_id]
            if mine:
                return mine
        return [p.clause for p in policies if p.user_id is None]
    except Exception as exc:
        print(f"[rls] ⚠ policy lookup failed (refresh proceeds UNFILTERED): {exc}", flush=True)
        return []
