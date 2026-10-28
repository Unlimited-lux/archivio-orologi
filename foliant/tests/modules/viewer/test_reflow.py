"""VIS-36 — Modalità riflusso."""

from __future__ import annotations

import pymupdf


def _ctl(w):
    return w.ctx.viewer_parts["reflow"]


def _doc(tmp_path, pages=8):
    path = tmp_path / "riflusso.pdf"
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 80), f"Capitolo {i + 1}", fontsize=24)
        page.insert_text((72, 150), f"Sezione {i + 1}.1", fontsize=16)
        y = 200
        for k in range(5):
            tw = pymupdf.TextWriter(page.rect)
            tw.fill_textbox(pymupdf.Rect(72, y, 520, y + 70),
                            f"Paragrafo {k + 1} della pagina {i + 1}: " + "testo del corpo " * 12, fontsize=10)
            tw.write_text(page)
            y += 90
    doc.save(str(path))
    return str(path)


def test_single_column_no_horizontal_scroll_and_headings(viewer_window, tmp_path):
    viewer_window.resize(600, 700)
    viewer_window.open_path(_doc(tmp_path))
    ctl = _ctl(viewer_window)
    assert ctl.enter()
    assert ctl.active
    kinds = {b.kind for b in ctl.blocks}
    assert {"h1", "h2", "p"} <= kinds
    html = ctl.widget.browser.toHtml()
    assert "Capitolo 1" in ctl.widget.browser.toPlainText()
    browser = ctl.widget.browser
    assert browser.horizontalScrollBar().maximum() == 0
    ctl.change_font(1)
    assert ctl.font_pt > 12
    ctl.leave()
    assert not ctl.active


def test_exit_returns_to_paragraph_page(viewer_window, tmp_path):
    view = viewer_window.open_path(_doc(tmp_path))
    ctl = _ctl(viewer_window)
    ctl.enter()
    target = [i for i, b in enumerate(ctl.blocks) if b.page == 6 and b.text.startswith("Paragrafo 4")][0]
    ctl.widget.browser.scrollToAnchor(f"b{target}")
    viewer_window.ctx.window.repaint()
    assert ctl.current_block() == target or ctl.blocks[ctl.current_block()].page == 6
    ctl.leave()
    assert view.current_page == 6


def test_no_text_suggests_ocr(viewer_window, tmp_path, monkeypatch):
    path = tmp_path / "vuoto.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(str(path))
    viewer_window.open_path(str(path))
    msgs = []
    monkeypatch.setattr(viewer_window.ctx, "status", lambda m, t=5000: msgs.append(m))
    assert not _ctl(viewer_window).enter()
    assert "OCR" in msgs[0]
