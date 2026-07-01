"""
ToolAgent — the multi-turn tool-use loop that powers every skill agent.

Flow per turn:
    1. Call bedrock_invoke_with_tools() with the current message history + tool schemas.
    2. If stop_reason == "end_turn"  → extract text blocks → return final answer.
    3. If stop_reason == "tool_use"  → dispatch each tool_use block → append
       tool_result blocks → continue to next turn.
    4. Repeat until end_turn or max_turns is exceeded.

Phase 5 adds a confirmation gate for write tools (create_note, update_status).
The gate is pre-wired as commented-out code here — uncomment in Phase 5.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from shared.bedrock_client import bedrock_invoke_with_tools, BEDROCK_SONNET_MODEL
from agent_service.tools import registry, dispatcher


# ── Context object ────────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """Carries all per-request state that tool implementations may need.

    Passed as the second argument to every tool function:
        async def my_tool(inp: dict, ctx: AgentContext) -> dict
    """
    project_id:    str
    connection_id: str          # DB connection for run_sql and write tools
    job_id:        str          # pipeline job ID for logging and WS emit
    db:            Any          # sqlalchemy AsyncSession (for DB-write tools)
    redis:         Any          # redis client (for future pub/sub use)
    emit:          Callable     # async (event: dict) → None  (WebSocket broadcast)
    # Injected by orchestrator from the schema cache + GraphRAG ranking.
    # [{name: "schema.table", columns: ["col1", "col2", ...]}] ordered by relevance.
    # Empty list when schema is unavailable or not yet fetched.
    schema_tables: list = field(default_factory=list)
    # Brainwave user profile loaded from brainwave_user_profiles table.
    # Shape: {user_email, brainwave_role, db_name, qualifier_id, can_impersonate}
    # None when the user has no profile (DEV_MODE passthrough) or is a VP/admin
    # who needs no per-user filter.
    user_profile: dict | None = None


# ── Write-tool gate ──────────────────────────────────────────────────────────
# Populated in Phase 5 (confirmation_manager.py).
# Any tool name listed here will pause the agent and request user confirmation
# before the tool is actually executed.
_WRITE_TOOLS: frozenset[str] = frozenset()


# ── Main agent class ─────────────────────────────────────────────────────────

class ToolAgent:
    """Multi-turn tool-use agent.

    Args:
        system_prompt: The skill-specific system prompt (defines agent persona + rules).
        tool_names:    Names of tools this agent is allowed to call.
                       Schemas are fetched from registry.get_schemas(tool_names).
        ctx:           Runtime context for this request.
        max_turns:     Maximum Bedrock→dispatch→Bedrock cycles before giving up.
    """

    def __init__(
        self,
        system_prompt: str,
        tool_names: list[str],
        ctx: AgentContext,
        max_turns: int = 8,
    ) -> None:
        self.system_prompt = system_prompt
        self.tool_names    = tool_names
        self.tools         = registry.get_schemas(tool_names)
        self.ctx           = ctx
        self.max_turns     = max_turns

    async def run(self, user_text: str) -> str:
        """Run the tool-use loop and return the final plain-text answer."""
        messages: list[dict] = [{"role": "user", "content": user_text}]
        t0 = time.time()

        for turn in range(self.max_turns):

            await self.ctx.emit({
                "type":   "agent.thinking",
                "job_id": self.ctx.job_id,
                "turn":   turn + 1,
            })

            # ── LLM call ─────────────────────────────────────────────────────
            resp = await bedrock_invoke_with_tools(
                model_id=BEDROCK_SONNET_MODEL,
                system_prompt=self.system_prompt,
                messages=messages,
                tools=self.tools,
                max_tokens=4096,
                temperature=0.2,
            )

            stop   = resp["stop_reason"]
            content = resp["content"]

            # Always keep message history so the LLM has context on next turn.
            messages.append({"role": "assistant", "content": content})

            print(
                f"[agent:{self.ctx.job_id}] turn={turn + 1}  stop={stop!r}  "
                f"elapsed={time.time() - t0:.1f}s",
                flush=True,
            )

            # ── Done — return text answer ─────────────────────────────────────
            if stop == "end_turn":
                return _extract_text(content)

            # ── Tool calls ───────────────────────────────────────────────────
            if stop == "tool_use":
                tool_results: list[dict] = []

                for block in content:
                    if block.get("type") != "tool_use":
                        continue

                    tool_name  = block["name"]
                    tool_input = block["input"]
                    use_id     = block["id"]

                    print(
                        f"[agent:{self.ctx.job_id}] → tool={tool_name!r}  "
                        f"input_keys={list(tool_input.keys())}",
                        flush=True,
                    )
                    await self.ctx.emit({
                        "type":   "agent.tool_call",
                        "job_id": self.ctx.job_id,
                        "tool":   tool_name,
                        "turn":   turn + 1,
                    })

                    # ── Phase 5: confirmation gate for write tools ────────────
                    # When Phase 5 is implemented, uncomment this block and
                    # populate _WRITE_TOOLS in confirmation_manager.py:
                    #
                    # if tool_name in _WRITE_TOOLS:
                    #     from agent_service.agents.confirmation_manager import (
                    #         _WRITE_TOOLS as _WT,
                    #         request_confirmation,
                    #     )
                    #     _WRITE_TOOLS = _WT   # sync the set
                    #     approved = await request_confirmation(
                    #         self.ctx.job_id, tool_name, tool_input, self.ctx.emit
                    #     )
                    #     if not approved:
                    #         tool_results.append({
                    #             "type":        "tool_result",
                    #             "tool_use_id": use_id,
                    #             "content":     "Action was cancelled by the user.",
                    #         })
                    #         continue

                    result = await dispatcher.dispatch(tool_name, tool_input, self.ctx)

                    print(
                        f"[agent:{self.ctx.job_id}] ← tool={tool_name!r}  "
                        f"rows={result.get('row_count') if isinstance(result, dict) else '?'}  "
                        f"error={bool((result or {}).get('error'))}",
                        flush=True,
                    )
                    await self.ctx.emit({
                        "type":      "agent.tool_result",
                        "job_id":    self.ctx.job_id,
                        "tool":      tool_name,
                        "row_count": (result or {}).get("row_count"),
                        "has_error": bool((result or {}).get("error")),
                    })

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": use_id,
                        "content":     json.dumps(result, default=str),
                    })

                # Feed all tool results back to Claude
                messages.append({"role": "user", "content": tool_results})
                continue

            # Unexpected stop reason (e.g. "max_tokens") — break and return
            # whatever text we can extract from the last assistant turn.
            print(
                f"[agent:{self.ctx.job_id}] unexpected stop_reason={stop!r}  "
                "extracting partial answer",
                flush=True,
            )
            break

        # ── Fallback: extract text from last assistant message ────────────────
        last_assistant = next(
            (m for m in reversed(messages) if m.get("role") == "assistant"),
            None,
        )
        if last_assistant:
            text = _extract_text(last_assistant["content"])
            if text:
                return text

        return "I reached the maximum number of reasoning steps without completing the request. Please try a more specific question."


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(content: list[dict]) -> str:
    """Join all text-type content blocks into a single string."""
    parts = [
        b["text"]
        for b in content
        if isinstance(b, dict)
        and b.get("type") == "text"
        and b.get("text", "").strip()
    ]
    return "\n\n".join(parts)
