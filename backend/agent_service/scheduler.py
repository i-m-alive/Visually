"""APScheduler-based scheduled refresh for dashboards.

Each dashboard that has layout_config.refresh_schedule = {enabled: true, cron: "..."}
gets a cron job registered here. Jobs re-run all widget SQL and persist fresh chart_data.

Usage:
  from agent_service.scheduler import start_scheduler, stop_scheduler
  # called from FastAPI lifespan
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    _HAS_APSCHEDULER = True
except ImportError:
    _HAS_APSCHEDULER = False
    log.warning("APScheduler not installed — scheduled refresh disabled. Install with: pip install apscheduler")

_scheduler: Optional["AsyncIOScheduler"] = None  # type: ignore[type-arg]


async def run_dashboard_refresh(dashboard_id: str, only_widget_id: str | None = None) -> dict:
    """Re-run widget SQL for a dashboard and persist fresh chart_data in DB.

    When `only_widget_id` is given, refresh just that one widget; otherwise all.
    Returns {refreshed, total, skipped, errors} so callers can report the real
    outcome instead of blindly claiming success."""
    import uuid as _uuid
    summary: dict = {"refreshed": 0, "total": 0, "skipped": 0, "errors": []}
    try:
        from shared.database import AsyncSessionLocal
        from shared.models.dashboards import Dashboard
        from shared.models.widgets import Widget as WidgetModel
        from agent_service.utils.http_clients import call_query_executor
        from sqlalchemy import select

        # The id columns are UUID(as_uuid=True); compare with a real UUID, not a str.
        try:
            dash_uuid = _uuid.UUID(str(dashboard_id))
        except ValueError:
            log.warning("Scheduler: invalid dashboard id %s", dashboard_id)
            return summary

        async with AsyncSessionLocal() as db:
            dash_result = await db.execute(
                select(Dashboard).where(Dashboard.id == dash_uuid)
            )
            dash = dash_result.scalar_one_or_none()
            if not dash:
                log.warning("Scheduler: dashboard %s not found", dashboard_id)
                return summary

            widgets_result = await db.execute(
                select(WidgetModel).where(WidgetModel.dashboard_id == dash_uuid)
            )
            widgets = list(widgets_result.scalars().all())
            if only_widget_id:
                widgets = [w for w in widgets if str(w.id) == str(only_widget_id)]
            summary["total"] = len(widgets)

            # Fallback connection for widgets that aren't individually bound.
            # Imported / dashboard-level-bound canvases keep the connection on
            # layout_config.connection_id (or the project), NOT on every widget.
            # Without this fallback, refresh silently skips every such widget and
            # the displayed data never changes even though a connection exists.
            fallback_conn_id: "str | None" = None
            lc = dash.layout_config or {}
            if lc.get("connection_id"):
                fallback_conn_id = str(lc["connection_id"])
            if not fallback_conn_id:
                try:
                    from shared.models.database_connections import DatabaseConnection
                    pc = await db.execute(
                        select(DatabaseConnection).where(
                            DatabaseConnection.project_id == dash.project_id,
                            DatabaseConnection.is_active == True,
                        ).limit(1)
                    )
                    pcobj = pc.scalar_one_or_none()
                    if pcobj:
                        fallback_conn_id = str(pcobj.id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Scheduler: fallback connection lookup failed: %s", exc)
            log.info(
                "Scheduler: refresh dashboard %s  widgets=%d  fallback_conn=%s",
                dashboard_id, len(widgets), (fallback_conn_id or "none"),
            )

            for w in widgets:
                sql = w.base_sql or w.sql_query
                conn_id = str(w.connection_id) if w.connection_id else fallback_conn_id
                if not sql or not conn_id:
                    summary["skipped"] += 1
                    continue
                try:
                    result = await call_query_executor(conn_id, sql, row_limit=500)
                    if result and not result.get("error"):
                        rows = result.get("rows", [])
                        columns = result.get("columns", [])
                        # Build the FULL chart_data payload (rows, columns, labels,
                        # values, series, matrix, …) — NOT just rows/columns. Without
                        # this a KPI loses chart_data.values and other chart types lose
                        # their derived fields after a refresh, so they render blank.
                        try:
                            from agent_service.agents.orchestrator import Orchestrator
                            payload = Orchestrator._build_chart_data_for_type(w.chart_type or "bar", rows, columns)
                        except Exception:
                            payload = {"rows": rows, "columns": columns}
                        payload["chart_type"] = w.chart_type
                        payload["title"] = w.title
                        # Preserve axis labels already captured on the widget.
                        prev = w.chart_data if isinstance(w.chart_data, dict) else {}
                        for k in ("x_axis_label", "y_axis_label"):
                            if prev.get(k) and not payload.get(k):
                                payload[k] = prev[k]
                        w.chart_data = payload
                        w.config = {
                            **(w.config or {}),
                            "updated_at": int(datetime.utcnow().timestamp() * 1000),
                        }
                        summary["refreshed"] += 1
                    else:
                        summary["errors"].append(
                            {"widget_id": str(w.id), "error": (result or {}).get("error", "unknown")}
                        )
                except Exception as exc:
                    summary["errors"].append({"widget_id": str(w.id), "error": str(exc)})
                    log.warning("Scheduler: widget %s failed: %s", w.id, exc)

            await db.commit()
            log.info(
                "Scheduler: refreshed %d/%d widgets (skipped %d, errors %d) for dashboard %s",
                summary["refreshed"], summary["total"], summary["skipped"], len(summary["errors"]), dashboard_id,
            )
    except Exception as exc:
        log.exception("Scheduler: refresh failed for dashboard %s: %s", dashboard_id, exc)
    return summary


def _make_job_id(dashboard_id: str) -> str:
    return f"refresh_{dashboard_id}"


def reload_job_for_dashboard(dashboard_id: str, schedule: dict) -> None:
    """Add, update, or remove the cron job for a dashboard."""
    if not _HAS_APSCHEDULER or _scheduler is None:
        return
    job_id = _make_job_id(dashboard_id)
    # Remove existing job if present
    existing = _scheduler.get_job(job_id)
    if existing:
        existing.remove()
    if not schedule.get("enabled") or not schedule.get("cron"):
        return
    try:
        trigger = CronTrigger.from_crontab(schedule["cron"], timezone=schedule.get("timezone", "UTC"))
        _scheduler.add_job(
            run_dashboard_refresh,
            trigger=trigger,
            args=[dashboard_id],
            id=job_id,
            name=f"Refresh dashboard {dashboard_id[:8]}",
            replace_existing=True,
        )
        log.info("Scheduler: registered cron '%s' for dashboard %s", schedule["cron"], dashboard_id)
    except Exception as exc:
        log.warning("Scheduler: could not register job for %s: %s", dashboard_id, exc)


async def _load_all_schedules() -> None:
    """On startup, scan all dashboards and register scheduled jobs."""
    try:
        from shared.database import AsyncSessionLocal
        from shared.models.dashboards import Dashboard
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Dashboard).where(Dashboard.is_archived == False))
            dashboards = result.scalars().all()
            for dash in dashboards:
                lc = dash.layout_config or {}
                schedule = lc.get("refresh_schedule")
                if schedule and schedule.get("enabled") and schedule.get("cron"):
                    reload_job_for_dashboard(str(dash.id), schedule)
        log.info("Scheduler: boot scan complete")
    except Exception as exc:
        log.warning("Scheduler: boot scan failed: %s", exc)


def start_scheduler() -> None:
    global _scheduler
    if not _HAS_APSCHEDULER:
        return
    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.start()
    # Load existing schedules after the event loop is running
    asyncio.get_event_loop().create_task(_load_all_schedules())
    log.info("APScheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("APScheduler stopped")
