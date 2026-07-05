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

            # RLS enforcement: scheduled refreshes persist chart_data that public
            # snapshot links serve later — apply the dashboard's catch-all
            # policies so cached data is never broader than any viewer may see.
            from agent_service.utils.rls import fetch_rls_clauses, inject_rls
            rls_clauses = await fetch_rls_clauses(db, dash_uuid)
            if rls_clauses:
                log.info("Scheduler: applying %d RLS clause(s) to dashboard %s",
                         len(rls_clauses), dashboard_id)

            for w in widgets:
                sql = w.base_sql or w.sql_query
                conn_id = str(w.connection_id) if w.connection_id else fallback_conn_id
                if not sql or not conn_id:
                    summary["skipped"] += 1
                    continue
                try:
                    result = await call_query_executor(conn_id, inject_rls(sql, rls_clauses), row_limit=500)
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


# ── Agent alerts evaluation ───────────────────────────────────────────────────

async def evaluate_alerts() -> None:
    """Evaluate every due, active AlertRule: run the widget SQL (RLS applied),
    check the rule, and on trigger generate an LLM explanation + notify."""
    try:
        from shared.database import AsyncSessionLocal
        from shared.models.alerts import AlertRule
        from shared.models.widgets import Widget as WidgetModel
        from agent_service.utils.http_clients import call_query_executor
        from agent_service.utils.rls import fetch_rls_clauses, inject_rls
        from sqlalchemy import select
        from datetime import timedelta

        now = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(AlertRule).where(AlertRule.is_active == True))  # noqa: E712
            for alert in result.scalars().all():
                due = (alert.last_evaluated_at is None or
                       now - alert.last_evaluated_at >= timedelta(minutes=alert.cadence_minutes))
                if not due or not alert.widget_id:
                    continue
                try:
                    wr = await db.execute(select(WidgetModel).where(WidgetModel.id == alert.widget_id))
                    w = wr.scalar_one_or_none()
                    sql = (w.base_sql or w.sql_query) if w else None
                    if not w or not sql or not w.connection_id:
                        alert.last_evaluated_at = now
                        continue
                    clauses = await fetch_rls_clauses(db, alert.dashboard_id)
                    res = await call_query_executor(str(w.connection_id), inject_rls(sql, clauses), row_limit=500)
                    rows = res.get("rows") or []
                    alert.last_evaluated_at = now
                    if res.get("error") or not rows:
                        continue
                    # Latest numeric value: rule.column if present, else first numeric col
                    cols = res.get("columns") or []
                    rule = alert.rule or {}
                    target_col = rule.get("column") or next(
                        (c for c in cols if isinstance(rows[-1].get(c), (int, float))), None)
                    if not target_col:
                        continue
                    series = [r.get(target_col) for r in rows if isinstance(r.get(target_col), (int, float))]
                    if not series:
                        continue
                    latest = series[-1]
                    triggered, reason = False, ""
                    if rule.get("type") == "threshold":
                        op, val = rule.get("op", "<"), float(rule.get("value", 0))
                        triggered = ((op == "<" and latest < val) or (op == "<=" and latest <= val)
                                     or (op == ">" and latest > val) or (op == ">=" and latest >= val)
                                     or (op == "=" and latest == val))
                        reason = f"latest {target_col} = {latest} (condition: {op} {val})"
                    else:  # anomaly
                        if len(series) >= 5:
                            mean = sum(series) / len(series)
                            var = sum((x - mean) ** 2 for x in series) / len(series)
                            std = var ** 0.5
                            sigma = float(rule.get("sigma", 2))
                            triggered = std > 0 and abs(latest - mean) > sigma * std
                            reason = f"latest {target_col} = {latest} vs mean {mean:.1f} (±{sigma}σ = {sigma * std:.1f})"
                    if triggered:
                        explanation = reason
                        try:
                            from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
                            explanation = await bedrock_invoke(
                                model_id=BEDROCK_HAIKU_MODEL,
                                system_prompt="You write 2-sentence data alert notifications. Be specific with numbers, plain language, no fluff.",
                                user_message=(f"Alert '{alert.name}' on widget '{w.title}' fired. "
                                              f"Condition: {alert.condition_text}. Evidence: {reason}. "
                                              f"Recent values of {target_col}: {series[-10:]}"),
                                max_tokens=200, temperature=0.2,
                            )
                        except Exception:
                            pass
                        alert.last_triggered_at = now
                        alert.last_result = {"triggered": True, "value": latest,
                                             "explanation": explanation, "at": now.isoformat()}
                        log.info("Alert TRIGGERED: %s — %s", alert.name, reason)
                        if alert.channel == "email" and alert.email:
                            from shared.email import send_email
                            await send_email(alert.email, f"⚠ Alert: {alert.name}",
                                             f"<h3>{alert.name}</h3><p>{explanation}</p>")
                    else:
                        alert.last_result = {"triggered": False, "value": latest, "at": now.isoformat()}
                except Exception as exc:
                    log.warning("Alert %s evaluation failed: %s", alert.id, exc)
            await db.commit()
    except Exception as exc:
        log.warning("evaluate_alerts tick failed: %s", exc)


# ── Snapshot email delivery ───────────────────────────────────────────────────

async def process_snapshot_schedules() -> None:
    """Send due email snapshots. Rows are created on the share page; before
    this job existed they were stored but never delivered."""
    try:
        from shared.database import AsyncSessionLocal
        from shared.models.snapshot_schedules import SnapshotSchedule
        from shared.models.dashboards import Dashboard
        from shared.models.widgets import Widget as WidgetModel
        from shared.email import send_email, is_email_configured
        from sqlalchemy import select
        from datetime import timedelta

        if not is_email_configured():
            return  # logged loudly on actual send attempts; skip the scan quietly
        now = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(SnapshotSchedule).where(
                SnapshotSchedule.is_active == True,          # noqa: E712
                SnapshotSchedule.next_send_at <= now,
            ))
            for sched in result.scalars().all():
                try:
                    dr = await db.execute(select(Dashboard).where(Dashboard.id == sched.dashboard_id))
                    dash = dr.scalar_one_or_none()
                    if not dash:
                        sched.is_active = False
                        continue
                    wr = await db.execute(select(WidgetModel).where(WidgetModel.dashboard_id == dash.id))
                    widgets = wr.scalars().all()
                    # Headline values from cached chart_data (KPIs first)
                    lines = []
                    for w in widgets[:12]:
                        cd = w.chart_data if isinstance(w.chart_data, dict) else {}
                        vals = cd.get("values") or []
                        if w.chart_type in ("kpi", "gauge") and vals:
                            lines.append(f"<li><strong>{w.title}:</strong> {vals[0]}</li>")
                    digest = ""
                    if getattr(sched, "include_ai_summary", False):
                        try:
                            from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
                            widget_summary = "; ".join(
                                f"{w.title} ({w.chart_type}, {len((w.chart_data or {}).get('rows', []))} rows)"
                                for w in widgets[:15]
                            )
                            digest = await bedrock_invoke(
                                model_id=BEDROCK_HAIKU_MODEL,
                                system_prompt="Write a 3-sentence executive digest of this dashboard for an email. Plain language.",
                                user_message=f"Dashboard '{dash.name}' widgets: {widget_summary}",
                                max_tokens=300, temperature=0.3,
                            )
                        except Exception:
                            pass
                    html = (
                        f"<h2>{dash.name}</h2>"
                        + (f"<p>{digest}</p>" if digest else "")
                        + (f"<ul>{''.join(lines)}</ul>" if lines else "")
                        + "<p style='color:#888;font-size:12px'>Scheduled snapshot from Visually.</p>"
                    )
                    ok = await send_email(sched.email, f"📊 {dash.name} — scheduled snapshot", html)
                    if ok:
                        sched.last_sent_at = now
                    # Advance next_send_at by frequency
                    freq = (getattr(sched, "frequency", None) or "daily").lower()
                    step = {"daily": timedelta(days=1), "weekly": timedelta(weeks=1),
                            "monthly": timedelta(days=30)}.get(freq, timedelta(days=1))
                    base = sched.next_send_at or now
                    while base <= now:
                        base += step
                    sched.next_send_at = base
                    log.info("Snapshot email %s to %s (dashboard %s)",
                             "sent" if ok else "FAILED", sched.email, dash.name)
                except Exception as exc:
                    log.warning("Snapshot schedule %s failed: %s", sched.id, exc)
            await db.commit()
    except Exception as exc:
        log.warning("process_snapshot_schedules tick failed: %s", exc)


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
    # Recurring platform jobs: alert evaluation + snapshot email delivery
    _scheduler.add_job(evaluate_alerts, "interval", minutes=5,
                       id="evaluate_alerts", replace_existing=True)
    _scheduler.add_job(process_snapshot_schedules, "interval", minutes=1,
                       id="snapshot_emails", replace_existing=True)
    log.info("APScheduler started (with alerts + snapshot email jobs)")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("APScheduler stopped")
