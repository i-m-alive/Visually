import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Load .env so DATABASE_URL / ENCRYPTION_KEY resolve to the correct values
_env_file = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
if os.path.exists(_env_file):
    try:
        from dotenv import load_dotenv as _load_dotenv
        _load_dotenv(_env_file, override=True)
    except ImportError:
        pass

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from shared.database import get_db
from query_executor.sandbox import validate_sql
from query_executor.drivers.router import route_and_execute

app = FastAPI(title="Visually Query Executor", version="1.0.0")

SERVICE_SECRET = os.getenv("SERVICE_SECRET", "internal-service-secret")


class ExecuteRequest(BaseModel):
    connection_id: str
    sql: str
    row_limit: int = 10000
    timeout_seconds: int = 30


class ExecuteResponse(BaseModel):
    rows: list[dict]
    row_count: int
    columns: list[str]
    duration_ms: float
    truncated: bool
    error: Optional[str] = None


@app.post("/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest, db: AsyncSession = Depends(get_db)):
    is_safe, reason = validate_sql(req.sql)
    if not is_safe:
        raise HTTPException(status_code=400, detail=f"SQL validation failed: {reason}")

    try:
        result = await route_and_execute(
            connection_id=req.connection_id,
            sql=req.sql,
            timeout_seconds=req.timeout_seconds,
            row_limit=req.row_limit,
            db=db,
        )
        return ExecuteResponse(**result)
    except Exception as e:
        print(f"[executor] ✗ unhandled exception for connection={req.connection_id}: {e}", flush=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}
