"""
Skill: analyze_layout
Assign grid positions (row, col, row_span, col_span) from bounding boxes.
Usage:
    python analyze_layout.py   (reads list of charts with x,y,width,height from stdin)
"""
import json
import sys


def analyze_layout(charts: list, cols: int = 12) -> list:
    """
    Assign CSS grid positions using normalised bounding box coordinates.
    x, y, width, height are all fractions of image dimensions (0.0–1.0).
    """
    # Sort by top-to-bottom, left-to-right
    sorted_charts = sorted(charts, key=lambda c: (c.get("y", 0), c.get("x", 0)))

    result = []
    for chart in sorted_charts:
        x = chart.get("x", 0.0)
        y = chart.get("y", 0.0)
        w = chart.get("width", 0.5)
        h = chart.get("height", 0.5)

        col_start = max(1, round(x * cols) + 1)
        col_span = max(1, round(w * cols))
        row_start = max(1, round(y * 20) + 1)
        row_span = max(1, round(h * 20))

        result.append({
            **chart,
            "grid_layout": {
                "col": col_start,
                "row": row_start,
                "col_span": col_span,
                "row_span": row_span,
            },
        })

    return result


if __name__ == "__main__":
    charts = json.load(sys.stdin)
    result = analyze_layout(charts)
    print(json.dumps(result, indent=2))
