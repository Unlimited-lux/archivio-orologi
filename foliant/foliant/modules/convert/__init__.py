"""Modulo Converti: conversione da/verso PDF, creazione di PDF, compressione e riparazione.

La logica senza interfaccia è in `engine.py` (`convert_pdf`), `create.py`,
`optimize.py`, `pdfa.py` e `batch.py`; le finestre in `*_dialog(s).py`;
i comandi in `actions.py`.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import _, add_translations, bind_icon

from .actions import ConvertController
from .i18n_en import EN

ORDER = 70


class _ClipboardWatcher(QObject):
    """Abilita «Dagli appunti» solo se gli appunti contengono un formato supportato."""

    def __init__(self, parent, action, controller):
        super().__init__(parent)
        self._action = action
        self._controller = controller

    @Slot()
    def update(self) -> None:
        act = self._action
        if act is None or not shiboken6.isValid(act):
            return
        act.setEnabled(self._controller.clipboard_supported())
        act.setToolTip(_("Crea un PDF dagli appunti") if act.isEnabled()
                       else _("Copia un'immagine, del testo o dei file per attivare questa voce"))


def register(ctx) -> None:
    add_translations("en", EN)
    c = ConvertController(ctx)
    ctx.convert_controller = c   # per test e altri moduli

    mode = ctx.add_mode("converti", _("Converti"), "file-output", 70)
    act_export = ctx.action(_("Esporta come…"), "file-output", lambda: c.export(), requires_document=False,
                            tooltip=_("Converti il PDF in un altro formato"))
    mode.add_action(act_export)
    for label, icon_name, fmt in ((_("In Word"), "file-text", "docx"), (_("In Excel"), "file-spreadsheet", "xlsx"),
                                  (_("In PowerPoint"), "presentation", "pptx"), (_("In immagini"), "file-image", "png"),
                                  (_("In testo"), "file-type", "txt"), (_("In HTML"), "code-xml", "html"),
                                  (_("In PDF/A"), "archive", "pdfa-2b")):
        mode.add_action(ctx.action(label, icon_name, lambda _c=False, f=fmt: c.export(f), requires_document=False,
                                   tooltip=_("Converti il documento: {f}").format(f=label)))
    mode.add_separator()

    # menu «Crea PDF»
    create_menu = QMenu(ctx.window)
    act_from_file = ctx.action(_("Da file…"), "file-plus", lambda: c.create_from_files(), requires_document=False)
    act_from_images = ctx.action(_("Da immagini…"), "images", lambda: c.create_from_images(), requires_document=False)
    act_from_url = ctx.action(_("Da pagina web…"), "globe", lambda: c.create_from_url(), requires_document=False)
    act_clipboard = ctx.action(_("Dagli appunti"), "clipboard-paste", lambda: c.create_from_clipboard(),
                               requires_document=False)
    act_scanner = ctx.action(_("Da scanner…"), "scan", lambda: c.create_from_scanner(), requires_document=False)
    act_photos = ctx.action(_("Da foto di documenti…"), "camera", lambda: c.create_from_photos(),
                            requires_document=False)
    act_screen = ctx.action(_("Da cattura schermo…"), "monitor", lambda: c.create_from_screen(),
                            requires_document=False)
    act_blank = ctx.action(_("PDF vuoto…"), "file", lambda: c.create_blank(), requires_document=False)
    act_portfolio = ctx.action(_("Crea portfolio…"), "folder-archive", lambda: c.create_portfolio(),
                               requires_document=False)
    act_edit_portfolio = ctx.action(_("Modifica portfolio…"), "folder-pen", lambda: c.edit_portfolio(),
                                    tooltip=_("Aggiungi o rimuovi i file del portfolio aperto"))
    act_batch_create = ctx.action(_("Crea in blocco…"), "files", lambda: c.batch_create(), requires_document=False)
    for a in (act_from_file, act_from_images, act_from_url, act_clipboard, act_scanner, act_photos, act_screen,
              act_blank):
        create_menu.addAction(a)
    create_menu.addSeparator()
    create_menu.addAction(act_portfolio)
    create_menu.addAction(act_edit_portfolio)
    create_menu.addAction(act_batch_create)
    create_btn = QToolButton()
    create_btn.setText(_("Crea PDF"))
    create_btn.setToolTip(_("Crea un nuovo PDF da file, immagini, pagine web o appunti"))
    bind_icon(create_btn, "file-plus-2")
    create_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    create_btn.setMenu(create_menu)
    create_btn.setToolButtonStyle(mode.toolbar.toolButtonStyle())
    mode.add_widget(create_btn)

    watcher = _ClipboardWatcher(ctx.window, act_clipboard, c)
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        # il ricevitore è figlio della finestra: Qt scollega il segnale quando la finestra viene distrutta
        clipboard.dataChanged.connect(watcher.update)
    create_menu.aboutToShow.connect(watcher.update)
    watcher.update()

    mode.add_separator()
    act_compress = ctx.action(_("Comprimi"), "shrink", lambda: c.compress(), requires_document=False,
                              tooltip=_("Riduci la dimensione del PDF"))
    act_compress_many = ctx.action(_("Comprimi più file…"), "shrink", lambda: c.compress_files(),
                                   requires_document=False)
    act_advanced = ctx.action(_("Compressione personalizzata…"), "sliders-horizontal", lambda: c.optimize_advanced(),
                              requires_document=False)
    act_flatten = ctx.action(_("Appiattisci…"), "layers", lambda: c.flatten(),
                             tooltip=_("Rendi fissi annotazioni, campi, livelli e ritaglio"))
    act_unpdfa = ctx.action(_("Rimuovi dichiarazione PDF/A"), "archive-x", lambda: c.remove_conformance())
    act_repair = ctx.action(_("Ripara PDF…"), "wrench", lambda: c.repair(), requires_document=False)
    act_batch = ctx.action(_("Converti più file…"), "folder-output", lambda: c.batch_convert(),
                           requires_document=False)
    mode.add_action(act_compress)
    more_menu = QMenu(ctx.window)
    for a in (act_advanced, act_flatten, act_unpdfa, act_compress_many, act_repair, act_batch,
              act_batch_create):
        more_menu.addAction(a)
    more_btn = QToolButton()
    more_btn.setText(_("Altri strumenti"))
    more_btn.setToolTip(_("Compressione personalizzata, riparazione e operazioni su più file"))
    bind_icon(more_btn, "wrench")
    more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    more_btn.setMenu(more_menu)
    more_btn.setToolButtonStyle(mode.toolbar.toolButtonStyle())
    mode.add_widget(more_btn)

    ctx.add_menu_action(act_export, _("Converti"))
    ctx.add_menu_action(act_blank, _("Converti"))
    ctx.add_menu_action(act_from_file, _("Converti"))
    ctx.add_menu_action(act_compress_many, _("Converti"))
    ctx.add_menu_action(act_batch_create, _("Converti"))

    ctx.add_home_action(_("Crea PDF da file"), "file-plus", lambda: c.create_from_files(),
                        _("Documenti d'ufficio (DOCX, XLSX, PPTX), testo, Markdown, CSV, HTML"), 30)
    ctx.add_home_action(_("Immagini in PDF"), "images", lambda: c.create_from_images(),
                        _("Una o più immagini, una per pagina"), 31)
    ctx.add_home_action(_("Converti PDF"), "file-output", lambda: c.export(),
                        _("In Word, Excel, PowerPoint, immagini, testo, PDF/A…"), 32)
    ctx.add_home_action(_("Comprimi PDF"), "shrink", lambda: c.compress(), _("Riduci la dimensione del file"), 33)

    c.actions = {"export": act_export, "from_file": act_from_file, "from_images": act_from_images,
                 "from_url": act_from_url, "clipboard": act_clipboard, "blank": act_blank,
                 "scanner": act_scanner, "photos": act_photos, "screen": act_screen,
                 "compress": act_compress, "compress_many": act_compress_many, "advanced": act_advanced,
                 "repair": act_repair, "flatten": act_flatten, "batch": act_batch, "batch_create": act_batch_create}
    c.create_button = create_btn
