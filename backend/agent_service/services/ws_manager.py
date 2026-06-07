import asyncio
import json
import redis.asyncio as aioredis
from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, job_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(job_id, []).append(websocket)

    def disconnect(self, job_id: str, websocket: WebSocket):
        connections = self.active_connections.get(job_id, [])
        if websocket in connections:
            connections.remove(websocket)

    async def broadcast(self, job_id: str, message: dict):
        for connection in list(self.active_connections.get(job_id, [])):
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


async def redis_listener(redis: aioredis.Redis, job_id: str):
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"pipeline:{job_id}")
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                await manager.broadcast(job_id, data)
                if data.get("type") in ("chart.confirmed", "pipeline.error"):
                    break
    finally:
        await pubsub.unsubscribe(f"pipeline:{job_id}")
