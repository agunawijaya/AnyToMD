"""Exact hints from a PDF page's vector drawing, for page vision.

A vision model reading a rendered slide is unreliable on fine detail: which
of two purples a border is, whether it is dashed, which end of a thin
connector carries the arrowhead. The PDF states all of that exactly, so we
read it here and hand it to the model next to the text layer:

- when the page has a legend (small swatches with a label), every text block
  inside a box drawn in a legend colour is tagged with that entry
  (`{legend: Phase 2}`) — matched here, exactly, instead of by eye;
- other boxes are tagged with a style id (`{box S2}`), and those styles are
  listed once (`S2: border #753dac solid, fill #f1e8f7`);
- connectors are listed tail → head, with their ends resolved to the text
  of the box they touch.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import fitz  # PyMuPDF

# Shapes bigger than this share of the page are backgrounds, not boxes.
_MAX_BOX_PAGE_SHARE = 0.5
# Legend swatch: a small shape with a label just to its right. Bullet
# squares are smaller than _SWATCH_MIN.
_SWATCH_MIN, _SWATCH_MAX_W, _SWATCH_MAX_H, _SWATCH_GAP = 9.0, 40.0, 30.0, 25.0
_LEGEND_WORD_GAP = 12.0  # a wider gap ends the label
# Arrowhead: a small filled shape touching one end of a connector.
_HEAD_MAX_SIZE, _HEAD_MAX_DIST = 14.0, 8.0
_MIN_CONNECTOR_LEN = 6.0  # short arrows bridge the gap between adjacent boxes
_END_TOLERANCE = 8.0  # connector ends sit on (or an arrowhead short of) box borders
# Badges and swatches on a connector are not what it connects.
_MIN_END_BOX = 30.0
# Connector label: short text whose centre is this close to the line.
_LABEL_MAX_DIST, _LABEL_MAX_CHARS = 18.0, 24

STYLES_HEADER = "BOX STYLES (exact, from the PDF drawing):"
ARROWS_HEADER = "CONNECTORS (exact, from the PDF drawing; tail → head):"


@dataclass
class TextBlock:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.y0 + self.y1) / 2

    @property
    def label(self) -> str:
        return self.text.strip().splitlines()[0].strip() if self.text.strip() else ""


@dataclass
class _Box:
    rect: fitz.Rect
    style: tuple[str | None, str | None, bool]  # (border, fill, dashed)

    @property
    def area(self) -> float:
        return self.rect.width * self.rect.height


@dataclass
class VectorHints:
    tags: list[str]       # per text block: "", " {legend: <label>}" or " {box S1}"
    appendix: str         # BOX STYLES + CONNECTORS sections, or ""


def _hex(color) -> str | None:
    if not color:
        return None
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c * 255))) for c in color[:3])


def _is_dashed(drawing: dict) -> bool:
    dashes = drawing.get("dashes") or ""
    return "[" in dashes and dashes.split("]")[0].strip("[ ") != ""


def _dist_to_segment(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    if dx == dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + t * dx, ay + t * dy))


def _path_points(items: list) -> list[fitz.Point]:
    pts: list[fitz.Point] = []
    for it in items:
        if it[0] == "l":
            pts.extend([it[1], it[2]])
        elif it[0] == "c":
            pts.extend([it[1], it[4]])
    return pts


def _find_legend(page: fitz.Page, boxes: list[_Box],
                 holders: set[int]) -> list[tuple[tuple, str]]:
    """(swatch style, label) for each legend entry on the page.

    A swatch is a small shape with no text inside and a label starting just
    to its right. Labels are read word by word, because several entries on
    one row usually end up in one text block. A real legend has at least two
    entries in different styles — a row of identical small squares is a
    bullet list, not a legend.
    """
    try:
        words = page.get_text("words")
    except Exception:
        return []
    candidates = [b for b in boxes
                  if id(b) not in holders
                  and _SWATCH_MIN <= b.rect.width <= _SWATCH_MAX_W
                  and _SWATCH_MIN <= b.rect.height <= _SWATCH_MAX_H
                  and not any(b.rect.contains(fitz.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
                              for w in words)]
    legend: list[tuple[tuple, str]] = []
    for sw in candidates:
        cy = (sw.rect.y0 + sw.rect.y1) / 2
        row = sorted((w for w in words
                      if abs((w[1] + w[3]) / 2 - cy) <= 8 and w[0] >= sw.rect.x1 - 1),
                     key=lambda w: w[0])
        if not row or row[0][0] - sw.rect.x1 > _SWATCH_GAP:
            continue
        label_words = [row[0]]
        for w in row[1:]:
            gap_start = label_words[-1][2]
            if w[0] - gap_start > _LEGEND_WORD_GAP or any(
                    gap_start <= other.rect.x0 <= w[0] for other in candidates if other is not sw):
                break
            label_words.append(w)
        legend.append((sw.style, " ".join(w[4] for w in label_words)))
    if len({style for style, _ in legend}) < 2:
        return []
    return legend


def page_vector_hints(page: fitz.Page, blocks: list[TextBlock]) -> VectorHints:
    try:
        drawings = page.get_drawings()
    except Exception:
        return VectorHints(tags=[""] * len(blocks), appendix="")
    page_area = max(page.rect.width * page.rect.height, 1.0)

    boxes: list[_Box] = []
    connectors: list[list[fitz.Point]] = []
    heads: list[fitz.Rect] = []
    for d in drawings:
        rect = fitz.Rect(d["rect"])
        items = d.get("items") or []
        kinds = {it[0] for it in items}
        fill, border = _hex(d.get("fill")), _hex(d.get("color"))
        if d.get("type") == "s" and kinds <= {"l", "c"} and not d.get("closePath"):
            pts = _path_points(items)
            if pts and math.dist(pts[0], pts[-1]) >= _MIN_CONNECTOR_LEN:
                connectors.append([pts[0], pts[-1]])
            continue
        if fill and not border and rect.width <= _HEAD_MAX_SIZE and rect.height <= _HEAD_MAX_SIZE:
            heads.append(rect)
            continue
        if rect.width * rect.height / page_area > _MAX_BOX_PAGE_SHARE:
            continue
        if fill in (None, "#ffffff") and border is None:
            continue
        if rect.width < 4 or rect.height < 4:
            continue
        boxes.append(_Box(rect=rect, style=(border, fill, _is_dashed(d) if border else False)))

    def _inside(pt, rect: fitz.Rect, tol: float = 0.0) -> bool:
        return (rect.x0 - tol <= pt[0] <= rect.x1 + tol
                and rect.y0 - tol <= pt[1] <= rect.y1 + tol)

    def _smallest_box(pt, tol: float = 0.0, min_side: float = 0.0) -> _Box | None:
        hits = [b for b in boxes if _inside(pt, b.rect, tol)
                and min(b.rect.width, b.rect.height) >= min_side]
        return min(hits, key=lambda b: b.area) if hits else None

    style_ids: dict[tuple, str] = {}

    def _style_id(style: tuple) -> str:
        if style not in style_ids:
            style_ids[style] = f"S{len(style_ids) + 1}"
        return style_ids[style]

    # Which boxes hold text — used to tell swatches (no text) from boxes.
    holders: set[int] = set()
    block_box: list[_Box | None] = []
    for blk in blocks:
        box = _smallest_box(blk.center)
        block_box.append(box)
        if box is not None:
            holders.add(id(box))

    legend = _find_legend(page, boxes, holders)

    def _legend_for(style: tuple) -> str | None:
        for sw_style, label in legend:
            if sw_style == style:
                return label
        border, fill, dashed = style
        for (sw_border, sw_fill, sw_dashed), label in legend:
            if border and sw_border and dashed != sw_dashed:
                continue
            # A solid swatch stands for boxes drawn in its colour, border or fill.
            colour = sw_fill if sw_fill and sw_fill != "#ffffff" else sw_border
            if colour and colour in (border, fill):
                return label
        return None

    tags: list[str] = []
    for box in block_box:
        if box is None:
            tags.append("")
            continue
        label = _legend_for(box.style) if legend else None
        tags.append(f" {{legend: {label}}}" if label else f" {{box {_style_id(box.style)}}}")

    def _box_label(box: _Box) -> str:
        inside = [b for b in blocks if _inside(b.center, box.rect)]
        if not inside:
            return ""
        top = min(inside, key=lambda b: (round(b.y0), b.x0))
        return top.label

    def _end_label(pt) -> str:
        box = _smallest_box(pt, _END_TOLERANCE, _MIN_END_BOX)
        if box is not None:
            label = _box_label(box)
            if label:
                return label
        near = [b for b in blocks
                if math.dist(pt, b.center) <= 40 or _inside(pt, fitz.Rect(b.x0, b.y0, b.x1, b.y1), 6)]
        if near:
            return min(near, key=lambda b: math.dist(pt, b.center)).label
        return f"point ({pt[0] / page.rect.width:.2f}, {pt[1] / page.rect.height:.2f})"

    def _on_connector_label(a, b, end_boxes: list[_Box | None]) -> str:
        """Short text sitting on the connector (a badge like "I1", "Yes")."""
        best, best_d = "", _LABEL_MAX_DIST
        for blk in blocks:
            text = " ".join(blk.text.split())
            if not text or len(text) > _LABEL_MAX_CHARS:
                continue
            if any(box is not None and _inside(blk.center, box.rect) for box in end_boxes):
                continue  # text of a box the connector ends at
            d = _dist_to_segment(blk.center, a, b)
            if d < best_d:
                best, best_d = text, d
        return best

    arrows: list[str] = []
    for a, b in connectors:
        def _head_at(pt) -> bool:
            return any(math.dist(pt, ((h.x0 + h.x1) / 2, (h.y0 + h.y1) / 2)) <= _HEAD_MAX_DIST
                       for h in heads)
        head_a, head_b = _head_at(a), _head_at(b)
        la, lb = _end_label(a), _end_label(b)
        if la == lb:
            continue  # rule inside one box (table ruling, underline)
        label = _on_connector_label(a, b, [_smallest_box(a, _END_TOLERANCE, _MIN_END_BOX),
                                           _smallest_box(b, _END_TOLERANCE, _MIN_END_BOX)])
        suffix = f' (label "{label}")' if label else ""
        if head_b and not head_a:
            arrows.append(f'- "{la}" → "{lb}"{suffix}')
        elif head_a and not head_b:
            arrows.append(f'- "{lb}" → "{la}"{suffix}')
        elif head_a and head_b:
            arrows.append(f'- "{la}" ↔ "{lb}"{suffix}')
        else:
            arrows.append(f'- "{la}" — "{lb}" (line, no arrowhead){suffix}')

    sections: list[str] = []
    if style_ids:
        lines = [STYLES_HEADER]
        for style, sid in style_ids.items():
            border, fill, dashed = style
            parts = []
            if border:
                parts.append(f"border {border} {'dashed' if dashed else 'solid'}")
            if fill:
                parts.append(f"fill {fill}")
            lines.append(f"{sid}: {', '.join(parts) or 'no border, no fill'}")
        sections.append("\n".join(lines))
    if arrows:
        sections.append("\n".join([ARROWS_HEADER] + list(dict.fromkeys(arrows))))
    return VectorHints(tags=tags, appendix="\n\n".join(sections))
