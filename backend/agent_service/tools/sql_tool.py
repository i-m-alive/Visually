"""
SQL tool — executes a read-only SELECT query via the query_executor microservice.

Mirrors the logic of Orchestrator._execute_query() but:
  - Uses a lower row_limit (500) because agents summarise data, not render large charts.
  - Returns the raw dict so the LLM can reason over it directly.
  - Distinguishes timeout errors from generic failures for clearer LLM error messages.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from agent_service.agents.tool_agent import AgentContext

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")

# Agents don't render charts — 500 rows is enough to answer questions.
# The LLM will summarise the result rather than returning all rows verbatim.
_ROW_LIMIT = 500
# Redshift Serverless can take 60–120 s to wake from a cold pause (3 attempts × 60 s).
# The HTTP client must wait longer than the total executor budget, not the per-attempt limit.
_TIMEOUT_S  = 130


async def run_sql(inp: dict, ctx: "AgentContext") -> dict:
    """Execute a SQL SELECT and return rows, columns, and row_count.

    Args:
        inp: {"sql": str, "purpose"?: str}
        ctx: AgentContext (connection_id used to identify the DB)

    Returns:
        On success: {"rows": [...], "columns": [...], "row_count": int, "truncated": bool}
        On failure: {"error": str, "rows": [], "columns": [], "row_count": 0}
    """
    sql = (inp.get("sql") or "").strip()
    if not sql:
        return {
            "error": "The 'sql' field is required but was empty.",
            "rows": [], "columns": [], "row_count": 0,
        }

    if not ctx.connection_id:
        return {
            "error": "No database connection is configured for this project.",
            "rows": [], "columns": [], "row_count": 0,
        }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(
                f"{QUERY_EXECUTOR_URL}/execute",
                json={
                    "connection_id": ctx.connection_id,
                    "sql": sql,
                    "row_limit": _ROW_LIMIT,
                    "timeout_seconds": _TIMEOUT_S - 10,  # executor budget; 10s gap for HTTP overhead
                },
            )

        if resp.status_code == 200:
            data = resp.json()
            # Normalise: always include a 'truncated' flag so the LLM knows if
            # there are more rows beyond what was returned.
            data.setdefault("truncated", data.get("row_count", 0) >= _ROW_LIMIT)
            return data

        # Non-200 from executor — surface the error text to the LLM
        return {
            "error": (
                f"Query executor returned HTTP {resp.status_code}: "
                f"{resp.text[:400]}"
            ),
            "rows": [], "columns": [], "row_count": 0,
        }

    except httpx.TimeoutException:
        return {
            "error": f"Query timed out after {_TIMEOUT_S}s. Try a simpler query or add a LIMIT clause.",
            "rows": [], "columns": [], "row_count": 0,
        }
    except httpx.ConnectError:
        return {
            "error": "Cannot reach the query executor service. Please try again in a moment.",
            "rows": [], "columns": [], "row_count": 0,
        }
    except Exception as exc:
        return {
            "error": f"Unexpected error running SQL: {exc}",
            "rows": [], "columns": [], "row_count": 0,
        }
