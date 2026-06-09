"""
SQL date condition extractor.

Used during dashboard assembly to detect which charts filter by a date column
so a global date-range picker can be shown in the frontend.

Conservative: only matches columns whose name contains a date-like keyword.
False negatives are safe; false positives would surface a spurious date picker.
"""
import re
from typing import Optional

# Heuristic: right-most segment of a qualified column must contain one of these
_DATE_HINTS = ("date", "time", "period", "year", "month", "_at", "_on", "created", "updated")


def _is_date_col(col: str) -> bool:
    lower = col.lower().split(".")[-1]
    return any(h in lower for h in _DATE_HINTS)


# Match patterns (minimum 6-char values to avoid matching short UUIDs / IDs)
_BETWEEN = re.compile(
    r"\b(\w+(?:\.\w+)*)\s+BETWEEN\s+'([^']{6,}?)'\s+AND\s+'([^']{6,}?)'",
    re.IGNORECASE,
)
_GTE_LTE = re.compile(
    r"\b(\w+(?:\.\w+)*)\s*>=\s*'([^']{6,}?)'\s+AND\s+\1\s*<=\s*'([^']{6,}?)'",
    re.IGNORECASE,
)
_LTE_GTE = re.compile(
    r"\b(\w+(?:\.\w+)*)\s*<=\s*'([^']{6,}?)'\s+AND\s+\1\s*>=\s*'([^']{6,}?)'",
    re.IGNORECASE,
)
# Open-ended: col >= 'YYYY-MM-DD' (no upper bound)
_GTE_ONLY = re.compile(
    r"\b(\w+(?:\.\w+)*)\s*>=\s*'(\d{4}-\d{2}-\d{2}[^']*)'",
    re.IGNORECASE,
)


def extract_date_filter(sql: str) -> Optional[dict]:
    """
    Scan sql for the first date-column WHERE condition.

    Returns:
        {
          "column":          "dateadded",          # bare name, no table prefix
          "table_qualified": "staging.orders",     # or None
          "start":           "2025-01-01",
          "end":             "2025-03-31",          # or None for open-ended
        }
    or None if no date condition is detected.
    """
    if not sql:
        return None

    # BETWEEN and pair-range patterns (both sides known)
    for pattern, swap in [
        (_BETWEEN, False),
        (_GTE_LTE, False),
        (_LTE_GTE, True),   # groups are (col, end, start) — swap them
    ]:
        for m in pattern.finditer(sql):
            col = m.group(1)
            if _is_date_col(col):
                a, b = m.group(2)[:10], m.group(3)[:10]
                start, end = (b, a) if swap else (a, b)
                return {
                    "column":          col.split(".")[-1],
                    "table_qualified": col if "." in col else None,
                    "start":           start,
                    "end":             end,
                }

    # Open-ended: col >= 'YYYY-MM-DD' with no upper bound
    for m in _GTE_ONLY.finditer(sql):
        col = m.group(1)
        if _is_date_col(col):
            return {
                "column":          col.split(".")[-1],
                "table_qualified": col if "." in col else None,
                "start":           m.group(2)[:10],
                "end":             None,
            }

    return None
