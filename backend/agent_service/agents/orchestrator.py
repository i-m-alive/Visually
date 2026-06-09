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
from shared.models.phase3 import ScreenshotJob, ChartReplicationState, HintQueueEntry
from shared.schemas.schema import SemanticSchemaDocument
from shared.redis_client import publish_pipeline_event, set_pipeline_state
from shared.bedrock_client import bedrock_invoke, BEDROCK_HAIKU_MODEL
from agent_service.agents.intent_classifier import IntentClassifier
from agent_service.agents.query_agent import QueryAgent
from agent_service.agents.validator_agent import ValidatorAgent
from agent_service.agents.schema_matcher import SchemaMatcher
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
        self._schema_matcher = SchemaMatcher()

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

                # Step 5: Render chart
                render_result = await self._render_chart(query_plan, execute_result)
                await emit({
                    "type": "chart.rendered",
                    "job_id": job_id,
                    "chart_type": query_plan.chart_type,
                })
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

    # ─── SCREENSHOT PIPELINE ─────────────────────────────────────────────────

    async def run_screenshot_pipeline(
        self,
        job_id: str,
        screenshot_job_id: str,
        uploaded_images: list[dict],
        project_id: str,
        user_id: str,
        connection_id: str,
        redis,
        db: AsyncSession,
        mode: str = "db",
        user_table_hints: list[str] = [],
        csv_data: list[dict] = [],
        user_context: str = "",
        pbit_bytes: Optional[bytes] = None,
        user_column_hints: list = [],
    ) -> dict:
        """Full screenshot replication pipeline: vision → schema match → per-chart SQL loop → assemble.

        mode="db"  — (default) use the live database connection.
        mode="csv" — use uploaded CSV files via DuckDB; connection_id is overridden
                     with a "csv_session:" path so ALL downstream calls (value sampler,
                     executor, validator) transparently route to DuckDB.

        user_table_hints — table names injected at position 0 with confidence=1.0 after
                           schema matching (Mode 2 — user knows which tables to use).
        user_context     — free-text description of the screenshot provided by the user
                           (Mode 3 — Guided Replication). Parsed into structured SQL
                           signals and injected into schema matching and SQL generation.
        """
        from agent_service.agents.vision_agent import VisionAgent
        from shared.bedrock_client import start_token_tracking, get_token_summary
        vision_agent = VisionAgent()
        start_token_tracking()

        # CSV mode: override connection_id so every downstream call routes to DuckDB.
        # The session directory is deterministic (/tmp/csv_{job_id}) so we can set it
        # here before the parallel schema-fetch task runs.
        csv_session_dir: str = ""
        if mode == "csv":
            import os as _os
            csv_session_dir = f"/tmp/csv_{job_id}"
            _os.makedirs(csv_session_dir, exist_ok=True)
            connection_id = f"csv_session:{csv_session_dir}"
            print(
                f"[pipeline:{job_id}] CSV mode — session dir: {csv_session_dir}",
                flush=True,
            )

        async def emit(event: dict):
            await publish_pipeline_event(redis, job_id, event)

        # Step 1: Vision parsing
        print(f"\n[pipeline:{job_id}] ── STEP 1: Vision parsing  images={len(uploaded_images)}", flush=True)
        await self._update_screenshot_job_status(screenshot_job_id, "vision_parsing", db)
        await emit({"type": "vision.started", "job_id": job_id, "image_count": len(uploaded_images)})

        print(f"[pipeline:{job_id}] calling VisionAgent.process_images ...", flush=True)
        manifest = await vision_agent.process_images(uploaded_images, job_id, redis)
        charts = manifest["charts"]
        report_metadata: dict = manifest.get(
            "report_metadata",
            {"report_title": None, "page_tabs": [], "logo_text": None, "colour_theme": None},
        )
        if report_metadata.get("report_title"):
            print(
                f"[pipeline:{job_id}] Report metadata → "
                f"title={report_metadata['report_title']!r}  "
                f"tabs={[t['name'] for t in report_metadata.get('page_tabs', [])]}",
                flush=True,
            )
        print(f"[pipeline:{job_id}] VisionAgent done → {len(charts)} charts detected", flush=True)
        await self._save_manifest(screenshot_job_id, manifest, db)

        if not charts:
            print(f"[pipeline:{job_id}] ✗ No charts detected — aborting", flush=True)
            await emit({"type": "pipeline.error", "job_id": job_id,
                       "message": "No charts detected in uploaded images.", "recoverable": False})
            await self._update_screenshot_job_status(screenshot_job_id, "failed", db,
                                                     error="No charts detected")
            return {"error": "No charts detected"}

        await emit({"type": "vision.parsed", "job_id": job_id, "chart_count": len(charts),
                    "chart_types": [c["type"] for c in charts], "charts": charts})

        await self._create_replication_states(screenshot_job_id, charts, db)

        # Steps 1b + 2 run IN PARALLEL — filter detection (vision) and schema fetch (DB) are
        # independent.  Running them concurrently saves ~1-3 s on every pipeline invocation.
        print(
            f"[pipeline:{job_id}] ── STEPS 1b+2 (parallel): filter detection + schema fetch",
            flush=True,
        )
        await self._update_screenshot_job_status(screenshot_job_id, "schema_matching", db)

        async def _detect_filters_task() -> list:
            try:
                filter_tasks = [
                    vision_agent.detect_filters(img["bytes"], img.get("filename", ""))
                    for img in uploaded_images
                ]
                filter_results = await asyncio.gather(*filter_tasks, return_exceptions=True)
                out: list[dict] = []
                seen_cols: set[str] = set()
                for res in filter_results:
                    if isinstance(res, list):
                        for f in res:
                            col = f.get("column_hint", "")
                            if col and col not in seen_cols:
                                seen_cols.add(col)
                                out.append(f)
                print(f"[pipeline:{job_id}] Filter detection → {len(out)} unique filters", flush=True)
                return out
            except Exception as fe:
                print(f"[pipeline:{job_id}] ⚠ Filter detection failed (non-fatal): {fe}", flush=True)
                return []

        async def _schema_fetch_task() -> tuple:
            if mode == "csv":
                from agent_service.agents.csv_ingestor import save_csvs, ingest_csvs
                from agent_service.agents.csv_relationship_detector import detect_relationships
                # Write CSV bytes to disk, parse into schema_doc, detect FK joins
                save_csvs(csv_data, job_id)
                _schema_doc = ingest_csvs(csv_session_dir)
                _schema_doc = detect_relationships(csv_session_dir, _schema_doc)
                _db_type = "duckdb"
                print(
                    f"[pipeline:{job_id}] CSV schema built → "
                    f"tables={len(_schema_doc.get('tables', []))}  db_type={_db_type}",
                    flush=True,
                )
            else:
                _schema_doc = await self._get_schema(project_id, db)
                _db_type = await self._get_connection_db_type(connection_id, db)
                print(
                    f"[pipeline:{job_id}] schema fetched → "
                    f"tables={len(_schema_doc.get('tables', []))}  db_type={_db_type}",
                    flush=True,
                )
            _enriched = await _schema_cache.get_or_build(connection_id, _schema_doc, _db_type)
            return _schema_doc, _db_type, _enriched

        (detected_filters, (schema_doc, db_type, enriched)) = await asyncio.gather(
            _detect_filters_task(),
            _schema_fetch_task(),
        )

        await emit({"type": "schema.fetched", "job_id": job_id,
                    "table_count": len(schema_doc.get("tables", [])),
                    "ambiguous_columns": len(enriched.ambiguous_columns)})

        # Step 2.5: Parse user context (Mode 3 — Guided Replication)
        # context_parser runs always; spec_reader runs in parallel for BI documentation PDFs.
        # Both outputs are used downstream: context_parser for schema-matching keywords,
        # spec_reader for per-chart SQL templates injected into query_agent.
        parsed_context: dict = {}
        report_spec = None          # ReportSpec from spec_reader (None when not a BI spec doc)
        chart_spec_hints: dict = {} # {chart_id: ChartSpecHint} — built after vision + spec parse
        calc_col_map: dict = {}     # {col_name: case_when_sql} — PDF calculated column substitutions
        _spec_business_rules: list = []  # Required WHERE conditions from PDF spec (e.g. isdeleted = FALSE)

        if user_context and user_context.strip():
            print(f"[pipeline:{job_id}] ── STEP 2.5: Parsing user context (Mode 3)", flush=True)
            from agent_service.agents.context_parser import parse_user_context as _puc
            from agent_service.agents.spec_reader import (
                parse_report_spec as _prs,
                is_spec_document as _isd,
                match_chart_to_spec as _mcts,
            )

            _is_spec = _isd(user_context)
            if _is_spec:
                print(f"[pipeline:{job_id}] spec document detected — running spec_reader in parallel", flush=True)
                _puc_result, _spec_result = await asyncio.gather(
                    _puc(user_context),
                    _prs(user_context, db_type),
                    return_exceptions=True,
                )
            else:
                _puc_result = await _puc(user_context)
                _spec_result = None

            parsed_context = _puc_result if not isinstance(_puc_result, Exception) else {}

            # spec_result is a ReportSpec dataclass or None/Exception
            if _is_spec and not isinstance(_spec_result, Exception) and _spec_result is not None:
                report_spec = _spec_result
                # Match each vision-detected chart to a documented ChartSpecHint
                for _chart in charts:
                    _hint = _mcts(_chart, report_spec)
                    if _hint:
                        chart_spec_hints[_chart["id"]] = _hint
                print(
                    f"[pipeline:{job_id}] spec_reader: "
                    f"{len(report_spec.charts)} chart specs extracted, "
                    f"{len(chart_spec_hints)}/{len(charts)} vision charts matched",
                    flush=True,
                )
                # Build calculated column substitution map for query_agent (Fix 2).
                # Calculated columns are Power BI derived fields that don't exist as raw DB columns
                # (e.g. 'replacement' → CASE WHEN p.replacement IS NOT NULL THEN 'Replaced' ELSE ...).
                # Injecting these prevents the model from grouping by raw FK integers.
                for _cc in (report_spec.calculated_columns or []):
                    _cc_name = (_cc.get("name") or "").strip().lower()
                    _cc_expr = (_cc.get("sql_expression") or "").strip()
                    if _cc_name and _cc_expr:
                        calc_col_map[_cc_name] = _cc_expr
                if calc_col_map:
                    print(
                        f"[pipeline:{job_id}] calc_col_map: {len(calc_col_map)} calculated column "
                        f"substitution(s): {list(calc_col_map.keys())}",
                        flush=True,
                    )
                # Capture business rules (e.g. "isdeleted = FALSE on all bullhorn_ tables").
                # These are REQUIRED WHERE conditions that Power BI enforces at the model level
                # but are not obvious from column names alone.
                _spec_business_rules: list[str] = list(report_spec.business_rules or [])
                if _spec_business_rules:
                    print(
                        f"[pipeline:{job_id}] business_rules: {len(_spec_business_rules)} rule(s) from spec",
                        flush=True,
                    )

            await emit({
                "type": "context.parsed",
                "job_id": job_id,
                "chart_intent": parsed_context.get("chart_intent", ""),
                "filter_count": len(parsed_context.get("implied_filters") or []),
                "has_date_range": bool((parsed_context.get("implied_date_range") or {}).get("start")),
                "spec_charts_extracted": len(report_spec.charts) if report_spec else 0,
                "spec_charts_matched": len(chart_spec_hints),
            })

        # Step 2.6: PBIT parsing — run after schema fetch so we have compact_tables for name mapping.
        # parse_pbit + translate DAX→SQL happen in parallel; result enriches calc_col_map,
        # business_rules, and builds per-chart pbit_column_hints for query_agent injection.
        pbit_model = None
        pbit_column_hints: dict = {}   # {chart_id: pbit_column_hint dict}

        if pbit_bytes:
            print(f"[pipeline:{job_id}] ── STEP 2.6: PBIT parsing", flush=True)
            try:
                from agent_service.utils.pbit_parser import (
                    parse_pbit as _parse_pbit,
                    translate_pbit_to_sql as _translate_pbit,
                    build_calc_col_map_from_pbit as _pbit_calc_map,
                    build_measure_map_from_pbit as _pbit_meas_map,
                    find_best_pbit_match as _find_best_pbit,
                    build_pbit_chart_hint as _build_pbit_hint,
                    map_pbit_tables_to_db as _map_pbit_tables,
                )
                # Parse ZIP synchronously (fast I/O) then translate DAX async
                _raw_model = _parse_pbit(pbit_bytes)
                pbit_model = await _translate_pbit(_raw_model, db_type)

                # Merge PBIT calculated columns into calc_col_map (PBIT overrides spec_reader)
                _pbit_cc = _pbit_calc_map(pbit_model)
                if _pbit_cc:
                    calc_col_map = {**calc_col_map, **_pbit_cc}
                    print(
                        f"[pipeline:{job_id}] PBIT calc_col_map merged: "
                        f"{len(_pbit_cc)} column(s) — total {len(calc_col_map)}",
                        flush=True,
                    )

                # Inject PBIT relationships into business_rules if not already present
                # (relationships encode FK integrity; useful as sql_constraints)
                _pbit_meas = _pbit_meas_map(pbit_model)
                if _pbit_meas:
                    print(
                        f"[pipeline:{job_id}] PBIT measures translated: "
                        f"{len(_pbit_meas)} measure(s) available for injection",
                        flush=True,
                    )

                # Build per-chart PBIT hints by matching PBIT visuals to vision charts
                _matched = 0
                for _chart in charts:
                    _best_visual = _find_best_pbit(
                        _chart, pbit_model.visuals, min_score=0.15
                    )
                    if _best_visual:
                        _hint = _build_pbit_hint(
                            _best_visual,
                            _pbit_meas,
                            enriched.compact_tables,
                            pbit_model.relationships,
                        )
                        pbit_column_hints[_chart["id"]] = _hint
                        _matched += 1
                        print(
                            f"[pipeline:{job_id}] PBIT match: chart={_chart['id']} "
                            f"→ visual='{_best_visual.title}' "
                            f"type={_best_visual.chart_type_hint} "
                            f"bindings={list(_best_visual.field_bindings.keys())} "
                            f"db_tables={_hint.get('db_tables', [])}",
                            flush=True,
                        )

                print(
                    f"[pipeline:{job_id}] PBIT: {_matched}/{len(charts)} charts matched to visuals",
                    flush=True,
                )
                await emit({
                    "type": "pbit.parsed",
                    "job_id": job_id,
                    "measures": len(pbit_model.measures),
                    "calculated_columns": len(pbit_model.calculated_columns),
                    "relationships": len(pbit_model.relationships),
                    "visuals_matched": _matched,
                    "total_visuals": len(pbit_model.visuals),
                })
            except Exception as _pbit_err:
                print(
                    f"[pipeline:{job_id}] ⚠ PBIT parsing failed (non-fatal): {_pbit_err}",
                    flush=True,
                )

        # Step 3: Schema matching — rank candidate tables for every chart IN PARALLEL (one task per chart)
        print(f"[pipeline:{job_id}] ── STEP 3: Schema matching  charts={len(charts)}", flush=True)
        await emit({"type": "schema.matching", "job_id": job_id, "chart_count": len(charts)})

        async def match_one(chart_spec: dict) -> list:
            candidates = await self._schema_matcher.rank_candidates(
                chart_spec,
                enriched,
                user_context=user_context,
                parsed_context=parsed_context if parsed_context else None,
            )
            print(f"[schema_match:{chart_spec['id']}] → {[(c['tables'], round(c.get('confidence',0),2)) for c in candidates[:3]]}", flush=True)

            # PBIT-derived table hint: if a PBIT visual was matched to this chart AND it has
            # mapped DB tables, prepend a PBIT-mandated candidate at position 0.
            # This takes priority over both user_table_hints and schema_matcher output.
            _cid = chart_spec["id"]
            _pbit_hint = pbit_column_hints.get(_cid)
            if _pbit_hint and _pbit_hint.get("db_tables"):
                _pbit_db_tables = _pbit_hint["db_tables"]
                # Build key_columns from PBIT field bindings
                _pbit_key_cols = {"dimension": None, "metric": None, "date": None, "group_by": None}
                for _role, _refs in (_pbit_hint.get("field_bindings") or {}).items():
                    if not _refs:
                        continue
                    _col = _refs[0].split(".", 1)[-1].strip("[]")
                    _role_lower = _role.lower()
                    if "category" in _role_lower or "axis" in _role_lower or "x" == _role_lower:
                        _pbit_key_cols["dimension"] = _col
                    elif "y" == _role_lower or "value" in _role_lower or "measure" in _role_lower:
                        _pbit_key_cols["metric"] = _col
                    elif "date" in _role_lower or "time" in _role_lower:
                        _pbit_key_cols["date"] = _col
                    elif "group" in _role_lower or "legend" in _role_lower or "series" in _role_lower:
                        _pbit_key_cols["group_by"] = _col
                # Build join condition from PBIT relationships
                _pbit_join = (
                    " | ".join(_pbit_hint["join_conditions"])
                    if _pbit_hint.get("join_conditions") else None
                )
                _pbit_cand = {
                    "tables":        _pbit_db_tables,
                    "key_columns":   _pbit_key_cols,
                    "join":          _pbit_join,
                    "reasoning":     "PBIT field bindings — ground-truth table/column selection",
                    "confidence":    1.0,
                    "user_mandated": True,
                    "from_pbit":     True,
                }
                candidates = [_pbit_cand] + [
                    _c for _c in candidates
                    if sorted(_c.get("tables", [])) != sorted(_pbit_db_tables)
                ]
                print(
                    f"[schema_match:{_cid}] ✓ PBIT tables prepended: {_pbit_db_tables}"
                    f"  key_cols={_pbit_key_cols}  join={_pbit_join}",
                    flush=True,
                )

            # Apply user_column_hints: override key_columns on any candidate whose table set
            # matches the hints. This lets users pick exact dimension/metric/date columns.
            if user_column_hints:
                for _candidate in candidates:
                    for _uch in user_column_hints:
                        _uch_table = _uch.get("table", "")
                        if _uch_table in _candidate.get("tables", []):
                            _existing_kc = dict(_candidate.get("key_columns") or {})
                            for _kname in ("dimension", "metric", "date", "group_by"):
                                if _uch.get(_kname):
                                    _existing_kc[_kname] = _uch[_kname]
                            _candidate["key_columns"] = _existing_kc
                            _candidate["user_mandated"] = True
                            print(
                                f"[schema_match:{_cid}] ✓ user_column_hints applied for "
                                f"table={_uch_table}: {_existing_kc}",
                                flush=True,
                            )
                            break

            # Mode 2: prepend user-specified table hint at position 0 with confidence=1.0.
            # The schema matcher pool is preserved as the fallback candidate list so that
            # if the hint table cannot produce a valid query the pipeline still has options.
            if user_table_hints:
                _hint_key_cols = {
                    "dimension": None, "metric": None, "date": None, "group_by": None
                }

                # Fix A: exact-match search first
                for _c in candidates:
                    if sorted(_c.get("tables", [])) == sorted(user_table_hints):
                        _hint_key_cols = _c.get("key_columns", _hint_key_cols)
                        break

                # Fix B: partial match — if any hint table appears in a candidate, borrow
                # non-null key_columns from it (better than staying all-null)
                if all(v is None for v in _hint_key_cols.values()):
                    for _c in candidates:
                        if any(t in _c.get("tables", []) for t in user_table_hints):
                            for k, v in (_c.get("key_columns") or {}).items():
                                if v is not None and _hint_key_cols.get(k) is None:
                                    _hint_key_cols[k] = v

                # Fix C: if still all-null, run a fast targeted LLM inference pass
                if all(v is None for v in _hint_key_cols.values()):
                    print(
                        f"[schema_match:{chart_spec['id']}] hint tables not in candidates "
                        "— running targeted key_column inference",
                        flush=True,
                    )
                    _hint_key_cols = await _infer_key_columns_for_hint(
                        user_table_hints, chart_spec, enriched
                    )

                # Fix D: collect FK join conditions for ALL pairs in the user-hint table set.
                # Previously only hints[0] vs hints[1] was checked, which produced a single
                # (often wrong) join when ≥3 tables were specified.  Now every pair is looked
                # up and all found conditions are surfaced to the query agent.
                _hint_join = None
                if len(user_table_hints) >= 2:
                    _join_parts: list[str] = []
                    for _hi in range(len(user_table_hints)):
                        for _hj in range(_hi + 1, len(user_table_hints)):
                            _cond = enriched.relationship_graph.get_join_condition(
                                user_table_hints[_hi], user_table_hints[_hj]
                            )
                            if _cond:
                                _join_parts.append(_cond)
                    if _join_parts:
                        _hint_join = " | ".join(_join_parts)
                        print(
                            f"[schema_match:{chart_spec['id']}] FK joins for hint"
                            f" ({len(_join_parts)} pairs): {_hint_join}",
                            flush=True,
                        )

                _hint_cand = {
                    "tables":       user_table_hints,
                    "key_columns":  _hint_key_cols,
                    "join":         _hint_join,
                    "reasoning":    "user-specified table hint — always tried first",
                    "confidence":   1.0,
                    "user_mandated": True,   # signals query_agent to never deviate
                }
                candidates = [_hint_cand] + [
                    _c for _c in candidates
                    if sorted(_c.get("tables", [])) != sorted(user_table_hints)
                ]
                print(
                    f"[schema_match:{chart_spec['id']}] ✓ user hint prepended: {user_table_hints}"
                    f"  key_cols={_hint_key_cols}  join={_hint_join}",
                    flush=True,
                )
            return candidates

        # All charts matched simultaneously — no semaphore here (pure LLM calls, low resource cost)
        all_candidates = await asyncio.gather(*[match_one(c) for c in charts], return_exceptions=True)
        chart_candidates = {
            charts[i]["id"]: (result if isinstance(result, list) else [])
            for i, result in enumerate(all_candidates)
        }

        # Step 3.5: Cross-chart column coordination — one LLM call to resolve conflicts where
        # different charts independently assigned the same semantic concept (e.g. "client name")
        # to different table.column combinations.  Fully non-fatal: pipeline continues on failure.
        print(f"[pipeline:{job_id}] ── STEP 3.5: Cross-chart column coordination", flush=True)
        from agent_service.agents.column_coordinator import coordinate_columns, apply_coordination
        coordination = await coordinate_columns(
            chart_specs=charts,
            all_candidates=chart_candidates,
            enriched=enriched,
        )

        # Step 3b: Resolve filter column names then sample available values.
        # resolve_all_filters maps vision-generated hints ("employment_type") to real
        # DB column names using fuzzy scoring against compact_tables — avoids sampling
        # columns that don't exist in the schema.
        filter_configs: list[dict] = []
        if detected_filters:
            print(f"[pipeline:{job_id}] ── STEP 3b: Filter column resolution + value sampling  filters={len(detected_filters)}", flush=True)
            from agent_service.agents.value_sampler import sample_distinct_for_filter as _sdf
            from agent_service.agents.filter_resolver import resolve_all_filters

            resolved_filters = resolve_all_filters(detected_filters, enriched.compact_tables)

            candidate_tables: list[str] = []
            for cands in chart_candidates.values():
                if cands:
                    for t in cands[0].get("tables", []):
                        if t not in candidate_tables:
                            candidate_tables.append(t)

            async def _sample_one_filter(flt: dict) -> Optional[dict]:
                col_to_use = flt.get("resolved_column") or flt.get("column_hint", "")
                resolved_table = flt.get("resolved_table")
                display_name = flt.get("display_name", col_to_use.replace("_", " ").title())
                if not col_to_use:
                    return None
                tables_to_try = []
                if resolved_table:
                    tables_to_try.append(resolved_table)
                tables_to_try.extend(t for t in candidate_tables[:5] if t != resolved_table)
                for table in tables_to_try:
                    try:
                        available_vals = await _sdf(
                            connection_id, table, col_to_use,
                            compact_tables=enriched.compact_tables,
                        )
                        if available_vals:
                            return {
                                "id": str(uuid.uuid4()),
                                "column": col_to_use,
                                "display_name": display_name,
                                "filter_type": flt.get("filter_type", "multi_select"),
                                "available_values": available_vals,
                                "table": table,
                                "resolution_score": flt.get("resolution_score", 0.0),
                            }
                    except Exception:
                        continue
                return None

            # All filters sampled in parallel — previously sequential (each waited for prior)
            _filter_results = await asyncio.gather(
                *[_sample_one_filter(flt) for flt in resolved_filters],
                return_exceptions=True,
            )
            filter_configs = [r for r in _filter_results if isinstance(r, dict)]
            print(f"[pipeline:{job_id}] Filter configs → {len(filter_configs)} with available values", flush=True)

        # Steps 3.6 + 3.7: Context synthesis (merge all signals) + pre-sampling, run in PARALLEL.
        # Synthesis produces a ResolvedChartSpec per chart so query_agent gets pre-decided
        # table/column/metric rather than having to reconcile competing sources itself.
        print(
            f"[pipeline:{job_id}] ── STEPS 3.6+3.7: Context synthesis + pre-sampling (parallel)"
            f"  charts={len(charts)}",
            flush=True,
        )
        from agent_service.agents.value_sampler import sample_top_candidates as _stc
        from agent_service.agents.context_synthesizer import synthesize_chart_context as _synthesize
        _presample_sem = asyncio.Semaphore(PRESAMPLE_CONCURRENCY)

        async def _presample_one(chart_spec):
            async with _presample_sem:
                return await _stc(
                    connection_id=connection_id,
                    candidates=chart_candidates.get(chart_spec["id"], []),
                    chart_spec=chart_spec,
                    max_candidates=3,
                    compact_tables=enriched.compact_tables,
                )

        async def _synthesize_one(chart_spec: dict):
            cid = chart_spec["id"]
            try:
                resolved = await _synthesize(
                    chart_spec=chart_spec,
                    candidates=chart_candidates.get(cid, []),
                    enriched=enriched,
                    pbit_hint=pbit_column_hints.get(cid),
                    spec_hint=chart_spec_hints.get(cid),
                    user_context=user_context,
                    parsed_context=parsed_context if parsed_context else None,
                    calc_col_map=calc_col_map if calc_col_map else None,
                    business_rules=_spec_business_rules if _spec_business_rules else None,
                    user_column_hints=user_column_hints if user_column_hints else None,
                    db_type=db_type,
                )
            except Exception as _se:
                print(f"[pipeline:{job_id}] ⚠ synthesis exception chart={cid}: {_se}", flush=True)
                resolved = None
            return cid, resolved

        _synthesis_raw, presample_results = await asyncio.gather(
            asyncio.gather(*[_synthesize_one(c) for c in charts], return_exceptions=True),
            asyncio.gather(*[_presample_one(c) for c in charts], return_exceptions=True),
        )

        resolved_chart_specs: dict = {}
        for _sr in _synthesis_raw:
            if isinstance(_sr, tuple):
                _scid, _sresolved = _sr
                if _sresolved is not None:
                    resolved_chart_specs[_scid] = _sresolved

        # chart_presample_cache: {chart_id: {0: ctx, 1: ctx, 2: ctx}}
        chart_presample_cache: dict[str, dict] = {}
        for c, res in zip(charts, presample_results):
            if isinstance(res, Exception):
                print(f"[pipeline:{job_id}] ⚠ presample failed for chart {c['id']} (non-fatal): {res}", flush=True)
                chart_presample_cache[c["id"]] = {}
            else:
                chart_presample_cache[c["id"]] = res or {}

        print(
            f"[pipeline:{job_id}] synthesis complete: {len(resolved_chart_specs)}/{len(charts)} charts resolved",
            flush=True,
        )

        # Step 4: N PARALLEL AGENTS — one dedicated agent per chart, all running simultaneously
        # Extract global date context from all chart specs (year/month from x_tick_labels)
        from agent_service.agents.value_sampler import extract_dashboard_date_context as _eddc
        dashboard_date_context = _eddc(charts)
        if dashboard_date_context.get("inferred_period"):
            print(
                f"[pipeline:{job_id}] dashboard date context: {dashboard_date_context.get('inferred_period')}",
                flush=True,
            )

        print(f"[pipeline:{job_id}] ── STEP 4: Spawning {len(charts)} parallel chart agents", flush=True)
        await self._update_screenshot_job_status(screenshot_job_id, "query_generating", db)

        async def process_chart(chart_spec: dict) -> dict:
            cid = chart_spec["id"]
            # Apply cross-chart coordination to the top candidate before SQL generation.
            # This ensures all charts that reference the same concept use the same column.
            raw_candidates = chart_candidates.get(cid, [])
            if raw_candidates:
                coordinated_top = apply_coordination(cid, raw_candidates[0], coordination)
                candidates_for_loop = [coordinated_top] + raw_candidates[1:]
            else:
                candidates_for_loop = raw_candidates

            # Crop the original chart region for Phase 3B visual comparison.
            # Match by source_image filename → uploaded image bytes.
            cropped_image: Optional[bytes] = None
            try:
                src_filename = chart_spec.get("source_image", "")
                for img_dict in uploaded_images:
                    if img_dict.get("filename") == src_filename:
                        from utils.image_processor import crop_chart_region as _crop
                        bb = chart_spec.get("bounding_box", {})
                        cropped_image, _ = _crop(img_dict["bytes"], bb)
                        break
            except Exception as _ce:
                print(f"[chart:{cid}] ⚠ could not crop image for visual comparison (non-fatal): {_ce}", flush=True)

            # Pick up the PDF spec hint for this chart (None when no spec or no match)
            _spec_hint = chart_spec_hints.get(cid)
            # Pick up the PBIT visual hint for this chart (None when no PBIT or no match)
            _pbit_hint = pbit_column_hints.get(cid)
            # Pick up the pre-resolved chart spec (merged all context sources in step 3.6)
            _resolved_spec = resolved_chart_specs.get(cid)

            # Each chart gets its own DB session — sharing one AsyncSession across
            # concurrent asyncio.gather tasks causes "concurrent operations are not
            # permitted" errors because AsyncSession is not concurrency-safe.
            from shared.database import AsyncSessionLocal
            async with AsyncSessionLocal() as chart_db:
                return await self._run_chart_replication_loop(
                    chart_spec=chart_spec,
                    enriched=enriched,
                    connection_id=connection_id,
                    screenshot_job_id=screenshot_job_id,
                    job_id=job_id,
                    redis=redis,
                    db=chart_db,
                    candidates=candidates_for_loop,
                    cropped_image_bytes=cropped_image,
                    dashboard_date_context=dashboard_date_context,
                    presample_contexts=chart_presample_cache.get(cid, {}),
                    user_context=user_context,
                    parsed_context=parsed_context if parsed_context else None,
                    spec_hint=_spec_hint,
                    calc_col_map=calc_col_map if calc_col_map else None,
                    business_rules=_spec_business_rules if _spec_business_rules else None,
                    pbit_column_hint=_pbit_hint if _pbit_hint else None,
                    resolved_spec=_resolved_spec,
                )

        chart_results = await asyncio.gather(
            *[process_chart(spec) for spec in charts],
            return_exceptions=True,
        )

        # Step 5: Dashboard assembly (initial pass)
        confirmed = [r for r in chart_results if isinstance(r, dict) and not r.get("error")]
        print(f"[pipeline:{job_id}] ── STEP 5: Dashboard assembly  confirmed={len(confirmed)}/{len(charts)}", flush=True)

        # ── Step 6: Verification loop ─────────────────────────────────────────
        # Compare each chart's SQL result against the original vision spec.
        # Charts that fail are re-queued for a single retry pass.
        from agent_service.agents.canvas_verifier import CanvasVerifier
        verifier = CanvasVerifier()

        # Option 5: Two verification loops.
        # First loop catches structural failures; subsequent loops retry low-score charts
        # until all pass threshold or MAX_VERIFY_LOOPS is exhausted.
        MAX_VERIFY_LOOPS = 3
        all_charts = confirmed.copy()
        loop_idx = 1

        while loop_idx <= MAX_VERIFY_LOOPS:
            print(f"[pipeline:{job_id}] ── STEP 6.{loop_idx}: Verification loop  charts={len(all_charts)}", flush=True)
            await emit({
                "type": "verification.started",
                "job_id": job_id,
                "loop": loop_idx,
                "chart_count": len(all_charts),
            })

            report = verifier.verify_dashboard(all_charts, loop=loop_idx)

            # Emit per-chart results
            for vr in report.results:
                await emit({
                    "type": "verification.chart.result",
                    "job_id": job_id,
                    "loop": loop_idx,
                    "chart_id": vr.chart_id,
                    "chart_title": vr.chart_title,
                    "passed": vr.passed,
                    "overall_score": vr.overall_score,
                    "type_match": vr.type_match,
                    "has_data": vr.has_data,
                    "row_count": vr.row_count,
                    "issues": vr.issues,
                })
                print(
                    f"[verify:{vr.chart_id}] {'✓' if vr.passed else '✗'} "
                    f"score={vr.overall_score:.2f}  issues={vr.issues}",
                    flush=True,
                )

            # Identify what needs retry:
            # (1) charts with 0 rows — structural failure, verifier now hard-fails these
            # (2) low-confidence charts — have data but LLM validator couldn't match values
            zero_row_ids = {vr.chart_id for vr in report.results if not vr.has_data}
            low_conf_ids = {
                c.get("chart_spec", {}).get("id")
                for c in all_charts
                if c.get("status") == "low_confidence"
            } - {None}

            # Charts that barely passed (0.72–0.80) on the first loop — retry them in
            # loop 2 to push them to higher confidence before calling them done.
            barely_passed_ids: set = set()
            if loop_idx == 1:
                barely_passed_ids = {
                    c.get("chart_spec", {}).get("id")
                    for c in all_charts
                    if 0.72 <= c.get("score", 0.0) <= 0.80
                    and c.get("status") != "low_confidence"
                } - {None}
                if barely_passed_ids:
                    print(
                        f"[pipeline:{job_id}] borderline charts flagged for loop-2 retry: "
                        f"{len(barely_passed_ids)} charts with score 0.72-0.80",
                        flush=True,
                    )

            needs_retry_ids = zero_row_ids | low_conf_ids | barely_passed_ids

            if not needs_retry_ids:
                # All confirmed with no low-confidence — truly done
                await emit({
                    "type": "verification.complete",
                    "job_id": job_id,
                    "loop": loop_idx,
                    "passed": True,
                    "overall_score": report.overall_score,
                    "passed_charts": report.passed_charts,
                    "failed_charts": 0,
                })
                print(f"[pipeline:{job_id}] ✓ All charts passed verification (loop {loop_idx})", flush=True)
                break

            # Build feedback for each chart that needs retry
            feedback_map: dict[str, str] = {}
            for vr in report.results:
                if vr.chart_id in zero_row_ids:
                    feedback_map[vr.chart_id] = (
                        vr.retry_feedback or "Query returned 0 rows — try a different table or WHERE clause."
                    )
            for c in all_charts:
                cid = c.get("chart_spec", {}).get("id")
                if cid in low_conf_ids:
                    score = c.get("score", 0.0)
                    feedback_map[cid] = (
                        f"Previous best score was {score:.0%}. The chart has data but values "
                        "don't match the screenshot. Try a completely different table, date range, "
                        "or aggregation method to better replicate the original chart."
                    )

            failed_ids = needs_retry_ids

            await emit({
                "type": "verification.retry.started",
                "job_id": job_id,
                "loop": loop_idx,
                "failed_chart_ids": list(failed_ids),
                "failed_count": len(failed_ids),
            })
            print(
                f"[pipeline:{job_id}] ↻ Retrying {len(failed_ids)} charts in parallel: "
                f"{len(zero_row_ids)} zero-row + {len(low_conf_ids)} low-confidence (loop {loop_idx})",
                flush=True,
            )

            # Find the chart specs for all charts needing retry
            failed_specs = [c for c in charts if c["id"] in failed_ids]

            # Re-run with max 3 attempts — these charts need a fresh approach
            # (0-row: need different table; low-confidence: need different aggregation/range)
            retry_tasks = []
            for chart_spec in failed_specs:
                cid = chart_spec["id"]
                vfeedback = feedback_map.get(cid, "")
                raw_candidates = chart_candidates.get(cid, [])

                async def _retry_one(spec=chart_spec, feedback=vfeedback, candidates=raw_candidates):
                    _sh = chart_spec_hints.get(spec["id"])
                    _ph = pbit_column_hints.get(spec["id"])
                    _rs = resolved_chart_specs.get(spec["id"])
                    from shared.database import AsyncSessionLocal
                    async with AsyncSessionLocal() as retry_db:
                        return await self._run_chart_replication_loop(
                            chart_spec=spec,
                            candidates=candidates,
                            enriched=enriched,
                            connection_id=connection_id,
                            job_id=job_id,
                            screenshot_job_id=screenshot_job_id,
                            redis=redis,
                            db=retry_db,
                            verify_feedback=feedback,
                            max_attempts=4 + loop_idx,  # loop 1→5, loop 2→6, loop 3→7
                            dashboard_date_context=dashboard_date_context,
                            spec_hint=_sh,
                            calc_col_map=calc_col_map if calc_col_map else None,
                            business_rules=_spec_business_rules if _spec_business_rules else None,
                            pbit_column_hint=_ph if _ph else None,
                            resolved_spec=_rs,
                        )

                retry_tasks.append(_retry_one())

            retry_results = await asyncio.gather(*retry_tasks, return_exceptions=True)
            improved = [r for r in retry_results if isinstance(r, dict) and not r.get("error")]

            # Merge: replace failed chart entries with improved ones
            improved_ids = {r["chart_spec"]["id"] for r in improved if r.get("chart_spec")}
            all_charts = [c for c in all_charts if c.get("chart_spec", {}).get("id") not in improved_ids]
            all_charts.extend(improved)

            # Second verification pass — confirm which retried charts actually improved
            post_retry_report = verifier.verify_dashboard(all_charts, loop=loop_idx)
            post_retry_zero_row_ids = {vr.chart_id for vr in post_retry_report.results if not vr.has_data}
            post_retry_low_conf_ids = {
                c.get("chart_spec", {}).get("id")
                for c in all_charts
                if c.get("status") == "low_confidence"
            } - {None}
            post_retry_still_failing = post_retry_zero_row_ids | post_retry_low_conf_ids

            # Emit updated scores for charts that were retried
            for vr in post_retry_report.results:
                if vr.chart_id in failed_ids:
                    await emit({
                        "type": "verification.chart.result",
                        "job_id": job_id,
                        "loop": loop_idx,
                        "chart_id": vr.chart_id,
                        "chart_title": vr.chart_title,
                        "overall_score": vr.overall_score,
                        "passed": vr.passed,
                        "issues": vr.issues,
                    })
                    print(
                        f"[verify:post-retry:{vr.chart_id}] {'✓' if vr.passed else '✗'} "
                        f"score={vr.overall_score:.2f}",
                        flush=True,
                    )

            if not post_retry_still_failing:
                # All charts resolved after retry — exit cleanly
                await emit({
                    "type": "verification.complete",
                    "job_id": job_id,
                    "loop": loop_idx,
                    "passed": True,
                    "overall_score": post_retry_report.overall_score,
                    "passed_charts": post_retry_report.total_charts,
                    "failed_charts": 0,
                })
                print(
                    f"[pipeline:{job_id}] ✓ Post-retry verification: all {post_retry_report.total_charts} "
                    f"charts pass (loop {loop_idx})",
                    flush=True,
                )
                break

            loop_idx += 1

        else:
            # Exhausted all loops — emit final report (some charts may still be failing)
            final_report = verifier.verify_dashboard(all_charts, loop=loop_idx - 1)
            await emit({
                "type": "verification.complete",
                "job_id": job_id,
                "loop": loop_idx - 1,
                "passed": final_report.passed,
                "overall_score": final_report.overall_score,
                "passed_charts": final_report.passed_charts,
                "failed_charts": final_report.failed_charts,
            })
            print(
                f"[pipeline:{job_id}] ⚠ Verification loops exhausted — "
                f"{final_report.passed_charts}/{final_report.total_charts} charts passed",
                flush=True,
            )

        # Step 7: Final dashboard assembly (with improved charts from verification)
        print(f"[pipeline:{job_id}] ── STEP 7: Final dashboard assembly  charts={len(all_charts)}", flush=True)
        dashboard = await self._assemble_screenshot_dashboard(
            project_id=project_id,
            user_id=user_id,
            confirmed_charts=all_charts,
            original_manifest=manifest,
            filter_configs=filter_configs,
            report_metadata=report_metadata,
            connection_id=connection_id,
            db=db,
        )

        dashboard_id = str(dashboard.id) if dashboard else None
        if dashboard_id:
            # Assembly succeeded — use the current session (already committed cleanly)
            await self._update_screenshot_job_status(
                screenshot_job_id, "completed", db, dashboard_id=dashboard_id
            )
        else:
            # Assembly failed — open a fresh session to avoid broken-transaction hang
            print(f"[pipeline:{job_id}] ⚠ Assembly returned None — updating status via fresh session", flush=True)
            from shared.database import AsyncSessionLocal
            async with AsyncSessionLocal() as fresh_db:
                await self._update_screenshot_job_status(
                    screenshot_job_id, "failed", fresh_db,
                    error="Dashboard assembly failed — check DB migration status (005_filter_config)"
                )
        # Step 7b: Cross-chart consistency check — lightweight post-processing.
        # Finds charts that share the same table/dimension but show different date ranges
        # or divergent totals, and logs them for debugging (non-blocking, non-fatal).
        try:
            _consistency_issues = _cross_chart_consistency_check(all_charts)
            if _consistency_issues:
                print(
                    f"[pipeline:{job_id}] ⚠ Cross-chart consistency: {len(_consistency_issues)} issue(s):",
                    flush=True,
                )
                for _issue in _consistency_issues[:5]:
                    print(f"  {_issue}", flush=True)
                await emit({
                    "type": "dashboard.consistency_issues",
                    "job_id": job_id,
                    "issues": _consistency_issues[:10],
                })
        except Exception as _cc_err:
            print(f"[pipeline:{job_id}] ⚠ cross-chart check failed (non-fatal): {_cc_err}", flush=True)

        token_summary = get_token_summary()
        await emit({
            "type": "dashboard.assembled",
            "job_id": job_id,
            "dashboard_id": dashboard_id,
            "widget_count": len(all_charts),
            "total_charts": len(charts),
            "token_usage": token_summary,
        })

        return {"dashboard_id": dashboard_id, "confirmed": len(confirmed), "total": len(charts)}

    async def _race_candidates(
        self,
        chart_spec: dict,
        enriched,
        connection_id: str,
        job_id: str,
        redis,
        candidates: list,
        sample_context: dict,
        parsed_context: Optional[dict] = None,
        user_context: str = "",
        spec_hint=None,  # Optional[ChartSpecHint] from spec_reader
        calc_col_map: Optional[dict] = None,  # {col_name: case_when_sql} from spec_reader global ctx
        business_rules: Optional[list] = None,  # Required WHERE conditions from PDF spec
        pbit_column_hint: Optional[dict] = None,  # PBIT visual field bindings (ground truth)
        resolved_spec=None,  # ResolvedChartSpec from context_synthesizer (highest priority)
    ) -> Optional[dict]:
        """
        Race top 2-3 close candidates simultaneously (confidence gap < 0.20).
        Each racer generates SQL + executes + validates in parallel.
        The first to pass validation wins; others are cancelled.
        Returns winner dict {candidate, query_plan, execute_result, validation} or None.
        Emits chart.racing / chart.race_winner events.
        """
        from utils.http_clients import call_query_executor
        from agent_service.agents.validator_agent import score_chart_screenshot_mode

        chart_id = chart_spec["id"]
        if len(candidates) < 2:
            return None

        gap = candidates[0].get("confidence", 0) - candidates[1].get("confidence", 0)
        if gap >= 0.30:
            return None  # clear winner (30+ pt gap) — skip racing to save latency

        race_candidates = candidates[: min(3, len(candidates))]
        await publish_pipeline_event(redis, job_id, {
            "type": "chart.racing",
            "job_id": job_id,
            "chart_id": chart_id,
            "candidate_count": len(race_candidates),
            "candidates": [
                {"tables": c.get("tables", []), "confidence": round(c.get("confidence", 0), 2)}
                for c in race_candidates
            ],
        })
        print(
            f"[chart:{chart_id}] 🏁 racing {len(race_candidates)} candidates (gap={gap:.2f})",
            flush=True,
        )

        async def _try_one(candidate: dict) -> Optional[dict]:
            try:
                query_plan = await self._query.generate_from_chart_spec(
                    chart_spec=chart_spec,
                    enriched=enriched,
                    attempt=1,
                    retry_feedback=None,
                    candidate=candidate,
                    sample_context=sample_context,
                    parsed_context=parsed_context,
                    user_context=user_context,
                    spec_hint=spec_hint,
                    calc_col_map=calc_col_map,
                    business_rules=business_rules,
                    pbit_column_hint=pbit_column_hint,
                    resolved_spec=resolved_spec,
                )
                result = await call_query_executor(connection_id, query_plan["sql"])
                if result.get("error"):
                    return None
                validation = score_chart_screenshot_mode(chart_spec, query_plan, result)
                if validation["passed"]:
                    return {
                        "candidate": candidate,
                        "query_plan": query_plan,
                        "execute_result": result,
                        "validation": validation,
                    }
            except Exception:
                pass
            return None

        tasks = [asyncio.ensure_future(_try_one(c)) for c in race_candidates]
        winner: Optional[dict] = None
        remaining = list(tasks)

        while remaining and winner is None:
            done, remaining_set = await asyncio.wait(remaining, return_when=asyncio.FIRST_COMPLETED)
            remaining = list(remaining_set)
            for task in done:
                try:
                    r = task.result()
                    if r is not None:
                        winner = r
                        break
                except Exception:
                    pass

        for task in remaining:
            task.cancel()

        if winner:
            winning_tables = winner["candidate"].get("tables", [])
            await publish_pipeline_event(redis, job_id, {
                "type": "chart.race_winner",
                "job_id": job_id,
                "chart_id": chart_id,
                "winning_tables": winning_tables,
                "score": winner["validation"]["score"],
            })
            print(
                f"[chart:{chart_id}] 🏆 race winner: {winning_tables}  "
                f"score={winner['validation']['score']:.2f}",
                flush=True,
            )

        return winner

    async def _run_chart_replication_loop(
        self,
        chart_spec: dict,
        enriched,  # EnrichedSchema
        connection_id: str,
        screenshot_job_id: str,
        job_id: str,
        redis,
        db: AsyncSession,
        candidates: list,
        cropped_image_bytes: Optional[bytes] = None,
        verify_feedback: Optional[str] = None,
        max_attempts: int = 5,
        dashboard_date_context: Optional[dict] = None,
        presample_contexts: Optional[dict] = None,
        user_context: str = "",
        parsed_context: Optional[dict] = None,
        spec_hint=None,  # Optional[ChartSpecHint] from spec_reader
        calc_col_map: Optional[dict] = None,  # {col_name: case_when_sql} from spec_reader global ctx
        business_rules: Optional[list] = None,  # Required WHERE conditions from PDF spec
        pbit_column_hint: Optional[dict] = None,  # PBIT visual field bindings (ground truth)
        resolved_spec=None,  # ResolvedChartSpec from context_synthesizer (highest priority)
    ) -> dict:
        """
        Per-chart autonomous retry loop.
        - Attempts 1-2: best candidate table, refine SQL on retry
        - Attempts 3-4: next candidate tables (different table/join combo)
        - Attempt 5: accept best achieved result
        No user input is ever requested.
        verify_feedback: optional feedback string from CanvasVerifier to seed retry_feedback.
        max_attempts: cap the loop (default 5; verification retries use 5).
        dashboard_date_context: global date window inferred from all chart specs (injected into SQL prompt).
        """
        from utils.http_clients import call_query_executor
        from agent_service.agents.validator_agent import score_chart_screenshot_mode, classify_failure
        from agent_service.agents.value_sampler import sample_candidate

        chart_id = chart_spec["id"]
        best_result = None
        best_score = 0.0
        # Seed retry_feedback with verification feedback if provided
        retry_feedback = verify_feedback or None
        # Tracks whether classify_failure requested an early candidate switch
        _force_switch = False
        # Failure type from the last classify_failure call — drives adaptive temperature.
        _last_failure_type: Optional[str] = None

        # ── Layer 1: Query signature tracking ─────────────────────────────────────
        # Normalised semantic key — captures tables + metric + date columns, which
        # are what distinguishes meaningful query variants, not cosmetic SQL text.
        # Two queries that use the same tables/metric/date → same signature → skip.
        def _query_signature(cand: Optional[dict]) -> str:
            if not cand:
                return ""
            tables = tuple(sorted(cand.get("tables", [])))
            key_cols = cand.get("key_columns") or {}
            return f"{tables}|{key_cols.get('metric', '')}|{key_cols.get('date', '')}"

        tried_signatures: set[str] = set()
        tried_tables_history: list[dict] = []  # [{tables, failure_reason}] — injected into LLM

        # Use pre-sampled context (from Step 3.7) when available — avoids an extra DB
        # round-trip on attempt 1.  Falls back to live sampling when presample_contexts
        # is empty (e.g. retry path that doesn't go through the presample step).
        _presample = presample_contexts or {}
        sample_context: dict = _presample.get(0, {})
        if not sample_context and candidates:
            try:
                sample_context = await sample_candidate(
                    connection_id=connection_id,
                    candidate=candidates[0],
                    chart_spec=chart_spec,
                )
            except Exception as _se:
                print(f"[chart:{chart_id}] ⚠ value sampling failed (non-fatal): {_se}", flush=True)
        if sample_context:
            print(
                f"[chart:{chart_id}] sample_context keys={list(sample_context.keys())} "
                f"(presample={'yes' if _presample.get(0) else 'no'})",
                flush=True,
            )
            # Row-count sanity check: flag wrong-grain candidates early
            from agent_service.agents.value_sampler import enrich_sample_context_with_row_count
            sample_context = enrich_sample_context_with_row_count(sample_context, chart_spec)
            # Grain mismatch: if the top candidate is at the wrong entity level, skip it
            # immediately on attempt 1 rather than wasting two tries on a bad candidate.
            if sample_context.get("row_count_warning"):
                _force_switch = True
                print(
                    f"[chart:{chart_id}] grain mismatch detected — forcing candidate switch on attempt 1",
                    flush=True,
                )

        # Track consecutive SQL errors to trigger recovery mode
        consecutive_error_counts: dict[str, int] = {}
        error_recovery_mode: Optional[str] = None

        # Build date range hint only for charts that have a genuine time dimension.
        # For KPI cards, bar-by-category, pie, and table charts this was injecting a
        # BETWEEN covering the entire DB range — useless as a filter and misleading to the LLM.
        date_filter_constraint: Optional[str] = None
        from agent_service.agents.query_agent import _chart_needs_date_filter as _cndf
        if _cndf(chart_spec) and sample_context and sample_context.get("actual_date_range"):
            _date_col = (candidates[0].get("key_columns") or {}).get("date") if candidates else None
            if _date_col:
                from agent_service.agents.value_sampler import build_date_constraint as _bdc
                date_filter_constraint = _bdc(sample_context["actual_date_range"], _date_col)
                if date_filter_constraint:
                    print(f"[chart:{chart_id}] date range hint: {date_filter_constraint}", flush=True)
        else:
            if not _cndf(chart_spec):
                print(f"[chart:{chart_id}] skipping date filter — chart type/content is not time-based", flush=True)

        # ── Layer 4: Load previously tried signatures from DB ──────────────────────
        # The outer verification loop can call _run_chart_replication_loop multiple
        # times for the same chart. Each call starts fresh — without this load, the
        # model would repeat all the same failed combinations from the prior pass.
        try:
            _prev_q = await db.execute(
                select(ChartReplicationState).where(
                    ChartReplicationState.job_id == uuid.UUID(screenshot_job_id),
                    ChartReplicationState.chart_id == chart_id,
                )
            )
            _prev_state = _prev_q.scalar_one_or_none()
            if _prev_state and _prev_state.validation_details:
                _pd = _prev_state.validation_details or {}
                tried_signatures.update(_pd.get("tried_signatures", []))
                tried_tables_history.extend(_pd.get("tried_tables_history", []))
                if tried_signatures:
                    print(
                        f"[chart:{chart_id}] ✓ loaded {len(tried_signatures)} prior tried "
                        f"signatures from DB for cross-retry continuity",
                        flush=True,
                    )
        except Exception as _pe:
            print(
                f"[chart:{chart_id}] ⚠ could not load prior exploration state (non-fatal): {_pe}",
                flush=True,
            )

        # ── Layer 3: Calendar / date dimension table detection ─────────────────────
        # Some BI dashboards use a separate date/calendar table as a join target for
        # time-filtering (the Power BI dim_date pattern). Detect these tables upfront
        # so they can be injected as last-resort candidates when all standard combos fail.
        from agent_service.agents.value_sampler import detect_date_tables as _detect_date_tables
        date_table_candidates: list[dict] = []
        try:
            if candidates and hasattr(enriched, "compact_tables"):
                _detected_dates = _detect_date_tables(enriched.compact_tables)
                if _detected_dates:
                    _existing = {t for c in candidates for t in c.get("tables", [])}
                    _new_date_tbls = [t for t in _detected_dates if t not in _existing]
                    _fact_table = candidates[0].get("tables", [""])[0] if candidates else ""

                    # Build a table→columns map for real FK join key detection.
                    _tbl_col_map: dict[str, list[str]] = {
                        t.get("name", ""): [c.get("name", "") for c in t.get("columns", [])]
                        for t in (enriched.compact_tables or [])
                    }

                    def _find_join_key(fact: str, dim: str) -> str:
                        """Try to find a real shared column or FK column for the join."""
                        fact_cols = set(_tbl_col_map.get(fact, []))
                        dim_cols  = set(_tbl_col_map.get(dim, []))
                        # Shared columns — most reliable join
                        shared = fact_cols & dim_cols
                        if shared:
                            return f"{fact} JOIN {dim} USING ({next(iter(shared))})"
                        # Common FK patterns: date_id, date_key, date_sk, dim_date_id
                        _date_key_patterns = ("date_id", "date_key", "date_sk", "dim_date_id",
                                              "datekey", "dateid", "date_dim_id")
                        for pat in _date_key_patterns:
                            if pat in fact_cols and pat in dim_cols:
                                return f"{fact} JOIN {dim} ON {fact}.{pat} = {dim}.{pat}"
                            if pat in fact_cols:
                                return f"{fact} JOIN {dim} ON {fact}.{pat} = {dim}.date_key"
                        # Fallback: let the LLM figure it out
                        return f"{fact} JOIN {dim} ON <infer_join_key_from_schema>"

                    for _dt in _new_date_tbls[:2]:
                        _dtc_cols = dict(candidates[0].get("key_columns") or {}) if candidates else {}
                        _join_str = _find_join_key(_fact_table, _dt) if _fact_table else _dt
                        date_table_candidates.append({
                            "tables": [_fact_table, _dt] if _fact_table else [_dt],
                            "key_columns": _dtc_cols,
                            "join": _join_str,
                            "reasoning": (
                                f"Calendar/date dimension table '{_dt}' paired with "
                                f"'{_fact_table}' for Power BI-style date filtering."
                            ),
                            "is_date_candidate": True,
                        })
                    if date_table_candidates:
                        print(
                            f"[chart:{chart_id}] detected date tables: {_detected_dates[:5]} "
                            f"→ {len(date_table_candidates)} extra candidate(s)",
                            flush=True,
                        )
        except Exception as _de:
            print(
                f"[chart:{chart_id}] ⚠ date table detection failed (non-fatal): {_de}",
                flush=True,
            )

        # ── Per-chart FK dimension CASE WHEN override ─────────────────────────
        # When spec hint SQL uses COALESCE(col, ...) on a column that is actually
        # an FK integer (listed in the table's relationships), the LLM groups by
        # raw integer IDs instead of meaningful category labels.
        # Detect such columns and add a CASE WHEN derived from the chart's
        # x_tick_labels (the category names the original screenshot shows).
        _chart_calc_col_map = dict(calc_col_map) if calc_col_map else {}
        try:
            if spec_hint and getattr(spec_hint, "sql_template", None):
                import re as _re_fk
                _coalesce_cols = {
                    m.lower()
                    for m in _re_fk.findall(r'COALESCE\s*\(\s*(\w+)', spec_hint.sql_template, _re_fk.IGNORECASE)
                }
                if _coalesce_cols:
                    _fk_col_set: set = set()
                    for _tbl in (getattr(enriched, "compact_tables", None) or []):
                        for _rel in (_tbl.get("relationships") or []):
                            if _rel.get("column"):
                                _fk_col_set.add(_rel["column"].lower())
                    _x_ticks = [
                        str(lbl).strip()
                        for lbl in (chart_spec.get("x_tick_labels") or [])
                        if str(lbl).strip()
                    ]
                    for _col in _coalesce_cols & _fk_col_set:
                        if _col in _chart_calc_col_map:
                            continue
                        if len(_x_ticks) < 2:
                            continue
                        _null_labels = [
                            l for l in _x_ticks
                            if any(n in l.lower() for n in ("not ", "no ", "blank", "none", "n/a"))
                        ]
                        _nonnull_labels = [l for l in _x_ticks if l not in _null_labels and l.lower() != "(blank)"]
                        if _null_labels and _nonnull_labels:
                            _case_expr = (
                                f"CASE WHEN {_col} IS NOT NULL THEN '{_nonnull_labels[0]}'"
                                f" ELSE '{_null_labels[0]}' END"
                            )
                        elif len(_x_ticks) >= 2:
                            _case_expr = (
                                f"CASE WHEN {_col} IS NOT NULL THEN '{_x_ticks[0]}'"
                                f" ELSE '{_x_ticks[1]}' END"
                            )
                        else:
                            continue
                        _chart_calc_col_map[_col] = _case_expr
                        print(
                            f"[pipeline:{chart_id}] FK dim override: '{_col}' → {_case_expr[:100]}",
                            flush=True,
                        )
        except Exception as _fk_e:
            print(f"[pipeline:{chart_id}] ⚠ FK dim override failed (non-fatal): {_fk_e}", flush=True)
        # Rebind so all downstream calls (race, attempt loop) pick up the extended map
        calc_col_map = _chart_calc_col_map

        # Phase 3A: Parallel Candidate Racing ────────────────────────────────
        # When the top two candidates are within 0.20 confidence of each other,
        # race them simultaneously.  The first to pass validation short-circuits
        # the normal retry loop entirely.
        if candidates and len(candidates) >= 2:
            race_winner = await self._race_candidates(
                chart_spec=chart_spec,
                enriched=enriched,
                connection_id=connection_id,
                job_id=job_id,
                redis=redis,
                candidates=candidates,
                sample_context=sample_context,
                parsed_context=parsed_context,
                user_context=user_context,
                spec_hint=spec_hint,
                calc_col_map=calc_col_map,
                business_rules=business_rules,
                pbit_column_hint=pbit_column_hint,
                resolved_spec=resolved_spec,
            )
            if race_winner is not None:
                rw_qp = race_winner["query_plan"]
                rw_exec = race_winner["execute_result"]
                rw_score = race_winner["validation"]["score"]
                # Phase 3B visual check on the race winner too
                if cropped_image_bytes is not None:
                    try:
                        from agent_service.agents.vision_agent import VisionAgent as _VAgentCls
                        _va_inner = _VAgentCls()
                        vis = await _va_inner.compare_charts(cropped_image_bytes, rw_qp, rw_exec)
                        await publish_pipeline_event(redis, job_id, {
                            "type": "chart.visual_comparison",
                            "job_id": job_id,
                            "chart_id": chart_id,
                            "match": vis["match"],
                            "score": vis["score"],
                            "mismatches": vis.get("mismatches", []),
                        })
                        print(
                            f"[chart:{chart_id}] race winner visual: match={vis['match']}  "
                            f"score={vis['score']:.2f}",
                            flush=True,
                        )
                        if not vis["match"] and vis.get("mismatches"):
                            # Still accept — racing already validated SQL passes threshold.
                            # Store the visual note for future improvement.
                            print(
                                f"[chart:{chart_id}] visual notes: {vis['mismatches']}",
                                flush=True,
                            )
                    except Exception as _ve:
                        print(f"[chart:{chart_id}] ⚠ visual comparison error (non-fatal): {_ve}", flush=True)

                await self._finalize_chart(
                    chart_id=chart_id,
                    screenshot_job_id=screenshot_job_id,
                    chart_spec=chart_spec,
                    query_plan=rw_qp,
                    score=rw_score,
                    status="confirmed",
                    db=db,
                )
                await publish_pipeline_event(redis, job_id, {
                    "type": "chart.confirmed",
                    "job_id": job_id,
                    "chart_id": chart_id,
                    "score": rw_score,
                    "via": "racing",
                    "chart_data": {
                        "chart_type": rw_qp["chart_type"],
                        "title": rw_qp["title"],
                        "sql": rw_qp["sql"],
                        "x_axis_label": rw_qp["x_axis_label"],
                        "y_axis_label": rw_qp["y_axis_label"],
                        "rows": rw_exec.get("rows", [])[:100],
                        "columns": rw_exec.get("columns", []),
                    },
                })
                return {
                    "chart_spec": chart_spec,
                    "query_plan": rw_qp,
                    "execute_result": rw_exec,
                    "validation": race_winner["validation"],
                    "status": "confirmed",
                    "score": rw_score,
                    "via": "racing",
                }

        # Confidence gap between top-2 candidates — used to decide early switching.
        _conf_gap = (
            candidates[0].get("confidence", 0) - candidates[1].get("confidence", 0)
            if len(candidates) >= 2 else 1.0
        )

        # Map attempt number → which candidate to use.
        # attempts 1,2 → candidates[0]; attempts 3,4 → candidates[1]; attempt 5 → accept best.
        # classify_failure can set _force_switch=True to advance the index early.
        # When top-2 candidates are nearly tied (gap < 0.10) try alternative at attempt 2.
        def _pick_candidate(attempt: int) -> Optional[dict]:
            nonlocal _force_switch
            if _force_switch:
                # Move to next candidate; reset flag so we don't keep switching
                _force_switch = False
                idx = min(attempt, len(candidates) - 1)
                return candidates[idx] if candidates else None
            if attempt == 1:
                return candidates[0] if candidates else None
            elif attempt == 2:
                # When candidates are nearly tied, try cand[1] earlier to save 1 wasted attempt.
                if _conf_gap < 0.10 and len(candidates) >= 2:
                    return candidates[1]
                return candidates[0] if candidates else None
            elif attempt <= 4:
                return candidates[1] if len(candidates) > 1 else (candidates[0] if candidates else None)
            return None

        for attempt in range(1, max_attempts + 1):
            candidate = _pick_candidate(attempt)

            # ── Signature-aware candidate override (Layers 1 + 3 escalation) ───────
            # If the picked candidate was already tried this run, advance to the next
            # untried candidate. When all standard candidates are exhausted at attempt
            # ≥ 3, escalate to Power BI-style date dimension table candidates (Layer 3).
            cand_sig = _query_signature(candidate)
            if cand_sig and cand_sig in tried_signatures:
                _all_pool = (candidates or []) + date_table_candidates
                _untried = [c for c in _all_pool if _query_signature(c) not in tried_signatures]
                if _untried:
                    candidate = _untried[0]
                    cand_sig = _query_signature(candidate)
                    print(
                        f"[chart:{chart_id}] attempt {attempt} — prior signature hit, "
                        f"switching to untried candidate: tables={candidate.get('tables', [])}",
                        flush=True,
                    )
                elif attempt >= 3 and date_table_candidates:
                    candidate = date_table_candidates[0]
                    cand_sig = _query_signature(candidate)
                    print(
                        f"[chart:{chart_id}] all standard candidates exhausted → escalating to "
                        f"date table candidate: {candidate.get('tables', [])}",
                        flush=True,
                    )

            strategy = (
                "initial" if attempt == 1
                else "refine_sql" if attempt == 2
                else "alternate_table" if attempt == 3
                else "alternate_approach" if attempt == 4
                else "accept_best"
            )
            cand_tables = candidate.get("tables", []) if candidate else []
            print(f"[chart:{chart_id}] attempt {attempt}/{max_attempts}  strategy={strategy}  tables={cand_tables}", flush=True)

            # On candidate switch (attempt >= 3), swap sample_context from presample cache
            # so the SQL generator immediately gets the right value context for the new table.
            if attempt >= 3 and _presample:
                # Find the presample index that corresponds to the current candidate
                _new_idx = next(
                    (i for i, c in enumerate(candidates[:3]) if c.get("tables") == cand_tables),
                    None,
                )
                if _new_idx is not None and _new_idx in _presample and _presample[_new_idx]:
                    sample_context = _presample[_new_idx]
                    print(
                        f"[chart:{chart_id}] updated sample_context from presample[{_new_idx}] "
                        f"for candidate tables={cand_tables}",
                        flush=True,
                    )

            if attempt > 1:
                await publish_pipeline_event(redis, job_id, {
                    "type": "validation.retry",
                    "job_id": job_id,
                    "chart_id": chart_id,
                    "attempt": attempt,
                    "strategy": strategy,
                    "tables": cand_tables,
                })

            # Last attempt: accept best so far
            if attempt == max_attempts:
                if best_result:
                    await self._finalize_chart(
                        chart_id=chart_id,
                        screenshot_job_id=screenshot_job_id,
                        chart_spec=chart_spec,
                        query_plan=best_result["query_plan"],
                        score=best_score,
                        status="low_confidence",
                        db=db,
                    )
                    await publish_pipeline_event(redis, job_id, {
                        "type": "chart.low_confidence",
                        "job_id": job_id,
                        "chart_id": chart_id,
                        "score": best_score,
                    })
                    return {**best_result, "status": "low_confidence", "score": best_score}
                break

            # Mark this candidate's signature as in-progress (Layer 1)
            tried_signatures.add(cand_sig)

            # Generate SQL using current candidate tables
            try:
                await self._update_replication_state(
                    screenshot_job_id, chart_id, "querying", attempt, None, None, None, db
                )
                query_plan = await self._query.generate_from_chart_spec(
                    chart_spec=chart_spec,
                    enriched=enriched,
                    attempt=attempt,
                    retry_feedback=retry_feedback,
                    candidate=candidate,
                    sample_context=sample_context,
                    date_filter_constraint=date_filter_constraint,
                    error_recovery_mode=error_recovery_mode,
                    dashboard_date_context=dashboard_date_context,
                    previously_tried=tried_tables_history[-8:] if tried_tables_history else None,
                    failure_type=_last_failure_type,
                    parsed_context=parsed_context,
                    user_context=user_context,
                    spec_hint=spec_hint,
                    calc_col_map=calc_col_map,
                    business_rules=business_rules,
                    pbit_column_hint=pbit_column_hint,
                    resolved_spec=resolved_spec,
                )
            except Exception as e:
                retry_feedback = f"SQL generation failed: {str(e)}. Try a simpler query."
                continue

            # If SQL was likely truncated (unbalanced parens), inject that as retry feedback.
            _trunc_warn = query_plan.get("_truncation_warning")
            if _trunc_warn:
                retry_feedback = _trunc_warn
                print(f"[chart:{chart_id}] ⚠ SQL truncation detected — injecting simplify feedback", flush=True)

            # If column pre-check found an issue, inject it into retry_feedback for next attempt
            # but still execute — the DB error will confirm and provide exact feedback.
            _col_err = query_plan.get("_column_error")
            if _col_err and not retry_feedback:
                retry_feedback = _col_err

            # Execute SQL
            print(f"[chart:{chart_id}] executing SQL: {query_plan['sql'][:120].replace(chr(10),' ')}", flush=True)
            execute_result = await call_query_executor(connection_id, query_plan["sql"])
            if execute_result.get("error"):
                failure = classify_failure(
                    sql_result=execute_result,
                    chart_spec=chart_spec,
                    query_plan=query_plan,
                )
                retry_feedback = failure["retry_instruction"]
                if failure["switch_candidate"]:
                    _force_switch = True
                _ftype = failure["failure_type"]
                _last_failure_type = _ftype
                print(
                    f"[chart:{chart_id}] SQL error  failure_type={_ftype} "
                    f"switch={failure['switch_candidate']}  issue={failure['specific_issue'][:80]}",
                    flush=True,
                )
                # Track consecutive identical errors → switch to targeted recovery mode
                consecutive_error_counts[_ftype] = consecutive_error_counts.get(_ftype, 0) + 1
                # Trigger no-alias mode after just 1 column_not_found — alias errors repeat endlessly
                if consecutive_error_counts.get("column_not_found", 0) >= 1:
                    error_recovery_mode = "no_alias"
                    print(f"[chart:{chart_id}] ⚠ column_not_found → no-alias recovery mode", flush=True)
                elif consecutive_error_counts.get("db_error", 0) >= 2:
                    error_recovery_mode = "no_union_order"
                    print(f"[chart:{chart_id}] ⚠ repeated db_error → no-union-order recovery mode", flush=True)
                tried_tables_history.append({
                    "tables": candidate.get("tables", []) if candidate else [],
                    "failure_reason": f"sql_error:{failure['failure_type']}",
                })
                await self._update_replication_state(
                    screenshot_job_id, chart_id, "retrying", attempt,
                    query_plan["sql"], None,
                    {
                        "tried_signatures": list(tried_signatures),
                        "tried_tables_history": tried_tables_history[-8:],
                    },
                    db,
                )
                continue

            # Validate result against chart spec
            await self._update_replication_state(
                screenshot_job_id, chart_id, "validating", attempt,
                query_plan["sql"], None, None, db
            )
            validation = score_chart_screenshot_mode(
                chart_spec=chart_spec,
                query_plan=query_plan,
                execute_result=execute_result,
            )
            score = validation["score"]

            if score > best_score:
                best_score = score
                best_result = {
                    "chart_spec": chart_spec,
                    "query_plan": query_plan,
                    "execute_result": execute_result,
                    "validation": validation,
                }
                consecutive_error_counts.clear()
                error_recovery_mode = None

            await publish_pipeline_event(redis, job_id, {
                "type": "validation.scored",
                "job_id": job_id,
                "chart_id": chart_id,
                "score": score,
                "attempt": attempt,
                "dimension_scores": validation["dimension_scores"],
                "passed": validation["passed"],
            })

            await self._update_replication_state(
                screenshot_job_id, chart_id,
                "confirmed" if validation["passed"] else "retrying",
                attempt, query_plan["sql"], score,
                {
                    **validation,
                    "tried_signatures": list(tried_signatures),
                    "tried_tables_history": tried_tables_history[-8:],
                },
                db,
            )

            print(f"[chart:{chart_id}] validation score={score:.2f}  passed={validation['passed']}  dims={validation['dimension_scores']}", flush=True)

            if validation["passed"]:
                # Phase 3B: Visual Comparison Feedback Loop ───────────────────
                # Compare the original cropped chart against the SQL result data.
                # If the vision model spots mismatches and we still have retries,
                # use the suggestion as targeted retry feedback (non-fatal).
                if cropped_image_bytes is not None and attempt <= 3:
                    try:
                        from agent_service.agents.vision_agent import VisionAgent as _VAgentCls2
                        _va2 = _VAgentCls2()
                        vis2 = await _va2.compare_charts(
                            original_bytes=cropped_image_bytes,
                            query_plan=query_plan,
                            execute_result=execute_result,
                        )
                        await publish_pipeline_event(redis, job_id, {
                            "type": "chart.visual_comparison",
                            "job_id": job_id,
                            "chart_id": chart_id,
                            "match": vis2["match"],
                            "score": vis2["score"],
                            "mismatches": vis2.get("mismatches", []),
                        })
                        print(
                            f"[chart:{chart_id}] visual comparison: match={vis2['match']}  "
                            f"score={vis2['score']:.2f}  mismatches={vis2.get('mismatches', [])}",
                            flush=True,
                        )
                        if not vis2["match"] and vis2.get("suggestion") and attempt < 4:
                            # One extra retry driven by visual feedback
                            mismatches_txt = "; ".join(vis2["mismatches"][:2])
                            retry_feedback = (
                                f"Visual comparison found chart mismatches: {mismatches_txt}. "
                                f"Fix: {vis2['suggestion']}"
                            )
                            continue  # Next attempt with this targeted feedback
                    except Exception as _ve2:
                        print(
                            f"[chart:{chart_id}] ⚠ visual comparison error (non-fatal): {_ve2}",
                            flush=True,
                        )

                # Validation + visual check passed — confirm chart
                await self._finalize_chart(
                    chart_id=chart_id,
                    screenshot_job_id=screenshot_job_id,
                    chart_spec=chart_spec,
                    query_plan=query_plan,
                    score=score,
                    status="confirmed",
                    db=db,
                )
                await publish_pipeline_event(redis, job_id, {
                    "type": "chart.confirmed",
                    "job_id": job_id,
                    "chart_id": chart_id,
                    "score": score,
                    "chart_data": {
                        "chart_type": query_plan["chart_type"],
                        "title": query_plan["title"],
                        "sql": query_plan["sql"],
                        "x_axis_label": query_plan["x_axis_label"],
                        "y_axis_label": query_plan["y_axis_label"],
                        "rows": execute_result.get("rows", [])[:100],
                        "columns": execute_result.get("columns", []),
                    },
                })
                return {**best_result, "status": "confirmed", "score": score}

            # Near-threshold visual comparison — run comparison even when score is 0.60-0.72
            # so we get targeted visual feedback before the final retry attempt.
            if 0.58 <= score < 0.72 and cropped_image_bytes is not None and attempt <= 3:
                try:
                    from agent_service.agents.vision_agent import VisionAgent as _VAgentNT
                    _va_nt = _VAgentNT()
                    vis_nt = await _va_nt.compare_charts(
                        original_bytes=cropped_image_bytes,
                        query_plan=query_plan,
                        execute_result=execute_result,
                    )
                    if vis_nt.get("suggestion"):
                        print(
                            f"[chart:{chart_id}] near-threshold visual hint: {vis_nt['suggestion'][:120]}",
                            flush=True,
                        )
                        # Prepend visual hint to the retry feedback that follows
                        retry_feedback = (
                            f"Visual analysis found: {'; '.join(vis_nt.get('mismatches', [])[:2])}. "
                            f"Fix: {vis_nt['suggestion']} "
                        )
                except Exception as _nte:
                    print(f"[chart:{chart_id}] ⚠ near-threshold visual comparison (non-fatal): {_nte}", flush=True)

            # Classify why it failed and build targeted retry feedback
            failure = classify_failure(
                sql_result=execute_result,
                chart_spec=chart_spec,
                query_plan=query_plan,
            )
            if failure["switch_candidate"]:
                _force_switch = True
            _last_failure_type = failure["failure_type"]
            print(
                f"[chart:{chart_id}] low score={score:.2f}  failure_type={failure['failure_type']} "
                f"switch={failure['switch_candidate']}",
                flush=True,
            )
            # Append weakest dimension and numeric mismatch hint to the classified instruction
            weakest = min(validation["dimension_scores"], key=validation["dimension_scores"].get)
            extra = (
                f"Score {score:.2f}; weakest dimension: {weakest} "
                f"= {validation['dimension_scores'][weakest]:.2f}."
            )
            if validation.get("value_mismatch"):
                extra += f" Value mismatch: {validation['value_mismatch']}"
            retry_feedback = failure["retry_instruction"] + f" ({extra})"

            tried_tables_history.append({
                "tables": candidate.get("tables", []) if candidate else [],
                "failure_reason": f"low_score:{score:.2f} weakest:{weakest}",
            })

            # Option 1: Multi-aggregation probe on attempts 1-3 when value_match is low.
            # Run COUNT/SUM/AVG/COUNT-DISTINCT in parallel and prepend the winning
            # aggregation as a concrete hint to the next SQL generation call.
            if (
                attempt <= 3
                and candidates
                and validation["dimension_scores"].get("value_match", 1.0) < 0.3
                and chart_spec.get("estimated_values")
            ):
                try:
                    from agent_service.agents.value_sampler import probe_aggregations as _probe_agg
                    agg_hint = await _probe_agg(
                        connection_id=connection_id,
                        candidate=candidates[0],
                        chart_spec=chart_spec,
                        estimated_values=chart_spec.get("estimated_values", {}),
                    )
                    if agg_hint:
                        retry_feedback = agg_hint + " " + retry_feedback
                        print(f"[chart:{chart_id}] aggregation probe: {agg_hint[:120]}", flush=True)
                except Exception as _pe:
                    print(f"[chart:{chart_id}] ⚠ aggregation probe failed (non-fatal): {_pe}", flush=True)

        return {"error": "Max retries reached", "chart_id": chart_id}

    async def _assemble_screenshot_dashboard(
        self,
        project_id: str,
        user_id: str,
        confirmed_charts: list[dict],
        original_manifest: dict,
        db: AsyncSession,
        filter_configs: Optional[list[dict]] = None,
        report_metadata: Optional[dict] = None,
        connection_id: Optional[str] = None,
    ) -> Optional[Dashboard]:
        """Create a Dashboard + Widgets from confirmed chart replication results."""
        from agent_service.utils.date_extractor import extract_date_filter

        try:
            filter_col_names = [f["column"] for f in (filter_configs or [])]
            meta = report_metadata or {}
            dashboard_name = meta.get("report_title") or f"Screenshot Dashboard — {len(confirmed_charts)} charts"
            layout_config = {
                "source": "screenshot_replication",
                "report_title": meta.get("report_title"),
                "page_tabs": meta.get("page_tabs", []),
                "logo_text": meta.get("logo_text"),
                "colour_theme": meta.get("colour_theme"),
            }

            # Collect date filter columns discovered across all charts so the frontend
            # can show a global date picker.  One filter_config entry per unique column.
            date_filter_map: dict[str, dict] = {}

            # First pass: scan each chart's SQL for date conditions
            widget_date_filters: list[Optional[dict]] = []
            for result in confirmed_charts:
                sql = result.get("query_plan", {}).get("sql")
                df = extract_date_filter(sql) if sql else None
                widget_date_filters.append(df)
                if df:
                    col = df["column"]
                    if col not in date_filter_map:
                        date_filter_map[col] = {
                            "id":               str(uuid.uuid4()),
                            "column":           col,
                            "display_name":     col.replace("_", " ").title(),
                            "filter_type":      "date_range",
                            "available_values": [],
                            "table":            df.get("table_qualified") or "",
                        }

            # Merge date filter entries with the existing filter_configs (from vision-detected
            # categorical filters) — de-duplicate by column name
            combined_filter_configs = list(filter_configs or [])
            for df_entry in date_filter_map.values():
                if not any(fc.get("column") == df_entry["column"] for fc in combined_filter_configs):
                    combined_filter_configs.append(df_entry)

            if date_filter_map:
                print(
                    f"[assemble] date filter column(s) detected: {list(date_filter_map.keys())}",
                    flush=True,
                )

            dashboard = Dashboard(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project_id),
                name=dashboard_name,
                layout_config=layout_config,
                filter_config=combined_filter_configs,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(dashboard)
            await db.flush()

            for i, result in enumerate(confirmed_charts):
                chart_spec = result.get("chart_spec", {})
                query_plan = result.get("query_plan", {})
                exec_result = result.get("execute_result", {})
                grid = chart_spec.get("grid_layout", {"x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4})

                rows = exec_result.get("rows", [])
                columns = exec_result.get("columns", [])
                chart_type = query_plan.get("chart_type", "bar_vertical")
                base_sql = query_plan.get("sql")

                chart_data = self._build_chart_data_for_type(chart_type, rows, columns)

                # Store the detected date filter in the widget's config so the frontend
                # knows the initial date range for this chart's data.
                widget_config: Optional[dict] = None
                if widget_date_filters[i]:
                    widget_config = {"date_filter": widget_date_filters[i]}

                widget = Widget(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard.id,
                    title=query_plan.get("title") or chart_spec.get("title") or f"Chart {i+1}",
                    widget_type="chart",
                    chart_type=chart_type,
                    sql_query=base_sql,
                    base_sql=base_sql,
                    filterable_columns=filter_col_names,
                    connection_id=uuid.UUID(connection_id) if connection_id else None,
                    position_x=grid.get("x", 0),
                    position_y=grid.get("y", i * 4),
                    width=grid.get("w", 6),
                    height=grid.get("h", 4),
                    validation_score=result.get("score"),
                    validation_status=result.get("status", "confirmed"),
                    chart_data=chart_data,
                    config=widget_config,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(widget)

            await db.commit()
            await db.refresh(dashboard)
            return dashboard
        except Exception as _asm_err:
            print(f"[assemble] ✗ Dashboard assembly failed: {type(_asm_err).__name__}: {_asm_err}", flush=True)
            try:
                await db.rollback()
            except Exception:
                pass
            return None

    async def _update_screenshot_job_status(
        self,
        screenshot_job_id: str,
        status: str,
        db: AsyncSession,
        error: Optional[str] = None,
        dashboard_id: Optional[str] = None,
    ) -> None:
        try:
            result = await db.execute(
                select(ScreenshotJob).where(ScreenshotJob.id == uuid.UUID(screenshot_job_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.status = status
                job.updated_at = datetime.utcnow()
                if error:
                    job.error_message = error
                if dashboard_id:
                    job.result_dashboard_id = uuid.UUID(dashboard_id)
                await db.commit()
        except Exception:
            pass

    async def _save_manifest(self, screenshot_job_id: str, manifest: dict, db: AsyncSession) -> None:
        try:
            result = await db.execute(
                select(ScreenshotJob).where(ScreenshotJob.id == uuid.UUID(screenshot_job_id))
            )
            job = result.scalar_one_or_none()
            if job:
                job.chart_manifest = manifest
                job.total_charts = manifest.get("total", 0)
                job.updated_at = datetime.utcnow()
                await db.commit()
        except Exception:
            pass

    async def _create_replication_states(
        self, screenshot_job_id: str, charts: list[dict], db: AsyncSession
    ) -> None:
        try:
            seen_ids: set[str] = set()
            for chart in charts:
                cid = chart["id"]
                if cid in seen_ids:
                    # Safety net: IDs should be unique after vision_agent re-numbers them,
                    # but skip silently rather than crashing on any edge case.
                    continue
                seen_ids.add(cid)
                state = ChartReplicationState(
                    id=uuid.uuid4(),
                    job_id=uuid.UUID(screenshot_job_id),
                    chart_id=cid,
                    chart_spec=chart,
                    status="pending",
                    attempt_count=0,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                db.add(state)
            await db.commit()
        except Exception:
            await db.rollback()  # Clear the poisoned transaction so later DB ops still work

    async def _finalize_chart(
        self,
        chart_id: str,
        screenshot_job_id: str,
        chart_spec: dict,
        query_plan: dict,
        score: float,
        status: str,
        db: AsyncSession,
        widget_id: Optional[str] = None,
    ) -> None:
        try:
            result = await db.execute(
                select(ChartReplicationState).where(
                    ChartReplicationState.job_id == uuid.UUID(screenshot_job_id),
                    ChartReplicationState.chart_id == chart_id,
                )
            )
            state = result.scalar_one_or_none()
            if state:
                state.status = status
                state.validation_score = score
                state.current_sql = query_plan.get("sql")
                if widget_id:
                    state.widget_id = uuid.UUID(widget_id)
                state.updated_at = datetime.utcnow()

            # Update confirmed_charts count on job
            job_result = await db.execute(
                select(ScreenshotJob).where(ScreenshotJob.id == uuid.UUID(screenshot_job_id))
            )
            job = job_result.scalar_one_or_none()
            if job and status in ("confirmed", "low_confidence"):
                job.confirmed_charts = (job.confirmed_charts or 0) + 1
                job.updated_at = datetime.utcnow()

            await db.commit()
        except Exception:
            pass

    async def _update_replication_state(
        self,
        screenshot_job_id: str,
        chart_id: str,
        status: str,
        attempt: int,
        sql: Optional[str],
        score: Optional[float],
        validation_details: Optional[dict],
        db: AsyncSession,
    ) -> None:
        try:
            result = await db.execute(
                select(ChartReplicationState).where(
                    ChartReplicationState.job_id == uuid.UUID(screenshot_job_id),
                    ChartReplicationState.chart_id == chart_id,
                )
            )
            state = result.scalar_one_or_none()
            if state:
                state.status = status
                state.attempt_count = attempt
                if sql:
                    state.current_sql = sql
                if score is not None:
                    state.validation_score = score
                if validation_details:
                    state.validation_details = validation_details
                state.updated_at = datetime.utcnow()
                await db.commit()
        except Exception:
            pass

    async def _get_schema(self, project_id: str, db: AsyncSession) -> dict:
        conn_result = await db.execute(
            select(DatabaseConnection)
            .where(DatabaseConnection.project_id == uuid.UUID(project_id))
            .where(DatabaseConnection.is_active == True)
            .limit(1)
        )
        conn = conn_result.scalar_one_or_none()
        if not conn:
            return {}
        snap_result = await db.execute(
            select(SchemaSnapshot)
            .where(SchemaSnapshot.connection_id == conn.id)
            .order_by(SchemaSnapshot.version.desc())
            .limit(1)
        )
        snapshot = snap_result.scalar_one_or_none()
        return snapshot.schema_document if snapshot else {}

    async def _get_connection_db_type(self, connection_id: str, db: AsyncSession) -> str:
        result = await db.execute(
            select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id))
        )
        conn = result.scalar_one_or_none()
        if conn:
            return conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)
        return "postgresql"

    # ─── EXPORT PIPELINE ──────────────────────────────────────────────────────

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
