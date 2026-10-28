"""Riconoscimento dei blocchi di testo di una pagina (TXT-01).

Raggruppa i glifi estratti dal motore in righe (stessa linea di base) e le
righe in paragrafi (stessa colonna, interlinea regolare, dimensione simile).
"""

from __future__ import annotations

import re
import statistics

import pymupdf

from .bidi import is_ltr_char, is_rtl_char, paragraph_is_rtl, unshape
from .model import (
    ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, GRANULARITY_LINE, SOFT_HYPHEN, Line, Run, Style,
    TextBlock, Word, color_from_int, merge_runs,
)

_SUBSET = re.compile(r"^[A-Z]{6}\+")
EXTRACT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP

REASON_UNDECODABLE = "non decodificabile"


def normalize_font_name(name: str) -> str:
    name = _SUBSET.sub("", name or "")
    return re.sub(r"[^a-z0-9]", "", name.lower())


def strip_subset(name: str) -> str:
    return _SUBSET.sub("", name or "")


def page_font_xrefs(page: pymupdf.Page) -> dict[str, int]:
    """Nome normalizzato del font -> xref, per i font della pagina."""
    out: dict[str, int] = {}
    try:
        fonts = page.get_fonts(full=True)
    except Exception:
        return out
    for f in fonts:
        xref, basefont = f[0], f[3]
        key = normalize_font_name(basefont)
        if key and key not in out:
            out[key] = xref
    for f in fonts:
        key = "~" + _family_key(f[3])
        if key != "~" and key not in out:
            out[key] = f[0]
    return out


_STYLE_SUFFIX = re.compile(r"(regular|roman|book|normal|mt|psmt|ps)$")


def _family_key(name: str) -> str:
    key = normalize_font_name(name)
    prev = None
    while key != prev:
        prev = key
        key = _STYLE_SUFFIX.sub("", key)
    return key


def lookup_xref(xrefs: dict[str, int], name: str) -> int:
    key = normalize_font_name(name)
    hit = xrefs.get(key) or xrefs.get("~" + _family_key(name), 0)
    if not hit and len(key) >= 8:
        # il motore tronca i nomi lunghi: confronto sul prefisso
        for k, xref in xrefs.items():
            if not k.startswith("~") and k.startswith(key):
                return xref
    return hit


def _style_from_span(span: dict, xrefs: dict[str, int]) -> Style:
    flags = span.get("flags", 0)
    name = strip_subset(span.get("font", "") or "Helvetica")
    low = name.lower()
    bold = bool(flags & 16) or "bold" in low or "black" in low or "heavy" in low
    italic = bool(flags & 2) or "italic" in low or "oblique" in low
    return Style(
        font=name,
        size=round(float(span.get("size", 12.0)), 3),
        color=color_from_int(int(span.get("color", 0) or 0)),
        bold=bold,
        italic=italic,
        serif=bool(flags & 4),
        mono=bool(flags & 8),
        font_xref=lookup_xref(xrefs, name),
    )


def _is_undecodable(text: str) -> bool:
    if not text:
        return False
    bad = sum(1 for c in text if c == "�" or 0xE000 <= ord(c) <= 0xF8FF)
    return bad > 0 and bad >= max(1, len(text.strip()) // 10)


def _make_line(raw_line: dict, xrefs: dict[str, int], frame: pymupdf.Matrix) -> tuple[Line, bool] | None:
    # tuple semplici per carattere (c, ox, oy, x0, y0, x1, y1, stile): l'analisi resta veloce
    fa, fb, fc, fd, fe, ff = frame.a, frame.b, frame.c, frame.d, frame.e, frame.f
    identity = (fa, fb, fc, fd, fe, ff) == (1, 0, 0, 1, 0, 0)
    chars: list[tuple] = []
    alphas: list[int] = []
    style_cache: dict[tuple, Style] = {}
    for span in raw_line.get("spans", []):
        span_chars = span.get("chars", [])
        if not span_chars:
            continue
        key = (span.get("font"), span.get("size"), span.get("color"), span.get("flags"))
        style = style_cache.get(key)
        if style is None:
            style = style_cache[key] = _style_from_span(span, xrefs)
        visible_text = False
        for ch in span_chars:
            c = ch["c"]
            ox, oy = ch["origin"]
            x0, y0, x1, y1 = ch["bbox"]
            if not identity:
                ox, oy = ox * fa + oy * fc + fe, ox * fb + oy * fd + ff
                ax, ay = x0 * fa + y0 * fc + fe, x0 * fb + y0 * fd + ff
                bx, by = x1 * fa + y1 * fc + fe, x1 * fb + y1 * fd + ff
                x0, x1 = min(ax, bx), max(ax, bx)
                y0, y1 = min(ay, by), max(ay, by)
            if "\uFB50" <= c <= "\uFEFF":
                c = unshape(c)          # forme di presentazione arabe -> lettere di base
            chars.append((c, ox, oy, x0, y0, x1, y1, style))
            if not visible_text and c.strip():
                visible_text = True
        if visible_text:
            alphas.append(span.get("alpha", 255))
    if any(is_rtl_char(c[0]) for c in chars):
        chars = _logical_rtl_chars(chars)
    # elimina spazi iniziali e finali
    start, end = 0, len(chars)
    while start < end and not chars[start][0].strip():
        start += 1
    while end > start and not chars[end - 1][0].strip():
        end -= 1
    chars = chars[start:end]
    if not chars:
        return None
    runs: list[Run] = []
    for c in chars:
        if runs and runs[-1].style is c[7]:
            runs[-1].text += c[0]
        else:
            runs.append(Run(c[0], c[7]))
    runs = merge_runs(runs)
    words: list[Word] = []
    first = None
    last = None
    buf: list[str] = []
    group: list = []

    def close_word() -> None:
        # x e x1 dai riquadri: nelle scritture da destra a sinistra l'ordine logico va verso sinistra
        words.append(Word("".join(c[0] for c in group), min(min(c[1], c[3]) for c in group),
                          max(c[5] for c in group), group[0][7]))

    for c in chars:
        if c[0].strip():
            group.append(c)
        elif group:
            close_word()
            group = []
    if group:
        close_word()
    bbox = pymupdf.Rect(min(c[3] for c in chars), min(c[4] for c in chars),
                        max(c[5] for c in chars), max(c[6] for c in chars))
    dx, dy = raw_line.get("dir", (1.0, 0.0))
    fdir = (dx * fa + dy * fc, dx * fb + dy * fd)
    line = Line(bbox=bbox, origin=pymupdf.Point(chars[0][1], chars[0][2]), runs=runs, dir=fdir, words=words)
    invisible = bool(alphas) and all(a == 0 for a in alphas)
    return line, invisible


def _logical_rtl_chars(chars: list[tuple]) -> list[tuple]:
    """Riga con scrittura da destra a sinistra: ordine logico ricostruito dalle posizioni visive
    (l'estrazione del motore non è affidabile con numeri e parole latine nella riga)."""
    visible = sorted((c for c in chars if c[0].strip()), key=lambda c: (c[3] + c[5]) / 2)
    if not visible:
        return chars
    size = max(c[7].size for c in visible)
    words: list[list[tuple]] = [[visible[0]]]
    for c in visible[1:]:
        if c[3] - words[-1][-1][5] > 0.15 * size:
            words.append([c])
        else:
            words[-1].append(c)

    def kind(word) -> str:
        text = "".join(c[0] for c in word)
        if any(is_rtl_char(ch) for ch in text):
            return "R"
        return "L" if any(is_ltr_char(ch) for ch in text) else "N"

    # gruppi visivi: sequenze L contigue restano nel loro ordine, il resto si legge da destra a sinistra
    groups: list[tuple[str, list]] = []
    for w in words:
        k = kind(w)
        if k == "L" and groups and groups[-1][0] == "L":
            groups[-1][1].append(w)
        else:
            groups.append(("L" if k == "L" else "R", [w]))
    logical_words: list[list[tuple]] = []
    for g, ws in reversed(groups):
        if g == "L":
            logical_words.extend(ws)
        else:
            logical_words.extend(list(reversed(w)) for w in reversed(ws))
    out: list[tuple] = []
    for k, w in enumerate(logical_words):
        if k:
            ref = w[0]
            out.append((" ", ref[1], ref[2], ref[3], ref[4], ref[3], ref[6], ref[7]))
        out.extend(w)
    return out


def frame_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Pagina non ruotata -> pagina come la vede l'utente (il "riquadro di lavoro")."""
    return pymupdf.Matrix(page.rotation_matrix) if page.rotation else pymupdf.Matrix(1, 0, 0, 1, 0, 0)


def unframe_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    return pymupdf.Matrix(page.derotation_matrix) if page.rotation else pymupdf.Matrix(1, 0, 0, 1, 0, 0)


def extract_lines(page: pymupdf.Page) -> list[tuple[Line, bool]]:
    xrefs = page_font_xrefs(page)
    frame = frame_matrix(page)
    raw = page.get_text("rawdict", flags=EXTRACT_FLAGS)
    out = []
    for block in raw.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for raw_line in block.get("lines", []):
            made = _make_line(raw_line, xrefs, frame)
            if made is not None:
                out.append(made)
    return _merge_segments(out)


def _split_line(line: Line, index: int) -> tuple[Line, Line]:
    """Divide una riga prima della parola `index` (i run seguono il testo delle parole)."""
    def make(words):
        runs: list[Run] = []
        for k, w in enumerate(words):
            if k:
                runs.append(Run(" ", words[k - 1].style))
            runs.append(Run(w.text, w.style))
        x0 = words[0].x
        x1 = words[-1].x1
        bbox = pymupdf.Rect(x0, line.bbox.y0, x1, line.bbox.y1)
        return Line(bbox=bbox, origin=pymupdf.Point(x0, line.origin.y), runs=merge_runs(runs), dir=line.dir,
                    words=list(words))
    return make(line.words[:index]), make(line.words[index:])


def _word_gaps(line: Line) -> list[float]:
    return [b.x - a.x1 for a, b in zip(line.words, line.words[1:])]


def _typical_gap(*lines: Line) -> float:
    """Spazio tipico tra le parole (mediana), per distinguere un rientro di colonna da uno spazio giustificato."""
    gaps = sorted(g for ln in lines for g in _word_gaps(ln) if g > 0)
    if not gaps:
        return 0.3 * max(ln.size for ln in lines)
    return gaps[len(gaps) // 2]


def _split_columns(lines: list[tuple[Line, bool]]) -> list[tuple[Line, bool]]:
    """Separa le celle di tabella che il motore ha messo nella stessa riga: una parola che inizia, dopo
    uno spazio largo, nello stesso punto in cui iniziano segmenti su altre linee di base."""
    starts: dict[int, set] = {}
    for line, _inv in lines:
        if _horizontal(line):
            starts.setdefault(round(line.bbox.x0), set()).add(round(line.baseline, 1))
    out: list[tuple[Line, bool]] = []
    for line, inv in lines:
        pending = [line]
        result = []
        while pending:
            ln = pending.pop(0)
            cut = None
            if _horizontal(ln) and len(ln.words) > 1:
                b = round(ln.baseline, 1)
                typical = _typical_gap(ln)
                for k in range(1, len(ln.words)):
                    gap = ln.words[k].x - ln.words[k - 1].x1
                    if gap < 0.6 * ln.size or gap < 2.0 * typical:
                        continue      # spazio normale o giustificato, non un salto di colonna
                    x = round(ln.words[k].x)
                    rows = set()
                    for d in (-1, 0, 1):
                        rows |= starts.get(x + d, set())
                    if rows - {b}:
                        cut = k
                        break
            if cut is None:
                result.append(ln)
            else:
                a, rest = _split_line(ln, cut)
                result.append(a)
                pending.insert(0, rest)
        out.extend((ln, inv) for ln in result)
    return out


def _merge_segments(lines: list[tuple[Line, bool]]) -> list[tuple[Line, bool]]:
    """Riunisce i segmenti della stessa riga separati da spazi larghi (testo giustificato)."""
    lines = _split_columns(lines)
    horiz = [x for x in lines if _horizontal(x[0])]
    others = [x for x in lines if not _horizontal(x[0])]
    horiz.sort(key=lambda x: (round(x[0].baseline, 1), x[0].bbox.x0))
    # inizi (e fine) di segmento ripetuti su più linee di base: colonne di una tabella, da non unire
    starts: dict[float, set] = {}
    ends: dict[float, set] = {}
    for line, _inv in horiz:
        starts.setdefault(round(line.bbox.x0), set()).add(round(line.baseline, 1))
        ends.setdefault(round(line.bbox.x1), set()).add(round(line.baseline, 1))

    def column(line: Line) -> bool:
        b = round(line.baseline, 1)
        # solo gli inizi: le fini coincidono anche nel testo giustificato (margine destro)
        for table in (starts,):
            edge = round(line.bbox.x0)
            rows = set()
            for k in (edge - 1, edge, edge + 1):
                rows |= table.get(k, set())
            if len(rows - {b}) >= 1:
                return True
        return False

    def end_aligned(line: Line) -> bool:
        b = round(line.baseline, 1)
        rows = set()
        for d in (-1, 0, 1):
            rows |= ends.get(round(line.bbox.x1) + d, set())
        return len(rows - {b}) >= 1

    merged: list[tuple[Line, bool]] = []
    for line, inv in horiz:
        if merged:
            prev, pinv = merged[-1]
            size = max(prev.size, line.size)
            gap = line.bbox.x0 - prev.bbox.x1
            typical = _typical_gap(prev, line)
            justified_gap = gap <= max(1.6 * typical, 0.35 * size)   # come gli altri spazi della riga
            # canaletta tra colonne: il segmento inizia dove iniziano altre righe e il precedente finisce
            # dove finiscono altre righe
            gutter = column(line) and end_aligned(prev)
            if (pinv == inv and abs(prev.baseline - line.baseline) < 0.3 and -0.5 <= gap <= 1.2 * size
                    and 0.8 <= line.size / prev.size <= 1.25 and not gutter
                    and (justified_gap or not column(line))):
                runs = merge_runs(prev.runs + [Run(" ", prev.runs[-1].style)] + line.runs)
                merged[-1] = (Line(bbox=prev.bbox | line.bbox, origin=prev.origin, runs=runs, dir=prev.dir,
                                   words=prev.words + line.words), inv)
                continue
        merged.append((line, inv))
    return merged + others


def _horizontal(line: Line) -> bool:
    return abs(line.dir[1]) < 0.02 and line.dir[0] > 0


def _compatible(prev: Line, line: Line, group: list[Line]) -> float | None:
    """Distanza (più piccola = migliore) se `line` può continuare il paragrafo, altrimenti None."""
    s0, s1 = prev.size, line.size
    if not (0.8 <= s1 / s0 <= 1.25):
        return None
    gap = line.baseline - prev.baseline
    size = max(s0, s1)
    if gap < 0.6 * size or gap > 1.75 * size:
        return None
    if len(group) >= 2:
        expected = group[-1].baseline - group[-2].baseline
        if abs(gap - expected) > 0.12 * expected + 0.6:
            return None
    # stessa colonna: sovrapposizione orizzontale e un bordo (o il centro) in comune
    if min(prev.bbox.x1, line.bbox.x1) - max(prev.bbox.x0, line.bbox.x0) <= 0:
        return None
    tol = max(1.5, 0.35 * size)
    lefts = abs(prev.bbox.x0 - line.bbox.x0) <= tol
    indent_first = len(group) == 1 and 0 < prev.bbox.x0 - line.bbox.x0 <= 4 * size
    centers = abs((prev.bbox.x0 + prev.bbox.x1) - (line.bbox.x0 + line.bbox.x1)) / 2 <= tol
    rights = abs(prev.bbox.x1 - line.bbox.x1) <= tol
    if len(group) >= 2:
        # il paragrafo ha già un allineamento: deve essere mantenuto
        g0, g1 = group[-2], group[-1]
        if abs(g0.bbox.x0 - g1.bbox.x0) <= tol or (len(group) == 2 and 0 < g0.bbox.x0 - g1.bbox.x0 <= 4 * size):
            ok = lefts
        elif abs((g0.bbox.x0 + g0.bbox.x1) - (g1.bbox.x0 + g1.bbox.x1)) / 2 <= tol:
            ok = centers
        else:
            ok = rights
        if not ok:
            return None
    elif not (lefts or indent_first or centers or rights):
        return None
    return gap


def group_lines(lines: list[Line], rules: list | None = None) -> list[list[Line]]:
    """Raggruppa righe orizzontali in paragrafi. `rules`: filetti orizzontali (rettangoli sottili),
    che separano sempre due righe."""
    ordered = sorted(lines, key=lambda ln: (round(ln.baseline, 1), ln.bbox.x0))
    # righe con altro testo vicino sulla stessa linea di base (celle di tabella): non si uniscono
    # a righe senza vicini (paragrafi)
    def has_neighbor(line: Line) -> bool:
        for other in ordered:
            if other is line or abs(other.baseline - line.baseline) > 0.5:
                continue
            gap = max(other.bbox.x0 - line.bbox.x1, line.bbox.x0 - other.bbox.x1)
            if gap <= 0:
                continue
            # vicino: una cella corta (quantità, prezzo, codice) sulla stessa linea di base, a qualsiasi
            # distanza; due segmenti lunghi affiancati sono invece colonne di testo
            if other.bbox.width < 6 * other.size or line.bbox.width < 6 * line.size:
                return True
        return False

    neighbors = {id(ln): has_neighbor(ln) for ln in ordered}
    groups: list[list[Line]] = []
    for line in ordered:
        if neighbors[id(line)]:
            # cella di tabella: blocco a sé, così una modifica non rifluisce nella riga sotto
            groups.append([line])
            continue
        best, best_d = None, None
        for g in groups:
            if neighbors[id(g[-1])]:
                continue
            if rules and _rule_between(g[-1], line, rules):
                continue
            d = _compatible(g[-1], line, g)
            if d is not None and (best_d is None or d < best_d):
                # nessun'altra riga già assegnata deve trovarsi tra le due
                best, best_d = g, d
        if best is not None:
            best.append(line)
        else:
            groups.append([line])
    return groups


def _rule_between(a: Line, b: Line, rules: list) -> bool:
    top, bottom = a.baseline, b.bbox.y0 + 0.2 * b.size
    for r in rules:
        y = (r.y0 + r.y1) / 2
        if top < y < bottom and min(a.bbox.x1, b.bbox.x1, r.x1) - max(a.bbox.x0, b.bbox.x0, r.x0) > 0:
            return True
    return False


def page_rules(page: pymupdf.Page) -> list:
    """Filetti orizzontali della pagina (riquadro di lavoro)."""
    out = []
    try:
        frame = frame_matrix(page)
        for d in page.get_drawings():
            r = pymupdf.Rect(d.get("rect")) * frame
            if r.height <= 2.0 and r.width >= 20:
                out.append(r)
    except Exception:
        return []
    return out


def _spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def _detect_alignment(lines: list[Line]) -> tuple[str, float, float, float]:
    """(allineamento, x0, larghezza, rientro prima riga)."""
    lefts = [ln.bbox.x0 for ln in lines]
    rights = [ln.bbox.x1 for ln in lines]
    size = statistics.median(ln.size for ln in lines)
    tol = max(1.0, 0.15 * size)
    x0, x1 = min(lefts), max(rights)
    if len(lines) == 1:
        # una riga sola: allineamento ambiguo; a destra per le scritture da destra a sinistra (TXT-05)
        return (ALIGN_RIGHT if paragraph_is_rtl(lines[0].text) else ALIGN_LEFT), x0, x1 - x0, 0.0
    body_lefts = lefts[1:] if len(lines) > 2 else lefts
    indent = 0.0
    left_eq = _spread(body_lefts) <= tol
    if left_eq and len(lines) > 2 and lefts[0] - min(body_lefts) > tol:
        indent = lefts[0] - min(body_lefts)
        x0 = min(body_lefts)
    elif not left_eq:
        left_eq = False
    centers = [(a + b) / 2 for a, b in zip(lefts, rights)]
    if left_eq and len(lines) >= 3 and _spread(rights[:-1]) <= tol and rights[-1] <= max(rights[:-1]) + tol:
        return ALIGN_JUSTIFY, x0, max(rights[:-1]) - x0, indent
    if left_eq and len(lines) == 2 and rights[1] < rights[0] - tol:
        # due righe: giustificato se la prima ha spazi tra le parole molto più larghi della seconda
        def mean_gap(ln: Line) -> float:
            gaps = [b.x - a.x1 for a, b in zip(ln.words, ln.words[1:])]
            return sum(gaps) / len(gaps) if gaps else 0.0
        g0, g1 = mean_gap(lines[0]), mean_gap(lines[1])
        if g1 > 0 and g0 > g1 * 1.25 and g0 - g1 > 0.05 * size:
            return ALIGN_JUSTIFY, x0, rights[0] - x0, indent
    if left_eq and (indent or _spread(lefts) <= tol):
        return ALIGN_LEFT, x0, x1 - x0, indent
    if _spread(centers) <= tol:
        return ALIGN_CENTER, x0, x1 - x0, 0.0
    if _spread(rights) <= tol:
        return ALIGN_RIGHT, x0, x1 - x0, 0.0
    return ALIGN_LEFT, x0, x1 - x0, 0.0


def _space_run(style: Style) -> Run:
    return Run(" ", style)


def _paragraphs_from_lines(lines: list[Line], align: str, x0: float, width: float) -> list[list[Run]]:
    paragraphs: list[list[Run]] = [[]]
    for i, line in enumerate(lines):
        runs = [Run(r.text, r.style) for r in line.runs]
        if i < len(lines) - 1:
            nxt = lines[i + 1]
            last = runs[-1]
            text = line.text
            first_next = nxt.words[0] if nxt.words else None
            if (len(text) >= 2 and text.endswith("-") and text[-2].isalpha() and first_next
                    and first_next.text[:1].islower()):
                runs[-1] = Run(last.text[:-1] + SOFT_HYPHEN, last.style)
                paragraphs[-1].extend(runs)
                continue
            hard = False
            if first_next is not None and align in (ALIGN_LEFT, ALIGN_JUSTIFY):
                space = 0.3 * line.size
                word_w = first_next.x1 - first_next.x
                if line.bbox.x1 + space + word_w < x0 + width - max(2.0, 0.2 * line.size):
                    hard = True
            elif first_next is not None and align in (ALIGN_CENTER, ALIGN_RIGHT):
                space = 0.3 * line.size
                word_w = first_next.x1 - first_next.x
                if line.bbox.width + space + word_w < width - max(2.0, 0.2 * line.size):
                    hard = True
            paragraphs[-1].extend(runs)
            if hard:
                paragraphs.append([])
            else:
                paragraphs[-1].append(_space_run(last.style))
        else:
            paragraphs[-1].extend(runs)
    return [merge_runs(p) for p in paragraphs]


def build_block(page_index: int, index: int, lines: list[Line], invisible: bool) -> TextBlock:
    rotated = not _horizontal(lines[0])
    if rotated:
        align, x0, width, indent = ALIGN_LEFT, lines[0].bbox.x0, lines[0].bbox.width, 0.0
    else:
        align, x0, width, indent = _detect_alignment(lines)
    if len(lines) >= 2:
        gaps = [b.baseline - a.baseline for a, b in zip(lines, lines[1:])]
        leading = statistics.median(gaps)
    else:
        leading = round(lines[0].size * 1.2, 3)
    paragraphs = _paragraphs_from_lines(lines, align, x0, width) if not rotated else [
        merge_runs([r for r in lines[0].runs])]
    text = "".join(ln.text for ln in lines)
    block = TextBlock(page=page_index, index=index, lines=lines, paragraphs=paragraphs, align=align,
                      leading=leading, width=width, auto_width=len(lines) == 1, indent=indent,
                      invisible=invisible, rotated=rotated)
    if _is_undecodable(text):
        block.decodable = False
        block.reason = REASON_UNDECODABLE
    return block


def analyze_page(page: pymupdf.Page, page_index: int = 0, granularity: str = "paragraph") -> list[TextBlock]:
    """Blocchi di testo modificabili della pagina, in ordine di lettura."""
    extracted = extract_lines(page)
    rules = page_rules(page) if granularity != GRANULARITY_LINE else []
    horizontal = [(ln, inv) for ln, inv in extracted if _horizontal(ln)]
    others = [(ln, inv) for ln, inv in extracted if not _horizontal(ln)]
    groups: list[tuple[list[Line], bool]] = []
    for inv in (False, True):
        subset = [ln for ln, i in horizontal if i == inv]
        if granularity == GRANULARITY_LINE:
            groups.extend(([ln], inv) for ln in subset)
        else:
            groups.extend((g, inv) for g in group_lines(subset, rules))
    groups.extend(([ln], inv) for ln, inv in others)
    groups.sort(key=lambda g: (round(g[0][0].bbox.y0 / 4), g[0][0].bbox.x0))
    return [build_block(page_index, i, g, inv) for i, (g, inv) in enumerate(groups)]


def block_at(blocks: list[TextBlock], point: pymupdf.Point, margin: float = 2.0) -> TextBlock | None:
    best = None
    for b in blocks:
        r = pymupdf.Rect(b.bbox)
        r.x0 -= margin
        r.y0 -= margin
        r.x1 += margin
        r.y1 += margin
        if r.contains(point):
            if best is None or b.bbox.get_area() < best.bbox.get_area():
                best = b
    return best


def page_has_images(page: pymupdf.Page) -> bool:
    try:
        return bool(page.get_images(full=False))
    except Exception:
        return False


# ---------------------------------------------------------------- unione e divisione (TXT-09)
SEPARATOR_SPACE = "space"
SEPARATOR_PARAGRAPH = "paragraph"


class GroupingError(ValueError):
    pass


def line_key(page_index: int, line: Line) -> tuple:
    return (page_index, round(line.baseline, 1), round(line.bbox.x0, 1), line.text[:12])


def merge_blocks(blocks: list[TextBlock], separator: str = SEPARATOR_SPACE) -> TextBlock:
    """Unisce blocchi della stessa pagina in uno (ordine di lettura); l'aspetto della pagina non cambia."""
    if len(blocks) < 2:
        raise GroupingError("Servono almeno due blocchi.")
    if len({b.page for b in blocks}) != 1:
        raise GroupingError("I blocchi devono essere sulla stessa pagina.")
    if any(b.rotated for b in blocks) or len({b.invisible for b in blocks}) != 1:
        raise GroupingError("I blocchi hanno orientamento o tipo diverso.")
    ordered = sorted(blocks, key=lambda b: (round(b.bbox.y0, 1), b.bbox.x0))
    first = ordered[0]
    paragraphs = [list(p) for p in first.paragraphs]
    for b in ordered[1:]:
        if separator == SEPARATOR_PARAGRAPH or not paragraphs:
            paragraphs.extend(list(p) for p in b.paragraphs)
        else:
            last = paragraphs[-1]
            style = last[-1].style if last else b.style
            paragraphs[-1] = merge_runs(last + [Run(" ", style)] + list(b.paragraphs[0]))
            paragraphs.extend(list(p) for p in b.paragraphs[1:])
    lines = [ln for b in ordered for ln in b.lines]
    x0 = min(b.x0 for b in ordered)
    x1 = max(max(b.x1, b.bbox.x1) for b in ordered)
    return TextBlock(page=first.page, index=first.index, lines=lines, paragraphs=paragraphs, align=first.align,
                     leading=first.leading, width=x1 - x0, auto_width=False, indent=first.indent,
                     invisible=first.invisible, decodable=all(b.decodable for b in ordered),
                     reason=next((b.reason for b in ordered if b.reason), ""))


def split_block(block: TextBlock, line_index: int) -> tuple[TextBlock, TextBlock] | None:
    """Divide il blocco prima della riga `line_index` (nessun effetto all'inizio o alla fine)."""
    if line_index <= 0 or line_index >= len(block.lines):
        return None
    a = build_block(block.page, block.index, block.lines[:line_index], block.invisible)
    b = build_block(block.page, block.index + 1, block.lines[line_index:], block.invisible)
    for part in (a, b):
        part.leading = block.leading if len(part.lines) == 1 else part.leading
        if len(part.lines) == 1 and block.align != ALIGN_LEFT:
            part.align = block.align
    return a, b


def apply_grouping(blocks: list[TextBlock], page_index: int, merges: list[tuple], splits: set) -> list[TextBlock]:
    """Applica al riconoscimento automatico le unioni e divisioni scelte dall'utente."""
    out = list(blocks)
    for key in splits:
        for i, b in enumerate(out):
            keys = [line_key(page_index, ln) for ln in b.lines]
            if key in keys:
                parts = split_block(b, keys.index(key))
                if parts:
                    out[i:i + 1] = list(parts)
                break
    for group, separator in merges:
        members = []
        for b in out:
            keys = {line_key(page_index, ln) for ln in b.lines}
            if keys & set(group):
                members.append(b)
        covered = {line_key(page_index, ln) for b in members for ln in b.lines}
        if len(members) >= 2 and set(group) <= covered:
            try:
                merged = merge_blocks(members, separator)
            except GroupingError:
                continue
            pos = min(out.index(m) for m in members)
            out = [b for b in out if b not in members]
            out.insert(pos, merged)
    for i, b in enumerate(out):
        b.index = i
    return out
