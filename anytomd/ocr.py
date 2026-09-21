"""OCR backends.

Four engines selectable via ConvertOptions.ocr_engine:

- `tesseract` — via pytesseract; requires Tesseract binary installed separately.
- `paddleocr` — via `paddleocr` package; optional heavy import.
- `ai`       — reuse the vision AI client with a transcription-only prompt.
- `hybrid`   — try Tesseract first; if the result looks unreliable (very short
               or empty), retry with AI.

Each backend implements OCRBackend.transcribe(image_bytes, lang) -> str.

The caller is responsible for deciding *whether* to OCR (see pdf._is_scanned_page)
and how the OCR text integrates into the final Markdown.
"""
from __future__ import annotations

import io
import os
import shutil
from typing import Callable, Protocol

from PIL import Image

# Try to locate a Tesseract binary once at import time.
_TESSERACT_PATH: str | None = None


def _find_tesseract() -> str | None:
    global _TESSERACT_PATH
    if _TESSERACT_PATH is not None:
        return _TESSERACT_PATH or None
    candidates = [
        os.environ.get("TESSERACT_CMD", "").strip(),
        shutil.which("tesseract") or "",
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"E:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            _TESSERACT_PATH = c
            return c
    _TESSERACT_PATH = ""
    return None


class OCRBackend(Protocol):
    name: str

    def transcribe(self, image_bytes: bytes, lang: str = "ind+eng") -> str: ...


# ---------- Tesseract ----------

class TesseractBackend:
    name = "tesseract"

    def __init__(self):
        path = _find_tesseract()
        if not path:
            raise RuntimeError(
                "Tesseract binary tidak ditemukan. Install dari "
                "https://github.com/UB-Mannheim/tesseract/wiki dan pastikan "
                "'tesseract' ada di PATH, atau set env TESSERACT_CMD."
            )
        try:
            import pytesseract  # deferred import
        except ImportError as e:
            raise RuntimeError("Paket 'pytesseract' belum diinstall. `pip install pytesseract`.") from e
        self._pyt = pytesseract
        pytesseract.pytesseract.tesseract_cmd = path

    def transcribe(self, image_bytes: bytes, lang: str = "ind+eng") -> str:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return self._pyt.image_to_string(img, lang=lang).strip()


# ---------- PaddleOCR ----------

class PaddleOCRBackend:
    name = "paddleocr"

    def __init__(self):
        try:
            from paddleocr import PaddleOCR  # deferred import
        except ImportError as e:
            raise RuntimeError(
                "Paket 'paddleocr' belum diinstall. `pip install paddleocr` "
                "(catatan: ini heavy install, butuh paddlepaddle framework)."
            ) from e
        # `id` language code = Indonesian for PaddleOCR; use `en` for English.
        # PaddleOCR doesn't support ind+eng combined; we'll run twice if needed.
        self._PaddleOCR = PaddleOCR
        self._instances: dict[str, object] = {}

    def _get(self, lang: str):
        # Map Tesseract-style lang code to PaddleOCR-style
        paddle_lang = lang.replace("ind", "id").split("+")[0]
        if paddle_lang not in self._instances:
            self._instances[paddle_lang] = self._PaddleOCR(
                lang=paddle_lang, use_angle_cls=False, show_log=False
            )
        return self._instances[paddle_lang]

    def transcribe(self, image_bytes: bytes, lang: str = "ind+eng") -> str:
        ocr = self._get(lang)
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        import numpy as np  # paddleocr already depends on numpy
        arr = np.array(img)
        result = ocr.ocr(arr, cls=False)
        # Flatten result -> lines of text
        lines: list[str] = []
        for page in result or []:
            for entry in page or []:
                try:
                    text = entry[1][0]
                    if text:
                        lines.append(text)
                except (IndexError, TypeError):
                    continue
        return "\n".join(lines).strip()


# ---------- AI vision (transcription mode) ----------

_AI_TRANSCRIBE_PROMPT = (
    "You are an OCR engine. Read the image and return ONLY the text content, "
    "preserving line breaks and reading order. Do NOT summarize, describe, or "
    "add commentary. If a line is unreadable, output '[?]' in its place. "
    "If the image has no text, output an empty response."
)


class AIVisionOCRBackend:
    name = "ai"

    def __init__(self, ai_client):
        # ai_client is an anytomd.ai_client.AIClient (Anthropic or Ollama)
        self.ai = ai_client

    def transcribe(self, image_bytes: bytes, lang: str = "ind+eng") -> str:
        # Reuse the vision client's underlying `_call` (raw text response),
        # but with a transcription-only system prompt. To avoid re-writing
        # the client, we call analyze_image with a specific hint; the model
        # follows the KIND/DESCRIPTION/MERMAID template, so we parse out
        # only the DESCRIPTION section as the transcript.
        #
        # This is a pragmatic compromise. A cleaner solution would be a
        # separate `_call_transcribe` on the client; kept simple for now.
        from .ai_client import ImageAnalysis  # noqa: F401
        analysis = self.ai.analyze_image(
            image_bytes,
            media_type="image/png",
            extra_context=(
                "Ini adalah page dari PDF scan. Transkripsikan seluruh teks yang "
                "kamu lihat, baris per baris, dalam bagian DESCRIPTION. "
                "Jangan deskripsikan visual, langsung transcript. "
                "Set KIND=other dan MERMAID=NONE."
            ),
        )
        return (analysis.description or "").strip()


# ---------- Hybrid ----------

class HybridOCRBackend:
    name = "hybrid"

    def __init__(self, ai_client, min_useful_chars: int = 50):
        self.min_useful_chars = min_useful_chars
        self._tesseract: TesseractBackend | None = None
        self._ai = AIVisionOCRBackend(ai_client)
        self._init_error: str = ""
        try:
            self._tesseract = TesseractBackend()
        except RuntimeError as e:
            self._init_error = str(e)

    def transcribe(self, image_bytes: bytes, lang: str = "ind+eng") -> str:
        if self._tesseract is None:
            # Tesseract not available; fall back to AI directly
            return self._ai.transcribe(image_bytes, lang)
        text = ""
        try:
            text = self._tesseract.transcribe(image_bytes, lang)
        except Exception:
            text = ""
        if len(text) >= self.min_useful_chars:
            return text
        # Tesseract failed or produced too little; retry with AI
        return self._ai.transcribe(image_bytes, lang)


# ---------- Factory ----------

def create_ocr_backend(engine: str, ai_client=None) -> OCRBackend:
    """Instantiate the requested OCR backend. Raises RuntimeError on failure."""
    engine = (engine or "").lower().strip()
    if engine == "tesseract":
        return TesseractBackend()
    if engine == "paddleocr":
        return PaddleOCRBackend()
    if engine == "ai":
        if ai_client is None:
            raise RuntimeError("AI OCR butuh AI client — pastikan provider Anthropic/Ollama siap.")
        return AIVisionOCRBackend(ai_client)
    if engine == "hybrid":
        if ai_client is None:
            raise RuntimeError("Hybrid OCR butuh AI client untuk fallback.")
        return HybridOCRBackend(ai_client)
    raise RuntimeError(f"OCR engine tidak dikenal: '{engine}'. Pilihan: tesseract, paddleocr, ai, hybrid.")


def is_engine_available(engine: str) -> tuple[bool, str]:
    """Check whether an engine can be constructed. Returns (ok, reason_if_not)."""
    engine = (engine or "").lower().strip()
    if engine == "tesseract":
        if _find_tesseract() is None:
            return False, "Tesseract binary tidak ditemukan"
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False, "pytesseract belum diinstall"
        return True, ""
    if engine == "paddleocr":
        try:
            import paddleocr  # noqa: F401
            return True, ""
        except ImportError:
            return False, "paddleocr belum diinstall (heavy)"
    if engine in {"ai", "hybrid"}:
        return True, ""  # depends on AI provider being ready — checked at call time
    return False, f"engine tidak dikenal: {engine}"
