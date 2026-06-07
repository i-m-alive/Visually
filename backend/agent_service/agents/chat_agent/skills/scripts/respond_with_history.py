"""
Skill: respond_with_history
Send conversation history + new message to Bedrock and get assistant reply.
Usage:
    python respond_with_history.py   (reads {system_prompt, messages, user_message} from stdin)
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL


async def respond_with_history(system_prompt: str, messages: list, user_message: str) -> str:
    full_messages = messages + [{"role": "user", "content": user_message}]
    return await bedrock_invoke_with_history(
        model_id=BEDROCK_SONNET_MODEL,
        system_prompt=system_prompt,
        messages=full_messages,
        max_tokens=1024,
        temperature=0.3,
    )


if __name__ == "__main__":
    data = json.load(sys.stdin)
    result = asyncio.run(respond_with_history(
        data.get("system_prompt", ""),
        data.get("messages", []),
        data.get("user_message", ""),
    ))
    print(result)
