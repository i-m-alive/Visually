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
    is_view: bool = False


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
    needs_join: bool = False
    join_path: list = field(default_factory=list)   # list of (from, to, condition) hops


# ── Tokenisation ──────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower())


# English stop words + query filler that must never drive concept lookups.
# Without this, tokens like "how"/"and"/"were" match description-mined concept
# entries and boost random tables (observed: "were" → bullhorn_timesheet2).
STOPWORDS = frozenset({
    "a", "an", "and", "any", "are", "all", "also", "as", "at", "be", "been",
    "but", "by", "can", "did", "do", "does", "each", "for", "from", "get",
    "give", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it",
    "its", "last", "many", "me", "much", "my", "no", "not", "now", "of", "on",
    "or", "our", "out", "per", "please", "show", "so", "some", "tell", "than",
    "that", "the", "their", "them", "then", "there", "these", "they", "this",
    "those", "to", "us", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
    # time-range filler — handled by intent time_range extraction, not retrieval
    "day", "days", "week", "weeks", "month", "months", "year", "years",
    "today", "yesterday", "seven", "thirty", "individually", "wise",
})

# Tables that are backups / temp copies / test scratch — they duplicate the
# columns of their production twin and must never outrank it.
_JUNK_TABLE_PAT = re.compile(
    r"(_temp$|_tmp$|_test$|_bkp$|_backup$|_old$|_copy$|_temporary_table$"
    r"|_bkp_|_test_|temporary)",
    re.IGNORECASE,
)
JUNK_TABLE_PENALTY = 0.4  # multiplier applied to the final composite score

# A single-aggregate ask ("how many candidates", "total revenue") vs. a
# grouped/breakdown ask ("revenue by region") — used to boost tables with a
# column flagged is_kpi_metric when the query looks like the former.
_KPI_QUERY_PAT = re.compile(
    r"\b(how many|how much|what is the|what's the|total|average|overall|"
    r"sum of|count of)\b", re.IGNORECASE,
)
_GROUPING_QUERY_PAT = re.compile(
    r"\b(by |per |each |breakdown|group by|wise)\b", re.IGNORECASE,
)


def _is_junk_table(table_name: str) -> bool:
    bare = table_name.split(".")[-1]
    return bool(_JUNK_TABLE_PAT.search(bare))


def _stem(token: str) -> str:
    """Crude plural stemmer so 'candidates' matches 'candidate', 'applications'
    matches 'application'. Good enough for table/column name matching."""
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


# Source-system / layer prefixes that carry no business meaning — dropped
# before computing name-match fraction so 'bqp_applications' scores as
# 'applications' (full match), not half a match.
_NAME_NOISE_TOKENS = frozenset({
    "bqp", "stg", "staging", "target", "bullhorn", "classic", "core",
    "quickbooks", "dim", "fact", "tbl", "raw", "src", "vw",
})


def _stem_variants(token: str) -> set:
    """All plausible stems of a query token, bridging verb/noun forms:
    'applied' → {'applied', 'appli'} so it can prefix-match 'application'."""
    out = {token, _stem(token)}
    if len(token) > 5 and token.endswith("ied"):
        out.add(token[:-3] + "y")
    if len(token) > 4 and token.endswith("ed"):
        out.add(token[:-2])
    if len(token) > 5 and token.endswith("ing"):
        out.add(token[:-3])
    return out


def _stems_match(name_stem: str, query_stems: set) -> bool:
    if name_stem in query_stems:
        return True
    # prefix bridge (≥5 chars): 'appli(ed)' ↔ 'application'
    for q in query_stems:
        if len(q) >= 5 and (name_stem.startswith(q) or q.startswith(name_stem)):
            return True
    return False


def _name_match_score(query_stems: set, table_name: str) -> float:
    """Fraction of the table's meaningful name tokens that appear in the query.
    A table NAMED 'applications' is much stronger evidence than a satellite
    table that merely carries an application_id FK column."""
    bare = table_name.split(".")[-1].lower()
    tokens = [t for t in re.split(r"[_\W]+", bare) if len(t) >= 3]
    meaningful = [t for t in tokens if t not in _NAME_NOISE_TOKENS] or tokens
    if not meaningful:
        return 0.0
    matched = sum(1 for t in meaningful if _stems_match(_stem(t), query_stems))
    return matched / len(meaningful)


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


def _cosine_list(a: list, b: list) -> float:
    """Cosine similarity between two dense float vectors. Returns 0.0 on any
    length/emptiness mismatch. Clamped to [0, 1] since negative similarity is
    not a useful retrieval signal here."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0; na = 0.0; nb = 0.0
    for x, y in zip(a, b):
        dot += x * y; na += x * x; nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    sim = dot / (math.sqrt(na) * math.sqrt(nb) + 1e-10)
    return sim if sim > 0.0 else 0.0


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
    history_tables: Optional[list] = None,
    domain: str = "",
    query_embedding: Optional[list] = None,
) -> RetrievedContext:
    """
    Multi-signal Graph RAG retrieval. Never raises — returns empty context on error.
    The orchestrator falls back to word-overlap scoring when context is empty.

    history_tables: tables used in recent conversation turns (see
    sql_utils.extract_recent_tables). When the current message's own
    retrieval signal is weak — typical of elliptical follow-ups like
    "what about last month" — these are kept in the candidate set so their
    schema still reaches the LLM instead of being silently dropped.

    domain: when "finance", the query is expanded with finance jargon synonyms
    (see finance_glossary) so terms like "AUM"/"delinquency"/"YoY" match the
    plain-english wording in auto-generated metadata.

    query_embedding: optional pre-computed embedding of the user's question. When
    provided AND the enriched schema carries per-table embeddings, cosine
    similarity becomes an additional retrieval signal (catches paraphrases the
    lexical signals miss). Absent/empty → pure lexical scoring, exactly as before.
    """
    try:
        return _retrieve(user_text, intent, enriched, top_k, history_tables, domain, query_embedding)
    except Exception as exc:
        print(f"[graph_rag] ⚠ retrieval failed (non-fatal): {exc}", flush=True)
        return RetrievedContext()


def _retrieve(
    user_text: str,
    intent,
    enriched: "EnrichedSchema",
    top_k: int,
    history_tables: Optional[list] = None,
    domain: str = "",
    query_embedding: Optional[list] = None,
) -> RetrievedContext:
    if not enriched or not enriched.compact_tables:
        return RetrievedContext()

    # ── Build query text ──────────────────────────────────────────────────────
    metrics = list(getattr(intent, "metrics", None) or [])
    entity_list = getattr(intent, "entities", None) or []
    entity_texts = [e.text for e in entity_list if hasattr(e, "text")]
    # Finance jargon expansion — additive tokens only, never replaces user words.
    glossary_tokens: list[str] = []
    if (domain or "").lower() == "finance":
        try:
            from agent_service.agents.finance_glossary import expand_finance_terms
            glossary_tokens = expand_finance_terms(user_text)
            if glossary_tokens:
                print(
                    f"[graph_rag] finance glossary expanded query with "
                    f"{len(glossary_tokens)} synonym token(s)",
                    flush=True,
                )
        except Exception as _ge:
            print(f"[graph_rag] glossary expansion failed (non-fatal): {_ge}", flush=True)
    query_text = " ".join([user_text] + metrics + entity_texts + glossary_tokens)
    query_tokens = _tokenize(query_text)

    tnames = [t["name"] for t in enriched.compact_tables]
    table_signals: dict[str, dict] = {tn: {} for tn in tnames}
    ct_map = {t["name"]: t for t in enriched.compact_tables}

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
    all_terms = metrics + [
        t for t in query_tokens if len(t) >= 3 and t not in STOPWORDS
    ]
    for term in all_terms:
        term_stem = _stem(term)
        # exact match on the term AND its stem ('candidates' must also consult
        # the 'candidate' concept — the plural key often holds only junk)
        entries = list(concept_idx.get(term, []))
        if term_stem != term:
            entries += concept_idx.get(term_stem, [])
        if not entries:
            # prefix match (min 4 chars — short prefixes match too loosely)
            if len(term) >= 4:
                for key, ents in concept_idx.items():
                    if key.startswith(term) or term.startswith(key):
                        entries = ents
                        break
        # Dedupe by table and boost up to 5 DISTINCT tables per concept.
        # The old entries[:3] let one table occupy all slots (duplicate
        # column entries) and starved the real production table of its boost.
        # Prefer entries whose TABLE NAME contains the term (hub tables like
        # bqp_applications) over tables that merely carry a matching FK column
        # (ml_score_temp.application_id); among in-name tables prefer the
        # SHORTEST name — the hub is 'applications', the satellites are
        # 'application_areas_of_expertise'.
        def _entry_rank(e: dict) -> tuple:
            tn_bare = (e.get("table", "").split(".")[-1]).lower()
            in_name = term_stem in tn_bare or term in tn_bare
            return (
                0 if in_name else 1,
                1 if _is_junk_table(e.get("table", "")) else 0,
                len(tn_bare) if in_name else 0,
                -float(e.get("score", 0.8)),
            )

        seen_tables: set = set()
        for entry in sorted(entries, key=_entry_rank):
            tn = entry.get("table", "")
            if not tn or tn in seen_tables:
                continue
            seen_tables.add(tn)
            if tn in table_signals:
                bonus = float(entry.get("score", 0.8))
                # A match on a bare id/FK column ("application_id" on an ML
                # scoring table) says the table REFERENCES the concept, not
                # that it's ABOUT it — halve the boost.
                col = (entry.get("column") or "").lower()
                if col.endswith("id") or col.endswith("_id"):
                    bonus *= 0.5
                table_signals[tn]["concept"] = max(
                    table_signals[tn].get("concept", 0.0), bonus
                )
            if len(seen_tables) >= 5:
                break

    # ── Signal 2b: table-name token match ─────────────────────────────────────
    # 'candidates' should surface bqp_candidate / bullhorn_core_candidate even
    # when the concept index is polluted by FK-column matches on other tables.
    query_stems: set = set()
    for t in query_tokens:
        if len(t) >= 3 and t not in STOPWORDS:
            query_stems |= _stem_variants(t)
    if query_stems:
        for tn in tnames:
            ns = _name_match_score(query_stems, tn)
            if ns > 0:
                table_signals[tn]["name"] = ns

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

    # ── 2-hop FK expansion (extend 1-hop to 2-hop) ────────────────────────────
    high_score_2hop = {
        tn for tn, sigs in table_signals.items()
        if max(sigs.values(), default=0.0) > 0.35
    }
    if rg and high_score_2hop:
        for tn in list(table_signals.keys()):
            if tn in high_score_2hop:
                continue
            for hs in high_score_2hop:
                # 1-hop already handled above; check 2-hop via intermediate
                for intermediate, _ in (rg.edges.get(hs) or {}).items():
                    if intermediate == tn:
                        continue
                    if rg.get_join_condition(intermediate, tn) or rg.get_join_condition(tn, intermediate):
                        table_signals[tn]["graph"] = max(
                            table_signals[tn].get("graph", 0.0), 0.20
                        )
                        break

    # ── Signal 6: semantic embedding cosine ──────────────────────────────────
    # Titan vectors are L2-normalized, so cosine == dot product; _cosine_list
    # still divides by norms defensively in case a non-normalized model is used.
    embed_present = False
    table_embs = getattr(enriched, "table_embeddings", None) or {}
    if query_embedding and table_embs:
        for tn in table_signals:
            vec = table_embs.get(tn)
            if vec:
                cos = _cosine_list(query_embedding, vec)
                if cos > 0:
                    table_signals[tn]["embed"] = cos
                    embed_present = True

    # ── Composite ─────────────────────────────────────────────────────────────
    # When an embedding signal is available for this query, rebalance the weights
    # to give it a real vote while keeping the total at 1.0 (so absolute score
    # thresholds downstream — clarify < 0.12, history boost < 0.20 — stay valid).
    # When no embedding signal exists, use the original lexical-only weights
    # unchanged, so behavior is byte-for-byte identical to before this feature.
    if embed_present:
        W = {"tfidf": 0.28, "embed": 0.22, "name": 0.20, "concept": 0.18, "entity": 0.10, "graph": 0.02}
    else:
        W = {"tfidf": 0.35, "name": 0.25, "concept": 0.20, "entity": 0.15, "graph": 0.05}
    ranked: list[tuple] = []
    for tn, sigs in table_signals.items():
        # A concept hit with near-zero TF-IDF means the table merely shares a
        # column name (e.g. application_id on an ML scoring table) but its
        # name/description have nothing to do with the question — halve it.
        adj = dict(sigs)
        if adj.get("concept", 0.0) > 0 and adj.get("tfidf", 0.0) < 0.01:
            adj["concept"] = adj["concept"] * 0.5
        score = sum(adj.get(sig, 0.0) * w for sig, w in W.items())
        # Backup/temp/test copies must never outrank their production twin.
        if _is_junk_table(tn):
            score *= JUNK_TABLE_PENALTY
        ranked.append((score, tn, list(sigs.keys())))

    ranked.sort(key=lambda x: x[0], reverse=True)

    # ── LLM semantic-metadata signals — never_use_for / is_fact_table / is_kpi_metric ──
    # These are generated by metadata_extractor.py and were sitting in table_semantics/
    # compact_tables entirely unused by retrieval until now (see metadata gap-analysis).
    sem_map_pre = enriched.table_semantics or {}
    looks_like_kpi = bool(_KPI_QUERY_PAT.search(user_text)) and not _GROUPING_QUERY_PAT.search(user_text)
    adjusted = []
    for score, tn, sigs in ranked:
        new_score = score
        sem = sem_map_pre.get(tn) or {}
        # never_use_for: an explicit LLM-generated NEGATIVE signal. If the query's
        # own terms overlap with what this table says NOT to use it for, penalize
        # heavily instead of leaving the signal completely inert.
        never_use = sem.get("never_use_for") or []
        if never_use and new_score > 0:
            never_tokens: set = set()
            for phrase in never_use:
                never_tokens |= set(_tokenize(str(phrase)))
            if never_tokens & set(query_tokens):
                new_score *= 0.15
        # is_fact_table: mirrors the boost nl_schema_router.py already applies in
        # the separate chat-agent pipeline — a fact/metric table is more likely
        # correct once it already has some relevance signal.
        if sem.get("is_fact_table") and new_score > 0:
            new_score = min(new_score + 0.08, 1.0)
        # is_kpi_metric: column-level flag — boost when the query reads like a
        # single-aggregate ask ("how many X", "total Y") rather than a breakdown.
        if looks_like_kpi and new_score > 0:
            ct = ct_map.get(tn) or {}
            if any(c.get("is_kpi_metric") for c in ct.get("columns", [])):
                new_score = min(new_score + 0.06, 1.0)
        # grain: light touch only — a small boost when the table's stated grain
        # ("one row per X") names an entity the query also mentions. Low priority/
        # lowest-confidence signal of this batch; kept intentionally small.
        grain = sem.get("grain") or ""
        if grain and new_score > 0:
            grain_tokens = {t for t in _tokenize(grain) if len(t) >= 3 and t not in STOPWORDS}
            if grain_tokens & set(query_tokens):
                new_score = min(new_score + 0.03, 1.0)
        adjusted.append((new_score, tn, sigs))
    ranked = adjusted
    ranked.sort(key=lambda x: x[0], reverse=True)

    # ── View-first boost: views are pre-joined / pre-aggregated — prefer them ──
    view_names: set[str] = set()
    for t in enriched.compact_tables:
        tname = t.get("name", "")
        is_v = t.get("is_view", False)
        bare = tname.split(".")[-1].lower()
        if is_v or bare.startswith("vw_") or bare.endswith("_view") or bare.endswith("_v"):
            view_names.add(tname)
    if view_names:
        boosted = []
        for score, tn, sigs in ranked:
            extra = 0.18 if tn in view_names and score >= 0.25 else 0.0
            boosted.append((min(score + extra, 1.0), tn, sigs))
        boosted.sort(key=lambda x: x[0], reverse=True)
        ranked = boosted

    # ── Conversation-history fallback ──────────────────────────────────────────
    # Only kicks in when the query's OWN signal is weak (top score < 0.20) —
    # a decently-matched query is left alone. When it does kick in, tables used
    # in recent turns are pulled back into contention so their columns still
    # reach the LLM for the follow-up, instead of the retrieval silently
    # wandering off to an unrelated table with equally weak signal.
    if history_tables and ranked and ranked[0][0] < 0.20:
        hist_norm = {h.lower() for h in history_tables}
        boosted = []
        for score, tn, sigs in ranked:
            tn_bare = tn.lower()
            is_hist = tn_bare in hist_norm or any(
                tn_bare.endswith(f".{h}") or h.endswith(f".{tn_bare}") for h in hist_norm
            )
            if is_hist and score < 0.30:
                boosted.append((0.30, tn, list(dict.fromkeys(sigs + ["history"]))))
            else:
                boosted.append((score, tn, sigs))
        boosted.sort(key=lambda x: x[0], reverse=True)
        ranked = boosted

    top = [(s, tn, ss) for s, tn, ss in ranked[:top_k] if s > 0.01]

    if not top:
        return RetrievedContext()

    # ── JOIN need detection: does the query span multiple semantic domains? ────
    needs_join = False
    join_path_result: list = []
    if len(top) >= 2 and rg:
        t1_name, t2_name = top[0][1], top[1][1]
        # Both tables scored meaningfully AND they are FK-connected → JOIN query
        if top[1][0] >= 0.25:
            cond = rg.get_join_condition(t1_name, t2_name) or rg.get_join_condition(t2_name, t1_name)
            if cond:
                needs_join = True
                join_path_result = [(t1_name, t2_name, cond)]
            else:
                # Check 2-hop path
                for intermediate in (rg.edges.get(t1_name) or {}):
                    mid_cond = rg.get_join_condition(t1_name, intermediate)
                    end_cond = rg.get_join_condition(intermediate, t2_name) or rg.get_join_condition(t2_name, intermediate)
                    if mid_cond and end_cond:
                        needs_join = True
                        join_path_result = [(t1_name, intermediate, mid_cond), (intermediate, t2_name, end_cond)]
                        break

    # ── Build TableCandidates ─────────────────────────────────────────────────
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
        # is_dimension/is_filter_eligible: column-level LLM flags that catch
        # GROUP BY / WHERE candidates key_dimension_cols missed (that list can be
        # stale/incomplete) — previously generated and stored but never read.
        for col in (ct.get("columns") or []):
            cname = col.get("name")
            if cname and cname not in col_hints and (col.get("is_dimension") or col.get("is_filter_eligible")):
                col_hints.append(cname)

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
            is_view=tn in view_names,
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
        needs_join=needs_join,
        join_path=join_path_result,
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

    if ctx.needs_join and ctx.join_path:
        lines.append("\nJOIN PATH (pre-verified, use this exact structure):")
        for from_t, to_t, cond in ctx.join_path:
            lines.append(f"  {from_t} JOIN {to_t} ON {cond}")

    view_tables = [c.table_name for c in ctx.candidates if c.is_view]
    if view_tables:
        lines.append(f"\nVIEWS detected (pre-aggregated, prefer these): {', '.join(view_tables)}")

    return "\n".join(lines)
