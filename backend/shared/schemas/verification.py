"""
Verification schemas for the canvas verification loop (Phase B).
"""
from __future__ import annotations
from pydantic import BaseModel
from typing import Optional


class ChartVerificationResult(BaseModel):
    chart_id: str
    chart_title: str
    chart_type_expected: str
    chart_type_actual: str
    type_match: bool
    has_data: bool
    row_count: int
    expected_row_count: int
    data_shape_score: float   # 0-1
    category_coverage: float  # 0-1
    value_plausibility: float # 0-1
    overall_score: float      # 0-1
    passed: bool
    issues: list[str]
    retry_feedback: Optional[str] = None


class DashboardVerificationReport(BaseModel):
    total_charts: int
    passed_charts: int
    failed_charts: int
    overall_score: float
    passed: bool              # True when all charts pass
    loop: int = 1
    results: list[ChartVerificationResult]
