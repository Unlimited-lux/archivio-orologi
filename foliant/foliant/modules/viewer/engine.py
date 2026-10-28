"""Logica pura del modulo viewer (senza interfaccia): ricerca, segnalibri,
cronologia di navigazione, etichette di pagina.

Le funzioni che ricevono un documento del motore vanno chiamate tenendo
ENGINE_LOCK (o dentro `Document.change()`).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple

import pymupdf

# =========================================================================== ricerca

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
              "ﬅ": "st", "ﬆ": "st"}
_HYPHENS = "-­‐‑"


@functools.lru_cache(maxsize=8192)
def normalize_char(c: str, case_sensitive: bool, ignore_accents: bool) -> str:
    """Forma confrontabile di un carattere (può restituire più caratteri o nessuno); in cache:
    i caratteri di un documento si ripetono e la ricerca li normalizza a ogni pagina."""
    s = _LIGATURES.get(c)
    if s is None:
        s = unicodedata.normalize("NFKC", c)
    if ignore_accents:
        s = "".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch))
    if not case_sensitive:
        s = s.casefold()
    if s.isspace():
        s = " "
    return s


def normalize_query(text: str, case_sensitive: bool = False, ignore_accents: bool = False) -> str:
    out = "".join(normalize_char(c, case_sensitive, ignore_accents) for c in text)
    return re.sub(r"\s+", " ", out).strip()


@dataclass
class PageText:
    """Testo di una pagina in ordine di lettura con il riquadro di ogni carattere.

    `boxes[i]` è il riquadro del carattere `text[i]` (None per gli spazi e le
    interruzioni aggiunti tra parole e righe)."""

    text: str = ""
    boxes: list = field(default_factory=list)
    lines: list = field(default_factory=list)   # indice di riga per carattere (-1 = separatore)

    @property
    def has_text(self) -> bool:
        return any(b is not None for b in self.boxes)


WIDE_BLOCK = 0.6      # blocco più largo di questa frazione del testo: attraversa le colonne


def reading_order(blocks: list[dict]) -> list[dict]:
    """Blocchi di testo in ordine di lettura, anche su più colonne.

    I blocchi stretti che si sovrappongono in orizzontale formano una colonna; le colonne
    si leggono da sinistra a destra e i blocchi di ciascuna dall'alto in basso. I blocchi
    larghi (titoli a tutta pagina) separano fasce lette una dopo l'altra. Una pagina a una
    colonna resta ordinata dall'alto in basso. (L'ordinamento di PyMuPDF con sort=True usa
    il bordo inferiore e poi x: su due colonne di altezza diversa mescola le colonne.)"""
    items = [b for b in blocks if b.get("bbox")]
    if len(items) < 2:
        return list(blocks)
    left = min(b["bbox"][0] for b in items)
    right = max(b["bbox"][2] for b in items)
    span = max(1e-6, right - left)
    wide = sorted((b for b in items if b["bbox"][2] - b["bbox"][0] > WIDE_BLOCK * span),
                  key=lambda b: b["bbox"][1])
    narrow = [b for b in items if b["bbox"][2] - b["bbox"][0] <= WIDE_BLOCK * span]
    out: list[dict] = []
    edges = [b["bbox"][1] for b in wide] + [float("inf")]
    start = float("-inf")
    for k, edge in enumerate(edges):
        band = [b for b in narrow if start <= (b["bbox"][1] + b["bbox"][3]) / 2 < edge]
        columns: list[list[dict]] = []
        end = float("-inf")
        for b in sorted(band, key=lambda b: b["bbox"][0]):
            if not columns or b["bbox"][0] >= end - 1:
                columns.append([])
                end = b["bbox"][2]
            else:
                end = max(end, b["bbox"][2])
            columns[-1].append(b)
        for col in columns:
            out.extend(sorted(col, key=lambda b: (b["bbox"][1], b["bbox"][0])))
        if k < len(wide):
            out.append(wide[k])
            start = edge
    return out


def page_blocks(page: pymupdf.Page, kind: str = "dict", flags: int | None = None) -> list[dict]:
    """Blocchi di `page.get_text(kind)` in ordine di lettura (vedi `reading_order`)."""
    data = page.get_text(kind, flags=flags) if flags is not None else page.get_text(kind)
    return reading_order(data.get("blocks", []))


class Box(NamedTuple):
    """Riquadro di un carattere: leggero (centinaia di migliaia per un documento grande),
    con gli stessi campi di `pymupdf.Rect`, che lo accetta come argomento."""
    x0: float
    y0: float
    x1: float
    y1: float


def extract_page_text(page: pymupdf.Page) -> PageText:
    """Testo della pagina carattere per carattere; le parole sillabate a fine
    riga ("infor-" + "mazione") sono ricomposte togliendo il trattino."""
    flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    blocks = page_blocks(page, "rawdict", flags)
    pt = PageText()
    chars: list[str] = []
    line_no = 0
    for block in blocks:
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            line_chars = [(ch["c"], ch["bbox"]) for span in line.get("spans", []) for ch in span.get("chars", [])]
            # spazi finali della riga non utili
            while line_chars and line_chars[-1][0].isspace():
                line_chars.pop()
            if not line_chars:
                continue
            hyphen_join = False
            if chars:
                prev = chars[-1]
                if prev in _HYPHENS and len(chars) >= 2 and chars[-2].isalpha() and line_chars[0][0].isalpha():
                    hyphen_join = True
                if hyphen_join:
                    # il trattino resta come carattere con riquadro, ma è marcato per l'eliminazione
                    pt.lines[-1] = -2
                else:
                    chars.append(" ")
                    pt.boxes.append(None)
                    pt.lines.append(-1)
            for c, bbox in line_chars:
                chars.append(c)
                pt.boxes.append(Box._make(bbox))
                pt.lines.append(line_no)
            line_no += 1
    pt.text = "".join(chars)
    return pt


@dataclass
class SearchOptions:
    case_sensitive: bool = False
    whole_word: bool = False
    ignore_accents: bool = False
    regex: bool = False


def compile_query(query: str, opts: SearchOptions) -> "re.Pattern | None":
    """Espressione regolare della ricerca (ValueError con messaggio se non valida)."""
    if not opts.regex:
        return None
    try:
        return re.compile(query, 0 if opts.case_sensitive else re.IGNORECASE)
    except re.error as e:
        raise ValueError(str(e)) from e


def _normalized_with_map(pt: PageText, opts: SearchOptions) -> tuple[str, list[int]]:
    key = (opts.case_sensitive, opts.ignore_accents)
    cache = pt.__dict__.setdefault("_norm_cache", {})
    if key not in cache:                  # una sola volta per pagina e opzioni
        cache[key] = _normalize_page(pt, opts)
    return cache[key]


def _normalize_page(pt: PageText, opts: SearchOptions) -> tuple[str, list[int]]:
    out: list[str] = []
    index: list[int] = []
    for i, c in enumerate(pt.text):
        if pt.lines[i] == -2:   # trattino di sillabazione: saltato
            continue
        n = normalize_char(c, opts.case_sensitive, opts.ignore_accents)
        if n == " " and out and out[-1] == " ":
            continue
        for ch in n:
            out.append(ch)
            index.append(i)
    return "".join(out), index


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def find_spans(pt: PageText, query: str, opts: SearchOptions | None = None) -> list[list[int]]:
    """Occorrenze di `query` come elenchi degli indici dei caratteri di `pt.text`."""
    opts = opts or SearchOptions()
    if not query.strip() or not pt.text:
        return []
    pattern = compile_query(query, opts)
    if pattern is not None:
        norm = SearchOptions(case_sensitive=True, ignore_accents=opts.ignore_accents)
        hay, index = _normalized_with_map(pt, norm)
        spans = [(m.start(), m.end()) for m in pattern.finditer(hay) if m.end() > m.start()]
    else:
        q = normalize_query(query, opts.case_sensitive, opts.ignore_accents)
        if not q:
            return []
        hay, index = _normalized_with_map(pt, opts)
        spans = []
        start = 0
        while True:
            pos = hay.find(q, start)
            if pos < 0:
                break
            spans.append((pos, pos + len(q)))
            start = pos + 1
    out: list[list[int]] = []
    last_end = -1
    for pos, end in spans:
        if pos < last_end:
            continue  # occorrenze sovrapposte
        if opts.whole_word:
            if (pos > 0 and _is_word_char(hay[pos - 1]) and _is_word_char(hay[pos])) or \
               (end < len(hay) and _is_word_char(hay[end]) and _is_word_char(hay[end - 1])):
                continue
        src = sorted(set(index[pos:end]))
        if any(pt.boxes[i] is not None and pt.lines[i] != -2 for i in src):
            out.append(src)
            last_end = end
    return out


def _rects_of(pt: PageText, src: list[int]) -> list[pymupdf.Rect]:
    # unione per riga calcolata sui numeri: un solo Rect per riga (le unioni di Rect costano)
    rects: dict[int, list[float]] = {}
    for i in src:
        box = pt.boxes[i]
        line = pt.lines[i]
        if box is None or line == -2:
            continue
        r = rects.get(line)
        if r is None:
            rects[line] = [box.x0, box.y0, box.x1, box.y1]
        else:
            r[0], r[1] = min(r[0], box.x0), min(r[1], box.y0)
            r[2], r[3] = max(r[2], box.x1), max(r[3], box.y1)
    return [pymupdf.Rect(*r) for r in rects.values()]


def rects_of(pt: PageText, src: list[int]) -> list[pymupdf.Rect]:
    """Rettangoli (uno per riga) dei caratteri `src` di un'occorrenza."""
    return _rects_of(pt, src)


def find_in_page_text(pt: PageText, query: str, opts: SearchOptions | None = None) -> list[list[pymupdf.Rect]]:
    """Occorrenze di `query`: per ciascuna, i rettangoli (uno per riga coinvolta)."""
    return [_rects_of(pt, src) for src in find_spans(pt, query, opts)]


def find_hits(pt: PageText, query: str,
              opts: SearchOptions | None = None) -> list[tuple[list[pymupdf.Rect], tuple[str, str, str]]]:
    """Occorrenze con rettangoli e contesto (prima, trovato, dopo) per l'elenco dei risultati:
    il contesto si ricava dagli indici dei caratteri, senza riscorrere la pagina."""
    return [(_rects_of(pt, src), span_snippet(pt, src, 28)) for src in find_spans(pt, query, opts)]


def find_across_pages(a: PageText, b: PageText, query: str,
                      opts: SearchOptions | None = None) -> list[tuple[list[pymupdf.Rect], list[pymupdf.Rect]]]:
    """Occorrenze che iniziano alla fine della pagina `a` e proseguono all'inizio di `b`:
    per ciascuna, (rettangoli su a, rettangoli su b)."""
    opts = opts or SearchOptions()
    if opts.regex or not a.has_text or not b.has_text:
        return []
    q = normalize_query(query, opts.case_sensitive, opts.ignore_accents)
    if len(q) < 2:
        return []
    keep = len(q) * 3 + 8
    ta = max(0, len(a.text) - keep)
    hb = min(len(b.text), keep)
    na = len(a.text) - ta
    joined = PageText()
    joined.text = a.text[ta:] + " " + b.text[:hb]
    joined.boxes = a.boxes[ta:] + [None] + b.boxes[:hb]
    joined.lines = a.lines[ta:] + [-1] + [(ln + 1_000_000 if ln >= 0 else ln) for ln in b.lines[:hb]]
    # parola sillabata a cavallo delle due pagine
    if na >= 2 and a.text[-1] in _HYPHENS and a.text[-2].isalpha() and b.text[:1].isalpha():
        joined.lines[na - 1] = -2
        joined.lines[na] = -2      # niente spazio tra le due parti
    out = []
    for src in find_spans(joined, query, opts):
        ia = [i + ta for i in src if i < na]
        ib = [i - na - 1 for i in src if i > na]
        if ia and ib:
            ra, rb = _rects_of(a, ia), _rects_of(b, ib)
            if ra and rb:
                out.append((ra, rb))
    return out


def context_snippet(pt: PageText, rects: list[pymupdf.Rect], width: int = 40) -> tuple[str, str, str]:
    """(prima, trovato, dopo) per l'elenco dei risultati: «trovato» comprende solo
    i caratteri il cui centro cade in uno dei rettangoli dell'occorrenza."""
    idx = []
    for i, box in enumerate(pt.boxes):
        if box is None or pt.lines[i] == -2:
            continue
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        if any(r.x0 - 0.5 <= cx <= r.x1 + 0.5 and r.y0 - 0.5 <= cy <= r.y1 + 0.5 for r in rects):
            idx.append(i)
    if not idx:
        return "", "", ""
    return _snippet(pt, idx[0], idx[-1] + 1, width)


def span_snippet(pt: PageText, src: list[int], width: int = 40) -> tuple[str, str, str]:
    """Come `context_snippet`, dagli indici dei caratteri dell'occorrenza (tempo costante)."""
    idx = [i for i in src if pt.boxes[i] is not None and pt.lines[i] != -2]
    if not idx:
        return "", "", ""
    return _snippet(pt, idx[0], idx[-1] + 1, width)


def _snippet(pt: PageText, a: int, b: int, width: int) -> tuple[str, str, str]:
    def clean(s: str) -> str:
        return re.sub(r"\s+", " ", s)

    found = "".join(c for i, c in enumerate(pt.text[a:b], start=a) if pt.lines[i] != -2)
    return clean(pt.text[max(0, a - width):a]).lstrip(), clean(found), clean(pt.text[b:b + width]).rstrip()


# ====================================================================== etichette

def page_labels(pdf: pymupdf.Document) -> list[str]:
    """Etichetta di ogni pagina ("" se il documento non ne definisce)."""
    try:
        if not pdf.get_page_labels():
            return [""] * pdf.page_count
    except Exception:
        return [""] * pdf.page_count
    labels = []
    for i in range(pdf.page_count):
        try:
            labels.append(pdf[i].get_label() or "")
        except Exception:
            labels.append("")
    return labels


def resolve_page_input(text: str, labels: list[str], count: int, start_after: int = -1) -> int | None:
    """Indice di pagina per il testo digitato: prima le etichette, poi il numero
    fisico; il prefisso "#" forza il numero fisico. Con etichette duplicate si
    restituisce la prima dopo `start_after` (Invio ripetuto passa alla successiva)."""
    t = text.strip()
    if not t:
        return None
    if t.startswith("#"):
        try:
            n = int(t[1:].strip())
        except ValueError:
            return None
        return n - 1 if 1 <= n <= count else None
    matches = [i for i, lab in enumerate(labels) if lab and lab == t]
    if not matches:
        matches = [i for i, lab in enumerate(labels) if lab and lab.casefold() == t.casefold()]
    if matches:
        later = [i for i in matches if i > start_after]
        return later[0] if later else matches[0]
    try:
        n = int(t)
    except ValueError:
        return None
    return n - 1 if 1 <= n <= count else None


# ===================================================================== cronologia

@dataclass(frozen=True)
class Position:
    page: int
    x: float = 0.0
    y: float = 0.0
    zoom: float = 1.0
    fit: str | None = None


class NavigationHistory:
    """Cronologia Indietro/Avanti di una vista (come in un browser)."""

    def __init__(self, limit: int = 100):
        self.limit = limit
        self._back: list[Position] = []
        self._forward: list[Position] = []

    @property
    def can_back(self) -> bool:
        return bool(self._back)

    @property
    def can_forward(self) -> bool:
        return bool(self._forward)

    def record(self, origin: Position) -> None:
        """Registra la posizione di partenza di un salto; cancella l'«avanti»."""
        if self._back and self._back[-1] == origin:
            return
        self._back.append(origin)
        if len(self._back) > self.limit:
            del self._back[0]
        self._forward.clear()

    def back(self, current: Position) -> Position | None:
        if not self._back:
            return None
        target = self._back.pop()
        self._forward.append(current)
        return target

    def forward(self, current: Position) -> Position | None:
        if not self._forward:
            return None
        target = self._forward.pop()
        self._back.append(current)
        return target

    def clamp(self, page_count: int) -> None:
        """Dopo l'eliminazione di pagine: le voci puntano alla pagina più vicina."""
        last = max(0, page_count - 1)

        def fix(p: Position) -> Position:
            return p if p.page <= last else Position(last, p.x, p.y, p.zoom, p.fit)

        self._back = [fix(p) for p in self._back]
        self._forward = [fix(p) for p in self._forward]


# ===================================================================== segnalibri

@dataclass
class Bookmark:
    """Voce dell'albero dei segnalibri (struttura piatta con livello, come il motore)."""

    level: int
    title: str
    page: int                      # indice 0-based; -1 = destinazione non valida
    dest: dict = field(default_factory=dict)

    @property
    def bold(self) -> bool:
        return bool(self.dest.get("bold"))

    @property
    def italic(self) -> bool:
        return bool(self.dest.get("italic"))

    @property
    def color(self) -> tuple | None:
        c = self.dest.get("color")
        return tuple(c) if c else None

    @property
    def collapsed(self) -> bool:
        return bool(self.dest.get("collapse"))


def read_outline(pdf: pymupdf.Document) -> list[Bookmark]:
    try:
        toc = pdf.get_toc(simple=False)
    except Exception:
        return []
    out = []
    for item in toc:
        level, title, page = item[0], item[1], item[2]
        dest = dict(item[3]) if len(item) > 3 and isinstance(item[3], dict) else {}
        out.append(Bookmark(level, title or "", page - 1 if page and page > 0 else -1, dest))
    return out


def children_counts(items: list[Bookmark]) -> list[int]:
    """Numero di discendenti diretti e indiretti di ogni voce."""
    counts = []
    for i, b in enumerate(items):
        n = 0
        for c in items[i + 1:]:
            if c.level <= b.level:
                break
            n += 1
        counts.append(n)
    return counts


def subtree_end(items: list[Bookmark], index: int) -> int:
    """Indice successivo all'ultimo discendente di `index`."""
    level = items[index].level
    j = index + 1
    while j < len(items) and items[j].level > level:
        j += 1
    return j


def is_valid_structure(items: list[Bookmark]) -> bool:
    prev = 0
    for b in items:
        if b.level < 1 or b.level > prev + 1:
            return False
        prev = b.level
    return True


def write_outline(pdf: pymupdf.Document, items: list[Bookmark]) -> None:
    """Scrive l'albero nel documento (dentro `Document.change()`)."""
    if not is_valid_structure(items):
        raise ValueError("struttura dei segnalibri non valida")
    toc = []
    for b in items:
        dest = {k: v for k, v in b.dest.items() if k not in ("xref",)}
        page = b.page if 0 <= b.page < pdf.page_count else 0
        dest.setdefault("kind", pymupdf.LINK_GOTO)
        if dest.get("kind") == pymupdf.LINK_GOTO:
            dest["page"] = page
            if not isinstance(dest.get("to"), pymupdf.Point):
                dest["to"] = pymupdf.Point(0, 0)
        toc.append([b.level, b.title, page + 1, dest])
    pdf.set_toc(toc, collapse=0)
    # Stato aperto/chiuso (`/Count` negativo = chiuso) voce per voce.
    xrefs = pdf.get_outline_xrefs()
    if len(xrefs) != len(items):
        return
    for xref, b, n in zip(xrefs, items, children_counts(items)):
        if not n:
            continue
        kind, value = pdf.xref_get_key(xref, "Count")
        if kind != "int":
            continue
        count = abs(int(value))
        pdf.xref_set_key(xref, "Count", str(-count if b.collapsed else count))


def bookmark_add(items: list[Bookmark], after: int | None, title: str, page: int,
                 dest: dict | None = None) -> tuple[list[Bookmark], int]:
    """Nuova voce dopo `after` (stesso livello, dopo i suoi figli), o in fondo."""
    items = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in items]
    if after is None or not (0 <= after < len(items)):
        level, pos = 1, len(items)
    else:
        level, pos = items[after].level, subtree_end(items, after)
    items.insert(pos, Bookmark(level, title, page, dict(dest or {})))
    return items, pos


def bookmark_delete(items: list[Bookmark], index: int) -> list[Bookmark]:
    end = subtree_end(items, index)
    return [Bookmark(b.level, b.title, b.page, dict(b.dest)) for i, b in enumerate(items)
            if not (index <= i < end)]


def bookmark_rename(items: list[Bookmark], index: int, title: str) -> list[Bookmark]:
    out = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in items]
    out[index].title = title
    return out


def _sibling_before(items: list[Bookmark], index: int) -> int | None:
    level = items[index].level
    j = index - 1
    while j >= 0:
        if items[j].level == level:
            return j
        if items[j].level < level:
            return None
        j -= 1
    return None


def _sibling_after(items: list[Bookmark], index: int) -> int | None:
    end = subtree_end(items, index)
    if end < len(items) and items[end].level == items[index].level:
        return end
    return None


def bookmark_move_up(items: list[Bookmark], index: int) -> tuple[list[Bookmark], int]:
    prev = _sibling_before(items, index)
    if prev is None:
        return items, index
    end = subtree_end(items, index)
    block = items[index:end]
    rest = items[:index] + items[end:]
    out = rest[:prev] + block + rest[prev:]
    return [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in out], prev


def bookmark_move_down(items: list[Bookmark], index: int) -> tuple[list[Bookmark], int]:
    nxt = _sibling_after(items, index)
    if nxt is None:
        return items, index
    out, _ = bookmark_move_up(items, nxt)
    new_index = index + (subtree_end(items, nxt) - nxt)
    return out, new_index


def bookmark_indent(items: list[Bookmark], index: int) -> list[Bookmark]:
    """Diventa figlio (ultimo) del fratello precedente."""
    if _sibling_before(items, index) is None:
        return items
    end = subtree_end(items, index)
    out = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in items]
    for b in out[index:end]:
        b.level += 1
    return out


def bookmark_outdent(items: list[Bookmark], index: int) -> tuple[list[Bookmark], int]:
    """Sale di un livello: diventa il fratello successivo del genitore."""
    if items[index].level <= 1:
        return items, index
    end = subtree_end(items, index)
    block = [Bookmark(b.level - 1, b.title, b.page, dict(b.dest)) for b in items[index:end]]
    rest = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in items[:index] + items[end:]]
    # genitore = ultima voce prima di index con livello minore
    parent = index - 1
    while parent >= 0 and items[parent].level >= items[index].level:
        parent -= 1
    parent_end = subtree_end(rest, parent)
    out = rest[:parent_end] + block + rest[parent_end:]
    return out, parent_end


def bookmark_move_into(items: list[Bookmark], index: int, target: int, as_child: bool) -> tuple[list[Bookmark], int]:
    """Sposta la voce `index` (con i figli) come figlia di `target` (in fondo)
    oppure subito prima di `target` allo stesso livello. Usato dal trascinamento."""
    end = subtree_end(items, index)
    if index <= target < end:
        return items, index  # non dentro se stessa
    block = items[index:end]
    rest = items[:index] + items[end:]
    t = target if target < index else target - (end - index)
    if as_child:
        base = rest[t].level + 1
        pos = subtree_end(rest, t)
    else:
        base = rest[t].level
        pos = t
    delta = base - block[0].level
    moved = [Bookmark(b.level + delta, b.title, b.page, dict(b.dest)) for b in block]
    out = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in rest[:pos]] + moved + \
          [Bookmark(b.level, b.title, b.page, dict(b.dest)) for b in rest[pos:]]
    return out, pos


def current_bookmark(items: list[Bookmark], page: int) -> int | None:
    """Voce più profonda la cui destinazione precede o coincide con `page`."""
    best = None
    for i, b in enumerate(items):
        if 0 <= b.page <= page:
            if best is None or b.page >= items[best].page:
                best = i
    return best


def section_pages(items: list[Bookmark], index: int, page_count: int) -> range:
    """Pagine della sezione: dalla voce fino alla successiva di pari livello o superiore."""
    start = items[index].page
    end = page_count
    for b in items[index + 1:]:
        if b.level <= items[index].level and b.page >= 0:
            end = max(start + 1, b.page)
            break
    return range(max(0, start), end)


# ============================================= segnalibri: strumenti avanzati (VIS-17)

def outline_to_text(items: list[Bookmark]) -> str:
    """Formato di scambio: rientro con tabulazioni per livello, "titolo<TAB>pagina"."""
    lines = []
    for b in items:
        title = " ".join(b.title.split())
        lines.append("\t" * (b.level - 1) + f"{title}\t{b.page + 1 if b.page >= 0 else ''}".rstrip())
    return "\n".join(lines) + ("\n" if lines else "")


def outline_from_text(text: str, page_count: int) -> tuple[list[Bookmark], list[str]]:
    """Legge il formato di scambio (rientro con tabulazioni o con 2/4 spazi).

    Restituisce (voci, errori): le righe con pagine fuori intervallo sono scartate e segnalate."""
    items: list[Bookmark] = []
    errors: list[str] = []
    indent_unit = None
    for n, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        stripped = raw.lstrip("\t ")
        lead = raw[:len(raw) - len(stripped)]
        if "\t" in lead:
            depth = lead.count("\t")
        else:
            spaces = len(lead)
            if spaces and indent_unit is None:
                indent_unit = spaces
            depth = spaces // indent_unit if indent_unit else 0
        parts = stripped.rsplit("\t", 1) if "\t" in stripped else stripped.rsplit(" ", 1)
        if len(parts) != 2 or not parts[1].strip().isdigit():
            errors.append(f"{n}: {stripped}")
            continue
        title, page = parts[0].strip(), int(parts[1])
        if not (1 <= page <= page_count):
            errors.append(f"{n}: {stripped}")
            continue
        prev = items[-1].level if items else 0
        level = max(1, min(depth + 1, prev + 1))
        items.append(Bookmark(level, title, page - 1, {"kind": pymupdf.LINK_GOTO, "page": page - 1,
                                                        "to": pymupdf.Point(0, 0)}))
    return items, errors


def outline_every_n(page_count: int, n: int, template: str = "Pagine {a}-{b}") -> list[Bookmark]:
    n = max(1, int(n))
    out = []
    for start in range(0, page_count, n):
        end = min(page_count, start + n)
        title = template.format(a=start + 1, b=end) if end - start > 1 else template.split("{a}")[0] + str(start + 1)
        out.append(Bookmark(1, title.strip(), start, {"kind": pymupdf.LINK_GOTO, "page": start,
                                                       "to": pymupdf.Point(0, 0)}))
    return out


# ------------------------------------------------------------ titoli taggati (VIS-17)
_HEADING_ROLES = {"H1": 1, "H2": 2, "H3": 3, "H4": 4, "H5": 5, "H6": 6, "H": 0, "Title": 1}
_MAX_STRUCT_NODES = 50000


def _pdf_obj(pdf: pymupdf.Document, value: str) -> str:
    """Testo dell'oggetto: risolve un riferimento «n 0 R»."""
    m = re.fullmatch(r"\s*(\d+)\s+0\s+R\s*", value or "")
    return pdf.xref_object(int(m.group(1)), compressed=True) if m else value


def _dict_get(pdf, obj: str, key: str) -> str | None:
    """Valore grezzo di /key in un dizionario PDF (testo), riferimenti non risolti."""
    m = re.search(r"/" + key + r"(?![A-Za-z0-9])\s*", obj)
    if not m:
        return None
    rest = obj[m.end():]
    if rest.startswith("<<"):
        depth, i = 0, 0
        while i < len(rest):
            if rest.startswith("<<", i):
                depth += 1
                i += 2
            elif rest.startswith(">>", i):
                depth -= 1
                i += 2
                if depth == 0:
                    return rest[:i]
            else:
                i += 1
        return rest
    if rest.startswith("["):
        depth = 0
        for i, ch in enumerate(rest):
            depth += ch == "["
            depth -= ch == "]"
            if depth == 0:
                return rest[:i + 1]
        return rest
    if rest.startswith("("):
        depth, i, esc = 0, 0, False
        while i < len(rest):
            ch = rest[i]
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return rest[:i + 1]
            i += 1
        return rest
    m2 = re.match(r"(\d+\s+0\s+R|<[0-9A-Fa-f\s]*>|/[^\s/<>\[\]()]+|[^\s/<>\[\]()]+)", rest)
    return m2.group(1) if m2 else None


def _pdf_string(raw: str | None) -> str:
    """Testo di una stringa PDF «(…)» o «<…>» (PDFDocEncoding o UTF-16BE con BOM)."""
    if not raw:
        return ""
    raw = raw.strip()
    data = b""
    if raw.startswith("<") and raw.endswith(">"):
        try:
            data = bytes.fromhex(re.sub(r"\s", "", raw[1:-1]))
        except ValueError:
            return ""
    elif raw.startswith("(") and raw.endswith(")"):
        out = bytearray()
        body, i = raw[1:-1], 0
        esc = {"n": 10, "r": 13, "t": 9, "b": 8, "f": 12, "(": 40, ")": 41, "\\": 92}
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                if nxt in esc:
                    out.append(esc[nxt])
                    i += 2
                    continue
                m = re.match(r"[0-7]{1,3}", body[i + 1:])
                if m:
                    out.append(int(m.group(0), 8) & 0xFF)
                    i += 1 + len(m.group(0))
                    continue
                i += 2
                continue
            out += ch.encode("latin-1", "replace")
            i += 1
        data = bytes(out)
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    return data.decode("latin-1", "replace")


def _show_ops(content: bytes) -> list[tuple[int | None, bytes]]:
    """Operatori di testo del flusso in ordine, con l'MCID del contenuto marcato che li contiene
    e la stringa mostrata (byte grezzi)."""
    tokens = re.finditer(rb"\((?:\\.|[^\\)])*\)|<<|>>|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/<>\[\]()%]+|"
                         rb"%[^\r\n]*|[^\s/<>\[\]()%]+", content)
    stack: list[int | None] = []
    operands: list[bytes] = []
    pending_dict: list[bytes] = []
    depth = 0
    out = []
    for m in tokens:
        tok = m.group(0)
        if tok.startswith(b"%"):
            continue
        if tok == b"<<":
            depth += 1
            pending_dict.append(tok)
            continue
        if depth:
            pending_dict.append(tok)
            if tok == b">>":
                depth -= 1
                if not depth:
                    operands.append(b" ".join(pending_dict))
                    pending_dict = []
            continue
        if tok[:1] in (b"(", b"<", b"[", b"]", b"/") or re.fullmatch(rb"[-+.\d]+", tok):
            operands.append(tok)
            continue
        op = tok
        if op in (b"BDC", b"BMC"):
            mcid = None
            for o in operands:
                mm = re.search(rb"/MCID\s+(\d+)", o)
                if mm:
                    mcid = int(mm.group(1))
            stack.append(mcid)
        elif op == b"EMC":
            if stack:
                stack.pop()
        elif op in (b"Tj", b"TJ", b"'", b'"'):
            current = next((x for x in reversed(stack) if x is not None), None)
            text = b"".join(o for o in operands if o[:1] in (b"(", b"<"))
            out.append((current, text))
        operands = []
    return out


def mcid_texts(page: pymupdf.Page) -> dict[int, tuple[str, pymupdf.Rect | None]]:
    """Testo e riquadro di ogni contenuto marcato (MCID) della pagina.

    Gli operatori di testo del flusso si collegano, nell'ordine, ai tratti del testo di MuPDF
    (`get_texttrace`, numerati in ordine di esecuzione): così il testo è decodificato con i font
    del documento anche con codifiche CID. Se i conteggi non tornano (testo in oggetti esterni),
    si ripiega sulla decodifica diretta delle stringhe."""
    try:
        ops = _show_ops(page.read_contents())
        trace = page.get_texttrace()
    except Exception:
        return {}
    groups: dict[int, list] = {}
    for span in trace:
        groups.setdefault(span["seqno"], []).append(span)
    ordered = [groups[k] for k in sorted(groups)]
    out: dict[int, list] = {}
    mapped = len(ordered) == len(ops)
    if not mapped:
        visible = [op for op in ops if op[1].strip(b" ()<>[]")]
        if len(visible) == len(ordered):
            ops, mapped = visible, True
    for i, (mcid, raw) in enumerate(ops):
        if mcid is None:
            continue
        entry = out.setdefault(mcid, ["", None])
        if mapped:
            for span in ordered[i]:
                entry[0] += "".join(chr(c[0]) for c in span["chars"] if c[0] > 0)
                r = pymupdf.Rect(span["bbox"])
                entry[1] = r if entry[1] is None else entry[1] | r
        else:
            entry[0] += "".join(_pdf_string(s.decode("latin-1"))
                                for s in re.findall(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>", raw))
    return {k: (v[0], v[1]) for k, v in out.items()}


def outline_from_structure(pdf: pymupdf.Document, progress=None) -> list[Bookmark]:
    """Segnalibri dai titoli della struttura logica (`/StructTreeRoot`: H1–H6, H, Title, anche
    tramite `/RoleMap`). Titolo da `/ActualText`, `/Alt` o `/T`, altrimenti dal testo dei
    contenuti marcati; destinazione sulla pagina dell'elemento. [] se il documento non è taggato."""
    cat = pdf.pdf_catalog()
    kind, root = pdf.xref_get_key(cat, "StructTreeRoot")
    if kind != "xref":
        return []
    root_xref = int(root.split()[0])
    page_of = {pdf.page_xref(i): i for i in range(pdf.page_count)}
    root_obj = pdf.xref_object(root_xref, compressed=True)
    rolemap: dict[str, str] = {}
    rm = _dict_get(pdf, root_obj, "RoleMap")
    if rm:
        rm = _pdf_obj(pdf, rm)
        rolemap = {a: b for a, b in re.findall(r"/([^\s/<>\[\]()]+)\s*/([^\s/<>\[\]()]+)", rm)}
    texts_cache: dict[int, dict] = {}

    def role(name: str) -> str:
        seen = set()
        while name in rolemap and name not in seen and name not in _HEADING_ROLES:
            seen.add(name)
            name = rolemap[name]
        return name

    def page_index(value: str | None) -> int | None:
        if not value:
            return None
        m = re.match(r"(\d+)\s+0\s+R", value)
        return page_of.get(int(m.group(1))) if m else None

    def collect_mcids(obj: str, page: int | None, out: list, seen: set, budget: list) -> None:
        k = _dict_get(pdf, obj, "K")
        if k is None:
            return
        items = re.findall(r"<<.*?>>|\d+\s+0\s+R|\d+", k[1:-1] if k.startswith("[") else k, re.S)
        for it in items:
            if budget[0] <= 0:
                return
            budget[0] -= 1
            if re.fullmatch(r"\d+", it):
                out.append((page, int(it)))
            elif it.startswith("<<"):
                mc = _dict_get(pdf, it, "MCID")
                if mc is not None and mc.isdigit():
                    out.append((page_index(_dict_get(pdf, it, "Pg")) if _dict_get(pdf, it, "Pg") else page,
                                int(mc)))
            else:
                x = int(it.split()[0])
                if x in seen:
                    continue
                seen.add(x)
                child = pdf.xref_object(x, compressed=True)
                if "/MCR" in child:
                    mc = _dict_get(pdf, child, "MCID")
                    if mc and mc.isdigit():
                        p = page_index(_dict_get(pdf, child, "Pg"))
                        out.append((p if p is not None else page, int(mc)))
                elif "/StructElem" in child or _dict_get(pdf, child, "S"):
                    p = page_index(_dict_get(pdf, child, "Pg"))
                    collect_mcids(child, p if p is not None else page, out, seen, budget)

    def element_text(obj: str, page: int | None) -> tuple[str, int | None, pymupdf.Rect | None]:
        for key in ("ActualText", "Alt", "T"):
            raw = _dict_get(pdf, obj, key)
            if raw:
                text = _pdf_string(_pdf_obj(pdf, raw))
                if text.strip():
                    return text, page, None
        mcids: list = []
        collect_mcids(obj, page, mcids, set(), [2000])
        text, first_page, rect = "", None, None
        for p, mcid in mcids:
            if p is None:
                continue
            if p not in texts_cache:
                texts_cache[p] = mcid_texts(pdf[p])
            t, r = texts_cache[p].get(mcid, ("", None))
            if t and first_page is None:
                first_page, rect = p, r
            text += t
        return text, first_page if first_page is not None else page, rect

    out: list[Bookmark] = []
    seen: set[int] = set()
    budget = [_MAX_STRUCT_NODES]

    def walk(xref: int, page: int | None, sect_depth: int) -> None:
        if xref in seen or budget[0] <= 0:
            return
        seen.add(xref)
        budget[0] -= 1
        obj = pdf.xref_object(xref, compressed=True)
        p = page_index(_dict_get(pdf, obj, "Pg"))
        page = p if p is not None else page
        s = _dict_get(pdf, obj, "S")
        name = role(s[1:]) if s and s.startswith("/") else ""
        if name in _HEADING_ROLES:
            text, hpage, rect = element_text(obj, page)
            title = re.sub(r"\s+", " ", text).strip()[:200]
            if title and hpage is not None:
                level = _HEADING_ROLES[name] or max(1, sect_depth)
                to = pymupdf.Point(0, max(0.0, rect.y0 - 6)) if rect is not None else pymupdf.Point(0, 0)
                out.append(Bookmark(level, title, hpage, {"kind": pymupdf.LINK_GOTO, "page": hpage, "to": to}))
            return
        child_depth = sect_depth + (1 if name == "Sect" else 0)
        k = _dict_get(pdf, obj, "K")
        if not k:
            return
        for ref in re.findall(r"(\d+)\s+0\s+R", k):
            walk(int(ref), page, child_depth)

    k = _dict_get(pdf, root_obj, "K")
    refs = [int(r) for r in re.findall(r"(\d+)\s+0\s+R", k or "")]
    for i, ref in enumerate(refs):
        if progress is not None:
            progress(i / max(1, len(refs)))
        walk(ref, None, 0)
    return _normalize_levels(out)


def _normalize_levels(items: list[Bookmark]) -> list[Bookmark]:
    """Livelli consecutivi: il primo è 1 e nessun salto di più di un livello (H1 → H3)."""
    if not items:
        return items
    base = min(b.level for b in items)
    prev = 0
    for b in items:
        lvl = b.level - base + 1
        b.level = max(1, min(lvl, prev + 1))
        prev = b.level
    return items


def outline_from_headings(pdf: pymupdf.Document, max_levels: int = 3, progress=None) -> list[Bookmark]:
    """Titoli riconosciuti dallo stile del testo: righe brevi con corpo più grande
    del testo normale (o in grassetto e più grandi). I corpi distinti diventano livelli."""
    from collections import Counter
    lines: list[tuple[int, float, float, str, bool]] = []
    sizes: Counter = Counter()
    count = pdf.page_count
    for i in range(count):
        if progress is not None:
            progress(i / max(1, count))
        try:
            blocks = page_blocks(pdf[i])
        except Exception:
            continue
        for block in blocks:
            if block.get("type", 0) != 0:
                continue
            for line in block.get("lines", []):
                spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
                if not spans:
                    continue
                text = " ".join(s["text"].strip() for s in spans)
                size = max(round(s["size"], 1) for s in spans)
                bold = all(s.get("flags", 0) & 16 for s in spans)
                for s in spans:
                    sizes[round(s["size"], 1)] += len(s["text"])
                lines.append((i, line["bbox"][1], size, text, bold))
    if not sizes:
        return []
    body = sizes.most_common(1)[0][0]
    candidates = [ln for ln in lines if ln[2] >= body * 1.15 and len(ln[3]) <= 120]
    levels = sorted({ln[2] for ln in candidates}, reverse=True)[:max_levels]
    out: list[Bookmark] = []
    for page, y, size, text, _bold in candidates:
        if size not in levels:
            continue
        level = levels.index(size) + 1
        prev = out[-1].level if out else 0
        level = min(level, prev + 1)
        out.append(Bookmark(level, text, page, {"kind": pymupdf.LINK_GOTO, "page": page,
                                                "to": pymupdf.Point(0, max(0.0, y - 10))}))
    return out


def verify_outline(items: list[Bookmark], page_count: int) -> list[tuple[int, str]]:
    """Segnalibri con problemi: (indice, motivo)."""
    problems = []
    seen: dict[tuple[str, int], int] = {}
    for i, b in enumerate(items):
        kind = b.dest.get("kind", pymupdf.LINK_GOTO)
        if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED, pymupdf.LINK_NONE) and not (0 <= b.page < page_count):
            problems.append((i, "broken"))
        elif not b.title.strip():
            problems.append((i, "untitled"))
        key = (b.title.strip().casefold(), b.page)
        if b.title.strip() and key in seen and items[seen[key]].level == b.level:
            problems.append((i, "duplicate"))
        seen.setdefault(key, i)
    return problems



def _fix_levels(items: list[Bookmark]) -> list[Bookmark]:
    """Dopo aver tolto voci: nessun salto di più di un livello rispetto alla voce precedente."""
    prev = 0
    for b in items:
        b.level = max(1, min(b.level, prev + 1))
        prev = b.level
    return items


def merge_duplicates(items: list[Bookmark]) -> tuple[list[Bookmark], int]:
    """Toglie le voci uguali (stesso titolo, pagina e livello) a una precedente; i loro figli
    restano e passano alla voce che le precede. Restituisce (voci, quante tolte)."""
    drop = {i for i, why in verify_outline(items, 10 ** 9) if why == "duplicate"}
    kept = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for i, b in enumerate(items) if i not in drop]
    return _fix_levels(kept), len(drop)


CASE_MODES = ("upper", "lower", "title", "sentence")


def change_case(items: list[Bookmark], mode: str) -> list[Bookmark]:
    """Maiuscole («upper»), minuscole («lower»), iniziali maiuscole («title») o come in una
    frase («sentence»: solo la prima lettera maiuscola)."""
    def conv(t: str) -> str:
        if mode == "upper":
            return t.upper()
        if mode == "lower":
            return t.lower()
        if mode == "title":
            return " ".join(w[:1].upper() + w[1:].lower() for w in t.split(" "))
        low = t.lower()
        for i, ch in enumerate(low):
            if ch.isalpha():
                return low[:i] + ch.upper() + low[i + 1:]
        return low
    return [Bookmark(b.level, conv(b.title), b.page, dict(b.dest)) for b in items]


def replace_in_titles(items: list[Bookmark], find: str, repl: str, regex: bool = False,
                      case_sensitive: bool = False) -> tuple[list[Bookmark], int]:
    """Trova e sostituisci nei titoli. Restituisce (voci, numero di sostituzioni)."""
    try:
        rx = re.compile(find if regex else re.escape(find), 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        raise ValueError(str(e)) from e
    total = 0
    out = []
    for b in items:
        title, n = rx.subn(repl if regex else (lambda _m: repl), b.title)
        total += n
        out.append(Bookmark(b.level, title, b.page, dict(b.dest)))
    return out, total


def remove_broken(items: list[Bookmark], page_count: int) -> tuple[list[Bookmark], int]:
    """Toglie i segnalibri con destinazione non valida (i figli restano)."""
    drop = {i for i, why in verify_outline(items, page_count) if why == "broken"}
    kept = [Bookmark(b.level, b.title, b.page, dict(b.dest)) for i, b in enumerate(items) if i not in drop]
    return _fix_levels(kept), len(drop)


def redirect_broken(items: list[Bookmark], page_count: int, page: int) -> tuple[list[Bookmark], int]:
    """Reindirizza i segnalibri con destinazione non valida alla pagina indicata."""
    broken = {i for i, why in verify_outline(items, page_count) if why == "broken"}
    out = []
    for i, b in enumerate(items):
        if i in broken:
            out.append(Bookmark(b.level, b.title, page,
                                {"kind": pymupdf.LINK_GOTO, "page": page, "to": pymupdf.Point(0, 0)}))
        else:
            out.append(Bookmark(b.level, b.title, b.page, dict(b.dest)))
    return out, len(broken)


def outline_from_regex(pdf: pymupdf.Document, pattern: str, case_sensitive: bool = False,
                       progress=None) -> list[Bookmark]:
    """Segnalibri dalle righe di testo che corrispondono all'espressione regolare (un livello;
    se l'espressione ha un gruppo chiamato `level` con cifre, quello indica il livello)."""
    try:
        rx = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        raise ValueError(str(e)) from e
    out: list[Bookmark] = []
    for i in range(pdf.page_count):
        if progress is not None:
            progress(i / max(1, pdf.page_count))
        try:
            blocks = page_blocks(pdf[i])
        except Exception:
            continue
        for block in blocks:
            for line in block.get("lines", []):
                text = " ".join(s.get("text", "").strip() for s in line.get("spans", [])).strip()
                if not text or len(text) > 200:
                    continue
                m = rx.search(text)
                if not m:
                    continue
                level = 1
                if "level" in rx.groupindex and m.group("level"):
                    level = max(1, min(6, m.group("level").count(".") + 1))
                prev = out[-1].level if out else 0
                level = min(level, prev + 1)
                out.append(Bookmark(level, text, i, {"kind": pymupdf.LINK_GOTO, "page": i,
                                                     "to": pymupdf.Point(0, max(0.0, line["bbox"][1] - 10))}))
    return out


def insert_toc_pages(pdf: pymupdf.Document, items: list[Bookmark], title: str = "Sommario",
                     fontsize: float = 11.0) -> int:
    """Inserisce in testa pagine di sommario con una riga per segnalibro (rientrata per
    livello), il numero di pagina e un collegamento alla destinazione. I numeri tengono
    conto delle pagine inserite. Restituisce il numero di pagine inserite (dentro change())."""
    entries = [b for b in items if 0 <= b.page < pdf.page_count]
    if not entries:
        return 0
    ref = pdf[0].rect
    width, height = (ref.width, ref.height) if ref.width > 0 else (595.0, 842.0)
    margin, line_h = 56.0, fontsize * 1.8
    first_y = margin + 40
    per_first = int((height - margin - first_y) // line_h)
    per_other = int((height - 2 * margin) // line_h)
    n_pages = 1 if len(entries) <= per_first else 1 + -(-(len(entries) - per_first) // max(1, per_other))
    for i in range(n_pages):
        pdf.new_page(pno=i, width=width, height=height)
    page = pdf[0]
    page.insert_text((margin, margin + 16), title, fontsize=fontsize * 1.8, fontname="helv")
    p_index, y = 0, first_y
    for b in entries:
        if y + line_h > height - margin:
            p_index += 1
            page = pdf[p_index]
            y = margin
        target = b.page + n_pages
        label = str(target + 1)
        indent = margin + 18 * (b.level - 1)
        text = b.title if len(b.title) <= 90 else b.title[:87] + "…"
        page.insert_text((indent, y + fontsize), text, fontsize=fontsize, fontname="helv")
        tw = pymupdf.get_text_length(label, fontname="helv", fontsize=fontsize)
        page.insert_text((width - margin - tw, y + fontsize), label, fontsize=fontsize, fontname="helv")
        page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(indent, y, width - margin, y + line_h),
                          "page": target, "to": pymupdf.Point(0, 0)})
        y += line_h
    return n_pages
