"""Collaudo 4: richiamo con /CropBox, FDF con risposte, casella di testo esportata, note su pagine ruotate,
ordinamento per pagina su pagine ruotate."""

import pymupdf
import pytest

from foliant.modules.annotations import engine


def _cl(pdf, xref):
    return engine._array(pdf, xref, "CL")


@pytest.mark.parametrize("rotation", [0, 90])
def test_callout_with_cropbox(rotation):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_cropbox(pymupdf.Rect(40, 50, 520, 780))
    page.set_rotation(rotation)
    xref = engine.create_freetext(pdf, 0, (220, 300, 380, 340), "Vedi qui", engine.Style(), "A",
                                  callout=[(100, 400), (200, 350)])
    page = pdf[0]
    cl = _cl(pdf, xref)
    with engine.unrotated(page):
        tip = engine.to_pdf_point(page, 100, 400)
        knee = engine.to_pdf_point(page, 200, 350)
    assert cl[:2] == pytest.approx(tip, abs=0.05)
    assert cl[2:4] == pytest.approx(knee, abs=0.05)
    if rotation == 0:
        assert cl[:2] == pytest.approx((140, 392), abs=0.05)
    pts = engine.vertex_points(pdf, 0, xref)
    assert (pts[0].x, pts[0].y) == pytest.approx((100, 400), abs=0.05)
    # il /Rect contiene punta e casella
    with engine.unrotated(page):
        r = pymupdf.Rect(engine.from_pdf_rect(page, engine._array(pdf, xref, "Rect")))
    assert r.contains(pymupdf.Point(100, 400)) and r.contains(pymupdf.Rect(220, 300, 380, 340))


def _roundtrip(pdf, fmt):
    """Esporta (XFDF o FDF), elimina tutto e reimporta."""
    from foliant.modules.annotations import ui_actions
    if fmt == "xfdf":
        data = engine.export_xfdf(pdf)
    else:
        data = ui_actions.fdf_to_xfdf(engine.export_fdf(pdf))
    engine.delete_all(pdf)
    assert engine.list_annotations(pdf) == []
    engine.import_xfdf(pdf, data)


@pytest.mark.parametrize("fmt", ["xfdf", "fdf"])
def test_callout_with_cropbox_roundtrip(fmt):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_cropbox(pymupdf.Rect(40, 50, 520, 780))
    engine.create_freetext(pdf, 0, (220, 300, 380, 340), "Vedi qui", engine.Style(), "A",
                           callout=[(100, 400), (200, 350)])
    _roundtrip(pdf, fmt)
    a, = engine.list_annotations(pdf)
    pts = engine.vertex_points(pdf, 0, a.xref)
    assert (pts[0].x, pts[0].y) == pytest.approx((100, 400), abs=0.05)
    assert engine._array(pdf, a.xref, "CL")[:2] == pytest.approx((140, 392), abs=0.05)


def test_fdf_keeps_replies_and_status():
    pdf = pymupdf.open()
    pdf.new_page()
    note = engine.create_note(pdf, 0, (100, 100), "Domanda", engine.Style(), "Anna")
    engine.add_reply(pdf, 0, note, "Prima risposta", "Bruno")
    engine.add_reply(pdf, 0, note, "Seconda risposta", "Carla")
    engine.set_status(pdf, 0, note, engine.STATE_ACCEPTED, "Bruno")
    fdf = engine.export_fdf(pdf)
    assert fdf.count(b"/IRT") == 3 and b"/RT/R" in fdf.replace(b" ", b"")
    _roundtrip(pdf, "fdf")
    top = engine.list_annotations(pdf)
    assert len(top) == 1
    assert [r.contents for r in top[0].replies] == ["Prima risposta", "Seconda risposta"]
    assert top[0].status == engine.STATE_ACCEPTED


@pytest.mark.parametrize("fmt", ["xfdf", "fdf"])
def test_freetext_roundtrip_keeps_border_and_rect(fmt):
    pdf = pymupdf.open()
    pdf.new_page()
    style = engine.Style(color=(1.0, 0.0, 0.0), width=1.5, fill=(1.0, 1.0, 0.8))
    style.autofit = False
    xref = engine.create_freetext(pdf, 0, (250, 120, 450, 190), "Riquadro", style, "A")
    page = pdf[0]
    before = pymupdf.Rect(engine.find_annot(page, xref).rect)
    _roundtrip(pdf, fmt)
    a, = engine.list_annotations(pdf)
    page = pdf[0]
    after = engine.find_annot(page, a.xref).rect
    assert tuple(after) == pytest.approx(tuple(before), abs=0.02)
    assert engine._array(pdf, a.xref, engine.FT_BORDER_KEY) == pytest.approx([1.0, 0.0, 0.0])
    assert a.contents == "Riquadro"


def _icon_box(page) -> pymupdf.IRect:
    """Riquadro dei pixel non bianchi della pagina visualizzata (ruotata)."""
    pix = page.get_pixmap(dpi=72)
    xs, ys = [], []
    for y in range(pix.height):
        for x in range(pix.width):
            if pix.pixel(x, y) != (255, 255, 255):
                xs.append(x)
                ys.append(y)
    return pymupdf.IRect(min(xs), min(ys), max(xs) + 1, max(ys) + 1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_note_icon_under_click_on_rotated_page(rotation):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_rotation(rotation)
    click = pymupdf.Point(150, 650)                      # coordinate standard (pagina non ruotata)
    xref = engine.create_note(pdf, 0, click, "x", engine.Style(color=(1, 0, 0)), "A")
    page = pdf[0]
    shown = click * page.rotation_matrix                  # punto nella pagina visualizzata
    box = _icon_box(page)
    assert box.contains(shown), (box, shown)
    centre = pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
    assert abs(centre - shown) < 3
    assert engine.annot_at(pdf, 0, click, 1.0) == xref   # il clic sull'icona la seleziona


@pytest.mark.parametrize("fmt", ["xfdf", "fdf"])
@pytest.mark.parametrize("rotation", [0, 90])
def test_note_roundtrip_on_rotated_page(fmt, rotation):
    pdf = pymupdf.open()
    page = pdf.new_page(width=595, height=842)
    page.set_rotation(rotation)
    xref = engine.create_note(pdf, 0, (190, 280), "x", engine.Style(), "A")
    raw = engine._array(pdf, xref, "Rect")
    page = pdf[0]
    shown = pymupdf.Rect(engine.find_annot(page, xref).rect)
    _roundtrip(pdf, fmt)
    a, = engine.list_annotations(pdf)
    assert engine._array(pdf, a.xref, "Rect") == pytest.approx(raw, abs=0.01)
    assert tuple(a.rect) == pytest.approx(tuple(shown), abs=0.01)


def test_comments_sorted_top_down_on_rotated_page(window, tmp_path, monkeypatch):
    """ANN-36: «Per pagina» segue l'ordine dall'alto in basso nella vista anche con /Rotate 90."""
    from PySide6.QtCore import Qt
    path = tmp_path / "ruotata.pdf"
    d = pymupdf.open()
    d.new_page(width=595, height=842).set_rotation(90)
    rect = engine.create_shape(d, 0, engine.RECT, [(100, 600), (160, 640)], engine.Style(), "A")
    ell = engine.create_shape(d, 0, engine.ELLIPSE, [(400, 100), (460, 140)], engine.Style(), "A")
    d.save(str(path))
    window.open_path(str(path))
    module = window.ctx.annotations
    window.set_mode("commenta")
    panel = module.comments
    panel.reload()
    panel.sort.setCurrentIndex(panel.sort.findData("page"))
    keys = [tuple(i.data(0, Qt.ItemDataRole.UserRole)) for i in panel.items()]
    assert keys == [(0, rect), (0, ell)]          # il rettangolo è in alto nella vista
