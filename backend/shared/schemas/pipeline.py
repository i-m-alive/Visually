from pydantic import BaseModel
from typing import Optional, Any, Literal, Union


class IntentClassifiedEvent(BaseModel):
    type: Literal["intent.classified"]
    job_id: str
    intent_type: str
    vagueness_score: float
    confidence: float


class SchemaFetchedEvent(BaseModel):
    type: Literal["schema.fetched"]
    job_id: str
    table_count: int
    important_tables: list[str]


class QueryGeneratedEvent(BaseModel):
    type: Literal["query.generated"]
    job_id: str
    sql: str
    chart_type: str
    table_used: str
    title: str


class QueryExecutedEvent(BaseModel):
    type: Literal["query.executed"]
    job_id: str
    row_count: int
    duration_ms: float


class ChartRenderedEvent(BaseModel):
    type: Literal["chart.rendered"]
    job_id: str
    chart_type: str


class ValidationScoredEvent(BaseModel):
    type: Literal["validation.scored"]
    job_id: str
    score: float
    passed: bool
    dimension_scores: Optional[dict] = None


class ValidationRetryEvent(BaseModel):
    type: Literal["validation.retry"]
    job_id: str
    attempt: int
    strategy: str


class ChartConfirmedEvent(BaseModel):
    type: Literal["chart.confirmed"]
    job_id: str
    score: float
    chart_data: dict
    low_confidence: bool = False


class PipelineErrorEvent(BaseModel):
    type: Literal["pipeline.error"]
    job_id: str
    message: str
    recoverable: bool = False


PipelineEvent = Union[
    IntentClassifiedEvent,
    SchemaFetchedEvent,
    QueryGeneratedEvent,
    QueryExecutedEvent,
    ChartRenderedEvent,
    ValidationScoredEvent,
    ValidationRetryEvent,
    ChartConfirmedEvent,
    PipelineErrorEvent,
]
