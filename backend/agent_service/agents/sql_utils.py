"""
SQL utilities: alias expansion, column existence pre-check, basic linting.

These run BEFORE DB execution to catch obvious errors without a round-trip.
All functions are synchronous and purely text-based — no DB, no LLM.
"""
import re
from typing import Optional


# ── 1. Table alias expansion ──────────────────────────────────────────────────

def expand_table_aliases(sql: str) -> str:
    """
    Expand short table aliases to full table names throughout the SQL.
    Handles:  FROM table AS t,  FROM table t,  JOIN table AS t,  JOIN table t

    Prevents 'column "t" does not exist' errors on Redshift/PostgreSQL when an
    alias is misinterpreted as a column name rather than a table alias.

    Non-destructive: leaves SQL unchanged if no expansions apply, or if a
    replacement would alter SQL semantics (CTEs, subqueries, star expansion).
    """
    if not sql or not sql.strip():
        return sql

    sql_stripped = sql.strip()

    # Keywords that can never be table aliases
    _RESERVED = frozenset({
        "where", "on", "group", "order", "inner", "left", "right", "full",
        "cross", "join", "having", "limit", "as", "by", "and", "or", "not",
        "in", "is", "null", "true", "false", "select", "from", "with",
        "union", "intersect", "except", "case", "when", "then", "else", "end",
        "between", "like", "ilike", "exists", "distinct", "all", "any",
    })

    # Find alias definitions: (FROM|JOIN) [schema.]table [[AS] alias]
    # The lookahead ensures we stop at the next SQL keyword.
    _ALIAS_PAT = re.compile(
        r'\b(FROM|JOIN)\s+'
        r'((?:[\w]+\.)?[\w]+)'                          # table_name (optionally schema-qualified)
        r'\s+(?:AS\s+)?(\b[a-zA-Z_]\w*\b)'             # [AS] alias
        r'(?=\s*(?:ON|WHERE|GROUP|ORDER|INNER|LEFT|RIGHT|FULL|CROSS|JOIN|HAVING|LIMIT|\)|,|$))',
        re.IGNORECASE,
    )

    alias_map: dict[str, str] = {}
    for m in _ALIAS_PAT.finditer(sql_stripped):
        table_name = m.group(2)
        alias = m.group(3)
        if alias.lower() not in _RESERVED and alias.lower() != table_name.lower():
            alias_map[alias] = table_name

    if not alias_map:
        return sql

    result = sql
    for alias, table_name in alias_map.items():
        # 1. Replace alias.column → table.column, using the BARE table name (last
        #    path segment). A schema-qualified ref like public.sales.col is a
        #    3-level reference that Redshift rejects; "sales.col" resolves fine
        #    against "FROM public.sales".
        bare_table = table_name.split(".")[-1]
        # Pattern handles both unquoted (col) and double-quoted ("col") identifiers.
        # \b inside the group applies only to unquoted names; quoted names end with "
        # which is its own natural boundary, so no trailing \b is needed for them.
        result = re.sub(
            r'\b' + re.escape(alias) + r'\.((?:"[^"]+"|[\w]+\b))',
            bare_table.replace('\\', '\\\\') + r'.\1',
            result,
        )
        # 2. Remove the alias DEFINITION from the FROM/JOIN clause so the table is
        #    referenced consistently by its full name. Leaving "FROM tbl alias"
        #    while rewriting refs to "tbl.col" makes the alias the only valid
        #    reference and causes Postgres/Redshift error 42P01
        #    ("invalid reference to FROM-clause entry ... perhaps you meant alias").
        result = re.sub(
            r'(\b(?:FROM|JOIN)\s+' + re.escape(table_name) + r')\s+(?:AS\s+)?' + re.escape(alias) + r'\b',
            r'\1',
            result,
            flags=re.IGNORECASE,
        )

    return result


# ── 2. Table.column reference extraction ─────────────────────────────────────

def extract_recent_tables(
    conversation_history: Optional[list], max_turns: int = 4, max_tables: int = 4
) -> list[str]:
    """
    Tables referenced via FROM/JOIN in the SQL of recent conversation turns.
    Used to bias table retrieval/schema selection for follow-up questions whose
    own wording gives little or no signal (e.g. "what about last month").
    """
    if not conversation_history:
        return []
    recent_tables: list[str] = []
    for turn in conversation_history[-max_turns:]:
        sql_text = turn.get("sql") or ""
        if sql_text:
            for m in re.finditer(r'\bFROM\s+([\w.]+)\b', sql_text, re.IGNORECASE):
                tname = m.group(1).strip('"').strip("'")
                if tname and tname not in recent_tables:
                    recent_tables.append(tname)
            for m in re.finditer(r'\bJOIN\s+([\w.]+)\b', sql_text, re.IGNORECASE):
                tname = m.group(1).strip('"').strip("'")
                if tname and tname not in recent_tables:
                    recent_tables.append(tname)
        else:
            # No SQL to scrape (text answer, low-confidence result) — fall back to
            # the table the turn recorded, so continuity survives a turn that
            # produced prose or a soft failure instead of a chart. A hard failure
            # with no result at all legitimately contributes nothing.
            tu = (turn.get("table_used") or "").strip()
            if tu and tu not in recent_tables:
                recent_tables.append(tu)
    return recent_tables[:max_tables]


def extract_table_column_refs(sql: str) -> list[tuple[str, str]]:
    """
    Extract all (table, column) pairs from qualified references in the SQL.
    Only finds explicit  table.column  form — unqualified columns are not extracted.
    """
    _FUNC_NAMES = frozenset({
        "min", "max", "sum", "avg", "count", "upper", "lower", "trim",
        "length", "coalesce", "nullif", "extract", "date_trunc", "date_part",
        "to_char", "to_date", "convert", "cast", "round", "floor", "ceil",
        "now", "getdate", "dateadd", "datediff", "nvl", "nvl2",
    })
    refs: list[tuple[str, str]] = []
    # Match word.word but exclude decimal literals (e.g., 3.14)
    for m in re.finditer(r'\b([a-zA-Z_]\w*)\.([\w]+)\b', sql):
        qualifier = m.group(1)
        col = m.group(2)
        if qualifier.lower() not in _FUNC_NAMES:
            refs.append((qualifier, col))
    return refs


# ── 3. Column existence pre-check ────────────────────────────────────────────

def verify_columns_against_schema(
    sql: str,
    compact_tables: list,
    candidate_tables: Optional[list[str]] = None,
) -> Optional[str]:
    """
    Parse the generated SQL and verify that all  table.column  references exist
    in the schema. Returns a human-readable error string when a missing column
    is detected, or None when everything checks out.

    Saves a full DB round-trip for obvious column-name errors (hallucinated columns,
    wrong table prefix, etc.).

    Only checks qualified references (table.col). Unqualified columns and
    function calls are skipped.
    """
    # Build {table_lower: {col_lower}} from compact_tables
    col_lookup: dict[str, set] = {}
    for t in compact_tables:
        tname = (t.get("name") or "").lower()
        cols = {(c.get("name") or "").lower() for c in (t.get("columns") or [])}
        if tname:
            col_lookup[tname] = cols
            # Also index without schema prefix for unqualified matches
            bare = tname.split(".")[-1]
            if bare not in col_lookup:
                col_lookup[bare] = cols

    candidate_lower = {t.lower() for t in (candidate_tables or [])}
    # Also add bare names of candidates
    candidate_lower.update(t.split(".")[-1].lower() for t in (candidate_tables or []))

    refs = extract_table_column_refs(sql)
    for table_ref, col_ref in refs:
        table_lower = table_ref.lower()
        col_lower = col_ref.lower()

        # Only check tables we know about
        known_cols = col_lookup.get(table_lower)
        if known_cols is None:
            continue

        # If we have candidate_tables, limit checks to those (skip join aliases etc.)
        if candidate_lower and table_lower not in candidate_lower:
            continue

        if known_cols and col_lower not in known_cols:
            # Look up the friendly table name for the error message
            display_table = table_ref
            available = sorted(known_cols)[:12]
            return (
                f"Column '{col_ref}' does not exist in table '{display_table}'. "
                f"Available columns: {', '.join(available)}"
            )

    return None


# ── 4. Basic SQL linting ──────────────────────────────────────────────────────

def basic_sql_lint(sql: str, db_type: str = "postgresql") -> Optional[str]:
    """
    Lightweight syntax check that catches common generation mistakes before
    sending the query to the database.

    Returns an error description string on problem, None when the SQL looks OK.
    Deliberately permissive — only flags obvious structural issues.
    """
    if not sql or not sql.strip():
        return "SQL is empty."

    trimmed = sql.strip()
    upper = trimmed.upper()

    # Must start with SELECT or WITH (CTEs)
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        return "SQL must start with SELECT (read-only queries only)."

    # Block dangerous DDL / DML statements
    _DANGEROUS = ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER",
                  "TRUNCATE", "GRANT", "REVOKE", "EXECUTE", "EXEC")
    for kw in _DANGEROUS:
        if re.search(r'\b' + kw + r'\b', upper):
            return f"SQL contains disallowed keyword '{kw}' — only SELECT is permitted."

    # Balanced parentheses
    depth = 0
    in_single = False
    in_double = False
    for i, ch in enumerate(trimmed):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            if depth < 0:
                return "SQL has unbalanced parentheses (extra closing ')' )."
    if depth != 0:
        return f"SQL has {depth} unclosed parenthesis/parentheses."

    # ORDER BY inside a UNION branch (invalid in most databases)
    union_idx = upper.find("UNION")
    if union_idx > 0:
        pre_union = upper[:union_idx]
        ob_idx = pre_union.rfind("ORDER BY")
        if ob_idx > 0:
            between = pre_union[ob_idx:]
            if between.count("(") == between.count(")"):
                return (
                    "ORDER BY inside a UNION branch is invalid. "
                    "Wrap the full UNION in a subquery and place ORDER BY outside: "
                    "SELECT * FROM (SELECT ... UNION ALL SELECT ...) AS t ORDER BY col"
                )

    # Redshift-specific: GROUP BY alias (not supported in Redshift)
    if db_type == "redshift":
        # This is tricky to detect accurately — skip for now, covered by the DB error path
        pass

    return None


# ── 4b. Intent-contract check ─────────────────────────────────────────────────
# Deterministic assertions that the generated SQL actually honours the parsed
# intent (time filter present, correct GROUP BY granularity, KPI shape).
# Runs before execution; violations become retry feedback.

_DATE_FILTER_MARKERS = (
    "current_date", "curdate", "getdate", "now()", "interval", "dateadd",
    "date_sub", "generator", "date_spine", "generate_series", "between",
)

_GRAN_MARKERS: dict = {
    "day": ("date_trunc('day'", 'date_trunc("day"', "::date", "date(", "to_date(",
            "date_spine", "generate_series", "generator", "%y-%m-%d"),
    "week": ("date_trunc('week'", 'date_trunc("week"', "week(", "weekofyear",
             "date_spine", "%x-%v"),
    "month": ("date_trunc('month'", 'date_trunc("month"', "month(", "monthname",
              "date_spine", "%y-%m", "'mon yyyy'", "'yyyy-mm'"),
    "quarter": ("date_trunc('quarter'", 'date_trunc("quarter"', "quarter(",
                "date_spine"),
    "year": ("date_trunc('year'", 'date_trunc("year"', "year(", "extract(year",
             "date_part('year'", "%y"),
}

# A DATE_TRUNC on a DIFFERENT unit than requested is a hard contract violation
_ALL_TRUNC_UNITS = ("day", "week", "month", "quarter", "year")


def check_sql_contract(
    sql: str,
    granularity: Optional[str] = None,
    time_filter_required: bool = False,
    chart_type: Optional[str] = None,
) -> Optional[str]:
    """
    Verify the generated SQL honours the parsed intent. Returns an error string
    describing the violation (used as retry feedback), or None when compliant.
    Deliberately permissive — only flags unambiguous violations.
    """
    if not sql or not sql.strip():
        return None
    lower = sql.lower()

    # 1. Time-range filter must exist when the user gave a time range
    if time_filter_required:
        has_where = "where" in lower
        has_date_math = any(m in lower for m in _DATE_FILTER_MARKERS)
        # literal ISO dates ('2026-06-28') also count as a date filter
        has_literal_date = bool(re.search(r"'\d{4}-\d{2}-\d{2}'", sql))
        if not (has_where and (has_date_math or has_literal_date)) and "date_spine" not in lower and "generator" not in lower:
            return (
                "The user specified a time range but the SQL has no date filter. "
                "Add a WHERE clause restricting the date column to the requested window."
            )

    # 2. Granularity: GROUP BY must bucket time at the requested unit
    if granularity in _GRAN_MARKERS:
        # A DATE_TRUNC at a different unit is an explicit violation
        for unit in _ALL_TRUNC_UNITS:
            if unit == granularity:
                continue
            if f"date_trunc('{unit}'" in lower or f'date_trunc("{unit}"' in lower:
                return (
                    f"The user asked for a {granularity}-wise breakdown but the SQL "
                    f"groups by {unit} (DATE_TRUNC('{unit}', ...)). "
                    f"Use DATE_TRUNC('{granularity}', date_col) instead."
                )
        if not any(m in lower for m in _GRAN_MARKERS[granularity]):
            return (
                f"The user asked for a {granularity}-wise breakdown but the SQL does not "
                f"bucket dates by {granularity}. Group by DATE_TRUNC('{granularity}', date_col) "
                f"(or the dialect equivalent) and return one row per {granularity}."
            )

    # 3. KPI shape: exactly one aggregate row — GROUP BY is a violation
    if (chart_type or "").lower() in ("kpi", "gauge") and re.search(r"\bGROUP\s+BY\b", sql, re.IGNORECASE):
        return (
            "Chart type is KPI (single value) but the SQL contains GROUP BY, which "
            "returns multiple rows. Remove the GROUP BY and return one aggregate value."
        )

    return None


def expected_period_count(start_iso: str, end_iso: str, granularity: str) -> Optional[int]:
    """Number of time buckets between two ISO dates at the given granularity."""
    from datetime import date as _date
    try:
        s = _date.fromisoformat(start_iso[:10])
        e = _date.fromisoformat(end_iso[:10])
    except (ValueError, TypeError):
        return None
    if e < s:
        return None
    days = (e - s).days + 1
    if granularity == "day":
        return days
    if granularity == "week":
        return max(1, round(days / 7))
    if granularity == "month":
        return (e.year - s.year) * 12 + (e.month - s.month) + 1
    if granularity == "quarter":
        sq, eq = (s.month - 1) // 3, (e.month - 1) // 3
        return (e.year - s.year) * 4 + (eq - sq) + 1
    if granularity == "year":
        return e.year - s.year + 1
    return None


# ── 5. Fuzzy column-name correction ──────────────────────────────────────────

def _fuzzy_col_score(needle: str, haystack: str) -> float:
    """Similarity score between two column name strings (both lowercased)."""
    if needle == haystack:
        return 1.0
    if needle in haystack or haystack in needle:
        return 0.85
    n_parts = set(needle.split("_"))
    h_parts = set(haystack.split("_"))
    if n_parts and h_parts:
        overlap = len(n_parts & h_parts) / max(len(n_parts), len(h_parts))
        if overlap >= 0.5:
            return 0.65 + overlap * 0.25
    common = sum(1 for a, b in zip(needle, haystack) if a == b)
    if needle and common / len(needle) >= 0.6:
        return 0.60
    return 0.0


def fuzzy_fix_column_names(
    sql: str,
    compact_tables: list,
    candidate_tables: Optional[list[str]] = None,
) -> tuple[str, list[str]]:
    """
    Auto-correct hallucinated column names in generated SQL via fuzzy matching.
    Returns (fixed_sql, list_of_corrections).
    Only touches qualified table.column references that fail schema lookup.
    Silently returns the original SQL unchanged if nothing can be fixed confidently.
    """
    col_lookup: dict[str, list[str]] = {}
    for t in compact_tables:
        tname = (t.get("name") or "").lower()
        cols = [c.get("name") for c in (t.get("columns") or []) if c.get("name")]
        if tname:
            col_lookup[tname] = cols
            bare = tname.split(".")[-1]
            if bare not in col_lookup:
                col_lookup[bare] = cols

    candidate_lower: set[str] = set()
    for raw in (candidate_tables or []):
        candidate_lower.add(raw.lower())
        candidate_lower.add(raw.lower().split(".")[-1])

    refs = extract_table_column_refs(sql)
    corrections: list[str] = []
    fixed = sql

    for table_ref, col_ref in refs:
        tl = table_ref.lower()
        cl = col_ref.lower()
        known = col_lookup.get(tl)
        if not known:
            continue
        if candidate_lower and tl not in candidate_lower:
            continue
        known_lower = {c.lower(): c for c in known}
        if cl in known_lower:
            continue  # already correct
        best_col, best_score = None, 0.0
        for orig_lower, orig in known_lower.items():
            s = _fuzzy_col_score(cl, orig_lower)
            if s > best_score:
                best_score, best_col = s, orig
        if best_col and best_score >= 0.60:
            fixed = re.sub(
                r'\b' + re.escape(table_ref) + r'\.' + re.escape(col_ref) + r'\b',
                f'{table_ref}.{best_col}',
                fixed,
            )
            corrections.append(f"'{col_ref}' → '{best_col}' (in {table_ref})")

    return fixed, corrections


# ── 5b. Unqualified column reference extraction + full-ref validation ─────────

# Combined reserved-word set: SQL keywords + common function names.
# Bare identifiers that match any entry here are never flagged as column refs.
_SQL_RESERVED: frozenset = frozenset({
    "select", "from", "where", "and", "or", "not", "in", "is", "null",
    "true", "false", "on", "as", "join", "inner", "left", "right", "full",
    "outer", "cross", "group", "by", "order", "having", "limit", "offset",
    "union", "intersect", "except", "all", "distinct", "case", "when",
    "then", "else", "end", "with", "recursive", "over", "partition",
    "rows", "range", "between", "preceding", "following", "current",
    "asc", "desc", "nulls", "first", "last", "filter", "within",
    "ilike", "like", "similar", "escape", "lateral", "values", "exists",
    "any", "some", "default", "interval", "epoch", "at", "time", "zone",
    "unbounded", "ties", "exclude", "no", "rollup", "cube",
    "grouping", "sets", "tablesample", "system", "bernoulli",
    "insert", "update", "delete", "create", "drop", "alter", "truncate",
    "merge", "upsert", "grant", "revoke",
    # Temporal keywords that can appear as bare words
    "date", "year", "month", "day", "hour", "minute", "second", "week", "quarter",
    # Aggregate + scalar functions
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "cast",
    "extract", "date_trunc", "date_part", "to_char", "to_date", "to_timestamp",
    "now", "getdate", "dateadd", "datediff", "current_date", "current_timestamp",
    "upper", "lower", "trim", "length", "substr", "substring", "replace",
    "concat", "string_agg", "listagg", "array_agg", "json_agg",
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value", "cume_dist", "percent_rank",
    "round", "floor", "ceil", "ceiling", "abs", "mod", "power", "sqrt",
    "log", "ln", "exp", "random", "generate_series", "unnest",
    "nvl", "nvl2", "decode", "greatest", "least", "ifnull", "iif",
    "convert", "try_cast", "strtol", "charindex", "patindex", "stuff",
    "split_part", "regexp_replace", "regexp_split_to_table",
    "percentile_cont", "percentile_disc", "mode",
    "bit_and", "bit_or", "bool_and", "bool_or", "md5", "sha256",
    "xmlagg", "xmlelement",
})

# Matches bare identifiers that are NOT preceded or followed by '.' or a word char.
_BARE_IDENT_RE = re.compile(r'(?<![.\w])([a-zA-Z_]\w*)(?![.\w])', re.IGNORECASE)


def extract_unqualified_col_refs(sql: str, extra_skip: Optional[set] = None) -> list[str]:
    """Extract bare (unqualified) identifiers that are candidate column references.

    Strips string literals and comments, filters SQL keywords/functions, table names,
    table aliases, CTE names, and column aliases (after AS).
    Returns a de-duplicated list of candidate bare column names.
    """
    # Strip single-quoted literals to avoid matching values like 'placed' as identifiers
    clean = re.sub(r"'[^']*'", " ", sql)
    # Strip double-quoted identifiers/aliases (e.g. AS "Buy Volume") — these are
    # output names or exact-case quoted references, never bare column refs to
    # validate. Without this, a multi-word alias like "Total Units" gets tokenized
    # into "Total" and "Units" and both get flagged as missing columns.
    clean = re.sub(r'"[^"]*"', " ", clean)
    # Strip comments
    clean = re.sub(r"--[^\n]*", " ", clean)
    clean = re.sub(r"/\*.*?\*/", " ", clean, flags=re.DOTALL)

    # Collect CTE names — they're virtual tables, not columns
    cte_names: set[str] = {
        m.lower()
        for m in re.findall(r'\b([a-zA-Z_]\w*)\s+AS\s*\(', clean, re.IGNORECASE)
    }

    # Collect column/expression aliases (after AS keyword, not followed by '(')
    alias_names: set[str] = set()
    for m in re.findall(r'\bAS\s+([a-zA-Z_]\w*)\b', clean, re.IGNORECASE):
        if m.lower() not in _SQL_RESERVED:
            alias_names.add(m.lower())

    # Collect table names and their aliases from FROM/JOIN
    from_join = re.findall(
        r'\b(?:FROM|JOIN)\s+([\w.]+)(?:\s+(?:AS\s+)?([a-zA-Z_]\w*))?\b',
        clean, re.IGNORECASE,
    )
    table_refs: set[str] = set()
    tbl_alias_names: set[str] = set()
    for tbl, alias in from_join:
        table_refs.add(tbl.lower())
        table_refs.add(tbl.split(".")[-1].lower())
        if alias and alias.lower() not in _SQL_RESERVED:
            tbl_alias_names.add(alias.lower())

    skip = (
        _SQL_RESERVED
        | table_refs
        | tbl_alias_names
        | cte_names
        | alias_names
        | (extra_skip or set())
    )

    seen: set[str] = set()
    refs: list[str] = []
    for m in _BARE_IDENT_RE.finditer(clean):
        name = m.group(1)
        nl = name.lower()
        if nl in skip or nl in seen or len(name) <= 1:
            continue
        seen.add(nl)
        refs.append(name)

    return refs


def verify_all_column_refs(
    sql: str,
    compact_tables: list,
    candidate_tables: Optional[list[str]] = None,
) -> list[str]:
    """Extended column validation covering BOTH qualified (table.col) AND
    unqualified (bare col) references. Returns all error messages found.
    Empty list means no issues detected.

    Unlike verify_columns_against_schema (qualified only, first error only),
    this function reports every bad column in a single pass.
    """
    # Build col lookup: {table_lower: {col_name_lower}}
    col_lookup: dict[str, set[str]] = {}
    for t in compact_tables:
        tname = (t.get("name") or "").lower()
        cols = {(c.get("name") or "").lower() for c in (t.get("columns") or []) if c.get("name")}
        if tname:
            col_lookup[tname] = cols
            bare = tname.split(".")[-1]
            if bare not in col_lookup:
                col_lookup[bare] = cols

    candidate_lower: set[str] = set()
    if candidate_tables:
        for t in candidate_tables:
            candidate_lower.add(t.lower())
            candidate_lower.add(t.split(".")[-1].lower())

    # Union of all known columns (restricted to candidates when specified)
    if candidate_lower:
        all_cols: set[str] = set()
        for tn, cols in col_lookup.items():
            if tn in candidate_lower:
                all_cols.update(cols)
    else:
        all_cols = {c for cols in col_lookup.values() for c in cols}

    errors: list[str] = []

    # -- Check 1: qualified refs (table.col) -- catches all, reports all errors
    for table_ref, col_ref in extract_table_column_refs(sql):
        tl = table_ref.lower()
        cl = col_ref.lower()
        known = col_lookup.get(tl)
        if known is None:
            continue
        if candidate_lower and tl not in candidate_lower:
            continue
        if known and cl not in known:
            available = sorted(known)[:10]
            errors.append(
                f"Column '{col_ref}' not in table '{table_ref}'. "
                f"Available: {', '.join(available)}"
            )

    # -- Check 2: unqualified refs (bare col names) --
    if all_cols:
        for col in extract_unqualified_col_refs(sql):
            if col.lower() not in all_cols:
                errors.append(f"Column '{col}' not found in any referenced table")

    return errors


def check_result_sanity(
    rows: list,
    columns: list,
    chart_type: str,
    sql: str = "",
) -> tuple[bool, str]:
    """Post-execution sanity check for result quality problems that SQL execution
    success doesn't catch (the query ran, but the result is nonsensical).

    Covers two gaps not already handled by the orchestrator's deterministic checks:
      • KPI returning NULL (aggregate matched 0 rows — silent empty result)
      • All-zero values for a numeric chart (wrong column or over-filtered data)

    Returns (ok, feedback_message) — ok=False means a retry is warranted.
    """
    if not rows:
        return False, (
            "Query returned 0 rows. Possible causes: overly strict WHERE filter, "
            "wrong table selected, or JOIN key mismatch."
        )

    ct = (chart_type or "").lower()
    _KPI = frozenset({"kpi", "kpi_card", "gauge", "metric", "scorecard"})

    # KPI/gauge with NULL aggregate value (matched 0 rows for SUM/COUNT/AVG)
    if ct in _KPI and len(rows) == 1 and columns:
        measure = columns[-1]
        val = rows[0].get(measure)
        if val is None:
            return False, (
                f"KPI returned NULL for '{measure}' — the aggregate matched no rows "
                "or the column is not numeric. "
                "Use COALESCE(SUM(col), 0) or check the WHERE clause."
            )

    # All-zero numeric values (wrong measure column or data entirely filtered out)
    _NUMERIC = frozenset({
        "bar_vertical", "bar_horizontal", "line", "area", "waterfall",
        "stacked_bar", "grouped_bar", "funnel",
    })
    if ct in _NUMERIC and columns and len(rows) >= 2:
        measure = columns[-1]
        sample = [rows[i].get(measure) for i in range(min(len(rows), 50))]
        numeric_vals = [v for v in sample if v is not None and isinstance(v, (int, float))]
        if numeric_vals and all(v == 0 for v in numeric_vals):
            return False, (
                f"All values in '{measure}' are 0 — the column may not hold the "
                "requested metric, or a WHERE filter is too restrictive."
            )

    return True, ""


# ── 8. "Which <dimension>" question/answer dimension mismatch ────────────────
# Catches a specific, easy-to-miss failure mode: SQL that runs fine and returns
# real data, but groups by the WRONG dimension because its values happen to
# overlap with words in the metric name (e.g. "which SEGMENT is highest in Buy
# vs Sell Volume" answered by grouping by a transaction_type column whose
# values are literally "Buy"/"Sell" — a different, wrong question that still
# executes without error and passes every other check).

_WHICH_DIMENSION_RE = re.compile(r'\bwhich\s+([a-z][a-z_]*)\b', re.IGNORECASE)
_WHICH_STOPWORDS = frozenset({
    "one", "of", "is", "are", "was", "were", "has", "have", "the", "a", "an",
})


def extract_which_dimension_hint(message: str) -> Optional[str]:
    """Extract the noun immediately following 'which' in a ranking question
    ('which segment is highest' -> 'segment'). Returns None if no match or the
    matched word is a stopword/pronoun rather than a real dimension noun."""
    m = _WHICH_DIMENSION_RE.search(message or "")
    if not m:
        return None
    word = m.group(1).lower()
    if word in _WHICH_STOPWORDS:
        return None
    return word


def check_dimension_match(
    user_message: str,
    result_columns: list,
    compact_tables: Optional[list] = None,
) -> Optional[str]:
    """For a 'which <dimension> is highest/lowest/best/worst/most/least' question,
    verify the SQL's first output column (the GROUP BY / label column) actually
    represents that dimension — not a column whose literal VALUES happen to
    overlap with words in the metric name. Deterministic, no LLM call.

    Returns a retry-feedback string when a likely mismatch is detected, else None.
    Deliberately lenient (fuzzy match + schema description match) to minimize
    false positives on genuinely correct queries using a synonymous column name.
    """
    hint = extract_which_dimension_hint(user_message)
    if not hint or not result_columns:
        return None
    first_col = str(result_columns[0]).lower()
    if hint in first_col or first_col in hint or _fuzzy_col_score(hint, first_col) >= 0.60:
        return None
    # Output aliases use spaces ("Client Name") while schema columns use
    # underscores (client_name) — normalize both before comparing so the
    # description lookup below actually finds the matching schema column.
    first_col_norm = first_col.replace(" ", "_").replace("-", "_")
    if compact_tables:
        for t in compact_tables:
            for c in (t.get("columns") or []):
                cname_norm = (c.get("name") or "").lower().replace(" ", "_").replace("-", "_")
                if cname_norm != first_col_norm:
                    continue
                desc = (c.get("description") or "").lower()
                if hint in desc:
                    return None
    return (
        f"The user asked WHICH {hint.upper()} — the result's first column is "
        f"'{result_columns[0]}', which does not represent {hint}. GROUP BY a "
        f"column that represents {hint} (a customer/entity attribute), not a "
        f"column whose literal values happen to overlap with words in the metric "
        f"name. If comparing multiple metrics (e.g. 'X vs Y'), pivot them into "
        f"separate columns with CASE WHEN and GROUP BY the {hint} column instead."
    )


# ── 5c. Filter-value verification ─────────────────────────────────────────────

def fix_filter_values(
    sql: str,
    entity_columns: dict,
) -> tuple[str, list[str]]:
    """
    Verify string literals in WHERE equality filters against cached sample
    values and auto-correct case/whitespace mismatches.

    `status = 'placed'` silently returns 0 rows when the DB stores 'Placed' —
    this catches it before execution using enriched.entity_columns:
      {entity_type: [{table, column, sample_values}]}

    Returns (fixed_sql, corrections). Conservative: only replaces when a
    case-insensitive exact match exists among the samples for a column with
    the same name.
    """
    if not sql or not entity_columns:
        return sql, []

    # column_name(lower) → set of known sample values
    samples_by_col: dict[str, set] = {}
    for col_infos in entity_columns.values():
        for ci in col_infos or []:
            cname = (ci.get("column") or "").lower()
            vals = {str(s) for s in (ci.get("sample_values") or []) if s is not None}
            if cname and vals:
                samples_by_col.setdefault(cname, set()).update(vals)

    if not samples_by_col:
        return sql, []

    corrections: list[str] = []
    fixed = sql

    # Match  col = 'literal'  and  col.col = 'literal'  (equality only — LIKE
    # and inequalities are intentional partial matches, leave them alone)
    for m in re.finditer(r"([\w.]+)\s*=\s*'([^']+)'", sql):
        col_ref, literal = m.group(1), m.group(2)
        bare_col = col_ref.split(".")[-1].lower()
        known = samples_by_col.get(bare_col)
        if not known or literal in known:
            continue
        # case/whitespace-insensitive match against samples
        lit_norm = literal.strip().lower()
        exact = next((v for v in known if v.strip().lower() == lit_norm), None)
        if exact and exact != literal:
            fixed = fixed.replace(f"'{literal}'", f"'{exact}'", 1)
            corrections.append(f"filter value '{literal}' → '{exact}' (column {col_ref})")

    return fixed, corrections


# ── 6. FK-graph JOIN path finder ──────────────────────────────────────────────

def find_join_path(
    graph_edges: dict,
    start: str,
    end: str,
    max_hops: int = 2,
) -> Optional[list[tuple[str, str, str]]]:
    """
    BFS over the FK relationship graph to find the shortest JOIN path between
    two tables. Returns a list of (from_table, to_table, join_condition) tuples,
    one per hop. Returns None when no path exists within max_hops.
    """
    if not graph_edges or start == end:
        return []
    from collections import deque
    queue: deque = deque([(start, [])])
    visited: set[str] = {start}
    while queue:
        current, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for neighbor, condition in graph_edges.get(current, {}).items():
            hop = (current, neighbor, condition)
            new_path = path + [hop]
            if neighbor == end:
                return new_path
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, new_path))
    return None


# ── N. Snowflake case-insensitive string comparison normalizer ─────────────────

# Matches: [alias.]col_name = 'literal'
_STR_EQ_RE = re.compile(
    r"""((?:[\w"]+\.)?[\w"]+)\s*=\s*'([^']*)'""",
    re.IGNORECASE,
)
# Matches: [alias.]col_name IN ('a', 'b', ...)
_STR_IN_RE = re.compile(
    r"""((?:[\w"]+\.)?[\w"]+)\s+IN\s*\(\s*('(?:[^']*)'(?:\s*,\s*'(?:[^']*)')*)\s*\)""",
    re.IGNORECASE,
)
# Literals that should NOT be uppercased (pure hex/UUID and numeric strings)
_SKIP_UPPER_RE = re.compile(r'^[0-9A-Fa-f]{6,}[-0-9A-Fa-f]*$')
_NUMERIC_LIT_RE = re.compile(r'^[\d.eE+\-]+$')


def _should_upper(lit: str) -> bool:
    """True when a string literal should be uppercased for case-insensitive comparison."""
    if _NUMERIC_LIT_RE.match(lit):
        return False
    # Long hex strings (UUIDs, hashes) — don't recase, they're already deterministic
    if len(lit) > 8 and _SKIP_UPPER_RE.match(lit):
        return False
    return True


def normalize_string_comparisons_snowflake(sql: str) -> str:
    """Rewrite string equality comparisons in Snowflake SQL to be case-insensitive.

    LLMs often guess lowercase values ('buy', 'sell') when the database stores
    mixed-case ('Buy', 'Sell'). This rewriter wraps the column with UPPER() and
    uppercases the literal so the comparison is always case-insensitive:

        col = 'buy'          →  UPPER(col) = 'BUY'
        t.col = 'Sell'       →  UPPER(t.col) = 'SELL'
        col IN ('a', 'b')    →  UPPER(col) IN ('A', 'B')

    Skips numeric literals, UUID-like hex strings, and columns already wrapped
    in UPPER() to avoid double-wrapping.

    Only applies to Snowflake (caller must gate on db_type == 'snowflake').
    """

    def _eq_sub(m: re.Match) -> str:
        col, lit = m.group(1), m.group(2)
        if not _should_upper(lit):
            return m.group(0)
        if col.upper().startswith("UPPER("):
            return m.group(0)
        return f"UPPER({col}) = '{lit.upper()}'"

    def _in_sub(m: re.Match) -> str:
        col = m.group(1)
        lits = re.findall(r"'([^']*)'", m.group(2))
        if not any(_should_upper(l) for l in lits):
            return m.group(0)
        if col.upper().startswith("UPPER("):
            return m.group(0)
        inner = ", ".join(f"'{l.upper()}'" for l in lits)
        return f"UPPER({col}) IN ({inner})"

    # Apply IN first (more specific pattern) then equality
    sql = _STR_IN_RE.sub(_in_sub, sql)
    sql = _STR_EQ_RE.sub(_eq_sub, sql)
    return sql


# ── 7. Root-cause follow-up detection ─────────────────────────────────────────
# Matches an explicit "why did that fail / why is this wrong" follow-up — domain
# agnostic, shared by orchestrator.py and the chat routers' root-cause gates.
# Only meaningful when combined with a check that the prior turn actually
# recorded a failure/low-confidence flag (callers must AND that in themselves).

ROOT_CAUSE_FOLLOWUP_PATTERN = re.compile(
    r"\bwhy\s+(is|was|did|does|are)\b.{0,40}\b(wrong|fail(ed)?|off|so\s+(high|low)|error|empty|zero|broken)\b"
    r"|\bwhat('s| is)\s+wrong\b"
    r"|\bwhat\s+happened\b"
    r"|\bdebug\s+(this|that)\b"
    r"|\bexplain\s+(this|that)\s+(error|result|failure|number)\b",
    re.IGNORECASE,
)


def root_cause_schema_context(enriched, table_names: list) -> list:
    """Small {name, columns} list for the tables most likely relevant to a
    root-cause diagnosis. Pure transform — no I/O, no DB, no LLM calls."""
    if not enriched or not getattr(enriched, "compact_tables", None) or not table_names:
        return []
    ct_map = {t["name"]: t for t in enriched.compact_tables}
    out = []
    for tn in table_names[:5]:
        ct = ct_map.get(tn)
        if ct:
            out.append({
                "name": tn,
                "columns": [c.get("name") for c in ct.get("columns", [])[:20]],
            })
    return out
