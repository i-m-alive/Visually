"""
Skill: run_screenshot_pipeline
Standalone runner for the screenshot replication pipeline.
Usage:
    python run_screenshot_pipeline.py path/to/screenshot.png project_id connection_id
"""
import asyncio
import json
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))


async def run(image_path: str, project_id: str, connection_id: str):
    from shared.database import AsyncSessionLocal
    from shared.redis_client import get_redis
    from agent_service.agents.orchestrator import Orchestrator
    import uuid

    img_bytes = Path(image_path).read_bytes()
    filename = Path(image_path).name
    mime = "image/png" if filename.endswith(".png") else "image/jpeg"

    uploaded_images = [{"bytes": img_bytes, "filename": filename, "mime_type": mime}]

    orch = Orchestrator()
    redis = await get_redis()
    job_id = str(uuid.uuid4())
    screenshot_job_id = str(uuid.uuid4())

    async with AsyncSessionLocal() as db:
        await orch.run_screenshot_pipeline(
            job_id=job_id,
            screenshot_job_id=screenshot_job_id,
            uploaded_images=uploaded_images,
            project_id=project_id,
            user_id="00000000-0000-0000-0000-000000000001",
            connection_id=connection_id,
            redis=redis,
            db=db,
        )
    await redis.aclose()
    print(f"Screenshot pipeline complete. job_id={job_id}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: run_screenshot_pipeline.py <image_path> <project_id> <connection_id>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2], sys.argv[3]))
