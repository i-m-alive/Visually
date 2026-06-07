# Vision Agent Skills

Model: `BEDROCK_VISION_MODEL` (configurable via `BEDROCK_VISION_MODEL_ID` env var)
Default: `anthropic.claude-3-sonnet-20240229-v1:0`

## Skills

### detect_charts
Send a normalised screenshot (1000px wide, RGB PNG) to the vision model.
Returns a list of bounding boxes `{chart_id, x, y, width, height, confidence}` for every
chart, KPI card, or data table detected in the image.

**Script:** `scripts/detect_charts.py`

---

### analyze_chart
Crop a single chart region from the screenshot using its bounding box (+ 10px padding),
then send the crop to the vision model for deep analysis.
Returns `{chart_type, title, x_axis_label, y_axis_label, data_points, estimated_values, confidence}`.

**Script:** `scripts/analyze_chart.py`

---

### deduplicate_charts
Remove duplicate detections across multiple screenshots using Jaccard word-level similarity
on (type + title + x_label + y_label) and data-point count ratio.
Threshold: 90% similarity → duplicate.

**Script:** `scripts/deduplicate_charts.py`

---

### analyze_layout
Assign grid positions (row, col, row_span, col_span) to each detected chart based on its
normalised bounding-box coordinates. Used to reconstruct the original dashboard layout.

**Script:** `scripts/analyze_layout.py`
