"""
Skill: deduplicate_charts
Remove duplicate chart detections across multiple screenshots.
Uses Jaccard word-level similarity on title + type + labels.
Threshold: 0.9 → duplicate.
Usage:
    python deduplicate_charts.py   (reads list of chart specs from stdin)
"""
import json
import sys


def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa and not wb:
        return 1.0
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _chart_signature(chart: dict) -> str:
    return " ".join([
        chart.get("chart_type", ""),
        chart.get("title", ""),
        chart.get("x_axis_label", ""),
        chart.get("y_axis_label", ""),
    ])


def deduplicate_charts(charts: list, threshold: float = 0.9) -> list:
    unique = []
    for chart in charts:
        sig = _chart_signature(chart)
        dpc = int(chart.get("data_point_count") or 0)
        is_dup = False
        for kept in unique:
            kept_sig = _chart_signature(kept)
            kept_dpc = int(kept.get("data_point_count") or 0)
            text_sim = _jaccard(sig, kept_sig)
            count_ratio = (min(dpc, kept_dpc) / max(dpc, kept_dpc)) if max(dpc, kept_dpc) > 0 else 1.0
            if text_sim >= threshold and count_ratio >= 0.7:
                is_dup = True
                break
        if not is_dup:
            unique.append(chart)
    return unique


if __name__ == "__main__":
    charts = json.load(sys.stdin)
    result = deduplicate_charts(charts)
    print(json.dumps(result, indent=2))
