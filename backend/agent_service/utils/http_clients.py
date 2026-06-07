"""HTTP client helpers for calling internal services."""
import os
import httpx

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
RENDER_SERVICE_URL = os.getenv("RENDER_SERVICE_URL", "http://localhost:3001")


async def call_query_executor(connection_id: str, sql: str, row_limit: int = 10000) -> dict:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={"connection_id": connection_id, "sql": sql, "row_limit": row_limit, "timeout_seconds": 30},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"rows": [], "row_count": 0, "columns": [], "error": f"Executor {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"rows": [], "row_count": 0, "columns": [], "error": str(e)}


async def call_render_service(query_plan: dict, rows: list) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{RENDER_SERVICE_URL}/render",
                json={"query_plan": query_plan, "rows": rows},
            )
            return resp.json() if resp.status_code == 200 else {}
    except Exception:
        return {}
