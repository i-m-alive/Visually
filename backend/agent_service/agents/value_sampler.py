"""
Value Sampler — runs cheap exploratory queries before SQL generation.

For each chart's top candidate tables/columns, fires up to 3 lightweight queries:
  1. DISTINCT values on the dimension column  → confirms real category names
  2. MIN / MAX on the date column             → reveals actual time span in the table
  3. MIN / MAX / AVG on the metric column     → reveals value magnitude / scale

Results are injected into the SQL-generation prompt so the LLM works with
confirmed DB values instead of vision-estimated guesses.

This is the single biggest accuracy lift for first-attempt SQL generation:
if the chart shows "Client A" on the x-axis and the DB actually stores "Client A"
in that column, the LLM can confidently write  WHERE client_name = 'Client A'.
"""
import asyncio
import re
from typing import Optional
from utils.http_clients import call_query_executor

# Safe, read-only sampling templates (SELECT-only, row-limited)
_DISTINCT_SQL  = "SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 25"
_DATE_RANGE_SQL = "SELECT MIN({col}) AS min_date, MAX({col}) AS max_date FROM {table}"
_METRIC_RANGE_SQL = "SELECT MIN({col}) AS min_val, MAX({col}) AS max_val, AVG({col}) AS avg_val FROM {table}"


async def sample_candidate(
    connection_id: str,
    candidate: dict,       # from SchemaMatcher: {tables, key_columns, join, ...}
    chart_spec: dict,      # from VisionAgent:   {type, x_tick_labels, estimated_values, ...}
    timeout_seconds: int = 8,
) -> dict:
    """
    Run exploratory queries for the top candidate's key columns.
    Returns a sample_context dict that is injected into the SQL-generation prompt.
    Returns {} on failure — caller treats it as non-fatal.
    """
    key_cols = candidate.get("key_columns") or {}
    tables = candidate.get("tables") or []
    if not tables:
        return {}

    primary_table = tables[0]
    dimension_col = key_cols.get("dimension")
    date_col      = key_cols.get("date")
    metric_col    = key_cols.get("metric")

    # Build the list of (label, sql) pairs for this candidate
    queries: list[tuple[str, str]] = []

    if dimension_col:
        queries.append(("dimension_values", _DISTINCT_SQL.format(
            col=dimension_col, table=primary_table,
        )))
        # Also get per-category counts — reveals which categories dominate
        # and lets the LLM match estimated_values to actual category magnitudes
        queries.append(("category_counts", (
            f"SELECT {dimension_col} AS category, COUNT(*) AS cnt "
            f"FROM {primary_table} WHERE {dimension_col} IS NOT NULL "
            f"GROUP BY {dimension_col} ORDER BY cnt DESC LIMIT 20"
        )))
    if date_col:
        queries.append(("date_range", _DATE_RANGE_SQL.format(
            col=date_col, table=primary_table,
        )))
    else:
        # Option 6: Try common date column names to prevent wrong_date_range failures.
        # Run as separate queries; failures are silently ignored (non-fatal).
        _AUTO_DATE_COLS = ["dateadded", "created_at", "startdate", "start_date", "createdate"]
        for _dc in _AUTO_DATE_COLS[:3]:
            queries.append((f"auto_date_{_dc}", _DATE_RANGE_SQL.format(
                col=_dc, table=primary_table,
            )))
    if metric_col:
        queries.append(("metric_range", _METRIC_RANGE_SQL.format(
            col=metric_col, table=primary_table,
        )))

    if not queries:
        return {}

    # Execute all sampling queries concurrently (fast, cheap)
    results = await asyncio.gather(
        *[
            call_query_executor(connection_id, sql, row_limit=25)
            for _, sql in queries
        ],
        return_exceptions=True,
    )

    raw: dict[str, list] = {}
    for (label, _sql), result in zip(queries, results):
        if isinstance(result, Exception):
            print(f"[value_sampler] ⚠ {label} failed: {result}", flush=True)
            continue
        if result and not result.get("error"):
            raw[label] = result.get("rows", [])

    return _format_context(raw, dimension_col, date_col, metric_col, chart_spec)


def _format_context(
    raw: dict,
    dimension_col: Optional[str],
    date_col: Optional[str],
    metric_col: Optional[str],
    chart_spec: dict,
) -> dict:
    """
    Convert raw sample rows into a clean context dict for the SQL-generation prompt.
    Cross-references dimension values against the chart's x_tick_labels so the LLM
    gets an explicit list of confirmed matching values.
    """
    context: dict = {}

    # ── 1. Dimension values ───────────────────────────────────────────────────
    dim_rows = raw.get("dimension_values", [])
    if dim_rows and dimension_col:
        actual_vals = [
            str(r.get(dimension_col, ""))
            for r in dim_rows
            if r.get(dimension_col) is not None
        ]
        context["actual_dimension_values"] = actual_vals[:20]

        # Cross-reference with vision x_tick_labels
        vision_ticks = chart_spec.get("x_tick_labels") or []
        if vision_ticks:
            confirmed = [
                v for v in actual_vals
                if any(_fuzzy_match(v, t) for t in vision_ticks)
            ]
            if confirmed:
                context["confirmed_dimension_values"] = confirmed
                context["dimension_note"] = (
                    f"These values from the DB match the chart's x-axis labels: {confirmed}. "
                    f"Use them directly in SQL (e.g. WHERE {dimension_col} IN "
                    f"({', '.join(repr(v) for v in confirmed[:5])})) if the chart filters by category."
                )

    # ── 1b. Category counts (per-category frequencies) ────────────────────────
    count_rows = raw.get("category_counts", [])
    if count_rows and dimension_col:
        cat_counts = {}
        for r in count_rows:
            cat_val = r.get("category") or r.get(dimension_col)
            cnt_val = r.get("cnt")
            if cat_val is not None and cnt_val is not None:
                try:
                    cat_counts[str(cat_val)] = int(cnt_val)
                except (TypeError, ValueError):
                    pass
        if cat_counts:
            context["category_counts"] = cat_counts
            # Cross-check counts against estimated_values magnitudes
            vision_ticks = chart_spec.get("x_tick_labels") or []
            estimated = chart_spec.get("estimated_values") or {}
            if estimated:
                # Find categories whose count magnitude matches estimated values
                def _parse_est_mag(v):
                    import re as _re
                    s = str(v).replace("~","").replace(",","").strip()
                    m = _re.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
                    if not m: return None
                    n = float(m.group(1))
                    suf = m.group(2).lower()
                    return n * {"k":1e3,"m":1e6,"b":1e9,"t":1e12}.get(suf, 1.0)
                est_vals = [v for v in [_parse_est_mag(str(ev)) for ev in estimated.values()] if v]
                avg_est = sum(est_vals) / len(est_vals) if est_vals else 0
                avg_cnt = sum(cat_counts.values()) / len(cat_counts) if cat_counts else 0
                if avg_est > 0 and avg_cnt > 0:
                    ratio = avg_cnt / avg_est
                    if 0.1 <= ratio <= 10:
                        context["count_magnitude_note"] = (
                            f"COUNT(*) per category averages {avg_cnt:,.0f} which is close to "
                            f"chart's estimated values (~{avg_est:,.0f}). "
                            f"Use COUNT(*) or COUNT(DISTINCT ...) as the aggregation — "
                            f"it directly matches the chart magnitude."
                        )
                    elif ratio > 10:
                        context["count_magnitude_note"] = (
                            f"COUNT(*) per category averages {avg_cnt:,.0f} but chart expects ~{avg_est:,.0f}. "
                            f"Do NOT use COUNT(*) directly — try AVG, SUM of a numeric column, "
                            f"or COUNT(DISTINCT id_col) to get values near {avg_est:,.0f}."
                        )
                    else:
                        context["count_magnitude_note"] = (
                            f"COUNT(*) per category averages {avg_cnt:,.0f} but chart expects ~{avg_est:,.0f}. "
                            f"Consider SUM(numeric_col) or multiply COUNT by a factor — "
                            f"the raw count is too small."
                        )
            context["category_count_note"] = (
                "Per-category row counts (use to verify aggregation choice): "
                + ", ".join(f"{k}={v:,}" for k, v in list(cat_counts.items())[:8])
            )

    # ── 2. Date range ─────────────────────────────────────────────────────────
    date_rows = raw.get("date_range", [])
    if date_rows:
        r = date_rows[0]
        min_d = str(r.get("min_date", ""))
        max_d = str(r.get("max_date", ""))
        if min_d or max_d:
            context["actual_date_range"] = {"min": min_d, "max": max_d}
            context["date_note"] = (
                f"Actual date range in table: {min_d} → {max_d}. "
                "Use this range (or a subset) for time filters. "
                "Do NOT filter outside this range — it will return 0 rows."
            )

    # ── 2b. Auto-detected date range (from common column name probing) ─────────
    if not context.get("actual_date_range"):
        for key, rows in raw.items():
            if key.startswith("auto_date_") and rows:
                r = rows[0]
                min_d = str(r.get("min_date", "") or "")
                max_d = str(r.get("max_date", "") or "")
                if min_d and min_d not in ("None", "null", ""):
                    detected_col = key.replace("auto_date_", "")
                    context["actual_date_range"] = {"min": min_d, "max": max_d}
                    context["date_note"] = (
                        f"Auto-detected date column '{detected_col}' range: {min_d} → {max_d}. "
                        "Use this column and range for time filters. "
                        "Do NOT filter outside this range — it will return 0 rows."
                    )
                    print(f"[value_sampler] auto-detected date col='{detected_col}' range={min_d}→{max_d}", flush=True)
                    break

    # ── 3. Metric scale ───────────────────────────────────────────────────────
    metric_rows = raw.get("metric_range", [])
    if metric_rows:
        r = metric_rows[0]
        try:
            avg_val = float(r.get("avg_val") or 0)
            min_val = r.get("min_val")
            max_val = r.get("max_val")
            context["actual_metric_range"] = {
                "min": min_val,
                "max": max_val,
                "avg": round(avg_val, 2),
            }
            context["metric_note"] = (
                f"Actual metric values: min={min_val}, max={max_val}, avg={round(avg_val, 2)}. "
                "Choose aggregation (SUM / COUNT / AVG) so the result magnitude matches "
                "the chart's estimated_values."
            )
        except (TypeError, ValueError):
            pass

    return context


def build_date_constraint(date_range: dict, date_col: str) -> str:
    """
    Convert an actual_date_range dict (min/max from DB) into a SQL WHERE fragment.
    Returns empty string if no usable range is found.
    """
    min_d = str(date_range.get("min", "") or "").strip()
    max_d = str(date_range.get("max", "") or "").strip()
    if not min_d and not max_d:
        return ""
    if min_d and max_d:
        return f"{date_col} BETWEEN '{min_d}' AND '{max_d}'"
    elif min_d:
        return f"{date_col} >= '{min_d}'"
    else:
        return f"{date_col} <= '{max_d}'"


def extract_dashboard_date_context(chart_specs: list) -> dict:
    """
    Scan all chart specs for date-like x_tick_labels to infer the dashboard's
    time window. Returns a dict with inferred_period + date_instruction fields
    that are injected into every SQL generation call for this dashboard.
    """
    import re
    all_labels: list[str] = []
    for spec in chart_specs:
        labels = spec.get("x_tick_labels") or []
        all_labels.extend(str(l) for l in labels)
        # Also scan title / axis labels for year mentions
        for field in ("title", "x_axis_label", "y_axis_label"):
            val = spec.get(field) or ""
            if val:
                all_labels.append(str(val))

    if not all_labels:
        return {}

    year_pattern = re.compile(r"\b(20\d{2})\b")
    years = sorted({int(m) for label in all_labels for m in year_pattern.findall(label)})

    # Quarter / month patterns
    quarter_pattern = re.compile(r"\bQ[1-4]\b", re.IGNORECASE)
    has_quarters = any(quarter_pattern.search(l) for l in all_labels)
    month_abbrevs = {"jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"}
    has_months = any(
        any(abbr in l.lower() for abbr in month_abbrevs)
        for l in all_labels
    )

    context: dict = {}
    if years:
        min_year, max_year = min(years), max(years)
        context["inferred_years"] = years
        if min_year == max_year:
            context["inferred_period"] = str(min_year)
            granularity = "monthly" if has_months else ("quarterly" if has_quarters else "annual")
            context["date_instruction"] = (
                f"Dashboard appears to cover {granularity} data for {min_year}. "
                f"Apply WHERE date_col BETWEEN '{min_year}-01-01' AND '{min_year}-12-31' "
                f"on any date column unless a wider range is clearly required by the chart."
            )
        else:
            context["inferred_period"] = f"{min_year}–{max_year}"
            context["date_instruction"] = (
                f"Dashboard covers data from {min_year} to {max_year}. "
                f"Restrict date columns to this range (WHERE date_col >= '{min_year}-01-01' "
                f"AND date_col <= '{max_year}-12-31') unless the chart clearly shows all-time data."
            )
    elif has_months:
        context["inferred_period"] = "monthly (year unknown)"
        context["date_instruction"] = (
            "Dashboard uses monthly granularity. Group date columns by month "
            "(DATE_TRUNC('month', date_col)) and ensure the WHERE clause "
            "covers the time period shown in the chart."
        )

    return context


async def probe_aggregations(
    connection_id: str,
    candidate: dict,
    chart_spec: dict,
    estimated_values: dict,
    timeout_seconds: int = 6,
) -> Optional[str]:
    """
    Option 1 — Multi-aggregation probe.

    After an attempt returns data but value_match < 0.3, run lightweight
    COUNT / SUM / AVG / COUNT-DISTINCT probes in parallel and return a feedback
    string telling the LLM which aggregation best matches the chart's estimated values.

    Returns a targeted retry_feedback string, or None if nothing useful found.
    """
    import re as _re
    key_cols = candidate.get("key_columns") or {}
    tables = candidate.get("tables") or []
    if not tables or not estimated_values:
        return None

    table = tables[0]
    dim_col = key_cols.get("dimension")
    metric_col = key_cols.get("metric")

    if not dim_col:
        return None

    # Build 4 candidate aggregation SQLs
    agg_sqls: list[tuple[str, str]] = [
        ("COUNT_STAR",    f"SELECT {dim_col} AS cat, COUNT(*) AS val FROM {table} WHERE {dim_col} IS NOT NULL GROUP BY {dim_col} ORDER BY val DESC LIMIT 20"),
        ("SUM",          f"SELECT {dim_col} AS cat, SUM({metric_col}) AS val FROM {table} WHERE {dim_col} IS NOT NULL GROUP BY {dim_col} ORDER BY val DESC LIMIT 20" if metric_col else ""),
        ("AVG",          f"SELECT {dim_col} AS cat, AVG({metric_col}) AS val FROM {table} WHERE {dim_col} IS NOT NULL GROUP BY {dim_col} ORDER BY val DESC LIMIT 20" if metric_col else ""),
        ("COUNT_DIST_ID", f"SELECT {dim_col} AS cat, COUNT(DISTINCT {metric_col}) AS val FROM {table} WHERE {dim_col} IS NOT NULL GROUP BY {dim_col} ORDER BY val DESC LIMIT 20" if metric_col else ""),
    ]
    agg_sqls = [(name, sql) for name, sql in agg_sqls if sql]

    # Parse estimated values into floats
    def _parse(v: str) -> Optional[float]:
        s = str(v).replace("~","").replace(",","").strip()
        m = _re.match(r"([\d.]+)\s*([kKmMbBtT]?)", s)
        if not m: return None
        n = float(m.group(1))
        suf = m.group(2).lower()
        return n * {"k":1e3,"m":1e6,"b":1e9,"t":1e12}.get(suf, 1.0)

    est_vals = [v for v in [_parse(str(ev)) for ev in estimated_values.values()] if v and v > 0]
    if not est_vals:
        return None
    avg_est = sum(est_vals) / len(est_vals)

    # Execute all probes concurrently
    results = await asyncio.gather(
        *[call_query_executor(connection_id, sql, row_limit=20) for _, sql in agg_sqls],
        return_exceptions=True,
    )

    best_agg = None
    best_ratio_dist = float("inf")

    for (agg_name, _), result in zip(agg_sqls, results):
        if isinstance(result, Exception) or not result or result.get("error"):
            continue
        rows = result.get("rows", [])
        if not rows:
            continue
        nums = []
        for row in rows[:10]:
            v = row.get("val")
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                pass
        if not nums:
            continue
        avg_actual = sum(nums) / len(nums)
        if avg_actual <= 0:
            continue
        ratio = avg_actual / avg_est
        # Closest ratio to 1.0 wins
        dist = abs(ratio - 1.0)
        if dist < best_ratio_dist:
            best_ratio_dist = dist
            best_agg = (agg_name, avg_actual, ratio)

    if best_agg is None:
        return None

    agg_name, avg_actual, ratio = best_agg
    agg_display = {
        "COUNT_STAR": "COUNT(*)",
        "SUM": f"SUM({metric_col})",
        "AVG": f"AVG({metric_col})",
        "COUNT_DIST_ID": f"COUNT(DISTINCT {metric_col})",
    }.get(agg_name, agg_name)

    if best_ratio_dist < 0.5:  # within 50% — genuinely useful signal
        return (
            f"AGGREGATION PROBE: {agg_display} on '{table}' returns avg ~{avg_actual:,.1f}, "
            f"which is closest to the chart's expected ~{avg_est:,.1f} (ratio={ratio:.2f}). "
            f"Use {agg_display} as your aggregation in the next attempt."
        )
    else:
        return (
            f"AGGREGATION PROBE: best aggregation was {agg_display} (avg={avg_actual:,.1f}) "
            f"but it's still {ratio:.1f}× off from expected ~{avg_est:,.1f}. "
            "Try joining another table or using a completely different metric column."
        )


def _fuzzy_match(db_value: str, vision_label: str, threshold: float = 0.55) -> bool:
    """
    Word-overlap check between a DB value string and a vision-extracted label.
    Returns True when Jaccard similarity of word sets ≥ threshold.
    """
    dw = set(re.sub(r"[^a-z0-9 ]", "", db_value.lower()).split())
    vw = set(re.sub(r"[^a-z0-9 ]", "", vision_label.lower()).split())
    if not dw or not vw:
        return db_value.lower().strip() == vision_label.lower().strip()
    union = dw | vw
    return len(dw & vw) / len(union) >= threshold if union else False
