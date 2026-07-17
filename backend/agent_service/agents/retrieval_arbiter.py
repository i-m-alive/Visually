"""
Retrieval Arbiter — the LLM tie-breaker in the ensemble retrieval pipeline.

The cheap deterministic experts (TF-IDF, embeddings, concept, entity, FK-graph)
fuse their rankings via RRF in graph_rag_retriever. When they strongly agree, we
trust that ranking and never call an LLM. Only when they DISAGREE (low agreement
or a thin top-two margin — see RetrievedContext.needs_arbiter) does this arbiter
run: it reads the shortlist's metadata plus each expert's vote and picks the
final table ordering, with a one-line justification.

Cost discipline: one small-prompt call, shortlist only (never the full schema),
and only on the minority of ambiguous queries. Never raises — on any failure it
returns the retriever's original order unchanged.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_SYSTEM_PROMPT = """You are a table-selection arbiter for a natural-language-to-SQL system.
Several independent ranking methods disagreed about which table(s) best answer the
user's question. Your job is to break the tie using the table metadata provided.

You are given: the user's question, and a SHORTLIST of candidate tables — each with
its description, key columns, and which ranking methods favored it.

Choose the table(s) that can actually answer the question. Prefer a table whose
columns contain the metric/dimension the question needs. If the question needs data
from more than one table, list them in priority order (the primary/fact table first).

Return ONLY valid JSON, no prose:
{
  "ordered_tables": ["schema.table_a", "schema.table_b"],
  "reason": "one short sentence"
}
Rules:
- Use ONLY table names that appear in the shortlist. Never invent one.
- ordered_tables must be non-empty and ranked best-first.
- Keep reason to one sentence.
"""


def _build_shortlist_block(ctx, enriched) -> str:
    """Compact metadata for the shortlisted tables + which experts favored each."""
    ct_map = {t.get("name"): t for t in (enriched.compact_tables or [])}
    sem_map = enriched.table_semantics or {}
    # invert expert_rankings → {table: [experts that ranked it top-5]}
    fans: dict[str, list] = {}
    for expert, order in (ctx.expert_rankings or {}).items():
        for tn in order:
            fans.setdefault(tn, []).append(expert)

    lines = []
    for tn in ctx.shortlist[:8]:
        ct = ct_map.get(tn) or {}
        sem = sem_map.get(tn) or {}
        desc = (sem.get("purpose") or ct.get("description") or "").strip()[:200]
        cols = [c.get("name") for c in (ct.get("columns") or [])[:12] if c.get("name")]
        favored = ", ".join(fans.get(tn, [])) or "none"
        lines.append(
            f"- {tn}\n"
            f"    description: {desc or '(none)'}\n"
            f"    columns: {', '.join(cols)}\n"
            f"    favored by: {favored}"
        )
    return "\n".join(lines)


def _parse(raw: str) -> Optional[dict]:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("ordered_tables"), list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


async def arbitrate(user_text: str, ctx, enriched) -> dict:
    """Reorder ctx.shortlist via one LLM call. Returns
    {ordered_tables: [...], reason: str, used: bool}. Never raises; on any
    problem returns the original order with used=False."""
    fallback = {"ordered_tables": list(ctx.shortlist), "reason": "", "used": False}
    if not ctx or not ctx.shortlist:
        return fallback
    try:
        block = _build_shortlist_block(ctx, enriched)
        user_message = (
            f"User question: {user_text}\n\n"
            f"Shortlist (ranking methods disagreed on the order):\n{block}"
        )
        raw = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_message,
            max_tokens=300,
            temperature=0.0,
        )
        data = _parse(raw)
        if not data:
            return fallback
        # Keep only names that were actually on the shortlist (guard against
        # a hallucinated table), preserving the arbiter's order.
        allowed = set(ctx.shortlist)
        ordered = [t for t in data["ordered_tables"] if t in allowed]
        if not ordered:
            return fallback
        # Append any shortlist tables the arbiter dropped, so nothing is lost.
        for tn in ctx.shortlist:
            if tn not in ordered:
                ordered.append(tn)
        return {"ordered_tables": ordered, "reason": str(data.get("reason", ""))[:200], "used": True}
    except Exception as exc:
        print(f"[retrieval_arbiter] arbitration failed (non-fatal): {exc}", flush=True)
        return fallback
