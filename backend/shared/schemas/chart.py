from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ChartType(str, Enum):
    # ── Basic ────────────────────────────────────────
    line = "line"
    bar_vertical = "bar_vertical"
    bar_horizontal = "bar_horizontal"
    pie = "pie"
    donut = "donut"
    kpi = "kpi"
    scatter = "scatter"
    table = "table"
    # ── Multi-series bar ────────────────────────────
    stacked_bar = "stacked_bar"
    stacked_bar_100 = "stacked_bar_100"
    stacked_bar_horizontal = "stacked_bar_horizontal"
    grouped_bar = "grouped_bar"
    # ── Area ────────────────────────────────────────
    area = "area"
    stacked_area = "stacked_area"
    # ── Combo ───────────────────────────────────────
    combo = "combo"
    # ── Extended scatter ────────────────────────────
    bubble = "bubble"
    # ── Distribution ────────────────────────────────
    histogram = "histogram"
    waterfall = "waterfall"
    funnel = "funnel"
    # ── Hierarchical / relational ───────────────────
    treemap = "treemap"
    heatmap = "heatmap"
    sunburst = "sunburst"
    # ── KPI variants ────────────────────────────────
    gauge = "gauge"
    multi_row_card = "multi_row_card"
    # ── Table variants ──────────────────────────────
    pivot_table = "pivot_table"
    data_table = "data_table"
    # ── Statistical ─────────────────────────────────
    box_plot = "box_plot"
    # ── Comparison ──────────────────────────────────
    bullet = "bullet"
    scorecard = "scorecard"
    dot_plot = "dot_plot"
    radar = "radar"
    # ── Trend / rank ────────────────────────────────
    ribbon = "ribbon"
    # ── Flow / relational ───────────────────────────
    sankey = "sankey"
    chord = "chord"
    network = "network"
    # ── Time-based ──────────────────────────────────
    gantt = "gantt"
    timeline = "timeline"
    calendar_heatmap = "calendar_heatmap"
    # ── Text ────────────────────────────────────────
    word_cloud = "word_cloud"
    # ── Hierarchical ────────────────────────────────
    org_chart = "org_chart"
    # ── Part-to-whole (advanced) ─────────────────────
    marimekko = "marimekko"
    # ── Geographic ──────────────────────────────────
    choropleth = "choropleth"


class QueryPlan(BaseModel):
    sql: str
    chart_type: str
    table_used: str
    x_axis_label: str
    y_axis_label: str
    title: str
    reasoning: str
    db_dialect: str


class ChartSpec(BaseModel):
    chart_type: str
    title: str
    x_axis_label: str
    y_axis_label: str
    data: dict


class ChartManifest(BaseModel):
    job_id: str
    query_plan: QueryPlan
    chart_spec: ChartSpec
    image_url: Optional[str] = None
    validation_score: Optional[float] = None
    passed: bool = False
    low_confidence: bool = False
