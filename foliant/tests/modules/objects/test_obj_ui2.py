"""Test UI: estrazione in blocco, cattura, permessi, firme, apertura sicura dei collegamenti, guide."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from foliant.modules import objects as O
from foliant.modules.objects import engine as E
from foliant.modules.objects import extract as X
from foliant.modules.objects import extras
from foliant.modules.objects import links as L
from foliant.modules.objects import linkopen as LO

from .test_obj_ui import drag, make_doc, objs, png_bytes, ui, vpos  # noqa: F401


def test_extract_all_flow(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170), (300, 70, 400, 170)]))
    out = tmp_path / "out"
    monkeypatch.setattr(extras, "ask_extract_options", lambda parent, n: X.ExtractOptions())
    monkeypatch.setattr(ui.ctx, "ask_directory", lambda title="": str(out))
    rep = extras.extract_all(ui.ctx, ui.c, view)
    assert len(rep.written) == 2
    assert sorted(p.name for p in out.iterdir()) == ["a_p1_img1.png", "a_p1_img2.png"]
    assert any("Immagini estratte: 2" in m for _t, m in ui.errors)


def test_extract_all_no_images(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    monkeypatch.setattr(extras, "ask_extract_options", lambda parent, n: X.ExtractOptions())
    called = []
    monkeypatch.setattr(ui.ctx, "ask_directory", lambda title="": called.append(1))
    assert extras.extract_all(ui.ctx, ui.c, view) is None
    assert not called


def _capture(ui, view, monkeypatch, a, b, choice):
    tool = O._state["capture_tool"]
    view.set_tool(tool)

    def run(menu, pos):
        for act in menu.actions():
            if act.text() == choice:
                act.trigger()
                return
        raise AssertionError([a.text() for a in menu.actions()])
    monkeypatch.setattr(view, "exec_menu", run)
    drag(view, 0, a, b)


def test_capture_copy_text_and_image(ui, tmp_path, monkeypatch):
    path = tmp_path / "t.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Totale fattura", fontsize=14)
    doc.save(str(path))
    view = ui.open(str(path))
    _capture(ui, view, monkeypatch, (60, 80), (200, 110), "Copia testo")
    assert QGuiApplication.clipboard().text() == "Totale fattura"
    O._state["capture_tool"].dpi = 300
    _capture(ui, view, monkeypatch, (72, 100), (216, 172), "Copia immagine")
    img = QGuiApplication.clipboard().image()
    assert abs(img.width() - 600) <= 2 and abs(img.height() - 300) <= 2
    assert not view.document.is_modified


def test_capture_insert_as_image(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    O._state["capture_tool"].dpi = 150
    _capture(ui, view, monkeypatch, (40, 60), (140, 120), "Inserisci come immagine")
    assert len(objs(view)) == 2


def test_signed_document_confirmation(ui, tmp_path, monkeypatch):
    path = tmp_path / "s.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    cat = doc.pdf_catalog()
    doc.xref_set_key(cat, "AcroForm", "<</Fields[]/SigFlags 3>>")
    doc.save(str(path))
    view = ui.open(str(path))
    asked = []
    monkeypatch.setattr(ui.ctx, "confirm", lambda *a: asked.append(1) or False)
    ui.c.set_selection(view, 0, [0])
    ui.c.translate(view, 10, 0)
    assert asked and not view.document.can_undo
    monkeypatch.setattr(ui.ctx, "confirm", lambda *a: asked.append(2) or True)
    ui.c.translate(view, 10, 0)
    ui.c.translate(view, 10, 0)
    assert asked.count(2) == 1
    assert abs(objs(view)[0].rect.x0 - 70) < 0.01


def test_permission_denied_blocks_changes(ui, tmp_path):
    path = tmp_path / "p.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    doc.save(str(path), encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="", owner_pw="padrone",
             permissions=pymupdf.PDF_PERM_PRINT)
    view = ui.window.open_path(str(path))
    ui.ctx.set_mode("modifica")
    ui.c.set_selection(view, 0, [0])
    ui.c.translate(view, 10, 0)
    assert not view.document.can_undo
    assert ui.errors
    O._update_actions(ui.ctx, ui.c)
    assert not O._state["actions"]["save_image"].isEnabled()


def test_smart_guides_snap_to_other_object(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 300, 150, 350), (300, 400, 350, 450)]))
    ui.c.smart_guides = True
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     vpos(view, 0, 320, 420))
    # bordo sinistro trascinato a 101.5 pt (meno di 5 px dal bordo a 100) -> si aggancia
    drag(view, 0, (320, 420), (121.5, 520))
    assert abs(objs(view)[1].rect.x0 - 100) < 0.01
    view.document.undo()
    ui.c.smart_guides = False
    drag(view, 0, (320, 420), (121.5, 520))
    ui.c.smart_guides = True
    assert abs(objs(view)[1].rect.x0 - 101.5) < 0.6


def test_snap_delta_pure():
    r = pymupdf.Rect(0, 0, 10, 10)
    dx, dy, gx, gy = E.snap_delta(r, 98.5, 0, [100.0], [500.0], 2)
    assert dx == 100 and gx == 100 and gy is None


# ------------------------------------------------------------------ OBJ-41
def test_decide_rules(tmp_path):
    t = L.LinkTarget(kind=L.URI, uri="https://esempio.it/x")
    assert LO.decide(t, 3, None, set()).action == "confirm_url"
    assert LO.decide(t, 3, None, {"esempio.it"}).action == "open_url"
    d = LO.decide(t, 3, None, set(), shown_text="www.banca.it")
    assert d.mismatch == "www.banca.it"
    assert LO.decide(L.LinkTarget(kind=L.URI, uri="javascript:alert(1)"), 3, None, set()).action == "blocked"
    assert LO.decide(L.LinkTarget(kind=L.PAGE, page=19), 40, None, set()).action == "goto"
    assert LO.decide(L.LinkTarget(kind=L.PAGE, page=49), 40, None, set()).action == "not_found"
    assert LO.decide(L.LinkTarget(kind=L.LAUNCH, file="script.sh"), 3, None, set()).action == "blocked"
    doc = tmp_path / "a.pdf"
    (tmp_path / "allegati").mkdir()
    (tmp_path / "allegati" / "b.pdf").write_bytes(b"%PDF")
    d = LO.decide(L.LinkTarget(kind=L.FILE, file="allegati/b.pdf", page=3), 3, str(doc), set())
    assert d.action == "open_pdf" and d.page == 3
    assert LO.decide(L.LinkTarget(kind=L.FILE, file="manca.pdf"), 3, str(doc), set()).action == "missing"


def test_allow_always_site(ui, tmp_path, monkeypatch):
    path = tmp_path / "w.pdf"
    doc = pymupdf.open()
    doc.new_page()
    L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.URI, uri="https://esempio.it"))
    L.create_link(doc, 0, pymupdf.Rect(100, 300, 200, 320), L.LinkTarget(kind=L.URI, uri="https://esempio.it/b"))
    doc.save(str(path))
    view = ui.open(str(path))
    opened, asked = [], []
    monkeypatch.setattr(LO, "open_url", lambda u: opened.append(u))
    monkeypatch.setattr(LO, "confirm_url", lambda *a: asked.append(a) or (False, False))
    LO.activate(ui.ctx, view, 0, pymupdf.Point(150, 210))
    assert asked and not opened
    monkeypatch.setattr(LO, "confirm_url", lambda *a: asked.append(a) or (True, True))
    LO.activate(ui.ctx, view, 0, pymupdf.Point(150, 210))
    assert opened == ["https://esempio.it"]
    n = len(asked)
    LO.activate(ui.ctx, view, 0, pymupdf.Point(150, 310))
    assert len(asked) == n and opened[-1] == "https://esempio.it/b"
    assert not view.document.is_modified


def test_launch_link_blocked(ui, tmp_path, monkeypatch):
    path = tmp_path / "x.pdf"
    doc = pymupdf.open()
    doc.new_page()
    L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.LAUNCH, file="esegui.sh"))
    doc.save(str(path))
    view = ui.open(str(path))
    opened = []
    monkeypatch.setattr(LO, "open_url", lambda u: opened.append(u))
    LO.activate(ui.ctx, view, 0, pymupdf.Point(150, 210))
    assert not opened
    assert any("bloccati" in m for _t, m in ui.errors)


def test_advanced_actions(ui, tmp_path, monkeypatch):
    from foliant.modules.objects import dialogs as D
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 120), (200, 70, 300, 120)]))
    ui.c.set_selection(view, 0, [0, 1])
    O._state["actions"]["group"].trigger()
    assert len(objs(view)) == 1 and objs(view)[0].kind == "composto"
    O._state["actions"]["decompose"].trigger()
    assert len(objs(view)) == 2
    ui.c.set_selection(view, 0, [0])
    monkeypatch.setattr(D, "ask_transform", lambda parent: (dict(rotate=30), False))
    O._state["actions"]["transform"].trigger()
    assert abs(E.object_angle(objs(view)[0]) - 30) < 0.01
    O._state["actions"]["reset_tf"].trigger()
    assert abs(E.object_angle(objs(view)[0])) < 0.01
    ui.c.set_selection(view, 0, [0])
    monkeypatch.setattr(D, "ask_replicate", lambda parent, n: ([0, 1], True))
    O._state["actions"]["replicate"].trigger()
    assert len(objs(view, 1)) == 1


def test_image_tools_actions(ui, tmp_path, monkeypatch):
    from foliant.modules.objects import dialogs as D
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 120)], paths=[(300, 300, 400, 380)]))
    ui.c.set_selection(view, 0, [0])
    monkeypatch.setattr(D, "ask_adjust", lambda parent, n: dict(grayscale=True))
    O._state["actions"]["adjust"].trigger()
    with O.ENGINE_LOCK:
        o = objs(view)[0]
        assert "Gray" in view.document.pdf.xref_get_key(o.xref, "ColorSpace")[1]
    ui.c.set_selection(view, 0, [0])
    monkeypatch.setattr(D, "ask_optimize", lambda parent, est: (est(max_ppi=72), dict(max_ppi=72))[1])
    O._state["actions"]["optimize"].trigger()
    assert objs(view)[0].width <= 101 or any("pesante" in m for _t, m in ui.errors)
    ui.c.set_selection(view, 0, [1])
    monkeypatch.setattr(D, "ask_path_style", lambda parent, obj: dict(stroke=(0, 0, 1), fill=None, width=4, dash=None))
    O._state["actions"]["path_style"].trigger()
    assert abs(objs(view)[1].state.line_width - 4) < 1e-6


def test_grid_snap(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(53, 77, 103, 127)]))
    ui.c.grid_on, ui.c.grid_snap, ui.c.grid_step_mm = True, True, 10.0
    ui.c.smart_guides = False
    try:
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         vpos(view, 0, 70, 100))
        drag(view, 0, (70, 100), (130, 160))
        r = objs(view)[0].rect
        step = 10 * 72 / 25.4
        assert abs(r.x0 / step - round(r.x0 / step)) < 1e-3 and abs(r.y0 / step - round(r.y0 / step)) < 1e-3
        view.flush_rendering()
        assert not ui.window.grab().isNull()
    finally:
        ui.c.grid_on = False
        ui.c.smart_guides = True


def test_external_edit_roundtrip(ui, tmp_path, monkeypatch):
    import os
    from foliant.modules.objects import external as XT
    launched = []
    monkeypatch.setattr(XT, "launch", lambda p: launched.append(p) or True)
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    ui.c.set_selection(view, 0, [0])
    with O.ENGINE_LOCK:
        before = view.document.pdf[0].get_pixmap(dpi=40).samples
    sess = ui.c.edit_external(view)
    assert sess is not None and launched and os.path.exists(launched[0])
    assert oct(os.stat(sess.dir).st_mode)[-3:] == "700"
    with open(launched[0], "wb") as f:
        f.write(png_bytes(200, 100, (0, 255, 0)))
    assert sess.reimport()
    with O.ENGINE_LOCK:
        assert view.document.pdf[0].get_pixmap(dpi=40).samples != before
    view.document.undo()
    with O.ENGINE_LOCK:
        assert view.document.pdf[0].get_pixmap(dpi=40).samples == before
    d = sess.dir
    ui.c.stop_external()
    assert not os.path.exists(d)


def test_link_on_image_moves_with_it(ui, tmp_path):
    path = tmp_path / "li.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10), keep_proportion=False)
    L.create_link(doc, 0, pymupdf.Rect(50, 70, 250, 170), L.LinkTarget(kind=L.URI, uri="https://a.it"))
    L.create_link(doc, 0, pymupdf.Rect(300, 300, 400, 320), L.LinkTarget(kind=L.URI, uri="https://b.it"))
    doc.save(str(path))
    view = ui.open(str(path))
    ui.c.set_selection(view, 0, [0])
    ui.c.translate(view, 30, 0)
    with O.ENGINE_LOCK:
        rects = sorted((round(i.rect.x0), i.target.uri) for i in L.page_links(view.document.pdf, 0))
    assert rects == [(80, "https://a.it"), (300, "https://b.it")]
    view.document.undo()
    with O.ENGINE_LOCK:
        assert sorted(round(i.rect.x0) for i in L.page_links(view.document.pdf, 0)) == [50, 300]


def test_parser_fuzz_never_crashes():
    import random
    from foliant.modules.objects.content import parse_page
    random.seed(7)
    toks = [b"q", b"Q", b"cm", b"1", b"0", b"-3.5", b"re", b"f", b"S", b"m", b"l", b"c", b"h", b"W", b"n",
            b"/Im0", b"Do", b"BT", b"ET", b"(abc", b")", b"<<", b">>", b"[", b"]", b"BI", b"ID", b"EI", b"gs",
            b"/G0", b"rg", b"\x00\xff", b"%x\n", b"sh", b"1 0 0 1 10 10", b"10 10 50 50"]
    for _k in range(120):
        data = b" ".join(random.choice(toks) for _ in range(random.randint(1, 50)))
        d = pymupdf.open()
        p = d.new_page()
        x = d.get_new_xref()
        d.update_object(x, "<<>>")
        d.update_stream(x, data, new=True)
        d.xref_set_key(p.xref, "Contents", f"{x} 0 R")
        pp = parse_page(d, d[0])
        if pp.objects:
            try:
                E.translate(d, 0, [0], 5, 5)
                E.delete_objects(d, 0, [0])
            except E.ObjectError:
                pass
