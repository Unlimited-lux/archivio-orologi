"""VIS-10 — Zoom: criteri di accettazione della specifica.

Lo zoom è del nucleo (DocumentView, campo zoom della finestra); il viewer aggiunge lo
strumento Zoom area (`test_tools.py`). Questi test verificano i criteri sulla finestra
completa, con il modulo viewer caricato."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from foliant.api import FIT_WIDTH


def _a4(tmp_path, pages=3):
    path = tmp_path / "a4.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page(width=595.28, height=841.89).insert_text((72, 100), f"Orologio {i + 1}", fontsize=20)
    doc.save(str(path))
    return str(path)


def test_fit_width_follows_window_resize(viewer_window, tmp_path, qtbot):
    """Dato «adatta larghezza», quando la finestra viene ridimensionata, allora la larghezza della
    pagina coincide con l'area visibile (±2 px) senza barra di scorrimento orizzontale."""
    view = viewer_window.open_path(_a4(tmp_path))
    view.set_fit(FIT_WIDTH)
    for width in (1200, 900, 1400):
        viewer_window.resize(width, 800)
        QApplication.processEvents()
        qtbot.wait(50)
        r = view.page_rect(view.current_page)
        hbar = view.horizontalScrollBar()
        assert hbar.maximum() == 0
        # area visibile per la pagina: il viewport meno i margini fissi della vista (e i righelli)
        assert abs(r.width() - view._available_size()[0]) <= 2
        assert 0 <= r.left() and r.right() <= view.viewport().width()


def test_actual_size_a4_at_96_dpi(viewer_window, tmp_path):
    """Dato un monitor a 96 dpi e una pagina A4, quando si sceglie «dimensione reale» (Ctrl+1),
    allora la pagina misura 794 × 1123 px (±1)."""
    view = viewer_window.open_path(_a4(tmp_path))
    viewer_window.act_zoom_100.trigger()
    r = view.page_rect(0)
    assert abs(r.width() - 794) <= 1 and abs(r.height() - 1123) <= 1


def test_typed_zoom_value(viewer_window, tmp_path):
    """Dato il valore 333 digitato nel campo zoom, quando si preme Invio, allora lo zoom è 333%."""
    view = viewer_window.open_path(_a4(tmp_path))
    box = viewer_window.zoom_combo
    box.setEditText("333")
    box.lineEdit().returnPressed.emit()
    assert view.zoom == pytest.approx(3.33)


def test_ctrl_wheel_keeps_point_under_cursor(viewer_window, tmp_path, qtbot):
    """Dato il puntatore sopra una parola, quando si usa Ctrl+rotella in avanti, allora lo zoom
    aumenta e la parola resta sotto il puntatore (±5 px)."""
    view = viewer_window.open_path(_a4(tmp_path))
    view.set_zoom(1.0)
    QApplication.processEvents()
    words = view.words(0)
    w = words[0]
    target = view.page_to_view(0, pymupdf.Rect(w[:4])).center()
    before = view.zoom
    ev = QWheelEvent(target, view.viewport().mapToGlobal(target.toPoint()), QPoint(0, 0), QPoint(0, 120),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase,
                     False)
    view.wheelEvent(ev)
    QApplication.processEvents()
    assert view.zoom > before
    after = view.page_to_view(0, pymupdf.Rect(w[:4])).center()
    assert abs(after.x() - target.x()) <= 5 and abs(after.y() - target.y()) <= 5


def test_zoom_limits_1_to_6400(viewer_window, tmp_path):
    """Limiti 1%–6400%: oltre il limite il comando non ha effetto."""
    view = viewer_window.open_path(_a4(tmp_path, pages=1))
    view.set_zoom(100)
    assert view.zoom == pytest.approx(64.0)
    view.zoom_in()
    assert view.zoom == pytest.approx(64.0)
    view.set_zoom(0.0001)
    assert view.zoom == pytest.approx(0.01)
    view.zoom_out()
    assert view.zoom == pytest.approx(0.01)


def test_zoom_list_values(viewer_window):
    """Il campo zoom elenca i livelli predefiniti della specifica."""
    box = viewer_window.zoom_combo
    values = {round(box.itemData(i) * 100) for i in range(box.count()) if isinstance(box.itemData(i), float)}
    assert {10, 25, 50, 75, 100, 125, 150, 200, 400, 800, 1600, 3200, 6400} <= values


def test_zoom_remembered_per_document(viewer_window, tmp_path):
    """Lo zoom è ricordato per documento (VIS-02)."""
    path = _a4(tmp_path)
    view = viewer_window.open_path(path)
    view.set_zoom(1.5)
    viewer_window.close_view(view)
    view = viewer_window.open_path(path)
    assert view.zoom == pytest.approx(1.5)


def _ctl(window):
    return window.ctx.viewer_parts["zoomfit"]


def test_fit_height_follows_window_resize(viewer_window, tmp_path, qtbot):
    """«Adatta all'altezza»: la pagina corrente occupa l'altezza visibile e l'adattamento resta
    attivo quando la finestra cambia dimensione, finché l'utente non cambia lo zoom."""
    view = viewer_window.open_path(_a4(tmp_path))
    ctl = _ctl(viewer_window)
    assert ctl.set_fit("height")
    for height in (800, 650, 950):
        viewer_window.resize(1200, height)
        QApplication.processEvents()
        qtbot.wait(30)
        QApplication.processEvents()
        r = view.page_rect(view.current_page)
        assert abs(r.height() - (view.viewport().height() - 2 * view.MARGIN)) <= 2
    view.zoom_in()
    assert ctl.fit_of(view) is None
    z = view.zoom
    viewer_window.resize(1200, 700)
    QApplication.processEvents()
    qtbot.wait(30)
    assert view.zoom == pytest.approx(z)


def test_fit_content_fills_view_with_drawn_objects(viewer_window, tmp_path, qtbot):
    """«Adatta al contenuto» usa l'ingombro degli oggetti visibili: un riquadro disegnato al
    centro di una pagina A4 riempie la vista lungo la dimensione limitante."""
    path = tmp_path / "riquadro.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=595.28, height=841.89)
    box = pymupdf.Rect(200, 300, 400, 400)
    page.draw_rect(box, color=(0, 0, 0), fill=(0.8, 0.8, 0.8))
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    ctl = _ctl(viewer_window)
    assert ctl.set_fit("content")
    qtbot.wait(50)              # le barre di scorrimento cambiano la vista: l'adattamento si riapplica
    QApplication.processEvents()
    from foliant.modules.viewer.zoomfit import content_bbox
    bbox = content_bbox(view.document.pdf[0])          # il rettangolo più il tratto del contorno
    assert bbox.contains(box) and bbox.width < box.width + 3
    vr = view.page_to_view(0, bbox)
    vw, vh = view.viewport().width(), view.viewport().height()
    m = 2 * view.MARGIN
    assert abs(vr.width() - (vw - m)) <= 3 or abs(vr.height() - (vh - m)) <= 3
    assert vr.left() >= 0 and vr.right() <= vw and vr.top() >= 0 and vr.bottom() <= vh
    assert abs(vr.center().x() - vw / 2) <= 3 and abs(vr.center().y() - vh / 2) <= 3


def test_fit_content_on_empty_page(viewer_window, tmp_path):
    path = tmp_path / "vuota.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    viewer_window.open_path(str(path))
    assert not _ctl(viewer_window).set_fit("content")


def test_fit_actions_in_view_menu(viewer_window):
    ctl = _ctl(viewer_window)
    assert ctl.act_height.text() == "Adatta all'altezza" and ctl.act_content.text() == "Adatta al contenuto"
