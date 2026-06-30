"""
Multi-candidate ranking and ambiguity detection.

Used by both the pipeline (orchestrator) and chat paths to decide when
multiple table candidates should be executed and compared, and whether
to auto-select the best result or surface alternatives to the user.

No I/O — pure Python logic, importable from any async context.
"""
from dataclasses import dataclass
from typing import Optional

# Runner-up / top score must be >= this to consider the result ambiguous.
AMBIGUITY_RATIO = 0.65
# Maximum number of candidates to explore in parallel.
MAX_CANDIDATES = 3
# Tables scoring below this fraction of the top score are excluded.
MIN_NORMALIZED_SCORE = 0.40
# When the best candidate's confidence exceeds the second by this margin,
# auto-select instead of surfacing alternatives.
AUTO_SELECT_GAP = 0.22


@dataclass
class CandidateScore:
    table_name: str
    rank_score: float
    normalized_score: float   # rank_score / top_rank_score, in [0, 1]


# ── Candidate extraction ───────────────────────────────────────────────────────

def get_pipeline_candidates(
    table_candidates: list,   # list[TableCandidate] from graph_rag_retriever
) -> list[CandidateScore]:
    """Extract and normalise scores from graph_rag_retriever TableCandidates.
    Returns an empty list when there is no useful signal."""
    if not table_candidates:
        return []
    top_score = table_candidates[0].score
    if top_score <= 0:
        return []
    result: list[CandidateScore] = []
    for tc in table_candidates[:MAX_CANDIDATES]:
        norm = tc.score / top_score
        if norm < MIN_NORMALIZED_SCORE:
            break
        result.append(CandidateScore(
            table_name=tc.table_name,
            rank_score=tc.score,
            normalized_score=round(norm, 3),
        ))
    return result


def get_chat_candidates(
    table_scores: dict,   # {table_name: score} from nl_schema_router
) -> list[CandidateScore]:
    """Extract and normalise scores from nl_schema_router table_scores dict."""
    if not table_scores:
        return []
    sorted_items = sorted(table_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_items[0][1]
    if top_score <= 0:
        return []
    result: list[CandidateScore] = []
    for name, score in sorted_items[:MAX_CANDIDATES]:
        norm = score / top_score
        if norm < MIN_NORMALIZED_SCORE:
            break
        result.append(CandidateScore(
            table_name=name,
            rank_score=score,
            normalized_score=round(norm, 3),
        ))
    return result


# ── Ambiguity detection ────────────────────────────────────────────────────────

def is_ambiguous(candidates: list[CandidateScore]) -> bool:
    """True when the runner-up is competitive enough to warrant exploring
    alternative candidates. Returns False for 0 or 1 candidates."""
    if len(candidates) < 2:
        return False
    return candidates[1].normalized_score >= AMBIGUITY_RATIO


# ── Result quality scoring ─────────────────────────────────────────────────────

def score_result_quality(rows: list, columns: list) -> float:
    """Score the quality of an executed query result (0.0–1.0).

    Considers three signals:
      - Row count (2–200 rows is ideal for a chart)
      - Non-null ratio across all cells
      - Presence of at least one numeric column (needed for charts)
    """
    if not rows or not columns:
        return 0.0
    row_count = len(rows)
    # Row count component
    if row_count == 0:
        return 0.0
    elif 2 <= row_count <= 200:
        row_score = 0.40
    elif row_count == 1:
        row_score = 0.15   # KPI scalar — valid but not ideal for trend/rank queries
    elif row_count <= 1000:
        row_score = 0.28
    else:
        row_score = 0.10   # too many rows; chart will be unreadable
    # Non-null ratio
    total_cells = row_count * len(columns)
    null_cells = sum(1 for r in rows for v in r.values() if v is None)
    null_score = (1.0 - null_cells / total_cells) * 0.30 if total_cells > 0 else 0.0
    # Numeric column presence
    numeric_cols = sum(
        1 for col in columns
        if any(
            isinstance(r.get(col), (int, float)) and r.get(col) is not None
            for r in rows[:5]
        )
    )
    numeric_score = min(numeric_cols / max(len(columns), 1), 1.0) * 0.30
    return min(row_score + null_score + numeric_score, 1.0)


# ── Confidence computation ─────────────────────────────────────────────────────

def compute_final_confidence(
    rank_score: float,
    result_quality: float,
    top_rank_score: float,
) -> float:
    """Blend ranking relevance (55 %) and result quality (45 %) into a single
    confidence value in [0, 1].  top_rank_score normalises across candidates."""
    normalised = min(rank_score / max(top_rank_score, 0.001), 1.0)
    return round(0.55 * normalised + 0.45 * result_quality, 3)


# ── Auto-select decision ───────────────────────────────────────────────────────

def should_auto_select(candidates: list[dict]) -> bool:
    """True when the best candidate's confidence exceeds the second by AUTO_SELECT_GAP.
    When True the system should pick the winner silently instead of asking the user."""
    if len(candidates) < 2:
        return True
    sorted_c = sorted(candidates, key=lambda x: x.get("confidence", 0), reverse=True)
    gap = sorted_c[0]["confidence"] - sorted_c[1]["confidence"]
    return gap > AUTO_SELECT_GAP


# ── Label helpers ──────────────────────────────────────────────────────────────

def candidate_label(index: int, table_name: str) -> str:
    """Human-readable label for a candidate option, e.g. 'Option A — placements'."""
    letter = chr(65 + index)   # A, B, C
    bare = table_name.split(".")[-1].replace("_", " ").title()
    return f"Option {letter} — {bare}"
