"""
offline_store.py — DuckDB-backed query engine for imported (no-DB) canvases.

When a .vly is imported without a live database connection, the report's raw
source tables are unpacked and persisted as Parquet in `vly_offline_tables`
(see shared/models/vly_offline.py). This module materializes those tables into
an in-process DuckDB database and executes the EXISTING widget / agent SQL
against that snapshot — so no caller needs to change how it builds SQL.

Public surface:
  - rows_to_parquet_bytes(rows, columns)         → bytes      (used at export)
  - async execute_offline_sql(db, dashboard_id, sql, row_limit) → driver-shaped dict
  - evict_offline(dashboard_id)                  → drop cached DuckDB handle
  - rewrite_for_duckdb(sql)                      → best-effort dialect shim

The returned dict matches the query_executor driver contract:
  {"rows": [...], "row_count": int, "columns": [...], "duration_ms": float,
   "truncated": bool, "error": str | None}

Storage abstraction: a table's bytes come from parquet_bytes (inline blob)
today; to move to external object storage later, populate blob_uri and extend
`_load_table_bytes` — nothing else changes.
"""

import asyncio
import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from typing import Optional

import duckdb
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ─── DuckDB handle cache ──────────────────────────────────────────────────────
# Offline table data is immutable after import, so caching a materialized DuckDB
# database per dashboard is safe. Keyed by dashboard_id; LRU-capped.
_MAX_CACHED = int(os.getenv("OFFLINE_STORE_MAX_CACHED", "8"))
_cache: "OrderedDict[str, duckdb.DuckDBPyConnection]" = OrderedDict()
_cache_lock = threading.Lock()
_build_locks: dict[str, asyncio.Lock] = {}


def _path_for_sql(p: str) -> str:
    """Filesystem path → safe single-quoted literal body for DuckDB SQL."""
    return p.replace("\\", "/").replace("'", "''")


def _split_name(table_name: str) -> tuple[Optional[str], str]:
    """'public.orders' → ('public', 'orders'); 'orders' → (None, 'orders')."""
    parts = table_name.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:-1]), parts[-1]
    return None, parts[0]


def _q(ident: str) -> str:
    """Double-quote a DuckDB identifier."""
    return '"' + ident.replace('"', '""') + '"'


# ═══════════════════════════════════════════════════════════════════════════════
# Parquet writer (export side) — no pyarrow needed; DuckDB writes Parquet itself.
# ═══════════════════════════════════════════════════════════════════════════════

def rows_to_parquet_bytes(rows: list[dict], columns: list[str]) -> bytes:
    """Serialize query-executor rows (list of dicts) to Parquet bytes."""
    con = duckdb.connect(database=":memory:")
    tmpdir = tempfile.mkdtemp(prefix="vly_pq_")
    out_path = os.path.join(tmpdir, "t.parquet")
    try:
        if not rows:
            cols = columns or ["col"]
            coldefs = ", ".join(f"{_q(c)} VARCHAR" for c in cols)
            con.execute(f"CREATE TABLE t ({coldefs})")
        else:
            ndjson_path = os.path.join(tmpdir, "t.ndjson")
            with open(ndjson_path, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, default=str))
                    fh.write("\n")
            con.execute(
                f"CREATE TABLE t AS SELECT * FROM "
                f"read_json_auto('{_path_for_sql(ndjson_path)}', format='newline_delimited')"
            )
        # Preserve declared column order where the columns exist in the table.
        existing = [row[0] for row in con.execute("DESCRIBE t").fetchall()]
        ordered = [c for c in (columns or []) if c in existing]
        ordered += [c for c in existing if c not in ordered]
        select_list = ", ".join(_q(c) for c in ordered) if ordered else "*"
        con.execute(
            f"COPY (SELECT {select_list} FROM t) TO '{_path_for_sql(out_path)}' (FORMAT PARQUET)"
        )
        with open(out_path, "rb") as fh:
            return fh.read()
    finally:
        con.close()
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# Dialect shim — best effort; on any failure the caller falls back to cached data.
# ═══════════════════════════════════════════════════════════════════════════════

# TO_CHAR format-mask tokens → strftime, greedily matched longest-first.
_TOCHAR_TOKENS = sorted([
    ("HH24", "%H"), ("HH12", "%I"),
    ("YYYY", "%Y"), ("MONTH", "%B"), ("MON", "%b"),
    ("DAY", "%A"), ("DY", "%a"),
    ("HH", "%H"), ("MI", "%M"), ("SS", "%S"),
    ("MM", "%m"), ("DD", "%d"), ("YY", "%y"),
], key=lambda t: -len(t[0]))

# Redshift DATEADD/DATEDIFF unit → DuckDB to_<unit>s() interval helper.
_DATE_UNIT = {
    "year": "to_years", "years": "to_years", "yr": "to_years",
    "month": "to_months", "months": "to_months", "mon": "to_months",
    "week": "to_weeks", "weeks": "to_weeks",
    "day": "to_days", "days": "to_days",
    "hour": "to_hours", "hours": "to_hours",
    "minute": "to_minutes", "minutes": "to_minutes",
    "second": "to_seconds", "seconds": "to_seconds",
}


def _mask_to_strftime(mask: str) -> str:
    res: list[str] = []
    i = 0
    up = mask.upper()
    while i < len(mask):
        for tok, repl in _TOCHAR_TOKENS:
            if up.startswith(tok, i):
                res.append(repl)
                i += len(tok)
                break
        else:
            res.append(mask[i])
            i += 1
    return "".join(res)


def rewrite_for_duckdb(sql: str) -> str:
    """Best-effort translation of common Redshift/Postgres/MySQL idioms to DuckDB.
    On anything it can't translate, the query simply errors and the caller falls
    back to the widget's cached data — so this never has to be exhaustive."""
    s = sql
    # MySQL backtick identifiers → double quotes.
    s = s.replace("`", '"')
    # Simple function aliases.
    s = re.sub(r"\bGETDATE\s*\(\s*\)", "now()", s, flags=re.IGNORECASE)
    s = re.sub(r"\bSYSDATE\s*\(\s*\)", "now()", s, flags=re.IGNORECASE)
    s = re.sub(r"\bISNULL\s*\(", "COALESCE(", s, flags=re.IGNORECASE)

    # TO_CHAR(expr, 'mask') → strftime(TRY_CAST(expr AS TIMESTAMP), 'translated').
    # TRY_CAST guards columns that came through the export as ISO strings.
    def _to_char(m: "re.Match") -> str:
        expr = m.group(1).strip()
        return f"strftime(TRY_CAST({expr} AS TIMESTAMP), '{_mask_to_strftime(m.group(2))}')"
    s = re.sub(
        r"\bto_char\s*\(\s*([^,()]+(?:\([^()]*\))?[^,()]*?)\s*,\s*'([^']*)'\s*\)",
        _to_char, s, flags=re.IGNORECASE,
    )

    # DATEADD(unit, n, date) → (date + to_<unit>s(n))
    def _dateadd(m: "re.Match") -> str:
        unit = _DATE_UNIT.get(m.group(1).strip().lower())
        if not unit:
            return m.group(0)
        return f"(TRY_CAST({m.group(3).strip()} AS TIMESTAMP) + {unit}({m.group(2).strip()}))"
    s = re.sub(
        r"\bdateadd\s*\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*([^()]+?(?:\([^()]*\))?[^()]*?)\s*\)",
        _dateadd, s, flags=re.IGNORECASE,
    )

    # DATEDIFF(unit, start, end) → date_diff('unit', start, end)
    def _datediff(m: "re.Match") -> str:
        unit = m.group(1).strip().lower().rstrip("s")
        return (f"date_diff('{unit}', TRY_CAST({m.group(2).strip()} AS TIMESTAMP), "
                f"TRY_CAST({m.group(3).strip()} AS TIMESTAMP))")
    s = re.sub(
        r"\bdatediff\s*\(\s*(\w+)\s*,\s*([^,]+?)\s*,\s*([^()]+?(?:\([^()]*\))?[^()]*?)\s*\)",
        _datediff, s, flags=re.IGNORECASE,
    )
    return s


# ═══════════════════════════════════════════════════════════════════════════════
# Materialization
# ═══════════════════════════════════════════════════════════════════════════════

def _load_table_bytes(rec) -> Optional[bytes]:
    """Return a table's Parquet bytes. Inline blob today; extend here for
    external object storage (rec.blob_uri)."""
    if rec.parquet_bytes:
        return rec.parquet_bytes
    # Future: if rec.blob_uri: download and return bytes.
    return None


def _build_duckdb(table_records: list) -> duckdb.DuckDBPyConnection:
    """Materialize the dashboard's offline tables into a fresh in-memory DuckDB.
    Each table is registered under its given (possibly schema-qualified) name,
    plus convenience aliases so both qualified and bare SQL references resolve."""
    con = duckdb.connect(database=":memory:")
    tmpdir = tempfile.mkdtemp(prefix="vly_load_")
    created_bare: set[str] = set()
    try:
        for i, rec in enumerate(table_records):
            data = _load_table_bytes(rec)
            if not data:
                continue
            pq_path = os.path.join(tmpdir, f"t{i}.parquet")
            with open(pq_path, "wb") as fh:
                fh.write(data)
            schema, bare = _split_name(rec.table_name)
            src = f"SELECT * FROM read_parquet('{_path_for_sql(pq_path)}')"

            if schema:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {_q(schema)}")
                con.execute(f"CREATE OR REPLACE TABLE {_q(schema)}.{_q(bare)} AS {src}")
                # bare alias (first table wins on collision)
                if bare not in created_bare:
                    con.execute(f"CREATE OR REPLACE VIEW {_q(bare)} AS SELECT * FROM {_q(schema)}.{_q(bare)}")
                    created_bare.add(bare)
            else:
                con.execute(f"CREATE OR REPLACE TABLE {_q(bare)} AS {src}")
                created_bare.add(bare)
                # public.<bare> alias so schema-qualified SQL resolves too.
                con.execute("CREATE SCHEMA IF NOT EXISTS public")
                con.execute(f"CREATE OR REPLACE VIEW public.{_q(bare)} AS SELECT * FROM {_q(bare)}")
        return con
    finally:
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass


async def _get_or_build(db: AsyncSession, dashboard_id: str) -> Optional[duckdb.DuckDBPyConnection]:
    with _cache_lock:
        con = _cache.get(dashboard_id)
        if con is not None:
            _cache.move_to_end(dashboard_id)
            return con

    lock = _build_locks.setdefault(dashboard_id, asyncio.Lock())
    async with lock:
        with _cache_lock:
            con = _cache.get(dashboard_id)
            if con is not None:
                _cache.move_to_end(dashboard_id)
                return con

        from shared.models.vly_offline import VlyOfflineTable
        import uuid as _uuid
        try:
            dash_uuid = _uuid.UUID(str(dashboard_id))
        except ValueError:
            return None
        recs = (await db.execute(
            select(VlyOfflineTable).where(VlyOfflineTable.dashboard_id == dash_uuid)
        )).scalars().all()
        if not recs:
            print(f"[offline-store] no bundled tables for dashboard={str(dashboard_id)[:8]}", flush=True)
            return None

        loop = asyncio.get_event_loop()
        con = await loop.run_in_executor(None, _build_duckdb, list(recs))
        names = [r.table_name for r in recs]
        print(f"[offline-store] materialized {len(names)} table(s) for dashboard={str(dashboard_id)[:8]}: {names}", flush=True)

        with _cache_lock:
            _cache[dashboard_id] = con
            _cache.move_to_end(dashboard_id)
            while len(_cache) > _MAX_CACHED:
                _old_id, old = _cache.popitem(last=False)
                try:
                    old.close()
                except Exception:
                    pass
        return con


def evict_offline(dashboard_id: str) -> None:
    with _cache_lock:
        con = _cache.pop(dashboard_id, None)
    if con is not None:
        try:
            con.close()
        except Exception:
            pass


def _serialize(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def _run_query(con: duckdb.DuckDBPyConnection, sql: str, row_limit: int) -> dict:
    start = time.monotonic()
    cur = con.cursor()  # independent cursor → thread-safe concurrent use
    cur.execute(sql)
    columns = [d[0] for d in (cur.description or [])]
    fetched = cur.fetchmany(row_limit + 1)
    truncated = len(fetched) > row_limit
    fetched = fetched[:row_limit]
    rows = [{columns[i]: _serialize(val) for i, val in enumerate(rec)} for rec in fetched]
    return {
        "rows": rows,
        "row_count": len(rows),
        "columns": columns,
        "duration_ms": (time.monotonic() - start) * 1000,
        "truncated": truncated,
        "error": None,
    }


async def execute_offline_sql(
    db: AsyncSession,
    dashboard_id: str,
    sql: str,
    row_limit: int = 1000,
) -> dict:
    """Execute SQL against the dashboard's offline (DuckDB) snapshot."""
    try:
        con = await _get_or_build(db, dashboard_id)
    except Exception as exc:  # noqa: BLE001
        return {"rows": [], "row_count": 0, "columns": [], "duration_ms": 0,
                "truncated": False, "error": f"offline store build failed: {exc}"}
    if con is None:
        return {"rows": [], "row_count": 0, "columns": [], "duration_ms": 0,
                "truncated": False, "error": "No offline data bundled for this canvas"}

    rewritten = rewrite_for_duckdb(sql)
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, _run_query, con, rewritten, row_limit)
    except Exception as exc:  # noqa: BLE001
        print(f"[offline-store] query failed dashboard={str(dashboard_id)[:8]}: {exc}", flush=True)
        return {"rows": [], "row_count": 0, "columns": [], "duration_ms": 0,
                "truncated": False, "error": str(exc)[:300]}
