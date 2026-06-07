"""
Filter Resolver — maps vision-extracted filter display names / column hints
to real database column names using the enriched schema.

Problem: the vision model generates English-sounding column hints like
"employment_type" or "parent_name" which often don't match actual DB column
names. This module scores every column in the schema against the hint using
word-overlap + substring + exact-match scoring and returns the best candidate
above a configurable threshold.

Usage:
    resolved = resolve_all_filters(detected_filters, enriched.compact_tables)
    # each item gets resolved_column, resolved_table, resolution_score, resolved
"""
import re
from typing import Optional


# ── Text normalisation ────────────────────────────────────────────────────────

def _words(text: str) -> list[str]:
    """Split snake_case / camelCase / spaces → lowercase word tokens."""
    # Insert space before uppercase runs so camelCase splits cleanly
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    spaced = re.sub(r"([a-z\d])([A-Z])", r"\1 \2", spaced)
    return [w for w in re.sub(r"[^a-z0-9]", " ", spaced.lower()).split() if len(w) > 1]


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_column(hint_words: list[str], col_name: str) -> float:
    """
    Score a DB column name against the hint word list.

    Components:
    - Jaccard word-overlap  (0–1, weight 0.5)
    - Substring coverage    (fraction of hint words that appear inside col_name, weight 0.3)
    - Exact-join bonus      (hint joined with _ == col name, +0.4)

    Returns a value in [0.0, 1.0] (clamped).
    """
    if not hint_words:
        return 0.0

    col_words = _words(col_name)
    if not col_words:
        return 0.0

    h_set = set(hint_words)
    c_set = set(col_words)

    # Jaccard
    union = h_set | c_set
    jaccard = len(h_set & c_set) / len(union) if union else 0.0

    # Substring: fraction of hint tokens that appear anywhere in the column name
    col_lower = col_name.lower()
    sub_hits = sum(1 for w in hint_words if w in col_lower)
    sub_score = sub_hits / len(hint_words)

    score = jaccard * 0.5 + sub_score * 0.3

    # Exact join bonus
    if "_".join(hint_words) == col_lower or "".join(hint_words) == col_lower:
        score += 0.4

    return min(1.0, score)


# ── Public API ────────────────────────────────────────────────────────────────

def resolve_filter_column(
    display_name: str,
    column_hint: str,
    compact_tables: list,
    min_score: float = 0.35,
) -> Optional[dict]:
    """
    Find the best-matching (table, column) pair for a single filter.

    Tries both the column_hint and the display_name as query terms, takes the
    best result across both.

    Args:
        display_name:   Human-readable label from the UI  (e.g. "Employment Type")
        column_hint:    Snake-case guess from vision model (e.g. "employment_type")
        compact_tables: List of compact table dicts from EnrichedSchema
        min_score:      Minimum score to consider a match (default 0.35)

    Returns:
        {"table": "schema.table_name", "column": "real_col_name", "score": 0.87}
        or None if no match above min_score.
    """
    hint_words  = _words(column_hint)
    label_words = _words(display_name)

    best: Optional[dict] = None
    best_score = 0.0

    for table in compact_tables:
        table_name = table.get("name", "")
        for col in (table.get("columns") or []):
            col_name = col.get("name") or ""
            if not col_name:
                continue
            # Try hint-based scoring
            sc1 = _score_column(hint_words,  col_name)
            sc2 = _score_column(label_words, col_name)
            sc  = max(sc1, sc2)
            if sc > best_score:
                best_score = sc
                best = {
                    "table":  table_name,
                    "column": col_name,
                    "score":  round(sc, 3),
                }

    if best and best_score >= min_score:
        return best
    return None


def resolve_all_filters(
    detected_filters: list[dict],
    compact_tables: list,
) -> list[dict]:
    """
    Resolve every detected filter against the schema.

    Each output dict keeps all original fields and adds:
        resolved_column   — best-matching DB column name (or original hint if unresolved)
        resolved_table    — best-matching qualified table name (or None)
        resolution_score  — float 0–1
        resolved          — True if a match above threshold was found

    Filters that cannot be resolved are kept so the UI can still display
    their label without any DB sampling.
    """
    if not compact_tables:
        return [
            {**f, "resolved_column": f.get("column_hint", ""), "resolved_table": None,
             "resolution_score": 0.0, "resolved": False}
            for f in detected_filters
        ]

    result = []
    for flt in detected_filters:
        col_hint     = flt.get("column_hint", "")
        display_name = flt.get("display_name", "")

        match = resolve_filter_column(display_name, col_hint, compact_tables)
        if match:
            result.append({
                **flt,
                "resolved_column":  match["column"],
                "resolved_table":   match["table"],
                "resolution_score": match["score"],
                "resolved":         True,
            })
            print(
                f"[filter_resolver] '{display_name}' ({col_hint}) → "
                f"{match['table']}.{match['column']}  score={match['score']}",
                flush=True,
            )
        else:
            result.append({
                **flt,
                "resolved_column":  col_hint,
                "resolved_table":   None,
                "resolution_score": 0.0,
                "resolved":         False,
            })
            print(
                f"[filter_resolver] '{display_name}' ({col_hint}) → no schema match "
                f"(DB query skipped)",
                flush=True,
            )

    resolved_count = sum(1 for r in result if r["resolved"])
    print(
        f"[filter_resolver] resolved {resolved_count}/{len(result)} filters",
        flush=True,
    )
    return result
