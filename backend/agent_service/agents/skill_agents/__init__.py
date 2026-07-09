"""
Skill agent registry.

Agents are scoped per project `domain` (see agent_service.agents.domain_config) so
a "recruitment" persona never fires for a "finance" project and vice versa. Each
skill agent module registers itself by calling register() at module load:

    # at the bottom of match_agent.py:
    from agent_service.agents.skill_agents import register
    register("recruitment", "MATCH", run)

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

_REGISTRY: dict[str, dict[str, Callable]] = {}


def register(domain: str, intent_type: str, fn: Callable) -> None:
    """Register an async run(user_text: str, ctx: AgentContext) → str function
    for this (domain, intent_type) pair."""
    _REGISTRY.setdefault(domain, {})[intent_type] = fn
    print(f"[skill_agents] registered agent domain={domain!r} intent={intent_type!r}", flush=True)


def get_agent(domain: str, intent_type: str) -> Callable:
    """Return the run function for this (domain, intent_type) pair.

    Falls back to a "coming soon" stub if no agent is registered for this
    domain/intent (e.g. a future phase, or a domain with no skill agents at
    all), so the user gets a helpful message instead of a 500 error.
    """
    fn = _REGISTRY.get(domain, {}).get(intent_type)
    if fn is not None:
        return fn

    # Stub for unimplemented/unsupported domain-intent combinations
    async def _coming_soon(user_text: str, ctx: AgentContext) -> str:
        return (
            f"The **{intent_type.title().replace('_', ' ')}** skill is not yet available "
            f"for this project's domain. I can still help you analyse data — try asking "
            "a chart or data question."
        )

    return _coming_soon


def registered_intents(domain: str | None = None) -> list[str]:
    """Return registered (domain, intent_type) pairs, or just intent types for
    one domain when `domain` is given (debugging / health check)."""
    if domain is not None:
        return list(_REGISTRY.get(domain, {}).keys())
    return [f"{d}:{i}" for d, intents in _REGISTRY.items() for i in intents]


# ── Auto-import skill agent modules ──────────────────────────────────────────
# Add the module path here when each phase's/domain's agent is implemented.
# The list is evaluated once at service startup.

_SKILL_MODULES: list[str] = [
    # Recruitment domain
    "agent_service.agents.skill_agents.match_agent",
    "agent_service.agents.skill_agents.briefing_agent",
    "agent_service.agents.skill_agents.prospect_agent",
    "agent_service.agents.skill_agents.audit_agent",
    "agent_service.agents.skill_agents.screen_agent",
    "agent_service.agents.skill_agents.enrich_agent",
    "agent_service.agents.skill_agents.verify_agent",
    "agent_service.agents.skill_agents.present_agent",
    # Finance domain
    "agent_service.agents.skill_agents.finance_match_agent",
    "agent_service.agents.skill_agents.finance_briefing_agent",
    "agent_service.agents.skill_agents.finance_audit_agent",
    "agent_service.agents.skill_agents.finance_prospect_agent",
    "agent_service.agents.skill_agents.finance_enrich_agent",
    "agent_service.agents.skill_agents.finance_reconcile_agent",
    "agent_service.agents.skill_agents.finance_anomaly_agent",
    "agent_service.agents.skill_agents.finance_forecast_agent",
    "agent_service.agents.skill_agents.finance_network_agent",
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
