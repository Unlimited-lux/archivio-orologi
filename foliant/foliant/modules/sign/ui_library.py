"""Dialogo «Gestisci libreria» delle firme e iniziali (SIG-04)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout,
)

from foliant.api import _, bind_icon

from .library import LibraryError, SignatureLibrary
from .ui_create import CreateSignatureDialog, item_pixmap

THUMB = QSize(180, 48)


def ask_password(parent, title: str, text: str, confirm: bool = False) -> str | None:
    pw, ok = QInputDialog.getText(parent, title, text, QLineEdit.EchoMode.Password)
    if not ok:
        return None
    if confirm:
        pw2, ok = QInputDialog.getText(parent, title, _("Ripeti la password:"), QLineEdit.EchoMode.Password)
        if not ok:
            return None
        if pw2 != pw:
            QMessageBox.warning(parent, title, _("Le due password non coincidono."))
            return None
    return pw


class LibraryDialog(QDialog):
    def __init__(self, library: SignatureLibrary, parent=None, default_name: str = ""):
        super().__init__(parent)
        self.lib = library
        self.default_name = default_name
        self.setWindowTitle(_("Libreria firme"))
        self.resize(560, 440)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setIconSize(THUMB)
        self.list.setAccessibleName(_("Elementi della libreria"))
        left.addWidget(self.list, 1)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        left.addWidget(self.notice)
        lay.addLayout(left, 1)
        col = QVBoxLayout()
        self.buttons = {}
        for key, text, icon_name in [
            ("new", _("Crea…"), "plus"), ("rename", _("Rinomina…"), "pencil"), ("up", _("Sposta su"), "move-up"),
            ("down", _("Sposta giù"), "move-down"), ("default", _("Imposta predefinito"), "check"),
            ("duplicate", _("Duplica"), "copy"), ("remove", _("Elimina…"), "trash-2"),
            ("protect", _("Proteggi la libreria…"), "lock"),
        ]:
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.clicked.connect(lambda _c=False, k=key: self._do(k))
            col.addWidget(b)
            self.buttons[key] = b
        col.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        col.addWidget(box)
        lay.addLayout(col)
        self.list.currentRowChanged.connect(lambda _r: self._update_buttons())
        self.refresh()

    def current_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def refresh(self, select: str | None = None) -> None:
        sel = select or self.current_id()
        self.list.clear()
        groups = [("firma", _("Firme")), ("iniziali", _("Iniziali"))]
        for kind, title in groups:
            header = QListWidgetItem(title)
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            f = header.font()
            f.setBold(True)
            header.setFont(f)
            self.list.addItem(header)
            for e in self.lib.entries(kind):
                label = e.name + ("  ★" if e.default else "")
                it = QListWidgetItem(label)
                it.setData(Qt.ItemDataRole.UserRole, e.id)
                if e.available:
                    try:
                        it.setIcon(item_pixmap(self.lib.load_item(e.id), THUMB))
                    except Exception:
                        pass
                else:
                    it.setText(_("{name} — non disponibile").format(name=e.name))
                it.setToolTip(_("{name}: {origin}, creata il {date}").format(
                    name=e.name, origin=_(e.origin), date=e.created[:10]))
                self.list.addItem(it)
                if e.id == sel:
                    self.list.setCurrentItem(it)
        self.notice.setText(self.lib.notice)
        self.buttons["protect"].setText(_("Rimuovi protezione") if self.lib.protected else
                                        _("Proteggi la libreria…"))
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = self.current_id() is not None
        for k in ("rename", "up", "down", "default", "duplicate", "remove"):
            self.buttons[k].setEnabled(has)

    def _do(self, key: str) -> None:
        eid = self.current_id()
        try:
            if key == "new":
                dlg = CreateSignatureDialog(self, default_name=self.default_name)
                dlg.save_check.setChecked(True)
                dlg.save_check.setEnabled(False)
                if dlg.exec() and dlg.item is not None:
                    e = self.lib.add(dlg.item, dlg.item.name or None)
                    self.refresh(e.id)
                return
            if key == "protect":
                if self.lib.protected:
                    self.lib.unprotect()
                else:
                    pw = ask_password(self, _("Proteggi la libreria"),
                                      _("Password per cifrare la libreria delle firme:"), confirm=True)
                    if pw:
                        self.lib.protect(pw)
                self.refresh()
                return
            if eid is None:
                return
            e = self.lib.entry(eid)
            if key == "rename":
                name, ok = QInputDialog.getText(self, _("Rinomina"), _("Nuovo nome:"), text=e.name)
                if ok:
                    self.lib.rename(eid, name)
            elif key == "up":
                self.lib.move(eid, -1)
            elif key == "down":
                self.lib.move(eid, 1)
            elif key == "default":
                self.lib.set_default(eid)
            elif key == "duplicate":
                eid = self.lib.duplicate(eid).id
            elif key == "remove":
                if QMessageBox.question(self, _("Elimina"), _("Eliminare «{name}» dalla libreria?").format(
                        name=e.name)) != QMessageBox.StandardButton.Yes:
                    return
                self.lib.remove(eid)
                eid = None
            self.refresh(eid)
        except LibraryError as ex:
            QMessageBox.warning(self, _("Libreria firme"), str(ex))


TRANSLATIONS_EN = {
    "Ripeti la password:": "Repeat the password:",
    "Le due password non coincidono.": "The two passwords do not match.",
    "Libreria firme": "Signature library", "Elementi della libreria": "Library items",
    "Crea…": "Create…", "Rinomina…": "Rename…", "Sposta su": "Move up", "Sposta giù": "Move down",
    "Imposta predefinito": "Set as default", "Duplica": "Duplicate", "Elimina…": "Delete…",
    "Proteggi la libreria…": "Protect the library…", "Rimuovi protezione": "Remove protection",
    "Chiudi": "Close", "Firme": "Signatures", "{name} — non disponibile": "{name} — not available",
    "{name}: {origin}, creata il {date}": "{name}: {origin}, created on {date}",
    "Proteggi la libreria": "Protect the library",
    "Password per cifrare la libreria delle firme:": "Password to encrypt the signature library:",
    "Rinomina": "Rename", "Nuovo nome:": "New name:", "Elimina": "Delete",
    "Eliminare «{name}» dalla libreria?": "Delete “{name}” from the library?",
}
