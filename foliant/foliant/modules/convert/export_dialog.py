"""Finestra «Esporta come» (CNV-01): formato, intervallo, opzioni comuni e specifiche, destinazione."""

from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from foliant.api import _, bind_icon, theme

from .common import RangeError, parse_page_range
from .engine import FORMATS

# ordine e gruppi del menu dei formati
FORMAT_ORDER = ["docx", "xlsx", "pptx", "csv", "png", "jpeg", "tiff", "bmp", "gif", "long-image",
                "txt", "rtf", "html", "xml", "epub", "pdfa-1b", "pdfa-2b", "pdfa-3b", "pdfa-2u", "pdfa-3u"]
LAYOUT_FORMATS = {"docx", "html", "rtf"}
OCR_FORMATS = {"docx", "xlsx", "csv", "pptx", "txt", "rtf", "html", "xml", "epub"}


def _combo(items: list[tuple[str, object]], current=None) -> QComboBox:
    box = QComboBox()
    for label, data in items:
        box.addItem(label, data)
    if current is not None:
        i = box.findData(current)
        if i >= 0:
            box.setCurrentIndex(i)
    return box


def _set_combo(box: QComboBox, value) -> None:
    i = box.findData(value)
    if i >= 0:
        box.setCurrentIndex(i)


class _Options(QWidget):
    """Pannello di opzioni specifiche: `values()` / `load(dict)`."""

    def __init__(self):
        super().__init__()
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.fields: dict[str, QWidget] = {}

    def add(self, key: str, label: str, widget: QWidget) -> QWidget:
        self.fields[key] = widget
        self.form.addRow(label, widget)
        return widget

    def values(self) -> dict:
        out = {}
        for key, w in self.fields.items():
            if isinstance(w, QComboBox):
                out[key] = w.currentData()
            elif isinstance(w, QCheckBox):
                out[key] = w.isChecked()
            elif isinstance(w, QSpinBox):
                out[key] = w.value()
            elif isinstance(w, QLineEdit):
                out[key] = w.text()
        return out

    def load(self, values: dict) -> None:
        for key, value in values.items():
            w = self.fields.get(key)
            if isinstance(w, QComboBox):
                _set_combo(w, value)
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QSpinBox):
                try:
                    w.setValue(int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(w, QLineEdit):
                w.setText(str(value))


def _dpi_spin(value: int) -> QSpinBox:
    s = QSpinBox()
    s.setRange(72, 1200)
    s.setSingleStep(50)
    s.setValue(value)
    s.setSuffix(" dpi")
    return s


def build_options(fmt: str) -> _Options:
    o = _Options()
    if fmt in ("png", "jpeg", "tiff", "bmp", "gif"):
        o.add("dpi", _("Risoluzione"), _dpi_spin(150))
        o.add("color", _("Colore"), _combo([(_("Colore"), "color"), (_("Scala di grigi"), "gray"),
                                            (_("Bianco e nero (soglia)"), "bw"),
                                            (_("Bianco e nero (retinatura)"), "bw-dither")]))
        if fmt == "jpeg":
            q = QSpinBox()
            q.setRange(1, 100)
            q.setValue(90)
            o.add("jpeg_quality", _("Qualità JPEG"), q)
        if fmt == "png":
            o.add("transparent", _("Sfondo trasparente"), QCheckBox())
        if fmt == "tiff":
            o.add("multipage", _("TIFF multipagina"), QCheckBox())
            o.add("tiff_compression", _("Compressione"), _combo([
                ("LZW", "lzw"), ("Deflate", "deflate"), ("JPEG", "jpeg"), (_("CCITT G4 (bianco e nero)"), "group4"),
                (_("Nessuna"), "none")]))
        if fmt == "bmp":
            o.add("bmp_bits", _("Profondità"), _combo([(_("24 bit"), 24), (_("8 bit indicizzato"), 8)]))
        ann = QCheckBox()
        ann.setChecked(True)
        o.add("annotations", _("Includi annotazioni e campi"), ann)
        name = QLineEdit("{nome}_{pagina:03}")
        name.setToolTip(_("Segnaposto: {nome}, {pagina}, {pagina:03}, {data}, {formato}"))
        o.add("name_template", _("Nome dei file"), name)
    elif fmt == "long-image":
        o.add("image_format", _("Formato"), _combo([("PNG", "png"), ("JPEG", "jpeg")]))
        o.add("dpi", _("Risoluzione"), _dpi_spin(96))
        sp = QSpinBox()
        sp.setRange(0, 500)
        sp.setSuffix(" px")
        o.add("spacing", _("Spaziatura tra le pagine"), sp)
        o.add("align", _("Allineamento"), _combo([(_("Centrato"), "center"), (_("A sinistra"), "left")]))
        split = QCheckBox()
        split.setChecked(True)
        o.add("split", _("Dividi se troppo alta"), split)
    elif fmt == "xlsx":
        o.add("sheets", _("Fogli"), _combo([(_("Un foglio unico"), "single"), (_("Un foglio per pagina"), "page"),
                                            (_("Un foglio per tabella"), "table")]))
        merge = QCheckBox()
        merge.setChecked(True)
        o.add("merge_tables", _("Unisci le tabelle su più pagine"), merge)
        o.add("include_text", _("Includi il testo fuori dalle tabelle"), QCheckBox())
        o.add("table_strategy", _("Rilevamento tabelle"), _combo([(_("Con bordi"), "lines"),
                                                                  (_("Con e senza bordi"), "auto"),
                                                                  (_("Senza bordi (allineamento)"), "text")]))
        o.add("decimal", _("Separatore decimale"), _combo([(_("Automatico"), None), (_("Virgola (1.234,56)"), ","),
                                                            (_("Punto (1,234.56)"), ".")]))
    elif fmt == "csv":
        o.add("delimiter", _("Delimitatore"), _combo([(_("Punto e virgola"), ";"), (_("Virgola"), ","),
                                                      (_("Tabulazione"), "tab")]))
        o.add("single_file", _("Un file unico"), QCheckBox())
        o.add("bom", _("UTF-8 con BOM"), QCheckBox())
        o.add("line_ending", _("Fine riga"), _combo([("CRLF", "crlf"), ("LF", "lf")]))
        o.add("numbers", _("Numeri"), _combo([(_("Come visualizzati"), "displayed"),
                                              (_("Normalizzati (punto decimale)"), "normalized")]))
        o.add("table_strategy", _("Rilevamento tabelle"), _combo([(_("Con bordi"), "lines"),
                                                                  (_("Con e senza bordi"), "auto"),
                                                                  (_("Senza bordi (allineamento)"), "text")]))
    elif fmt == "pptx":
        o.add("slide_size", _("Dimensione diapositiva"), _combo([(_("Come la pagina"), "page"), ("16:9", "16:9"),
                                                                ("4:3", "4:3")]))
        o.add("vector", _("Grafica vettoriale"), _combo([(_("Come forme"), "shapes"), (_("Omessa"), "none")]))
    elif fmt == "txt":
        o.add("mode", _("Modalità"), _combo([(_("Ordine di lettura"), "reading"), (_("Impaginazione"), "layout")]))
        o.add("encoding", _("Codifica"), _combo([("UTF-8", "utf-8"), (_("UTF-8 con BOM"), "utf-8-bom"),
                                                 ("UTF-16", "utf-16")]))
        o.add("line_ending", _("Fine riga"), _combo([("LF", "lf"), ("CRLF", "crlf")]))
        o.add("page_separator", _("Separatore di pagina"), _combo([(_("Avanzamento modulo"), "ff"),
                                                                    (_("Riga di trattini"), "----------"),
                                                                    (_("Nessuno"), "none")]))
        dh = QCheckBox()
        dh.setChecked(True)
        o.add("dehyphenate", _("Ricongiungi le parole sillabate"), dh)
        o.add("annotations", _("Includi il testo delle annotazioni"), QCheckBox())
    elif fmt == "html":
        single = QCheckBox()
        single.setChecked(True)
        o.add("single_file", _("File unico (immagini incorporate)"), single)
        toc = QCheckBox()
        toc.setChecked(True)
        o.add("toc", _("Sommario dai segnalibri"), toc)
    elif fmt == "xml":
        o.add("detail", _("Dettaglio"), _combo([(_("Parole con coordinate"), "words"), (_("Righe"), "lines"),
                                                (_("Blocchi"), "blocks")]))
        ann = QCheckBox()
        ann.setChecked(True)
        o.add("annotations", _("Includi le annotazioni"), ann)
    elif fmt == "epub":
        every = QSpinBox()
        every.setRange(1, 1000)
        every.setValue(10)
        o.add("chapter_every", _("Capitoli ogni N pagine (senza segnalibri)"), every)
        rm = QCheckBox()
        rm.setChecked(True)
        o.add("remove_furniture", _("Rimuovi intestazioni e piè di pagina ripetuti"), rm)
        o.add("layout", _("Impaginazione"), _combo([(_("Scorrevole"), "flow"), (_("Layout fisso"), "fixed")]))
        for key, label in (("title", _("Titolo")), ("author", _("Autore")), ("lang", _("Lingua (es. it)"))):
            edit = QLineEdit()
            edit.setPlaceholderText(_("Dal documento"))
            o.add(key, label, edit)
    elif fmt.startswith("pdfa"):
        o.add("save_anyway", _("Se la conformità non è raggiunta, salva un PDF ordinario"), QCheckBox())
    return o


class ExportDialog(QDialog):
    """Scelta di formato, pagine, opzioni e destinazione. `values()` → (formato, percorso, opzioni)."""

    def __init__(self, parent, *, page_count: int, current_page: int = 0, name: str = "documento",
                 folder: str = "", fmt: str = "docx", selected: list[int] | None = None,
                 settings: QSettings | None = None, scanned: bool = False):
        super().__init__(parent)
        self.setWindowTitle(_("Esporta come"))
        self.setMinimumWidth(520)
        self.page_count = page_count
        self.current_page = current_page
        self.selected = selected or []
        self.settings = settings
        self._pages: list[int] = []
        lay = QVBoxLayout(self)

        top = QFormLayout()
        self.format_box = QComboBox()
        for key in FORMAT_ORDER:
            self.format_box.addItem(_(FORMATS[key][1]), key)
        top.addRow(_("Formato"), self.format_box)
        preset_row = QHBoxLayout()
        self.preset_box = QComboBox()
        self.preset_box.setMinimumWidth(200)
        preset_row.addWidget(self.preset_box, 1)
        self.preset_save = QPushButton(_("Salva…"))
        self.preset_del = QPushButton(_("Elimina"))
        for b in (self.preset_save, self.preset_del):
            b.setAutoDefault(False)
            preset_row.addWidget(b)
        top.addRow(_("Preset"), preset_row)

        range_row = QHBoxLayout()
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(_("Tutte (es. 1-3, 7, 10-fine, pari)"))
        range_row.addWidget(self.range_edit, 1)
        for label, value in ((_("Tutte"), ""), (_("Corrente"), "corrente")):
            b = QPushButton(label)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _c=False, v=value: self.range_edit.setText(v))
            range_row.addWidget(b)
        if self.selected:
            b = QPushButton(_("Selezionate"))
            b.setAutoDefault(False)
            b.clicked.connect(lambda: self.range_edit.setText("selezionate"))
            range_row.addWidget(b)
        top.addRow(_("Pagine"), range_row)
        self.range_error = QLabel()
        self.range_error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.range_error.setVisible(False)
        top.addRow("", self.range_error)
        lay.addLayout(top)

        self.common = QGroupBox(_("Opzioni comuni"))
        cf = QFormLayout(self.common)
        self.layout_box = _combo([(_("Testo scorrevole"), "flow"), (_("Fedele all'impaginazione"), "faithful")])
        cf.addRow(_("Layout"), self.layout_box)
        self.ocr_box = _combo([(_("Automatico (solo pagine scansionate)"), "auto"), (_("Sempre"), "always"),
                               (_("Mai"), "never")])
        cf.addRow(_("OCR"), self.ocr_box)
        self.lang_edit = QLineEdit("ita+eng")
        self.lang_edit.setToolTip(_("Lingue OCR separate da +, es. ita+eng"))
        cf.addRow(_("Lingue OCR"), self.lang_edit)
        self.scanned_hint = QLabel(_("Il documento contiene pagine scansionate: l'OCR ne renderà il testo modificabile."))
        self.scanned_hint.setWordWrap(True)
        self.scanned_hint.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.scanned_hint.setVisible(scanned)
        cf.addRow(self.scanned_hint)
        lay.addWidget(self.common)

        self.specific = QGroupBox(_("Opzioni del formato"))
        sl = QVBoxLayout(self.specific)
        self.stack = QStackedWidget()
        sl.addWidget(self.stack)
        self.panels: dict[str, _Options] = {}
        for key in FORMAT_ORDER:
            panel = build_options(key)
            self.panels[key] = panel
            self.stack.addWidget(panel)
        lay.addWidget(self.specific)

        dest = QGroupBox(_("Destinazione"))
        df = QFormLayout(dest)
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(folder or os.path.expanduser("~"))
        browse = QPushButton(_("Sfoglia…"))
        bind_icon(browse, "folder-open")
        browse.setAutoDefault(False)
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        df.addRow(_("Cartella"), folder_row)
        self.name_edit = QLineEdit(name)
        df.addRow(_("Nome del file"), self.name_edit)
        self.policy_box = _combo([(_("Aggiungi un suffisso numerico"), "rename"), (_("Sovrascrivi"), "overwrite")])
        df.addRow(_("Se il file esiste"), self.policy_box)
        lay.addWidget(dest)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Converti"))
        bind_icon(self.ok_button, "file-output")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.format_box.currentIndexChanged.connect(self._format_changed)
        self._reload_presets()
        self.preset_box.activated.connect(self._apply_preset)
        self.preset_save.clicked.connect(self._save_preset_dialog)
        self.preset_del.clicked.connect(self._delete_preset)
        self.range_edit.textChanged.connect(lambda _t: self.range_error.setVisible(False))
        _set_combo(self.format_box, fmt)
        self._format_changed()

    # ------------------------------------------------------------------ stato
    @property
    def fmt(self) -> str:
        return self.format_box.currentData()

    def _format_changed(self, *_a) -> None:
        fmt = self.fmt
        from PySide6.QtWidgets import QSizePolicy
        for key, panel in self.panels.items():
            pol = QSizePolicy.Policy.Preferred if key == fmt else QSizePolicy.Policy.Ignored
            panel.setSizePolicy(pol, pol)
        self.stack.setCurrentWidget(self.panels[fmt])
        self.specific.setVisible(bool(self.panels[fmt].fields))
        self.layout_box.setEnabled(fmt in LAYOUT_FORMATS)
        self.ocr_box.setEnabled(fmt in OCR_FORMATS)
        self.lang_edit.setEnabled(fmt in OCR_FORMATS)
        self.common.setVisible(fmt in LAYOUT_FORMATS or fmt in OCR_FORMATS)
        self._load_settings(fmt)
        self.adjustSize()

    def _settings_key(self, fmt: str) -> str:
        return f"convert/options/{fmt}"

    def _load_settings(self, fmt: str) -> None:
        if self.settings is None:
            return
        raw = self.settings.value(self._settings_key(fmt), "")
        try:
            values = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            values = {}
        self.panels[fmt].load(values)
        if "layout" in values:
            _set_combo(self.layout_box, values["layout"])
        if "ocr" in values:
            _set_combo(self.ocr_box, values["ocr"])

    def _save_settings(self, fmt: str, options: dict) -> None:
        if self.settings is None:
            return
        keep = {k: v for k, v in options.items() if k not in ("pages", "policy", "selected", "current")}
        self.settings.setValue(self._settings_key(fmt), json.dumps(keep))
        self.settings.setValue("convert/last_format", fmt)

    # ------------------------------------------------------------------ preset
    def presets(self) -> dict:
        if self.settings is None:
            return {}
        try:
            data = json.loads(self.settings.value("convert/presets", "") or "{}")
            return data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    def _reload_presets(self) -> None:
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem(_("(nessuno)"), None)
        for name in sorted(self.presets()):
            self.preset_box.addItem(name, name)
        self.preset_box.blockSignals(False)
        self.preset_del.setEnabled(len(self.presets()) > 0)
        self.preset_box.setEnabled(self.settings is not None)
        self.preset_save.setEnabled(self.settings is not None)

    def save_preset(self, name: str) -> None:
        presets = self.presets()
        opts = self.panels[self.fmt].values()
        opts.update(layout=self.layout_box.currentData(), ocr=self.ocr_box.currentData(),
                    language=self.lang_edit.text())
        presets[name] = {"format": self.fmt, "options": opts}
        self.settings.setValue("convert/presets", json.dumps(presets))
        self._reload_presets()
        self.preset_box.setCurrentIndex(self.preset_box.findData(name))

    def apply_preset(self, name: str) -> None:
        preset = self.presets().get(name)
        if not preset:
            return
        _set_combo(self.format_box, preset.get("format"))
        opts = dict(preset.get("options") or {})
        self.panels[self.fmt].load(opts)
        if opts.get("layout"):
            _set_combo(self.layout_box, opts["layout"])
        if opts.get("ocr"):
            _set_combo(self.ocr_box, opts["ocr"])
        if opts.get("language"):
            self.lang_edit.setText(opts["language"])

    def _apply_preset(self, *_a) -> None:
        name = self.preset_box.currentData()
        if name:
            self.apply_preset(name)

    def _save_preset_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, _("Salva preset"), _("Nome del preset:"))
        if ok and name.strip():
            self.save_preset(name.strip())

    def _delete_preset(self) -> None:
        name = self.preset_box.currentData()
        if not name or self.settings is None:
            return
        presets = self.presets()
        presets.pop(name, None)
        self.settings.setValue("convert/presets", json.dumps(presets))
        self._reload_presets()

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Scegli cartella"), self.folder_edit.text())
        if path:
            self.folder_edit.setText(path)

    # ------------------------------------------------------------- risultato
    def validate(self) -> bool:
        try:
            self._pages = parse_page_range(self.range_edit.text(), self.page_count, self.current_page, self.selected)
        except RangeError as e:
            self.range_error.setText(str(e))
            self.range_error.setVisible(True)
            self.range_edit.setFocus()
            return False
        if not self.name_edit.text().strip():
            self.range_error.setText(_("Indica il nome del file."))
            self.range_error.setVisible(True)
            return False
        return True

    def accept(self) -> None:
        if not self.validate():
            return
        self._save_settings(self.fmt, self.options())
        super().accept()

    def options(self) -> dict:
        fmt = self.fmt
        opts = dict(self.panels[fmt].values())
        if fmt in LAYOUT_FORMATS:
            opts["layout"] = self.layout_box.currentData()
        if fmt in OCR_FORMATS:
            opts["ocr"] = self.ocr_box.currentData()
            opts["language"] = self.lang_edit.text().strip() or None
        if fmt == "long-image" and opts.get("image_format") == "jpeg":
            pass
        opts["pages"] = list(self._pages) if self._pages else self.range_edit.text()
        opts["policy"] = self.policy_box.currentData()
        return opts

    def output_path(self) -> str:
        fmt = self.fmt
        ext = FORMATS[fmt][0]
        if fmt == "long-image":
            ext = ".jpg" if self.panels[fmt].values().get("image_format") == "jpeg" else ".png"
        name = self.name_edit.text().strip()
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
        return str(Path(self.folder_edit.text().strip() or ".") / (name + ext))

    def values(self) -> tuple[str, str, dict]:
        return self.fmt, self.output_path(), self.options()


__all__ = ["ExportDialog", "FORMAT_ORDER", "build_options", "Qt"]
