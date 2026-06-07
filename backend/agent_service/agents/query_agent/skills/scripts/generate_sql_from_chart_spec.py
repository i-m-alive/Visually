"""
Skill: generate_sql_from_chart_spec
Generate SQL from a vision-detected chart spec (screenshot replication mode).
Usage:
    python generate_sql_from_chart_spec.py
    (reads chart_spec + schema JSON from stdin, prints SQL plan to stdout)
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

SYSTEM_PROMPT = """You are replicating a dashboard chart from a screenshot.
Given a chart specification extracted by the Vision Agent and the live database schema,
generate a SQL query that produces data matching the original chart as closely as possible.

HARD RULES:
- SELECT/WITH only — never INSERT, UPDATE, DELETE, DROP
- Map visual axis labels and category names to real column names in the schema
- Use sample_values to confirm you're joining the right tables and using the right column values
- If retry_feedback is provided, fix the specific issues mentioned before generating new SQL
- If hint_response is provided, the user has clarified the data source — prioritize it

SQL FORMAT GUIDE BY CHART TYPE:

─── BASIC (2-column: category, value) ───────────────────────────────────────────
bar_vertical / bar_horizontal / line / area / pie / donut / scatter:
  SELECT x_col AS "<x_axis_label>", AGG(y_col) AS "<y_axis_label>"
  FROM table [JOIN ...] [WHERE ...]
  GROUP BY x_col ORDER BY x_col [or ORDER BY 2 DESC] LIMIT 50

kpi / gauge:
  SELECT AGG(metric_col) AS "<title>"
  FROM table [WHERE ...]
  (single row, single value; for gauge add second col for target if visible)

─── MULTI-SERIES (3+ columns: category + one col per series) ────────────────────
stacked_bar / stacked_bar_100 / stacked_bar_horizontal / grouped_bar / stacked_area:
  CRITICAL: col[0] = category, col[1..n] = one column per visible series/segment.
  Use CONDITIONAL AGGREGATION (CASE WHEN) to pivot long → wide format:

  SELECT month_col AS "Month",
         SUM(CASE WHEN series_col = 'Series A' THEN value_col ELSE 0 END) AS "Series A",
         SUM(CASE WHEN series_col = 'Series B' THEN value_col ELSE 0 END) AS "Series B",
         SUM(CASE WHEN series_col = 'Series C' THEN value_col ELSE 0 END) AS "Series C"
  FROM table [WHERE ...] GROUP BY month_col ORDER BY month_col LIMIT 24

  Alternatively, if the table already has separate metric columns:
  SELECT category_col, metric_a_col AS "Metric A", metric_b_col AS "Metric B"
  FROM table [WHERE ...] GROUP BY category_col ORDER BY category_col LIMIT 24

─── COMBO (3 columns: category, bar_metric, line_metric) ────────────────────────
combo:
  col[0]=category, col[1]=bar value (usually a sum/count), col[2]=line value (usually a ratio/avg)
  SELECT month, SUM(revenue) AS "Revenue", ROUND(AVG(margin_pct)*100, 1) AS "Margin %"
  FROM sales GROUP BY month ORDER BY month LIMIT 24

─── BUBBLE (3-4 columns: x_numeric, y_numeric, size_numeric, [label]) ───────────
bubble:
  col[0]=label (optional), col[1]=x_value, col[2]=y_value, col[3]=size_value
  OR: col[0]=x_value, col[1]=y_value, col[2]=size_value (if no label)
  SELECT product_name, SUM(units_sold) AS units, AVG(price) AS avg_price, SUM(profit) AS profit
  FROM products GROUP BY product_name ORDER BY profit DESC LIMIT 50

─── DISTRIBUTION / FLOW ─────────────────────────────────────────────────────────
histogram:
  Return the raw numeric column values — frontend bins them:
  SELECT numeric_col FROM table WHERE numeric_col IS NOT NULL LIMIT 2000

waterfall:
  col[0]=stage label, col[1]=signed numeric value (positive=gain, negative=loss)
  Ensure ORDER BY reflects the correct left-to-right visual sequence:
  SELECT category, signed_value FROM financials ORDER BY display_order LIMIT 20

funnel:
  col[0]=stage name, col[1]=count or value (MUST be descending — add ORDER BY 2 DESC):
  SELECT stage_name, COUNT(*) AS count FROM pipeline GROUP BY stage_name ORDER BY 2 DESC LIMIT 10

─── HIERARCHICAL ────────────────────────────────────────────────────────────────
treemap:
  col[0]=label, col[1]=size_value, [col[2]=parent_group optional]:
  SELECT category_name, SUM(revenue) AS revenue FROM sales GROUP BY 1 ORDER BY 2 DESC LIMIT 40

sunburst (3 cols: parent, child, value):
  SELECT parent_category, child_category, SUM(value) AS value
  FROM table GROUP BY 1, 2 ORDER BY 1, 3 DESC LIMIT 60

heatmap (3 cols: row_label, col_label, numeric_value):
  SELECT row_dimension, column_dimension, AGG(value) AS metric
  FROM table GROUP BY 1, 2 ORDER BY 1, 2 LIMIT 500
  Example: SELECT day_of_week, hour_of_day, COUNT(*) FROM events GROUP BY 1, 2 ORDER BY 1, 2

─── MULTI-ROW KPI ────────────────────────────────────────────────────────────────
multi_row_card (2 cols: metric_name, metric_value — multiple rows):
  SELECT 'Total Revenue' AS metric, SUM(revenue)::text AS value FROM sales
  UNION ALL SELECT 'Active Users', COUNT(DISTINCT user_id)::text FROM users
  UNION ALL SELECT 'Avg Order Value', ROUND(AVG(order_total),2)::text FROM orders

─── TABLE VARIANTS ───────────────────────────────────────────────────────────────
table / data_table: SELECT * FROM table [WHERE ...] ORDER BY ... LIMIT 100
pivot_table (3 cols: row_dim, col_dim, value): SELECT r, c, AGG(v) FROM t GROUP BY 1,2

DATE / TIME FILTER RULES:
- If ground_truth_from_db contains actual_date_range → use it as a WHERE constraint
- If dashboard_date_context contains date_instruction → follow it
- If date_filter_constraint is provided → it is a HARD REQUIREMENT: include it verbatim in WHERE
- Always prefer date range that matches the chart's visual time axis

SQL ANTI-PATTERNS (these always cause errors — never generate them):
1. ALIAS BUG: Never use a table alias that isn't defined in FROM/JOIN.
   BAD:  SELECT p.name FROM staging.table_x  (p was never aliased)
   GOOD: SELECT t.name FROM staging.table_x AS t
   Or avoid aliases entirely: SELECT staging.table_x.name FROM staging.table_x

2. UNION ORDER BY BUG: Never put ORDER BY inside a UNION branch.
   BAD:  (SELECT a FROM t1 ORDER BY a) UNION (SELECT b FROM t2)
   GOOD: SELECT * FROM (SELECT a FROM t1 UNION SELECT b FROM t2) sub ORDER BY 1

3. MULTI-ROW KPI with ORDER BY on UNION: For multi_row_card using UNION ALL,
   wrap the entire UNION ALL in a subquery before adding ORDER BY:
   SELECT * FROM (SELECT 'A' AS metric, COUNT(*) FROM t1 UNION ALL SELECT 'B', SUM(x) FROM t2) sub

4. COLUMN AMBIGUITY: If joining two tables that both have a column with the same name,
   always qualify with table alias: t1.col_name NOT just col_name

Return ONLY valid JSON (no markdown):
{
  "sql": "SELECT ...",
  "chart_type": "<type matching the spec>",
  "x_axis_label": "<label>",
  "y_axis_label": "<label>",
  "title": "<chart title>"
}"""


async def generate_from_chart_spec(
    chart_spec: dict,
    schema: dict,
    retry_feedback: str = "",
    hint_response: str = "",
    sample_values: dict | None = None,
) -> dict:
    parts = [
        f"Chart spec:\n{json.dumps(chart_spec, indent=2)}",
        f"\nDatabase schema:\n{json.dumps(schema, indent=2)}",
    ]
    if sample_values:
        parts.append(f"\nSample column values (use to match real data):\n{json.dumps(sample_values, indent=2)}")
    if retry_feedback:
        parts.append(f"\nRetry feedback (fix these issues):\n{retry_feedback}")
    if hint_response:
        parts.append(f"\nUser clarification (highest priority):\n{hint_response}")

    raw = await bedrock_invoke(
        model_id=BEDROCK_SONNET_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message="\n".join(parts),
        max_tokens=1536,
        temperature=0.0,
    )
    raw = raw.strip()
    # Strip markdown fences if model adds them
    if raw.startswith("```"):
        import re
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
    return json.loads(raw)


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = asyncio.run(generate_from_chart_spec(
        data.get("chart_spec", {}),
        data.get("schema", {}),
        data.get("retry_feedback", ""),
        data.get("hint_response", ""),
        data.get("sample_values"),
    ))
    print(json.dumps(result, indent=2))
