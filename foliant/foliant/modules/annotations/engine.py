"""Logica delle annotazioni, senza interfaccia.

Tutte le funzioni lavorano su un `pymupdf.Document` già bloccato dal chiamante
(dentro `Document.change()` per le modifiche, `ENGINE_LOCK` per le letture) e
usano coordinate pagina non ruotate (origine in alto a sinistra, punti).
Le annotazioni create sono annotazioni PDF standard: restano visibili negli
altri lettori e sopravvivono a salvataggio e riapertura.

Le annotazioni si identificano con (indice pagina, xref): lo xref resta stabile
anche dopo annulla/ripeti perché le copie interne non rinumerano gli oggetti.
"""

from __future__ import annotations

import getpass
import math
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

import pymupdf

try:    # testi scritti nei documenti nella lingua dell'interfaccia (NFR-25)
    from foliant.i18n import _
except Exception:  # pragma: no cover - motore usato senza l'applicazione
    def _(text: str) -> str:
        return text

mupdf = pymupdf.mupdf

# --------------------------------------------------------------------- tipi
HIGHLIGHT = "highlight"
UNDERLINE = "underline"
STRIKEOUT = "strikeout"
SQUIGGLY = "squiggly"
NOTE = "note"
FREETEXT = "freetext"
CALLOUT = "callout"
RECT = "rect"
ELLIPSE = "ellipse"
LINE = "line"
ARROW = "arrow"
POLYGON = "polygon"
POLYLINE = "polyline"
CLOUD = "cloud"
INK = "ink"
STAMP = "stamp"
ATTACHMENT = "attachment"
DISTANCE = "distance"
PERIMETER = "perimeter"
AREA = "area"
ANGLE = "angle"
TYPEWRITER = "typewriter"
CARET = "caret"
REPLACE = "replace"

MARKUP_KINDS = (HIGHLIGHT, UNDERLINE, STRIKEOUT, SQUIGGLY)
SHAPE_KINDS = (RECT, ELLIPSE, LINE, ARROW, POLYGON, POLYLINE, CLOUD)
MEASURE_KINDS = (DISTANCE, PERIMETER, AREA, ANGLE)

# Tipi del motore che non sono commenti (non elencati né esportati).
NON_COMMENT_TYPES = {pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_POPUP}

# Colori predefiniti (RGB 0-1) per tipo.
DEFAULT_COLORS = {
    HIGHLIGHT: (1.0, 0.85, 0.0),
    UNDERLINE: (0.05, 0.45, 0.85),
    STRIKEOUT: (0.85, 0.1, 0.1),
    SQUIGGLY: (0.1, 0.6, 0.2),
    NOTE: (1.0, 0.8, 0.1),
    FREETEXT: (0.0, 0.0, 0.0),
    CALLOUT: (0.0, 0.0, 0.0),
    INK: (0.1, 0.25, 0.75),
    STAMP: (0.75, 0.1, 0.1),
    ATTACHMENT: (0.3, 0.3, 0.8),
}
SHAPE_COLOR = (0.85, 0.15, 0.15)
MEASURE_COLOR = (0.05, 0.45, 0.85)

# Stati di revisione (modello «Review» dello standard PDF).
STATE_NONE = "None"
STATE_ACCEPTED = "Accepted"
STATE_REJECTED = "Rejected"
STATE_CANCELLED = "Cancelled"
STATE_COMPLETED = "Completed"
REVIEW_STATES = (STATE_NONE, STATE_ACCEPTED, STATE_REJECTED, STATE_CANCELLED, STATE_COMPLETED)
STATE_TEXTS = {STATE_NONE: "Nessuno", STATE_ACCEPTED: "Accettato", STATE_REJECTED: "Rifiutato",
               STATE_CANCELLED: "Annullato", STATE_COMPLETED: "Completato"}

DASH_STYLES = {"solid": None, "dashed": [6, 3], "dotted": [1, 2]}


# Chiave privata (seconda classe) con i dati dei timbri creati da Foliant.
STAMP_KEY = "FoliantStamp"


class AnnotationError(Exception):
    """Errore con messaggio per l'utente."""


def default_author() -> str:
    """Nome completo dell'account di sistema (o nome utente)."""
    try:
        import pwd

        gecos = pwd.getpwuid(__import__("os").getuid()).pw_gecos.split(",")[0].strip()
        if gecos:
            return gecos
    except Exception:
        pass
    try:
        return getpass.getuser()
    except Exception:
        return ""


# Soggetto predefinito = nome del tipo di strumento (ANN-02).
SUBJECTS = {
    HIGHLIGHT: "Evidenziazione", UNDERLINE: "Sottolineatura", STRIKEOUT: "Barrato", SQUIGGLY: "Ondulato",
    NOTE: "Nota", FREETEXT: "Casella di testo", CALLOUT: "Richiamo", RECT: "Rettangolo", ELLIPSE: "Ellisse",
    LINE: "Linea", ARROW: "Freccia", POLYGON: "Poligono", POLYLINE: "Polilinea", CLOUD: "Nuvola",
    INK: "Disegno a mano libera", STAMP: "Timbro", ATTACHMENT: "Allegato", DISTANCE: "Distanza",
    PERIMETER: "Perimetro", AREA: "Area", ANGLE: "Angolo", TYPEWRITER: "Commento di testo", CARET: "Inserimento",
    REPLACE: "Sostituzione",
}


def pdf_now() -> str:
    """Data corrente nel formato delle date PDF."""
    now = datetime.now().astimezone()
    off = now.utcoffset() or timezone.utc.utcoffset(None)
    minutes = int(off.total_seconds() // 60) if off else 0
    sign = "+" if minutes >= 0 else "-"
    minutes = abs(minutes)
    return now.strftime("D:%Y%m%d%H%M%S") + f"{sign}{minutes // 60:02d}'{minutes % 60:02d}'"


_DATE_RE = re.compile(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?'?")


def parse_pdf_date(value: str | None) -> datetime | None:
    if not value:
        return None
    m = _DATE_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) if g else dflt for g, dflt in zip(m.groups()[:6], (0, 1, 1, 0, 0, 0)))
    try:
        dt = datetime(y, mo, d, h, mi, s)
    except ValueError:
        return None
    sign = m.group(7)
    if sign in ("+", "-"):
        from datetime import timedelta
        off = timedelta(hours=int(m.group(8) or 0), minutes=int(m.group(9) or 0))
        dt = dt.replace(tzinfo=timezone(off if sign == "+" else -off))
    elif sign in ("Z", "z"):
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _english() -> bool:
    try:
        from foliant import i18n
        return i18n.language() == "en"
    except Exception:  # pragma: no cover - motore usato senza l'applicazione
        return False


def local_date(when: datetime) -> str:
    """Data nella lingua dell'interfaccia (anno a 4 cifre): 28/10/2026 o 10/28/2026 (NFR-26)."""
    return when.strftime("%m/%d/%Y" if _english() else "%d/%m/%Y")


def local_time(when: datetime) -> str:
    """Ora nella lingua dell'interfaccia: 14:05 o 2:05 PM."""
    if _english():
        return f"{when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"
    return when.strftime("%H:%M")


def format_date(value: str | None) -> str:
    dt = parse_pdf_date(value)
    if dt is None:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return f"{local_date(dt)} {local_time(dt)}"


# ----------------------------------------------------------------- stile
@dataclass
class Style:
    """Aspetto di un'annotazione. Colori RGB 0-1; None = nessuno."""

    color: tuple | None = None
    fill: tuple | None = None
    opacity: float = 1.0
    width: float = 1.5
    dash: str = "solid"          # solid / dashed / dotted
    cloud: bool = False
    fontsize: float = 12.0
    text_color: tuple | None = None
    align: int = 0               # 0 sinistra, 1 centro, 2 destra
    autofit: bool = True         # caselle di testo: altezza adattata al testo
    icon: str = "Comment"        # icona delle note

    def copy(self, **changes) -> "Style":
        data = dict(self.__dict__)
        data.update(changes)
        return Style(**data)


def _rgb(c) -> tuple | None:
    if c is None:
        return None
    c = tuple(float(v) for v in c)
    if len(c) == 1:
        return (c[0], c[0], c[0])
    if len(c) == 4:  # CMYK -> RGB
        cc, mm, yy, kk = c
        return ((1 - cc) * (1 - kk), (1 - mm) * (1 - kk), (1 - yy) * (1 - kk))
    return c[:3]


def color_to_hex(c) -> str:
    c = _rgb(c)
    if c is None:
        return ""
    return "#" + "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in c)


def hex_to_color(h: str | None) -> tuple | None:
    if not h:
        return None
    h = h.strip().lstrip("#")
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


# ----------------------------------------------------------------- utilità motore
@contextmanager
def unrotated(page: pymupdf.Page) -> Iterator[pymupdf.Page]:
    """Le funzioni di basso livello lavorano nello spazio pagina visualizzato:
    togliamo temporaneamente la rotazione così da usare coordinate non ruotate."""
    rot = page.rotation
    if rot:
        page.set_rotation(0)
    try:
        yield page
    finally:
        if rot:
            page.set_rotation(rot)


def iter_annots(page: pymupdf.Page, types: Sequence[int] | None = None) -> Iterator[pymupdf.Annot]:
    """Annotazioni della pagina in costo lineare (``page.annots()`` del motore è quadratico)."""
    annot = page.first_annot
    while annot is not None:
        current, annot = annot, annot.next
        if types is None or current.type[0] in types:
            yield current


def _silent(pdf: pymupdf.Document, annot: pymupdf.Annot) -> bool:
    """Annotazione che non si vede né si seleziona: nascosta, oppure risposta o stato (/IRT senza
    /RT /Group). Nei PDF/A le risposte non possono essere nascoste (ANN-01): hanno un aspetto vuoto."""
    if annot.flags & pymupdf.PDF_ANNOT_IS_HIDDEN:
        return True
    if not annot.irt_xref:
        return False
    return pdf.xref_get_key(annot.xref, "RT")[1] != "/Group"


def find_annot(page: pymupdf.Page, xref: int) -> pymupdf.Annot | None:
    """Annotazione con questo xref nella pagina, oppure None."""
    try:
        return page.load_annot(xref)
    except Exception:
        return None


def get_annot(page: pymupdf.Page, xref: int) -> pymupdf.Annot:
    annot = find_annot(page, xref)
    if annot is None:
        raise AnnotationError("L'annotazione non esiste più.")
    return annot


def _placement():
    try:
        from foliant.core import placement
        return placement
    except Exception:   # pragma: no cover - nucleo senza il modulo di posizionamento
        return None


_MATRIX_CACHE: dict | None = None


@contextmanager
def bulk_geometry() -> Iterator[None]:
    """Durante un'operazione su molte annotazioni (importazione, esportazione) le matrici di
    `placement` sono calcolate una volta per pagina e rotazione invece che a ogni punto."""
    global _MATRIX_CACHE
    outer = _MATRIX_CACHE is not None
    if not outer:
        _MATRIX_CACHE = {}
    try:
        yield
    finally:
        if not outer:
            _MATRIX_CACHE = None


def _page_matrix(page: pymupdf.Page, to_pdf: bool):
    pl = _placement()
    if pl is None:
        return None
    if _MATRIX_CACHE is None:
        return pl.page_to_pdf_matrix(page) if to_pdf else pl.pdf_to_page_matrix(page)
    key = (id(page.parent), page.number, page.rotation, to_pdf)
    m = _MATRIX_CACHE.get(key)
    if m is None:
        m = pl.page_to_pdf_matrix(page) if to_pdf else pl.pdf_to_page_matrix(page)
        _MATRIX_CACHE[key] = m
    return m


def to_pdf_point(page: pymupdf.Page, x: float, y: float) -> tuple[float, float]:
    """Coordinate standard (area visibile, y verso il basso) → spazio utente PDF."""
    m = _page_matrix(page, True)
    if m is not None:
        p = pymupdf.Point(x, y) * m
        return p.x, p.y
    mb = page.mediabox
    return x + mb.x0, mb.y1 - y


def from_pdf_point(page: pymupdf.Page, x: float, y: float) -> tuple[float, float]:
    m = _page_matrix(page, False)
    if m is not None:
        p = pymupdf.Point(x, y) * m
        return p.x, p.y
    mb = page.mediabox
    return x - mb.x0, mb.y1 - y


def display_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """Coordinate standard → pagina visualizzata (ruotata come nella vista)."""
    pl = _placement()
    return pl.page_to_display_matrix(page) if pl is not None else page.rotation_matrix


def to_pdf_rect(page: pymupdf.Page, r) -> tuple[float, float, float, float]:
    r = pymupdf.Rect(r)
    x0, y1 = to_pdf_point(page, r.x0, r.y0)
    x1, y0 = to_pdf_point(page, r.x1, r.y1)
    return x0, y0, x1, y1


def from_pdf_rect(page: pymupdf.Page, r: Sequence[float]) -> pymupdf.Rect:
    x0, y0, x1, y1 = r
    a = from_pdf_point(page, x0, y0)
    b = from_pdf_point(page, x1, y1)
    return pymupdf.Rect(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _fz_point(p) -> "mupdf.FzPoint":
    return mupdf.FzPoint(float(p[0]), float(p[1]))


def new_name() -> str:
    return str(uuid.uuid4())


def _finish(annot: pymupdf.Annot, author: str | None, contents: str | None = None,
            subject: str | None = None, date: str | None = None, kind: str = "") -> int:
    """Completa una nuova annotazione: autore, soggetto, date uguali, /NM univoco."""
    now = date or pdf_now()
    info = {"title": author if author is not None else default_author(), "creationDate": now, "modDate": now}
    if contents is not None:
        info["content"] = contents
    subject = subject if subject is not None else SUBJECTS.get(kind)
    if subject:
        info["subject"] = _(subject)
    annot.set_info(**info)
    annot.update()
    doc = annot.get_parent().parent
    doc.xref_set_key(annot.xref, "NM", _pdf_string(new_name()))
    return annot.xref


def _apply_style(annot: pymupdf.Annot, style: Style, *, stroke=True, fill=False) -> None:
    colors = {}
    if stroke and style.color is not None:
        colors["stroke"] = _rgb(style.color)
    if fill:
        colors["fill"] = _rgb(style.fill) if style.fill is not None else None
    if colors:
        annot.set_colors(**{k: v for k, v in colors.items() if v is not None})
    dashes = DASH_STYLES.get(style.dash)
    try:
        annot.set_border(width=max(0.0, float(style.width)), dashes=dashes or [],
                         clouds=1 if style.cloud else 0)
    except Exception:
        pass
    annot.set_opacity(max(0.05, min(1.0, float(style.opacity))))


def touch(annot: pymupdf.Annot) -> None:
    annot.set_info(modDate=pdf_now())


def _dirty_update(annot: pymupdf.Annot) -> None:
    mupdf.pdf_dirty_annot(annot.this)
    annot.update()


# ------------------------------------------------------------------ creazione
def quads_from_words(words: Iterable[tuple]) -> list[pymupdf.Quad]:
    """Unisce le parole della stessa riga in un quadrilatero per riga."""
    lines: dict[tuple, pymupdf.Rect] = {}
    order: list[tuple] = []
    for w in words:
        key = (w[5], w[6]) if len(w) > 6 else (len(order), 0)
        r = pymupdf.Rect(w[:4])
        if key in lines:
            lines[key] |= r
        else:
            lines[key] = r
            order.append(key)
    return [lines[k].quad for k in order]


def create_markup(pdf: pymupdf.Document, page_no: int, kind: str, quads: Sequence, style: Style,
                  author: str | None = None, contents: str = "", area: bool = False) -> int:
    if kind not in MARKUP_KINDS:
        raise AnnotationError("Tipo di evidenziazione sconosciuto.")
    quads = [q if isinstance(q, pymupdf.Quad) else pymupdf.Rect(q).quad for q in quads]
    quads = [q for q in quads if not q.rect.is_empty]
    if not quads:
        raise AnnotationError("Nessun testo selezionato.")
    page = pdf[page_no]
    fn = {HIGHLIGHT: page.add_highlight_annot, UNDERLINE: page.add_underline_annot,
          STRIKEOUT: page.add_strikeout_annot, SQUIGGLY: page.add_squiggly_annot}[kind]
    annot = fn(quads=quads)
    if annot is None:
        raise AnnotationError("Impossibile creare l'evidenziazione.")
    annot.set_colors(stroke=_rgb(style.color or DEFAULT_COLORS[kind]))
    annot.set_opacity(max(0.0, min(1.0, style.opacity)))
    xref = _finish(annot, author, contents, kind=kind)
    if area:
        pdf.xref_set_key(xref, AREA_KEY, "true")
        _fit_area_rect(pdf, page, xref, quads[0].rect if len(quads) == 1 else None)
    if contents:
        ensure_popup(pdf, page_no, xref)
    return xref


AREA_KEY = "FoliantArea"


def _fit_area_rect(pdf: pymupdf.Document, page: pymupdf.Page, xref: int, rect=None) -> None:
    """Evidenziazione ad area: /Rect coincide con il quadrilatero (ANN-11)."""
    if rect is not None:
        r = pymupdf.Rect(rect)
        pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, r)))
        return
    annot = get_annot(page, xref)
    v = annot.vertices or []
    if len(v) == 4:
        r = pymupdf.Rect(min(p[0] for p in v), min(p[1] for p in v), max(p[0] for p in v), max(p[1] for p in v))
        pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, r)))


def is_area_markup(pdf: pymupdf.Document, xref: int) -> bool:
    return pdf.xref_get_key(xref, AREA_KEY)[1] == "true"


def marked_text(page: pymupdf.Page, annot: pymupdf.Annot, words: list | None = None) -> str:
    """Testo coperto dai quadrilateri di un markup (non memorizzato nel file)."""
    verts = annot.vertices or []
    if words is None:
        try:
            words = page.get_text("words")
        except Exception:
            words = []
    parts = []
    for i in range(0, len(verts) - 3, 4):
        r = pymupdf.Quad(*verts[i:i + 4]).rect
        # si restringe in verticale per non prendere le righe vicine
        h = r.height
        r = pymupdf.Rect(r.x0, r.y0 + h * 0.2, r.x1, r.y1 - h * 0.2)
        found = []
        for w in words:
            if w[2] > r.x0 and w[0] < r.x1 and w[3] > r.y0 and w[1] < r.y1:
                # parola coperta per almeno metà della larghezza
                cover = min(w[2], r.x1) - max(w[0], r.x0)
                if cover >= (w[2] - w[0]) * 0.5:
                    found.append(w[4])
        parts.append(" ".join(found))
    return " ".join(p for p in parts if p).strip()


def open_popups(pdf: pymupdf.Document, page_no: int) -> list[tuple[pymupdf.Rect, str, str, str]]:
    """Popup aperte della pagina: (riquadro, autore, data, testo). Costo lineare: si leggono
    solo le chiavi /Popup e /Open dal dizionario (chiamata a ogni ridisegno)."""
    page = pdf[page_no]
    out = []
    for xref, atype, _nm in page.annot_xrefs():
        if atype in NON_COMMENT_TYPES:
            continue
        k, v = pdf.xref_get_key(xref, "Popup")
        if k != "xref":
            continue
        pop = int(v.split()[0])
        if pdf.xref_get_key(pop, "Open")[1] != "true":
            continue
        k_f, v_f = pdf.xref_get_key(xref, "F")
        if k_f == "int" and int(v_f) & pymupdf.PDF_ANNOT_IS_HIDDEN:
            continue
        a = find_annot(page, xref)
        if a is None:
            continue
        try:
            info = a.info or {}
            out.append((pymupdf.Rect(a.popup_rect), info.get("title", ""),
                        format_date(info.get("modDate") or info.get("creationDate")), info.get("content", "")))
        except Exception:
            continue
    return out


def set_popup_open(pdf: pymupdf.Document, page_no: int, xref: int, open_: bool) -> None:
    ensure_popup(pdf, page_no, xref, open_)


def ensure_popup(pdf: pymupdf.Document, page_no: int, xref: int, open_: bool | None = None) -> None:
    """Crea (se manca) la finestra popup collegata all'annotazione (ANN-05)."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    if annot.type[0] in (pymupdf.PDF_ANNOT_FREE_TEXT, pymupdf.PDF_ANNOT_POPUP) or \
            annot.type[0] in NON_COMMENT_TYPES:
        return
    if not annot.popup_xref:
        raw = _array(pdf, xref, "Rect")
        r = from_pdf_rect(page, raw) if raw and len(raw) == 4 else annot.rect
        pr = page.rect if not page.rotation else pymupdf.Rect(0, 0, page.mediabox.width, page.mediabox.height)
        x0 = min(r.x1 + 8, pr.x1 - 200)
        y0 = max(pr.y0, min(r.y0, pr.y1 - 120))
        with unrotated(page):
            annot.set_popup(pymupdf.Rect(x0, y0, x0 + 200, y0 + 120))
        annot = page.load_annot(xref)
    if open_ is not None:
        annot.set_open(bool(open_))


NOTE_ICONS = ["Comment", "Note", "Key", "Help", "Paragraph", "NewParagraph", "Insert"]
NOTE_SIZE = 20.0


def create_note(pdf: pymupdf.Document, page_no: int, point, text: str, style: Style,
                author: str | None = None, icon: str | None = None) -> int:
    page = pdf[page_no]
    p = pymupdf.Point(point)
    icon = icon or style.icon or "Comment"
    if icon not in NOTE_ICONS:
        icon = "Note"
    with unrotated(page):
        annot = page.add_text_annot(p, text, icon=icon)
        h = NOTE_SIZE / 2
        annot.set_rect(pymupdf.Rect(p.x - h, p.y - h, p.x + h, p.y + h))
    annot.set_flags(pymupdf.PDF_ANNOT_IS_PRINT | pymupdf.PDF_ANNOT_IS_NO_ZOOM | pymupdf.PDF_ANNOT_IS_NO_ROTATE)
    if page.rotation:
        # /NoRotate: l'icona resta diritta ruotando attorno all'angolo superiore sinistro di /Rect,
        # quindi su una pagina ruotata cade fuori da /Rect. Si trasla /Rect perché l'icona
        # visualizzata (annot.rect del motore) sia centrata sul punto.
        xref = annot.xref
        shown = pymupdf.Rect(page.load_annot(xref).rect)
        dx, dy = p.x - (shown.x0 + shown.x1) / 2, p.y - (shown.y0 + shown.y1) / 2
        with unrotated(page):
            annot = page.load_annot(xref)
            annot.set_rect(pymupdf.Rect(p.x - h + dx, p.y - h + dy, p.x + h + dx, p.y + h + dy))
        annot = page.load_annot(xref)
    annot.set_colors(stroke=_rgb(style.color or DEFAULT_COLORS[NOTE]))
    annot.set_opacity(max(0.0, min(1.0, style.opacity)))
    xref = _finish(annot, author, text, kind=NOTE)
    ensure_popup(pdf, page_no, xref)
    return xref


def create_freetext(pdf: pymupdf.Document, page_no: int, rect, text: str, style: Style,
                    author: str | None = None, callout: Sequence | None = None) -> int:
    page = pdf[page_no]
    rect = pymupdf.Rect(rect).normalize()
    if rect.width < 8 or rect.height < 8:
        rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + 180, rect.y0 + max(28, style.fontsize * 2.2))
    kwargs = dict(fontsize=max(4.0, float(style.fontsize)),
                  text_color=_rgb(style.text_color or style.color or (0, 0, 0)),
                  fill_color=_rgb(style.fill),
                  border_width=float(style.width) if style.width > 0 else 0,
                  opacity=max(0.0, min(1.0, style.opacity)),
                  align=int(style.align) if style.align in (0, 1, 2) else 0,
                  fontname="Helv")
    if style.dash in ("dashed", "dotted"):
        kwargs["dashes"] = DASH_STYLES[style.dash]
    if callout:
        pts = [pymupdf.Point(p) for p in callout]
        kwargs["callout"] = pts
        kwargs["line_end"] = pymupdf.PDF_ANNOT_LE_OPEN_ARROW
    with unrotated(page):
        annot = page.add_freetext_annot(rect, text, **kwargs)
        if not callout:
            pdf.xref_set_key(annot.xref, "CL", "null")
        else:
            # il motore sottrae l'origine del /CropBox invece di sommarla: /CL, /Rect e /RD
            # vengono riscritti con le conversioni di `placement`
            _set_callout(pdf, page, annot.xref, rect, kwargs["callout"])
        annot = page.load_annot(annot.xref)
    xref = _finish(annot, author, text, kind=CALLOUT if callout else FREETEXT)
    if style.autofit and not callout:
        pdf.xref_set_key(xref, AUTOFIT_KEY, "true")
    if style.width > 0 and style.color is not None:
        pdf.xref_set_key(xref, FT_BORDER_KEY, "[{:.3f} {:.3f} {:.3f}]".format(*_rgb(style.color)))
    refresh_freetext(pdf, page_no, xref)
    return xref


FT_BORDER_KEY = "FoliantBorder"
AUTOFIT_KEY = "FoliantAutoFit"


def _array(pdf: pymupdf.Document, xref: int, key: str) -> list[float] | None:
    k, v = pdf.xref_get_key(xref, key)
    if k != "array":
        return None
    try:
        return [float(x) for x in v.strip("[]").split()]
    except ValueError:
        return None


def _da(pdf: pymupdf.Document, xref: int) -> tuple[str, float, tuple]:
    k, v = pdf.xref_get_key(xref, "DA")
    da = _decode_text_string(v) if k == "string" else "0 g /Helv 12 Tf"
    m = re.search(r"/(\S+)\s+([\d.]+)\s+Tf", da)
    font = m.group(1) if m else "Helv"
    size = float(m.group(2)) if m else 12.0
    cm = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da)
    gm = re.search(r"(?<![\d.])([\d.]+)\s+g\b", da)
    color = tuple(float(x) for x in cm.groups()) if cm else ((float(gm.group(1)),) * 3 if gm else (0.0, 0.0, 0.0))
    return font, size or 12.0, color


def _wrap(text: str, width: float, size: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            cand = w if not cur else cur + " " + w
            if pymupdf.get_text_length(cand, fontname="helv", fontsize=size) <= width or not cur:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def _wrap_runs(paras: list, width: float, size: float) -> list[list]:
    """A capo automatico per paragrafi formattati: righe come elenchi di tratti."""
    lines: list[list] = []
    for para in paras:
        words: list[tuple] = []          # (parola, stile..., spazio_dopo)
        for t, bb, ii, uu, cc in para:
            parts = re.split(r"(\s+)", t)
            for part in parts:
                if not part:
                    continue
                if part.isspace():
                    if words:
                        words[-1] = words[-1][:5] + (True,)
                    continue
                words.append((part, bb, ii, uu, cc, False))
        cur: list = []
        cur_w = 0.0
        for wtxt, bb, ii, uu, cc, sp in words:
            code = _FT_FONTS[(bb, ii)][1]
            ww = pymupdf.get_text_length(wtxt, fontname=code, fontsize=size)
            sw = pymupdf.get_text_length(" ", fontname=code, fontsize=size)
            if cur and cur_w + ww > width:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append((wtxt + (" " if sp else ""), bb, ii, uu, cc))
            cur_w += ww + (sw if sp else 0)
        lines.append(cur)
    return lines


def refresh_freetext(pdf: pymupdf.Document, page_no: int, xref: int) -> None:
    """Aspetto vettoriale di caselle di testo e richiami: riempimento, bordo (colore proprio),
    testo a capo con allineamento, linea di richiamo con punta. Per testi fuori dalla codifica
    dei caratteri standard o pagine ruotate si usa l'aspetto generato dal motore."""
    page = pdf[page_no]
    rc = pdf.xref_get_key(xref, "RC")
    try:
        _refresh_freetext(pdf, page, page_no, xref)
        if not page.rotation % 360:
            keep_rotation(pdf, page_no, xref)
    finally:
        if rc[0] == "string" and pdf.xref_get_key(xref, "RC")[0] != "string":
            pdf.xref_set_key(xref, "RC", pymupdf.get_pdf_str(rc[1]))


# Caratteri standard per stile: (grassetto, corsivo) -> (nome risorsa, codice per le misure)
_FT_FONTS = {(False, False): ("Helv", "helv", "Helvetica"), (True, False): ("HeBo", "hebo", "Helvetica-Bold"),
             (False, True): ("HeIt", "heit", "Helvetica-Oblique"), (True, True): ("HeBI", "hebi", "Helvetica-BoldOblique")}


def rich_runs(xhtml: str) -> list[list[tuple[str, bool, bool, bool, tuple | None]]]:
    """Paragrafi di /RC come elenchi di tratti (testo, grassetto, corsivo, sottolineato, colore)."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.paras: list[list] = []
            self.stack: list[dict] = [{}]

        def handle_starttag(self, tag, attrs):
            st = dict(self.stack[-1])
            a = dict(attrs)
            css = a.get("style", "") or ""
            if tag in ("b", "strong") or re.search(r"font-weight\s*:\s*(bold|[6-9]00)", css):
                st["b"] = True
            if tag in ("i", "em") or re.search(r"font-style\s*:\s*italic", css):
                st["i"] = True
            if tag == "u" or "underline" in css:
                st["u"] = True
            m = re.search(r"color\s*:\s*#([0-9a-fA-F]{6})", css)
            if m:
                st["c"] = hex_to_color(m.group(1))
            if tag == "p" or not self.paras:
                self.paras.append([])
            if tag == "br":
                self.paras.append([])
            self.stack.append(st)

        def handle_endtag(self, tag):
            if len(self.stack) > 1:
                self.stack.pop()

        def handle_data(self, data):
            if not self.paras:
                self.paras.append([])
            st = self.stack[-1]
            if data:
                self.paras[-1].append((data, bool(st.get("b")), bool(st.get("i")), bool(st.get("u")), st.get("c")))

    p = P()
    try:
        p.feed(xhtml)
    except Exception:
        return []
    return [para for para in p.paras]


def _refresh_freetext(pdf: pymupdf.Document, page: pymupdf.Page, page_no: int, xref: int) -> None:
    annot = get_annot(page, xref)
    text = (annot.info or {}).get("content", "")
    try:
        text.encode("cp1252")
        custom = True
    except UnicodeEncodeError:
        custom = False
    rot = page.rotation % 360
    cl0 = _array(pdf, xref, "CL")
    if rot and cl0 and _intent(pdf, xref) == "FreeTextCallout":
        custom = False      # richiamo su pagina ruotata: aspetto del motore
    if not custom:
        _dirty_update(annot)
        return
    _font, size, tcolor = _da(pdf, xref)
    fill = _array(pdf, xref, "C")
    border = _array(pdf, xref, FT_BORDER_KEY)
    bs = annot.border or {}
    bw = float(bs.get("width", 0) or 0) if bs.get("width", -1) >= 0 else 0.0
    dashes = bs.get("dashes") or []
    q = pdf.xref_get_key(xref, "Q")
    align = int(q[1]) if q[0] == "int" else 0
    rect = pymupdf.Rect(annot.rect)
    inner = _freetext_inner(page, annot)
    _ang, base = _rotation(pdf, xref)
    if base is not None and not rot:
        # casella ruotata: si disegna alla dimensione originale, la rotazione è nella /Matrix
        c = _std_rect(pdf, page, xref)
        cx, cy = (c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2
        rect = pymupdf.Rect(cx - base[0] / 2, cy - base[1] / 2, cx + base[0] / 2, cy + base[1] / 2)
        inner = pymupdf.Rect(rect)
    real_rect = None
    if rot:
        # pagina ruotata: si disegna nell'orientamento della vista e si ruota l'aspetto (/Matrix)
        raw = _array(pdf, xref, "Rect")
        real_rect = from_pdf_rect(page, raw) if raw and len(raw) == 4 else rect
        w_, h_ = (real_rect.height, real_rect.width) if rot in (90, 270) else (real_rect.width, real_rect.height)
        rect = pymupdf.Rect(0, 0, w_, h_)
        inner = pymupdf.Rect(rect)
    cl = _array(pdf, xref, "CL")
    pts = []
    if cl and _intent(pdf, xref) == "FreeTextCallout":
        pts = [pymupdf.Point(*from_pdf_point(page, cl[i], cl[i + 1])) for i in range(0, len(cl) - 1, 2)]
    opacity = annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0
    H = rect.height

    def loc(p: pymupdf.Point) -> tuple[float, float]:
        return p.x - rect.x0, H - (p.y - rect.y0)

    ops = ["/GS0 gs"]
    x0, y1 = loc(inner.tl)
    x1, y0 = loc(inner.br)
    if fill:
        r, g, b = _rgb(fill)
        ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg {x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f} re f")
    stroke = _rgb(border) if border else (0.0, 0.0, 0.0)
    if bw > 0 and border:
        r, g, b = stroke
        dash = "[" + " ".join(f"{d:g}" for d in dashes) + "] 0 d " if dashes else ""
        ops.append(f"{dash}{r:.3f} {g:.3f} {b:.3f} RG {bw:.2f} w "
                   f"{x0 + bw / 2:.2f} {y0 + bw / 2:.2f} {x1 - x0 - bw:.2f} {y1 - y0 - bw:.2f} re S [] 0 d")
    if len(pts) >= 2:
        r, g, b = stroke if border else _rgb(tcolor)
        lw = max(bw, 1.0)
        path = " ".join(f"{loc(p)[0]:.2f} {loc(p)[1]:.2f} {'m' if i == 0 else 'l'}" for i, p in enumerate(pts))
        tip, nxt = pts[0], pts[1]
        ang = math.atan2(nxt.y - tip.y, nxt.x - tip.x)
        head = 6 + 2 * lw
        wing = []
        for da_ in (0.45, -0.45):
            wp = pymupdf.Point(tip.x + head * math.cos(ang + da_), tip.y + head * math.sin(ang + da_))
            wing.append(loc(wp))
        tx, ty = loc(tip)
        ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG {lw:.2f} w 1 J 1 j {path} S "
                   f"{wing[0][0]:.2f} {wing[0][1]:.2f} m {tx:.2f} {ty:.2f} l {wing[1][0]:.2f} {wing[1][1]:.2f} l S")
    pad = 2 + bw
    avail = max(10.0, inner.width - 2 * pad)
    rich = get_rich_text(pdf, xref)
    runs = rich_runs(rich) if rich else []
    if runs and "".join(t for para in runs for t, *_r in para).replace("\n", "").strip() != \
            text.replace("\n", "").strip():
        runs = []          # /RC discordante da /Contents: si usa /Contents
    lines = _wrap(text, avail, size) if not runs else _wrap_runs(runs, avail, size)
    if pdf.xref_get_key(xref, AUTOFIT_KEY)[1] == "true" and not pts and not rot and base is None:
        need = 2 * pad + size * 1.2 * len(lines) + size * 0.2
        if need > inner.height + 0.5:
            # adattamento automatico: si allunga la casella verso il basso e si ridisegna
            grown = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1 + (need - inner.height))
            pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, grown)))
            return _refresh_freetext(pdf, page, page_no, xref)
    r, g, b = _rgb(tcolor)
    lead = size * 1.2
    y = y1 - pad - size * 0.9
    ops.append(f"BT /Helv {size:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg")
    underlines = []
    for line in lines:
        if y < y0 + pad - size * 0.3:
            break
        segs = line if isinstance(line, list) else [(line, False, False, False, None)]
        w = sum(pymupdf.get_text_length(t, fontname=_FT_FONTS[(bb, ii)][1], fontsize=size) for t, bb, ii, _u, _c in segs)
        x = x0 + pad
        if align == 1:
            x = x0 + (inner.width - w) / 2
        elif align == 2:
            x = x1 - pad - w
        for t, bb, ii, uu, cc in segs:
            res, code, _base = _FT_FONTS[(bb, ii)]
            cr, cg, cb = _rgb(cc) if cc else (r, g, b)
            ops.append(f"/{res} {size:.2f} Tf {cr:.3f} {cg:.3f} {cb:.3f} rg 1 0 0 1 {x:.2f} {y:.2f} Tm "
                       f"{_pdf_string(t)} Tj")
            tw = pymupdf.get_text_length(t, fontname=code, fontsize=size)
            if uu:
                underlines.append((x, y - size * 0.12, tw, (cr, cg, cb)))
            x += tw
        y -= lead
    ops.append("ET")
    for ux, uy, uw, (cr, cg, cb) in underlines:
        ops.append(f"{cr:.3f} {cg:.3f} {cb:.3f} RG {max(0.5, size / 16):.2f} w {ux:.2f} {uy:.2f} m {ux + uw:.2f} {uy:.2f} l S")
    fonts = []
    for (res, _code, base) in _FT_FONTS.values():
        fx = pdf.get_new_xref()
        pdf.update_object(fx, f"<</Type/Font/Subtype/Type1/BaseFont/{base}/Encoding/WinAnsiEncoding>>")
        fonts.append(f"/{res} {fx} 0 R")
    ap = pdf.get_new_xref()
    pdf.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[0 0 {rect.width:.2f} {H:.2f}]/Matrix[1 0 0 1 0 0]"
                          f"/Resources<</Font<<{''.join(fonts)}>>"
                          f"/ExtGState<</GS0<</Type/ExtGState/CA {opacity:.3f}/ca {opacity:.3f}>>>>>>>>")
    pdf.update_stream(ap, "\n".join(ops).encode("latin-1"))
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
    if real_rect is not None:
        a = math.radians(rot)
        c, s_ = round(math.cos(a)), round(math.sin(a))
        pdf.xref_set_key(ap, "Matrix", f"[{c} {s_} {-s_} {c} 0 0]")
        pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, real_rect)))


def create_typewriter(pdf: pymupdf.Document, page_no: int, point, text: str, style: Style,
                      author: str | None = None) -> int | None:
    """Commento di testo sulla pagina, senza bordo né sfondo (ANN-15). Testo vuoto: nulla."""
    if not text.strip():
        return None
    size = max(4.0, float(style.fontsize))
    lines = text.split("\n")
    try:
        width = max(pymupdf.get_text_length(ln, fontname="helv", fontsize=size) for ln in lines) + 8
    except Exception:
        width = size * max(len(ln) for ln in lines) * 0.6 + 8
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x, p.y, p.x + max(width, 20), p.y + size * 1.2 * len(lines) + 6)
    st = style.copy(fill=None, width=0, color=None, text_color=style.text_color or style.color or (0, 0, 0))
    xref = create_freetext(pdf, page_no, rect, text, st, author)
    pdf.xref_set_key(xref, "IT", "/FreeTextTypeWriter")
    page = pdf[page_no]
    annot = get_annot(page, xref)
    annot.set_info(subject=_(SUBJECTS[TYPEWRITER]))
    annot.update()
    refresh_freetext(pdf, page_no, xref)
    return xref


def _new_caret(pdf: pymupdf.Document, page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Annot:
    with unrotated(page):
        a = mupdf.pdf_create_annot(page._pdf_page(), mupdf.PDF_ANNOT_CARET)
        mupdf.pdf_set_annot_rect(a, mupdf.fz_make_rect(rect.x0, rect.y0, rect.x1, rect.y1))
        mupdf.pdf_update_annot(a)
        xref = mupdf.pdf_to_num(mupdf.pdf_annot_obj(a))
    return get_annot(page, xref)


def create_caret(pdf: pymupdf.Document, page_no: int, point, text: str, style: Style,
                 author: str | None = None, paragraph: bool = False, height: float = 12.0) -> int:
    """Segno di inserimento (^) centrato sul punto, con il testo da inserire (ANN-13)."""
    page = pdf[page_no]
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x - 4, p.y - height / 2, p.x + 4, p.y + height / 2)
    annot = _new_caret(pdf, page, rect)
    annot.set_colors(stroke=_rgb(style.color or (0.1, 0.3, 0.8)))
    xref = _finish(annot, author, text, kind=CARET)
    pdf.xref_set_key(xref, "Sy", "/P" if paragraph else "/None")
    return xref


def create_replacement(pdf: pymupdf.Document, page_no: int, quads: Sequence, text: str, style: Style,
                       author: str | None = None) -> int:
    """Sostituzione: barrato sul testo + segno di inserimento alla fine, raggruppati (ANN-13).
    Restituisce lo xref del segno di inserimento (la voce principale)."""
    quads = [q if isinstance(q, pymupdf.Quad) else pymupdf.Rect(q).quad for q in quads]
    if not quads:
        raise AnnotationError("Nessun testo selezionato.")
    last = quads[-1].rect
    caret = create_caret(pdf, page_no, (last.x1, (last.y0 + last.y1) / 2), text, style, author,
                         height=max(6.0, last.height))
    strike = create_markup(pdf, page_no, STRIKEOUT, quads, style.copy(color=style.color or (0.85, 0.1, 0.1)), author)
    page = pdf[page_no]
    annot = get_annot(page, strike)
    annot.set_irt_xref(caret)
    pdf.xref_set_key(strike, "RT", "/Group")
    annot = get_annot(page, caret)
    annot.set_info(subject=_(SUBJECTS[REPLACE]))
    annot.update()
    return caret


def create_shape(pdf: pymupdf.Document, page_no: int, kind: str, points: Sequence, style: Style,
                 author: str | None = None, contents: str = "") -> int:
    """Forme: RECT/ELLIPSE ricevono due punti opposti; LINE/ARROW due estremi;
    POLYGON/POLYLINE/CLOUD i vertici."""
    page = pdf[page_no]
    pts = [pymupdf.Point(p) for p in points]
    if kind in (RECT, ELLIPSE):
        if len(pts) < 2:
            raise AnnotationError("Forma troppo piccola.")
        r = pymupdf.Rect(pts[0], pts[1]).normalize()
        if r.width < 2 or r.height < 2:
            raise AnnotationError("Forma troppo piccola.")
        annot = page.add_rect_annot(r) if kind == RECT else page.add_circle_annot(r)
        _apply_style(annot, style.copy(color=style.color or SHAPE_COLOR), fill=True)
    elif kind in (LINE, ARROW):
        if len(pts) < 2 or abs(pts[0] - pts[1]) < 2:
            raise AnnotationError("Linea troppo corta.")
        annot = page.add_line_annot(pts[0], pts[-1])
        _apply_style(annot, style.copy(color=style.color or SHAPE_COLOR, cloud=False), fill=style.fill is not None)
        if kind == ARROW:
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        if contents:
            try:
                mupdf.pdf_set_annot_line_caption(annot.this, 1)
            except Exception:
                pass
    elif kind in (POLYGON, POLYLINE, CLOUD):
        need = 2 if kind == POLYLINE else 3
        if len(pts) < need:
            raise AnnotationError("Servono almeno {n} punti.".replace("{n}", str(need)))
        if kind == POLYLINE:
            annot = page.add_polyline_annot(pts)
        else:
            annot = page.add_polygon_annot(pts)
        st = style.copy(color=style.color or SHAPE_COLOR, cloud=style.cloud or kind == CLOUD)
        _apply_style(annot, st, fill=kind != POLYLINE)
    else:
        raise AnnotationError("Forma sconosciuta.")
    xref = _finish(annot, author, contents, kind=kind)
    if kind == ARROW:
        pdf.xref_set_key(xref, "IT", "/LineArrow")
    elif kind == CLOUD:
        pdf.xref_set_key(xref, "IT", "/PolygonCloud")
    return xref


INK_TOLERANCE = 0.5
INK_MAX_POINTS = 2000


def simplify(points: Sequence, tolerance: float = INK_TOLERANCE) -> list[tuple[float, float]]:
    """Semplificazione di Ramer-Douglas-Peucker (scarto massimo `tolerance`)."""
    pts = [pymupdf.Point(p) for p in points]
    if len(pts) < 3:
        return [(p.x, p.y) for p in pts]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            d = _dist_to_segment(pts[i], pts[a], pts[b])
            if d > best:
                best, idx = d, i
        if idx >= 0 and best > tolerance:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [(p.x, p.y) for p, k in zip(pts, keep) if k]


def _clean_strokes(strokes: Sequence[Sequence]) -> list[list[tuple[float, float]]]:
    out = []
    for s in strokes:
        pts = [tuple(map(float, p[:2])) for p in s]
        if not pts:
            continue
        if len(pts) == 1 or all(abs(pymupdf.Point(p) - pymupdf.Point(pts[0])) < 0.01 for p in pts):
            # tocco senza movimento: un punto visibile
            pts = [pts[0], (pts[0][0] + 0.01, pts[0][1] + 0.01)]
        else:
            pts = simplify(pts)
        out.append(pts)
    total = sum(len(s) for s in out)
    tol = INK_TOLERANCE
    while total > INK_MAX_POINTS and tol < 50:
        tol *= 1.5
        out = [simplify(s, tol) for s in out]
        total = sum(len(s) for s in out)
    return out


def create_ink(pdf: pymupdf.Document, page_no: int, strokes: Sequence[Sequence], style: Style,
               author: str | None = None, marker: bool = False) -> int:
    """Disegno a mano libera; con `marker` è un pennarello evidenziatore (fusione moltiplicativa)."""
    strokes = _clean_strokes(strokes)
    if not strokes:
        raise AnnotationError("Disegno vuoto.")
    page = pdf[page_no]
    with unrotated(page):
        annot = page.add_ink_annot(strokes)
    _apply_style(annot, style.copy(color=style.color or DEFAULT_COLORS[INK], cloud=False, dash="solid"))
    if marker:
        annot.set_blendmode(pymupdf.PDF_BM_Multiply)
    return _finish(annot, author, "", kind=INK)


def add_ink_strokes(pdf: pymupdf.Document, page_no: int, xref: int, strokes: Sequence[Sequence]) -> None:
    """Aggiunge tratti a un disegno esistente (tratti consecutivi = un'unica annotazione)."""
    page = pdf[page_no]
    new = _clean_strokes(strokes)
    if not new:
        return
    with unrotated(page):
        annot = get_annot(page, xref)
        old = [[pymupdf.Point(v) for v in s] for s in annot.vertices or []]
        _set_ink(annot, old + [[pymupdf.Point(p) for p in s] for s in new])
        touch(annot)
        _dirty_update(annot)


# ------------------------------------------------------------------ misure
@dataclass
class Scale:
    """Scala di misura: `factor` unità reali per punto, `unit` unità reale."""

    unit: str = "mm"
    factor: float = 25.4 / 72
    label: str = ""
    calibrated: bool = False
    all_pages: bool = True       # ambito scelto nella finestra «Scala»

    @classmethod
    def from_ratio(cls, paper: float, paper_unit: str, real: float, real_unit: str) -> "Scale":
        """Es. 1 cm = 2 m → from_ratio(1, "cm", 2, "m")."""
        if paper <= 0 or real <= 0:
            raise AnnotationError("I valori della scala devono essere maggiori di zero.")
        pts = paper * PAGE_UNITS.get(paper_unit, 1.0)
        return cls(real_unit, real / pts, f"{_num(paper)} {paper_unit} = {_num(real)} {real_unit}", True)

    @classmethod
    def calibrate(cls, points: float, real: float, real_unit: str) -> "Scale":
        if points <= 0 or real <= 0:
            raise AnnotationError("La lunghezza di calibrazione deve essere maggiore di zero.")
        return cls(real_unit, real / points, f"{_num(points)} pt = {_num(real)} {real_unit}", True)

    def length(self, points: float) -> float:
        return points * self.factor

    def area(self, points2: float) -> float:
        return points2 * self.factor * self.factor

    def text(self) -> str:
        return self.label or f"1 pt = {_num(self.factor)} {self.unit}"


PAGE_UNITS = {"pt": 1.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "in": 72.0}
REAL_UNITS = ["mm", "cm", "m", "km", "in", "ft", "yd", "mi", "pt"]


def _num(v: float) -> str:
    return f"{v:g}".replace(".", ",")


def polyline_length(points: Sequence) -> float:
    pts = [pymupdf.Point(p) for p in points]
    return sum(abs(pts[i + 1] - pts[i]) for i in range(len(pts) - 1))


def polygon_area(points: Sequence) -> float:
    pts = [pymupdf.Point(p) for p in points]
    s = 0.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        s += a.x * b.y - b.x * a.y
    return abs(s) / 2


def format_measure(value: float, unit: str, square: bool = False) -> str:
    """Valore con due decimali e virgola decimale (lingua italiana)."""
    txt = f"{value:.2f}".replace(".", ",")
    return f"{txt} {unit}²" if square else f"{txt} {unit}"


def angle_degrees(points: Sequence) -> float:
    """Angolo in gradi (0-180) al secondo vertice fra i lati verso il primo e il terzo."""
    a, b, c = (pymupdf.Point(p) for p in points[:3])
    u, v = a - b, c - b
    nu, nv = abs(u), abs(v)
    if nu == 0 or nv == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (u.x * v.x + u.y * v.y) / (nu * nv)))
    return math.degrees(math.acos(cos))


def measure_number(kind: str, points: Sequence, scale: Scale) -> float:
    """Valore numerico di una misura (unità reali; gradi per l'angolo)."""
    if kind == DISTANCE:
        return scale.length(polyline_length(points[:2]))
    if kind == PERIMETER:
        return scale.length(polyline_length(points))
    if kind == AREA:
        return scale.area(polygon_area(points))
    if kind == ANGLE:
        return angle_degrees(points)
    raise AnnotationError("Misura sconosciuta.")


def measure_value(kind: str, points: Sequence, scale: Scale) -> str:
    if kind == ANGLE:
        if len(points) < 3:
            raise AnnotationError("Servono almeno 3 punti.")
        return f"{angle_degrees(points):.1f}°".replace(".", ",")
    if kind == DISTANCE:
        return format_measure(scale.length(polyline_length(points[:2])), scale.unit)
    if kind == PERIMETER:
        return format_measure(scale.length(polyline_length(points)), scale.unit)
    if kind == AREA:
        return format_measure(scale.area(polygon_area(points)), scale.unit, square=True)
    raise AnnotationError("Misura sconosciuta.")


def _measure_dict(scale: Scale, angle: bool = False) -> str:
    unit = scale.unit
    num = f"<</Type/NumberFormat/U{_pdf_string(unit)}/C {scale.factor:.10g}/D 100>>"
    area = f"<</Type/NumberFormat/U{_pdf_string(unit + '2')}/C {scale.factor * scale.factor:.10g}/D 100>>"
    # angolo: formato angolare /T (gradi) del dizionario di misura (ANN-33)
    ang = f"/T[<</Type/NumberFormat/U{_pdf_string('°')}/C 1/D 10>>]" if angle else ""
    return f"<</Type/Measure/Subtype/RL/R{_pdf_string(scale.text())}/X[{num}]/D[{num}]/A[{area}]{ang}>>"


def scale_of(pdf: pymupdf.Document, xref: int) -> Scale | None:
    """Scala memorizzata in una misura (`/Measure`)."""
    k, v = pdf.xref_get_key(xref, "Measure/D")
    if k != "array":
        k, v = pdf.xref_get_key(xref, "Measure/X")
    if k != "array":
        return None
    m = re.search(r"/C\s+([\d.eE+-]+)", v)
    u = re.search(r"/U\s*\(([^)]*)\)", v)
    if not m:
        return None
    r = pdf.xref_get_key(xref, "Measure/R")
    return Scale(u.group(1) if u else "", float(m.group(1)),
                 _unescape_pdf_string(r[1]) if r[0] == "string" else "", True)


def viewport_scale(pdf: pymupdf.Document, page_no: int, point) -> Scale | None:
    """Scala dell'area di misura /VP della pagina che contiene il punto (ISO 32000-2 §12.9):
    si usa l'ultima area dell'elenco che contiene il punto."""
    page = pdf[page_no]
    k, v = pdf.xref_get_key(page.xref, "VP")
    if k == "null":
        return None
    if k == "xref":
        v = pdf.xref_object(int(v.split()[0]), compressed=True)
    # riferimenti indiretti alle singole aree: si sostituiscono con il loro contenuto
    v = re.sub(r"(\d+)\s+0\s+R", lambda m: pdf.xref_object(int(m.group(1)), compressed=True), v)
    p = pymupdf.Point(point)
    found = None
    for part in v.split("/BBox")[1:]:
        bm = re.match(r"\s*\[([^\]]*)\]", part)
        if not bm:
            continue
        try:
            bb = [float(x) for x in bm.group(1).split()]
        except ValueError:
            continue
        if len(bb) != 4 or not from_pdf_rect(page, bb).contains(p):
            continue
        xm = re.search(r"/X\s*\[(.*?)\]", part, re.S)
        src = xm.group(1) if xm else part
        cm = re.search(r"/C\s+([\d.eE+-]+)", src)
        um = re.search(r"/U\s*\(([^)]*)\)", src)
        rm = re.search(r"/R\s*\(([^)]*)\)", part)
        if cm:
            found = Scale(um.group(1) if um else "", float(cm.group(1)), rm.group(1) if rm else "", True)
    return found


def update_measure(pdf: pymupdf.Document, page_no: int, xref: int) -> None:
    """Ricalcola il valore di una misura dalla sua geometria e dalla sua scala."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    kind = kind_of(pdf, annot)
    if kind not in MEASURE_KINDS:
        return
    scale = scale_of(pdf, xref) or Scale()
    value = measure_value(kind, [pymupdf.Point(v) for v in annot.vertices or []], scale)
    annot.set_info(content=value, modDate=pdf_now())
    annot.update()


def create_measure(pdf: pymupdf.Document, page_no: int, kind: str, points: Sequence, style: Style,
                   scale: Scale, author: str | None = None) -> int:
    page = pdf[page_no]
    pts = [pymupdf.Point(p) for p in points]
    value = measure_value(kind, pts, scale)
    st = style.copy(color=style.color or MEASURE_COLOR, cloud=False)
    if kind == DISTANCE:
        if len(pts) < 2 or abs(pts[0] - pts[1]) < 1:
            raise AnnotationError("Linea troppo corta.")
        annot = page.add_line_annot(pts[0], pts[1])
        _apply_style(annot, st)
        annot.set_line_ends(pymupdf.PDF_ANNOT_LE_BUTT, pymupdf.PDF_ANNOT_LE_BUTT)
        intent = "LineDimension"
    elif kind == PERIMETER:
        if len(pts) < 2:
            raise AnnotationError("Servono almeno 2 punti.")
        annot = page.add_polyline_annot(pts)
        _apply_style(annot, st)
        intent = "PolyLineDimension"
    elif kind == AREA:
        if len(pts) < 3:
            raise AnnotationError("Servono almeno 3 punti.")
        annot = page.add_polygon_annot(pts)
        _apply_style(annot, st.copy(fill=st.fill), fill=st.fill is not None)
        intent = "PolygonDimension"
    elif kind == ANGLE:
        if len(pts) < 3:
            raise AnnotationError("Servono almeno 3 punti.")
        annot = page.add_polyline_annot(pts[:3])        # polilinea di tre vertici (ANN-33)
        _apply_style(annot, st)
        intent = "PolyLineDimension"
    else:
        raise AnnotationError("Misura sconosciuta.")
    pdf.xref_set_key(annot.xref, "IT", "/" + intent)
    pdf.xref_set_key(annot.xref, "Measure", _measure_dict(scale, angle=kind == ANGLE))
    if kind == DISTANCE:
        try:
            mupdf.pdf_set_annot_line_caption(annot.this, 1)
        except Exception:
            pass
    return _finish(annot, author, value, kind=kind)


CONVERTIBLE = {LINE: DISTANCE, ARROW: DISTANCE, POLYLINE: PERIMETER, POLYGON: AREA, CLOUD: AREA, RECT: AREA}


def convert_to_measure(pdf: pymupdf.Document, page_no: int, xref: int, scale: Scale) -> int:
    """«Converti in misura» (ANN-33): linea → /IT /LineDimension, polilinea → /PolyLineDimension,
    poligono → /PolygonDimension, con /Measure e il valore come testo. Un rettangolo diventa un
    poligono di misura dell'area con lo stesso aspetto. Restituisce l'xref della misura."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    kind = kind_of(pdf, annot)
    target = CONVERTIBLE.get(kind)
    if target is None:
        raise AnnotationError("Questa annotazione non si può convertire in misura.")
    if kind == RECT:
        info = annot.info or {}
        colors = annot.colors or {}
        border = annot.border or {}
        r = _freetext_inner(page, annot)           # riquadro della forma: /Rect meno /RD
        style = Style(color=_rgb(colors.get("stroke")) if colors.get("stroke") else MEASURE_COLOR,
                      fill=_rgb(colors.get("fill")) if colors.get("fill") else None,
                      opacity=annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1.0,
                      width=float(border.get("width", 1) if border.get("width", -1) >= 0 else 1))
        new = create_measure(pdf, page_no, AREA, [r.tl, r.tr, r.br, r.bl], style, scale, info.get("title", ""))
        delete_annot(pdf, page_no, xref, force=True)
        return new
    intent = {DISTANCE: "LineDimension", PERIMETER: "PolyLineDimension", AREA: "PolygonDimension"}[target]
    pts = [pymupdf.Point(v) for v in annot.vertices or []]
    value = measure_value(target, pts, scale)
    pdf.xref_set_key(xref, "IT", "/" + intent)
    pdf.xref_set_key(xref, "Measure", _measure_dict(scale))
    if kind == CLOUD:
        pdf.xref_set_key(xref, "BE", "null")
    annot = get_annot(page, xref)
    if target == DISTANCE:
        try:
            mupdf.pdf_set_annot_line_caption(annot.this, 1)
        except Exception:
            pass
    annot.set_info(content=value, modDate=pdf_now())
    _dirty_update(annot)
    return xref


# ------------------------------------------------------------------ aggancio (ANN-33)
SNAP_END = "estremo"
SNAP_MID = "punto medio"
SNAP_CROSS = "intersezione"
SNAP_PATH = "tracciato"
SNAP_ANNOT = "annotazione"
SNAP_KINDS = (SNAP_END, SNAP_MID, SNAP_CROSS, SNAP_PATH, SNAP_ANNOT)
MAX_SNAP_SEGMENTS = 20000      # oltre: solo estremi e punti medi
MAX_CROSS_SEGMENTS = 3000      # oltre: niente intersezioni (costo quadratico anche con la griglia)


@dataclass
class SnapTargets:
    """Punti di aggancio di una pagina, in coordinate standard."""
    points: list = field(default_factory=list)          # [(Point, tipo)]
    segments: list = field(default_factory=list)        # [(Point, Point)] del contenuto vettoriale


def _bezier(p0, p1, p2, p3, n: int = 8) -> list[pymupdf.Point]:
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append(p0 * (u ** 3) + p1 * (3 * u * u * t) + p2 * (3 * u * t * t) + p3 * (t ** 3))
    return out


def _segment_cross(a, b, c, d) -> pymupdf.Point | None:
    r = b - a
    q = d - c
    den = r.x * q.y - r.y * q.x
    if abs(den) < 1e-9:
        return None
    t = ((c.x - a.x) * q.y - (c.y - a.y) * q.x) / den
    u = ((c.x - a.x) * r.y - (c.y - a.y) * r.x) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return a + r * t
    return None


def snap_targets(pdf: pymupdf.Document, page_no: int) -> SnapTargets:
    """Estremi, punti medi, intersezioni e segmenti del contenuto vettoriale della pagina e vertici
    delle annotazioni. Pagina senza grafica vettoriale (scansione): solo le annotazioni."""
    page = pdf[page_no]
    res = SnapTargets()
    straight: list[tuple[pymupdf.Point, pymupdf.Point]] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        for item in d.get("items", ()):
            op = item[0]
            if op == "l":
                straight.append((pymupdf.Point(item[1]), pymupdf.Point(item[2])))
            elif op == "re":
                r = pymupdf.Rect(item[1])
                c = [r.tl, r.tr, r.br, r.bl]
                straight += [(c[i], c[(i + 1) % 4]) for i in range(4)]
            elif op == "qu":
                q = item[1]
                c = [q.ul, q.ur, q.lr, q.ll]
                straight += [(c[i], c[(i + 1) % 4]) for i in range(4)]
            elif op == "c":
                pts = _bezier(*(pymupdf.Point(x) for x in item[1:5]))
                res.points += [(pts[0], SNAP_END), (pts[-1], SNAP_END)]
                res.segments += [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
            if len(straight) + len(res.segments) > MAX_SNAP_SEGMENTS:
                break
    for a, b in straight:
        if abs(b - a) < 0.01:
            continue
        res.points += [(a, SNAP_END), (b, SNAP_END), ((a + b) * 0.5, SNAP_MID)]
        res.segments.append((a, b))
    if len(straight) <= MAX_CROSS_SEGMENTS:
        res.points += [(p, SNAP_CROSS) for p in _crossings(straight)]
    for a in iter_annots(page):
        if a.type[0] in NON_COMMENT_TYPES or _silent(pdf, a):
            continue
        verts = a.vertices or []
        if a.type[0] == pymupdf.PDF_ANNOT_INK:          # dei tratti a mano libera solo inizio e fine
            verts = [v for stroke in verts for v in stroke[:1] + stroke[-1:]]
        res.points += [(pymupdf.Point(v), SNAP_ANNOT) for v in verts]
        if a.type[0] in (pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_FREE_TEXT,
                         pymupdf.PDF_ANNOT_STAMP):
            r = a.rect
            res.points += [(p, SNAP_ANNOT) for p in (r.tl, r.tr, r.br, r.bl)]
    return res


def _crossings(segs: list) -> list[pymupdf.Point]:
    """Intersezioni dei segmenti con una griglia (costo quasi lineare sui disegni tecnici)."""
    cell = 40.0
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(segs):
        x0, x1 = sorted((a.x, b.x))
        y0, y1 = sorted((a.y, b.y))
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                grid.setdefault((gx, gy), []).append(i)
    seen: set[tuple[int, int]] = set()
    out: list[pymupdf.Point] = []
    for ids in grid.values():
        for n, i in enumerate(ids):
            for j in ids[n + 1:]:
                key = (i, j) if i < j else (j, i)
                if key in seen:
                    continue
                seen.add(key)
                a, b = segs[i]
                c, d = segs[j]
                # gli estremi condivisi sono già punti di aggancio
                p = _segment_cross(a, b, c, d)
                if p is not None and min(abs(p - a), abs(p - b), abs(p - c), abs(p - d)) > 0.5:
                    out.append(p)
    return out


def _nearest_on_segment(p, a, b) -> pymupdf.Point:
    d = b - a
    ln = d.x * d.x + d.y * d.y
    if ln <= 0:
        return pymupdf.Point(a)
    t = max(0.0, min(1.0, ((p.x - a.x) * d.x + (p.y - a.y) * d.y) / ln))
    return a + d * t


def snap(targets: SnapTargets, point, tolerance: float, kinds=SNAP_KINDS) -> tuple[pymupdf.Point, str] | None:
    """Punto di aggancio più vicino entro la tolleranza: prima i punti notevoli (estremi,
    intersezioni, vertici delle annotazioni, punti medi), poi il tracciato più vicino."""
    p = pymupdf.Point(point)
    best = None
    best_d = tolerance
    priority = {SNAP_END: 0, SNAP_ANNOT: 0, SNAP_CROSS: 1, SNAP_MID: 2}
    for q, kind in targets.points:
        if kind not in kinds:
            continue
        dist = abs(q - p)
        if dist <= tolerance and (best is None or (priority[kind], dist) < (priority[best[1]], best_d)):
            best, best_d = (pymupdf.Point(q), kind), dist
    if best is not None:
        return best
    if SNAP_PATH in kinds:
        for a, b in targets.segments:
            q = _nearest_on_segment(p, a, b)
            dist = abs(q - p)
            if dist <= best_d:
                best, best_d = (q, SNAP_PATH), dist
    return best


# ------------------------------------------------------------------ timbri
@dataclass
class StampSpec:
    text: str
    color: tuple = (0.75, 0.1, 0.1)
    subtitle: str = ""      # seconda riga (data, autore) per i timbri dinamici
    name: str = ""          # /Name standard (Approved, Draft, …) o personalizzato


_GREEN, _RED, _BLUE, _GREY, _ORANGE, _PURPLE = ((0.08, 0.5, 0.24), (0.75, 0.1, 0.1), (0.15, 0.3, 0.7),
                                                 (0.4, 0.4, 0.4), (0.85, 0.35, 0.0), (0.5, 0.2, 0.6))
# Timbri predefiniti: (etichetta, /Name, colore). Etichette nella lingua dell'interfaccia.
PRESET_STAMPS = [
    ("Approvato", "Approved", _GREEN), ("Non approvato", "NotApproved", _RED), ("Bozza", "Draft", _BLUE),
    ("Definitivo", "Final", _GREEN), ("Riservato", "Confidential", _RED), ("Per commenti", "ForComment", _BLUE),
    ("Per la divulgazione", "ForPublicRelease", _GREEN), ("Non per la divulgazione", "NotForPublicRelease", _RED),
    ("Scaduto", "Expired", _RED), ("Così com'è", "AsIs", _BLUE), ("Sperimentale", "Experimental", _BLUE),
    ("Venduto", "Sold", _PURPLE), ("Dipartimentale", "Departmental", _BLUE), ("Segretissimo", "TopSecret", _RED),
    ("Ricevuto", "FoliantRicevuto", _BLUE), ("Pagato", "FoliantPagato", _GREEN),
    ("Verificato", "FoliantVerificato", _GREEN), ("Urgente", "FoliantUrgente", _ORANGE),
    ("Copia", "FoliantCopia", _GREY), ("Originale", "FoliantOriginale", _GREY),
]

# Modelli di timbri dinamici (segnaposto: {autore} {data} {ora} {file} {pagina}).
DYNAMIC_STAMPS = [
    ("Ricevuto", "Ricevuto il {data} alle {ora}", _BLUE),
    ("Approvato", "Approvato da {autore} il {data}", _GREEN),
    ("Verificato", "Verificato da {autore} il {data} {ora}", _GREEN),
    ("Pagato", "Pagato il {data}", _GREEN),
    ("Protocollo", "Protocollo n. {contatore} del {data}", _BLUE),
]


def fill_template(template: str, *, author: str = "", when: datetime | None = None, filename: str = "",
                  page: int | None = None, date_format: str | None = None, counter: int | None = None) -> str:
    """Compila un modello di timbro dinamico; data e ora nel formato della lingua dell'interfaccia,
    salvo un `date_format` esplicito (strftime)."""
    when = when or datetime.now()
    date = when.strftime(date_format) if date_format else local_date(when)
    values = {"{autore}": author or "", "{nome}": author or "", "{data}": date,
              "{contatore}": f"{counter:03d}" if counter is not None else "{contatore}",
              "{ora}": local_time(when), "{file}": filename,
              "{pagina}": str(page + 1) if page is not None else ""}
    if counter is None:         # senza contatore il segnaposto resta, con il nome della lingua del modello
        values.pop("{contatore}")
    for k, v in values.items():
        template = template.replace(k, v).replace(PLACEHOLDER_EN[k], v)
    return template


PLACEHOLDERS = ("{autore}", "{nome}", "{data}", "{ora}", "{file}", "{pagina}", "{contatore}")
# gli stessi segnaposto con i nomi inglesi (modelli dei timbri dinamici in inglese)
PLACEHOLDER_EN = {"{autore}": "{author}", "{nome}": "{name}", "{data}": "{date}", "{ora}": "{time}",
                  "{file}": "{file}", "{pagina}": "{page}", "{contatore}": "{counter}"}


def unknown_placeholders(template: str) -> list[str]:
    known = set(PLACEHOLDERS) | set(PLACEHOLDER_EN.values())
    return [p for p in re.findall(r"\{[^{}]*\}", template) if p not in known]


def dynamic_subtitle(template: str, author: str, when: datetime | None = None) -> str:
    return fill_template(template, author=author, when=when)


def _pdf_string(text: str) -> str:
    data = text.encode("cp1252", errors="replace")
    out = []
    for b in data:
        ch = chr(b)
        if ch in "()\\":
            out.append("\\" + ch)
        elif 32 <= b < 127:
            out.append(ch)
        else:
            out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def stamp_size(spec: StampSpec, height: float = 44.0) -> tuple[float, float]:
    main = pymupdf.get_text_length(spec.text.upper(), fontname="hebo", fontsize=height * 0.45)
    sub = pymupdf.get_text_length(spec.subtitle, fontname="helv", fontsize=height * 0.2) if spec.subtitle else 0
    return max(main, sub) + height * 0.6, height


def _stamp_appearance(spec: StampSpec, w: float, h: float, opacity: float) -> bytes:
    r, g, b = _rgb(spec.color)
    lw = max(1.5, h * 0.06)
    big = h * (0.4 if spec.subtitle else 0.5)
    small = h * 0.22
    main = spec.text.upper()
    main_w = pymupdf.get_text_length(main, fontname="hebo", fontsize=big)
    if main_w > w - h * 0.3:
        big *= (w - h * 0.3) / max(main_w, 1)
        main_w = pymupdf.get_text_length(main, fontname="hebo", fontsize=big)
    y_main = h * 0.5 - big * 0.35 + (small * 0.75 if spec.subtitle else 0)
    ops = [f"/GS0 gs {r:.3f} {g:.3f} {b:.3f} RG {r:.3f} {g:.3f} {b:.3f} rg {lw:.2f} w",
           f"{lw:.2f} {lw:.2f} {w - 2 * lw:.2f} {h - 2 * lw:.2f} re S",
           f"BT /FoHB {big:.2f} Tf {(w - main_w) / 2:.2f} {y_main:.2f} Td {_pdf_string(main)} Tj ET"]
    if spec.subtitle:
        sub_w = pymupdf.get_text_length(spec.subtitle, fontname="helv", fontsize=small)
        if sub_w > w - lw * 4:
            small *= (w - lw * 4) / max(sub_w, 1)
            sub_w = pymupdf.get_text_length(spec.subtitle, fontname="helv", fontsize=small)
        ops.append(f"BT /FoH {small:.2f} Tf {(w - sub_w) / 2:.2f} {h * 0.16:.2f} Td {_pdf_string(spec.subtitle)} Tj ET")
    return "\n".join(ops).encode("latin-1")


ANGLE_KEY = "FoliantAngle"
SIZE_KEY = "FoliantSize"


def _std_rect(pdf: pymupdf.Document, page: pymupdf.Page, xref: int) -> pymupdf.Rect:
    """/Rect in coordinate standard letto dal dizionario (Annot.rect del motore sbaglia su alcuni ritagli)."""
    raw = _array(pdf, xref, "Rect")
    if raw and len(raw) == 4:
        return from_pdf_rect(page, raw)
    return pymupdf.Rect(get_annot(page, xref).rect)


def _rotation(pdf: pymupdf.Document, xref: int) -> tuple[float, tuple[float, float] | None]:
    k, v = pdf.xref_get_key(xref, ANGLE_KEY)
    angle = float(v) if k in ("int", "real") else 0.0
    size = _array(pdf, xref, SIZE_KEY)
    return angle, (size[0], size[1]) if size and len(size) == 2 else None


def _rotated_box(w: float, h: float, angle: float) -> tuple[float, float]:
    a = math.radians(angle)
    return abs(w * math.cos(a)) + abs(h * math.sin(a)), abs(w * math.sin(a)) + abs(h * math.cos(a))


def _apply_rotation(pdf: pymupdf.Document, page: pymupdf.Page, xref: int) -> None:
    """Ruota l'aspetto (/Matrix del Form XObject) e adatta /Rect all'ingombro ruotato."""
    angle, size = _rotation(pdf, xref)
    get_annot(page, xref)
    k, v = pdf.xref_get_key(xref, "AP/N")
    if k != "xref" or size is None:
        return
    ap = int(v.split()[0])
    w, h = size
    a = math.radians(angle)
    c, s_ = math.cos(a), math.sin(a)
    pdf.xref_set_key(ap, "Matrix", f"[{c:.6f} {s_:.6f} {-s_:.6f} {c:.6f} 0 0]")
    bbox = _array(pdf, ap, "BBox")
    if bbox and len(bbox) == 4 and abs((bbox[2] - bbox[0]) - w) > 0.5:
        # aspetto con riquadro diverso (es. immagine 1×1): la scala porta il riquadro alla dimensione
        sx, sy = w / max(bbox[2] - bbox[0], 1e-6), h / max(bbox[3] - bbox[1], 1e-6)
        pdf.xref_set_key(ap, "Matrix", f"[{c * sx:.6f} {s_ * sx:.6f} {-s_ * sy:.6f} {c * sy:.6f} 0 0]")
    rw, rh = _rotated_box(w, h, angle)
    r = _std_rect(pdf, page, xref)
    cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
    new = pymupdf.Rect(cx - rw / 2, cy - rh / 2, cx + rw / 2, cy + rh / 2)
    pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, new)))


ROTATABLE = (pymupdf.PDF_ANNOT_STAMP, pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE,
             pymupdf.PDF_ANNOT_FREE_TEXT)


def keep_rotation(pdf: pymupdf.Document, page_no: int, xref: int) -> None:
    """Dopo una rigenerazione dell'aspetto da parte del motore, riapplica la rotazione salvata."""
    if _rotation(pdf, xref)[1] is not None:
        _apply_rotation(pdf, pdf[page_no], xref)


def rotate_annot(pdf: pymupdf.Document, page_no: int, xref: int, degrees: float) -> None:
    """Ruota timbri (vettoriali e immagine) di `degrees` gradi in senso antiorario (ANN-03)."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    t = annot.type[0]
    if t not in ROTATABLE or (t == pymupdf.PDF_ANNOT_FREE_TEXT and (
            page.rotation % 360 or _intent(pdf, xref) == "FreeTextCallout")):
        raise AnnotationError("Si possono ruotare timbri, rettangoli, ellissi e caselle di testo.")
    if annot.flags & pymupdf.PDF_ANNOT_IS_LOCKED:
        raise AnnotationError("L'annotazione è bloccata: non si può spostare né ridimensionare.")
    angle, size = _rotation(pdf, xref)
    if size is None:
        r = _std_rect(pdf, page, xref)
        size = (r.width, r.height)
        pdf.xref_set_key(xref, SIZE_KEY, f"[{size[0]:.3f} {size[1]:.3f}]")
    angle = (angle + degrees) % 360
    pdf.xref_set_key(xref, ANGLE_KEY, f"{angle:g}")
    touch(get_annot(page, xref))
    _apply_rotation(pdf, page, xref)


def _write_stamp_ap(pdf: pymupdf.Document, annot: pymupdf.Annot, spec: StampSpec, opacity: float) -> None:
    r = annot.rect
    w, h = r.width, r.height
    angle, size = _rotation(pdf, annot.xref)
    if size is not None:
        w, h = size
    font_b = pdf.get_new_xref()
    pdf.update_object(font_b, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica-Bold/Encoding/WinAnsiEncoding>>")
    font_r = pdf.get_new_xref()
    pdf.update_object(font_r, "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")
    ap = pdf.get_new_xref()
    pdf.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[0 0 {w:.2f} {h:.2f}]/Matrix[1 0 0 1 0 0]"
                          f"/Resources<</Font<</FoHB {font_b} 0 R/FoH {font_r} 0 R>>"
                          f"/ExtGState<</GS0<</Type/ExtGState/CA {opacity:.3f}/ca {opacity:.3f}>>>>>>>>")
    pdf.update_stream(ap, _stamp_appearance(spec, w, h, opacity))
    pdf.xref_set_key(annot.xref, "AP", f"<</N {ap} 0 R>>")
    if size is not None:
        _apply_rotation(pdf, annot.get_parent(), annot.xref)


def create_stamp(pdf: pymupdf.Document, page_no: int, point, spec: StampSpec, style: Style,
                 author: str | None = None, height: float = 44.0) -> int:
    """Timbro vettoriale con aspetto proprio, centrato su `point`."""
    page = pdf[page_no]
    w, h = stamp_size(spec, height)
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2)
    return _stamp_in_rect(pdf, page, rect, spec, style.opacity, author)


def _stamp_in_rect(pdf, page, rect, spec: StampSpec, opacity: float, author: str | None,
                   date: str | None = None) -> int:
    with unrotated(page):
        annot = page.add_stamp_annot(rect, 0)
    xref = _finish(annot, author, spec.text + (f" {spec.subtitle}" if spec.subtitle else ""), kind=STAMP,
                   date=date)
    annot = page.load_annot(xref)
    annot.set_opacity(max(0.0, min(1.0, opacity)))
    annot.update()
    name = spec.name or re.sub(r"[^A-Za-z0-9]", "", "Foliant" + spec.text.title())[:60] or "FoliantTimbro"
    pdf.xref_set_key(xref, "Name", "/" + name)
    pdf.xref_set_key(xref, "C", "[{:.3f} {:.3f} {:.3f}]".format(*_rgb(spec.color)))
    pdf.xref_set_key(xref, STAMP_KEY, "<</T{}/S{}>>".format(_pdf_string(spec.text), _pdf_string(spec.subtitle)))
    _write_stamp_ap(pdf, page.load_annot(xref), spec, max(0.05, min(1.0, opacity)))
    _upright(pdf, page, xref)
    return xref


def _upright(pdf: pymupdf.Document, page: pymupdf.Page, xref: int) -> None:
    """Su una pagina ruotata il timbro si raddrizza nella vista (rotazione inversa dell'aspetto)."""
    if page.rotation % 360:
        rotate_annot(pdf, page.number, xref, page.rotation % 360)


def stamp_spec_of(pdf: pymupdf.Document, xref: int) -> StampSpec | None:
    kind, _val = pdf.xref_get_key(xref, STAMP_KEY)
    if kind != "dict":
        return None
    t = pdf.xref_get_key(xref, STAMP_KEY + "/T")[1]
    s = pdf.xref_get_key(xref, STAMP_KEY + "/S")[1]
    c = pdf.xref_get_key(xref, "C")[1]
    try:
        color = tuple(float(v) for v in c.strip("[]").split())
    except ValueError:
        color = DEFAULT_COLORS[STAMP]
    n = pdf.xref_get_key(xref, "Name")
    return StampSpec(_unescape_pdf_string(t), _rgb(color) or DEFAULT_COLORS[STAMP], _unescape_pdf_string(s),
                     n[1].lstrip("/") if n[0] == "name" else "")


def _unescape_pdf_string(s: str) -> str:
    if s is None:
        return ""
    s = s.strip()
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]
    out = bytearray()
    i = 0
    raw = s.encode("latin-1", errors="replace")
    while i < len(raw):
        c = raw[i]
        if c == 0x5C and i + 1 < len(raw):
            n = raw[i + 1]
            if 0x30 <= n <= 0x37:
                j = i + 1
                digits = b""
                while j < len(raw) and len(digits) < 3 and 0x30 <= raw[j] <= 0x37:
                    digits += bytes([raw[j]])
                    j += 1
                out.append(int(digits, 8) & 0xFF)
                i = j
                continue
            out.append({ord("n"): 10, ord("r"): 13, ord("t"): 9}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return out.decode("cp1252", errors="replace")


def refresh_stamp(pdf: pymupdf.Document, page_no: int, xref: int) -> None:
    """Ridisegna l'aspetto di un timbro Foliant (dopo spostamento o modifica)."""
    spec = stamp_spec_of(pdf, xref)
    if spec is None:
        return
    page = pdf[page_no]
    annot = get_annot(page, xref)
    _write_stamp_ap(pdf, annot, spec, annot.opacity if annot.opacity >= 0 else 1.0)


# ------------------------------------------------------------------ adesivi (ANN-27)
STICKER_KEY = "FoliantSticker"
STICKERS = {
    "spunta": ("Spunta verde", "0.10 0.62 0.25 RG 12 w 1 J 1 j 18 50 m 40 26 l 84 76 l S"),
    "croce": ("Croce rossa", "0.82 0.12 0.12 RG 12 w 1 J 24 24 m 76 76 l S 76 24 m 24 76 l S"),
    "stella": ("Stella", "0.98 0.78 0.10 rg 0.85 0.5 0.05 RG 3 w 1 j 50 94 m 61 63 l 94 63 l 67 43 l 78 10 l "
                         "50 30 l 22 10 l 33 43 l 6 63 l 39 63 l h B"),
    "attenzione": ("Attenzione", "0.95 0.55 0.05 rg 50 96 m 96 12 l 4 12 l h f 1 1 1 rg 45 40 10 38 re f "
                                 "45 20 10 10 re f"),
    "info": ("Informazione", "0.15 0.4 0.85 rg 50 50 m 50 50 l h 50 94 m 74.3 94 94 74.3 94 50 c 94 25.7 74.3 6 50 6 c "
                             "25.7 6 6 25.7 6 50 c 6 74.3 25.7 94 50 94 c f 1 1 1 rg 44 18 12 40 re f 44 66 12 12 re f"),
    "freccia": ("Freccia", "0.15 0.4 0.85 rg 6 38 m 56 38 l 56 14 l 94 50 l 56 86 l 56 62 l 6 62 l h f"),
}


def _circle(cx: float, cy: float, r: float) -> str:
    """Cerchio come quattro curve di Bézier (operatori PDF)."""
    k = 0.5523 * r
    return (f"{cx + r} {cy} m {cx + r} {cy + k} {cx + k} {cy + r} {cx} {cy + r} c "
            f"{cx - k} {cy + r} {cx - r} {cy + k} {cx - r} {cy} c {cx - r} {cy - k} {cx - k} {cy - r} {cx} {cy - r} c "
            f"{cx + k} {cy - r} {cx + r} {cy - k} {cx + r} {cy} c h ")


_THUMB = ("0.98 0.78 0.35 rg 0.55 0.35 0.1 RG 3 w 1 j 1 J 28 10 m 74 10 l 82 10 88 16 88 24 c 88 52 l "
          "88 58 84 62 78 62 c 56 62 l 60 86 l 60 92 54 96 50 94 c 44 92 l 34 64 l 28 62 l h B "
          "10 10 18 52 re B")
STICKERS.update({
    "completato": ("Completato", "0.10 0.62 0.25 rg " + _circle(50, 50, 44) + "f 1 1 1 RG 10 w 1 J 1 j "
                                 "28 50 m 44 34 l 74 66 l S"),
    "esclamativo": ("Punto esclamativo", "0.85 0.15 0.15 rg " + _circle(50, 50, 44) + "f 1 1 1 rg 44 40 12 40 re f "
                                         "44 18 12 12 re f"),
    "domanda": ("Punto interrogativo", "0.15 0.4 0.85 rg " + _circle(50, 50, 44) + "f 1 1 1 RG 10 w 1 J "
                                       "34 64 m 34 76 42 82 50 82 c 60 82 66 76 66 66 c 66 54 50 52 50 40 c 50 36 l S "
                                       "1 1 1 rg 45 16 10 10 re f"),
    "divieto": ("Divieto", "0.85 0.12 0.12 RG 10 w " + _circle(50, 50, 40) + "S 22 22 m 78 78 l S"),
    "in_corso": ("In corso", "0.95 0.6 0.1 rg " + _circle(50, 50, 44) + "f 1 1 1 RG 8 w 1 J 50 50 m 50 78 l S "
                             "50 50 m 70 40 l S"),
    "freccia_sinistra": ("Freccia a sinistra", "0.15 0.4 0.85 rg 94 38 m 44 38 l 44 14 l 6 50 l 44 86 l 44 62 l "
                                               "94 62 l h f"),
    "freccia_su": ("Freccia in alto", "0.15 0.4 0.85 rg 38 6 m 38 56 l 14 56 l 50 94 l 86 56 l 62 56 l 62 6 l h f"),
    "freccia_giu": ("Freccia in basso", "0.15 0.4 0.85 rg 38 94 m 38 44 l 14 44 l 50 6 l 86 44 l 62 44 l "
                                        "62 94 l h f"),
    "faccina": ("Faccina sorridente", "0.99 0.82 0.2 rg 0.75 0.5 0.05 RG 3 w " + _circle(50, 50, 44) + "B "
                                      "0.25 0.15 0.05 rg " + _circle(35, 62, 6) + "f " + _circle(65, 62, 6) + "f "
                                      "0.25 0.15 0.05 RG 5 w 1 J 28 38 m 38 22 62 22 72 38 c S"),
    "faccina_triste": ("Faccina triste", "0.99 0.82 0.2 rg 0.75 0.5 0.05 RG 3 w " + _circle(50, 50, 44) + "B "
                                         "0.25 0.15 0.05 rg " + _circle(35, 62, 6) + "f " + _circle(65, 62, 6) + "f "
                                         "0.25 0.15 0.05 RG 5 w 1 J 28 26 m 38 42 62 42 72 26 c S"),
    "pollice_su": ("Pollice in su", _THUMB),
    "pollice_giu": ("Pollice in giù", "q 1 0 0 -1 0 100 cm " + _THUMB + " Q"),
    "cuore": ("Cuore", "0.88 0.15 0.3 rg 50 14 m 20 38 6 54 6 70 c 6 84 16 94 30 94 c 40 94 46 88 50 80 c "
                       "54 88 60 94 70 94 c 84 94 94 84 94 70 c 94 54 80 38 50 14 c h f"),
})

# categorie della tavolozza degli adesivi (ANN-27)
STICKER_CATEGORIES = [
    ("Spunte e croci", ["spunta", "croce", "completato"]),
    ("Simboli di stato", ["attenzione", "info", "esclamativo", "domanda", "divieto", "in_corso"]),
    ("Frecce", ["freccia", "freccia_sinistra", "freccia_su", "freccia_giu"]),
    ("Faccine e gesti", ["faccina", "faccina_triste", "pollice_su", "pollice_giu"]),
    ("Stelle e cuori", ["stella", "cuore"]),
]
STICKER_SUBJECT = "Adesivo"
STICKER_MIN = 8.0       # lato minimo di un adesivo ridimensionato (pt)


def _sticker_ap(pdf: pymupdf.Document, xref: int, name: str, opacity: float) -> None:
    ops = STICKERS[name][1]
    ap = pdf.get_new_xref()
    pdf.update_object(ap, "<</Type/XObject/Subtype/Form/BBox[0 0 100 100]/Matrix[1 0 0 1 0 0]"
                          f"/Resources<</ExtGState<</GS0<</Type/ExtGState/CA {opacity:.3f}/ca {opacity:.3f}>>>>>>>>")
    pdf.update_stream(ap, ("/GS0 gs " + ops).encode("latin-1"))
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")


def create_sticker(pdf: pymupdf.Document, page_no: int, point, name: str, style: Style,
                   author: str | None = None, size: float = 36.0) -> int:
    """Adesivo grafico vettoriale (non dipende da caratteri emoji)."""
    if name not in STICKERS:
        raise AnnotationError("Adesivo sconosciuto.")
    page = pdf[page_no]
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x - size / 2, p.y - size / 2, p.x + size / 2, p.y + size / 2)
    with unrotated(page):
        annot = page.add_stamp_annot(rect, 0)
    xref = _finish(annot, author, _(STICKERS[name][0]), subject=STICKER_SUBJECT, kind=STAMP)
    pdf.xref_set_key(xref, "Name", "/FoliantAdesivo" + name.title().replace("_", ""))
    pdf.xref_set_key(xref, STICKER_KEY, "/" + name)
    _sticker_ap(pdf, xref, name, max(0.0, min(1.0, style.opacity)))
    pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, rect)))
    _upright(pdf, page, xref)
    return xref


def is_sticker(pdf: pymupdf.Document, xref: int) -> bool:
    if pdf.xref_get_key(xref, STICKER_KEY)[0] == "name":
        return True
    k, v = pdf.xref_get_key(xref, "Subj")
    return k == "string" and v in (STICKER_SUBJECT, _(STICKER_SUBJECT))


def refresh_sticker(pdf: pymupdf.Document, page_no: int, xref: int) -> None:
    k, v = pdf.xref_get_key(xref, STICKER_KEY)
    if k != "name" or v.lstrip("/") not in STICKERS:
        return
    page = pdf[page_no]
    annot = get_annot(page, xref)
    _sticker_ap(pdf, xref, v.lstrip("/"), annot.opacity if annot.opacity >= 0 else 1.0)
    if _rotation(pdf, xref)[1] is not None:
        _apply_rotation(pdf, page, xref)


PAGE_STAMP_KEY = "FoliantPageStamp"


def create_page_stamp(pdf: pymupdf.Document, page_no: int, point, src: pymupdf.Document, src_page: int,
                      style: Style, author: str | None = None, max_side: float = 200.0, name: str = "") -> int:
    """Timbro con l'aspetto vettoriale di una pagina di un altro PDF (contenuto e risorse innestati)."""
    if src.needs_pass:
        raise AnnotationError("Il PDF di origine è protetto da password.")
    if not 0 <= src_page < src.page_count:
        raise AnnotationError("Pagina inesistente.")
    sp = src[src_page]
    box = sp.cropbox if not sp.cropbox.is_empty else sp.mediabox
    w, h = box.width, box.height
    k = max_side / max(w, h)
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x - w * k / 2, p.y - h * k / 2, p.x + w * k / 2, p.y + h * k / 2)
    page = pdf[page_no]
    with unrotated(page):
        annot = page.add_stamp_annot(rect, 0)
    xref = _finish(annot, author, name or "Timbro da pagina", kind=STAMP)
    # risorse della pagina sorgente (anche ereditate) innestate nel documento
    src_obj = mupdf.pdf_load_object(pymupdf._as_pdf_document(src), sp.xref)
    res = mupdf.pdf_dict_get_inheritable(src_obj, mupdf.pdf_new_name("Resources"))
    dst_doc = pymupdf._as_pdf_document(pdf)
    res_num = 0
    if res.m_internal:
        grafted = mupdf.pdf_graft_object(dst_doc, res)
        res_num = mupdf.pdf_to_num(mupdf.pdf_add_object(dst_doc, grafted)) if not mupdf.pdf_is_indirect(grafted) \
            else mupdf.pdf_to_num(grafted)
    content = b"\n".join(src.xref_stream(x) or b"" for x in sp.get_contents())
    mb = sp.mediabox
    cb = box if box else mb
    ap = pdf.get_new_xref()
    resources = f"/Resources {res_num} 0 R" if res_num else ""
    # /BBox nelle coordinate della pagina sorgente (area visibile): contenuto vettoriale, nessuna immagine
    x0, y0 = cb.x0 + mb.x0, mb.y1 - cb.y1
    pdf.update_object(ap, f"<</Type/XObject/Subtype/Form/BBox[{x0:.3f} {y0:.3f} {x0 + w:.3f} {y0 + h:.3f}]"
                          f"/Matrix[1 0 0 1 0 0]{resources}>>")
    pdf.update_stream(ap, content)
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
    pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, rect)))
    pdf.xref_set_key(xref, "Name", "/FoliantPagina")
    pdf.xref_set_key(xref, PAGE_STAMP_KEY, "true")
    annot = get_annot(page, xref)
    annot.set_opacity(max(0.0, min(1.0, style.opacity)))
    pdf.xref_set_key(xref, "AP", f"<</N {ap} 0 R>>")
    _upright(pdf, page, xref)
    return xref


def create_image_stamp(pdf: pymupdf.Document, page_no: int, point, image: bytes, style: Style,
                       author: str | None = None, max_side: float = 160.0, name: str = "",
                       intent: str = "StampImage", sticker: bool = False) -> int:
    """Timbro da immagine; con `sticker` è un adesivo personalizzato (/Subj «Adesivo», ANN-27)."""
    page = pdf[page_no]
    try:
        pix = pymupdf.Pixmap(image)
        w, h = pix.width, pix.height
    except Exception as e:
        raise AnnotationError("L'immagine non è leggibile.") from e
    if w <= 0 or h <= 0:
        raise AnnotationError("L'immagine non è leggibile.")
    k = max_side / max(w, h)
    p = pymupdf.Point(point)
    rect = pymupdf.Rect(p.x - w * k / 2, p.y - h * k / 2, p.x + w * k / 2, p.y + h * k / 2)
    with unrotated(page):
        annot = page.add_stamp_annot(rect, image)
    annot.set_opacity(max(0.0, min(1.0, style.opacity)))
    xref = _finish(annot, author, name or _("Timbro immagine"), kind=STAMP,
                   subject=STICKER_SUBJECT if sticker else None)
    pdf.xref_set_key(xref, "IT", "/" + intent)
    _upright(pdf, page, xref)
    return xref


# ------------------------------------------------------------------ allegati
MAX_ATTACHMENT = 200 * 1024 * 1024


def create_attachment(pdf: pymupdf.Document, page_no: int, point, data: bytes, filename: str,
                      style: Style, author: str | None = None, description: str = "") -> int:
    if pdfa_part(pdf) == "1":
        raise AnnotationError("Il documento è PDF/A-1: il profilo non ammette file allegati.")
    if len(data) > MAX_ATTACHMENT:
        raise AnnotationError("Il file è troppo grande per essere allegato (massimo 200 MB).")
    page = pdf[page_no]
    annot = page.add_file_annot(pymupdf.Point(point), data, filename, desc=description or filename,
                                icon="PushPin")
    annot.set_colors(stroke=_rgb(style.color or DEFAULT_COLORS[ATTACHMENT]))
    annot.set_flags(pymupdf.PDF_ANNOT_IS_PRINT | pymupdf.PDF_ANNOT_IS_NO_ZOOM | pymupdf.PDF_ANNOT_IS_NO_ROTATE)
    return _finish(annot, author, description or filename, kind=ATTACHMENT)


def pdfa_part(pdf: pymupdf.Document) -> str:
    """Parte PDF/A dichiarata nei metadati XMP ("1", "2", "3") oppure ""."""
    try:
        xmp = pdf.get_xml_metadata() or ""
    except Exception:
        return ""
    m = re.search(r"pdfaid:part\s*=\s*[\"'](\d)|<pdfaid:part>\s*(\d)", xmp)
    return (m.group(1) or m.group(2)) if m else ""


# ------------------------------------------------------------------ PDF/A e PDF taggati (ANN-01)
_FORBIDDEN_FLAGS = (pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_INVISIBLE | pymupdf.PDF_ANNOT_IS_NO_VIEW
                    | pymupdf.PDF_ANNOT_IS_TOGGLE_NO_VIEW)


def is_tagged(pdf: pymupdf.Document) -> bool:
    return pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")[0] == "xref"


@dataclass
class ConformResult:
    opacity_forced: int = 0      # annotazioni portate a opacità 1 (PDF/A-1)
    tagged: int = 0              # annotazioni inserite nella struttura logica


def conform(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> ConformResult:
    """Adegua le annotazioni di commento ai vincoli del documento (ANN-01).

    PDF/A dichiarato: flag Print, nessun flag Hidden/Invisible/NoView/ToggleNoView, /AP con la sola
    voce /N (le risposte e gli stati, che non si possono nascondere, ricevono un aspetto vuoto),
    opacità 1 per PDF/A-1. Documento taggato: ogni annotazione non ancora nella struttura logica
    diventa un elemento /Annot con testo alternativo e la pagina riceve /Tabs /S."""
    res = ConformResult()
    part = pdfa_part(pdf)
    tagged = is_tagged(pdf)
    if not part and not tagged:
        return res
    if tagged:
        _prune_structure(pdf)
    for page_no in (range(pdf.page_count) if pages is None else pages):
        page = pdf[page_no]
        items = [(x, t) for x, t, _n in page.annot_xrefs() if t not in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK)]
        if not items:
            continue
        for x, t in items:
            if part:
                _conform_pdfa(pdf, page, x, t, part, res)
            if tagged and t != pymupdf.PDF_ANNOT_POPUP:
                if _add_to_structure(pdf, page, x):
                    res.tagged += 1
        if tagged and pdf.xref_get_key(page.xref, "Tabs")[1] != "/S":
            pdf.xref_set_key(page.xref, "Tabs", "/S")
    return res


def _conform_pdfa(pdf, page, xref: int, t: int, part: str, res: ConformResult) -> None:
    k, v = pdf.xref_get_key(xref, "F")
    flags = int(v) if k == "int" else 0
    hidden = bool(flags & pymupdf.PDF_ANNOT_IS_HIDDEN)
    new = (flags | pymupdf.PDF_ANNOT_IS_PRINT) & ~_FORBIDDEN_FLAGS
    if new != flags:
        pdf.xref_set_key(xref, "F", str(new))
    if t == pymupdf.PDF_ANNOT_POPUP:
        return
    if part == "1":
        k, v = pdf.xref_get_key(xref, "CA")
        try:
            if k in ("float", "int") and float(v) < 1:
                pdf.xref_set_key(xref, "CA", "1")
                res.opacity_forced += 1
        except ValueError:
            pass
    if hidden or (pdf.xref_get_key(xref, "IRT")[0] == "xref" and pdf.xref_get_key(xref, "RT")[1] != "/Group"):
        # risposta, stato o annotazione nascosta: resta invisibile con un aspetto vuoto
        empty = pdf.get_new_xref()
        pdf.update_object(empty, "<</Type/XObject/Subtype/Form/BBox[0 0 0 0]/Resources<<>>/Length 0>>")
        pdf.update_stream(empty, b"")
        pdf.xref_set_key(xref, "AP", f"<</N {empty} 0 R>>")
        return
    ap_kind, _ap = pdf.xref_get_key(xref, "AP")
    if ap_kind == "null":
        return
    for key in ("D", "R"):
        if pdf.xref_get_key(xref, f"AP/{key}")[0] != "null":
            pdf.xref_set_key(xref, f"AP/{key}", "null")
    if pdf.xref_get_key(xref, "AP/N")[0] == "dict":    # aspetti per stato: solo /N con uno stream
        k_as, v_as = pdf.xref_get_key(xref, "AS")
        k_n, v_n = pdf.xref_get_key(xref, "AP/N/" + v_as.lstrip("/")) if k_as == "name" else ("null", "")
        if k_n == "xref":
            pdf.xref_set_key(xref, "AP/N", v_n)


def _alt_text(pdf, xref: int) -> str:
    for key in ("Contents", "Subj"):
        k, v = pdf.xref_get_key(xref, key)
        if k == "string" and v.strip():
            return v.strip()
    k, v = pdf.xref_get_key(xref, "Subtype")
    sub = v.lstrip("/") if k == "name" else ""
    return _(TYPE_LABELS.get(sub, sub)) or _("Annotazione")


def _add_to_structure(pdf, page, xref: int) -> bool:
    """Inserisce l'annotazione nella struttura logica come elemento /Annot (se non c'è già)."""
    if pdf.xref_get_key(xref, "StructParent")[0] == "int":
        return False
    root = int(pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    # genitore: l'elemento /Document se è l'unico figlio della radice, altrimenti la radice
    parent = root
    k, v = pdf.xref_get_key(root, "K")
    if k == "xref":
        kid = int(v.split()[0])
        if pdf.xref_get_key(kid, "S")[1] == "/Document":
            parent = kid
    k, v = pdf.xref_get_key(root, "ParentTreeNextKey")
    key = int(v) if k == "int" else _next_parent_key(pdf, root)
    elem = pdf.get_new_xref()
    alt = _pdf_string(_alt_text(pdf, xref))
    pdf.update_object(elem, f"<</Type/StructElem/S/Annot/P {parent} 0 R/Pg {page.xref} 0 R/Alt{alt}"
                            f"/K<</Type/OBJR/Obj {xref} 0 R/Pg {page.xref} 0 R>>>>")
    _append_kid(pdf, parent, elem)
    pdf.xref_set_key(xref, "StructParent", str(key))
    _parent_tree_add(pdf, root, key, elem)
    pdf.xref_set_key(root, "ParentTreeNextKey", str(key + 1))
    return True


def _prune_structure(pdf) -> None:
    """Toglie dalla struttura gli elementi /Annot di annotazioni eliminate."""
    root = int(pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")[1].split()[0])
    k, v = pdf.xref_get_key(root, "ParentTree")
    if k != "xref":
        return
    tree = int(v.split()[0])
    k, nums = pdf.xref_get_key(tree, "Nums")
    if k != "array":
        return
    changed = False
    for key, elem in re.findall(r"(\d+)\s+(\d+)\s+0\s+R", nums):
        elem = int(elem)
        if pdf.xref_get_key(elem, "S")[1] != "/Annot":
            continue
        k_o, v_o = pdf.xref_get_key(elem, "K/Obj")
        obj = int(v_o.split()[0]) if k_o == "xref" else 0
        alive = obj and pdf.xref_get_key(obj, "Type")[1] == "/Annot" and _annot_on_page(pdf, elem, obj)
        if alive:
            continue
        parent = pdf.xref_get_key(elem, "P")
        if parent[0] == "xref":
            pid = int(parent[1].split()[0])
            k_k, v_k = pdf.xref_get_key(pid, "K")
            if k_k == "array":
                pdf.xref_set_key(pid, "K", re.sub(rf"(?<!\d){elem}\s+0\s+R", "", v_k))
        nums = re.sub(rf"(?<!\d){key}\s+{elem}\s+0\s+R", "", nums)
        changed = True
    if changed:
        pdf.xref_set_key(tree, "Nums", nums)


def _annot_on_page(pdf, elem: int, obj: int) -> bool:
    k, v = pdf.xref_get_key(elem, "K/Pg")
    if k != "xref":
        return True
    k, v = pdf.xref_get_key(int(v.split()[0]), "Annots")
    return k == "array" and re.search(rf"(?<!\d){obj}\s+0\s+R", v) is not None


def _append_kid(pdf, parent: int, elem: int) -> None:
    k, v = pdf.xref_get_key(parent, "K")
    ref = f"{elem} 0 R"
    if k == "array":
        pdf.xref_set_key(parent, "K", v.rstrip()[:-1] + f" {ref}]")
    elif k in ("xref", "int", "dict"):
        pdf.xref_set_key(parent, "K", f"[{v} {ref}]")
    else:
        pdf.xref_set_key(parent, "K", f"[{ref}]")


def _parent_tree_nums(pdf, node: int, out: list[int]) -> None:
    k, v = pdf.xref_get_key(node, "Nums")
    if k == "array":
        out.extend(int(n) for n in re.findall(r"(?:^|[\[\s])(\d+)\s+(?=\d+\s+0\s+R|\[|<<)", v))
    k, v = pdf.xref_get_key(node, "Kids")
    if k == "array":
        for kid in re.findall(r"(\d+)\s+0\s+R", v):
            _parent_tree_nums(pdf, int(kid), out)


def _next_parent_key(pdf, root: int) -> int:
    k, v = pdf.xref_get_key(root, "ParentTree")
    keys: list[int] = []
    if k == "xref":
        _parent_tree_nums(pdf, int(v.split()[0]), keys)
    return max(keys) + 1 if keys else 0


def _parent_tree_add(pdf, root: int, key: int, elem: int) -> None:
    k, v = pdf.xref_get_key(root, "ParentTree")
    if k != "xref":
        tree = pdf.get_new_xref()
        pdf.update_object(tree, f"<</Nums[{key} {elem} 0 R]>>")
        pdf.xref_set_key(root, "ParentTree", f"{tree} 0 R")
        return
    tree = int(v.split()[0])
    k, v = pdf.xref_get_key(tree, "Nums")
    if k == "array":
        pdf.xref_set_key(tree, "Nums", v.rstrip()[:-1] + f" {key} {elem} 0 R]")
        return
    # albero con /Kids: nuova foglia in coda (le chiavi nuove sono le più alte)
    leaf = pdf.get_new_xref()
    pdf.update_object(leaf, f"<</Limits[{key} {key}]/Nums[{key} {elem} 0 R]>>")
    _append_kid(pdf, tree, leaf) if pdf.xref_get_key(tree, "Kids")[0] != "array" else \
        pdf.xref_set_key(tree, "Kids", pdf.xref_get_key(tree, "Kids")[1].rstrip()[:-1] + f" {leaf} 0 R]")


def attachment_data(pdf: pymupdf.Document, page_no: int, xref: int) -> tuple[str, bytes]:
    page = pdf[page_no]
    annot = get_annot(page, xref)
    info = annot.file_info
    return info.get("filename") or "allegato", annot.get_file()


# ------------------------------------------------------------------ lettura
TYPE_LABELS = {
    "Text": "Nota", "FreeText": "Casella di testo", "Highlight": "Evidenziazione", "Underline": "Sottolineatura",
    "StrikeOut": "Barrato", "Squiggly": "Ondulato", "Square": "Rettangolo", "Circle": "Ellisse",
    "Line": "Linea", "Polygon": "Poligono", "PolyLine": "Polilinea", "Ink": "Disegno a mano", "Stamp": "Timbro",
    "FileAttachment": "Allegato", "Caret": "Inserimento", "Sound": "Suono", "Redact": "Redazione",
}


@dataclass
class AnnotInfo:
    page: int
    xref: int
    type: str                 # nome del tipo PDF ("Highlight", "Square", …)
    kind: str                 # tipo Foliant (HIGHLIGHT, RECT, …) o ""
    author: str
    contents: str
    subject: str
    created: str
    modified: str
    color: tuple | None
    fill: tuple | None
    opacity: float
    width: float
    dashed: bool
    rect: pymupdf.Rect
    name: str                 # /NM
    irt: int = 0              # xref della annotazione a cui risponde
    state: str = ""           # stato di revisione (per le risposte di stato)
    state_model: str = ""
    hidden: bool = False
    flags: int = 0
    marked_text: str = ""
    replies: list["AnnotInfo"] = field(default_factory=list)
    status: str = STATE_NONE   # stato corrente (ultimo stato registrato)
    marked: bool = False
    history: list["AnnotInfo"] = field(default_factory=list)   # stati registrati, in ordine
    reply_type: str = ""        # /RT: "R" risposta, "Group" elemento di un gruppo
    group: list["AnnotInfo"] = field(default_factory=list)

    @property
    def is_state(self) -> bool:
        return bool(self.state_model)

    @property
    def locked(self) -> bool:
        return bool(self.flags & (pymupdf.PDF_ANNOT_IS_LOCKED | pymupdf.PDF_ANNOT_IS_READ_ONLY))

    @property
    def label(self) -> str:
        if self.kind == REPLACE:
            return SUBJECTS[REPLACE] if self.contents else "Cancellazione"
        if self.kind == STAMP and self.subject in (STICKER_SUBJECT, _(STICKER_SUBJECT)):
            return STICKER_SUBJECT
        if self.kind in (CALLOUT, ARROW, CLOUD, DISTANCE, PERIMETER, AREA, TYPEWRITER):
            return SUBJECTS[self.kind]
        return TYPE_LABELS.get(self.type, self.type)


def _intent(pdf: pymupdf.Document, xref: int) -> str:
    k, v = pdf.xref_get_key(xref, "IT")
    return v.lstrip("/") if k == "name" else ""


MARKUP_TYPES = (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_STRIKE_OUT,
                pymupdf.PDF_ANNOT_SQUIGGLY)


def kind_of(pdf: pymupdf.Document, annot: pymupdf.Annot) -> str:
    t = annot.type[0]
    intent = _intent(pdf, annot.xref)
    if t == pymupdf.PDF_ANNOT_HIGHLIGHT:
        return HIGHLIGHT
    if t == pymupdf.PDF_ANNOT_UNDERLINE:
        return UNDERLINE
    if t == pymupdf.PDF_ANNOT_STRIKE_OUT:
        return STRIKEOUT
    if t == pymupdf.PDF_ANNOT_SQUIGGLY:
        return SQUIGGLY
    if t == pymupdf.PDF_ANNOT_TEXT:
        return NOTE
    if t == pymupdf.PDF_ANNOT_FREE_TEXT:
        if intent == "FreeTextTypeWriter":
            return TYPEWRITER
        return CALLOUT if intent == "FreeTextCallout" else FREETEXT
    if t == pymupdf.PDF_ANNOT_CARET:
        return CARET
    if t == pymupdf.PDF_ANNOT_SQUARE:
        return RECT
    if t == pymupdf.PDF_ANNOT_CIRCLE:
        return ELLIPSE
    if t == pymupdf.PDF_ANNOT_LINE:
        if intent == "LineDimension":
            return DISTANCE
        return ARROW if intent == "LineArrow" or any(annot.line_ends or ()) else LINE
    if t == pymupdf.PDF_ANNOT_POLYGON:
        if intent == "PolygonDimension":
            return AREA
        return CLOUD if intent == "PolygonCloud" or pdf.xref_get_key(annot.xref, "BE/S")[1] == "/C" else POLYGON
    if t == pymupdf.PDF_ANNOT_POLY_LINE:
        if intent == "PolyLineDimension":
            return ANGLE if pdf.xref_get_key(annot.xref, "Measure/T")[0] == "array" else PERIMETER
        return POLYLINE
    if t == pymupdf.PDF_ANNOT_INK:
        return INK
    if t == pymupdf.PDF_ANNOT_STAMP:
        return STAMP
    if t == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
        return ATTACHMENT
    return ""


def _info(pdf: pymupdf.Document, page_no: int, annot: pymupdf.Annot, words: list | None = None) -> AnnotInfo:
    info = annot.info or {}
    colors = annot.colors or {}
    border = annot.border or {}
    sm = pdf.xref_get_key(annot.xref, "StateModel")
    st = pdf.xref_get_key(annot.xref, "State")
    state_model = _unescape_pdf_string(sm[1]) if sm[0] == "string" else (sm[1].lstrip("/") if sm[0] == "name" else "")
    state = _unescape_pdf_string(st[1]) if st[0] == "string" else (st[1].lstrip("/") if st[0] == "name" else "")
    op = annot.opacity
    return AnnotInfo(
        page=page_no, xref=annot.xref, type=annot.type[1], kind=kind_of(pdf, annot),
        author=info.get("title", ""), contents=info.get("content", ""), subject=info.get("subject", ""),
        created=info.get("creationDate", ""), modified=info.get("modDate", ""),
        color=_rgb(colors.get("stroke")) if colors.get("stroke") else None,
        fill=_rgb(colors.get("fill")) if colors.get("fill") else None,
        opacity=1.0 if op is None or op < 0 else float(op),
        width=float(border.get("width", 1) if border.get("width", -1) >= 0 else 1),
        dashed=bool(border.get("dashes")),
        rect=pymupdf.Rect(annot.rect), name=info.get("id", ""), irt=annot.irt_xref or 0,
        state=state, state_model=state_model, hidden=bool(annot.flags & pymupdf.PDF_ANNOT_IS_HIDDEN),
        flags=annot.flags,
        reply_type=pdf.xref_get_key(annot.xref, "RT")[1].lstrip("/") if annot.irt_xref else "",
        marked_text=marked_text(annot.get_parent(), annot, words) if annot.type[0] in MARKUP_TYPES else "",
    )


def page_annotations(pdf: pymupdf.Document, page_no: int) -> list[AnnotInfo]:
    page = pdf[page_no]
    out = []
    words = None
    annot = page.first_annot
    while annot is not None:
        current, annot = annot, annot.next
        if current.type[0] in NON_COMMENT_TYPES:
            continue
        if words is None and current.type[0] in MARKUP_TYPES:
            try:
                words = page.get_text("words")
            except Exception:
                words = []
        try:
            out.append(_info(pdf, page_no, current, words))
        except Exception:
            continue
    return out


def list_annotations(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> list[AnnotInfo]:
    """Commenti principali con risposte e stato (le risposte non compaiono in cima)."""
    with bulk_geometry():
        return _list_annotations(pdf, pages)


def _list_annotations(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> list[AnnotInfo]:
    result: list[AnnotInfo] = []
    for page_no in (pages if pages is not None else range(pdf.page_count)):
        items = page_annotations(pdf, page_no)
        by_xref = {a.xref: a for a in items}
        roots: list[AnnotInfo] = []
        states: list[AnnotInfo] = []
        for a in items:
            parent = by_xref.get(a.irt) if a.irt else None
            if parent is None:
                if not a.is_state:
                    roots.append(a)
                continue
            if a.is_state:
                states.append(a)
                continue
            if a.reply_type == "Group":
                parent.group.append(a)
                if parent.kind == CARET:
                    parent.kind = REPLACE
                    parent.marked_text = a.marked_text
                continue
            # risalire al commento principale (catene cicliche interrotte)
            seen = {a.xref}
            root = parent
            cyclic = False
            while root.irt and root.irt in by_xref:
                if root.xref in seen:
                    cyclic = True
                    break
                seen.add(root.xref)
                root = by_xref[root.irt]
            if cyclic or root.xref in seen:
                roots.append(a)
            else:
                root.replies.append(a)
        for st in sorted(states, key=lambda x: (x.created, x.xref)):
            parent = by_xref[st.irt]
            parent.history.append(st)
            if st.state_model.lower() == "marked":
                parent.marked = st.state.lower() == "marked"
            else:
                parent.status = st.state or STATE_NONE
        for r in roots:
            r.replies.sort(key=lambda x: (x.created, x.xref))
        result.extend(roots)
    return result


def _irt_of(pdf: pymupdf.Document, xref: int) -> int:
    k, v = pdf.xref_get_key(xref, "IRT")
    return int(v.split()[0]) if k == "xref" else 0


def annotation(pdf: pymupdf.Document, page_no: int, xref: int) -> AnnotInfo | None:
    """Una sola annotazione con risposte dirette e stato (costo lineare, usata a ogni ridisegno)."""
    page = pdf[page_no]
    annot = find_annot(page, xref)
    if annot is None or annot.type[0] in NON_COMMENT_TYPES:
        return None
    info = _info(pdf, page_no, annot)
    children = [x for x, t, _n in page.annot_xrefs() if t not in NON_COMMENT_TYPES and _irt_of(pdf, x) == xref]
    states = []
    for x in children:
        c = find_annot(page, x)
        if c is None:
            continue
        ci = _info(pdf, page_no, c)
        if ci.is_state:
            states.append(ci)
        elif ci.reply_type == "Group":
            info.group.append(ci)
            if info.kind == CARET:
                info.kind = REPLACE
                info.marked_text = ci.marked_text
        else:
            info.replies.append(ci)
    for st in sorted(states, key=lambda x: (x.created, x.xref)):
        info.history.append(st)
        if st.state_model.lower() == "marked":
            info.marked = st.state.lower() == "marked"
        else:
            info.status = st.state or STATE_NONE
    info.replies.sort(key=lambda x: (x.created, x.xref))
    return info


def _dist_to_segment(p: pymupdf.Point, a: pymupdf.Point, b: pymupdf.Point) -> float:
    ab = b - a
    l2 = ab.x * ab.x + ab.y * ab.y
    if l2 == 0:
        return abs(p - a)
    t = max(0.0, min(1.0, ((p.x - a.x) * ab.x + (p.y - a.y) * ab.y) / l2))
    return abs(p - (a + ab * t))


ICON_TYPES = (pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FILE_ATTACHMENT, pymupdf.PDF_ANNOT_SOUND)


def _no_rotate(pdf: pymupdf.Document, xref: int) -> bool:
    k, v = pdf.xref_get_key(xref, "F")
    try:
        return k == "int" and bool(int(v) & pymupdf.PDF_ANNOT_IS_NO_ROTATE)
    except ValueError:
        return False


def annot_rects(pdf: pymupdf.Document, page_no: int) -> list[tuple[int, pymupdf.Rect | None]]:
    """(xref, riquadro standard) delle annotazioni di commento, in ordine di sovrapposizione."""
    page = pdf[page_no]
    pl = _placement()
    m = pl.pdf_to_page_matrix(page) if pl is not None else None
    out = []
    rotated = bool(page.rotation)
    for x, t, _n in page.annot_xrefs():
        if t in NON_COMMENT_TYPES:
            continue
        raw = _array(pdf, x, "Rect")
        if not raw or len(raw) != 4:
            out.append((x, None))
        elif rotated and t in ICON_TYPES and _no_rotate(pdf, x):
            a = find_annot(page, x)     # icona diritta: il riquadro visualizzato non è /Rect
            out.append((x, pymupdf.Rect(a.rect) if a is not None else None))
        elif m is not None:
            out.append((x, pymupdf.Rect(pymupdf.Point(raw[0], raw[1]) * m,
                                        pymupdf.Point(raw[2], raw[3]) * m).normalize()))
        else:
            out.append((x, from_pdf_rect(page, raw)))
    return out


def annot_at(pdf: pymupdf.Document, page_no: int, point, tolerance: float = 4.0,
             rects: list | None = None) -> int | None:
    """Xref dell'annotazione più in alto sotto il punto (None se nessuna)."""
    page = pdf[page_no]
    p = pymupdf.Point(point)
    hit = None
    # preselezione dai riquadri (/Rect): si caricano solo le annotazioni vicine al punto
    if rects is None:
        rects = annot_rects(pdf, page_no)
    near = [x for x, r in rects
            if r is None or (r + (-tolerance - 2, -tolerance - 2, tolerance + 2, tolerance + 2)).contains(p)]
    for x in near:
        annot = find_annot(page, x)
        if annot is None:
            continue
        t = annot.type[0]
        if t in NON_COMMENT_TYPES or _silent(pdf, annot):
            continue
        r = annot.rect
        if not (r + (-tolerance, -tolerance, tolerance, tolerance)).contains(p):
            continue
        if t in (pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_POLY_LINE, pymupdf.PDF_ANNOT_INK):
            verts = annot.vertices or []
            strokes = verts if t == pymupdf.PDF_ANNOT_INK else [verts]
            tol = tolerance + (annot.border or {}).get("width", 1)
            ok = False
            for s in strokes:
                pts = [pymupdf.Point(v) for v in s]
                if len(pts) == 1 and abs(p - pts[0]) <= tol:
                    ok = True
                for i in range(len(pts) - 1):
                    if _dist_to_segment(p, pts[i], pts[i + 1]) <= tol:
                        ok = True
                        break
                if ok:
                    break
            if not ok:
                continue
        elif t in (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_STRIKE_OUT,
                   pymupdf.PDF_ANNOT_SQUIGGLY):
            verts = annot.vertices or []
            quads = [pymupdf.Quad(*verts[i:i + 4]).rect for i in range(0, len(verts) - 3, 4)]
            if quads and not any((q + (-1, -1, 1, 1)).contains(p) for q in quads):
                continue
        hit = annot.xref
    return hit


# ------------------------------------------------------------------ modifica
def set_style(pdf: pymupdf.Document, page_no: int, xref: int, **props) -> None:
    _set_style(pdf, page_no, xref, **props)
    keep_rotation(pdf, page_no, xref)


def _set_style(pdf: pymupdf.Document, page_no: int, xref: int, *, color=..., fill=..., opacity=None,
               width=None, dash=None, cloud=None, fontsize=None, text_color=...) -> None:
    page = pdf[page_no]
    annot = get_annot(page, xref)
    t = annot.type[0]
    if t == pymupdf.PDF_ANNOT_STAMP and stamp_spec_of(pdf, xref) is not None:
        if color is not ... and color is not None:
            pdf.xref_set_key(xref, "C", "[{:.3f} {:.3f} {:.3f}]".format(*_rgb(color)))
        if opacity is not None:
            annot.set_opacity(max(0.05, min(1.0, float(opacity))))
            annot.update()
        touch(page.load_annot(xref))
        refresh_stamp(pdf, page_no, xref)
        return
    if t == pymupdf.PDF_ANNOT_STAMP and pdf.xref_get_key(xref, STICKER_KEY)[0] == "name":
        if opacity is not None:
            annot.set_opacity(max(0.0, min(1.0, float(opacity))))
        touch(annot)
        annot.update()
        refresh_sticker(pdf, page_no, xref)
        return
    if t == pymupdf.PDF_ANNOT_FREE_TEXT:
        _set_freetext_style(pdf, page, annot, color=color, fill=fill, opacity=opacity, width=width, dash=dash,
                            fontsize=fontsize, text_color=text_color)
        return
    if color is not ... and color is not None:
        annot.set_colors(stroke=_rgb(color))
        if t == pymupdf.PDF_ANNOT_LINE and annot.line_ends and any(annot.line_ends) and \
                pdf.xref_get_key(xref, "IC")[0] != "null":
            annot.set_colors(fill=_rgb(color))
    if fill is not ... and t in (pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_POLYGON):
        if fill is None:
            pdf.xref_set_key(xref, "IC", "null")
            annot = page.load_annot(xref)
        else:
            annot.set_colors(fill=_rgb(fill))
    if opacity is not None:
        annot.set_opacity(max(0.05, min(1.0, float(opacity))))
    if width is not None or dash is not None or cloud is not None:
        border = annot.border or {}
        w = float(width) if width is not None else border.get("width", 1)
        dashes = DASH_STYLES.get(dash) if dash is not None else (border.get("dashes") or None)
        clouds = (1 if cloud else 0) if cloud is not None else border.get("clouds", 0)
        if t not in (pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_SQUARE, pymupdf.PDF_ANNOT_CIRCLE):
            clouds = 0
        annot.set_border(width=max(0.0, w), dashes=dashes or [], clouds=max(0, clouds or 0))
    touch(annot)
    annot.update()


def _set_freetext_style(pdf, page, annot, *, color, fill, opacity, width, dash, fontsize, text_color) -> None:
    xref = annot.xref
    if color is not ...:
        if color is None:
            pdf.xref_set_key(xref, FT_BORDER_KEY, "null")
        else:
            pdf.xref_set_key(xref, FT_BORDER_KEY, "[{:.3f} {:.3f} {:.3f}]".format(*_rgb(color)))
    if fill is not ...:
        if fill is None:
            pdf.xref_set_key(xref, "C", "null")
        else:
            pdf.xref_set_key(xref, "C", "[{:.3f} {:.3f} {:.3f}]".format(*_rgb(fill)))
    da = pdf.xref_get_key(xref, "DA")[1] or "0 g /Helv 12 Tf"
    m = re.search(r"/(\S+)\s+([\d.]+)\s+Tf", da)
    font = m.group(1) if m else "Helv"
    size = float(fontsize) if fontsize is not None else (float(m.group(2)) if m else 12.0)
    tc = text_color if text_color is not ... else None
    if tc is None:
        cm = re.search(r"([\d.]+\s+[\d.]+\s+[\d.]+)\s+rg", da)
        gm = re.search(r"([\d.]+)\s+g", da)
        tc = tuple(float(v) for v in cm.group(1).split()) if cm else ((float(gm.group(1)),) * 3 if gm else (0, 0, 0))
    r, g, b = _rgb(tc)
    pdf.xref_set_key(xref, "DA", _pdf_string(f"{r:.3f} {g:.3f} {b:.3f} rg /{font} {size:g} Tf"))
    annot = page.load_annot(xref)
    if opacity is not None:
        annot.set_opacity(max(0.0, min(1.0, float(opacity))))
    if width is not None or dash is not None:
        border = annot.border or {}
        w = float(width) if width is not None else border.get("width", 0)
        dashes = DASH_STYLES.get(dash) if dash is not None else (border.get("dashes") or None)
        annot.set_border(width=max(0.0, w), dashes=dashes or [])
        if w > 0 and pdf.xref_get_key(xref, FT_BORDER_KEY)[0] != "array":
            pdf.xref_set_key(xref, FT_BORDER_KEY, "[0 0 0]")
    touch(annot)
    _dirty_update(annot)
    refresh_freetext(pdf, page.number, xref)


def get_rich_text(pdf: pymupdf.Document, xref: int) -> str:
    k, v = pdf.xref_get_key(xref, "RC")
    if k == "string":
        try:
            return _decode_text_string(v)
        except Exception:
            return ""
    return ""


def _decode_text_string(v: str) -> str:
    v = v.strip()
    if v.startswith("<") and v.endswith(">") and re.fullmatch(r"[0-9A-Fa-f\s]*", v[1:-1]):
        raw = bytes.fromhex(v[1:-1])
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be", errors="replace")
        return raw.decode("latin-1")
    return _unescape_pdf_string(v)


def set_contents(pdf: pymupdf.Document, page_no: int, xref: int, text: str, rich: str | None = None) -> None:
    """Testo del commento (/Contents) ed eventuale testo formattato XHTML (/RC)."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    if annot.flags & FLAG_LOCKED_CONTENTS:
        raise AnnotationError("Il contenuto dell'annotazione è bloccato.")
    annot.set_info(content=text, modDate=pdf_now())
    if annot.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT:
        mupdf.pdf_set_annot_contents(annot.this, text)
        pdf.xref_set_key(xref, "RC", pymupdf.get_pdf_str(rich) if rich else "null")
        _dirty_update(get_annot(page, xref))
        refresh_freetext(pdf, page_no, xref)
    else:
        annot.update()
        if annot.type[0] == pymupdf.PDF_ANNOT_STAMP:
            refresh_stamp(pdf, page_no, xref)
        # /RC dopo l'aggiornamento (che altrimenti lo rimuove)
        pdf.xref_set_key(xref, "RC", pymupdf.get_pdf_str(rich) if rich else "null")


def set_author(pdf: pymupdf.Document, page_no: int, xref: int, author: str) -> None:
    page = pdf[page_no]
    annot = get_annot(page, xref)
    annot.set_info(title=author, modDate=pdf_now())
    annot.update()
    if annot.type[0] == pymupdf.PDF_ANNOT_STAMP:
        refresh_stamp(pdf, page_no, xref)


def _geometry(annot: pymupdf.Annot) -> dict:
    t = annot.type[0]
    g: dict = {"rect": pymupdf.Rect(annot.rect)}
    if t in (pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE):
        g["vertices"] = [pymupdf.Point(v) for v in annot.vertices or []]
    elif t == pymupdf.PDF_ANNOT_INK:
        g["ink"] = [[pymupdf.Point(v) for v in s] for s in annot.vertices or []]
    elif t in (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_STRIKE_OUT,
               pymupdf.PDF_ANNOT_SQUIGGLY):
        v = annot.vertices or []
        g["quads"] = [pymupdf.Quad(*v[i:i + 4]) for i in range(0, len(v) - 3, 4)]
    return g


def _set_vertices(annot: pymupdf.Annot, pts: Sequence[pymupdf.Point]) -> None:
    t = annot.type[0]
    if t == pymupdf.PDF_ANNOT_LINE:
        mupdf.pdf_set_annot_line(annot.this, _fz_point(pts[0]), _fz_point(pts[-1]))
    else:
        mupdf.pdf_clear_annot_vertices(annot.this)
        for p in pts:
            mupdf.pdf_add_annot_vertex(annot.this, _fz_point(p))


def _set_ink(annot: pymupdf.Annot, strokes: Sequence[Sequence[pymupdf.Point]]) -> None:
    mupdf.pdf_clear_annot_ink_list(annot.this)
    for s in strokes:
        mupdf.pdf_add_annot_ink_list_stroke(annot.this)
        for p in s:
            mupdf.pdf_add_annot_ink_list_stroke_vertex(annot.this, _fz_point(p))


def _set_quads(annot: pymupdf.Annot, quads: Sequence[pymupdf.Quad]) -> None:
    mupdf.pdf_clear_annot_quad_points(annot.this)
    for q in quads:
        fq = mupdf.fz_make_quad(q.ul.x, q.ul.y, q.ur.x, q.ur.y, q.ll.x, q.ll.y, q.lr.x, q.lr.y)
        mupdf.pdf_add_annot_quad_point(annot.this, fq)


def transform_annot(pdf: pymupdf.Document, page_no: int, xref: int, new_rect) -> None:
    """Sposta/ridimensiona un'annotazione portando il suo rettangolo in `new_rect`
    (i vertici di linee, poligoni, disegni ed evidenziazioni vengono scalati)."""
    page = pdf[page_no]
    new_rect = pymupdf.Rect(new_rect).normalize()
    with unrotated(page):
        annot = get_annot(page, xref)
        if annot.flags & pymupdf.PDF_ANNOT_IS_LOCKED:
            raise AnnotationError("L'annotazione è bloccata: non si può spostare né ridimensionare.")
        t = annot.type[0]
        g = _geometry(annot)
        old = g["rect"]
        if old.width <= 0 or old.height <= 0:
            raise AnnotationError("Annotazione non valida.")
        if t == pymupdf.PDF_ANNOT_STAMP and is_sticker(pdf, xref) and min(new_rect.width, new_rect.height) < STICKER_MIN:
            k = STICKER_MIN / max(min(new_rect.width, new_rect.height), 0.01)   # minimo 8 pt, in proporzione
            new_rect = pymupdf.Rect(new_rect.x0, new_rect.y0, new_rect.x0 + new_rect.width * k,
                                    new_rect.y0 + new_rect.height * k)
        _angle, base = _rotation(pdf, xref)
        if base is not None:
            k = new_rect.width / old.width
            pdf.xref_set_key(xref, SIZE_KEY, f"[{base[0] * k:.3f} {base[1] * k:.3f}]")
        sx = new_rect.width / old.width
        sy = new_rect.height / old.height

        def tr(p: pymupdf.Point) -> pymupdf.Point:
            return pymupdf.Point(new_rect.x0 + (p.x - old.x0) * sx, new_rect.y0 + (p.y - old.y0) * sy)

        if "vertices" in g:
            _set_vertices(annot, [tr(p) for p in g["vertices"]])
        elif "ink" in g:
            _set_ink(annot, [[tr(p) for p in s] for s in g["ink"]])
        elif "quads" in g:
            _set_quads(annot, [pymupdf.Quad(tr(q.ul), tr(q.ur), tr(q.ll), tr(q.lr)) for q in g["quads"]])
        elif t == pymupdf.PDF_ANNOT_FREE_TEXT:
            cl = pdf.xref_get_key(xref, "CL")
            if cl[0] == "array":
                # sposta il riquadro del testo e la linea di richiamo insieme
                nums = [float(v) for v in cl[1].strip("[]").split()]
                pts = [pymupdf.Point(*from_pdf_point(page, nums[i], nums[i + 1])) for i in range(0, len(nums) - 1, 2)]
                inner = _freetext_inner(page, annot)
                dx, dy = new_rect.x0 - old.x0, new_rect.y0 - old.y0
                moved_pts = [p + (dx, dy) for p in pts]
                inner2 = pymupdf.Rect(inner.x0 + dx, inner.y0 + dy, inner.x0 + dx + inner.width * sx,
                                      inner.y0 + dy + inner.height * sy)
                _set_callout(pdf, page, xref, inner2, moved_pts)
                annot = page.load_annot(xref)
            else:
                _set_rect_exact(annot, new_rect)
        else:
            _set_rect_exact(annot, new_rect)
        touch(annot)
        keep_ap = pdf.xref_get_key(xref, "AP") if pdf.xref_get_key(xref, PAGE_STAMP_KEY)[0] != "null" else None
        _dirty_update(annot)
        if keep_ap is not None:      # timbro da pagina PDF: aspetto vettoriale proprio, solo riposizionato
            pdf.xref_set_key(xref, "AP", keep_ap[1])
            pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*to_pdf_rect(page, new_rect)))
        if t == pymupdf.PDF_ANNOT_STAMP and stamp_spec_of(pdf, xref) is not None:
            refresh_stamp(pdf, page_no, xref)
        elif t == pymupdf.PDF_ANNOT_STAMP and pdf.xref_get_key(xref, STICKER_KEY)[0] == "name":
            refresh_sticker(pdf, page_no, xref)
        elif base is not None:
            _apply_rotation(pdf, page, xref)
        if t == pymupdf.PDF_ANNOT_FREE_TEXT:
            refresh_freetext(pdf, page_no, xref)
        if is_area_markup(pdf, xref):
            _fit_area_rect(pdf, page, xref)
    # sposta anche le risposte (stessa posizione del commento)
    for a in iter_annots(page):
        if a.irt_xref == xref and _silent(pdf, a):
            try:
                r = a.rect
                a.set_rect(pymupdf.Rect(new_rect.x0, new_rect.y0, new_rect.x0 + r.width, new_rect.y0 + r.height))
                a.update()
            except Exception:
                pass


def _set_rect_exact(annot: pymupdf.Annot, rect: pymupdf.Rect) -> None:
    """Imposta /Rect compensando il margine che il motore aggiunge al tratto."""
    annot.set_rect(rect)
    annot.update()
    got = annot.rect
    d = (got.x0 - rect.x0, got.y0 - rect.y0, got.x1 - rect.x1, got.y1 - rect.y1)
    if any(abs(v) > 0.01 for v in d):
        fixed = pymupdf.Rect(rect.x0 - d[0], rect.y0 - d[1], rect.x1 - d[2], rect.y1 - d[3])
        if fixed.width > 1 and fixed.height > 1:
            annot.set_rect(fixed)
            annot.update()


def _freetext_inner(page: pymupdf.Page, annot: pymupdf.Annot) -> pymupdf.Rect:
    """Riquadro del testo di una casella con richiamo (Rect meno /RD)."""
    r = annot.rect
    k, v = page.parent.xref_get_key(annot.xref, "RD")
    if k == "array":
        try:
            left, top, right, bottom = (float(x) for x in v.strip("[]").split())
            # /RD è in coordinate PDF: sinistra, basso, destra, alto
            return pymupdf.Rect(r.x0 + left, r.y0 + bottom, r.x1 - right, r.y1 - top)
        except ValueError:
            pass
    return r


def _set_callout(pdf, page, xref, inner: pymupdf.Rect, pts: Sequence[pymupdf.Point]) -> None:
    allpts = [inner.tl, inner.br] + list(pts)
    xs = [p.x for p in allpts]
    ys = [p.y for p in allpts]
    outer = pymupdf.Rect(min(xs) - 6, min(ys) - 6, max(xs) + 6, max(ys) + 6)
    pdf_outer = to_pdf_rect(page, outer)
    pdf.xref_set_key(xref, "Rect", "[{:.2f} {:.2f} {:.2f} {:.2f}]".format(*pdf_outer))
    left = inner.x0 - outer.x0
    right = outer.x1 - inner.x1
    top = inner.y0 - outer.y0
    bottom = outer.y1 - inner.y1
    pdf.xref_set_key(xref, "RD", f"[{left:.2f} {bottom:.2f} {right:.2f} {top:.2f}]")
    flat = []
    for p in pts:
        flat.extend(to_pdf_point(page, p.x, p.y))
    pdf.xref_set_key(xref, "CL", "[" + " ".join(f"{v:.2f}" for v in flat) + "]")


VERTEX_TYPES = (pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE)


def vertex_points(pdf: pymupdf.Document, page_no: int, xref: int) -> list[pymupdf.Point]:
    """Punti modificabili: vertici di linee e poligoni, punta dei richiami."""
    page = pdf[page_no]
    annot = find_annot(page, xref)
    if annot is None:
        return []
    if annot.type[0] in VERTEX_TYPES:
        return [pymupdf.Point(v) for v in annot.vertices or []]
    if annot.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT and _intent(pdf, xref) == "FreeTextCallout":
        cl = _array(pdf, xref, "CL")
        if cl and len(cl) >= 2:
            return [pymupdf.Point(*from_pdf_point(page, cl[0], cl[1]))]
    return []


def set_vertex(pdf: pymupdf.Document, page_no: int, xref: int, index: int, point) -> None:
    """Sposta un vertice (o la punta di un richiamo, indice 0) lasciando invariato il resto."""
    page = pdf[page_no]
    p = pymupdf.Point(point)
    with unrotated(page):
        annot = get_annot(page, xref)
        if annot.flags & pymupdf.PDF_ANNOT_IS_LOCKED:
            raise AnnotationError("L'annotazione è bloccata: non si può spostare né ridimensionare.")
        t = annot.type[0]
        if t in VERTEX_TYPES:
            pts = [pymupdf.Point(v) for v in annot.vertices or []]
            if not 0 <= index < len(pts):
                raise AnnotationError("Vertice inesistente.")
            pts[index] = p
            _set_vertices(annot, pts)
            touch(annot)
            _dirty_update(annot)
        elif t == pymupdf.PDF_ANNOT_FREE_TEXT:
            cl = _array(pdf, xref, "CL") or []
            pts = [pymupdf.Point(*from_pdf_point(page, cl[i], cl[i + 1])) for i in range(0, len(cl) - 1, 2)]
            if not pts:
                raise AnnotationError("Vertice inesistente.")
            inner = _freetext_inner(page, annot)
            pts[0] = p
            _set_callout(pdf, page, xref, inner, pts)
            annot = get_annot(page, xref)
            touch(annot)
            refresh_freetext(pdf, page_no, xref)
        else:
            raise AnnotationError("Vertice inesistente.")
    update_measure(pdf, page_no, xref)


def edit_vertices(pdf: pymupdf.Document, page_no: int, xref: int, point, add: bool) -> None:
    """Aggiunge un vertice sul lato più vicino al punto oppure rimuove il vertice più vicino."""
    page = pdf[page_no]
    p = pymupdf.Point(point)
    with unrotated(page):
        annot = get_annot(page, xref)
        if annot.type[0] not in (pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE):
            raise AnnotationError("Vertice inesistente.")
        if annot.flags & pymupdf.PDF_ANNOT_IS_LOCKED:
            raise AnnotationError("L'annotazione è bloccata: non si può spostare né ridimensionare.")
        pts = [pymupdf.Point(v) for v in annot.vertices or []]
        closed = annot.type[0] == pymupdf.PDF_ANNOT_POLYGON
        if add:
            segs = list(range(len(pts) - 1)) + ([len(pts) - 1] if closed else [])
            best = min(segs, key=lambda i: _dist_to_segment(p, pts[i], pts[(i + 1) % len(pts)]))
            pts.insert(best + 1, p)
        else:
            if len(pts) <= (3 if closed else 2):
                raise AnnotationError("Servono almeno {n} punti.".replace("{n}", "3" if closed else "2"))
            pts.pop(min(range(len(pts)), key=lambda i: abs(pts[i] - p)))
        _set_vertices(annot, pts)
        touch(annot)
        _dirty_update(annot)
    update_measure(pdf, page_no, xref)


def set_cloud_intensity(pdf: pymupdf.Document, page_no: int, xref: int, intensity: int) -> None:
    """Intensità degli archi del bordo a nuvola (0 = bordo continuo, 1–2)."""
    page = pdf[page_no]
    annot = get_annot(page, xref)
    border = annot.border or {}
    annot.set_border(width=border.get("width", 1), dashes=border.get("dashes") or [], clouds=max(0, min(2, intensity)))
    touch(annot)
    annot.update()


def move_annot(pdf: pymupdf.Document, page_no: int, xref: int, dx: float, dy: float) -> None:
    page = pdf[page_no]
    r = pymupdf.Rect(get_annot(page, xref).rect)
    transform_annot(pdf, page_no, xref, r + (dx, dy, dx, dy))


def delete_annot(pdf: pymupdf.Document, page_no: int, xref: int, force: bool = False) -> None:
    """Elimina un'annotazione con le sue risposte, i suoi stati e la sua popup."""
    page = pdf[page_no]
    target = get_annot(page, xref)
    if not force and target.flags & pymupdf.PDF_ANNOT_IS_LOCKED:
        raise AnnotationError("L'annotazione è bloccata: non si può eliminare.")
    victims = {xref}
    if target.irt_xref and pdf.xref_get_key(xref, "RT")[1] == "/Group":
        victims.add(target.irt_xref)   # elemento di un gruppo: si elimina tutto il gruppo
    del target
    changed = True
    annots = list(iter_annots(page))
    while changed:
        changed = False
        for a in annots:
            if a.xref not in victims and a.irt_xref in victims:
                victims.add(a.xref)
                changed = True
    for x in sorted(victims, reverse=True):
        a = find_annot(page, x)
        if a is not None:
            page.delete_annot(a)


def _annots_array(pdf: pymupdf.Document, page: pymupdf.Page) -> tuple[int, str, list[int]]:
    """(xref dell'oggetto che contiene l'array, chiave, xref delle annotazioni)."""
    kind, value = pdf.xref_get_key(page.xref, "Annots")
    if kind == "xref":
        holder = int(value.split()[0])
        return holder, "", [int(x) for x in re.findall(r"(\d+)\s+0\s+R", pdf.xref_object(holder))]
    return page.xref, "Annots", [int(x) for x in re.findall(r"(\d+)\s+0\s+R", value or "")]


def _write_annots_array(pdf, holder: int, key: str, refs: list[int]) -> None:
    arr = "[" + " ".join(f"{x} 0 R" for x in refs) + "]"
    if key:
        pdf.xref_set_key(holder, key, arr)
    else:
        pdf.update_object(holder, arr)


def restack(pdf: pymupdf.Document, page_no: int, xref: int, front: bool) -> None:
    """Porta in primo piano (fine dell'array /Annots) o sullo sfondo (inizio)."""
    page = pdf[page_no]
    holder, key, refs = _annots_array(pdf, page)
    if xref not in refs:
        raise AnnotationError("L'annotazione non esiste più.")
    refs.remove(xref)
    if front:
        refs.append(xref)
    else:
        refs.insert(0, xref)
    _write_annots_array(pdf, holder, key, refs)


FLAG_PRINT = pymupdf.PDF_ANNOT_IS_PRINT
FLAG_LOCKED = pymupdf.PDF_ANNOT_IS_LOCKED
FLAG_READ_ONLY = pymupdf.PDF_ANNOT_IS_READ_ONLY
FLAG_LOCKED_CONTENTS = 512
FLAG_HIDDEN = pymupdf.PDF_ANNOT_IS_HIDDEN


def set_flag(pdf: pymupdf.Document, page_no: int, xref: int, flag: int, on: bool) -> None:
    page = pdf[page_no]
    annot = get_annot(page, xref)
    flags = annot.flags | flag if on else annot.flags & ~flag
    annot.set_flags(flags)
    touch(annot)
    annot.update()
    keep_rotation(pdf, page_no, xref)


def duplicate_annot(pdf: pymupdf.Document, src_page: int, xref: int, dst_page: int, dx: float = 0.0,
                    dy: float = 0.0, author: str | None = None, keep_identity: bool = False) -> int:
    """Copia un'annotazione (dizionario e aspetto) su una pagina dello stesso documento.

    Con `keep_identity` conserva /NM, autore e date (taglia+incolla), altrimenti la copia
    riceve un nuovo /NM e l'autore e le date correnti."""
    page = pdf[src_page]
    subtype = get_annot(page, xref).type[0]
    dst = page if dst_page == src_page else pdf[dst_page]
    # annotazione creata dal motore (registrata nella pagina aperta) e poi riempita con il dizionario
    with unrotated(dst):
        created = mupdf.pdf_create_annot(dst._pdf_page(), subtype)
        new = mupdf.pdf_to_num(mupdf.pdf_annot_obj(created))
    for key in pdf.xref_get_keys(xref):
        if key in ("P", "Popup", "IRT", "RT", "Parent", "Type", "Subtype"):
            continue
        kind, value = pdf.xref_get_key(xref, key)
        if kind == "null":
            continue
        if kind == "string":
            value = pymupdf.get_pdf_str(value)
        pdf.xref_set_key(new, key, value)
    for key in ("Popup", "IRT", "RT"):
        pdf.xref_set_key(new, key, "null")
    kind, value = pdf.xref_get_key(xref, "AP/N")
    if kind == "xref":
        src_ap = int(value.split()[0])
        ap = pdf.get_new_xref()
        pdf.update_object(ap, pdf.xref_object(src_ap, compressed=False))
        pdf.update_stream(ap, pdf.xref_stream(src_ap) or b"")
        pdf.xref_set_key(new, "AP", f"<</N {ap} 0 R>>")
    try:   # l'aspetto copiato è già valido: il motore non lo deve rigenerare
        created.m_internal.needs_new_ap = 0
    except Exception:
        pass
    annot = get_annot(dst, new)
    if not keep_identity:
        now = pdf_now()
        annot.set_info(title=author if author is not None else default_author(), creationDate=now, modDate=now)
        pdf.xref_set_key(new, "NM", _pdf_string(new_name()))
    if dx or dy:
        with unrotated(dst):
            annot = get_annot(dst, new)
            r = pymupdf.Rect(annot.rect)
        transform_annot(pdf, dst_page, new, r + (dx, dy, dx, dy))
    return new


def copy_annot_to(src: pymupdf.Document, src_page: int, xref: int, dst: pymupdf.Document, dst_page: int,
                  author: str | None = None, keep_identity: bool = False) -> int:
    """Copia un'annotazione in un altro documento: dizionario, aspetto e risorse (caratteri, immagini,
    file incorporati) vengono innestati nel documento di destinazione."""
    sp = src[src_page]
    subtype = get_annot(sp, xref).type[0]
    obj = mupdf.pdf_load_object(pymupdf._as_pdf_document(src), xref)
    copy = mupdf.pdf_copy_dict(obj)
    for key in ("P", "Popup", "IRT", "RT", "Parent"):
        mupdf.pdf_dict_dels(copy, key)
    grafted = mupdf.pdf_graft_object(pymupdf._as_pdf_document(dst), copy)
    dp = dst[dst_page]
    with unrotated(dp):
        created = mupdf.pdf_create_annot(dp._pdf_page(), subtype)
        target = mupdf.pdf_annot_obj(created)
        for i in range(mupdf.pdf_dict_len(grafted)):
            k = mupdf.pdf_dict_get_key(grafted, i)
            if mupdf.pdf_to_name(k) in ("Type", "Subtype"):
                continue
            mupdf.pdf_dict_put(target, k, mupdf.pdf_dict_get_val(grafted, i))
        try:
            created.m_internal.needs_new_ap = 0
        except Exception:
            pass
        new = mupdf.pdf_to_num(target)
    if not keep_identity:
        now = pdf_now()
        get_annot(dp, new).set_info(title=author if author is not None else default_author(), creationDate=now,
                                    modDate=now)
        dst.xref_set_key(new, "NM", _pdf_string(new_name()))
    # il riquadro resta nella stessa posizione relativa, ricondotto dentro la pagina di destinazione
    r = _std_rect(dst, dp, new)
    box = pymupdf.Rect(0, 0, dp.mediabox.width, dp.mediabox.height)
    dx = min(0.0, box.x1 - r.x1) + max(0.0, box.x0 - r.x0)
    dy = min(0.0, box.y1 - r.y1) + max(0.0, box.y0 - r.y0)
    if dx or dy:
        transform_annot(dst, dst_page, new, r + (dx, dy, dx, dy))
    return new


def move_thread(pdf: pymupdf.Document, src_page: int, xref: int, dst_page: int, dx: float = 0.0,
                dy: float = 0.0) -> int:
    """Sposta un commento con risposte e stati (taglia+incolla): identità conservate, /IRT ricollegati."""
    page = pdf[src_page]
    order = [xref]
    i = 0
    while i < len(order):          # discendenti in ampiezza (catene cicliche interrotte)
        parent = order[i]
        for x, t, _n in page.annot_xrefs():
            if t not in NON_COMMENT_TYPES and x not in order and _irt_of(pdf, x) == parent:
                order.append(x)
        i += 1
    mapping: dict[int, int] = {}
    for x in order:
        parent = _irt_of(pdf, x) if x != xref else 0
        new = duplicate_annot(pdf, src_page, x, dst_page, dx, dy, keep_identity=True)
        mapping[x] = new
        if parent in mapping:
            dst = pdf[dst_page]
            get_annot(dst, new).set_irt_xref(mapping[parent])
            k, v = pdf.xref_get_key(x, "RT")
            if k == "name":
                pdf.xref_set_key(new, "RT", v)
    delete_annot(pdf, src_page, xref, force=True)
    return mapping[xref]


def delete_all(pdf: pymupdf.Document, pages: Iterable[int] | None = None) -> int:
    n = 0
    for page_no in (pages if pages is not None else range(pdf.page_count)):
        page = pdf[page_no]
        xrefs = [a.xref for a in iter_annots(page) if a.type[0] not in NON_COMMENT_TYPES]
        for x in xrefs:
            a = find_annot(page, x)
            if a is not None:
                page.delete_annot(a)
                n += 1
    return n


# ------------------------------------------------------------------ risposte e stato
def add_reply(pdf: pymupdf.Document, page_no: int, parent_xref: int, text: str, author: str | None = None,
              date: str | None = None) -> int:
    page = pdf[page_no]
    parent = get_annot(page, parent_xref)
    if parent.type[0] in NON_COMMENT_TYPES:
        raise AnnotationError("Non si può rispondere a collegamenti o campi modulo.")
    r = pymupdf.Rect(parent.rect)
    with unrotated(page):
        annot = page.add_text_annot(r.tl, text, icon="Comment")
        annot.set_rect(r)
    annot.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_PRINT)
    annot.set_irt_xref(parent_xref)
    xref = _finish(annot, author, text, date=date, subject="")
    pdf.xref_set_key(xref, "RT", "/R")
    return xref


def set_status(pdf: pymupdf.Document, page_no: int, xref: int, state: str, author: str | None = None,
               model: str = "Review", date: str | None = None) -> int:
    """Registra uno stato (risposta di stato nascosta, come da standard PDF)."""
    page = pdf[page_no]
    parent = get_annot(page, xref)
    annot = page.add_text_annot(parent.rect.tl, "", icon="Comment")
    annot.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_PRINT)
    annot.set_irt_xref(xref)
    who = author if author is not None else default_author()
    label = _(STATE_TEXTS.get(state, state))
    text = _("{state} impostato da {who}").format(state=label, who=who) if who else label
    new = _finish(annot, who, text, date=date, subject="")
    pdf.xref_set_key(new, "State", "/" + _name_token(state))
    pdf.xref_set_key(new, "StateModel", "/" + _name_token(model))
    return new


def _name_token(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", text) or "None"


def set_marked(pdf: pymupdf.Document, page_no: int, xref: int, marked: bool, author: str | None = None) -> int:
    return set_status(pdf, page_no, xref, "Marked" if marked else "Unmarked", author, model="Marked")


# ------------------------------------------------------------------ gomma
def erase_ink(pdf: pymupdf.Document, page_no: int, point, radius: float = 6.0, whole: bool = False) -> int:
    """Cancella i tratti a mano libera vicini al punto. Spezza i tratti attraversati;
    le annotazioni che restano vuote vengono eliminate. Restituisce i tratti toccati."""
    page = pdf[page_no]
    p = pymupdf.Point(point)
    touched = 0
    with unrotated(page):
        for annot in list(iter_annots(page, [pymupdf.PDF_ANNOT_INK])):
            r = annot.rect + (-radius, -radius, radius, radius)
            if not r.contains(p) or annot.flags & (pymupdf.PDF_ANNOT_IS_LOCKED | pymupdf.PDF_ANNOT_IS_READ_ONLY):
                continue
            strokes = [_densify([pymupdf.Point(v) for v in s], max(0.5, radius / 3))
                       for s in annot.vertices or []]
            new: list[list[pymupdf.Point]] = []
            hit = False

            def keep(piece):
                if len(piece) > 1:
                    # si tengono i vertici originali e gli estremi del tratto rimasto
                    new.append([v for i, (v, orig) in enumerate(piece) if orig or i in (0, len(piece) - 1)])

            for s in strokes:
                if whole:
                    if any(abs(v - p) <= radius for v, _o in s):
                        hit = True
                    else:
                        keep(s)
                    continue
                cur: list = []
                for v, orig in s:
                    if abs(v - p) <= radius:
                        hit = True
                        keep(cur)
                        cur = []
                    else:
                        cur.append((v, orig))
                keep(cur)
            if not hit:
                continue
            touched += 1
            if not new:
                xref = annot.xref
                delete_annot(pdf, page_no, xref, force=True)
                continue
            _set_ink(annot, new)
            touch(annot)
            _dirty_update(annot)
    return touched


def _densify(points: list[pymupdf.Point], step: float) -> list[tuple[pymupdf.Point, bool]]:
    """Punti intermedi ogni `step` punti; il booleano indica i vertici originali."""
    if len(points) < 2:
        return [(p, True) for p in points]
    out = [(points[0], True)]
    for a, b in zip(points, points[1:]):
        n = int(abs(b - a) / step)
        for k in range(1, n + 1):
            out.append((a + (b - a) * (k / (n + 1)), False))
        out.append((b, True))
    return out


# ------------------------------------------------------------------ appiattimento
def flatten(pdf: pymupdf.Document, pages: Iterable[int] | None = None,
            only: Iterable[tuple[int, int]] | None = None) -> int:
    """Rende le annotazioni parte del contenuto della pagina (non più modificabili).

    Con `pages` si appiattiscono solo quelle pagine. I campi modulo restano."""
    selected = set(only) if only is not None else None
    if selected is not None:
        return _flatten_selected(pdf, selected)
    targets = set(pages) if pages is not None else set(range(pdf.page_count))
    count = 0
    for i in targets:
        page = pdf[i]
        count += sum(1 for a in iter_annots(page) if a.type[0] not in NON_COMMENT_TYPES
                     and (selected is None or (i, a.xref) in selected))
    if not count:
        return 0
    # si nascondono temporaneamente le annotazioni da non appiattire: quelle delle altre
    # pagine e quelle non stampabili (che restano annotazioni)
    saved: dict[int, tuple[str, str]] = {}
    partial: dict[int, list[int]] = {}     # pagine appiattite in parte: xref da rimettere
    _strip_hidden(pdf, targets)
    try:
        for i in range(pdf.page_count):
            page = pdf[i]
            xref = page.xref
            if i not in targets:
                saved[xref] = pdf.xref_get_key(xref, "Annots")
                pdf.xref_set_key(xref, "Annots", "null")
                page = pdf.reload_page(page)
                continue
            keep = [a.xref for a in iter_annots(page)
                    if a.type[0] in NON_COMMENT_TYPES or a.flags & pymupdf.PDF_ANNOT_IS_PRINT
                    or a.type[0] == pymupdf.PDF_ANNOT_POPUP]
            noprint = [a.xref for a in iter_annots(page) if a.xref not in keep]
            if selected is not None:
                # solo le annotazioni scelte (e le loro popup); le altre restano annotazioni
                chosen = {x for p, x in selected if p == i}
                popups = {a.popup_xref for a in iter_annots(page) if a.xref in chosen and a.popup_xref}
                noprint = [a.xref for a in iter_annots(page)
                           if a.xref not in chosen and a.xref not in popups
                           and a.type[0] not in (pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_LINK)
                           or (a.xref in chosen and a.xref not in keep)]
            if noprint:
                count -= len([x for x in noprint if selected is None or (i, x) in selected])
                partial[xref] = noprint
                _, _k, refs = _annots_array(pdf, page)
                pdf.xref_set_key(xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in refs if x not in noprint) + "]")
                page = pdf.reload_page(page)   # il motore rilegge l'elenco delle annotazioni
        # nessuna pagina deve restare aperta: il motore userebbe l'elenco di annotazioni in memoria
        page = None
        import gc
        gc.collect()
        pdf.bake(annots=True, widgets=False)
    finally:
        for xref, (kind, value) in saved.items():
            pdf.xref_set_key(xref, "Annots", value if kind != "null" else "null")
        for xref, keep_refs in partial.items():
            k, v = pdf.xref_get_key(xref, "Annots")
            now = [int(x) for x in re.findall(r"(\d+)\s+0\s+R", v)] if k == "array" else []
            pdf.xref_set_key(xref, "Annots", "[" + " ".join(f"{x} 0 R" for x in keep_refs + now) + "]")
    return count


def _flatten_selected(pdf: pymupdf.Document, selected: set[tuple[int, int]]) -> int:
    """Appiattisce solo le annotazioni scelte: si cuociono in una copia che contiene soltanto
    quelle (senza il contenuto della pagina) e il risultato si sovrappone alla pagina."""
    from foliant.core import placement

    pages = sorted({p for p, _x in selected})
    tmp = pymupdf.open(stream=pdf.tobytes(garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
    count = 0
    try:
        for page_no in range(tmp.page_count):
            tp = tmp[page_no]
            chosen = {x for p, x in selected if p == page_no}
            keep = set()
            for a in iter_annots(tp):
                if a.xref in chosen and a.flags & pymupdf.PDF_ANNOT_IS_PRINT and not _silent(tmp, a):
                    keep.add(a.xref)
            for x in [a.xref for a in iter_annots(tp) if a.xref not in keep
                      and a.type[0] not in (pymupdf.PDF_ANNOT_WIDGET,)]:
                a = find_annot(tp, x)
                if a is not None:
                    tp.delete_annot(a)
            count += len(keep)
            if page_no in pages:
                for cx in tp.get_contents():
                    tmp.update_stream(cx, b" ")
        if not count:
            return 0
        tmp.bake(annots=True, widgets=False)
        for page_no in pages:
            page = pdf[page_no]
            placement.show_pdf_page(page, placement.page_rect(page), tmp, page_no, keep_proportion=False,
                                    overlay=True, as_displayed=False)
            done = {x for p, x in selected if p == page_no}
            for x in list(done):
                a = find_annot(page, x)
                if a is not None and a.flags & pymupdf.PDF_ANNOT_IS_PRINT:
                    delete_annot(pdf, page_no, x, force=True)
    finally:
        tmp.close()
    return count


def _strip_hidden(pdf: pymupdf.Document, pages: Iterable[int]) -> None:
    for i in pages:
        page = pdf[i]
        for a in list(iter_annots(page)):
            if a.type[0] not in NON_COMMENT_TYPES and _silent(pdf, a):
                page.delete_annot(a)


# ------------------------------------------------------------------ XFDF
XFDF_NS = "http://ns.adobe.com/xfdf/"

_XFDF_TAGS = {
    "Text": "text", "FreeText": "freetext", "Highlight": "highlight", "Underline": "underline",
    "StrikeOut": "strikeout", "Squiggly": "squiggly", "Square": "square", "Circle": "circle", "Line": "line",
    "Polygon": "polygon", "PolyLine": "polyline", "Ink": "ink", "Stamp": "stamp",
    "FileAttachment": "fileattachment", "Caret": "caret",
}
_TAG_TYPES = {v: k for k, v in _XFDF_TAGS.items()}


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def _export_rect(pdf: pymupdf.Document, page: pymupdf.Page, a: pymupdf.Annot) -> tuple:
    """/Rect così com'è nel file (per le icone diritte su pagine ruotate differisce da annot.rect)."""
    raw = _array(pdf, a.xref, "Rect")
    if raw and len(raw) == 4:
        return min(raw[0], raw[2]), min(raw[1], raw[3]), max(raw[0], raw[2]), max(raw[1], raw[3])
    return to_pdf_rect(page, a.rect)


def export_xfdf(pdf: pymupdf.Document, filename: str = "", pages: Iterable[int] | None = None) -> str:
    """Esporta i commenti in XFDF (XML standard di interscambio delle annotazioni)."""
    with bulk_geometry():
        return _export_xfdf(pdf, filename, pages)


def _export_xfdf(pdf: pymupdf.Document, filename: str = "", pages: Iterable[int] | None = None) -> str:
    from xml.sax.saxutils import escape, quoteattr

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<xfdf xmlns="{XFDF_NS}" xml:space="preserve">', "<annots>"]
    for page_no in (pages if pages is not None else range(pdf.page_count)):
        page = pdf[page_no]
        names: dict[int, str] = {}
        annots = [a for a in iter_annots(page) if a.type[0] not in NON_COMMENT_TYPES]
        for a in annots:
            names[a.xref] = (a.info or {}).get("id") or f"foliant-{page_no}-{a.xref}"
        for a in annots:
            tag = _XFDF_TAGS.get(a.type[1])
            if tag is None:
                continue
            info = a.info or {}
            attrs = {"page": str(page_no), "name": names[a.xref],
                     "rect": ",".join(_fmt(v) for v in _export_rect(pdf, page, a)),
                     "flags": _xfdf_flags(a.flags)}
            if info.get("title"):
                attrs["title"] = info["title"]
            if info.get("subject"):
                attrs["subject"] = info["subject"]
            if info.get("creationDate"):
                attrs["creationdate"] = info["creationDate"]
            if info.get("modDate"):
                attrs["date"] = info["modDate"]
            colors = a.colors or {}
            stroke = colors.get("stroke")
            if a.type[0] == pymupdf.PDF_ANNOT_FREE_TEXT:
                k, v = pdf.xref_get_key(a.xref, "C")
                if k == "array":
                    try:
                        attrs["color"] = color_to_hex([float(x) for x in v.strip("[]").split()])
                    except ValueError:
                        pass
            elif stroke:
                attrs["color"] = color_to_hex(stroke)
            if colors.get("fill") and a.type[0] != pymupdf.PDF_ANNOT_FREE_TEXT:
                attrs["interior-color"] = color_to_hex(colors["fill"])
            if a.opacity is not None and 0 <= a.opacity < 1:
                attrs["opacity"] = _fmt(a.opacity)
            border = a.border or {}
            if border.get("width", -1) >= 0 and a.type[0] not in (pymupdf.PDF_ANNOT_TEXT,):
                attrs["width"] = _fmt(border["width"])
            if border.get("dashes"):
                attrs["style"] = "dash"
                attrs["dashes"] = ",".join(_fmt(d) for d in border["dashes"])
            if border.get("clouds", 0) > 0:
                attrs["style"] = "cloudy"
                attrs["intensity"] = _fmt(border["clouds"])
            if a.irt_xref and a.irt_xref in names:
                attrs["inreplyto"] = names[a.irt_xref]
                k, v = pdf.xref_get_key(a.xref, "RT")
                if k == "name" and v == "/Group":
                    attrs["replyType"] = "group"
            for key, attr in (("State", "state"), ("StateModel", "statemodel")):
                k, v = pdf.xref_get_key(a.xref, key)
                if k == "string":
                    attrs[attr] = _unescape_pdf_string(v)
                elif k == "name":
                    attrs[attr] = v.lstrip("/")
            intent = _intent(pdf, a.xref)
            if intent:
                attrs["intent"] = intent
            t = a.type[0]
            children: list[str] = []
            verts = a.vertices or []
            if t in (pymupdf.PDF_ANNOT_HIGHLIGHT, pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_STRIKE_OUT,
                     pymupdf.PDF_ANNOT_SQUIGGLY):
                coords = []
                for v in verts:
                    coords.extend(to_pdf_point(page, v[0], v[1]))
                attrs["coords"] = ",".join(_fmt(c) for c in coords)
            elif t == pymupdf.PDF_ANNOT_LINE and len(verts) >= 2:
                s = to_pdf_point(page, *verts[0])
                e = to_pdf_point(page, *verts[1])
                attrs["start"] = f"{_fmt(s[0])},{_fmt(s[1])}"
                attrs["end"] = f"{_fmt(e[0])},{_fmt(e[1])}"
                le = a.line_ends or (0, 0)
                attrs["head"] = _LE_NAMES.get(le[0], "None")
                attrs["tail"] = _LE_NAMES.get(le[1], "None")
            elif t in (pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE):
                pts = []
                for v in verts:
                    x, y = to_pdf_point(page, v[0], v[1])
                    pts.append(f"{_fmt(x)},{_fmt(y)}")
                children.append(f"<vertices>{';'.join(pts)}</vertices>")
            elif t == pymupdf.PDF_ANNOT_INK:
                gestures = []
                for s in verts:
                    pts = []
                    for v in s:
                        x, y = to_pdf_point(page, v[0], v[1])
                        pts.append(f"{_fmt(x)},{_fmt(y)}")
                    gestures.append(f"<gesture>{';'.join(pts)}</gesture>")
                children.append("<inklist>" + "".join(gestures) + "</inklist>")
            elif t == pymupdf.PDF_ANNOT_TEXT:
                k, v = pdf.xref_get_key(a.xref, "Name")
                attrs["icon"] = v.lstrip("/") if k == "name" else "Comment"
            elif t == pymupdf.PDF_ANNOT_STAMP:
                k, v = pdf.xref_get_key(a.xref, "Name")
                attrs["icon"] = v.lstrip("/") if k == "name" else "Draft"
                spec = stamp_spec_of(pdf, a.xref)
                if spec is not None:
                    attrs["foliant-stamp"] = spec.text
                    if spec.subtitle:
                        attrs["foliant-stamp-sub"] = spec.subtitle
                try:   # aspetto incluso perché la grafica non vada persa (immagine PNG in base64)
                    import base64
                    pix = a.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=True)
                    children.append("<appearance>" + base64.b64encode(pix.tobytes("png")).decode("ascii")
                                    + "</appearance>")
                except Exception:
                    pass
            elif t == pymupdf.PDF_ANNOT_FREE_TEXT:
                da = pdf.xref_get_key(a.xref, "DA")[1]
                if da:
                    children.append(f"<defaultappearance>{escape(_unescape_pdf_string(da))}</defaultappearance>")
                k, v = pdf.xref_get_key(a.xref, "CL")
                if k == "array" and _intent(pdf, a.xref) == "FreeTextCallout":
                    attrs["callout"] = ",".join(_fmt(float(x)) for x in v.strip("[]").split())
                k, v = pdf.xref_get_key(a.xref, "RD")
                if k == "array":
                    attrs["fringe"] = ",".join(_fmt(float(x)) for x in v.strip("[]").split())
                border_color = _array(pdf, a.xref, FT_BORDER_KEY)
                if border_color and len(border_color) == 3:
                    attrs["foliant-border-color"] = color_to_hex(border_color)
                if pdf.xref_get_key(a.xref, AUTOFIT_KEY)[1] == "true":
                    attrs["foliant-autofit"] = "true"
            elif t == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                fi = a.file_info or {}
                attrs["file"] = fi.get("filename", "")
                attrs["icon"] = "PushPin"
            content = info.get("content", "")
            if content:
                children.insert(0, f"<contents>{escape(content)}</contents>")
            attr_txt = " ".join(f"{k}={quoteattr(str(v))}" for k, v in attrs.items())
            lines.append(f"<{tag} {attr_txt}>" + "".join(children) + f"</{tag}>")
    lines.append("</annots>")
    if filename:
        lines.append(f'<f href={quoteattr(filename)}/>')
    lines.append("</xfdf>")
    return "\n".join(lines) + "\n"


_LE_NAMES = {
    pymupdf.PDF_ANNOT_LE_NONE: "None", pymupdf.PDF_ANNOT_LE_SQUARE: "Square", pymupdf.PDF_ANNOT_LE_CIRCLE: "Circle",
    pymupdf.PDF_ANNOT_LE_DIAMOND: "Diamond", pymupdf.PDF_ANNOT_LE_OPEN_ARROW: "OpenArrow",
    pymupdf.PDF_ANNOT_LE_CLOSED_ARROW: "ClosedArrow", pymupdf.PDF_ANNOT_LE_BUTT: "Butt",
    pymupdf.PDF_ANNOT_LE_R_OPEN_ARROW: "ROpenArrow", pymupdf.PDF_ANNOT_LE_R_CLOSED_ARROW: "RClosedArrow",
    pymupdf.PDF_ANNOT_LE_SLASH: "Slash",
}
_LE_VALUES = {v: k for k, v in _LE_NAMES.items()}

_FLAG_NAMES = [(pymupdf.PDF_ANNOT_IS_INVISIBLE, "invisible"), (pymupdf.PDF_ANNOT_IS_HIDDEN, "hidden"),
               (pymupdf.PDF_ANNOT_IS_PRINT, "print"), (pymupdf.PDF_ANNOT_IS_NO_ZOOM, "nozoom"),
               (pymupdf.PDF_ANNOT_IS_NO_ROTATE, "norotate"), (pymupdf.PDF_ANNOT_IS_NO_VIEW, "noview"),
               (pymupdf.PDF_ANNOT_IS_READ_ONLY, "readonly"), (pymupdf.PDF_ANNOT_IS_LOCKED, "locked"),
               (pymupdf.PDF_ANNOT_IS_TOGGLE_NO_VIEW, "togglenoview")]


def _xfdf_flags(flags: int) -> str:
    return ",".join(name for bit, name in _FLAG_NAMES if flags & bit)


def _parse_flags(text: str | None) -> int:
    if not text:
        return pymupdf.PDF_ANNOT_IS_PRINT
    names = {n.strip().lower() for n in text.split(",")}
    return sum(bit for bit, name in _FLAG_NAMES if name in names)


def _floats(text: str | None) -> list[float]:
    if not text:
        return []
    return [float(v) for v in re.split(r"[,;\s]+", text.strip()) if v]


def _points(text: str | None) -> list[tuple[float, float]]:
    v = _floats(text)
    return [(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    messages: list[str] = field(default_factory=list)


def import_xfdf(pdf: pymupdf.Document, data: str | bytes, *, replace: bool = False) -> ImportResult:
    """Importa commenti da XFDF. Con `replace` elimina prima i commenti esistenti.

    I commenti già presenti con lo stesso nome (/NM) non vengono duplicati."""
    with bulk_geometry():
        return _import_xfdf(pdf, data, replace=replace)


def _import_xfdf(pdf: pymupdf.Document, data: str | bytes, *, replace: bool = False) -> ImportResult:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(data if isinstance(data, bytes) else data.encode("utf-8"))
    except ET.ParseError as e:
        raise AnnotationError("Il file non è un XFDF valido.") from e
    if root.tag.split("}")[-1] != "xfdf":
        raise AnnotationError("Il file non è un XFDF valido.")
    annots_el = None
    for child in root:
        if child.tag.split("}")[-1] == "annots":
            annots_el = child
    result = ImportResult()
    if annots_el is None:
        return result
    if replace:
        delete_all(pdf)
    name_map: dict[str, tuple[int, int]] = {}
    for i in range(pdf.page_count):
        page = pdf[i]
        for a in iter_annots(page):
            nm = (a.info or {}).get("id")
            if nm:
                name_map[(i, nm)] = (i, a.xref)
    existing = set(name_map)
    pending_irt: list[tuple[int, int, str]] = []
    for el in annots_el:
        tag = el.tag.split("}")[-1]
        try:
            page_no = int(el.get("page", "0"))
        except ValueError:
            result.skipped += 1
            continue
        if not 0 <= page_no < pdf.page_count:
            result.skipped += 1
            result.messages.append(f"Pagina {page_no + 1} inesistente: commento ignorato.")
            continue
        nm = el.get("name") or ""
        if nm and (page_no, nm) in existing:
            result.skipped += 1
            continue
        try:
            xref = _import_one(pdf, page_no, tag, el)
        except Exception as e:  # un elemento difettoso non blocca gli altri
            xref = None
            result.messages.append(f"Commento «{tag}» non importato: {e}")
        if xref is None:
            result.skipped += 1
            continue
        if nm:
            pdf.xref_set_key(xref, "NM", _pdf_string(nm) if nm.isascii() else pymupdf.get_pdf_str(nm))
            name_map[(page_no, nm)] = (page_no, xref)
            existing.add((page_no, nm))
        irt = el.get("inreplyto")
        if irt:
            pending_irt.append((page_no, xref, irt))
        result.imported += 1
    for page_no, xref, irt in pending_irt:
        target = name_map.get((page_no, irt))
        if target is None or target[0] != page_no:
            continue
        page = pdf[page_no]
        annot = find_annot(page, xref)
        if annot is not None:
            annot.set_irt_xref(target[1])
    return result


def _el_text(el, name: str) -> str | None:
    for child in el:
        if child.tag.split("}")[-1] == name:
            if name == "contents-richtext":
                return "".join(child.itertext())
            return child.text or ""
    return None


def _import_one(pdf: pymupdf.Document, page_no: int, tag: str, el) -> int | None:
    page = pdf[page_no]
    t = _TAG_TYPES.get(tag)
    if t is None:
        return None
    rect_vals = _floats(el.get("rect"))
    rect = from_pdf_rect(page, rect_vals) if len(rect_vals) == 4 else None
    color = hex_to_color(el.get("color"))
    fill = hex_to_color(el.get("interior-color"))
    opacity = float(el.get("opacity", "1") or 1)
    width = float(el.get("width", "1") or 1)
    dash = "dashed" if el.get("style") == "dash" else "solid"
    cloud = el.get("style") == "cloudy"
    author = el.get("title", "")
    contents = _el_text(el, "contents")
    if contents is None:
        contents = _el_text(el, "contents-richtext") or ""
    date = el.get("creationdate") or el.get("date")
    style = Style(color=color, fill=fill, opacity=opacity, width=width, dash=dash, cloud=cloud)
    intent = el.get("intent", "")
    with unrotated(page):
        if t in ("Highlight", "Underline", "StrikeOut", "Squiggly"):
            pts = [pymupdf.Point(*from_pdf_point(page, x, y)) for x, y in _points(el.get("coords"))]
            quads = [pymupdf.Quad(*pts[i:i + 4]) for i in range(0, len(pts) - 3, 4)]
            if not quads and rect is not None:
                quads = [rect.quad]
            kind = {"Highlight": HIGHLIGHT, "Underline": UNDERLINE, "StrikeOut": STRIKEOUT, "Squiggly": SQUIGGLY}[t]
            xref = create_markup(pdf, page_no, kind, quads, style, author, contents)
        elif t == "Text":
            if rect is None:
                return None
            annot = page.add_text_annot(rect.tl, contents, icon=el.get("icon") or "Comment")
            pdf.xref_set_key(annot.xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*rect_vals))
            annot = page.load_annot(annot.xref)
            if color:
                annot.set_colors(stroke=color)
            annot.set_opacity(opacity)
            xref = _finish(annot, author, contents, date=date)
            state = el.get("state")
            if state:
                pdf.xref_set_key(xref, "State", "/" + _name_token(state))
                pdf.xref_set_key(xref, "StateModel", "/" + _name_token(el.get("statemodel") or "Review"))
                annot = page.load_annot(xref)
                annot.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_PRINT)
        elif t == "FreeText":
            if rect is None:
                return None
            da = _el_text(el, "defaultappearance") or ""
            m = re.search(r"([\d.]+)\s+Tf", da)
            style.fontsize = float(m.group(1)) if m else 12
            cm = re.search(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+rg", da)
            style.text_color = tuple(float(v) for v in cm.groups()) if cm else (0, 0, 0)
            style.fill = color
            style.color = hex_to_color(el.get("foliant-border-color"))
            style.autofit = el.get("foliant-autofit") == "true"
            style.width = float(el.get("width", "0") or 0)
            callout_vals = _floats(el.get("callout"))
            fringe = _floats(el.get("fringe"))
            inner = rect
            if len(fringe) == 4:
                left, bottom, right, top = fringe
                inner = pymupdf.Rect(rect.x0 + left, rect.y0 + top, rect.x1 - right, rect.y1 - bottom)
            callout = None
            if callout_vals:
                callout = [pymupdf.Point(*from_pdf_point(page, x, y)) for x, y in
                           zip(callout_vals[::2], callout_vals[1::2])]
            xref = create_freetext(pdf, page_no, inner, contents, style, author, callout=callout)
            if not callout:     # /Rect esportato tal quale (create_freetext lo allarga del bordo)
                pdf.xref_set_key(xref, "Rect", "[{:.3f} {:.3f} {:.3f} {:.3f}]".format(*rect_vals))
                refresh_freetext(pdf, page_no, xref)
        elif t in ("Square", "Circle"):
            if rect is None:
                return None
            xref = create_shape(pdf, page_no, RECT if t == "Square" else ELLIPSE, [rect.tl, rect.br], style, author,
                                contents)
            _set_rect_exact(get_annot(page, xref), rect)
        elif t == "Line":
            s = _floats(el.get("start"))
            e = _floats(el.get("end"))
            if len(s) < 2 or len(e) < 2:
                return None
            a = pymupdf.Point(*from_pdf_point(page, s[0], s[1]))
            b = pymupdf.Point(*from_pdf_point(page, e[0], e[1]))
            annot = page.add_line_annot(a, b)
            _apply_style(annot, style.copy(color=color or SHAPE_COLOR), fill=fill is not None)
            head = _LE_VALUES.get(el.get("head", "None"), 0)
            tail = _LE_VALUES.get(el.get("tail", "None"), 0)
            if head or tail:
                annot.set_line_ends(head, tail)
            xref = _finish(annot, author, contents, date=date)
            if intent:
                pdf.xref_set_key(xref, "IT", "/" + intent)
        elif t in ("Polygon", "PolyLine"):
            pts = [pymupdf.Point(*from_pdf_point(page, x, y)) for x, y in _points(_el_text(el, "vertices"))]
            if len(pts) < 2:
                return None
            annot = page.add_polygon_annot(pts) if t == "Polygon" else page.add_polyline_annot(pts)
            _apply_style(annot, style.copy(color=color or SHAPE_COLOR), fill=t == "Polygon")
            xref = _finish(annot, author, contents, date=date)
            if intent:
                pdf.xref_set_key(xref, "IT", "/" + intent)
        elif t == "Ink":
            strokes = []
            for child in el:
                if child.tag.split("}")[-1] == "inklist":
                    for g in child:
                        pts = [from_pdf_point(page, x, y) for x, y in _points(g.text)]
                        if pts:
                            strokes.append(pts)
            if not strokes:
                return None
            xref = create_ink(pdf, page_no, strokes, style, author)
            page.load_annot(xref).set_info(content=contents)
        elif t == "Stamp":
            if rect is None:
                return None
            appearance = _el_text(el, "appearance")
            if not el.get("foliant-stamp") and appearance:
                import base64
                try:
                    png = base64.b64decode(appearance.strip())
                    with unrotated(page):
                        annot = page.add_stamp_annot(rect, png)
                    xref = _finish(annot, author, contents or "Timbro immagine", kind=STAMP, date=date)
                    pdf.xref_set_key(xref, "IT", "/StampImage")
                except Exception:
                    appearance = None
            if el.get("foliant-stamp") or not appearance:
                text = el.get("foliant-stamp") or el.get("icon") or "Timbro"
                spec = StampSpec(text, color or DEFAULT_COLORS[STAMP], el.get("foliant-stamp-sub", ""),
                                 el.get("icon", ""))
                xref = _stamp_in_rect(pdf, page, rect, spec, opacity, author, date=date)
            if contents:
                page.load_annot(xref).set_info(content=contents)
        elif t == "FileAttachment":
            return None  # i dati del file non viaggiano nell'XFDF senza flusso incorporato
        else:
            return None
    annot = find_annot(page, xref)
    if annot is None:
        return None
    info = {}
    if date:
        info["creationDate"] = el.get("creationdate") or date
        info["modDate"] = el.get("date") or date
    if el.get("subject"):
        info["subject"] = el.get("subject")
    if info:
        annot.set_info(**info)
    flags = _parse_flags(el.get("flags"))
    if flags != annot.flags:
        annot.set_flags(flags)
    if t != "Stamp":
        annot.update()
    else:
        annot.update()
        refresh_stamp(pdf, page_no, xref)
    return xref


# ------------------------------------------------------------------ FDF
def export_fdf(pdf: pymupdf.Document, filename: str = "") -> bytes:
    """FDF minimale (formato a oggetti PDF) con i commenti: un oggetto per annotazione.

    /IRT (risposte e stati) punta all'oggetto FDF del commento padre; /RT e /NM restano."""
    items: list[tuple[int, int]] = []           # (pagina, xref)
    for page_no in range(pdf.page_count):
        page = pdf[page_no]
        for a in iter_annots(page):
            if a.type[0] not in NON_COMMENT_TYPES:
                items.append((page_no, a.xref))
    numbers = {xref: i + 2 for i, (_p, xref) in enumerate(items)}

    def irt(m: re.Match) -> str:
        n = numbers.get(int(m.group(1)))
        return f"/IRT {n} 0 R" if n is not None else ""

    objs: list[bytes] = []
    for page_no, xref in items:
        src = pdf.xref_object(xref, compressed=True)
        src = re.sub(r"/IRT\s*(\d+)\s+\d+\s+R", irt, src)
        # rimuove riferimenti non trasferibili (pagina, aspetto, popup)
        src = re.sub(r"/(P|AP|Popup|Parent)\s*(\d+\s+\d+\s+R|<<.*?>>)", "", src, flags=re.S)
        src = src.strip()
        if src.endswith(">>"):
            src = src[:-2] + f"/Page {page_no}>>"
        objs.append(f"{numbers[xref]} 0 obj\n{src}\nendobj\n".encode("latin-1", errors="replace"))
    refs = " ".join(f"{numbers[x]} 0 R" for _p, x in items)
    head = f"%FDF-1.2\n1 0 obj\n<</FDF<</Annots[{refs}]" + (f"/F{_pdf_string(filename)}" if filename else "") + \
        ">>>>\nendobj\n"
    body = head.encode("latin-1", errors="replace") + b"".join(objs)
    return body + b"trailer\n<</Root 1 0 R>>\n%%EOF\n"


# ------------------------------------------------------------------ riepilogo
def summary_rows(pdf: pymupdf.Document) -> list[dict]:
    rows = []
    for a in list_annotations(pdf):
        status = _(STATE_TEXTS[a.status]) if a.status in STATE_TEXTS and a.status != STATE_NONE else (
            "" if a.status == STATE_NONE else a.status)
        rows.append({"pagina": a.page + 1, "tipo": _(a.label), "autore": a.author,
                     "data": format_date(a.modified or a.created),
                     "testo": a.contents, "testo_marcato": a.marked_text,
                     "stato": status, "risposte": [(r.author, format_date(r.created), r.contents) for r in a.replies],
                     "colore": color_to_hex(a.color)})
    return rows


def summary_text(pdf: pymupdf.Document, title: str = "") -> str:
    out = [_("Riepilogo commenti — {title}").format(title=title) if title else _("Riepilogo commenti"), ""]
    rows = summary_rows(pdf)
    if not rows:
        out.append(_("Nessun commento."))
    current = None
    for r in rows:
        if r["pagina"] != current:
            current = r["pagina"]
            out.append(_("Pagina {n}").format(n=current))
        head = f"  • {r['tipo']}"
        if r["autore"]:
            head += f" — {r['autore']}"
        if r["data"]:
            head += f" ({r['data']})"
        if r["stato"]:
            head += f" [{r['stato']}]"
        out.append(head)
        if r["testo_marcato"]:
            out.append(f"    «{r['testo_marcato']}»")
        if r["testo"]:
            out.extend("    " + line for line in r["testo"].splitlines())
        for author, date, text in r["risposte"]:
            out.append(f"    ↳ {author} ({date}): {text}")
    return "\n".join(out) + "\n"


def measures_csv(pdf: pymupdf.Document, sep: str = ",") -> str:
    """Tutte le misure in CSV: pagina, tipo, valore numerico, valore formattato, unità, scala, autore, data."""
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep)
    w.writerow([_("Pagina"), _("Tipo"), _("Valore"), _("Valore formattato"), _("Unità"), _("Scala"), _("Autore"),
                _("Data")])
    for a in list_annotations(pdf):
        if a.kind not in MEASURE_KINDS:
            continue
        page = pdf[a.page]
        annot = find_annot(page, a.xref)
        scale = scale_of(pdf, a.xref) or Scale()
        pts = [pymupdf.Point(v) for v in (annot.vertices or [])] if annot else []
        value = measure_number(a.kind, pts, scale) if len(pts) >= (3 if a.kind == ANGLE else 2) else 0.0
        unit = "°" if a.kind == ANGLE else scale.unit
        w.writerow([a.page + 1, _(SUBJECTS[a.kind]), f"{value:.4f}", a.contents, unit, scale.text(), a.author,
                    format_date(a.created)])
    return buf.getvalue()


def summary_csv(pdf: pymupdf.Document) -> str:
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([_("Pagina"), _("Tipo"), _("Autore"), _("Data"), _("Stato"), _("Testo"), _("Testo marcato"), _("N."),
                _("Risposta a")])
    n = 0
    for r in summary_rows(pdf):
        n += 1
        parent = n
        w.writerow([r["pagina"], r["tipo"], r["autore"], r["data"], r["stato"], r["testo"], r["testo_marcato"], n, ""])
        for author, date, text in r["risposte"]:
            n += 1
            w.writerow([r["pagina"], _("Risposta"), author, date, "", text, "", n, parent])
    return buf.getvalue()


_UNICODE_FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/dejavu/DejaVuSans.ttf")


def _summary_font() -> tuple[str, str | None]:
    import os
    for path in _UNICODE_FONTS:
        if os.path.exists(path):
            return "fol", path
    return "helv", None


def _wrap_chars(line: str, max_chars: int) -> list[str]:
    out = []
    while len(line) > max_chars:
        cut = line.rfind(" ", 0, max_chars)
        cut = cut if cut > 20 else max_chars
        out.append(line[:cut])
        line = "      " + line[cut:].lstrip()
    out.append(line)
    return out


def summary_pdf(pdf: pymupdf.Document, title: str = "", layout: str = "list") -> bytes:
    """Riepilogo dei commenti come nuovo PDF (l'originale non cambia).

    layout="list": solo elenco; layout="page": per ogni pagina con commenti, l'immagine della
    pagina con marcatori numerati e accanto l'elenco numerato degli stessi commenti."""
    fontname, fontfile = _summary_font()
    out = pymupdf.open()

    def text_at(page, xy, txt, size, color=(0, 0, 0)):
        kw = {"fontname": fontname, "fontsize": size, "color": color}
        if fontfile:
            kw["fontfile"] = fontfile
        try:
            page.insert_text(xy, txt, **kw)
        except Exception:
            page.insert_text(xy, txt.encode("latin-1", "replace").decode("latin-1"), fontname="helv",
                             fontsize=size, color=color)

    if layout == "page":
        rows = list_annotations(pdf)
        n = 0
        by_page: dict[int, list] = {}
        for a in rows:
            by_page.setdefault(a.page, []).append(a)
        if not by_page:
            p = out.new_page(width=842, height=595)
            text_at(p, (40, 60), _("Nessun commento."), 12)
        for page_no in sorted(by_page):
            src = pdf[page_no]
            W, H = 842, 595
            dst = out.new_page(width=W, height=H)
            page_title = _("Pagina {n}").format(n=page_no + 1)
            text_at(dst, (30, 28), f"{title} — {page_title}" if title else page_title, 11)
            pix = src.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), annots=True)
            box = pymupdf.Rect(30, 40, 30 + (H - 70) * src.rect.width / max(src.rect.height, 1), H - 30)
            if box.width > W * 0.55:
                k = (W * 0.55) / box.width
                box = pymupdf.Rect(box.x0, box.y0, box.x0 + box.width * k, box.y0 + box.height * k)
            dst.insert_image(box, pixmap=pix)
            dst.draw_rect(box, color=(0.6, 0.6, 0.6), width=0.5)
            sx = box.width / src.rect.width
            sy = box.height / src.rect.height
            m = src.rotation_matrix
            y = 60.0
            x_list = box.x1 + 30
            for a in by_page[page_no]:
                n += 1
                r = a.rect * m
                cx = box.x0 + r.x0 * sx
                cy = box.y0 + r.y0 * sy
                # marcatore numerato sulla pagina e connettore verso la voce
                dst.draw_circle((cx, cy), 7, color=(0.8, 0.1, 0.1), fill=(1, 1, 1), width=1)
                text_at(dst, (cx - (3 if n < 10 else 5.5), cy + 3), str(n), 7, (0.8, 0.1, 0.1))
                dst.draw_line((cx + 7, cy), (x_list - 4, y - 3), color=(0.8, 0.5, 0.5), width=0.4)
                head = (f"{n}. {_(a.label)} — {a.author or _('(autore sconosciuto)')} · "
                        f"{format_date(a.modified or a.created)}")
                lines = _wrap_chars(head, 60)
                if a.marked_text:
                    lines += _wrap_chars(f"«{a.marked_text}»", 60)
                if a.contents:
                    for ln in a.contents.splitlines()[:6]:
                        lines += _wrap_chars(ln, 60)
                for r_ in a.replies:
                    lines += _wrap_chars(f"↳ {r_.author}: {r_.contents}", 60)
                for i, ln in enumerate(lines):
                    if y > H - 30:
                        break
                    text_at(dst, (x_list, y), ln, 8.5 if i else 9, (0, 0, 0))
                    y += 12
                y += 6
        data = out.tobytes(garbage=3, deflate=True)
        out.close()
        return data

    text = summary_text(pdf, title)
    width, height, margin, size = 595, 842, 50, 10
    lh = size * 1.45
    wrapped: list[str] = []
    for line in text.splitlines():
        wrapped.extend(_wrap_chars(line, 95))
    page = None
    y = 0.0
    for line in wrapped:
        if page is None or y > height - margin:
            page = out.new_page(width=width, height=height)
            y = margin + size
        text_at(page, (margin, y), line, size)
        y += lh
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    return data


def summary_html(pdf: pymupdf.Document, title: str = "") -> str:
    from html import escape

    heading = title or _("Riepilogo commenti")
    lang = "en" if _("Riepilogo commenti") != "Riepilogo commenti" else "it"
    parts = [f"<!doctype html><html lang='{lang}'><head><meta charset='utf-8'>",
             f"<title>{escape(heading)}</title>",
             "<style>body{font-family:sans-serif;max-width:820px;margin:2em auto;color:#222}"
             "h2{border-bottom:1px solid #ccc}.c{margin:.6em 0;padding:.4em .8em;border-left:4px solid #999}"
             ".m{color:#555;font-size:.9em}.q{font-style:italic}.r{margin-left:1.5em;color:#333}</style></head><body>",
             f"<h1>{escape(heading)}</h1>"]
    rows = summary_rows(pdf)
    if not rows:
        parts.append(f"<p>{escape(_('Nessun commento.'))}</p>")
    current = None
    for r in rows:
        if r["pagina"] != current:
            current = r["pagina"]
            parts.append(f"<h2>{escape(_('Pagina {n}').format(n=current))}</h2>")
        color = r["colore"] or "#999999"
        meta = " · ".join(x for x in (r["tipo"], r["autore"], r["data"], r["stato"]) if x)
        parts.append(f"<div class='c' style='border-color:{color}'><div class='m'>{escape(meta)}</div>")
        if r["testo_marcato"]:
            parts.append(f"<div class='q'>«{escape(r['testo_marcato'])}»</div>")
        if r["testo"]:
            parts.append("<div>" + escape(r["testo"]).replace("\n", "<br>") + "</div>")
        for author, date, text in r["risposte"]:
            parts.append(f"<div class='r'>↳ <b>{escape(author)}</b> <span class='m'>{escape(date)}</span>: "
                         f"{escape(text)}</div>")
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)
