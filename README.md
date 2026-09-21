# AnyToMD

## Objective

**Convert mixed-format documents into clean Markdown while spending as little AI as possible.**

Copy-paste from PDFs/PPTX/DOCX/XLSX into Markdown is repetitive: strip page furniture, rebuild tables, describe images by hand, redraw diagrams. Naively delegating the whole job to an LLM burns tokens on things that don't need a model — text extraction, tables, sheets — and even wastes tokens on image work by re-analyzing the same corporate logo on every page.

AnyToMD's design principle: **text is truth for text; AI is only for what text can't capture.** All body text, tables, and spreadsheet data are extracted 100% offline with structural parsers. The AI is only invoked for images, and even that call is opt-in filterable to skip duplicates, tiny decorations, or non-diagrams. PPTX shape diagrams and native charts are turned into Mermaid or markdown tables **deterministically without any AI call**.

### Benchmark (2026-09-21) — CoverGo RFP proposal, 57 pages

| Metric | AnyToMD (Ollama) | Microsoft markitdown | Claude direct |
|---|---|---|---|
| Time | 232s | 7s | ~30s |
| Headings captured | **104** | 0 | 77 |
| Table rows captured | **114** | 46 | 63 |
| Handles PPTX shape diagrams | **✅ (direct Mermaid)** | ❌ (skipped) | ✅ (narrative) |
| Handles scanned PDF | ✅ (4 OCR engines) | ❌ | ✅ |
| Cost | 0 (Ollama) or ~$0.20 (Anthropic hybrid) | 0 | $$$ (page-vision every page) |

For a 6-page report with a header logo on every page + 1 real diagram, this drops the AI bill from **7 image calls to 1** with two toggles enabled.

---

## Quick start

```powershell
git clone https://github.com/agunawijaya/AnyToMD.git
cd AnyToMD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Configure at least ONE provider (Anthropic recommended for quality)
# Option A: via .env (copy .env.example and fill in)
Copy-Item .env.example .env
notepad .env
# Option B: launch GUI and use Settings → API Keys (Ctrl+,)
python -m anytomd
```

Drag any PDF/PPTX/DOCX/XLSX/HTML/image into the drop zone, or press Ctrl+O.

## GUI at a glance

```
┌─ File   Settings   Help                            ← menu bar
├─ [📂 Open]  [💾 Save]  │  Provider [Claude ▼]  │  [⚙ Settings]
├─────────────────────────────────────────────────
│                                                 
│    ╔═════════════════════════════════════╗     ← hero drop zone
│    ║      Drop file here — or click      ║       (dashed border,
│    ║   PDF · PPTX · DOCX · XLSX · HTML   ║        click to browse)
│    ╚═════════════════════════════════════╝     
│                                                 
│  [ Filter gambar ]        [ OCR untuk PDF scan ]  ← options side-by-side
│                                                 
├──── draggable splitter ────────────────────────
│  Preview                              [📋 Copy]  ← preview grows to fill
│  ┌─ # Document Title                       ─┐  
│  │ ## Section 1                              │  
│  │ ...                                       │  
├─────────────────────────────────────────────────
│ Siap · Anthropic · Claude Sonnet 4.6    $0.012 ← status bar
```

Settings dialog has 5 tabs: **API Keys** (secure per-provider) · **Hybrid routing** (per image kind) · **Model & endpoint** · **Advanced** (budget cap) · **Info**.

Keyboard shortcuts: `Ctrl+O` open · `Ctrl+S` save · `Ctrl+,` settings · `Ctrl+Q` exit.

---

## Supported formats

Convert **PDF**, **PPTX**, **DOCX**, **XLSX**, **HTML**, and **PNG/JPG** files into clean Markdown.

- **PDF** → strips repeated header/footer/page numbers, keeps body text, **detects tables via pdfplumber** and interleaves them with text by y-position, describes embedded images. **Scanned/image-only PDFs** can be OCR'd (auto-detect or forced) with a choice of engine: Tesseract, PaddleOCR, AI vision, or hybrid.
- **PPTX** → per-slide content + **speaker notes** + layout description + **native charts as markdown tables (zero AI)** + Mermaid diagrams for pictures **and** for native shape diagrams (auto-rasterized). **Author-supplied Alt Text is used verbatim, bypassing the AI.** Slide number & footer placeholders skipped.
- **DOCX** → headings, paragraphs, lists, tables (as MD tables), inline images described. Word headers/footers/page numbers are outside the document body, so they're naturally skipped.
- **XLSX** → each sheet becomes its own `## Sheet: <name>` section with a Markdown table. Trailing empty rows/columns trimmed. Large sheets capped (5000 rows × 100 cols).
- **HTML/HTM** → saved web pages / articles: strips `<script>/<style>/<nav>/<footer>/<aside>`, converts body via `markdownify`. Anchors dropped on link-heavy pages. No AI call.
- **PNG/JPG/WEBP** → natural-language description in Bahasa Indonesia; if it's a structural diagram, Mermaid source is also emitted.

Lima backend untuk vision tasks — **plus hybrid mode** yang bisa route per-jenis-gambar ke provider berbeda:

- **`anthropic`** (default) — Claude `claude-sonnet-4-6`, kualitas terbaik untuk diagram info-dense.
- **`openai`** — ChatGPT `gpt-4o`, kualitas sangat baik, harga menengah.
- **`gemini`** — Google `gemini-2.5-flash`, paling murah dari cloud (~$0.002/image).
- **`moonshot`** — Kimi `moonshot-v1-32k-vision-preview` (Chinese vision LLM, OpenAI-compatible).
- **`ollama`** — offline `qwen2.5vl:7b` (~8 GB VRAM). Positioned sebagai **fallback / graceful degradation / testing**, bukan first-class. Kualitas VLM 7B lokal lebih rendah untuk diagram info-dense.

Provider dipilih via **dropdown di toolbar** untuk switching cepat, atau di Settings → Hybrid routing untuk konfigurasi per-jenis-gambar.

**Secure API key storage**: buka **Settings → API Keys** (Ctrl+,) untuk mengelola kunci per provider. Kunci disimpan terenkripsi via **Windows Credential Manager (DPAPI)** — bukan plain text di `.env`. Precedence: environment variable → keyring → kosong. `.env` tetap didukung untuk backwards-compat dan skenario CI/script. Kunci tidak pernah ditampilkan di UI setelah disimpan.

**Hybrid mode** — set per-jenis-gambar ke provider berbeda:
```
ANYTOMD_PROVIDER=anthropic                       # default (text + fallback)
ANYTOMD_IMAGE_PROVIDER_FOR_DIAGRAM=anthropic     # diagram → Claude (best)
ANYTOMD_IMAGE_PROVIDER_FOR_CHART=anthropic
ANYTOMD_IMAGE_PROVIDER_FOR_SCREENSHOT=openai     # UI screenshot → GPT-4o
ANYTOMD_IMAGE_PROVIDER_FOR_PHOTO=gemini          # foto → Gemini (paling murah)
ANYTOMD_COST_CAP_USD=0.50                        # auto-stop kalau melebihi
```

Local classifier (`image_heuristics.classify_image`) menentukan jenis image sebelum routing — zero cost, deterministic. Budget cap kalau tercapai → auto-downgrade ke Ollama (kalau tersedia) atau emit placeholder.

---

## Install

```powershell
cd E:\Projects\AnyToMD
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure

Semua konfigurasi via file **`.env`** yang **wajib ditaruh di root project**: `E:\Projects\AnyToMD\.env` (satu folder dengan `requirements.txt`). File ini di-load otomatis oleh `python-dotenv` saat app start.

```powershell
cd E:\Projects\AnyToMD
Copy-Item .env.example .env
notepad .env
```

`.env` sudah masuk `.gitignore` — **jangan pernah commit file ini** (berisi API key).

### Mode Anthropic (default, kualitas terbaik)

1. **Dapatkan API key**: buka https://console.anthropic.com/settings/keys → *Create Key* → copy string yang diawali `sk-ant-...`.
2. **Isi `.env`**:
   ```
   ANYTOMD_PROVIDER=anthropic
   ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ANYTOMD_MODEL=claude-sonnet-4-6
   ```
   Model pilihan: `claude-sonnet-4-6` (balance kualitas & biaya) atau `claude-opus-4-7` (paling akurat, lebih mahal).

3. **Alternatif** — daripada `.env`, bisa juga set environment variable Windows (persistent):
   ```powershell
   [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")
   ```
   Atau per-session: `$env:ANTHROPIC_API_KEY = "sk-ant-..."` sebelum jalankan app.

### Mode Ollama (offline, tidak butuh API key)

1. **Isi `.env`**:
   ```
   ANYTOMD_PROVIDER=ollama
   OLLAMA_HOST=http://localhost:11434
   ANYTOMD_OLLAMA_MODEL=qwen2.5vl:7b
   ```
   Model lain: `qwen2.5vl:32b` (24 GB VRAM), `qwen2.5vl:72b` (48+ GB), `llama3.2-vision:11b`, `minicpm-v:8b`.

2. **Pull model + jalankan server** (models disimpan di `E:\OllamaModels`):
   ```powershell
   $env:OLLAMA_MODELS = "E:\OllamaModels"
   & "E:\Ollama\ollama.exe" pull qwen2.5vl:7b
   & "E:\Ollama\ollama.exe" serve
   ```
   `ollama serve` harus tetap jalan selama app dipakai. Kalau Ollama Desktop sudah auto-start, biasanya server sudah jalan di `localhost:11434` — cukup pastikan model sudah di-pull.

## Run (GUI)

```powershell
python -m anytomd
```

Then drag & drop a file into the window, or use **Browse**. The `.md` output is saved next to the source file; use **Simpan .md…** to save elsewhere.

---

## How it works

| Stage                  | Tool                                                    |
|------------------------|---------------------------------------------------------|
| PDF text + images      | PyMuPDF (`fitz`)                                        |
| PDF tables             | pdfplumber (rulings → cells, interleaved with text)     |
| Header/footer stripper | position (top/bottom 10%) + repeat-frequency ≥ 35%      |
| PPTX parsing           | python-pptx (shapes, placeholders, tables, pictures, **charts**, **notes**) |
| PPTX shape rasterizer  | Pillow — rectangles/rounded/oval/diamond + connectors   |
| PPTX alt-text          | Read `<p:cNvPr descr="…">` — bypass AI if ≥ 12 chars     |
| DOCX parsing           | python-docx (body walk preserves para/table interleave) |
| XLSX parsing           | openpyxl (`data_only=True` for formula results)         |
| HTML → Markdown        | beautifulsoup4 (strip nav/script/style) + markdownify   |
| Image → Markdown       | Anthropic Messages API **atau** Ollama chat API (VLM)   |
| Mermaid safety net     | validator + auto-fix loop + one retry with error hint   |
| GUI                    | PySide6 (Qt) with drag-drop and background worker thread |

Untuk mode Anthropic, system prompt di-cache (`cache_control: ephemeral`) supaya analisis banyak gambar di satu file jadi lebih murah.

## Token efficiency

### Yang selalu gratis (zero AI, by design)

| Task | Handled by |
|------|-----------|
| PDF body text + header/footer/page# stripping | PyMuPDF + heuristik posisi/frekuensi |
| **PDF tables (bordered)** | **pdfplumber `find_tables()` — 0 AI call** |
| PPTX text, tabel, layout, judul, footer/slide-# skip | python-pptx |
| **PPTX native chart → tabel MD** | **python-pptx `chart.plots[0]` — 0 AI call** |
| **PPTX speaker notes** | **python-pptx `notes_slide` — 0 AI call** |
| **PPTX picture dengan Alt Text penulis** | **XML `<p:cNvPr descr>` — 0 AI call, teks penulis dipakai verbatim** |
| DOCX heading, paragraf, list, tabel, header/footer skip | python-docx (header/footer di section XML, otomatis ter-skip) |
| XLSX semua sheet | openpyxl — **0 AI call** dijamin |
| **HTML → MD** | **beautifulsoup4 + markdownify — 0 AI call** |
| Image resize + PNG normalize sebelum kirim | Pillow (mengurangi image tokens ~4×) |
| Mermaid syntax validate + auto-fix | regex lokal — retry ke AI hanya kalau autofix gagal |

Anthropic system prompt di-cache (`cache_control: ephemeral`) — dokumen multi-gambar hanya bayar prompt sekali penuh, sisanya 90% diskon.

### AI usage per format (default behavior)

| Format | AI calls per file |
|--------|-------------------|
| DOCX text-only (tidak ada gambar) | **0** |
| PPTX text/tabel saja | **0** |
| PDF text-only | **0** |
| **XLSX apapun** | **0** — selalu |
| PDF/PPTX/DOCX dengan gambar | 1 per gambar |
| PPTX dengan shape diagram | 1 per slide shape-diagram (rasterized) |
| Standalone PNG/JPG | 1 |

### Optimasi opt-in untuk file image-heavy

Empat filter tambahan tersedia — semua **default OFF** supaya perilaku existing tidak berubah. Bisa diaktifkan lewat **GUI panel "Optimasi token AI"** sebelum konversi, atau global lewat `.env`:

| Optimasi | Env var | Efek |
|----------|---------|------|
| **Dedup by hash** | `ANYTOMD_IMAGE_DEDUP=true` | SHA-256 setiap image bytes; kalau sama persis dengan yang sudah dianalisis, reuse cache — tidak panggil AI lagi. Membunuh 100% waste dari logo/header berulang. |
| **Skip tiny (pixel)** | `ANYTOMD_IMAGE_MIN_DIM=64` | Skip gambar dengan sisi terpanjang < N pixel. Cocok untuk filter watermark, bullet icon, corner logo. |
| **Skip tiny (bytes)** | `ANYTOMD_IMAGE_MIN_BYTES=3000` | Skip gambar dengan raw bytes < N. Alternatif filter ukuran. |
| **Mode `diagrams_only`** | `ANYTOMD_IMAGE_MODE=diagrams_only` | Heuristik lokal (palet warna + edge density + white-ratio) menentukan apakah gambar mirip diagram; kalau bukan (foto, screenshot), skip. Zero-cost prefilter. |
| **Mode `none`** | `ANYTOMD_IMAGE_MODE=none` | Skip semua gambar, emit placeholder `[Gambar dilewati]`. **0 AI call** untuk seluruh file. |

Skipped gambar dapat placeholder ringkas di output:
```
> *[Gambar dilewati (32x32, < 64px min) — page1_img1]*
```

### Measured impact

Fixture: `tests/fixtures/dedup_test.pdf` — 6-page PDF dengan logo 32×32 di header setiap halaman + 1 diagram unik di halaman 6 = **7 image occurrences**.

| Configuration               | AI calls | Waktu (Ollama Qwen2.5-VL 7B) |
|-----------------------------|----------|-------------------------------|
| Baseline (semua OFF)        | 7        | 18.5 s                        |
| **Dedup ON**                | **2**    | 5.6 s   (-70%)                |
| **Skip < 64 px**            | **1**    | 3.5 s   (-81%)                |
| Diagrams-only heuristic     | 7*       | 15.6 s                        |
| **Full skip (mode=none)**   | **0**    | 0.0 s                         |
| **Dedup + skip-tiny combined** | **1** | 3.8 s   (-79%)                |

*Heuristik tidak menolak logo (palet sedikit + banyak putih → mirip diagram). Diagrams-only lebih berguna untuk filter foto/screenshot, bukan logo — untuk logo pakai skip-tiny.

### Rekomendasi kombinasi

| Skenario | Setting |
|----------|---------|
| PDF laporan berulang (logo per halaman + sedikit diagram real) | **Dedup ON + skip < 64 px** — ~80% hemat |
| Presentasi PPTX (logo per slide + beberapa diagram) | **Dedup ON** |
| DOCX legal/technical (gambar minimal, semua penting) | Default (semua OFF) |
| Urgent, tidak mau bayar token | **Mode: none** — 0 AI call, hanya text + tabel |
| PDF scan (image-only) | **OCR auto + Tesseract** — 0 AI call, teks langsung dikstrak |

Verifikasi sendiri dengan:
```powershell
python tests\run_options_smoke.py
python tests\run_ocr_smoke.py
```

## OCR untuk PDF scanned

PDF hasil scan (image-only, tidak ada teks selectable) sekarang bisa di-OCR sehingga isi teksnya masuk ke `.md` tanpa perlu deskripsi visual dari AI vision.

### 4 engine — pilih di GUI atau `.env`

| Engine       | Offline? | Butuh install               | Kecepatan | Akurasi teks Latin |
|--------------|----------|-----------------------------|-----------|--------------------|
| **Tesseract**| ✅       | Tesseract binary + pytesseract | Cepat  | Baik (kadang salah tafsir Q/O/0/l) |
| **PaddleOCR**| ✅       | `pip install paddleocr` (~500 MB) | Sedang | Sangat baik untuk layout kompleks |
| **AI Vision**| ❌ (kecuali Ollama) | tidak ada tambahan | Lambat | Sangat baik, tapi kadang paraphrase |
| **Hybrid**   | ✅→❌   | Tesseract + AI provider siap | Cepat kalau Tesseract cukup | Auto-eskalasi ke AI untuk page sulit |

### 3 mode trigger

| Mode      | Kapan OCR jalan |
|-----------|-----------------|
| **never** (default) | tidak pernah — perilaku existing |
| **auto**  | hanya untuk page yang tampak scan: teks extraction kosong (<30 char) + ada image yang cover ≥ 50% area page |
| **always**| paksa OCR setiap page, walaupun sudah ada teks extractable |

### Setup Tesseract di Windows

1. Download installer: https://github.com/UB-Mannheim/tesseract/wiki (pilih `.exe` terbaru)
2. Install ke default location (biasanya `C:\Program Files\Tesseract-OCR\`)
3. Saat install, pilih language packs "Indonesian" dan "English" (atau download `ind.traineddata` + `eng.traineddata` ke folder `tessdata` kalau sudah terlanjur install)
4. Pastikan `tesseract.exe` ada di PATH, atau set `TESSERACT_CMD` di `.env`

### Bahasa

Format Tesseract, kode 3-huruf, gabung dengan `+`:
- `ind+eng` (default, direkomendasikan)
- `eng` (Inggris saja, lebih cepat)
- `ind+eng+ara` (butuh install `ara.traineddata`)

### Hasil test (fixture `scanned.pdf`, 2 halaman, teks bilingual EN+ID)

| Config                    | AI calls | Waktu   | Kualitas |
|---------------------------|----------|---------|----------|
| OCR OFF (baseline)        | 2        | 27.0 s  | Deskripsi visual, bukan teks |
| **OCR auto + Tesseract**  | **0**    | 1.7 s   | Teks lengkap, Q ↔ A mistake |
| OCR always + Tesseract    | 0        | 1.4 s   | Sama seperti auto |
| OCR auto + Hybrid         | 0        | 1.5 s   | Sama seperti Tesseract (fallback tidak fire) |
| OCR auto + AI (Ollama)    | 2        | 13.0 s  | Teks lengkap, sedikit paraphrase |

**Kesimpulan**: untuk PDF scan, **Tesseract auto** adalah pilihan optimal — 100% offline, 0 AI token, ~16x lebih cepat dari baseline, dan output lebih relevan (teks vs deskripsi visual).

## Project layout

```
AnyToMD/
├── anytomd/
│   ├── __main__.py          # `python -m anytomd`
│   ├── ai_client.py         # AnthropicClient + OllamaClient + factory
│   ├── config.py            # .env loader
│   ├── gui.py               # PySide6 desktop UI
│   ├── slide_render.py      # PIL rasterizer for PPTX shape diagrams
│   └── converters/
│       ├── __init__.py      # dispatcher
│       ├── pdf.py       # + pdfplumber (tables)
│       ├── pptx.py      # + charts, notes, alt-text
│       ├── docx.py
│       ├── xlsx.py
│       ├── html.py      # NEW
│       └── image.py
├── requirements.txt
├── .env.example
└── README.md
```

## License

Personal use. No warranty.
