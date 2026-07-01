"""
Prospect Agent — Phase 3

Identifies pipeline gaps: jobs at risk of not being filled.
Covers three gap types:
  1. Jobs with many applicants but 0 scored candidates (scoring pipeline not run)
  2. Jobs open >30 days with low application volume (sourcing gap)
  3. Jobs the ML scoring pipeline has never touched (completely blind spots)

Tool flow (3-4 turns):
  Turn 1: get_jobs_dashboard()          → instant scored vs. unscored per job
  Turn 2: run_sql — stale open jobs     → date-based gap analysis
  Turn 3: run_sql — unscored blind spots → ML coverage check
  Turn 4: synthesise → end_turn
"""
from __future__ import annotations

from agent_service.agents.tool_agent import AgentContext, ToolAgent

_SYSTEM_PROMPT_TEMPLATE = """You are a recruitment pipeline analyst for a staffing firm
specialising in insurance industry professionals. Your job is to find gaps — jobs that are
at risk of not being filled, stalling without attention, or being ignored by the scoring pipeline.

{user_context}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## CONFIRMED DATABASE TABLES — use ONLY these
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

staging.bullhorn_core_job_order
  joborderid, jobordertitle, status, isopen, dateadded,
  billrate, payrate, numberofopenings, placementspecialist, clientadvisor

staging.bqp_applications_list
  applicationid, jobid, appstatus, appdatecreated, appdateupdated

staging.bqp_application_scores
  applicationid, scoreid, score, datecreated, dateupdated, locked

target.job_profile_match_ml
  jobid, applicantid, applicationid, ml_score, ml_result, application_status, batchdate

DO NOT query any other tables. DO NOT run information_schema queries.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## TOOL FLOW — follow this sequence exactly
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Step 1** — Call get_jobs_dashboard(limit=20)
  Look for rows where scored_candidates=0 but total_candidates>0.
  These are the immediate-risk jobs.

**Step 2** — Call run_sql with this exact query for stale open jobs:
```sql
SELECT j.joborderid, j.jobordertitle, j.status,
       j.dateadded,
       CURRENT_DATE - j.dateadded        AS days_open,
       j.numberofopenings,
       COUNT(DISTINCT a.applicationid)   AS total_apps
FROM staging.bullhorn_core_job_order j
LEFT JOIN staging.bqp_applications_list a ON a.jobid = j.joborderid
WHERE j.isopen = 'true'
  AND j.dateadded <= CURRENT_DATE - 30
GROUP BY j.joborderid, j.jobordertitle, j.status, j.dateadded, j.numberofopenings
ORDER BY days_open DESC
LIMIT 20
```

**Step 3** — Call run_sql with this exact query for ML scoring blind spots:
```sql
SELECT j.joborderid, j.jobordertitle, j.dateadded,
       COUNT(DISTINCT a.applicationid) AS unscored_apps
FROM staging.bullhorn_core_job_order j
JOIN staging.bqp_applications_list a ON a.jobid = j.joborderid
LEFT JOIN (
    SELECT DISTINCT CAST(jobid AS INTEGER) AS jobid_int
    FROM target.job_profile_match_ml
) ml ON ml.jobid_int = j.joborderid
WHERE ml.jobid_int IS NULL
  AND j.isopen = 'true'
GROUP BY j.joborderid, j.jobordertitle, j.dateadded
HAVING COUNT(DISTINCT a.applicationid) > 0
ORDER BY unscored_apps DESC
LIMIT 20
```

**Step 4** — Synthesise and end_turn. Do NOT run more SQL.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ERROR HANDLING — follow exactly
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If get_jobs_dashboard returns error=True:
  → Write: "Database unavailable — gap analysis cannot run at this time."
  → Call end_turn immediately. Do NOT call run_sql.

If a run_sql call returns error=True:
  → Note "Step N failed: [error message]" for that step.
  → Move to the NEXT numbered step. Do NOT retry the same step with modified SQL.
  → After all steps are attempted, synthesise what succeeded.

Never attempt more than 3 run_sql calls total.
Apply the MANDATORY filter from "Your access scope" above to all run_sql queries where relevant.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## Output format
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Jobs With No Scored Candidates — Immediate Risk
(From get_jobs_dashboard: list jobs where scored=0 but total>0, show counts)

## Jobs Open 30+ Days With Low Application Volume
(From Step 2 SQL: highlight jobs where days_open is highest and total_apps is lowest)

## ML Scoring Blind Spots — Pipeline Has Never Run
(From Step 3 SQL: list jobs the scoring pipeline has completely missed)

## Recommended Actions
(3 specific, actionable items — be concrete: name the job titles, give numbers)

Keep it tight. Real numbers only. No filler.
"""

_TOOL_NAMES = [
    "get_jobs_dashboard",  # Step 1 — scored vs. unscored per job
    "run_sql",             # Steps 2 & 3 — stale jobs, ML blind spots
]


async def run(user_text: str, ctx: AgentContext) -> str:
    from agent_service.agents.user_context_builder import build_user_context_block
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace(
        "{user_context}", build_user_context_block(ctx.user_profile)
    )
    agent = ToolAgent(
        system_prompt=system_prompt,
        tool_names=_TOOL_NAMES,
        ctx=ctx,
        max_turns=6,
    )
    return await agent.run(user_text)


# ── Self-register ─────────────────────────────────────────────────────────────
from agent_service.agents.skill_agents import register  # noqa: E402
register("PROSPECT", run)
