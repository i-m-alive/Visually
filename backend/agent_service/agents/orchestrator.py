import asyncio
import uuid
import json
import os
import re
from datetime import datetime
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import AsyncSessionLocal
from shared.models.database_connections import DatabaseConnection
from shared.models.schema_snapshots import SchemaSnapshot
from shared.models.pipeline_jobs import PipelineJob
from shared.models.dashboards import Dashboard
from shared.models.widgets import Widget
from shared.schemas.schema import SemanticSchemaDocument
from shared.redis_client import publish_pipeline_event, set_pipeline_state
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
from agent_service.agents.intent_classifier import IntentClassifier
from agent_service.agents.query_agent import QueryAgent
from agent_service.agents.validator_agent import ValidatorAgent
import agent_service.agents.schema_cache as _schema_cache
import agent_service.agents.graph_rag_retriever as _graph_rag
from agent_service.services.ws_manager import manager as _ws_manager
# Canonical definitions live in sql_utils.py (shared with the chat routers'
# own root-cause gates); re-imported under the old private name so the rest
# of this file needs no changes.
from agent_service.agents.sql_utils import (
    ROOT_CAUSE_FOLLOWUP_PATTERN as _ROOT_CAUSE_FOLLOWUP_PATTERN,
    root_cause_schema_context as _root_cause_schema_context_fn,
)

# Minimum validation score to emit a chart result (was 0.80, kept retrying 4×).
# 0.65 lets well-formed results pass on the first attempt.
_VALIDATION_PASS_THRESHOLD = 0.65

DASHBOARD_DECOMPOSE_MODEL = BEDROCK_HAIKU_MODEL
DASHBOARD_MAX_CHARTS = 5        # max charts per dashboard (count cap)
CHART_CONCURRENCY = 10          # parallel Bedrock chart slots (can exceed DASHBOARD_MAX_CHARTS)
# Windows select() caps at 512 FDs. Pre-sampling spawns N_charts × 3 candidates × ~5 DB queries
# concurrently. Cap chart-level parallelism so total open sockets stays well under 512.
PRESAMPLE_CONCURRENCY = 6       # max charts sampling in parallel at step 3.7

# In-process hint communication — maps hint_id → asyncio.Event / response string
_hint_events: dict[str, asyncio.Event] = {}
_hint_responses: dict[str, str] = {}

QUERY_EXECUTOR_URL = os.getenv("QUERY_EXECUTOR_URL", "http://localhost:8002")
RENDER_SERVICE_URL = os.getenv("RENDER_SERVICE_URL", "http://localhost:3001")


class Orchestrator:
    def __init__(self):
        self._intent = IntentClassifier()
        self._query = QueryAgent()
        self._validator = ValidatorAgent()
        from agent_service.agents.root_cause_agent import RootCauseAgent
        self._root_cause = RootCauseAgent()

    @staticmethod
    def _root_cause_schema_context(enriched, table_names: list) -> list:
        """Small {name, columns} list for the tables most likely relevant to a
        diagnosis — reused by both the automatic and on-demand root-cause paths.
        Canonical implementation lives in sql_utils.py; kept as a delegating
        staticmethod here for backward compat with this class's own call sites."""
        return _root_cause_schema_context_fn(enriched, table_names)

    async def run_single_viz_pipeline(
        self,
        job_id: str,
        user_text: str,
        project_id: str,
        user_id: str,
        connection_id: str,
        redis,
        db: AsyncSession,
        conversation_history: Optional[list] = None,
        scope: Optional[str] = None,
        selected_tables: Optional[list[str]] = None,
        selected_hops: Optional[int] = 2,
        output_mode_override: Optional[str] = None,
        user_profile: Optional[dict] = None,
        domain: str = "recruitment",
        preclassified_intent=None,
    ) -> dict:
        async def emit(event: dict):
            # Direct in-process broadcast (works with or without Redis)
            await _ws_manager.broadcast(job_id, event)
            await publish_pipeline_event(redis, job_id, event)
            await set_pipeline_state(redis, job_id, "last_event", event.get("type", ""))

        # Update job status
        job_result = await db.execute(
            select(PipelineJob).where(PipelineJob.id == uuid.UUID(job_id))
        )
        job = job_result.scalar_one_or_none()
        if job:
            job.status = "running"
            job.started_at = datetime.utcnow()
            await db.commit()

        try:
            # STEP 1: Classify intent — reuse the classification already computed
            # upstream (main._run_pipeline) when provided, so we never pay for a
            # second, redundant classification pass on the critical path.
            await set_pipeline_state(redis, job_id, "step", "classifying")
            if preclassified_intent is not None:
                intent = preclassified_intent
            else:
                intent = await self._intent.classify(
                    user_text, conversation_history=conversation_history, domain=domain,
                )
            await emit({
                "type": "intent.classified",
                "job_id": job_id,
                "intent_type": intent.intent_type,
                "vagueness_score": intent.vagueness_score,
                "confidence": intent.confidence,
            })
            await set_pipeline_state(redis, job_id, "step", "intent_classified")

            # ── Agent skill routing ──────────────────────────────────────────
            # When the intent is an agent skill type, route to the tool-use loop
            # instead of the SQL pipeline. These intents don't need schema fetch,
            # GraphRAG, or query generation — they go directly to a ToolAgent.
            _AGENT_INTENTS = frozenset({
                "MATCH", "BRIEFING", "SCREEN", "ENRICH",
                "VERIFY", "PRESENT", "AUDIT", "PROSPECT", "ACTION",
                # Finance-only intents (see intent_classifier.py's finance skill
                # block — these are never offered to non-finance domains).
                "RECONCILE", "ANOMALY", "FORECAST", "NETWORK",
            })
            # Skill agents are per-domain personas (see domain_config.SKILL_DOMAINS).
            # A domain with no skill agents (e.g. "generic") never routes here even
            # if the classifier somehow returns one of these intent types — the
            # intent-classifier prompt already omits this vocabulary for such
            # domains, this is defense in depth.
            from agent_service.agents.domain_config import SKILL_DOMAINS
            _route_to_agent = intent.intent_type in _AGENT_INTENTS and domain in SKILL_DOMAINS
            # ── DIAGNOSTIC LOG ──────────────────────────────────────────────
            print(
                f"[DIAG orchestrator:{job_id[:8]}] "
                f"intent={intent.intent_type!r} confidence={intent.confidence:.2f} domain={domain!r} "
                f"route={'AGENT_SKILL' if _route_to_agent else 'SQL_PIPELINE'} "
                f"user_profile={'set(role=' + str(user_profile.get('brainwave_role')) + ')' if user_profile else 'None'}",
                flush=True,
            )
            if _route_to_agent:
                from agent_service.agents.tool_agent import AgentContext
                from agent_service.agents import skill_agents

                # ── Schema context injection ──────────────────────────────────
                # Fetch and rank tables from the schema cache so the agent knows
                # which tables exist on turn 1 — avoids blind probe loops.
                # Completely non-fatal: a failure here still runs the agent, just
                # without the schema preamble.
                _schema_tables: list[dict] = []
                agent_user_text = user_text
                try:
                    _schema_doc = await self._get_latest_schema(connection_id, db)
                    if _schema_doc:
                        _conn_r = await db.execute(
                            select(DatabaseConnection).where(
                                DatabaseConnection.id == uuid.UUID(connection_id)
                            )
                        )
                        _conn_rec = _conn_r.scalar_one_or_none()
                        _db_type  = _conn_rec.db_type.value if _conn_rec else "postgresql"
                        _enriched = await _schema_cache.get_or_build(
                            connection_id, _schema_doc, _db_type
                        )
                        if _enriched and _enriched.compact_tables:
                            _rag = _graph_rag.retrieve(
                                user_text, intent, _enriched, top_k=20, domain=domain
                            )
                            _candidates = (
                                _rag.candidates if (_rag and _rag.candidates) else []
                            )
                            if _candidates:
                                _ct_by_name = {
                                    t["name"]: t for t in _enriched.compact_tables
                                }
                                _schema_tables = [
                                    {
                                        "name": c.table_name,
                                        "columns": [
                                            col["name"]
                                            for col in _ct_by_name.get(
                                                c.table_name, {}
                                            ).get("columns", [])[:20]
                                        ],
                                    }
                                    for c in _candidates
                                ]
                            else:
                                _schema_tables = [
                                    {
                                        "name": t["name"],
                                        "columns": [
                                            c["name"]
                                            for c in t.get("columns", [])[:20]
                                        ],
                                    }
                                    for t in _enriched.compact_tables[:25]
                                ]

                            # Build schema preamble injected into the user message
                            _lines = [
                                "## Available database tables "
                                "(ranked by relevance to your query):"
                            ]
                            for _t in _schema_tables[:20]:
                                _cols = ", ".join(_t.get("columns", [])[:15])
                                _lines.append(f"  - {_t['name']}  columns: [{_cols}]")

                            # Token optimization: pre-resolve likely table roles for
                            # finance agents so they can skip a discovery turn instead
                            # of spending a run_sql call figuring out "which table is
                            # the transaction table" on every single invocation.
                            if domain == "finance":
                                from agent_service.agents.table_role_classifier import (
                                    classify_table_roles, format_table_roles,
                                )
                                _role_block = format_table_roles(
                                    classify_table_roles(_enriched.compact_tables)
                                )
                                if _role_block:
                                    _lines += ["", _role_block]

                            _lines += ["", f"User request: {user_text}"]
                            agent_user_text = "\n".join(_lines)
                            print(
                                f"[pipeline:{job_id}] schema context injected for agent: "
                                f"{len(_schema_tables)} tables",
                                flush=True,
                            )
                except Exception as _se:
                    print(
                        f"[pipeline:{job_id}] schema context fetch failed (non-fatal): {_se}",
                        flush=True,
                    )

                # ── Conversation history for the skill agent ─────────────────
                # Without this, a follow-up like "how much has THIS customer
                # invested?" reaches the agent with no idea who "this customer"
                # is (the SQL pipeline already gets history; the agent path did
                # not) and it asks the user to re-identify the entity. Inject a
                # compact recent-turns block ahead of the request so the agent
                # can resolve the reference against what was already discussed.
                if conversation_history:
                    _hist_lines = []
                    for _turn in conversation_history[-6:]:
                        _role = _turn.get("role", "user")
                        _content = (_turn.get("content") or "").strip()
                        if _content:
                            _hist_lines.append(f"{_role}: {_content[:600]}")
                    if _hist_lines:
                        _hist_block = (
                            "## Recent conversation (resolve references like "
                            "'this customer', 'that account', 'the same one' "
                            "against it):\n" + "\n".join(_hist_lines)
                        )
                        agent_user_text = _hist_block + "\n\n" + agent_user_text

                ctx = AgentContext(
                    project_id=project_id,
                    connection_id=connection_id,
                    job_id=job_id,
                    db=db,
                    redis=redis,
                    emit=emit,
                    schema_tables=_schema_tables,
                    user_profile=user_profile,
                    domain=domain,
                    conversation_history=conversation_history or [],
                )
                await emit({
                    "type":       "agent.started",
                    "job_id":     job_id,
                    "agent_type": intent.intent_type,
                })
                await set_pipeline_state(redis, job_id, "step", "agent_running")

                try:
                    agent_fn = skill_agents.get_agent(domain, intent.intent_type)
                    answer   = await agent_fn(agent_user_text, ctx)
                except Exception as _ae:
                    print(
                        f"[pipeline:{job_id}] agent error "
                        f"({intent.intent_type}): {_ae}",
                        flush=True,
                    )
                    answer = (
                        "I ran into an error processing that request. "
                        "Please try again or rephrase your question."
                    )

                agent_result = {
                    "job_id":             job_id,
                    "score":              1.0,
                    "chart_data":         {
                        "rows": [], "columns": [], "labels": [], "values": [],
                    },
                    "low_confidence":     False,
                    "sql":                "",
                    "chart_type":         "text",
                    "title":              intent.intent_type.replace("_", " ").title(),
                    "table_used":         "",
                    "x_axis_label":       "",
                    "y_axis_label":       "",
                    "output_mode":        "text",
                    "narrative":          answer,
                    "validation_details": {},
                }
                try:
                    from shared.bedrock_client import get_cost_summary
                    agent_result["cost"] = get_cost_summary()
                except Exception:
                    pass
                await emit({
                    "type":           "chart.confirmed",
                    "job_id":         job_id,
                    "score":          1.0,
                    "chart_data":     agent_result,
                    "low_confidence": False,
                })
                if job:
                    job.status           = "completed"
                    job.result_payload   = agent_result
                    job.completed_at     = datetime.utcnow()
                    await db.commit()
                await set_pipeline_state(redis, job_id, "step", "agent_done")
                return agent_result

            # STEP 2: Fetch schema
            await set_pipeline_state(redis, job_id, "step", "fetching_schema")
            schema_doc = await self._get_latest_schema(connection_id, db)
            if not schema_doc:
                await emit({
                    "type": "pipeline.error",
                    "job_id": job_id,
                    "message": "No schema found. Please crawl the database schema first.",
                    "recoverable": False,
                })
                await self._fail_job(job_id, "No schema found", db)
                return {"error": "No schema found"}

            schema = SemanticSchemaDocument(**schema_doc)
            await emit({
                "type": "schema.fetched",
                "job_id": job_id,
                "table_count": schema.total_tables,
                "important_tables": schema.important_tables,
            })
            await set_pipeline_state(redis, job_id, "step", "schema_fetched")

            # Get db_type for this connection
            conn_result = await db.execute(
                select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id))
            )
            conn_rec = conn_result.scalar_one_or_none()
            db_type = conn_rec.db_type.value if conn_rec else "postgresql"

            # Build enriched schema (L1/L2 cache hit is sub-ms; L3 cold build is async)
            enriched = None
            try:
                enriched = await _schema_cache.get_or_build(connection_id, schema_doc, db_type)
            except Exception as _e:
                print(f"[pipeline:{job_id}] ⚠ schema enrichment failed (non-fatal): {_e}", flush=True)

            # Graph RAG retrieval — runs in <5 ms against in-memory EnrichedSchema.
            # Returns ranked TableCandidates with column/JOIN hints for QueryAgent.
            retrieved_context = None
            if enriched:
                from agent_service.agents.sql_utils import extract_recent_tables
                _history_tables = extract_recent_tables(conversation_history)
                # Embed the question once so the retriever can add a semantic
                # cosine signal — only worth it when the schema actually carries
                # per-table embeddings. Fully non-fatal: None → lexical-only.
                _query_embedding = None
                if getattr(enriched, "table_embeddings", None):
                    try:
                        from shared.bedrock_client import bedrock_embed
                        _query_embedding = await bedrock_embed(user_text)
                    except Exception as _ee:
                        print(f"[pipeline:{job_id}] query embed failed (non-fatal): {_ee}", flush=True)
                retrieved_context = _graph_rag.retrieve(
                    user_text=user_text,
                    intent=intent,
                    enriched=enriched,
                    top_k=6,
                    history_tables=_history_tables,
                    domain=domain,
                    query_embedding=_query_embedding,
                )
                # ── LLM arbiter (ensemble tie-breaker) ────────────────────────
                # Only when the cheap experts disagreed (needs_arbiter). Reorders
                # the candidate list so the arbiter's pick leads. One small call,
                # fully non-fatal — original order stands on any failure.
                if retrieved_context and getattr(retrieved_context, "needs_arbiter", False):
                    try:
                        from agent_service.agents.retrieval_arbiter import arbitrate
                        await set_pipeline_state(redis, job_id, "step", "retrieval_arbiter")
                        _arb = await arbitrate(user_text, retrieved_context, enriched)
                        if _arb.get("used") and _arb.get("ordered_tables"):
                            _by_name = {c.table_name: c for c in (retrieved_context.candidates or [])}
                            _reordered = [_by_name[t] for t in _arb["ordered_tables"] if t in _by_name]
                            _reordered += [c for c in (retrieved_context.candidates or []) if c not in _reordered]
                            retrieved_context.candidates = _reordered
                            retrieved_context.primary_tables = [c.table_name for c in _reordered]
                            retrieved_context.arbiter_used = True
                            retrieved_context.arbiter_reason = _arb.get("reason", "")
                            print(
                                f"[pipeline:{job_id}] arbiter reordered → "
                                f"{retrieved_context.primary_tables[:3]}  ({_arb.get('reason','')[:80]})",
                                flush=True,
                            )
                    except Exception as _abe:
                        print(f"[pipeline:{job_id}] arbiter step failed (non-fatal): {_abe}", flush=True)

                if retrieved_context and retrieved_context.primary_tables:
                    await emit({
                        "type": "rag.retrieved",
                        "job_id": job_id,
                        "tables": retrieved_context.primary_tables[:4],
                        "confidence": round(retrieved_context.confidence, 3),
                        "agreement": round(getattr(retrieved_context, "agreement", 1.0), 3),
                        "arbiter_used": getattr(retrieved_context, "arbiter_used", False),
                    })

            # ── Explicit "why did that fail?" follow-up ──────────────────────────
            # Domain-agnostic: works identically for recruitment/finance/generic.
            # Only fires when the immediately prior turn actually recorded a
            # failure/low-confidence flag — otherwise this falls through to the
            # normal pipeline exactly as before (a plain follow-up chart request).
            _last_turn = conversation_history[-1] if conversation_history else None
            _wants_root_cause = bool(
                _last_turn
                and (_last_turn.get("error") or _last_turn.get("low_confidence"))
                and _ROOT_CAUSE_FOLLOWUP_PATTERN.search(user_text)
            )
            if _wants_root_cause:
                from agent_service.agents.sql_utils import extract_recent_tables as _ert
                await set_pipeline_state(redis, job_id, "step", "root_cause_diagnosis")
                _failed_sql = _last_turn.get("sql") or ""
                _rc_tables = _ert([_last_turn]) if _failed_sql else []
                _rc_ctx = self._root_cause_schema_context(enriched, _rc_tables)
                _diag = await self._root_cause.diagnose(
                    user_text=user_text,
                    failed_sql=_failed_sql,
                    problem=_last_turn.get("error") or (
                        "The previous result was flagged low-confidence — it may not "
                        "have accurately answered the question."
                    ),
                    db_type=db_type,
                    tables_context=_rc_ctx,
                )
                _om = (getattr(intent, "output_mode", "chart") or "chart").lower()
                _fixed_result = None
                if _diag.fixed_sql:
                    _fixed_result = await self._execute_query(connection_id, _diag.fixed_sql)
                    if _fixed_result.get("error"):
                        _fixed_result = None

                if _fixed_result is not None:
                    from shared.schemas.chart import QueryPlan as _QueryPlan
                    _plan = _QueryPlan(
                        sql=_diag.fixed_sql,
                        chart_type=("table" if _om != "chart" else "bar_vertical"),
                        table_used=(_rc_tables[0] if _rc_tables else ""),
                        title="Corrected result", reasoning="", db_dialect=db_type,
                    )
                    _render = await self._render_chart(_plan, _fixed_result) if _om == "chart" else {}
                    root_cause_result = {
                        "job_id": job_id,
                        "score": 0.6,
                        "chart_data": self._build_chart_data(_plan, _fixed_result, _render),
                        "low_confidence": True,
                        "sql": _diag.fixed_sql,
                        "chart_type": _plan.chart_type,
                        "title": _plan.title,
                        "table_used": _plan.table_used,
                        "x_axis_label": "",
                        "y_axis_label": "",
                        "output_mode": _om,
                        "narrative": f"{_diag.explanation} I corrected the query — here's the result.",
                        "validation_details": {},
                        "root_cause": _diag.model_dump(),
                    }
                else:
                    root_cause_result = {
                        "job_id": job_id,
                        "score": 0.0,
                        "chart_data": {"rows": [], "columns": [], "labels": [], "values": []},
                        "low_confidence": True,
                        "sql": _failed_sql,
                        "chart_type": "text",
                        "title": "Why that didn't work",
                        "table_used": "",
                        "x_axis_label": "",
                        "y_axis_label": "",
                        "output_mode": "text",
                        "narrative": _diag.explanation,
                        "validation_details": {},
                        "root_cause": _diag.model_dump(),
                    }
                await emit({
                    "type": "chart.confirmed",
                    "job_id": job_id,
                    "score": root_cause_result["score"],
                    "chart_data": root_cause_result,
                    "low_confidence": True,
                })
                if job:
                    job.status = "completed"
                    job.result_payload = root_cause_result
                    job.completed_at = datetime.utcnow()
                    await db.commit()
                await set_pipeline_state(redis, job_id, "step", "root_cause_done")
                return root_cause_result

            # SCHEMA_EXPLORE: skip SQL pipeline, return a plain-English schema overview
            if intent.intent_type == "SCHEMA_EXPLORE":
                await set_pipeline_state(redis, job_id, "step", "schema_explored")
                schema_overview = await self._build_schema_overview(user_text, schema, enriched)
                schema_result = {
                    "job_id": job_id,
                    "score": 1.0,
                    "chart_data": {"rows": [], "columns": [], "labels": [], "values": []},
                    "low_confidence": False,
                    "sql": "",
                    "chart_type": "table",
                    "title": "What data do you have?",
                    "table_used": "",
                    "x_axis_label": "",
                    "y_axis_label": "",
                    "output_mode": "text",
                    "narrative": schema_overview,
                    "validation_details": {},
                }
                await emit({
                    "type": "chart.confirmed",
                    "job_id": job_id,
                    "score": 1.0,
                    "chart_data": schema_result,
                    "low_confidence": False,
                })
                if job:
                    job.status = "completed"
                    job.result_payload = schema_result
                    job.completed_at = datetime.utcnow()
                    await db.commit()
                return schema_result

            # Scope filtering: when scope="selected", restrict RAG candidates to chosen tables
            if scope == "selected" and selected_tables and retrieved_context:
                _norm = {t.lower() for t in selected_tables}
                filtered_candidates = [
                    c for c in (retrieved_context.candidates or [])
                    if any(
                        c.table_name.lower() == st or c.table_name.lower().endswith(f".{st}")
                        for st in _norm
                    )
                ]
                # Fallback: keep top candidate if none of the selected tables matched
                retrieved_context.candidates = filtered_candidates or retrieved_context.candidates[:1]
                filtered_primary = [
                    t for t in (retrieved_context.primary_tables or [])
                    if any(t.lower() == st or t.lower().endswith(f".{st}") for st in _norm)
                ]
                retrieved_context.primary_tables = filtered_primary or retrieved_context.primary_tables[:1]

            # ── Clarification turn: don't guess when the question is unanswerable ──────
            # Very low retrieval confidence means no table plausibly matches; a compound
            # question ("how many X and what were their average Y") needs splitting.
            # One clarifying question beats a confidently wrong chart.
            _clarify_reason = None
            if intent.intent_type == "SINGLE_VIZ" and not conversation_history:
                _rag_conf = getattr(retrieved_context, "confidence", 0.0) if retrieved_context else 0.0
                if enriched and retrieved_context is not None and _rag_conf < 0.12:
                    _clarify_reason = (
                        "I couldn't confidently match your question to any table in this database. "
                        "Could you rephrase it using terms closer to your data — for example, "
                        "mention the specific metric or table you have in mind?"
                    )
                elif re.search(r"\?.+\band\b.+\?|\band\s+(what|how\s+many|how\s+much|which)\b",
                               user_text.lower()) and len(intent.entities.metrics) >= 2:
                    _clarify_reason = (
                        "Your question asks for two different things at once, which usually "
                        "produces a muddled answer. Could you ask them one at a time? "
                        f"For example, start with just the first part."
                    )
            if _clarify_reason:
                clarify_result = {
                    "job_id": job_id,
                    "score": 1.0,
                    "chart_data": {"rows": [], "columns": [], "labels": [], "values": []},
                    "low_confidence": True,
                    "sql": "",
                    "chart_type": "text",
                    "title": "Need a quick clarification",
                    "table_used": "",
                    "x_axis_label": "",
                    "y_axis_label": "",
                    "output_mode": "text",
                    "narrative": _clarify_reason,
                    "validation_details": {},
                    "needs_clarification": True,
                }
                await emit({
                    "type": "chart.confirmed",
                    "job_id": job_id,
                    "score": 1.0,
                    "chart_data": clarify_result,
                    "low_confidence": True,
                })
                if job:
                    job.status = "completed"
                    job.result_payload = clarify_result
                    job.completed_at = datetime.utcnow()
                    await db.commit()
                await set_pipeline_state(redis, job_id, "step", "clarification_requested")
                return clarify_result

            # ── Accuracy context: metric registry + query memory + time contract ──────
            from agent_service.agents import metric_registry as _metric_registry
            from agent_service.agents import query_memory as _query_memory
            from agent_service.agents.query_agent import _compute_date_bounds as _cdb
            from agent_service.agents.sql_utils import (
                check_sql_contract, expected_period_count, fix_filter_values,
            )

            _metric_defs: list = []
            _few_shots: list = []
            try:
                _metric_defs = _metric_registry.match_metrics(user_text, connection_id)
                if _metric_defs:
                    print(f"[pipeline:{job_id}] metric registry matched: "
                          f"{[m['name'] for m in _metric_defs]}", flush=True)
            except Exception as _me:
                print(f"[pipeline:{job_id}] metric registry lookup failed (non-fatal): {_me}", flush=True)
            try:
                _few_shots = _query_memory.find_similar(connection_id, user_text, k=3)
                if _few_shots:
                    print(f"[pipeline:{job_id}] query memory: {len(_few_shots)} similar past queries", flush=True)
            except Exception as _qe:
                print(f"[pipeline:{job_id}] query memory lookup failed (non-fatal): {_qe}", flush=True)

            # Intent time contract, used by the SQL contract check and result shape check
            _gran = getattr(intent.entities, "time_granularity", None)
            _time_bounds = None
            if intent.entities.time_range and intent.entities.time_range.type == "relative":
                _time_bounds = _cdb(intent.entities.time_range.value)
            _expected_periods = None
            if _gran and _time_bounds:
                _expected_periods = expected_period_count(_time_bounds[0], _time_bounds[1], _gran)

            # ── Deterministic SQL template (attempt 1 only) ────────────────────────────
            # A single registered metric + a simple shape → build SQL in Python,
            # no LLM, no hallucination. Falls back to the LLM on any execution issue.
            _template_plan = None
            try:
                from agent_service.agents.sql_template_builder import try_build_template_sql
                _user_filter = None
                if user_profile:
                    from agent_service.agents.user_context_builder import get_sql_filter_clause
                    _user_filter = get_sql_filter_clause(user_profile)
                _template_plan = try_build_template_sql(
                    intent, db_type, _metric_defs,
                    date_bounds=_time_bounds,
                    n_periods=_expected_periods,
                    enriched=enriched,
                    user_filter_clause=_user_filter,
                )
                if _template_plan is not None:
                    print(f"[pipeline:{job_id}] deterministic template SQL built "
                          f"(metric={_metric_defs[0]['name']!r})", flush=True)
            except Exception as _tpe:
                print(f"[pipeline:{job_id}] template builder failed (non-fatal): {_tpe}", flush=True)
                _template_plan = None

            # ── Multi-candidate ambiguity check ───────────────────────────────────────
            # When the top-2 RAG candidates are close in score, run all in parallel
            # so the user sees every plausible answer — not just the first guess.
            final_result = None
            _multi_candidate_results: list[dict] = []

            if retrieved_context and len(retrieved_context.candidates) >= 2:
                from agent_service.agents.candidate_ranker import (
                    get_pipeline_candidates, is_ambiguous,
                    should_auto_select, candidate_label,
                )
                cand_scores = get_pipeline_candidates(retrieved_context.candidates)
                # Fan out to best-of-N either when the score spread looks ambiguous
                # (existing heuristic) OR when the ensemble experts disagreed
                # (low agreement) — the latter is exactly the "retrieval was
                # uncertain, generate multiple candidates and let validation pick"
                # case. Bounded: still only runs on the ambiguous minority.
                _low_agreement = getattr(retrieved_context, "agreement", 1.0) < 0.5
                if is_ambiguous(cand_scores) or _low_agreement:
                    print(
                        f"[pipeline:{job_id}] multi-candidate ambiguity: "
                        + " | ".join(
                            f"{c.table_name.split('.')[-1]}={c.normalized_score:.2f}"
                            for c in cand_scores
                        ),
                        flush=True,
                    )
                    await emit({
                        "type": "candidates.ranking",
                        "job_id": job_id,
                        "tables": [
                            {"table": c.table_name, "score": round(c.rank_score, 3)}
                            for c in cand_scores
                        ],
                    })
                    tasks = [
                        asyncio.wait_for(
                            self._run_single_candidate(
                                c.table_name, c.rank_score, job_id, user_text,
                                intent, schema, db_type, enriched,
                                retrieved_context, connection_id,
                            ),
                            timeout=40.0,
                        )
                        for c in cand_scores
                    ]
                    raw = await asyncio.gather(*tasks, return_exceptions=True)
                    cand_results = [r for r in raw if isinstance(r, dict) and r is not None]
                    if len(cand_results) >= 2:
                        for i, cr in enumerate(cand_results):
                            cr["label"] = candidate_label(i, cr["table"])
                        if should_auto_select(cand_results):
                            best = max(cand_results, key=lambda x: x["confidence"])
                            final_result = self._candidate_to_final_result(job_id, best)
                            print(
                                f"[pipeline:{job_id}] auto-selected "
                                f"{best['table']!r} confidence={best['confidence']:.3f}",
                                flush=True,
                            )
                        else:
                            _multi_candidate_results = cand_results
                            best = max(cand_results, key=lambda x: x["confidence"])
                            final_result = self._candidate_to_final_result(job_id, best)
                            print(
                                f"[pipeline:{job_id}] surfacing {len(cand_results)} "
                                "candidates to user",
                                flush=True,
                            )
                    elif len(cand_results) == 1:
                        final_result = self._candidate_to_final_result(job_id, cand_results[0])

            # Phase 6: remember the multi-candidate / arbiter-selected winner. The
            # attempt loop below (which normally records success) is skipped once a
            # candidate has produced final_result, so record it here instead — this
            # is how a validation-judged best-of-N answer becomes a future few-shot.
            if final_result and final_result.get("sql"):
                try:
                    _query_memory.record_success(
                        connection_id, user_text, final_result["sql"],
                        final_result.get("table_used", ""),
                        final_result.get("chart_type", ""),
                        score=final_result.get("score", 0.7),
                    )
                except Exception as _qmw:
                    print(f"[pipeline:{job_id}] multi-candidate memory record failed (non-fatal): {_qmw}", flush=True)

            # STEP 3+4+5+6: Query → Execute → Render → Validate (up to 4 attempts)
            # Skipped when multi-candidate already produced a final_result.
            retry_feedback: Optional[str] = None
            _MAX_SINGLE_VIZ_ATTEMPTS = 4
            query_plan = None           # guards narration / fallback below
            execute_result: dict = {}
            # Extract user-requested chart type (None if user didn't specify one)
            expected_chart_type: Optional[str] = getattr(intent.entities, "chart_type", None)
            # How the user wants the answer presented: "chart" (visualize) or "text" (prose answer)
            output_mode: str = (getattr(intent, "output_mode", "chart") or "chart").lower()
            # Allow the request to override the LLM-classified output mode
            if output_mode_override:
                output_mode = output_mode_override.lower()

            for attempt in range(1, _MAX_SINGLE_VIZ_ATTEMPTS + 1) if final_result is None else []:
                # Step 3: Generate query — deterministic template on attempt 1 when a
                # registered metric matched; LLM otherwise (and on all retries).
                await set_pipeline_state(redis, job_id, "step", f"generating_query_attempt_{attempt}")
                _used_template = False
                if attempt == 1 and _template_plan is not None:
                    query_plan = _template_plan
                    _used_template = True
                else:
                    query_plan = await self._query.generate(
                        intent, schema, db_type, retry_feedback, attempt, enriched,
                        retrieved_context=retrieved_context,
                        conversation_history=conversation_history,
                        user_profile=user_profile,
                        metric_definitions=_metric_defs or None,
                        few_shot_examples=_few_shots or None,
                        output_mode=output_mode,
                    )
                # Honor an explicit output-mode selection from the user's toggle.
                # "table" → force a tabular result no matter what chart type the
                # model chose; "text" → the narration path already renders prose
                # (no chart is rendered when output_mode != "chart"). "chart" and
                # "auto" leave the model's choice intact.
                if output_mode == "table" and query_plan is not None:
                    query_plan.chart_type = "table"

                await emit({
                    "type": "query.generated",
                    "job_id": job_id,
                    "sql": query_plan.sql,
                    "chart_type": query_plan.chart_type,
                    "table_used": query_plan.table_used,
                    "title": query_plan.title,
                })
                await set_pipeline_state(redis, job_id, "step", "query_generated")

                # ── Sandbox guardrail: block write SQL before it reaches the DB ────────
                # Belt-and-suspenders: sandbox also runs inside the query executor, but
                # catching it here lets us retry with corrective feedback instead of a
                # hard 400 error from the executor.
                try:
                    from query_executor.sandbox import validate_sql as _sandbox_validate
                    _sql_safe, _sql_reason = _sandbox_validate(query_plan.sql)
                    if not _sql_safe:
                        retry_feedback = (
                            f"Guardrail blocked a write operation in the generated SQL: {_sql_reason}. "
                            "Rewrite as a read-only SELECT/WITH query."
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "write_op_blocked"})
                        print(f"[orchestrator:{job_id[:8]}] sandbox blocked SQL attempt {attempt}: {_sql_reason}", flush=True)
                        continue
                except ImportError:
                    pass  # sandbox not available in this environment

                # ── Intent-contract check: SQL must honour granularity/time/KPI shape ──
                # (templates are correct by construction — skip)
                if not _used_template:
                    contract_err = check_sql_contract(
                        query_plan.sql,
                        granularity=_gran,
                        time_filter_required=bool(_time_bounds),
                        chart_type=query_plan.chart_type,
                    )
                    if contract_err and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                        retry_feedback = f"Intent contract violation: {contract_err}"
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "fix_intent_contract"})
                        continue

                # ── Pre-execution validation ──────────────────────────────────
                if enriched and enriched.compact_tables:
                    from agent_service.agents.sql_utils import (
                        basic_sql_lint, verify_columns_against_schema, fuzzy_fix_column_names,
                    )
                    # 1. Syntax lint
                    lint_err = basic_sql_lint(query_plan.sql, db_type)
                    if lint_err and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                        retry_feedback = f"SQL syntax error (caught before execution): {lint_err}. Rewrite the query fixing this issue."
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "fix_lint_error"})
                        continue

                    # 2. Column existence check
                    col_err = verify_columns_against_schema(
                        query_plan.sql, enriched.compact_tables,
                        candidate_tables=[query_plan.table_used],
                    )
                    if col_err:
                        # 3. Try auto-fix via fuzzy matching before giving up
                        fixed_sql, corrections = fuzzy_fix_column_names(
                            query_plan.sql, enriched.compact_tables,
                            candidate_tables=[query_plan.table_used],
                        )
                        if corrections:
                            print(f"[orchestrator:{job_id}] auto-fixed columns: {corrections}", flush=True)
                            query_plan = query_plan.__class__(
                                sql=fixed_sql,
                                chart_type=query_plan.chart_type,
                                table_used=query_plan.table_used,
                                x_axis_label=query_plan.x_axis_label,
                                y_axis_label=query_plan.y_axis_label,
                                title=query_plan.title,
                                reasoning=query_plan.reasoning,
                                db_dialect=query_plan.db_dialect,
                            )
                        elif attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                            # Build schema snippet for the retry message
                            ct_map = {t["name"]: t for t in enriched.compact_tables}
                            cand_ct = ct_map.get(query_plan.table_used)
                            schema_hint = ""
                            if cand_ct:
                                col_names = [c.get("name") for c in cand_ct.get("columns", [])[:20]]
                                schema_hint = f" Actual columns in {query_plan.table_used}: {', '.join(col_names)}."
                            retry_feedback = f"{col_err}.{schema_hint} Use only columns that exist in the schema."
                            await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "fix_column_error"})
                            continue

                # ── 2b. Unqualified column ref check ─────────────────────────────
                # Catches bare column names like SELECT revenue FROM tbl that the
                # qualified check above misses (it only handles tbl.revenue).
                if enriched and enriched.compact_tables and query_plan.sql:
                    from agent_service.agents.sql_utils import verify_all_column_refs
                    _all_col_errs = verify_all_column_refs(
                        query_plan.sql, enriched.compact_tables,
                        candidate_tables=[query_plan.table_used] if query_plan.table_used else None,
                    )
                    # Isolate unqualified errors only (qualified errors were already handled above)
                    _unqual_errs = [e for e in _all_col_errs if "not found in any referenced table" in e]
                    if _unqual_errs and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                        _ct_map_uq = {t["name"]: t for t in enriched.compact_tables}
                        _cand_uq = _ct_map_uq.get(query_plan.table_used or "")
                        _hint_uq = ""
                        if _cand_uq:
                            _cols_uq = [c.get("name") for c in _cand_uq.get("columns", [])[:20]]
                            _hint_uq = f" Real columns in {query_plan.table_used}: {', '.join(_cols_uq)}."
                        retry_feedback = (
                            f"Unrecognised column(s) detected: {'; '.join(_unqual_errs[:3])}.{_hint_uq} "
                            "Use only column names that exist in the schema above."
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "fix_unqual_column"})
                        print(f"[orchestrator:{job_id[:8]}] unqual col check: {_unqual_errs[:3]}", flush=True)
                        continue

                # ── Filter-value verification: fix case mismatches in WHERE literals ──
                # ("status = 'placed'" when the DB stores 'Placed' → silent 0 rows)
                if enriched and getattr(enriched, "entity_columns", None):
                    try:
                        _fv_sql, _fv_corrections = fix_filter_values(
                            query_plan.sql, enriched.entity_columns,
                        )
                        if _fv_corrections:
                            print(f"[pipeline:{job_id}] filter values corrected: {_fv_corrections}", flush=True)
                            query_plan.sql = _fv_sql
                    except Exception as _fe:
                        print(f"[pipeline:{job_id}] filter-value check failed (non-fatal): {_fe}", flush=True)

                # Step 4: Execute query
                execute_result = await self._execute_query(connection_id, query_plan.sql)
                if execute_result.get("error"):
                    await emit({
                        "type": "query.executed",
                        "job_id": job_id,
                        "row_count": 0,
                        "duration_ms": execute_result.get("duration_ms", 0),
                    })
                    if attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                        # Include schema hint in retry message
                        _schema_hint = ""
                        if enriched and enriched.compact_tables:
                            _ct_map = {t["name"]: t for t in enriched.compact_tables}
                            _cand_ct = _ct_map.get(query_plan.table_used if query_plan else "")
                            if _cand_ct:
                                _col_names = [c.get("name") for c in _cand_ct.get("columns", [])[:20]]
                                _schema_hint = f" Valid columns for {query_plan.table_used}: {', '.join(_col_names)}."
                        retry_feedback = (
                            f"Query execution failed: {execute_result['error']}."
                            f"{_schema_hint} Fix the SQL — use only real column names from the schema above."
                        )
                        await emit({
                            "type": "validation.retry",
                            "job_id": job_id,
                            "attempt": attempt + 1,
                            "strategy": "fix_sql_error",
                        })
                        continue
                    else:
                        # Final attempt still hard-failed — diagnose the root cause and
                        # attempt one corrected query before giving up entirely, instead
                        # of returning a bare, chartless error.
                        _rc_ctx = self._root_cause_schema_context(
                            enriched, [query_plan.table_used] if query_plan else [],
                        )
                        _diag = await self._root_cause.diagnose(
                            user_text=user_text,
                            failed_sql=query_plan.sql if query_plan else "",
                            problem=f"SQL execution error: {execute_result['error']}",
                            db_type=db_type,
                            tables_context=_rc_ctx,
                        )
                        _fixed_result = None
                        if _diag.fixed_sql:
                            _fixed_result = await self._execute_query(connection_id, _diag.fixed_sql)
                            if _fixed_result.get("error"):
                                _fixed_result = None

                        if _fixed_result is not None:
                            # Auto-fix worked — feed the RECOVERED result through the
                            # normal success path below so it's narrated/rendered
                            # exactly like any other successful attempt.
                            print(f"[orchestrator:{job_id[:8]}] root-cause auto-fix succeeded ({_diag.root_cause})", flush=True)
                            query_plan.sql = _diag.fixed_sql
                            execute_result = _fixed_result
                            await emit({
                                "type": "query.executed",
                                "job_id": job_id,
                                "row_count": execute_result.get("row_count", 0),
                                "duration_ms": execute_result.get("duration_ms", 0),
                            })
                            render_result = (
                                await self._render_chart(query_plan, execute_result)
                                if output_mode == "chart" else {}
                            )
                            validation = await self._validator.validate(
                                query_plan, execute_result, attempt,
                                expected_chart_type=expected_chart_type,
                            )
                            chart_data = self._build_chart_data(query_plan, execute_result, render_result)
                            final_result = {
                                "job_id": job_id,
                                "score": validation.score,
                                "chart_data": chart_data,
                                "low_confidence": True,
                                "sql": query_plan.sql,
                                "chart_type": query_plan.chart_type,
                                "title": query_plan.title,
                                "table_used": query_plan.table_used,
                                "x_axis_label": query_plan.x_axis_label,
                                "y_axis_label": query_plan.y_axis_label,
                                "validation_details": validation.model_dump(),
                                "root_cause": _diag.model_dump(),
                            }
                            break

                        # Couldn't fix it — explain why instead of a bare error, so the
                        # user gets a real answer instead of a dead end. Built and
                        # returned directly (not via the post-loop narrator) so this
                        # explanation can't be silently overwritten by narration.
                        print(f"[orchestrator:{job_id[:8]}] root-cause diagnosis, no fix available ({_diag.root_cause})", flush=True)
                        await emit({
                            "type": "pipeline.error",
                            "job_id": job_id,
                            "message": f"Query failed after {_MAX_SINGLE_VIZ_ATTEMPTS} attempts: {execute_result['error']}",
                            "recoverable": False,
                        })
                        _explain_result = {
                            "job_id": job_id,
                            "score": 0.0,
                            "chart_data": {"rows": [], "columns": [], "labels": [], "values": []},
                            "low_confidence": True,
                            "sql": query_plan.sql if query_plan else "",
                            "chart_type": "text",
                            "title": "I couldn't complete this query",
                            "table_used": query_plan.table_used if query_plan else "",
                            "x_axis_label": "",
                            "y_axis_label": "",
                            "output_mode": "text",
                            "narrative": _diag.explanation,
                            "validation_details": {},
                            "error": execute_result["error"],
                            "root_cause": _diag.model_dump(),
                        }
                        await emit({
                            "type": "chart.confirmed",
                            "job_id": job_id,
                            "score": 0.0,
                            "chart_data": _explain_result,
                            "low_confidence": True,
                        })
                        if job:
                            job.status = "completed"
                            job.result_payload = _explain_result
                            job.completed_at = datetime.utcnow()
                            await db.commit()
                        return _explain_result

                await emit({
                    "type": "query.executed",
                    "job_id": job_id,
                    "row_count": execute_result.get("row_count", 0),
                    "duration_ms": execute_result.get("duration_ms", 0),
                })
                await set_pipeline_state(redis, job_id, "step", "query_executed")

                # ── Result quality guard ──────────────────────────────────────
                _rows = execute_result.get("rows") or []
                _row_count = execute_result.get("row_count", len(_rows))
                if _row_count == 0 and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                    _schema_hint2 = ""
                    if enriched and enriched.compact_tables:
                        _ct_map2 = {t["name"]: t for t in enriched.compact_tables}
                        _cand_ct2 = _ct_map2.get(query_plan.table_used if query_plan else "")
                        if _cand_ct2:
                            _col_names2 = [c.get("name") for c in _cand_ct2.get("columns", [])[:15]]
                            _schema_hint2 = f" Table {query_plan.table_used} has columns: {', '.join(_col_names2)}."
                    retry_feedback = (
                        f"Query returned 0 rows.{_schema_hint2} "
                        "Possible causes: wrong table selected, overly strict WHERE filter, "
                        "or JOIN key mismatch. Try: (1) a different table, "
                        "(2) remove or loosen WHERE filters, (3) check JOIN keys exist."
                    )
                    await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "zero_rows"})
                    continue
                # Bad JOIN detection: if row_count > 0 but null ratio > 70% in a JOIN query
                elif _rows and "JOIN" in (query_plan.sql or "").upper() and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                    _cols = execute_result.get("columns") or []
                    if _cols:
                        _total = len(_rows) * len(_cols)
                        _nulls = sum(1 for r in _rows for v in r.values() if v is None)
                        if _total > 0 and _nulls / _total > 0.70:
                            retry_feedback = (
                                "JOIN produced too many NULL values (possible key mismatch). "
                                "Verify the JOIN key column names are correct in both tables, "
                                "or try INNER JOIN instead of LEFT JOIN."
                            )
                            await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "bad_join"})
                            continue

                # ── Result shape check: row count must match the requested time buckets ──
                # "last 7 days day-wise" must return 7 rows; 4 rows means zero-count days
                # were dropped (missing date spine); far more means wrong granularity.
                if (
                    _expected_periods and _expected_periods >= 2
                    and _row_count > 0 and attempt < _MAX_SINGLE_VIZ_ATTEMPTS
                ):
                    if _row_count < _expected_periods:
                        retry_feedback = (
                            f"The user asked for a {_gran}-wise breakdown over "
                            f"{_expected_periods} {_gran}s but the query returned only "
                            f"{_row_count} rows — {_gran}s with zero activity were dropped. "
                            f"Rewrite using a date spine CTE (generate all {_expected_periods} "
                            f"{_gran}s, LEFT JOIN the data, COUNT a table column so empty "
                            f"{_gran}s show 0)."
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "missing_periods"})
                        continue
                    if _row_count > _expected_periods * 3:
                        retry_feedback = (
                            f"The user asked for a {_gran}-wise breakdown "
                            f"(~{_expected_periods} rows expected) but the query returned "
                            f"{_row_count} rows — the data is bucketed at a finer granularity "
                            f"than requested. GROUP BY DATE_TRUNC('{_gran}', date_col) so "
                            f"there is exactly one row per {_gran}."
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "wrong_granularity"})
                        continue

                # ── Deterministic data-sanity checks (before expensive LLM validator) ──
                # These are zero-cost Python checks that catch common shape failures
                # without burning a Bedrock call on something obviously wrong.
                _san_rows  = execute_result.get("rows") or []
                _san_count = execute_result.get("row_count", len(_san_rows))
                _san_cols  = execute_result.get("columns") or []
                _san_ct    = (query_plan.chart_type or "").lower()

                # 1. KPI/gauge must return exactly 1 row with 1 numeric column
                _KPI_TYPES = frozenset({"kpi", "kpi_card", "gauge", "metric", "scorecard"})
                if _san_ct in _KPI_TYPES and _san_count > 1 and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                    retry_feedback = (
                        f"KPI/gauge chart returned {_san_count} rows — must return exactly 1 row. "
                        "Use a single aggregate (SUM, COUNT, AVG) with no GROUP BY."
                    )
                    await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "kpi_shape_mismatch"})
                    print(f"[orchestrator:{job_id[:8]}] KPI shape fail: {_san_count} rows", flush=True)
                    continue

                # 2. Cartesian JOIN guard: JOIN + extreme row count = missing JOIN key
                if "JOIN" in (query_plan.sql or "").upper() and _san_count > 200_000 and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                    retry_feedback = (
                        f"Query with JOIN returned {_san_count:,} rows — likely a cartesian product "
                        "(missing or incorrect JOIN condition). "
                        "Verify that JOIN keys exist in both tables and are correctly matched."
                    )
                    await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "cartesian_join"})
                    print(f"[orchestrator:{job_id[:8]}] cartesian guard triggered: {_san_count:,} rows with JOIN", flush=True)
                    continue

                # 3. Numeric presence: chart types that require a numeric measure must have one
                _NUMERIC_CHART_TYPES = frozenset({
                    "bar_vertical", "bar_horizontal", "line", "area", "stacked_bar",
                    "stacked_area", "grouped_bar", "scatter", "bubble", "histogram",
                    "waterfall", "funnel", "combo",
                })
                if (
                    _san_ct in _NUMERIC_CHART_TYPES
                    and _san_rows and _san_cols
                    and attempt < _MAX_SINGLE_VIZ_ATTEMPTS
                ):
                    _first_row = _san_rows[0]
                    _has_numeric = any(
                        isinstance(_first_row.get(c), (int, float))
                        for c in _san_cols
                        if _first_row.get(c) is not None
                    )
                    if not _has_numeric:
                        retry_feedback = (
                            f"Chart type '{_san_ct}' requires at least one numeric column, "
                            "but all returned columns contain non-numeric data. "
                            "Apply an aggregate (SUM, COUNT, AVG) to produce a numeric measure."
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": "no_numeric_column"})
                        print(f"[orchestrator:{job_id[:8]}] no numeric column for {_san_ct}", flush=True)
                        continue

                # 4. NULL aggregate + all-zero value checks (gaps in the checks above)
                if _san_rows and _san_cols and attempt < _MAX_SINGLE_VIZ_ATTEMPTS:
                    from agent_service.agents.sql_utils import check_result_sanity
                    _san_ok, _san_msg = check_result_sanity(
                        _san_rows, _san_cols, _san_ct, query_plan.sql
                    )
                    if not _san_ok and _san_msg:
                        retry_feedback = _san_msg + (
                            " Rewrite the query to return a non-null, non-zero value."
                        )
                        _strategy = (
                            "null_aggregate" if "NULL" in _san_msg
                            else "all_zero_values"
                        )
                        await emit({"type": "validation.retry", "job_id": job_id, "attempt": attempt + 1, "strategy": _strategy})
                        print(f"[orchestrator:{job_id[:8]}] result sanity: {_san_msg[:120]}", flush=True)
                        continue

                # Post-execution: correct the chart title's year range to match actual data.
                # Handles float years (2021.0), non-string columns, and falls back to
                # scanning all columns for year-like values when column name is ambiguous.
                try:
                    import re as _re
                    _rows = execute_result.get("rows") or []
                    _cols = execute_result.get("columns") or []

                    # Primary: find column by name containing a date keyword
                    _date_col = next(
                        (c for c in _cols if isinstance(c, str) and
                         any(k in c.lower() for k in ["year", "date", "month", "period", "quarter"])),
                        None,
                    )

                    # Fallback: scan each column's values — pick one whose first few rows
                    # are all 4-digit year-range numbers (1990–2100)
                    if not _date_col and _rows:
                        for _c in _cols:
                            if not isinstance(_c, str):
                                continue
                            _probe = [_rows[i].get(_c) for i in range(min(5, len(_rows)))]
                            _probe = [v for v in _probe if v is not None]
                            if _probe and all(
                                isinstance(v, (int, float)) and 1990 <= float(v) <= 2100
                                for v in _probe
                            ):
                                _date_col = _c
                                break

                    if _date_col and _rows:
                        _year_ints = []
                        for _r in _rows:
                            _v = _r.get(_date_col)
                            if _v is not None:
                                try:
                                    _year_ints.append(int(float(str(_v))))
                                except (ValueError, TypeError):
                                    pass
                        if len(_year_ints) >= 2:
                            _actual = f"{min(_year_ints)}–{max(_year_ints)}"
                            _old_title = query_plan.title
                            _new_title = _re.sub(
                                r'\(\d{4}\s*[-–]\s*\d{4}\)',
                                f'({_actual})',
                                query_plan.title,
                            )
                            if _new_title != _old_title:
                                query_plan.title = _new_title
                                print(
                                    f"[orchestrator] title corrected: '{_old_title}' → '{_new_title}'",
                                    flush=True,
                                )
                except Exception as _te:
                    print(f"[orchestrator] title correction error (non-fatal): {_te}", flush=True)

                # Step 5: Render chart — only when the answer is meant to be a chart
                if output_mode == "chart":
                    render_result = await self._render_chart(query_plan, execute_result)
                    await emit({
                        "type": "chart.rendered",
                        "job_id": job_id,
                        "chart_type": query_plan.chart_type,
                    })
                else:
                    render_result = {}
                await set_pipeline_state(redis, job_id, "step", "chart_rendered")

                # Step 6: Validate — pass expected_chart_type so type comparison is accurate
                validation = await self._validator.validate(
                    query_plan, execute_result, attempt,
                    expected_chart_type=expected_chart_type,
                )
                await emit({
                    "type": "validation.scored",
                    "job_id": job_id,
                    "score": validation.score,
                    "passed": validation.passed,
                    "dimension_scores": validation.dimension_scores.model_dump(),
                })

                # Use local threshold (0.65) instead of validator's hardcoded 0.80.
                # This lets well-formed results pass on the first attempt.
                _passed = validation.score >= _VALIDATION_PASS_THRESHOLD
                if not _passed and attempt < _MAX_SINGLE_VIZ_ATTEMPTS and validation.retry_feedback:
                    retry_feedback = validation.retry_feedback.feedback
                    await emit({
                        "type": "validation.retry",
                        "job_id": job_id,
                        "attempt": attempt + 1,
                        "strategy": validation.retry_feedback.strategy,
                    })
                    continue

                # Build chart data for frontend
                chart_data = self._build_chart_data(query_plan, execute_result, render_result)
                final_result = {
                    "job_id": job_id,
                    "score": validation.score,
                    "chart_data": chart_data,
                    "low_confidence": validation.low_confidence,
                    "sql": query_plan.sql,
                    "chart_type": query_plan.chart_type,
                    "title": query_plan.title,
                    "table_used": query_plan.table_used,
                    "x_axis_label": query_plan.x_axis_label,
                    "y_axis_label": query_plan.y_axis_label,
                    "validation_details": validation.model_dump(),
                }

                # ── Query memory: remember this success as a future few-shot ──────────
                try:
                    _query_memory.record_success(
                        connection_id, user_text, query_plan.sql,
                        query_plan.table_used, query_plan.chart_type,
                        score=validation.score,
                    )
                except Exception as _qme:
                    print(f"[pipeline:{job_id}] query memory record failed (non-fatal): {_qme}", flush=True)
                break

            if not final_result and query_plan is not None:
                # All single-candidate attempts failed — use last results with low confidence
                chart_data = self._build_chart_data(query_plan, execute_result, {})
                final_result = {
                    "job_id": job_id,
                    "score": 0.0,
                    "chart_data": chart_data,
                    "low_confidence": True,
                    "sql": query_plan.sql,
                    "chart_type": query_plan.chart_type,
                    "title": query_plan.title,
                    "table_used": query_plan.table_used,
                    "x_axis_label": query_plan.x_axis_label,
                    "y_axis_label": query_plan.y_axis_label,
                    "validation_details": {},
                }

            # Correct final_result title: replace user-stated year range with actual data range.
            # Works on the plain dict so Pydantic field-setting is not involved.
            if final_result and execute_result:
                try:
                    import re as _re2
                    _r2 = execute_result.get("rows") or []
                    _c2 = execute_result.get("columns") or []
                    _dc = next(
                        (c for c in _c2 if isinstance(c, str) and
                         any(k in c.lower() for k in ["year", "date", "month", "period", "quarter"])),
                        None,
                    )
                    if not _dc and _r2:
                        for _cc in _c2:
                            if not isinstance(_cc, str):
                                continue
                            _pv = [_r2[i].get(_cc) for i in range(min(5, len(_r2)))]
                            _pv = [v for v in _pv if v is not None]
                            if _pv and all(isinstance(v, (int, float)) and 1990 <= float(v) <= 2100 for v in _pv):
                                _dc = _cc
                                break
                    if _dc and _r2:
                        _yi = []
                        for _rr in _r2:
                            _vv = _rr.get(_dc)
                            if _vv is not None:
                                try:
                                    _yi.append(int(float(str(_vv))))
                                except (ValueError, TypeError):
                                    pass
                        if len(_yi) >= 2:
                            _act = f"{min(_yi)}–{max(_yi)}"
                            _old = final_result.get("title", "")
                            _new = _re2.sub(r'\(\d{4}\s*[-–]\s*\d{4}\)', f'({_act})', _old)
                            if _new != _old:
                                final_result["title"] = _new
                                # Also update nested chart_data title if present
                                if isinstance(final_result.get("chart_data"), dict):
                                    final_result["chart_data"]["title"] = _new
                                print(f"[orchestrator] title corrected: '{_old}' → '{_new}'", flush=True)
                except Exception as _te2:
                    print(f"[orchestrator] title correction (final_result) failed: {_te2}", flush=True)

            # Narrate the result: stream tokens in real-time (single-viz path)
            # or use the batch narrator (multi-candidate path where query_plan is None).
            narrative = ""
            try:
                from agent_service.agents.result_narrator import (
                    narrate_stream as _narrate_stream,
                    narrate_from_result as _narrate_from_result,
                )
                if query_plan is not None:
                    # Stream tokens for real-time display in the frontend
                    async for token in _narrate_stream(user_text, query_plan, execute_result, output_mode):
                        narrative += token
                        await emit({
                            "type": "narrative.token",
                            "job_id": job_id,
                            "token": token,
                        })
                else:
                    # Multi-candidate path: query_plan is None, narrate from the result dict
                    narrative = await _narrate_from_result(user_text, final_result, output_mode)
            except Exception as _ne:
                print(f"[pipeline:{job_id}] narration failed (non-fatal): {_ne}", flush=True)

            # ── Grounding verification: ensure narrator only cited real data ─────────
            # After the stream completes, run a cheap Haiku check that verifies every
            # number in the narrative appears in the actual rows. If hallucinated values
            # are found, the corrected text is emitted as a separate event so the
            # frontend can replace the streamed tokens with the grounded version.
            try:
                from agent_service.agents.result_narrator import verify_narrative_grounding as _verify_grounding
                if narrative and execute_result.get("rows"):
                    _grounded, _corrected = await _verify_grounding(narrative, execute_result)
                    if not _grounded and _corrected and _corrected != narrative:
                        print(f"[pipeline:{job_id[:8]}] narrator grounding corrected — hallucination detected", flush=True)
                        narrative = _corrected
                        await emit({
                            "type": "narrative.corrected",
                            "job_id": job_id,
                            "narrative": narrative,
                        })
            except Exception as _gve:
                print(f"[pipeline:{job_id}] grounding check failed (non-fatal): {_gve}", flush=True)

            final_result["output_mode"] = output_mode
            final_result["narrative"] = narrative
            await emit({
                "type": "result.narrated",
                "job_id": job_id,
                "output_mode": output_mode,
                "narrative": narrative,
            })

            # When multiple candidates are competitive, surface them all so the user
            # can pick the right answer before we commit to the best guess.
            if _multi_candidate_results:
                await emit({
                    "type": "candidates.available",
                    "job_id": job_id,
                    "candidates": _multi_candidate_results,
                    "message": (
                        "I found multiple possible answers. "
                        "Please choose the one that looks right:"
                    ),
                })

            # Attach the per-query cost/token summary. All LLM calls (classify →
            # generate → validate → narrate → ground) have completed by now, so
            # this captures the full turn cost. Non-fatal if tracking is off.
            try:
                from shared.bedrock_client import get_cost_summary
                final_result["cost"] = get_cost_summary()
            except Exception:
                pass

            await emit({
                "type": "chart.confirmed",
                "job_id": job_id,
                "score": final_result["score"],
                "chart_data": final_result,
                "low_confidence": final_result["low_confidence"],
            })
            await set_pipeline_state(redis, job_id, "step", "confirmed")
            await set_pipeline_state(redis, job_id, "result", json.dumps(final_result))

            # Persist result
            if job:
                job.status = "completed"
                job.result_payload = final_result
                job.completed_at = datetime.utcnow()
                await db.commit()

            return final_result

        except Exception as e:
            err_msg = str(e)
            await emit({
                "type": "pipeline.error",
                "job_id": job_id,
                "message": err_msg,
                "recoverable": False,
            })
            await self._fail_job(job_id, err_msg, db)
            return {"error": err_msg}

    async def _get_latest_schema(self, connection_id: str, db: AsyncSession) -> Optional[dict]:
        result = await db.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.connection_id == uuid.UUID(connection_id))
            .order_by(SchemaSnapshot.version.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()
        if snapshot:
            return snapshot.schema_document
        return None

    async def _execute_query(self, connection_id: str, sql: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{QUERY_EXECUTOR_URL}/execute",
                    json={"connection_id": connection_id, "sql": sql, "row_limit": 10000, "timeout_seconds": 30},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {
                    "rows": [], "row_count": 0, "columns": [],
                    "duration_ms": 0, "truncated": False,
                    "error": f"Query executor returned {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            return {
                "rows": [], "row_count": 0, "columns": [],
                "duration_ms": 0, "truncated": False,
                "error": str(e),
            }

    async def _render_chart(self, query_plan, execute_result: dict) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{RENDER_SERVICE_URL}/render",
                    json={
                        "query_plan": {
                            "chart_type": query_plan.chart_type,
                            "x_axis_label": query_plan.x_axis_label,
                            "y_axis_label": query_plan.y_axis_label,
                            "title": query_plan.title,
                        },
                        "rows": execute_result.get("rows", []),
                    },
                    headers={"Accept": "application/json"},
                )
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"Render service error {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    # ── Chart types that use wide-format multi-series SQL ────────────────────
    _MULTI_SERIES_TYPES = frozenset({
        "stacked_bar", "stacked_bar_100", "stacked_bar_horizontal",
        "grouped_bar", "stacked_area", "radar", "ribbon", "marimekko",
    })
    _COMBO_TYPES = frozenset({"combo"})
    _BUBBLE_TYPES = frozenset({"bubble"})
    _HEATMAP_TYPES = frozenset({"heatmap"})
    _HISTOGRAM_TYPES = frozenset({"histogram"})

    @staticmethod
    def _build_chart_data_for_type(chart_type: str, rows: list, columns: list) -> dict:
        """
        Build the chart_data payload for any chart type.
        Always includes raw rows/columns for full fidelity.
        Adds derived fields (labels, values, series, matrix, etc.) per chart type
        so the frontend ChartRenderer can consume them without extra transformation.
        """
        ct = (chart_type or "bar_vertical").lower().strip()
        base = {"rows": rows[:200], "columns": columns}

        if not rows or not columns:
            return {**base, "labels": [], "values": [], "series": []}

        x_col = columns[0]
        y_col = columns[1] if len(columns) > 1 else columns[0]

        # ── Multi-series: stacked_bar, grouped_bar, stacked_bar_100, stacked_bar_horizontal, stacked_area,
        #                  radar, ribbon, marimekko
        if ct in ("stacked_bar", "stacked_bar_100", "stacked_bar_horizontal", "grouped_bar", "stacked_area",
                  "radar", "ribbon", "marimekko") \
                and len(columns) > 2:
            labels = [str(row.get(x_col, "")) for row in rows]
            series = [
                {"name": col, "values": [row.get(col) for row in rows]}
                for col in columns[1:]
            ]
            return {
                **base,
                "labels": labels[:50],
                "values": [row.get(y_col) for row in rows][:50],  # compat fallback
                "series": series,
            }

        # ── Combo: col[0]=category, col[1]=bar value, col[2]=line value
        if ct == "combo" and len(columns) >= 3:
            labels = [str(row.get(x_col, "")) for row in rows]
            return {
                **base,
                "labels": labels[:50],
                "bar_values": [row.get(columns[1]) for row in rows][:50],
                "line_values": [row.get(columns[2]) for row in rows][:50],
                "bar_label": columns[1],
                "line_label": columns[2],
                "values": [row.get(y_col) for row in rows][:50],  # compat
                "series": [],
            }

        # ── Bubble: col[0]=x, col[1]=y, col[2]=size, col[3]=label (optional)
        if ct == "bubble" and len(columns) >= 3:
            label_col = columns[3] if len(columns) > 3 else None
            return {
                **base,
                "labels": [str(row.get(label_col, i)) if label_col else str(i) for i, row in enumerate(rows)][:100],
                "x_values": [row.get(columns[0]) for row in rows][:100],
                "y_values": [row.get(columns[1]) for row in rows][:100],
                "z_values": [row.get(columns[2]) for row in rows][:100],
                "values": [row.get(y_col) for row in rows][:100],  # compat
                "series": [],
            }

        # ── Heatmap: col[0]=row_label, col[1]=col_label, col[2]=value
        if ct == "heatmap" and len(columns) >= 3:
            row_labels = list(dict.fromkeys(str(row.get(columns[0], "")) for row in rows))
            col_labels = list(dict.fromkeys(str(row.get(columns[1], "")) for row in rows))
            cell_map = {
                (str(row.get(columns[0], "")), str(row.get(columns[1], ""))): row.get(columns[2])
                for row in rows
            }
            return {
                **base,
                "labels": row_labels,
                "values": [],
                "series": [],
                "matrix": {
                    "row_labels": row_labels,
                    "col_labels": col_labels,
                    "values": [
                        [cell_map.get((rl, cl)) for cl in col_labels]
                        for rl in row_labels
                    ],
                },
            }

        # ── Histogram: raw numeric values, frontend bins them
        if ct == "histogram":
            raw_values = [row.get(x_col) for row in rows if row.get(x_col) is not None]
            return {
                **base,
                "labels": [],
                "values": raw_values[:2000],
                "series": [],
            }

        # ── Bullet / Scorecard: col[0]=name, col[1]=actual, col[2]=target
        if ct in ("bullet", "scorecard") and len(columns) >= 2:
            labels = [str(row.get(x_col, "")) for row in rows]
            actual = [row.get(columns[1]) for row in rows]
            target = [row.get(columns[2]) for row in rows] if len(columns) > 2 else [None] * len(rows)
            return {
                **base,
                "labels": labels[:50],
                "values": actual[:50],
                "target_values": target[:50],
                "series": [],
            }

        # ── Box Plot: col[0]=category, col[1]=min, col[2]=q1, col[3]=median, col[4]=q3, col[5]=max
        if ct == "box_plot" and len(columns) >= 6:
            labels = [str(row.get(x_col, "")) for row in rows]
            box_stats = [
                {
                    "min": row.get(columns[1]),
                    "q1": row.get(columns[2]),
                    "median": row.get(columns[3]),
                    "q3": row.get(columns[4]),
                    "max": row.get(columns[5]),
                }
                for row in rows
            ]
            return {**base, "labels": labels[:50], "values": [], "box_stats": box_stats[:50], "series": []}

        # ── Sankey / Chord: col[0]=source, col[1]=target, col[2]=value
        if ct in ("sankey", "chord") and len(columns) >= 3:
            all_nodes = list(dict.fromkeys(
                [str(row.get(columns[0], "")) for row in rows] +
                [str(row.get(columns[1], "")) for row in rows]
            ))
            if ct == "sankey":
                links = [
                    {
                        "source": all_nodes.index(str(row.get(columns[0], ""))),
                        "target": all_nodes.index(str(row.get(columns[1], ""))),
                        "value": float(row.get(columns[2]) or 1),
                    }
                    for row in rows
                ]
                return {**base, "labels": all_nodes, "values": [], "nodes": all_nodes, "links": links, "series": []}
            else:  # chord
                n = len(all_nodes)
                matrix = [[0.0] * n for _ in range(n)]
                for row in rows:
                    si = all_nodes.index(str(row.get(columns[0], "")))
                    ti = all_nodes.index(str(row.get(columns[1], "")))
                    matrix[si][ti] += float(row.get(columns[2]) or 1)
                return {
                    **base, "labels": all_nodes, "values": [],
                    "chord_matrix": {"entities": all_nodes, "matrix": matrix}, "series": [],
                }

        # ── Network: col[0]=source, col[1]=target, col[2]=weight (optional)
        if ct == "network" and len(columns) >= 2:
            all_nodes = list(dict.fromkeys(
                [str(row.get(columns[0], "")) for row in rows] +
                [str(row.get(columns[1], "")) for row in rows]
            ))
            edges = [
                {
                    "source": str(row.get(columns[0], "")),
                    "target": str(row.get(columns[1], "")),
                    "weight": float(row.get(columns[2]) or 1) if len(columns) > 2 else 1.0,
                }
                for row in rows
            ]
            return {**base, "labels": all_nodes, "values": [], "network_nodes": all_nodes, "network_edges": edges, "series": []}

        # ── Gantt: col[0]=task, col[1]=start, col[2]=end, col[3]=category (optional)
        if ct == "gantt" and len(columns) >= 3:
            tasks = [
                {
                    "task": str(row.get(columns[0], "")),
                    "start": str(row.get(columns[1], "")),
                    "end": str(row.get(columns[2], "")),
                    "category": str(row.get(columns[3], "")) if len(columns) > 3 else "",
                }
                for row in rows
            ]
            return {**base, "labels": [t["task"] for t in tasks], "values": [], "gantt_tasks": tasks, "series": []}

        # ── Org Chart: col[0]=id, col[1]=name, col[2]=parent_id
        if ct == "org_chart" and len(columns) >= 2:
            org_nodes = [
                {
                    "id": str(row.get(columns[0], "")),
                    "name": str(row.get(columns[1], "")),
                    "parent": str(row.get(columns[2], "")) if len(columns) > 2 else "",
                }
                for row in rows
            ]
            return {**base, "labels": [n["name"] for n in org_nodes], "values": [], "org_nodes": org_nodes, "series": []}

        # ── Calendar Heatmap / Word Cloud / Timeline / Dot Plot / Choropleth
        #    All use standard 2-column (label, value) — fall through to default.

        # ── Default: 2-column standard (bar, line, pie, donut, scatter, area,
        #            waterfall, funnel, treemap, sunburst, gauge, kpi, table, etc.)
        labels = [str(row.get(x_col, "")) for row in rows]
        values = [row.get(y_col) for row in rows]
        return {
            **base,
            "labels": labels[:50],
            "values": values[:50],
            "series": [],
        }

    def _build_chart_data(self, query_plan, execute_result: dict, render_result: dict) -> dict:
        rows = execute_result.get("rows", [])
        columns = execute_result.get("columns", [])
        chart_type = query_plan.chart_type if hasattr(query_plan, "chart_type") else str(query_plan)

        payload = self._build_chart_data_for_type(chart_type, rows, columns)
        payload.update({
            "chart_type": chart_type,
            "title": query_plan.title if hasattr(query_plan, "title") else "",
            "x_axis_label": query_plan.x_axis_label if hasattr(query_plan, "x_axis_label") else "",
            "y_axis_label": query_plan.y_axis_label if hasattr(query_plan, "y_axis_label") else "",
            "image_data": render_result.get("image_base64"),
        })
        return payload

    # ── Multi-candidate helpers ────────────────────────────────────────────────

    async def _run_single_candidate(
        self,
        candidate_table: str,
        candidate_score: float,
        job_id: str,
        user_text: str,
        intent,
        schema,
        db_type: str,
        enriched,
        retrieved_context,
        connection_id: str,
    ) -> Optional[dict]:
        """Run query → execute → render for ONE candidate table.
        Returns a result dict with confidence and chart_data, or None on failure.
        Used by the multi-candidate parallel execution path."""
        import copy

        # Pre-flight: skip candidate tables that have no numeric/metric columns when the
        # intent requires aggregation. Pure dimension/profile tables (all TEXT + DATE columns)
        # will cause the LLM to hallucinate metric column names → SQL compilation errors.
        if enriched and intent and getattr(intent.entities, "metrics", None):
            _NUMERIC_TYPE_FRAGMENTS = (
                "number", "int", "float", "decimal", "numeric", "double",
                "bigint", "smallint", "real", "money", "currency", "amount",
            )
            _ct_map = {t["name"]: t for t in (enriched.compact_tables or [])}
            _ct = _ct_map.get(candidate_table)
            if _ct:
                cols = _ct.get("columns", [])
                has_numeric = any(
                    c.get("semantic_type") == "metric"
                    or any(frag in (c.get("type") or "").lower() for frag in _NUMERIC_TYPE_FRAGMENTS)
                    for c in cols
                )
                if not has_numeric:
                    print(
                        f"[pipeline:{job_id}] skipping {candidate_table!r} "
                        f"— no numeric/metric columns for aggregation query",
                        flush=True,
                    )
                    return None

        try:
            # Build a focused RetrievedContext that only mentions this candidate.
            local_ctx = copy.copy(retrieved_context) if retrieved_context else None
            if local_ctx is not None:
                focused = [c for c in (local_ctx.candidates or []) if c.table_name == candidate_table]
                if not focused:
                    from agent_service.agents.graph_rag_retriever import TableCandidate
                    focused = [TableCandidate(table_name=candidate_table, score=candidate_score)]
                local_ctx.candidates = focused
                local_ctx.primary_tables = [candidate_table]

            query_plan = await self._query.generate(
                intent, schema, db_type, None, 1, enriched,
                retrieved_context=local_ctx,
                conversation_history=None,
            )

            exec_result = await self._execute_query(connection_id, query_plan.sql)
            if exec_result.get("error"):
                print(
                    f"[pipeline:{job_id}] candidate {candidate_table!r} "
                    f"exec error: {str(exec_result['error'])[:80]}",
                    flush=True,
                )
                return None

            rows = exec_result.get("rows") or []
            cols = exec_result.get("columns") or []
            if not rows:
                return None

            from agent_service.agents.candidate_ranker import (
                score_result_quality, compute_final_confidence,
            )
            quality = score_result_quality(rows, cols)
            if quality < 0.15:
                return None
            confidence = compute_final_confidence(candidate_score, quality, candidate_score)

            render_result = await self._render_chart(query_plan, exec_result)
            chart_data = self._build_chart_data(query_plan, exec_result, render_result)

            # Correct title: replace user-stated year range with actual data range
            import re as _re_c
            corrected_title = query_plan.title
            try:
                _dc = next(
                    (c for c in cols if isinstance(c, str) and
                     any(k in c.lower() for k in ["year", "date", "month", "period", "quarter"])),
                    None,
                )
                if not _dc:
                    for _cc in cols:
                        if not isinstance(_cc, str):
                            continue
                        _pv = [rows[i].get(_cc) for i in range(min(5, len(rows)))]
                        _pv = [v for v in _pv if v is not None]
                        if _pv and all(isinstance(v, (int, float)) and 1990 <= float(v) <= 2100 for v in _pv):
                            _dc = _cc
                            break
                if _dc:
                    _yi = []
                    for _r in rows:
                        _v = _r.get(_dc)
                        if _v is not None:
                            try:
                                _yi.append(int(float(str(_v))))
                            except (ValueError, TypeError):
                                pass
                    if len(_yi) >= 2:
                        _act = f"{min(_yi)}–{max(_yi)}"
                        _new_t = _re_c.sub(r'\(\d{4}\s*[-–]\s*\d{4}\)', f'({_act})', query_plan.title)
                        if _new_t != query_plan.title:
                            corrected_title = _new_t
                            chart_data["title"] = _new_t
                            print(
                                f"[pipeline:{job_id}] candidate title corrected: "
                                f"'{query_plan.title}' → '{_new_t}'",
                                flush=True,
                            )
            except Exception as _tce:
                print(f"[pipeline:{job_id}] candidate title correction error: {_tce}", flush=True)

            return {
                "table": candidate_table,
                "rank_score": round(candidate_score, 4),
                "result_quality": round(quality, 3),
                "confidence": confidence,
                "sql": query_plan.sql,
                "chart_type": query_plan.chart_type,
                "title": corrected_title,
                "x_axis_label": query_plan.x_axis_label,
                "y_axis_label": query_plan.y_axis_label,
                "table_used": query_plan.table_used,
                "chart_data": chart_data,
                "row_count": len(rows),
            }
        except Exception as exc:
            print(f"[pipeline:{job_id}] candidate {candidate_table!r} failed: {exc}", flush=True)
            return None

    def _candidate_to_final_result(self, job_id: str, candidate: dict) -> dict:
        """Convert a _run_single_candidate result dict into the final_result shape
        expected by the narration + chart.confirmed steps."""
        return {
            "job_id": job_id,
            "score": candidate["confidence"],
            "chart_data": candidate["chart_data"],
            "low_confidence": candidate["confidence"] < 0.65,
            "sql": candidate["sql"],
            "chart_type": candidate["chart_type"],
            "title": candidate["title"],
            "table_used": candidate["table"],
            "x_axis_label": candidate["x_axis_label"],
            "y_axis_label": candidate["y_axis_label"],
            "validation_details": {
                "rank_score": candidate["rank_score"],
                "result_quality": candidate["result_quality"],
            },
        }

    async def _build_schema_overview(self, user_text: str, schema, enriched) -> str:
        """Generate a plain-English overview of the database schema for SCHEMA_EXPLORE intent."""
        tables_info: list[str] = []
        if enriched and hasattr(enriched, "compact_tables"):
            for t in (enriched.compact_tables or [])[:35]:
                name = t.get("name", "")
                description = t.get("description", "")
                col_count = len(t.get("columns", []))
                if name:
                    tables_info.append(
                        f"- {name}: {description or 'no description'} ({col_count} columns)"
                    )
        elif hasattr(schema, "tables"):
            for t in (schema.tables or [])[:35]:
                name = getattr(t, "table_name", getattr(t, "name", ""))
                if name:
                    tables_info.append(f"- {name}")

        table_list = "\n".join(tables_info) if tables_info else "No tables found"
        prompt = (
            f"User asked: \"{user_text}\"\n\n"
            f"Available tables and views:\n{table_list}\n\n"
            f"Write a friendly 3-5 sentence overview of what data is available — what kinds of "
            f"business questions can be answered, what domains the data covers. "
            f"Then provide exactly 3 example questions the user could ask, formatted as:\n"
            f"**Example questions you can ask:**\n- ...\n- ...\n- ..."
        )
        try:
            overview = await bedrock_invoke(
                model_id=BEDROCK_HAIKU_MODEL,
                system_prompt=(
                    "You are a helpful data analyst explaining what data is available. "
                    "Be concise, friendly, and use plain language. "
                    "Format example questions as a markdown list."
                ),
                user_message=prompt,
                max_tokens=700,
                temperature=0.3,
            )
            return (overview or "").strip()
        except Exception as exc:
            print(f"[orchestrator] schema overview failed: {exc}", flush=True)
            total = len(tables_info)
            return (
                f"You have access to {total} tables and views. "
                f"Ask me anything about your data, like revenue trends, user activity, or product performance."
            )

    async def run_dashboard_pipeline(
        self,
        job_id: str,
        user_text: str,
        project_id: str,
        user_id: str,
        connection_id: str,
        redis,
        db: AsyncSession,
    ) -> dict:
        async def emit(event: dict):
            await _ws_manager.broadcast(job_id, event)
            await publish_pipeline_event(redis, job_id, event)

        await emit({"type": "dashboard.decomposing", "job_id": job_id})

        sub_intents = await self._decompose_dashboard(user_text)
        sub_intents = sub_intents[:DASHBOARD_MAX_CHARTS]

        await emit({
            "type": "dashboard.decomposed",
            "job_id": job_id,
            "chart_count": len(sub_intents),
            "charts": sub_intents,
        })

        semaphore = asyncio.Semaphore(CHART_CONCURRENCY)
        results = []

        async def run_one(idx: int, sub_text: str):
            async with semaphore:
                sub_job_id = f"{job_id}_chart_{idx}"
                async with AsyncSessionLocal() as sub_db:
                    sub_job = PipelineJob(
                        id=uuid.UUID(sub_job_id) if _is_valid_uuid(sub_job_id) else uuid.uuid4(),
                        project_id=uuid.UUID(project_id),
                        user_id=uuid.UUID(user_id),
                        job_type="SINGLE_VIZ",
                        status="pending",
                        input_payload={"user_text": sub_text, "connection_id": connection_id},
                        created_at=datetime.utcnow(),
                    )
                    sub_db.add(sub_job)
                    await sub_db.commit()
                    await sub_db.refresh(sub_job)

                    result = await self.run_single_viz_pipeline(
                        job_id=str(sub_job.id),
                        user_text=sub_text,
                        project_id=project_id,
                        user_id=user_id,
                        connection_id=connection_id,
                        redis=redis,
                        db=sub_db,
                    )
                await emit({
                    "type": "dashboard.chart_done",
                    "job_id": job_id,
                    "chart_index": idx,
                    "chart_result": result,
                })
                return result

        tasks = [run_one(i, text) for i, text in enumerate(sub_intents)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        charts = [r for r in results if isinstance(r, dict) and not r.get("error")]
        layout = _auto_layout(len(charts))

        # Persist dashboard + widgets to DB
        dashboard = await self._save_dashboard(
            project_id=project_id,
            user_id=user_id,
            title=f"Dashboard — {user_text[:60]}",
            charts=charts,
            layout=layout,
            connection_id=connection_id,
            db=db,
        )

        dashboard_result = {
            "job_id": job_id,
            "dashboard_id": str(dashboard.id) if dashboard else None,
            "chart_count": len(charts),
            "charts": charts,
            "layout": layout,
        }

        await emit({"type": "dashboard.complete", "job_id": job_id, "result": dashboard_result})

        job_result = await db.execute(
            select(PipelineJob).where(PipelineJob.id == uuid.UUID(job_id))
        )
        job = job_result.scalar_one_or_none()
        if job:
            job.status = "completed"
            job.result_payload = dashboard_result
            job.completed_at = datetime.utcnow()
            await db.commit()

        return dashboard_result

    async def _save_dashboard(
        self,
        project_id: str,
        user_id: str,
        title: str,
        charts: list[dict],
        layout: list[dict],
        connection_id: str,
        db: AsyncSession,
    ) -> Optional[Dashboard]:
        try:
            dashboard = Dashboard(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project_id),
                name=title,
                layout_config={"layout": layout},
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(dashboard)
            await db.flush()

            for i, (chart, pos) in enumerate(zip(charts, layout)):
                chart_data = chart.get("chart_data", {})
                widget = Widget(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard.id,
                    title=chart.get("title", f"Chart {i+1}"),
                    widget_type="chart",
                    chart_type=chart.get("chart_type"),
                    sql_query=chart.get("sql"),
                    connection_id=uuid.UUID(connection_id) if connection_id else None,
                    position_x=pos.get("x", 0),
                    position_y=pos.get("y", 0),
                    width=pos.get("w", 6),
                    height=pos.get("h", 4),
                    validation_score=chart.get("score"),
                    validation_status="confirmed" if not chart.get("low_confidence") else "low_confidence",
                    chart_data=chart_data,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(widget)

            await db.commit()
            await db.refresh(dashboard)
            return dashboard
        except Exception:
            return None

    async def _decompose_dashboard(self, user_text: str) -> list[str]:
        prompt = f"""The user wants a dashboard: "{user_text}"
Decompose this into 2-5 individual chart requests. Each chart should focus on one metric or dimension.
Return ONLY a JSON array of strings. Example: ["Monthly revenue trend as line chart", "Top 10 products by revenue as bar chart"]
No explanation. No markdown. Just the JSON array."""
        try:
            raw = await bedrock_invoke(
                model_id=DASHBOARD_DECOMPOSE_MODEL,
                system_prompt="You decompose dashboard requests into individual chart requests. Return JSON array only.",
                user_message=prompt,
                max_tokens=512,
                temperature=0.2,
            )
            raw = raw.strip()
            if raw.startswith("```"):
                import re
                raw = re.sub(r"^```[a-z]*\n?", "", raw)
                raw = re.sub(r"```$", "", raw).strip()
            charts = json.loads(raw)
            if isinstance(charts, list):
                return [str(c) for c in charts[:DASHBOARD_MAX_CHARTS]]
        except Exception:
            pass
        # Fallback: single chart from user text
        return [user_text]

    async def _fail_job(self, job_id: str, error: str, db: AsyncSession):
        try:
            result = await db.execute(
                select(PipelineJob).where(PipelineJob.id == uuid.UUID(job_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.status = "failed"
                job.error_message = error
                job.completed_at = datetime.utcnow()
                await db.commit()
        except Exception:
            pass

    async def trigger_export(
        self,
        export_job_id: str,
        pipeline_job_id: str,
        dashboard_id: str,
        project_id: str,
        user_id: str,
        export_type: str,
        theme: str,
        include_chat: bool,
        token_expiry_days: int,
        redis,
        db: AsyncSession,
    ) -> dict:
        """
        Full export pipeline:
        1. Mark ExportJob as generating
        2. Emit export.started event
        3. Load Dashboard + Widgets from DB
        4. Refresh all widget data in parallel via query executor
        5. Create export token if html + include_chat
        6. Call export service HTTP endpoint
        7. Save file via upload_file
        8. Update ExportJob with status=completed
        9. Emit export.ready event
        """
        import os as _os
        from shared.models.phase4 import ExportJob, ExportToken
        from shared.export_tokens import create_export_token
        from shared.file_storage import upload_file
        from shared.redis_client import publish_pipeline_event

        EXPORT_SERVICE_URL = _os.getenv("EXPORT_SERVICE_URL", "http://localhost:8005")
        API_BASE = _os.getenv("AGENT_SERVICE_URL", "http://localhost:8001")

        async def emit(event: dict):
            await publish_pipeline_event(redis, pipeline_job_id, event)

        # Step 1: Mark ExportJob status = generating
        ej_result = await db.execute(
            select(ExportJob).where(ExportJob.id == uuid.UUID(export_job_id))
        )
        export_job = ej_result.scalar_one_or_none()
        if not export_job:
            raise ValueError(f"ExportJob {export_job_id} not found")

        export_job.status = "generating"
        export_job.started_at = datetime.utcnow()
        await db.commit()

        # Update pipeline job status
        pj_result = await db.execute(
            select(PipelineJob).where(PipelineJob.id == uuid.UUID(pipeline_job_id))
        )
        pipeline_job = pj_result.scalar_one_or_none()
        if pipeline_job:
            pipeline_job.status = "running"
            pipeline_job.started_at = datetime.utcnow()
            await db.commit()

        # Step 2: Emit export.started
        await emit({
            "type": "export.started",
            "job_id": pipeline_job_id,
            "export_job_id": export_job_id,
            "dashboard_id": dashboard_id,
            "export_type": export_type,
        })

        try:
            # Step 3: Load Dashboard + Widgets
            dash_result = await db.execute(
                select(Dashboard).where(Dashboard.id == uuid.UUID(dashboard_id))
            )
            dashboard = dash_result.scalar_one_or_none()
            if not dashboard:
                raise ValueError(f"Dashboard {dashboard_id} not found")

            from shared.models.widgets import Widget as WidgetModel
            widgets_result = await db.execute(
                select(WidgetModel).where(WidgetModel.dashboard_id == uuid.UUID(dashboard_id))
            )
            widgets = list(widgets_result.scalars().all())

            # Step 4: Refresh widget data in parallel via query executor
            semaphore = asyncio.Semaphore(5)

            async def refresh_widget(widget) -> dict:
                async with semaphore:
                    refreshed_data = widget.chart_data or {}
                    if widget.sql_query and widget.connection_id:
                        try:
                            exec_result = await self._execute_query(
                                str(widget.connection_id), widget.sql_query
                            )
                            if not exec_result.get("error"):
                                rows = exec_result.get("rows", [])
                                columns = exec_result.get("columns", [])
                                labels = [str(r.get(columns[0], "")) for r in rows] if columns else []
                                values = [r.get(columns[1]) for r in rows] if len(columns) > 1 else []
                                refreshed_data = {
                                    "rows": rows[:500],
                                    "columns": columns,
                                    "labels": labels[:200],
                                    "values": values[:200],
                                }
                        except Exception:
                            pass  # Use cached chart_data on failure

                    return {
                        "id": str(widget.id),
                        "title": widget.title,
                        "widget_type": widget.widget_type,
                        "chart_type": widget.chart_type,
                        "sql_query": widget.sql_query,
                        "position_x": widget.position_x,
                        "position_y": widget.position_y,
                        "width": widget.width,
                        "height": widget.height,
                        "x_axis_label": (widget.config or {}).get("x_axis_label", "") if widget.config else "",
                        "y_axis_label": (widget.config or {}).get("y_axis_label", "") if widget.config else "",
                        "chart_data": refreshed_data,
                    }

            widget_dicts = await asyncio.gather(
                *[refresh_widget(w) for w in widgets],
                return_exceptions=False,
            )

            # Step 5: Create export token if html + include_chat
            raw_token = ""
            if export_type == "html" and include_chat:
                raw_token, _token_record = await create_export_token(
                    db=db,
                    export_job_id=uuid.UUID(export_job_id),
                    project_id=uuid.UUID(project_id),
                    expiry_days=token_expiry_days,
                    scopes=["chat:read"],
                )

            # Step 6: Call export service
            export_payload = {
                "dashboard_title": dashboard.name,
                "theme": theme,
                "include_chat": include_chat and bool(raw_token),
                "export_token": raw_token,
                "api_base": API_BASE,
                "widgets": list(widget_dicts),
            }

            html_content = ""
            async with httpx.AsyncClient(timeout=120.0) as client:
                if export_type == "html":
                    resp = await client.post(
                        f"{EXPORT_SERVICE_URL}/export/html",
                        json=export_payload,
                    )
                    if resp.status_code != 200:
                        raise ValueError(f"Export service returned {resp.status_code}: {resp.text[:300]}")
                    resp_data = resp.json()
                    html_content = resp_data.get("html_content", "")
                else:
                    raise ValueError(f"Unsupported export_type: {export_type}")

            # Step 7: Save file via upload_file
            file_bytes = html_content.encode("utf-8")
            filename = f"export-{export_job_id[:8]}.{export_type}"
            storage_result = await upload_file(
                file_bytes=file_bytes,
                filename=filename,
                mime_type="text/html; charset=utf-8",
                project_id=project_id,
            )
            s3_key = storage_result["s3_key"]
            size_bytes = storage_result.get("size_bytes", len(file_bytes))

            # Build a relative download URL
            download_url = f"/export/jobs/{export_job_id}/download"

            # Step 8: Update ExportJob
            export_job.status = "completed"
            export_job.s3_key = s3_key
            export_job.file_size_bytes = size_bytes
            export_job.download_url = download_url
            export_job.completed_at = datetime.utcnow()
            await db.commit()

            if pipeline_job:
                pipeline_job.status = "completed"
                pipeline_job.result_payload = {
                    "export_job_id": export_job_id,
                    "download_url": download_url,
                    "file_size_bytes": size_bytes,
                    "s3_key": s3_key,
                }
                pipeline_job.completed_at = datetime.utcnow()
                await db.commit()

            # Step 9: Emit export.ready
            await emit({
                "type": "export.ready",
                "job_id": pipeline_job_id,
                "export_job_id": export_job_id,
                "download_url": download_url,
                "file_size_bytes": size_bytes,
            })

            return {
                "export_job_id": export_job_id,
                "status": "completed",
                "download_url": download_url,
                "file_size_bytes": size_bytes,
                "s3_key": s3_key,
            }

        except Exception as exc:
            err_msg = str(exc)
            export_job.status = "failed"
            export_job.error_message = err_msg
            export_job.completed_at = datetime.utcnow()
            await db.commit()

            if pipeline_job:
                pipeline_job.status = "failed"
                pipeline_job.error_message = err_msg
                pipeline_job.completed_at = datetime.utcnow()
                await db.commit()

            await emit({
                "type": "export.failed",
                "job_id": pipeline_job_id,
                "export_job_id": export_job_id,
                "error": err_msg,
            })
            raise


async def _infer_key_columns_for_hint(
    hint_tables: list[str],
    chart_spec: dict,
    enriched,
) -> dict:
    """
    Targeted key_column inference for user-specified hint tables when schema_matcher
    didn't naturally rank them (so key_columns would otherwise default to all-null).
    One fast Haiku call with only the hint tables' columns in context.
    """
    import re as _re
    _default = {"dimension": None, "metric": None, "date": None, "group_by": None}

    hint_metas = [t for t in (enriched.compact_tables or []) if t.get("name") in hint_tables]
    if not hint_metas:
        return _default

    chart_summary = {
        "type": chart_spec.get("type"),
        "title": chart_spec.get("title"),
        "x_axis_label": chart_spec.get("x_axis_label"),
        "y_axis_label": chart_spec.get("y_axis_label"),
        "x_tick_labels": (chart_spec.get("x_tick_labels") or [])[:8],
        "estimated_values": chart_spec.get("estimated_values") or {},
        "data_point_count": chart_spec.get("data_point_count", 0),
    }

    prompt = (
        f"CHART:\n{json.dumps(chart_summary, indent=2)}\n\n"
        f"TABLES (user-specified — must use these):\n{json.dumps(hint_metas, indent=2)}\n\n"
        "Identify the best columns from these tables to reproduce the chart.\n"
        "Return ONLY valid JSON:\n"
        '{"dimension": "col_for_xaxis_groupby", "metric": "col_to_aggregate", '
        '"date": "date_col_or_null", "group_by": "secondary_groupby_or_null"}\n\n'
        "Rules:\n"
        "- dimension: categorical column for x-axis / GROUP BY\n"
        "- metric: numeric column for SUM/COUNT/AVG\n"
        "- date: date/timestamp for time-series charts, null otherwise\n"
        "- group_by: secondary grouping for stacked/grouped charts, null otherwise\n"
        "- Only use column names that exist in the tables above\n"
        "- For KPI charts: dimension=null, metric=the single numeric value column"
    )

    try:
        raw = await bedrock_invoke(
            model_id=BEDROCK_HAIKU_MODEL,
            system_prompt="You are a database schema analyst. Return only valid JSON, no prose.",
            user_message=prompt,
            temperature=0.0,
            max_tokens=256,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = _re.sub(r"^```(?:json)?\s*\n?", "", raw)
            raw = _re.sub(r"\n?```\s*$", "", raw)
        result = json.loads(raw)
        if isinstance(result, dict):
            cols = {
                "dimension": result.get("dimension") or None,
                "metric":    result.get("metric")    or None,
                "date":      result.get("date")      or None,
                "group_by":  result.get("group_by")  or None,
            }
            print(
                f"[orchestrator] hint key_column inference → {cols}",
                flush=True,
            )
            return cols
    except Exception as _e:
        print(f"[orchestrator] ⚠ hint key_column inference failed: {_e}", flush=True)

    return _default


def _cross_chart_consistency_check(charts: list[dict]) -> list[str]:
    """
    Lightweight post-processing: detect charts that likely share source data but
    returned inconsistent results (e.g. same table, radically different row counts).
    Returns a list of human-readable warning strings (empty if no issues found).
    Non-blocking — only logs; never modifies chart data.
    """
    issues: list[str] = []

    # Group charts by their primary table
    table_groups: dict[str, list[dict]] = {}
    for chart in charts:
        tables = (chart.get("table_used") or "").split(",")
        primary = (tables[0] or "").strip()
        if primary:
            table_groups.setdefault(primary, []).append(chart)

    for table, group in table_groups.items():
        if len(group) < 2:
            continue

        # Check for radically different row counts for same source table
        row_counts = [c.get("row_count", 0) for c in group if c.get("row_count") is not None]
        if len(row_counts) >= 2:
            max_rc = max(row_counts)
            min_rc = min(row_counts)
            if max_rc > 0 and min_rc > 0:
                ratio = max_rc / min_rc
                if ratio > 20:
                    titles = [c.get("title", "unknown") for c in group]
                    issues.append(
                        f"Table '{table}': charts {titles} have row counts {min_rc}–{max_rc} "
                        f"(ratio={ratio:.1f}×) — possible wrong GROUP BY or date filter on one chart"
                    )

    return issues


def _is_valid_uuid(val: str) -> bool:
    try:
        uuid.UUID(val)
        return True
    except ValueError:
        return False


def _auto_layout(chart_count: int) -> list[dict]:
    positions = []
    cols_per_row = 2 if chart_count > 2 else chart_count
    for i in range(chart_count):
        row = i // cols_per_row
        col = i % cols_per_row
        positions.append({
            "chart_index": i,
            "x": col * 6,
            "y": row * 4,
            "w": 6,
            "h": 4,
        })
    return positions
