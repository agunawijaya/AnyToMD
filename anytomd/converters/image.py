from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..ai_client import AIClient

ProgressFn = Callable[[str], None]

_MEDIA_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def convert_image(path: Path, ai: AIClient, progress: ProgressFn | None = None) -> str:
    if progress:
        progress(f"Menganalisis {path.name}")
    media = _MEDIA_MAP.get(path.suffix.lower(), "image/png")
    data = path.read_bytes()
    analysis = ai.analyze_image(
        data,
        media_type=media,
        extra_context=f"File asli: {path.name}",
    )

    out = [f"# {path.stem}", ""]
    if analysis.skipped:
        out.append(f"*Gambar tidak dikirim ke AI: {analysis.skip_reason}.*")
        out.append("")
        return "\n".join(out).rstrip() + "\n"

    out.append(f"**Jenis gambar:** {analysis.kind}")
    out.append("")
    out.append("## Deskripsi")
    out.append("")
    out.append(analysis.description.strip())
    out.append("")
    if analysis.mermaid:
        out.append("## Diagram (Mermaid)")
        out.append("")
        out.append("```mermaid")
        out.append(analysis.mermaid.strip())
        out.append("```")
        out.append("")
    else:
        out.append("*(Bukan diagram struktural — tidak ada Mermaid yang dihasilkan.)*")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
