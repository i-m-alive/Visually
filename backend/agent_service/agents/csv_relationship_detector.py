"""
CSV Relationship Detector — discovers FK-like join columns between uploaded CSVs.

Two strategies applied in order per column-name match:
  1. Column name match: same lowercased column name appears in two tables → candidate
  2. Value-overlap check: 200-row sample; if ≥ 20% of values in the smaller set
     appear in the other, the join is confirmed.  Below 20% means the shared name
     is probably coincidental (e.g. both have a generic "id" column with disjoint values).

Results are written into schema_doc["tables"][i]["relationships"] in the exact
format the DB schema crawler uses, so the schema_cache relationship_graph and
the SQL generator's JOIN logic work identically to the database code path.
"""
from pathlib import Path


def detect_relationships(session_dir: str, schema_doc: dict) -> dict:
    """
    Mutate schema_doc in-place: populate the "relationships" list in each table.
    Returns the same schema_doc (mutation + return for convenient chaining).

    Relationship format (FK from table_a.col_a → table_b.col_b):
        {
          "column":            "col_a",
          "references_table":  "table_b",
          "references_column": "col_b",
        }
    Both directions are added so the schema_cache's undirected graph sees the edge
    from either side.
    """
    import pandas as pd

    tables = schema_doc.get("tables", [])
    if len(tables) < 2:
        return schema_doc

    # Build a map from sanitized table name → set of lowercased column names
    table_cols: dict[str, set[str]] = {
        t["name"]: {c["name"].lower() for c in t.get("columns", [])}
        for t in tables
    }
    table_names = list(table_cols.keys())

    # Load 200-row samples for value-overlap verification
    # We try the canonical CSV path (safe_name + ".csv") and fall back to
    # globbing for the original file if the name was sanitized differently.
    dfs: dict[str, "pd.DataFrame"] = {}
    for tname in table_names:
        csv_path = Path(session_dir) / f"{tname}.csv"
        if not csv_path.exists():
            # Walk the directory looking for the original file
            for candidate in Path(session_dir).glob("*.csv"):
                if candidate.stem.replace("-", "_").replace(" ", "_") == tname:
                    csv_path = candidate
                    break
        try:
            df = pd.read_csv(csv_path, nrows=200, on_bad_lines="skip")
            df.columns = [c.lower() for c in df.columns]  # normalize for matching
            dfs[tname] = df
        except Exception:
            pass  # no sample available → skip value-overlap for this table

    # Collect confirmed FK pairs
    found: list[dict] = []

    for i, ta in enumerate(table_names):
        for tb in table_names[i + 1:]:
            # Only examine columns shared by name between these two tables
            shared_cols = table_cols[ta] & table_cols[tb]
            for col in shared_cols:
                # Value overlap check
                if (
                    ta in dfs and tb in dfs
                    and col in dfs[ta].columns
                    and col in dfs[tb].columns
                ):
                    va = set(dfs[ta][col].dropna().astype(str))
                    vb = set(dfs[tb][col].dropna().astype(str))
                    if va and vb:
                        smaller = min(len(va), len(vb))
                        overlap = len(va & vb) / smaller
                        if overlap < 0.20:
                            print(
                                f"[csv_relationship] skip {ta}.{col} ↔ {tb}.{col} "
                                f"— overlap {overlap:.0%} < 20%",
                                flush=True,
                            )
                            continue

                found.append({
                    "table_a": ta,
                    "col_a":   col,
                    "table_b": tb,
                    "col_b":   col,
                })
                print(
                    f"[csv_relationship] ✓ FK: {ta}.{col} ↔ {tb}.{col}",
                    flush=True,
                )

    # Build a per-table relationship map and inject into schema_doc
    rel_map: dict[str, list] = {t["name"]: [] for t in tables}
    for rel in found:
        rel_map[rel["table_a"]].append({
            "column":            rel["col_a"],
            "references_table":  rel["table_b"],
            "references_column": rel["col_b"],
        })
        rel_map[rel["table_b"]].append({
            "column":            rel["col_b"],
            "references_table":  rel["table_a"],
            "references_column": rel["col_a"],
        })

    for t in tables:
        t["relationships"] = rel_map.get(t["name"], [])

    print(
        f"[csv_relationship] {len(found)} FK relationship(s) "
        f"across {len(tables)} table(s)",
        flush=True,
    )
    return schema_doc
