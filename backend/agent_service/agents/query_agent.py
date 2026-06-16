import json
import re
from typing import TYPE_CHECKING, Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL
from shared.schemas.agent import IntentResult
from shared.schemas.schema import SemanticSchemaDocument
from shared.schemas.chart import QueryPlan
from agent_service.agents.sql_utils import expand_table_aliases, basic_sql_lint, verify_columns_against_schema

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema


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
1. SELECT only — never write INSERT, UPDATE, DELETE, DROP, or any DDL.
2. Always include an ORDER BY clause.
3. Apply LIMIT — 10000 for chart queries, 100000 for table widgets.
4. For time-series: use date_trunc('month', date_col) for monthly grouping (PostgreSQL/Redshift).
5. For bar charts: ORDER BY metric DESC, LIMIT 20 unless user specified otherwise.
6. For KPI cards: return a single aggregate value (SUM, COUNT, AVG).
7. Choose column aliases that match what should appear as axis labels.
8. Prefer the most important table from the schema unless user specified another.
9. Phase 2: you MAY join 2 tables when the metric and dimension don't share a table. Max 1 join. Use INNER JOIN or LEFT JOIN only.
10. For MySQL: use DATE_FORMAT instead of date_trunc.

CHART TYPE SQL PATTERNS:
- line: SELECT date_trunc('month', date_col) AS period, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 1
- bar_vertical: SELECT dim AS category, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 20
- bar_horizontal: same as bar_vertical
- waterfall: SELECT step_name AS category, delta_value AS value FROM table ORDER BY step_order LIMIT 20
  [Each row is one step: positive value = gain (green bar), negative = loss (red bar), last row labelled "Total"/"Net" becomes the summary bar.
   For derived waterfalls (no dedicated waterfall table): SELECT period AS category, SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 1 LIMIT 20]
- pie: SELECT dim AS label, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 8
- donut: same as pie
- kpi: SELECT {agg}(metric) AS value FROM table  [CRITICAL: NO WHERE, NO GROUP BY — must return exactly 1 row]
- multi_row_card: SELECT dim AS label, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 20
  [Use when the user wants a KPI broken down by a category — produces label/value pairs like "TAO: 231 / VCS: 6531"]
- scatter: SELECT x_col AS x, y_col AS y FROM table LIMIT 1000
- table: SELECT relevant_cols FROM table ORDER BY sort_col DESC LIMIT 100
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

CONCRETE EXAMPLES — copy the pattern, not the table/column names:

BAR CHART (counts by category):
  Chart: "Open Positions by Job Role", x="Job Role", y="Count"
  SQL:   SELECT job_role AS "Job Role", COUNT(*) AS "Count"
         FROM jobs
         WHERE job_role IS NOT NULL
         GROUP BY job_role ORDER BY 2 DESC LIMIT 20

LINE CHART (trend over time):
  Chart: "Monthly Revenue", x="Month", y="Revenue ($)"
  SQL:   SELECT DATE_TRUNC('month', order_date) AS "Month",
                SUM(amount) AS "Revenue"
         FROM orders
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
         GROUP BY source ORDER BY 2 DESC LIMIT 20

WATERFALL CHART (bridge / variance decomposition — positive=gain, negative=loss, last row=total):
  Chart: "Revenue Bridge Q1→Q2", steps=cost categories
  SQL:   SELECT step_name AS "Category", delta AS "Change"
         FROM revenue_bridge
         ORDER BY step_order LIMIT 20
  If no dedicated bridge table, derive month-over-month deltas:
  SQL:   SELECT TO_CHAR(DATE_TRUNC('month', sale_date), 'Mon YYYY') AS "Month",
                SUM(revenue) - LAG(SUM(revenue)) OVER (ORDER BY DATE_TRUNC('month', sale_date)) AS "Change"
         FROM sales
         GROUP BY 1 ORDER BY DATE_TRUNC('month', sale_date) LIMIT 20

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
    ) -> QueryPlan:
        # Prefer enriched compact_tables with TF-IDF ranking when available
        if enriched and enriched.compact_tables:
            intent_text = (intent.reasoning or "") + " " + " ".join(
                intent.entities.metrics + intent.entities.dimensions
            )
            scored = sorted(
                enriched.compact_tables,
                key=lambda t: _score_table(intent_text, t),
                reverse=True,
            )
            # On retries widen to top 12; first attempt top 8
            top_n = 12 if attempt > 1 else 8
            top_tables = scored[:top_n]
            tables_context = [
                {
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
                }
                for t in top_tables
            ]
            important = [t.get("name") for t in top_tables[:5]]
            semantics = enriched.get_table_semantics_text(important)
        else:
            important = schema.important_tables[:5]
            tables_context = []
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
            semantics = ""

        user_content: dict = {
            "user_intent": intent.reasoning,
            "intent_type": intent.intent_type,
            "entities": {
                "metrics": intent.entities.metrics,
                "dimensions": intent.entities.dimensions,
                "time_range": intent.entities.time_range.model_dump() if intent.entities.time_range else None,
                "chart_type_hint": intent.entities.chart_type,
                "filters": [f.model_dump() for f in intent.entities.filters],
            },
            "vagueness_score": intent.vagueness_score,
            "db_type": db_type,
            "schema": {"important_tables": important, "tables": tables_context},
        }
        if semantics:
            user_content["table_semantics"] = semantics

        if retry_feedback:
            user_content["retry_feedback"] = retry_feedback
            user_content["attempt"] = attempt
            user_content["instruction"] = "This is a retry. Apply the retry_feedback to fix the previous query."

        # Increase temperature on retries to force diverse SQL exploration
        _temp = 0.10 if attempt == 1 else min(0.20 + (attempt - 1) * 0.10, 0.45)
        raw = await bedrock_invoke(
            model_id=QUERY_AGENT_MODEL,
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

