# Intent Classifier Skills

Model: `BEDROCK_HAIKU_MODEL` (configurable via `BEDROCK_HAIKU_MODEL_ID` env var)

## Skills

### classify_intent
Classifies the user's natural-language message into one of four intent types:
- `SINGLE_VIZ` — one chart or metric
- `DASHBOARD` — multiple charts / overview
- `SCREENSHOT` — image/file attached
- `FOLLOWUP` — references prior result

Returns a structured `IntentResult` with confidence, vagueness score, and extracted entities.

**Script:** `scripts/classify_intent.py`

---

### extract_entities
Extracts structured entities from the user's text:
- **metrics** — numeric measure words (revenue, count, churn …)
- **dimensions** — grouping words (region, product, month …)
- **time_range** — date references normalised to `{type, value}`
- **chart_type** — explicit chart name (bar, line, pie …) or null
- **filters** — explicit conditions `[{column, op, value}]`

**Script:** `scripts/extract_entities.py`
