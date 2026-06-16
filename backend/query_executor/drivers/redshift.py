import asyncio
import os
import time
from typing import Any

import redshift_connector


def _execute_sync(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl: bool,
    sql: str,
    iam_role_arn: str | None,
    row_limit: int = 10000,
) -> dict:
    start = time.monotonic()

    is_serverless = "redshift-serverless" in (host or "")
    # Serverless workgroups auto-pause. We RETRY the connect (see below), so use a
    # shorter per-attempt timeout: the first attempt kicks off the wake, and a later
    # attempt lands on the now-awake cluster. Shorter per-attempt timeouts mean the
    # retries fit inside the caller's overall request budget instead of one long
    # attempt eating it all.
    _timeout = 60 if is_serverless else 60

    conn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "database": database,
        "ssl": ssl,
        "timeout": _timeout,
    }
    if is_serverless:
        conn_kwargs["is_serverless"] = True
        # Host format: <workgroup>.<account>.<region>.redshift-serverless.amazonaws.com
        _parts = (host or "").split(".")
        if len(_parts) >= 3:
            conn_kwargs["region"] = _parts[2]
        if _parts:
            conn_kwargs["serverless_work_group"] = _parts[0]

    # IAM auth: when password is blank, use AWS credential env vars instead of user/password
    if not password:
        try:
            from dotenv import load_dotenv as _ld
            _env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '.env')
            if os.path.exists(_env):
                _ld(_env, override=True)
        except ImportError:
            pass
        conn_kwargs["iam"] = True
        conn_kwargs["aws_access_key_id"] = os.getenv("AWS_ACCESS_KEY_ID", "")
        conn_kwargs["aws_secret_access_key"] = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        aws_session_token = os.getenv("AWS_SESSION_TOKEN", "")
        if aws_session_token:
            conn_kwargs["aws_session_token"] = aws_session_token
        # database_user is required for IAM — use "awsuser" default for Redshift Serverless
        conn_kwargs["database_user"] = user if user else "awsuser"
    else:
        conn_kwargs["user"] = user
        conn_kwargs["password"] = password

    if iam_role_arn:
        conn_kwargs["iam"] = True
        conn_kwargs["iam_role_arn"] = iam_role_arn

    # Serverless workgroups pause when idle; the FIRST connect to a cold cluster
    # triggers a wake that can take ~60–120s and may itself time out or get reset
    # mid-wake. Retry so the follow-up attempt lands on an awake cluster instead of
    # surfacing "connection time out" to the user. Provisioned clusters don't pause,
    # so a single attempt is enough there.
    _max_attempts = 3 if is_serverless else 1
    conn = None
    last_exc: Exception | None = None
    for _attempt in range(1, _max_attempts + 1):
        try:
            conn = redshift_connector.connect(**conn_kwargs)
            if _attempt > 1:
                print(f"[redshift] ✓ connected on attempt {_attempt} (cluster was waking)", flush=True)
            break
        except Exception as exc:
            last_exc = exc
            print(f"[redshift] ✗ connect attempt {_attempt}/{_max_attempts} failed: {exc}", flush=True)
            if _attempt < _max_attempts:
                time.sleep(3)  # brief pause; the prior attempt already kicked off the wake
    if conn is None:
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(last_exc) if last_exc else "connect failed",
        }

    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        all_rows = cursor.fetchall()
        truncated = len(all_rows) >= row_limit
        rows_as_dicts = [dict(zip(columns, row)) for row in all_rows[:row_limit]]

        # Serialize non-JSON-safe types (Decimal, date, UUID, etc.)
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
        print(f"[redshift] ✗ query failed: {exc}", flush=True)
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": (time.monotonic() - start) * 1000,
            "truncated": False,
            "error": str(exc),
        }
    finally:
        conn.close()


async def execute_redshift(
    host: str,
    port: int,
    database: str,
    user: str,
    password: str,
    ssl: bool,
    sql: str,
    iam_role_arn: str | None = None,
    row_limit: int = 10000,
) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _execute_sync,
        host, port, database, user, password, ssl, sql, iam_role_arn, row_limit,
    )
