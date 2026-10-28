"""Composizione del testo nel riquadro (TXT-03): ritorno a capo e allineamento.

Le larghezze vengono misurate con i font reali scelti da `FontResolver`, gli
stessi che verranno scritti nel PDF: ciò che si misura è ciò che si ottiene.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

from .fonts import FontChoice, FontResolver
from .model import ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_RIGHT, SOFT_HYPHEN, Run, Style


@dataclass
class Piece:
    x: float
    text: str
    style: Style
    choice: FontChoice
    width: float


@dataclass
class PlacedLine:
    baseline: float
    pieces: list[Piece] = field(default_factory=list)
    size: float = 12.0

    @property
    def text(self) -> str:
        out, prev_end = [], None
        for p in self.pieces:
            if prev_end is not None and p.x - prev_end > 0.1 * p.style.size and not out[-1].endswith(" ") \
                    and not p.text.startswith(" "):
                out.append(" ")
            out.append(p.text)
            prev_end = p.x + p.width
        return "".join(out).strip()

    @property
    def x0(self) -> float:
        return min((p.x for p in self.pieces if p.text.strip()), default=0.0)

    @property
    def x1(self) -> float:
        return max((p.x + p.width for p in self.pieces if p.text.strip()), default=0.0)


@dataclass
class LayoutResult:
    lines: list[PlacedLine]
    x0: float
    width: float

    @property
    def bbox(self) -> pymupdf.Rect:
        if not self.lines or not any(ln.pieces for ln in self.lines):
            return pymupdf.Rect()
        r = None
        for ln in self.lines:
            for p in ln.pieces:
                asc = p.choice.font.ascender or 0.8
                desc = p.choice.font.descender or -0.2
                pr = pymupdf.Rect(p.x, ln.baseline - asc * p.style.size, p.x + p.width,
                                  ln.baseline - desc * p.style.size)
                r = pr if r is None else r | pr
        return r or pymupdf.Rect()


@dataclass
class _Atom:
    segs: list[tuple[str, Style]]
    space: bool = False
    hyph_after: bool = False      # dopo questa sillaba si può andare a capo con un trattino
    width: float = 0.0


class Composer:
    def __init__(self, resolver: FontResolver):
        self.resolver = resolver
        self._split_cache: dict = {}
        self._primary: dict = {}       # stile -> font principale (scelto sul testo di tutto il blocco)
        self.slack = 0.0               # tolleranza aggiuntiva di riga (blocchi di una sola riga)

    def prepare(self, paragraphs: list[list[Run]]) -> None:
        """Sceglie un font principale per stile in base a tutto il testo che lo usa."""
        samples: dict = {}
        for p in paragraphs:
            for r in p:
                samples[r.style] = samples.get(r.style, "") + r.text
        for st, text in samples.items():
            if st not in self._primary:
                self._primary[st] = self.resolver.primary(st, sample=text)

    def pieces(self, text: str, style: Style) -> list[tuple[str, FontChoice, float]]:
        key = (text, style)
        hit = self._split_cache.get(key)
        if hit is None:
            hit = []
            for part, choice in self.resolver.split(text, style, self._primary.get(style)):
                w = choice.text_length(part, style.size) + style.char_spacing * len(part)
                hit.append((part, choice, w))
            self._split_cache[key] = hit
        return hit

    def width(self, text: str, style: Style) -> float:
        return sum(p[2] for p in self.pieces(text, style))

    # ----------------------------------------------------------- atomi
    def _atoms(self, paragraph: list[Run]) -> list[_Atom]:
        atoms: list[_Atom] = []
        for run in paragraph:
            text = run.text.replace("\t", " ").replace("\n", " ")
            for part in re.split(r"( +)", text):
                if not part:
                    continue
                if part.isspace():
                    if atoms and atoms[-1].space:
                        atoms[-1].segs.append((part, run.style))
                    else:
                        atoms.append(_Atom([(part, run.style)], space=True))
                    continue
                syllables = part.split(SOFT_HYPHEN)
                for k, syl in enumerate(syllables):
                    last = k == len(syllables) - 1
                    if syl:
                        if atoms and not atoms[-1].space and not atoms[-1].hyph_after:
                            atoms[-1].segs.append((syl, run.style))
                        else:
                            atoms.append(_Atom([(syl, run.style)]))
                    if not last and atoms and not atoms[-1].space:
                        atoms[-1].hyph_after = True
        for a in atoms:
            a.width = sum(self.width(t, s) for t, s in a.segs)
        return atoms

    def _split_long(self, atom: _Atom, limit: float) -> tuple[_Atom, _Atom | None]:
        """Spezza una parola più lunga della riga al carattere."""
        chars = [(c, s) for t, s in atom.segs for c in t]
        acc, cut = 0.0, 0
        for i, (c, s) in enumerate(chars):
            w = self.width(c, s)
            if acc + w > limit and i > 0:
                break
            acc += w
            cut = i + 1
        head_chars, tail_chars = chars[:cut], chars[cut:]

        def build(cs):
            segs: list[tuple[str, Style]] = []
            for c, s in cs:
                if segs and segs[-1][1] == s:
                    segs[-1] = (segs[-1][0] + c, s)
                else:
                    segs.append((c, s))
            a = _Atom(segs)
            a.width = sum(self.width(t, s) for t, s in segs)
            return a

        head = build(head_chars)
        tail = build(tail_chars) if tail_chars else None
        if tail is not None:
            tail.hyph_after = atom.hyph_after
        else:
            head.hyph_after = atom.hyph_after
        return head, tail

    def _break(self, atoms: list[_Atom], first_limit: float, limit: float) -> list[tuple[list[_Atom], bool]]:
        """Interruzioni di riga golose. Restituisce [(atomi della riga, termina con trattino)]."""
        lines: list[tuple[list[_Atom], bool]] = []
        cur: list[_Atom] = []
        cur_w = 0.0
        queue = list(atoms)
        while queue:
            a = queue.pop(0)
            lim = first_limit if not lines else limit
            lim += max(0.3, 0.004 * lim) + self.slack   # arrotondamenti delle larghezze nel PDF
            if a.space:
                if cur:
                    cur.append(a)
                    cur_w += a.width
                continue
            has_word = any(not x.space for x in cur)
            if cur_w + a.width <= lim + 0.01 or not has_word:
                if not has_word and a.width > lim + 0.01:
                    head, tail = self._split_long(a, lim - cur_w)
                    cur.append(head)
                    lines.append((cur, False))
                    cur, cur_w = [], 0.0
                    if tail is not None:
                        queue.insert(0, tail)
                    continue
                cur.append(a)
                cur_w += a.width
                continue
            # a capo prima di `a`
            hyphen = bool(cur) and not cur[-1].space and cur[-1].hyph_after
            while cur and cur[-1].space:
                cur.pop()
            lines.append((cur, hyphen))
            cur, cur_w = [], 0.0
            queue.insert(0, a)
        while cur and cur[-1].space:
            cur.pop()
        if cur or not lines:
            lines.append((cur, False))
        return lines

    # ----------------------------------------------------------- composizione
    def layout(self, paragraphs: list[list[Run]], *, x0: float, first_baseline: float, width: float,
               align: str, leading: float, indent: float = 0.0, auto_width: bool = False,
               page_rect: pymupdf.Rect | None = None, base_size: float | None = None,
               space_after: float = 0.0, anchor_width: float | None = None) -> LayoutResult:
        """Compone i paragrafi.

        `x0`/`width`: riquadro; con `auto_width` la larghezza si adatta al testo
        fino al bordo dell'area visibile (`page_rect`), ancorata secondo l'allineamento
        al riquadro originale (`anchor_width`).
        """
        if base_size is None:
            sizes = [r.style.size for p in paragraphs for r in p if r.text.strip()]
            base_size = sizes[0] if sizes else 12.0
        self.prepare(paragraphs)
        para_atoms = [self._atoms(p) for p in paragraphs]
        margin = 0.0
        if auto_width:
            pr = page_rect or pymupdf.Rect(0, 0, x0 + max(width, 1) * 4, 1e6)
            anchor_w = anchor_width if anchor_width is not None else width
            if align == ALIGN_RIGHT:
                limit = (x0 + anchor_w) - pr.x0 - margin
            elif align == ALIGN_CENTER:
                c = x0 + anchor_w / 2
                limit = 2 * min(c - pr.x0, pr.x1 - c) - margin
            else:
                limit = pr.x1 - x0 - margin
            limit = max(limit, 1.0)
            natural = 0.0
            for atoms in para_atoms:
                for line_atoms, hyph in self._break(atoms, limit - indent, limit):
                    natural = max(natural, sum(a.width for a in line_atoms))
            eff = min(max(natural + indent, 0.0), limit)
            if align == ALIGN_RIGHT:
                x0 = x0 + anchor_w - eff
            elif align == ALIGN_CENTER:
                x0 = x0 + anchor_w / 2 - eff / 2
            width = eff
        width = max(width, 1.0)
        out: list[PlacedLine] = []
        baseline = first_baseline
        first = True
        for atoms in para_atoms:
            broken = self._break(atoms, width - indent, width)
            for li, (line_atoms, hyph) in enumerate(broken):
                last_line = li == len(broken) - 1
                sizes = [s.size for a in line_atoms for _t, s in a.segs] or [base_size]
                line_size = max(sizes)
                if not first:
                    step = leading * (line_size / base_size if line_size > base_size else 1.0)
                    baseline += step
                first = False
                pl = PlacedLine(baseline=baseline, size=line_size)
                line_indent = indent if li == 0 else 0.0
                hyph_seg = None
                if hyph and line_atoms:
                    st = line_atoms[-1].segs[-1][1]
                    hyph_seg = ("-", st)
                natural = sum(a.width for a in line_atoms) + (self.width("-", hyph_seg[1]) if hyph_seg else 0.0)
                avail = width - line_indent
                extra = avail - natural
                spaces = [a for a in line_atoms if a.space]
                space_extra = 0.0
                if align == ALIGN_RIGHT:
                    x = x0 + line_indent + extra
                elif align == ALIGN_CENTER:
                    x = x0 + line_indent + extra / 2
                else:
                    x = x0 + line_indent
                    if align == ALIGN_JUSTIFY and not last_line and spaces and extra > 0:
                        space_extra = extra / len(spaces)
                segs: list[tuple[str, Style, bool]] = []
                for a in line_atoms:
                    for t, s in a.segs:
                        segs.append((t, s, a.space))
                if hyph_seg:
                    segs.append((hyph_seg[0], hyph_seg[1], False))
                for t, s, is_space in segs:
                    if is_space:
                        w = self.width(t, s) + space_extra
                        prev = pl.pieces[-1] if pl.pieces else None
                        if (prev is not None and not space_extra and prev.style == s and prev.choice.has_space
                                and abs(prev.x + prev.width - x) < 0.01 and prev.choice.covers(" ")):
                            prev.text += t
                            prev.width += w
                        x += w
                        continue
                    for part, choice, w in self.pieces(t, s):
                        prev = pl.pieces[-1] if pl.pieces else None
                        if prev is not None and prev.style == s and prev.choice is choice and \
                                abs(prev.x + prev.width - x) < 0.01:
                            prev.text += part
                            prev.width += w
                        else:
                            pl.pieces.append(Piece(x, part, s, choice, w))
                        x += w
                for p in pl.pieces:
                    stripped = p.text.rstrip(" ")
                    if stripped != p.text:
                        p.width = self.width(stripped, p.style) if stripped else 0.0
                        p.text = stripped
                pl.pieces = [p for p in pl.pieces if p.text]
                out.append(pl)
            baseline += space_after
        return LayoutResult(out, x0, width)
