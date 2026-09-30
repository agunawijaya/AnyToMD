"""PPTX shape flowchart → Mermaid, straight from the slide XML (no AI).

A diagram built from native shapes and connectors carries its graph exactly:

1. Every connector (`p:cxnSp` or a line) resolves its two ends to the shapes it
   is glued to (`a:stCxn` / `a:endCxn` ids), or else to the text-bearing
   shape whose box holds the end point. Text boxes count as nodes too — slide
   diagrams are often bordered text boxes, not auto-shapes.
2. The arrowheads (`a:headEnd` at the begin point, `a:tailEnd` at the end
   point) give the direction. A connector with no arrow information at all is
   read begin → end.
3. Block arrows (`downArrow`, `rightArrow`, …) link the nearest box behind
   them to the nearest box ahead.
4. A short text box near a connector's midpoint is that edge's label.

The result lists nodes, edges, the shapes it consumed and ready Mermaid; the
edges double as exact connector hints for slide vision. `None` when fewer than
two edges resolve — then the slide is not treated as a diagram.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .pptx_shapes import ShapeInfo, collect_shapes

# Preset geometry → Mermaid node brackets (label always quoted).
_NODE_BRACKETS = {
    "roundRect": ("([", "])"),
    "flowChartTerminator": ("([", "])"),
    "flowChartAlternateProcess": ("([", "])"),
    "diamond": ("{", "}"),
    "flowChartDecision": ("{", "}"),
    "ellipse": ("((", "))"),
    "flowChartConnector": ("((", "))"),
}
_BLOCK_ARROWS = {
    "rightArrow": (1, 0), "notchedRightArrow": (1, 0), "stripedRightArrow": (1, 0),
    "leftArrow": (-1, 0), "upArrow": (0, -1), "downArrow": (0, 1),
}
_END_TOL = 0.015        # connector end may stop this far (share of slide width) from a box
_LABEL_DIST = 0.08      # edge label centre within this share of slide width of the midpoint
_LABEL_MAX_CHARS = 24
_ARROW_REACH = 0.12     # block arrow → box gap, share of slide size


@dataclass
class FlowEdge:
    src: ShapeInfo
    dst: ShapeInfo
    label: str = ""
    directed: bool = True
    mid: tuple[float, float] | None = None  # connector / arrow midpoint, for labels


@dataclass
class Flow:
    nodes: list[ShapeInfo]
    edges: list[FlowEdge]
    consumed: set[str] = field(default_factory=set)  # sids of nodes, connectors, arrows, labels

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (min(n.left for n in self.nodes), min(n.top for n in self.nodes),
                max(n.right for n in self.nodes), max(n.bottom for n in self.nodes))

    def mermaid(self) -> str:
        horizontal = sum(abs(e.dst.cx - e.src.cx) > abs(e.dst.cy - e.src.cy) for e in self.edges)
        lines = [f"flowchart {'LR' if horizontal * 2 >= len(self.edges) else 'TD'}"]
        ids = {n.sid: f"n{i}" for i, n in enumerate(self.nodes, start=1)}
        for n in self.nodes:
            open_, close = _NODE_BRACKETS.get(n.prst, ("[", "]"))
            lines.append(f'    {ids[n.sid]}{open_}"{_label(n.text)}"{close}')
        for e in self.edges:
            arrow = "-->" if e.directed else "---"
            if e.label:
                arrow += f"|{_label(e.label).replace('|', ' ')}|"
            lines.append(f"    {ids[e.src.sid]} {arrow} {ids[e.dst.sid]}")
        return "\n".join(lines)

    def hint_lines(self) -> list[str]:
        """Edges in the CONNECTORS format page vision already knows."""
        out = []
        for e in self.edges:
            suffix = f' (label "{e.label}")' if e.label else ""
            if e.directed:
                out.append(f'- "{e.src.text}" → "{e.dst.text}"{suffix}')
            else:
                out.append(f'- "{e.src.text}" — "{e.dst.text}" (line, no arrowhead){suffix}')
        return out


def _label(text: str) -> str:
    return text.replace('"', "'").strip()


def extract_flow(infos: list[ShapeInfo], slide_w: float, slide_h: float) -> Flow | None:
    """The flowchart drawn on a slide, or None if fewer than two edges resolve."""
    edges = resolve_edges(infos, slide_w, slide_h)
    if len(edges) < 2:
        return None
    # Source order follows the flow: edges by where they start, nodes by
    # first appearance — the diagram's entry point comes first.
    horizontal = sum(abs(e.dst.cx - e.src.cx) > abs(e.dst.cy - e.src.cy) for e in edges) * 2 >= len(edges)
    edges.sort(key=lambda e: (e.src.left, e.src.top) if horizontal else (e.src.top, e.src.left))
    nodes: list[ShapeInfo] = []
    for e in edges:
        for n in (e.src, e.dst):
            if n not in nodes:
                nodes.append(n)
    node_ids = [n.sid for n in nodes]
    flow = Flow(nodes=nodes, edges=edges)
    flow.consumed = set(node_ids) | _consumed_extras(infos, edges, slide_w)
    return flow


def resolve_edges(infos: list[ShapeInfo], slide_w: float, slide_h: float) -> list[FlowEdge]:
    """Every connector and block arrow whose two ends land on different text shapes."""
    candidates = [i for i in infos if i.kind == "text" and i.role not in ("title", "number", "footer", "date")]
    by_sid = {i.sid: i for i in candidates}
    tol = _END_TOL * slide_w

    def _at(sid: list, point) -> ShapeInfo | None:
        if sid and sid[0] in by_sid:
            return by_sid[sid[0]]
        x, y = point
        hits = [c for c in candidates if c.contains(x, y, tol)]
        return min(hits, key=lambda c: c.area) if hits else None

    edges: list[FlowEdge] = []
    for c in (i for i in infos if i.kind == "connector" and i.begin and i.end):
        el = c.shape._element
        a = _at(el.xpath(".//a:stCxn/@id"), c.begin)
        b = _at(el.xpath(".//a:endCxn/@id"), c.end)
        if a is None or b is None or a is b:
            continue
        head = _arrow(el, "headEnd")   # at the begin point
        tail = _arrow(el, "tailEnd")   # at the end point
        if head is True and tail is not True:
            a, b = b, a
        directed = bool(head or tail) or (head is None and tail is None)
        mid = ((c.begin[0] + c.end[0]) / 2, (c.begin[1] + c.end[1]) / 2)
        edges.append(FlowEdge(a, b, directed=directed, mid=mid))

    for arrow in (i for i in infos if i.prst in _BLOCK_ARROWS):
        pair = _block_arrow_ends(arrow, candidates, slide_w, slide_h)
        if pair:
            edges.append(FlowEdge(pair[0], pair[1], label=arrow.text, mid=(arrow.cx, arrow.cy)))

    _attach_labels(edges, infos, candidates, slide_w)
    seen: set[tuple[str, str]] = set()
    unique = []
    for e in edges:
        key = (e.src.sid, e.dst.sid)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _arrow(el, tag: str) -> bool | None:
    """True/False for an explicit arrowhead or none at that end; None if the
    line carries no arrow information at all."""
    vals = el.xpath(f"./p:spPr/a:ln/a:{tag}/@type")
    if not vals:
        return None
    return str(vals[0]) != "none"


def _block_arrow_ends(arrow: ShapeInfo, boxes: list[ShapeInfo], slide_w: float,
                      slide_h: float) -> tuple[ShapeInfo, ShapeInfo] | None:
    dx, dy = _BLOCK_ARROWS[arrow.prst]
    reach = _ARROW_REACH * (slide_w if dx else slide_h)

    def _pick(sign: int) -> ShapeInfo | None:
        best, best_gap = None, reach
        for b in boxes:
            if b is arrow:
                continue
            if dx:  # horizontal arrow: box must overlap vertically
                if b.bottom < arrow.top or b.top > arrow.bottom:
                    continue
                gap = (b.left - arrow.right) if dx * sign > 0 else (arrow.left - b.right)
            else:
                if b.right < arrow.left or b.left > arrow.right:
                    continue
                gap = (b.top - arrow.bottom) if dy * sign > 0 else (arrow.top - b.bottom)
            if -reach * 0.25 <= gap < best_gap:
                best, best_gap = b, gap
        return best

    ahead, behind = _pick(1), _pick(-1)
    if ahead is None or behind is None or ahead is behind:
        return None
    return behind, ahead


def _attach_labels(edges: list[FlowEdge], infos: list[ShapeInfo], nodes: list[ShapeInfo],
                   slide_w: float) -> None:
    """Each short free text box goes to the edge whose midpoint is nearest."""
    used = {e.src.sid for e in edges} | {e.dst.sid for e in edges}
    for t in infos:
        if t.kind != "text" or t.sid in used or not (0 < len(t.text) <= _LABEL_MAX_CHARS):
            continue
        if t.role in ("title", "number", "footer", "date"):
            continue
        best, best_d = None, _LABEL_DIST * slide_w
        for e in edges:
            if e.label:
                continue
            mx, my = e.mid or ((e.src.cx + e.dst.cx) / 2, (e.src.cy + e.dst.cy) / 2)
            d = ((t.cx - mx) ** 2 + (t.cy - my) ** 2) ** 0.5
            if d < best_d:
                best, best_d = e, d
        if best is not None:
            best.label = t.text


def _consumed_extras(infos: list[ShapeInfo], edges: list[FlowEdge], slide_w: float) -> set[str]:
    labels = {e.label for e in edges if e.label}
    out: set[str] = set()
    for i in infos:
        if i.kind == "connector" or i.prst in _BLOCK_ARROWS:
            out.add(i.sid)
        elif i.kind == "text" and i.text in labels and len(i.text) <= _LABEL_MAX_CHARS:
            out.add(i.sid)
    return out


def try_extract_mermaid(slide, shapes=None) -> str | None:
    """Mermaid for a slide that is a shape diagram, or None. (Kept for callers
    of the pre-2026-09-30 API; `shapes` is ignored.)"""
    prs_part = slide.part.package.presentation_part.presentation
    flow = extract_flow(collect_shapes(slide), prs_part.slide_width, prs_part.slide_height)
    return flow.mermaid() if flow else None
