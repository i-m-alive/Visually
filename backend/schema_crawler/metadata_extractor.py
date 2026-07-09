"""
schema_crawler/metadata_extractor.py

Background task that enriches schema metadata using Claude + DB validation.
Triggered automatically after every schema crawl completes.

Three phases:
  A — LLM analysis:   Claude analyzes table schema + 25 sample rows per table
                       → business names, descriptions, grain, FK candidates,
                         semantic types for every column.
  B — FK confirmation: SQL overlap check validates each FK candidate Claude suggested.
                       Only confirmed FKs (≥60% value overlap) are persisted.
  C — Filter values:   DISTINCT queries for filter-eligible columns with a 20s timeout
                       (vs the 4s runtime timeout) — pre-populates example_values so
                       the value_sampler never hits the live DB again.

Results stored in schema_table_metadata + schema_column_metadata.
schema_cache._build() reads these on cold build, injecting:
  - confirmed FK edges into the relationship graph
  - richer descriptions / semantic types into compact_tables
  - pre-collected example_values (eliminating runtime TimeoutErrors)
  - table semantics (skipping redundant _analyze_table_semantics LLM calls)
"""
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime
from typing import Optional

# Add backend/ (parent of both schema_crawler/ and shared/) to sys.path so that
# "shared.*" imports resolve the same package instance already used by main.py.
# Pointing at "shared/" directly (the previous approach) caused SQLAlchemy to
# import models a second time under a bare "models" alias, registering every
# table twice on the same MetaData and raising InvalidRequestError on startup.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.bedrock_client import bedrock_invoke_with_history, BEDROCK_SONNET_MODEL  # noqa: E402
from shared.database import AsyncSessionLocal                            # noqa: E402
from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata  # noqa: E402
from sqlalchemy import delete, select                                    # noqa: E402

_EXTRACTION_MODEL = BEDROCK_SONNET_MODEL
_FK_CONFIRM_THRESHOLD = 0.6   # 60% value overlap confirms a FK
_PII_SIGNALS = frozenset({
    "email", "phone", "ssn", "dob", "password", "secret",
    "token", "auth", "credit", "card",
})


# ── helpers ──────────────────────────────────────────────────────────────────

def _quote(qualified_name: str) -> str:
    """'schema.table' → '"schema"."table"' for SQL safety."""
    parts = qualified_name.split(".", 1)
    return f'"{parts[0]}"."{parts[1]}"' if len(parts) == 2 else f'"{qualified_name}"'


async def _open_user_db(db_conn_kwargs: dict):
    """Open an asyncpg connection to the user's database (postgres/redshift)."""
    import asyncpg
    return await asyncpg.connect(
        host=db_conn_kwargs["host"],
        port=int(db_conn_kwargs["port"]),
        database=db_conn_kwargs["database"],
        user=db_conn_kwargs["user"],
        password=db_conn_kwargs.get("password", ""),
        ssl="require" if db_conn_kwargs.get("ssl") else None,
        command_timeout=30,
    )


# ── Snowflake Phase B + C (sync connector run in executor) ────────────────────

def _sf_normalize_account(account: str) -> str:
    suffix = ".snowflakecomputing.com"
    return account[: -len(suffix)] if account.lower().endswith(suffix) else account


def _sf_open_conn(db_conn_kwargs: dict):
    import snowflake.connector
    raw_account = db_conn_kwargs.get("account", db_conn_kwargs.get("host", ""))
    account = _sf_normalize_account(raw_account)
    if account != raw_account:
        print(
            f"[metadata_extractor] Snowflake account normalized: {raw_account!r} → {account!r}",
            flush=True,
        )
    print(
        f"[metadata_extractor] Snowflake connecting"
        f"  account={account!r}  user={db_conn_kwargs.get('user')!r}"
        f"  database={db_conn_kwargs.get('database')!r}"
        f"  warehouse={db_conn_kwargs.get('warehouse')!r}"
        f"  role={db_conn_kwargs.get('role')!r}",
        flush=True,
    )
    conn = snowflake.connector.connect(
        account=account,
        user=db_conn_kwargs.get("user", ""),
        password=db_conn_kwargs.get("password", ""),
        database=db_conn_kwargs.get("database") or None,
        warehouse=db_conn_kwargs.get("warehouse") or None,
        role=db_conn_kwargs.get("role") or None,
        login_timeout=30,
        network_timeout=60,
    )
    print("[metadata_extractor] Snowflake connection established", flush=True)
    return conn


def _snowflake_run_fk_checks_sync(db_conn_kwargs: dict, fk_candidates: list) -> set:
    """Confirm FK candidates using Snowflake connector (synchronous — runs in executor)."""
    print(
        f"[metadata_extractor] Snowflake Phase B: checking {len(fk_candidates)} FK candidate(s)",
        flush=True,
    )
    confirmed: set = set()
    conn = _sf_open_conn(db_conn_kwargs)
    cursor = conn.cursor()
    try:
        for fk in fk_candidates:
            src_q = _quote(fk["src_table"])
            tgt_q = _quote(fk["tgt_table"])
            fk_col, pk_col = fk["fk_col"], fk["pk_col"]
            try:
                cursor.execute(
                    f'SELECT DISTINCT "{fk_col}" FROM {src_q} '
                    f'WHERE "{fk_col}" IS NOT NULL LIMIT 100'
                )
                rows = cursor.fetchall()
                fk_vals = [r[0] for r in rows if r[0] is not None]
                if not fk_vals:
                    continue
                in_clause = ", ".join(["%s"] * len(fk_vals))
                cursor.execute(
                    f'SELECT COUNT(DISTINCT "{pk_col}") FROM {tgt_q}'
                    f' WHERE "{pk_col}" IN ({in_clause})',
                    fk_vals,
                )
                result = cursor.fetchone()
                score = float(result[0] or 0) / len(fk_vals)
                key = (fk["src_table"], fk_col, fk["tgt_table"], pk_col)
                if score >= _FK_CONFIRM_THRESHOLD:
                    confirmed.add(key)
                    print(
                        f"[metadata_extractor] ✓ Snowflake FK {fk['src_table']}.{fk_col}"
                        f" → {fk['tgt_table']}  overlap={score:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"[metadata_extractor] ✗ Snowflake FK {fk['src_table']}.{fk_col}"
                        f" → {fk['tgt_table']}  overlap={score:.2f} (below threshold)",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[metadata_extractor] Snowflake FK check {fk}: {exc}", flush=True)
    finally:
        cursor.close()
        conn.close()
    print(
        f"[metadata_extractor] Snowflake Phase B done:"
        f"  confirmed={len(confirmed)}/{len(fk_candidates)}",
        flush=True,
    )
    return confirmed


def _snowflake_run_filter_collection_sync(db_conn_kwargs: dict, filter_candidates: list) -> dict:
    """Collect DISTINCT values for filter-eligible columns (synchronous — runs in executor)."""
    print(
        f"[metadata_extractor] Snowflake Phase C: collecting distinct values"
        f" for {len(filter_candidates)} column(s)",
        flush=True,
    )
    filter_values: dict = {}
    conn = _sf_open_conn(db_conn_kwargs)
    cursor = conn.cursor()
    try:
        for tname, cname in filter_candidates:
            try:
                cursor.execute(
                    f'SELECT DISTINCT "{cname}" FROM {_quote(tname)} '
                    f'WHERE "{cname}" IS NOT NULL LIMIT 200'
                )
                vals = [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
                if vals:
                    filter_values[(tname, cname)] = vals
                    print(
                        f"[metadata_extractor] ✓ Snowflake {tname}.{cname}"
                        f" → {len(vals)} distinct value(s)",
                        flush=True,
                    )
            except Exception as exc:
                print(f"[metadata_extractor] Snowflake distinct {tname}.{cname}: {exc}", flush=True)
    finally:
        cursor.close()
        conn.close()
    print(
        f"[metadata_extractor] Snowflake Phase C done:"
        f"  columns_with_values={len(filter_values)}/{len(filter_candidates)}",
        flush=True,
    )
    return filter_values


async def _confirm_fk(conn, src_table: str, fk_col: str, tgt_table: str, pk_col: str) -> float:
    """
    Measure FK overlap: what fraction of distinct FK values from src_table
    exist in tgt_table.pk_col?  Returns 0.0–1.0.
    """
    try:
        src_q, tgt_q = _quote(src_table), _quote(tgt_table)
        rows = await asyncio.wait_for(
            conn.fetch(
                f'SELECT DISTINCT "{fk_col}" FROM {src_q} '
                f'WHERE "{fk_col}" IS NOT NULL LIMIT 100'
            ),
            timeout=15.0,
        )
        if not rows:
            return 0.0
        fk_vals = [r[fk_col] for r in rows if r[fk_col] is not None]
        if not fk_vals:
            return 0.0
        placeholders = ", ".join(f"${i + 1}" for i in range(len(fk_vals)))
        match_count = await asyncio.wait_for(
            conn.fetchval(
                f'SELECT COUNT(*) FROM {tgt_q} WHERE "{pk_col}" IN ({placeholders})',
                *fk_vals,
            ),
            timeout=15.0,
        )
        return float(match_count or 0) / len(fk_vals)
    except Exception as exc:
        print(
            f"[metadata_extractor] FK check {src_table}.{fk_col}→{tgt_table}: {exc}",
            flush=True,
        )
        return 0.0


async def _collect_distinct_values(conn, table: str, column: str) -> list[str]:
    """DISTINCT query with a generous 20s timeout for filter-eligible columns."""
    try:
        rows = await asyncio.wait_for(
            conn.fetch(
                f'SELECT DISTINCT "{column}" FROM {_quote(table)} '
                f'WHERE "{column}" IS NOT NULL LIMIT 200'
            ),
            timeout=20.0,
        )
        return [str(r[column]) for r in rows if r[column] is not None]
    except Exception as exc:
        print(
            f"[metadata_extractor] distinct {table}.{column}: {exc}",
            flush=True,
        )
        return []


# ── Phase A: LLM extraction ───────────────────────────────────────────────────

# Cell values inside sample_rows are serialized verbatim into the prompt with
# no per-value cap — a single wide text/JSON column could otherwise dominate a
# batch's token cost with no bound. Truncate defensively before sending.
_MAX_CELL_VALUE_LEN = 200


def _truncate_sample_rows(rows: list[dict], max_len: int = _MAX_CELL_VALUE_LEN) -> list[dict]:
    out = []
    for row in rows:
        out.append({
            k: (v[:max_len] + "…" if isinstance(v, str) and len(v) > max_len else v)
            for k, v in row.items()
        })
    return out


# Static instructions — identical for every batch/mop-up call within a run, and
# identical across every run forever. Kept separate from the per-batch table
# name list and payload so both can sit behind one Anthropic prompt-cache
# breakpoint (see _call_llm_batch) instead of being re-billed on every call.
_SYSTEM_INSTRUCTIONS = """You are analyzing database tables for a BI/analytics platform.

For each table, analyze the column schema and sample rows. Infer:
- Business purpose, data grain, fact vs dimension table
- semantic_type per column: pk | fk | metric | dimension | date | identifier | text | flag
- FK target tables (ONLY when the column name clearly implies another table in the list above — do NOT invent FK targets not present in the list)
- Filter-eligible columns: low-cardinality categoricals (status, type, category) NOT IDs or free-text
- example_values: for filter-eligible dimension/category columns only, list up to 10 representative distinct values seen in sample_rows. Omit entirely (empty list []) for PII columns (email, phone, password, ssn, token, auth, credit, card, secret, dob) and for high-cardinality or numeric columns.

Return ONLY valid JSON with this exact structure (no prose, no markdown):
{
  "tables": [
    {
      "table_name": "schema.table_name",
      "business_name": "Human Friendly Name",
      "description": "One sentence: what this table stores and what one row represents.",
      "grain": "one row per <entity>",
      "is_fact_table": true,
      "use_for": ["analytics use case 1"],
      "never_use_for": ["wrong use case"],
      "key_metric_cols": ["col_used_for_sum_count"],
      "key_dimension_cols": ["col_used_for_group_by"],
      "key_date_cols": ["col_that_is_a_date"],
      "columns": [
        {
          "name": "column_name",
          "business_name": "Human Readable Column Name",
          "description": "5-12 words: what this column measures or identifies",
          "semantic_type": "pk",
          "fk_target_table": null,
          "fk_target_column": null,
          "example_values": [],
          "is_kpi_metric": false,
          "is_dimension": false,
          "is_filter_eligible": false
        }
      ]
    }
  ]
}"""


async def _call_llm_batch(batch: list[dict], all_table_names: list[str]) -> list[dict]:
    """
    Send one batch (≤3 tables) to Claude.
    Each element in `batch` is:
      {qualified_name, row_count, columns: [{name, type, is_primary_key, description}],
       sample_rows: [row_dict, ...]}
    Returns list of table metadata dicts matching the DB schema.
    """
    tables_payload = []
    for t in batch:
        cols = [
            {
                "name": c.get("name", ""),
                "type": c.get("type", ""),
                "is_pk": c.get("is_primary_key", False),
            }
            for c in t.get("columns", [])[:25]  # cap at 25 cols to control output size
        ]
        tables_payload.append({
            "table_name": t["qualified_name"],
            "row_count": t.get("row_count", 0),
            "columns": cols,
            "sample_rows": _truncate_sample_rows(t.get("sample_rows", [])[:5]),  # cap at 5 rows
        })

    # Cache breakpoint: instructions + the full table-name list are identical
    # across every batch/mop-up call within one extraction run — an unqualified
    # schema resends this ~10+ times otherwise. Anthropic prompt caching means
    # only the first call in a run pays full price for this block; subsequent
    # calls within the ~5 min cache window read it at a steep discount.
    system_blocks = [{
        "type": "text",
        "text": (
            _SYSTEM_INSTRUCTIONS
            + "\n\nAll tables in this database (use these exact names for fk_target_table):\n"
            + json.dumps(all_table_names)
        ),
        "cache_control": {"type": "ephemeral"},
    }]
    user_message = f"Tables to analyze:\n{json.dumps(tables_payload, default=str)}"

    for attempt in range(3):
        try:
            if attempt > 0:
                await asyncio.sleep(2 ** attempt)  # 2s, 4s backoff
            raw = await asyncio.wait_for(
                bedrock_invoke_with_history(
                    model_id=_EXTRACTION_MODEL,
                    system_prompt=system_blocks,
                    messages=[{"role": "user", "content": user_message}],
                    temperature=0.0,
                    max_tokens=12000,
                ),
                timeout=120.0,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
            parsed = json.loads(raw)
            results = parsed.get("tables", [])
            if results:
                return results
            # LLM returned empty tables list — treat as soft failure and retry
            print(
                f"[metadata_extractor] LLM batch returned 0 tables"
                f" (attempt {attempt + 1}/3), retrying…",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[metadata_extractor] LLM batch failed"
                f" (attempt {attempt + 1}/3): {exc}",
                flush=True,
            )
    return []


async def _persist_metadata(
    connection_id: str,
    snapshot_version: int,
    llm_results: list[dict],
    confirmed_fks: set,
    filter_values: dict,
) -> None:
    """
    Write llm_results (+ any Phase B/C enrichment already available) to the DB.

    Called twice by _do_extraction: once as a safety checkpoint right after
    Phase A (with confirmed_fks/filter_values empty, before the Phase B/C DB
    round-trips that could fail/timeout), and once more at the end with the
    fully enriched/corrected data. Both calls are idempotent — each deletes
    then reinserts only the rows for tables present in `llm_results` — so a
    crash during Phase B/C loses at most the FK-confirmation/filter-value
    enrichment, never the (expensive, already-paid-for) Phase A extraction
    itself.
    """
    conn_uuid = uuid.UUID(connection_id)
    now = datetime.utcnow()

    # Scoped delete: only the tables we're actually about to reinsert below —
    # NOT a blanket wipe of the whole connection. A table that isn't in
    # llm_results this run (unchanged and skipped, or failed extraction even
    # after mop-up) must keep its existing metadata untouched rather than
    # losing it with nothing to replace it.
    reinsert_names = {tbl.get("table_name", "") for tbl in llm_results if tbl.get("table_name")}
    if not reinsert_names:
        return

    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(SchemaTableMetadata).where(
                SchemaTableMetadata.connection_id == conn_uuid,
                SchemaTableMetadata.table_name.in_(reinsert_names),
            )
        )
        await db.execute(
            delete(SchemaColumnMetadata).where(
                SchemaColumnMetadata.connection_id == conn_uuid,
                SchemaColumnMetadata.table_name.in_(reinsert_names),
            )
        )

        for tbl in llm_results:
            tname = tbl.get("table_name", "")
            if not tname:
                continue

            db.add(SchemaTableMetadata(
                id=uuid.uuid4(),
                connection_id=conn_uuid,
                schema_snapshot_version=snapshot_version,
                table_name=tname,
                business_name=tbl.get("business_name"),
                description=tbl.get("description"),
                grain=tbl.get("grain"),
                is_fact_table=tbl.get("is_fact_table"),
                use_for=tbl.get("use_for") or [],
                never_use_for=tbl.get("never_use_for") or [],
                key_metric_cols=tbl.get("key_metric_cols") or [],
                key_dimension_cols=tbl.get("key_dimension_cols") or [],
                key_date_cols=tbl.get("key_date_cols") or [],
                generation_method="llm_sample_rows",
                generated_at=now,
            ))

            for col in tbl.get("columns", []):
                cname = col.get("name", "")
                if not cname:
                    continue

                # PII detection: columns whose name signals personally-identifiable data
                # should never have example values stored and should not be filter candidates.
                is_pii = any(sig in cname.lower() for sig in _PII_SIGNALS)

                # Determine FK confirmation
                is_fk = col.get("semantic_type") == "fk"
                fk_key = (tname, cname, col.get("fk_target_table") or "", col.get("fk_target_column") or "")
                fk_confirmed = is_fk and fk_key in confirmed_fks

                # Merge LLM example_values with Phase C distinct values.
                # Clear any examples for PII columns (crawlers mask sample rows, but the
                # LLM may still suggest inferred examples).
                if is_pii:
                    example_vals = []
                else:
                    example_vals = list(col.get("example_values") or [])
                    phase_c_vals = filter_values.get((tname, cname), [])
                    if phase_c_vals:
                        seen = set(example_vals)
                        for v in phase_c_vals:
                            if v not in seen:
                                example_vals.append(v)
                                seen.add(v)
                    example_vals = example_vals[:200]

                # Compute cardinality from collected distinct values — used downstream
                # to distinguish low-cardinality dimension columns from high-cardinality
                # free-text or ID columns.  Only populated when Phase C ran.
                cardinality = len(filter_values.get((tname, cname), [])) if not is_pii else None

                # Override is_filter_eligible for PII columns and high-cardinality columns
                is_filter_eligible = col.get("is_filter_eligible")
                if is_pii:
                    is_filter_eligible = False
                elif cardinality is not None and cardinality > 150:
                    # High cardinality → not useful as a filter dropdown
                    is_filter_eligible = False

                db.add(SchemaColumnMetadata(
                    id=uuid.uuid4(),
                    connection_id=conn_uuid,
                    schema_snapshot_version=snapshot_version,
                    table_name=tname,
                    column_name=cname,
                    business_name=col.get("business_name"),
                    description=col.get("description"),
                    semantic_type=col.get("semantic_type"),
                    fk_target_table=col.get("fk_target_table"),
                    fk_target_column=col.get("fk_target_column"),
                    fk_confirmed=fk_confirmed,
                    fk_confirmation_score=None,
                    example_values=example_vals or None,
                    is_kpi_metric=col.get("is_kpi_metric"),
                    is_dimension=col.get("is_dimension"),
                    is_filter_eligible=is_filter_eligible,
                    generation_method="llm_sample_rows",
                    generated_at=now,
                ))

        await db.commit()


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_metadata_extraction(
    connection_id: str,
    snapshot_version: int,
    schema_doc: dict,
    sample_rows_map: dict,     # {qualified_table_name: [row_dict, ...]}
    db_conn_kwargs: dict,      # {host, port, database, user, password, ssl}
    db_type: str,
    diff_summary: Optional[dict] = None,
) -> None:
    """
    Entry point called as a background task after every crawl.
    Errors are logged but never propagated — this is always non-fatal.

    diff_summary: the dict from schema_crawler.diff.compute_schema_diff, or None
    on the first-ever crawl for this connection (no prior snapshot to diff
    against). When present, extraction is scoped to added/changed tables plus
    any table with no existing metadata row yet — see _do_extraction — instead
    of redoing the whole connection's LLM extraction on every single crawl.
    """
    print(
        f"[metadata_extractor] starting  connection={connection_id}"
        f"  tables={len(schema_doc.get('tables', []))}  snapshot_v={snapshot_version}",
        flush=True,
    )
    try:
        await _do_extraction(
            connection_id, snapshot_version, schema_doc,
            sample_rows_map, db_conn_kwargs, db_type, diff_summary,
        )
    except Exception:
        import traceback
        print(
            f"[metadata_extractor] ⚠ extraction failed (non-fatal):\n{traceback.format_exc()}",
            flush=True,
        )


async def _do_extraction(
    connection_id: str,
    snapshot_version: int,
    schema_doc: dict,
    sample_rows_map: dict,
    db_conn_kwargs: dict,
    db_type: str,
    diff_summary: Optional[dict] = None,
) -> None:
    tables = schema_doc.get("tables", [])

    # Build qualified-name list and table payloads for LLM
    all_qualified: list[str] = []
    bare_to_qualified: dict[str, str] = {}
    table_payloads: list[dict] = []

    for t in tables:
        schema_part = (t.get("schema") or "").strip()
        name_part = (t.get("name") or "").strip()
        qualified = f"{schema_part}.{name_part}" if schema_part else name_part
        all_qualified.append(qualified)
        bare_to_qualified[name_part] = qualified
        row_count = t.get("row_count", 0)
        # Empty tables (0 rows) have no sample data for Phase A — include them so
        # they get basic semantic typing, but flag them so the LLM knows there's
        # no data to examine. Don't skip them entirely or they lose all enrichment.
        sample_rows = sample_rows_map.get(qualified, []) or sample_rows_map.get(name_part, [])
        table_payloads.append({
            "qualified_name": qualified,
            "row_count": row_count,
            "columns": t.get("columns", []),
            "sample_rows": sample_rows,
        })

    # ── Diff-aware scoping: skip tables that didn't change AND already have
    # metadata, instead of re-extracting everything on every single crawl ──────
    conn_uuid = uuid.UUID(connection_id)
    dropped_qualified: set[str] = set()
    tables_to_extract: Optional[set[str]] = None  # None = extract everything

    async with AsyncSessionLocal() as _db:
        existing_names = {
            row[0] for row in (await _db.execute(
                select(SchemaTableMetadata.table_name)
                .where(SchemaTableMetadata.connection_id == conn_uuid)
            )).all()
        }

    if diff_summary is not None:
        changed_bare = set(diff_summary.get("added_tables", [])) | {
            c["table"] for c in (diff_summary.get("column_changes") or [])
        }
        changed_qualified = {bare_to_qualified.get(b, b) for b in changed_bare}
        dropped_qualified = {
            bare_to_qualified.get(b, b) for b in (diff_summary.get("dropped_tables") or [])
        }
        # Always (re)extract: changed tables + any table with no metadata row
        # yet (covers first-time-seen tables and tables a prior run failed on).
        never_extracted = set(all_qualified) - existing_names
        tables_to_extract = changed_qualified | never_extracted
        skipped = len(all_qualified) - len(tables_to_extract)
        print(
            f"[metadata_extractor] diff-aware scoping: {len(tables_to_extract)} table(s)"
            f" to extract ({len(changed_qualified)} changed, {len(never_extracted)} never-extracted),"
            f" {skipped} unchanged table(s) skipped, {len(dropped_qualified)} dropped",
            flush=True,
        )
        table_payloads = [p for p in table_payloads if p["qualified_name"] in tables_to_extract]

        # Dropped-table cleanup happens here, unconditionally — NOT folded into
        # the abort-if-nothing-to-extract check below, so a crawl that only
        # dropped tables (nothing added/changed) still removes their stale
        # metadata instead of silently leaving orphaned rows behind forever.
        if dropped_qualified:
            async with AsyncSessionLocal() as _db:
                await _db.execute(
                    delete(SchemaTableMetadata).where(
                        SchemaTableMetadata.connection_id == conn_uuid,
                        SchemaTableMetadata.table_name.in_(dropped_qualified),
                    )
                )
                await _db.execute(
                    delete(SchemaColumnMetadata).where(
                        SchemaColumnMetadata.connection_id == conn_uuid,
                        SchemaColumnMetadata.table_name.in_(dropped_qualified),
                    )
                )
                await _db.commit()
            print(
                f"[metadata_extractor] removed metadata for {len(dropped_qualified)} dropped table(s)",
                flush=True,
            )

        if not table_payloads:
            print("[metadata_extractor] no added/changed tables to extract", flush=True)
            return

    # ── Phase A: LLM extraction (3 tables per batch, max 5 concurrent) ─────────
    _BATCH = 3
    # Limit to 100 table names in the prompt context (reduces token bloat per call)
    _PROMPT_TABLE_NAMES = all_qualified[:100]
    batches = [table_payloads[i:i + _BATCH] for i in range(0, len(table_payloads), _BATCH)]
    print(
        f"[metadata_extractor] Phase A: {len(table_payloads)} tables"
        f" → {len(batches)} LLM batch(es) (max 5 concurrent)",
        flush=True,
    )
    # Semaphore prevents thundering-herd rate-limit failures when there are many tables
    _sem = asyncio.Semaphore(5)

    async def _guarded_batch(b: list[dict]) -> list[dict]:
        async with _sem:
            return await _call_llm_batch(b, _PROMPT_TABLE_NAMES)

    batch_results = await asyncio.gather(*[_guarded_batch(b) for b in batches])
    llm_results: list[dict] = [r for batch in batch_results for r in batch]
    print(f"[metadata_extractor] Phase A done: {len(llm_results)} tables extracted", flush=True)

    # Mop-up: retry any tables that were silently dropped (solo, 1 per batch)
    extracted_names = {t.get("table_name", "").lower() for t in llm_results}
    missing_payloads = [
        p for p in table_payloads
        if p["qualified_name"].lower() not in extracted_names
    ]
    if missing_payloads:
        print(
            f"[metadata_extractor] Phase A mop-up: {len(missing_payloads)} tables"
            " missing, retrying solo (1 per call)…",
            flush=True,
        )
        mop_sem = asyncio.Semaphore(3)

        async def _guarded_solo(p: dict) -> list[dict]:
            async with mop_sem:
                return await _call_llm_batch([p], _PROMPT_TABLE_NAMES)

        mop_results = await asyncio.gather(*[_guarded_solo(p) for p in missing_payloads])
        recovered = [r for batch in mop_results for r in batch]
        print(
            f"[metadata_extractor] Phase A mop-up recovered {len(recovered)}"
            f" / {len(missing_payloads)} tables",
            flush=True,
        )
        llm_results.extend(recovered)

        # Failure visibility: tables still missing after batch + mop-up retries
        # get no metadata row and previously vanished into a bare count with no
        # indication of WHICH tables failed. Log each one with a consistent,
        # greppable tag — Phase 4's never_extracted check already retries these
        # automatically on the next crawl since they have no existing row.
        recovered_names = {t.get("table_name", "").lower() for t in recovered}
        still_missing = [
            p["qualified_name"] for p in missing_payloads
            if p["qualified_name"].lower() not in recovered_names
        ]
        for tname in still_missing:
            print(f"[metadata_extractor] EXTRACTION_FAILED table={tname}", flush=True)

    if not llm_results:
        print("[metadata_extractor] Phase A returned 0 tables — aborting", flush=True)
        return

    # Safety checkpoint: persist Phase A's output now, BEFORE the Phase B/C
    # DB round-trips below (which open a connection to the USER's database and
    # could fail/timeout) — so a crash there doesn't discard this (expensive,
    # already-paid-for) LLM extraction with nothing to show for it.
    try:
        await _persist_metadata(connection_id, snapshot_version, llm_results, set(), {})
        print(f"[metadata_extractor] checkpoint: persisted {len(llm_results)} table(s) from Phase A", flush=True)
    except Exception as _cp_exc:
        print(f"[metadata_extractor] checkpoint persist failed (non-fatal, retried at final persist): {_cp_exc}", flush=True)

    # Normalise FK target names — Claude might return bare table names; resolve to qualified
    qualified_set = set(all_qualified)
    for tbl in llm_results:
        for col in tbl.get("columns", []):
            raw_tgt = col.get("fk_target_table") or ""
            if raw_tgt and raw_tgt not in qualified_set:
                resolved = bare_to_qualified.get(raw_tgt)
                col["fk_target_table"] = resolved if resolved else None

    # Build a fast lookup: bare column name → set of qualified table names that have it
    # Used to validate FK targets actually have the referenced primary key column.
    _tbl_col_lookup: dict[str, set] = {}
    for tbl in llm_results:
        tname = tbl["table_name"]
        for col in tbl.get("columns", []):
            _tbl_col_lookup.setdefault(tname, set()).add(col["name"].lower())
    # Also index bare table names for unqualified lookups
    _bare_col_lookup: dict[str, set] = {
        tname.split(".")[-1]: cols for tname, cols in _tbl_col_lookup.items()
    }

    # Collect FK candidates — filter out those whose target table OR target column
    # doesn't exist in the schema (prevents hallucinated FKs from being DB-validated)
    fk_candidates = []
    for tbl in llm_results:
        for col in tbl.get("columns", []):
            if col.get("semantic_type") != "fk":
                continue
            tgt_table = col.get("fk_target_table")
            tgt_col = col.get("fk_target_column")
            if not tgt_table or not tgt_col:
                continue
            # Verify FK target table is in the known schema
            tgt_cols = _tbl_col_lookup.get(tgt_table) or _bare_col_lookup.get(tgt_table.split(".")[-1])
            if tgt_cols is None:
                print(
                    f"[metadata_extractor] Dropping FK {tbl['table_name']}.{col['name']}"
                    f" → {tgt_table} (target table not in schema)",
                    flush=True,
                )
                col["fk_target_table"] = None
                col["fk_target_column"] = None
                col["semantic_type"] = "identifier"
                continue
            # Verify FK target column exists in the target table
            if tgt_col.lower() not in tgt_cols:
                print(
                    f"[metadata_extractor] Dropping FK {tbl['table_name']}.{col['name']}"
                    f" → {tgt_table}.{tgt_col} (column not found in target)",
                    flush=True,
                )
                col["fk_target_table"] = None
                col["fk_target_column"] = None
                col["semantic_type"] = "identifier"
                continue
            fk_candidates.append({
                "src_table": tbl["table_name"],
                "fk_col": col["name"],
                "tgt_table": tgt_table,
                "pk_col": tgt_col,
            })

    # Collect filter candidates — columns marked filter-eligible with no example values yet.
    # Also collect columns that have example_values = [] (empty list, not missing) from prior runs.
    filter_candidates = [
        (tbl["table_name"], col["name"])
        for tbl in llm_results
        for col in tbl.get("columns", [])
        if col.get("is_filter_eligible") and not col.get("example_values")
    ]

    # ── Phase B + C: DB queries (requires reconnecting to user DB) ────────────
    user_conn = None
    confirmed_fks: set[tuple] = set()    # (src_table, fk_col, tgt_table, pk_col)
    filter_values: dict[tuple, list[str]] = {}

    db_phases_supported = db_type in ("postgresql", "redshift", "snowflake")

    if not (fk_candidates or filter_candidates):
        pass  # nothing to do
    elif not db_phases_supported:
        print(
            f"[metadata_extractor] Phase B/C skipped (db_type={db_type} not supported)",
            flush=True,
        )
    elif db_type == "snowflake":
        # Snowflake uses a sync connector — run both phases in the thread executor.
        loop = asyncio.get_running_loop()
        if fk_candidates:
            print(
                f"[metadata_extractor] Phase B (Snowflake): confirming"
                f" {len(fk_candidates)} FK candidate(s)",
                flush=True,
            )
            try:
                confirmed_fks = await loop.run_in_executor(
                    None, _snowflake_run_fk_checks_sync, db_conn_kwargs, fk_candidates
                )
            except Exception as exc:
                print(f"[metadata_extractor] Snowflake Phase B failed: {exc}", flush=True)
        if filter_candidates:
            print(
                f"[metadata_extractor] Phase C (Snowflake): collecting distinct values"
                f" for {len(filter_candidates)} filter column(s)",
                flush=True,
            )
            try:
                filter_values = await loop.run_in_executor(
                    None, _snowflake_run_filter_collection_sync, db_conn_kwargs, filter_candidates
                )
            except Exception as exc:
                print(f"[metadata_extractor] Snowflake Phase C failed: {exc}", flush=True)
    else:
        # PostgreSQL / Redshift — asyncpg path
        try:
            user_conn = await _open_user_db(db_conn_kwargs)
        except Exception as exc:
            print(f"[metadata_extractor] cannot open user DB for Phases B/C: {exc}", flush=True)

        if user_conn and fk_candidates:
            print(
                f"[metadata_extractor] Phase B: confirming {len(fk_candidates)} FK candidate(s)",
                flush=True,
            )
            for fk in fk_candidates:
                score = await _confirm_fk(
                    user_conn,
                    fk["src_table"], fk["fk_col"],
                    fk["tgt_table"], fk["pk_col"],
                )
                key = (fk["src_table"], fk["fk_col"], fk["tgt_table"], fk["pk_col"])
                if score >= _FK_CONFIRM_THRESHOLD:
                    confirmed_fks.add(key)
                    print(
                        f"[metadata_extractor] ✓ FK {fk['src_table']}.{fk['fk_col']}"
                        f" → {fk['tgt_table']}  overlap={score:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"[metadata_extractor] ✗ FK {fk['src_table']}.{fk['fk_col']}"
                        f" → {fk['tgt_table']}  overlap={score:.2f} (below threshold)",
                        flush=True,
                    )

        if user_conn and filter_candidates:
            print(
                f"[metadata_extractor] Phase C: collecting distinct values"
                f" for {len(filter_candidates)} filter column(s)",
                flush=True,
            )
            for tname, cname in filter_candidates:
                vals = await _collect_distinct_values(user_conn, tname, cname)
                if vals:
                    filter_values[(tname, cname)] = vals
                    print(
                        f"[metadata_extractor] ✓ {tname}.{cname}"
                        f" → {len(vals)} distinct value(s)",
                        flush=True,
                    )

        if user_conn:
            try:
                await user_conn.close()
            except Exception:
                pass

    # ── Semantic type verification (post-process LLM output) ──────────────────
    # The LLM can misclassify columns — apply deterministic corrections before
    # persisting to catch obvious errors without an extra LLM call.
    _NUMERIC_TYPE_FRAGMENTS = frozenset({
        "int", "float", "decimal", "numeric", "double",
        "bigint", "smallint", "real", "money", "number",
    })
    _DATE_TYPE_FRAGMENTS = frozenset({"date", "time", "timestamp"})
    _BOOL_TYPE_FRAGMENTS = frozenset({"bool", "bit", "tinyint(1)"})

    for tbl in llm_results:
        for col in tbl.get("columns", []):
            cname_l = col.get("name", "").lower()
            ctype_l = (col.get("type") or "").lower()
            stype = (col.get("semantic_type") or "").lower()

            # Primary key columns: if column_key indicates PK, override to "pk"
            if col.get("is_primary_key") and stype not in ("pk",):
                col["semantic_type"] = "pk"

            # Date/time columns should be "date", not "dimension" or "text"
            elif stype not in ("date", "pk") and any(f in ctype_l for f in _DATE_TYPE_FRAGMENTS):
                col["semantic_type"] = "date"

            # Boolean / flag columns should be "flag"
            elif stype not in ("pk", "flag") and any(f in ctype_l for f in _BOOL_TYPE_FRAGMENTS):
                col["semantic_type"] = "flag"
                col["is_kpi_metric"] = False
                col["is_dimension"] = False

            # Numeric columns misclassified as "identifier" or "text" → "metric"
            elif stype in ("identifier", "text") and any(f in ctype_l for f in _NUMERIC_TYPE_FRAGMENTS):
                if not cname_l.endswith("_id") and cname_l != "id":
                    col["semantic_type"] = "metric"
                    col["is_kpi_metric"] = True

    # ── Persist all results to app DB ─────────────────────────────────────────
    await _persist_metadata(connection_id, snapshot_version, llm_results, confirmed_fks, filter_values)

    # Invalidate the in-process schema cache so that the next query against this
    # connection rebuilds EnrichedSchema from the freshly stored metadata (with
    # correct semantic types, FK edges, example_values, and a fresh TF-IDF index).
    # Without this, the stale cache persists until its TTL expires (up to 72h).
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from agent_service.agents import schema_cache as _sc
        _sc.invalidate(connection_id)
        print(f"[metadata_extractor] schema cache invalidated for {connection_id[:8]}", flush=True)
    except Exception as _inv_exc:
        print(f"[metadata_extractor] cache invalidation skipped: {_inv_exc}", flush=True)

    print(
        f"[metadata_extractor] ✓ done  tables={len(llm_results)}"
        f"  confirmed_fks={len(confirmed_fks)}"
        f"  filter_cols_with_values={len(filter_values)}",
        flush=True,
    )
