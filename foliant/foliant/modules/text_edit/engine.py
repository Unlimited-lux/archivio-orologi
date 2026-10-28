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
from .analysis import analyze_page, frame_matrix, unframe_matrix
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


def remove_lines(page: pymupdf.Page, lines: list[Line]) -> None:
    """Rimuove i glifi delle righe indicate, senza toccare immagini, grafica e annotazioni."""
    if not lines:
        return
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


class _Writer:
    """Scrive righe composte nella pagina."""

    def __init__(self, page: pymupdf.Page):
        self.page = page
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
                          border_width=border, rotate=self.rot)
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
                self.page.draw_line(p0 * self.unframe, p1 * self.unframe, color=st.color, width=width)


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
    remove_lines(page, to_remove)
    writer = _Writer(page)
    for pl in to_write:
        writer.write_line(pl)
    bbox = lay.bbox
    area = visible_area(page)
    return EditResult(bbox=bbox, substitutions=_substitutions(resolver, paragraphs), changed=True,
                      overflow=not area.contains(bbox), lines_written=len(to_write), lines_kept=len(kept_orig))


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
    remove_lines(src, block.lines)
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
    return EditResult(bbox=moved, substitutions=subs, changed=True)


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
