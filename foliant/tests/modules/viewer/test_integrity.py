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


def test_repaired_file_shows_a_single_banner(window, tmp_path):
    """Tutti i moduli caricati: xref rotta → un solo avviso sulla riparazione, quello della
    conversione con «Salva copia riparata»; il viewer non ne aggiunge un secondo."""
    view = window.open_path(_broken_pdf(tmp_path))
    assert view.page_count == 3
    ids = window.ctx.banners(view)
    assert [i for i in ids if "ripar" in i or "repair" in i] == ["riparato"]
    bar = window.tabs.page(view).banners
    assert "danneggiato" in bar.text_label.text()
    texts = [b.text() for b in bar.action_buttons]
    assert len([t for t in texts if "ripara" in t.lower()]) == 1


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
