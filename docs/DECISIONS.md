# Decisions

Chronological log of choices where the rationale matters more than the code. Each entry states the decision, the alternatives considered, and the *why*. When re-visiting a decision, add a new dated entry — don't rewrite the old one.

## Format & date

`## YYYY-MM-DD — short title`
Then: **Context**, **Options**, **Decision**, **Consequences**.

---

## 2026-08-05 — Provider abstraction: Anthropic + Ollama behind a Protocol

**Context**: The user wants both cloud-quality vision (Anthropic) and offline (Ollama). Adding a second provider mid-project risked coupling converters to one SDK.

**Options**:
- (A) Anthropic-only. Simple, but excludes users without an API key.
- (B) Ollama-only. Free/offline but Mermaid quality is worse than Claude.
- (C) Both, selected by config. More code, more testing.

**Decision**: (C). Both providers implement a `typing.Protocol` (`AIClient`) with a single method `analyze_image(...)`. A factory `create_client(config)` picks the concrete class based on `ANYTOMD_PROVIDER`.

**Consequences**:
- Converters import `AIClient` structurally — no import cycles, no runtime type checks.
- Adding a third provider (e.g. GPT-4o) is a matter of writing a new class + one line in the factory.
- Cost of maintaining two backends is real but low; both share the parse/validate/autofix/retry pipeline.

---

## 2026-08-05 — Library picks

**Context**: Each format needs a parser. Multiple viable options existed for each.

**Options considered per format**:

| Format | Chosen | Alternatives | Why chosen |
|--------|--------|--------------|------------|
| PDF | PyMuPDF (`fitz`) | pdfplumber, pdfminer.six | Faster, richer geometry (bbox-per-block), gets images in one API. Downside: AGPL. Acceptable for personal-use tool. |
| PPTX | python-pptx | pptx-tools, pptx2md | Only maintained library with full shape+connector access. |
| DOCX | python-docx | docx2python, mammoth | Best for structural walk (body + tables interleaved). Mammoth is HTML-focused; docx2python is heavier. |
| XLSX | openpyxl | pandas, xlrd | No need for pandas here — reading raw sheets is simpler with openpyxl and avoids a 40 MB dep. xlrd doesn't support xlsx anymore. |
| Vision | anthropic + ollama | openai, google-generativeai | Matches provider choice. |
| GUI | PySide6 | Tkinter, PyQt6, wxPython, Dear PyGui, Streamlit | User picked "GUI desktop". PySide6 is Qt6 with LGPL, better looks than Tk, mature drag-drop. |
| Image | Pillow | OpenCV, wand | Pillow is already a transitive dep; sufficient for our normalize + rasterize needs. OpenCV would be overkill. |

**Decision**: as above.

**Consequences**: All are permissive-ish licenses (AGPL for PyMuPDF, LGPL for PySide6, MIT/BSD for others). No known ABI issues on Windows 11 + Python 3.11.

---

## 2026-08-05 — PPTX shape diagrams: PIL rasterizer, not PowerPoint/LibreOffice

**Context**: Slides containing native PPTX shape flowcharts (rectangles + connectors, no bitmap) need to become Mermaid. We need an image the VLM can look at.

**Options**:
- (A) **PowerPoint COM automation** (via `pywin32`). Highest fidelity. Requires PowerPoint installed. Fragile — spawns a background PPT process that can leak on crash. Windows-only.
- (B) **LibreOffice headless** (`soffice --headless --convert-to png`). Good fidelity. Requires LibreOffice installed. Portable. Not installed on this box.
- (C) **PIL programmatic render**. We iterate shapes ourselves via python-pptx, draw them on a PIL canvas. Coarse fidelity — no themes, no gradients, no fancy fonts. Portable. Always works.

**Decision**: (C) as the default. (A) and (B) are viable v2 upgrades if fidelity becomes a bottleneck.

**Consequences**:
- Renderer lives in `anytomd/slide_render.py`. Only understands a subset of MSO_SHAPE types (rectangle, rounded, oval, diamond, connector). Anything else falls back to a rectangle. That's usually good enough — the VLM cares about layout structure and labels, not exact shape.
- Downside: less recognizable to a VLM than a real PowerPoint render. Mermaid quality is correspondingly weaker for complex slides.
- Upside: no external dep, no COM lifecycle to manage, works on machines without Office.
- Preview available via `python tests\preview_render.py` — writes the rasterized PNG to `tests/output/shape_diagram_render.png` for eyeballing.

---

## 2026-08-05 — Mermaid safety net: validate → autofix → retry <a id="mermaid-safety-net"></a>

**Context**: Qwen2.5-VL 7B routinely produces syntactically invalid Mermaid — `C [No] --> D`, `Pass?` as a bare identifier, etc. Emitting these makes `.md` files render with red parse errors.

**Options**:
- (A) **Do nothing**. Emit what the model returns. Loud errors in output.
- (B) **Reject** invalid Mermaid, keep description. Robust but loses information.
- (C) **Post-process** with regex-based auto-fix. Cheap, offline, mostly works.
- (D) **Retry** the AI call with an error hint. Doubles cost/latency on failed cases but improves quality.
- (E) **Full Mermaid parse** via mermaid-cli (Node.js). Most reliable validator. Adds Node.js runtime dep.
- (F) **Only use Anthropic** (which rarely produces invalid Mermaid). Solves the symptom but blocks the offline user.

**Decision**: (B) + (C) + (D), in that order. Pipeline: parse → validate → if invalid, autofix loop (fixed-point, max 5 iter) → re-validate → if still invalid and `kind=="diagram"`, retry AI once with the previous attempt + specific error reasons in the prompt → re-validate/fix → give up (description remains).

**Consequences**:
- Description is always preserved. Mermaid may be dropped but is never emitted broken.
- Retry doubles latency only on failed cases. Ollama fixture pass rate went from ~30% to ~90% after adding autofix + retry.
- Adding new bad-pattern regexes is cheap — see `_MERMAID_BAD_PATTERNS`. Each entry is `(pattern, human_reason)` so retry hints are specific.
- Not chosen (E) because pulling in Node.js is a heavy dep for what regex handles. If the false-negative rate becomes a real problem, revisit.

---

## 2026-08-05 — Mermaid validator false-positive with `(OK?)` <a id="mermaid-validator-false-positive"></a>

**Context**: First cut of the `?`-in-identifier check was `(?<![\[\{\(|])[A-Za-z_]\w*\?`. This matched inside `(OK?)` — starting a fresh match at "K", whose lookbehind sees "O" (not in the exclusion class).

**Options**:
- (A) Live with false positives — reject valid mermaid too. Overshoots.
- (B) Add `\b` word boundary — subtle behavior in Python regex.
- (C) Extend the exclusion class to include word characters (`\w`).

**Decision**: (C). Pattern is now `(?<![\[\{\(|\w])`. If the preceding character is any word char, we're mid-identifier and shouldn't restart a match there.

**Consequences**: `(OK?)` (paren-wrapped) and `{OK?}` (curly-wrapped) both accepted correctly. Bare `Pass?` outside brackets still rejected.

---

## 2026-08-05 — Autofix rule ordering matters <a id="autofix-order"></a>

**Context**: Original autofix ran the `X [label] -->` → `X -->|label|` rule before stripping `?` from bare identifiers. For `Test --> Pass? [Pass?]`, the `-->` variant `--> X [label]` → `-->|label| X` couldn't fire because `Pass?` isn't a `\w*` match on its own.

**Options**:
- (A) Rewrite rules to be `?`-tolerant.
- (B) Reorder: strip `?` first, then run other fixes.
- (C) Loop the ruleset to fixed point.

**Decision**: (B) + (C). Strip `?` first, then apply label-repair rules, loop max 5 iterations. Both cheap; combining them makes the pipeline robust to whatever order the individual mistakes appear.

**Consequences**: All observed error classes now converge to valid Mermaid within 1-2 iterations.

---

## 2026-08-05 — DOCX list detection via style name (fallback) <a id="docx-list-fallback"></a>

**Context**: python-docx creates list items with the "List Bullet" style. Word inherits the numbering property from the style, so the paragraph's own `<w:pPr>/<w:numPr>` is absent. First cut of `_is_list_item()` returned False and the bullets appeared as plain paragraphs.

**Options**:
- (A) Fully resolve the style hierarchy to find the effective `numPr`. Correct but complex.
- (B) Fallback: if direct `numPr` absent, check style name for "list" or "bullet".
- (C) Ignore list-ness entirely; render everything as paragraphs. Regression in output quality.

**Decision**: (B). Cheap and covers ~all practical cases (Word's built-in list styles all include one of those words: "List Bullet", "List Number", "List Paragraph", etc.). Direct-`numPr` check remains as the primary signal for custom lists.

**Consequences**: Custom numbering styles named something exotic would miss detection. Deemed acceptable — users can rename or explicitly apply a "List" style.

---

## 2026-08-05 — PPTX shape-diagram dedup <a id="pptx-shape-diagram-dedup"></a>

**Context**: First implementation of the shape-diagram branch appended both (a) the text-frame extraction (bullets like "Commit", "Build", ...) AND (b) the AI's rasterized description. This doubled up: the shape labels appeared as prose bullets AND inside the diagram description.

**Options**:
- (A) Skip text extraction when the shape-diagram branch fires.
- (B) Skip the shape-diagram AI call when text extraction succeeds. Loses the Mermaid benefit.
- (C) De-duplicate in post — compare extracted text tokens against the AI description. Fragile.

**Decision**: (A). If the slide is a shape diagram, the render + AI is the source of truth; text extraction is redundant. `pptx.convert_pptx` branches early on `is_shape_diagram`.

**Consequences**: Marginal loss if the slide has both a shape diagram AND legitimate body text — but this is rare (usually a slide is one OR the other). If it becomes an issue, add a distinction between "shape-diagram text" (labels inside shape boxes) and "narrative text" (separate text placeholders).

---

## 2026-08-05 — Output filename includes source extension <a id="output-filename"></a>

**Context**: Naive `stem + ".md"` collides when input has same stem in two formats — `sample.pdf` and `sample.pptx` both wrote to `sample.md`, second overwriting first. Discovered when smoke-testing DOCX/XLSX add.

**Options**:
- (A) Overwrite (current behavior). Confusing when user runs multiple.
- (B) Auto-suffix with number (`sample_1.md`, `sample_2.md`). Awkward.
- (C) Include source extension in output name (`sample_pdf.md`).

**Decision**: (C) for the smoke test outputs. GUI still uses `stem + ".md"` next to the source because the source's extension is preserved by the sibling file's name — this collision only affects the test harness.

**Consequences**: Test outputs are unambiguously named. Production convention (`x.pdf` → `x.md` alongside) preserved for user familiarity.

---

## 2026-08-06 — OCR: 4 engines + 3 modes, user-selectable <a id="ocr-design"></a>

**Context**: Scanned PDFs (image-only, no extractable text) were falling through to the AI as generic image analyses — describing each page instead of transcribing text. Wasteful and useless as output.

**Options**:
- (A) Single engine (Tesseract). Simple, offline, fast. Users with heavier accuracy needs stuck.
- (B) AI-only OCR (via existing vision provider). No new install, but expensive tokens for every scanned page.
- (C) User picks per conversion — all four engines available.

**Decision**: (C). Backends in `anytomd/ocr.py`:
- `TesseractBackend` — via `pytesseract`, requires local Tesseract binary. Default.
- `PaddleOCRBackend` — via `paddleocr`, optional heavy install (runtime `ImportError` if missing).
- `AIVisionOCRBackend` — reuses AI client with transcription-focused extra_context.
- `HybridOCRBackend` — Tesseract first, falls back to AI if result < 50 chars.

Trigger modes: `never` (default, preserves existing behavior), `auto` (heuristic in `_should_ocr_page`), `always` (force every page).

**Key mechanical detail**: OCR uses the RAW AI client, not the image-filter-wrapped one. Page renders are meta-images (200 KB each), not embedded document images — subjecting them to dedup or size filters would produce wrong results (all pages of a scanned doc would look identical to size filters).

**Consequences**:
- User owns the tradeoff between speed (Tesseract), accuracy (PaddleOCR), context understanding (AI), and hybrid.
- PaddleOCR is optional — install only if wanted. Keeps default `pip install` lean.
- Auto-detect heuristic: text < 30 chars + image coverage ≥ 50% of page area. Tuned on the `scanned.pdf` fixture.
- Measured: Tesseract auto on a 2-page bilingual scan takes 1.7 s and produces correct transcript. AI OCR on same takes 13 s and paraphrases slightly.

---

## 2026-08-05 — `.env` loaded from project root, not CWD <a id="env-location"></a>

**Context**: Default `load_dotenv()` looks at CWD. If the user launches the GUI from a Start Menu shortcut, CWD is often `C:\Windows\System32` or similar, and `.env` doesn't load.

**Options**:
- (A) Require users to always launch from the project directory. Bad UX.
- (B) Compute project root from the package's `__file__` and load from there.
- (C) Search upward from CWD for `.env`. More logic, harder to reason about.

**Decision**: (B). `config.py` does `Path(__file__).resolve().parent.parent / ".env"`. Also does a CWD fallback so shell workflows keep working.

**Consequences**: Shortcut-launch works. Users who run from arbitrary CWDs also work because the CWD fallback is still there.
