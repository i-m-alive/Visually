"""
Metric Registry — per-connection canonical metric definitions (mini semantic layer).

A metric definition pins a business term ("placements", "revenue") to an exact
table + aggregation expression + date column, so the SQL generator uses the
definition verbatim instead of re-deriving it on every question. This is the
single biggest lever against "same question, different answer" drift.

Storage: filesystem JSON per connection (survives restarts, no DB migration
needed), mirroring the schema_cache L3 pattern.

Definition shape:
{
  "name": "placements",
  "synonyms": ["placement", "placed candidates"],
  "table": "staging.bullhorn_core_placement",
  "expression": "COUNT(*)",
  "date_column": "date_added",
  "filter": "status = 'Approved'",          # optional WHERE fragment
  "description": "A candidate placed into a job"
}
"""
import json
import os
import pathlib
import re
from typing import Optional


def _registry_dir() -> pathlib.Path:
    return pathlib.Path(
        os.getenv(
            "METRIC_REGISTRY_DIR",
            str(pathlib.Path(__file__).parent.parent.parent / ".metric_registry"),
        )
    )


def _registry_path(connection_id: str) -> pathlib.Path:
    safe = re.sub(r"[^\w-]", "_", connection_id)
    return _registry_dir() / f"metrics_{safe}.json"


_REQUIRED_FIELDS = ("name", "table", "expression")


def get_metrics(connection_id: str) -> dict:
    """Load all metric definitions for a connection: {name_lower: definition}."""
    path = _registry_path(connection_id)
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[metric_registry] read failed (non-fatal): {e}", flush=True)
    return {}


def save_metric(connection_id: str, definition: dict) -> dict:
    """Insert or update one metric definition. Returns the stored registry."""
    for f in _REQUIRED_FIELDS:
        if not definition.get(f):
            raise ValueError(f"Metric definition missing required field '{f}'")
    metrics = get_metrics(connection_id)
    key = definition["name"].strip().lower()
    metrics[key] = {
        "name": definition["name"].strip(),
        "synonyms": [s.strip() for s in (definition.get("synonyms") or []) if s and s.strip()],
        "table": definition["table"].strip(),
        "expression": definition["expression"].strip(),
        "date_column": (definition.get("date_column") or "").strip() or None,
        "filter": (definition.get("filter") or "").strip() or None,
        "description": (definition.get("description") or "").strip() or None,
    }
    d = _registry_dir()
    d.mkdir(parents=True, exist_ok=True)
    _registry_path(connection_id).write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    return metrics


def delete_metric(connection_id: str, name: str) -> bool:
    """Remove a metric definition. Returns True when it existed."""
    metrics = get_metrics(connection_id)
    key = name.strip().lower()
    if key not in metrics:
        return False
    del metrics[key]
    _registry_path(connection_id).write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    return True


def _stem(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def match_metrics(user_text: str, connection_id: str) -> list:
    """
    Return the metric definitions whose name or synonyms appear in the user's
    question (stem-normalized). Called by the orchestrator before SQL gen.
    """
    metrics = get_metrics(connection_id)
    if not metrics:
        return []
    text_stems = {_stem(t) for t in re.findall(r"[a-z0-9]+", user_text.lower())}
    matched = []
    for defn in metrics.values():
        terms = [defn["name"]] + (defn.get("synonyms") or [])
        for term in terms:
            term_stems = {_stem(t) for t in re.findall(r"[a-z0-9]+", term.lower())}
            # every word of the metric term must appear in the question
            if term_stems and term_stems <= text_stems:
                matched.append(defn)
                break
    return matched
