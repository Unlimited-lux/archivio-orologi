"""Riservatezza del modulo Pagine: nessun percorso o nome di file conservato tra un avvio e l'altro.

- `cleanup_temp_dirs`: a ogni avvio cancella le cartelle temporanee `foliant-pagine-*`
  (pagine trascinate o condivise, con il nome del documento) rimaste da una sessione
  terminata in modo anomalo; solo quelle dell'utente e di processi non più attivi.
- `cleanup_legacy`: una volta sola, toglie il percorso del logo dai modelli di
  intestazione e piè di pagina salvati dalle versioni precedenti.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import tempfile
import time

TEMP_PREFIX = "foliant-pagine-"
CLEANUP_KEY = "pages/privacy_cleanup"
CLEANUP_VERSION = 1
TEMPLATES_KEY = "pages/headerfooter_templates"
LEGACY_GRACE_SECONDS = 3600   # cartelle delle versioni precedenti: tolte dopo un'ora


def temp_prefix() -> str:
    """Prefisso della cartella temporanea di questo processo (contiene il PID)."""
    return f"{TEMP_PREFIX}{os.getpid()}-"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def cleanup_temp_dirs(keep: str | None = None, base: str | None = None) -> list[str]:
    """Cancella le cartelle temporanee rimaste; restituisce quelle tolte."""
    base = base or tempfile.gettempdir()
    removed = []
    uid = os.getuid() if hasattr(os, "getuid") else None
    for path in glob.glob(os.path.join(glob.escape(base), TEMP_PREFIX + "*")):
        if keep and os.path.abspath(path) == os.path.abspath(keep):
            continue
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not os.path.isdir(path) or os.path.islink(path) or (uid is not None and st.st_uid != uid):
            continue
        rest = os.path.basename(path)[len(TEMP_PREFIX):]
        head, sep, _tail = rest.partition("-")
        if sep and head.isdigit():
            if _pid_alive(int(head)):
                continue   # cartella di un'altra istanza di Foliant ancora aperta
        elif time.time() - st.st_mtime < LEGACY_GRACE_SECONDS:
            continue       # formato precedente (senza PID): forse un'istanza ancora aperta
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed.append(path)
    return removed


def scrub_templates(settings) -> None:
    """Toglie i percorsi del logo dai modelli salvati (il logo incorporato resta)."""
    raw = settings.value(TEMPLATES_KEY, "")
    if not raw:
        return
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = None
    if not isinstance(data, dict):
        settings.remove(TEMPLATES_KEY)
        return
    changed = False
    for params in data.values():
        if isinstance(params, dict) and params.get("logo") is not None:
            params["logo"] = None
            changed = True
    if changed:
        settings.setValue(TEMPLATES_KEY, json.dumps(data, ensure_ascii=False))


def cleanup_legacy(settings) -> bool:
    """Pulizia una tantum dei dati delle versioni precedenti; True se eseguita ora."""
    try:
        done = int(settings.value(CLEANUP_KEY, 0) or 0)
    except (TypeError, ValueError):
        done = 0
    if done >= CLEANUP_VERSION:
        return False
    scrub_templates(settings)
    settings.setValue(CLEANUP_KEY, CLEANUP_VERSION)
    return True
