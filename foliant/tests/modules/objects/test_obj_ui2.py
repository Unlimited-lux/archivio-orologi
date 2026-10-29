"""Test UI: estrazione in blocco, cattura, permessi, firme, apertura sicura dei collegamenti, guide."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from tests.conftest import has_mode
from foliant.modules import objects as O
from foliant.modules.objects import engine as E
from foliant.modules.objects import extract as X
from foliant.modules.objects import extras
from foliant.modules.objects import links as L

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


from tests.test_document import _signed_pdf, _validate, test_signer  # noqa: E402,F401


def _sign_guard_asks(monkeypatch):
    """Registra le domande della guardia SIG-21 del modulo Firma (risposta: Annulla)."""
    from foliant.modules.sign import controller as SC
    asked = []
    monkeypatch.setattr(SC.SignController, "_ask_invalidation",
                        lambda self, view, what: asked.append(what) or "cancel")
    return asked


def test_link_on_signed_document_keeps_signature(ui, tmp_path, monkeypatch, test_signer):
    asked = _sign_guard_asks(monkeypatch)
    path = _signed_pdf(tmp_path, test_signer, objstm=False)
    view = ui.open(str(path))
    xref = ui.c.run_change(view, "Crea collegamento", L.create_link, 1, pymupdf.Rect(100, 500, 200, 520),
                           L.LinkTarget(kind=L.URI, uri="https://esempio.it"), L.LinkStyle(), "", None,
                           kind="annotation")
    assert xref and not asked
    view.document.save()
    st = _validate(path.read_bytes(), test_signer)
    assert st.intact and st.valid


def test_image_on_signed_document_asks(ui, tmp_path, monkeypatch, test_signer):
    asked = _sign_guard_asks(monkeypatch)
    path = _signed_pdf(tmp_path, test_signer, objstm=False)
    view = ui.open(str(path))
    res = ui.c.insert_image_bytes(view, [png_bytes(20, 20)], 0, pymupdf.Point(300, 600))
    assert asked and res is None
    assert not view.document.can_undo
    assert not any(t != "info" for t, _m in ui.errors)   # annullato: nessun messaggio d'errore


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
def test_ctrl_click_uri_goes_through_core_confirmation(ui, tmp_path, monkeypatch):
    """Ctrl+clic negli strumenti di modifica: stessa conferma unica della lettura (VIS-19/SEC-27)."""
    from foliant.modules.security import links as SL
    path = tmp_path / "w.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((100, 215), "www.banca.it", fontsize=12)
    L.create_link(doc, 0, pymupdf.Rect(95, 200, 200, 220), L.LinkTarget(kind=L.URI, uri="https://altro.example"))
    doc.save(str(path))
    view = ui.open(str(path))
    asked, opened = [], []
    monkeypatch.setattr(SL, "ask_open", lambda parent, uri, host, warnings: asked.append((uri, warnings)) or "cancel")
    monkeypatch.setattr(SL, "open_url", lambda u: opened.append(u))
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                     vpos(view, 0, 150, 210))
    assert asked and asked[0][0] == "https://altro.example" and not opened
    assert any("diverso" in w or "different" in w for w in asked[0][1])   # testo e destinazione diversi
    assert not view.document.is_modified


def test_hover_link_shows_destination_tooltip(ui, tmp_path):
    path = tmp_path / "t.pdf"
    doc = pymupdf.open()
    for _k in range(3):
        doc.new_page()
    L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.PAGE, page=2, fit="Fit"))
    doc.save(str(path))
    view = ui.open(str(path))
    QTest.mouseMove(view.viewport(), vpos(view, 0, 150, 210))
    assert "3" in view.toolTip()

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
    monkeypatch.setattr(D, "ask_adjust", lambda parent, n, *a: dict(grayscale=True))
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
    assert has_mode(sess.dir, 0o700)
    with open(launched[0], "wb") as f:
        f.write(png_bytes(200, 100, (0, 255, 0)))
    assert sess.reimport()
    with O.ENGINE_LOCK:
        assert view.document.pdf[0].get_pixmap(dpi=40).samples != before
    view.document.undo()
    with O.ENGINE_LOCK:
        assert view.document.pdf[0].get_pixmap(dpi=40).samples == before
    d = sess.dir
    assert os.path.basename(d).startswith(f"foliant-img-{os.getpid()}-") and d in XT._LIVE_DIRS
    ui.c.stop_external()
    assert not os.path.exists(d) and d not in XT._LIVE_DIRS


def test_external_edit_temp_removed_at_exit(ui, tmp_path, monkeypatch):
    """Riservatezza: se l'app esce con una modifica esterna aperta, l'immagine temporanea sparisce."""
    import os
    from foliant.modules.objects import external as XT
    monkeypatch.setattr(XT, "launch", lambda p: True)
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    ui.c.set_selection(view, 0, [0])
    sess = ui.c.edit_external(view)
    d = sess.dir
    XT.remove_live_dirs()           # quello che fa atexit
    assert not os.path.exists(d)
    ui.c.stop_external()


def test_external_edit_leftovers_cleaned(tmp_path):
    """Le cartelle temporanee lasciate da un arresto anomalo si cancellano all'avvio; non quelle di un
    processo vivo, quelle recenti senza PID delle versioni precedenti, né altri file."""
    import os
    import time
    from foliant.modules.objects import external as XT
    dead = tmp_path / "foliant-img-999999999-abc"
    alive = tmp_path / f"foliant-img-{os.getpid()}-abc"
    legacy_old = tmp_path / "foliant-img-vecchia"
    legacy_new = tmp_path / "foliant-img-nuova"
    for d in (dead, alive, legacy_old, legacy_new):
        d.mkdir()
        (d / "immagine_p1.png").write_bytes(b"x")
    past = time.time() - 3600
    os.utime(legacy_old, (past, past))
    (tmp_path / "foliant-img-file").write_bytes(b"x")
    assert XT.cleanup_leftovers(str(tmp_path)) == 2
    assert not dead.exists() and not legacy_old.exists()
    assert alive.exists() and legacy_new.exists() and (tmp_path / "foliant-img-file").exists()


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


def test_background_parse(ui, tmp_path, monkeypatch, qtbot):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    monkeypatch.delenv("FOLIANT_SYNC_TASKS", raising=False)
    ui.c._cache.clear()
    assert ui.c.parsed(view, 0, quiet=True, background=True) is None
    qtbot.waitUntil(lambda: ui.c.parsed(view, 0, quiet=True, background=True) is not None, timeout=5000)
    assert [o.kind for o in ui.c.parsed(view, 0).objects] == ["immagine", "testo"]


def test_context_menu_full_on_selected_object(ui, tmp_path):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QMenu
    from foliant.api import PageEvent
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    QGuiApplication.clipboard().setText("solo testo")
    statuses = []
    view.statusMessage.connect(statuses.append)
    menu = QMenu()
    pe = PageEvent(0, pymupdf.Point(100, 100), QPointF(0, 0), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    for provider in view.menu_providers:
        provider(view, pe, menu)
    texts = [a.text() for a in menu.actions()]
    assert not any("Errore" in s for s in statuses), statuses
    for t in ("Elimina", "Ruota a sinistra", "Capovolgi orizzontalmente", "Sostituisci immagine…",
              "Salva immagine come…"):
        assert t in texts, texts


def test_link_selection_after_save(ui, tmp_path):
    path = tmp_path / "l3.pdf"
    doc = pymupdf.open()
    doc.new_page()
    for k, name in enumerate(("uno", "due", "tre")):
        L.create_link(doc, 0, pymupdf.Rect(50, 50 + 40 * k, 150, 70 + 40 * k), L.LinkTarget(kind=L.URI, uri=f"https://{name}.it"))
    doc.save(str(path))
    view = ui.open(str(path))
    view.set_tool(ui.c.link_tool)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vpos(view, 0, 100, 60))
    QTest.keyClick(view, Qt.Key.Key_Delete)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, vpos(view, 0, 100, 100))
    view.document.save()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    with O.ENGINE_LOCK:
        left = [i.target.uri for i in L.page_links(view.document.pdf, 0)]
    assert left == ["https://tre.it"]


def test_reinsert_image_after_save_is_correct(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    red, blue = png_bytes(30, 30, (255, 0, 0)), png_bytes(30, 30, (0, 0, 255))
    ui.c.insert_image_bytes(view, [red], 0, pymupdf.Point(200, 200))
    ui.c.delete(view)
    ui.c.insert_image_bytes(view, [blue], 0, pymupdf.Point(200, 200))
    view.document.save()
    ui.c.insert_image_bytes(view, [red], 0, pymupdf.Point(400, 400))
    with O.ENGINE_LOCK:
        o = objs(view)[-1]
        pix = view.document.pdf[0].get_pixmap(dpi=72, clip=o.rect)
    r, g, b = pix.pixel(pix.width // 2, pix.height // 2)[:3]
    assert r > 200 and b < 60


def test_crop_with_square_ratio(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 300)]))
    ui.c.crop_ratio = "1:1"
    try:
        ui.c.set_selection(view, 0, [0])
        ui.c.start_crop(view)
        r = ui.c.object_tool.crop["rect"]
        assert abs(r.width - 100) < 0.01 and abs(r.height - 100) < 0.01 and abs(r.x0 - 150) < 0.01
        drag(view, 0, (250, 300), (230, 300))   # maniglia in basso a destra verso sinistra
        r = ui.c.object_tool.crop["rect"]
        assert abs(r.width - r.height) < 0.01 and abs(r.x0 - 150) < 0.5 and abs(r.y0 - 200) < 0.5
        QTest.keyClick(view, Qt.Key.Key_Return)
        o = objs(view)[0]
        assert abs(o.rect.width - o.rect.height) < 0.6
    finally:
        ui.c.crop_ratio = "free"


def test_ratio_helpers():
    from foliant.modules.objects.tool import fit_ratio, ratio_from_handle
    r = fit_ratio(pymupdf.Rect(0, 0, 160, 90), 1.0)
    assert (r.x0, r.width, r.height) == (35, 90, 90)
    r = ratio_from_handle(pymupdf.Rect(0, 0, 160, 50), 4, 16 / 9)
    assert abs(r.width / r.height - 16 / 9) < 1e-6 and r.x0 == 0 and r.y0 == 0


def test_save_image_as_shown_cropped_rotated(ui, tmp_path, monkeypatch):
    from PIL import Image as PImage
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 300)]))
    ui.c.set_selection(view, 0, [0])
    with view.document.change("prep") as pdf:
        E.set_crop(pdf, 0, 0, (0, 0, 0.25, 1))
        E.rotate(pdf, 0, [0], 90)
    ui.c.set_selection(view, 0, [0])
    out = tmp_path / "vista.png"
    monkeypatch.setattr(ui.ctx, "ask_save_path", lambda *a, **k: str(out))
    O._state["actions"]["save_shown"].trigger()
    im = PImage.open(out)
    o = objs(view)[0]
    assert abs(im.width / im.height - o.rect.width / o.rect.height) < 0.05
    assert im.width > im.height   # quarto sinistro (50×100 pt) ruotato di 90°: orizzontale


def test_optimize_document_images(ui, tmp_path, monkeypatch):
    import numpy as np
    from PIL import Image as PImage
    from foliant.modules.objects import dialogs as D
    path = tmp_path / "foto.pdf"
    doc = pymupdf.open()
    rng = np.random.default_rng(0)
    for k in range(2):
        page = doc.new_page()
        arr = (rng.random((600, 600, 3)) * 255).astype("uint8")
        b = io.BytesIO()
        PImage.fromarray(arr).save(b, "PNG")
        page.insert_image(pymupdf.Rect(50, 50, 122, 122), stream=b.getvalue())   # 600 px su 1 in
    doc.save(str(path))
    view = ui.open(str(path))
    monkeypatch.setattr(D, "ask_optimize", lambda parent, est: dict(max_ppi=150, mode="jpeg", quality=70))
    O._state["actions"]["optimize_doc"].trigger()
    for i in range(2):
        o = objs(view, i)[0]
        assert o.width <= 151 and abs(o.rect.width - 72) < 0.5
    assert view.document.undo_label == "Ottimizza le immagini del documento"
    view.document.undo()
    assert objs(view, 0)[0].width == 600


def test_panel_reference_point_and_skew(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 120)]))
    ui.c.set_selection(view, 0, [0])
    panel = O._state["panel"]
    panel.set_reference((1.0, 1.0))      # angolo in basso a destra
    assert abs(panel.x.value() - 150) < 0.01 and abs(panel.y.value() - 120) < 0.01
    panel.lock.setChecked(True)
    panel.w.setValue(200)
    panel.w.editingFinished.emit()
    r = objs(view)[0].rect
    assert abs(r.x1 - 150) < 0.01 and abs(r.y1 - 120) < 0.01 and abs(r.width - 200) < 0.01
    panel.set_reference((0.5, 0.5))
    panel.x.setValue(300)
    panel.x.editingFinished.emit()
    r = objs(view)[0].rect
    assert abs((r.x0 + r.x1) / 2 - 300) < 0.01
    assert panel.skew.text().startswith("0.0")
    from foliant.modules.objects import advanced as A
    with view.document.change("incl") as pdf:
        A.apply_transform(pdf, 0, [0], skew_x=20)
    ui.c.set_selection(view, 0, [0])
    assert abs(abs(float(panel.skew.text().split()[0])) - 20) < 0.5
    panel.set_reference((0.0, 0.0))


def test_user_guides_snap_and_persist(ui, tmp_path):
    path = make_doc(tmp_path / "g.pdf", images=[(300, 400, 350, 450)])
    view = ui.open(path)
    ui.c.smart_guides = True
    ui.c.add_guide(view, "x", 100.0)
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     vpos(view, 0, 320, 420))
    drag(view, 0, (320, 420), (121.5, 520))
    assert abs(objs(view)[0].rect.x0 - 100) < 0.01
    view.flush_rendering()
    assert not ui.window.grab().isNull()
    assert ui.c.guides(view, 0)["x"] == [100.0]
    assert view.document.undo_label == "Sposta oggetti"   # aggiungere guide non crea passi nel PDF
    ui.c.clear_guides(view)
    assert ui.c.guides(view, 0)["x"] == []


def test_old_per_document_guides_purged(ui, tmp_path):
    """Riservatezza: nessuna chiave derivata dal percorso dei documenti resta nelle impostazioni."""
    import hashlib
    import json
    import os
    path = make_doc(tmp_path / "old.pdf", images=[])
    key = "objects/guides/" + hashlib.sha1(os.path.abspath(path).encode()).hexdigest()
    st = ui.ctx.settings()
    st.setValue(key, json.dumps({"x": [72.0], "y": [144.0]}))
    ui.c.purge_old_guides()
    view = ui.open(path)
    assert not st.value(key, "")
    assert not [k for k in st.allKeys() if k.startswith("objects/guides")]
    assert all("/" not in k.split("objects/", 1)[-1] for k in st.allKeys() if k.startswith("objects/"))
    del view


@pytest.fixture
def old_guides_settings(settings):
    import hashlib
    import json
    for name in ("a.pdf", "b.pdf"):
        key = "objects/guides/" + hashlib.sha1(f"/home/rossi/{name}".encode()).hexdigest()
        settings.setValue(key, json.dumps({"x": [72.0], "y": []}))
    settings.setValue("objects/smart_guides", "false")
    settings.sync()
    return settings


def test_old_per_document_guides_purged_at_startup(old_guides_settings, window):
    """Riservatezza: il gruppo objects/guides/<sha1 del percorso> delle versioni precedenti si
    cancella all'avvio del modulo, senza aprire documenti; le preferenze generali restano."""
    s = old_guides_settings
    s.sync()
    assert not [k for k in s.allKeys() if k.startswith("objects/guides")]
    assert s.value("objects/smart_guides") == "false"


def test_link_from_multiline_multipage_selection(ui, tmp_path, monkeypatch):
    from foliant.api import selection_from_words
    from foliant.modules.objects import dialogs as D
    path = tmp_path / "mp.pdf"
    doc = pymupdf.open()
    for k in range(2):
        p = doc.new_page()
        p.insert_text((72, 100), f"prima riga {k}", fontsize=12)
        p.insert_text((72, 120), f"seconda riga {k}", fontsize=12)
    doc.save(str(path))
    view = ui.open(path)
    view.set_tool(None)
    sels = [selection_from_words(i, view.words(i)) for i in range(2)]
    view.set_text_selections(sels)
    monkeypatch.setattr(D, "ask_link", lambda *a, **k: (L.LinkTarget(kind=L.URI, uri="https://esempio.it"),
                                                        L.LinkStyle(), "x"))
    ui.c.create_link_from_selection(view)
    with O.ENGINE_LOCK:
        infos = [L.page_links(view.document.pdf, i) for i in range(2)]
    assert [len(x) for x in infos] == [1, 1]
    assert all(x[0].target.uri == "https://esempio.it" and len(x[0].quads) == 2 for x in infos)
    assert view.document.undo_label == "Crea collegamento"
    view.document.undo()
    with O.ENGINE_LOCK:
        assert sum(len(L.page_links(view.document.pdf, i)) for i in range(2)) == 0


def test_actions_follow_document_access_changes(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    ui.c.set_selection(view, 0, [0])
    monkeypatch.setattr(type(ui.c), "extract_allowed", lambda self, v: False)
    ui.ctx.documentAccessChanged.emit(view)
    assert not O._state["actions"]["save_image"].isEnabled()
    monkeypatch.setattr(type(ui.c), "extract_allowed", lambda self, v: True)
    ui.ctx.documentAccessChanged.emit(view)
    assert O._state["actions"]["save_image"].isEnabled()


def test_pdfa_warning_once_and_xfa_blocked(ui, tmp_path):
    path = tmp_path / "pdfa.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                         '<rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="1" '
                         'pdfaid:conformance="B"/></rdf:RDF></x:xmpmeta>')
    doc.save(str(path))
    view = ui.open(str(path))
    ui.c.set_selection(view, 0, [0])
    ui.c.translate(view, 5, 0)
    ui.c.translate(view, 5, 0)
    warns = [m for t, m in ui.errors if "PDF/A-1" in m]
    assert len(warns) == 1 and abs(objs(view)[0].rect.x0 - 60) < 0.01
    path2 = tmp_path / "xfa.pdf"
    d2 = pymupdf.open()
    d2.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    d2.xref_set_key(d2.pdf_catalog(), "NeedsRendering", "true")
    d2.save(str(path2))
    v2 = ui.open(str(path2))
    ui.c.set_selection(v2, 0, [0])
    ui.c.translate(v2, 5, 0)
    assert not v2.document.can_undo
    assert any("XFA" in m for _t, m in ui.errors)


def test_opacity_on_pdfa1_asks_before_applying(ui, tmp_path, monkeypatch):
    path = tmp_path / "pdfa1.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                         '<rdf:Description xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="1"/></rdf:RDF></x:xmpmeta>')
    doc.save(str(path))
    view = ui.open(str(path))
    ui.c.set_selection(view, 0, [0])
    asked = []
    monkeypatch.setattr(ui.ctx, "confirm", lambda title, msg: asked.append(msg) or False)
    ui.c.set_opacity(view, 0.8)
    assert asked and not view.document.can_undo
    monkeypatch.setattr(ui.ctx, "confirm", lambda title, msg: True)
    ui.c.set_opacity(view, 0.8)
    assert abs(objs(view)[0].opacity - 0.8) < 1e-6


def test_huge_image_proposes_resampling(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    monkeypatch.setattr(E, "MAX_PIXELS", 10_000)          # soglia ridotta per il test
    asked = []
    monkeypatch.setattr(ui.ctx, "confirm", lambda title, msg: asked.append(msg) or True)
    ui.c.insert_image_bytes(view, [png_bytes(200, 100)], 0, pymupdf.Point(300, 400))
    o = objs(view)[-1]
    assert asked and o.width * o.height <= 10_000 and abs(o.width / o.height - 2) < 0.05


def test_align_key_object_and_page_reference(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 300, 70, 320), (80, 400, 100, 420),
                                                         (120, 500, 140, 520)]))
    ui.c.set_selection(view, 0, [2, 0, 1])          # oggetto chiave: il primo selezionato (indice 2)
    O._state["actions"]["ref_key"].trigger()
    try:
        O._state["actions"]["al_bottom"].trigger()
        ys = [round(o.rect.y1) for o in objs(view)]
        assert ys == [520, 520, 520]
        assert round(objs(view)[2].rect.y0) == 500   # l'oggetto chiave non si muove
        ui.c.set_selection(view, 0, [0])
        O._state["actions"]["ref_page"].trigger()
        O._state["actions"]["al_hc"].trigger()
        r = objs(view)[0].rect
        with O.ENGINE_LOCK:
            pw = view.document.pdf[0].rect.width
        assert abs((r.x0 + r.x1) / 2 - pw / 2) < 0.1
    finally:
        O._state["actions"]["ref_auto"].trigger()


def test_distribute_centers_method(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(0, 300, 20, 320), (30, 300, 70, 320),
                                                         (100, 300, 110, 320), (200, 300, 260, 320)]))
    ui.c.set_selection(view, 0, [0, 1, 2, 3])
    O._state["actions"]["dist_centers"].trigger()
    try:
        O._state["actions"]["dist_h"].trigger()
        cs = sorted((o.rect.x0 + o.rect.x1) / 2 for o in objs(view))
        gaps = [b - a for a, b in zip(cs, cs[1:])]
        assert max(gaps) - min(gaps) < 0.1
    finally:
        O._state["actions"]["dist_spacing"].trigger()


def test_link_dialog_new_named_destination_from_view(ui, tmp_path, monkeypatch):
    from foliant.modules.objects import dialogs as D
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[], pages=3))
    view.go_to_page(2)
    monkeypatch.setattr(D, "ask_text", lambda *a: "listino")
    dlg = D.LinkDialog(None, view, 0, None)
    dlg.kind.setCurrentIndex(dlg.kind.findData(L.NAMED))
    dlg._new_named()
    target, style, desc = dlg.values()
    assert target.kind == L.NAMED and target.name == "listino" and target.new_dest[0] == 2
    xref = ui.c.run_change(view, "Crea collegamento", L.create_link, 0, pymupdf.Rect(50, 300, 150, 320),
                           target, style, desc, None, kind="annotation")
    assert xref
    with O.ENGINE_LOCK:
        assert L.named_destinations(view.document.pdf)["listino"]["page"] == 2


def test_adjust_dialog_live_preview(ui, tmp_path):
    from foliant.modules.objects import dialogs as D
    dlg = D.AdjustDialog(None, 1, png_bytes(40, 20, (200, 30, 30)))
    before = dlg._preview_arr.copy()
    assert not dlg.preview.pixmap().isNull()
    dlg.gray.setChecked(True)
    after = dlg._preview_arr
    assert (after[..., 0] == after[..., 1]).all() and (after != before).any()
    dlg.gray.setChecked(False)
    dlg.sliders["brightness"].setValue(50)
    assert dlg._preview_arr.mean() > before.mean()


def test_percent_without_space(ui):
    from PySide6.QtWidgets import QAbstractSpinBox, QLabel
    from foliant.modules.objects import dialogs as D
    assert O._state["panel"].opacity.suffix() == "%"
    labels = [w.text() for w in ui.window.findChildren(QLabel) if w.text().endswith("%")]
    assert "100%" in labels and not any(t.endswith(" %") for t in labels)
    dlg = D.TransformDialog(None) if hasattr(D, "TransformDialog") else None
    if dlg is not None:
        sfx = [s.suffix() for s in dlg.findChildren(QAbstractSpinBox) if hasattr(s, "suffix") and "%" in s.suffix()]
        assert sfx and all(s == "%" for s in sfx)
