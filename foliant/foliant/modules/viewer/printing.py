"""Stampa (VIS-43, permessi VIS-06, orientamento VIS-11, livelli VIS-20).

Flusso: dialogo opzioni di Foliant → dialogo di stampa del sistema →
rendering pagina per pagina in un'attività annullabile.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPageLayout
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, OperationCancelled, _, add_translations, theme
from foliant.i18n import pgettext

from .printing_engine import (
    ACTUAL_SIZE, FIT_PAGE, LAYERS_PRINT, LAYERS_SCREEN, LAYOUT_BOOKLET, LAYOUT_NORMAL, LAYOUT_POSTER,
    PAGES_PER_SHEET, PrintOptions, RangeError, document_print_capability, nup_sheet_orientation, parse_page_ranges,
    print_document,
)

try:  # PySide6-Essentials include QtPrintSupport; ripiego su PDF se mancasse
    from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
    HAVE_PRINT_SUPPORT = True
except ImportError:  # pragma: no cover - dipende dall'installazione
    QAbstractPrintDialog = QPrintDialog = QPrinter = None
    HAVE_PRINT_SUPPORT = False

EN = {
    "Modalità": "Mode",
    "Normale": "Normal",
    "Opuscolo (fogli piegati)": "Booklet (folded sheets)",
    "Poster (pagina divisa su più fogli)": "Poster (page split over several sheets)",
    "Rilegatura a destra": "Right-edge binding",
    "Sovrapposizione": "Overlap",
    "Due pagine per facciata in ordine di piegatura; se le pagine non sono multipli di 4 si aggiungono pagine "
    "bianche in fondo. Stampa fronte/retro sul lato corto (o rigira i fogli a mano).":
        "Two pages per side in folding order; blank pages are added at the end if the page count is not a "
        "multiple of 4. Print two-sided on the short edge (or turn the sheets over by hand).",
    "Ogni pagina a dimensione reale divisa su più fogli, con una fascia di sovrapposizione e segni di taglio.":
        "Each page at actual size split over several sheets, with an overlap band and cut marks.",
    "solo fronte": "one-sided",
    "fronte/retro sul lato corto": "two-sided on the short edge",
    "fronte/retro sul lato lungo": "two-sided on the long edge",
    "Il documento chiede la stampa {mode}.": "The document asks for {mode} printing.",
    "Pagine per foglio": "Pages per sheet",
    "Ordine per colonne": "Column order",
    "Bordo attorno alle pagine": "Border around pages",
    "Fronte/retro": "Two-sided",
    "Come la stampante": "Printer default",
    "Solo fronte": "One-sided",
    "Sul lato lungo": "Long edge",
    "Sul lato corto": "Short edge",
    "Scala di grigi": "Grayscale",
    "Stampa…": "Print…",
    "File": "File",
    "Stampa": "Print",
    "Pagine": "Pages",
    "Tutte ({n})": "All ({n})",
    "Pagina corrente ({n})": "Current page ({n})",
    "Intervallo": "Range",
    "es. 1-3, 5, 8-": "e.g. 1-3, 5, 8-",
    "Copie": "Copies",
    "Fascicola": "Collate",
    "Dimensione": "Size",
    "stampa\x04Adatta alla pagina": "Fit to page",
    "Dimensione reale": "Actual size",
    "Opzioni": "Options",
    "Stampa annotazioni e commenti": "Print annotations and comments",
    "Orientamento automatico": "Automatic orientation",
    "Ruota le pagine orizzontali per adattarle al foglio": "Rotate landscape pages to fit the sheet",
    "Livelli": "Layers",
    "Impostazioni di stampa del documento": "Document print settings",
    "Come a schermo": "As shown on screen",
    "Avanti…": "Next…",
    "Annulla": "Cancel",
    "Stampa non disponibile": "Printing not available",
    "Stampa di «{name}»": "Printing “{name}”",
    "Stampa non riuscita": "Printing failed",
    "Stampa annullata.": "Printing cancelled.",
    "Inviate alla stampante {n} pagine.": "Sent {n} pages to the printer.",
    "Il supporto di stampa di Qt non è disponibile: il documento verrà stampato su file PDF.":
        "Qt print support is not available: the document will be printed to a PDF file.",
    "Stampa su file PDF": "Print to PDF file",
    # motore
    "Indica le pagine da stampare.": "Enter the pages to print.",
    "Intervallo non valido: «{x}».": "Invalid range: “{x}”.",
    "La pagina {n} è fuori intervallo: il documento ha {t} pagine.":
        "Page {n} is out of range: the document has {t} pages.",
    "Le impostazioni di protezione del documento non consentono la stampa.":
        "The document's security settings do not allow printing.",
    "Il documento consente solo la stampa a bassa qualità ({dpi} dpi).":
        "The document only allows low-quality printing ({dpi} dpi).",
    "Nessuna pagina da stampare.": "No pages to print.",
    "Impossibile avviare la stampa sul dispositivo scelto.": "Could not start printing on the chosen device.",
    "Pagina {n} di {t}": "Page {n} of {t}",
    "Invio alla stampante…": "Sending to the printer…",
}


class PrintOptionsDialog(QDialog):
    """Opzioni di stampa di Foliant (prima del dialogo del sistema)."""

    def __init__(self, parent: QWidget | None, page_count: int, current_page: int,
                 has_layers: bool = False, quality_note: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("Stampa"))
        self.setObjectName("printOptionsDialog")
        self.page_count = page_count
        self.current_page = current_page
        self.result_options: PrintOptions | None = None

        root = QVBoxLayout(self)
        root.setSpacing(12)

        pages_box = QGroupBox(_("Pagine"), self)
        pl = QVBoxLayout(pages_box)
        self.rb_all = QRadioButton(_("Tutte ({n})").format(n=page_count), pages_box)
        self.rb_current = QRadioButton(_("Pagina corrente ({n})").format(n=current_page + 1), pages_box)
        self.rb_range = QRadioButton(_("Intervallo"), pages_box)
        self.range_edit = QLineEdit(pages_box)
        self.range_edit.setPlaceholderText(_("es. 1-3, 5, 8-"))
        self.range_edit.textEdited.connect(lambda _t: self.rb_range.setChecked(True))
        self.range_edit.textEdited.connect(lambda _t: self.error_label.hide())
        self.rb_all.setChecked(True)
        self._page_group = QButtonGroup(self)
        for rb in (self.rb_all, self.rb_current, self.rb_range):
            self._page_group.addButton(rb)
        pl.addWidget(self.rb_all)
        pl.addWidget(self.rb_current)
        row = QHBoxLayout()
        row.addWidget(self.rb_range)
        row.addWidget(self.range_edit, 1)
        pl.addLayout(row)
        self.error_label = QLabel(pages_box)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error_label.hide()
        pl.addWidget(self.error_label)
        root.addWidget(pages_box)

        mode_box = QGroupBox(_("Modalità"), self)
        ml = QVBoxLayout(mode_box)       # non QFormLayout: il testo d'aiuto va a capo correttamente
        self.layout_mode = QComboBox(mode_box)
        self.layout_mode.addItem(_("Normale"), LAYOUT_NORMAL)
        self.layout_mode.addItem(_("Opuscolo (fogli piegati)"), LAYOUT_BOOKLET)
        self.layout_mode.addItem(_("Poster (pagina divisa su più fogli)"), LAYOUT_POSTER)
        ml.addWidget(self.layout_mode)
        self.nup = QComboBox(mode_box)
        for n in PAGES_PER_SHEET:
            self.nup.addItem(str(n), n)
        self.nup_label = QLabel(_("Pagine per foglio"), mode_box)
        self.nup_columns = QCheckBox(_("Ordine per colonne"), mode_box)
        self.nup_border = QCheckBox(_("Bordo attorno alle pagine"), mode_box)
        nup_row = QHBoxLayout()
        nup_row.addWidget(self.nup_label)
        nup_row.addWidget(self.nup)
        nup_row.addStretch(1)
        ml.addLayout(nup_row)
        ml.addWidget(self.nup_columns)
        ml.addWidget(self.nup_border)
        self.nup.currentIndexChanged.connect(self._on_layout_mode)
        self.booklet_rtl = QCheckBox(_("Rilegatura a destra"), mode_box)
        ml.addWidget(self.booklet_rtl)
        self.overlap = QDoubleSpinBox(mode_box)
        self.overlap.setRange(0, 50)
        self.overlap.setDecimals(1)
        self.overlap.setValue(10)
        self.overlap.setSuffix(" mm")
        self.overlap_label = QLabel(_("Sovrapposizione"), mode_box)
        overlap_row = QHBoxLayout()
        overlap_row.addWidget(self.overlap_label)
        overlap_row.addWidget(self.overlap)
        overlap_row.addStretch(1)
        ml.addLayout(overlap_row)
        self.mode_hint = QLabel(mode_box)
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setProperty("muted", True)
        ml.addWidget(self.mode_hint)
        self.layout_mode.currentIndexChanged.connect(self._on_layout_mode)
        root.addWidget(mode_box)

        size_box = QGroupBox(_("Dimensione"), self)
        self.size_box = size_box
        sl = QVBoxLayout(size_box)
        self.rb_fit = QRadioButton(pgettext("stampa", "Adatta alla pagina"), size_box)
        self.rb_actual = QRadioButton(_("Dimensione reale"), size_box)
        self.rb_fit.setChecked(True)
        sl.addWidget(self.rb_fit)
        sl.addWidget(self.rb_actual)
        root.addWidget(size_box)

        opt_box = QGroupBox(_("Opzioni"), self)
        form = QFormLayout(opt_box)
        copies_row = QHBoxLayout()
        self.copies = QSpinBox(opt_box)
        self.copies.setRange(1, 999)
        self.collate = QCheckBox(_("Fascicola"), opt_box)
        self.collate.setChecked(True)
        copies_row.addWidget(self.copies)
        copies_row.addWidget(self.collate)
        copies_row.addStretch(1)
        form.addRow(_("Copie"), copies_row)
        self.annotations = QCheckBox(_("Stampa annotazioni e commenti"), opt_box)
        self.annotations.setChecked(True)
        form.addRow(self.annotations)
        self.auto_rotate = QCheckBox(_("Orientamento automatico"), opt_box)
        self.auto_rotate.setToolTip(_("Ruota le pagine orizzontali per adattarle al foglio"))
        self.auto_rotate.setChecked(True)
        form.addRow(self.auto_rotate)
        self.layers = QComboBox(opt_box)
        self.layers.addItem(_("Impostazioni di stampa del documento"), LAYERS_PRINT)
        self.layers.addItem(_("Come a schermo"), LAYERS_SCREEN)
        self.layers_label = QLabel(_("Livelli"), opt_box)
        form.addRow(self.layers_label, self.layers)
        self.layers.setVisible(has_layers)
        self.layers_label.setVisible(has_layers)
        self.duplex_combo = QComboBox(opt_box)
        for text, value in ((_("Come la stampante"), ""), (_("Solo fronte"), "Simplex"),
                            (_("Sul lato lungo"), "DuplexFlipLongEdge"),
                            (_("Sul lato corto"), "DuplexFlipShortEdge")):
            self.duplex_combo.addItem(text, value)
        form.addRow(_("Fronte/retro"), self.duplex_combo)
        self.grayscale = QCheckBox(_("Scala di grigi"), opt_box)
        form.addRow(self.grayscale)
        root.addWidget(opt_box)

        self.duplex_note = QLabel(self)
        self.duplex_note.setWordWrap(True)
        self.duplex_note.setProperty("muted", True)
        self.duplex_note.hide()
        root.addWidget(self.duplex_note)

        self.note = QLabel(quality_note, self)
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.hex_color('warning')};")
        self.note.setVisible(bool(quality_note))
        root.addWidget(self.note)

        buttons = QDialogButtonBox(self)
        self.ok_button = buttons.addButton(_("Avanti…"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        self.ok_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.setMinimumWidth(400)
        self._on_layout_mode()

    def _on_layout_mode(self, *_a) -> None:
        mode = self.layout_mode.currentData()
        normal = mode == LAYOUT_NORMAL
        for w in (self.nup, self.nup_label):
            w.setVisible(normal)
        multi = normal and (self.nup.currentData() or 1) > 1
        self.nup_columns.setVisible(multi)
        self.nup_border.setVisible(multi)
        self.booklet_rtl.setVisible(mode == LAYOUT_BOOKLET)
        self.overlap.setVisible(mode == LAYOUT_POSTER)
        self.overlap_label.setVisible(mode == LAYOUT_POSTER)
        self.size_box.setEnabled(normal and not multi)
        self.auto_rotate.setEnabled(normal and not multi)
        hints = {LAYOUT_BOOKLET: _("Due pagine per facciata in ordine di piegatura; se le pagine non sono multipli "
                                   "di 4 si aggiungono pagine bianche in fondo. Stampa fronte/retro sul lato corto "
                                   "(o rigira i fogli a mano)."),
                 LAYOUT_POSTER: _("Ogni pagina a dimensione reale divisa su più fogli, con una fascia di "
                                  "sovrapposizione e segni di taglio.")}
        self.mode_hint.setText(hints.get(mode, ""))
        self.mode_hint.setVisible(mode in hints)
        # altezza del testo a capo calcolata sulla larghezza disponibile (i layout di Qt la
        # sottostimano per le etichette con a capo), poi il dialogo si adatta
        width = max(self.mode_hint.width(), self.minimumWidth() - 60)
        self.mode_hint.setMinimumHeight(self.mode_hint.heightForWidth(width) if mode in hints else 0)
        self.adjustSize()

    def apply_document_preferences(self, prefs) -> None:
        """Preselezione dalle preferenze di stampa del documento (VIS-42): scala, copie,
        fronte/retro (passato al dialogo di stampa del sistema)."""
        if prefs is None:
            return
        if prefs.print_scaling_none:
            self.rb_actual.setChecked(True)
        if prefs.num_copies > 1:
            self.copies.setValue(prefs.num_copies)
        if prefs.duplex:
            self.duplex_combo.setCurrentIndex(max(0, self.duplex_combo.findData(prefs.duplex)))
            labels = {"Simplex": _("solo fronte"), "DuplexFlipShortEdge": _("fronte/retro sul lato corto"),
                      "DuplexFlipLongEdge": _("fronte/retro sul lato lungo")}
            self.duplex_note.setText(_("Il documento chiede la stampa {mode}.").format(mode=labels[prefs.duplex]))
            self.duplex_note.show()

    def options(self) -> PrintOptions:
        """Opzioni scelte; solleva RangeError se l'intervallo non è valido."""
        if self.rb_current.isChecked():
            pages = [self.current_page]
        elif self.rb_range.isChecked():
            pages = parse_page_ranges(self.range_edit.text(), self.page_count)
        else:
            pages = list(range(self.page_count))
        return PrintOptions(
            pages=pages,
            copies=self.copies.value(),
            collate=self.collate.isChecked(),
            scaling=FIT_PAGE if self.rb_fit.isChecked() else ACTUAL_SIZE,
            annotations=self.annotations.isChecked(),
            auto_rotate=self.auto_rotate.isChecked(),
            layers=self.layers.currentData() or LAYERS_PRINT,
            duplex=self.duplex_combo.currentData() or "",
            layout=self.layout_mode.currentData() or LAYOUT_NORMAL,
            booklet_rtl=self.booklet_rtl.isChecked(),
            poster_overlap_mm=self.overlap.value(),
            pages_per_sheet=self.nup.currentData() or 1,
            nup_by_columns=self.nup_columns.isChecked(),
            nup_border=self.nup_border.isChecked(),
            grayscale=self.grayscale.isChecked(),
        )

    def accept(self) -> None:
        try:
            self.result_options = self.options()
        except RangeError as e:
            self.error_label.setText(str(e))
            self.error_label.show()
            self.range_edit.setFocus()
            return
        super().accept()


class PrintController:
    def __init__(self, ctx):
        self.ctx = ctx
        self.action = ctx.action(_("Stampa…"), "printer", self.print_current, shortcut="Ctrl+P",
                                 requires_document=False)
        self._base_tooltip = self.action.toolTip()
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_action(self.action)
        ctx.add_menu_action(self.action, _("File"))
        ctx.currentViewChanged.connect(lambda _v: self.update_state())
        ctx.documentChanged.connect(lambda _v: self.update_state())
        ctx.documentAccessChanged.connect(lambda _v: self.update_state())   # sblocco/blocco (SEC-02)
        self.update_state()

    # ------------------------------------------------------------------ stato
    def update_state(self) -> None:
        view = self.ctx.current_view()
        if view is None or view.document.closed:
            self.action.setEnabled(False)
            self.action.setToolTip(self._base_tooltip)
            return
        cap = document_print_capability(view.document)
        self.action.setEnabled(cap.allowed)
        self.action.setToolTip(f"{self._base_tooltip}\n{cap.reason}" if cap.reason else self._base_tooltip)

    # ----------------------------------------------------------------- dialoghi
    def ask_options(self, view) -> PrintOptions | None:
        """Mostra il dialogo opzioni; None se annullato (sostituibile nei test)."""
        dlg = self.options_dialog(view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.result_options

    def options_dialog(self, view) -> PrintOptionsDialog:
        cap = document_print_capability(view.document)
        with ENGINE_LOCK:
            try:
                has_layers = bool(view.document.pdf.get_ocgs())
            except Exception:
                has_layers = False
        dlg = PrintOptionsDialog(self.ctx.window, view.page_count, view.current_page, has_layers,
                                 cap.reason if cap.allowed else "")
        from .session_engine import read_open_options
        try:
            with ENGINE_LOCK:
                prefs = read_open_options(view.document.pdf)
        except Exception:  # noqa: BLE001
            prefs = None
        dlg.apply_document_preferences(prefs)
        return dlg

    def ask_printer(self, view, options: PrintOptions):
        """Dialogo di stampa del sistema. Restituisce il dispositivo o None."""
        if not HAVE_PRINT_SUPPORT:
            from PySide6.QtGui import QPdfWriter
            self.ctx.info(_("Stampa"), _("Il supporto di stampa di Qt non è disponibile: "
                                         "il documento verrà stampato su file PDF."))
            path = self.ctx.ask_save_path(_("Stampa su file PDF"), view.document.display_name)
            return QPdfWriter(path) if path else None
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_printer(printer, view.document, options)
        dlg = QPrintDialog(printer, self.ctx.window)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, False)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, False)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintSelection, False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return printer

    # ------------------------------------------------------------------ stampa
    def print_current(self) -> int:
        view = self.ctx.current_view()
        if view is None:
            return 0
        cap = document_print_capability(view.document)
        if not cap.allowed:
            self.ctx.error(_("Stampa non disponibile"), cap.reason)
            return 0
        options = self.ask_options(view)
        if options is None:
            return 0
        options.max_dpi = cap.max_dpi
        device = self.ask_printer(view, options)
        if device is None:
            return 0
        if HAVE_PRINT_SUPPORT and isinstance(device, QPrinter):
            # le copie le genera Foliant (stesso risultato su ogni stampante)
            options.copies = max(options.copies, device.copyCount())
            options.collate = device.collateCopies()
            device.setCopyCount(1)
        return self.print_to(view.document, device, options)

    def print_to(self, document, device, options: PrintOptions) -> int:
        """Stampa su un dispositivo già configurato, con avanzamento annullabile."""
        try:
            n = self.ctx.run_task(_("Stampa di «{name}»").format(name=document.display_name),
                                  lambda progress: print_document(document, device, options, progress))
        except OperationCancelled:
            self.ctx.status(_("Stampa annullata."))
            return 0
        except Exception as e:  # noqa: BLE001 - mai chiudere l'applicazione
            self.ctx.error(_("Stampa non riuscita"), str(e))
            return 0
        n = int(n or 0)
        self.ctx.status(_("Inviate alla stampante {n} pagine.").format(n=n))
        return n


def configure_printer(printer, document, options: PrintOptions) -> None:
    """Parametri del lavoro di stampa dalle opzioni: copie, fronte/retro, colore, orientamento."""
    printer.setDocName(document.display_name)
    printer.setCopyCount(options.copies)
    printer.setCollateCopies(options.collate)
    duplex = {"Simplex": QPrinter.DuplexMode.DuplexNone,
              "DuplexFlipShortEdge": QPrinter.DuplexMode.DuplexShortSide,
              "DuplexFlipLongEdge": QPrinter.DuplexMode.DuplexLongSide}.get(options.duplex)
    if duplex is not None:
        printer.setDuplex(duplex)
    printer.setColorMode(QPrinter.ColorMode.GrayScale if options.grayscale else QPrinter.ColorMode.Color)
    first = options.pages[0] if options.pages else 0
    with ENGINE_LOCK:  # orientamento del documento, non della vista (VIS-11)
        rect = document.pdf[first].rect
    w, h = rect.width, rect.height
    landscape = None
    if options.layout == LAYOUT_BOOKLET:      # due pagine affiancate per foglio
        landscape = h >= w
    elif options.layout == LAYOUT_POSTER:
        pass
    elif options.pages_per_sheet > 1:
        landscape = nup_sheet_orientation(options.pages_per_sheet, w, h)
    elif options.auto_rotate:
        landscape = w > h
    if landscape is not None:
        printer.setPageOrientation(QPageLayout.Orientation.Landscape if landscape
                                   else QPageLayout.Orientation.Portrait)


def install(ctx) -> PrintController:
    add_translations("en", EN)
    return PrintController(ctx)
