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

# Add backend/ (parent of both schema_crawler/ and shared/) to sys.path so that
# "shared.*" imports resolve the same package instance already used by main.py.
# Pointing at "shared/" directly (the previous approach) caused SQLAlchemy to
# import models a second time under a bare "models" alias, registering every
# table twice on the same MetaData and raising InvalidRequestError on startup.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL  # noqa: E402
from shared.database import AsyncSessionLocal                            # noqa: E402
from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata  # noqa: E402
from sqlalchemy import delete                                            # noqa: E402

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
            "sample_rows": t.get("sample_rows", [])[:5],  # cap at 5 sample rows
        })

    prompt = f"""You are analyzing database tables for a BI/analytics platform.

All tables in this database (use these exact names for fk_target_table):
{json.dumps(all_table_names)}

For each table, analyze the column schema and sample rows. Infer:
- Business purpose, data grain, fact vs dimension table
- semantic_type per column: pk | fk | metric | dimension | date | identifier | text | flag
- FK target tables (only when the column name clearly implies another table in the list)
- Filter-eligible columns: low-cardinality categoricals (status, type, category) NOT IDs or free-text

Tables to analyze:
{json.dumps(tables_payload, default=str)}

Return ONLY valid JSON with this exact structure (no prose, no markdown):
{{
  "tables": [
    {{
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
        {{
          "name": "column_name",
          "description": "5-12 words: what this column measures or identifies",
          "semantic_type": "pk",
          "fk_target_table": null,
          "fk_target_column": null,
          "is_kpi_metric": false,
          "is_dimension": false,
          "is_filter_eligible": false
        }}
      ]
    }}
  ]
}}"""

    try:
        raw = await asyncio.wait_for(
            bedrock_invoke(
                model_id=_EXTRACTION_MODEL,
                system_prompt="You are a database schema analyst. Return only valid JSON.",
                user_message=prompt,
                temperature=0.0,
                max_tokens=8000,
            ),
            timeout=90.0,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = re.sub(r"\n?```\s*$", "", raw)
        parsed = json.loads(raw)
        return parsed.get("tables", [])
    except Exception as exc:
        print(f"[metadata_extractor] LLM batch failed: {exc}", flush=True)
        return []


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_metadata_extraction(
    connection_id: str,
    snapshot_version: int,
    schema_doc: dict,
    sample_rows_map: dict,     # {qualified_table_name: [row_dict, ...]}
    db_conn_kwargs: dict,      # {host, port, database, user, password, ssl}
    db_type: str,
) -> None:
    """
    Entry point called as a background task after every crawl.
    Errors are logged but never propagated — this is always non-fatal.
    """
    print(
        f"[metadata_extractor] starting  connection={connection_id}"
        f"  tables={len(schema_doc.get('tables', []))}  snapshot_v={snapshot_version}",
        flush=True,
    )
    try:
        await _do_extraction(
            connection_id, snapshot_version, schema_doc,
            sample_rows_map, db_conn_kwargs, db_type,
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
        table_payloads.append({
            "qualified_name": qualified,
            "row_count": t.get("row_count", 0),
            "columns": t.get("columns", []),
            "sample_rows": sample_rows_map.get(qualified, []),
        })

    # ── Phase A: LLM extraction (3 tables per batch, all batches in parallel) ─
    _BATCH = 3
    batches = [table_payloads[i:i + _BATCH] for i in range(0, len(table_payloads), _BATCH)]
    print(
        f"[metadata_extractor] Phase A: {len(table_payloads)} tables"
        f" → {len(batches)} LLM batch(es)",
        flush=True,
    )
    batch_results = await asyncio.gather(
        *[_call_llm_batch(b, all_qualified) for b in batches]
    )
    llm_results: list[dict] = [r for batch in batch_results for r in batch]
    print(f"[metadata_extractor] Phase A done: {len(llm_results)} tables extracted", flush=True)

    if not llm_results:
        print("[metadata_extractor] Phase A returned 0 tables — aborting", flush=True)
        return

    # Normalise FK target names — Claude might return bare table names; resolve to qualified
    qualified_set = set(all_qualified)
    for tbl in llm_results:
        for col in tbl.get("columns", []):
            raw_tgt = col.get("fk_target_table") or ""
            if raw_tgt and raw_tgt not in qualified_set:
                resolved = bare_to_qualified.get(raw_tgt)
                col["fk_target_table"] = resolved if resolved else None

    # Collect FK candidates
    fk_candidates = [
        {
            "src_table": tbl["table_name"],
            "fk_col": col["name"],
            "tgt_table": col["fk_target_table"],
            "pk_col": col["fk_target_column"],
        }
        for tbl in llm_results
        for col in tbl.get("columns", [])
        if col.get("semantic_type") == "fk"
        and col.get("fk_target_table")
        and col.get("fk_target_column")
    ]

    # Collect filter candidates (columns with no example_values yet)
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

    db_phases_supported = db_type in ("postgresql", "redshift")

    if (fk_candidates or filter_candidates) and db_phases_supported:
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
    elif not db_phases_supported:
        print(
            f"[metadata_extractor] Phase B/C skipped (db_type={db_type} not supported)",
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

    # ── Persist all results to app DB ─────────────────────────────────────────
    conn_uuid = uuid.UUID(connection_id)
    now = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        # Replace old metadata for this connection
        await db.execute(
            delete(SchemaTableMetadata).where(SchemaTableMetadata.connection_id == conn_uuid)
        )
        await db.execute(
            delete(SchemaColumnMetadata).where(SchemaColumnMetadata.connection_id == conn_uuid)
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

                # Determine FK confirmation
                is_fk = col.get("semantic_type") == "fk"
                fk_key = (tname, cname, col.get("fk_target_table") or "", col.get("fk_target_column") or "")
                fk_confirmed = is_fk and fk_key in confirmed_fks

                # Merge LLM example_values with Phase C distinct values
                example_vals: list[str] = list(col.get("example_values") or [])
                phase_c_vals = filter_values.get((tname, cname), [])
                if phase_c_vals:
                    seen = set(example_vals)
                    for v in phase_c_vals:
                        if v not in seen:
                            example_vals.append(v)
                            seen.add(v)
                example_vals = example_vals[:200]

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
                    is_filter_eligible=col.get("is_filter_eligible"),
                    generation_method="llm_sample_rows",
                    generated_at=now,
                ))

        await db.commit()

    print(
        f"[metadata_extractor] ✓ done  tables={len(llm_results)}"
        f"  confirmed_fks={len(confirmed_fks)}"
        f"  filter_cols_with_values={len(filter_values)}",
        flush=True,
    )
