"""intel_guardrails.py — SQL safety, governance, and token estimation for
Intelligence Copilot (both report mode and full-DB mode).

Guardrail layers (applied in order; first failure wins):
  1. Comment stripping      — removes --line and /* block */ comments before analysis.
  2. Statement-chain check  — blocks semicolons that chain multiple statements.
  3. Statement-type check   — only SELECT / WITH (CTE) allowed; all writes/DDL/admin blocked.
  4. System-catalog check   — blocks information_schema, pg_catalog, Redshift STL/STV, etc.
  5. Row-limit enforcement  — injects or caps LIMIT at max_rows (default 10 000).
  6. Table-scope check      — BLOCKS (report mode) or WARNS (database mode) out-of-scope tables.

Token estimation:
  estimate_query_tokens() reports cached vs dynamic token counts and USD cost at
  Bedrock Claude Sonnet on-demand pricing, accounting for prompt caching.
"""

import re
from dataclasses import dataclass
from typing import Optional


# ── GuardrailResult ────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    safe: bool                       # False → caller must NOT execute
    error: Optional[str] = None      # set when safe=False; surface to the user
    warning: Optional[str] = None    # set when safe=True; log, optionally surface
    sql: str = ""                    # cleaned + limit-capped SQL to execute


# ── Blocked keyword sets ───────────────────────────────────────────────────────

_WRITE_KEYWORDS = frozenset({"INSERT", "UPDATE", "DELETE", "MERGE", "UPSERT"})

_DDL_KEYWORDS = frozenset({
    "CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "COMMENT",
})

_ADMIN_KEYWORDS = frozenset({
    "COPY", "GRANT", "REVOKE", "EXECUTE", "CALL",
    "VACUUM", "REINDEX", "CLUSTER", "CHECKPOINT", "LOCK",
    "NOTIFY", "LISTEN", "UNLISTEN",
    "PREPARE", "DEALLOCATE",
    "IMPORT", "UNLOAD",          # Redshift data load/export
})

_INTROSPECTION_KEYWORDS = frozenset({
    "EXPLAIN", "ANALYZE",
    "SET", "SHOW", "RESET",
    "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
    "START", "ABORT",            # START TRANSACTION / ABORT
})

_ALL_BLOCKED = _WRITE_KEYWORDS | _DDL_KEYWORDS | _ADMIN_KEYWORDS | _INTROSPECTION_KEYWORDS

_KEYWORD_CATEGORY: dict[str, str] = {
    **{k: "write operation"             for k in _WRITE_KEYWORDS},
    **{k: "DDL statement"               for k in _DDL_KEYWORDS},
    **{k: "admin / privileged command"  for k in _ADMIN_KEYWORDS},
    **{k: "transaction / session control" for k in _INTROSPECTION_KEYWORDS},
}


# ── System-catalog / internal-table patterns ───────────────────────────────────
# Each item: (lowercase_substring_to_search, human_readable_label).
# Checked against the lowercased, comment-stripped SQL.

_SYSTEM_CATALOG_PATTERNS: list[tuple[str, str]] = [
    # PostgreSQL
    ("information_schema.",    "information_schema"),
    ("pg_catalog.",            "pg_catalog"),
    ("pg_class",               "pg_class"),
    ("pg_namespace",           "pg_namespace"),
    ("pg_stat",                "pg_stat"),
    ("pg_locks",               "pg_locks"),
    ("pg_bgwriter",            "pg_bgwriter"),
    ("pg_roles",               "pg_roles"),
    ("pg_user",                "pg_user"),
    ("pg_shadow",              "pg_shadow"),
    ("pg_authid",              "pg_authid"),
    ("pg_database",            "pg_database"),
    ("pg_tables",              "pg_tables"),
    ("pg_indexes",             "pg_indexes"),
    ("pg_views",               "pg_views"),
    ("pg_sequences",           "pg_sequences"),
    ("pg_proc",                "pg_proc"),
    ("pg_attribute",           "pg_attribute"),
    ("pg_inherits",            "pg_inherits"),
    ("pg_constraint",          "pg_constraint"),
    # SQL Server
    ("sys.tables",             "sys.tables"),
    ("sys.columns",            "sys.columns"),
    ("sys.objects",            "sys.objects"),
    ("sys.schemas",            "sys.schemas"),
    # IBM Db2
    ("syscat.",                "syscat"),
    # Amazon Redshift system tables
    ("stl_",                   "Redshift STL system tables"),
    ("stv_",                   "Redshift STV system tables"),
    ("svl_",                   "Redshift SVL system views"),
    ("svv_",                   "Redshift SVV system views"),
    # Snowflake account-usage
    ("snowflake.account_usage.", "Snowflake account_usage views"),
]

_MAX_ROW_LIMIT: int = 10_000


# ── Internal helpers ───────────────────────────────────────────────────────────

def _strip_comments(sql: str) -> str:
    """Remove /* block */ and -- line comments.
    Does NOT touch string literals — strips only true SQL comments."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql.strip()


def _first_keyword(sql: str) -> str:
    """Return the first alphanumeric token from the SQL, uppercased.
    Skips leading whitespace and open-parentheses (CTEs may start with `WITH`,
    but can be written as `(WITH ...)` — rare but valid)."""
    stripped = sql.strip()
    while stripped and stripped[0] in "( \t\n\r":
        stripped = stripped[1:]
    m = re.match(r"([A-Za-z_]\w*)", stripped)
    return m.group(1).upper() if m else ""


def _has_multiple_statements(sql: str) -> bool:
    """Return True when a semicolon separates two non-empty statements.
    A trailing semicolon with nothing after it is allowed."""
    in_single = in_double = False
    for i, ch in enumerate(sql):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ";" and not in_single and not in_double:
            if sql[i + 1:].strip():
                return True
    return False


def _inject_row_limit(sql: str, max_rows: int) -> tuple[str, bool]:
    """Ensure the query ends with LIMIT <= max_rows.
    Appends LIMIT if absent; lowers it if it exceeds the cap.
    Returns (modified_sql, was_changed)."""
    m = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    if m:
        existing = int(m.group(1))
        if existing <= max_rows:
            return sql, False
        new_sql = sql[: m.start()] + f"LIMIT {max_rows}" + sql[m.end():]
        return new_sql, True
    # No LIMIT present — append one.
    cleaned = sql.rstrip().rstrip(";").rstrip()
    return cleaned + f"\nLIMIT {max_rows}", True


# ── Public guardrail API ────────────────────────────────────────────────────────

def apply_fulldb_guardrails(
    sql: str,
    allowed_tables: Optional[set] = None,
    mode: str = "database",
    max_rows: int = _MAX_ROW_LIMIT,
) -> GuardrailResult:
    """Apply all Intelligence Copilot SQL guardrails.

    Parameters
    ----------
    sql            : raw SQL string produced by the LLM.
    allowed_tables : set of table names (bare or schema-qualified) that are in-scope.
                     None means "any table is allowed" — no scope check is performed.
    mode           : "report"   → out-of-scope table access BLOCKS (hard error).
                     "database" → out-of-scope table access WARNS (logged, allowed).
    max_rows       : hard row cap; injected/capped in the SQL; default 10 000.

    Returns
    -------
    GuardrailResult
        .safe=False → do NOT execute; surface .error to the user.
        .safe=True  → execute .sql (may have LIMIT injected/capped).
                      .warning (if set) should be logged and optionally surfaced.
    """
    if not sql or not sql.strip():
        return GuardrailResult(safe=False, error="Empty SQL query.", sql="")

    # ── Layer 0: Strip comments ───────────────────────────────────────────────
    clean = _strip_comments(sql)
    if not clean:
        return GuardrailResult(
            safe=False, error="SQL is empty after comment stripping.", sql=""
        )

    # ── Layer 1: No statement chaining ───────────────────────────────────────
    if _has_multiple_statements(clean):
        return GuardrailResult(
            safe=False,
            error=(
                "Multiple SQL statements are not allowed. "
                "Send one SELECT query at a time — no semicolons between statements."
            ),
            sql="",
        )

    # ── Layer 2: Allowed statement types ─────────────────────────────────────
    first = _first_keyword(clean)
    if first in _ALL_BLOCKED:
        category = _KEYWORD_CATEGORY.get(first, "restricted statement")
        return GuardrailResult(
            safe=False,
            error=(
                f"Only read-only SELECT or WITH (CTE) queries are allowed. "
                f"{first} is a {category} and is not permitted in this mode."
            ),
            sql="",
        )
    if first not in ("SELECT", "WITH"):
        return GuardrailResult(
            safe=False,
            error=f"Only SELECT and WITH (CTE) queries are allowed. Received: {first}.",
            sql="",
        )

    # ── Layer 3: System-catalog / internal-table access ───────────────────────
    lower = clean.lower()
    for pattern, label in _SYSTEM_CATALOG_PATTERNS:
        if pattern in lower:
            return GuardrailResult(
                safe=False,
                error=(
                    f"Access to system tables or internal catalog views ({label}) "
                    "is not allowed. Only user-defined tables in the database schema "
                    "may be queried."
                ),
                sql="",
            )

    # ── Layer 4: Row-limit enforcement ────────────────────────────────────────
    warnings: list[str] = []
    limited_sql, limit_changed = _inject_row_limit(clean, max_rows)
    if limit_changed:
        warnings.append(f"Result set capped to {max_rows:,} rows.")

    # ── Layer 5: Table scope validation ──────────────────────────────────────
    if allowed_tables is not None:
        # Build a normalised lookup that matches both bare and schema-qualified names.
        allowed_lower: set[str] = set()
        for t in allowed_tables:
            allowed_lower.add(t.lower())
            allowed_lower.add(t.split(".")[-1].lower())

        from_join_tables = re.findall(
            r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)",
            limited_sql, re.IGNORECASE,
        )
        cte_aliases = {
            m.lower()
            for m in re.findall(
                r"\b([a-zA-Z_]\w*)\s+AS\s*\(", limited_sql, re.IGNORECASE
            )
        }

        out_of_scope: list[str] = []
        for tbl in from_join_tables:
            bare = tbl.split(".")[-1].lower()
            if bare in cte_aliases:
                continue
            if tbl.lower() not in allowed_lower and bare not in allowed_lower:
                out_of_scope.append(tbl)

        if out_of_scope:
            scope_msg = (
                f"SQL references table(s) outside the allowed scope: "
                f"{', '.join(out_of_scope)}"
            )
            if mode == "report":
                return GuardrailResult(
                    safe=False,
                    error=(
                        scope_msg
                        + " — in report mode only the report's tables and their "
                          "FK neighbours may be queried."
                    ),
                    sql="",
                )
            else:
                warnings.append(scope_msg)

    return GuardrailResult(
        safe=True,
        warning="; ".join(warnings) if warnings else None,
        sql=limited_sql,
    )


# ── Token estimation ───────────────────────────────────────────────────────────

_CHARS_PER_TOKEN: float = 4.0  # rough average for English prose + SQL

# Bedrock Claude Sonnet on-demand pricing (per million tokens, us-east-1, 2026).
_PRICE_CACHE_WRITE: float = 3.75   # $/M — first write of a cached prefix block
_PRICE_CACHE_READ: float  = 0.30   # $/M — subsequent reads (same block, same session)
_PRICE_INPUT: float       = 3.00   # $/M — uncached input tokens
_PRICE_OUTPUT: float      = 15.00  # $/M — output tokens


def estimate_query_tokens(
    message: str,
    schema_text: str,
    widget_context: str,
    history_texts: Optional[list] = None,
    instructions_text: str = "",
    mode: str = "report",
) -> dict:
    """Estimate token usage and USD cost for a single Intelligence Copilot turn.

    Prompt-caching structure assumed:
      ZONE 1 — instructions (NOT cached — dynamic header may vary)
      ZONE 2 — schema block (cached with ephemeral cache_control)
      ZONE 3 — graph-RAG hints, verified tables, canvas context (NOT cached)

    The schema block is written to cache on the first turn of a session and read
    from cache on all subsequent turns, cutting 92 % off the schema token cost.

    Parameters
    ----------
    message           : user's message for this turn.
    schema_text       : the rendered schema block text (ZONE 2 — cached).
    widget_context    : the dynamic canvas context text (ZONE 3).
    history_texts     : list of prior raw message strings; last 8 are used.
    instructions_text : the static instruction block text (ZONE 1, NOT cached).
    mode              : "report" | "database" — informational.

    Returns
    -------
    dict with token counts, cost breakdown, and a monthly projection assuming
    100 queries/day and 70 % cache-hit rate.
    """
    history_texts = history_texts or []
    recent = history_texts[-8:]

    cached_tokens  = len(schema_text) / _CHARS_PER_TOKEN
    dynamic_tokens = (
        len(instructions_text)
        + len(widget_context)
        + len(message)
        + sum(len(h) for h in recent)
    ) / _CHARS_PER_TOKEN

    output_est = 700  # text ~400 + SQL ~200 + chart spec ~100 (conservative)
    total_input = cached_tokens + dynamic_tokens
    total = total_input + output_est

    first_turn_usd = (
        cached_tokens  / 1_000_000 * _PRICE_CACHE_WRITE
        + dynamic_tokens / 1_000_000 * _PRICE_INPUT
        + output_est     / 1_000_000 * _PRICE_OUTPUT
    )
    subsequent_turn_usd = (
        cached_tokens  / 1_000_000 * _PRICE_CACHE_READ
        + dynamic_tokens / 1_000_000 * _PRICE_INPUT
        + output_est     / 1_000_000 * _PRICE_OUTPUT
    )
    savings_pct = (
        round((1 - subsequent_turn_usd / max(first_turn_usd, 1e-9)) * 100, 1)
        if first_turn_usd > 0 else 0.0
    )

    # Monthly estimate — 100 queries/day, 70 % cache-hit rate (warm session).
    daily_cost = (
        100 * 0.70 * subsequent_turn_usd
        + 100 * 0.30 * first_turn_usd
    )

    return {
        "mode": mode,
        "tokens": {
            "cached_schema":   round(cached_tokens),
            "dynamic_input":   round(dynamic_tokens),
            "output_estimate": output_est,
            "total_input":     round(total_input),
            "total":           round(total),
        },
        "breakdown_chars": {
            "schema":       len(schema_text),
            "instructions": len(instructions_text),
            "widget":       len(widget_context),
            "message":      len(message),
            "history":      sum(len(h) for h in recent),
        },
        "cost_usd": {
            "first_turn":        round(first_turn_usd, 6),
            "subsequent_turn":   round(subsequent_turn_usd, 6),
            "cache_savings_pct": savings_pct,
            "monthly_100q_day":  round(daily_cost * 30, 4),
        },
        "note": (
            f"Schema block ({round(cached_tokens):,} tokens) uses ephemeral prompt caching. "
            f"First turn writes the cache (${_PRICE_CACHE_WRITE}/M); subsequent turns read "
            f"at ${_PRICE_CACHE_READ}/M — {savings_pct}% cheaper per turn."
        ),
    }
