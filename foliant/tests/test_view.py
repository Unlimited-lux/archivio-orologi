import pymupdf
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt

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


def test_zoom_limits_spec(view):
    """VIS-10: limiti 1%–6400%; i passi proseguono oltre l'800% senza saltare al massimo."""
    view.set_zoom(1000)
    assert view.zoom == pytest.approx(64.0)
    view.zoom_in()
    assert view.zoom == pytest.approx(64.0)
    view.set_zoom(8.0)
    view.zoom_in()
    assert 8.0 < view.zoom < 64.0
    view.set_zoom(0.0001)
    assert view.zoom == pytest.approx(0.01)
    view.zoom_out()
    assert view.zoom == pytest.approx(0.01)


def test_zoom_1_percent(view):
    """VIS-10: lo zoom all'1% funziona e disegna le pagine."""
    view.set_zoom(0.01)
    assert view.zoom == pytest.approx(0.01)
    view.flush_rendering()
    view.grab()
    r = view.page_rect(0)
    assert 5 < r.width() < 10 and 8 < r.height() < 14
    key = view._scale_key()
    assert (0, key, 0, view.document.version) in view._cache


def test_zoom_6400_renders_only_visible_tiles(qtbot, tmp_path, monkeypatch):
    """VIS-10: a 6400% una A4 (circa 38.000 px di lato) si disegna a tasselle, mai per intero."""
    path = make_pdf(tmp_path / "a4.pdf", pages=2)
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    v.flush_rendering()
    sizes = []
    original = pymupdf.Page.get_pixmap

    def spy(page, *a, **k):
        pix = original(page, *a, **k)
        sizes.append(pix.width * pix.height)
        return pix

    monkeypatch.setattr(pymupdf.Page, "get_pixmap", spy)
    v.set_zoom(64)
    assert v.zoom == pytest.approx(64.0)
    v.flush_rendering()
    v.grab()
    assert sizes and max(sizes) <= 20_000_000, max(sizes)
    assert all(img.width() * img.height() <= 20_000_000 for img in v._cache.values())
    assert v._tiled(0, v._scale_key()) and v._tiles
    assert v._tiles_bytes <= v.TILE_CACHE_LIMIT
    # le tasselle visibili ci sono tutte
    key, ver = v._scale_key(), v.document.version
    visible = v._tiles_in(0, key, QRectF(v.viewport().rect()))
    assert visible and all((0, key, 0, ver) + t in v._tiles for t in visible)
    # allo scorrimento si disegnano solo le tasselle nuove; tornando indietro si riusano quelle in cache
    hbar, vbar = v.horizontalScrollBar(), v.verticalScrollBar()
    x, y = hbar.value(), vbar.value()
    vbar.setValue(y + 5000)
    v.flush_rendering()
    hbar.setValue(x + 3000)
    v.flush_rendering()
    hbar.setValue(x)
    vbar.setValue(y)
    before = len(sizes)
    v.flush_rendering()
    assert len(sizes) == before
    assert max(sizes) <= 20_000_000
    v.set_zoom(1.0)                     # zoom normale: niente più tasselle in memoria
    v.flush_rendering()
    assert not v._tiles and v._tiles_bytes == 0


def test_tile_cache_is_bounded(qtbot, sample_pdf, monkeypatch):
    """La cache delle tasselle scarta le meno usate oltre il limite."""
    v = DocumentView(Document.open(sample_pdf))
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    monkeypatch.setattr(v, "TILE_CACHE_LIMIT", 12 * 1024 * 1024)
    v.set_zoom(20)
    vbar = v.verticalScrollBar()
    for step in range(6):
        vbar.setValue(vbar.value() + 1500)
        v.flush_rendering()
        assert v._tiles_bytes <= v.TILE_CACHE_LIMIT
        assert v._tiles_bytes == sum(t[0].sizeInBytes() for t in v._tiles.values())


@pytest.mark.parametrize("rotation,crop,view_rotation", [
    (0, None, 0),
    (90, (20, 40, 500, 700), 0),
    (90, (20, 40, 500, 700), 90),
    (270, (50, 30, 550, 800), 0),
    (180, None, 270),
])
def test_tiled_rendering_matches_mapping(qtbot, tmp_path, rotation, crop, view_rotation):
    """A zoom 3200% il rettangolo nero compare, a tasselle, dove page_to_view lo mappa."""
    target = pymupdf.Rect(100, 200, 140, 230)

    def extra(page, i):
        page.draw_rect(target, color=None, fill=(0, 0, 0))     # senza bordo: margini netti
        assert page.get_drawings()
        if crop:
            page.set_cropbox(pymupdf.Rect(*crop))
        page.set_rotation(rotation)

    path = make_pdf(tmp_path / "r.pdf", pages=1, extra=extra)
    with pymupdf.open(path) as d:
        target = d[0].get_drawings()[0]["rect"]
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(800, 900)
    v.show()
    qtbot.waitExposed(v)
    if view_rotation:
        v.rotate_view(view_rotation)
    v.set_zoom(32)
    assert v._tiled(0, v._scale_key())
    hbar, vbar = v.horizontalScrollBar(), v.verticalScrollBar()
    vw, vh = v.viewport().width(), v.viewport().height()

    def show_tiles_at(point: QPointF):
        """Porta `point` (viewport) al centro e cattura solo le tasselle (senza la resa intera sotto)."""
        hbar.setValue(int(round(hbar.value() + point.x() - vw / 2)))
        vbar.setValue(int(round(vbar.value() + point.y() - vh / 2)))
        v.flush_rendering()
        v._stale.clear()
        return v.grab().toImage()

    def dark(img, x, y):
        dpr = img.devicePixelRatio()
        px = img.pixelColor(int(x * dpr), int(y * dpr))
        return px.red() < 60 and px.green() < 60, px.name()

    img = show_tiles_at(v.page_to_view(0, target).center())
    c = v.page_to_view(0, target).center()
    ok, name = dark(img, c.x(), c.y())
    assert ok, (rotation, crop, view_rotation, name)
    # precisione: un angolo del rettangolo, ±12 px (meno di 0,3 pt) dentro e fuori
    mapped = v.page_to_view(0, target)
    img = show_tiles_at(mapped.topLeft())
    corner = v.page_to_view(0, target).topLeft()
    assert dark(img, corner.x() + 12, corner.y() + 12)[0]
    assert not dark(img, corner.x() - 12, corner.y() - 12)[0]
    assert not dark(img, corner.x() + 12, corner.y() - 12)[0]
    assert not dark(img, corner.x() - 12, corner.y() + 12)[0]
    page, pt = v.view_to_page(QPointF(corner.x() + 12, corner.y() + 12))
    assert page == 0 and pymupdf.Rect(target).contains(pt)


def test_tiles_follow_content_changes(qtbot, sample_pdf):
    """Dopo una modifica le tasselle si ridisegnano; quelle vecchie restano solo finché servono."""
    v = DocumentView(Document.open(sample_pdf))
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    v.set_zoom(16)
    v.go_to_page(0, pymupdf.Point(72, 100))
    v.flush_rendering()
    old = v.document.version
    with v.document.change("Riquadro") as pdf:
        pdf[0].draw_rect(pymupdf.Rect(0, 0, 595, 842), color=None, fill=(0, 0, 0))
    assert v.document.version != old
    assert v._tiles and all(k[3] == old for k in v._tiles)   # resa provvisoria in attesa delle nuove
    v.flush_rendering()
    key, ver = v._scale_key(), v.document.version
    visible = v._tiles_in(0, key, QRectF(v.viewport().rect()))
    assert visible and all((0, key, 0, ver) + t in v._tiles for t in visible)
    assert not any(k[3] == old and (k[4], k[5]) in visible for k in v._tiles if k[0] == 0)
    img = v.grab().toImage()
    r = v.page_rect(0) & QRectF(v.viewport().rect())
    px = img.pixelColor(int(r.center().x() * img.devicePixelRatio()), int(r.center().y() * img.devicePixelRatio()))
    assert px.red() < 30


def test_tiles_respect_hidden_annotations(qtbot, tmp_path):
    """Con le annotazioni nascoste, anche le tasselle si disegnano senza."""
    path = tmp_path / "annot.pdf"
    d = pymupdf.open()
    p = d.new_page()
    a = p.add_rect_annot(pymupdf.Rect(100, 200, 300, 400))
    a.set_colors(stroke=(0, 0, 0), fill=(0, 0, 0))
    a.update()
    d.save(str(path))
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(800, 800)
    v.show()
    qtbot.waitExposed(v)
    v.set_zoom(12)
    v.ensure_visible(0, pymupdf.Rect(150, 250, 250, 350))
    centre = v.page_to_view(0, pymupdf.Rect(150, 250, 250, 350)).center()

    def colour():
        v.flush_rendering()
        v._stale.clear()
        img = v.grab().toImage()
        dpr = img.devicePixelRatio()
        return img.pixelColor(int(centre.x() * dpr), int(centre.y() * dpr)).red()

    assert v._tiled(0, v._scale_key())
    assert colour() < 60
    v.show_annotations = False
    v._invalidate_render()
    assert colour() > 200


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


def test_fit_width_has_no_horizontal_scroll(qtbot, sample_pdf):
    from foliant.ui.document_view import FIT_WIDTH
    v = DocumentView(Document.open(sample_pdf))
    qtbot.addWidget(v)
    for w in (640, 777, 858, 1001, 1333):
        v.resize(w, 700)
        v.show()
        qtbot.waitExposed(v)
        v.set_fit(FIT_WIDTH)
        assert v.horizontalScrollBar().maximum() == 0, w


def test_drag_far_from_text_selects_nothing(view, qtbot):
    """Trascinare in una zona vuota, lontano dal testo, non seleziona la parola più vicina."""
    view.set_zoom(1.0)
    view.go_to_page(0)
    a = view.page_transform(0).map(QPointF(300, 600))
    b = view.page_transform(0).map(QPointF(450, 700))
    qtbot.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a.toPoint())
    qtbot.mouseMove(view.viewport(), pos=b.toPoint())
    qtbot.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b.toPoint())
    assert view.selected_text() == ""


def test_copy_respects_permission(qtbot, tmp_path):
    """Con il permesso di copia negato, Ctrl+C non mette il testo negli appunti."""
    from PySide6.QtGui import QGuiApplication
    src = make_pdf(tmp_path / "a.pdf", pages=1)
    d = pymupdf.open(src)
    path = tmp_path / "noc.pdf"
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
           permissions=pymupdf.PDF_PERM_PRINT)
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(900, 700)
    v.show()
    qtbot.waitExposed(v)
    QGuiApplication.clipboard().setText("ORIGINALE")
    v._select_all_on_page()
    assert v.selected_text()
    assert not v.copy_selection()
    assert QGuiApplication.clipboard().text() == "ORIGINALE"
    assert v.document.pdf.authenticate("capo")      # accesso da proprietario: copia consentita
    assert v.copy_selection()
    assert QGuiApplication.clipboard().text() != "ORIGINALE"
    QGuiApplication.clipboard().clear()


def test_unreadable_page_shows_notice(qtbot, tmp_path):
    """NFR-14: una pagina che il motore non carica mostra un riquadro, le altre restano visibili."""
    d = pymupdf.open()
    for i in range(2):
        d.new_page().insert_text((72, 72), f"Pagina {i + 1}")
    px = d.pdf_catalog()
    px = int(d.xref_get_key(px, "Pages")[1].split()[0])
    d.xref_set_key(px, "Kids", d.xref_get_key(px, "Kids")[1].replace("]", f" {px} 0 R]"))
    d.xref_set_key(px, "Count", "3")
    path = tmp_path / "ciclo.pdf"
    d.save(str(path))
    v = DocumentView(Document.open(path))
    qtbot.addWidget(v)
    v.resize(700, 900)
    v.show()
    qtbot.waitExposed(v)
    if v.page_count < 3:          # il motore ha ricostruito l'albero: nessuna pagina illeggibile
        return
    v.set_fit(FIT_PAGE)
    v.go_to_page(2)
    v.flush_rendering()                                   # nessuna eccezione
    img = v.grab().toImage()
    r = v.page_rect(2)
    dpr = img.devicePixelRatio()
    c = img.pixelColor(int(r.center().x() * dpr), int((r.top() + 20) * dpr))
    assert (c.red(), c.green(), c.blue()) != (255, 255, 255)   # riquadro grigio, non pagina bianca
