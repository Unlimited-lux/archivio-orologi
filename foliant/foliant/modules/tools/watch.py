"""Cartelle monitorate (BAT-21): i file copiati in una cartella vengono elaborati
automaticamente con un'operazione o una sequenza.

Logica pura (scansione periodica, file «stabili», elaborazione, registro) più un
piccolo servizio Qt che la richiama con un timer ed elabora in un thread.

Riservatezza: configurazioni, file già elaborati e registro restano solo in memoria
finché Foliant è aperto; nulla viene scritto nel profilo. Per riusare i monitoraggi in
un'altra sessione l'utente li esporta (e reimporta) in un file scelto da lui.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from foliant.api import DocumentError, _, atomic_write

from . import engine as be

ORIGINALS_DIR = "originali"
ERRORS_DIR = "errori"


@dataclass
class WatchConfig:
    folder: str
    steps: list[be.Step]
    destination: str
    extensions: tuple[str, ...] = (".pdf",)
    originals: str = "keep"          # "keep" | "move" (nella sottocartella «originali»)
    stable_seconds: float = 5.0
    pattern: str = "{nome}"
    conflict: str = "rename"
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {"id": self.id, "folder": self.folder, "steps": [s.to_dict() for s in self.steps],
                "destination": self.destination, "extensions": list(self.extensions), "originals": self.originals,
                "stable_seconds": self.stable_seconds, "pattern": self.pattern, "conflict": self.conflict,
                "enabled": self.enabled}

    @classmethod
    def from_dict(cls, d: dict) -> "WatchConfig":
        return cls(folder=d["folder"], steps=[be.Step.from_dict(s) for s in d.get("steps", [])],
                   destination=d.get("destination", ""), extensions=tuple(d.get("extensions") or (".pdf",)),
                   originals=d.get("originals", "keep"), stable_seconds=float(d.get("stable_seconds", 5.0)),
                   pattern=d.get("pattern", "{nome}"), conflict=d.get("conflict", "rename"),
                   enabled=bool(d.get("enabled", True)), id=d.get("id") or uuid.uuid4().hex[:12])

    def label(self) -> str:
        return ", ".join(be.action_label(s.action) for s in self.steps)


def validate_watch(cfg: WatchConfig) -> list[str]:
    errors = []
    if not cfg.folder or not os.path.isdir(cfg.folder):
        errors.append(_("La cartella da monitorare non esiste."))
    if not cfg.steps:
        errors.append(_("Scegli almeno un'operazione."))
    errors.extend(be.validate_sequence(cfg.steps))
    if any(be.ACTIONS.get(s.action) and be.ACTIONS[s.action].kind in ("merge", "print") for s in cfg.steps):
        errors.append(_("Unione e stampa non sono ammesse per una cartella monitorata."))
    for s in cfg.steps:
        spec = be.ACTIONS.get(s.action)
        if spec and any(p.secret and not s.params.get(p.key) for p in spec.params) and s.action in (
                "protect", "remove_password"):
            errors.append(_("«{a}» richiede password: non è ammessa per una cartella monitorata.").format(
                a=be.action_label(s.action)))
    if not cfg.destination:
        errors.append(_("Scegli la cartella di destinazione."))
    elif cfg.folder:
        folder = os.path.realpath(cfg.folder)
        dest = os.path.realpath(cfg.destination)
        if dest == folder or dest.startswith(folder + os.sep):
            errors.append(_("La destinazione non può essere la cartella monitorata o una sua sottocartella."))
    return errors


WATCHES_FORMAT = "foliant-watches"


def export_watches(path: str, configs: list[WatchConfig]) -> None:
    """Esporta le configurazioni in un file scelto dall'utente (senza password)."""
    data = {"format": WATCHES_FORMAT, "version": 1, "watches": [c.to_dict() for c in configs]}   # Step senza segreti
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


def import_watches(path: str) -> list[WatchConfig]:
    """Configurazioni da un file esportato con `export_watches` (nuovi identificativi)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise DocumentError(_("Configurazione non leggibile: {f}").format(f=os.path.basename(path))) from e
    items = data.get("watches") if isinstance(data, dict) and data.get("format") == WATCHES_FORMAT else None
    if not isinstance(items, list):
        raise DocumentError(_("Il file non contiene cartelle monitorate di Foliant."))
    out = []
    for d in items:
        try:
            cfg = WatchConfig.from_dict({k: v for k, v in d.items() if k != "id"})
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
        out.append(cfg)
    return out


class FolderWatcher:
    """Monitoraggio di una cartella con scansione periodica (non ricorsiva)."""

    LOG_LIMIT = 1000

    def __init__(self, cfg: WatchConfig, clock=time.monotonic):
        self.cfg = cfg
        self.clock = clock
        # tutto solo in memoria (riservatezza): sparisce alla chiusura di Foliant
        self._seen: dict[str, tuple[int, float]] = {}      # percorso -> (dimensione, da quando è stabile)
        self.processed: dict[str, list] = {}               # percorso -> [dimensione, mtime] già elaborati
        self.log: list[dict] = []
        self.status = "listening"
        self.last_error = ""

    @property
    def processed_count(self) -> int:
        return sum(1 for e in self.log if e.get("status") in ("ok", "warning"))

    # ---------------------------------------------------------------- scansione
    def scan(self) -> list[str]:
        """File pronti: estensione ammessa, non ancora elaborati, dimensione invariata
        da almeno `stable_seconds` (copia completata)."""
        folder = self.cfg.folder
        if not os.path.isdir(folder):
            self.status = "unreachable"
            return []
        if self.status == "unreachable":
            self.status = "listening"
        now = self.clock()
        ready = []
        try:
            names = sorted(os.listdir(folder), key=be.natural_key)
        except OSError:
            self.status = "unreachable"
            return []
        present = set()
        for name in names:
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or name.startswith(".") or not name.lower().endswith(self.cfg.extensions):
                continue
            present.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            done = self.processed.get(path)
            if done and done[0] == st.st_size and abs(done[1] - st.st_mtime) < 1e-6:
                continue
            size, since = self._seen.get(path, (-1, now))
            if size != st.st_size:
                self._seen[path] = (st.st_size, now)
                if self.cfg.stable_seconds <= 0:
                    ready.append(path)
                continue
            if now - since >= self.cfg.stable_seconds:
                ready.append(path)
        for gone in set(self._seen) - present:
            self._seen.pop(gone, None)
        return ready

    # ---------------------------------------------------------------- elaborazione
    def process(self, path: str, progress=None) -> be.FileResult:
        self.status = "processing"
        item = be.analyze_file(path)
        settings = be.JobSettings(steps=list(self.cfg.steps), destination=self.cfg.destination,
                                  pattern=self.cfg.pattern, conflict=self.cfg.conflict)
        try:
            st = os.stat(path)
            os.makedirs(self.cfg.destination, exist_ok=True)
            report = be.run_job(progress, [item], settings)
            res = report.results[0]
        except Exception as e:   # il monitoraggio non deve mai fermarsi
            res = be.FileResult(path, status="error", code="GEN-01", message=str(e))
            st = None
        self._seen.pop(path, None)
        if res.status in ("ok", "warning"):
            if self.cfg.originals == "move":
                _move_into(path, os.path.join(self.cfg.folder, ORIGINALS_DIR))
            elif st is not None:
                self.processed[path] = [st.st_size, st.st_mtime]
        else:
            self.last_error = f"{os.path.basename(path)}: {res.message}"
            _move_into(path, os.path.join(self.cfg.folder, ERRORS_DIR))
        self.log.append({"time": datetime.now().isoformat(timespec="seconds"), "input": path,
                         "outputs": res.outputs, "status": res.status, "code": res.code, "message": res.message})
        del self.log[:-self.LOG_LIMIT]
        self.status = "listening"
        return res

    def poll(self, progress=None) -> list[be.FileResult]:
        return [self.process(p, progress) for p in self.scan()]

    def report(self) -> be.JobReport:
        """Rapporto cumulativo (esportabile come quello di BAT-05)."""
        results = [be.FileResult(e["input"], list(e.get("outputs") or []), e.get("status", ""), e.get("code", ""),
                                 e.get("message", "")) for e in self.log]
        started = datetime.fromisoformat(self.log[0]["time"]) if self.log else datetime.now()
        return be.JobReport(results, list(self.cfg.steps), started, datetime.now())

    def status_text(self) -> str:
        labels = {"listening": _("in ascolto"), "processing": _("in elaborazione"),
                  "unreachable": _("non raggiungibile")}
        text = labels.get(self.status, self.status) if self.cfg.enabled else _("disattivata")
        text += " · " + _("{n} elaborati").format(n=self.processed_count)
        if self.last_error:
            text += " · " + _("ultimo errore: {e}").format(e=self.last_error)
        return text


def _move_into(path: str, directory: str) -> str | None:
    try:
        os.makedirs(directory, exist_ok=True)
        target = os.path.join(directory, os.path.basename(path))
        stem, ext = os.path.splitext(target)
        n = 1
        while os.path.exists(target):
            target = f"{stem} ({n}){ext}"
            n += 1
        shutil.move(path, target)
        return target
    except OSError:
        return None


class WatchService:
    """Esegue i monitoraggi attivi: scansione con un timer Qt, elaborazione in un thread.

    I monitoraggi valgono per la sessione: non vengono salvati e si fermano alla chiusura."""

    INTERVAL_MS = 2000

    def __init__(self):
        self.watchers: dict[str, FolderWatcher] = {}
        self._busy = threading.Lock()
        self.timer = None

    def configs(self) -> list[WatchConfig]:
        return [w.cfg for w in self.watchers.values()]

    def add(self, cfg: WatchConfig) -> list[str]:
        errors = validate_watch(cfg)
        if errors:
            return errors
        self.watchers[cfg.id] = FolderWatcher(cfg)
        self.ensure_timer()
        return []

    def remove(self, watch_id: str) -> None:
        self.watchers.pop(watch_id, None)
        self.ensure_timer()

    def set_enabled(self, watch_id: str, enabled: bool) -> None:
        w = self.watchers.get(watch_id)
        if w is not None:
            w.cfg.enabled = enabled
            self.ensure_timer()

    def ensure_timer(self) -> None:
        """Avvia la scansione periodica solo se c'è almeno un monitoraggio attivo."""
        active = any(w.cfg.enabled for w in self.watchers.values())
        if active and self.timer is None:
            from PySide6.QtCore import QTimer
            self.timer = QTimer()
            self.timer.setInterval(self.INTERVAL_MS)
            self.timer.timeout.connect(self.tick)
            self.timer.start()
            self.tick()   # scansione iniziale: file già presenti nella cartella
        elif not active and self.timer is not None:
            self.timer.stop()
            self.timer = None

    def tick(self, wait: bool = False) -> None:
        if not self._busy.acquire(blocking=False):
            return
        jobs = [(w, w.scan()) for w in self.watchers.values() if w.cfg.enabled]
        jobs = [(w, ready) for w, ready in jobs if ready]
        if not jobs:
            self._busy.release()
            return

        def work() -> None:
            try:
                for w, ready in jobs:
                    for path in ready:
                        w.process(path)
            finally:
                self._busy.release()

        t = threading.Thread(target=work, name="foliant-watch", daemon=True)
        t.start()
        if wait:
            t.join()

    def stop(self) -> None:
        if self.timer is not None:
            self.timer.stop()
            self.timer = None
