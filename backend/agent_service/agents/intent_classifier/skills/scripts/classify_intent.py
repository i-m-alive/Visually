"""
Skill: classify_intent
Classify user text into SINGLE_VIZ | DASHBOARD | SCREENSHOT | FOLLOWUP.
Usage:
    python classify_intent.py "show me monthly revenue by region"
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL

SYSTEM_PROMPT = """You are an intent classification system for a data visualization platform.
Classify the user message into exactly one intent type.

INTENT TYPES:
- SINGLE_VIZ: one chart/visualization
- DASHBOARD: multiple charts or overview
- SCREENSHOT: image/file attached
- FOLLOWUP: refers to a prior result with pronouns

Return ONLY valid JSON:
{
  "intent_type": "SINGLE_VIZ",
  "confidence": 0.9,
  "vagueness_score": 0.4,
  "reasoning": "one sentence"
}"""


async def classify_intent(text: str) -> dict:
    raw = await bedrock_invoke(
        model_id=BEDROCK_HAIKU_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=text,
        max_tokens=256,
        temperature=0.0,
    )
    return json.loads(raw.strip())


if __name__ == "__main__":
    text = " ".join(sys.argv[1:]) or "show me monthly revenue by region"
    result = asyncio.run(classify_intent(text))
    print(json.dumps(result, indent=2))
