"""Riconoscimento dei blocchi di testo di una pagina (TXT-01).

Raggruppa i glifi estratti dal motore in righe (stessa linea di base) e le
righe in paragrafi (stessa colonna, interlinea regolare, dimensione simile).
"""

from __future__ import annotations

import re
import statistics

import pymupdf

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
    return xrefs.get(normalize_font_name(name)) or xrefs.get("~" + _family_key(name), 0)


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
    runs: list[Run] = []
    chars: list[tuple[str, pymupdf.Point, pymupdf.Rect, Style]] = []
    alphas: list[int] = []
    for span in raw_line.get("spans", []):
        style = _style_from_span(span, xrefs)
        text = "".join(ch["c"] for ch in span.get("chars", []))
        if not text:
            continue
        if text.strip():
            alphas.append(span.get("alpha", 255))
        runs.append(Run(text, style))
        for ch in span.get("chars", []):
            chars.append((ch["c"], pymupdf.Point(ch["origin"]) * frame, pymupdf.Rect(ch["bbox"]) * frame, style))
    # elimina spazi iniziali e finali
    while chars and not chars[0][0].strip():
        chars.pop(0)
    while chars and not chars[-1][0].strip():
        chars.pop()
    if not chars:
        return None
    runs = merge_runs([Run(c[0], c[3]) for c in chars])
    words: list[Word] = []
    current: list = []
    for c in chars + [(" ", None, None, None)]:
        if c[0].strip():
            current.append(c)
        elif current:
            words.append(Word("".join(x[0] for x in current), current[0][1].x, current[-1][2].x1, current[0][3]))
            current = []
    bbox = pymupdf.Rect(chars[0][2])
    for c in chars[1:]:
        bbox |= c[2]
    dx, dy = raw_line.get("dir", (1.0, 0.0))
    fdir = (dx * frame.a + dy * frame.c, dx * frame.b + dy * frame.d)
    line = Line(bbox=bbox, origin=pymupdf.Point(chars[0][1]), runs=runs, dir=fdir, words=words)
    invisible = bool(alphas) and all(a == 0 for a in alphas)
    return line, invisible


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
    return out


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


def group_lines(lines: list[Line]) -> list[list[Line]]:
    """Raggruppa righe orizzontali in paragrafi."""
    ordered = sorted(lines, key=lambda ln: (round(ln.baseline, 1), ln.bbox.x0))
    groups: list[list[Line]] = []
    for line in ordered:
        best, best_d = None, None
        for g in groups:
            d = _compatible(g[-1], line, g)
            if d is not None and (best_d is None or d < best_d):
                # nessun'altra riga già assegnata deve trovarsi tra le due
                best, best_d = g, d
        if best is not None:
            best.append(line)
        else:
            groups.append([line])
    return groups


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
        return ALIGN_LEFT, x0, x1 - x0, 0.0
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
    horizontal = [(ln, inv) for ln, inv in extracted if _horizontal(ln)]
    others = [(ln, inv) for ln, inv in extracted if not _horizontal(ln)]
    groups: list[tuple[list[Line], bool]] = []
    for inv in (False, True):
        subset = [ln for ln, i in horizontal if i == inv]
        if granularity == GRANULARITY_LINE:
            groups.extend(([ln], inv) for ln in subset)
        else:
            groups.extend((g, inv) for g in group_lines(subset))
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
