"""Intelligence Report Copilot agent — intelligence_chat_agent.py

FORKED FROM agent_service/agents/chat_agent.py on 2026-06-18.

This is a deliberate, standalone copy of ChatAgent that powers ONLY the
"Report Copilot" on the intelligence page. The Canvas Assistant keeps using
chat_agent.ChatAgent. They started byte-identical so functionality matches
exactly; this fork exists so the Report Copilot's prompt / chart rules /
behaviour can diverge later WITHOUT touching the canvas builder.

What is intentionally NOT forked (shared infrastructure, imported as-is):
  • shared.bedrock_client      — LLM transport (streaming + non-streaming)
  • agent_service.agents.schema_cache — enriched schema cache
  • query_executor / render_service    — via the router's httpx calls

Isolation from the canvas chat:
  • Redis history namespace:  intel_chat:history:{session_id}  (NOT chat:history:)
  • Distinct log prefix:      [intel_chat_agent]
"""
import json
import os
import re
from typing import Optional, TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL, BEDROCK_OPUS_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

INTEL_CHAT_MODEL = BEDROCK_SONNET_MODEL
INTEL_CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours
# Separate Redis namespace so Report-Copilot conversations never collide with
# the Canvas Assistant's. Grep the logs for this to confirm the forked path runs.
INTEL_HISTORY_PREFIX = "intel_chat:history:"

# In-memory fallback when Redis is unavailable (separate dict from chat_agent's).
_intel_memory_history: dict[str, list[dict]] = {}
# Distilled conversation memory (gist of prior questions), Redis-less fallback.
_intel_memory_summary: dict[str, list[str]] = {}
_MEMORY_MAX = 20  # max remembered prior-question gists

print("[intel_chat_agent] module loaded — Report Copilot agent (forked from chat_agent)", flush=True)

# ─── System prompt (forked copy — safe to diverge from the canvas builder) ─────
_SYSTEM_PROMPT_TEMPLATE = """You are the Report Copilot, a conversational data analyst embedded in the intelligence report view of a BI platform called Visually.
You have full access to the user's live database and their complete multi-page canvas report.
The DATABASE SCHEMA and your CURRENT CANVAS REPORT are supplied as additional context blocks below — read them before answering.

CAPABILITIES:
1. Answer questions about any chart or data across all canvas pages.
2. Query any table in the connected database — write and execute SQL on demand.
3. Generate new chart visualizations inline in this conversation.
4. Explain trends, anomalies, and patterns in plain English.
5. Add charts to the current active page or suggest placements across pages.

CHART CREATION GUIDELINES:
- Prefer tables already in use on the canvas (listed as PRIORITY TABLES) — they are pre-verified and relevant.
- You may query any other table in the schema when the user's request requires it.
- When creating a chart for a specific page, mention the page name in your response.

══════════════════════════════════════════════════════════════════════
DATA QUESTIONS — MANDATORY EXECUTION PROTOCOL
══════════════════════════════════════════════════════════════════════
When the user asks a question that requires fetching data (e.g. "what was X",
"how many Y", "show me Z", "find", "list", "who had the most", "total", etc.):

  STEP 1 — Write 1–2 sentences BEFORE the block: restate what the user is asking
           and say what the result will show (which table/columns, any filter or
           time range applied). Do NOT state numbers — the data is fetched after.
  STEP 2 — IMMEDIATELY output a sql_execute block to fetch the answer.
           Use chart_type "table" for row-level results, "kpi" for a single number,
           "multi_row_card" for ranked/grouped numbers.
  STEP 3 — STOP after the block. (Text after the block is discarded — explain in STEP 1.)

❌ FORBIDDEN — these responses FAIL the user:
   "I'll query the billing hours for Scarbrough Medlin in 2023."  ← no block = WRONG
   "Let me look up that data for you."  ← no block = WRONG

✅ CORRECT:
   "You want Scarbrough Medlin's billing hours for 2023. I'm pulling every billing
   record for that client filtered to 2023 and listing the hours per entry:"
   ```sql_execute
   {{"sql": "SELECT ...", "chart_type": "table", "title": "Billing Hours - Scarbrough Medlin 2023", "x_label": "", "y_label": ""}}
   ```

══════════════════════════════════════════════════════════════════════
CHART CREATION — MANDATORY EXECUTION PROTOCOL
══════════════════════════════════════════════════════════════════════
When the user asks to CREATE / BUILD / GENERATE / SHOW / MAKE / ADD
a chart, table, graph, visualization, or KPI:

  STEP 1 — Write a short, helpful explanation (2–4 sentences) BEFORE the block:
           • Restate what the user asked for, in your own words.
           • Say what the chart will show and how to read it: the metric being
             measured, how it is grouped (the grain), any filter or time range
             applied, and one line on why this chart type fits the question.
           Do NOT invent specific numbers or findings — the data is fetched
           AFTER this step, so describe the chart's intent, not its results.
  STEP 2 — IMMEDIATELY output the sql_execute block below the explanation.
  STEP 3 — STOP after the block. (Any text you write AFTER the block is discarded
           and never shown — put all explanation BEFORE the block, in STEP 1.)

❌ ABSOLUTELY FORBIDDEN — these responses will FAIL the user:
   Any response that describes a chart WITHOUT a sql_execute block.
   "I'll create a bar chart that visualizes..."  ← no block = WRONG
   Putting the explanation AFTER the block (it gets discarded).

✅ CORRECT — explanation first, then the block:
   "You asked for a breakdown of placements by current status. This pie chart
   groups every placement by its status value and shows each status as a share
   of the whole, so you can see at a glance which statuses dominate. A pie fits
   because the parts add up to a meaningful total."
   ```sql_execute
   {{"sql": "...", "chart_type": "pie", ...}}
   ```

BEFORE you write your response, ask yourself:
  - Does it contain a sql_execute block? If NO → you are WRONG. Add one.
  - Is the explanation BEFORE the block (not after)? If after → move it before.
  - Did I state actual numbers I don't have yet? If YES → remove them.

══════════════════════════════════════════════════════════════════════
SQL CORRECTNESS — AVOID EMPTY RESULTS (these show as "N/A" = a FAILURE)
══════════════════════════════════════════════════════════════════════
A query that matches no rows renders as "N/A". Prevent it:
  - TEXT / NAME filters: NEVER assume the exact stored spelling. Use case-insensitive
    partial matching — WHERE col ILIKE '%Scarbrough Medlin%'  (NOT col = 'Scarbrough Medlin').
    Stored values often differ in case, punctuation, or suffixes (e.g. ", LLC", " Inc").
  - YEAR / date filters: filter the table's real date column —
    EXTRACT(YEAR FROM date_col) = 2023   or   date_col >= '2023-01-01' AND date_col < '2024-01-01'.
  - Choose the column whose [semantic_type], name, description, or sample values best
    match the words the user used; do not guess a column that may not hold that value.
  - For a single-number KPI, guard against NULL so an empty match still returns a number:
    SELECT COALESCE(SUM(hours), 0) AS "Total Billable Hours".
  - PICK THE RIGHT ENTITY TABLE. A COMPANY / FIRM / AGENCY / CLIENT / "parent" name
    (e.g. "Scarbrough Medlin", "Marsh McLennan") lives in the CLIENT/COMPANY table's name
    column (such as a *client_corporation.parentname) — NOT in a candidate/person name
    column. A PERSON's name lives in the candidate/employee table. Match the named entity
    to the table whose grain and sample values fit it; for organisation-sounding names,
    prefer the company/client table and join to fact tables via its id (e.g.
    clientcorporationid), not via a candidate id.
  - JOIN ON IDS DIRECTLY: write `x_id IN (SELECT y_id FROM ...)`. NEVER wrap join keys in
    CONCAT / LOWER / CAST gymnastics like LOWER(CONCAT(id::text)) — it is unnecessary and errors.

SQL DIALECT RULES:
  - The target engine is named on the "SQL DIALECT" line at the top of the schema — every
    query MUST be valid for that engine.
  - Amazon Redshift: CONCAT() takes EXACTLY TWO arguments — never call CONCAT() with a single
    argument. Use the || operator for string concatenation (a || b || c). On MySQL use
    CONCAT(a, b, ...) instead (MySQL has no || string operator).
  - Use ILIKE for case-insensitive matching (PostgreSQL / Redshift); on MySQL use LOWER(col) LIKE.

If the user provides DAX/Power BI-style formulas (SUMX, HASONEFILTER, DISTINCTCOUNT, etc.),
translate them to SQL equivalents:
  DISTINCTCOUNT(table[col]) → COUNT(DISTINCT col)
  SUMX(table, expr)          → SUM(expr) or GROUP BY computation
  HASONEFILTER(table[col])   → omit — just write the aggregate directly
  [Measure] * [Other]        → col * other_col
If the user specifies column aliases like "Name : name", use AS "Name" in SELECT.
For multi-table requests: use JOIN. Match columns to their tables by name.

COLUMN LABELS — PRESERVE THE REAL COLUMN NAME (do NOT invent new names):
  When you SELECT an existing column, alias it to the SAME column name, cleaned ONLY for display —
  convert snake_case / camelCase / lowercase to spaced Title Case and fix capitalization & obvious
  spelling. KEEP the original words; never substitute a different business term or concept.
    ✅ first_name → AS "First Name"   ✅ clientcorporationid → AS "Client Corporation Id"   ✅ city → AS "City"
    ❌ region → AS "Sales Territory"   ❌ status → AS "Pipeline Stage"   ❌ amount → AS "Revenue"
  Aggregates are labelled by the operation on the cleaned column name — SUM(amount) AS "Total Amount",
  AVG(rate) AS "Average Rate", COUNT(*) AS "Count". Use a DIFFERENT alias only when the user explicitly asks for it.
══════════════════════════════════════════════════════════════════════

MULTI-CHART RESPONSES:
If the user asks for MULTIPLE charts/KPIs in one message (e.g. "create 3 KPIs", "show a bar and a pie", "give me a dashboard with sales, revenue, and status"), output MULTIPLE sql_execute blocks — one per chart. Each block must be complete and independently valid.

Example — two charts at once:
User: "Create a bar chart for monthly placements and a KPI for total count"
Response: "You asked for two views of placements: a monthly trend and an overall total. The bar chart counts placements per month so you can see how volume rises and falls over time, and the KPI shows the single all-time total for quick reference."
```sql_execute
{{"sql": "SELECT DATE_TRUNC('month', start_date) AS \"Month\", COUNT(*) AS \"Placements\" FROM bullhorn_core_placement GROUP BY 1 ORDER BY 1", "chart_type": "bar_vertical", "title": "Monthly Placements", "x_label": "Month", "y_label": "Placements"}}
```
```sql_execute
{{"sql": "SELECT COUNT(*) AS \"Total Placements\" FROM bullhorn_core_placement", "chart_type": "kpi", "title": "Total Placements", "x_label": "", "y_label": ""}}
```

RESPONSE FORMAT:
For data questions (what, how many, show, find, list, total, etc.): 1–2 explanatory sentences + sql_execute block (see DATA QUESTIONS above).
For chart/table/KPI creation: a 2–4 sentence explanation (what was asked + what the chart shows and how to read it) + sql_execute block(s) (see CHART CREATION above).
Always put the explanation BEFORE the block — text after the block is never shown.
For conversational questions (greetings, explanations, "what is X concept"): plain English only, no sql_execute.

```sql_execute
{{"sql": "SELECT ...", "chart_type": "bar_vertical|line|pie|kpi|multi_row_card|scatter|table|waterfall|area|donut|slicer", "title": "Chart Title", "x_label": "...", "y_label": "..."}}
```

CHART TYPE SELECTION RULES:
- Use "pie" for proportional distributions (status breakdown, category share, etc.)
- Use "kpi" ONLY for a single aggregate number (one row, one value): SELECT COUNT(*) AS value FROM table
- Use "multi_row_card" when the user wants a KPI broken down by a dimension — multiple label/value pairs:
    SQL pattern: SELECT dim AS label, COUNT(*)/SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 20
- Use "table" for detailed row-level data with many columns
- Use "bar_vertical" when the user wants to compare values visually

CHART CREATION EXAMPLES — copy these patterns exactly (note the 2–4 sentence explanation BEFORE each block):

Example 1 — pie chart by specific columns (user names table + columns):
User: "Create a PieChart for current status. Table: bullhorn_core_placement. Columns: placementID, status."
Response: "You asked for a breakdown of placements by their current status. This pie chart counts the distinct placements in each status and shows every status as a slice of the whole, so you can see which statuses are most common at a glance. A pie fits here because the statuses are mutually exclusive parts of one total."
```sql_execute
{{"sql": "SELECT status AS \"Status\", COUNT(DISTINCT \"placementID\") AS \"Count\" FROM bullhorn_core_placement GROUP BY status ORDER BY 2 DESC LIMIT 20", "chart_type": "pie", "title": "Current Status", "x_label": "Status", "y_label": "Count"}}
```

Example 2 — table chart:
User: "Create a table chart showing employee name and salary"
Response: "You want a list of employees alongside their salaries. This table pulls each employee's name and salary and sorts it from highest to lowest pay, so the top earners sit at the top. A table is the right choice because you're after exact row-level values rather than a trend or proportion."
```sql_execute
{{"sql": "SELECT name AS \"Name\", salary AS \"Salary\" FROM employees ORDER BY salary DESC LIMIT 1000", "chart_type": "table", "title": "Employee Salaries", "x_label": "", "y_label": ""}}
```

Example 3 — grouped KPI (multi_row_card):
User: "Show job count broken down by source type" or "KPI showing jobs per category"
Response: "You asked how jobs are distributed across source types. This card groups every job by its source and shows the count for each as a ranked list, so the biggest sources stand out first. A multi-row card fits because you want one number per category rather than a single overall total."
```sql_execute
{{"sql": "SELECT source AS \"Source\", COUNT(*) AS \"Count\" FROM jobs GROUP BY source ORDER BY 2 DESC LIMIT 20", "chart_type": "multi_row_card", "title": "Job Count by Source", "x_label": "Source", "y_label": "Count"}}
```

Example 4 — slicer / filter widget:
User: "Add a filter for status" / "Create a dropdown to filter by region" / "Add a checkbox slicer for category"
Response: "Here's a Status slicer:"
```sql_execute
{{"sql": "SELECT DISTINCT status FROM jobs WHERE status IS NOT NULL ORDER BY 1 LIMIT 300", "chart_type": "slicer", "title": "Status Filter", "slicer_type": "dropdown", "slicer_column": "status", "x_label": "", "y_label": ""}}
```
NOTE for slicers:
- chart_type must be "slicer"
- slicer_type: "dropdown" (single value), "checkbox" (multi-select), or "date_range" (date picker)
- slicer_column: the exact column name being filtered (must match column in other widgets' queries)
- sql must be SELECT DISTINCT <column> ... so the slicer can populate its option list
- The slicer will automatically filter all other charts on the page when the user selects a value
- Use "checkbox" when the user says "multi-select", "multiple", or "checkboxes"
- Use "date_range" when filtering by a date/timestamp column

For dashboard modifications, include:
```dashboard_action
{{"action": "filter_widget"|"add_widget"|"rename_widget", "params": {{}}}}
```

WHEN THE USER MESSAGE CONTAINS AN [INTELLIGENCE REPORT] OR ╔══ BLOCK:
The user has already provided the full pre-computed report data inline.
- Answer IMMEDIATELY with specific numbers from that data block.
- NEVER say "I'll analyze", "I would show you", "I'll look into", or "Let me check".
- If the answer is in the data, cite the exact numbers right away.
- Only generate SQL if the question explicitly asks for something NOT present in the provided data.

WHEN THE USER PASTES A PRE-COMPUTED NARRATIVE (long paragraph with specific dollar amounts and/or percentages):
If the user's message is a long prose passage (more than ~300 characters) that already contains specific
formatted values — such as "$68,600,000", "90%", "16%", "$217,800" — it IS pre-computed report data they
are sharing for discussion, NOT a request to query the database.
- Treat it exactly like an [INTELLIGENCE REPORT] block: answer DIRECTLY from the numbers in the pasted text.
- NEVER say "I'll verify", "let me check", "I'll query", or "I'll look into" — the data is already present.
- Do NOT generate SQL or a chart unless the user's message ALSO contains an EXPLICIT follow-up question
  that asks for something not already covered by the pasted figures.
- If there is no follow-up question, summarise the key takeaways or ask "What would you like to explore further?"

══════════════════════════════════════════════════════════════════════
WHY / EXPLAIN / ROOT-CAUSE QUESTIONS — MANDATORY DRILL-DOWN PROTOCOL
══════════════════════════════════════════════════════════════════════
When the user asks WHY a number is what it is, asks you to EXPLAIN a metric or trend,
or asks HOW a result was reached (e.g. "why is TTM revenue $68M?", "explain the churn",
"what is driving this?", "how did we get this number?", "break this down", "justify this"):

  STEP 1 — Acknowledge what metric is being discussed (1 sentence).
  STEP 2 — Generate SQL to DRILL DOWN into the underlying drivers.
            The goal is to answer "why" — so decompose the metric:
              • Break it down by the most useful dimension (segment, region, time, tier, status, etc.)
              • Show top contributors (ORDER BY value DESC LIMIT 10–20)
              • If time-based, show the trend (GROUP BY month/quarter)
            Use chart_type "bar_vertical" or "multi_row_card" for breakdowns,
            "line" for trends, "table" for row-level detail.
  STEP 3 — The explanation must come BEFORE the sql_execute block (never after).

❌ WRONG — these fail the user:
   "The TTM lost revenue is $68M because of churn."  ← no sql = no real answer
   "I'll investigate the reasons for you."  ← promise without delivery

✅ CORRECT:
   "You want to understand what is driving the $68.6M in TTM lost business revenue.
   Here's a breakdown by client segment so you can see which segments contributed most
   to the churn:"
   ```sql_execute
   {{"sql": "SELECT segment AS \"Segment\", SUM(lost_revenue) AS \"Lost Revenue\" FROM ...", "chart_type": "bar_vertical", ...}}
   ```

══════════════════════════════════════════════════════════════════════
ACCOUNTABILITY — OWNING AND CORRECTING MISTAKES
══════════════════════════════════════════════════════════════════════
If a previous answer in this conversation was wrong — either you are aware of the error,
or the user points one out (e.g. "you said X but the correct value is Y", "that number
is wrong", "your chart showed 740 but the report says $68M", "that's not right") —
you MUST:

  1. ACKNOWLEDGE THE MISTAKE DIRECTLY and specifically:
       ✅ "You're right — I made an error. I queried a COUNT of records (740 rows)
           instead of the SUM of revenue. That's incorrect."
       ❌ "I apologise for any confusion." ← vague, not accountable
       ❌ "The data may vary depending on filters." ← deflecting
  2. STATE exactly what went wrong (wrong column, wrong table, wrong aggregation, etc.).
  3. IMMEDIATELY generate the corrected SQL to fetch the right answer.
  4. NEVER repeat the same mistake — if you counted rows when you should have summed a
     revenue column, your corrected query must SUM the revenue column.

IMPORTANT: When the user provides the correct value themselves (e.g. "$68,600,000"),
treat that as ground truth. Do not argue or re-query to "verify" it — accept it,
acknowledge the error, and only generate SQL if the user asks for further breakdown.

TONE: Clear, helpful, and data-focused. For charts/tables/KPIs, always explain the request and what the chart shows in 2–4 sentences BEFORE the block (never after it). Reference actual values when the data is already provided; avoid empty filler phrases."""

# ── Prompt zones (see chat_agent caching design) ──────────────────────────────
# ZONE 1 — instructions. Message-INDEPENDENT, byte-stable → part of the cached
# prefix. `.format()` with no args unescapes the doubled `{{ }}` in the JSON
# examples without substituting anything (there are no placeholders left).
_INSTRUCTIONS = _SYSTEM_PROMPT_TEMPLATE.format()

_SAMPLE_VALUE_LIMIT = int(os.getenv("INTEL_CHAT_SAMPLE_VALUE_LIMIT", "5"))
_COL_DESC_MAX = int(os.getenv("INTEL_CHAT_COL_DESC_MAX", "80"))
_TABLE_DESC_MAX = int(os.getenv("INTEL_CHAT_TABLE_DESC_MAX", "160"))

# ── Widget-table extraction helpers ───────────────────────────────────────────
_WIDGET_TABLE_RE = re.compile(r'\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_.]*)', re.IGNORECASE)
_WIDGET_CTE_RE   = re.compile(r'\b([a-zA-Z_]\w*)\s+AS\s*\(', re.IGNORECASE)


def _extract_widget_tables(sql: str) -> list:
    """Return real table names referenced in widget SQL (excludes CTE aliases)."""
    sql = sql or ''
    ctes = {m.lower() for m in _WIDGET_CTE_RE.findall(sql)}
    seen: set = set()
    out: list = []
    for m in _WIDGET_TABLE_RE.findall(sql):
        ml = m.lower()
        if ml.split('.')[-1] in ctes or ml in seen:
            continue
        seen.add(ml)
        out.append(m)
    return out


def _format_verified_tables(doc: dict) -> str:
    """Render the VERIFIED TABLES block that is injected into the system prompt.

    Structure of each entry in `doc`:
      { "used_by_widgets": [...], "columns": [...], "source": "report_metadata|enriched_schema|live_fetch" }
    """
    if not doc:
        return ""
    lines = [
        "══════════════════════════════════════════════════════════════════════",
        "VERIFIED TABLES — authoritative mapping built from this report's actual SQL.",
        "These are the REAL tables backing each widget. ALWAYS query from this list first.",
        "Do NOT invent or assume alternative table names — every widget's source table is listed here.",
        "══════════════════════════════════════════════════════════════════════",
    ]
    for tbl in sorted(doc.keys()):
        info      = doc[tbl]
        widgets   = info.get("used_by_widgets") or []
        cols      = info.get("columns") or []
        w_str     = ", ".join(f'"{w}"' for w in widgets[:12])
        lines.append(f"\n[{tbl}]" + (f"  →  used by: {w_str}" if w_str else ""))
        if cols:
            lines.append(f"  Columns: {', '.join(str(c) for c in cols[:50])}")
        src = info.get("source", "")
        if src == "live_fetch":
            lines.append("  (columns fetched live from DB — authoritative)")
        elif src == "report_metadata":
            lines.append("  (columns from saved report metadata — authoritative)")
    lines.append(
        "\nWhen writing SQL for any widget listed above, use the exact table name shown "
        "and only the columns listed. For any widget NOT listed, use the schema below."
    )
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    """Trim text to `limit` chars (limit<=0 disables trimming)."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


# Memoised zone-2 schema maps, keyed by (connection_id, schema_hash). Distinct
# dict from chat_agent's so the two forks never share mutable state.
_intel_schema_map_cache: dict[str, str] = {}


def _is_categorical_col(c: dict) -> bool:
    """Sample values only help for low-cardinality / categorical columns.
    They are noise (and pure prompt bloat) for ids, numerics, dates, and free text."""
    sem = (c.get("semantic_type") or "").lower()
    if sem in {"dimension", "category", "categorical", "enum", "status", "boolean"}:
        return True
    if sem in {"metric", "measure", "id", "identifier", "date", "datetime", "timestamp"}:
        return False
    ctype = (c.get("type") or "").lower()
    cname = (c.get("name") or "").lower()
    if cname == "id" or cname.endswith("_id") or cname.endswith("id"):
        return False
    if any(k in ctype for k in ("int", "numeric", "decimal", "float", "double", "real",
                                "money", "serial", "date", "time", "timestamp")):
        return False
    if any(k in ctype for k in ("char", "text", "string", "bool", "enum", "uuid")):
        return True
    return True  # unknown type → keep samples (favour accuracy)


_CHART_CREATION_KEYWORDS = {
    "create", "make", "build", "generate", "add", "show", "give",
    "chart", "graph", "pie", "bar", "line", "kpi", "table", "visual",
    "plot", "donut", "scatter", "funnel", "treemap", "waterfall",
}

# Detects a formatted dollar amount: $1,234  $68,600,000  $15.8M  $2.3B  $500K
_DOLLAR_RE = re.compile(r'\$[\d,]+(?:\.\d+)?\s*[MBKmk]?\b')
# Detects an explicit percentage: 16%  9.2%  90%
_PCT_RE    = re.compile(r'\d+(?:\.\d+)?%')


def _is_provided_data_block(message: str) -> bool:
    """Return True when the message looks like a pre-computed narrative the user is
    sharing for discussion — not a question asking us to fetch data.

    A block is recognised when ALL of the following are true:
      1. Long message (>300 chars) — short questions don't qualify.
      2. Contains at least two specific dollar amounts OR two percentages — these are
         hallmarks of pre-computed report text, not a query.
      3. Does NOT start with a recognised question/command word — it reads as prose,
         not as an imperative or interrogative directed at the database.
    """
    if len(message) < 300:
        return False
    if len(_DOLLAR_RE.findall(message)) < 2 and len(_PCT_RE.findall(message)) < 2:
        return False
    question_starts = (
        "what ", "how ", "which ", "who ", "when ", "where ", "why ",
        "show me", "find ", "list ", "get me", "give me", "tell me",
        "create ", "make ", "build ", "generate ", "add ", "fetch ",
    )
    lower_strip = message.lower().strip()
    return not any(lower_strip.startswith(s) for s in question_starts)


def _is_chart_creation_request(message: str) -> bool:
    """Return True when the message is asking for chart/viz creation."""
    if _is_provided_data_block(message):
        return False
    words = set(re.sub(r"[^a-z0-9 ]", " ", message.lower()).split())
    action_words = {"create", "make", "build", "generate", "add", "give", "draw", "produce"}
    subject_words = {"chart", "graph", "pie", "bar", "kpi", "table", "visual",
                     "visualization", "plot", "donut", "scatter", "funnel", "treemap", "waterfall"}
    has_action = bool(words & action_words)
    has_subject = bool(words & subject_words)
    return has_action and has_subject


def _is_data_query_request(message: str) -> bool:
    """Return True when the message is asking a data question that requires SQL.

    Also covers "why/explain/drill-down" requests: even when the user already has a
    number in front of them, asking WHY it is that value or to EXPLAIN it requires
    generating SQL to show the underlying breakdown/drivers.
    """
    if _is_provided_data_block(message):
        return False
    lower = message.lower()
    question_starters = (
        "what", "how many", "how much", "which", "who", "when", "where",
        "show me", "find", "list", "get me", "give me", "tell me",
        "fetch", "retrieve", "calculate", "compute", "sum", "count",
        "total", "average", "top", "bottom", "highest", "lowest",
        "most", "least", "compare", "breakdown", "analyse", "analyze",
        # WHY / EXPLAIN / ROOT-CAUSE triggers
        "why", "explain", "reason", "cause", "what caused", "what is driving",
        "what's driving", "how did", "how come", "drill down", "drill into",
        "break down", "break this", "justify", "what led", "root cause",
    )
    return any(lower.strip().startswith(s) or f" {s} " in lower for s in question_starters)


# Default reach for report-scoped mode: how many FK hops out from the report's
# own tables we still consider "related" and worth surfacing to the copilot.
INTEL_SCOPE_HOPS = int(os.getenv("INTEL_SCOPE_HOPS", "2"))


def _nhop_neighbors(edges: dict, seed: set, hops: int) -> set:
    """BFS the FK adjacency graph `edges` outward from `seed` up to `hops` levels.
    Returns the reachable nodes EXCLUDING the seed itself. Used by report-scoped
    mode to pull in tables joinable to the report's own tables."""
    visited = set(seed)
    frontier = set(seed)
    for _ in range(max(0, hops)):
        nxt = set()
        for node in frontier:
            for neighbor in (edges.get(node) or {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    nxt.add(neighbor)
        frontier = nxt
        if not frontier:
            break
    return visited - set(seed)


class IntelligenceChatAgent:
    """Standalone Report-Copilot agent. Mirrors ChatAgent's public surface
    (prepare / respond / parse_raw / retry_for_sql / *_history) so the
    intelligence_chat router can drive it exactly like chat.py drives ChatAgent."""

    def _dialect_label(self, enriched: "EnrichedSchema") -> str:
        dialect = (enriched.db_type or "").lower()
        return {
            "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
            "redshift": "Amazon Redshift", "mysql": "MySQL",
        }.get(dialect, enriched.db_type or "SQL")

    def _render_table_detail(self, enriched: "EnrichedSchema", t: dict) -> list[str]:
        """Full per-table detail block (description, grain, columns, sample values).
        Shared by the full-DB builder and the report-scoped builder so both render a
        table identically."""
        lines: list[str] = []
        tname = t.get("name", "")
        desc = _clip(t.get("description") or "", _TABLE_DESC_MAX)
        row_count = t.get("row_count")
        row_hint = f"  ~{row_count:,} rows" if row_count else ""
        lines.append(f"\n[{tname}]{row_hint}")
        if desc:
            lines.append(f"  {desc}")

        sem = enriched.table_semantics.get(tname, {})
        grain = sem.get("grain") or ""
        use_for = sem.get("use_for") or []
        never_use = sem.get("never_use_for") or []
        if grain:
            lines.append(f"  Grain: {grain}")
        if use_for:
            lines.append(f"  Use for: {', '.join(use_for)}")
        if never_use:
            lines.append(f"  Never use for: {', '.join(never_use)}")

        cols = t.get("columns") or []
        col_parts: list[str] = []
        sample_parts: list[str] = []
        for c in cols:
            cname = c.get("name") or ""
            ctype = c.get("type") or ""
            cdesc = _clip(c.get("description") or "", _COL_DESC_MAX)
            sem_type = c.get("semantic_type") or ""
            tag = f"[{sem_type}]" if sem_type else ""
            col_parts.append(f"{cname}{tag} ({ctype}){': ' + cdesc if cdesc else ''}")

            stats = c.get("stats") or {}
            top_vals = stats.get("top_values") or []
            if top_vals and _is_categorical_col(c):
                sample_strs = []
                for rv in top_vals[:_SAMPLE_VALUE_LIMIT]:
                    if isinstance(rv, dict):
                        val = rv.get(cname) or next(iter(rv.values()), None)
                    else:
                        val = rv
                    if val is not None:
                        sample_strs.append(str(val))
                if sample_strs:
                    sample_parts.append(f"{cname}: [{', '.join(sample_strs)}]")

        if col_parts:
            lines.append(f"  Columns: {' | '.join(col_parts)}")
        if sample_parts:
            lines.append(f"  Sample values: {' | '.join(sample_parts)}")
        return lines

    def _join_condition_lines(
        self, enriched: "EnrichedSchema", only: Optional[set] = None
    ) -> list[str]:
        """Deduplicated JOIN CONDITIONS block. When `only` is given, keep an edge
        only if BOTH endpoints are in that set (used by report-scoped mode)."""
        join_hints: list[str] = []
        seen_edges: set[frozenset] = set()
        for tbl_a in sorted(enriched.relationship_graph.edges.keys()):
            for tbl_b, condition in enriched.relationship_graph.edges[tbl_a].items():
                if only is not None and (tbl_a not in only or tbl_b not in only):
                    continue
                edge_key = frozenset([tbl_a, tbl_b])
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    join_hints.append(f"  {condition}")
        if not join_hints:
            return []
        return ["\nJOIN CONDITIONS:", *join_hints]

    def _build_cached_schema(self, enriched: "EnrichedSchema") -> str:
        """ZONE 2 (cached) — FULL detailed schema for EVERY table, in deterministic
        (alphabetical) order so the block is byte-stable and message-INDEPENDENT.
        Used by FULL-DB ("database") scope."""
        compact = sorted(enriched.compact_tables or [], key=lambda x: x.get("name", ""))

        dialect_label = self._dialect_label(enriched)
        lines = [
            f"SQL DIALECT: {dialect_label} — every query you write MUST be valid {dialect_label} SQL.",
            f"DATABASE SCHEMA — {len(compact)} tables (the complete database; "
            f"you may query any of them):",
        ]
        for t in compact:
            lines.extend(self._render_table_detail(enriched, t))

        lines.extend(self._join_condition_lines(enriched))

        disambig = enriched.get_disambiguation_text()
        if disambig and disambig != "COLUMN DISAMBIGUATION (same column name, different meanings per table):":
            lines.append(f"\n{disambig}")

        return "\n".join(lines)

    # ── Report-scoped mode (default on the intelligence page) ──────────────────

    def resolve_scope_tables(
        self,
        enriched: "EnrichedSchema",
        priority_tables: Optional[set],
        hops: int = INTEL_SCOPE_HOPS,
    ) -> tuple[set, set]:
        """Map the report's own tables (priority_tables, derived from widget SQL) onto
        the enriched schema's qualified table names, then walk the FK graph `hops`
        levels out to collect related neighbours.

        Returns (seed_qualified, neighbor_qualified) — both restricted to tables that
        actually exist in compact_tables. seed is empty when nothing matched (caller
        then falls back to the full schema)."""
        pri = {p.lower() for p in (priority_tables or set())}
        compact_names = [t.get("name", "") for t in (enriched.compact_tables or []) if t.get("name")]
        compact_set = set(compact_names)

        def _matches(qn: str) -> bool:
            return qn.lower() in pri or qn.split(".")[-1].lower() in pri

        seed = {qn for qn in compact_names if _matches(qn)}
        if not seed:
            return set(), set()

        edges = enriched.relationship_graph.edges or {}
        neighbors = _nhop_neighbors(edges, seed, hops)
        neighbors = {n for n in neighbors if n in compact_set} - seed
        return seed, neighbors

    def _build_scoped_schema(
        self,
        enriched: "EnrichedSchema",
        seed: set,
        neighbors: set,
    ) -> str:
        """ZONE 2 (cached) for REPORT scope. Two detail tiers:
          • seed tables (used by the report)  → FULL detail (cols, samples, semantics)
          • neighbour tables (≤N FK hops away) → LIGHTWEIGHT (name + purpose + 1 join)
        Keeps the prompt small while still telling the model which other tables exist
        and how to reach them."""
        compact_by_name = {
            t.get("name", ""): t for t in (enriched.compact_tables or []) if t.get("name")
        }
        dialect_label = self._dialect_label(enriched)
        lines = [
            f"SQL DIALECT: {dialect_label} — every query you write MUST be valid {dialect_label} SQL.",
            "SCOPE: REPORT — answer using the tables/views that build THIS report. "
            "RELATED TABLES (listed after) are joinable and may be queried only when the "
            "question genuinely needs data beyond what the report tables hold.",
            f"\nREPORT TABLES — {len(seed)} table(s) used by this report's charts:",
        ]
        for tname in sorted(seed):
            t = compact_by_name.get(tname)
            if t:
                lines.extend(self._render_table_detail(enriched, t))

        if neighbors:
            lines.append(
                f"\nRELATED TABLES — {len(neighbors)} table(s) within "
                f"{INTEL_SCOPE_HOPS} join-hop(s) of the report's tables (names + how to join only):"
            )
            for tname in sorted(neighbors):
                t = compact_by_name.get(tname)
                sem = enriched.table_semantics.get(tname, {})
                purpose = sem.get("purpose") or (_clip(t.get("description") or "", _TABLE_DESC_MAX) if t else "") or ""
                lines.append(f"\n[{tname}]{(' ' + purpose) if purpose else ''}")
                # one join path back toward the report's own tables (or any scoped table)
                cond = None
                for s in sorted(seed):
                    cond = enriched.relationship_graph.get_join_condition(tname, s)
                    if cond:
                        break
                if cond:
                    lines.append(f"  Join: {cond}")

        # JOIN CONDITIONS limited to the scoped set (seed + neighbours)
        scoped = set(seed) | set(neighbors)
        lines.extend(self._join_condition_lines(enriched, only=scoped))

        return "\n".join(lines)

    def _build_schema_section_raw(self, schema_doc: dict) -> str:
        schema_parts = []
        for table in schema_doc.get("tables", [])[:12]:
            col_names = [c["name"] for c in table.get("columns", [])[:20]]
            schema_parts.append(
                f"Table: {table.get('schema', '')}.{table['name']} "
                f"({table.get('description', '')}) | Columns: {', '.join(col_names)}"
            )
        return "DATABASE SCHEMA:\n" + (
            "\n".join(schema_parts) if schema_parts else "Schema not available."
        )

    def _build_dashboard_context(
        self,
        dashboard_widgets: list[dict],
        dashboard_pages: list[dict],
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]] = None,
    ) -> str:
        if not dashboard_widgets:
            return "Canvas is empty — no charts yet."

        priority_tables = priority_tables or set()
        pages_map: dict[str, str] = {p["id"]: p["name"] for p in dashboard_pages if "id" in p}

        by_page: dict[str, list[dict]] = {}
        unassigned: list[dict] = []
        for w in dashboard_widgets:
            pid = w.get("page_id")
            if pid:
                by_page.setdefault(pid, []).append(w)
            else:
                unassigned.append(w)

        parts: list[str] = []

        if dashboard_pages:
            page_summary = []
            for p in sorted(dashboard_pages, key=lambda x: x.get("order", 0)):
                count = len(by_page.get(p["id"], []))
                marker = " [ACTIVE]" if p["id"] == active_page_id else ""
                page_summary.append(f"{p['name']} ({count} chart{'s' if count != 1 else ''}){marker}")
            total = len(dashboard_widgets)
            parts.append(
                f"CANVAS PAGES ({len(dashboard_pages)} pages, {total} total charts): "
                + " | ".join(page_summary)
            )

        ordered_pages = sorted(dashboard_pages, key=lambda x: x.get("order", 0))
        for p in ordered_pages:
            pid = p["id"]
            page_widgets = by_page.get(pid, [])
            if not page_widgets:
                parts.append(f"\nPage '{p['name']}' — empty")
                continue
            active_marker = " (ACTIVE — new charts go here)" if pid == active_page_id else ""
            parts.append(f"\nPage '{p['name']}'{active_marker}:")
            for w in page_widgets:
                tbls = _extract_widget_tables(w.get("sql_query") or "")
                tbl_hint = f" | tables: {', '.join(tbls)}" if tbls else ""
                parts.append(f"  • {w['title']} [{w['chart_type']}]{tbl_hint}")

        if unassigned:
            parts.append(f"\nUnassigned widgets ({len(unassigned)}):")
            for w in unassigned:
                parts.append(f"  • {w['title']} [{w['chart_type']}]")

        if priority_tables:
            parts.append(
                f"\nPRIORITY TABLES (used by existing charts — prefer these when building new ones): "
                f"{', '.join(sorted(priority_tables))}"
            )

        return "\n".join(parts)

    def _get_cached_schema(self, enriched: "EnrichedSchema", connection_id: Optional[str]) -> str:
        """Memoise the zone-2 full schema per (connection_id, schema_hash)."""
        try:
            from agent_service.agents.schema_cache import compute_schema_hash
            key = f"{connection_id or '_'}:{compute_schema_hash(enriched.schema_doc)}"
        except Exception:
            key = None
        if key and key in _intel_schema_map_cache:
            return _intel_schema_map_cache[key]
        schema = self._build_cached_schema(enriched)
        if key:
            _intel_schema_map_cache[key] = schema
        return schema

    def _build_dynamic_context(
        self,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
    ) -> str:
        dashboard_context = self._build_dashboard_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables
        )
        return "CURRENT CANVAS REPORT:\n" + dashboard_context

    def _build_system_blocks(
        self,
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
        enriched: Optional["EnrichedSchema"] = None,
        connection_id: Optional[str] = None,
        scope: str = "database",
        verified_tables_doc: Optional[dict] = None,
    ) -> list[dict]:
        dynamic = self._build_dynamic_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables,
        )

        if enriched and enriched.compact_tables:
            total = len(enriched.compact_tables)
            if scope == "report":
                try:
                    seed, neighbors = self.resolve_scope_tables(enriched, priority_tables)
                except Exception as exc:  # noqa: BLE001 — never let scoping break a turn
                    seed, neighbors = set(), set()
                    print(f"[intel_chat_agent] ⚠ resolve_scope_tables failed ({exc!r}) — full schema", flush=True)
                if seed:
                    try:
                        schema = self._build_scoped_schema(enriched, seed, neighbors)
                        print(
                            f"[intel_chat_agent] scope=report  seed_tables={len(seed)}  "
                            f"related_tables={len(neighbors)}  (of {total} total) — scoped schema built",
                            flush=True,
                        )
                    except Exception as exc:  # noqa: BLE001
                        schema = self._get_cached_schema(enriched, connection_id)
                        print(f"[intel_chat_agent] ⚠ scoped build failed ({exc!r}) — full schema", flush=True)
                else:
                    # No report tables matched (e.g. widgets have no parseable SQL) →
                    # fall back to the full schema so the copilot is never blind.
                    schema = self._get_cached_schema(enriched, connection_id)
                    print(
                        f"[intel_chat_agent] scope=report  seed_tables=0 — no report tables "
                        f"matched, falling back to FULL schema ({total} tables)",
                        flush=True,
                    )
            else:
                schema = self._get_cached_schema(enriched, connection_id)
                print(
                    f"[intel_chat_agent] scope=database — full schema ({total} tables)",
                    flush=True,
                )
            verified_block = _format_verified_tables(verified_tables_doc or {})
            extra = [{"type": "text", "text": verified_block}] if verified_block else []
            return [
                {"type": "text", "text": _INSTRUCTIONS},
                {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
                *extra,
                {"type": "text", "text": dynamic},
            ]

        raw_schema = self._build_schema_section_raw(schema_doc)
        verified_block = _format_verified_tables(verified_tables_doc or {})
        extra = [{"type": "text", "text": verified_block}] if verified_block else []
        return [
            {"type": "text", "text": _INSTRUCTIONS + "\n\n" + raw_schema,
             "cache_control": {"type": "ephemeral"}},
            *extra,
            {"type": "text", "text": dynamic},
        ]

    def prepare(
        self,
        message: str,
        conversation_history: list[dict],
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: Optional[list] = None,
        active_page_id: Optional[str] = None,
        priority_tables: Optional[set[str]] = None,
        enriched_schema: Optional["EnrichedSchema"] = None,
        model_override: Optional[str] = None,
        connection_id: Optional[str] = None,
        scope: str = "database",
        conversation_memory: Optional[list[str]] = None,
        verified_tables_doc: Optional[dict] = None,
    ) -> tuple[list[dict], list[dict], str, int]:
        """Build (system_blocks, messages, model_id, max_tokens)."""
        system_blocks = self._build_system_blocks(
            schema_doc=schema_doc,
            dashboard_widgets=dashboard_widgets,
            dashboard_pages=dashboard_pages or [],
            active_page_id=active_page_id,
            priority_tables=priority_tables,
            enriched=enriched_schema,
            connection_id=connection_id,
            scope=scope,
            verified_tables_doc=verified_tables_doc,
        )
        # Distilled memory: the gist of earlier questions, injected as a small dynamic
        # block so the copilot remembers what was asked WITHOUT replaying the full
        # transcript. Lets us keep only a short raw window below (lower tokens).
        if conversation_memory:
            mem_text = (
                "CONVERSATION MEMORY — earlier in this session the user asked about:\n"
                + "\n".join(f"- {q}" for q in conversation_memory[-_MEMORY_MAX:])
                + "\n\nStay consistent with these, build on prior answers, and don't "
                  "re-introduce topics already covered unless the user asks again."
            )
            system_blocks = system_blocks + [{"type": "text", "text": mem_text}]
        # Keep only a short raw window for immediate coherence — the long arc lives in
        # the distilled memory above (vs. previously replaying the last 20 messages).
        messages = conversation_history[-8:] + [{"role": "user", "content": message}]
        effective_model = BEDROCK_OPUS_MODEL if model_override == "opus" else INTEL_CHAT_MODEL
        effective_max_tokens = 8192 if model_override == "opus" else 2048

        cached_len = sum(len(b["text"]) for b in system_blocks if "cache_control" in b)
        dynamic_len = sum(len(b["text"]) for b in system_blocks if "cache_control" not in b)
        print(
            f"[intel_chat_agent] model={effective_model.split('/')[-1]}  "
            f"max_tokens={effective_max_tokens}  "
            f"history_turns={len(conversation_history) // 2}  "
            f"msg_len={len(message)}  scope={scope}  "
            f"cached_prefix={cached_len}  dynamic_suffix={dynamic_len}",
            flush=True,
        )
        return system_blocks, messages, effective_model, effective_max_tokens

    @staticmethod
    def parse_raw(raw: str) -> dict:
        """Split a raw model response into prose text + sql_execute specs +
        a dashboard_action. No model calls — pure parsing."""
        sqls_to_execute: list[dict] = []
        dashboard_action = None
        text = raw

        if "```sql_execute" in raw:
            for block in re.findall(r"```sql_execute\n(.*?)\n```", raw, re.DOTALL):
                try:
                    sqls_to_execute.append(json.loads(block.strip()))
                except json.JSONDecodeError:
                    pass
            text = re.sub(r"```sql_execute\n.*?\n```", "", text, flags=re.DOTALL).strip()

        sql_to_execute = sqls_to_execute[0] if sqls_to_execute else None

        if "```dashboard_action" in raw:
            match = re.search(r"```dashboard_action\n(.*?)\n```", raw, re.DOTALL)
            if match:
                try:
                    dashboard_action = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass
            text = re.sub(r"```dashboard_action\n.*?\n```", "", text, flags=re.DOTALL).strip()

        if "<action>" in raw and not sql_to_execute:
            match = re.search(r"<action>(.*?)</action>", raw, re.DOTALL)
            if match:
                try:
                    action_data = json.loads(match.group(1).strip())
                    if action_data.get("type") == "modify_query":
                        dashboard_action = action_data
                except json.JSONDecodeError:
                    pass
            text = re.sub(r"<action>.*?</action>", "", text, flags=re.DOTALL).strip()

        return {
            "text": text,
            "sql_to_execute": sql_to_execute,
            "sqls_to_execute": sqls_to_execute,
            "dashboard_action": dashboard_action,
        }

    async def retry_for_sql(
        self, message: str, conversation_history: list[dict], system_blocks: list[dict]
    ) -> list[dict]:
        """One silent retry with an ultra-strict prompt when the model narrated
        instead of emitting a sql_execute block. Returns the parsed sql specs (or [])."""
        retry_msg = (
            "EXECUTE ONLY — output NOTHING except a single sql_execute block.\n"
            "Do NOT write any description, acknowledgment, or explanation.\n"
            "The user is waiting for actual data — you MUST output a sql_execute block.\n"
            f"Original request: {message}"
        )
        retry_messages = conversation_history[-20:] + [{"role": "user", "content": retry_msg}]
        print("[intel_chat_agent] retry_for_sql — model narrated without a sql block, retrying", flush=True)
        try:
            raw2 = await bedrock_invoke_with_history(
                model_id=INTEL_CHAT_MODEL,
                system_prompt=system_blocks,
                messages=retry_messages,
                max_tokens=1024,
                temperature=0.0,
            )
            return self.parse_raw(raw2)["sqls_to_execute"]
        except Exception:
            return []

    async def respond(
        self,
        message: str,
        conversation_history: list[dict],
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: Optional[list] = None,
        active_page_id: Optional[str] = None,
        priority_tables: Optional[set[str]] = None,
        enriched_schema: Optional["EnrichedSchema"] = None,
        model_override: Optional[str] = None,
        connection_id: Optional[str] = None,
        scope: str = "database",
        conversation_memory: Optional[list[str]] = None,
        verified_tables_doc: Optional[dict] = None,
    ) -> dict:
        system_blocks, messages, model_id, max_tokens = self.prepare(
            message, conversation_history, schema_doc, dashboard_widgets,
            dashboard_pages, active_page_id, priority_tables, enriched_schema,
            model_override, connection_id, scope=scope,
            conversation_memory=conversation_memory,
            verified_tables_doc=verified_tables_doc,
        )

        raw = await bedrock_invoke_with_history(
            model_id=model_id,
            system_prompt=system_blocks,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
        )

        print(
            f"[intel_chat_agent] response_len={len(raw)}  "
            f"has_sql={'yes' if '```sql_execute' in raw else 'no'}  "
            f"has_action={'yes' if '```dashboard_action' in raw else 'no'}",
            flush=True,
        )

        parsed = self.parse_raw(raw)

        if not parsed["sqls_to_execute"] and (
            _is_chart_creation_request(message) or _is_data_query_request(message)
        ):
            retry_sqls = await self.retry_for_sql(message, conversation_history, system_blocks)
            if retry_sqls:
                parsed["sqls_to_execute"] = retry_sqls
                parsed["sql_to_execute"] = retry_sqls[0]

        return parsed

    @staticmethod
    async def load_history(session_id: str, redis) -> list[dict]:
        if redis is None:
            return list(_intel_memory_history.get(session_id, []))
        raw = await redis.get(f"{INTEL_HISTORY_PREFIX}{session_id}")
        if raw:
            try:
                data = json.loads(raw)
                return data.get("messages", data) if isinstance(data, dict) else data
            except Exception:
                return []
        return []

    @staticmethod
    async def load_memory(session_id: str, redis) -> list[str]:
        """Distilled memory = the gist of what the user has asked before (NOT the raw
        transcript). Lets the copilot stay consistent and build on prior questions
        without replaying the whole conversation."""
        if redis is None:
            return list(_intel_memory_summary.get(session_id, []))
        raw = await redis.get(f"{INTEL_HISTORY_PREFIX}{session_id}")
        if raw:
            try:
                data = json.loads(raw)
                return data.get("memory", []) if isinstance(data, dict) else []
            except Exception:
                return []
        return []

    @staticmethod
    def distill_memory(prev: list[str], user_message: str) -> list[str]:
        """Append the gist of the latest user question to memory (deduped, capped).
        Cheap + deterministic — no extra LLM call."""
        q = " ".join((user_message or "").split())[:180]
        if not q:
            return list(prev or [])[-_MEMORY_MAX:]
        out = [m for m in (prev or []) if m.strip().lower() != q.strip().lower()]
        out.append(q)
        return out[-_MEMORY_MAX:]

    @staticmethod
    async def save_history(session_id: str, messages: list[dict], redis, memory: Optional[list[str]] = None) -> None:
        trimmed = messages[-40:]
        mem = (memory or [])[-_MEMORY_MAX:]
        if redis is None:
            _intel_memory_history[session_id] = trimmed
            _intel_memory_summary[session_id] = mem
            return
        await redis.setex(
            f"{INTEL_HISTORY_PREFIX}{session_id}",
            INTEL_CONVERSATION_TTL_SECONDS,
            json.dumps({"messages": trimmed, "memory": mem}),
        )

    @staticmethod
    async def clear_history(session_id: str, redis) -> None:
        if redis is None:
            _intel_memory_history.pop(session_id, None)
            return
        await redis.delete(f"{INTEL_HISTORY_PREFIX}{session_id}")
