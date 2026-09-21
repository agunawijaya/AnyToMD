from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.slide import Slide
from pptx.util import Emu

from ..ai_client import AIClient
from ..slide_render import render_slide_shapes
from .pptx_shape_mermaid import try_extract_mermaid

ProgressFn = Callable[[str], None]

# Alt text below this length is treated as auto-generated / non-informative
# ("Picture 3", "image1", empty). Above → trust the author.
_ALT_TEXT_MIN_LEN = 12
# How much slide body text we hand to the AI as extra_context (prevents
# extra_context from ballooning while still giving useful hints).
_CONTEXT_TEXT_MAX = 300


def convert_pptx(path: Path, ai: AIClient, progress: ProgressFn | None = None) -> str:
    prs = Presentation(str(path))
    slide_w = prs.slide_width
    slide_h = prs.slide_height
    out: list[str] = [f"# {path.stem}", ""]

    total = len(prs.slides)
    for slide_idx, slide in enumerate(prs.slides, start=1):
        _emit(progress, f"Slide {slide_idx}/{total}")
        out.append(f"## Slide {slide_idx}")
        title = _slide_title(slide)
        if title:
            out.append(f"**{title}**")
            out.append("")

        layout_name = slide.slide_layout.name if slide.slide_layout else "(unknown)"
        shapes = _visible_shapes(slide)
        pictures = [s for s in shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
        charts = [s for s in shapes if getattr(s, "has_chart", False)]
        is_shape_diagram = (
            _looks_like_shape_diagram(shapes) and not pictures and not charts
        )

        # Collect body text early — needed both for the emit path AND as
        # extra_context for AI image calls (so the model sees the surrounding
        # copy, not just the pixels).
        text_blocks = _extract_text_blocks(shapes)
        context_text = _summarize_context(title, text_blocks)

        if is_shape_diagram:
            # Text inside diagram shapes would double up with the AI's description.
            # Skip text/table extraction; render + AI (or direct Mermaid) is
            # the source of truth.
            out.append(f"> **Layout:** '{layout_name}' — shape diagram")
            out.append("")
            # E1: Try direct XML → Mermaid extraction first. Zero AI cost,
            # 100% correct labels when it succeeds. Falls back to raster+VLM
            # if the graph structure isn't cleanly recoverable.
            direct_mermaid = try_extract_mermaid(slide, shapes)
            if direct_mermaid:
                _emit(progress, f"Slide {slide_idx}: Mermaid direct dari XML shape")
                out.append(
                    "> **[Shape diagram — Mermaid diekstrak langsung dari struktur shape]**"
                )
                out.append("")
                out.append("```mermaid")
                out.append(direct_mermaid)
                out.append("```")
                out.append("")
                out.append("---")
                out.append("")
                continue
            _emit(progress, f"Slide {slide_idx}: render shape diagram")
            try:
                png = render_slide_shapes(slide, slide_w, slide_h)
                analysis = ai.analyze_image(
                    png,
                    media_type="image/png",
                    extra_context=(
                        f"Slide {slide_idx} sebuah PPTX. Diagram digambar pakai shape + connector "
                        f"PPTX asli (bukan bitmap). Render ini rasterisasi shape-nya — hanya bentuk "
                        f"& label yang penting, abaikan warna/font.\n{context_text}"
                    ).strip(),
                )
            except Exception as e:
                out.append(f"> *[Gagal render/analisa shape-diagram slide {slide_idx}: {e}]*")
                out.append("")
            else:
                out.append(analysis.to_markdown_block(alt_hint=f"slide{slide_idx}_shapes"))
                out.append("")
        else:
            # Normal slide: emit text, tables, layout, then analyze each picture.
            for block in text_blocks:
                out.append(block)
                out.append("")

            for tbl_md in _extract_tables(shapes):
                out.append(tbl_md)
                out.append("")

            for chart_md in _extract_charts(charts):
                out.append(chart_md)
                out.append("")

            out.append(f"> **Layout:** {_describe_layout(shapes, layout_name)}")
            out.append("")

            for pic_idx, pic in enumerate(pictures, start=1):
                _emit(progress, f"Slide {slide_idx}: gambar {pic_idx}/{len(pictures)}")
                alt = _picture_alt_text(pic)
                if len(alt) >= _ALT_TEXT_MIN_LEN:
                    out.append(_alt_text_block(alt, slide_idx, pic_idx))
                    out.append("")
                    continue
                try:
                    blob = pic.image.blob
                    content_type = pic.image.content_type or "image/png"
                except Exception as e:
                    out.append(f"> *[Gagal mengambil gambar {pic_idx}: {e}]*")
                    out.append("")
                    continue
                try:
                    analysis = ai.analyze_image(
                        blob,
                        media_type=content_type,
                        extra_context=(
                            f"Gambar ini dari slide {slide_idx} sebuah PPTX. "
                            f"Layout: {layout_name}.\n{context_text}"
                        ).strip(),
                    )
                except Exception as e:
                    out.append(f"> *[Gagal menganalisis gambar {pic_idx}: {e}]*")
                    out.append("")
                    continue
                out.append(analysis.to_markdown_block(alt_hint=f"slide{slide_idx}_img{pic_idx}"))
                out.append("")

        notes = _speaker_notes(slide)
        if notes:
            out.append("### Notes")
            out.append(notes)
            out.append("")

        out.append("---")
        out.append("")

    return "\n".join(out).rstrip() + "\n"


def _slide_title(slide: Slide) -> str:
    try:
        if slide.shapes.title and slide.shapes.title.has_text_frame:
            return slide.shapes.title.text_frame.text.strip()
    except Exception:
        pass
    return ""


def _visible_shapes(slide: Slide):
    shapes = []
    for shape in _iter_shapes(slide.shapes):
        # Skip slide number and footer placeholders
        try:
            if shape.is_placeholder:
                ph = shape.placeholder_format
                if ph and ph.idx in {15, 16}:  # slide number, footer
                    continue
        except Exception:
            pass
        shapes.append(shape)
    return shapes


def _iter_shapes(shapes):
    for s in shapes:
        if s.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _iter_shapes(s.shapes)
        else:
            yield s


def _extract_text_blocks(shapes) -> list[str]:
    # Sort by top-then-left so bullet order feels natural.
    with_frames = [s for s in shapes if getattr(s, "has_text_frame", False)]
    with_frames.sort(key=lambda s: (_safe_top(s), _safe_left(s)))
    blocks: list[str] = []
    for shape in with_frames:
        text = shape.text_frame.text.strip()
        if not text:
            continue
        # Skip title (already emitted)
        try:
            if shape.is_placeholder and shape.placeholder_format and shape.placeholder_format.idx == 0:
                continue
        except Exception:
            pass
        # Convert paragraphs to bullets when there are multiple lines
        paras = [p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()]
        if len(paras) > 1:
            blocks.append("\n".join(f"- {p}" for p in paras))
        else:
            blocks.append(paras[0] if paras else text)
    return blocks


def _extract_tables(shapes) -> list[str]:
    out: list[str] = []
    for shape in shapes:
        if not getattr(shape, "has_table", False):
            continue
        table = shape.table
        rows = [
            [cell.text.strip().replace("\n", " ") for cell in row.cells]
            for row in table.rows
        ]
        if not rows:
            continue
        header, *body = rows
        md_lines = ["| " + " | ".join(header) + " |",
                    "| " + " | ".join("---" for _ in header) + " |"]
        for r in body:
            md_lines.append("| " + " | ".join(r) + " |")
        out.append("\n".join(md_lines))
    return out


def _extract_charts(chart_shapes) -> list[str]:
    """Pull chart data via python-pptx (structured) → markdown table.

    Skips the AI entirely: chart data is deterministic and the model tends to
    misread axis values. If extraction fails for any reason, fall back to a
    single-line placeholder.
    """
    out: list[str] = []
    for shape in chart_shapes:
        try:
            chart = shape.chart
        except Exception:
            out.append("> *[Chart: tidak bisa dibaca]*")
            continue
        title = _chart_title(chart)
        heading = f"### Chart: {title}" if title else "### Chart"
        try:
            plot = chart.plots[0]
            categories = [str(c) for c in plot.categories]
            series_data: list[tuple[str, list]] = []
            for s in plot.series:
                name = str(s.name) if getattr(s, "name", None) else "Series"
                series_data.append((name, list(s.values)))
        except Exception as e:
            out.append(f"{heading}\n\n> *[Gagal ekstrak data chart: {e}]*")
            continue
        if not categories or not series_data:
            out.append(f"{heading}\n\n> *[Chart kosong atau tidak terstruktur]*")
            continue
        header = ["Category"] + [name for name, _ in series_data]
        md_lines = ["| " + " | ".join(header) + " |",
                    "| " + " | ".join("---" for _ in header) + " |"]
        for i, cat in enumerate(categories):
            row = [cat]
            for _, vals in series_data:
                v = vals[i] if i < len(vals) else ""
                row.append(_fmt_num(v))
            md_lines.append("| " + " | ".join(row) + " |")
        out.append(f"{heading}\n\n" + "\n".join(md_lines))
    return out


def _chart_title(chart) -> str:
    try:
        if chart.has_title and chart.chart_title.has_text_frame:
            return chart.chart_title.text_frame.text.strip()
    except Exception:
        pass
    return ""


def _fmt_num(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        # Trim trailing zeros for clean tables
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s or "0"
    return str(v)


def _picture_alt_text(pic) -> str:
    """Return author-supplied Alt Text from `<p:cNvPr descr="...">`.

    Returns empty when the author didn't set one; caller decides whether the
    string is meaty enough to skip AI.
    """
    try:
        descrs = pic._element.xpath(
            ".//*[local-name()='cNvPr']/@descr"
        )
        if descrs:
            return str(descrs[0]).strip()
    except Exception:
        pass
    return ""


def _alt_text_block(alt: str, slide_idx: int, pic_idx: int) -> str:
    return (
        f"> **[Gambar dalam dokumen asli — alt text penulis]**\n\n"
        f"{alt}\n\n"
        f"*(Placeholder: slide{slide_idx}_img{pic_idx})*"
    )


def _speaker_notes(slide) -> str:
    try:
        if not slide.has_notes_slide:
            return ""
        notes = slide.notes_slide.notes_text_frame.text.strip()
    except Exception:
        return ""
    return notes


def _summarize_context(title: str, text_blocks: list[str]) -> str:
    """Build a compact context string for AI image calls."""
    parts: list[str] = []
    if title:
        parts.append(f"Judul slide: {title}")
    joined = " | ".join(b.replace("\n", " ") for b in text_blocks if b)
    if joined:
        if len(joined) > _CONTEXT_TEXT_MAX:
            joined = joined[:_CONTEXT_TEXT_MAX] + "…"
        parts.append(f"Teks slide: {joined}")
    return "\n".join(parts)


def _describe_layout(shapes, layout_name: str) -> str:
    counts = {"text": 0, "picture": 0, "table": 0, "chart": 0, "shape": 0}
    for s in shapes:
        st = s.shape_type
        if st == MSO_SHAPE_TYPE.PICTURE:
            counts["picture"] += 1
        elif getattr(s, "has_table", False):
            counts["table"] += 1
        elif getattr(s, "has_chart", False):
            counts["chart"] += 1
        elif getattr(s, "has_text_frame", False) and s.text_frame.text.strip():
            counts["text"] += 1
        else:
            counts["shape"] += 1
    parts = [f"{v} {k}" for k, v in counts.items() if v]
    detail = ", ".join(parts) if parts else "(kosong)"
    return f"'{layout_name}' — {detail}"


def _looks_like_shape_diagram(shapes) -> bool:
    autoshapes = sum(1 for s in shapes if s.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE)
    connectors = sum(1 for s in shapes if s.shape_type == MSO_SHAPE_TYPE.LINE)
    return autoshapes >= 3 and connectors >= 1


def _safe_top(shape) -> int:
    try:
        return int(shape.top or 0)
    except Exception:
        return 0


def _safe_left(shape) -> int:
    try:
        return int(shape.left or 0)
    except Exception:
        return 0


def _emit(progress: ProgressFn | None, msg: str) -> None:
    if progress:
        progress(msg)
