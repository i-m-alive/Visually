import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.models.database_connections import DatabaseConnection
from shared.encryption import decrypt
from .postgres import execute_postgres
from .mysql import execute_mysql
from .redshift import execute_redshift


async def route_and_execute(
    connection_id: str,
    sql: str,
    timeout_seconds: int,
    row_limit: int,
    db: AsyncSession,
) -> dict:
    try:
        result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id))
        )
        conn = result.scalar_one_or_none()
    except Exception as e:
        print(f"[router] ✗ platform DB lookup failed for {connection_id}: {e}", flush=True)
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": 0, "truncated": False,
            "error": f"Platform DB error looking up connection: {e}",
        }

    if not conn:
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": 0, "truncated": False,
            "error": f"Connection {connection_id} not found",
        }

    _db_type_early = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
    if _db_type_early == "vly_offline":
        # Offline (imported, no live DB): execute against the bundled DuckDB snapshot.
        from shared.offline_store import execute_offline_sql
        dashboard_id = (conn.connection_options or {}).get("dashboard_id") if isinstance(conn.connection_options, dict) else None
        if not dashboard_id:
            return {"rows": [], "row_count": 0, "columns": [], "duration_ms": 0,
                    "truncated": False, "error": "Offline connection missing dashboard_id"}
        return await execute_offline_sql(db, str(dashboard_id), sql, row_limit)

    password = ""
    if conn.encrypted_password:
        try:
            password = decrypt(conn.encrypted_password)
        except Exception:
            return {
                "rows": [], "row_count": 0, "columns": [],
                "duration_ms": 0, "truncated": False,
                "error": "Failed to decrypt connection password",
            }

    db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)

    try:
        if db_type == "redshift":
            iam_role_arn = None
            if conn.connection_options and isinstance(conn.connection_options, dict):
                iam_role_arn = conn.connection_options.get("iam_role_arn")
            return await execute_redshift(
                host=conn.host or "localhost",
                port=conn.port or 5439,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                ssl=conn.ssl_enabled,
                sql=sql,
                iam_role_arn=iam_role_arn,
                row_limit=row_limit,
            )
        elif db_type == "postgresql":
            return await execute_postgres(
                host=conn.host or "localhost",
                port=conn.port or 5432,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                sql=sql,
                timeout_seconds=timeout_seconds,
                row_limit=row_limit,
                ssl=conn.ssl_enabled,
            )
        elif db_type == "mysql":
            return await execute_mysql(
                host=conn.host or "localhost",
                port=conn.port or 3306,
                database=conn.database_name or "",
                user=conn.username or "",
                password=password,
                sql=sql,
                timeout_seconds=timeout_seconds,
                row_limit=row_limit,
                ssl=conn.ssl_enabled,
            )
        else:
            return {
                "rows": [], "row_count": 0, "columns": [],
                "duration_ms": 0, "truncated": False,
                "error": f"Unsupported db_type: {db_type}",
            }
    except Exception as e:
        print(f"[router] ✗ driver raised unhandled exception ({db_type}): {e}", flush=True)
        return {
            "rows": [], "row_count": 0, "columns": [],
            "duration_ms": 0, "truncated": False,
            "error": f"Driver error ({db_type}): {e}",
        }
