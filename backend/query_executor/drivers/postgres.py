import time
import asyncpg
from typing import Optional


async def execute_postgres(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    sql: str,
    timeout_seconds: int = 30,
    row_limit: int = 10000,
    ssl: bool = False,
) -> dict:
    start = time.monotonic()
    try:
        conn = await asyncpg.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            ssl="require" if ssl else None,
            command_timeout=timeout_seconds,
        )
        try:
            await conn.execute(f"SET statement_timeout = '{timeout_seconds * 1000}'")
            records = await conn.fetch(sql)
            elapsed_ms = (time.monotonic() - start) * 1000

            truncated = len(records) >= row_limit
            rows = [dict(r) for r in records[:row_limit]]
            columns = list(rows[0].keys()) if rows else []

            # Serialize non-serializable types
            for row in rows:
                for k, v in row.items():
                    if hasattr(v, 'isoformat'):
                        row[k] = v.isoformat()
                    elif not isinstance(v, (str, int, float, bool, type(None))):
                        row[k] = str(v)

            return {
                "rows": rows,
                "row_count": len(rows),
                "columns": columns,
                "duration_ms": elapsed_ms,
                "truncated": truncated,
                "error": None,
            }
        finally:
            await conn.close()
    except asyncpg.PostgresError as e:
        return {
            "rows": [],
            "row_count": 0,
            "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(e),
        }
    except Exception as e:
        return {
            "rows": [],
            "row_count": 0,
            "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(e),
        }
