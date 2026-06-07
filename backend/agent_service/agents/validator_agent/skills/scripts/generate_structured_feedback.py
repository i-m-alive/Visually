"""
Skill: generate_structured_feedback
Synthesise validation scores into a prioritised retry prompt for the Query Agent.
Usage:
    python generate_structured_feedback.py
    (reads ValidationResult JSON from stdin, prints retry feedback to stdout)
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

SYSTEM_PROMPT = """You are a SQL debugging assistant.
Given validation scores for a chart query, generate concise, actionable retry instructions
for the SQL generator. Focus on the lowest-scoring dimensions first.

Return a plain text retry prompt (2-5 bullet points max)."""


async def generate_feedback(validation: dict) -> str:
    msg = f"Validation result:\n{json.dumps(validation, indent=2)}"
    return await bedrock_invoke(
        model_id=BEDROCK_SONNET_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_message=msg,
        max_tokens=512,
        temperature=0.0,
    )


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = asyncio.run(generate_feedback(data))
    print(result)
