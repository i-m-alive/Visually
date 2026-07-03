import asyncio
import time
from typing import Optional


def _execute_sync(
    account: str,
    user: str,
    password: str,
    database: Optional[str],
    sql: str,
    warehouse: Optional[str],
    role: Optional[str],
    schema: Optional[str],
    row_limit: int,
) -> dict:
    import snowflake.connector
    start = time.monotonic()

    connect_kwargs = {
        "account": account,
        "user": user,
        "password": password,
        "login_timeout": 30,
        "network_timeout": 60,
    }
    if database:
        connect_kwargs["database"] = database
    if warehouse:
        connect_kwargs["warehouse"] = warehouse
    if role:
        connect_kwargs["role"] = role
    if schema:
        connect_kwargs["schema"] = schema

    conn = None
    try:
        conn = snowflake.connector.connect(**connect_kwargs)
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        all_rows = cursor.fetchmany(row_limit + 1)
        truncated = len(all_rows) > row_limit
        rows_subset = all_rows[:row_limit]
        rows_as_dicts = [dict(zip(columns, row)) for row in rows_subset]

        for row in rows_as_dicts:
            for k, v in row.items():
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
                elif not isinstance(v, (str, int, float, bool, type(None))):
                    row[k] = str(v)

        return {
            "rows": rows_as_dicts,
            "row_count": len(rows_as_dicts),
            "columns": columns,
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": truncated,
            "error": None,
        }
    except Exception as exc:
        print(f"[snowflake] ✗ query failed: {exc}", flush=True)
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(exc),
        }
    finally:
        if conn:
            conn.close()


async def execute_snowflake(
    account: str,
    user: str,
    password: str,
    database: Optional[str],
    sql: str,
    warehouse: Optional[str] = None,
    role: Optional[str] = None,
    schema: Optional[str] = None,
    row_limit: int = 10000,
    timeout_seconds: int = 30,
) -> dict:
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                None,
                _execute_sync,
                account, user, password, database, sql,
                warehouse, role, schema, row_limit,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": timeout_seconds * 1000,
            "truncated": False,
            "error": f"Query timed out after {timeout_seconds}s (Snowflake)",
        }
