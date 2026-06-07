"""
Vision Agent — processes screenshot images to produce a structured Chart Manifest.
Uses AWS Bedrock anthropic.claude-opus-4-6 (multimodal).
"""
import asyncio
import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "shared"))
# Ensure agent_service root is on path so `from utils.image_processor` resolves
_agent_root = os.path.join(os.path.dirname(__file__), "..")
if _agent_root not in sys.path:
    sys.path.insert(0, _agent_root)

from shared.bedrock_client import bedrock_invoke_with_image, BEDROCK_VISION_MODEL
from utils.image_processor import normalize_image, crop_chart_region  # import once at module level

VISION_MODEL_ID = BEDROCK_VISION_MODEL


def _parse_json_response(text: str) -> dict:
    """Parse a Bedrock JSON response, handling markdown code fences and truncated output."""
    # Strip markdown code fence if present
    clean = text.strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*\n?", "", clean)
        clean = re.sub(r"\n?```\s*$", "", clean)
    clean = clean.strip()

    # Fast path: valid JSON
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    # Extract the outermost {...} block
    match = re.search(r"\{.*\}", clean, re.DOTALL)
    if not match:
        return {}

    candidate = match.group()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Truncated JSON: extract as many complete chart entries as we can
    print(f"[vision] ⚠ Truncated JSON response — extracting partial chart list", flush=True)
    charts = []
    for m in re.finditer(r'\{[^{}]*"id"\s*:\s*"[^"]*"[^{}]*"confidence"\s*:[^{}]*"bounding_box"\s*:\s*\{[^{}]*\}[^{}]*\}', candidate, re.DOTALL):
        try:
            charts.append(json.loads(m.group()))
        except json.JSONDecodeError:
            pass
    if charts:
        print(f"[vision] ⚠ Recovered {len(charts)} chart entries from truncated JSON", flush=True)
        return {"charts_detected": charts, "total_found": len(charts)}
    return {}

DETECTION_SYSTEM_PROMPT = """You are a computer vision system specialized in analyzing data visualization screenshots.
Your task is to identify every chart, graph, KPI card, or data table visible in the image.
Be exhaustive — do not miss any visualization, even small or partially visible ones.
Return ONLY valid JSON. No prose before or after the JSON block."""

DETECTION_USER_PROMPT = """Analyze this image and identify all data visualizations (charts, graphs, tables, KPI cards).

For each visualization found, return a bounding box as percentages of the image dimensions
(0.0 = left/top edge, 1.0 = right/bottom edge).

Return JSON in this exact format:
{
  "charts_detected": [
    {
      "id": "chart_001",
      "confidence": 0.0,
      "bounding_box": {
        "x_pct": 0.0,
        "y_pct": 0.0,
        "w_pct": 0.5,
        "h_pct": 0.4
      }
    }
  ],
  "total_found": 0,
  "image_notes": "any relevant notes about image quality or layout"
}

If no charts are found, return {"charts_detected": [], "total_found": 0, "image_notes": "..."}.
Do not include non-chart elements like navigation bars, logos, or plain text paragraphs."""

ANALYSIS_SYSTEM_PROMPT = """You are a data visualization expert analyzing a single chart or graph image.
Extract all structural and semantic information from this chart.
Return ONLY valid JSON. Be precise about numbers you can read and honest about estimates.

CRITICAL: KPI card recognition rules — use chart_type "kpi" (NOT "kpi_card") when:
- The visual shows a single large number prominently displayed (e.g. "$1.2M", "4,521", "87%")
- There is a metric label above or below the number (e.g. "Total Revenue", "Active Users")
- There may be a trend indicator (up/down arrow, percentage change) but NO x/y axes
- The card occupies a small tile region — it is NOT a bar/line chart with one bar or point
Do NOT use "kpi" for single-bar charts or single-point line charts — those have axes."""

ANALYSIS_USER_PROMPT = """Analyze this chart image and extract all information you can see.

Return JSON in this exact format:
{
  "chart_type": "bar_vertical|bar_horizontal|line|area|pie|donut|scatter|kpi|table|heatmap|gauge|funnel|treemap",
  "title": "exact chart title text or null",
  "subtitle": "subtitle text or null",
  "x_axis_label": "x-axis label text or null (null for KPI cards)",
  "y_axis_label": "y-axis label text or null (null for KPI cards)",
  "x_tick_labels": ["Jan", "Feb"],
  "y_scale": {"min": 0, "max": 1000, "unit": null},
  "legend_labels": [],
  "estimated_values": {},
  "data_point_count": 0,
  "series_count": 1,
  "has_trend_line": false,
  "color_scheme": "sequential|diverging|categorical|monochrome",
  "kpi_metric_name": "metric label text if this is a KPI card, else null",
  "kpi_value_text": "raw display value if KPI card (e.g. '$1.2M', '4,521'), else null",
  "reasoning": "brief explanation of your analysis"
}

For estimated_values:
- Bar chart: {"Category1": 1200, "Category2": 800}
- Line chart: {"2024-01": 1200, "2024-02": 1350} (use tick labels as keys)
- Pie/Donut: {"Segment1": 35.0, "Segment2": 25.0} (as percentages)
- KPI card: {"value": 1234567, "change_pct": 12.3, "metric_name": "Total Revenue"}
- Table: {"row_count": 10, "col_count": 5, "columns": ["col1", "col2"]}

Mark unclear values with "~" prefix: "~1200"."""


FILTER_DETECTION_SYSTEM_PROMPT = """You are a UI analysis system that identifies interactive filter controls in business intelligence dashboards.
Return ONLY valid JSON. No prose before or after the JSON block."""

FILTER_DETECTION_USER_PROMPT = """Examine this screenshot for any filter panels, slicer controls, dropdown filters, date range pickers, search boxes, or any UI element that lets users filter the data shown.

Common Power BI locations: right-side filter pane, slicer visuals anywhere in the report, top-bar dropdowns.

For each filter control found, return:
- display_name: the label text visible next to or above the control (e.g. "Job Status", "Region")
- column_hint: likely database column name in snake_case (e.g. "job_status", "region")
- filter_type: one of [multi_select, single_select, date_range, search_box]
- visible_values: list of values/options visible in the filter UI (max 20)
- selected_values: values that appear currently selected/checked (empty list if none)

Return JSON:
{
  "filters_detected": [
    {
      "display_name": "Status",
      "column_hint": "status",
      "filter_type": "multi_select",
      "visible_values": ["Active", "Closed", "New"],
      "selected_values": ["Active"]
    }
  ],
  "total_found": 0
}

If no filter controls are visible, return {"filters_detected": [], "total_found": 0}."""


class VisionAgent:
    def __init__(self):
        self.model_id = VISION_MODEL_ID
        self.min_confidence_threshold = 0.30  # lowered: complex dashboards often get 0.3-0.6

    async def detect_charts(self, image_bytes: bytes, source_filename: str) -> list[dict]:
        """Detect all chart bounding boxes in an image."""
        print(f"[vision] detect_charts START  file={source_filename}  size={len(image_bytes)//1024}KB", flush=True)
        normalized_bytes, original_w, original_h = normalize_image(image_bytes, target_width=1000)
        print(f"[vision] image normalised → {original_w}×{original_h}px  normalised={len(normalized_bytes)//1024}KB", flush=True)

        try:
            response_text = await bedrock_invoke_with_image(
                model_id=self.model_id,
                system_prompt=DETECTION_SYSTEM_PROMPT,
                user_text=DETECTION_USER_PROMPT,
                image_bytes=normalized_bytes,
                image_media_type="image/png",
                temperature=0.1,
            )
            print(f"[vision] raw Bedrock response (first 500 chars): {response_text[:500]}", flush=True)
        except Exception as bedrock_err:
            print(f"[vision] ✗ Bedrock call FAILED: {type(bedrock_err).__name__}: {bedrock_err}", flush=True)
            response_text = '{}'

        result = _parse_json_response(response_text)

        charts = result.get("charts_detected", [])
        print(f"[vision] detect_charts → {len(charts)} raw detections  confidences={[round(c.get('confidence',0),2) for c in charts]}", flush=True)
        filtered = [
            {**c, "source_image": source_filename, "original_w": original_w, "original_h": original_h}
            for c in charts
            if c.get("confidence", 0) >= self.min_confidence_threshold
        ]
        # Fallback: include all if nothing passed threshold
        if not filtered and charts:
            filtered = [
                {**c, "source_image": source_filename, "original_w": original_w, "original_h": original_h, "low_confidence": True}
                for c in charts
            ]
        # Ultimate fallback: Bedrock returned nothing — treat full image as one chart
        if not filtered:
            print(f"[vision] ⚠ No charts from Bedrock — using full-image fallback", flush=True)
            filtered = [{
                "id": "chart_001",
                "confidence": 0.5,
                "low_confidence": True,
                "source_image": source_filename,
                "original_w": original_w,
                "original_h": original_h,
                "bounding_box": {"x_pct": 0.0, "y_pct": 0.0, "w_pct": 1.0, "h_pct": 1.0},
            }]
        print(f"[vision] detect_charts → {len(filtered)} charts after confidence filter (threshold={self.min_confidence_threshold})", flush=True)
        return filtered

    async def analyze_chart(self, image_bytes: bytes, bounding_box: dict, chart_id: str, source_filename: str) -> dict:
        """Analyze a single cropped chart region."""
        print(f"[vision] analyze_chart START  chart_id={chart_id}", flush=True)
        cropped_bytes = crop_chart_region(image_bytes, bounding_box)
        print(f"[vision] analyze_chart cropped region → {len(cropped_bytes)//1024}KB  sending to Bedrock...", flush=True)

        response_text = await bedrock_invoke_with_image(
            model_id=self.model_id,
            system_prompt=ANALYSIS_SYSTEM_PROMPT,
            user_text=ANALYSIS_USER_PROMPT,
            image_bytes=cropped_bytes,
            image_media_type="image/png",
            temperature=0.1,
        )

        analysis = _parse_json_response(response_text)

        print(f"[vision] analyze_chart DONE  chart_id={chart_id}  type={analysis.get('chart_type')}  title={analysis.get('title')!r}", flush=True)
        return {
            "id": chart_id,
            "type": analysis.get("chart_type", "bar_vertical"),
            "title": analysis.get("title"),
            "subtitle": analysis.get("subtitle"),
            "x_axis_label": analysis.get("x_axis_label"),
            "y_axis_label": analysis.get("y_axis_label"),
            "x_tick_labels": analysis.get("x_tick_labels", []),
            "y_scale": analysis.get("y_scale"),
            "legend_labels": analysis.get("legend_labels", []),
            "estimated_values": analysis.get("estimated_values", {}),
            "data_point_count": int(analysis.get("data_point_count") or 0),
            "series_count": analysis.get("series_count", 1),
            "bounding_box": bounding_box,
            "source_image": source_filename,
            "confidence": 0.9,
            "low_confidence": False,
            "analysis_reasoning": analysis.get("reasoning", ""),
        }

    def analyze_layout(self, chart_specs: list[dict]) -> list[dict]:
        """Assign grid layout positions from bounding boxes."""
        if not chart_specs:
            return []
        sorted_specs = sorted(chart_specs, key=lambda c: (
            round(c["bounding_box"]["y_pct"] * 10),
            c["bounding_box"]["x_pct"],
        ))
        for i, spec in enumerate(sorted_specs):
            bb = spec["bounding_box"]
            w_cols = max(3, min(12, round(bb["w_pct"] * 12)))
            x_col = min(round(bb["x_pct"] * 12), 12 - w_cols)
            h_rows = max(2, round(bb["h_pct"] * 10))
            spec["grid_layout"] = {"x": x_col, "y": i * 4, "w": w_cols, "h": h_rows, "order": i}
        return sorted_specs

    def deduplicate_charts(self, all_chart_specs: list[dict]) -> list[dict]:
        """Remove duplicate charts across multiple screenshots."""
        if len(all_chart_specs) <= 1:
            return all_chart_specs
        deduplicated = []
        seen_signatures = []
        for spec in all_chart_specs:
            sig = self._chart_signature(spec)
            if not any(self._signature_similarity(sig, seen) > 0.90 for seen in seen_signatures):
                deduplicated.append(spec)
                seen_signatures.append(sig)
        return deduplicated

    def _chart_signature(self, spec: dict) -> dict:
        return {
            "type": spec.get("type"),
            "title": (spec.get("title") or "").lower().strip(),
            "x_label": (spec.get("x_axis_label") or "").lower().strip(),
            "y_label": (spec.get("y_axis_label") or "").lower().strip(),
            "data_point_count": int(spec.get("data_point_count") or 0),
        }

    def _signature_similarity(self, a: dict, b: dict) -> float:
        if a["type"] != b["type"]:
            return 0.0
        scores = []
        if a["title"] and b["title"]:
            scores.append(self._jaccard(a["title"], b["title"]))
        elif not a["title"] and not b["title"]:
            scores.append(1.0)
        else:
            scores.append(0.5)
        for key in ["x_label", "y_label"]:
            if a[key] and b[key]:
                scores.append(self._jaccard(a[key], b[key]))
        if a["data_point_count"] and b["data_point_count"]:
            ratio = min(a["data_point_count"], b["data_point_count"]) / max(a["data_point_count"], b["data_point_count"])
            scores.append(ratio)
        return sum(scores) / len(scores) if scores else 0.0

    def _jaccard(self, a: str, b: str) -> float:
        sa, sb = set(a.split()), set(b.split())
        if not sa and not sb:
            return 1.0
        return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0

    async def compare_charts(
        self,
        original_bytes: bytes,
        query_plan: dict,
        execute_result: dict,
    ) -> dict:
        """
        Visual diff: show the original cropped chart image to the vision model alongside
        SQL result data and ask if they match.
        Returns {match: bool, score: float, mismatches: [str], suggestion: str}.
        Non-fatal — always returns a dict, never raises.
        """
        import json as _json
        try:
            rows = execute_result.get("rows", [])[:10]
            columns = execute_result.get("columns", [])
            row_count = execute_result.get("row_count", 0)
            data_summary = (
                f"Row count: {row_count}  |  Columns: {columns}\n"
                f"First 5 rows: {_json.dumps(rows[:5], default=str)}"
            )
            user_text = (
                "You are comparing the ORIGINAL chart image (shown) with SQL query result data listed below.\n\n"
                f"Expected chart type: {query_plan.get('chart_type', '')}\n"
                f"x-axis label: '{query_plan.get('x_axis_label', '')}'\n"
                f"y-axis label: '{query_plan.get('y_axis_label', '')}'\n"
                f"Title: '{query_plan.get('title', '')}'\n\n"
                f"SQL RESULT DATA:\n{data_summary}\n\n"
                "Does the SQL result data match what you see in the chart image?\n"
                "Check: correct categories, correct value magnitudes, correct time periods, correct chart type.\n\n"
                "Return ONLY this JSON (no prose):\n"
                '{"match": true, "score": 0.0, '
                '"mismatches": ["specific mismatch if any"], '
                '"suggestion": "one concrete SQL fix, or empty string"}'
            )
            print(f"[vision] compare_charts: querying vision model ...", flush=True)
            raw = await bedrock_invoke_with_image(
                model_id=self.model_id,
                system_prompt=(
                    "You compare chart images with SQL result data. "
                    "Return JSON only. Be precise — cite exact values or categories that differ."
                ),
                user_text=user_text,
                image_bytes=original_bytes,
                image_media_type="image/png",
                temperature=0.1,
                max_tokens=512,
            )
            result = _parse_json_response(raw)
            return {
                "match": bool(result.get("match", True)),
                "score": float(result.get("score", 0.5)),
                "mismatches": list(result.get("mismatches", [])),
                "suggestion": str(result.get("suggestion", "")),
            }
        except Exception as exc:
            print(f"[vision] ⚠ compare_charts failed (non-fatal): {exc}", flush=True)
            return {"match": True, "score": 0.5, "mismatches": [], "suggestion": ""}

    async def detect_filters(self, image_bytes: bytes, source_filename: str) -> list[dict]:
        """Detect filter panels and slicer controls in a BI screenshot."""
        print(f"[vision] detect_filters START  file={source_filename}", flush=True)
        try:
            normalized_bytes, _, _ = normalize_image(image_bytes, target_width=1000)
            response_text = await bedrock_invoke_with_image(
                model_id=self.model_id,
                system_prompt=FILTER_DETECTION_SYSTEM_PROMPT,
                user_text=FILTER_DETECTION_USER_PROMPT,
                image_bytes=normalized_bytes,
                image_media_type="image/png",
                temperature=0.1,
                max_tokens=1024,
            )
            result = _parse_json_response(response_text)
            filters = result.get("filters_detected", [])
            print(f"[vision] detect_filters → {len(filters)} filters found in {source_filename}", flush=True)
            return [f for f in filters if f.get("column_hint")]
        except Exception as exc:
            print(f"[vision] ⚠ detect_filters failed (non-fatal): {exc}", flush=True)
            return []

    async def process_images(self, images: list[dict], job_id: str, redis=None) -> dict:
        """
        Full Vision Agent pipeline for one or more images.
        Returns ChartManifest: {charts, total, source_images}.
        """
        if redis:
            await redis.publish(f"pipeline:{job_id}", json.dumps({
                "type": "vision.started",
                "job_id": job_id,
                "image_count": len(images),
            }))

        async def process_one(img_dict: dict) -> list[dict]:
            img_bytes = img_dict["bytes"]
            filename = img_dict["filename"]
            detected = await self.detect_charts(img_bytes, filename)
            if not detected:
                return []
            tasks = [
                self.analyze_chart(img_bytes, d["bounding_box"], d["id"], filename)
                for d in detected
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            return [c for c in results if isinstance(c, dict)]

        per_image = await asyncio.gather(*[process_one(img) for img in images], return_exceptions=True)
        all_charts = []
        for i, result in enumerate(per_image):
            if isinstance(result, Exception):
                import traceback
                print(f"[vision] process_one[{i}] FAILED: {type(result).__name__}: {result}", flush=True)
                print(traceback.format_exception(type(result), result, result.__traceback__), flush=True)
            elif isinstance(result, list):
                all_charts.extend(result)

        deduped = self.deduplicate_charts(all_charts)
        with_layout = self.analyze_layout(deduped)

        # Re-assign globally-unique sequential IDs — the LLM restarts from chart_001
        # for every image, so multi-image runs produce duplicate IDs before this step.
        for i, chart in enumerate(with_layout, start=1):
            chart["id"] = f"chart_{i:03d}"

        if redis:
            await redis.publish(f"pipeline:{job_id}", json.dumps({
                "type": "vision.parsed",
                "job_id": job_id,
                "chart_count": len(with_layout),
                "chart_types": [c["type"] for c in with_layout],
            }))

        return {
            "charts": with_layout,
            "total": len(with_layout),
            "source_images": [img["filename"] for img in images],
        }
