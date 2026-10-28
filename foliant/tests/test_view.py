import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt

from foliant.core.document import Document
from foliant.ui.document_view import FIT_PAGE, FIT_WIDTH, VIEW_FACING, VIEW_SINGLE, DocumentView
from tests.conftest import make_pdf


@pytest.fixture
def view(qtbot, sample_pdf):
    v = DocumentView(Document.open(sample_pdf))
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    return v


def test_layout_and_navigation(view, qtbot):
    assert view.page_count == 3
    assert view.fit_mode == FIT_WIDTH
    view.go_to_page(2)
    assert view.current_page == 2
    view.go_to_page(0)
    assert view.current_page == 0
    view.next_page()
    assert view.current_page == 1


def test_zoom(view):
    view.set_zoom(2.0)
    assert view.zoom == pytest.approx(2.0)
    assert view.fit_mode is None
    view.zoom_in()
    assert view.zoom == pytest.approx(2.5)
    view.zoom_out()
    assert view.zoom == pytest.approx(2.0)
    view.set_fit(FIT_PAGE)
    assert view.fit_mode == FIT_PAGE


@pytest.mark.parametrize("rotation,crop", [(0, None), (90, None), (180, (50, 30, 550, 800)), (270, (20, 40, 500, 700))])
def test_coordinate_mapping_matches_rendering(qtbot, tmp_path, rotation, crop):
    """Un rettangolo nero noto deve comparire esattamente dove la vista lo mappa."""
    target = pymupdf.Rect(100, 200, 140, 230)

    def extra(page, i):
        page.draw_rect(target, color=(0, 0, 0), fill=(0, 0, 0))
        if crop:
            page.set_cropbox(pymupdf.Rect(*crop))
        page.set_rotation(rotation)

    path = make_pdf(tmp_path / "r.pdf", pages=1, extra=extra)
    with pymupdf.open(path) as d:            # coordinate come le riporta il motore dopo ritaglio/rotazione
        target = d[0].get_drawings()[0]["rect"]
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(800, 900)
    v.show()
    qtbot.waitExposed(v)
    v.set_zoom(1.0)
    v.flush_rendering()
    img = v.grab().toImage()
    mapped = v.page_to_view(0, target)
    dpr = img.devicePixelRatio()
    cx, cy = mapped.center().x(), mapped.center().y()
    px = img.pixelColor(int(cx * dpr), int(cy * dpr))
    assert px.red() < 60 and px.green() < 60, (rotation, crop, mapped, px.name())
    # e il punto inverso torna alle coordinate pagina
    page, pt = v.view_to_page(QPointF(cx, cy))
    assert page == 0
    assert abs(pt.x - target.x0 - 20) < 1 and abs(pt.y - target.y0 - 15) < 1


def test_view_modes(view):
    view.set_view_mode(VIEW_FACING)
    r0, r1 = view.page_rect(0), view.page_rect(1)
    assert abs(r0.top() - r1.top()) < 1 and r1.left() > r0.right()
    view.set_view_mode(VIEW_SINGLE)
    view.go_to_page(2)
    assert view.page_rect(0).isEmpty() and not view.page_rect(2).isEmpty()


def test_text_selection_by_drag(view, qtbot):
    view.set_zoom(1.0)
    view.go_to_page(0)
    words = view.words(0)
    first = next(w for w in words if w[4] == "Pagina")
    second = next(w for w in words if w[4] == "1")
    a = view.page_transform(0).map(QPointF(first[0] + 2, (first[1] + first[3]) / 2))
    b = view.page_transform(0).map(QPointF(second[2] - 1, (second[1] + second[3]) / 2))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a.toPoint())
    qtbot.mouseMove(view.viewport(), pos=b.toPoint())
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b.toPoint())
    assert view.selected_text() == "Pagina 1"


def test_content_change_refreshes(view, qtbot):
    doc = view.document
    with doc.change("Elimina") as pdf:
        pdf.delete_page(0)
    assert view.page_count == 2
    doc.undo()
    assert view.page_count == 3


def test_rotate_view_keeps_document(view):
    view.rotate_view(90)
    assert view.view_rotation == 90
    assert not view.document.is_modified
    w, h = view.page_size(0)
    assert w > h


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_link_click_on_rotated_page(qtbot, tmp_path, rotation):
    """Il collegamento si trova dove è disegnato, anche su pagine ruotate."""
    path = tmp_path / "link.pdf"
    d = pymupdf.open()
    d.new_page(); d.new_page()
    p = d[0]
    area = pymupdf.Rect(100, 200, 260, 230)
    p.insert_link({"kind": pymupdf.LINK_GOTO, "from": area, "page": 1, "to": pymupdf.Point(72, 72)})
    p.set_rotation(rotation)
    d.save(str(path))
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(900, 900)
    v.show()
    qtbot.waitExposed(v)
    hit = v.view_to_page(v.page_to_view(0, area).center())
    assert hit is not None and hit[0] == 0
    link = v.link_at(0, hit[1])
    assert link is not None and link.get("page") == 1, (rotation, hit, v.links(0))


def test_crop_larger_than_mediabox_mapping(qtbot, tmp_path):
    """CropBox più grande del MediaBox su pagina ruotata: testo selezionabile dove si vede."""
    path = tmp_path / "fuori.pdf"
    d = pymupdf.open()
    p = d.new_page(width=595, height=842)
    p.insert_text((100, 300), "ALFA", fontsize=30)
    d.xref_set_key(p.xref, "CropBox", "[-20 -30 620 857]")
    d.xref_set_key(p.xref, "Rotate", "90")
    d.save(str(path))
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(900, 900)
    v.show()
    qtbot.waitExposed(v)
    v.set_zoom(1.0)
    v.flush_rendering()
    word = next(w for w in v.words(0) if w[4] == "ALFA")
    centre = v.page_to_view(0, pymupdf.Rect(word[:4])).center()
    img = v.grab().toImage()
    dpr = img.devicePixelRatio()
    # nel riquadro della parola c'è inchiostro (pixel scuri)
    dark = 0
    r = v.page_to_view(0, pymupdf.Rect(word[:4]))
    for x in range(int(r.left()), int(r.right()), 2):
        for y in range(int(r.top()), int(r.bottom()), 2):
            if img.pixelColor(int(x * dpr), int(y * dpr)).lightness() < 100:
                dark += 1
    assert dark > 10, (r, dark)
    assert v.word_at(0, v.view_to_page(centre)[1])[4] == "ALFA"
