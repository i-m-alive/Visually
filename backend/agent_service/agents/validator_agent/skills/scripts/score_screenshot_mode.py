"""
Skill: score_screenshot_mode
Compare live query result against vision-extracted chart data.
Uses DTW for time-series, KL divergence for categorical distributions.
Acceptance threshold: 95%.
Usage:
    python score_screenshot_mode.py
    (reads JSON with live_rows, chart_spec from stdin)
"""
import json
import math
import sys


def _normalize(values: list) -> list:
    mn, mx = min(values), max(values)
    rng = mx - mn
    if rng == 0:
        return [0.5] * len(values)
    return [(v - mn) / rng for v in values]


def _dtw(a: list, b: list) -> float:
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return 1.0
    dp = [[float("inf")] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = abs(a[i - 1] - b[j - 1])
            dp[i][j] = cost + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    return dp[n][m] / max(n, m)


def _kl_divergence(p: list, q: list) -> float:
    eps = 1e-9
    total_p, total_q = sum(p) + eps, sum(q) + eps
    p_norm = [(x + eps) / total_p for x in p]
    q_norm = [(x + eps) / total_q for x in q]
    kl = sum(pi * math.log(pi / qi) for pi, qi in zip(p_norm, q_norm))
    kl_sym = kl + sum(qi * math.log(qi / pi) for pi, qi in zip(p_norm, q_norm))
    return min(kl_sym / 10.0, 1.0)


def score_screenshot_mode(live_rows: list, chart_spec: dict) -> dict:
    chart_type = chart_spec.get("chart_type", "bar")
    estimated = chart_spec.get("estimated_values", [])

    if not live_rows or not estimated:
        return {"score": 0.0, "passed": False, "reason": "missing data"}

    # Extract first numeric column from live rows
    live_values = []
    if live_rows:
        numeric_col = next((k for k, v in live_rows[0].items() if isinstance(v, (int, float))), None)
        if numeric_col:
            live_values = [float(row.get(numeric_col, 0)) for row in live_rows]

    if not live_values:
        return {"score": 0.0, "passed": False, "reason": "no numeric column in live result"}

    est_values = [float(v) for v in estimated if isinstance(v, (int, float))]
    if not est_values:
        return {"score": 0.5, "passed": False, "reason": "no estimated values in chart spec"}

    live_norm = _normalize(live_values)
    est_norm = _normalize(est_values)

    if chart_type in ("line", "area", "bar"):
        distance = _dtw(live_norm, est_norm)
        shape_score = max(0.0, 1.0 - distance)
    else:
        divergence = _kl_divergence(live_values, est_values)
        shape_score = max(0.0, 1.0 - divergence)

    THRESHOLD = 0.95
    return {
        "score": round(shape_score, 4),
        "passed": shape_score >= THRESHOLD,
        "threshold": THRESHOLD,
        "chart_type": chart_type,
    }


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = score_screenshot_mode(data.get("live_rows", []), data.get("chart_spec", {}))
    print(json.dumps(result, indent=2))
