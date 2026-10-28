"""Trova pagine vuote e duplicate (PAG-40): opzioni, analisi in background,
risultati con motivazione e anteprime affiancate, poi selezione
nell'organizzatore o eliminazione in un unico passo annullabile.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, _, icon

from . import detect
from .engine import delete_pages
from .layout_dialogs import PageScope, _error_label, _muted, _render, _spin

TRANSLATIONS = {
    "Trova pagine vuote e duplicate…": "Find blank and duplicate pages…",
    "Ricerca di pagine vuote e duplicate…": "Searching for blank and duplicate pages…",
    "Pagine vuote e duplicate": "Blank and duplicate pages",
    "Nessuna pagina vuota o duplicata trovata.": "No blank or duplicate page found.",
    "Pagina {n}: duplicato della pagina {o}": "Page {n}: duplicate of page {o}",
    "Elimina pagine vuote e duplicate": "Delete blank and duplicate pages",
    "Elimina": "Delete",
    "Annulla": "Cancel",
    "Cerca pagine vuote": "Find blank pages",
    "Soglia di inchiostro": "Ink threshold",
    "Ignora le annotazioni": "Ignore annotations",
    "Ignora intestazioni e numeri di pagina": "Ignore headers and page numbers",
    "Il testo invisibile (OCR) rende la pagina non vuota": "Invisible (OCR) text makes the page non-blank",
    "Cerca pagine duplicate": "Find duplicate pages",
    "Somiglianza": "Similarity",
    "Identiche": "Identical",
    "Quasi identiche": "Nearly identical",
    "Soglia di somiglianza": "Similarity threshold",
    "Confronto": "Comparison",
    "Con tutto il documento": "With the whole document",
    "Solo pagine adiacenti": "Adjacent pages only",
    "Cerca": "Search",
    "Scegli almeno un tipo di ricerca.": "Choose at least one kind of search.",
    "Pagina {n}: vuota (inchiostro {p}%)": "Page {n}: blank (ink {p}%)",
    "Pagina {n}: non valida (non renderizzabile o di dimensione nulla)":
        "Page {n}: invalid (cannot be rendered or zero size)",
    "Risultati: {n} pagine": "Results: {n} pages",
    "Seleziona nell'organizzatore": "Select in the organizer",
    "Elimina le pagine spuntate": "Delete the checked pages",
    "Originale": "Original",
    "Pagina {n}": "Page {n}",
    "Non si possono eliminare tutte le pagine del documento.": "All pages of the document cannot be deleted.",
    "Pagine eliminate: {n}.": "Pages deleted: {n}.",
}


class DetectDialog(QDialog):
    """Opzioni della ricerca."""

    def __init__(self, parent, document, selected: list[int] | None = None, current: int = 0):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(_("Pagine vuote e duplicate"))
        self.setWindowIcon(icon("scan-search"))
        with ENGINE_LOCK:
            n = document.pdf.page_count
        root = QVBoxLayout(self)
        self.blank_box = QGroupBox(_("Cerca pagine vuote"))
        self.blank_box.setCheckable(True)
        self.blank_box.setChecked(True)
        bf = QFormLayout(self.blank_box)
        self.ink = _spin(1.0, 0.0, 20.0, 1, "%", 0.1)
        bf.addRow(_("Soglia di inchiostro"), self.ink)
        self.ignore_annots = QCheckBox(_("Ignora le annotazioni"))
        self.ignore_annots.setChecked(True)
        self.ignore_artifacts = QCheckBox(_("Ignora intestazioni e numeri di pagina"))
        self.ignore_artifacts.setChecked(True)
        self.ocr_text = QCheckBox(_("Il testo invisibile (OCR) rende la pagina non vuota"))
        for w in (self.ignore_annots, self.ignore_artifacts, self.ocr_text):
            bf.addRow(w)
        root.addWidget(self.blank_box)
        self.dup_box = QGroupBox(_("Cerca pagine duplicate"))
        self.dup_box.setCheckable(True)
        self.dup_box.setChecked(True)
        df = QFormLayout(self.dup_box)
        self.mode = QComboBox()
        self.mode.addItem(_("Identiche"), "identical")
        self.mode.addItem(_("Quasi identiche"), "similar")
        self.similarity = _spin(95, 50, 100, 0, "%")
        self.scope_all = QRadioButton(_("Con tutto il documento"))
        self.scope_adj = QRadioButton(_("Solo pagine adiacenti"))
        self.scope_all.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.scope_all)
        grp.addButton(self.scope_adj)
        sc = QWidget()
        sl = QHBoxLayout(sc)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self.scope_all)
        sl.addWidget(self.scope_adj)
        df.addRow(_("Somiglianza"), self.mode)
        df.addRow(_("Soglia di somiglianza"), self.similarity)
        df.addRow(_("Confronto"), sc)
        root.addWidget(self.dup_box)
        self.scope = PageScope(n, list(selected or []), current)
        root.addWidget(self.scope)
        self.error_label = _error_label()
        root.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Cerca"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        for sig in (self.blank_box.toggled, self.dup_box.toggled, self.scope.changed, self.mode.currentIndexChanged):
            sig.connect(self.validate)
        self.validate()

    def validate(self, *_a) -> None:
        self.similarity.setEnabled(self.mode.currentData() == "similar")
        err = self.scope.error()
        if err is None and not (self.blank_box.isChecked() or self.dup_box.isChecked()):
            err = _("Scegli almeno un tipo di ricerca.")
        self.error_label.setText(err or "")
        self.error_label.setVisible(bool(err))
        self.ok.setEnabled(err is None)

    def options(self) -> dict:
        try:
            pages = self.scope.pages()
        except FoliantError:
            pages = []
        return {
            "pages": pages,
            "blank": self.blank_box.isChecked(),
            "ink_threshold": self.ink.value() / 100,
            "ignore_annots": self.ignore_annots.isChecked(),
            "ignore_pagination_artifacts": self.ignore_artifacts.isChecked(),
            "ocr_text_counts": self.ocr_text.isChecked(),
            "duplicates": self.dup_box.isChecked(),
            "mode": self.mode.currentData(),
            "threshold": self.similarity.value() / 100,
            "scope": "adjacent" if self.scope_adj.isChecked() else "all",
        }


def run_detection(pdf, o: dict, progress=None) -> list[dict]:
    """Esegue la ricerca (tenere ENGINE_LOCK): risultati ordinati per pagina."""
    found: dict[int, dict] = {}
    if o["blank"]:
        details: dict = {}
        for i in detect.find_blank_pages(pdf, o["pages"], o["ink_threshold"], o["ignore_annots"],
                                         o["ignore_pagination_artifacts"], progress=progress,
                                         ocr_text_counts=o["ocr_text_counts"], details=details):
            info = details[i]
            reason = (_("Pagina {n}: non valida (non renderizzabile o di dimensione nulla)").format(n=i + 1)
                      if info.invalid else _("Pagina {n}: vuota (inchiostro {p}%)").format(
                          n=i + 1, p=f"{info.ink * 100:.2f}".replace(".", ",")))
            found[i] = {"page": i, "kind": "blank", "original": None, "reason": reason}
    if o["duplicates"]:
        for dup, orig in detect.find_duplicate_pages(pdf, o["pages"], mode=o["mode"], scope=o["scope"],
                                                     progress=progress, threshold=o["threshold"]):
            if dup not in found:
                found[dup] = {"page": dup, "kind": "duplicate", "original": orig,
                              "reason": _("Pagina {n}: duplicato della pagina {o}").format(n=dup + 1, o=orig + 1)}
    return [found[k] for k in sorted(found)]


class DetectResultsDialog(QDialog):
    """Risultati spuntabili con anteprime affiancate (pagina e originale)."""

    SELECT, DELETE = "select", "delete"

    def __init__(self, parent, document, results: list[dict]):
        super().__init__(parent)
        self.document = document
        self.results = results
        self.action = self.SELECT
        self.setWindowTitle(_("Pagine vuote e duplicate"))
        self.setWindowIcon(icon("scan-search"))
        self.resize(780, 480)
        root = QVBoxLayout(self)
        root.addWidget(_muted(_("Risultati: {n} pagine").format(n=len(results))))
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self.list = QListWidget()
        self.list.setMinimumWidth(320)
        for r in results:
            it = QListWidgetItem(r["reason"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        body.addWidget(self.list, 1)
        thumbs = QHBoxLayout()
        self.thumb_page, self.cap_page = QLabel(), QLabel()
        self.thumb_orig, self.cap_orig = QLabel(), QLabel()
        for img, cap in ((self.thumb_page, self.cap_page), (self.thumb_orig, self.cap_orig)):
            col = QVBoxLayout()
            img.setFixedSize(190, 260)
            img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(img)
            col.addWidget(cap)
            col.addStretch(1)
            thumbs.addLayout(col)
        body.addLayout(thumbs)
        self.list.currentRowChanged.connect(self._show)
        buttons = QDialogButtonBox()
        self.select_btn = QPushButton(icon("mouse-pointer-click"), _("Seleziona nell'organizzatore"))
        self.delete_btn = QPushButton(icon("trash-2"), _("Elimina le pagine spuntate"))
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText(_("Annulla"))
        buttons.addButton(self.select_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.delete_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        self.select_btn.clicked.connect(lambda: self._finish(self.SELECT))
        self.delete_btn.clicked.connect(lambda: self._finish(self.DELETE))
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.list.itemChanged.connect(self._update_buttons)
        if results:
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _finish(self, action: str) -> None:
        self.action = action
        self.accept()

    def _update_buttons(self, *_a) -> None:
        n = len(self.chosen())
        self.select_btn.setEnabled(n > 0)
        self.delete_btn.setEnabled(n > 0)

    def _set_thumb(self, label: QLabel, caption: QLabel, index: int | None, text: str = "") -> None:
        if index is None:
            label.clear()
            caption.setText("")
            return
        img, _s = _render(self.document, index, 260)
        if img is not None:
            label.setPixmap(QPixmap.fromImage(img).scaled(label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
        caption.setText(text)

    def _show(self, row: int) -> None:
        if not 0 <= row < len(self.results):
            return
        r = self.results[row]
        self._set_thumb(self.thumb_page, self.cap_page, r["page"], _("Pagina {n}").format(n=r["page"] + 1))
        orig = r["original"]
        self._set_thumb(self.thumb_orig, self.cap_orig, orig,
                        "" if orig is None else _("Originale") + " — " + _("Pagina {n}").format(n=orig + 1))

    def set_checked(self, pages: list[int]) -> None:
        for k, r in enumerate(self.results):
            self.list.item(k).setCheckState(Qt.CheckState.Checked if r["page"] in pages else Qt.CheckState.Unchecked)

    def chosen(self) -> list[int]:
        return [r["page"] for k, r in enumerate(self.results)
                if self.list.item(k).checkState() == Qt.CheckState.Checked]


def open_detect_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-40. Restituisce True se il documento è cambiato (pagine eliminate)."""
    if view is None:
        return False
    parent = getattr(ctx, "window", None) or view
    dlg = DetectDialog(parent, view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()
    document = view.document

    def work(progress):
        with ENGINE_LOCK:
            return run_detection(document.pdf, o, progress)

    try:
        results = ctx.run_task(_("Ricerca di pagine vuote e duplicate…"), work)
    except OperationCancelled:
        return False
    except Exception as e:  # noqa: BLE001
        ctx.error(_("Pagine vuote e duplicate"), str(e) or type(e).__name__)
        return False
    if not results:
        ctx.info(_("Pagine vuote e duplicate"), _("Nessuna pagina vuota o duplicata trovata."))
        return False
    res = DetectResultsDialog(parent, document, results)
    if res.exec() != QDialog.DialogCode.Accepted:
        return False
    pages = res.chosen()
    if not pages:
        return False
    c = getattr(ctx, "pages_controller", None)
    if res.action == DetectResultsDialog.SELECT:
        try:
            if c is not None:
                ctx.set_mode("pagine")
                c.organizer.grid.select_pages(pages)
        except Exception:  # noqa: BLE001 - la selezione è un aiuto
            pass
        return False
    if len(pages) >= document.page_count:
        ctx.error(_("Pagine vuote e duplicate"), _("Non si possono eliminare tutte le pagine del documento."))
        return False
    try:
        with document.change(_("Elimina pagine vuote e duplicate")) as pdf:
            n = delete_pages(pdf, pages)
    except Exception as e:  # noqa: BLE001
        ctx.error(_("Pagine vuote e duplicate"), str(e) or type(e).__name__)
        return False
    try:
        ctx.status(_("Pagine eliminate: {n}.").format(n=n))
    except Exception:  # noqa: BLE001
        pass
    return True
