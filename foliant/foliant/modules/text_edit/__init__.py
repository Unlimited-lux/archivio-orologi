"""Modifica del testo di pagina (sezione 3 della specifica, TXT-*).

Strumenti nella modalità «Modifica» (condivisa con il modulo degli oggetti):
- Modifica testo: clic su un blocco per modificarlo in loco, clic sul bordo per
  selezionarlo come oggetto (spostare, ridimensionare, copiare, eliminare);
- Aggiungi testo: nuove caselle di testo scritte nel contenuto della pagina;
- Trova e sostituisci / Trova ed elimina.
"""

from __future__ import annotations

from foliant.api import Context, _, add_translations

from .translations import EN

ORDER = 30

ALIGN_LABELS = (("left", "A sinistra"), ("hcenter", "Al centro"), ("right", "A destra"), ("top", "In alto"),
                ("vcenter", "Al centro verticale"), ("bottom", "In basso"))

_controller = None


def controller():
    return _controller


def register(ctx: Context) -> None:
    global _controller
    from .controller import TextEditController
    from .ui_panel import TextPropertiesPanel

    add_translations("en", EN)
    try:
        from .find_dialog import TRANSLATIONS as FIND_EN
        add_translations("en", FIND_EN)
    except Exception:
        pass
    from . import fontlist, fonts
    fonts.set_user_mappings(fontlist.load_mappings())
    ctrl = TextEditController(ctx)
    _controller = ctrl
    ctx.text_edit = ctrl  # accesso per i test e per altri moduli

    mode = ctx.add_mode("modifica", _("Modifica"), "pencil-line", 30)
    panel = TextPropertiesPanel(ctrl)
    ctx.add_properties_panel("text_edit", panel)
    ctrl.panel = panel

    def use_edit_tool(checked: bool = True) -> None:
        view = ctx.current_view()
        if view is None:
            return
        view.set_tool(ctrl.edit_tool(view))
        ctx.show_properties("text_edit")

    def use_add_tool(checked: bool = True) -> None:
        view = ctx.current_view()
        if view is None:
            return
        view.set_tool(ctrl.add_tool(view))
        ctx.show_properties("text_edit")

    act_edit = ctx.action(_("Modifica testo"), "text-cursor-input", use_edit_tool, checkable=True,
                          tooltip=_("Modifica il testo esistente: clic per scrivere, clic sul bordo per "
                                    "spostare o ridimensionare il blocco"))
    act_add = ctx.action(_("Aggiungi testo"), "type", use_add_tool, checkable=True,
                         tooltip=_("Aggiungi una casella di testo: clic per una riga, trascina per un riquadro"))
    act_find = ctx.action(_("Trova e sostituisci"), "replace", lambda: open_find_dialog(ctx),
                          shortcut="Ctrl+H")
    mode.add_action(act_edit)
    mode.add_action(act_add)
    act_fonts = ctx.action(_("Font del documento"), "type-outline", lambda: open_fonts_dialog(ctx))
    mode.add_action(act_find)
    mode.add_action(act_fonts)
    mode.add_separator()
    ctrl.actions = {"edit": act_edit, "add": act_add, "find": act_find, "fonts": act_fonts}
    ctx.add_menu_action(act_find, _("Modifica"))

    def sync_actions(*_a) -> None:
        view = ctx.current_view()
        tool = view.tool if view is not None else None
        act_edit.setChecked(view is not None and tool is ctrl.tools(view)[0])
        act_add.setChecked(view is not None and tool is ctrl.tools(view)[1])

    def connect_view(view) -> None:
        if view is not None:
            try:
                view.toolChanged.connect(sync_actions)
            except Exception:
                pass

    ctx.documentOpened.connect(connect_view)
    ctx.currentViewChanged.connect(lambda v: sync_actions())

    def entered() -> None:
        view = ctx.current_view()
        if view is None:
            return
        # attiva la modifica del testo se nessun altro strumento della modalità è attivo
        if view.tool is view._default_tool:
            use_edit_tool()
        ctx.show_properties("text_edit")

    def left() -> None:
        for view in ctx.views():
            ctrl.commit_editor(view)
        ctx.hide_properties()

    mode.entered.connect(entered)
    mode.left.connect(left)

    def view_menu(view, pe, menu) -> None:
        if ctx.current_mode() != "modifica" or pe.page is None:
            return
        tool = ctrl.tools(view)[0]
        if view.tool is not tool:
            return
        from .analysis import block_at
        fpt = pe.point * ctrl.frame(pe.page, view)
        block = block_at(ctrl.blocks(view, pe.page), fpt)
        selected = ctrl.selected_blocks(view)
        if len(selected) >= 2:
            menu.addAction(_("Unisci blocchi (con uno spazio)"), lambda: ctrl.merge_selected(view, "space"))
            menu.addAction(_("Unisci blocchi (nuovo paragrafo)"), lambda: ctrl.merge_selected(view, "paragraph"))
            sub = menu.addMenu(_("Allinea"))
            for mode_id, label in ALIGN_LABELS:
                sub.addAction(_(label), lambda m=mode_id: ctrl.align_selected(view, m))
            dist = menu.addMenu(_("Distribuisci"))
            dist.addAction(_("In orizzontale"), lambda: ctrl.distribute_selected(view, "h"))
            dist.addAction(_("In verticale"), lambda: ctrl.distribute_selected(view, "v"))
            menu.addAction(_("Elimina blocchi"), lambda: ctrl.delete_selected(view))
            menu.addSeparator()
        if block is None or not block.editable:
            if ctrl._clipboard_block() is not None:
                menu.addAction(_("Incolla blocco di testo"), lambda: ctrl.paste_block(view))
            menu.addAction(_("Rileva di nuovo i blocchi"), lambda: ctrl.redetect(view, pe.page))
            return
        menu.addAction(_("Modifica testo"), lambda: ctrl.open_editor(view, pe.page, block))
        menu.addAction(_("Copia blocco"), lambda: ctrl.copy_block(view, pe.page, block))
        menu.addAction(_("Duplica blocco"), lambda: ctrl.duplicate_block(view, pe.page, block))
        menu.addAction(_("Elimina blocco"), lambda: ctrl.delete_block(view, pe.page, block))
        line_index = next((i for i, ln in enumerate(block.lines) if ln.bbox.y0 <= fpt.y <= ln.bbox.y1), -1)
        if line_index > 0:
            menu.addAction(_("Dividi il blocco da questa riga"),
                           lambda: ctrl.split_block_at(view, pe.page, block, line_index))
        menu.addAction(_("Rileva di nuovo i blocchi"), lambda: ctrl.redetect(view, pe.page))
        arrange = menu.addMenu(_("Disponi"))

        def single(fn):
            def run():
                tool.select(view, pe.page, block)
                fn()
            return run

        arrange.addAction(_("Ruota…"), single(lambda: ask_rotation(ctx, ctrl, view)))
        arrange.addAction(_("Ruota di 90° a sinistra"), single(lambda: ctrl.rotate_selected(view, 90)))
        arrange.addAction(_("Ruota di 90° a destra"), single(lambda: ctrl.rotate_selected(view, -90)))
        arrange.addAction(_("Porta in primo piano"), single(lambda: ctrl.restack_selected(view, True)))
        arrange.addAction(_("Porta sullo sfondo"), single(lambda: ctrl.restack_selected(view, False)))
        page_align = arrange.addMenu(_("Allinea alla pagina"))
        for mode_id, label in ALIGN_LABELS:
            page_align.addAction(_(label), single(lambda m=mode_id: ctrl.align_selected(view, m, to_page=True)))
        if block.invisible:
            menu.addAction(_("Converti in testo modificabile"), lambda: ctrl.convert_ocr_block(view, pe.page, block))

    ctx.add_view_menu_provider(view_menu)


def open_find_dialog(ctx: Context):
    """Apre (o porta in primo piano) il dialogo Trova e sostituisci."""
    view = ctx.current_view()
    if view is None:
        return None
    ctrl = _controller
    if ctrl is not None:
        ctrl.commit_editor(view)
    try:
        from .find_dialog import FindReplaceDialog
    except Exception as e:  # il dialogo non deve mai bloccare l'applicazione
        ctx.error(_("Trova e sostituisci"), str(e))
        return None
    dlg = getattr(ctx, "_text_find_dialog", None)
    if dlg is None:
        dlg = FindReplaceDialog(ctx, ctx.window)
        ctx._text_find_dialog = dlg
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def open_fonts_dialog(ctx: Context):
    """Dialogo «Font del documento» (TXT-11)."""
    if ctx.current_view() is None:
        return None
    if _controller is not None:
        _controller.commit_editor(ctx.current_view())
    from .fontlist_ui import FontsDialog
    dlg = FontsDialog(ctx, ctx.window)
    ctx._text_fonts_dialog = dlg
    dlg.show()
    return dlg


def ask_rotation(ctx: Context, ctrl, view) -> None:
    from PySide6.QtWidgets import QInputDialog
    value, ok = QInputDialog.getDouble(ctx.window, _("Ruota blocco"), _("Angolo (gradi, antiorario):"), 15.0,
                                       -360.0, 360.0, 1)
    if ok and value:
        ctrl.rotate_selected(view, value)
