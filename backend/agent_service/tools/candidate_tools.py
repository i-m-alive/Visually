"""
Candidate tools — Phase 1

Schema-adaptive: tries common table/column patterns before falling back to
information_schema discovery. No table names are hardcoded as required config.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_service.agents.tool_agent import AgentContext

# Recommendation quality order (best → worst)
_CATEGORY_ORDER = [
    "highly_recommended",
    "recommended",
    "borderline",
    "not_recommended",
    "highly_not_recommended",
]

_CATEGORY_LABELS = {
    "highly_recommended":     "Highly Recommended",
    "recommended":            "Recommended",
    "borderline":             "Borderline",
    "not_recommended":        "Not Recommended",
    "highly_not_recommended": "Highly Not Recommended",
}

# (table_name, candidate_id_col, job_id_col, score_col, category_col)
_TABLE_PATTERNS = [
    ("candidate_scores",  "candidate_id", "job_id", "score",    "category"),
    ("applications",      "candidate_id", "job_id", "ml_score", "recommendation"),
    ("job_applications",  "candidate_id", "job_id", "score",    "status"),
    ("candidates",        "id",           "job_id", "score",    "category"),
]


async def get_candidate_scores(inp: dict, ctx: "AgentContext") -> dict:
    """Find and return ML-scored candidates from the database.

    Tries known table/column patterns in order. If all fail, queries
    information_schema and returns discovered table names for the agent to retry.
    """
    from agent_service.tools.sql_tool import run_sql

    job_id     = (inp.get("job_id")     or "").strip()
    job_title  = (inp.get("job_title")  or "").strip()
    category   = (inp.get("category")   or "").strip()
    table_hint = (inp.get("table_name") or "").strip()

    # ── Caller provided a specific table — query it directly ─────────────────
    if table_hint:
        where_parts = []
        if job_id:
            where_parts.append(f"job_id = '{_safe(job_id)}'")
        if category:
            where_parts.append(f"LOWER(category::text) = '{_safe(category.lower())}'")
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = (
            f"SELECT * FROM {_safe_ident(table_hint)} "
            f"{where_clause} "
            f"ORDER BY score DESC NULLS LAST LIMIT 100"
        )
        result = await run_sql(
            {"sql": sql, "purpose": f"Query {table_hint} for candidate scores"}, ctx
        )
        if not result.get("error"):
            result["matched_table"] = table_hint
        return result

    # ── Probe known patterns ──────────────────────────────────────────────────
    for (tbl, _cand_col, job_col, score_col, cat_col) in _TABLE_PATTERNS:
        where_parts = []
        if job_id and job_col:
            where_parts.append(f"{job_col} = '{_safe(job_id)}'")
        if job_title and job_col:
            where_parts.append(
                f"LOWER({job_col}::text) LIKE '%{_safe(job_title.lower())}%'"
            )
        if category and cat_col:
            where_parts.append(f"LOWER({cat_col}::text) = '{_safe(category.lower())}'")
        where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        sql = (
            f"SELECT * FROM {tbl} "
            f"{where_clause} "
            f"ORDER BY {score_col} DESC NULLS LAST LIMIT 100"
        )

        result = await run_sql(
            {"sql": sql, "purpose": f"Probe {tbl} for candidate scores"}, ctx
        )

        if result.get("error"):
            continue  # table likely doesn't exist — try next pattern

        # Table exists (even if 0 rows due to filters)
        result["matched_table"] = tbl
        return result

    # ── Fall back to schema discovery ─────────────────────────────────────────
    # Try multiple approaches for compatibility with different DB engines.
    # Redshift uses SVV_TABLES and pg_tables; PostgreSQL uses information_schema.
    _DISCOVERY_SQLS = [
        # Standard SQL — no schema filter (works across schemas, all engines)
        (
            "SELECT DISTINCT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type = 'BASE TABLE' "
            "AND (table_name ILIKE '%candidate%' OR table_name ILIKE '%applicant%' "
            "     OR table_name ILIKE '%score%'   OR table_name ILIKE '%application%') "
            "ORDER BY table_schema, table_name LIMIT 50"
        ),
        # Redshift SVV_TABLES (Redshift-specific system view)
        (
            "SELECT DISTINCT schema_name AS table_schema, table_name "
            "FROM SVV_TABLES "
            "WHERE table_type = 'TABLE' "
            "AND (table_name ILIKE '%candidate%' OR table_name ILIKE '%applicant%' "
            "     OR table_name ILIKE '%score%'   OR table_name ILIKE '%application%') "
            "ORDER BY schema_name, table_name LIMIT 50"
        ),
        # pg_tables — works on both PostgreSQL and Redshift
        (
            "SELECT DISTINCT schemaname AS table_schema, tablename AS table_name "
            "FROM pg_tables "
            "WHERE tablename ILIKE '%candidate%' OR tablename ILIKE '%applicant%' "
            "      OR tablename ILIKE '%score%'   OR tablename ILIKE '%application%' "
            "ORDER BY schemaname, tablename LIMIT 50"
        ),
    ]

    discovered: list[str] = []
    for _dsql in _DISCOVERY_SQLS:
        _disc = await run_sql(
            {"sql": _dsql, "purpose": "Discover candidate-related tables"}, ctx
        )
        if not _disc.get("error") and _disc.get("row_count", 0) > 0:
            for r in (_disc.get("rows") or []):
                schema = r.get("table_schema") or r.get("schemaname") or ""
                tname  = r.get("table_name")  or r.get("tablename")  or ""
                if tname:
                    qualified = f"{schema}.{tname}" if schema else tname
                    if qualified not in discovered:
                        discovered.append(qualified)
            break  # found results — stop trying

    return {
        "error": (
            "Could not find candidate scoring data in standard table patterns "
            f"({', '.join(p[0] for p in _TABLE_PATTERNS)}). "
            "Retry with table_name set to one of the discovered_tables."
            if not discovered else
            "No rows found with the given filters. Try without job_id/category filters, "
            "or use run_sql to inspect one of the discovered_tables."
        ),
        "discovered_tables": discovered,
        "rows":      [],
        "columns":   [],
        "row_count": 0,
    }


async def list_candidates(inp: dict, ctx: "AgentContext") -> dict:
    """List and sort candidates from a known scoring table with optional filters."""
    from agent_service.tools.sql_tool import run_sql

    table_name      = (inp.get("table_name")      or "").strip()
    job_id          = (inp.get("job_id")          or "").strip()
    min_score       = inp.get("min_score")
    category_filter = (inp.get("category_filter") or "").strip()
    top_n           = min(int(inp.get("top_n") or 20), 200)

    if not table_name:
        return {
            "error": (
                "table_name is required. "
                "Run get_candidate_scores first to identify the correct table."
            ),
            "rows": [], "columns": [], "row_count": 0,
        }

    where_parts = []
    if job_id:
        where_parts.append(f"job_id = '{_safe(job_id)}'")
    if min_score is not None:
        try:
            where_parts.append(f"score >= {float(min_score)}")
        except (TypeError, ValueError):
            pass
    if category_filter:
        where_parts.append(f"LOWER(category::text) = '{_safe(category_filter.lower())}'")

    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    sql = (
        f"SELECT * FROM {_safe_ident(table_name)} "
        f"{where_clause} "
        f"ORDER BY score DESC NULLS LAST "
        f"LIMIT {top_n}"
    )
    return await run_sql(
        {"sql": sql, "purpose": f"List top {top_n} candidates from {table_name}"}, ctx
    )


async def build_shortlist(inp: dict, ctx: "AgentContext") -> dict:
    """Format a ranked shortlist grouped by recommendation category.

    Pure logic — no DB call. Call this last after get_candidate_scores /
    list_candidates have returned the raw rows.
    """
    candidates = inp.get("candidates") or []
    role_name  = (inp.get("role_name") or "Role").strip()
    top_n      = int(inp.get("top_n") or 10)

    if not candidates:
        return {
            "error": "No candidate data provided. Fetch candidate scores first.",
            "shortlist_markdown": "",
            "total":       0,
            "by_category": {},
        }

    def _normalise_cat(c: dict) -> str:
        raw = (
            c.get("category") or c.get("recommendation") or c.get("status") or ""
        ).lower().replace(" ", "_").replace("-", "_")
        return raw if raw in _CATEGORY_ORDER else "borderline"

    def _sort_key(c: dict):
        cat   = _normalise_cat(c)
        rank  = _CATEGORY_ORDER.index(cat) if cat in _CATEGORY_ORDER else 99
        score = float(c.get("score") or c.get("ml_score") or 0)
        return (rank, -score)

    sorted_candidates = sorted(candidates, key=_sort_key)[:top_n]

    by_category: dict[str, list] = {}
    for c in sorted_candidates:
        cat = _normalise_cat(c)
        by_category.setdefault(cat, []).append(c)

    lines: list[str] = [f"## Candidate Shortlist — {role_name}\n"]
    for cat_key in _CATEGORY_ORDER:
        group = by_category.get(cat_key)
        if not group:
            continue
        label = _CATEGORY_LABELS.get(cat_key, cat_key.replace("_", " ").title())
        lines.append(f"### {label} ({len(group)})")
        for i, c in enumerate(group, 1):
            name = (
                c.get("name") or c.get("candidate_name")
                or c.get("full_name") or c.get("candidate_id") or "Unknown"
            )
            score = c.get("score") or c.get("ml_score")
            score_str = f" — score: {float(score):.2f}" if score is not None else ""
            lines.append(f"{i}. **{name}**{score_str}")
        lines.append("")

    lines.append(
        f"*{len(sorted_candidates)} of {len(candidates)} candidates shown, "
        f"ranked by ML score.*"
    )

    return {
        "shortlist_markdown": "\n".join(lines),
        "total":       len(candidates),
        "shown":       len(sorted_candidates),
        "by_category": {k: len(v) for k, v in by_category.items()},
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe(value: str) -> str:
    """Strip characters that could break a SQL string literal."""
    return re.sub(r"['\";\\]", "", str(value))


def _safe_ident(value: str) -> str:
    """Allow only alphanumeric + underscore for table/column identifiers."""
    return re.sub(r"[^a-zA-Z0-9_]", "", str(value))
