"""
Pipeline tools — Phase 2

Query tools for recruitment pipeline analysis and daily briefings.
Targets the actual Redshift schema confirmed from the production database.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_service.agents.tool_agent import AgentContext

# Confirmed table names from the production Redshift DB
_PIPELINE_TABLE  = "staging.bqp_applications_list"
_SCORING_TABLE   = "staging.bqp_audit_application_scored_events"
_ML_TABLE        = "target.job_profile_match_ml"


def _user_filter_fragment(ctx: "AgentContext") -> str:
    """Return a SQL AND fragment for the user's role filter, or empty string.

    Examples:
        "AND placementspecialist = 'Julie Petty'"
        "AND clientadvisor = 'Bob Smith'"
        ""   ← VP / admin / no profile
    """
    from agent_service.agents.user_context_builder import get_sql_filter_clause
    profile = getattr(ctx, "user_profile", None)
    clause = get_sql_filter_clause(profile)
    if clause:
        print(f"[pipeline_tools] applying user filter: {clause!r}", flush=True)
        return f"AND ({clause})"
    return ""


async def get_my_placements(inp: dict, ctx: "AgentContext") -> dict:
    """Get the logged-in user's current active candidates / placements.

    Automatically applies the user's ownership filter — no SQL knowledge needed.
    Inputs: { status?: str, limit?: int }
    Returns: applicant_name, jobtitle, company, status, last_updated
    """
    from agent_service.tools.sql_tool import run_sql
    from agent_service.agents.user_context_builder import get_sql_filter_clause

    profile = getattr(ctx, "user_profile", None)
    if not profile:
        return {
            "rows": [], "row_count": 0,
            "error": "No user profile configured. Ask your admin to assign a Brainwave role.",
        }

    role_key      = profile.get("brainwave_role", "")
    db_name       = (profile.get("db_name") or "").strip()
    safe_name     = db_name.replace("'", "''")
    limit         = min(int(inp.get("limit") or 25), 100)
    status_filter = (inp.get("status") or "").strip()

    if not db_name or role_key in ("vp", "admin"):
        return {
            "rows": [], "row_count": 0,
            "error": "No ownership filter available for your role. Try get_pipeline_summary for a team-wide view.",
        }

    # Build status WHERE part
    status_clause = f"AND a.appstatus = '{_safe(status_filter)}' " if status_filter else ""

    # Role-aware JOIN — ownership columns live in separate tables, not bqp_applications_list
    if role_key in ("placement_specialist", "client_advisor"):
        ownership_col = "placementspecialist" if role_key == "placement_specialist" else "clientadvisor"
        sql = (
            "SELECT "
            "    a.appname                   AS applicant_name, "
            "    a.jobtitle, "
            "    COALESCE(a.companname, '')  AS company, "
            "    a.appstatus                 AS status, "
            "    a.appdateupdated            AS last_updated "
            f"FROM {_PIPELINE_TABLE} a "
            "JOIN staging.bullhorn_core_job_order j ON j.joborderid = a.jobid "
            f"WHERE j.{ownership_col} = '{safe_name}' "
            f"{status_clause}"
            "ORDER BY a.appdateupdated DESC "
            f"LIMIT {limit}"
        )
    elif role_key == "relationship_manager":
        sql = (
            "SELECT "
            "    a.appname                   AS applicant_name, "
            "    a.jobtitle, "
            "    COALESCE(a.companname, '')  AS company, "
            "    a.appstatus                 AS status, "
            "    a.appdateupdated            AS last_updated "
            f"FROM {_PIPELINE_TABLE} a "
            "JOIN staging.bullhorn_core_candidate c ON c.candidateid = a.applicantid "
            f"WHERE c.relationshipmanager = '{safe_name}' "
            f"{status_clause}"
            "ORDER BY a.appdateupdated DESC "
            f"LIMIT {limit}"
        )
    else:
        # qualifying_specialist and fallback — use IN-subquery filter
        filter_clause = get_sql_filter_clause(profile)
        if not filter_clause:
            return {"rows": [], "row_count": 0, "error": "No filter clause available for this role."}
        sql = (
            "SELECT "
            "    a.appname                   AS applicant_name, "
            "    a.jobtitle, "
            "    COALESCE(a.companname, '')  AS company, "
            "    a.appstatus                 AS status, "
            "    a.appdateupdated            AS last_updated "
            f"FROM {_PIPELINE_TABLE} a "
            f"WHERE {filter_clause} "
            f"{status_clause}"
            "ORDER BY a.appdateupdated DESC "
            f"LIMIT {limit}"
        )

    print(f"[get_my_placements] role={role_key!r} name={db_name!r}", flush=True)
    result = await run_sql({"sql": sql, "purpose": "Get user's current placements"}, ctx)
    if not result.get("error"):
        result["role"] = role_key
    return result


async def get_pipeline_summary(inp: dict, ctx: "AgentContext") -> dict:
    """Get aggregate application counts by pipeline stage.

    Queries the primary applications table and groups by current status
    so the agent can see overall pipeline health at a glance.
    """
    from agent_service.tools.sql_tool import run_sql

    days = min(int(inp.get("days") or 90), 365)
    _uf  = _user_filter_fragment(ctx)

    sql = (
        f"SELECT "
        f"    COALESCE(appstatus, 'UNKNOWN') AS stage, "
        f"    COUNT(*) AS count "
        f"FROM {_PIPELINE_TABLE} "
        f"WHERE appdatecreated >= CURRENT_DATE - {days} {_uf} "
        f"GROUP BY appstatus "
        f"ORDER BY count DESC "
        f"LIMIT 30"
    )

    result = await run_sql(
        {"sql": sql, "purpose": "Get pipeline status distribution"}, ctx
    )

    if not result.get("error"):
        rows = result.get("rows") or []
        result["total"] = sum(r.get("count", 0) for r in rows)
        result["time_window_days"] = days
        return result

    # Fallback: ML table has application_status
    fallback_sql = (
        "SELECT application_status AS stage, COUNT(*) AS count "
        f"FROM {_ML_TABLE} "
        "GROUP BY application_status "
        "ORDER BY count DESC "
        "LIMIT 20"
    )
    fallback = await run_sql(
        {"sql": fallback_sql, "purpose": "Pipeline summary from ML table"}, ctx
    )
    if not fallback.get("error"):
        rows = fallback.get("rows") or []
        fallback["total"] = sum(r.get("count", 0) for r in rows)
        fallback["source_table"] = _ML_TABLE
    return fallback


async def get_recent_activity(inp: dict, ctx: "AgentContext") -> dict:
    """Get new applications submitted and scoring events over the last N days.

    Returns two sub-results:
    - new_applications: count of applications added per day
    - recent_scoring:  scoring events from the audit log
    """
    from agent_service.tools.sql_tool import run_sql

    days  = min(int(inp.get("days")  or 7),  90)
    limit = min(int(inp.get("limit") or 20), 100)
    _uf   = _user_filter_fragment(ctx)

    # --- New applications by day ---
    new_apps_sql = (
        "SELECT "
        "    CAST(appdatecreated AS DATE) AS activity_date, "
        "    COUNT(*) AS new_applications, "
        "    COUNT(DISTINCT jobid) AS jobs_active "
        f"FROM {_PIPELINE_TABLE} "
        f"WHERE appdatecreated >= CURRENT_DATE - {days} {_uf} "
        "GROUP BY CAST(appdatecreated AS DATE) "
        "ORDER BY activity_date DESC "
        f"LIMIT {limit}"
    )
    new_apps = await run_sql(
        {"sql": new_apps_sql, "purpose": "New applications per day"}, ctx
    )

    # --- Recent scoring events (scoring table has no ownership col — no user filter here) ---
    scored_sql = (
        "SELECT "
        "    applicationscoredate AS score_date, "
        "    companyname, "
        "    jobtitle, "
        "    COUNT(*) AS applications_scored "
        f"FROM {_SCORING_TABLE} "
        f"WHERE applicationscoredate >= CURRENT_DATE - {days} "
        "GROUP BY applicationscoredate, companyname, jobtitle "
        "ORDER BY score_date DESC "
        f"LIMIT {limit}"
    )
    scored = await run_sql(
        {"sql": scored_sql, "purpose": "Recent application scoring events"}, ctx
    )

    return {
        "new_applications":       new_apps.get("rows") or [],
        "new_applications_error": new_apps.get("error"),
        "recent_scoring":         scored.get("rows") or [],
        "recent_scoring_error":   scored.get("error"),
        "time_window_days":       days,
        # Flat rows list for ToolAgent serialisation
        "rows": (new_apps.get("rows") or []) + (scored.get("rows") or []),
        "row_count": (new_apps.get("row_count") or 0) + (scored.get("row_count") or 0),
    }


async def get_jobs_dashboard(inp: dict, ctx: "AgentContext") -> dict:
    """Get open jobs ranked by pipeline activity.

    Returns one row per active job with counts of candidates at each stage —
    total, scored, interviewed, and hired — plus the date of last activity.
    Useful for identifying jobs that are stalled or understaffed.
    """
    from agent_service.tools.sql_tool import run_sql

    limit         = min(int(inp.get("limit") or 15), 50)
    status_filter = (inp.get("status_filter") or "").strip()
    _uf           = _user_filter_fragment(ctx)

    where_parts = []
    if status_filter:
        where_parts.append(f"jobstatus = '{_safe(status_filter)}'")
    if _uf:
        # _uf already starts with "AND (..." — strip the "AND " for the first clause
        where_parts.append(_uf.lstrip("AND ").strip())
    where_clause = ("WHERE " + " AND ".join(where_parts) + " ") if where_parts else ""

    # Note: the column is 'companname' (one 'y') — confirmed from schema.json
    sql = (
        "SELECT "
        "    jobid, "
        "    jobtitle                                          AS job_title, "
        "    COALESCE(companname, '')                          AS company_name, "
        "    jobstatus, "
        "    COUNT(DISTINCT applicantid)                       AS total_candidates, "
        "    COUNT(DISTINCT CASE WHEN appstatus = 'PACKAGE_SCORED' "
        "        THEN applicantid END)                         AS scored_candidates, "
        "    COUNT(DISTINCT CASE WHEN interviewstatus IS NOT NULL "
        "        AND interviewstatus != '' "
        "        THEN applicantid END)                         AS interviewed_candidates, "
        "    COUNT(DISTINCT CASE WHEN appstatus = 'CANDIDATE_HIRED' "
        "        THEN applicantid END)                         AS hired_candidates, "
        "    MAX(appdateupdated)                               AS last_activity "
        f"FROM {_PIPELINE_TABLE} "
        f"{where_clause}"
        "GROUP BY jobid, jobtitle, companname, jobstatus "
        "HAVING COUNT(DISTINCT applicantid) > 0 "
        "ORDER BY total_candidates DESC "
        f"LIMIT {limit}"
    )

    return await run_sql(
        {"sql": sql, "purpose": f"Jobs dashboard (top {limit} by candidate volume)"}, ctx
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(value: str) -> str:
    """Strip SQL injection characters from a literal string value."""
    return re.sub(r"['\";\\]", "", str(value))
