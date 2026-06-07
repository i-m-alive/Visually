import json
import re
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL
from shared.schemas.agent import IntentResult
from shared.schemas.schema import SemanticSchemaDocument
from shared.schemas.chart import QueryPlan
from agent_service.agents.sql_utils import expand_table_aliases, basic_sql_lint, verify_columns_against_schema

QUERY_AGENT_MODEL = BEDROCK_SONNET_MODEL

# SQL is rarely > 500 tokens — 1024 cuts latency without sacrificing quality
_SQL_MAX_TOKENS = 1024

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


def _chart_needs_date_filter(chart_spec: dict) -> bool:
    """
    Return True only when the chart is genuinely time-based and benefits from
    a date WHERE clause.

    Rules:
    - KPI / gauge: NEVER — total aggregates should scan all time
    - line / area / calendar_heatmap / timeline / gantt: ALWAYS — intrinsically temporal
    - Everything else (bar, pie, table, treemap …): only when title / axis labels /
      x_tick_labels contain explicit time references (year pattern, month abbrev,
      quarter label, or time keyword like "monthly" / "ytd")
    """
    chart_type = (chart_spec.get("type") or "").lower().replace("-", "_")

    if chart_type in ("kpi", "kpi_card", "gauge"):
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

REDSHIFT-SPECIFIC RULES (when db_dialect is "redshift"):
- Use GROUP BY 1, 2 (ordinal positions), NOT column alias names
- Use GETDATE() instead of NOW()
- Use ILIKE for case-insensitive matching
- VARCHAR max 65535, no TEXT type
- Use DATE_TRUNC (same as PostgreSQL)

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
    ) -> QueryPlan:
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

        if retry_feedback:
            user_content["retry_feedback"] = retry_feedback
            user_content["attempt"] = attempt
            user_content["instruction"] = "This is a retry. Apply the retry_feedback to fix the previous query."

        raw = await bedrock_invoke(
            model_id=QUERY_AGENT_MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_message=json.dumps(user_content, default=str),
            max_tokens=_SQL_MAX_TOKENS,
            temperature=0.1,
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
    ) -> dict:
        """
        Generate SQL from a Vision Agent ChartSpec (screenshot replication mode).
        enriched: EnrichedSchema with disambiguation + table semantics already built.
        candidate: dict from SchemaMatcher with tables, key_columns, join, reasoning.
        Returns a plain dict with sql, chart_type, x_axis_label, y_axis_label, title.
        """
        schema = enriched.schema_doc
        db_type = enriched.db_type

        # Use candidate tables first, then fill with other tables
        all_tables = {t.get("name"): t for t in enriched.compact_tables}
        if candidate and candidate.get("tables"):
            focus_names = candidate["tables"]
            tables_context = [all_tables[n] for n in focus_names if n in all_tables]
            # Add a few more for join context
            rest = [t for n, t in all_tables.items() if n not in focus_names][:6]
            tables_context = tables_context + rest
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

        if candidate:
            user_content["schema_analysis"] = {
                "recommended_tables": candidate.get("tables", []),
                "key_columns": candidate.get("key_columns", {}),
                "join_condition": candidate.get("join"),
                "reasoning": candidate.get("reasoning", ""),
            }
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

        # ── Option 2: Magnitude anchor ─────────────────────────────────────────
        estimated_values = chart_spec.get("estimated_values") or {}
        if estimated_values:
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
            _tried_entries = previously_tried[-4:]  # last 4 only — context cost
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

        raw = await bedrock_invoke(
            model_id=QUERY_AGENT_MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_message=json.dumps(user_content, default=str),
            max_tokens=_SQL_MAX_TOKENS,
            temperature=0.15,
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
        }
