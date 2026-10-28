"""Pulizia guidata del documento (PAG-41): scelta dei controlli, analisi in
background, revisione delle proposte per categoria e applicazione come
un'unica operazione annullabile.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, _, icon

from . import cleanup
from .layout_dialogs import PageScope, _error_label, _muted, _render, _spin

TRANSLATIONS = {
    "Pulizia guidata": "Guided cleanup",
    "Annulla": "Cancel",
    "Controlli da eseguire": "Checks to run",
    "Soglia di inchiostro per le pagine vuote": "Ink threshold for blank pages",
    "Usa il riconoscimento locale dell'orientamento per le scansioni (tesseract)":
        "Use local orientation detection for scans (tesseract)",
    "L'analisi avviene solo su questo computer: nessun dato viene inviato.":
        "The analysis runs only on this computer: no data is sent.",
    "Seleziona almeno un controllo.": "Select at least one check.",
    "Analizza": "Analyse",
    "Analisi del documento…": "Analysing the document…",
    "Revisione delle proposte": "Review proposals",
    "Scegli le correzioni da applicare: verranno eseguite come un'unica operazione annullabile.":
        "Choose the corrections to apply: they will run as a single undoable operation.",
    "{cat} ({n})": "{cat} ({n})",
    "Esporta annotazioni…": "Export annotations…",
    "Annotazioni (JSON)": "Annotations (JSON)",
    "Annotazioni esportate: {n}.": "Annotations exported: {n}.",
    "Applica le correzioni": "Apply corrections",
    "I permessi del documento non consentono modifiche: puoi solo consultare l'analisi.":
        "The document permissions do not allow changes: you can only review the analysis.",
    "Nessuna proposta: il documento è già in ordine.": "No proposals: the document is already tidy.",
    "Impossibile completare la pulizia": "Cannot complete the cleanup",
    "Pagine eliminate: {n}": "Pages deleted: {n}",
    "Pagine raddrizzate: {n}": "Pages straightened: {n}",
    "Pagine uniformate: {n}": "Pages resized: {n}",
    "Annotazioni rimosse: {n}": "Annotations removed: {n}",
    "Pagine da riconoscere con l'OCR: {p}": "Pages to recognise with OCR: {p}",
    "Passare ora alla modalità OCR?": "Switch to OCR mode now?",
    "Riepilogo": "Summary",
    "Nessuna correzione applicata.": "No corrections applied.",
}


class CleanupDialog(QDialog):
    """Scelta dei controlli e dell'ambito."""

    def __init__(self, parent, document, selected: list[int] | None = None, current: int = 0):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(_("Pulizia guidata"))
        self.setWindowIcon(icon("sparkles"))
        with ENGINE_LOCK:
            n = document.pdf.page_count
        root = QVBoxLayout(self)
        box = QGroupBox(_("Controlli da eseguire"))
        lay = QVBoxLayout(box)
        self.checks: dict[str, QCheckBox] = {}
        for key in cleanup.CATEGORIES:
            cb = QCheckBox(_(cleanup.CATEGORY_LABELS[key]))
            cb.setChecked(True)
            cb.toggled.connect(self._validate)
            self.checks[key] = cb
            lay.addWidget(cb)
        root.addWidget(box)
        form = QFormLayout()
        self.ink = _spin(1.0, 0.0, 20.0, 1, "%", 0.1)
        form.addRow(_("Soglia di inchiostro per le pagine vuote"), self.ink)
        root.addLayout(form)
        self.osd = QCheckBox(_("Usa il riconoscimento locale dell'orientamento per le scansioni (tesseract)"))
        self.osd.setEnabled(cleanup.tesseract_available())
        self.osd.setChecked(cleanup.tesseract_available())
        root.addWidget(self.osd)
        self.scope = PageScope(n, list(selected or []), current)
        self.scope.changed.connect(self._validate)
        root.addWidget(self.scope)
        root.addWidget(_muted(_("L'analisi avviene solo su questo computer: nessun dato viene inviato.")))
        self.error_label = _error_label()
        root.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Analizza"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._validate()

    def _validate(self, *_a) -> None:
        err = self.scope.error()
        if err is None and not any(cb.isChecked() for cb in self.checks.values()):
            err = _("Seleziona almeno un controllo.")
        self.error_label.setText(err or "")
        self.error_label.setVisible(bool(err))
        self.ok.setEnabled(err is None)

    def options(self) -> dict:
        try:
            pages = self.scope.pages()
        except FoliantError:
            pages = []
        return {
            "checks": [k for k, cb in self.checks.items() if cb.isChecked()],
            "pages": pages,
            "ink_threshold": self.ink.value() / 100,
            "use_tesseract": self.osd.isChecked(),
        }


class CleanupReviewDialog(QDialog):
    """Proposte per categoria, ognuna accettabile o rifiutabile, con anteprima."""

    def __init__(self, parent, document, results: dict, can_modify: bool = True, export_fn=None):
        super().__init__(parent)
        self.document = document
        self.results = results
        self.setWindowTitle(_("Revisione delle proposte"))
        self.setWindowIcon(icon("sparkles"))
        self.resize(820, 520)
        root = QVBoxLayout(self)
        root.addWidget(_muted(_("Scegli le correzioni da applicare: verranno eseguite come un'unica operazione "
                                "annullabile.")))
        body = QHBoxLayout()
        root.addLayout(body, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(460)
        body.addWidget(self.tree, 1)
        self.thumb = QLabel()
        self.thumb.setFixedSize(240, 320)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.addWidget(self.thumb)
        self.items: list[tuple[QTreeWidgetItem, cleanup.Proposal]] = []
        for cat in cleanup.CATEGORIES:
            props = results.get(cat, [])
            top = QTreeWidgetItem([_("{cat} ({n})").format(cat=_(cleanup.CATEGORY_LABELS[cat]), n=len(props))])
            top.setFlags(top.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            if props:
                top.setFlags(top.flags() | Qt.ItemFlag.ItemIsAutoTristate | Qt.ItemFlag.ItemIsUserCheckable)
            else:
                top.setDisabled(True)
            self.tree.addTopLevelItem(top)
            for p in props:
                it = QTreeWidgetItem([p.reason])
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if p.accepted else Qt.CheckState.Unchecked)
                top.addChild(it)
                self.items.append((it, p))
            top.setExpanded(bool(props))
        self.tree.currentItemChanged.connect(self._show)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Applica le correzioni"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.export_btn = QPushButton(icon("download"), _("Esporta annotazioni…"))
        if export_fn is not None:
            self.export_btn.clicked.connect(export_fn)
        buttons.addButton(self.export_btn, QDialogButtonBox.ButtonRole.ActionRole)
        self.export_btn.setVisible(bool(results.get("annotations")) and export_fn is not None)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if not can_modify:
            self.ok.setEnabled(False)
            note = QLabel(_("I permessi del documento non consentono modifiche: puoi solo consultare l'analisi."))
            note.setWordWrap(True)
            root.addWidget(note)
        root.addWidget(buttons)
        if self.items:
            self.tree.setCurrentItem(self.items[0][0])

    def _show(self, item, _prev=None) -> None:
        prop = next((p for it, p in self.items if it is item), None)
        if prop is None:
            self.thumb.clear()
            return
        img, _size = _render(self.document, prop.page, 300)
        if img is not None:
            self.thumb.setPixmap(QPixmap.fromImage(img).scaled(
                self.thumb.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def set_accepted(self, category: str, accepted: bool) -> None:
        for it, p in self.items:
            if p.category == category:
                it.setCheckState(0, Qt.CheckState.Checked if accepted else Qt.CheckState.Unchecked)

    def proposals(self) -> list[cleanup.Proposal]:
        """Tutte le proposte, con `accepted` aggiornato dalle caselle."""
        out = []
        for it, p in self.items:
            p.accepted = it.checkState(0) == Qt.CheckState.Checked
            out.append(p)
        return out


def _shift(index: int, deleted: list[int]) -> int:
    return index - sum(1 for d in deleted if d < index)


def open_cleanup_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-41: analisi locale, revisione e correzioni in un solo passo annullabile."""
    if view is None:
        return False
    parent = getattr(ctx, "window", None) or view
    dlg = CleanupDialog(parent, view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()
    document = view.document

    def work(progress):
        with ENGINE_LOCK:
            return cleanup.analyze(document.pdf, o["checks"], o["pages"], progress=progress,
                                   ink_threshold=o["ink_threshold"], use_tesseract=o["use_tesseract"])

    try:
        results = ctx.run_task(_("Analisi del documento…"), work)
    except OperationCancelled:
        return False
    except Exception as e:  # noqa: BLE001 - mai chiudere l'applicazione
        ctx.error(_("Impossibile completare la pulizia"), str(e) or type(e).__name__)
        return False
    if not any(results.values()):
        ctx.info(_("Pulizia guidata"), _("Nessuna proposta: il documento è già in ordine."))
        return False
    with ENGINE_LOCK:
        modifiable = cleanup.can_modify(document.pdf)

    def export() -> None:
        path = ctx.ask_save_path(_("Esporta annotazioni…"), "annotazioni.json", _("Annotazioni (JSON)") + " (*.json)",
                                 ".json")
        if not path:
            return
        try:
            with ENGINE_LOCK:
                n = cleanup.export_annotations_json(document.pdf, path, o["pages"])
            ctx.info(_("Pulizia guidata"), _("Annotazioni esportate: {n}.").format(n=n))
        except Exception as e:  # noqa: BLE001
            ctx.error(_("Impossibile completare la pulizia"), str(e) or type(e).__name__)

    review = CleanupReviewDialog(parent, document, results, modifiable, export)
    if review.exec() != QDialog.DialogCode.Accepted:
        return False
    props = review.proposals()
    changes = [p for p in props if p.accepted and p.action != "ocr"]
    ocr_pages = sorted({p.page for p in props if p.accepted and p.action == "ocr"})
    done = {"deleted": 0, "rotated": 0, "resized": 0, "annotations": 0}
    if changes:
        try:
            with document.change(_("Pulizia guidata")) as pdf:
                done = cleanup.apply_proposals(pdf, changes)
        except Exception as e:  # noqa: BLE001
            ctx.error(_("Impossibile completare la pulizia"), str(e) or type(e).__name__)
            return False
    deleted = sorted({p.page for p in changes if p.action == "delete"})
    ocr_pages = [_shift(i, deleted) for i in ocr_pages if i not in deleted]
    lines = []
    for key, text in (("deleted", "Pagine eliminate: {n}"), ("rotated", "Pagine raddrizzate: {n}"),
                      ("resized", "Pagine uniformate: {n}"), ("annotations", "Annotazioni rimosse: {n}")):
        if done.get(key):
            lines.append(_(text).format(n=done[key]))
    if ocr_pages:
        lines.append(_("Pagine da riconoscere con l'OCR: {p}").format(p=", ".join(str(i + 1) for i in ocr_pages)))
        c = getattr(ctx, "pages_controller", None)
        try:
            if c is not None:
                c.organizer.grid.select_pages(ocr_pages)
        except Exception:  # noqa: BLE001 - la selezione è solo un aiuto
            pass
    ctx.info(_("Riepilogo"), "\n".join(lines) or _("Nessuna correzione applicata."))
    if ocr_pages and ctx.mode("ocr") is not None and ctx.confirm(_("Pulizia guidata"),
                                                                 _("Passare ora alla modalità OCR?")):
        ctx.set_mode("ocr")
    return bool(changes)
