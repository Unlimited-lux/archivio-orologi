"""Azioni della modalità Pagine: barra contestuale, menu e scorciatoie."""

from __future__ import annotations

import importlib

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import _, bind_icon

from . import engine as E


def _dialog(ctrl, module: str, func: str, **kwargs):
    """Apre un dialogo di un sotto-modulo con le pagine selezionate."""
    def run():
        view = ctrl.view()
        if view is None:
            return
        try:
            mod = importlib.import_module(f"{__package__}.{module}")
            fn = getattr(mod, func)
        except Exception as e:
            ctrl.ctx.error(_("Funzione non disponibile"), _("Impossibile aprire la funzione: {e}").format(e=e))
            return
        try:
            fn(ctrl.ctx, view, ctrl.selected(), **kwargs)
        except Exception as e:   # un errore di un dialogo non deve chiudere l'applicazione
            ctrl.ctx.error(_("Errore"), _("Operazione non riuscita: {e}").format(e=e))
    return run


def build_actions(ctx, c, mode) -> None:
    win = ctx.window
    structural: list[QAction] = []

    scoped: list[QAction] = []

    def act(text, icon_name, slot, shortcut=None, structural_op=True, tip=None, key=None):
        """`shortcut`: scorciatoia valida solo nella modalità Pagine; `key`: tasto gestito
        dalla griglia quando ha il fuoco (Canc, Ctrl+C/X/V), mostrato solo nel suggerimento."""
        a = ctx.action(text, icon_name, slot, tooltip=tip)
        base = tip or text
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            scoped.append(a)
        if key is not None:
            seq = QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)
            base = f"{base} ({seq})"
            a.setToolTip(base)
        a.setProperty("baseTip", base)
        if structural_op:
            structural.append(a)
        c.all_actions.append(a)
        return a

    def menu_button(text, icon_name, actions, tip=None) -> QToolButton:
        btn = QToolButton(win)
        btn.setText(text)
        btn.setToolTip(tip or text)
        bind_icon(btn, icon_name)
        btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setAutoRaise(True)
        m = QMenu(btn)
        for a in actions:
            if a is None:
                m.addSeparator()
            else:
                m.addAction(a)
        btn.setMenu(m)
        ctx.window.register_document_action(btn)
        return btn

    # --- comandi principali
    c.act_rotate_left = act(_("Ruota a sinistra"), "rotate-ccw", lambda: c.rotate(-90), "Ctrl+Shift+-")
    c.act_rotate_right = act(_("Ruota a destra"), "rotate-cw", lambda: c.rotate(90), "Ctrl+Shift++")
    c.act_rotate_180 = act(_("Ruota di 180°"), "refresh-cw", lambda: c.rotate(180))
    c.act_delete = act(_("Elimina"), "trash-2", c.delete, key=QKeySequence.StandardKey.Delete,
                       tip=_("Elimina le pagine selezionate"))
    c.act_duplicate = act(_("Duplica"), "copy-plus", lambda: c.duplicate(),
                          tip=_("Duplica le pagine selezionate"))
    c.act_duplicate_dlg = act(_("Duplica…"), "copy-plus", _dialog(c, "dialogs", "open_duplicate_dialog"))
    c.act_extract = act(_("Estrai…"), "file-output", _dialog(c, "dialogs", "open_extract_dialog"),
                        structural_op=False, tip=_("Estrai le pagine in un nuovo PDF"))
    c.act_extract_images = act(_("Estrai come immagini…"), "file-image",
                               _dialog(c, "images_dialogs", "open_export_images_dialog"), structural_op=False)
    c.act_replace = act(_("Sostituisci…"), "replace", _dialog(c, "dialogs", "open_replace_dialog"))
    c.act_split = act(_("Dividi documento…"), "split", _dialog(c, "dialogs", "open_split_dialog"),
                      structural_op=False)
    c.act_merge = ctx.action(_("Unisci file…"), "combine", lambda: _merge(c), requires_document=False)
    c.all_actions.append(c.act_merge)

    # --- inserisci
    c.act_insert_blank = act(_("Pagina vuota…"), "file-plus", _dialog(c, "dialogs", "open_insert_blank_dialog"))
    c.act_insert_file = act(_("Da file…"), "file-input", _dialog(c, "dialogs", "open_insert_file_dialog"))
    c.act_insert_images = act(_("Da immagini…"), "image-plus", _dialog(c, "images_dialogs", "open_insert_images_dialog"))
    c.act_insert_clipboard = act(_("Dagli appunti…"), "clipboard-paste", _dialog(c, "images_dialogs", "open_paste_dialog"))
    c.act_insert_scanner = act(_("Da scanner…"), "scan", _dialog(c, "dialogs", "open_scanner_dialog"))

    # --- sposta e riordina
    c.act_move_to = act(_("Sposta in…"), "move", _dialog(c, "dialogs", "open_move_dialog"))
    c.act_move_start = act(_("Sposta all'inizio"), "arrow-up-to-line", c.move_start, "Ctrl+Home")
    c.act_move_end = act(_("Sposta alla fine"), "arrow-down-to-line", c.move_end, "Ctrl+End")
    c.act_reverse = act(_("Inverti ordine"), "arrow-down-up", c.reverse)
    c.act_swap = act(_("Scambia due pagine"), "arrow-right-left", c.swap)
    c.act_swap_pairs = act(_("Scambia coppie"), "repeat", c.swap_pairs)
    c.act_collate = act(_("Fascicola fronte/retro…"), "book-copy", _dialog(c, "dialogs", "open_collate_dialog"))

    # --- appunti
    c.act_copy = act(_("Copia pagine"), "copy", lambda: c.copy_pages(False), key=QKeySequence.StandardKey.Copy,
                     structural_op=False)
    c.act_cut = act(_("Taglia pagine"), "scissors", lambda: c.copy_pages(True), key=QKeySequence.StandardKey.Cut)
    c.act_paste = act(_("Incolla pagine dopo"), "clipboard-paste", lambda: c.paste_pages(False),
                      key=QKeySequence.StandardKey.Paste)
    c.act_paste_before = act(_("Incolla pagine prima"), "clipboard-paste", lambda: c.paste_pages(True))
    c.act_detect = act(_("Trova pagine vuote e duplicate…"), "scan-search", lambda: c.find_blank_and_duplicates(),
                       structural_op=False)
    c.act_share = act(_("Invia per posta"), "mail", lambda: c.share("mail"), structural_op=False)
    c.act_share_save = act(_("Salva una copia delle pagine…"), "share-2", lambda: c.share("save"),
                           structural_op=False)

    # --- impaginazione (sotto-modulo layout)
    c.act_crop = act(_("Ritaglia…"), "crop", _dialog(c, "layout_dialogs", "open_crop_dialog"))
    c.act_margins = act(_("Rimuovi margini bianchi…"), "scan-line", _dialog(c, "layout_dialogs", "open_remove_margins_dialog"))
    c.act_boxes = act(_("Riquadri di stampa…"), "square-dashed", _dialog(c, "layout_dialogs", "open_boxes_dialog"))
    c.act_resize = act(_("Formato pagina…"), "scaling", _dialog(c, "layout_dialogs", "open_resize_dialog"))
    c.act_split_page = act(_("Dividi pagina…"), "square-split-horizontal",
                           _dialog(c, "layout_dialogs", "open_split_page_dialog"))
    c.act_nup = act(_("Pagine affiancate…"), "grid-2x2", _dialog(c, "layout_dialogs", "open_nup_dialog"),
                    structural_op=False)

    # --- timbri (sotto-modulo stamp)
    c.act_numbers = act(_("Numeri di pagina…"), "hash", _dialog(c, "stamp_dialogs", "open_page_numbers_dialog"))
    c.act_bates = act(_("Numerazione Bates…"), "list-ordered", _dialog(c, "stamp_dialogs", "open_bates_dialog"))
    c.act_header = act(_("Intestazione e piè di pagina…"), "panel-top",
                       _dialog(c, "stamp_dialogs", "open_header_footer_dialog"))
    c.act_watermark = act(_("Filigrana…"), "droplet", _dialog(c, "stamp_dialogs", "open_watermark_dialog"))
    c.act_background = act(_("Sfondo…"), "paint-bucket", _dialog(c, "stamp_dialogs", "open_background_dialog"))
    c.act_remove_stamps = act(_("Rimuovi filigrane, numeri e sfondi…"), "file-x",
                              _dialog(c, "stamp_dialogs", "open_remove_stamps_dialog"))
    c.act_labels = act(_("Etichette di pagina…"), "tag", _dialog(c, "stamp_dialogs", "open_page_labels_dialog"))

    # --- selezione
    c.act_select_all = ctx.action(_("Seleziona tutte"), "check-check", lambda: c.organizer.grid.selectAll())
    c.act_select_odd = ctx.action(_("Pagine dispari"), None, lambda: c.select_filter(E.FILTER_ODD))
    c.act_select_even = ctx.action(_("Pagine pari"), None, lambda: c.select_filter(E.FILTER_EVEN))
    c.act_select_portrait = ctx.action(_("Pagine verticali"), None, lambda: c.select_filter(E.FILTER_PORTRAIT))
    c.act_select_landscape = ctx.action(_("Pagine orizzontali"), None, lambda: c.select_filter(E.FILTER_LANDSCAPE))
    c.act_select_range = ctx.action(_("Intervallo…"), None, c.select_range)
    c.act_unlock = ctx.action(_("Sblocca"), "lock-open", c.unlock, tooltip=_("Sblocca con la password del proprietario"))

    c.structural_actions = structural
    mode.scope_shortcuts(*scoped)
    c.organizer.grid.keyCommands = {
        QKeySequence.StandardKey.Delete: c.act_delete, QKeySequence.StandardKey.Copy: c.act_copy,
        QKeySequence.StandardKey.Cut: c.act_cut, QKeySequence.StandardKey.Paste: c.act_paste,
    }

    # --- barra contestuale
    mode.add_action(c.act_rotate_left)
    mode.add_action(c.act_rotate_right)
    mode.add_action(c.act_delete)
    mode.add_action(c.act_duplicate)
    mode.add_separator()
    mode.add_widget(menu_button(_("Inserisci"), "file-plus-2", [
        c.act_insert_blank, c.act_insert_file, c.act_insert_images, c.act_insert_clipboard, c.act_insert_scanner]))
    mode.add_action(c.act_extract)
    mode.add_widget(menu_button(_("Dividi e unisci"), "split", [
        c.act_split, c.act_merge, None, c.act_replace, c.act_split_page]))
    mode.add_widget(menu_button(_("Disponi"), "move", [
        c.act_move_to, c.act_move_start, c.act_move_end, None, c.act_reverse, c.act_swap, c.act_swap_pairs,
        c.act_collate, None, c.act_rotate_180]))
    mode.add_separator()
    mode.add_widget(menu_button(_("Formato"), "crop", [
        c.act_crop, c.act_margins, c.act_resize, c.act_boxes, None, c.act_nup]))
    mode.add_widget(menu_button(_("Timbri"), "stamp", [
        c.act_numbers, c.act_bates, c.act_header, None, c.act_watermark, c.act_background, c.act_remove_stamps,
        None, c.act_labels]))
    mode.add_widget(menu_button(_("Altro"), "ellipsis", [
        c.act_copy, c.act_cut, c.act_paste, c.act_paste_before, None, c.act_detect, c.act_extract_images, c.act_share,
        c.act_share_save, None, c.act_select_all, c.act_select_odd, c.act_select_even, c.act_select_portrait,
        c.act_select_landscape, c.act_select_range]))
    mode.add_action(c.act_unlock)
    c.act_unlock.setVisible(False)

    # --- menu contestuale della griglia
    insert_menu = QMenu(_("Inserisci"), c.organizer)
    for a in (c.act_insert_blank, c.act_insert_file, c.act_insert_images, c.act_insert_clipboard):
        insert_menu.addAction(a)
    c.context_actions = [c.act_rotate_left, c.act_rotate_right, c.act_rotate_180, None,
                         c.act_copy, c.act_cut, c.act_paste, c.act_paste_before, None,
                         c.act_duplicate, c.act_delete, insert_menu, None,
                         c.act_extract, c.act_extract_images, c.act_replace, None,
                         c.act_move_to, c.act_move_start, c.act_move_end, None, c.act_crop, c.act_split_page]

    # --- menu principale e Home
    ctx.add_menu_action(c.act_merge, _("Pagine"))
    ctx.add_menu_action(c.act_split, _("Pagine"))
    ctx.add_home_action(_("Unisci PDF"), "combine", lambda: _merge(c),
                        _("Combina più file in un unico PDF"), order=40)


def _merge(c) -> None:
    try:
        from .dialogs import open_merge_dialog
        open_merge_dialog(c.ctx)
    except Exception as e:
        c.ctx.error(_("Unisci file"), _("Operazione non riuscita: {e}").format(e=e))
