import calendar
import json
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING, Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL
from shared.schemas.agent import IntentResult
from shared.schemas.schema import SemanticSchemaDocument
from shared.schemas.chart import QueryPlan
from agent_service.agents.sql_utils import (
    expand_table_aliases, basic_sql_lint, verify_columns_against_schema,
    normalize_string_comparisons_snowflake,
)

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema
    from agent_service.agents.graph_rag_retriever import RetrievedContext


def _months_ago_q(today: date, n: int) -> date:
    month, year = today.month - n, today.year
    while month <= 0:
        month += 12; year -= 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


def _compute_date_bounds(time_range_value: str) -> tuple[str, str] | None:
    """Convert a relative time-range string to concrete (start_iso, end_iso) dates.
    Returns None when the pattern is not recognised."""
    lower = (time_range_value or "").lower().strip()
    today = date.today()

    m = re.search(r"\blast\s+(\d+)\s+(day|week|month|year)s?\b", lower)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "day":   start = today - timedelta(days=n)
        elif unit == "week":start = today - timedelta(weeks=n)
        elif unit == "month": start = _months_ago_q(today, n)
        else:               start = _months_ago_q(today, n * 12)
        return start.isoformat(), today.isoformat()

    if re.search(r"\bthis\s+year\b|\bytd\b|\byear[- ]to[- ]date\b", lower):
        return date(today.year, 1, 1).isoformat(), today.isoformat()

    if re.search(r"\blast\s+year\b|\bprevious\s+year\b", lower):
        return date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()

    if re.search(r"\bthis\s+month\b", lower):
        return date(today.year, today.month, 1).isoformat(), today.isoformat()

    if re.search(r"\blast\s+month\b|\bprevious\s+month\b", lower):
        first_this = date(today.year, today.month, 1)
        end_prev   = first_this - timedelta(days=1)
        return date(end_prev.year, end_prev.month, 1).isoformat(), end_prev.isoformat()

    if re.search(r"\blast\s+quarter\b|\bprevious\s+quarter\b", lower):
        q = (today.month - 1) // 3
        if q == 0:
            return date(today.year - 1, 10, 1).isoformat(), date(today.year - 1, 12, 31).isoformat()
        sm = (q - 1) * 3 + 1
        return (date(today.year, sm, 1).isoformat(),
                date(today.year, sm + 2, calendar.monthrange(today.year, sm + 2)[1]).isoformat())

    return None


def _score_table(intent_text: str, table: dict) -> float:
    """Word-overlap score between intent text and a compact_table entry."""
    words = set(re.sub(r"[^a-z0-9_]", " ", intent_text.lower()).split())
    table_text = (
        (table.get("description") or "")
        + " " + table.get("name", "")
        + " " + " ".join(c.get("name", "") for c in table.get("columns", []))
    )
    twords = set(re.sub(r"[^a-z0-9_]", " ", table_text.lower()).split())
    if not twords:
        return 0.0
    return len(words & twords) / (len(words) + 1)

QUERY_AGENT_MODEL = BEDROCK_SONNET_MODEL

from shared.bedrock_client import BEDROCK_HAIKU_MODEL as _HAIKU_MODEL

def _pick_model(
    intent,
    retrieved_context,
    attempt: int,
    retry_feedback,
) -> str:
    """
    Use Haiku (fast, cheap) for simple single-table queries with high RAG confidence.
    Fall back to Sonnet for complex, multi-table, low-confidence, or retry scenarios.
    """
    if attempt > 1 or retry_feedback:
        return QUERY_AGENT_MODEL  # Sonnet on retries — needs more reasoning
    if retrieved_context is None:
        return QUERY_AGENT_MODEL
    confidence = getattr(retrieved_context, "confidence", 0.0)
    needs_join = getattr(retrieved_context, "needs_join", False)
    num_candidates = len(getattr(retrieved_context, "candidates", []))
    num_metrics = len(getattr(intent.entities, "metrics", []))
    has_filters = bool(getattr(intent.entities, "filters", []))
    # Date-spine queries (granularity breakdowns) need CTE reasoning — Sonnet only
    has_granularity = bool(getattr(intent.entities, "time_granularity", None))
    is_simple = (
        confidence >= 0.65
        and not needs_join
        and num_candidates <= 1
        and num_metrics <= 2
        and not has_filters
        and not has_granularity
    )
    return _HAIKU_MODEL if is_simple else QUERY_AGENT_MODEL


# 4096 tokens: context-enriched prompts (Mode 3) produce larger SQL with CTEs,
# multi-table JOINs, CASE expressions, and date filters that can exceed 1024 tokens.
_SQL_MAX_TOKENS = 4096

# Chart types that are intrinsically temporal — always benefit from a date filter
_TIME_CHART_TYPES = frozenset({
    "line", "area", "stacked_area", "area_stacked",
    "calendar_heatmap", "ribbon", "timeline", "gantt",
})

# Keywords that indicate a chart has a time dimension even if the type isn't line/area
_TIME_KEYWORDS = frozenset({
    "month", "monthly", "year", "yearly", "annual", "quarterly",
    "week", "weekly", "daily", "ytd", "mtd", "qtd",
    "trend", "over time", "by month", "by quarter", "by year",
    "by week", "by date", "by period",
})

_MONTH_ABBREVS = frozenset({
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
})

_QUARTER_LABELS = frozenset({"q1", "q2", "q3", "q4"})


_KPI_PERIOD_KEYWORDS = frozenset({
    "ytd", "mtd", "qtd", "this year", "this month", "this quarter",
    "current year", "last year", "last month", "last quarter",
    "year to date", "month to date", "quarter to date",
})
_KPI_PERIOD_MONTH_ABBREVS = frozenset({
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
})


def _detect_tick_granularity(x_tick_labels: list) -> str | None:
    """
    Infer date grouping granularity (year/quarter/month) from x-axis tick labels.
    Returns "year" | "quarter" | "month" | "week" | None.
    """
    if not x_tick_labels:
        return None
    ticks = [str(t).lower().strip() for t in x_tick_labels[:8]]
    import re as _re_gran
    # Quarters: Q1, Q2, Q1 2024, FY24 Q1, etc.
    if sum(1 for t in ticks if any(q in t for q in ("q1", "q2", "q3", "q4"))) >= 2:
        return "quarter"
    # Month abbreviations
    if sum(1 for t in ticks if any(m in t for m in _MONTH_ABBREVS)) >= 3:
        return "month"
    # Full month names
    _FULL_MONTHS = {
        "january", "february", "march", "april", "june",
        "july", "august", "september", "october", "november", "december",
    }
    if sum(1 for t in ticks if any(mn in t for mn in _FULL_MONTHS)) >= 3:
        return "month"
    # Year labels: 2020, 2021 (only years, no month detail)
    year_ticks = [t for t in ticks if _re_gran.match(r"^20\d{2}$", t.strip())]
    if len(year_ticks) >= 3:
        return "year"
    # Week indicators: "Week 1", "W01", "wk 3"
    if sum(1 for t in ticks if _re_gran.search(r"\b(week|wk|w)\s*\d", t)) >= 2:
        return "week"
    return None


def _chart_needs_date_filter(chart_spec: dict) -> bool:
    """
    Return True only when the chart is genuinely time-based and benefits from
    a date WHERE clause.

    Rules:
    - KPI / gauge: NEVER — UNLESS title/metric name contains explicit period keywords
      (YTD, 2024, last year, etc.) in which case a date WHERE is required.
    - line / area / calendar_heatmap / timeline / gantt: ALWAYS — intrinsically temporal
    - Everything else (bar, pie, table, treemap …): only when title / axis labels /
      x_tick_labels contain explicit time references (year pattern, month abbrev,
      quarter label, or time keyword like "monthly" / "ytd")
    """
    chart_type = (chart_spec.get("type") or "").lower().replace("-", "_")
    import re as _re_dt2

    if chart_type in ("kpi", "kpi_card", "gauge"):
        # KPI needs a date filter when its title/metric explicitly names a period
        kpi_text = " ".join(filter(None, [
            chart_spec.get("title") or "",
            chart_spec.get("kpi_metric_name") or "",
            chart_spec.get("subtitle") or "",
        ])).lower()
        if any(kw in kpi_text for kw in _KPI_PERIOD_KEYWORDS):
            return True
        # Year pattern in title: "Revenue 2024", "2023 Headcount"
        if _re_dt2.search(r"\b20\d{2}\b", kpi_text):
            return True
        # Month abbreviation in KPI title
        if any(m in kpi_text for m in _KPI_PERIOD_MONTH_ABBREVS):
            return True
        return False

    if chart_type in _TIME_CHART_TYPES:
        return True

    # For bar / pie / table and other types: check whether the chart text mentions time
    text = " ".join(filter(None, [
        chart_spec.get("title") or "",
        chart_spec.get("x_axis_label") or "",
        chart_spec.get("y_axis_label") or "",
    ])).lower()

    if any(kw in text for kw in _TIME_KEYWORDS):
        return True

    # Check x_tick_labels for year patterns (20xx), month abbrevs, or quarter labels
    ticks = [str(t).lower() for t in (chart_spec.get("x_tick_labels") or [])[:8]]
    if ticks:
        import re as _re_dt
        if any(_re_dt.search(r"\b20\d{2}\b", t) for t in ticks):
            return True
        if any(any(m in t for m in _MONTH_ABBREVS) for t in ticks):
            return True
        if any(any(q in t for q in _QUARTER_LABELS) for t in ticks):
            return True

    return False

SYSTEM_PROMPT = """You are an expert SQL query generator for a data visualization platform.
Given a user's intent, extracted entities, and a database schema document, generate a SQL query that produces correct data for the requested visualization.

RULES:
1. SELECT / WITH only — NEVER write INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, MERGE,
   TRUNCATE, CALL, VACUUM, LOCK, or any DDL/DML. This is an absolute read-only guardrail.
2. Always include an ORDER BY clause.
3. LIMIT CLAUSE — DO NOT AUTO-ADD SMALL LIMITS: Only add LIMIT when the chart type requires it
   for readability (pie/donut ≤ 20 slices, scatter/bubble ≤ 1000 points, funnel ≤ 10 stages,
   slicer ≤ 300 values) OR when the user explicitly asks for a specific row count (e.g. "top 10",
   "limit to 5"). Never add LIMIT to bar, line, area, table, KPI, multi_row_card, or aggregate
   queries unless the user requests it.
4. For time-series: use date_trunc('month', date_col) for monthly grouping (PostgreSQL/Redshift).
5. For bar charts: ORDER BY metric DESC — no automatic LIMIT unless user specified a count.
6. For KPI cards: return a single aggregate value (SUM, COUNT, AVG).
7. Choose column aliases that match what should appear as axis labels.
8. Prefer the most important table from the schema unless user specified another.
9. Phase 2: you MAY join 2 tables when the metric and dimension don't share a table. Max 1 join. Use INNER JOIN or LEFT JOIN only.
10. For MySQL: use DATE_FORMAT instead of date_trunc.
11. TIME RANGE FILTERING (CRITICAL — apply whenever "time_range" is present in entities):
    - ALWAYS add a WHERE clause that restricts the date column to the requested range.
    - "last N months"  →  WHERE date_col >= CURRENT_DATE - INTERVAL 'N months'          (PostgreSQL/Redshift)
    - "last N days"    →  WHERE date_col >= CURRENT_DATE - INTERVAL 'N days'
    - "last N years"   →  WHERE date_col >= CURRENT_DATE - INTERVAL 'N years'
    - "this month"     →  WHERE DATE_TRUNC('month', date_col) = DATE_TRUNC('month', CURRENT_DATE)
    - "this year"      →  WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE)
    - "last year"      →  WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE) - 1
    - "ytd"            →  WHERE date_col >= DATE_TRUNC('year', CURRENT_DATE)
    - "last quarter"   →  WHERE date_col >= DATE_TRUNC('quarter', CURRENT_DATE) - INTERVAL '3 months'
                           AND date_col < DATE_TRUNC('quarter', CURRENT_DATE)
    - MySQL equivalents: CURDATE() instead of CURRENT_DATE, DATE_SUB(CURDATE(), INTERVAL N MONTH) for intervals
    - Redshift: same as PostgreSQL — CURRENT_DATE and INTERVAL work identically
    - NEVER skip the WHERE clause when a time_range is given — without it the query returns ALL history.
    - Place the date WHERE clause BEFORE GROUP BY.
12. DATE SPINE — ZERO-FILL (CRITICAL for day-wise / date-grouped charts):
    When a user asks for a day-wise, date-wise, or daily breakdown (e.g. "last 7 days",
    "last 30 days", "each day this month") and the chart has a DATE column on the X-axis,
    you MUST generate a date spine and LEFT JOIN the actual data to it.
    This ensures every day in the requested range appears on the chart even when count = 0.
    NEVER use plain GROUP BY date — it silently drops zero-count days.

    Snowflake pattern (use when db_dialect is "snowflake" or connection is Snowflake):
      WITH date_spine AS (
          SELECT DATEADD(day, seq4(), DATEADD(day, -(N-1), CURRENT_DATE())) AS dt
          FROM TABLE(GENERATOR(ROWCOUNT => N))
      )
      SELECT ds.dt AS "Date", COUNT(t.id) AS "Count"
      FROM date_spine ds
      LEFT JOIN schema_name.table_name t ON DATE(t.date_column) = ds.dt
      GROUP BY ds.dt ORDER BY ds.dt

    PostgreSQL / Redshift pattern:
      WITH date_spine AS (
          SELECT generate_series(
              CURRENT_DATE - INTERVAL 'N-1 days',
              CURRENT_DATE,
              INTERVAL '1 day'
          )::date AS dt
      )
      SELECT ds.dt AS "Date", COUNT(t.id) AS "Count"
      FROM date_spine ds
      LEFT JOIN table_name t ON DATE(t.date_column) = ds.dt
      GROUP BY ds.dt ORDER BY ds.dt

    MySQL pattern:
      WITH RECURSIVE date_spine AS (
          SELECT CURDATE() - INTERVAL (N-1) DAY AS dt
          UNION ALL
          SELECT dt + INTERVAL 1 DAY FROM date_spine WHERE dt < CURDATE()
      )
      SELECT ds.dt AS "Date", COUNT(t.id) AS "Count"
      FROM date_spine ds
      LEFT JOIN table_name t ON DATE(t.date_column) = ds.dt
      GROUP BY ds.dt ORDER BY ds.dt

    Replace N with the actual number of days requested (e.g. 7 for "last 7 days").
    Use COUNT(t.primary_key_col) — NOT COUNT(*) — so that unmatched LEFT JOIN rows return 0.

    THE SAME ZERO-FILL RULE APPLIES TO WEEK-WISE AND MONTH-WISE CHARTS:
    months/weeks with no data MUST still appear with value 0.
    Snowflake month spine ("last 12 months"):
      WITH month_spine AS (
          SELECT DATE_TRUNC('month', DATEADD(month, -seq4(), CURRENT_DATE())) AS mth
          FROM TABLE(GENERATOR(ROWCOUNT => 12))
      )
      SELECT ms.mth AS "Month", COUNT(t.id) AS "Count"
      FROM month_spine ms
      LEFT JOIN schema.table_name t ON DATE_TRUNC('month', t.date_column) = ms.mth
      GROUP BY ms.mth ORDER BY ms.mth
    PostgreSQL/Redshift month spine: generate_series(DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '11 months', DATE_TRUNC('month', CURRENT_DATE), INTERVAL '1 month')
    For week spines use DATE_TRUNC('week', ...) with 1-week steps.

13. TIME GRANULARITY (CRITICAL — when "time_granularity" is present in entities):
    The user explicitly asked for that time bucket. You MUST group dates at EXACTLY
    that unit — no other:
    - "day"     → DATE_TRUNC('day', date_col)  or DATE(date_col)
    - "week"    → DATE_TRUNC('week', date_col)
    - "month"   → DATE_TRUNC('month', date_col)
    - "quarter" → DATE_TRUNC('quarter', date_col)
    - "year"    → DATE_TRUNC('year', date_col)  or EXTRACT(YEAR FROM date_col)
    NEVER substitute a different unit. "month wise" with daily rows or yearly totals
    is WRONG. Combine with the date-spine rule so empty buckets appear as 0.

CHART TYPE SQL PATTERNS:
- line: SELECT date_trunc('month', date_col) AS period, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 1
- bar_vertical: SELECT dim AS category, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC
- bar_horizontal: same as bar_vertical
- waterfall: SELECT step_name AS category, delta_value AS value FROM table ORDER BY step_order
  [Each row is one step: positive value = gain (green bar), negative = loss (red bar), last row labelled "Total"/"Net" becomes the summary bar.
   For derived waterfalls (no dedicated waterfall table): SELECT period AS category, SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 1]
- pie: SELECT dim AS label, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 20
- donut: same as pie
- kpi: SELECT {agg}(metric) AS value FROM table  [CRITICAL: NO WHERE, NO GROUP BY — must return exactly 1 row]
- multi_row_card: SELECT dim AS label, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC
  [Use when the user wants a KPI broken down by a category — produces label/value pairs like "TAO: 231 / VCS: 6531"]
- scatter: SELECT x_col AS x, y_col AS y FROM table LIMIT 1000
- table: SELECT relevant_cols FROM table ORDER BY sort_col DESC
- area: SELECT date_trunc('month', date_col) AS period, SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 1
- stacked_bar: SELECT dim1, dim2, SUM(metric) AS value FROM table GROUP BY 1, 2 ORDER BY 3 DESC
- grouped_bar: SELECT dim1, dim2, SUM(metric) AS value FROM table GROUP BY 1, 2 ORDER BY 1
- funnel: SELECT stage_col AS stage, COUNT(*) AS count FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 10
- gauge: SELECT SUM(metric) AS current_value FROM table
- treemap: SELECT parent_dim, child_dim, SUM(metric) AS value FROM table GROUP BY 1, 2
- pivot_table: SELECT row_dim, col_dim, SUM(metric) AS value FROM table GROUP BY 1, 2
- radar: SELECT subject_dim, series_a_val, series_b_val, series_c_val FROM table GROUP BY 1 ORDER BY 1 LIMIT 10
- ribbon: SELECT period_col, cat_a_val, cat_b_val, cat_c_val FROM table GROUP BY 1 ORDER BY 1
- bullet: SELECT metric_name, actual_value, target_value FROM table ORDER BY 1 LIMIT 20
- scorecard: SELECT metric_name, actual_value, target_value FROM table ORDER BY 1 LIMIT 20
- dot_plot: SELECT category_col, numeric_value FROM table ORDER BY 1 LIMIT 500
- box_plot: SELECT category_col, MIN(val) AS min_val, PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY val) AS q1, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY val) AS median_val, PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY val) AS q3, MAX(val) AS max_val FROM table GROUP BY 1 ORDER BY 1
- sankey: SELECT source_col, target_col, SUM(flow_metric) AS value FROM table GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50
- chord: SELECT from_entity, to_entity, SUM(value_metric) AS value FROM table GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 100
- network: SELECT source_node, target_node, SUM(weight_metric) AS weight FROM table GROUP BY 1, 2 LIMIT 100
- gantt: SELECT task_name, start_date, end_date, category_col FROM table ORDER BY start_date LIMIT 50
- timeline: SELECT event_name, event_date FROM table ORDER BY event_date LIMIT 50
- calendar_heatmap: SELECT date_col AS date, SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 1
- word_cloud: SELECT term_col AS word, COUNT(*) AS frequency FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 100
- org_chart: SELECT id_col, name_col, parent_id_col FROM table ORDER BY 1 LIMIT 200
- marimekko: SELECT category_col, segment_col, SUM(value_metric) AS value FROM table GROUP BY 1, 2 ORDER BY 1
- choropleth: SELECT region_col AS region, SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC
- slicer: SELECT DISTINCT filter_col FROM table WHERE filter_col IS NOT NULL ORDER BY 1 LIMIT 300
  [Slicer is a FILTER CONTROL widget — the SQL must return one column of distinct values for the dropdown/checkbox.
   Set slicer_type: "dropdown" (single value), "checkbox" (multi-select), or "date_range" (date picker).
   Set slicer_column to the exact column name that will be filtered in other widgets' queries.]

KPI ABSOLUTE RULES (when chart_type is "kpi"):
- SELECT exactly ONE aggregate — SUM(col), COUNT(*), AVG(col), or COUNT(DISTINCT col)
- NO WHERE clause — scan the full table unless a date constraint was explicitly given
- NO GROUP BY — never group, this is a scalar metric
- The query MUST return exactly 1 row with 1 numeric column
- Wrong: SELECT job_role, COUNT(*) FROM jobs GROUP BY 1  → this returns N rows, not 1
- Right:  SELECT COUNT(*) AS total_jobs FROM jobs
- EXCEPTION — when kpi_grouped=true in chart_spec: the KPI card shows multiple values
  broken down by a dimension (e.g. "TAO: 231 / VCS: 6531"). In this case:
  USE the kpi_group_labels as the GROUP BY dimension values.
  SQL: SELECT {group_col}, COUNT(*) AS value FROM table GROUP BY 1 ORDER BY 1
  where {group_col} is the column whose distinct values match kpi_group_labels.

PBIT FIELD BINDINGS (when pbit_field_bindings is present in the request — ABSOLUTE PRIORITY):
- The field_bindings dict maps visual roles to columns/measures:
    Category / Axis / X → GROUP BY dimension column
    Y / Values / Measure → aggregate metric (COUNT, SUM, AVG, or a measure expression)
    Legend / Series / Color → secondary GROUP BY for series breakdown
    Tooltips → additional SELECT columns (do not GROUP BY these)
- Column references in field_bindings follow "TableName.ColumnName" or "TableName.[Measure]" format.
  Strip brackets and use only the column/measure name in SQL.
- If a measure name appears in both field_bindings and the measures dict, use the measures.sql expression
  directly as the SELECT expression (it is the translated DAX formula).
- NEVER invent a column not in field_bindings — only columns/measures listed there go in SELECT.
- db_tables lists the EXACT DB tables to use — do not add or replace these.

REDSHIFT-SPECIFIC RULES (when db_dialect is "redshift"):
- Use GROUP BY 1, 2 (ordinal positions), NOT column alias names
- Use GETDATE() instead of NOW()
- Use ILIKE for case-insensitive matching
- VARCHAR max 65535, no TEXT type
- Use DATE_TRUNC (same as PostgreSQL)
- COLUMN REFERENCE RULE (critical): column references in SELECT/WHERE/JOIN ON must use
  ONLY "alias.column" or "table_name.column" — NEVER the 3-level "schema.table.column"
  form (this is invalid SQL and will raise "invalid reference to FROM-clause entry").
  Always assign aliases to schema-qualified tables in FROM:
    CORRECT:  FROM staging.bullhorn_client_corporation AS cc  →  SELECT cc.name
    WRONG:    SELECT staging.bullhorn_client_corporation.name  (3-level = invalid)
  If two tables share a column name, qualify with the alias, not the schema prefix.

SNOWFLAKE-SPECIFIC RULES (when db_dialect is "snowflake"):
- COLUMN REFERENCE RULE (critical — same as Redshift): NEVER use 3-part "SCHEMA.TABLE.COLUMN"
  in SELECT/WHERE/JOIN ON. Always assign a short alias to schema-qualified tables:
    CORRECT:  FROM FAR_TRANS.TRANSACTIONS AS t  →  SELECT t.TOTALVALUE
    WRONG:    SELECT FAR_TRANS.TRANSACTIONS.TOTALVALUE  (Snowflake error 000904 invalid ident)
- RESERVED KEYWORDS AS COLUMN NAMES: If a column name is a Snowflake reserved word or type
  keyword (e.g. TIMESTAMP, VALUE, TYPE, DATE, TIME, YEAR, MONTH, DAY, INTERVAL, COLUMN,
  CURRENT, POSITION, TABLE, SCHEMA), always double-quote it:
    CORRECT:  SELECT t."TIMESTAMP" AS "Date"
    WRONG:    SELECT t.TIMESTAMP  (Snowflake treats TIMESTAMP as a type cast, causing errors)
- Use DATE_TRUNC('month', col) — same syntax as PostgreSQL/Redshift.
- Use CURRENT_DATE() (with parentheses) in Snowflake date spine generators (DATEADD, GENERATOR).
  Use CURRENT_DATE (no parentheses) in plain WHERE date comparisons.
- Use ILIKE for case-insensitive string matching.
- Use TO_DATE(col) or TRY_TO_DATE(col) instead of CAST(col AS DATE) for string-to-date.

COLUMN HALLUCINATION PREVENTION (applies to ALL dialects — CRITICAL):
- You will receive an "exact_column_schema" in the user message listing EVERY column in the
  target table. You MUST copy column names EXACTLY as spelled there — do NOT invent, shorten,
  expand, or paraphrase column names.
- If the metric the user requested (e.g. "total net") does not appear as any column in
  exact_column_schema.columns, do NOT guess a plausible-sounding name. Instead set sql to a
  simple COUNT(*) fallback and explain the metric is unavailable in reasoning.
- A query that references a non-existent column will always fail at runtime. A COUNT(*) or
  "no data" result is far better than a SQL compilation error.
- NEVER fabricate table names, column names, or filter values not present in the provided schema.
- NEVER generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, MERGE, TRUNCATE, CALL, or any
  DDL/DML statement — SELECT and WITH only. This rule has no exceptions.

CONCRETE EXAMPLES — copy the pattern, not the table/column names:

BAR CHART (counts by category):
  Chart: "Open Positions by Job Role", x="Job Role", y="Count"
  SQL:   SELECT job_role AS "Job Role", COUNT(*) AS "Count"
         FROM jobs
         WHERE job_role IS NOT NULL
         GROUP BY job_role ORDER BY 2 DESC

LINE CHART (trend over time — NO time_range given, return all history):
  Chart: "Monthly Revenue", x="Month", y="Revenue ($)"
  SQL:   SELECT DATE_TRUNC('month', order_date) AS "Month",
                SUM(amount) AS "Revenue"
         FROM orders
         GROUP BY 1 ORDER BY 1

LINE CHART with time_range "last 3 months" (MUST include WHERE to restrict to that window):
  Chart: "Placement Trend – Last 3 Months"
  SQL:   SELECT DATE_TRUNC('month', placement_date) AS "Month",
                COUNT(*) AS "Placements"
         FROM placements
         WHERE placement_date >= CURRENT_DATE - INTERVAL '3 months'
         GROUP BY 1 ORDER BY 1

KPI CARD (single aggregate — NO GROUP BY, NO WHERE unless chart shows a period):
  Chart: "Total Active Employees"
  SQL:   SELECT COUNT(*) AS "Total Active Employees" FROM employees

PIE CHART (proportional breakdown):
  Chart: "Sales by Region", slices=regions
  SQL:   SELECT region AS "Region", SUM(sales_amount) AS "Sales"
         FROM sales
         GROUP BY region ORDER BY 2 DESC LIMIT 8

GROUPED KPI / MULTI-ROW CARD (multiple metric values broken down by a category):
  Chart: "Job Count by Source", groups=[TAO, VCS]
  SQL:   SELECT source AS "Source", COUNT(*) AS "Jobs"
         FROM bullhorn_core_job_order
         GROUP BY source ORDER BY 2 DESC

DAY-WISE BAR/LINE CHART with date spine (ALWAYS use this for "last N days" with daily granularity):
  Chart: "Daily Placements – Last 7 Days", x="Date", y="Placements"
  Snowflake SQL:
    WITH date_spine AS (
        SELECT DATEADD(day, seq4(), DATEADD(day, -6, CURRENT_DATE())) AS dt
        FROM TABLE(GENERATOR(ROWCOUNT => 7))
    )
    SELECT ds.dt AS "Date", COUNT(t.id) AS "Placements"
    FROM date_spine ds
    LEFT JOIN STG_POC.TRNSCTN t ON DATE(t.created_at) = ds.dt
    GROUP BY ds.dt
    ORDER BY ds.dt
  ← All 7 days appear, zero-count days show 0 instead of being omitted.

WATERFALL CHART (bridge / variance decomposition — positive=gain, negative=loss, last row=total):
  Chart: "Revenue Bridge Q1→Q2", steps=cost categories
  SQL:   SELECT step_name AS "Category", delta AS "Change"
         FROM revenue_bridge
         ORDER BY step_order
  If no dedicated bridge table, derive month-over-month deltas:
  SQL:   SELECT TO_CHAR(DATE_TRUNC('month', sale_date), 'Mon YYYY') AS "Month",
                SUM(revenue) - LAG(SUM(revenue)) OVER (ORDER BY DATE_TRUNC('month', sale_date)) AS "Change"
         FROM sales
         GROUP BY 1 ORDER BY DATE_TRUNC('month', sale_date)

Return ONLY valid JSON:
{
  "sql": "SELECT ...",
  "chart_type": "line|bar_vertical|bar_horizontal|pie|donut|kpi|multi_row_card|scatter|table|area|stacked_bar|grouped_bar|funnel|gauge|treemap|pivot_table|radar|ribbon|bullet|scorecard|dot_plot|box_plot|sankey|chord|network|gantt|timeline|calendar_heatmap|word_cloud|org_chart|marimekko|choropleth|waterfall",
  "table_used": "table_name",
  "x_axis_label": "...",
  "y_axis_label": "...",
  "title": "Auto-generated chart title",
  "reasoning": "Why this SQL and chart type was chosen",
  "db_dialect": "postgresql|mysql|redshift",
  "tables_joined": []
}"""


class QueryAgent:
    async def generate(
        self,
        intent: IntentResult,
        schema: SemanticSchemaDocument,
        db_type: str,
        retry_feedback: Optional[str] = None,
        attempt: int = 1,
        enriched: Optional["EnrichedSchema"] = None,
        retrieved_context: Optional["RetrievedContext"] = None,
        conversation_history: Optional[list] = None,
        user_profile: Optional[dict] = None,
        metric_definitions: Optional[list] = None,
        few_shot_examples: Optional[list] = None,
    ) -> QueryPlan:
        # ── Table selection: Graph RAG > word-overlap > schema.important_tables ──
        if retrieved_context and retrieved_context.primary_tables and enriched and enriched.compact_tables:
            # Use Graph RAG ranked tables — most accurate path
            ct_map = {t["name"]: t for t in enriched.compact_tables}
            # Primary candidates first, then fill with any extras on retries
            ordered = list(dict.fromkeys(retrieved_context.primary_tables))
            if attempt > 1:
                # On retries, add more context: top word-overlap fallback tables
                intent_text = (intent.reasoning or "") + " " + " ".join(
                    intent.entities.metrics + intent.entities.dimensions
                )
                fallback = sorted(
                    enriched.compact_tables,
                    key=lambda t: _score_table(intent_text, t),
                    reverse=True,
                )
                for t in fallback:
                    tn = t.get("name", "")
                    if tn not in ordered:
                        ordered.append(tn)
                    if len(ordered) >= 12:
                        break
            top_n = min(len(ordered), 12 if attempt > 1 else 8)
            top_tables = [ct_map[tn] for tn in ordered[:top_n] if tn in ct_map]
            important = [t.get("name") for t in top_tables[:5]]
            semantics = enriched.get_table_semantics_text(important)
        elif enriched and enriched.compact_tables:
            # Fallback: word-overlap scoring (old path)
            intent_text = (intent.reasoning or "") + " " + " ".join(
                intent.entities.metrics + intent.entities.dimensions
            )
            scored = sorted(
                enriched.compact_tables,
                key=lambda t: _score_table(intent_text, t),
                reverse=True,
            )
            top_n = 12 if attempt > 1 else 8
            top_tables = scored[:top_n]
            important = [t.get("name") for t in top_tables[:5]]
            semantics = enriched.get_table_semantics_text(important)
        else:
            important = schema.important_tables[:5]
            top_tables = []
            semantics = ""

        tables_context = []
        if top_tables:
            for t in top_tables:
                tables_context.append({
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "row_count": t.get("row_count", 0),
                    "columns": [
                        {
                            "name": c.get("name"),
                            "type": c.get("type"),
                            "is_primary_key": c.get("semantic_type") == "pk",
                            "description": c.get("description") or "",
                            "semantic_type": c.get("semantic_type"),
                        }
                        for c in t.get("columns", [])
                    ],
                    "relationships": [
                        {"column": r.get("column"), "references": r.get("references")}
                        for r in (t.get("relationships") or [])
                    ],
                })
        else:
            for table in schema.tables:
                if table.name in important or attempt > 1:
                    tables_context.append({
                        "name": table.name,
                        "description": table.description,
                        "row_count": table.row_count,
                        "columns": [
                            {"name": c.name, "type": c.type, "is_primary_key": c.is_primary_key, "description": c.description}
                            for c in table.columns
                        ],
                        "relationships": [{"column": r.column, "references": r.references} for r in table.relationships],
                    })

        user_content: dict = {
            "user_intent": intent.reasoning,
            "intent_type": intent.intent_type,
            "entities": {
                "metrics": intent.entities.metrics,
                "dimensions": intent.entities.dimensions,
                "time_range": intent.entities.time_range.model_dump() if intent.entities.time_range else None,
                "time_granularity": getattr(intent.entities, "time_granularity", None),
                "chart_type_hint": intent.entities.chart_type,
                "filters": [f.model_dump() for f in intent.entities.filters],
            },
            "vagueness_score": intent.vagueness_score,
            "db_type": db_type,
            "schema": {"important_tables": important, "tables": tables_context},
        }
        if semantics:
            user_content["table_semantics"] = semantics

        # Inject Graph RAG hints when available — steers column/JOIN choices
        if retrieved_context and retrieved_context.candidates:
            from agent_service.agents.graph_rag_retriever import format_retrieval_hints
            hints = format_retrieval_hints(retrieved_context)
            if hints:
                user_content["graph_rag_hints"] = hints

        # Column-exact injection: give the LLM the precise column names/types
        # for the top candidate so it copies rather than guesses
        if enriched and enriched.compact_tables and retrieved_context and retrieved_context.candidates:
            top_tname = retrieved_context.candidates[0].table_name
            ct_map = {t["name"]: t for t in enriched.compact_tables}
            top_ct = ct_map.get(top_tname)
            if top_ct:
                exact_cols = [
                    {
                        "name": c.get("name"),
                        "type": c.get("type"),
                        "semantic_type": c.get("semantic_type"),
                        "description": c.get("description") or "",
                    }
                    for c in top_ct.get("columns", [])
                ]
                user_content["exact_column_schema"] = {
                    "table": top_tname,
                    "is_view": top_ct.get("is_view", False),
                    "columns": exact_cols,
                    "instruction": (
                        "IMPORTANT: use ONLY the column names listed in exact_column_schema.columns. "
                        "Never invent column names. If a needed column is missing, say so in your reasoning."
                    ),
                }
                if getattr(retrieved_context, "needs_join", False) and getattr(retrieved_context, "join_path", []):
                    user_content["exact_column_schema"]["join_path"] = retrieved_context.join_path

        # Inject conversation history so the LLM can resolve follow-up references
        # ("same table", "now add region", "break that down by X", "filter by last year", etc.)
        if conversation_history:
            # Keep the last 6 turns max to stay within token budget
            trimmed = conversation_history[-6:]
            user_content["conversation_history"] = trimmed
            user_content["conversation_note"] = (
                "The user may be asking a follow-up or refinement question. "
                "Use conversation_history to interpret references like 'that chart', "
                "'same table', 'now also by X', 'filter by last year', 'add a column', etc. "
                "If the current query is self-contained ignore the history. "
                "If it is a follow-up, build the SQL that satisfies both the prior intent "
                "and the new refinement."
            )

        # Recently-used table memory: tables used in recent turns get priority
        if conversation_history:
            import re as _re
            recent_tables: list[str] = []
            for turn in conversation_history[-4:]:
                sql_text = turn.get("sql") or ""
                if sql_text:
                    for m in _re.finditer(r'\bFROM\s+([\w.]+)\b', sql_text, _re.IGNORECASE):
                        tname = m.group(1).strip('"').strip("'")
                        if tname and tname not in recent_tables:
                            recent_tables.append(tname)
                    for m in _re.finditer(r'\bJOIN\s+([\w.]+)\b', sql_text, _re.IGNORECASE):
                        tname = m.group(1).strip('"').strip("'")
                        if tname and tname not in recent_tables:
                            recent_tables.append(tname)
            if recent_tables:
                user_content["recently_used_tables"] = {
                    "tables": recent_tables[:4],
                    "note": "These tables were used in recent turns of this conversation. Prefer them when ambiguous.",
                }

        # ── User profile filter (Brainwave role-based access control) ────────────
        # If the user has a role that restricts data access (e.g. placement_specialist),
        # inject a mandatory WHERE clause so the generated SQL is always filtered.
        if user_profile:
            from agent_service.agents.user_context_builder import get_sql_filter_clause
            _sql_filter = get_sql_filter_clause(user_profile)
            _role = user_profile.get("brainwave_role", "")
            _name = user_profile.get("db_name") or user_profile.get("full_name", "")
            print(
                f"[query_agent] user_profile role={_role!r} db_name={_name!r} "
                f"filter={_sql_filter!r}",
                flush=True,
            )
            if _sql_filter:
                user_content["mandatory_user_filter"] = {
                    "instruction": (
                        "MANDATORY ACCESS FILTER — you MUST include this WHERE clause "
                        "in every SQL query you generate. Never omit it. This user can only "
                        "see their own records."
                    ),
                    "where_clause": _sql_filter,
                    "name_in_db":   _name,
                    "role":         _role,
                }

        # ── Authoritative metric definitions (metric registry) ───────────────────
        # When the project has canonical definitions for the metrics mentioned,
        # the LLM must use them verbatim instead of re-deriving table/column/agg.
        if metric_definitions:
            user_content["metric_definitions"] = {
                "definitions": metric_definitions,
                "instruction": (
                    "AUTHORITATIVE: these are this project's canonical metric definitions. "
                    "Use the specified table, aggregation expression, and date column EXACTLY "
                    "as defined — do not substitute other tables or columns for these metrics."
                ),
            }

        # ── Few-shot examples from query memory (past successful queries) ────────
        if few_shot_examples:
            user_content["similar_past_queries"] = {
                "examples": few_shot_examples,
                "note": (
                    "These similar questions were answered successfully before on THIS database. "
                    "Reuse their table choices and SQL patterns when applicable."
                ),
            }

        # ── Hard granularity constraint ───────────────────────────────────────────
        _gran = getattr(intent.entities, "time_granularity", None)
        if _gran:
            user_content["granularity_required"] = {
                "unit": _gran,
                "instruction": (
                    f"MANDATORY: the user asked for a {_gran}-wise breakdown. "
                    f"GROUP BY DATE_TRUNC('{_gran}', date_col) (or dialect equivalent) — "
                    f"exactly one row per {_gran}. Use a date spine (rule 12) so {_gran}s "
                    f"with zero activity still appear with value 0. Do NOT use any other time unit."
                ),
            }

        if retry_feedback:
            user_content["retry_feedback"] = retry_feedback
            user_content["attempt"] = attempt
            user_content["instruction"] = "This is a retry. Apply the retry_feedback to fix the previous query."

        # Pre-compute concrete date bounds for relative time ranges so the LLM writes
        # the correct WHERE clause instead of guessing INTERVAL syntax.
        if intent.entities.time_range and intent.entities.time_range.type == "relative":
            bounds = _compute_date_bounds(intent.entities.time_range.value)
            if bounds:
                start_iso, end_iso = bounds
                user_content["time_filter_required"] = {
                    "start_date": start_iso,
                    "end_date": end_iso,
                    "instruction": (
                        f"MANDATORY: add WHERE {{date_col}} >= '{start_iso}' "
                        f"AND {{date_col}} <= '{end_iso}' "
                        f"to the query. Replace {{date_col}} with the actual date column. "
                        f"NEVER omit this filter — without it you return all history."
                    ),
                }

        # Increase temperature on retries to force diverse SQL exploration
        _temp = 0.10 if attempt == 1 else min(0.20 + (attempt - 1) * 0.10, 0.45)
        _model = _pick_model(intent, retrieved_context, attempt, retry_feedback)
        print(f"[query_agent] model={_model.split('.')[-1]} attempt={attempt} confidence={getattr(retrieved_context, 'confidence', 0):.2f}", flush=True)
        raw = await bedrock_invoke(
            model_id=_model,
            system_prompt=SYSTEM_PROMPT,
            user_message=json.dumps(user_content, default=str),
            max_tokens=_SQL_MAX_TOKENS,
            temperature=_temp,
        )

        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            first_table = schema.important_tables[0] if schema.important_tables else "unknown"
            data = {
                "sql": f"SELECT * FROM {first_table} LIMIT 100",
                "chart_type": "table",
                "table_used": first_table,
                "x_axis_label": "row",
                "y_axis_label": "value",
                "title": "Data Preview",
                "reasoning": "Fallback due to parse error",
                "db_dialect": "redshift" if db_type == "redshift" else ("postgresql" if db_type in ("postgresql",) else "mysql"),
                "tables_joined": [],
            }

        # Post-process: expand short aliases to prevent alias-reference errors
        if data.get("sql"):
            data["sql"] = expand_table_aliases(data["sql"])
            # Snowflake string comparisons are case-sensitive; the LLM commonly guesses
            # lowercase values ('buy', 'sell') when the DB stores mixed-case ('Buy', 'Sell').
            # Rewrite col = 'val' → UPPER(col) = 'VAL' for case-insensitive matching.
            if db_type == "snowflake":
                data["sql"] = normalize_string_comparisons_snowflake(data["sql"])

        return QueryPlan(
            sql=data.get("sql", "SELECT 1"),
            chart_type=data.get("chart_type", "table"),
            table_used=data.get("table_used", "unknown"),
            # KPI/single-value charts have no axes → LLM returns null; coerce to "".
            # (data.get(key, default) does NOT apply the default when the key is
            # present with a null value, so use `or` to catch None.)
            x_axis_label=data.get("x_axis_label") or "",
            y_axis_label=data.get("y_axis_label") or "",
            title=data.get("title", "Chart"),
            reasoning=data.get("reasoning", ""),
            db_dialect=data.get("db_dialect", "postgresql"),
        )

