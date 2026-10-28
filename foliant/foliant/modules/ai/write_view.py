"""Dialoghi «Scrivi» (AI-19) e anteprima dei segnalibri generati (AI-22).

I dialoghi non inviano nulla da soli: ricevono funzioni (`generate`, `on_insert`,
`on_new_pdf`) dal chiamante, che le esegue nel thread di lavoro o, nei test,
direttamente.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme

from .bookmarks_ai import BNode
from .prompts import LANGUAGE_NAMES_IT


def _muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
    return lab


def write_kinds() -> list[tuple[str, str]]:
    return [("lettera", _("Lettera")), ("e-mail", _("E-mail")), ("paragrafo", _("Paragrafo")),
            ("articolo", _("Articolo")), ("introduzione", _("Introduzione")), ("conclusioni", _("Conclusioni"))]


def write_tones() -> list[tuple[str, str]]:
    return [("formale", _("Formale")), ("professionale", _("Professionale")), ("cordiale", _("Cordiale")),
            ("informale", _("Informale")), ("persuasivo", _("Persuasivo")), ("neutro", _("Neutro"))]


# =================================================================== Scrivi
class WriteDialog(QDialog):
    """Richiesta, testo generato modificabile e azioni (copia, inserisci, nuovo PDF).

    `generate(params) -> str | Answer | None`: se restituisce None il risultato arriverà
    più tardi con `set_result()` o `set_error()` (esecuzione in un thread di lavoro).
    `params`: kind, instructions, tone, length_words, language, use_document.
    """

    def __init__(self, generate: Callable[[dict], object], *, on_insert: Callable[[str], None] | None = None,
                 on_new_pdf: Callable[[str], None] | None = None, has_document: bool = True,
                 language: str = "it", parent=None):
        super().__init__(parent)
        self._generate = generate
        self._on_insert = on_insert
        self._on_new_pdf = on_new_pdf
        self._busy = False
        self.setWindowTitle(_("Scrivi con l'assistente"))
        self.resize(560, 620)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        for key, label in write_kinds():
            self.kind.addItem(label, key)
        form.addRow(_("Tipo di testo"), self.kind)
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(_("Argomento o istruzioni, ad esempio: risposta a un cliente per un "
                                               "ritardo nella consegna"))
        self.instructions.setFixedHeight(90)
        form.addRow(_("Istruzioni"), self.instructions)
        self.tone = QComboBox()
        for key, label in write_tones():
            self.tone.addItem(label, key)
        form.addRow(_("Tono"), self.tone)
        self.length = QSpinBox()
        self.length.setRange(20, 5000)
        self.length.setSingleStep(50)
        self.length.setValue(200)
        self.length.setSuffix(" " + _("parole"))
        form.addRow(_("Lunghezza"), self.length)
        self.language = QComboBox()
        for code, name in LANGUAGE_NAMES_IT.items():
            self.language.addItem(_(name), code)
        idx = self.language.findData(language)
        self.language.setCurrentIndex(max(0, idx))
        form.addRow(_("Lingua"), self.language)
        self.use_document = QCheckBox(_("Usa il documento come fonte"))
        self.use_document.setEnabled(has_document)
        form.addRow("", self.use_document)
        lay.addLayout(form)

        self.generate_button = QPushButton(_("Genera"))
        bind_icon(self.generate_button, "sparkles")
        self.generate_button.clicked.connect(self.run_generate)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.generate_button)
        lay.addLayout(row)

        lay.addWidget(QLabel(_("Testo generato (modificabile)")))
        self.result = QPlainTextEdit()
        lay.addWidget(self.result, 1)
        self.status = _muted("")
        lay.addWidget(self.status)

        actions = QHBoxLayout()
        self.copy_button = QPushButton(_("Copia"))
        bind_icon(self.copy_button, "copy")
        self.copy_button.clicked.connect(self.copy)
        self.insert_button = QPushButton(_("Inserisci nel documento"))
        bind_icon(self.insert_button, "text-cursor-input")
        self.insert_button.clicked.connect(self.insert)
        self.insert_button.setVisible(on_insert is not None)
        self.new_pdf_button = QPushButton(_("Crea nuovo PDF"))
        bind_icon(self.new_pdf_button, "file-plus")
        self.new_pdf_button.clicked.connect(self.new_pdf)
        self.new_pdf_button.setVisible(on_new_pdf is not None)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        for b in (self.copy_button, self.insert_button, self.new_pdf_button):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(close)
        lay.addLayout(actions)

        self.instructions.textChanged.connect(self._update)
        self.result.textChanged.connect(self._update)
        self._update()

    # ------------------------------------------------------------ stato
    def params(self) -> dict:
        return {
            "kind": self.kind.currentData(),
            "instructions": self.instructions.toPlainText().strip(),
            "tone": self.tone.currentData(),
            "length_words": self.length.value(),
            "language": self.language.currentData(),
            "use_document": self.use_document.isChecked() and self.use_document.isEnabled(),
        }

    def text(self) -> str:
        return self.result.toPlainText()

    def _update(self) -> None:
        has_req = bool(self.instructions.toPlainText().strip())
        has_text = bool(self.result.toPlainText().strip())
        self.generate_button.setEnabled(has_req and not self._busy)
        self.generate_button.setText(_("Rigenera") if has_text else _("Genera"))
        for b in (self.copy_button, self.insert_button, self.new_pdf_button):
            b.setEnabled(has_text and not self._busy)

    # ------------------------------------------------------------ azioni
    def run_generate(self) -> None:
        params = self.params()
        if not params["instructions"]:
            return
        self._busy = True
        self.status.setText(_("Generazione in corso…"))
        self._update()
        try:
            out = self._generate(params)
        except Exception as e:  # noqa: BLE001 — errore mostrato nel dialogo
            self.set_error(str(e))
            return
        if out is not None:
            self.set_result(out)

    def append_text(self, chunk: str) -> None:
        """Testo in arrivo durante la generazione."""
        self.result.moveCursor(self.result.textCursor().MoveOperation.End)
        self.result.insertPlainText(chunk)

    def set_result(self, answer) -> None:
        text = getattr(answer, "text", answer)
        flags = list(getattr(answer, "flags", []) or [])
        self._busy = False
        self.result.setPlainText(str(text or ""))
        self.status.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.status.setText(" · ".join(flags))
        self._update()

    def set_error(self, message: str) -> None:
        self._busy = False
        self.status.setText(message)
        self.status.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self._update()

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.text())
        self.status.setText(_("Testo copiato."))

    def insert(self) -> None:
        if self._on_insert and self.text().strip():
            self._on_insert(self.text())

    def new_pdf(self) -> None:
        if self._on_new_pdf and self.text().strip():
            self._on_new_pdf(self.text())


# =================================================================== segnalibri
class BookmarksDialog(QDialog):
    """Anteprima modificabile dei segnalibri proposti; «Sostituisci» o «Unisci».

    Dopo `exec()`: `mode` ("replace" | "merge" | None) e `nodes()` con i titoli modificati.
    Con `on_apply(nodes, mode)` l'applicazione avviene subito alla scelta.
    """

    def __init__(self, nodes: list[BNode], warnings: list[str] | None = None, *, has_existing: bool = True,
                 on_apply: Callable[[list[BNode], str], None] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Segnalibri proposti"))
        self.resize(520, 520)
        self.mode: str | None = None
        self._on_apply = on_apply
        lay = QVBoxLayout(self)
        lay.addWidget(_muted(_("Controlla la proposta: fai doppio clic su un titolo per modificarlo, "
                               "togli il segno di spunta alle voci da escludere.")))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Titolo"), _("Pagina")])
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                  | QAbstractItemView.EditTrigger.EditKeyPressed)
        lay.addWidget(self.tree, 1)
        self._fill(self.tree.invisibleRootItem(), nodes)
        self.tree.expandAll()
        self.tree.resizeColumnToContents(1)
        if warnings:
            lay.addWidget(_muted("\n".join(warnings)))
        buttons = QDialogButtonBox()
        self.replace_button = buttons.addButton(_("Sostituisci"), QDialogButtonBox.ButtonRole.AcceptRole)
        bind_icon(self.replace_button, "replace")
        self.merge_button = buttons.addButton(_("Unisci"), QDialogButtonBox.ButtonRole.AcceptRole)
        bind_icon(self.merge_button, "merge")
        self.merge_button.setEnabled(has_existing)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.replace_button.clicked.connect(lambda: self.choose("replace"))
        self.merge_button.clicked.connect(lambda: self.choose("merge"))
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        enabled = bool(nodes)
        self.replace_button.setEnabled(enabled)
        self.merge_button.setEnabled(enabled and has_existing)

    def _fill(self, parent: QTreeWidgetItem, nodes: list[BNode]) -> None:
        for n in nodes:
            item = QTreeWidgetItem(parent, [n.title, str(n.page + 1)])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, (n.page, n.y))
            self._fill(item, n.children)

    def _collect(self, item: QTreeWidgetItem) -> list[BNode]:
        out: list[BNode] = []
        for i in range(item.childCount()):
            ch = item.child(i)
            kids = self._collect(ch)
            if ch.checkState(0) != Qt.CheckState.Checked:
                out.extend(kids)
                continue
            page, y = ch.data(0, Qt.ItemDataRole.UserRole)
            title = ch.text(0).strip()
            if not title:
                out.extend(kids)
                continue
            out.append(BNode(title, page, y, kids))
        return out

    def nodes(self) -> list[BNode]:
        return self._collect(self.tree.invisibleRootItem())

    def choose(self, mode: str) -> None:
        self.mode = mode
        if self._on_apply is not None:
            self._on_apply(self.nodes(), mode)
        self.accept()
