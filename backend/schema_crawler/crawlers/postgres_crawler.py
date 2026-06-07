import asyncio
import json
import re
import os
import sys
from datetime import datetime, timezone
from typing import Optional
import asyncpg

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared"))
from bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL  # noqa: E402

SCHEMA_MODEL = BEDROCK_SONNET_MODEL


SEMANTIC_TABLE_KEYWORDS = {
    "order", "sale", "customer", "event", "transaction", "revenue",
    "metric", "product", "user", "account", "payment",
}


async def _run_with_timeout(coro, timeout: float):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def crawl_postgres(
    host: str, port: int, database: str, user: str, password: str, ssl: bool,
    connection_id: str,
) -> dict:
    start = datetime.now(timezone.utc)

    conn = await asyncpg.connect(
        host=host, port=port, database=database,
        user=user, password=password,
        ssl="require" if ssl else None,
        command_timeout=60,
    )
    try:
        # Step 1: enumerate tables
        tables_raw = await conn.fetch("""
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
        """)

        columns_raw = await conn.fetch("""
            SELECT table_schema, table_name, column_name, ordinal_position,
                   data_type, character_maximum_length, numeric_precision,
                   is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name, ordinal_position
        """)

        fk_raw = await conn.fetch("""
            SELECT tc.table_schema, tc.table_name, kcu.column_name,
                   ccu.table_name AS foreign_table_name, ccu.column_name AS foreign_column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
        """)

        pk_raw = await conn.fetch("""
            SELECT tc.table_schema, tc.table_name, kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
            WHERE tc.constraint_type = 'PRIMARY KEY'
        """)

        # Build lookup structures
        pks: dict[str, set] = {}
        for pk in pk_raw:
            key = f"{pk['table_schema']}.{pk['table_name']}"
            pks.setdefault(key, set()).add(pk["column_name"])

        fks: dict[str, list] = {}
        for fk in fk_raw:
            key = f"{fk['table_schema']}.{fk['table_name']}"
            fks.setdefault(key, []).append({
                "column": fk["column_name"],
                "ref_table": fk["foreign_table_name"],
                "ref_column": fk["foreign_column_name"],
            })

        # Group columns by table
        col_map: dict[str, list] = {}
        for col in columns_raw:
            key = f"{col['table_schema']}.{col['table_name']}"
            col_map.setdefault(key, []).append(dict(col))

        # Step 2: sample data
        table_data = {}
        for t in tables_raw:
            tkey = f"{t['table_schema']}.{t['table_name']}"
            tname = t["table_name"]
            tschema = t["table_schema"]
            full_name = f'"{tschema}"."{tname}"'

            row_count = 0
            try:
                rc = await _run_with_timeout(
                    conn.fetchval(f"SELECT reltuples::bigint FROM pg_class WHERE relname = $1", tname),
                    timeout=5.0
                )
                row_count = int(rc or 0)
            except Exception:
                pass

            col_stats = {}
            for col in (col_map.get(tkey) or [])[:20]:
                cname = col["column_name"]
                dtype = col["data_type"].lower()
                try:
                    if any(t in dtype for t in ("character", "text", "varchar")):
                        vals = await _run_with_timeout(
                            conn.fetch(
                                f'SELECT "{cname}", COUNT(*) as cnt FROM {full_name} '
                                f'WHERE "{cname}" IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10'
                            ),
                            timeout=5.0
                        )
                        if vals:
                            col_stats[cname] = {"top_values": [dict(r) for r in vals]}
                    elif any(t in dtype for t in ("integer", "numeric", "real", "double", "bigint", "smallint", "decimal", "float")):
                        stat = await _run_with_timeout(
                            conn.fetchrow(
                                f'SELECT MIN("{cname}") as min, MAX("{cname}") as max, '
                                f'AVG("{cname}") as avg, COUNT("{cname}") as cnt FROM {full_name}'
                            ),
                            timeout=5.0
                        )
                        if stat:
                            col_stats[cname] = {
                                "min": float(stat["min"]) if stat["min"] is not None else None,
                                "max": float(stat["max"]) if stat["max"] is not None else None,
                                "avg": float(stat["avg"]) if stat["avg"] is not None else None,
                            }
                    elif "date" in dtype or "time" in dtype:
                        stat = await _run_with_timeout(
                            conn.fetchrow(
                                f'SELECT MIN("{cname}") as min, MAX("{cname}") as max FROM {full_name}'
                            ),
                            timeout=5.0
                        )
                        if stat:
                            col_stats[cname] = {
                                "min": str(stat["min"]) if stat["min"] else None,
                                "max": str(stat["max"]) if stat["max"] else None,
                            }
                except Exception:
                    pass

            table_data[tkey] = {
                "table_schema": tschema,
                "table_name": tname,
                "row_count": row_count,
                "columns": col_map.get(tkey, []),
                "primary_keys": list(pks.get(tkey, [])),
                "foreign_keys": fks.get(tkey, []),
                "col_stats": col_stats,
            }

        # Step 3: infer relationships
        all_table_names = {t["table_name"] for t in tables_raw}
        for tkey, tdata in table_data.items():
            inferred_rels = []
            for col in tdata["columns"]:
                cname = col["column_name"]
                if cname.endswith("_id") and cname != "id":
                    base = cname[:-3]
                    if base + "s" in all_table_names:
                        inferred_rels.append({
                            "column": cname,
                            "ref_table": base + "s",
                            "ref_column": "id",
                            "inferred": True,
                        })
                    elif base in all_table_names:
                        inferred_rels.append({
                            "column": cname,
                            "ref_table": base,
                            "ref_column": "id",
                            "inferred": True,
                        })
            explicit = [
                {**fk, "inferred": False}
                for fk in tdata.get("foreign_keys", [])
            ]
            tdata["all_relationships"] = explicit + inferred_rels

        # Step 4: LLM semantic descriptions
        descriptions = await _generate_descriptions(table_data)

        # Step 5: importance ranking
        ranked_tables = _rank_tables(table_data)

        # Build final document
        tables_out = []
        for rank_idx, (tkey, tdata) in enumerate(ranked_tables, start=1):
            desc = descriptions.get(tdata["table_name"], {})
            columns_out = []
            for col in tdata["columns"]:
                cname = col["column_name"]
                col_desc = desc.get("columns", {}).get(cname, f"Column {cname}")
                col_out = {
                    "name": cname,
                    "type": col["data_type"],
                    "is_nullable": col.get("is_nullable", "YES") == "YES",
                    "is_primary_key": cname in tdata["primary_keys"],
                    "description": col_desc,
                    "stats": tdata["col_stats"].get(cname),
                }
                columns_out.append(col_out)

            rels_out = []
            for rel in tdata.get("all_relationships", []):
                rels_out.append({
                    "column": rel["column"],
                    "references": f"{rel['ref_table']}.{rel['ref_column']}",
                    "cardinality": "many-to-one",
                    "inferred": rel.get("inferred", False),
                })

            tables_out.append({
                "name": tdata["table_name"],
                "schema": tdata["table_schema"],
                "row_count": tdata["row_count"],
                "importance_rank": rank_idx,
                "description": desc.get("description", f"Table {tdata['table_name']}"),
                "columns": columns_out,
                "relationships": rels_out,
            })

        important_tables = [t["name"] for t in tables_out[:5]]

        crawl_duration = (datetime.now(timezone.utc) - start).total_seconds()

        return {
            "connection_id": connection_id,
            "crawled_at": start.isoformat(),
            "tables": tables_out,
            "important_tables": important_tables,
            "total_tables": len(tables_out),
            "version": 1,
            "crawl_duration_seconds": crawl_duration,
        }
    finally:
        await conn.close()


async def _generate_descriptions(table_data: dict) -> dict:
    schema_summary = {}
    for tkey, tdata in table_data.items():
        tname = tdata["table_name"]
        schema_summary[tname] = {
            "columns": [
                {
                    "name": col["column_name"],
                    "type": col["data_type"],
                    "sample_values": tdata["col_stats"].get(col["column_name"], {}),
                }
                for col in tdata["columns"][:30]
            ],
            "row_count": tdata["row_count"],
        }

    prompt = json.dumps(schema_summary, default=str)

    for attempt in range(2):
        try:
            text = await bedrock_invoke(
                model_id=SCHEMA_MODEL,
                system_prompt=(
                    "You are a database analyst. Given raw database schema metadata including table names, "
                    "column names, data types, and sample values, generate semantic descriptions.\n\n"
                    "For each table: write 1-2 sentences describing what business entity or process this "
                    "table represents, and what one row represents.\n"
                    "For each column: write a short phrase (5-15 words) describing what this column measures or identifies.\n\n"
                    "Return ONLY valid JSON. No prose, no markdown, no explanation."
                ),
                user_message=f"Generate descriptions for this schema:\n{prompt}",
                max_tokens=4096,
                temperature=0.1,
            )
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"```$", "", text).strip()
            parsed = json.loads(text)
            return parsed.get("tables", parsed)
        except (json.JSONDecodeError, Exception):
            if attempt == 1:
                return {}
    return {}


def _rank_tables(table_data: dict) -> list:
    counts = [t["row_count"] for t in table_data.values()]
    max_count = max(counts) if counts else 1

    scored = []
    for tkey, tdata in table_data.items():
        tname = tdata["table_name"].lower()
        row_count = tdata["row_count"]
        cols = tdata["columns"]

        row_score = row_count / max_count if max_count > 0 else 0

        numeric_types = {"integer", "numeric", "real", "double precision", "bigint", "smallint", "decimal", "float"}
        non_id_numeric = sum(
            1 for c in cols
            if c["data_type"].lower() in numeric_types and not c["column_name"].endswith("_id") and c["column_name"] != "id"
        )
        richness = non_id_numeric / len(cols) if cols else 0

        # centrality: how many other tables reference this one
        centrality_count = sum(
            1 for other in table_data.values()
            for rel in other.get("all_relationships", [])
            if rel.get("ref_table") == tdata["table_name"]
        )
        centrality = min(centrality_count / 5, 1.0)

        name_bonus = 0.3 if any(kw in tname for kw in SEMANTIC_TABLE_KEYWORDS) else 0.0

        score = (row_score * 0.4 + richness * 0.2 + centrality * 0.1 + name_bonus)
        scored.append((tkey, tdata, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    return [(tkey, tdata) for tkey, tdata, _ in scored]
