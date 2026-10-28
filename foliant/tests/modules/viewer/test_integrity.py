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
    bars = ctl.bars[view]
    assert bars and "ricostruito" in bars[0].label.text()
    assert bars[0].isVisible()
    out = tmp_path / "riparato.pdf"
    monkeypatch.setattr(viewer_window.ctx, "ask_save_path", lambda *a, **k: str(out))
    bars[0].button.click()
    assert out.exists()
    reopened = pymupdf.open(str(out))
    assert not reopened.is_repaired and reopened.page_count == 3
    assert view not in ctl.bars or not any(b.isVisible() for b in ctl.bars[view])


def test_healthy_file_has_no_bar(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    assert view not in viewer_window.ctx.viewer_parts["integrity"].bars
