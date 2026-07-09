"""
Lightweight heuristic table-role classification for finance-domain skill agents.

Pure keyword matching over table names — no LLM, negligible cost — computed
fresh on every skill-agent invocation from the already-in-memory
EnrichedSchema.compact_tables (no caching needed; there's nothing expensive to
cache). This is the token-optimization companion to the finance skill agents
(finance_*_agent.py): every agent's persona gets pre-resolved candidate table
names injected directly, instead of spending a tool-call turn discovering
"which table is the transaction table" via run_sql on every single invocation.

A table can match more than one role (e.g. "account_transactions" could
plausibly be both) — callers should treat these as ranked hints for the LLM
to verify against actual columns, not a strict partition.
"""
from __future__ import annotations

import re

_ROLE_KEYWORDS: dict[str, frozenset[str]] = {
    "transaction_table": frozenset({
        "transaction", "transactions", "trans", "txn", "txns", "payment",
        "payments", "entry", "entries",
    }),
    "account_table": frozenset({
        "account", "accounts", "acct",
    }),
    "customer_table": frozenset({
        "customer", "customers", "client", "clients", "party", "parties",
    }),
    "ledger_table": frozenset({
        "ledger", "gl", "generalledger", "journal",
    }),
    "risk_table": frozenset({
        "risk", "fraud", "flag", "flags", "alert", "alerts", "score", "scores",
        "exception", "exceptions",
    }),
    "statement_table": frozenset({
        "statement", "statements", "invoice", "invoices", "billing",
    }),
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def classify_table_roles(compact_tables: list[dict] | None) -> dict[str, list[str]]:
    """Return {role: [table_name, ...]} for tables whose name matches a role's
    keywords, ordered as found. Empty dict when nothing matches — callers must
    always fall back to plain table discovery in that case."""
    if not compact_tables:
        return {}
    roles: dict[str, list[str]] = {role: [] for role in _ROLE_KEYWORDS}
    for t in compact_tables:
        name = t.get("name", "")
        tokens = _tokenize(name)
        for role, keywords in _ROLE_KEYWORDS.items():
            if tokens & keywords:
                roles[role].append(name)
    return {role: names for role, names in roles.items() if names}


def format_table_roles(roles: dict[str, list[str]]) -> str:
    """Render classify_table_roles() output as a short preamble block."""
    if not roles:
        return ""
    lines = ["## Table role hints (heuristic — verify against actual columns before trusting):"]
    for role, names in roles.items():
        lines.append(f"  {role}: {', '.join(names[:6])}")
    return "\n".join(lines)
