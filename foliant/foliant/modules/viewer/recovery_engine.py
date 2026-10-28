"""Logica pura del salvataggio automatico e del recupero dopo crash.

Ogni documento modificato ha nella cartella di recupero due file:

- `<id>.pdf`: i dati del documento (con la stessa protezione del file originale);
- `<id>.json`: data/ora, pagina corrente, pid del processo che l'ha scritta e
  versione del formato.

Riservatezza: nei metadati non ci sono né il nome né il percorso del file
originale; al ripristino la copia si apre come «Documento recuperato» da salvare
con «Salva con nome». `sanitize_legacy` toglie nome e percorso dai metadati
scritti dalle versioni precedenti.

Una voce è "orfana" (cioè proponibile per il ripristino) quando il processo che
l'ha scritta non è più in esecuzione. Nessuna funzione qui tocca l'interfaccia.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

import pymupdf

from foliant.api import ENGINE_LOCK, _, atomic_write

FORMAT_VERSION = 1
DIR_MODE = 0o700
FILE_MODE = 0o600
# campi che le versioni precedenti scrivevano nei metadati (nome e percorso del file)
LEGACY_PRIVATE_FIELDS = ("original_path", "display_name")
TEMP_MAX_AGE = 3600          # temporanei di atomic_write più vecchi di così: abbandonati


def recovery_dir(env: Mapping[str, str] | None = None) -> Path:
    """Cartella di recupero: $FOLIANT_RECOVERY_DIR, poi $XDG_STATE_HOME, poi ~/.local/state."""
    env = os.environ if env is None else env
    explicit = env.get("FOLIANT_RECOVERY_DIR")
    if explicit:
        return Path(explicit).expanduser()
    state = env.get("XDG_STATE_HOME")
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return base / "foliant" / "recovery"


def ensure_dir(directory: Path) -> Path:
    """Crea la cartella (permessi 0700) se manca e restituisce il percorso."""
    directory.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    try:
        os.chmod(directory, DIR_MODE)
    except OSError:
        pass
    return directory


def new_entry_id() -> str:
    return uuid.uuid4().hex


def pid_alive(pid: int) -> bool:
    """True se esiste un processo con questo pid (anche di un altro utente)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
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
    except (OverflowError, OSError):
        return False
    return True


def make_meta(*, page: int, pid: int | None = None, timestamp: float | None = None) -> dict:
    """Metadati di una copia: niente nome né percorso del documento (riservatezza)."""
    return {
        "format": FORMAT_VERSION,
        "timestamp": time.time() if timestamp is None else timestamp,
        "page": int(page),
        "pid": os.getpid() if pid is None else int(pid),
    }


def _write_private(target: Path, data: bytes) -> None:
    atomic_write(target, data)
    try:
        os.chmod(target, FILE_MODE)
    except OSError:
        pass


def write_entry(directory: Path, entry_id: str, data: bytes | None, meta: dict) -> None:
    """Scrive (in modo atomico) i dati e i metadati di una voce.

    Con `data` None si riscrivono solo i metadati (es. per rivendicare una voce)."""
    ensure_dir(directory)
    if data is not None:
        _write_private(directory / f"{entry_id}.pdf", data)
    _write_private(directory / f"{entry_id}.json",
                   json.dumps(meta, ensure_ascii=False, indent=1).encode("utf-8"))


def remove_entry(directory: Path, entry_id: str) -> None:
    for suffix in (".pdf", ".json"):
        try:
            (directory / f"{entry_id}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def read_meta(path: Path) -> dict | None:
    """Metadati di una voce, o None se il file è illeggibile o non valido."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    try:
        meta["page"] = int(meta.get("page", 0) or 0)
        meta["pid"] = int(meta.get("pid", 0) or 0)
        meta["timestamp"] = float(meta.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return None
    for key in LEGACY_PRIVATE_FIELDS:      # campi delle versioni precedenti: ignorati
        meta.pop(key, None)
    return meta


def sanitize_legacy(directory: Path, alive: Callable[[int], bool] = pid_alive,
                    now: float | None = None) -> int:
    """Toglie nome e percorso del documento dai metadati scritti dalle versioni precedenti.

    - ogni `<id>.json` che contiene `original_path` o `display_name` viene riscritto
      (atomico, 0600) solo con formato, pid, data/ora e pagina: la copia resta recuperabile;
    - i `.json` senza `.pdf` di processi non più in esecuzione vengono eliminati;
    - i temporanei di `atomic_write` (`.foliant-*.tmp`) abbandonati da più di un'ora vengono eliminati.
    Non tocca altri file. Restituisce il numero di file riscritti o eliminati; non solleva mai."""
    changed = 0
    try:
        if not directory.is_dir():
            return 0
        now = time.time() if now is None else now
        for path in list(directory.iterdir()):
            try:
                if not path.is_file() or path.is_symlink():
                    continue
                name = path.name
                if name.startswith(".foliant-") and name.endswith(".tmp"):
                    if now - path.stat().st_mtime > TEMP_MAX_AGE:
                        path.unlink()
                        changed += 1
                    continue
                if name.startswith(".") or path.suffix != ".json":
                    continue
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, UnicodeDecodeError):
                    raw = None
                has_pdf = (directory / f"{path.stem}.pdf").exists()
                if not has_pdf:
                    pid = raw.get("pid", 0) if isinstance(raw, dict) else 0
                    if not alive(pid):
                        path.unlink()
                        changed += 1
                    continue
                if not isinstance(raw, dict):
                    if any(k.encode() in path.read_bytes() for k in LEGACY_PRIVATE_FIELDS):
                        path.unlink()      # illeggibile ma con nome/percorso: il PDF resta proponibile
                        changed += 1
                    continue
                if not any(k in raw for k in LEGACY_PRIVATE_FIELDS):
                    continue
                clean = {k: v for k, v in raw.items() if k not in LEGACY_PRIVATE_FIELDS}
                _write_private(path, json.dumps(clean, ensure_ascii=False, indent=1).encode("utf-8"))
                changed += 1
            except OSError:
                continue
    except OSError:
        pass
    return changed


def pdf_is_valid(path: Path) -> bool:
    """True se il file è un PDF apribile (anche cifrato)."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if not data:
        return False
    with ENGINE_LOCK:
        try:
            pdf = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return False
        try:
            return bool(pdf.needs_pass) or pdf.page_count > 0
        finally:
            pdf.close()


@dataclass
class RecoveryEntry:
    id: str
    pdf_path: Path
    json_path: Path
    meta: dict | None = None
    extra: dict = field(default_factory=dict)

    @property
    def meta_valid(self) -> bool:
        return self.meta is not None

    @property
    def original_path(self) -> str:
        """Sempre vuoto: le copie non conservano il percorso del documento (riservatezza)."""
        return ""

    @property
    def display_name(self) -> str:
        """Sempre «Documento recuperato»: le copie non conservano il nome del documento."""
        return _("Documento recuperato")

    @property
    def timestamp(self) -> float:
        if self.meta and self.meta.get("timestamp"):
            return float(self.meta["timestamp"])
        try:
            return self.pdf_path.stat().st_mtime
        except OSError:
            return 0.0

    @property
    def page(self) -> int:
        return int((self.meta or {}).get("page", 0) or 0)

    def read_data(self) -> bytes:
        return self.pdf_path.read_bytes()


def list_entries(directory: Path) -> list[RecoveryEntry]:
    """Tutte le voci presenti (con il PDF), lette dalla cartella."""
    if not directory.is_dir():
        return []
    entries = []
    for pdf in sorted(directory.glob("*.pdf")):
        entry_id = pdf.stem
        if entry_id.startswith("."):
            continue  # temporanei di atomic_write
        js = directory / f"{entry_id}.json"
        entries.append(RecoveryEntry(entry_id, pdf, js, read_meta(js) if js.exists() else None))
    return entries


def orphan_entries(directory: Path, alive: Callable[[int], bool] = pid_alive,
                   cleanup: bool = True) -> list[RecoveryEntry]:
    """Voci lasciate da processi non più in esecuzione, dalla più recente.

    - voce di un processo vivo → ignorata (appartiene a un'altra istanza);
    - metadati illeggibili → proponibile se il PDF è valido, altrimenti eliminata;
    - metadati senza PDF di un processo morto → eliminati (con `cleanup`).
    """
    if not directory.is_dir():
        return []
    result = []
    for entry in list_entries(directory):
        if entry.meta is not None:
            if alive(entry.meta.get("pid", 0)):
                continue
            if pdf_is_valid(entry.pdf_path):
                result.append(entry)
            elif cleanup:
                remove_entry(directory, entry.id)
            continue
        if pdf_is_valid(entry.pdf_path):
            result.append(entry)
        elif cleanup:
            remove_entry(directory, entry.id)
    if cleanup:
        for js in directory.glob("*.json"):
            if js.name.startswith(".") or (directory / f"{js.stem}.pdf").exists():
                continue
            meta = read_meta(js)
            if meta is None or not alive(meta.get("pid", 0)):
                try:
                    js.unlink()
                except OSError:
                    pass
    result.sort(key=lambda e: e.timestamp, reverse=True)
    return result
