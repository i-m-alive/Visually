import asyncio
from datetime import datetime, timezone
from typing import Optional

_PII_SIGNALS = frozenset({"email", "phone", "ssn", "dob", "password", "secret", "token", "auth", "credit", "card"})
_SYSTEM_DATABASES = frozenset({"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"})

SEMANTIC_TABLE_KEYWORDS = {
    "order", "sale", "customer", "event", "transaction", "revenue",
    "metric", "product", "user", "account", "payment",
}


def _normalize_account(account: str) -> str:
    """Strip trailing .snowflakecomputing.com — the connector appends it automatically."""
    suffix = ".snowflakecomputing.com"
    return account[: -len(suffix)] if account.lower().endswith(suffix) else account


def _connect_snowflake(account: str, user: str, password: str, database: Optional[str],
                       warehouse: Optional[str], role: Optional[str]):
    import snowflake.connector
    normalized = _normalize_account(account)
    if normalized != account:
        print(
            f"[snowflake_crawler] account normalized: {account!r} → {normalized!r}",
            flush=True,
        )
    print(
        f"[snowflake_crawler] connecting  account={normalized!r}  user={user!r}"
        f"  database={database!r}  warehouse={warehouse!r}  role={role!r}",
        flush=True,
    )
    kwargs = {
        "account": normalized,
        "user": user,
        "password": password,
        "login_timeout": 30,
        "network_timeout": 60,
    }
    if database:
        kwargs["database"] = database
    if warehouse:
        kwargs["warehouse"] = warehouse
    if role:
        kwargs["role"] = role
    conn = snowflake.connector.connect(**kwargs)
    print("[snowflake_crawler] connection established", flush=True)
    return conn


def _get_databases(cursor) -> list[str]:
    """Return user-owned databases, skipping Snowflake system databases."""
    cursor.execute("SHOW DATABASES")
    rows = cursor.fetchall()
    # Use cursor.description to look up column positions by name — positional
    # indexing is fragile across Snowflake versions that add/reorder columns.
    col_names = [d[0].lower() for d in cursor.description]
    try:
        name_idx = col_names.index("name")
        origin_idx = col_names.index("origin")
        print(
            f"[snowflake_crawler] SHOW DATABASES columns={col_names}"
            f"  name_idx={name_idx}  origin_idx={origin_idx}",
            flush=True,
        )
    except ValueError:
        name_idx, origin_idx = 1, 4
        print(
            f"[snowflake_crawler] SHOW DATABASES: 'name'/'origin' not in description"
            f" {col_names} — falling back to positional idx name={name_idx} origin={origin_idx}",
            flush=True,
        )
    all_dbs = [r[name_idx] for r in rows]
    system_skipped = [r[name_idx] for r in rows if r[name_idx].upper() in _SYSTEM_DATABASES]
    shared_skipped = [r[name_idx] for r in rows if r[origin_idx]]
    result = [
        r[name_idx] for r in rows
        if r[name_idx].upper() not in _SYSTEM_DATABASES and not r[origin_idx]
    ]
    print(
        f"[snowflake_crawler] SHOW DATABASES: total={len(all_dbs)}"
        f"  system_skipped={system_skipped}  shared_skipped={shared_skipped}"
        f"  will_crawl={result}",
        flush=True,
    )
    return result


def _crawl_one_database(cursor, db_name: str, schema_filter: Optional[str]) -> dict:
    """
    Crawl tables/columns/PKs/FKs for a single Snowflake database.
    Returns {tables_raw, columns_raw, pk_raw, fk_raw, row_counts, sample_rows_map}.
    """
    cursor.execute(f'USE DATABASE "{db_name}"')
    schema_clause = f"AND TABLE_SCHEMA = '{schema_filter.upper()}'" if schema_filter else ""
    print(
        f"[snowflake_crawler] [{db_name}] crawling"
        + (f"  schema_filter={schema_filter.upper()!r}" if schema_filter else "  schema_filter=<all>"),
        flush=True,
    )

    cursor.execute(f"""
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA')
        AND TABLE_SCHEMA NOT LIKE 'PG_%'
        {schema_clause}
        AND TABLE_TYPE IN ('BASE TABLE', 'VIEW')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    tables_raw = cursor.fetchall()
    print(
        f"[snowflake_crawler] [{db_name}] tables={len(tables_raw)}"
        f"  schemas={sorted({r[0] for r in tables_raw})}",
        flush=True,
    )

    cursor.execute(f"""
        SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION,
               DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION,
               IS_NULLABLE, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('INFORMATION_SCHEMA')
        AND TABLE_SCHEMA NOT LIKE 'PG_%'
        {schema_clause}
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """)
    columns_raw = cursor.fetchall()
    col_col_names = [d[0] for d in cursor.description]
    print(f"[snowflake_crawler] [{db_name}] columns={len(columns_raw)}", flush=True)

    try:
        cursor.execute(f"""
            SELECT kc.TABLE_SCHEMA, kc.TABLE_NAME, kc.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kc
              ON tc.CONSTRAINT_NAME = kc.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA = kc.TABLE_SCHEMA
            WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
            {schema_clause.replace('AND TABLE_SCHEMA', 'AND tc.TABLE_SCHEMA')}
        """)
        pk_raw = cursor.fetchall()
        print(f"[snowflake_crawler] [{db_name}] primary_key_cols={len(pk_raw)}", flush=True)
    except Exception as exc:
        pk_raw = []
        print(
            f"[snowflake_crawler] [{db_name}] PK query failed (permission?): {exc}"
            f" — continuing without PK info",
            flush=True,
        )

    try:
        # CONSTRAINT_COLUMN_USAGE holds the REFERENCED (PK) columns, not the FK columns.
        # The link between an FK constraint and the PK it references is REFERENTIAL_CONSTRAINTS.
        # Joining ccu directly on FK constraint name (old code) returns wrong/empty results.
        cursor.execute(f"""
            SELECT kcu.TABLE_SCHEMA, kcu.TABLE_NAME, kcu.COLUMN_NAME,
                   ccu.TABLE_NAME  AS FOREIGN_TABLE_NAME,
                   ccu.COLUMN_NAME AS FOREIGN_COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
              ON  tc.CONSTRAINT_NAME   = kcu.CONSTRAINT_NAME
             AND tc.TABLE_SCHEMA       = kcu.TABLE_SCHEMA
            JOIN INFORMATION_SCHEMA.REFERENTIAL_CONSTRAINTS rc
              ON  rc.CONSTRAINT_NAME   = tc.CONSTRAINT_NAME
             AND rc.CONSTRAINT_SCHEMA  = tc.TABLE_SCHEMA
            JOIN INFORMATION_SCHEMA.CONSTRAINT_COLUMN_USAGE ccu
              ON  ccu.CONSTRAINT_NAME  = rc.UNIQUE_CONSTRAINT_NAME
            WHERE tc.CONSTRAINT_TYPE = 'FOREIGN KEY'
            {schema_clause.replace('AND TABLE_SCHEMA', 'AND tc.TABLE_SCHEMA')}
        """)
        fk_raw = cursor.fetchall()
        print(
            f"[snowflake_crawler] [{db_name}] foreign_keys={len(fk_raw)}"
            + (
                "  " + ", ".join(f"{r[1]}.{r[2]}→{r[3]}.{r[4]}" for r in fk_raw[:5])
                if fk_raw else "  (none — Snowflake FKs are rarely enforced)"
            ),
            flush=True,
        )
    except Exception as exc:
        print(f"[snowflake_crawler] [{db_name}] FK query failed: {exc}", flush=True)
        fk_raw = []

    # Row counts and sample rows per table
    row_counts: dict[str, int] = {}
    sample_rows_map: dict[str, list] = {}
    count_errors, sample_errors = 0, 0

    for tschema, tname, _ in tables_raw:
        tkey = f"{tschema}.{tname}"
        full_name = f'"{db_name}"."{tschema}"."{tname}"'

        try:
            cursor.execute(f'SELECT COUNT(*) FROM {full_name}')
            row = cursor.fetchone()
            row_counts[tkey] = int(row[0]) if row else 0
        except Exception as exc:
            row_counts[tkey] = 0
            count_errors += 1
            print(f"[snowflake_crawler] [{db_name}] COUNT(*) failed for {tkey}: {exc}", flush=True)

        try:
            cursor.execute(f'SELECT * FROM {full_name} LIMIT 25')
            rows = cursor.fetchall()
            col_names = [d[0] for d in cursor.description]
            pii_cols = {c for c in col_names if any(sig in c.lower() for sig in _PII_SIGNALS)}
            if pii_cols:
                print(f"[snowflake_crawler] [{db_name}] {tkey}: redacting PII cols {pii_cols}", flush=True)
            dicts = [dict(zip(col_names, r)) for r in rows]
            for row in dicts:
                for pii_col in pii_cols:
                    if pii_col in row:
                        row[pii_col] = "[REDACTED]"
            sample_rows_map[tkey] = [
                {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                 for k, v in row.items()}
                for row in dicts
            ]
        except Exception as exc:
            sample_rows_map[tkey] = []
            sample_errors += 1
            print(f"[snowflake_crawler] [{db_name}] sample rows failed for {tkey}: {exc}", flush=True)

    print(
        f"[snowflake_crawler] [{db_name}] done"
        f"  row_counts_ok={len(tables_raw) - count_errors}/{len(tables_raw)}"
        f"  samples_ok={len(tables_raw) - sample_errors}/{len(tables_raw)}",
        flush=True,
    )
    return {
        "tables_raw": tables_raw,
        "columns_raw": [dict(zip(col_col_names, r)) for r in columns_raw],
        "pk_raw": pk_raw,
        "fk_raw": fk_raw,
        "row_counts": row_counts,
        "sample_rows_map": sample_rows_map,
    }


def _fetch_all_sync(account: str, user: str, password: str, database: Optional[str],
                    warehouse: Optional[str], role: Optional[str], schema_filter: Optional[str]) -> dict:
    """
    Synchronously crawl Snowflake metadata. Runs in an executor thread.
    If database is specified, crawls only that database.
    If not, auto-discovers all non-system databases and crawls each.
    """
    conn = _connect_snowflake(account, user, password, database, warehouse, role)
    cursor = conn.cursor()
    try:
        if database:
            databases_to_crawl = [database]
            print(f"[snowflake_crawler] crawling specified database: {database!r}", flush=True)
        else:
            databases_to_crawl = _get_databases(cursor)
            if not databases_to_crawl:
                print("[snowflake_crawler] no user databases found — check account permissions", flush=True)
                return {"tables_raw": [], "columns_raw": [], "pk_raw": [], "fk_raw": [],
                        "row_counts": {}, "sample_rows_map": {}}

        # Merge results from all databases
        all_tables: list = []
        all_columns: list = []
        all_pks: list = []
        all_fks: list = []
        all_row_counts: dict = {}
        all_sample_rows: dict = {}

        for db in databases_to_crawl:
            try:
                print(f"[snowflake_crawler] crawling database: {db}", flush=True)
                result = _crawl_one_database(cursor, db, schema_filter)
                all_tables.extend(result["tables_raw"])
                all_columns.extend(result["columns_raw"])
                all_pks.extend(result["pk_raw"])
                all_fks.extend(result["fk_raw"])
                all_row_counts.update(result["row_counts"])
                all_sample_rows.update(result["sample_rows_map"])
            except Exception as e:
                print(f"[snowflake_crawler] skipping database {db}: {e}", flush=True)

        return {
            "tables_raw": all_tables,
            "columns_raw": all_columns,
            "pk_raw": all_pks,
            "fk_raw": all_fks,
            "row_counts": all_row_counts,
            "sample_rows_map": all_sample_rows,
        }
    finally:
        cursor.close()
        conn.close()


async def crawl_snowflake(
    account: str,
    database: Optional[str],
    user: str,
    password: str,
    connection_id: str,
    warehouse: Optional[str] = None,
    role: Optional[str] = None,
    schema_filter: Optional[str] = None,
    ssl: bool = True,
) -> tuple[dict, dict]:
    """
    Crawl Snowflake schema. Returns (schema_doc, sample_rows_map).
    When database is None/empty, auto-discovers all user databases.
    """
    print(
        f"[snowflake_crawler] crawl_snowflake START"
        f"  account={account!r}  database={database!r}"
        f"  warehouse={warehouse!r}  role={role!r}  schema_filter={schema_filter!r}",
        flush=True,
    )
    start = datetime.now(timezone.utc)
    loop = asyncio.get_running_loop()

    raw = await loop.run_in_executor(
        None,
        _fetch_all_sync,
        account, user, password, database or None, warehouse, role, schema_filter,
    )

    tables_raw = raw["tables_raw"]
    columns_raw = raw["columns_raw"]
    pk_raw = raw["pk_raw"]
    fk_raw = raw["fk_raw"]
    row_counts = raw["row_counts"]
    sample_rows_map = raw["sample_rows_map"]

    if not tables_raw:
        print("[snowflake_crawler] no tables found — check database access permissions", flush=True)

    # Build lookup structures
    pks: dict[str, set] = {}
    for row in pk_raw:
        tschema, tname, cname = row[0], row[1], row[2]
        key = f"{tschema}.{tname}"
        pks.setdefault(key, set()).add(cname)

    fks: dict[str, list] = {}
    for row in fk_raw:
        tschema, tname, cname = row[0], row[1], row[2]
        ref_table, ref_col = row[3], row[4]
        key = f"{tschema}.{tname}"
        fks.setdefault(key, []).append({
            "column": cname,
            "ref_table": ref_table,
            "ref_column": ref_col,
        })

    col_map: dict[str, list] = {}
    for col in columns_raw:
        key = f"{col['TABLE_SCHEMA']}.{col['TABLE_NAME']}"
        col_map.setdefault(key, []).append(col)

    table_data: dict[str, dict] = {}
    for tschema, tname, ttype in tables_raw:
        tkey = f"{tschema}.{tname}"
        table_data[tkey] = {
            "table_schema": tschema,
            "table_name": tname,
            "row_count": row_counts.get(tkey, 0),
            "columns": col_map.get(tkey, []),
            "primary_keys": list(pks.get(tkey, [])),
            "foreign_keys": fks.get(tkey, []),
        }

    # Infer FK relationships from _id column naming
    all_table_names_lower = {t[1].lower() for t in tables_raw}
    for tkey, tdata in table_data.items():
        inferred_rels = []
        for col in tdata["columns"]:
            cname = col.get("COLUMN_NAME", "")
            if cname.lower().endswith("_id") and cname.lower() != "id":
                base = cname[:-3].lower()
                if base + "s" in all_table_names_lower:
                    inferred_rels.append({"column": cname, "ref_table": base + "s", "ref_column": "ID", "inferred": True})
                elif base in all_table_names_lower:
                    inferred_rels.append({"column": cname, "ref_table": base, "ref_column": "ID", "inferred": True})
        explicit = [{**fk, "inferred": False} for fk in tdata.get("foreign_keys", [])]
        tdata["all_relationships"] = explicit + inferred_rels

    descriptions = _heuristic_descriptions(table_data)
    ranked_tables = _rank_tables(table_data)

    tables_out = []
    for rank_idx, (tkey, tdata) in enumerate(ranked_tables, start=1):
        desc = descriptions.get(tdata["table_name"], {})
        columns_out = []
        for col in tdata["columns"]:
            cname = col.get("COLUMN_NAME", "")
            col_out = {
                "name": cname,
                "type": col.get("DATA_TYPE", ""),
                "is_nullable": col.get("IS_NULLABLE", "YES") == "YES",
                "is_primary_key": cname in tdata["primary_keys"],
                "description": desc.get("columns", {}).get(cname, f"Column {cname}"),
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
            "row_count": tdata["row_count"],
            "importance_rank": rank_idx,
            "description": desc.get("description", f"Table {tdata['table_name']}"),
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
    }
    explicit_rels = sum(len(t["relationships"]) for t in tables_out)
    print(
        f"[snowflake_crawler] crawl_snowflake DONE"
        f"  tables={len(tables_out)}"
        f"  relationships={explicit_rels}"
        f"  sample_tables={len(sample_rows_map)}"
        f"  duration={crawl_duration:.1f}s",
        flush=True,
    )
    return schema_doc, sample_rows_map


def _heuristic_descriptions(table_data: dict) -> dict:
    """
    Zero-cost placeholder descriptions — no LLM call.

    This pass used to make its own Bedrock call (same model as
    metadata_extractor.py's Phase A), but its output was always fully
    superseded moments later in the same crawl once schema_cache.py loads
    metadata_extractor's richer, sample-row-informed descriptions — that LLM
    spend was pure waste (see the metadata-pipeline gap analysis, Phase 3).
    This heuristic exists only to keep compact_tables/TF-IDF text non-empty
    during the brief async window before that richer pass finishes.
    """
    results: dict = {}
    for _tkey, tdata in table_data.items():
        tname = tdata["table_name"]
        col_names = [col.get("COLUMN_NAME", "") for col in tdata["columns"][:8]]
        col_names = [c for c in col_names if c]
        results[tname] = {
            "description": (
                f"Table with columns: {', '.join(col_names)}" if col_names else f"Table {tname}"
            ),
            "columns": {
                col.get("COLUMN_NAME", ""): col.get("COLUMN_NAME", "").replace("_", " ")
                for col in tdata["columns"] if col.get("COLUMN_NAME")
            },
        }
    return results


def _rank_tables(table_data: dict) -> list:
    counts = [t["row_count"] for t in table_data.values()]
    max_count = max(counts) if counts else 1

    scored = []
    for tkey, tdata in table_data.items():
        tname = tdata["table_name"].lower()
        row_count = tdata["row_count"]
        cols = tdata["columns"]

        row_score = row_count / max_count if max_count > 0 else 0

        numeric_types = {"number", "float", "integer", "int", "decimal", "numeric", "double", "bigint"}
        non_id_numeric = sum(
            1 for c in cols
            if c.get("DATA_TYPE", "").lower() in numeric_types
            and not c.get("COLUMN_NAME", "").lower().endswith("_id")
            and c.get("COLUMN_NAME", "").lower() != "id"
        )
        richness = non_id_numeric / len(cols) if cols else 0

        centrality_count = sum(
            1 for other in table_data.values()
            for rel in other.get("all_relationships", [])
            if rel.get("ref_table", "").lower() == tdata["table_name"].lower()
        )
        centrality = min(centrality_count / 5, 1.0)

        name_bonus = 0.3 if any(kw in tname for kw in SEMANTIC_TABLE_KEYWORDS) else 0.0

        score = row_score * 0.4 + richness * 0.2 + centrality * 0.1 + name_bonus
        scored.append((tkey, tdata, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    return [(tkey, tdata) for tkey, tdata, _ in scored]
