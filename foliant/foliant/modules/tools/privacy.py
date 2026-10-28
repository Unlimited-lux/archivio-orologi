"""Riservatezza: pulizia una tantum dei dati delle versioni precedenti del modulo Strumenti.

Le versioni precedenti conservavano nel profilo (accanto al file delle impostazioni)
percorsi di file e cartelle: configurazione e registro delle cartelle monitorate,
giornale del lavoro in corso, cronologia dei rapporti e cartelle di destinazione o file
dei parametri dentro le sequenze salvate. Ora tutto questo resta solo in memoria; qui si
cancellano (o, per le sequenze, si ripuliscono) i file noti lasciati dalle versioni
precedenti. Si toccano solo nomi di file di Foliant, mai altro.
"""

from __future__ import annotations

import glob
import os

CLEANUP_KEY = "tools/privacy_cleanup"
CLEANUP_VERSION = 1


def base_dir(settings) -> str:
    """Cartella del file delle impostazioni (dove le versioni precedenti scrivevano)."""
    try:
        name = settings.fileName()
    except Exception:
        name = ""
    return os.path.dirname(name) if name else ""


def _unlink(path: str) -> None:
    try:
        if os.path.isfile(path) and not os.path.islink(path):
            os.unlink(path)
    except OSError:
        pass


def _rmdir_if_empty(path: str) -> None:
    try:
        if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


def remove_legacy_files(base: str) -> None:
    """Cancella i file delle versioni precedenti con percorsi dei file elaborati."""
    if not base or not os.path.isdir(base):
        return
    from . import engine as be
    _unlink(os.path.join(base, "foliant-monitor.json"))               # cartelle monitorate
    state = os.path.join(base, "foliant-monitor")                     # registro dei file elaborati
    for path in glob.glob(os.path.join(glob.escape(state), "monitor-*.json")):
        _unlink(path)
    _rmdir_if_empty(state)
    _unlink(os.path.join(base, "foliant-lavoro-in-corso.json"))       # giornale del lavoro in corso
    history = os.path.join(base, "foliant-rapporti")                  # cronologia dei rapporti
    for path in glob.glob(os.path.join(glob.escape(history), "lavoro_*.json")):
        _unlink(path)
    _rmdir_if_empty(history)
    sequences = os.path.join(base, "foliant-sequenze")                # sequenze: si tengono, senza percorsi
    for path in glob.glob(os.path.join(glob.escape(sequences), "*.json")):
        be.scrub_sequence_file(path)


def cleanup_legacy(settings) -> bool:
    """Pulizia una tantum all'avvio. Restituisce True se è stata eseguita ora."""
    try:
        done = int(settings.value(CLEANUP_KEY, 0) or 0)
    except (TypeError, ValueError):
        done = 0
    if done >= CLEANUP_VERSION:
        return False
    remove_legacy_files(base_dir(settings))
    settings.setValue(CLEANUP_KEY, CLEANUP_VERSION)
    return True
