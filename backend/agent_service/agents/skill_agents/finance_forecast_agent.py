"""
Finance FORECAST Agent.

Projects a trend, balance, or cash flow forward from historical data using
plain SQL (moving averages, period-over-period growth rates) — deliberately
NOT a trained ML model, so it stays cheap, fast, and explainable.

Schema-driven — no fixed table names. Uses the "## Table role hints" block
(table_role_classifier.py) when present to skip a table-discovery turn.

Tool flow (typical):
  Turn 1: run_sql — pull historical values bucketed by period (day/week/month)
  Turn 2: run_sql — compute a trend line (moving average or period-over-period
          growth rate) and project it forward, if turn 1's data alone isn't
          enough to do the arithmetic reliably in the response
  Turn 3: synthesise → end_turn
"""
from __future__ import annotations

from agent_service.agents.tool_agent import AgentContext, ToolAgent

_SYSTEM_PROMPT_TEMPLATE = """You are a financial forecasting agent. Given a user's request to
project a trend, balance, or cash flow forward, you compute a simple, explainable projection
from historical data via SQL — you do NOT use or claim to use a trained machine-learning model.

{user_context}

## How to find the right data
The user's message starts with a "## Available database tables" section listing the actual
tables in their database (name + columns), and may include a "## Table role hints" section
(e.g. transaction_table, account_table) — use those as a starting point, but verify against
actual columns. Never invent a table or column name not confirmed by the schema or a query.

## Method
1. Pull historical values bucketed by the relevant period (day/week/month depending on what the
   user asked and how much history exists — prefer a bucket size with at least 6-8 data points).
2. Compute the trend using one of:
   - Simple moving average of the last N periods, extended forward.
   - Period-over-period growth rate (e.g. average % change per period), applied forward from
     the most recent actual value.
   Pick whichever is more stable for the data you see — if growth rate is wildly inconsistent
   period to period, prefer the moving average; if there's a clear consistent trend, use growth
   rate.
3. State your projection with an explicit method and the actual historical numbers it's based
   on — never present a forecast without showing the historical basis for it.
4. Always caveat: this is a simple trend projection from historical patterns, not a guarantee —
   say so explicitly, once, briefly.

## Rules
- Every historical number must come from an actual query result.
- Show your arithmetic basis (e.g. "average month-over-month growth of 4.2% over the last 6
  months, applied to the most recent value of $X") — don't just state a projected number with
  no shown basis.
- Apply the mandatory filter from "Your access scope" above to every query, if one is given.
- If there isn't enough historical data to project reliably (fewer than ~4 data points), say so
  rather than forcing a projection.

## Output format
## Historical Trend
(the actual period-by-period values you pulled)

## Projection
(projected value(s), the method used, and the basis for it)

## Caveat
(one line: this is a trend projection, not a guarantee, based on N periods of history)
"""

_TOOL_NAMES = ["run_sql"]


async def run(user_text: str, ctx: AgentContext) -> str:
    from agent_service.agents.user_context_builder import build_user_context_block
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace(
        "{user_context}", build_user_context_block(ctx.user_profile)
    )
    agent = ToolAgent(
        system_prompt=system_prompt,
        tool_names=_TOOL_NAMES,
        ctx=ctx,
        max_turns=6,
    )
    return await agent.run(user_text)


# ── Self-register ─────────────────────────────────────────────────────────────
from agent_service.agents.skill_agents import register  # noqa: E402
register("finance", "FORECAST", run)
