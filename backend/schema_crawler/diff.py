"""
Schema diff: compare two SemanticSchemaDocument snapshots and detect breaking changes.
"""
from typing import Any


def compute_schema_diff(old_doc: dict, new_doc: dict) -> dict:
    old_tables = {t["name"]: t for t in old_doc.get("tables", [])}
    new_tables = {t["name"]: t for t in new_doc.get("tables", [])}

    dropped_tables = [name for name in old_tables if name not in new_tables]
    added_tables = [name for name in new_tables if name not in old_tables]
    common_tables = [name for name in old_tables if name in new_tables]

    column_changes: list[dict] = []
    for tname in common_tables:
        old_cols = {c["name"]: c for c in old_tables[tname].get("columns", [])}
        new_cols = {c["name"]: c for c in new_tables[tname].get("columns", [])}

        for cname in old_cols:
            if cname not in new_cols:
                column_changes.append({
                    "table": tname, "column": cname,
                    "change_type": "column_dropped",
                    "breaking": True,
                    "old": old_cols[cname].get("type"), "new": None,
                })
            else:
                old_type = old_cols[cname].get("type", "")
                new_type = new_cols[cname].get("type", "")
                if old_type != new_type:
                    column_changes.append({
                        "table": tname, "column": cname,
                        "change_type": "column_type_changed",
                        "breaking": True,
                        "old": old_type, "new": new_type,
                    })

        for cname in new_cols:
            if cname not in old_cols:
                column_changes.append({
                    "table": tname, "column": cname,
                    "change_type": "column_added",
                    "breaking": False,
                    "old": None, "new": new_cols[cname].get("type"),
                })

    breaking_changes = (
        [{"table": t, "change_type": "table_dropped"} for t in dropped_tables]
        + [c for c in column_changes if c["breaking"]]
    )

    return {
        "dropped_tables": dropped_tables,
        "added_tables": added_tables,
        "column_changes": column_changes,
        "breaking_changes": breaking_changes,
        "has_breaking_changes": len(breaking_changes) > 0,
        "severity": "critical" if breaking_changes else ("warning" if added_tables else "info"),
    }


def flag_affected_widgets(diff: dict, widgets: list[dict]) -> list[str]:
    """Return widget IDs whose sql_query references a dropped/modified table or column."""
    affected_ids: list[str] = []

    broken_tables = set(diff.get("dropped_tables", []))
    broken_table_columns: dict[str, set] = {}
    for change in diff.get("column_changes", []):
        if change["breaking"]:
            broken_table_columns.setdefault(change["table"], set()).add(change["column"])

    for widget in widgets:
        sql = (widget.get("sql_query") or "").lower()
        widget_id = widget.get("id", "")
        if not sql:
            continue
        flagged = False
        for tname in broken_tables:
            if tname.lower() in sql:
                flagged = True
                break
        if not flagged:
            for tname, cols in broken_table_columns.items():
                if tname.lower() in sql:
                    for col in cols:
                        if col.lower() in sql:
                            flagged = True
                            break
                if flagged:
                    break
        if flagged:
            affected_ids.append(widget_id)

    return affected_ids
