"""Operazioni sul testo di pagina, senza interfaccia (TXT-02, 03, 06, 07, 10, 15).

Tutte le funzioni ricevono il documento del motore (`pymupdf.Document`) e vanno
chiamate dentro `Document.change()` (che tiene ENGINE_LOCK e rende l'operazione
annullabile). Le coordinate dei blocchi sono quelle del «riquadro di lavoro»:
la pagina come la vede l'utente (vedi `analysis.frame_matrix`).

Strategia di riscrittura: il vecchio testo del blocco viene rimosso con una
redazione limitata ai soli glifi (immagini e grafica intatte), il nuovo testo
viene composto con i font scelti da `FontResolver` e scritto nella stessa
posizione. Le righe la cui composizione non cambia restano intatte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pymupdf

from ...core.errors import FoliantError
from ...i18n import _
from .analysis import analyze_page, extract_lines, frame_matrix, unframe_matrix
from .fonts import FontResolver, Substitution, UncoveredCharacter
from .layout import Composer, LayoutResult, Piece, PlacedLine
from .model import (
    ALIGN_LEFT, SOFT_HYPHEN, Line, Run, Style, TextBlock, merge_runs, paragraphs_text,
)

ITALIC_SLANT = math.tan(math.radians(12))
MIN_SIZE, MAX_SIZE = 1.0, 1000.0


class TextEditError(FoliantError):
    """Errore con messaggio per l'utente."""


@dataclass
class EditResult:
    bbox: pymupdf.Rect = field(default_factory=pymupdf.Rect)
    substitutions: list[Substitution] = field(default_factory=list)
    changed: bool = False
    overflow: bool = False
    lines_written: int = 0
    lines_kept: int = 0
    orphans: int = 0                      # annotazioni il cui testo non esiste più


# ====================================================================== utilità
def visible_area(page: pymupdf.Page) -> pymupdf.Rect:
    """Area visibile della pagina nel riquadro di lavoro."""
    return pymupdf.Rect(0, 0, page.rect.width, page.rect.height)


def _lin(m: pymupdf.Matrix) -> pymupdf.Matrix:
    return pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)


def _band(line: Line) -> pymupdf.Rect:
    """Fascia interna alla riga: tocca tutti i suoi glifi e nessuno delle righe vicine."""
    size = line.size
    if abs(line.dir[1]) < 0.02:
        y = line.baseline
        x0 = line.bbox.x0 + min(0.3, line.bbox.width / 4)
        x1 = line.bbox.x1 - min(0.3, line.bbox.width / 4)
        return pymupdf.Rect(x0, y - 0.55 * size, x1, y - 0.15 * size)
    r = pymupdf.Rect(line.bbox)
    return pymupdf.Rect(r.x0 + 0.3, r.y0 + 0.3, r.x1 - 0.3, r.y1 - 0.3)


def _lkey(line: Line) -> tuple:
    return (round(line.baseline, 1), round(line.bbox.x0, 1), line.text)


def remove_lines(page: pymupdf.Page, lines: list[Line], relocated_links: list | None = None) -> None:
    """Rimuove i glifi delle righe indicate, senza toccare immagini, grafica, annotazioni e altro testo.

    La redazione toglie ogni glifo che tocca l'area: le righe di altri blocchi sovrapposte
    vengono ripristinate subito dopo, nella stessa posizione."""
    if not lines:
        return
    targets = {_lkey(ln) for ln in lines}
    before = extract_lines(page)
    _redact_lines(page, lines, relocated_links)
    after = extract_lines(page)
    after_keys = {_lkey(ln) for ln, _inv in after}
    before_keys = {_lkey(ln) for ln, _inv in before}
    lost = [(ln, inv) for ln, inv in before if _lkey(ln) not in targets and _lkey(ln) not in after_keys]
    if not lost:
        return
    # resti delle righe danneggiate (glifi non toccati): via anche loro, poi riscrittura completa
    remains = [ln for ln, _inv in after if _lkey(ln) not in before_keys and any(
        abs(ln.baseline - l2.baseline) < 0.5 and ln.bbox.intersects(l2.bbox) for l2, _i in lost)]
    if remains:
        _redact_lines(page, remains)
    resolver = FontResolver(page.parent)
    composer = Composer(resolver)
    writer = _Writer(page)
    for ln, inv in lost:
        for w in ln.words:
            x = w.x
            try:
                pieces = composer.pieces(w.text, w.style)
            except UncoveredCharacter:
                continue
            for part, choice, width in pieces:
                piece = Piece(x, part, w.style, choice, width)
                writer.write_piece(piece, ln.baseline, render_mode=3 if inv else 0)
                x += width


def _redact_lines(page: pymupdf.Page, lines: list[Line], relocated_links: list | None = None) -> None:
    unframe = unframe_matrix(page)
    # le redazioni già presenti (segnate dall'utente) non devono essere applicate ora
    pending = []
    for annot in list(page.annots(types=[pymupdf.PDF_ANNOT_REDACT]) or []):
        pending.append((pymupdf.Rect(annot.rect), dict(annot.info), annot.colors))
    if pending:
        xrefs = [a.xref for a in page.annots(types=[pymupdf.PDF_ANNOT_REDACT])]
        for xref in xrefs:
            a = page.load_annot(xref)
            if a is not None:
                page.delete_annot(a)
    links_before = page.get_links()
    for line in lines:
        band = _band(line) * unframe
        if band.is_empty:
            band = pymupdf.Rect(line.bbox) * unframe
        page.add_redact_annot(band, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    # i collegamenti non vanno persi con la rimozione del testo
    after = page.get_links()
    for link in links_before:
        if relocated_links and any(_same_link(link, other) for other in relocated_links):
            continue   # verrà reinserito nella nuova posizione del testo
        if not any(_same_link(link, other) for other in after):
            clean = {k: v for k, v in link.items() if k not in ("xref", "id")}
            try:
                page.insert_link(clean)
            except Exception:
                pass
    for rect, info, colors in pending:
        a = page.add_redact_annot(rect, fill=colors.get("fill") or False)
        try:
            a.set_info(info)
            a.update()
        except Exception:
            pass


def _same_link(a: dict, b: dict) -> bool:
    return (pymupdf.Rect(a.get("from")) == pymupdf.Rect(b.get("from")) and a.get("kind") == b.get("kind")
            and a.get("uri") == b.get("uri") and a.get("page") == b.get("page"))


# ====================================================================== annotazioni ancorate (TXT-20)
_MARKUP = {pymupdf.PDF_ANNOT_HIGHLIGHT: "add_highlight_annot", pymupdf.PDF_ANNOT_UNDERLINE: "add_underline_annot",
           pymupdf.PDF_ANNOT_STRIKE_OUT: "add_strikeout_annot", pymupdf.PDF_ANNOT_SQUIGGLY: "add_squiggly_annot"}


def _quad_rects(annot) -> list[pymupdf.Rect]:
    v = annot.vertices or []
    return [pymupdf.Quad(v[i:i + 4]).rect for i in range(0, len(v) - 3, 4)]


def collect_anchors(page: pymupdf.Page, bbox: pymupdf.Rect) -> list[dict]:
    """Evidenziazioni (e simili) e collegamenti che coprono testo del riquadro (coordinate di lavoro)."""
    area = pymupdf.Rect(bbox) * unframe_matrix(page)
    out = []
    for annot in page.annots(types=list(_MARKUP)) or []:
        if not annot.rect.intersects(area):
            continue
        text = " ".join(page.get_textbox(r + (-0.5, -0.5, 0.5, 0.5)).strip() for r in _quad_rects(annot)).strip()
        if text:
            out.append({"kind": "annot", "xref": annot.xref, "type": annot.type[0], "text": text})
    for link in page.get_links():
        r = pymupdf.Rect(link["from"])
        if r.intersects(area):
            text = page.get_textbox(r + (-0.5, -0.5, 0.5, 0.5)).strip()
            if text:
                out.append({"kind": "link", "link": link, "text": text})
    return out


def reanchor(page: pymupdf.Page, anchors: list[dict], bbox: pymupdf.Rect) -> int:
    """Riallinea le annotazioni al testo nella nuova posizione; restituisce quante sono rimaste orfane."""
    if not anchors:
        return 0
    area = (pymupdf.Rect(bbox) * unframe_matrix(page)) + (-2, -2, 2, 2)
    orphans = 0
    for a in anchors:
        hits = page.search_for(a["text"], clip=area, quads=True)
        if not hits:
            orphans += 1
            if a["kind"] == "link":
                page.insert_link({k: v for k, v in a["link"].items() if k not in ("xref", "id")})
            continue
        if a["kind"] == "annot":
            old = page.load_annot(a["xref"])
            if old is None:
                continue
            if all(any(abs(q.rect.x0 - r.x0) < 0.5 and abs(q.rect.y0 - r.y0) < 0.5 for r in _quad_rects(old))
                   for q in hits):
                continue
            info, colors, opacity = dict(old.info), old.colors, old.opacity
            new = getattr(page, _MARKUP[a["type"]])(hits)
            try:
                new.set_info(info)
                new.set_colors(stroke=colors.get("stroke"))
                if opacity is not None and 0 <= opacity < 1:
                    new.set_opacity(opacity)
                new.update()
            except Exception:
                pass
            page.delete_annot(old)
        else:
            link = a["link"]
            new_rect = hits[0].rect
            for q in hits[1:]:
                new_rect |= q.rect
            clean = {k: v for k, v in link.items() if k not in ("xref", "id")}
            clean["from"] = new_rect
            page.insert_link(clean)
    return orphans


class _Writer:
    """Scrive righe composte nella pagina."""

    def __init__(self, page: pymupdf.Page, overlay: bool = True):
        self.page = page
        self.overlay = overlay
        self.rot = page.rotation
        self.unframe = unframe_matrix(page)
        self.frame = frame_matrix(page)
        self._fonts: set[str] = set()

    def _font(self, piece: Piece) -> str:
        choice = piece.choice
        if choice.base14:
            return choice.base14
        if choice.key not in self._fonts:
            existing = {f[4] for f in self.page.get_fonts(full=True)}
            if choice.key not in existing:
                self.page.insert_font(fontname=choice.key, fontbuffer=choice.data())
            self._fonts.add(choice.key)
        return choice.key

    def _morph(self, origin: pymupdf.Point, m: pymupdf.Matrix) -> tuple:
        """Trasformazione `m` (riquadro di lavoro) attorno a `origin`, espressa nella pagina non ruotata."""
        mm = _lin(self.frame) * m * _lin(self.unframe)
        return (origin * self.unframe, mm)

    def write_line(self, line: PlacedLine, render_mode: int = 0, hscale: float = 1.0,
                   angle: float = 0.0, origin: pymupdf.Point | None = None) -> None:
        for piece in line.pieces:
            if not piece.text.strip():
                continue
            self.write_piece(piece, line.baseline, render_mode, hscale, angle, origin)

    def write_piece(self, piece: Piece, baseline: float, render_mode: int = 0, hscale: float = 1.0,
                    angle: float = 0.0, origin: pymupdf.Point | None = None) -> None:
        st = piece.style
        fontname = self._font(piece)
        y = baseline - st.rise
        mode = render_mode
        border = 0.05
        if piece.choice.simulate_bold and render_mode == 0:
            mode = 2
            border = 0.035
        m = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
        anchor = origin or pymupdf.Point(piece.x, y)
        if hscale != 1.0:
            m = pymupdf.Matrix(hscale, 0, 0, 1, 0, 0) * m
        if piece.choice.simulate_italic:
            m = pymupdf.Matrix(1, 0, ITALIC_SLANT, 1, 0, 0) * m
        # `morph` del motore usa l'orientamento matematico (y verso l'alto): rotazione con segno opposto
        mm = m * pymupdf.Matrix(-angle) if angle else m
        if angle:
            m = m * pymupdf.Matrix(angle)
        identity = (m.a, m.b, m.c, m.d) == (1, 0, 0, 1)
        chunks: list[tuple[float, str]]
        if st.char_spacing:
            chunks, x = [], piece.x
            for ch in piece.text:
                chunks.append((x, ch))
                x += piece.choice.text_length(ch, st.size) + st.char_spacing
        else:
            chunks = [(piece.x, piece.text)]
        for x, text in chunks:
            if not text.strip():
                continue
            pt = pymupdf.Point(x, y)
            if angle or hscale != 1.0:
                # posizione lungo la linea di base trasformata attorno all'origine della riga
                rel = pymupdf.Point(x - anchor.x, y - anchor.y) * m
                pt = pymupdf.Point(anchor.x + rel.x, anchor.y + rel.y)
            kwargs = dict(fontsize=st.size, fontname=fontname, color=st.color, render_mode=mode,
                          border_width=border, rotate=self.rot, overlay=self.overlay)
            if mode == 2:
                kwargs["fill"] = st.color
            if not identity:
                kwargs["morph"] = self._morph(pt, mm)
            self.page.insert_text(pt * self.unframe, text, **kwargs)
        if (st.underline or st.strike) and render_mode != 3:
            width = max(0.4, st.size * 0.06)
            for flag, offset in ((st.underline, 0.12), (st.strike, -0.28)):
                if not flag:
                    continue
                p0 = pymupdf.Point(piece.x, y + offset * st.size)
                p1 = pymupdf.Point(piece.x + piece.width, y + offset * st.size)
                if angle:
                    p0 = anchor + (p0 - anchor) * pymupdf.Matrix(angle)
                    p1 = anchor + (p1 - anchor) * pymupdf.Matrix(angle)
                self.page.draw_line(p0 * self.unframe, p1 * self.unframe, color=st.color, width=width,
                                   overlay=self.overlay)


# ====================================================================== confronto righe
def _char_styles(runs_or_pieces) -> list[tuple[str, tuple]]:
    out = []
    for item in runs_or_pieces:
        st = item.style
        key = (st.font, round(st.size, 2), tuple(round(c, 3) for c in st.color), st.bold, st.italic,
               st.underline, st.strike, round(st.char_spacing, 2), round(st.rise, 2))
        for ch in item.text:
            if not ch.isspace():
                out.append((ch, key))
    return out


def _same_line(placed: PlacedLine, line: Line) -> bool:
    if abs(placed.baseline - line.baseline) > 0.05:
        return False
    if not placed.pieces:
        return False
    if abs(placed.x0 - line.origin.x) > 0.75:
        return False
    return _char_styles(placed.pieces) == _char_styles(line.runs)


# ====================================================================== operazioni
def _check_sizes(paragraphs: list[list[Run]]) -> None:
    for p in paragraphs:
        for r in p:
            if not (MIN_SIZE <= r.style.size <= MAX_SIZE):
                raise TextEditError(_("La dimensione del carattere deve essere tra 1 e 1000 pt."))


def _layout(page: pymupdf.Page, composer: Composer, block: TextBlock, paragraphs, *, align, leading, width,
            indent, auto_width, dx=0.0, dy=0.0, space_after=0.0) -> LayoutResult:
    base = block.style.size
    try:
        return composer.layout(paragraphs, x0=block.x0 + dx, first_baseline=block.first_baseline + dy, width=width,
                               align=align, leading=leading, indent=indent, auto_width=auto_width,
                               page_rect=visible_area(page), base_size=base, anchor_width=block.width,
                               space_after=space_after)
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» (U+{n:04X}) non è disponibile in nessun font installato: "
                              "non può essere inserito.").format(c=e.char, n=ord(e.char))) from e


def _substitutions(resolver: FontResolver, paragraphs) -> list[Substitution]:
    merged: dict[tuple, Substitution] = {}
    for p in paragraphs:
        for r in p:
            if not r.text.strip():
                continue
            for s in resolver.substitutions(r.text, r.style):
                key = (s.original, s.substitute)
                if key in merged:
                    merged[key].chars += s.chars
                    merged[key].text += s.text
                else:
                    merged[key] = s
    return list(merged.values())


def replace_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, paragraphs: list[list[Run]], *,
                  align: str | None = None, leading: float | None = None, width: float | None = None,
                  indent: float | None = None, dx: float = 0.0, dy: float = 0.0,
                  resolver: FontResolver | None = None, keep_unchanged: bool = True,
                  space_after: float = 0.0) -> EditResult:
    """Sostituisce il testo del blocco con `paragraphs`, ricomponendolo nel riquadro."""
    page = pdf[page_index]
    paragraphs = [merge_runs(p) for p in paragraphs]
    _check_sizes(paragraphs)
    if block.invisible:
        return replace_invisible_block(pdf, page_index, block, paragraphs_text(paragraphs))
    if not paragraphs_text(paragraphs).strip():
        remove_lines(page, block.lines)
        return EditResult(changed=True)
    resolver = resolver or FontResolver(pdf)
    composer = Composer(resolver)
    if block.rotated:
        return _replace_rotated(page, composer, resolver, block, paragraphs, dx, dy)
    auto = block.auto_width if width is None else False
    lay = _layout(page, composer, block, paragraphs, align=align or block.align,
                  leading=leading if leading is not None else block.leading,
                  width=width if width is not None else block.width,
                  indent=indent if indent is not None else block.indent, auto_width=auto, dx=dx, dy=dy,
                  space_after=space_after)
    kept_orig: set[int] = set()
    kept_new: set[int] = set()
    if keep_unchanged and not dx and not dy:
        for i, pl in enumerate(lay.lines):
            if i < len(block.lines) and _same_line(pl, block.lines[i]):
                kept_orig.add(i)
                kept_new.add(i)
    to_remove = [ln for i, ln in enumerate(block.lines) if i not in kept_orig]
    to_write = [pl for i, pl in enumerate(lay.lines) if i not in kept_new]
    if not to_remove and not to_write:
        return EditResult(bbox=block.bbox, changed=False, lines_kept=len(kept_orig))
    anchors = collect_anchors(page, block.bbox)
    remove_lines(page, to_remove, [a["link"] for a in anchors if a["kind"] == "link"])
    writer = _Writer(page)
    for pl in to_write:
        writer.write_line(pl)
    bbox = lay.bbox
    orphans = reanchor(page, anchors, bbox | block.bbox) if anchors else 0
    area = visible_area(page)
    return EditResult(bbox=bbox, substitutions=_substitutions(resolver, paragraphs), changed=True,
                      overflow=not area.contains(bbox), lines_written=len(to_write), lines_kept=len(kept_orig),
                      orphans=orphans)


def _replace_rotated(page, composer, resolver, block, paragraphs, dx, dy) -> EditResult:
    line = block.lines[0]
    angle = math.degrees(math.atan2(line.dir[1], line.dir[0]))
    text_runs = merge_runs([r for p in paragraphs for r in p])
    origin = pymupdf.Point(line.origin.x + dx, line.origin.y + dy)
    try:
        lay = composer.layout([text_runs], x0=origin.x, first_baseline=origin.y, width=1e6, align=ALIGN_LEFT,
                              leading=block.leading)
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» non è disponibile in nessun font installato.").format(c=e.char))
    remove_lines(page, block.lines)
    writer = _Writer(page)
    for pl in lay.lines:
        writer.write_line(pl, angle=angle, origin=origin)
    return EditResult(bbox=block.bbox, substitutions=_substitutions(resolver, [text_runs]), changed=True)


def replace_invisible_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, text: str) -> EditResult:
    """Correzione del livello OCR invisibile: l'immagine non cambia; ogni riga (o parola) corretta
    viene riscalata per coprire la parola corrispondente nell'immagine (TXT-15)."""
    page = pdf[page_index]
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    new_lines = [t.strip() for t in text.replace(SOFT_HYPHEN, "").split("\n")]
    if not "".join(new_lines).strip():
        remove_lines(page, block.lines)
        return EditResult(changed=True)
    # le righe del testo modificato corrispondono alle righe originali
    orig_texts = [" ".join(w.text for w in ln.words) for ln in block.lines]
    if len(new_lines) < len(block.lines) and len(new_lines) == 1 and len(block.lines) > 1:
        # testo riunito in una riga: redistribuisci le parole sulle righe originali
        words = new_lines[0].split()
        counts = [len(ln.words) for ln in block.lines]
        new_lines, k = [], 0
        for i, c in enumerate(counts):
            take = c if i < len(counts) - 1 else len(words) - k
            new_lines.append(" ".join(words[k:k + take]))
            k += take
    writer = _Writer(page)
    to_remove: list[Line] = []
    jobs: list[tuple[Line | None, str, int]] = []
    for i, t in enumerate(new_lines):
        orig = block.lines[i] if i < len(block.lines) else None
        if orig is not None and t == orig_texts[i]:
            continue
        if orig is not None:
            to_remove.append(orig)
        jobs.append((orig, t, i))
    for i in range(len(new_lines), len(block.lines)):
        to_remove.append(block.lines[i])
    if not to_remove and not jobs:
        return EditResult(bbox=block.bbox, changed=False)
    remove_lines(page, to_remove)
    last = block.lines[-1]
    for orig, t, i in jobs:
        if not t:
            continue
        ref = orig or last
        style = ref.runs[0].style
        baseline = ref.baseline if orig is not None else last.baseline + block.leading * (i - len(block.lines) + 1)
        words = t.split()
        if orig is not None and len(words) == len(orig.words):
            # parola per parola: ogni parola copre il riquadro della parola originale
            for w, ow in zip(words, orig.words):
                _write_scaled(writer, composer, w, ow.style, ow.x, ow.x1, baseline)
        else:
            x0 = ref.origin.x
            x1 = ref.bbox.x1 if orig is not None else x0 + composer.width(t, style)
            _write_scaled(writer, composer, t, style, x0, x1, baseline)
    return EditResult(bbox=block.bbox, changed=True)


def _write_scaled(writer: _Writer, composer: Composer, text: str, style: Style, x0: float, x1: float,
                  baseline: float) -> None:
    natural = composer.width(text, style)
    scale = (x1 - x0) / natural if natural > 0 and x1 > x0 else 1.0
    x = x0
    for part, choice, w in composer.pieces(text, style):
        piece = Piece(x, part, style, choice, w)
        writer.write_piece(piece, baseline, render_mode=3, hscale=scale, origin=pymupdf.Point(x0, baseline))
        x += w * scale


def delete_block(pdf: pymupdf.Document, page_index: int, block: TextBlock) -> None:
    remove_lines(pdf[page_index], block.lines)


def _original_placed_lines(block: TextBlock, dx: float, dy: float, resolver: FontResolver,
                           composer: Composer) -> list[PlacedLine]:
    """Righe originali parola per parola nelle posizioni originali (spostamento fedele)."""
    out = []
    for line in block.lines:
        pl = PlacedLine(baseline=line.baseline + dy, size=line.size)
        for w in line.words:
            x = w.x + dx
            for part, choice, width in composer.pieces(w.text, w.style):
                pl.pieces.append(Piece(x, part, w.style, choice, width))
                x += width
        out.append(pl)
    return out


def move_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, dx: float, dy: float,
               target_page: int | None = None) -> EditResult:
    """Sposta il blocco di (dx, dy) punti, eventualmente su un'altra pagina, senza ricomporlo."""
    src = pdf[page_index]
    dst_index = page_index if target_page is None else target_page
    dst = pdf[dst_index]
    bbox = pymupdf.Rect(block.bbox)
    moved = pymupdf.Rect(bbox.x0 + dx, bbox.y0 + dy, bbox.x1 + dx, bbox.y1 + dy)
    if not visible_area(dst).intersects(moved):
        raise TextEditError(_("Il blocco finirebbe fuori dall'area visibile della pagina: spostamento annullato."))
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    try:
        lines = _original_placed_lines(block, dx, dy, resolver, composer)
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» non è disponibile in nessun font installato.").format(c=e.char))
    if block.invisible:
        remove_lines(src, block.lines)
        writer = _Writer(dst)
        for ln, pl in zip(block.lines, lines):
            for p in pl.pieces:
                writer.write_piece(p, pl.baseline, render_mode=3)
        return EditResult(bbox=moved, changed=True)
    anchors = collect_anchors(src, block.bbox) if dst_index == page_index else []
    remove_lines(src, block.lines, [a["link"] for a in anchors if a["kind"] == "link"])
    writer = _Writer(dst)
    if block.rotated:
        line = block.lines[0]
        angle = math.degrees(math.atan2(line.dir[1], line.dir[0]))
        origin = pymupdf.Point(line.origin.x + dx, line.origin.y + dy)
        flat = composer.layout([merge_runs(line.runs)], x0=origin.x, first_baseline=origin.y, width=1e6,
                               align=ALIGN_LEFT, leading=block.leading)
        for pl in flat.lines:
            writer.write_line(pl, angle=angle, origin=origin)
    else:
        for pl in lines:
            writer.write_line(pl)
    subs = _substitutions(resolver, block.paragraphs)
    orphans = reanchor(dst, anchors, moved) if anchors else 0
    return EditResult(bbox=moved, substitutions=subs, changed=True, orphans=orphans)


def resize_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, x0: float, width: float) -> EditResult:
    """Cambia la larghezza del riquadro (il testo rifluisce; il blocco diventa a larghezza fissa)."""
    widest = 0.0
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    for p in block.paragraphs:
        for r in p:
            for word in r.text.split():
                try:
                    widest = max(widest, composer.width(word[:1], r.style))
                except UncoveredCharacter:
                    pass
    width = max(width, widest, 2.0)
    return replace_block(pdf, page_index, block, block.paragraphs, width=width, dx=x0 - block.x0,
                         resolver=resolver, keep_unchanged=False)


def restyle_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, **changes) -> EditResult:
    """Applica proprietà di carattere a tutto il blocco (font, size, color, bold, ...)."""
    if ("bold" in changes or "italic" in changes or "font" in changes) and "font_xref" not in changes:
        changes = dict(changes, font_xref=0)
    paragraphs = [[Run(r.text, r.style.with_(**changes)) for r in p] for p in block.paragraphs]
    return replace_block(pdf, page_index, block, paragraphs)


def add_text(pdf: pymupdf.Document, page_index: int, top_left: pymupdf.Point, paragraphs: list[list[Run]], *,
             width: float | None = None, align: str = ALIGN_LEFT, leading: float | None = None) -> EditResult:
    """Nuovo blocco di testo di pagina con l'angolo superiore sinistro in `top_left` (TXT-06)."""
    page = pdf[page_index]
    paragraphs = [merge_runs(p) for p in paragraphs]
    if not paragraphs_text(paragraphs).strip():
        return EditResult(changed=False)
    _check_sizes(paragraphs)
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    first = next(r.style for p in paragraphs for r in p if r.text.strip())
    try:
        primary = resolver.primary(first)
    except Exception:
        primary = None
    asc = (primary.font.ascender if primary is not None else 0.8) or 0.8
    baseline = top_left.y + asc * first.size
    try:
        lay = composer.layout(paragraphs, x0=top_left.x, first_baseline=baseline,
                              width=width if width else 1.0, align=align,
                              leading=leading if leading else round(first.size * 1.2, 3),
                              auto_width=not width, page_rect=visible_area(page), base_size=first.size,
                              anchor_width=width or 0.0)
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» (U+{n:04X}) non è disponibile in nessun font installato: "
                              "non può essere inserito.").format(c=e.char, n=ord(e.char))) from e
    writer = _Writer(page)
    for pl in lay.lines:
        writer.write_line(pl)
    return EditResult(bbox=lay.bbox, substitutions=_substitutions(resolver, paragraphs), changed=True,
                      overflow=not visible_area(page).contains(lay.bbox), lines_written=len(lay.lines))


# ====================================================================== appunti
def block_to_data(block: TextBlock) -> dict:
    """Rappresentazione serializzabile del blocco (per copia/incolla)."""
    def style_dict(s: Style) -> dict:
        return {"font": s.font, "size": s.size, "color": list(s.color), "bold": s.bold, "italic": s.italic,
                "underline": s.underline, "strike": s.strike, "char_spacing": s.char_spacing, "rise": s.rise,
                "serif": s.serif, "mono": s.mono}
    return {
        "paragraphs": [[{"text": r.text, "style": style_dict(r.style)} for r in p] for p in block.paragraphs],
        "align": block.align, "leading": block.leading, "width": block.width, "auto_width": block.auto_width,
        "indent": block.indent, "x0": block.x0, "top": block.bbox.y0, "page": block.page,
        "first_baseline": block.first_baseline,
    }


def data_paragraphs(data: dict, pdf: pymupdf.Document | None = None) -> list[list[Run]]:
    out = []
    for p in data.get("paragraphs", []):
        runs = []
        for r in p:
            sd = dict(r["style"])
            sd["color"] = tuple(sd.get("color", (0, 0, 0)))
            runs.append(Run(r["text"], Style(**sd)))
        out.append(runs)
    return out


def paste_block(pdf: pymupdf.Document, page_index: int, data: dict, dx: float = 0.0, dy: float = 0.0) -> EditResult:
    """Incolla un blocco copiato (stessa posizione + scostamento)."""
    paragraphs = data_paragraphs(data)
    page = pdf[page_index]
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    base = next((r.style.size for p in paragraphs for r in p if r.text.strip()), 12.0)
    try:
        lay = composer.layout(paragraphs, x0=data["x0"] + dx, first_baseline=data["first_baseline"] + dy,
                              width=data["width"], align=data["align"], leading=data["leading"],
                              indent=data.get("indent", 0.0), auto_width=data.get("auto_width", False),
                              page_rect=visible_area(page), base_size=base, anchor_width=data["width"])
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» non è disponibile in nessun font installato.").format(c=e.char))
    writer = _Writer(page)
    for pl in lay.lines:
        writer.write_line(pl)
    return EditResult(bbox=lay.bbox, substitutions=_substitutions(resolver, paragraphs), changed=True)


def find_block(pdf: pymupdf.Document, page_index: int, bbox: pymupdf.Rect, granularity: str = "paragraph"
               ) -> TextBlock | None:
    """Blocco attuale che occupa (quasi) la stessa area: per ritrovarlo dopo una modifica."""
    best, best_iou = None, 0.0
    for b in analyze_page(pdf[page_index], page_index, granularity):
        inter = pymupdf.Rect(b.bbox) & bbox
        if inter.is_empty:
            continue
        union = pymupdf.Rect(b.bbox) | bbox
        iou = inter.get_area() / max(union.get_area(), 1e-6)
        if iou > best_iou:
            best, best_iou = b, iou
    return best


def convert_invisible_block(pdf: pymupdf.Document, page_index: int, block: TextBlock,
                            background: tuple = (1.0, 1.0, 1.0), color: tuple = (0.0, 0.0, 0.0),
                            family: str = "Liberation Sans") -> EditResult:
    """Converte un blocco OCR invisibile in testo reale visibile (TXT-15): copre il testo
    nell'immagine con il colore dello sfondo e scrive il testo con un font di sistema."""
    if not block.invisible:
        raise TextEditError(_("Il blocco non è testo OCR invisibile."))
    page = pdf[page_index]
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    remove_lines(page, block.lines)
    unframe = unframe_matrix(page)
    cover = pymupdf.Rect(block.bbox)
    pad = max(1.0, 0.08 * block.style.size)
    cover = pymupdf.Rect(cover.x0 - pad, cover.y0 - pad, cover.x1 + pad, cover.y1 + pad)
    page.draw_rect(cover * unframe, color=None, fill=background, width=0, overlay=True)
    writer = _Writer(page)
    for line in block.lines:
        text = " ".join(w.text for w in line.words)
        if not text:
            continue
        base = line.runs[0].style
        style = Style(font=family, size=base.size, color=tuple(color), serif="serif" in family.lower())
        natural = composer.width(text, style)
        target = line.bbox.width
        if natural > 0 and target > 0:
            size = base.size * target / natural
            height_size = line.bbox.height / 1.15
            size = max(min(size, height_size * 1.6), height_size * 0.6, 2.0)
            style = style.with_(size=round(size, 2))
        lay = composer.layout([[Run(text, style)]], x0=line.origin.x, first_baseline=line.baseline, width=1e6,
                              align=ALIGN_LEFT, leading=block.leading)
        for pl in lay.lines:
            writer.write_line(pl)
    return EditResult(bbox=block.bbox, changed=True)


# ====================================================================== disposizione (TXT-08)
ALIGN_MODES = ("left", "hcenter", "right", "top", "vcenter", "bottom")


def align_blocks(pdf: pymupdf.Document, page_index: int, blocks: list[TextBlock], mode: str,
                 reference: pymupdf.Rect | None = None) -> list[pymupdf.Rect]:
    """Allinea i riquadri dei blocchi rispetto alla selezione (o a `reference`, es. la pagina)."""
    if mode not in ALIGN_MODES:
        raise ValueError(mode)
    ref = pymupdf.Rect(reference) if reference is not None else None
    if ref is None:
        ref = pymupdf.Rect(blocks[0].bbox)
        for b in blocks[1:]:
            ref |= b.bbox
    out = []
    # dal basso verso l'alto: la rimozione di un blocco non tocca gli altri
    for b in blocks:
        r = b.bbox
        dx = dy = 0.0
        if mode == "left":
            dx = ref.x0 - r.x0
        elif mode == "right":
            dx = ref.x1 - r.x1
        elif mode == "hcenter":
            dx = (ref.x0 + ref.x1) / 2 - (r.x0 + r.x1) / 2
        elif mode == "top":
            dy = ref.y0 - r.y0
        elif mode == "bottom":
            dy = ref.y1 - r.y1
        else:
            dy = (ref.y0 + ref.y1) / 2 - (r.y0 + r.y1) / 2
        if abs(dx) < 0.01 and abs(dy) < 0.01:
            out.append(pymupdf.Rect(r))
            continue
        res = move_block(pdf, page_index, b, dx, dy)
        out.append(res.bbox)
    return out


def distribute_blocks(pdf: pymupdf.Document, page_index: int, blocks: list[TextBlock], axis: str = "h") -> None:
    """Distribuisce i blocchi con spazi uguali tra i riquadri (orizzontale "h" o verticale "v")."""
    if len(blocks) < 3:
        return
    key = (lambda b: b.bbox.x0) if axis == "h" else (lambda b: b.bbox.y0)
    ordered = sorted(blocks, key=key)
    if axis == "h":
        total = sum(b.bbox.width for b in ordered)
        span = ordered[-1].bbox.x1 - ordered[0].bbox.x0
    else:
        total = sum(b.bbox.height for b in ordered)
        span = ordered[-1].bbox.y1 - ordered[0].bbox.y0
    gap = (span - total) / (len(ordered) - 1)
    pos = (ordered[0].bbox.x1 if axis == "h" else ordered[0].bbox.y1) + gap
    for b in ordered[1:-1]:
        if axis == "h":
            d = pos - b.bbox.x0
            if abs(d) > 0.01:
                move_block(pdf, page_index, b, d, 0)
            pos += b.bbox.width + gap
        else:
            d = pos - b.bbox.y0
            if abs(d) > 0.01:
                move_block(pdf, page_index, b, 0, d)
            pos += b.bbox.height + gap


def rotate_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, degrees: float) -> EditResult:
    """Ruota il blocco attorno all'origine della prima riga; angolo antiorario in gradi (TXT-08)."""
    page = pdf[page_index]
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    origin = pymupdf.Point(block.lines[0].origin)
    current = math.degrees(math.atan2(block.lines[0].dir[1], block.lines[0].dir[0])) if block.rotated else 0.0
    # nelle coordinate della pagina (y verso il basso) un angolo antiorario è negativo
    angle = current - degrees
    try:
        if block.rotated:
            flat = composer.layout([merge_runs(block.lines[0].runs)], x0=origin.x, first_baseline=origin.y,
                                   width=1e6, align=ALIGN_LEFT, leading=block.leading).lines
        else:
            flat = []
            for ln in block.lines:
                flat.extend(composer.layout([merge_runs(ln.runs)], x0=ln.origin.x, first_baseline=ln.baseline,
                                            width=1e6, align=ALIGN_LEFT, leading=block.leading).lines)
    except UncoveredCharacter as e:
        raise TextEditError(_("Il carattere «{c}» non è disponibile in nessun font installato.").format(c=e.char))
    remove_lines(page, block.lines)
    writer = _Writer(page)
    for pl in flat:
        if abs(angle) < 1e-6:
            writer.write_line(pl)
        else:
            writer.write_line(pl, angle=angle, origin=origin)
    return EditResult(bbox=block.bbox, changed=True)


def restack_block(pdf: pymupdf.Document, page_index: int, block: TextBlock, front: bool = True) -> EditResult:
    """Porta il blocco in primo piano (scritto dopo il resto del contenuto) o sullo sfondo (prima)."""
    page = pdf[page_index]
    resolver = FontResolver(pdf)
    composer = Composer(resolver)
    lines = _original_placed_lines(block, 0.0, 0.0, resolver, composer)
    remove_lines(page, block.lines)
    writer = _Writer(page, overlay=front)
    for pl in lines:
        if block.invisible:
            for p in pl.pieces:
                writer.write_piece(p, pl.baseline, render_mode=3)
        else:
            writer.write_line(pl)
    return EditResult(bbox=block.bbox, changed=True)
