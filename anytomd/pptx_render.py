"""Slide images for PPTX: exact via PowerPoint, approximate via PIL.

Used by slide vision (the model reads the rendered slide) and to show a
picture the way the reader sees it when text boxes sit on top of it.

- PowerPoint (Windows with Office): one COM session through PowerShell
  exports every slide as PNG. The file is opened read-only, without a
  window; PowerPoint is quit afterwards only if it has no other
  presentations open (never closes the user's work).
- PIL: shapes with their fills and borders, pictures, text wrapped in its
  box, connectors with arrowheads, block arrows — in drawing order. Rough,
  but the layout and every word are where they belong.

`ANYTOMD_PPTX_RENDERER=auto|powerpoint|pil` (default auto: PowerPoint when it
works, else PIL).
"""
from __future__ import annotations

import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from .converters.pptx_shapes import ShapeInfo

RENDER_WIDTH = 2560  # px; crops of pictures stay sharp, page vision downsizes to 1568

_PS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app = New-Object -ComObject PowerPoint.Application
$pres = $app.Presentations.Open($env:ANYTOMD_SRC, -1, 0, 0)
try {
  for ($i = 1; $i -le $pres.Slides.Count; $i++) {
    $pres.Slides.Item($i).Export((Join-Path $env:ANYTOMD_OUT ("s{0}.png" -f $i)), 'PNG',
                                 [int]$env:ANYTOMD_W, [int]$env:ANYTOMD_H)
  }
} finally {
  $pres.Close()
  if ($app.Presentations.Count -eq 0) { $app.Quit() }
}
"""

_powerpoint_failed = False  # don't retry a broken PowerPoint for every file


def renderer_mode() -> str:
    mode = os.getenv("ANYTOMD_PPTX_RENDERER", "auto").strip().lower()
    return mode if mode in ("auto", "powerpoint", "pil") else "auto"


def render_with_powerpoint(path: Path, slide_count: int, slide_w: float,
                           slide_h: float) -> dict[int, bytes] | None:
    """{slide number (1-based): PNG} for every slide, or None if PowerPoint
    is unavailable or fails."""
    global _powerpoint_failed
    if _powerpoint_failed or renderer_mode() == "pil" or sys.platform != "win32":
        return None
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if not shell:
        return None
    width = RENDER_WIDTH
    height = max(1, round(RENDER_WIDTH * slide_h / slide_w))
    with tempfile.TemporaryDirectory(prefix="anytomd_slides_") as out:
        env = dict(os.environ, ANYTOMD_SRC=str(path.resolve()), ANYTOMD_OUT=out,
                   ANYTOMD_W=str(width), ANYTOMD_H=str(height))
        try:
            subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                            "-Command", _PS_SCRIPT],
                           env=env, capture_output=True, timeout=60 + 5 * slide_count,
                           stdin=subprocess.DEVNULL, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            _powerpoint_failed = True
            return None
        images: dict[int, bytes] = {}
        for i in range(1, slide_count + 1):
            f = Path(out) / f"s{i}.png"
            if not f.exists():
                _powerpoint_failed = True
                return None
            images[i] = f.read_bytes()
    return images


def crop_box(slide_png: bytes, info: ShapeInfo, slide_w: float, slide_h: float) -> bytes:
    """The part of a rendered slide under `info`'s box, as PNG."""
    img = Image.open(io.BytesIO(slide_png))
    sx, sy = img.width / slide_w, img.height / slide_h
    box = (max(0, int(info.left * sx)), max(0, int(info.top * sy)),
           min(img.width, int(math.ceil(info.right * sx))),
           min(img.height, int(math.ceil(info.bottom * sy))))
    buf = io.BytesIO()
    img.crop(box).save(buf, format="PNG")
    return buf.getvalue()


# ---------- PIL renderer ----------

_SCHEME = {"bg1": "#ffffff", "lt1": "#ffffff", "bg2": "#eeeeee", "lt2": "#eeeeee",
           "tx1": "#000000", "dk1": "#000000", "tx2": "#1f2937", "dk2": "#1f2937"}
_ARROW_POLYS = {  # unit polygons, x/y in 0..1 of the box
    "rightArrow": [(0, .25), (.6, .25), (.6, 0), (1, .5), (.6, 1), (.6, .75), (0, .75)],
    "leftArrow": [(1, .25), (.4, .25), (.4, 0), (0, .5), (.4, 1), (.4, .75), (1, .75)],
    "downArrow": [(.25, 0), (.75, 0), (.75, .6), (1, .6), (.5, 1), (0, .6), (.25, .6)],
    "upArrow": [(.25, 1), (.75, 1), (.75, .4), (1, .4), (.5, 0), (0, .4), (.25, .4)],
}


def render_with_pil(infos: list[ShapeInfo], slide_w: float, slide_h: float) -> bytes:
    width = RENDER_WIDTH
    height = max(1, round(RENDER_WIDTH * slide_h / slide_w))
    scale = width / slide_w
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    for info in infos:
        box = (info.left * scale, info.top * scale, info.right * scale, info.bottom * scale)
        if box[2] - box[0] < 1 and box[3] - box[1] < 1 and info.kind != "connector":
            continue
        try:
            if info.kind == "picture":
                _paste_picture(img, info, box)
            elif info.kind == "connector":
                _draw_connector(draw, info, scale)
            elif info.kind in ("text", "shape"):
                _draw_shape(draw, info, box)
                if info.kind == "text":
                    _draw_text(draw, info, box, scale)
        except Exception:
            continue
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _colour(node) -> str | None:
    """Colour of an `a:solidFill` (srgbClr or a scheme colour)."""
    if node is None:
        return None
    srgb = node.xpath("./a:srgbClr/@val")
    if srgb:
        return f"#{srgb[0]}"
    scheme = node.xpath("./a:schemeClr/@val")
    if scheme:
        return _SCHEME.get(str(scheme[0]), "#d9d9d9")
    return None


def _paste_picture(img: Image.Image, info: ShapeInfo, box) -> None:
    pic = Image.open(io.BytesIO(info.shape.image.blob)).convert("RGBA")
    w, h = max(1, int(box[2] - box[0])), max(1, int(box[3] - box[1]))
    pic = pic.resize((w, h), Image.LANCZOS)
    img.paste(pic, (int(box[0]), int(box[1])), pic)


def _draw_shape(draw: ImageDraw.ImageDraw, info: ShapeInfo, box) -> None:
    sp = info.shape._element.xpath("./p:spPr")
    if not sp:
        return
    sp = sp[0]
    fill_node = sp.find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")
    fill = _colour(fill_node)
    ln = sp.xpath("./a:ln")
    outline = None
    if ln and not ln[0].xpath("./a:noFill"):
        outline = _colour(ln[0].find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")) \
            or "#404040"
    elif not ln and info.kind == "shape" and not sp.xpath("./a:noFill"):
        outline = "#808080"
    if fill is None and outline is None:
        return
    if info.prst in _ARROW_POLYS:
        pts = [(box[0] + x * (box[2] - box[0]), box[1] + y * (box[3] - box[1]))
               for x, y in _ARROW_POLYS[info.prst]]
        draw.polygon(pts, fill=fill or "#1f77b4", outline=outline)
    elif info.prst == "ellipse":
        draw.ellipse(box, fill=fill, outline=outline, width=3)
    elif info.prst == "roundRect":
        draw.rounded_rectangle(box, radius=min(40, (box[3] - box[1]) / 4), fill=fill,
                               outline=outline, width=3)
    else:
        draw.rectangle(box, fill=fill, outline=outline, width=3)


def _font(size_px: int, bold: bool) -> ImageFont.ImageFont:
    names = ("arialbd.ttf", "DejaVuSans-Bold.ttf") if bold else ("arial.ttf", "DejaVuSans.ttf")
    for name in names:
        try:
            return ImageFont.truetype(name, max(8, size_px))
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_text(draw: ImageDraw.ImageDraw, info: ShapeInfo, box, scale: float) -> None:
    frame = info.shape.text_frame
    pad_x, pad_y = 91440 * scale, 45720 * scale
    x0, y0, x1 = box[0] + pad_x, box[1] + pad_y, max(box[0] + pad_x + 10, box[2] - pad_x)
    y = y0
    for p in frame.paragraphs:
        runs = [r for r in p.runs if r.text]
        text = "".join(r.text for r in runs).replace("\v", " ")
        sizes = [r.font.size.pt for r in runs if r.font.size is not None]
        size_px = int((max(sizes) if sizes else 18) * 12700 * scale)
        bold = bool(runs) and all(r.font.bold for r in runs)
        font = _font(size_px, bold)
        colour = "#000000"
        for r in runs:
            fill = r._r.xpath("./a:rPr/a:solidFill")
            c = _colour(fill[0]) if fill else None
            if c:
                colour = c
                break
        centred = p.alignment is not None and "CENTER" in str(p.alignment)
        for line in _wrap(draw, text, font, x1 - x0) or [""]:
            w = draw.textlength(line, font=font)
            x = x0 + ((x1 - x0) - w) / 2 if centred else x0
            draw.text((x, y), line, fill=colour, font=font)
            y += size_px * 1.2


def _wrap(draw, text: str, font, width: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        cand = f"{line} {word}".strip()
        if line and draw.textlength(cand, font=font) > width:
            lines.append(line)
            line = word
        else:
            line = cand
    if line:
        lines.append(line)
    return lines


def _draw_connector(draw: ImageDraw.ImageDraw, info: ShapeInfo, scale: float) -> None:
    (bx, by), (ex, ey) = info.begin, info.end
    bx, by, ex, ey = bx * scale, by * scale, ex * scale, ey * scale
    ln = info.shape._element.xpath("./p:spPr/a:ln")
    colour = "#404040"
    if ln:
        c = _colour(ln[0].find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill"))
        colour = c or colour
    if info.prst.startswith("bentConnector"):
        mx = (bx + ex) / 2
        pts = [(bx, by), (mx, by), (mx, ey), (ex, ey)]
    else:
        pts = [(bx, by), (ex, ey)]
    draw.line(pts, fill=colour, width=3)
    head = ln[0].xpath("./a:headEnd/@type") if ln else []
    tail = ln[0].xpath("./a:tailEnd/@type") if ln else []
    if tail and tail[0] != "none":
        _arrowhead(draw, pts[-2], pts[-1], colour)
    if head and head[0] != "none":
        _arrowhead(draw, pts[1], pts[0], colour)


def _arrowhead(draw, frm, to, colour: str, size: float = 18) -> None:
    angle = math.atan2(to[1] - frm[1], to[0] - frm[0])
    a = (to[0] - size * math.cos(angle - math.pi / 7), to[1] - size * math.sin(angle - math.pi / 7))
    b = (to[0] - size * math.cos(angle + math.pi / 7), to[1] - size * math.sin(angle + math.pi / 7))
    draw.polygon([to, a, b], fill=colour)
