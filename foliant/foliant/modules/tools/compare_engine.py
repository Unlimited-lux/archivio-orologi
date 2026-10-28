"""Confronto di due PDF (A = riferimento, B = versione da verificare).

Logica pura, senza interfaccia: estrazione del testo nell'ordine di lettura,
differenze di parole (aggiunte, eliminazioni, modifiche), differenze di
formattazione, abbinamento delle pagine, differenze visive sui pixel e
corrispondenza delle posizioni per lo scorrimento sincronizzato.

Convenzioni: *aggiunta* = presente solo in B, *eliminazione* = solo in A,
*modifica* = tratto di A sostituito da un tratto diverso in B.
Coordinate dei riquadri: punti PDF della pagina non ruotata (come il motore).
"""

from __future__ import annotations

import bisect
import difflib
import hashlib
import re
import string
import unicodedata
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pymupdf

from foliant.api import ENGINE_LOCK, DocumentError, OperationCancelled, PasswordRequired, WrongPassword, _

ADDED = "added"
DELETED = "deleted"
MODIFIED = "modified"
FORMAT = "format"
VISUAL = "visual"
PAGE = "page"
IMAGE = "image"
ANNOTATION = "annotation"
FIELD = "field"
KINDS = (ADDED, DELETED, MODIFIED, FORMAT, VISUAL, PAGE, IMAGE, ANNOTATION, FIELD)

MODE_AUTO = "auto"
MODE_TEXT = "text"
MODE_VISUAL = "visual"

# Sensibilità del confronto visivo: tolleranza di colore (0–255), pixel diversi
# minimi per cella di 8×8 px e celle minime per regione.
SENSITIVITY = {
    "high": {"tolerance": 24, "cell_pixels": 2, "min_cells": 1},
    "medium": {"tolerance": 64, "cell_pixels": 6, "min_cells": 1},
    "low": {"tolerance": 110, "cell_pixels": 14, "min_cells": 3},
}
CELL = 8
MAX_SHIFT_MM = 5.0
MAX_PIXELS = 40_000_000
MM = 72.0 / 25.4

_SUBSET = re.compile(r"^[A-Z]{6}\+")
_STYLE_SUFFIX = re.compile(r"[-,]?(bold|italic|oblique|regular|roman|medium|semibold|black|light|book|bd|it|bi)+$",
                           re.IGNORECASE)
_PUNCT = set(string.punctuation) | set("«»“”‘’„–—…·¡¿")


STATUSES = ("accepted", "rejected", "review")
SESSION_VERSION = 1


def status_label(status: str, plural: bool = False) -> str:
    labels = {
        "accepted": (_("Accettata"), _("accettate")),
        "rejected": (_("Rifiutata"), _("rifiutate")),
        "review": (_("Da verificare"), _("da verificare")),
        "": (_("Senza stato"), _("senza stato")),
    }
    return labels.get(status, (status, status))[1 if plural else 0]


def kind_label(kind: str, plural: bool = False) -> str:
    labels = {
        ADDED: (_("Aggiunta"), _("aggiunte")),
        DELETED: (_("Eliminazione"), _("eliminazioni")),
        MODIFIED: (_("Modifica"), _("modifiche")),
        FORMAT: (_("Formattazione"), _("formattazione")),
        VISUAL: (_("Visiva"), _("visive")),
        PAGE: (_("Pagina"), _("pagine")),
        IMAGE: (_("Immagine"), _("immagini")),
        ANNOTATION: (_("Annotazione"), _("annotazioni")),
        FIELD: (_("Campo modulo"), _("campi modulo")),
    }
    return labels[kind][1 if plural else 0]


# =============================================================================
# Modello
# =============================================================================
@dataclass
class CompareOptions:
    mode: str = MODE_AUTO
    ignore_case: bool = False
    ignore_whitespace: bool = False
    ignore_punctuation: bool = False
    compare_format: bool = False
    dpi: int = 150
    sensitivity: str = "medium"
    auto_align: bool = True
    include_annotations: bool = True
    size_tolerance: float = 0.1
    color_tolerance: int = 24
    # esclusioni (CMP-08)
    ignore_headers_footers: bool = False
    band_top_mm: float = 0.0        # 0 = rilevamento automatico del testo ripetuto
    band_bottom_mm: float = 0.0
    ignore_page_numbers: bool = False
    exclusions: list = field(default_factory=list)   # [pagina o -1, x0, y0, x1, y1] in punti
    compare_structure: bool = True  # pagine spostate/ruotate, immagini, annotazioni, campi (CMP-09)
    match_pages_by_content: bool = True   # abbina anche pagine riordinate (pagine spostate)
    preset: str = ""                # "", "text", "presentation", "drawing", "scan" (CMP-12)
    ocr_languages: str = "ita+eng"

    def criteria(self) -> list[str]:
        """Descrizione leggibile dei criteri applicati."""
        modes = {MODE_AUTO: _("Automatico"), MODE_TEXT: _("Solo testo"), MODE_VISUAL: _("Solo visivo")}
        sens = {"high": _("Alta"), "medium": _("Media"), "low": _("Bassa")}
        out = [_("Tipo di confronto: {m}").format(m=modes.get(self.mode, self.mode))]
        presets = {"text": _("Testo"), "presentation": _("Presentazione"), "drawing": _("Disegno tecnico"),
                   "scan": _("Scansione")}
        if self.preset in presets:
            out.append(_("Preset: {p}").format(p=presets[self.preset]))
            if self.preset == "scan":
                out.append(_("Riconoscimento del testo in memoria: {l}").format(l=self.ocr_languages))
        if self.mode != MODE_VISUAL:
            if self.ignore_case:
                out.append(_("Ignora maiuscole/minuscole"))
            if self.ignore_whitespace:
                out.append(_("Ignora spazi e a capo"))
            if self.ignore_punctuation:
                out.append(_("Ignora punteggiatura"))
            if self.compare_format:
                out.append(_("Confronta formattazione"))
        if self.mode != MODE_TEXT:
            s = SENSITIVITY.get(self.sensitivity, SENSITIVITY["medium"])
            out.append(_("Sensibilità visiva: {s} (tolleranza {t}, {p} px per cella)").format(
                s=sens.get(self.sensitivity, self.sensitivity), t=s["tolerance"], p=s["cell_pixels"]))
            out.append(_("Risoluzione: {d} dpi").format(d=self.dpi))
            out.append(_("Allineamento automatico") if self.auto_align else _("Allineamento automatico disattivato"))
            if not self.include_annotations:
                out.append(_("Ignora annotazioni"))
        if self.ignore_headers_footers:
            out.append(_("Ignora intestazioni e piè di pagina"))
        if self.ignore_page_numbers:
            out.append(_("Ignora numeri di pagina"))
        if self.exclusions:
            out.append(_("Aree escluse: {n}").format(n=len(self.exclusions)))
        return out

    def to_dict(self) -> dict:
        return asdict(self)

    def excluded_areas(self, page: int) -> list[tuple]:
        return [tuple(e[1:5]) for e in self.exclusions if int(e[0]) in (-1, page)]

    @classmethod
    def from_dict(cls, data: dict) -> "CompareOptions":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in fields})


@dataclass
class SideInfo:
    label: str
    path: str | None
    sha256: str
    trailer_id: str
    page_count: int
    pages: list[int]
    unsaved: bool = False
    text_allowed: bool = True
    repaired: bool = False

    @property
    def title(self) -> str:
        return self.label + (" " + _("(non salvato)") if self.unsaved else "")


@dataclass
class Difference:
    kind: str
    subtype: str = "text"
    text_a: str = ""
    text_b: str = ""
    rects_a: list[tuple] = field(default_factory=list)   # (pagina, x0, y0, x1, y1)
    rects_b: list[tuple] = field(default_factory=list)
    point_a: tuple | None = None    # punto di inserimento sul lato opposto (aggiunte)
    point_b: tuple | None = None    # (eliminazioni)
    detail: str = ""
    area: float = 0.0               # percentuale di area variata (visive)
    id: int = 0
    status: str = ""
    note: str = ""

    # ---------------------------------------------------------------- utilità
    def pages(self, side: str) -> list[int]:
        rects = self.rects_a if side == "a" else self.rects_b
        point = self.point_a if side == "a" else self.point_b
        pages = sorted({r[0] for r in rects})
        if not pages and point is not None:
            pages = [point[0]]
        return pages

    def page_label(self, side: str) -> str:
        pages = self.pages(side)
        if not pages:
            return "–"
        if pages[0] == pages[-1]:
            return str(pages[0] + 1)
        return f"{pages[0] + 1}–{pages[-1] + 1}"

    def locations(self, side: str) -> list[tuple]:
        """Riquadri da mostrare su un lato (o il punto di inserimento)."""
        rects = self.rects_a if side == "a" else self.rects_b
        if rects:
            return rects
        point = self.point_a if side == "a" else self.point_b
        return [point] if point is not None else []

    def excerpt(self, limit: int = 120) -> str:
        if self.kind == ADDED:
            text = self.text_b
        elif self.kind == DELETED:
            text = self.text_a
        elif self.kind == MODIFIED:
            text = f"{self.text_a} → {self.text_b}"
        else:
            text = self.detail or self.text_b or self.text_a
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def as_text(self) -> str:
        lines = [f"{kind_label(self.kind)} — A p. {self.page_label('a')} / B p. {self.page_label('b')}"]
        if self.text_a:
            lines.append(_("Prima: {t}").format(t=self.text_a))
        if self.text_b:
            lines.append(_("Dopo: {t}").format(t=self.text_b))
        if self.detail:
            lines.append(self.detail)
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompareResult:
    side_a: SideInfo
    side_b: SideInfo
    options: CompareOptions
    differences: list[Difference] = field(default_factory=list)
    pairs: list[tuple[int, int]] = field(default_factory=list)
    anchors: list[tuple[float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    visual_only_pages: list[int] = field(default_factory=list)   # pagine di A confrontate solo visivamente
    shifts: dict = field(default_factory=dict)                   # (pa, pb) -> (dx, dy) in punti
    identical_files: bool = False
    excluded_a: dict = field(default_factory=dict)               # pagina -> riquadri di testo escluso
    excluded_b: dict = field(default_factory=dict)

    def counts(self, differences: list[Difference] | None = None) -> dict[str, int]:
        out = {k: 0 for k in KINDS}
        for d in self.differences if differences is None else differences:
            out[d.kind] += 1
        return out

    def summary(self) -> str:
        c = self.counts()
        parts = [f"{c[k]} {kind_label(k, plural=True)}" for k in KINDS if c[k] or k in (ADDED, DELETED, MODIFIED)]
        return " · ".join(parts)

    def map_a_to_b(self, pos: float) -> float:
        return _interpolate(self.anchors, pos, 0)

    def map_b_to_a(self, pos: float) -> float:
        return _interpolate(self.anchors, pos, 1)

    def partner_b(self, page_a: int) -> int | None:
        for pa, pb in self.pairs:
            if pa == page_a:
                return pb
        return None

    def partner_a(self, page_b: int) -> int | None:
        for pa, pb in self.pairs:
            if pb == page_b:
                return pa
        return None


def _interpolate(anchors: list[tuple[float, float]], pos: float, src: int) -> float:
    if not anchors:
        return pos
    dst = 1 - src
    pts = sorted(anchors, key=lambda a: (a[src], a[dst]))
    xs = [p[src] for p in pts]
    i = bisect.bisect_right(xs, pos) - 1
    if i < 0:
        return pts[0][dst]
    if i >= len(pts) - 1:
        return pts[-1][dst]
    x0, y0 = pts[i][src], pts[i][dst]
    x1, y1 = pts[i + 1][src], pts[i + 1][dst]
    if x1 <= x0:
        return y0
    return y0 + (y1 - y0) * (pos - x0) / (x1 - x0)


# =============================================================================
# Apertura dei due lati
# =============================================================================
def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def looks_like_pdf(data: bytes) -> bool:
    return b"%PDF-" in data[:1024]


def open_side(data: bytes, password: str | None = None) -> tuple[pymupdf.Document, bool, bool]:
    """Apre i dati di un lato. Restituisce (documento, testo_consentito, riparato).

    Solleva DocumentError con messaggi chiari (non PDF, danneggiato, password)."""
    if not looks_like_pdf(data):
        raise DocumentError(_("Il file non è un PDF. Convertilo prima in PDF (modalità Converti)."))
    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception as e:
            raise DocumentError(_("Il file è danneggiato e non può essere riparato: {e}").format(e=e)) from e
        auth = 1
        if doc.needs_pass:
            if password is None:
                doc.close()
                raise PasswordRequired()
            auth = doc.authenticate(password)
            if not auth:
                doc.close()
                raise WrongPassword()
        if doc.page_count == 0:
            doc.close()
            raise DocumentError(_("Il documento non contiene pagine."))
        encrypted = bool((doc.metadata or {}).get("encryption"))
        owner = auth in (4, 6) or not encrypted
        text_allowed = owner or bool(doc.permissions & pymupdf.PDF_PERM_COPY)
        repaired = bool(getattr(doc, "is_repaired", False))
        return doc, text_allowed, repaired


def trailer_id(doc: pymupdf.Document) -> str:
    try:
        with ENGINE_LOCK:
            kind, value = doc.xref_get_key(-1, "ID")
        return value if kind != "null" else ""
    except Exception:
        return ""


def page_range(count: int, rng: tuple[int, int] | None) -> list[int]:
    if not rng:
        return list(range(count))
    a, b = rng
    a = max(0, min(int(a), count - 1))
    b = max(a, min(int(b), count - 1))
    return list(range(a, b + 1))


# =============================================================================
# Estrazione del testo
# =============================================================================
@dataclass
class Token:
    key: str
    text: str
    page: int
    rects: list[tuple]
    line: int
    font: str = ""
    size: float = 0.0
    color: int = 0
    bold: bool = False
    italic: bool = False


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).replace("­", "")
    return text


def _font_family(name: str) -> str:
    name = _SUBSET.sub("", name or "")
    base = _STYLE_SUFFIX.sub("", name)
    return base or name


def extract_tokens(doc: pymupdf.Document, pages: list[int], progress=None, base: float = 0.0,
                   span: float = 0.0) -> tuple[list[Token], list[int], list[int]]:
    """Parole nell'ordine di lettura delle pagine scelte.

    Restituisce (parole, pagine_senza_testo, pagine_non_decodificabili)."""
    tokens: list[Token] = []
    empty: list[int] = []
    undecodable: list[int] = []
    line_no = 0
    n = max(1, len(pages))
    for k, pno in enumerate(pages):
        if progress is not None:
            progress.update(base + span * k / n)
        with ENGINE_LOCK:
            page = doc[pno]
            raw = page.get_text("rawdict", sort=True, flags=pymupdf.TEXT_PRESERVE_LIGATURES
                                | pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
        page_tokens: list[Token] = []
        bad = False
        for block in raw.get("blocks", []):
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                line_no += 1
                current: list[tuple] = []   # (carattere, bbox, span)

                def flush() -> None:
                    nonlocal bad
                    if not current:
                        return
                    text = _normalize("".join(c for c, _b, _s in current))
                    if "�" in text:
                        bad = True
                        current.clear()
                        return
                    x0 = min(b[0] for _c, b, _s in current)
                    y0 = min(b[1] for _c, b, _s in current)
                    x1 = max(b[2] for _c, b, _s in current)
                    y1 = max(b[3] for _c, b, _s in current)
                    sp = current[0][2]
                    flags = sp.get("flags", 0)
                    font = sp.get("font", "")
                    lower = font.lower()
                    page_tokens.append(Token(
                        key=text, text=text, page=pno, rects=[(pno, x0, y0, x1, y1)], line=line_no,
                        font=_font_family(font), size=float(sp.get("size", 0.0)), color=int(sp.get("color", 0)),
                        bold=bool(flags & 16) or "bold" in lower or "black" in lower,
                        italic=bool(flags & 2) or "italic" in lower or "oblique" in lower))
                    current.clear()

                for sp in line.get("spans", []):
                    for ch in sp.get("chars", []):
                        c = ch.get("c", "")
                        if not c or c.isspace():
                            flush()
                            continue
                        current.append((c, ch["bbox"], sp))
                flush()
        if not page_tokens:
            empty.append(pno)
        if bad:
            undecodable.append(pno)
        tokens.extend(page_tokens)
    return _join_hyphens(tokens), empty, undecodable


def _join_hyphens(tokens: list[Token]) -> list[Token]:
    """Ricompone le parole sillabate a fine riga ("contrat-" + "to")."""
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        # solo se è l'ultima parola della riga e la riga dopo inizia in minuscolo
        if (nxt is not None and len(t.text) > 1 and t.text[-1] in "-\u2010" and t.text[-2].isalpha()
                and nxt.line != t.line and nxt.text[:1].islower()):
            text = t.text[:-1] + nxt.text
            out.append(Token(text, text, t.page, t.rects + nxt.rects, t.line, t.font, t.size, t.color,
                             t.bold, t.italic))
            i += 2
            continue
        out.append(t)
        i += 1
    return out


def _apply_key(tokens: list[Token], options: CompareOptions) -> list[Token]:
    out = []
    for t in tokens:
        key = t.text
        if options.ignore_punctuation:
            key = "".join(c for c in key if c not in _PUNCT and unicodedata.category(c)[0] != "P")
            if not key:
                continue
        if options.ignore_case:
            key = key.casefold()
        t.key = key
        out.append(t)
    return out


# =============================================================================
# Esclusioni (CMP-08)
# =============================================================================
_PAGE_NUMBER = re.compile(r"^[-–—(\[]?\s*(pagina|pag\.?|page|p\.)?\s*#+(\s*(di|of|/|su)\s*#+)?\s*[-–—)\]]?$",
                          re.IGNORECASE)


def _line_groups(tokens: list[Token]) -> dict[int, list[Token]]:
    lines: dict[int, list[Token]] = {}
    for t in tokens:
        lines.setdefault(t.line, []).append(t)
    return lines


def excluded_lines(tokens: list[Token], pages: list[int], heights: dict, options: CompareOptions) -> set[int]:
    """Righe da escludere: intestazioni/piè ripetuti, numeri di pagina, fasce."""
    out: set[int] = set()
    if not (options.ignore_headers_footers or options.ignore_page_numbers):
        return out
    lines = _line_groups(tokens)
    info = {}
    for ln, toks in lines.items():
        page = toks[0].page
        h = heights.get(page, 842.0)
        y0 = min(r[2] for t in toks for r in t.rects)
        y1 = max(r[4] for t in toks for r in t.rects)
        text = re.sub(r"\d+", "#", " ".join(t.text for t in toks)).strip()
        in_band = y1 <= 0.15 * h or y0 >= 0.85 * h
        info[ln] = (page, y0, y1, text, in_band, h)
        if options.ignore_page_numbers and in_band and _PAGE_NUMBER.match(text):
            out.add(ln)
        if options.ignore_headers_footers:
            top = options.band_top_mm * MM
            bottom = options.band_bottom_mm * MM
            if (top and y1 <= top) or (bottom and y0 >= h - bottom):
                out.add(ln)
    if options.ignore_headers_footers and len(pages) >= 2:
        groups: dict[tuple, set[int]] = {}
        for ln, (page, y0, _y1, text, in_band, _h) in info.items():
            if in_band:
                groups.setdefault((text, round(y0 / 6)), set()).add(page)
        n_all = len(pages)
        n_even = len([p for p in pages if p % 2 == 0])
        n_odd = n_all - n_even
        repeated = set()
        for key, pg in groups.items():
            even = len([p for p in pg if p % 2 == 0])
            odd = len(pg) - even
            if len(pg) >= 2 and (len(pg) * 2 >= n_all or (n_even >= 2 and even * 2 >= n_even and even >= 2)
                                 or (n_odd >= 2 and odd * 2 >= n_odd and odd >= 2)):
                repeated.add(key)
        for ln, (page, y0, _y1, text, in_band, _h) in info.items():
            if in_band and (text, round(y0 / 6)) in repeated:
                out.add(ln)
    return out


def _inside(r: tuple, area: tuple) -> bool:
    return r[0] >= area[0] - 0.5 and r[1] >= area[1] - 0.5 and r[2] <= area[2] + 0.5 and r[3] <= area[3] + 0.5


def apply_exclusions(tokens: list[Token], pages: list[int], heights: dict, options: CompareOptions) -> list[Token]:
    drop = excluded_lines(tokens, pages, heights, options)
    out = []
    for t in tokens:
        if t.line in drop:
            continue
        areas = options.excluded_areas(t.page)
        if areas and all(any(_inside(r[1:], a) for a in areas) for r in t.rects):
            continue
        out.append(t)
    return out


def excluded_boxes(tokens_all: list[Token], kept: list[Token]) -> dict[int, list[tuple]]:
    """Riquadri del testo escluso, per pagina (per scartare anche le differenze visive)."""
    kept_ids = {id(t) for t in kept}
    out: dict[int, list[tuple]] = {}
    for t in tokens_all:
        if id(t) not in kept_ids:
            for r in t.rects:
                out.setdefault(r[0], []).append(r[1:])
    return out


def _center_in(r: tuple, areas: list[tuple]) -> bool:
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    return any(a[0] <= cx <= a[2] and a[1] <= cy <= a[3] for a in areas)


# =============================================================================
# Differenze di testo
# =============================================================================
def _lines(tokens: list[Token]) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    start = 0
    for i in range(1, len(tokens) + 1):
        if i == len(tokens) or tokens[i].line != tokens[start].line:
            out.append((" ".join(t.key for t in tokens[start:i]), start, i))
            start = i
    return out


def diff_opcodes(a: list[Token], b: list[Token], progress=None) -> list[tuple[str, int, int, int, int]]:
    """Opcode di parola (come difflib) con un primo allineamento per righe."""
    la, lb = _lines(a), _lines(b)
    sm = difflib.SequenceMatcher(None, [x[0] for x in la], [x[0] for x in lb], autojunk=False)
    ops: list[tuple[str, int, int, int, int]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if progress is not None:
            progress.check()
        a0 = la[i1][1] if i1 < len(la) else len(a)
        a1 = la[i2 - 1][2] if i2 > i1 else a0
        b0 = lb[j1][1] if j1 < len(lb) else len(b)
        b1 = lb[j2 - 1][2] if j2 > j1 else b0
        if tag == "equal":
            ops.append(("equal", a0, a1, b0, b1))
            continue
        if a0 == a1 or b0 == b1:
            ops.append(("delete" if b0 == b1 else "insert", a0, a1, b0, b1))
            continue
        big = (a1 - a0) > 20000 or (b1 - b0) > 20000
        wm = difflib.SequenceMatcher(None, [t.key for t in a[a0:a1]], [t.key for t in b[b0:b1]], autojunk=big)
        for wtag, x1, x2, y1, y2 in wm.get_opcodes():
            ops.append((wtag, a0 + x1, a0 + x2, b0 + y1, b0 + y2))
    # accorpa opcode adiacenti dello stesso tipo
    merged: list[list] = []
    for op in ops:
        if op[1] == op[2] and op[3] == op[4]:
            continue
        if merged and merged[-1][0] == op[0]:
            merged[-1][2], merged[-1][4] = op[2], op[4]
        elif merged and op[0] != "equal" and merged[-1][0] != "equal":
            merged[-1] = ["replace", merged[-1][1], op[2], merged[-1][3], op[4]]
        else:
            merged.append(list(op))
    return [tuple(m) for m in merged]


def _line_rects(tokens: list[Token]) -> list[tuple]:
    """Riquadri delle parole uniti per riga."""
    out: list[list] = []
    for t in tokens:
        for r in t.rects:
            if out and out[-1][0] == r[0] and abs(out[-1][2] - r[2]) < 2 and abs(out[-1][4] - r[4]) < 2 \
                    and r[1] >= out[-1][1] - 1:
                out[-1][3] = max(out[-1][3], r[3])
                out[-1][1] = min(out[-1][1], r[1])
            else:
                out.append(list(r))
    return [tuple(r) for r in out]


def _insertion_point(tokens: list[Token], index: int, fallback_page: int | None) -> tuple | None:
    if index > 0 and tokens:
        r = tokens[min(index, len(tokens)) - 1].rects[-1]
        return (r[0], r[3], r[2], r[3] + 1.5, r[4])
    if tokens:
        r = tokens[0].rects[0]
        return (r[0], r[1] - 1.5, r[2], r[1], r[4])
    if fallback_page is not None:
        return (fallback_page, 0, 0, 2, 2)
    return None


def text_differences(a: list[Token], b: list[Token], ops, options: CompareOptions) -> list[Difference]:
    diffs: list[Difference] = []
    for tag, a0, a1, b0, b1 in ops:
        if tag == "equal":
            continue
        ta, tb = a[a0:a1], b[b0:b1]
        text_a = " ".join(t.text for t in ta)
        text_b = " ".join(t.text for t in tb)
        if tag == "replace" and options.ignore_whitespace:
            if "".join(t.key for t in ta) == "".join(t.key for t in tb):
                continue
        if tag == "insert":
            diffs.append(Difference(ADDED, text_b=text_b, rects_b=_line_rects(tb),
                                    point_a=_insertion_point(a, a0, None)))
        elif tag == "delete":
            diffs.append(Difference(DELETED, text_a=text_a, rects_a=_line_rects(ta),
                                    point_b=_insertion_point(b, b0, None)))
        else:
            diffs.append(Difference(MODIFIED, text_a=text_a, text_b=text_b, rects_a=_line_rects(ta),
                                    rects_b=_line_rects(tb), detail=_char_detail(text_a, text_b)))
    return diffs


def _char_detail(a: str, b: str) -> str:
    """Dettaglio al carattere per le parole modificate (es. "1[0→2]0")."""
    if not a or not b or len(a) > 80 or len(b) > 80 or " " in a or " " in b:
        return ""
    p = 0
    while p < min(len(a), len(b)) and a[p] == b[p]:
        p += 1
    s = 0
    while s < min(len(a), len(b)) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
        s += 1
    if not p and not s:
        return ""
    return f"{a[:p]}[{a[p:len(a) - s]}→{b[p:len(b) - s]}]{a[len(a) - s:]}"


def _color_hex(c: int) -> str:
    return f"#{c:06X}"


def _color_distance(c1: int, c2: int) -> float:
    r1, g1, b1 = (c1 >> 16) & 255, (c1 >> 8) & 255, c1 & 255
    r2, g2, b2 = (c2 >> 16) & 255, (c2 >> 8) & 255, c2 & 255
    return max(abs(r1 - r2), abs(g1 - g2), abs(b1 - b2))


def _style_name(t: Token) -> str:
    if t.bold and t.italic:
        return _("grassetto corsivo")
    if t.bold:
        return _("grassetto")
    if t.italic:
        return _("corsivo")
    return _("normale")


def format_details(ta: Token, tb: Token, options: CompareOptions) -> list[str]:
    out = []
    if ta.font.lower() != tb.font.lower():
        out.append(_("font {a} → {b}").format(a=ta.font, b=tb.font))
    if abs(ta.size - tb.size) > options.size_tolerance:
        out.append(_("dimensione {a} → {b} pt").format(a=_fmt_num(ta.size), b=_fmt_num(tb.size)))
    if _color_distance(ta.color, tb.color) > options.color_tolerance:
        out.append(_("colore {a} → {b}").format(a=_color_hex(ta.color), b=_color_hex(tb.color)))
    if (ta.bold, ta.italic) != (tb.bold, tb.italic):
        out.append(_("stile {a} → {b}").format(a=_style_name(ta), b=_style_name(tb)))
    return out


def _fmt_num(v: float) -> str:
    r = round(v, 1)
    return str(int(r)) if r == int(r) else str(r).replace(".", ",")


def format_differences(a: list[Token], b: list[Token], ops, options: CompareOptions,
                       progress=None) -> list[Difference]:
    diffs: list[Difference] = []
    for tag, a0, a1, b0, b1 in ops:
        if tag != "equal":
            continue
        if progress is not None:
            progress.check()
        group: list[tuple[Token, Token]] = []
        group_detail: str | None = None
        for k in range(a1 - a0):
            ta, tb = a[a0 + k], b[b0 + k]
            detail = "; ".join(format_details(ta, tb, options))
            if detail and detail == group_detail:
                group.append((ta, tb))
                continue
            if group:
                diffs.append(_format_diff(group, group_detail))
            group, group_detail = ([(ta, tb)], detail) if detail else ([], None)
        if group:
            diffs.append(_format_diff(group, group_detail))
    return diffs


def _format_diff(group: list[tuple[Token, Token]], detail: str) -> Difference:
    ta = [g[0] for g in group]
    tb = [g[1] for g in group]
    return Difference(FORMAT, subtype="format", text_a=" ".join(t.text for t in ta),
                      text_b=" ".join(t.text for t in tb), rects_a=_line_rects(ta), rects_b=_line_rects(tb),
                      detail=detail)


# =============================================================================
# Abbinamento delle pagine e ancore
# =============================================================================
def pair_pages(pages_a: list[int], pages_b: list[int], weights: dict[tuple[int, int], int]) -> list[tuple[int, int]]:
    """Abbinamento monotono che massimizza le parole uguali; il resto in sequenza."""
    if not weights:
        return list(zip(pages_a, pages_b))
    na, nb = len(pages_a), len(pages_b)
    ia = {p: i for i, p in enumerate(pages_a)}
    ib = {p: i for i, p in enumerate(pages_b)}
    w = np.zeros((na, nb), dtype=np.int64)
    for (pa, pb), c in weights.items():
        if pa in ia and pb in ib:
            w[ia[pa], ib[pb]] = c
    score = np.zeros((na + 1, nb + 1), dtype=np.int64)
    for i in range(1, na + 1):
        row_prev = score[i - 1]
        row = score[i]
        wi = w[i - 1]
        for j in range(1, nb + 1):
            best = row_prev[j]
            if row[j - 1] > best:
                best = row[j - 1]
            if wi[j - 1] > 0 and row_prev[j - 1] + wi[j - 1] > best:
                best = row_prev[j - 1] + wi[j - 1]
            row[j] = best
    anchors: list[tuple[int, int]] = []
    i, j = na, nb
    while i > 0 and j > 0:
        if w[i - 1, j - 1] > 0 and score[i, j] == score[i - 1, j - 1] + w[i - 1, j - 1]:
            anchors.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif score[i, j] == score[i - 1, j]:
            i -= 1
        else:
            j -= 1
    anchors.reverse()
    # le pagine senza parole in comune tra due ancore si abbinano in sequenza
    pairs: list[tuple[int, int]] = []
    prev = (-1, -1)
    for anchor in anchors + [(na, nb)]:
        ga = list(range(prev[0] + 1, anchor[0]))
        gb = list(range(prev[1] + 1, anchor[1]))
        if len(ga) == len(gb):
            pairs.extend(zip(ga, gb))
        if anchor != (na, nb):
            pairs.append(anchor)
        prev = anchor
    return [(pages_a[x], pages_b[y]) for x, y in pairs]


def _lis_nondecreasing(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sottosequenza più lunga non decrescente nella seconda coordinata."""
    tails: list[float] = []
    tail_idx: list[int] = []
    prev = [-1] * len(points)
    for i, (_x, y) in enumerate(points):
        k = bisect.bisect_right(tails, y)
        if k == len(tails):
            tails.append(y)
            tail_idx.append(i)
        else:
            tails[k] = y
            tail_idx[k] = i
        prev[i] = tail_idx[k - 1] if k > 0 else -1
    out = []
    i = tail_idx[-1] if tail_idx else -1
    while i >= 0:
        out.append(points[i])
        i = prev[i]
    return out[::-1]


def build_anchors(pairs, a: list[Token], b: list[Token], ops, heights_a: dict, heights_b: dict,
                  count_a: int, count_b: int) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = [(0.0, 0.0), (float(count_a), float(count_b))]
    for pa, pb in pairs:
        pts.append((float(pa), float(pb)))
        pts.append((pa + 0.999, pb + 0.999))
    seen_lines = set()
    for tag, a0, a1, b0, b1 in ops:
        if tag != "equal":
            continue
        for k in range(a1 - a0):
            ta, tb = a[a0 + k], b[b0 + k]
            if ta.line in seen_lines:
                continue
            seen_lines.add(ta.line)
            ra, rb = ta.rects[0], tb.rects[0]
            pts.append((ra[0] + ra[2] / heights_a.get(ra[0], 842.0), rb[0] + rb[2] / heights_b.get(rb[0], 842.0)))
    pts = sorted(set(pts))
    return _lis_nondecreasing(pts)


# =============================================================================
# Confronto visivo
# =============================================================================
def render_page(doc: pymupdf.Document, pno: int, dpi: int, annots: bool = True,
                unrotated: bool = False) -> tuple[np.ndarray, float]:
    """Pagina come array RGB (H, W, 3). Restituisce anche la scala px/pt effettiva.

    Con `unrotated` la pagina è resa senza /Rotate: i pixel corrispondono alle
    coordinate della pagina non ruotata (quelle delle differenze)."""
    with ENGINE_LOCK:
        page = doc[pno]
        scale = dpi / 72.0
        pixels = page.rect.width * scale * page.rect.height * scale
        if pixels > MAX_PIXELS:
            scale *= (MAX_PIXELS / pixels) ** 0.5
        mat = pymupdf.Matrix(scale, scale)
        if unrotated and page.rotation:
            mat = mat.prerotate(-page.rotation)
        pix = page.get_pixmap(matrix=mat, alpha=False, annots=annots, colorspace=pymupdf.csRGB)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width * 3]
        return arr.reshape(pix.height, pix.width, 3).copy(), scale


def _neighborhood(img: np.ndarray, radius: int = 1) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape[:2]
    pad = np.pad(img, ((radius, radius), (radius, radius), (0, 0)), mode="edge")
    lo = img.copy()
    hi = img.copy()
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            v = pad[radius + dy: radius + dy + h, radius + dx: radius + dx + w]
            np.minimum(lo, v, out=lo)
            np.maximum(hi, v, out=hi)
    return lo, hi


def diff_mask(img_a: np.ndarray, img_b: np.ndarray, tolerance: int) -> np.ndarray:
    """Pixel diversi, robusto all'antialiasing: un pixel è diverso solo se
    nessun pixel vicino dell'altra immagine ha un colore simile."""
    h = min(img_a.shape[0], img_b.shape[0])
    w = min(img_a.shape[1], img_b.shape[1])
    a = img_a[:h, :w].astype(np.int16)
    b = img_b[:h, :w].astype(np.int16)
    lo_b, hi_b = _neighborhood(b)
    lo_a, hi_a = _neighborhood(a)
    out_a = ((a < lo_b - tolerance) | (a > hi_b + tolerance)).any(axis=2)
    out_b = ((b < lo_a - tolerance) | (b > hi_a + tolerance)).any(axis=2)
    return out_a | out_b


def regions_from_mask(mask: np.ndarray, cell_pixels: int, min_cells: int = 1, merge: int = 2,
                      cell: int = CELL) -> list[tuple[int, int, int, int]]:
    """Regioni (x0, y0, x1, y1 in pixel) delle aree variate, accorpando le vicine."""
    h, w = mask.shape
    gh, gw = -(-h // cell), -(-w // cell)
    padded = np.zeros((gh * cell, gw * cell), dtype=np.uint8)
    padded[:h, :w] = mask
    counts = padded.reshape(gh, cell, gw, cell).sum(axis=(1, 3))
    changed = counts >= cell_pixels
    if not changed.any():
        return []
    grown = changed.copy()
    for _i in range(merge):
        g = grown.copy()
        g[1:, :] |= grown[:-1, :]
        g[:-1, :] |= grown[1:, :]
        g[:, 1:] |= grown[:, :-1]
        g[:, :-1] |= grown[:, 1:]
        grown = g
    labels = np.zeros(grown.shape, dtype=np.int32)
    regions = []
    current = 0
    for sy, sx in zip(*np.nonzero(grown)):
        if labels[sy, sx]:
            continue
        current += 1
        labels[sy, sx] = current
        queue = deque([(sy, sx)])
        cells = []
        while queue:
            y, x = queue.popleft()
            if changed[y, x]:
                cells.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < gh and 0 <= nx < gw and grown[ny, nx] and not labels[ny, nx]:
                    labels[ny, nx] = current
                    queue.append((ny, nx))
        if len(cells) < min_cells:
            continue
        ys = [c[0] for c in cells]
        xs = [c[1] for c in cells]
        cy0, cy1, cx0, cx1 = min(ys) * cell, (max(ys) + 1) * cell, min(xs) * cell, (max(xs) + 1) * cell
        sub = mask[cy0:min(cy1, h), cx0:min(cx1, w)]
        yy, xx = np.nonzero(sub)
        if len(yy) == 0:
            continue
        regions.append((cx0 + int(xx.min()), cy0 + int(yy.min()), cx0 + int(xx.max()) + 1, cy0 + int(yy.max()) + 1))
    return regions


def estimate_shift(img_a: np.ndarray, img_b: np.ndarray, max_shift: int) -> tuple[int, int]:
    """Traslazione (dx, dy) in pixel da applicare a B per sovrapporla ad A
    (correlazione di fase)."""
    h = min(img_a.shape[0], img_b.shape[0])
    w = min(img_a.shape[1], img_b.shape[1])
    step = max(1, int(max(h, w) / 1400))
    a = 255.0 - img_a[:h:step, :w:step].mean(axis=2)
    b = 255.0 - img_b[:h:step, :w:step].mean(axis=2)
    if a.sum() == 0 or b.sum() == 0:
        return 0, 0
    fa = np.fft.rfft2(a)
    fb = np.fft.rfft2(b)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    corr = np.fft.irfft2(r, s=a.shape)
    lim = max(1, int(max_shift / step))
    hh, ww = corr.shape
    best, by, bx = -1.0, 0, 0
    for dy in range(-lim, lim + 1):
        row = corr[dy % hh]
        for dx in range(-lim, lim + 1):
            v = row[dx % ww]
            if v > best:
                best, by, bx = v, dy, dx
    return bx * step, by * step


def shift_image(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Trasla l'immagine riempiendo di bianco."""
    if dx == 0 and dy == 0:
        return img
    h, w = img.shape[:2]
    out = np.full_like(img, 255)
    xs0, xs1 = max(0, -dx), min(w, w - dx)
    ys0, ys1 = max(0, -dy), min(h, h - dy)
    if xs1 > xs0 and ys1 > ys0:
        out[ys0 + dy: ys1 + dy, xs0 + dx: xs1 + dx] = img[ys0:ys1, xs0:xs1]
    return out


@dataclass
class VisualPageResult:
    regions_a: list[tuple]          # (x0, y0, x1, y1) in punti di A
    regions_b: list[tuple]
    areas: list[float]
    shift: tuple[float, float]      # traslazione applicata a B (punti)
    misaligned: bool
    size_differs: bool


def compare_visual_pages(doc_a, pa: int, doc_b, pb: int, options: CompareOptions,
                         text_mask_a: list[tuple] | None = None, text_mask_b: list[tuple] | None = None
                         ) -> VisualPageResult:
    sens = SENSITIVITY.get(options.sensitivity, SENSITIVITY["medium"])
    img_a, sa = render_page(doc_a, pa, options.dpi, options.include_annotations, unrotated=True)
    img_b, sb = render_page(doc_b, pb, options.dpi, options.include_annotations, unrotated=True)
    scale = min(sa, sb)
    if abs(sa - sb) > 1e-6:   # riduzione per pagine enormi: stessa scala per entrambe
        img_a, sa = render_page(doc_a, pa, int(scale * 72), options.include_annotations, unrotated=True)
        img_b, sb = render_page(doc_b, pb, int(scale * 72), options.include_annotations, unrotated=True)
        scale = sa
    with ENGINE_LOCK:
        ra, rb = doc_a[pa].cropbox, doc_b[pb].cropbox
    size_differs = abs(ra.width - rb.width) > 1 or abs(ra.height - rb.height) > 1
    if img_a.shape == img_b.shape and np.array_equal(img_a, img_b):   # caso più comune: pagina invariata
        return VisualPageResult([], [], [], (0.0, 0.0), False, size_differs)
    max_shift = int(MAX_SHIFT_MM * MM * scale)
    mask = diff_mask(img_a, img_b, sens["tolerance"])
    dx = dy = 0
    misaligned = False
    if mask.any():
        sdx, sdy = estimate_shift(img_a, img_b, max_shift)
        if sdx or sdy:
            shifted = diff_mask(img_a, shift_image(img_b, sdx, sdy), sens["tolerance"])
            better = shifted.sum() < mask.sum() * 0.5
            if options.auto_align and better:
                mask, dx, dy = shifted, sdx, sdy
            elif better:
                misaligned = True
    regions = regions_from_mask(mask, sens["cell_pixels"], sens["min_cells"])
    page_area = max(1.0, mask.shape[0] * mask.shape[1])
    regions_a, regions_b, areas = [], [], []
    for x0, y0, x1, y1 in regions:
        ax = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
        bx = ((x0 - dx) / scale, (y0 - dy) / scale, (x1 - dx) / scale, (y1 - dy) / scale)
        if text_mask_a is not None and text_mask_b is not None:
            if _mostly_covered(ax, text_mask_a) or _mostly_covered(bx, text_mask_b):
                continue
        regions_a.append(ax)
        regions_b.append(bx)
        areas.append(100.0 * (x1 - x0) * (y1 - y0) / page_area)
    return VisualPageResult(regions_a, regions_b, areas, (dx / scale, dy / scale), misaligned, size_differs)


def _to_unrotated(r: tuple, derot: pymupdf.Matrix) -> tuple:
    rect = pymupdf.Rect(r) * derot
    rect.normalize()
    return (rect.x0, rect.y0, rect.x1, rect.y1)


def _mostly_covered(r: tuple, boxes: list[tuple]) -> bool:
    area = max(1e-6, (r[2] - r[0]) * (r[3] - r[1]))
    covered = 0.0
    for b in boxes:
        w = min(r[2], b[2]) - max(r[0], b[0])
        h = min(r[3], b[3]) - max(r[1], b[1])
        if w > 0 and h > 0:
            covered += w * h
    return covered >= 0.5 * area


def _rotated_boxes(doc, pno: int, tokens: list[Token]) -> list[tuple]:
    """Riquadri delle parole (con un piccolo margine) nelle coordinate del rendering."""
    return [(r[1] - 2, r[2] - 2, r[3] + 2, r[4] + 2) for t in tokens for r in t.rects if r[0] == pno]


# =============================================================================
# Confronto completo
# =============================================================================
@dataclass
class SideInput:
    data: bytes
    label: str
    path: str | None = None
    password: str | None = None
    page_range: tuple[int, int] | None = None   # indici 0-based inclusi
    unsaved: bool = False


class _NoProgress:
    def update(self, fraction=None, text=None) -> None:
        pass

    def check(self) -> None:
        pass


def _ocr_function():
    try:
        from foliant.modules.ocr.engine import ocr_pdf_bytes
    except Exception:
        return None
    return ocr_pdf_bytes


def _ocr_in_memory(doc: pymupdf.Document, options: CompareOptions, progress) -> pymupdf.Document:
    """Riconoscimento del testo temporaneo (preset Scansione): i file non cambiano."""
    fn = _ocr_function()
    with ENGINE_LOCK:
        plain = doc.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
    from .engine import run_ocr
    langs = [x for x in re.split(r"[+,\s]+", options.ocr_languages or "ita+eng") if x]
    data = run_ocr(fn, plain, langs)
    with ENGINE_LOCK:
        new = pymupdf.open(stream=data, filetype="pdf")
        doc.close()
    return new


def run_compare(progress, side_a: SideInput, side_b: SideInput, options: CompareOptions | None = None) -> CompareResult:
    """Confronta A e B. `progress` come in `ctx.run_task` (può essere None)."""
    options = options or CompareOptions()
    progress = progress or _NoProgress()
    doc_a, allow_a, rep_a = open_side(side_a.data, side_a.password)
    try:
        doc_b, allow_b, rep_b = open_side(side_b.data, side_b.password)
    except Exception:
        doc_a.close()
        raise
    notes = []
    try:
        if options.preset == "scan" and sha256(side_a.data) != sha256(side_b.data):
            if _ocr_function() is None:
                notes.append(_("OCR non disponibile (modulo OCR non installato): eseguito solo il confronto "
                               "visivo."))
                options = CompareOptions.from_dict({**options.to_dict(), "mode": MODE_VISUAL})
            else:
                progress.update(0.0, _("Riconoscimento del testo di A…"))
                doc_a = _ocr_in_memory(doc_a, options, progress)
                progress.update(0.0, _("Riconoscimento del testo di B…"))
                doc_b = _ocr_in_memory(doc_b, options, progress)
        result = _compare(progress, doc_a, doc_b, side_a, side_b, allow_a, allow_b, rep_a, rep_b, options)
        result.warnings[:0] = notes
        return result
    finally:
        with ENGINE_LOCK:
            doc_a.close()
            doc_b.close()


def _side_info(doc, side: SideInput, allowed: bool, repaired: bool) -> SideInfo:
    with ENGINE_LOCK:
        count = doc.page_count
    return SideInfo(side.label, side.path, sha256(side.data), trailer_id(doc), count,
                    page_range(count, side.page_range), side.unsaved, allowed, repaired)


def _compare(progress, doc_a, doc_b, side_a, side_b, allow_a, allow_b, rep_a, rep_b,
             options: CompareOptions) -> CompareResult:
    info_a = _side_info(doc_a, side_a, allow_a, rep_a)
    info_b = _side_info(doc_b, side_b, allow_b, rep_b)
    result = CompareResult(info_a, info_b, options)
    for info in (info_a, info_b):
        if info.repaired:
            result.warnings.append(_("«{n}» era danneggiato ed è stato riparato in memoria.").format(n=info.label))
    with ENGINE_LOCK:
        heights_a = {p: doc_a[p].rect.height or 1.0 for p in info_a.pages}
        heights_b = {p: doc_b[p].rect.height or 1.0 for p in info_b.pages}
    same = info_a.sha256 == info_b.sha256 and info_a.pages == info_b.pages
    if same:
        result.identical_files = True
        result.pairs = list(zip(info_a.pages, info_b.pages))
        result.anchors = [(0.0, 0.0), (float(info_a.page_count), float(info_b.page_count))]
        progress.update(1.0)
        return result

    use_text = options.mode != MODE_VISUAL
    if use_text and not (allow_a and allow_b):
        if options.mode == MODE_TEXT:
            raise DocumentError(_("Il documento vieta l'estrazione del contenuto: il confronto del testo non è "
                                  "disponibile. Usa il confronto visivo oppure fornisci la password dei permessi."))
        result.warnings.append(_("Un documento vieta l'estrazione del contenuto: eseguito solo il confronto visivo."))
        use_text = False
    use_visual = options.mode != MODE_TEXT

    ta: list[Token] = []
    tb: list[Token] = []
    ops: list = []
    empty_a: list[int] = []
    empty_b: list[int] = []
    if use_text:
        progress.update(0.0, _("Lettura del testo di A…"))
        ta, empty_a, bad_a = extract_tokens(doc_a, info_a.pages, progress, 0.0, 0.15)
        progress.update(0.15, _("Lettura del testo di B…"))
        tb, empty_b, bad_b = extract_tokens(doc_b, info_b.pages, progress, 0.15, 0.15)
        for p in bad_a:
            result.warnings.append(_("Testo non decodificabile a pagina {n} di A.").format(n=p + 1))
        for p in bad_b:
            result.warnings.append(_("Testo non decodificabile a pagina {n} di B.").format(n=p + 1))
        raw_a, raw_b = ta, tb
        ta = apply_exclusions(ta, info_a.pages, heights_a, options)
        tb = apply_exclusions(tb, info_b.pages, heights_b, options)
        result.excluded_a = excluded_boxes(raw_a, ta)
        result.excluded_b = excluded_boxes(raw_b, tb)
        ta = _apply_key(ta, options)
        tb = _apply_key(tb, options)
        progress.update(0.3, _("Confronto del testo…"))
        ops_all = diff_opcodes(ta, tb, progress)
        moved = detect_moved_pages(ta, tb, ops_all) if options.match_pages_by_content else []
        moved_a = {p for p, _q in moved}
        moved_b = {q for _p, q in moved}
        main_a = [t for t in ta if t.page not in moved_a]
        main_b = [t for t in tb if t.page not in moved_b]
        segments = [(main_a, main_b, diff_opcodes(main_a, main_b, progress) if moved else ops_all)]
        for pa, pb in moved:
            sa = [t for t in ta if t.page == pa]
            sb = [t for t in tb if t.page == pb]
            segments.append((sa, sb, diff_opcodes(sa, sb, progress)))
        matched = 0
        weights: dict[tuple[int, int], int] = {}
        for sa, sb, sops in segments:
            result.differences.extend(text_differences(sa, sb, sops, options))
            if options.compare_format:
                result.differences.extend(format_differences(sa, sb, sops, options, progress))
            for tag, a0, a1, b0, b1 in sops:
                if tag == "equal":
                    matched += a1 - a0
                    if sa is main_a:
                        for k in range(a1 - a0):
                            key = (sa[a0 + k].page, sb[b0 + k].page)
                            weights[key] = weights.get(key, 0) + 1
        ta, tb, ops = main_a, main_b, segments[0][2]
        total = len(ta) + len(tb) + sum(len(x[0]) + len(x[1]) for x in segments[1:])
        if total and 2 * matched / total < 0.2:
            result.warnings.append(_("I documenti sembrano non correlati: oltre l'80% delle parole è diverso."))
        for pa, pb in moved:
            with ENGINE_LOCK:
                ra, rb = doc_a[pa].rect, doc_b[pb].rect
            result.differences.append(Difference(
                PAGE, subtype="moved", rects_a=[(pa, 0, 0, ra.width, ra.height)],
                rects_b=[(pb, 0, 0, rb.width, rb.height)],
                detail=_("pagina spostata: {a} → {b}").format(a=pa + 1, b=pb + 1)))
        all_tokens_a = main_a + [t for x in segments[1:] for t in x[0]]
        all_tokens_b = main_b + [t for x in segments[1:] for t in x[1]]
        pages_a = [p for p in info_a.pages if p not in moved_a]
        pages_b = [p for p in info_b.pages if p not in moved_b]
        result.pairs = sorted(pair_pages(pages_a, pages_b, weights) + list(moved))
    else:
        result.pairs = list(zip(info_a.pages, info_b.pages))
    progress.update(0.4)

    paired_a = {p for p, _q in result.pairs}
    paired_b = {q for _p, q in result.pairs}
    if use_text:
        ta_all, tb_all = all_tokens_a, all_tokens_b
    else:
        ta_all, tb_all = ta, tb
    text_pages_a = {t.page for t in ta_all}
    text_pages_b = {t.page for t in tb_all}
    # pagine presenti solo da un lato (e senza testo, altrimenti già coperte dal testo)
    for pb in info_b.pages:
        if pb not in paired_b and pb not in text_pages_b:
            prev = [pa for pa, q in result.pairs if q < pb]
            pa = prev[-1] if prev else (info_a.pages[0] if info_a.pages else 0)
            with ENGINE_LOCK:
                r = doc_b[pb].rect
                ha = doc_a[pa].rect
            y = ha.height - 1.5 if prev else 0
            result.differences.append(Difference(
                ADDED, subtype="page", text_b=_("Pagina {n} presente solo in B").format(n=pb + 1),
                rects_b=[(pb, 0, 0, r.width, r.height)], point_a=(pa, 0, y, ha.width, y + 1.5)))
    for pa in info_a.pages:
        if pa not in paired_a and pa not in text_pages_a:
            prev = [q for p, q in result.pairs if p < pa]
            pb = prev[-1] if prev else (info_b.pages[0] if info_b.pages else 0)
            with ENGINE_LOCK:
                r = doc_a[pa].rect
                hb = doc_b[pb].rect
            y = hb.height - 1.5 if prev else 0
            result.differences.append(Difference(
                DELETED, subtype="page", text_a=_("Pagina {n} presente solo in A").format(n=pa + 1),
                rects_a=[(pa, 0, 0, r.width, r.height)], point_b=(pb, 0, y, hb.width, y + 1.5)))

    if options.compare_structure:
        progress.update(0.4, _("Confronto della struttura…"))
        result.differences.extend(field_differences(doc_a, info_a.pages, doc_b, info_b.pages))
    n = max(1, len(result.pairs))
    for k, (pa, pb) in enumerate(result.pairs):
        progress.update(0.4 + 0.58 * k / n, _("Confronto delle pagine: {i} di {n}").format(i=k + 1, n=n))
        skip: list[tuple] = []
        if options.compare_structure:
            struct = structure_pair_differences(doc_a, pa, doc_b, pb, options)
            result.differences.extend(struct)
            skip = [loc[1:] for d in struct if d.kind in (IMAGE, ANNOTATION) for loc in d.rects_b if loc[0] == pb]
            skip_a = [loc[1:] for d in struct if d.kind in (IMAGE, ANNOTATION) for loc in d.rects_a if loc[0] == pa]
            skip = (skip_a, skip)
        if use_visual:
            result.differences.extend(visual_pair_differences(doc_a, pa, doc_b, pb, options, ta_all, tb_all,
                                                              result, skip or None))
    if use_text:
        for pa in info_a.pages:
            if pa in empty_a:
                result.visual_only_pages.append(pa)
        if result.visual_only_pages and options.mode == MODE_AUTO:
            result.warnings.append(_("Alcune pagine non hanno testo estraibile e sono state confrontate solo "
                                     "visivamente: per le scansioni è consigliato il preset Scansione."))
    result.anchors = build_anchors(result.pairs, ta, tb, ops, heights_a, heights_b,
                                   info_a.page_count, info_b.page_count)
    finalize(result)
    progress.update(1.0)
    return result


def visual_pair_differences(doc_a, pa: int, doc_b, pb: int, options: CompareOptions, ta: list[Token],
                            tb: list[Token], result: CompareResult, skip=None) -> list[Difference]:
    """Differenze visive di una coppia di pagine (anche per il ricalcolo)."""
    pt_a = [t for t in ta if t.page == pa]
    pt_b = [t for t in tb if t.page == pb]
    masks = None
    if options.mode == MODE_AUTO and pt_a and pt_b:
        masks = (_rotated_boxes(doc_a, pa, pt_a), _rotated_boxes(doc_b, pb, pt_b))
    vr = compare_visual_pages(doc_a, pa, doc_b, pb, options, *(masks or (None, None)))
    result.shifts[(pa, pb)] = vr.shift
    out: list[Difference] = []
    if vr.size_differs:
        with ENGINE_LOCK:
            ra, rb = doc_a[pa].rect, doc_b[pb].rect
        out.append(Difference(
            VISUAL, subtype="page_size", rects_a=[(pa, 0, 0, ra.width, ra.height)],
            rects_b=[(pb, 0, 0, rb.width, rb.height)],
            detail=_("dimensione pagina {a} → {b} mm").format(
                a=f"{ra.width / MM:.0f}×{ra.height / MM:.0f}", b=f"{rb.width / MM:.0f}×{rb.height / MM:.0f}")))
    if vr.misaligned:
        msg = _("Pagine disallineate: pagina {a} di A e pagina {b} di B.").format(a=pa + 1, b=pb + 1)
        if msg not in result.warnings:
            result.warnings.append(msg)
    skip_a = options.excluded_areas(pa) + [tuple(x) for x in result.excluded_a.get(pa, [])]
    skip_b = options.excluded_areas(pb) + [tuple(x) for x in result.excluded_b.get(pb, [])]
    if skip:
        skip_a += [tuple(x) for x in skip[0]]
        skip_b += [tuple(x) for x in skip[1]]
    for r_a, r_b, area in zip(vr.regions_a, vr.regions_b, vr.areas):
        if (skip_a and _center_in(r_a, skip_a)) or (skip_b and _center_in(r_b, skip_b)):
            continue
        out.append(Difference(VISUAL, subtype="region", rects_a=[(pa, *r_a)], rects_b=[(pb, *r_b)], area=area,
                              detail=_("area variata {p}%").format(p=f"{area:.2f}".replace(".", ","))))
    return out


# =============================================================================
# Differenze di struttura (CMP-09)
# =============================================================================
def detect_moved_pages(ta: list[Token], tb: list[Token], ops=None, threshold: float = 0.8) -> list[tuple[int, int]]:
    """Pagine con lo stesso contenuto in una posizione diversa (riordinate).

    Si considerano solo le pagine il cui testo è rimasto quasi tutto senza
    corrispondenza nell'allineamento in ordine (`ops`): così il costo resta basso
    anche su documenti lunghi."""
    from collections import Counter
    if ops is None:
        ops = diff_opcodes(ta, tb)
    matched_a = [False] * len(ta)
    matched_b = [False] * len(tb)
    for tag, a0, a1, b0, b1 in ops:
        if tag == "equal":
            for k in range(a1 - a0):
                matched_a[a0 + k] = True
                matched_b[b0 + k] = True

    def candidates(tokens, matched):
        total: Counter = Counter()
        free: Counter = Counter()
        for t, m in zip(tokens, matched):
            total[t.page] += 1
            if not m:
                free[t.page] += 1
        return {p for p, n in total.items() if n >= 5 and free[p] >= threshold * n}

    cand_a = candidates(ta, matched_a)
    cand_b = candidates(tb, matched_b)
    if not cand_a or not cand_b:
        return []
    bags_a: dict[int, Counter] = {}
    bags_b: dict[int, Counter] = {}
    for t in ta:
        if t.page in cand_a:
            bags_a.setdefault(t.page, Counter())[t.key] += 1
    for t in tb:
        if t.page in cand_b:
            bags_b.setdefault(t.page, Counter())[t.key] += 1
    scored = []
    for pa, ca in bags_a.items():
        na = sum(ca.values())
        for pb, cb in bags_b.items():
            sim = sum((ca & cb).values()) / max(na, sum(cb.values()))
            if sim >= threshold:
                scored.append((sim, pa, pb))
    moved = []
    used_a: set[int] = set()
    used_b: set[int] = set()
    for sim, pa, pb in sorted(scored, reverse=True):
        if pa not in used_a and pb not in used_b:
            used_a.add(pa)
            used_b.add(pb)
            moved.append((pa, pb))
    return sorted(moved)


def _close(r1, r2, tol: float = MM) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(r1, r2))


def _iou(r1, r2) -> float:
    w = min(r1[2], r2[2]) - max(r1[0], r2[0])
    h = min(r1[3], r2[3]) - max(r1[1], r2[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    a1 = (r1[2] - r1[0]) * (r1[3] - r1[1])
    a2 = (r2[2] - r2[0]) * (r2[3] - r2[1])
    return inter / max(1e-6, a1 + a2 - inter)


def _images(doc, pno: int) -> list[dict]:
    with ENGINE_LOCK:
        try:
            infos = doc[pno].get_image_info(hashes=True)
        except Exception:
            return []
    out = []
    for info in infos:
        bbox = tuple(info.get("bbox", (0, 0, 0, 0)))
        if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
            continue
        out.append({"bbox": bbox, "digest": info.get("digest") or b""})
    return out


def _clip_similar(doc_a, pa, ra, doc_b, pb, rb) -> bool:
    """Le due aree hanno lo stesso aspetto (es. immagine solo ricompressa)?"""
    try:
        with ENGINE_LOCK:
            pa_ = doc_a[pa].get_pixmap(dpi=60, clip=pymupdf.Rect(ra), alpha=False, colorspace=pymupdf.csRGB)
            pb_ = doc_b[pb].get_pixmap(dpi=60, clip=pymupdf.Rect(rb), alpha=False, colorspace=pymupdf.csRGB)
        ia = np.frombuffer(pa_.samples, np.uint8).reshape(pa_.height, pa_.stride)[:, : pa_.width * 3]
        ib = np.frombuffer(pb_.samples, np.uint8).reshape(pb_.height, pb_.stride)[:, : pb_.width * 3]
        ia = ia.reshape(pa_.height, pa_.width, 3)
        ib = ib.reshape(pb_.height, pb_.width, 3)
        s = SENSITIVITY["medium"]
        return not regions_from_mask(diff_mask(ia, ib, s["tolerance"]), s["cell_pixels"], s["min_cells"])
    except Exception:
        return False


def image_differences(doc_a, pa: int, doc_b, pb: int, options: CompareOptions | None = None) -> list[Difference]:
    ia, ib = _images(doc_a, pa), _images(doc_b, pb)
    with ENGINE_LOCK:
        page_area = max(1.0, abs(doc_a[pa].rect))
    align = options.auto_align if options is not None else True
    out: list[Difference] = []
    used_b: set[int] = set()
    rest_a = []
    for x in ia:
        same = [j for j, y in enumerate(ib) if j not in used_b and y["digest"] == x["digest"] and x["digest"]]
        if same:
            j = min(same, key=lambda j: sum(abs(u - v) for u, v in zip(ib[j]["bbox"], x["bbox"])))
            used_b.add(j)
            if not _close(x["bbox"], ib[j]["bbox"]):
                ra, rb = x["bbox"], ib[j]["bbox"]
                resized = abs((ra[2] - ra[0]) - (rb[2] - rb[0])) > MM or abs((ra[3] - ra[1]) - (rb[3] - rb[1])) > MM
                scan = (ra[2] - ra[0]) * (ra[3] - ra[1]) >= 0.8 * page_area
                if scan and align and not resized and _close(ra, rb, MAX_SHIFT_MM * MM):
                    continue   # scansione traslata: compensata dall'allineamento automatico
                out.append(Difference(IMAGE, subtype="resized" if resized else "moved", rects_a=[(pa, *ra)],
                                      rects_b=[(pb, *rb)],
                                      detail=_("immagine ridimensionata") if resized else _("immagine spostata")))
            continue
        rest_a.append(x)
    rest_b = [y for j, y in enumerate(ib) if j not in used_b]
    for x in rest_a:
        match = max(rest_b, key=lambda y: _iou(x["bbox"], y["bbox"]), default=None)
        if match is not None and _iou(x["bbox"], match["bbox"]) >= 0.6:
            rest_b.remove(match)
            if _clip_similar(doc_a, pa, x["bbox"], doc_b, pb, match["bbox"]):
                continue
            out.append(Difference(IMAGE, subtype="replaced", rects_a=[(pa, *x["bbox"])],
                                  rects_b=[(pb, *match["bbox"])], detail=_("immagine sostituita")))
        else:
            out.append(Difference(IMAGE, subtype="deleted", rects_a=[(pa, *x["bbox"])],
                                  point_b=(pb, *x["bbox"]), detail=_("immagine eliminata")))
    for y in rest_b:
        out.append(Difference(IMAGE, subtype="added", rects_b=[(pb, *y["bbox"])], point_a=(pa, *y["bbox"]),
                              detail=_("immagine aggiunta")))
    return out


_SKIP_ANNOTS = {"Widget", "Link", "Popup"}


def _annotations(doc, pno: int) -> list[dict]:
    out = []
    with ENGINE_LOCK:
        page = doc[pno]
        for an in page.annots() or []:
            kind = an.type[1]
            if kind in _SKIP_ANNOTS:
                continue
            info = an.info or {}
            r = an.rect
            out.append({"type": kind, "rect": (r.x0, r.y0, r.x1, r.y1), "content": info.get("content", ""),
                        "author": info.get("title", ""), "id": info.get("id", "")})
    return out


def annotation_differences(doc_a, pa: int, doc_b, pb: int) -> list[Difference]:
    la, lb = _annotations(doc_a, pa), _annotations(doc_b, pb)
    out: list[Difference] = []
    used: set[int] = set()

    def center(r):
        return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)

    for x in la:
        cands = [j for j, y in enumerate(lb) if j not in used and y["type"] == x["type"]]
        match = None
        if x["id"]:
            match = next((j for j in cands if lb[j]["id"] == x["id"]), None)
        if match is None:
            near = [j for j in cands if not (x["id"] and lb[j]["id"])
                    and abs(center(lb[j]["rect"])[0] - center(x["rect"])[0]) <= 3 * MM
                    and abs(center(lb[j]["rect"])[1] - center(x["rect"])[1]) <= 3 * MM]
            if near:
                match = min(near, key=lambda j: sum(abs(u - v) for u, v in zip(lb[j]["rect"], x["rect"])))
        if match is None:
            out.append(Difference(ANNOTATION, subtype="deleted", text_a=x["content"], rects_a=[(pa, *x["rect"])],
                                  point_b=(pb, *x["rect"]),
                                  detail=_("annotazione eliminata ({t})").format(t=x["type"])))
            continue
        used.add(match)
        y = lb[match]
        details = []
        if x["content"] != y["content"]:
            details.append(_("contenuto modificato"))
        if x["author"] != y["author"]:
            details.append(_("autore {a} → {b}").format(a=x["author"] or "–", b=y["author"] or "–"))
        if not _close(x["rect"], y["rect"]):
            details.append(_("spostata"))
        if details:
            out.append(Difference(ANNOTATION, subtype="modified", text_a=x["content"], text_b=y["content"],
                                  rects_a=[(pa, *x["rect"])], rects_b=[(pb, *y["rect"])],
                                  detail=_("annotazione modificata ({t}): {d}").format(t=x["type"],
                                                                                      d=", ".join(details))))
    for j, y in enumerate(lb):
        if j not in used:
            out.append(Difference(ANNOTATION, subtype="added", text_b=y["content"], rects_b=[(pb, *y["rect"])],
                                  point_a=(pa, *y["rect"]),
                                  detail=_("annotazione aggiunta ({t})").format(t=y["type"])))
    return out


def structure_pair_differences(doc_a, pa: int, doc_b, pb: int, options: CompareOptions) -> list[Difference]:
    out: list[Difference] = []
    with ENGINE_LOCK:
        rot_a, rot_b = doc_a[pa].rotation, doc_b[pb].rotation
        ra, rb = doc_a[pa].rect, doc_b[pb].rect
    if rot_a != rot_b:
        out.append(Difference(PAGE, subtype="rotation", rects_a=[(pa, 0, 0, ra.width, ra.height)],
                              rects_b=[(pb, 0, 0, rb.width, rb.height)],
                              detail=_("rotazione pagina {a}° → {b}°").format(a=rot_a, b=rot_b)))
    out.extend(image_differences(doc_a, pa, doc_b, pb, options))
    if options.include_annotations:
        out.extend(annotation_differences(doc_a, pa, doc_b, pb))
    return out


def _fields(doc, pages: list[int]) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    with ENGINE_LOCK:
        if not doc.is_form_pdf:
            return out
        for pno in pages:
            for w in doc[pno].widgets() or []:
                name = w.field_name or ""
                if name and name not in out:
                    r = w.rect
                    out[name] = (pno, (r.x0, r.y0, r.x1, r.y1), "" if w.field_value is None else str(w.field_value))
    return out


def field_differences(doc_a, pages_a: list[int], doc_b, pages_b: list[int]) -> list[Difference]:
    fa, fb = _fields(doc_a, pages_a), _fields(doc_b, pages_b)
    out: list[Difference] = []
    for name, (pa, ra, va) in fa.items():
        if name not in fb:
            out.append(Difference(FIELD, subtype="deleted", text_a=va, rects_a=[(pa, *ra)],
                                  detail=_("campo «{n}» eliminato").format(n=name)))
            continue
        pb, rb, vb = fb[name]
        if va != vb:
            out.append(Difference(FIELD, subtype="value", text_a=va, text_b=vb, rects_a=[(pa, *ra)],
                                  rects_b=[(pb, *rb)],
                                  detail=_("campo «{n}»: {a} → {b}").format(n=name, a=va or "–", b=vb or "–")))
    for name, (pb, rb, vb) in fb.items():
        if name not in fa:
            out.append(Difference(FIELD, subtype="added", text_b=vb, rects_b=[(pb, *rb)],
                                  detail=_("campo «{n}» aggiunto").format(n=name)))
    return out


def finalize(result: CompareResult) -> None:
    """Ordina le differenze in ordine di documento e assegna gli identificativi."""
    def key(d: Difference):
        loc = d.rects_a[0] if d.rects_a else d.point_a
        if loc is None:
            pb = d.rects_b[0][0] if d.rects_b else 0
            pa = result.partner_a(pb)
            loc = (pa if pa is not None else pb, 0, 0, 0, 0)
        locb = d.rects_b[0] if d.rects_b else (d.point_b or (0, 0, 0, 0, 0))
        return (loc[0], round(loc[2], 1), loc[1], locb[0], locb[2], KINDS.index(d.kind))

    result.differences.sort(key=key)
    for i, d in enumerate(result.differences, 1):
        d.id = i


def recompute_pair(result: CompareResult, data_a: bytes, data_b: bytes, pa: int, pb: int,
                   password_a: str | None = None, password_b: str | None = None) -> CompareResult:
    """Abbinamento manuale «pagina pa di A ↔ pagina pb di B»: ricalcola solo le
    differenze visive di quelle pagine."""
    doc_a, _x, _y = open_side(data_a, password_a)
    doc_b, _x, _y = open_side(data_b, password_b)
    try:
        old_pairs = [(x, y) for x, y in result.pairs if x != pa and y != pb]
        result.pairs = sorted(old_pairs + [(pa, pb)])
        result.differences = [d for d in result.differences if not (
            d.kind == VISUAL and ((d.rects_a and d.rects_a[0][0] == pa) or (d.rects_b and d.rects_b[0][0] == pb)))]
        result.differences = [d for d in result.differences if not (
            d.subtype == "page" and ((d.rects_a and d.rects_a[0][0] == pa) or (d.rects_b and d.rects_b[0][0] == pb)))]
        opts = CompareOptions.from_dict(result.options.to_dict())
        opts.mode = MODE_VISUAL if result.options.mode == MODE_VISUAL else opts.mode
        result.differences.extend(visual_pair_differences(doc_a, pa, doc_b, pb, opts, [], [], result))
        result.anchors = sorted(set(result.anchors) | {(float(pa), float(pb)), (pa + 0.999, pb + 0.999)})
        result.anchors = _lis_nondecreasing(result.anchors)
        finalize(result)
        return result
    finally:
        with ENGINE_LOCK:
            doc_a.close()
            doc_b.close()


# =============================================================================
# Vista sovrapposta
# =============================================================================
def transform_image(img: np.ndarray, angle: float = 0.0, scale: float = 1.0) -> np.ndarray:
    """Rotazione fine (gradi, antiorario) e scala attorno all'angolo in alto a sinistra;
    le aree scoperte restano bianche."""
    if abs(angle) < 1e-6 and abs(scale - 1.0) < 1e-6:
        return img
    import math

    from PIL import Image
    h, w = img.shape[:2]
    a = math.radians(angle)
    cos, sin = math.cos(a) / scale, math.sin(a) / scale
    # trasformazione inversa (uscita -> ingresso) per PIL
    data = (cos, sin, 0.0, -sin, cos, 0.0)
    out = Image.fromarray(img).transform((w, h), Image.Transform.AFFINE, data, resample=Image.Resampling.BILINEAR,
                                         fillcolor=(255, 255, 255))
    return np.asarray(out).copy()


def overlay_composite(img_a: np.ndarray | None, img_b: np.ndarray | None, opacity_a: float = 1.0,
                      opacity_b: float = 1.0, color_a=(220, 38, 38), color_b=(37, 99, 235),
                      common=(40, 40, 40), shift_b: tuple[int, int] = (0, 0), angle_b: float = 0.0,
                      scale_b: float = 1.0) -> np.ndarray:
    """Sovrapposizione: parti comuni in grigio scuro, solo A nel colore di A,
    solo B nel colore di B. Le immagini sono RGB uint8 con la stessa scala."""
    if img_a is None and img_b is None:
        raise ValueError("nessuna immagine")
    ref = img_a if img_a is not None else img_b
    h = max(x.shape[0] for x in (img_a, img_b) if x is not None)
    w = max(x.shape[1] for x in (img_a, img_b) if x is not None)

    def darkness(img, opacity, shift=(0, 0)):
        d = np.zeros((h, w), dtype=np.float32)
        if img is None or opacity <= 0:
            return d
        img = shift_image(img, *shift)
        g = img.astype(np.float32).mean(axis=2)
        d[: g.shape[0], : g.shape[1]] = (255.0 - g) / 255.0
        return d * float(opacity)

    da = darkness(img_a, opacity_a)
    if img_b is not None:
        img_b = transform_image(img_b, angle_b, scale_b)
    db = darkness(img_b, opacity_b, shift_b)
    both = np.minimum(da, db)
    only_a = da - both
    only_b = db - both
    out = np.full((h, w, 3), 255.0, dtype=np.float32)
    for c in range(3):
        out[:, :, c] -= both * (255.0 - common[c]) + only_a * (255.0 - color_a[c]) + only_b * (255.0 - color_b[c])
    del ref
    return np.clip(out, 0, 255).astype(np.uint8)


# =============================================================================
# Sessione salvabile
# =============================================================================
def session_to_dict(result: CompareResult, offsets: dict | None = None, view_state: dict | None = None) -> dict:
    return {
        "format": "foliant-compare",
        "version": SESSION_VERSION,
        "a": {"path": result.side_a.path, "label": result.side_a.label, "sha256": result.side_a.sha256,
              "pages": [result.side_a.pages[0], result.side_a.pages[-1]] if result.side_a.pages else None},
        "b": {"path": result.side_b.path, "label": result.side_b.label, "sha256": result.side_b.sha256,
              "pages": [result.side_b.pages[0], result.side_b.pages[-1]] if result.side_b.pages else None},
        "options": result.options.to_dict(),
        "offsets": {f"{k[0]}:{k[1]}" if isinstance(k, tuple) else str(k): list(v) for k, v in (offsets or {}).items()},
        "pairs": [list(p) for p in result.pairs],
        "status": {str(d.id): {"status": d.status, "note": d.note, "kind": d.kind, "text_a": d.text_a,
                               "text_b": d.text_b, "pa": d.pages("a"), "pb": d.pages("b")}
                   for d in result.differences if d.status or d.note},
        "view": view_state or {},
    }


def restore_statuses(result: CompareResult, data: dict) -> list[dict]:
    """Riapplica stati e note salvati. Le differenze si riconoscono per
    identificativo (se tipo e testo coincidono) oppure per tipo, testo e pagine.
    Restituisce le voci salvate che non corrispondono più (scartate)."""
    saved = data.get("status") or {}
    by_id = {str(d.id): d for d in result.differences}
    lost = []
    used = set()
    for key, st in saved.items():
        d = by_id.get(key)
        ok = d is not None and d.kind == st.get("kind", d.kind) and d.text_a == st.get("text_a", d.text_a) \
            and d.text_b == st.get("text_b", d.text_b)
        if not ok:
            d = next((x for x in result.differences if id(x) not in used and x.kind == st.get("kind")
                      and x.text_a == st.get("text_a", "") and x.text_b == st.get("text_b", "")
                      and (not st.get("pa") or x.pages("a") == st.get("pa"))), None)
        if d is None:
            lost.append(st)
            continue
        used.add(id(d))
        d.status, d.note = st.get("status", ""), st.get("note", "")
    return lost


def check_session_version(data: dict) -> None:
    version = int(data.get("version", 1) or 1)
    if version > SESSION_VERSION:
        raise DocumentError(_("La sessione è stata salvata da una versione più recente (schema {v}): serve una "
                              "versione di Foliant che supporti lo schema {v}.").format(v=version))


def parse_offsets(data: dict) -> dict:
    """Allineamenti manuali salvati: (pa, pb) -> (dx, dy[, rotazione, scala])."""
    out = {}
    for k, v in (data.get("offsets") or {}).items():
        values = tuple(float(x) for x in v)
        if ":" in k:
            a, b = k.split(":", 1)
            out[(int(a), int(b))] = values
        else:
            out[k] = values
    return out


ProgressFn = Callable[[float, str], None]
__all__ = [n for n in dir() if not n.startswith("_") and n not in ("annotations",)] + ["OperationCancelled"]
