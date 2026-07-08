import json
import re
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
from shared.schemas.agent import IntentResult, IntentEntities, TimeRange, FilterCondition
from agent_service.agents.domain_config import SKILL_DOMAINS, normalize_domain

INTENT_CLASSIFIER_MODEL = BEDROCK_HAIKU_MODEL

_CORE_PROMPT = """You are an intent classification system for a data visualization platform.
Analyze the user's message and extract structured information.

INTENT TYPES (choose exactly one):
- SINGLE_VIZ: User wants one chart/visualization. Signals: "show me", "chart", "graph", "visualize", metric/dimension words.
- DASHBOARD: User wants multiple charts, a full dashboard, or an overview. Signals: "dashboard", "overview", "summary", "report", multiple different metric words in one request.
- FOLLOWUP: User is referring to a prior result with pronouns. Signals: "it", "that", "this chart", "the graph", "filter", "drill", "refine", "update", "change it", "why did it".
- SCHEMA_EXPLORE: User wants to explore what data/tables are available, understand the database structure, or get example questions they can ask. Signals: "what tables do I have", "what data do I have", "explain my schema", "what can I ask", "what's in my database", "what data is available", "explore data", "list my tables", "what kind of questions", "what do I have access to", "show me what you know".
"""

# Per-domain "AGENT SKILL INTENTS" vocabulary. A domain not listed here (e.g.
# "generic") gets NO skill-intent block at all — those questions always stay on
# the SINGLE_VIZ/DASHBOARD/FOLLOWUP/SCHEMA_EXPLORE chart/SQL path instead of
# being routed to a vertical-specific persona that may refuse out-of-scope data.
_SKILL_INTENT_BLOCKS: dict[str, str] = {
    "recruitment": """
AGENT SKILL INTENTS — route to the tool-use agent layer, NOT the SQL pipeline.
Use these when the user's request maps to a recruitment workflow action, not a data visualisation:
- MATCH: Find, rank, or score candidates for a job using ML scores. Signals: "best candidates", "top candidates", "rank candidates", "who should I interview", "shortlist for", "score candidates", "find candidates for [role]", "strongest applicants", "recommend candidates".
- BRIEFING: Give a daily overview of what needs attention in the recruitment pipeline, OR answer personal/role-specific queries when the user says "my". Signals: "briefing", "daily summary", "what should I focus on", "what needs attention today", "pipeline overview", "morning priorities", "what's urgent", "catch me up", "my placements", "my candidates", "my clients", "my accounts", "my pipeline", "my applications", "show me my", "what am I working on", "who am I placing", "my activity", "my performance", "my jobs", "my work", "what is my name", "who am I", "tell me about myself", "my data", "my focus", "my open roles".
- SCREEN: Screen a specific candidate or generate screening questions based on their resume and the job. Signals: "screen [candidate]", "screening questions for", "interview questions for [person]", "evaluate this candidate", "assess [name]".
- ENRICH: Enrich or complete a candidate's profile using their parsed resume data. Signals: "enrich profile", "fill in missing info", "complete [candidate]'s profile", "summarize [candidate]", "update profile from resume", "build profile for".
- VERIFY: Verify a candidate's resume or profile for inconsistencies, gaps, or mismatches with their ML score. Signals: "verify [candidate]", "check resume", "validate skills", "inconsistencies in profile", "does the resume match", "anomalies", "red flags".
- PRESENT: Generate a candidate submission packet or presentation for a client. Signals: "present candidates", "submission packet", "candidate report", "prepare presentation for client", "send candidates to", "client submission".
- AUDIT: Audit the recruitment pipeline for data quality issues, missing scores, stale records, or financial/billing integrity. Signals: "audit pipeline", "data quality", "missing scores", "unscored candidates", "stale applications", "incomplete profiles", "compliance check", "what's missing", "billing issues", "billing rate", "bill rate", "pay rate", "rate mismatch", "inverted margin", "placement errors", "financial audit", "missing bill rate", "zero bill rate".
- PROSPECT: Find pipeline gaps, jobs at risk, or business development opportunities. Signals: "pipeline gaps", "jobs with no candidates", "at-risk roles", "opportunities", "business development", "which jobs need attention", "roles without recommendations".
- ACTION: Create a note, update a candidate or application status, or tag a record. Signals: "add note", "update status", "mark as", "move to [stage]", "create note for", "tag [candidate]", "change status of".
""",
    "finance": """
AGENT SKILL INTENTS — route to the tool-use agent layer, NOT the SQL pipeline.
Use these when the user's request maps to a finance/operations workflow action, not a data visualisation:
- MATCH: Find, rank, or flag accounts, transactions, or customers by risk, fraud, or priority score. Signals: "highest risk accounts", "flag suspicious transactions", "top risk customers", "rank accounts by exposure", "score transactions", "find high-risk transactions", "riskiest accounts".
- BRIEFING: Give a daily overview of what needs attention in finance operations, OR answer personal/role-specific queries when the user says "my". Signals: "briefing", "daily summary", "what should I focus on", "what needs attention today", "operations overview", "morning priorities", "what's urgent", "catch me up", "my accounts", "my transactions", "my portfolio", "my clients", "show me my", "what am I working on", "my activity", "my performance", "my exceptions", "my open items".
- SCREEN: Screen a specific account, transaction, or customer, or generate a review checklist. Signals: "screen [account]", "review checklist for", "evaluate this transaction", "assess [customer]".
- ENRICH: Enrich or complete an account, customer, or transaction record using available data. Signals: "enrich profile", "fill in missing info", "complete [customer]'s profile", "summarize [account]", "update record".
- VERIFY: Verify a transaction or account for inconsistencies, gaps, or compliance mismatches. Signals: "verify [transaction]", "check compliance", "validate account", "inconsistencies", "does this match", "anomalies", "red flags".
- PRESENT: Generate a client-facing statement, report, or presentation. Signals: "present accounts", "statement", "client report", "prepare presentation for client", "send report to".
- AUDIT: Audit finance data for quality issues, missing values, stale records, or reconciliation/integrity problems. Signals: "audit transactions", "data quality", "missing values", "stale records", "incomplete records", "compliance check", "what's missing", "reconciliation issues", "balance mismatch", "duplicate transactions", "orphaned records", "negative balance".
- PROSPECT: Find operational gaps, at-risk accounts, or business opportunities. Signals: "gaps", "accounts with no activity", "at-risk accounts", "opportunities", "which accounts need attention", "unresolved exceptions".
- ACTION: Create a note, update a record's status, or tag a record. Signals: "add note", "update status", "mark as", "move to [stage]", "create note for", "tag [account]", "change status of".
""",
}

_TAIL_PROMPT = """
OUTPUT MODE (choose exactly one) — how the answer is best presented:
- "chart": the answer is best SHOWN as a visualization. Signals: "trend", "over time", "by <category>", "compare", "distribution", "breakdown", "top N", "show me a chart/graph", any explicit chart_type, or any request whose result is a series of values across a dimension/time.
- "text": the answer is best stated in WORDS, with no chart. Signals: a single fact or aggregate ("how many", "what is the total", "what's the average", "which is highest/lowest"), yes/no questions, or "explain", "summarize", "describe", "tell me about". A single number or short fact -> "text".
When unsure, prefer "chart" if the result naturally has a dimension + a metric (something to plot); otherwise "text".

ENTITY TYPES to extract:
- metrics: numeric measure words (revenue, sales, count, orders, churn, rate, total, average)
- dimensions: grouping/category words (region, product, category, country, status, month, year, user)
- time_range: date references ("last quarter", "this year", "2024", "past 30 days") -> normalize to {type: "relative"|"absolute", value: str}
- time_granularity: the GROUP BY time bucket the user wants, when they ask for a time-bucketed breakdown.
  "day wise" / "daily" / "per day" / "date wise" -> "day"
  "week wise" / "weekly" / "per week" -> "week"
  "month wise" / "monthly" / "per month" / "month over month" -> "month"
  "quarter wise" / "quarterly" -> "quarter"
  "year wise" / "yearly" / "annual" / "year over year" -> "year"
  null when the user did not ask for a time-bucketed breakdown.
  CRITICAL: time_granularity is how to GROUP the data; time_range is how to FILTER it.
  "placements per month for the last year" -> time_granularity="month" AND time_range="last year".
  A request for a chart with DATES on the x axis is ALWAYS a granularity request:
  "7-day application inflow, in x axis I want dates of last 7 days" -> time_granularity="day" AND time_range="last 7 days".
- chart_type: explicit chart requests (bar, line, pie, donut, scatter, kpi, multi_row_card, table, area, funnel, gauge, treemap, waterfall, slicer) -> null if not specified
  NOTE: use "multi_row_card" when the user wants a KPI card with MULTIPLE values broken down by a category (e.g. "count by region", "jobs per type"). Use "kpi" only for a single aggregate number.
  NOTE: use "slicer" when the user wants a filter control, dropdown slicer, or checkbox filter that will filter other charts on the page.
- filters: explicit filter conditions [{column, op, value}]

VAGUENESS SCORE (0.0 to 1.0):
- 0.0-0.3: fully vague ("show me something interesting")
- 0.3-0.6: partially specified ("show revenue by region")
- 0.6-0.9: mostly specified ("bar chart of monthly revenue")
- 0.9-1.0: fully specified (exact SQL intent)
"""

_BASE_INTENT_TYPES = ["SINGLE_VIZ", "DASHBOARD", "FOLLOWUP", "SCHEMA_EXPLORE"]
_SKILL_INTENT_TYPES = ["MATCH", "BRIEFING", "SCREEN", "ENRICH", "VERIFY", "PRESENT", "AUDIT", "PROSPECT", "ACTION"]


def build_system_prompt(domain: str) -> str:
    """Assemble the classifier's system prompt for this project's domain.

    Domains in SKILL_DOMAINS get their own AGENT SKILL INTENTS vocabulary (and
    those intent types in the JSON schema enum); other domains (e.g. "generic")
    get neither — those questions can only ever land on SINGLE_VIZ/DASHBOARD/
    FOLLOWUP/SCHEMA_EXPLORE, so a specialist persona can never misfire on them.
    """
    skill_block = _SKILL_INTENT_BLOCKS.get(domain, "") if domain in SKILL_DOMAINS else ""
    intent_types = _BASE_INTENT_TYPES + (_SKILL_INTENT_TYPES if skill_block else [])
    intent_enum = " | ".join(intent_types)

    json_schema = f"""
Return ONLY valid JSON:
{{
  "intent_type": "{intent_enum}",
  "confidence": 0.0,
  "entities": {{
    "metrics": [],
    "dimensions": [],
    "time_range": null,
    "time_granularity": null,
    "chart_type": null,
    "filters": []
  }},
  "vagueness_score": 0.0,
  "followup_ref": null,
  "sub_intents": [],
  "output_mode": "chart",
  "reasoning": "one sentence"
}}"""
    return _CORE_PROMPT + skill_block + _TAIL_PROMPT + json_schema


# Deterministic granularity detection — backs up the LLM so "month wise"
# is NEVER lost even when the classifier misses it.
_GRANULARITY_PATTERNS: list[tuple[str, str]] = [
    ("day",     r"\b(day\s*wise|daywise|daily|per\s+day|each\s+day|date\s*wise|datewise|day\s+by\s+day)\b"),
    ("week",    r"\b(week\s*wise|weekwise|weekly|per\s+week|each\s+week)\b"),
    ("month",   r"\b(month\s*wise|monthwise|monthly|per\s+month|each\s+month|month\s+over\s+month|mom)\b"),
    ("quarter", r"\b(quarter\s*wise|quarterly|per\s+quarter|each\s+quarter|qoq)\b"),
    ("year",    r"\b(year\s*wise|yearwise|yearly|annual|annually|per\s+year|each\s+year|year\s+over\s+year|yoy)\b"),
]

_VALID_GRANULARITIES = frozenset({"day", "week", "month", "quarter", "year"})


def detect_time_granularity(text: str) -> str | None:
    """Keyword-based granularity detection. Returns day|week|month|quarter|year|None."""
    lower = (text or "").lower()
    for gran, pat in _GRANULARITY_PATTERNS:
        if re.search(pat, lower):
            return gran
    # Chart-over-dates heuristics — daily buckets implied even without "day wise":
    # "dates on the x axis", "x axis I want dates", "dates of last 7 days"
    if (
        re.search(r"\bdates?\s+(?:on|in|at|for)\s+(?:the\s+)?x[\s-]*axis\b", lower)
        or re.search(r"\bx[\s-]*axis\b[^.?!]*\bdates?\b", lower)
        or re.search(r"\bdates?\s+(?:of|for)\s+(?:the\s+)?last\s+\d+\s+days\b", lower)
    ):
        return "day"
    # "7-day inflow chart/graph/trend" — N-day + a chart word implies daily buckets
    if re.search(r"\b\d+\s*-\s*day\b", lower) and re.search(
        r"\b(graph|chart|plot|trend|inflow|breakdown|histogram)\b", lower
    ):
        return "day"
    return None


class IntentClassifier:
    async def classify(
        self, text: str, conversation_history: Optional[list] = None, domain: str = "recruitment",
    ) -> IntentResult:
        # A follow-up ("what about last month", "now by region") carries almost no
        # signal on its own — without the prior turn, metrics/dimensions extract
        # empty and the message reads as fully vague. Give the classifier the
        # last turn so it can resolve pronouns/ellipsis against it.
        user_message = text
        if conversation_history:
            context_lines = []
            for turn in conversation_history[-2:]:
                role = turn.get("role", "user")
                content = (turn.get("content") or "").strip()
                if content:
                    context_lines.append(f"{role}: {content}")
            if context_lines:
                user_message = (
                    "Recent conversation (use it to resolve references like 'that', "
                    "'it', 'now by X', 'last month' in the current message):\n"
                    + "\n".join(context_lines)
                    + "\n\nCurrent message: " + text
                )

        raw = await bedrock_invoke(
            model_id=INTENT_CLASSIFIER_MODEL,
            system_prompt=build_system_prompt(normalize_domain(domain)),
            user_message=user_message,
            max_tokens=1024,
            temperature=0.1,
        )

        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"```$", "", raw).strip()

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {
                "intent_type": "SINGLE_VIZ",
                "confidence": 0.5,
                "entities": {"metrics": [], "dimensions": [], "time_range": None, "chart_type": None, "filters": []},
                "vagueness_score": 0.2,
                "followup_ref": None,
                "sub_intents": [],
                "output_mode": "chart",
                "reasoning": "Could not parse LLM response; defaulting to SINGLE_VIZ",
            }

        entities_raw = data.get("entities", {})

        time_range = None
        if entities_raw.get("time_range"):
            tr = entities_raw["time_range"]
            if isinstance(tr, dict):
                time_range = TimeRange(type=tr.get("type", "relative"), value=tr.get("value", ""))

        filters = []
        for f in entities_raw.get("filters", []):
            if isinstance(f, dict):
                filters.append(FilterCondition(column=f.get("column", ""), op=f.get("op", "="), value=f.get("value")))

        # Granularity: trust the LLM when it returned a valid value; otherwise
        # fall back to deterministic keyword detection on the raw text so
        # "month wise" is never silently dropped.
        gran_raw = entities_raw.get("time_granularity")
        granularity = gran_raw if gran_raw in _VALID_GRANULARITIES else None
        if granularity is None:
            granularity = detect_time_granularity(text)

        entities = IntentEntities(
            metrics=entities_raw.get("metrics", []),
            dimensions=entities_raw.get("dimensions", []),
            time_range=time_range,
            chart_type=entities_raw.get("chart_type"),
            filters=filters,
            time_granularity=granularity,
        )

        raw_sub_intents = data.get("sub_intents", [])
        sub_intents = [
            s if isinstance(s, str) else json.dumps(s)
            for s in raw_sub_intents
        ]

        return IntentResult(
            intent_type=data.get("intent_type", "SINGLE_VIZ"),
            confidence=float(data.get("confidence", 0.5)),
            entities=entities,
            vagueness_score=float(data.get("vagueness_score", 0.5)),
            followup_ref=data.get("followup_ref"),
            sub_intents=sub_intents,
            reasoning=data.get("reasoning", ""),
            output_mode=(data.get("output_mode") or "chart").lower().strip(),
        )
