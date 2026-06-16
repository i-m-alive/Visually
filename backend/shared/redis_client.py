import os
import json
import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# Azure Container Apps internal TCP ingress can take >1s to resolve + connect on
# first contact; the old 1s timeout produced spurious "Redis not available" even
# though the Redis container was healthy. Give it real headroom (env-overridable).
_CONNECT_TIMEOUT = float(os.getenv("REDIS_CONNECT_TIMEOUT", "5"))

_client: "aioredis.Redis | None" = None  # cached, shared connection pool
_redis_available: bool | None = None      # None = not yet checked


async def get_redis() -> aioredis.Redis | None:
    """Return a connected (cached) Redis client, or None if Redis is unavailable.

    The client is cached so we don't open + leak a new connection pool on every
    call. On a connection failure we return None (callers degrade gracefully) and
    retry on the next call, so Redis can recover after a cold start.
    """
    global _client, _redis_available
    if _client is not None:
        return _client
    try:
        client = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=_CONNECT_TIMEOUT,
            socket_timeout=_CONNECT_TIMEOUT,
            retry_on_timeout=True,
            health_check_interval=30,
        )
        await client.ping()
        if _redis_available is not True:
            print("[redis] Connected to Redis at", REDIS_URL, flush=True)
        _redis_available = True
        _client = client
        return client
    except Exception as exc:
        if _redis_available is not False:
            print(
                f"[redis] Redis not available ({type(exc).__name__}: {exc}) — running "
                f"without it (no real-time events, in-memory chat history)",
                flush=True,
            )
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
