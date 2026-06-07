"""
Skill: analyze_shape_match
Compare SQL result shape against expected chart spec.
Usage:
    python analyze_shape_match.py
    (reads JSON with rows, columns, expected_row_count from stdin)
"""
import json
import sys


def analyze_shape_match(rows: list, columns: list, expected_row_count: int = 0) -> dict:
    actual = len(rows)
    issues = []
    score = 1.0

    if actual == 0:
        return {"score": 0.0, "issues": ["No rows returned"], "row_count": 0}

    # Row count plausibility
    if expected_row_count > 0:
        ratio = min(actual, expected_row_count) / max(actual, expected_row_count)
        if ratio < 0.5:
            issues.append(f"Row count mismatch: got {actual}, expected ~{expected_row_count}")
            score *= ratio

    # Check numeric columns exist
    numeric_cols = []
    if rows:
        for col in columns:
            val = rows[0].get(col)
            if isinstance(val, (int, float)):
                numeric_cols.append(col)

    if not numeric_cols:
        issues.append("No numeric columns found in result")
        score *= 0.5

    return {
        "score": round(score, 3),
        "issues": issues,
        "row_count": actual,
        "numeric_columns": numeric_cols,
    }


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = analyze_shape_match(
        data.get("rows", []),
        data.get("columns", []),
        data.get("expected_row_count", 0),
    )
    print(json.dumps(result, indent=2))
