"""Offline checks for image handling — no AI, no network.

Covers: the TABLE section of image answers (parse, normalise, retry), images
the model calls decorative, transparent PNGs (heuristics and upload), the
perceptual hash that recognises a re-cropped logo, the PPTX picture path
(Office icons, pictures marked decorative, Office-generated alt text), the
PPTX layout path (titles, real list numbering, reading order, flowcharts from
connectors, a word patched over a picture) and slide vision (placeholders
filled from the file, copies dropped, routing).

Run after `tests/make_fixtures.py`:
    .venv\\Scripts\\python.exe tests\\run_pptx_unit.py
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

from PIL import Image

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
os.environ["ANYTOMD_PPTX_RENDERER"] = "pil"  # offline and deterministic: no PowerPoint

from anytomd.ai_client import (TABLE_RETRY_HINT, ImageAnalysis, PageTranscription,  # noqa: E402
                               _analyze_with_retry, _finalize_table, _normalize_image,
                               _parse_sections, _validate_mermaid)
from anytomd.converters import pptx as pptx_conv  # noqa: E402
from anytomd.converters import convert  # noqa: E402
from anytomd.image_heuristics import (is_decorative, is_icon_sized, load_rgb, perceptual_hash,  # noqa: E402
                                      same_picture)
from anytomd.options import ConvertOptions  # noqa: E402
from make_fixtures import _logo_png, _table_png, _transparent_text_png  # noqa: E402

PICTURES = FIX / "pictures.pptx"
LAYOUT = FIX / "layout.pptx"

TABLE_ANSWER = (
    "KIND: table\n---\nDESCRIPTION: Tabel nilai per tahun.\n---\n"
    "TABLE:\n| Tahun | Nilai |\n|---|---|\n| 2031 | 18,40 |\n| 2032 | 17,95 |\n---\nMERMAID: NONE"
)

_failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def test_table_parsing() -> None:
    print("TABLE section")
    parsed = _parse_sections(TABLE_ANSWER)
    table, _ = _finalize_table(parsed.table)
    check("kind table parsed", parsed.kind == "table" and parsed.description == "Tabel nilai per tahun.")
    check("table normalised", table == "| Tahun | Nilai |\n| --- | --- |\n| 2031 | 18,40 |\n| 2032 | 17,95 |",
          repr(table))
    check("MERMAID NONE still read", parsed.mermaid == "NONE" and parsed.text is None)

    two = "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n| C |\n| :---: |\n| 3 |"
    t2, _ = _finalize_table(two)
    check("two tables kept apart", t2 is not None and t2.count("| --- |") == 2)
    check("short row padded", _finalize_table("| A | B |\n|---|---|\n| 1 |")[0] == "| A | B |\n| --- | --- |\n| 1 |  |")
    too_wide, reason = _finalize_table("| A | B |\n|---|---|\n| 1 | 2 | 3 |")
    check("row wider than header rejected", too_wide is None and "3 cells" in reason, reason)
    no_sep, reason = _finalize_table("| A | B |\n| 1 | 2 |\n| 3 | 4 |")
    check("missing separator rejected", no_sep is None and "separator" in reason, reason)
    check("NONE → no table", _finalize_table("NONE")[0] is None)
    check("fenced table accepted",
          _finalize_table(_parse_sections("KIND: table\n---\nDESCRIPTION: x\n---\nTABLE: ```markdown\n"
                                           "| A |\n|---|\n| 1 |\n```\n---\nMERMAID: NONE").table)[0]
          == "| A |\n| --- |\n| 1 |")
    old = _parse_sections("KIND: photo\n---\nDESCRIPTION: Foto gedung.\n---\nMERMAID: NONE")
    check("three-section answer (no TABLE/TEXT) still parses", old.kind == "photo" and old.table is None
          and old.text is None and old.description == "Foto gedung.")
    escaped = _finalize_table("| A | B |\n|---|---|\n| x \\| y | 2 |")[0]
    check("escaped pipe stays in its cell", escaped is not None and "| x \\| y | 2 |" in escaped, repr(escaped))
    halves = _finalize_table("| Tahun | Nilai | Tahun | Nilai |\n|---|---|---|---|\n"
                             "| 2031 | 18,40 | 2033 | 17,20 |\n| 2032 | 17,95 | | |")[0]
    check("side-by-side halves stacked into one table",
          halves == "| Tahun | Nilai |\n| --- | --- |\n| 2031 | 18,40 |\n| 2032 | 17,95 |\n| 2033 | 17,20 |", repr(halves))
    distinct = _finalize_table("| A | B | A | C |\n|---|---|---|---|\n| 1 | 2 | 3 | 4 |")[0]
    check("different headers stay side by side", distinct is not None and distinct.startswith("| A | B | A | C |"))
    text = _parse_sections("KIND: text\n---\nDESCRIPTION: Ketentuan layanan.\n---\nTABLE: NONE\n---\n"
                           "TEXT: Layanan ini bersifat opsional.\n\n1. Satu\n---\nMERMAID: NONE")
    check("TEXT section keeps its multi-line transcription",
          text.kind == "text" and text.text == "Layanan ini bersifat opsional.\n\n1. Satu", repr(text.text))
    check("TEXT NONE → no text", _parse_sections("KIND: photo\n---\nDESCRIPTION: x\n---\nTEXT: NONE").text is None)
    block = _analyze_with_retry(lambda _p, _c: "KIND: text\n---\nDESCRIPTION: Ketentuan layanan.\n---\n"
                                "TABLE: NONE\n---\nTEXT: Layanan ini bersifat opsional.\n---\nMERMAID: NONE",
                                b"", "").to_markdown_block("img")
    check("picture of text renders as the text alone", block == "Layanan ini bersifat opsional.", repr(block))
    photo = _analyze_with_retry(lambda _p, _c: "KIND: photo\n---\nDESCRIPTION: Foto gedung\nkantor.\n---\n"
                                "TABLE: NONE\n---\nTEXT: NONE\n---\nMERMAID: NONE", b"", "").to_markdown_block("x")
    check("other pictures: one Gambar line, no placeholder", photo == "> **Gambar (photo):** Foto gedung kantor.",
          repr(photo))


def test_analysis_retry() -> None:
    print("Image analysis")
    prompts: list[str] = []
    answers = ["KIND: table\n---\nDESCRIPTION: Tabel.\n---\nTABLE: NONE\n---\nMERMAID: NONE", TABLE_ANSWER]

    def call(_png, ctx):
        prompts.append(ctx)
        return answers[len(prompts) - 1]

    res = _analyze_with_retry(call, b"", "ctx")
    check("table without TABLE → one retry", len(prompts) == 2)
    check("retry prompt carries the table hint", TABLE_RETRY_HINT.split("(")[0] in prompts[1])
    check("retried table kept", res.table is not None and "| 2032 | 17,95 |" in res.table)
    block = res.to_markdown_block("img")
    check("table picture renders as the table alone", block.startswith("| Tahun |") and "Tabel nilai" not in block)

    ctx = ("Gambar ini dari slide 6 sebuah PPTX. Output language: Bahasa Indonesia.\n"
           "Judul slide: Nilai\nTeks slide: Terdapat tambahan nilai bagi anggota yang memiliki pasangan "
           "| Contoh hitungan - Nilai dasar = 3 juta per bulan…")
    echo = ("KIND: text\n---\nDESCRIPTION: Teks.\n---\nTABLE: NONE\n---\nTEXT: Terdapat tambahan nilai bagi "
            "anggota yang memiliki pasangan | Contoh hitungan - Nilai dasar = 3 juta\n---\nMERMAID: NONE")
    calls: list[str] = []

    def echo_then_table(_png, c):
        calls.append(c)
        return echo if len(calls) == 1 else TABLE_ANSWER

    fixed = _analyze_with_retry(echo_then_table, b"", ctx)
    check("answer copying the slide-text context → retried, second answer kept",
          len(calls) == 2 and "copied the slide text" in calls[1] and fixed.table is not None)
    stubborn = _analyze_with_retry(lambda _p, _c: echo, b"", ctx)
    check("still copying after the retry → dropped silently", stubborn.silent and stubborn.to_markdown_block() == "")
    own_words = _analyze_with_retry(lambda _p, _c: TABLE_ANSWER, b"", ctx)
    check("an answer that does not copy the context is not retried", own_words.table is not None)
    logo_other = _analyze_with_retry(
        lambda _p, _c: "KIND: other\n---\nDESCRIPTION: Logo perusahaan dengan nama NORDVIK.\n---\nTABLE: NONE"
                       "\n---\nTEXT: NONE\n---\nMERMAID: NONE", b"", "")
    check("'Logo …' description under another kind → decorative, silent", logo_other.silent)
    said_table: list[str] = []

    def described_then_table(_png, c):
        said_table.append(c)
        return ("KIND: other\n---\nDESCRIPTION: Gambar ini menunjukkan tabel nilai per tahun.\n---\nTABLE: NONE"
                "\n---\nTEXT: NONE\n---\nMERMAID: NONE") if len(said_table) == 1 else TABLE_ANSWER

    described = _analyze_with_retry(described_then_table, b"", "")
    check("'shows a table' under another kind → retried as a table",
          len(said_table) == 2 and described.table is not None)
    icon = io.BytesIO()
    Image.new("RGBA", (125, 108), (200, 60, 60, 255)).save(icon, format="PNG")
    check("icon-sized picture (≤128 px) recognised", is_icon_sized(icon.getvalue())
          and not is_icon_sized(_table_png()))
    mid_sep = _finalize_table("| A | B |\n|---|---|\n| 1 | 2 |\n| --- | --- |\n| 3 | 4 |")[0]
    check("a second separator row mid-table is dropped", mid_sep == "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |",
          repr(mid_sep))

    good = _analyze_with_retry(lambda _p, _c: TABLE_ANSWER, b"", "")
    check("valid table → no retry", good.table is not None)

    logo = _analyze_with_retry(
        lambda _p, _c: "KIND: decorative\n---\nDESCRIPTION: Logo.\n---\nTABLE: NONE\n---\nMERMAID: NONE", b"", "")
    check("decorative → silent, no Markdown", logo.silent and logo.to_markdown_block("x") == "")


def test_transparency() -> None:
    print("Transparent PNGs")
    text_png = _transparent_text_png()
    check("fixture reproduces the trap: plain RGB convert is all black",
          Image.open(io.BytesIO(text_png)).convert("RGB").getextrema() == ((0, 0), (0, 0), (0, 0)))
    check("text on transparent background is not decorative", not is_decorative(text_png))
    check("load_rgb flattens onto white", load_rgb(text_png).getpixel((0, 0)) == (255, 255, 255))
    sent = Image.open(io.BytesIO(_normalize_image(text_png)))
    check("upload keeps the transparency", sent.mode == "RGBA" and sent.getpixel((0, 0))[3] == 0)

    buf = io.BytesIO()
    Image.new("RGBA", (200, 120), (30, 90, 200, 160)).save(buf, format="PNG")
    check("plain colour fill with alpha still decorative", is_decorative(buf.getvalue()))


def test_perceptual_hash() -> None:
    print("Perceptual hash")
    a, b = perceptual_hash(_logo_png()), perceptual_hash(_logo_png(1.3, pad=20))
    t = perceptual_hash(_table_png())
    check("same logo, other size and crop → same picture", bool(a and b and same_picture(a, b)),
          f"{bin(a[0] ^ b[0]).count('1') if a and b else '?'} bits")
    check("logo vs table → different", bool(a and t and not same_picture(a, t)))


class FakeVisionAI:
    """Answers like a vision model would, keyed on the picture it gets."""
    is_local = True

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def analyze_image(self, image_bytes, media_type="image/png", extra_context=""):
        return _analyze_with_retry(self._call, image_bytes, extra_context)

    def _call(self, image_bytes, _ctx) -> str:
        img = Image.open(io.BytesIO(image_bytes))
        self.calls.append(img.size)
        if img.mode == "RGBA":
            return "KIND: decorative\n---\nDESCRIPTION: Logo NORDVIK.\n---\nTABLE: NONE\n---\nMERMAID: NONE"
        if img.mode == "P":
            return ("KIND: text\n---\nDESCRIPTION: Ketentuan layanan.\n---\nTABLE: NONE\n---\n"
                    "TEXT: Layanan ini bersifat opsional dan dapat diaktifkan oleh setiap anggota.\n---\n"
                    "MERMAID: NONE")
        return TABLE_ANSWER


def test_pptx_pictures() -> None:
    print("PPTX pictures")
    ai = FakeVisionAI()
    opts = ConvertOptions()
    md = convert(PICTURES, ai, options=opts)
    s = opts._stats
    check("icon and picture marked decorative never sent; repeated logo recognised",
          len(ai.calls) == 4, f"{len(ai.calls)} calls: {ai.calls}")
    check("Office icon name not emitted as alt text", "Bullseye" not in md)
    check("generated alt text ignored → picture analysed", "automatically generated" not in md
          and "alt text penulis" not in md)
    check("table transcribed on both slides", md.count("| 2031 | 18,40 |") == 2)
    check("text picture on transparent background analysed", "bersifat opsional" in md)
    check("logo leaves no trace", "NORDVIK" not in md and "decorative" not in md)
    check("skipped pictures leave no blank-line runs", "\n\n\n" not in md)
    check("stats: 1 logo judged by AI, 1 recognised locally",
          s["ai_decorative"] == 1 and s["skipped_decorative"] == 1, str(s))
    check("summary names logo/icon", "logo/ikon" in opts.stats_summary())


class FakeLayoutAI:
    """Pictures on layout.pptx: records what it was sent, answers as text."""
    is_local = True

    def __init__(self) -> None:
        self.sizes: list[tuple[int, int]] = []

    def analyze_image(self, image_bytes, media_type="image/png", extra_context=""):
        self.sizes.append(Image.open(io.BytesIO(image_bytes)).size)
        self.context = extra_context
        return ImageAnalysis(kind="text", description="Kalimat.", mermaid=None,
                             text="Anggota Baru dapat memilih pencairan tunai.")


def _slide(md: str, n: int) -> str:
    return md.split("\n---\n")[n - 1]


def test_pptx_layout() -> None:
    print("PPTX layout (no slide vision)")
    ai = FakeLayoutAI()
    md = convert(LAYOUT, ai, options=ConvertOptions(pptx_slide_vision="never"))
    s1, s2, s3 = _slide(md, 1), _slide(md, 2), _slide(md, 3)
    check("text-box title found, soft break → space", "## PROGRAM TABUNGAN ANGGOTA Tahun 2027" in s1, s1[:120])
    check("no vertical-tab characters anywhere", "\v" not in md)
    check("page number 'Hal. 1' dropped", "Hal. 1" not in md)
    check("no Slide/Layout/Placeholder scaffolding",
          not re.search(r"^## Slide \d|Layout:|Placeholder", md, re.M))
    expected = ("1. Biaya operasional naik\n"
                "    - Harga bahan naik\n"
                "    - Upah naik\n"
                "2. Kewajiban lama besar\n"
                "   Penjelasan kewajiban lama.\n"
                "3. Aset habis tahun 2030\n"
                "   Penjelasan aset.")
    check("auto + hand-typed numbering, nested bullets, continuations", expected in s1, s1)
    check("plain paragraphs stay paragraphs", "Tujuan:\n\nMenjaga layanan tetap berjalan." in s1)
    order = [s2.find(k) for k in ("KIRI SATU", "KIRI DUA", "KIRI TIGA", "KANAN SATU", "| 2031 | 18,40 |")]
    check("two columns read column by column (then the table)", -1 not in order and order == sorted(order),
          str(order))
    check("bold heading paragraph → **bold**", "**KIRI SATU**" in s2)
    check("placeholder title → ##", "## DUA KOLOM" in s2)

    blocks = re.findall(r"```mermaid\n(.*?)```", s3, re.S)
    check("flowchart → one valid Mermaid block", len(blocks) == 1 and _validate_mermaid(blocks[0])[0])
    src = blocks[0] if blocks else ""
    ids = dict((label, nid) for nid, label in re.findall(r'(n\d+)\S*?"([^"]+)"', src))
    edges = set(re.findall(r"(n\d+) -->(?:\|([^|]*)\|)? (n\d+)", src))
    want = {("Program Tabungan", "", "Bersifat opsional"), ("Bersifat opsional", "", "Anggota Lama"),
            ("Bersifat opsional", "", "Anggota Baru"), ("Anggota Lama", "ya", "Pencairan Tunai"),
            ("Anggota Baru", "", "Pindah ke Reksa Dana"), ("Pencairan Tunai", "", "Target: 50% peserta ikut"),
            ("Pindah ke Reksa Dana", "", "Target: 50% peserta ikut"),
            ("Target: 50% peserta ikut", "", "EVALUASI")}
    got = {(k1, lbl, k2) for a, lbl, b in edges
           for k1, v1 in ids.items() if v1 == a for k2, v2 in ids.items() if v2 == b}
    check("every edge from the connectors, block arrow and label included", got == want, str(got ^ want))
    check("divider line is not an edge; node texts not repeated as paragraphs",
          s3.count("Bersifat opsional") == 1 and "\nya\n" not in s3)
    pic_w = 9 / 13.333 * 2560
    check("patched picture sent as a crop of the slide render",
          len(ai.sizes) == 1 and abs(ai.sizes[0][0] - pic_w) <= 3, str(ai.sizes))
    check("patch word not left as loose text", "\nBaru\n" not in s3 and "Anggota Baru dapat" in s3)
    check("picture description asks for the document language", "Output language:" in ai.context)


class FakeSlideAI(FakeLayoutAI):
    """A cloud-like model with slide vision; answers every slide with the
    placeholders in the order it was given, plus mistakes to clean up."""
    is_local = False
    coverage = 1.0

    def __init__(self) -> None:
        super().__init__()
        self.layers: list[str] = []

    def transcribe_page(self, png_bytes, text_layer, extra_context=""):
        self.layers.append(text_layer)
        tokens = re.findall(r"\[\[[A-Z]+ \d+\]\]", text_layer.split("\n\n")[0])
        body = "\n\n".join(f"**{t}**" for t in tokens[1:])  # first token left out on purpose
        copy = "| 2031 | 18,40 |\n| 2032 | 17,95 |" if "TABLE" in text_layer else ""
        mermaid = "```mermaid\nflowchart LR\n  a --> b\n```" if "DIAGRAM" in text_layer else ""
        md = "\n\n".join(x for x in ("### Isi slide", body, copy, mermaid) if x)
        return PageTranscription(markdown=md, coverage=self.coverage, input_tokens=10, output_tokens=5)


def test_slide_vision() -> None:
    print("PPTX slide vision")
    ai = FakeSlideAI()
    opts = ConvertOptions()
    md = convert(LAYOUT, ai, options=opts)
    s2, s3 = _slide(md, 2), _slide(md, 3)
    check("auto + cloud model → one transcription per slide", len(ai.layers) == 3 and
          opts._stats["page_vision_pages"] == 3, str(len(ai.layers)))
    layer3 = ai.layers[2]
    check("flowchart and picture enter the text layer as placeholders",
          "[[DIAGRAM 1]]" in layer3 and "[[PICTURE 1]]" in layer3 and "Bersifat opsional" not in layer3)
    check("text layer carries the real numbering", "1. Biaya operasional naik" in ai.layers[0])
    check("placeholders filled with the file's content", "[[" not in md and "| 2031 | 18,40 |" in s2
          and "Anggota Baru dapat memilih" in s3)
    check("a placeholder the model left out is appended", s3.count("```mermaid") == 1 and "EVALUASI" in s3)
    check("model's own table copy and Mermaid dropped", s2.count("| 2031 | 18,40 |") == 1
          and "a --> b" not in md)
    check("slide heading added when the model wrote none", s2.lstrip().startswith("## DUA KOLOM"), s2[:60])
    local = FakeSlideAI()
    local.is_local = True
    convert(LAYOUT, local, options=ConvertOptions())
    check("auto + local model → no slide vision", not local.layers)
    convert(LAYOUT, local, options=ConvertOptions(pptx_slide_vision="always"))
    check("always → slide vision even for a local model", len(local.layers) == 3)
    cloud = FakeSlideAI()
    convert(LAYOUT, cloud, options=ConvertOptions(pptx_slide_vision="never"))
    check("never → no slide vision", not cloud.layers)
    lossy = FakeSlideAI()
    lossy.coverage = 0.5
    lossy_opts = ConvertOptions()
    lossy_md = convert(LAYOUT, lossy, options=lossy_opts)
    check("coverage still low after retry → the XML layout is used",
          "1. Biaya operasional naik" in lossy_md and "### Isi slide" not in lossy_md
          and lossy_opts._stats["page_vision_fallbacks"] == 3)
    tokens = {"[[TABLE 1]]": "| A |\n| --- |\n| 1 |"}
    done = pptx_conv.finish_slide_markdown("## T\n\n[[TABLE 1]]\n\n[[TABLE 1]]", tokens, "T")
    check("a placeholder written twice is filled once", done.count("| 1 |") == 1, repr(done))


def main() -> int:
    for f in (PICTURES, LAYOUT):
        if not f.exists():
            print(f"Missing {f} — run tests/make_fixtures.py first")
            return 1
    test_table_parsing()
    test_analysis_retry()
    test_transparency()
    test_perceptual_hash()
    test_pptx_pictures()
    test_pptx_layout()
    test_slide_vision()
    print("ALL PASSED" if not _failures else f"{len(_failures)} FAILED")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
