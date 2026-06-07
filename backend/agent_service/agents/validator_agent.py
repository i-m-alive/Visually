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

    async def validate(self, query_plan: QueryPlan, execute_result: dict, attempt: int = 1) -> ValidationResult:
        row_count = execute_result.get("row_count", 0)
        columns = execute_result.get("columns", [])
        actual_x = columns[0] if columns else ""
        actual_y = columns[1] if len(columns) > 1 else ""

        chart_type_score = self.validate_chart_type(query_plan.chart_type, query_plan.chart_type)
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
        if not passed and attempt <= 3:
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
                max_tokens=200,
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


import math as _math


def _normalize_values(values: list) -> list:
    if not values:
        return []
    clean = [float(str(v).replace("~", "") or 0) for v in values]
    min_v, max_v = min(clean), max(clean)
    if max_v == min_v:
        return [1.0] * len(clean)
    return [(v - min_v) / (max_v - min_v) for v in clean]


def _dtw_distance(a: list, b: list) -> float:
    if not a or not b:
        return 1.0
    a_n, b_n = _normalize_values(a), _normalize_values(b)
    n, m = len(a_n), len(b_n)
    dtw = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    dtw[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(a_n[i - 1] - b_n[j - 1])
            dtw[i][j] = cost + min(dtw[i - 1][j], dtw[i][j - 1], dtw[i - 1][j - 1])
    return dtw[n][m] / (n + m)


def _kl_divergence(p: list, q: list) -> float:
    if not p or not q or len(p) != len(q):
        return 1.0
    sum_p = sum(p) or 1.0
    sum_q = sum(q) or 1.0
    p_n = [x / sum_p + 1e-10 for x in p]
    q_n = [x / sum_q + 1e-10 for x in q]
    kl_pq = sum(pi * _math.log(pi / qi) for pi, qi in zip(p_n, q_n))
    kl_qp = sum(qi * _math.log(qi / pi) for pi, qi in zip(p_n, q_n))
    return (kl_pq + kl_qp) / 2


def _label_similarity(a: str, b: str) -> float:
    words_a = set(a.replace("($)", "").replace("(k)", "").replace("(%)", "").split())
    words_b = set(b.replace("($)", "").replace("(k)", "").replace("(%)", "").split())
    if not words_a and not words_b:
        return 1.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0


def _compute_shape_score_screenshot(
    chart_type: str,
    chart_spec: dict,
    actual_rows: list,
    actual_columns: list,
) -> float:
    estimated = chart_spec.get("estimated_values", {})
    if chart_type in ("line", "area", "stacked_area"):
        est_vals = [float(str(v).replace("~", "") or 0) for v in estimated.values() if v is not None]
        if not actual_rows or not est_vals:
            return 0.5
        numeric_col = next(
            (c for c in actual_columns if c not in ("period", "date", "month", "week", "year")),
            actual_columns[-1] if actual_columns else None,
        )
        if not numeric_col:
            return 0.5
        act_vals = [float(row.get(numeric_col, 0) or 0) for row in actual_rows[:len(est_vals)]]
        dist = _dtw_distance(est_vals, act_vals)
        return max(0.0, 1.0 - dist * 2)
    elif chart_type in ("bar_vertical", "bar_horizontal", "pie", "donut", "funnel"):
        est_vals = [float(str(v).replace("~", "") or 0) for v in estimated.values() if v is not None]
        if not actual_rows or not est_vals:
            return 0.5
        numeric_col = next(
            (c for c in actual_columns if actual_rows and isinstance(actual_rows[0].get(c), (int, float))),
            None,
        )
        if not numeric_col:
            return 0.5
        act_vals = [float(row.get(numeric_col, 0) or 0) for row in actual_rows[:len(est_vals)]]
        kl = _kl_divergence(est_vals, act_vals)
        return max(0.0, 1.0 - kl / 2)
    elif chart_type == "kpi_card":
        kpi_est = estimated.get("value")
        if not kpi_est or not actual_rows:
            return 0.5
        kpi_act = float(list(actual_rows[0].values())[0] or 0) if actual_rows else 0
        if kpi_act == 0:
            # SQL returned 0 — likely a wrong filter or wrong table, not a shape problem.
            # Return a low-but-non-zero score so the retry feedback targets the value.
            return 0.2
        em = _math.floor(_math.log10(abs(float(str(kpi_est).replace("~", "")) or 1) + 1))
        am = _math.floor(_math.log10(abs(kpi_act) + 1))
        diff = abs(em - am)
        return 1.0 if diff == 0 else (0.7 if diff == 1 else 0.2)
    elif chart_type in ("table", "data_table"):
        # Table widgets show N visible rows but the SQL should return ALL relevant rows —
        # so data_point_count from the screenshot is the viewport size, not the query limit.
        # Give at least 0.6 for any non-empty result; apply a soft penalty only for near-empty.
        if not actual_rows:
            return 0.0
        return 0.8  # table charts get high shape score when they return any data
    else:
        expected_count = int(chart_spec.get("data_point_count") or 5)
        if not actual_rows:
            return 0.0
        ratio = min(len(actual_rows), expected_count) / max(len(actual_rows), expected_count)
        return ratio


def _extract_column_from_error(error: str) -> str:
    """Pull the offending column name out of a DB error message."""
    m = re.search(r'column ["\']?(\w+)["\']?', error, re.IGNORECASE)
    if m:
        return m.group(1)
    # Redshift-style: 'column "foo" does not exist'
    m2 = re.search(r'"(\w+)"', error)
    return m2.group(1) if m2 else "unknown_column"


def _detect_scale_mismatch(
    estimated: dict,
    rows: list,
    tolerance_ratio: float = 100.0,
) -> Optional[str]:
    """
    Compare vision-estimated values against actual SQL results.
    Returns a human-readable mismatch description when values differ by
    more than tolerance_ratio (default 100×), otherwise None.
    """
    def parse_est(val_str: str) -> Optional[float]:
        s = str(val_str).replace("~", "").replace(",", "").strip()
        m = re.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
        if not m:
            return None
        n = float(m.group(1))
        suffix = m.group(2).lower()
        return n * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(suffix, 1.0)

    # Collect all numeric values from the first 10 result rows
    actual_nums: list[float] = []
    for row in rows[:10]:
        for v in row.values():
            try:
                actual_nums.append(float(v))
            except (TypeError, ValueError):
                pass

    if not actual_nums:
        return None

    avg_actual = sum(actual_nums) / len(actual_nums)

    for label, est_str in list(estimated.items())[:3]:
        est_val = parse_est(str(est_str))
        if not est_val or est_val == 0:
            continue
        ratio = avg_actual / est_val
        if ratio < 1.0 / tolerance_ratio:
            return (
                f"Values are ~{int(1/ratio)}× too small. "
                f"Expected ~{est_val:,.0f} (from chart), got ~{avg_actual:,.0f} from SQL. "
                f"Check if you need SUM instead of COUNT, or are missing a join."
            )
        if ratio > tolerance_ratio:
            return (
                f"Values are ~{int(ratio)}× too large. "
                f"Expected ~{est_val:,.0f} (from chart), got ~{avg_actual:,.0f} from SQL. "
                f"Check aggregation — you may be double-counting or using raw values instead of aggregated."
            )
    return None


def classify_failure(
    sql_result: dict,
    chart_spec: dict,
    query_plan: dict,
) -> dict:
    """
    Classify why a SQL attempt failed and produce a targeted retry instruction.

    Returns:
      {
        "failure_type":      str,   one of the categories below
        "specific_issue":    str,   human-readable root cause
        "retry_instruction": str,   injected as retry_feedback into next generation
        "switch_candidate":  bool,  True → advance to next ranked candidate table
      }
    """
    error = (sql_result.get("error") or "").lower()
    rows = sql_result.get("rows") or []
    row_count = sql_result.get("row_count", 0)

    # ── 1. DB-level errors ────────────────────────────────────────────────────
    if error:
        if any(kw in error for kw in [
            "does not exist", "undefined column", "unknown column", "no such column",
            "invalid column", "column not found",
        ]):
            bad_col = _extract_column_from_error(error)
            return {
                "failure_type": "column_not_found",
                "specific_issue": f"Column '{bad_col}' does not exist in the table.",
                "retry_instruction": (
                    f"Column '{bad_col}' does not exist. "
                    "Use ONLY the exact column names listed in the schema for this table. "
                    "Do not invent, guess, or alias column names that are not in the schema."
                ),
                "switch_candidate": False,
            }

        if any(kw in error for kw in ["syntax error", "parse error", "unexpected token", "invalid syntax"]):
            dialect = (query_plan or {}).get("db_dialect") or "redshift"
            return {
                "failure_type": "syntax_error",
                "specific_issue": f"SQL syntax error for dialect '{dialect}': {error[:150]}",
                "retry_instruction": (
                    f"The SQL had a syntax error. Dialect is {dialect}. "
                    "For Redshift: use GROUP BY ordinal positions (1, 2, 3), GETDATE() not NOW(), "
                    "DATE_TRUNC('period', col) for truncation, ILIKE for case-insensitive match, "
                    "no TEXT type (use VARCHAR). Rewrite the query from scratch with correct syntax."
                ),
                "switch_candidate": False,
            }

        if any(kw in error for kw in [
            "no such table", "relation", "table not found", "does not exist" , "undefined table",
        ]) and "column" not in error:
            return {
                "failure_type": "wrong_table",
                "specific_issue": f"Table does not exist: {error[:150]}",
                "retry_instruction": (
                    "The table you used does not exist in the database. "
                    "Use ONLY the table names listed in the schema. "
                    "Do not invent table names."
                ),
                "switch_candidate": True,
            }

        if any(kw in error for kw in ["permission denied", "access denied", "insufficient privilege"]):
            return {
                "failure_type": "permission_denied",
                "specific_issue": "Permission denied on this table or column.",
                "retry_instruction": (
                    "Permission denied. Try a different table from the schema — "
                    "use the next best candidate table instead."
                ),
                "switch_candidate": True,
            }

        # Generic DB error — try alternate table
        return {
            "failure_type": "db_error",
            "specific_issue": f"Database error: {error[:200]}",
            "retry_instruction": (
                f"The query failed with a database error: {error[:150]}. "
                "Fix the SQL or try an alternate table."
            ),
            "switch_candidate": False,
        }

    # ── 2. Zero rows ──────────────────────────────────────────────────────────
    if row_count == 0 or not rows:
        sql_lower = ((query_plan or {}).get("sql") or "").lower()
        has_date_filter = any(kw in sql_lower for kw in [
            "where", "between", "date_trunc", "dateadd", "> '", "< '",
        ])
        if has_date_filter:
            return {
                "failure_type": "wrong_date_range",
                "specific_issue": "Query returned 0 rows — date filter likely excludes all data.",
                "retry_instruction": (
                    "The query returned 0 rows. The date/time filter is probably wrong or too narrow. "
                    "Remove the WHERE date filter entirely first to confirm the table has data, "
                    "then add a broad date range. Do NOT hardcode specific years — use MIN/MAX from the table."
                ),
                "switch_candidate": False,
            }
        return {
            "failure_type": "zero_rows",
            "specific_issue": "Query returned 0 rows — wrong table or WHERE clause too restrictive.",
            "retry_instruction": (
                "The query returned 0 rows. Either you used the wrong table or the WHERE clause "
                "is too restrictive. Try the query WITHOUT any WHERE filters first to confirm the "
                "table has data. Then add filters one at a time."
            ),
            "switch_candidate": True,
        }

    # ── 3. Wrong numeric scale ────────────────────────────────────────────────
    estimated = chart_spec.get("estimated_values") or {}
    if estimated and rows:
        scale_issue = _detect_scale_mismatch(estimated, rows)
        if scale_issue:
            return {
                "failure_type": "wrong_scale",
                "specific_issue": scale_issue,
                "retry_instruction": (
                    f"{scale_issue} "
                    "Re-read the chart's estimated_values carefully and choose the correct "
                    "aggregation (SUM vs COUNT vs AVG) to match the expected magnitude."
                ),
                "switch_candidate": False,
            }

    # ── 4. Value magnitude mismatch (value_match=0 with data present) ─────────
    # When we have rows but score_value_match returns 0.0, give the LLM a specific
    # numeric ratio so it knows exactly how wrong its aggregation is.
    if estimated and rows:
        vm_score, _vm_text = score_value_match(estimated, rows, chart_spec.get("type", ""))
        if vm_score == 0.0:
            actual_nums: list[float] = []
            for row in rows[:10]:
                for v in row.values():
                    try:
                        actual_nums.append(float(v))
                    except (TypeError, ValueError):
                        pass

            def _pe(v: str):
                import re as _re
                s = str(v).replace("~", "").replace(",", "").strip()
                m = _re.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
                if not m:
                    return None
                n = float(m.group(1))
                suffix = m.group(2).lower()
                return n * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(suffix, 1.0)

            est_vals = [v for v in [_pe(str(ev)) for ev in estimated.values()] if v and v > 0]

            if est_vals and actual_nums:
                est_avg = sum(est_vals) / len(est_vals)
                act_avg = sum(actual_nums) / len(actual_nums)
                if est_avg > 0 and act_avg > 0:
                    ratio = act_avg / est_avg
                    direction = "too small" if ratio < 1 else "too large"
                    if ratio < 0.01:
                        agg_hint = "use SUM instead of COUNT — your values are 100× too small"
                    elif ratio < 0.1:
                        agg_hint = "use SUM instead of COUNT, or check for a missing join that would multiply rows"
                    elif ratio < 0.5:
                        agg_hint = "consider SUM instead of COUNT, or remove a GROUP BY column that is splitting the data"
                    elif ratio > 100:
                        agg_hint = "use COUNT or COUNT(DISTINCT id_col) instead of SUM — your values are 100× too large"
                    elif ratio > 10:
                        agg_hint = "use COUNT(DISTINCT id_col) or AVG instead of SUM to avoid double-counting"
                    elif ratio > 2:
                        agg_hint = "try AVG or COUNT(DISTINCT) — you may be double-counting rows"
                    else:
                        agg_hint = "adjust your WHERE filters or date range to narrow/widen the result set"

                    return {
                        "failure_type": "value_magnitude_mismatch",
                        "specific_issue": (
                            f"SQL avg={act_avg:,.1f} but chart expects ~{est_avg:,.1f} "
                            f"({ratio:.2f}× {direction})."
                        ),
                        "retry_instruction": (
                            f"Your SQL returns values averaging {act_avg:,.1f} but the chart expects ~{est_avg:,.1f} "
                            f"({ratio:.2f}× {direction}). "
                            f"Fix: {agg_hint}. "
                            "Re-read estimated_values and pick the aggregation that produces the correct magnitude."
                        ),
                        "switch_candidate": ratio > 50,  # if wildly off, also try a different table
                    }

    # ── 5. Generic low-score fallback ─────────────────────────────────────────
    return {
        "failure_type": "low_validation_score",
        "specific_issue": "Chart data does not closely match the original.",
        "retry_instruction": (
            "The generated chart does not match the original closely enough. "
            "Re-read the chart_spec carefully: match the chart type, axis label semantics, "
            "estimated values magnitude, and x_tick_labels. "
            "Choose aggregation and grouping that produce the same shape as the original chart."
        ),
        "switch_candidate": False,
    }


def score_value_match(
    estimated_values: dict,
    rows: list,
    chart_type: str,
    tolerance: float = 0.20,
) -> tuple:
    """
    Compare vision-estimated values against actual SQL result data using fuzzy numeric matching.

    Returns (score: float 0-1, mismatch_description: str | None).
      1.0  →  ≥70 % of estimated values are within ±tolerance of actual results
      0.5  →  40-70 % match (or can't fully judge)
      0.0  →  <40 % match (values wildly off)

    The mismatch_description is injected into retry_feedback so the next SQL attempt
    knows *specifically* what magnitude to target.
    """
    def _parse(val_str: str) -> Optional[float]:
        s = str(val_str).replace("~", "").replace(",", "").strip()
        m = re.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
        if not m:
            return None
        n = float(m.group(1))
        suffix = m.group(2).lower()
        return n * {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}.get(suffix, 1.0)

    if not estimated_values or not rows:
        return 0.5, None

    # Collect all numeric values from the SQL result
    actual_nums: list[float] = []
    for row in rows[:20]:
        for v in row.values():
            try:
                actual_nums.append(float(v))
            except (TypeError, ValueError):
                pass

    if not actual_nums:
        return 0.3, "SQL returned no numeric values."

    # ── KPI / Gauge: single-value comparison ─────────────────────────────────
    if chart_type in ("kpi", "kpi_card", "gauge"):
        est_val = _parse(str(list(estimated_values.values())[0]))
        if est_val and est_val > 0:
            actual = actual_nums[0]
            ratio = actual / est_val
            if (1.0 - tolerance) <= ratio <= (1.0 + tolerance):
                return 1.0, None
            elif 0.05 <= ratio <= 20:
                return 0.5, (
                    f"KPI value {actual:,.0f} is {ratio:.1f}× the expected ~{est_val:,.0f}."
                )
            else:
                return 0.0, (
                    f"KPI value {actual:,.0f} is {ratio:.1f}× off from expected ~{est_val:,.0f}. "
                    "Check aggregation (SUM vs COUNT vs AVG)."
                )
        return 0.5, None

    # ── Multi-value charts ────────────────────────────────────────────────────
    matches = 0
    total = 0
    mismatches: list[str] = []

    for label, est_str in list(estimated_values.items())[:8]:
        est_val = _parse(str(est_str))
        if est_val is None or est_val == 0:
            continue
        total += 1
        # Find the actual value closest to the estimate (we don't have label→row mapping)
        best_ratio = min(abs(a / est_val - 1.0) for a in actual_nums) if actual_nums else 999.0
        if best_ratio <= tolerance:
            matches += 1
        else:
            mismatches.append(
                f"'{label}' expected ~{est_val:,.0f}, "
                f"closest actual was {min(actual_nums, key=lambda a: abs(a/est_val-1)):,.0f}"
            )

    if total == 0:
        return 0.5, None

    match_rate = matches / total
    mismatch_text = "; ".join(mismatches[:3]) if mismatches else None

    if match_rate >= 0.70:
        return 1.0, None
    elif match_rate >= 0.40:
        return 0.5, mismatch_text
    else:
        return 0.0, mismatch_text


def score_chart_screenshot_mode(
    chart_spec: dict,
    query_plan: dict,
    execute_result: dict,
) -> dict:
    """
    Full validation scoring for screenshot replication mode. Threshold: 0.72.

    Five dimensions (weights sum to 1.0):
      chart_type   0.20  — exact type match (after alias normalisation)
      axis_labels  0.15  — Jaccard similarity of axis label words
      data_shape   0.20  — DTW (time-series) / KL divergence (categorical)
      completeness 0.20  — row-count ratio vs expected data_point_count
      value_match  0.25  — fuzzy numeric comparison vs vision-estimated values
    """
    dim_scores: dict[str, float] = {}
    value_mismatch: Optional[str] = None

    type_aliases = {"bar": "bar_vertical", "bar_chart": "bar_vertical", "line_chart": "line", "kpi": "kpi_card"}
    expected_type = type_aliases.get(chart_spec.get("type", ""), chart_spec.get("type", ""))
    rendered_type = type_aliases.get(query_plan.get("chart_type", ""), query_plan.get("chart_type", ""))
    dim_scores["chart_type"] = 1.0 if expected_type == rendered_type else 0.0

    exp_x = (chart_spec.get("x_axis_label") or "").lower().strip()
    exp_y = (chart_spec.get("y_axis_label") or "").lower().strip()
    ren_x = (query_plan.get("x_axis_label") or "").lower().strip()
    ren_y = (query_plan.get("y_axis_label") or "").lower().strip()
    x_score = _label_similarity(exp_x, ren_x) if exp_x and ren_x else 0.7
    y_score = _label_similarity(exp_y, ren_y) if exp_y and ren_y else 0.7
    dim_scores["axis_labels"] = (x_score + y_score) / 2

    rows = execute_result.get("rows", [])
    if execute_result.get("error") or execute_result.get("row_count", 0) == 0:
        dim_scores["data_shape"] = 0.0
    else:
        # Use the already-aliased expected_type so _compute_shape_score_screenshot
        # receives "bar_vertical" / "line" / "kpi_card" instead of raw vision output
        # like "bar" or "bar_chart" which falls through to the generic else branch.
        dim_scores["data_shape"] = _compute_shape_score_screenshot(
            expected_type,
            chart_spec,
            rows,
            execute_result.get("columns", []),
        )

    expected_count = int(chart_spec.get("data_point_count") or 5)
    actual_count = int(execute_result.get("row_count") or 0)
    if actual_count == 0:
        dim_scores["completeness"] = 0.0
    elif expected_type in ("table", "data_table"):
        # Table charts should return all matching rows — the screenshot's data_point_count
        # is the viewport size, not the total result set. Any non-empty result gets 0.8.
        dim_scores["completeness"] = 0.8
    else:
        ratio = min(actual_count, expected_count) / max(actual_count, expected_count)
        dim_scores["completeness"] = ratio  # plain ratio — no double-penalty

    # Dimension 5: fuzzy numeric value match vs vision estimates
    vm_score, value_mismatch = score_value_match(
        estimated_values=chart_spec.get("estimated_values") or {},
        rows=rows,
        chart_type=chart_spec.get("type", ""),
    )
    dim_scores["value_match"] = vm_score

    weights = {
        "chart_type":   0.20,   # was 0.25
        "axis_labels":  0.15,   # was 0.20
        "data_shape":   0.20,   # was 0.25
        "completeness": 0.20,   # was 0.30
        "value_match":  0.25,   # NEW — most reliable signal for numeric accuracy
    }
    overall = sum(dim_scores[k] * weights[k] for k in weights)
    return {
        "score": round(overall, 3),
        "dimension_scores": dim_scores,
        "passed": overall >= 0.72,
        "chart_type_match": dim_scores["chart_type"] == 1.0,
        "value_mismatch": value_mismatch,   # surfaced in retry feedback when not None
    }
