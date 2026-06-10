import json
import re
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
from shared.schemas.agent import IntentResult, IntentEntities, TimeRange, FilterCondition

INTENT_CLASSIFIER_MODEL = BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """You are an intent classification system for a data visualization platform.
Analyze the user's message and extract structured information.

INTENT TYPES (choose exactly one):
- SINGLE_VIZ: User wants one chart/visualization. Signals: "show me", "chart", "graph", "visualize", metric/dimension words.
- DASHBOARD: User wants multiple charts, a full dashboard, or an overview. Signals: "dashboard", "overview", "summary", "report", multiple different metric words in one request.
- SCREENSHOT: User uploaded an image (file attachment detected). Always SCREENSHOT if files are present.
- FOLLOWUP: User is referring to a prior result with pronouns. Signals: "it", "that", "this chart", "the graph", "filter", "drill", "refine", "update", "change it", "why did it".

ENTITY TYPES to extract:
- metrics: numeric measure words (revenue, sales, count, orders, churn, rate, total, average)
- dimensions: grouping/category words (region, product, category, country, status, month, year, user)
- time_range: date references ("last quarter", "this year", "2024", "past 30 days") -> normalize to {type: "relative"|"absolute", value: str}
- chart_type: explicit chart requests (bar, line, pie, donut, scatter, kpi, multi_row_card, table, area, funnel, gauge, treemap, waterfall, slicer) -> null if not specified
  NOTE: use "multi_row_card" when the user wants a KPI card with MULTIPLE values broken down by a category (e.g. "count by region", "jobs per type"). Use "kpi" only for a single aggregate number.
  NOTE: use "slicer" when the user wants a filter control, dropdown slicer, or checkbox filter that will filter other charts on the page.
- filters: explicit filter conditions [{column, op, value}]

VAGUENESS SCORE (0.0 to 1.0):
- 0.0-0.3: fully vague ("show me something interesting")
- 0.3-0.6: partially specified ("show revenue by region")
- 0.6-0.9: mostly specified ("bar chart of monthly revenue")
- 0.9-1.0: fully specified (exact SQL intent)

Return ONLY valid JSON:
{
  "intent_type": "SINGLE_VIZ",
  "confidence": 0.0,
  "entities": {
    "metrics": [],
    "dimensions": [],
    "time_range": null,
    "chart_type": null,
    "filters": []
  },
  "vagueness_score": 0.0,
  "followup_ref": null,
  "sub_intents": [],
  "reasoning": "one sentence"
}"""


class IntentClassifier:
    async def classify(self, text: str) -> IntentResult:
        raw = await bedrock_invoke(
            model_id=INTENT_CLASSIFIER_MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_message=text,
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

        entities = IntentEntities(
            metrics=entities_raw.get("metrics", []),
            dimensions=entities_raw.get("dimensions", []),
            time_range=time_range,
            chart_type=entities_raw.get("chart_type"),
            filters=filters,
        )

        return IntentResult(
            intent_type=data.get("intent_type", "SINGLE_VIZ"),
            confidence=float(data.get("confidence", 0.5)),
            entities=entities,
            vagueness_score=float(data.get("vagueness_score", 0.5)),
            followup_ref=data.get("followup_ref"),
            sub_intents=data.get("sub_intents", []),
            reasoning=data.get("reasoning", ""),
        )
