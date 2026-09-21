"""Rasterize a PPTX slide's shapes to a PNG using PIL.

Not pixel-perfect — no gradients, themes, or fancy fills. Enough for a VLM to
recognize the diagram structure: rectangles/rounded rects/ovals/diamonds with
their text labels, and connector lines with arrowheads between them.
"""
from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.slide import Slide
from pptx.util import Emu

EMU_PER_PX = 9525  # at 96 dpi
DEFAULT_FILL = "#DDEBF7"
DEFAULT_OUTLINE = "#1F4E78"
DEFAULT_TEXT = "#000000"
LINE_COLOR = "#333333"

# MSO_SHAPE codes for shapes we treat as "diamond/decision"
_DIAMOND_SHAPES = {MSO_SHAPE.DIAMOND, MSO_SHAPE.FLOWCHART_DECISION}
_OVAL_SHAPES = {MSO_SHAPE.OVAL, MSO_SHAPE.FLOWCHART_TERMINATOR,
                MSO_SHAPE.FLOWCHART_CONNECTOR}
_ROUND_RECT_SHAPES = {MSO_SHAPE.ROUNDED_RECTANGLE}


def render_slide_shapes(slide: Slide, slide_w_emu: int, slide_h_emu: int) -> bytes:
    """Return PNG bytes of the slide with its shapes rendered."""
    w_px = max(1, slide_w_emu // EMU_PER_PX)
    h_px = max(1, slide_h_emu // EMU_PER_PX)
    img = Image.new("RGB", (w_px, h_px), "white")
    draw = ImageDraw.Draw(img)

    shapes = list(_iter_shapes(slide.shapes))
    # Draw non-connector shapes first, connectors on top
    for shape in shapes:
        if _is_connector(shape):
            continue
        _draw_shape(draw, shape)
    for shape in shapes:
        if _is_connector(shape):
            _draw_connector(draw, shape)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _iter_shapes(shapes) -> Iterable:
    for s in shapes:
        if s.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(s.shapes)
        else:
            yield s


def _is_connector(shape) -> bool:
    st = shape.shape_type
    return st in {MSO_SHAPE_TYPE.LINE} or (
        # LineFormat-only connectors sometimes report as AUTO_SHAPE with LINE_INVERSE
        hasattr(shape, "connector_format")
    ) and st == MSO_SHAPE_TYPE.LINE


def _emu_to_px(v: int) -> int:
    return int(v // EMU_PER_PX)


def _bbox(shape) -> tuple[int, int, int, int]:
    left = _emu_to_px(shape.left or 0)
    top = _emu_to_px(shape.top or 0)
    right = left + _emu_to_px(shape.width or 0)
    bottom = top + _emu_to_px(shape.height or 0)
    return left, top, right, bottom


def _shape_kind(shape) -> str:
    """Return one of: rect, rounded, oval, diamond, other."""
    st = shape.shape_type
    if st == MSO_SHAPE_TYPE.TEXT_BOX:
        return "textbox"
    if st != MSO_SHAPE_TYPE.AUTO_SHAPE:
        return "other"
    try:
        code = shape.auto_shape_type
    except Exception:
        return "rect"
    if code in _DIAMOND_SHAPES:
        return "diamond"
    if code in _OVAL_SHAPES:
        return "oval"
    if code in _ROUND_RECT_SHAPES:
        return "rounded"
    return "rect"


def _draw_shape(draw: ImageDraw.ImageDraw, shape) -> None:
    left, top, right, bottom = _bbox(shape)
    if right <= left or bottom <= top:
        return
    kind = _shape_kind(shape)
    if kind == "textbox":
        # No outline, just text
        pass
    elif kind == "oval":
        draw.ellipse((left, top, right, bottom), fill=DEFAULT_FILL, outline=DEFAULT_OUTLINE, width=2)
    elif kind == "diamond":
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        draw.polygon(
            [(cx, top), (right, cy), (cx, bottom), (left, cy)],
            fill=DEFAULT_FILL, outline=DEFAULT_OUTLINE,
        )
    elif kind == "rounded":
        radius = min(20, (bottom - top) // 4, (right - left) // 4)
        _rounded_rect(draw, left, top, right, bottom, radius)
    else:
        draw.rectangle((left, top, right, bottom), fill=DEFAULT_FILL, outline=DEFAULT_OUTLINE, width=2)

    _draw_text(draw, shape, left, top, right, bottom)


def _rounded_rect(draw: ImageDraw.ImageDraw, l, t, r, b, radius) -> None:
    if radius <= 0:
        draw.rectangle((l, t, r, b), fill=DEFAULT_FILL, outline=DEFAULT_OUTLINE, width=2)
        return
    draw.rounded_rectangle((l, t, r, b), radius=radius, fill=DEFAULT_FILL,
                           outline=DEFAULT_OUTLINE, width=2)


def _draw_text(draw: ImageDraw.ImageDraw, shape, l, t, r, b) -> None:
    if not getattr(shape, "has_text_frame", False):
        return
    text = shape.text_frame.text.strip()
    if not text:
        return
    font = _pick_font(min((b - t) // 3, (r - l) // max(6, len(text) // 2 + 1), 22))
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = l + (r - l - tw) // 2
    ty = t + (b - t - th) // 2
    draw.text((tx, ty), text, fill=DEFAULT_TEXT, font=font)


def _pick_font(size: int) -> ImageFont.ImageFont:
    size = max(10, size)
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_connector(draw: ImageDraw.ImageDraw, shape) -> None:
    """Connectors have begin/end points; draw a line with an arrowhead."""
    try:
        bx = _emu_to_px(shape.begin_x)
        by = _emu_to_px(shape.begin_y)
        ex = _emu_to_px(shape.end_x)
        ey = _emu_to_px(shape.end_y)
    except (AttributeError, ValueError):
        # Not a connector we can address geometrically — fall back to bbox line
        l, t, r, b = _bbox(shape)
        bx, by, ex, ey = l, t, r, b
    draw.line((bx, by, ex, ey), fill=LINE_COLOR, width=2)
    _arrowhead(draw, bx, by, ex, ey)


def _arrowhead(draw: ImageDraw.ImageDraw, x1, y1, x2, y2, size: int = 10) -> None:
    """Draw a triangular arrowhead at (x2, y2) pointing away from (x1, y1)."""
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    ax = x2 - size * math.cos(angle - math.pi / 7)
    ay = y2 - size * math.sin(angle - math.pi / 7)
    bx = x2 - size * math.cos(angle + math.pi / 7)
    by = y2 - size * math.sin(angle + math.pi / 7)
    draw.polygon([(x2, y2), (ax, ay), (bx, by)], fill=LINE_COLOR)
