"""
Builds the user context block injected into every agent's system prompt.

The block tells the agent:
  1. Who the logged-in user is (name + role)
  2. Which SQL filter to apply when querying ownership columns

Ownership columns in the Brainwave Redshift DB are stored as name strings, not
foreign keys, so the agent receives the exact string to use in WHERE clauses.
The one exception is bqp_interview.qualifierid, which is an integer FK.
"""
from __future__ import annotations

_ROLE_DISPLAY = {
    "qualifying_specialist": "Qualifying Specialist",
    "client_advisor":        "Client Advisor",
    "placement_specialist":  "Placement Specialist",
    "relationship_manager":  "Relationship Manager",
    "vp":                    "VP",
    "admin":                 "Administrator",
}

_NO_PROFILE_BLOCK = """\
## Your access scope
No personalization active — showing data across all users.
"""


def get_sql_filter_clause(user_profile: dict | None) -> str:
    """Return the raw SQL WHERE fragment for this user, or empty string.

    Examples:
        "placementspecialist = 'Julie Petty'"
        "qualifiername = 'Bob Lee'  OR  qualifierid = 42"
        ""   ← for VP / admin / no profile
    """
    if not user_profile:
        return ""
    role_key = user_profile.get("brainwave_role", "")
    db_name  = (user_profile.get("db_name") or "").strip()
    qual_id  = user_profile.get("qualifier_id")

    if role_key in ("vp", "admin") or not db_name:
        return ""

    safe_name = db_name.replace("'", "''")

    # Ownership columns live in staging.bullhorn_core_job_order (for job-scoped roles)
    # or staging.bullhorn_core_candidate (for candidate-scoped roles), not in
    # staging.bqp_applications_list itself — so we use IN-subqueries.
    if role_key == "placement_specialist":
        return (
            f"jobid IN ("
            f"SELECT joborderid FROM staging.bullhorn_core_job_order "
            f"WHERE placementspecialist = '{safe_name}'"
            f")"
        )
    if role_key == "client_advisor":
        return (
            f"jobid IN ("
            f"SELECT joborderid FROM staging.bullhorn_core_job_order "
            f"WHERE clientadvisor = '{safe_name}'"
            f")"
        )
    if role_key == "relationship_manager":
        return (
            f"applicantid IN ("
            f"SELECT candidateid FROM staging.bullhorn_core_candidate "
            f"WHERE relationshipmanager = '{safe_name}'"
            f")"
        )
    if role_key == "qualifying_specialist":
        clause = (
            f"applicantid IN ("
            f"SELECT candidateid FROM staging.bullhorn_core_candidate "
            f"WHERE qualifier = '{safe_name}'"
            f")"
        )
        if qual_id:
            clause += (
                f" OR applicantid IN ("
                f"SELECT candidateid FROM staging.bqp_interview "
                f"WHERE qualifierid = {int(qual_id)}"
                f")"
            )
        return clause
    return ""


def build_user_context_block(user_profile: dict | None) -> str:
    """Return a markdown block describing the current user's scope.

    Injected into each agent's system prompt via the {user_context} placeholder.
    """
    if not user_profile:
        return _NO_PROFILE_BLOCK

    role_key   = user_profile.get("brainwave_role", "")
    db_name    = (user_profile.get("db_name") or "").strip()
    qual_id    = user_profile.get("qualifier_id")
    full_name  = (user_profile.get("full_name") or db_name or "Unknown")
    role_label = _ROLE_DISPLAY.get(role_key, "Team Member")
    email      = user_profile.get("user_email", "")

    lines = [
        "## Your access scope",
        f"**Your name**: {full_name}  (also stored in Brainwave DB as: **{db_name or full_name}**)",
        f"**Your role**: {role_label}",
        f"**Your email**: {email}",
    ]

    sql_filter = get_sql_filter_clause(user_profile)

    if sql_filter:
        lines += [
            "",
            f"**MANDATORY SQL FILTER — apply to every query that touches ownership columns:**",
            f"  `WHERE {sql_filter}`",
            "",
            "⚠️  You MUST include this filter in every SQL query you generate.",
            "     Never omit it. Never show data belonging to other team members",
            "     unless the user explicitly asks for a team-wide view.",
        ]

        # Role-specific join hints (ownership cols are NOT in bqp_applications_list)
        if role_key == "placement_specialist":
            lines.append("     `placementspecialist` is in staging.bullhorn_core_job_order — join on joborderid=jobid")
        elif role_key == "client_advisor":
            lines.append("     `clientadvisor` is in staging.bullhorn_core_job_order — join on joborderid=jobid")
        elif role_key == "relationship_manager":
            lines.append("     `relationshipmanager` is in staging.bullhorn_core_candidate — join on candidateid=applicantid")
        elif role_key == "qualifying_specialist":
            lines.append("     `qualifier` is in staging.bullhorn_core_candidate; `qualifierid` is in staging.bqp_interview")

    elif role_key in ("vp", "admin"):
        lines += [
            "",
            "**Data scope**: Full access — all team members' data visible.",
            "Do NOT add any ownership filter unless the user specifically requests it.",
        ]

    lines += [
        "",
        "When asked 'what is my name', 'who am I', or any identity question,",
        f"answer directly: your name is {full_name}, your role is {role_label}.",
    ]

    return "\n".join(lines)
