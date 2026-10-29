"""Invio e condivisione locale (VIS-45): invio per e-mail con il client
predefinito, copia del file negli appunti, in File › Condividi › «Invia».
(«Mostra nella cartella» e «Copia percorso» sono nella parte `files`.)"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QMimeData, QProcess, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations
from foliant.core import platform

TRANSLATIONS = {
    "Invia": "Send",
    "Allega il PDF a un nuovo messaggio": "Attach the PDF to a new message",
    "Da incollare in una cartella, una chat o un messaggio": "To paste into a folder, a chat or a message",
    "Invia per e-mail…": "Send by e-mail…",
    "Copia file negli appunti": "Copy file to clipboard",
    "Il documento ha modifiche non salvate.": "The document has unsaved changes.",
    "Cosa vuoi inviare?": "What do you want to send?",
    "Versione salvata": "Saved version",
    "Salva e invia": "Save and send",
    "Copia con le modifiche": "Copy with the changes",
    "Nessun client di posta trovato. Puoi allegare il file manualmente: usa «Mostra nella cartella».":
        "No e-mail client found. You can attach the file manually: use “Show in folder”.",
    "Invio per e-mail": "Send by e-mail",
    "Il documento è protetto: comunica la password al destinatario per un altro canale.":
        "The document is protected: give the recipient the password through another channel.",
    "File copiato negli appunti": "File copied to the clipboard",
    "Il file supera {n} MB: valuta di comprimerlo prima dell'invio.":
        "The file exceeds {n} MB: consider compressing it before sending.",
}

SIZE_WARNING_MB = 20


class ShareController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self._temp_dirs: list[str] = []
        self.act_email = ctx.action(_("Invia per e-mail…"), "mail", self.send_email)
        self.act_copy = ctx.action(_("Copia file negli appunti"), "clipboard-copy", self.copy_file)
        ctx.add_file_action(self.act_email, "condividi", _("Invia"), _("Allega il PDF a un nuovo messaggio"),
                            order=10)
        ctx.add_file_action(self.act_copy, "condividi", _("Invia"),
                            _("Da incollare in una cartella, una chat o un messaggio"), order=20)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cleanup)

    # ------------------------------------------------------------- utilità
    def launch(self, program: str, args: list[str]) -> bool:
        """Avvia un programma esterno senza attendere (sostituibile nei test)."""
        ok, _pid = QProcess.startDetached(program, args)
        return bool(ok)

    def mail_client(self) -> str | None:
        """xdg-email su Linux; «MAPI» su Windows (il programma di posta predefinito)."""
        return platform.mail_client()

    def ask_modified_choice(self) -> str | None:
        box = QMessageBox(QMessageBox.Icon.Question, _("Invio per e-mail"), _("Il documento ha modifiche non salvate."),
                          QMessageBox.StandardButton.Cancel, self.ctx.window)
        box.setInformativeText(_("Cosa vuoi inviare?"))
        saved = box.addButton(_("Versione salvata"), QMessageBox.ButtonRole.AcceptRole)
        save = box.addButton(_("Salva e invia"), QMessageBox.ButtonRole.AcceptRole)
        copy = box.addButton(_("Copia con le modifiche"), QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        return {saved: "saved", save: "save", copy: "copy"}.get(box.clickedButton())

    def private_copy(self, view: DocumentView) -> str:
        """Copia con le modifiche in una cartella temporanea privata, col nome originale."""
        d = tempfile.mkdtemp(prefix=f"foliant-share-{os.getpid()}-")
        os.chmod(d, 0o700)
        self._temp_dirs.append(d)
        target = Path(d) / view.document.display_name
        view.document.save_copy(target)
        return str(target)

    def cleanup(self) -> None:
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)
        self._temp_dirs.clear()

    def file_to_send(self, view: DocumentView) -> str | None:
        doc = view.document
        if doc.path is None:
            return self.private_copy(view)
        if not doc.is_modified:
            return str(doc.path)
        choice = self.ask_modified_choice()
        if choice == "saved":
            return str(doc.path)
        if choice == "save":
            return str(doc.path) if self.ctx.save_current() else None
        if choice == "copy":
            return self.private_copy(view)
        return None

    # ---------------------------------------------------------------- comandi
    def send_email(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            return False
        client = self.mail_client()
        if client is None:
            self.ctx.info(_("Invio per e-mail"), _("Nessun client di posta trovato. Puoi allegare il file manualmente: "
                                                   "usa «Mostra nella cartella»."))
            return False
        path = self.file_to_send(view)
        if path is None:
            return False
        with ENGINE_LOCK:
            title = (view.document.pdf.metadata or {}).get("title") or ""
        subject = title.strip() or Path(path).stem
        try:
            if os.path.getsize(path) > SIZE_WARNING_MB * 1024 * 1024:
                self.ctx.status(_("Il file supera {n} MB: valuta di comprimerlo prima dell'invio.")
                                .format(n=SIZE_WARNING_MB), 8000)
        except OSError:
            pass
        if view.document.is_protected:
            self.ctx.status(_("Il documento è protetto: comunica la password al destinatario per un altro canale."), 8000)
        if client == "MAPI":
            ok = platform.compose_mail(path, subject=subject)
        else:
            ok = self.launch(client, ["--subject", subject, "--attach", path])
        if not ok:
            self.ctx.info(_("Invio per e-mail"), _("Nessun client di posta trovato. Puoi allegare il file manualmente: "
                                                   "usa «Mostra nella cartella»."))
        return ok

    def copy_file(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            return False
        path = str(view.document.path) if view.document.path is not None and not view.document.is_modified \
            else self.private_copy(view)
        data = QMimeData()
        url = QUrl.fromLocalFile(path)
        data.setUrls([url])
        data.setData("x-special/gnome-copied-files", f"copy\n{url.toString()}".encode("utf-8"))
        QGuiApplication.clipboard().setMimeData(data)
        self.ctx.status(_("File copiato negli appunti"))
        return True


def install(ctx: Context) -> ShareController:
    add_translations("en", TRANSLATIONS)
    return ShareController(ctx)
