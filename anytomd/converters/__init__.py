from pathlib import Path
from typing import Callable

from ..ai_client import AIClient, wrap_with_options
from ..options import ConvertOptions
from .docx import convert_docx
from .html import convert_html
from .image import convert_image
from .pdf import convert_pdf
from .pptx import convert_pptx
from .xlsx import convert_xlsx

ProgressFn = Callable[[str], None]


def convert(path: Path, ai: AIClient, progress: ProgressFn | None = None,
            options: ConvertOptions | None = None) -> str:
    """Dispatch to the right converter based on suffix. Returns Markdown text.

    Passing `options` enables per-conversion token-saving filters (dedup,
    skip-tiny, diagram-only, full-skip). Defaults to `all` (no filtering).
    """
    ext = path.suffix.lower()
    # PDF and PPTX need the raw AI client (OCR, page/slide vision) — they wrap
    # internally for their per-image analysis calls.
    if ext == ".pdf":
        return convert_pdf(path, ai, progress, options=options)
    if ext == ".pptx":
        return convert_pptx(path, ai, progress, options=options)
    # Other formats: wrap here so all their image AI calls go through filters.
    if options is not None:
        ai = wrap_with_options(ai, options)
    if ext == ".docx":
        return convert_docx(path, ai, progress)
    if ext == ".xlsx":
        return convert_xlsx(path, ai, progress)
    if ext in {".html", ".htm"}:
        return convert_html(path, ai, progress)
    if ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        return convert_image(path, ai, progress)
    raise ValueError(f"Format tidak didukung: {ext}")


__all__ = [
    "convert",
    "convert_pdf", "convert_pptx", "convert_docx", "convert_xlsx",
    "convert_image", "convert_html",
    "ProgressFn",
]
