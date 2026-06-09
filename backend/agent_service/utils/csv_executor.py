"""
DuckDB in-process query executor for CSV mode.

Loads all CSVs in a session directory into DuckDB in-memory tables, executes SQL,
and returns {rows, columns, row_count} in the exact same format as
call_query_executor() — so the entire pipeline (value sampler, query agent,
validator) is unaware it is talking to CSV data instead of a live database.

Called transparently from call_query_executor() when connection_id starts with
"csv_session:" — no changes needed anywhere else in the pipeline.
"""
import asyncio
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Dedicated thread pool — DuckDB is synchronous; we run it off the event loop
_DUCK_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="duckdb")


def _run_duckdb_sync(session_dir: str, sql: str, row_limit: int) -> dict:
    """
    Synchronous DuckDB execution.  Runs in a thread-pool worker so the async
    event loop is never blocked.

    Each CSV in session_dir becomes a table named after the file stem.
    Hyphens and spaces are replaced with underscores so the names are valid
    SQL identifiers and match what csv_ingestor.py reported in the schema_doc.
    """
    import duckdb

    conn = duckdb.connect(database=":memory:")
    loaded: list[str] = []

    for csv_path in sorted(Path(session_dir).glob("*.csv")):
        raw_name = csv_path.stem
        safe_name = raw_name.replace("-", "_").replace(" ", "_")
        try:
            conn.execute(
                f'CREATE TABLE "{safe_name}" AS '
                f"SELECT * FROM read_csv_auto('{csv_path}', header=true)"
            )
            loaded.append(safe_name)
            print(
                f"[csv_executor] loaded '{safe_name}' ← {csv_path.name}",
                flush=True,
            )
        except Exception as e:
            print(
                f"[csv_executor] ⚠ failed to load {csv_path.name}: {e}",
                flush=True,
            )

    if not loaded:
        return {
            "rows": [], "columns": [], "row_count": 0,
            "error": "No CSV files could be loaded into DuckDB",
        }

    # Try wrapping in a subquery so LIMIT is always respected cleanly.
    # Some queries (already containing ORDER BY ... LIMIT) cannot be wrapped;
    # we fall back to running them directly in that case.
    limited_sql = f"SELECT * FROM ({sql}) __q LIMIT {row_limit}"
    try:
        result_df = conn.execute(limited_sql).df()
    except Exception:
        try:
            result_df = conn.execute(sql).df()
        except Exception as e2:
            return {
                "rows": [], "columns": [], "row_count": 0,
                "error": f"DuckDB execution error: {e2}",
            }

    # Convert pandas Timestamps / non-JSON-serialisable types to strings
    rows = result_df.to_dict(orient="records")
    for row in rows:
        for k, v in row.items():
            if hasattr(v, "isoformat"):          # datetime / date / Timestamp
                row[k] = v.isoformat()
            elif hasattr(v, "item"):              # numpy scalar → Python native
                row[k] = v.item()

    return {
        "rows": rows,
        "columns": list(result_df.columns),
        "row_count": len(rows),
    }


async def call_csv_executor(
    session_dir: str,
    sql: str,
    row_limit: int = 10000,
) -> dict:
    """
    Async wrapper — delegates to _run_duckdb_sync in the thread pool.
    Returns same dict shape as call_query_executor().
    """
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            _DUCK_EXECUTOR, _run_duckdb_sync, session_dir, sql, row_limit
        )
    except Exception as e:
        return {
            "rows": [], "columns": [], "row_count": 0,
            "error": f"csv_executor unexpected error: {e}",
        }
