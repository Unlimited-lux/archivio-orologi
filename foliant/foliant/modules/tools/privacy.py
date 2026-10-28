"""Riservatezza: pulizia all'avvio dei dati delle versioni precedenti del modulo Strumenti.

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


def base_dir(settings) -> str:
    """Cartella del file delle impostazioni (dove le versioni precedenti scrivevano)."""
    try:
        name = settings.fileName()
    except Exception:
        name = ""
    return os.path.dirname(name) if name else ""


def _unlink(path: str) -> bool:
    try:
        if os.path.isfile(path) and not os.path.islink(path):
            os.unlink(path)
            return True
    except OSError:
        pass
    return False


def _rmdir_if_empty(path: str) -> bool:
    try:
        if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
            os.rmdir(path)
            return True
    except OSError:
        pass
    return False


def remove_legacy_files(base: str) -> list[str]:
    """Cancella i file delle versioni precedenti con percorsi dei file elaborati e ripulisce
    le sequenze; restituisce i file toccati (vuoto se non c'era nulla da fare)."""
    if not base or not os.path.isdir(base):
        return []
    from . import engine as be
    touched = []
    state = os.path.join(base, "foliant-monitor")                     # registro dei file elaborati
    history = os.path.join(base, "foliant-rapporti")                  # cronologia dei rapporti
    candidates = [os.path.join(base, "foliant-monitor.json"),         # cartelle monitorate
                  os.path.join(base, "foliant-lavoro-in-corso.json")]  # giornale del lavoro in corso
    candidates += glob.glob(os.path.join(glob.escape(state), "monitor-*.json"))
    candidates += glob.glob(os.path.join(glob.escape(history), "lavoro_*.json"))
    touched += [p for p in candidates if _unlink(p)]
    touched += [d for d in (state, history) if _rmdir_if_empty(d)]
    sequences = os.path.join(base, "foliant-sequenze")                # sequenze: si tengono, senza percorsi
    touched += [p for p in glob.glob(os.path.join(glob.escape(sequences), "*.json"))
                if be.scrub_sequence_file(p)]
    return touched


def cleanup_legacy(settings) -> bool:
    """Pulizia all'avvio, silenziosa e idempotente: agisce solo se trova dati delle versioni
    precedenti (quindi, in pratica, una volta sola) e non scrive nulla nelle impostazioni.
    Restituisce True se ha cancellato o ripulito qualcosa."""
    return bool(remove_legacy_files(base_dir(settings)))
