# Query Agent Skills

Model: `BEDROCK_SONNET_MODEL` (configurable via `BEDROCK_SONNET_MODEL_ID` env var)

## Skills

### generate_sql_from_intent
Given a classified `IntentResult` and the project's semantic schema document, generates a
`QueryPlan` with:
- `sql` — a read-only SELECT/WITH query
- `chart_type` — recommended visualisation type
- `x_axis_label`, `y_axis_label`, `title`
- `limit` — row cap (default 500)

Respects the SQL sandbox: SELECT/WITH only, no DDL/DML.

**Script:** `scripts/generate_sql_from_intent.py`

---

### generate_sql_from_chart_spec
Given a vision-produced `chart_spec` dict (type, title, axis labels, estimated values) and the
live schema, generates SQL that produces data matching the chart visible in the screenshot.

Accepts optional `retry_feedback` (validator critique) and `hint_response` (user hint text)
to refine the query on retry attempts.

**Script:** `scripts/generate_sql_from_chart_spec.py`
