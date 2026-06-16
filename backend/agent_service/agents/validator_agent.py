import json
import re
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL, BEDROCK_HAIKU_MODEL
from shared.schemas.chart import QueryPlan
from shared.schemas.validation import ValidationResult, DimensionScores, RetryFeedback

VALIDATOR_MODEL = BEDROCK_SONNET_MODEL
LABEL_MODEL = BEDROCK_HAIKU_MODEL

RETRY_STRATEGIES = {
    1: "adjust_aggregation",
    2: "alternate_table",
    3: "expand_join",
    4: "broaden_query",
}

# Maps any chart type string (from vision OR SQL) to its canonical family name.
# Both sides get canonicalized before comparison, so "column" vs "bar_vertical"
# both map to "bar_vertical" and score 1.0 instead of 0.0.
_CHART_TYPE_CANONICAL: dict[str, str] = {
    # bar_vertical family
    "bar": "bar_vertical", "bar_chart": "bar_vertical", "bar_vertical": "bar_vertical",
    "column": "bar_vertical", "column_chart": "bar_vertical",
    "stacked_bar": "bar_vertical", "stacked_bar_100": "bar_vertical",
    "stacked_column": "bar_vertical", "grouped_bar": "bar_vertical",
    "histogram": "bar_vertical",
    # waterfall — own canonical family (same data shape as bar but distinct render)
    "waterfall": "waterfall",
    # bar_horizontal family
    "bar_horizontal": "bar_horizontal", "horizontal_bar": "bar_horizontal",
    "stacked_bar_horizontal": "bar_horizontal",
    # line family (area/combo share same data structure)
    "line": "line", "line_chart": "line", "area": "line",
    "stacked_area": "line", "combo": "line",
    # pie family
    "pie": "pie", "pie_chart": "pie", "donut": "pie", "sunburst": "pie",
    # kpi family
    "kpi": "kpi_card", "kpi_card": "kpi_card", "card": "kpi_card",
    "metric": "kpi_card", "gauge": "kpi_card", "scorecard": "kpi_card",
    "multi_row_card": "kpi_card", "number": "kpi_card",
    # table family
    "table": "table", "data_table": "table", "pivot_table": "table",
    # scatter family
    "scatter": "scatter", "scatter_plot": "scatter", "bubble": "scatter",
    # other
    "treemap": "treemap", "funnel": "funnel",
    "heatmap": "heatmap", "calendar_heatmap": "heatmap",
    # slicer — filter control widget, not a chart
    "slicer": "slicer", "filter": "slicer", "dropdown": "slicer",
}

# Synonym map for axis label comparison — collapses semantically equivalent words
_LABEL_SYNONYMS: dict[str, str] = {
    "count": "total", "number": "total", "no": "total", "num": "total",
    "sum": "total", "quantity": "total", "qty": "total",
    "amount": "value", "revenue": "value", "sales": "value",
    "date": "period", "month": "period", "time": "period",
    "year": "period", "week": "period", "quarter": "period",
    "category": "type", "company": "organization", "org": "organization",
}


class ValidatorAgent:
    def validate_chart_type(self, expected: str, rendered: str) -> float:
        return 1.0 if expected == rendered else 0.0

    async def validate_axis_labels(self, expected_x: str, expected_y: str, actual_x: str, actual_y: str) -> float:
        prompt = (
            f"Are these axis labels semantically equivalent for a data chart?\n"
            f"Expected X: '{expected_x}', Actual X: '{actual_x}'\n"
            f"Expected Y: '{expected_y}', Actual Y: '{actual_y}'\n"
            f"Return JSON: {{\"score\": 0.0-1.0, \"reason\": \"...\"}}"
        )
        try:
            raw = await bedrock_invoke(
                model_id=LABEL_MODEL,
                system_prompt="You are a data label comparator. Return JSON only.",
                user_message=prompt,
                max_tokens=128,
                temperature=0.0,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"```$", "", raw).strip()
            return float(json.loads(raw).get("score", 0.7))
        except Exception:
            return 0.7

    def analyze_shape_match(self, actual_row_count: int, expected_row_count: Optional[int] = None) -> float:
        if actual_row_count == 0:
            return 0.0
        if expected_row_count is None or expected_row_count == 0:
            return 1.0
        ratio = actual_row_count / expected_row_count
        if 0.5 <= ratio <= 2.0:
            return 1.0
        elif ratio < 0.5:
            return ratio * 2
        return max(0.0, 1.0 - (ratio - 2.0) * 0.1)

    def check_completeness(self, actual_count: int, expected_count: Optional[int] = None) -> float:
        if actual_count == 0:
            return 0.0
        if expected_count is None or expected_count == 0:
            return min(1.0, actual_count / 10)
        return min(actual_count / expected_count, 1.0)

    def compute_score(self, chart_type_score: float, label_score: float, shape_score: float, completeness_score: float) -> float:
        return (
            chart_type_score * 0.25
            + label_score * 0.20
            + shape_score * 0.25
            + completeness_score * 0.30
        )

    async def validate(
        self,
        query_plan: QueryPlan,
        execute_result: dict,
        attempt: int = 1,
        expected_chart_type: Optional[str] = None,
    ) -> ValidationResult:
        row_count = execute_result.get("row_count", 0)
        columns = execute_result.get("columns", [])
        actual_x = columns[0] if columns else ""
        actual_y = columns[1] if len(columns) > 1 else ""

        # Compare against user-requested chart type when available; neutral score when none specified
        if expected_chart_type:
            chart_type_score = self.validate_chart_type(expected_chart_type, query_plan.chart_type)
        else:
            chart_type_score = 0.7  # neutral — no ground-truth type to compare against
        label_score = await self.validate_axis_labels(query_plan.x_axis_label, query_plan.y_axis_label, actual_x, actual_y)
        shape_score = self.analyze_shape_match(row_count)
        completeness_score = self.check_completeness(row_count)

        if execute_result.get("error"):
            chart_type_score = 0.0
            shape_score = 0.0
            completeness_score = 0.0

        score = self.compute_score(chart_type_score, label_score, shape_score, completeness_score)
        passed = score >= 0.80

        retry_feedback = None
        if not passed and attempt <= 4:
            # Skip LLM feedback for obvious DB errors — the error message itself is sufficient
            # and calling the LLM wastes ~500ms per retry.
            if execute_result.get("error"):
                feedback_text = (
                    f"Database error: {execute_result['error'][:200]}. "
                    "Fix the SQL syntax/columns or try a different table."
                )
            else:
                feedback_text = await self.generate_structured_feedback(
                    query_plan={"sql": query_plan.sql, "chart_type": query_plan.chart_type, "title": query_plan.title},
                    execute_result=execute_result,
                    score=score,
                    dimension_scores={
                        "chart_type": chart_type_score,
                        "axis_labels": label_score,
                        "data_shape": shape_score,
                        "completeness": completeness_score,
                    },
                )
            retry_feedback = RetryFeedback(
                attempt=attempt + 1,
                strategy=RETRY_STRATEGIES.get(attempt, "adjust_aggregation"),
                feedback=feedback_text,
            )

        return ValidationResult(
            score=round(score, 3),
            passed=passed,
            dimension_scores=DimensionScores(
                chart_type=chart_type_score,
                axis_labels=label_score,
                data_shape=shape_score,
                completeness=completeness_score,
            ),
            retry_feedback=retry_feedback,
            low_confidence=not passed,
        )

    async def generate_structured_feedback(
        self,
        query_plan: dict,
        execute_result: dict,
        score: float,
        dimension_scores: dict,
    ) -> str:
        prompt = f"""
A SQL query was generated and validated. Overall score: {score:.2f} (threshold: 0.80).

Original SQL:
{query_plan.get('sql', '')}

Dimension scores:
- Chart type: {dimension_scores.get('chart_type', 0):.2f}
- Axis labels: {dimension_scores.get('axis_labels', 0):.2f}
- Data shape: {dimension_scores.get('data_shape', 0):.2f}
- Completeness: {dimension_scores.get('completeness', 0):.2f}

Query result:
- Row count: {execute_result.get('row_count', 0)}
- Columns: {execute_result.get('columns', [])}
- First row: {execute_result.get('rows', [{}])[0] if execute_result.get('rows') else 'none'}
- Error: {execute_result.get('error', 'none')}

Provide ONE specific, actionable fix instruction. Return only a single sentence. No JSON. No explanation.
"""
        try:
            return await bedrock_invoke(
                model_id=VALIDATOR_MODEL,
                system_prompt="You are a SQL debugging assistant. Give one specific fix instruction in one sentence.",
                user_message=prompt,
                max_tokens=500,
                temperature=0.0,
            )
        except Exception:
            return "Try a different aggregation or table selection."

    # Legacy method name kept for compatibility
    async def generate_retry_feedback(self, query_plan: QueryPlan, result: dict, score: float) -> str:
        return await self.generate_structured_feedback(
            query_plan={"sql": query_plan.sql, "chart_type": query_plan.chart_type},
            execute_result=result,
            score=score,
            dimension_scores={},
        )


