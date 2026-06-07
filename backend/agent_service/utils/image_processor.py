"""
Image preprocessing utilities for the Vision Agent.
Uses Pillow (PIL) for all operations.
"""
import io

from PIL import Image

MIN_WIDTH = 400
MIN_HEIGHT = 300
MAX_WIDTH = 1568
MAX_HEIGHT = 1568


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


def normalize_image(image_bytes: bytes, target_width: int = 1000) -> tuple[bytes, int, int]:
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
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), original_w, original_h


def crop_chart_region(
    image_bytes: bytes,
    bounding_box: dict,
    padding_pct: float = 0.02,
) -> bytes:
    """Crop a chart region from the full image with padding."""
    img = Image.open(io.BytesIO(image_bytes))
    w, h = img.size
    x = max(0, (bounding_box["x_pct"] - padding_pct) * w)
    y = max(0, (bounding_box["y_pct"] - padding_pct) * h)
    x2 = min(w, (bounding_box["x_pct"] + bounding_box["w_pct"] + padding_pct) * w)
    y2 = min(h, (bounding_box["y_pct"] + bounding_box["h_pct"] + padding_pct) * h)
    cropped = img.crop((int(x), int(y), int(x2), int(y2)))
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue()
