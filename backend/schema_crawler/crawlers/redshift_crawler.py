import asyncio
import json
import re
import os
import sys
from datetime import datetime, timezone
from typing import Any

import redshift_connector

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared"))
from bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL  # noqa: E402

SCHEMA_MODEL = BEDROCK_SONNET_MODEL

# Route Serverless crawls through the AWS Redshift Data API (public HTTPS, no VPC/VPN
# needed) instead of a direct TCP connect to the private workgroup endpoint. Same path
# the query executor uses. Requires valid AWS creds + redshift-data permissions.
_USE_DATA_API = os.getenv("REDSHIFT_USE_DATA_API", "").lower() in ("true", "1", "yes")


def _da_cell(cell: dict):
    if cell.get("isNull"):
        return None
    for k in ("stringValue", "longValue", "doubleValue", "booleanValue"):
        if k in cell:
            return cell[k]
    if "blobValue" in cell:
        return str(cell["blobValue"])
    return None


class _DataAPICursor:
    """Minimal cursor shim over the Redshift Data API so the existing crawl logic
    (cursor.execute / fetchall / description) runs unchanged from outside the VPC."""

    def __init__(self, client, workgroup: str, database: str, secret_arn: str):
        self._client = client
        self._wg = workgroup
        self._db = database
        self._secret = secret_arn
        self._rows: list[tuple] = []
        self.description: list | None = None

    def execute(self, sql: str):
        import time as _t
        kwargs = {"WorkgroupName": self._wg, "Database": self._db, "Sql": sql}
        if self._secret:
            kwargs["SecretArn"] = self._secret
        sid = self._client.execute_statement(**kwargs)["Id"]
        # Poll — the first query also wakes a paused Serverless workgroup (why the
        # Data API tolerates cold starts where a socket connect would time out).
        deadline = _t.monotonic() + 180
        status, d = "SUBMITTED", {}
        while _t.monotonic() < deadline:
            d = self._client.describe_statement(Id=sid)
            status = d["Status"]
            if status in ("FINISHED", "FAILED", "ABORTED"):
                break
            _t.sleep(0.6)
        if status != "FINISHED":
            raise RuntimeError(d.get("Error") or f"Data API statement {status}")
        self._rows, self.description = [], None
        if not d.get("HasResultSet"):
            return
        columns: list[str] = []
        records: list[list] = []
        token = None
        while True:
            rk = {"Id": sid}
            if token:
                rk["NextToken"] = token
            res = self._client.get_statement_result(**rk)
            if not columns:
                columns = [c.get("name", c.get("label", "")) for c in res.get("ColumnMetadata", [])]
            for rec in res.get("Records", []):
                records.append(tuple(_da_cell(c) for c in rec))
            token = res.get("NextToken")
            if not token:
                break
        self.description = [(c,) for c in columns]
        self._rows = records

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _DataAPIConn:
    def __init__(self, host: str, database: str):
        import boto3
        parts = (host or "").split(".")
        self._wg = parts[0] if parts else ""
        region = parts[2] if len(parts) >= 3 else os.getenv("AWS_REGION", "us-east-1")
        self._client = boto3.client("redshift-data", region_name=region)
        self._db = database
        self._secret = os.getenv("REDSHIFT_DATA_API_SECRET_ARN", "").strip()

    def cursor(self):
        return _DataAPICursor(self._client, self._wg, self._db, self._secret)

    def close(self):
        pass


_PII_SIGNALS = frozenset({"email", "phone", "ssn", "dob", "password", "secret", "token", "auth", "credit", "card"})

SEMANTIC_TABLE_KEYWORDS = {
    "order", "sale", "customer", "event", "transaction", "revenue",
    "metric", "product", "user", "account", "payment",
}


def _crawl_sync(
    host: str, port: int, database: str, user: str, password: str,
    ssl: bool, connection_id: str, iam_role_arn: str | None,
) -> dict:
    is_serverless = "redshift-serverless" in (host or "")

    # Serverless workgroups are private (VPC-only) — a direct TCP connect from outside
    # the VPC (e.g. Azure) times out. When the Data API is enabled, crawl through it
    # instead: it reaches the workgroup via AWS's control plane over public HTTPS.
    if is_serverless and _USE_DATA_API:
        conn = _DataAPIConn(host, database)
        cursor = conn.cursor()
        try:
            return _crawl_with_cursor(cursor, is_serverless)
        finally:
            cursor.close()
            conn.close()

    # Serverless workgroups auto-pause; allow more time for cold-start wake-up
    _timeout = 180 if is_serverless else 120

    conn_kwargs: dict[str, Any] = {
        "host": host, "port": port, "database": database,
        "ssl": ssl, "timeout": _timeout,
    }

    if is_serverless:
        conn_kwargs["is_serverless"] = True
        # Host format: <workgroup>.<account>.<region>.redshift-serverless.amazonaws.com
        _parts = (host or "").split(".")
        if len(_parts) >= 3:
            conn_kwargs["region"] = _parts[2]
        if _parts:
            conn_kwargs["serverless_work_group"] = _parts[0]

    # IAM auth when password is blank — use AWS credential env vars
    if not password:
        try:
            from dotenv import load_dotenv as _ld
            _env = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '.env')
            if os.path.exists(_env):
                _ld(_env, override=True)
        except ImportError:
            pass
        _ak = os.getenv("AWS_ACCESS_KEY_ID", "")
        _sk = os.getenv("AWS_SECRET_ACCESS_KEY", "")
        _tok = os.getenv("AWS_SESSION_TOKEN", "")
        _region = conn_kwargs.get("region", "us-east-1")

        # Pre-validate AWS credentials before handing them to redshift_connector.
        # Expired STS tokens cause redshift_connector to silently fall back to
        # password auth (empty password), which produces a confusing error.
        try:
            import boto3
            sts = boto3.client(
                "sts",
                aws_access_key_id=_ak,
                aws_secret_access_key=_sk,
                aws_session_token=_tok or None,
                region_name=_region,
            )
            sts.get_caller_identity()
        except Exception as cred_err:
            raise Exception(
                f"AWS credentials are invalid or expired: {cred_err}\n"
                "Run: aws sts get-session-token  — then update AWS_ACCESS_KEY_ID, "
                "AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN in .env and restart the backend."
            )

        conn_kwargs["iam"] = True
        conn_kwargs["aws_access_key_id"] = _ak
        conn_kwargs["aws_secret_access_key"] = _sk
        if _tok:
            conn_kwargs["aws_session_token"] = _tok
        conn_kwargs["database_user"] = user if user else "awsuser"
    else:
        conn_kwargs["user"] = user
        conn_kwargs["password"] = password

    if iam_role_arn:
        conn_kwargs["iam"] = True
        conn_kwargs["iam_role_arn"] = iam_role_arn

    conn = redshift_connector.connect(**conn_kwargs)
    cursor = conn.cursor()
    try:
        return _crawl_with_cursor(cursor, is_serverless)
    finally:
        cursor.close()
        conn.close()


def _crawl_with_cursor(cursor, is_serverless: bool) -> dict:
    """Run the schema introspection over an already-open cursor (real redshift_connector
    cursor, or the Data API shim — both expose execute/fetchall/description)."""
    try:
        # Tables and views via information_schema
        cursor.execute("""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog','information_schema','pg_toast')
            AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_type, table_schema, table_name
        """)
        tables_raw = cursor.fetchall()

        # Row counts via pg_class (approximate, works for regular users)
        row_count_map: dict[str, int] = {}
        try:
            cursor.execute("""
                SELECT n.nspname, c.relname, c.reltuples::bigint
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                AND n.nspname NOT IN ('pg_catalog','information_schema','pg_toast')
            """)
            for r in cursor.fetchall():
                row_count_map[f"{r[0]}.{r[1]}"] = max(0, int(r[2]))
        except Exception:
            pass

        # Columns via information_schema
        cursor.execute("""
            SELECT table_schema, table_name, column_name, ordinal_position,
                   data_type, character_maximum_length, numeric_precision,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog','information_schema','pg_toast')
            ORDER BY table_schema, table_name, ordinal_position
        """)
        columns_raw = cursor.fetchall()
        col_headers = ["table_schema", "table_name", "column_name", "ordinal_position",
                       "data_type", "character_maximum_length", "numeric_precision",
                       "is_nullable", "column_default"]
        col_dicts = [dict(zip(col_headers, row)) for row in columns_raw]

        # Foreign keys via information_schema
        try:
            cursor.execute("""
                SELECT
                    kcu_pk.table_schema, kcu_pk.table_name,
                    kcu_fk.table_schema, kcu_fk.table_name,
                    kcu_fk.column_name, kcu_pk.column_name
                FROM information_schema.referential_constraints rc
                JOIN information_schema.key_column_usage kcu_fk
                    ON kcu_fk.constraint_name = rc.constraint_name
                    AND kcu_fk.table_schema = rc.constraint_schema
                JOIN information_schema.key_column_usage kcu_pk
                    ON kcu_pk.constraint_name = rc.unique_constraint_name
                    AND kcu_pk.table_schema = rc.unique_constraint_schema
                WHERE kcu_fk.table_schema NOT IN ('pg_catalog','information_schema')
            """)
            fk_raw = cursor.fetchall()
        except Exception:
            fk_raw = []

        # Primary keys from pg_attribute + pg_constraint
        try:
            cursor.execute("""
                SELECT n.nspname as schema_name, c.relname as table_name, a.attname as column_name
                FROM pg_constraint con
                JOIN pg_class c ON c.oid = con.conrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(con.conkey)
                WHERE con.contype = 'p'
                  AND n.nspname NOT IN ('pg_catalog','information_schema')
            """)
            pk_raw = cursor.fetchall()
        except Exception:
            pk_raw = []

        # Build lookup structures
        pks: dict[str, set] = {}
        for row in pk_raw:
            key = f"{row[0]}.{row[1]}"
            pks.setdefault(key, set()).add(row[2])

        fks: dict[str, list] = {}
        for row in fk_raw:
            key = f"{row[2]}.{row[3]}"
            fks.setdefault(key, []).append({
                "column": row[4],
                "ref_table": row[1],
                "ref_column": row[5],
            })

        col_map: dict[str, list] = {}
        for col in col_dicts:
            key = f"{col['table_schema']}.{col['table_name']}"
            col_map.setdefault(key, []).append(col)

        table_data = {}
        all_table_names = {row[1] for row in tables_raw}

        for trow in tables_raw:
            tschema, tname, ttype = trow[0], trow[1], trow[2]
            is_view = ttype == 'VIEW'
            row_count = row_count_map.get(f"{tschema}.{tname}", 0)
            tkey = f"{tschema}.{tname}"

            col_stats: dict = {}

            inferred_rels = []
            for col in (col_map.get(tkey) or []):
                cname = col["column_name"]
                if cname.endswith("_id") and cname != "id":
                    base = cname[:-3]
                    if base + "s" in all_table_names:
                        inferred_rels.append({"column": cname, "ref_table": base + "s", "ref_column": "id", "inferred": True})
                    elif base in all_table_names:
                        inferred_rels.append({"column": cname, "ref_table": base, "ref_column": "id", "inferred": True})

            explicit = [{**fk, "inferred": False} for fk in fks.get(tkey, [])]

            # ── Sample rows (25 rows; views always use LIMIT, never TABLESAMPLE) ───
            col_names = [c["column_name"] for c in (col_map.get(tkey) or [])]
            sample_rows: list[dict] = []
            try:
                sample_sql = (
                    f'SELECT * FROM "{tschema}"."{tname}" LIMIT 25'
                    if row_count <= 100 or is_view
                    else f'SELECT * FROM "{tschema}"."{tname}" TABLESAMPLE BERNOULLI(1) LIMIT 25'
                )
                cursor.execute(sample_sql)
                col_headers = [d[0] for d in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(zip(col_headers, row))
                    # Mask PII columns
                    for cname in col_names:
                        if any(sig in cname.lower() for sig in _PII_SIGNALS) and cname in row_dict:
                            row_dict[cname] = "[REDACTED]"
                    # Stringify non-serialisable types
                    sample_rows.append({
                        k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                        for k, v in row_dict.items()
                    })
            except Exception:
                pass

            # ── Derive top_values for categorical VARCHAR columns ────────────────
            # Phase A: extract unique values seen in the 25 sample rows — zero extra
            #          queries, best-effort for large tables.
            # Phase B: run SELECT DISTINCT for small tables (≤5 000 rows) to get a
            #          complete value list cheaply (sub-second on Redshift).
            _VARCHAR_TYPES = {"character varying", "varchar", "text", "char", "bpchar"}
            _SKIP_COL_SUFFIXES = ("_id", "url", "description", "note", "notes",
                                  "text", "email", "phone", "address", "addr")
            _SKIP_COL_PREFIXES = ("description", "note", "comment", "addr",
                                  "narrative", "detail")
            _distinct_done = 0  # cap DISTINCT queries per table to limit crawl time
            for col in (col_map.get(tkey) or []):
                cname = col["column_name"]
                cname_lower = cname.lower()
                dtype = (col.get("data_type") or "").lower()
                if dtype not in _VARCHAR_TYPES:
                    continue
                if any(sig in cname_lower for sig in _PII_SIGNALS):
                    continue
                if (cname_lower == "id"
                        or cname_lower.endswith(_SKIP_COL_SUFFIXES)
                        or any(cname_lower.startswith(p) for p in _SKIP_COL_PREFIXES)):
                    continue

                # Phase A — from sample rows (always runs)
                seen_vals: dict = {}
                for srow in sample_rows:
                    v = srow.get(cname)
                    if v is not None and v != "[REDACTED]":
                        s = str(v).strip()
                        if s:
                            seen_vals[s] = True

                # Phase B — full DISTINCT for very small base tables (max 5 cols per table, skip views)
                if row_count <= 1_000 and not is_view and _distinct_done < 5:
                    try:
                        cursor.execute(
                            f'SELECT DISTINCT "{cname}" FROM "{tschema}"."{tname}" '
                            f'WHERE "{cname}" IS NOT NULL LIMIT 100'
                        )
                        for r in cursor.fetchall():
                            if r[0] is not None:
                                s = str(r[0]).strip()
                                if s:
                                    seen_vals[s] = True
                        _distinct_done += 1
                    except Exception:
                        pass

                if seen_vals:
                    col_stats[cname] = {
                        "top_values": [{cname: v} for v in list(seen_vals.keys())[:100]]
                    }

            table_data[tkey] = {
                "table_schema": tschema,
                "table_name": tname,
                "is_view": is_view,
                "row_count": row_count,
                "columns": col_map.get(tkey, []),
                "primary_keys": list(pks.get(tkey, [])),
                "foreign_keys": fks.get(tkey, []),
                "col_stats": col_stats,
                "all_relationships": explicit + inferred_rels,
                "_sample_rows": sample_rows,
            }

        return table_data
    finally:
        # Caller owns the cursor/connection lifecycle (closes both).
        pass


async def crawl_redshift(
    host: str, port: int, database: str, user: str, password: str,
    ssl: bool, connection_id: str, iam_role_arn: str | None = None,
) -> tuple[dict, dict]:
    """
    Returns (schema_doc, sample_rows_map).
    sample_rows_map — {qualified_table_name: [row_dict, ...]} — NOT stored in schema_doc.
    """
    start = datetime.now(timezone.utc)

    loop = asyncio.get_event_loop()
    table_data = await loop.run_in_executor(
        None, _crawl_sync, host, port, database, user, password, ssl, connection_id, iam_role_arn
    )

    # Extract sample rows before building the clean schema_doc
    sample_rows_map: dict[str, list] = {
        tkey: tdata.pop("_sample_rows", [])
        for tkey, tdata in table_data.items()
    }

    descriptions = await _generate_descriptions(table_data)
    ranked = _rank_tables(table_data)

    tables_out = []
    for rank_idx, (tkey, tdata) in enumerate(ranked, start=1):
        desc = descriptions.get(tdata["table_name"], {})
        columns_out = []
        for col in tdata["columns"]:
            cname = col["column_name"]
            col_out = {
                "name": cname,
                "type": col["data_type"],
                "is_nullable": col.get("is_nullable", "YES") == "YES",
                "is_primary_key": cname in tdata["primary_keys"],
                "description": desc.get("columns", {}).get(cname, f"Column {cname}"),
                "stats": tdata["col_stats"].get(cname),
            }
            columns_out.append(col_out)

        rels_out = [
            {
                "column": rel["column"],
                "references": f"{rel['ref_table']}.{rel['ref_column']}",
                "cardinality": "many-to-one",
                "inferred": rel.get("inferred", False),
            }
            for rel in tdata.get("all_relationships", [])
        ]

        tables_out.append({
            "name": tdata["table_name"],
            "schema": tdata["table_schema"],
            "is_view": tdata.get("is_view", False),
            "row_count": tdata["row_count"],
            "importance_rank": rank_idx,
            "description": desc.get("description", f"{'View' if tdata.get('is_view') else 'Table'} {tdata['table_name']}"),
            "columns": columns_out,
            "relationships": rels_out,
        })

    crawl_duration = (datetime.now(timezone.utc) - start).total_seconds()

    schema_doc = {
        "connection_id": connection_id,
        "crawled_at": start.isoformat(),
        "tables": tables_out,
        "important_tables": [t["name"] for t in tables_out[:5]],
        "total_tables": len(tables_out),
        "version": 1,
        "crawl_duration_seconds": crawl_duration,
        "dialect": "redshift",
    }
    return schema_doc, sample_rows_map


async def _describe_batch(batch: list, batch_idx: int) -> dict:
    schema_summary = {
        tdata["table_name"]: {
            "columns": [
                {"name": c["column_name"], "type": c["data_type"]}
                for c in tdata["columns"][:15]
            ],
            "row_count": tdata["row_count"],
        }
        for _tkey, tdata in batch
    }
    prompt = json.dumps(schema_summary, default=str)
    for attempt in range(2):
        try:
            text = await asyncio.wait_for(
                bedrock_invoke(
                    model_id=SCHEMA_MODEL,
                    system_prompt=(
                        "You are a database analyst. Generate semantic descriptions for the given Redshift schema.\n"
                        "For each table: 1-2 sentences about the business entity it represents.\n"
                        "For each column: a short phrase (5-12 words) describing what it measures or identifies.\n"
                        "Return ONLY valid JSON in this exact shape: "
                        '{\"table_name\": {\"description\": \"...\", \"columns\": {\"col_name\": \"...\"}}}. '
                        "No prose, no markdown, no explanation."
                    ),
                    user_message=f"Generate descriptions:\n{prompt}",
                    max_tokens=4096,
                    temperature=0.1,
                ),
                timeout=90.0,
            )
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"```$", "", text).strip()
            parsed = json.loads(text)
            return parsed.get("tables", parsed)
        except Exception as exc:
            print(f"[schema_crawler] Redshift descriptions batch {batch_idx} attempt {attempt+1} failed: {exc}")
    return {}


async def _generate_descriptions(table_data: dict) -> dict:
    _BATCH = 10
    items = list(table_data.items())
    batches = [items[i: i + _BATCH] for i in range(0, len(items), _BATCH)]
    batch_results = await asyncio.gather(*[_describe_batch(b, idx) for idx, b in enumerate(batches)])
    results: dict = {}
    for r in batch_results:
        results.update(r)
    return results


def _rank_tables(table_data: dict) -> list:
    counts = [t["row_count"] for t in table_data.values()]
    max_count = max(counts) if counts else 1
    numeric_types = {"integer", "numeric", "real", "double precision", "bigint", "smallint", "decimal", "float"}
    scored = []
    for tkey, tdata in table_data.items():
        tname = tdata["table_name"].lower()
        row_count = tdata["row_count"]
        cols = tdata["columns"]
        row_score = row_count / max_count if max_count > 0 else 0
        non_id_numeric = sum(
            1 for c in cols
            if c["data_type"].lower() in numeric_types
            and not c["column_name"].endswith("_id") and c["column_name"] != "id"
        )
        richness = non_id_numeric / len(cols) if cols else 0
        centrality_count = sum(
            1 for other in table_data.values()
            for rel in other.get("all_relationships", [])
            if rel.get("ref_table") == tdata["table_name"]
        )
        centrality = min(centrality_count / 5, 1.0)
        name_bonus = 0.3 if any(kw in tname for kw in SEMANTIC_TABLE_KEYWORDS) else 0.0
        score = row_score * 0.4 + richness * 0.2 + centrality * 0.1 + name_bonus
        scored.append((tkey, tdata, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return [(tkey, tdata) for tkey, tdata, _ in scored]
