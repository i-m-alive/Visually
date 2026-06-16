"""HTTP client helpers for calling internal services."""
import os
import httpx

QUERY_EXECUTOR_URL  = os.getenv("QUERY_EXECUTOR_URL",  "http://localhost:8002")
RENDER_SERVICE_URL  = os.getenv("RENDER_SERVICE_URL",  "http://localhost:3001")
SCHEMA_CRAWLER_URL  = os.getenv("SCHEMA_CRAWLER_URL",  "http://localhost:8003")


async def call_query_executor(
    connection_id: str,
    sql: str,
    row_limit: int = 10000,
    timeout_seconds: int = 30,
) -> dict:
    try:
        # Give httpx a 5s margin over the query executor's own timeout
        http_timeout = max(60.0, timeout_seconds + 5.0)
        async with httpx.AsyncClient(timeout=http_timeout) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={"connection_id": connection_id, "sql": sql, "row_limit": row_limit, "timeout_seconds": timeout_seconds},
            )
            if resp.status_code == 200:
                return resp.json()
            return {"rows": [], "row_count": 0, "columns": [], "error": f"Executor {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"rows": [], "row_count": 0, "columns": [], "error": str(e)}


async def call_schema_crawler(connection_id: str, project_id: str) -> None:
    """Fire-and-forget: ask the schema crawler to refresh a connection. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{SCHEMA_CRAWLER_URL}/crawl",
                json={"connection_id": connection_id, "project_id": project_id},
            )
    except Exception:
        pass


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
