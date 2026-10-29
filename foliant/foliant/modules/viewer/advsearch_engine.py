"""Logica pura della ricerca avanzata (VIS-25), senza interfaccia.

Riusa il motore di ricerca di `engine.py` (testo carattere per carattere,
normalizzazione, opzioni) ed estende la ricerca a commenti, segnalibri, campi
modulo e metadati, a più documenti e a una cartella con le sottocartelle.

Ogni accesso a pymupdf avviene tenendo il lock passato (ENGINE_LOCK).
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import queue
import subprocess
import threading
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable

import pymupdf

from foliant.core import platform
from foliant.core.errors import OperationCancelled

from .engine import (
    PageText, SearchOptions, compile_query, context_snippet, extract_page_text, find_spans, rects_of, span_snippet,
)

# modalità
PHRASE = "phrase"
ALL_WORDS = "all"
ANY_WORD = "any"
REGEX = "regex"
MODES = (PHRASE, ALL_WORDS, ANY_WORD, REGEX)

# tipi di risultato
KIND_TEXT = "text"
KIND_COMMENT = "comment"
KIND_BOOKMARK = "bookmark"
KIND_FIELD = "field"
KIND_METADATA = "metadata"

MAX_RESULTS = 20000
DEFAULT_PAGE_TIMEOUT = 2.0   # secondi per pagina per le espressioni regolari


@dataclass
class AdvOptions:
    query: str = ""
    mode: str = PHRASE
    case_sensitive: bool = False
    whole_word: bool = False
    ignore_accents: bool = False
    comments: bool = False
    bookmarks: bool = False
    fields: bool = False
    metadata: bool = False
    attachments: bool = False        # PDF incorporati (un livello)

    def search_options(self) -> SearchOptions:
        return SearchOptions(case_sensitive=self.case_sensitive, whole_word=self.whole_word,
                             ignore_accents=self.ignore_accents, regex=self.mode == REGEX)

    def terms(self) -> list[str]:
        if self.mode in (ALL_WORDS, ANY_WORD):
            return [w for w in self.query.split() if w]
        return [self.query] if self.query.strip() else []


@dataclass
class AdvHit:
    file: str            # percorso o nome visualizzato del documento
    page: int            # indice di pagina (-1 = intero documento, es. metadati)
    kind: str
    before: str
    found: str
    after: str
    rects: list = field(default_factory=list)   # rettangoli (tuple x0,y0,x1,y1) sulla pagina
    doc_key: object = None   # chiave del documento aperto (None = file su disco)
    label: str = ""          # nome del campo, chiave dei metadati, ecc.
    attachment: str = ""     # nome dell'allegato PDF in cui si trova l'occorrenza ("" = il documento)
    stale: bool = False      # indice: file cambiato o eliminato dopo l'indicizzazione

    @property
    def excerpt(self) -> str:
        return f"{self.before}{self.found}{self.after}"


@dataclass
class AdvSummary:
    files: int = 0
    files_with_hits: int = 0
    skipped: list = field(default_factory=list)    # [(percorso, motivo)]
    timed_out: list = field(default_factory=list)  # [(file, pagina)]
    stale: list = field(default_factory=list)      # indice: file cambiati dopo l'indicizzazione
    truncated: bool = False


def validate(opts: AdvOptions) -> str | None:
    """Messaggio d'errore per la ricerca (None se valida)."""
    if not opts.query.strip():
        return None
    if opts.mode == REGEX:
        try:
            compile_query(opts.query, opts.search_options())
        except ValueError as e:
            return str(e)
    return None


# ================================================================= testo semplice

def string_page_text(s: str) -> PageText:
    """PageText "finto" per una stringa: il carattere i ha il riquadro (i, 0, i+1, 1),
    così il motore di ricerca della pagina vale anche per commenti, campi, titoli."""
    pt = PageText()
    pt.text = s
    pt.boxes = [pymupdf.Rect(i, 0, i + 1, 1) for i in range(len(s))]
    pt.lines = [0] * len(s)
    return pt


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def find_in_string(s: str, opts: AdvOptions, width: int = 40) -> list[tuple[str, str, str]]:
    """Occorrenze nella stringa come (prima, trovato, dopo), secondo la modalità."""
    if not s or not s.strip():
        return []
    pt = string_page_text(s)
    spans = _match_units(pt, opts)
    if spans is None:
        return []
    out = []
    for rects, _src in spans:
        a = int(round(rects[0].x0))
        b = int(round(rects[-1].x1))
        out.append((_clean(s[max(0, a - width):a]).lstrip(), _clean(s[a:b]), _clean(s[b:b + width]).rstrip()))
    return out


def _match_units(pt: PageText, opts: AdvOptions) -> list | None:
    """Occorrenze in un testo secondo la modalità, come (rettangoli, indici dei caratteri);
    None se la modalità "tutte le parole" non è soddisfatta."""
    so = opts.search_options()
    terms = opts.terms()
    if not terms:
        return []

    def hits(term: str) -> list:
        return [(rects_of(pt, src), src) for src in find_spans(pt, term, so)]
    if opts.mode in (PHRASE, REGEX):
        return hits(terms[0])
    found: list = []
    for t in terms:
        found_t = hits(t)
        if not found_t and opts.mode == ALL_WORDS:
            return None
        found.extend(found_t)
    found.sort(key=lambda h: (h[0][0].y0, h[0][0].x0))
    return found


def page_snippet(pt: PageText, rects: list, width: int = 40) -> tuple[str, str, str]:
    """(prima, trovato, dopo) per un'occorrenza sulla pagina: i caratteri il cui
    centro cade in uno dei rettangoli (solo quelli della riga dell'occorrenza)."""
    idx = []
    for i, box in enumerate(pt.boxes):
        if box is None or pt.lines[i] == -2:
            continue
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        if any(r.x0 - 0.5 <= cx <= r.x1 + 0.5 and r.y0 - 0.5 <= cy <= r.y1 + 0.5 for r in rects):
            idx.append(i)
    if not idx:
        return context_snippet(pt, rects, width)
    a, b = idx[0], idx[-1] + 1
    found = "".join(c for i, c in enumerate(pt.text[a:b], start=a) if pt.lines[i] != -2)
    return (_clean(pt.text[max(0, a - width):a]).lstrip(), _clean(found),
            _clean(pt.text[b:b + width]).rstrip())


# ======================================================= limite di tempo (regex)

_GUARD_SCRIPT = r"""
import sys, re, json
for line in sys.stdin:
    req = json.loads(line)
    try:
        p = re.compile(req["p"], req["f"])
        for t in req["t"]:
            for _m in p.finditer(t):
                pass
    except Exception:
        pass
    sys.stdout.write("ok\n")
    sys.stdout.flush()
"""


class RegexGuard:
    """Prova l'espressione regolare su ogni pagina in un processo separato con un
    limite di tempo: le espressioni con tempi patologici non bloccano la ricerca
    (il processo viene terminato e la pagina segnalata). Se il processo non può
    partire, la ricerca prosegue senza limite."""

    def __init__(self, pattern: str, flags: int, timeout: float = DEFAULT_PAGE_TIMEOUT):
        self.pattern = pattern
        self.flags = flags
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None

    def _start(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        try:
            self.proc = subprocess.Popen(platform.python_command("-I", "-c", _GUARD_SCRIPT), stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                         **platform.subprocess_kwargs())
        except Exception:
            self.proc = None
            return False
        # le risposte arrivano da un thread lettore: select() sulle pipe non esiste su Windows
        self._replies = queue.Queue()
        threading.Thread(target=self._read_replies, args=(self.proc, self._replies), daemon=True,
                         name="foliant-regex-guard").start()
        return True

    @staticmethod
    def _read_replies(proc: subprocess.Popen, replies: "queue.Queue") -> None:
        try:
            for line in proc.stdout:
                replies.put(line)
        except (OSError, ValueError):
            pass
        replies.put(None)

    def check(self, texts: list[str]) -> bool:
        """True se l'espressione termina entro il limite su tutti i testi."""
        texts = [t for t in texts if t]
        if not texts or not self._start():
            return True
        proc = self.proc
        try:
            proc.stdin.write(json.dumps({"p": self.pattern, "f": self.flags, "t": texts}) + "\n")
            proc.stdin.flush()
            try:
                line = self._replies.get(timeout=self.timeout)
            except queue.Empty:
                line = None
            if line is not None and line.strip() == "ok":
                return True
        except Exception:
            self.close()
            return True   # nessuna protezione disponibile: si procede
        self.close()       # tempo scaduto: si termina il processo bloccato
        return False

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass
        for s in (proc.stdin, proc.stdout):
            try:
                s.close()
            except Exception:
                pass


def make_guard(opts: AdvOptions, timeout: float | None) -> RegexGuard | None:
    if opts.mode != REGEX or not timeout:
        return None
    return RegexGuard(opts.query, 0 if opts.case_sensitive else re.IGNORECASE, timeout)


# ============================================================ ricerca nel documento

def _rect_tuple(r) -> tuple:
    r = pymupdf.Rect(r)
    return (r.x0, r.y0, r.x1, r.y1)


def search_document(pdf: pymupdf.Document, opts: AdvOptions, file: str, *, doc_key=None,
                    lock=None, check: Callable[[int, int], None] | None = None,
                    guard: RegexGuard | None = None, summary: AdvSummary | None = None,
                    limit: int = MAX_RESULTS) -> list[AdvHit]:
    """Cerca in un documento aperto del motore. `check(pagina, totale)` è chiamata
    a ogni pagina (può sollevare OperationCancelled)."""
    lock = lock or threading.RLock()
    hits: list[AdvHit] = []

    def add(hit: AdvHit) -> bool:
        if len(hits) >= limit:
            if summary is not None:
                summary.truncated = True
            return False
        hits.append(hit)
        return True

    with lock:
        count = pdf.page_count
    # segnalibri e metadati: una volta per documento
    if opts.bookmarks:
        with lock:
            try:
                toc = pdf.get_toc(simple=True)
            except Exception:
                toc = []
        texts = [str(t[1]) for t in toc]
        if guard is None or guard.check(texts):
            for _lvl, title, page in toc:
                for b, f, a in find_in_string(str(title), opts):
                    add(AdvHit(file, max(-1, min(int(page) - 1, count - 1)), KIND_BOOKMARK, b, f, a, [], doc_key))
    if opts.metadata:
        with lock:
            try:
                meta = dict(pdf.metadata or {})
            except Exception:
                meta = {}
            try:
                xmp = pdf.get_xml_metadata() or ""
            except Exception:
                xmp = ""
        items = [(k, str(v)) for k, v in meta.items() if v and k not in ("format", "encryption")]
        if xmp:
            items.append(("XMP", re.sub(r"<[^>]+>", " ", xmp)))
        if guard is None or guard.check([v for _k, v in items]):
            for key, value in items:
                for b, f, a in find_in_string(value, opts):
                    add(AdvHit(file, -1, KIND_METADATA, b, f, a, [], doc_key, label=key))

    for i in range(count):
        if check is not None:
            check(i, count)
        if len(hits) >= limit:
            break
        comments: list[tuple[str, tuple]] = []
        widgets: list[tuple[str, str, tuple]] = []
        with lock:
            page = pdf[i]
            pt = extract_page_text(page)
            if opts.comments:
                for annot in page.annots() or []:
                    try:
                        content = (annot.info or {}).get("content", "") or ""
                    except Exception:
                        content = ""
                    if content.strip():
                        comments.append((content, _rect_tuple(annot.rect)))
            if opts.fields:
                for w in page.widgets() or []:
                    value = w.field_value
                    if value in (None, "", False):
                        continue
                    if isinstance(value, (list, tuple)):
                        value = ", ".join(str(v) for v in value)
                    widgets.append((str(value), w.field_name or "", _rect_tuple(w.rect)))
            del page
        if guard is not None and not guard.check([pt.text] + [c for c, _r in comments] + [v for v, _n, _r in widgets]):
            if summary is not None:
                summary.timed_out.append((file, i))
            continue
        units = _match_units(pt, opts) if pt.text else []
        for rects, src in units or []:
            b, f, a = span_snippet(pt, src, width=40)
            if not add(AdvHit(file, i, KIND_TEXT, b, f, a, [_rect_tuple(r) for r in rects], doc_key)):
                break
        for content, rect in comments:
            for b, f, a in find_in_string(content, opts):
                add(AdvHit(file, i, KIND_COMMENT, b, f, a, [rect], doc_key))
        for value, name, rect in widgets:
            for b, f, a in find_in_string(value, opts):
                add(AdvHit(file, i, KIND_FIELD, b, f, a, [rect], doc_key, label=name))
    if opts.attachments and len(hits) < limit:
        for name, data in _pdf_attachments(pdf, lock):
            try:
                sub = pymupdf.open("pdf", data)
            except Exception:
                if summary is not None:
                    summary.skipped.append((f"{file} › {name}", "damaged"))
                continue
            if sub.needs_pass:
                if summary is not None:
                    summary.skipped.append((f"{file} › {name}", "encrypted"))
                continue
            inner = replace(opts, attachments=False)        # un solo livello di allegati
            for h in search_document(sub, inner, file, doc_key=doc_key, guard=guard, summary=summary,
                                     limit=limit - len(hits)):
                h.attachment = name
                if not add(h):
                    break
            sub.close()
    return hits


def _pdf_attachments(pdf: pymupdf.Document, lock) -> list[tuple[str, bytes]]:
    """Allegati di documento che sono PDF: (nome nel name tree, dati)."""
    out = []
    with lock:
        try:
            names = pdf.embfile_names()
        except Exception:
            names = []
        for name in names:
            try:
                data = pdf.embfile_get(name)
            except Exception:
                continue
            if data[:1024].lstrip().startswith(b"%PDF") or b"%PDF-" in data[:1024]:
                out.append((name, data))
    return out


# ============================================================ ricerca in cartella

def list_pdfs(folder: str, recursive: bool = True) -> list[str]:
    out: list[str] = []
    if recursive:
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            for name in sorted(files):
                if name.lower().endswith(".pdf"):
                    out.append(os.path.join(root, name))
    else:
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            names = []
        out = [os.path.join(folder, n) for n in names
               if n.lower().endswith(".pdf") and os.path.isfile(os.path.join(folder, n))]
    return out


def search_files(progress, paths: Iterable[str], opts: AdvOptions, lock, *, page_timeout: float | None =
                 DEFAULT_PAGE_TIMEOUT, encrypted_reason: str = "cifrato",
                 damaged_reason: str = "danneggiato") -> tuple[list[AdvHit], AdvSummary]:
    """Lavoro per `run_task`: apre ogni file in proprio (mai widget)."""
    paths = list(paths)
    summary = AdvSummary()
    hits: list[AdvHit] = []
    guard = make_guard(opts, page_timeout)
    n = len(paths)
    try:
        for k, path in enumerate(paths):
            name = os.path.basename(path)
            progress.update(k / max(1, n), f"{k + 1}/{n} · {name}")
            reason = None
            with lock:
                try:
                    pdf = pymupdf.open(path)
                except Exception:
                    pdf = None
                    reason = damaged_reason
                if pdf is not None and (pdf.needs_pass or not pdf.is_pdf):
                    reason = encrypted_reason if pdf.needs_pass else damaged_reason
                    pdf.close()
                    pdf = None
            if pdf is None:
                summary.skipped.append((path, reason or damaged_reason))
                continue
            summary.files += 1

            def check(i, count, k=k, name=name):
                progress.update((k + (i / max(1, count))) / max(1, n), f"{k + 1}/{n} · {name}")

            try:
                found = search_document(pdf, opts, path, lock=lock, check=check, guard=guard, summary=summary,
                                        limit=MAX_RESULTS - len(hits))
            except OperationCancelled:
                raise
            except Exception:
                summary.skipped.append((path, damaged_reason))
                summary.files -= 1
                found = []
            finally:
                with lock:
                    pdf.close()
            if found:
                summary.files_with_hits += 1
                hits.extend(found)
            if len(hits) >= MAX_RESULTS:
                summary.truncated = True
                break
        progress.update(1.0)
    finally:
        if guard is not None:
            guard.close()
    return hits, summary


def search_documents(progress, docs: list[tuple[object, str, object]], opts: AdvOptions, lock, *,
                     page_timeout: float | None = DEFAULT_PAGE_TIMEOUT) -> tuple[list[AdvHit], AdvSummary]:
    """Lavoro per `run_task` sui documenti aperti: `docs` = [(chiave, nome, documento Foliant)].
    Il documento interno (`document.pdf`) si legge tenendo il lock."""
    summary = AdvSummary()
    hits: list[AdvHit] = []
    guard = make_guard(opts, page_timeout)
    n = len(docs)
    try:
        for k, (key, name, document) in enumerate(docs):
            progress.update(k / max(1, n), f"{k + 1}/{n} · {os.path.basename(name)}")
            with lock:
                if getattr(document, "closed", False):
                    continue
                pdf = document.pdf
            summary.files += 1

            def check(i, count, k=k, name=name):
                progress.update((k + (i / max(1, count))) / max(1, n), f"{k + 1}/{n} · {os.path.basename(name)}")

            found = search_document(pdf, opts, name, doc_key=key, lock=lock, check=check, guard=guard,
                                    summary=summary, limit=MAX_RESULTS - len(hits))
            if found:
                summary.files_with_hits += 1
                hits.extend(found)
            if len(hits) >= MAX_RESULTS:
                summary.truncated = True
                break
        progress.update(1.0)
    finally:
        if guard is not None:
            guard.close()
    return hits, summary


# ================================================================= raggruppamento

def group_hits(hits: list[AdvHit]) -> list[tuple[str, list[tuple[int, list[int]]]]]:
    """[(file, [(pagina, [indici dei risultati])])] nell'ordine di arrivo dei file e per pagina."""
    files: dict[str, dict[int, list[int]]] = {}
    for idx, h in enumerate(hits):
        files.setdefault(h.file, {}).setdefault(h.page, []).append(idx)
    return [(f, sorted(pages.items())) for f, pages in files.items()]


def export_csv(hits: list[AdvHit], kind_names: dict[str, str], header: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for h in hits:
        writer.writerow([h.file, h.page + 1 if h.page >= 0 else "", kind_names.get(h.kind, h.kind),
                         _clean(h.excerpt).strip()])
    return buf.getvalue().encode("utf-8-sig")
