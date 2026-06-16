import asyncio
import uuid
import json
import os
from datetime import datetime
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

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
from agent_service.services.ws_manager import manager as _ws_manager

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

    async def run_single_viz_pipeline(
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
            # STEP 1: Classify intent
            await set_pipeline_state(redis, job_id, "step", "classifying")
            intent = await self._intent.classify(user_text)
            await emit({
                "type": "intent.classified",
                "job_id": job_id,
                "intent_type": intent.intent_type,
                "vagueness_score": intent.vagueness_score,
                "confidence": intent.confidence,
            })
            await set_pipeline_state(redis, job_id, "step", "intent_classified")

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

            # STEP 3+4+5+6: Query → Execute → Render → Validate (up to 4 attempts)
            final_result = None
            retry_feedback: Optional[str] = None
            _MAX_SINGLE_VIZ_ATTEMPTS = 4
            # Extract user-requested chart type (None if user didn't specify one)
            expected_chart_type: Optional[str] = getattr(intent.entities, "chart_type", None)
            # How the user wants the answer presented: "chart" (visualize) or "text" (prose answer)
            output_mode: str = (getattr(intent, "output_mode", "chart") or "chart").lower()

            for attempt in range(1, _MAX_SINGLE_VIZ_ATTEMPTS + 1):
                # Step 3: Generate query — pass attempt so temperature scales on retries
                await set_pipeline_state(redis, job_id, "step", f"generating_query_attempt_{attempt}")
                query_plan = await self._query.generate(intent, schema, db_type, retry_feedback, attempt, enriched)
                await emit({
                    "type": "query.generated",
                    "job_id": job_id,
                    "sql": query_plan.sql,
                    "chart_type": query_plan.chart_type,
                    "table_used": query_plan.table_used,
                    "title": query_plan.title,
                })
                await set_pipeline_state(redis, job_id, "step", "query_generated")

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
                        retry_feedback = f"Query execution failed: {execute_result['error']}. Fix the SQL syntax or table/column names."
                        await emit({
                            "type": "validation.retry",
                            "job_id": job_id,
                            "attempt": attempt + 1,
                            "strategy": "fix_sql_error",
                        })
                        continue
                    else:
                        await emit({
                            "type": "pipeline.error",
                            "job_id": job_id,
                            "message": f"Query failed after {_MAX_SINGLE_VIZ_ATTEMPTS} attempts: {execute_result['error']}",
                            "recoverable": False,
                        })
                        await self._fail_job(job_id, execute_result["error"], db)
                        return {"error": execute_result["error"]}

                await emit({
                    "type": "query.executed",
                    "job_id": job_id,
                    "row_count": execute_result.get("row_count", 0),
                    "duration_ms": execute_result.get("duration_ms", 0),
                })
                await set_pipeline_state(redis, job_id, "step", "query_executed")

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

                if not validation.passed and attempt < _MAX_SINGLE_VIZ_ATTEMPTS and validation.retry_feedback:
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
                break

            if not final_result:
                # All attempts failed — use last results with low confidence
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

            # Narrate the result: a concise chart caption, or a prose answer for text mode.
            narrative = ""
            try:
                from agent_service.agents.result_narrator import narrate as _narrate
                narrative = await _narrate(user_text, query_plan, execute_result, output_mode)
            except Exception as _ne:
                print(f"[pipeline:{job_id}] narration failed (non-fatal): {_ne}", flush=True)
            final_result["output_mode"] = output_mode
            final_result["narrative"] = narrative
            await emit({
                "type": "result.narrated",
                "job_id": job_id,
                "output_mode": output_mode,
                "narrative": narrative,
            })

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
                sub_job = PipelineJob(
                    id=uuid.UUID(sub_job_id) if _is_valid_uuid(sub_job_id) else uuid.uuid4(),
                    project_id=uuid.UUID(project_id),
                    user_id=uuid.UUID(user_id),
                    job_type="SINGLE_VIZ",
                    status="pending",
                    input_payload={"user_text": sub_text, "connection_id": connection_id},
                    created_at=datetime.utcnow(),
                )
                db.add(sub_job)
                await db.commit()
                await db.refresh(sub_job)

                result = await self.run_single_viz_pipeline(
                    job_id=str(sub_job.id),
                    user_text=sub_text,
                    project_id=project_id,
                    user_id=user_id,
                    connection_id=connection_id,
                    redis=redis,
                    db=db,
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
