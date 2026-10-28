"""ANN-33: aggancio e mirino delle misure, angolo, conversione in misura, CSV con separatore."""

import pymupdf
import pytest

from foliant.modules.annotations import engine

from .conftest import drag, move


def _vector_pdf(tmp_path, name="vettoriale.pdf"):
    path = tmp_path / name
    d = pymupdf.open()
    page = d.new_page(width=595, height=842)
    sh = page.new_shape()
    sh.draw_line((100, 100), (300, 100))
    sh.finish(color=(0, 0, 0), width=1)
    sh.draw_line((200, 50), (200, 150))
    sh.finish(color=(0, 0, 0), width=1)
    sh.commit()
    d.save(str(path))
    return str(path)


def test_snap_targets_engine():
    d = pymupdf.open()
    page = d.new_page()
    sh = page.new_shape()
    sh.draw_line((100, 100), (300, 100))
    sh.finish(color=(0, 0, 0))
    sh.draw_line((200, 50), (200, 150))
    sh.finish(color=(0, 0, 0))
    sh.commit()
    engine.create_shape(d, 0, engine.POLYLINE, [(400, 400), (450, 420), (500, 400)], engine.Style(), "A")
    t = engine.snap_targets(d, 0)
    assert engine.snap(t, (297, 102), 5) == (pymupdf.Point(300, 100), engine.SNAP_END)
    assert engine.snap(t, (198, 99), 5)[1] == engine.SNAP_CROSS
    assert engine.snap(t, (202, 101), 5, (engine.SNAP_MID,)) == (pymupdf.Point(200, 100), engine.SNAP_MID)
    assert engine.snap(t, (130, 103), 5) == (pymupdf.Point(130, 100), engine.SNAP_PATH)
    p, kind = engine.snap(t, (448, 421), 5)                 # vertice di un'altra annotazione
    assert p == pymupdf.Point(450, 420) and kind in (engine.SNAP_ANNOT, engine.SNAP_END)
    assert engine.snap(t, (297, 102), 5, (engine.SNAP_PATH,))[1] == engine.SNAP_PATH   # tipi disattivati
    assert engine.snap(t, (50, 700), 5) is None
    # pagina senza grafica vettoriale: solo le annotazioni
    page2 = d.new_page()
    page2.insert_image(page2.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), 0))
    assert engine.snap_targets(d, 1).points == []


def test_distance_snaps_to_vector_endpoint(comments, tmp_path):
    """Clic entro 5 px dall'estremo destro della linea (100,100)-(300,100): la misura finisce in (300,100)."""
    window, _view, module = comments
    v = window.open_path(_vector_pdf(tmp_path))
    window.set_mode("commenta")
    v.set_zoom(1.0)
    module.ctrl.snap_targets(v, 0, wait=True)
    module.activate(engine.DISTANCE)
    drag(v, 0, (103, 300), (296, 103))
    info, = module.ctrl.annotations()
    pdf = v.document.pdf
    page = pdf[0]
    a = engine.find_annot(page, info.xref)
    assert tuple(a.vertices[1]) == pytest.approx((300, 100), abs=0.01)
    assert tuple(a.vertices[0]) == pytest.approx((103, 300), abs=0.01)    # lontano da tutto: libero


def test_crosshair_shows_real_coordinates(comments, tmp_path, monkeypatch):
    window, view, module = comments
    module.ctrl.set_scale(engine.Scale("mm", 25.4 / 72, "", True))
    notes = []
    monkeypatch.setattr(window.ctx, "status", lambda m, *a: notes.append(m))
    module.activate(engine.DISTANCE)
    move(view, 0, 72, 144, pressed=False)
    assert notes and "25,40 mm" in notes[-1] and "50,80 mm" in notes[-1]
    tool = module.tools[engine.DISTANCE]
    assert tool._cross is not None and tool._cross[0] == 0


def test_snap_kinds_setting(comments):
    window, view, module = comments
    module.ctrl.set_snap_kind(engine.SNAP_MID, False)
    assert engine.SNAP_MID not in module.ctrl.snap_kinds()
    module.ctrl.set_snap_kind(engine.SNAP_MID, True)
    assert set(module.ctrl.snap_kinds()) == set(engine.SNAP_KINDS)
