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
    "You are a data analyst writing a SHORT caption for a chart that was just generated "
    "from the user's question. In 2-4 sentences, plainly state what the chart shows and the "
    "single most important takeaway — the peak, the trend direction, the largest category, "
    "the total, or a notable comparison — using the actual numbers from the data. "
    "No preamble, no bullet points, no markdown headings. Output only the caption text."
)

_SYSTEM_TEXT = (
    "You are a data analyst answering the user's question DIRECTLY in prose, using the query "
    "result. Answer in 1-3 sentences and include the actual number(s). Do NOT describe a chart "
    "or say 'the chart shows'. No preamble, no markdown. Output only the answer."
)


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
    system = _SYSTEM_TEXT if output_mode == "text" else _SYSTEM_CHART

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
        return text
    except Exception as exc:
        print(f"[result_narrator] narration failed (non-fatal): {exc}", flush=True)
        return ""
