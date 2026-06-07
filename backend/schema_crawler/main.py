import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
import uuid
from datetime import datetime
from fastapi import FastAPI, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from shared.database import get_db
from shared.models.database_connections import DatabaseConnection
from shared.models.schema_snapshots import SchemaSnapshot
from shared.models.phase2 import SchemaChangeAlert
from shared.models.widgets import Widget
from shared.models.dashboards import Dashboard
from shared.encryption import decrypt
from schema_crawler.crawlers.postgres_crawler import crawl_postgres
from schema_crawler.crawlers.mysql_crawler import crawl_mysql
from schema_crawler.crawlers.redshift_crawler import crawl_redshift
from schema_crawler.diff import compute_schema_diff, flag_affected_widgets

app = FastAPI(title="Visually Schema Crawler", version="2.0.0")

_crawl_jobs: dict[str, dict] = {}


class CrawlRequest(BaseModel):
    connection_id: str
    project_id: str


async def _run_crawl(job_id: str, connection_id: str, project_id: str):
    from shared.database import AsyncSessionLocal
    _crawl_jobs[job_id] = {"status": "running", "result": None, "error": None}

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(DatabaseConnection).where(DatabaseConnection.id == uuid.UUID(connection_id))
            )
            conn = result.scalar_one_or_none()
            if not conn:
                _crawl_jobs[job_id] = {"status": "failed", "result": None, "error": "Connection not found"}
                return

            password = ""
            if conn.encrypted_password:
                password = decrypt(conn.encrypted_password)

            iam_role_arn = None
            if conn.connection_options and isinstance(conn.connection_options, dict):
                iam_role_arn = conn.connection_options.get("iam_role_arn")

            db_type = conn.db_type.value if hasattr(conn.db_type, "value") else str(conn.db_type)

            if db_type == "redshift":
                schema_doc = await crawl_redshift(
                    host=conn.host or "localhost",
                    port=conn.port or 5439,
                    database=conn.database_name or "",
                    user=conn.username or "",
                    password=password,
                    ssl=conn.ssl_enabled,
                    connection_id=connection_id,
                    iam_role_arn=iam_role_arn,
                )
            elif db_type == "postgresql":
                schema_doc = await crawl_postgres(
                    host=conn.host or "localhost",
                    port=conn.port or 5432,
                    database=conn.database_name or "",
                    user=conn.username or "",
                    password=password,
                    ssl=conn.ssl_enabled,
                    connection_id=connection_id,
                )
            elif db_type == "mysql":
                schema_doc = await crawl_mysql(
                    host=conn.host or "localhost",
                    port=conn.port or 3306,
                    database=conn.database_name or "",
                    user=conn.username or "",
                    password=password,
                    ssl=conn.ssl_enabled,
                    connection_id=connection_id,
                )
            else:
                _crawl_jobs[job_id] = {"status": "failed", "result": None, "error": f"Unsupported db_type: {db_type}"}
                return

            # Get previous snapshot for diff
            prev_result = await db.execute(
                select(SchemaSnapshot)
                .where(SchemaSnapshot.connection_id == uuid.UUID(connection_id))
                .order_by(SchemaSnapshot.version.desc())
                .limit(1)
            )
            prev_snapshot = prev_result.scalar_one_or_none()
            prev_version = prev_snapshot.version if prev_snapshot else 0

            new_snapshot = SchemaSnapshot(
                id=uuid.uuid4(),
                connection_id=uuid.UUID(connection_id),
                version=prev_version + 1,
                schema_document=schema_doc,
                table_count=schema_doc.get("total_tables", 0),
                crawl_duration_seconds=schema_doc.get("crawl_duration_seconds"),
                created_at=datetime.utcnow(),
            )
            db.add(new_snapshot)
            await db.flush()

            # Compute schema diff if we have a previous snapshot
            diff_summary = None
            if prev_snapshot:
                old_doc = prev_snapshot.schema_document or {}
                diff = compute_schema_diff(old_doc, schema_doc)
                diff_summary = diff

                if diff["has_breaking_changes"] or diff["column_changes"]:
                    # Find affected widgets for this project's dashboards
                    widgets_result = await db.execute(
                        select(Widget).join(Dashboard, Widget.dashboard_id == Dashboard.id).where(
                            Dashboard.project_id == uuid.UUID(project_id)
                        )
                    )
                    all_widgets = widgets_result.scalars().all()
                    widget_dicts = [
                        {"id": str(w.id), "sql_query": w.sql_query or ""}
                        for w in all_widgets
                    ]
                    affected_ids = flag_affected_widgets(diff, widget_dicts)

                    alert = SchemaChangeAlert(
                        id=uuid.uuid4(),
                        connection_id=uuid.UUID(connection_id),
                        old_snapshot_id=prev_snapshot.id,
                        new_snapshot_id=new_snapshot.id,
                        diff_summary=diff,
                        breaking_changes=diff.get("breaking_changes"),
                        affected_widget_ids=affected_ids,
                        severity=diff.get("severity", "info"),
                        is_acknowledged=False,
                        created_at=datetime.utcnow(),
                    )
                    db.add(alert)

                    # Publish schema.changed event to Redis for real-time frontend alerts
                    try:
                        import redis.asyncio as aioredis
                        REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
                        redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
                        await redis_client.publish(
                            f"schema_alerts:{project_id}",
                            json.dumps({
                                "type": "schema.changed",
                                "connection_id": connection_id,
                                "project_id": project_id,
                                "severity": diff.get("severity", "info"),
                                "has_breaking_changes": diff["has_breaking_changes"],
                                "affected_widget_count": len(affected_ids),
                                "diff": {
                                    "dropped_tables": diff.get("dropped_tables", []),
                                    "added_tables": diff.get("added_tables", []),
                                    "breaking_changes": diff.get("breaking_changes", []),
                                },
                            }),
                        )
                        await redis_client.aclose()
                    except Exception:
                        pass

            await db.commit()

            _crawl_jobs[job_id] = {
                "status": "completed",
                "result": schema_doc,
                "snapshot_id": str(new_snapshot.id),
                "diff_summary": diff_summary,
                "error": None,
            }

        except Exception as e:
            import traceback
            print(f"\n[schema_crawler] JOB {job_id} FAILED:\n{traceback.format_exc()}\n")
            _crawl_jobs[job_id] = {"status": "failed", "result": None, "error": str(e)}


@app.post("/crawl")
async def start_crawl(req: CrawlRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    _crawl_jobs[job_id] = {"status": "pending", "result": None, "error": None}
    background_tasks.add_task(_run_crawl, job_id, req.connection_id, req.project_id)
    return {"job_id": job_id}


@app.get("/crawl/{job_id}")
async def get_crawl_status(job_id: str):
    job = _crawl_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, **job}


@app.get("/health")
async def health():
    return {"status": "ok"}
