"""Logica pura del modulo viewer (senza interfaccia): ricerca, segnalibri,
cronologia di navigazione, etichette di pagina.

Le funzioni che ricevono un documento del motore vanno chiamate tenendo
ENGINE_LOCK (o dentro `Document.change()`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pymupdf

# =========================================================================== ricerca

_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
              "ﬅ": "st", "ﬆ": "st"}
_HYPHENS = "-­‐‑"


def normalize_char(c: str, case_sensitive: bool, ignore_accents: bool) -> str:
    """Forma confrontabile di un carattere (può restituire più caratteri o nessuno)."""
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


def extract_page_text(page: pymupdf.Page) -> PageText:
    """Testo della pagina carattere per carattere; le parole sillabate a fine
    riga ("infor-" + "mazione") sono ricomposte togliendo il trattino."""
    flags = pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
    raw = page.get_text("rawdict", flags=flags, sort=True)
    pt = PageText()
    chars: list[str] = []
    line_no = 0
    for block in raw.get("blocks", []):
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
                pt.boxes.append(pymupdf.Rect(bbox))
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


def _span_rects(pt: PageText, index: list[int], pos: int, end: int) -> list[pymupdf.Rect]:
    src = sorted(set(index[pos:end]))
    rects: dict[int, pymupdf.Rect] = {}
    order: list[int] = []
    for i in src:
        box = pt.boxes[i]
        line = pt.lines[i]
        if box is None or line == -2:
            continue
        if line not in rects:
            rects[line] = pymupdf.Rect(box)
            order.append(line)
        else:
            rects[line] |= box
    return [rects[k] for k in order]


def find_in_page_text(pt: PageText, query: str, opts: SearchOptions | None = None) -> list[list[pymupdf.Rect]]:
    """Occorrenze di `query`: per ciascuna, i rettangoli (uno per riga coinvolta)."""
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
    hits: list[list[pymupdf.Rect]] = []
    last_end = -1
    for pos, end in spans:
        if pos < last_end:
            continue  # occorrenze sovrapposte
        if opts.whole_word:
            if (pos > 0 and _is_word_char(hay[pos - 1]) and _is_word_char(hay[pos])) or \
               (end < len(hay) and _is_word_char(hay[end]) and _is_word_char(hay[end - 1])):
                continue
        rects = _span_rects(pt, index, pos, end)
        if rects:
            hits.append(rects)
            last_end = end
    return hits


def context_snippet(pt: PageText, rects: list[pymupdf.Rect], width: int = 40) -> tuple[str, str, str]:
    """(prima, trovato, dopo) per l'elenco dei risultati."""
    first = rects[0]
    idx = [i for i, b in enumerate(pt.boxes) if b is not None and pt.lines[i] != -2 and first.intersects(b)
           and abs((b.x0 + b.x1) / 2 - (first.x0 + first.x1) / 2) <= first.width / 2 + 0.5]
    if not idx:
        return "", "", ""
    a = idx[0]
    last = rects[-1]
    idx_end = [i for i, b in enumerate(pt.boxes) if b is not None and last.intersects(b)
               and (b.x0 + b.x1) / 2 <= last.x1 + 0.5 and (b.x0 + b.x1) / 2 >= last.x0 - 0.5]
    b = (idx_end[-1] if idx_end else a) + 1

    def clean(s: str) -> str:
        return re.sub(r"\s+", " ", s)

    before = clean(pt.text[max(0, a - width):a])
    found = clean("".join(c for i, c in enumerate(pt.text[a:b], start=a) if pt.lines[i] != -2))
    after = clean(pt.text[b:b + width])
    return before.lstrip(), found, after.rstrip()


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
            data = pdf[i].get_text("dict", sort=True)
        except Exception:
            continue
        for block in data.get("blocks", []):
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
