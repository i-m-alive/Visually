import time
import aiomysql


async def execute_mysql(
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
        conn = await aiomysql.connect(
            host=host,
            port=port,
            db=database,
            user=user,
            password=password,
            ssl=ssl,
            connect_timeout=timeout_seconds,
            cursorclass=aiomysql.DictCursor,
        )
        try:
            async with conn.cursor() as cursor:
                await cursor.execute(f"SET SESSION max_execution_time = {timeout_seconds * 1000}")
                await cursor.execute(sql)
                records = await cursor.fetchmany(row_limit + 1)

            elapsed_ms = (time.monotonic() - start) * 1000
            truncated = len(records) > row_limit
            rows = list(records[:row_limit])
            columns = list(rows[0].keys()) if rows else []

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
            conn.close()
    except Exception as e:
        return {
            "rows": [],
            "row_count": 0,
            "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(e),
        }
