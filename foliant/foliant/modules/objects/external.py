"""Modifica di un'immagine in un'applicazione esterna (OBJ-19).

L'immagine viene esportata senza perdita in una cartella temporanea privata;
a ogni salvataggio del file l'immagine del documento si aggiorna con un passo
annullabile. La sessione termina con «Termina modifica esterna» o alla chiusura
del documento, e i file temporanei vengono cancellati.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from foliant.api import ENGINE_LOCK, _

from . import engine as E
from .content import IMAGE


def launch(path: str) -> bool:
    """Apre il file con l'applicazione predefinita del sistema (sostituibile nei test)."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


class ExternalEdit(QObject):
    def __init__(self, controller, view, page: int, index: int):
        super().__init__(controller)
        self.c = controller
        self.view = view
        self.page = page
        self.index = index
        self.dir = tempfile.mkdtemp(prefix="foliant-img-")
        os.chmod(self.dir, 0o700)
        self.path = ""
        self.watcher = QFileSystemWatcher(self)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.reimport)
        self.watcher.fileChanged.connect(lambda _p: self.timer.start())
        self.watcher.directoryChanged.connect(lambda _p: self.timer.start())
        self._last = None
        self.active = False

    def start(self) -> bool:
        objs = self.c.parsed(self.view, self.page)
        o = objs.objects[self.index] if objs and self.index < len(objs.objects) else None
        if o is None or o.kind != IMAGE:
            return False
        with ENGINE_LOCK:
            data = E.extract_image(self.view.document.pdf, o, "png").data
        self.path = os.path.join(self.dir, f"immagine_p{self.page + 1}.png")
        with open(self.path, "wb") as f:
            f.write(data)
        os.chmod(self.path, 0o600)
        self._last = data
        self.watcher.addPath(self.path)
        self.watcher.addPath(self.dir)
        self.active = True
        if not launch(self.path):
            self.c.ctx.error(_("Modifica con…"), _("Nessuna applicazione disponibile per aprire le immagini."))
            self.stop()
            return False
        self.c.status(_("Modifica esterna avviata: salva il file nell'editor per aggiornare il documento."))
        return True

    def reimport(self) -> bool:
        if not self.active:
            return False
        if not os.path.exists(self.path):
            self.c.ctx.error(_("Modifica con…"), _("Il file temporaneo è stato eliminato o rinominato: "
                                                   "la modifica esterna è terminata."))
            self.stop()
            return False
        if self.path not in self.watcher.files():
            self.watcher.addPath(self.path)
        try:
            with open(self.path, "rb") as f:
                data = f.read()
        except OSError:
            return False
        if not data or data == self._last:
            return False
        try:
            _w, _h, pw, ph = E.natural_size(data)
        except E.ObjectError:
            return False  # file non ancora completo: si attende il prossimo salvataggio
        objs = self.c.parsed(self.view, self.page)
        if objs is None or self.index >= len(objs.objects) or objs.objects[self.index].kind != IMAGE:
            self.stop()
            return False
        o = objs.objects[self.index]
        same = o.width and o.height and abs(pw / ph - o.width / o.height) < 1e-3
        mode = E.STRETCH if same else E.FIT
        res = self.c.run_change(self.view, _("Modifica esterna dell'immagine"), E.replace_image, self.page,
                                self.index, data, mode, False)
        if res is not None:
            self._last = data
            return True
        return False

    def stop(self) -> None:
        self.active = False
        self.timer.stop()
        for p in self.watcher.files() + self.watcher.directories():
            self.watcher.removePath(p)
        shutil.rmtree(self.dir, ignore_errors=True)
