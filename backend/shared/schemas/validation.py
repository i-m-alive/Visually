from pydantic import BaseModel
from typing import Optional


class DimensionScores(BaseModel):
    chart_type: float
    axis_labels: float
    data_shape: float
    completeness: float


class RetryFeedback(BaseModel):
    attempt: int
    strategy: str
    feedback: str


class ValidationResult(BaseModel):
    score: float
    passed: bool
    dimension_scores: DimensionScores
    retry_feedback: Optional[RetryFeedback] = None
    low_confidence: bool = False
