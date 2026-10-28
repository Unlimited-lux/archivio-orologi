"""Posizionamento esatto dei contenuti inseriti (foliant.core.placement).

Per ogni geometria di pagina (rotazione 0/90/180/270 × CropBox assente, uguale
al MediaBox, rientrato su un lato, su due, su tutti o più grande del MediaBox ×
MediaBox con origine nulla, positiva, negativa) si inserisce con le funzioni di
placement e si rilegge con le funzioni di lettura del motore: il contenuto deve
cadere dove richiesto (±1 pt). I valori attesi sono calcolati qui in modo
indipendente dal modulo.
"""

from __future__ import annotations

import logging
import re

import pymupdf
import os

import pytest
from pymupdf import Matrix, Point, Quad, Rect

from foliant.core import placement
from foliant.core.placement import PlacementError

TOL = 1.0   # tolleranza richiesta, in punti

MEDIABOXES = {
    "mb0": (0, 0, 595, 842),
    "mb+": (10, 20, 605, 862),
    "mb-": (-100, -50, 495, 792),
}
# Rientri (sinistra, basso, destra, alto) dal MediaBox, in punti PDF. None = nessun /CropBox.
CROPS = {
    "nocrop": None,
    "crop=mb": (0, 0, 0, 0),
    "sx": (50, 0, 0, 0),
    "dx": (0, 0, 45, 0),
    "basso": (0, 40, 0, 0),
    "alto": (0, 0, 0, 42),
    "sx+basso": (50, 40, 0, 0),
    "tutti": (50, 42, 45, 30),
    "tutti2": (30, 60, 20, 10),
    "fuori": (-20, -30, -25, -15),      # /CropBox più grande del MediaBox (file reali)
}
ROTATIONS = (0, 90, 180, 270)

# Matrice completa (120 geometrie, ~5000 casi) con FOLIANT_FULL_PLACEMENT=1; per impostazione
# predefinita un sottoinsieme rappresentativo, così la suite resta rapida per tutti.
_FULL = os.environ.get("FOLIANT_FULL_PLACEMENT") == "1"
_QUICK_CROPS = ("nocrop", "tutti", "fuori")
_QUICK_MB = ("mb0", "mb+")
GEOMETRIES = [
    pytest.param((mb, crop, rot), id=f"{mbn}-{cn}-r{rot}")
    for mbn, mb in MEDIABOXES.items()
    for cn, crop in CROPS.items()
    for rot in ROTATIONS
    if _FULL or (mbn in _QUICK_MB and cn in _QUICK_CROPS)
]
# Sottoinsieme per i test secondari: i casi più ostici.
HARD = [
    pytest.param((MEDIABOXES["mb0"], None, 0), id="semplice"),
    pytest.param((MEDIABOXES["mb+"], CROPS["tutti"], 90), id="mb+-tutti-r90"),
    pytest.param((MEDIABOXES["mb-"], CROPS["tutti2"], 180), id="mb--tutti2-r180"),
    pytest.param((MEDIABOXES["mb0"], CROPS["sx+basso"], 270), id="mb0-sx+basso-r270"),
    pytest.param((MEDIABOXES["mb+"], CROPS["alto"], 0), id="mb+-alto-r0"),
    pytest.param((MEDIABOXES["mb-"], CROPS["fuori"], 90), id="mb--fuori-r90"),
]


# ------------------------------------------------------------------ strumenti
def crop_box(mediabox, crop):
    """/CropBox scritto nel file (può uscire dal MediaBox)."""
    return (mediabox[0] + crop[0], mediabox[1] + crop[1], mediabox[2] - crop[2], mediabox[3] - crop[3])


def visible_box(mediabox, crop):
    """Area visibile: /CropBox ∩ /MediaBox."""
    if crop is None:
        return tuple(mediabox)
    c = crop_box(mediabox, crop)
    return (max(c[0], mediabox[0]), max(c[1], mediabox[1]), min(c[2], mediabox[2]), min(c[3], mediabox[3]))


def fmt(box):
    return "[" + " ".join(f"{v:g}" for v in box) + "]"


def make_page(geom, doc=None):
    """Pagina con MediaBox, CropBox e Rotate scritti tali e quali nel file."""
    mediabox, crop, rotation = geom
    doc = doc if doc is not None else pymupdf.open()
    page = doc.new_page(width=mediabox[2] - mediabox[0], height=mediabox[3] - mediabox[1])
    doc.xref_set_key(page.xref, "MediaBox", fmt(mediabox))
    if crop is not None:
        doc.xref_set_key(page.xref, "CropBox", fmt(crop_box(mediabox, crop)))
    if rotation:
        doc.xref_set_key(page.xref, "Rotate", str(rotation))
    return doc.reload_page(page)


def std_size(geom):
    v = visible_box(geom[0], geom[1])
    return v[2] - v[0], v[3] - v[1]


def std_to_pdf(geom):
    """Coordinate standard -> spazio utente PDF (definizione delle coordinate standard)."""
    v = visible_box(geom[0], geom[1])
    return Matrix(1, 0, 0, -1, v[0], v[3])


def std_to_display(geom):
    """Coordinate standard -> pagina visualizzata (ruotata in senso orario di /Rotate)."""
    w, h = std_size(geom)
    return {0: Matrix(1, 0, 0, 1, 0, 0), 90: Matrix(0, 1, -1, 0, h, 0),
            180: Matrix(-1, 0, 0, -1, w, h), 270: Matrix(0, -1, 1, 0, 0, w)}[geom[2]]


def reference_page(geom):
    """Pagina semplice con le stesse dimensioni standard: lì il motore è esatto."""
    w, h = std_size(geom)
    doc = pymupdf.open()
    return doc.new_page(width=w, height=h)


def direction(vector, matrix):
    """Trasforma un vettore (solo parte lineare) e lo normalizza."""
    v = Point(vector.x * matrix.a + vector.y * matrix.c, vector.x * matrix.b + vector.y * matrix.d)
    return v / abs(v)


FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_MEDIABOX_CLIP


def chars(page):
    """[(carattere, origine, direzione della riga, bbox)] letti dal motore."""
    out = []
    data = page.get_text("rawdict", clip=pymupdf.INFINITE_RECT(), flags=FLAGS)
    for block in data["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    out.append((ch["c"], Point(ch["origin"]), Point(line["dir"]), Rect(ch["bbox"])))
    return out


def words(page):
    return [(w[4], Rect(w[:4])) for w in page.get_text("words", clip=pymupdf.INFINITE_RECT(), flags=FLAGS)]


def close(a, b, tol=TOL):
    if isinstance(a, Rect) or isinstance(b, Rect):
        a, b = Rect(a), Rect(b)
        return all(abs(x - y) <= tol for x, y in zip(a, b))
    a, b = Point(a), Point(b)
    return abs(a.x - b.x) <= tol and abs(a.y - b.y) <= tol


def assert_close(got, want, what="", tol=TOL):
    assert close(got, want, tol), f"{what}: letto {got}, atteso {want}"


def drawings_by_fill(page):
    out = {}
    for d in page.get_drawings():
        if d.get("fill") is not None:
            out.setdefault(tuple(round(c, 2) for c in d["fill"]), []).append(d)
    return out


@pytest.fixture(autouse=True)
def _fresh_cache():
    placement.clear_cache()
    yield
    placement.clear_cache()


@pytest.fixture(params=GEOMETRIES)
def geom(request):
    return request.param


@pytest.fixture(params=HARD)
def hard_geom(request):
    return request.param


R = Rect(150, 300, 190, 330)          # rettangolo richiesto (coordinate standard)
P1, P2 = Point(120, 260), Point(220, 280)
C = Point(300, 400)
TP = Point(100, 200)
LOREM = "Alfa beta gamma delta epsilon zeta eta theta iota kappa lambda mi ni xi omicron pi"


# ---------------------------------------------------- definizione delle coordinate
def test_standard_coordinates_match_engine_reading(geom):
    """Contenuto scritto direttamente in PDF: il motore lo rilegge nelle coordinate standard attese."""
    page = make_page(geom)
    doc = page.parent
    pdf_rect = R * std_to_pdf(geom)
    origin = TP * std_to_pdf(geom)
    font = doc.get_new_xref()
    doc.update_object(font, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    doc.xref_set_key(page.xref, "Resources", f"<</Font<</F1 {font} 0 R>>>>")
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    box = f"{pdf_rect.x0:g} {pdf_rect.y0:g} {pdf_rect.width:g} {pdf_rect.height:g}"
    doc.update_stream(stream, (f"q 1 0 0 rg {box} re f Q "
                               f"BT /F1 12 Tf 1 0 0 1 {origin.x:g} {origin.y:g} Tm (Hxy) Tj ET").encode())
    doc.xref_set_key(page.xref, "Contents", f"{stream} 0 R")
    page = doc.reload_page(page)
    assert_close(page.get_drawings()[0]["rect"], R, "get_drawings")
    assert_close(chars(page)[0][1], TP, "get_text")
    m = placement.page_to_pdf_matrix(page)
    assert all(abs(x - y) < 1e-6 for x, y in zip(m, std_to_pdf(geom)))
    d = placement.page_to_display_matrix(page)
    assert all(abs(x - y) < 1e-6 for x, y in zip(d, std_to_display(geom)))
    assert placement.page_rect(page) == Rect(0, 0, *std_size(geom))


# ------------------------------------------------------------------- disegno
def test_draw_rect(geom):
    page = make_page(geom)
    placement.draw_rect(page, R, color=None, fill=(1, 0, 0))
    assert_close(page.get_drawings()[0]["rect"], R, "draw_rect")


def test_draw_line(geom):
    page = make_page(geom)
    last = placement.draw_line(page, P1, P2, color=(0, 0, 1), width=1)
    item = page.get_drawings()[0]["items"][0]
    assert item[0] == "l"
    assert_close(item[1], P1, "p1")
    assert_close(item[2], P2, "p2")
    assert_close(last, P2, "punto restituito")


def test_draw_circle(geom):
    page = make_page(geom)
    placement.draw_circle(page, C, 20, color=None, fill=(0, 1, 0))
    assert_close(page.get_drawings()[0]["rect"], Rect(C.x - 20, C.y - 20, C.x + 20, C.y + 20), "draw_circle")


DRAW_CASES = {
    "polyline": ("draw_polyline", ([P1, P2, C],), {}),
    "oval-rect": ("draw_oval", (R,), {}),
    "oval-quad": ("draw_oval", (Quad(P1, P2, Point(130, 330), Point(240, 350)),), {}),
    "quad": ("draw_quad", (Quad(P1, P2, Point(130, 330), Point(240, 350)),), {}),
    "bezier": ("draw_bezier", (P1, Point(150, 200), Point(250, 380), P2), {}),
    "curve": ("draw_curve", (P1, Point(200, 200), P2), {}),
    "sector": ("draw_sector", (C, Point(340, 400), 60), {}),
    "squiggle": ("draw_squiggle", (P1, P2), {}),
    "zigzag": ("draw_zigzag", (P1, P2), {}),
    "rect-rounded": ("draw_rect", (R,), {"radius": 0.2}),
}


@pytest.mark.parametrize("case", list(DRAW_CASES), ids=list(DRAW_CASES))
def test_other_draw_functions(geom, case):
    """Ogni primitiva coincide con la stessa disegnata dal motore su una pagina semplice."""
    method, args, kw = DRAW_CASES[case]
    page = make_page(geom)
    ref = reference_page(geom)
    got_last = getattr(placement, method)(page, *args, color=(0, 0, 0), **kw)
    ref_last = getattr(ref, method)(*args, color=(0, 0, 0), **kw)
    got = [d["rect"] for d in page.get_drawings()]
    want = [d["rect"] for d in ref.get_drawings()]
    assert len(got) == len(want) == 1
    assert_close(got[0], want[0], method)
    assert_close(got_last, ref_last, "punto restituito")


def test_new_shape(geom):
    page = make_page(geom)
    shapes = [placement.new_shape(page), reference_page(geom).new_shape()]
    lasts = []
    for shape in shapes:
        shape.draw_rect(R)
        shape.draw_polyline([P1, P2, C])
        lasts.append((shape.last_point, shape.rect))
        shape.finish(color=(0, 0, 0), fill=None, closePath=False)
        shape.draw_circle(Point(400, 500), 15)
        shape.finish(color=None, fill=(0, 0, 1))
        shape.insert_text(TP, "Forma", fontsize=12)
    shape, ref = shapes
    assert_close(lasts[0][0], lasts[1][0], "Shape.last_point")
    assert_close(lasts[0][1], lasts[1][1], "Shape.rect")
    assert_close(shape.rect, ref.rect, "Shape.rect finale")
    shape.commit()
    items = [it for d in page.get_drawings() for it in d["items"]]
    rects = [it[1] for it in items if it[0] == "re"]
    lines = [it for it in items if it[0] == "l"]
    assert_close(rects[0], R, "rect della forma")
    assert_close(lines[0][1], P1, "polilinea p1")
    assert_close(lines[0][2], P2, "polilinea p2")
    assert_close(lines[1][2], C, "polilinea p3")
    circle = drawings_by_fill(page)[(0.0, 0.0, 1.0)][0]["rect"]
    assert_close(circle, Rect(385, 485, 415, 515), "cerchio della forma")
    assert_close(chars(page)[0][1], TP, "testo della forma")


def test_morph(geom):
    """Il punto fisso di ``morph`` è in coordinate standard (disegni, forme, testo)."""
    page = make_page(geom)
    ref = reference_page(geom)
    fix = Point(250, 450)
    # ingrandimento 2× attorno a un punto lontano: q = fix + 2 (p - fix)
    scaled = lambda p: fix + (Point(p) - fix) * 2  # noqa: E731
    placement.draw_rect(page, R, color=None, fill=(1, 0, 0), morph=(fix, Matrix(2, 2)))
    shape = placement.new_shape(page)
    shape.draw_rect(Rect(300, 100, 340, 130))
    shape.finish(color=None, fill=(0, 1, 0), morph=(Point(320, 115), Matrix(90)))
    shape.commit()
    placement.insert_text(page, TP, "Hxy", fontsize=12, morph=(fix, Matrix(2, 2)))
    box = Rect(80, 600, 260, 700)
    placement.insert_textbox(page, box, "Kappa", fontsize=11, morph=(fix, Matrix(2, 2)))
    ref.insert_textbox(box, "Kappa", fontsize=11, morph=(fix, Matrix(2, 2)))
    fills = drawings_by_fill(page)
    assert_close(fills[(1.0, 0.0, 0.0)][0]["rect"], Rect(scaled(R.tl), scaled(R.br)), "draw_rect con morph")
    assert_close(fills[(0.0, 1.0, 0.0)][0]["rect"], Rect(305, 95, 335, 135), "Shape.finish con morph")
    found = {c: o for c, o, _, _ in chars(page)}
    assert_close(found["H"], scaled(TP), "insert_text con morph")
    assert_close(found["K"], {c: o for c, o, _, _ in chars(ref)}["K"], "insert_textbox con morph")


def test_underlay(hard_geom):
    page = make_page(hard_geom)
    placement.draw_rect(page, R, color=None, fill=(1, 0, 0), overlay=False)
    placement.write_text(page, [(TP, "Hxy")], overlay=False)
    placement.insert_image(page, Rect(300, 100, 340, 130), pixmap=pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 4, 3), False),
                           overlay=False)
    assert_close(page.get_drawings()[0]["rect"], R, "draw_rect sotto")
    assert_close(chars(page)[0][1], TP, "write_text sotto")
    assert_close(Rect(page.get_image_info()[0]["bbox"]), Rect(300, 100, 340, 130), "insert_image sotto")


# ------------------------------------------------------------------ immagini
@pytest.fixture
def pixmap():
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False)
    pix.clear_with(255)
    pix.set_rect(pymupdf.IRect(0, 0, 10, 10), (0, 0, 0))
    return pix


@pytest.fixture
def png(tmp_path, pixmap):
    path = tmp_path / "sonda.png"
    pixmap.save(str(path))
    return str(path)


def image_axes(page, geom, display=False):
    """Direzioni degli assi x e y dell'immagine (in coordinate standard o visualizzate)."""
    t = Matrix(page.get_image_info()[0]["transform"])
    m = std_to_display(geom) if display else Matrix(1, 0, 0, 1, 0, 0)
    return direction(Point(t.a, t.b), m), direction(Point(t.c, t.d), m)


@pytest.mark.parametrize("source", ["file", "pixmap"])
def test_insert_image(geom, source, png, pixmap):
    page = make_page(geom)
    kw = {"filename": png} if source == "file" else {"pixmap": pixmap}
    placement.insert_image(page, R, **kw)
    assert_close(Rect(page.get_image_info()[0]["bbox"]), R, "insert_image")
    x_axis, y_axis = image_axes(page, geom)
    assert close(x_axis, Point(1, 0), 1e-3) and close(y_axis, Point(0, 1), 1e-3), "immagine ruotata"


def test_insert_image_upright(geom, pixmap):
    page = make_page(geom)
    rect = Rect(150, 300, 190, 330) if geom[2] in (0, 180) else Rect(150, 300, 180, 340)
    placement.insert_image(page, rect, pixmap=pixmap, upright=True)
    assert_close(Rect(page.get_image_info()[0]["bbox"]), rect, "insert_image upright")
    x_axis, y_axis = image_axes(page, geom, display=True)
    assert close(x_axis, Point(1, 0), 1e-3) and close(y_axis, Point(0, 1), 1e-3), "immagine non diritta a schermo"


# --------------------------------------------------------------------- testo
def test_insert_text(geom):
    page = make_page(geom)
    placement.insert_text(page, TP, "Hxy", fontsize=12)
    c, origin, line_dir, _ = chars(page)[0]
    assert c == "H"
    assert_close(origin, TP, "insert_text")
    assert close(line_dir, Point(1, 0), 1e-3)


def test_insert_text_upright(geom):
    page = make_page(geom)
    placement.insert_text(page, TP, "Hxy", fontsize=12, upright=True)
    c, origin, line_dir, _ = chars(page)[0]
    assert_close(origin, TP, "insert_text upright")
    assert close(direction(line_dir, std_to_display(geom)), Point(1, 0), 1e-3), "testo non diritto a schermo"


@pytest.mark.parametrize("upright", [False, True], ids=["allineato", "diritto"])
def test_insert_textbox(geom, upright):
    page = make_page(geom)
    ref = reference_page(geom)
    box = Rect(100, 150, 300, 330)
    rotate = geom[2] if upright else 0
    rc = placement.insert_textbox(page, box, LOREM, fontsize=11, upright=upright)
    ref_rc = ref.insert_textbox(box, LOREM, fontsize=11, rotate=rotate)
    assert rc == pytest.approx(ref_rc) and rc >= 0
    got, want = chars(page), chars(ref)
    assert len(got) == len(want) > 20
    for (c, origin, line_dir, _), (rc_, rorigin, _, _) in zip(got, want):
        assert c == rc_
        assert_close(origin, rorigin, f"insert_textbox «{c}»")
        assert box.x0 - TOL <= origin.x <= box.x1 + TOL and box.y0 - TOL <= origin.y <= box.y1 + TOL
    if upright:
        assert close(direction(got[0][2], std_to_display(geom)), Point(1, 0), 1e-3)


HTML = "<p>Alfa beta <b>gamma</b> delta</p><p style='text-align:right'>Epsilon zeta</p><p>eta theta iota</p>"


@pytest.mark.parametrize("upright", [False, True], ids=["allineato", "diritto"])
def test_insert_htmlbox(geom, upright):
    page = make_page(geom)
    ref = reference_page(geom)
    box = Rect(100, 150, 300, 330)
    spare, scale = placement.insert_htmlbox(page, box, HTML, upright=upright)
    ref_spare, ref_scale = ref.insert_htmlbox(box, HTML, rotate=geom[2] if upright else 0)
    assert spare >= 0 and spare == pytest.approx(ref_spare) and scale == pytest.approx(ref_scale)
    got, want = words(page), words(ref)
    assert [w for w, _ in got] == [w for w, _ in want] and len(got) == 9
    for (word, bbox), (_, rbbox) in zip(got, want):
        assert_close(bbox, rbbox, f"insert_htmlbox «{word}»")
        assert bbox.intersects(box) and box.contains(_center(bbox))
    if upright:
        assert close(direction(chars(page)[0][2], std_to_display(geom)), Point(1, 0), 1e-3)


def _center(r):
    return Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def test_insert_htmlbox_links(geom):
    """I collegamenti dell'HTML finiscono sulla parola a cui appartengono."""
    html = "<p>Vedi <a href='https://example.org/'>sito</a> esterno</p>"
    box = Rect(100, 150, 300, 330)
    page = make_page(geom)
    ref = reference_page(geom)
    placement.insert_htmlbox(page, box, html, upright=True)
    ref.insert_htmlbox(box, html, rotate=geom[2])
    page = page.parent.reload_page(page)        # il motore rilegge i collegamenti solo così
    ref = ref.parent.reload_page(ref)
    links = placement.get_links(page)
    assert len(links) == 1
    assert_close(links[0]["from"], ref.get_links()[0]["from"], "collegamento HTML")
    word = [b for w, b in words(page) if w == "sito"][0]
    assert word.contains(_center(links[0]["from"]))


# ----------------------------------------------------------------- TextWriter
def test_textwriter_append(geom):
    page = make_page(geom)
    writer = pymupdf.TextWriter(page.rect)
    writer.append(TP, "Hxy", fontsize=12)
    writer.append(Point(300, 500), "Kz", fontsize=9)
    placement.write_text(page, writer)
    found = {c: o for c, o, _, _ in chars(page)}
    assert_close(found["H"], TP, "TextWriter.append")
    assert_close(found["K"], Point(300, 500), "TextWriter.append (2)")


@pytest.mark.parametrize("upright", [False, True], ids=["allineato", "diritto"])
def test_textwriter_items(geom, upright):
    page = make_page(geom)
    placement.write_text(page, [(TP, "Uno"), {"pos": Point(300, 500), "text": "Due", "fontsize": 9}],
                         upright=upright, color=(1, 0, 0))
    found = {c: (o, d) for c, o, d, _ in chars(page)}
    assert_close(found["U"][0], TP, "testo 1")
    assert_close(found["D"][0], Point(300, 500), "testo 2")
    expected_dir = Point(1, 0)
    for _, line_dir in found.values():
        d = direction(line_dir, std_to_display(geom)) if upright else line_dir
        assert close(d, expected_dir, 1e-3)


@pytest.mark.parametrize("upright", [False, True], ids=["allineato", "diritto"])
def test_fill_textbox(geom, upright):
    page = make_page(geom)
    box = Rect(100, 150, 300, 330)
    rest = placement.fill_textbox(page, box, LOREM, fontsize=11, upright=upright)
    assert not rest
    # riferimento: stesso testo composto dal motore su una pagina semplice; se
    # diritto, la pagina semplice ha le dimensioni della pagina visualizzata
    view = std_to_display(geom) if upright else Matrix(1, 0, 0, 1, 0, 0)
    w, h = std_size(geom)
    size = Rect(0, 0, w, h) * view
    ref = pymupdf.open().new_page(width=size.width, height=size.height)
    writer = pymupdf.TextWriter(ref.rect)
    writer.fill_textbox(box * view, LOREM, fontsize=11)
    writer.write_text(ref)
    got, want = chars(page), chars(ref)
    assert len(got) == len(want) > 20
    for (c, origin, line_dir, _), (rc, rorigin, _, _) in zip(got, want):
        assert c == rc
        assert_close(origin, rorigin * ~view, f"fill_textbox «{c}»")
        assert box.x0 - TOL <= origin.x <= box.x1 + TOL and box.y0 - TOL <= origin.y <= box.y1 + TOL
    if upright:
        assert close(direction(got[0][2], std_to_display(geom)), Point(1, 0), 1e-3)


def test_textwriter_on_unbalanced_content(hard_geom):
    """Una «cm» lasciata aperta dal contenuto esistente non sposta il testo."""
    page = make_page(hard_geom)
    doc = page.parent
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, b"2 0 0 -2 30 900 cm 0 g 10 10 20 20 re f")
    doc.xref_set_key(page.xref, "Contents", f"{stream} 0 R")
    page = doc.reload_page(page)
    writer = pymupdf.TextWriter(page.rect)
    writer.append(TP, "Hxy", fontsize=12)
    placement.write_text(page, writer)
    placement.insert_text(page, Point(300, 500), "Kz", fontsize=12)
    found = {c: (o, d) for c, o, d, _ in chars(page)}
    assert_close(found["H"][0], TP, "TextWriter")
    assert close(found["H"][1], Point(1, 0), 1e-3)
    assert_close(found["K"][0], Point(300, 500), "insert_text")


# ----------------------------------------------------------- show_pdf_page
def source_document(geom):
    """PDF di una pagina: area visibile grigia, quadrato nero in alto a sinistra e
    rosso in basso a destra (coordinate standard della sorgente), blu fuori dal ritaglio."""
    page = make_page(geom)
    doc = page.parent
    w, h = std_size(geom)
    m = std_to_pdf(geom)
    ops = []
    for color, rect in (("0.5 g", Rect(0, 0, w, h)), ("0 g", Rect(0, 0, 10, 10)),
                        ("1 0 0 rg", Rect(w - 10, h - 10, w, h)), ("0 0 1 rg", Rect(-40, -40, -30, -30))):
        r = rect * m
        ops.append(f"{color} {r.x0:g} {r.y0:g} {r.width:g} {r.height:g} re f")
    stream = doc.get_new_xref()
    doc.update_object(stream, "<<>>")
    doc.update_stream(stream, ("q " + " ".join(ops) + " Q").encode())
    doc.xref_set_key(page.xref, "Contents", f"{stream} 0 R")
    return pymupdf.open("pdf", doc.tobytes())


SOURCES = [
    pytest.param((MEDIABOXES[mbn], CROPS[cn], rot), id=f"src-{mbn}-{cn}-r{rot}")
    for mbn in ("mb0", "mb+") for cn in ("nocrop", "tutti") for rot in ROTATIONS
]


def check_shown_page(page, target, geom, src_geom, *, as_displayed, upright):
    """Verifica posizione e orientamento della pagina sorgente mostrata in ``target``."""
    sw, sh = std_size(src_geom)
    src_rot = src_geom[2] if as_displayed else 0
    src_view = std_to_display((src_geom[0], src_geom[1], src_rot))
    dw, dh = (sw, sh) if src_rot in (0, 180) else (sh, sw)
    view = std_to_display(geom) if upright else Matrix(1, 0, 0, 1, 0, 0)
    fills = drawings_by_fill(page)
    shown = Rect(target) * view
    assert_close(fills[(0.5, 0.5, 0.5)][0]["rect"] * view, shown, "area della pagina mostrata")
    scale = shown.width / dw
    assert shown.height == pytest.approx(dh * scale, abs=TOL)
    for color, corner in (((0.0, 0.0, 0.0), Rect(0, 0, 10, 10)), ((1.0, 0.0, 0.0), Rect(sw - 10, sh - 10, sw, sh))):
        expected = _center(corner * src_view)
        expected = Point(shown.x0 + expected.x * scale, shown.y0 + expected.y * scale)
        assert_close(_center(fills[color][0]["rect"] * view), expected, f"segno {color}")


def target_rect(geom, src_geom, *, as_displayed, upright, scale=0.25):
    """Rettangolo standard con le proporzioni che la sorgente avrà sulla pagina."""
    sw, sh = std_size(src_geom)
    src_rot = src_geom[2] if as_displayed else 0
    dw, dh = (sw, sh) if src_rot in (0, 180) else (sh, sw)
    if upright and geom[2] in (90, 270):
        dw, dh = dh, dw
    return Rect(60, 80, 60 + dw * scale, 80 + dh * scale)


def test_show_pdf_page(geom):
    src_geom = ((0, 0, 400, 200), None, 0)
    src = source_document(src_geom)
    page = make_page(geom)
    target = target_rect(geom, src_geom, as_displayed=True, upright=False)
    placement.show_pdf_page(page, target, src, 0)
    check_shown_page(page, target, geom, src_geom, as_displayed=True, upright=False)


def test_show_pdf_page_upright(geom):
    src_geom = ((0, 0, 400, 200), None, 0)
    src = source_document(src_geom)
    page = make_page(geom)
    target = target_rect(geom, src_geom, as_displayed=True, upright=True)
    placement.show_pdf_page(page, target, src, 0, upright=True)
    check_shown_page(page, target, geom, src_geom, as_displayed=True, upright=True)


@pytest.mark.parametrize("src_geom", SOURCES)
@pytest.mark.parametrize("as_displayed", [True, False], ids=["come-visualizzata", "non-ruotata"])
@pytest.mark.parametrize("upright", [False, True], ids=["allineato", "diritto"])
def test_show_pdf_page_source_geometry(hard_geom, src_geom, as_displayed, upright):
    """Sorgenti ruotate, ritagliate o con MediaBox spostato; la sorgente resta invariata."""
    src = source_document(src_geom)
    before = src.xref_get_key(src[0].xref, "Rotate")
    page = make_page(hard_geom)
    target = target_rect(hard_geom, src_geom, as_displayed=as_displayed, upright=upright)
    placement.show_pdf_page(page, target, src, 0, as_displayed=as_displayed, upright=upright)
    check_shown_page(page, target, hard_geom, src_geom, as_displayed=as_displayed, upright=upright)
    assert src.xref_get_key(src[0].xref, "Rotate") == before


def test_show_pdf_page_clip(hard_geom):
    """``clip`` è in coordinate standard della sorgente, anche se ruotata."""
    src_geom = (MEDIABOXES["mb+"], CROPS["tutti"], 90)
    src = source_document(src_geom)
    page = make_page(hard_geom)
    target = Rect(60, 80, 100, 120)
    placement.show_pdf_page(page, target, src, 0, clip=Rect(0, 0, 20, 20), as_displayed=False)
    black = drawings_by_fill(page)[(0.0, 0.0, 0.0)][0]["rect"]
    assert_close(black, Rect(60, 80, 80, 100), "angolo ritagliato")


# --------------------------------------------------------------- collegamenti
def link_rect_from_pdf(page, geom):
    xref = [a[0] for a in page.annot_xrefs() if a[1] == pymupdf.PDF_ANNOT_LINK][0]
    nums = [float(v) for v in re.findall(r"[-\d.]+", page.parent.xref_get_key(xref, "Rect")[1])]
    return Rect(nums) * ~std_to_pdf(geom)


def test_insert_link(geom):
    page = make_page(geom)
    placement.insert_link(page, {"kind": pymupdf.LINK_URI, "from": R, "uri": "https://example.org/"})
    page = page.parent.reload_page(page)
    assert_close(link_rect_from_pdf(page, geom), R, "/Rect del collegamento")
    links = placement.get_links(page)
    assert len(links) == 1 and links[0]["uri"] == "https://example.org/"
    assert_close(links[0]["from"], R, "get_links (placement)")
    # il motore legge in coordinate della pagina visualizzata
    assert_close(page.get_links()[0]["from"], R * std_to_display(geom), "get_links (motore)")


def test_insert_link_goto(geom):
    doc = pymupdf.open()
    doc.new_page(width=300, height=300)
    make_page(geom, doc)
    start = doc[0]
    placement.insert_link(start, {"kind": pymupdf.LINK_GOTO, "from": Rect(10, 10, 60, 30), "page": 1, "to": TP})
    start = doc.reload_page(start)
    xref = [a[0] for a in start.annot_xrefs()][0]
    m = re.search(r"/XYZ\s*([-\d.]+)\s+([-\d.]+)", doc.xref_object(xref, compressed=True))
    assert_close(Point(float(m.group(1)), float(m.group(2))) * ~std_to_pdf(geom), TP, "destinazione nel file")
    link = placement.get_links(start)[0]
    assert link["page"] == 1
    assert_close(link["to"], TP, "destinazione riletta")
    assert_close(link["from"], Rect(10, 10, 60, 30), "area del collegamento")


def test_update_link(hard_geom):
    page = make_page(hard_geom)
    placement.insert_link(page, {"kind": pymupdf.LINK_URI, "from": R, "uri": "https://example.org/"})
    page = page.parent.reload_page(page)
    link = placement.get_links(page)[0]
    link["from"] = link["from"] + (10, 20, 10, 20)
    placement.update_link(page, link)
    page = page.parent.reload_page(page)
    assert_close(placement.get_links(page)[0]["from"], R + (10, 20, 10, 20), "collegamento spostato")


# ------------------------------------------------------------------ redazione
GRID = {f"w{i}{j}": Point(100 + 60 * j, 300 + 40 * i) for i in range(3) for j in range(3)}
REDACT = Rect(150, 320, 290, 395)       # copre interamente w11 w12 w21 w22, lontano dalle altre


@pytest.mark.parametrize("shape", ["rect", "quad"])
def test_redaction(geom, shape):
    page = make_page(geom)
    for word, origin in GRID.items():
        placement.insert_text(page, origin, word, fontsize=10)
    before = {w: b for w, b in words(page)}
    assert set(before) == set(GRID)
    area = REDACT if shape == "rect" else REDACT.quad
    annot = placement.add_redact_annot(page, area, fill=(0, 0, 0))
    nums = [float(v) for v in re.findall(r"[-\d.]+", page.parent.xref_get_key(annot.xref, "Rect")[1])]
    assert_close(Rect(nums) * ~std_to_pdf(geom), REDACT, "/Rect dell'annotazione di redazione")
    page.apply_redactions()
    after = {w for w, _ in words(page)}
    gone = set(GRID) - after
    assert gone == {"w11", "w12", "w21", "w22"}
    assert all(REDACT.contains(before[w]) for w in gone)


# --------------------------------------------- calibrazione, cache, casi limite
def test_measurement_matches_formula(geom):
    """Per la versione attuale del motore le formule documentate coincidono con le misure."""
    page = make_page(geom)
    for kind in placement.KINDS:
        m = placement.insertion_matrix(page, kind)
        off = placement.formula_offset(page, kind)
        assert (m.a, m.b, m.c, m.d) == (1, 0, 0, 1)
        assert m.e == pytest.approx(-off.x, abs=0.01) and m.f == pytest.approx(-off.y, abs=0.01), kind


def test_measurement_is_cached(monkeypatch):
    calls = []
    original = placement._PROBES["shape"]
    monkeypatch.setitem(placement._PROBES, "shape", lambda g: calls.append(g) or original(g))
    geom_a = (MEDIABOXES["mb+"], CROPS["tutti"], 90)
    geom_b = (MEDIABOXES["mb0"], CROPS["sx"], 270)
    for geom in (geom_a, geom_a, geom_b, geom_a):
        page = make_page(geom)
        placement.draw_rect(page, R, color=None, fill=(1, 0, 0))
        assert_close(page.get_drawings()[0]["rect"], R)
    assert len(calls) == 2


def test_fallback_to_formula(monkeypatch, caplog):
    def broken(_g):
        raise RuntimeError("sonda guasta")
    monkeypatch.setitem(placement._PROBES, "shape", broken)
    page = make_page((MEDIABOXES["mb+"], CROPS["tutti"], 90))
    with caplog.at_level(logging.WARNING, logger="foliant.core.placement"):
        placement.draw_rect(page, R, color=None, fill=(1, 0, 0))
    assert "sonda guasta" in caplog.text
    assert_close(page.get_drawings()[0]["rect"], R, "con la formula")


def test_adapts_to_engine_fix(monkeypatch):
    """Se il motore correggesse la matrice di pagina, la misura si adegua (la formula no)."""
    geom = (MEDIABOXES["mb+"], CROPS["tutti"], 90)
    page = make_page(geom)
    stale = placement.formula_offset(page, "shape")
    monkeypatch.setattr(pymupdf.Page, "transformation_matrix",
                        property(lambda self: placement.pdf_to_page_matrix(self)))
    page = make_page(geom)
    placement.draw_rect(page, R, color=None, fill=(1, 0, 0))
    assert_close(page.get_drawings()[0]["rect"], R, "dopo la correzione del motore")
    m = placement.insertion_matrix(page, "shape")
    assert (m.e, m.f) == (0, 0) and abs(stale) > 10


def test_html_links_follow_link_correction(monkeypatch):
    """Se insert_link del motore avesse uno scarto diverso da insert_htmlbox, i
    collegamenti dell'HTML verrebbero comunque riportati sulla loro parola."""
    original = pymupdf.Page.insert_link

    def shifted(page, lnk, mark=True):
        lnk = dict(lnk)
        lnk["from"] = Rect(lnk["from"]) + (7, 5, 7, 5)
        return original(page, lnk, mark)

    monkeypatch.setattr(pymupdf.Page, "insert_link", shifted)
    page = make_page((MEDIABOXES["mb+"], CROPS["tutti"], 90))
    placement.insert_htmlbox(page, Rect(100, 150, 300, 330), "<p>Vedi <a href='https://example.org/'>sito</a></p>")
    placement.insert_link(page, {"kind": pymupdf.LINK_URI, "from": R, "uri": "https://example.org/x"})
    page = page.parent.reload_page(page)
    word = [b for w, b in words(page) if w == "sito"][0]
    links = placement.get_links(page)
    assert word.contains(_center(links[0]["from"])), f"collegamento HTML {links[0]['from']} fuori da {word}"
    assert_close(links[1]["from"], R, "insert_link")


def test_inherited_page_attributes():
    """MediaBox, CropBox e Rotate ereditati dal nodo /Pages."""
    geom = (MEDIABOXES["mb+"], CROPS["tutti"], 270)
    doc = pymupdf.open()
    page = doc.new_page()
    pages = int(doc.xref_get_key(page.xref, "Parent")[1].split()[0])
    doc.xref_set_key(pages, "MediaBox", fmt(geom[0]))
    doc.xref_set_key(pages, "CropBox", fmt(visible_box(geom[0], geom[1])))
    doc.xref_set_key(pages, "Rotate", "270")
    for key in ("MediaBox", "CropBox", "Rotate"):
        doc.xref_set_key(page.xref, key, "null")
    page = doc.reload_page(page)
    assert page.rotation == 270
    placement.draw_rect(page, R, color=None, fill=(1, 0, 0))
    placement.write_text(page, [(TP, "Hxy")])
    assert_close(page.get_drawings()[0]["rect"], R, "draw_rect")
    assert_close(chars(page)[0][1], TP, "write_text")


def test_unsupported_geometries():
    page = make_page((MEDIABOXES["mb0"], None, 0))
    page.parent.xref_set_key(page.xref, "UserUnit", "2")
    with pytest.raises(PlacementError):
        placement.draw_rect(page, R)
    page = make_page((MEDIABOXES["mb0"], None, 0))
    page.parent.xref_set_key(page.xref, "Rotate", "45")
    with pytest.raises(PlacementError):
        placement.insert_text(page, TP, "x")
    with pytest.raises(ValueError):
        placement.insertion_matrix(make_page((MEDIABOXES["mb0"], None, 0)), "boh")
