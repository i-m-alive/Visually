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
    # surfacing "connection time out" to the user. This is the Redshift driver, so
    # ALWAYS retry — when reached via a tunnel (e.g. ngrok for a demo) the host won't
    # contain "redshift-serverless", so we can't rely on is_serverless to decide.
    _max_attempts = 3
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


# ── Redshift Data API path (no VPC network access needed) ────────────────────
# Set REDSHIFT_USE_DATA_API=true to route Serverless queries through the AWS
# Redshift Data API instead of a direct TCP connection. The Data API reaches the
# workgroup through AWS's control plane using IAM credentials, so it works from
# anywhere (e.g. Azure) WITHOUT being on the cluster's VPN/VPC. Requires valid
# AWS creds (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY[/ AWS_SESSION_TOKEN]) in
# the environment and redshift-data + redshift-serverless:GetCredentials perms.
_USE_DATA_API = os.getenv("REDSHIFT_USE_DATA_API", "").lower() in ("true", "1", "yes")


def _cell_value(cell: dict):
    if cell.get("isNull"):
        return None
    for k in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if k in cell:
            return cell[k]
    if "blobValue" in cell:
        return str(cell["blobValue"])
    return None


def _execute_data_api_sync(host: str, database: str, sql: str, row_limit: int) -> dict:
    import boto3
    start = time.monotonic()
    parts = (host or "").split(".")
    workgroup = parts[0] if parts else ""
    region = parts[2] if len(parts) >= 3 else os.getenv("AWS_REGION", "us-east-1")
    try:
        client = boto3.client("redshift-data", region_name=region)
        resp = client.execute_statement(WorkgroupName=workgroup, Database=database, Sql=sql)
        sid = resp["Id"]
        # Poll until the statement finishes. First query against a paused Serverless
        # workgroup also wakes it, so allow generous time (handled by polling, not a
        # socket timeout — this is why the Data API is robust to cold starts).
        deadline = start + 120
        status = "SUBMITTED"
        while time.monotonic() < deadline:
            d = client.describe_statement(Id=sid)
            status = d["Status"]
            if status in ("FINISHED", "FAILED", "ABORTED"):
                break
            time.sleep(0.6)
        if status != "FINISHED":
            err = (d.get("Error") if status != "SUBMITTED" else None) or f"statement {status}"
            print(f"[redshift-data] ✗ {status}: {err}", flush=True)
            return {"rows": [], "row_count": 0, "columns": [],
                    "duration_ms": (time.monotonic() - start) * 1000, "truncated": False, "error": err}

        if not d.get("HasResultSet"):
            return {"rows": [], "row_count": 0, "columns": [],
                    "duration_ms": (time.monotonic() - start) * 1000, "truncated": False, "error": None}

        columns: list[str] = []
        records: list[list] = []
        token = None
        while True:
            kwargs = {"Id": sid}
            if token:
                kwargs["NextToken"] = token
            res = client.get_statement_result(**kwargs)
            if not columns:
                columns = [c.get("name", c.get("label", "")) for c in res.get("ColumnMetadata", [])]
            for rec in res.get("Records", []):
                records.append([_cell_value(c) for c in rec])
            token = res.get("NextToken")
            if not token or len(records) >= row_limit:
                break

        truncated = len(records) > row_limit or bool(token)
        rows = [dict(zip(columns, r)) for r in records[:row_limit]]
        print(f"[redshift-data] ✓ {len(rows)} rows via Data API (wg={workgroup})", flush=True)
        return {"rows": rows, "row_count": len(rows), "columns": columns,
                "duration_ms": (time.monotonic() - start) * 1000, "truncated": truncated, "error": None}
    except Exception as exc:
        print(f"[redshift-data] ✗ Data API failed: {exc}", flush=True)
        return {"rows": [], "row_count": 0, "columns": [],
                "duration_ms": (time.monotonic() - start) * 1000, "truncated": False, "error": str(exc)}


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
    # Prefer the Data API for Serverless when enabled — it needs no VPC/VPN access.
    if _USE_DATA_API and "redshift-serverless" in (host or ""):
        return await loop.run_in_executor(
            None, _execute_data_api_sync, host, database, sql, row_limit,
        )
    return await loop.run_in_executor(
        None,
        _execute_sync,
        host, port, database, user, password, ssl, sql, iam_role_arn, row_limit,
    )
