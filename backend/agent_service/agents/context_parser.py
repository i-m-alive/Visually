"""
Context Parser — converts the user's free-text description of a screenshot into
structured SQL signals (Mode 3: Guided Replication).

Called once per pipeline job when the user provides natural-language context such as
"This chart shows active placements by employment type for Q1 2024."

The parsed output is injected into two pipeline stages:
  - schema_matcher  : context_keywords boost TF-IDF table ranking
  - query_agent     : implied_filters / date_range / aggregation injected as hard constraints
"""
import json
import re
from typing import Optional

from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

_PARSER_MODEL = BEDROCK_HAIKU_MODEL

_SYSTEM_PROMPT = """You are a SQL analyst who extracts structured query signals from
business text. The input may be a short description OR a longer document (PDF report,
PPTX slide deck, Word doc). Your job is to scan the entire text and pull out only the
parts relevant to SQL: dates, filters, groupings, aggregations, and business rules.

Ignore: executive summaries, company branding, author names, table of contents,
general commentary, introductory paragraphs, headers, footers, and anything that
does not directly constrain a SQL query.

Return ONLY valid JSON — no prose, no markdown."""

_USER_TEMPLATE = """Input text (may be a brief description or a full document extract):
"{context}"

Scan the entire text. Extract SQL signals. Return ONLY valid JSON with this exact structure:
{{
  "chart_intent": "one-sentence description of what this chart shows",
  "implied_filters": [
    {{
      "column_hint": "status",
      "operator": "=",
      "value": "Active",
      "description": "Only active records"
    }}
  ],
  "implied_date_range": {{
    "start": "YYYY-MM-DD or null",
    "end": "YYYY-MM-DD or null",
    "column_hint": "date column name hint or null",
    "granularity": "year|quarter|month|week|day|null",
    "description": "plain English date constraint or null"
  }},
  "implied_aggregation": "COUNT|SUM|AVG|COUNT_DISTINCT|null",
  "implied_metric_hint": "column to aggregate or null",
  "implied_groupby_hint": "primary GROUP BY column or null",
  "implied_sort": {{
    "direction": "ASC|DESC|null",
    "limit": null
  }},
  "sql_constraints": ["plain English constraint 1", "constraint 2"],
  "context_keywords": ["keyword1", "keyword2"]
}}

Rules:
- Scan ALL of the text, not just the first paragraph. Important signals (date ranges,
  filter values) often appear deep in a document.
- IGNORE: document titles, author names, company logos/headers, table of contents,
  general market commentary, introductory boilerplate, and any text that would not
  change a SQL WHERE clause or GROUP BY.
- implied_filters: WHERE conditions from phrases like "active", "open jobs", "status = X",
  "only include", "exclude", "filter by"
  - operator options: "=", "!=", ">", "<", ">=", "<=", "IN", "LIKE", "IS NULL", "IS NOT NULL"
  - value: the literal value as a string (e.g. "Active", "true", "Q1 2024")
- implied_date_range: convert period references to ISO dates:
  - "Q1 YYYY" → start=YYYY-01-01, end=YYYY-03-31
  - "Q2 YYYY" → start=YYYY-04-01, end=YYYY-06-30
  - "Q3 YYYY" → start=YYYY-07-01, end=YYYY-09-30
  - "Q4 YYYY" → start=YYYY-10-01, end=YYYY-12-31
  - "FY YYYY" / "fiscal year YYYY" → start=YYYY-01-01, end=YYYY-12-31
  - "last year" → previous calendar year
  - "YTD" → January 1 of current year to today
  - Specific month: "March 2024" → start=2024-03-01, end=2024-03-31
  - null if no date period is mentioned
- implied_aggregation:
  - "count of", "number of", "how many", "headcount" → COUNT or COUNT_DISTINCT
  - "total", "sum of", "revenue", "spend" → SUM
  - "average", "avg", "mean" → AVG
  - null if unclear
- context_keywords: business terms for table/column matching (2-15 words)
  - include entity names (placements, jobs, clients), status values, column hints
  - exclude stop words and document boilerplate
- sql_constraints: specific, actionable constraints the SQL MUST satisfy
  - good: "Only count placements with status = Active"
  - bad: "Provide detailed analysis of quarterly performance" (not actionable SQL)
- If something is not mentioned, use null (not empty string, not [])"""


async def parse_user_context(user_context: str) -> dict:
    """
    Parse free-text user description into structured SQL signals.
    Returns empty dict if context is blank or parsing fails.

    Example input:
        "This shows active placements by employment type for Q1 2024.
         Only include open job orders."

    Example output:
        {
          "chart_intent": "Active placements grouped by employment type for Q1 2024",
          "implied_filters": [
              {"column_hint": "status", "operator": "=", "value": "Active", ...},
              {"column_hint": "is_open", "operator": "=", "value": "true", ...}
          ],
          "implied_date_range": {"start": "2024-01-01", "end": "2024-03-31", ...},
          "implied_aggregation": "COUNT",
          "implied_groupby_hint": "employment_type",
          "sql_constraints": ["Filter to active placements only", "Limit to Q1 2024"],
          "context_keywords": ["placements", "employment", "type", "open", "jobs", "active"]
        }
    """
    if not user_context or not user_context.strip():
        return {}

    prompt = _USER_TEMPLATE.format(context=user_context.strip())

    try:
        raw = await bedrock_invoke(
            model_id=_PARSER_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            user_message=prompt,
            temperature=0.0,
            max_tokens=4096,
        )
        raw = raw.strip()
        # Strip code fences if the model wrapped the JSON
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()

        result = json.loads(raw)
        if not isinstance(result, dict):
            return {}

        # Ensure list fields are always lists, never null
        for field in ("implied_filters", "sql_constraints", "context_keywords"):
            if not isinstance(result.get(field), list):
                result[field] = []

        # Ensure nested dicts are always dicts, never null
        if not isinstance(result.get("implied_date_range"), dict):
            result["implied_date_range"] = {}
        if not isinstance(result.get("implied_sort"), dict):
            result["implied_sort"] = {}

        # Derive context_keywords from chart_intent if the model returned an empty list
        if not result["context_keywords"] and result.get("chart_intent"):
            _STOP = {
                "the", "a", "an", "of", "by", "in", "for", "and", "or", "to",
                "is", "are", "with", "that", "this", "it", "at", "on", "be",
            }
            words = re.sub(r"[^a-z0-9 ]", " ", result["chart_intent"].lower()).split()
            result["context_keywords"] = [w for w in words if len(w) > 2 and w not in _STOP]

        _dr = result.get("implied_date_range", {})
        print(
            f"[context_parser] parsed → intent='{(result.get('chart_intent') or '')[:60]}'"
            f"  filters={len(result['implied_filters'])}"
            f"  date={_dr.get('start')}→{_dr.get('end')}"
            f"  agg={result.get('implied_aggregation')}"
            f"  groupby={result.get('implied_groupby_hint')}"
            f"  keywords={result['context_keywords'][:5]}",
            flush=True,
        )
        return result

    except Exception as exc:
        print(f"[context_parser] ⚠ parse failed: {exc}", flush=True)
        return {}


def build_context_instruction(parsed_context: dict, user_context: str = "") -> str:
    """
    Build a high-priority instruction block from parsed context for injection into
    the query agent prompt.  Returns an empty string when there is nothing to inject.
    """
    if not parsed_context and not user_context:
        return ""

    parts: list[str] = []

    if user_context:
        parts.append(f"USER CONTEXT (highest priority — the user described the chart as): \"{user_context.strip()}\"")

    intent = (parsed_context or {}).get("chart_intent")
    if intent:
        parts.append(f"CHART INTENT: {intent}")

    filters = (parsed_context or {}).get("implied_filters") or []
    if filters:
        filter_strs = []
        for f in filters:
            col = f.get("column_hint", "?")
            op = f.get("operator", "=")
            val = f.get("value", "?")
            desc = f.get("description", "")
            filter_strs.append(f"{col} {op} '{val}'" + (f" ({desc})" if desc else ""))
        parts.append("REQUIRED WHERE CONDITIONS: " + " AND ".join(filter_strs))

    dr = (parsed_context or {}).get("implied_date_range") or {}
    start, end = dr.get("start"), dr.get("end")
    col_hint = dr.get("column_hint")
    if start and end:
        col_str = f" on column '{col_hint}'" if col_hint else ""
        parts.append(
            f"REQUIRED DATE RANGE{col_str}: {start} to {end}"
            f" — filter WHERE {col_hint or 'date_col'} BETWEEN '{start}' AND '{end}'"
        )

    agg = (parsed_context or {}).get("implied_aggregation")
    metric = (parsed_context or {}).get("implied_metric_hint")
    if agg:
        metric_str = f"({metric})" if metric else ""
        parts.append(f"REQUIRED AGGREGATION: use {agg}{metric_str} for the metric column")

    groupby = (parsed_context or {}).get("implied_groupby_hint")
    if groupby:
        parts.append(f"REQUIRED GROUP BY: group by '{groupby}' or a column that represents it")

    constraints = (parsed_context or {}).get("sql_constraints") or []
    if constraints:
        numbered = " | ".join(f"({i+1}) {c}" for i, c in enumerate(constraints))
        parts.append(f"ADDITIONAL SQL CONSTRAINTS: {numbered}")

    if not parts:
        return ""

    return (
        "⚑ CRITICAL — MODE 3 USER CONTEXT (override any conflicting inference): "
        + " ▸ ".join(parts)
    )
