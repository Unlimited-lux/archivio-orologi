"""Stampa (VIS-43, permessi VIS-06, orientamento VIS-11, livelli VIS-20).

Flusso: dialogo opzioni di Foliant → dialogo di stampa del sistema →
rendering pagina per pagina in un'attività annullabile.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPageLayout
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, OperationCancelled, _, add_translations, theme

from .printing_engine import (
    ACTUAL_SIZE, FIT_PAGE, LAYERS_PRINT, LAYERS_SCREEN, PrintOptions, RangeError, document_print_capability,
    parse_page_ranges, print_document,
)

try:  # PySide6-Essentials include QtPrintSupport; ripiego su PDF se mancasse
    from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
    HAVE_PRINT_SUPPORT = True
except ImportError:  # pragma: no cover - dipende dall'installazione
    QAbstractPrintDialog = QPrintDialog = QPrinter = None
    HAVE_PRINT_SUPPORT = False

EN = {
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
    "Adatta alla pagina": "Fit to page",
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

        size_box = QGroupBox(_("Dimensione"), self)
        sl = QVBoxLayout(size_box)
        self.rb_fit = QRadioButton(_("Adatta alla pagina"), size_box)
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
        root.addWidget(opt_box)

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
        return PrintOptionsDialog(self.ctx.window, view.page_count, view.current_page, has_layers,
                                  cap.reason if cap.allowed else "")

    def ask_printer(self, view, options: PrintOptions):
        """Dialogo di stampa del sistema. Restituisce il dispositivo o None."""
        if not HAVE_PRINT_SUPPORT:
            from PySide6.QtGui import QPdfWriter
            self.ctx.info(_("Stampa"), _("Il supporto di stampa di Qt non è disponibile: "
                                         "il documento verrà stampato su file PDF."))
            path = self.ctx.ask_save_path(_("Stampa su file PDF"), view.document.display_name)
            return QPdfWriter(path) if path else None
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(view.document.display_name)
        printer.setCopyCount(options.copies)
        printer.setCollateCopies(options.collate)
        first = options.pages[0] if options.pages else 0
        with ENGINE_LOCK:  # orientamento del documento, non della vista (VIS-11)
            rect = view.document.pdf[first].rect
        w, h = rect.width, rect.height
        if options.auto_rotate:
            printer.setPageOrientation(QPageLayout.Orientation.Landscape if w > h
                                       else QPageLayout.Orientation.Portrait)
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


def install(ctx) -> PrintController:
    add_translations("en", EN)
    return PrintController(ctx)
