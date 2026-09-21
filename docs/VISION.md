# Vision

## Problem

Modern knowledge work produces documents in incompatible silos: PDFs from finance, PPTX from consultants, DOCX from legal, XLSX from ops, HTML from saved web pages, image screenshots from everywhere. When any of these needs to feed into a text-native workflow — a wiki, an LLM context window, a static-site generator, a review — the friction is manual and repetitive: open, copy-paste, reformat, describe the image in words, redraw the diagram, strip repeating page headers.

Naively delegating the whole job to a cloud LLM burns tokens on things that don't need a model — text extraction, tables, sheets — and even wastes tokens on image work by re-analyzing the same corporate logo on every page.

## What AnyToMD is

A local desktop tool that turns any of those documents into **one clean Markdown file** — no manual reformatting, no separate copies of images, no page furniture (headers, footers, page numbers) polluting the body.

Design principle: **text is truth for text; AI is only for what text can't capture.** All body text, tables, and spreadsheet data are extracted 100% offline with structural parsers. The AI is invoked only for images, and that call is filterable (dedup, skip-tiny, decorative detection). Where a page contains a picture, the AI describes it in Bahasa Indonesia, and — if it's a structural diagram — emits a runnable Mermaid block. **Native PPTX shape diagrams are extracted deterministically from the XML without any AI call at all.**

## Target user

A power user on Windows who:

- Regularly reads/produces mixed-format documents
- Wants to feed those documents into other tools (LLM prompts, note apps, static docs sites)
- Cares about output cleanliness (no leftover page numbers, no image-file dependencies)
- Has enough machine (Windows 11, ~16 GB RAM) to run either a local vision model (Ollama, ~8 GB VRAM) or use a cloud provider API key
- Wants control over cost vs quality tradeoff — routes premium (diagrams) to Claude, cheap (photos) to Gemini, decorative to skip
- Reads and writes Bahasa Indonesia and English fluently — output descriptions come out in Bahasa Indonesia

## What it is NOT

- Not a **document editor** — one-way conversion only.
- Not a **round-trip Markdown → source** tool. Output is a distillation, not a reversible transform.
- Not a **format converter** in the "same fidelity, different container" sense. It intentionally throws away styling, colors, exact layout — Markdown can't carry those.
- Not a **cloud service**. It's a local desktop app; cloud calls (Anthropic / OpenAI / Gemini / Moonshot) are optional per-image, and can be avoided entirely by using Ollama or setting image mode to `none`.
- Not **multi-user** or **collaborative**. Single user, single machine.
- Not a **summarizer / rewriter**. Body text is preserved verbatim.

## Design principles

1. **Text is truth for text; AI is only for what text can't capture.** Never send a text-extractable PDF page to the AI — that's waste and hallucination risk. Send the AI only images, and only ask it to (a) describe and (b) produce structured output (Mermaid) when appropriate.
2. **Deterministic beats VLM when the source is structured.** Native PPTX charts → markdown table from `chart.plots`, no AI. Native PPTX shape diagrams → Mermaid from shape XML endpoints, no AI. Author Alt Text on pictures → used verbatim, no AI. Only fall back to VLM when structure can't be recovered.
3. **Fail loud on structure, fail quiet on style.** If a Mermaid diagram would render broken, drop it silently and keep the description — a working `.md` is worth more than a broken diagram. But if a whole file fails to parse, surface the error in the GUI.
4. **Reproducible over pretty.** All AI calls use temperature 0.2. Rerunning on the same file should produce ~the same output. This matters because the user may re-run to fix small issues.
5. **Provider-swappable, cost-aware, budget-capped.** Five backends behind one Protocol. Hybrid routing lets each image kind go to a different provider. Budget cap in USD auto-downgrades to Ollama (or placeholder) once hit. No provider is baked into converters.
6. **User owns the API key.** No usage telemetry, no key-in-code. Keys stored via Windows Credential Manager (DPAPI) or `.env`, never in git. The user pays their Anthropic/OpenAI/Gemini/Moonshot bill directly.

## Success criteria

A conversion is successful if:

- Body text is extracted verbatim, without page furniture.
- Section numbering and heading hierarchy are captured (`1.0 Executive Summary` → `##`, `2.1 Financial Record` → `###`).
- Paragraphs merge cleanly across PDF block boundaries — no line breaks mid-sentence.
- Tables round-trip as Markdown tables (loose approximation for merged cells is OK — MD tables don't support merging).
- PPTX native charts become Markdown tables with actual numbers.
- PPTX shape diagrams become Mermaid `flowchart LR/TD` with correct labels.
- Images produce (a) a description in Bahasa Indonesia and (b) a Mermaid block when the image is a structural diagram. Mermaid must be syntactically valid (renderable). Semantic accuracy is a stretch goal; syntactic validity is a hard requirement.
- Screenshots with visible text have that text extracted via Tesseract and appended to the AI description.
- Scanned PDFs are OCR'd when in auto/always mode, using the user's chosen engine (Tesseract, PaddleOCR, AI Vision, or Hybrid).
- Header/footer/page-number text does not leak into the body.
- Multi-sheet spreadsheets produce one MD file, one section per sheet.
- The `.md` file opens without error in a standard renderer (VS Code preview, GitHub, Obsidian).

## Benchmark reference

On a 57-page RFP PDF (CoverGo Proposal), AnyToMD v1.0 achieved:

| Metric | AnyToMD | Microsoft markitdown | Claude direct (native PDF) |
|---|---|---|---|
| Headings captured | **104** | 0 | 77 |
| Table rows captured | **114** | 46 | 63 |
| PPTX shape diagram support | ✅ direct Mermaid | ❌ skipped | ✅ narrative |
| Scanned PDF support | ✅ 4 OCR engines | ❌ | ✅ |
| Cost (with Ollama) | $0 | $0 | ~57 page-vision calls |

## Explicit non-goals for v1

- No batch/queue mode. One file at a time via GUI.
- No CLI mode. GUI only. (CLI is a possible v1.1.)
- No support for `.doc` (legacy binary Word), `.xls` (legacy binary Excel), `.odt`/`.ods` (OpenDocument), or RTF.
- No support for audio transcription or YouTube URLs (markitdown covers those; not our target use case).
- No summarization or LLM-based rewriting of body text. Text is preserved verbatim.
- No cross-machine key sync. Keys stay in the OS credential store on the machine that saved them.

## What has already landed (that was originally out of scope)

Items that started as "not for v1" in earlier drafts but shipped anyway during the Sprints 1-4 push:

- **HTML conversion** (`converters/html.py`) — beautifulsoup4 + markdownify.
- **OCR for scanned PDFs** (`ocr.py`) — 4 engines × 3 trigger modes.
- **Direct PPTX shape → Mermaid** (`pptx_shape_mermaid.py`) — deterministic, zero AI.
- **Multi-provider cloud** — OpenAI, Gemini, Moonshot in addition to the original Anthropic + Ollama.
- **Hybrid routing per image kind** — was originally a "maybe" in ROADMAP.
- **Secure key storage** — Windows Credential Manager via `keyring`.

## Comparable / prior art

- **Microsoft's MarkItDown** (Python) — closest peer. Broader format list (audio, YouTube, EPub, ZIP), but no per-image AI description, no shape-diagram-to-Mermaid path, no OCR for scanned PDFs, no heading detection from font sizes. Naming: MarkItDown is why we picked "AnyToMD" instead of colliding.
- **Pandoc** — universal document converter, but treats images as opaque and doesn't do Mermaid generation.
- **Marker** (VikParuchuri/marker) — PDF-focused, produces MD with model-based layout analysis. Adjacent but PDF-only, no PPTX/DOCX/XLSX.
- **LlamaParse / Reducto / Unstructured.io** — cloud services for RAG ingestion. Similar goal but SaaS-only; AnyToMD is local-first with optional cloud vision.

AnyToMD's differentiators: **shape diagrams → Mermaid deterministically from XML**, **hybrid multi-provider routing** with budget cap, **secure key storage** via OS credential manager, and **honest cost visibility** in the status bar.
