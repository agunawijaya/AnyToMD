# Roadmap

Ordered by rough priority. This is where "what next" lives — when the user says "lanjutkan", start from the top.

## Legend

- **P0** — should do before considering v1.0 ready to share
- **P1** — nice-to-have quality/UX improvements
- **P2** — bigger features, only if user asks

## Recently completed (2026-09-27) — PDF slide decks

Motivated by two real slide decks exported from LibreOffice Impress (client
material, 4 + 14 pages) where the PDF path lost diagrams, tables and reading order and
emitted ~740 image placeholders. See [DECISIONS](DECISIONS.md) 2026-09-27.

- **Page vision** (`pdf_page_vision`, `ANYTOMD_PDF_PAGE_VISION`, GUI group "PDF slide → AI per halaman") — slide decks + cloud model: each page rendered and transcribed by the vision model, anchored on the text layer; Mermaid for connected boxes, tables for grids, `###` for panels. Coverage / invented-word / Mermaid checks with one retry; per-page fallback to text extraction on error. 4 pages in parallel.
- **Vector hints** (`converters/pdf_vector.py`) — legend colour per box (`{legend: X}`), box styles, connectors with direction and badge labels, all read from the PDF drawing.
- **Painted-only images** (`_drawn_images`) — `get_image_info` instead of `get_images` (resources shared across pages). **Template images** (painted on ≥50% of pages) skipped. Images placed at their y-position.
- **Silent placeholders** — decorative / tiny / duplicate images emit nothing.
- **Description language** (`anytomd/lang.py`) — PDF image descriptions in the document's language.
- **Heading false positives** — number badges and small step labels no longer `##`.
- **Whole-word table cells** (`_table_from_words`) — text-strategy tables no longer cut words at the bbox edge. CoverGo: word loss 1.3% → 0.0%, table rows 100 → 122 (no-AI run).
- **Mermaid validator** — ignores quoted label content, catches multi-line quoted labels, unbalanced shapes, unclosed subgraphs, unquoted `(…)` in `[…]`; autofix for the last two kinds. Checked against the real Mermaid parser (node + `mermaid@11` + jsdom).
- Tests: `tests/run_pdf_unit.py` (offline, 44 checks), `tests/run_page_vision_smoke.py` (cloud), fixture `slide_deck.pdf`.

**Still open from this work:**
- PPTX/DOCX image descriptions still default to Bahasa Indonesia (language detection only wired into PDF).
- Slide decks with Ollama use the text path: better than before, but vector diagrams and borderless tables are still flat text there. Try `ANYTOMD_PDF_PAGE_VISION=always` with a stronger local VLM if one becomes available.
- `.odp` input is not supported (the decks that prompted this came from Impress); PPTX input gives better results than its PDF export.

## Recently completed (2026-09-21) — post-benchmark v2 (Sprints 1-4)

Motivated by benchmark against Microsoft's `markitdown` and Claude-direct on
a real 57-page RFP PDF (CoverGo). AnyToMD now surpasses both on heading count
(104 vs 0 / 77) and table row capture (114 vs 46 / 63), while remaining
100% offline-capable and adding a hybrid cloud mode for premium image quality.
*(2026-09-27 correction: part of those table rows had words cut at the table
edge — 129 words (1.3%) were silently lost. Fixed by `_table_from_words`; a
no-AI run now gives 122 table rows with 0.0% word loss.)*

### Sprint 1 — quick wins (nol AI cost)

- **A3 bullet/unicode normalization** (`_clean_text`) — chars `●•◦▪∙◆❖➤➢➔►★` + missing-glyph `?` → `-`. Zero-width chars stripped.
- **A4 pdfplumber over-detection fix** (`_extract_tables_by_page`, `_merge_disjoint_columns`) — snap/join tolerance tuned, columns collapsed if 100%-empty, adjacent columns merged when their non-empty rows are disjoint (spurious splits from over-detected vertical rulings).
- **A5 header-row fallback** (`_table_to_markdown`) — leading/trailing empty rows dropped so header row promotion works.
- **A6 PDF outline passthrough** (`_extract_outline`, `doc.get_toc()`) — bookmarks become authoritative heading source when present.
- **A7 cover/divider page skip** — `<20 chars text + no tables + no images` → compact placeholder instead of noise.
- **B1 decorative image classifier** (`image_heuristics.is_decorative`) — solid blobs, gradient circles never touch the AI.

### Sprint 2 — heading & paragraph structure

- **A1 auto-heading from font size** (`_extract_blocks` refactored to `page.get_text("dict")`, `_detect_body_font_size`, `_classify_heading_level`) — modal-font body baseline, numbered headings (`1.0 Executive Summary`) become `##` via `_NUM_HEADING_RE`, big/bold blocks become `###`/`####` by size ratio.
- **A2 paragraph merge across blocks** (`_merge_paragraph_blocks`) — same-x consecutive prose blocks with no sentence terminator + small y-gap merge into single paragraph. Fixed hard-wrapping issue from PyMuPDF block extraction.
- **D2 nested bullet from indent** (`_clean_text`) — bullet indent-char count → nesting tier via `tier_map`. Bullet+body-on-separate-lines coalesced.

### Sprint 3 — hybrid cloud AI

- **C1 per-image provider override** (`Config.image_provider`, `Config.image_provider_by_kind`, `Config.provider_for_kind`, `HybridAIClient`) — env vars `ANYTOMD_IMAGE_PROVIDER_FOR_{DIAGRAM,CHART,SCREENSHOT,PHOTO,DECORATIVE,UNKNOWN}` route each kind to its own provider. Text conversion stays offline; diagrams go to premium Claude.
- **C2 two-pass classify** (`image_heuristics.classify_image` + `HybridAIClient._pick_provider`) — local heuristic labels image, then routing table picks provider. Decorative always shortcircuits.
- **C4 cost estimator + budget cap** (`cost_per_anthropic_call`, `Config.cost_cap_usd`, `HybridAIClient._estimated_cost_usd`) — cap in USD; when hit, remaining images auto-downgrade to Ollama or emit placeholder. Stats surface `est_cost_usd` and `anthropic_calls` to GUI.
- **C5 fallback chain** — if primary provider errors on a specific image, `HybridAIClient` tries the other once before giving up.

### Sprint 4 — advanced

- **D1 screenshot OCR** (`_ocr_screenshot_if_possible` in `pdf.py`) — when AI classifies image as `screenshot`, Tesseract extracts on-screen text and appends after AI narration in a fenced code block. Free (no extra AI call) when Tesseract is available.
- **E1 PPTX shape → Mermaid deterministik** (`converters/pptx_shape_mermaid.py`, `try_extract_mermaid`) — parses auto-shapes + connectors + textbox labels, matches endpoints to node bboxes, emits `flowchart LR/TD` with proper node shape mapping (`[]`, `{}`, `(())`, `([])`) and edge labels from nearby textboxes. Zero AI, 100% correct labels. Falls back to raster+VLM if graph structure can't be recovered.

## Recently completed (2026-09-21) — "AI seperlunya saja" pass

Motivated by comparison with Microsoft's `markitdown`. Goal: keep our unique
strengths (Mermaid pipeline, OCR, shape rasterization) while cutting AI calls
where structured data already gives us a truthful answer.

- **PPTX author Alt Text passthrough** (`_picture_alt_text`, `converters/pptx.py`) —
  if `<p:cNvPr descr="…">` is meatier than 12 chars, use it verbatim and skip
  the AI call. Author's own words > AI paraphrase for credibility.
- **PPTX native chart → markdown table** (`_extract_charts`, `converters/pptx.py`) —
  reads `shape.chart.plots[0].categories` and `series.values` directly. No AI
  call — chart data is deterministic and models often misread axis values.
- **PPTX speaker notes** (`_speaker_notes`) — emitted as `### Notes` per slide.
  Often the actual decision/context lives here, not the deck bullets.
- **PPTX AI extra_context enriched** — slide title + first ~300 chars of body
  text passed to the vision model so its narration/Mermaid gets useful grounding.
- **PDF table extraction via `pdfplumber`** (`_extract_tables_by_page`, `converters/pdf.py`) —
  detects tables from rulings, converts to markdown, filters PyMuPDF text
  blocks whose y-midpoint falls inside a table bbox to prevent duplication.
- **HTML converter** (`converters/html.py`) — `beautifulsoup4` + `markdownify`,
  strips nav/footer/script/style, drops anchors on link-heavy pages.

## P0 — v1.0 gaps

### 1. Verify Anthropic path with a real API key

**Status**: **done 2026-09-27** — key stored in Windows Credential Manager; `claude-sonnet-4-6` exercised end-to-end on image descriptions (two real slide decks) and on PDF page vision (`tests/run_page_vision_smoke.py`, 18 real slide pages). `tests/run_smoke.py` still forces Ollama; the Anthropic comparison for its fixtures is not written up.

**Steps**:
1. User sets `ANTHROPIC_API_KEY` in `.env` and switches `ANYTOMD_PROVIDER=anthropic`.
2. Run `python tests\run_smoke.py`.
3. Compare Mermaid output quality vs Ollama. Expected: higher hit rate, better semantic accuracy for shape-diagram case.
4. Note observed cost per fixture in `docs/TEST_PLAN.md` if useful.

### 2. Initialize as git repo + first commit

**Status**: **done** — the project is a git repo on `main` (initial commit `973c5bf`).

**Steps**:
```powershell
cd E:\Projects\AnyToMD
git init
git add .
git commit -m "Initial commit: AnyToMD v0.1 — PDF/PPTX/DOCX/XLSX/image → MD with Anthropic+Ollama backends"
```
Confirm `.env` is gitignored (it is, per `.gitignore`) so no key leaks.

### 3. Manual GUI smoke pass

**Status**: never fully exercised. Ran `python -m anytomd` implicitly via imports at compile-check time only.

**Steps**: follow S9 in [TEST_PLAN.md](TEST_PLAN.md#s9--gui-smoke-manual). Report issues.

### 4. Per-page/slide error handling in converters

**Status**: currently, an exception in the middle of a multi-page conversion aborts the whole file. Per-image errors are caught, but not per-page.

**Steps**: wrap the per-page loop in `pdf.py` and per-slide loop in `pptx.py` with try/except that emits `> *[Gagal proses halaman/slide N: ...]*` and continues. Same pattern as the existing per-image handling.

---

## P1 — quality & UX

### 5. Better prompt for Anthropic shape-diagram case

Current prompt is provider-agnostic. Claude might benefit from an extra hint that "the image was rasterized by our own renderer, so shape colors and fonts are placeholder — focus on structure and labels only". Test with real API access.

### 6. CLI mode

Some workflows want to batch or script conversions. Add an optional CLI: `python -m anytomd <input> [-o <output>]`. Keep GUI as default when no args.

**Sketch**:
```python
# In __main__.py
if len(sys.argv) > 1 and not sys.argv[1].startswith("--gui"):
    return cli.run(sys.argv[1:])
return gui.run()
```

### 7. PowerPoint COM renderer as opt-in

If the user has Office (this box does), we can use `pywin32` to drive PowerPoint to export a slide to PNG. Higher fidelity than our PIL renderer for complex slides. Behind an env flag: `ANYTOMD_PPTX_RENDERER=powerpoint` else falls back to PIL.

**Risk**: COM lifecycle. Needs proper `pythoncom.CoInitialize()` in the worker thread; PPT process cleanup on error paths.

### 8. Better XLSX rendering for wide sheets

100-column cap is arbitrary. For truly wide sheets, consider:
- Splitting into multiple MD tables (per group of ~10 cols)
- Or emitting as a code block with pipe-aligned columns
- Or a warning + pointer to the source file for anything past N cols

### 9. Preview for what will be sent to AI (debug mode)

When Mermaid is wrong, it's hard to know if the fault is the render or the model. A "save what we sent to AI" toggle would write the normalized PNG next to the output for inspection. Same reasoning as `preview_render.py` but automatic.

### 10. Cache AI results per image hash

If the user re-runs on the same document, all the image analyses re-fire. A local cache (sqlite? JSON in `~/.anytomd/cache/`) keyed on `sha256(image_bytes)` + provider + model would make re-runs near-instant.

### 11. Progress bar granularity

Current progress bar is indeterminate (busy indicator). Since we know page/slide/image counts up front, we can show N/M with a determinate bar. Minor but nice.

---

## P2 — bigger features (only if asked)

### 12. Additional input formats

- ~~**HTML** → MD~~ **Resolved 2026-09-21** — `anytomd/converters/html.py` via `beautifulsoup4` + `markdownify`. Strips nav/footer/script/style; drops anchor tags on link-heavy pages.
- **RTF** → MD (harder — `striprtf` for basic; loses formatting)
- **ODT / ODS** → MD (LibreOffice route, or `odfpy`)
- **EPUB** → MD (`ebooklib`, useful for research reading)
- **Legacy binary `.doc` and `.xls`** → route through LibreOffice headless conversion first

### 13. Post-processing plugins

Hook points after a converter runs, before the file is written:
- "Strip watermark phrases" (list of regex patterns from user config)
- "Anonymize names" (regex + replacement)
- "Translate section headings" (calls the AI with just the heading text)

### 14. Multi-file / folder mode

Drop a folder, convert everything inside, produce one combined `.md` or a folder of `.md` files. Requires CLI or a new GUI panel.

### 15. Live Mermaid preview panel in GUI

Render Mermaid blocks in the preview using `mermaid-cli` or an embedded webview. Right now Mermaid is text-only in preview.

### 16. ~~Direct PPTX shape → Mermaid (bypass VLM)~~

**Resolved 2026-09-21 (Sprint 4 E1)** — `converters/pptx_shape_mermaid.py`.
Parses auto-shapes + connectors + textbox labels from python-pptx, matches
endpoints to node bboxes, emits `flowchart LR/TD` with shape-mapped nodes.
Fixture `shape_diagram.pptx` converts in **0.01s** (was 5.3s with Ollama) with
100% correct labels and edge annotations (`Yes` / `No`).

### 17. Provider auto-fallback

If Anthropic call fails (network, rate limit), fall back to Ollama automatically. Would surface as a warning in output rather than a hard error.

### 18. Packaging as a Windows installer

`PyInstaller` or `briefcase` to produce a single `.exe`. Only meaningful once v1.0 is stable and CLI/GUI decision is settled.

---

## Known limitations (won't fix unless a P2 item covers it)

- **Encrypted PDFs** — fail with PyMuPDF exception, no password UI. Fix would be a password dialog.
- ~~**Scanned PDFs** — text extraction returns empty; images fall through to AI~~. **Resolved 2026-08-06** with the OCR module (`anytomd/ocr.py`) — Tesseract/PaddleOCR/AI/Hybrid engines, auto-detect via `_should_ocr_page`.
- **Merged cells in tables** — MD tables can't represent merges. Merged-out cells appear as empty in the output; top-left has the value.
- ~~**Embedded charts / SmartArt in DOCX/PPTX** — currently ignored~~. **Partially resolved 2026-09-21**: PPTX native charts (`shape.has_chart`) now render as markdown tables using `chart.plots[0].categories` + `series.values` (no AI call — deterministic). SmartArt and DOCX charts still ignored.
- **Right-to-left text** — untested. Should work if the source library handles it, but not verified.
- **Very large files** — no streaming, everything is in-memory. 500-slide decks or 100k-row sheets may swap.

---

## Bug watch (things noticed but not chased)

- **Anthropic base64 media_type is hardcoded to `image/png`** even though we resize to PNG anyway; if input was originally JPEG we still send it as PNG. Not a bug (works), but the `media_type` parameter to `analyze_image` is unused for that branch. Consider removing the parameter or honoring it.
- **`_validate_mermaid` doesn't validate individual node references** — first-line + regex patterns + (since 2026-09-27) quote/bracket/subgraph balance. A nonsensical body like `flowchart TD\nasdf ijkl` would still pass. The real parser (`mermaid@11` under node + jsdom, ~15 s startup) was used to verify outputs during development; wiring it in as an optional check is possible since node is on this box.
- **Text blocks beside a table are dropped** — `_block_in_any_table` only compares the block's y-midpoint with the table's vertical band, ignoring x. Prose in a second column next to a table disappears. Not seen in the word-loss checks run so far, but a two-column layout would hit it.
- **DOCX list item detection doesn't distinguish ordered from bullet.** Emit as `-` regardless. Numbered lists lose their numbering. Minor.

---

## When you're picking this up cold

Read [CLAUDE.md](../CLAUDE.md) first (that's the top-level project intro). Then this file. Then whichever `docs/*.md` matches what you're about to touch. Skip the rest.
