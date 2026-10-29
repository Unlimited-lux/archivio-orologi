"""Modulo Converti: conversione da/verso PDF, creazione di PDF, compressione e riparazione.

La logica senza interfaccia è in `engine.py` (`convert_pdf`), `create.py`,
`optimize.py`, `pdfa.py` e `batch.py`; le finestre in `*_dialog(s).py`;
i comandi in `actions.py`.
"""

from __future__ import annotations

import shiboken6
from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QGuiApplication

from foliant.api import _, add_translations

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
    ctx.documentOpened.connect(c.on_document_opened)

    # scheda Converti divisa in gruppi come la barra multifunzione di Excel: Esporta | Crea | Ottimizza
    mode = ctx.add_mode("converti", _("Converti"), "file-output", 70)
    mode.add_group(_("Esporta"), "esporta")
    act_export = ctx.action(_("Esporta come…"), "file-output", lambda: c.export(), requires_document=False,
                            tooltip=_("Converti il PDF in un altro formato"))
    mode.add_action(act_export, group="esporta")
    # i formati più usati: nella barra basta il nome del formato (il gruppo dice già «Esporta»);
    # menu, scheda File e ricerca dei comandi mostrano il nome intero («In Word»)
    for label, short, icon_name, fmt in ((_("In Word"), "Word", "file-text", "docx"),
                                         (_("In Excel"), "Excel", "file-spreadsheet", "xlsx"),
                                         (_("In PowerPoint"), "PowerPoint", "presentation", "pptx"),
                                         (_("In immagini"), _("Immagini"), "file-image", "png"),
                                         (_("In testo"), _("Testo"), "file-type", "txt"),
                                         (_("In HTML"), "HTML", "code-xml", "html"),
                                         (_("In PDF/A"), "PDF/A", "archive", "pdfa-2b")):
        act_format = ctx.action(label, icon_name, lambda _c=False, f=fmt: c.export(f), requires_document=False,
                                tooltip=_("Converti il documento: {f}").format(f=label))
        act_format.setIconText(short)
        mode.add_action(act_format, group="esporta")

    # gruppo «Crea»: il pulsante ▾ «Crea PDF» con tutte le origini
    act_from_file = ctx.action(_("PDF da file…"), "file-plus", lambda: c.create_from_files(),
                               requires_document=False)
    act_from_file.setIconText(_("Da file…"))
    act_from_images = ctx.action(_("Da immagini…"), "images", lambda: c.create_from_images(), requires_document=False)
    act_from_url = ctx.action(_("Da pagina web…"), "globe", lambda: c.create_from_url(), requires_document=False)
    act_clipboard = ctx.action(_("Dagli appunti"), "clipboard-paste", lambda: c.create_from_clipboard(),
                               requires_document=False)
    act_scanner = ctx.action(_("Da scanner…"), "scan", lambda: c.create_from_scanner(), requires_document=False)
    act_photos = ctx.action(_("Da foto di documenti…"), "image", lambda: c.create_from_photos(),
                            requires_document=False)
    act_camera = ctx.action(_("Da fotocamera…"), "camera", lambda: c.create_from_camera(), requires_document=False,
                            tooltip=_("Scatta le pagine con la webcam"))
    act_screen = ctx.action(_("Da cattura schermo…"), "monitor", lambda: c.create_from_screen(),
                            requires_document=False)
    act_blank = ctx.action(_("PDF vuoto…"), "file", lambda: c.create_blank(), requires_document=False)
    act_portfolio = ctx.action(_("Crea portfolio…"), "folder-archive", lambda: c.create_portfolio(),
                               requires_document=False)
    act_edit_portfolio = ctx.action(_("Modifica portfolio…"), "folder-pen", lambda: c.edit_portfolio(),
                                    tooltip=_("Aggiungi o rimuovi i file del portfolio aperto"))
    act_batch_create = ctx.action(_("Crea in blocco…"), "files", lambda: c.batch_create(), requires_document=False)
    mode.add_group(_("Crea"), "crea")
    create_btn = mode.add_menu_button(
        _("Crea PDF"), "file-plus-2",
        [act_from_file, act_from_images, act_from_url, act_clipboard, act_scanner, act_camera, act_photos,
         act_screen, act_blank, None, act_portfolio, act_edit_portfolio, act_batch_create],
        group="crea", tooltip=_("Crea un nuovo PDF da file, immagini, pagine web o appunti"),
        requires_document=False)
    create_menu = create_btn.menu()

    watcher = _ClipboardWatcher(ctx.window, act_clipboard, c)
    clipboard = QGuiApplication.clipboard()
    if clipboard is not None:
        # il ricevitore è figlio della finestra: Qt scollega il segnale quando la finestra viene distrutta
        clipboard.dataChanged.connect(watcher.update)
    create_menu.aboutToShow.connect(watcher.update)
    watcher.update()

    # gruppo «Ottimizza»: Comprimi e il pulsante ▾ con gli altri strumenti
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
    mode.add_group(_("Ottimizza"), "ottimizza")
    mode.add_action(act_compress, group="ottimizza")
    more_btn = mode.add_menu_button(
        _("Altri strumenti"), "wrench",
        [act_advanced, act_flatten, act_unpdfa, act_compress_many, act_repair, act_batch, act_batch_create],
        group="ottimizza", tooltip=_("Compressione personalizzata, riparazione e operazioni su più file"),
        requires_document=False)

    # scheda File: le stesse azioni della barra (niente doppioni), in Esporta e in Nuovo
    ctx.add_file_action(act_export, "esporta", _("Esporta"),
                        _("Word, Excel, PowerPoint, immagini, testo, HTML, PDF/A"), order=10)
    ctx.add_file_action(act_blank, "nuovo", _("Crea"), _("Scegli formato, orientamento e numero di pagine"),
                        order=20)
    ctx.add_file_action(act_from_file, "nuovo", _("Crea"), _("Da Word, Excel, immagini e altri formati"),
                        order=30)
    ctx.add_file_action(act_batch_create, "nuovo", _("Crea"), _("Converte in PDF una cartella intera"), order=40)

    ctx.add_home_action(_("Crea PDF da file"), "file-plus", lambda: c.create_from_files(),
                        _("Documenti d'ufficio (DOCX, XLSX, PPTX), testo, Markdown, CSV, HTML"), 30)
    ctx.add_home_action(_("Immagini in PDF"), "images", lambda: c.create_from_images(),
                        _("Una o più immagini, una per pagina"), 31)
    ctx.add_home_action(_("Converti PDF"), "file-output", lambda: c.export(),
                        _("In Word, Excel, PowerPoint, immagini, testo, PDF/A…"), 32)
    ctx.add_home_action(_("Comprimi PDF"), "shrink", lambda: c.compress(), _("Riduci la dimensione del file"), 33)

    c.actions = {"export": act_export, "from_file": act_from_file, "from_images": act_from_images,
                 "from_url": act_from_url, "clipboard": act_clipboard, "blank": act_blank,
                 "scanner": act_scanner, "photos": act_photos, "camera": act_camera, "screen": act_screen,
                 "compress": act_compress, "compress_many": act_compress_many, "advanced": act_advanced,
                 "repair": act_repair, "flatten": act_flatten, "batch": act_batch, "batch_create": act_batch_create}
    c.create_button = create_btn
    c.more_button = more_btn
