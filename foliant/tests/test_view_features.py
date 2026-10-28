"""Vista documento: selezione su più pagine, righelli e guide, filtro delle annotazioni, Tab agli strumenti."""

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication

from foliant.core import placement
from foliant.core.document import Document
from foliant.ui.document_view import FIT_WIDTH, GUIDE_HORIZONTAL, GUIDE_VERTICAL, DocumentView
from foliant.ui.rulers import RULER_SIZE, major_step, ruler_ticks
from foliant.ui.tools import Tool
from tests.conftest import make_pdf

LINE2 = "Orologeria Verdi - contratto di compravendita"


def _make_view(qtbot, path, w=900, h=700, cls=DocumentView):
    v = cls(Document.open(str(path)))
    qtbot.addWidget(v)
    v.resize(w, h)
    v.show()
    qtbot.waitExposed(v)
    return v


def _word_center(v, page, text):
    w = next(w for w in v.words(page) if w[4] == text)
    return v.page_transform(page).map(QPointF((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)).toPoint()


# ====================================================================== selezione su più pagine
def test_drag_selects_across_three_pages_and_copies(qtbot, tmp_path):
    v = _make_view(qtbot, make_pdf(tmp_path / "tre.pdf", pages=3), w=700, h=1200)
    v.set_zoom(0.3)
    v.verticalScrollBar().setValue(0)
    assert set(v.visible_pages()) == {0, 1, 2}
    changes = []
    v.selectionChanged.connect(lambda: changes.append(1))
    a = _word_center(v, 0, "Pagina")
    b = _word_center(v, 2, "Verdi")
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(v.viewport(), pos=_word_center(v, 1, "Pagina"))
    qtbot.mouseMove(v.viewport(), pos=b)
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=b)
    sels = v.text_selections
    assert [s.page for s in sels] == [0, 1, 2]
    assert sels[0].text == "Pagina 1\n" + LINE2
    assert sels[1].text == "Pagina 2\n" + LINE2
    assert sels[2].text == "Pagina 3\nOrologeria Verdi"
    # compatibilità: text_selection è la selezione della prima pagina
    assert v.text_selection is not None and v.text_selection.page == 0
    expected = "\n\n".join(s.text for s in sels)
    assert v.selected_text() == expected
    assert changes
    # Ctrl+C copia tutte le pagine
    QGuiApplication.clipboard().setText("ORIGINALE")
    v.setFocus()
    qtbot.keyClick(v, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().text() == expected
    QGuiApplication.clipboard().clear()
    # Ctrl+A torna a selezionare la sola pagina corrente
    qtbot.keyClick(v, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert [s.page for s in v.text_selections] == [v.current_page]


def test_drag_backwards_across_pages_keeps_reading_order(qtbot, tmp_path):
    v = _make_view(qtbot, make_pdf(tmp_path / "tre.pdf", pages=3), w=700, h=1200)
    v.set_zoom(0.3)
    v.verticalScrollBar().setValue(0)
    a = _word_center(v, 2, "Verdi")
    b = _word_center(v, 0, "Pagina")
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=a)
    qtbot.mouseMove(v.viewport(), pos=b)
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=b)
    assert [s.page for s in v.text_selections] == [0, 1, 2]
    assert v.selected_text().startswith("Pagina 1\n") and v.selected_text().endswith("Pagina 3\nOrologeria Verdi")
    # un clic altrove azzera tutta la selezione
    qtbot.mouseClick(v.viewport(), Qt.MouseButton.LeftButton, pos=_word_center(v, 1, "Verdi"))
    assert v.text_selections == [] and v.selected_text() == ""


def test_multipage_copy_respects_permission(qtbot, tmp_path):
    src = make_pdf(tmp_path / "a.pdf", pages=2)
    d = pymupdf.open(src)
    path = tmp_path / "noc.pdf"
    d.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="capo", user_pw="",
           permissions=pymupdf.PDF_PERM_PRINT)
    v = _make_view(qtbot, path)
    from foliant.ui.tools import selection_from_words
    v.set_text_selections([selection_from_words(1, v.words(1)), selection_from_words(0, v.words(0))])
    assert [s.page for s in v.text_selections] == [0, 1]
    QGuiApplication.clipboard().setText("ORIGINALE")
    assert not v.copy_selection()
    assert QGuiApplication.clipboard().text() == "ORIGINALE"
    QGuiApplication.clipboard().clear()


def test_selection_autoscrolls_past_the_edge(qtbot, tmp_path):
    v = _make_view(qtbot, make_pdf(tmp_path / "tre.pdf", pages=3), w=900, h=700)
    v.set_zoom(1.0)
    v.go_to_page(0)
    vbar = v.verticalScrollBar()
    start = vbar.value()
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=_word_center(v, 0, "Pagina"))
    below = QPoint(v.viewport().width() // 2, v.viewport().height() + 40)
    qtbot.mouseMove(v.viewport(), pos=below)
    assert v._autoscroll_timer.isActive()
    for _ in range(400):
        v._autoscroll_step()
        if not v._autoscroll_timer.isActive():
            break
    assert vbar.value() > start
    pages = [s.page for s in v.text_selections]
    assert pages[0] == 0 and len(pages) >= 2 and pages == sorted(pages)
    assert v.text_selections[0].text.startswith("Pagina 1")
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=below)
    assert not v._autoscroll_timer.isActive()
    # gli strumenti senza `autoscroll` non fanno scorrere la vista
    v.set_tool(v.hand_tool)
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
    qtbot.mouseMove(v.viewport(), pos=below)
    assert not v._autoscroll_timer.isActive()
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=below)


# ====================================================================== righelli
def test_ruler_ticks_follow_zoom_and_unit():
    ppp = 96 / 72                                     # zoom 100%
    assert major_step("mm", ppp * 72 / 25.4) == 20
    ticks = ruler_ticks(100.0, ppp, "mm", 0.0, 400.0)
    majors = [(round(pos, 3), value) for pos, value, level in ticks if level == 0]
    assert (100.0, 0.0) in majors
    assert (round(100.0 + 20 * 72 / 25.4 * ppp, 3), 20.0) in majors
    assert any(level == 2 for _p, _v, level in ticks)
    # zoom elevato: passi più fitti; pollici con suddivisioni in ottavi
    assert major_step("mm", 64 * ppp * 72 / 25.4) < 1
    inch = ruler_ticks(0.0, ppp, "in", 0.0, 300.0)
    assert [v for _p, v, level in inch if level == 0][:3] == [0.0, 1.0, 2.0]
    # nessuna esplosione di tacche agli zoom minimi
    assert len(ruler_ticks(0.0, 0.01 * ppp, "pt", 0.0, 2000.0)) < 2000 / 4 + 2


def test_rulers_toggle_and_measure_a4(qtbot, tmp_path):
    v = _make_view(qtbot, make_pdf(tmp_path / "a4.pdf", pages=2))
    v.set_fit(FIT_WIDTH)
    before = v.page_rect(0)
    v.set_rulers_visible(True)
    hr, vr, corner = v._rulers
    assert hr.isVisible() and vr.isVisible() and corner.isVisible()
    assert hr.height() == RULER_SIZE and hr.width() == v.viewport().width()
    v.go_to_page(0)
    r = v.page_rect(0)
    assert r.left() >= RULER_SIZE and r.top() >= RULER_SIZE      # le pagine non stanno sotto i righelli
    assert v.horizontalScrollBar().maximum() == 0                 # «adatta larghezza» tiene conto dei righelli
    # VIS-39: A4 in mm, angolo inferiore destro = 210 × 297
    x, y = v.ruler_position(r.bottomRight(), page=0)
    assert x == pytest.approx(210.0, abs=0.5) and y == pytest.approx(297.0, abs=0.5)
    v.set_ruler_unit("pt")
    x, y = v.ruler_position(r.bottomRight(), page=0)
    assert x == pytest.approx(595, abs=0.5) and y == pytest.approx(842, abs=0.5)
    with pytest.raises(ValueError):
        v.set_ruler_unit("km")
    v.grab()                                                       # disegno dei righelli senza errori
    v.set_rulers_visible(False)
    assert not hr.isVisible()
    assert v.page_rect(0).width() == pytest.approx(before.width(), abs=1)


@pytest.mark.parametrize("view_rotation", [0, 90])
def test_rulers_follow_crop_and_rotation(qtbot, tmp_path, view_rotation):
    def extra(page, i):
        page.set_cropbox(pymupdf.Rect(50, 30, 550, 800))
        page.set_rotation(90)

    v = _make_view(qtbot, make_pdf(tmp_path / "r.pdf", pages=1, extra=extra))
    v.set_rulers_visible(True)
    v.set_ruler_unit("pt")
    if view_rotation:
        v.rotate_view(view_rotation)
    v.set_zoom(0.5)
    r = v.page_rect(0)
    x, y = v.ruler_position(r.bottomRight(), page=0)
    w, h = (770, 500) if view_rotation == 0 else (500, 770)       # pagina come si vede
    assert x == pytest.approx(w, abs=0.5) and y == pytest.approx(h, abs=0.5)
    # le coordinate della pagina visualizzata coincidono con quelle del motore per l'inserimento
    with pymupdf.open(v.document.path) as d:
        base = placement.page_to_display_matrix(d[0])
    for pt in (pymupdf.Point(100, 200), pymupdf.Point(400, 600)):
        a = v.page_transform(0).map(QPointF(pt.x, pt.y))
        dp = pt * base
        b = v.display_transform(0).map(QPointF(dp.x, dp.y))
        assert abs(a.x() - b.x()) < 0.01 and abs(a.y() - b.y()) < 0.01
    assert v.display_size(0) == pytest.approx((770, 500), abs=0.01)


# ====================================================================== guide
def test_guides_api_and_page_lines(qtbot, tmp_path):
    def extra(page, i):
        page.set_rotation(90)

    v = _make_view(qtbot, make_pdf(tmp_path / "g.pdf", pages=2, extra=extra))
    seen = []
    v.guidesChanged.connect(lambda: seen.append(v.guides()))
    assert v.add_guide("v", 100) == (GUIDE_VERTICAL, 100.0, None)
    assert v.add_guide(GUIDE_HORIZONTAL, 50, page=1) == (GUIDE_HORIZONTAL, 50.0, 1)
    v.add_guide("vertical", 100)                                  # doppione: ignorato
    assert v.guides() == [(GUIDE_VERTICAL, 100.0, None), (GUIDE_HORIZONTAL, 50.0, 1)]
    assert len(seen) == 2
    assert v.guides_on_page(0) == [(GUIDE_VERTICAL, 100.0)]
    assert v.guides_on_page(1) == [(GUIDE_VERTICAL, 100.0), (GUIDE_HORIZONTAL, 50.0)]
    # pagina con /Rotate 90: la guida verticale della pagina visualizzata ha y costante nel motore
    lines = v.page_guide_lines(0)
    assert lines["x"] == [] and len(lines["y"]) == 1
    with pymupdf.open(v.document.path) as d:
        m = placement.page_to_display_matrix(d[0])
    for x in (10, 300, 500):
        assert (pymupdf.Point(x, lines["y"][0]) * m).x == pytest.approx(100, abs=1e-6)
    with pytest.raises(ValueError):
        v.add_guide("diagonale", 3)
    assert v.remove_guide(*v.guides()[1])
    assert not v.remove_guide("h", 50, 1)
    v.set_guides([("y", 20), ("x", 30, 0)])
    assert v.guides() == [(GUIDE_HORIZONTAL, 20.0, None), (GUIDE_VERTICAL, 30.0, 0)]
    v.clear_guides()
    assert v.guides() == [] and seen[-1] == []


def test_guides_dragged_from_rulers_moved_and_deleted(qtbot, sample_pdf):
    v = _make_view(qtbot, sample_pdf)
    v.set_rulers_visible(True)
    v.set_zoom(1.0)
    v.go_to_page(0)
    hr, vr, _corner = v._rulers
    r = v.page_rect(0)
    x = int(r.left() + 200)
    y = int(r.top() + 150)
    # dal righello in alto: guida orizzontale sulla pagina sotto il puntatore
    qtbot.mousePress(hr, Qt.MouseButton.LeftButton, pos=QPoint(x, 10))
    qtbot.mouseMove(hr, pos=QPoint(x, y))
    assert v._guide_drag is not None
    v.grab()                                                      # anteprima tratteggiata
    qtbot.mouseRelease(hr, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    inv, _ok = v.display_transform(0).inverted()
    expected = inv.map(QPointF(x, y)).y()
    (o, pos, page), = v.guides()
    assert o == GUIDE_HORIZONTAL and page == 0 and pos == pytest.approx(expected, abs=0.01)
    # dal righello a sinistra, con Ctrl: guida verticale su tutte le pagine
    qtbot.mousePress(vr, Qt.MouseButton.LeftButton, pos=QPoint(10, y + 40))
    qtbot.mouseMove(vr, pos=QPoint(x + 60, y + 40))
    qtbot.mouseRelease(vr, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, QPoint(x + 60, y + 40))
    assert v.guides()[1][0] == GUIDE_VERTICAL and v.guides()[1][2] is None
    assert v.guides()[1][1] == pytest.approx(inv.map(QPointF(x + 60, 0)).x(), abs=0.01)
    v.grab()
    # spostamento: si prende la guida sulla pagina e la si trascina
    qtbot.mouseMove(v.viewport(), pos=QPoint(x - 100, y))
    assert v.viewport().cursor().shape() == Qt.CursorShape.SplitVCursor
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x - 100, y))
    qtbot.mouseMove(v.viewport(), pos=QPoint(x - 100, y + 50))
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x - 100, y + 50))
    assert v.guides()[0][1] == pytest.approx(expected + 50 / v._scale(), abs=0.02)
    assert v.text_selections == []                                # lo strumento non ha ricevuto il clic
    # eliminazione: trascinata fuori dalla pagina
    gy = int(v.display_transform(0).map(QPointF(0, v.guides()[0][1])).y())
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(x - 100, gy))
    qtbot.mouseMove(v.viewport(), pos=QPoint(int(r.left()) - 10, gy))
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(int(r.left()) - 10, gy))
    assert len(v.guides()) == 1 and v.guides()[0][0] == GUIDE_VERTICAL
    # bloccate o con i righelli nascosti le guide non si trascinano
    gx = int(v.display_transform(0).map(QPointF(v.guides()[0][1], 0)).x())
    v.set_guides_locked(True)
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(gx, y + 200))
    assert v._guide_drag is None
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(gx, y + 200))
    v.set_guides_locked(False)
    v.set_rulers_visible(False)
    qtbot.mousePress(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(gx, y + 200))
    assert v._guide_drag is None
    qtbot.mouseRelease(v.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(gx, y + 200))
    assert len(v.guides()) == 1


def test_guide_on_rotated_view_is_attached_to_the_page(qtbot, sample_pdf):
    v = _make_view(qtbot, sample_pdf)
    v.set_rulers_visible(True)
    v.rotate_view(90)
    v.set_zoom(0.5)
    v.go_to_page(0)
    hr = v._rulers[0]
    r = v.page_rect(0)
    x, y = int(r.left() + 100), int(r.top() + 120)
    qtbot.mousePress(hr, Qt.MouseButton.LeftButton, pos=QPoint(x, 10))
    qtbot.mouseMove(hr, pos=QPoint(x, y))
    qtbot.mouseRelease(hr, Qt.MouseButton.LeftButton, pos=QPoint(x, y))
    # vista ruotata di 90°: la linea orizzontale sullo schermo è una guida verticale della pagina
    (o, pos, page), = v.guides()
    assert o == GUIDE_VERTICAL and page == 0
    assert pos == pytest.approx((y - r.top()) / v._scale(), abs=0.05)
    line = v._guide_line(0, o, pos)
    assert abs(line.dy()) < 0.01 and abs(line.p1().y() - y) < 0.6


# ====================================================================== filtro delle annotazioni
def _annot_pdf(tmp_path):
    path = tmp_path / "annot.pdf"
    d = pymupdf.open()
    p = d.new_page()
    for rect in (pymupdf.Rect(100, 200, 300, 400), pymupdf.Rect(350, 200, 500, 400)):
        a = p.add_rect_annot(rect)
        a.set_colors(stroke=(0, 0, 0), fill=(0, 0, 0))
        a.update()
    d.save(str(path))
    return path


@pytest.mark.parametrize("zoom", [1.0, 12.0])
def test_annotation_filter_applies_to_pages_and_tiles(qtbot, tmp_path, zoom):
    from foliant.core.engine import ENGINE_LOCK
    v = _make_view(qtbot, _annot_pdf(tmp_path), w=800, h=800)
    with ENGINE_LOCK:
        xa, xb = [x[0] for x in v.document.pdf[0].annot_xrefs()]
        flags = v.document.pdf.xref_get_key(xa, "F")
    v.set_zoom(zoom)
    centres = {xa: pymupdf.Rect(150, 250, 250, 350), xb: pymupdf.Rect(400, 250, 450, 350)}

    def red(xref):
        v.ensure_visible(0, centres[xref])
        v.flush_rendering()
        v._stale.clear()                    # solo la resa nuova (pagina intera o tasselle)
        c = v.page_to_view(0, centres[xref]).center()
        img = v.grab().toImage()
        dpr = img.devicePixelRatio()
        return img.pixelColor(int(c.x() * dpr), int(c.y() * dpr)).red()

    assert v._tiled(0, v._scale_key()) == (zoom > 2)
    assert red(xa) < 60 and red(xb) < 60
    calls = []
    v.set_annotation_filter(lambda page, xref: calls.append((page, xref)) or xref != xa)
    assert red(xa) > 200 and red(xb) < 60
    assert (0, xa) in calls and (0, xb) in calls
    with ENGINE_LOCK:                       # il flag Hidden è stato solo temporaneo
        assert v.document.pdf.xref_get_key(xa, "F") == flags
    assert not v.document.is_modified
    v.show_annotations = False
    v.refresh_rendering()
    assert red(xb) > 200
    v.show_annotations = True
    v.set_annotation_filter(None)
    assert red(xa) < 60


def test_broken_annotation_filter_shows_everything(qtbot, tmp_path):
    v = _make_view(qtbot, _annot_pdf(tmp_path), w=800, h=800)
    v.set_zoom(1.0)

    def broken(page, xref):
        raise RuntimeError("filtro difettoso")

    v.set_annotation_filter(broken)
    v.flush_rendering()
    v._stale.clear()
    c = v.page_to_view(0, pymupdf.Rect(150, 250, 250, 350)).center()
    img = v.grab().toImage()
    dpr = img.devicePixelRatio()
    assert img.pixelColor(int(c.x() * dpr), int(c.y() * dpr)).red() < 60


# ====================================================================== Tab agli strumenti
class _TabTool(Tool):
    def __init__(self):
        self.keys = []

    def key(self, view, ev):
        if ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.keys.append(ev.key())
            return True
        return False


class _FocusView(DocumentView):
    def __init__(self, document):
        self.focus_moves = []
        super().__init__(document)

    def focusNextPrevChild(self, forward):
        self.focus_moves.append(forward)
        return True


def test_tab_goes_to_the_active_tool(qtbot, sample_pdf):
    v = _make_view(qtbot, sample_pdf, cls=_FocusView)
    tool = _TabTool()
    v.set_tool(tool)
    v.setFocus()
    qtbot.keyClick(v, Qt.Key.Key_Tab)
    qtbot.keyClick(v, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert tool.keys == [Qt.Key.Key_Tab, Qt.Key.Key_Backtab]
    assert v.focus_moves == []
    # strumento che non gestisce Tab: il fuoco si sposta come sempre
    v.set_tool(None)
    qtbot.keyClick(v, Qt.Key.Key_Tab)
    qtbot.keyClick(v, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert v.focus_moves == [True, False]


# ====================================================================== finestra
def test_rulers_action_context_and_guides_per_document(window, sample_pdf):
    from foliant.i18n import _
    ctx = window.ctx
    assert window.act_rulers in window._menu_sections[_("Vista")]
    assert window.act_rulers.shortcut().isEmpty()
    view = window.open_path(sample_pdf)
    assert not view.rulers_visible
    ctx.set_rulers_visible(True)
    assert window.act_rulers.isChecked() and view.rulers_visible and ctx.rulers_visible()
    ctx.set_ruler_unit("in")
    assert view.ruler_unit == "in" and ctx.ruler_unit() == "in"
    with pytest.raises(ValueError):
        ctx.set_ruler_unit("cubiti")
    view.set_ruler_unit("pt")                               # scelta dal menu del righello: vale per tutti
    assert ctx.ruler_unit() == "pt"
    view.add_guide("h", 72.5, 0)
    view.add_guide("v", 30)
    window.close_view(view)
    window.act_rulers.trigger()                             # voce del menu: nasconde
    assert not ctx.rulers_visible()
    view = window.open_path(sample_pdf)
    assert view.guides() == [(GUIDE_HORIZONTAL, 72.5, 0), (GUIDE_VERTICAL, 30.0, None)]
    assert not view.rulers_visible and view.ruler_unit == "pt"
    view.clear_guides()
    window.close_view(view)
    view = window.open_path(sample_pdf)
    assert view.guides() == []
