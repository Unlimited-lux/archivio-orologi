"""Finestre della cifratura con certificato (SEC-09)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import _, icon

from . import certenc as CE
from . import protection as P
from .dialogs import PasswordField, _note

def ask_pkcs12_password(parent, path: str) -> str | None:
    pw, ok = QInputDialog.getText(parent, _("File PKCS#12"), _("Password di «{n}»:").format(n=path.rsplit("/", 1)[-1]),
                                  QLineEdit.EchoMode.Password)
    return pw if ok else None


def confirm_problem(parent, name: str, problems: list[str]) -> bool:
    box = QMessageBox(QMessageBox.Icon.Warning, _("Certificato non adatto"),
                      _("«{n}»:").format(n=name) + "\n" + "\n".join(problems) + "\n\n" +
                      _("Aggiungerlo comunque?"), parent=parent)
    yes = box.addButton(_("Aggiungi comunque"), QMessageBox.ButtonRole.AcceptRole)
    box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


class CertEncryptDialog(QDialog):
    """Destinatari, permessi di ciascuno e cifratura dei metadati."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.recipients: list[CE.Recipient] = []
        self.setWindowTitle(_("Cifra con certificato"))
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(_note(_("Solo i destinatari scelti potranno aprire il documento, ciascuno con la propria "
                              "chiave privata e con i permessi indicati. Cifratura AES a 256 bit.")))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([_("Destinatario"), _("Emittente"), _("Scadenza"), _("Permessi")])
        self.tree.setRootIsDecorated(False)
        head = self.tree.header()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.itemSelectionChanged.connect(self._load_selected)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        add = QPushButton(_("Aggiungi certificato…"))
        add.setIcon(icon("plus"))
        add.clicked.connect(self._add)
        self.remove_button = QPushButton(_("Rimuovi"))
        self.remove_button.clicked.connect(self.remove_selected)
        row.addWidget(add)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        lay.addLayout(row)
        self.perms_box = QGroupBox(_("Permessi del destinatario selezionato"))
        pl = QVBoxLayout(self.perms_box)
        self.mine = QCheckBox(_("È il mio certificato"))
        pl.addWidget(self.mine)
        form = QFormLayout()
        self.print_combo = QComboBox()
        for key, label in P.print_labels().items():
            self.print_combo.addItem(label, key)
        form.addRow(_("Stampa:"), self.print_combo)
        pl.addLayout(form)
        grid = QGridLayout()
        self.perm_boxes: dict[str, QCheckBox] = {}
        for n, (key, label) in enumerate(P.permission_labels().items()):
            cb = QCheckBox(label)
            self.perm_boxes[key] = cb
            grid.addWidget(cb, n // 2, n % 2)
        pl.addLayout(grid)
        lay.addWidget(self.perms_box)
        for w in (self.mine, *self.perm_boxes.values()):
            w.toggled.connect(self._store_selected)
        self.print_combo.currentIndexChanged.connect(self._store_selected)
        self.encrypt_metadata = QCheckBox(_("Cifra anche i metadati"))
        self.encrypt_metadata.setChecked(True)
        lay.addWidget(self.encrypt_metadata)
        self.error = _note("", "danger")
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Cifra e salva…"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._loading = False
        self._load_selected()

    # ---------------------------------------------------------- destinatari
    def add_recipient(self, cert, has_key: bool = False) -> CE.Recipient | None:
        problems = CE.cert_problems(cert)
        if problems and not confirm_problem(self, CE.cert_name(cert), problems):
            return None
        fingerprint = cert.sha256
        if any(r.cert.sha256 == fingerprint for r in self.recipients):
            self.error.setText(_("«{n}» è già tra i destinatari.").format(n=CE.cert_name(cert)))
            return None
        r = CE.Recipient(cert, mine=has_key)
        self.recipients.append(r)
        self._reload(select=len(self.recipients) - 1)
        self.error.setText("")
        return r

    def add_file(self, path: str, password: str | None = None) -> CE.Recipient | None:
        if path.lower().endswith((".p12", ".pfx")) and password is None:
            password = ask_pkcs12_password(self, path)
            if password is None:
                return None
        try:
            cert, has_key = CE.load_certificate(path, password)
        except CE.CertError as e:
            self.error.setText(str(e))
            return None
        return self.add_recipient(cert, has_key)

    def _add(self) -> None:
        paths, _f = QFileDialog.getOpenFileNames(self, _("Aggiungi certificato"), "", _("Certificati (*.pem *.crt *.cer *.der *.p12 *.pfx);;Tutti i file (*)"))
        for path in paths:
            self.add_file(path)

    def remove_selected(self) -> None:
        i = self._index()
        if i is not None:
            del self.recipients[i]
            self._reload(select=min(i, len(self.recipients) - 1))

    def _index(self) -> int | None:
        item = self.tree.currentItem()
        return self.tree.indexOfTopLevelItem(item) if item is not None else None

    def _summary(self, r: CE.Recipient) -> str:
        if CE.is_full(r):
            return _("Tutti")
        labels = P.permission_labels()
        allowed = [labels[k] for k in P.PERM_BITS if k in r.allowed and k != "accessibility"]
        prints = P.print_labels()[r.print_level]
        return _("Stampa: {p}").format(p=prints.lower()) + ("; " + ", ".join(a.lower() for a in allowed)
                                                            if allowed else "")

    def _reload(self, select: int | None = None) -> None:
        self.tree.clear()
        for r in self.recipients:
            expiry = r.cert["tbs_certificate"]["validity"]["not_after"].native.strftime("%d/%m/%Y")
            name = r.name + (" " + _("(io)") if r.mine else "")
            item = QTreeWidgetItem(self.tree, [name, CE.cert_issuer(r.cert), expiry, self._summary(r)])
            item.setToolTip(3, item.text(3))
        if select is not None and 0 <= select < len(self.recipients):
            self.tree.setCurrentItem(self.tree.topLevelItem(select))
        self._load_selected()

    def _load_selected(self) -> None:
        i = self._index()
        on = i is not None
        self.perms_box.setEnabled(on)
        self.remove_button.setEnabled(on)
        if not on:
            return
        r = self.recipients[i]
        self._loading = True
        self.mine.setChecked(r.mine)
        self.print_combo.setCurrentIndex(max(0, self.print_combo.findData(r.print_level)))
        for key, cb in self.perm_boxes.items():
            cb.setChecked(key in r.allowed)
        self.perm_boxes["accessibility"].setChecked(True)
        self.perm_boxes["accessibility"].setEnabled(False)     # sempre consentita con AES-256
        self._loading = False

    def _store_selected(self, *_a) -> None:
        i = self._index()
        if self._loading or i is None:
            return
        r = self.recipients[i]
        r.mine = self.mine.isChecked()
        r.print_level = self.print_combo.currentData()
        r.allowed = {k for k, cb in self.perm_boxes.items() if cb.isChecked()}
        item = self.tree.topLevelItem(i)
        item.setText(0, r.name + (" " + _("(io)") if r.mine else ""))
        item.setText(3, self._summary(r))
        item.setToolTip(3, item.text(3))

    def _accept(self) -> None:
        if not self.recipients:
            self.error.setText(_("Aggiungi almeno un destinatario."))
            return
        self.accept()


class CertOpenDialog(QDialog):
    """Certificato con chiave privata (PKCS#12) per aprire un documento cifrato."""

    def __init__(self, parent=None, expected: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Apri con certificato"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        if expected:
            lay.addWidget(_note(_("Destinatari previsti (emittente — numero di serie):") + "\n" +
                                "\n".join("• " + e for e in expected)))
        form = QFormLayout()
        row = QHBoxLayout()
        self.path = QLineEdit()
        browse = QPushButton(_("Sfoglia…"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        form.addRow(_("File PKCS#12:"), row)
        self.password = PasswordField()
        form.addRow(_("Password o PIN:"), self.password)
        lay.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Apri"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Certificato con chiave privata"), "",
                                               _("PKCS#12 (*.p12 *.pfx);;Tutti i file (*)"))
        if path:
            self.path.setText(path)
