"""VIS-07 — File danneggiati e contenuti non supportati."""

from __future__ import annotations

import pymupdf


def _broken_pdf(tmp_path):
    d = pymupdf.open()
    for i in range(3):
        d.new_page().insert_text((72, 100), f"Pagina {i + 1}")
    b = d.tobytes()
    i = b.rfind(b"xref")
    path = tmp_path / "rotto.pdf"
    path.write_bytes(b[:i] + b"xref\n0 2\ngarbage\n" + b[i + 40:])
    return str(path)


def test_rebuilt_file_shows_bar_and_repair(viewer_window, tmp_path, monkeypatch):
    view = viewer_window.open_path(_broken_pdf(tmp_path))
    assert view.page_count == 3
    ctl = viewer_window.ctx.viewer_parts["integrity"]
    assert ctl.BANNER_REPAIRED in viewer_window.ctx.banners(view)     # barra di avviso del nucleo
    bar = viewer_window.tabs.page(view).banners
    assert "ricostruito" in bar.text_label.text() and bar.isVisible()
    out = tmp_path / "riparato.pdf"
    monkeypatch.setattr(viewer_window.ctx, "ask_save_path", lambda *a, **k: str(out))
    [b for b in bar.action_buttons if "Ripara" in b.text()][0].click()
    assert out.exists()
    reopened = pymupdf.open(str(out))
    assert not reopened.is_repaired and reopened.page_count == 3
    assert ctl.BANNER_REPAIRED not in viewer_window.ctx.banners(view)


def test_healthy_file_has_no_bar(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    assert not viewer_window.ctx.banners(view)


def test_deferred_callbacks_after_window_destroyed(qtbot, settings, tmp_path):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    from foliant.ui.main_window import MainWindow
    win = MainWindow(settings)
    view = win.open_path(_broken_pdf(tmp_path))
    view.go_to_page(1)                       # programma un aggiornamento differito della navigazione
    view.resize(500, 400)                    # e un'impaginazione differita della barra informativa
    for v in win.views():
        v.document._saved_state = v.document._state
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()             # nessuna eccezione dai callback tardivi
