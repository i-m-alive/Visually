"""
Stage 1 of the NL2SQL two-stage pipeline: Fast Intent Parser.

Uses the Haiku/Sonnet model to extract structured intent slots from the user's
natural-language query in a single fast LLM call (~200 ms).  The parsed
ParsedIntent is then consumed by nl_schema_router.py (Stage 2) to resolve
which tables, columns, and JOINs are needed before the SQL generator ever
runs — so the SQL generator receives a focused, pre-resolved context rather
than the full raw schema.

Slot taxonomy
─────────────
intent_type   : metric_lookup | trend | comparison | list | rank | count
                | filter | explanation | conversational
entities      : named things the user mentioned (companies, people, products …)
metrics       : business measure terms (revenue, billing hours, placements …)
time_filter   : optional time window (year / month / quarter / custom range)
group_by      : dimension terms to group/break-down by (by region, per month …)
chart_hint    : suggested chart type derived from intent wording
needs_sql     : False only for purely conversational questions
"""
import json
import re
import asyncio
from dataclasses import dataclass, field, asdict
from typing import Optional

from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ParsedEntity:
    text: str
    entity_type: str  # company | person | product | location | status | date_val | unknown


@dataclass
class TimeFilter:
    period: Optional[str] = None    # year | month | quarter | week | ytd | last_n | custom
    year: Optional[int] = None
    month: Optional[int] = None
    quarter: Optional[int] = None
    last_n_days: Optional[int] = None
    from_date: Optional[str] = None  # ISO-8601
    to_date: Optional[str] = None    # ISO-8601


@dataclass
class ParsedIntent:
    intent_type: str                      # see taxonomy above
    entities: list = field(default_factory=list)        # list[ParsedEntity]
    metrics: list = field(default_factory=list)         # list[str]
    time_filter: Optional[TimeFilter] = None
    group_by: list = field(default_factory=list)        # list[str]
    chart_hint: Optional[str] = None
    needs_sql: bool = True
    confidence: float = 1.0
    raw_message: str = ""


# ── Intent type heuristic map ─────────────────────────────────────────────────

_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("trend",          ["trend", "over time", "per month", "monthly", "weekly", "daily",
                        "by year", "year over year", "yoy", "growth", "change", "historical"]),
    ("rank",           ["top", "bottom", "highest", "lowest", "best", "worst", "most", "least",
                        "ranking", "ranked", "rank"]),
    ("comparison",     ["compare", "versus", "vs", "against", "difference", "between"]),
    ("count",          ["how many", "count", "number of", "total count", "how much"]),
    ("metric_lookup",  ["revenue", "sales", "amount", "total", "sum", "average", "avg",
                        "median", "rate", "value", "profit", "cost", "spend", "billing"]),
    ("list",           ["list", "show all", "display", "give me all", "fetch", "retrieve",
                        "what are", "who are"]),
    ("filter",         ["filter", "where", "only", "exclude", "include", "just show"]),
    ("explanation",    ["why", "what caused", "reason", "explain", "how come", "what happened"]),
    ("conversational", ["hi", "hello", "thanks", "thank you", "what is", "what does",
                        "how do", "help me understand"]),
]

_CHART_HINT_MAP: dict[str, str] = {
    "trend": "line",
    "rank": "bar_vertical",
    "comparison": "bar_vertical",
    "count": "kpi",
    "metric_lookup": "kpi",
    "list": "table",
    "filter": "table",
    "explanation": "table",
    "conversational": "",
}

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_ENTITY_TYPE_KEYWORDS: dict[str, list[str]] = {
    "company":  ["company", "client", "corporation", "firm", "org", "business", "account",
                 "agency", "enterprise", "partner", "vendor"],
    "person":   ["person", "employee", "candidate", "user", "staff", "worker", "contact",
                 "rep", "recruiter", "manager", "candidate"],
    "product":  ["product", "item", "sku", "service", "offering", "plan"],
    "location": ["city", "state", "country", "region", "area", "territory", "office", "location"],
    "job":      ["job", "role", "position", "vacancy", "opening", "order", "placement"],
    "status":   ["status", "stage", "state", "phase", "category", "type"],
}


# ── LLM-based intent parser ───────────────────────────────────────────────────

_INTENT_SYSTEM_PROMPT = """You are a precise NLU system for a BI analytics platform.
Extract structured intent from user queries about business data.
Return ONLY valid JSON — no prose, no markdown fences."""

_INTENT_USER_TEMPLATE = """Extract the intent from this analytics query:

"{message}"

Return this exact JSON shape (no extra fields, no comments):
{{
  "intent_type": "<metric_lookup|trend|comparison|list|rank|count|filter|explanation|conversational>",
  "entities": [
    {{"text": "<exact text from query>", "entity_type": "<company|person|product|location|job|status|date_val|unknown>"}}
  ],
  "metrics": ["<business measure term 1>", "<business measure term 2>"],
  "time_filter": {{
    "period": "<year|month|quarter|week|ytd|last_n|custom|null>",
    "year": <int or null>,
    "month": <int 1-12 or null>,
    "quarter": <int 1-4 or null>,
    "last_n_days": <int or null>,
    "from_date": "<YYYY-MM-DD or null>",
    "to_date": "<YYYY-MM-DD or null>"
  }},
  "group_by": ["<dimension phrase 1>"],
  "chart_hint": "<kpi|bar_vertical|line|pie|table|multi_row_card|scatter|null>",
  "needs_sql": <true|false>,
  "confidence": <0.0-1.0>
}}

Rules:
- entities: only NAMED things (proper nouns, specific values). Do NOT include generic words like "revenue" or "data".
- metrics: business measure words ("revenue", "billing hours", "placement count") — NOT column names.
- needs_sql: false only for greetings, conceptual questions, or "what does X mean".
- chart_hint null means let the system decide.
- If nothing matches a field, use null or [].
- time_filter null if no time is mentioned."""


async def parse_intent(message: str) -> ParsedIntent:
    """
    Parse a user query into a structured ParsedIntent.

    Tries an LLM call first; falls back to heuristic parsing on any failure so
    the pipeline never blocks on an intent-parser error.
    """
    if not message or not message.strip():
        return ParsedIntent(
            intent_type="conversational", needs_sql=False,
            raw_message=message, confidence=0.0,
        )

    # Fast heuristic pre-check: skip LLM for obvious conversational messages
    lower = message.lower().strip()
    if len(lower) < 15 and any(lower.startswith(g) for g in ("hi", "hello", "hey", "thanks", "ok")):
        return ParsedIntent(
            intent_type="conversational", needs_sql=False,
            raw_message=message, confidence=1.0,
        )

    try:
        raw = await asyncio.wait_for(
            bedrock_invoke(
                model_id=BEDROCK_HAIKU_MODEL,
                system_prompt=_INTENT_SYSTEM_PROMPT,
                user_message=_INTENT_USER_TEMPLATE.format(message=message[:1500]),
                temperature=0.0,
                max_tokens=512,
            ),
            timeout=8.0,   # hard cap — never slow down the main response
        )
        return _parse_llm_response(raw, message)
    except Exception as exc:
        print(f"[intent_parser] ⚠ LLM call failed ({exc!r}) — using heuristics", flush=True)
        return _heuristic_parse(message)


# ── Response parser ───────────────────────────────────────────────────────────

def _parse_llm_response(raw: str, original_message: str) -> ParsedIntent:
    """Parse the LLM's JSON response into a ParsedIntent."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract just the JSON object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return _heuristic_parse(original_message)
        else:
            return _heuristic_parse(original_message)

    entities = [
        ParsedEntity(
            text=str(e.get("text", "")),
            entity_type=str(e.get("entity_type", "unknown")),
        )
        for e in (data.get("entities") or [])
        if e.get("text")
    ]

    tf_raw = data.get("time_filter") or {}
    time_filter: Optional[TimeFilter] = None
    if tf_raw and tf_raw.get("period") and tf_raw["period"] != "null":
        time_filter = TimeFilter(
            period=tf_raw.get("period"),
            year=_int_or_none(tf_raw.get("year")),
            month=_int_or_none(tf_raw.get("month")),
            quarter=_int_or_none(tf_raw.get("quarter")),
            last_n_days=_int_or_none(tf_raw.get("last_n_days")),
            from_date=tf_raw.get("from_date") or None,
            to_date=tf_raw.get("to_date") or None,
        )

    chart_hint = data.get("chart_hint")
    if chart_hint == "null" or not chart_hint:
        chart_hint = None

    intent = ParsedIntent(
        intent_type=str(data.get("intent_type") or "metric_lookup"),
        entities=entities,
        metrics=[str(m) for m in (data.get("metrics") or [])],
        time_filter=time_filter,
        group_by=[str(g) for g in (data.get("group_by") or [])],
        chart_hint=chart_hint,
        needs_sql=bool(data.get("needs_sql", True)),
        confidence=float(data.get("confidence") or 0.8),
        raw_message=original_message,
    )
    print(
        f"[intent_parser] LLM parsed: type={intent.intent_type} "
        f"entities={len(intent.entities)} metrics={intent.metrics} "
        f"chart_hint={intent.chart_hint} needs_sql={intent.needs_sql}",
        flush=True,
    )
    return intent


# ── Heuristic fallback ────────────────────────────────────────────────────────

def _heuristic_parse(message: str) -> ParsedIntent:
    """Pure-regex heuristic intent parsing — zero LLM calls, instant."""
    lower = message.lower()

    # intent_type
    intent_type = "metric_lookup"
    for itype, keywords in _INTENT_PATTERNS:
        if any(kw in lower for kw in keywords):
            intent_type = itype
            break

    # needs_sql
    needs_sql = intent_type != "conversational"

    # time_filter — year detection
    time_filter: Optional[TimeFilter] = None
    year_match = re.search(r"\b(20\d{2}|19\d{2})\b", message)
    month_match = re.search(
        r"\b(" + "|".join(_MONTH_NAMES.keys()) + r")\b", lower
    )
    quarter_match = re.search(r"\bq([1-4])\b", lower)
    ytd_match = re.search(r"\byt[d]\b|\byear[ -]to[ -]date\b", lower)
    last_n_match = re.search(r"\blast\s+(\d+)\s+(day|week|month)", lower)

    if year_match or month_match or quarter_match or ytd_match or last_n_match:
        time_filter = TimeFilter(
            period=(
                "ytd" if ytd_match else
                "last_n" if last_n_match else
                "quarter" if quarter_match else
                "month" if month_match else
                "year"
            ),
            year=int(year_match.group(1)) if year_match else None,
            month=_MONTH_NAMES.get(month_match.group(1)) if month_match else None,
            quarter=int(quarter_match.group(1)) if quarter_match else None,
            last_n_days=(
                int(last_n_match.group(1)) * {"day": 1, "week": 7, "month": 30}[last_n_match.group(2)]
                if last_n_match else None
            ),
        )

    # entities — quoted strings + capitalized proper-noun-looking phrases
    entities: list[ParsedEntity] = []
    for quoted in re.findall(r'"([^"]{2,})"', message):
        entities.append(ParsedEntity(text=quoted, entity_type="unknown"))

    # group_by
    group_by: list[str] = []
    for m in re.finditer(r"\bby\s+(\w+(?:\s+\w+)?)\b", lower):
        phrase = m.group(1).strip()
        if phrase not in ("the", "a", "an"):
            group_by.append(phrase)

    # metrics — look for business measure words
    metrics: list[str] = []
    for phrase in ["revenue", "billing hours", "hours", "placements", "sales", "count",
                   "amount", "total", "rate", "profit", "spend", "cost"]:
        if phrase in lower:
            metrics.append(phrase)

    chart_hint = _CHART_HINT_MAP.get(intent_type) or None

    return ParsedIntent(
        intent_type=intent_type,
        entities=entities,
        metrics=metrics,
        time_filter=time_filter,
        group_by=group_by,
        chart_hint=chart_hint or None,
        needs_sql=needs_sql,
        confidence=0.6,
        raw_message=message,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _int_or_none(v) -> Optional[int]:
    if v is None or v == "null":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
