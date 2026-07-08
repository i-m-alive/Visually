"""
Root Cause Agent — domain-agnostic query-failure diagnosis.

Used by the orchestrator in two situations:
  1. Automatically, when SQL generation/execution exhausts all normal retries
     and still hard-fails — instead of surfacing a bare error, this explains
     what went wrong and proposes one corrected query before giving up.
  2. On demand, when a user follow-up explicitly asks "why did that fail" /
     "why is this wrong" about a prior turn's result (see
     orchestrator.py's _ROOT_CAUSE_FOLLOWUP_PATTERN).

Works identically across every project domain (recruitment/finance/generic) —
it reasons purely from the user's question, the SQL, the error/quality-check
text, and the schema, never from vertical-specific vocabulary. This is core
pipeline infrastructure, not a skill_agents persona.
"""
from __future__ import annotations

import json
import re
from typing import Optional
from pydantic import BaseModel
from shared.bedrock_client import bedrock_invoke, BEDROCK_SONNET_MODEL

_MODEL = BEDROCK_SONNET_MODEL

_SYSTEM_PROMPT = """You are a SQL root-cause diagnosis agent for a data visualization platform.
Given a user's question, the SQL that was generated to try to answer it, what went wrong (a
database error or a data-quality problem), and the relevant database schema, you must:

1. Explain in plain, non-technical language WHY the query failed or produced a bad result —
   e.g. wrong table chosen, a column that doesn't exist, a filter that excluded everything,
   a type mismatch, a JOIN key mismatch, or the data genuinely not existing.
2. If the fix is clear from the schema and the error, produce a corrected SQL query using
   ONLY tables/columns present in the provided schema. If you cannot confidently fix it
   (e.g. the requested data genuinely isn't in this database), leave fixed_sql null — do not
   guess. A wrong fix is worse than admitting you can't produce one.

Return ONLY valid JSON:
{
  "explanation": "one or two plain-language sentences a non-technical user would understand",
  "root_cause": "short label: wrong_table | missing_column | overly_strict_filter | join_key_mismatch | type_mismatch | no_data | syntax_error | unknown",
  "fixed_sql": "corrected SQL, or null if you cannot confidently fix it",
  "confidence": 0.0
}"""


class RootCauseResult(BaseModel):
    explanation: str
    root_cause: str = "unknown"
    fixed_sql: Optional[str] = None
    confidence: float = 0.0


class RootCauseAgent:
    async def diagnose(
        self,
        user_text: str,
        failed_sql: str,
        problem: str,
        db_type: str,
        tables_context: Optional[list] = None,
    ) -> RootCauseResult:
        """Never raises — returns a best-effort explanation on any internal failure
        so a diagnosis attempt can never itself break the pipeline."""
        user_content = {
            "user_question": user_text,
            "sql_that_was_run": failed_sql,
            "what_went_wrong": problem,
            "db_type": db_type,
        }
        if tables_context:
            user_content["schema"] = tables_context

        try:
            raw = await bedrock_invoke(
                model_id=_MODEL,
                system_prompt=_SYSTEM_PROMPT,
                user_message=json.dumps(user_content, default=str),
                max_tokens=1024,
                temperature=0.1,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"```$", "", raw).strip()
            data = json.loads(raw)
            return RootCauseResult(
                explanation=data.get("explanation") or "I couldn't determine exactly why this happened.",
                root_cause=data.get("root_cause") or "unknown",
                fixed_sql=(data.get("fixed_sql") or None),
                confidence=float(data.get("confidence") or 0.0),
            )
        except Exception as exc:
            print(f"[root_cause_agent] diagnosis failed (non-fatal): {exc}", flush=True)
            return RootCauseResult(
                explanation="I ran into this issue but couldn't fully diagnose why — the underlying query failed.",
                root_cause="unknown",
                fixed_sql=None,
                confidence=0.0,
            )
