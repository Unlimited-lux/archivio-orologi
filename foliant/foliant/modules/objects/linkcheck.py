"""Verifica dei collegamenti (OBJ-42): controllo locale e verifica online
facoltativa degli URL, rapporto CSV (logica pura, senza interfaccia).

La verifica locale non accede mai alla rete. La verifica online
(`check_urls_online`) va chiamata solo dopo un consenso esplicito.
"""

from __future__ import annotations

import csv
import errno
import io
import os
import re
import socket
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

import pymupdf

from . import links as L

# tipi
WEB, INTERNAL, FILE, OTHER = "web", "interno", "file", "altro"

# stati della verifica locale
OK = "OK"
BROKEN = "rotto"
SUSPECT = "sospetto"
UNCHECKED = "non verificato"
UNCHECKABLE = "non verificabile"
# stati della verifica online
REDIRECTED = "reindirizzato"
NOT_FOUND = "non trovato"
ERROR = "errore"
TIMEOUT = "tempo scaduto"

STATUSES = (OK, BROKEN, SUSPECT, UNCHECKED, UNCHECKABLE, REDIRECTED, NOT_FOUND, ERROR, TIMEOUT)
PROBLEMS = (BROKEN, SUSPECT, NOT_FOUND, ERROR, TIMEOUT)

ONLINE_TIMEOUT = 10
USER_AGENT = "Foliant-LinkCheck/1.0"


@dataclass
class LinkRow:
    page: int            # indice 0-based della pagina che contiene il collegamento
    xref: int
    kind: str            # web / interno / file / altro
    destination: str
    description: str
    status: str
    detail: str = ""


def _kind(t: L.LinkTarget) -> str:
    if t.kind == L.URI:
        return WEB
    if t.kind in (L.PAGE, L.NAMED):
        return INTERNAL
    if t.kind in (L.FILE, L.LAUNCH):
        return FILE
    return OTHER


def _file_path(spec: str) -> str:
    """Percorso locale da una specifica di file PDF (anche in forma /C/dir/f.pdf o file://)."""
    p = spec.strip()
    if p.lower().startswith("file:"):
        p = unquote(urlsplit(p).path)
    if os.name == "nt" and re.match(r"^/[A-Za-z]/", p):
        p = p[1] + ":" + p[2:]
    return p


def _check_file(t: L.LinkTarget, doc_path) -> tuple[str, str]:
    spec = t.file
    if not spec:
        return BROKEN, "Nessun file indicato."
    p = _file_path(spec)
    if not os.path.isabs(p):
        if not doc_path:
            return UNCHECKED, "Percorso relativo: il documento non è ancora salvato."
        p = os.path.join(os.path.dirname(os.path.abspath(str(doc_path))), p)
    if not os.path.exists(p):
        return BROKEN, "Il file collegato non esiste."
    if os.path.isdir(p):
        return BROKEN, "Il percorso indica una cartella, non un file."
    if t.kind == L.FILE and t.page > 0 and p.lower().endswith(".pdf"):
        try:
            other = pymupdf.open(p)
            try:
                if not other.needs_pass and t.page >= other.page_count:
                    return BROKEN, f"Il file collegato ha solo {other.page_count} pagine."
            finally:
                other.close()
        except Exception:
            pass
    return OK, ""


def _check_target(pdf: pymupdf.Document, t: L.LinkTarget, names, doc_path) -> tuple[str, str]:
    n = pdf.page_count
    if t.kind == L.PAGE:
        if t.page < 0:
            return BROKEN, "La pagina di destinazione non esiste più."
        if t.page >= n:
            return BROKEN, f"La pagina {t.page + 1} non esiste (il documento ha {n} pagine)."
        return OK, ""
    if t.kind == L.NAMED:
        d = names.get(t.name)
        if d is None:
            return BROKEN, f"La destinazione «{t.name}» non esiste."
        pg = d.get("page", -1) if isinstance(d, dict) else -1
        if pg is None or pg < 0 or pg >= n:
            return BROKEN, f"La destinazione «{t.name}» punta a una pagina inesistente."
        return OK, ""
    if t.kind in (L.FILE, L.LAUNCH):
        return _check_file(t, doc_path)
    if t.kind == L.URI:
        if not t.uri.strip():
            return BROKEN, "Indirizzo vuoto."
        chk = L.normalize_uri(t.uri)
        if chk.error:
            return SUSPECT, chk.error
        if chk.warnings:
            return SUSPECT, " ".join(chk.warnings)
        return UNCHECKED, ""
    # destinazione con numero di pagina diretto (non standard ma diffusa): [N /Fit]
    m = re.match(r"^\s*\[\s*(\d+)\s*/\w+", t.raw or "")
    if m:
        p = int(m.group(1))
        if p >= n:
            return BROKEN, f"La pagina {p + 1} non esiste (il documento ha {n} pagine)."
        return UNCHECKABLE, "Destinazione in forma non standard."
    return UNCHECKABLE, "Azione non standard."


def _named_from_dest(pdf: pymupdf.Document, xref: int, t: L.LinkTarget) -> None:
    """/Dest come stringa semplice (il motore la restituisce senza parentesi)."""
    try:
        typ, val = pdf.xref_get_key(xref, "Dest")
    except Exception:
        return
    if typ in ("string", "name"):
        t.kind, t.name = L.NAMED, val.lstrip("/") if typ == "name" else val


def _destination(t: L.LinkTarget) -> str:
    if t.kind == L.OTHER:
        raw = re.sub(r"\s+", " ", t.raw or "").strip()
        m = re.search(r"/S\s*/(\w+)", raw)
        if m:
            return f"Azione /{m.group(1)}"
        return raw[:80] or "Azione sconosciuta"
    if t.kind == L.PAGE and t.page < 0:
        return "Pagina eliminata"
    return t.describe()


def check_document(pdf: pymupdf.Document, doc_path=None) -> list[LinkRow]:
    """Elenca e verifica localmente tutti i collegamenti (nessun accesso alla rete).

    Va chiamata tenendo `ENGINE_LOCK` se il documento è condiviso."""
    try:
        # PyMuPDF memorizza il risultato: dopo una modifica sarebbe superato
        if "_resolved_names" in vars(pdf):
            del pdf._resolved_names
    except Exception:
        pass
    try:
        names = pdf.resolve_names() or {}
    except Exception:
        names = {}
    rows: list[LinkRow] = []
    for i in range(pdf.page_count):
        for lk in L.page_links(pdf, i):
            t = lk.target
            if t.kind == L.OTHER:
                _named_from_dest(pdf, lk.xref, t)
            status, detail = _check_target(pdf, t, names, doc_path)
            rows.append(LinkRow(i, lk.xref, _kind(t), _destination(t), lk.description or "", status, detail))
    return rows


# ================================================================ online
def _is_cancelled(cancel) -> bool:
    if cancel is None:
        return False
    if hasattr(cancel, "is_set"):
        return cancel.is_set()
    return bool(cancel())


def _network_down(reason) -> bool:
    if isinstance(reason, socket.gaierror):
        return reason.errno in (getattr(socket, "EAI_AGAIN", -3),)
    if isinstance(reason, OSError):
        return reason.errno in (errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH)
    return False


def _is_timeout(e) -> bool:
    return isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower()


def check_url(url: str, timeout: float = ONLINE_TIMEOUT, opener=None) -> tuple[str, str]:
    """Verifica un URL http(s): HEAD, poi GET se il server non accetta HEAD."""
    open_ = opener or urllib.request.urlopen   # risolto qui: sostituibile nei test
    last = None
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            resp = open_(req, timeout=timeout)
            try:
                code = getattr(resp, "status", None) or resp.getcode()
                final = resp.geturl() if hasattr(resp, "geturl") else url
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            if final and final.rstrip("/") != url.rstrip("/"):
                return REDIRECTED, final
            if code and 300 <= code < 400:
                return REDIRECTED, f"HTTP {code}"
            return OK, f"HTTP {code}" if code else ""
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                return NOT_FOUND, f"HTTP {e.code}"
            if method == "HEAD" and e.code in (400, 403, 405, 501):
                last = (ERROR, f"HTTP {e.code}")
                continue
            return ERROR, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            if _is_timeout(e.reason):
                return TIMEOUT, ""
            if _network_down(e.reason):
                return UNCHECKED, "Rete non disponibile."
            if isinstance(e.reason, socket.gaierror):
                return NOT_FOUND, "Server inesistente."
            return ERROR, str(e.reason)
        except Exception as e:  # noqa: BLE001 - qualunque errore diventa uno stato
            if _is_timeout(e):
                return TIMEOUT, ""
            return ERROR, str(e)
    return last or (ERROR, "")


def check_urls_online(rows: list[LinkRow], timeout: float = ONLINE_TIMEOUT, max_workers: int = 4,
                      opener=None, cancel=None, on_progress=None) -> list[LinkRow]:
    """Verifica online gli URL http(s) delle righe (solo dopo consenso esplicito).

    Aggiorna le righe sul posto e le restituisce. Ogni URL è richiesto una volta
    sola; le richieste sono in parallelo (al più `max_workers`) e interrompibili
    con `cancel` (callable o threading.Event). `on_progress(fatti, totale)`."""
    targets: dict[str, list[LinkRow]] = {}
    for r in rows:
        if r.kind != WEB or r.status in (BROKEN, SUSPECT):
            continue
        uri = L.normalize_uri(r.destination).uri or r.destination
        if urlsplit(uri).scheme.lower() not in ("http", "https"):
            continue
        targets.setdefault(uri, []).append(r)
    total = len(targets)
    if not total:
        return rows

    def job(u):
        if _is_cancelled(cancel):
            return None
        return check_url(u, timeout, opener)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as ex:
        futs = {ex.submit(job, u): u for u in targets}
        for f in as_completed(futs):
            done += 1
            res = None if f.cancelled() else f.result()
            if res is not None:
                status, detail = res
                for r in targets[futs[f]]:
                    r.status, r.detail = status, detail
            if on_progress:
                on_progress(done, total)
            if _is_cancelled(cancel):
                for g in futs:
                    g.cancel()
    return rows


# ================================================================ CSV
def to_csv(rows: list[LinkRow]) -> str:
    """Rapporto CSV (UTF-8): pagina (da 1), tipo, destinazione, stato."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["pagina", "tipo", "destinazione", "stato"])
    for r in rows:
        w.writerow([r.page + 1, r.kind, r.destination, r.status])
    return buf.getvalue()


def write_csv(rows: list[LinkRow], path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(to_csv(rows))
