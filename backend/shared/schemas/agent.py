from pydantic import BaseModel
from typing import Optional, List, Any


class TimeRange(BaseModel):
    type: str  # "relative" | "absolute"
    value: str


class FilterCondition(BaseModel):
    column: str
    op: str
    value: Any


class IntentEntities(BaseModel):
    metrics: List[str] = []
    dimensions: List[str] = []
    time_range: Optional[TimeRange] = None
    chart_type: Optional[str] = None
    filters: List[FilterCondition] = []


class IntentRequest(BaseModel):
    text: str
    project_id: str


class IntentResult(BaseModel):
    intent_type: str
    confidence: float
    entities: IntentEntities
    vagueness_score: float
    followup_ref: Optional[str] = None
    sub_intents: List[str] = []
    reasoning: str
    # How the answer should be presented: "chart" (visualize) or "text" (prose answer).
    output_mode: str = "chart"


class SchemaResolved(BaseModel):
    table_name: str
    columns: List[dict]
    relationships: List[dict]
