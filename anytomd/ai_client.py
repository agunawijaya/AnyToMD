from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image

from .config import Config, cost_per_call, cost_per_anthropic_call

ImageKind = Literal["diagram", "photo", "chart", "screenshot", "illustration", "other"]

DESCRIBE_SYSTEM = (
    "You analyze images extracted from documents (PDF, PPTX) or standalone image files. "
    "Your job: (1) classify the image, (2) produce a concise factual description in Bahasa Indonesia, "
    "(3) if it's a diagram/flowchart/graph/architecture with clear nodes and connections, generate valid "
    "Mermaid syntax that reproduces it.\n\n"
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
    "Output STRICT format (three sections separated by lines containing only `---`):\n"
    "KIND: <one of: diagram, photo, chart, screenshot, illustration, other>\n"
    "---\n"
    "DESCRIPTION: <2-5 sentences in Bahasa Indonesia describing what the image shows>\n"
    "---\n"
    "MERMAID: <mermaid source, or the literal word NONE>"
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


@dataclass
class ImageAnalysis:
    kind: ImageKind
    description: str
    mermaid: str | None
    skipped: bool = False
    skip_reason: str = ""

    def to_markdown_block(self, alt_hint: str = "image") -> str:
        if self.skipped:
            return f"> *[Gambar dilewati ({self.skip_reason}) — {alt_hint}]*"
        parts = [
            f"> **[Gambar dalam dokumen asli — {self.kind}]**",
            "",
            self.description.strip(),
        ]
        if self.mermaid:
            parts.extend(["", "```mermaid", self.mermaid.strip(), "```"])
        parts.extend(["", f"*(Placeholder: {alt_hint})*"])
        return "\n".join(parts)

    @classmethod
    def skipped_result(cls, reason: str) -> "ImageAnalysis":
        return cls(kind="other", description="", mermaid=None,
                   skipped=True, skip_reason=reason)


class AIClient(Protocol):
    def analyze_image(self, image_bytes: bytes, media_type: str = "image/png",
                      extra_context: str = "") -> ImageAnalysis: ...


# ---------- Anthropic ----------

class AnthropicClient:
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

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        user_text = "Analisis gambar berikut sesuai instruksi sistem."
        if extra_context:
            user_text += f"\n\nKonteks tambahan: {extra_context}"

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=[{
                "type": "text",
                "text": DESCRIBE_SYSTEM,
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
        return "".join(block.text for block in resp.content if block.type == "text")


# ---------- Ollama ----------

class OllamaClient:
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

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        user_text = "Analisis gambar berikut sesuai instruksi sistem."
        if extra_context:
            user_text += f"\n\nKonteks tambahan: {extra_context}"
        try:
            resp = self.client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": DESCRIBE_SYSTEM},
                    {"role": "user", "content": user_text, "images": [png_bytes]},
                ],
                options={"temperature": 0.2, "num_predict": 2048},
            )
        except self._ollama.ResponseError as e:
            if "not found" in str(e).lower():
                raise RuntimeError(
                    f"Model '{self.model}' belum ada di Ollama. "
                    f"Jalankan: ollama pull {self.model}"
                ) from e
            raise
        return resp["message"]["content"] if isinstance(resp, dict) else resp.message.content


# ---------- OpenAI (ChatGPT) ----------

class OpenAIClient:
    """OpenAI Chat Completions with vision. Supports Azure/proxy via base_url."""

    _api_key_attr = "openai_api_key"
    _model_attr = "openai_model"
    _base_url_attr = "openai_base_url"
    _label = "OpenAI"

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

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        user_text = "Analisis gambar berikut sesuai instruksi sistem."
        if extra_context:
            user_text += f"\n\nKonteks tambahan: {extra_context}"
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=2048,
            temperature=0.2,
            messages=[
                {"role": "system", "content": DESCRIBE_SYSTEM},
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
        return (resp.choices[0].message.content or "") if resp.choices else ""


# ---------- Moonshot (Kimi) — OpenAI-compatible ----------

class MoonshotClient(OpenAIClient):
    """Moonshot Kimi vision — reuses OpenAI SDK with a different base_url."""
    _api_key_attr = "moonshot_api_key"
    _model_attr = "moonshot_model"
    _base_url_attr = "moonshot_base_url"
    _label = "Moonshot"


# ---------- Google Gemini ----------

class GeminiClient:
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

    def _call(self, png_bytes: bytes, extra_context: str) -> str:
        user_text = "Analisis gambar berikut sesuai instruksi sistem."
        if extra_context:
            user_text += f"\n\nKonteks tambahan: {extra_context}"
        cfg = self._types.GenerateContentConfig(
            system_instruction=DESCRIBE_SYSTEM,
            temperature=0.2,
            max_output_tokens=2048,
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
        return getattr(resp, "text", "") or ""


# ---------- Optimization wrapper ----------

class OptimizedAIClient:
    """Wraps a real AI client with token-saving filters.

    Filters (all opt-in via ConvertOptions):
      - full-skip (image_mode="none")
      - min-bytes / min-dim (skip tiny)
      - diagram-only prefilter (image_mode="diagrams_only")
      - dedup by SHA-256 of image bytes
    Stats are counted on `options._stats` so the GUI can show savings.
    """

    def __init__(self, inner: "AIClient", options):  # options: ConvertOptions
        self.inner = inner
        self.options = options
        self._cache: dict[str, ImageAnalysis] = {}

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
                f"{len(image_bytes)} B < {opts.image_min_bytes} B min"
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
                        f"{img.size[0]}x{img.size[1]}, < {opts.image_min_dim}px min"
                    )
            except Exception:
                pass  # if PIL can't open, let AI try

        # 4a. Decorative filter (solid blobs, gradient circles) — kills wasted
        # AI calls on cover-page filler. Cheap: ~10ms per image.
        if opts.skip_decorative:
            from .image_heuristics import is_decorative
            if is_decorative(image_bytes):
                stats["skipped_decorative"] = stats.get("skipped_decorative", 0) + 1
                return ImageAnalysis.skipped_result("gambar dekoratif (blob/lingkaran solid)")

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
                # Return a shallow copy noting dedup (same content, different placeholder)
                return ImageAnalysis(
                    kind=cached.kind,
                    description=cached.description,
                    mermaid=cached.mermaid,
                    skipped=False,
                    skip_reason="",
                )

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

    def _pick_provider(self, kind: str) -> str:
        chosen = self.config.provider_for_kind(kind)
        # Budget cap: if this call would push us over cap AND the provider
        # actually costs money, try to downgrade to Ollama.
        if self.config.cost_cap_usd > 0:
            n = self._calls_by_provider.get(chosen, 0)
            projected = self._estimated_cost_usd + cost_per_call(chosen, is_first=(n == 0))
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
            return ImageAnalysis.skipped_result("dekoratif (klasifikasi lokal)")

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

    def _account(self, provider: str) -> None:
        n = self._calls_by_provider.get(provider, 0)
        self._calls_by_provider[provider] = n + 1
        self._estimated_cost_usd += cost_per_call(provider, is_first=(n == 0))

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
    """Call the model, then if it tried Mermaid but produced invalid syntax,
    auto-fix; if still invalid, retry once with an error hint."""
    text = call_fn(png_bytes, extra_context)
    kind, desc, raw_mermaid = _parse_sections(text)
    mermaid, reasons = _finalize_mermaid(raw_mermaid)

    should_retry = (
        kind == "diagram"
        and raw_mermaid
        and raw_mermaid.upper() != "NONE"
        and mermaid is None
    )
    if should_retry:
        hint = RETRY_HINT_TEMPLATE.format(
            prev=raw_mermaid.strip(),
            reasons="; ".join(reasons) or "syntax error",
        )
        text2 = call_fn(png_bytes, (extra_context + "\n\n" + hint).strip())
        kind2, desc2, raw_mermaid2 = _parse_sections(text2)
        mermaid2, _ = _finalize_mermaid(raw_mermaid2)
        if mermaid2:
            mermaid = mermaid2
        if desc2 and len(desc2) > len(desc):
            desc = desc2
        if kind2 != "other":
            kind = kind2

    return ImageAnalysis(kind=kind, description=desc, mermaid=mermaid)


# ---------- Shared helpers ----------

def _normalize_image(data: bytes) -> bytes:
    """Convert to PNG and cap dimensions so uploads stay small."""
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    max_side = 1568
    if max(img.size) > max_side:
        ratio = max_side / max(img.size)
        img = img.resize((int(img.size[0] * ratio), int(img.size[1] * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_KIND_RE = re.compile(r"^KIND:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
_VALID_KINDS: set[str] = {"diagram", "photo", "chart", "screenshot", "illustration", "other"}


def _parse_sections(text: str) -> tuple[ImageKind, str, str | None]:
    """Extract KIND / DESCRIPTION / raw MERMAID (no validation)."""
    sections = [s.strip() for s in re.split(r"^---\s*$", text, flags=re.MULTILINE)]
    kind: ImageKind = "other"
    description = ""
    raw_mermaid: str | None = None

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
        elif low.startswith("mermaid:"):
            body = section.split(":", 1)[1].strip()
            body = re.sub(r"^```(?:mermaid)?\s*", "", body)
            body = re.sub(r"\s*```$", "", body)
            if body:
                raw_mermaid = body

    if not description:
        description = text.strip() or "(Tidak ada deskripsi)"
    return kind, description, raw_mermaid


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
    reasons: list[str] = []
    for pat, why in _MERMAID_BAD_PATTERNS:
        if pat.search(stripped):
            reasons.append(why)
    return (not reasons), reasons


def _autofix_mermaid(src: str) -> str:
    """Best-effort repair for common VLM mistakes. Runs rules to a fixed point."""
    prev = None
    fixed = src
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
