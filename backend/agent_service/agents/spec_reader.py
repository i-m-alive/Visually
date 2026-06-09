"""
Report Spec Reader — deep-parses BI documentation (PDF/Word/PPTX exports) into
structured chart specifications with SQL-ready templates.

Unlike context_parser (which extracts a single flat set of SQL signals for all
charts combined), spec_reader produces ONE ChartSpecHint per visual described in
the document, each with:
  - Exact measure definitions translated from DAX to SQL
  - Chart-specific WHERE filters (including active-status and isdeleted rules)
  - Calculated column SQL expressions (CASE WHEN ... END)
  - A complete, executable SQL template per chart

Two-pass LLM extraction:
  Pass 1 (global): tables, relationships, business rules, DAX measures, calculated columns
  Pass 2 (charts): one entry per visual described, each with a full SQL template

Integration:
  - orchestrator.py Step 2.5 calls parse_report_spec() in parallel with context_parser
  - Each vision-detected chart gets matched to a ChartSpecHint via match_chart_to_spec()
  - query_agent.generate_from_chart_spec() injects the SQL template as highest-priority instruction
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional

from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_MODEL = BEDROCK_SONNET_MODEL

# Minimum number of BI-domain signals to classify a text as a spec document
_MIN_SPEC_SIGNALS = 2

_BI_SIGNALS = [
    "dax", "measure", "visual", "power bi", "tableau", "looker", "metabase",
    "calculated column", "userelationship", "distinctcount", "calculate(",
    "stacked bar", "donut", "kpi card", "kpi", "fact table", "dimension table",
    "report page", "chart type", "pie chart", "bar chart", "line chart",
    "matrix", "scorecard", "data model", "relationship", "foreign key",
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ChartSpecHint:
    """One documented visual with a ready-to-run SQL template."""
    chart_id: str                           # e.g. "spec_chart_001"
    visual_type: str                        # pie, bar, line, kpi, matrix, donut, stacked_bar
    title: str
    sql_template: str                       # complete, executable SQL query
    measures_used: list[str] = field(default_factory=list)
    dimension_column: Optional[str] = None  # "table.column" for GROUP BY
    metric_column: Optional[str] = None     # "table.column" for aggregation
    filters: list[str] = field(default_factory=list)
    tables_needed: list[str] = field(default_factory=list)
    join_conditions: list[str] = field(default_factory=list)
    position_hint: str = ""                 # "top-left zone", "center donut", etc.


@dataclass
class ReportSpec:
    """Full spec extracted from a BI documentation document."""
    report_name: str
    global_filters: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)
    dax_measures: list[dict] = field(default_factory=list)
    calculated_columns: list[dict] = field(default_factory=list)
    business_rules: list[str] = field(default_factory=list)
    charts: list[ChartSpecHint] = field(default_factory=list)


# ── LLM prompts ───────────────────────────────────────────────────────────────

_GLOBAL_SYSTEM = """You are a BI documentation analyst specialising in extracting SQL-ready
specifications from Power BI, Tableau, Looker, and similar BI tool documentation.

Given report documentation, extract every piece of information that constrains a SQL query:
tables, joins, filters, DAX/calculated measures translated to SQL, and implicit business rules.

Return ONLY valid JSON — no prose, no markdown code fences, no explanation."""

_GLOBAL_USER = """REPORT DOCUMENTATION:
{text}

Extract the global specification. Return ONLY this exact JSON structure:
{{
  "report_name": "...",
  "db_schema_prefix": "staging",
  "tables": ["staging.bullhorn_core_placement", "..."],
  "global_filters": ["isdeleted = FALSE", "..."],
  "relationships": [
    {{
      "from_table": "staging.bullhorn_core_placement",
      "from_column": "joborderid",
      "to_table": "staging.bullhorn_core_job_order",
      "to_column": "id",
      "join_sql": "p JOIN staging.bullhorn_core_job_order j ON p.joborderid = j.id"
    }}
  ],
  "dax_measures": [
    {{
      "name": "Currently Active Candidates",
      "dax": "CALCULATE(DISTINCTCOUNT(placement[id]), ...)",
      "sql_snippet": "COUNT(DISTINCT id) WHERE isdeleted = FALSE AND status IN ('Approved','Needs Approval') AND datebegin <= CURRENT_DATE AND dateend >= CURRENT_DATE",
      "filter_conditions": ["isdeleted = FALSE", "status IN ('Approved','Needs Approval')", "datebegin <= CURRENT_DATE", "dateend >= CURRENT_DATE"]
    }}
  ],
  "calculated_columns": [
    {{
      "table": "staging.bullhorn_core_placement",
      "name": "turn_v_attr",
      "description": "Classifies each placement as Attrition or Turnover based on statusreason",
      "sql_expression": "CASE WHEN statusreason IN ('value1','value2') THEN 'Attrition' ELSE 'Turnover' END AS turn_v_attr"
    }}
  ],
  "business_rules": [
    "All bullhorn_ tables must filter isdeleted = FALSE",
    "Active placement: status IN ('Approved','Needs Approval') AND datebegin <= CURRENT_DATE AND dateend >= CURRENT_DATE"
  ]
}}

DAX-to-SQL translation rules:
- TODAY() → CURRENT_DATE
- IN {{val1, val2}} → IN ('val1', 'val2')
- DISTINCTCOUNT(table[col]) → COUNT(DISTINCT col)
- CALCULATE(expr, filter) → expr with the filter in a WHERE clause
- USERELATIONSHIP(t1[col1], t2[col2]) → JOIN t2 ON t1.col1 = t2.col2
- IF(cond, true_val, false_val) → CASE WHEN cond THEN true_val ELSE false_val END
- SWITCH(expr, v1, r1, v2, r2) → CASE expr WHEN v1 THEN r1 WHEN v2 THEN r2 END
- SUM(table[col]) → SUM(col)
- DIVIDE(a, b) → CASE WHEN b = 0 THEN NULL ELSE a / b END
- Include schema prefix (e.g. 'staging.') in all table names if the document mentions it
- business_rules: capture domain-specific logic that must appear in SQL but is NOT obvious from column names alone"""

_CHARTS_SYSTEM = """You are a BI documentation analyst. Given a report documentation and its
already-extracted global spec, identify EVERY individual chart/visual described and write a
complete, ready-to-run SQL query for each one.

Return ONLY valid JSON — no prose, no markdown code fences, no explanation."""

_CHARTS_USER = """REPORT DOCUMENTATION:
{text}

GLOBAL CONTEXT ALREADY EXTRACTED:
{global_ctx}

Identify every chart/visual described in the documentation and write a complete SQL query for each.
Return ONLY this exact JSON structure:
{{
  "charts": [
    {{
      "id": "spec_chart_001",
      "type": "pie",
      "title": "Active Candidates by Employment Type",
      "position_hint": "top-left zone of the dashboard",
      "tables_needed": ["staging.bullhorn_core_placement"],
      "join_conditions": [],
      "filters": ["isdeleted = FALSE", "status IN ('Approved', 'Needs Approval')", "datebegin <= CURRENT_DATE", "dateend >= CURRENT_DATE"],
      "dimension": "staging.bullhorn_core_placement.employmenttype",
      "metric": "COUNT(DISTINCT staging.bullhorn_core_placement.id)",
      "measures_used": ["Currently Active Candidates"],
      "sql_template": "SELECT p.employmenttype, COUNT(DISTINCT p.id) AS active_count FROM staging.bullhorn_core_placement p WHERE p.isdeleted = FALSE AND p.status IN ('Approved', 'Needs Approval') AND p.datebegin <= CURRENT_DATE AND p.dateend >= CURRENT_DATE GROUP BY p.employmenttype ORDER BY active_count DESC"
    }}
  ]
}}

Rules:
- Write exactly one entry per distinct chart/visual described in the documentation
- sql_template MUST be a complete, valid SQL query (SELECT ... FROM ... WHERE ... GROUP BY ...)
- Apply ALL global_filters from the global context to every chart
- Apply ALL business_rules from the global context (e.g. isdeleted = FALSE on every bullhorn_ table)
- For charts using calculated columns, embed the sql_expression inline in the SELECT clause
- For charts joining multiple tables, include all necessary JOIN clauses
- Use CURRENT_DATE (not NOW()) for "today" references
- type must be one of: pie, donut, bar, stacked_bar, line, combo, kpi, kpi_card, matrix, table
- position_hint: where in the dashboard (e.g. "top row left", "center row", "bottom matrix")
- For KPI/scorecard charts, the sql_template should return a single aggregate value
- For net/change calculations, use CTEs to compute start and end counts then subtract
- Number chart ids sequentially: spec_chart_001, spec_chart_002, ..."""


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_spec_document(text: str) -> bool:
    """
    Heuristic: returns True when text looks like a BI report specification.
    Checks for domain-specific BI signals (DAX keywords, chart types, etc.).
    """
    text_lower = text.lower()
    hits = sum(1 for s in _BI_SIGNALS if s in text_lower)
    return hits >= _MIN_SPEC_SIGNALS


def _strip_fences(raw: str) -> str:
    """Remove markdown code fences that the model may have added."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
    return raw.strip()


# ── Pass 1: global context ────────────────────────────────────────────────────

async def _extract_global_context(text: str) -> dict:
    """
    Pass 1: extract tables, relationships, business rules, DAX measures,
    and calculated columns from the full document text.
    """
    try:
        raw = await bedrock_invoke(
            model_id=_MODEL,
            system_prompt=_GLOBAL_SYSTEM,
            user_message=_GLOBAL_USER.format(text=text[:60_000]),
            temperature=0.0,
            max_tokens=6000,
        )
        result = json.loads(_strip_fences(raw))
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        print(f"[spec_reader] global extraction failed: {exc}", flush=True)
        return {}


# ── Pass 2: per-chart specs ───────────────────────────────────────────────────

async def _extract_chart_specs(text: str, global_ctx: dict) -> list[dict]:
    """
    Pass 2: extract one chart spec (with SQL template) per visual described
    in the document.  Uses the global context from Pass 1 as additional input.
    """
    try:
        global_ctx_str = json.dumps(global_ctx, ensure_ascii=False)[:4000]
        raw = await bedrock_invoke(
            model_id=_MODEL,
            system_prompt=_CHARTS_SYSTEM,
            user_message=_CHARTS_USER.format(text=text[:60_000], global_ctx=global_ctx_str),
            temperature=0.0,
            max_tokens=8000,
        )
        result = json.loads(_strip_fences(raw))
        return result.get("charts", []) if isinstance(result, dict) else []
    except Exception as exc:
        print(f"[spec_reader] chart spec extraction failed: {exc}", flush=True)
        return []


# ── Main entry point ──────────────────────────────────────────────────────────

async def parse_report_spec(
    text: str,
    db_type: str = "redshift",
) -> Optional[ReportSpec]:
    """
    Parse a BI documentation text (extracted from PDF/Word/PPTX) into a ReportSpec.

    Runs two sequential LLM passes:
      Pass 1 — global context (tables, relationships, measures, business rules)
      Pass 2 — per-chart specs with full SQL templates

    Returns None (non-fatal) if extraction fails or produces no charts.
    """
    if not text or not text.strip():
        return None

    print("[spec_reader] ── starting two-pass extraction", flush=True)

    global_ctx = await _extract_global_context(text)
    if not global_ctx:
        print("[spec_reader] ⚠ pass 1 returned empty — aborting spec extraction", flush=True)
        return None

    chart_dicts = await _extract_chart_specs(text, global_ctx)

    # Build ChartSpecHint objects, skipping entries with no SQL template
    charts: list[ChartSpecHint] = []
    for i, c in enumerate(chart_dicts):
        sql = (c.get("sql_template") or "").strip()
        if not sql:
            continue
        charts.append(ChartSpecHint(
            chart_id=c.get("id") or f"spec_chart_{i + 1:03d}",
            visual_type=(c.get("type") or "bar").lower(),
            title=c.get("title") or "",
            sql_template=sql,
            measures_used=c.get("measures_used") or [],
            dimension_column=c.get("dimension"),
            metric_column=c.get("metric"),
            filters=c.get("filters") or [],
            tables_needed=c.get("tables_needed") or [],
            join_conditions=c.get("join_conditions") or [],
            position_hint=c.get("position_hint") or "",
        ))

    spec = ReportSpec(
        report_name=global_ctx.get("report_name") or "",
        global_filters=global_ctx.get("global_filters") or [],
        tables=global_ctx.get("tables") or [],
        relationships=global_ctx.get("relationships") or [],
        dax_measures=global_ctx.get("dax_measures") or [],
        calculated_columns=global_ctx.get("calculated_columns") or [],
        business_rules=global_ctx.get("business_rules") or [],
        charts=charts,
    )

    print(
        f"[spec_reader] ✓ extracted: report='{spec.report_name}'"
        f"  tables={len(spec.tables)}"
        f"  measures={len(spec.dax_measures)}"
        f"  calc_cols={len(spec.calculated_columns)}"
        f"  charts={len(spec.charts)}",
        flush=True,
    )
    return spec if spec.charts else None


# ── Chart matching ────────────────────────────────────────────────────────────

def _stem_word(w: str) -> str:
    """Minimal singular normalisation — strips trailing 'es'/'s' for plural matching.
    'Replacements' → 'Replacement', 'placements' → 'placement', etc.
    """
    if len(w) > 4 and w.endswith("es"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s"):
        return w[:-1]
    return w


_TYPE_GROUPS: list[frozenset] = [
    frozenset({"pie", "donut"}),
    frozenset({"bar", "stacked_bar", "column", "grouped_bar"}),
    frozenset({"line", "combo", "area"}),
    frozenset({"kpi", "kpi_card", "gauge", "scorecard", "metric"}),
    frozenset({"matrix", "table", "pivot"}),
]


def _types_compatible(a: str, b: str) -> bool:
    """Returns True if two visual type strings belong to the same semantic family."""
    for group in _TYPE_GROUPS:
        if a in group and b in group:
            return True
    return False


def match_chart_to_spec(
    chart_spec: dict,
    report_spec: ReportSpec,
) -> Optional[ChartSpecHint]:
    """
    Match a vision-detected chart to the closest documented ChartSpecHint.

    Scoring:
      - Title word overlap × 2.0  (strongest signal)
      - Exact visual type match   +1.5
      - Compatible type family    +0.5

    Returns None when no hint scores above the minimum threshold (0.4).
    """
    if not report_spec or not report_spec.charts:
        return None

    detected_title = (chart_spec.get("title") or "").lower()
    detected_type = (chart_spec.get("type") or "").lower()

    best_hint: Optional[ChartSpecHint] = None
    best_score = -1.0

    for hint in report_spec.charts:
        score = 0.0

        # Title word-overlap score — use stem normalisation so "Replacements" matches "Replacement"
        hint_words = set(_stem_word(w) for w in re.sub(r"[^a-z0-9 ]", " ", hint.title.lower()).split())
        det_words = set(_stem_word(w) for w in re.sub(r"[^a-z0-9 ]", " ", detected_title).split())
        if hint_words and det_words:
            overlap = len(hint_words & det_words) / (len(hint_words | det_words) + 1)
            score += overlap * 2.0

        # Visual type score
        hint_type = hint.visual_type.lower()
        if hint_type == detected_type:
            score += 1.5
        elif _types_compatible(hint_type, detected_type):
            score += 0.5

        if score > best_score:
            best_score = score
            best_hint = hint

    # Always log the best candidate so we can diagnose non-matches
    if best_hint is not None:
        print(
            f"[spec_reader] best-match: '{detected_title}' ({detected_type})"
            f" → '{best_hint.title}' ({best_hint.visual_type})"
            f"  score={best_score:.2f}  threshold=0.30",
            flush=True,
        )

    # Require at least a meaningful overlap (lowered from 0.40 → 0.30 to handle
    # singular/plural title variants and partial word matches)
    if best_score < 0.30 or best_hint is None:
        print(
            f"[spec_reader] ✗ no spec match for '{detected_title}' — best_score={best_score:.2f} < 0.30",
            flush=True,
        )
        return None

    return best_hint
