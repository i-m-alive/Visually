"""
CSV Ingestor — saves uploaded CSV bytes to disk and builds a schema_doc dict
that matches the DB schema crawler format.

One CSV file = one "table".  Table name = filename stem (sanitized for SQL).
Column stats (type, distinct_count, null_pct, sample_values) are inferred from
a pandas sample of up to 5 000 rows so the schema matcher and value sampler get
realistic context without loading the whole file into memory.

The returned schema_doc is passed directly into schema_cache.get_or_build(),
so the rest of the pipeline (schema_matcher, sql_agent, value_sampler,
executor) runs identically to the database code path.
"""
import os
import re
from pathlib import Path


def save_csvs(csv_files: list[dict], job_id: str) -> str:
    """
    Write CSV bytes to /tmp/csv_{job_id}/.
    Each item in csv_files must be {"filename": str, "bytes": bytes}.
    Returns the session directory path.

    Filenames are sanitized (path-traversal characters stripped) before writing.
    """
    session_dir = f"/tmp/csv_{job_id}"
    os.makedirs(session_dir, exist_ok=True)
    for f in csv_files:
        # Strip any path components so we write flat files only
        safe_filename = Path(f["filename"]).name
        dest = os.path.join(session_dir, safe_filename)
        with open(dest, "wb") as fp:
            fp.write(f["bytes"])
        kb = len(f["bytes"]) // 1024
        print(f"[csv_ingestor] saved '{safe_filename}'  ({kb} KB)", flush=True)
    return session_dir


# ── Column type inference ─────────────────────────────────────────────────────

def _infer_col_type(series) -> str:
    """Map a pandas Series dtype to a DB-compatible type string."""
    import pandas as pd

    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "float"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    # String column — check whether values look like dates
    if series.dtype == object:
        sample = series.dropna().head(20).astype(str)
        if len(sample) > 0:
            date_hits = sum(
                1 for v in sample
                if re.search(
                    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", v
                )
            )
            if date_hits >= len(sample) * 0.7:
                return "date"
        # Low-cardinality → treat as categorical (enums, boolean-like, statuses)
        n_unique = series.nunique(dropna=True)
        n_total = series.count()
        if n_total > 0 and n_unique <= max(30, n_total * 0.05):
            return "categorical"
        return "text"
    return "text"


# ── Public API ────────────────────────────────────────────────────────────────

def ingest_csvs(session_dir: str) -> dict:
    """
    Parse all CSVs in session_dir and return a schema_doc matching the DB
    schema crawler format:

        {"tables": [
            {
              "name":        "orders",            # sanitized stem
              "description": "Uploaded CSV: orders.csv  (12 345 rows)",
              "row_count":   12345,
              "columns": [
                  {"name": "order_id",  "type": "integer",  "description": "...",
                   "distinct_count": 12345, "null_pct": 0.0, "sample_values": ["1","2"]}
              ],
              "relationships": []              # populated by csv_relationship_detector
            }
        ]}
    """
    import pandas as pd

    tables: list[dict] = []
    csv_paths = sorted(Path(session_dir).glob("*.csv"))

    if not csv_paths:
        print(f"[csv_ingestor] ⚠ no CSV files found in {session_dir}", flush=True)
        return {"tables": []}

    for csv_path in csv_paths:
        raw_stem = csv_path.stem
        # Sanitize name: same transformation used in csv_executor.py so table
        # names in schema_doc match what DuckDB creates at query time.
        table_name = raw_stem.replace("-", "_").replace(" ", "_")

        # Count rows without loading the full file (newline count − 1 for header)
        try:
            with open(csv_path, "r", encoding="utf-8", errors="replace") as fh:
                total_rows = max(0, sum(1 for _ in fh) - 1)
        except Exception:
            total_rows = 0

        # Load a sample for type inference and stats
        try:
            df = pd.read_csv(csv_path, nrows=5000, on_bad_lines="skip")
        except Exception as e:
            print(f"[csv_ingestor] ⚠ failed to parse {csv_path.name}: {e}", flush=True)
            continue

        columns: list[dict] = []
        for col in df.columns:
            col_type = _infer_col_type(df[col])
            null_pct = round(float(df[col].isna().mean()) * 100, 1)
            distinct_count = int(df[col].nunique(dropna=True))
            sample_vals = (
                df[col].dropna().astype(str).unique()[:5].tolist()
            )
            columns.append({
                "name":           col,
                "type":           col_type,
                "description":    f"{col_type} column from {csv_path.name}",
                "distinct_count": distinct_count,
                "null_pct":       null_pct,
                "sample_values":  sample_vals,
            })

        tables.append({
            "name":          table_name,
            "description":   f"Uploaded CSV: {csv_path.name}  ({total_rows:,} rows)",
            "row_count":     total_rows,
            "columns":       columns,
            "relationships": [],
        })
        print(
            f"[csv_ingestor] ✓ '{table_name}'  "
            f"rows={total_rows:,}  cols={len(columns)}",
            flush=True,
        )

    return {"tables": tables}
