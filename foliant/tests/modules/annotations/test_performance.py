"""Prestazioni: pagina con 2000 annotazioni (NFR-05, ANN-08/34)."""

import time

import pymupdf

from foliant.modules.annotations import engine


def _many(tmp_path, n=2000):
    path = tmp_path / "duemila.pdf"
    d = pymupdf.open()
    p = d.new_page()
    for i in range(n):
        a = p.add_rect_annot((10 + (i % 50) * 11, 10 + (i // 50) * 18, 18 + (i % 50) * 11, 22 + (i // 50) * 18))
        a.set_info(content=f"nota {i}")
        a.update()
    d.save(str(path))
    return str(path)


def _repaint_ms(view):
    view.flush_rendering()
    t = time.perf_counter()
    view.viewport().repaint()
    return (time.perf_counter() - t) * 1000


def test_repaint_fast_with_2000_annotations(window, tmp_path, monkeypatch):
    monkeypatch.setattr(window.ctx, "error", lambda *a: None)
    view = window.open_path(_many(tmp_path))
    module = window.ctx.annotations
    window.set_mode("commenta")
    ctrl = module.ctrl
    note = ctrl.apply("n", engine.create_note, 0, (300, 780), "aperta", engine.Style(), "A")
    ctrl.toggle_popup(0, note)
    _repaint_ms(view)                         # primo disegno (cache delle immagini)
    assert _repaint_ms(view) < 300
    some = ctrl.hit(view, 0, pymupdf.Point(14, 16), 2)
    assert some is not None
    ctrl.select([(0, some)], view)
    ctrl.set_style(color=(0, 0, 1))           # modifica
    assert _repaint_ms(view) < 300
    view.document.undo()                      # annulla
    assert _repaint_ms(view) < 300
    t = time.perf_counter()
    ctrl.hit(view, 0, pymupdf.Point(300, 300), 2)
    ctrl.hit(view, 0, pymupdf.Point(301, 301), 2)
    assert (time.perf_counter() - t) < 0.3     # passaggio del mouse
    view.document._saved_state = view.document._state
