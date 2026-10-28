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
    def __init__(self, library: SignatureLibrary, parent=None, default_name: str = "", controller=None):
        super().__init__(parent)
        self.lib = library
        self.default_name = default_name
        self.c = controller       # SignController: sincronizzazione (facoltativo)
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
            ("sync", _("Sincronizza con cartella…"), "folder-sync"),
        ]:
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.clicked.connect(lambda _c=False, k=key: self._do(k))
            col.addWidget(b)
            self.buttons[key] = b
        self.buttons["sync"].setVisible(controller is not None)
        if controller is not None:
            controller.librarySynced.connect(self.refresh)
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
        sync_text = getattr(self.c, "sync_status", "") if self.c is not None else ""
        self.notice.setText("\n".join(t for t in (self.lib.notice, sync_text) if t))
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
            if key == "sync":
                SyncDialog(self.lib, self.c, self).exec()
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


class SyncDialog(QDialog):
    """«Sincronizza con cartella» (SIG-07): scelta della cartella e della passphrase, stato,
    sincronizzazione immediata, elementi eliminati recuperabili, scollegamento."""

    def __init__(self, library: SignatureLibrary, controller, parent=None):
        super().__init__(parent)
        from . import sync
        self.sync = sync
        self.lib = library
        self.c = controller
        self.setWindowTitle(_("Sincronizza con cartella"))
        self.resize(520, 0)
        lay = QVBoxLayout(self)
        intro = QLabel(_("Scegli una cartella locale, di rete o gestita da un programma di sincronizzazione: "
                         "la libreria vi viene copiata cifrata con la passphrase e allineata con gli altri "
                         "dispositivi collegati alla stessa cartella all'avvio e ogni minuto."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.state = QLabel("")
        self.state.setWordWrap(True)
        lay.addWidget(self.state)
        row = QHBoxLayout()
        self.b_folder = QPushButton(_("Scegli cartella…"))
        self.b_now = QPushButton(_("Sincronizza ora"))
        self.b_deleted = QPushButton(_("Elementi eliminati…"))
        self.b_off = QPushButton(_("Scollega"))
        for b, fn in [(self.b_folder, self.choose_folder), (self.b_now, self.sync_now),
                      (self.b_deleted, self.restore_deleted), (self.b_off, self.disconnect)]:
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.update_state()

    def folder(self) -> str | None:
        return self.sync.configured_folder(self.lib)

    def update_state(self) -> None:
        folder = self.folder()
        if folder:
            self.state.setText(self.c.sync_status or _("Collegata a {folder}.").format(folder=folder))
        else:
            self.state.setText(_("La libreria non è sincronizzata."))
        for b in (self.b_now, self.b_deleted, self.b_off):
            b.setEnabled(bool(folder))

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, _("Cartella di sincronizzazione"))
        if not path:
            return
        from pathlib import Path
        new = not (Path(path) / self.sync.HEADER).exists()
        pw = ask_password(self, _("Sincronizza con cartella"),
                          _("Passphrase per cifrare la libreria nella cartella (la stessa su tutti i dispositivi):")
                          if new else _("Passphrase della libreria sincronizzata in questa cartella:"), confirm=new)
        if not pw:
            return
        try:
            res = self.sync.connect(self.lib, path, pw)
        except LibraryError as e:
            QMessageBox.warning(self, _("Sincronizza con cartella"), str(e))
            return
        folder = self.folder()
        if not self.c.remember_sync_passphrase(folder, pw):
            QMessageBox.information(self, _("Sincronizza con cartella"), _(
                "Il portachiavi del sistema non è disponibile: la passphrase resta in memoria e andrà reinserita "
                "al prossimo avvio."))
        self.c.sync_library(interactive=False)
        QMessageBox.information(self, _("Sincronizza con cartella"), _(
            "Libreria collegata: inviati {p}, ricevuti {r} elementi.").format(p=res.pushed, r=res.pulled))
        self.update_state()

    def _passphrase(self) -> str | None:
        folder = self.folder()
        pw = self.c.sync_passphrase(folder) if folder else None
        if not pw and folder:
            pw = ask_password(self, _("Sincronizza con cartella"), _("Passphrase della cartella:"))
            if pw:
                self.c.remember_sync_passphrase(folder, pw)
        return pw

    def sync_now(self) -> None:
        if self._passphrase():
            self.c.sync_library(interactive=False)
        self.update_state()

    def restore_deleted(self) -> None:
        pw = self._passphrase()
        if not pw:
            return
        try:
            items = self.sync.deleted_items(self.lib, pw)
        except LibraryError as e:
            QMessageBox.warning(self, _("Sincronizza con cartella"), str(e))
            return
        if not items:
            QMessageBox.information(self, _("Elementi eliminati"), _(
                "Nessun elemento eliminato negli ultimi 30 giorni."))
            return
        labels = [f"{name} — {when[:10]}" for _eid, name, when in items]
        choice, ok = QInputDialog.getItem(self, _("Elementi eliminati"), _("Elemento da ripristinare:"), labels,
                                          0, False)
        if not ok:
            return
        eid = items[labels.index(choice)][0]
        try:
            self.sync.restore(self.lib, pw, eid)
        except LibraryError as e:
            QMessageBox.warning(self, _("Sincronizza con cartella"), str(e))
        self.c.librarySynced.emit()
        self.update_state()

    def disconnect(self) -> None:
        if QMessageBox.question(self, _("Scollega"), _(
                "Scollegare la libreria dalla cartella? Gli elementi restano sia qui sia nella cartella.")) \
                != QMessageBox.StandardButton.Yes:
            return
        self.sync.disconnect(self.lib)
        self.c.sync_library(interactive=False)     # ferma il ciclo periodico
        self.update_state()


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
    "Sincronizza con cartella…": "Sync with folder…", "Sincronizza con cartella": "Sync with folder",
    "Scegli una cartella locale, di rete o gestita da un programma di sincronizzazione: la libreria vi viene "
    "copiata cifrata con la passphrase e allineata con gli altri dispositivi collegati alla stessa cartella "
    "all'avvio e ogni minuto.": "Choose a local or network folder, or one managed by a sync program: the library "
                                "is copied there encrypted with the passphrase and kept aligned with the other "
                                "devices linked to the same folder at startup and every minute.",
    "Scegli cartella…": "Choose folder…", "Sincronizza ora": "Sync now", "Elementi eliminati…": "Deleted items…",
    "Scollega": "Disconnect", "Collegata a {folder}.": "Linked to {folder}.",
    "La libreria non è sincronizzata.": "The library is not synced.",
    "Cartella di sincronizzazione": "Sync folder",
    "Passphrase per cifrare la libreria nella cartella (la stessa su tutti i dispositivi):":
        "Passphrase to encrypt the library in the folder (the same on every device):",
    "Passphrase della libreria sincronizzata in questa cartella:": "Passphrase of the library synced in this folder:",
    "Il portachiavi del sistema non è disponibile: la passphrase resta in memoria e andrà reinserita al prossimo "
    "avvio.": "The system keyring is not available: the passphrase stays in memory and must be entered again at "
              "the next start.",
    "Libreria collegata: inviati {p}, ricevuti {r} elementi.": "Library linked: {p} items sent, {r} received.",
    "Passphrase della cartella:": "Folder passphrase:", "Elementi eliminati": "Deleted items",
    "Nessun elemento eliminato negli ultimi 30 giorni.": "No items deleted in the last 30 days.",
    "Elemento da ripristinare:": "Item to restore:",
    "Scollegare la libreria dalla cartella? Gli elementi restano sia qui sia nella cartella.":
        "Disconnect the library from the folder? The items stay both here and in the folder.",
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
