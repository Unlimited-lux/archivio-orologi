"""Modello dei blocchi di testo: stili, run, righe e blocchi.

Coordinate in punti PDF della pagina non ruotata, origine in alto a sinistra.
Nessuna dipendenza dall'interfaccia.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import pymupdf

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"
ALIGN_JUSTIFY = "justify"
ALIGNMENTS = (ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT, ALIGN_JUSTIFY)

GRANULARITY_PARAGRAPH = "paragraph"
GRANULARITY_LINE = "line"

SOFT_HYPHEN = "­"


@dataclass(frozen=True)
class Style:
    """Stile di un run di caratteri."""

    font: str = "Helvetica"                 # nome del font (senza prefisso di sottoinsieme)
    size: float = 12.0                      # dimensione in punti
    color: tuple = (0.0, 0.0, 0.0)          # RGB 0..1
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strike: bool = False
    char_spacing: float = 0.0               # spaziatura aggiuntiva tra caratteri, in punti
    rise: float = 0.0                       # spostamento della linea di base (apice/pedice), in punti
    serif: bool = False
    mono: bool = False
    font_xref: int = 0                      # font originale nel PDF (0 = nessuno/nuovo)

    def with_(self, **changes) -> "Style":
        return replace(self, **changes)

    def same_look(self, other: "Style") -> bool:
        return (self.font == other.font and abs(self.size - other.size) < 0.01
                and _same_color(self.color, other.color) and self.bold == other.bold
                and self.italic == other.italic and self.underline == other.underline
                and self.strike == other.strike and abs(self.char_spacing - other.char_spacing) < 0.01
                and abs(self.rise - other.rise) < 0.01)


def _same_color(a, b) -> bool:
    return all(abs(x - y) < 0.004 for x, y in zip(a, b))


@dataclass
class Run:
    text: str
    style: Style


Paragraph = list  # list[Run]


@dataclass
class Word:
    """Parola di una riga originale, con la posizione della sua origine."""

    text: str
    x: float           # origine x del primo carattere
    x1: float          # bordo destro
    style: Style


@dataclass
class Line:
    bbox: pymupdf.Rect
    origin: pymupdf.Point             # origine del primo carattere (linea di base)
    runs: list[Run]
    dir: tuple = (1.0, 0.0)
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def baseline(self) -> float:
        return self.origin.y

    @property
    def size(self) -> float:
        return max((r.style.size for r in self.runs if r.text.strip()), default=self.runs[0].style.size if self.runs else 12)


@dataclass
class TextBlock:
    """Unità di modifica: un paragrafo (o una riga) di testo di pagina."""

    page: int
    index: int
    lines: list[Line]
    paragraphs: list[Paragraph]          # testo logico del blocco: paragrafi di run
    align: str = ALIGN_LEFT
    leading: float = 14.4                # distanza tra linee di base
    width: float = 0.0                   # larghezza del riquadro
    auto_width: bool = True              # una riga sola: si allarga secondo l'allineamento
    indent: float = 0.0                  # rientro della prima riga
    invisible: bool = False              # testo invisibile (livello OCR)
    decodable: bool = True
    rotated: bool = False
    reason: str = ""                     # motivo per cui non è modificabile (vuoto = modificabile)

    @property
    def bbox(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.lines[0].bbox)
        for ln in self.lines[1:]:
            r |= ln.bbox
        return r

    @property
    def x0(self) -> float:
        return min(ln.bbox.x0 for ln in self.lines)

    @property
    def x1(self) -> float:
        return self.x0 + self.width

    @property
    def first_baseline(self) -> float:
        return self.lines[0].baseline

    @property
    def text(self) -> str:
        return paragraphs_text(self.paragraphs)

    @property
    def style(self) -> Style:
        """Stile prevalente (del primo carattere non vuoto)."""
        for para in self.paragraphs:
            for run in para:
                if run.text.strip():
                    return run.style
        return self.lines[0].runs[0].style

    @property
    def editable(self) -> bool:
        return not self.reason


def paragraphs_text(paragraphs: list[Paragraph]) -> str:
    return "\n".join("".join(r.text for r in p) for p in paragraphs).replace(SOFT_HYPHEN, "")


def paragraphs_from_text(text: str, style: Style) -> list[Paragraph]:
    return [[Run(part, style)] if part else [] for part in text.split("\n")]


def merge_runs(runs: list[Run]) -> list[Run]:
    out: list[Run] = []
    for r in runs:
        if not r.text:
            continue
        if out and out[-1].style == r.style:
            out[-1] = Run(out[-1].text + r.text, r.style)
        else:
            out.append(Run(r.text, r.style))
    return out


def color_from_int(value: int) -> tuple:
    return (((value >> 16) & 255) / 255.0, ((value >> 8) & 255) / 255.0, (value & 255) / 255.0)


def color_to_hex(color: tuple) -> str:
    return "#%02X%02X%02X" % tuple(max(0, min(255, round(c * 255))) for c in color[:3])


def color_from_hex(text: str) -> tuple:
    t = text.strip().lstrip("#")
    if len(t) != 6:
        raise ValueError(text)
    return tuple(int(t[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
