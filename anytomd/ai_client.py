from __future__ import annotations

import base64
import dataclasses
import io
import re
from dataclasses import dataclass
from typing import Literal, NamedTuple, Protocol

from PIL import Image

from .config import Config, cost_per_call, cost_per_anthropic_call

ImageKind = Literal["diagram", "photo", "chart", "table", "text", "screenshot", "illustration",
                    "decorative", "other"]

DESCRIBE_SYSTEM = (
    "You analyze images extracted from documents (PDF, PPTX) or standalone image files. "
    "Your job: (1) classify the image, (2) produce a concise factual description in the output "
    "language named in the context (default: Bahasa Indonesia), "
    "(3) if it's a table, transcribe it in full, "
    "(4) if it's a diagram/flowchart/graph/architecture with clear nodes and connections, generate valid "
    "Mermaid syntax that reproduces it.\n\n"
    "Kinds:\n"
    "- table: text or numbers laid out in rows and columns, with or without borders.\n"
    "- decorative: a logo, brand mark, icon, pictogram, ornament or background that adds no "
    "information to the document. An icon stays decorative when it depicts people, arrows or "
    "connections: a diagram has labelled boxes. DESCRIPTION is then a few words only "
    "(e.g. `Logo ACME`).\n"
    "- text: mostly running text (paragraphs or a list pasted as a picture). DESCRIPTION is "
    "then one sentence naming what the text is about; the TEXT section holds the text itself "
    "(see below).\n\n"
    "Rules for TEXT:\n"
    "- For KIND text: the text transcribed word for word in its own language — not a summary, "
    "not translated — with numbered or bulleted items as a Markdown list.\n"
    "- For any other kind, TEXT is the literal word NONE.\n\n"
    "Rules for TABLE:\n"
    "- For KIND table: a GitHub Markdown table with every row and every column. Copy each cell "
    "exactly as shown: digits, decimal commas, symbols (… – %), capitalisation. Never round, "
    "summarize or skip rows. A header that wraps onto two lines is one cell. DESCRIPTION is "
    "then one sentence naming what the table shows.\n"
    "- Several tables side by side with the same header are ONE table continued: output a "
    "single table, rows in reading order (down the left table, then down the next one). "
    "Tables with different headers → separate Markdown tables, one blank line between them.\n"
    "- For any other kind, TABLE is the literal word NONE.\n\n"
    "Rules for Mermaid:\n"
    "- Only produce Mermaid when the image is genuinely a structural diagram (flowchart, sequence, class, "
    "ER, state, gantt, mindmap, architecture). Do NOT invent Mermaid for photos, screenshots, charts of "
    "data (bar/line/pie), or decorative graphics.\n"
    "- Prefer `flowchart TD` unless a different type clearly fits.\n"
    "- Use short bare-word node IDs (A, B, C, N1, node1...). NEVER put `?`, spaces, or punctuation "
    "in the node ID itself.\n"
    "- Node shapes: `[label]` = rectangle, `{label}` = diamond/decision, `([label])` = stadium, "
    "`((label))` = circle.\n"
    "- Edge labels go INSIDE `|...|` between arrow parts, e.g. `A -->|Yes| B`. NEVER `A [Yes] --> B`.\n"
    "- Do not wrap in markdown code fences — the caller adds those.\n"
    "- If you cannot produce valid Mermaid, return NONE.\n\n"
    "CORRECT full example for a decision:\n"
    "  flowchart TD\n"
    "    A[Start] --> B[Process]\n"
    "    B --> C{OK?}\n"
    "    C -->|Yes| D[End]\n"
    "    C -->|No| E[Retry]\n\n"
    "WRONG examples to avoid:\n"
    "  A [Yes] --> B         (space between id and bracket)\n"
    "  A --> Pass? [X]        (`?` in bare id; label with space)\n"
    "  A([Yes]) --> B         (accidentally made A a stadium-node)\n\n"
    "Output STRICT format (five sections separated by lines containing only `---`):\n"
    "KIND: <one of: diagram, photo, chart, table, text, screenshot, illustration, decorative, "
    "other>\n"
    "---\n"
    "DESCRIPTION: <2-5 sentences in the output language describing what the image shows>\n"
    "---\n"
    "TABLE: <Markdown table(s), or the literal word NONE>\n"
    "---\n"
    "TEXT: <the transcribed text, or the literal word NONE>\n"
    "---\n"
    "MERMAID: <mermaid source, or the literal word NONE>"
)

TABLE_RETRY_HINT = (
    "Your previous answer classified the image as a table but its TABLE section was missing "
    "or not a valid Markdown table ({reason}). Regenerate the whole response following the "
    "STRICT format; in TABLE output every row as `| cell | cell |`, with a `| --- | --- |` "
    "line under the header row and the same number of cells in every row."
)

RETRY_HINT_TEMPLATE = (
    "Your previous Mermaid attempt was:\n"
    "```\n{prev}\n```\n"
    "It FAILED validation because: {reasons}. "
    "Regenerate the whole response following the STRICT format. In the MERMAID section, "
    "output syntactically valid Mermaid. Common fixes: "
    "(1) put edge labels inside `|...|`, e.g. `A -->|Yes| B` (never `A [Yes] --> B`). "
    "(2) node IDs must be bare words (letters/digits/underscore) — if the label has `?` or spaces, "
    "put it INSIDE the shape brackets, e.g. `C{{OK?}}` (id is `C`, label is `OK?`). "
    "(3) never put a space between a node id and its label bracket."
)

# Page vision: one rendered page (typically a slide exported to PDF) + its
# text layer → Markdown. The text layer pins the wording; the image supplies
# the structure (tables without rulings, vector diagrams, reading order).
PAGE_SYSTEM = (
    "You convert ONE slide (from a PPTX file, or a presentation-style PDF page) into clean "
    "GitHub-flavored Markdown.\n\n"
    "You receive (1) an image of the rendered page and (2) the page's TEXT LAYER extracted "
    "from the file: one entry per text block, prefixed with its position as fractions of the "
    "page width (x) and height (y); continuation lines of a block are indented. The tags "
    "below come straight from the file's drawing and are exact:\n"
    "- `{legend: X}` at the end of an entry: the box around this text is drawn in the "
    "colour/border of legend entry X. Use X as the item's category (e.g. its phase).\n"
    "- `{box Sn}` at the end of an entry: the box around this text has style Sn, listed under "
    "BOX STYLES after the entries.\n"
    "- CONNECTORS, after the entries, lists every arrow or line drawn on the page, "
    "tail → head, by the text of the boxes at its ends, with the short text sitting on the "
    "connector as `label`. Take edges, arrow directions and edge labels from it, not from "
    "your reading of the image.\n"
    "The tags themselves never appear in your output.\n"
    "- `[[PICTURE n]]`, `[[TABLE n]]`, `[[CHART n]]` and `[[DIAGRAM n]]` entries are "
    "placeholders: the exact content of that element (a table, a transcribed picture, a "
    "flowchart) is inserted there afterwards. Write each placeholder exactly once, alone on "
    "its own line, where the element belongs in the reading order. Never transcribe, "
    "describe or redraw these elements yourself, and write no Figure line for them.\n\n"
    "Wording:\n"
    "- Copy wording from the text layer exactly, keeping its capitalisation. Do not "
    "paraphrase, translate, summarize, correct, or add words. Every text-layer entry must "
    "appear in your output. Entries may already carry Markdown — `**bold**`, list markers "
    "`1.` / `-` with the slide's own numbering, indented continuation lines: keep them.\n"
    "- Never add content that is not on the page: no invented legends, captions, "
    "explanations, summaries or colour names.\n"
    "- Take STRUCTURE from the image: reading order, grouping, which label belongs to which "
    "box, table rows and columns, arrows.\n\n"
    "Structure:\n"
    "- Slide title → `## `. Panel or section titles inside the slide → `### `. Never use `#`. "
    "A small kicker label above the title → an italic line directly under the `##` title.\n"
    "- Anything laid out as rows × columns, with or without borders, whose cells in a row "
    "belong together (a row label, or aligned attributes of one item) → a Markdown table with "
    "every row and every column. Join a label that wraps across lines into one cell. Leave a "
    "cell empty where the slide has nothing. If the slide has no header row, use an empty one.\n"
    "- Side-by-side panels, cards or columns that are independent of each other (lists or "
    "short texts that merely sit next to each other) → one `###` section per panel, left to "
    "right, each with its own list or paragraph. Not a table.\n"
    "- Boxes connected by arrows or lines (flowchart, pipeline, journey, architecture with "
    "connections) → a ```mermaid block. Use `flowchart LR` or `flowchart TB` to match the "
    "layout. Node ids are short bare words (n1, n2, ...). Put EVERY label in double quotes: "
    "n1[\"Title<br/>sub-label\"]. Inside a label: join text that merely wraps onto one line, "
    "use `<br/>` only between a box's title and its smaller sub-label, no Markdown (`**`), no "
    "`\\n`, no `\"`. Boxes inside a panel go in `subgraph g1[\"Panel title\"] ... end` — "
    "containment is a subgraph, never an edge. Draw ONLY the arrows and lines visible in the "
    "image, each pointing the way its arrowhead points; an arrow that ends on a panel border "
    "targets the subgraph. Edge labels: n1 -- \"label\" --> n2.\n"
    "- A grid of boxes WITHOUT arrows where colour or border style marks a category from a "
    "legend (e.g. layers × phases) → a Markdown table with one row per row label and one "
    "column per legend category; each cell lists that row's boxes of that category, "
    "separated by `; `.\n"
    "- Colour that carries meaning given ON THE PAGE (a legend entry via `{legend: X}`, or a "
    "highlighted option the page recommends) → write that meaning next to each item it applies "
    "to, e.g. `(Phase 1)` or `(recommended)`, inside Mermaid labels too. A legend drawn on the "
    "page → one line `**Legend:** <its own labels>`.\n"
    "- Numbered cards or steps → a numbered list. Bullets → `-` lists, nested by indentation.\n"
    "- A call-out bar of the form `LABEL  sentence` → `**Label:** sentence`.\n"
    "- A photo, illustration, or screenshot that carries content → one line `> **Figure:** "
    "<short description in the language of the page>`. Never use Figure lines for shapes, "
    "boxes or arrows (those are the diagram) or for logos, icons, decorative shapes and "
    "backgrounds (ignore them).\n"
    "- Ignore page numbers and running footers such as 'Confidential'.\n\n"
    "Output only the Markdown for this page: no preamble, no closing remarks, no code fence "
    "around the whole answer."
)

PAGE_RETRY_TEMPLATE = (
    "Your previous answer for this page had problems:\n{problems}\n\n"
    "Produce the full Markdown for the page again, following every rule, and fix these problems."
)

# A transcribed table (a few dozen rows) needs more room than a description.
DESCRIBE_MAX_TOKENS = 4096

# Rough multiplier over the per-image cost estimate for one page-vision call
# (bigger prompt with the text layer, several times more output tokens).
PAGE_COST_FACTOR = 4.0


@dataclass
class ImageAnalysis:
    kind: ImageKind
    description: str
    mermaid: str | None
    skipped: bool = False
    skip_reason: str = ""
    # Silent results (decorative, tiny, repeated) produce no Markdown at all —
    # a placeholder per logo/background only buries the real content.
    silent: bool = False
    table: str | None = None  # Markdown table(s) transcribed from a table image
    text: str | None = None   # text transcribed from a picture of text

    def to_markdown_block(self, alt_hint: str = "image") -> str:
        """Markdown for the picture where it sits in the document.

        A table or a picture of text *is* document content: it comes out as
        that content alone. Anything else is one `> **Gambar (kind):**` line
        with the description, plus the Mermaid source for a diagram."""
        if self.silent:
            return ""
        if self.skipped:
            return f"> *[Gambar dilewati ({self.skip_reason}) — {alt_hint}]*"
        if self.table:
            return self.table.strip()
        if self.text:
            return self.text.strip()
        desc = " ".join(self.description.split())
        parts = [f"> **Gambar ({self.kind}):** {desc}"]
        if self.mermaid:
            parts.extend(["", "```mermaid", self.mermaid.strip(), "```"])
        return "\n".join(parts)

    @classmethod
    def skipped_result(cls, reason: str, silent: bool = False) -> "ImageAnalysis":
        return cls(kind="other", description="", mermaid=None,
                   skipped=True, skip_reason=reason, silent=silent)


@dataclass
class PageTranscription:
    markdown: str
    coverage: float           # share of distinct text-layer words present in the output
    retried: bool = False
    invalid_mermaid: int = 0  # blocks demoted to plain code fences
    input_tokens: int = 0
    output_tokens: int = 0


class AIClient(Protocol):
    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis: ...


# Optional capability, implemented by every built-in client:
#   transcribe_page(png_bytes, text_layer, extra_context="") -> PageTranscription
#   is_local: bool   (True = offline model; page vision stays opt-in for those)


# ---------- Anthropic ----------

class AnthropicClient:
    is_local = False

    def __init__(self, config: Config):
        if not config.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY tidak ada. Set di .env.")
        from anthropic import Anthropic  # deferred import
        self.client = Anthropic(api_key=config.api_key)
        self.model = config.anthropic_model

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        png_bytes = _normalize_image(image_bytes)
        return _analyze_with_retry(self._call, png_bytes, extra_context)

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        return _transcribe_page_with_retry(self._complete, _normalize_image(png_bytes),
                                           text_layer, extra_context)

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        return self._complete(png_bytes, DESCRIBE_SYSTEM, _describe_user_text(extra_context),
                              DESCRIBE_MAX_TOKENS)[0]

    def _complete(self, png_bytes: bytes, system: str, user_text: str,
                  max_tokens: int) -> tuple[str, int, int]:
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    },
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        usage = getattr(resp, "usage", None)
        # input_tokens excludes the cached system prompt; count it too.
        tokens_in = sum(int(getattr(usage, k, 0) or 0) for k in
                        ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        return text, tokens_in, int(getattr(usage, "output_tokens", 0) or 0)


# ---------- Ollama ----------

class OllamaClient:
    is_local = True

    def __init__(self, config: Config):
        try:
            import ollama  # deferred import
        except ImportError as e:
            raise RuntimeError(
                "Paket 'ollama' belum terinstall. Jalankan: pip install ollama"
            ) from e
        self._ollama = ollama
        self.client = ollama.Client(host=config.ollama_host)
        self.model = config.ollama_model

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        png_bytes = _normalize_image(image_bytes)
        return _analyze_with_retry(self._call, png_bytes, extra_context)

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        return _transcribe_page_with_retry(self._complete, _normalize_image(png_bytes),
                                           text_layer, extra_context)

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        return self._complete(png_bytes, DESCRIBE_SYSTEM, _describe_user_text(extra_context),
                              DESCRIBE_MAX_TOKENS)[0]

    def _complete(self, png_bytes: bytes, system: str, user_text: str,
                  max_tokens: int) -> tuple[str, int, int]:
        options = {"temperature": 0.2, "num_predict": max_tokens}
        if max_tokens >= PAGE_MAX_TOKENS:
            # Page transcription: image + text layer + long answer overflow
            # Ollama's small default context window.
            options["num_ctx"] = 16384
        try:
            resp = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text, "images": [png_bytes]},
                ],
                options=options,
            )
        except self._ollama.ResponseError as e:
            if "not found" in str(e).lower():
                raise RuntimeError(
                    f"Model '{self.model}' belum ada di Ollama. "
                    f"Jalankan: ollama pull {self.model}"
                ) from e
            raise
        text = resp["message"]["content"] if isinstance(resp, dict) else resp.message.content
        return text or "", 0, 0


# ---------- OpenAI (ChatGPT) ----------

class OpenAIClient:
    """OpenAI Chat Completions with vision. Supports Azure/proxy via base_url."""

    _api_key_attr = "openai_api_key"
    _model_attr = "openai_model"
    _base_url_attr = "openai_base_url"
    _label = "OpenAI"
    is_local = False

    def __init__(self, config: Config):
        try:
            from openai import OpenAI  # deferred
        except ImportError as e:
            raise RuntimeError(
                f"Paket 'openai' belum terinstall. Jalankan: pip install openai"
            ) from e
        api_key = getattr(config, self._api_key_attr, "")
        if not api_key:
            raise RuntimeError(f"{self._label} API key kosong.")
        base_url = getattr(config, self._base_url_attr, "") or None
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = getattr(config, self._model_attr)

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        png_bytes = _normalize_image(image_bytes)
        return _analyze_with_retry(self._call, png_bytes, extra_context)

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        return _transcribe_page_with_retry(self._complete, _normalize_image(png_bytes),
                                           text_layer, extra_context)

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        return self._complete(png_bytes, DESCRIBE_SYSTEM, _describe_user_text(extra_context),
                              DESCRIBE_MAX_TOKENS)[0]

    def _complete(self, png_bytes: bytes, system: str, user_text: str,
                  max_tokens: int) -> tuple[str, int, int]:
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64}",
                            },
                        },
                    ],
                },
            ],
        )
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        usage = getattr(resp, "usage", None)
        return (text,
                int(getattr(usage, "prompt_tokens", 0) or 0),
                int(getattr(usage, "completion_tokens", 0) or 0))


# ---------- Moonshot (Kimi) — OpenAI-compatible ----------

class MoonshotClient(OpenAIClient):
    """Moonshot Kimi vision — reuses OpenAI SDK with a different base_url."""
    _api_key_attr = "moonshot_api_key"
    _model_attr = "moonshot_model"
    _base_url_attr = "moonshot_base_url"
    _label = "Moonshot"


# ---------- Google Gemini ----------

class GeminiClient:
    is_local = False

    def __init__(self, config: Config):
        try:
            from google import genai  # deferred (google-genai package)
            from google.genai import types as _types  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "Paket 'google-genai' belum terinstall. "
                "Jalankan: pip install google-genai"
            ) from e
        if not config.gemini_api_key:
            raise RuntimeError("Gemini API key kosong (GEMINI_API_KEY).")
        self._genai = genai
        self._types = _types
        self.client = genai.Client(api_key=config.gemini_api_key)
        self.model = config.gemini_model

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        png_bytes = _normalize_image(image_bytes)
        return _analyze_with_retry(self._call, png_bytes, extra_context)

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        return _transcribe_page_with_retry(self._complete, _normalize_image(png_bytes),
                                           text_layer, extra_context)

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        return self._complete(png_bytes, DESCRIBE_SYSTEM, _describe_user_text(extra_context),
                              DESCRIBE_MAX_TOKENS)[0]

    def _complete(self, png_bytes: bytes, system: str, user_text: str,
                  max_tokens: int) -> tuple[str, int, int]:
        cfg = self._types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=max_tokens,
        )
        resp = self.client.models.generate_content(
            model=self.model,
            contents=[
                self._types.Content(
                    role="user",
                    parts=[
                        self._types.Part(text=user_text),
                        self._types.Part(
                            inline_data=self._types.Blob(
                                mime_type="image/png",
                                data=png_bytes,
                            )
                        ),
                    ],
                ),
            ],
            config=cfg,
        )
        usage = getattr(resp, "usage_metadata", None)
        return (getattr(resp, "text", "") or "",
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0))


# ---------- Optimization wrapper ----------

class OptimizedAIClient:
    """Wraps a real AI client with token-saving filters.

    Filters (all opt-in via ConvertOptions):
      - full-skip (image_mode="none")
      - min-bytes / min-dim (skip tiny)
      - diagram-only prefilter (image_mode="diagrams_only")
      - dedup by SHA-256 of image bytes
      - with skip_decorative: pictures that look like one the model already
        called decorative (the same logo on every slide, re-exported with
        another crop or size) are skipped without a call
    Stats are counted on `options._stats` so the GUI can show savings.
    """

    def __init__(self, inner: "AIClient", options):  # options: ConvertOptions
        self.inner = inner
        self.options = options
        self._cache: dict[str, ImageAnalysis] = {}
        self._decorative_hashes: list[tuple[int, float]] = []

    @property
    def is_local(self) -> bool:
        return getattr(self.inner, "is_local", True)

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        # Page renders are never filtered: the caller decided the page needs it.
        return self.inner.transcribe_page(png_bytes, text_layer, extra_context)

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        opts = self.options
        stats = opts._stats

        # 1. Full skip mode
        if opts.image_mode == "none":
            stats["skipped_mode_none"] += 1
            return ImageAnalysis.skipped_result("mode: none")

        # 2. Min bytes
        if opts.image_min_bytes > 0 and len(image_bytes) < opts.image_min_bytes:
            stats["skipped_tiny"] += 1
            return ImageAnalysis.skipped_result(
                f"{len(image_bytes)} B < {opts.image_min_bytes} B min", silent=True
            )

        # 3. Min dim (needs decoding — do it lazily)
        if opts.image_min_dim > 0:
            try:
                from PIL import Image as _PImg
                import io as _io
                img = _PImg.open(_io.BytesIO(image_bytes))
                if max(img.size) < opts.image_min_dim:
                    stats["skipped_tiny"] += 1
                    return ImageAnalysis.skipped_result(
                        f"{img.size[0]}x{img.size[1]}, < {opts.image_min_dim}px min", silent=True
                    )
            except Exception:
                pass  # if PIL can't open, let AI try

        # 4a. Decorative filter (solid blobs, gradient circles) — kills wasted
        # AI calls on cover-page filler. Cheap: ~10ms per image.
        phash = None
        if opts.skip_decorative:
            from .image_heuristics import is_decorative, is_icon_sized, perceptual_hash, same_picture
            if is_icon_sized(image_bytes):
                stats["skipped_decorative"] = stats.get("skipped_decorative", 0) + 1
                return ImageAnalysis.skipped_result("ikon kecil", silent=True)
            if is_decorative(image_bytes):
                stats["skipped_decorative"] = stats.get("skipped_decorative", 0) + 1
                return ImageAnalysis.skipped_result("gambar dekoratif (blob/lingkaran solid)",
                                                    silent=True)
            phash = perceptual_hash(image_bytes)
            if phash and any(same_picture(phash, h) for h in self._decorative_hashes):
                stats["skipped_decorative"] = stats.get("skipped_decorative", 0) + 1
                return ImageAnalysis.skipped_result("logo/ikon yang sama sudah dinilai AI",
                                                    silent=True)

        # 4b. Diagram-only prefilter
        if opts.image_mode == "diagrams_only":
            from .image_heuristics import looks_like_diagram
            if not looks_like_diagram(image_bytes):
                stats["skipped_not_diagram"] += 1
                return ImageAnalysis.skipped_result("bukan diagram (heuristik lokal)")

        # 5. Dedup
        cache_key = None
        if opts.image_dedup:
            import hashlib
            cache_key = hashlib.sha256(image_bytes).hexdigest()
            cached = self._cache.get(cache_key)
            if cached is not None:
                stats["skipped_duplicate"] += 1
                # Already described at its first occurrence — repeating the same
                # paragraph (a logo on every page) is pure noise.
                return dataclasses.replace(cached, silent=True)

        # 6. Actually call the real client
        stats["sent"] += 1
        result = self.inner.analyze_image(image_bytes, media_type, extra_context)
        # Sprint 3 (C4): if we're wrapping a HybridAIClient, sync its running
        # cost + anthropic-call count into per-conversion stats so GUI can show.
        inner = self.inner
        if isinstance(inner, HybridAIClient):
            stats["est_cost_usd"] = round(inner.estimated_cost_usd, 4)
            stats["anthropic_calls"] = inner.anthropic_calls
        if cache_key is not None:
            self._cache[cache_key] = result
        if result.kind == "decorative" and not result.skipped:
            stats["ai_decorative"] = stats.get("ai_decorative", 0) + 1
            if phash:
                self._decorative_hashes.append(phash)
        return result


# ---------- Hybrid router ----------

class HybridAIClient:
    """Sprint 3 (C1, C2, C4, C5): local-classify → route to per-kind provider,
    with fallback chain and cost cap.

    - Classifies the image locally via `image_heuristics.classify_image` (zero
      cost). Uses that kind to pick a provider from `config.image_provider_by_kind`
      (falls back to `config.image_provider`, then `config.provider`).
    - Tracks estimated cost per Anthropic call and stops routing to Anthropic
      once `config.cost_cap_usd` is hit (falls back to Ollama or emits a
      "budget cap reached" placeholder).
    - On network / API error, tries the other provider once as a fallback.
    """

    def __init__(self, config: Config):
        self.config = config
        self._clients: dict[str, AIClient] = {}
        # Per-provider call counter — used both for cost accounting and for
        # deciding whether the system prompt has been "cached" yet (Anthropic).
        self._calls_by_provider: dict[str, int] = {}
        self._estimated_cost_usd = 0.0
        # Page vision runs pages concurrently; guard the counters.
        import threading
        self._lock = threading.Lock()

    @property
    def is_local(self) -> bool:
        # Slide pages are diagram-heavy, so they follow the diagram route.
        return self.config.provider_for_kind("diagram") == "ollama"

    def transcribe_page(self, png_bytes: bytes, text_layer: str,
                        extra_context: str = "") -> PageTranscription:
        provider = self._pick_provider("diagram", factor=PAGE_COST_FACTOR)
        if provider == "capped":
            raise RuntimeError(f"budget cap ${self.config.cost_cap_usd:.2f} tercapai")
        try:
            result = self._get_client(provider).transcribe_page(png_bytes, text_layer, extra_context)
            self._account(provider, factor=PAGE_COST_FACTOR)
            return result
        except Exception as primary_err:
            for alt in self._fallback_order(provider):
                try:
                    result = self._get_client(alt).transcribe_page(png_bytes, text_layer,
                                                                   extra_context)
                    self._account(alt, factor=PAGE_COST_FACTOR)
                    return result
                except Exception:
                    continue
            raise primary_err

    def _get_client(self, provider: str) -> "AIClient":
        if provider not in self._clients:
            factory = {
                "anthropic": AnthropicClient,
                "openai": OpenAIClient,
                "gemini": GeminiClient,
                "moonshot": MoonshotClient,
                "ollama": OllamaClient,
            }.get(provider)
            if factory is None:
                raise RuntimeError(f"Provider tidak dikenal: {provider}")
            self._clients[provider] = factory(self.config)
        return self._clients[provider]

    def _pick_provider(self, kind: str, factor: float = 1.0) -> str:
        chosen = self.config.provider_for_kind(kind)
        # Budget cap: if this call would push us over cap AND the provider
        # actually costs money, try to downgrade to Ollama.
        if self.config.cost_cap_usd > 0:
            n = self._calls_by_provider.get(chosen, 0)
            projected = (self._estimated_cost_usd
                         + cost_per_call(chosen, is_first=(n == 0)) * factor)
            if projected > self.config.cost_cap_usd:
                if "ollama" in self.config.all_providers_in_use() or self.config.ollama_host:
                    return "ollama"
                return "capped"
        return chosen

    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis:
        # Local classify (free)
        try:
            from .image_heuristics import classify_image
            kind = classify_image(image_bytes)
        except Exception:
            kind = "unknown"

        # Decorative images: never worth an AI call, regardless of provider.
        # (This runs even when OptimizedAIClient isn't wrapping us.)
        if kind == "decorative":
            return ImageAnalysis.skipped_result("dekoratif (klasifikasi lokal)", silent=True)

        provider = self._pick_provider(kind)
        if provider == "capped":
            return ImageAnalysis.skipped_result(
                f"budget cap ${self.config.cost_cap_usd:.2f} tercapai"
            )

        try:
            client = self._get_client(provider)
            result = client.analyze_image(image_bytes, media_type, extra_context)
            self._account(provider)
        except Exception as primary_err:
            # C5: fallback chain — try Ollama first (free), then the "other"
            # cloud provider if the primary already was Ollama.
            fallbacks = self._fallback_order(provider)
            result = None
            for alt in fallbacks:
                try:
                    client = self._get_client(alt)
                    result = client.analyze_image(image_bytes, media_type, extra_context)
                    self._account(alt)
                    break
                except Exception:
                    continue
            if result is None:
                raise primary_err
        return result

    def _fallback_order(self, primary: str) -> list[str]:
        seen = {primary}
        order: list[str] = []
        # Always try Ollama first if available (free)
        if primary != "ollama" and self.config.ollama_host and self.config.ollama_model:
            order.append("ollama")
            seen.add("ollama")
        # Then any other provider in use
        for p in self.config.all_providers_in_use():
            if p not in seen and self.config.has_key_for(p):
                order.append(p)
                seen.add(p)
        return order

    def _account(self, provider: str, factor: float = 1.0) -> None:
        with self._lock:
            n = self._calls_by_provider.get(provider, 0)
            self._calls_by_provider[provider] = n + 1
            self._estimated_cost_usd += cost_per_call(provider, is_first=(n == 0)) * factor

    @property
    def estimated_cost_usd(self) -> float:
        return self._estimated_cost_usd

    @property
    def anthropic_calls(self) -> int:
        return self._calls_by_provider.get("anthropic", 0)

    @property
    def calls_by_provider(self) -> dict[str, int]:
        return dict(self._calls_by_provider)


# ---------- Factory ----------

_CLIENT_FACTORY = {
    "anthropic": AnthropicClient,
    "openai": OpenAIClient,
    "gemini": GeminiClient,
    "moonshot": MoonshotClient,
    "ollama": OllamaClient,
}


def create_client(config: Config) -> "AIClient":
    # C1: if hybrid image routing is configured (per-kind or global image
    # override), return a HybridAIClient that classifies+routes per image.
    if config.image_provider or config.image_provider_by_kind:
        return HybridAIClient(config)
    factory = _CLIENT_FACTORY.get(config.provider)
    if factory is None:
        raise RuntimeError(f"Provider tidak dikenal: {config.provider}")
    return factory(config)


def wrap_with_options(client: "AIClient", options) -> "AIClient":
    """Return a client wrapped with optimizations, or the original if no opts on."""
    if options is None or not options.any_optimization_on:
        return client
    return OptimizedAIClient(client, options)


# ---------- Analysis orchestration ----------

def _analyze_with_retry(call_fn, png_bytes: bytes, extra_context: str) -> ImageAnalysis:
    """Call the model, then retry once with an error hint if it copied the
    context's slide text into its answer, tried Mermaid but produced invalid
    syntax (after autofix), or called the image a table without a usable
    TABLE section. An answer that still copies the context is dropped."""
    first = _parse_sections(call_fn(png_bytes, extra_context))
    kind, desc, body_text = first.kind, first.description, first.text
    table, table_problem = _finalize_table(first.table)
    mermaid, reasons = _finalize_mermaid(first.mermaid)
    echoed = _echoes_context(first, extra_context)

    hint = ""
    if echoed:
        hint = ECHO_RETRY_HINT
    elif (kind == "diagram" and first.mermaid and first.mermaid.upper() != "NONE"
            and mermaid is None):
        hint = RETRY_HINT_TEMPLATE.format(
            prev=first.mermaid.strip(),
            reasons="; ".join(reasons) or "syntax error",
        )
    elif kind == "table" and table is None:
        hint = TABLE_RETRY_HINT.format(reason=table_problem)
    elif table is None and _TABLE_WORD_RE.search(desc) and kind not in ("diagram", "decorative"):
        # "shows a table …" under another kind: small models describe the
        # table instead of transcribing it.
        hint = TABLE_RETRY_HINT.format(reason="the description says the image is a table, "
                                              "but KIND was not table")
    if hint:
        second = _parse_sections(call_fn(png_bytes, (extra_context + "\n\n" + hint).strip()))
        mermaid2, _ = _finalize_mermaid(second.mermaid)
        table2, _ = _finalize_table(second.table)
        if echoed:
            if _echoes_context(second, extra_context):
                return ImageAnalysis.skipped_result("model menyalin teks konteks", silent=True)
            kind, desc, body_text = second.kind, second.description, second.text
            table, mermaid = table2, mermaid2
        else:
            if mermaid2:
                mermaid = mermaid2
            if table2:
                table = table2
                desc = second.description or desc
            elif second.description and len(second.description) > len(desc):
                desc = second.description
            body_text = body_text or second.text
            if second.kind != "other":
                kind = second.kind

    # Small models label a logo "other"/"illustration" but still say what it is.
    if kind not in ("table", "text", "diagram", "chart") and _LOGO_RE.match(desc.strip()):
        kind = "decorative"
    return ImageAnalysis(kind=kind, description=desc, mermaid=mermaid, table=table,
                         text=body_text, silent=(kind == "decorative"))


ECHO_RETRY_HINT = (
    "Your previous answer copied the slide text from the context. The context is background "
    "only: describe or transcribe what THIS image itself shows, nothing from the context."
)
_CONTEXT_TEXT_RE = re.compile(r"(?:Teks slide|Slide text): (.*)")
_TABLE_WORD_RE = re.compile(r"\b(?:tabel|table)\b", re.IGNORECASE)
_ECHO_WINDOW = 50
_LOGO_RE = re.compile(
    r"^(?:(?:gambar\s+ini|gambar|this\s+image|the\s+image|image|this)\s+"
    r"(?:adalah|menampilkan|berisi|merupakan|shows|is|depicts)\s+)?"
    r"(?:sebuah\s+|an?\s+|the\s+)?logo\b",
    re.IGNORECASE,
)


def _echoes_context(answer: _Sections, extra_context: str) -> bool:
    """True if the answer repeats a run of the surrounding slide text given
    as context (small local models do this instead of reading the image)."""
    m = _CONTEXT_TEXT_RE.search(extra_context or "")
    if not m:
        return False
    ctx = " ".join(m.group(1).rstrip("…").split())
    said = " ".join(" ".join(x for x in (answer.description, answer.text or "", answer.table or "")
                             if x).split())
    if len(ctx) < _ECHO_WINDOW:
        return False
    return any(ctx[i:i + _ECHO_WINDOW] in said for i in range(0, len(ctx) - _ECHO_WINDOW + 1, 10))


def _describe_user_text(extra_context: str) -> str:
    user_text = "Analisis gambar berikut sesuai instruksi sistem."
    if extra_context:
        user_text += f"\n\nKonteks tambahan: {extra_context}"
    return user_text


# ---------- Page vision ----------

PAGE_MAX_TOKENS = 8192
# Below this share of text-layer words found in the answer, retry once.
PAGE_MIN_COVERAGE = 0.92

_TEXT_LAYER_POS_RE = re.compile(r"^\s*\[x=[\d.]+ y=[\d.]+\] ?")
_TEXT_LAYER_TAG_RE = re.compile(r"\s*\{(?:box S\d+|legend: [^{}]*)\}\s*$")
_MERMAID_BLOCK_RE = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.DOTALL)
_WORD_RE = re.compile(r"\w+")


def format_text_layer_entry(x: float, y: float, text: str) -> str:
    """One text-layer entry: position prefix, continuation lines indented."""
    prefix = f"[x={x:.2f} y={y:.2f}] "
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return prefix + ("\n" + " " * len(prefix)).join(lines)


def _text_layer_entries(text_layer: str) -> list[str]:
    """Split a formatted text layer back into plain-text entries (without
    style tags). Stops at the first section after the entries (box styles,
    connectors)."""
    entries: list[str] = []
    for line in text_layer.splitlines():
        if _TEXT_LAYER_POS_RE.match(line):
            entries.append(_TEXT_LAYER_TAG_RE.sub("", _TEXT_LAYER_POS_RE.sub("", line)))
        elif line[:1].isspace() and line.strip() and entries:
            entries[-1] += " " + _TEXT_LAYER_TAG_RE.sub("", line.strip())
        elif line.strip():
            break
    return entries


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 1 or w.isdigit()}


def page_coverage(text_layer: str, markdown: str) -> tuple[float, list[str]]:
    """Share of distinct text-layer words present in `markdown`, plus the
    entries that are mostly missing (used to steer a retry)."""
    out_words = _words(markdown)
    entries = _text_layer_entries(text_layer)
    layer_words: set[str] = set()
    missing: list[str] = []
    for entry in entries:
        ws = _words(entry)
        layer_words |= ws
        if ws and len(ws & out_words) / len(ws) < 0.6:
            missing.append(entry)
    if not layer_words:
        return 1.0, []
    return len(layer_words & out_words) / len(layer_words), missing


_LETTER_WORD_RE = re.compile(r"[^\W\d_]{3,}")
# Words the page-vision rules themselves ask for.
_PAGE_SCAFFOLD_WORDS = {"legend", "figure", "recommended", "label"}
# Retry when the answer brings in more distinct new words than this.
PAGE_MAX_ADDED_WORDS = 2


def added_words(text_layer: str, markdown: str) -> list[str]:
    """Words in `markdown` that are nowhere on the page — invented legends,
    explanations, colour names. Figure lines and Mermaid syntax (node ids,
    keywords) are skipped; inside Mermaid only quoted labels count."""
    page = "".join(_text_layer_entries(text_layer)).lower()
    page = re.sub(r"\W", "", page)
    scanned: list[str] = []
    in_code = False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            scanned.extend(re.findall(r'"([^"]*)"', line))
        elif not line.lstrip().startswith("> **Figure:**"):
            scanned.append(line)
    out: list[str] = []
    for word in _LETTER_WORD_RE.findall(" ".join(scanned).replace("<br/>", " ")):
        w = word.lower()
        if w not in page and w not in _PAGE_SCAFFOLD_WORDS and w not in out:
            out.append(w)
    return out


def _clean_page_markdown(text: str) -> str:
    text = text.strip()
    # Whole answer wrapped in a ```markdown fence despite instructions.
    m = re.match(r"^```(?:markdown|md)?[ \t]*\n(.*)\n```$", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # `#` is reserved for the document title; demote page-level H1s.
    out: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("# "):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def _check_page_mermaid(markdown: str) -> tuple[str, list[str]]:
    """Autofix Mermaid blocks in place; return (markdown, problems left)."""
    problems: list[str] = []

    def _fix(m: re.Match[str]) -> str:
        src = m.group(1)
        ok, reasons = _validate_mermaid(src)
        if ok:
            return m.group(0)
        fixed = _autofix_mermaid(src)
        ok2, reasons2 = _validate_mermaid(fixed)
        if ok2:
            return "```mermaid\n" + fixed.strip() + "\n```"
        first = src.strip().splitlines()[0] if src.strip() else ""
        problems.append(f"Mermaid block starting `{first}` is invalid: {'; '.join(reasons2)}")
        return m.group(0)

    return _MERMAID_BLOCK_RE.sub(_fix, markdown), problems


def _demote_invalid_mermaid(markdown: str) -> tuple[str, int]:
    count = 0

    def _demote(m: re.Match[str]) -> str:
        nonlocal count
        if _validate_mermaid(m.group(1))[0]:
            return m.group(0)
        count += 1
        return ("*(Diagram Mermaid tidak valid — ditampilkan sebagai teks)*\n\n```\n"
                + m.group(1).strip() + "\n```")

    return _MERMAID_BLOCK_RE.sub(_demote, markdown), count


def _transcribe_page_with_retry(complete_fn, png_bytes: bytes, text_layer: str,
                                extra_context: str) -> PageTranscription:
    """One page → Markdown. Retries once if words from the text layer went
    missing or a Mermaid block is invalid; keeps the better attempt."""
    base_user = (extra_context.strip() + "\n\n" if extra_context.strip() else "") \
        + "TEXT LAYER:\n" + (text_layer.strip() or "(no text on this page)")

    def _attempt(user_text: str):
        raw, tin, tout = complete_fn(png_bytes, PAGE_SYSTEM, user_text, PAGE_MAX_TOKENS)
        md, problems = _check_page_mermaid(_clean_page_markdown(raw))
        cov, missing = page_coverage(text_layer, md)
        extra = added_words(text_layer, md) if text_layer.strip() else []
        if len(extra) > PAGE_MAX_ADDED_WORDS:
            problems.append("You added words that are not on the page ("
                            + ", ".join(extra[:20]) + "). Remove every invented legend, "
                            "explanation or colour name; keep only the page's own text.")
        return md, problems, cov, missing, tin, tout

    md, problems, cov, missing, tin, tout = _attempt(base_user)
    retried = False
    if problems or cov < PAGE_MIN_COVERAGE:
        issues = list(problems)
        if missing:
            listed = "\n".join(f"  - {e}" for e in missing[:40])
            issues.append("These text-layer entries are missing from your answer:\n" + listed)
        hint = PAGE_RETRY_TEMPLATE.format(problems="\n".join(f"- {p}" for p in issues))
        md2, problems2, cov2, _missing2, tin2, tout2 = _attempt(base_user + "\n\n" + hint)
        retried = True
        tin += tin2
        tout += tout2
        if (len(problems2), -cov2) < (len(problems), -cov):
            md, cov = md2, cov2

    md, invalid = _demote_invalid_mermaid(md)
    return PageTranscription(markdown=md, coverage=cov, retried=retried,
                             invalid_mermaid=invalid, input_tokens=tin, output_tokens=tout)


# ---------- Shared helpers ----------

def _normalize_image(data: bytes) -> bytes:
    """Convert to PNG and cap dimensions so uploads stay small."""
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "RGBA"):
        # Keep transparency: converting a palette PNG with a transparent
        # background straight to RGB paints that background black, which
        # hides black text.
        has_alpha = img.mode in ("LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
    max_side = 1568
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_KIND_RE = re.compile(r"^KIND:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_VALID_KINDS: set[str] = {"diagram", "photo", "chart", "table", "text", "screenshot", "illustration",
                           "decorative", "other"}


class _Sections(NamedTuple):
    kind: ImageKind
    description: str
    table: str | None    # raw, not validated
    text: str | None     # None when absent or NONE
    mermaid: str | None  # raw, not validated


def _parse_sections(text: str) -> _Sections:
    """Extract KIND / DESCRIPTION / TABLE / TEXT / MERMAID (no validation)."""
    sections = [s.strip() for s in re.split(r"^---\s*$", text, flags=re.MULTILINE)]
    kind: ImageKind = "other"
    description = ""
    raw: dict[str, str | None] = {"table": None, "text": None, "mermaid": None}
    fence_lang = {"table": "markdown|md", "text": "markdown|md|text", "mermaid": "mermaid"}

    for section in sections:
        low = section.lower().lstrip()
        if low.startswith("kind:"):
            m = _KIND_RE.search(section)
            if m:
                cand = m.group(1).lower()
                if cand in _VALID_KINDS:
                    kind = cand  # type: ignore[assignment]
        elif low.startswith("description:"):
            description = section.split(":", 1)[1].strip()
        else:
            name = low.split(":", 1)[0].strip()
            if name in raw and ":" in low:
                body = section.split(":", 1)[1].strip()
                body = re.sub(rf"^```(?:{fence_lang[name]})?\s*", "", body)
                body = re.sub(r"\s*```$", "", body)
                if body:
                    raw[name] = body

    if not description:
        description = text.strip() or "(Tidak ada deskripsi)"
    body_text = raw["text"]
    if body_text and body_text.upper() == "NONE":
        body_text = None
    return _Sections(kind, description, raw["table"], body_text, raw["mermaid"])


_TABLE_SEP_CELL_RE = re.compile(r"^:?-{3,}:?$")


def _table_cells(line: str) -> list[str]:
    """Cells of one `| a | b |` row; `\\|` inside a cell stays."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def _finalize_table(raw: str | None) -> tuple[str | None, str]:
    """Return (normalized Markdown table(s) or None, what was wrong with it).

    Accepts one or more pipe tables separated by blank or non-table lines.
    Each needs a header row and a `---` separator row; short rows are padded,
    a row with more cells than its header rejects the whole answer."""
    if not raw or raw.strip().upper() == "NONE":
        return None, "TABLE section empty"
    groups: list[list[str]] = []
    current: list[str] = []
    for line in raw.splitlines():
        if line.strip().startswith("|"):
            current.append(line)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    if not groups:
        return None, "no `|` rows"

    tables: list[str] = []
    for group in groups:
        rows = [_table_cells(ln) for ln in group]
        if len(rows) < 3 or not all(_TABLE_SEP_CELL_RE.match(c) for c in rows[1]):
            return None, "header row not followed by a `| --- |` separator row"
        header, width = rows[0], len(rows[0])
        body = rows[2:]
        for i, r in enumerate(body, start=1):
            if len(r) > width:
                return None, f"row {i} has {len(r)} cells, header has {width}"
        # a second `| --- |` row mid-table (a model stacking two halves) is not data
        body = [r + [""] * (width - len(r)) for r in body
                if not all(_TABLE_SEP_CELL_RE.match(c) for c in r)]
        header, body = _unstack_side_by_side(header, body)
        lines = ["| " + " | ".join(header) + " |",
                 "| " + " | ".join("---" for _ in header) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        tables.append("\n".join(lines))
    return "\n\n".join(tables), ""


def _unstack_side_by_side(header: list[str], body: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """`| Age | Factor | Age | Factor |` is one long table printed in two
    halves side by side: stack the halves (left one first), dropping the
    blank padding rows of a shorter half."""
    width = len(header)
    for k in range(1, width // 2 + 1):
        if width % k or any(header[i] != header[i % k] for i in range(width)):
            continue
        if not any(header[:k]):
            return header, body
        stacked = [r[c:c + k] for c in range(0, width, k) for r in body]
        return header[:k], [r for r in stacked if any(r)]
    return header, body


def _finalize_mermaid(raw: str | None) -> tuple[str | None, list[str]]:
    """Return (valid_mermaid_or_None, reasons_it_failed). Applies autofix."""
    if not raw or raw.strip().upper() == "NONE":
        return None, []
    ok, reasons = _validate_mermaid(raw)
    if ok:
        return raw.strip(), []
    fixed = _autofix_mermaid(raw)
    if fixed != raw:
        ok2, reasons2 = _validate_mermaid(fixed)
        if ok2:
            return fixed.strip(), []
        return None, reasons2
    return None, reasons


# Common malformations produced by VLMs. Catches classes of error seen in the
# wild. Not a full parser — just rejects obvious garbage so the .md doesn't
# render as broken.
_MERMAID_BAD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"[A-Za-z_]\w*\s+\[[^\]]+\]\s*(-->|--)"),
     "space between node id and `[label]` before arrow"),
    (re.compile(r"[A-Za-z_]\w*\s+\{[^\}]+\}\s*(-->|--)"),
     "space between node id and `{label}` before arrow"),
    (re.compile(r"[A-Za-z_]\w*\s+\([^\)]+\)\s*(-->|--)"),
     "space between node id and `(label)` before arrow"),
    (re.compile(r"(?<![\[\{\(|\w])[A-Za-z_]\w*\?"),
     "`?` in a bare node identifier (put it inside `{...}` label)"),
    (re.compile(r"(?:-->|---)\s*[A-Za-z_]\w*\s+\["),
     "space between arrow-target id and its `[label]`"),
    # `A[Foo (bar)]` is a parse error; `A[(db)]` (cylinder) is fine.
    (re.compile(r"\w\[(?!\()[^\]\"\n]*[()][^\]\"\n]*\]"),
     "parentheses inside an unquoted `[label]` (quote the label)"),
]
_MERMAID_TYPES = (
    "flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram",
    "erDiagram", "gantt", "mindmap", "journey", "pie", "gitGraph", "timeline",
    "quadrantChart", "requirementDiagram", "C4Context", "block-beta", "architecture-beta",
)


def _validate_mermaid(src: str) -> tuple[bool, list[str]]:
    stripped = src.strip()
    if not stripped:
        return False, ["empty"]
    first_line = stripped.splitlines()[0].strip()
    if not first_line.startswith(_MERMAID_TYPES):
        return False, [f"first line `{first_line}` isn't a known Mermaid diagram type"]
    # Quoted labels may legally contain `?`, brackets and spaces — blank them
    # out so the pattern checks only see the diagram's syntax.
    scan = re.sub(r'"[^"\n]*"', '""', stripped)
    scan = re.sub(r"^\s*%%.*$", "", scan, flags=re.MULTILINE)
    reasons: list[str] = []
    for pat, why in _MERMAID_BAD_PATTERNS:
        if pat.search(scan):
            reasons.append(why)
    if '"' in re.sub(r'"[^"\n]*"', "", stripped):
        reasons.append("a quoted label spans several lines (use <br/>) or quotes are unbalanced")
    if first_line.startswith(("flowchart", "graph")):
        # Flowchart node shapes must close; `id>label]` (asymmetric) is the
        # one shape with a lone bracket.
        shapes = re.sub(r"(\w)>[^\]\n]*\]", r"\1", scan)
        for open_ch, close_ch in (("[", "]"), ("{", "}"), ("(", ")")):
            if shapes.count(open_ch) != shapes.count(close_ch):
                reasons.append(f"unbalanced `{open_ch}{close_ch}`")
        lines = [ln.strip() for ln in scan.splitlines()]
        opened = sum(1 for ln in lines if ln.startswith("subgraph"))
        closed = sum(1 for ln in lines if ln == "end")
        if opened != closed:
            reasons.append(f"{opened} `subgraph` vs {closed} `end`")
    return (not reasons), reasons


def _autofix_mermaid(src: str) -> str:
    """Best-effort repair for common VLM mistakes. Runs rules to a fixed point."""
    prev = None
    fixed = src
    # A quoted label broken over several lines → one line with <br/>.
    # Quotes pair up in order, so every odd-numbered segment is a label.
    parts = fixed.split('"')
    if len(parts) % 2 == 1:
        for i in range(1, len(parts), 2):
            if "\n" in parts[i]:
                parts[i] = re.sub(r"\s*\n\s*", "<br/>", parts[i].strip())
        fixed = '"'.join(parts)
    # `A[Foo (bar)]` → `A["Foo (bar)"]`
    fixed = re.sub(r"(\w)\[(?!\()([^\]\"\n]*[()][^\]\"\n]*)\]", r'\1["\2"]', fixed)
    for _ in range(5):
        if fixed == prev:
            break
        prev = fixed
        # First: strip `?` from bare node identifiers (outside brackets/labels).
        # This may lose decision semantics but the label typically survives elsewhere.
        fixed = re.sub(
            r"(?<![\[\{\(|\w])([A-Za-z_]\w*)\?",
            r"\1",
            fixed,
        )
        # `X [label] -->` → `X -->|label|`
        fixed = re.sub(
            r"(^|\s)([A-Za-z_]\w*)\s+\[([^\]]+)\]\s*-->",
            lambda m: f"{m.group(1)}{m.group(2)} -->|{m.group(3).strip()}|",
            fixed,
            flags=re.MULTILINE,
        )
        # `--> X [label]` → `-->|label| X`
        fixed = re.sub(
            r"-->\s*([A-Za-z_]\w*)\s+\[([^\]]+)\]",
            lambda m: f"-->|{m.group(2).strip()}| {m.group(1)}",
            fixed,
        )
    return fixed
