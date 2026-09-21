from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from ..ai_client import AIClient

ProgressFn = Callable[[str], None]

# Tags that are almost always noise in article-style HTML. Kept aggressive
# because the target use case is "someone saved a web page and wants the
# content as Markdown", not "preserve every widget".
_STRIP_TAGS = ("script", "style", "noscript", "iframe", "svg", "form", "nav",
               "footer", "aside", "header")


def convert_html(path: Path, ai: AIClient, progress: ProgressFn | None = None) -> str:
    _emit(progress, f"Membaca HTML: {path.name}")
    raw = _read_text(path)

    try:
        from bs4 import BeautifulSoup  # type: ignore
        import markdownify  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "Paket 'beautifulsoup4' dan 'markdownify' belum terinstall. "
            "Jalankan: pip install beautifulsoup4 markdownify"
        ) from e

    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(list(_STRIP_TAGS)):
        tag.decompose()

    title = _page_title(soup) or path.stem
    body = soup.body or soup

    _emit(progress, "Konversi HTML → Markdown")
    md = markdownify.markdownify(
        str(body),
        heading_style="ATX",
        strip=["a"] if _looks_link_heavy(body) else None,
        bullets="-",
    )
    md = _tidy_blank_lines(md)

    return f"# {title}\n\n{md.strip()}\n"


def _read_text(path: Path) -> str:
    # Try utf-8 first; fall back to cp1252 which covers most Western pages.
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _page_title(soup) -> str:
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    return ""


def _looks_link_heavy(body) -> bool:
    """Strip anchor tags when >30% of text sits inside <a>. That pattern is
    typical of navigation pages / link farms; the raw text reads cleaner
    without the URL noise."""
    total = len(body.get_text(strip=True))
    if total < 200:
        return False
    link_chars = sum(len(a.get_text(strip=True)) for a in body.find_all("a"))
    return link_chars / total > 0.3


_BLANKS_RE = re.compile(r"\n{3,}")


def _tidy_blank_lines(md: str) -> str:
    return _BLANKS_RE.sub("\n\n", md)


def _emit(progress: ProgressFn | None, msg: str) -> None:
    if progress:
        progress(msg)
