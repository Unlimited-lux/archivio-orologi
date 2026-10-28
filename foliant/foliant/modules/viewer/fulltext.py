"""Indici full-text (VIS-25): ricerche immediate su grandi archivi di PDF.

Un indice è un database SQLite (FTS5, accenti ignorati) nel profilo utente, con il testo di ogni
pagina dei PDF di una cartella. «Aggiorna» rilegge solo i file cambiati (data e dimensione) e
toglie quelli eliminati. La ricerca usa FTS5 per trovare in fretta le pagine candidate e poi la
stessa verifica esatta della ricerca avanzata (modalità e opzioni) sul testo salvato. I PDF
cifrati non si indicizzano: compaiono tra i file saltati. Logica pura, senza widget: le
funzioni lunghe ricevono `progress` (come i lavori di `run_task`)."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from . import advsearch_engine as eng
from .engine import extract_page_text

SCHEMA_VERSION = 1


def index_dir(env=None) -> Path:
    """Cartella degli indici: $XDG_DATA_HOME/foliant/indici (o ~/.local/share/...)."""
    env = os.environ if env is None else env
    base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "foliant" / "indici"


def safe_name(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name.strip())[:80] or "indice"


def list_indexes() -> list[str]:
    d = index_dir()
    return sorted(p.stem for p in d.glob("*.sqlite")) if d.is_dir() else []


@dataclass
class IndexStats:
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    skipped: list = field(default_factory=list)       # [(percorso, motivo)]


class FullTextIndex:
    def __init__(self, name: str):
        self.name = safe_name(name)
        self.path = index_dir() / f"{self.name}.sqlite"

    # ------------------------------------------------------------ database
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path))
        db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime REAL, "
                   "size INTEGER, pages INTEGER)")
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(file_id UNINDEXED, page UNINDEXED, text, "
                   "tokenize='unicode61 remove_diacritics 2')")
        return db

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def settings(self) -> dict:
        if not self.exists:
            return {}
        with self._connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
        return json.loads(row[0]) if row else {}

    def files(self) -> list[str]:
        if not self.exists:
            return []
        with self._connect() as db:
            return [r[0] for r in db.execute("SELECT path FROM files ORDER BY path")]

    def delete(self) -> None:
        if self.path.exists():
            self.path.unlink()

    # ------------------------------------------------------------ costruzione
    def build(self, progress, folder: str, recursive: bool = True) -> IndexStats:
        """Crea (o ricrea) l'indice della cartella."""
        self.delete()
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('settings', ?)",
                       (json.dumps({"folder": os.path.abspath(folder), "recursive": recursive,
                                    "version": SCHEMA_VERSION, "created": time.time()}),))
        return self.update(progress)

    def update(self, progress) -> IndexStats:
        """Rilegge i file nuovi o cambiati e toglie quelli eliminati."""
        s = self.settings()
        stats = IndexStats()
        folder = s.get("folder")
        if not folder:
            return stats
        paths = eng.list_pdfs(folder, bool(s.get("recursive", True)))
        with self._connect() as db:
            known = {p: (fid, mtime, size) for fid, p, mtime, size in
                     db.execute("SELECT id, path, mtime, size FROM files")}
            for gone in set(known) - set(paths):
                fid = known[gone][0]
                db.execute("DELETE FROM pages WHERE file_id = ?", (fid,))
                db.execute("DELETE FROM files WHERE id = ?", (fid,))
                stats.removed += 1
            n = len(paths)
            for k, path in enumerate(paths):
                if progress is not None:
                    progress.update(k / max(1, n), f"{k + 1}/{n} · {os.path.basename(path)}")
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                old = known.get(path)
                if old is not None and abs(old[1] - st.st_mtime) < 1e-6 and old[2] == st.st_size:
                    stats.unchanged += 1
                    continue
                texts, reason = _page_texts(path)
                if old is not None:
                    db.execute("DELETE FROM pages WHERE file_id = ?", (old[0],))
                    db.execute("DELETE FROM files WHERE id = ?", (old[0],))
                if texts is None:
                    stats.skipped.append((path, reason))
                    continue
                cur = db.execute("INSERT INTO files (path, mtime, size, pages) VALUES (?, ?, ?, ?)",
                                 (path, st.st_mtime, st.st_size, len(texts)))
                fid = cur.lastrowid
                db.executemany("INSERT INTO pages (file_id, page, text) VALUES (?, ?, ?)",
                               [(fid, i, t) for i, t in enumerate(texts) if t.strip()])
                if old is None:
                    stats.added += 1
                else:
                    stats.updated += 1
        if progress is not None:
            progress.update(1.0)
        return stats

    # ------------------------------------------------------------ ricerca
    def search(self, progress, opts: eng.AdvOptions, page_timeout: float | None = eng.DEFAULT_PAGE_TIMEOUT
               ) -> tuple[list[eng.AdvHit], eng.AdvSummary]:
        """Risultati come la ricerca avanzata (testo delle pagine; posizioni calcolate al clic).
        I file cambiati o eliminati dopo l'indicizzazione sono segnalati nel riepilogo."""
        summary = eng.AdvSummary()
        hits: list[eng.AdvHit] = []
        if not self.exists:
            return hits, summary
        guard = eng.make_guard(opts, page_timeout)
        try:
            with self._connect() as db:
                files = {fid: (p, mtime, size) for fid, p, mtime, size in
                         db.execute("SELECT id, path, mtime, size FROM files")}
                summary.files = len(files)
                match = _fts_query(opts)
                rows = (db.execute("SELECT file_id, page, text FROM pages WHERE pages MATCH ? "
                                   "ORDER BY file_id, page", (match,)) if match else
                        db.execute("SELECT file_id, page, text FROM pages ORDER BY file_id, page"))
                stale: set[str] = set()
                for path, mtime, size in files.values():     # cambiati o eliminati dopo l'indicizzazione
                    try:
                        st = os.stat(path)
                        if abs(st.st_mtime - mtime) > 1e-6 or st.st_size != size:
                            stale.add(path)
                    except OSError:
                        stale.add(path)
                with_hits: set[int] = set()
                for k, (fid, page, text) in enumerate(rows):
                    if progress is not None and k % 200 == 0:
                        progress.update(0.5, "")
                    path, mtime, size = files.get(int(fid), ("", 0, 0))
                    if guard is not None and not guard.check([text]):
                        summary.timed_out.append((path, int(page)))
                        continue
                    units = eng.find_in_string(text, opts)
                    if not units:
                        continue
                    changed = path in stale
                    for b, f, a in units:
                        if len(hits) >= eng.MAX_RESULTS:
                            summary.truncated = True
                            break
                        hits.append(eng.AdvHit(path, int(page), eng.KIND_TEXT, b, f, a, [], None, stale=changed))
                        with_hits.add(int(fid))
                    if summary.truncated:
                        break
                summary.files_with_hits = len(with_hits)
                summary.stale.extend(sorted(stale))
        finally:
            if guard is not None:
                guard.close()
        return hits, summary


def _page_texts(path: str) -> tuple[list[str] | None, str]:
    """Testo di ogni pagina (stesso testo della ricerca, con le parole sillabate ricomposte)."""
    try:
        pdf = pymupdf.open(path)
    except Exception:
        return None, "damaged"
    try:
        if pdf.needs_pass:
            return None, "encrypted"
        if not pdf.is_pdf:
            return None, "damaged"
        out = []
        for i in range(pdf.page_count):
            try:
                out.append(extract_page_text(pdf[i]).text)
            except Exception:
                out.append("")
        return out, ""
    finally:
        pdf.close()


def _fts_query(opts: eng.AdvOptions) -> str | None:
    """Espressione FTS5 per le pagine candidate (None = tutte, per le espressioni regolari)."""
    if opts.mode == eng.REGEX:
        return None
    words = [w for w in re.findall(r"\w+", opts.query, re.UNICODE) if w]
    if not words:
        return None

    def quoted(w: str) -> str:
        return '"' + w.replace('"', '""') + '"'
    if opts.mode == eng.PHRASE:
        return quoted(" ".join(words))
    joiner = " AND " if opts.mode == eng.ALL_WORDS else " OR "
    return joiner.join(quoted(w) for w in words)
