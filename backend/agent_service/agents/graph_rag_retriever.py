"""
Graph RAG Retriever — orchestrator pipeline (main Query Chat).

Replaces _score_table() word-overlap with a four-signal retrieval:
  1. TF-IDF cosine similarity over table+column text (from enriched.tfidf_index)
  2. concept_index exact/prefix term matching
  3. entity_columns fuzzy matching (named entity → sample value → table)
  4. FK graph expansion (1-hop bonus for JOIN-connected tables)

Returns RetrievedContext with ranked TableCandidates carrying:
  - column_hints   (specific columns to SELECT/GROUP BY)
  - join_conditions (FK ON clauses, pre-verified)
  - filter_hints   (WHERE column = 'value' from entity matching)

Zero LLM calls. Runs in <5 ms against in-memory EnrichedSchema.
Called by Orchestrator.run_single_viz_pipeline() after schema fetch.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TableCandidate:
    table_name: str
    score: float
    signals: list = field(default_factory=list)    # ["tfidf", "concept", "entity", "graph"]
    column_hints: list = field(default_factory=list)
    metric_columns: list = field(default_factory=list)
    dimension_columns: list = field(default_factory=list)
    date_columns: list = field(default_factory=list)
    join_conditions: list = field(default_factory=list)


@dataclass
class FilterHint:
    table: str
    column: str
    value: str
    entity_text: str = ""


@dataclass
class RetrievedContext:
    candidates: list = field(default_factory=list)       # list[TableCandidate]
    primary_tables: list = field(default_factory=list)   # convenience: top table names
    join_paths: list = field(default_factory=list)
    filter_hints: list = field(default_factory=list)     # list[FilterHint]
    metric_hints: list = field(default_factory=list)     # "table.column"
    date_hints: list = field(default_factory=list)
    confidence: float = 0.0


# ── Tokenisation ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


def _table_text(table: dict, table_semantics: dict) -> str:
    """Combine all table metadata into a single text blob for TF-IDF."""
    tname = table.get("name", "")
    sem = table_semantics.get(tname, {})
    parts: list[str] = []

    bare = tname.split(".")[-1].replace("_", " ")
    parts += [bare, bare, bare]                            # 3× name weight

    if table.get("description"):
        parts.append(table["description"])
    if sem.get("business_name"):
        parts += [sem["business_name"], sem["business_name"]]
    if sem.get("purpose"):
        parts.append(sem["purpose"])
    for use in (sem.get("use_for") or []):
        parts.append(use)
    for col in (sem.get("key_metric_cols") or []):
        parts += [col.replace("_", " ")] * 2
    for col in (sem.get("key_dimension_cols") or []):
        parts += [col.replace("_", " ")] * 2
    for col in (sem.get("key_date_cols") or []):
        parts.append(col.replace("_", " "))

    for c in table.get("columns", []):
        cname = c.get("name", "")
        cdesc = c.get("description", "")
        stype = c.get("semantic_type", "")
        parts.append(cname.replace("_", " "))
        if cdesc:
            parts.append(cdesc[:150])
        if stype in ("metric", "dimension"):
            parts.append(cname.replace("_", " "))

    return " ".join(parts)


# ── TF-IDF helpers ────────────────────────────────────────────────────────────

def _cosine(a: dict, b: dict) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(t, 0.0) * v for t, v in b.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb + 1e-10)


def _query_vec(tokens: list, idf: dict) -> dict:
    if not tokens:
        return {}
    freq: dict = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    n = len(tokens)
    return {t: (c / n) * idf.get(t, 1.0) for t, c in freq.items()}


# ── Fuzzy entity matching ─────────────────────────────────────────────────────

def _fuzzy_match(needle: str, haystack: list) -> Optional[str]:
    if not needle or not haystack:
        return None
    for h in haystack:
        if needle == h:
            return h
    for h in haystack:
        if needle in h or h in needle:
            return h
    needle_tokens = set(needle.split())
    for h in haystack:
        h_tokens = set(h.split())
        if needle_tokens and h_tokens and len(needle_tokens & h_tokens) / len(needle_tokens) >= 0.5:
            return h
    return None


# ── Main retrieval ────────────────────────────────────────────────────────────

def retrieve(
    user_text: str,
    intent,               # ParsedIntent (from intent_parser.py) — duck-typed
    enriched: "EnrichedSchema",
    top_k: int = 5,
) -> RetrievedContext:
    """
    Multi-signal Graph RAG retrieval. Never raises — returns empty context on error.
    The orchestrator falls back to word-overlap scoring when context is empty.
    """
    try:
        return _retrieve(user_text, intent, enriched, top_k)
    except Exception as exc:
        print(f"[graph_rag] ⚠ retrieval failed (non-fatal): {exc}", flush=True)
        return RetrievedContext()


def _retrieve(
    user_text: str,
    intent,
    enriched: "EnrichedSchema",
    top_k: int,
) -> RetrievedContext:
    if not enriched or not enriched.compact_tables:
        return RetrievedContext()

    # ── Build query text ──────────────────────────────────────────────────────
    metrics = list(getattr(intent, "metrics", None) or [])
    entity_list = getattr(intent, "entities", None) or []
    entity_texts = [e.text for e in entity_list if hasattr(e, "text")]
    query_text = " ".join([user_text] + metrics + entity_texts)
    query_tokens = _tokenize(query_text)

    tnames = [t["name"] for t in enriched.compact_tables]
    table_signals: dict[str, dict] = {tn: {} for tn in tnames}

    # ── Signal 1: TF-IDF ─────────────────────────────────────────────────────
    tfidf_idx = enriched.tfidf_index or {}
    idf = tfidf_idx.get("idf") or {}
    table_vecs = tfidf_idx.get("tables") or {}

    if idf and table_vecs:
        qvec = _query_vec(query_tokens, idf)
        for tn, tdata in table_vecs.items():
            if tn in table_signals:
                sim = _cosine(qvec, tdata.get("tfidf_vec") or {})
                if sim > 0:
                    table_signals[tn]["tfidf"] = sim
    else:
        # Fallback: simple overlap when index not yet built
        qset = set(query_tokens)
        ct_sem = enriched.table_semantics or {}
        for t in enriched.compact_tables:
            tn = t["name"]
            ttoks = set(_tokenize(_table_text(t, ct_sem)))
            overlap = len(qset & ttoks) / (len(qset) + 1)
            if overlap > 0:
                table_signals[tn]["tfidf"] = min(overlap * 2.0, 1.0)

    # ── Signal 2: concept_index ───────────────────────────────────────────────
    concept_idx = enriched.concept_index or {}
    all_terms = metrics + [t for t in query_tokens if len(t) >= 3]
    for term in all_terms:
        # exact match first
        entries = concept_idx.get(term, [])
        if not entries:
            # prefix match
            for key, ents in concept_idx.items():
                if key.startswith(term) or term.startswith(key):
                    entries = ents
                    break
        for entry in entries[:3]:
            tn = entry.get("table", "")
            if tn in table_signals:
                bonus = float(entry.get("score", 0.8))
                table_signals[tn]["concept"] = max(
                    table_signals[tn].get("concept", 0.0), bonus
                )

    # ── Signal 3: entity_columns (fuzzy named-entity → sample values) ─────────
    filter_hints: list[FilterHint] = []
    entity_cols = enriched.entity_columns or {}
    for entity in entity_list:
        if not hasattr(entity, "text"):
            continue
        etype = getattr(entity, "entity_type", "unknown")
        etext = entity.text.lower()
        # try matching entity type first, then all types
        type_order = ([etype] if etype in entity_cols else []) + [
            k for k in entity_cols if k != etype
        ]
        matched = False
        for et in type_order[:4]:
            for col_info in entity_cols.get(et, []):
                tn = col_info.get("table", "")
                cname = col_info.get("column", "")
                samples = [str(s).lower() for s in (col_info.get("sample_values") or [])]
                hit = _fuzzy_match(etext, samples)
                if hit:
                    if tn in table_signals:
                        table_signals[tn]["entity"] = max(
                            table_signals[tn].get("entity", 0.0), 0.90
                        )
                    filter_hints.append(FilterHint(
                        table=tn, column=cname,
                        value=hit, entity_text=entity.text,
                    ))
                    matched = True
                    break
            if matched:
                break

    # ── Signal 4: FK graph expansion (1-hop bonus) ────────────────────────────
    rg = enriched.relationship_graph
    high_score = {
        tn for tn, sigs in table_signals.items()
        if max(sigs.values(), default=0.0) > 0.35
    }
    if rg and high_score:
        for tn in list(table_signals.keys()):
            if tn in high_score:
                continue
            for hs in high_score:
                if (rg.get_join_condition(hs, tn) or rg.get_join_condition(tn, hs)):
                    table_signals[tn]["graph"] = max(
                        table_signals[tn].get("graph", 0.0), 0.30
                    )
                    break

    # ── Composite score: TF-IDF 40 | concept 30 | entity 20 | graph 10 ────────
    W = {"tfidf": 0.40, "concept": 0.30, "entity": 0.20, "graph": 0.10}
    ranked: list[tuple] = []
    for tn, sigs in table_signals.items():
        score = sum(sigs.get(sig, 0.0) * w for sig, w in W.items())
        ranked.append((score, tn, list(sigs.keys())))

    ranked.sort(key=lambda x: x[0], reverse=True)
    top = [(s, tn, ss) for s, tn, ss in ranked[:top_k] if s > 0.01]

    if not top:
        return RetrievedContext()

    # ── Build TableCandidates ─────────────────────────────────────────────────
    ct_map = {t["name"]: t for t in enriched.compact_tables}
    sem_map = enriched.table_semantics or {}
    top_names = {tn for _, tn, _ in top}

    candidates: list[TableCandidate] = []
    for score, tn, sigs_fired in top:
        ct = ct_map.get(tn) or {}
        sem = sem_map.get(tn) or {}

        # column_hints: metric cols first, then concept-matched columns, then dims
        col_hints: list[str] = []
        for col in (sem.get("key_metric_cols") or []):
            if col not in col_hints:
                col_hints.append(col)
        for term in all_terms:
            for entry in concept_idx.get(term, []):
                if entry.get("table") == tn:
                    ccol = entry.get("column", "")
                    if ccol and ccol not in col_hints:
                        col_hints.append(ccol)
        for col in (sem.get("key_dimension_cols") or []):
            if col not in col_hints:
                col_hints.append(col)

        # JOIN conditions to other top tables
        join_conds: list[str] = []
        for _, other_tn, _ in top:
            if other_tn == tn:
                continue
            cond = (rg.get_join_condition(tn, other_tn) if rg else None) or \
                   (rg.get_join_condition(other_tn, tn) if rg else None)
            if cond and cond not in join_conds:
                join_conds.append(cond)

        candidates.append(TableCandidate(
            table_name=tn,
            score=round(score, 4),
            signals=sigs_fired,
            column_hints=col_hints[:10],
            metric_columns=list(sem.get("key_metric_cols") or []),
            dimension_columns=list(sem.get("key_dimension_cols") or []),
            date_columns=list(sem.get("key_date_cols") or []),
            join_conditions=join_conds[:5],
        ))

    primary_tables = [c.table_name for c in candidates]
    all_joins = list({cond for c in candidates for cond in c.join_conditions})
    metric_hints = [f"{c.table_name}.{col}" for c in candidates for col in c.metric_columns]
    date_hints = [f"{c.table_name}.{col}" for c in candidates for col in c.date_columns]

    confidence = top[0][0] if top else 0.0
    print(
        f"[graph_rag] tables={primary_tables[:3]}  "
        f"signals={[c.signals for c in candidates[:3]]}  "
        f"confidence={confidence:.3f}  filters={len(filter_hints)}",
        flush=True,
    )

    return RetrievedContext(
        candidates=candidates,
        primary_tables=primary_tables,
        join_paths=all_joins,
        filter_hints=filter_hints,
        metric_hints=metric_hints[:8],
        date_hints=date_hints[:4],
        confidence=confidence,
    )


# ── Prompt formatter ──────────────────────────────────────────────────────────

def format_retrieval_hints(ctx: Optional[RetrievedContext]) -> str:
    """Render a RetrievedContext as a system-prompt section for the QueryAgent LLM."""
    if not ctx or not ctx.candidates:
        return ""

    lines = [
        "GRAPH RAG RETRIEVAL HINTS — use these to write accurate SQL:",
        f"Retrieval confidence: {ctx.confidence:.0%}",
        f"Primary tables (ranked by relevance): {', '.join(ctx.primary_tables[:4])}",
    ]

    for c in ctx.candidates[:4]:
        lines.append(f"\n[{c.table_name}]  score={c.score:.2f}  signals={c.signals}")
        if c.metric_columns:
            lines.append(f"  metric columns: {', '.join(c.metric_columns[:6])}")
        if c.dimension_columns:
            lines.append(f"  dimension columns: {', '.join(c.dimension_columns[:6])}")
        if c.date_columns:
            lines.append(f"  date columns: {', '.join(c.date_columns[:3])}")
        extra = [h for h in c.column_hints
                 if h not in c.metric_columns and h not in c.dimension_columns]
        if extra:
            lines.append(f"  also consider: {', '.join(extra[:5])}")

    if ctx.join_paths:
        lines.append("\nJOIN conditions (verified FK relationships):")
        for jp in ctx.join_paths[:5]:
            lines.append(f"  {jp}")

    if ctx.filter_hints:
        lines.append("\nWHERE clause hints (entity matches from user query):")
        for fh in ctx.filter_hints[:4]:
            lines.append(f"  {fh.table}.{fh.column} = '{fh.value}'  ('{fh.entity_text}')")

    return "\n".join(lines)
