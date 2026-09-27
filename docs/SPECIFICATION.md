# Specification

Behavior contract per format. Anything the code does that isn't documented here should either be added here or removed from the code.

## 1. Overall output

- Output is a single UTF-8 `.md` file, same stem as input, saved next to source by default. GUI's *Simpan .md…* lets user choose another destination.
- File starts with `# <stem>` as level-1 heading. All subsequent structure is level ≥ 2 to avoid competing with the title.
- No BOM. LF line endings (Python default).
- Placeholder pattern for images: block-quote header `> **[Gambar dalam dokumen asli — <kind>]**`, then a paragraph of description, optional fenced Mermaid, then italic `*(Placeholder: <hint>)*`. This is machine-parseable via the leading `> **[Gambar dalam dokumen asli — ` marker if the user wants to post-process.

## 2. Providers

### 2.1 Configuration keys (from `.env` at project root)

| Key                       | Provider  | Default                          | Notes |
|---------------------------|-----------|----------------------------------|-------|
| `ANYTOMD_PROVIDER`        | selector  | `anthropic`                      | One of `anthropic`, `openai`, `gemini`, `moonshot`, `ollama`. Unknown → `anthropic`. |
| `ANTHROPIC_API_KEY`       | anthropic | *(none, required)* | Key from console.anthropic.com/settings/keys |
| `ANYTOMD_MODEL`           | anthropic | `claude-sonnet-4-6`              | Any Anthropic vision model ID. |
| `OPENAI_API_KEY`          | openai    | *(none, required)* | Key from platform.openai.com/api-keys |
| `ANYTOMD_OPENAI_MODEL`    | openai    | `gpt-4o`                         | Any OpenAI vision model (`gpt-4o`, `gpt-4o-mini`, etc). |
| `OPENAI_BASE_URL`         | openai    | *(unset)*                        | Override for Azure/proxy. |
| `GEMINI_API_KEY`          | gemini    | *(none, required)* | Key from aistudio.google.com/apikey |
| `ANYTOMD_GEMINI_MODEL`    | gemini    | `gemini-2.5-flash`               | Any Gemini vision model (`gemini-2.5-pro`, `gemini-2.0-flash`, etc). |
| `MOONSHOT_API_KEY`        | moonshot  | *(none, required)* | Key from platform.moonshot.cn/console/api-keys |
| `ANYTOMD_MOONSHOT_MODEL`  | moonshot  | `moonshot-v1-32k-vision-preview` | Kimi vision model. |
| `MOONSHOT_BASE_URL`       | moonshot  | `https://api.moonshot.cn/v1`     | Base URL (use `api.moonshot.ai` for non-CN region). |
| `OLLAMA_HOST`             | ollama    | `http://localhost:11434`         | Ollama server URL. |
| `ANYTOMD_OLLAMA_MODEL`    | ollama    | `qwen2.5vl:7b`                   | Must be **vision-capable**. |
| `ANYTOMD_IMAGE_PROVIDER`  | all       | *(unset)*                        | Sprint 3 hybrid: default provider for image analysis (distinct from text/dispatch). Value: any of the 5 providers. Overrides `ANYTOMD_PROVIDER` for image work. |
| `ANYTOMD_IMAGE_PROVIDER_FOR_DIAGRAM`    | all | *(unset)* | Sprint 3 hybrid: route diagram-classified images to a specific provider. Value: any of the 5. Wins over `ANYTOMD_IMAGE_PROVIDER`. |
| `ANYTOMD_IMAGE_PROVIDER_FOR_CHART`      | both | *(unset)* | Same, for chart-classified images. |
| `ANYTOMD_IMAGE_PROVIDER_FOR_SCREENSHOT` | both | *(unset)* | Same, for screenshot-classified images. |
| `ANYTOMD_IMAGE_PROVIDER_FOR_PHOTO`      | both | *(unset)* | Same, for photo-classified images. |
| `ANYTOMD_COST_CAP_USD`    | both      | `0`                              | Sprint 3: budget cap in USD for accumulated Anthropic image calls. When reached, subsequent images downgrade to Ollama (if configured anywhere) or emit "cap reached" placeholder. `0` = no cap. |
| `ANYTOMD_SKIP_DECORATIVE` | both      | `true`                           | Sprint 1: run the local `is_decorative` classifier before any AI call. Kills 3-10 wasted calls per file (cover balls, gradient circles). |
| `ANYTOMD_IMAGE_DEDUP`     | both      | `false`                          | Optimization: hash images by SHA-256; reuse analysis for repeats within the same file. |
| `ANYTOMD_IMAGE_MIN_DIM`   | both      | `0`                              | Optimization: skip AI call if the longest side of the image is < N pixels (0 disables). |
| `ANYTOMD_IMAGE_MIN_BYTES` | both      | `0`                              | Optimization: skip AI call if raw image bytes < N (0 disables). |
| `ANYTOMD_IMAGE_MODE`      | both      | `all`                            | `all` (default), `diagrams_only` (local heuristic pre-filter), or `none` (never send images). |
| `ANYTOMD_PDF_OCR`         | PDF only  | `never`                          | `never` / `auto` / `always`. Auto-detects scanned pages; always OCRs every page regardless. |
| `ANYTOMD_OCR_ENGINE`      | PDF only  | `tesseract`                      | `tesseract` / `paddleocr` / `ai` / `hybrid`. AI/hybrid use current provider. |
| `ANYTOMD_OCR_LANG`        | PDF only  | `ind+eng`                        | Tesseract-style language code(s), `+`-separated. |
| `TESSERACT_CMD`           | PDF only  | *(auto-detected)*                | Absolute path to `tesseract.exe` if not on PATH. |

All optimization keys are also editable **live in the GUI** via the "Optimasi token AI" panel before each conversion.

### 2.2 Vision call contract

Both providers implement `AIClient.analyze_image(image_bytes, media_type, extra_context) -> ImageAnalysis`. See [ARCHITECTURE.md § Vision pipeline](ARCHITECTURE.md#vision-pipeline) for internals. Contract:

- Input: raw image bytes (normalized to PNG, max 1568px on longest side).
- Output: `ImageAnalysis(kind, description, mermaid)`. `mermaid` is `None` if the model didn't produce one, or produced one that failed validation and autofix and retry.
- `kind` ∈ `{"diagram", "photo", "chart", "screenshot", "illustration", "other"}`.
- `description` is 2–5 sentences in the output language named in the context (PDF: the document's language, see §7), default Bahasa Indonesia. Always populated.
- `silent=True` (decorative, too small, or a repeat of an image already described) → `to_markdown_block()` returns `""` and converters emit nothing.

**Page transcription contract** (PDF page vision, 2026-09-27): `transcribe_page(png_bytes, text_layer, extra_context) -> PageTranscription(markdown, coverage, retried, invalid_mermaid, input_tokens, output_tokens)`. Implemented by every built-in client; `is_local` tells the converter whether the model is offline (Ollama). See [PDF § Page vision](#pdf).

## 3. Per-format behavior

### PDF

**Library**: PyMuPDF (`fitz`).

**Body extraction**: `page.get_text("dict")` for each page (upgraded 2026-09-21). Each block carries `x0`, `y0`, `y1`, text, plus `max_font_size` and `is_bold` derived from span-level metadata. Blocks sorted by y then x.

**Bullet/unicode normalization** (added 2026-09-21): chars `●•◦▪∙◆❖➤➢➔►★` at line start → `-`. Missing-glyph `?` followed by whitespace + text → `-`. Zero-width chars (`​`, `‌`, `‍`, `﻿`) stripped. Runs of `\s{2,}` collapsed to single space.

**Heading auto-detection** (added 2026-09-21): body font size is the modal size across all blocks (weighted by character count). Per block, heading level is derived from:
- Explicit numbering `^\d+(\.\d+){0,3}\s+Text` at block start → `##`/`###`/`####` by depth. Not when the number stands alone on the block's first line (a card/list badge: `1\nPlace an order`), and not for a bare integer in text smaller than the body (a step label: `1  CUSTOMER`) — added 2026-09-27.
- Font size ratio: ≥1.6× body → `##`; ≥1.3× → `###`; ≥1.15× + bold + <80 chars → `####`.
- PDF outline (`doc.get_toc()`) — if the document has real bookmarks, emitted as `## Title` before body of the anchor page. In slide decks, generic titles (`Slide 3`, `Page 3`, `Halaman 3`) are dropped.

**Paragraph merge across blocks** (added 2026-09-21): consecutive prose blocks merge when: same x-indent (±4pt), font size delta < 0.6pt, y-gap < 1.5× body size, previous doesn't end with sentence terminator, next doesn't start with a bullet. Fixes hard-wrapping from PyMuPDF's block splitter cutting mid-sentence.

**Nested bullets** (added 2026-09-21): within a block, distinct bullet-line indent-char counts map to nesting tiers (`- item`, `  - subitem`). Only kicks in when there are ≥2 distinct indent levels with ≥2-char delta. Bullet marker on its own line grabs the next non-empty line as its body.

**Cover/divider page skip** (added 2026-09-21): pages with <20 chars text AND no tables AND no (non-template) images emit `*(Halaman N: cover/divider — dilewati)*` instead of full-page whitespace noise.

**Slide decks** (added 2026-09-27): a PDF is a slide deck when ≥80% of its pages are landscape (ratio >1.2) AND either its creator/producer names Impress, PowerPoint, Keynote or Google Slides, or every page is a screen format (16:9, 16:10, 4:3 ±0.02; A4/Letter landscape don't qualify). Slide decks get `---` between pages and generic outline titles dropped; with page vision (below) they are read page by page.

Text within a block is joined; paragraph breaks (double newline) preserved; soft-wraps merged; de-hyphenation applied (`word-\nfrag` → `wordfrag`).

**Header/footer/page-# stripping** (heuristic):

1. Define boundary zones: top 10% and bottom 10% of page height.
2. For each page, collect distinct block-texts in boundary zones. Normalize by lowercasing and replacing digits with `#` (so "Page 3" and "Page 4" collapse to the same key).
3. Count how many pages each normalized boundary text appears on.
4. If a text appears on ≥ 35% of pages, it is treated as page furniture and skipped from output on every page.
5. Additionally, any boundary-zone block matching `^\s*(page\s*)?\d+(\s*[/of\-]\s*\d+)?\s*$` is skipped (catches raw page numbers even if they vary per page).

Body blocks (not in boundary zone, or in boundary zone but not matching the above rules) are emitted.

**Tables** (pdfplumber, added 2026-09-21): after opening the PDF with PyMuPDF, we also open it with `pdfplumber` once. Two-pass detection:
1. **Lines strategy** (primary): `snap_tolerance=4`, `join_tolerance=4`, `edge_min_length=8`. Catches most bordered tables.
2. **Text-clustering strategy** (fallback per-page, only when lines pass found nothing): stricter validation — max 4 columns, sparsity <30%, no cell >100 chars, each column non-empty in ≥40% of rows. Catches whitespace-aligned tables while rejecting prose. When the detected bbox cuts through a word (pdfplumber puts the last column's edge where most rows end, so a longer line loses its tail: `Modernization` → `Modernizat`), the cells are rebuilt from whole words by `_table_from_words`: rows and column starts from pdfplumber, each word to the row of its vertical centre and the last column starting at or before it, and a line that runs past the right edge is followed word by word (gap ≤12pt) — added 2026-09-27. Before this fix the CoverGo benchmark silently lost 129 words (1.3%) inside tables.

Each detected table's `.extract()` output is converted to markdown by `_table_to_markdown` with:
- **Empty-column collapse**: columns 100%-empty across all rows are dropped.
- **Disjoint-column merge** (`_merge_disjoint_columns`): adjacent columns whose non-empty rows don't overlap are the same logical column split by a spurious ruling → merged. Recovers clean N-col tables from bloated N+k detections (e.g. Cloud Hosting Options 9→3 cols).
- **Sparse-cell rejection**: if >40% cells empty in a wide table (>4 cols), rejected entirely (not really a table).
- **Header row fallback**: leading/trailing fully-empty rows dropped so header row promotion works.

For each page, text blocks whose y-midpoint falls inside a table's bbox (±2pt) are dropped from the text stream to avoid duplication; text blocks and tables are then interleaved by y-coordinate so reading order is preserved. If `pdfplumber` is not installed the step is silently skipped.

**Images** (reworked 2026-09-27): only images actually **painted** on the page count — `page.get_image_info(xrefs=True, hashes=True)`, one entry per xref. (`page.get_images()` lists the page's *resources*; slide exports share one resource dictionary, so every page "had" every image of the deck — 742 images for a 14-page deck of which 91 are painted.) An image painted on ≥50% of pages of a ≥3-page document is **template** (logo, background, frame) and is dropped without an AI call (rides on `skip_decorative`, counted as `skipped_template`). The rest are extracted via `doc.extract_image(xref)`, sent to the AI, and emitted **at their y-position** among the page's text and tables. Silent results (decorative, tiny, duplicate) emit nothing.

**Page vision** (added 2026-09-27; `ConvertOptions.pdf_page_vision` / `ANYTOMD_PDF_PAGE_VISION`): each page is rendered (long side 1568 px) and a vision model writes its Markdown, anchored on the page's text layer.
- Modes: `auto` (default) = slide decks only, only when the client is not local (`is_local=False`: Anthropic/OpenAI/Gemini/Moonshot, or a hybrid whose *diagram* route is cloud), and not when `image_mode="none"`. `always` = every page with content, any provider (Ollama included). `never` = classic extraction only.
- Pages with <20 chars of text and no non-template image are not sent (cover/divider path). Up to 4 pages run concurrently; PyMuPDF work (render, text layer) happens on the main thread first.
- **Text layer** sent with the image: one entry per body block (header/footer removed) `[x=0.16 y=0.23] text`, continuation lines indented, plus exact hints read from the PDF's vector drawing (`converters/pdf_vector.py`):
  - `{legend: X}` — the box around the text is drawn in the colour/border of legend entry X. A legend = ≥2 small text-less swatches (9–40 × 9–30 pt) in different styles, each with a label starting ≤25 pt to its right (read word by word). A swatch matches a box by identical style, else by its colour equalling the box's border or fill (dashed must agree).
  - `{box Sn}` + a `BOX STYLES` list for boxes that match no legend.
  - `CONNECTORS` — every open stroked path (≥6 pt) as `"tail box" → "head box"`, direction from a small filled shape (≤14 pt) within 8 pt of an end; ends resolved to the top text of the smallest box (min side 30 pt, 8 pt tolerance) around them; short text (≤24 chars) within 18 pt of the line becomes `(label "I1")`. Lines whose both ends resolve to the same box (rulings, underlines) are dropped.
- Prompt (`PAGE_SYSTEM`): wording copied exactly from the text layer (capitalisation kept, nothing added); structure from the image; slide title `##`, panels `###`; row-related grids → tables, independent side-by-side panels → `###` sections; boxes+arrows → Mermaid with quoted labels, subgraphs for containment, edges/directions/labels from CONNECTORS; legend categories written next to each item; `> **Figure:**` only for content-bearing pictures.
- **Checks and one retry** (`_transcribe_page_with_retry`): Mermaid blocks validated + autofixed; `coverage` = share of distinct text-layer words present in the answer; `added_words` = words in the answer found nowhere on the page (outside Figure lines; inside Mermaid only quoted labels). Retry once if coverage <92%, a Mermaid block stays invalid, or >2 distinct added words; the retry prompt lists the missing entries / invented words; the better attempt is kept. A Mermaid block still invalid afterwards is demoted to a plain code fence with a note.
- **Failure**: an exception on a page (API error, budget cap) → that page goes through classic extraction; counted as `page_vision_fallbacks`.
- Stats: `page_vision_pages`, `page_vision_retries`, `page_vision_min_coverage`, `page_vision_tokens_in/out` (Anthropic input includes cached system-prompt tokens).

**Encrypted PDFs**: PyMuPDF will raise; the error surfaces in the GUI dialog. No password-prompt UI.

**Scanned / image-only PDFs**: when `ocr_mode` is not `never`, per-page decision:
- `ocr_mode="always"`: OCR every page.
- `ocr_mode="auto"`: OCR only pages where `sum(len(block.text)) < 30` AND at least one image occupies ≥ 50% of the page area (heuristic in `_should_ocr_page` / `_has_full_page_image`).
Selected engine renders the page at 300 DPI via `page.get_pixmap()` → PNG → `backend.transcribe(png, lang)`. Extracted text is emitted as the page body; the normal per-image loop is **skipped for that page** (the "images" ARE the page). Engines:
- `tesseract` — `pytesseract` + local Tesseract binary. Fully offline. Zero token cost.
- `paddleocr` — `paddleocr` Python package. Fully offline. Heavier install (~500 MB). Optional runtime import.
- `ai` — reuses the current AI provider with a transcription-focused prompt. Tokens counted normally.
- `hybrid` — Tesseract first; if result < 50 chars, retry with AI. Best quality/cost tradeoff for mixed-quality scans.

The OCR path uses the **raw** AI client (bypasses the image dedup/skip filters) because page renders are meta-images, not embedded document images. Image filters still apply to any embedded images on non-OCR pages.

### PPTX

**Library**: python-pptx.

**Slide iteration**: `presentation.slides` in slide order. Each slide → `## Slide <n>` header, then title if present as bold text, then content, then layout description as block-quote, then image analyses (if any).

**Text extraction**: only from placeholders and text frames whose text is non-empty. Order: top-to-bottom, then left-to-right. Multi-paragraph text frames become bulleted lists.

**Placeholders skipped**: slide number (idx 15) and footer (idx 16). Title placeholder handled separately (emitted bolded before content).

**Tables**: `shape.has_table` — emitted as Markdown table with pipe-escape on cell text.

**Charts** (native PPTX charts, added 2026-09-21): any shape with `has_chart == True` is emitted as `### Chart: <title>` followed by a markdown table with `Category` as the first column and one column per series (from `chart.plots[0].categories` and `series.values`). **No AI call** — chart data is deterministic and models often misread axes. Numeric values are trimmed of trailing zeros. Chart-shape presence also disables the shape-diagram detector for the slide (charts + connectors would otherwise false-trigger it).

**Speaker notes** (added 2026-09-21): if `slide.has_notes_slide` and the notes text frame is non-empty, emitted as `### Notes` + body at the end of the slide, before the `---` separator. Often the actual decision context lives here rather than on the deck.

**Layout description**: block-quote `> **Layout:** '<layout_name>' — <counts>` where counts is `N text, N picture, N table, N chart, N shape`.

**Pictures**: any `MSO_SHAPE_TYPE.PICTURE`. Order after text/table/chart/layout.

**Author Alt Text passthrough** (added 2026-09-21): before calling the AI, each picture's `<p:cNvPr descr="…">` attribute is read. If the alt text is ≥ 12 chars (author actually filled it in, not the auto-generated "Picture 3" default), it is emitted as the narration verbatim and **the AI call is skipped**. Rationale: the author's own words are more credible than an AI paraphrase and cost nothing. Alt-text blocks use a distinct header (`> **[Gambar dalam dokumen asli — alt text penulis]**`) so they're distinguishable from AI-generated blocks in post-processing.

**AI extra_context enrichment** (added 2026-09-21): when the AI is called for a picture or shape-diagram, `extra_context` includes the slide title and up to ~300 chars of surrounding body text (joined with ` | `). Improves narration accuracy and Mermaid label choice by giving the vision model textual grounding, not just pixels.

**Shape diagrams** (native PPTX shape + connector graphs, no bitmap):

- Detection: `_looks_like_shape_diagram(shapes)` = at least 3 AUTO_SHAPE **and** at least 1 LINE (connector) **and** no PICTURE **and no CHART** on the slide.
- **First pass — direct Mermaid extraction** (`pptx_shape_mermaid.try_extract_mermaid`, added Sprint 4 E1, 2026-09-21):
  - Enumerate auto-shapes → nodes with `text_frame.text` as label. Shape style → Mermaid node syntax: `RECTANGLE` → `[label]`, `ROUNDED_RECTANGLE` → `([label])`, `DIAMOND`/`FLOWCHART_DECISION` → `{label}`, `OVAL`/`FLOWCHART_CONNECTOR` → `((label))`, `FLOWCHART_TERMINATOR` → `([label])`.
  - Enumerate LINE connectors → edges. Endpoints (`begin_x/y`, `end_x/y`) matched to node bboxes with ±0.1 inch tolerance; if no bbox contains an endpoint, nearest-center match within 3× tolerance is used.
  - Small standalone text boxes (≤24 chars) near connector midpoints (within 0.75 inch) become edge labels (`-->|Yes|`).
  - Direction: `LR` if ≥50% of edges are more horizontal than vertical, else `TD`.
  - Rejected (falls back to raster+VLM) if: <2 nodes, no edges, or <50% of nodes have any edge.
  - When it succeeds: zero AI cost, 100% correct labels. Fixture `shape_diagram.pptx` converts in ~10ms.
- **Fallback pass — raster + VLM**: if direct extraction returns `None`, rasterize the slide via `slide_render.render_slide_shapes()` → PNG → AI.
- Rasterizer supports: rectangle, rounded rectangle, oval, diamond, and connectors. Fidelity is intentionally rough — the goal is "recognizable by a VLM," not pixel-perfect.

### DOCX

**Library**: python-docx.

**Body walk**: iterate `doc.element.body.iterchildren()`; recognize `w:p` (paragraph) and `w:tbl` (table). Preserves interleaving.

**Header/footer/page-number**: **automatically excluded** because they live in section-part XML (`headerN.xml`, `footerN.xml`), not in the body element. No filter code needed. Verified with fixture.

**Paragraphs**:
- Style-based mapping: `Title` → `# `, `Heading 1..6` → `## ` .. `####### ` (capped at H6). Body → plain paragraph.
- List detection: paragraph has `<w:numPr>` **or** style name contains "list" or starts with "bullet" → emitted as `- item`.
- Text is squeezed for repeated whitespace, but line breaks within a paragraph (from Shift+Enter) are collapsed to single spaces.

**Tables**: iterate rows; each cell's text is joined from its paragraphs with spaces. Pipes escaped. Newlines within cells collapsed. First row is treated as header.

**Inline images**: for each paragraph, `findall('.//a:blip')` yields embedded image relationships; blob retrieved via `doc.part.related_parts[rid]`. Sent to AI as image.

**Nested tables**: not specially handled — a nested table would be emitted as part of the outer cell's text (whatever python-docx returns). Rare in practice.

**Track changes / comments**: ignored; only the "current" text is exported.

### XLSX

**Library**: openpyxl with `data_only=True` (so cells with formulas expose the last-computed value, not the formula string).

**Sheet iteration**: `wb.sheetnames` in workbook order. Each sheet → `## Sheet: <name>` + table.

**Cell rendering**:
- `None` → empty string.
- `float` where `v.is_integer()` → shown as int (e.g. `4650.0` → `4650`); other floats via `f"{v:g}"` (concise).
- Dates/datetimes/times → ISO 8601.
- Strings: pipes escaped as `\|`, newlines collapsed to space.

**Trimming**:
- Trailing rows where every cell is empty → dropped.
- Trailing columns where every cell across all rows is empty → dropped.
- Leading fully-empty rows → dropped.

**Bounds**: hard-capped at 5000 rows × 100 columns per sheet. If exceeded, output includes `*(dipotong pada 5000 baris × 100 kolom)*` after the table.

**Merged cells**: openpyxl's `iter_rows(values_only=True)` returns the merged-out cells as `None`, only the top-left cell of the merge has the value. This is fine — MD tables can't merge anyway.

**Empty sheets**: rendered as `## Sheet: <name>` followed by `*(sheet kosong)*`. Not skipped, so the user can see they exist.

**Charts, images, pivot tables**: ignored. Charts/images sit outside the cell grid; supporting them is v2.

### Image (standalone PNG/JPG/WEBP/GIF/BMP)

Simple pass-through to AI. Output structure:

```
# <stem>

**Jenis gambar:** <kind>

## Deskripsi

<description>

## Diagram (Mermaid)      ← only if mermaid produced

```mermaid
<mermaid>
```
```

If the AI returns no valid Mermaid, the Mermaid section is replaced by `*(Bukan diagram struktural — tidak ada Mermaid yang dihasilkan.)*`.

### HTML (.html, .htm)

Added 2026-09-21. No AI call — HTML is already structured.

**Libraries**: `beautifulsoup4` for parsing, `markdownify` for HTML→MD.

**Encoding fallback chain**: `utf-8` → `utf-8-sig` → `cp1252` → `latin-1` → `utf-8 errors=replace`. Covers most exports without asking the user.

**Element stripping**: `<script>`, `<style>`, `<noscript>`, `<iframe>`, `<svg>`, `<form>`, `<nav>`, `<footer>`, `<aside>`, `<header>` are dropped before conversion. Rationale: target use case is "someone saved a web page", not "preserve every widget".

**Title**: `<title>` if present, else first `<h1>`, else input filename stem.

**Link-heavy pages**: if >30% of body text sits inside `<a>` tags and body has >200 chars, anchor tags are stripped (`markdownify(..., strip=["a"])`). Catches nav dumps / link farms; article pages keep their in-text links intact.

**Blank-line cleanup**: consecutive blank lines collapsed to one via regex.

## 4. Mermaid pipeline (applies to all image-producing paths)

See [ARCHITECTURE.md § Mermaid quality pipeline](ARCHITECTURE.md#mermaid-quality-pipeline) for full detail. Summary:

1. Parse AI output into `kind` / `description` / raw Mermaid.
2. If raw Mermaid exists, validate (regex checks for known bad patterns).
3. If invalid, auto-fix (loop of substitution rules to fixed-point).
4. If still invalid **and** `kind == "diagram"`, retry the AI call once with an explicit error hint. Re-validate/fix.
5. If still invalid, drop the Mermaid block. Description is preserved regardless.

**Rejection classes** (regex patterns in `ai_client._MERMAID_BAD_PATTERNS`):
- `X [label] -->` — space between node id and label bracket
- `X {label} -->`, `X (label) -->` — same for other bracket types
- Bare `X?` node ID outside `[...]`/`{...}`/`(...)`/`|...|`
- `--> X [label]` — space between arrow target id and its label

**Autofix rules** (applied in loop, max 5 iterations, in this order):
1. Strip `?` from bare node ids.
2. `X [label] -->` → `X -->|label|`
3. `--> X [label]` → `-->|label| X`

## 5. Error handling

- **Missing API key when Anthropic selected**: GUI shows warning dialog before starting conversion.
- **Ollama server down / model not pulled**: `RuntimeError` propagated to GUI, shown in dialog.
- **Per-image AI call failure**: caught per image; a `> *[Gagal menganalisa gambar N: <err>]*` block is emitted in place and conversion continues.
- **Per-page/slide failure**: not caught individually today — bubbles up to the top-level try in the worker thread and shows in GUI. See [ROADMAP](ROADMAP.md).
- **Unsupported extension**: `ValueError("Format tidak didukung: .xxx")` from the dispatcher.

## 6. Token-saving optimizations

All opt-in. Default behavior (no optimizations) is identical to the pre-optimization code path — every image gets an AI call.

Optimizations are configured per-conversion via `ConvertOptions` (from `anytomd/options.py`). GUI collects settings from the "Optimasi token AI" panel; CLI/env picks up `ANYTOMD_IMAGE_*` at start.

The `OptimizedAIClient` wrapper (in `ai_client.py`) enforces filters in this order:

1. **Full-skip** (`image_mode="none"`) — return a placeholder analysis without calling AI. Zero token cost. Placeholder text: `> *[Gambar dilewati (mode: none) — <alt>]*`.
2. **Min-bytes** (`image_min_bytes>0`) — if raw image is smaller than threshold, skip.
3. **Min-dim** (`image_min_dim>0`) — decode image header; if longest side < threshold, skip.
4. **Diagram-only** (`image_mode="diagrams_only"`) — run `image_heuristics.looks_like_diagram()`. Passing signals (any one): ≤20 distinct colors, ≥55% near-white pixels, or ≥6% edge density. Tuned to lean toward keeping (false positives waste one AI call; false negatives lose a diagram — the worse failure mode).
5. **Dedup** (`image_dedup=True`) — SHA-256 of image bytes. First occurrence goes through, result cached; subsequent identical bytes reuse the cached analysis. Cache is per-conversion (fresh cache per file).

Skipped images produce the compact placeholder above instead of the full `> **[Gambar dalam dokumen asli — ...]**` block — except **silent** skips (2026-09-27): decorative, too small (bytes or px), and duplicates emit nothing at all (a duplicate was already described at its first occurrence). Placeholders remain for choices the user made (`mode: none`, `diagrams_only`) and for the budget cap. Stats (sent / skipped_tiny / skipped_duplicate / skipped_not_diagram / skipped_mode_none / skipped_decorative / skipped_template) are accumulated on `ConvertOptions._stats` and shown in the GUI status bar after conversion.

**Measured impact** on `tests/fixtures/dedup_test.pdf` (6-page PDF with a 32×32 logo repeated on every page + 1 real diagram = 7 image occurrences):

| Configuration              | AI calls | Time (Ollama) |
|----------------------------|----------|---------------|
| Baseline (no optimizations)| 7        | 18.5 s        |
| Dedup ON                   | 2        | 5.6 s         |
| Skip < 64 px               | 1        | 3.5 s         |
| Diagrams-only heuristic    | 7*       | 15.6 s        |
| Full skip                  | 0        | 0.0 s         |
| Dedup + skip-tiny combined | 1        | 3.8 s         |

*Heuristic didn't reject the logo — logos with limited palette look "diagram-like" to the local classifier. Diagram-only is more useful for filtering photos/screenshots, not logos.

Since 2026-09-27 the logo in this fixture is painted on all 6 pages and is therefore **template** — every configuration with `skip_decorative` on (the default) sends only the diagram (1 call). `tests/run_options_smoke.py` adds a `skip_decorative=False` case to keep exercising dedup (2 calls).

## 7. Language of output

- Structural markdown (headings, table headers) is not translated — the values come from the source document as-is.
- AI-generated content (image descriptions) is in **Bahasa Indonesia** by default. For PDFs (2026-09-27) the document language is guessed by `anytomd/lang.py` (stopword counts: English / Bahasa Indonesia; Arabic by script share) and passed as `Output language: <lang>` — an English PDF gets English descriptions. PPTX/DOCX still use the default.
- Page vision copies the page's own words; it adds none (checked, see PDF § Page vision).
- The Mermaid block itself uses whatever labels the source shows; those labels are extracted from the image and typically match source language.
