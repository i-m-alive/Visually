"""
Tool registry — JSON Schema definitions for every agent tool.

Each entry follows Claude's tool schema format:
    {"name": str, "description": str, "input_schema": {...}}

Tools are added per phase:
    Phase 0: run_sql
    Phase 1: score_candidate, get_candidate_scores, list_candidates, build_shortlist
    Phase 2: get_job_pipeline, list_jobs
    Phase 4: parse_resume, get_candidate_profile
    Phase 5: create_note, update_status
    Phase 8: trigger_export

Use register_tool() to add a tool schema from another module, or add it
directly to _ALL below.
"""
from __future__ import annotations

# ── Master schema store ──────────────────────────────────────────────────────
_ALL: dict[str, dict] = {

    # ── Phase 0 ─────────────────────────────────────────────────────────────
    "run_sql": {
        "name": "run_sql",
        "description": (
            "Execute a read-only SQL SELECT query against the project's connected database. "
            "Returns rows (list of dicts), column names (list), and row_count. "
            "Use for any data retrieval not covered by a more specific tool. "
            "The row limit is 500 — summarise the results rather than returning raw data."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "A read-only SQL SELECT statement. "
                        "Must not contain INSERT, UPDATE, DELETE, DROP, or DDL. "
                        "Use explicit column aliases for clarity."
                    ),
                },
                "purpose": {
                    "type": "string",
                    "description": "One sentence explaining why this query is needed.",
                },
            },
            "required": ["sql"],
        },
    },

    # ── Phase 1: candidate scoring tools ────────────────────────────────────
    "get_candidate_scores": {
        "name": "get_candidate_scores",
        "description": (
            "Query the database for ML-scored candidates for a role. "
            "Schema-adaptive: auto-discovers the scoring table if not known. "
            "Returns rows with candidate IDs, scores, and recommendation categories "
            "(highly_recommended / recommended / borderline / not_recommended / highly_not_recommended). "
            "If the table is not found, returns discovered_tables so you can retry with table_name set."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job or position ID to filter by.",
                },
                "job_title": {
                    "type": "string",
                    "description": "Job title keyword to search by (partial match).",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Filter to one category: highly_recommended, recommended, "
                        "borderline, not_recommended, or highly_not_recommended."
                    ),
                },
                "table_name": {
                    "type": "string",
                    "description": (
                        "Exact table name if already known (e.g. from a previous "
                        "get_candidate_scores call that returned discovered_tables). "
                        "Omit to let the tool auto-detect."
                    ),
                },
            },
            "required": [],
        },
    },

    "list_candidates": {
        "name": "list_candidates",
        "description": (
            "List and sort candidates from a known scoring table with optional filters. "
            "Use after get_candidate_scores has identified the correct table_name. "
            "Returns candidates ordered by score descending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Scoring table name (required — from get_candidate_scores).",
                },
                "job_id": {
                    "type": "string",
                    "description": "Filter to a specific job or position ID.",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum score threshold (0.0–1.0). Omit to return all.",
                },
                "category_filter": {
                    "type": "string",
                    "description": "Restrict to one recommendation category.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Return only the top N candidates (default 20, max 200).",
                },
            },
            "required": ["table_name"],
        },
    },

    "build_shortlist": {
        "name": "build_shortlist",
        "description": (
            "Format a ranked candidate shortlist from score data. "
            "Groups candidates by recommendation category (Highly Recommended → Borderline) "
            "and returns a ready-to-present markdown summary. "
            "Call this last — after you have the candidate rows from the database."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "Array of candidate dicts (must include score and category fields).",
                },
                "role_name": {
                    "type": "string",
                    "description": "Job title / role name for the shortlist heading.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Maximum candidates to include in the shortlist (default 10).",
                },
            },
            "required": ["candidates", "role_name"],
        },
    },

    # ── score_candidate reserved for Phase 4 (Lambda 2 on-demand) ────────────
    # "score_candidate": {...}  — added in Phase 4

    # ── Phase 2: pipeline / briefing tools ───────────────────────────────────
    "get_my_placements": {
        "name": "get_my_placements",
        "description": (
            "Get the logged-in user's current active candidates / placements. "
            "Automatically filters to show ONLY this user's records (by placementspecialist, "
            "clientadvisor, or relationshipmanager column depending on their role). "
            "Use this for ANY question about 'my candidates', 'my placements', 'who am I placing', "
            "'my pipeline', 'my activity', 'what am I working on'. "
            "No SQL needed — just call this tool directly."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": (
                        "Optional: filter to a specific app status "
                        "(e.g. 'QI_PENDING', 'PACKAGE_SCORED', 'CANDIDATE_HIRED'). "
                        "Omit to return all active candidates."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default 25, max 100).",
                },
            },
            "required": [],
        },
    },

    "get_pipeline_summary": {
        "name": "get_pipeline_summary",
        "description": (
            "Get aggregate application counts grouped by pipeline stage. "
            "Queries staging.bqp_applications_list. Returns one row per status "
            "(e.g. PACKAGE_SCORED, QI_PENDING, CANDIDATE_HIRED) with a count, "
            "plus a total field. Use this first in every briefing to understand "
            "overall pipeline health."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": (
                        "Look-back window in days from today (default 90, max 365). "
                        "Set to 365 for an all-time view."
                    ),
                },
            },
            "required": [],
        },
    },

    "get_recent_activity": {
        "name": "get_recent_activity",
        "description": (
            "Get new application submissions and scoring events for the last N days. "
            "Returns two sub-lists: new_applications (count per day) and "
            "recent_scoring (scoring events by company/job title). "
            "Call with days=7 for a weekly snapshot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Look-back window in days (default 7, max 90).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return per sub-list (default 20).",
                },
            },
            "required": [],
        },
    },

    "get_jobs_dashboard": {
        "name": "get_jobs_dashboard",
        "description": (
            "Get open jobs ranked by candidate volume. Returns one row per job with: "
            "total_candidates, scored_candidates, interviewed_candidates, hired_candidates, "
            "last_activity date, and job_status. "
            "Use to identify jobs with many unscored candidates (need scoring pipeline run) "
            "or jobs with no recent activity (stalled)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of jobs to return (default 15, max 50).",
                },
                "status_filter": {
                    "type": "string",
                    "description": (
                        "Filter to a specific job status (e.g. 'OPEN', 'FILLED'). "
                        "Omit to return all statuses."
                    ),
                },
            },
            "required": [],
        },
    },

    # ── Phase 4 placeholders (filled by lambda_bridge + s3_reader) ──────────
    # "parse_resume":          {...}  — added in Phase 4
    # "get_candidate_profile": {...}  — added in Phase 4

    # ── Phase 5 placeholders (filled by record_tools) ───────────────────────
    # "create_note":   {...}  — added in Phase 5  (WRITE — needs confirmation)
    # "update_status": {...}  — added in Phase 5  (WRITE — needs confirmation)

    # ── Phase 8 placeholder (filled by candidate_tools / export) ────────────
    # "trigger_export": {...}  — added in Phase 8
}


# ── Public API ───────────────────────────────────────────────────────────────

def get_schemas(names: list[str]) -> list[dict]:
    """Return the Claude-format tool schemas for the requested tool names.

    Unknown or not-yet-registered names are silently skipped so that a skill
    agent asking for a Phase-N tool before Phase N is implemented doesn't crash.
    """
    return [_ALL[n] for n in names if n in _ALL]


def register_tool(schema: dict) -> None:
    """Register a tool schema at module-load time.

    Called by each phase's tool module so tools self-register when imported:

        from agent_service.tools.registry import register_tool
        register_tool({"name": "score_candidate", "description": "...", "input_schema": {...}})
    """
    name = schema.get("name")
    if not name:
        raise ValueError(f"Tool schema missing 'name' field: {schema!r}")
    _ALL[name] = schema


def list_all() -> list[str]:
    """Return the names of all registered tools (debugging / MCP server use)."""
    return list(_ALL.keys())
