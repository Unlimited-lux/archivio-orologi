"""Trova e sostituisci / Trova ed elimina nel testo di pagina (TXT-12, TXT-13).

Logica pura, senza interfaccia. La ricerca avviene sul testo logico dei blocchi
riconosciuti da `analysis.analyze_page`, normalizzato (NFC, legature scomposte,
spazi consecutivi equivalenti, trattini di sillabazione ignorati; facoltativamente
senza accenti) mantenendo una mappa verso le posizioni originali. Le occorrenze
non attraversano mai due blocchi; annotazioni, campi modulo e segnalibri sono esclusi.

Le sostituzioni passano da `engine.replace_block` (stile del primo carattere
dell'occorrenza, riflusso del solo blocco, copertura del font) e vanno eseguite
dentro `Document.change()`: `replace_all` usa un unico passo annullabile.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Iterable

import pymupdf

from ...core.errors import FoliantError
from ...i18n import _
from . import engine
from .analysis import EXTRACT_FLAGS, analyze_page, unframe_matrix
from .fonts import Substitution
from .model import SOFT_HYPHEN, Run, Style, TextBlock, merge_runs

CONTEXT_CHARS = 30

# legature tipografiche -> lettere (U+FB00–FB06)
LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st",
}
_SPACES = {" ", "\t", "\u00a0", "\u2000", "\u2001", "\u2002", "\u2003", "\u2004", "\u2005", "\u2006",
           "\u2007", "\u2008", "\u2009", "\u200a", "\u202f", "\u205f", "\u3000"}
_CLOSING_PUNCT = set(".,;:!?)]}»”’")


class SearchError(FoliantError):
    """Errore di ricerca/sostituzione con messaggio per l'utente."""


@dataclass
class SearchOptions:
    case_sensitive: bool = False
    whole_word: bool = False
    ignore_accents: bool = False
    regex: bool = False


@dataclass
class Match:
    """Occorrenza trovata in un blocco di testo di pagina."""

    page: int
    block: int                              # indice del blocco nella pagina
    start: int                              # posizione nel testo logico del blocco (`TextBlock.text`)
    end: int
    text: str                               # testo originale dell'occorrenza
    context: str                            # testo attorno all'occorrenza, su una riga
    rects: list[pymupdf.Rect] = field(default_factory=list)   # pagina non ruotata
    replaceable: bool = True
    reason: str = ""
    block_bbox: pymupdf.Rect = field(default_factory=pymupdf.Rect)  # riquadro di lavoro
    groups: tuple = ()                      # testo originale dei gruppi (0 = intera occorrenza)
    group_names: dict = field(default_factory=dict)  # nome gruppo -> numero


@dataclass
class ReplaceSummary:
    replaced: int = 0
    not_replaceable: list[tuple[Match, str]] = field(default_factory=list)
    substitutions: list[Substitution] = field(default_factory=list)
    blocks_changed: int = 0


# ====================================================================== normalizzazione
@dataclass
class _Normalized:
    text: str
    starts: list[int]       # indice normalizzato -> inizio nel testo logico
    ends: list[int]         # indice normalizzato -> fine (esclusa) nel testo logico

    def span(self, a: int, b: int) -> tuple[int, int]:
        """Intervallo normalizzato [a, b) -> intervallo logico."""
        if b <= a:
            pos = self.starts[a] if a < len(self.starts) else (self.ends[-1] if self.ends else 0)
            return pos, pos
        return self.starts[a], self.ends[b - 1]


def _fold(cluster: str, ignore_accents: bool) -> str:
    s = unicodedata.normalize("NFC", cluster)
    s = "".join(LIGATURES.get(c, c) for c in s)
    if ignore_accents:
        s = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
        s = unicodedata.normalize("NFC", s)
    return s


def normalize(text: str, ignore_accents: bool = False) -> _Normalized:
    """Testo normalizzato per la ricerca, con la mappa verso il testo originale."""
    n = len(text)
    if text.isascii() and "  " not in text and "\t" not in text:
        return _Normalized(text, list(range(n)), list(range(1, n + 1)))
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    while i < n:
        c = text[i]
        if c < "\x80" and c != " " and c != "\t" and (i + 1 >= n or not unicodedata.combining(text[i + 1])):
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
            i += 1
            continue
        j = i + 1
        while j < n and unicodedata.combining(text[j]):
            j += 1
        cluster = text[i:j]
        if cluster == SOFT_HYPHEN or cluster in ("\u200b", "\u200c", "\u200d", "\ufeff"):
            if ends:
                ends[-1] = j
            i = j
            continue
        if cluster[0] in _SPACES:
            if out and out[-1] in (" ", "\n"):
                ends[-1] = j        # spazi consecutivi equivalenti a uno
                i = j
                continue
            folded = " "
        else:
            folded = _fold(cluster, ignore_accents)
        for c in folded:
            out.append(c)
            starts.append(i)
            ends.append(j)
        i = j
    return _Normalized("".join(out), starts, ends)


def compile_pattern(pattern: str, options: SearchOptions) -> re.Pattern:
    """Espressione regolare equivalente alla ricerca; solleva SearchError se non valida."""
    if not pattern or (not options.regex and not pattern.strip()):
        raise SearchError(_("Inserisci il testo da cercare."))
    norm = normalize(pattern, options.ignore_accents).text if not options.regex else _fold_pattern(
        pattern, options.ignore_accents)
    if options.regex:
        body = norm
    else:
        parts = re.split(r"\s+", norm)
        body = r"\s+".join(re.escape(p) for p in parts)
    if options.whole_word:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    flags = re.UNICODE | (0 if options.case_sensitive else re.IGNORECASE)
    try:
        return re.compile(body, flags)
    except re.error as e:
        raise SearchError(_("Espressione regolare non valida: {err}.").format(err=_regex_error(e))) from e
    except (OverflowError, RecursionError) as e:
        raise SearchError(_("Espressione regolare troppo complessa.")) from e


def _fold_pattern(pattern: str, ignore_accents: bool) -> str:
    s = unicodedata.normalize("NFC", pattern)
    s = "".join(LIGATURES.get(c, c) for c in s)
    if ignore_accents:
        s = unicodedata.normalize("NFC", "".join(
            c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)))
    return s


_RE_MESSAGES = [
    ("missing ), unterminated subpattern", "parentesi «(» non chiusa"),
    ("unbalanced parenthesis", "parentesi «)» senza apertura"),
    ("unterminated character set", "classe di caratteri «[» non chiusa"),
    ("nothing to repeat", "quantificatore senza nulla da ripetere"),
    ("multiple repeat", "quantificatori ripetuti"),
    ("bad escape (end of pattern)", "«\\» alla fine dell'espressione"),
    ("bad escape", "sequenza di escape non valida"),
    ("bad character range", "intervallo di caratteri non valido"),
    ("unknown group name", "nome di gruppo sconosciuto"),
    ("invalid group reference", "riferimento a gruppo non valido"),
    ("redefinition of group name", "nome di gruppo ripetuto"),
    ("missing group name", "nome di gruppo mancante"),
    ("bad character in group name", "nome di gruppo non valido"),
    ("look-behind requires fixed-width pattern", "il «lookbehind» richiede una lunghezza fissa"),
    ("unknown extension", "estensione «(?» sconosciuta"),
    ("missing ), unterminated name", "nome di gruppo non chiuso"),
    ("missing :", "«:» mancante"),
    ("unterminated comment", "commento non chiuso"),
]


def _regex_error(e: re.error) -> str:
    raw = e.msg or str(e)
    msg = next((_(it) for en, it in _RE_MESSAGES if raw.startswith(en)), _("errore di sintassi"))
    if e.pos is not None:
        return _("{msg} (posizione {pos})").format(msg=msg, pos=e.pos + 1)
    return msg


# ====================================================================== sostitutivo
_TEMPLATE = re.compile(r"\$(?:\$|(\d+)|\{([^}]*)\})")


def parse_replacement(template: str) -> list:
    """Scompone il sostitutivo in testo (str) e riferimenti a gruppi (int o nome, come tupla)."""
    parts: list = []
    pos = 0
    for m in _TEMPLATE.finditer(template):
        if m.start() > pos:
            parts.append(template[pos:m.start()])
        if m.group(0) == "$$":
            parts.append("$")
        elif m.group(1) is not None:
            parts.append((int(m.group(1)),))
        else:
            name = m.group(2).strip()
            if not name:
                raise SearchError(_("Riferimento a gruppo vuoto nel testo sostitutivo: «${}»."))
            parts.append((int(name) if name.isdigit() else name,))
        pos = m.end()
    if pos < len(template):
        parts.append(template[pos:])
    return parts


def to_python_template(template: str) -> str:
    """Converte `$1`, `${nome}` nella sintassi Python `\\g<1>`, `\\g<nome>`."""
    out = []
    for part in parse_replacement(template):
        if isinstance(part, tuple):
            out.append(f"\\g<{part[0]}>")
        else:
            out.append(part.replace("\\", "\\\\"))
    return "".join(out)


def expand_replacement(match_obj, template: str, group_text: Callable[[int | str], str | None] | None = None
                       ) -> str:
    """Testo sostitutivo per una corrispondenza di espressione regolare.

    `group_text` (facoltativo) restituisce il testo di un gruppo: serve a usare il
    testo originale (con accenti e legature) invece di quello normalizzato.
    """
    getter = group_text or match_obj.group
    out = []
    for part in parse_replacement(template):
        if not isinstance(part, tuple):
            out.append(part)
            continue
        ref = part[0]
        try:
            value = getter(ref)
        except (IndexError, KeyError) as e:
            raise SearchError(_("Il testo sostitutivo fa riferimento al gruppo «{g}», che non esiste "
                                "nell'espressione.").format(g=ref)) from e
        out.append(value or "")
    return "".join(out)


def check_replacement(pattern: str, template: str, options: SearchOptions) -> None:
    """Verifica che i gruppi citati nel sostitutivo esistano (solo per le espressioni regolari)."""
    if not options.regex:
        return
    rx = compile_pattern(pattern, options)
    for part in parse_replacement(template):
        if isinstance(part, tuple):
            ref = part[0]
            ok = (0 <= ref <= rx.groups) if isinstance(ref, int) else ref in rx.groupindex
            if not ok:
                raise SearchError(_("Il testo sostitutivo fa riferimento al gruppo «{g}», che non esiste "
                                    "nell'espressione.").format(g=ref))


def replacement_for(match: Match, template: str, options: SearchOptions) -> str:
    """Testo che sostituirà l'occorrenza."""
    if not options.regex:
        return template

    def group_text(ref):
        idx = match.group_names[ref] if isinstance(ref, str) else ref
        if idx < 0 or idx >= len(match.groups):
            raise IndexError(ref)
        return match.groups[idx]

    return expand_replacement(None, template, group_text)


# ====================================================================== ricerca
def _line_spans(block: TextBlock) -> list[tuple[int, int, int]] | None:
    """(riga, inizio logico, fine logica) di ogni riga del blocco, o None se non ricostruibile."""
    text = block.text
    spans = []
    pos = 0
    lines = block.lines
    for i, line in enumerate(lines):
        lt = line.text
        last = i == len(lines) - 1
        hyphen = (not last and len(lt) >= 2 and lt.endswith("-")
                  and text[pos:pos + len(lt)] != lt and text[pos:pos + len(lt) - 1] == lt[:-1])
        n = len(lt) - 1 if hyphen else len(lt)
        if text[pos:pos + n] != lt[:n]:
            return None
        spans.append((i, pos, pos + n))
        pos += n
        if not last and not hyphen:
            pos += 1     # spazio o a capo tra le righe
    return spans if pos == len(text) else None


def _char_boxes(line) -> list[tuple[float, float] | None]:
    """Estensione orizzontale approssimata di ogni carattere della riga (interpolata nella parola)."""
    text = line.text
    out: list[tuple[float, float] | None] = [None] * len(text)
    j, w_idx = 0, 0
    n = len(text)
    while j < n:
        if not text[j].strip():
            j += 1
            continue
        k = j
        while k < n and text[k].strip():
            k += 1
        if w_idx < len(line.words):
            w = line.words[w_idx]
            step = (w.x1 - w.x) / max(1, k - j)
            for c in range(j, k):
                out[c] = (w.x + step * (c - j), w.x + step * (c - j + 1))
        w_idx += 1
        j = k
    return out


def _match_rects(block: TextBlock, spans, start: int, end: int, unframe: pymupdf.Matrix,
                 page: pymupdf.Page | None = None, cache: dict | None = None) -> list[pymupdf.Rect]:
    """Riquadri dell'occorrenza nella pagina non ruotata (approssimati carattere per carattere)."""
    rects: list[pymupdf.Rect] = []      # riquadro di lavoro
    direct: list[pymupdf.Rect] = []     # già nella pagina non ruotata
    for i, a, b in spans or []:
        lo, hi = max(a, start), min(b, end)
        if lo >= hi:
            continue
        line = block.lines[i]
        if block.rotated or abs(line.dir[1]) >= 0.02:
            hit = _search_hit(page, line, lo - a, hi - a, unframe)
            if hit is not None:
                direct.append(hit)
            else:
                rects.append(pymupdf.Rect(line.bbox))
            continue
        boxes = cache.get(i) if cache is not None else None
        if boxes is None:
            boxes = _char_boxes(line)
            if cache is not None:
                cache[i] = boxes
        xs = [bx for bx in boxes[lo - a:hi - a] if bx is not None]
        if xs:
            rects.append(pymupdf.Rect(min(x[0] for x in xs), line.bbox.y0, max(x[1] for x in xs), line.bbox.y1))
    if not rects and not direct and spans is None:
        rects = [pymupdf.Rect(ln.bbox) for ln in block.lines]
    if tuple(unframe) == (1, 0, 0, 1, 0, 0):
        return direct + rects
    return direct + [r * unframe for r in rects]


def _search_hit(page: pymupdf.Page | None, line, lo: int, hi: int, unframe: pymupdf.Matrix) -> pymupdf.Rect | None:
    """Per le righe non orizzontali: riquadro trovato dal motore nella riga (pagina non ruotata)."""
    if page is None:
        return None
    segment = line.text[lo:hi].strip()
    if not segment:
        return None
    clip = pymupdf.Rect(line.bbox) * unframe
    clip = pymupdf.Rect(clip.x0 - 1, clip.y0 - 1, clip.x1 + 1, clip.y1 + 1)
    try:
        hits = page.search_for(segment, clip=clip)
    except Exception:  # noqa: BLE001 - evidenziazione facoltativa
        return None
    k = line.text[:lo].lower().count(segment.lower())
    return pymupdf.Rect(hits[k]) if k < len(hits) else None


def _context(text: str, start: int, end: int) -> str:
    a = max(0, start - CONTEXT_CHARS)
    b = min(len(text), end + CONTEXT_CHARS)
    s = ("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else "")
    return re.sub(r"\s+", " ", s).strip()


def block_matches(block: TextBlock, rx: re.Pattern, options: SearchOptions,
                  unframe: pymupdf.Matrix | None = None, page: pymupdf.Page | None = None) -> list[Match]:
    """Occorrenze di `rx` nel blocco."""
    text = block.text
    if not text:
        return []
    norm = normalize(text, options.ignore_accents)
    spans = _line_spans(block)
    unframe = unframe or pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    reason = block.reason
    out: list[Match] = []
    boxes: dict = {}
    for m in rx.finditer(norm.text):
        if m.end() <= m.start():
            continue
        start, end = norm.span(m.start(), m.end())
        if end <= start:
            continue
        groups = []
        for g in range(rx.groups + 1):
            a, b = m.span(g)
            if a < 0:
                groups.append(None)
            else:
                s, e = norm.span(a, b)
                groups.append(text[s:e])
        out.append(Match(
            page=block.page, block=block.index, start=start, end=end, text=text[start:end],
            context=_context(text, start, end), rects=_match_rects(block, spans, start, end, unframe, page, boxes),
            replaceable=not reason, reason=_("blocco non modificabile: {r}").format(r=reason) if reason else "",
            block_bbox=pymupdf.Rect(block.bbox), groups=tuple(groups), group_names=dict(rx.groupindex),
        ))
    return out


def _page_indices(pdf: pymupdf.Document, pages: Iterable[int] | None) -> list[int]:
    if pages is None:
        return list(range(pdf.page_count))
    seen, out = set(), []
    for p in pages:
        p = int(p)
        if 0 <= p < pdf.page_count and p not in seen:
            seen.add(p)
            out.append(p)
    return sorted(out)


def _prefilter_token(pattern: str, options: SearchOptions) -> str | None:
    """Parte del testo letterale che deve comparire nel testo della pagina (per scartare in fretta)."""
    if options.regex:
        return None
    token = _loose(pattern, options)
    return token or None


def _loose(text: str, options: SearchOptions) -> str:
    """Testo senza spazi né trattini, per un confronto largo (mai falsi negativi)."""
    s = normalize(text, options.ignore_accents).text
    s = re.sub(r"[\s\-\u2010\u2011]+", "", s)
    return s if options.case_sensitive else s.casefold()


def _page_may_match(page: pymupdf.Page, token: str, options: SearchOptions) -> bool:
    try:
        text = page.get_text("text", flags=EXTRACT_FLAGS)
    except Exception:  # noqa: BLE001 - in caso di dubbio si analizza la pagina
        return True
    return token in _loose(text, options)


_CACHE: dict[tuple, list[TextBlock]] = {}
_CACHE_MAX = 3000


def _page_digest(page: pymupdf.Page) -> str | None:
    try:
        h = hashlib.blake2b(digest_size=16)
        h.update(page.read_contents() or b"")
        h.update(repr((page.rotation, tuple(page.rect), page.get_fonts(full=True))).encode())
        # il testo può stare anche in oggetti esterni (Form XObject) richiamati dalla pagina
        for x in page.get_xobjects():
            h.update(page.parent.xref_stream_raw(x[0]) or b"")
        return h.hexdigest()
    except Exception:  # noqa: BLE001 - senza impronta niente cache
        return None


def page_blocks(page: pymupdf.Page, page_index: int, granularity: str = "paragraph") -> list[TextBlock]:
    """`analyze_page` con una cache basata sul contenuto della pagina (ricerche ripetute veloci)."""
    digest = _page_digest(page)
    key = (page_index, granularity, digest)
    if digest is not None and key in _CACHE:
        return _CACHE[key]
    blocks = analyze_page(page, page_index, granularity)
    if digest is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = blocks
    return blocks


def find_all(pdf: pymupdf.Document, pattern: str, options: SearchOptions | None = None,
             pages: Iterable[int] | None = None, granularity: str = "paragraph") -> list[Match]:
    """Tutte le occorrenze nel testo di pagina, in ordine di pagina e di lettura.

    Va chiamata tenendo ENGINE_LOCK (o dentro `Document.change()`).
    """
    options = options or SearchOptions()
    rx = compile_pattern(pattern, options)
    token = _prefilter_token(pattern, options)
    out: list[Match] = []
    for p in _page_indices(pdf, pages):
        page = pdf[p]
        if token is not None and not _page_may_match(page, token, options):
            continue
        unframe = unframe_matrix(page)
        for block in page_blocks(page, p, granularity):
            out.extend(block_matches(block, rx, options, unframe, page))
    return out


def find_in_document(document, pattern: str, options: SearchOptions | None = None,
                     pages: Iterable[int] | None = None, granularity: str = "paragraph") -> list[Match]:
    """`find_all` su un `Document` di Foliant, tenendo il lock del motore."""
    with document.lock:
        return find_all(document.pdf, pattern, options, pages, granularity)


# ====================================================================== riscrittura del blocco
_TOUCH = object()     # segnaposto: punto in cui è avvenuta una sostituzione


def replace_in_block(block: TextBlock, matches_in_block: list[Match], replacement_texts: list[str], *,
                     delete: bool = False, remove_adjacent_space: bool = False) -> list[list[Run]]:
    """Nuovi paragrafi del blocco con le occorrenze sostituite.

    Ogni sostitutivo prende lo stile del primo carattere dell'occorrenza; gli altri
    run e i trattini di sillabazione restano invariati. Con `delete` (o sostitutivo
    vuoto e `remove_adjacent_space`) viene tolto anche uno spazio adiacente e i
    paragrafi rimasti vuoti vengono rimossi.
    """
    items: list[tuple[str, object]] = []        # (carattere, stile); a capo = ("\n", None)
    logical: list[int] = []                     # indice logico -> indice in items
    for pi, para in enumerate(block.paragraphs):
        if pi:
            logical.append(len(items))
            items.append(("\n", None))
        for run in para:
            for ch in run.text:
                if ch != SOFT_HYPHEN:
                    logical.append(len(items))
                items.append((ch, run.style))
    fallback = block.style
    pairs = sorted(zip(matches_in_block, replacement_texts), key=lambda mr: mr[0].start, reverse=True)
    limit = len(logical)
    for m, rep in pairs:
        if m.start < 0 or m.end > limit or m.end <= m.start:
            raise SearchError(_("L'occorrenza non corrisponde più al testo del blocco."))
        fs = logical[m.start]
        fe = logical[m.end - 1] + 1
        style = next((st for ch, st in items[fs:fe] if isinstance(st, Style) and ch != SOFT_HYPHEN), None)
        if style is None:
            style = next((st for ch, st in reversed(items[:fs]) if isinstance(st, Style)), fallback)
        new = [("\n", None) if c == "\n" else (c, style) for c in rep]
        items[fs:fe] = new + [("", _TOUCH)]
        if remove_adjacent_space and not rep:
            _drop_adjacent_space(items, fs)
    # ricostruzione dei paragrafi
    paragraphs: list[list[Run]] = []
    current: list[Run] = []
    touched = False

    def flush():
        text = "".join(r.text for r in current)
        if (delete or remove_adjacent_space) and touched and not text.replace(SOFT_HYPHEN, "").strip():
            return
        paragraphs.append(merge_runs(current))

    for ch, st in items:
        if st is _TOUCH:
            touched = True
        elif st is None:
            flush()
            current, touched = [], False
        else:
            current.append(Run(ch, st))
    flush()
    return paragraphs


def _drop_adjacent_space(items: list, pos: int) -> None:
    """Dopo un'eliminazione in `pos`, toglie gli spazi in eccesso così che non restino doppi spazi."""
    def skip(i: int, step: int) -> int:
        while 0 <= i < len(items) and (items[i][1] is _TOUCH or items[i][0] == SOFT_HYPHEN):
            i += step
        return i

    def is_space(i: int) -> bool:
        return 0 <= i < len(items) and items[i][1] is not None and items[i][0] in _SPACES

    def char(i: int) -> str | None:
        """Carattere in `i`, None al bordo del paragrafo."""
        if 0 <= i < len(items) and items[i][1] is not None:
            return items[i][0]
        return None

    # spazi che seguono e che precedono il punto di eliminazione
    after: list[int] = []
    i = skip(pos, 1)
    while is_space(i):
        after.append(i)
        i = skip(i + 1, 1)
    next_ch = char(i)
    before: list[int] = []
    i = skip(pos - 1, -1)
    while is_space(i):
        before.append(i)
        i = skip(i - 1, -1)
    prev_ch = char(i)
    drop: list[int] = []
    if after and (before or prev_ch is None):
        drop = after + before[1:] if before else after
        if next_ch is None:
            drop = after + before
    elif before and (next_ch is None or next_ch in _CLOSING_PUNCT):
        drop = before
    elif len(before) > 1:
        drop = before[1:]
    for k in sorted(drop, reverse=True):
        del items[k]


# ====================================================================== sostituzione
def _merge_substitutions(into: list[Substitution], new: list[Substitution]) -> None:
    for s in new:
        for t in into:
            if (t.original, t.substitute) == (s.original, s.substitute):
                t.chars += s.chars
                t.text += s.text
                break
        else:
            into.append(Substitution(s.original, s.substitute, s.chars, s.text))


def _apply_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, matches: list[Match],
                 texts: list[str], summary: ReplaceSummary, *, delete: bool, remove_adjacent_space: bool,
                 recompose: bool = True) -> None:
    if delete and not recompose:
        # senza ricomposizione: via solo le parole trovate, il resto della riga resta dov'è
        rects = [r for m in matches for r in m.rects]
        if rects and engine.delete_words_in_place(pdf, page_index, block, rects):
            summary.replaced += len(matches)
            summary.blocks_changed += 1
        else:
            for m in matches:
                summary.not_replaceable.append((m, _("L'occorrenza non copre parole intere.")))
        return
    paragraphs = replace_in_block(block, matches, texts, delete=delete,
                                  remove_adjacent_space=remove_adjacent_space)
    try:
        result = engine.replace_block(pdf, page_index, block, paragraphs)
    except engine.TextEditError as e:
        for m in matches:
            summary.not_replaceable.append((m, str(e)))
        return
    summary.replaced += len(matches)
    summary.blocks_changed += 1
    _merge_substitutions(summary.substitutions, result.substitutions)


def replace_all(document, pattern: str, replacement: str, options: SearchOptions | None = None,
                pages: Iterable[int] | None = None, granularity: str = "paragraph", delete: bool = False,
                remove_adjacent_space: bool = True, exclude: Iterable[tuple[int, int, int]] = (),
                recompose: bool = True) -> ReplaceSummary:
    """Sostituisce (o elimina) tutte le occorrenze in un unico passo annullabile.

    `exclude`: chiavi (`match_key`) delle occorrenze da lasciare invariate, riferite
    allo stato del documento in cui sono state trovate.
    """
    excluded = set(exclude)
    options = options or SearchOptions()
    rx = compile_pattern(pattern, options)
    if delete:
        replacement = ""
    else:
        check_replacement(pattern, replacement, options)
    removing = delete or replacement == ""
    summary = ReplaceSummary()
    # conteggio preliminare: senza occorrenze sostituibili non si crea un passo di annulla
    with document.lock:
        found = find_all(document.pdf, pattern, options, pages, granularity)
    found = [m for m in found if match_key(m) not in excluded]
    todo_pages = sorted({m.page for m in found if m.replaceable})
    if not todo_pages:
        summary.not_replaceable = [(m, m.reason) for m in found]
        return summary
    n = sum(1 for m in found if m.replaceable)
    label = (_("Trova ed elimina ({n})") if removing else _("Sostituisci tutto ({n})")).format(n=n)
    try:
        _replace_pages(document, label, todo_pages, rx, replacement, options, granularity, summary,
                       removing, remove_adjacent_space, excluded, recompose)
    except _NothingChanged:
        pass
    summary.not_replaceable.extend((m, m.reason) for m in found if not m.replaceable and m.page not in todo_pages)
    summary.not_replaceable.sort(key=lambda mr: (mr[0].page, mr[0].block, mr[0].start))
    return summary


class _NothingChanged(Exception):
    pass


def match_key(match: Match) -> tuple[int, int, int]:
    """Identità dell'occorrenza nello stato del documento in cui è stata trovata."""
    return (match.page, match.block, match.start)


def _replace_pages(document, label, todo_pages, rx, replacement, options, granularity, summary, removing,
                   remove_adjacent_space, excluded, recompose: bool = True) -> None:
    with document.change(label) as pdf:
        for p in todo_pages:
            page = pdf[p]
            unframe = unframe_matrix(page)
            blocks = analyze_page(page, p, granularity)
            # dal basso verso l'alto: un blocco che cresce non tocca quelli già riscritti
            for block in reversed(blocks):
                matches = [m for m in block_matches(block, rx, options, unframe, page) if match_key(m) not in excluded]
                if not matches:
                    continue
                if not block.editable:
                    summary.not_replaceable.extend((m, m.reason) for m in matches)
                    continue
                try:
                    texts = [replacement_for(m, replacement, options) for m in matches]
                except SearchError as e:
                    summary.not_replaceable.extend((m, str(e)) for m in matches)
                    continue
                _apply_block(pdf, p, block, matches, texts, summary, delete=removing,
                             remove_adjacent_space=remove_adjacent_space, recompose=recompose)
        if summary.replaced == 0:
            # nessuna modifica riuscita: annulla il passo (il documento torna com'era)
            raise _NothingChanged()


def locate(pdf: pymupdf.Document, match: Match, granularity: str = "paragraph") -> tuple[TextBlock, int, int]:
    """Ritrova l'occorrenza nel documento attuale: (blocco, inizio, fine). Solleva SearchError."""
    if not 0 <= match.page < pdf.page_count:
        raise SearchError(_("L'occorrenza non esiste più."))
    best, best_iou = None, 0.0
    for b in analyze_page(pdf[match.page], match.page, granularity):
        inter = pymupdf.Rect(b.bbox) & match.block_bbox
        if inter.is_empty:
            continue
        union = pymupdf.Rect(b.bbox) | match.block_bbox
        iou = inter.get_area() / max(union.get_area(), 1e-6)
        if iou > best_iou:
            best, best_iou = b, iou
    if best is None:
        raise SearchError(_("L'occorrenza non esiste più."))
    text = best.text
    if text[match.start:match.end] == match.text:
        return best, match.start, match.end
    positions = [m.start() for m in re.finditer(re.escape(match.text), text)]
    if not positions:
        raise SearchError(_("L'occorrenza non esiste più."))
    start = min(positions, key=lambda s: abs(s - match.start))
    return best, start, start + len(match.text)


def replace_one(document, match: Match, replacement: str, options: SearchOptions | None = None, *,
                granularity: str = "paragraph", delete: bool = False,
                remove_adjacent_space: bool = True) -> ReplaceSummary:
    """Sostituisce una sola occorrenza (un passo annullabile)."""
    options = options or SearchOptions()
    if not match.replaceable:
        raise SearchError(_("Questa occorrenza non può essere sostituita: {r}.").format(r=match.reason))
    text = "" if delete else replacement_for(match, replacement, options)
    with document.lock:
        block, start, end = locate(document.pdf, match, granularity)
    if not block.editable:
        raise SearchError(_("Questa occorrenza non può essere sostituita: {r}.").format(r=block.reason))
    label = _("Elimina «{t}»").format(t=_short(match.text)) if not text else \
        _("Sostituisci «{t}»").format(t=_short(match.text))
    summary = ReplaceSummary()
    with document.change(label) as pdf:
        block, start, end = locate(pdf, match, granularity)
        current = Match(page=match.page, block=block.index, start=start, end=end, text=match.text,
                        context=match.context, block_bbox=pymupdf.Rect(block.bbox))
        paragraphs = replace_in_block(block, [current], [text], delete=not text,
                                      remove_adjacent_space=remove_adjacent_space)
        try:
            result = engine.replace_block(pdf, match.page, block, paragraphs)
        except engine.TextEditError as e:
            raise SearchError(str(e)) from e
        summary.replaced = 1
        summary.blocks_changed = 1
        _merge_substitutions(summary.substitutions, result.substitutions)
    return summary


def _short(text: str, n: int = 24) -> str:
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[:n - 1] + "…"


# ====================================================================== ambito
def parse_page_ranges(text: str, page_count: int) -> list[int]:
    """«1-3, 5» (numeri da 1) -> indici di pagina da 0. Solleva SearchError se non valido."""
    pages: list[int] = []
    if not text.strip():
        raise SearchError(_("Indica le pagine, ad esempio «1-3, 5»."))
    for part in re.split(r"[,;]", text):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*(?:[-–]\s*(\d+))?", part)
        if not m:
            raise SearchError(_("Intervallo di pagine non valido: «{p}».").format(p=part))
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a > b:
            a, b = b, a
        if a < 1 or b > page_count:
            raise SearchError(_("Le pagine vanno da 1 a {n}: «{p}» non è valido.").format(n=page_count, p=part))
        pages.extend(range(a - 1, b))
    if not pages:
        raise SearchError(_("Indica le pagine, ad esempio «1-3, 5»."))
    return sorted(set(pages))
