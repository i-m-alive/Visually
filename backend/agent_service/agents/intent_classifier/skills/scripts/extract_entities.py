"""
Skill: extract_entities
Extract metrics, dimensions, time_range, chart_type, and filters from user text.
Usage:
    python extract_entities.py "bar chart of revenue by region last quarter"
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """Extract structured entities from the user message.

Return ONLY valid JSON:
{
  "metrics": ["revenue"],
  "dimensions": ["region"],
  "time_range": {"type": "relative", "value": "last quarter"},
  "chart_type": "bar",
  "filters": []
}

- metrics: numeric measure words
- dimensions: grouping/category words
- time_range: null if not mentioned
- chart_type: null if not specified
- filters: [{column, op, value}] for explicit conditions"""


async def extract_entities(text: str) -> dict:
    raw = await bedrock_invoke(
        model_id=BEDROCK_HAIKU_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=text,
        max_tokens=512,
        temperature=0.0,
    )
    return json.loads(raw.strip())


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "bar chart of revenue by region last quarter"
    result = asyncio.run(extract_entities(text))
    print(json.dumps(result, indent=2))
