"""Test del riconoscimento automatico dei campi (forms.detect)."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.forms.detect import (Candidate, detect, detect_page,
                                         page_needs_ocr)


def _doc() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    return doc, doc.new_page(width=595, height=842)


def _draw_form(page: pymupdf.Page) -> None:
    """Modulo di prova: «Nome:» con linea e casella «Accetto»."""
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100), width=1)
    page.draw_rect(pymupdf.Rect(72, 150, 84, 162), width=1)
    page.insert_text((90, 160), "Accetto", fontsize=11)


def test_label_and_line():
    doc, page = _doc()
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100))
    cands = detect_page(doc, 0)
    assert len(cands) == 1
    c = cands[0]
    assert (c.kind, c.name, c.source, c.page) == ("text", "Nome", "line", 0)
    assert c.rect.y1 <= 100 and c.rect.y1 >= 98
    assert 10 <= c.rect.height <= 18
    assert c.rect.x0 == pytest.approx(110) and c.rect.x1 == pytest.approx(300)


def test_checkbox_with_label_on_right():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 150, 84, 162))
    page.insert_text((90, 160), "Accetto", fontsize=11)
    cands = detect_page(doc, 0)
    assert len(cands) == 1
    c = cands[0]
    assert (c.kind, c.name, c.source) == ("checkbox", "Accetto", "box")
    assert c.rect == pymupdf.Rect(73, 151, 83, 161)


def test_big_empty_rect_is_multiline():
    doc, page = _doc()
    page.insert_text((72, 290), "Note", fontsize=11)
    page.draw_rect(pymupdf.Rect(72, 300, 400, 400))
    (c,) = detect_page(doc, 0)
    assert c.kind == "text" and c.multiline and c.name == "Note"


def test_small_empty_rect_is_single_line():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 300, 250, 320))
    (c,) = detect_page(doc, 0)
    assert c.kind == "text" and not c.multiline and c.name == ""


def test_rect_with_text_is_ignored():
    doc, page = _doc()
    page.draw_rect(pymupdf.Rect(72, 300, 400, 340), fill=(0.9, 0.9, 0.9))
    page.insert_text((80, 325), "Intestazione del modulo", fontsize=11)
    assert detect_page(doc, 0) == []


def test_underscores():
    doc, page = _doc()
    page.insert_text((72, 200), "Cognome: __________", fontsize=11)
    page.insert_text((72, 240), "Citta:__________", fontsize=11)
    cands = detect_page(doc, 0)
    assert [c.name for c in cands] == ["Cognome", "Citta"]
    first = cands[0]
    assert first.kind == "text" and first.source == "underscore"
    label = page.search_for("Cognome:")[0]
    assert first.rect.x0 > label.x1 - 1
    assert first.rect.y1 == pytest.approx(200, abs=2)


def test_underlined_text_not_detected():
    doc, page = _doc()
    page.insert_text((72, 200), "Testo sottolineato importante", fontsize=11)
    width = pymupdf.get_text_length("Testo sottolineato importante",
                                    fontsize=11)
    page.draw_line((72, 201.5), (72 + width, 201.5))
    assert detect_page(doc, 0) == []


def test_table_grid_not_detected():
    doc, page = _doc()
    for y in (100, 120, 140):
        page.draw_line((72, y), (372, y))
    for x in (72, 172, 272, 372):
        page.draw_line((x, 100), (x, 140))
    assert all(c.source != "line" for c in detect_page(doc, 0))


def test_existing_widget_suppresses_candidate():
    doc, page = _doc()
    page.insert_text((72, 100), "Nome:", fontsize=11)
    page.draw_line((110, 100), (300, 100))
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "nome"
    w.rect = pymupdf.Rect(110, 82, 300, 99)
    page.add_widget(w)
    assert detect_page(doc, 0) == []


def test_scanned_page():
    src, page = _doc()
    _draw_form(page)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
    doc = pymupdf.open()
    scan = doc.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    cands = detect_page(doc, 0)
    assert {c.source for c in cands} == {"raster"}
    texts = [c for c in cands if c.kind == "text"]
    checks = [c for c in cands if c.kind == "checkbox"]
    assert len(texts) == 1 and len(checks) == 1
    t, k = texts[0].rect, checks[0].rect
    assert t.x0 == pytest.approx(110, abs=4) and t.x1 == pytest.approx(300, abs=4)
    assert t.y1 == pytest.approx(99, abs=4)
    for a, b in zip(k, (73, 151, 83, 161)):
        assert a == pytest.approx(b, abs=4)
    # senza analisi raster non si trova nulla
    assert detect_page(doc, 0, raster=False) == []


def test_scanned_rotated_page():
    # scansione orizzontale raddrizzata con /Rotate 90: si vede diritta
    src, page = _doc()
    _draw_form(page)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    doc = pymupdf.open()
    scan = doc.new_page(width=842, height=595)
    scan.set_rotation(90)
    scan.insert_image(scan.rect * scan.derotation_matrix, pixmap=pix,
                      rotate=90)
    assert scan.rect.width == 595
    derot = scan.derotation_matrix
    cands = detect_page(doc, 0)
    checks = [c for c in cands if c.kind == "checkbox"]
    texts = [c for c in cands if c.kind == "text"]
    assert len(checks) == 1 and len(texts) == 1
    assert checks[0].rect.contains(pymupdf.Point(78, 156) * derot)
    assert texts[0].rect.contains(pymupdf.Point(200, 95) * derot)
    # in coordinate non ruotate il campo di testo è «verticale»
    assert texts[0].rect.height > texts[0].rect.width


def test_rotated_vector_page():
    doc, page = _doc()
    _draw_form(page)
    page.set_rotation(90)
    cands = detect_page(doc, 0)
    by_kind = {c.kind: c for c in cands}
    assert set(by_kind) == {"text", "checkbox"}
    assert by_kind["text"].rect.contains(pymupdf.Point(200, 95))
    assert by_kind["text"].name == "Nome"
    assert by_kind["checkbox"].rect.contains(pymupdf.Point(78, 156))
    assert by_kind["checkbox"].name == "Accetto"


def test_empty_page():
    doc, _ = _doc()
    assert detect_page(doc, 0) == []
    assert detect_page(doc, 0, raster=True) == []


def test_sorted_and_named_in_order():
    doc, page = _doc()
    _draw_form(page)
    page.insert_text((320, 100), "Cognome:", fontsize=11)
    page.draw_line((370, 100), (520, 100))
    names = [c.name for c in detect_page(doc, 0)]
    assert names == ["Nome", "Cognome", "Accetto"]


def test_detect_progress():
    doc = pymupdf.open()
    for _ in range(3):
        _draw_form(doc.new_page())
    calls: list[tuple[float, str]] = []
    cands = detect(doc, progress=lambda f, t: calls.append((f, t)))
    assert len(calls) == 3 and calls[-1][0] == pytest.approx(1.0)
    assert [c.page for c in cands] == [0, 0, 1, 1, 2, 2]
    assert all(isinstance(c, Candidate) for c in cands)
    assert [c.page for c in detect(doc, pages=[2])] == [2, 2]


# ---------------------------------------------------------------- pettini


def _iou(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = abs(a & b)
    return inter / (abs(a) + abs(b) - inter)


def _comb_boxes(page, x, y, n, w=14, h=16):
    """Pettine di `n` rettangoli separati con i lati in comune."""
    for i in range(n):
        page.draw_rect(pymupdf.Rect(x + i * w, y, x + (i + 1) * w, y + h))
    return pymupdf.Rect(x, y, x + n * w, y + h)


def test_comb_separate_boxes():
    doc, page = _doc()
    page.insert_text((72, 212), "Codice fiscale:", fontsize=11)
    box = _comb_boxes(page, 160, 200, 16)
    (c,) = detect_page(doc, 0)
    assert (c.kind, c.comb, c.source) == ("text", 16, "comb")
    assert "Codice" in c.name
    assert _iou(c.rect, box) >= 0.9


def test_comb_outer_rect_with_dividers():
    doc, page = _doc()
    page.insert_text((72, 212), "Codice fiscale:", fontsize=11)
    box = pymupdf.Rect(160, 200, 160 + 16 * 14, 216)
    page.draw_rect(box)
    for i in range(1, 16):
        page.draw_line((160 + i * 14, 200), (160 + i * 14, 216))
    (c,) = detect_page(doc, 0)
    assert (c.kind, c.comb, c.name) == ("text", 16, "Codice_fiscale")
    assert _iou(c.rect, box) >= 0.9


def test_comb_loose_lines_and_gaps():
    doc, page = _doc()
    # solo linee: due orizzontali e 9 verticali (8 caselle)
    page.draw_line((100, 300), (228, 300))
    page.draw_line((100, 316), (228, 316))
    for i in range(9):
        page.draw_line((100 + i * 16, 300), (100 + i * 16, 316))
    # rettangoli separati da 1,5 pt
    for i in range(5):
        x = 100 + i * 15.5
        page.draw_rect(pymupdf.Rect(x, 400, x + 14, 414))
    cands = detect_page(doc, 0)
    assert [(c.comb, c.kind) for c in cands] == [(8, "text"), (5, "text")]
    assert _iou(cands[0].rect, pymupdf.Rect(100, 300, 228, 316)) >= 0.9
    assert _iou(cands[1].rect, pymupdf.Rect(100, 400, 176, 414)) >= 0.9


def test_two_boxes_are_not_a_comb():
    doc, page = _doc()
    _comb_boxes(page, 100, 200, 2)
    cands = detect_page(doc, 0)
    assert [c.kind for c in cands] == ["checkbox", "checkbox"]
    assert all(c.comb == 0 for c in cands)


# -------------------------------------------------------------- benchmark


def _benchmark_form(page) -> list[tuple[str, pymupdf.Rect]]:
    """Modulo vettoriale con 20 campi attesi (tipo, rettangolo) e rumore."""
    exp: list[tuple[str, pymupdf.Rect]] = []

    def text(pos, s, size=10):
        page.insert_text(pos, s, fontsize=size)

    def line(label, x, y, x1, lx=None):
        text((lx if lx is not None else x - 70, y - 2), label)
        page.draw_line((x, y), (x1, y))
        exp.append(("text", pymupdf.Rect(x, y - 16, x1, y)))

    def box(label, r):
        text((r.x0 - 70, r.y1 - 5), label)
        page.draw_rect(r)
        exp.append(("text", r))

    def check(label, x, y):
        r = pymupdf.Rect(x, y, x + 12, y + 12)
        page.draw_rect(r)
        text((x + 16, y + 10), label)
        exp.append(("checkbox", r))

    # rumore: titolo, intestazione con testo, tabella, sottolineatura
    text((72, 50), "Domanda di iscrizione", 16)
    page.draw_rect(pymupdf.Rect(60, 60, 535, 80), fill=(0.9, 0.9, 0.9))
    text((70, 74), "Sezione A - Dati anagrafici")
    text((72, 760), "Leggere attentamente l'informativa")
    page.draw_line((72, 762), (240, 762))
    for y in (700, 715, 730):
        page.draw_line((72, y), (372, y))
    for x in (72, 172, 272, 372):
        page.draw_line((x, 700), (x, 730))

    line("Nome:", 140, 110, 290)                    # 1
    line("Cognome:", 370, 110, 530, lx=310)          # 2 stessa riga
    line("Indirizzo:", 140, 140, 530)                # 3
    line("Citta:", 140, 170, 360)                    # 4
    line("CAP:", 400, 170, 530, lx=370)              # 5 stessa riga
    text((72, 192), "Codice fiscale:")               # 6 pettine
    cf = _comb_boxes(page, 160, 180, 16)
    exp.append(("text", cf))
    text((72, 222), "Partita IVA:")                  # 7 pettine a divisori
    piva = pymupdf.Rect(160, 210, 160 + 11 * 14, 226)
    page.draw_rect(piva)
    for i in range(1, 11):
        page.draw_line((160 + i * 14, 210), (160 + i * 14, 226))
    exp.append(("text", piva))
    box("Telefono", pymupdf.Rect(140, 240, 300, 260))  # 8
    box("Email", pymupdf.Rect(370, 240, 530, 260))     # 9 stessa riga
    text((72, 285), "Data di nascita: ____________")   # 10 trattini
    w = pymupdf.get_text_length("Data di nascita: ", fontsize=10)
    w2 = pymupdf.get_text_length("____________", fontsize=10)
    exp.append(("text", pymupdf.Rect(72 + w, 271, 72 + w + w2, 287)))
    text((72, 315), "Note")                            # 11 multiriga
    page.draw_rect(pymupdf.Rect(72, 320, 530, 400))
    exp.append(("text", pymupdf.Rect(72, 320, 530, 400)))
    text((72, 425), "Descrizione dell'attivita")       # 12 multiriga
    page.draw_rect(pymupdf.Rect(72, 430, 300, 500))
    exp.append(("text", pymupdf.Rect(72, 430, 300, 500)))
    check("Privato", 72, 520)                          # 13
    check("Azienda", 160, 520)                         # 14
    check("Si", 72, 545)                               # 15
    check("No", 160, 545)                              # 16
    check("Accetto il trattamento dei dati", 72, 570)  # 17
    line("Luogo:", 140, 620, 290)                      # 18
    line("Data:", 370, 620, 530, lx=330)               # 19 stessa riga
    line("Firma:", 140, 660, 400)                      # 20
    return exp


def test_benchmark_vector_form():
    doc, page = _doc()
    exp = _benchmark_form(page)
    assert len(exp) == 20
    cands = detect_page(doc, 0)
    used: set[int] = set()
    matched = 0
    for kind, r in exp:
        best = max((i for i in range(len(cands)) if i not in used),
                   key=lambda i: _iou(cands[i].rect, r), default=None)
        if best is None or _iou(cands[best].rect, r) < 0.6:
            continue
        used.add(best)
        if cands[best].kind == kind:
            matched += 1
    spurious = len(cands) - len(used)
    assert matched >= 18, (matched, cands)
    assert spurious <= 2, [cands[i] for i in range(len(cands)) if i not in used]
    combs = sorted(c.comb for c in cands if c.comb)
    assert combs == [11, 16]


# -------------------------------------------------------------------- OCR


def test_page_needs_ocr():
    src, page = _doc()
    _draw_form(page)
    assert not page_needs_ocr(src, 0)          # testo presente
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
    doc = pymupdf.open()
    scan = doc.new_page(width=595, height=842)
    scan.insert_image(scan.rect, pixmap=pix)
    small = doc.new_page(width=595, height=842)
    small.insert_image(pymupdf.Rect(0, 0, 200, 200), pixmap=pix)
    doc.new_page()
    assert page_needs_ocr(doc, 0)
    assert not page_needs_ocr(doc, 1)          # immagine piccola
    assert not page_needs_ocr(doc, 2)          # pagina vuota
