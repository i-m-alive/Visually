"""
Stage 2 of the NL2SQL two-stage pipeline: NL Schema Router.

Given a ParsedIntent (from intent_parser.py) and an EnrichedSchema (from
schema_cache.py), resolves:

  1. Table relevance   — which tables are most likely needed, via concept-index
                         lookup + TF-IDF + semantic-type scoring
  2. Entity resolution — maps named entities ("Acme Corp") to the best-matching
                         table.column using the entity_columns index + fuzzy
                         sample-value matching
  3. JOIN path         — BFS over the FK adjacency graph to find the minimal set
                         of JOIN conditions that connect the resolved tables
  4. Metric columns    — maps business measure terms to specific table.columns

Returns a ResolvedContext that chat_agent.py injects into the system prompt as
a focused "QUERY ROUTING HINTS" block — a fast path that lets the SQL model
pick the right tables without reading the entire schema.

All logic here is pure Python (no LLM calls, no I/O).  The only async wrapper
is the public `route_query()` entry point, which just calls the sync internals
in a thread-executor-free way (they're fast enough to run inline).
"""
import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema
    from agent_service.agents.intent_parser import ParsedIntent


# ── Result data classes ───────────────────────────────────────────────────────

@dataclass
class EntityResolution:
    original_text: str        # "Acme Corp"
    entity_type: str          # "company"
    resolved_table: str       # "staging.client_corporation"
    resolved_column: str      # "parentname"
    match_confidence: float   # 0.0–1.0
    filter_snippet: str       # "parentname ILIKE '%Acme Corp%'"


@dataclass
class MetricColumn:
    concept: str              # "revenue"
    table: str                # "staging.billing_timesheetentry"
    column: str               # "billedgeneralrate"
    score: float


@dataclass
class ResolvedContext:
    intent_type: str
    relevant_tables: list = field(default_factory=list)       # list[str], ordered by score
    focused_tables: list = field(default_factory=list)        # top-5 tables for focused mode
    entity_resolutions: list = field(default_factory=list)    # list[EntityResolution]
    metric_columns: list = field(default_factory=list)        # list[MetricColumn]
    join_conditions: list = field(default_factory=list)       # list[str]  "a.id = b.a_id"
    chart_hint: Optional[str] = None
    routing_notes: list = field(default_factory=list)         # list[str]  human-readable
    fallback: bool = False    # True when routing had no useful signal


# ── Public entry point ────────────────────────────────────────────────────────

def route_query(
    intent: "ParsedIntent",
    enriched: "EnrichedSchema",
    original_message: str,
    top_k: int = 8,
) -> ResolvedContext:
    """
    Route a parsed intent to specific schema elements.

    Returns ResolvedContext with relevant_tables, entity_resolutions,
    metric_columns, and join_conditions.  Always succeeds — returns a minimal
    fallback context on any error so the pipeline is never interrupted.
    """
    try:
        return _route(intent, enriched, original_message, top_k)
    except Exception as exc:
        print(f"[nl_schema_router] ⚠ routing failed ({exc!r}) — using fallback", flush=True)
        return ResolvedContext(
            intent_type=intent.intent_type,
            chart_hint=intent.chart_hint,
            fallback=True,
            routing_notes=[f"Routing failed: {exc!r}"],
        )


# ── Main routing logic ────────────────────────────────────────────────────────

def _route(intent: "ParsedIntent", enriched: "EnrichedSchema", message: str, top_k: int) -> ResolvedContext:
    notes: list[str] = []

    # ── 1. Score tables ───────────────────────────────────────────────────────
    scored = _score_tables(intent, enriched, message)
    # scored: list[(table_name, float)]  ordered descending

    relevant_tables = [t for t, _ in scored[:top_k]]
    focused_tables  = [t for t, _ in scored[:5]]

    if relevant_tables:
        notes.append(
            f"Relevant tables (top {len(relevant_tables)}): "
            + ", ".join(relevant_tables[:5])
            + ("…" if len(relevant_tables) > 5 else "")
        )

    # ── 2. Entity resolution ──────────────────────────────────────────────────
    entity_resolutions: list[EntityResolution] = []
    if intent.entities and hasattr(enriched, "entity_columns") and enriched.entity_columns:
        for ent in intent.entities:
            res = _resolve_entity(ent, enriched)
            if res:
                entity_resolutions.append(res)
                # Add the resolved table to the relevant set if not already there
                if res.resolved_table not in relevant_tables:
                    relevant_tables.insert(0, res.resolved_table)
                    focused_tables.insert(0, res.resolved_table)
                notes.append(
                    f'Entity "{ent.text}" → {res.resolved_table}.{res.resolved_column}'
                    f" (confidence={res.match_confidence:.2f})"
                )

    # ── 3. Metric columns ─────────────────────────────────────────────────────
    metric_columns: list[MetricColumn] = []
    if intent.metrics and hasattr(enriched, "concept_index") and enriched.concept_index:
        for metric_term in intent.metrics:
            hits = _lookup_concept(metric_term, enriched.concept_index, max_hits=3)
            for hit in hits:
                mc = MetricColumn(
                    concept=metric_term,
                    table=hit["table"],
                    column=hit["column"],
                    score=hit["score"],
                )
                metric_columns.append(mc)
                if hit["table"] not in relevant_tables:
                    relevant_tables.append(hit["table"])
                if hit["table"] not in focused_tables:
                    focused_tables.append(hit["table"])
        if metric_columns:
            notes.append(
                "Metric columns: "
                + "; ".join(f'"{m.concept}" → {m.table}.{m.column}' for m in metric_columns[:4])
            )

    # ── 4. Fallback: if no tables resolved, use TF-IDF on raw message ─────────
    if not relevant_tables:
        tfidf = _tfidf_table_scores(message, enriched)
        relevant_tables = [t for t, _ in tfidf[:top_k]]
        focused_tables  = [t for t, _ in tfidf[:5]]
        if relevant_tables:
            notes.append(f"TF-IDF fallback tables: {', '.join(relevant_tables[:3])}")

    # ── 5. JOIN path ──────────────────────────────────────────────────────────
    join_conditions: list[str] = []
    if len(focused_tables) >= 2:
        join_conditions = _find_join_conditions(focused_tables, enriched)
        if join_conditions:
            notes.append(f"JOIN conditions ({len(join_conditions)} found)")

    # ── 6. Chart hint ─────────────────────────────────────────────────────────
    chart_hint = intent.chart_hint
    # Override chart hint based on metric column count
    if not chart_hint:
        if len(metric_columns) >= 2 or len(entity_resolutions) >= 1:
            chart_hint = "multi_row_card" if len(entity_resolutions) >= 1 else "bar_vertical"
    if intent.intent_type == "trend":
        chart_hint = "line"
    elif intent.intent_type == "rank":
        chart_hint = "bar_vertical"
    elif intent.intent_type == "count" and not entity_resolutions:
        chart_hint = "kpi"

    fallback = not relevant_tables and not entity_resolutions and not metric_columns

    print(
        f"[nl_schema_router] routed  intent={intent.intent_type}"
        f"  tables={len(relevant_tables)}  entities={len(entity_resolutions)}"
        f"  metrics={len(metric_columns)}  joins={len(join_conditions)}"
        f"  chart_hint={chart_hint}  fallback={fallback}",
        flush=True,
    )

    return ResolvedContext(
        intent_type=intent.intent_type,
        relevant_tables=relevant_tables,
        focused_tables=focused_tables[:5],
        entity_resolutions=entity_resolutions,
        metric_columns=metric_columns,
        join_conditions=join_conditions,
        chart_hint=chart_hint,
        routing_notes=notes,
        fallback=fallback,
    )


# ── Table scoring ─────────────────────────────────────────────────────────────

def _score_tables(
    intent: "ParsedIntent",
    enriched: "EnrichedSchema",
    message: str,
) -> list[tuple[str, float]]:
    """
    Multi-signal table scoring.  Combines:
      A. Concept-index lookup (highest weight — direct business-term match)
      B. Semantic-type match (key_metric_cols / key_dimension_cols)
      C. TF-IDF on table names + descriptions
      D. Time-filter signal (tables with key_date_cols boosted)
    Returns sorted list of (table_name, score) descending.
    """
    scores: dict[str, float] = {}

    compact_by_name = {t.get("name", ""): t for t in (enriched.compact_tables or []) if t.get("name")}

    # A. Concept-index — highest signal
    concept_index = getattr(enriched, "concept_index", {}) or {}
    all_query_terms = set()
    for m in intent.metrics:
        all_query_terms.update(_tokenize(m))
    for g in intent.group_by:
        all_query_terms.update(_tokenize(g))
    all_query_terms.update(_tokenize(message)[:20])  # cap to avoid noise

    for term in all_query_terms:
        if len(term) < 3:
            continue
        for concept_key, hits in concept_index.items():
            if term in _tokenize(concept_key) or concept_key.startswith(term):
                for hit in hits:
                    tname = hit.get("table", "")
                    scores[tname] = scores.get(tname, 0.0) + hit.get("score", 0.5) * 2.0

    # B. Semantic-type: tables whose key_metric/dimension cols overlap with intent metrics/group_by
    metric_words = {w for m in intent.metrics for w in _tokenize(m) if len(w) > 3}
    group_words  = {w for g in intent.group_by for w in _tokenize(g) if len(w) > 3}

    for tname, sem in enriched.table_semantics.items():
        metric_cols = [c.lower() for c in (sem.get("key_metric_cols") or [])]
        dim_cols    = [c.lower() for c in (sem.get("key_dimension_cols") or [])]
        date_cols   = sem.get("key_date_cols") or []

        for col in metric_cols:
            col_words = set(_tokenize(col))
            overlap = len(metric_words & col_words)
            if overlap:
                scores[tname] = scores.get(tname, 0.0) + overlap * 1.5

        for col in dim_cols:
            col_words = set(_tokenize(col))
            overlap = len(group_words & col_words)
            if overlap:
                scores[tname] = scores.get(tname, 0.0) + overlap * 1.2

        # Time-filter boost
        if intent.time_filter and date_cols:
            scores[tname] = scores.get(tname, 0.0) + 0.5

        # Fact-table boost for metric queries
        if intent.intent_type in ("metric_lookup", "trend", "rank", "count"):
            if sem.get("is_fact_table"):
                scores[tname] = scores.get(tname, 0.0) + 0.8

    # C. TF-IDF on table names + descriptions
    tfidf_scores = dict(_tfidf_table_scores(message, enriched))
    for tname, tscore in tfidf_scores.items():
        scores[tname] = scores.get(tname, 0.0) + tscore * 0.8

    # D. Entity-type affinity: boost tables whose name suggests the entity type
    entity_columns = getattr(enriched, "entity_columns", {}) or {}
    for ent in intent.entities:
        for ec in entity_columns.get(ent.entity_type, []):
            tname = ec.get("table", "")
            scores[tname] = scores.get(tname, 0.0) + 1.5  # strong signal

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def _tfidf_table_scores(message: str, enriched: "EnrichedSchema") -> list[tuple[str, float]]:
    """Simple TF-IDF scoring of tables against the user message."""
    query_tokens = set(t for t in _tokenize(message) if len(t) > 2)
    if not query_tokens:
        return []
    results: list[tuple[str, float]] = []
    for t in (enriched.compact_tables or []):
        tname = t.get("name", "")
        if not tname:
            continue
        target_tokens: set[str] = set()
        for src in [tname, t.get("description") or "", " ".join(t.get("all_column_names") or [])]:
            target_tokens.update(tok for tok in _tokenize(src) if len(tok) > 2)
        # Add semantic words from table_semantics
        sem = enriched.table_semantics.get(tname, {})
        for col_list_key in ("key_metric_cols", "key_dimension_cols", "key_date_cols"):
            for col in (sem.get(col_list_key) or []):
                target_tokens.update(tok for tok in _tokenize(col) if len(tok) > 2)
        if target_tokens:
            overlap = len(query_tokens & target_tokens)
            if overlap:
                results.append((tname, overlap / len(query_tokens)))
    return sorted(results, key=lambda x: x[1], reverse=True)


# ── Entity resolution ─────────────────────────────────────────────────────────

def _resolve_entity(ent: "ParsedEntity", enriched: "EnrichedSchema") -> Optional[EntityResolution]:
    """
    Resolve a named entity to a table.column.

    Strategy:
      1. Look in entity_columns index for the entity type
      2. For each candidate column, check if the entity text fuzzy-matches any
         known sample values (trigram-style: shared token overlap)
      3. Return the best match by confidence
    """
    entity_columns = getattr(enriched, "entity_columns", {}) or {}
    candidates = entity_columns.get(ent.entity_type, [])

    # Also check "unknown" entity type candidates (catch-all)
    if not candidates:
        candidates = entity_columns.get("unknown", [])

    # If still nothing, try heuristic: scan all columns named *name* or *title*
    if not candidates:
        candidates = _find_name_columns_fallback(enriched)

    if not candidates:
        return None

    best_confidence = 0.0
    best_candidate = None
    entity_tokens = set(_tokenize(ent.text))

    for cand in candidates:
        tname   = cand.get("table", "")
        colname = cand.get("column", "")
        sample_values = cand.get("sample_values") or []

        # Base confidence from is_primary flag
        base_conf = 0.7 if cand.get("is_primary") else 0.4

        # Boost if sample values fuzzy-match the entity text
        fuzzy_conf = _fuzzy_match_score(ent.text, entity_tokens, sample_values)
        confidence = base_conf + fuzzy_conf * 0.3

        if confidence > best_confidence:
            best_confidence = confidence
            best_candidate = (tname, colname)

    if not best_candidate or best_confidence < 0.3:
        return None

    tname, colname = best_candidate
    db_type = (enriched.db_type or "").lower()
    if db_type in ("mysql",):
        filter_snippet = f"LOWER({colname}) LIKE LOWER('%{_escape_sql(ent.text)}%')"
    else:
        filter_snippet = f"{colname} ILIKE '%{_escape_sql(ent.text)}%'"

    return EntityResolution(
        original_text=ent.text,
        entity_type=ent.entity_type,
        resolved_table=tname,
        resolved_column=colname,
        match_confidence=min(best_confidence, 1.0),
        filter_snippet=filter_snippet,
    )


def _find_name_columns_fallback(enriched: "EnrichedSchema") -> list[dict]:
    """Heuristic fallback: find columns whose name contains 'name' or 'title'."""
    results: list[dict] = []
    for t in (enriched.compact_tables or []):
        tname = t.get("name", "")
        for col in (t.get("columns") or []):
            cname = (col.get("name") or "").lower()
            ctype = (col.get("type") or "").lower()
            if any(kw in cname for kw in ("name", "title", "label")) and \
               any(ct in ctype for ct in ("char", "text", "string", "varchar")):
                results.append({
                    "table": tname, "column": col.get("name", ""),
                    "is_primary": "name" in cname, "sample_values": [],
                })
    return results


def _fuzzy_match_score(text: str, text_tokens: set, sample_values: list) -> float:
    """Compute overlap score between entity text and a list of sample values."""
    if not sample_values or not text_tokens:
        return 0.0
    best = 0.0
    for val in sample_values[:30]:
        val_str = str(val).lower()
        # Exact substring match
        if text.lower() in val_str or val_str in text.lower():
            return 1.0
        val_tokens = set(_tokenize(val_str))
        if not val_tokens:
            continue
        overlap = len(text_tokens & val_tokens) / max(len(text_tokens), len(val_tokens))
        best = max(best, overlap)
    return best


# ── Concept-index lookup ──────────────────────────────────────────────────────

def _lookup_concept(
    term: str,
    concept_index: dict,
    max_hits: int = 3,
) -> list[dict]:
    """
    Look up a business term in the concept index.
    Supports exact match, prefix match, and token-overlap match.
    Returns list of {table, column, score} dicts, sorted by score desc.
    """
    term_lower = term.lower().strip()
    term_tokens = set(_tokenize(term_lower))
    hits: list[dict] = []

    for key, entries in concept_index.items():
        key_lower = key.lower()
        key_tokens = set(_tokenize(key_lower))

        # Exact match
        if key_lower == term_lower:
            score_mult = 1.0
        # Term is prefix of key or key is prefix of term
        elif key_lower.startswith(term_lower) or term_lower.startswith(key_lower):
            score_mult = 0.85
        # Token overlap
        elif term_tokens and key_tokens:
            overlap = len(term_tokens & key_tokens) / max(len(term_tokens), len(key_tokens))
            if overlap < 0.5:
                continue
            score_mult = overlap * 0.7
        else:
            continue

        for entry in entries:
            hits.append({
                "table":  entry.get("table", ""),
                "column": entry.get("column", ""),
                "score":  entry.get("score", 0.5) * score_mult,
                "concept": key,
            })

    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:max_hits]


# ── JOIN path finder ──────────────────────────────────────────────────────────

def _find_join_conditions(tables: list[str], enriched: "EnrichedSchema") -> list[str]:
    """
    Find JOIN conditions that connect the given tables using the FK graph.

    Uses a greedy Steiner-tree approximation: start from the first table,
    find the shortest path to each subsequent table, accumulate edges.
    Returns deduplicated list of JOIN condition strings.
    """
    if len(tables) < 2:
        return []

    edges = enriched.relationship_graph.edges or {}
    conditions: list[str] = []
    seen_edges: set = set()
    connected: set = {tables[0]}

    for target in tables[1:]:
        if target in connected:
            continue
        path_edges = _bfs_path(connected, target, edges, max_hops=4)
        for edge_key, cond in path_edges:
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                conditions.append(cond)
                # Add intermediate nodes to connected set
                a, b = list(edge_key)
                connected.add(a)
                connected.add(b)

    return conditions


def _bfs_path(
    sources: set[str],
    target: str,
    edges: dict,
    max_hops: int,
) -> list[tuple[frozenset, str]]:
    """
    BFS from any source node to target.  Returns the list of (edge_key, condition)
    pairs along the shortest path.
    """
    # Track: node → (path_so_far_as_edge_list)
    queue: list[tuple[str, list]] = [(s, []) for s in sources]
    visited: set = set(sources)

    for _ in range(max_hops):
        next_queue: list[tuple[str, list]] = []
        for node, path in queue:
            for neighbor, condition in (edges.get(node) or {}).items():
                if neighbor == target:
                    edge_key = frozenset([node, neighbor])
                    return path + [(edge_key, condition)]
                if neighbor not in visited:
                    visited.add(neighbor)
                    edge_key = frozenset([node, neighbor])
                    next_queue.append((neighbor, path + [(edge_key, condition)]))
        queue = next_queue
        if not queue:
            break

    # Direct edge fallback
    for src in sources:
        cond = (edges.get(src) or {}).get(target)
        if cond:
            return [(frozenset([src, target]), cond)]

    return []


# ── Utility ───────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")

def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumeric, return tokens ≥2 chars."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2]


def _escape_sql(s: str) -> str:
    """Minimal SQL string escape — replace ' with '' (standard SQL)."""
    return s.replace("'", "''")


# ── Public formatter ──────────────────────────────────────────────────────────

def format_routing_hints(resolved: ResolvedContext) -> str:
    """
    Render a ResolvedContext as a compact system-prompt block.
    This is what gets injected into the LLM's system prompt as Zone 2.5.
    """
    if resolved.fallback and not resolved.entity_resolutions and not resolved.metric_columns:
        return ""

    lines = [
        "══════════════════════════════════════════════════════════════════════",
        "QUERY ROUTING HINTS  (pre-resolved — use these as your starting point)",
        "══════════════════════════════════════════════════════════════════════",
    ]

    lines.append(f"Intent: {resolved.intent_type.replace('_', ' ')}")

    if resolved.entity_resolutions:
        lines.append("\nEntity Resolution:")
        for er in resolved.entity_resolutions:
            lines.append(
                f'  • "{er.original_text}" ({er.entity_type})'
                f" → {er.resolved_table}.{er.resolved_column}"
                f"  [{er.filter_snippet}]"
                f"  (confidence {er.match_confidence:.0%})"
            )

    if resolved.metric_columns:
        # De-duplicate by (table, column) keeping highest score
        seen: dict[tuple, MetricColumn] = {}
        for mc in resolved.metric_columns:
            k = (mc.table, mc.column)
            if k not in seen or mc.score > seen[k].score:
                seen[k] = mc
        lines.append("\nMetric Columns:")
        for mc in sorted(seen.values(), key=lambda x: x.score, reverse=True)[:6]:
            lines.append(f'  • "{mc.concept}" → {mc.table}.{mc.column}')

    if resolved.focused_tables:
        lines.append(f"\nFocused Tables (most relevant to this query):")
        lines.append("  " + ", ".join(resolved.focused_tables))

    if resolved.join_conditions:
        lines.append("\nSuggested JOIN conditions:")
        for cond in resolved.join_conditions[:6]:
            lines.append(f"  {cond}")

    if resolved.chart_hint:
        lines.append(f"\nSuggested chart type: {resolved.chart_hint}")

    lines.append(
        "\nNOTE: These hints are pre-computed — verify against the full schema below."
        " If the routing looks wrong, fall back to the schema."
    )
    lines.append("══════════════════════════════════════════════════════════════════════")
    return "\n".join(lines)
