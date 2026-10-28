"""Dialogo «Gestisci libreria» delle firme e iniziali (SIG-04)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from foliant.api import _, bind_icon

from .library import PACKAGE_EXT, LibraryError, SignatureLibrary, export_package, import_package, read_package
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
            ("export", _("Esporta…"), "upload"), ("import", _("Importa…"), "download"),
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
            if key == "export":
                self.export_items()
                return
            if key == "import":
                self.import_items()
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

    # ------------------------------------------------------- SIG-07
    def export_items(self) -> int:
        """Esporta gli elementi scelti in un pacchetto cifrato con password obbligatoria."""
        entries = [e for e in self.lib.entries() if e.available]
        if not entries:
            QMessageBox.information(self, _("Esporta"), _("La libreria non contiene elementi da esportare."))
            return 0
        pick = ExportPickDialog(entries, self)
        if not pick.exec() or not pick.selected():
            return 0
        pw = ask_password(self, _("Esporta"), _("Password per proteggere il pacchetto (obbligatoria):"),
                          confirm=True)
        if not pw:
            return 0
        path, _f = QFileDialog.getSaveFileName(self, _("Esporta la libreria"), "firme" + PACKAGE_EXT,
                                               _("Pacchetto di firme (*{ext})").format(ext=PACKAGE_EXT))
        if not path:
            return 0
        if not path.endswith(PACKAGE_EXT):
            path += PACKAGE_EXT
        n = export_package(self.lib, pick.selected(), pw, path)
        QMessageBox.information(self, _("Esporta"), _("Elementi esportati: {n}.").format(n=n))
        return n

    def import_items(self) -> dict | None:
        """Importa un pacchetto: password, anteprima degli elementi e scelta per i duplicati."""
        path, _f = QFileDialog.getOpenFileName(self, _("Importa nella libreria"), "",
                                               _("Pacchetto di firme (*{ext})").format(ext=PACKAGE_EXT))
        if not path:
            return None
        pw = ask_password(self, _("Importa"), _("Password del pacchetto:"))
        if pw is None:
            return None
        items, _files, warnings = read_package(path, pw)    # PackageError → messaggio, niente importato
        have = {e.sha256 for e in self.lib.entries()}
        dups = [it for it in items if it.get("sha256") in have]
        names = "\n".join("• " + str(it.get("name") or "?") + (_(" (già presente)") if it in dups else "")
                          for it in items)
        box = QMessageBox(QMessageBox.Icon.Question, _("Importa"),
                          _("Il pacchetto contiene {n} elementi:").format(n=len(items)), parent=self)
        box.setInformativeText(names + ("\n\n" + "\n".join(warnings) if warnings else ""))
        if dups:
            b_skip = box.addButton(_("Importa e salta i duplicati"), QMessageBox.ButtonRole.AcceptRole)
            b_repl = box.addButton(_("Sostituisci i duplicati"), QMessageBox.ButtonRole.AcceptRole)
            b_both = box.addButton(_("Mantieni entrambi"), QMessageBox.ButtonRole.AcceptRole)
        else:
            b_skip = box.addButton(_("Importa"), QMessageBox.ButtonRole.AcceptRole)
            b_repl = b_both = None
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked not in (b_skip, b_repl, b_both) or clicked is None:
            return None
        mode = "replace" if clicked is b_repl else ("both" if clicked is b_both else "skip")
        res = import_package(self.lib, path, pw, mode)
        self.refresh()
        text = _("Importati: {a} · saltati: {s} · sostituiti: {r}").format(
            a=res["added"], s=res["skipped"], r=res["replaced"])
        if res["warnings"]:
            text += "\n\n" + "\n".join(res["warnings"])
        QMessageBox.information(self, _("Importa"), text)
        return res


class ExportPickDialog(QDialog):
    """Scelta degli elementi da esportare (tutti selezionati all'inizio)."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Esporta la libreria"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Elementi da esportare:")))
        self.list = QListWidget()
        for e in entries:
            it = QListWidgetItem(e.name + "  ·  " + (_("Iniziali") if e.kind == "iniziali" else _("Firma")))
            it.setData(Qt.ItemDataRole.UserRole, e.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Ok).setText(_("Avanti…"))
        box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


TRANSLATIONS_EN = {
    "Esporta…": "Export…", "Importa…": "Import…", "Esporta": "Export", "Importa": "Import",
    "La libreria non contiene elementi da esportare.": "The library contains no items to export.",
    "Password per proteggere il pacchetto (obbligatoria):": "Password to protect the package (required):",
    "Esporta la libreria": "Export the library", "Pacchetto di firme (*{ext})": "Signature package (*{ext})",
    "Elementi esportati: {n}.": "Items exported: {n}.", "Importa nella libreria": "Import into the library",
    "Password del pacchetto:": "Package password:", " (già presente)": " (already present)",
    "Il pacchetto contiene {n} elementi:": "The package contains {n} items:",
    "Importa e salta i duplicati": "Import and skip duplicates", "Sostituisci i duplicati": "Replace duplicates",
    "Mantieni entrambi": "Keep both", "Annulla": "Cancel",
    "Importati: {a} · saltati: {s} · sostituiti: {r}": "Imported: {a} · skipped: {s} · replaced: {r}",
    "Elementi da esportare:": "Items to export:", "Avanti…": "Next…", "Iniziali": "Initials", "Firma": "Signature",
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
