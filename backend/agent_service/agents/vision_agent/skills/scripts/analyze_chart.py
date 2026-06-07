"""
Skill: analyze_chart
Deep-analyze a single cropped chart region.
Usage:
    python analyze_chart.py path/to/crop.png
"""
import asyncio
import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
from shared.bedrock_client import bedrock_invoke_with_image, BEDROCK_VISION_MODEL

SYSTEM_PROMPT = """You are analyzing a cropped chart image extracted from a dashboard screenshot.
Identify the chart type precisely and extract all available metadata.

CHART TYPE REFERENCE — choose exactly one of these values for "chart_type":
  bar_vertical        — vertical bars, one bar per category (most common bar chart)
  bar_horizontal      — horizontal bars, categories on Y-axis
  stacked_bar         — vertical bars subdivided into colored segments (multiple series stacked)
  stacked_bar_100     — vertical stacked bars where each bar reaches 100% (percentage stacked)
  stacked_bar_horizontal — horizontal bars subdivided into colored segments
  grouped_bar         — multiple bars side-by-side per category (clustered bar)
  line                — one or more lines connecting data points over a continuous axis
  area                — filled area beneath a single line
  stacked_area        — multiple filled areas stacked on top of each other
  combo               — bar chart overlaid with a line chart sharing the same X-axis
  pie                 — circular chart divided into slices by proportion
  donut               — pie chart with a hollow center
  sunburst            — concentric ring chart showing hierarchical data (rings = levels)
  scatter             — individual points plotted on X/Y axes (no connecting lines)
  bubble              — scatter plot where each point has a third dimension encoded as circle size
  kpi                 — single large prominent number (metric card / scorecard)
  multi_row_card      — multiple KPI values arranged in rows or a compact grid
  gauge               — speedometer / dial showing a single value vs a scale or target
  histogram           — bar chart where bars represent frequency bins of a continuous variable
  waterfall           — chart showing cumulative effect of sequential positive/negative values
  funnel              — progressively narrowing bars showing stage-by-stage conversion
  treemap             — rectangular tiles sized by value, nested for hierarchy
  heatmap             — grid of colored cells where color encodes a numeric intensity
  table               — rows and columns of text/numeric data (no visualization)
  data_table          — styled table with conditional formatting or totals rows
  pivot_table         — cross-tabulation with row/column dimensions and aggregated values
  radar               — spider/web chart with multiple axes radiating from center (polygon shape)
  dot_plot            — strip plot with individual dots per category along a common axis
  bullet              — horizontal bar showing actual vs target with background range bands
  scorecard           — multi-row table with metrics, actual values, targets, and progress indicators
  ribbon              — bump/rank chart with thick lines tracking category rankings over time
  box_plot            — box-and-whisker showing quartiles (Q1, median, Q3) and outlier whiskers
  sankey              — flow diagram with nodes and proportional-width curved links between them
  chord               — circular diagram with arcs and chords showing bidirectional flows
  network             — node-link graph with circles (nodes) connected by lines (edges)
  gantt               — horizontal bar chart showing tasks over time with start/end dates
  timeline            — horizontal line with event markers placed at dates
  calendar_heatmap    — grid of squares arranged as a calendar, colored by daily value intensity
  word_cloud          — text visualization where word size encodes frequency or importance
  org_chart           — tree diagram showing hierarchical relationships (parent→child nodes)
  marimekko           — mosaic chart where both bar width and height encode proportional values
  choropleth          — geographic map with regions colored by data value intensity

Return ONLY valid JSON with no extra text:
{
  "chart_type": "<one value from list above>",
  "title": "Chart title or empty string if not visible",
  "subtitle": "Subtitle or empty string",
  "x_axis_label": "X axis label or empty string",
  "y_axis_label": "Y axis label or empty string",
  "data_point_count": 12,
  "series_count": 1,
  "categories": ["Jan", "Feb", "Mar"],
  "estimated_values": [100, 150, 200],
  "legend_labels": [],
  "confidence": 0.9,
  "analysis_reasoning": "Brief explanation of chart type classification"
}

Rules:
- For multi-series charts (stacked_bar, grouped_bar, stacked_area, combo), series_count > 1 and legend_labels lists each series name
- For bubble charts, estimated_values should be the size dimension values
- For gauge, estimated_values[0] = current value, estimated_values[1] = max/target if visible
- For heatmap, categories = row labels, legend_labels = column labels
- For sunburst, categories = parent labels, legend_labels = child labels
- For waterfall, estimated_values may include negative numbers
- confidence should reflect certainty of chart_type classification (0.3–1.0)"""


async def analyze_chart(image_bytes: bytes, media_type: str = "image/png") -> dict:
    raw = await bedrock_invoke_with_image(
        model_id=BEDROCK_VISION_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_text="Analyze this chart image and return structured JSON with chart_type and all metadata.",
        image_bytes=image_bytes,
        image_media_type=media_type,
        max_tokens=1536,
        temperature=0.0,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"```$", "", raw).strip()
    return json.loads(raw)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print("Usage: analyze_chart.py path/to/image.png")
        sys.exit(1)
    img = Path(path).read_bytes()
    result = asyncio.run(analyze_chart(img))
    print(json.dumps(result, indent=2))
