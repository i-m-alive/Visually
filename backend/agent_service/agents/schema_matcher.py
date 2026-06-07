"""
Schema Matcher Agent — ranks schema tables/column combinations for a given chart spec.
Accepts an EnrichedSchema (from schema_cache) so it gets disambiguation context and
pre-built compact table list for free — no re-processing per call.

Speed: uses Haiku (5× faster, 10× cheaper than Sonnet) — table ranking is a
classification task that doesn't require deep reasoning.

Accuracy: TF-IDF pre-filter reduces the table set sent to the LLM, shrinking the
prompt and focusing the model on the most relevant tables.
"""
import json
import re
from typing import TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

# Haiku is sufficient for table ranking (classification, not generation)
MATCHER_MODEL = BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """You are a database schema analyst specializing in data visualization.
Given a chart specification detected from a screenshot and an enriched database schema,
identify which tables and columns are MOST LIKELY needed to reproduce the chart.

Use the COLUMN DISAMBIGUATION and TABLE SEMANTICS sections to resolve ambiguous column names
and understand what each table is for before picking candidates.

ANALYSIS APPROACH:
1. Match chart title keywords → table names, descriptions, column names
2. Match x-axis label / x-tick labels → candidate dimension or date columns
3. Match y-axis label / estimated values → candidate metric/aggregate columns
4. Match legend labels → candidate grouping columns
5. chart type inference: KPI→single aggregate, line→time series, bar→category+metric
6. data_point_count hints at expected row granularity
7. Consider joins when a single table can't supply both dimension and metric

SEMANTIC GRAIN CHECK (critical — wrong grain = wrong table):
- Chart title says "jobs by role" or "open positions" → look for a table whose grain is JOB/POSTING (one row per job), not employee or profile table
- Chart title says "revenue by month" → look for a transaction or order table, not a product or customer table
- Chart title says "employees by department" → look for an employee or headcount table, not a jobs table
- PENALIZE tables that are at the wrong entity level — even if they share a column name
- When TABLE SEMANTICS section is present, read the "purpose" and "grain" fields before assigning confidence

CONFIDENCE CALIBRATION:
- Start from 1.0 and subtract based on mismatches
- Mismatch in grain: -0.4
- Mismatch in key_metric_cols: -0.2
- Mismatch in chart title keywords vs table description: -0.1 per keyword miss
- If a better-matching table exists in the schema, assign the wrong one confidence ≤ 0.3

Return ONLY valid JSON."""

USER_TEMPLATE = """CHART TO REPLICATE:
{chart_json}

{disambiguation_block}

{semantics_block}

ALL AVAILABLE TABLES ({table_count} total):
{schema_json}

Database type: {db_type}

Return a JSON array of up to 5 candidate table combinations ranked by confidence (highest first):
[
  {{
    "tables": ["table_name"],
    "key_columns": {{"dimension": "col_name", "metric": "col_name", "date": "col_or_null", "group_by": "col_or_null"}},
    "join": null,
    "reasoning": "Why this table best matches the chart — reference disambiguation if applicable",
    "confidence": 0.95
  }},
  {{
    "tables": ["table_a", "table_b"],
    "key_columns": {{"dimension": "col", "metric": "col", "date": null, "group_by": null}},
    "join": "table_a.fk_col = table_b.pk_col",
    "reasoning": "Join needed because ...",
    "confidence": 0.70
  }}
]

Rules:
- Only use table names that exist in the schema above
- confidence 0.8+ = strong match, 0.5-0.8 = plausible, <0.5 = speculative
- When a column name is ambiguous, pick the occurrence from the most relevant table (see DISAMBIGUATION)
- When x_tick_labels show job titles / roles (e.g. "Software Engineer", "Manager"), the dimension col must be a role/title column in a JOBS or POSITIONS table — not an employee profile or account table
- When x_tick_labels show company names or clients, prefer tables with a company/client grain
- Return at least 1 candidate"""


def _tfidf_prefilter(chart_spec: dict, compact_tables: list, top_n: int = 15) -> list:
    """
    Word-overlap pre-filter: scores each table by how many chart-spec words appear
    in its name + description + column names.  Keeps top_n tables before calling
    the LLM.  Recall-focused (not Jaccard) — we prefer not to miss the right table.

    When the schema has ≤ top_n tables the function returns the original list unchanged.
    """
    if not compact_tables or len(compact_tables) <= top_n:
        return compact_tables

    # Collect query terms from the chart spec
    query_terms: set[str] = set()

    def _add(text: str) -> None:
        if text:
            for w in re.sub(r"[^a-z0-9_]", " ", text.lower()).split():
                if len(w) > 2:
                    query_terms.add(w)

    _add(chart_spec.get("title", ""))
    _add(chart_spec.get("x_axis_label", ""))
    _add(chart_spec.get("y_axis_label", ""))
    for lbl in (chart_spec.get("x_tick_labels") or [])[:6]:
        _add(str(lbl))
    for lbl in (chart_spec.get("legend_labels") or [])[:4]:
        _add(str(lbl))
    # estimated_values keys are a strong signal for KPI charts that have no title
    # e.g. {"Active Placements": 1234} → adds "active", "placements" to query_terms
    for key in (chart_spec.get("estimated_values") or {}).keys():
        _add(str(key))

    if not query_terms:
        # Nothing to match on — rank by table size; largest tables are most likely
        # to contain the chart data in a typical analytics database.
        print(
            f"[schema_matcher] TF-IDF no query terms — fallback to row_count order",
            flush=True,
        )
        return sorted(compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)[:top_n]

    def _score(table: dict) -> float:
        tterms: set[str] = set()

        def _add_t(text: str) -> None:
            if text:
                for w in re.sub(r"[^a-z0-9_]", " ", text.lower()).split():
                    if len(w) > 2:
                        tterms.add(w)

        _add_t(table.get("name", ""))
        _add_t(table.get("description", ""))
        for col in (table.get("columns") or [])[:30]:
            _add_t(col.get("name", ""))
            _add_t(col.get("description", ""))
        if not tterms:
            return 0.0
        return len(query_terms & tterms) / len(query_terms)

    scored = sorted(compact_tables, key=_score, reverse=True)
    # If the top score is still 0 (no term overlap with any table), fall back to
    # row_count sort — better than an arbitrary stable-sort order.
    if scored and _score(scored[0]) == 0.0:
        print(
            f"[schema_matcher] TF-IDF zero overlap — fallback to row_count order",
            flush=True,
        )
        scored = sorted(compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)
    result = scored[:top_n]
    print(
        f"[schema_matcher] TF-IDF pre-filter: {len(compact_tables)} → {len(result)} tables",
        flush=True,
    )
    return result


class SchemaMatcher:
    async def rank_candidates(
        self,
        chart_spec: dict,
        enriched: "EnrichedSchema",
    ) -> list[dict]:
        """
        Rank schema tables by relevance to this chart spec using the enriched schema.
        Returns up to 5 candidates sorted by confidence desc.
        """
        tables = enriched.schema_doc.get("tables", [])
        if not tables:
            return []

        # TF-IDF pre-filter: send at most 15 tables to the LLM
        filtered_tables = _tfidf_prefilter(chart_spec, enriched.compact_tables, top_n=15)

        chart_summary = {
            "type": chart_spec.get("type"),
            "title": chart_spec.get("title"),
            "x_axis_label": chart_spec.get("x_axis_label"),
            "y_axis_label": chart_spec.get("y_axis_label"),
            "x_tick_labels": (chart_spec.get("x_tick_labels") or [])[:10],
            "estimated_values": chart_spec.get("estimated_values") or {},
            "legend_labels": (chart_spec.get("legend_labels") or [])[:8],
            "data_point_count": chart_spec.get("data_point_count", 0),
        }

        disambiguation_block = enriched.get_disambiguation_text()
        # Only show semantics for the filtered tables to keep prompt short
        filtered_names = [t.get("name") for t in filtered_tables]
        semantics_block = enriched.get_table_semantics_text(table_names=filtered_names)

        user_msg = USER_TEMPLATE.format(
            chart_json=json.dumps(chart_summary, indent=2),
            disambiguation_block=disambiguation_block if disambiguation_block else "",
            semantics_block=semantics_block if semantics_block else "",
            schema_json=json.dumps(filtered_tables, indent=2),
            table_count=len(filtered_tables),
            db_type=enriched.db_type,
        )

        try:
            raw = await bedrock_invoke(
                model_id=MATCHER_MODEL,
                system_prompt=SYSTEM_PROMPT,
                user_message=user_msg,
                temperature=0.1,
            )
            raw = raw.strip()
            # Strip code fences
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
                raw = raw.strip()
            # Extract just the JSON array — Haiku often appends prose/notes after the
            # closing ']', which causes json.loads to fail with "Extra data".
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            if m:
                raw = m.group(0)
            candidates = json.loads(raw)
            if isinstance(candidates, list):
                # validate_names must use compact_tables names (schema-qualified when the DB
                # has non-default schemas) — NOT raw schema_doc names (bare names).
                # The LLM sees compact_tables in its prompt so its output uses the same format.
                valid_names = {t.get("name") for t in enriched.compact_tables}
                valid = []
                for c in candidates:
                    tbls = c.get("tables", [])
                    fixed = [t for t in tbls if t in valid_names]
                    if fixed:
                        c["tables"] = fixed
                        valid.append(c)

                # Deduplicate: keep only the highest-confidence entry per unique table set
                seen_keys: dict[tuple, dict] = {}
                for c in valid:
                    key = tuple(sorted(c.get("tables", [])))
                    if key not in seen_keys or c.get("confidence", 0) > seen_keys[key].get("confidence", 0):
                        seen_keys[key] = c
                valid = list(seen_keys.values())

                ranked = sorted(valid, key=lambda x: x.get("confidence", 0), reverse=True)[:5]

                # Validate join paths against the FK relationship graph
                for candidate in ranked:
                    tbls = candidate.get("tables", [])
                    if len(tbls) < 2:
                        continue
                    t_a, t_b = tbls[0], tbls[1]
                    graph = enriched.relationship_graph
                    if not graph.path_exists(t_a, t_b):
                        # No FK path — nullify the join suggestion and penalise confidence
                        if candidate.get("join"):
                            print(
                                f"[schema_matcher] ⚠ no FK path {t_a}↔{t_b} — "
                                f"clearing join, penalising confidence",
                                flush=True,
                            )
                            candidate["join"] = None
                            candidate["confidence"] = round(
                                candidate.get("confidence", 0.5) * 0.6, 3
                            )
                    else:
                        # FK path confirmed — replace LLM-guessed condition with real one
                        real_cond = graph.get_join_condition(t_a, t_b)
                        if real_cond and not candidate.get("join"):
                            candidate["join"] = real_cond
                            print(
                                f"[schema_matcher] ✓ FK join confirmed {t_a}↔{t_b}: {real_cond}",
                                flush=True,
                            )

                # Re-sort after confidence adjustments
                ranked = sorted(ranked, key=lambda x: x.get("confidence", 0), reverse=True)
                print(f"[schema_matcher] candidates: {[(c['tables'], round(c.get('confidence',0),2)) for c in ranked]}", flush=True)
                return ranked
        except Exception as e:
            print(f"[schema_matcher] ⚠ ranking failed: {e} — fallback to row_count order", flush=True)

        # Fallback: top 5 tables by row_count — use compact_tables so names are schema-qualified
        sorted_tables = sorted(enriched.compact_tables, key=lambda t: t.get("row_count") or 0, reverse=True)
        return [
            {
                "tables": [t["name"]],
                "key_columns": {"dimension": None, "metric": None, "date": None, "group_by": None},
                "join": None,
                "reasoning": "Fallback — largest table by row count",
                "confidence": max(0.05, 0.3 - i * 0.05),
            }
            for i, t in enumerate(sorted_tables[:5])
        ]
