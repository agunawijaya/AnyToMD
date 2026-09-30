"""Deterministic slide layout → Markdown pieces (no AI).

- the slide title: the title placeholder, or else the largest-font text near
  the top (decks often use plain text boxes named "Title 7");
- page-number boxes ("Hal. 3", "Page 2 of 9") and slide-number/footer/date
  placeholders, which are dropped;
- paragraphs with their real list structure: `buAutoNum` → numbered, `buChar`
  → bullet, `buNone` → paragraph, hand-typed "2.  " → numbered; depth from the
  marker position (marL + first-line indent); an indented paragraph right
  after a list item continues that item;
- reading order by recursive XY-cut, so two-column slides read column by
  column instead of line by line across both;
- text boxes drawn on top of a picture (a correction patch over a word) —
  they belong to the picture, not to the text flow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pptx_shapes import ShapeInfo, clean_text

_PAGE_NUMBER_RE = re.compile(
    r"^(?:(?:hal(?:aman)?|hlm|page|pg|slide|p)\.?\s*)?\d{1,4}(?:\s*(?:/|of|dari)\s*\d{1,4})?$",
    re.IGNORECASE,
)
_MANUAL_NUMBER_RE = re.compile(r"^(\d{1,2})[.)]\s+(.+)$")
_MANUAL_BULLET_RE = re.compile(r"^[•▪■◦‣●○–-]\s+(.+)$")
_DEFAULT_LEVEL_INDENT = 457200   # 0.5 inch per outline level when marL is inherited
_INDENT_BUCKET = 60000           # marker positions closer than this are one depth
_TITLE_MIN_PT = 18


@dataclass
class Block:
    """One piece of slide content in reading order."""
    kind: str                     # text | table | chart | picture | diagram
    left: float
    top: float
    right: float
    bottom: float
    markdown: str = ""
    info: ShapeInfo | None = None
    overlays: list[ShapeInfo] = field(default_factory=list)  # pictures only


# ---------- title and page numbers ----------

def is_page_number(info: ShapeInfo, slide_h: float) -> bool:
    if info.role in ("number", "footer", "date"):
        return True
    if info.kind != "text" or not _PAGE_NUMBER_RE.match(info.text):
        return False
    return info.top >= 0.8 * slide_h or info.bottom <= 0.15 * slide_h


def find_title(infos: list[ShapeInfo], slide_h: float) -> ShapeInfo | None:
    texts = [i for i in infos if i.kind == "text" and not is_page_number(i, slide_h)]
    for i in texts:
        if i.role == "title":
            return i
    top = [i for i in texts if i.top < 0.25 * slide_h and len(i.text) <= 200]
    if not top:
        return None
    best = max(top, key=lambda i: (max_font_pt(i.shape), -i.top))
    size = max_font_pt(best.shape)
    if size < _TITLE_MIN_PT or size < max(max_font_pt(i.shape) for i in texts):
        return None
    return best


def max_font_pt(shape) -> float:
    try:
        sizes = [r.font.size.pt for p in shape.text_frame.paragraphs for r in p.runs
                 if r.font.size is not None and r.text.strip()]
    except Exception:
        return 0.0
    return max(sizes) if sizes else 0.0


# ---------- overlays ----------

def find_overlays(infos: list[ShapeInfo]) -> dict[str, list[ShapeInfo]]:
    """{picture sid: text shapes drawn on top of it (≥80% inside)}."""
    pictures = [i for i in infos if i.kind == "picture"]
    out: dict[str, list[ShapeInfo]] = {}
    for t in infos:
        if t.kind != "text" or t.role:
            continue
        under = [p for p in pictures if p.z < t.z and t.overlap_share(p) >= 0.8]
        if under:
            top_pic = max(under, key=lambda p: p.z)
            out.setdefault(top_pic.sid, []).append(t)
    return out


# ---------- paragraphs ----------

@dataclass
class _Para:
    text: str
    kind: str          # number | bullet | plain | blank
    scheme: str = ""   # buAutoNum type
    start: int = 1
    marker: int = 0    # EMU position of the bullet/number
    content: int = 0   # EMU position of the text
    leading_ws: int = 0
    bold: bool = False
    number: int = 0    # hand-typed number


def frame_markdown(shape, default_bullets: bool = False) -> str:
    """Markdown for one text frame, keeping its list structure."""
    try:
        paragraphs = list(shape.text_frame.paragraphs)
    except Exception:
        return ""
    paras = [_para(p, default_bullets) for p in paragraphs]
    list_markers = sorted({p.marker for p in paras if p.kind in ("number", "bullet")})
    buckets: list[int] = []
    for m in list_markers:
        if not buckets or m - buckets[-1] > _INDENT_BUCKET:
            buckets.append(m)

    def _depth(pos: int) -> int:
        d = 0
        for i, b in enumerate(buckets):
            if pos >= b - _INDENT_BUCKET:
                d = i
        return d

    lines: list[str] = []
    counters: dict[int, int] = {}
    prev: str = "start"            # start | list | plain | blank
    last_item: tuple[int, int] | None = None   # (content indent in chars, marker EMU)
    for p in paras:
        if p.kind == "blank":
            if prev != "start":
                prev = "blank"
            last_item = None if prev == "blank" else last_item
            continue
        is_continuation = (
            p.kind == "plain" and last_item is not None and prev == "list"
            and (p.leading_ws >= 2 or p.content > last_item[1] + _INDENT_BUCKET // 2)
        )
        if is_continuation:
            lines.append(" " * last_item[0] + p.text)
            continue
        if p.kind in ("number", "bullet"):
            depth = _depth(p.marker)
            for d in [d for d in counters if d > depth]:
                del counters[d]
            indent = "    " * depth
            if p.kind == "number":
                n = p.number or (counters.get(depth, p.start - 1) + 1)
                counters[depth] = n
                marker = _number_label(n, p.scheme)
            else:
                marker = "-"
            if prev in ("plain", "blank") and lines:
                lines.append("")
            lines.append(f"{indent}{marker} {p.text}")
            last_item = (len(indent) + len(marker) + 1, p.marker)
            prev = "list"
        else:
            if lines:
                lines.append("")
            lines.append(f"**{p.text}**" if p.bold else p.text)
            last_item = None
            prev = "plain"
    return "\n".join(lines).strip("\n")


def _para(p, default_bullets: bool) -> _Para:
    raw = p.text or ""
    text = clean_text(raw)
    if not text:
        return _Para(text="", kind="blank")
    ppr = p._p.pPr
    bullet = None
    mar_l = indent = None
    if ppr is not None:
        for child in ppr:
            name = child.tag.rsplit("}", 1)[-1]
            if name in ("buNone", "buChar", "buAutoNum", "buBlip"):
                bullet = (name, child)
        mar_l = ppr.get("marL")
        indent = ppr.get("indent")
    content = int(mar_l) if mar_l is not None else p.level * _DEFAULT_LEVEL_INDENT
    marker = content + (int(indent) if indent is not None else 0)
    leading_ws = len(raw.replace("\v", " ")) - len(raw.replace("\v", " ").lstrip())
    runs = [r for r in p.runs if r.text.strip()]
    bold = bool(runs) and all(r.font.bold is True for r in runs)
    para = _Para(text=text, kind="plain", marker=marker, content=content,
                 leading_ws=leading_ws, bold=bold)
    if bullet is None:
        para.kind = "bullet" if default_bullets else "plain"
    elif bullet[0] == "buAutoNum":
        para.kind = "number"
        para.scheme = bullet[1].get("type", "arabicPeriod")
        para.start = int(bullet[1].get("startAt", 1))
    elif bullet[0] in ("buChar", "buBlip"):
        para.kind = "bullet"
    if para.kind == "plain":
        m = _MANUAL_NUMBER_RE.match(text)
        if m:
            para.kind, para.number, para.text = "number", int(m.group(1)), m.group(2)
        else:
            mb = _MANUAL_BULLET_RE.match(text)
            if mb:
                para.kind, para.text = "bullet", mb.group(1)
    if para.kind != "plain":
        para.bold = False
    return para


def _number_label(n: int, scheme: str) -> str:
    """Markdown ordered lists only take digits; other schemes become text."""
    if scheme.startswith("alphaLc"):
        label = _alpha(n).lower()
    elif scheme.startswith("alphaUc"):
        label = _alpha(n)
    elif scheme.startswith("romanLc"):
        label = _roman(n).lower()
    elif scheme.startswith("romanUc"):
        label = _roman(n)
    else:
        return f"{n}."
    return f"- {label}."


def _alpha(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _roman(n: int) -> str:
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out, n = out + s, n - v
    return out


def table_markdown(shape) -> str:
    rows = [[clean_text(c.text).replace("|", "\\|") for c in row.cells] for row in shape.table.rows]
    if not rows:
        return ""
    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


# ---------- reading order ----------

def reading_order(blocks: list[Block], slide_w: float, slide_h: float) -> list[Block]:
    """Recursive XY-cut: split at the widest clear gap (columns or rows),
    recurse into each part; top-to-bottom, left-to-right inside a part."""
    tol_x, tol_y = 0.004 * slide_w, 0.004 * slide_h
    return _xy_cut(blocks, tol_x, tol_y)


def _xy_cut(blocks: list[Block], tol_x: float, tol_y: float) -> list[Block]:
    if len(blocks) <= 1:
        return list(blocks)
    best = None
    for axis, tol in (("y", tol_y), ("x", tol_x)):
        groups, gap = _split(blocks, axis, tol)
        if len(groups) > 1 and (best is None or gap > best[1]):
            best = (groups, gap)
    if best is None:
        return sorted(blocks, key=lambda b: (b.top, b.left))
    return [b for g in best[0] for b in _xy_cut(g, tol_x, tol_y)]


def _split(blocks: list[Block], axis: str, tol: float) -> tuple[list[list[Block]], float]:
    lo = (lambda b: b.left) if axis == "x" else (lambda b: b.top)
    hi = (lambda b: b.right) if axis == "x" else (lambda b: b.bottom)
    ordered = sorted(blocks, key=lo)
    groups = [[ordered[0]]]
    end = hi(ordered[0])
    widest = float("-inf")
    for b in ordered[1:]:
        if lo(b) >= end - tol:
            widest = max(widest, lo(b) - end)
            groups.append([b])
        else:
            groups[-1].append(b)
        end = max(end, hi(b))
    return groups, widest
