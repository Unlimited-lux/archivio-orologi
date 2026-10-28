"""Gestione dei file locali (VIS-03): rinomina, duplica, sposta, cestino, mostra
nella cartella, copia percorso; le schede aperte seguono il file.

Comandi nel menu principale (sezione «File») sul documento corrente e su file
scelti. I dialoghi passano per metodi `ask_*` sostituibili nei test.
Riservatezza: il modulo non usa l'elenco dei file recenti (né lo alimenta) e
non conserva nomi o percorsi dei file tra un avvio e l'altro.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QFile, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox

from foliant.api import Context, _, add_translations

from . import files_engine as F

REPLACE, AUTO, CANCEL = "replace", "auto", "cancel"

TRANSLATIONS = {
    "File": "File",
    "Rinomina…": "Rename…",
    "Duplica": "Duplicate",
    "Sposta in…": "Move to…",
    "Sposta nel cestino": "Move to trash",
    "Mostra nella cartella": "Show in folder",
    "Copia percorso": "Copy path",
    "Duplica file…": "Duplicate files…",
    "Sposta file in…": "Move files to…",
    "Sposta file nel cestino…": "Move files to trash…",
    "Rinomina": "Rename",
    "Nuovo nome per «{n}»:": "New name for “{n}”:",
    "Sposta": "Move",
    "Scegli la cartella di destinazione": "Choose the destination folder",
    "Scegli i file da duplicare": "Choose the files to duplicate",
    "Scegli i file da spostare": "Choose the files to move",
    "Scegli i file da spostare nel cestino": "Choose the files to move to trash",
    "Nome già esistente": "Name already exists",
    "Nella cartella «{d}» esiste già un file chiamato «{n}».":
        "A file named “{n}” already exists in the folder “{d}”.",
    "Sostituisci": "Replace",
    "Rinomina automaticamente": "Rename automatically",
    "Annulla": "Cancel",
    "Modifiche non salvate": "Unsaved changes",
    "«{n}» ha modifiche non salvate. Il documento aperto seguirà il file e le modifiche restano da salvare. Continuare?":
        "“{n}” has unsaved changes. The open document will follow the file and the changes still need saving. Continue?",
    "«{n}» ha modifiche non salvate che andranno perse. Continuare?":
        "“{n}” has unsaved changes that will be lost. Continue?",
    "Spostare nel cestino «{n}»?": "Move “{n}” to the trash?",
    "Spostare nel cestino {k} file?": "Move {k} files to the trash?",
    "Il documento aperto verrà chiuso.": "The open document will be closed.",
    "Cestino non disponibile": "Trash not available",
    "Il cestino del sistema non è disponibile per «{n}». Eliminarlo definitivamente? L'operazione non si può annullare.":
        "The system trash is not available for “{n}”. Delete it permanently? This cannot be undone.",
    "Gestione file": "File management",
    "Il file «{n}» è aperto in un'altra scheda: chiudila prima di sostituirlo.":
        "The file “{n}” is open in another tab: close it before replacing it.",
    "Rinominato in «{n}»": "Renamed to “{n}”",
    "Creata la copia «{n}»": "Created copy “{n}”",
    "Create {k} copie": "Created {k} copies",
    "Spostato in «{d}»": "Moved to “{d}”",
    "Spostato nel cestino: {n}": "Moved to trash: {n}",
    "Eliminato: {n}": "Deleted: {n}",
    "Percorso copiato: {p}": "Path copied: {p}",
    "Il documento non è ancora stato salvato.": "The document has not been saved yet.",
    # files_engine
    "Esiste già un file chiamato «{n}».": "A file named “{n}” already exists.",
    "Il nome non può essere vuoto.": "The name cannot be empty.",
    "«{n}» non è un nome di file valido.": "“{n}” is not a valid file name.",
    "carattere nullo": "null character",
    "Il nome contiene caratteri non consentiti: {c}.": "The name contains characters that are not allowed: {c}.",
    "Il nome è troppo lungo ({n} byte, massimo {m}).": "The name is too long ({n} bytes, maximum {m}).",
    "copia": "copy",
    "Permesso di scrittura mancante sulla cartella «{d}».": "Missing write permission on the folder “{d}”.",
    "Il volume che contiene «{d}» è in sola lettura.": "The volume containing “{d}” is read-only.",
    "Il file «{n}» non esiste più.": "The file “{n}” no longer exists.",
    "Il nome o il percorso è troppo lungo.": "The name or path is too long.",
    "Spazio su disco insufficiente in «{d}».": "Not enough disk space in “{d}”.",
    "«{n}» è una cartella.": "“{n}” is a folder.",
    "Operazione non riuscita su «{n}»: {e}.": "Operation failed on “{n}”: {e}.",
    "La cartella «{d}» non esiste.": "The folder “{d}” does not exist.",
    "Esiste già una cartella chiamata «{n}».": "A folder named “{n}” already exists.",
}


def _norm(path) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


class FilesController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        w = ctx.window
        a = ctx.action
        # Comandi sul documento corrente: attivi solo se il documento ha un file su disco.
        self.act_rename = a(_("Rinomina…"), "pencil", self.rename_current, requires_document=False)
        self.act_duplicate = a(_("Duplica"), "copy", self.duplicate_current, requires_document=False)
        self.act_move = a(_("Sposta in…"), "folder-input", self.move_current, requires_document=False)
        self.act_trash = a(_("Sposta nel cestino"), "trash-2", self.trash_current, requires_document=False)
        self.act_reveal = a(_("Mostra nella cartella"), "folder-open", self.reveal_current,
                            requires_document=False)
        self.act_copy_path = a(_("Copia percorso"), "clipboard-copy", self.copy_path_current,
                               requires_document=False)
        self.current_actions = (self.act_rename, self.act_duplicate, self.act_move, self.act_trash,
                                self.act_reveal, self.act_copy_path)
        # Comandi su file scelti (uno o più).
        self.act_duplicate_many = a(_("Duplica file…"), "files", self.duplicate_dialog, requires_document=False)
        self.act_move_many = a(_("Sposta file in…"), "folder-output", self.move_dialog, requires_document=False)
        self.act_trash_many = a(_("Sposta file nel cestino…"), "trash", self.trash_dialog,
                                requires_document=False)
        separator = QAction(w)
        separator.setSeparator(True)
        for act in self.current_actions + (separator, self.act_duplicate_many, self.act_move_many,
                                           self.act_trash_many):
            ctx.add_menu_action(act, _("File"))
        ctx.currentViewChanged.connect(lambda *_a: self.update_actions())
        ctx.documentChanged.connect(lambda *_a: self.update_actions())
        w.main_menu.aboutToShow.connect(self.update_actions)
        self.update_actions()

    # ================================================================ stato
    def current_path(self) -> str | None:
        doc = self.ctx.current_document()
        if doc is None or doc.path is None:
            return None
        return _norm(doc.path)

    def update_actions(self) -> None:
        enabled = self.current_path() is not None
        for act in self.current_actions:
            act.setEnabled(enabled)

    def views_for(self, path: str) -> list:
        path = _norm(path)
        return [v for v in self.ctx.views() if v.document.path is not None and _norm(v.document.path) == path]

    # ============================================================== dialoghi
    def show_error(self, message: str) -> None:
        self.ctx.error(_("Gestione file"), message)

    def ask_new_name(self, path: str) -> str | None:
        name = os.path.basename(path)
        dlg = QInputDialog(self.ctx.window)
        dlg.setWindowTitle(_("Rinomina"))
        dlg.setLabelText(_("Nuovo nome per «{n}»:").format(n=name))
        dlg.setTextValue(name)
        dlg.setOkButtonText(_("Rinomina"))
        dlg.setCancelButtonText(_("Annulla"))
        dlg.resize(420, dlg.sizeHint().height())
        edit = dlg.findChild(QLineEdit)
        if edit is not None:  # seleziona il nome senza estensione, dopo la selezione automatica
            QTimer.singleShot(0, lambda: edit.setSelection(0, len(Path(name).stem)))
        if not dlg.exec():
            return None
        return dlg.textValue()

    def ask_conflict(self, target: str) -> str:
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(_("Nome già esistente"))
        box.setText(_("Nella cartella «{d}» esiste già un file chiamato «{n}».").format(
            d=os.path.dirname(target), n=os.path.basename(target)))
        replace = box.addButton(_("Sostituisci"), QMessageBox.ButtonRole.DestructiveRole)
        auto = box.addButton(_("Rinomina automaticamente"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(auto)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace:
            return REPLACE
        if clicked is auto:
            return AUTO
        return CANCEL

    def ask_folder(self) -> str | None:
        return self.ctx.ask_directory(_("Scegli la cartella di destinazione"))

    def ask_paths(self, title: str) -> list[str]:
        return self.ctx.ask_open_paths(title)

    def ask_unsaved(self, path: str, losing: bool) -> bool:
        name = os.path.basename(path)
        text = (_("«{n}» ha modifiche non salvate che andranno perse. Continuare?") if losing else
                _("«{n}» ha modifiche non salvate. Il documento aperto seguirà il file e le modifiche "
                  "restano da salvare. Continuare?")).format(n=name)
        return QMessageBox.warning(self.ctx.window, _("Modifiche non salvate"), text,
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                   QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def ask_trash(self, paths: list[str]) -> bool:
        if len(paths) == 1:
            text = _("Spostare nel cestino «{n}»?").format(n=os.path.basename(paths[0]))
        else:
            text = _("Spostare nel cestino {k} file?").format(k=len(paths))
        if any(self.views_for(p) for p in paths):
            text += "\n\n" + _("Il documento aperto verrà chiuso.")
        return self.ctx.confirm(_("Sposta nel cestino"), text)

    def ask_delete_permanently(self, path: str) -> bool:
        return QMessageBox.warning(
            self.ctx.window, _("Cestino non disponibile"),
            _("Il cestino del sistema non è disponibile per «{n}». Eliminarlo definitivamente? "
              "L'operazione non si può annullare.").format(n=os.path.basename(path)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def move_to_trash(self, path: str) -> bool:
        """Sposta nel cestino del sistema; False se il cestino non è disponibile."""
        result = QFile.moveToTrash(path)
        return bool(result[0] if isinstance(result, tuple) else result)

    def open_folder_url(self, url: QUrl) -> bool:
        return QDesktopServices.openUrl(url)

    # ======================================================== schede aperte
    def _unsaved_ok(self, path: str, losing: bool) -> bool:
        if any(v.document.is_modified for v in self.views_for(path)):
            return self.ask_unsaved(path, losing)
        return True

    def _follow(self, old: str, new: str) -> None:
        """Le schede aperte seguono il file da `old` a `new`."""
        w = self.ctx.window
        for view in self.views_for(old):
            view.document.path = Path(new)
            w._update_tab_title(view)
        self.update_actions()

    def _forget(self, path: str) -> None:
        for view in self.views_for(path):
            # modifiche già scartate dall'utente; file eliminato o spostato: niente domande
            self.ctx.close_view(view, discard=True)
        self.update_actions()

    def _move_with_conflicts(self, src: str, dst: str) -> str | None:
        """Sposta gestendo il nome già esistente (sostituisci / rinomina automatica / annulla)."""
        overwrite = False
        while True:
            try:
                result = F.move(src, dst, overwrite=overwrite)
            except F.NameConflict as e:
                choice = self.ask_conflict(e.target)
                if choice == REPLACE:
                    if self.views_for(e.target):
                        self.show_error(_("Il file «{n}» è aperto in un'altra scheda: chiudila prima di "
                                          "sostituirlo.").format(n=os.path.basename(e.target)))
                        return None
                    overwrite = True
                elif choice == AUTO:
                    dst = F.auto_rename_target(e.target)
                else:
                    return None
                continue
            except F.FileOpError as e:
                self.show_error(str(e))
                return None
            self._follow(_norm(src), _norm(result))
            return result

    # ============================================================ operazioni
    def rename_path(self, path: str, new_name: str | None = None) -> str | None:
        path = _norm(path)
        if new_name is None:
            new_name = self.ask_new_name(path)
            if new_name is None:
                return None
        try:
            target = F.rename_target(path, new_name)
        except F.FileOpError as e:
            self.show_error(str(e))
            return None
        if target == path:
            return path
        result = self._move_with_conflicts(path, target)
        if result:
            self.ctx.status(_("Rinominato in «{n}»").format(n=os.path.basename(result)))
        return result

    def duplicate_paths(self, paths: list[str]) -> list[str]:
        created = []
        for p in paths:
            try:
                created.append(F.duplicate(_norm(p)))
            except F.FileOpError as e:
                self.show_error(str(e))
        if len(created) == 1:
            self.ctx.status(_("Creata la copia «{n}»").format(n=os.path.basename(created[0])))
        elif created:
            self.ctx.status(_("Create {k} copie").format(k=len(created)))
        return created

    def move_paths(self, paths: list[str], folder: str | None = None) -> list[str]:
        if not paths:
            return []
        if folder is None:
            folder = self.ask_folder()
            if not folder:
                return []
        moved = []
        for p in paths:
            p = _norm(p)
            if not self._unsaved_ok(p, losing=False):
                continue
            result = self._move_with_conflicts(p, os.path.join(_norm(folder), os.path.basename(p)))
            if result:
                moved.append(result)
        if moved:
            self.ctx.status(_("Spostato in «{d}»").format(d=folder))
        return moved

    def trash_paths(self, paths: list[str], confirm: bool = True) -> list[str]:
        paths = [_norm(p) for p in paths]
        if not paths or (confirm and not self.ask_trash(paths)):
            return []
        done = []
        for p in paths:
            if not self._unsaved_ok(p, losing=True):
                continue
            try:
                F._check_source(p)
                F.check_writable_folder(os.path.dirname(p))
            except F.FileOpError as e:
                self.show_error(str(e))
                continue
            if self.move_to_trash(p):
                self.ctx.status(_("Spostato nel cestino: {n}").format(n=os.path.basename(p)))
            else:
                if not self.ask_delete_permanently(p):
                    continue
                try:
                    F.delete_permanently(p)
                except F.FileOpError as e:
                    self.show_error(str(e))
                    continue
                self.ctx.status(_("Eliminato: {n}").format(n=os.path.basename(p)))
            self._forget(p)
            done.append(p)
        return done

    def reveal_path(self, path: str) -> bool:
        return self.open_folder_url(QUrl.fromLocalFile(os.path.dirname(_norm(path))))

    def copy_path(self, path: str) -> None:
        path = _norm(path)
        QApplication.clipboard().setText(path)
        self.ctx.status(_("Percorso copiato: {p}").format(p=path))

    # ===================================================== documento corrente
    def _current_or_warn(self) -> str | None:
        path = self.current_path()
        if path is None:
            self.ctx.status(_("Il documento non è ancora stato salvato."))
        return path

    def rename_current(self) -> str | None:
        path = self._current_or_warn()
        return self.rename_path(path) if path else None

    def duplicate_current(self) -> list[str]:
        path = self._current_or_warn()
        return self.duplicate_paths([path]) if path else []

    def move_current(self) -> list[str]:
        path = self._current_or_warn()
        return self.move_paths([path]) if path else []

    def trash_current(self) -> list[str]:
        path = self._current_or_warn()
        return self.trash_paths([path]) if path else []

    def reveal_current(self) -> None:
        path = self._current_or_warn()
        if path:
            self.reveal_path(path)

    def copy_path_current(self) -> None:
        path = self._current_or_warn()
        if path:
            self.copy_path(path)

    # ========================================================= file scelti
    def duplicate_dialog(self) -> list[str]:
        return self.duplicate_paths(self.ask_paths(_("Scegli i file da duplicare")))

    def move_dialog(self) -> list[str]:
        paths = self.ask_paths(_("Scegli i file da spostare"))
        return self.move_paths(paths) if paths else []

    def trash_dialog(self) -> list[str]:
        return self.trash_paths(self.ask_paths(_("Scegli i file da spostare nel cestino")))


def install(ctx: Context) -> FilesController:
    add_translations("en", TRANSLATIONS)
    return FilesController(ctx)
