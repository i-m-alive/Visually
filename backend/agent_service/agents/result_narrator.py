"""Result narrator — turns a query result into a human-facing explanation.

Two modes (driven by IntentResult.output_mode):
  - "chart": a concise 2-4 sentence caption describing what the chart shows and the
             single most important takeaway, using the real numbers.
  - "text":  a direct prose answer to the user's question (no chart), using the data.

One Bedrock (Sonnet) call. Fully non-fatal: returns "" on any error so the pipeline
still completes (the frontend falls back to the raw result).
"""
import json
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

# Cap rows sent to the LLM so a large result set can't blow the token budget.
_MAX_SAMPLE_ROWS = 50

_SYSTEM_CHART = (
    "You are a data analyst writing a clear, insightful explanation for a chart just generated "
    "from the user's question. "
    "CRITICAL: derive ALL facts — especially dates and time periods — from rows_sample and "
    "actual_data_range (if provided), NOT from user_question. "
    "If the user asked for 2015-2026 but actual_data_range shows 2021-2026, write '2021 to 2026'. "
    "Write exactly 2-3 sentences covering: "
    "(1) what the chart shows — the real time period (from actual_data_range) or breakdown; "
    "(2) the key finding — peak value, trend direction, or most notable comparison, "
    "using actual numbers from rows_sample; "
    "(3) a brief business implication in practical terms. "
    "No preamble, no bullet points, no markdown. Output only the explanation text."
)

def _extract_date_range(rows: list, columns: list) -> str | None:
    """Return 'YYYY to YYYY' from the actual result rows.

    Handles float years (2021.0 → 2021), non-string columns, and falls back to
    scanning column values when the column name doesn't contain a date keyword.
    """
    # Primary: find column by name
    date_col = next(
        (c for c in columns if isinstance(c, str) and
         any(k in c.lower() for k in ["year", "date", "month", "period", "quarter", "week"])),
        None,
    )
    # Fallback: look for a column whose values are all 4-digit years
    if not date_col and rows:
        for c in columns:
            if not isinstance(c, str):
                continue
            probe = [rows[i].get(c) for i in range(min(5, len(rows)))]
            probe = [v for v in probe if v is not None]
            if probe and all(isinstance(v, (int, float)) and 1990 <= float(v) <= 2100 for v in probe):
                date_col = c
                break

    if not date_col or not rows:
        return None

    year_ints = []
    for r in rows:
        v = r.get(date_col)
        if v is not None:
            try:
                year_ints.append(int(float(str(v))))
            except (ValueError, TypeError):
                pass

    if len(year_ints) >= 2:
        return f"{min(year_ints)} to {max(year_ints)}"
    if len(year_ints) == 1:
        return str(year_ints[0])
    return None


_SYSTEM_TEXT = (
    "You are a data analyst answering the user's question DIRECTLY in prose, using the query "
    "result. Answer in 1-3 sentences and include the actual number(s). Do NOT describe a chart "
    "or say 'the chart shows'. No preamble, no markdown. Output only the answer."
)


def _range_mismatch_note(user_question: str, actual_range: str | None) -> str:
    """Return a warning note when the user asked for data starting before what's available.

    E.g. user asked '2015 to 2026' but data only starts from 2021 → returns a note.
    Returns '' when there's no mismatch or not enough info to compare.
    """
    if not actual_range:
        return ""
    import re as _re_note
    user_years = [int(y) for y in _re_note.findall(r'\b(19\d{2}|20\d{2})\b', user_question)]
    range_years = [int(y) for y in _re_note.findall(r'\b(19\d{2}|20\d{2})\b', actual_range)]
    if not user_years or not range_years:
        return ""
    requested_start = min(user_years)
    actual_start = min(range_years)
    if actual_start > requested_start:
        return (
            f"Note: Data is only available from {actual_start} — "
            f"no records exist before {actual_start} in this dataset. "
        )
    return ""


async def narrate(
    user_text: str,
    query_plan,            # QueryPlan — has .chart_type, .title, .sql
    execute_result: dict,  # {"rows": [...], "columns": [...], "row_count": int}
    output_mode: str = "chart",
) -> str:
    rows = (execute_result or {}).get("rows") or []
    columns = (execute_result or {}).get("columns") or []
    sample = rows[:_MAX_SAMPLE_ROWS]

    payload = {
        "user_question": user_text,
        "chart_type": getattr(query_plan, "chart_type", None),
        "title": getattr(query_plan, "title", None),
        "columns": columns,
        "row_count": (execute_result or {}).get("row_count", len(rows)),
        "rows_sample": sample,
    }
    actual_range = _extract_date_range(sample, columns)
    if actual_range:
        payload["actual_data_range"] = actual_range
    system = _SYSTEM_TEXT if output_mode == "text" else _SYSTEM_CHART
    note = _range_mismatch_note(user_text, actual_range)

    try:
        raw = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=system,
            user_message=json.dumps(payload, default=str)[:12000],
            max_tokens=400,
            temperature=0.2,
        )
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        return (note + text).strip() if note else text
    except Exception as exc:
        print(f"[result_narrator] narration failed (non-fatal): {exc}", flush=True)
        return note.strip() if note else ""


async def narrate_stream(
    user_text: str,
    query_plan,            # QueryPlan — has .chart_type, .title, .sql
    execute_result: dict,  # {"rows": [...], "columns": [...], "row_count": int}
    output_mode: str = "chart",
):
    """Streaming variant: yields text tokens as they arrive from Bedrock.
    Use this when you want to emit narrative tokens in real-time (e.g. for
    'narrative.token' WebSocket events). Falls back silently on any error."""
    from shared.bedrock_client import bedrock_invoke_stream
    rows = (execute_result or {}).get("rows") or []
    columns = (execute_result or {}).get("columns") or []
    sample = rows[:_MAX_SAMPLE_ROWS]

    payload = {
        "user_question": user_text,
        "chart_type": getattr(query_plan, "chart_type", None),
        "title": getattr(query_plan, "title", None),
        "columns": columns,
        "row_count": (execute_result or {}).get("row_count", len(rows)),
        "rows_sample": sample,
    }
    actual_range = _extract_date_range(sample, columns)
    if actual_range:
        payload["actual_data_range"] = actual_range
    system = _SYSTEM_TEXT if output_mode == "text" else _SYSTEM_CHART
    note = _range_mismatch_note(user_text, actual_range)
    system_blocks = [{"type": "text", "text": system}]
    messages = [{"role": "user", "content": json.dumps(payload, default=str)[:12000]}]

    if note:
        yield note
    try:
        async for kind, token in bedrock_invoke_stream(
            BEDROCK_SONNET_MODEL, system_blocks, messages, 400, 0.2
        ):
            if kind == "text":
                yield token
    except Exception as exc:
        print(f"[result_narrator] stream narration failed: {exc}", flush=True)


async def narrate_from_result(
    user_text: str,
    final_result: dict,
    output_mode: str = "chart",
) -> str:
    """Narrate from a final_result dict (used when query_plan is not available,
    e.g. the multi-candidate path). Falls back gracefully to '' on error."""
    chart_data = final_result.get("chart_data") or {}
    rows = chart_data.get("rows") or []
    columns = chart_data.get("columns") or []
    sample = rows[:_MAX_SAMPLE_ROWS]

    payload = {
        "user_question": user_text,
        "chart_type": final_result.get("chart_type"),
        "title": final_result.get("title"),
        "columns": columns,
        "row_count": len(rows),
        "rows_sample": sample,
    }
    actual_range = _extract_date_range(sample, columns)
    if actual_range:
        payload["actual_data_range"] = actual_range
    system = _SYSTEM_TEXT if output_mode == "text" else _SYSTEM_CHART
    note = _range_mismatch_note(user_text, actual_range)

    try:
        raw = await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=system,
            user_message=json.dumps(payload, default=str)[:12000],
            max_tokens=400,
            temperature=0.2,
        )
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        return (note + text).strip() if note else text
    except Exception as exc:
        print(f"[result_narrator] result-dict narration failed: {exc}", flush=True)
        return note.strip() if note else ""
