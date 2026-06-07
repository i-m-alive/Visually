"""
Skill: run_single_viz_pipeline
Standalone runner for the single-viz pipeline (useful for testing without the full service).
Usage:
    python run_single_viz_pipeline.py '{"text":"show revenue by month","project_id":"...","connection_id":"..."}'
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


async def run(text: str, project_id: str, connection_id: str):
    from shared.database import AsyncSessionLocal
    from shared.redis_client import get_redis
    from agent_service.agents.orchestrator import Orchestrator
    import uuid

    orch = Orchestrator()
    redis = await get_redis()
    job_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as db:
        await orch.run_single_viz_pipeline(
            job_id=job_id,
            user_text=text,
            project_id=project_id,
            user_id="00000000-0000-0000-0000-000000000001",
            connection_id=connection_id,
            redis=redis,
            db=db,
        )
    await redis.aclose()
    print(f"Pipeline complete. job_id={job_id}")


if __name__ == "__main__":
    data = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    asyncio.run(run(
        data.get("text", "show me total revenue by month"),
        data.get("project_id", ""),
        data.get("connection_id", ""),
    ))
