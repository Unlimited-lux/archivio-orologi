"""VIS-23 — Thread di articoli."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, Qt


def _threads_pdf(tmp_path):
    d = pymupdf.open()
    for i in range(6):
        d.new_page(width=595, height=842)
    cat = d.pdf_catalog()
    t, b1, b2, b3 = (d.get_new_xref() for _ in range(4))
    d.update_object(t, f"<</Type/Thread/F {b1} 0 R/I<</Title(Orologi di lusso)>>>>")

    def bead(x, nxt, prv, page, rect):
        d.update_object(x, f"<</Type/Bead/T {t} 0 R/N {nxt} 0 R/V {prv} 0 R/P {d[page].xref} 0 R/R[{rect}]>>")

    bead(b1, b2, b3, 1, "50 400 290 800")
    bead(b2, b3, b1, 1, "300 400 550 800")
    bead(b3, b1, b2, 4, "50 100 550 700")     # catena circolare: torna al primo
    d.xref_set_key(cat, "Threads", f"[{t} 0 R]")
    path = tmp_path / "rivista.pdf"
    d.save(str(path))
    return str(path)


def test_read_articles(tmp_path):
    from foliant.modules.viewer.articles import read_articles
    d = pymupdf.open(_threads_pdf(tmp_path))
    arts = read_articles(d)
    assert len(arts) == 1 and arts[0].title == "Orologi di lusso"
    assert [p for p, _r in arts[0].beads] == [1, 1, 4]
    page, r = arts[0].beads[0]
    assert abs(r.y0 - (842 - 800)) < 0.5 and abs(r.x0 - 50) < 0.5


def test_reading_sequence_and_end(viewer_window, tmp_path, qtbot):
    view = viewer_window.open_path(_threads_pdf(tmp_path))
    ctl = viewer_window.ctx.viewer_parts["articles"]
    assert ctl.panel.list.count() == 1
    ctl.start(0)
    assert view.current_page == 1
    r = view.page_to_view(1, ctl.articles[0].beads[0][1])
    assert abs(r.width() - (view.viewport().width() - 48)) < 4   # adattato in larghezza
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert ctl.reading == (0, 1) and view.current_page == 1
    qtbot.keyClick(view, Qt.Key.Key_Return)
    assert ctl.reading == (0, 2) and view.current_page == 4
    qtbot.keyClick(view, Qt.Key.Key_Return)        # ultimo riquadro: la vista non cambia
    assert ctl.reading == (0, 2) and view.current_page == 4
    qtbot.keyClick(view, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert ctl.reading == (0, 1)
    qtbot.keyClick(view, Qt.Key.Key_Escape)
    assert ctl.reading is None


def test_no_articles(viewer_window, sample_pdf):
    viewer_window.open_path(sample_pdf)
    ctl = viewer_window.ctx.viewer_parts["articles"]
    assert ctl.articles == []
    assert not ctl.panel.empty.isHidden()
