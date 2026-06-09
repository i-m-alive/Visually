import json
import re
from typing import Optional, TYPE_CHECKING
from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL

if TYPE_CHECKING:
    from agent_service.agents.schema_cache import EnrichedSchema

CHAT_MODEL = BEDROCK_SONNET_MODEL
CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours

# In-memory fallback when Redis is unavailable
_memory_history: dict[str, list[dict]] = {}

_SYSTEM_PROMPT_TEMPLATE = """You are a conversational data analyst embedded in a BI platform called Visually.
You have full access to the user's live database and their complete multi-page canvas report.

{schema_section}

CANVAS REPORT STRUCTURE:
{dashboard_context}

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

WHEN TO GENERATE SQL:
If the user asks a question that requires data not already in the canvas context, generate SQL.
Return SQL inside a special JSON block so the system can execute it.

RESPONSE FORMAT:
For text answers: respond in plain conversational English.
For SQL queries you want executed, include this anywhere in your response:
```sql_execute
{{"sql": "SELECT ...", "chart_type": "bar_vertical|line|pie|kpi|scatter|table", "title": "Chart Title", "x_label": "...", "y_label": "..."}}
```

For dashboard modifications, include:
```dashboard_action
{{"action": "filter_widget"|"add_widget"|"rename_widget", "params": {{}}}}
```

TONE: Be concise, helpful, and data-focused. Reference actual values from the data. Avoid filler phrases."""


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

        if total_tables > 18:
            scored = sorted(
                compact,
                key=lambda t: _tfidf_score(message, t)
                + (5.0 if t.get("name", "").lower() in priority_tables else 0.0),
                reverse=True,
            )
            top_tables = scored[:18]
        else:
            top_tables = compact

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
            for c in cols:
                cname = c.get("name") or ""
                ctype = c.get("type") or ""
                cdesc = c.get("description") or ""
                sem_type = c.get("semantic_type") or ""
                tag = f"[{sem_type}]" if sem_type else ""
                col_parts.append(f"{cname}{tag} ({ctype}){': ' + cdesc if cdesc else ''}")

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
            active_marker = " (ACTIVE — new charts go here)" if pid == active_page_id else ""
            parts.append(f"\nPage '{p['name']}'{active_marker}:")
            for w in page_widgets:
                sql_preview = (w.get("sql_query") or "")[:180]
                rows = (w.get("chart_data") or {}).get("rows", [])
                sample = f" | Sample data: {rows[:1]}" if rows else ""
                parts.append(
                    f"  • {w['title']} [{w['chart_type']}]"
                    + (f" | SQL: {sql_preview}" if sql_preview else "")
                    + sample
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

    def _build_system_prompt(
        self,
        message: str,
        schema_doc: dict,
        dashboard_widgets: list,
        dashboard_pages: list,
        active_page_id: Optional[str],
        priority_tables: Optional[set[str]],
        enriched: Optional["EnrichedSchema"] = None,
    ) -> str:
        if enriched and enriched.compact_tables:
            schema_section = self._build_schema_section_enriched(
                message, enriched, priority_tables
            )
        else:
            schema_section = self._build_schema_section_raw(schema_doc)

        dashboard_context = self._build_dashboard_context(
            dashboard_widgets, dashboard_pages, active_page_id, priority_tables
        )

        return _SYSTEM_PROMPT_TEMPLATE.format(
            schema_section=schema_section,
            dashboard_context=dashboard_context,
        )

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
    ) -> dict:
        system_prompt = self._build_system_prompt(
            message=message,
            schema_doc=schema_doc,
            dashboard_widgets=dashboard_widgets,
            dashboard_pages=dashboard_pages or [],
            active_page_id=active_page_id,
            priority_tables=priority_tables,
            enriched=enriched_schema,
        )
        messages = conversation_history[-20:] + [{"role": "user", "content": message}]

        raw = await bedrock_invoke_with_history(
            model_id=CHAT_MODEL,
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=2048,
            temperature=0.3,
        )

        sql_to_execute = None
        dashboard_action = None
        text = raw

        if "```sql_execute" in raw:
            match = re.search(r"```sql_execute\n(.*?)\n```", raw, re.DOTALL)
            if match:
                try:
                    sql_to_execute = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass
            text = re.sub(r"```sql_execute\n.*?\n```", "", text, flags=re.DOTALL).strip()

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
