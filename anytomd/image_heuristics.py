"""Cheap, local heuristics to classify images without an AI call.

Used by the diagrams-only optimization mode. False positives here (a photo
mistakenly kept) waste one AI call; false negatives (a diagram mistakenly
skipped) lose a diagram — the more damaging failure mode. Thresholds are
tuned to lean toward keeping.
"""
from __future__ import annotations

import io
from typing import Literal

from PIL import Image, ImageFilter, ImageOps

ImageClass = Literal["decorative", "photo", "chart", "screenshot", "diagram", "unknown"]

_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


def _has_alpha(img: Image.Image) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info


def load_rgb(image_bytes: bytes, background: tuple[int, int, int] = _WHITE) -> Image.Image:
    """Open an image as RGB with transparency flattened onto `background`.

    A bare `convert("RGB")` turns transparent pixels black: a PNG of black
    text on a transparent background (common in slide exports) becomes a
    solid black block.
    """
    img = Image.open(io.BytesIO(image_bytes))
    if not _has_alpha(img):
        return img.convert("RGB")
    rgba = img.convert("RGBA")
    base = Image.new("RGBA", rgba.size, background + (255,))
    return Image.alpha_composite(base, rgba).convert("RGB")


def looks_like_diagram(image_bytes: bytes) -> bool:
    """Return True if the image plausibly is a structural diagram.

    Signals used, any of which passes → True:
      - Limited distinct colors (diagrams typically <= 32 dominant colors).
      - High edge density (line art, boxes, arrows).
      - High proportion of near-white pixels (whiteboard/canvas background).
    """
    try:
        img = load_rgb(image_bytes)
    except Exception:
        # If PIL can't open it, be safe: treat as diagram-worthy so we don't drop it
        return True

    # Downsample for speed (heuristics don't need full res)
    img.thumbnail((256, 256), Image.LANCZOS)
    w, h = img.size
    total_px = max(1, w * h)

    # Signal 1: distinct colors after mild quantization
    quant = img.quantize(colors=32).convert("RGB")
    distinct = len({p for p in quant.getdata()})
    if distinct <= 20:
        return True

    # Signal 2: near-white background ratio
    white_px = 0
    for r, g, b in img.getdata():
        if r >= 240 and g >= 240 and b >= 240:
            white_px += 1
    white_ratio = white_px / total_px
    if white_ratio >= 0.55:
        return True

    # Signal 3: edge density (sharp line art)
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_px = sum(1 for p in edges.getdata() if p > 60)
    edge_ratio = edge_px / total_px
    if edge_ratio >= 0.06:
        return True

    return False


def is_decorative(image_bytes: bytes) -> bool:
    """Return True if the image is likely pure decoration (solid blob, gradient
    circle, filler graphic on a cover/divider page).

    Signals — ALL must hold:
      - Very few distinct colors (≤4 after coarse quantization)
      - Dominant color covers ≥60% of pixels
      - Low edge density (<2%) — no text, no line art, no logo strokes
      - No high-frequency detail (few sharp gradients)

    A transparent image must look decorative on white AND on black: dark text
    vanishes on black, white text vanishes on white.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        backgrounds = (_WHITE, _BLACK) if _has_alpha(img) else (_WHITE,)
        return all(_is_decorative_rgb(load_rgb(image_bytes, bg)) for bg in backgrounds)
    except Exception:
        return False


def _is_decorative_rgb(img: Image.Image) -> bool:
    img.thumbnail((160, 160), Image.LANCZOS)
    total_px = max(1, img.size[0] * img.size[1])

    # Dominant-color area
    quant = img.quantize(colors=8).convert("RGB")
    color_counts: dict[tuple[int, int, int], int] = {}
    for p in quant.getdata():
        color_counts[p] = color_counts.get(p, 0) + 1
    if not color_counts:
        return False
    dominant_share = max(color_counts.values()) / total_px
    distinct = len(color_counts)

    # Edge density. FIND_EDGES leaves the 1px frame unfiltered (raw brightness),
    # which would count the frame of any light image as edges — drop it.
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    w, h = edges.size
    if w > 2 and h > 2:
        edges = edges.crop((1, 1, w - 1, h - 1))
    edge_px = sum(1 for p in edges.getdata() if p > 60)
    edge_ratio = edge_px / max(1, edges.size[0] * edges.size[1])

    return distinct <= 4 and dominant_share >= 0.60 and edge_ratio < 0.02


ICON_MAX_SIDE = 128  # px; icons and bullets, too small to carry readable content


def is_icon_sized(image_bytes: bytes) -> bool:
    """True for pictures no larger than an icon (longest side ≤ 128 px).
    Small vision models turn these into invented diagrams or text."""
    try:
        return max(Image.open(io.BytesIO(image_bytes)).size) <= ICON_MAX_SIDE
    except Exception:
        return False


def perceptual_hash(image_bytes: bytes) -> tuple[int, float] | None:
    """256-bit difference hash of the image's content box, plus its aspect ratio.

    The plain border is trimmed first, so the same logo exported with a
    different crop, padding or resolution hashes alike. None if unreadable.
    """
    try:
        gray = load_rgb(image_bytes).convert("L")
    except Exception:
        return None
    box = ImageOps.invert(gray).point(lambda v: 255 if v > 24 else 0).getbbox()
    if box:
        gray = gray.crop(box)
    w, h = gray.size
    if w < 2 or h < 2:
        return None
    px = list(gray.resize((17, 16), Image.LANCZOS).getdata())
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (px[y * 17 + x] > px[y * 17 + x + 1])
    return bits, w / h


def same_picture(a: tuple[int, float], b: tuple[int, float]) -> bool:
    """True if two `perceptual_hash` results show the same picture.

    Measured on a real deck: the same logo in two crops differs by ≤11 of 256
    bits, different pictures by ≥100.
    """
    (ha, ra), (hb, rb) = a, b
    return bin(ha ^ hb).count("1") <= 24 and abs(ra - rb) <= 0.15 * max(ra, rb)


def classify_image(image_bytes: bytes) -> ImageClass:
    """Cheap local classifier — one label per image. No AI, no OCR.

    Used by two-pass routing to decide whether an image is worth sending to a
    premium provider. Precision matters more than recall for `diagram`/`chart`
    (false positives waste premium tokens); `decorative`/`photo` errors on the
    side of local Ollama/skip.
    """
    if is_decorative(image_bytes):
        return "decorative"
    try:
        img = load_rgb(image_bytes)
    except Exception:
        return "unknown"
    img.thumbnail((256, 256), Image.LANCZOS)
    total_px = max(1, img.size[0] * img.size[1])

    quant = img.quantize(colors=32).convert("RGB")
    distinct = len({p for p in quant.getdata()})

    white_px = 0
    for r, g, b in img.getdata():
        if r >= 240 and g >= 240 and b >= 240:
            white_px += 1
    white_ratio = white_px / total_px

    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_px = sum(1 for p in edges.getdata() if p > 60)
    edge_ratio = edge_px / total_px

    # Screenshot: dominant near-white background + moderate edge (UI chrome + text)
    # AND high distinct-color count (typical of anti-aliased UI text)
    if white_ratio >= 0.45 and edge_ratio >= 0.04 and distinct >= 25:
        return "screenshot"

    # Structural diagram: whiteboard-ish + high edges + limited palette
    if white_ratio >= 0.55 and edge_ratio >= 0.05 and distinct <= 40:
        return "diagram"

    # Chart: moderate palette, moderate edges, likely axes/gridlines
    if 8 <= distinct <= 30 and edge_ratio >= 0.03 and white_ratio >= 0.35:
        return "chart"

    # High distinct colors + low edges + not near-white → photo
    if distinct > 60 and edge_ratio < 0.05:
        return "photo"

    return "unknown"
