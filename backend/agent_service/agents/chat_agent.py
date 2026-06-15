import json
import re
from typing import Optional, Literal, TYPE_CHECKING
from shared.bedrock_client import (
    bedrock_invoke_with_history_cached,
    BEDROCK_SONNET_MODEL,
    BEDROCK_OPUS_MODEL,
)

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

CHAT_MODEL = BEDROCK_SONNET_MODEL
CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours

# In-memory fallback when Redis is unavailable
_memory_history: dict[str, list[dict]] = {}

# Static instructions — no variables. Identical on every call → Bedrock prompt cache always hits.
_SYSTEM_INSTRUCTIONS = """You are a conversational data analyst embedded in a BI platform called Visually.
You have full access to the user's live database and their complete multi-page canvas report.

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

  STEP 1 — Write one short sentence (max 12 words) acknowledging the question.
  STEP 2 — IMMEDIATELY output a sql_execute block to fetch the answer.
           Use chart_type "table" for row-level results, "kpi" for a single number,
           "multi_row_card" for ranked/grouped numbers.
  STEP 3 — STOP. Do NOT describe what the SQL does.

❌ FORBIDDEN — these responses FAIL the user:
   "I'll query the billing hours for Scarbrough Medlin in 2023."  ← no block = WRONG
   "Let me look up that data for you."  ← no block = WRONG

✅ CORRECT:
   "Here are the billing hours for Scarbrough Medlin in 2023:"
   ```sql_execute
   {"sql": "SELECT ...", "chart_type": "table", "title": "Billing Hours - Scarbrough Medlin 2023", "x_label": "", "y_label": ""}
   ```

══════════════════════════════════════════════════════════════════════
CHART CREATION — MANDATORY EXECUTION PROTOCOL
══════════════════════════════════════════════════════════════════════
When the user asks to CREATE / BUILD / GENERATE / SHOW / MAKE / ADD
a chart, table, graph, visualization, or KPI:

  STEP 1 — Write one short sentence (max 10 words): "Here's the X:"
  STEP 2 — IMMEDIATELY output the sql_execute block below it.
  STEP 3 — STOP. Do NOT describe the chart. Do NOT explain what the SQL does.

❌ ABSOLUTELY FORBIDDEN — these responses will FAIL the user:
   "This pie chart shows the distribution of..."
   "I'll create a bar chart that visualizes..."
   "The chart displays data from the table..."
   Any response that describes a chart WITHOUT a sql_execute block.

✅ CORRECT — this is the ONLY acceptable pattern:
   "Here's the Current Status pie chart:"
   ```sql_execute
   {"sql": "...", "chart_type": "pie", ...}
   ```

BEFORE you write your response, ask yourself:
  - Does it contain a sql_execute block? If NO → you are WRONG. Add one.
  - Does it describe what the chart shows? If YES → delete that description.

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
Response: "Here are 2 charts:"
```sql_execute
{"sql": "SELECT DATE_TRUNC('month', start_date) AS \"Month\", COUNT(*) AS \"Placements\" FROM bullhorn_core_placement GROUP BY 1 ORDER BY 1", "chart_type": "bar_vertical", "title": "Monthly Placements", "x_label": "Month", "y_label": "Placements"}
```
```sql_execute
{"sql": "SELECT COUNT(*) AS \"Total Placements\" FROM bullhorn_core_placement", "chart_type": "kpi", "title": "Total Placements", "x_label": "", "y_label": ""}
```

RESPONSE FORMAT:
For data questions (what, how many, show, find, list, total, etc.): one sentence + sql_execute block (see DATA QUESTIONS above).
For chart/table/KPI creation: one sentence + sql_execute block(s) (see CHART CREATION above).
For conversational questions (greetings, explanations, "what is X concept"): plain English only, no sql_execute.

```sql_execute
{"sql": "SELECT ...", "chart_type": "bar_vertical|line|pie|kpi|multi_row_card|scatter|table|waterfall|area|donut|slicer", "title": "Chart Title", "x_label": "...", "y_label": "..."}
```

CHART TYPE SELECTION RULES:
- Use "pie" for proportional distributions (status breakdown, category share, etc.)
- Use "kpi" ONLY for a single aggregate number (one row, one value): SELECT COUNT(*) AS value FROM table
- Use "multi_row_card" when the user wants a KPI broken down by a dimension — multiple label/value pairs:
    SQL pattern: SELECT dim AS label, COUNT(*)/SUM(metric) AS value FROM table GROUP BY 1 ORDER BY 2 DESC LIMIT 20
- Use "table" for detailed row-level data with many columns
- Use "bar_vertical" when the user wants to compare values visually

CHART CREATION EXAMPLES — copy these patterns exactly:

Example 1 — pie chart by specific columns (user names table + columns):
User: "Create a PieChart for current status. Table: bullhorn_core_placement. Columns: placementID, status."
Response: "Here's the Current Status pie chart:"
```sql_execute
{"sql": "SELECT status AS \"Status\", COUNT(DISTINCT \"placementID\") AS \"Count\" FROM bullhorn_core_placement GROUP BY status ORDER BY 2 DESC LIMIT 20", "chart_type": "pie", "title": "Current Status", "x_label": "Status", "y_label": "Count"}
```

Example 2 — table chart:
User: "Create a table chart showing employee name and salary"
Response: "Here's the table:"
```sql_execute
{"sql": "SELECT name AS \"Name\", salary AS \"Salary\" FROM employees ORDER BY salary DESC LIMIT 1000", "chart_type": "table", "title": "Employee Salaries", "x_label": "", "y_label": ""}
```

Example 3 — grouped KPI (multi_row_card):
User: "Show job count broken down by source type" or "KPI showing jobs per category"
Response: "Here's the Job Count by Source:"
```sql_execute
{"sql": "SELECT source AS \"Source\", COUNT(*) AS \"Count\" FROM jobs GROUP BY source ORDER BY 2 DESC LIMIT 20", "chart_type": "multi_row_card", "title": "Job Count by Source", "x_label": "Source", "y_label": "Count"}
```

Example 4 — slicer / filter widget:
User: "Add a filter for status" / "Create a dropdown to filter by region" / "Add a checkbox slicer for category"
Response: "Here's a Status slicer:"
```sql_execute
{"sql": "SELECT DISTINCT status FROM jobs WHERE status IS NOT NULL ORDER BY 1 LIMIT 300", "chart_type": "slicer", "title": "Status Filter", "slicer_type": "dropdown", "slicer_column": "status", "x_label": "", "y_label": ""}
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
{"action": "filter_widget"|"add_widget"|"rename_widget", "params": {}}
```

══════════════════════════════════════════════════════════════════════
ANALYTICAL QUESTIONS — INSIGHT PROTOCOL
══════════════════════════════════════════════════════════════════════
When the user asks WHY a metric has a certain value, what is DRIVING it,
or wants EXPLANATION / INSIGHT — e.g.:
  "What is driving X being Y?", "Why is X so high?", "Explain why X",
  "What factors affect X?", "What caused X?", "Give me insight into X"

  STEP 1 — Write 2-4 sentences of direct analysis. Reference specific dimensions
           that are likely influencing the metric (time trend, category split,
           status distribution, source type, geography, etc.).
  STEP 2 — Provide 1-3 sql_execute blocks showing MEANINGFUL BREAKDOWNS:
           • By relevant dimension (status, source, region, month, etc.)
           • chart_type: bar_vertical, line, pie, or table — NOT "kpi"
           • DO NOT re-query the same single aggregate — that shows no insight
  STEP 3 — STOP.

❌ WRONG for "What is driving Total Job Orders being 7,377?":
   A single KPI card showing "7,382" — that's the same number, zero insight.

✅ CORRECT for "What is driving Total Job Orders being 7,377?":
   "Job orders have reached 7,382. Here's the breakdown by source to see what's contributing most:"
   ```sql_execute
   {"sql": "SELECT source AS \"Source\", COUNT(*) AS \"Count\" FROM job_orders GROUP BY source ORDER BY 2 DESC LIMIT 15", "chart_type": "bar_vertical", "title": "Job Orders by Source", "x_label": "Source", "y_label": "Count"}
   ```
   "And here's how they've trended over the past 12 months:"
   ```sql_execute
   {"sql": "SELECT DATE_TRUNC('month', created_date) AS \"Month\", COUNT(*) AS \"Count\" FROM job_orders GROUP BY 1 ORDER BY 1", "chart_type": "line", "title": "Job Orders Monthly Trend", "x_label": "Month", "y_label": "Count"}
   ```

══════════════════════════════════════════════════════════════════════

WHEN THE USER MESSAGE CONTAINS AN [INTELLIGENCE REPORT] OR ╔══ BLOCK:
The user has already provided the full pre-computed report data inline.
- Answer IMMEDIATELY with specific numbers from that data block.
- NEVER say "I'll analyze", "I would show you", "I'll look into", or "Let me check".
- If the answer is in the data, cite the exact numbers right away.
- Only generate SQL if the question explicitly asks for something NOT present in the provided data.

TONE: Be concise, helpful, and data-focused. Reference actual values from the data. Avoid filler phrases."""

_CONV_STARTERS = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|great|sure|got it|cool|nice|awesome|"
    r"good morning|good afternoon|good evening|what (is|are|does|do) (a|an|the)?\s*(?!data|table|column|metric)\w+\b)",
    re.IGNORECASE,
)
_DASHBOARD_WORDS = re.compile(
    r"\b(how many (chart|widget|page)|list (chart|widget|page)|rename|delete (chart|widget)|"
    r"which page|active page|canvas structure|how is (this|the) (report|canvas|dashboard) (set up|structured|organized))\b",
    re.IGNORECASE,
)
_DATA_WORDS = re.compile(
    r"\b(what|how many|how much|which|who|when|where|show|find|list|get|give|tell|fetch|"
    r"retrieve|calculate|compute|sum|count|total|average|top|bottom|highest|lowest|most|least|"
    r"compare|breakdown|analyse|analyze|create|make|build|generate|add|chart|graph|kpi|table|"
    r"pie|bar|line|visual|plot|donut|scatter|waterfall|treemap|revenue|sales|data|metric|trend)\b",
    re.IGNORECASE,
)


def _classify_intent(message: str) -> Literal["conversational", "dashboard", "data"]:
    """Route the message to one of three cost tiers.

    conversational — no schema needed (greetings, concept explanations)
    dashboard      — canvas structure questions only, no DB schema needed
    data           — needs schema + SQL capability
    """
    stripped = message.strip()
    if _CONV_STARTERS.match(stripped) and not _DATA_WORDS.search(stripped):
        return "conversational"
    if _DASHBOARD_WORDS.search(stripped) and not _DATA_WORDS.search(stripped):
        return "dashboard"
    return "data"


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


_ANALYTICAL_PATTERNS = re.compile(
    r"\b(what is driving|what's driving|what are driving|why is|why are|why was|why were|"
    r"what caused|what factors|what factor|what leads|what's causing|what is causing|"
    r"explain (why|how|what)|give me insight|insight into|root cause|what contributed|"
    r"what's behind|what is behind|analyze (why|this|the|how)|analyse (why|this|the|how)|"
    r"dig into|break(down)? (why|the reason)|reason (for|behind|why)|understand why)\b",
    re.IGNORECASE,
)


def _is_analytical_request(message: str) -> bool:
    """Return True for insight/explanation questions — these need narrative + breakdowns,
    NOT a single KPI of the same number."""
    return bool(_ANALYTICAL_PATTERNS.search(message))


def _is_data_query_request(message: str) -> bool:
    """Return True when the message is asking a data question that requires SQL."""
    lower = message.lower()
    # Analytical questions are handled by a different protocol — skip the forced-SQL path
    if _is_analytical_request(lower):
        return False
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
    def _build_schema_section_enriched(
        self,
        message: str,
        enriched: "EnrichedSchema",
        priority_tables: Optional[set[str]] = None,
    ) -> str:
        """Build a rich schema section. Priority tables get a +5 boost in TF-IDF ranking."""
        compact = enriched.compact_tables or []
        total_tables = len(compact)
        priority_tables = priority_tables or set()

        MAX_TABLES = 8
        scored = sorted(
            compact,
            key=lambda t: _tfidf_score(message, t)
            + (5.0 if t.get("name", "").lower() in priority_tables else 0.0),
            reverse=True,
        )
        top_tables = scored[:MAX_TABLES]
        # Only the top 3 tables get sample values — they dominate token cost
        sample_allowed = {t.get("name") for t in top_tables[:3]}

        lines = [
            f"DATABASE SCHEMA ({total_tables} tables total"
            f" — showing {len(top_tables)} most relevant to your question):"
        ]
        if priority_tables:
            lines.append(
                f"PRIORITY TABLES (already used in this canvas — prefer these): "
                f"{', '.join(sorted(priority_tables))}"
            )

        for t in top_tables:
            tname = t.get("name", "")
            desc = t.get("description") or ""
            row_count = t.get("row_count")
            row_hint = f"  ~{row_count:,} rows" if row_count else ""
            in_use = " ★" if tname.lower() in priority_tables else ""
            lines.append(f"\n[{tname}]{in_use}{row_hint}")
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
            include_samples = tname in sample_allowed
            for c in cols:
                cname = c.get("name") or ""
                ctype = c.get("type") or ""
                cdesc = c.get("description") or ""
                sem_type = c.get("semantic_type") or ""
                tag = f"[{sem_type}]" if sem_type else ""
                col_parts.append(f"{cname}{tag} ({ctype}){': ' + cdesc if cdesc else ''}")

                if not include_samples:
                    continue
                stats = c.get("stats") or {}
                top_vals = stats.get("top_values") or []
                if top_vals:
                    sample_strs = []
                    for rv in top_vals[:8]:
                        if isinstance(rv, dict):
                            val = rv.get(cname) or next(iter(rv.values()), None)
                        else:
                            val = rv
                        if val is not None:
                            sample_strs.append(str(val))
                    if sample_strs:
                        sample_parts.append(f"{cname}: [{', '.join(sample_strs)}]")

            if col_parts:
                lines.append(f"  Columns: {' | '.join(col_parts[:30])}")
            if sample_parts:
                lines.append(f"  Sample values: {' | '.join(sample_parts)}")

        top_names = {t.get("name") for t in top_tables}
        join_hints = []
        seen_edges: set[frozenset] = set()
        for tbl_a, neighbors in enriched.relationship_graph.edges.items():
            if tbl_a not in top_names:
                continue
            for tbl_b, condition in neighbors.items():
                edge_key = frozenset([tbl_a, tbl_b])
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    join_hints.append(f"  {condition}")
        if join_hints:
            lines.append("\nJOIN CONDITIONS:")
            lines.extend(join_hints[:20])

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
            is_active = pid == active_page_id
            active_marker = " (ACTIVE — new charts go here)" if is_active else ""
            parts.append(f"\nPage '{p['name']}'{active_marker}:")
            for w in page_widgets:
                if is_active:
                    sql_preview = (w.get("sql_query") or "")[:180]
                    rows = (w.get("chart_data") or {}).get("rows", [])
                    sample = f" | Sample data: {rows[:1]}" if rows else ""
                    parts.append(
                        f"  • {w['title']} [{w['chart_type']}]"
                        + (f" | SQL: {sql_preview}" if sql_preview else "")
                        + sample
                    )
                else:
                    # Inactive pages: name + type only — SQL omitted to save tokens
                    parts.append(f"  • {w['title']} [{w['chart_type']}]")

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

    def _build_dynamic_context(
        self,
        message: str,
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
        enriched: Optional["EnrichedSchema"],
        intent: str,
    ) -> str:
        """Build the per-request dynamic context block (schema + canvas).

        Returns an empty string for conversational intent.
        Returns canvas-only for dashboard intent.
        Returns schema + canvas for data intent.
        """
        parts: list[str] = []

        if intent == "data":
            if enriched and enriched.compact_tables:
                schema_section = self._build_schema_section_enriched(message, enriched, priority_tables)
            elif schema_doc:
                schema_section = self._build_schema_section_raw(schema_doc)
            else:
                schema_section = ""
            if schema_section:
                parts.append(schema_section)

        if intent in ("data", "dashboard"):
            dashboard_context = self._build_dashboard_context(
                dashboard_widgets, dashboard_pages, active_page_id, priority_tables
            )
            if dashboard_context:
                parts.append(f"CANVAS REPORT STRUCTURE:\n{dashboard_context}")

        return "\n\n".join(parts)

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
    ) -> dict:
        intent = _classify_intent(message)
        dynamic_context = self._build_dynamic_context(
            message=message,
            schema_doc=schema_doc,
            dashboard_widgets=dashboard_widgets or [],
            dashboard_pages=dashboard_pages or [],
            active_page_id=active_page_id,
            priority_tables=priority_tables,
            enriched=enriched_schema,
            intent=intent,
        )

        messages = conversation_history[-20:] + [{"role": "user", "content": message}]
        effective_model = BEDROCK_OPUS_MODEL if model_override == "opus" else CHAT_MODEL
        effective_max_tokens = 8192 if model_override == "opus" else 2048

        print(
            f"[chat_agent] intent={intent}  model={effective_model.split('/')[-1]}  "
            f"max_tokens={effective_max_tokens}  "
            f"history_turns={len(conversation_history) // 2}  "
            f"msg_len={len(message)}  "
            f"stable_len={len(_SYSTEM_INSTRUCTIONS)}  "
            f"dynamic_len={len(dynamic_context)}",
            flush=True,
        )

        raw, cache_info = await bedrock_invoke_with_history_cached(
            model_id=effective_model,
            system_stable=_SYSTEM_INSTRUCTIONS,
            system_dynamic=dynamic_context,
            messages=messages,
            max_tokens=effective_max_tokens,
            temperature=0.3,
        )
        print(
            f"[chat_agent] cache={'HIT' if cache_info['cache_hit'] else ('CREATED' if cache_info['cache_created'] else 'MISS')}  "
            f"cache_read={cache_info['cache_read_tokens']}  cache_create={cache_info['cache_creation_tokens']}",
            flush=True,
        )

        print(
            f"[chat_agent] response_len={len(raw)}  "
            f"has_sql={'yes' if '```sql_execute' in raw else 'no'}  "
            f"has_action={'yes' if '```dashboard_action' in raw else 'no'}",
            flush=True,
        )

        sqls_to_execute: list[dict] = []
        dashboard_action = None
        text = raw

        if "```sql_execute" in raw:
            all_blocks = re.findall(r"```sql_execute\n(.*?)\n```", raw, re.DOTALL)
            for block in all_blocks:
                try:
                    spec = json.loads(block.strip())
                    sqls_to_execute.append(spec)
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

        # ── Auto-retry when model narrated instead of executing ───────────────
        # If the user asked a data question or chart creation but got no sql_execute,
        # send one silent retry with an ultra-strict prompt.
        # Analytical questions ("what is driving X", "why is X") are excluded — they
        # should return narrative + breakdowns, not a forced single sql block.
        needs_sql = (
            not _is_analytical_request(message)
            and (_is_chart_creation_request(message) or _is_data_query_request(message))
        )
        if not sqls_to_execute and needs_sql:
            retry_msg = (
                "EXECUTE ONLY — output NOTHING except a single sql_execute block.\n"
                "Do NOT write any description, acknowledgment, or explanation.\n"
                "The user is waiting for actual data — you MUST output a sql_execute block.\n"
                f"Original request: {message}"
            )
            retry_messages = conversation_history[-20:] + [{"role": "user", "content": retry_msg}]
            try:
                raw2, _ = await bedrock_invoke_with_history_cached(
                    model_id=CHAT_MODEL,
                    system_stable=_SYSTEM_INSTRUCTIONS,
                    system_dynamic=dynamic_context,
                    messages=retry_messages,
                    max_tokens=1024,
                    temperature=0.0,
                )
                if "```sql_execute" in raw2:
                    retry_blocks = re.findall(r"```sql_execute\n(.*?)\n```", raw2, re.DOTALL)
                    for block in retry_blocks:
                        try:
                            sqls_to_execute.append(json.loads(block.strip()))
                        except json.JSONDecodeError:
                            pass
                    sql_to_execute = sqls_to_execute[0] if sqls_to_execute else None
            except Exception:
                pass  # retry failed — return original narration, frontend shows retry button

        return {
            "text": text,
            "sql_to_execute": sql_to_execute,
            "sqls_to_execute": sqls_to_execute,
            "dashboard_action": dashboard_action,
        }

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
