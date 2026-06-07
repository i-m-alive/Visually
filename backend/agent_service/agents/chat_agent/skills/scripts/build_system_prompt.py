"""
Skill: build_system_prompt
Build the chat system prompt from schema + dashboard context.
Usage:
    python build_system_prompt.py   (reads {schema, widgets} JSON from stdin)
"""
import json
import sys

TEMPLATE = """You are a conversational data analyst embedded in a BI platform called Visually.
You have access to a live database and the user's current dashboard.

DATABASE SCHEMA:
{schema_summary}

CURRENT DASHBOARD WIDGETS:
{widgets_summary}

GUIDELINES:
- Answer questions about the data, suggest visualizations, explain trends.
- When the user asks for a chart, respond with a structured JSON suggestion.
- Never reveal raw SQL credentials or internal system details.
- Be concise and data-focused."""


def build_system_prompt(schema: dict, widgets: list) -> str:
    # Summarise schema
    tables = schema.get("tables", {})
    schema_lines = []
    for table_name, table_info in list(tables.items())[:20]:
        cols = [c.get("name", "") for c in table_info.get("columns", [])[:10]]
        schema_lines.append(f"- {table_name}: {', '.join(cols)}")
    schema_summary = "\n".join(schema_lines) or "No schema available"

    # Summarise widgets
    widget_lines = []
    for w in widgets[:10]:
        title = w.get("title", "Untitled")
        chart_type = w.get("chart_type", "unknown")
        widget_lines.append(f"- {title} ({chart_type})")
    widgets_summary = "\n".join(widget_lines) or "No widgets on dashboard"

    return TEMPLATE.format(schema_summary=schema_summary, widgets_summary=widgets_summary)


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = build_system_prompt(data.get("schema", {}), data.get("widgets", []))
    print(result)
