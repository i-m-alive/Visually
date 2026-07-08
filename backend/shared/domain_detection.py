"""
Heuristic project-domain detection from a freshly crawled schema.

Runs synchronously right after a schema crawl completes (see
schema_crawler/main.py's _run_crawl), using the raw crawler output
(table/column names + the crawler's own lightweight descriptions) — it does
NOT wait on the slow LLM metadata-enrichment pass.

Deliberately a keyword heuristic, not an LLM call: it needs to run on every
crawl for free, synchronously, with no added latency or cost.
"""
from __future__ import annotations

import re

# Keep these in sync with agent_service.agents.domain_config.VALID_DOMAINS —
# duplicated here (rather than imported) so schema_crawler doesn't need a
# dependency on the agent_service package.
_RECRUITMENT_KEYWORDS = frozenset({
    "candidate", "candidates", "applicant", "applicants", "application", "applications",
    "placement", "placements", "joborder", "job_order", "recruiter", "recruiting",
    "interview", "resume", "hire", "hiring", "hired", "bullhorn", "qualifier",
    "screening", "submission", "clientadvisor", "placementspecialist",
    "relationshipmanager", "vacancy", "jobposting", "job_posting", "onboarding",
})

_FINANCE_KEYWORDS = frozenset({
    "transaction", "transactions", "account", "accounts", "balance", "ledger",
    "invoice", "invoices", "payment", "payments", "riskscore", "risk_score",
    "fraud", "compliance", "aml", "kyc", "portfolio", "currency", "debit", "credit",
    "reconciliation", "statement", "billing", "revenue", "expense", "asset",
    "liability", "trans", "exposure", "underwriting", "premium", "claim", "claims",
})

_WORD_RE = re.compile(r"[a-z0-9]+")

# Minimum number of DISTINCT keyword hits before declaring a domain — a single
# incidental match (e.g. "credit" in an unrelated column name) shouldn't be
# enough to flip a project's domain.
_MIN_DISTINCT_HITS = 2


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def detect_domain_from_schema(tables: list[dict] | None) -> str:
    """Best-effort domain guess from a crawled schema's tables/columns.

    `tables` is the raw crawler output shape: [{"name", "description",
    "columns": [{"name", "description"}, ...]}, ...]. Returns "recruitment",
    "finance", or "generic" (when signal is absent, weak, or ambiguous).
    """
    tokens: set[str] = set()
    for t in (tables or []):
        tokens |= _tokenize(t.get("name", ""))
        tokens |= _tokenize(t.get("description", ""))
        for c in (t.get("columns") or []):
            tokens |= _tokenize(c.get("name", ""))
            tokens |= _tokenize(c.get("description", ""))

    if not tokens:
        return "generic"

    rec_hits = tokens & _RECRUITMENT_KEYWORDS
    fin_hits = tokens & _FINANCE_KEYWORDS

    rec_score, fin_score = len(rec_hits), len(fin_hits)
    if rec_score < _MIN_DISTINCT_HITS and fin_score < _MIN_DISTINCT_HITS:
        return "generic"
    if rec_score > fin_score:
        return "recruitment"
    if fin_score > rec_score:
        return "finance"
    return "generic"  # tie — don't guess
