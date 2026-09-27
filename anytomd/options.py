"""Per-conversion knobs for cost/quality tradeoffs.

Defaults preserve the pre-existing behavior: every image goes to the AI.
User opts in to optimizations via .env or the GUI settings panel.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

ImageMode = Literal["all", "diagrams_only", "none"]
OCRMode = Literal["auto", "always", "never"]
OCREngine = Literal["tesseract", "paddleocr", "ai", "hybrid"]
PageVisionMode = Literal["auto", "always", "never"]


@dataclass
class ConvertOptions:
    # Skip AI call if the same image bytes were already analyzed in this conversion.
    image_dedup: bool = False

    # Skip AI call if the image's longest side is smaller than this many pixels
    # (0 = disabled). Catches tiny decorative/bullet/watermark images.
    image_min_dim: int = 0

    # Skip AI call if the raw image bytes are smaller than this (0 = disabled).
    image_min_bytes: int = 0

    # Filtering mode:
    #   "all"           = send every eligible image to the AI (default)
    #   "diagrams_only" = pre-classify with a local heuristic, only send
    #                     diagram-like images (skips photos/screenshots)
    #   "none"          = never send images to AI, emit a placeholder instead
    image_mode: ImageMode = "all"

    # B1: Locally detect "pure decoration" (solid blobs, gradient circles used
    # as cover-page filler). Always active by default — no reason to burn a
    # token on a solid-color blob.
    skip_decorative: bool = True

    # OCR: only affects PDF converter. Ignored for other formats.
    #   ocr_mode:
    #     "never"   = never OCR, current behavior
    #     "auto"    = OCR only pages that look scanned (empty text + full-page image)
    #     "always"  = OCR every page (even pages with extractable text)
    #   ocr_engine: tesseract | paddleocr | ai | hybrid
    #   ocr_lang:   Tesseract-style code(s), e.g. "ind+eng", "eng", "ind+eng+ara"
    ocr_mode: OCRMode = "never"
    ocr_engine: OCREngine = "tesseract"
    ocr_lang: str = "ind+eng"

    # PDF page vision: render each page and let a vision model write its
    # Markdown, anchored on the page's text layer. Recovers what per-element
    # extraction loses on slide decks: vector diagrams, borderless tables,
    # multi-column reading order.
    #   "auto"   = slide-deck PDFs only, and only with a cloud provider
    #   "always" = every page with content, any provider (Ollama included)
    #   "never"  = classic text + table + image extraction
    pdf_page_vision: PageVisionMode = "auto"

    # Runtime counters (mutable) — the wrapper updates these so the GUI can
    # show "hemat N calls". Values are numeric (int for counts, float for cost).
    _stats: dict[str, float] = field(default_factory=lambda: {
        "sent": 0,
        "skipped_tiny": 0,
        "skipped_duplicate": 0,
        "skipped_not_diagram": 0,
        "skipped_mode_none": 0,
        "skipped_decorative": 0,
        "skipped_template": 0,
        "ocr_pages": 0,
        "page_vision_pages": 0,
        "page_vision_retries": 0,
        "page_vision_fallbacks": 0,
        "page_vision_min_coverage": 1.0,
        "page_vision_tokens_in": 0,
        "page_vision_tokens_out": 0,
        "anthropic_calls": 0,
        "est_cost_usd": 0.0,
    })

    @property
    def any_optimization_on(self) -> bool:
        return (self.image_dedup
                or self.image_min_dim > 0
                or self.image_min_bytes > 0
                or self.image_mode != "all"
                or self.skip_decorative)

    @property
    def ocr_enabled(self) -> bool:
        return self.ocr_mode != "never"

    def stats_summary(self) -> str:
        s = self._stats
        skipped = (s["skipped_tiny"] + s["skipped_duplicate"]
                   + s["skipped_not_diagram"] + s["skipped_mode_none"]
                   + s.get("skipped_decorative", 0) + s.get("skipped_template", 0))
        total = s["sent"] + skipped
        parts: list[str] = []
        if s.get("page_vision_pages"):
            pv = (f"{int(s['page_vision_pages'])} halaman via AI per halaman "
                  f"(cakupan teks min {s['page_vision_min_coverage']:.0%}")
            if s.get("page_vision_retries"):
                pv += f", {int(s['page_vision_retries'])} diulang"
            if s.get("page_vision_tokens_in"):
                pv += (f", token {int(s['page_vision_tokens_in'])} in"
                       f" / {int(s['page_vision_tokens_out'])} out")
            parts.append(pv + ")")
        if s.get("page_vision_fallbacks"):
            parts.append(f"{int(s['page_vision_fallbacks'])} halaman gagal AI → ekstraksi teks")
        if total > 0:
            parts.append(f"{s['sent']}/{total} gambar dikirim ke AI")
            if s["skipped_duplicate"]:
                parts.append(f"{s['skipped_duplicate']} duplikat")
            if s["skipped_tiny"]:
                parts.append(f"{s['skipped_tiny']} terlalu kecil")
            if s.get("skipped_decorative", 0):
                parts.append(f"{s['skipped_decorative']} hiasan")
            if s.get("skipped_template", 0):
                parts.append(f"{s['skipped_template']} template berulang")
            if s["skipped_not_diagram"]:
                parts.append(f"{s['skipped_not_diagram']} bukan diagram")
            if s["skipped_mode_none"]:
                parts.append(f"{s['skipped_mode_none']} di-skip (mode: none)")
        if s.get("ocr_pages"):
            parts.append(f"{int(s['ocr_pages'])} halaman di-OCR")
        if s.get("anthropic_calls"):
            parts.append(f"{int(s['anthropic_calls'])} panggilan Anthropic")
        cost = s.get("est_cost_usd", 0.0)
        if cost > 0:
            parts.append(f"est. biaya ${cost:.3f}")
        if not parts:
            return "Tidak ada gambar/OCR diproses."
        return ", ".join(parts)

    @classmethod
    def from_env(cls) -> "ConvertOptions":
        def _flag(name: str, default: bool = False) -> bool:
            v = os.getenv(name, "").strip().lower()
            if not v:
                return default
            return v in {"1", "true", "yes", "on"}

        def _int(name: str, default: int = 0) -> int:
            try:
                return int(os.getenv(name, "").strip() or default)
            except ValueError:
                return default

        mode = os.getenv("ANYTOMD_IMAGE_MODE", "all").strip().lower()
        if mode not in {"all", "diagrams_only", "none"}:
            mode = "all"

        ocr_mode = os.getenv("ANYTOMD_PDF_OCR", "never").strip().lower()
        if ocr_mode not in {"never", "auto", "always"}:
            ocr_mode = "never"
        ocr_engine = os.getenv("ANYTOMD_OCR_ENGINE", "tesseract").strip().lower()
        if ocr_engine not in {"tesseract", "paddleocr", "ai", "hybrid"}:
            ocr_engine = "tesseract"
        ocr_lang = os.getenv("ANYTOMD_OCR_LANG", "ind+eng").strip() or "ind+eng"
        page_vision = os.getenv("ANYTOMD_PDF_PAGE_VISION", "auto").strip().lower()
        if page_vision not in {"auto", "always", "never"}:
            page_vision = "auto"

        return cls(
            image_dedup=_flag("ANYTOMD_IMAGE_DEDUP", False),
            image_min_dim=_int("ANYTOMD_IMAGE_MIN_DIM", 0),
            image_min_bytes=_int("ANYTOMD_IMAGE_MIN_BYTES", 0),
            image_mode=mode,  # type: ignore[arg-type]
            skip_decorative=_flag("ANYTOMD_SKIP_DECORATIVE", True),
            ocr_mode=ocr_mode,  # type: ignore[arg-type]
            ocr_engine=ocr_engine,  # type: ignore[arg-type]
            ocr_lang=ocr_lang,
            pdf_page_vision=page_vision,  # type: ignore[arg-type]
        )
