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
from dataclasses import dataclass, field
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_CACHE_MODEL = BEDROCK_SONNET_MODEL

SCHEMA_CACHE_TTL = int(os.getenv("SCHEMA_CACHE_TTL", "86400"))   # 24 h default
_REDIS_KEY_PREFIX = "schema_cache"

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
            lines.append(
                f"  [{tname}] {sem.get('purpose', '')} | grain: {sem.get('grain', '')} | "
                f"metrics: {sem.get('key_metric_cols', [])} | "
                f"dims: {sem.get('key_dimension_cols', [])} | "
                f"dates: {sem.get('key_date_cols', [])}"
            )
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

    # L2: Redis
    schema_hash = compute_schema_hash(schema_doc)
    redis_key = f"{_REDIS_KEY_PREFIX}:{connection_id}:{schema_hash}"
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
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

    # L3: cold build
    print(
        f"[schema_cache] building enriched schema  connection={connection_id}"
        f"  tables={len(schema_doc.get('tables', []))}",
        flush=True,
    )
    enriched = await _build(schema_doc, db_type)
    _store[connection_id] = enriched

    # Persist to Redis
    try:
        from shared.redis_client import get_redis
        redis = await get_redis()
        if redis is not None:
            serialized = _serialize_enriched(enriched)
            await redis.setex(redis_key, SCHEMA_CACHE_TTL, serialized)
            print(
                f"[schema_cache] ✓ stored in Redis  connection={connection_id}"
                f"  hash={schema_hash}  ttl={SCHEMA_CACHE_TTL}s  size={len(serialized)//1024}KB",
                flush=True,
            )
    except Exception as _re:
        print(f"[schema_cache] ⚠ Redis write failed (non-fatal): {_re}", flush=True)

    print(
        f"[schema_cache] ✓ built  ambiguous_cols={len(enriched.ambiguous_columns)}"
        f"  tables_analysed={len(enriched.table_semantics)}",
        flush=True,
    )
    return enriched


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


# ── Internal builder ──────────────────────────────────────────────────────────

def _qualified(table: dict) -> str:
    schema = (table.get("schema") or "").strip()
    name = (table.get("name") or "").strip()
    return f"{schema}.{name}" if schema else name


def _build_relationship_graph(tables: list, qualified_name_map: dict) -> RelationshipGraph:
    graph = RelationshipGraph()
    bare_names = set(qualified_name_map.keys())

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

    print(f"[schema_cache] relationship_graph built  edges={len(graph)}", flush=True)
    return graph


async def _build(schema_doc: dict, db_type: str) -> EnrichedSchema:
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

    # column_map
    column_map: dict[str, list] = {}
    for table in tables:
        qualified_tname = qualified_name_map.get(table.get("name", ""), table.get("name", ""))
        for col in table.get("columns", []):
            key = col.get("name", "").lower()
            if not key:
                continue
            column_map.setdefault(key, []).append({
                "table": qualified_tname,
                "type": col.get("type"),
                "description": col.get("description") or "",
            })

    ambiguous_columns = [k for k, v in column_map.items() if len(v) > 1]

    # compact_tables — built once here, reused everywhere
    compact_tables = []
    for t in tables:
        qualified_tname = qualified_name_map.get(t.get("name", ""), t.get("name", ""))
        compact_tables.append({
            "name": qualified_tname,
            "description": (t.get("description") or "")[:150],
            "row_count": t.get("row_count"),
            "columns": [
                {
                    "name": c.get("name"),
                    "type": c.get("type"),
                    "description": (c.get("description") or "")[:80],
                }
                for c in t.get("columns", [])[:30]
            ],
            "relationships": t.get("relationships", [])[:6],
        })

    # relationship_graph — pure Python
    relationship_graph = _build_relationship_graph(tables, qualified_name_map)

    # LLM jobs in parallel
    disambiguation, table_semantics = await asyncio.gather(
        _disambiguate_columns(ambiguous_columns, column_map, tables),
        _analyze_table_semantics(compact_tables, db_type),
    )

    return EnrichedSchema(
        schema_doc=schema_doc,
        db_type=db_type,
        column_map=column_map,
        ambiguous_columns=ambiguous_columns,
        disambiguation=disambiguation,
        table_semantics=table_semantics,
        compact_tables=compact_tables,
        relationship_graph=relationship_graph,
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
        table_cols[t.get("name", "")] = [c.get("name") for c in t.get("columns", [])[:20]]

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
