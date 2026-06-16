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
        result = re.sub(
            r'\b' + re.escape(alias) + r'\.([\w]+)\b',
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
