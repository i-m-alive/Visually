"""
Deterministic SQL templates for simple, well-understood queries.

When a question maps to a registered metric definition (metric_registry) and a
simple shape — single KPI, time-series with granularity, or one-dimension
breakdown — the SQL is built in Python from the definition: zero LLM call,
zero hallucination, always the same answer for the same question.

Anything more complex returns None and falls through to the LLM path.
"""
import re
from typing import Optional

from shared.schemas.chart import QueryPlan


def _quote_ident(name: str) -> str:
    return name  # identifiers come from the schema cache / registry, already valid


def _date_spine_sql(db_type: str, granularity: str, n_periods: int) -> Optional[str]:
    """CTE body generating one row per period ending today. None if unsupported."""
    g = granularity
    if db_type == "snowflake":
        if g == "day":
            return (
                f"SELECT DATEADD(day, seq4(), DATEADD(day, -{n_periods - 1}, CURRENT_DATE())) AS bucket\n"
                f"    FROM TABLE(GENERATOR(ROWCOUNT => {n_periods}))"
            )
        return (
            f"SELECT DATE_TRUNC('{g}', DATEADD({g}, -seq4(), CURRENT_DATE())) AS bucket\n"
            f"    FROM TABLE(GENERATOR(ROWCOUNT => {n_periods}))"
        )
    if db_type in ("postgresql", "redshift"):
        if g == "day":
            return (
                f"SELECT generate_series(CURRENT_DATE - INTERVAL '{n_periods - 1} days', "
                f"CURRENT_DATE, INTERVAL '1 day')::date AS bucket"
            )
        return (
            f"SELECT generate_series(DATE_TRUNC('{g}', CURRENT_DATE) - INTERVAL '{n_periods - 1} {g}s', "
            f"DATE_TRUNC('{g}', CURRENT_DATE), INTERVAL '1 {g}')::date AS bucket"
        )
    if db_type == "mysql":
        if g == "day":
            return (
                f"WITH RECURSIVE spine AS (SELECT CURDATE() - INTERVAL {n_periods - 1} DAY AS bucket "
                f"UNION ALL SELECT bucket + INTERVAL 1 DAY FROM spine WHERE bucket < CURDATE()) "
                f"SELECT bucket FROM spine"
            )
        return None  # month/week recursive spines in MySQL: let the LLM handle it
    return None


def _bucket_expr(db_type: str, granularity: str, date_col: str) -> str:
    if granularity == "day":
        return f"DATE({date_col})" if db_type in ("mysql", "snowflake") else f"{date_col}::date"
    return f"DATE_TRUNC('{granularity}', {date_col})"


def _resolve_dimension_column(dimension_terms: list, table_ct: dict) -> Optional[str]:
    """Fuzzy-match an intent dimension phrase to a real column on the table."""
    if not dimension_terms or not table_ct:
        return None
    cols = table_ct.get("columns") or []
    for term in dimension_terms:
        t_norm = re.sub(r"[^a-z0-9]", "", term.lower())
        if not t_norm:
            continue
        for c in cols:
            cname = (c.get("name") or "")
            c_norm = re.sub(r"[^a-z0-9]", "", cname.lower())
            if c_norm and (c_norm == t_norm or t_norm in c_norm or c_norm in t_norm):
                # only group by categorical-ish columns
                if (c.get("semantic_type") or "") in ("dimension", "text", "") or "char" in (c.get("type") or "").lower():
                    return cname
    return None


def try_build_template_sql(
    intent,
    db_type: str,
    metric_definitions: list,
    date_bounds: Optional[tuple] = None,      # (start_iso, end_iso) from _compute_date_bounds
    n_periods: Optional[int] = None,          # expected buckets when granularity is set
    enriched=None,
    user_filter_clause: Optional[str] = None, # mandatory role-based access filter
) -> Optional[QueryPlan]:
    """
    Build SQL deterministically for simple shapes. Returns None whenever the
    question doesn't fit a template exactly — the LLM path then takes over.

    Requires EXACTLY ONE matched metric definition (the registry is the source
    of truth for table/expression/date column) and no explicit filters beyond
    the definition's own.
    """
    if len(metric_definitions or []) != 1:
        return None
    if getattr(intent.entities, "filters", []):
        return None  # user filters need LLM interpretation

    defn = metric_definitions[0]
    table = defn["table"]
    expr = defn["expression"]
    date_col = defn.get("date_column")
    metric_name = defn["name"]
    granularity = getattr(intent.entities, "time_granularity", None)
    dimensions = list(getattr(intent.entities, "dimensions", []) or [])
    # drop time words from dimensions — granularity handles those
    dimensions = [d for d in dimensions if d.lower() not in
                  ("month", "year", "day", "week", "quarter", "date", "time")]

    where_parts: list = []
    if defn.get("filter"):
        where_parts.append(f"({defn['filter']})")
    if user_filter_clause:
        where_parts.append(f"({user_filter_clause})")

    dialect = "redshift" if db_type == "redshift" else (
        "mysql" if db_type == "mysql" else "postgresql")

    # ── Shape 1: time-series with granularity (date spine, zero-filled) ────────
    if granularity and date_col:
        n = n_periods or {"day": 30, "week": 12, "month": 12, "quarter": 8, "year": 5}[granularity]
        spine = _date_spine_sql(db_type, granularity, n)
        if not spine:
            return None
        bucket = _bucket_expr(db_type, granularity, f"t.{date_col}")
        data_where = ""
        if where_parts:
            data_where = " AND " + " AND ".join(where_parts).replace(date_col, f"t.{date_col}")
        # COUNT-style expressions must count a table column so LEFT JOIN misses = 0
        join_expr = expr
        if re.fullmatch(r"COUNT\(\s*\*\s*\)", expr, re.IGNORECASE):
            pk = None
            if enriched and getattr(enriched, "compact_tables", None):
                ct = next((t for t in enriched.compact_tables if t.get("name") == table), None)
                if ct:
                    pk = next((c.get("name") for c in ct.get("columns", [])
                               if c.get("semantic_type") == "pk"), None)
                    if not pk and ct.get("columns"):
                        pk = ct["columns"][0].get("name")
            join_expr = f"COUNT(t.{pk})" if pk else "COUNT(t.*)"
        else:
            join_expr = re.sub(r"\(\s*(\w+)\s*\)", r"(t.\1)", expr)

        if db_type == "mysql":
            # spine already includes WITH RECURSIVE — inline differently
            sql = (
                f"{spine.replace('SELECT bucket FROM spine', '')}"
                f"SELECT s.bucket AS \"Period\", {join_expr} AS \"{metric_name}\"\n"
                f"FROM spine s LEFT JOIN {table} t ON {_bucket_expr(db_type, granularity, f't.{date_col}')} = s.bucket{data_where}\n"
                f"GROUP BY s.bucket ORDER BY s.bucket"
            )
        else:
            sql = (
                f"WITH date_spine AS (\n    {spine}\n)\n"
                f"SELECT ds.bucket AS \"Period\", {join_expr} AS \"{metric_name}\"\n"
                f"FROM date_spine ds\n"
                f"LEFT JOIN {table} t ON {bucket} = ds.bucket{data_where}\n"
                f"GROUP BY ds.bucket\nORDER BY ds.bucket"
            )
        return QueryPlan(
            sql=sql,
            chart_type="bar_vertical" if granularity == "day" else "line",
            table_used=table,
            x_axis_label="Period",
            y_axis_label=metric_name.title(),
            title=f"{metric_name.title()} per {granularity.title()}",
            reasoning=f"Deterministic template: registered metric '{metric_name}' bucketed by {granularity} with zero-fill date spine.",
            db_dialect=dialect,
        )

    # ── Shape 2: one-dimension breakdown ────────────────────────────────────────
    if dimensions and not granularity:
        dim_col = None
        if enriched and getattr(enriched, "compact_tables", None):
            ct = next((t for t in enriched.compact_tables if t.get("name") == table), None)
            dim_col = _resolve_dimension_column(dimensions, ct)
        if not dim_col:
            return None
        wp = list(where_parts)
        if date_bounds and date_col:
            wp.append(f"{date_col} >= '{date_bounds[0]}' AND {date_col} <= '{date_bounds[1]}'")
        where_sql = ("WHERE " + " AND ".join(wp) + "\n") if wp else ""
        sql = (
            f"SELECT {dim_col} AS \"{dim_col}\", {expr} AS \"{metric_name}\"\n"
            f"FROM {table}\n{where_sql}"
            f"GROUP BY {dim_col}\nORDER BY 2 DESC\nLIMIT 20"
        )
        return QueryPlan(
            sql=sql,
            chart_type="bar_vertical",
            table_used=table,
            x_axis_label=dim_col,
            y_axis_label=metric_name.title(),
            title=f"{metric_name.title()} by {dim_col}",
            reasoning=f"Deterministic template: registered metric '{metric_name}' grouped by {dim_col}.",
            db_dialect=dialect,
        )

    # ── Shape 3: single KPI ─────────────────────────────────────────────────────
    if not dimensions and not granularity:
        wp = list(where_parts)
        if date_bounds and date_col:
            wp.append(f"{date_col} >= '{date_bounds[0]}' AND {date_col} <= '{date_bounds[1]}'")
        where_sql = ("WHERE " + " AND ".join(wp) + "\n") if wp else ""
        sql = f"SELECT {expr} AS \"{metric_name}\"\nFROM {table}\n{where_sql}".rstrip()
        return QueryPlan(
            sql=sql,
            chart_type="kpi",
            table_used=table,
            x_axis_label="",
            y_axis_label="",
            title=f"Total {metric_name.title()}",
            reasoning=f"Deterministic template: registered metric '{metric_name}' as single KPI.",
            db_dialect=dialect,
        )

    return None
