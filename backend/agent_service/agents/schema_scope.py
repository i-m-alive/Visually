"""Shared schema-scoping helpers — pure functions, no LLM, no I/O.

Given an EnrichedSchema (from schema_cache), a seed set of table names, and an FK
hop count, produce a compact two-tier schema prompt block:
  • seed tables       → FULL detail (columns, types, sample values, semantics)
  • neighbour tables  → LIGHTWEIGHT (name + purpose + one join path)

Used by the Canvas Assistant (ChatAgent) to limit the prompt to a builder-chosen
set of tables + their N-hop FK neighbours. The Report Copilot
(IntelligenceChatAgent) currently carries an equivalent private copy; both can
converge onto this module later.
"""
from typing import Optional


def clip(text: str, limit: int) -> str:
    """Trim text to `limit` chars (limit<=0 disables trimming)."""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def is_categorical_col(c: dict) -> bool:
    """Sample values help only for low-cardinality / categorical columns; they are
    noise for ids, numerics, dates, and free text."""
    sem = (c.get("semantic_type") or "").lower()
    if sem in {"dimension", "category", "categorical", "enum", "status", "boolean"}:
        return True
    if sem in {"metric", "measure", "id", "identifier", "date", "datetime", "timestamp"}:
        return False
    ctype = (c.get("type") or "").lower()
    cname = (c.get("name") or "").lower()
    if cname == "id" or cname.endswith("_id") or cname.endswith("id"):
        return False
    if any(k in ctype for k in ("int", "numeric", "decimal", "float", "double", "real",
                                "money", "serial", "date", "time", "timestamp")):
        return False
    if any(k in ctype for k in ("char", "text", "string", "bool", "enum", "uuid")):
        return True
    return True  # unknown type → keep samples (favour accuracy)


def dialect_label(enriched) -> str:
    dialect = (enriched.db_type or "").lower()
    return {
        "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
        "redshift": "Amazon Redshift", "mysql": "MySQL",
    }.get(dialect, enriched.db_type or "SQL")


def nhop_neighbors(edges: dict, seed: set, hops: int) -> set:
    """BFS the FK adjacency graph `edges` outward from `seed` up to `hops` levels.
    Returns reachable nodes EXCLUDING the seed. hops<=0 → empty set (seed only)."""
    visited = set(seed)
    frontier = set(seed)
    for _ in range(max(0, hops)):
        nxt = set()
        for node in frontier:
            for neighbor in (edges.get(node) or {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    nxt.add(neighbor)
        frontier = nxt
        if not frontier:
            break
    return visited - set(seed)


def resolve_scope_tables(enriched, seed_names, hops: int) -> tuple[set, set]:
    """Map a set of table names (bare or schema-qualified, any case) onto the enriched
    schema's qualified table names, then walk the FK graph `hops` levels out.

    Returns (seed_qualified, neighbor_qualified), both restricted to tables that
    actually exist in compact_tables. seed is empty when nothing matched."""
    pri = {str(p).lower() for p in (seed_names or set())}
    compact_names = [t.get("name", "") for t in (enriched.compact_tables or []) if t.get("name")]
    compact_set = set(compact_names)

    def _matches(qn: str) -> bool:
        return qn.lower() in pri or qn.split(".")[-1].lower() in pri

    seed = {qn for qn in compact_names if _matches(qn)}
    if not seed:
        return set(), set()

    edges = enriched.relationship_graph.edges or {}
    neighbors = nhop_neighbors(edges, seed, hops)
    neighbors = {n for n in neighbors if n in compact_set} - seed
    return seed, neighbors


def render_table_detail(
    enriched, t: dict, *, col_desc_max: int, table_desc_max: int, sample_limit: int
) -> list[str]:
    """Full per-table detail block (description, grain, columns, sample values)."""
    lines: list[str] = []
    tname = t.get("name", "")
    desc = clip(t.get("description") or "", table_desc_max)
    row_count = t.get("row_count")
    row_hint = f"  ~{row_count:,} rows" if row_count else ""
    lines.append(f"\n[{tname}]{row_hint}")
    if desc:
        lines.append(f"  {desc}")

    sem = enriched.table_semantics.get(tname, {})
    grain = sem.get("grain") or ""
    use_for = sem.get("use_for") or []
    never_use = sem.get("never_use_for") or []
    if grain:
        lines.append(f"  Grain: {grain}")
    if use_for:
        lines.append(f"  Use for: {', '.join(use_for)}")
    if never_use:
        lines.append(f"  Never use for: {', '.join(never_use)}")

    cols = t.get("columns") or []
    col_parts: list[str] = []
    sample_parts: list[str] = []
    for c in cols:
        cname = c.get("name") or ""
        ctype = c.get("type") or ""
        cdesc = clip(c.get("description") or "", col_desc_max)
        sem_type = c.get("semantic_type") or ""
        tag = f"[{sem_type}]" if sem_type else ""
        col_parts.append(f"{cname}{tag} ({ctype}){': ' + cdesc if cdesc else ''}")

        stats = c.get("stats") or {}
        top_vals = stats.get("top_values") or []
        if top_vals and is_categorical_col(c):
            sample_strs = []
            for rv in top_vals[:sample_limit]:
                if isinstance(rv, dict):
                    val = rv.get(cname) or next(iter(rv.values()), None)
                else:
                    val = rv
                if val is not None:
                    sample_strs.append(str(val))
            if sample_strs:
                sample_parts.append(f"{cname}: [{', '.join(sample_strs)}]")

    if col_parts:
        lines.append(f"  Columns: {' | '.join(col_parts)}")
    if sample_parts:
        lines.append(f"  Sample values: {' | '.join(sample_parts)}")
    return lines


def join_condition_lines(enriched, only: Optional[set] = None) -> list[str]:
    """Deduplicated JOIN CONDITIONS block. When `only` is given, keep an edge only
    if BOTH endpoints are in that set."""
    join_hints: list[str] = []
    seen_edges: set = set()
    for tbl_a in sorted(enriched.relationship_graph.edges.keys()):
        for tbl_b, condition in enriched.relationship_graph.edges[tbl_a].items():
            if only is not None and (tbl_a not in only or tbl_b not in only):
                continue
            edge_key = frozenset([tbl_a, tbl_b])
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                join_hints.append(f"  {condition}")
    if not join_hints:
        return []
    return ["\nJOIN CONDITIONS:", *join_hints]


def render_scoped_schema(
    enriched, seed: set, neighbors: set, hops: int, *,
    col_desc_max: int, table_desc_max: int, sample_limit: int,
    scope_intro: str, seed_header: str, related_header_fmt: str,
) -> str:
    """Assemble the two-tier scoped schema block. `seed_header` is shown before the
    full-detail seed tables; `related_header_fmt` is a format string accepting
    {n} (neighbour count) and {hops}."""
    compact_by_name = {
        t.get("name", ""): t for t in (enriched.compact_tables or []) if t.get("name")
    }
    label = dialect_label(enriched)
    lines = [
        f"SQL DIALECT: {label} — every query you write MUST be valid {label} SQL.",
        scope_intro,
        f"\n{seed_header}",
    ]
    for tname in sorted(seed):
        t = compact_by_name.get(tname)
        if t:
            lines.extend(render_table_detail(
                enriched, t,
                col_desc_max=col_desc_max, table_desc_max=table_desc_max, sample_limit=sample_limit,
            ))

    if neighbors:
        lines.append("\n" + related_header_fmt.format(n=len(neighbors), hops=hops))
        for tname in sorted(neighbors):
            t = compact_by_name.get(tname)
            sem = enriched.table_semantics.get(tname, {})
            purpose = sem.get("purpose") or (clip(t.get("description") or "", table_desc_max) if t else "") or ""
            lines.append(f"\n[{tname}]{(' ' + purpose) if purpose else ''}")
            cond = None
            for s in sorted(seed):
                cond = enriched.relationship_graph.get_join_condition(tname, s)
                if cond:
                    break
            if cond:
                lines.append(f"  Join: {cond}")

    lines.extend(join_condition_lines(enriched, only=set(seed) | set(neighbors)))
    return "\n".join(lines)
