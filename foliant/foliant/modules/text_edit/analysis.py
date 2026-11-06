"""Riconoscimento dei blocchi di testo di una pagina (TXT-01).

Raggruppa i glifi estratti dal motore in righe (stessa linea di base) e le
righe in paragrafi (stessa colonna, interlinea regolare, dimensione simile).
"""

from __future__ import annotations

import bisect
import re
import statistics
from dataclasses import dataclass, field

import pymupdf

from .bidi import is_ltr_char, is_rtl_char, paragraph_is_rtl, unshape
from .model import (
    ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, GRANULARITY_LINE, NOT_COUNTED, SOFT_HYPHEN, Line, Run,
    Style, TextBlock, Word, color_from_int, merge_runs,
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


def extract_lines(page: pymupdf.Page, vborders: list | None = None) -> list[tuple[Line, bool]]:
    """Righe di testo della pagina. `vborders`: bordi verticali di `page_borders` (letti dalla pagina se
    mancano), che separano le celle di tabella scritte dal motore nella stessa riga."""
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
    if vborders is None:
        vborders = page_borders(page)[0]
    return _merge_segments(out, vborders)


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


def _cut_key(line: Line) -> tuple:
    return (round(line.baseline, 1), round(line.bbox.x0, 1))


def _split_words(line: Line, cuts: list[int]) -> list[Line]:
    """Divide una riga prima delle parole di indice `cuts` (crescenti)."""
    out: list[Line] = []
    cur, offset = line, 0
    for k in cuts:
        a, cur = _split_line(cur, k - offset)
        offset = k
        out.append(a)
    out.append(cur)
    return out


def _border_between(x_left: float, x_right: float, line: Line, vborders: list) -> bool:
    """Un bordo verticale passa nello spazio tra due parole (o due segmenti) della riga, staccato da
    entrambe: il bordo tra due celle, non un tratto che tocca o attraversa il testo."""
    y = (line.bbox.y0 + line.bbox.y1) / 2
    for x, y0, y1 in vborders:
        if x_left + 0.5 <= x <= x_right - 0.5 and y0 - 0.5 <= y <= y1 + 0.5:
            return True
    return False


def _split_at_borders(lines: list[tuple[Line, bool]], vborders: list, cuts: set) -> list[tuple[Line, bool]]:
    """Separa le celle di tabella che il motore ha messo nella stessa riga quando tra due parole passa un
    bordo verticale (PDF stampati dai browser: celle vicine con poco spazio interno)."""
    if not vborders:
        return lines
    out: list[tuple[Line, bool]] = []
    for line, inv in lines:
        ks = []
        if _horizontal(line) and len(line.words) > 1:
            for k in range(1, len(line.words)):
                a, b = line.words[k - 1], line.words[k]
                # più largo di uno spazio normale: in una cella il bordo sta nel margine interno
                if b.x - a.x1 >= 0.35 * line.size and _border_between(a.x1, b.x, line, vborders):
                    ks.append(k)
        if not ks:
            out.append((line, inv))
            continue
        parts = _split_words(line, ks)
        cuts.update(_cut_key(p) for p in parts[1:])
        out.extend((p, inv) for p in parts)
    return out


def _wide_gaps(line: Line) -> list[tuple[int, float, float]]:
    """Spazi della riga molto più larghi degli altri: (indice della parola dopo, x0, x1)."""
    gaps = _word_gaps(line)
    out = []
    for k, g in enumerate(gaps, 1):
        if g < 0.6 * line.size:
            continue
        others = sorted(x for j, x in enumerate(gaps, 1) if j != k and x > 0)
        typical = others[len(others) // 4] if others else 0.3 * line.size
        if g >= 2 * typical:
            out.append((k, line.words[k - 1].x1, line.words[k].x))
    return out


def _split_channels(lines: list[tuple[Line, bool]], cuts: set) -> list[tuple[Line, bool]]:
    """Tabelle senza bordi: separa due celle che il motore ha messo nella stessa riga quando lo spazio
    tra loro è molto più largo degli altri e prosegue, come corridoio bianco, in almeno due righe vicine,
    con un bordo in comune (colonna allineata a sinistra o a destra). Il testo giustificato ha spazi
    uniformi e non viene diviso."""
    horiz = [ln for ln, _inv in lines if _horizontal(ln)]
    if len(horiz) < 3:
        return lines
    wide = {id(ln): _wide_gaps(ln) for ln in horiz if len(ln.words) > 1}
    if not any(wide.values()):
        return lines
    # spazi bianchi di ogni linea di base: tra i segmenti e, dentro i segmenti, quelli molto larghi
    rows: list[tuple[float, list[tuple[float, float]]]] = []
    for ln in sorted(horiz, key=lambda x: (x.baseline, x.bbox.x0)):
        if rows and abs(rows[-1][0] - ln.baseline) <= 0.5:
            rows[-1][1].append(ln)
        else:
            rows.append((ln.baseline, [ln]))
    spaces: list[tuple[float, list[tuple[float, float]]]] = []
    for base, segs in rows:
        segs.sort(key=lambda s: s.bbox.x0)
        ivs = [(s.bbox.x1, t.bbox.x0) for s, t in zip(segs, segs[1:])
               if t.bbox.x0 - s.bbox.x1 >= 0.6 * max(s.size, t.size)]
        for s in segs:
            ivs.extend((x0, x1) for _k, x0, x1 in wide.get(id(s), ()))
        spaces.append((base, ivs))

    def channel(line: Line, x0: float, x1: float) -> bool:
        size = line.size
        hits, edge = 0, False
        for base, ivs in spaces:
            if abs(base - line.baseline) <= 0.5 or abs(base - line.baseline) > 4 * size:
                continue
            for u0, u1 in ivs:
                overlap = min(x1, u1) - max(x0, u0)
                if overlap >= max(0.2 * size, 0.5 * min(x1 - x0, u1 - u0)):
                    hits += 1
                    edge = edge or abs(u0 - x0) <= 1.0 or abs(u1 - x1) <= 1.0
                    break
        return hits >= 2 and edge

    out: list[tuple[Line, bool]] = []
    for line, inv in lines:
        ks = [k for k, x0, x1 in wide.get(id(line), ()) if channel(line, x0, x1)]
        if not ks:
            out.append((line, inv))
            continue
        parts = _split_words(line, ks)
        cuts.update(_cut_key(p) for p in parts[1:])
        out.extend((p, inv) for p in parts)
    return out


def _merge_segments(lines: list[tuple[Line, bool]], vborders: list | None = None) -> list[tuple[Line, bool]]:
    """Riunisce i segmenti della stessa riga separati da spazi larghi (testo giustificato), dopo aver
    separato le celle di tabella scritte nella stessa riga."""
    vborders = vborders or []
    cuts: set = set()
    lines = _split_at_borders(lines, vborders, cuts)
    lines = _split_channels(lines, cuts)
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
            # celle separate (bordo verticale in mezzo, o divise sopra): mai riunite
            gutter = gutter or _cut_key(line) in cuts or (
                bool(vborders) and _border_between(prev.bbox.x1, line.bbox.x0, line, vborders))
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


def _cell_box(line: Line, vborders: list, hborders: list) -> tuple | None:
    """Cella chiusa da bordi sui quattro lati che contiene la riga, come (sinistra, alto, destra, basso);
    None se manca un lato."""
    box = line.bbox
    y = (box.y0 + box.y1) / 2
    lefts = [x for x, y0, y1 in vborders if y0 - 0.5 <= y <= y1 + 0.5 and x <= box.x0 + 0.5]
    rights = [x for x, y0, y1 in vborders if y0 - 0.5 <= y <= y1 + 0.5 and x >= box.x1 - 0.5]
    if not lefts or not rights:
        return None
    left, right = max(lefts), min(rights)
    # sopra e sotto: bordi larghi quanto la cella (non una sottolineatura o un disegno nella cella)
    across = [hy for hy, x0, x1 in hborders if x0 <= left + 2.0 and x1 >= right - 2.0]
    tops = [hy for hy in across if hy <= line.baseline - 0.4 * line.size]
    bottoms = [hy for hy in across if hy >= line.baseline]
    if not tops or not bottoms:
        return None
    return (left, max(tops), right, min(bottoms))


def _same_cell(a: tuple | None, b: tuple | None) -> bool:
    return a is not None and b is not None and all(abs(p - q) <= 1.0 for p, q in zip(a, b))


def cell_boxes(lines: list[Line], borders: tuple[list, list]) -> dict[int, tuple]:
    """id(riga) -> cella chiusa da bordi che la contiene (solo le righe orizzontali in una cella di
    tabella: le cornici alte, come il riquadro della pagina o di un pannello, non sono celle)."""
    vborders, hborders = borders
    if not vborders or not hborders:
        return {}
    out = {}
    for ln in lines:
        if _horizontal(ln):
            box = _cell_box(ln, vborders, hborders)
            if box is not None and box[3] - box[1] <= 15 * ln.size:
                out[id(ln)] = box
    return out


def group_lines(lines: list[Line], rules: list | None = None, cells: dict | None = None,
                hborders: list | None = None) -> list[list[Line]]:
    """Raggruppa righe orizzontali in paragrafi. `rules`: filetti orizzontali (rettangoli sottili),
    che separano sempre due righe. `cells` (di `cell_boxes`) e `hborders`: le righe della stessa cella di
    una tabella con bordi formano un paragrafo anche se nella riga della tabella ci sono altre celle."""
    ordered = sorted(lines, key=lambda ln: (round(ln.baseline, 1), ln.bbox.x0))
    cells = dict(cells or {})
    # una cella vale solo se la riga è da sola nella cella (altrimenti è una tabella senza bordi interni)
    for ln in ordered:
        box = cells.get(id(ln))
        if box is not None and any(o is not ln and abs(o.baseline - ln.baseline) <= 0.5
                                   and o.bbox.x1 > box[0] and o.bbox.x0 < box[2] for o in ordered):
            del cells[id(ln)]
    cell_rules = list(rules or []) + [pymupdf.Rect(x0, y, x1, y) for y, x0, x1 in (hborders or ())]
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
        cell = cells.get(id(line))
        if cell is not None:
            # riga di una cella con bordi: continua il paragrafo della stessa cella (testo andato a capo
            # nella cella), mai attraverso un bordo orizzontale
            best, best_d = None, None
            for g in groups:
                if not _same_cell(cells.get(id(g[-1])), cell) or _rule_between(g[-1], line, cell_rules):
                    continue
                d = _compatible(g[-1], line, g)
                if d is not None and (best_d is None or d < best_d):
                    best, best_d = g, d
            if best is not None:
                best.append(line)
                continue
        if neighbors[id(line)]:
            # cella di tabella: blocco a sé, così una modifica non rifluisce nella riga sotto
            groups.append([line])
            continue
        best, best_d = None, None
        for g in groups:
            if neighbors[id(g[-1])]:
                continue
            other = cells.get(id(g[-1]))
            if cell is not None and other is not None and not _same_cell(cell, other):
                continue      # celle diverse della tabella
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


def page_drawings(page: pymupdf.Page) -> list:
    """Disegni vettoriali della pagina (una sola lettura per analisi: su pagine con molta grafica è lenta)."""
    try:
        return page.get_drawings()
    except Exception:
        return []


def page_rules(page: pymupdf.Page, drawings: list | None = None) -> list:
    """Filetti orizzontali della pagina (riquadro di lavoro)."""
    out = []
    try:
        frame = frame_matrix(page)
        for d in page_drawings(page) if drawings is None else drawings:
            r = pymupdf.Rect(d.get("rect")) * frame
            if r.height <= 2.0 and r.width >= 20:
                out.append(r)
    except Exception:
        return []
    return out


def page_borders(page: pymupdf.Page, drawings: list | None = None) -> tuple[list, list]:
    """Bordi di tabella della pagina (riquadro di lavoro): verticali come (x, y0, y1) e orizzontali come
    (y, x0, x1).

    Solo tratti di bordo: linee, lati dei rettangoli tracciati, rettangoli pieni sottili (i bordi dei
    browser); mai i lati dei riempimenti larghi (sfondi, evidenziazioni del testo, loghi)."""
    vertical: list[tuple[float, float, float]] = []
    horizontal: list[tuple[float, float, float]] = []
    try:
        frame = frame_matrix(page)
        for d in page_drawings(page) if drawings is None else drawings:
            stroked = "s" in (d.get("type") or "")
            for item in d.get("items", ()):
                kind = item[0]
                if kind == "l":
                    p0, p1 = item[1] * frame, item[2] * frame
                    if abs(p0.x - p1.x) <= 1.0 and abs(p0.y - p1.y) >= 4.0:
                        vertical.append(((p0.x + p1.x) / 2, min(p0.y, p1.y), max(p0.y, p1.y)))
                    elif abs(p0.y - p1.y) <= 1.0 and abs(p0.x - p1.x) >= 4.0:
                        horizontal.append(((p0.y + p1.y) / 2, min(p0.x, p1.x), max(p0.x, p1.x)))
                    continue
                if kind == "re":
                    r = pymupdf.Rect(item[1]) * frame
                elif kind == "qu":
                    q = item[1]
                    r = pymupdf.Rect(q.rect) * frame
                    if not q.is_rectangular:
                        continue
                else:
                    continue
                if r.width <= 2.0 and r.height >= 4.0:          # filetto verticale disegnato come rettangolo
                    vertical.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                elif r.height <= 2.0 and r.width >= 4.0:        # filetto orizzontale
                    horizontal.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
                elif stroked and r.width > 2.0 and r.height > 2.0:
                    vertical.append((r.x0, r.y0, r.y1))
                    vertical.append((r.x1, r.y0, r.y1))
                    horizontal.append((r.y0, r.x0, r.x1))
                    horizontal.append((r.y1, r.x0, r.x1))
    except Exception:
        return [], []
    return vertical, horizontal


def page_edges(page: pymupdf.Page, drawings: list | None = None) -> list[tuple[float, float, float]]:
    """Bordi verticali della pagina (riquadro di lavoro): linee e lati dei rettangoli, come
    (x, y0, y1). Servono a riconoscere le celle di tabella che contengono una riga di testo."""
    out: list[tuple[float, float, float]] = []
    try:
        frame = frame_matrix(page)
        for d in page_drawings(page) if drawings is None else drawings:
            for item in d.get("items", ()):
                kind = item[0]
                if kind == "l":
                    p0, p1 = item[1] * frame, item[2] * frame
                    if abs(p0.x - p1.x) <= 1.0 and abs(p0.y - p1.y) >= 4.0:
                        out.append(((p0.x + p1.x) / 2, min(p0.y, p1.y), max(p0.y, p1.y)))
                elif kind == "re":
                    r = pymupdf.Rect(item[1]) * frame
                    if r.height < 4.0:
                        continue
                    if r.width <= 2.0:                       # filetto verticale disegnato come rettangolo
                        out.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                    else:
                        out.append((r.x0, r.y0, r.y1))
                        out.append((r.x1, r.y0, r.y1))
    except Exception:
        return []
    return out


def _cell_alignment(line: Line, edges: list) -> str | None:
    """Allineamento di una riga sola dentro una cella di tabella (bordi a sinistra e a destra alla sua
    altezza): centrata o a destra se è così nella cella, altrimenti None (non si sa)."""
    sides = _cell_sides(line, edges)
    if sides is None:
        return None
    left, right = sides
    box = line.bbox
    ml, mr = box.x0 - left, right - box.x1
    tol = max(1.5, 0.3 * line.size)
    if min(ml, mr) > tol and abs(ml - mr) <= tol:
        return ALIGN_CENTER
    if mr <= max(tol, 0.6 * line.size) and ml > 2 * mr + tol:
        return ALIGN_RIGHT
    return None


def _cell_sides(line: Line, edges: list) -> tuple[float, float] | None:
    box = line.bbox
    y = (box.y0 + box.y1) / 2
    lefts = [x for x, y0, y1 in edges if y0 - 0.5 <= y <= y1 + 0.5 and x <= box.x0 + 0.5]
    rights = [x for x, y0, y1 in edges if y0 - 0.5 <= y <= y1 + 0.5 and x >= box.x1 - 0.5]
    if not lefts or not rights:
        return None
    return max(lefts), min(rights)


def _column_alignments(lines: list[Line], edges: list, page_width: float) -> dict[int, str]:
    """Righe sole nelle celle di una stessa colonna di tabella (stessi bordi a sinistra e a destra): se
    iniziano tutte nello stesso punto la colonna è allineata a sinistra, se finiscono tutte nello stesso
    punto a destra, se hanno lo stesso centro è centrata. Decide anche quando il testo riempie la cella
    (importi nelle colonne strette dei PDF stampati dai browser). Le cornici larghe quanto la pagina non
    sono colonne. id(riga) -> allineamento."""
    columns: list[tuple[tuple[float, float], list[Line]]] = []
    for ln in lines:
        sides = _cell_sides(ln, edges)
        if sides is None or sides[1] - sides[0] > 0.6 * page_width or paragraph_is_rtl(ln.text):
            continue
        for key, members in columns:
            if abs(key[0] - sides[0]) <= 1.0 and abs(key[1] - sides[1]) <= 1.0:
                members.append(ln)
                break
        else:
            columns.append((sides, [ln]))
    out: dict[int, str] = {}
    for _key, members in columns:
        if len(members) < 2:
            continue
        tol = max(1.0, 0.15 * statistics.median(m.size for m in members))
        lefts_eq = _spread([m.bbox.x0 for m in members]) <= tol
        rights_eq = _spread([m.bbox.x1 for m in members]) <= tol
        if lefts_eq and rights_eq:
            continue                     # tutte larghe uguali: non si sa
        if lefts_eq:
            align = ALIGN_LEFT           # anche la più larga, che riempie la cella e sembra centrata
        elif rights_eq:
            align = ALIGN_RIGHT
        elif _spread([(m.bbox.x0 + m.bbox.x1) / 2 for m in members]) <= tol:
            align = ALIGN_CENTER
        else:
            continue
        for m in members:
            out[id(m)] = align
    return out


@dataclass
class PageMargins:
    """Margini del testo della pagina, ricavati dalle righe: bordo sinistro comune, bordi destri
    (testo giustificato, colonne allineate a destra, simmetrico del sinistro) e centri possibili."""

    width: float
    left: float | None = None
    rights: list = field(default_factory=list)
    centers: list = field(default_factory=list)


def _peaks(values: list[float], half: float = 0.6, min_count: int = 3) -> list[tuple[float, int]]:
    """Valori ripetuti: (valore, quante volte compare entro ±`half`), al più uno per zona, i più
    frequenti prima. Finestra fissa: valori sparsi (fine delle righe a sinistra) non si concatenano."""
    vals = sorted(values)
    found = []
    for v in vals:
        n = bisect.bisect_right(vals, v + half) - bisect.bisect_left(vals, v - half)
        if n >= min_count:
            found.append((v, n))
    out: list[tuple[float, int]] = []
    for v, n in sorted(found, key=lambda t: (-t[1], t[0])):
        if all(abs(v - p) > 2 * half for p, _n in out):
            out.append((v, n))
    return out


def page_margins(lines: list[Line], width: float) -> PageMargins:
    horiz = [ln for ln in lines if _horizontal(ln)]
    m = PageMargins(width=width)
    lefts = [(x, n) for x, n in _peaks([ln.bbox.x0 for ln in horiz]) if x < width / 2]
    if lefts:
        m.left = lefts[0][0]
        # testo giustificato: buona parte delle righe che iniziano sul margine sinistro finisce nello stesso punto
        body = [ln for ln in horiz if abs(ln.bbox.x0 - m.left) <= 0.6]
        need = max(3, len(body) // 4)
        justified = [x for x, n in _peaks([ln.bbox.x1 for ln in body]) if n >= need and x > width / 2]
        m.rights.extend(justified)
        m.rights.append(width - m.left)                 # margini simmetrici (impostazione dei programmi)
        m.centers = [(m.left + r) / 2 for r in m.rights if r > m.left]
    # righe allineate a destra (totali, date, colonne di importi): stessa fine, inizi diversi
    for x, _n in _peaks([ln.bbox.x1 for ln in horiz]):
        if x <= width / 2:
            continue
        starts = sorted(ln.bbox.x0 for ln in horiz if abs(ln.bbox.x1 - x) <= 0.6)
        if 1 + sum(1 for a, b in zip(starts, starts[1:]) if b - a > 1.0) >= 3:
            m.rights.append(x)
    m.centers.append(width / 2)
    return m


_LIST_MARKER = re.compile(r"^(?:[•◦▪▫■□●○‣⁃∙·*\-–—➢►✓]|\(?(?:\d{1,3}|[a-zA-Z]|[ivxlcIVXLC]{1,6})[.)°])$")


def _margin_alignment(line: Line, others: list[Line], margins: PageMargins) -> str | None:
    """Allineamento di una riga sola fuori dalle celle, rispetto ai margini del testo della pagina:
    a destra se finisce sul margine destro, centrata se il suo centro è quello della pagina o del testo;
    in entrambi i casi senza toccare il margine sinistro. Nel dubbio None (resta a sinistra): righe con
    altro testo sulla stessa linea di base, voci di elenco, righe che iniziano dove inizia un'altra riga
    (rientri, tabulazioni)."""
    if not line.words or not _horizontal(line) or paragraph_is_rtl(line.text):
        return None
    if _LIST_MARKER.match(line.words[0].text):
        return None
    x0, x1 = line.bbox.x0, line.bbox.x1
    for o in others:
        if o is line or not _horizontal(o):
            continue
        if abs(o.baseline - line.baseline) <= 0.5 or abs(o.bbox.x0 - x0) <= 0.75:
            return None
    size = line.size
    if x0 < (margins.left + 2 * size if margins.left is not None else 0.15 * margins.width):
        return None
    # senza margine sinistro riconosciuto (poche righe) solo il centro esatto della pagina
    tol = max(1.0, 0.08 * size) if margins.left is not None else 0.5
    if any(abs(x1 - r) <= tol for r in margins.rights):
        return ALIGN_RIGHT
    if any(abs((x0 + x1) / 2 - c) <= tol for c in margins.centers):
        return ALIGN_CENTER
    return None


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


def _soft_hyphen_at_end(line: Line, nxt: Line | None) -> bool:
    """La riga finisce con un trattino di sillabazione che nel testo logico diventa un trattino morbido."""
    text = line.text
    first_next = nxt.words[0] if nxt is not None and nxt.words else None
    return (len(text) >= 2 and text.endswith("-") and text[-2].isalpha() and first_next is not None
            and first_next.text[:1].islower())


def line_char_counts(block: TextBlock) -> list[int] | None:
    """Caratteri di ogni riga del blocco contati come in `layout.Composer.line_counts` (senza spazi e
    trattini morbidi): dove va a capo la pagina, per il riquadro di modifica (E-4). None se le righe non
    corrispondono al testo logico del blocco."""
    if block.rotated:
        return None
    out = []
    for i, line in enumerate(block.lines):
        n = sum(1 for c in line.text if c not in NOT_COUNTED)
        nxt = block.lines[i + 1] if i + 1 < len(block.lines) else None
        if nxt is not None and _soft_hyphen_at_end(line, nxt):
            n -= 1
        out.append(n)
    total = sum(1 for p in block.paragraphs for r in p for c in r.text if c not in NOT_COUNTED)
    return out if sum(out) == total else None


def _paragraphs_from_lines(lines: list[Line], align: str, x0: float, width: float) -> list[list[Run]]:
    paragraphs: list[list[Run]] = [[]]
    for i, line in enumerate(lines):
        runs = [Run(r.text, r.style) for r in line.runs]
        if i < len(lines) - 1:
            nxt = lines[i + 1]
            last = runs[-1]
            first_next = nxt.words[0] if nxt.words else None
            if _soft_hyphen_at_end(line, nxt):
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


@dataclass
class PageContext:
    """Quello che serve a `build_block` del resto della pagina: righe visibili, celle, margini."""

    lines: list                                   # righe orizzontali visibili
    margins: PageMargins
    cells: dict = field(default_factory=dict)     # id(riga) -> cella chiusa da bordi (`cell_boxes`)
    columns: dict = field(default_factory=dict)   # id(riga) -> allineamento della colonna di tabella


def _cell_width(lines: list[Line], x0: float, width: float, context: PageContext) -> float:
    """Larghezza di composizione di un paragrafo (a sinistra o giustificato) che occupa da solo una
    cella con bordi: fino al bordo destro meno lo stesso margine interno che ha a sinistra."""
    box = context.cells.get(id(lines[0]))
    if box is None or not all(_same_cell(context.cells.get(id(ln)), box) for ln in lines):
        return width
    own = {id(ln) for ln in lines}
    if any(id(o) not in own and _same_cell(context.cells.get(id(o)), box) for o in context.lines):
        return width            # nella cella c'è altro testo (riquadro di pagina, cornice): non è una cella
    left, _top, right, _bottom = box
    pad = x0 - left
    if pad < 0 or pad > (right - left) / 3:
        return width
    return max(width, right - pad - x0)


def build_block(page_index: int, index: int, lines: list[Line], invisible: bool,
                edges: list | None = None, context: PageContext | None = None) -> TextBlock:
    rotated = not _horizontal(lines[0])
    if rotated:
        align, x0, width, indent = ALIGN_LEFT, lines[0].bbox.x0, lines[0].bbox.width, 0.0
    else:
        align, x0, width, indent = _detect_alignment(lines)
        if len(lines) == 1 and not paragraph_is_rtl(lines[0].text):
            # una riga sola in una cella di tabella (fogli presenze, preventivi esportati da Excel):
            # centrata o a destra come la colonna o la cella, così il testo modificato resta al suo posto
            found = context.columns.get(id(lines[0])) if context is not None else None
            if found is None and edges:
                found = _cell_alignment(lines[0], edges)
            if found is None and context is not None and not invisible:
                # fuori dalle celle: titoli centrati, date e firme a destra rispetto ai margini del testo
                found = _margin_alignment(lines[0], context.lines, context.margins)
            align = found or align
        elif len(lines) > 1 and context is not None and align in (ALIGN_LEFT, ALIGN_JUSTIFY):
            # paragrafo in una cella: va a capo dentro la cella
            width = _cell_width(lines, x0, width, context)
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
    drawings = page_drawings(page)
    borders = page_borders(page, drawings)
    extracted = extract_lines(page, borders[0])
    rules = page_rules(page, drawings) if granularity != GRANULARITY_LINE else []
    edges = page_edges(page, drawings)
    horizontal = [(ln, inv) for ln, inv in extracted if _horizontal(ln)]
    others = [(ln, inv) for ln, inv in extracted if not _horizontal(ln)]
    cells = cell_boxes([ln for ln, _inv in horizontal], borders)
    groups: list[tuple[list[Line], bool]] = []
    for inv in (False, True):
        subset = [ln for ln, i in horizontal if i == inv]
        if granularity == GRANULARITY_LINE:
            groups.extend(([ln], inv) for ln in subset)
        else:
            groups.extend((g, inv) for g in group_lines(subset, rules, cells, borders[1]))
    groups.extend(([ln], inv) for ln, inv in others)
    groups.sort(key=lambda g: (round(g[0][0].bbox.y0 / 4), g[0][0].bbox.x0))
    visible = [ln for ln, inv in horizontal if not inv]
    singles = [g[0] for g, inv in groups if len(g) == 1 and not inv and _horizontal(g[0])]
    context = PageContext(lines=visible, margins=page_margins(visible, page.rect.width), cells=cells,
                          columns=_column_alignments(singles, edges, page.rect.width) if edges else {})
    return [build_block(page_index, i, g, inv, edges, context) for i, (g, inv) in enumerate(groups)]


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


def images_have_text_lines(page: pymupdf.Page) -> bool:
    """Indica se almeno un'immagine della pagina contiene anche solo una riga di testo.

    Righe di testo: fasce orizzontali scure separate da righe chiare, con molte alternanze
    chiaro/scuro lungo la fascia (le lettere); le foto non hanno righe chiare né questo ritmo."""
    try:
        import numpy as np
    except ImportError:
        return True
    try:
        infos = page.get_image_info()
    except Exception:
        return False
    for info in infos:
        r = pymupdf.Rect(info.get("bbox")) & page.rect
        if r.is_empty or r.width < 20 or r.height < 8:
            continue
        zoom = max(1.0, min(4.0, 600.0 / max(r.width, 1.0)))
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r, colorspace=pymupdf.csGRAY, alpha=False)
        except Exception:
            continue
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
        if gray.size == 0 or min(gray.shape) < 8:
            continue
        lo, hi = float(gray.min()), float(gray.max())
        if hi - lo < 60:
            continue   # nessun contrasto
        dark = gray < (lo + hi) / 2
        if dark.mean() > 0.5:
            dark = ~dark   # testo chiaro su fondo scuro
        row_ratio = dark.mean(axis=1)
        inked = row_ratio > 0.01
        if inked.mean() > 0.85:
            continue   # nessuna riga di fondo tra le righe: foto o riempimento
        # fasce di righe con inchiostro
        y = 0
        h = len(inked)
        while y < h:
            if not inked[y]:
                y += 1
                continue
            y0 = y
            while y < h and inked[y]:
                y += 1
            band = dark[y0:y]
            bh = y - y0
            if bh < 4 or bh > 0.6 * h and h > 40:
                continue
            cols = band.any(axis=0)
            width = cols.nonzero()[0]
            if len(width) == 0:
                continue
            span = width[-1] - width[0] + 1
            # alternanze chiaro/scuro lungo la riga centrale della fascia: almeno una ogni ~1,5 altezze
            mid = band[bh // 2]
            changes = int(np.count_nonzero(mid[1:] != mid[:-1]))
            fill = band[:, width[0]:width[-1] + 1].mean()
            if span >= 3 * bh and changes >= max(6, 2 * span / (1.5 * bh)) * 0.5 and 0.08 <= fill <= 0.7:
                return True
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
