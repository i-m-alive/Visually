"""
Skill: validate_axis_labels
Check that SQL result column names match the expected axis labels using Haiku.
Usage:
    python validate_axis_labels.py '{"columns":["month","total_revenue"],"x_label":"Month","y_label":"Revenue"}'
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """You are checking whether SQL column names match the expected chart axis labels.
Return a JSON object with a similarity score (0.0 to 1.0) and brief reasoning.

{"score": 0.85, "reasoning": "..."}"""


async def validate_axis_labels(columns: list, x_label: str, y_label: str) -> dict:
    msg = (
        f"SQL columns: {columns}\n"
        f"Expected x-axis label: {x_label}\n"
        f"Expected y-axis label: {y_label}\n"
        "Do the column names semantically match these labels?"
    )
    raw = await bedrock_invoke(
        model_id=BEDROCK_HAIKU_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=msg,
        max_tokens=256,
        temperature=0.0,
    )
    return json.loads(raw.strip())


if __name__ == "__main__":
    data = json.loads(sys.argv[1]) if len(sys.argv) > 1 else json.load(sys.stdin)
    result = asyncio.run(validate_axis_labels(
        data.get("columns", []),
        data.get("x_label", ""),
        data.get("y_label", ""),
    ))
    print(json.dumps(result, indent=2))
