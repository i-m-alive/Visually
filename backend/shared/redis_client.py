import os
import json
import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

_redis_available: bool | None = None  # None = not yet checked


async def get_redis() -> aioredis.Redis | None:
    """Return a connected Redis client, or None if Redis is unavailable."""
    global _redis_available
    try:
        client = aioredis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        await client.ping()
        if _redis_available is not True:
            print("[redis] Connected to Redis at", REDIS_URL)
        _redis_available = True
        return client
    except Exception:
        if _redis_available is not False:
            print("[redis] Redis not available — running without it (no real-time WebSocket events, in-memory chat history)")
        _redis_available = False
        return None


async def publish_pipeline_event(redis: aioredis.Redis | None, job_id: str, event: dict):
    if redis is None:
        return
    await redis.publish(f"pipeline:{job_id}", json.dumps(event))


async def set_pipeline_state(redis: aioredis.Redis | None, job_id: str, field: str, value: str):
    if redis is None:
        return
    await redis.hset(f"pipeline_state:{job_id}", field, value)
    await redis.expire(f"pipeline_state:{job_id}", 7200)


async def get_pipeline_state(redis: aioredis.Redis | None, job_id: str) -> dict:
    if redis is None:
        return {}
    return await redis.hgetall(f"pipeline_state:{job_id}")
