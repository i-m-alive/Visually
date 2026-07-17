"""
ACTION Agent — read-only handler for write-style requests (create / update /
delete / tag a record).

The platform is a read-only analytics assistant: write SQL is blocked at two
independent sandbox layers on purpose, and this agent deliberately does NOT hold
the run_sql tool, so it is structurally incapable of executing anything against
the connected database. Instead of the old terse "skill not yet available" stub,
it gives a genuinely useful response — it drafts the exact SQL the user could run
themselves, grounded in their real schema, and is explicit that nothing was
executed. For destructive operations (DELETE / DROP / TRUNCATE) it adds a clear
irreversibility warning.

Registered for both skill domains (finance + recruitment). Domain-agnostic: it
reads the "## Available database tables" block the orchestrator injects into the
user message rather than assuming any fixed schema.
"""
from __future__ import annotations

from agent_service.agents.tool_agent import AgentContext
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_SYSTEM_PROMPT = """You are the assistant for a READ-ONLY data-analytics platform.
The user has asked you to create, update, delete, tag, or otherwise CHANGE a record.

You cannot and must not perform writes: this platform only reads data, and write
access to the connected database is intentionally disabled. Your job is to be
maximally helpful WITHOUT executing anything.

Respond with exactly this structure, in plain language:

1. One short sentence acknowledging what they want to do and stating plainly that
   you're a read-only analytics assistant, so you won't change the database
   directly.
2. The exact SQL statement(s) that WOULD accomplish it, in a fenced code block,
   grounded in the user's ACTUAL schema. The user's message contains a
   "## Available database tables" section listing real tables and columns — use
   ONLY table/column names that appear there. If you're unsure which table or
   column is correct, say so and show your best guess clearly labelled as a guess.
3. A one-line offer to double-check the target table/columns against their schema
   before they run it.

Hard rules:
- NEVER claim you created, updated, deleted, or changed anything. You did not.
- NEVER say the action is "done", "complete", or "saved".
- If the request is a DELETE / DROP / TRUNCATE (or otherwise destructive), add a
  short, bold warning that it is irreversible and should be run only against the
  right environment with a backup — and, when you can tell from context, note
  roughly how many rows it would affect.
- Do not invent columns. If a needed column (e.g. a name column) isn't in the
  available-tables list, point that out instead of fabricating one.
- Keep it tight. No filler. Real table/column names only.
"""


async def run(user_text: str, ctx: AgentContext) -> str:
    # Single, tool-less LLM call. No run_sql / write tool is available to this
    # agent, so there is no code path by which it can mutate the database — the
    # read-only guarantee is structural, not just prompt-based.
    try:
        return await bedrock_invoke(
            model_id=BEDROCK_SONNET_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            user_message=user_text,
            max_tokens=1200,
            temperature=0.1,
        )
    except Exception as exc:
        print(f"[action_agent] generation failed (non-fatal): {exc}", flush=True)
        return (
            "I'm a read-only analytics assistant, so I can't create, update, or "
            "delete records in your database directly. I can still help you draft "
            "the SQL to do it yourself, or answer any analytical question about "
            "your data — try rephrasing what you'd like to see."
        )


# ── Self-register (both skill domains) ────────────────────────────────────────
from agent_service.agents.skill_agents import register  # noqa: E402
register("finance", "ACTION", run)
register("recruitment", "ACTION", run)
