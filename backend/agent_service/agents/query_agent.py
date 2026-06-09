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
    from agent_service.agents.spec_reader import ChartSpecHint


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
- pie: SELECT dim AS label, {agg}(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 8
- donut: same as pie
- kpi: SELECT {agg}(metric) AS value FROM table  [CRITICAL: NO WHERE, NO GROUP BY — must return exactly 1 row]
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

Return ONLY valid JSON:
{
  "sql": "SELECT ...",
  "chart_type": "line|bar_vertical|bar_horizontal|pie|donut|kpi|scatter|table|area|stacked_bar|grouped_bar|funnel|gauge|treemap|pivot_table|radar|ribbon|bullet|scorecard|dot_plot|box_plot|sankey|chord|network|gantt|timeline|calendar_heatmap|word_cloud|org_chart|marimekko|choropleth",
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
            x_axis_label=data.get("x_axis_label", "x"),
            y_axis_label=data.get("y_axis_label", "y"),
            title=data.get("title", "Chart"),
            reasoning=data.get("reasoning", ""),
            db_dialect=data.get("db_dialect", "postgresql"),
        )

    async def generate_from_chart_spec(
        self,
        chart_spec: dict,
        enriched,  # EnrichedSchema from schema_cache
        attempt: int = 1,
        retry_feedback: Optional[str] = None,
        candidate: Optional[dict] = None,
        sample_context: Optional[dict] = None,  # from value_sampler — real DB values
        date_filter_constraint: Optional[str] = None,  # hard WHERE date constraint from sampler
        error_recovery_mode: Optional[str] = None,     # "no_alias" | "no_union_order"
        dashboard_date_context: Optional[dict] = None, # global date window across all charts
        previously_tried: Optional[list] = None,       # [{tables, failure_reason}] — Layer 2 exploration
        failure_type: Optional[str] = None,            # from classify_failure — drives temperature
        parsed_context: Optional[dict] = None,         # structured signals from context_parser (Mode 3)
        user_context: str = "",                        # raw free-text from user (Mode 3)
        spec_hint: Optional["ChartSpecHint"] = None,  # per-chart SQL template from spec_reader (Mode 3 PDF)
        calc_col_map: Optional[dict] = None,           # {col_name: sql_expression} — PDF calculated columns
        business_rules: Optional[list] = None,         # required WHERE constraints from PDF spec
        pbit_column_hint: Optional[dict] = None,       # PBIT visual field bindings — highest priority
        resolved_spec=None,                            # ResolvedChartSpec from context_synthesizer
    ) -> dict:
        """
        Generate SQL from a Vision Agent ChartSpec (screenshot replication mode).
        enriched: EnrichedSchema with disambiguation + table semantics already built.
        candidate: dict from SchemaMatcher with tables, key_columns, join, reasoning.
        Returns a plain dict with sql, chart_type, x_axis_label, y_axis_label, title.
        """
        schema = enriched.schema_doc
        db_type = enriched.db_type

        # Build the table context sent in the prompt.
        # user_mandated=True means the user explicitly selected these tables —
        # do NOT append any extra tables. Sending extra tables lets the model
        # "accidentally" reference them even when told not to.
        all_tables = {t.get("name"): t for t in enriched.compact_tables}
        if candidate and candidate.get("tables"):
            focus_names = candidate["tables"]
            tables_context = [all_tables[n] for n in focus_names if n in all_tables]
            if not candidate.get("user_mandated"):
                # Non-mandated: add a few neighbours for optional join context
                rest = [t for n, t in all_tables.items() if n not in focus_names][:6]
                tables_context = tables_context + rest
            # user_mandated: show ONLY the selected tables — nothing else visible
        else:
            tables_context = enriched.compact_tables[:12]

        # Build table semantics context for candidate tables
        cand_tables = candidate.get("tables", []) if candidate else []
        semantics_text = enriched.get_table_semantics_text(cand_tables if cand_tables else None)

        user_content = {
            "mode": "screenshot_replication",
            "chart_spec": {
                "chart_type": chart_spec.get("type"),
                "title": chart_spec.get("title"),
                "x_axis_label": chart_spec.get("x_axis_label"),
                "y_axis_label": chart_spec.get("y_axis_label"),
                "x_tick_labels": chart_spec.get("x_tick_labels", []),
                "estimated_values": chart_spec.get("estimated_values", {}),
                "data_point_count": chart_spec.get("data_point_count", 0),
                    "legend_labels": chart_spec.get("legend_labels", []),
                "kpi_grouped": chart_spec.get("kpi_grouped", False),
                "kpi_group_labels": chart_spec.get("kpi_group_labels", []),
            },
            "db_type": db_type,
            "schema": {"tables": tables_context},
            "attempt": attempt,
            "disambiguation": enriched.get_disambiguation_text(),
            "table_semantics": semantics_text,
            "instruction": (
                "Generate SQL that exactly reproduces this chart from the live database. "
                "Match chart type, axis semantics, and data granularity. "
                "Use estimated_values and x_tick_labels as ground-truth hints. "
                "Use the DISAMBIGUATION section to pick the correct column when the same name exists in multiple tables. "
                "Do NOT ask the user — infer everything from the schema, semantics, and chart spec."
            ),
        }

        # Inject FK join conditions from the relationship graph for candidate tables.
        # The LLM cannot infer join keys reliably from column name similarity alone —
        # injecting the exact FK conditions eliminates wrong-column JOIN errors.
        if candidate and candidate.get("tables") and hasattr(enriched, "relationship_graph"):
            _rg = enriched.relationship_graph
            _cand_tables = candidate.get("tables", [])
            _join_lines = []
            for _ta in _cand_tables:
                nbrs = (_rg.edges or {}).get(_ta, {})
                for _tb, _cond in nbrs.items():
                    if _tb in _cand_tables and _cond:
                        _join_lines.append(_cond)
            if _join_lines:
                user_content["join_conditions"] = list(dict.fromkeys(_join_lines))  # deduplicate

        # Mode 3: inject user context as the highest-priority instruction block.
        # This runs BEFORE all other instruction appends so it can override schema inference.
        if parsed_context or user_context:
            from agent_service.agents.context_parser import build_context_instruction as _bci
            ctx_instruction = _bci(parsed_context or {}, user_context)
            if ctx_instruction:
                user_content["instruction"] = ctx_instruction + " | " + user_content["instruction"]
                user_content["user_context"] = user_context or ""
                # Inject structured signals for the model to parse directly
                if parsed_context:
                    _ctx_signals: dict = {}
                    if parsed_context.get("implied_filters"):
                        _ctx_signals["required_filters"] = parsed_context["implied_filters"]
                    _dr = parsed_context.get("implied_date_range") or {}
                    if _dr.get("start"):
                        _ctx_signals["required_date_range"] = _dr
                    if parsed_context.get("implied_aggregation"):
                        _ctx_signals["required_aggregation"] = parsed_context["implied_aggregation"]
                    if parsed_context.get("implied_groupby_hint"):
                        _ctx_signals["required_groupby"] = parsed_context["implied_groupby_hint"]
                    if parsed_context.get("sql_constraints"):
                        _ctx_signals["sql_constraints"] = parsed_context["sql_constraints"]
                    if _ctx_signals:
                        user_content["context_signals"] = _ctx_signals

        # Business rules from PDF spec — mandatory WHERE constraints that Power BI enforces at the
        # model level (e.g. "isdeleted = FALSE on all bullhorn_ tables"). Every query must apply these.
        if business_rules:
            rules_text = "; ".join(business_rules[:10])
            user_content["instruction"] = (
                f"⚑ MANDATORY SQL CONSTRAINTS (from report specification — apply to EVERY query): "
                f"{rules_text}. "
                "These are not optional — omitting them returns soft-deleted or inactive records "
                "that were excluded from the original Power BI report. "
                "| " + user_content["instruction"]
            )
            user_content["business_rules"] = business_rules[:10]
            print(
                f"[query_agent] ✓ business_rules injected: {len(business_rules)} rule(s)",
                flush=True,
            )

        # Spec hint from PDF documentation — injected as the HIGHEST priority instruction block.
        # When present, the SQL template from the document overrides all schema inference.
        # The model must use the documented business logic, filters, and aggregation exactly.
        if spec_hint and spec_hint.sql_template:
            spec_instruction = (
                "⚑ DOCUMENTATION SPEC — A verified SQL template exists for this exact chart. "
                "You MUST replicate this template with FULL FIDELITY. Rules: "
                "(1) Keep EVERY expression in the SELECT list — do NOT drop window functions "
                "(OVER, PARTITION BY), CTEs, CASE WHEN expressions, or percentage calculations. "
                "(2) Keep the same GROUP BY, ORDER BY, and WHERE filter logic. "
                "(3) Only allowed adaptations: add/remove schema prefix, fix column name casing "
                "when a column does not exist as written. "
                "(4) Do NOT simplify a window function to a plain aggregate. "
                "(5) Do NOT replace a CTE with an inline subquery unless the CTE itself fails. "
                "(6) If the template has COALESCE(col, ...) but col is listed as an FK integer "
                "in calc_col_substitutions, use the CASE WHEN from calc_col_substitutions instead. "
                "FAILURE MODE TO AVOID: the template shows `ROUND(100 * COUNT(...) / SUM(COUNT(...)) OVER (), 1)` "
                "and you write just `COUNT(*)` — this produces raw counts instead of percentages and "
                "the chart will fail validation. "
                f"SQL TEMPLATE FROM DOCUMENTATION:\n{spec_hint.sql_template}"
            )
            user_content["instruction"] = spec_instruction + " | " + user_content["instruction"]
            user_content["spec_hint"] = {
                "title": spec_hint.title,
                "visual_type": spec_hint.visual_type,
                "tables_needed": spec_hint.tables_needed,
                "filters": spec_hint.filters,
                "measures_used": spec_hint.measures_used,
            }
            print(
                f"[query_agent] ✓ spec hint injected: '{spec_hint.title}'"
                f"  sql={spec_hint.sql_template[:100].replace(chr(10), ' ')}",
                flush=True,
            )

        # Calculated column substitutions from PDF spec_reader global context.
        # These are Power BI calculated columns that do NOT exist as raw DB columns —
        # they are CASE WHEN expressions derived from raw FK / status columns.
        # Example: 'replacement' → CASE WHEN p.replacement IS NOT NULL THEN 'Replaced' ELSE 'Not Replacing' END
        # Without this, the model groups by the raw FK integer (IDs like 4145, 6208)
        # instead of the meaningful category labels shown in the chart.
        if calc_col_map:
            subst_parts = [
                f"'{col}' → {expr}"
                for col, expr in list(calc_col_map.items())[:8]
            ]
            user_content["calc_col_substitutions"] = calc_col_map
            user_content["instruction"] = (
                "⚑ CALCULATED COLUMN SUBSTITUTIONS (CRITICAL): the following columns are Power BI "
                "calculated columns. They contain raw IDs or FK integers in the database — they do NOT "
                "contain the category labels shown in the chart. When you need to GROUP BY or SELECT "
                "any of these columns, you MUST replace them with the CASE WHEN expression shown. "
                "Grouping by the raw column will produce numeric IDs (4145, 6208 etc.) instead of "
                "meaningful categories. Substitutions: "
                + "; ".join(subst_parts)
                + " | " + user_content["instruction"]
            )
            print(
                f"[query_agent] ✓ calc_col_map injected: {len(calc_col_map)} substitution(s): "
                f"{list(calc_col_map.keys())}",
                flush=True,
            )

        # Context synthesis result — pre-resolved SQL components.
        # Only inject on attempt 1 (first try) and only when confidence is high.
        # On retries the model must be free to explore — don't lock it to potentially
        # wrong synthesized components across all 5 attempts.
        if resolved_spec and resolved_spec.primary_table and resolved_spec.confidence >= 0.65 and attempt == 1:
            _rs_parts = [
                f"primary_table: {resolved_spec.primary_table}",
                f"chart_type: {resolved_spec.chart_type}",
            ]
            if resolved_spec.dimension_column:
                _rs_parts.append(f"dimension (x-axis/GROUP BY): {resolved_spec.dimension_column}")
            if resolved_spec.metric_expression:
                _rs_parts.append(f"metric (aggregation): {resolved_spec.metric_expression}")
            if resolved_spec.date_column:
                _rs_parts.append(f"date_column: {resolved_spec.date_column}")
            if resolved_spec.join_tables:
                _rs_parts.append(f"join_tables: {', '.join(resolved_spec.join_tables)}")
            if resolved_spec.join_conditions:
                _rs_parts.append(f"join_conditions: {'; '.join(resolved_spec.join_conditions)}")
            if resolved_spec.where_conditions:
                _rs_parts.append(f"where: {'; '.join(resolved_spec.where_conditions)}")
            if resolved_spec.group_by_columns:
                _rs_parts.append(f"group_by: {', '.join(resolved_spec.group_by_columns)}")
            if resolved_spec.order_by:
                _rs_parts.append(f"order_by: {resolved_spec.order_by}")
            if resolved_spec.limit:
                _rs_parts.append(f"limit: {resolved_spec.limit}")

            _rs_instruction = (
                "⚡ CONTEXT SYNTHESIS SUGGESTION (start with these, adapt if SQL fails): "
                + " | ".join(_rs_parts)
                + f" | confidence={resolved_spec.confidence:.2f} sources={resolved_spec.sources_used}"
                + " | " + user_content["instruction"]
            )
            user_content["instruction"] = _rs_instruction
            user_content["resolved_spec"] = {
                "primary_table": resolved_spec.primary_table,
                "join_tables": resolved_spec.join_tables,
                "join_conditions": resolved_spec.join_conditions,
                "dimension": resolved_spec.dimension_column,
                "metric_expression": resolved_spec.metric_expression,
                "where": resolved_spec.where_conditions,
                "group_by": resolved_spec.group_by_columns,
                "chart_type": resolved_spec.chart_type,
            }
            print(
                f"[query_agent] ✓ resolved_spec injected (attempt 1): table={resolved_spec.primary_table}"
                f" dim={resolved_spec.dimension_column}"
                f" metric={resolved_spec.metric_expression[:60]}"
                f" conf={resolved_spec.confidence:.2f}",
                flush=True,
            )

        # PBIT field bindings — ground truth from the Power BI model file.
        # This is the HIGHEST priority instruction: it overrides all schema inference,
        # spec_hint templates, and context_parser signals.  The field bindings come
        # directly from the PBIT visual's projections — they are the exact columns and
        # measures Power BI uses to render this chart.
        if pbit_column_hint:
            _pb_bindings = pbit_column_hint.get("field_bindings") or {}
            _pb_db_tables = pbit_column_hint.get("db_tables") or []
            _pb_measures = pbit_column_hint.get("measures") or {}
            _pb_joins = pbit_column_hint.get("join_conditions") or []
            _pb_title = pbit_column_hint.get("title", "")

            _binding_lines = []
            for _role, _refs in _pb_bindings.items():
                _binding_lines.append(f"{_role}: {', '.join(_refs)}")

            _measure_lines = [
                f"  [{mname}] = {mexpr}"
                for mname, mexpr in list(_pb_measures.items())[:8]
            ]

            _pbit_instruction = (
                "⚑ PBIT FILE — GROUND TRUTH FIELD BINDINGS (ABSOLUTE HIGHEST PRIORITY): "
                "These bindings come directly from the Power BI PBIT model file and represent "
                "the EXACT columns/measures this chart was built with. You MUST use them. "
                "Rules: "
                "(1) Use ONLY the tables listed in db_tables — do NOT add other tables. "
                "(2) Build the SELECT and GROUP BY from the field_bindings below — Category → GROUP BY dimension, "
                "Y/Values → aggregate metric, Legend/Series → secondary GROUP BY. "
                "(3) If a field binding references a measure (contains [brackets]), "
                "use its translated SQL expression from the measures dict. "
                "(4) If join_conditions are provided, use them verbatim in the JOIN ON clause. "
                "(5) Do NOT invent columns — every column in SELECT must come from field_bindings or measures. "
                f"db_tables: {_pb_db_tables} | "
                f"field_bindings: {'; '.join(_binding_lines)} | "
                + (f"measures: {chr(10).join(_measure_lines)} | " if _measure_lines else "")
                + (f"join_conditions: {'; '.join(_pb_joins)} | " if _pb_joins else "")
                + (f"visual_title: {_pb_title}" if _pb_title else "")
            )
            user_content["instruction"] = _pbit_instruction + " | " + user_content["instruction"]
            user_content["pbit_field_bindings"] = {
                "field_bindings": _pb_bindings,
                "db_tables": _pb_db_tables,
                "measures": _pb_measures,
                "join_conditions": _pb_joins,
            }
            print(
                f"[query_agent] ✓ PBIT field bindings injected: "
                f"tables={_pb_db_tables}  bindings={list(_pb_bindings.keys())}  "
                f"measures={list(_pb_measures.keys())}  joins={len(_pb_joins)}",
                flush=True,
            )

        if candidate:
            user_content["schema_analysis"] = {
                "recommended_tables": candidate.get("tables", []),
                "key_columns": candidate.get("key_columns", {}),
                "join_condition": candidate.get("join"),
                "reasoning": candidate.get("reasoning", ""),
            }
            if candidate.get("user_mandated"):
                # User explicitly specified these tables — do not deviate regardless of schema
                _mandated = candidate.get("tables", [])
                user_content["instruction"] += (
                    f" CRITICAL — USER-MANDATED TABLES: the user has explicitly specified that"
                    f" ONLY {_mandated} must be used. Do NOT reference any other table."
                    f" Do NOT join with tables outside this list."
                    f" If the data you need appears to require another table, find it within"
                    f" {_mandated} instead."
                )
            else:
                user_content["instruction"] += f" PRIORITY: use {candidate.get('tables')} as recommended by schema analysis."

        # Ground-truth DB values from value_sampler — highest-confidence hints available
        is_kpi = chart_spec.get("type", "") in ("kpi", "kpi_card", "gauge")
        needs_date = _chart_needs_date_filter(chart_spec)
        if sample_context:
            user_content["ground_truth_from_db"] = sample_context
            notes = []
            if not is_kpi:
                # dimension_note and metric_note always useful for non-KPI
                for key in ("dimension_note", "metric_note"):
                    if sample_context.get(key):
                        notes.append(sample_context[key])
                # date_note only when the chart actually has a time dimension
                if needs_date and sample_context.get("date_note"):
                    notes.append(sample_context["date_note"])
                if sample_context.get("confirmed_dimension_values"):
                    notes.append(
                        f"CONFIRMED: these dimension values actually exist in the DB: "
                        f"{sample_context['confirmed_dimension_values']}"
                    )
            else:
                # KPI only gets metric magnitude hints — no dimension or date filters
                if sample_context.get("metric_note"):
                    notes.append(sample_context["metric_note"])
            if notes:
                user_content["instruction"] += (
                    " GROUND TRUTH FROM DB (use these facts — they are confirmed from the live database): "
                    + " | ".join(notes)
                )

        if retry_feedback:
            user_content["retry_feedback"] = retry_feedback
            user_content["instruction"] += " RETRY: apply retry_feedback exactly to fix the previous query."

        # Date range hint from sampled DB — only for charts that have a time dimension.
        # Presented as a SOFT HINT (not a mandate) so the LLM can pick a narrower
        # sub-range when the chart's x_tick_labels point to a specific year/period.
        if date_filter_constraint and needs_date:
            user_content["date_range_hint"] = date_filter_constraint
            user_content["instruction"] += (
                f" DATE RANGE HINT: the date column spans {date_filter_constraint} in the live DB. "
                "Do NOT filter outside this range — it will return 0 rows. "
                "If the chart shows a specific period, filter to that period within this range."
            )

        # Dashboard-level date context — only inject for charts that actually need a date filter.
        # Applying this to KPI cards and bar-by-category charts was forcing incorrect WHERE clauses.
        if dashboard_date_context and dashboard_date_context.get("date_instruction") and needs_date:
            user_content["dashboard_date_context"] = {
                "inferred_period": dashboard_date_context.get("inferred_period", ""),
                "instruction": dashboard_date_context["date_instruction"],
            }
            user_content["instruction"] += (
                f" DASHBOARD DATE: {dashboard_date_context['date_instruction']}"
            )

        # ── x_tick_labels granularity hint ────────────────────────────────────
        x_tick_labels_raw = chart_spec.get("x_tick_labels") or []
        granularity = _detect_tick_granularity(x_tick_labels_raw)
        if granularity and _chart_needs_date_filter(chart_spec):
            _trunc_map = {"quarter": "quarter", "month": "month", "year": "year", "week": "week"}
            _trunc_fn = _trunc_map.get(granularity, "month")
            user_content["instruction"] += (
                f" DATE GRANULARITY: x-axis tick labels indicate {granularity}-level grouping — "
                f"use DATE_TRUNC('{_trunc_fn}', date_col) AS period for the time dimension. "
                f"Do NOT group at day or month level when the chart shows {granularity}s."
            )

        # ── Option 2: Magnitude anchor ─────────────────────────────────────────
        estimated_values = chart_spec.get("estimated_values") or {}
        # If vision OCR was uncertain, magnitude anchor is unreliable — skip it and
        # let the model infer from schema semantics instead.
        has_uncertain_estimates = any(
            isinstance(v, str) and str(v).startswith("~")
            for v in estimated_values.values()
        )
        if has_uncertain_estimates:
            user_content["instruction"] += (
                " NOTE: vision OCR values are uncertain (marked ~) — do NOT anchor to their "
                "magnitude. Focus on correct table + aggregation from schema semantics."
            )
        if estimated_values and not has_uncertain_estimates:
            import re as _re2
            def _parse_est_val(v: str):
                s = str(v).replace("~", "").replace(",", "").strip()
                m = _re2.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
                if not m:
                    return None
                n = float(m.group(1))
                suffix = m.group(2).lower()
                return n * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(suffix, 1.0)

            parsed_vals = [v for v in [_parse_est_val(str(ev)) for ev in estimated_values.values()] if v and v > 0]
            if parsed_vals:
                avg_est = sum(parsed_vals) / len(parsed_vals)
                magnitude_label = (
                    "millions (1,000,000+)" if avg_est >= 1e6
                    else "thousands (1,000-999,999)" if avg_est >= 1e3
                    else "hundreds (100-999)" if avg_est >= 100
                    else "tens or less (< 100)"
                )
                user_content["magnitude_anchor"] = {
                    "expected_avg_value": round(avg_est, 0),
                    "magnitude": magnitude_label,
                    "raw_estimates": dict(list(estimated_values.items())[:6]),
                    "rule": (
                        f"Your SQL must return numeric values averaging ~{avg_est:,.0f} ({magnitude_label}). "
                        "BEFORE writing SQL: estimate what COUNT(*), SUM(col), AVG(col) would return "
                        "and choose the one closest to this magnitude. "
                        "If COUNT returns 5 but the chart expects 5000, use SUM. "
                        "If SUM returns 5,000,000 but chart expects 500, use AVG or COUNT. "
                        "Wrong magnitude = guaranteed retry."
                    ),
                }
                user_content["instruction"] += (
                    f" MAGNITUDE REQUIREMENT: result values must average ~{avg_est:,.0f}. "
                    "Pick aggregation (SUM/COUNT/AVG/COUNT DISTINCT) that matches this scale."
                )

        # ── Option 3: x_tick_labels as WHERE / GROUP BY seeds ──────────────────
        x_tick_labels = chart_spec.get("x_tick_labels") or []
        already_confirmed = sample_context and sample_context.get("confirmed_dimension_values")
        if x_tick_labels and not already_confirmed:
            user_content["x_tick_label_hints"] = {
                "labels": x_tick_labels[:15],
                "instruction": (
                    f"The chart's X-axis shows these category labels: {x_tick_labels[:15]}. "
                    "GROUP BY a column whose DISTINCT values semantically match these labels. "
                    "Do NOT add WHERE col IN (...) — this over-filters results and causes zero rows. "
                    "Let GROUP BY + aggregation naturally produce the matching categories. "
                    "Do NOT group by a column whose values cannot produce these labels."
                ),
            }
            user_content["instruction"] += (
                f" X-AXIS HINT: chart shows categories like {x_tick_labels[:4]} — "
                "match these via GROUP BY, NOT a hard WHERE filter."
            )

        # SQL error recovery mode — activated after 2+ consecutive identical errors
        if error_recovery_mode == "no_alias":
            user_content["error_recovery"] = {
                "mode": "no_alias",
                "instruction": (
                    "CRITICAL FIX: the previous SQL failed because it referenced an undefined table alias. "
                    "You MUST rewrite the SQL with NO aliases at all. "
                    "Write the full table name everywhere — in SELECT, FROM, JOIN, WHERE, GROUP BY. "
                    "BAD:  SELECT p.name, p.count FROM positions p → 'p' may be undefined\n"
                    "GOOD: SELECT positions.name, positions.count FROM positions\n"
                    "BAD:  SELECT cc.job_role FROM candidates cc → 'cc' is not defined\n"
                    "GOOD: SELECT candidates.job_role FROM candidates\n"
                    "Every column reference must use either the full table name or no qualifier."
                ),
            }
            user_content["instruction"] += (
                " NO-ALIAS MANDATORY: write full table names everywhere, zero single-letter aliases."
            )
        elif error_recovery_mode == "no_union_order":
            user_content["error_recovery"] = {
                "mode": "no_union_order",
                "instruction": (
                    "CRITICAL FIX: previous attempts failed because ORDER BY was placed inside a UNION branch. "
                    "Always wrap UNION/UNION ALL in a subquery and put ORDER BY outside: "
                    "SELECT * FROM (SELECT ... UNION ALL SELECT ...) AS combined ORDER BY col. "
                    "Never use ORDER BY inside individual UNION branches."
                ),
            }
            user_content["instruction"] += (
                " ERROR RECOVERY (no-union-order mode): wrap UNION in a subquery, ORDER BY outside only."
            )

        # ── Layer 2: Previously tried table combinations ─────────────────────────
        # Injected by the orchestrator from tried_tables_history. The LLM must
        # explore a NEW set of tables / joins not present in this list.
        if previously_tried:
            _tried_entries = previously_tried[-8:]  # keep last 8 — prevents LLM from re-trying forgotten combos
            _tried_summary = "; ".join(
                "tables={} reason={}".format(
                    p.get("tables", []),
                    (p.get("failure_reason") or "failed")[:80],
                )
                for p in _tried_entries
            )
            user_content["previously_tried_combinations"] = _tried_entries
            user_content["instruction"] += (
                f" EXPLORATION REQUIRED: {len(_tried_entries)} table combination(s) have ALREADY "
                f"been tried and FAILED — {_tried_summary}. "
                "You MUST use a DIFFERENT set of tables and/or join keys. "
                "Look for alternative paths: different fact tables, different join keys, "
                "sub-queries, CTEs, or a completely different aggregation grain that "
                "avoids the failed combinations entirely. "
                "Repeating a failed combination will guarantee another retry."
            )

        # Adaptive temperature — jump aggressively when the issue is a wrong table/data,
        # stay low when fixing syntax errors (deterministic fix needed).
        if attempt == 1:
            _temp = 0.15
        elif failure_type in ("table_not_found", "permission_denied", "zero_rows", "wrong_table"):
            _temp = 0.38  # wrong data source → need radically different SQL structure
        elif failure_type in ("column_not_found", "syntax_error", "db_error", "alias_error"):
            _temp = 0.12  # structural fix → keep low temp for determinism
        elif failure_type in ("value_mismatch", "low_score_value"):
            _temp = 0.25  # aggregation change → moderate exploration
        else:
            _temp = min(0.20 + (attempt - 1) * 0.08, 0.40)  # fallback: gentle ramp
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
            first_table = (candidate["tables"][0] if candidate and candidate.get("tables")
                          else (schema.get("important_tables") or ["unknown"])[0])
            data = {
                "sql": f"SELECT * FROM {first_table} LIMIT 100",
                "chart_type": chart_spec.get("type", "table"),
                "table_used": first_table,
                "x_axis_label": chart_spec.get("x_axis_label", "x"),
                "y_axis_label": chart_spec.get("y_axis_label", "y"),
                "title": chart_spec.get("title") or "Chart",
                "reasoning": "Fallback due to parse error",
            }

        # Post-process: expand short aliases → full table names
        generated_sql = data.get("sql", "SELECT 1")
        generated_sql = expand_table_aliases(generated_sql)
        data["sql"] = generated_sql

        # Truncation detection: unbalanced parentheses or response near token limit are
        # reliable signs that the LLM stopped mid-SQL due to max_tokens.
        _open = generated_sql.count("(")
        _close = generated_sql.count(")")
        if _open != _close:
            print(
                f"[query_agent] ⚠ SQL truncation likely: "
                f"unbalanced parens ({_open} open, {_close} close) — "
                f"SQL may be incomplete. Flagging for retry.",
                flush=True,
            )
            data["_truncation_warning"] = (
                f"Unbalanced parentheses ({_open} open vs {_close} close) — SQL may be truncated. "
                "Rewrite using simpler structure without deeply nested subqueries."
            )

        # Basic lint before returning (non-fatal — logs warning, passes through)
        db_dialect = data.get("db_dialect", "redshift" if enriched and enriched.db_type == "redshift" else "postgresql")
        lint_error = basic_sql_lint(generated_sql, db_dialect)
        if lint_error:
            print(f"[query_agent] ⚠ SQL lint: {lint_error}", flush=True)

        # Column existence pre-check against schema (non-fatal — appends _column_error)
        if enriched and candidate and candidate.get("tables"):
            col_error = verify_columns_against_schema(
                generated_sql, enriched.compact_tables, candidate.get("tables")
            )
            if col_error:
                print(f"[query_agent] ⚠ column pre-check: {col_error}", flush=True)
                data["_column_error"] = col_error

        return {
            "sql": data.get("sql", "SELECT 1"),
            "chart_type": data.get("chart_type", "table"),
            "table_used": data.get("table_used", "unknown"),
            "x_axis_label": data.get("x_axis_label", "x"),
            "y_axis_label": data.get("y_axis_label", "y"),
            "title": data.get("title") or chart_spec.get("title") or "Chart",
            "reasoning": data.get("reasoning", ""),
            "_column_error": data.get("_column_error"),
            "_truncation_warning": data.get("_truncation_warning"),
        }
