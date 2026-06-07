"""
Schema Matcher Agent — ranks schema tables/column combinations for a given chart spec.
Accepts an EnrichedSchema (from schema_cache) so it gets disambiguation context and
pre-built compact table list for free — no re-processing per call.
"""
import json
import re
from typing import TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

MATCHER_MODEL = BEDROCK_SONNET_MODEL

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
- Return at least 1 candidate"""


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
        semantics_block = enriched.get_table_semantics_text()

        user_msg = USER_TEMPLATE.format(
            chart_json=json.dumps(chart_summary, indent=2),
            disambiguation_block=disambiguation_block if disambiguation_block else "",
            semantics_block=semantics_block if semantics_block else "",
            schema_json=json.dumps(enriched.compact_tables, indent=2),
            table_count=len(tables),
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
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
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
