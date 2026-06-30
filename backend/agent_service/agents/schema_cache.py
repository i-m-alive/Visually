"""
Schema Cache — built once per connection, persisted to Redis, exportable as JSON.

Four enrichments on top of the raw schema:
  1. column_map        — {col_name: [{table, type, description}]} for every column
  2. disambiguation    — LLM-inferred meaning per occurrence for ambiguous column names
  3. table_semantics   — LLM-inferred purpose, grain, key metric/dimension/date cols
  4. relationship_graph — FK adjacency graph from table.relationships

Cache hierarchy:
  L1 — in-process dict (_store): zero-latency, lives for the process lifetime
  L2 — Redis: keyed by connection_id + schema_hash, TTL = SCHEMA_CACHE_TTL seconds
  L3 — cold build: LLM disambiguation + table semantics in parallel (~10-20s)

Schema hash invalidation: if the schema changes (new migration), the hash changes
and Redis cache is bypassed. The old key expires automatically after TTL.

Export/import: the full enriched schema serialises to a single JSON string that can
be downloaded, committed, and re-uploaded to skip the cold build entirely.
"""
import asyncio
import hashlib
import json
import os
import re
import pathlib
from dataclasses import dataclass, field
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_CACHE_MODEL = BEDROCK_SONNET_MODEL

SCHEMA_CACHE_TTL = int(os.getenv("SCHEMA_CACHE_TTL", "259200"))  # 72 h default
_REDIS_KEY_PREFIX = "schema_cache"

# ── Filesystem cache (L2 fallback when Redis is unavailable) ──────────────────
# Read env var at call time (not module load) so .env is already loaded by then.
def _fs_cache_dir() -> pathlib.Path:
    return pathlib.Path(os.getenv("SCHEMA_CACHE_DIR", str(pathlib.Path(__file__).parent.parent.parent / ".schema_cache")))

def _fs_cache_path(connection_id: str, schema_hash: str) -> pathlib.Path:
    d = _fs_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"schema_{connection_id}_{schema_hash}.json"

def _fs_read(connection_id: str, schema_hash: str) -> Optional[str]:
    try:
        p = _fs_cache_path(connection_id, schema_hash)
        if p.exists():
            print(f"[schema_cache] reading filesystem cache  path={p}", flush=True)
            return p.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[schema_cache] ⚠ filesystem read failed: {e}", flush=True)
    return None

def _fs_write(connection_id: str, schema_hash: str, data: str) -> None:
    try:
        p = _fs_cache_path(connection_id, schema_hash)
        p.write_text(data, encoding="utf-8")
        print(f"[schema_cache] ✓ filesystem write OK  path={p}  size={len(data)//1024}KB", flush=True)
    except Exception as e:
        print(f"[schema_cache] ✗ filesystem write FAILED: {e}  dir={_fs_cache_dir()}", flush=True)

# ── in-process L1 cache ───────────────────────────────────────────────────────
_store: dict[str, "EnrichedSchema"] = {}


# ── Schema hash ───────────────────────────────────────────────────────────────

def compute_schema_hash(schema_doc: dict) -> str:
    """
    Lightweight fingerprint for change detection.
    Hashes sorted(table_name:column_count) — invalidated by new tables/columns
    (a migration) but stable across description-only edits.
    """
    tables = schema_doc.get("tables", [])
    sig = "|".join(
        f"{t.get('name', '')}:{len(t.get('columns', []))}"
        for t in sorted(tables, key=lambda x: x.get("name", ""))
    )
    return hashlib.md5(sig.encode()).hexdigest()[:12]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class RelationshipGraph:
    """
    Bidirectional FK adjacency graph built from schema table.relationships.
    edges: {table_a: {table_b: "table_a.fk_col = table_b.pk_col"}}
    """
    edges: dict = field(default_factory=dict)

    def add_edge(self, from_table: str, to_table: str, condition: str) -> None:
        self.edges.setdefault(from_table, {})[to_table] = condition
        self.edges.setdefault(to_table, {})[from_table] = condition

    def path_exists(self, table_a: str, table_b: str, max_hops: int = 2) -> bool:
        if table_a == table_b:
            return True
        visited = {table_a}
        frontier = [table_a]
        for _ in range(max_hops):
            next_frontier = []
            for node in frontier:
                for neighbor in self.edges.get(node, {}):
                    if neighbor == table_b:
                        return True
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return False

    def get_join_condition(self, table_a: str, table_b: str) -> Optional[str]:
        return self.edges.get(table_a, {}).get(table_b)

    def __len__(self) -> int:
        return sum(len(v) for v in self.edges.values()) // 2


@dataclass
class EnrichedSchema:
    schema_doc: dict
    db_type: str

    column_map: dict = field(default_factory=dict)
    ambiguous_columns: list = field(default_factory=list)
    disambiguation: dict = field(default_factory=dict)
    table_semantics: dict = field(default_factory=dict)
    compact_tables: list = field(default_factory=list)
    relationship_graph: RelationshipGraph = field(default_factory=RelationshipGraph)

    # ── NL2SQL intelligence index (Phase 2–3) ─────────────────────────────────
    # concept_index: maps business-vocabulary terms to table.column entries.
    #   {concept_term: [{table, column, score, context}]}
    #   e.g. {"revenue": [{"table": "billing.timesheetentry", "column": "billedgeneralrate", "score": 0.9, "context": "metric"}]}
    concept_index: dict = field(default_factory=dict)

    # entity_columns: maps entity types to their "name" columns.
    #   {entity_type: [{table, column, is_primary, sample_values}]}
    #   e.g. {"company": [{"table": "client_corporation", "column": "parentname", "is_primary": True, "sample_values": [...]}]}
    entity_columns: dict = field(default_factory=dict)

    # tfidf_index: pre-computed TF-IDF document vectors per table for Graph RAG retrieval.
    #   {"idf": {term: idf_weight}, "tables": {table_name: {"tfidf_vec": {term: weight}}}}
    tfidf_index: dict = field(default_factory=dict)

    def get_disambiguation_text(self) -> str:
        if not self.disambiguation:
            return ""
        lines = ["COLUMN DISAMBIGUATION (same column name, different meanings per table):"]
        for col_name, occurrences in self.disambiguation.items():
            lines.append(f"  • {col_name}:")
            for occ in occurrences:
                lines.append(
                    f"      [{occ['table']}] {occ.get('meaning', '')} "
                    f"— use when: {occ.get('use_when', '')}"
                )
        return "\n".join(lines)

    def get_table_semantics_text(self, table_names: Optional[list] = None) -> str:
        lines = ["TABLE SEMANTICS:"]
        for tname, sem in self.table_semantics.items():
            if table_names and tname not in table_names:
                continue
            use_for = sem.get("use_for") or []
            never_use_for = sem.get("never_use_for") or []
            line = (
                f"  [{tname}] {sem.get('purpose', '')} | grain: {sem.get('grain', '')} | "
                f"metrics: {sem.get('key_metric_cols', [])} | "
                f"dims: {sem.get('key_dimension_cols', [])} | "
                f"dates: {sem.get('key_date_cols', [])}"
            )
            if use_for:
                line += f" | use_for: {use_for}"
            if never_use_for:
                line += f" | never_use_for: {never_use_for}"
            lines.append(line)
        return "\n".join(lines)


# ── Serialisation ─────────────────────────────────────────────────────────────

def _serialize_enriched(enriched: EnrichedSchema) -> str:
    """Serialize an EnrichedSchema to a compact JSON string (for Redis or export)."""
    data = {
        "schema_doc": enriched.schema_doc,
        "db_type": enriched.db_type,
        "column_map": enriched.column_map,
        "ambiguous_columns": enriched.ambiguous_columns,
        "disambiguation": enriched.disambiguation,
        "table_semantics": enriched.table_semantics,
        "compact_tables": enriched.compact_tables,
        "relationship_graph_edges": enriched.relationship_graph.edges,
        # NL2SQL intelligence indexes
        "concept_index": enriched.concept_index,
        "entity_columns": enriched.entity_columns,
        "tfidf_index": enriched.tfidf_index,
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _deserialize_enriched(json_str: str) -> EnrichedSchema:
    """Reconstruct an EnrichedSchema from a serialized JSON string."""
    data = json.loads(json_str)
    graph = RelationshipGraph()
    graph.edges = data.get("relationship_graph_edges", {})
    return EnrichedSchema(
        schema_doc=data["schema_doc"],
        db_type=data["db_type"],
        column_map=data["column_map"],
        ambiguous_columns=data["ambiguous_columns"],
        disambiguation=data["disambiguation"],
        table_semantics=data["table_semantics"],
        compact_tables=data["compact_tables"],
        relationship_graph=graph,
        # NL2SQL intelligence indexes — default to {} for backward compat with old caches
        concept_index=data.get("concept_index") or {},
        entity_columns=data.get("entity_columns") or {},
        tfidf_index=data.get("tfidf_index") or {},
    )


# ── Public cache API ──────────────────────────────────────────────────────────

async def get_or_build(connection_id: str, schema_doc: dict, db_type: str) -> EnrichedSchema:
    """
    Return the enriched schema for this connection (L1 → L2 → L3).
    """
    # L1: in-process
    if connection_id in _store:
        print(f"[schema_cache] ✓ in-process hit  connection={connection_id}", flush=True)
        return _store[connection_id]

    # L2: Redis (preferred) → filesystem (fallback when Redis is unavailable)
    schema_hash = compute_schema_hash(schema_doc)
    redis_key = f"{_REDIS_KEY_PREFIX}:{connection_id}:{schema_hash}"
    redis_available = False
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            redis_available = True
            cached_json = await redis.get(redis_key)
            if cached_json:
                enriched = _deserialize_enriched(cached_json)
                _store[connection_id] = enriched
                print(
                    f"[schema_cache] ✓ Redis hit  connection={connection_id}  hash={schema_hash}",
                    flush=True,
                )
                return enriched
    except Exception as _re:
        print(f"[schema_cache] ⚠ Redis read failed (non-fatal): {_re}", flush=True)

    # L2b: filesystem cache — always check after a Redis miss (not only when Redis is down)
    fs_json = _fs_read(connection_id, schema_hash)
    if fs_json:
        enriched = _deserialize_enriched(fs_json)
        _store[connection_id] = enriched
        print(
            f"[schema_cache] ✓ filesystem hit  connection={connection_id}  hash={schema_hash}",
            flush=True,
        )
        # Backfill Redis while we're here so the next hit is faster
        if redis_available:
            try:
                redis = await get_redis()
                if redis is not None:
                    await redis.setex(redis_key, SCHEMA_CACHE_TTL, fs_json)
                    print(f"[schema_cache] ✓ backfilled Redis from filesystem  connection={connection_id}", flush=True)
            except Exception:
                pass
        return enriched

    # L3: cold build
    print(
        f"[schema_cache] building enriched schema  connection={connection_id}"
        f"  tables={len(schema_doc.get('tables', []))}",
        flush=True,
    )
    enriched = await _build(schema_doc, db_type, connection_id)
    _store[connection_id] = enriched

    # Persist to both Redis and filesystem so either can warm the cache after a restart
    serialized = _serialize_enriched(enriched)
    if redis_available:
        try:
            redis = await get_redis()
            if redis is not None:
                await redis.setex(redis_key, SCHEMA_CACHE_TTL, serialized)
                print(
                    f"[schema_cache] ✓ stored in Redis  connection={connection_id}"
                    f"  hash={schema_hash}  ttl={SCHEMA_CACHE_TTL}s  size={len(serialized)//1024}KB",
                    flush=True,
                )
        except Exception as _re:
            print(f"[schema_cache] ⚠ Redis write failed (non-fatal): {_re}", flush=True)
    # Always write filesystem — survives Redis eviction and server restarts
    _fs_write(connection_id, schema_hash, serialized)

    print(
        f"[schema_cache] ✓ built  ambiguous_cols={len(enriched.ambiguous_columns)}"
        f"  tables_analysed={len(enriched.table_semantics)}",
        flush=True,
    )
    return enriched


async def get_cached(connection_id: str, schema_doc: dict) -> Optional["EnrichedSchema"]:
    """Return the enriched schema ONLY if already cached (L1 → Redis → filesystem).
    NEVER builds. Used by .vly export so exporting bundles a warm cache when present
    but never triggers a multi-minute cold LLM enrichment build."""
    if connection_id in _store:
        return _store[connection_id]
    try:
        schema_hash = compute_schema_hash(schema_doc)
    except Exception:
        return None
    redis_key = f"{_REDIS_KEY_PREFIX}:{connection_id}:{schema_hash}"
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            cached_json = await redis.get(redis_key)
            if cached_json:
                enriched = _deserialize_enriched(cached_json)
                _store[connection_id] = enriched
                return enriched
    except Exception:
        pass
    fs_json = _fs_read(connection_id, schema_hash)
    if fs_json:
        enriched = _deserialize_enriched(fs_json)
        _store[connection_id] = enriched
        return enriched
    return None


# Tiny precomputed table-name list cache (just [{name, columns}]) — keeps the
# picker instant after the first open without re-reading the multi-MB snapshot.
_TABLE_LIST_PREFIX = "schema_tables_list"
_TABLE_LIST_TTL = int(os.getenv("SCHEMA_TABLE_LIST_TTL", "3600"))  # 1 h


async def get_table_list_cached(connection_id: str) -> Optional[list[dict]]:
    """Return a previously-cached lightweight table list, or None."""
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            raw = await redis.get(f"{_TABLE_LIST_PREFIX}:{connection_id}")
            if raw:
                data = json.loads(raw)
                if isinstance(data, list):
                    return data
    except Exception as _e:
        print(f"[schema_cache] ⚠ get_table_list_cached failed: {_e}", flush=True)
    return None


async def set_table_list_cached(connection_id: str, tables: list[dict]) -> None:
    """Cache a lightweight table list for fast repeat picker opens."""
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            await redis.setex(
                f"{_TABLE_LIST_PREFIX}:{connection_id}",
                _TABLE_LIST_TTL,
                json.dumps(tables),
            )
    except Exception as _e:
        print(f"[schema_cache] ⚠ set_table_list_cached failed: {_e}", flush=True)


async def get_cached_table_names(connection_id: str) -> Optional[list[dict]]:
    """Return [{name, columns}] for a connection from ANY warm enriched-cache tier
    (in-process → Redis → filesystem) WITHOUT rebuilding. Used by lightweight table
    pickers when no SchemaSnapshot row exists but the enriched cache is warm
    (common for imported .vly canvases). Returns None if nothing is cached."""
    def _names(enriched: "EnrichedSchema") -> list[dict]:
        return [
            {"name": t.get("name", ""), "columns": len(t.get("columns") or [])}
            for t in (enriched.compact_tables or []) if t.get("name")
        ]

    # L1 in-process
    enr = _store.get(connection_id)
    if enr and enr.compact_tables:
        return _names(enr)

    # Lightweight: pluck names straight from the serialized JSON without rebuilding
    # the full EnrichedSchema (relationship graph, column_map, etc.) — much faster
    # for big schemas where the enriched blob is multiple MB.
    def _names_from_json(raw: str) -> Optional[list[dict]]:
        try:
            data = json.loads(raw)
            cts = data.get("compact_tables") or []
            names = [
                {"name": t.get("name", ""), "columns": len(t.get("columns") or [])}
                for t in cts if t.get("name")
            ]
            return names or None
        except Exception:
            return None

    # L2 Redis (any schema_hash for this connection)
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            async for key in redis.scan_iter(f"{_REDIS_KEY_PREFIX}:{connection_id}:*"):
                cached = await redis.get(key)
                if cached:
                    names = _names_from_json(cached)
                    if names:
                        return names
    except Exception as _e:
        print(f"[schema_cache] ⚠ get_cached_table_names Redis read failed: {_e}", flush=True)

    # L3 filesystem
    try:
        import glob as _glob
        for p in _glob.glob(str(_fs_cache_dir() / f"schema_{connection_id}_*.json")):
            names = _names_from_json(pathlib.Path(p).read_text(encoding="utf-8"))
            if names:
                return names
    except Exception as _e:
        print(f"[schema_cache] ⚠ get_cached_table_names filesystem read failed: {_e}", flush=True)

    return None


def invalidate(connection_id: str) -> None:
    """
    Evict all cached data for this connection from in-process cache and Redis.
    Call after a schema re-crawl or when a migration is detected.
    """
    _store.pop(connection_id, None)
    import asyncio as _asyncio

    async def _clear_redis():
        try:
            from shared.redis_client import get_redis
            redis = await get_redis()
            if redis is not None:
                async for key in redis.scan_iter(f"{_REDIS_KEY_PREFIX}:{connection_id}:*"):
                    await redis.delete(key)
                print(
                    f"[schema_cache] invalidated Redis keys for connection={connection_id}",
                    flush=True,
                )
        except Exception:
            pass

    try:
        loop = _asyncio.get_event_loop()
        if loop.is_running():
            _asyncio.ensure_future(_clear_redis())
    except Exception:
        pass


# ── Export / Import ───────────────────────────────────────────────────────────

def export_cache_json(connection_id: str) -> Optional[str]:
    """
    Export the cached enriched schema for this connection as a JSON string.
    Returns None if the connection is not in cache (call get_or_build first).

    The exported string can be:
      • Saved as  schema_cache_{connection_id}.json  and committed to the repo
      • Uploaded on a fresh environment to skip the cold build entirely
      • Downloaded via GET /schema-cache/{connection_id}/export
    """
    enriched = _store.get(connection_id)
    if not enriched:
        return None
    return _serialize_enriched(enriched)


def import_cache_json(connection_id: str, json_str: str) -> EnrichedSchema:
    """
    Import a previously exported schema cache JSON into the in-process cache.
    Returns the reconstructed EnrichedSchema.
    """
    enriched = _deserialize_enriched(json_str)
    _store[connection_id] = enriched
    print(
        f"[schema_cache] ✓ imported from JSON  connection={connection_id}"
        f"  tables={len(enriched.compact_tables)}  ambiguous_cols={len(enriched.ambiguous_columns)}",
        flush=True,
    )
    return enriched


async def export_cache_for_connection(
    connection_id: str, schema_doc: dict, db_type: str, cache_only: bool = False
) -> Optional[str]:
    """
    Return the serialized enriched cache for this connection.

    cache_only=True  → return it ONLY if already warm (L1/Redis/FS); never build.
                       Used by .vly export so exporting is fast and never triggers a
                       cold crawl+LLM enrichment.
    cache_only=False → build it from schema_doc if not cached (slow on a cold cache).

    Returns None if no cache is available (and, when cache_only, not yet built).
    """
    if not connection_id or not schema_doc or not schema_doc.get("tables"):
        return None
    try:
        if cache_only:
            enriched = await get_cached(connection_id, schema_doc)
            if enriched is None:
                return None
        else:
            # get_or_build populates _store (L1) — hitting Redis/filesystem if warm,
            # cold-building once otherwise.
            await get_or_build(connection_id, schema_doc, db_type)
    except Exception as exc:
        print(f"[schema_cache] ⚠ export_cache_for_connection failed: {exc}", flush=True)
        return None
    return export_cache_json(connection_id)


async def install_imported_cache(connection_id: str, enriched_json: str) -> Optional[str]:
    """
    Install a cache that was exported elsewhere (e.g. embedded in a .vly archive) so a
    freshly-resolved connection is warm with ZERO cold build.

    Re-keys the cache onto `connection_id` and persists it across all tiers:
      • L1 in-process (_store)
      • filesystem  (survives restarts)
      • Redis       (shared across workers, when available)

    The cache key is derived from the embedded schema_doc's hash, so it matches the
    hash that get_or_build() computes from the SchemaSnapshot written at import time.

    Returns the schema_hash used, or None on failure (non-fatal — caller falls back to
    a normal crawl/cold build).
    """
    try:
        enriched = _deserialize_enriched(enriched_json)
    except Exception as exc:
        print(f"[schema_cache] ⚠ install_imported_cache deserialize failed: {exc}", flush=True)
        return None

    _store[connection_id] = enriched
    schema_hash = compute_schema_hash(enriched.schema_doc)
    # Re-serialize canonically so the persisted form matches what _serialize_enriched
    # would write (the incoming string may carry foreign whitespace / key order).
    serialized = _serialize_enriched(enriched)

    _fs_write(connection_id, schema_hash, serialized)
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            await redis.setex(
                f"{_REDIS_KEY_PREFIX}:{connection_id}:{schema_hash}",
                SCHEMA_CACHE_TTL,
                serialized,
            )
            print(
                f"[schema_cache] ✓ installed imported cache in Redis  connection={connection_id}"
                f"  hash={schema_hash}",
                flush=True,
            )
    except Exception as exc:
        print(f"[schema_cache] ⚠ install_imported_cache Redis write failed (non-fatal): {exc}", flush=True)

    print(
        f"[schema_cache] ✓ imported cache installed  connection={connection_id}"
        f"  hash={schema_hash}  tables={len(enriched.compact_tables)}",
        flush=True,
    )
    return schema_hash


# ── Internal builder ──────────────────────────────────────────────────────────

async def _load_db_metadata(connection_id: str) -> dict:
    """
    Load persisted schema metadata from the DB (written by metadata_extractor).
    Returns:
      {
        "tables":  {qualified_table_name: SchemaTableMetadata ORM object},
        "columns": {qualified_table_name: {column_name: SchemaColumnMetadata ORM object}},
      }
    Non-fatal — returns empty dicts on any failure (cold path still works without it).
    """
    if not connection_id:
        return {"tables": {}, "columns": {}}
    try:
        import uuid as _uuid
        from sqlalchemy import select as _select
        from shared.database import AsyncSessionLocal
        from shared.models.schema_metadata import SchemaTableMetadata, SchemaColumnMetadata

        conn_uuid = _uuid.UUID(connection_id)
        async with AsyncSessionLocal() as db:
            tbl_rows = (await db.execute(
                _select(SchemaTableMetadata)
                .where(SchemaTableMetadata.connection_id == conn_uuid)
            )).scalars().all()
            col_rows = (await db.execute(
                _select(SchemaColumnMetadata)
                .where(SchemaColumnMetadata.connection_id == conn_uuid)
            )).scalars().all()

        tables_meta = {r.table_name: r for r in tbl_rows}
        columns_meta: dict[str, dict] = {}
        for r in col_rows:
            columns_meta.setdefault(r.table_name, {})[r.column_name] = r

        print(
            f"[schema_cache] DB metadata loaded  tables={len(tables_meta)}"
            f"  column_rows={len(col_rows)}",
            flush=True,
        )
        return {"tables": tables_meta, "columns": columns_meta}
    except Exception as exc:
        print(f"[schema_cache] ⚠ _load_db_metadata failed (non-fatal): {exc}", flush=True)
        return {"tables": {}, "columns": {}}


def _qualified(table: dict) -> str:
    schema = (table.get("schema") or "").strip()
    name = (table.get("name") or "").strip()
    return f"{schema}.{name}" if schema else name


def _build_relationship_graph(tables: list, qualified_name_map: dict) -> RelationshipGraph:
    graph = RelationshipGraph()
    bare_names = set(qualified_name_map.keys())

    # Pass 1: declared FK relationships (from crawler — only populated on engines that
    # enforce FK constraints, e.g. Postgres.  Empty on Redshift / most data warehouses).
    for table in tables:
        bare_tname = table.get("name", "")
        qualified_tname = qualified_name_map.get(bare_tname, bare_tname)
        for rel in table.get("relationships", []):
            ref = rel.get("references", "")
            fk_col = rel.get("column", "")
            if "." not in ref:
                continue
            ref_bare, ref_col = ref.split(".", 1)
            if ref_bare in bare_names and bare_tname and fk_col:
                qualified_ref = qualified_name_map.get(ref_bare, ref_bare)
                condition = f"{qualified_tname}.{fk_col} = {qualified_ref}.{ref_col}"
                graph.add_edge(qualified_tname, qualified_ref, condition)

    declared_edges = len(graph)

    # Pass 2: heuristic FK inference from shared column names.
    # Critical for Redshift / data warehouses where FKs are declared but unenforced
    # (or not declared at all).  Two rules:
    #   Rule A: underscore-delimited IDs — _id / _key / _sk / _fk / _ref
    #   Rule B: compact IDs without underscore (Bullhorn / legacy style) — ends in "id"
    #           with length > 4 to exclude bare "id" column (which exists in every table
    #           and would create false edges everywhere).  e.g. joborderid, placementid,
    #           clientcorporationid, candidateid all qualify; "id" itself does not.
    # O(n²) on table count but pure-Python set intersection is negligible for ≤500 tables.
    _FK_SUFFIXES = ("_id", "_key", "_sk", "_fk", "_ref")
    tbl_col_index: dict[str, set] = {}
    for table in tables:
        bare_tname = table.get("name", "")
        qualified_tname = qualified_name_map.get(bare_tname, bare_tname)
        cols = {(c.get("name") or "").lower() for c in table.get("columns", [])}
        tbl_col_index[qualified_tname] = cols

    def _is_fk_col(col_name: str) -> bool:
        return (
            any(col_name.endswith(s) for s in _FK_SUFFIXES)
            or (col_name.endswith("id") and len(col_name) > 4)
        )

    # Noise words stripped when deriving the "bare" table name for FK column matching.
    # e.g. "staging.bullhorn_core_placement" → "placement"
    _BARE_NOISE = ("staging", "target", "public", "bullhorn", "classic", "core", "bqp")

    def _bare_table_name(qualified: str) -> str:
        """Strip schema prefix and vendor prefixes to get a short, matchable name."""
        name = qualified.split(".")[-1].lower()
        for noise in _BARE_NOISE:
            name = name.replace(noise + "_", "").replace("_" + noise, "")
        return name.replace("_", "")

    def _best_fk_col(fk_cols: list[str], tbl_a: str, tbl_b: str) -> str:
        """
        Pick the most semantically meaningful shared FK column for a table pair.

        Prefers a column whose name starts with the bare name of one of the tables —
        e.g. 'placementid' over 'clientcorporationid' when one table is
        'bullhorn_core_placement'. Falls back to alphabetically first.

        Without this, 'clientcorporationid' (c < j < p) would always win because it
        sorts first alphabetically, producing wrong join conditions for most pairs.
        """
        bare_a = _bare_table_name(tbl_a)
        bare_b = _bare_table_name(tbl_b)
        for col in sorted(fk_cols):
            if (bare_a and col.startswith(bare_a)) or (bare_b and col.startswith(bare_b)):
                return col
        return sorted(fk_cols)[0]  # alphabetically first as last resort

    q_names = list(tbl_col_index.keys())
    for i, tbl_a in enumerate(q_names):
        for tbl_b in q_names[i + 1:]:
            if graph.get_join_condition(tbl_a, tbl_b):
                continue  # already have a declared edge — keep it
            shared = tbl_col_index[tbl_a] & tbl_col_index[tbl_b]
            fk_shared = sorted(c for c in shared if _is_fk_col(c))
            if fk_shared:
                join_col = _best_fk_col(fk_shared, tbl_a, tbl_b)
                condition = f"{tbl_a}.{join_col} = {tbl_b}.{join_col}"
                graph.add_edge(tbl_a, tbl_b, condition)

    heuristic_edges = len(graph) - declared_edges
    print(
        f"[schema_cache] relationship_graph  declared={declared_edges}"
        f"  heuristic={heuristic_edges}  total={len(graph)}",
        flush=True,
    )
    return graph


async def _build(schema_doc: dict, db_type: str, connection_id: str = "") -> EnrichedSchema:
    tables = schema_doc.get("tables", [])

    qualified_name_map: dict[str, str] = {
        t.get("name", ""): _qualified(t)
        for t in tables
        if t.get("name")
    }
    has_schemas = any("." in q for q in qualified_name_map.values())
    if has_schemas:
        schemas = {q.split(".")[0] for q in qualified_name_map.values() if "." in q}
        print(f"[schema_cache] schema-qualifying table names  schemas={schemas}", flush=True)

    # Load persisted DB metadata (written by metadata_extractor after each crawl).
    # Non-fatal — all enrichments below fall back to LLM paths when db_meta is empty.
    db_meta = await _load_db_metadata(connection_id)
    db_tables = db_meta["tables"]    # {qualified_name: SchemaTableMetadata}
    db_cols   = db_meta["columns"]   # {qualified_name: {col_name: SchemaColumnMetadata}}

    # column_map
    column_map: dict[str, list] = {}
    for table in tables:
        qualified_tname = qualified_name_map.get(table.get("name", ""), table.get("name", ""))
        for col in table.get("columns", []):
            key = col.get("name", "").lower()
            if not key:
                continue
            # Prefer DB business_name as the column description when available
            db_col = db_cols.get(qualified_tname, {}).get(col.get("name", ""))
            description = (
                (db_col.description if db_col and db_col.description else None)
                or col.get("description")
                or ""
            )
            column_map.setdefault(key, []).append({
                "table": qualified_tname,
                "type": col.get("type"),
                "description": description,
            })

    ambiguous_columns = [k for k, v in column_map.items() if len(v) > 1]

    # compact_tables — built once here, reused everywhere.
    # DB metadata enriches each column: richer descriptions + example_values as top_values
    # (so value_sampler skips live DB queries for pre-collected filter values).
    compact_tables = []
    for t in tables:
        qualified_tname = qualified_name_map.get(t.get("name", ""), t.get("name", ""))
        all_cols = t.get("columns", [])
        db_tbl = db_tables.get(qualified_tname)
        tbl_cols_meta = db_cols.get(qualified_tname, {})

        # Table-level: prefer DB description / business_name
        tbl_description = (
            (db_tbl.description if db_tbl and db_tbl.description else None)
            or t.get("description")
            or ""
        )

        enriched_cols = []
        for c in all_cols:
            cname = c.get("name") or ""
            db_col = tbl_cols_meta.get(cname)

            col_desc = (
                (db_col.description if db_col and db_col.description else None)
                or c.get("description")
                or ""
            )

            # Merge stats: start from crawler stats, then inject DB example_values as
            # top_values in the format value_sampler expects — {col_name: value} rows.
            stats = dict(c.get("stats") or {})
            if db_col and db_col.example_values and not stats.get("top_values"):
                stats["top_values"] = [{cname: v} for v in db_col.example_values]

            enriched_cols.append({
                "name": cname,
                "type": c.get("type"),
                "description": col_desc[:250],
                "semantic_type": db_col.semantic_type if db_col else None,
                "stats": stats or None,
            })

        compact_tables.append({
            "name": qualified_tname,
            "description": tbl_description[:350],
            "row_count": t.get("row_count"),
            "columns": enriched_cols,
            "all_column_names": [c.get("name") for c in all_cols if c.get("name")],
            "relationships": t.get("relationships", [])[:10],
        })

    # relationship_graph — Pass 1 (declared) + Pass 2 (heuristic)
    relationship_graph = _build_relationship_graph(tables, qualified_name_map)

    # Pass 3: inject confirmed FKs from DB metadata (overrides / supplements heuristics).
    # These were SQL-validated by metadata_extractor Phase B at crawl time.
    confirmed_fk_count = 0
    for tbl_name, cols_meta in db_cols.items():
        for col_name, col_meta in cols_meta.items():
            if (
                col_meta.fk_confirmed
                and col_meta.fk_target_table
                and col_meta.fk_target_column
            ):
                condition = (
                    f"{tbl_name}.{col_name}"
                    f" = {col_meta.fk_target_table}.{col_meta.fk_target_column}"
                )
                relationship_graph.add_edge(tbl_name, col_meta.fk_target_table, condition)
                confirmed_fk_count += 1

    if confirmed_fk_count:
        print(
            f"[schema_cache] Pass 3: injected {confirmed_fk_count} confirmed FK edge(s)"
            f" from DB metadata",
            flush=True,
        )

    # Table semantics — build from DB metadata first, then run LLM only for uncovered tables.
    db_covered_semantics: dict = {}
    for qualified_tname, db_tbl in db_tables.items():
        db_covered_semantics[qualified_tname] = {
            "purpose": db_tbl.description or "",
            "grain": db_tbl.grain or "",
            "key_metric_cols": db_tbl.key_metric_cols or [],
            "key_dimension_cols": db_tbl.key_dimension_cols or [],
            "key_date_cols": db_tbl.key_date_cols or [],
            "use_for": db_tbl.use_for or [],
            "never_use_for": db_tbl.never_use_for or [],
            "is_fact_table": db_tbl.is_fact_table,
            "business_name": db_tbl.business_name or "",
        }

    tables_needing_llm = [
        ct for ct in compact_tables
        if ct["name"] not in db_covered_semantics
    ]
    if tables_needing_llm:
        print(
            f"[schema_cache] table_semantics: {len(db_covered_semantics)} from DB,"
            f" {len(tables_needing_llm)} need LLM",
            flush=True,
        )
    else:
        print(
            f"[schema_cache] table_semantics: all {len(db_covered_semantics)} table(s)"
            f" covered by DB metadata — skipping LLM call",
            flush=True,
        )

    # LLM jobs in parallel: disambiguation always runs; table semantics only for uncovered tables
    if tables_needing_llm:
        disambiguation, llm_semantics = await asyncio.gather(
            _disambiguate_columns(ambiguous_columns, column_map, tables),
            _analyze_table_semantics(tables_needing_llm, db_type),
        )
        table_semantics = {**db_covered_semantics, **llm_semantics}
    else:
        disambiguation = await _disambiguate_columns(ambiguous_columns, column_map, tables)
        table_semantics = db_covered_semantics

    # ── NL2SQL intelligence indexes ───────────────────────────────────────────
    # Build concept_index and entity_columns in parallel with everything else.
    # concept_index uses both a heuristic pass (instant) and an LLM-enhanced pass
    # (runs as a 3rd parallel task during cold build — zero extra wall-clock time).
    heuristic_concept_index = _build_concept_index_heuristic(compact_tables, table_semantics)
    heuristic_entity_columns = _build_entity_columns(compact_tables)

    llm_concept_index: dict = {}
    try:
        llm_concept_index = await _build_concept_index_llm(compact_tables, db_type)
    except Exception as exc:
        print(f"[schema_cache] ⚠ LLM concept-index build failed (non-fatal): {exc}", flush=True)

    # Merge: LLM entries take precedence over heuristic entries when present
    concept_index = {**heuristic_concept_index}
    for term, entries in llm_concept_index.items():
        if term in concept_index:
            # Deduplicate by (table, column), LLM wins on overlap
            existing = {(e["table"], e["column"]): e for e in concept_index[term]}
            for e in entries:
                existing[(e["table"], e["column"])] = e
            concept_index[term] = sorted(existing.values(), key=lambda x: x.get("score", 0), reverse=True)
        else:
            concept_index[term] = entries

    print(
        f"[schema_cache] concept_index built  terms={len(concept_index)}"
        f"  entity_types={list(heuristic_entity_columns.keys())}",
        flush=True,
    )

    # ── TF-IDF index for Graph RAG retrieval ─────────────────────────────────
    tfidf_index = _build_tfidf_index(compact_tables, table_semantics)

    return EnrichedSchema(
        schema_doc=schema_doc,
        db_type=db_type,
        column_map=column_map,
        ambiguous_columns=ambiguous_columns,
        disambiguation=disambiguation,
        table_semantics=table_semantics,
        compact_tables=compact_tables,
        relationship_graph=relationship_graph,
        concept_index=concept_index,
        entity_columns=heuristic_entity_columns,
        tfidf_index=tfidf_index,
    )


def _parse_disambiguation_response(raw: str) -> dict:
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*\n?", "", clean)
        clean = re.sub(r"\n?```\s*$", "", clean)
    clean = clean.strip()

    try:
        result = json.loads(clean)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    last_bracket = clean.rfind("]")
    if last_bracket != -1:
        candidate = clean[: last_bracket + 1].rstrip().rstrip(",") + "\n}"
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                print(
                    f"[schema_cache] ⚠ disambiguation: recovered partial JSON ({len(result)} columns)",
                    flush=True,
                )
                return result
        except json.JSONDecodeError:
            pass

    result: dict = {}
    for m in re.finditer(
        r'"(\w+)"\s*:\s*(\[(?:\s*\{[^{}]+\}\s*,?\s*)+\])',
        clean,
        re.DOTALL,
    ):
        col_name = m.group(1)
        try:
            arr = json.loads(m.group(2))
            if isinstance(arr, list) and arr:
                result[col_name] = arr
        except json.JSONDecodeError:
            pass

    if result:
        print(
            f"[schema_cache] ⚠ disambiguation: regex-recovered {len(result)} column entries",
            flush=True,
        )
    return result


async def _disambiguate_columns(
    ambiguous_columns: list,
    column_map: dict,
    tables: list,
) -> dict:
    if not ambiguous_columns:
        return {}

    # Process top 60 most-ambiguous columns, batched at 15 per LLM call to avoid
    # truncation. The single-call approach with 30 columns at max_tokens=10000 was
    # being cut off at ~22 columns — batching + parallel calls is both faster and
    # more complete.
    ranked = sorted(ambiguous_columns, key=lambda c: len(column_map[c]), reverse=True)[:60]

    table_cols: dict[str, list] = {}
    for t in tables:
        table_cols[t.get("name", "")] = [c.get("name") for c in t.get("columns", [])]

    def _build_batch_prompt(batch: list[str]) -> str:
        entries = []
        for col_name in batch:
            occurrences = column_map[col_name]
            occ_text = []
            for occ in occurrences:
                tname = occ["table"]
                sibling_cols = table_cols.get(tname, [])[:15]
                occ_text.append(
                    f"  table={tname}  type={occ['type']}  "
                    f"other_cols=[{', '.join(sibling_cols)}]  "
                    f"col_desc={occ['description'][:60]}"
                )
            entries.append(f"column: {col_name}\n" + "\n".join(occ_text))

        return f"""The following column names appear in multiple database tables.
For each column, infer the DIFFERENT semantic meaning in each table context based on the table name and sibling columns.

{chr(10).join(entries)}

Return ONLY valid JSON:
{{
  "column_name": [
    {{"table": "table_a", "meaning": "what this column represents in table_a", "use_when": "which chart/metric scenario"}},
    {{"table": "table_b", "meaning": "what this column represents in table_b", "use_when": "which chart/metric scenario"}}
  ]
}}

Be specific and concise. Focus on how to tell them apart when generating SQL for a chart."""

    async def _call_one_batch(batch: list[str]) -> dict:
        prompt = _build_batch_prompt(batch)
        try:
            raw = await bedrock_invoke(
                model_id=_CACHE_MODEL,
                system_prompt="You are a database semantic analyst. Return only valid JSON.",
                user_message=prompt,
                temperature=0.0,
                max_tokens=8000,
            )
            result = _parse_disambiguation_response(raw.strip())
            return result or {}
        except Exception as e:
            print(f"[schema_cache] ⚠ disambiguation batch failed: {e}", flush=True)
            return {}

    # Split into batches of 15 and run all batches in parallel
    _BATCH_SIZE = 15
    batches = [ranked[i: i + _BATCH_SIZE] for i in range(0, len(ranked), _BATCH_SIZE)]
    batch_results = await asyncio.gather(*[_call_one_batch(b) for b in batches])

    # Merge results — first batch wins on key collision (most-ambiguous columns first)
    merged: dict = {}
    for batch_result in batch_results:
        for k, v in batch_result.items():
            if k not in merged:
                merged[k] = v

    if merged:
        print(
            f"[schema_cache] disambiguation: resolved {len(merged)}/{len(ranked)} columns "
            f"across {len(batches)} batch(es)",
            flush=True,
        )
    return merged


async def _analyze_table_semantics(compact_tables: list, db_type: str) -> dict:
    if not compact_tables:
        return {}

    results: dict = {}
    for batch_start in range(0, len(compact_tables), 15):
        batch = compact_tables[batch_start: batch_start + 15]
        batch_json = json.dumps(batch, indent=2)

        prompt = f"""Analyze these database tables (db_type={db_type}) and for each table return:
- purpose: one sentence describing what this table stores
- grain: what one row represents (e.g. "one placement event per candidate per job")
- key_metric_cols: column names best suited as numeric metrics to aggregate (SUM/COUNT/AVG)
- key_dimension_cols: column names best suited as categorical group-by dimensions
- key_date_cols: column names that are dates/timestamps suitable for time-series

Tables:
{batch_json}

Return ONLY valid JSON:
{{
  "table_name": {{
    "purpose": "...",
    "grain": "...",
    "key_metric_cols": ["col1", "col2"],
    "key_dimension_cols": ["col3", "col4"],
    "key_date_cols": ["col5"]
  }}
}}"""

        try:
            raw = await bedrock_invoke(
                model_id=_CACHE_MODEL,
                system_prompt="You are a database schema analyst. Return only valid JSON.",
                user_message=prompt,
                temperature=0.0,
                max_tokens=10000,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
            try:
                batch_result = json.loads(raw)
            except json.JSONDecodeError:
                last_brace = raw.rfind("}")
                if last_brace != -1:
                    raw = raw[: last_brace + 1].rstrip().rstrip(",") + "\n}"
                batch_result = json.loads(raw)
            if isinstance(batch_result, dict):
                results.update(batch_result)
        except Exception as e:
            print(f"[schema_cache] ⚠ table semantics batch failed: {e}", flush=True)

    return results


# ── TF-IDF Index Builder (for Graph RAG retriever) ────────────────────────────

def _build_tfidf_index(compact_tables: list, table_semantics: dict) -> dict:
    """
    Build a lightweight TF-IDF index over all table+column text blobs.
    Pure Python — no numpy/sklearn. Runs at cache-build time (not query time).

    Returns:
      {
        "idf": {term: float},
        "tables": {table_name: {"tfidf_vec": {term: float}}}
      }
    """
    import math as _math
    import re as _re

    def _tok(text: str) -> list:
        return _re.findall(r"[a-z0-9]+", text.lower())

    def _table_blob(t: dict, sem: dict) -> str:
        tn = t.get("name", "")
        s = sem.get(tn, {})
        parts: list[str] = []
        bare = tn.split(".")[-1].replace("_", " ")
        parts += [bare, bare, bare]
        if t.get("description"):
            parts.append(t["description"])
        if s.get("business_name"):
            parts += [s["business_name"], s["business_name"]]
        if s.get("purpose"):
            parts.append(s["purpose"])
        for u in (s.get("use_for") or []):
            parts.append(u)
        for col in (s.get("key_metric_cols") or []):
            parts += [col.replace("_", " ")] * 2
        for col in (s.get("key_dimension_cols") or []):
            parts += [col.replace("_", " ")] * 2
        for col in (s.get("key_date_cols") or []):
            parts.append(col.replace("_", " "))
        for c in t.get("columns", []):
            cname = c.get("name", "")
            cdesc = c.get("description", "")
            stype = c.get("semantic_type", "")
            parts.append(cname.replace("_", " "))
            if cdesc:
                parts.append(cdesc[:150])
            if stype in ("metric", "dimension"):
                parts.append(cname.replace("_", " "))
        return " ".join(parts)

    if not compact_tables:
        return {}

    # Build per-table term frequencies
    table_tfs: dict[str, dict] = {}
    for t in compact_tables:
        tn = t.get("name", "")
        if not tn:
            continue
        tokens = _tok(_table_blob(t, table_semantics))
        if not tokens:
            continue
        freq: dict[str, int] = {}
        for tok in tokens:
            freq[tok] = freq.get(tok, 0) + 1
        n = len(tokens)
        table_tfs[tn] = {tok: c / n for tok, c in freq.items()}

    if not table_tfs:
        return {}

    # IDF: log((N + 1) / (df + 1)) + 1
    N = len(table_tfs)
    df: dict[str, int] = {}
    for tfs in table_tfs.values():
        for tok in tfs:
            df[tok] = df.get(tok, 0) + 1
    idf = {tok: _math.log((N + 1) / (d + 1)) + 1.0 for tok, d in df.items()}

    # TF-IDF vectors per table
    tables_out: dict[str, dict] = {}
    for tn, tfs in table_tfs.items():
        tfidf_vec = {tok: tf * idf.get(tok, 1.0) for tok, tf in tfs.items()}
        tables_out[tn] = {"tfidf_vec": tfidf_vec}

    return {"idf": idf, "tables": tables_out}


# ── NL2SQL Concept-Index & Entity-Column Builders ─────────────────────────────

def _build_concept_index_heuristic(
    compact_tables: list[dict],
    table_semantics: dict,
) -> dict:
    """
    Build concept_index without any LLM calls.

    Maps business-vocabulary terms → [{table, column, score, context}] by
    mining column names, descriptions, semantic_type tags, and the
    key_metric / key_dimension / key_date arrays from table_semantics.
    """
    index: dict[str, list[dict]] = {}

    def _add(term: str, table: str, column: str, score: float, context: str = "") -> None:
        term = term.lower().strip()
        if not term or len(term) < 2:
            return
        entry = {"table": table, "column": column, "score": score, "context": context}
        if term not in index:
            index[term] = []
        # Avoid exact duplicates
        if not any(e["table"] == table and e["column"] == column for e in index[term]):
            index[term].append(entry)

    for tbl in compact_tables:
        tname: str = tbl.get("name", "")
        sem: dict = table_semantics.get(tname, {})
        key_metrics: list[str] = sem.get("key_metric_cols", [])
        key_dims: list[str] = sem.get("key_dimension_cols", [])
        key_dates: list[str] = sem.get("key_date_cols", [])

        for col in tbl.get("columns", []):
            cname: str = col.get("name", "")
            ctype: str = col.get("type", "").lower()
            desc: str = col.get("description", "") or ""
            stype: str = col.get("semantic_type", "") or ""

            # High-score: column is a known key-metric/dim/date
            if cname in key_metrics:
                # Add both the bare name and a human-readable "table metric" alias
                _add(cname.replace("_", " "), tname, cname, 0.95, "key_metric")
                pretty = f"{tname.replace('_', ' ')} {cname.replace('_', ' ')}"
                _add(pretty, tname, cname, 0.90, "key_metric")
            elif cname in key_dims:
                _add(cname.replace("_", " "), tname, cname, 0.85, "key_dimension")
            elif cname in key_dates:
                _add(cname.replace("_", " "), tname, cname, 0.80, "key_date")
            else:
                _add(cname.replace("_", " "), tname, cname, 0.60, "column_name")

            # Semantic-type tags → concept aliases
            if stype:
                for tag in stype.replace(",", " ").split():
                    _add(tag, tname, cname, 0.75, f"semantic_type:{stype}")

            # Description tokens → concept terms
            if desc:
                for token in re.findall(r"[a-zA-Z_]{3,}", desc):
                    _add(token.replace("_", " ").lower(), tname, cname, 0.55, "description")

            # Table-level concept: "table_name column_name" combined phrase
            combined = f"{tname.replace('_', ' ')} {cname.replace('_', ' ')}"
            _add(combined, tname, cname, 0.70, "combined")

        # Table-name itself → all key columns
        tname_term = tname.replace("_", " ")
        for cname in key_metrics[:3]:
            _add(tname_term, tname, cname, 0.65, "table_key_metric")

    # Sort each term's list by score descending
    for term in index:
        index[term].sort(key=lambda e: e["score"], reverse=True)

    return index


def _build_entity_columns(compact_tables: list[dict]) -> dict:
    """
    Build entity_columns: {entity_type -> [{table, column, is_primary, sample_values}]}.

    Pure heuristic — no LLM.  Finds "name" / "id" / identifier columns and infers
    the entity type from the table name using a keyword vocabulary.
    """
    _ENTITY_TABLE_KEYWORDS: dict[str, list[str]] = {
        "company":  ["company", "client", "account", "customer", "organization",
                     "employer", "agency", "vendor", "partner"],
        "person":   ["person", "employee", "candidate", "user", "contact", "staff",
                     "worker", "recruiter", "manager", "applicant", "member"],
        "job":      ["job", "role", "position", "vacancy", "opening", "order",
                     "placement", "requisition"],
        "product":  ["product", "item", "sku", "service", "offering", "plan",
                     "package", "subscription"],
        "location": ["location", "office", "city", "region", "territory", "address",
                     "site", "branch"],
        "invoice":  ["invoice", "bill", "payment", "transaction", "charge", "fee"],
        "timesheet":["timesheet", "timeentry", "time_log", "attendance", "hour"],
    }

    # Column-name patterns that suggest a "name" / identifier column
    _NAME_COL_PATTERNS = re.compile(
        r"(^name$|_name$|^title$|_title$|^label$|^display_name$|^full_name$"
        r"|^description$|^desc$|^caption$)",
        re.IGNORECASE,
    )
    _ID_COL_PATTERNS = re.compile(r"(^id$|_id$|^uuid$|^code$|^key$)", re.IGNORECASE)

    result: dict[str, list[dict]] = {}

    for tbl in compact_tables:
        tname: str = tbl.get("name", "").lower()
        cols: list[dict] = tbl.get("columns", [])

        # Detect entity type from table name
        entity_type: str | None = None
        for etype, keywords in _ENTITY_TABLE_KEYWORDS.items():
            if any(kw in tname for kw in keywords):
                entity_type = etype
                break
        if entity_type is None:
            continue

        # Find name columns (high priority) then id columns (lower priority)
        name_cols = [c for c in cols if _NAME_COL_PATTERNS.match(c.get("name", ""))]
        id_cols = [c for c in cols if _ID_COL_PATTERNS.match(c.get("name", ""))]

        for col in name_cols:
            entry = {
                "table": tbl.get("name", ""),
                "column": col["name"],
                "is_primary": True,
                "sample_values": col.get("sample_values") or [],
            }
            result.setdefault(entity_type, []).append(entry)

        for col in id_cols:
            entry = {
                "table": tbl.get("name", ""),
                "column": col["name"],
                "is_primary": False,
                "sample_values": col.get("sample_values") or [],
            }
            result.setdefault(entity_type, []).append(entry)

    return result


_CONCEPT_INDEX_LLM_SYSTEM = (
    "You are a BI metadata analyst. Given a database table's columns, return a "
    "JSON object mapping business-vocabulary terms to the column(s) that satisfy "
    "them. Return ONLY valid JSON — no prose, no markdown fences."
)

_CONCEPT_INDEX_LLM_TEMPLATE = """Table: {table_name}
Purpose: {purpose}
Columns: {columns_json}

Return a JSON object where each key is a SHORT business term (1-4 words, lowercase)
that a business user might say, and each value is a list of objects:
  {{"column": "<col_name>", "score": <0.5-1.0>, "context": "<brief why>"}}

Cover at minimum:
- Every numeric column (revenue, count, amount, rate …)
- Every date column (date, period, created_at …)
- Key dimension/category columns

Example output format:
{{
  "revenue": [{{"column": "total_amount", "score": 0.95, "context": "billing revenue"}}],
  "date": [{{"column": "invoice_date", "score": 0.9, "context": "when invoiced"}}]
}}"""


async def _build_concept_index_llm(compact_tables: list[dict], db_type: str) -> dict:
    """
    LLM-enhanced concept index.  Runs one small Haiku call per table (batched
    where possible) and returns the merged result.  Failures are tolerated
    per-table; any surviving entries are merged with the heuristic index.
    """
    from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL  # noqa: PLC0415

    result: dict[str, list[dict]] = {}

    async def _call_one(tbl: dict) -> None:
        tname = tbl.get("name", "")
        cols_summary = [
            {"name": c["name"], "type": c.get("type", ""), "desc": c.get("description", "")}
            for c in tbl.get("columns", [])[:30]  # cap at 30 cols per table
        ]
        prompt = _CONCEPT_INDEX_LLM_TEMPLATE.format(
            table_name=tname,
            purpose=tbl.get("description", ""),
            columns_json=json.dumps(cols_summary, ensure_ascii=False),
        )
        try:
            raw = await asyncio.wait_for(
                bedrock_invoke(
                    model_id=BEDROCK_HAIKU_MODEL,
                    system_prompt=_CONCEPT_INDEX_LLM_SYSTEM,
                    user_message=prompt,
                    temperature=0.0,
                    max_tokens=1024,
                ),
                timeout=12.0,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw).strip()
            data = json.loads(raw)
            if not isinstance(data, dict):
                return
            for term, entries in data.items():
                term_lower = term.lower().strip()
                if not term_lower or not isinstance(entries, list):
                    continue
                for e in entries:
                    if not isinstance(e, dict) or not e.get("column"):
                        continue
                    entry = {
                        "table": tname,
                        "column": str(e["column"]),
                        "score": float(e.get("score", 0.75)),
                        "context": str(e.get("context", "")),
                    }
                    result.setdefault(term_lower, []).append(entry)
        except Exception as exc:
            print(f"[schema_cache] ⚠ concept LLM failed for {tname}: {exc}", flush=True)

    # Run at most 8 tables concurrently to avoid hammering Bedrock
    semaphore = asyncio.Semaphore(8)

    async def _bounded(tbl: dict) -> None:
        async with semaphore:
            await _call_one(tbl)

    await asyncio.gather(*[_bounded(t) for t in compact_tables])

    # Sort entries per term by score desc
    for term in result:
        result[term].sort(key=lambda e: e["score"], reverse=True)

    return result
