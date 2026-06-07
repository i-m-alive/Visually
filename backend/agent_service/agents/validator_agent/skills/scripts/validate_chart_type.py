"""
Skill: validate_chart_type
Check that SQL result columns match the required chart type shape.
Usage:
    python validate_chart_type.py '{"chart_type":"bar_vertical","columns":["month","revenue"],"row_count":12}'
"""
import json
import sys

# min_cols: minimum number of SELECT columns required
# needs_numeric: at least one numeric column required
# multi_series: chart benefits from col[0]=category + col[1..n]=series values (wide format)
CHART_REQUIREMENTS = {
    # ── Basic ────────────────────────────────────────────────────────────────
    "bar":                      {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "bar_vertical":             {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "bar_horizontal":           {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "line":                     {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "pie":                      {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "donut":                    {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "scatter":                  {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "kpi":                      {"min_cols": 1, "needs_numeric": True,  "multi_series": False},
    "kpi_card":                 {"min_cols": 1, "needs_numeric": True,  "multi_series": False},
    "table":                    {"min_cols": 1, "needs_numeric": False, "multi_series": False},
    "data_table":               {"min_cols": 1, "needs_numeric": False, "multi_series": False},
    # ── Multi-series bar ─────────────────────────────────────────────────────
    "stacked_bar":              {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    "stacked_bar_100":          {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    "stacked_bar_horizontal":   {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    "grouped_bar":              {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Area ─────────────────────────────────────────────────────────────────
    "area":                     {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "stacked_area":             {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Combo ────────────────────────────────────────────────────────────────
    "combo":                    {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Extended scatter ─────────────────────────────────────────────────────
    "bubble":                   {"min_cols": 3, "needs_numeric": True,  "multi_series": False},
    # ── Distribution ─────────────────────────────────────────────────────────
    "histogram":                {"min_cols": 1, "needs_numeric": True,  "multi_series": False},
    "waterfall":                {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "funnel":                   {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── Hierarchical ─────────────────────────────────────────────────────────
    "treemap":                  {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "heatmap":                  {"min_cols": 3, "needs_numeric": True,  "multi_series": False},
    "sunburst":                 {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── KPI variants ─────────────────────────────────────────────────────────
    "gauge":                    {"min_cols": 1, "needs_numeric": True,  "multi_series": False},
    "multi_row_card":           {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── Table variants ───────────────────────────────────────────────────────
    "pivot_table":              {"min_cols": 3, "needs_numeric": True,  "multi_series": False},
    # ── Statistical ──────────────────────────────────────────────────────────
    "box_plot":                 {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── Comparison ───────────────────────────────────────────────────────────
    "bullet":                   {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "scorecard":                {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "dot_plot":                 {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    "radar":                    {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Trend / rank ─────────────────────────────────────────────────────────
    "ribbon":                   {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Flow / relational ────────────────────────────────────────────────────
    "sankey":                   {"min_cols": 3, "needs_numeric": True,  "multi_series": False},
    "chord":                    {"min_cols": 3, "needs_numeric": True,  "multi_series": False},
    "network":                  {"min_cols": 2, "needs_numeric": False, "multi_series": False},
    # ── Time-based ───────────────────────────────────────────────────────────
    "gantt":                    {"min_cols": 3, "needs_numeric": False, "multi_series": False},
    "timeline":                 {"min_cols": 2, "needs_numeric": False, "multi_series": False},
    "calendar_heatmap":         {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── Text ─────────────────────────────────────────────────────────────────
    "word_cloud":               {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
    # ── Hierarchical ─────────────────────────────────────────────────────────
    "org_chart":                {"min_cols": 2, "needs_numeric": False, "multi_series": False},
    # ── Part-to-whole ─────────────────────────────────────────────────────────
    "marimekko":                {"min_cols": 3, "needs_numeric": True,  "multi_series": True},
    # ── Geographic ───────────────────────────────────────────────────────────
    "choropleth":               {"min_cols": 2, "needs_numeric": True,  "multi_series": False},
}


def validate_chart_type(chart_type: str, columns: list, row_count: int) -> dict:
    ct = chart_type.lower().strip()
    req = CHART_REQUIREMENTS.get(ct, {"min_cols": 1, "needs_numeric": False, "multi_series": False})
    issues = []

    if len(columns) < req["min_cols"]:
        issues.append(
            f"'{ct}' requires at least {req['min_cols']} columns, got {len(columns)}. "
            f"{'Add more series columns (wide format: category + one column per series).' if req['multi_series'] else ''}"
        )

    if row_count == 0:
        issues.append("Query returned 0 rows — check WHERE clause, date filters, or table name.")

    if req.get("multi_series") and len(columns) == 2:
        issues.append(
            f"'{ct}' is a multi-series chart — SQL should return category + 2+ value columns "
            f"(e.g. SELECT month, product_a_revenue, product_b_revenue FROM ...). Got only 2 columns."
        )

    return {
        "passed": len(issues) == 0,
        "issues": issues,
        "chart_type": ct,
        "column_count": len(columns),
        "row_count": row_count,
        "multi_series": req.get("multi_series", False),
    }


if __name__ == "__main__":
    data = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    result = validate_chart_type(
        data.get("chart_type", "bar_vertical"),
        data.get("columns", []),
        data.get("row_count", 0),
    )
    print(json.dumps(result, indent=2))
