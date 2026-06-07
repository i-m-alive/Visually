import json
import re
from typing import Optional
from shared.bedrock_client import bedrock_invoke, bedrock_invoke_with_history, BEDROCK_SONNET_MODEL

CHAT_MODEL = BEDROCK_SONNET_MODEL
CONVERSATION_TTL_SECONDS = 4 * 60 * 60  # 4 hours

# In-memory fallback when Redis is unavailable
_memory_history: dict[str, list[dict]] = {}

SYSTEM_PROMPT_TEMPLATE = """You are a conversational data analyst embedded in a BI platform called Visually.
You have access to a live database and the user's current dashboard.

DATABASE SCHEMA:
{schema_summary}

CURRENT DASHBOARD WIDGETS:
{dashboard_context}

CAPABILITIES:
1. Answer questions about data visible in the current dashboard charts.
2. Answer questions about any data in the connected database (you can write SQL).
3. Generate new chart visualizations inline in this conversation.
4. Explain trends, anomalies, and patterns in plain English.
5. Modify the dashboard: filter charts, suggest follow-up queries.

WHEN TO GENERATE SQL:
If the user asks a question that requires data not already in the dashboard context, generate SQL.
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


class ChatAgent:
    def _build_system_prompt(self, schema_doc: dict, dashboard_widgets: list) -> str:
        schema_parts = []
        for table in schema_doc.get("tables", [])[:10]:
            col_names = [c["name"] for c in table.get("columns", [])[:15]]
            schema_parts.append(
                f"Table: {table['name']} ({table.get('description', '')}) | Columns: {', '.join(col_names)}"
            )
        schema_summary = "\n".join(schema_parts) if schema_parts else "Schema not available."

        dashboard_parts = []
        for w in dashboard_widgets:
            data_preview = ""
            if w.get("chart_data") and isinstance(w["chart_data"], dict):
                rows = w["chart_data"].get("rows", [])
                if rows:
                    data_preview = f" | Sample: {rows[:2]}"
            dashboard_parts.append(
                f"Widget: {w.get('title', 'Untitled')} | Type: {w.get('chart_type', 'unknown')} | SQL: {(w.get('sql_query') or '')[:200]}{data_preview}"
            )
        dashboard_context = "\n".join(dashboard_parts) if dashboard_parts else "No widgets on dashboard yet."

        return SYSTEM_PROMPT_TEMPLATE.format(
            schema_summary=schema_summary,
            dashboard_context=dashboard_context,
        )

    async def respond(
        self,
        message: str,
        conversation_history: list[dict],
        schema_doc: dict,
        dashboard_widgets: list,
    ) -> dict:
        system_prompt = self._build_system_prompt(schema_doc, dashboard_widgets)
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
