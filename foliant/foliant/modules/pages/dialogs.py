"""Dialoghi delle operazioni strutturali (inserisci, estrai, dividi, unisci…)."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, Document, FoliantError, _, icon, theme

from . import engine as E

TRANSLATIONS = {
    "Pagine": "Pages",
    "Pagina corrente": "Current page",
    "Pagine selezionate ({n})": "Selected pages ({n})",
    "Tutte le pagine": "All pages",
    "Intervallo:": "Range:",
    "es. 1-3, 8-": "e.g. 1-3, 8-",
    "Filtro:": "Filter:",
    "Tutte": "All",
    "Dispari": "Odd",
    "Pari": "Even",
    "Verticali": "Portrait",
    "Orizzontali": "Landscape",
    "{n} pagine": "{n} pages",
    "1 pagina": "1 page",
    "Posizione": "Position",
    "Dopo la selezione": "After the selection",
    "Prima della selezione": "Before the selection",
    "All'inizio": "At the start",
    "Alla fine": "At the end",
    "Inserisci pagine vuote": "Insert blank pages",
    "Quantità:": "Count:",
    "Formato:": "Size:",
    "Come la pagina adiacente": "Same as adjacent page",
    "Personalizzato": "Custom",
    "Larghezza:": "Width:",
    "Altezza:": "Height:",
    "Unità:": "Unit:",
    "Orientamento:": "Orientation:",
    "Verticale": "Portrait",
    "Orizzontale": "Landscape",
    "Inserisci pagina vuota": "Insert blank page",
    "Inserisci da file": "Insert from file",
    "File:": "File:",
    "Sfoglia…": "Browse…",
    "Pagine del file:": "File pages:",
    "tutte": "all",
    "Inserimento alternato ogni": "Interleave every",
    "pagine del documento": "document pages",
    "Pagine del file in ordine inverso": "File pages in reverse order",
    "Includi annotazioni": "Include annotations",
    "Includi campi modulo": "Include form fields",
    "Scegli un file.": "Choose a file.",
    "Scegli il file da inserire": "Choose the file to insert",
    "Documenti (*.pdf *.xps *.epub *.png *.jpg *.jpeg *.tif *.tiff)": "Documents (*.pdf *.xps *.epub *.png *.jpg *.jpeg *.tif *.tiff)",
    "{n} pagine nel file": "{n} pages in the file",
    "Inserisci pagine da file": "Insert pages from file",
    "Estrai pagine": "Extract pages",
    "Modalità": "Mode",
    "Un unico file": "A single file",
    "Un file per pagina": "One file per page",
    "Un file per intervallo (separati da ;)": "One file per range (separated by ;)",
    "Nome dei file:": "File names:",
    "Segnaposto: {nome}, {pagina}, {n}, {inizio}, {fine}": "Placeholders: {nome}, {pagina}, {n}, {inizio}, {fine}",
    "Elimina le pagine dal documento dopo l'estrazione": "Delete the pages from the document after extracting",
    "Apri il risultato": "Open the result",
    "Estrazione in corso…": "Extracting…",
    "Scegli la cartella di destinazione": "Choose the destination folder",
    "Creati {n} file in {f}": "Created {n} files in {f}",
    "Le pagine estratte non sono state eliminate: il documento deve conservarne almeno una.":
        "The extracted pages were not deleted: the document must keep at least one.",
    "Elimina pagine estratte": "Delete extracted pages",
    "Dividi documento": "Split document",
    "Metodo": "Method",
    "Ogni N pagine": "Every N pages",
    "Per intervalli (es. 1-3; 4-10; 11-)": "By ranges (e.g. 1-3; 4-10; 11-)",
    "In K parti uguali": "Into K equal parts",
    "Per segnalibri, fino al livello": "By bookmarks, down to level",
    "Per dimensione massima (MB)": "By maximum size (MB)",
    "Alle pagine vuote (scartate)": "At blank pages (discarded)",
    "Alle pagine che contengono il testo (espressione regolare)": "At pages containing the text (regular expression)",
    "Raccogli le pagine escluse in un file aggiuntivo": "Collect the excluded pages in an extra file",
    "Sovrascrivi i file esistenti": "Overwrite existing files",
    "Segnaposto: {nome}, {n}, {inizio}, {fine}, {segnalibro}, {match1}": "Placeholders: {nome}, {n}, {inizio}, {fine}, {segnalibro}, {match1}",
    "Si otterranno {k} file: {sizes}": "{k} files will be produced: {sizes}",
    "Divisione in corso…": "Splitting…",
    "Calcolo della divisione…": "Computing the split…",
    "Unisci file": "Merge files",
    "Aggiungi file…": "Add files…",
    "Rimuovi": "Remove",
    "Su": "Up",
    "Giù": "Down",
    "Ordina per nome": "Sort by name",
    "Ordina per data": "Sort by date",
    "File": "File",
    "Pagine da usare": "Pages to use",
    "Dimensione": "Size",
    "Nomi dei file come segnalibri": "File names as bookmarks",
    "Conserva i segnalibri originali": "Keep the original bookmarks",
    "Conserva annotazioni e campi": "Keep annotations and fields",
    "Pagina vuota dopo i file con pagine dispari": "Blank page after files with an odd page count",
    "Apri il risultato in una nuova scheda": "Open the result in a new tab",
    "Salva direttamente su file": "Save directly to file",
    "Scegli i file da unire": "Choose the files to merge",
    "Unione in corso…": "Merging…",
    "Alcuni file sono stati esclusi:": "Some files were excluded:",
    "Unito.pdf": "Merged.pdf",
    "Salva il PDF unito": "Save the merged PDF",
    "File salvato: {p}": "File saved: {p}",
    "protetto": "protected",
    "illeggibile": "unreadable",
    "Duplica pagine": "Duplicate pages",
    "Copie:": "Copies:",
    "Subito dopo ciascuna pagina": "Right after each page",
    "Dopo l'ultima pagina selezionata": "After the last selected page",
    "In fondo al documento": "At the end of the document",
    "Mantieni i nomi dei campi modulo (valori condivisi)": "Keep form field names (shared values)",
    "Stai per creare più di 500 copie. Continuare?": "You are about to create more than 500 copies. Continue?",
    "Sposta pagine": "Move pages",
    "Sposta prima della pagina:": "Move before page:",
    "(oltre l'ultima = in fondo)": "(past the last = at the end)",
    "Fascicola fronte/retro": "Collate front/back",
    "I retri iniziano dalla pagina:": "Backs start at page:",
    "Retri in ordine inverso": "Backs in reverse order",
    "Ordine risultante: {o}": "Resulting order: {o}",
    "Le due parti hanno lunghezze diverse: le pagine in eccesso saranno accodate.":
        "The two parts have different lengths: the extra pages will be appended.",
    "Fascicola": "Collate",
    "Sostituisci pagine": "Replace pages",
    "Pagine da sostituire:": "Pages to replace:",
    "Pagine sorgente:": "Source pages:",
    "Annotazioni da conservare:": "Annotations to keep:",
    "Delle pagine originali": "From the original pages",
    "Della sorgente": "From the source",
    "Entrambe": "Both",
    "Il numero di pagine è diverso ({a} da sostituire, {b} sorgente). Continuare?":
        "The page counts differ ({a} to replace, {b} source). Continue?",
    "Acquisizione da scanner": "Scan",
    "Nessuno scanner rilevato. Collega un dispositivo e riprova.": "No scanner detected. Connect a device and try again.",
    "Dispositivo:": "Device:",
    "Risoluzione (dpi):": "Resolution (dpi):",
    "Colore:": "Color:",
    "Colori": "Color",
    "Grigi": "Grayscale",
    "Bianco e nero": "Black and white",
    "Acquisizione in corso…": "Scanning…",
    "Acquisizione non riuscita: {e}": "Scan failed: {e}",
    "Inserisci da scanner": "Insert from scanner",
    "Riprova": "Retry",
    "Applica anche ad altri file…": "Apply to other files too…",
    "Altri file da dividere": "Other files to split",
    "File non divisi:": "Files not split:",
    "Includi i segnalibri (sotto un segnalibro con il nome del file)": "Include bookmarks (under a bookmark named after the file)",
    "Unione alternata di due file (fronti e retri)": "Alternate merge of two files (fronts and backs)",
    "Secondo file in ordine inverso": "Second file in reverse order",
    "L'unione alternata richiede esattamente due file.": "Alternate merge requires exactly two files.",
    "Interpreta i valori come etichette di pagina": "Interpret values as page labels",
    "Annulla": "Cancel",
    "Acquisisci": "Scan",
    "Estrai": "Extract",
    "Dividi": "Split",
    "Unisci": "Merge",
    "Sposta": "Move",
    "Sostituisci": "Replace",
    "pagine": "pages",
}


# =========================================================================
# Ambito di pagine (PAG-02)
# =========================================================================

class PageScope(QGroupBox):
    """Scelta delle pagine: corrente, selezionate, tutte, intervallo + filtro."""

    changed = Signal()

    def __init__(self, document: Document, selected: list[int], current: int = 0, parent=None,
                 ordered: bool = False, allow_filter: bool = True):
        super().__init__(_("Pagine"), parent)
        self.document = document
        self.selected = list(selected)
        self.current = current
        self.ordered = ordered
        with ENGINE_LOCK:
            self.page_count = document.pdf.page_count
        lay = QVBoxLayout(self)
        self.rb_current = QRadioButton(_("Pagina corrente"))
        self.rb_selected = QRadioButton(_("Pagine selezionate ({n})").format(n=len(self.selected)))
        self.rb_all = QRadioButton(_("Tutte le pagine"))
        self.rb_range = QRadioButton(_("Intervallo:"))
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(_("es. 1-3, 8-"))
        group = QButtonGroup(self)
        for rb in (self.rb_current, self.rb_selected, self.rb_all, self.rb_range):
            group.addButton(rb)
        lay.addWidget(self.rb_selected if len(self.selected) > 1 else self.rb_current)
        if len(self.selected) > 1:
            self.rb_current.hide()
        else:
            self.rb_selected.hide()
        lay.addWidget(self.rb_all)
        row = QHBoxLayout()
        row.addWidget(self.rb_range)
        row.addWidget(self.range_edit, 1)
        lay.addLayout(row)
        self.use_labels = QCheckBox(_("Interpreta i valori come etichette di pagina"))
        with ENGINE_LOCK:
            self.labels = E.page_labels(document.pdf)
            has_labels = bool(document.pdf.get_page_labels())
        self.use_labels.setVisible(has_labels)
        lay.addWidget(self.use_labels)
        frow = QHBoxLayout()
        self.filter_label = QLabel(_("Filtro:"))
        self.filter_combo = QComboBox()
        for key, text in ((E.FILTER_ALL, _("Tutte")), (E.FILTER_ODD, _("Dispari")), (E.FILTER_EVEN, _("Pari")),
                          (E.FILTER_PORTRAIT, _("Verticali")), (E.FILTER_LANDSCAPE, _("Orizzontali"))):
            self.filter_combo.addItem(text, key)
        frow.addWidget(self.filter_label)
        frow.addWidget(self.filter_combo, 1)
        if not allow_filter:
            self.filter_label.hide()
            self.filter_combo.hide()
        lay.addLayout(frow)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        (self.rb_selected if len(self.selected) > 1 else self.rb_current).setChecked(True)
        for rb in (self.rb_current, self.rb_selected, self.rb_all, self.rb_range):
            rb.toggled.connect(self._update)
        self.range_edit.textChanged.connect(self._on_text)
        self.use_labels.toggled.connect(self._update)
        self.filter_combo.currentIndexChanged.connect(self._update)
        self._pages: list[int] = []
        self._error: str | None = None
        self._update()

    def _on_text(self) -> None:
        if not self.rb_range.isChecked():
            self.rb_range.setChecked(True)
        self._update()

    def set_all(self) -> None:
        self.rb_all.setChecked(True)

    def set_range(self, text: str) -> None:
        self.range_edit.setText(text)
        self.rb_range.setChecked(True)
        self._update()

    def set_filter(self, flt: str) -> None:
        self.filter_combo.setCurrentIndex(max(0, self.filter_combo.findData(flt)))

    def _update(self, *_a) -> None:
        self._error = None
        try:
            if self.rb_all.isChecked():
                base = list(range(self.page_count))
            elif self.rb_range.isChecked():
                base = E.parse_ranges(self.range_edit.text(), self.page_count, ordered=self.ordered,
                                      labels=self.labels if self.use_labels.isChecked() else None)
            elif self.rb_selected.isChecked():
                base = list(self.selected)
            else:
                base = [self.current]
            with ENGINE_LOCK:
                pages = E.filter_pages(self.document.pdf, base, self.filter_combo.currentData())
            if not pages:
                raise E.RangeError(_("Nessuna pagina corrisponde ai criteri indicati."))
            self._pages = pages
        except E.RangeError as e:
            self._pages = []
            self._error = str(e)
        if self._error:
            self.status.setText(self._error)
            self.status.setStyleSheet(f"color: {theme.hex_color('danger')};")
        else:
            n = len(self._pages)
            self.status.setText(_("1 pagina") if n == 1 else _("{n} pagine").format(n=n))
            self.status.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.changed.emit()

    def is_valid(self) -> bool:
        return self._error is None and bool(self._pages)

    def error(self) -> str | None:
        return self._error

    def pages(self) -> list[int]:
        return list(self._pages)


class BaseDialog(QDialog):
    """Dialogo con pulsanti OK/Annulla e convalida."""

    def __init__(self, parent, title: str, ok_text: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.layout_ = QVBoxLayout(self)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text or _("OK"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

    def finish_layout(self) -> None:
        self.layout_.addWidget(self.message)
        self.layout_.addStretch(1)
        self.layout_.addWidget(self.buttons)

    def ok_button(self) -> QPushButton:
        return self.buttons.button(QDialogButtonBox.StandardButton.Ok)

    def set_error(self, text: str | None) -> None:
        self.ok_button().setEnabled(not text)
        if text:
            self.message.setText(text)
            self.message.setStyleSheet(f"color: {theme.hex_color('danger')};")
            self.message.show()
        else:
            self.message.hide()

    def set_note(self, text: str | None) -> None:
        if text:
            self.message.setText(text)
            self.message.setStyleSheet(f"color: {theme.hex_color('warning')};")
            self.message.show()
        else:
            self.message.hide()


class PositionBox(QGroupBox):
    def __init__(self, parent=None, has_selection: bool = True):
        super().__init__(_("Posizione"), parent)
        lay = QVBoxLayout(self)
        self.combo = QComboBox()
        for key, text in (("after", _("Dopo la selezione")), ("before", _("Prima della selezione")),
                          ("start", _("All'inizio")), ("end", _("Alla fine"))):
            self.combo.addItem(text, key)
        lay.addWidget(self.combo)

    def set_where(self, where: str) -> None:
        self.combo.setCurrentIndex(max(0, self.combo.findData(where)))

    def index(self, selected: list[int], page_count: int) -> int:
        where = self.combo.currentData()
        if where == "start":
            return 0
        if where == "end" or not selected:
            return page_count
        return selected[0] if where == "before" else selected[-1] + 1


def _apply(ctx, label: str, fn, select=None):
    c = getattr(ctx, "pages_controller", None)
    if c is not None:
        return c.apply(label, fn, select=select)
    view = ctx.current_view()
    try:
        with view.document.change(label) as pdf:
            return fn(pdf)
    except FoliantError as e:
        ctx.error(label, str(e))
        return None


def _exec(dlg: QDialog) -> bool:
    return dlg.exec() == QDialog.DialogCode.Accepted


# =========================================================================
# PAG-06 — pagina vuota
# =========================================================================

UNITS = {"mm": E.MM, "pt": 1.0, "in": E.INCH}


class InsertBlankDialog(BaseDialog):
    def __init__(self, parent, document: Document, selected: list[int]):
        super().__init__(parent, _("Inserisci pagine vuote"), _("Inserisci"))
        self.document = document
        self.selected = selected
        form = QFormLayout()
        self.count = QSpinBox()
        self.count.setRange(1, E.MAX_BLANK)
        form.addRow(_("Quantità:"), self.count)
        self.size = QComboBox()
        self.size.addItem(_("Come la pagina adiacente"), "like")
        for name in E.PAPER_SIZES:
            self.size.addItem(name, name)
        self.size.addItem(_("Personalizzato"), "custom")
        form.addRow(_("Formato:"), self.size)
        self.width = QDoubleSpinBox()
        self.height = QDoubleSpinBox()
        for sb in (self.width, self.height):
            sb.setRange(0.1, 20000)
            sb.setDecimals(1)
        self.width.setValue(210)
        self.height.setValue(297)
        self.unit = QComboBox()
        for u in UNITS:
            self.unit.addItem(u, u)
        form.addRow(_("Larghezza:"), self.width)
        form.addRow(_("Altezza:"), self.height)
        form.addRow(_("Unità:"), self.unit)
        self.orientation = QComboBox()
        self.orientation.addItem(_("Verticale"), False)
        self.orientation.addItem(_("Orizzontale"), True)
        form.addRow(_("Orientamento:"), self.orientation)
        self.layout_.addLayout(form)
        self.position = PositionBox(self)
        self.layout_.addWidget(self.position)
        self.finish_layout()
        self.size.currentIndexChanged.connect(self._update)
        for w in (self.width, self.height):
            w.valueChanged.connect(self._update)
        self.unit.currentIndexChanged.connect(self._update)
        self._update()

    def _update(self, *_a) -> None:
        custom = self.size.currentData() == "custom"
        for w in (self.width, self.height, self.unit):
            w.setEnabled(custom)
        self.orientation.setEnabled(self.size.currentData() != "like")
        try:
            opts = self.options()
            if opts["size"] is not None:
                E.check_page_size(*opts["size"])
            self.set_error(None)
        except E.PageOpError as e:
            self.set_error(str(e))

    def options(self) -> dict:
        key = self.size.currentData()
        size = None
        if key == "custom":
            f = UNITS[self.unit.currentData()]
            size = (self.width.value() * f, self.height.value() * f)
        elif key != "like":
            size = E.PAPER_SIZES[key]
        with ENGINE_LOCK:
            n = self.document.pdf.page_count
        return {"count": self.count.value(), "size": size,
                "landscape": self.orientation.currentData() if size is not None else None,
                "at": self.position.index(self.selected, n)}


def open_insert_blank_dialog(ctx, view, selected: list[int]) -> bool:
    dlg = InsertBlankDialog(ctx.window, view.document, selected)
    if not _exec(dlg):
        return False
    o = dlg.options()
    like = (o["at"] - 1 if o["at"] > 0 else 0) if o["size"] is None else None
    res = _apply(ctx, _("Inserisci pagina vuota"),
                 lambda pdf: E.insert_blank_pages(pdf, o["at"], o["count"], o["size"], o["landscape"], like),
                 select=lambda r: r)
    return res is not None


# =========================================================================
# PAG-07 — inserisci da file
# =========================================================================

class InsertFileDialog(BaseDialog):
    def __init__(self, parent, ctx, document: Document, selected: list[int]):
        super().__init__(parent, _("Inserisci da file"), _("Inserisci"))
        self.ctx = ctx
        self.document = document
        self.selected = selected
        self.source: pymupdf.Document | None = None
        self.path: str | None = None
        form = QFormLayout()
        row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setReadOnly(True)
        browse = QPushButton(_("Sfoglia…"))
        browse.clicked.connect(self.browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        form.addRow(_("File:"), row)
        self.src_info = QLabel()
        form.addRow("", self.src_info)
        self.src_range = QLineEdit()
        self.src_range.setPlaceholderText(_("tutte"))
        form.addRow(_("Pagine del file:"), self.src_range)
        self.layout_.addLayout(form)
        self.position = PositionBox(self)
        self.layout_.addWidget(self.position)
        alt = QHBoxLayout()
        self.interleave = QCheckBox(_("Inserimento alternato ogni"))
        self.every = QSpinBox()
        self.every.setRange(1, 1000)
        alt.addWidget(self.interleave)
        alt.addWidget(self.every)
        alt.addWidget(QLabel(_("pagine del documento")))
        alt.addStretch(1)
        self.layout_.addLayout(alt)
        self.reverse = QCheckBox(_("Pagine del file in ordine inverso"))
        self.annots = QCheckBox(_("Includi annotazioni"))
        self.annots.setChecked(True)
        self.widgets = QCheckBox(_("Includi campi modulo"))
        self.widgets.setChecked(True)
        self.bookmarks = QCheckBox(_("Includi i segnalibri (sotto un segnalibro con il nome del file)"))
        for cb in (self.reverse, self.annots, self.widgets, self.bookmarks):
            self.layout_.addWidget(cb)
        self.finish_layout()
        self.src_range.textChanged.connect(self._update)
        self._update()

    def browse(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli il file da inserire"),
                                        _("Documenti (*.pdf *.xps *.epub *.png *.jpg *.jpeg *.tif *.tiff)"),
                                        multiple=False)
        if paths:
            self.set_path(paths[0])

    def set_path(self, path: str) -> bool:
        c = getattr(self.ctx, "pages_controller", None)
        if Path(path).suffix.lower() in E.IMAGE_SUFFIXES:
            try:
                src = E.image_to_pdf(path)
            except FoliantError as e:
                self.ctx.error(_("Inserisci da file"), str(e))
                return False
        else:
            src = c.open_source_interactive(path) if c else E.open_source(path)
        if src is None:
            return False
        if self.source is not None:
            self.source.close()
        self.source = src
        self.path = path
        self.path_edit.setText(path)
        self.src_info.setText(_("{n} pagine nel file").format(n=src.page_count))
        self._update()
        return True

    def _update(self, *_a) -> None:
        if self.source is None:
            self.set_error(_("Scegli un file."))
            return
        try:
            self.source_pages()
            self.set_error(None)
        except E.RangeError as e:
            self.set_error(str(e))

    def source_pages(self) -> list[int]:
        text = self.src_range.text().strip()
        if not text:
            return list(range(self.source.page_count))
        return E.parse_ranges(text, self.source.page_count, ordered=True)

    def options(self) -> dict:
        with ENGINE_LOCK:
            n = self.document.pdf.page_count
        return {"src_indices": self.source_pages(), "at": self.position.index(self.selected, n),
                "interleave": self.every.value() if self.interleave.isChecked() else 0,
                "reverse": self.reverse.isChecked(), "annots": self.annots.isChecked(),
                "widgets": self.widgets.isChecked(),
                "bookmark_title": Path(self.path).stem if self.bookmarks.isChecked() and self.path else None}

    def done(self, r) -> None:
        super().done(r)


def open_insert_file_dialog(ctx, view, selected: list[int], path: str | None = None) -> bool:
    dlg = InsertFileDialog(ctx.window, ctx, view.document, selected)
    if path and not dlg.set_path(path):
        return False
    try:
        if not _exec(dlg) or dlg.source is None:
            return False
        o = dlg.options()
        src = dlg.source
        res = _apply(ctx, _("Inserisci pagine da file"),
                     lambda pdf: E.insert_pdf_pages(pdf, src, o["at"], o["src_indices"], interleave=o["interleave"],
                                                    reverse=o["reverse"], annots=o["annots"], widgets=o["widgets"],
                                                    bookmark_title=o["bookmark_title"]),
                     select=lambda r: r)
        return res is not None
    finally:
        if dlg.source is not None:
            dlg.source.close()


# =========================================================================
# PAG-12 — estrai
# =========================================================================

class ExtractDialog(BaseDialog):
    def __init__(self, parent, document: Document, selected: list[int], current: int = 0):
        super().__init__(parent, _("Estrai pagine"), _("Estrai"))
        self.document = document
        self.scope = PageScope(document, selected, current, self, ordered=True)
        self.layout_.addWidget(self.scope)
        box = QGroupBox(_("Modalità"))
        bl = QVBoxLayout(box)
        self.rb_single = QRadioButton(_("Un unico file"))
        self.rb_each = QRadioButton(_("Un file per pagina"))
        self.rb_ranges = QRadioButton(_("Un file per intervallo (separati da ;)"))
        self.rb_single.setChecked(True)
        for rb in (self.rb_single, self.rb_each, self.rb_ranges):
            bl.addWidget(rb)
            rb.toggled.connect(self._update)
        self.layout_.addWidget(box)
        form = QFormLayout()
        self.template = QLineEdit("{nome}_p{pagina}")
        self.template.setToolTip(_("Segnaposto: {nome}, {pagina}, {n}, {inizio}, {fine}"))
        form.addRow(_("Nome dei file:"), self.template)
        self.layout_.addLayout(form)
        self.delete_after = QCheckBox(_("Elimina le pagine dal documento dopo l'estrazione"))
        self.open_result = QCheckBox(_("Apri il risultato"))
        self.open_result.setChecked(True)
        self.layout_.addWidget(self.delete_after)
        self.layout_.addWidget(self.open_result)
        self.finish_layout()
        self.scope.changed.connect(self._update)
        self._update()

    def mode(self) -> str:
        return "single" if self.rb_single.isChecked() else "each" if self.rb_each.isChecked() else "ranges"

    def _update(self, *_a) -> None:
        self.template.setEnabled(self.mode() != "single")
        self.open_result.setEnabled(self.mode() == "single")
        if self.mode() == "ranges":
            text = self.scope.range_edit.text()
            try:
                E.plan_ranges(self.scope.page_count, text)
                self.set_error(None)
            except E.RangeError as e:
                self.set_error(str(e))
            return
        self.set_error(self.scope.error())

    def options(self) -> dict:
        mode = self.mode()
        if mode == "ranges":
            plan = E.plan_ranges(self.scope.page_count, self.scope.range_edit.text())
            parts = plan.parts
        elif mode == "each":
            parts = [E.SplitPart([i]) for i in self.scope.pages()]
        else:
            parts = [E.SplitPart(self.scope.pages())]
        return {"mode": mode, "parts": parts, "template": self.template.text() or "{nome}_{n}",
                "delete_after": self.delete_after.isChecked(), "open": self.open_result.isChecked()}


def open_extract_dialog(ctx, view, selected: list[int]) -> bool:
    dlg = ExtractDialog(ctx.window, view.document, selected, view.current_page)
    if not _exec(dlg):
        return False
    o = dlg.options()
    document = view.document
    stem = Path(document.display_name).stem
    pages = sorted({i for p in o["parts"] for i in p.indices})
    if o["mode"] == "single":
        part = o["parts"][0]
        with ENGINE_LOCK:
            data = E.extract_pages(document.pdf, part.indices)
        name = f"{stem} ({_('pagine')} {E.format_ranges(part.indices).replace(' ', '')}).pdf"
        if o["open"]:
            ctx.open_document(Document.from_bytes(data, display_name=E.safe_filename(name)))
        else:
            path = ctx.ask_save_path(_("Estrai pagine"), E.safe_filename(name))
            if not path:
                return False
            from foliant.api import atomic_write
            atomic_write(path, data)
    else:
        folder = ctx.ask_directory(_("Scegli la cartella di destinazione"))
        if not folder:
            return False

        def work(progress):
            return E.write_parts(document.pdf, o["parts"], folder, o["template"], stem,
                                 progress=lambda f, t: progress.update(f, t))
        try:
            files = ctx.run_task(_("Estrazione in corso…"), work)
        except FoliantError as e:
            ctx.error(_("Estrai pagine"), str(e))
            return False
        ctx.status(_("Creati {n} file in {f}").format(n=len(files), f=folder))
    if o["delete_after"]:
        with ENGINE_LOCK:
            n = document.pdf.page_count
        if len(pages) >= n:
            ctx.info(_("Estrai pagine"), _("Le pagine estratte non sono state eliminate: il documento deve "
                                           "conservarne almeno una."))
        else:
            _apply(ctx, _("Elimina pagine estratte"), lambda pdf: E.delete_pages(pdf, pages),
                   select=lambda r: [max(0, min(pages[0], pdf_count(document) - 1))])
    return True


def pdf_count(document: Document) -> int:
    with ENGINE_LOCK:
        return document.pdf.page_count


# =========================================================================
# PAG-20…23 — dividi
# =========================================================================

class SplitDialog(BaseDialog):
    METHODS = ("every", "ranges", "parts", "bookmarks", "size", "blank", "text")

    def __init__(self, parent, document: Document):
        super().__init__(parent, _("Dividi documento"), _("Dividi"))
        self.document = document
        self.setMinimumWidth(520)
        with ENGINE_LOCK:
            self.page_count = document.pdf.page_count
            self.has_toc = bool(document.pdf.get_toc())
        box = QGroupBox(_("Metodo"))
        grid = QFormLayout(box)
        self.rbs: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)

        def add(key, text, widget=None):
            rb = QRadioButton(text)
            group.addButton(rb)
            self.rbs[key] = rb
            rb.toggled.connect(self._update)
            grid.addRow(rb, widget if widget is not None else QWidget())
        self.every = QSpinBox()
        self.every.setRange(1, 100000)
        self.every.setValue(1)
        add("every", _("Ogni N pagine"), self.every)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText("1-3; 4-10; 11-")
        add("ranges", _("Per intervalli (es. 1-3; 4-10; 11-)"), self.ranges)
        self.parts = QSpinBox()
        self.parts.setRange(1, 100000)
        self.parts.setValue(2)
        add("parts", _("In K parti uguali"), self.parts)
        self.level = QSpinBox()
        self.level.setRange(1, 10)
        add("bookmarks", _("Per segnalibri, fino al livello"), self.level)
        self.size_mb = QDoubleSpinBox()
        self.size_mb.setRange(0.01, 100000)
        self.size_mb.setValue(10)
        add("size", _("Per dimensione massima (MB)"), self.size_mb)
        add("blank", _("Alle pagine vuote (scartate)"))
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText(r"Fattura n\. (\d+)")
        add("text", _("Alle pagine che contengono il testo (espressione regolare)"), self.pattern)
        self.rbs["bookmarks"].setEnabled(self.has_toc)
        self.rbs["every"].setChecked(True)
        self.layout_.addWidget(box)
        self.collect = QCheckBox(_("Raccogli le pagine escluse in un file aggiuntivo"))
        self.layout_.addWidget(self.collect)
        form = QFormLayout()
        self.template = QLineEdit("{nome}_parte{n}")
        self.template.setToolTip(_("Segnaposto: {nome}, {n}, {inizio}, {fine}, {segnalibro}, {match1}"))
        form.addRow(_("Nome dei file:"), self.template)
        self.layout_.addLayout(form)
        self.overwrite = QCheckBox(_("Sovrascrivi i file esistenti"))
        self.layout_.addWidget(self.overwrite)
        self.extra_paths: list[str] = []
        erow = QHBoxLayout()
        self.extra_btn = QPushButton(icon("files"), _("Applica anche ad altri file…"))
        self.extra_btn.clicked.connect(self._choose_extra)
        self.extra_label = QLabel()
        self.extra_label.setWordWrap(True)
        erow.addWidget(self.extra_btn)
        erow.addWidget(self.extra_label, 1)
        self.layout_.addLayout(erow)
        self.ask_paths = None
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.layout_.addWidget(self.preview)
        self.finish_layout()
        for w in (self.every, self.parts, self.level, self.size_mb):
            w.valueChanged.connect(self._update)
        for w in (self.ranges, self.pattern):
            w.textChanged.connect(self._update)
        self.collect.toggled.connect(self._update)
        self._update()

    def method(self) -> str:
        return next(k for k, rb in self.rbs.items() if rb.isChecked())

    def _choose_extra(self) -> None:
        if self.ask_paths is None:
            return
        paths = self.ask_paths(_("Altri file da dividere"), "PDF (*.pdf)")
        self.set_extra_paths(paths)

    def set_extra_paths(self, paths: list[str]) -> None:
        self.extra_paths = list(paths)
        self.extra_label.setText(", ".join(Path(p).name for p in self.extra_paths))

    def plan(self) -> E.SplitPlan:
        with ENGINE_LOCK:
            return self.plan_for(self.document.pdf)

    def plan_for(self, pdf: pymupdf.Document, max_bytes: int | None = None, progress=None) -> E.SplitPlan:
        """Stessa divisione applicata a un documento qualsiasi."""
        m = self.method()
        with ENGINE_LOCK:
            if m == "size":
                return E.split_document(pdf, "size", max_bytes or int(self.size_mb.value() * 1024 * 1024),
                                        progress=progress)
            if m == "every":
                return E.split_document(pdf, "every", self.every.value())
            if m == "ranges":
                return E.split_document(pdf, "ranges", self.ranges.text(), collect_rest=self.collect.isChecked())
            if m == "parts":
                return E.split_document(pdf, "parts", self.parts.value())
            if m == "bookmarks":
                return E.split_document(pdf, "bookmarks", self.level.value())
            if m == "blank":
                return E.split_document(pdf, "separators", blank=True, discard=True)
            if m == "text":
                if not self.pattern.text():
                    raise E.RangeError(_("Indica almeno un intervallo."))
                return E.split_document(pdf, "separators", pattern=self.pattern.text())
        raise E.PageOpError("")

    def _update(self, *_a) -> None:
        if not hasattr(self, "preview") or not any(rb.isChecked() for rb in self.rbs.values()):
            return   # costruzione in corso
        m = self.method()
        self.collect.setEnabled(m == "ranges")
        defaults = {"bookmarks": "{segnalibro}", "text": "{nome}_{match1}"}
        if self.template.text() in ("{nome}_parte{n}", "{segnalibro}", "{nome}_{match1}"):
            self.template.setText(defaults.get(m, "{nome}_parte{n}"))
        if m in ("size", "blank"):
            self.preview.setText("")   # calcolo costoso: eseguito alla conferma
            self.set_error(None)
            return
        try:
            plan = self.plan()
        except FoliantError as e:
            self.preview.setText("")
            self.set_error(str(e))
            return
        sizes = ", ".join(str(len(p.indices)) for p in plan.parts[:12]) + (" …" if len(plan.parts) > 12 else "")
        self.preview.setText(_("Si otterranno {k} file: {sizes}").format(k=len(plan.parts), sizes=sizes))
        self.set_error(None)
        if plan.warnings:
            self.set_note("\n".join(plan.warnings))

    def options(self) -> dict:
        return {"method": self.method(), "template": self.template.text() or "{nome}_{n}",
                "overwrite": self.overwrite.isChecked(), "max_bytes": int(self.size_mb.value() * 1024 * 1024)}


def open_split_dialog(ctx, view, selected: list[int] | None = None) -> bool:
    document = view.document
    with ENGINE_LOCK:
        if document.pdf.page_count < 2:
            ctx.error(_("Dividi documento"), _("Il documento non ha pagine."))
            return False
    dlg = SplitDialog(ctx.window, document)
    dlg.ask_paths = ctx.ask_open_paths
    if not _exec(dlg):
        return False
    o = dlg.options()
    folder = ctx.ask_directory(_("Scegli la cartella di destinazione"))
    if not folder:
        return False
    stem = Path(document.display_name).stem
    method = o["method"]

    extra = list(dlg.extra_paths)
    failures: list[str] = []

    def work(progress):
        progress.update(0, _("Calcolo della divisione…"))
        plan = dlg.plan_for(document.pdf, o["max_bytes"], progress=lambda f: progress.update(f * 0.5))
        files = E.write_parts(document.pdf, plan.parts, folder, o["template"], stem, overwrite=o["overwrite"],
                              progress=lambda f, t: progress.update(0.5 + f * 0.5, t))
        for path in extra:
            try:
                src = E.open_source(path)
            except FoliantError as e:
                failures.append(f"{Path(path).name}: {e}")
                continue
            try:
                p2 = dlg.plan_for(src, o["max_bytes"])
                files += E.write_parts(src, p2.parts, folder, o["template"], Path(path).stem,
                                       overwrite=o["overwrite"])
            except FoliantError as e:
                failures.append(f"{Path(path).name}: {e}")
            finally:
                src.close()
        return plan, files
    try:
        plan, files = ctx.run_task(_("Divisione in corso…"), work)
    except FoliantError as e:
        ctx.error(_("Dividi documento"), str(e))
        return False
    msg = _("Creati {n} file in {f}").format(n=len(files), f=folder)
    if plan.warnings:
        msg += "\n\n" + "\n".join(plan.warnings)
    if failures:
        msg += "\n\n" + _("File non divisi:") + "\n" + "\n".join(failures)
    ctx.info(_("Dividi documento"), msg)
    return True


# =========================================================================
# PAG-24 — unisci
# =========================================================================

class MergeDialog(BaseDialog):
    def __init__(self, parent, ctx, paths: list[str] | None = None):
        super().__init__(parent, _("Unisci file"), _("Unisci"))
        self.ctx = ctx
        self.setMinimumSize(680, 460)
        self.passwords: dict[str, str] = {}
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("File"), _("Pagine"), _("Pagine da usare"), _("Dimensione")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.verticalHeader().hide()
        self.layout_.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, ic, slot in ((_("Aggiungi file…"), "file-plus", self.browse), (_("Rimuovi"), "trash-2", self.remove),
                               (_("Su"), "arrow-up", lambda: self.move(-1)), (_("Giù"), "arrow-down", lambda: self.move(1)),
                               (_("Ordina per nome"), "arrow-down-a-z", lambda: self.sort("name")),
                               (_("Ordina per data"), "calendar", lambda: self.sort("date"))):
            b = QPushButton(icon(ic), text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        self.layout_.addLayout(row)
        self.bookmarks = QCheckBox(_("Nomi dei file come segnalibri"))
        self.bookmarks.setChecked(True)
        self.keep_bookmarks = QCheckBox(_("Conserva i segnalibri originali"))
        self.keep_bookmarks.setChecked(True)
        self.keep_annots = QCheckBox(_("Conserva annotazioni e campi"))
        self.keep_annots.setChecked(True)
        self.blank_odd = QCheckBox(_("Pagina vuota dopo i file con pagine dispari"))
        self.alternate = QCheckBox(_("Unione alternata di due file (fronti e retri)"))
        self.alternate_reverse = QCheckBox(_("Secondo file in ordine inverso"))
        for cb in (self.bookmarks, self.keep_bookmarks, self.keep_annots, self.blank_odd, self.alternate,
                   self.alternate_reverse):
            self.layout_.addWidget(cb)
        self.rb_open = QRadioButton(_("Apri il risultato in una nuova scheda"))
        self.rb_save = QRadioButton(_("Salva direttamente su file"))
        self.rb_open.setChecked(True)
        self.layout_.addWidget(self.rb_open)
        self.layout_.addWidget(self.rb_save)
        self.finish_layout()
        self.table.itemChanged.connect(lambda *_a: self._update())
        self.alternate.toggled.connect(lambda *_a: self._update())
        for p in paths or []:
            self.add_path(p)
        self._update()

    def browse(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli i file da unire"),
                                        _("Documenti (*.pdf *.xps *.epub *.png *.jpg *.jpeg *.tif *.tiff)"))
        for p in paths:
            self.add_path(p)

    def add_path(self, path: str) -> None:
        pages = "?"
        try:
            if Path(path).suffix.lower() in E.IMAGE_SUFFIXES:
                pages = "1"
            else:
                src = E.open_source(path, self.passwords.get(path))
                pages = str(src.page_count)
                src.close()
        except (E.PasswordRequired, E.WrongPassword):
            c = getattr(self.ctx, "pages_controller", None)
            src = None
            if c is not None:
                from PySide6.QtWidgets import QInputDialog
                pw, ok = QInputDialog.getText(self, _("Documento protetto"),
                                              Path(path).name + "\n" + _("Inserisci la password:"),
                                              QLineEdit.EchoMode.Password)
                if ok:
                    try:
                        src = E.open_source(path, pw)
                        self.passwords[path] = pw
                        pages = str(src.page_count)
                        src.close()
                    except FoliantError:
                        pages = _("protetto")
            else:
                pages = _("protetto")
        except FoliantError:
            pages = _("illeggibile")
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        item = QTableWidgetItem(Path(path).name)
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 0, item)
        it = QTableWidgetItem(pages)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 1, it)
        self.table.setItem(r, 2, QTableWidgetItem(""))
        try:
            size = os.path.getsize(path)
            size_text = f"{size / 1024:.0f} kB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        except OSError:
            size_text = "—"
        it = QTableWidgetItem(size_text)
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(r, 3, it)
        self.table.blockSignals(False)
        self._update()

    def rows(self) -> list[tuple[str, str, str]]:
        out = []
        for r in range(self.table.rowCount()):
            path = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            out.append((path, self.table.item(r, 1).text(), self.table.item(r, 2).text().strip()))
        return out

    def _set_rows(self, rows) -> None:
        old = self.rows()
        self.table.setRowCount(0)
        for path, _p, rng in rows:
            self.add_path(path)
            self.table.item(self.table.rowCount() - 1, 2).setText(rng)
        del old

    def remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
        self._update()

    def move(self, delta: int) -> None:
        r = self.table.currentRow()
        rows = self.rows()
        if r < 0 or not 0 <= r + delta < len(rows):
            return
        rows[r], rows[r + delta] = rows[r + delta], rows[r]
        self._set_rows(rows)
        self.table.selectRow(r + delta)

    def sort(self, key: str) -> None:
        rows = self.rows()
        if key == "name":
            rows.sort(key=lambda t: Path(t[0]).name.lower())
        else:
            rows.sort(key=lambda t: os.path.getmtime(t[0]) if os.path.exists(t[0]) else 0)
        self._set_rows(rows)

    def _update(self) -> None:
        rows = self.rows()
        if not rows:
            self.set_error(_("Aggiungi almeno un file da unire."))
            return
        self.alternate_reverse.setEnabled(self.alternate.isChecked())
        if self.alternate.isChecked() and len(rows) != 2:
            self.set_error(_("L'unione alternata richiede esattamente due file."))
            return
        for path, pages, rng in rows:
            if rng and pages.isdigit():
                try:
                    E.parse_ranges(rng, int(pages), ordered=True)
                except E.RangeError as e:
                    self.set_error(f"{Path(path).name}: {e}")
                    return
        self.set_error(None)

    def entries(self) -> list[E.MergeEntry]:
        return [E.MergeEntry(path, rng or None, self.passwords.get(path)) for path, _p, rng in self.rows()]

    def options(self) -> dict:
        return {"entries": self.entries(), "bookmarks": self.bookmarks.isChecked(),
                "keep_bookmarks": self.keep_bookmarks.isChecked(), "annots": self.keep_annots.isChecked(),
                "blank_after_odd": self.blank_odd.isChecked(), "open": self.rb_open.isChecked(),
                "alternate": self.alternate.isChecked(), "alternate_reverse": self.alternate_reverse.isChecked()}


def open_merge_dialog(ctx, view=None, selected=None, paths: list[str] | None = None) -> bool:
    if paths is None and view is not None and view.document.path is not None:
        paths = [str(view.document.path)]
    dlg = MergeDialog(ctx.window, ctx, paths)
    if not _exec(dlg):
        return False
    o = dlg.options()
    errors: list[str] = []

    def work(progress):
        if o["alternate"]:
            a, b = o["entries"]
            return E.merge_alternate(a.path, b.path, reverse_b=o["alternate_reverse"], password_a=a.password,
                                     password_b=b.password)
        return E.merge_files(o["entries"], bookmarks=o["bookmarks"], keep_bookmarks=o["keep_bookmarks"],
                             annots=o["annots"], widgets=o["annots"], blank_after_odd=o["blank_after_odd"],
                             progress=lambda f, t: progress.update(f, t), errors=errors)
    try:
        data = ctx.run_task(_("Unione in corso…"), work)
    except FoliantError as e:
        ctx.error(_("Unisci file"), str(e))
        return False
    if errors:
        ctx.error(_("Unisci file"), _("Alcuni file sono stati esclusi:") + "\n" + "\n".join(errors))
    if o["open"]:
        ctx.open_document(Document.from_bytes(data, display_name=_("Unito.pdf")))
    else:
        path = ctx.ask_save_path(_("Salva il PDF unito"), _("Unito.pdf"))
        if not path:
            return False
        from foliant.api import atomic_write
        atomic_write(path, data)
        ctx.status(_("File salvato: {p}").format(p=path))
    return True


# =========================================================================
# PAG-15 — duplica con opzioni; PAG-04 — sposta in; PAG-05 — fascicola
# =========================================================================

class DuplicateDialog(BaseDialog):
    def __init__(self, parent):
        super().__init__(parent, _("Duplica pagine"), _("Duplica"))
        form = QFormLayout()
        self.copies = QSpinBox()
        self.copies.setRange(1, 10000)
        form.addRow(_("Copie:"), self.copies)
        self.where = QComboBox()
        for key, text in (("after", _("Subito dopo ciascuna pagina")), ("block", _("Dopo l'ultima pagina selezionata")),
                          ("end", _("In fondo al documento"))):
            self.where.addItem(text, key)
        form.addRow(_("Posizione"), self.where)
        self.layout_.addLayout(form)
        self.keep_names = QCheckBox(_("Mantieni i nomi dei campi modulo (valori condivisi)"))
        self.layout_.addWidget(self.keep_names)
        self.finish_layout()

    def options(self) -> dict:
        return {"copies": self.copies.value(), "where": self.where.currentData(),
                "keep_names": self.keep_names.isChecked()}


def open_duplicate_dialog(ctx, view, selected: list[int]) -> bool:
    dlg = DuplicateDialog(ctx.window)
    if not _exec(dlg):
        return False
    o = dlg.options()
    if o["copies"] * len(selected) > 500 and not ctx.confirm(_("Duplica pagine"),
                                                              _("Stai per creare più di 500 copie. Continuare?")):
        return False
    res = _apply(ctx, _("Duplica pagine"),
                 lambda pdf: E.duplicate_pages(pdf, selected, o["copies"], o["where"], o["keep_names"]),
                 select=lambda r: r)
    return res is not None


class MoveDialog(BaseDialog):
    def __init__(self, parent, page_count: int):
        super().__init__(parent, _("Sposta pagine"), _("Sposta"))
        form = QFormLayout()
        self.target = QSpinBox()
        self.target.setRange(1, page_count + 1)
        form.addRow(_("Sposta prima della pagina:"), self.target)
        form.addRow("", QLabel(_("(oltre l'ultima = in fondo)")))
        self.layout_.addLayout(form)
        self.finish_layout()

    def options(self) -> dict:
        return {"dest": self.target.value() - 1}


def open_move_dialog(ctx, view, selected: list[int]) -> bool:
    dlg = MoveDialog(ctx.window, view.page_count)
    if not _exec(dlg):
        return False
    c = getattr(ctx, "pages_controller", None)
    if c is not None:
        c.move_to_index(selected, dlg.options()["dest"])
        return True
    return _apply(ctx, _("Sposta pagine"), lambda pdf: E.move_pages(pdf, selected, dlg.options()["dest"])) is not None


class CollateDialog(BaseDialog):
    def __init__(self, parent, page_count: int):
        super().__init__(parent, _("Fascicola fronte/retro"), _("Fascicola"))
        self.page_count = page_count
        form = QFormLayout()
        self.split = QSpinBox()
        self.split.setRange(1, max(1, page_count))
        self.split.setValue((page_count + 1) // 2 + 1)
        form.addRow(_("I retri iniziano dalla pagina:"), self.split)
        self.layout_.addLayout(form)
        self.reverse = QCheckBox(_("Retri in ordine inverso"))
        self.reverse.setChecked(True)
        self.layout_.addWidget(self.reverse)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        self.layout_.addWidget(self.preview)
        self.finish_layout()
        self.split.valueChanged.connect(self._update)
        self.reverse.toggled.connect(self._update)
        self._update()

    def order(self) -> tuple[list[int], bool]:
        return E.collate_order(self.page_count, self.split.value() - 1, self.reverse.isChecked())

    def _update(self, *_a) -> None:
        order, uneven = self.order()
        shown = ", ".join(str(i + 1) for i in order[:24]) + (" …" if len(order) > 24 else "")
        self.preview.setText(_("Ordine risultante: {o}").format(o=shown))
        self.set_note(_("Le due parti hanno lunghezze diverse: le pagine in eccesso saranno accodate.") if uneven else None)


def open_collate_dialog(ctx, view, selected: list[int]) -> bool:
    dlg = CollateDialog(ctx.window, view.page_count)
    if not _exec(dlg):
        return False
    order, _uneven = dlg.order()
    if order == list(range(view.page_count)):
        return False
    return _apply(ctx, _("Fascicola"), lambda pdf: E.reorder(pdf, order)) is not None


# =========================================================================
# PAG-19 — sostituisci
# =========================================================================

class ReplaceDialog(InsertFileDialog):
    def __init__(self, parent, ctx, document: Document, selected: list[int]):
        super().__init__(parent, ctx, document, selected)
        self.setWindowTitle(_("Sostituisci pagine"))
        self.ok_button().setText(_("Sostituisci"))
        self.position.hide()
        for w in (self.interleave, self.every, self.reverse, self.widgets):
            w.hide()
        for i in range(self.layout_.count()):
            item = self.layout_.itemAt(i)
            if item is not None and item.layout() is not None:
                for j in range(item.layout().count()):
                    w = item.layout().itemAt(j).widget()
                    if isinstance(w, QLabel) and w.text() == _("pagine del documento"):
                        w.hide()
        self.annots.hide()
        self.target = QLineEdit(E.format_ranges(selected))
        form = QFormLayout()
        form.addRow(_("Pagine da sostituire:"), self.target)
        self.keep = QComboBox()
        for key, text in (("original", _("Delle pagine originali")), ("source", _("Della sorgente")),
                          ("both", _("Entrambe"))):
            self.keep.addItem(text, key)
        form.addRow(_("Annotazioni da conservare:"), self.keep)
        self.layout_.insertLayout(0, form)
        self.target.textChanged.connect(self._update)
        self._update()

    def _update(self, *_a) -> None:
        if not hasattr(self, "target"):
            return super()._update()
        try:
            E.parse_ranges(self.target.text(), pdf_count(self.document))
        except E.RangeError as e:
            self.set_error(str(e))
            return
        super()._update()

    def options(self) -> dict:
        return {"targets": E.parse_ranges(self.target.text(), pdf_count(self.document)),
                "src_indices": self.source_pages(), "keep": self.keep.currentData()}


def open_replace_dialog(ctx, view, selected: list[int], path: str | None = None) -> bool:
    dlg = ReplaceDialog(ctx.window, ctx, view.document, selected)
    if path and not dlg.set_path(path):
        return False
    try:
        if not _exec(dlg) or dlg.source is None:
            return False
        o = dlg.options()
        if len(o["targets"]) != len(o["src_indices"]) and not ctx.confirm(
                _("Sostituisci pagine"), _("Il numero di pagine è diverso ({a} da sostituire, {b} sorgente). "
                                           "Continuare?").format(a=len(o["targets"]), b=len(o["src_indices"]))):
            return False
        src = dlg.source
        res = _apply(ctx, _("Sostituisci pagine"),
                     lambda pdf: E.replace_pages(pdf, o["targets"], src, o["src_indices"], o["keep"]),
                     select=lambda r: r)
        return res is not None
    finally:
        if dlg.source is not None:
            dlg.source.close()


# =========================================================================
# PAG-10 — scanner (SANE tramite scanimage)
# =========================================================================

def list_scanners() -> list[tuple[str, str]]:
    """Dispositivi disponibili come (id, descrizione). Vuoto se SANE manca."""
    import shutil
    import subprocess
    exe = shutil.which("scanimage")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-f", "%d\t%v %m%n"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    devices = []
    for line in out.splitlines():
        if "\t" in line:
            dev, desc = line.split("\t", 1)
            devices.append((dev.strip(), desc.strip() or dev.strip()))
    return devices


def scan_pages(device: str, dpi: int = 300, mode: str = "Color", batch: bool = False, progress=None) -> list[bytes]:
    """Acquisisce una (o più, con alimentatore) pagine come PNG. Solleva FoliantError."""
    import shutil
    import subprocess
    import tempfile
    exe = shutil.which("scanimage")
    if not exe:
        raise E.PageOpError(_("Nessuno scanner rilevato. Collega un dispositivo e riprova."))
    with tempfile.TemporaryDirectory() as tmp:
        args = [exe, "-d", device, "--resolution", str(dpi), "--mode", mode, "--format=png"]
        if batch:
            args += [f"--batch={tmp}/p%04d.png"]
            proc = subprocess.run(args, capture_output=True, timeout=600)
            files = sorted(Path(tmp).glob("p*.png"))
            if not files:
                raise E.PageOpError(_("Acquisizione non riuscita: {e}").format(e=proc.stderr.decode(errors="ignore")))
            return [f.read_bytes() for f in files]
        proc = subprocess.run(args, capture_output=True, timeout=600)
        if proc.returncode != 0 or not proc.stdout:
            raise E.PageOpError(_("Acquisizione non riuscita: {e}").format(e=proc.stderr.decode(errors="ignore")))
        return [proc.stdout]


def scanned_to_pdf(images: list[bytes], dpi: int) -> pymupdf.Document:
    """Una pagina per immagine acquisita, dimensionata secondo la risoluzione."""
    out = pymupdf.open()
    for data in images:
        pix = pymupdf.Pixmap(data)
        w, h = pix.width * 72.0 / dpi, pix.height * 72.0 / dpi
        page = out.new_page(width=w, height=h)
        page.insert_image(page.rect, stream=data)
    return out


class ScannerDialog(BaseDialog):
    def __init__(self, parent, devices: list[tuple[str, str]]):
        super().__init__(parent, _("Acquisizione da scanner"), _("Acquisisci"))
        form = QFormLayout()
        self.device = QComboBox()
        for dev, desc in devices:
            self.device.addItem(desc, dev)
        form.addRow(_("Dispositivo:"), self.device)
        self.dpi = QComboBox()
        for d in (150, 200, 300, 600):
            self.dpi.addItem(str(d), d)
        self.dpi.setCurrentIndex(2)
        form.addRow(_("Risoluzione (dpi):"), self.dpi)
        self.color = QComboBox()
        for key, text in (("Color", _("Colori")), ("Gray", _("Grigi")), ("Lineart", _("Bianco e nero"))):
            self.color.addItem(text, key)
        form.addRow(_("Colore:"), self.color)
        self.layout_.addLayout(form)
        self.position = PositionBox(self)
        self.layout_.addWidget(self.position)
        self.finish_layout()

    def options(self) -> dict:
        return {"device": self.device.currentData(), "dpi": self.dpi.currentData(), "mode": self.color.currentData()}


def open_scanner_dialog(ctx, view, selected: list[int]) -> bool:
    devices = list_scanners()
    if not devices:
        ctx.error(_("Acquisizione da scanner"), _("Nessuno scanner rilevato. Collega un dispositivo e riprova."))
        return False
    dlg = ScannerDialog(ctx.window, devices)
    if not _exec(dlg):
        return False
    o = dlg.options()
    try:
        images = ctx.run_task(_("Acquisizione in corso…"),
                              lambda progress: scan_pages(o["device"], o["dpi"], o["mode"]))
    except FoliantError as e:
        ctx.error(_("Acquisizione da scanner"), str(e))
        return False
    at = dlg.position.index(selected, view.page_count)
    src = scanned_to_pdf(images, o["dpi"])
    try:
        return _apply(ctx, _("Inserisci da scanner"), lambda pdf: E.insert_pdf_pages(pdf, src, at),
                      select=lambda r: r) is not None
    finally:
        src.close()
