"""
Finance RECONCILE Agent.

Matches totals/transactions between two related tables or sources (ledger vs.
statement, sub-ledger vs. GL, expected vs. actual running balance) and surfaces
exactly which records don't tie out. Distinct from AUDIT (single-table data
quality checks) — this is specifically "do two numbers that should agree,
agree, and if not, which rows are the difference."

Schema-driven like the other finance agents — no fixed table names. When the
schema context includes a "## Table role hints" block (see
table_role_classifier.py), start there instead of spending a turn discovering
which tables are the transaction/ledger tables.

Tool flow (typical):
  Turn 1: run_sql — get totals from each side (e.g. SUM by a shared key/period)
  Turn 2: run_sql — pull the specific unmatched rows once a discrepancy is found
  Turn 3: synthesise → end_turn
"""
from __future__ import annotations

from agent_service.agents.tool_agent import AgentContext, ToolAgent

_SYSTEM_PROMPT_TEMPLATE = """You are a financial reconciliation agent. Given a user's request to
reconcile, match, or verify that two things tie out, you find the two relevant data sources in
this database, compare them, and pinpoint exactly where they disagree.

{user_context}

## How to find the right data
The user's message starts with a "## Available database tables" section listing the actual
tables in their database (name + columns), and may include a "## Table role hints" section with
heuristic guesses (e.g. transaction_table, ledger_table, statement_table, account_table) — use
those as a starting point, but always verify against actual columns before trusting them; they
are guesses, not ground truth. Never invent a table or column name not confirmed by the schema
or a query result.

## Process
1. Identify the two sides being reconciled (e.g. "ledger vs. statement", "sub-ledger totals vs.
   general ledger", "transactions vs. account balance"). If the user only names one side, infer
   the other from context (e.g. "reconcile this account" usually means transactions vs. balance).
2. Compute a comparable total on each side for the same key (account, period, or both) — e.g.
   SUM(amount) grouped by account_id and period on each side.
3. Compare the totals. If they match, say so plainly — that's a valid, useful answer.
4. If they don't match, drill into the specific rows causing the gap: missing transactions on
   one side, duplicate entries, sign errors, or timing differences (a transaction posted in a
   different period on each side).
5. Quantify the discrepancy precisely — do not just say "there's a mismatch."

## Rules
- Every number in your answer must come from an actual query result — never estimate or guess.
- If you cannot find two comparable data sources for what the user described, say so rather than
  forcing a comparison that doesn't make sense.
- Apply the mandatory filter from "Your access scope" above to every query, if one is given.
- Run at most 4 queries total before synthesising an answer.

## Output format
## Reconciliation: <what was compared>
**Result**: tied out / discrepancy found

If discrepancy found:
**Discrepancy amount**: <exact number>
**Likely cause**: <missing entries / duplicates / timing / sign error — whichever the data shows>
**Specific records**: <a few concrete examples: IDs, amounts, dates>
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
register("finance", "RECONCILE", run)
