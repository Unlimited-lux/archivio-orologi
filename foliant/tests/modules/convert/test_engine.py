"""Test del motore di conversione da PDF (CNV-01, 02, 03, 07, 09 e formati P1/P2)."""

from __future__ import annotations

import csv
import io
import os
import zipfile

import pymupdf
import pytest

from foliant.api import OperationCancelled
from foliant.modules.convert.common import ConversionError, RangeError, parse_page_range
from foliant.modules.convert.engine import convert_pdf, run_conversion


# ------------------------------------------------------------------ fixture
def _save(doc, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def two_columns(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 60), "Titolo del documento", fontsize=18)
    left = pymupdf.Rect(50, 100, 280, 800)
    right = pymupdf.Rect(315, 100, 545, 800)
    page.insert_textbox(left, "SINISTRA uno. " + "alfa beta gamma delta " * 30 + "FINESINISTRA", fontsize=10)
    page.insert_textbox(right, "DESTRA uno. " + "epsilon zeta eta theta " * 30 + "FINEDESTRA", fontsize=10)
    return _save(doc, tmp_path / "colonne.pdf")


@pytest.fixture
def links_pdf(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=595, height=842)
    p0 = doc[0]
    for k, url in enumerate(["https://a.example.com/", "https://b.example.com/x", "https://c.example.com/y"]):
        y = 100 + k * 40
        p0.insert_text((72, y), f"Collegamento numero {k + 1}", fontsize=12)
        p0.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, y - 12, 250, y + 4), "uri": url})
    for k, target in enumerate([1, 2]):
        y = 300 + k * 40
        p0.insert_text((72, y), f"Vai alla pagina {target + 1}", fontsize=12)
        p0.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, y - 12, 250, y + 4), "page": target,
                        "to": pymupdf.Point(72, 100)})
    doc[1].insert_text((72, 100), "Destinazione due", fontsize=12)
    doc[2].insert_text((72, 100), "Destinazione tre", fontsize=12)
    return _save(doc, tmp_path / "link.pdf")


def _jpeg_bytes(w=320, h=200):
    from PIL import Image
    img = Image.new("RGB", (w, h))
    for x in range(w):
        for y in range(0, h, 4):
            img.putpixel((x, y), (x % 256, y % 256, 120))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def image_pdf(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Didascalia della fotografia", fontsize=12)
    page.insert_image(pymupdf.Rect(72, 100, 392, 300), stream=_jpeg_bytes())
    return _save(doc, tmp_path / "immagine.pdf")


@pytest.fixture
def scanned_mixed(tmp_path):
    """3 pagine: 1–2 native, 3 scansionata (immagine di testo senza livello di testo)."""
    src = pymupdf.open()
    sp = src.new_page(width=595, height=842)
    sp.insert_text((60, 200), "SCANSIONE PROVA", fontsize=40)
    sp.insert_text((60, 300), "Orologio automatico", fontsize=40)
    pix = sp.get_pixmap(dpi=200)
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Testo nativo pagina {i + 1}", fontsize=14)
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    return _save(doc, tmp_path / "misto.pdf")


# ------------------------------------------------------------------ CNV-01
def test_parse_page_range():
    assert parse_page_range("2-4, 10", 20) == [1, 2, 3, 9]
    assert parse_page_range("", 3) == [0, 1, 2]
    assert parse_page_range("18-fine", 20) == [17, 18, 19]
    assert parse_page_range("pari", 5) == [1, 3]
    assert parse_page_range("dispari", 5) == [0, 2, 4]
    assert parse_page_range("corrente", 5, current=3) == [3]
    assert parse_page_range("3-1", 5) == [2, 1, 0]
    for bad in ("25-30", "0", "abc", "1-x"):
        with pytest.raises(RangeError):
            parse_page_range(bad, 20)


def test_range_txt_order(twenty_pages, tmp_path):
    out = tmp_path / "out" / "venti.txt"
    paths = convert_pdf(twenty_pages, out, "txt", pages="2-4, 10")
    text = open(paths[0], encoding="utf-8").read()
    nums = [int(line.split()[-1]) for line in text.replace("\f", "\n").splitlines() if line.startswith("Pagina")]
    assert nums == [2, 3, 4, 10]


def test_invalid_range_no_file(twenty_pages, tmp_path):
    out = tmp_path / "dest"
    with pytest.raises(RangeError):
        convert_pdf(twenty_pages, out / "x.txt", "txt", pages="25-30")
    assert not out.exists() or not list(out.iterdir())


def test_cancel_leaves_nothing(twenty_pages, tmp_path):
    from foliant.api import Progress
    out = tmp_path / "dest"
    out.mkdir()
    progress = Progress()
    calls = {"n": 0}
    original = progress.update

    def update(fraction=None, text=None):
        calls["n"] += 1
        if calls["n"] == 5:
            progress.cancel()
        original(fraction, text)

    progress.update = update
    for fmt in ("txt", "docx", "png"):
        calls["n"] = 0
        progress._cancel.clear()
        with pytest.raises(OperationCancelled):
            convert_pdf(twenty_pages, out / f"venti.{fmt}", fmt, progress=progress)
        assert list(out.iterdir()) == []


def test_rename_policy_keeps_existing(sample_pdf, tmp_path):
    target = tmp_path / "esistente.txt"
    target.write_bytes(b"originale")
    paths = convert_pdf(sample_pdf, target, "txt")
    assert paths == [str(tmp_path / "esistente (1).txt")]
    assert target.read_bytes() == b"originale"
    paths = convert_pdf(sample_pdf, target, "txt", policy="overwrite")
    assert paths == [str(target)] and target.read_bytes() != b"originale"


def test_extraction_forbidden(tmp_path):
    path = tmp_path / "vietato.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Riservato", fontsize=12)
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
             permissions=pymupdf.PDF_PERM_PRINT)
    doc.close()
    with pytest.raises(ConversionError) as e:
        convert_pdf(str(path), tmp_path / "x.docx", "docx")
    assert "copia del contenuto" in str(e.value)
    assert not (tmp_path / "x.docx").exists()
    # con la password del proprietario la conversione è permessa
    assert convert_pdf(str(path), tmp_path / "x.txt", "txt", password="capo")


def test_encrypted_with_password(encrypted_pdf, tmp_path):
    from foliant.api import PasswordRequired
    with pytest.raises(PasswordRequired):
        convert_pdf(encrypted_pdf, tmp_path / "p.txt", "txt")
    paths = convert_pdf(encrypted_pdf, tmp_path / "p.txt", "txt", password="segreta")
    assert "Riservato" in open(paths[0], encoding="utf-8").read()


def test_bytes_source_and_signed_warning(sample_pdf, tmp_path):
    data = open(sample_pdf, "rb").read()
    result = run_conversion(data, tmp_path / "da_byte.txt", "txt")
    assert "Pagina 1" in open(result.paths[0], encoding="utf-8").read()


# ------------------------------------------------------------------ CNV-09
def test_txt_two_columns_reading_order(two_columns, tmp_path):
    text = open(convert_pdf(two_columns, tmp_path / "c.txt", "txt")[0], encoding="utf-8").read()
    assert text.index("Titolo") < text.index("SINISTRA")
    assert text.index("FINESINISTRA") < text.index("DESTRA uno")


def test_txt_ligature_and_form_feed(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 100), "la ﬁne del contratto" if i == 0 else f"pagina {i + 1}", fontsize=12,
                      fontname="dejavu", fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    path = _save(doc, tmp_path / "lig.pdf")
    out = convert_pdf(path, tmp_path / "lig.txt", "txt", page_separator="ff")[0]
    raw = open(out, "rb").read()
    text = raw.decode("utf-8")
    assert "fine" in text and "ﬁ" not in text
    assert text.count("\f") == 2


def test_txt_layout_and_encoding(sample_pdf, tmp_path):
    out = convert_pdf(sample_pdf, tmp_path / "l.txt", "txt", mode="layout", encoding="utf-16", line_ending="crlf")[0]
    text = open(out, encoding="utf-16").read()
    assert "Pagina 1" in text and "\r\n" in open(out, "rb").read().decode("utf-16")


# ------------------------------------------------------------------ CNV-03
def _docx_text(path):
    import docx
    d = docx.Document(path)
    return "\n".join(p.text for p in d.paragraphs), d


def test_docx_flow_text_and_order(two_columns, tmp_path):
    text, _d = _docx_text(convert_pdf(two_columns, tmp_path / "c.docx", "docx", layout="flow")[0])
    assert text.index("FINESINISTRA") < text.index("DESTRA uno")
    with pymupdf.open(two_columns) as doc:
        src_words = doc[0].get_text().split()
    out_words = text.split()
    common = sum(1 for w in src_words if w in set(out_words))
    assert common / len(src_words) >= 0.99


def test_docx_table(table_pdf, tmp_path):
    path, rows = table_pdf
    _t, d = _docx_text(convert_pdf(path, tmp_path / "t.docx", "docx")[0])
    assert len(d.tables) == 1
    t = d.tables[0]
    assert (len(t.rows), len(t.columns)) == (5, 4)
    assert [[c.text for c in r.cells] for r in t.rows] == rows


def test_docx_links(links_pdf, tmp_path):
    out = convert_pdf(links_pdf, tmp_path / "l.docx", "docx")[0]
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode()
        rels = z.read("word/_rels/document.xml.rels").decode()
    for url in ("https://a.example.com/", "https://b.example.com/x", "https://c.example.com/y"):
        assert url in rels
    assert xml.count("<w:hyperlink") == 5
    assert xml.count('w:anchor="page_2"') == 1 and xml.count('w:anchor="page_3"') == 1
    assert 'w:name="page_2"' in xml and 'w:name="page_3"' in xml


def test_docx_image_original_pixels(image_pdf, tmp_path):
    out = convert_pdf(image_pdf, tmp_path / "i.docx", "docx")[0]
    from PIL import Image
    with zipfile.ZipFile(out) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        assert len(media) == 1
        img = Image.open(io.BytesIO(z.read(media[0])))
    assert img.size == (320, 200)


def test_docx_faithful_sections(tmp_path):
    doc = pymupdf.open()
    for i in range(6):
        p = doc.new_page(width=595.28, height=841.89)
        p.insert_text((57, 100), f"Pagina fedele {i + 1}", fontsize=14)
    path = _save(doc, tmp_path / "sei.pdf")
    _t, d = _docx_text(convert_pdf(path, tmp_path / "sei.docx", "docx", layout="faithful")[0])
    for s in d.sections:
        assert abs(s.page_width.mm - 210) <= 1 and abs(s.page_height.mm - 297) <= 1
    with zipfile.ZipFile(tmp_path / "sei.docx") as z:
        xml = z.read("word/document.xml").decode()
    breaks = xml.count("<w:sectPr") - 1 + xml.count('w:type="page"')
    assert breaks >= 5


# ------------------------------------------------------------------ CNV-02
def test_ocr_only_scanned_page(scanned_mixed, tmp_path):
    from foliant.modules.convert.extract import ocr_available
    if not ocr_available():
        pytest.skip("tesseract non installato")
    result = run_conversion(scanned_mixed, tmp_path / "m.txt", "txt")
    assert result.ocr_pages == [3]
    pages = open(result.paths[0], encoding="utf-8").read().split("\f")
    with pymupdf.open(scanned_mixed) as doc:
        for i in range(2):
            assert pages[i].strip() == doc[i].get_text().strip()
    assert "SCANSIONE" in pages[2].upper() and "OROLOGIO" in pages[2].upper()


def test_ocr_never_warns(scanned_mixed, tmp_path):
    result = run_conversion(scanned_mixed, tmp_path / "m.txt", "txt", ocr="never")
    assert result.scanned_pages == [3]
    assert any("Pagina 3" in w for w in result.warnings)


# ------------------------------------------------------------------ CNV-07
def test_png_300dpi(sample_pdf, tmp_path):
    from PIL import Image
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=595.28, height=841.89).insert_text((72, 100), f"A4 {i}", fontsize=12)
    path = _save(doc, tmp_path / "doc.pdf")
    paths = convert_pdf(path, tmp_path / "img" / "doc.png", "png", dpi=300)
    assert [os.path.basename(p) for p in paths] == ["doc_001.png", "doc_002.png", "doc_003.png"]
    for p in paths:
        img = Image.open(p)
        assert abs(img.size[0] - 2480) <= 1 and abs(img.size[1] - 3508) <= 1
        assert round(img.info["dpi"][0]) == 300


def test_tiff_multipage_g4(sample_pdf, tmp_path):
    from PIL import Image
    paths = convert_pdf(sample_pdf, tmp_path / "doc.tif", "tiff", multipage=True, color="bw",
                        tiff_compression="group4", dpi=100)
    assert len(paths) == 1
    img = Image.open(paths[0])
    assert img.n_frames == 3
    for k in range(3):
        img.seek(k)
        assert img.mode == "1"
        assert img.info.get("compression") == "group4"


def test_gif_palette_and_size(sample_pdf, tmp_path):
    from PIL import Image
    gif = convert_pdf(sample_pdf, tmp_path / "g.gif", "gif", pages="1", dpi=100)[0]
    png = convert_pdf(sample_pdf, tmp_path / "g.png", "png", pages="1", dpi=100)[0]
    g = Image.open(gif)
    assert g.mode == "P" and len(g.getpalette()) // 3 <= 256
    assert g.size == Image.open(png).size


def test_rotated_page_and_jpeg_bmp(tmp_path):
    from PIL import Image
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Ruotata", fontsize=12)
    p.set_rotation(90)
    path = _save(doc, tmp_path / "rot.pdf")
    for fmt in ("jpeg", "bmp"):
        out = convert_pdf(path, tmp_path / f"r.{fmt}", fmt, dpi=72)[0]
        w, h = Image.open(out).size
        assert w > h and abs(w - 842) <= 1 and abs(h - 595) <= 1


def test_annotations_excluded(tmp_path):
    from PIL import Image, ImageChops
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=300)
    p.insert_text((20, 50), "Testo", fontsize=12)
    path_plain = _save(doc, tmp_path / "plain.pdf")
    doc = pymupdf.open(path_plain)
    doc[0].add_rect_annot(pymupdf.Rect(50, 50, 250, 250)).update()
    path_ann = _save(doc, tmp_path / "ann.pdf")
    without = convert_pdf(path_ann, tmp_path / "a.png", "png", dpi=100, annotations=False)[0]
    plain = convert_pdf(path_plain, tmp_path / "p.png", "png", dpi=100)[0]
    with_ann = convert_pdf(path_ann, tmp_path / "w.png", "png", dpi=100, annotations=True)[0]
    a, b = Image.open(without).convert("RGB"), Image.open(plain).convert("RGB")
    assert ImageChops.difference(a, b).getbbox() is None
    assert ImageChops.difference(Image.open(with_ann).convert("RGB"), b).getbbox() is not None


def test_image_size_limit(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=14000, height=200)
    path = _save(doc, tmp_path / "largo.pdf")
    with pytest.raises(ConversionError) as e:
        convert_pdf(path, tmp_path / "x.jpg", "jpeg", dpi=600)
    assert "dpi" in str(e.value)


def test_long_image(tmp_path):
    from PIL import Image
    doc = pymupdf.open()
    for i in range(5):
        doc.new_page(width=595.28, height=841.89).insert_text((72, 100), f"P{i}", fontsize=12)
    path = _save(doc, tmp_path / "cinque.pdf")
    out = convert_pdf(path, tmp_path / "lunga.png", "long-image", dpi=96, spacing=0)
    assert len(out) == 1
    w, h = Image.open(out[0]).size
    assert abs(w - 794) <= 5 and abs(h - 5615) <= 5
    parts = convert_pdf(path, tmp_path / "lunga.jpg", "long-image", dpi=96, max_height=2500)
    assert len(parts) == 3 and all("_parte" in p for p in parts)
    sizes = [Image.open(p).size[1] for p in parts]
    assert all(s <= 2500 for s in sizes) and abs(sum(sizes) - h) <= 5


# ------------------------------------------------------------- CNV-04/05
@pytest.fixture
def amounts_pdf(tmp_path):
    """Tabella 20 righe × 6 colonne con importi «1.234,56» e riga dei totali."""
    import random
    random.seed(4)
    header = ["Codice", "Articolo", "Q1", "Q2", "Q3", "Q4"]
    rows, totals = [], [0.0] * 4
    for r in range(18):
        vals = [round(random.uniform(10, 9999), 2) for _ in range(4)]
        totals = [t + v for t, v in zip(totals, vals)]
        rows.append([f"00{r + 100}", f"Orologio {r}"] + [f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                                                        for v in vals])
    rows.append(["", "Totale"] + [f"{t:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") for t in totals])
    data = [header] + rows
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    x0, y0, cw, rh = 40, 40, 120, 24
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.6)
            if val:
                page.insert_text((rect.x0 + 3, rect.y1 - 7), val, fontsize=10)
    return _save(doc, tmp_path / "importi.pdf"), totals


def test_xlsx_numbers_and_totals(amounts_pdf, tmp_path):
    from openpyxl import load_workbook
    path, totals = amounts_pdf
    out = convert_pdf(path, tmp_path / "i.xlsx", "xlsx")[0]
    ws = load_workbook(out).active
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0] == ["Codice", "Articolo", "Q1", "Q2", "Q3", "Q4"]
    body, total_row = values[1:-1], values[-1]
    assert ws.cell(row=2, column=1).value == "00100" and ws.cell(row=2, column=1).data_type == "s"
    for c in range(4):
        col = [r[2 + c] for r in body]
        assert all(isinstance(v, float) for v in col)
        assert abs(sum(col) - total_row[2 + c]) < 0.01
        assert abs(total_row[2 + c] - round(totals[c], 2)) < 0.01


def test_xlsx_merge_pages(tmp_path):
    from openpyxl import load_workbook
    doc = pymupdf.open()
    n = 0
    for pg in range(3):
        page = doc.new_page(width=595, height=842)
        data = [["Data", "Descrizione", "Importo"]] + [[f"0{k + 1}/01/2025", f"Voce {n + k}", f"{n + k},00"]
                                                       for k in range(5)]
        n += 5
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                rect = pymupdf.Rect(50 + c * 150, 50 + r * 22, 200 + c * 150, 72 + r * 22)
                page.draw_rect(rect, color=(0, 0, 0), width=0.6)
                page.insert_text((rect.x0 + 3, rect.y1 - 6), val, fontsize=10)
    path = _save(doc, tmp_path / "lunga.pdf")
    ws = load_workbook(convert_pdf(path, tmp_path / "m.xlsx", "xlsx", merge_tables=True)[0]).active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[0] == ["Data", "Descrizione", "Importo"]
    assert sum(1 for r in rows if r[0] == "Data") == 1
    assert [r[1] for r in rows[1:]] == [f"Voce {k}" for k in range(15)]
    import datetime
    assert isinstance(rows[1][0], datetime.datetime)


def test_xlsx_two_level_header(tmp_path):
    from openpyxl import load_workbook
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    # intestazione superiore unita su 2 colonne + 2 colonne
    page.draw_rect(pymupdf.Rect(50, 50, 250, 72), color=(0, 0, 0))
    page.insert_text((55, 66), "Vendite", fontsize=10)
    page.draw_rect(pymupdf.Rect(250, 50, 450, 72), color=(0, 0, 0))
    page.insert_text((255, 66), "Acquisti", fontsize=10)
    for r in range(1, 4):
        for c in range(4):
            rect = pymupdf.Rect(50 + c * 100, 50 + r * 22, 150 + c * 100, 72 + r * 22)
            page.draw_rect(rect, color=(0, 0, 0))
            page.insert_text((rect.x0 + 3, rect.y1 - 6), ["Q", "V", "Q", "V"][c] if r == 1 else str(r * 10 + c),
                             fontsize=10)
    path = _save(doc, tmp_path / "h2.pdf")
    ws = load_workbook(convert_pdf(path, tmp_path / "h2.xlsx", "xlsx")[0]).active
    merged = {str(m) for m in ws.merged_cells.ranges}
    assert "A1:B1" in merged and "C1:D1" in merged


def test_csv_rfc4180_and_matrix(tmp_path):
    from openpyxl import load_workbook
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    data = [["Nome", "Nota"], ['Rossi; "Mario"', "riga uno"], ["Bianchi", "ok"]]
    for r, row in enumerate(data):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(50 + c * 200, 50 + r * 40, 250 + c * 200, 90 + r * 40)
            page.draw_rect(rect, color=(0, 0, 0))
            lines = val.split("\n")
            for k, line in enumerate(lines):
                page.insert_text((rect.x0 + 3, rect.y0 + 14 + k * 12), line, fontsize=10)
    page.insert_text((253, 50 + 40 + 26), "e due", fontsize=10)   # seconda riga nella cella «Nota»
    path = _save(doc, tmp_path / "csv.pdf")
    out = convert_pdf(path, tmp_path / "t.csv", "csv", delimiter=";")
    assert [os.path.basename(p) for p in out] == ["t_p1_t1.csv"]
    with open(out[0], encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f, delimiter=";"))
    assert rows[1][0] == 'Rossi; "Mario"'
    assert rows[1][1] == "riga uno\ne due"
    ws = load_workbook(convert_pdf(path, tmp_path / "t.xlsx", "xlsx")[0]).active
    matrix = [[("" if c.value is None else str(c.value)) for c in r] for r in ws.iter_rows()]
    assert matrix == rows


def test_csv_no_tables_no_file(sample_pdf, tmp_path):
    with pytest.raises(ConversionError) as e:
        convert_pdf(sample_pdf, tmp_path / "x.csv", "csv")
    assert "tabella" in str(e.value)
    assert list(tmp_path.glob("*.csv")) == []


# ------------------------------------------------------------------ CNV-06
def test_pptx_slides(tmp_path):
    from pptx import Presentation
    doc = pymupdf.open()
    for i in range(12):
        p = doc.new_page(width=960, height=540)
        p.insert_text((60, 80), f"Diapositiva {i + 1}", fontsize=28)
        p.insert_text((60, 140), "Collezione autunno orologi", fontsize=16)
    path = _save(doc, tmp_path / "pres.pdf")
    prs = Presentation(convert_pdf(path, tmp_path / "p.pptx", "pptx")[0])
    assert len(prs.slides) == 12
    assert abs(prs.slide_width / prs.slide_height - 16 / 9) < 0.01
    for i, slide in enumerate(prs.slides):
        text = " ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)
        assert f"Diapositiva {i + 1}" in text and "Collezione autunno orologi" in text


def test_pptx_image_and_a4(image_pdf, tmp_path):
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(convert_pdf(image_pdf, tmp_path / "i.pptx", "pptx")[0])
    assert abs(prs.slide_width / prs.slide_height - 210 / 297) / (210 / 297) < 0.005
    shapes = list(prs.slides[0].shapes)
    pics = [s for s in shapes if s.shape_type == MSO_SHAPE_TYPE.PICTURE]
    texts = [s for s in shapes if s.has_text_frame and s.text_frame.text.strip()]
    assert len(pics) == 1 and texts
    pic = pics[0]
    k = prs.slide_width / 595
    assert abs(pic.left - 72 * k) <= 0.02 * prs.slide_width
    assert abs(pic.top - 100 * k) <= 0.02 * prs.slide_width


# ------------------------------------------------------------------ CNV-11
def test_html_flow_headings_and_single_file(tmp_path):
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 80), f"Capitolo {i + 1}", fontsize=24)
        p.insert_text((72, 120), "Testo del paragrafo con < e & speciali", fontsize=11)
        if i == 0:
            p.insert_image(pymupdf.Rect(72, 150, 272, 275), stream=_jpeg_bytes())
    path = _save(doc, tmp_path / "h.pdf")
    out = convert_pdf(path, tmp_path / "h.html", "html", layout="flow")[0]
    html = open(out, encoding="utf-8").read()
    assert html.count("<h1>") == 3
    for i in range(3):
        assert f"<h1>Capitolo {i + 1}</h1>" in html
    assert "&lt; e &amp;" in html
    assert 'src="data:image/' in html and 'src="http' not in html
    from html.parser import HTMLParser
    HTMLParser().feed(html)
    # cartella di risorse
    outs = convert_pdf(path, tmp_path / "h2.html", "html", single_file=False)
    assert any(p.endswith(".jpg") or p.endswith(".jpeg") for p in outs)
    assert os.path.isdir(tmp_path / "h2_files")


def test_html_faithful(sample_pdf, tmp_path):
    html = open(convert_pdf(sample_pdf, tmp_path / "f.html", "html", layout="faithful")[0], encoding="utf-8").read()
    assert html.count('id="page-') == 3 and "Pagina 2" in html


# ------------------------------------------------------------- CNV-12/13
def test_xml_words(sample_pdf, tmp_path):
    import xml.etree.ElementTree as ET
    out = convert_pdf(sample_pdf, tmp_path / "x.xml", "xml", pages="1-2")[0]
    root = ET.parse(out).getroot()
    ns = {"f": "urn:foliant:export:1"}
    pages = root.findall("f:page", ns)
    assert len(pages) == 2
    txt = open(convert_pdf(sample_pdf, tmp_path / "x.txt", "txt", pages="1-2")[0], encoding="utf-8").read()
    for page in pages:
        w, h = float(page.get("width")), float(page.get("height"))
        words = page.findall(".//f:word", ns)
        assert words
        for word in words:
            x0, y0, x1, y1 = map(float, word.get("bbox").split())
            assert 0 <= x0 <= x1 <= w and 0 <= y0 <= y1 <= h
        joined = " ".join(word.text for word in words)
        assert joined.split()[0] in txt


def test_xml_valid_against_schema(sample_pdf, tmp_path):
    lxml = pytest.importorskip("lxml.etree")
    from foliant.modules.convert.export_text import XSD_PATH
    schema = lxml.XMLSchema(lxml.parse(str(XSD_PATH)))
    doc = pymupdf.open(sample_pdf)
    doc[0].add_text_annot((100, 200), "Nota \x07 con controllo")
    doc[0].insert_image(pymupdf.Rect(300, 300, 400, 380), stream=_jpeg_bytes())
    path = _save(doc, tmp_path / "s.pdf")
    for detail in ("words", "lines", "blocks"):
        out = convert_pdf(path, tmp_path / f"{detail}.xml", "xml", detail=detail)[0]
        assert schema.validate(lxml.parse(out)), schema.error_log


def test_xml_annotations(sample_pdf, tmp_path):
    import xml.etree.ElementTree as ET
    doc = pymupdf.open(sample_pdf)
    for k in range(4):
        doc[0].add_text_annot((100 + k * 30, 200), f"Nota {k}")
    path = _save(doc, tmp_path / "ann.pdf")
    root = ET.parse(convert_pdf(path, tmp_path / "a.xml", "xml")[0]).getroot()
    anns = root.findall(".//{urn:foliant:export:1}annotation")
    assert len(anns) == 4 and anns[0].get("type") == "Text" and anns[2].text == "Nota 2"


def test_epub_bookmarks_and_fixed(tmp_path):
    doc = pymupdf.open()
    for i in range(20):
        p = doc.new_page()
        p.insert_text((72, 300), f"Sezione {i // 5 + 1} pagina {i + 1}", fontsize=12)
        p.insert_text((72, 800), f"Pagina {i + 1} di 20", fontsize=9)
    doc.set_toc([[1, f"Parte {k + 1}", k * 5 + 1] for k in range(4)])
    path = _save(doc, tmp_path / "libro.pdf")
    out = convert_pdf(path, tmp_path / "l.epub", "epub")[0]
    with zipfile.ZipFile(out) as z:
        assert z.namelist()[0] == "mimetype" and z.read("mimetype") == b"application/epub+zip"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        nav = z.read("OEBPS/nav.xhtml").decode()
        assert all(f">Parte {k + 1}<" in nav for k in range(4))
        chapters = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("OEBPS/chapter"))
        assert "di 20" not in chapters and "Sezione 4 pagina 20" in chapters
        import xml.etree.ElementTree as ET
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".xml")):
                ET.fromstring(z.read(n))
    fixed = convert_pdf(path, tmp_path / "f.epub", "epub", layout="fixed", dpi=50)[0]
    with zipfile.ZipFile(fixed) as z:
        pages = [n for n in z.namelist() if n.startswith("OEBPS/page") and n.endswith(".xhtml")]
        assert len(pages) == 20 and pages == sorted(pages)
        assert "pre-paginated" in z.read("OEBPS/content.opf").decode()


# ------------------------------------------------------------------ CNV-10
def test_rtf_styles_and_images(tmp_path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Normale àèìòù", fontsize=12)
    p.insert_text((72, 130), "Grassetto", fontsize=12, fontname="hebo")
    p.insert_text((72, 160), "Corsivo", fontsize=12, fontname="heit")
    p.insert_text((72, 190), "Greco αβγ", fontsize=12, fontname="Helvetica")
    p.insert_image(pymupdf.Rect(72, 220, 232, 320), stream=_jpeg_bytes())
    p.insert_image(pymupdf.Rect(72, 340, 172, 400), stream=_jpeg_bytes(200, 120))
    path = _save(doc, tmp_path / "r.pdf")
    rtf = open(convert_pdf(path, tmp_path / "r.rtf", "rtf")[0], encoding="ascii").read()
    assert rtf.startswith("{\\rtf1") and rtf.count("{") == rtf.count("}") + rtf.count("\\{") - rtf.count("\\}")
    import re
    assert re.search(r"\\b\\i0 Grassetto", rtf) and re.search(r"\\b0\\i Corsivo", rtf)
    assert "\\u224?" in rtf    # à
    assert rtf.count("\\pict") == 2


# ------------------------------------------------------------------ CNV-14
@pytest.mark.skipif(__import__("shutil").which("gs") is None, reason="Ghostscript non installato")
def test_pdfa_through_engine(sample_pdf, tmp_path):
    from foliant.modules.convert import pdfa
    result = run_conversion(sample_pdf, tmp_path / "a.pdf", "pdfa-2b")
    data = open(result.paths[0], "rb").read()
    assert pdfa.verify_pdfa(data, "2b") == []
    assert result.details["pdfa"]["compliant"]
    assert os.path.basename(result.paths[0]) == "a.pdf"


def test_pptx_comments_as_notes(sample_pdf, tmp_path):
    from pptx import Presentation
    doc = pymupdf.open(sample_pdf)
    doc[1].add_text_annot((100, 300), "Ricordare il prezzo")
    path = _save(doc, tmp_path / "note.pdf")
    prs = Presentation(convert_pdf(path, tmp_path / "n.pptx", "pptx")[0])
    assert prs.slides[1].notes_slide.notes_text_frame.text == "Ricordare il prezzo"
    assert not prs.slides[0].has_notes_slide


def test_ocr_language_from_catalog(tmp_path):
    from foliant.modules.convert.engine import _lang_to_tesseract
    doc = pymupdf.open()
    doc.new_page()
    assert _lang_to_tesseract(doc) == "ita+eng"
    doc.xref_set_key(doc.pdf_catalog(), "Lang", "(de-DE)")
    assert _lang_to_tesseract(doc) == "deu+eng"


@pytest.mark.skipif(__import__("shutil").which("gs") is None, reason="Ghostscript non installato")
def test_pdfa_2u(sample_pdf, tmp_path):
    from foliant.modules.convert import pdfa
    result = run_conversion(sample_pdf, tmp_path / "u.pdf", "pdfa-2u")
    data = open(result.paths[0], "rb").read()
    assert pdfa.verify_pdfa(data, "2u") == []
    assert "<pdfaid:conformance>U</pdfaid:conformance>" in pymupdf.open(stream=data).get_xml_metadata()


def test_ocr_rotated_page_accuracy(tmp_path):
    from foliant.modules.convert.extract import ocr_available
    if not ocr_available():
        pytest.skip("tesseract non installato")
    import difflib
    lines = ["Contratto di compravendita orologio", "Il venditore dichiara che il bene",
             "e conforme alla descrizione fornita", "Prezzo concordato tremila euro"]
    src = pymupdf.open()
    sp = src.new_page()
    for k, line in enumerate(lines):
        sp.insert_text((60, 120 + k * 40), line, fontsize=20)
    straight = sp.get_pixmap(dpi=300)
    from PIL import Image
    img = Image.frombytes("RGB", (straight.width, straight.height), straight.samples)
    results = []
    for angle in (0, 90):
        rot = img.rotate(angle, expand=True)
        doc = pymupdf.open()
        p = doc.new_page(width=rot.width * 72 / 300, height=rot.height * 72 / 300)
        import io
        buf = io.BytesIO()
        rot.save(buf, "PNG")
        p.insert_image(p.rect, stream=buf.getvalue())
        path = tmp_path / f"r{angle}.pdf"
        doc.save(str(path))
        text = open(convert_pdf(str(path), tmp_path / f"r{angle}.txt", "txt")[0], encoding="utf-8").read()
        ref = " ".join(lines)
        got = " ".join(text.split())
        results.append(difflib.SequenceMatcher(None, ref, got).ratio())
    assert results[0] >= 0.95 and abs(results[0] - results[1]) <= 0.01


# ------------------------------------------------------------ regressioni
def test_pptx_rotated_page_inside_slide(tmp_path):
    from pptx import Presentation
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=500)
    p.insert_text((40, 450), "In fondo alla pagina", fontsize=12)
    p.set_rotation(90)
    path = _save(doc, tmp_path / "rot.pdf")
    prs = Presentation(convert_pdf(path, tmp_path / "r.pptx", "pptx")[0])
    assert prs.slide_width > prs.slide_height
    for shape in prs.slides[0].shapes:
        assert 0 <= shape.left <= prs.slide_width and 0 <= shape.top <= prs.slide_height


def test_html_reexport_with_assets(image_pdf, tmp_path):
    first = convert_pdf(image_pdf, tmp_path / "img.html", "html", single_file=False)
    second = convert_pdf(image_pdf, tmp_path / "img.html", "html", single_file=False)
    assert second[0].endswith("img (1).html")
    html = open(second[0], encoding="utf-8").read()
    import re
    src = re.search(r'src="([^"]+)"', html).group(1)
    assert os.path.exists(tmp_path / src) and src.startswith("img (1)_files/")
    assert first[0].endswith("img.html")


def test_xlsx_formula_like_text_and_file_mode(tmp_path):
    from openpyxl import load_workbook
    doc = pymupdf.open()
    page = doc.new_page()
    for r, row in enumerate([["Formula", "Valore"], ["=A1+B2", "3"]]):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(50 + c * 150, 50 + r * 25, 200 + c * 150, 75 + r * 25)
            page.draw_rect(rect, color=(0, 0, 0))
            page.insert_text((rect.x0 + 3, rect.y1 - 7), val, fontsize=10)
    path = _save(doc, tmp_path / "f.pdf")
    out = convert_pdf(path, tmp_path / "f.xlsx", "xlsx")[0]
    ws = load_workbook(out).active
    assert ws["A2"].value == "=A1+B2" and ws["A2"].data_type == "s"
    mask = os.umask(0)
    os.umask(mask)
    assert os.stat(out).st_mode & 0o777 == 0o666 & ~mask


def test_internal_links_on_page_subset(tmp_path):
    doc = pymupdf.open()
    for i in range(4):
        doc.new_page().insert_text((72, 100), f"Pagina {i + 1}", fontsize=12)
    doc[2].insert_text((72, 200), "Vai avanti", fontsize=12)
    doc[2].insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(70, 188, 200, 204), "page": 3,
                        "to": pymupdf.Point(72, 100)})
    path = _save(doc, tmp_path / "l.pdf")
    html = open(convert_pdf(path, tmp_path / "l.html", "html", pages="3-4")[0], encoding="utf-8").read()
    assert 'href="#page-4"' in html and 'id="page-4"' in html


def test_long_names_and_bad_template(sample_pdf, tmp_path):
    name = "è" * 120
    out = convert_pdf(sample_pdf, tmp_path / f"{name}.txt", "txt")
    assert os.path.exists(out[0])
    with pytest.raises(ConversionError):
        convert_pdf(sample_pdf, tmp_path / "x.png", "png", dpi=72, name_template="{pagina:zz}")
    assert list(tmp_path.glob("*.png")) == []


def _tagged_pdf(path):
    """Pagina con due paragrafi: la struttura mette prima quello in basso."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "x", fontsize=1)
    font = [f[4] for f in page.get_fonts()][0]
    content = (f"/P <</MCID 0>> BDC BT /{font} 14 Tf 72 700 Td (Paragrafo in alto) Tj ET EMC\n"
               f"/P <</MCID 1>> BDC BT /{font} 14 Tf 72 400 Td (Paragrafo in basso) Tj ET EMC").encode()
    doc.update_stream(page.get_contents()[0], content)
    root, el, p1, p0 = (doc.get_new_xref() for _ in range(4))
    doc.update_object(p1, f"<</Type/StructElem/S/P/P {el} 0 R/Pg {page.xref} 0 R/K 1>>")
    doc.update_object(p0, f"<</Type/StructElem/S/P/P {el} 0 R/Pg {page.xref} 0 R/K 0>>")
    doc.update_object(el, f"<</Type/StructElem/S/Document/P {root} 0 R/K[{p1} 0 R {p0} 0 R]>>")
    doc.update_object(root, f"<</Type/StructTreeRoot/K {el} 0 R>>")
    doc.xref_set_key(doc.pdf_catalog(), "StructTreeRoot", f"{root} 0 R")
    doc.xref_set_key(doc.pdf_catalog(), "MarkInfo", "<</Marked true>>")
    return _save(doc, path)


def test_txt_follows_structure_tree(tmp_path):
    path = _tagged_pdf(tmp_path / "tag.pdf")
    text = open(convert_pdf(path, tmp_path / "t.txt", "txt")[0], encoding="utf-8").read()
    assert text.index("Paragrafo in basso") < text.index("Paragrafo in alto")
    geo = open(convert_pdf(path, tmp_path / "g.txt", "txt", use_structure=False)[0], encoding="utf-8").read()
    assert geo.index("Paragrafo in alto") < geo.index("Paragrafo in basso")


def test_docx_comments_and_header_footer(tmp_path):
    import docx
    doc = pymupdf.open()
    for i in range(4):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 40), "Orologeria Verdi - Catalogo", fontsize=9)
        p.insert_text((72, 300), f"Contenuto della pagina {i + 1}", fontsize=12)
        p.insert_text((260, 815), f"Pagina {i + 1} di 4", fontsize=9)
    doc[1].add_text_annot((80, 290), "Verificare il prezzo").set_info(title="Anna")
    path = _save(doc, tmp_path / "hf.pdf")
    out = convert_pdf(path, tmp_path / "hf.docx", "docx")[0]
    d = docx.Document(out)
    body = "\n".join(p.text for p in d.paragraphs)
    assert "Catalogo" not in body and "di 4" not in body and "Contenuto della pagina 3" in body
    assert "Catalogo" in d.sections[0].header.paragraphs[0].text
    with zipfile.ZipFile(out) as z:
        footer = "".join(z.read(n).decode() for n in z.namelist() if n.startswith("word/footer"))
        comments = z.read("word/comments.xml").decode()
    assert 'w:instr="PAGE"' in footer and 'w:instr="NUMPAGES"' in footer
    assert "Verificare il prezzo" in comments and 'w:author="Anna"' in comments


def test_many_pages_fast(tmp_path):
    """Documenti grandi: elaborazione per pagina senza il modello di layout (≈ ms per pagina)."""
    import time
    doc = pymupdf.open()
    for i in range(300):
        p = doc.new_page()
        for k in range(15):
            p.insert_text((72, 100 + k * 14), f"Pagina {i} riga {k} del registro di magazzino", fontsize=10)
    path = _save(doc, tmp_path / "grande.pdf")
    t = time.time()
    out = convert_pdf(path, tmp_path / "g.txt", "txt")[0]
    assert time.time() - t < 30
    assert open(out, encoding="utf-8").read().count("\f") == 299


def test_encrypted_faithful_docx_and_pdfa(encrypted_pdf, tmp_path):
    import docx
    out = convert_pdf(encrypted_pdf, tmp_path / "e.docx", "docx", layout="faithful", password="segreta")[0]
    assert "Riservato" in "\n".join(p.text for p in docx.Document(out).paragraphs)
    if __import__("shutil").which("gs"):
        pdf = convert_pdf(encrypted_pdf, tmp_path / "e.pdf", "pdfa-2b", password="segreta")[0]
        d = pymupdf.open(pdf)
        assert not d.needs_pass and "Riservato" in d[0].get_text()


def test_html_per_page(links_pdf, tmp_path):
    outs = convert_pdf(links_pdf, tmp_path / "s.html", "html", per_page=True)
    names = [os.path.basename(p) for p in outs]
    assert names == ["s.html", "s_001.html", "s_002.html", "s_003.html"]
    first = open(outs[1], encoding="utf-8").read()
    assert 'href="s_002.html#page-2"' in first and 'href="s_002.html"' in first


def test_ocr_skewed_scan(tmp_path):
    from foliant.modules.convert.extract import ocr_available
    if not ocr_available():
        pytest.skip("tesseract non installato")
    import difflib
    import io as _io
    from PIL import Image
    lines = ["Garanzia internazionale del produttore", "Numero di serie verificato in negozio",
             "Scatola e documenti originali presenti"]
    src = pymupdf.open()
    sp = src.new_page()
    for k, line in enumerate(lines):
        sp.insert_text((60, 120 + k * 40), line, fontsize=20)
    pix = sp.get_pixmap(dpi=300)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).rotate(3, fillcolor="white")
    doc = pymupdf.open()
    p = doc.new_page()
    buf = _io.BytesIO()
    img.save(buf, "PNG")
    p.insert_image(p.rect, stream=buf.getvalue())
    path = _save(doc, tmp_path / "storto.pdf")
    text = " ".join(open(convert_pdf(path, tmp_path / "s.txt", "txt")[0], encoding="utf-8").read().split())
    assert difflib.SequenceMatcher(None, " ".join(lines), text).ratio() >= 0.95


def test_scan_to_docx_uses_ocr_module_both_layouts(scanned_mixed, tmp_path, monkeypatch):
    """Scansione → Word: il modulo OCR (modalità EDITABLE) alimenta entrambi i layout."""
    import sys
    import types
    import docx
    calls = []
    words = "Articolo Quantità Prezzo orologio cinturino garanzia"

    def fake_ocr_pdf_bytes(data, languages, mode, pages=None, progress=None, options=None):
        calls.append((languages, mode, pages))
        doc = pymupdf.open(stream=data, filetype="pdf")
        for i in pages:
            page = doc[i]
            page.insert_text((72, 200), words, fontsize=14)   # testo vero e visibile
        out = doc.tobytes()
        doc.close()
        return out

    engine_mod = types.ModuleType("foliant.modules.ocr.engine")
    engine_mod.ocr_pdf_bytes = fake_ocr_pdf_bytes
    engine_mod.EDITABLE = "editable"
    pkg = types.ModuleType("foliant.modules.ocr")
    pkg.engine = engine_mod
    monkeypatch.setitem(sys.modules, "foliant.modules.ocr", pkg)
    monkeypatch.setitem(sys.modules, "foliant.modules.ocr.engine", engine_mod)
    for layout in ("flow", "faithful"):
        result = run_conversion(scanned_mixed, tmp_path / f"{layout}.docx", "docx", layout=layout, language="ita+eng")
        text = " ".join(p.text for p in docx.Document(result.paths[0]).paragraphs)
        found = sum(1 for w in words.split() if w in text)
        assert found / len(words.split()) >= 0.95, (layout, text)
        assert result.ocr_pages == [3]
    assert calls[0] == (["ita", "eng"], "editable", [2])


def _scanned_quote(tmp_path):
    font = dict(fontname="dejavu", fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    src = pymupdf.open()
    p = src.new_page()
    p.insert_text((60, 80), "Preventivo di riparazione", fontsize=20, **font)
    p.insert_text((60, 130), "Il cliente consegna un orologio automatico per la revisione.", fontsize=12, **font)
    rows = [["Articolo", "Quantità", "Prezzo"], ["Cinturino", "4", "120,00 €"], ["Vetro", "2", "85,50 €"],
            ["Corona", "3", "40,00 €"]]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            rect = pymupdf.Rect(60 + c * 150, 230 + r * 28, 210 + c * 150, 258 + r * 28)
            p.draw_rect(rect, color=(0, 0, 0), width=1)
            p.insert_text((rect.x0 + 6, rect.y1 - 9), v, fontsize=12, **font)
    pix = p.get_pixmap(dpi=300)
    doc = pymupdf.open()
    q = doc.new_page()
    q.insert_image(q.rect, stream=pix.tobytes("png"))
    return _save(doc, tmp_path / "scan.pdf"), rows


def test_scan_table_to_docx_and_xlsx_with_real_ocr(tmp_path):
    """Scansione con tabella → DOCX (tabella nativa) e XLSX (celle tipizzate) con il modulo OCR vero."""
    from foliant.modules.convert.engine import foliant_ocr_module
    from foliant.modules.convert.extract import ocr_available
    if foliant_ocr_module() is None or not ocr_available():
        pytest.skip("modulo OCR o tesseract non disponibili")
    import docx
    from openpyxl import load_workbook
    path, rows = _scanned_quote(tmp_path)
    d = docx.Document(convert_pdf(path, tmp_path / "s.docx", "docx", language="ita+eng")[0])
    assert "Preventivo di riparazione" in " ".join(p.text for p in d.paragraphs)
    cells = [[sorted(c.text.split()) for c in r.cells] for r in d.tables[0].rows]
    assert cells == [[sorted(v.split()) for v in row] for row in rows]   # parole di ogni cella
    ws = load_workbook(convert_pdf(path, tmp_path / "s.xlsx", "xlsx", language="ita+eng")[0]).active
    values = [[c.value for c in r] for r in ws.iter_rows()]
    assert values[0] == rows[0] and values[1] == ["Cinturino", 4, 120] and values[2][2] == 85.5


def test_grid_tables_ignores_paragraphs():
    from foliant.modules.convert.extract import grid_tables

    def blk(x0, y0, x1, text):
        return {"type": 0, "bbox": (x0, y0, x1, y0 + 14),
                "lines": [{"spans": [{"text": text, "bbox": (x0, y0, x1, y0 + 14), "size": 12}]}]}
    blocks = [blk(60, 100, 500, "Un paragrafo lungo"), blk(60, 130, 500, "Un altro paragrafo"),
              blk(60, 200, 100, "A"), blk(200, 200, 240, "B"), blk(60, 228, 100, "1"), blk(200, 228, 240, "2")]
    found = grid_tables(blocks)
    assert len(found) == 1 and found[0][1] == [["A", "B"], ["1", "2"]]
    # una riga per blocco con le celle come segmenti separati (come l'OCR modificabile)
    row = lambda y, cells: {"type": 0, "bbox": (60, y, 400, y + 14), "lines": [{"spans": [
        {"text": t, "bbox": (x, y, x + 30, y + 14), "size": 12} for x, t in cells]}]}
    found = grid_tables([row(300, [(60, "Art"), (200, "Qtà")]), row(330, [(60, "Vetro"), (200, "2")])])
    assert found and found[0][1] == [["Art", "Qtà"], ["Vetro", "2"]]


def test_images_hide_unsigned_signature_placeholder(tmp_path):
    """Il segnaposto disegnato dal motore sui campi firma vuoti non deve comparire nelle immagini."""
    from PIL import Image
    doc = pymupdf.open()
    p = doc.new_page(width=300, height=200)
    p.insert_text((20, 30), "Firma qui sotto", fontsize=12)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE
    w.field_name = "Firma"
    w.rect = pymupdf.Rect(20, 60, 280, 160)
    p.add_widget(w)
    path = _save(doc, tmp_path / "firma.pdf")
    out = convert_pdf(path, tmp_path / "f.png", "png", dpi=72)[0]
    img = Image.open(out).convert("RGB")
    orange = sum(1 for r, g, b in img.getdata() if r > 200 and 80 < g < 200 and b < 80)
    assert orange == 0
    assert pymupdf.open(path)[0].first_widget is not None   # il file sorgente non cambia


def _words(text: str) -> list[str]:
    import unicodedata
    return unicodedata.normalize("NFC", text).split()


def test_docx_flow_similarity_ten_pages(tmp_path):
    """CNV-03: PDF nativo di 10 pagine a colonna singola → similarità testuale ≥ 99%."""
    import difflib
    import docx
    doc = pymupdf.open()
    lorem = ("Il presente contratto di compravendita riguarda un orologio automatico con cassa in acciaio, "
             "quadrante blu e bracciale originale. Il venditore garantisce la provenienza lecita del bene. ")
    for i in range(10):
        p = doc.new_page()
        p.insert_textbox(pymupdf.Rect(72, 72, 523, 770), f"Articolo {i + 1}. " + lorem * 6, fontsize=11)
    path = _save(doc, tmp_path / "dieci.pdf")
    src = _words(" ".join(pymupdf.open(path)[i].get_text() for i in range(10)))
    out = _words(" ".join(p.text for p in docx.Document(convert_pdf(path, tmp_path / "d.docx", "docx")[0]).paragraphs))
    assert difflib.SequenceMatcher(None, src, out, autojunk=False).ratio() >= 0.99


@pytest.mark.skipif(__import__("shutil").which("soffice") is None, reason="LibreOffice non installato")
def test_docx_opens_in_office_suite(links_pdf, table_pdf, tmp_path):
    """CNV-03: il DOCX prodotto è valido per un'applicazione d'ufficio indipendente (LibreOffice lo
    apre e lo converte senza errori, con il testo atteso)."""
    import subprocess
    out = convert_pdf(table_pdf[0], tmp_path / "t.docx", "docx")[0]
    profile = tmp_path / "lo-profile"
    subprocess.run(["soffice", f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf",
                    "--outdir", str(tmp_path / "rt"), out], capture_output=True, timeout=180)
    back = tmp_path / "rt" / "t.pdf"
    assert back.exists()
    text = pymupdf.open(str(back))[0].get_text()
    assert "Marca1" in text and "Prezzo" in text


def test_docx_rotated_text_as_rotated_box(tmp_path):
    """CNV-03: testo ruotato o verticale → casella di testo ruotata (fuori dal flusso)."""
    import zipfile
    import docx
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Fattura numero 42 del 3 marzo", fontsize=12)
    p.insert_text((72, 130), "Orologio cronografo, cassa in acciaio, movimento automatico", fontsize=12)
    p.insert_text((40, 700), "Codice modulo FT-2026", fontsize=8, rotate=90)
    p.insert_text((560, 100), "Copia per il cliente", fontsize=8, rotate=270)
    path = _save(doc, tmp_path / "ruotato.pdf")
    out = convert_pdf(path, tmp_path / "r.docx", "docx")[0]
    body = [q.text for q in docx.Document(out).paragraphs]
    assert body == ["Fattura numero 42 del 3 marzo", "Orologio cronografo, cassa in acciaio, movimento automatico"]
    xml = zipfile.ZipFile(out).read("word/document.xml").decode()
    assert xml.count("<wps:txbx>") == 2
    assert 'vert="vert270"' in xml and 'vert="vert"' in xml
    assert "Codice modulo FT-2026" in xml and "Copia per il cliente" in xml
    import shutil
    import subprocess
    if shutil.which("soffice"):
        subprocess.run(["soffice", f"-env:UserInstallation=file://{tmp_path / 'lo'}", "--headless",
                        "--convert-to", "pdf", "--outdir", str(tmp_path / "rt"), out],
                       capture_output=True, timeout=180)
        back = pymupdf.open(str(tmp_path / "rt" / "r.pdf"))[0]
        dirs = {"".join(s["text"] for s in ln["spans"]): ln["dir"]
                for b in back.get_text("dict")["blocks"] for ln in b.get("lines", [])}
        assert dirs["Codice modulo FT-2026"] == (0.0, -1.0)
        assert dirs["Copia per il cliente"] == (0.0, 1.0)


def test_rotated_page_text_angle(tmp_path):
    """L'angolo del testo è quello visualizzato: su una pagina con /Rotate 90 il testo orizzontale
    della pagina non ruotata appare verticale."""
    from foliant.modules.convert.extract import analyze_page
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Testo normale", fontsize=12)
    p.set_rotation(90)
    paras = [it for it in analyze_page(p).items if hasattr(it, "angle")]
    assert [it.angle for it in paras] == [270]


def test_docx_mostly_rotated_page_stays_in_flow(tmp_path):
    """Pagina con /Rotate e testo tutto «ruotato»: il testo resta nel flusso del documento."""
    import docx
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Listino prezzi autunno", fontsize=12)
    p.insert_text((72, 130), "Cronografo in oro rosa", fontsize=12)
    p.set_rotation(90)
    path = _save(doc, tmp_path / "pagina_ruotata.pdf")
    body = [q.text for q in docx.Document(convert_pdf(path, tmp_path / "p.docx", "docx")[0]).paragraphs]
    assert body == ["Listino prezzi autunno", "Cronografo in oro rosa"]


def test_txt_5000_pages_memory(tmp_path):
    """CNV-01: 5 000 pagine in TXT restano sotto il limite di memoria di NFR-14 (2 GB),
    misurato in un processo separato."""
    import subprocess
    import sys
    script = tmp_path / "misura.py"
    script.write_text(
        "import resource, sys, pymupdf\n"
        "import foliant\n"
        "from foliant.modules.convert.engine import convert_pdf\n"
        "doc = pymupdf.open()\n"
        "for i in range(5000):\n"
        "    p = doc.new_page()\n"
        "    p.insert_text((72, 100), f'Pagina {i + 1} listino', fontsize=12)\n"
        "    p.insert_text((72, 130), 'Referenza 1234 acciaio automatico 5.400 euro', fontsize=10)\n"
        "data = doc.tobytes(); doc.close()\n"
        "base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n"
        "out = convert_pdf(data, sys.argv[1], 'txt')[0]\n"
        "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024)\n"
        "print(open(out, encoding='utf-8').read().count('\\f'))\n")
    from pathlib import Path
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    res = subprocess.run([sys.executable, str(script), str(tmp_path / "grande.txt")], capture_output=True,
                         text=True, timeout=170, env=env)
    assert res.returncode == 0, res.stderr[-2000:]
    peak_mb, feeds = (int(v) for v in res.stdout.split())
    assert peak_mb < 2048
    assert feeds == 4999


def _char_accuracy(ref: str, got: str) -> float:
    """1 − distanza di edit sui caratteri / lunghezza del riferimento (definizione della specifica)."""
    prev = list(range(len(got) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(got, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return 1 - prev[-1] / max(1, len(ref))


def _scan_of(lines: list[str], tmp_path, dpi: int = 300, name: str = "scan.pdf") -> str:
    font = dict(fontname="dejavu", fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf")
    src = pymupdf.open()
    sp = src.new_page()
    for k, line in enumerate(lines):
        sp.insert_text((60, 90 + k * 26), line, fontsize=12, **font)
    pix = sp.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=pix.tobytes("png"))
    return _save(doc, tmp_path / name)


CONTRACT_LINES = [
    "Contratto di compravendita di orologio usato",
    "Il venditore cede all'acquirente un cronografo automatico",
    "con cassa in acciaio, quadrante nero e bracciale originale.",
    "Il prezzo concordato è di quattromila euro, già versati.",
    "Il venditore dichiara la provenienza lecita del bene e",
    "garantisce il funzionamento per dodici mesi dalla consegna.",
]


def test_ocr_docx_accuracy_and_confidence_report(tmp_path):
    """CNV-02: scansione a 300 dpi in italiano → DOCX con accuratezza per carattere ≥ 98%;
    il report indica pagine, lingue e confidenza media."""
    from foliant.modules.convert.engine import foliant_ocr_module
    from foliant.modules.convert.extract import ocr_available
    if foliant_ocr_module() is None or not ocr_available():
        pytest.skip("modulo OCR o tesseract non disponibili")
    import docx
    path = _scan_of(CONTRACT_LINES, tmp_path)
    res = run_conversion(path, tmp_path / "c.docx", "docx", language="ita")
    got = " ".join(" ".join(p.text for p in docx.Document(res.paths[0]).paragraphs).split())
    ref = " ".join(CONTRACT_LINES)
    assert _char_accuracy(ref, got) >= 0.98, got
    ocr = res.details["ocr"]
    assert res.ocr_pages == [1] and ocr["languages"] == ["ita"]
    assert ocr["mean_confidence"] is not None and ocr["mean_confidence"] > 80
    report = res.report_text()
    assert "Confidenza media OCR" in report and "Lingue OCR: ita" in report


def test_ocr_illegible_page_flagged(tmp_path):
    """Pagina illeggibile (scarabocchi): resta l'immagine e la pagina è segnalata nel report."""
    from foliant.modules.convert.engine import foliant_ocr_module
    from foliant.modules.convert.extract import ocr_available
    if foliant_ocr_module() is None or not ocr_available():
        pytest.skip("modulo OCR o tesseract non disponibili")
    import random

    from PIL import Image, ImageDraw
    rnd = random.Random(4)
    img = Image.new("L", (2480, 3508), 255)
    d = ImageDraw.Draw(img)
    for _k in range(300):
        x, y = rnd.randrange(200, 2200), rnd.randrange(200, 3200)
        d.line([(x, y), (x + rnd.randrange(-60, 60), y + rnd.randrange(-40, 40)),
                (x + rnd.randrange(-60, 60), y + rnd.randrange(-40, 40))], fill=0, width=4)
    import io
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=buf.getvalue())
    path = _save(doc, tmp_path / "scarabocchi.pdf")
    res = run_conversion(path, tmp_path / "s.txt", "txt", language="ita")
    assert any("Pagina 1" in w and ("poco leggibile" in w or "non riuscito" in w) for w in res.warnings), res.warnings


def test_undecodable_font_area_recognized_with_ocr(tmp_path):
    """CNV-02: testo nativo con font senza /ToUnicode → OCR della sola area, con avviso;
    il testo decodificabile della stessa pagina resta quello estratto."""
    from foliant.modules.convert.extract import ocr_available
    if not ocr_available():
        pytest.skip("tesseract non installato")
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Garanzia internazionale valida", fontsize=16, fontname="dv",
                  fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    p.insert_text((72, 200), "Testo normale leggibile", fontsize=12)
    for xref, *_rest in p.get_fonts():
        if doc.xref_get_key(xref, "ToUnicode")[0] == "xref":
            doc.xref_set_key(xref, "ToUnicode", "null")
    path = _save(doc, tmp_path / "senza_unicode.pdf")
    assert "Garanzia" not in pymupdf.open(path)[0].get_text()
    res = run_conversion(path, tmp_path / "u.txt", "txt", language="ita")
    text = open(res.paths[0], encoding="utf-8").read()
    assert "Garanzia internazionale valida" in text and "Testo normale leggibile" in text
    assert any("non decodificabile" in w for w in res.warnings)
    assert res.ocr_pages == [1]
    # con OCR «mai» non si riconosce nulla
    res = run_conversion(path, tmp_path / "n.txt", "txt", ocr="never")
    assert "Garanzia" not in open(res.paths[0], encoding="utf-8").read()


def test_ocr_low_resolution_scan_warns(tmp_path):
    """Scansione sotto 150 dpi: OCR comunque eseguito e avviso di accuratezza ridotta nel report."""
    from foliant.modules.convert.engine import foliant_ocr_module
    from foliant.modules.convert.extract import ocr_available
    if foliant_ocr_module() is None or not ocr_available():
        pytest.skip("modulo OCR o tesseract non disponibili")
    path = _scan_of(CONTRACT_LINES[:2], tmp_path, dpi=100, name="bassa.pdf")
    res = run_conversion(path, tmp_path / "b.txt", "txt", language="ita")
    assert res.ocr_pages == [1]
    assert any("Pagina 1" in w and "isoluzione" in w for w in res.warnings), res.warnings


def test_total_line_right_below_table_is_kept(tmp_path):
    """Collaudo B, D2: la riga «Totale imponibile» 3-4 pt sotto l'ultimo bordo della tabella, che
    MuPDF unisce all'ultima riga in un solo blocco, non deve sparire (DOCX, HTML, RTF, TXT)."""
    import docx
    doc = pymupdf.open()
    p = doc.new_page()
    rows = [["Articolo", "Qtà", "Prezzo", "Importo"], ["Cronografo", "1", "18.000,00", "18.000,00"],
            ["Cinturino", "2", "193,25", "386,50"], ["Revisione", "1", "4.000,00", "4.000,00"]]
    x0, y0, cw, rh = 90, 140, 90, 14
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            rect = pymupdf.Rect(x0 + c * cw, y0 + r * rh, x0 + (c + 1) * cw, y0 + (r + 1) * rh)
            p.draw_rect(rect, color=(0, 0, 0), width=0.8)
            p.insert_text((rect.x0 + 4, rect.y1 - 3), v, fontsize=10)
    bottom = y0 + len(rows) * rh
    p.insert_text((x0 + 4, bottom + 3.5 + 8), "Totale imponibile 22.386,50 EUR", fontsize=10)
    path = _save(doc, tmp_path / "fattura.pdf")
    # precondizione: MuPDF mette tabella e totale nello stesso blocco
    with pymupdf.open(path) as chk:
        merged = [b for b in chk[0].get_text("dict")["blocks"]
                  if any("Totale" in "".join(s["text"] for s in ln["spans"]) for ln in b["lines"])]
        assert len(merged[0]["lines"]) > 1
    d = docx.Document(convert_pdf(path, tmp_path / "f.docx", "docx")[0])
    assert any("Totale imponibile 22.386,50 EUR" in q.text for q in d.paragraphs)
    assert len(d.tables) == 1 and d.tables[0].cell(3, 0).text == "Revisione"
    for fmt in ("html", "rtf", "txt"):
        out = convert_pdf(path, tmp_path / f"f.{fmt}", fmt)[0]
        text = open(out, encoding="utf-8", errors="replace").read()
        assert "22.386,50" in text, fmt
        assert text.count("Revisione") == 1, fmt
