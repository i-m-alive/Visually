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


async def run_dashboard_refresh(dashboard_id: str) -> None:
    """Re-run all widget SQL for a dashboard and persist chart_data in DB."""
    try:
        from shared.database import AsyncSessionLocal
        from shared.models.dashboards import Dashboard
        from shared.models.widgets import Widget as WidgetModel
        from agent_service.utils.http_clients import call_query_executor
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            dash_result = await db.execute(
                select(Dashboard).where(Dashboard.id == dashboard_id)
            )
            dash = dash_result.scalar_one_or_none()
            if not dash:
                log.warning("Scheduler: dashboard %s not found", dashboard_id)
                return

            widgets_result = await db.execute(
                select(WidgetModel).where(WidgetModel.dashboard_id == dashboard_id)
            )
            widgets = widgets_result.scalars().all()

            refreshed = 0
            for w in widgets:
                sql = w.base_sql or w.sql_query
                conn_id = str(w.connection_id) if w.connection_id else None
                if not sql or not conn_id:
                    continue
                try:
                    result = await call_query_executor(conn_id, sql, row_limit=500)
                    if result and not result.get("error"):
                        w.chart_data = {
                            "rows": result.get("rows", []),
                            "columns": result.get("columns", []),
                        }
                        w.config = {
                            **(w.config or {}),
                            "updated_at": int(datetime.utcnow().timestamp() * 1000),
                        }
                        refreshed += 1
                except Exception as exc:
                    log.warning("Scheduler: widget %s failed: %s", w.id, exc)

            await db.commit()
            log.info("Scheduler: refreshed %d/%d widgets for dashboard %s", refreshed, len(widgets), dashboard_id)
    except Exception as exc:
        log.exception("Scheduler: refresh failed for dashboard %s: %s", dashboard_id, exc)


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
