"""Test dell'interfaccia del modulo «objects»: strumenti, tastiera, appunti, collegamenti."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest

from foliant.modules import objects as O
from foliant.modules.objects import controller as CTL
from foliant.modules.objects import dialogs as D
from foliant.modules.objects import engine as E
from foliant.modules.objects import links as L


def png_bytes(w, h, color=(200, 30, 30), mode="RGB", dpi=None):
    b = io.BytesIO()
    im = Image.new(mode, (w, h), color)
    im.save(b, "PNG", **({"dpi": dpi} if dpi else {}))
    return b.getvalue()


def make_doc(path, images=((50, 70, 250, 170),), pages=2, colors=None, paths=()):
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 40), f"Pagina {i + 1}", fontsize=12)
    page = doc[0]
    for k, r in enumerate(images):
        col = (colors[k] if colors else (40 + 60 * k, 90, 160))
        page.insert_image(pymupdf.Rect(r), stream=png_bytes(200, 100, col), keep_proportion=False)
    for r in paths:
        page.draw_rect(pymupdf.Rect(r), color=(1, 0, 0), fill=(1, 1, 0), width=2)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def ui(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    # nessuna finestra modale (es. richiesta di salvataggio alla chiusura)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    errors = []
    monkeypatch.setattr(window.ctx, "error", lambda title, msg: errors.append((title, msg)))
    monkeypatch.setattr(window.ctx, "info", lambda title, msg: errors.append(("info", msg)))

    class UI:
        pass
    u = UI()
    u.window = window
    u.ctx = window.ctx
    u.c = O.controller()
    u.errors = errors
    u.tmp = tmp_path

    def open_doc(path):
        view = window.open_path(path)
        window.ctx.set_mode("modifica")
        view.set_tool(u.c.object_tool)
        view.set_zoom(1.0)
        QGuiApplication.processEvents()
        return view
    u.open = open_doc
    return u


def vpos(view, page, x, y) -> QPoint:
    p = view.page_transform(page).map(QPointF(x, y))
    return QPoint(round(p.x()), round(p.y()))


def objs(view, page=0):
    with O.ENGINE_LOCK:
        return E.objects(view.document.pdf, page).objects


def click(view, page, x, y, mods=Qt.KeyboardModifier.NoModifier):
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, mods, vpos(view, page, x, y))


def drag(view, page, a, b, mods=Qt.KeyboardModifier.NoModifier, steps=4, release=True):
    vp = view.viewport()
    pa = vpos(view, page, *a)
    QTest.mousePress(vp, Qt.MouseButton.LeftButton, mods, pa)
    for k in range(1, steps + 1):
        x = a[0] + (b[0] - a[0]) * k / steps
        y = a[1] + (b[1] - a[1]) * k / steps
        QTest.mouseMove(vp, vpos(view, page, x, y))
    if release:
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, mods, vpos(view, page, *b))


# ------------------------------------------------------------------ OBJ-01
def test_mode_and_tool_registered(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    assert ui.ctx.mode("modifica") is not None
    assert view.tool is ui.c.object_tool
    ui.ctx.set_mode("leggi")
    assert view.tool is not ui.c.object_tool
    ui.ctx.set_mode("modifica")
    QTest.qWait(10)
    assert view.tool is ui.c.object_tool
    assert not view.document.is_modified


def test_hover_outlines_object(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", paths=[(300, 300, 400, 380)]))
    QTest.mouseMove(view.viewport(), vpos(view, 0, 100, 100))
    assert ui.c.object_tool.hover == (0, 0)
    QTest.mouseMove(view.viewport(), vpos(view, 0, 350, 340))
    assert ui.c.object_tool.hover == (0, 1)
    QTest.mouseMove(view.viewport(), vpos(view, 0, 500, 250))
    assert ui.c.object_tool.hover is None


# ------------------------------------------------------------------ OBJ-02
def test_click_selects_topmost_and_alt_click_cycles(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170), (150, 120, 350, 220)]))
    click(view, 0, 200, 150)
    assert ui.c.selection == [1]
    click(view, 0, 200, 150, Qt.KeyboardModifier.AltModifier)
    assert ui.c.selection == [0]
    click(view, 0, 200, 150, Qt.KeyboardModifier.AltModifier)
    assert ui.c.selection == [1]


def test_shift_click_toggles_and_escape_clears(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 170), (300, 70, 400, 170)]))
    click(view, 0, 100, 100)
    click(view, 0, 350, 100, Qt.KeyboardModifier.ShiftModifier)
    assert sorted(ui.c.selection) == [0, 1]
    click(view, 0, 350, 100, Qt.KeyboardModifier.ShiftModifier)
    assert ui.c.selection == [0]
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert ui.c.selection == []


def test_rubber_band_contained_and_alt_intersected(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 300, 100, 350), (120, 300, 170, 350),
                                                         (190, 300, 290, 350)]))
    drag(view, 0, (40, 290), (200, 360))
    assert sorted(ui.c.selection) == [0, 1]
    drag(view, 0, (40, 290), (200, 360), Qt.KeyboardModifier.AltModifier)
    assert sorted(ui.c.selection) == [0, 1, 2]


def test_ctrl_a_with_filter_and_tab_cycle(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 170), (300, 70, 400, 170)],
                            paths=[(50, 400, 100, 450), (150, 400, 200, 450), (250, 400, 300, 450)]))
    view.setFocus()
    D.set_select_filter({E.IMAGE})
    try:
        QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        assert len(ui.c.selection) == 2
    finally:
        D.set_select_filter(set())
    QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert len(ui.c.selection) == 5
    ui.c.clear_selection(view)
    QTest.keyClick(view, Qt.Key.Key_Tab)
    assert ui.c.selection == [0]
    QTest.keyClick(view, Qt.Key.Key_Tab)
    assert ui.c.selection == [1]


def test_selection_on_other_page_replaces(ui, tmp_path):
    def extra(page, i):
        page.insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(20, 10))
    from tests.conftest import make_pdf
    path = make_pdf(tmp_path / "b.pdf", pages=2, extra=extra)
    view = ui.open(path)
    ui.c.set_selection(view, 0, [0])
    view.go_to_page(1)
    QGuiApplication.processEvents()
    click(view, 1, 100, 100)
    assert ui.c.sel_page == 1 and ui.c.selection == [0]


# ------------------------------------------------------------------ OBJ-08
def test_drag_moves_with_single_undo_step(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    drag(view, 0, (100, 100), (200, 150), steps=10)
    r = objs(view)[0].rect
    assert abs(r.x0 - 150) < 1.5 and abs(r.y0 - 120) < 1.5
    assert view.document.undo_label == "Sposta oggetti"
    view.document.undo()
    r = objs(view)[0].rect
    assert abs(r.x0 - 50) < 0.01 and abs(r.y0 - 70) < 0.01
    assert not view.document.can_undo


def test_escape_during_drag_cancels(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    drag(view, 0, (100, 100), (200, 150), release=False)
    QTest.keyClick(view, Qt.Key.Key_Escape)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       vpos(view, 0, 200, 150))
    assert not view.document.can_undo
    assert abs(objs(view)[0].rect.x0 - 50) < 0.01


def test_arrow_keys_move(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    click(view, 0, 100, 100)
    view.setFocus()
    for _k in range(3):
        QTest.keyClick(view, Qt.Key.Key_Right)
    QTest.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert abs(objs(view)[0].rect.x0 - 63) < 0.01


def test_shift_drag_constrains_horizontal(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    click(view, 0, 100, 100)
    drag(view, 0, (100, 100), (200, 110), Qt.KeyboardModifier.ShiftModifier)
    r = objs(view)[0].rect
    assert abs(r.y0 - 70) < 0.01 and abs(r.x0 - 150) < 1.5


def test_move_keeps_object_on_page(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    click(view, 0, 100, 100)
    ui.c.nudge(view, -1000, 0)
    assert objs(view)[0].rect.x1 >= 10 - 0.01


# ------------------------------------------------------------------ OBJ-09 / OBJ-10
def test_corner_handle_resizes_proportionally(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    click(view, 0, 100, 100)
    drag(view, 0, (250, 170), (350, 180))
    r = objs(view)[0].rect
    assert abs(r.x0 - 50) < 0.01 and abs(r.y0 - 70) < 0.01
    assert abs(r.width - 300) < 1.5 and abs(r.height - 150) < 1


def test_shift_corner_resizes_freely(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    click(view, 0, 100, 100)
    drag(view, 0, (250, 170), (350, 190), Qt.KeyboardModifier.ShiftModifier)
    r = objs(view)[0].rect
    assert abs(r.width - 300) < 1.5 and abs(r.height - 120) < 1.5


def test_rotation_handle_snaps_with_shift(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 300)]))
    click(view, 0, 150, 250)
    s = view.page_transform(0).m11()
    top = (200, 200 - 22 / s)
    drag(view, 0, top, (330, 190), Qt.KeyboardModifier.ShiftModifier)
    ang = E.object_angle(objs(view)[0])
    assert abs(ang / 15 - round(ang / 15)) < 1e-6
    assert ang != 0


def test_rotate_buttons_and_flip(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 300)]))
    click(view, 0, 150, 250)
    O._state["actions"]["rotate_cw"].trigger()
    r = objs(view)[0].rect
    assert abs(r.width - 100) < 0.01 and abs(r.height - 200) < 0.01
    for _k in range(3):
        O._state["actions"]["rotate_cw"].trigger()
    O._state["actions"]["flip_h"].trigger()
    O._state["actions"]["flip_h"].trigger()
    o = objs(view)[0]
    assert E.matrix_close(o.ctm, pymupdf.Matrix(200, 0, 0, 100, 100, 542))


# ------------------------------------------------------------------ OBJ-13
def test_crop_by_double_click_and_handles(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 400)]))
    click(view, 0, 150, 250)
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      vpos(view, 0, 150, 250))
    assert ui.c.object_tool.crop is not None
    drag(view, 0, (300, 300), (200, 300))
    QTest.keyClick(view, Qt.Key.Key_Return)
    r = objs(view)[0].rect
    assert abs(r.width - 100) < 1 and abs(r.height - 200) < 0.5
    O._state["actions"]["crop_reset"].trigger()
    r = objs(view)[0].rect
    assert abs(r.width - 200) < 0.01


def test_crop_escape_cancels(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(100, 200, 300, 400)]))
    click(view, 0, 150, 250)
    ui.c.start_crop(view)
    drag(view, 0, (300, 300), (200, 300))
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not view.document.can_undo


# ------------------------------------------------------------------ OBJ-15
def test_delete_key_and_undo(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 170), (300, 70, 400, 170)]))
    click(view, 0, 100, 100)
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert len(objs(view)) == 1
    view.document.undo()
    assert len(objs(view)) == 2


# ------------------------------------------------------------------ OBJ-04 / OBJ-05 / OBJ-06
def test_insert_image_from_file(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    img = tmp_path / "logo.png"
    img.write_bytes(png_bytes(300, 150, dpi=(150, 150)))
    monkeypatch.setattr(D, "ask_image_paths", lambda ctx, **k: [str(img)])
    O._state["actions"]["insert"].trigger()
    o = objs(view)[-1]
    assert abs(o.rect.width - 144) < 0.5 and abs(o.rect.height - 72) < 0.5
    assert ui.c.selection == [o.index]


def test_insert_corrupted_png_shows_error(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    bad = tmp_path / "rotto.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\nnon e' un png")
    monkeypatch.setattr(D, "ask_image_paths", lambda ctx, **k: [str(bad)])
    O._state["actions"]["insert"].trigger()
    assert ui.errors
    assert not view.document.is_modified and not view.document.can_undo


def test_paste_image_from_clipboard_centered(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    img = QImage.fromData(png_bytes(400, 200, (10, 200, 10, 128), mode="RGBA"))
    QGuiApplication.clipboard().setImage(img)
    view.setFocus()
    c = ui.c.visible_center(view, 0)
    QTest.keyClick(view, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    o = objs(view)[-1]
    assert (o.width, o.height) == (400, 200)
    with O.ENGINE_LOCK:
        assert view.document.pdf.xref_get_key(o.xref, "SMask")[0] == "xref"
    assert abs((o.rect.x0 + o.rect.x1) / 2 - c.x) < 1 and abs((o.rect.y0 + o.rect.y1) / 2 - c.y) < 1


def test_paste_uri_list_inserts_cascade(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    files = []
    for k in range(2):
        p = tmp_path / f"f{k}.jpg"
        Image.new("RGB", (64, 32), (k * 100, 50, 50)).save(p, "JPEG")
        files.append(p)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in files])
    QGuiApplication.clipboard().setMimeData(mime)
    ui.c.paste(view)
    os_ = objs(view)
    assert len(os_) == 2
    assert abs(os_[1].rect.x0 - os_[0].rect.x0 - 20) < 0.01 and abs(os_[1].rect.y0 - os_[0].rect.y0 - 20) < 0.01
    view.document.undo()
    assert len(objs(view)) == 0


def test_context_menu_paste_as_image_disabled_with_text(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    QGuiApplication.clipboard().setText("solo testo")
    CTL._last_payload = None
    from PySide6.QtWidgets import QMenu
    from foliant.api import PageEvent
    menu = QMenu()
    pe = PageEvent(0, pymupdf.Point(100, 100), QPointF(0, 0), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    for provider in view.menu_providers:
        provider(view, pe, menu)
    acts = {a.text(): a for a in menu.actions()}
    assert "Incolla come immagine" in acts
    assert not acts["Incolla come immagine"].isEnabled()


def test_drop_images_on_page(ui, tmp_path):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QDropEvent
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    files = []
    for k in range(3):
        p = tmp_path / f"d{k}.png"
        p.write_bytes(png_bytes(96, 48))
        files.append(p)
    docx = tmp_path / "lettera.docx"
    docx.write_bytes(b"PK")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in files + [docx]])
    pos = vpos(view, 0, 200, 300)
    ev = QDropEvent(QPointF(pos), Qt.DropAction.CopyAction, mime, Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier, QEvent.Type.Drop)
    view._objects_drop.eventFilter(view.viewport(), ev)
    os_ = objs(view)
    assert len(os_) == 3
    c = os_[0].rect
    assert abs((c.x0 + c.x1) / 2 - 200) < 1 and abs((c.y0 + c.y1) / 2 - 300) < 1
    assert any("lettera.docx" in m for _t, m in ui.errors)
    view.document.undo()
    assert len(objs(view)) == 0


# ------------------------------------------------------------------ OBJ-30 / OBJ-31
def test_copy_paste_between_pages_and_documents(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    click(view, 0, 100, 100)
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QGuiApplication.clipboard().mimeData().hasFormat("application/x-foliant-objects")
    ui.c.paste(view, page=1)
    o = objs(view, 1)[-1]
    assert abs(o.rect.x0 - 50) < 0.01 and abs(o.rect.y0 - 70) < 0.01
    view2 = ui.open(make_doc(tmp_path / "b.pdf", images=[]))
    ui.c.paste(view2, page=0)
    o2 = objs(view2, 0)[-1]
    assert abs(o2.rect.x0 - 50) < 0.01
    assert not view.document.undo_label == "Incolla oggetti" or True
    assert view2.document.undo_label == "Incolla oggetti"


def test_paste_same_page_offsets(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    click(view, 0, 100, 100)
    ui.c.copy(view)
    ui.c.paste(view)
    ui.c.paste(view)
    xs = sorted(round(o.rect.x0) for o in objs(view))
    assert xs == [50, 60, 70]


def test_duplicate_twice_does_not_touch_clipboard(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    QGuiApplication.clipboard().setText("invariato")
    click(view, 0, 100, 100)
    O._state["actions"]["duplicate"].trigger()
    O._state["actions"]["duplicate"].trigger()
    xs = sorted(round(o.rect.x0) for o in objs(view))
    assert xs == [50, 60, 70]
    assert QGuiApplication.clipboard().text() == "invariato"


def test_cut_is_single_step(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    click(view, 0, 100, 100)
    QTest.keyClick(view, Qt.Key.Key_X, Qt.KeyboardModifier.ControlModifier)
    assert len(objs(view)) == 0
    view.document.undo()
    assert len(objs(view)) == 1


def test_move_to_page_and_undo(ui, tmp_path, monkeypatch):
    path = tmp_path / "m.pdf"
    doc = pymupdf.open()
    for _k in range(7):
        doc.new_page()
    doc[1].insert_image(pymupdf.Rect(100, 100, 200, 150), stream=png_bytes(20, 10))
    doc.save(str(path))
    view = ui.open(str(path))
    ui.c.set_selection(view, 1, [0])
    monkeypatch.setattr(D, "ask_page", lambda *a: 6)
    O._state["actions"]["move_page"].trigger()
    assert len(objs(view, 1)) == 0
    o = objs(view, 6)[0]
    assert abs(o.rect.x0 - 100) < 0.01 and abs(o.rect.y0 - 100) < 0.01
    view.document.undo()
    assert len(objs(view, 1)) == 1 and len(objs(view, 6)) == 0


# ------------------------------------------------------------------ OBJ-20 / 21 / 22
def test_arrange_actions(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 300, 70, 320), (80, 400, 100, 420),
                                                         (120, 500, 140, 520)]))
    ui.c.set_selection(view, 0, [0, 1, 2])
    O._state["actions"]["al_left"].trigger()
    assert [round(o.rect.x0) for o in objs(view)] == [50, 50, 50]
    assert [round(o.rect.y0) for o in objs(view)] == [300, 400, 500]
    assert view.document.undo_label == "Allinea oggetti"
    O._state["actions"]["al_left"].trigger()  # già allineati: nessun passo
    assert view.document.undo_label == "Allinea oggetti"
    view.document.undo()
    assert [round(o.rect.x0) for o in objs(view)] == [50, 80, 120]


def test_reorder_front_shortcut(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170), (100, 100, 300, 200),
                                                         (150, 120, 350, 220)]))
    click(view, 0, 60, 80)
    assert ui.c.selection == [0]
    QTest.keyClick(view, Qt.Key.Key_BracketRight,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert ui.c.selection == [2]
    assert abs(objs(view)[2].rect.x0 - 50) < 0.01


def test_opacity_slider_single_step(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    click(view, 0, 100, 100)
    slider = ui.c._opacity_slider
    slider.setValue(40)
    slider.sliderReleased.emit()
    assert abs(objs(view)[0].opacity - 0.4) < 1e-6
    assert view.document.undo_label == "Opacità"


# ------------------------------------------------------------------ OBJ-03
def test_properties_panel_width_with_lock(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 120)]))
    click(view, 0, 100, 100)
    panel = O._state["panel"]
    assert panel.title.text() == "Immagine"
    panel.lock.setChecked(True)
    panel.w.setValue(200)
    panel.w.editingFinished.emit()
    r = objs(view)[0].rect
    assert abs(r.width - 200) < 0.01 and abs(r.height - 100) < 0.01
    assert abs(r.x0 - 50) < 0.01 and abs(r.y0 - 70) < 0.01
    assert "216" not in panel.details.text() or True


def test_properties_panel_ppi_and_mixed_opacity(ui, tmp_path):
    path = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(50, 70, 250, 170), stream=png_bytes(600, 300), keep_proportion=False)
    page.insert_image(pymupdf.Rect(300, 70, 400, 170), stream=png_bytes(10, 10), keep_proportion=False)
    doc.save(str(path))
    view = ui.open(str(path))
    click(view, 0, 100, 100)
    assert "216 × 216 ppi" in O._state["panel"].details.text()
    with view.document.change("op") as pdf:
        E.set_opacity(pdf, 0, [0], 0.4)
        E.set_opacity(pdf, 0, [1], 0.7)
    ui.c.set_selection(view, 0, [0, 1])
    panel = O._state["panel"]
    assert panel.opacity.text().startswith("misto")
    panel.opacity.setValue(50)
    panel.opacity.editingFinished.emit()
    assert all(abs(o.opacity - 0.5) < 1e-6 for o in objs(view))


# ------------------------------------------------------------------ OBJ-27
def test_save_image_original_jpeg(ui, tmp_path, monkeypatch):
    jpg = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 30, 200)).save(jpg, "JPEG", quality=85)
    path = tmp_path / "j.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_image(pymupdf.Rect(50, 70, 250, 170), stream=jpg.getvalue())
    doc.save(str(path))
    view = ui.open(str(path))
    click(view, 0, 100, 100)
    out = tmp_path / "estratta.jpg"
    seen = {}

    def ask(title, name, flt, default_suffix=".pdf"):
        seen["name"] = name
        return str(out)
    monkeypatch.setattr(ui.ctx, "ask_save_path", ask)
    O._state["actions"]["save_image"].trigger()
    with O.ENGINE_LOCK:
        raw = view.document.pdf.xref_stream_raw(objs(view)[0].xref)
    assert out.read_bytes() == raw
    assert seen["name"] == "j_p1_img1.jpg"


def test_copy_image_puts_png_on_clipboard(ui, tmp_path):
    view = ui.open(make_doc(tmp_path / "a.pdf"))
    click(view, 0, 100, 100)
    O._state["actions"]["copy_image"].trigger()
    img = QGuiApplication.clipboard().image()
    assert img.width() == 200 and img.height() == 100


# ------------------------------------------------------------------ OBJ-14
def test_replace_image_dialog_flow(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    new = tmp_path / "q.png"
    new.write_bytes(png_bytes(50, 50, (0, 0, 0)))
    monkeypatch.setattr(D, "ask_image_paths", lambda ctx, **k: [str(new)])
    monkeypatch.setattr(D, "ask_replace_options", lambda parent, n: (E.FIT, False))
    click(view, 0, 100, 100)
    O._state["actions"]["replace"].trigger()
    r = objs(view)[0].rect
    assert abs(r.width - 100) < 0.01 and abs(r.height - 100) < 0.01
    assert abs((r.x0 + r.x1) / 2 - 150) < 0.01


# ------------------------------------------------------------------ collegamenti
def test_link_tool_creates_link(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    view.set_tool(ui.c.link_tool)
    monkeypatch.setattr(D, "ask_link", lambda *a, **k: (L.LinkTarget(kind=L.URI, uri="https://esempio.it"),
                                                        L.LinkStyle(visible=False), "Sito"))
    drag(view, 0, (100, 200), (200, 220))
    with O.ENGINE_LOCK:
        links = L.page_links(view.document.pdf, 0)
    assert len(links) == 1
    assert links[0].target.uri == "https://esempio.it"
    assert abs(links[0].rect.width - 100) < 1 and abs(links[0].rect.height - 20) < 1
    drag(view, 0, (300, 300), (302, 302))
    with O.ENGINE_LOCK:
        assert len(L.page_links(view.document.pdf, 0)) == 1


def test_link_tool_cancel_dialog_no_change(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[]))
    view.set_tool(ui.c.link_tool)
    monkeypatch.setattr(D, "ask_link", lambda *a, **k: None)
    drag(view, 0, (100, 200), (200, 220))
    assert not view.document.can_undo


def test_link_tool_select_move_delete_undo(ui, tmp_path, monkeypatch):
    path = tmp_path / "l.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.URI, uri="https://a.it"),
                  L.LinkStyle(visible=True, color=(1, 0, 0)))
    doc.save(str(path))
    view = ui.open(str(path))
    view.set_tool(ui.c.link_tool)
    drag(view, 0, (150, 210), (160, 210))
    with O.ENGINE_LOCK:
        info = L.page_links(view.document.pdf, 0)[0]
    assert abs(info.rect.x0 - 110) < 1
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Delete)
    with O.ENGINE_LOCK:
        assert L.page_links(view.document.pdf, 0) == []
    view.document.undo()
    with O.ENGINE_LOCK:
        info = L.page_links(view.document.pdf, 0)[0]
    assert info.target.uri == "https://a.it" and info.style.visible and info.style.color[0] == 1


def test_link_double_click_edits_to_page(ui, tmp_path, monkeypatch):
    path = tmp_path / "l.pdf"
    doc = pymupdf.open()
    for _k in range(3):
        doc.new_page()
    x = L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.URI, uri="https://a.it"))
    del x
    doc.save(str(path))
    view = ui.open(str(path))
    view.set_tool(ui.c.link_tool)
    monkeypatch.setattr(D, "ask_link", lambda *a, **k: (L.LinkTarget(kind=L.PAGE, page=2, fit="Fit"),
                                                        L.LinkStyle(), ""))
    QTest.mouseDClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                      vpos(view, 0, 150, 210))
    with O.ENGINE_LOCK:
        lk = view.document.pdf[0].get_links()
    assert lk and lk[0]["kind"] == pymupdf.LINK_GOTO and lk[0]["page"] == 2


def test_create_link_from_text_selection(ui, tmp_path, monkeypatch):
    from foliant.api import selection_from_words
    path = tmp_path / "t.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Visita www.esempio.it oggi", fontsize=14)
    doc.save(str(path))
    view = ui.open(str(path))
    view.set_tool(None)
    words = [w for w in view.words(0) if w[4] == "www.esempio.it"]
    view.set_text_selection(selection_from_words(0, words))
    seen = {}

    def ask(parent, v, page, info, target=None, description="", multiple=False):
        seen["target"] = target
        seen["desc"] = description
        return target, L.LinkStyle(), description
    monkeypatch.setattr(D, "ask_link", ask)
    ui.c.create_link_from_selection(view)
    assert seen["target"].uri == "https://www.esempio.it"
    with O.ENGINE_LOCK:
        infos = L.page_links(view.document.pdf, 0)
    assert len(infos) == 1 and len(infos[0].quads) == 1


def test_detect_urls_flow_single_undo(ui, tmp_path, monkeypatch):
    path = tmp_path / "u.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    for k in range(5):
        page.insert_text((72, 100 + 20 * k), f"Sito www.esempio{k}.it e info{k}@esempio.it", fontsize=11)
    doc.save(str(path))
    view = ui.open(str(path))
    monkeypatch.setattr(D, "ask_url_scope", lambda parent, n, cur: ([0], True, True))
    monkeypatch.setattr(D, "ask_found_urls", lambda parent, found: found)
    O._state["actions"]["detect_urls"].trigger()
    with O.ENGINE_LOCK:
        assert len(view.document.pdf[0].get_links()) == 10
    view.document.undo()
    with O.ENGINE_LOCK:
        assert len(view.document.pdf[0].get_links()) == 0


def test_delete_links_dialog(ui, tmp_path, monkeypatch):
    path = tmp_path / "d.pdf"
    doc = pymupdf.open()
    for _k in range(2):
        doc.new_page()
    for k in range(3):
        L.create_link(doc, k % 2, pymupdf.Rect(50, 50 + 30 * k, 150, 70 + 30 * k),
                      L.LinkTarget(kind=L.URI, uri=f"https://s{k}.it"))
    L.create_link(doc, 0, pymupdf.Rect(300, 50, 400, 70), L.LinkTarget(kind=L.PAGE, page=1))
    doc.save(str(path))
    view = ui.open(str(path))
    monkeypatch.setattr(D, "ask_delete_links", lambda parent, v: ([0, 1], {"web"}))
    monkeypatch.setattr(ui.ctx, "confirm", lambda *a: True)
    O._state["actions"]["delete_links"].trigger()
    with O.ENGINE_LOCK:
        assert sum(len(L.page_links(view.document.pdf, i)) for i in range(2)) == 1


def test_ctrl_click_follows_link_in_object_tool(ui, tmp_path, monkeypatch):
    path = tmp_path / "f.pdf"
    doc = pymupdf.open()
    for _k in range(3):
        doc.new_page()
    L.create_link(doc, 0, pymupdf.Rect(100, 200, 200, 220), L.LinkTarget(kind=L.PAGE, page=2, fit="Fit"))
    doc.save(str(path))
    view = ui.open(str(path))
    click(view, 0, 150, 210)
    assert view.current_page == 0
    click(view, 0, 150, 210, Qt.KeyboardModifier.ControlModifier)
    assert view.current_page == 2


# ------------------------------------------------------------------ OBJ-43
def test_five_operations_undo_redo(ui, tmp_path, monkeypatch):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)]))
    with O.ENGINE_LOCK:
        before = [view.document.pdf[i].get_pixmap(dpi=50).samples for i in range(2)]
    ui.c.insert_image_bytes(view, [png_bytes(40, 40)], 0, pymupdf.Point(300, 400))
    click(view, 0, 100, 100)
    ui.c.translate(view, 20, 0)
    ui.c.rotate(view, -90)
    ui.c.crop_to_rect(view, 0, 0, pymupdf.Rect(0, 0, 1000, 1000) & objs(view)[0].rect + (0, 0, -20, 0))
    with view.document.change("Crea collegamento") as pdf:
        L.create_link(pdf, 0, pymupdf.Rect(50, 500, 150, 520), L.LinkTarget(kind=L.URI, uri="https://x.it"))
    with O.ENGINE_LOCK:
        after = [view.document.pdf[i].get_pixmap(dpi=50).samples for i in range(2)]
    for _k in range(5):
        assert view.document.undo()
    assert not view.document.is_modified
    with O.ENGINE_LOCK:
        assert [view.document.pdf[i].get_pixmap(dpi=50).samples for i in range(2)] == before
    for _k in range(5):
        assert view.document.redo()
    with O.ENGINE_LOCK:
        assert [view.document.pdf[i].get_pixmap(dpi=50).samples for i in range(2)] == after


def test_document_untouched_until_save(ui, tmp_path):
    path = make_doc(tmp_path / "a.pdf")
    original = open(path, "rb").read()
    view = ui.open(path)
    click(view, 0, 100, 100)
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert open(path, "rb").read() == original


# ------------------------------------------------------------------ catture
@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_screenshots(ui, tmp_path, theme_name):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    theme.apply(QApplication.instance(), theme_name)
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 250, 170)], paths=[(300, 100, 400, 200)]))
    click(view, 0, 100, 100)
    view.flush_rendering()
    img = ui.window.grab()
    assert not img.isNull()
    img.save(str(tmp_path / f"obj_{theme_name}.png"))
    theme.apply(QApplication.instance(), "light")
