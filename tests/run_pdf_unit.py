"""Offline checks for the PDF slide-deck path — no AI, no network.

Covers: slide-deck detection, drawn-vs-referenced images, template-image
skipping, silent placeholders, heading false positives, document language,
vector hints (legend + connectors), page-vision routing and fallback, the
page-transcription retry loop, and the Mermaid validator.

Run after `tests/make_fixtures.py`:
    .venv\\Scripts\\python.exe tests\\run_pdf_unit.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import fitz

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))

from anytomd import ai_client  # noqa: E402
from anytomd.ai_client import (ImageAnalysis, PageTranscription, _transcribe_page_with_retry,  # noqa: E402
                               _validate_mermaid, format_text_layer_entry, page_coverage)
from anytomd.converters import convert  # noqa: E402
from anytomd.converters.pdf import (_Block, _classify_heading_level, _drawn_images,  # noqa: E402
                                    _extract_blocks, _detect_repeated_boundary_text,
                                    _filter_body, _is_slide_deck, _template_image_digests)
from anytomd.converters.pdf_vector import TextBlock, page_vector_hints  # noqa: E402
from anytomd.lang import detect_language  # noqa: E402
from anytomd.options import ConvertOptions  # noqa: E402

SLIDES = FIX / "slide_deck.pdf"
SAMPLE = FIX / "sample.pdf"
DEDUP = FIX / "dedup_test.pdf"

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


class FakeImageAI:
    """Describes every image the same way and counts the calls."""
    is_local = True

    def __init__(self) -> None:
        self.calls = 0

    def analyze_image(self, image_bytes, media_type="image/png", extra_context=""):
        self.calls += 1
        self.last_context = extra_context
        return ImageAnalysis(kind="diagram", description="FAKE-DESCRIPTION", mermaid=None)


class FakePageAI(FakeImageAI):
    """Page vision stand-in: echoes the text layer back as Markdown."""

    def __init__(self, is_local: bool = False, fail: bool = False) -> None:
        super().__init__()
        self.is_local = is_local
        self.fail = fail
        self.pages: list[str] = []

    def transcribe_page(self, png_bytes, text_layer, extra_context=""):
        if self.fail:
            raise RuntimeError("simulated API error")
        self.pages.append(extra_context)
        body = "\n".join(ai_client._text_layer_entries(text_layer))
        return PageTranscription(markdown="PAGE-VISION\n\n" + body, coverage=1.0)


def test_detection() -> None:
    print("detection")
    with fitz.open(SLIDES) as d:
        check("slide_deck.pdf is a slide deck", _is_slide_deck(d))
    with fitz.open(SAMPLE) as d:
        check("sample.pdf is not a slide deck", not _is_slide_deck(d))
    with fitz.open(DEDUP) as d:
        check("dedup_test.pdf is not a slide deck", not _is_slide_deck(d))
        drawn = [_drawn_images(p) for p in d]
        check("logo painted on every page is template", len(_template_image_digests(drawn)) == 1)
    check("English detected", detect_language("The platform and the agents of the library are in the UAE for the visitors.") == "English")
    check("Indonesian detected", detect_language("Dokumen ini adalah contoh yang dibuat untuk pengujian dan tidak untuk dipakai.") == "Bahasa Indonesia")


def test_headings() -> None:
    print("headings")
    step = _Block(page=0, y0=0, y1=10, text="1  CUSTOMER", max_font_size=11, is_bold=True)
    badge = _Block(page=0, y0=0, y1=10, text="1\nPlace an order", max_font_size=11.5, is_bold=True)
    real = _Block(page=0, y0=0, y1=10, text="1.0 Executive Summary", max_font_size=14, is_bold=True)
    check("small bold step label is not a heading", _classify_heading_level(step, 12.5) == 0)
    check("number badge on its own line is not a heading", _classify_heading_level(badge, 12.5) == 0)
    check("numbered section is a heading", _classify_heading_level(real, 11) == 3)


def test_vector_hints() -> None:
    print("vector hints")
    with fitz.open(SLIDES) as d:
        blocks, heights = _extract_blocks(d)
        skip = _detect_repeated_boundary_text(blocks, heights)
        body = _filter_body(blocks[1], heights[1], skip)
        hints = page_vector_hints(d[1], [TextBlock(b.x0, b.y0, b.x1, b.y1, b.text) for b in body])
    tag = {b.text.splitlines()[0]: t for b, t in zip(body, hints.tags)}
    check("footer '- Confidential -' filtered", not any("Confidential" in b.text for b in body))
    check("teal box → Phase 1 legend", tag.get("Source system", "").strip() == "{legend: Phase 1: built now}", tag.get("Source system"))
    check("purple box → Phase 2 legend", tag.get("Voice agent", "").strip() == "{legend: Phase 2}", tag.get("Voice agent"))
    for a, b in [("1  SOURCE", "2  EXTRACT"), ("2  EXTRACT", "3  INDEX"), ("3  INDEX", "4  ANSWER")]:
        check(f"connector {a} → {b}", f'"{a}" → "{b}"' in hints.appendix)
    layer = "\n".join(format_text_layer_entry(0.1, 0.1, b.text) + t for b, t in zip(body, hints.tags))
    layer += "\n\n" + hints.appendix
    entries = ai_client._text_layer_entries(layer)
    check("text layer round-trips without tags", len(entries) == len(body) and not any("{" in e for e in entries))


def test_text_path() -> None:
    print("text path (page vision off)")
    ai = FakeImageAI()
    md = convert(SLIDES, ai, options=ConvertOptions(pdf_page_vision="never"))
    check("template logo never sent to AI", ai.calls == 0, f"{ai.calls} calls")
    check("no skip placeholders", "Gambar dilewati" not in md)
    check("step labels not promoted to headings", "## 1  SOURCE" not in md and "## 1 SOURCE" not in md)
    check("slides separated by ---", md.count("\n---\n") == 2)
    check("text-strategy table keeps whole words", "Voice agent" in md)
    ai = FakeImageAI()
    md = convert(DEDUP, ai, options=ConvertOptions(image_dedup=True))
    check("dedup fixture: only the diagram is described", ai.calls == 1 and md.count("FAKE-DESCRIPTION") == 1,
          f"{ai.calls} calls")
    check("description asks for document language", "Output language: English" in ai.last_context)


def test_page_vision_routing() -> None:
    print("page vision routing")
    ai = FakePageAI(is_local=False)
    opts = ConvertOptions()
    md = convert(SLIDES, ai, options=opts)
    check("auto + cloud + slide deck → page vision on every page", len(ai.pages) == 3, f"{len(ai.pages)} pages")
    check("page vision output used", md.count("PAGE-VISION") == 3)
    check("stats count pages", opts._stats["page_vision_pages"] == 3)
    check("page context names the language", "Document language: English" in ai.pages[0])

    ai = FakePageAI(is_local=True)
    convert(SLIDES, ai, options=ConvertOptions())
    check("auto + local model → no page vision", not ai.pages)

    ai = FakePageAI(is_local=False)
    convert(SAMPLE, ai, options=ConvertOptions())
    check("auto + ordinary PDF → no page vision", not ai.pages)

    ai = FakePageAI(is_local=True)
    convert(SAMPLE, ai, options=ConvertOptions(pdf_page_vision="always"))
    check("always → page vision even for local model / ordinary PDF", len(ai.pages) >= 1)

    ai = FakePageAI(is_local=False)
    convert(SLIDES, ai, options=ConvertOptions(image_mode="none"))
    check("auto + image_mode=none → no page vision", not ai.pages)

    ai = FakePageAI(is_local=False, fail=True)
    opts = ConvertOptions()
    md = convert(SLIDES, ai, options=opts)
    check("API failure falls back to text extraction", "From source system to answer" in md
          and opts._stats["page_vision_fallbacks"] == 3)


def test_retry_loop() -> None:
    print("page transcription retry")
    layer = "\n".join([format_text_layer_entry(0.1, 0.1, "Source system"),
                       format_text_layer_entry(0.5, 0.1, "Voice agent")])
    answers = iter([
        # 1st: misses "Voice agent", Mermaid label broken beyond autofix
        ("## Title\n\nSource system\n\n```mermaid\nflowchart LR\n  n1[\"x --> n2\n```", 100, 10),
        # 2nd: complete and valid
        ("## Title\n\nSource system\n\n```mermaid\nflowchart LR\n  n1[\"Source system\"] --> n2[\"Voice agent\"]\n```", 100, 10),
    ])
    prompts: list[str] = []

    def fake_complete(png, system, user_text, max_tokens):
        prompts.append(user_text)
        return next(answers)

    tr = _transcribe_page_with_retry(fake_complete, b"png", layer, "Page 1 of 1.")
    check("retried once", tr.retried and len(prompts) == 2)
    check("retry prompt lists the missing entry", "Voice agent" in prompts[1] and "missing" in prompts[1])
    check("better attempt kept", tr.coverage == 1.0 and tr.invalid_mermaid == 0)
    check("tokens summed", tr.input_tokens == 200 and tr.output_tokens == 20)

    answers2 = iter([("```mermaid\nflowchart LR\n  n1[\"x --> n2\n```", 1, 1)] * 2)
    tr2 = _transcribe_page_with_retry(lambda *a: next(answers2), b"png", "", "")
    check("invalid Mermaid demoted to plain code", tr2.invalid_mermaid == 1 and "```mermaid" not in tr2.markdown)
    cov, missing = page_coverage(layer, "Source system")
    check("coverage reports missing entry", missing == ["Voice agent"] and 0 < cov < 1)


def test_mermaid_validator() -> None:
    print("mermaid validator")
    check("quoted ? and parentheses are fine", _validate_mermaid('flowchart LR\n  n1["OK? (yes)"] --> n2')[0])
    check("cylinder/stadium/circle shapes are fine", _validate_mermaid("flowchart LR\n  A[(db)] --> B([s]) --> C((c))")[0])
    check("unquoted parentheses rejected", not _validate_mermaid("flowchart LR\n  a[Foo (bar)] --> b")[0])
    check("unclosed subgraph rejected", not _validate_mermaid('flowchart LR\n  subgraph g["G"]\n  n1["x"]')[0])
    check("multi-line quoted label rejected", not _validate_mermaid('flowchart LR\n  n1["a\n  b"]')[0])
    check("sequence diagrams skip bracket checks", _validate_mermaid("sequenceDiagram\n  A->>B: call(x")[0])


def main() -> int:
    for path in (SLIDES, SAMPLE, DEDUP):
        if not path.exists():
            print(f"missing fixture {path.name} — run tests/make_fixtures.py first", file=sys.stderr)
            return 2
    test_detection()
    test_headings()
    test_vector_hints()
    test_text_path()
    test_page_vision_routing()
    test_retry_loop()
    test_mermaid_validator()
    print(f"\n{'ALL PASSED' if not _failures else f'{len(_failures)} FAILED: ' + ', '.join(_failures)}")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
