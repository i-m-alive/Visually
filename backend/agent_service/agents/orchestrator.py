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

            # STEP 3+4+5+6: Query → Execute → Render → Validate (with one retry)
            final_result = None
            retry_feedback: Optional[str] = None

            for attempt in range(1, 3):
                # Step 3: Generate query
                await set_pipeline_state(redis, job_id, "step", f"generating_query_attempt_{attempt}")
                query_plan = await self._query.generate(intent, schema, db_type, retry_feedback)
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
                    if attempt == 1:
                        retry_feedback = f"Query execution failed: {execute_result['error']}. Fix the SQL syntax or table/column names."
                        await emit({
                            "type": "validation.retry",
                            "job_id": job_id,
                            "attempt": 2,
                            "strategy": "fix_sql_error",
                        })
                        continue
                    else:
                        await emit({
                            "type": "pipeline.error",
                            "job_id": job_id,
                            "message": f"Query failed after retry: {execute_result['error']}",
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

                # Step 6: Validate
                validation = await self._validator.validate(query_plan, execute_result, attempt)
                await emit({
                    "type": "validation.scored",
                    "job_id": job_id,
                    "score": validation.score,
                    "passed": validation.passed,
                    "dimension_scores": validation.dimension_scores.model_dump(),
                })

                if not validation.passed and attempt == 1 and validation.retry_feedback:
                    retry_feedback = validation.retry_feedback.feedback
                    await emit({
                        "type": "validation.retry",
                        "job_id": job_id,
                        "attempt": 2,
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
                # Both attempts failed — use last results with low confidence
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
    ) -> dict:
        """Full screenshot replication pipeline: vision → schema match → per-chart SQL loop → assemble."""
        from agent_service.agents.vision_agent import VisionAgent
        from shared.bedrock_client import start_token_tracking, get_token_summary
        vision_agent = VisionAgent()
        start_token_tracking()

        async def emit(event: dict):
            await publish_pipeline_event(redis, job_id, event)

        # Step 1: Vision parsing
        print(f"\n[pipeline:{job_id}] ── STEP 1: Vision parsing  images={len(uploaded_images)}", flush=True)
        await self._update_screenshot_job_status(screenshot_job_id, "vision_parsing", db)
        await emit({"type": "vision.started", "job_id": job_id, "image_count": len(uploaded_images)})

        print(f"[pipeline:{job_id}] calling VisionAgent.process_images ...", flush=True)
        manifest = await vision_agent.process_images(uploaded_images, job_id, redis)
        charts = manifest["charts"]
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
            schema_doc = await self._get_schema(project_id, db)
            db_type = await self._get_connection_db_type(connection_id, db)
            print(
                f"[pipeline:{job_id}] schema fetched → tables={len(schema_doc.get('tables', []))}  db_type={db_type}",
                flush=True,
            )
            enriched = await _schema_cache.get_or_build(connection_id, schema_doc, db_type)
            return schema_doc, db_type, enriched

        (detected_filters, (schema_doc, db_type, enriched)) = await asyncio.gather(
            _detect_filters_task(),
            _schema_fetch_task(),
        )

        await emit({"type": "schema.fetched", "job_id": job_id,
                    "table_count": len(schema_doc.get("tables", [])),
                    "ambiguous_columns": len(enriched.ambiguous_columns)})

        # Step 3: Schema matching — rank candidate tables for every chart IN PARALLEL (one task per chart)
        print(f"[pipeline:{job_id}] ── STEP 3: Schema matching  charts={len(charts)}", flush=True)
        await emit({"type": "schema.matching", "job_id": job_id, "chart_count": len(charts)})

        async def match_one(chart_spec: dict) -> list:
            candidates = await self._schema_matcher.rank_candidates(chart_spec, enriched)
            print(f"[schema_match:{chart_spec['id']}] → {[(c['tables'], round(c.get('confidence',0),2)) for c in candidates[:3]]}", flush=True)
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

        # Step 3b: Sample available values for each detected filter column
        # Uses candidate tables from schema matching to discover the full value set
        filter_configs: list[dict] = []
        if detected_filters:
            print(f"[pipeline:{job_id}] ── STEP 3b: Filter value sampling  filters={len(detected_filters)}", flush=True)
            from agent_service.agents.value_sampler import sample_distinct_for_filter as _sdf
            candidate_tables: list[str] = []
            for cands in chart_candidates.values():
                if cands:
                    for t in cands[0].get("tables", []):
                        if t not in candidate_tables:
                            candidate_tables.append(t)

            for flt in detected_filters:
                col_hint = flt.get("column_hint", "")
                if not col_hint:
                    continue
                for table in candidate_tables[:5]:
                    try:
                        available_vals = await _sdf(connection_id, table, col_hint)
                        if available_vals:
                            filter_configs.append({
                                "id": str(uuid.uuid4()),
                                "column": col_hint,
                                "display_name": flt.get("display_name", col_hint.replace("_", " ").title()),
                                "filter_type": flt.get("filter_type", "multi_select"),
                                "available_values": available_vals,
                                "table": table,
                            })
                            break
                    except Exception:
                        continue
            print(f"[pipeline:{job_id}] Filter configs → {len(filter_configs)} with available values", flush=True)

        # Step 3.7: Pre-sample top 3 candidates for every chart IN PARALLEL.
        # Results are cached per-chart so that when the candidate switches on retry we
        # already have the sample context without an extra DB round-trip.
        print(f"[pipeline:{job_id}] ── STEP 3.7: Pre-sampling top candidates  charts={len(charts)}", flush=True)
        from agent_service.agents.value_sampler import sample_top_candidates as _stc
        presample_tasks = [
            _stc(
                connection_id=connection_id,
                candidates=chart_candidates.get(c["id"], []),
                chart_spec=c,
                max_candidates=3,
            )
            for c in charts
        ]
        presample_results = await asyncio.gather(*presample_tasks, return_exceptions=True)
        # chart_presample_cache: {chart_id: {0: ctx, 1: ctx, 2: ctx}}
        chart_presample_cache: dict[str, dict] = {}
        for c, res in zip(charts, presample_results):
            if isinstance(res, Exception):
                print(f"[pipeline:{job_id}] ⚠ presample failed for chart {c['id']} (non-fatal): {res}", flush=True)
                chart_presample_cache[c["id"]] = {}
            else:
                chart_presample_cache[c["id"]] = res or {}

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
                        cropped_image = _crop(img_dict["bytes"], bb)
                        break
            except Exception as _ce:
                print(f"[chart:{cid}] ⚠ could not crop image for visual comparison (non-fatal): {_ce}", flush=True)

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

            needs_retry_ids = zero_row_ids | low_conf_ids

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
                            max_attempts=5,
                            dashboard_date_context=dashboard_date_context,
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
        if gap >= 0.20:
            return None  # clear winner — skip racing

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
                    for _dt in _new_date_tbls[:2]:
                        _dtc_cols = dict(candidates[0].get("key_columns") or {}) if candidates else {}
                        date_table_candidates.append({
                            "tables": [_fact_table, _dt] if _fact_table else [_dt],
                            "key_columns": _dtc_cols,
                            "join": (
                                f"{_fact_table} JOIN {_dt} USING (<date_key>)"
                                if _fact_table else _dt
                            ),
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

        # Map attempt number → which candidate to use.
        # attempts 1,2 → candidates[0]; attempts 3,4 → candidates[1]; attempt 5 → accept best.
        # classify_failure can set _force_switch=True to advance the index early.
        def _pick_candidate(attempt: int) -> Optional[dict]:
            nonlocal _force_switch
            if _force_switch:
                # Move to next candidate; reset flag so we don't keep switching
                _force_switch = False
                idx = min(attempt, len(candidates) - 1)
                return candidates[idx] if candidates else None
            if attempt <= 2:
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
                    previously_tried=tried_tables_history[-4:] if tried_tables_history else None,
                )
            except Exception as e:
                retry_feedback = f"SQL generation failed: {str(e)}. Try a simpler query."
                continue

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
                if cropped_image_bytes is not None and attempt <= 2:
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

            # Classify why it failed and build targeted retry feedback
            failure = classify_failure(
                sql_result=execute_result,
                chart_spec=chart_spec,
                query_plan=query_plan,
            )
            if failure["switch_candidate"]:
                _force_switch = True
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
    ) -> Optional[Dashboard]:
        """Create a Dashboard + Widgets from confirmed chart replication results."""
        try:
            filter_col_names = [f["column"] for f in (filter_configs or [])]
            dashboard = Dashboard(
                id=uuid.uuid4(),
                project_id=uuid.UUID(project_id),
                name=f"Screenshot Dashboard — {len(confirmed_charts)} charts",
                layout_config={"source": "screenshot_replication"},
                filter_config=filter_configs or [],
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

                widget = Widget(
                    id=uuid.uuid4(),
                    dashboard_id=dashboard.id,
                    title=query_plan.get("title") or chart_spec.get("title") or f"Chart {i+1}",
                    widget_type="chart",
                    chart_type=chart_type,
                    sql_query=base_sql,
                    base_sql=base_sql,
                    filterable_columns=filter_col_names,
                    position_x=grid.get("x", 0),
                    position_y=grid.get("y", i * 4),
                    width=grid.get("w", 6),
                    height=grid.get("h", 4),
                    validation_score=result.get("score"),
                    validation_status=result.get("status", "confirmed"),
                    chart_data=chart_data,
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
