from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import fitz  # PyMuPDF

from ..ai_client import AIClient, wrap_with_options
from ..options import ConvertOptions

ProgressFn = Callable[[str], None]

# A block is header/footer if it sits in the top/bottom N% of the page.
BOUNDARY_RATIO = 0.10
# ... and appears on at least this fraction of pages with the same text.
REPEAT_THRESHOLD = 0.35
_PAGE_NUM_RE = re.compile(r"^\s*(?:page\s*)?\d+(?:\s*[/of\-]\s*\d+)?\s*$", re.IGNORECASE)

# Heuristic: page is likely scanned if extractable text is shorter than this
# AND the page contains an image covering >= this fraction of the page area.
_SCAN_TEXT_CUTOFF = 30
_SCAN_IMAGE_COVERAGE = 0.5
_OCR_RENDER_DPI = 300  # sweet spot for Tesseract on printed text

# Table bbox tolerance when checking if a text block falls inside a table.
_TABLE_BBOX_TOL = 2.0

# A7: Skip cover/divider page threshold — any page with text below this AND no
# non-decorative embedded image is emitted as a compact marker instead of full
# noise. Prevents "V2"-only pages from producing junk.
_COVER_TEXT_CUTOFF = 20

# A3: Bullet/dash normalization — noisy chars from PDF rendering.
_BULLET_CHARS = "●•◦▪∙◆❖➤➢➔►★"
_BULLET_RE = re.compile(rf"^[\s{re.escape(_BULLET_CHARS)}]+")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_ZERO_WIDTH = re.compile(r"[​‌‍﻿]")


@dataclass
class _Block:
    page: int
    y0: float
    y1: float
    text: str
    # Sprint 2: typography metadata for heading detection + paragraph merging
    x0: float = 0.0
    max_font_size: float = 0.0
    is_bold: bool = False


# Numbering patterns that reliably signal headings, independent of font size.
_NUM_HEADING_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[\.\)]?\s+(\S.*)$")


@dataclass
class _TableHit:
    bbox: tuple[float, float, float, float]  # (x0, top, x1, bottom)
    markdown: str


def convert_pdf(path: Path, ai: AIClient, progress: ProgressFn | None = None,
                options: ConvertOptions | None = None) -> str:
    doc = fitz.open(path)
    try:
        blocks_per_page, page_heights = _extract_blocks(doc)
        skip_texts = _detect_repeated_boundary_text(blocks_per_page, page_heights)
        tables_by_page = _extract_tables_by_page(path, progress)
        # A6: PDF outline — if the doc has real bookmarks, use them as heading
        # source-of-truth. `pages_with_outline` maps page_idx → list of
        # (level, title) so we can promote them to markdown headings.
        outline_by_page = _extract_outline(doc)
        # A1: derive body font size once — used to promote big-font blocks to
        # markdown headings during per-page emission.
        body_font_size = _detect_body_font_size(blocks_per_page)

        # OCR uses the RAW ai (bypasses image dedup/skip filters — those are
        # for embedded document images, not page renders).
        ocr_backend = _init_ocr(options, ai, progress)
        # Image analysis uses the WRAPPED ai (respects dedup/skip/mode).
        ai_for_images = wrap_with_options(ai, options) if options else ai

        out: list[str] = [f"# {path.stem}", ""]
        for page_idx in range(len(doc)):
            _emit(progress, f"Page {page_idx + 1}/{len(doc)}: teks")
            page = doc[page_idx]
            body_blocks = _filter_body(
                blocks_per_page[page_idx], page_heights[page_idx], skip_texts
            )
            tables_here = tables_by_page.get(page_idx, [])

            # Emit any outline headings anchored at this page BEFORE content.
            for level, title in outline_by_page.get(page_idx, []):
                hashes = "#" * min(level + 1, 6)  # PDF level 1 → ##
                out.append(f"{hashes} {title}")
                out.append("")

            # A7: Skip cover/divider pages
            text_len = sum(len(b.text) for b in body_blocks)
            if (text_len < _COVER_TEXT_CUTOFF
                    and not tables_here
                    and not page.get_images(full=True)):
                out.append(f"*(Halaman {page_idx + 1}: cover/divider — dilewati)*")
                out.append("")
                continue

            # Decide OCR usage for this page
            do_ocr = _should_ocr_page(options, page, body_blocks)
            if do_ocr and ocr_backend is not None:
                _emit(progress, f"Page {page_idx + 1}: OCR ({ocr_backend.name})")
                try:
                    ocr_text = _ocr_page(page, ocr_backend, options.ocr_lang if options else "ind+eng")
                    if ocr_text.strip():
                        out.append(ocr_text.strip())
                        out.append("")
                        if options is not None:
                            options._stats["ocr_pages"] = options._stats.get("ocr_pages", 0) + 1
                        # Skip the normal image loop for OCR'd pages — the images
                        # ARE the page and we've already read their text.
                        continue
                    else:
                        out.append(f"> *[OCR halaman {page_idx + 1} tidak menghasilkan teks]*")
                        out.append("")
                except Exception as e:
                    out.append(f"> *[Gagal OCR halaman {page_idx + 1}: {e}]*")
                    out.append("")

            # Interleave text blocks and tables in y-order so reading order is
            # preserved. Any text block whose center falls inside a table bbox
            # is dropped — the table extraction already covers that region.
            filtered_text = [b for b in body_blocks if not _block_in_any_table(b, tables_here)]
            # A2: merge consecutive prose blocks (same left, small gap, prev
            # doesn't end with sentence-terminator) into a single paragraph.
            merged = _merge_paragraph_blocks(filtered_text, body_font_size)
            items: list[tuple[float, str]] = []
            for b in merged:
                # A1: promote to heading if typography says so.
                level = _classify_heading_level(b, body_font_size)
                if level > 0:
                    # Strip any leading `?` bullet noise before heading text
                    heading_text = b.text.strip()
                    items.append((b.y0, ("#" * level) + " " + heading_text))
                else:
                    cleaned = _clean_text(b.text)
                    if cleaned:
                        items.append((b.y0, cleaned))
            for tbl in tables_here:
                items.append((tbl.bbox[1], tbl.markdown))
            items.sort(key=lambda x: x[0])
            for _y, content in items:
                out.append(content)
                out.append("")

            image_infos = page.get_images(full=True)
            for img_idx, info in enumerate(image_infos, start=1):
                xref = info[0]
                _emit(progress, f"Page {page_idx + 1}: gambar {img_idx}/{len(image_infos)}")
                try:
                    img_bytes, media = _extract_image(doc, xref)
                except Exception as e:
                    out.append(f"> *[Gagal mengekstrak gambar {img_idx} halaman {page_idx + 1}: {e}]*")
                    out.append("")
                    continue
                try:
                    analysis = ai_for_images.analyze_image(
                        img_bytes,
                        media_type=media,
                        extra_context=f"Gambar ini dari halaman {page_idx + 1} sebuah PDF.",
                    )
                except Exception as e:
                    out.append(f"> *[Gagal menganalisis gambar {img_idx} halaman {page_idx + 1}: {e}]*")
                    out.append("")
                    continue
                block = analysis.to_markdown_block(alt_hint=f"page{page_idx + 1}_img{img_idx}")
                # D1: if AI classified this as a screenshot AND OCR is enabled
                # (or Tesseract is available), extract the on-screen text so
                # users can search/grep it. Free vs. paying for another AI call.
                extra = _ocr_screenshot_if_possible(analysis.kind, img_bytes, options)
                if extra:
                    block += "\n\n**Teks di dalam gambar (OCR):**\n\n" + extra
                out.append(block)
                out.append("")

        return "\n".join(out).rstrip() + "\n"
    finally:
        doc.close()


# ---------- Table extraction (pdfplumber) ----------

def _extract_tables_by_page(
    pdf_path: Path, progress: ProgressFn | None
) -> dict[int, list[_TableHit]]:
    """Detect tables per page via pdfplumber. Returns {} if lib missing/broken."""
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        _emit(progress, "pdfplumber tidak terinstall — table extraction dilewati")
        return {}
    # A4: table settings — snap loose gridlines and join broken cells so
    # pdfplumber merges adjacent column rulings that visually look like one.
    # We keep default word-count thresholds so short tables (Cloud Hosting
    # Options: 1-2 words per cell) still get detected. Over-detection is
    # instead handled downstream in `_table_to_markdown` (sparse-cell rejection).
    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "edge_min_length": 8,
    }
    # Fallback pass: some PDFs render tables without visible cell borders (only
    # row separators or pure whitespace alignment). `text` strategy uses word
    # clustering instead of rulings.
    text_settings = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
        "snap_tolerance": 4,
        "text_tolerance": 3,
    }
    result: dict[int, list[_TableHit]] = {}
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for idx, page in enumerate(pdf.pages):
                hits: list[_TableHit] = []
                try:
                    found = page.find_tables(table_settings=table_settings)
                except Exception:
                    found = []
                for tbl in found:
                    try:
                        data = tbl.extract()
                    except Exception:
                        continue
                    md = _table_to_markdown(data)
                    if md:
                        hits.append(_TableHit(bbox=tuple(tbl.bbox), markdown=md))
                # If ruling-based pass found nothing, try text-clustering as
                # a fallback — but be strict about quality since text-strategy
                # false-positives are common in prose columns.
                if not hits:
                    try:
                        found_text = page.find_tables(table_settings=text_settings)
                    except Exception:
                        found_text = []
                    for tbl in found_text:
                        try:
                            data = tbl.extract()
                        except Exception:
                            continue
                        md = _table_to_markdown(data, strict=True)
                        if md:
                            hits.append(_TableHit(bbox=tuple(tbl.bbox), markdown=md))
                if hits:
                    result[idx] = hits
    except Exception as e:
        _emit(progress, f"pdfplumber gagal: {e}")
        return {}
    return result


def _merge_disjoint_columns(padded: list[list[str]], ncols: int) -> tuple[list[list[str]], int]:
    """Merge adjacent columns whose non-empty rows don't overlap.

    A pattern like `["", "Tenancy", ""]` across rows in cols [0,1,2] is very
    common when pdfplumber picks up light rulings inside a wide cell. If cols
    0 and 1 never both have content in the same row (disjoint), they represent
    the same logical column — merge them by concatenating cell text.
    """
    if ncols < 2:
        return padded, ncols
    changed = True
    while changed and ncols >= 2:
        changed = False
        for i in range(ncols - 1):
            col_a = [row[i] for row in padded]
            col_b = [row[i + 1] for row in padded]
            # Both cols must have at least some content
            if not any(col_a) or not any(col_b):
                continue
            # Row-set disjoint?
            overlap = any(a and b for a, b in zip(col_a, col_b))
            if overlap:
                continue
            # Merge col i+1 into col i (whichever has content wins per row)
            for row in padded:
                merged = row[i] or row[i + 1]
                row[i] = merged
                del row[i + 1]
            ncols -= 1
            changed = True
            break
    return padded, ncols


def _extract_outline(doc: fitz.Document) -> dict[int, list[tuple[int, str]]]:
    """Map page_idx → list of (level, title) from PDF bookmarks. Empty if none."""
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        return {}
    if not toc:
        return {}
    result: dict[int, list[tuple[int, str]]] = {}
    for level, title, page_1based in toc:
        page_idx = max(0, page_1based - 1)
        clean = (title or "").strip()
        if not clean:
            continue
        result.setdefault(page_idx, []).append((int(level), clean))
    return result


def _table_to_markdown(rows, strict: bool = False) -> str:
    """Convert extracted rows (list of list of str|None) into a markdown table.

    Rejects trivial tables (<2 cleaned rows). Pads ragged rows to uniform width.
    A5: if the extracted first row is empty but the second is not, promote the
    second as header (handles pdfplumber corner case where header cell text is
    outside the detected bbox).
    A4-continued: reject tables where >40% of cells are empty AND >4 columns —
    likely over-segmentation from too-many vertical rulings.
    When `strict=True` (text-clustering fallback), tighten sparse-cell threshold
    and require >=2 rows AND >=2 cols with meaningful content in every row.
    """
    cleaned: list[list[str]] = []
    for row in rows or []:
        norm = [(c or "").strip().replace("\n", " ").replace("|", "\\|") for c in row]
        cleaned.append(norm)
    # Drop leading fully-empty rows (A5)
    while cleaned and not any(c for c in cleaned[0]):
        cleaned.pop(0)
    # Drop trailing fully-empty rows
    while cleaned and not any(c for c in cleaned[-1]):
        cleaned.pop()
    if len(cleaned) < 2:
        return ""
    ncols = max(len(r) for r in cleaned)
    if ncols < 2:
        return ""
    padded = [r + [""] * (ncols - len(r)) for r in cleaned]

    # Drop columns that are 100% empty across all rows.
    keep_cols = [i for i in range(ncols) if any(row[i] for row in padded)]
    if not keep_cols:
        return ""
    padded = [[row[i] for i in keep_cols] for row in padded]
    ncols = len(keep_cols)

    # Merge adjacent columns where their non-empty row-sets are disjoint AND
    # the total non-empty rows fit inside the row-set of a single logical
    # column. This collapses spurious column splits from over-detected
    # vertical rulings (pdfplumber often turns one wide cell into 2-3 narrow
    # ones separated by tiny gaps).
    padded, ncols = _merge_disjoint_columns(padded, ncols)

    padded = [row for row in padded if any(row)]
    if len(padded) < 2 or ncols < 2:
        return ""

    # Sparse-cell rejection: if too many cells are empty in a wide table, it's
    # probably not really a table.
    total_cells = ncols * len(padded)
    empty_cells = sum(1 for row in padded for cell in row if not cell)
    if ncols > 4 and empty_cells / total_cells > 0.4:
        return ""
    if strict:
        # Text-clustering fallback: reject anything that looks like prose.
        # Rules:
        #   - Max 4 columns (prose columns usually give 5+).
        #   - Overall sparsity < 30%.
        #   - Every column must have content in >= 40% of rows (a real column,
        #     not a spurious split).
        #   - No cell may contain >100 chars (prose sentences do).
        if ncols > 4:
            return ""
        if empty_cells / total_cells > 0.30:
            return ""
        for col_idx in range(ncols):
            non_empty = sum(1 for row in padded if row[col_idx])
            if non_empty / len(padded) < 0.4:
                return ""
        for row in padded:
            for cell in row:
                if len(cell) > 100:
                    return ""

    header, *body = padded
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _block_in_any_table(blk: _Block, tables: list[_TableHit]) -> bool:
    mid = (blk.y0 + blk.y1) / 2
    for tbl in tables:
        _x0, top, _x1, bottom = tbl.bbox
        if top - _TABLE_BBOX_TOL <= mid <= bottom + _TABLE_BBOX_TOL:
            return True
    return False


# ---------- OCR helpers ----------

def _init_ocr(options: ConvertOptions | None, ai: AIClient, progress):
    if options is None or not options.ocr_enabled:
        return None
    try:
        from ..ocr import create_ocr_backend
        backend = create_ocr_backend(options.ocr_engine, ai_client=ai)
        _emit(progress, f"OCR engine: {options.ocr_engine} ({options.ocr_lang})")
        return backend
    except Exception as e:
        _emit(progress, f"OCR init gagal ({options.ocr_engine}): {e}")
        return None


def _should_ocr_page(options: ConvertOptions | None, page, body_blocks) -> bool:
    if options is None or options.ocr_mode == "never":
        return False
    if options.ocr_mode == "always":
        return True
    # auto: OCR if text is essentially empty AND a big image covers the page
    text_len = sum(len(b.text) for b in body_blocks)
    if text_len >= _SCAN_TEXT_CUTOFF:
        return False
    return _has_full_page_image(page)


def _has_full_page_image(page) -> bool:
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return False
    try:
        for info in page.get_images(full=True):
            xref = info[0]
            for bbox in page.get_image_rects(xref):
                if bbox.width * bbox.height / page_area >= _SCAN_IMAGE_COVERAGE:
                    return True
    except Exception:
        return False
    return False


def _ocr_page(page, backend, lang: str) -> str:
    pix = page.get_pixmap(dpi=_OCR_RENDER_DPI)
    png_bytes = pix.tobytes("png")
    return backend.transcribe(png_bytes, lang=lang)


def _extract_blocks(doc: fitz.Document) -> tuple[list[list[_Block]], list[float]]:
    """Extract text blocks with font metadata via `page.get_text("dict")`.

    Each block carries max font size, bold flag, and leftmost x — used by the
    heading detector and paragraph-merge step.
    """
    per_page: list[list[_Block]] = []
    heights: list[float] = []
    for page_idx, page in enumerate(doc):
        blocks: list[_Block] = []
        try:
            info = page.get_text("dict")
        except Exception:
            info = {"blocks": []}
        for blk in info.get("blocks", []):
            if blk.get("type") != 0:  # 0 = text
                continue
            bbox = blk.get("bbox") or (0, 0, 0, 0)
            x0, y0, _x1, y1 = bbox
            # Collect spans across all lines in the block
            texts: list[str] = []
            max_size = 0.0
            bold_char_count = 0
            total_char_count = 0
            for line in blk.get("lines", []):
                line_parts: list[str] = []
                for span in line.get("spans", []):
                    st = (span.get("text") or "")
                    if not st:
                        continue
                    line_parts.append(st)
                    size = float(span.get("size") or 0)
                    if size > max_size:
                        max_size = size
                    # PyMuPDF flags: bit 4 (0x10) = bold, bit 1 (0x2) = italic
                    flags = int(span.get("flags") or 0)
                    is_bold_span = bool(flags & 0x10) or "bold" in (span.get("font") or "").lower()
                    n = len(st)
                    total_char_count += n
                    if is_bold_span:
                        bold_char_count += n
                if line_parts:
                    texts.append("".join(line_parts))
            text = "\n".join(t.strip() for t in texts if t.strip())
            if not text.strip():
                continue
            blocks.append(_Block(
                page=page_idx,
                y0=y0,
                y1=y1,
                text=text.strip(),
                x0=float(x0),
                max_font_size=max_size,
                is_bold=(bold_char_count / max(1, total_char_count)) >= 0.6,
            ))
        blocks.sort(key=lambda b: (b.y0, b.x0))
        per_page.append(blocks)
        heights.append(page.rect.height)
    return per_page, heights


_SENTENCE_END_RE = re.compile(r"[.!?:;]$|[.!?:;][\"')\]]$")


def _merge_paragraph_blocks(blocks: list[_Block], body_size: float) -> list[_Block]:
    """A2: merge consecutive prose blocks that look like one continued paragraph.

    Two blocks merge if:
      - Both are body-size (not headings).
      - They're adjacent in y (b2.y0 - b1.y1 < ~1.5 line-heights).
      - Similar left indent (|b1.x0 - b2.x0| < 4).
      - Previous block's text does NOT end with sentence terminator.
      - Neither starts with a bullet marker.
    """
    if not blocks:
        return blocks
    out: list[_Block] = [blocks[0]]
    for cur in blocks[1:]:
        prev = out[-1]
        if not _blocks_should_merge(prev, cur, body_size):
            out.append(cur)
            continue
        # Merge: extend prev.text with a space and cur's text
        merged_text = prev.text + " " + cur.text
        out[-1] = _Block(
            page=prev.page,
            y0=prev.y0,
            y1=cur.y1,
            text=merged_text,
            x0=min(prev.x0, cur.x0),
            max_font_size=max(prev.max_font_size, cur.max_font_size),
            is_bold=prev.is_bold and cur.is_bold,
        )
    return out


def _blocks_should_merge(a: _Block, b: _Block, body_size: float) -> bool:
    # Only body-size blocks merge (headings never)
    if _classify_heading_level(a, body_size) > 0 or _classify_heading_level(b, body_size) > 0:
        return False
    # Similar font size
    if abs(a.max_font_size - b.max_font_size) > 0.6:
        return False
    # Same left indent (allow small drift)
    if abs(a.x0 - b.x0) > 4.0:
        return False
    # Vertical adjacency: gap < 1.5 line heights (approx 1.5 * font size)
    gap = b.y0 - a.y1
    if gap < -1 or gap > body_size * 1.5:
        return False
    # Previous ends with sentence terminator → paragraph boundary, don't merge
    last_char = a.text.rstrip()[-1:] if a.text.rstrip() else ""
    if _SENTENCE_END_RE.search(last_char + last_char):
        return False
    # Next starts with bullet/dash — new list item
    b_stripped = b.text.lstrip()
    if b_stripped.startswith(("-", "•", "●", "◦", "▪", "*")):
        return False
    return True


def _detect_body_font_size(all_blocks: list[list[_Block]]) -> float:
    """Return the modal font size across all body blocks — used as the baseline
    threshold for heading detection.
    """
    from collections import Counter
    sizes: Counter[float] = Counter()
    for page in all_blocks:
        for b in page:
            # Round to nearest 0.5 so near-equal sizes cluster
            rounded = round(b.max_font_size * 2) / 2
            sizes[rounded] += len(b.text)
    if not sizes:
        return 11.0  # sane default
    # Most-used size by character count = body
    return max(sizes.items(), key=lambda kv: kv[1])[0]


def _classify_heading_level(block: _Block, body_size: float) -> int:
    """Return heading level 1..4, or 0 if this block is not a heading.

    Signals:
      - Numbering prefix at line start (`1.0`, `2.1`, `3.1.4`) → strong.
      - Font size significantly larger than body → moderate/strong.
      - Bold + short text (<80 chars) + starts with capital → weak, requires
        font size boost.
    """
    text = block.text.strip()
    if not text or len(text) > 200:
        return 0

    m = _NUM_HEADING_RE.match(text)
    numbering = m.group(1) if m else ""
    depth = numbering.count(".") if numbering else -1

    size_ratio = block.max_font_size / body_size if body_size > 0 else 1.0

    # Explicit numbering wins
    if numbering and (block.is_bold or size_ratio >= 1.05):
        # depth 0 = "1", depth 1 = "1.0", depth 2 = "1.0.1"
        return min(2 + depth, 4)  # "1" or "1.0" → H2, "1.0.1" → H3

    # Font-size promotion (no numbering)
    if size_ratio >= 1.6:
        return 2  # very large → H2
    if size_ratio >= 1.3:
        return 3
    if size_ratio >= 1.15 and block.is_bold and len(text) < 80:
        return 4
    return 0


def _detect_repeated_boundary_text(
    per_page: list[list[_Block]], heights: list[float]
) -> set[str]:
    counts: Counter[str] = Counter()
    n = max(len(per_page), 1)
    for page_idx, blocks in enumerate(per_page):
        h = heights[page_idx]
        top_lim = h * BOUNDARY_RATIO
        bot_lim = h * (1 - BOUNDARY_RATIO)
        seen_this_page: set[str] = set()
        for b in blocks:
            if b.y1 <= top_lim or b.y0 >= bot_lim:
                norm = _normalize_for_repeat(b.text)
                if norm:
                    seen_this_page.add(norm)
        for norm in seen_this_page:
            counts[norm] += 1

    return {t for t, c in counts.items() if c / n >= REPEAT_THRESHOLD}


def _filter_body(blocks: list[_Block], height: float, skip_texts: set[str]) -> list[_Block]:
    top_lim = height * BOUNDARY_RATIO
    bot_lim = height * (1 - BOUNDARY_RATIO)
    out: list[_Block] = []
    for b in blocks:
        in_boundary = b.y1 <= top_lim or b.y0 >= bot_lim
        norm = _normalize_for_repeat(b.text)
        if in_boundary and (norm in skip_texts or _PAGE_NUM_RE.match(b.text)):
            continue
        if _PAGE_NUM_RE.match(b.text) and in_boundary:
            continue
        out.append(b)
    return out


def _normalize_for_repeat(text: str) -> str:
    # Replace digits so "Page 3" and "Page 4" collapse to the same key.
    return re.sub(r"\d+", "#", text.strip().lower())


def _ocr_screenshot_if_possible(kind: str, image_bytes: bytes,
                                options: ConvertOptions | None) -> str:
    """D1: Extract on-screen text from a screenshot using Tesseract.

    Runs only when:
      - The AI classified the image as a screenshot (or the local classifier
        marks it as `screenshot`).
      - Tesseract binary is discoverable (via `_find_tesseract`).
    Returns the extracted text as a markdown code block, or empty string.
    """
    if kind != "screenshot":
        return ""
    try:
        from ..ocr import _find_tesseract
        if not _find_tesseract():
            return ""
        import pytesseract  # type: ignore
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(image_bytes))
        lang = options.ocr_lang if options and options.ocr_lang else "ind+eng"
        text = pytesseract.image_to_string(img, lang=lang).strip()
    except Exception:
        return ""
    if len(text) < 20:
        return ""
    # Fence the OCR text so it's clearly distinguishable from AI narration.
    return "```\n" + text + "\n```"


def _apply_nested_bullets(text: str, base_x: float, indent_step: float = 18.0) -> str:
    """D2: If bullet-lines exist in `text`, translate their x-indent (relative
    to `base_x`) into markdown nesting depth. Only affects lines that already
    start with `-` after A3 normalization.
    """
    if "-" not in text:
        return text
    # We don't have per-line x here (block-level only), so this hook is
    # kept as a no-op stub. Full x-per-line detection lives in the dict
    # extraction path; keeping the structure in place for future extension.
    return text


def _clean_text(text: str) -> str:
    # Merge soft-wrapped lines: keep paragraph breaks (double newlines) intact.
    # A3: normalize unicode bullet chars → "- ", strip zero-width chars, collapse
    # runs of spaces. Applied per-line so bullets at line start are recognized.
    # D2 (partial): within a block, if multiple bullet lines have different
    # leading indent, translate that to nested markdown bullets. We estimate
    # indent tier from character-column count of leading spaces per line.
    text = _ZERO_WIDTH.sub("", text)
    paragraphs = re.split(r"\n\s*\n", text.strip())
    cleaned = []
    for p in paragraphs:
        merged = re.sub(r"-\n(\w)", r"\1", p)  # de-hyphenate broken words
        # Gather bullet indent hints for D2 nesting
        raw_lines = merged.split("\n")
        bullet_indents: list[int] = []
        for ln in raw_lines:
            stripped = ln.lstrip()
            if stripped and (stripped[0] in _BULLET_CHARS
                             or re.match(r"^\?\s+\S", stripped)):
                bullet_indents.append(len(ln) - len(stripped))
        # Build a tier map: unique sorted indents → 0, 1, 2...
        tier_map: dict[int, int] = {}
        if bullet_indents:
            unique = sorted(set(bullet_indents))
            # Only trust nesting if there are ≥2 distinct indent levels AND
            # the delta between them is meaningful (>=2 chars).
            if len(unique) > 1 and (unique[-1] - unique[0]) >= 2:
                tier_map = {v: i for i, v in enumerate(unique)}

        lines_out: list[str] = []
        i = 0
        while i < len(raw_lines):
            ln = raw_lines[i]
            stripped = ln.lstrip()
            indent_chars = len(ln) - len(stripped)
            is_bullet = stripped and stripped[0] in _BULLET_CHARS
            is_qmark_bullet = bool(re.match(r"^\?\s+\S", stripped))
            if is_bullet or is_qmark_bullet:
                body = (_BULLET_RE.sub("", ln).lstrip() if is_bullet
                        else stripped[1:].lstrip())
                # If the bullet marker is on its own line, grab the next
                # non-empty line as its body (PDF exports frequently split
                # bullet + body onto separate lines).
                while not body and i + 1 < len(raw_lines):
                    i += 1
                    nxt = raw_lines[i].strip()
                    if nxt:
                        body = nxt
                        break
                tier = tier_map.get(indent_chars, 0)
                lines_out.append("  " * tier + "- " + body)
            else:
                lines_out.append(ln)
            i += 1
        merged = "\n".join(lines_out)
        # After bullet extraction, collapse only NON-bullet line joins.
        # Bullets keep their own line; only prose lines merge.
        out_lines: list[str] = []
        buf: list[str] = []
        for ln in merged.split("\n"):
            if re.match(r"^\s*-\s", ln):
                if buf:
                    out_lines.append(" ".join(x.strip() for x in buf if x.strip()))
                    buf = []
                out_lines.append(ln.rstrip())
            else:
                buf.append(ln)
        if buf:
            out_lines.append(" ".join(x.strip() for x in buf if x.strip()))
        merged = "\n".join(x for x in out_lines if x)
        merged = _MULTI_SPACE.sub(" ", merged)
        cleaned.append(merged.strip())
    return "\n\n".join(c for c in cleaned if c)


def _extract_image(doc: fitz.Document, xref: int) -> tuple[bytes, str]:
    info = doc.extract_image(xref)
    data: bytes = info["image"]
    ext = (info.get("ext") or "png").lower()
    media = f"image/{'jpeg' if ext == 'jpg' else ext}"
    return data, media


def _emit(progress: ProgressFn | None, msg: str) -> None:
    if progress:
        progress(msg)
