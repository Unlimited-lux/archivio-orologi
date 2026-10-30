"""Un file, una scheda: lo stesso file non è mai aperto due volte nello stesso momento.

Vale per le schede della stessa finestra, per le finestre dello stesso processo (Ctrl+N) e per gli
altri processi Foliant (un secondo avvio mentre il primo è occupato, `--new-window`…):

- nel processo un registro in memoria `percorso normalizzato → scheda` (`OpenFile`) dice se un'altra
  scheda ha già il file: si porta in primo piano quella, con la sua finestra (`show`);
- tra processi il blocco su disco di core/openlock.py: il processo che ha il file riceve sul proprio
  canale (ui/instance.py) la richiesta di mostrarlo, con il solo nome del blocco (HMAC), mai il
  percorso (`ask_to_show`, `show_name`).

Il blocco segue il documento: si prende prima di leggere il file (`reserve`: due aperture
contemporanee non lo leggono entrambe), passa al nuovo percorso dopo «Salva con nome», una rinomina
o uno spostamento (`follow`) e si libera alla chiusura della scheda, della finestra o del processo
(`release`, `openlock.release_all`). Il registro sta solo in memoria.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from ..core import openlock, platform
from ..i18n import _

_mutex = threading.RLock()


class OpenFile:
    """Un file aperto in una scheda di questo processo, o in apertura (`view` ancora None)."""

    def __init__(self, path: str, lock: openlock.FileLock | None, window):
        self.path = path            # percorso normalizzato, solo in memoria
        self.lock = lock            # None senza cartella privata: controllo solo nel processo
        self.window = window        # finestra della scheda, o che sta aprendo il file
        self.view = None            # DocumentView, None finché il documento non è caricato

    @property
    def name(self) -> str | None:
        """Nome del blocco su disco (HMAC del percorso)."""
        return self.lock.name if self.lock is not None else None


@dataclass(frozen=True)
class Busy:
    """File già aperto: in una scheda di questo processo (`entry`) o in un altro processo (`holder`)."""

    entry: OpenFile | None = None
    holder: openlock.Holder | None = None


_files: dict[str, OpenFile] = {}


def _valid(obj) -> bool:
    import shiboken6
    return obj is not None and shiboken6.isValid(obj)


def _alive(entry: OpenFile) -> bool:
    if entry.view is None:                  # in apertura (domanda della password…) o Salva con nome
        return entry.window is None or _valid(entry.window)
    return _valid(entry.view) and not entry.view.document.closed


def entries() -> list[OpenFile]:
    """File aperti (o in apertura) in questo processo."""
    with _mutex:
        return [e for e in list(_files.values()) if _alive(e)]


def find(path) -> OpenFile | None:
    """Scheda di questo processo (di qualunque finestra) che ha o sta aprendo il file `path`."""
    with _mutex:
        entry = _files.get(platform.normalize_path(path))
        if entry is not None and not _alive(entry):
            release(entry)                  # scheda distrutta senza chiusura: il file è libero
            return None
        return entry


def entry_of(view) -> OpenFile | None:
    """Il file della scheda `view`, se ne ha uno."""
    with _mutex:
        return next((e for e in _files.values() if e.view is view), None)


def view_for(path):
    """Vista di questo processo che ha aperto il file `path`, o None."""
    entry = find(path)
    return entry.view if entry is not None else None


def busy(path) -> Busy | None:
    """Solo controllo: chi ha già aperto `path` (questo processo o un altro), o None se è libero."""
    entry = find(path)
    if entry is not None:
        return Busy(entry=entry)
    holder = openlock.holder(path)
    return Busy(holder=holder) if holder is not None else None


def reserve(path, window, replacing=None) -> OpenFile | Busy:
    """Prende il file `path` per una scheda di `window`; Busy se è già aperto. Il file resta preso
    finché non lo si lega a una scheda (`bind`) e la si chiude, o finché non lo si libera (`release`).

    `replacing`: vista il cui documento il nuovo sostituisce (ricarica dal disco): il suo file passa
    alla nuova scheda invece di risultare già aperto."""
    norm = platform.normalize_path(path)
    with _mutex:
        entry = find(norm)
        if entry is not None:
            return entry if replacing is not None and entry.view is replacing else Busy(entry=entry)
        lock = openlock.acquire(norm)
        if isinstance(lock, openlock.Holder):
            return Busy(holder=lock)
        entry = OpenFile(norm, lock, window)
        _files[norm] = entry
        return entry


def bind(entry: OpenFile, view, window) -> None:
    """Il file preso con `reserve` appartiene ora alla scheda `view` di `window`."""
    with _mutex:
        entry.view = view
        entry.window = window
        _files[entry.path] = entry


def release(entry: OpenFile | None) -> None:
    """Libera il file: in questo processo e su disco (il blocco si cancella)."""
    if entry is None:
        return
    with _mutex:
        if _files.get(entry.path) is entry:
            del _files[entry.path]
        entry.view = None
    if entry.lock is not None:
        entry.lock.release()


def follow(view, window) -> Busy | None:
    """Il documento di `view` ha cambiato percorso (Salva con nome, rinomina, spostamento): il file
    preso passa al nuovo percorso e il vecchio si libera. Busy se il nuovo percorso era già aperto
    altrove: il documento resta senza blocco (i controlli prima di scrivere lo evitano)."""
    path = view.document.path
    norm = platform.normalize_path(path) if path is not None else None
    with _mutex:
        entry = entry_of(view)
        if entry is not None and entry.path == norm:
            return None
        target, result = None, None
        if norm is not None:
            got = find(norm)
            if got is None:
                got = reserve(norm, window)
            if isinstance(got, Busy):
                result = got
            elif got.view is None or got.view is view:      # preso da save_view per questo salvataggio
                target = got
            else:
                result = Busy(entry=got)
        if target is not None:
            bind(target, view, window)
        if entry is not None and entry is not target:
            release(entry)
    return result


def raise_window(window) -> None:
    """Porta in primo piano `window`, se il sistema lo permette (altrimenti il pulsante lampeggia)."""
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
    QApplication.alert(window)


def show(entry: OpenFile):
    """Porta in primo piano la scheda del file e la sua finestra; restituisce la vista (None se il
    file è ancora in apertura: si mostra la finestra che lo sta aprendo)."""
    window = entry.window
    if not _valid(window):
        return None
    if entry.view is not None:
        window.activate_view(entry.view)
    raise_window(window)
    return entry.view


def show_name(name: str) -> bool:
    """Richiesta di un altro processo Foliant (ui/instance.py): mostra la scheda del file con il
    blocco `name`. False se questo processo non ha (più) quel file."""
    entry = next((e for e in entries() if e.name == name), None)
    if entry is None:
        return False
    show(entry)
    return True


def ask_to_show(holder: openlock.Holder | None) -> bool:
    """Chiede al processo Foliant che ha il file di mostrarne la scheda (se il sistema lo permette).
    False se non si sa chi è o non risponde subito (la richiesta resta e arriva quando può)."""
    if holder is None or not holder.pid or holder.pid == os.getpid():
        return False
    from .instance import show_in_process
    return show_in_process(holder.pid, holder.name)


def open_elsewhere_text(names: list[str]) -> str:
    """Messaggio per i file già aperti in un altro processo Foliant (solo a schermo)."""
    if len(names) == 1:
        return _("«{name}» è già aperto in un'altra finestra di Foliant.").format(name=names[0])
    return _("Questi file sono già aperti in un'altra finestra di Foliant:") + "\n\n" + "\n".join(
        "• " + n for n in names)


def in_use_text(path) -> str:
    """Messaggio per una destinazione (Salva con nome, Salva una copia, rinomina) già aperta."""
    return _("«{name}» è aperto in un'altra scheda o finestra di Foliant: chiudilo prima di "
             "sostituirlo, oppure scegli un altro nome.").format(name=os.path.basename(str(path)))
