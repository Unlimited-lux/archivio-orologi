"""Finestre dello spazio protetto (SEC-31)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QRadioButton, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import _, icon

from . import vault as V
from .dialogs import PasswordField, StrengthBar, _note


class SetupDialog(QDialog):
    """Prima configurazione: passphrase e chiave di recupero da conservare."""

    def __init__(self, vault: V.Vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        self.recovery = ""
        self.setWindowTitle(_("Crea lo spazio protetto"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(_note(_("I documenti dello spazio protetto sono cifrati sul disco e si aprono solo dopo "
                              "lo sblocco con la passphrase. Se dimentichi la passphrase e perdi la chiave di "
                              "recupero, i documenti non sono più recuperabili."), "text"))
        form = QFormLayout()
        self.pw, self.pw2 = PasswordField(), PasswordField()
        self.strength = StrengthBar()
        self.pw.changed.connect(lambda: self.strength.set_password(self.pw.text()))
        form.addRow(_("Passphrase:"), self.pw)
        form.addRow(_("Conferma:"), self.pw2)
        form.addRow("", self.strength)
        lay.addLayout(form)
        self.key_box = QLineEdit()
        self.key_box.setReadOnly(True)
        self.key_box.setVisible(False)
        self.copy = QPushButton(_("Copia"))
        self.copy.setIcon(icon("copy"))
        self.copy.setVisible(False)
        self.copy.clicked.connect(lambda: QApplication.clipboard().setText(self.recovery))
        row = QHBoxLayout()
        row.addWidget(self.key_box, 1)
        row.addWidget(self.copy)
        self.key_label = _note(_("Chiave di recupero: conservala in un luogo sicuro, separato dal computer. "
                                 "Non verrà più mostrata."), "warning")
        self.key_label.setVisible(False)
        lay.addWidget(self.key_label)
        lay.addLayout(row)
        self.saved = QCheckBox(_("Ho conservato la chiave di recupero"))
        self.saved.setVisible(False)
        self.saved.toggled.connect(lambda on: self.ok.setEnabled(on))
        lay.addWidget(self.saved)
        self.error = _note("", "danger")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(_("Crea"))
        self.cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        self.cancel.setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def reject(self) -> None:
        if not self.recovery:                 # dopo la creazione si esce solo con «Fine»
            super().reject()

    def problem(self) -> str:
        if len(self.pw.text()) < 8:
            return _("La passphrase deve avere almeno 8 caratteri.")
        if self.pw.text() != self.pw2.text():
            return _("Le password non coincidono")
        return ""

    def create(self) -> bool:
        """Crea lo spazio e mostra la chiave di recupero."""
        problem = self.problem()
        self.error.setText(problem)
        if problem:
            return False
        try:
            self.recovery = self.vault.create(self.pw.text())
        except (V.VaultError, OSError) as e:
            self.error.setText(str(e))
            return False
        for w in (self.pw, self.pw2):
            w.setEnabled(False)
        self.key_box.setText(self.recovery)
        for w in (self.key_box, self.copy, self.key_label, self.saved):
            w.setVisible(True)
        self.ok.setText(_("Fine"))
        self.ok.setEnabled(False)
        self.cancel.setEnabled(False)
        return True

    def _accept(self) -> None:
        if not self.recovery:
            self.create()
        elif self.saved.isChecked():
            self.accept()


class UnlockDialog(QDialog):
    def __init__(self, vault: V.Vault, parent=None):
        super().__init__(parent)
        self.vault = vault
        self.recovered = False
        self.setWindowTitle(_("Sblocca lo spazio protetto"))
        lay = QVBoxLayout(self)
        self.use_pass = QRadioButton(_("Passphrase"))
        self.use_key = QRadioButton(_("Chiave di recupero"))
        self.use_pass.setChecked(True)
        row = QHBoxLayout()
        row.addWidget(self.use_pass)
        row.addWidget(self.use_key)
        row.addStretch(1)
        lay.addLayout(row)
        self.secret = PasswordField()
        lay.addWidget(self.secret)
        self.error = _note("", "danger")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Sblocca"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def try_unlock(self) -> bool:
        try:
            if self.use_key.isChecked():
                self.vault.unlock_with_recovery(self.secret.text())
                self.recovered = True
            else:
                self.vault.unlock(self.secret.text())
        except V.VaultError as e:
            self.error.setText(str(e))
            self.secret.setText("")
            return False
        return True

    def _accept(self) -> None:
        if self.try_unlock():
            self.accept()


class VaultDialog(QDialog):
    """Elenco dei documenti dello spazio protetto e comandi."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle(_("Spazio protetto"))
        self.resize(620, 400)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Documento"), _("Dimensione"), _("Modificato")])
        self.tree.setRootIsDecorated(False)
        head = self.tree.header()
        head.setStretchLastSection(False)
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda *_a: self.open_selected())
        self.tree.itemSelectionChanged.connect(self._update)
        left.addWidget(self.tree, 1)
        auto = QHBoxLayout()
        auto.addWidget(QLabel(_("Blocca dopo un'inattività di:")))
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 240)
        self.minutes.setSuffix(_(" min"))
        self.minutes.setSpecialValueText(_("mai"))
        self.minutes.setValue(self.c.vault_minutes())
        self.minutes.valueChanged.connect(self.c.set_vault_minutes)
        auto.addWidget(self.minutes)
        auto.addStretch(1)
        left.addLayout(auto)
        self.notice = _note("")
        left.addWidget(self.notice)
        lay.addLayout(left, 1)
        col = QVBoxLayout()
        self.open_button = QPushButton(_("Apri"))
        self.open_button.clicked.connect(self.open_selected)
        add = QPushButton(_("Aggiungi file…"))
        add.clicked.connect(self._add)
        self.move_button = QPushButton(_("Sposta qui il documento aperto"))
        self.move_button.clicked.connect(self._move)
        self.move_button.setEnabled(self.c.can_move_to_vault())
        self.export_button = QPushButton(_("Esporta…"))
        self.export_button.clicked.connect(self._export)
        self.remove_button = QPushButton(_("Elimina"))
        self.remove_button.clicked.connect(self._remove)
        lock = QPushButton(_("Blocca ora"))
        lock.setIcon(icon("lock"))
        lock.clicked.connect(self._lock)
        for w in (self.open_button, add, self.move_button, self.export_button, self.remove_button):
            col.addWidget(w)
        col.addStretch(1)
        col.addWidget(lock)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        col.addWidget(close)
        lay.addLayout(col)
        self.reload()

    def reload(self) -> None:
        self.tree.clear()
        for e in self.c.vault().entries():
            item = QTreeWidgetItem(self.tree, [e.name, _human(e.size), e.modified[:16].replace("T", " ")])
            item.setData(0, Qt.ItemDataRole.UserRole, e.id)
        if self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.notice.setText("\n".join(self.c.vault().notices) or
                            ("" if self.tree.topLevelItemCount() else _("Lo spazio protetto è vuoto.")))
        self._update()

    def current(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None

    def _update(self) -> None:
        on = self.current() is not None
        for b in (self.open_button, self.export_button, self.remove_button):
            b.setEnabled(on)

    def open_selected(self) -> None:
        if self.current() and self.c.open_from_vault(self.current()):
            self.accept()

    def _add(self) -> None:
        self.c.vault_add_files()
        self.reload()

    def _move(self) -> None:
        if self.c.move_to_vault():
            self.move_button.setEnabled(False)
            self.reload()

    def _export(self) -> None:
        self.c.vault_export(self.current())

    def _remove(self) -> None:
        if self.c.vault_remove(self.current()):
            self.reload()

    def _lock(self) -> None:
        self.c.lock_vault()
        self.reject()


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"
