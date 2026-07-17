"""
Finance domain glossary — synonym / jargon expansion for retrieval.

The Graph RAG retriever is purely lexical: a table only surfaces when the user's
wording shares literal tokens with the table's metadata (description /
business_name / concept index). Finance users, however, speak in jargon and
abbreviations that rarely appear verbatim in an auto-generated schema
description — "AUM", "NAV", "delinquency", "YoY". This module expands such terms
into the plain-English tokens that DO tend to appear in metadata, closing part of
the paraphrase gap without needing embeddings.

Schema-independent by design: it maps jargon → generic english terms (never to
specific table/column names, which vary per connection), so it works against any
finance database. Expansion is additive — it only appends synonym tokens to the
query, never removes the user's own words — so it can never hurt an exact match.

Extend `_GLOSSARY` per engagement: add the client's own vocabulary (e.g. Fidelity
fund/account terms) as new entries. Keys and values are matched/added lowercased.
"""
from __future__ import annotations

import re

# term (as the user might type it) -> list of canonical english expansion tokens.
# Keep expansions to words likely to appear in an LLM-written column/table
# description. Multi-word keys are matched as phrases; single-word keys match on
# word boundaries.
_GLOSSARY: dict[str, list[str]] = {
    # Balances / holdings
    "aum":              ["assets", "under", "management", "total", "balance", "holdings"],
    "assets under management": ["total", "balance", "portfolio", "holdings", "value"],
    "nav":              ["net", "asset", "value", "fund", "price"],
    "net asset value":  ["fund", "price", "per", "share"],
    "balance":          ["amount", "outstanding", "total"],
    "book value":       ["carrying", "amount", "value"],
    "notional":         ["principal", "face", "amount"],
    "exposure":         ["risk", "outstanding", "amount", "balance"],

    # Income / revenue
    "revenue":          ["income", "earnings", "sales", "billed", "amount"],
    "income":           ["revenue", "earnings", "billed"],
    "yield":            ["return", "rate", "interest"],
    "fees":             ["charge", "commission", "cost"],
    "expense":          ["cost", "spend", "outflow"],
    "pnl":              ["profit", "loss", "gain", "net"],
    "p&l":              ["profit", "loss", "gain", "net"],
    "margin":           ["spread", "profit", "difference"],

    # Risk / quality
    "delinquency":      ["overdue", "late", "past", "due", "arrears", "default"],
    "delinquent":       ["overdue", "late", "past", "due", "arrears"],
    "default":          ["nonpayment", "charge", "off", "delinquent"],
    "charge-off":       ["writeoff", "loss", "default"],
    "npa":              ["nonperforming", "asset", "default"],
    "aging":            ["age", "days", "outstanding", "bucket"],
    "provision":        ["reserve", "allowance", "loss"],

    # Transactions / flow
    "txn":              ["transaction", "payment", "transfer"],
    "transaction":      ["payment", "transfer", "entry"],
    "cash flow":        ["inflow", "outflow", "movement", "payment"],
    "cashflow":         ["inflow", "outflow", "movement", "payment"],
    "settlement":       ["settled", "cleared", "payment", "date"],
    "reconciliation":   ["reconcile", "match", "tie", "out", "compare"],
    "reconcile":        ["match", "tie", "out", "compare", "ledger"],

    # Entities
    "counterparty":     ["party", "client", "customer", "account"],
    "custodian":        ["holder", "account", "keeper"],
    "portfolio":        ["holdings", "account", "positions"],
    "position":         ["holding", "quantity", "amount"],
    "ledger":           ["book", "account", "entry", "gl"],
    "gl":               ["general", "ledger", "account"],

    # Time comparisons
    "yoy":              ["year", "over", "year", "annual", "change"],
    "year over year":   ["annual", "change", "growth"],
    "qoq":              ["quarter", "over", "quarter", "change"],
    "mom":              ["month", "over", "month", "change"],
    "mtd":              ["month", "to", "date"],
    "ytd":              ["year", "to", "date"],
    "run rate":         ["annualized", "projected", "pace"],
}

# Longest-first so multi-word phrases match before their single-word components.
_PHRASE_KEYS = sorted(
    (k for k in _GLOSSARY if " " in k or "-" in k or "&" in k),
    key=len, reverse=True,
)
_WORD_KEYS = {k for k in _GLOSSARY if k not in set(_PHRASE_KEYS)}


def expand_finance_terms(text: str) -> list[str]:
    """
    Return the extra expansion tokens implied by any finance jargon in `text`.
    Additive: caller appends these to the original query text before tokenizing.
    Returns [] when nothing matches (the common case for non-finance wording).
    """
    if not text:
        return []
    low = text.lower()
    added: list[str] = []
    seen: set[str] = set()

    def _push(tokens: list[str]) -> None:
        for tok in tokens:
            if tok not in seen:
                seen.add(tok)
                added.append(tok)

    # Phrase matches first (substring is fine — these are distinctive).
    for phrase in _PHRASE_KEYS:
        if phrase in low:
            _push(_GLOSSARY[phrase])

    # Then single-word matches on word boundaries.
    words = set(re.findall(r"[a-z0-9&]+", low))
    for w in words:
        if w in _WORD_KEYS:
            _push(_GLOSSARY[w])

    return added
