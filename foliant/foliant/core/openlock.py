"""Blocco dei file aperti tra processi Foliant: lo stesso file non si apre in due processi.

Ogni file aperto in una scheda ha un file di blocco (`QLockFile`) nella cartella privata `folder()`
(`platform.runtime_dir()`/foliant-locks, 0700). Il blocco dice quale processo lo tiene (PID) e si
cancella quando il file si chiude; all'uscita si cancellano anche quelli dimenticati (`atexit`).
Il blocco di un processo terminato male (crash) non ferma nessuno: `QLockFile` controlla che il
processo sia vivo e sia lo stesso programma e, in più, ogni blocco altrui si prova a togliere, cosa
che il sistema impedisce finché il processo che lo tiene è vivo (blocco nativo su POSIX, file aperto
senza condivisione su Windows): così non inganna nemmeno un PID riusato.

Riservatezza (nessun nome o percorso su disco, nemmeno mentre il file è aperto): il nome del blocco
è l'HMAC-SHA256 del percorso normalizzato (`platform.normalize_path`) con una chiave casuale di 32
byte conservata nella stessa cartella (0600). Senza la chiave il nome non dice nulla e non si può
confrontare con l'impronta di un percorso noto; la chiave si rinnova quando non resta nessun blocco
(nessun file aperto in nessun processo), così i nomi non si ripetono da una sessione di lavoro
all'altra. Il contenuto del blocco è quello di `QLockFile`: PID, nome del programma e del computer.

Il file PDF non viene aperto né bloccato: altri programmi lo leggono e il salvataggio lo sostituisce
(`platform.replace_file`) come prima. Il registro dei file aperti nel processo (schede e finestre) è
in ui/open_files.py.
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QLockFile

from . import platform

FOLDER_NAME = "foliant-locks"
KEY_NAME = "chiave"                 # 32 byte casuali
GUARD_NAME = "chiave.guard"         # platform.file_lock: chiave e blocchi si leggono e creano uno alla volta
LOCK_SUFFIX = ".lock"
KEY_BYTES = 32
FRESH_SECONDS = 2.0                 # un blocco ancora senza contenuto più recente può essere in scrittura

_mutex = threading.RLock()
_held: dict[str, "FileLock"] = {}           # file di blocco → blocco tenuto da questo processo
_folders: dict[tuple, Path | None] = {}     # cartella per ambiente (i test cambiano XDG_RUNTIME_DIR)


@dataclass(frozen=True)
class Holder:
    """Processo che tiene il blocco di un file (vedi `acquire`, `holder`)."""

    pid: int        # 0 = non noto (blocco appena creato, ancora senza contenuto)
    name: str       # nome del blocco: per chiedere a quel processo di mostrare il file (ui/instance.py)


class FileLock:
    """Blocco di un file tenuto da questo processo (vedi `acquire`)."""

    def __init__(self, name: str, path: Path, lock: QLockFile):
        self.name = name                # HMAC del percorso del file aperto
        self.path = str(path)           # file di blocco
        self._lock: QLockFile | None = lock
        self._pid = os.getpid()

    def release(self) -> None:
        """Libera il file: il blocco si cancella dal disco. Si può chiamare più volte."""
        with _mutex:
            lock, self._lock = self._lock, None
            if _held.get(self.path) is self:
                del _held[self.path]
        # un processo figlio (fork) non cancella i blocchi del padre
        if lock is not None and os.getpid() == self._pid:
            lock.unlock()


def folder() -> Path | None:
    """Cartella dei blocchi (creata se manca), o None senza una cartella privata: allora i file si
    controllano solo dentro il processo (ui/open_files.py)."""
    env = tuple(os.environ.get(v) for v in ("XDG_RUNTIME_DIR", "LOCALAPPDATA", "FOLIANT_PORTABLE_DIR"))
    with _mutex:
        cached = _folders.get(env, False)
        if cached is None or (cached and cached.is_dir()):
            return cached
        base = platform.runtime_dir()
        if base is None and not platform.IS_WINDOWS:
            # come il canale dell'istanza singola: la cartella di runtime di Qt (0700), chiesta solo
            # se serve (senza XDG_RUNTIME_DIR Qt lo segnala sul terminale a ogni chiamata)
            from PySide6.QtCore import QStandardPaths
            base = platform.runtime_dir([QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.RuntimeLocation)])
        path = None
        if base is not None:
            path = base / FOLDER_NAME
            try:
                platform.make_private_dir(path)
            except OSError:
                path = None
            if path is not None and not platform.private_dir(path):
                path = None
        _folders[env] = path
        return path


def _qlock(path) -> QLockFile:
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)        # mai «vecchio» per l'età: un file resta aperto anche per ore
    return lock


def _name(key: bytes, path: str) -> str:
    return hmac.new(key, os.fsencode(path), hashlib.sha256).hexdigest()


def _read_key(base: Path, renew: bool) -> bytes:
    """Chiave HMAC della cartella; da chiamare con il blocco `GUARD_NAME` tenuto. Con `renew` (nessun
    blocco nella cartella) se ne crea una nuova. Scrittura atomica: un'interruzione lascia la vecchia."""
    path = base / KEY_NAME
    if not renew:
        try:
            data = path.read_bytes()
            if len(data) == KEY_BYTES:
                return data
        except OSError:
            pass
    data = secrets.token_bytes(KEY_BYTES)
    tmp = base / f"{KEY_NAME}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    platform.make_private_file(tmp)
    platform.replace_file(tmp, path)
    return data


def _other_holder(lock: QLockFile, path: Path, name: str) -> Holder | None:
    """Chi tiene il blocco `path` (non di questo processo), o None se è libero: il blocco di un
    processo terminato si cancella. Il sistema impedisce di cancellare quello di un processo vivo."""
    pid, _host, _app = lock.getLockInfo()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None                                 # già tolto
    if not pid and age < FRESH_SECONDS:
        return Holder(0, name)                      # appena creato: il contenuto sta per arrivare
    if lock.removeStaleLockFile():
        return None
    return Holder(pid if pid and platform.pid_alive(pid) else 0, name)


def _sweep(base: Path) -> bool:
    """Toglie i blocchi rimasti da processi terminati male; True se ne resta qualcuno."""
    remaining = any(os.path.dirname(p) == str(base) for p in _held)
    try:
        entries = list(os.scandir(base))
    except OSError:
        return remaining
    for entry in entries:
        if not entry.name.endswith(LOCK_SUFFIX):
            continue
        name = entry.name[:-len(LOCK_SUFFIX)]
        if entry.path in _held or _other_holder(_qlock(entry.path), Path(entry.path), name) is not None:
            remaining = True
    return remaining


def acquire(path: str | os.PathLike) -> FileLock | Holder | None:
    """Prende il blocco del file `path` per questo processo.

    Restituisce il `FileLock`; `Holder` se il file è già aperto da un altro processo Foliant vivo
    (o da questo, se lo tiene già: il registro delle schede dovrebbe averlo visto prima); None se non
    c'è una cartella privata o non vi si può scrivere (nessun controllo tra processi: aprire un file
    non deve mai fallire per il blocco). Il blocco rimasto da un processo terminato male si toglie
    e si prende."""
    base = folder()
    if base is None:
        return None
    norm = platform.normalize_path(path)
    try:
        with _mutex, platform.file_lock(base / GUARD_NAME):
            key = _read_key(base, renew=not _sweep(base))
            name = _name(key, norm)
            target = base / (name + LOCK_SUFFIX)
            if str(target) in _held:
                return Holder(os.getpid(), name)
            lock = _qlock(target)
            for _attempt in range(2):
                if lock.tryLock(0):
                    own = FileLock(name, target, lock)
                    _held[own.path] = own
                    return own
                if lock.error() != QLockFile.LockError.LockFailedError:
                    return None                     # permessi, disco pieno…: niente blocco
                other = _other_holder(lock, target, name)
                if other is not None:
                    return other
            return Holder(0, name)                  # ripreso da un altro processo nel frattempo
    except OSError:
        return None


def holder(path: str | os.PathLike) -> Holder | None:
    """Altro processo Foliant che ha aperto il file `path`, o None se è libero, se lo ha aperto
    questo processo o senza cartella privata. Solo controllo: non prende il blocco."""
    base = folder()
    if base is None:
        return None
    norm = platform.normalize_path(path)
    try:
        with _mutex, platform.file_lock(base / GUARD_NAME):
            if not (base / KEY_NAME).exists():
                return None                         # nessuna chiave: nessun blocco
            name = _name(_read_key(base, renew=False), norm)
            target = base / (name + LOCK_SUFFIX)
            if str(target) in _held or not target.exists():
                return None
            return _other_holder(_qlock(target), target, name)
    except OSError:
        return None


def release_all() -> None:
    """Libera tutti i blocchi di questo processo (uscita)."""
    with _mutex:
        locks = list(_held.values())
    for lock in locks:
        lock.release()


atexit.register(release_all)
