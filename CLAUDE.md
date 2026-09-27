# CLAUDE.md — AnyToMD project guide

> This file is auto-loaded when Claude Code is invoked in this directory. Read it first when
> the user says "lanjutkan" (continue). It's the map, not the territory — for details, follow
> the links into `docs/`.

## What this project is

**AnyToMD** — a Windows desktop app (PySide6 GUI) that converts documents into clean Markdown.

Supported inputs → single `.md` output:
- **PDF** (text + strip header/footer/page#; describes embedded images)
- **PPTX** (slide text, layout, tables, pictures; **shape diagrams rasterized** with PIL then AI-analyzed)
- **DOCX** (headings, paragraphs, lists, tables, inline images)
- **XLSX** (each sheet → `## Sheet: <name>` + MD table)
- **PNG / JPG / WEBP / GIF / BMP** (AI description; Mermaid if diagram)

Five AI backends, selectable via GUI dropdown or `.env`:
- `anthropic` (default, `claude-sonnet-4-6`) — highest quality
- `openai` (`gpt-4o`) — ChatGPT vision, OpenAI SDK
- `gemini` (`gemini-2.5-flash`) — Google, cheapest cloud option
- `moonshot` (`moonshot-v1-32k-vision-preview`) — Kimi, OpenAI-compatible API
- `ollama` (`qwen2.5vl:7b`) — offline fallback, no key needed, ~8 GB VRAM. Positioned as fallback/graceful-degradation, not first-class.

**Hybrid routing** (Sprint 3): each image kind (diagram/chart/screenshot/photo/decorative) can be routed to a different provider. GUI dropdowns per kind. Env vars: `ANYTOMD_IMAGE_PROVIDER_FOR_DIAGRAM=openai` etc.

**Secure key storage** (2026-09-21): API keys can be stored via Windows Credential Manager (or macOS Keychain / Linux Secret Service) instead of `.env`. GUI has per-provider `Set…` / `Ubah…` / `Hapus` buttons. Precedence: env var (shell / `.env`) → keyring → empty. Module: `anytomd/keystore.py`.

## Current state (2026-09-27, PDF slide decks)

**Functional, end-to-end tested with Ollama and Anthropic, benchmarked against Microsoft markitdown and Claude-direct on a real 57-page RFP PDF** (AnyToMD surpasses both on heading count and table row capture, see `_bench/`) **and on two real slide decks exported to PDF** (page vision now on par with a direct Claude read; see [DECISIONS 2026-09-27](docs/DECISIONS.md)).

- ✅ **PDF slide decks → page vision** (2026-09-27) — deck detected by app name / screen ratio; with a cloud provider each page is rendered and transcribed by the vision model, anchored on the text layer + exact vector hints (`converters/pdf_vector.py`: legend colour per box, connectors with direction and badge labels). Checks: text coverage, invented words, Mermaid validity → one retry. `ANYTOMD_PDF_PAGE_VISION=auto|always|never`, GUI group "PDF slide → AI per halaman".
- ✅ PDF images: only **painted** images count (`get_image_info`, not `get_images`), template images (on ≥50% of pages) skipped, images placed by y, skipped images **silent** (no placeholder lines), descriptions in the document's language (`anytomd/lang.py`).
- ✅ PDF text-strategy tables rebuilt from whole words when pdfplumber's bbox cuts words (CoverGo lost 1.3% of its words before).

- ✅ 6 converters implemented (`pdf`, `pptx`, `docx`, `xlsx`, `image`, `html`)
- ✅ Two backends via factory + **hybrid router** (Sprint 3): route diagrams to Anthropic, photos to Ollama, all in one conversion
- ✅ PySide6 GUI with drag-drop + background worker + preview
- ✅ Mermaid safety net: validator + autofix loop + retry-with-hint
- ✅ **PPTX shape-diagram → Mermaid direct from XML** (Sprint 4 E1) — deterministic node/edge extraction, `flowchart LR`/`TD`, correct shape mapping (rectangle → `[]`, diamond → `{}`, oval → `(())`, rounded → `([])`), edge labels from nearby textboxes. Falls back to raster+VLM if the graph can't be recovered.
- ✅ PPTX native charts → markdown table (no AI)
- ✅ PPTX speaker notes → `### Notes`
- ✅ PPTX author Alt Text passthrough
- ✅ PDF tables via pdfplumber with column-collapse (empty phantom cols) + disjoint-column merge + text-strategy fallback
- ✅ **PDF heading auto-detection** (Sprint 2 A1) — font-size clustering + numbering pattern recognition. Numbered headings (`1.0 Executive Summary`) become `##`; big-bold prose becomes `###`/`####`.
- ✅ **PDF paragraph merge across blocks** (Sprint 2 A2) — same-x consecutive prose blocks with no sentence terminator on prev merge into single paragraph.
- ✅ **PDF nested bullets from indent** (Sprint 2 D2) — bullet chars normalized to `-`, indent tier → nested markdown depth.
- ✅ PDF outline / bookmarks passthrough — `doc.get_toc()` becomes authoritative heading hierarchy when present.
- ✅ Cover/divider page skip (Sprint 1 A7) — pages with <20 chars text and no meaningful image get compact placeholder.
- ✅ Decorative image classifier (Sprint 1 B1) — solid blobs / gradient circles never touch the AI.
- ✅ **Hybrid cloud AI routing** (Sprint 3): `ANYTOMD_IMAGE_PROVIDER_FOR_{DIAGRAM,CHART,SCREENSHOT,PHOTO}` env vars. Local classifier picks provider per image. Budget cap via `ANYTOMD_COST_CAP_USD`. Fallback chain on error.
- ✅ **Screenshot OCR** (Sprint 4 D1) — screenshot images get Tesseract-extracted on-screen text appended after AI narration.
- ✅ OCR for scanned PDFs — 4 engines × 3 trigger modes
- ✅ Token-saving optimizations (dedup, skip tiny, mode filter, full skip)
- ✅ Auto-load `.env` from project root
- ✅ Fixtures + smoke test script in `tests/`
- ✅ Anthropic backend smoke-tested end-to-end (2026-09-27; key in Windows Credential Manager, `claude-sonnet-4-6`)
- ✅ Offline PDF unit checks: `tests/run_pdf_unit.py` (44 checks, fake AI) + cloud smoke `tests/run_page_vision_smoke.py`
- ⚠️  GUI code compiles but **not automated-tested** (Qt GUIs hard to headless-test; `OptionsPanel` is instantiated offscreen in checks, `QT_QPA_PLATFORM=offscreen QT_QPA_FONTDIR=C:/Windows/Fonts` renders a readable screenshot)
- ⚠️  Slide decks with **Ollama** still use the text path (vector diagrams / borderless tables stay flat); PPTX/DOCX image descriptions still default to Bahasa Indonesia

### Benchmark vs alternatives (CoverGo Proposal PDF, 57 pages)

| Metric | AnyToMD (v2, Ollama) | markitdown 0.1.7 | Claude direct |
|---|---|---|---|
| Time | 232s | 7s | ~30s |
| Headings (## and #) | **104** | 0 | 77 |
| Table rows | **114**\* | 46 | 63 |
| Image narration | 40 (Ollama) | 0 (none) | 57 (Claude native) |
| Cost | 0 | 0 | ~57 pages × page vision |

\* 2026-09-27: part of these rows had words cut at the table edge (1.3% of the document's words lost). After the fix a no-AI run gives 122 rows with 0.0% word loss. Measure fidelity with a word-loss check (PDF body words missing from the .md), not only with row counts.

For prioritized next work, see [ROADMAP.md](docs/ROADMAP.md).

## Project layout

```
E:\Projects\AnyToMD\
├── CLAUDE.md                    ← you are here
├── README.md                    ← user-facing install & config
├── requirements.txt
├── .env.example                 ← copy to .env and fill in
├── .gitignore                   ← .env is ignored
├── anytomd/
│   ├── __main__.py              # entry: `python -m anytomd`
│   ├── config.py                # .env loader, provider selection
│   ├── ai_client.py             # AnthropicClient + OllamaClient + OptimizedAIClient + factory + Mermaid pipeline
│   ├── options.py               # ConvertOptions: dedup, skip-tiny, mode, ocr_*
│   ├── image_heuristics.py      # local diagram-vs-photo classifier
│   ├── lang.py                  # document-language guess (descriptions in the doc's language)
│   ├── ocr.py                   # Tesseract + PaddleOCR + AI-vision + Hybrid backends
│   ├── slide_render.py          # PIL-based PPTX shape rasterizer
│   ├── gui.py                   # PySide6 desktop UI
│   └── converters/
│       ├── __init__.py                  # dispatcher by extension
│       ├── pdf.py                       # PyMuPDF + pdfplumber + heading/para/list detection + page vision
│       ├── pdf_vector.py                # vector hints for page vision: legend colours, connectors
│       ├── pptx.py                      # python-pptx (+ chart/notes/alt-text)
│       ├── pptx_shape_mermaid.py        # E1: shape XML → Mermaid deterministik
│       ├── docx.py                      # python-docx
│       ├── xlsx.py                      # openpyxl
│       ├── html.py                      # beautifulsoup4 + markdownify
│       └── image.py                     # single-image path
├── docs/
│   ├── VISION.md                # why this exists
│   ├── SPECIFICATION.md         # per-format behavior contracts
│   ├── ARCHITECTURE.md          # diagrams + module map + Mermaid pipeline
│   ├── TEST_PLAN.md             # fixtures + scenarios + how to run
│   ├── DECISIONS.md             # ADR-style rationale log
│   └── ROADMAP.md               # known limits + next work
└── tests/
    ├── make_fixtures.py         # generates fixtures/*.{png,pdf,pptx,docx,xlsx}
    ├── run_smoke.py             # forces Ollama, runs each converter
    ├── run_pdf_unit.py          # offline PDF checks (no AI): slide decks, page vision routing, validator
    ├── run_page_vision_smoke.py # page vision on slide_deck.pdf with a cloud provider
    ├── preview_render.py        # renders shape_diagram.pptx to PNG for inspection
    ├── fixtures/                # auto-generated inputs
    └── output/                  # smoke-test outputs (.md + preview .png)
```

## Common commands (PowerShell)

```powershell
# One-time setup
cd E:\Projects\AnyToMD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env       # fill in ANTHROPIC_API_KEY or leave and use ANYTOMD_PROVIDER=ollama

# Run the GUI
python -m anytomd

# Regenerate fixtures + run all smoke tests (Ollama)
.venv\Scripts\python.exe tests\make_fixtures.py
.venv\Scripts\python.exe tests\run_smoke.py

# Just render the shape-diagram fixture to a PNG (for inspection)
.venv\Scripts\python.exe tests\preview_render.py

# Verify token-saving optimizations against the dedup fixture
.venv\Scripts\python.exe tests\run_options_smoke.py

# Offline PDF checks (no AI, must print ALL PASSED) + page vision with a cloud key
.venv\Scripts\python.exe tests\run_pdf_unit.py
.venv\Scripts\python.exe tests\run_page_vision_smoke.py
```

## Environment specifics on this box

- Python 3.11 at `C:\Users\aguna\AppData\Local\Programs\Python\Python311\python.exe`
- Ollama at `E:\Ollama\ollama.exe`, models at `E:\OllamaModels`
- Model already pulled: **`qwen2.5vl:7b`** (6.0 GB, vision-capable)
- Ollama server listens on `http://localhost:11434` (auto-started by Ollama Desktop)
- Tesseract 5.5 at `E:\Program Files\Tesseract-OCR\tesseract.exe`, language packs installed: `ind`, `eng`, `osd`, `script\Arabic`
- PowerPoint (Office16) installed at `C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE` — currently **not used** by the app, but available if we later want higher-fidelity slide render than PIL
- No LibreOffice on this box

## Configuration cheat sheet (`.env`)

```
# Pilih provider utama: anthropic | openai | gemini | moonshot | ollama
ANYTOMD_PROVIDER=anthropic

# Anthropic (default, kualitas terbaik)
ANTHROPIC_API_KEY=sk-ant-...
ANYTOMD_MODEL=claude-sonnet-4-6

# OpenAI (ChatGPT)
OPENAI_API_KEY=sk-...
ANYTOMD_OPENAI_MODEL=gpt-4o

# Google Gemini
GEMINI_API_KEY=...
ANYTOMD_GEMINI_MODEL=gemini-2.5-flash

# Moonshot (Kimi)
MOONSHOT_API_KEY=...
ANYTOMD_MOONSHOT_MODEL=moonshot-v1-32k-vision-preview
# MOONSHOT_BASE_URL=https://api.moonshot.cn/v1

# Ollama (offline fallback)
OLLAMA_HOST=http://localhost:11434
ANYTOMD_OLLAMA_MODEL=qwen2.5vl:7b

# Hybrid image routing (opsional)
# ANYTOMD_IMAGE_PROVIDER_FOR_DIAGRAM=anthropic
# ANYTOMD_IMAGE_PROVIDER_FOR_PHOTO=gemini
# ANYTOMD_COST_CAP_USD=0.50

# PDF slide deck → AI per halaman: auto (slide deck + cloud) | always | never
# ANYTOMD_PDF_PAGE_VISION=auto
```

## When the user says "lanjutkan"

1. Read this file (already loaded — you're reading it).
2. Skim [`docs/ROADMAP.md`](docs/ROADMAP.md) for the current priority list.
3. Check `git status` — the project is a git repo on `main`.
4. Ask the user what they want to work on next only if `docs/ROADMAP.md` doesn't make it obvious.

## Non-obvious things to know

- **`.env` loading** happens from the project root via `Path(__file__).parent.parent`, so the app works when launched from Start Menu shortcuts too. Do NOT change `config.py` to `load_dotenv()` bare — it would break launcher use.
- **Mermaid quality on Ollama is inconsistent.** The safety-net pipeline (validate → autofix → retry) hides most VLM syntax bugs. If a diagram output looks wrong, first check `qwen2.5vl:7b` isn't the bottleneck — try `ANYTOMD_PROVIDER=anthropic` and see if it fixes.
- **PPTX shape diagrams** are rasterized by our own PIL renderer (`slide_render.py`), not by PowerPoint or LibreOffice. Fidelity is intentionally rough — the goal is "recognizable enough for a VLM," not pixel-perfect. See [DECISIONS.md](docs/DECISIONS.md).
- **PPTX pictures with author Alt Text bypass the AI entirely.** Alt Text ≥ 12 chars is trusted and emitted verbatim with a distinct block header (`— alt text penulis`). If your PPT authors set good alt text, most images will never touch the model.
- **PPTX native charts bypass the AI.** Categories + series values are read directly from `python-pptx` and emitted as a markdown table. Chart-shape presence also disables the shape-diagram detector on that slide (chart + connector would otherwise false-trigger it).
- **PDF table detection needs visible rulings.** `pdfplumber.find_tables()` reads horizontal/vertical lines. Tables that rely on whitespace alignment (no borders) only come through the strict text-strategy fallback — otherwise the text falls through to normal paragraph extraction. On slide decks with a cloud provider, page vision recovers them.
- **`page.get_images()` lies for slide exports.** It lists the page's *resources*; Impress/PowerPoint exports share one resource dictionary, so every page "has" every image of the deck. Use `_drawn_images` (`get_image_info`) for what is actually painted.
- **Page vision checks are the safety net, keep them.** The model reliably misreads fine visual facts (dashed vs solid border, arrowhead direction) and occasionally adds explanatory sentences. Exact facts come from `pdf_vector.py`; coverage / invented-word / Mermaid checks trigger the retry. If output quality drops, look at the text layer the model received (`_run_page_vision` builds it) before touching the prompt.
- **Client documents stay out of the repo.** `_bench/` is gitignored; tests use synthetic fixtures (`slide_deck.pdf` from `make_fixtures.py`). Don't quote client content in docs or test strings.
- **`Path.write_text` on Windows writes CRLF.** The repo uses `core.autocrlf=true`, so it's harmless for git, but a Python one-off that edits files with a `\r` inside a string (e.g. `tests\run_…` in a non-raw string) plants a stray carriage return. Prefer the Edit tool for docs.
- **DOCX header/footer stripping** is free — python-docx's body walk naturally excludes them (they live in section XML parts). No filter needed. Same reasoning for PPTX slide-number placeholders.
- **PDF header/footer stripping** is heuristic (position + repeat-frequency), not free. See [SPECIFICATION.md § PDF](docs/SPECIFICATION.md#pdf).
- **HTML converter aggressively strips `<nav>/<header>/<footer>/<aside>`** by default. If someone hands you an HTML export where the "real" content lives inside `<aside>`, this will nuke it. Adjust `_STRIP_TAGS` in `converters/html.py`.
- **The output file collision bug** in `tests/run_smoke.py` was fixed by including the input extension in the output filename (`sample_pdf.md` vs `sample_pptx.md`). If you add a new format with a shared stem, this pattern already handles it.

## User preferences (from prior sessions)

- Bahasa Indonesia in chat responses.
- Terse; no preamble; execute directly.
- Prefers to confirm before large decisions but wants proactive execution once confirmed.
- Test-first mindset: after any change, actually run it. Don't claim success without seeing green.

## Adding a new converter (recipe)

1. Create `anytomd/converters/<format>.py` with `def convert_<fmt>(path: Path, ai: AIClient, progress: ProgressFn | None) -> str`.
2. Register in `anytomd/converters/__init__.py` dispatcher.
3. Add extension to `SUPPORTED` in `anytomd/gui.py` (drop zone + file dialog filter + hint text).
4. Add a fixture generator in `tests/make_fixtures.py`.
5. Add a case in `tests/run_smoke.py`.
6. Add dependency to `requirements.txt`.
7. Update: `README.md` (bullets + how-it-works table), `docs/SPECIFICATION.md`, `docs/ROADMAP.md` if partial.
8. Run `tests/make_fixtures.py` then `tests/run_smoke.py`; verify output.
