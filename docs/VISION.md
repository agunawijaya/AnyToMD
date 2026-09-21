# Vision

## Problem

Modern knowledge work produces documents in incompatible silos: PDFs from finance, PPTX from consultants, DOCX from legal, XLSX from ops, image screenshots from everywhere. When any of these needs to feed into a text-native workflow — a wiki, an LLM context window, a static-site generator, a review — the friction is manual and repetitive: open, copy-paste, reformat, describe the image in words, redraw the diagram, strip repeating page headers.

## What AnyToMD is

A local desktop tool that turns any of those documents into **one clean Markdown file** — no manual reformatting, no separate copies of images, no page furniture (headers, footers, page numbers) polluting the body.

Where a page contains a picture or diagram, the tool asks an AI vision model to describe it in Bahasa Indonesia, and — if the picture is a structural diagram — emit a runnable Mermaid diagram inline. If the AI can't generate valid Mermaid, the description still stands.

## Target user

A power user on Windows who:

- Regularly reads/produces mixed-format documents
- Wants to feed those documents into other tools (LLM prompts, note apps, static docs sites)
- Cares about output cleanliness (no leftover page numbers, no image-file dependencies)
- Has enough machine (Windows 11, ~16 GB RAM) to run a local vision model **or** an internet connection for Anthropic API
- Reads and writes Bahasa Indonesia and English fluently — output descriptions come out in Bahasa Indonesia

## What it is NOT

- Not a **document editor** — one-way conversion only.
- Not a **round-trip Markdown → source** tool. Output is a distillation, not a reversible transform.
- Not an **OCR** tool for scanned PDFs. It relies on text extraction, not raster OCR. Scanned PDFs will surface as image blocks the AI can attempt to describe, but this isn't the primary path.
- Not a **format converter** in the "same fidelity, different container" sense. It intentionally throws away styling, colors, exact layout — Markdown can't carry those.
- Not a **cloud service**. It's a local desktop app; the only remote call is optional (Anthropic API when that provider is selected).
- Not **multi-user** or **collaborative**. Single user, single machine.

## Design principles

1. **Text is truth for text; AI is only for what text can't capture.** Never send a text-extractable PDF page to the AI — that's waste and hallucination risk. Send the AI only images, and only ask it to (a) describe and (b) produce structured output (Mermaid) when appropriate.
2. **Fail loud on structure, fail quiet on style.** If a Mermaid diagram would render broken, drop it silently and keep the description — a working `.md` is worth more than a broken diagram. But if a whole file fails to parse, surface the error in the GUI.
3. **Reproducible over pretty.** All AI calls use temperature 0.2. Rerunning on the same file should produce ~the same output. This matters because the user may re-run to fix small issues.
4. **Provider-swappable.** Anthropic and Ollama are behind the same interface. Neither is baked into converters. Ollama is the "no key, offline, worse quality" fallback; Anthropic is the "internet, quality" default.
5. **User owns the API key.** No usage telemetry, no key-in-code, `.env` is `.gitignore`d. The user pays their Anthropic bill directly.

## Success criteria

A conversion is successful if:

- Body text is extracted verbatim, without page furniture.
- Tables round-trip as Markdown tables (loose approximation for merged cells is OK — MD tables don't support merging).
- Images produce (a) a description in Bahasa Indonesia and (b) a Mermaid block when the image is a structural diagram. Mermaid must be syntactically valid (renderable). Semantic accuracy is a stretch goal; syntactic validity is a hard requirement.
- Header/footer/page-number text does not leak into the body.
- Multi-sheet spreadsheets produce one MD file, one section per sheet.
- The `.md` file opens without error in a standard renderer (VS Code preview, GitHub, Obsidian).

## Explicit non-goals for v1

- No batch/queue mode. One file at a time via GUI.
- No CLI mode. GUI only. (CLI is a possible v1.1 — see [ROADMAP](ROADMAP.md).)
- No PPTX shape → Mermaid via direct XML analysis. We rasterize and delegate to the VLM. Direct XML analysis is a stretch goal.
- No support for `.doc` (legacy binary Word), `.xls` (legacy binary Excel), `.odt`/`.ods` (OpenDocument), RTF, or HTML.
- No image OCR for scanned PDFs.
- No summarization or LLM-based rewriting of body text. Text is preserved verbatim.

## Comparable / prior art

- **Microsoft's MarkItDown** (Python) — closest peer. Broader format list, but no per-image AI description and no shape-diagram-to-Mermaid path. Naming: MarkItDown is why we picked "AnyToMD" instead of colliding.
- **Pandoc** — universal document converter, but treats images as opaque and doesn't do Mermaid generation.
- **Marker** (VikParuchuri/marker) — PDF-focused, produces MD with model-based layout analysis. Adjacent but PDF-only, no PPTX/DOCX/XLSX.

AnyToMD's differentiator: **shape diagrams → Mermaid** via the rasterize-then-VLM pipeline, and **provider-swappable** cloud/offline vision.
