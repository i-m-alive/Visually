"""
Context Synthesis Agent.

Merges all available context signals (PBIT field bindings, schema candidates, document
spec, user hints, calc_col_map, business_rules) into a single resolved chart spec.
The query_agent receives this pre-resolved spec and only needs to write correct SQL
instead of reconciling multiple competing sources simultaneously.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL


@dataclass
class ResolvedChartSpec:
    chart_id: str
    primary_table: str                              # "schema.table"
    join_tables: list = field(default_factory=list)
    join_conditions: list = field(default_factory=list)
    dimension_column: Optional[str] = None          # exact DB column for x-axis / GROUP BY
    metric_expression: str = "COUNT(*)"             # full SQL aggregate expression
    date_column: Optional[str] = None
    group_by_columns: list = field(default_factory=list)
    where_conditions: list = field(default_factory=list)
    chart_type: str = "bar_vertical"
    title: str = ""
    order_by: Optional[str] = None
    limit: Optional[int] = None
    confidence: float = 0.0
    reasoning: str = ""
    sources_used: list = field(default_factory=list)


def _get_table_columns(table_name: str, compact_tables: list) -> list:
    for t in compact_tables:
        if t.get("name") == table_name:
            return [c.get("name", "") for c in t.get("columns", []) if c.get("name")]
    return []


def _make_fallback(chart_spec: dict, candidates: list) -> "ResolvedChartSpec":
    top = candidates[0] if candidates else {}
    tables = top.get("tables", [])
    kc = top.get("key_columns") or {}
    metric = f"COUNT(DISTINCT {kc['metric']})" if kc.get("metric") else "COUNT(*)"
    return ResolvedChartSpec(
        chart_id=chart_spec.get("id", ""),
        primary_table=tables[0] if tables else "",
        join_tables=tables[1:],
        join_conditions=[top["join"]] if top.get("join") else [],
        dimension_column=kc.get("dimension"),
        metric_expression=metric,
        date_column=kc.get("date"),
        group_by_columns=[kc["dimension"]] if kc.get("dimension") else [],
        chart_type=chart_spec.get("type", "bar_vertical"),
        title=chart_spec.get("title", ""),
        confidence=0.3,
        reasoning="fallback from top schema candidate (synthesizer failed)",
        sources_used=["schema_matcher_fallback"],
    )


async def synthesize_chart_context(
    chart_spec: dict,
    candidates: list,
    enriched: Any,
    pbit_hint: Optional[dict] = None,
    spec_hint: Any = None,
    user_context: str = "",
    parsed_context: Optional[dict] = None,
    calc_col_map: Optional[dict] = None,
    business_rules: Optional[list] = None,
    user_column_hints: Optional[list] = None,
    db_type: str = "redshift",
) -> ResolvedChartSpec:
    """
    Merge all context signals into a ResolvedChartSpec.
    Non-fatal: returns fallback spec derived from top candidate on any failure.
    """
    chart_id = chart_spec.get("id", "unknown")

    sections: list[str] = []
    sources_used: list[str] = []

    # 1. Vision-detected chart spec
    sections.append("CHART SPEC (from screenshot):\n" + json.dumps({
        "type": chart_spec.get("type"),
        "title": chart_spec.get("title"),
        "x_axis_label": chart_spec.get("x_axis_label"),
        "y_axis_label": chart_spec.get("y_axis_label"),
        "x_tick_labels": (chart_spec.get("x_tick_labels") or [])[:8],
        "estimated_values": chart_spec.get("estimated_values") or {},
        "data_point_count": chart_spec.get("data_point_count", 0),
        "legend_labels": (chart_spec.get("legend_labels") or [])[:5],
        "kpi_metric_name": chart_spec.get("kpi_metric_name"),
    }, indent=2))
    sources_used.append("vision")

    # 2. Schema candidates with actual column lists
    top_cands = candidates[:3]
    if top_cands:
        cand_summary = []
        for c in top_cands:
            tables = c.get("tables", [])
            table_schemas = [
                {"table": t, "columns": _get_table_columns(t, enriched.compact_tables)[:25]}
                for t in tables[:3]
            ]
            cand_summary.append({
                "tables": tables,
                "key_columns": c.get("key_columns"),
                "join": c.get("join"),
                "confidence": round(c.get("confidence", 0), 2),
                "schema": table_schemas,
            })
        sections.append("SCHEMA CANDIDATES (ranked):\n" + json.dumps(cand_summary, indent=2))
        sources_used.append("schema_matcher")

    # 3. PBIT field bindings — highest-priority signal when present
    if pbit_hint:
        sections.append("PBIT FIELD BINDINGS (Power BI ground truth — HIGHEST PRIORITY):\n" + json.dumps({
            "field_bindings": pbit_hint.get("field_bindings", {}),
            "db_tables": pbit_hint.get("db_tables", []),
            "measures": pbit_hint.get("measures", {}),
            "join_conditions": pbit_hint.get("join_conditions", []),
            "visual_type": pbit_hint.get("visual_type"),
            "title": pbit_hint.get("title"),
        }, indent=2))
        sources_used.append("pbit")

    # 4. Document spec SQL template
    if spec_hint and getattr(spec_hint, "sql_template", None):
        sections.append("DOCUMENT SPEC:\n" + json.dumps({
            "title": spec_hint.title,
            "sql_template": spec_hint.sql_template,
            "tables_needed": spec_hint.tables_needed,
            "filters": spec_hint.filters,
            "measures_used": spec_hint.measures_used,
        }, indent=2))
        sources_used.append("document")

    # 5. Calculated column expressions
    if calc_col_map:
        sections.append("CALCULATED COLUMNS (Power BI derived fields, not raw DB columns):\n"
                        + json.dumps(calc_col_map, indent=2))
        sources_used.append("calc_col_map")

    # 6. Mandatory WHERE conditions
    if business_rules:
        sections.append("MANDATORY WHERE CONDITIONS:\n" + json.dumps(business_rules, indent=2))
        sources_used.append("business_rules")

    # 7. User context
    if user_context and user_context.strip():
        sections.append("USER DESCRIPTION:\n" + user_context.strip()[:400])
        sources_used.append("user_context")
    if parsed_context:
        _sig = {k: v for k, v in parsed_context.items()
                if v and k in ("implied_filters", "implied_date_range",
                               "implied_aggregation", "implied_groupby_hint", "sql_constraints")}
        if _sig:
            sections.append("PARSED CONTEXT SIGNALS:\n" + json.dumps(_sig, indent=2))

    # 8. User column selections
    if user_column_hints:
        sections.append("USER COLUMN SELECTIONS:\n" + json.dumps(user_column_hints, indent=2))
        sources_used.append("user_column_hints")

    context_block = "\n\n".join(sections)

    prompt = f"""{context_block}

TASK: Determine the exact SQL components needed to reproduce this chart from the database.

Priority order (highest → lowest):
1. PBIT FIELD BINDINGS — ground truth from original Power BI file
2. DOCUMENT SPEC SQL TEMPLATE — pre-verified business logic
3. USER COLUMN SELECTIONS — explicit user choices
4. SCHEMA CANDIDATES + CALCULATED COLUMNS
5. USER DESCRIPTION + PARSED CONTEXT SIGNALS

Return ONLY a JSON object with these exact fields:
{{
  "primary_table": "schema.table_name (fully qualified)",
  "join_tables": ["schema.table2"],
  "join_conditions": ["t1.col = t2.col"],
  "dimension": "exact_db_column_for_x_axis_or_null",
  "metric_expression": "COUNT(DISTINCT col) or SUM(col) or COUNT(*) etc.",
  "date_column": "date_col_or_null",
  "group_by": ["col1"],
  "where_conditions": ["isdeleted = FALSE"],
  "chart_type": "bar_vertical|line|pie|kpi|table|...",
  "title": "chart title",
  "order_by": "metric DESC or null",
  "limit": 50,
  "confidence": 0.0-1.0,
  "reasoning": "which sources drove the decision and why",
  "sources_used": ["pbit", "schema_matcher"]
}}

Critical rules:
- primary_table MUST include schema prefix (e.g. staging.table_name)
- dimension: use exact column name from the DB schema shown in SCHEMA CANDIDATES
- If PBIT has a translated measure expression, use it as metric_expression
- If a PBIT field maps to a CALCULATED COLUMN, use the CASE WHEN expression
- where_conditions: only WHERE predicates, NOT join conditions
- For KPI: dimension=null, metric_expression=the single numeric aggregation
- chart_type must match PBIT visual_type when available
"""

    try:
        raw = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=(
                "You are a database expert that resolves chart requirements into precise SQL components. "
                "Return ONLY a valid JSON object — no prose, no markdown, no code blocks."
            ),
            user_message=prompt,
            max_tokens=1024,
            temperature=0.0,
        )
        raw = raw.strip()
        # Strip markdown fences if present
        _m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if _m:
            raw = _m.group(1)
        elif not raw.startswith("{"):
            _m2 = re.search(r"\{.*\}", raw, re.DOTALL)
            if _m2:
                raw = _m2.group()

        obj = json.loads(raw)

        spec = ResolvedChartSpec(
            chart_id=chart_id,
            primary_table=obj.get("primary_table") or (top_cands[0].get("tables", [""])[0] if top_cands else ""),
            join_tables=obj.get("join_tables") or [],
            join_conditions=obj.get("join_conditions") or [],
            dimension_column=obj.get("dimension") or None,
            metric_expression=obj.get("metric_expression") or "COUNT(*)",
            date_column=obj.get("date_column") or None,
            group_by_columns=obj.get("group_by") or [],
            where_conditions=obj.get("where_conditions") or [],
            chart_type=obj.get("chart_type") or chart_spec.get("type", "bar_vertical"),
            title=obj.get("title") or chart_spec.get("title", ""),
            order_by=obj.get("order_by") or None,
            limit=int(obj["limit"]) if obj.get("limit") else None,
            confidence=float(obj.get("confidence", 0.7)),
            reasoning=obj.get("reasoning", ""),
            sources_used=obj.get("sources_used") or sources_used,
        )
        print(
            f"[context_synthesizer] chart={chart_id} → table={spec.primary_table}"
            f" dim={spec.dimension_column} metric={spec.metric_expression[:60]}"
            f" conf={spec.confidence:.2f} sources={spec.sources_used}",
            flush=True,
        )
        return spec

    except Exception as exc:
        print(f"[context_synthesizer] ⚠ chart={chart_id} failed (non-fatal): {exc}", flush=True)
        return _make_fallback(chart_spec, candidates)
