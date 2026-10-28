"""Test della presentazione (VIS-32)."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication

from foliant.modules.viewer.presentation_engine import (
    SCREEN_BLACK, SCREEN_PAGE, SCREEN_WHITE, TRANSITION_FADE, TRANSITION_NONE, PageTiming, PresentationOptions,
    PresentationState, resolve_transition,
)

from .conftest import part


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def pres(viewer_window, long_pdf):
    window = viewer_window
    view = window.open_path(long_pdf)
    ctrl = part(window, "presentation")
    yield window, view, ctrl
    ctrl.stop()


def _dur_pdf(tmp_path, dur="5", pages=4, trans=None):
    path = tmp_path / "dur.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Diapositiva {i + 1}", fontsize=30)
        doc.xref_set_key(page.xref, "Dur", dur)
        if trans:
            doc.xref_set_key(page.xref, "Trans", trans)
    doc.save(str(path))
    doc.close()
    return str(path)


# ------------------------------------------------------------------ motore puro
def test_engine_navigation_and_jump():
    st = PresentationState(10)
    st.key("space")
    st.key("right")
    st.key("pagedown")
    assert st.page == 3
    st.key("left")
    st.key("backspace")
    assert st.page == 1
    st.key("7", "7")
    st.key("return")
    assert st.page == 6
    st.key("1", "1")
    st.key("2", "2")
    st.key("enter")
    assert st.page == 9          # oltre la fine: ultima pagina
    st.key("home")
    assert st.page == 0
    st.key("end")
    assert st.page == 9
    st.key("space")
    assert st.page == 9 and st.finished


def test_engine_black_white_any_key_returns_same_page():
    st = PresentationState(5, page=2)
    st.key("b")
    assert st.screen == SCREEN_BLACK
    assert st.key("right") == "unblank"
    assert st.screen == SCREEN_PAGE and st.page == 2
    st.key("w")
    assert st.screen == SCREEN_WHITE
    st.key("x")
    assert st.screen == SCREEN_PAGE and st.page == 2


def test_engine_dur_timing_and_loop():
    timings = [PageTiming(duration=5.0) for _ in range(3)]
    st = PresentationState(3, timings=timings)
    assert st.tick(4.7) == 0 and st.page == 0
    assert st.tick(0.3) == 1 and st.page == 1
    st.tick(5.0)
    assert st.page == 2
    st.tick(50)
    assert st.page == 2 and st.finished
    loop = PresentationState(3, timings=[PageTiming(duration=2.0)] * 3, options=PresentationOptions(loop=True))
    loop.tick(6.0)
    assert loop.page == 0


def test_engine_user_interval_and_transitions():
    st = PresentationState(3, options=PresentationOptions(use_document_timing=False, interval=3.0))
    st.tick(3.0)
    assert st.page == 1
    manual = PresentationState(3)
    manual.tick(100)
    assert manual.page == 0
    assert resolve_transition("/Glitter", 1.0) == (TRANSITION_FADE, 1.0)   # non supportata -> dissolvenza
    assert resolve_transition("/R", 1.0)[0] == TRANSITION_NONE
    assert resolve_transition(None, None)[0] == TRANSITION_NONE


# ------------------------------------------------------------------ finestra
def test_actions_registered(pres):
    window, view, ctrl = pres
    assert ctrl.act_start.shortcut().toString() == "F5"
    assert ctrl.act_current.shortcut().toString() == "Shift+F5"
    assert ctrl.act_start in window.ctx.mode("leggi").toolbar.actions()
    assert ctrl.act_start.isEnabled()


def test_forward_backward_and_numeric_jump(pres, qtbot):
    window, view, ctrl = pres
    win = ctrl.start(False)
    assert win is not None and win.isVisible() and win.isFullScreen()
    assert win.state.page == 0
    qtbot.keyClick(win, Qt.Key.Key_Space)
    qtbot.keyClick(win, Qt.Key.Key_Right)
    qtbot.keyClick(win, Qt.Key.Key_PageDown)
    assert win.state.page == 3
    qtbot.keyClick(win, Qt.Key.Key_Left)
    qtbot.keyClick(win, Qt.Key.Key_Backspace)
    assert win.state.page == 1
    qtbot.mouseClick(win, Qt.MouseButton.LeftButton)
    assert win.state.page == 2
    qtbot.keyClicks(win, "15")
    qtbot.keyClick(win, Qt.Key.Key_Return)
    assert win.state.page == 14
    qtbot.keyClick(win, Qt.Key.Key_End)
    assert win.state.page == 29
    qtbot.keyClick(win, Qt.Key.Key_Home)
    assert win.state.page == 0


def test_start_from_current_page_and_next_page_cached(pres, qtbot):
    window, view, ctrl = pres
    view.go_to_page(5)
    win = ctrl.start(True)
    assert win.state.page == 5
    win.repaint()
    qtbot.waitUntil(lambda: 6 in win.cached_pages())
    img = win.image(5)
    assert img.devicePixelRatio() == pytest.approx(win.devicePixelRatioF())
    r = win.image_rect(img)
    assert r.width() <= win.width() + 1 and r.height() <= win.height() + 1
    assert max(r.width() / win.width(), r.height() / win.height()) > 0.98   # adattata allo schermo


def test_black_then_any_key_returns_same_page(pres, qtbot):
    window, view, ctrl = pres
    view.go_to_page(4)
    win = ctrl.start(True)
    qtbot.keyClick(win, Qt.Key.Key_B)
    assert win.state.screen == SCREEN_BLACK
    win.repaint()
    qtbot.keyClick(win, Qt.Key.Key_Space)
    assert win.state.screen == SCREEN_PAGE and win.state.page == 4
    qtbot.keyClick(win, Qt.Key.Key_W)
    assert win.state.screen == SCREEN_WHITE
    qtbot.keyClick(win, Qt.Key.Key_A)
    assert win.state.screen == SCREEN_PAGE and win.state.page == 4


def test_pen_stroke_does_not_modify_document(pres, qtbot):
    window, view, ctrl = pres
    doc = view.document
    version = doc.version
    win = ctrl.start(False)
    qtbot.keyClick(win, Qt.Key.Key_P)
    assert win.pen
    qtbot.mousePress(win, Qt.MouseButton.LeftButton, pos=win.rect().center())
    for dx in range(0, 120, 10):
        qtbot.mouseMove(win, win.rect().center() + QPoint(dx, dx // 2))
    qtbot.mouseRelease(win, Qt.MouseButton.LeftButton, pos=win.rect().center())
    assert win.state.page == 0                # in modalità penna il clic non avanza
    assert len(win.strokes[0]) == 1 and len(win.strokes[0][0]) > 1
    win.repaint()
    qtbot.keyClick(win, Qt.Key.Key_E)
    assert not win.strokes.get(0)
    qtbot.keyClick(win, Qt.Key.Key_L)
    assert win.laser and not win.pen
    assert win.cursor().shape() == Qt.CursorShape.BlankCursor
    ctrl.stop()
    assert not doc.is_modified and doc.version == version and not doc.can_undo


def test_escape_returns_to_view_on_reached_page(pres, qtbot):
    window, view, ctrl = pres
    win = ctrl.start(False)
    qtbot.keyClicks(win, "12")
    qtbot.keyClick(win, Qt.Key.Key_Return)
    qtbot.keyClick(win, Qt.Key.Key_Escape)
    assert ctrl.window is None and not win.isVisible()
    assert view.current_page == 11


def test_dur_5_seconds_per_page(viewer_window, tmp_path):
    window = viewer_window
    view = window.open_path(_dur_pdf(tmp_path, "5", trans="<</S/Glitter/D 0.5>>"))
    ctrl = part(window, "presentation")
    ctrl.set_options(PresentationOptions(use_document_timing=True))
    clock = FakeClock()
    ctrl.clock = clock
    try:
        win = ctrl.start(False)
        win.timer.stop()                       # tempo simulato
        assert win.state.page_duration() == pytest.approx(5.0)
        assert win.state.transition_for(1) == (TRANSITION_FADE, 0.5)   # /Glitter -> dissolvenza
        # Visibile per 5 s ± 0,3: a 4,7 s ancora sulla prima, a 5,3 s sulla seconda.
        clock.t += 4.7
        win._on_tick()
        assert win.state.page == 0
        clock.t += 0.6
        win._on_tick()
        assert win.state.page == 1
        clock.t += 4.6
        win._on_tick()
        assert win.state.page == 1
        clock.t += 0.4
        win._on_tick()
        assert win.state.page == 2
        assert win.TICK_MS <= 100              # risoluzione del timer reale entro la tolleranza
    finally:
        ctrl.stop()


def test_single_screen_has_no_presenter_view(pres):
    window, view, ctrl = pres
    assert len(QGuiApplication.screens()) == 1
    ctrl.start(False)
    assert ctrl.presenter is None


def test_two_screens_presenter_view(pres, monkeypatch, qtbot):
    window, view, ctrl = pres
    screen = QGuiApplication.primaryScreen()
    monkeypatch.setattr(QGuiApplication, "screens", staticmethod(lambda: [screen, screen]))
    win = ctrl.start(False)
    assert ctrl.presenter is not None and ctrl.presenter.isVisible()
    qtbot.keyClick(win, Qt.Key.Key_Right)
    texts = ctrl.presenter.texts()
    assert "2" in texts["current"] and "3" in texts["next"]
    assert ":" in texts["elapsed"] and ":" in texts["clock"]
    ctrl.presenter.repaint()
    ctrl.stop()
    assert ctrl.presenter is None


def test_options_saved_in_settings(pres):
    window, view, ctrl = pres
    ctrl.set_options(PresentationOptions(use_document_timing=False, interval=7.5, transition=TRANSITION_NONE,
                                         loop=True))
    opts = ctrl.options()
    assert opts.interval == 7.5 and opts.loop and not opts.use_document_timing
    assert opts.transition == TRANSITION_NONE
