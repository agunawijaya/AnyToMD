"""E1: Direct PPTX shape → Mermaid extraction (bypass VLM).

For slides where the diagram is built from native PowerPoint shapes +
connectors (not a bitmap), we can reconstruct the flow graph deterministically:

1. Enumerate auto-shapes → nodes. Text label = shape.text_frame.text.
2. Enumerate LINE connectors → edges. Endpoint (x, y) matches the shape whose
   bbox contains it → source. Same for the other endpoint → target.
3. Nearby textboxes (small standalone rectangles with short text) sit close to
   connector midpoints → treat as edge labels ("Yes", "No", …).
4. Emit `flowchart LR` (if avg edge horizontal) or `flowchart TD` (vertical).

Node shape mapping (Mermaid syntax):
- Rounded rectangle  → `([label])`
- Rectangle          → `[label]`
- Diamond / decision → `{label}`
- Oval               → `((label))`
- Others             → `[label]`

Return `None` if the structure can't be recovered cleanly (missing shapes for
connector endpoints, degenerate layout). Caller then falls back to raster+VLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE

def _shape_style_map() -> dict:
    """Build the auto-shape → mermaid-node-open map, tolerant to enum members
    that may not exist across python-pptx versions."""
    result = {}
    _pairs = [
        ("RECTANGLE",           "["),
        ("ROUNDED_RECTANGLE",   "(["),
        ("DIAMOND",             "{"),
        ("FLOWCHART_DECISION",  "{"),
        ("OVAL",                "(("),
        ("FLOWCHART_CONNECTOR", "(("),
        ("FLOWCHART_TERMINATOR", "(["),
    ]
    for name, open_ in _pairs:
        member = getattr(MSO_SHAPE, name, None)
        if member is not None:
            result[member] = open_
    return result


_NODE_SHAPE_STYLE = _shape_style_map()

_CLOSE = {"[": "]", "([": "])", "{": "}", "((": "))"}

# Bounding-box match tolerance for connector endpoints (EMU)
_MATCH_TOL = 91440  # ≈ 0.1 inch
# Textbox-as-label proximity to connector midpoint (EMU)
_LABEL_PROX = 685800  # ≈ 0.75 inch
# Max label length to still count as an edge label (avoid grabbing paragraphs)
_MAX_LABEL_LEN = 24


@dataclass
class _Node:
    idx: int
    label: str
    style_open: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    def contains(self, x: float, y: float, tol: int = _MATCH_TOL) -> bool:
        return (self.left - tol <= x <= self.right + tol
                and self.top - tol <= y <= self.bottom + tol)


@dataclass
class _Edge:
    src: int
    dst: int
    label: str = ""


def try_extract_mermaid(slide, shapes) -> str | None:
    """Return Mermaid source, or None if extraction fails cleanly."""
    nodes: list[_Node] = []
    connectors: list = []
    text_labels: list[tuple[int, int, str]] = []  # (cx, cy, text)

    for shape in shapes:
        st = shape.shape_type
        if st == MSO_SHAPE_TYPE.AUTO_SHAPE:
            try:
                auto_type = shape.auto_shape_type
            except (AttributeError, ValueError):
                auto_type = None
            style_open = _NODE_SHAPE_STYLE.get(auto_type, "[")
            label = _clean_label(_shape_text(shape))
            if not label:
                continue
            left, top = int(shape.left or 0), int(shape.top or 0)
            width, height = int(shape.width or 0), int(shape.height or 0)
            nodes.append(_Node(
                idx=len(nodes),
                label=label,
                style_open=style_open,
                left=left, top=top,
                right=left + width, bottom=top + height,
            ))
        elif st == MSO_SHAPE_TYPE.LINE:
            connectors.append(shape)
        elif st == MSO_SHAPE_TYPE.TEXT_BOX:
            label_text = _clean_label(_shape_text(shape))
            if 0 < len(label_text) <= _MAX_LABEL_LEN:
                left, top = int(shape.left or 0), int(shape.top or 0)
                width, height = int(shape.width or 0), int(shape.height or 0)
                text_labels.append((left + width // 2, top + height // 2, label_text))

    if len(nodes) < 2 or not connectors:
        return None

    edges: list[_Edge] = []
    horizontal_edges = 0
    for c in connectors:
        try:
            bx, by, ex, ey = _connector_endpoints(c)
        except Exception:
            continue
        src = _nearest_node(nodes, bx, by)
        dst = _nearest_node(nodes, ex, ey)
        if src is None or dst is None or src.idx == dst.idx:
            continue
        # Edge label from nearby textbox (near midpoint)
        mx, my = (bx + ex) // 2, (by + ey) // 2
        label = _find_label_near(text_labels, mx, my)
        edges.append(_Edge(src=src.idx, dst=dst.idx, label=label))
        if abs(ex - bx) > abs(ey - by):
            horizontal_edges += 1

    if not edges:
        return None
    # Require at least half of nodes to be connected — otherwise structure is
    # too sparse to be trusted as a graph.
    connected: set[int] = set()
    for e in edges:
        connected.add(e.src)
        connected.add(e.dst)
    if len(connected) / len(nodes) < 0.5:
        return None

    direction = "LR" if horizontal_edges >= len(edges) / 2 else "TD"
    lines = [f"flowchart {direction}"]
    for n in nodes:
        if n.idx not in connected:
            continue  # skip orphans (decorative shapes without edges)
        open_ = n.style_open
        close_ = _CLOSE[open_]
        lines.append(f"    N{n.idx}{open_}{_escape_label(n.label)}{close_}")
    for e in edges:
        arrow = f"-->|{_escape_label(e.label)}|" if e.label else "-->"
        lines.append(f"    N{e.src} {arrow} N{e.dst}")
    return "\n".join(lines)


def _shape_text(shape) -> str:
    try:
        return shape.text_frame.text or ""
    except Exception:
        return ""


def _clean_label(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _escape_label(text: str) -> str:
    # Mermaid: strip pipes (they'd break edge-label syntax) and brackets that
    # confuse the parser. Preserve the essential content.
    return (text.replace("|", " ").replace("[", "(").replace("]", ")").replace('"', "'"))


def _connector_endpoints(shape) -> tuple[int, int, int, int]:
    """Return (begin_x, begin_y, end_x, end_y) in EMU for a LINE/connector."""
    # python-pptx exposes .begin_x, .begin_y, .end_x, .end_y on connectors
    return (int(shape.begin_x), int(shape.begin_y),
            int(shape.end_x), int(shape.end_y))


def _nearest_node(nodes: list[_Node], x: float, y: float) -> _Node | None:
    """Find the node whose bbox contains (x, y) with tolerance, or the closest
    if none contain it. Returns None if closest is > 2× tolerance away."""
    containing = [n for n in nodes if n.contains(x, y)]
    if containing:
        # Pick the smallest bbox (tightest match) if multiple contain
        return min(containing, key=lambda n: (n.right - n.left) * (n.bottom - n.top))
    # No container — find closest center within reasonable range
    best = None
    best_dist = float("inf")
    for n in nodes:
        dx, dy = n.cx - x, n.cy - y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = n
    if best is None or best_dist > _MATCH_TOL * 3:
        return None
    return best


def _find_label_near(labels: list[tuple[int, int, str]], mx: int, my: int) -> str:
    best = ""
    best_dist = _LABEL_PROX
    for lx, ly, text in labels:
        dx, dy = lx - mx, ly - my
        dist = (dx * dx + dy * dy) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = text
    return best
