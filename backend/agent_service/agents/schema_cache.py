"""
Schema Cache — built once per connection, held in process memory.

Four enrichments on top of the raw schema:
  1. column_map        — {col_name: [{table, type, description}]} for every column across all tables
  2. disambiguation    — for columns that appear in 2+ tables, LLM-inferred meaning per occurrence
  3. table_semantics   — LLM-inferred purpose, grain, key metric/dimension/date cols per table
  4. relationship_graph — FK adjacency graph built from table.relationships; validates join paths

LLM jobs 2+3 run in parallel on first build. Graph (4) is pure Python — instant.
Cache is keyed by connection_id so different projects/connections stay isolated.
"""
import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Optional
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_CACHE_MODEL = BEDROCK_SONNET_MODEL

# ── in-process cache ──────────────────────────────────────────────────────────
_store: dict[str, "EnrichedSchema"] = {}


@dataclass
class RelationshipGraph:
    """
    Bidirectional FK adjacency graph built from schema table.relationships.
    edges: {table_a: {table_b: "table_a.fk_col = table_b.pk_col"}}
    Both directions are stored so path_exists(a, b) == path_exists(b, a).
    """
    edges: dict = field(default_factory=dict)

    def add_edge(self, from_table: str, to_table: str, condition: str) -> None:
        self.edges.setdefault(from_table, {})[to_table] = condition
        self.edges.setdefault(to_table, {})[from_table] = condition  # bidirectional

    def path_exists(self, table_a: str, table_b: str, max_hops: int = 2) -> bool:
        """BFS up to max_hops to find any path between table_a and table_b."""
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
        """Return the direct FK join condition string, or None if no direct edge."""
        return self.edges.get(table_a, {}).get(table_b)

    def __len__(self) -> int:
        return sum(len(v) for v in self.edges.values()) // 2  # each edge stored twice


@dataclass
class EnrichedSchema:
    schema_doc: dict
    db_type: str

    # {col_name_lower: [{table, type, description}]}
    column_map: dict = field(default_factory=dict)

    # column names that appear in 2+ tables
    ambiguous_columns: list = field(default_factory=list)

    # {col_name: [{table, meaning, use_when}]}
    disambiguation: dict = field(default_factory=dict)

    # {table_name: {purpose, grain, key_metric_cols, key_dimension_cols, key_date_cols}}
    table_semantics: dict = field(default_factory=dict)

    # pre-built compact table list for LLM prompts — avoids rebuilding per agent call
    compact_tables: list = field(default_factory=list)

    # FK adjacency graph — validates join paths proposed by schema_matcher
    relationship_graph: RelationshipGraph = field(default_factory=RelationshipGraph)

    def get_disambiguation_text(self) -> str:
        """Render the disambiguation map as a readable block for LLM prompts."""
        if not self.disambiguation:
            return ""
        lines = ["COLUMN DISAMBIGUATION (same column name, different meanings per table):"]
        for col_name, occurrences in self.disambiguation.items():
            lines.append(f"  • {col_name}:")
            for occ in occurrences:
                lines.append(f"      [{occ['table']}] {occ.get('meaning', '')} — use when: {occ.get('use_when', '')}")
        return "\n".join(lines)

    def get_table_semantics_text(self, table_names: Optional[list] = None) -> str:
        """Render table semantics as a readable block for a subset of tables."""
        lines = ["TABLE SEMANTICS:"]
        for tname, sem in self.table_semantics.items():
            if table_names and tname not in table_names:
                continue
            lines.append(f"  [{tname}] {sem.get('purpose', '')} | grain: {sem.get('grain', '')} | "
                         f"metrics: {sem.get('key_metric_cols', [])} | "
                         f"dims: {sem.get('key_dimension_cols', [])} | "
                         f"dates: {sem.get('key_date_cols', [])}")
        return "\n".join(lines)


async def get_or_build(connection_id: str, schema_doc: dict, db_type: str) -> EnrichedSchema:
    """Return cached EnrichedSchema for this connection, or build and cache it."""
    if connection_id in _store:
        print(f"[schema_cache] ✓ cache hit  connection={connection_id}", flush=True)
        return _store[connection_id]

    print(f"[schema_cache] building enriched schema  connection={connection_id}  tables={len(schema_doc.get('tables', []))}", flush=True)
    enriched = await _build(schema_doc, db_type)
    _store[connection_id] = enriched
    print(f"[schema_cache] ✓ built  ambiguous_cols={len(enriched.ambiguous_columns)}  tables_analysed={len(enriched.table_semantics)}", flush=True)
    return enriched


def invalidate(connection_id: str) -> None:
    """Evict cache for this connection (call after schema re-crawl)."""
    _store.pop(connection_id, None)


def _build_relationship_graph(tables: list, qualified_name_map: dict) -> RelationshipGraph:
    """
    Extract FK relationships from the schema doc and build a bidirectional adjacency graph.
    Reads each table's 'relationships' list, which has the structure:
      [{"column": "candidate_id", "references": "candidates.id"}]
    Uses schema-qualified names (e.g. "navikenz.candidates") as graph node keys
    so that path_exists() and get_join_condition() work when callers use qualified names.
    Pure Python — no LLM call, runs instantly.
    """
    graph = RelationshipGraph()
    bare_names = set(qualified_name_map.keys())

    for table in tables:
        bare_tname = table.get("name", "")
        qualified_tname = qualified_name_map.get(bare_tname, bare_tname)
        for rel in table.get("relationships", []):
            ref = rel.get("references", "")   # e.g. "candidates.id"
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


# ── builder ───────────────────────────────────────────────────────────────────

def _qualified(table: dict) -> str:
    """Return schema-qualified table name: 'schema.table' or just 'table' if no schema."""
    schema = (table.get("schema") or "").strip()
    name = (table.get("name") or "").strip()
    return f"{schema}.{name}" if schema else name


async def _build(schema_doc: dict, db_type: str) -> EnrichedSchema:
    tables = schema_doc.get("tables", [])

    # Pre-build bare→qualified lookup so all structures use consistent names.
    # Qualified name: "schema.table_name" when schema is present, else just "table_name".
    qualified_name_map: dict[str, str] = {
        t.get("name", ""): _qualified(t)
        for t in tables
        if t.get("name")
    }
    has_schemas = any("." in q for q in qualified_name_map.values())
    if has_schemas:
        schemas = {q.split(".")[0] for q in qualified_name_map.values() if "." in q}
        print(f"[schema_cache] schema-qualifying table names  schemas={schemas}", flush=True)

    # Step 1: column_map — pure Python, instant; uses qualified table names
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

    # Step 2: pre-build compact table list once; uses qualified names so
    # the schema_matcher and SQL generation agents produce fully-qualified SQL.
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

    # Step 3: Relationship graph — pure Python, instant (no LLM needed)
    relationship_graph = _build_relationship_graph(tables, qualified_name_map)

    # Step 4: LLM jobs in parallel (disambiguation + table semantics)
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
    """
    Parse the LLM disambiguation response with graceful recovery for truncated output.

    Strategy:
      1. Strip markdown fences.
      2. Fast path: valid JSON → return immediately.
      3. Partial-close recovery: walk backwards to the last complete array entry
         (`]` that closes a column list), append `}` and try again.
      4. Regex extraction: pull out each complete `"col": [{...}]` entry
         individually — works even when the outer object is never closed.
    """
    clean = raw.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*\n?", "", clean)
        clean = re.sub(r"\n?```\s*$", "", clean)
    clean = clean.strip()

    # 1. Fast path
    try:
        result = json.loads(clean)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 2. Partial-close: find the last ']' that closes a top-level array value,
    #    then try closing the outer object.
    last_bracket = clean.rfind("]")
    if last_bracket != -1:
        candidate = clean[: last_bracket + 1].rstrip().rstrip(",") + "\n}"
        try:
            result = json.loads(candidate)
            if isinstance(result, dict):
                print(
                    f"[schema_cache] ⚠ disambiguation: recovered partial JSON "
                    f"({len(result)} columns)",
                    flush=True,
                )
                return result
        except json.JSONDecodeError:
            pass

    # 3. Regex extraction: match each complete "col_name": [{flat objects}] entry.
    #    Inner objects are always flat {key: val, ...} — no nested braces.
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
    """
    For each column that appears in 2+ tables, ask the LLM to describe the
    different semantic meanings based on table context.
    """
    if not ambiguous_columns:
        return {}

    # Cap at top 30 most ambiguous (most tables) to keep prompt size reasonable
    ranked = sorted(ambiguous_columns, key=lambda c: len(column_map[c]), reverse=True)[:30]

    # Build a table-name → column-list index for context
    table_cols: dict[str, list] = {}
    for t in tables:
        table_cols[t.get("name", "")] = [c.get("name") for c in t.get("columns", [])[:20]]

    entries = []
    for col_name in ranked:
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

    prompt = f"""The following column names appear in multiple database tables.
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

    try:
        raw = await bedrock_invoke(
            model_id=_CACHE_MODEL,
            system_prompt="You are a database semantic analyst. Return only valid JSON.",
            user_message=prompt,
            temperature=0.0,
            max_tokens=30000,
        )
        raw = raw.strip()
        result = _parse_disambiguation_response(raw)
        if result:
            return result
    except Exception as e:
        print(f"[schema_cache] ⚠ disambiguation failed: {e}", flush=True)
    return {}


async def _analyze_table_semantics(compact_tables: list, db_type: str) -> dict:
    """
    For every table, infer: purpose, data grain, and which columns are
    metrics, dimensions, and dates.  Returned as {table_name: {...}}.
    """
    if not compact_tables:
        return {}

    # Batch in groups of 15 to avoid huge prompts
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
                max_tokens=30000,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
                raw = re.sub(r"\n?```\s*$", "", raw)
            # Partial-close recovery: if truncated, try closing the outer object
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
