"""Immagini, oggetti e collegamenti (sezione 4 della specifica, OBJ-*).

Strumenti della modalità «Modifica»: selezione e trasformazione di immagini,
tracciati e oggetti composti; inserimento di immagini da file, appunti e
trascinamento; copia/incolla tra pagine e documenti; collegamenti.
"""

from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import ENGINE_LOCK, Context, _, add_translations, bind_icon

from . import engine as E
from .content import IMAGE, PATH
from .controller import IMAGE_EXTS, ObjectsController, clipboard_has_image
from .translations import EN

ORDER = 31

_state: dict = {}


def controller() -> ObjectsController | None:
    return _state.get("controller")


def register(ctx: Context) -> None:
    add_translations("en", EN)
    c = ObjectsController(ctx)
    _state["controller"] = c
    _state["ctx"] = ctx
    mode = ctx.add_mode("modifica", _("Modifica"), "pencil-line", 30)
    acts: dict = {}
    _state["actions"] = acts

    def tool_action(key, text, icon_name, tool, tip):
        act = ctx.action(text, icon_name, None, checkable=True, tooltip=tip)
        act.triggered.connect(lambda checked=False, t=tool: _toggle_tool(ctx, t, checked))
        acts[key] = act
        mode.add_action(act)
        return act

    tool_action("objects", _("Oggetti"), "mouse-pointer-2", c.object_tool,
                _("Seleziona e modifica immagini e oggetti"))
    a = ctx.action(_("Aggiungi immagine"), "image-plus", lambda: c.ask_insert_image(ctx.current_view()))
    acts["insert"] = a
    mode.add_action(a)
    tool_action("links", _("Collegamento"), "link", c.link_tool, _("Crea e modifica collegamenti"))
    mode.add_separator()

    def sel_action(key, text, icon_name, fn, shortcut=None):
        act = ctx.action(text, icon_name, lambda: fn(ctx.current_view()))
        acts[key] = act
        return act

    sel_action("rotate_ccw", _("Ruota a sinistra"), "rotate-ccw", lambda v: c.rotate(v, 90))
    sel_action("rotate_cw", _("Ruota a destra"), "rotate-cw", lambda v: c.rotate(v, -90))
    sel_action("flip_h", _("Capovolgi orizzontalmente"), "flip-horizontal-2", lambda v: c.flip(v, True))
    sel_action("flip_v", _("Capovolgi verticalmente"), "flip-vertical-2", lambda v: c.flip(v, False))
    sel_action("crop", _("Ritaglia"), "crop", c.start_crop)
    sel_action("crop_reset", _("Ripristina ritaglio"), "undo-2", c.reset_crop)
    sel_action("crop_apply", _("Applica ritaglio definitivamente"), "scissors", c.apply_crop)
    sel_action("replace", _("Sostituisci immagine…"), "replace", c.replace_image)
    sel_action("replace_clip", _("Sostituisci con gli appunti"), "clipboard-paste", c.replace_with_clipboard)
    sel_action("save_image", _("Salva immagine come…"), "image-down", c.save_image)
    sel_action("copy_image", _("Copia immagine"), "copy", c.copy_image)
    sel_action("delete", _("Elimina"), "trash-2", c.delete)
    sel_action("copy", _("Copia"), "copy", c.copy)
    sel_action("cut", _("Taglia"), "scissors", c.cut)
    sel_action("duplicate", _("Duplica"), "copy-plus", lambda v: c.duplicate(v, E.PASTE_OFFSET, E.PASTE_OFFSET))
    sel_action("move_page", _("Sposta in pagina…"), "file-output", c.ask_move_to_page)
    sel_action("similar", _("Seleziona simili"), "shapes", c.select_similar)
    sel_action("make_link", _("Crea collegamento"), "link-2", c.create_link_from_selection)
    acts["paste"] = ctx.action(_("Incolla"), "clipboard-paste", lambda: c.paste(ctx.current_view()))
    acts["paste_in_place"] = ctx.action(_("Incolla in posizione"), "clipboard-paste",
                                        lambda: c.paste(ctx.current_view(), in_place=True))
    for key, where, text, icon_name in (("front", E.FRONT, _("Porta in primo piano"), "bring-to-front"),
                                        ("forward", E.FORWARD, _("Porta avanti"), "arrow-up"),
                                        ("backward", E.BACKWARD, _("Porta indietro"), "arrow-down"),
                                        ("back", E.BACK, _("Porta in fondo"), "send-to-back")):
        sel_action(key, text, icon_name, lambda v, w=where: c.reorder(v, w))
    for key, how, text, icon_name in (("al_left", E.LEFT, _("Allinea a sinistra"), "align-start-vertical"),
                                      ("al_hc", E.HCENTER, _("Centra in orizzontale"), "align-center-vertical"),
                                      ("al_right", E.RIGHT, _("Allinea a destra"), "align-end-vertical"),
                                      ("al_top", E.TOP, _("Allinea in alto"), "align-start-horizontal"),
                                      ("al_vc", E.VCENTER, _("Centra in verticale"), "align-center-horizontal"),
                                      ("al_bottom", E.BOTTOM, _("Allinea in basso"), "align-end-horizontal")):
        sel_action(key, text, icon_name, lambda v, h=how: c.align(v, h))
    sel_action("dist_h", _("Distribuisci in orizzontale"), "align-horizontal-distribute-center",
               lambda v: c.distribute(v, True))
    sel_action("dist_v", _("Distribuisci in verticale"), "align-vertical-distribute-center",
               lambda v: c.distribute(v, False))
    acts["detect_urls"] = ctx.action(_("Crea collegamenti dagli URL…"), "link",
                                     lambda: c.detect_urls(ctx.current_view()))
    acts["delete_links"] = ctx.action(_("Elimina collegamenti…"), "unlink",
                                      lambda: c.delete_links_dialog(ctx.current_view()))
    from .panel import ObjectPanel, add_opacity_widget
    for key in ("rotate_ccw", "rotate_cw", "flip_h", "flip_v", "crop", "replace", "delete"):
        b = QToolButton()
        b.setDefaultAction(acts[key])
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        b.setAutoRaise(True)
        mode.add_widget(b)
    # menu «Disponi»
    arrange = QMenu(ctx.window)
    for key in ("front", "forward", "backward", "back"):
        arrange.addAction(acts[key])
    arrange.addSeparator()
    for key in ("al_left", "al_hc", "al_right", "al_top", "al_vc", "al_bottom"):
        arrange.addAction(acts[key])
    arrange.addSeparator()
    arrange.addAction(acts["dist_h"])
    arrange.addAction(acts["dist_v"])
    btn = QToolButton()
    btn.setText(_("Disponi"))
    btn.setToolTip(_("Ordine, allineamento e distribuzione"))
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    btn.setMenu(arrange)
    bind_icon(btn, "layers")
    mode.add_widget(btn)
    _state["arrange_button"] = btn
    add_opacity_widget(ctx, c, mode)
    more = QMenu(ctx.window)
    for key in ("paste", "paste_in_place", "duplicate", "move_page", "similar", "crop_reset", "crop_apply",
                "replace_clip", "save_image", "copy_image", "make_link"):
        more.addAction(acts[key])
    more.addSeparator()
    more.addAction(acts["detect_urls"])
    more.addAction(acts["delete_links"])
    btn2 = QToolButton()
    btn2.setText(_("Altro"))
    btn2.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    btn2.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    btn2.setMenu(more)
    bind_icon(btn2, "ellipsis")
    mode.add_widget(btn2)
    ctx.add_menu_action(acts["detect_urls"], "Strumenti")
    ctx.add_view_menu_provider(lambda view, pe, menu: _context_menu(ctx, c, view, pe, menu))
    panel = ObjectPanel(ctx, c)
    ctx.add_properties_panel("oggetti", panel)
    _state["panel"] = panel
    c.selectionChanged.connect(lambda: _update_actions(ctx, c))
    ctx.currentViewChanged.connect(lambda v: (_install_drop(ctx, c, v), _update_actions(ctx, c)))
    ctx.documentOpened.connect(lambda v: _install_drop(ctx, c, v))
    mode.entered.connect(lambda: _on_mode_entered(ctx, c))
    mode.left.connect(lambda: _on_mode_left(ctx, c))
    _update_actions(ctx, c)


def _toggle_tool(ctx: Context, tool, checked: bool) -> None:
    view = ctx.current_view()
    if view is None:
        return
    if checked:
        if ctx.current_mode() != "modifica":
            ctx.set_mode("modifica")
        view.set_tool(tool)
    elif view.tool is tool:
        view.set_tool(None)
    _sync_tool_buttons(ctx)


def _sync_tool_buttons(ctx: Context) -> None:
    c = controller()
    acts = _state.get("actions", {})
    view = ctx.current_view()
    tool = view.tool if view is not None else None
    if "objects" in acts:
        acts["objects"].setChecked(tool is c.object_tool)
        acts["links"].setChecked(tool is c.link_tool)


def _on_mode_entered(ctx: Context, c: ObjectsController) -> None:
    from PySide6.QtCore import QTimer

    def later():
        view = ctx.current_view()
        if view is not None and ctx.current_mode() == "modifica" and view.tool is view._default_tool:
            view.set_tool(c.object_tool)
        _sync_tool_buttons(ctx)
    QTimer.singleShot(0, later)


def _on_mode_left(ctx: Context, c: ObjectsController) -> None:
    view = ctx.current_view()
    if view is not None and view.tool in (c.object_tool, c.link_tool):
        view.set_tool(None)
    ctx.hide_properties()
    _sync_tool_buttons(ctx)


def _update_actions(ctx: Context, c: ObjectsController) -> None:
    acts = _state.get("actions", {})
    view = ctx.current_view()
    objs = c.selected_objects(view) if view is not None else []
    n = len(objs)
    single_image = n == 1 and objs[0].kind == IMAGE
    has_doc = view is not None
    for key in ("rotate_ccw", "rotate_cw", "flip_h", "flip_v", "delete", "copy", "cut", "duplicate", "move_page",
                "front", "forward", "backward", "back", "al_left", "al_hc", "al_right", "al_top", "al_vc",
                "al_bottom", "make_link"):
        if key in acts:
            acts[key].setEnabled(has_doc and n > 0)
    for key in ("crop", "crop_reset", "crop_apply", "replace", "replace_clip", "save_image", "copy_image"):
        if key in acts:
            acts[key].setEnabled(has_doc and single_image)
    if single_image:
        w = objs[0].wrapper
        acts["crop_reset"].setEnabled(bool(w and w.clip_unit))
        acts["crop_apply"].setEnabled(bool(w and w.clip_unit))
    for key in ("dist_h", "dist_v"):
        acts[key].setEnabled(has_doc and n >= 3)
    acts["similar"].setEnabled(n == 1 and objs[0].kind == PATH)
    panel = _state.get("panel")
    if panel is not None:
        panel.refresh()
        if n and view is not None and view.tool is c.object_tool:
            ctx.show_properties("oggetti")
    _sync_tool_buttons(ctx)


def _context_menu(ctx: Context, c: ObjectsController, view, pe, menu: QMenu) -> None:
    acts = _state["actions"]
    if view.tool is c.link_tool:
        if pe.page is not None:
            info = c.link_tool._hit(view, pe.page, pe.point)
            if info is not None:
                c.link_tool.selection = [info.xref]
                c.link_tool.page = pe.page
                menu.addAction(_("Proprietà del collegamento…"),
                               lambda: c.edit_link_interactive(view, pe.page, [info.xref]))
                menu.addAction(_("Elimina collegamento"), lambda: c.delete_links(view, pe.page, [info.xref]))
        menu.addAction(acts["detect_urls"])
        return
    if view.text_selection is not None and view.text_selection.text:
        menu.addAction(_("Crea collegamento…"), lambda: c.create_link_from_selection(view))
    if view.tool is not c.object_tool:
        if ctx.current_mode() == "modifica" and clipboard_has_image():
            menu.addAction(_("Incolla come immagine"), lambda: c.paste(view, at=pe.point, page=pe.page))
        return
    if pe.page is not None:
        parsed = c.parsed(view, pe.page)
        hits = E.hit_test(parsed, pe.point) if parsed else []
        if hits and not (c.sel_page == pe.page and hits[0] in c.selection):
            c.set_selection(view, pe.page, [hits[0]])
    objs = c.selected_objects(view)
    if objs:
        for key in ("cut", "copy"):
            menu.addAction(acts[key])
    paste_here = menu.addAction(_("Incolla qui"), lambda: c.paste(view, at=pe.point, page=pe.page))
    paste_here.setEnabled(pe.page is not None and clipboard_has_image() or _has_internal())
    img_paste = menu.addAction(_("Incolla come immagine"), lambda: c.paste(view, at=pe.point, page=pe.page))
    img_paste.setEnabled(pe.page is not None and clipboard_has_image())
    if not objs:
        return
    menu.addAction(acts["duplicate"])
    menu.addAction(acts["delete"])
    menu.addSeparator()
    menu.addAction(acts["rotate_ccw"])
    menu.addAction(acts["rotate_cw"])
    menu.addAction(acts["flip_h"])
    menu.addAction(acts["flip_v"])
    order = menu.addMenu(_("Ordine"))
    for key in ("front", "forward", "backward", "back"):
        order.addAction(acts[key])
    if len(objs) == 1 and objs[0].kind == IMAGE:
        menu.addSeparator()
        for key in ("crop", "crop_reset", "replace", "replace_clip", "save_image", "copy_image"):
            menu.addAction(acts[key])
    menu.addSeparator()
    menu.addAction(acts["make_link"])
    menu.addAction(acts["move_page"])


def _has_internal() -> bool:
    from . import controller as ctl
    return ctl._last_payload is not None


# ------------------------------------------------------------------ trascinamento
class _DropFilter(QObject):
    """Accetta il rilascio di immagini sulla vista; i PDF passano alla finestra."""

    def __init__(self, ctx: Context, c: ObjectsController, view):
        super().__init__(view)
        self.ctx = ctx
        self.c = c
        self.view = view

    @staticmethod
    def image_paths(mime) -> tuple[list[str], list[str]]:
        imgs, other = [], []
        if mime.hasUrls():
            for u in mime.urls():
                if u.isLocalFile():
                    p = u.toLocalFile()
                    (imgs if p.lower().endswith(IMAGE_EXTS) else other).append(p)
        return imgs, other

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            imgs, _other = self.image_paths(ev.mimeData())
            if imgs or ev.mimeData().hasImage():
                ev.acceptProposedAction()
                return True
            return False
        if t == QEvent.Type.Drop:
            mime = ev.mimeData()
            imgs, other = self.image_paths(mime)
            if not imgs and not mime.hasImage():
                return False
            pos = QPointF(ev.position())
            hit = self.view.view_to_page(pos, clamp=True)
            if hit is None:
                return True
            page, pt = hit
            if imgs:
                if other:
                    pdfs = [p for p in other if p.lower().endswith(".pdf")]
                    rest = [os.path.basename(p) for p in other if p not in pdfs]
                    if rest:
                        self.ctx.info(_("Inserisci immagini"), _("File non inseriti perché non sono immagini:\n{f}")
                                      .format(f="\n".join(rest)))
                self.c.insert_image_files(self.view, imgs, page, center=pt)
            else:
                from PySide6.QtCore import QBuffer, QIODevice
                img = mime.imageData()
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                img.save(buf, "PNG")
                self.c.insert_image_bytes(self.view, [bytes(buf.data())], page, center=pt)
            ev.acceptProposedAction()
            return True
        return False


class _TabFilter(QObject):
    """Tab e Maiusc+Tab scorrono gli oggetti invece di spostare il fuoco."""

    def __init__(self, c: ObjectsController, view):
        super().__init__(view)
        self.c = c
        self.view = view

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab) \
                and self.view.tool is self.c.object_tool:
            self.c.object_tool.key(self.view, ev)
            self.view.viewport().update()
            return True
        return False


def _install_drop(ctx: Context, c: ObjectsController, view) -> None:
    if view is None or getattr(view, "_objects_drop", None) is not None:
        return
    f = _DropFilter(ctx, c, view)
    view._objects_drop = f
    view.viewport().setAcceptDrops(True)
    view.viewport().installEventFilter(f)
    view._objects_tab = _TabFilter(c, view)
    view.installEventFilter(view._objects_tab)


__all__ = ["register", "controller", "ORDER", "pymupdf", "ENGINE_LOCK"]
