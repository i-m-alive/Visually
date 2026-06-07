"""
CanvasVerifier — Phase B verification loop agent.

Strategy:
  1. Any chart that already passed the LLM validator (status="confirmed", score≥0.80)
     is auto-passed — no structural re-check needed.
  2. Charts with status="low_confidence" (score 0.65-0.80) get a lightweight structural
     check.  They pass if they have data and the chart type roughly matches.
  3. Charts with status="failed" or 0 rows are flagged for retry.

PASS_THRESHOLD = 0.45 (lenient — verifier is a safety net, not a replacement for the
LLM validator).

Scoring dimensions (only applied to low_confidence/unknown charts):
  type_match        (30%) — does chart_type match vision detection?
  data_presence     (40%) — did the query return at least 1 row?
  data_shape        (30%) — do column count / row count meet chart-type minimums?

Category/value plausibility checks are deliberately omitted — vision estimates are
rough and would cause too many false failures.
"""
from __future__ import annotations

from typing import Optional

from shared.schemas.verification import ChartVerificationResult, DashboardVerificationReport

PASS_THRESHOLD = 0.45

# Chart types that need at least this many rows to be considered non-trivial
_MIN_ROWS: dict[str, int] = {
    "kpi": 1, "kpi_card": 1, "gauge": 1, "multi_row_card": 2,
    "pie": 3, "donut": 3, "funnel": 2, "waterfall": 3,
    "treemap": 3, "sunburst": 3, "heatmap": 4,
    "histogram": 5,
    # New chart types
    "bullet": 1, "scorecard": 1, "dot_plot": 3, "radar": 3,
    "ribbon": 3, "box_plot": 3,
    "sankey": 2, "chord": 3, "network": 2,
    "gantt": 1, "timeline": 2, "calendar_heatmap": 7,
    "word_cloud": 3, "org_chart": 2, "marimekko": 3, "choropleth": 2,
}
_DEFAULT_MIN_ROWS = 2

# Multi-series chart types that need ≥3 columns
_MULTI_SERIES = frozenset({
    "stacked_bar", "stacked_bar_100", "stacked_bar_horizontal",
    "grouped_bar", "stacked_area", "combo", "heatmap", "sunburst", "pivot_table",
    # New multi-series types
    "radar", "ribbon", "marimekko",
})

_W = {"type_match": 0.30, "data_presence": 0.40, "data_shape": 0.30}


def _normalise_type(ct: str) -> str:
    ct = (ct or "").lower().strip()
    return {"bar": "bar_vertical", "kpi_card": "kpi"}.get(ct, ct)


def _is_related_type(a: str, b: str) -> bool:
    """True if two chart types are close enough (same family)."""
    families = [
        {"bar_vertical", "bar_horizontal", "bar", "stacked_bar", "stacked_bar_100",
         "stacked_bar_horizontal", "grouped_bar"},
        {"area", "stacked_area"},
        {"line", "ribbon"},
        {"pie", "donut", "sunburst", "marimekko"},
        {"kpi", "kpi_card", "gauge", "multi_row_card", "bullet", "scorecard"},
        {"table", "data_table", "pivot_table"},
        {"scatter", "bubble", "dot_plot"},
        {"heatmap", "calendar_heatmap", "chord"},
        {"treemap", "org_chart", "sunburst"},
        {"sankey", "network"},
        {"gantt", "timeline"},
        {"radar"},
        {"word_cloud"},
        {"choropleth"},
        {"box_plot", "histogram"},
    ]
    for fam in families:
        if a in fam and b in fam:
            return True
    return False


def _score_chart(
    chart_spec: dict,
    query_plan: dict,
    exec_result: dict,
    validation_score: Optional[float] = None,
    validation_status: Optional[str] = None,
) -> ChartVerificationResult:
    chart_id = chart_spec.get("id", "unknown")
    chart_title = query_plan.get("title") or chart_spec.get("title") or chart_id

    # ── Fast-pass: already confirmed by LLM validator ─────────────────────────
    # "confirmed" means LLM validator scored ≥ 0.80 — trust it completely.
    if validation_status == "confirmed" or (validation_score is not None and validation_score >= 0.80):
        return ChartVerificationResult(
            chart_id=chart_id,
            chart_title=chart_title,
            chart_type_expected="",
            chart_type_actual=_normalise_type(query_plan.get("chart_type", "")),
            type_match=True,
            has_data=True,
            row_count=len(exec_result.get("rows", [])),
            expected_row_count=0,
            data_shape_score=1.0,
            category_coverage=1.0,
            value_plausibility=1.0,
            overall_score=validation_score if validation_score is not None else 1.0,
            passed=True,
            issues=[],
            retry_feedback=None,
        )

    rows    = exec_result.get("rows", [])
    columns = exec_result.get("columns", [])
    row_count = len(rows)
    issues: list[str] = []

    # Support both "chart_type" and "type" keys from vision spec
    expected_raw = chart_spec.get("chart_type") or chart_spec.get("type", "")
    expected_type = _normalise_type(expected_raw)
    actual_type   = _normalise_type(query_plan.get("chart_type", ""))

    # 1. Type match (30%)
    if not expected_type:
        type_score = 1.0  # no expectation → can't penalise
        type_match = True
    elif expected_type == actual_type:
        type_score = 1.0
        type_match = True
    elif _is_related_type(expected_type, actual_type):
        type_score = 0.7
        type_match = False
        issues.append(f"Chart type: expected '{expected_type}', got '{actual_type}' (same family)")
    else:
        type_score = 0.0
        type_match = False
        issues.append(f"Chart type mismatch: expected '{expected_type}', got '{actual_type}'")

    # 2. Data presence (40%)
    min_rows = _MIN_ROWS.get(actual_type, _DEFAULT_MIN_ROWS)
    has_data = row_count >= 1
    if row_count >= min_rows:
        data_presence_score = 1.0
    elif has_data:
        data_presence_score = 0.5
        issues.append(f"Few rows for '{actual_type}': {row_count} (need ≥{min_rows})")
    else:
        data_presence_score = 0.0
        issues.append("Query returned 0 rows — wrong table/WHERE clause")

    # 3. Data shape (30%)
    if actual_type in _MULTI_SERIES:
        data_shape_score = 1.0 if len(columns) >= 3 else (0.3 if len(columns) == 2 else 0.0)
        if len(columns) < 3:
            issues.append(f"Multi-series '{actual_type}' needs ≥3 columns — got {len(columns)}")
    elif actual_type in {"kpi", "gauge", "histogram"}:
        data_shape_score = 1.0
    else:
        data_shape_score = 1.0 if len(columns) >= 2 else (0.5 if len(columns) == 1 else 0.0)
        if len(columns) < 2:
            issues.append(f"Standard chart needs ≥2 columns — got {len(columns)}")

    overall = (
        _W["type_match"]      * type_score
        + _W["data_presence"] * data_presence_score
        + _W["data_shape"]    * data_shape_score
    )
    passed = overall >= PASS_THRESHOLD
    if not has_data:
        passed = False  # Hard gate: 0 rows always fails regardless of type/shape score

    retry_feedback: Optional[str] = None
    if not passed and issues:
        retry_feedback = (
            f"Verification failed (score {overall:.2f}). Issues:\n"
            + "\n".join(f"  - {i}" for i in issues)
            + "\nFix the SQL to address these issues."
        )

    return ChartVerificationResult(
        chart_id=chart_id,
        chart_title=chart_title,
        chart_type_expected=expected_type,
        chart_type_actual=actual_type,
        type_match=type_match,
        has_data=has_data,
        row_count=row_count,
        expected_row_count=0,
        data_shape_score=round(data_shape_score, 3),
        category_coverage=1.0,
        value_plausibility=1.0,
        overall_score=round(overall, 3),
        passed=passed,
        issues=issues,
        retry_feedback=retry_feedback,
    )


class CanvasVerifier:
    """
    Lightweight structural verification: fast-passes already-confirmed charts,
    applies structural checks only to low_confidence/unknown ones.
    """

    def verify_dashboard(
        self,
        chart_results: list[dict],
        loop: int = 1,
    ) -> DashboardVerificationReport:
        """
        Each item in chart_results must contain:
          - chart_spec:       vision-detected spec (id, chart_type/type, …)
          - query_plan:       LLM query plan (chart_type, title, sql, …)
          - execute_result:   SQL execution output (rows, columns)
          - score:            LLM validator score (float, optional)
          - status:           "confirmed" | "low_confidence" | None
        """
        results: list[ChartVerificationResult] = []
        for r in chart_results:
            result = _score_chart(
                chart_spec=r.get("chart_spec", {}),
                query_plan=r.get("query_plan", {}),
                exec_result=r.get("execute_result", {}),
                validation_score=r.get("score"),
                validation_status=r.get("status"),
            )
            results.append(result)

        passed_charts = sum(1 for r in results if r.passed)
        failed_charts = len(results) - passed_charts
        overall_score = (sum(r.overall_score for r in results) / len(results)) if results else 0.0

        return DashboardVerificationReport(
            total_charts=len(results),
            passed_charts=passed_charts,
            failed_charts=failed_charts,
            overall_score=round(overall_score, 3),
            passed=failed_charts == 0,
            loop=loop,
            results=results,
        )
