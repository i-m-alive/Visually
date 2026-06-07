from pydantic import BaseModel
from typing import Optional


class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    owner_id: str
    created_at: str


class ConnectionCreate(BaseModel):
    name: str
    db_type: str
    host: Optional[str] = None
    port: Optional[int] = None
    database_name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    ssl_enabled: bool = False
    connection_options: Optional[dict] = None


class ConnectionResponse(BaseModel):
    id: str
    project_id: str
    name: str
    db_type: str
    host: Optional[str]
    port: Optional[int]
    database_name: Optional[str]
    username: Optional[str]
    ssl_enabled: bool
    is_active: bool
    last_tested_at: Optional[str]
    created_at: str


class ConnectionTestResult(BaseModel):
    success: bool
    message: str
    latency_ms: Optional[float] = None
