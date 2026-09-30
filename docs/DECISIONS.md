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

---

## 2026-09-21 — PDF heading & paragraph detection: font-size clustering + numbering regex

**Context**: Benchmark against Microsoft markitdown and Claude-direct on a 57-page CoverGo RFP showed AnyToMD had 2 headings (just the title + one) vs Claude's 77 vs 0 for markitdown. Body paragraphs were also hard-wrapped mid-sentence because PyMuPDF `get_text("blocks")` splits at whatever visual block boundary PDF has.

**Options**:
- (A) Leave as-is. Text extract still works, just no MD structure.
- (B) Use PDF outline (bookmarks) if present. Only works for well-authored PDFs.
- (C) `page.get_text("dict")` for span-level font info + cluster font sizes globally to determine heading tiers.
- (D) LLM post-process the extracted text to add structure. Expensive, non-deterministic.

**Decision**: (B) + (C). Outline used when present (`doc.get_toc()`); otherwise fall back to font-size clustering. Modal font size across all blocks = "body". Ratios 1.6× / 1.3× / 1.15×+bold → H2/H3/H4. Numbering pattern `^\d+(\.\d+){0,3}` boosts to explicit numbered heading. Paragraph merge across adjacent blocks with same x-indent and no sentence terminator.

**Consequences**:
- Heading count jumped from 2 → 104 on the CoverGo benchmark, surpassing Claude direct (77).
- The upgrade from `get_text("blocks")` to `get_text("dict")` was mechanical but touched the whole extract pipeline. Kept `_Block` shape but enriched with `x0`, `max_font_size`, `is_bold`.
- Doesn't help for scanned PDFs — those go through OCR, no font info.

---

## 2026-09-21 — pdfplumber column-collapse strategy for over-detected tables

**Context**: pdfplumber's default `find_tables()` over-segments tables when the source PDF has many light vertical rulings (common in Google Slides / InDesign exports). CoverGo Cloud Hosting Options table came out as 9 columns of which 6 were empty pad columns.

**Options**:
- (A) Tune `table_settings` more strictly. Rejects legit tables like the SLA one.
- (B) Post-process: drop columns that are 100%-empty across all rows.
- (C) Post-process: merge adjacent columns whose non-empty rows are disjoint (spurious split from one wide cell into narrow siblings).
- (D) Reject any table with >40% empty cells + >4 cols as false positive.

**Decision**: (B) + (C) + (D) combined. Also added a two-pass strategy: `lines` first, `text` clustering as per-page fallback when `lines` found nothing.

**Consequences**:
- Cloud Hosting Options came out as a clean 3-col table.
- SLA and RACI tables (4+ cols) survived. Some multi-line headers still get split — acceptable for v1.
- Text-strategy fallback is expensive (extra pass) but only per-page-with-no-tables. Adds ~5s on CoverGo PDF.

---

## 2026-09-21 — Direct PPTX shape → Mermaid extraction (bypass VLM entirely)

**Context**: PPTX shape diagrams (auto shapes + connectors) were being rasterized by our PIL renderer and sent to a VLM for Mermaid regeneration. This was our main advantage over markitdown (which skips them entirely) but had two problems: (1) Ollama qwen2.5vl:7b regularly produced broken Mermaid, (2) even when Mermaid was valid, node labels sometimes drifted from the source ("Deploy" → "Deploys").

**Options**:
- (A) Ship better prompts + accept VLM randomness.
- (B) Require Anthropic for shape diagrams. Costs tokens.
- (C) Parse the shape XML directly: auto-shapes → nodes with `text_frame.text`; LINE connectors → edges by matching endpoints to node bboxes; textboxes near midpoints → edge labels.

**Decision**: (C). New module `pptx_shape_mermaid.py` with `try_extract_mermaid(slide, shapes) -> str | None`. Falls back to raster+VLM only when the graph can't be recovered cleanly (missing endpoints, <2 nodes, <50% nodes connected).

**Consequences**:
- Fixture `shape_diagram.pptx` conversion time: 5.3s (Ollama VLM) → 0.01s (deterministic XML).
- 100% correct labels (they come from `shape.text_frame.text` verbatim).
- Direction inference from majority edge orientation is heuristic; sometimes produces `LR` where `TD` would read better. Acceptable — user can flip after the fact.
- Fallback to raster+VLM still there for edge cases (SmartArt, grouped shapes with complex nesting).

---

## 2026-09-21 — Ollama demoted to "fallback / graceful degradation" (Opsi B)

**Context**: Benchmark showed Ollama qwen2.5vl:7b gave generic one-line descriptions on info-dense diagrams (business capability model, org charts, gantt) where Claude gave multi-paragraph structured narration. User asked "apakah worth menyertakan Ollama". Full removal (Opsi A) considered.

**Options**:
- (A) Remove Ollama entirely. Simplifies code (drop ~150 lines) but breaks: offline mode, fallback chain, budget-cap graceful degradation, free smoke testing.
- (B) Keep Ollama, change default provider to Anthropic. Ollama positioned as fallback / offline mode / testing.
- (C) Replace with a larger local VLM. Doesn't address root cause — 7B VLMs are inherently weaker at info-dense diagrams.

**Decision**: (B). `.env.example` default `ANYTOMD_PROVIDER=anthropic`. Docs explicit: "Ollama untuk offline / testing / graceful degradation". Fallback chain in HybridAIClient always tries Ollama first when primary fails (free).

**Consequences**:
- New users get Claude quality out of the box (assuming API key present).
- Testing without paying: still works via Ollama.
- Budget cap gracefully downgrades to Ollama instead of hard-stopping.
- Docs are honest about Ollama's quality gap for info-dense images.

---

## 2026-09-21 — Multi-provider: OpenAI, Gemini, Moonshot (Kimi) alongside Anthropic + Ollama

**Context**: User wanted choice of cloud providers with different price/quality tradeoffs. Also enables hybrid routing (diagram → best provider, photo → cheapest).

**Options**:
- (A) Anthropic + Ollama only (status quo). Simplest.
- (B) Add all major cloud vision providers. More SDKs, more testing surface.
- (C) Wrap everything in a single OpenAI-compatible interface (many providers offer this). Loses provider-specific features (Anthropic cache_control, Gemini native).

**Decision**: (B) with native SDK per provider where it matters. `OpenAIClient` uses `openai` package; `MoonshotClient` extends `OpenAIClient` with `base_url` swap (Kimi is OpenAI-compatible). `GeminiClient` uses `google-genai` (Gemini has best native support there). Anthropic keeps its Messages API for prompt caching. All SDKs deferred-imported (app runs without them if unused).

**Consequences**:
- 5 provider clients total. `create_client(config)` factory dispatches by config.provider or returns `HybridAIClient` if hybrid routing configured.
- Cost estimates diverge per provider — `_COST_TABLE` in `config.py` needs manual tuning per model.
- GUI dropdown gained 3 items. Settings dialog "Model & endpoint" tab per-provider model IDs.
- Testing gap: only Anthropic + Ollama actually smoke-tested on this box (no other keys). OpenAI / Gemini / Moonshot verified only at construction + config-parsing level.

---

## 2026-09-21 — Hybrid AI routing with local classifier + budget cap + fallback chain

**Context**: Per-conversion image mix is diverse — a proposal has decorative circles, screenshot UIs, architecture diagrams, and photos. Sending all to Claude wastes tokens (photos don't need premium); sending all to Ollama loses quality (diagrams suffer). User wants "diagram → Claude, photo → Gemini, etc." with a budget cap.

**Options**:
- (A) Manual per-file provider choice. Too much friction.
- (B) Route by image kind, classified locally (no AI cost). Simple to reason about.
- (C) Route by AI-in-the-loop classification (send image to cheap AI first, then premium). Two-pass, defeats the purpose.

**Decision**: (B). New `image_heuristics.classify_image()` returns one of `decorative/photo/chart/screenshot/diagram/unknown` from cheap PIL analysis (edge density, distinct colors, white ratio). `HybridAIClient` uses that label + `config.provider_for_kind(kind)` to pick provider. Budget cap `ANYTOMD_COST_CAP_USD` triggers auto-downgrade to Ollama when hit. Fallback chain: on error, try Ollama (free) first, then any other cloud provider whose key exists.

**Consequences**:
- Predictable per-conversion cost — user knows diagram-heavy files cost more.
- Local classifier is imperfect (screenshots with white backgrounds sometimes labeled `diagram`); false-positives get high-quality treatment which is fine, false-negatives are the failure mode we care about — mitigated by conservative thresholds.
- Cost display in GUI status bar surfaces `est_cost_usd` from `HybridAIClient._estimated_cost_usd`.
- Fallback chain is silent — user only sees the final result. Errors that hit fallback logged via progress signal ("Anthropic error, retry via Ollama") in a future iteration.

---

## 2026-09-21 — Secure API key storage: Windows Credential Manager via `keyring`

**Context**: Storing API keys in plain-text `.env` has real risks: accidental git commit, backup exposure, screen-share leaks, cloud sync (OneDrive, iCloud). User asked for secure GUI input.

**Options**:
- (A) Session-only input (paste each time). No persistence. High friction.
- (B) Encrypted file with user-provided master password (Fernet/AES). User has to remember password.
- (C) OS credential store via `keyring` package. Uses OS-native crypto (Windows DPAPI, macOS Keychain, Linux Secret Service). No master password.
- (D) Custom encryption with hardcoded key. Security theater.

**Decision**: (C). New `anytomd/keystore.py` wraps `keyring`. On Windows, backend is `WinVaultKeyring` — encrypts via DPAPI using the user's Windows login as the key. `.env` still supported for backwards-compat and CI/scripts. Precedence: env var → keyring → empty.

**Consequences**:
- Docs must be honest: this protects against **casual disk theft, accidental commit, screen leak** — NOT against malware running as the same user (which can read the credential vault) or process memory dumps. Threat model documented in Settings → Info tab.
- Portable between machines requires re-entry (feature: prevents key exfil via cloud file sync).
- Adds `keyring>=25.0.0` dependency, ~10 sub-deps including pywin32-ctypes on Windows.
- GUI: Settings → API Keys tab shows source per provider (`📄 dari environment` / `🔑 tersimpan (keyring)` / `❌ belum ada`). Key never displayed after save — even a "peek" toggle in the input dialog only reveals during typing, not for retrieval.

---

## 2026-09-21 — GUI rombak: menubar + toolbar + hero drop zone + splitter + status bar

**Context**: Initial GUI had everything stacked vertically in a `QVBoxLayout` — drop zone at top, provider panel, options panel, buttons row at bottom (Browse/Save/Settings all at bottom), status label, preview. User feedback: "Settings di bawah tersembunyi. Browse juga di bawah. Gimana sih. UI/UX kurang bagus. Buat GUI yang rapi."

**Options**:
- (A) Just reorder widgets. Cosmetic, still cramped.
- (B) Sidebar-driven layout (VSCode/Figma style). Heavy for a small tool.
- (C) Standard desktop app pattern: `QMenuBar` + `QToolBar` + central content + `QStatusBar`. Familiar to Windows users.
- (D) Wizard-style (step 1 → 2 → 3). Bad for repeated use.

**Decision**: (C). `MainWindow` uses:
- `menuBar()` — File / Settings / Help (with keyboard shortcuts Ctrl+O, Ctrl+S, Ctrl+,, Ctrl+Q).
- `QToolBar` — Open · Save · Provider dropdown · spacer · Settings (always visible).
- `QSplitter(Qt.Vertical)` — top: hero drop zone (min 220px, dashed border, hover state, click-to-browse) + options row (2 group boxes side-by-side); bottom: preview area with Copy button. User can drag splitter to resize.
- `QStatusBar` — status message (left) + progress bar (right) + cost label (permanent right).
- Global QSS: subtle neutral palette, rounded borders, primary button blue.

All rarely-changed config (API keys, hybrid routing, model IDs, base URLs, cost cap) moved to `SettingsDialog` — modal, 5 tabs (API Keys / Hybrid routing / Model & endpoint / Advanced / Info). Main window is for **operational choices per conversion**; Settings is for **persistent config**.

**Consequences**:
- Primary action (Browse) elevated to hero drop-zone (click-to-browse) + toolbar Open button + File menu + Ctrl+O — 4 discovery paths.
- Preview area dominates the visual weight, matching its role as the output view.
- Provider switching between conversions stays in the toolbar (frequent action). Hybrid routing tucked into Settings (set once, rarely touched).
- QSS is minimal — heavy styling breaks on macOS/Linux. Current palette works with native controls beneath.
- `ProviderPanel` widget kept in code (embeddable) but unused in current MainWindow — could be re-added if we ever want a "quick advanced settings" sidebar.

---

## 2026-09-27 — PDF slide decks: page vision anchored on the text layer and the vector drawing

**Context**: Two real slide decks exported from LibreOffice Impress (4 and 14 pages, client material) came out of the PDF converter badly, with Ollama and with Anthropic alike: vector architecture diagrams became scattered words, tables without rulings were missed or mangled, two-column layouts interleaved, step labels (`1  CUSTOMER`) became `##` headings, and 742 "images" produced ~740 placeholder lines. A plain Claude read of the same PDFs was far better. The problems sit *before* the AI call, so a better model alone changed nothing.

**Options**:
- (A) Keep per-element extraction and add heuristics: borderless-table detection, reading-order clustering, vector-graph reconstruction into Mermaid. Deterministic, but every slide layout is a new special case.
- (B) Send each rendered page to a vision model and let it write the Markdown ("Claude-direct" inside the app).
- (C) (B), with the page's text layer attached so wording is copied rather than read off pixels, plus exact facts from the PDF drawing (legend colour per box, connectors with direction and labels), and machine checks on the answer.

**Decision**: (C), for slide decks only by default (`pdf_page_vision="auto"`: deck detected by app name or screen ratio, cloud model only).
- First runs of (B)+text layer showed the model's weak spots were exactly the fine visual facts: dashed vs solid purple borders (Phase 2 vs 3 put in the wrong column), arrowhead direction (I1–I6 reversed), and badge labels on arrows. All three are exact in the PDF, so `converters/pdf_vector.py` reads them and tags the text layer (`{legend: Phase 2}`, `CONNECTORS: "Source system" → "Platform…" (label "I1")`). Matching boxes to legend entries in code, not in the prompt, took the architecture slide from wrong to 71/71 fragments in the right phase column.
- The model then added a sentence of its own (a colour-coded explanation of the arrow badges) and a legend that isn't on the slide. The answer is now checked: coverage (text-layer words present, retry below 92%), invented words (retry above 2), Mermaid validity (autofix, then retry, then demote). One retry, better attempt kept.

**Consequences**:
- The two decks: 0.0–0.1% of words lost, no invented text, every Mermaid block parses in the real Mermaid parser, 18 pages in ~50 s with Anthropic (4 pages in parallel), ~46k input tokens (plus the cached system prompt) / ~7k output tokens.
- Offline (Ollama) is not covered by `auto`: a 7B VLM misreads dense slides. `always` exists for those who want to try; the text path got the cheap fixes instead (painted-only images, template images, silent placeholders, heading rule, whole-word table cells).
- Vector hints only exist for vector drawings. A slide pasted into the PDF as one bitmap gets the image and whatever text layer there is.
- Side-by-side independent panels are sometimes still rendered as a one-row table; the prompt asks for `###` sections, the model mostly complies.

---

## 2026-09-27 — Placeholders for skipped images are silent

**Context**: A 14-page deck produced ~700 `> *[Gambar dilewati (gambar dekoratif …)]*` lines and the same logo description on every page. Placeholders were meant to be honest about what was skipped; at that volume they bury the content.

**Decision**: `ImageAnalysis.silent` — decorative, too small, template (painted on ≥50% of pages), and repeats of an already-described image produce no Markdown. Placeholders stay where the user made a choice (`mode: none`, `diagrams_only`) or a limit hit (budget cap). Counts remain in the stats line.

**Consequences**: Output of the dedup fixture shrinks to text + one diagram. Anyone who wants to see every skip can still read the stats; a verbose mode can come back if asked.

## 2026-09-30 — Pictures of tables and text are transcribed; logos and icons are judged by the model

**Context**: A real 7-slide PPTX (numeric lookup tables pasted as bitmaps) came out with the key content missing: each table became a 3-sentence summary (a range and a trend instead of the values; one summary even misstated the row intervals), a key paragraph — black text on a transparent PNG — was skipped as "decorative", two logos were described at length on every slide, and PowerPoint's own icon name `Bullseye outline` was emitted as author alt text.

**Decision**:
- The describe prompt gets two content sections next to `MERMAID`: `TABLE` (every row and column, exact cells) and `TEXT` (a picture of text, word for word). A table answer without a valid table retries once, like Mermaid. Side-by-side halves with the same header are stacked in code, since the model does not reliably do it.
- `kind: decorative` from the model (logo, icon, pictogram, ornament) is silent. Its perceptual hash is remembered, so the same logo with another crop on later slides costs no call.
- Transparency is kept for upload and flattened properly for heuristics (white and black).
- PPTX: Office-generated alt text is not trusted; library icons and pictures marked decorative are dropped locally.

**Alternatives considered**: a position heuristic for logos (small picture in the header/footer band) — cheaper, but guesses about layout drop real content in the band; the model sees the picture. Tesseract cross-check of table digits — the tables are small, low-resolution bitmaps where OCR noise would trigger needless retries; the real deck was checked by hand instead (72/72 rows exact).

**Consequences**: One `DESCRIBE_MAX_TOKENS = 4096` (was 2048) so a 40-row table fits; Ollama keeps its larger context only for page transcription. A logo still costs one call at its first appearance. The icon regex is English (`outline` / `with solid fill`); icons inserted from a localized Office go to the model, which should answer decorative.

## 2026-09-30 — PPTX: layout from the XML, slide vision with placeholders

**Context**: Compared with a direct Kimi read of the same 7-slide deck, AnyToMD had every word but no structure: `## Slide N` and the title as plain text, `> Layout:` / `*(Placeholder: …)*` lines (107 words that are not on the slides), every multi-paragraph frame flattened to `-` bullets (auto numbering lost, "2." typed by hand kept as text), two-column slides read line by line across both columns, the soft line break `\v` written as an invisible character (the two words around it glued together), and a flowchart of bordered text boxes emitted as loose lines — the shape-diagram path only ran on slides without pictures and only took auto-shapes as nodes. Both tools also missed that the slide patches a text box with another word over one word of a bitmap sentence. Kimi in turn reordered a slide, paraphrased, invented headings and dropped the slide-3 title.

**Decision**:
- Deterministic layout from the XML (`pptx_shapes`, `pptx_layout`): real titles, real list structure (`buAutoNum`, `buChar`, hand-typed numbers, depth from `marL + indent`, continuations), XY-cut reading order, page-number boxes and number/footer/date placeholders dropped, soft breaks as spaces. No layout or placeholder lines; image blocks shrink to their content (tables, text) or one `> **Gambar (kind):**` line.
- Flowcharts straight from the connectors (`extract_flow`): glue ids first, box hit second, arrowheads for direction, block arrows, midpoint labels; text boxes are nodes. Runs on every slide, pictures or not.
- Text boxes on top of a picture belong to the picture: it is analysed as a crop of the rendered slide.
- Slide vision for cloud providers: rendered slide (PowerPoint via COM when available — the box has Office, no LibreOffice — else a PIL approximation) + text layer. Everything whose content is already exact (tables, charts, pictures, flowchart) enters as a `[[KIND n]]` placeholder and is filled in afterwards, so the model only decides structure; its copies of that content are dropped; below 92% coverage after the retry the slide keeps the deterministic output.

**Alternatives considered**: PPTX → PDF via PowerPoint and reuse the PDF page-vision path as is — least new code, but loses the XML's exact lists, connector ids and picture blobs, needs PowerPoint for every PPTX, and table bitmaps would be re-read from a downscaled page render. Letting the model transcribe tables/pictures inside slide vision — cheaper by the picture calls, but the verified per-picture transcription is more reliable than a second read from the full slide. `pywin32` for COM — PowerShell is always there and avoids COM apartment handling in the worker thread.

**Consequences**: With the default settings the deck converts in ~85 s (10 picture calls + 7 slide calls, ~22k input tokens for the slides); word-level: 877/877 slide words, 0 invented, the patched sentence right, the flowchart edge for edge. Without a cloud model (Ollama, or `never`) the deterministic path gives the same words and flowchart with flatter headings. PowerPoint must not be busy with a modal dialog; any failure falls back to PIL for the rest of the process. The PIL render ignores gradients, theme colours beyond `bg1/tx1`, rotation and picture cropping (`srcRect`).
