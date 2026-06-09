"""
Image preprocessing utilities for the Vision Agent.
Uses Pillow (PIL) for all operations.
"""
import io
import math

from PIL import Image

MIN_WIDTH = 400
MIN_HEIGHT = 300
MAX_WIDTH = 1568
MAX_HEIGHT = 1568

# Raised from 1000 → 1400 so small axis-label text stays legible after resize.
_DEFAULT_NORMALIZE_WIDTH = 1400


def validate_image(image_bytes: bytes) -> dict:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            return {
                "valid": False, "width": w, "height": h,
                "format": img.format,
                "error": f"Image too small: {w}x{h}. Minimum: {MIN_WIDTH}x{MIN_HEIGHT}",
            }
        return {"valid": True, "width": w, "height": h, "format": img.format, "error": None}
    except Exception as e:
        return {"valid": False, "width": 0, "height": 0, "format": None, "error": str(e)}


def normalize_image(image_bytes: bytes, target_width: int = _DEFAULT_NORMALIZE_WIDTH) -> tuple[bytes, int, int]:
    """
    Normalize to target_width, convert to RGB PNG.
    Returns (normalized_bytes, original_width, original_height).
    """
    img = Image.open(io.BytesIO(image_bytes))
    original_w, original_h = img.size

    if img.mode in ("RGBA", "P", "LA"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        alpha = img.split()[-1] if "A" in img.mode else None
        background.paste(img, mask=alpha)
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    aspect = original_h / original_w
    new_w = min(target_width, original_w)
    new_h = int(new_w * aspect)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    if new_w > MAX_WIDTH or new_h > MAX_HEIGHT:
        img.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.LANCZOS)

    buf = io.BytesIO()
    # optimize=False preserves full color fidelity; PNG is lossless so size cost is minor.
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue(), original_w, original_h


def crop_chart_region(
    image_bytes: bytes,
    bounding_box: dict,
    padding_pct: float = 0.02,
) -> tuple[bytes, float]:
    """
    Crop a chart region from the full image with padding.
    Returns (cropped_bytes, ocr_quality_score).

    ocr_quality_score is 0.0–1.0:
      - 1.0  →  crop covers ≥15% of image area (large, easy to read)
      - 0.5  →  crop covers ~5% (typical chart in a busy dashboard)
      - 0.0  →  tiny crop (<2%) — text likely illegible
    """
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    # Use round() instead of int() so border pixels aren't silently dropped.
    x = max(0.0, (bounding_box["x_pct"] - padding_pct) * w)
    y = max(0.0, (bounding_box["y_pct"] - padding_pct) * h)
    x2 = min(w, (bounding_box["x_pct"] + bounding_box["w_pct"] + padding_pct) * w)
    y2 = min(h, (bounding_box["y_pct"] + bounding_box["h_pct"] + padding_pct) * h)
    cropped = img.crop((round(x), round(y), round(x2), round(y2)))

    # OCR quality: fraction of total image area covered by this crop.
    crop_area_frac = ((x2 - x) * (y2 - y)) / (w * h) if (w * h) > 0 else 0.0
    # Smooth score: 1.0 at 15%+ area, 0.0 at 1% area, linear in log-space.
    MIN_AREA_LOG = math.log(0.01 + 1e-9)
    GOOD_AREA_LOG = math.log(0.15 + 1e-9)
    area_log = math.log(crop_area_frac + 1e-9)
    quality = max(0.0, min(1.0, (area_log - MIN_AREA_LOG) / (GOOD_AREA_LOG - MIN_AREA_LOG)))

    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue(), quality


def deduplicate_bboxes(chart_detections: list[dict], iou_threshold: float = 0.40) -> list[dict]:
    """
    Remove overlapping bounding boxes (IoU ≥ iou_threshold).
    Keeps the higher-confidence detection when two boxes overlap significantly.
    """
    if len(chart_detections) <= 1:
        return chart_detections

    def _iou(a: dict, b: dict) -> float:
        ax1, ay1 = a["x_pct"], a["y_pct"]
        ax2, ay2 = ax1 + a["w_pct"], ay1 + a["h_pct"]
        bx1, by1 = b["x_pct"], b["y_pct"]
        bx2, by2 = bx1 + b["w_pct"], by1 + b["h_pct"]
        inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        union = a["w_pct"] * a["h_pct"] + b["w_pct"] * b["h_pct"] - inter
        return inter / union if union > 0 else 0.0

    # Sort by confidence descending — keep the most-confident box when two overlap.
    sorted_detections = sorted(
        chart_detections, key=lambda c: c.get("confidence", 0), reverse=True
    )
    kept: list[dict] = []
    for det in sorted_detections:
        bb = det.get("bounding_box", {})
        if not bb:
            kept.append(det)
            continue
        overlap = any(_iou(bb, k.get("bounding_box", {})) >= iou_threshold for k in kept)
        if not overlap:
            kept.append(det)
    return kept
