"""
Skill: manage_conversation_history
Redis-backed conversation history storage for the Chat Agent.
Usage:
    python manage_conversation_history.py load <redis_url> <session_key>
    python manage_conversation_history.py append <redis_url> <session_key> <role> <content>
"""
import asyncio
import json
import sys

CONVERSATION_TTL = 4 * 60 * 60  # 4 hours
MAX_TURNS = 50  # keep last 50 messages


async def load_history(redis_url: str, session_key: str) -> list:
    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url)
    try:
        raw = await r.get(session_key)
        if raw:
            return json.loads(raw)
        return []
    finally:
        await r.aclose()


async def append_turn(redis_url: str, session_key: str, role: str, content: str) -> list:
    import redis.asyncio as aioredis
    r = aioredis.from_url(redis_url)
    try:
        raw = await r.get(session_key)
        history = json.loads(raw) if raw else []
        history.append({"role": role, "content": content})
        # Trim to last MAX_TURNS
        if len(history) > MAX_TURNS:
            history = history[-MAX_TURNS:]
        await r.set(session_key, json.dumps(history), ex=CONVERSATION_TTL)
        return history
    finally:
        await r.aclose()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "load"
    redis_url = sys.argv[2] if len(sys.argv) > 2 else "redis://localhost:6379"
    key = sys.argv[3] if len(sys.argv) > 3 else "chat:test"

    if cmd == "load":
        result = asyncio.run(load_history(redis_url, key))
    elif cmd == "append":
        role = sys.argv[4] if len(sys.argv) > 4 else "user"
        content = sys.argv[5] if len(sys.argv) > 5 else ""
        result = asyncio.run(append_turn(redis_url, key, role, content))
    else:
        result = []

    print(json.dumps(result, indent=2))
