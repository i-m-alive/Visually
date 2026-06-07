import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
import aiomysql

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


async def crawl_mysql(
    host: str, port: int, database: str, user: str, password: str, ssl: bool,
    connection_id: str,
) -> dict:
    start = datetime.now(timezone.utc)

    conn = await aiomysql.connect(
        host=host, port=port, db=database,
        user=user, password=password,
        ssl=ssl,
        connect_timeout=60,
        cursorclass=aiomysql.DictCursor,
    )
    try:
        async def fetch(sql, *args):
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return await cur.fetchall()

        tables_raw = await fetch("""
            SELECT TABLE_SCHEMA as table_schema, TABLE_NAME as table_name,
                   TABLE_TYPE as table_type, TABLE_ROWS as table_rows
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME
        """, database)

        columns_raw = await fetch("""
            SELECT TABLE_SCHEMA as table_schema, TABLE_NAME as table_name,
                   COLUMN_NAME as column_name, ORDINAL_POSITION as ordinal_position,
                   DATA_TYPE as data_type, CHARACTER_MAXIMUM_LENGTH as character_maximum_length,
                   NUMERIC_PRECISION as numeric_precision,
                   IS_NULLABLE as is_nullable, COLUMN_DEFAULT as column_default,
                   COLUMN_KEY as column_key
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
        """, database)

        fk_raw = await fetch("""
            SELECT TABLE_NAME as table_name, COLUMN_NAME as column_name,
                   REFERENCED_TABLE_NAME as foreign_table_name,
                   REFERENCED_COLUMN_NAME as foreign_column_name
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL
        """, database)

        pks: dict[str, set] = {}
        for col in columns_raw:
            if col.get("column_key") == "PRI":
                tname = col["table_name"]
                pks.setdefault(tname, set()).add(col["column_name"])

        fks: dict[str, list] = {}
        for fk in fk_raw:
            tname = fk["table_name"]
            fks.setdefault(tname, []).append({
                "column": fk["column_name"],
                "ref_table": fk["foreign_table_name"],
                "ref_column": fk["foreign_column_name"],
            })

        col_map: dict[str, list] = {}
        for col in columns_raw:
            col_map.setdefault(col["table_name"], []).append(dict(col))

        table_data = {}
        all_table_names = {t["table_name"] for t in tables_raw}

        for t in tables_raw:
            tname = t["table_name"]
            row_count = int(t.get("table_rows") or 0)
            col_stats = {}

            for col in (col_map.get(tname) or [])[:20]:
                cname = col["column_name"]
                dtype = col["data_type"].lower()
                try:
                    if any(x in dtype for x in ("char", "text", "varchar")):
                        vals = await _run_with_timeout(
                            fetch(
                                f"SELECT `{cname}`, COUNT(*) as cnt FROM `{tname}` "
                                f"WHERE `{cname}` IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
                            ),
                            timeout=5.0
                        )
                        if vals:
                            col_stats[cname] = {"top_values": vals}
                    elif any(x in dtype for x in ("int", "float", "decimal", "double", "numeric", "real")):
                        stat = await _run_with_timeout(
                            fetch(f"SELECT MIN(`{cname}`) as min, MAX(`{cname}`) as max, AVG(`{cname}`) as avg FROM `{tname}`"),
                            timeout=5.0
                        )
                        if stat:
                            col_stats[cname] = {
                                "min": float(stat[0]["min"]) if stat[0]["min"] is not None else None,
                                "max": float(stat[0]["max"]) if stat[0]["max"] is not None else None,
                                "avg": float(stat[0]["avg"]) if stat[0]["avg"] is not None else None,
                            }
                    elif "date" in dtype or "time" in dtype:
                        stat = await _run_with_timeout(
                            fetch(f"SELECT MIN(`{cname}`) as min, MAX(`{cname}`) as max FROM `{tname}`"),
                            timeout=5.0
                        )
                        if stat:
                            col_stats[cname] = {
                                "min": str(stat[0]["min"]) if stat[0]["min"] else None,
                                "max": str(stat[0]["max"]) if stat[0]["max"] else None,
                            }
                except Exception:
                    pass

            inferred_rels = []
            for col in (col_map.get(tname) or []):
                cname = col["column_name"]
                if cname.endswith("_id") and cname != "id":
                    base = cname[:-3]
                    if base + "s" in all_table_names:
                        inferred_rels.append({"column": cname, "ref_table": base + "s", "ref_column": "id", "inferred": True})
                    elif base in all_table_names:
                        inferred_rels.append({"column": cname, "ref_table": base, "ref_column": "id", "inferred": True})

            explicit = [{**fk, "inferred": False} for fk in fks.get(tname, [])]

            table_data[tname] = {
                "table_schema": database,
                "table_name": tname,
                "row_count": row_count,
                "columns": col_map.get(tname, []),
                "primary_keys": list(pks.get(tname, [])),
                "foreign_keys": fks.get(tname, []),
                "col_stats": col_stats,
                "all_relationships": explicit + inferred_rels,
            }

        descriptions = await _generate_descriptions(table_data)
        ranked_tables = _rank_tables(table_data)

        tables_out = []
        for rank_idx, (tname, tdata) in enumerate(ranked_tables, start=1):
            desc = descriptions.get(tname, {})
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
                "row_count": tdata["row_count"],
                "importance_rank": rank_idx,
                "description": desc.get("description", f"Table {tname}"),
                "columns": columns_out,
                "relationships": rels_out,
            })

        crawl_duration = (datetime.now(timezone.utc) - start).total_seconds()

        return {
            "connection_id": connection_id,
            "crawled_at": start.isoformat(),
            "tables": tables_out,
            "important_tables": [t["name"] for t in tables_out[:5]],
            "total_tables": len(tables_out),
            "version": 1,
            "crawl_duration_seconds": crawl_duration,
        }
    finally:
        conn.close()


async def _generate_descriptions(table_data: dict) -> dict:
    schema_summary = {
        tname: {
            "columns": [{"name": c["column_name"], "type": c["data_type"]} for c in tdata["columns"][:30]],
            "row_count": tdata["row_count"],
        }
        for tname, tdata in table_data.items()
    }
    prompt = json.dumps(schema_summary, default=str)
    for attempt in range(2):
        try:
            text = await bedrock_invoke(
                model_id=SCHEMA_MODEL,
                system_prompt=(
                    "You are a database analyst. Given raw database schema metadata, generate semantic descriptions.\n"
                    "For each table: 1-2 sentences about the business entity.\n"
                    "For each column: 5-15 words describing what it measures.\n"
                    "Return ONLY valid JSON. No prose, no markdown."
                ),
                user_message=f"Generate descriptions:\n{prompt}",
                max_tokens=4096,
                temperature=0.1,
            )
            text = text.strip()
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"```$", "", text).strip()
            parsed = json.loads(text)
            return parsed.get("tables", parsed)
        except Exception:
            if attempt == 1:
                return {}
    return {}


def _rank_tables(table_data: dict) -> list:
    counts = [t["row_count"] for t in table_data.values()]
    max_count = max(counts) if counts else 1
    numeric_types = {"int", "bigint", "smallint", "tinyint", "float", "double", "decimal", "numeric", "real"}
    scored = []
    for tname, tdata in table_data.items():
        cols = tdata["columns"]
        row_score = tdata["row_count"] / max_count if max_count > 0 else 0
        non_id_numeric = sum(
            1 for c in cols
            if c["data_type"].lower() in numeric_types and not c["column_name"].endswith("_id") and c["column_name"] != "id"
        )
        richness = non_id_numeric / len(cols) if cols else 0
        name_bonus = 0.3 if any(kw in tname.lower() for kw in SEMANTIC_TABLE_KEYWORDS) else 0.0
        score = row_score * 0.4 + richness * 0.2 + name_bonus
        scored.append((tname, tdata, score))
    scored.sort(key=lambda x: x[2], reverse=True)
    return [(tname, tdata) for tname, tdata, _ in scored]
