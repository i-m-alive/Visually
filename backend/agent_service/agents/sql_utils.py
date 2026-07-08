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
        if not sql_text:
            continue
        for m in re.finditer(r'\bFROM\s+([\w.]+)\b', sql_text, re.IGNORECASE):
            tname = m.group(1).strip('"').strip("'")
            if tname and tname not in recent_tables:
                recent_tables.append(tname)
        for m in re.finditer(r'\bJOIN\s+([\w.]+)\b', sql_text, re.IGNORECASE):
            tname = m.group(1).strip('"').strip("'")
            if tname and tname not in recent_tables:
                recent_tables.append(tname)
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


# ── 5b. Filter-value verification ─────────────────────────────────────────────

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
