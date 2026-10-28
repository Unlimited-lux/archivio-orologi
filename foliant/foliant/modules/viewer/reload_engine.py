"""Logica pura di «Ripristina e ricarica» (VIS-49): impronta del file su disco,
completezza del file durante la scrittura, decisioni di ricarica."""

from __future__ import annotations

import os
from dataclasses import dataclass

MODE_AUTO = "auto"
MODE_ASK = "ask"
MODE_NEVER = "never"
MODES = (MODE_AUTO, MODE_ASK, MODE_NEVER)

# Esiti di `decide`
RELOAD = "reload"      # ricarica subito mantenendo la posizione
ASK = "ask"            # barra «Il file è cambiato: Ricarica / Ignora»
CONFLICT = "conflict"  # modifiche non salvate: barra con le tre scelte
IGNORE = "ignore"      # nessuna azione


@dataclass(frozen=True)
class FileStamp:
    """Impronta di un file: basta un cambiamento di uno dei campi per dire che è cambiato."""
    exists: bool
    mtime_ns: int = 0
    size: int = 0
    inode: int = 0


MISSING = FileStamp(False)


def file_stamp(path) -> FileStamp:
    try:
        st = os.stat(path)
    except OSError:
        return MISSING
    return FileStamp(True, st.st_mtime_ns, st.st_size, st.st_ino)


def looks_complete(path, tail_size: int = 2048) -> bool:
    """Un PDF scritto per intero termina con «%%EOF» (eventualmente seguito da spazi).
    Un file a metà scrittura di solito no: la ricarica va rimandata.
    Per gli altri formati (immagini, ecc.) basta che il file non sia vuoto."""
    try:
        size = os.path.getsize(path)
        if size == 0:
            return False
        if not str(path).lower().endswith(".pdf"):
            return True
        with open(path, "rb") as f:
            f.seek(max(0, size - tail_size))
            tail = f.read()
    except OSError:
        return False
    return b"%%EOF" in tail


def normalize_mode(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in MODES else MODE_AUTO


def decide(mode: str, modified: bool) -> str:
    """Cosa fare quando il file è cambiato su disco."""
    if modified:
        return CONFLICT
    mode = normalize_mode(mode)
    if mode == MODE_AUTO:
        return RELOAD
    if mode == MODE_ASK:
        return ASK
    return IGNORE


def clamp_page(page: int, page_count: int) -> int:
    """Posizione limitata all'ultima pagina se il documento ora ne ha meno."""
    if page_count <= 0:
        return 0
    return min(max(0, int(page)), page_count - 1)
