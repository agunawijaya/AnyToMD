"""Generate test fixtures: PNG diagram, sample PDF, sample PPTX."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.shared import Inches as DocxInches
from openpyxl import Workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Emu, Inches, Pt
import fitz  # PyMuPDF

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
FIX.mkdir(exist_ok=True)


def _font(size: int = 20) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def make_diagram_png() -> Path:
    """Draw a simple flowchart: Start -> Process -> Decision -> End/Retry."""
    img = Image.new("RGB", (600, 400), "white")
    d = ImageDraw.Draw(img)
    f = _font(18)

    # Boxes: (x0, y0, x1, y1, label)
    boxes = [
        (240, 20, 360, 70, "Start"),
        (240, 120, 360, 180, "Process"),
        (240, 230, 360, 300, "OK?"),
        (60, 340, 180, 390, "Retry"),
        (420, 340, 540, 390, "End"),
    ]
    for x0, y0, x1, y1, label in boxes:
        d.rectangle((x0, y0, x1, y1), outline="black", width=2)
        bbox = d.textbbox((0, 0), label, font=f)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((x0 + (x1 - x0 - tw) / 2, y0 + (y1 - y0 - th) / 2), label, fill="black", font=f)

    # Arrows
    def arrow(x1, y1, x2, y2, label=""):
        d.line((x1, y1, x2, y2), fill="black", width=2)
        d.polygon(
            [(x2, y2), (x2 - 6, y2 - 12), (x2 + 6, y2 - 12)] if y2 > y1
            else [(x2, y2), (x2 - 6, y2 + 12), (x2 + 6, y2 + 12)],
            fill="black",
        )
        if label:
            d.text((x1 + 6, (y1 + y2) / 2 - 10), label, fill="black", font=f)

    arrow(300, 70, 300, 120)
    arrow(300, 180, 300, 230)
    # Decision → End (right, "Yes")
    d.line((360, 265, 480, 265), fill="black", width=2)
    d.line((480, 265, 480, 340), fill="black", width=2)
    d.polygon([(480, 340), (474, 328), (486, 328)], fill="black")
    d.text((400, 245), "Yes", fill="black", font=f)
    # Decision → Retry (left, "No")
    d.line((240, 265, 120, 265), fill="black", width=2)
    d.line((120, 265, 120, 340), fill="black", width=2)
    d.polygon([(120, 340), (114, 328), (126, 328)], fill="black")
    d.text((150, 245), "No", fill="black", font=f)

    p = FIX / "diagram.png"
    img.save(p)
    return p


def _draw_pdf_table(page, left, top, col_widths, headers, rows,
                    row_height=22, header_fontsize=10, cell_fontsize=10):
    """Render a bordered table on a PyMuPDF page — required for pdfplumber
    detection (finds tables from rulings)."""
    total_w = sum(col_widths)
    all_rows = [headers] + list(rows)
    n_rows = len(all_rows)
    # Horizontal rulings
    for j in range(n_rows + 1):
        y = top + j * row_height
        page.draw_line((left, y), (left + total_w, y), color=(0, 0, 0), width=0.6)
    # Vertical rulings
    for i in range(len(col_widths) + 1):
        x = left + sum(col_widths[:i])
        page.draw_line((x, top), (x, top + n_rows * row_height), color=(0, 0, 0), width=0.6)
    # Text (baseline is ~row_height - 6 from top)
    for j, row in enumerate(all_rows):
        for i, cell in enumerate(row):
            x = left + sum(col_widths[:i]) + 4
            y = top + j * row_height + row_height - 6
            fs = header_fontsize if j == 0 else cell_fontsize
            page.insert_text((x, y), str(cell), fontsize=fs)


def make_sample_pdf() -> Path:
    """3-page PDF: header + footer + page# on every page; body varies; one page has an image."""
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((50, 40), "Company Confidential — Internal Use Only",
                         fontsize=9, color=(0.4, 0.4, 0.4))
        page.insert_text((50, 750), f"Page {i + 1} of 3", fontsize=9, color=(0.4, 0.4, 0.4))
        page.insert_text((300, 750), "Q3 2026 Report", fontsize=9, color=(0.4, 0.4, 0.4))

        body_x, body_y = 72, 100
        if i == 0:
            page.insert_text((body_x, body_y), "Executive Summary", fontsize=18)
            page.insert_text((body_x, body_y + 40),
                             "This report summarizes Q3 2026 revenue and operational\n"
                             "performance across all business units. Highlights include a\n"
                             "12% YoY revenue increase and improved margins in the APAC\n"
                             "region driven by enterprise contract renewals.",
                             fontsize=11)
        elif i == 1:
            page.insert_text((body_x, body_y), "Regional Performance", fontsize=18)
            page.insert_text((body_x, body_y + 40),
                             "APAC delivered the strongest quarter with $14.2M in bookings.\n"
                             "EMEA continued its recovery trajectory. Americas remained flat\n"
                             "due to extended procurement cycles at two Tier-1 accounts.",
                             fontsize=11)
            # A visible-bordered table so pdfplumber can detect it.
            _draw_pdf_table(
                page,
                left=body_x, top=body_y + 130,
                col_widths=(90, 130, 100),
                headers=("Region", "Bookings (USD M)", "YoY Growth"),
                rows=(("APAC", "14.2", "+21%"),
                      ("EMEA", "9.8",  "+7%"),
                      ("Americas", "11.1", "+1%")),
            )
        else:
            page.insert_text((body_x, body_y), "Architecture Overview", fontsize=18)
            page.insert_text((body_x, body_y + 40),
                             "The following diagram outlines the ingestion pipeline used by\n"
                             "the reporting stack.", fontsize=11)
            img_path = FIX / "diagram.png"
            if img_path.exists():
                page.insert_image(fitz.Rect(body_x, body_y + 100, body_x + 400, body_y + 366),
                                  filename=str(img_path))

    out = FIX / "sample.pdf"
    doc.save(out)
    doc.close()
    return out


def make_sample_pptx() -> Path:
    prs = Presentation()
    # Slide 1: title + speaker notes
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "Q3 2026 Kickoff"
    s1.placeholders[1].text = "Aguna — 2026-08-05"
    s1.notes_slide.notes_text_frame.text = (
        "Perkenalkan tim baru sebelum mulai. Sebutkan target 12% YoY."
    )

    # Slide 2: title + bullets
    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Goals for the Quarter"
    tf = s2.placeholders[1].text_frame
    tf.text = "Ship the reporting pipeline v2"
    tf.add_paragraph().text = "Onboard 3 new enterprise customers"
    tf.add_paragraph().text = "Reduce p95 API latency by 30%"
    s2.notes_slide.notes_text_frame.text = (
        "Latency: fokus di endpoint /search yang p95-nya 2.4s sekarang."
    )

    # Slide 3: image with author-supplied ALT TEXT (should skip AI)
    s3 = prs.slides.add_slide(prs.slide_layouts[5])  # title only
    s3.shapes.title.text = "Ingestion Pipeline"
    img = FIX / "diagram.png"
    if img.exists():
        pic = s3.shapes.add_picture(str(img), Inches(2), Inches(1.5), width=Inches(6))
        _set_pic_alt(
            pic,
            "Diagram alur ingestion pipeline: Start → Process → Decision (OK?) → "
            "End (Yes) atau Retry (No). Ditulis penulis sebagai Alt Text.",
        )

    # Slide 4: native PPTX chart (structured data, AI harus di-skip)
    s4 = prs.slides.add_slide(prs.slide_layouts[5])
    s4.shapes.title.text = "Bookings by Region"
    chart_data = CategoryChartData()
    chart_data.categories = ["Q1", "Q2", "Q3"]
    chart_data.add_series("APAC", (4.2, 5.1, 4.9))
    chart_data.add_series("EMEA", (2.8, 3.4, 3.6))
    chart = s4.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED,
        Inches(1.5), Inches(1.8), Inches(7), Inches(4.5),
        chart_data,
    ).chart
    chart.has_title = True
    chart.chart_title.text_frame.text = "Bookings by Region (USD M)"
    s4.notes_slide.notes_text_frame.text = (
        "Highlight APAC Q2 sebagai puncak. Jangan lupa mention EMEA recovery."
    )

    out = FIX / "sample.pptx"
    prs.save(out)
    return out


def _table_png() -> bytes:
    """A two-column factor table drawn as a bitmap (no text layer)."""
    rows = [("Tahun", "Nilai"), ("2031", "18,40"), ("2032", "17,95"), ("2033", "17,20"), ("2034", "16,85")]
    img = Image.new("RGB", (320, 30 * len(rows)), "white")
    d = ImageDraw.Draw(img)
    for i, (a, b) in enumerate(rows):
        y = 30 * i
        if i == 0:
            d.rectangle([0, 0, 319, 29], fill=(40, 60, 90))
        d.rectangle([0, y, 319, y + 29], outline="black")
        d.line([140, y, 140, y + 29], fill="black")
        colour = "white" if i == 0 else "black"
        d.text((20, y + 5), a, fill=colour, font=_font(18))
        d.text((160, y + 5), b, fill=colour, font=_font(18))
    return _png_bytes(img)


def _transparent_text_png() -> bytes:
    """Black text on a transparent background, stored the way slide exports
    store it: a palette PNG whose transparent entry is black (0, 0, 0)."""
    img = Image.new("P", (900, 110), 0)
    img.putpalette([0, 0, 0, 0, 0, 0] + [0] * 762)  # entry 0 transparent, entry 1 opaque, both black
    d = ImageDraw.Draw(img)
    d.text((10, 10), "Layanan ini bersifat opsional dan dapat diaktifkan oleh setiap anggota.",
           fill=1, font=_font(26))
    d.text((10, 60), "1. Pendaftaran dibuka setiap awal bulan.", fill=1, font=_font(26))
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=bytes([0, 255]))
    return buf.getvalue()


def _logo_png(scale: float = 1.0, pad: int = 4) -> bytes:
    """A made-up brand logo on a transparent background; `scale`/`pad` give
    the re-exported variants a deck typically carries."""
    w, h = int(200 * scale), int(50 * scale)
    img = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(pad, pad + h), (pad + h * 0.8, pad), (pad + h * 0.8, pad + h)], fill=(20, 120, 200, 255))
    d.text((pad + int(h * 0.95), pad + int(h * 0.2)), "NORDVIK", fill=(40, 40, 60, 255),
           font=_font(int(28 * scale)))
    return _png_bytes(img)


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _add_ext(pic, xpath: str, uri: str, child_xml: str) -> None:
    """Append `<a:ext uri=...>child</a:ext>` to the extLst of the node at `xpath`."""
    from lxml import etree
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    node = pic._element.xpath(xpath)[0]
    ext_lst = node.find(f"{{{a}}}extLst")
    if ext_lst is None:
        ext_lst = etree.SubElement(node, f"{{{a}}}extLst")
    ext = etree.SubElement(ext_lst, f"{{{a}}}ext")
    ext.set("uri", uri)
    ext.append(etree.fromstring(child_xml))


def make_pictures_pptx() -> Path:
    """Pictures that must NOT be handled by their surface: a table as a
    bitmap, text on a transparent background, a logo repeated with another
    crop, an Office library icon, a picture marked decorative, and one whose
    alt text Office generated."""
    prs = Presentation()
    blank = prs.slide_layouts[5]  # title only
    s1 = prs.slides.add_slide(blank)
    s1.shapes.title.text = "Nilai per Tahun"
    s1.shapes.add_picture(io.BytesIO(_logo_png()), Inches(0.2), Inches(0.1), width=Inches(1.4))
    s1.shapes.add_picture(io.BytesIO(_table_png()), Inches(5), Inches(1.6), width=Inches(4))
    s1.shapes.add_picture(io.BytesIO(_transparent_text_png()), Inches(0.5), Inches(5.2), width=Inches(9))

    icon = s1.shapes.add_picture(io.BytesIO(_logo_png(0.5)), Inches(0.3), Inches(4.4), width=Inches(0.5))
    _set_pic_alt(icon, "Bullseye outline")
    rid = icon._element.xpath(".//*[local-name()='blip']/@*[local-name()='embed']")[0]
    _add_ext(icon, ".//*[local-name()='blip']", "{96DAC541-7B7A-43D3-8B79-37D633B846F1}",
             '<asvg:svgBlip xmlns:asvg="http://schemas.microsoft.com/office/drawing/2016/SVG/main" '
             'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
             f'r:embed="{rid}"/>')

    deco = s1.shapes.add_picture(io.BytesIO(_logo_png(0.7, pad=12)), Inches(8.5), Inches(0.1), width=Inches(1))
    _add_ext(deco, ".//*[local-name()='cNvPr']", "{C183D7F6-B498-43B3-948B-1728B52AA6E4}",
             '<adec:decorative xmlns:adec="http://schemas.microsoft.com/office/drawing/2017/decorative" '
             'val="1"/>')

    s2 = prs.slides.add_slide(blank)
    s2.shapes.title.text = "Contoh Perhitungan"
    s2.shapes.add_picture(io.BytesIO(_logo_png(1.3, pad=20)), Inches(0.2), Inches(0.1), width=Inches(1.6))
    auto = s2.shapes.add_picture(io.BytesIO(_table_png()), Inches(1), Inches(2), width=Inches(4))
    _set_pic_alt(auto, "A table with numbers and text. Description automatically generated")

    out = FIX / "pictures.pptx"
    prs.save(out)
    return out


def _para_xml(p, *, bullet: str | None = None, mar_l: int | None = None, indent: int | None = None,
              size: int = 16, bold: bool | None = None) -> None:
    """Set a paragraph's list formatting the way PowerPoint writes it."""
    from lxml import etree
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ppr = p._p.get_or_add_pPr()
    if mar_l is not None:
        ppr.set("marL", str(mar_l))
    if indent is not None:
        ppr.set("indent", str(indent))
    if bullet == "none":
        etree.SubElement(ppr, f"{{{a}}}buNone")
    elif bullet == "number":
        etree.SubElement(ppr, f"{{{a}}}buAutoNum").set("type", "arabicPeriod")
    elif bullet == "char":
        etree.SubElement(ppr, f"{{{a}}}buFont").set("typeface", "Arial")
        etree.SubElement(ppr, f"{{{a}}}buChar").set("char", "•")
    for r in p.runs:
        r.font.size = Pt(size)
        if bold is not None:
            r.font.bold = bold


def _text_box(slide, left, top, width, height, paras, *, border=False, size=16, fill_white=False):
    """paras: list of (text, kwargs for _para_xml)."""
    from pptx.dml.color import RGBColor
    tb = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = tb.text_frame
    tf.word_wrap = True
    for i, (text, kw) in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = text
        _para_xml(p, size=kw.pop("size", size), **kw)
    if border:
        tb.line.color.rgb = RGBColor(0x1F, 0x4E, 0x78)
    if fill_white:
        tb.fill.solid()
        tb.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return tb


def _connect(slide, a, a_pt, b, b_pt, *, elbow=False, arrow=True):
    from lxml import etree
    kind = MSO_CONNECTOR.ELBOW if elbow else MSO_CONNECTOR.STRAIGHT
    c = slide.shapes.add_connector(kind, 0, 0, 0, 0)
    c.begin_connect(a, a_pt)   # 0 top, 1 left, 2 bottom, 3 right
    c.end_connect(b, b_pt)
    ln = c.line._get_or_add_ln()
    etree.SubElement(ln, "{http://schemas.openxmlformats.org/drawingml/2006/main}tailEnd").set(
        "type", "triangle" if arrow else "none")
    return c


def _patched_text_png() -> tuple[bytes, tuple[int, int, int, int]]:
    """A sentence as a transparent bitmap, plus the pixel box of the word the
    slide patches over ("Lama")."""
    font = _font(26)
    before, word = "Anggota ", "Lama"
    img = Image.new("P", (900, 60), 0)
    img.putpalette([0, 0, 0, 0, 0, 0] + [0] * 762)
    d = ImageDraw.Draw(img)
    d.text((10, 12), "Anggota Lama dapat memilih pencairan tunai.", fill=1, font=font)
    x0 = 10 + int(d.textlength(before, font=font))
    x1 = x0 + int(d.textlength(word, font=font))
    buf = io.BytesIO()
    img.save(buf, format="PNG", transparency=bytes([0, 255]))
    return buf.getvalue(), (x0 - 2, 8, x1 + 2, 48)


def make_layout_pptx() -> Path:
    """Three slides with the layouts real decks use and naive extraction
    flattens: a text-box title with a soft break and a "Hal. 1" page number,
    auto numbering mixed with hand-typed numbers, nested bullets and
    continuation paragraphs; two columns and a native table; a flowchart of
    bordered text boxes glued by connectors (straight, elbow, an edge label,
    a block arrow) and a text bitmap with a word patched over it."""
    from pptx.dml.color import RGBColor
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(blank)
    _text_box(s1, 2.5, 0.6, 8, 0.8, [("PROGRAM TABUNGAN ANGGOTA\vTahun 2027", {"bullet": "none", "size": 24})])
    _text_box(s1, 0.3, 1.5, 12.5, 1.0, [("Tujuan:", {"bullet": "none"}),
                                        ("Menjaga layanan tetap berjalan.", {"bullet": "none"})])
    _text_box(s1, 0.3, 2.8, 12.5, 3.5, [
        ("Biaya operasional naik", {"bullet": "number", "mar_l": 342900, "indent": -342900}),
        ("Harga bahan naik", {"bullet": "char", "mar_l": 621030, "indent": -262255}),
        ("Upah naik", {"bullet": "char", "mar_l": 621030, "indent": -262255}),
        ("2.   Kewajiban lama besar", {}),
        ("Penjelasan kewajiban lama.", {"bullet": "none", "mar_l": 358775}),
        ("3.   Aset habis tahun 2030", {"bullet": "none"}),
        ("Penjelasan aset.", {"bullet": "none", "mar_l": 358775}),
    ])
    _text_box(s1, 12.1, 7.0, 1.1, 0.35, [("Hal. 1", {"size": 12, "bold": True})])

    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    s2.shapes.title.text = "DUA KOLOM"
    for n, top in (("SATU", 1.5), ("DUA", 3.3), ("TIGA", 5.1)):
        _text_box(s2, 0.5, top, 5.5, 1.6, [(f"KIRI {n}", {"bullet": "none", "bold": True}),
                                           (f"Isi kiri {n.lower()}.", {"bullet": "none"})])
    _text_box(s2, 6.8, 2.4, 6, 1.5, [("KANAN SATU", {"bullet": "none", "bold": True}),
                                     ("Isi kanan satu.", {"bullet": "none"})])
    table = s2.shapes.add_table(3, 2, Inches(6.8), Inches(4.5), Inches(6), Inches(1.5)).table
    for r, (k, v) in enumerate((("Tahun", "Nilai"), ("2031", "18,40"), ("2032", "17,95"))):
        table.cell(r, 0).text, table.cell(r, 1).text = k, v

    s3 = prs.slides.add_slide(blank)
    _text_box(s3, 1.0, 0.2, 8, 0.7, [("ALUR PROGRAM TABUNGAN", {"bullet": "none", "size": 24, "bold": True})])
    box = {}
    for key, text, x, y, w in (("A", "Program Tabungan", 0.2, 2.2, 2.0), ("B", "Bersifat opsional", 2.6, 2.2, 1.6),
                               ("C", "Anggota Lama", 4.6, 0.9, 2.0), ("D", "Anggota Baru", 4.6, 3.4, 2.0),
                               ("E", "Pencairan Tunai", 7.4, 0.9, 1.8), ("F", "Pindah ke Reksa Dana", 7.4, 3.4, 1.8),
                               ("G", "Target: 50% peserta ikut", 10.3, 1.4, 2.6), ("H", "EVALUASI", 10.3, 3.9, 2.6)):
        box[key] = _text_box(s3, x, y, w, 0.6, [(text, {"bullet": "none"})], border=True)
    _connect(s3, box["A"], 3, box["B"], 1)
    _connect(s3, box["B"], 3, box["C"], 1, elbow=True)
    _connect(s3, box["B"], 3, box["D"], 1, elbow=True)
    _connect(s3, box["C"], 3, box["E"], 1)
    _connect(s3, box["D"], 3, box["F"], 1)
    _connect(s3, box["E"], 3, box["G"], 1, elbow=True)
    _connect(s3, box["F"], 3, box["G"], 1, elbow=True)
    _text_box(s3, 6.75, 0.55, 0.5, 0.35, [("ya", {"bullet": "none", "size": 12})])
    arrow = s3.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, Inches(11.3), Inches(2.1), Inches(0.6), Inches(1.7))
    arrow.fill.solid()
    arrow.fill.fore_color.rgb = RGBColor(0x1F, 0x77, 0xB4)
    divider = s3.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(0), Inches(4.8), Inches(13.333), Inches(4.8))
    divider.line.color.rgb = RGBColor(0x80, 0x80, 0x80)

    png, (px0, py0, px1, py1) = _patched_text_png()
    pic_left, pic_top, pic_w = Inches(0.5), Inches(5.4), Inches(9)
    pic = s3.shapes.add_picture(io.BytesIO(png), pic_left, pic_top, width=pic_w)
    emu = pic_w / 900
    patch = s3.shapes.add_textbox(pic_left + int(px0 * emu), pic_top + int(py0 * emu),
                                  int((px1 - px0) * emu), int((py1 - py0) * emu))
    patch.text_frame.text = "Baru"
    patch.text_frame.margin_left = patch.text_frame.margin_top = 0
    _para_xml(patch.text_frame.paragraphs[0], bullet="none", size=18)
    patch.fill.solid()
    patch.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    assert pic._element.getparent() is patch._element.getparent()

    out = FIX / "layout.pptx"
    prs.save(out)
    return out


def _set_pic_alt(pic, descr: str) -> None:
    """Set author Alt Text on a picture shape via low-level XML."""
    nodes = pic._element.xpath(".//*[local-name()='cNvPr']")
    if nodes:
        nodes[0].set("descr", descr)


def make_shape_pptx() -> Path:
    """PPTX with a slide whose diagram is native shapes + connectors (no bitmap)."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    slide.shapes.title.text = "Deployment Pipeline"

    shapes = slide.shapes

    def add_box(shape_enum, left_in, top_in, w_in, h_in, label):
        s = shapes.add_shape(shape_enum, Inches(left_in), Inches(top_in),
                             Inches(w_in), Inches(h_in))
        s.text_frame.text = label
        for p in s.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(14)
        return s

    # Layout (inches)
    #    [Commit] -> [Build] -> [Test] -> {Pass?} -Yes-> [Deploy]
    #                                          |
    #                                          No
    #                                          v
    #                                       [Notify]
    b1 = add_box(MSO_SHAPE.ROUNDED_RECTANGLE, 0.5, 2.5, 1.5, 0.8, "Commit")
    b2 = add_box(MSO_SHAPE.RECTANGLE,         2.4, 2.5, 1.5, 0.8, "Build")
    b3 = add_box(MSO_SHAPE.RECTANGLE,         4.3, 2.5, 1.5, 0.8, "Test")
    b4 = add_box(MSO_SHAPE.DIAMOND,           6.2, 2.3, 1.6, 1.2, "Pass?")
    b5 = add_box(MSO_SHAPE.ROUNDED_RECTANGLE, 8.3, 2.5, 1.4, 0.8, "Deploy")
    b6 = add_box(MSO_SHAPE.RECTANGLE,         6.5, 4.5, 1.4, 0.8, "Notify")

    def connect_lr(src, dst):
        """Right edge of src to left edge of dst."""
        bx = src.left + src.width
        by = src.top + src.height // 2
        ex = dst.left
        ey = dst.top + dst.height // 2
        return shapes.add_connector(MSO_CONNECTOR.STRAIGHT, bx, by, ex, ey)

    def connect_tb(src, dst):
        """Bottom center of src to top center of dst."""
        bx = src.left + src.width // 2
        by = src.top + src.height
        ex = dst.left + dst.width // 2
        ey = dst.top
        return shapes.add_connector(MSO_CONNECTOR.STRAIGHT, bx, by, ex, ey)

    connect_lr(b1, b2)
    connect_lr(b2, b3)
    connect_lr(b3, b4)
    connect_lr(b4, b5)   # Pass? -> Deploy (Yes)
    connect_tb(b4, b6)   # Pass? -> Notify (No)

    # Add small edge labels as textboxes
    def label(text, left_in, top_in, w_in=0.6, h_in=0.3):
        tb = shapes.add_textbox(Inches(left_in), Inches(top_in), Inches(w_in), Inches(h_in))
        tb.text_frame.text = text
        for p in tb.text_frame.paragraphs:
            for r in p.runs:
                r.font.size = Pt(12)

    label("Yes", 7.9, 2.3)
    label("No", 7.4, 3.7)

    out = FIX / "shape_diagram.pptx"
    prs.save(out)
    return out


def make_dedup_pdf() -> Path:
    """PDF where the SAME logo appears on every page + one unique diagram at end.

    Exists to prove the dedup + tiny-skip optimizations work. Without dedup,
    the AI would be called once per page for the same logo. With dedup, once.
    """
    # Build a small logo image (24x24 ~= tiny; will be filtered by min_dim=64)
    logo = Image.new("RGB", (32, 32), "white")
    d = ImageDraw.Draw(logo)
    d.rectangle((0, 0, 31, 31), outline="black", width=2)
    d.text((6, 8), "LOGO", fill="black", font=_font(10))
    logo_path = FIX / "_logo.png"
    logo.save(logo_path)

    doc = fitz.open()
    for i in range(5):
        page = doc.new_page(width=612, height=792)
        # Logo top-left on every page (same bytes -> dedup should catch it)
        page.insert_image(fitz.Rect(40, 30, 72, 62), filename=str(logo_path))
        page.insert_text((90, 55), f"Weekly Report — Week {i + 1}", fontsize=14)
        page.insert_text((72, 120),
                         f"This is the body text of week {i + 1}. It contains "
                         f"no images beyond the shared corner logo.",
                         fontsize=11)
        page.insert_text((72, 750), f"Page {i + 1} of 5", fontsize=9, color=(0.4, 0.4, 0.4))

    # Final page: unique diagram
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(40, 30, 72, 62), filename=str(logo_path))
    page.insert_text((90, 55), "Summary", fontsize=14)
    page.insert_text((72, 120), "System architecture:", fontsize=11)
    diag = FIX / "diagram.png"
    if diag.exists():
        page.insert_image(fitz.Rect(72, 150, 472, 416), filename=str(diag))
    page.insert_text((72, 750), "Page 6 of 5", fontsize=9, color=(0.4, 0.4, 0.4))

    out = FIX / "dedup_test.pdf"
    doc.save(out)
    doc.close()
    logo_path.unlink(missing_ok=True)  # cleanup helper
    return out


def make_slide_deck_pdf() -> Path:
    """16:9 PDF shaped like a slide deck exported from Impress/PowerPoint.

    Exercises what per-element extraction gets wrong on slides:
      - a template logo painted on every page (must not be described),
      - step labels like "1  SOURCE" in small bold text (not headings),
      - a vector diagram: boxes + connectors with arrowheads, and a legend
        whose swatch colours say which phase each box belongs to,
      - a table with no rulings (text aligned in columns only).
    """
    teal, purple, grey = (0.12, 0.62, 0.59), (0.46, 0.24, 0.67), (0.55, 0.48, 0.61)
    logo = Image.new("RGB", (120, 60), (40, 20, 60))
    ImageDraw.Draw(logo).text((10, 20), "ACME", fill="white", font=_font(22))
    logo_path = FIX / "_deck_logo.png"
    logo.save(logo_path)

    doc = fitz.open()
    doc.set_metadata({"creator": "Impress", "producer": "LibreOffice 24.2",
                      "title": "Platform rollout"})

    def _frame(page, n: int) -> None:
        page.insert_image(fitz.Rect(840, 20, 920, 60), filename=str(logo_path))
        page.insert_text((440, 525), "- Confidential -", fontsize=8, color=(0.5, 0.5, 0.5))
        page.insert_text((915, 525), f"{n:02d}", fontsize=8, color=(0.5, 0.5, 0.5))

    # 1. Title slide
    page = doc.new_page(width=960, height=540)
    _frame(page, 1)
    page.insert_text((60, 240), "Platform Rollout 2027", fontsize=40)
    page.insert_text((60, 290), "Phase 1  ·  Architecture  ·  Draft for discussion", fontsize=22)

    # 2. Pipeline diagram with a phase legend
    page = doc.new_page(width=960, height=540)
    _frame(page, 2)
    page.insert_text((40, 40), "THE PIPELINE  ·  PHASE BY PHASE", fontsize=14, color=purple)
    page.insert_text((40, 85), "From source system to answer", fontsize=28)
    boxes = [
        ("1  SOURCE", "Source system", "read-only", teal),
        ("2  EXTRACT", "Extractor job", "nightly", teal),
        ("3  INDEX", "Search index", "hybrid ranking", teal),
        ("4  ANSWER", "Voice agent", "hands-free", purple),
    ]
    x = 40
    for i, (step, title, sub, colour) in enumerate(boxes):
        rect = fitz.Rect(x, 180, x + 190, 290)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(color=colour, fill=(1, 1, 1), width=1.5)
        shape.commit()
        page.insert_text((x + 12, 205), step, fontsize=10, fontname="helv")
        page.insert_text((x + 12, 240), title, fontsize=14, fontname="hebo")
        page.insert_text((x + 12, 262), sub, fontsize=11)
        if i < len(boxes) - 1:
            y = 235
            shape = page.new_shape()
            shape.draw_line(fitz.Point(x + 191, y), fitz.Point(x + 222, y))
            shape.finish(color=grey, width=2, closePath=False)
            shape.draw_polyline([fitz.Point(x + 221, y - 4), fitz.Point(x + 229, y),
                                 fitz.Point(x + 221, y + 4)])
            shape.finish(color=None, fill=grey, closePath=True)
            shape.commit()
        x += 230
    for i, (label, colour) in enumerate([("Phase 1: built now", teal), ("Phase 2", purple)]):
        sx = 40 + i * 200
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(sx, 400, sx + 22, 414))
        shape.finish(color=colour, fill=(1, 1, 1), width=1)
        shape.commit()
        page.insert_text((sx + 30, 412), label, fontsize=11)

    # 3. Borderless table
    page = doc.new_page(width=960, height=540)
    _frame(page, 3)
    page.insert_text((40, 85), "The stack, layer by layer", fontsize=28)
    rows = [("LAYER", "TECHNOLOGY", "PHASE"),
            ("Edge", "Front Door with WAF", "PHASE 1"),
            ("Database", "PostgreSQL with pgvector", "PHASE 1"),
            ("Real-time voice", "LiveKit server", "PHASE 2")]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text((40 + c * 300, 150 + r * 40), cell, fontsize=12,
                             fontname="hebo" if r == 0 or c == 0 else "helv")

    out = FIX / "slide_deck.pdf"
    doc.save(out)
    doc.close()
    logo_path.unlink(missing_ok=True)
    return out


def make_scanned_pdf() -> Path:
    """PDF where every page is an image rendered from text (no extractable text).

    Simulates a scanned document. Text extraction returns empty; the OCR path
    must kick in when ocr_mode is auto or always.
    """
    doc = fitz.open()
    page_w, page_h = 612, 792
    # Sample paragraphs (bilingual to exercise ind+eng)
    pages_text = [
        [
            "EXECUTIVE SUMMARY",
            "",
            "This report is a fictional Q3 2026 summary used to test OCR.",
            "APAC delivered the strongest performance, driven by enterprise",
            "renewals. EMEA continued its recovery. Americas remained flat.",
        ],
        [
            "RINGKASAN OPERASIONAL",
            "",
            "Wilayah APAC mencatat pertumbuhan tertinggi dengan kontrak",
            "enterprise baru. Wilayah EMEA melanjutkan pemulihan sejak Q2.",
            "Wilayah Americas relatif stagnan karena siklus pengadaan yang panjang.",
        ],
    ]
    for lines in pages_text:
        # Render text to a PIL image at 200 DPI-ish
        img = Image.new("RGB", (int(page_w * 2.5), int(page_h * 2.5)), "white")
        d = ImageDraw.Draw(img)
        f_title = _font(46)
        f_body = _font(32)
        y = 120
        for line in lines:
            font = f_title if line.isupper() and line else f_body
            d.text((120, y), line, fill="black", font=font)
            y += 60 if font == f_title else 44
        # Save as PNG then embed as full-page image
        tmp_png = FIX / "_scan_page.png"
        img.save(tmp_png)
        page = doc.new_page(width=page_w, height=page_h)
        page.insert_image(fitz.Rect(0, 0, page_w, page_h), filename=str(tmp_png))
        tmp_png.unlink(missing_ok=True)

    out = FIX / "scanned.pdf"
    doc.save(out)
    doc.close()
    return out


def make_sample_docx() -> Path:
    """DOCX with title, heading, paragraphs, list, table, header, footer, an image."""
    doc = Document()

    # Header + footer (should be stripped by converter — they're outside <w:body>)
    section = doc.sections[0]
    section.header.paragraphs[0].text = "Company Confidential — do not distribute"
    footer_para = section.footer.paragraphs[0]
    footer_para.text = "Q3 2026 Report · Page "
    # (page-number field would normally follow, but text is enough for a stripping test)

    doc.add_heading("Q3 2026 Report", level=0)
    doc.add_heading("Executive Summary", level=1)
    doc.add_paragraph(
        "This quarter APAC delivered the strongest performance, driven by enterprise "
        "renewals. EMEA continued its recovery. Americas remained flat."
    )

    doc.add_heading("Key Metrics", level=2)
    tbl = doc.add_table(rows=4, cols=3)
    tbl.style = "Light Grid"
    hdr = tbl.rows[0].cells
    hdr[0].text = "Region"
    hdr[1].text = "Bookings (USD M)"
    hdr[2].text = "YoY Growth"
    data = [
        ("APAC", "14.2", "+21%"),
        ("EMEA", "9.8",  "+7%"),
        ("Americas", "11.1", "+1%"),
    ]
    for i, row in enumerate(data, start=1):
        for j, val in enumerate(row):
            tbl.rows[i].cells[j].text = val

    doc.add_heading("Priorities", level=2)
    for item in ("Ship reporting pipeline v2",
                 "Onboard 3 new enterprise customers",
                 "Reduce p95 API latency by 30%"):
        p = doc.add_paragraph(item, style="List Bullet")
        _ = p  # keep the list style

    doc.add_heading("Architecture", level=1)
    doc.add_paragraph("The ingestion pipeline is shown below.")
    img_path = FIX / "diagram.png"
    if img_path.exists():
        doc.add_picture(str(img_path), width=DocxInches(4.5))

    out = FIX / "sample.docx"
    doc.save(out)
    return out


def make_sample_xlsx() -> Path:
    """XLSX with 3 sheets: Sales, Regions, Notes (empty-ish)."""
    wb = Workbook()

    # Sheet 1
    ws1 = wb.active
    ws1.title = "Sales"
    ws1.append(["Month", "Region", "Bookings (USD)", "Deals"])
    rows = [
        ("2026-01", "APAC", 4200000, 12),
        ("2026-01", "EMEA", 2800000, 9),
        ("2026-02", "APAC", 5100000, 14),
        ("2026-02", "EMEA", 3400000, 11),
        ("2026-03", "APAC", 4900000, 13),
        ("2026-03", "EMEA", 3600000, 10),
    ]
    for r in rows:
        ws1.append(r)
    # Trailing empty rows/cols to confirm trimming
    ws1.append([None, None, None, None, None])
    ws1.append([None, None, None, None, None])

    # Sheet 2
    ws2 = wb.create_sheet("Regions")
    ws2.append(["Code", "Region", "Countries", "Population (M)"])
    ws2.append(["APAC",     "Asia Pacific", 15, 4650.0])
    ws2.append(["EMEA",     "Europe/ME/Africa", 40, 2400.5])
    ws2.append(["Americas", "N + S America",  22, 1030.7])

    # Sheet 3: minimal
    ws3 = wb.create_sheet("Notes")
    ws3["A1"] = "Data source: internal CRM export, generated 2026-08-05."
    ws3["A2"] = "Values exclude in-flight opportunities."

    out = FIX / "sample.xlsx"
    wb.save(out)
    return out


def make_sample_html() -> Path:
    """Article-style HTML with headings, list, table, link-heavy nav that
    should get stripped."""
    html = """<!doctype html>
<html>
<head>
  <title>Q3 2026 Reporting Notes</title>
  <style>body{font-family:sans-serif}</style>
  <script>console.log("noise")</script>
</head>
<body>
  <nav>
    <a href="#a">Home</a> · <a href="#b">Docs</a> · <a href="#c">Blog</a>
  </nav>
  <header><h1>Q3 2026 Reporting Notes</h1></header>
  <main>
    <p>Ringkasan singkat performa kuartal ketiga tahun 2026.</p>
    <h2>Highlights</h2>
    <ul>
      <li>APAC tumbuh 21% YoY</li>
      <li>EMEA pulih setelah dua kuartal turun</li>
      <li>Americas relatif datar</li>
    </ul>
    <h2>Numbers</h2>
    <table>
      <thead><tr><th>Region</th><th>Bookings (USD M)</th></tr></thead>
      <tbody>
        <tr><td>APAC</td><td>14.2</td></tr>
        <tr><td>EMEA</td><td>9.8</td></tr>
        <tr><td>Americas</td><td>11.1</td></tr>
      </tbody>
    </table>
    <p>Lihat <a href="https://example.com/detail">laporan lengkap</a>
       untuk breakdown per akun.</p>
  </main>
  <footer><small>&copy; 2026 Internal</small></footer>
</body>
</html>
"""
    out = FIX / "sample.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    p1 = make_diagram_png()
    print(f"PNG:            {p1}")
    p2 = make_sample_pdf()
    print(f"PDF:            {p2}")
    p3 = make_sample_pptx()
    print(f"PPTX (picture): {p3}")
    p4 = make_shape_pptx()
    print(f"PPTX (shapes):  {p4}")
    p5 = make_sample_docx()
    print(f"DOCX:           {p5}")
    p6 = make_sample_xlsx()
    print(f"XLSX:           {p6}")
    p7 = make_dedup_pdf()
    print(f"PDF (dedup):    {p7}")
    p8 = make_scanned_pdf()
    print(f"PDF (scanned):  {p8}")
    p9 = make_sample_html()
    print(f"HTML:           {p9}")
    p10 = make_slide_deck_pdf()
    print(f"PDF (slides):   {p10}")
    p11 = make_pictures_pptx()
    print(f"PPTX (images):  {p11}")
    p12 = make_layout_pptx()
    print(f"PPTX (layout):  {p12}")
