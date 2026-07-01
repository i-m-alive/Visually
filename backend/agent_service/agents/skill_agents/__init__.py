"""
Skill agent registry.

Each skill agent module registers itself by calling register() at module load:

    # at the bottom of match_agent.py:
    from agent_service.agents.skill_agents import register
    register("MATCH", run)

This module auto-imports every known agent module so registration happens on
service startup. ImportError is silently ignored — an unimplemented agent falls
back to a "coming soon" stub rather than crashing the service.
"""
from __future__ import annotations

import importlib
import sys
from typing import Callable

from agent_service.agents.tool_agent import AgentContext  # re-exported for convenience

# ── Registry ─────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, Callable] = {}


def register(intent_type: str, fn: Callable) -> None:
    """Register an async run(user_text: str, ctx: AgentContext) → str function."""
    _REGISTRY[intent_type] = fn
    print(f"[skill_agents] registered agent for intent={intent_type!r}", flush=True)


def get_agent(intent_type: str) -> Callable:
    """Return the run function for this intent type.

    Falls back to a "coming soon" stub if the agent module hasn't been
    implemented yet (i.e. a future phase), so the user gets a helpful message
    instead of a 500 error.
    """
    fn = _REGISTRY.get(intent_type)
    if fn is not None:
        return fn

    # Stub for unimplemented future agents
    async def _coming_soon(user_text: str, ctx: AgentContext) -> str:
        return (
            f"The **{intent_type.title().replace('_', ' ')}** skill is not yet available. "
            "I can still help you analyse data — try asking a chart or data question."
        )

    return _coming_soon


def registered_intents() -> list[str]:
    """Return the intent types that have a registered agent (debugging / health check)."""
    return list(_REGISTRY.keys())


# ── Auto-import skill agent modules ──────────────────────────────────────────
# Add the module path here when each phase's agent is implemented.
# The list is evaluated once at service startup.

_SKILL_MODULES: list[str] = [
    # Phase 1
    "agent_service.agents.skill_agents.match_agent",
    # Phase 2
    "agent_service.agents.skill_agents.briefing_agent",
    # Phase 3
    "agent_service.agents.skill_agents.prospect_agent",
    "agent_service.agents.skill_agents.audit_agent",
    # Phase 5
    "agent_service.agents.skill_agents.screen_agent",
    # Phase 6
    "agent_service.agents.skill_agents.enrich_agent",
    # Phase 7
    "agent_service.agents.skill_agents.verify_agent",
    # Phase 8
    "agent_service.agents.skill_agents.present_agent",
]


def _load_all() -> None:
    for module_path in _SKILL_MODULES:
        try:
            importlib.import_module(module_path)
        except ImportError:
            # Agent not implemented yet — silently skip.
            pass
        except Exception as exc:
            # Broken agent file (syntax error, missing dep, etc.) — log and continue.
            # We don't want one bad agent to prevent the other agents from loading.
            print(
                f"[skill_agents] WARNING: failed to load {module_path}: {exc}",
                file=sys.stderr,
                flush=True,
            )


_load_all()
