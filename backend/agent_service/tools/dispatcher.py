"""
Tool dispatcher — routes tool_use blocks from Bedrock to their Python implementations.

Each phase uncomments (or adds) its elif branch here. The AgentContext carries
project_id, connection_id, db, redis, and emit — all tools receive it so they can
access whatever they need without extra arguments.

Write tools (create_note, update_status) are gated behind the confirmation manager
starting from Phase 5. The gate is a commented-out block here; Phase 5 activates it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_service.agents.tool_agent import AgentContext


async def dispatch(name: str, inp: dict, ctx: "AgentContext") -> dict:
    """Dispatch a single tool call by name.

    Args:
        name:  The tool name from the Bedrock tool_use content block.
        inp:   The parsed input dict from the tool_use block.
        ctx:   The AgentContext for this request (carries db, connection_id, etc.)

    Returns:
        A JSON-serialisable dict that is sent back to Claude as tool_result content.
        On error, returns {"error": "...", ...} rather than raising — Claude will
        receive the error text and can decide how to proceed.
    """

    # ── Phase 0: SQL ─────────────────────────────────────────────────────────
    if name == "run_sql":
        from agent_service.tools.sql_tool import run_sql
        return await run_sql(inp, ctx)

    # ── Phase 1: candidate scoring + shortlisting ───────────────────────────
    elif name == "get_candidate_scores":
        from agent_service.tools.candidate_tools import get_candidate_scores
        return await get_candidate_scores(inp, ctx)

    elif name == "list_candidates":
        from agent_service.tools.candidate_tools import list_candidates
        return await list_candidates(inp, ctx)

    elif name == "build_shortlist":
        from agent_service.tools.candidate_tools import build_shortlist
        return await build_shortlist(inp, ctx)

    # score_candidate (Lambda 1 on-demand invoke) — reserved for Phase 4
    # elif name == "score_candidate":
    #     from agent_service.tools.lambda_bridge import score_candidate
    #     return await score_candidate(inp, ctx)

    # ── Phase 2: pipeline / briefing tools ──────────────────────────────────
    elif name == "get_my_placements":
        from agent_service.tools.pipeline_tools import get_my_placements
        return await get_my_placements(inp, ctx)

    elif name == "get_pipeline_summary":
        from agent_service.tools.pipeline_tools import get_pipeline_summary
        return await get_pipeline_summary(inp, ctx)

    elif name == "get_recent_activity":
        from agent_service.tools.pipeline_tools import get_recent_activity
        return await get_recent_activity(inp, ctx)

    elif name == "get_jobs_dashboard":
        from agent_service.tools.pipeline_tools import get_jobs_dashboard
        return await get_jobs_dashboard(inp, ctx)

    # ── Phase 4: Lambda 2 / S3 (resume + profile) ───────────────────────────
    # if name == "parse_resume":
    #     from agent_service.tools.lambda_bridge import parse_resume
    #     return await parse_resume(inp, ctx)
    #
    # if name == "get_candidate_profile":
    #     from agent_service.tools.lambda_bridge import get_candidate_profile
    #     return await get_candidate_profile(inp, ctx)

    # ── Phase 5: write tools (gated by confirmation manager) ─────────────────
    # Write tools are checked BEFORE dispatch in tool_agent.py — if the user
    # cancels, a tool_result with "Action cancelled" is returned without calling
    # these functions. The confirmation gate lives in tool_agent.py, not here.
    #
    # if name == "create_note":
    #     from agent_service.tools.record_tools import create_note
    #     return await create_note(inp, ctx)
    #
    # if name == "update_status":
    #     from agent_service.tools.record_tools import update_status
    #     return await update_status(inp, ctx)

    # ── Phase 8: export ──────────────────────────────────────────────────────
    # if name == "trigger_export":
    #     from agent_service.tools.candidate_tools import trigger_export
    #     return await trigger_export(inp, ctx)

    # ── Unknown tool ─────────────────────────────────────────────────────────
    return {
        "error": f"Unknown tool: {name!r}. This tool has not been implemented yet.",
        "available_tools": [
            "run_sql",
            "get_candidate_scores", "list_candidates", "build_shortlist",
            "get_my_placements",
            "get_pipeline_summary", "get_recent_activity", "get_jobs_dashboard",
        ],
    }
