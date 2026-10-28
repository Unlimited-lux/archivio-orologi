"""Inserimento di contenuti esattamente dove richiesto, nelle coordinate standard della pagina.

Coordinate standard
-------------------
In lettura il motore usa ovunque le coordinate della pagina *non ruotata*, in
punti, con origine nell'angolo superiore sinistro dell'area visibile e asse y
verso il basso: ``get_text()``, ``search_for()``, ``get_drawings()``,
``get_image_info()``, le annotazioni, ``PageEvent.point`` degli strumenti.
L'area visibile V è il /CropBox intersecato con il /MediaBox (in coordinate
PDF); un punto (x, y) standard corrisponde nello spazio utente PDF a::

    x_pdf = V.x0 + x        y_pdf = V.y1 - y

Il problema
-----------
Alcune funzioni di inserimento del motore (PyMuPDF 2.0) convertono le
coordinate con matrici sbagliate quando la pagina ha /Rotate diverso da 0, un
/CropBox diverso dal /MediaBox o un /MediaBox con origine non nulla. Misurando
tutte le combinazioni (rotazioni 0/90/180/270 × 10 ritagli × 3 MediaBox) lo
scarto è sempre una pura traslazione, *riletto = richiesto + scarto*, con
(V = area visibile, C = /CropBox così com'è nel file, M = /MediaBox, tutto in
coordinate PDF; se /CropBox manca, C = M):

- ``draw_*`` e ``new_shape()`` (punti del tracciato), ``insert_image``,
  ``show_pdf_page``, ``insert_htmlbox``, ``insert_link`` (rettangolo e
  destinazione ``to``): (0, 0) con /Rotate 0; altrimenti
  (-V.x0, V.y1 - altezza(C)), cioè (-V.x0, +V.y0) se C è dentro M. Il motore
  usa la matrice ``1 0 0 -1 0 altezza(C)`` e ignora l'origine dei riquadri.
- ``TextWriter.write_text``: (C.x0 - V.x0, (C.y0 + C.y1) - (M.y0 + M.y1)) con
  qualunque rotazione, cioè (0, C.y0 + C.y1 - M.y0 - M.y1) se C è dentro M.
- ``insert_text``, ``insert_textbox`` e il punto fisso di ``morph`` (anche in
  ``draw_*`` e ``Shape.finish``): (C.x0 - V.x0, V.y1 - C.y1), nullo se C è
  dentro M (un /CropBox più grande del /MediaBox, frequente nei file reali,
  le sposta).
- annotazioni, ``add_redact_annot`` + ``apply_redactions``: corrette.

Il contenuto non viene mai ruotato né scalato per errore. Altri difetti
trovati e compensati qui: ``TextWriter.write_text`` non isola il contenuto
esistente (una ``cm`` lasciata aperta dalla pagina sposta il testo);
``show_pdf_page`` con una pagina sorgente ruotata usa un'area sorgente
sbagliata (scala e ritaglio errati); ``get_links()`` restituisce ``from`` e
``to`` in coordinate della pagina *visualizzata* (ruotata), non standard.
In lettura, con un /CropBox più grande del /MediaBox su pagine ruotate,
``Annot.rect``, ``Page.rotation_matrix`` e ``Page.derotation_matrix`` usano le
dimensioni del /CropBox del file invece dell'area visibile: qui si usano
``page_to_display_matrix`` / ``display_to_page_matrix`` e il /Rect del file.

Approccio
---------
Per ogni geometria di pagina (MediaBox, CropBox, Rotate) e per ogni tipo di
inserimento (``KINDS``) lo scarto viene **misurato**: su un documento
temporaneo si crea una pagina con la stessa geometria, si inserisce una sonda
in tre punti noti con la funzione del motore, la si rilegge con le funzioni di
lettura e si verifica che lo scarto sia una traslazione. Il risultato è in
cache, quindi la misura costa pochi millisecondi una sola volta per geometria.
Così la correzione resta giusta anche se una versione futura del motore
corregge o cambia questi difetti; se la misura non riesce si usano le formule
qui sopra (``formula_offset``) registrando un avviso.

Pagine con /UserUnit diverso da 1 o /Rotate non multiplo di 90 non sono
gestite in modo coerente dal motore: le funzioni sollevano ``PlacementError``.

Uso
---
Tutte le funzioni ricevono coordinate standard e vanno chiamate dentro
``document.change()`` (o tenendo ``ENGINE_LOCK``)::

    from foliant.core import placement

    with document.change(_("Timbro")) as pdf:
        page = pdf[i]
        placement.draw_rect(page, pymupdf.Rect(50, 50, 200, 90), color=(1, 0, 0))
        placement.insert_text(page, (60, 75), "APPROVATO", fontsize=14, upright=True)

``upright=True`` rende il testo (o l'immagine, o la pagina mostrata) diritto
nel verso della pagina *visualizzata*, compensando /Rotate; con il valore
predefinito ``False`` il contenuto è allineato alla pagina non ruotata, come
fa il motore. Il parametro ``rotate`` (multipli di 90, antiorario come nel
motore) si somma alla compensazione.
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

import pymupdf
from pymupdf import Matrix, Point, Quad, Rect

from .engine import ENGINE_LOCK
from .errors import FoliantError

log = logging.getLogger(__name__)

__all__ = [
    "KINDS", "PlacementError", "Geometry", "geometry", "page_rect",
    "page_to_pdf_matrix", "pdf_to_page_matrix", "page_to_display_matrix", "display_to_page_matrix",
    "insertion_matrix", "to_insertion_point", "to_insertion_rect", "formula_offset", "clear_cache",
    "PlacedShape", "new_shape", "draw_line", "draw_squiggle", "draw_zigzag", "draw_polyline",
    "draw_bezier", "draw_oval", "draw_circle", "draw_curve", "draw_sector", "draw_rect", "draw_quad",
    "insert_text", "insert_textbox", "insert_htmlbox", "write_text", "fill_textbox",
    "insert_image", "show_pdf_page", "insert_link", "update_link", "get_links", "add_redact_annot",
]

#: Tipi di inserimento, ciascuno con la propria correzione:
#: ``shape`` (punti di Page.draw_* e Shape.draw_*), ``shapemorph`` (punto fisso
#: di ``morph`` in draw_* e Shape.finish), ``image`` (insert_image), ``text`` e
#: ``textmorph`` (insert_text: punto e punto fisso di ``morph``), ``textbox`` e
#: ``textboxmorph`` (insert_textbox), ``htmlbox`` (insert_htmlbox),
#: ``textwriter`` (TextWriter.write_text), ``pdfpage`` (show_pdf_page),
#: ``link`` (rettangolo di insert_link), ``linkdest`` (punto ``to`` di un
#: collegamento interno, sulla pagina di destinazione), ``annot`` (annotazioni,
#: add_redact_annot).
KINDS = ("shape", "shapemorph", "image", "text", "textmorph", "textbox", "textboxmorph", "htmlbox",
         "textwriter", "pdfpage", "link", "linkdest", "annot")

_IDENTITY = Matrix(1, 0, 0, 1, 0, 0)
_CACHE_SIZE = 512
_CONSISTENCY = 0.02     # pt: le tre sonde devono dare lo stesso scarto entro questa tolleranza


class PlacementError(FoliantError):
    """La pagina ha una geometria che il motore non gestisce in modo coerente."""


class _CalibrationError(Exception):
    """Misura non riuscita: si ripiega sulle formule."""


# ------------------------------------------------------------------ geometria
@dataclass(frozen=True)
class Geometry:
    """Riquadri di una pagina come li usa il motore (coordinate PDF).

    ``mediabox``: /MediaBox normalizzato (Letter se assente o non valido);
    ``cropbox``: /CropBox normalizzato o None se assente; ``rotation``: /Rotate
    (0, 90, 180, 270); ``visible``: area visibile = CropBox ∩ MediaBox, l'origine
    delle coordinate standard.
    """

    mediabox: tuple[float, float, float, float]
    cropbox: tuple[float, float, float, float] | None
    rotation: int
    visible: tuple[float, float, float, float]

    @property
    def width(self) -> float:
        """Larghezza dell'area visibile (pagina non ruotata)."""
        return self.visible[2] - self.visible[0]

    @property
    def height(self) -> float:
        """Altezza dell'area visibile (pagina non ruotata)."""
        return self.visible[3] - self.visible[1]

    @property
    def key(self) -> tuple:
        rnd = lambda r: None if r is None else tuple(round(v, 4) for v in r)  # noqa: E731
        return rnd(self.mediabox), rnd(self.cropbox), self.rotation


def _resolve(doc: pymupdf.Document, value: tuple[str, str]) -> tuple[str, str]:
    """Risolve un riferimento indiretto restituito da ``xref_get_key``."""
    kind, text = value
    seen = 0
    while kind == "xref" and seen < 32:
        obj = doc.xref_object(int(text.split()[0]), compressed=True).strip()
        if obj.startswith("["):
            return "array", obj
        try:
            float(obj)
            return ("int" if re.fullmatch(r"[-+]?\d+", obj) else "real"), obj
        except ValueError:
            return "null", "null"
    return kind, text


def _inherited(doc: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    """Valore di una chiave della pagina, ereditata dai nodi /Pages se assente."""
    seen: set[int] = set()
    while xref and xref not in seen:
        seen.add(xref)
        value = doc.xref_get_key(xref, key)
        if value[0] != "null":
            return _resolve(doc, value)
        parent = doc.xref_get_key(xref, "Parent")
        if parent[0] != "xref":
            break
        xref = int(parent[1].split()[0])
    return "null", "null"


_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")


def _box(value: tuple[str, str]) -> tuple[float, float, float, float] | None:
    """Rettangolo PDF normalizzato da un array (come pdf_to_rect del motore)."""
    if value[0] != "array":
        return None
    nums = [float(n) for n in _NUMBER.findall(value[1])[:4]]
    nums += [0.0] * (4 - len(nums))
    a, b, c, d = nums
    return min(a, c), min(b, d), max(a, c), max(b, d)


def _is_empty(r) -> bool:
    return r is None or r[0] >= r[2] or r[1] >= r[3]


def geometry(page: pymupdf.Page) -> Geometry:
    """Riquadri e rotazione effettivi della pagina, letti dal file (con l'ereditarietà).

    Solleva ``PlacementError`` per /UserUnit diverso da 1 o /Rotate non multiplo di 90.
    """
    with ENGINE_LOCK:
        return _geometry(page)


def _geometry(page: pymupdf.Page) -> Geometry:
    doc = page.parent
    if doc is None or not doc.is_pdf:
        raise ValueError("la pagina non appartiene a un documento PDF")
    xref = page.xref
    mediabox = _box(_inherited(doc, xref, "MediaBox"))
    if _is_empty(mediabox):
        mediabox = (0.0, 0.0, 612.0, 792.0)
    elif mediabox[2] - mediabox[0] < 1 or mediabox[3] - mediabox[1] < 1:
        mediabox = (0.0, 0.0, 1.0, 1.0)
    cropbox = _box(_inherited(doc, xref, "CropBox"))
    if cropbox is None:
        visible = mediabox
    else:
        visible = (max(mediabox[0], cropbox[0]), max(mediabox[1], cropbox[1]),
                   min(mediabox[2], cropbox[2]), min(mediabox[3], cropbox[3]))
    if _is_empty(visible):
        visible = (0.0, 0.0, 612.0, 792.0)
    elif visible[2] - visible[0] < 1 or visible[3] - visible[1] < 1:
        visible = (0.0, 0.0, 1.0, 1.0)
    kind, text = _inherited(doc, xref, "Rotate")
    raw_rotation = int(float(text)) if kind in ("int", "real") else 0
    if raw_rotation % 90:
        raise PlacementError(
            f"La pagina {page.number + 1} ha una rotazione non valida ({raw_rotation}°): "
            "normalizzala prima di aggiungere contenuti."
        )
    kind, text = _resolve(doc, doc.xref_get_key(xref, "UserUnit"))
    if kind in ("int", "real") and abs(float(text) - 1.0) > 1e-6:
        raise PlacementError(
            f"La pagina {page.number + 1} usa un'unità di misura non standard (UserUnit {text}): "
            "l'inserimento di contenuti non è supportato."
        )
    return Geometry(mediabox, cropbox, raw_rotation % 360, visible)


def page_rect(page: pymupdf.Page) -> Rect:
    """Area visibile in coordinate standard: ``Rect(0, 0, larghezza, altezza)`` della pagina non ruotata.

    Diversa da ``page.rect``, che ha le dimensioni della pagina *visualizzata*.
    """
    g = geometry(page)
    return Rect(0, 0, g.width, g.height)


def _page_to_pdf(g: Geometry) -> Matrix:
    return Matrix(1, 0, 0, -1, g.visible[0], g.visible[3])


def _page_to_display(g: Geometry) -> Matrix:
    w, h = g.width, g.height
    return {
        0: Matrix(1, 0, 0, 1, 0, 0),
        90: Matrix(0, 1, -1, 0, h, 0),
        180: Matrix(-1, 0, 0, -1, w, h),
        270: Matrix(0, -1, 1, 0, 0, w),
    }[g.rotation]


def page_to_pdf_matrix(page: pymupdf.Page) -> Matrix:
    """Matrice da coordinate standard a spazio utente PDF (``punto * matrice``)."""
    return _page_to_pdf(geometry(page))


def pdf_to_page_matrix(page: pymupdf.Page) -> Matrix:
    """Matrice da spazio utente PDF a coordinate standard."""
    return ~_page_to_pdf(geometry(page))


def page_to_display_matrix(page: pymupdf.Page) -> Matrix:
    """Matrice da coordinate standard a coordinate della pagina visualizzata (ruotata, ``page.rect``)."""
    return _page_to_display(geometry(page))


def display_to_page_matrix(page: pymupdf.Page) -> Matrix:
    """Matrice da coordinate della pagina visualizzata a coordinate standard."""
    return ~_page_to_display(geometry(page))


# --------------------------------------------------------------- calibrazione
def _fmt(v: float) -> str:
    text = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _probe_page(g: Geometry, *, reference: bool = False, pages_before: int = 0):
    """Documento temporaneo con una pagina della stessa geometria (o di riferimento).

    La pagina di riferimento ha le stesse dimensioni standard ma MediaBox con
    origine nulla, nessun CropBox e nessuna rotazione: lì il motore è esatto.
    """
    doc = pymupdf.open()
    for _ in range(pages_before):
        doc.new_page(width=200, height=200)
    page = doc.new_page(width=max(g.width, 1), height=max(g.height, 1))
    if reference:
        doc.xref_set_key(page.xref, "MediaBox", f"[0 0 {_fmt(g.width)} {_fmt(g.height)}]")
    else:
        doc.xref_set_key(page.xref, "MediaBox", "[" + " ".join(_fmt(v) for v in g.mediabox) + "]")
        if g.cropbox is not None:
            doc.xref_set_key(page.xref, "CropBox", "[" + " ".join(_fmt(v) for v in g.cropbox) + "]")
        if g.rotation:
            doc.xref_set_key(page.xref, "Rotate", str(g.rotation))
    return doc, doc.reload_page(page)


def _anchors(g: Geometry) -> list[Point]:
    w, h = g.width, g.height
    return [Point(0.2 * w, 0.3 * h), Point(0.7 * w, 0.3 * h), Point(0.2 * w, 0.7 * h)]


def _probe_size(g: Geometry) -> float:
    return max(0.5, min(12.0, min(g.width, g.height) / 12))


def _center(r) -> Point:
    r = Rect(r)
    return Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


_TEXT_FLAGS = getattr(pymupdf, "TEXTFLAGS_RAWDICT", 0) & ~getattr(pymupdf, "TEXT_MEDIABOX_CLIP", 0)


def _char_origins(page: pymupdf.Page) -> dict[str, Point]:
    """Origine del primo carattere di ogni lettera, senza ritagliare all'area visibile."""
    out: dict[str, Point] = {}
    data = page.get_text("rawdict", clip=pymupdf.INFINITE_RECT(), flags=_TEXT_FLAGS)
    for block in data["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                for ch in span["chars"]:
                    out.setdefault(ch["c"], Point(ch["origin"]))
    return out


def _word_centers(page: pymupdf.Page) -> dict[str, Point]:
    out: dict[str, Point] = {}
    for w in page.get_text("words", clip=pymupdf.INFINITE_RECT(), flags=_TEXT_FLAGS):
        out.setdefault(w[4], _center(w[:4]))
    return out


def _pick(found: dict, keys) -> list[Point]:
    try:
        return [found[k] for k in keys]
    except KeyError as e:
        raise _CalibrationError(f"sonda non trovata: {e}") from None


def _probe_shape(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        pts = _anchors(g)
        shape = page.new_shape()
        shape.draw_polyline(pts)
        shape.finish(color=(0, 0, 0), closePath=False)
        shape.commit()
        lines = [it for d in page.get_drawings() for it in d["items"] if it[0] == "l"]
        if len(lines) < 2:
            raise _CalibrationError("tracciato sonda non trovato")
        return list(zip(pts, [Point(lines[0][1]), Point(lines[0][2]), Point(lines[1][2])]))
    finally:
        doc.close()


def _probe_text(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        pts = _anchors(g)
        for p, c in zip(pts, "ABC"):
            page.insert_text(p, c, fontsize=_probe_size(g))
        return list(zip(pts, _pick(_char_origins(page), "ABC")))
    finally:
        doc.close()


def _morph_fixpoint(g: Geometry) -> Point:
    return Point(0.5 * g.width, 0.5 * g.height)


def _polyline_points(drawing: dict) -> list[Point]:
    lines = [it for it in drawing["items"] if it[0] == "l"]
    if len(lines) < 2:
        raise _CalibrationError("tracciato sonda non trovato")
    return [Point(lines[0][1]), Point(lines[0][2]), Point(lines[1][2])]


def _probe_shapemorph(g: Geometry) -> list[tuple[Point, Point]]:
    """Punto fisso di ``morph``: stesso tracciato senza e con ingrandimento 2× attorno al punto.

    Con ingrandimento uniforme ``q = F + 2 (a - F)``, quindi il punto fisso
    effettivo è ``F = 2 a - q``.
    """
    doc, page = _probe_page(g)
    try:
        fix = _morph_fixpoint(g)
        for morph in (None, (fix, Matrix(2, 2))):
            shape = page.new_shape()
            shape.draw_polyline(_anchors(g))
            shape.finish(color=(0, 0, 0), closePath=False, morph=morph)
            shape.commit()
        drawings = page.get_drawings()
        if len(drawings) != 2:
            raise _CalibrationError("tracciati sonda non trovati")
        plain, morphed = (_polyline_points(d) for d in drawings)
        return [(fix, a * 2 - q) for a, q in zip(plain, morphed)]
    finally:
        doc.close()


def _probe_textmorph(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        fix, size = _morph_fixpoint(g), _probe_size(g)
        for p, plain, morphed in zip(_anchors(g), "ABC", "DEF"):
            page.insert_text(p, plain, fontsize=size)
            page.insert_text(p, morphed, fontsize=size, morph=(fix, Matrix(2, 2)))
        o = _char_origins(page)
        return [(fix, a * 2 - q) for a, q in zip(_pick(o, "ABC"), _pick(o, "DEF"))]
    finally:
        doc.close()


def _probe_textboxmorph(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        fix, size = _morph_fixpoint(g), _probe_size(g)
        for p, plain, morphed in zip(_anchors(g), "ABC", "DEF"):
            box = Rect(p.x, p.y, p.x + 6 * size, p.y + 3 * size)
            if (page.insert_textbox(box, plain, fontsize=size) < 0
                    or page.insert_textbox(box, morphed, fontsize=size, morph=(fix, Matrix(2, 2))) < 0):
                raise _CalibrationError("testo sonda non contenuto")
        o = _char_origins(page)
        return [(fix, a * 2 - q) for a, q in zip(_pick(o, "ABC"), _pick(o, "DEF"))]
    finally:
        doc.close()


def _probe_textwriter(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        pts = _anchors(g)
        writer = pymupdf.TextWriter(page.rect)
        for p, c in zip(pts, "ABC"):
            writer.append(p, c, fontsize=_probe_size(g))
        writer.write_text(page)
        return list(zip(pts, _pick(_char_origins(page), "ABC")))
    finally:
        doc.close()


def _relative_probe(g: Geometry, run) -> list[tuple[Point, Point]]:
    """Sonda confrontata con la stessa operazione su una pagina di riferimento."""
    ref_doc, ref_page = _probe_page(g, reference=True)
    doc, page = _probe_page(g)
    try:
        return list(zip(run(ref_page), run(page)))
    finally:
        doc.close()
        ref_doc.close()


def _probe_textbox(g: Geometry) -> list[tuple[Point, Point]]:
    def run(page):
        w, h = g.width, g.height
        rc = page.insert_textbox(Rect(0.1 * w, 0.1 * h, 0.9 * w, 0.9 * h), "Alfa Beta\nGamma",
                                 fontsize=_probe_size(g))
        if rc < 0:
            raise _CalibrationError("testo sonda non contenuto")
        return _pick(_char_origins(page), "ABG")
    return _relative_probe(g, run)


def _probe_htmlbox(g: Geometry) -> list[tuple[Point, Point]]:
    html = "<p>Alfa</p><p style='text-align:right'>Beta</p><p>Gamma</p>"

    def run(page):
        w, h = g.width, g.height
        spare, _scale = page.insert_htmlbox(Rect(0.1 * w, 0.1 * h, 0.9 * w, 0.9 * h), html)
        if spare < 0:
            raise _CalibrationError("HTML sonda non contenuto")
        return _pick(_word_centers(page), ("Alfa", "Beta", "Gamma"))
    return _relative_probe(g, run)


def _probe_pixmap() -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4), False)
    pix.clear_with(128)
    return pix


def _probe_image(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        s = _probe_size(g)
        rects = [Rect(p.x, p.y, p.x + s, p.y + s) for p in _anchors(g)]
        pix = _probe_pixmap()
        for r in rects:
            page.insert_image(r, pixmap=pix, keep_proportion=False)
        info = page.get_image_info()
        if len(info) != 3:
            raise _CalibrationError("immagini sonda non trovate")
        return [(_center(r), _center(i["bbox"])) for r, i in zip(rects, info)]
    finally:
        doc.close()


_PROBE_SOURCE: bytes | None = None
_SOURCE_MARKS = {(1.0, 0.0, 0.0): Rect(10, 10, 20, 20), (0.0, 1.0, 0.0): Rect(80, 10, 90, 20),
                 (0.0, 0.0, 1.0): Rect(10, 30, 20, 40)}


def _probe_source() -> pymupdf.Document:
    """Pagina sorgente 100×50 con tre quadrati colorati in posizioni note."""
    global _PROBE_SOURCE
    if _PROBE_SOURCE is None:
        src = pymupdf.open()
        sp = src.new_page(width=100, height=50)
        for color, r in _SOURCE_MARKS.items():
            sp.draw_rect(r, color=None, fill=color)
        _PROBE_SOURCE = src.tobytes()
        src.close()
    return pymupdf.open("pdf", _PROBE_SOURCE)


def _probe_pdfpage(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    src = _probe_source()
    try:
        a = _anchors(g)[0]
        width = 0.4 * g.width
        target = Rect(a.x, a.y, a.x + width, a.y + width / 2)
        page.show_pdf_page(target, src, 0)
        scale = width / 100
        found = {}
        for d in page.get_drawings():
            fill = d.get("fill")
            if fill is not None:
                found[tuple(round(c, 2) for c in fill)] = _center(d["rect"])
        pairs = []
        for color, r in _SOURCE_MARKS.items():
            c = _center(r)
            pairs.append((Point(target.x0 + c.x * scale, target.y0 + c.y * scale), _pick(found, [color])[0]))
        return pairs
    finally:
        src.close()
        doc.close()


def _link_rects(g: Geometry) -> list[Rect]:
    s = _probe_size(g)
    return [Rect(p.x, p.y, p.x + s, p.y + s) for p in _anchors(g)]


def _probe_link(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g)
    try:
        rects = _link_rects(g)
        for r in rects:
            page.insert_link({"kind": pymupdf.LINK_URI, "from": r, "uri": "https://example.invalid/"})
        xrefs = [a[0] for a in page.annot_xrefs() if a[1] == pymupdf.PDF_ANNOT_LINK]
        if len(xrefs) != 3:
            raise _CalibrationError("collegamenti sonda non trovati")
        to_page = ~_page_to_pdf(g)
        got = [_center(Rect(_box(_resolve(doc, doc.xref_get_key(x, "Rect")))) * to_page) for x in xrefs]
        return [(_center(r), p) for r, p in zip(rects, got)]
    finally:
        doc.close()


_XYZ = re.compile(r"/XYZ\s*([-+]?[\d.]+)\s+([-+]?[\d.]+)")


def _probe_linkdest(g: Geometry) -> list[tuple[Point, Point]]:
    doc, page = _probe_page(g, pages_before=1)
    try:
        pts = _anchors(g)
        start = doc[0]
        for i, p in enumerate(pts):
            start.insert_link({"kind": pymupdf.LINK_GOTO, "from": Rect(10, 10 + 20 * i, 20, 20 + 20 * i),
                               "page": 1, "to": p})
        to_page = ~_page_to_pdf(g)
        got = []
        for xref in [a[0] for a in start.annot_xrefs() if a[1] == pymupdf.PDF_ANNOT_LINK]:
            m = _XYZ.search(doc.xref_object(xref, compressed=True))
            if not m:
                raise _CalibrationError("destinazione sonda non trovata")
            got.append(Point(float(m.group(1)), float(m.group(2))) * to_page)
        if len(got) != 3:
            raise _CalibrationError("collegamenti sonda non trovati")
        return list(zip(pts, got))
    finally:
        doc.close()


def _probe_annot(g: Geometry) -> list[tuple[Point, Point]]:
    # /Rect letto dal file: ``Annot.rect`` sbaglia con /CropBox più grande del
    # MediaBox su pagine ruotate (usa le dimensioni del /CropBox del file).
    doc, page = _probe_page(g)
    try:
        rects = _link_rects(g)
        xrefs = [page.add_redact_annot(r).xref for r in rects]
        to_page = ~_page_to_pdf(g)
        got = [_center(Rect(_box(_resolve(doc, doc.xref_get_key(x, "Rect")))) * to_page) for x in xrefs]
        return [(_center(r), p) for r, p in zip(rects, got)]
    finally:
        doc.close()


_PROBES = {
    "shape": _probe_shape, "shapemorph": _probe_shapemorph, "image": _probe_image, "text": _probe_text,
    "textmorph": _probe_textmorph, "textbox": _probe_textbox, "textboxmorph": _probe_textboxmorph,
    "htmlbox": _probe_htmlbox, "textwriter": _probe_textwriter, "pdfpage": _probe_pdfpage,
    "link": _probe_link, "linkdest": _probe_linkdest, "annot": _probe_annot,
}


def _measure(g: Geometry, kind: str) -> Point:
    """Scarto (riletto - richiesto) misurato con una sonda; deve essere una traslazione."""
    pairs = _PROBES[kind](g)
    offsets = [Point(got) - Point(want) for want, got in pairs]
    for o in offsets[1:]:
        if abs(o - offsets[0]) > _CONSISTENCY:
            raise _CalibrationError(f"lo scarto di «{kind}» non è una traslazione: {offsets}")
    n = len(offsets)
    return Point(round(sum(o.x for o in offsets) / n, 3), round(sum(o.y for o in offsets) / n, 3))


def formula_offset(page: pymupdf.Page, kind: str) -> Point:
    """Scarto previsto dalle formule (PyMuPDF 2.0) per ``kind`` su questa pagina.

    È il ripiego quando la misura non riesce; vedi le formule in testa al modulo.
    """
    if kind not in KINDS:
        raise ValueError(f"tipo di inserimento sconosciuto: {kind!r}")
    with ENGINE_LOCK:
        g = geometry(page)
        vx0, _vy0, _vx1, vy1 = g.visible
        if kind == "annot":
            return Point(0, 0)
        if kind in ("text", "textmorph", "textbox", "textboxmorph", "shapemorph"):
            cbp = page.cropbox_position         # /CropBox del file, non intersecato
            return Point(cbp.x - vx0, vy1 - page.mediabox_size.y + cbp.y)
        if kind == "textwriter":
            cbp, mb, r = page.cropbox_position, page.mediabox, page.rect
            delta = r.height - r.width if g.rotation in (90, 270) else 0
            return Point(cbp.x - vx0, vy1 - r.height - cbp.y - mb.y0 + delta)
        if g.rotation == 0:
            return Point(0, 0)
        return Point(-vx0, vy1 - page.cropbox.height)


_cache: OrderedDict[tuple, Point] = OrderedDict()


def clear_cache() -> None:
    """Svuota la cache delle misure (utile nei test)."""
    with ENGINE_LOCK:
        _cache.clear()


def _offset(page: pymupdf.Page, kind: str) -> Point:
    if kind not in KINDS:
        raise ValueError(f"tipo di inserimento sconosciuto: {kind!r} (validi: {', '.join(KINDS)})")
    with ENGINE_LOCK:
        g = geometry(page)
        key = (g.key, kind)
        off = _cache.get(key)
        if off is None:
            try:
                off = _measure(g, kind)
            except Exception as e:      # misura impossibile: formule note
                off = formula_offset(page, kind)
                log.warning("placement: misura di «%s» non riuscita (%s); uso la formula %s", kind, e, off)
            _cache[key] = off
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
        else:
            _cache.move_to_end(key)
        return Point(off)


def insertion_matrix(page: pymupdf.Page, kind: str) -> Matrix:
    """Matrice da coordinate standard alle coordinate da passare alla funzione del motore ``kind``.

    Esempio: ``page.draw_rect(rect * insertion_matrix(page, "shape"))`` disegna
    il rettangolo esattamente in ``rect``.
    """
    off = _offset(page, kind)
    return Matrix(1, 0, 0, 1, -off.x, -off.y)


def to_insertion_point(page: pymupdf.Page, point, kind: str) -> Point:
    """Punto standard convertito per la funzione del motore ``kind``."""
    return Point(point) * insertion_matrix(page, kind)


def to_insertion_rect(page: pymupdf.Page, rect, kind: str) -> Rect:
    """Rettangolo standard convertito per la funzione del motore ``kind``."""
    return Rect(rect) * insertion_matrix(page, kind)


# --------------------------------------------------------------- utilità comuni
def _rotation(page: pymupdf.Page, rotate: int, upright: bool) -> int:
    return (int(rotate) + (geometry(page).rotation if upright else 0)) % 360


def _morph(morph, page: pymupdf.Page, kind: str):
    """``morph=(punto_fisso, matrice)`` con il punto fisso convertito per ``kind``."""
    if morph is None:
        return None
    return to_insertion_point(page, morph[0], kind), Matrix(morph[1])


def _tetra(value, m: Matrix):
    """Trasforma un rettangolo o un quadrilatero (rect-like o quad-like)."""
    if isinstance(value, Quad) or (len(value) == 4 and not isinstance(value[0], (int, float))):
        return Quad(value) * m
    return Rect(value) * m


# --------------------------------------------------------------------- disegno
class PlacedShape:
    """Come ``pymupdf.Shape`` ma in coordinate standard (``new_shape(page)``).

    Metodi ``draw_*``, ``finish``, ``insert_text``, ``insert_textbox`` e
    ``commit`` con gli stessi argomenti del motore; ``last_point`` e ``rect``
    sono in coordinate standard. L'oggetto del motore è in ``.shape``.

        shape = placement.new_shape(page)
        shape.draw_rect(pymupdf.Rect(50, 50, 150, 100))
        shape.draw_line((50, 120), (150, 120))
        shape.finish(color=(0, 0, 1), width=2)
        shape.commit()
    """

    def __init__(self, page: pymupdf.Page):
        self.page = page
        with ENGINE_LOCK:
            self.shape = page.new_shape()
            self._m = insertion_matrix(page, "shape")
        self._back = ~self._m

    def _p(self, p) -> Point:
        return Point(p) * self._m

    def _ret(self, p):
        return None if p is None else Point(p) * self._back

    @property
    def last_point(self) -> Point | None:
        return self._ret(self.shape.last_point)

    @property
    def rect(self) -> Rect | None:
        return None if self.shape.rect is None else Rect(self.shape.rect) * self._back

    def draw_line(self, p1, p2) -> Point:
        return self._ret(self.shape.draw_line(self._p(p1), self._p(p2)))

    def draw_squiggle(self, p1, p2, breadth: float = 2) -> Point:
        return self._ret(self.shape.draw_squiggle(self._p(p1), self._p(p2), breadth=breadth))

    def draw_zigzag(self, p1, p2, breadth: float = 2) -> Point:
        return self._ret(self.shape.draw_zigzag(self._p(p1), self._p(p2), breadth=breadth))

    def draw_polyline(self, points) -> Point:
        return self._ret(self.shape.draw_polyline([self._p(p) for p in points]))

    def draw_bezier(self, p1, p2, p3, p4) -> Point:
        return self._ret(self.shape.draw_bezier(self._p(p1), self._p(p2), self._p(p3), self._p(p4)))

    def draw_oval(self, tetra) -> Point:
        return self._ret(self.shape.draw_oval(_tetra(tetra, self._m)))

    def draw_circle(self, center, radius: float) -> Point:
        return self._ret(self.shape.draw_circle(self._p(center), radius))

    def draw_curve(self, p1, p2, p3) -> Point:
        return self._ret(self.shape.draw_curve(self._p(p1), self._p(p2), self._p(p3)))

    def draw_sector(self, center, point, beta: float, fullSector: bool = True) -> Point:  # noqa: N803
        return self._ret(self.shape.draw_sector(self._p(center), self._p(point), beta, fullSector=fullSector))

    def draw_rect(self, rect, *, radius=None) -> Point:
        return self._ret(self.shape.draw_rect(Rect(rect) * self._m, radius=radius))

    def draw_quad(self, quad) -> Point:
        return self._ret(self.shape.draw_quad(Quad(quad) * self._m))

    def finish(self, *args, morph=None, **kw) -> None:
        """Come ``Shape.finish``; il punto fisso di ``morph`` (per nome) è in coordinate standard."""
        with ENGINE_LOCK:
            return self.shape.finish(*args, morph=_morph(morph, self.page, "shapemorph"), **kw)

    def insert_text(self, point, buffer, *, rotate: int = 0, upright: bool = False, morph=None, **kw) -> int:
        with ENGINE_LOCK:
            return self.shape.insert_text(to_insertion_point(self.page, point, "text"), buffer,
                                          rotate=_rotation(self.page, rotate, upright),
                                          morph=_morph(morph, self.page, "textmorph"), **kw)

    def insert_textbox(self, rect, buffer, *, rotate: int = 0, upright: bool = False, morph=None, **kw) -> float:
        with ENGINE_LOCK:
            return self.shape.insert_textbox(to_insertion_rect(self.page, rect, "textbox"), buffer,
                                             rotate=_rotation(self.page, rotate, upright),
                                             morph=_morph(morph, self.page, "textboxmorph"), **kw)

    def commit(self, overlay: bool = True) -> None:
        with ENGINE_LOCK:
            self.shape.commit(overlay)

    def __getattr__(self, name: str) -> Any:
        if name == "shape":
            raise AttributeError(name)
        return getattr(self.shape, name)


def new_shape(page: pymupdf.Page) -> PlacedShape:
    """Forma composta (più tracciati e testi, un solo ``commit``) in coordinate standard."""
    return PlacedShape(page)


def _draw(page: pymupdf.Page, method: str, args, kw: dict) -> Point:
    """Chiama ``page.<method>`` con gli argomenti ``args(m)`` convertiti dalla matrice ``m``."""
    with ENGINE_LOCK:
        m = insertion_matrix(page, "shape")
        if kw.get("morph") is not None:
            kw["morph"] = _morph(kw["morph"], page, "shapemorph")
        result = getattr(page, method)(*args(m), **kw)
        return None if result is None else Point(result) * ~m


def draw_line(page: pymupdf.Page, p1, p2, **kw) -> Point:
    """``Page.draw_line`` in coordinate standard: ``draw_line(page, (50, 50), (200, 50), color=(1, 0, 0))``."""
    return _draw(page, "draw_line", lambda m: (Point(p1) * m, Point(p2) * m), kw)


def draw_squiggle(page: pymupdf.Page, p1, p2, **kw) -> Point:
    """``Page.draw_squiggle`` in coordinate standard."""
    return _draw(page, "draw_squiggle", lambda m: (Point(p1) * m, Point(p2) * m), kw)


def draw_zigzag(page: pymupdf.Page, p1, p2, **kw) -> Point:
    """``Page.draw_zigzag`` in coordinate standard."""
    return _draw(page, "draw_zigzag", lambda m: (Point(p1) * m, Point(p2) * m), kw)


def draw_polyline(page: pymupdf.Page, points, **kw) -> Point:
    """``Page.draw_polyline`` in coordinate standard: ``draw_polyline(page, [(10, 10), (60, 40), (10, 70)])``."""
    points = list(points)
    return _draw(page, "draw_polyline", lambda m: ([Point(p) * m for p in points],), kw)


def draw_bezier(page: pymupdf.Page, p1, p2, p3, p4, **kw) -> Point:
    """``Page.draw_bezier`` in coordinate standard."""
    return _draw(page, "draw_bezier", lambda m: tuple(Point(p) * m for p in (p1, p2, p3, p4)), kw)


def draw_oval(page: pymupdf.Page, rect, **kw) -> Point:
    """``Page.draw_oval`` (rettangolo o quadrilatero) in coordinate standard."""
    return _draw(page, "draw_oval", lambda m: (_tetra(rect, m),), kw)


def draw_circle(page: pymupdf.Page, center, radius: float, **kw) -> Point:
    """``Page.draw_circle`` in coordinate standard: ``draw_circle(page, (300, 400), 20, fill=(0, 1, 0))``."""
    return _draw(page, "draw_circle", lambda m: (Point(center) * m, radius), kw)


def draw_curve(page: pymupdf.Page, p1, p2, p3, **kw) -> Point:
    """``Page.draw_curve`` in coordinate standard."""
    return _draw(page, "draw_curve", lambda m: tuple(Point(p) * m for p in (p1, p2, p3)), kw)


def draw_sector(page: pymupdf.Page, center, point, beta: float, **kw) -> Point:
    """``Page.draw_sector`` in coordinate standard."""
    return _draw(page, "draw_sector", lambda m: (Point(center) * m, Point(point) * m, beta), kw)


def draw_rect(page: pymupdf.Page, rect, **kw) -> Point:
    """``Page.draw_rect`` in coordinate standard: ``draw_rect(page, Rect(150, 300, 190, 330), fill=(1, 0, 0))``."""
    return _draw(page, "draw_rect", lambda m: (Rect(rect) * m,), kw)


def draw_quad(page: pymupdf.Page, quad, **kw) -> Point:
    """``Page.draw_quad`` in coordinate standard."""
    return _draw(page, "draw_quad", lambda m: (Quad(quad) * m,), kw)


# ----------------------------------------------------------------------- testo
def insert_text(page: pymupdf.Page, point, text, *, rotate: int = 0, upright: bool = False,
                morph=None, **kw) -> int:
    """``Page.insert_text``: ``point`` è l'origine della prima riga (linea di base) in coordinate standard.

    ``upright=True``: testo leggibile nel verso della pagina visualizzata.

        insert_text(page, (72, 100), "Pagina 1 di 3", fontsize=9, upright=True)
    """
    with ENGINE_LOCK:
        return page.insert_text(to_insertion_point(page, point, "text"), text,
                                rotate=_rotation(page, rotate, upright), morph=_morph(morph, page, "textmorph"), **kw)


def insert_textbox(page: pymupdf.Page, rect, text, *, rotate: int = 0, upright: bool = False,
                   morph=None, **kw) -> float:
    """``Page.insert_textbox`` con ``rect`` in coordinate standard (restituisce lo spazio rimasto, <0 se non sta).

        insert_textbox(page, Rect(72, 72, 300, 200), "Testo a capo automatico", fontsize=11, upright=True)
    """
    with ENGINE_LOCK:
        return page.insert_textbox(to_insertion_rect(page, rect, "textbox"), text,
                                   rotate=_rotation(page, rotate, upright), morph=_morph(morph, page, "textboxmorph"),
                                   **kw)


def _link_xrefs(page: pymupdf.Page) -> list[int]:
    return [a[0] for a in page.annot_xrefs() if a[1] == pymupdf.PDF_ANNOT_LINK]


def insert_htmlbox(page: pymupdf.Page, rect, text, *, rotate: int = 0, upright: bool = False, **kw) -> tuple:
    """``Page.insert_htmlbox`` con ``rect`` in coordinate standard; restituisce ``(spazio_rimasto, scala)``.

    Anche i collegamenti contenuti nell'HTML finiscono sul testo a cui appartengono.

        insert_htmlbox(page, Rect(72, 72, 400, 300), "<b>Nota:</b> verificato", css="* {font-size: 10pt}")
    """
    with ENGINE_LOCK:
        m = insertion_matrix(page, "htmlbox")
        before = set(_link_xrefs(page))
        result = page.insert_htmlbox(Rect(rect) * m, text, rotate=_rotation(page, rotate, upright), **kw)
        # Il motore reinserisce i collegamenti dell'HTML con insert_link,
        # relativi al rettangolo già corretto per l'HTML: se le due correzioni
        # differiscono vanno spostati della differenza.
        added = [x for x in _link_xrefs(page) if x not in before]
        delta = _offset(page, "htmlbox") - _offset(page, "link") if added else Point(0, 0)
        if abs(delta) > 1e-3:
            doc = page.parent
            move = pdf_to_page_matrix(page) * Matrix(1, 0, 0, 1, delta.x, delta.y) * page_to_pdf_matrix(page)
            for xref in added:
                r = Rect(_box(_resolve(doc, doc.xref_get_key(xref, "Rect")))) * move
                doc.xref_set_key(xref, "Rect", "[" + " ".join(_fmt(v) for v in r) + "]")
        return result


def _writer_from_items(page: pymupdf.Page, items: Iterable, upright: bool) -> pymupdf.TextWriter:
    """TextWriter con i testi ``items`` (ancore standard), composto nel sistema giusto."""
    writer = pymupdf.TextWriter(page.rect)
    to_writer = page_to_display_matrix(page) if upright else _IDENTITY
    for item in items:
        if isinstance(item, dict):
            options = dict(item)
            pos = options.pop("pos", None)
            if pos is None:
                pos = options.pop("point")
            text = options.pop("text")
        else:
            pos, text, *rest = item
            options = dict(rest[0]) if rest else {}
        writer.append(Point(pos) * to_writer, text, **options)
    return writer


def write_text(page: pymupdf.Page, writer, *, upright: bool = False, color=None, opacity: float = -1,
               overlay: bool = True, morph=None, matrix=None, render_mode: int = 0, oc: int = 0) -> pymupdf.TextWriter:
    """Scrive testo con ``TextWriter`` esattamente dove richiesto; restituisce il TextWriter usato.

    ``writer`` può essere:

    - un ``pymupdf.TextWriter(page.rect)`` già composto: con ``upright=False`` le
      sue coordinate sono standard (pagina non ruotata, come nel motore); con
      ``upright=True`` sono coordinate della pagina *visualizzata* (``page.rect``,
      vedi ``page_to_display_matrix``) e il testo risulta diritto a schermo;
    - un elenco di testi con ancora (origine della linea di base) **sempre in
      coordinate standard**: tuple ``(punto, testo)`` o ``(punto, testo,
      {opzioni di append})``, oppure dict ``{"pos": punto, "text": testo,
      "fontsize": 12, ...}``. Con ``upright=True`` ogni testo è diritto a schermo.

    ``morph`` (punto fisso nelle coordinate del writer) e ``matrix`` hanno il
    significato del motore. Esempio::

        write_text(page, [((72, 90), "Bozza", {"fontsize": 20})], upright=True, color=(1, 0, 0))
    """
    with ENGINE_LOCK:
        if not isinstance(writer, pymupdf.TextWriter):
            writer = _writer_from_items(page, writer, upright)
        off = _offset(page, "textwriter")
        ctm = Matrix(getattr(writer, "ctm", None) or Matrix(1, 0, 0, -1, 0, writer.rect.height))
        writer_to_page = display_to_page_matrix(page) if upright else _IDENTITY
        correction = ctm * writer_to_page * Matrix(1, 0, 0, 1, -off.x, -off.y) * ~ctm
        user = _IDENTITY
        if morph is not None:
            p = Point(morph[0]) * ~ctm
            user = Matrix(1, 0, 0, 1, -p.x, -p.y) * Matrix(morph[1]) * Matrix(1, 0, 0, 1, p.x, p.y)
        elif matrix is not None:
            user = Matrix(matrix)
        if overlay:
            page.wrap_contents()        # il motore non isola il contenuto esistente
        writer.write_text(page, color=color, opacity=opacity, overlay=overlay, matrix=user * correction,
                          render_mode=render_mode, oc=oc)
        return writer


def fill_textbox(page: pymupdf.Page, rect, text, *, upright: bool = False, pos=None, font=None,
                 fontsize: float = 11, lineheight=None, align: int = 0, warn=None, right_to_left: bool = False,
                 small_caps: bool = False, color=None, opacity: float = 1, overlay: bool = True,
                 render_mode: int = 0, oc: int = 0) -> list:
    """``TextWriter.fill_textbox`` + ``write_text`` con ``rect`` (e ``pos``) in coordinate standard.

    Restituisce le righe che non sono entrate nel rettangolo (come il motore).

        fill_textbox(page, Rect(72, 600, 520, 760), testo, fontsize=10, align=3, upright=True)
    """
    with ENGINE_LOCK:
        writer = pymupdf.TextWriter(page.rect, opacity=opacity, color=color)
        to_writer = page_to_display_matrix(page) if upright else _IDENTITY
        box = Rect(rect) * to_writer
        start = None if pos is None else Point(pos) * to_writer
        lines = writer.fill_textbox(box, text, pos=start, font=font, fontsize=fontsize, lineheight=lineheight,
                                    align=align, warn=warn, right_to_left=right_to_left, small_caps=small_caps)
        write_text(page, writer, upright=upright, overlay=overlay, render_mode=render_mode, oc=oc)
        return lines


# ------------------------------------------------------------ immagini e pagine
def insert_image(page: pymupdf.Page, rect, *, rotate: int = 0, upright: bool = False, **kw) -> int:
    """``Page.insert_image`` (file, stream, pixmap o xref) nel rettangolo standard ``rect``; restituisce l'xref.

        insert_image(page, Rect(400, 700, 520, 780), filename="firma.png", upright=True)
    """
    with ENGINE_LOCK:
        return page.insert_image(to_insertion_rect(page, rect, "image"), rotate=_rotation(page, rotate, upright), **kw)


def show_pdf_page(page: pymupdf.Page, rect, src: pymupdf.Document, pno: int = 0, *, keep_proportion: bool = True,
                  overlay: bool = True, oc: int = 0, rotate: int = 0, clip=None, upright: bool = False,
                  as_displayed: bool = True) -> int:
    """Mostra la pagina ``pno`` di ``src`` nel rettangolo standard ``rect``; restituisce l'xref dell'oggetto.

    ``as_displayed=True``: la pagina sorgente appare come la si vede (con la
    sua /Rotate e ritagliata al suo CropBox); ``False``: contenuto non ruotato,
    come nel motore. ``clip`` è in coordinate standard della pagina sorgente.

        show_pdf_page(page, Rect(36, 36, 300, 400), altro_pdf, 0, upright=True)
    """
    with ENGINE_LOCK:
        target = to_insertion_rect(page, rect, "pdfpage")
        while pno < 0:
            pno += src.page_count
        src_page = src[pno]
        src_rotation = geometry(src_page).rotation
        total = (int(rotate) + (geometry(page).rotation if upright else 0)
                 - (src_rotation if as_displayed else 0)) % 360
        if not src_rotation:
            return page.show_pdf_page(target, src, pno, keep_proportion=keep_proportion, overlay=overlay,
                                      oc=oc, rotate=total, clip=clip)
        # Con una sorgente ruotata il motore calcola male l'area sorgente: la
        # rotazione viene tolta per la durata della chiamata (come fa il motore
        # stesso per estrarre il testo) e ripristinata esattamente.
        raw = src.xref_get_key(src_page.xref, "Rotate")
        src.xref_set_key(src_page.xref, "Rotate", "0")
        try:
            return page.show_pdf_page(target, src, pno, keep_proportion=keep_proportion, overlay=overlay,
                                      oc=oc, rotate=total, clip=clip)
        finally:
            src.xref_set_key(src_page.xref, "Rotate", "null" if raw[0] == "null" else raw[1])


# ------------------------------------------------------- collegamenti e redazioni
def _link_for_engine(page: pymupdf.Page, link: dict) -> dict:
    lnk = dict(link)
    lnk["from"] = to_insertion_rect(page, lnk["from"], "link")
    dest = lnk.get("page", -1)
    if lnk.get("kind") == pymupdf.LINK_GOTO and isinstance(dest, int) and 0 <= dest < page.parent.page_count:
        # senza "to" il motore usa (0, 0): l'angolo in alto a sinistra, da correggere anch'esso
        lnk["to"] = to_insertion_point(page.parent[dest], lnk.get("to") or (0, 0), "linkdest")
    return lnk


def insert_link(page: pymupdf.Page, link: dict, mark: bool = True) -> None:
    """``Page.insert_link`` con ``link["from"]`` standard e, per i collegamenti interni,
    ``link["to"]`` in coordinate standard della pagina di destinazione.

        insert_link(page, {"kind": pymupdf.LINK_GOTO, "from": Rect(72, 700, 200, 715), "page": 4, "to": Point(72, 90)})
        insert_link(page, {"kind": pymupdf.LINK_URI, "from": Rect(72, 720, 200, 735), "uri": "https://example.org"})
    """
    with ENGINE_LOCK:
        page.insert_link(_link_for_engine(page, link), mark=mark)


def update_link(page: pymupdf.Page, link: dict) -> None:
    """``Page.update_link`` per un collegamento letto con ``get_links`` (coordinate standard)."""
    with ENGINE_LOCK:
        page.update_link(_link_for_engine(page, link))


def get_links(page: pymupdf.Page) -> list[dict]:
    """``Page.get_links`` con ``from`` e (per i collegamenti interni) ``to`` in coordinate standard.

    Il motore restituisce coordinate della pagina visualizzata (ruotata); per
    destinazioni con una sola coordinata su pagine ruotate ``to`` è indicativo.
    Come nel motore, i collegamenti appena aggiunti si vedono solo dopo
    ``page = doc.reload_page(page)``.
    """
    with ENGINE_LOCK:
        links = page.get_links()
        back = display_to_page_matrix(page)
        doc = page.parent
        for link in links:
            if "from" in link:
                link["from"] = Rect(link["from"]) * back
            dest = link.get("page", -1)
            if (link.get("kind") == pymupdf.LINK_GOTO and isinstance(link.get("to"), Point)
                    and isinstance(dest, int) and 0 <= dest < doc.page_count):
                try:
                    link["to"] = link["to"] * display_to_page_matrix(doc[dest])
                except PlacementError:
                    pass        # pagina di destinazione non gestibile: valore del motore
        return links


def add_redact_annot(page: pymupdf.Page, quad, **kw) -> pymupdf.Annot:
    """``Page.add_redact_annot`` con rettangolo o quadrilatero in coordinate standard.

    Dopo ``page.apply_redactions()`` sparisce esattamente il contenuto dell'area.

        add_redact_annot(page, Rect(100, 200, 300, 220), fill=(0, 0, 0))
        page.apply_redactions()
    """
    with ENGINE_LOCK:
        return page.add_redact_annot(_tetra(quad, insertion_matrix(page, "annot")), **kw)
