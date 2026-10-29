"""Regola di riservatezza del viewer: pulizia all'avvio dei dati delle versioni precedenti.

Foliant non conserva su disco, tra un avvio e l'altro, nomi, percorsi o cartelle dei file
aperti, né cronologie o indici che li contengano. Le versioni precedenti del viewer salvavano:

- in QSettings: posizione per file (`viewer/positions`), sessione (`viewer/session`,
  `viewer/restore_session`), cronologia delle ricerche (`viewer/search_history`) e le
  preferenze ormai senza senso `viewer/no_history` e `viewer/restore_position`;
- gli indici full-text in `$XDG_DATA_HOME/foliant/indici/*.sqlite`;
- nome e percorso del documento nei metadati delle copie di recupero;
- la voce `foliant.thumbnailer` per il file manager (mai installata da Foliant stesso).

La pulizia è idempotente e non scrive marcatori: a ogni avvio controlla se le chiavi e i
file delle versioni precedenti esistono e cancella solo quelli (su un profilo nuovo o già
pulito non scrive nulla e non sincronizza le impostazioni). Il vecchio marcatore
`viewer/privacy_cleanup`, se presente, viene tolto anch'esso. Ogni avvio si ripuliscono
inoltre i metadati di recupero e le cartelle temporanee abbandonate dopo un crash
(`foliant-share-*`, `foliant-allegato-*`), che contengono il nome del documento. La pulizia
è silenziosa, tocca solo chiavi e file noti di Foliant e non solleva mai eccezioni.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

from foliant.core import platform

log = logging.getLogger("foliant.modules.viewer")

# marcatore scritto dalle versioni precedenti della pulizia: ora si cancella e non si scrive più
CLEANUP_FLAG = "viewer/privacy_cleanup"

LEGACY_KEYS = (
    "viewer/positions",
    "viewer/session",
    "viewer/restore_session",
    "viewer/search_history",
    "viewer/no_history",
    "viewer/restore_position",
    CLEANUP_FLAG,
)

TEMP_PREFIXES = ("foliant-share-", "foliant-allegato-")
TEMP_MAX_AGE = 6 * 3600      # cartelle senza pid più recenti: forse di un'altra istanza aperta


def remove_legacy_settings(settings) -> list[str]:
    """Toglie da QSettings le chiavi delle versioni precedenti; restituisce quelle trovate."""
    found = []
    try:
        existing = settings.allKeys()
    except Exception:  # noqa: BLE001
        existing = []
    for key in LEGACY_KEYS:
        # la chiave stessa o un gruppo con quel nome (es. `viewer/positions/<...>`)
        if settings.contains(key) or any(k.startswith(key + "/") for k in existing):
            found.append(key)
            settings.remove(key)
    return found


def _owner_pid(name: str) -> int | None:
    """Pid del processo che ha creato la cartella (`foliant-share-<pid>-xxxx`), se indicato."""
    for prefix in TEMP_PREFIXES:
        if name.startswith(prefix):
            head = name[len(prefix):].split("-", 1)
            if len(head) == 2 and head[0].isdigit():
                return int(head[0])
    return None


def _pid_alive(pid: int) -> bool:
    """True se esiste un processo con questo pid (su Windows senza os.kill, che lo terminerebbe)."""
    return platform.pid_alive(pid)


def remove_orphan_temp_dirs(tmpdir: str | None = None, now: float | None = None,
                            max_age: float = TEMP_MAX_AGE) -> int:
    """Cancella le cartelle temporanee del viewer abbandonate (solo di questo utente).

    Una cartella col pid di un processo terminato si cancella subito, quella di un processo
    vivo mai; senza pid (versioni precedenti) solo se più vecchia di `max_age`."""
    base = Path(tmpdir or tempfile.gettempdir())
    now = time.time() if now is None else now
    uid = os.getuid() if hasattr(os, "getuid") else None
    removed = 0
    try:
        candidates = [p for p in base.iterdir() if p.name.startswith(TEMP_PREFIXES)]
    except OSError:
        return 0
    for p in candidates:
        try:
            st = p.lstat()
            if p.is_symlink() or not p.is_dir():
                continue
            if uid is not None and st.st_uid != uid:
                continue
            pid = _owner_pid(p.name)
            if pid is not None:
                if pid == os.getpid() or _pid_alive(pid):
                    continue                 # di un'istanza aperta: le cancella il suo aboutToQuit
            elif now - st.st_mtime < max_age:
                continue                     # senza pid e recente: forse di un'altra istanza aperta
            shutil.rmtree(p, ignore_errors=True)
            if not p.exists():
                removed += 1
        except OSError:
            continue
    return removed


def legacy_thumbnailer_paths(env=None) -> list[Path]:
    """Dove una versione precedente avrebbe installato la voce del generatore di miniature."""
    env = os.environ if env is None else env
    base = platform.data_home(env)
    return [Path(base) / "thumbnailers" / "foliant.thumbnailer"]


def remove_legacy_thumbnailer(env=None) -> int:
    """Toglie `foliant.thumbnailer` solo se il contenuto è esattamente quello di Foliant."""
    from .thumbnailer import THUMBNAILER_ENTRY
    removed = 0
    for p in legacy_thumbnailer_paths(env):
        try:
            if p.is_file() and not p.is_symlink() and p.read_text(encoding="utf-8") == THUMBNAILER_ENTRY:
                p.unlink()
                removed += 1
        except (OSError, UnicodeDecodeError):
            continue
    return removed


def sanitize_recovery(env=None) -> int:
    from . import recovery_engine
    return recovery_engine.sanitize_legacy(recovery_engine.recovery_dir(env))


def run_once(settings, env=None) -> bool:
    """Cancella i dati delle versioni precedenti, se ci sono; True se ha tolto qualcosa.

    Idempotente e senza marcatori: se non trova nulla non scrive nelle impostazioni e non le
    sincronizza (un profilo nuovo resta senza file delle impostazioni)."""
    from . import fulltext
    removed_keys = remove_legacy_settings(settings)
    removed_files = fulltext.remove_legacy_indexes(env) + remove_legacy_thumbnailer(env)
    if removed_keys:
        try:
            settings.sync()
        except Exception:  # noqa: BLE001
            pass
    return bool(removed_keys or removed_files)


def startup_cleanup(settings, env=None) -> None:
    """Pulizia all'avvio: dati delle versioni precedenti (se ci sono) e resti di un crash."""
    for step in (lambda: run_once(settings, env), lambda: sanitize_recovery(env), remove_orphan_temp_dirs):
        try:
            step()
        except Exception:  # noqa: BLE001 - la pulizia non deve mai impedire l'avvio
            log.warning("viewer: privacy cleanup step failed")   # senza dettagli: niente percorsi
