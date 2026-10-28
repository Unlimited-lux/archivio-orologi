"""Finestre di dialogo del modulo OCR (impostazioni, lingue, testo, revisione, blocco)."""

from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, icon, theme

from . import engine, languages
from .preprocess import PreprocessOptions

DPI_CHOICES = (150, 200, 300, 400, 600)


# ============================================================ selezione lingue
class LanguageSelector(QWidget):
    """Elenco delle lingue installate con caselle e ordine (la prima è la principale)."""

    manageRequested = Signal()
    changed = Signal()

    def __init__(self, selected: list[str], parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget(self)
        self.list.setObjectName("ocrLanguages")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setMinimumHeight(110)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        self.up = QPushButton(icon("arrow-up"), "", self)
        self.up.setToolTip(_("Sposta su (la prima lingua è la principale)"))
        self.down = QPushButton(icon("arrow-down"), "", self)
        self.down.setToolTip(_("Sposta giù"))
        self.manage = QPushButton(icon("languages"), _("Gestisci lingue…"), self)
        row.addWidget(self.up)
        row.addWidget(self.down)
        row.addStretch(1)
        row.addWidget(self.manage)
        lay.addLayout(row)
        self.warning = QLabel(self)
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {theme.hex_color('warning')};")
        lay.addWidget(self.warning)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.manage.clicked.connect(self.manageRequested.emit)
        self.list.itemChanged.connect(lambda _i: self._update())
        self.populate(selected)

    def populate(self, selected: list[str]) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        installed = languages.available_languages(refresh=True)
        # le lingue richieste ma non installate restano in elenco (mai tolte in silenzio, OCR-03)
        order = list(dict.fromkeys(selected)) + [c for c in installed if c not in selected]
        for code in order:
            label = f"{languages.language_name(code)}  ({code})"
            if code not in installed:
                label += "  — " + _("non installata")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if code in selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self._update()

    def selected(self) -> list[str]:
        out = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def set_checked(self, code: str, checked: bool) -> None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == code:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        new = row + delta
        if row < 0 or not 0 <= new < self.list.count():
            return
        item = self.list.takeItem(row)
        self.list.insertItem(new, item)
        self.list.setCurrentRow(new)
        self._update()

    def _update(self) -> None:
        n = len(self.selected())
        missing = languages.missing_languages(self.selected())
        if missing:
            self.warning.setText(_("Lingue non installate: {l}.").format(
                l=", ".join(languages.language_name(c) for c in missing)))
        elif n == 0:
            self.warning.setText(_("Scegli almeno una lingua."))
        elif n > 3:
            self.warning.setText(_("Più di 3 lingue insieme rallentano il riconoscimento."))
        else:
            self.warning.setText("")
        self.changed.emit()


# ============================================================ impostazioni OCR
class OcrDialog(QDialog):
    """Impostazioni del riconoscimento: modalità, lingue, pagine, pre-elaborazione."""

    def __init__(self, parent, *, page_count: int, current_page: int, detected: list[int],
                 settings=None, mode: str = engine.SEARCHABLE, pages_text: str | None = None,
                 allow_text_mode: bool = True, infos: dict | None = None):
        super().__init__(parent)
        self.infos = infos or {}
        self.setWindowTitle(_("Riconoscimento del testo (OCR)"))
        self.setObjectName("ocrDialog")
        self.page_count = page_count
        self.current_page = current_page
        self.detected = detected
        self.settings = settings
        get = (lambda k, d: settings.value(f"ocr/{k}", d)) if settings is not None else (lambda k, d: d)

        lay = QVBoxLayout(self)
        intro = QLabel(_("Foliant riconosce il testo delle pagine scannerizzate sul tuo computer, senza rete."))
        intro.setWordWrap(True)
        lay.addWidget(intro)

        # --- risultato
        box = QGroupBox(_("Risultato"), self)
        bl = QVBoxLayout(box)
        self.mode_group = QButtonGroup(self)
        self.rb_searchable = QRadioButton(_("PDF ricercabile — testo invisibile sopra l'immagine, aspetto identico"))
        self.rb_editable = QRadioButton(_("PDF modificabile — testo vero, modificabile, su sfondo ripulito"))
        self.rb_text = QRadioButton(_("Solo testo — da copiare o salvare come file TXT"))
        for i, rb in enumerate((self.rb_searchable, self.rb_editable, self.rb_text)):
            self.mode_group.addButton(rb, i)
            bl.addWidget(rb)
        self.rb_text.setVisible(allow_text_mode)
        {engine.SEARCHABLE: self.rb_searchable, engine.EDITABLE: self.rb_editable,
         engine.TEXT_ONLY: self.rb_text}.get(mode, self.rb_searchable).setChecked(True)
        lay.addWidget(box)

        form = QFormLayout()
        # --- lingue
        saved = get("languages", None)
        if isinstance(saved, str):
            saved = [s for s in saved.split(",") if s]
        selected = list(saved) if saved else engine.default_languages()
        self.languages = LanguageSelector(selected, self)
        self.languages.manageRequested.connect(self._manage_languages)
        form.addRow(_("Lingue:"), self.languages)

        # --- pagine
        self.pages = QComboBox(self)
        self.pages.addItem(_("Tutte le pagine"), "all")
        self.pages.addItem(_("Pagina corrente ({n})").format(n=current_page + 1), "current")
        if detected:
            self.pages.addItem(_("Pagine senza testo ({p})").format(p=engine.format_pages(detected)), "detected")
        self.pages.addItem(_("Intervallo…"), "custom")
        self.range_edit = QLineEdit(self)
        self.range_edit.setPlaceholderText(_("es. 1-3, 7, 10-fine"))
        self.range_edit.setEnabled(False)
        prow = QHBoxLayout()
        prow.addWidget(self.pages, 1)
        prow.addWidget(self.range_edit, 1)
        form.addRow(_("Pagine:"), prow)
        self.pages.currentIndexChanged.connect(
            lambda _i: self.range_edit.setEnabled(self.pages.currentData() == "custom"))
        if pages_text:
            self.pages.setCurrentIndex(self.pages.findData("custom"))
            self.range_edit.setText(pages_text)
        elif detected:
            self.pages.setCurrentIndex(self.pages.findData("detected"))

        self.existing = QComboBox(self)
        self.existing.addItem(_("Salta le pagine che hanno già testo"), engine.SKIP)
        self.existing.addItem(_("Sostituisci il livello OCR esistente"), engine.REPLACE)
        self.existing.addItem(_("Riconosci anche quelle (la pagina diventa un'immagine)"), engine.FORCE)
        self.existing.setCurrentIndex(max(0, self.existing.findData(get("existing", engine.SKIP))))
        form.addRow(_("Pagine con testo:"), self.existing)

        self.dpi = QComboBox(self)
        self.dpi.addItem(_("Automatica (300 dpi)"), 0)
        for d in DPI_CHOICES:
            self.dpi.addItem(f"{d} dpi", d)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(int(get("dpi", 0)))))
        form.addRow(_("Risoluzione di lavoro:"), self.dpi)

        self.font = QComboBox(self)
        self.font.addItem(_("Automatico (come nella scansione)"), "auto")
        self.font.addItem(_("Senza grazie"), "sans")
        self.font.addItem(_("Con grazie"), "serif")
        self.font.addItem(_("Monospaziato"), "mono")
        self.font.setCurrentIndex(max(0, self.font.findData(get("font", "auto"))))
        form.addRow(_("Carattere del testo modificabile:"), self.font)
        self.destination = QComboBox(self)
        self.destination.addItem(_("Documento corrente"), "current")
        self.destination.addItem(_("Nuovo documento (l'originale resta invariato)"), "new")
        self.destination.setCurrentIndex(max(0, self.destination.findData(get("destination", "current"))))
        form.addRow(_("Destinazione:"), self.destination)
        lay.addLayout(form)

        # --- pre-elaborazione
        pre = QGroupBox(_("Miglioramento dell'immagine"), self)
        pl = QHBoxLayout(pre)
        self.cb_deskew = QCheckBox(_("Raddrizza"))
        self.cb_rotate = QCheckBox(_("Rotazione automatica"))
        self.cb_denoise = QCheckBox(_("Rimuovi rumore"))
        self.cb_contrast = QCheckBox(_("Migliora contrasto"))
        for cb, key in ((self.cb_deskew, "deskew"), (self.cb_rotate, "auto_rotate"),
                        (self.cb_denoise, "denoise"), (self.cb_contrast, "contrast")):
            cb.setChecked(str(get(key, "true")).lower() in ("true", "1"))
            pl.addWidget(cb)
        lay.addWidget(pre)
        self.cb_apply_page = QCheckBox(_("Raddrizza e ruota anche la pagina visibile (PDF ricercabile)"))
        self.cb_apply_page.setChecked(str(get("apply_to_page", "false")).lower() in ("true", "1"))
        lay.addWidget(self.cb_apply_page)
        self.cb_clean_page = QCheckBox(_("Applica la pulizia anche all'immagine della pagina (PDF ricercabile)"))
        self.cb_clean_page.setChecked(str(get("clean_page", "false")).lower() in ("true", "1"))
        lay.addWidget(self.cb_clean_page)
        self.cb_keep = QCheckBox(_("Conserva la scansione originale come livello nascosto (PDF modificabile)"))
        self.cb_keep.setChecked(str(get("keep_original", "false")).lower() in ("true", "1"))
        lay.addWidget(self.cb_keep)

        self.cb_keep_review = QCheckBox(_("Conserva nel file i dati di revisione (parole incerte)"))
        self.cb_keep_review.setChecked(str(get("keep_review", "false")).lower() in ("true", "1"))
        lay.addWidget(self.cb_keep_review)
        self.cb_preview = QCheckBox(_("Mostra l'anteprima e le parole incerte prima di applicare"))
        self.cb_preview.setChecked(str(get("preview", "true")).lower() in ("true", "1"))
        lay.addWidget(self.cb_preview)

        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(self.summary)
        self.error = QLabel(self)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)

        bottom = QHBoxLayout()
        self.btn_save_profile = QPushButton(icon("save"), _("Salva profilo…"), self)
        self.btn_load_profile = QPushButton(icon("folder-open"), _("Carica profilo…"), self)
        bottom.addWidget(self.btn_save_profile)
        bottom.addWidget(self.btn_load_profile)
        bottom.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Avvia OCR"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)
        self.btn_save_profile.clicked.connect(self._save_profile_dialog)
        self.btn_load_profile.clicked.connect(self._load_profile_dialog)
        self.mode_group.idToggled.connect(lambda *_a: self._update_mode())
        self.range_edit.textChanged.connect(lambda _t: self.validate())
        self.pages.currentIndexChanged.connect(lambda _i: self.validate())
        self.existing.currentIndexChanged.connect(lambda _i: self.validate())
        self.languages.changed.connect(self.validate)
        self._update_mode()
        self.validate()

    def _update_mode(self) -> None:
        editable = self.rb_editable.isChecked()
        text = self.rb_text.isChecked()
        self.font.setEnabled(editable)
        self.cb_preview.setEnabled(not text)
        self.cb_apply_page.setEnabled(self.rb_searchable.isChecked())
        self.cb_clean_page.setEnabled(self.rb_searchable.isChecked())
        self.cb_keep.setEnabled(editable)
        self.destination.setEnabled(not text)
        self.validate()

    def validate(self) -> bool:
        """Controllo immediato: errore accanto ai campi e «Avvia» disattivato se non valido."""
        if not hasattr(self, "ok_button"):
            return True
        error = ""
        pages: list[int] = []
        try:
            pages = self.selected_pages()
        except ValueError as e:
            error = str(e)
        if not error and not self.languages.selected():
            error = _("Scegli almeno una lingua.")
        self.error.setText(error)
        self.ok_button.setEnabled(not error)
        self.range_edit.setStyleSheet(f"border: 1px solid {theme.hex_color('danger')};"
                                      if error and self.pages.currentData() == "custom" else "")
        self._update_summary(pages if not error else [])
        return not error

    def _update_summary(self, pages: list[int]) -> None:
        if not pages:
            self.summary.setText("")
            return
        from . import detect
        mode = self.mode()
        policy = self.existing.currentData()
        todo, skipped = 0, {}
        for i in pages:
            inf = self.infos.get(i)
            cat = inf.category if inf is not None else detect.IMAGE_ONLY
            skip = cat == detect.EMPTY or (cat == detect.TEXT and policy != engine.FORCE) or (
                cat == detect.OCR_LAYER and policy == engine.SKIP and mode != engine.EDITABLE)
            if skip:
                label = detect.category_label(cat)
                skipped[label] = skipped.get(label, 0) + 1
            else:
                todo += 1
        workers = engine.OcrOptions().worker_count()
        seconds = max(1, round(todo * 4.0 / workers))
        est = _("{s} s").format(s=seconds) if seconds < 90 else _("{m} min").format(m=round(seconds / 60))
        text = _("Da elaborare: {n} pagine · tempo stimato: circa {t}").format(n=todo, t=est)
        if skipped:
            text += " · " + _("saltate: {s}").format(
                s=", ".join(f"{n} ({lab})" for lab, n in skipped.items()))
        self.summary.setText(text)

    # ------------------------------------------------------------- profili
    def profile(self) -> dict:
        o = self.options()
        return {"format": "foliant-ocr-profile", "version": 1, "mode": o.mode, "languages": o.languages,
                "dpi": self.dpi.currentData(), "existing_text": o.existing_text, "font": o.font,
                "preprocess": o.preprocess.as_dict(), "apply_to_page": o.apply_to_page, "keep_original": o.keep_original, "clean_page": o.clean_page,
                "destination": self.destination.currentData(), "preview": self.cb_preview.isChecked()}

    def load_profile(self, data: dict) -> None:
        if not isinstance(data, dict) or data.get("format") != "foliant-ocr-profile":
            raise ValueError(_("Il file non è un profilo OCR di Foliant."))
        {engine.SEARCHABLE: self.rb_searchable, engine.EDITABLE: self.rb_editable,
         engine.TEXT_ONLY: self.rb_text}.get(data.get("mode"), self.rb_searchable).setChecked(True)
        langs = [c for c in data.get("languages", []) if isinstance(c, str)]
        self.languages.populate(langs)
        for c in langs:
            self.languages.set_checked(c, True)
        for combo, key in ((self.dpi, "dpi"), (self.existing, "existing_text"), (self.font, "font"),
                           (self.destination, "destination")):
            i = combo.findData(data.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        pre = data.get("preprocess", {})
        for cb, key in ((self.cb_deskew, "deskew"), (self.cb_rotate, "auto_rotate"),
                        (self.cb_denoise, "denoise"), (self.cb_contrast, "contrast")):
            if key in pre:
                cb.setChecked(bool(pre[key]))
        self.cb_apply_page.setChecked(bool(data.get("apply_to_page", False)))
        self.cb_keep.setChecked(bool(data.get("keep_original", False)))
        self.cb_clean_page.setChecked(bool(data.get("clean_page", False)))
        self.cb_preview.setChecked(bool(data.get("preview", True)))
        self.validate()

    def _save_profile_dialog(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getSaveFileName(self, _("Salva profilo OCR"), "profilo-ocr.json",
                                               _("Profilo OCR (*.json)"))
        if path:
            from foliant.core.document import atomic_write
            atomic_write(path, json.dumps(self.profile(), ensure_ascii=False, indent=2).encode("utf-8"))

    def _load_profile_dialog(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getOpenFileName(self, _("Carica profilo OCR"), "", _("Profilo OCR (*.json)"))
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                self.load_profile(json.load(f))
        except (OSError, ValueError) as e:
            self.error.setText(str(e))

    def _manage_languages(self) -> None:
        dlg = LanguagesDialog(self)
        dlg.exec()
        self.languages.populate(self.languages.selected())

    def mode(self) -> str:
        if self.rb_editable.isChecked():
            return engine.EDITABLE
        if self.rb_text.isChecked():
            return engine.TEXT_ONLY
        return engine.SEARCHABLE

    def selected_pages(self) -> list[int]:
        kind = self.pages.currentData()
        if kind == "current":
            return [self.current_page]
        if kind == "detected":
            return list(self.detected)
        if kind == "custom":
            return engine.parse_pages(self.range_edit.text(), self.page_count)
        return list(range(self.page_count))

    def options(self) -> engine.OcrOptions:
        return engine.OcrOptions(
            languages=self.languages.selected(), mode=self.mode(), dpi=int(self.dpi.currentData()) or 300,
            apply_to_page=self.cb_apply_page.isChecked() and self.mode() == engine.SEARCHABLE,
            keep_original=self.cb_keep.isChecked() and self.mode() == engine.EDITABLE,
            clean_page=self.cb_clean_page.isChecked() and self.mode() == engine.SEARCHABLE,
            preprocess=PreprocessOptions(deskew=self.cb_deskew.isChecked(), auto_rotate=self.cb_rotate.isChecked(),
                                         denoise=self.cb_denoise.isChecked(),
                                         contrast=self.cb_contrast.isChecked()),
            existing_text=self.existing.currentData(), font=self.font.currentData())

    @property
    def new_document(self) -> bool:
        return self.destination.currentData() == "new" and self.mode() != engine.TEXT_ONLY

    @property
    def preview(self) -> bool:
        return self.cb_preview.isChecked() and self.mode() != engine.TEXT_ONLY

    def accept(self) -> None:
        if not self.validate():
            return
        if self.settings is not None:
            o = self.options()
            s = self.settings
            s.setValue("ocr/languages", ",".join(o.languages))
            s.setValue("ocr/dpi", self.dpi.currentData())
            s.setValue("ocr/destination", self.destination.currentData())
            s.setValue("ocr/apply_to_page", "true" if self.cb_apply_page.isChecked() else "false")
            s.setValue("ocr/keep_original", "true" if self.cb_keep.isChecked() else "false")
            s.setValue("ocr/clean_page", "true" if self.cb_clean_page.isChecked() else "false")
            s.setValue("ocr/keep_review", "true" if self.cb_keep_review.isChecked() else "false")
            s.setValue("ocr/font", o.font)
            s.setValue("ocr/existing", o.existing_text)
            for k, v in o.preprocess.as_dict().items():
                if isinstance(v, bool):
                    s.setValue(f"ocr/{k}", "true" if v else "false")
            s.setValue("ocr/preview", "true" if self.cb_preview.isChecked() else "false")
        super().accept()


def size_label(mb: float) -> str:
    """Dimensione leggibile: «< 1 MB» sotto il megabyte, un decimale fino a 10 MB."""
    if mb < 1:
        return _("< 1 MB")
    return f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"


# ============================================================ gestione lingue
class LanguagesDialog(QDialog):
    """Catalogo delle lingue: installate, da installare da file o da scaricare."""

    def __init__(self, parent=None, run_task=None):
        super().__init__(parent)
        self.setWindowTitle(_("Lingue di riconoscimento"))
        self.setObjectName("ocrLanguagesDialog")
        self.resize(620, 520)
        self._run_task = run_task
        lay = QVBoxLayout(self)
        info = QLabel(_("Italiano e inglese sono sempre disponibili. Le altre lingue si installano da un file "
                        "«.traineddata» oppure, solo se lo chiedi, si scaricano da Internet."))
        info.setWordWrap(True)
        lay.addWidget(info)
        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels([_("Lingua"), _("Codice"), _("Scrittura"), _("Stato")])
        header = self.table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.btn_file = QPushButton(icon("file-input"), _("Installa da file…"), self)
        self.btn_download = QPushButton(icon("download"), _("Scarica…"), self)
        self.btn_remove = QPushButton(icon("trash-2"), _("Rimuovi"), self)
        row.addWidget(self.btn_file)
        row.addWidget(self.btn_download)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        lay.addLayout(row)
        version = languages.tesseract_version() or _("motore OCR non trovato")
        foot = QLabel(_("Motore: {v}").format(v=version))
        foot.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(foot)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        close.rejected.connect(self.reject)
        lay.addWidget(close)
        self.btn_file.clicked.connect(self.install_file)
        self.btn_download.clicked.connect(self.download)
        self.btn_remove.clicked.connect(self.remove)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    def refresh(self) -> None:
        installed = set(languages.available_languages(refresh=True))
        user = set(languages.user_installed_languages())
        codes = sorted(languages.CATALOG, key=lambda c: (c not in installed, {"ita": 0, "eng": 1}.get(c, 2),
                                                         languages.language_name(c)))
        codes += sorted(c for c in installed if c not in languages.CATALOG)
        self.table.setRowCount(len(codes))
        for r, code in enumerate(codes):
            info = languages.CATALOG.get(code)
            if code in user:
                state = _("installata") if languages.verify_installed(code) else _("danneggiata")
            elif code in installed:
                state = _("installata (sistema)")
            else:
                size = " · " + size_label(info.size_mb) if info and info.size_mb else ""
                state = _("non installata") + size
            values = [languages.language_name(code), code, info.script if info else "", state]
            for c, v in enumerate(values):
                item = QTableWidgetItem(v)
                item.setData(Qt.ItemDataRole.UserRole, code)
                if code in installed and c == 3:
                    item.setIcon(icon("check"))
                item.setToolTip(state if c == 3 else v)
                self.table.setItem(r, c, item)
        self._update_buttons()

    def current_code(self) -> str | None:
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _update_buttons(self) -> None:
        code = self.current_code()
        installed = set(languages.available_languages())
        user = set(languages.user_installed_languages())
        self.btn_download.setEnabled(bool(code) and code not in installed)
        self.btn_remove.setEnabled(bool(code) and code in user and code not in languages.PROTECTED)

    def install_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path, _f = QFileDialog.getOpenFileName(self, _("Pacchetto lingua"), os.path.expanduser("~"),
                                               _("Pacchetti lingua (*.traineddata)"))
        if not path:
            return
        try:
            code = languages.install_from_file(path)
        except languages.LanguagePackError as e:
            QMessageBox.warning(self, _("Pacchetto non valido"), str(e))
            return
        self.refresh()
        QMessageBox.information(self, _("Lingua installata"),
                                _("«{n}» è pronta per il riconoscimento.").format(n=languages.language_name(code)))

    def download(self) -> None:
        code = self.current_code()
        if not code:
            return
        info = languages.CATALOG.get(code)
        size = f" ({size_label(info.size_mb)})" if info and info.size_mb else ""
        answer = QMessageBox.question(
            self, _("Scarica lingua"),
            _("Scaricare da Internet il pacchetto «{n}»{s}? Serve una connessione di rete; "
              "nessun documento viene inviato.").format(n=languages.language_name(code), s=size))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._run_task is not None:
                self._run_task(_("Download della lingua…"), lambda p: languages.download_language(code, p))
            else:
                languages.download_language(code)
        except languages.LanguagePackError as e:
            QMessageBox.warning(self, _("Download non riuscito"), str(e))
            return
        except Exception as e:  # annullato o errore imprevisto
            if type(e).__name__ != "OperationCancelled":
                QMessageBox.warning(self, _("Download non riuscito"), str(e))
            return
        self.refresh()

    def remove(self) -> None:
        code = self.current_code()
        if not code:
            return
        try:
            languages.remove_language(code)
        except languages.LanguagePackError as e:
            QMessageBox.warning(self, _("Impossibile rimuovere"), str(e))
            return
        self.refresh()


# ============================================================ testo riconosciuto
class TextDialog(QDialog):
    """Testo riconosciuto: copia, salvataggio TXT e, con un risultato OCR, opzioni ed export ALTO/hOCR."""

    def __init__(self, parent, text: str, title: str | None = None, save_cb=None, result=None, save_path_cb=None,
                 source_name: str = "", actions: list | None = None):
        super().__init__(parent)
        self.setWindowTitle(title or _("Testo riconosciuto"))
        self.setObjectName("ocrTextDialog")
        self.resize(720, 560)
        self._save_cb = save_cb
        self._save_path_cb = save_path_cb   # (titolo, nome, filtro, suffisso) -> percorso | None
        self.result = result
        self.source_name = source_name
        lay = QVBoxLayout(self)
        if result is not None:
            opts = QHBoxLayout()
            self.separator = QComboBox(self)
            self.separator.addItem(_("Riga «— Pagina N —»"), "line")
            self.separator.addItem(_("Salto pagina (U+000C)"), "formfeed")
            self.cb_paragraphs = QCheckBox(_("Paragrafi ricomposti"), self)
            self.cb_dehyphen = QCheckBox(_("Togli la sillabazione a fine riga"), self)
            self.cb_crlf = QCheckBox(_("Fine riga Windows (CRLF)"), self)
            opts.addWidget(QLabel(_("Separatore:")))
            opts.addWidget(self.separator)
            for w in (self.cb_paragraphs, self.cb_dehyphen, self.cb_crlf):
                opts.addWidget(w)
            opts.addStretch(1)
            lay.addLayout(opts)
            for w in (self.cb_paragraphs, self.cb_dehyphen, self.cb_crlf):
                w.toggled.connect(lambda _c: self.regenerate())
            self.separator.currentIndexChanged.connect(lambda _i: self.regenerate())
        self.edit = QPlainTextEdit(self)
        self.edit.setPlainText(text)
        lay.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.btn_copy = QPushButton(icon("copy"), _("Copia tutto"), self)
        self.btn_save = QPushButton(icon("save"), _("Salva come TXT…"), self)
        row.addWidget(self.btn_copy)
        if result is not None:
            self.page_box = QComboBox(self)
            for res in result.pages:
                self.page_box.addItem(_("Pagina {n}").format(n=res.index + 1), res.index)
            self.btn_copy_page = QPushButton(icon("clipboard-copy"), _("Copia pagina"), self)
            row.addWidget(self.page_box)
            row.addWidget(self.btn_copy_page)
            self.btn_copy_page.clicked.connect(self.copy_page)
        row.addWidget(self.btn_save)
        if result is not None:
            self.btn_alto = QPushButton(_("Salva ALTO…"), self)
            self.btn_hocr = QPushButton(_("Salva hOCR…"), self)
            row.addWidget(self.btn_alto)
            row.addWidget(self.btn_hocr)
            self.btn_alto.clicked.connect(lambda: self.save_xml("alto"))
            self.btn_hocr.clicked.connect(lambda: self.save_xml("hocr"))
        self.action_buttons = {}
        for label, cb in actions or []:
            b = QPushButton(label, self)
            b.clicked.connect(lambda _c=False, cb=cb: (self.accept(), cb()))
            row.addWidget(b)
            self.action_buttons[label] = b
        row.addStretch(1)
        close = QPushButton(_("Chiudi"), self)
        row.addWidget(close)
        lay.addLayout(row)
        self.btn_copy.clicked.connect(self.copy)
        self.btn_save.clicked.connect(self.save)
        close.clicked.connect(self.accept)
        if result is not None:
            self.regenerate()

    def text_options(self) -> dict:
        return {"separator": self.separator.currentData(), "paragraphs": self.cb_paragraphs.isChecked(),
                "dehyphenate": self.cb_dehyphen.isChecked(), "newline": "\r\n" if self.cb_crlf.isChecked() else "\n"}

    def regenerate(self) -> None:
        from . import textexport
        self.edit.setPlainText(textexport.result_text(self.result, **self.text_options()))

    def copy(self) -> None:
        from foliant.api import copy_text_to_clipboard
        copy_text_to_clipboard(self.edit.toPlainText())
        self.btn_copy.setText(_("Copiato"))

    def copy_page(self) -> None:
        from foliant.api import copy_text_to_clipboard

        from . import textexport
        res = self.result.page(self.page_box.currentData())
        if res is not None:
            o = self.text_options()
            copy_text_to_clipboard(textexport.page_text(res, o["paragraphs"], o["dehyphenate"]))

    def save(self) -> None:
        if self._save_cb is not None:
            self._save_cb(self.edit.toPlainText())

    def save_xml(self, kind: str) -> None:
        from . import textexport
        if self._save_path_cb is None:
            return
        base = os.path.splitext(self.source_name or "testo")[0]
        if kind == "alto":
            path = self._save_path_cb(_("Salva ALTO"), base + ".alto.xml", _("ALTO XML (*.xml)"), ".xml")
            if path:
                textexport.save_alto(path, self.result, self.source_name)
        else:
            path = self._save_path_cb(_("Salva hOCR"), base + ".hocr", _("hOCR (*.hocr *.html)"), ".hocr")
            if path:
                textexport.save_hocr(path, self.result, self.source_name)


# ============================================================ anteprima e revisione
class PagePreview(QWidget):
    """Immagine della pagina con i riquadri delle parole (incerte evidenziate)."""

    wordClicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QImage | None = None
        self.boxes: list[tuple[QRectF, bool]] = []
        self.current = -1
        self.scale = 1.0
        self.setMinimumSize(200, 200)

    def set_page(self, image: QImage | None, boxes: list[tuple[QRectF, bool]]) -> None:
        self.image = image
        self.boxes = boxes
        self.current = -1
        if image is not None:
            self.setFixedSize(image.size())
        self.update()

    def set_current(self, i: int) -> None:
        self.current = i
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("canvas"))
        if self.image is None:
            p.end()
            return
        p.drawImage(0, 0, self.image)
        warn = QColor(theme.color("warning"))
        warn.setAlpha(80)
        acc = QColor(theme.color("accent"))
        for i, (r, low) in enumerate(self.boxes):
            if i == self.current:
                p.setPen(QPen(acc, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
            elif low:
                p.setPen(QPen(theme.color("warning"), 1))
                p.setBrush(QBrush(warn))
            else:
                c = QColor(acc)
                c.setAlpha(60)
                p.setPen(QPen(c, 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
        p.end()

    def mousePressEvent(self, e) -> None:
        pos = e.position()
        for i, (r, _low) in enumerate(self.boxes):
            if r.contains(pos):
                self.wordClicked.emit(i)
                return


class ReviewDialog(QDialog):
    """Anteprima del risultato e correzione delle parole prima di applicarlo."""

    PREVIEW_DPI = 110

    def __init__(self, parent, result: engine.OcrResult, source_pdf: pymupdf.Document):
        super().__init__(parent)
        self.setWindowTitle(_("Anteprima e revisione OCR"))
        self.setObjectName("ocrReviewDialog")
        self.resize(1100, 720)
        self.result = result
        self.pdf = source_pdf
        self._words: list = []
        self._loading = False
        lay = QVBoxLayout(self)
        summary = QLabel(self._summary())
        summary.setWordWrap(True)
        lay.addWidget(summary)
        split = QSplitter(Qt.Orientation.Horizontal, self)
        self.pages = QListWidget(split)
        self.pages.setMaximumWidth(220)
        scroll = QScrollArea(split)
        self.scroll = scroll
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview = PagePreview()
        scroll.setWidget(self.preview)
        right = QWidget(split)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.only_low = QCheckBox(_("Solo parole incerte"), right)
        self.only_low.setChecked(True)
        rl.addWidget(self.only_low)
        self.table = QTableWidget(0, 2, right)
        self.table.setHorizontalHeaderLabels([_("Parola"), _("Affidabilità")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        rl.addWidget(self.table, 1)
        hint = QLabel(_("Fai doppio clic su una parola per correggerla."))
        hint.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        rl.addWidget(hint)
        split.setStretchFactor(1, 1)
        split.setSizes([200, 600, 300])
        lay.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Applica"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        for res in result.pages:
            if res.ok:
                low = len(res.low_confidence_words())
                text = _("Pagina {n} — {k} parole incerte").format(n=res.index + 1, k=low)
                ic = icon("circle-alert") if low else icon("circle-check")
            elif res.status == "failed":
                text = _("Pagina {n} — non riconosciuta").format(n=res.index + 1)
                ic = icon("circle-x")
            else:
                text = _("Pagina {n} — saltata ({m})").format(n=res.index + 1, m=res.message)
                ic = icon("circle-minus")
            if res.notes:
                text += " · ⚠"
                ic = icon("triangle-alert")
            item = QListWidgetItem(ic, text)
            item.setData(Qt.ItemDataRole.UserRole, res.index)
            tip = "\n".join(([res.message] if res.message else []) + res.notes)
            if tip:
                item.setToolTip(tip)
            self.pages.addItem(item)
        self.pages.currentRowChanged.connect(self._show_page)
        self.only_low.toggled.connect(lambda _c: self._fill_table())
        self.table.itemChanged.connect(self._edited)
        self.table.currentCellChanged.connect(lambda r, *_a: self._select_row(r))
        self.preview.wordClicked.connect(self._select_word)
        first = next((i for i, r in enumerate(result.pages) if r.ok), 0)
        if self.pages.count():
            self.pages.setCurrentRow(first)

    def _summary(self) -> str:
        r = self.result
        words = sum(len(p.words) for p in r.recognized)
        low = sum(len(p.low_confidence_words()) for p in r.recognized)
        parts = [_("Pagine riconosciute: {n}").format(n=len(r.recognized)),
                 _("parole: {w}").format(w=words), _("incerte: {l}").format(l=low)]
        if r.failed:
            parts.append(_("non riconosciute: {f}").format(f=engine.format_pages([p.index for p in r.failed])))
        if r.skipped:
            parts.append(_("saltate: {s}").format(s=len(r.skipped)))
        text = " · ".join(parts)
        notes = r.notes()
        if notes:
            text += "\n" + "\n".join(notes)
        return text

    def current_result(self) -> engine.PageResult | None:
        item = self.pages.currentItem()
        return self.result.page(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _show_page(self, _row: int) -> None:
        res = self.current_result()
        if res is None or self.pdf.is_closed:
            return
        with ENGINE_LOCK:
            page = self.pdf[res.index]
            zoom = self._zoom_for(page.rect.width)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        self._img = img
        self._fill_table()

    def _zoom_for(self, width_pt: float) -> float:
        avail = max(300, self.scroll.viewport().width() - 8) if self.isVisible() else 560
        self._zoom = min(self.PREVIEW_DPI / 72.0, avail / max(1.0, width_pt))
        return self._zoom

    def showEvent(self, e) -> None:
        super().showEvent(e)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, lambda: self.isVisible() and self._show_page(self.pages.currentRow()))

    def _fill_table(self) -> None:
        res = self.current_result()
        if res is None:
            return
        zoom = self._zoom
        words = res.words if (not self.only_low.isChecked() or not res.ok) else res.low_confidence_words()
        if not res.ok:
            words = []
        self._words = words
        boxes = []
        for w in res.words:
            r = res.word_rect_display(w)
            boxes.append((QRectF(r.x0 * zoom, r.y0 * zoom, r.width * zoom, r.height * zoom), w.low))
        self._all_words = res.words
        self.preview.set_page(self._img, boxes)
        self._loading = True
        self.table.setRowCount(len(words))
        for i, w in enumerate(words):
            it = QTableWidgetItem(w.text)
            conf = QTableWidgetItem(f"{w.conf:.0f}%")
            conf.setFlags(conf.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if w.low:
                conf.setForeground(QBrush(theme.color("warning")))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, conf)
        self._loading = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._loading or item.column() != 0:
            return
        w = self._words[item.row()]
        w.text = item.text().strip()
        w.conf = 100.0
        res = self.current_result()
        if res is not None and not w.text:
            for ln in res.lines:
                if w in ln.words:
                    ln.words.remove(w)
            res.lines = [ln for ln in res.lines if ln.words]

    def _select_row(self, row: int) -> None:
        if 0 <= row < len(self._words):
            try:
                self.preview.set_current(self._all_words.index(self._words[row]))
            except ValueError:
                pass

    def _select_word(self, i: int) -> None:
        w = self._all_words[i]
        if w not in self._words:
            self.only_low.setChecked(False)
        if w in self._words:
            self.table.setCurrentCell(self._words.index(w), 0)
        self.preview.set_current(i)


# ============================================================ OCR in blocco
class BatchDialog(QDialog):
    """Più file in una volta: modalità, lingue e cartella di destinazione."""

    def __init__(self, parent, settings=None, ask_files=None, ask_dir=None):
        super().__init__(parent)
        self.setWindowTitle(_("OCR in blocco"))
        self.setObjectName("ocrBatchDialog")
        self.resize(640, 560)
        self._ask_files = ask_files
        self._ask_dir = ask_dir
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("File PDF da riconoscere:")))
        self.files = QListWidget(self)
        lay.addWidget(self.files, 1)
        row = QHBoxLayout()
        add = QPushButton(icon("plus"), _("Aggiungi file…"), self)
        add_dir = QPushButton(icon("folder-plus"), _("Aggiungi cartella…"), self)
        rem = QPushButton(icon("minus"), _("Togli"), self)
        row.addWidget(add)
        row.addWidget(add_dir)
        row.addWidget(rem)
        add_dir.clicked.connect(self._add_dir)
        row.addStretch(1)
        lay.addLayout(row)
        form = QFormLayout()
        self.mode = QComboBox(self)
        self.mode.addItem(_("PDF ricercabile"), engine.SEARCHABLE)
        self.mode.addItem(_("PDF modificabile"), engine.EDITABLE)
        form.addRow(_("Risultato:"), self.mode)
        saved = settings.value("ocr/languages", None) if settings is not None else None
        if isinstance(saved, str):
            saved = [s for s in saved.split(",") if s]
        self.languages = LanguageSelector(list(saved) if saved else engine.default_languages(), self)
        self.languages.manageRequested.connect(lambda: (LanguagesDialog(self).exec(),
                                                        self.languages.populate(self.languages.selected())))
        form.addRow(_("Lingue:"), self.languages)
        drow = QHBoxLayout()
        self.out_dir = QLineEdit(self)
        self.out_dir.setPlaceholderText(_("Stessa cartella dell'originale"))
        browse = QPushButton(icon("folder-open"), "", self)
        browse.setToolTip(_("Scegli cartella"))
        drow.addWidget(self.out_dir, 1)
        drow.addWidget(browse)
        form.addRow(_("Destinazione:"), drow)
        self.suffix = QLineEdit("_ocr", self)
        form.addRow(_("Suffisso del nome:"), self.suffix)
        self.format = QComboBox(self)
        self.format.addItem(_("PDF"), "pdf")
        self.format.addItem(_("Testo (TXT)"), "txt")
        form.addRow(_("Formato:"), self.format)
        lay.addLayout(form)
        self.cb_skip = QCheckBox(_("Salta i file già ricercabili"), self)
        self.cb_skip.setChecked(True)
        self.cb_overwrite = QCheckBox(_("Sovrascrivi gli originali (con copia di sicurezza «.bak»)"), self)
        lay.addWidget(self.cb_skip)
        lay.addWidget(self.cb_overwrite)
        self.error = QLabel(self)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Avvia"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        add.clicked.connect(self._add)
        rem.clicked.connect(lambda: [self.files.takeItem(self.files.row(i)) for i in self.files.selectedItems()])
        browse.clicked.connect(self._browse)

    def add_paths(self, paths: list[str]) -> None:
        existing = set(self.paths())
        for p in paths:
            if p not in existing:
                item = QListWidgetItem(icon("file-text"), os.path.basename(p))
                item.setData(Qt.ItemDataRole.UserRole, p)
                item.setToolTip(p)
                self.files.addItem(item)

    def paths(self) -> list[str]:
        return [self.files.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.files.count())]

    def _add(self) -> None:
        if self._ask_files is not None:
            self.add_paths(self._ask_files())

    def _add_dir(self) -> None:
        if self._ask_dir is not None:
            d = self._ask_dir()
            if d:
                from . import batch
                self.add_paths(batch.collect([d]))

    def _browse(self) -> None:
        if self._ask_dir is not None:
            d = self._ask_dir()
            if d:
                self.out_dir.setText(d)

    def accept(self) -> None:
        if not self.paths():
            self.error.setText(_("Aggiungi almeno un file."))
            return
        if not self.languages.selected():
            self.error.setText(_("Scegli almeno una lingua."))
            return
        d = self.out_dir.text().strip()
        if d and not os.path.isdir(d):
            self.error.setText(_("La cartella di destinazione non esiste."))
            return
        if self.cb_overwrite.isChecked() and QMessageBox.question(
                self, _("Sovrascrivi gli originali"),
                _("I file originali saranno sostituiti dal risultato (resta una copia «.bak»). Continuare?")
        ) != QMessageBox.StandardButton.Yes:
            return
        super().accept()


class ReportDialog(QDialog):
    """Rapporto dell'OCR in blocco, esportabile in CSV."""

    def __init__(self, parent, title: str, items: list, save_path_cb=None):
        super().__init__(parent)
        from . import batch
        self.setWindowTitle(title)
        self.resize(720, 420)
        self.items = items
        self._save_path_cb = save_path_cb
        lay = QVBoxLayout(self)
        counts = {k: sum(1 for i in items if i.status == k) for k in (batch.OK, batch.SKIPPED, batch.ERROR)}
        lay.addWidget(QLabel(_("Completati: {o} · saltati: {s} · errori: {e}").format(
            o=counts[batch.OK], s=counts[batch.SKIPPED], e=counts[batch.ERROR])))
        self.table = table = QTableWidget(len(items), 2, self)
        table.setHorizontalHeaderLabels([_("File"), _("Esito")])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        icons = {batch.OK: "circle-check", batch.SKIPPED: "circle-minus", batch.ERROR: "circle-x"}
        for r, it in enumerate(items):
            table.setItem(r, 0, QTableWidgetItem(icon(icons.get(it.status, "circle-x")), os.path.basename(it.path)))
            table.setItem(r, 1, QTableWidgetItem(it.message))
        table.resizeColumnToContents(0)
        lay.addWidget(table)
        row = QHBoxLayout()
        self.btn_csv = QPushButton(icon("file-spreadsheet"), _("Esporta CSV…"), self)
        self.btn_csv.clicked.connect(self.export_csv)
        row.addWidget(self.btn_csv)
        row.addStretch(1)
        b = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        b.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        b.rejected.connect(self.reject)
        row.addWidget(b)
        lay.addLayout(row)

    def export_csv(self) -> None:
        from foliant.core.document import atomic_write

        from . import batch
        if self._save_path_cb is None:
            return
        path = self._save_path_cb(_("Esporta rapporto"), "rapporto-ocr.csv", _("CSV (*.csv)"), ".csv")
        if path:
            atomic_write(path, batch.report_csv(self.items).encode("utf-8"))
