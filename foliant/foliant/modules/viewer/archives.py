"""Archivi ZIP (VIS-04): comprimi più PDF, apri un PDF contenuto, estrai tutto."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QLineEdit

from foliant.api import Context, Document, DocumentError, OperationCancelled, _, add_translations

from . import archives_engine as A

TRANSLATIONS = {
    "Archivi": "Archives",
    "Comprimi in archivio ZIP…": "Compress into ZIP archive…",
    "Apri da archivio ZIP…": "Open from ZIP archive…",
    "Estrai archivio ZIP…": "Extract ZIP archive…",
    "Scegli i file da comprimere": "Choose the files to compress",
    "Salva archivio": "Save archive",
    "Scegli l'archivio": "Choose the archive",
    "Archivi ZIP (*.zip)": "ZIP archives (*.zip)",
    "Archivio creato: {p}": "Archive created: {p}",
    "Archivio": "Archive",
    "L'archivio non contiene file PDF.": "The archive contains no PDF files.",
    "Quale documento aprire?": "Which document should be opened?",
    "Archivio protetto": "Protected archive",
    "Password dell'archivio «{n}»:": "Password for archive “{n}”:",
    "Password errata o archivio protetto.": "Wrong password or protected archive.",
    "Estrazione…": "Extracting…",
    "{n} file estratti in {d}": "{n} files extracted to {d}",
    "Scegli dove estrarre": "Choose where to extract",
}


class ArchivesController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.act_compress = ctx.action(_("Comprimi in archivio ZIP…"), "file-archive", self.compress_dialog,
                                       requires_document=False)
        self.act_open = ctx.action(_("Apri da archivio ZIP…"), "package-open", self.open_dialog,
                                   requires_document=False)
        self.act_extract = ctx.action(_("Estrai archivio ZIP…"), "folder-output", self.extract_dialog,
                                      requires_document=False)
        for act in (self.act_open, self.act_compress, self.act_extract):
            ctx.add_menu_action(act, _("Archivi"))

    def _password(self, path: str) -> str | None:
        text, ok = QInputDialog.getText(self.ctx.window, _("Archivio protetto"),
                                        _("Password dell'archivio «{n}»:").format(n=Path(path).name),
                                        QLineEdit.EchoMode.Password)
        return text if ok else None

    # ---------------------------------------------------------------- comprimi
    def compress(self, paths: list[str], target: str) -> bool:
        try:
            A.compress(paths, target)
        except (OSError, A.ArchiveError) as e:
            self.ctx.error(_("Archivio"), str(e))
            return False
        self.ctx.status(_("Archivio creato: {p}").format(p=target))
        return True

    def compress_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli i file da comprimere"), "PDF (*.pdf);;Tutti i file (*)")
        if not paths:
            return
        target = self.ctx.ask_save_path(_("Salva archivio"), Path(paths[0]).stem + ".zip",
                                        _("Archivi ZIP (*.zip)"), ".zip")
        if target:
            self.compress(paths, target)

    # -------------------------------------------------------------------- apri
    def open_archive(self, path: str, member: str | None = None):
        try:
            names = A.list_pdfs(path)
        except A.ArchiveError as e:
            self.ctx.error(_("Archivio"), str(e))
            return None
        if not names:
            self.ctx.error(_("Archivio"), _("L'archivio non contiene file PDF."))
            return None
        if member is None:
            if len(names) == 1:
                member = names[0]
            else:
                member, ok = QInputDialog.getItem(self.ctx.window, _("Archivio"), _("Quale documento aprire?"),
                                                  names, 0, False)
                if not ok:
                    return None
        password = None
        for _attempt in range(4):
            try:
                data = A.read_member(path, member, password)
                break
            except A.PasswordNeeded:
                password = self._password(path)
                if password is None:
                    return None
            except A.ArchiveError as e:
                self.ctx.error(_("Archivio"), str(e))
                return None
        else:
            self.ctx.error(_("Archivio"), _("Password errata o archivio protetto."))
            return None
        try:
            # in memoria: nessun file temporaneo; il salvataggio chiede dove scrivere
            doc = Document.from_bytes(data, display_name=Path(member).name, modified=False)
        except DocumentError as e:
            self.ctx.error(_("Archivio"), f"{Path(member).name}\n\n{e}")
            return None
        return self.ctx.open_document(doc)

    def open_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli l'archivio"), _("Archivi ZIP (*.zip)"), multiple=False)
        if paths:
            self.open_archive(paths[0])

    # ------------------------------------------------------------------ estrai
    def extract(self, path: str, dest: str, password: str | None = None) -> list[str] | None:
        while True:
            try:
                out = self.ctx.run_task(_("Estrazione…"), lambda progress: A.extract_all(
                    path, dest, password, progress=lambda f, t: progress.update(f, t)))
                break
            except A.PasswordNeeded:
                password = self._password(path)
                if password is None:
                    return None
            except OperationCancelled:
                return None
            except (A.ArchiveError, OSError) as e:
                self.ctx.error(_("Archivio"), str(e))
                return None
        self.ctx.status(_("{n} file estratti in {d}").format(n=len(out), d=dest))
        return out

    def extract_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli l'archivio"), _("Archivi ZIP (*.zip)"), multiple=False)
        if not paths:
            return
        dest = self.ctx.ask_directory(_("Scegli dove estrarre"))
        if dest:
            self.extract(paths[0], dest)


def install(ctx: Context) -> ArchivesController:
    add_translations("en", TRANSLATIONS)
    return ArchivesController(ctx)
