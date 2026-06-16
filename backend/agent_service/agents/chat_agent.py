import json
import os
import re
from typing import Optional, TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL, BEDROCK_OPUS_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

CHAT_MODEL = BEDROCK_SONNET_MODEL
CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours

# In-memory fallback when Redis is unavailable
_memory_history: dict[str, list[dict]] = {}

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
    """Return True when the message is asking a data question that requires SQL."""
    lower = message.lower()
    # Question starters that imply a data lookup
    question_starters = (
        "what", "how many", "how much", "which", "who", "when", "where",
        "show me", "find", "list", "get me", "give me", "tell me",
        "fetch", "retrieve", "calculate", "compute", "sum", "count",
        "total", "average", "top", "bottom", "highest", "lowest",
        "most", "least", "compare", "breakdown", "analyse", "analyze",
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

    def _build_system_blocks(
        self,
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
        enriched: Optional["EnrichedSchema"] = None,
        connection_id: Optional[str] = None,
    ) -> list[dict]:
        """Assemble the system prompt as Bedrock content blocks with a cache
        breakpoint at the end of the schema. Zones 1+2 (instructions + full schema)
        are cached; only zone 3 (the small canvas tail) is re-sent each turn."""
        dynamic = self._build_dynamic_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables,
        )

        if enriched and enriched.compact_tables:
            schema = self._get_cached_schema(enriched, connection_id)
            return [
                {"type": "text", "text": _INSTRUCTIONS},
                {"type": "text", "text": schema, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic},
            ]

        # No enriched schema → fall back to the raw doc. Cache instructions + raw
        # schema together (still stable per schema); canvas stays in the tail.
        raw_schema = self._build_schema_section_raw(schema_doc)
        return [
            {"type": "text", "text": _INSTRUCTIONS + "\n\n" + raw_schema,
             "cache_control": {"type": "ephemeral"}},
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
        )
        messages = conversation_history[-20:] + [{"role": "user", "content": message}]
        effective_model = BEDROCK_OPUS_MODEL if model_override == "opus" else CHAT_MODEL
        effective_max_tokens = 8192 if model_override == "opus" else 2048  # Opus needs more

        cached_len = sum(len(b["text"]) for b in system_blocks if "cache_control" in b)
        dynamic_len = sum(len(b["text"]) for b in system_blocks if "cache_control" not in b)
        print(
            f"[chat_agent] model={effective_model.split('/')[-1]}  "
            f"max_tokens={effective_max_tokens}  "
            f"history_turns={len(conversation_history) // 2}  "
            f"msg_len={len(message)}  "
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
        retry_messages = conversation_history[-20:] + [{"role": "user", "content": retry_msg}]
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
    ) -> dict:
        system_blocks, messages, model_id, max_tokens = self.prepare(
            message, conversation_history, schema_doc, dashboard_widgets,
            dashboard_pages, active_page_id, priority_tables, enriched_schema,
            model_override, connection_id,
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
    async def save_history(session_id: str, messages: list[dict], redis) -> None:
        trimmed = messages[-40:]
        if redis is None:
            _memory_history[session_id] = trimmed
            return
        await redis.setex(
            f"chat:history:{session_id}",
            CONVERSATION_TTL_SECONDS,
            json.dumps({"messages": trimmed}),
        )

    @staticmethod
    async def clear_history(session_id: str, redis) -> None:
        if redis is None:
            _memory_history.pop(session_id, None)
            return
        await redis.delete(f"chat:history:{session_id}")
