"""
Query Memory — per-connection store of past successful (question → SQL) pairs.

Every validated success is recorded; on new questions the most similar past
successes are injected into the SQL prompt as few-shot examples, so the agent
reuses proven table choices and SQL patterns instead of re-deriving them.
Thumbs-down feedback removes an entry so a bad answer is never re-suggested.

Storage: filesystem JSONL per connection (append-friendly, no migrations),
capped at _MAX_ENTRIES with oldest-first eviction.
"""
import json
import os
import pathlib
import re
from typing import Optional

_MAX_ENTRIES = 300
_MIN_SIMILARITY = 0.30


def _memory_dir() -> pathlib.Path:
    return pathlib.Path(
        os.getenv(
            "QUERY_MEMORY_DIR",
            str(pathlib.Path(__file__).parent.parent.parent / ".query_memory"),
        )
    )


def _memory_path(connection_id: str) -> pathlib.Path:
    safe = re.sub(r"[^\w-]", "_", connection_id)
    return _memory_dir() / f"memory_{safe}.jsonl"


def _tokenize(text: str) -> set:
    _STOP = {
        "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "me",
        "my", "show", "give", "tell", "what", "how", "many", "much", "is",
        "are", "was", "were", "please", "can", "you", "i", "we", "last",
        "this", "that", "with", "by", "per",
    }
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) >= 2}


def _load_entries(connection_id: str) -> list:
    path = _memory_path(connection_id)
    entries: list = []
    try:
        if path.exists():
            for line in path.read_text().splitlines():
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as e:
        print(f"[query_memory] read failed (non-fatal): {e}", flush=True)
    return entries


def _write_entries(connection_id: str, entries: list) -> None:
    d = _memory_dir()
    d.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(e, ensure_ascii=False, default=str) for e in entries)
    _memory_path(connection_id).write_text(payload + ("\n" if payload else ""))


def record_success(
    connection_id: str,
    question: str,
    sql: str,
    table_used: str,
    chart_type: str = "",
    score: float = 1.0,
) -> None:
    """Store a validated successful query. Dedupes by normalized question."""
    if not connection_id or not question or not sql:
        return
    try:
        entries = _load_entries(connection_id)
        q_norm = " ".join(sorted(_tokenize(question)))
        # Replace an existing entry for the same normalized question
        entries = [e for e in entries if e.get("q_norm") != q_norm]
        entries.append({
            "q_norm": q_norm,
            "question": question[:500],
            "sql": sql[:4000],
            "table_used": table_used,
            "chart_type": chart_type,
            "score": round(float(score), 3),
        })
        if len(entries) > _MAX_ENTRIES:
            entries = entries[-_MAX_ENTRIES:]
        _write_entries(connection_id, entries)
    except Exception as e:
        print(f"[query_memory] record failed (non-fatal): {e}", flush=True)


def remove_entry(connection_id: str, question: str) -> bool:
    """Drop the memory entry for a question (thumbs-down feedback)."""
    try:
        entries = _load_entries(connection_id)
        q_norm = " ".join(sorted(_tokenize(question)))
        kept = [e for e in entries if e.get("q_norm") != q_norm]
        if len(kept) != len(entries):
            _write_entries(connection_id, kept)
            return True
    except Exception as e:
        print(f"[query_memory] remove failed (non-fatal): {e}", flush=True)
    return False


def find_similar(connection_id: str, question: str, k: int = 3) -> list:
    """
    Return up to k past successful queries most similar to the question
    (Jaccard token overlap ≥ _MIN_SIMILARITY), shaped for prompt injection.
    """
    try:
        entries = _load_entries(connection_id)
        if not entries:
            return []
        q_tokens = _tokenize(question)
        if not q_tokens:
            return []
        scored = []
        for e in entries:
            e_tokens = set((e.get("q_norm") or "").split())
            if not e_tokens:
                continue
            jaccard = len(q_tokens & e_tokens) / len(q_tokens | e_tokens)
            if jaccard >= _MIN_SIMILARITY:
                scored.append((jaccard, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "question": e["question"],
                "sql": e["sql"],
                "table_used": e.get("table_used", ""),
                "similarity": round(s, 2),
            }
            for s, e in scored[:k]
        ]
    except Exception as e:
        print(f"[query_memory] find_similar failed (non-fatal): {e}", flush=True)
        return []
