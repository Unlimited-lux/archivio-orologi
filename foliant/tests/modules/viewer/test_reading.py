"""Test di modalità lettura, schermo intero (VIS-31) e scorrimento automatico (VIS-33)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from foliant.api import VIEW_SINGLE
from foliant.modules.viewer.reading import MODE_CONTINUOUS, MODE_PAGES, AutoScrollEngine, chrome_widgets

from .conftest import part


@pytest.fixture
def reading(viewer_window, long_pdf, qtbot):
    window = viewer_window
    view = window.open_path(long_pdf)
    ctrl = part(window, "reading")
    yield window, view, ctrl
    ctrl.stop_autoscroll()
    if ctrl.reading_active:
        ctrl.set_reading(False)
    if window.isFullScreen():
        window.toggle_fullscreen()


def _visibility(window):
    return {k: not w.isHidden() for k, w in chrome_widgets(window).items()}


def _move_mouse(widget, pos=QPointF(200, 200)):
    ev = QMouseEvent(QEvent.Type.MouseMove, pos, widget.mapToGlobal(pos), Qt.MouseButton.NoButton,
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, ev)


# ------------------------------------------------------------------ logica pura
def test_engine_fractional_accumulation():
    e = AutoScrollEngine(50)
    total = sum(e.advance_pixels(1 / 60) for _ in range(600))   # 10 s a 60 Hz
    assert abs(total - 500) <= 1
    e.paused = True
    assert e.advance_pixels(5) == 0
    p = AutoScrollEngine(mode=MODE_PAGES, page_seconds=4)
    assert p.advance_pages(3.9) == 0 and p.advance_pages(0.2) == 1


# ------------------------------------------------------------------ modalità lettura
def test_reading_mode_keeps_page_and_zoom_and_restores_bars(reading, qtbot):
    window, view, ctrl = reading
    view.set_zoom(1.2)
    view.go_to_page(7)
    qtbot.wait(20)
    before = _visibility(window)
    assert before["header"] and before["status"] and before["context"]
    top = view.verticalScrollBar().value()

    ctrl.act_reading.trigger()
    qtbot.wait(30)
    assert ctrl.reading_active and ctrl.act_reading.isChecked()
    assert not any(_visibility(window).values())
    assert view.current_page == 7 and view.zoom == pytest.approx(1.2)
    assert ctrl.hint.isVisible()

    qtbot.keyClick(view, Qt.Key.Key_Escape)   # Esc esce
    qtbot.wait(30)
    assert not ctrl.reading_active and not ctrl.act_reading.isChecked()
    assert _visibility(window) == before
    assert view.current_page == 7 and view.zoom == pytest.approx(1.2)
    assert abs(view.verticalScrollBar().value() - top) <= 2


def test_reading_mode_shortcut_toggles_and_survives_tab_change(reading, qtbot, sample_pdf):
    window, view, ctrl = reading
    assert ctrl.act_reading.shortcut().toString() == "Ctrl+Shift+L"
    ctrl.act_reading.trigger()
    window.open_path(sample_pdf)          # cambio scheda: il nucleo rimostrerebbe le barre
    qtbot.wait(30)
    assert window.context_stack.isHidden() and window.sidebar.isHidden() and window.header.isHidden()
    ctrl.act_reading.trigger()
    qtbot.wait(10)
    assert not window.header.isHidden() and not window.context_stack.isHidden()


def test_reading_restores_exact_previous_visibility(reading, qtbot):
    window, view, ctrl = reading
    window.sidebar.hide()                 # disposizione personalizzata
    window.statusBar().hide()
    before = _visibility(window)
    ctrl.set_reading(True)
    ctrl.set_reading(False)
    assert _visibility(window) == before


# ------------------------------------------------------------------ schermo intero
def test_fullscreen_hides_cursor_and_bar_after_inactivity(reading, qtbot):
    window, view, ctrl = reading
    assert ctrl.hide_delay_ms == 3000
    ctrl.hide_delay_ms = 60
    window.toggle_fullscreen()
    qtbot.waitUntil(lambda: ctrl.fullscreen_active)
    assert window.header.isHidden() and window.statusBar().isHidden()
    _move_mouse(view.viewport())
    assert ctrl.bar.isVisible()
    assert "/ 30" in ctrl.bar.page_label.text()
    qtbot.waitUntil(lambda: ctrl.cursor_hidden, timeout=2000)
    assert not ctrl.bar.isVisible()
    assert view.viewport().cursor().shape() == Qt.CursorShape.BlankCursor
    _move_mouse(view.viewport())
    assert not ctrl.cursor_hidden and ctrl.bar.isVisible()
    assert view.viewport().cursor().shape() != Qt.CursorShape.BlankCursor
    # Esc esce e ripristina
    ctrl.escape()
    qtbot.waitUntil(lambda: not window.isFullScreen())
    assert not ctrl.fullscreen_active and not ctrl.bar.isVisible()
    assert not window.header.isHidden() and not window.statusBar().isHidden()


def test_fullscreen_bar_zoom_buttons(reading, qtbot):
    window, view, ctrl = reading
    window.toggle_fullscreen()
    qtbot.waitUntil(lambda: ctrl.fullscreen_active)
    _move_mouse(view.viewport())
    view.set_zoom(1.0)
    ctrl.bar.zoom_in_button.click()
    assert view.zoom > 1.0
    ctrl.bar.exit_button.click()
    qtbot.waitUntil(lambda: not window.isFullScreen())


def test_pagemode_fullscreen_asks_confirmation(viewer_window, tmp_path, qtbot, monkeypatch):
    window = viewer_window
    ctrl = part(window, "reading")
    path = tmp_path / "fs.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.xref_set_key(doc.pdf_catalog(), "PageMode", "/FullScreen")
    doc.save(str(path))
    doc.close()
    asked = []
    monkeypatch.setattr(ctrl, "ask_fullscreen", lambda: asked.append(1) or False)
    window.open_path(str(path))
    qtbot.waitUntil(lambda: bool(asked))
    assert not window.isFullScreen()              # rifiutato: nessun passaggio
    monkeypatch.setattr(ctrl, "ask_fullscreen", lambda: True)
    copy = tmp_path / "fs2.pdf"
    copy.write_bytes(path.read_bytes())
    window.open_path(str(copy))
    qtbot.waitUntil(lambda: window.isFullScreen())
    assert ctrl.fullscreen_active
    ctrl.exit_fullscreen()
    assert not window.isFullScreen()


# ------------------------------------------------------------------ scorrimento automatico
def test_autoscroll_50px_per_second_for_10_seconds(reading):
    window, view, ctrl = reading
    ctrl.set_scroll_mode(MODE_CONTINUOUS)
    ctrl.set_speed(50)
    view.go_to_page(0)
    vbar = view.verticalScrollBar()
    vbar.setValue(0)
    assert ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()          # tempo simulato
    for _ in range(100):
        ctrl.tick(0.1)
    assert vbar.value() == pytest.approx(500, rel=0.05)
    assert ctrl.indicator.isVisible() and "50" in ctrl.indicator.text()


def test_space_pauses_and_resumes_from_same_position(reading, qtbot):
    window, view, ctrl = reading
    ctrl.set_scroll_mode(MODE_CONTINUOUS)
    ctrl.set_speed(50)
    view.verticalScrollBar().setValue(0)
    ctrl.act_autoscroll.trigger()
    assert ctrl.scrolling
    ctrl.scroll_timer.stop()
    ctrl.tick(2)
    pos = view.verticalScrollBar().value()
    assert pos == 100
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert ctrl.paused and "pausa" in ctrl.indicator.text()
    ctrl.tick(5)
    assert view.verticalScrollBar().value() == pos
    qtbot.keyClick(view, Qt.Key.Key_Space)
    assert not ctrl.paused
    ctrl.tick(1)
    assert view.verticalScrollBar().value() == pos + 50


def test_speed_with_arrows_and_saved(reading, qtbot):
    window, view, ctrl = reading
    ctrl.set_speed(50)
    ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()
    qtbot.keyClick(view, Qt.Key.Key_Down)
    assert ctrl.engine.speed == 60
    qtbot.keyClick(view, Qt.Key.Key_Up)
    qtbot.keyClick(view, Qt.Key.Key_Up)
    assert ctrl.engine.speed == 40
    assert float(window.ctx.settings().value("viewer/autoscroll/speed")) == 40
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert not ctrl.scrolling and not ctrl.act_autoscroll.isChecked()


def test_manual_scroll_pauses_and_click_stops(reading, qtbot):
    window, view, ctrl = reading
    ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() + 300)
    assert ctrl.paused
    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    assert not ctrl.scrolling


def test_autoscroll_stops_at_end_or_restarts(reading):
    window, view, ctrl = reading
    vbar = view.verticalScrollBar()
    ctrl.set_scroll_mode(MODE_CONTINUOUS)
    ctrl.set_speed(500)
    view.go_to_page(29)
    vbar.setValue(vbar.maximum() - 100)
    ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()
    ctrl.tick(1)
    assert vbar.value() == vbar.maximum()
    assert not ctrl.scrolling                 # ultima pagina interamente visibile
    ctrl.set_loop(True)
    vbar.setValue(vbar.maximum() - 100)
    ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()
    ctrl.tick(1)
    assert ctrl.scrolling and vbar.value() == 0
    ctrl.set_loop(False)


def test_single_page_layout_uses_pages(reading):
    window, view, ctrl = reading
    view.set_view_mode(VIEW_SINGLE)
    view.go_to_page(0)
    ctrl.set_scroll_mode(MODE_CONTINUOUS)
    ctrl.engine.set_page_seconds(5)
    ctrl.start_autoscroll()
    ctrl.scroll_timer.stop()
    assert ctrl.effective_mode() == MODE_PAGES
    ctrl.tick(4.9)
    assert view.current_page == 0
    ctrl.tick(0.2)
    assert view.current_page == 1
    s = window.ctx.settings()
    ctrl.set_scroll_mode(MODE_PAGES)
    assert s.value("viewer/autoscroll/mode") == MODE_PAGES
    ctrl.set_scroll_mode(MODE_CONTINUOUS)


def test_pagemode_fullscreen_prompt_is_not_blocking(viewer_window, tmp_path, qtbot):
    import pymupdf
    from PySide6.QtWidgets import QMessageBox
    path = tmp_path / "fs.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.xref_set_key(doc.pdf_catalog(), "PageMode", "/FullScreen")
    doc.save(str(path))
    ctl = viewer_window.ctx.viewer_parts["reading"]
    viewer_window.open_path(str(path))
    qtbot.waitUntil(lambda: getattr(ctl, "fullscreen_prompt", None) is not None, timeout=2000)
    note = ctl.fullscreen_prompt                 # avviso non bloccante del nucleo (ctx.notify)
    assert note.is_open and not viewer_window.isFullScreen()
    from PySide6.QtWidgets import QPushButton
    [b for b in note.findChildren(QPushButton) if b.text() == "Schermo intero"][0].click()
    qtbot.waitUntil(viewer_window.isFullScreen, timeout=2000)
    viewer_window.toggle_fullscreen()
