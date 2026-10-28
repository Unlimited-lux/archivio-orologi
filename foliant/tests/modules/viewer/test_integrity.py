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


def _truncated(tmp_path, keep=0.6):
    """PDF di 10 pagine troncato al 60% della lunghezza (download interrotto)."""
    doc = pymupdf.open()
    for i in range(10):
        p = doc.new_page()
        p.insert_text((72, 100), f"Pagina {i + 1} " + "testo " * 50, fontsize=11)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), 0)
        pix.clear_with(180)                   # contenuto fisso: il file troncato è sempre lo stesso
        p.insert_image(pymupdf.Rect(72, 200, 400, 500), pixmap=pix)
    data = doc.tobytes(garbage=0, deflate=True)
    path = tmp_path / "troncato.pdf"
    path.write_bytes(data[: int(len(data) * keep)])
    return str(path)


def test_truncated_file_shows_intact_pages_and_placeholders(viewer_window, tmp_path, qtbot):
    """Dato un PDF troncato al 60%, quando viene aperto, le pagine integre sono visualizzate e le
    altre mostrano il segnaposto «Pagina non leggibile»; un avviso le elenca."""
    from foliant.modules.viewer.integrity import page_ranges, unreadable_pages
    from foliant.ui import theme
    view = viewer_window.open_path(_truncated(tmp_path))
    assert view.page_count == 10
    ctl = viewer_window.ctx.viewer_parts["integrity"]
    with view.document.lock:
        broken = unreadable_pages(view.document.pdf)
    assert broken and broken[0] >= 5 and broken == list(range(broken[0], 10))
    assert ctl.unreadable[view] == set(broken)
    assert ctl.BANNER_UNREADABLE in viewer_window.ctx.banners(view)
    assert page_ranges(broken) == f"{broken[0] + 1}–10"
    assert not ctl.is_unreadable(view, 0) and ctl.is_unreadable(view, 9)
    # segnaposto disegnato sopra la pagina illeggibile; le pagine integre hanno il loro testo
    view.go_to_page(9)
    qtbot.wait(50)
    img = view.viewport().grab().toImage()
    r = view.page_rect(9).intersected(view.viewport().rect().toRectF())
    c = img.pixelColor(int(r.center().x()), int(r.center().y()))
    assert c.name() != "#ffffff"           # non la pagina bianca della resa
    assert abs(c.lightness() - theme.color("surface_alt").lightness()) < 40
    assert "Pagina 1" in view.document.pdf[0].get_text()
    # miniature: segnaposto per le pagine illeggibili
    thumbs = viewer_window.ctx.viewer_parts["thumbnails"].panel
    viewer_window.ctx.show_sidebar_panel("thumbnails")
    thumbs._render_one(9)
    assert 9 in thumbs.failed
    view.document._saved_state = view.document._state


def test_page_ranges_format():
    from foliant.modules.viewer.integrity import page_ranges
    assert page_ranges([6, 7, 8, 9, 12]) == "7–10, 13"
    assert page_ranges([0]) == "1"


def test_repaired_copy_reopens_without_banners(viewer_window, tmp_path, monkeypatch):
    """Dato un file ricostruito, quando l'utente salva la copia riparata, il nuovo file si riapre
    senza avviso di riparazione e la sua tabella xref è coerente."""
    view = viewer_window.open_path(_truncated(tmp_path))
    out = tmp_path / "riparato_ok.pdf"
    monkeypatch.setattr(viewer_window.ctx, "ask_save_path", lambda *a, **k: str(out))
    bar = viewer_window.tabs.page(view).banners
    [b for b in bar.action_buttons if "ripara" in b.text().lower()][0].click()
    assert out.exists()
    check = pymupdf.open(str(out))
    assert not check.is_repaired and check.page_count == 10
    again = viewer_window.open_path(str(out))
    ids = viewer_window.ctx.banners(again)
    assert "riparato" not in ids and "viewer.unreadable" not in ids
    view.document._saved_state = view.document._state
