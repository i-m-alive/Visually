"""
Skill: detect_charts
Detect all chart bounding boxes in a screenshot using the Vision model.
Usage:
    python detect_charts.py path/to/screenshot.png
"""
import asyncio
import json
import re
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", "shared"))
from shared.bedrock_client import bedrock_invoke_with_image, BEDROCK_VISION_MODEL

SYSTEM_PROMPT = """You are a computer vision system analyzing data visualization screenshots.
Identify every chart, KPI card, or data table visible in the image.

Return ONLY valid JSON:
{
  "charts": [
    {
      "chart_id": "chart_1",
      "x": 0.0,
      "y": 0.0,
      "width": 0.5,
      "height": 0.4,
      "confidence": 0.95
    }
  ]
}
Coordinates are fractions of image dimensions (0.0 to 1.0)."""


async def detect_charts(image_bytes: bytes, media_type: str = "image/png") -> dict:
    raw = await bedrock_invoke_with_image(
        model_id=BEDROCK_VISION_MODEL,
        system_prompt=SYSTEM_PROMPT,
        user_text="Detect all charts and return their bounding boxes.",
        image_bytes=image_bytes,
        image_media_type=media_type,
        max_tokens=1024,
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
        print("Usage: detect_charts.py path/to/image.png")
        sys.exit(1)
    img = Path(path).read_bytes()
    result = asyncio.run(detect_charts(img))
    print(json.dumps(result, indent=2))
