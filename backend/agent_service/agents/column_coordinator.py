"""
Column Coordinator — resolves cross-chart column conflicts after schema matching.

When multiple charts on the same dashboard reference the same semantic concept
(e.g. "client name", "revenue", "placement date"), each chart's independent
schema-matcher might assign the concept to different columns or tables.

This agent runs ONCE per dashboard (one LLM call) after all N schema-matcher
calls complete. It looks at every chart's top candidate simultaneously and:
  1. Spots conflicts — same concept mapped to different table.column across charts
  2. Produces a global_assignments dict — one authoritative table.column per concept
  3. Produces per-chart overrides where the global assignment doesn't fit

Each chart agent then calls apply_coordination() before SQL generation to get
the globally-consistent column assignments injected into its candidate dict.
"""
import copy
import json
import re
from typing import TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

_MODEL = BEDROCK_SONNET_MODEL

_SYSTEM = """You are a data modeling expert resolving column-assignment conflicts across multiple
dashboard charts that share the same database.

When the same semantic concept (e.g. "client name", "revenue", "placement date") is mapped
to different columns or tables in different charts, you must pick one authoritative
table+column for each concept and apply it consistently across all charts.

Return ONLY valid JSON — no prose, no markdown."""

_USER_TEMPLATE = """DASHBOARD OVERVIEW — {chart_count} charts detected.

CURRENT COLUMN ASSIGNMENTS PER CHART:
{charts_summary}

TABLE SEMANTICS (business purpose of each table):
{semantics_block}

COLUMN DISAMBIGUATION CONTEXT:
{disambiguation_block}

TASK:
1. Identify semantic conflicts — same concept assigned to different table.column in different charts.
2. For each conflict, pick the single most appropriate table+column (prefer the table with the most
   chart assignments, or the one whose table semantics best fit the concept).
3. Output a global_assignments map and per-chart overrides where needed.

Return ONLY this JSON structure:
{{
  "conflicts_found": [
    "client_name: chart_001 uses placements.client_name, chart_003 uses employees.client_name"
  ],
  "global_assignments": {{
    "client_name": {{"table": "placements", "column": "client_name", "reason": "placements is the primary fact table; 3 of 4 charts already use it"}},
    "revenue":     {{"table": "placements", "column": "fee_amount",  "reason": "fee_amount is the only monetary metric in the schema"}}
  }},
  "chart_overrides": {{
    "chart_003": {{
      "dimension": {{"table": "placements", "column": "client_name"}},
      "metric":    {{"table": "placements", "column": "fee_amount"}}
    }}
  }}
}}

Rules:
- Only include a concept in global_assignments if there IS a conflict — don't reassign things that already agree.
- chart_overrides[chart_id] takes precedence over global_assignments for that chart.
- If zero conflicts, return empty conflicts_found, global_assignments, and chart_overrides.
- Only use table and column names that appear in the CURRENT COLUMN ASSIGNMENTS above."""


async def coordinate_columns(
    chart_specs: list[dict],
    all_candidates: dict,   # {chart_id: [candidate_dict, ...]}
    enriched: "EnrichedSchema",
) -> dict:
    """
    Run a single LLM call to resolve cross-chart column conflicts.

    Returns:
      {
        "global_assignments": {concept: {table, column, reason}},
        "chart_overrides":    {chart_id: {role: {table, column}}},
        "conflicts_found":    [str, ...]
      }

    Never raises — returns empty dicts on any failure so the pipeline continues.
    """
    empty = {"global_assignments": {}, "chart_overrides": {}, "conflicts_found": []}

    if not chart_specs or not all_candidates:
        return empty

    # Build a compact summary: one block per chart showing its top candidate assignments
    summary_parts: list[str] = []
    for spec in chart_specs:
        cid = spec.get("id", "")
        candidates = all_candidates.get(cid, [])
        top = candidates[0] if candidates else {}
        key_cols = top.get("key_columns") or {}
        summary_parts.append(
            f"[{cid}]  type={spec.get('type','?')}  title=\"{spec.get('title') or ''}\"\n"
            f"  tables={top.get('tables', [])}  confidence={round(top.get('confidence', 0), 2)}\n"
            f"  dimension={key_cols.get('dimension')}  metric={key_cols.get('metric')}  "
            f"date={key_cols.get('date')}  group_by={key_cols.get('group_by')}"
        )

    disambiguation_block = enriched.get_disambiguation_text()

    # Collect all tables referenced across charts for focused semantics
    all_referenced_tables = list({
        t
        for spec in chart_specs
        for cand in all_candidates.get(spec.get("id", ""), [])[:1]
        for t in (cand.get("tables") or [])
    })
    semantics_block = enriched.get_table_semantics_text(all_referenced_tables or None)

    user_msg = _USER_TEMPLATE.format(
        chart_count=len(chart_specs),
        charts_summary="\n\n".join(summary_parts),
        semantics_block=semantics_block or "(no table semantics available)",
        disambiguation_block=disambiguation_block or "(no ambiguous columns detected)",
    )

    try:
        raw = await bedrock_invoke(
            model_id=_MODEL,
            system_prompt=_SYSTEM,
            user_message=user_msg,
            temperature=0.0,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)

        result = json.loads(raw)
        conflicts = result.get("conflicts_found", [])
        overrides = result.get("chart_overrides", {})

        if conflicts:
            print(
                f"[column_coordinator] {len(conflicts)} conflict(s) resolved: {conflicts}",
                flush=True,
            )
        else:
            print("[column_coordinator] no cross-chart conflicts detected", flush=True)

        if overrides:
            print(
                f"[column_coordinator] chart overrides applied: {list(overrides.keys())}",
                flush=True,
            )

        return {
            "global_assignments": result.get("global_assignments", {}),
            "chart_overrides": overrides,
            "conflicts_found": conflicts,
        }

    except Exception as exc:
        print(f"[column_coordinator] ⚠ coordination failed (non-fatal): {exc}", flush=True)
        return empty


def apply_coordination(
    chart_id: str,
    candidate: dict,
    coordination: dict,
) -> dict:
    """
    Merge global_assignments + chart_overrides into the candidate dict for one chart.

    Returns a deep copy with updated key_columns and tables list.
    Does not mutate the original candidate.
    """
    updated = copy.deepcopy(candidate)
    key_cols: dict = dict(updated.get("key_columns") or {})

    chart_override = coordination.get("chart_overrides", {}).get(chart_id, {})

    # Apply per-chart overrides first (highest priority)
    for role in ("dimension", "metric", "date", "group_by"):
        override = chart_override.get(role) or {}
        if override.get("column"):
            key_cols[role] = override["column"]
            # Prepend overriding table to the tables list if it changed
            new_table = override.get("table")
            current_tables: list = list(updated.get("tables") or [])
            if new_table and new_table not in current_tables:
                updated["tables"] = [new_table] + current_tables

    updated["key_columns"] = key_cols
    return updated
