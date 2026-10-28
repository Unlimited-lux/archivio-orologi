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


# ------------------------------------------------------ scansione inclinata


def test_skewed_scan_3_degrees():
    import io
    import math

    from PIL import Image

    src, page = _doc()
    lines = [(150, 120), (150, 200), (200, 280)]     # (x0, y), lunghe 250 pt
    for i, (x, y) in enumerate(lines):
        page.insert_text((60, y - 2), f"Campo {i}:", fontsize=10)
        page.draw_line((x, y), (x + 250, y))
    boxes = [pymupdf.Rect(150, 360, 162, 372), pymupdf.Rect(300, 360, 312, 372)]
    for b in boxes:
        page.draw_rect(b)
        page.insert_text((b.x1 + 4, b.y1 - 2), "Scelta", fontsize=10)
    pix = page.get_pixmap(dpi=200)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    im = im.rotate(3, expand=False, fillcolor="white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    doc = pymupdf.open()
    scan = doc.new_page(width=595, height=842)
    scan.insert_image(scan.rect, stream=buf.getvalue())

    # rotazione antioraria di 3° attorno al centro (y verso il basso)
    t = math.radians(3)
    cx, cy = 595 / 2, 842 / 2

    def rot(x, y):
        return (cx + (x - cx) * math.cos(t) + (y - cy) * math.sin(t),
                cy - (x - cx) * math.sin(t) + (y - cy) * math.cos(t))

    cands = detect_page(doc, 0, raster=True)
    texts = [c for c in cands if c.kind == "text"]
    checks = [c for c in cands if c.kind == "checkbox"]
    assert all(c.angle == pytest.approx(3, abs=0.5) for c in cands)
    for x, y in lines:
        # centro del campo (1 pt sopra la linea, alto 18) dopo la rotazione
        ex, ey = rot(x + 125, y - 10)
        best = min(texts, key=lambda c: math.hypot(
            (c.rect.x0 + c.rect.x1) / 2 - ex, (c.rect.y0 + c.rect.y1) / 2 - ey))
        mx = (best.rect.x0 + best.rect.x1) / 2
        my = (best.rect.y0 + best.rect.y1) / 2
        assert math.hypot(mx - ex, my - ey) <= 2, (best.rect, ex, ey)
        # bordo inferiore rispetto alla linea inclinata nello stesso punto x
        lx, ly = rot(x + 125, y)
        line_y = ly - (mx - lx) * math.tan(t)
        assert best.rect.y1 == pytest.approx(line_y - 1, abs=2)
        assert best.rect.width == pytest.approx(250, abs=4)
    for b in boxes:
        ex, ey = rot((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
        assert any(math.hypot((c.rect.x0 + c.rect.x1) / 2 - ex,
                              (c.rect.y0 + c.rect.y1) / 2 - ey) <= 3
                   for c in checks), (checks, ex, ey)


def _skewed_scan(angle: float, dpi: int = 200, extra=None) -> pymupdf.Document:
    """Scansione simulata del modulo di prova, ruotata di `angle` gradi (antioraria)."""
    import io

    from PIL import Image
    src, page = _doc()
    for i, y in enumerate((120, 200, 280)):
        page.insert_text((60, y - 2), f"Campo {i}:", fontsize=10)
        page.draw_line((150, y), (400, y))
    if extra is not None:
        extra(page)
    pix = page.get_pixmap(dpi=dpi)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if angle:
        im = im.rotate(angle, expand=False, fillcolor="white")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buf.getvalue())
    return doc


def test_scan_quality_and_straighten():
    """FRM-27: inclinazione oltre 5° stimata e raddrizzata; dopo, le linee sono orizzontali."""
    from foliant.modules.forms.detect import estimate_skew, scan_quality, straighten_page
    doc = _skewed_scan(7)
    q = scan_quality(doc, 0)
    assert q["skew"] == pytest.approx(7, abs=0.5) and q["dpi"] == pytest.approx(200, abs=2)
    straighten_page(doc, 0, q["skew"])
    assert abs(estimate_skew(doc[0])) < 0.5
    texts = [c for c in detect_page(doc, 0, raster=True) if c.kind == "text"]
    assert len(texts) >= 3
    for y in (120, 200, 280):   # i campi tornano sulle linee originali
        assert any(abs(c.rect.y1 - (y - 1)) <= 3 and c.rect.x0 == pytest.approx(150, abs=4) for c in texts), y


def test_low_resolution_reported():
    from foliant.modules.forms.detect import scan_quality
    assert scan_quality(_skewed_scan(0, dpi=100), 0)["dpi"] == pytest.approx(100, abs=2)


def test_handwritten_boxes_proposed_with_note():
    """Caselle già spuntate e riquadri con scrittura a mano: proposti con «contenuto presente»."""
    def extra(page):
        box = pymupdf.Rect(150, 360, 164, 374)
        page.draw_rect(box)
        page.draw_line(box.tl + (3, 3), box.br - (3, 3), width=1.5)   # «X» a mano
        page.draw_line(box.bl + (3, -3), box.tr + (-3, 3), width=1.5)
        empty = pymupdf.Rect(300, 360, 314, 374)
        page.draw_rect(empty)
        note = pymupdf.Rect(150, 420, 450, 470)
        page.draw_rect(note)
        page.draw_bezier((170, 450), (200, 430), (230, 460), (260, 440), width=1.2)   # scarabocchio
    doc = _skewed_scan(0, extra=extra)
    cands = detect_page(doc, 0, raster=True)
    checks = sorted((c for c in cands if c.kind == "checkbox"), key=lambda c: c.rect.x0)
    assert len(checks) == 2
    assert checks[0].note == "contenuto presente" and checks[1].note == ""
    boxed = [c for c in cands if c.kind == "text" and c.rect.y0 > 400]
    assert boxed and boxed[0].note == "contenuto presente"


def test_scan_300dpi_fifteen_fields():
    """FRM-27: scansione a 300 dpi di un modulo con 15 campi annotati (righe, riquadri, caselle)
    → almeno 12 proposti con il tipo giusto e intersezione/unione ≥ 0,5. Tipi e posizioni non
    dipendono dall'OCR, che serve solo ai nomi."""
    src, page = _doc()
    truth = []
    for i in range(6):                      # righe con etichetta: campo sopra la linea
        y = 90 + 40 * i
        page.insert_text((60, y - 2), f"Voce {i}:", fontsize=10)
        page.draw_line((150, y), (420, y))
        truth.append(("text", pymupdf.Rect(150, y - 18, 420, y - 1)))
    for i in range(4):                      # riquadri di testo
        r = pymupdf.Rect(60 + 130 * i, 360, 180 + 130 * i, 380)
        page.draw_rect(r)
        truth.append(("text", r))
    for i in range(5):                      # caselle
        r = pymupdf.Rect(60 + 90 * i, 430, 72 + 90 * i, 442)
        page.draw_rect(r)
        page.insert_text((r.x1 + 4, r.y1 - 2), "Sì", fontsize=10)
        truth.append(("checkbox", r))
    pix = page.get_pixmap(dpi=300)
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_image(pymupdf.Rect(0, 0, 595, 842), pixmap=pix)
    cands = detect_page(doc, 0, raster=True)

    def iou(a, b):
        inter = abs(a & b)
        return inter / (abs(a) + abs(b) - inter)

    found = sum(any(c.kind == kind and iou(c.rect, r) >= 0.5 for c in cands) for kind, r in truth)
    assert found >= 12, found
