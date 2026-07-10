import calendar
import json
import os
import re
from datetime import date, timedelta
from typing import Optional, TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL, BEDROCK_OPUS_MODEL
from agent_service.agents import schema_scope as _scope

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema
    from agent_service.agents.nl_schema_router import ResolvedContext
    from agent_service.agents.graph_rag_retriever import RetrievedContext

from agent_service.agents.nl_schema_router import format_routing_hints

CHAT_MODEL = BEDROCK_SONNET_MODEL
CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours


# ── Pre-compute date bounds for relative time expressions ──────────────────────

def _months_ago(today: date, n: int) -> date:
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(today.day, max_day))


def _extract_time_filter_hint(message: str) -> str:
    """Detect a relative-date phrase in the user message and return a mandatory
    SQL hint block (with pre-computed ISO date strings) that the LLM must apply
    as a WHERE clause.  Returns an empty string when no phrase is found."""
    lower = message.lower()
    today = date.today()
    start: date | None = None
    end: date = today
    label = ""

    # "last N days / weeks / months / years"
    m = re.search(r"\blast\s+(\d+)\s+(day|week|month|year)s?\b", lower)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        label = m.group(0)
        if unit == "day":
            start = today - timedelta(days=n)
        elif unit == "week":
            start = today - timedelta(weeks=n)
        elif unit == "month":
            start = _months_ago(today, n)
        elif unit == "year":
            start = _months_ago(today, n * 12)

    elif re.search(r"\bthis\s+year\b|\bytd\b|\byear[- ]to[- ]date\b", lower):
        start = date(today.year, 1, 1)
        label = "year-to-date"

    elif re.search(r"\blast\s+year\b|\bprevious\s+year\b", lower):
        start = date(today.year - 1, 1, 1)
        end   = date(today.year - 1, 12, 31)
        label = f"last year ({today.year - 1})"

    elif re.search(r"\bthis\s+month\b", lower):
        start = date(today.year, today.month, 1)
        label = today.strftime("%B %Y")

    elif re.search(r"\blast\s+month\b|\bprevious\s+month\b", lower):
        first_of_this = date(today.year, today.month, 1)
        end_prev = first_of_this - timedelta(days=1)
        start = date(end_prev.year, end_prev.month, 1)
        end   = end_prev
        label = start.strftime("%B %Y")

    elif re.search(r"\blast\s+quarter\b|\bprevious\s+quarter\b", lower):
        q = (today.month - 1) // 3          # current quarter index (0-based)
        if q == 0:
            start = date(today.year - 1, 10, 1)
            end   = date(today.year - 1, 12, 31)
        else:
            sm = (q - 1) * 3 + 1
            start = date(today.year, sm, 1)
            end   = date(today.year, sm + 2, calendar.monthrange(today.year, sm + 2)[1])
        label = "last quarter"

    if start is None:
        return ""

    return (
        f"⚠️ MANDATORY TIME FILTER (you MUST apply this WHERE clause to every SQL you generate):\n"
        f"  Detected range: \"{label}\" → {start.isoformat()} to {end.isoformat()}\n"
        f"  Required WHERE: {{date_col}} >= '{start.isoformat()}' AND {{date_col}} <= '{end.isoformat()}'\n"
        f"  Replace {{date_col}} with the actual date/timestamp column name from the schema.\n"
        f"  NEVER omit this filter — without it the query returns all historical data, not just {label}.\n"
    )
# Default FK reach for builder-selected "Selected tables" scope when the request
# omits an explicit hop count.
CHAT_SELECTED_HOPS_DEFAULT = int(os.getenv("CHAT_SELECTED_HOPS_DEFAULT", "2"))

# In-memory fallback when Redis is unavailable
_memory_history: dict[str, list[dict]] = {}
# Distilled conversation memory (gist of prior questions), Redis-less fallback.
_memory_summary: dict[str, list[str]] = {}
_CHAT_MEMORY_MAX = 20  # max remembered prior-question gists


def _sanitize_history_for_llm(history: list[dict]) -> list[dict]:
    """Strip stored turns down to {role, content} before sending to Bedrock.
    Stored history carries extra bookkeeping keys (sql/error/low_confidence) for
    the root-cause follow-up gate — the Messages API rejects any message object
    with keys other than role/content."""
    return [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in history]

_SYSTEM_PROMPT_TEMPLATE = """You are a conversational data analyst embedded in a BI platform called Visually.
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
  - RELATIVE TIME RANGES (CRITICAL — when the user says "last N months/days/years", "this year", "ytd", etc.):
    ALWAYS add a WHERE clause to restrict the date column. NEVER return all history when a range is given.
      "last 3 months"  →  WHERE date_col >= CURRENT_DATE - INTERVAL '3 months'     (PostgreSQL/Redshift)
      "last 30 days"   →  WHERE date_col >= CURRENT_DATE - INTERVAL '30 days'
      "last year"      →  WHERE EXTRACT(YEAR FROM date_col) = EXTRACT(YEAR FROM CURRENT_DATE) - 1
      "this year/ytd"  →  WHERE date_col >= DATE_TRUNC('year', CURRENT_DATE)
      "last quarter"   →  WHERE date_col >= DATE_TRUNC('quarter', CURRENT_DATE) - INTERVAL '3 months'
                           AND date_col < DATE_TRUNC('quarter', CURRENT_DATE)
      MySQL: use CURDATE() and DATE_SUB(CURDATE(), INTERVAL N MONTH) instead of CURRENT_DATE/INTERVAL syntax.
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

LIMIT CLAUSE — DO NOT AUTO-ADD SMALL LIMITS:
  Never add a LIMIT clause unless (a) the user explicitly asks for a specific number of rows
  (e.g. "show me the top 10", "limit to 5"), or (b) the chart type naturally requires a cap
  to stay readable (e.g. pie/donut ≤ 20 slices, scatter/bubble ≤ 1000 points, slicer ≤ 500 values).
  Do NOT add LIMIT to bar charts, line charts, area charts, tables, KPIs, or aggregate queries
  unless the user asks. Return all rows the query naturally produces.

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
{{"sql": "SELECT ...", "chart_type": "bar_vertical|line|pie|kpi|multi_row_card|scatter|table|waterfall|area|donut|slicer|grouped_bar|stacked_bar", "title": "Chart Title", "x_label": "...", "y_label": "..."}}
```

CHART TYPE SELECTION RULES:
- Use "pie" for proportional distributions (status breakdown, category share, etc.)
- Use "kpi" ONLY for a single aggregate number (one row, one value): SELECT COUNT(*) AS value FROM table
- Use "multi_row_card" when the user wants a KPI broken down by a dimension — multiple label/value pairs:
    SQL pattern: SELECT dim AS label, COUNT(*)/SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC
- Use "table" for detailed row-level data with many columns
- Use "bar_vertical" when the user wants to compare values visually
- Use "grouped_bar" or "stacked_bar" when the user asks to compare TWO OR MORE metrics
  broken down by the SAME dimension (e.g. "buy vs sell volume by segment", "revenue vs
  cost by region"): pivot each compared metric into its OWN column with a CASE WHEN,
  GROUP BY the dimension — never GROUP BY a column derived from the compared metric
  itself. See Example 5 below.

WHICH-QUESTION DIMENSION RULE — critical, causes wrong answers if ignored:
When the user asks "which <noun> is highest/lowest/best/worst/most/least in <metric>",
the SQL's GROUP BY / first output column MUST represent that exact <noun> (an entity/
dimension column — segment, region, customer, product, etc.), never a column whose
literal VALUES merely happen to overlap with words in the metric name. For example,
"which SEGMENT is highest in Buy vs Sell Volume" must GROUP BY a customer/entity
segment column — grouping by a transaction-type column (whose values are literally
"Buy"/"Sell") answers a different, wrong question even though it runs without error.

CHART CREATION EXAMPLES — copy these patterns exactly (note the 2–4 sentence explanation BEFORE each block):

Example 1 — pie chart by specific columns (user names table + columns):
User: "Create a PieChart for current status. Table: bullhorn_core_placement. Columns: placementID, status."
Response: "You asked for a breakdown of placements by their current status. This pie chart counts the distinct placements in each status and shows every status as a slice of the whole, so you can see which statuses are most common at a glance. A pie fits here because the statuses are mutually exclusive parts of one total."
```sql_execute
{{"sql": "SELECT status AS \"Status\", COUNT(DISTINCT \"placementID\") AS \"Count\" FROM bullhorn_core_placement GROUP BY status ORDER BY 2 DESC", "chart_type": "pie", "title": "Current Status", "x_label": "Status", "y_label": "Count"}}
```

Example 2 — table chart:
User: "Create a table chart showing employee name and salary"
Response: "You want a list of employees alongside their salaries. This table pulls each employee's name and salary and sorts it from highest to lowest pay, so the top earners sit at the top. A table is the right choice because you're after exact row-level values rather than a trend or proportion."
```sql_execute
{{"sql": "SELECT name AS \"Name\", salary AS \"Salary\" FROM employees ORDER BY salary DESC", "chart_type": "table", "title": "Employee Salaries", "x_label": "", "y_label": ""}}
```

Example 3 — grouped KPI (multi_row_card):
User: "Show job count broken down by source type" or "KPI showing jobs per category"
Response: "You asked how jobs are distributed across source types. This card groups every job by its source and shows the count for each as a ranked list, so the biggest sources stand out first. A multi-row card fits because you want one number per category rather than a single overall total."
```sql_execute
{{"sql": "SELECT source AS \"Source\", COUNT(*) AS \"Count\" FROM jobs GROUP BY source ORDER BY 2 DESC", "chart_type": "multi_row_card", "title": "Job Count by Source", "x_label": "Source", "y_label": "Count"}}
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

Example 5 — compound comparison by dimension (grouped_bar with CASE WHEN pivot):
User: "Which segment is highest in buy vs sell volume?"
Response: "You want to compare buy volume against sell volume for each customer segment. This groups every transaction by the customer's segment and splits the total units into a Buy column and a Sell column, so you can see which segment leads in each. A grouped bar chart fits because you're comparing two metrics side by side across the same dimension."
```sql_execute
{{"sql": "SELECT s.segment AS \"Segment\", SUM(CASE WHEN t.transaction_type = 'BUY' THEN t.units ELSE 0 END) AS \"Buy Volume\", SUM(CASE WHEN t.transaction_type = 'SELL' THEN t.units ELSE 0 END) AS \"Sell Volume\" FROM transactions t JOIN customers s ON t.customer_id = s.customer_id GROUP BY s.segment ORDER BY 2 DESC", "chart_type": "grouped_bar", "title": "Buy vs Sell Volume by Segment", "x_label": "Segment", "y_label": "Volume"}}
```
Note: GROUP BY is the customer's segment column — NOT transaction_type. transaction_type
is only used INSIDE the CASE WHEN to split the metric into two columns; it must never be
the GROUP BY column itself, or the chart answers "which transaction type" instead of
"which segment."

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

WHY / EXPLAIN / ROOT-CAUSE QUESTIONS — DRILL-DOWN PROTOCOL:
When the user asks WHY a number is what it is, asks you to EXPLAIN a metric or trend, or
asks HOW a result was reached (e.g. "why is revenue down?", "explain the churn", "what is
driving this?", "break this down"):
  1. Acknowledge what metric is being discussed (1 sentence).
  2. Generate SQL that DECOMPOSES the metric — don't just re-run the same query:
     break it down by the most useful dimension (segment, region, time, status, etc.),
     show top contributors (ORDER BY value DESC LIMIT 10-20), or show the trend over time.
  3. The explanation must come BEFORE the sql_execute block, never after it.
A vague answer with no SQL ("it's probably because of X") is not acceptable — the user
needs a real breakdown grounded in a fresh query, not a guess.

TONE: Clear, helpful, and data-focused. For charts/tables/KPIs, always explain the request and what the chart shows in 2–4 sentences BEFORE the block (never after it). Reference actual values when the data is already provided; avoid empty filler phrases."""

# ── Prompt zones (see chat_agent caching design) ──────────────────────────────
# ZONE 1 — instructions. Message-INDEPENDENT, byte-stable → part of the cached
# prefix. `.format()` with no args unescapes the doubled `{{ }}` in the JSON
# examples without substituting anything (there are no placeholders left).
_INSTRUCTIONS = _SYSTEM_PROMPT_TEMPLATE.format()

# Sample values shown per categorical column. The detailed schema is now cached
# (message-independent), so we can afford richer samples without per-turn cost —
# this is what lets the model match exact filter values and avoid empty/N-A results.
_SAMPLE_VALUE_LIMIT = int(os.getenv("CHAT_SAMPLE_VALUE_LIMIT", "5"))

# The cached schema keeps EVERY table and column (for accuracy), but the long
# LLM-generated descriptions are the bulk of its size. Clipping them keeps the
# column visible (name/type/samples) while pulling the cached prefix well back
# from the model's context ceiling. Set to 0 to keep full descriptions.
_COL_DESC_MAX = int(os.getenv("CHAT_COL_DESC_MAX", "80"))
_TABLE_DESC_MAX = int(os.getenv("CHAT_TABLE_DESC_MAX", "160"))

# GraphRAG-scoped schema: only kick in when the DB is large enough to matter.
# For small DBs (<10 tables) the full schema is cheap anyway — don't bother.
_GRAPHRAG_MIN_TABLES = int(os.getenv("CHAT_GRAPHRAG_MIN_TABLES", "10"))
# FK hops to walk outward from the GraphRAG seed when building the scoped schema.
_GRAPHRAG_SCOPE_HOPS = int(os.getenv("CHAT_GRAPHRAG_HOPS", "2"))


def _clip(text: str, limit: int) -> str:
    """Trim text to `limit` chars (limit<=0 disables trimming)."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"

# Memoised zone-2 schema maps, keyed by (connection_id, schema_hash). The map is
# byte-identical across sessions for a given schema, so caching it here keeps the
# cached prefix stable and skips re-formatting on every request.
_schema_map_cache: dict[str, str] = {}


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


def _tfidf_score(message: str, table: dict) -> float:
    query_words: set[str] = set()
    for w in re.sub(r"[^a-z0-9_]", " ", message.lower()).split():
        if len(w) > 2:
            query_words.add(w)
            if len(w) > 5:
                query_words.add(w[:5])
    if not query_words:
        return 0.0

    target_words: set[str] = set()
    for src in [
        table.get("name", ""),
        table.get("description", ""),
        " ".join(table.get("all_column_names") or []),
    ]:
        for w in re.sub(r"[^a-z0-9_]", " ", (src or "").lower()).split():
            if len(w) > 2:
                target_words.add(w)
                if len(w) > 5:
                    target_words.add(w[:5])

    overlap = len(query_words & target_words)
    return overlap / len(query_words) if query_words else 0.0


_CHART_CREATION_KEYWORDS = {
    "create", "make", "build", "generate", "add", "show", "give",
    "chart", "graph", "pie", "bar", "line", "kpi", "table", "visual",
    "plot", "donut", "scatter", "funnel", "treemap", "waterfall",
}

def _is_chart_creation_request(message: str) -> bool:
    """Return True when the message is asking for chart/viz creation."""
    words = set(re.sub(r"[^a-z0-9 ]", " ", message.lower()).split())
    action_words  = {"create", "make", "build", "generate", "add", "give", "draw", "produce"}
    subject_words = {"chart", "graph", "pie", "bar", "kpi", "table", "visual",
                     "visualization", "plot", "donut", "scatter", "funnel", "treemap", "waterfall"}
    has_action  = bool(words & action_words)
    has_subject = bool(words & subject_words)
    return has_action and has_subject


def _is_data_query_request(message: str) -> bool:
    """Return True when the message is asking a data question that requires SQL.

    Also covers "why/explain/drill-down" requests: even when the user already has a
    number in front of them, asking WHY it is that value or to EXPLAIN it requires
    generating SQL to show the underlying breakdown/drivers.
    """
    lower = message.lower()
    # Question starters that imply a data lookup
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


class ChatAgent:
    def _build_cached_schema(
        self,
        enriched: "EnrichedSchema",
    ) -> str:
        """ZONE 2 (cached) — FULL detailed schema for EVERY table, in deterministic
        (alphabetical) order so the block is byte-stable and message-INDEPENDENT.
        Because it is cached after the first message, we can afford rich detail
        (all columns, types, descriptions, sample values, grain, joins, disambiguation).
        This is what restores the accuracy that per-turn compression had to sacrifice:
        the model sees every table and real sample values, so it can match exact
        filter columns/values instead of guessing. Priority/canvas hints stay in the
        dynamic (uncached) zone to keep this block stable."""
        compact = sorted(enriched.compact_tables or [], key=lambda x: x.get("name", ""))

        dialect = (enriched.db_type or "").lower()
        dialect_label = {
            "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
            "redshift": "Amazon Redshift", "mysql": "MySQL",
        }.get(dialect, enriched.db_type or "SQL")

        lines = [
            f"SQL DIALECT: {dialect_label} — every query you write MUST be valid {dialect_label} SQL.",
            f"DATABASE SCHEMA — {len(compact)} tables (the complete database; "
            f"you may query any of them):",
        ]

        for t in compact:
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
            col_parts = []
            sample_parts = []
            for c in cols:
                cname = c.get("name") or ""
                ctype = c.get("type") or ""
                cdesc = _clip(c.get("description") or "", _COL_DESC_MAX)
                sem_type = c.get("semantic_type") or ""
                tag = f"[{sem_type}]" if sem_type else ""
                col_parts.append(f"{cname}{tag} ({ctype}){': ' + cdesc if cdesc else ''}")

                # Sample values matter most for categorical/low-cardinality columns —
                # they let the model write correct WHERE filters (avoiding empty/N-A).
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

        # Full join graph, deterministic order.
        join_hints = []
        seen_edges: set[frozenset] = set()
        for tbl_a in sorted(enriched.relationship_graph.edges.keys()):
            for tbl_b, condition in enriched.relationship_graph.edges[tbl_a].items():
                edge_key = frozenset([tbl_a, tbl_b])
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    join_hints.append(f"  {condition}")
        if join_hints:
            lines.append("\nJOIN CONDITIONS:")
            lines.extend(join_hints)

        disambig = enriched.get_disambiguation_text()
        if disambig and disambig != "COLUMN DISAMBIGUATION (same column name, different meanings per table):":
            lines.append(f"\n{disambig}")

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

        # Build page name map
        pages_map: dict[str, str] = {p["id"]: p["name"] for p in dashboard_pages if "id" in p}

        # Group widgets by page_id
        by_page: dict[str, list[dict]] = {}
        unassigned: list[dict] = []
        for w in dashboard_widgets:
            pid = w.get("page_id")
            if pid:
                by_page.setdefault(pid, []).append(w)
            else:
                unassigned.append(w)

        parts: list[str] = []

        # Page summary header
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

        # Per-page widget detail
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
                sql_preview = (w.get("sql_query") or "")[:60]
                parts.append(
                    f"  • {w['title']} [{w['chart_type']}]"
                    + (f" | SQL: {sql_preview}" if sql_preview else "")
                )

        # Widgets not yet assigned to a page (legacy / just added)
        if unassigned:
            parts.append(f"\nUnassigned widgets ({len(unassigned)}):")
            for w in unassigned:
                parts.append(f"  • {w['title']} [{w['chart_type']}]")

        # Priority tables extracted from existing SQL
        if priority_tables:
            parts.append(
                f"\nPRIORITY TABLES (used by existing charts — prefer these when building new ones): "
                f"{', '.join(sorted(priority_tables))}"
            )

        return "\n".join(parts)

    def _get_cached_schema(
        self, enriched: "EnrichedSchema", connection_id: Optional[str]
    ) -> str:
        """Memoise the zone-2 full schema per (connection_id, schema_hash). Skips
        re-formatting and guarantees a byte-identical cached prefix across requests
        for the same schema (so Bedrock's prefix cache actually hits)."""
        try:
            from agent_service.agents.schema_cache import compute_schema_hash
            key = f"{connection_id or '_'}:{compute_schema_hash(enriched.schema_doc)}"
        except Exception:
            key = None
        if key and key in _schema_map_cache:
            return _schema_map_cache[key]
        schema = self._build_cached_schema(enriched)
        if key:
            _schema_map_cache[key] = schema
        return schema

    def _build_dynamic_context(
        self,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
    ) -> str:
        """ZONE 3 — the per-turn tail (UNCACHED): just the current canvas state and
        which tables it already uses. Small, so re-sending it every turn is cheap.
        The schema now lives entirely in the cached zone 2."""
        dashboard_context = self._build_dashboard_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables
        )
        return "CURRENT CANVAS REPORT:\n" + dashboard_context

    def _build_selected_schema(
        self,
        enriched: "EnrichedSchema",
        selected_tables: list[str],
        selected_hops: int,
        connection_id: Optional[str],
        total: int,
    ) -> str:
        """Report-scoped schema for the builder's "Selected tables" mode: the chosen
        tables (full detail) + their `selected_hops`-hop FK neighbours (lightweight).
        Any failure or empty match degrades to the full schema so a turn never breaks."""
        try:
            seed, neighbors = _scope.resolve_scope_tables(
                enriched, set(selected_tables or []), selected_hops
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[chat] ⚠ resolve_scope_tables failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

        if not seed:
            print(
                f"[chat] scope=selected picked={len(selected_tables or [])} seed=0 — "
                f"no tables matched, falling back to FULL schema ({total} tables)",
                flush=True,
            )
            return self._get_cached_schema(enriched, connection_id)

        try:
            schema = _scope.render_scoped_schema(
                enriched, seed, neighbors, selected_hops,
                col_desc_max=_COL_DESC_MAX, table_desc_max=_TABLE_DESC_MAX,
                sample_limit=_SAMPLE_VALUE_LIMIT,
                scope_intro=(
                    "SCOPE: SELECTED TABLES — the builder chose these tables to focus on. "
                    "Build queries from the SELECTED TABLES below; RELATED TABLES are joinable "
                    "and may be used only when the request genuinely needs them."
                ),
                seed_header=f"SELECTED TABLES — {len(seed)} table(s) you are focusing on:",
                related_header_fmt=(
                    "RELATED TABLES — {n} table(s) within {hops} join-hop(s) of the "
                    "selected tables (names + how to join only):"
                ),
            )
            print(
                f"[chat] scope=selected picked={len(selected_tables or [])} seed={len(seed)} "
                f"related={len(neighbors)} hops={selected_hops} (of {total} total) — scoped schema built",
                flush=True,
            )
            return schema
        except Exception as exc:  # noqa: BLE001
            print(f"[chat] ⚠ scoped build failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

    def _build_true_graphrag_schema(
        self,
        enriched: "EnrichedSchema",
        retrieved: "RetrievedContext",
        connection_id: Optional[str],
        total: int,
    ) -> str:
        """Schema block driven by graph_rag_retriever.retrieve() results — richer
        than nl_schema_router because it scores tables using TF-IDF, concept index,
        entity columns, and FK-graph signals simultaneously (zero LLM calls).
        Falls back to the full cached schema when nothing resolves."""
        seed_names = set((retrieved.primary_tables or [])[:8])
        if not seed_names:
            print(
                f"[chat_agent] true-graphrag: no primary tables "
                f"(confidence={retrieved.confidence:.2f}) — full schema ({total} tables)",
                flush=True,
            )
            return self._get_cached_schema(enriched, connection_id)

        try:
            seed, neighbors = _scope.resolve_scope_tables(enriched, seed_names, _GRAPHRAG_SCOPE_HOPS)
        except Exception as exc:
            print(f"[chat_agent] ⚠ true-graphrag resolve failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

        if not seed:
            print(
                f"[chat_agent] true-graphrag: 0 seed matched from "
                f"{list(seed_names)[:4]} — full schema ({total} tables)",
                flush=True,
            )
            return self._get_cached_schema(enriched, connection_id)

        try:
            schema = _scope.render_scoped_schema(
                enriched, seed, neighbors, _GRAPHRAG_SCOPE_HOPS,
                col_desc_max=_COL_DESC_MAX, table_desc_max=_TABLE_DESC_MAX,
                sample_limit=_SAMPLE_VALUE_LIMIT,
                scope_intro=(
                    "SCOPE: DATABASE (true graph-RAG ranked) — tables selected by "
                    "TF-IDF, concept matching, entity signals, and FK-graph expansion "
                    "for this specific query. FOCUSED TABLES have the highest relevance "
                    "scores. RELATED TABLES are FK-graph neighbours available for JOINs. "
                    "Graph-RAG column hints follow immediately after this schema block."
                ),
                seed_header=(
                    f"FOCUSED TABLES — {len(seed)} table(s) ranked most relevant "
                    f"(confidence={retrieved.confidence:.0%}):"
                ),
                related_header_fmt=(
                    "RELATED TABLES — {n} table(s) within {hops} FK-hop(s) of the "
                    "focused tables (name + purpose + join path):"
                ),
            )
            full_len = len(self._get_cached_schema(enriched, connection_id))
            scoped_len = len(schema)
            saved_pct = round((1 - scoped_len / max(full_len, 1)) * 100, 1)
            print(
                f"[chat_agent] scope=database(true-graphrag)  "
                f"seed={len(seed)}  related={len(neighbors)}  total={total}  "
                f"chars={scoped_len:,}/{full_len:,}  saved={saved_pct}%  "
                f"≈{(full_len - scoped_len) // 4:,} input tokens saved",
                flush=True,
            )
            return schema
        except Exception as exc:
            print(f"[chat_agent] ⚠ true-graphrag render failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

    def _build_graphrag_scoped_schema(
        self,
        enriched: "EnrichedSchema",
        resolved_context: "ResolvedContext",
        connection_id: Optional[str],
        total: int,
    ) -> str:
        """GraphRAG-ranked scope for scope=database: uses the NL router's top-ranked
        tables as seed, then expands to their FK-graph neighbours (_GRAPHRAG_SCOPE_HOPS
        hops).  Compared to sending the full schema this can save 90%+ of input tokens
        on databases with 50+ tables.  Falls back to full schema if nothing resolves."""
        seed_names = set((resolved_context.relevant_tables or [])[:8])
        try:
            seed, neighbors = _scope.resolve_scope_tables(enriched, seed_names, _GRAPHRAG_SCOPE_HOPS)
        except Exception as exc:
            print(f"[chat] ⚠ graphrag scope resolve failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

        if not seed:
            print(
                f"[chat] graphrag scope: 0 seed tables matched from "
                f"candidates {list(seed_names)[:4]} — full schema ({total} tables)",
                flush=True,
            )
            return self._get_cached_schema(enriched, connection_id)

        try:
            schema = _scope.render_scoped_schema(
                enriched, seed, neighbors, _GRAPHRAG_SCOPE_HOPS,
                col_desc_max=_COL_DESC_MAX, table_desc_max=_TABLE_DESC_MAX,
                sample_limit=_SAMPLE_VALUE_LIMIT,
                scope_intro=(
                    "SCOPE: DATABASE (query-ranked) — tables selected by relevance to "
                    "this specific query. FOCUSED TABLES (below) are the highest-ranked "
                    "matches. RELATED TABLES are their FK-graph neighbours available for "
                    "JOINs. If you genuinely need a table not listed, say so in your reply."
                ),
                seed_header=f"FOCUSED TABLES — {len(seed)} table(s) ranked most relevant to this query:",
                related_header_fmt=(
                    "RELATED TABLES — {n} table(s) within {hops} FK-hop(s) of the focused "
                    "tables (name + description + join path; full detail available on request):"
                ),
            )
            full_len = len(self._get_cached_schema(enriched, connection_id))
            scoped_len = len(schema)
            saved_pct = round((1 - scoped_len / max(full_len, 1)) * 100, 1)
            print(
                f"[chat] scope=database(graphrag)  seed={len(seed)}  related={len(neighbors)}  "
                f"total_tables={total}  chars={scoped_len:,}/{full_len:,}  "
                f"saved={saved_pct}%  ≈{(full_len - scoped_len) // 4:,} input tokens saved",
                flush=True,
            )
            return schema
        except Exception as exc:
            print(f"[chat] ⚠ graphrag scoped render failed ({exc!r}) — full schema", flush=True)
            return self._get_cached_schema(enriched, connection_id)

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
        selected_tables: Optional[list[str]] = None,
        selected_hops: int = CHAT_SELECTED_HOPS_DEFAULT,
        resolved_context: Optional["ResolvedContext"] = None,
        conversation_history: Optional[list] = None,
        retrieved_graphrag: Optional["RetrievedContext"] = None,
    ) -> list[dict]:
        """Assemble the system prompt as Bedrock content blocks with a cache
        breakpoint at the end of the schema. Zones 1+2 (instructions + schema)
        are cached; Zone 2.5 (NL2SQL routing hints, per-query) and Zone 3
        (canvas context) are re-sent each turn without cache.

        scope:
          "database" → full enriched schema (every table) — the default.
          "selected" → only the builder-picked tables (`selected_tables`) plus their
                        `selected_hops`-hop FK neighbours, via schema_scope. Falls back
                        to the full schema when nothing matches."""
        dynamic = self._build_dynamic_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables,
        )

        # Zone 2.5 — NL2SQL routing hints (non-empty only when resolved_context was built)
        routing_hints = format_routing_hints(resolved_context) if resolved_context else ""

        if enriched and enriched.compact_tables:
            total = len(enriched.compact_tables)
            graphrag_hints_text = ""
            if scope == "selected":
                schema = self._build_selected_schema(
                    enriched, selected_tables or [], selected_hops, connection_id, total
                )
            else:
                # scope="database" — prefer true graph-RAG (TF-IDF + concept + entity +
                # FK-graph signals) over the lighter NL router when both are available.
                # route_query() resolves tables from the CURRENT message alone, so an
                # elliptical follow-up ("what about last month") with no table-name
                # signal of its own can score every table near-zero and get routed to
                # whichever table wins that noise — pruning the schema down to it and
                # silently dropping the table the conversation was actually about.
                # When history is present and the router's own confidence is weak,
                # skip scoping and send the full schema instead of guessing.
                _top_score = max(resolved_context.table_scores.values(), default=0.0) if resolved_context else 0.0
                _ambiguous_followup = bool(conversation_history) and _top_score < 0.15
                if (
                    retrieved_graphrag
                    and retrieved_graphrag.candidates
                    and not _ambiguous_followup
                ):
                    schema = self._build_true_graphrag_schema(
                        enriched, retrieved_graphrag, connection_id, total
                    )
                    try:
                        from agent_service.agents.graph_rag_retriever import format_retrieval_hints
                        graphrag_hints_text = format_retrieval_hints(retrieved_graphrag)
                    except Exception as exc:
                        print(f"[chat] ⚠ format_retrieval_hints failed ({exc!r})", flush=True)
                elif (
                    resolved_context
                    and not resolved_context.fallback
                    and resolved_context.focused_tables
                    and total >= _GRAPHRAG_MIN_TABLES
                    and not _ambiguous_followup
                ):
                    schema = self._build_graphrag_scoped_schema(
                        enriched, resolved_context, connection_id, total
                    )
                elif _ambiguous_followup:
                    schema = self._get_cached_schema(enriched, connection_id)
                    print(
                        f"[chat] scope=database — full schema (ambiguous follow-up, "
                        f"top_score={_top_score:.3f}, {total} tables)",
                        flush=True,
                    )
                else:
                    schema = self._get_cached_schema(enriched, connection_id)
                    print(f"[chat] scope=database — full schema ({total} tables)", flush=True)

            blocks: list[dict] = [
                {"type": "text", "text": _INSTRUCTIONS},
                {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
            ]
            # Graph-RAG column-level hints (changes per query — placed AFTER the cached
            # schema so it doesn't bust the cache key).
            if graphrag_hints_text:
                blocks.append({"type": "text", "text": graphrag_hints_text})
            # Zone 2.5 injected only when non-empty (avoids a pointless empty block)
            if routing_hints:
                blocks.append({"type": "text", "text": routing_hints})
            blocks.append({"type": "text", "text": dynamic})
            return blocks

        # No enriched schema → fall back to the raw doc. Cache instructions + raw
        # schema together (still stable per schema); canvas stays in the tail.
        raw_schema = self._build_schema_section_raw(schema_doc)
        blocks = [
            {"type": "text", "text": _INSTRUCTIONS + "\n\n" + raw_schema,
             "cache_control": {"type": "ephemeral"}},
        ]
        if routing_hints:
            blocks.append({"type": "text", "text": routing_hints})
        blocks.append({"type": "text", "text": dynamic})
        return blocks

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
        selected_tables: Optional[list[str]] = None,
        selected_hops: int = CHAT_SELECTED_HOPS_DEFAULT,
        resolved_context: Optional["ResolvedContext"] = None,
        retrieved_graphrag: Optional["RetrievedContext"] = None,
        conversation_memory: Optional[list[str]] = None,
    ) -> tuple[list[dict], list[dict], str, int]:
        """Build everything needed for a model call: (system_blocks, messages,
        model_id, max_tokens). Shared by both respond() and the streaming path."""
        system_blocks = self._build_system_blocks(
            schema_doc=schema_doc,
            dashboard_widgets=dashboard_widgets,
            dashboard_pages=dashboard_pages or [],
            active_page_id=active_page_id,
            priority_tables=priority_tables,
            enriched=enriched_schema,
            connection_id=connection_id,
            scope=scope,
            selected_tables=selected_tables,
            selected_hops=selected_hops,
            resolved_context=resolved_context,
            conversation_history=conversation_history,
            retrieved_graphrag=retrieved_graphrag,
        )
        if conversation_memory:
            mem_text = (
                "CONVERSATION MEMORY — earlier in this session the user asked about:\n"
                + "\n".join(f"- {q}" for q in conversation_memory[-_CHAT_MEMORY_MAX:])
                + "\n\nStay consistent with these, build on prior answers, and don't "
                  "re-introduce topics already covered unless the user asks again."
            )
            system_blocks = system_blocks + [{"type": "text", "text": mem_text}]
        # Prepend pre-computed date bounds so the LLM can't miss or misinterpret them
        time_hint = _extract_time_filter_hint(message)
        effective_message = f"{time_hint}\n{message}" if time_hint else message
        if time_hint:
            print(f"[chat_agent] time_filter injected: {time_hint.splitlines()[1].strip()}", flush=True)
        messages = _sanitize_history_for_llm(conversation_history[-20:]) + [
            {"role": "user", "content": effective_message}
        ]
        effective_model = BEDROCK_OPUS_MODEL if model_override == "opus" else CHAT_MODEL
        effective_max_tokens = 8192 if model_override == "opus" else 2048  # Opus needs more

        cached_len = sum(len(b["text"]) for b in system_blocks if "cache_control" in b)
        dynamic_len = sum(len(b["text"]) for b in system_blocks if "cache_control" not in b)
        print(
            f"[chat_agent] model={effective_model.split('/')[-1]}  "
            f"max_tokens={effective_max_tokens}  "
            f"history_turns={len(conversation_history) // 2}  "
            f"msg_len={len(message)}  scope={scope}  "
            f"cached_prefix={cached_len}  dynamic_suffix={dynamic_len}",
            flush=True,
        )
        return system_blocks, messages, effective_model, effective_max_tokens

    @staticmethod
    def parse_raw(raw: str) -> dict:
        """Split a raw model response into prose text + executable sql_execute specs
        + a dashboard_action. No model calls — pure parsing."""
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

        # Legacy <action> block support
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
        retry_messages = _sanitize_history_for_llm(conversation_history[-20:]) + [
            {"role": "user", "content": retry_msg}
        ]
        try:
            raw2 = await bedrock_invoke_with_history(
                model_id=CHAT_MODEL,
                system_prompt=system_blocks,
                messages=retry_messages,
                max_tokens=1024,
                temperature=0.0,
            )
            return self.parse_raw(raw2)["sqls_to_execute"]
        except Exception:
            return []  # retry failed — caller falls back to original narration

    async def retry_with_feedback(
        self, message: str, conversation_history: list[dict], system_blocks: list[dict], feedback: str,
    ) -> list[dict]:
        """One silent retry with a specific corrective feedback message — used when
        the generated SQL executed fine but a deterministic post-check found it
        likely answers the wrong dimension/question (e.g. a check_dimension_match
        mismatch). Unlike retry_for_sql (which fires when NO sql was produced at
        all), this fires when SQL WAS produced but is probably wrong. Returns the
        parsed sql specs (or [])."""
        retry_msg = (
            f"Your previous query executed successfully but has a problem: {feedback}\n"
            "Rewrite the query to fix this. Output ONLY a corrected sql_execute block — "
            "no narration, no explanation.\n"
            f"Original request: {message}"
        )
        retry_messages = _sanitize_history_for_llm(conversation_history[-20:]) + [
            {"role": "user", "content": retry_msg}
        ]
        try:
            raw2 = await bedrock_invoke_with_history(
                model_id=CHAT_MODEL,
                system_prompt=system_blocks,
                messages=retry_messages,
                max_tokens=1024,
                temperature=0.0,
            )
            return self.parse_raw(raw2)["sqls_to_execute"]
        except Exception:
            return []  # retry failed — caller falls back to original result

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
        selected_tables: Optional[list[str]] = None,
        selected_hops: int = CHAT_SELECTED_HOPS_DEFAULT,
        resolved_context: Optional["ResolvedContext"] = None,
        retrieved_graphrag: Optional["RetrievedContext"] = None,
        conversation_memory: Optional[list[str]] = None,
    ) -> dict:
        system_blocks, messages, model_id, max_tokens = self.prepare(
            message, conversation_history, schema_doc, dashboard_widgets,
            dashboard_pages, active_page_id, priority_tables, enriched_schema,
            model_override, connection_id,
            scope=scope, selected_tables=selected_tables, selected_hops=selected_hops,
            resolved_context=resolved_context, retrieved_graphrag=retrieved_graphrag,
            conversation_memory=conversation_memory,
        )

        raw = await bedrock_invoke_with_history(
            model_id=model_id,
            system_prompt=system_blocks,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
        )

        print(
            f"[chat_agent] response_len={len(raw)}  "
            f"has_sql={'yes' if '```sql_execute' in raw else 'no'}  "
            f"has_action={'yes' if '```dashboard_action' in raw else 'no'}",
            flush=True,
        )

        parsed = self.parse_raw(raw)

        # Auto-retry when the model narrated a data/chart request without a sql block.
        if not parsed["sqls_to_execute"] and (
            _is_chart_creation_request(message) or _is_data_query_request(message)
        ):
            retry_sqls = await self.retry_for_sql(message, conversation_history, system_blocks)
            if retry_sqls:
                parsed["sqls_to_execute"] = retry_sqls
                parsed["sql_to_execute"] = retry_sqls[0]

        # Exposed so callers can run a post-execution dimension-mismatch retry
        # (retry_with_feedback) without re-building the schema/context from scratch.
        parsed["system_blocks"] = system_blocks
        return parsed

    @staticmethod
    async def load_history(session_id: str, redis) -> list[dict]:
        if redis is None:
            return list(_memory_history.get(session_id, []))
        raw = await redis.get(f"chat:history:{session_id}")
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
        transcript). Lets the assistant stay consistent and build on prior questions
        without replaying the whole conversation."""
        if redis is None:
            return list(_memory_summary.get(session_id, []))
        raw = await redis.get(f"chat:history:{session_id}")
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
            return list(prev or [])[-_CHAT_MEMORY_MAX:]
        out = [m for m in (prev or []) if m.strip().lower() != q.strip().lower()]
        out.append(q)
        return out[-_CHAT_MEMORY_MAX:]

    @staticmethod
    async def save_history(
        session_id: str, messages: list[dict], redis, memory: Optional[list[str]] = None,
    ) -> None:
        trimmed = messages[-40:]
        mem = (memory or [])[-_CHAT_MEMORY_MAX:]
        if redis is None:
            _memory_history[session_id] = trimmed
            _memory_summary[session_id] = mem
            return
        await redis.setex(
            f"chat:history:{session_id}",
            CONVERSATION_TTL_SECONDS,
            json.dumps({"messages": trimmed, "memory": mem}),
        )

    @staticmethod
    async def clear_history(session_id: str, redis) -> None:
        if redis is None:
            _memory_history.pop(session_id, None)
            _memory_summary.pop(session_id, None)
            return
        await redis.delete(f"chat:history:{session_id}")
