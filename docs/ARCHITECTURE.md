# Architecture

## Component overview

```mermaid
flowchart TB
    subgraph UI[GUI layer]
        GUI[gui.py<br/>PySide6 MainWindow<br/>Drop zone + preview]
        DZ[DropZone widget]
        WORKER[ConvertWorker<br/>QThread]
    end

    subgraph CORE[Core orchestration]
        DISPATCH[converters/__init__.py<br/>dispatch by suffix]
        CFG[config.py<br/>.env loader]
        FACT[ai_client.create_client<br/>factory]
    end

    subgraph CONV[Format converters]
        PDF[pdf.py<br/>PyMuPDF]
        PPTX[pptx.py<br/>python-pptx]
        DOCX[docx.py<br/>python-docx]
        XLSX[xlsx.py<br/>openpyxl]
        IMG[image.py]
    end

    subgraph AI[Vision layer]
        RENDER[slide_render.py<br/>PIL rasterizer]
        ACLIENT[AnthropicClient]
        OCLIENT[OllamaClient]
        PIPE[Mermaid pipeline<br/>validate + autofix + retry]
    end

    subgraph EXT[External]
        ANTHROPIC[(Anthropic API)]
        OLLAMA[(Ollama server<br/>localhost:11434)]
    end

    GUI --> DZ
    GUI --> WORKER
    WORKER --> DISPATCH
    WORKER --> FACT
    FACT --> CFG
    FACT --> ACLIENT
    FACT --> OCLIENT
    DISPATCH --> PDF
    DISPATCH --> PPTX
    DISPATCH --> DOCX
    DISPATCH --> XLSX
    DISPATCH --> IMG
    PDF --> ACLIENT
    PDF --> OCLIENT
    PPTX --> RENDER
    PPTX --> ACLIENT
    PPTX --> OCLIENT
    DOCX --> ACLIENT
    DOCX --> OCLIENT
    IMG --> ACLIENT
    IMG --> OCLIENT
    ACLIENT --> PIPE
    OCLIENT --> PIPE
    ACLIENT --> ANTHROPIC
    OCLIENT --> OLLAMA
```

## Module responsibilities

| Module                            | Responsibility |
|-----------------------------------|----------------|
| `anytomd/__main__.py`             | Entry point; invokes `gui.run()`. |
| `anytomd/gui.py`                  | PySide6 UI. `MainWindow`, `DropZone`, `ConvertWorker`. Runs conversion off the UI thread. |
| `anytomd/config.py`               | Loads `.env` from project root (so shortcut launches work), exposes `Config` dataclass with provider + credentials. |
| `anytomd/ai_client.py`            | `AnthropicClient`, `OllamaClient`, factory, `ImageAnalysis` result type, Mermaid validate/autofix/retry pipeline. |
| `anytomd/slide_render.py`         | PIL rasterizer for PPTX native shapes (rectangle, rounded, oval, diamond, connector). |
| `anytomd/converters/__init__.py`  | Dispatcher: extension → `convert_*` function. |
| `anytomd/converters/pdf.py`       | PyMuPDF text extraction, header/footer heuristic, image extraction. |
| `anytomd/converters/pptx.py`      | Slide walk; text/table/layout emission; shape-diagram branch. |
| `anytomd/converters/docx.py`      | Body walk (paragraphs + tables interleaved); heading/list detection; inline image extraction. |
| `anytomd/converters/xlsx.py`      | Sheet iteration; cell rendering; trim + cap. |
| `anytomd/converters/image.py`     | Single-image AI call. |

## Data flow — a typical run

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant GUI as MainWindow
    participant W as ConvertWorker (QThread)
    participant D as dispatcher
    participant C as format converter
    participant A as AI Client
    participant P as Mermaid pipeline
    participant Ext as Anthropic / Ollama

    User->>GUI: drops file (or Browse)
    GUI->>GUI: create_client(config)
    GUI->>W: start(input_path, output_path, ai)
    W->>D: convert(path, ai, progress)
    D->>C: convert_pdf/pptx/docx/... (path, ai, progress)
    loop per page/slide/paragraph
        C->>C: extract text/tables
        alt image present
            C->>A: analyze_image(bytes, media, ctx)
            A->>Ext: chat/messages with image
            Ext-->>A: response text
            A->>P: parse + validate mermaid
            alt invalid mermaid + kind=diagram
                P->>A: retry with hint
                A->>Ext: second call
                Ext-->>A: response text
                A->>P: re-validate
            end
            P-->>A: ImageAnalysis(kind, desc, mermaid?)
            A-->>C: ImageAnalysis
            C->>C: append to_markdown_block()
        end
    end
    C-->>W: markdown string
    W->>W: write file
    W-->>GUI: finished(output_path, markdown)
    GUI->>User: preview + status
```

## Vision pipeline

Same for both providers. The only difference is transport: Anthropic uses base64+system-prompt-with-cache; Ollama uses raw bytes in the `images` field of `chat`.

```mermaid
flowchart TD
    A[image_bytes] --> B[_normalize_image<br/>convert to PNG,<br/>cap 1568px]
    B --> C{provider}
    C -->|anthropic| D[Anthropic Messages API<br/>system prompt cached<br/>ephemeral]
    C -->|ollama| E[Ollama chat API<br/>qwen2.5vl:7b<br/>temp=0.2]
    D --> F[raw text response]
    E --> F
    F --> G[_parse_sections<br/>KIND / DESCRIPTION / raw MERMAID]
    G --> H{raw mermaid?}
    H -->|no| Z[ImageAnalysis]
    H -->|yes| I[_validate_mermaid]
    I -->|valid| Z
    I -->|invalid| J[_autofix_mermaid loop<br/>5 iterations max]
    J --> K[_validate_mermaid]
    K -->|valid| Z
    K -->|invalid + kind=diagram| L[retry with error hint]
    K -->|invalid + other kind| M[drop mermaid,<br/>keep description]
    L --> N[second AI call<br/>with prev + reasons in prompt]
    N --> O[parse + validate + autofix]
    O --> Z
    M --> Z
```

## Mermaid quality pipeline

Purpose: hide inconsistency of VLM Mermaid syntax so the `.md` renders without errors. Description is always preserved; only the Mermaid block can be dropped.

### Bad-pattern regex (`ai_client._MERMAID_BAD_PATTERNS`)

Ordered list — validator returns first failure reason:

| Pattern | Rejects |
|---------|---------|
| `\w+\s+\[.+\]\s*(-->|--)` | `A [label] -->` — space between id and `[label]` |
| `\w+\s+\{.+\}\s*(-->|--)` | `A {label} -->` |
| `\w+\s+\(.+\)\s*(-->|--)` | `A (label) -->` |
| `(?<![\[\{\(|\w])\w+\?` | `Pass?` as bare identifier (allowed only inside `[]`, `{}`, `()`, or `|...|`) |
| `(?:-->|---)\s*\w+\s+\[` | `--> B [label]` — space between target id and its label |

Also fails if the first line doesn't start with a known Mermaid diagram type (`flowchart`, `graph`, `sequenceDiagram`, `classDiagram`, `stateDiagram`, `erDiagram`, `gantt`, `mindmap`, `journey`, `pie`, `gitGraph`, `timeline`, `quadrantChart`, `requirementDiagram`, `C4Context`, `block-beta`, `architecture-beta`).

### Autofix loop (`ai_client._autofix_mermaid`)

Runs to a fixed point (max 5 iterations) in this order per iteration:

1. **Strip bare `?`** from identifiers not inside brackets: `Pass?` → `Pass`.
2. **Label-before-arrow**: `X [label] -->` → `X -->|label|`.
3. **Label-after-arrow-target**: `--> X [label]` → `-->|label| X`.

The `?` strip runs first because otherwise it blocks rules 2/3 (their regex requires plain `\w*` on either side, which `?` isn't).

### Retry policy

Fires only when:
- `kind == "diagram"` (AI thinks the image IS a diagram, so we owe the user Mermaid)
- Raw Mermaid was produced and non-`NONE`
- Post-autofix mermaid is still `None`

Retry sends a second AI call with the same image plus an appended context containing (a) the previous invalid Mermaid verbatim and (b) the specific failure reasons. Model given one more shot. If it still fails, description-only is emitted.

Retry doubles latency for failed cases only. On our smoke fixtures with Ollama, most diagrams autofix without retry.

## PPTX shape-diagram flow (unique to this format)

```mermaid
flowchart TD
    A[Slide element] --> B{_visible_shapes}
    B --> C{has PICTURE?}
    C -->|yes| D[Normal flow:<br/>text + tables + layout desc<br/>+ analyze each picture]
    C -->|no| E{≥3 AUTO_SHAPE<br/>AND ≥1 LINE?}
    E -->|no| D
    E -->|yes| F[Shape-diagram branch]
    F --> G[Skip text extraction<br/>from shapes]
    G --> H[slide_render.render_slide_shapes]
    H --> I[Rasterize:<br/>rect / rounded / oval / diamond<br/>+ connectors with arrowheads]
    I --> J[PNG bytes]
    J --> K[ai.analyze_image with<br/>extra_context 'shape diagram']
    K --> L[Mermaid pipeline]
    L --> M[Emit ImageAnalysis block]
```

Text extraction is intentionally skipped in the shape-diagram branch because otherwise the shape labels ("Commit", "Build", ...) would appear twice: once as bullet-style paragraphs from the text-frame walk, and again as part of the AI's diagram description. See [DECISIONS.md § shape-diagram dedup](DECISIONS.md#pptx-shape-diagram-dedup).

## GUI threading model

```mermaid
sequenceDiagram
    participant UI as Qt main thread
    participant WT as QThread
    participant CV as convert()

    UI->>UI: user drops file
    UI->>UI: create AI client (may fail here → dialog)
    UI->>WT: moveToThread(worker); start()
    WT->>CV: convert(path, ai, progress=callback)
    loop per page/slide
        CV-->>UI: progress signal (queued connection)
        UI->>UI: update status label
    end
    alt success
        CV-->>WT: markdown string
        WT-->>UI: finished signal (output_path, markdown)
        UI->>UI: preview + enable Save-as
    else failure
        CV--xWT: exception
        WT-->>UI: failed signal (traceback)
        UI->>UI: error dialog
    end
    WT->>WT: quit()
    UI->>UI: teardown worker
```

Only one conversion at a time — the UI prevents starting a second while one is in progress. Progress messages are pushed via a Qt signal from the worker thread; they're queued to the UI thread (default `Qt::QueuedConnection` across threads).

## Provider factory

```mermaid
classDiagram
    class AIClient {
        <<Protocol>>
        +analyze_image(bytes, media_type, extra_context) ImageAnalysis
    }
    class AnthropicClient {
        -client: Anthropic
        -model: str
        +analyze_image()
    }
    class OllamaClient {
        -client: ollama.Client
        -model: str
        +analyze_image()
    }
    class ImageAnalysis {
        +kind: str
        +description: str
        +mermaid: str?
        +to_markdown_block(alt_hint) str
    }
    class Config {
        +provider: str
        +api_key: str
        +anthropic_model: str
        +ollama_host: str
        +ollama_model: str
        +is_ready: bool
        +display_model: str
    }
    AIClient <|.. AnthropicClient
    AIClient <|.. OllamaClient
    AnthropicClient --> ImageAnalysis
    OllamaClient --> ImageAnalysis
    Config --> AnthropicClient : constructs
    Config --> OllamaClient : constructs
```

`create_client(config)` picks the concrete class based on `config.provider`. Because `AIClient` is a `typing.Protocol`, converters take it structurally — no import cycles.

## Non-obvious cross-cutting behaviors

- **All image bytes are normalized to PNG** and downsized to max 1568px on the longest side before sending to any provider. Reduces both API cost and inference time; avoids format-support mismatch.
- **System prompt is cached (Anthropic)** via `cache_control: ephemeral` so a 4-image PDF pays the prompt cost once, not four times.
- **`.env` is loaded from project root**, not CWD. `config.py` computes `Path(__file__).parent.parent / ".env"`. This lets the app work when launched from a Start Menu shortcut whose working directory isn't the project.
- **Progress messages are user-facing strings in Bahasa Indonesia** (`"Page 1/3: teks"`, `"Slide 3: gambar 1/1"`). Not localized because the whole app is single-locale.
