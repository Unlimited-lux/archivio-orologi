"""Filigrane, sfondi, numeri di pagina, Bates, intestazioni/piè di pagina ed
etichette di pagina (senza interfaccia): PAG-33, 34, 35, 36, 38, 39.

Come per `engine.py`, le funzioni ricevono un `pymupdf.Document` e lo
modificano sul posto: chiamarle dentro `with document.change(...) as pdf:`.

Come si ritrovano i timbri
--------------------------
Ogni applicazione è un *timbro* con un identificativo. Il timbro è disegnato
su una pagina temporanea grande quanto la pagina *visualizzata*, in verticale,
e inserito con `placement.show_pdf_page(..., upright=True)` (corretto su
pagine ruotate, CropBox spostati o più grandi del MediaBox) come Form XObject:

- il nome della risorsa è ``/FolStamp_<tipo>_<id>``;
- il flusso di contenuto aggiunto è separato dall'originale e marcato come
  artefatto (``/Artifact <</Type/Pagination/Subtype/Watermark>> BDC … EMC``,
  ``/Type/Background`` per gli sfondi), quindi è escluso dall'albero di
  struttura e dall'ordine di lettura dei PDF taggati;
- il Form XObject porta ``/FoliantStamp <</K tipo /Id (id) /P <json>>>`` con i
  parametri usati, così «Modifica» li ritrova e sostituisce senza duplicati;
- la visibilità dipende da un gruppo di contenuto facoltativo «Foliant: …»,
  con ``/Usage`` ``/View`` e ``/Print`` per le varianti solo schermo o solo stampa.

La rimozione toglie il flusso (o il blocco, se i flussi sono stati uniti) e la
voce nelle risorse: il rendering torna identico all'originale.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import uuid
from collections.abc import Iterable, Sequence

import pymupdf

from foliant.api import _, placement

from . import fields
from .engine import PageOpError

# Tipi di timbro.
WATERMARK = "watermark"
BACKGROUND = "background"
NUMBERS = "numbers"
BATES = "bates"
HEADERFOOTER = "headerfooter"
KINDS = (WATERMARK, BACKGROUND, NUMBERS, BATES, HEADERFOOTER)

POSITIONS = (
    "top-left", "top-center", "top-right",
    "middle-left", "center", "middle-right",
    "bottom-left", "bottom-center", "bottom-right",
)
_POSITION_ALIASES = {"top": "top-center", "bottom": "bottom-center", "left": "middle-left",
                     "right": "middle-right", "middle": "center", "center-left": "middle-left",
                     "center-right": "middle-right", "middle-center": "center"}

SLOTS = ("header_left", "header_center", "header_right", "footer_left", "footer_center", "footer_right")
_SLOT_POS = {"header_left": "top-left", "header_center": "top-center", "header_right": "top-right",
             "footer_left": "bottom-left", "footer_center": "bottom-center", "footer_right": "bottom-right"}

# Visibilità (contenuto facoltativo).
VISIBILITY = ("both", "screen", "print")

# Scala di sfondi e immagini.
SCALES = ("fit", "fill", "percent", "actual")

# Stili dei numeri (come le etichette PDF).
NUMBER_STYLES = ("D", "r", "R", "a", "A")

# Font standard del PDF (non richiedono incorporamento).
FONTS = {
    "helv": "Helvetica", "hebo": "Helvetica grassetto", "heit": "Helvetica corsivo",
    "tiro": "Times", "tibo": "Times grassetto", "tiit": "Times corsivo",
    "cour": "Courier", "cobo": "Courier grassetto",
}

LABEL_STYLES = ("D", "r", "R", "a", "A", "")

_NAME_PREFIX = "FolStamp_"
_NAME_RE = re.compile(rb"/FolStamp_([a-z]+)_([A-Za-z0-9]+)\s+Do")
_FZ_RE = re.compile(rb"/(fzFrm\d+)\s+Do")


def _layer_names() -> dict[str, str]:
    return {
        WATERMARK: _("Foliant: filigrana"),
        BACKGROUND: _("Foliant: sfondo"),
        NUMBERS: _("Foliant: numeri di pagina"),
        BATES: _("Foliant: numerazione Bates"),
        HEADERFOOTER: _("Foliant: intestazione e piè di pagina"),
    }


# Nomi dei livelli in tutte le lingue (per ritrovarli a prescindere dalla lingua attiva).
_LAYER_NAMES_ALL = {
    WATERMARK: {"Foliant: filigrana", "Foliant: watermark"},
    BACKGROUND: {"Foliant: sfondo", "Foliant: background"},
    NUMBERS: {"Foliant: numeri di pagina", "Foliant: page numbers"},
    BATES: {"Foliant: numerazione Bates", "Foliant: Bates numbering"},
    HEADERFOOTER: {"Foliant: intestazione e piè di pagina", "Foliant: header and footer"},
}
_VIS_SUFFIX = {"screen": " (schermo)", "print": " (stampa)"}


# =========================================================================
# Validazione
# =========================================================================

def _target_pages(pdf: pymupdf.Document, pages: Iterable[int] | None) -> list[int]:
    if not pdf.is_pdf:
        raise PageOpError(_("Il documento non è un PDF."))
    n = pdf.page_count
    if n == 0:
        raise PageOpError(_("Il documento non ha pagine."))
    if pages is None:
        return list(range(n))
    result: list[int] = []
    seen: set[int] = set()
    for p in pages:
        try:
            i = int(p)
        except (TypeError, ValueError):
            raise PageOpError(_("Numero di pagina non valido.")) from None
        if i < 0 or i >= n:
            raise PageOpError(_("La pagina {n} non esiste (il documento ha {tot} pagine).").format(n=i + 1, tot=n))
        if i not in seen:
            seen.add(i)
            result.append(i)
    if not result:
        raise PageOpError(_("Indica almeno una pagina."))
    return sorted(result)


def _check_color(color, what: str | None = None) -> tuple[float, float, float]:
    try:
        vals = tuple(float(c) for c in color)
    except (TypeError, ValueError):
        vals = ()
    if len(vals) != 3 or any(not (0.0 <= c <= 1.0) or math.isnan(c) for c in vals):
        raise PageOpError(_("Colore non valido: servono tre valori tra 0 e 1."))
    return vals  # type: ignore[return-value]


def _check_opacity(opacity) -> float:
    try:
        value = float(opacity)
    except (TypeError, ValueError):
        value = -1.0
    if not (0.0 < value <= 1.0) or math.isnan(value):
        raise PageOpError(_("L'opacità deve essere compresa tra 1% e 100%."))
    return value


def _check_font_size(size) -> float:
    try:
        value = float(size)
    except (TypeError, ValueError):
        value = -1.0
    if not (1.0 <= value <= 1000.0):
        raise PageOpError(_("La dimensione del carattere deve essere tra 1 e 1000 punti."))
    return value


def _check_margin(margin) -> float:
    try:
        value = float(margin)
    except (TypeError, ValueError):
        value = -1.0
    if not (0.0 <= value <= 2000.0):
        raise PageOpError(_("Il margine deve essere tra 0 e 2000 punti."))
    return value


def _check_position(position: str) -> str:
    pos = _POSITION_ALIASES.get(str(position).strip().lower(), str(position).strip().lower())
    if pos not in POSITIONS:
        raise PageOpError(_("Posizione «{p}» non valida.").format(p=position))
    return pos


class _Font:
    """Font per il disegno: standard (nome breve) o file TrueType/OpenType."""

    def __init__(self, font: str | None):
        font = font or "helv"
        self.file: str | None = None
        try:
            if os.path.sep in font or font.lower().endswith((".ttf", ".otf", ".ttc")):
                if not os.path.isfile(font):
                    raise PageOpError(_("File del carattere «{f}» non trovato.").format(f=os.path.basename(font)))
                self.file = font
                self.name = "FolFont"
                self.font = pymupdf.Font(fontfile=font)
            else:
                if font not in FONTS:
                    raise PageOpError(_("Carattere «{f}» non disponibile.").format(f=font))
                self.name = font
                self.font = pymupdf.Font(font)
        except PageOpError:
            raise
        except Exception:
            raise PageOpError(_("Impossibile caricare il carattere «{f}».").format(f=font)) from None

    def width(self, text: str, size: float) -> float:
        return self.font.text_length(text, fontsize=size)

    @property
    def ascender(self) -> float:
        return self.font.ascender

    @property
    def descender(self) -> float:
        return self.font.descender

    def kwargs(self) -> dict:
        kw = {"fontname": self.name}
        if self.file:
            kw["fontfile"] = self.file
        return kw


def _load_image(image) -> tuple[bytes, int, int]:
    """Legge un'immagine (percorso o bytes) e restituisce dati e dimensioni in pixel."""
    if image is None:
        raise PageOpError(_("Nessuna immagine indicata."))
    if isinstance(image, (bytes, bytearray, memoryview)):
        data = bytes(image)
        name = _("immagine")
    else:
        path = os.fspath(image)
        name = os.path.basename(path)
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            raise PageOpError(_("Il file «{f}» non esiste.").format(f=name)) from None
        except OSError as e:
            raise PageOpError(_("Impossibile leggere «{f}»: {e}.").format(f=name, e=e.strerror)) from None
    if not data:
        raise PageOpError(_("L'immagine «{f}» è vuota.").format(f=name))
    try:
        pix = pymupdf.Pixmap(data)
        w, h = pix.width, pix.height
    except Exception:
        raise PageOpError(_("«{f}» non è un'immagine leggibile.").format(f=name)) from None
    if w <= 0 or h <= 0:
        raise PageOpError(_("«{f}» non è un'immagine leggibile.").format(f=name))
    return data, w, h




# =========================================================================
# Sorgenti grafiche: immagine o pagina PDF
# =========================================================================

class _Source:
    """Immagine o pagina di un PDF da disegnare (incorporata una sola volta per livello)."""

    def __init__(self, image=None, pdf=None, page: int = 0):
        self.image = None
        self.doc: pymupdf.Document | None = None
        self.own_doc = False
        self.page = 0
        self._img_doc: pymupdf.Document | None = None
        if image is not None:
            self.image = _load_image(image)
            data, w, h = self.image
            try:
                pix = pymupdf.Pixmap(data)
                dpi = pix.xres if pix.xres and pix.xres > 1 else 72
            except Exception:
                dpi = 72
            self.natural = (w * 72.0 / dpi, h * 72.0 / dpi)
            self.aspect = h / w
        elif pdf is not None:
            self.doc, self.own_doc = _open_pdf_source(pdf)
            if not (0 <= int(page) < self.doc.page_count):
                self.close()
                raise PageOpError(_("Il PDF sorgente non ha la pagina {n}.").format(n=int(page) + 1))
            self.page = int(page)
            self.natural = self.size_of(self.page)
            self.aspect = self.natural[1] / self.natural[0]
        else:
            raise PageOpError(_("Nessuna immagine indicata."))

    def size_of(self, pno: int) -> tuple[float, float]:
        r = self.doc[pno].rect  # dimensioni visualizzate
        return max(1.0, r.width), max(1.0, r.height)

    def page_count(self) -> int:
        return self.doc.page_count if self.doc is not None else 1

    def draw(self, layer: _Layer, center: pymupdf.Point, w: float, h: float, rotation: float,
             pno: int | None = None, keep: bool = False) -> None:
        """Disegna il contenuto w×h (non ruotato) centrato in `center`, ruotato di `rotation`."""
        bw, bh = _rotated_box(w, h, rotation)
        rect = pymupdf.Rect(center.x - bw / 2, center.y - bh / 2, center.x + bw / 2, center.y + bh / 2)
        if self.doc is not None:
            placement.show_pdf_page(layer.page, rect, self.doc, self.page if pno is None else pno,
                                    keep_proportion=True, rotate=rotation, as_displayed=True)
        elif rotation % 90 == 0:
            quarter = int(round(rotation)) % 360
            # una sola copia dell'immagine per livello (mosaico): l'xref è conservato nel livello
            xref = layer.image_xrefs.get(id(self), 0)
            if xref:
                layer.page.insert_image(rect, xref=xref, rotate=quarter, keep_proportion=False)
            else:
                layer.image_xrefs[id(self)] = layer.page.insert_image(rect, stream=self.image[0], rotate=quarter,
                                                                      keep_proportion=False)
        else:
            if self._img_doc is None:
                self._img_doc = pymupdf.open()
                ip = self._img_doc.new_page(width=100, height=100 * self.aspect)
                ip.insert_image(ip.rect, stream=self.image[0], keep_proportion=False)
            layer.page.show_pdf_page(rect, self._img_doc, 0, keep_proportion=True, rotate=rotation)
        layer.empty = False

    def close(self) -> None:
        if self._img_doc is not None:
            self._img_doc.close()
            self._img_doc = None
        if self.doc is not None and self.own_doc:
            self.doc.close()
        self.doc = None


def _open_pdf_source(pdf) -> tuple[pymupdf.Document, bool]:
    if isinstance(pdf, pymupdf.Document):
        # Una copia: il motore non accetta come sorgente il documento di destinazione.
        return pymupdf.open("pdf", pdf.tobytes()), True
    try:
        if isinstance(pdf, (bytes, bytearray, memoryview)):
            doc = pymupdf.open("pdf", bytes(pdf))
        else:
            path = os.fspath(pdf)
            if not os.path.isfile(path):
                raise PageOpError(_("Il file «{f}» non esiste.").format(f=os.path.basename(path)))
            doc = pymupdf.open(path)
    except PageOpError:
        raise
    except Exception:
        raise PageOpError(_("Il PDF sorgente non è leggibile.")) from None
    if doc.needs_pass:
        doc.close()
        raise PageOpError(_("Il PDF sorgente è protetto da password."))
    if not doc.is_pdf or doc.page_count == 0:
        doc.close()
        raise PageOpError(_("Il PDF sorgente non è leggibile."))
    return doc, True


# =========================================================================
# Geometria
# =========================================================================

def _visible_size(page: pymupdf.Page) -> tuple[float, float]:
    """Larghezza e altezza della pagina come la si vede (area visibile, ruotata)."""
    g = placement.geometry(page)
    return (g.height, g.width) if g.rotation in (90, 270) else (g.width, g.height)


def _anchor_point(pos: str, page_w: float, page_h: float, box_w: float, box_h: float,
                  margin: float, offset: tuple[float, float] = (0.0, 0.0)) -> pymupdf.Point:
    """Centro del riquadro `box_w`×`box_h` ancorato a `pos` nella pagina visibile."""
    vert, _sep, horiz = pos.partition("-")
    if pos == "center":
        vert, horiz = "middle", "center"
    if horiz == "left":
        cx = margin + box_w / 2
    elif horiz == "right":
        cx = page_w - margin - box_w / 2
    else:
        cx = page_w / 2
    if vert == "top":
        cy = margin + box_h / 2
    elif vert == "bottom":
        cy = page_h - margin - box_h / 2
    else:
        cy = page_h / 2
    return pymupdf.Point(cx + offset[0], cy + offset[1])


def _rotated_box(w: float, h: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


def _mirror_position(pos: str, pno: int, mirror) -> str:
    """Lato esterno/interno per il fronte/retro: con "outer" le pagine dispari hanno il
    numero a destra e le pari a sinistra; "inner" il contrario (verticale da `pos`)."""
    if mirror is True:
        mirror = "outer"
    if mirror not in ("outer", "inner"):
        return pos
    vertical = pos.split("-")[0] if "-" in pos else "bottom"
    if vertical == "middle" or pos == "center":
        vertical = "middle"
    odd = (pno + 1) % 2 == 1
    right = odd if mirror == "outer" else not odd
    return f"{vertical}-{'right' if right else 'left'}"


# =========================================================================
# Disegno sul livello temporaneo
# =========================================================================

class _Layer:
    """Pagina temporanea (in verticale, dimensioni visibili) su cui disegnare un timbro."""

    def __init__(self, width: float, height: float):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=width, height=height)
        self.width = width
        self.height = height
        self.empty = True
        self.image_xrefs: dict[int, int] = {}  # sorgente → xref dell'immagine già incorporata

    def measure_text(self, lines, font: _Font, size: float):
        ws = [font.width(line, size) for line in lines]
        bw = max(ws) if ws else 0.0
        bh = (font.ascender - font.descender) * size + size * 1.2 * (len(lines) - 1)
        return ws, bw, bh

    def text_block(self, text: str, *, pos: str, margin: float, font: _Font, size: float,
                   color, rotation: float = 0.0, align: str | None = None, fit: bool = False,
                   max_width: float | None = None, underline: bool = False,
                   offset: tuple[float, float] = (0.0, 0.0), center: pymupdf.Point | None = None) -> None:
        lines = text.split("\n")
        if not any(line.strip() for line in lines):
            return
        widths, block_w, block_h = self.measure_text(lines, font, size)
        bw, bh = _rotated_box(block_w, block_h, rotation)
        if (fit or max_width) and bw > 0 and bh > 0:
            avail_w = max_width if max_width else self.width - 2 * margin
            avail_h = self.height - 2 * margin
            factor = min(1.0, avail_w / bw if avail_w > 0 else 1.0, avail_h / bh if avail_h > 0 else 1.0)
            if factor < 1.0:
                size = max(1.0, size * factor)
                widths, block_w, block_h = self.measure_text(lines, font, size)
                bw, bh = _rotated_box(block_w, block_h, rotation)
        line_h = size * 1.2
        asc = font.ascender * size
        if center is None:
            center = _anchor_point(pos, self.width, self.height, bw, bh, margin, offset)
        if align is None:
            horiz = pos.partition("-")[2] if pos != "center" else "center"
            align = horiz if horiz in ("left", "right") else "center"
        morph = (center, pymupdf.Matrix(rotation)) if rotation % 360 else None
        top = center.y - block_h / 2
        for k, (line, lw) in enumerate(zip(lines, widths)):
            if not line:
                continue
            if align == "left":
                x = center.x - block_w / 2
            elif align == "right":
                x = center.x + block_w / 2 - lw
            else:
                x = center.x - lw / 2
            baseline = top + asc + k * line_h
            self.page.insert_text((x, baseline), line, fontsize=size, color=color, morph=morph,
                                  **font.kwargs())
            if underline and line.strip():
                y = baseline + size * 0.15
                self.page.draw_line((x, y), (x + lw, y), color=color, width=max(0.4, size * 0.06), morph=morph)
        self.empty = False

    def text_box_size(self, text: str, font: _Font, size: float, rotation: float) -> tuple[float, float]:
        _w, bw, bh = self.measure_text(text.split("\n"), font, size)
        return _rotated_box(bw, bh, rotation)

    def fill(self, color) -> None:
        self.page.draw_rect(self.page.rect, color=None, fill=color, width=0)
        self.empty = False

    def finish(self, opacity: float = 1.0) -> None:
        """Unisce i flussi e applica l'opacità all'intero livello."""
        doc, page = self.doc, self.page
        xrefs = page.get_contents()
        data = b"\n".join(doc.xref_stream(x) or b"" for x in xrefs)
        if opacity < 1.0:
            _set_key(doc, page.xref, "Resources/ExtGState/FolGS",
                     f"<</Type/ExtGState/ca {opacity:.4f}/CA {opacity:.4f}>>")
            data = b"q /FolGS gs\n" + data + b"\nQ"
        if xrefs:
            doc.update_stream(xrefs[0], data)
            doc.xref_set_key(page.xref, "Contents", f"{xrefs[0]} 0 R")

    def close(self) -> None:
        self.doc.close()


def _set_key(pdf: pymupdf.Document, xref: int, path: str, value: str) -> None:
    """Come `xref_set_key` ma segue i riferimenti indiretti lungo il percorso."""
    parts = path.split("/")
    i = 0
    while i < len(parts) - 1:
        typ, val = pdf.xref_get_key(xref, "/".join(parts[:i + 1]))
        if typ == "xref":
            xref = int(val.split()[0])
            parts = parts[i + 1:]
            i = 0
            continue
        if typ == "null":
            break
        i += 1
    pdf.xref_set_key(xref, "/".join(parts), value)


# =========================================================================
# Livelli (contenuto facoltativo)
# =========================================================================

def _check_visibility(visibility: str) -> str:
    v = (visibility or "both").lower()
    if v not in VISIBILITY:
        raise PageOpError(_("Visibilità «{v}» non valida.").format(v=visibility))
    return v


def _layer_xref(pdf: pymupdf.Document, kind: str, visibility: str = "both") -> int:
    """Gruppo di contenuto facoltativo del tipo di timbro e della visibilità (creato se manca)."""
    suffix = _VIS_SUFFIX.get(visibility, "")
    names = {n + suffix for n in _LAYER_NAMES_ALL[kind]}
    try:
        for xref, info in pdf.get_ocgs().items():
            if info.get("name") in names:
                return xref
    except Exception:
        pass
    xref = pdf.add_ocg(_layer_names()[kind] + suffix, on=True)
    if visibility != "both":
        view, prn = ("ON", "OFF") if visibility == "screen" else ("OFF", "ON")
        pdf.xref_set_key(xref, "Usage", f"<</View<</ViewState/{view}>>/Print<</PrintState/{prn}>>>>")
        _update_usage_events(pdf)
    return xref


def _update_usage_events(pdf: pymupdf.Document) -> None:
    """Aggiorna /AS nella configurazione predefinita: applica /Usage a schermo e in stampa."""
    refs = []
    for xref, info in pdf.get_ocgs().items():
        if str(info.get("name", "")).startswith("Foliant") and pdf.xref_get_key(xref, "Usage")[0] != "null":
            refs.append(f"{xref} 0 R")
    cat = pdf.pdf_catalog()
    if not refs:
        if pdf.xref_get_key(cat, "OCProperties/D/AS")[0] != "null":
            _set_key(pdf, cat, "OCProperties/D/AS", "null")
        return
    ocgs = " ".join(refs)
    _set_key(pdf, cat, "OCProperties/D/AS",
             f"[<</Event/View/OCGs[{ocgs}]/Category[/View]>><</Event/Print/OCGs[{ocgs}]/Category[/Print]>>]")


def _artifact_props(kind: str, subtype: str | None) -> str:
    if kind == BACKGROUND:
        return "<</Type/Background>>"
    sub = subtype or {WATERMARK: "Watermark", NUMBERS: "Footer", BATES: "Footer", HEADERFOOTER: "Header"}[kind]
    return f"<</Type/Pagination/Subtype/{sub}>>"


# =========================================================================
# Inserimento e identificazione
# =========================================================================

def _new_id() -> str:
    return uuid.uuid4().hex[:10]


def _encode_params(params: dict) -> str:
    def clean(v):
        if isinstance(v, (bytes, bytearray, memoryview)):
            return None
        if isinstance(v, (list, tuple)):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (_dt.date, _dt.datetime)):
            return v.isoformat()
        if isinstance(v, pymupdf.Document):
            return None
        return v
    data = json.dumps(clean(params), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return "<" + data.hex() + ">"


def _decode_params(raw: str) -> dict:
    raw = raw.strip()
    try:
        if raw.startswith("<") and raw.endswith(">"):
            return json.loads(bytes.fromhex(raw[1:-1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
    return {}


def _stamp_page(pdf: pymupdf.Document, pno: int, layer: _Layer, kind: str, sid: str, *, behind: bool, oc: int,
                record: str, subtype: str | None = None) -> None:
    """Inserisce `layer` nella pagina `pno` come timbro riconoscibile."""
    page = pdf[pno]
    before = set(page.get_contents())
    placement.show_pdf_page(page, placement.page_rect(page), layer.doc, 0, keep_proportion=False,
                            overlay=not behind, oc=oc, upright=True, as_displayed=True)
    page = pdf[pno]
    target = old_name = None
    for x in page.get_contents():
        if x in before:
            continue
        m = _FZ_RE.search(pdf.xref_stream(x) or b"")
        if m:
            target, old_name = x, m.group(1).decode()
            break
    if target is None:  # non dovrebbe accadere
        raise PageOpError(_("Impossibile inserire il timbro nella pagina {n}.").format(n=pno + 1))
    new_name = f"{_NAME_PREFIX}{kind}_{sid}"
    props = _artifact_props(kind, subtype)
    pdf.update_stream(target, f"/Artifact {props} BDC q /{new_name} Do Q EMC\n".encode())
    typ, val = pdf.xref_get_key(page.xref, f"Resources/XObject/{old_name}")
    if typ != "null":
        _set_key(pdf, page.xref, f"Resources/XObject/{new_name}", val)
        _set_key(pdf, page.xref, f"Resources/XObject/{old_name}", "null")
        if typ == "xref":
            pdf.xref_set_key(int(val.split()[0]), "FoliantStamp", record)


def _release(pdf: pymupdf.Document, layer: _Layer) -> None:
    """Chiude il livello e libera le tabelle di innesto che il documento tiene per lui."""
    gid = getattr(layer.doc, "_graft_id", None)
    if gid is not None:
        try:
            pdf.Graftmaps.pop(gid, None)
            for key in [k for k in pdf.ShownPages if k[0] == gid]:
                pdf.ShownPages.pop(key, None)
        except Exception:
            pass
    layer.close()


def _apply_layers(pdf: pymupdf.Document, kind: str, targets: Sequence[int], build, *, params: dict,
                  behind: bool = False, opacity: float = 1.0, cache_key=None, visibility: str = "both",
                  subtype=None) -> list[int]:
    """Costruisce con `build(layer, pno)` un livello per pagina e lo applica.

    Con `cache_key(pno)` le pagine con la stessa chiave condividono lo stesso
    livello (un solo XObject nel file). `subtype(pno)` sceglie il sottotipo di artefatto.
    """
    oc = _layer_xref(pdf, kind, visibility)
    sid = _new_id()
    record = f"<</K/{kind}/Id({sid})/P{_encode_params(params)}>>"
    cache: dict = {}
    stamped: list[int] = []
    try:
        for pno in targets:
            page = pdf[pno]
            w, h = _visible_size(page)
            key = (cache_key(pno), round(w, 2), round(h, 2)) if cache_key else None
            layer = cache.get(key) if key is not None else None
            if layer is None:
                layer = _Layer(w, h)
                build(layer, pno)
                if layer.empty:
                    layer.close()
                    continue
                layer.finish(opacity)
                if key is not None:
                    cache[key] = layer
            _stamp_page(pdf, pno, layer, kind, sid, behind=behind, oc=oc, record=record,
                        subtype=subtype(pno) if subtype else None)
            if key is None:
                _release(pdf, layer)
            stamped.append(pno)
    finally:
        for layer in cache.values():
            _release(pdf, layer)
    if not stamped:
        _cleanup_layers(pdf)
    return stamped


# =========================================================================
# Segnaposto (PAG-32)
# =========================================================================

def _check_template(template: str) -> None:
    err = fields.validate_template(template or "")
    if err:
        raise PageOpError(err)


def _roman(n: int) -> str:
    if n <= 0:
        return str(n)
    out = ""
    for value, sym in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                       (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out += sym
            n -= value
    return out


def format_number(n: int, style: str = "D") -> str:
    """Numero nello stile delle etichette PDF: D 1, r i, R I, a a, A A (aa, bb… oltre la z)."""
    if style == "r":
        return _roman(n)
    if style == "R":
        return _roman(n).upper()
    if style in ("a", "A") and n > 0:
        letter = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return letter.upper() if style == "A" else letter
    return str(n)


class _Values:
    """Valori dei segnaposto per pagina, calcolati una volta per documento."""

    def __init__(self, pdf: pymupdf.Document, filename: str = "", date: _dt.date | None = None,
                 overrides: dict | None = None):
        self.pdf = pdf
        self.base = fields.document_values(pdf, filename or None)
        self.overrides = dict(overrides or {})
        if date is None:
            self.now = _dt.datetime.now()
        elif isinstance(date, _dt.datetime):
            self.now = date
        else:
            self.now = _dt.datetime.combine(date, _dt.datetime.now().time())

    def for_page(self, pno: int, number=None, **extra) -> dict:
        vals = fields.page_values(self.pdf, pno, self.base, number)
        vals["n"] = vals["pagina"]  # compatibilità con i modelli precedenti
        vals.update(extra)
        vals.update(self.overrides)
        return vals

    def render(self, template: str, values: dict, date_format: str | None = None) -> str:
        if date_format:
            try:
                fixed = self.now.strftime(date_format)
            except (ValueError, TypeError):
                raise PageOpError(_("Formato della data non valido.")) from None
            fixed = fixed.replace("{", "{{").replace("}", "}}")
            template = template.replace("{date}", fixed).replace("{data}", fixed)
        return fields.render_template(template, values, now=self.now)


def _has_fields(template: str) -> bool:
    return bool(re.search(r"(?<!\{)\{(?!\{)", template or ""))


# =========================================================================
# Avvisi
# =========================================================================

def pdfa_part(pdf: pymupdf.Document) -> str | None:
    """Conformità PDF/A dichiarata nei metadati XMP (es. "1B"), oppure None."""
    try:
        xmp = pdf.get_xml_metadata() or ""
    except Exception:
        return None
    part = re.search(r"pdfaid:part\s*=\s*[\"'](\d)|<pdfaid:part>\s*(\d)", xmp)
    if not part:
        return None
    conf = re.search(r"pdfaid:conformance\s*=\s*[\"'](\w)|<pdfaid:conformance>\s*(\w)", xmp)
    p = part.group(1) or part.group(2)
    c = (conf.group(1) or conf.group(2)) if conf else ""
    return f"{p}{c.upper()}"


def opaque_scan_pages(pdf: pymupdf.Document, pages: Iterable[int] | None = None, coverage: float = 0.9) -> list[int]:
    """Pagine coperte quasi del tutto da un'immagine (scansioni): uno sfondo o una filigrana
    «sotto» non si vedrebbero."""
    result = []
    for pno in (range(pdf.page_count) if pages is None else pages):
        page = pdf[pno]
        area = abs(page.rect.width * page.rect.height) or 1.0
        try:
            infos = page.get_image_info()
        except Exception:
            continue
        for info in infos:
            r = pymupdf.Rect(info["bbox"]) & placement.page_rect(page)
            if abs(r.width * r.height) >= coverage * area:
                result.append(pno)
                break
    return result


# =========================================================================
# API pubblica: filigrane e sfondi
# =========================================================================

def _check_rotation(rotation) -> float:
    try:
        return float(rotation) % 360
    except (TypeError, ValueError):
        raise PageOpError(_("Rotazione non valida.")) from None


def _check_offset(offset) -> tuple[float, float]:
    try:
        dx, dy = (float(v) for v in offset)
    except (TypeError, ValueError):
        raise PageOpError(_("Scostamento non valido.")) from None
    if abs(dx) > 5000 or abs(dy) > 5000:
        raise PageOpError(_("Scostamento non valido."))
    return dx, dy


def _tile_centers(page_w: float, page_h: float, bw: float, bh: float, spacing: float) -> list[pymupdf.Point]:
    step_x, step_y = bw + spacing, bh + spacing
    nx = int(page_w / step_x / 2) + 2
    ny = int(page_h / step_y / 2) + 2
    out = []
    for j in range(-ny, ny + 1):
        shift = step_x / 2 if j % 2 else 0.0  # righe sfalsate
        for i in range(-nx, nx + 1):
            c = pymupdf.Point(page_w / 2 + i * step_x + shift, page_h / 2 + j * step_y)
            if c.x + bw / 2 > 0 and c.x - bw / 2 < page_w and c.y + bh / 2 > 0 and c.y - bh / 2 < page_h:
                out.append(c)
    return out


def add_watermark(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, text: str | None = None,
                  image=None, source_pdf=None, source_page: int = 0, font_size: float = 60,
                  color=(0.5, 0.5, 0.5), opacity: float = 0.3, rotation: float = 45, position: str = "center",
                  offset=(0.0, 0.0), scale: float | None = None, behind: bool = False, font: str = "helv",
                  margin: float = 36, fit: bool = True, tile: bool = False, spacing: float = 72,
                  visibility: str = "both", filename: str = "", date: _dt.date | None = None,
                  field_values: dict | None = None) -> list[int]:
    """Aggiunge una filigrana di testo, immagine o pagina PDF. Restituisce le pagine timbrate.

    `rotation` in gradi, antiorario; `offset` (dx, dy) in punti dalla posizione
    di ancoraggio (y verso il basso); `scale` (immagini e PDF) è la frazione
    della larghezza della pagina (predefinita 0,5); `behind=True` la disegna
    sotto il contenuto; `tile=True` la ripete a mosaico con `spacing` punti tra
    le copie; `visibility` "both", "screen" o "print". Il testo accetta i campi
    dinamici (PAG-32). Con `fit` il testo troppo grande viene ridotto per
    restare dentro la pagina.
    """
    targets = _target_pages(pdf, pages)
    has_text = bool(text and text.strip())
    n_sources = sum(1 for s in (has_text, image is not None, source_pdf is not None) if s)
    if n_sources == 0:
        raise PageOpError(_("Indica il testo o l'immagine della filigrana."))
    if n_sources > 1:
        raise PageOpError(_("Scegli testo oppure immagine, non entrambi."))
    opacity = _check_opacity(opacity)
    pos = _check_position(position)
    margin = _check_margin(margin)
    rotation = _check_rotation(rotation)
    off = _check_offset(offset)
    visibility = _check_visibility(visibility)
    try:
        spacing = max(0.0, float(spacing))
    except (TypeError, ValueError):
        raise PageOpError(_("Spaziatura non valida.")) from None
    params = {"text": text if has_text else None, "image": image if isinstance(image, str) else None,
              "source_pdf": source_pdf if isinstance(source_pdf, str) else None, "source_page": source_page,
              "font_size": font_size, "color": list(color) if color is not None else None, "opacity": opacity,
              "rotation": rotation, "position": pos, "offset": list(off), "scale": scale, "behind": behind,
              "font": font, "margin": margin, "fit": fit, "tile": tile, "spacing": spacing,
              "visibility": visibility}
    src = None
    if has_text:
        _check_template(text)
        size = _check_font_size(font_size)
        rgb = _check_color(color)
        fnt = _Font(font)
        values = _Values(pdf, filename, date, field_values)

        def build(layer: _Layer, pno: int) -> None:
            txt = values.render(text, values.for_page(pno))
            if tile:
                bw, bh = layer.text_box_size(txt, fnt, size, rotation)
                for c in _tile_centers(layer.width, layer.height, bw, bh, spacing):
                    layer.text_block(txt, pos="center", margin=0, font=fnt, size=size, color=rgb,
                                     rotation=rotation, align="center", center=c)
            else:
                layer.text_block(txt, pos=pos, margin=margin, font=fnt, size=size, color=rgb, rotation=rotation,
                                 align="center", fit=fit, offset=off)

        cache = (lambda pno: 0) if not _has_fields(text) else None
    else:
        if scale is None:
            scale = 0.5
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = -1
        if not (0.01 <= scale <= 5.0):
            raise PageOpError(_("La scala dell'immagine deve essere tra 1% e 500% della larghezza della pagina."))
        src = _Source(image=image, pdf=source_pdf, page=source_page)

        def build(layer: _Layer, pno: int) -> None:
            w = layer.width * scale
            h = w * src.aspect
            bw, bh = _rotated_box(w, h, rotation)
            if tile:
                centers = _tile_centers(layer.width, layer.height, bw, bh, spacing)
            else:
                centers = [_anchor_point(pos, layer.width, layer.height, bw, bh, margin, off)]
            for c in centers:
                src.draw(layer, c, w, h, rotation)

        cache = lambda pno: 0  # noqa: E731
    try:
        return _apply_layers(pdf, WATERMARK, targets, build, params=params, behind=behind, opacity=opacity,
                             cache_key=cache, visibility=visibility)
    finally:
        if src is not None:
            src.close()


def add_background(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, color=None, image=None,
                   source_pdf=None, source_page: int = 0, first_rest: bool = False, opacity: float = 1.0,
                   scale: str = "fill", percent: float = 100, position: str = "center", rotation: float = 0,
                   visibility: str = "both") -> list[int]:
    """Aggiunge uno sfondo sotto il contenuto: colore pieno, immagine o pagina di un PDF
    (carta intestata).

    `scale`: "fit" (adatta, proporzioni mantenute), "fill" (riempie la pagina;
    per le immagini distorce, come uno sfondo), "percent" (`percent` % della
    dimensione reale), "actual" (dimensione reale). Con `first_rest` la pagina
    1 del PDF sorgente va sulla prima pagina indicata e la pagina 2 sulle altre.
    """
    targets = _target_pages(pdf, pages)
    if color is None and image is None and source_pdf is None:
        raise PageOpError(_("Indica il colore o l'immagine dello sfondo."))
    if image is not None and source_pdf is not None:
        raise PageOpError(_("Scegli un'immagine oppure un PDF, non entrambi."))
    rgb = _check_color(color) if color is not None else None
    opacity = _check_opacity(opacity)
    pos = _check_position(position)
    rotation = _check_rotation(rotation)
    visibility = _check_visibility(visibility)
    if scale not in SCALES:
        raise PageOpError(_("Scala «{s}» non valida.").format(s=scale))
    try:
        percent = float(percent)
    except (TypeError, ValueError):
        percent = -1
    if not (1 <= percent <= 1000):
        raise PageOpError(_("La percentuale deve essere tra 1 e 1000."))
    src = _Source(image=image, pdf=source_pdf, page=source_page) if (image is not None or source_pdf is not None) \
        else None
    params = {"color": list(rgb) if rgb else None, "image": image if isinstance(image, str) else None,
              "source_pdf": source_pdf if isinstance(source_pdf, str) else None, "source_page": source_page,
              "first_rest": first_rest, "opacity": opacity, "scale": scale, "percent": percent, "position": pos,
              "rotation": rotation, "visibility": visibility}

    def src_page_for(pno: int) -> int:
        if src is None or src.doc is None:
            return 0
        if first_rest:
            return 0 if pno == targets[0] or src.page_count() < 2 else 1
        return src.page

    def build(layer: _Layer, pno: int) -> None:
        if rgb is not None:
            layer.fill(rgb)
        if src is None:
            return
        sp = src_page_for(pno)
        nat_w, nat_h = src.size_of(sp) if src.doc is not None else src.natural
        if rotation % 180 == 90:
            box_w, box_h = nat_h, nat_w
        else:
            box_w, box_h = _rotated_box(nat_w, nat_h, rotation)
        if scale == "fill" and src.doc is None and rotation % 90 == 0:
            w, h = (layer.width, layer.height) if rotation % 180 == 0 else (layer.height, layer.width)
            src.draw(layer, pymupdf.Point(layer.width / 2, layer.height / 2), w, h, rotation)
            return
        if scale == "fit":
            f = min(layer.width / box_w, layer.height / box_h)
        elif scale == "fill":
            f = max(layer.width / box_w, layer.height / box_h)
        elif scale == "percent":
            f = percent / 100.0
        else:
            f = 1.0
        w, h = nat_w * f, nat_h * f
        bw, bh = _rotated_box(w, h, rotation)
        center = _anchor_point(pos, layer.width, layer.height, bw, bh, 0)
        # Il livello è grande quanto la pagina: ciò che esce viene ritagliato.
        src.draw(layer, center, w, h, rotation, pno=sp)

    try:
        return _apply_layers(pdf, BACKGROUND, targets, build, params=params, behind=True, opacity=opacity,
                             cache_key=src_page_for, visibility=visibility)
    finally:
        if src is not None:
            src.close()


# =========================================================================
# API pubblica: numerazioni, intestazioni
# =========================================================================

def _text_stamp_common(font_size, color, margin, position, font):
    return (_check_font_size(font_size), _check_color(color), _check_margin(margin), _check_position(position),
            _Font(font))


def _remove_kind_on(pdf: pymupdf.Document, pages: Sequence[int], kind: str) -> None:
    if any(kind in kinds for p, kinds in list_watermarks(pdf).items() if p in set(pages)):
        remove_watermarks(pdf, pages, kind)


def add_page_numbers(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, fmt: str = "{pagina}",
                     start: int = 1, position: str = "bottom-center", margin: float = 36, font_size: float = 10,
                     color=(0, 0, 0), font: str = "helv", skip_first: bool = False, opacity: float = 1.0,
                     style: str = "D", start_from: int | None = None, exclude: Iterable[int] = (),
                     mirror: str = "none", use_label: bool = False, replace: bool = False,
                     filename: str = "", visibility: str = "both",
                  field_values: dict | None = None) -> list[int]:
    """Numera le pagine (PAG-33). Il formato accetta i campi PAG-32: {pagina},
    {totale} (= ultimo numero assegnato), {etichetta}…; {n}, {total} e {label}
    restano validi come alias.

    Si numerano le pagine indicate a partire da `start_from` (indice), tolte
    quelle in `exclude`; la prima numerata riceve `start`. Con `skip_first` la
    prima resta senza numero ma viene contata. `style` D/r/R/a/A; `mirror`
    "outer"/"inner" mette il numero sul lato esterno/interno nel fronte/retro;
    `use_label` stampa l'etichetta di pagina al posto del numero; `replace`
    toglie prima i numeri di Foliant presenti sulle stesse pagine.
    """
    targets = _target_pages(pdf, pages)
    if not fmt or not fmt.strip():
        raise PageOpError(_("Il formato del numero non può essere vuoto."))
    _check_template(fmt)
    try:
        start = int(start)
    except (TypeError, ValueError):
        raise PageOpError(_("Il numero iniziale non è valido.")) from None
    if start < 0:
        raise PageOpError(_("Il numero iniziale non può essere negativo."))
    if style not in NUMBER_STYLES:
        raise PageOpError(_("Stile di numerazione «{s}» non valido.").format(s=style))
    size, rgb, margin, pos, fnt = _text_stamp_common(font_size, color, margin, position, font)
    opacity = _check_opacity(opacity)
    visibility = _check_visibility(visibility)
    excluded = {int(p) for p in exclude or ()}
    targets = [p for p in targets if p not in excluded and (start_from is None or p >= int(start_from))]
    if not targets:
        raise PageOpError(_("Non ci sono pagine da numerare."))
    numbers = {pno: start + k for k, pno in enumerate(targets)}
    total = start + len(targets) - 1
    if skip_first:
        targets = targets[1:]
        if not targets:
            raise PageOpError(_("Non ci sono pagine da numerare dopo aver saltato la prima."))
    params = {"fmt": fmt, "start": start, "position": pos, "margin": margin, "font_size": size,
              "color": list(rgb), "font": font, "skip_first": skip_first, "opacity": opacity, "style": style,
              "start_from": start_from, "exclude": sorted(excluded), "mirror": mirror, "use_label": use_label,
              "visibility": visibility}
    if replace:
        _remove_kind_on(pdf, targets, NUMBERS)
    values = _Values(pdf, filename, None, field_values)

    def page_pos(pno: int) -> str:
        return _mirror_position(pos, pno, mirror)

    def build(layer: _Layer, pno: int) -> None:
        vals = values.for_page(pno)
        label = vals["etichetta"]
        number = label if use_label else format_number(numbers[pno], style)
        vals.update(pagina=number, n=number, totale=format_number(total, style))
        vals.update({k: v for k, v in (field_values or {}).items() if k not in ("pagina", "n")})
        layer.text_block(values.render(fmt, vals), pos=page_pos(pno), margin=margin, font=fnt, size=size,
                         color=rgb)

    return _apply_layers(pdf, NUMBERS, targets, build, params=params, opacity=opacity, visibility=visibility,
                         subtype=lambda pno: "Header" if page_pos(pno).startswith("top") else "Footer")


def format_bates(number: int, *, prefix: str = "", suffix: str = "", digits: int = 6) -> str:
    return f"{prefix}{number:0{digits}d}{suffix}"


def add_bates(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, prefix: str = "", suffix: str = "",
              start: int = 1, digits: int = 6, position: str = "bottom-right", margin: float = 36,
              font_size: float = 10, color=(0, 0, 0), font: str = "helv", replace: bool = False) -> str:
    """Numerazione Bates (prefisso + numero a cifre fisse + suffisso). Restituisce l'ultimo codice."""
    targets = _target_pages(pdf, pages)
    try:
        start, digits = int(start), int(digits)
    except (TypeError, ValueError):
        raise PageOpError(_("Numero iniziale o cifre non validi.")) from None
    if start < 0:
        raise PageOpError(_("Il numero iniziale non può essere negativo."))
    if not (1 <= digits <= 15):
        raise PageOpError(_("Le cifre devono essere tra 1 e 15."))
    if len(str(start + len(targets) - 1)) > digits:
        raise PageOpError(_("Le cifre indicate non bastano per numerare tutte le pagine."))
    if any(c in (prefix or "") + (suffix or "") for c in "\n\r"):
        raise PageOpError(_("Prefisso e suffisso non possono andare a capo."))
    size, rgb, margin, pos, fnt = _text_stamp_common(font_size, color, margin, position, font)
    codes = {pno: format_bates(start + k, prefix=prefix or "", suffix=suffix or "", digits=digits)
             for k, pno in enumerate(targets)}
    params = {"prefix": prefix, "suffix": suffix, "start": start, "digits": digits, "position": pos,
              "margin": margin, "font_size": size, "color": list(rgb), "font": font}
    if replace:
        _remove_kind_on(pdf, targets, BATES)

    def build(layer: _Layer, pno: int) -> None:
        layer.text_block(codes[pno], pos=pos, margin=margin, font=fnt, size=size, color=rgb)

    _apply_layers(pdf, BATES, targets, build, params=params,
                  subtype=lambda pno: "Header" if pos.startswith("top") else "Footer")
    return codes[targets[-1]]


def _slots_of(source: dict | None) -> dict[str, str]:
    source = source or {}
    return {k: str(source.get(k) or "") for k in SLOTS}


def add_header_footer(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, header_left: str = "",
                      header_center: str = "", header_right: str = "", footer_left: str = "",
                      footer_center: str = "", footer_right: str = "", margin: float = 36, font_size: float = 10,
                      color=(0, 0, 0), font: str = "helv", date_format: str | None = None,
                      filename: str = "", date: _dt.date | None = None, even: dict | None = None,
                      first: dict | None = None, opacity: float = 1.0, underline: bool = False,
                      logo=None, logo_position: str = "top-left", logo_height: float = 28,
                      replace: bool = False, visibility: str = "both",
                  field_values: dict | None = None) -> list[int]:
    """Intestazione e piè di pagina in sei aree, testo su più righe (PAG-34).

    Campi PAG-32 ({pagina}, {totale} = pagine del documento, {data:…}, {nome},
    {titolo}…; alias {n}, {total}, {date}, {file}). `even` e `first` sono
    dizionari con le stesse sei chiavi (``header_left``…) per le pagine pari e
    per la prima pagina indicata (aree vuote = niente). `logo` (percorso o
    bytes) è disegnato nell'area `logo_position` alto `logo_height` punti.
    `replace` toglie prima intestazioni e piè di Foliant dalle stesse pagine.
    """
    targets = _target_pages(pdf, pages)
    default = _slots_of({"header_left": header_left, "header_center": header_center,
                         "header_right": header_right, "footer_left": footer_left,
                         "footer_center": footer_center, "footer_right": footer_right})
    variants = {"default": default, "even": _slots_of(even) if even is not None else None,
                "first": _slots_of(first) if first is not None else None}
    any_text = any(v and any(t.strip() for t in v.values()) for v in variants.values())
    if not any_text and logo is None:
        raise PageOpError(_("Scrivi almeno un testo di intestazione o piè di pagina."))
    for v in variants.values():
        for t in (v or {}).values():
            _check_template(t)
    size, rgb, margin, _pos, fnt = _text_stamp_common(font_size, color, margin, "center", font)
    opacity = _check_opacity(opacity)
    visibility = _check_visibility(visibility)
    logo_pos = _check_position(logo_position)
    if date_format:
        try:
            _dt.date.today().strftime(date_format)
        except (ValueError, TypeError):
            raise PageOpError(_("Formato della data non valido.")) from None
    try:
        logo_height = float(logo_height)
    except (TypeError, ValueError):
        logo_height = -1
    if logo is not None and not (2 <= logo_height <= 500):
        raise PageOpError(_("L'altezza del logo deve essere tra 2 e 500 punti."))
    params = {"variants": variants, "margin": margin, "font_size": size, "color": list(rgb), "font": font,
              "date_format": date_format, "opacity": opacity, "underline": underline,
              "logo": logo if isinstance(logo, str) else None, "logo_position": logo_pos,
              "logo_height": logo_height, "visibility": visibility}
    values = _Values(pdf, filename, date, field_values)
    src = _Source(image=logo) if logo is not None else None
    if replace:
        _remove_kind_on(pdf, targets, HEADERFOOTER)

    def variant_for(pno: int) -> dict:
        if variants["first"] is not None and pno == targets[0]:
            return variants["first"]
        if variants["even"] is not None and (pno + 1) % 2 == 0:
            return variants["even"]
        return default

    def build(layer: _Layer, pno: int) -> None:
        vals = values.for_page(pno)
        area_w = (layer.width - 2 * margin) / 3 * 1.9  # testo laterale può invadere metà dell'area centrale
        for slot, tmpl in variant_for(pno).items():
            if tmpl.strip():
                pos = _SLOT_POS[slot]
                layer.text_block(values.render(tmpl, vals, date_format), pos=pos, margin=margin, font=fnt,
                                 size=size, color=rgb, underline=underline,
                                 max_width=area_w if not pos.endswith("center") else layer.width - 2 * margin)
        if src is not None and (variants["first"] is None or pno != targets[0]
                                or any(t.strip() for t in variants["first"].values())):
            h = logo_height
            w = h / src.aspect
            src.draw(layer, _anchor_point(logo_pos, layer.width, layer.height, w, h, margin), w, h, 0)

    has_header = any(t.strip() for v in variants.values() if v for k, t in v.items() if k.startswith("header"))
    try:
        return _apply_layers(pdf, HEADERFOOTER, targets, build, params=params, opacity=opacity,
                             visibility=visibility, subtype=lambda pno: "Header" if has_header else "Footer")
    finally:
        if src is not None:
            src.close()


# =========================================================================
# Elenco, parametri e rimozione
# =========================================================================

def _page_stamps(pdf: pymupdf.Document, pno: int) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    page = pdf[pno]
    for x in page.get_contents():
        for m in _NAME_RE.finditer(pdf.xref_stream(x) or b""):
            kind = m.group(1).decode()
            if kind in KINDS:
                found.append((kind, m.group(2).decode()))
    return found


def list_watermarks(pdf: pymupdf.Document) -> dict[int, list[str]]:
    """Timbri presenti, per pagina: {indice: [tipo, …]} (solo pagine con timbri)."""
    result: dict[int, list[str]] = {}
    for pno in range(pdf.page_count):
        kinds = [k for k, _sid in _page_stamps(pdf, pno)]
        if kinds:
            result[pno] = kinds
    return result


def list_stamps(pdf: pymupdf.Document, kind: str | None = None) -> list[dict]:
    """Timbri di Foliant uno per uno: [{id, kind, pages, params}] nell'ordine di aggiunta."""
    stamps: dict[str, dict] = {}
    for pno in range(pdf.page_count):
        for k, sid in _page_stamps(pdf, pno):
            if kind is not None and k != kind:
                continue
            entry = stamps.get(sid)
            if entry is None:
                params = {}
                typ, val = pdf.xref_get_key(pdf[pno].xref, f"Resources/XObject/{_NAME_PREFIX}{k}_{sid}")
                if typ == "xref":
                    ptyp, pval = pdf.xref_get_key(int(val.split()[0]), "FoliantStamp/P")
                    if ptyp in ("string", "hexstring") or pval.startswith("<"):
                        params = _decode_params(pval if pval.startswith("<") else "<" + pval.encode().hex() + ">")
                entry = stamps[sid] = {"id": sid, "kind": k, "pages": [], "params": params}
            if pno not in entry["pages"]:
                entry["pages"].append(pno)
    return list(stamps.values())


def stamp_params(pdf: pymupdf.Document, kind: str) -> dict | None:
    """Parametri dell'ultimo timbro del tipo indicato (per «Modifica»), oppure None."""
    stamps = list_stamps(pdf, kind)
    return dict(stamps[-1]["params"]) if stamps else None


def kwargs_from_params(kind: str, params: dict | None) -> dict:
    """Argomenti per la funzione `add_*` del tipo a partire dai parametri salvati
    (per «Modifica» e per i modelli): l'inverso di quanto registrato nel timbro."""
    p = dict(params or {})
    if kind == HEADERFOOTER:
        variants = p.pop("variants", None) or {}
        default = variants.get("default") or {}
        p.update({k: default.get(k, "") for k in SLOTS})
        p["even"] = variants.get("even")
        p["first"] = variants.get("first")
    for key in ("color",):
        if isinstance(p.get(key), list):
            p[key] = tuple(p[key])
    if isinstance(p.get("offset"), list):
        p["offset"] = tuple(p["offset"])
    return {k: v for k, v in p.items() if v is not None or k in ("even", "first")}


def _strip_block_re(kinds: Sequence[str], sids: Sequence[str] | None) -> tuple[re.Pattern, re.Pattern]:
    alt = "|".join(re.escape(k) for k in kinds).encode()
    sid = ("|".join(re.escape(s) for s in sids) if sids else r"[A-Za-z0-9]+").encode()
    name = rb"/FolStamp_(?:" + alt + rb")_(?:" + sid + rb")\s+Do"
    block = re.compile(
        rb"/Artifact\s*<<(?:[^<>]|<<[^<>]*>>)*>>\s*BDC\s*q\s+(?:[^Qq]*?\s)?" + name + rb"\s+Q\s*EMC"
        rb"|q\s+(?:[^Qq]*?\s)?" + name + rb"\s+Q")
    names = re.compile(rb"/(FolStamp_(?:" + alt + rb")_(?:" + sid + rb"))\s+Do")
    return block, names


def remove_watermarks(pdf: pymupdf.Document, pages: Iterable[int] | None = None,
                      kind: str | Sequence[str] | None = WATERMARK, *, stamp_id: str | Sequence[str] | None = None
                      ) -> int:
    """Rimuove i timbri del tipo indicato (`None` o "all" = tutti) o con gli `stamp_id`
    indicati. Restituisce quante occorrenze (timbro × pagina) ha tolto."""
    targets = _target_pages(pdf, pages)
    if kind is None or kind == "all":
        kinds = list(KINDS)
    elif isinstance(kind, str):
        kinds = [kind]
    else:
        kinds = list(kind)
    for k in kinds:
        if k not in KINDS:
            raise PageOpError(_("Tipo di timbro «{k}» sconosciuto.").format(k=k))
    sids = [stamp_id] if isinstance(stamp_id, str) else (list(stamp_id) if stamp_id else None)
    if sids is not None:
        for s in sids:
            if not re.fullmatch(r"[A-Za-z0-9]+", s):
                raise PageOpError(_("Identificativo di timbro non valido."))
    block_re, name_re = _strip_block_re(kinds, sids)
    removed = 0
    for pno in targets:
        page = pdf[pno]
        keep: list[int] = []
        names: set[str] = set()
        changed = False
        for x in page.get_contents():
            data = pdf.xref_stream(x) or b""
            found = name_re.findall(data)
            if not found:
                keep.append(x)
                continue
            names.update(n.decode() for n in found)
            removed += len(found)
            changed = True
            stripped = block_re.sub(b"", data)
            stripped = name_re.sub(b"", stripped)  # blocchi non riconosciuti: togli almeno il disegno
            if stripped.strip():
                pdf.update_stream(x, stripped)
                keep.append(x)
        if not changed:
            continue
        if len(keep) == 1:
            pdf.xref_set_key(page.xref, "Contents", f"{keep[0]} 0 R")
        else:
            pdf.xref_set_key(page.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in keep) + "]")
        for name in names:
            if pdf.xref_get_key(page.xref, f"Resources/XObject/{name}")[0] != "null":
                _set_key(pdf, page.xref, f"Resources/XObject/{name}", "null")
    _cleanup_layers(pdf)
    return removed


def remove_stamp(pdf: pymupdf.Document, stamp_id: str | Sequence[str], pages: Iterable[int] | None = None) -> int:
    """Rimuove un timbro (o più) per identificativo, come elencati da `list_stamps`."""
    return remove_watermarks(pdf, pages, None, stamp_id=stamp_id)


def _cleanup_layers(pdf: pymupdf.Document) -> None:
    """Toglie i livelli «Foliant: …» rimasti senza timbri."""
    try:
        ocgs = pdf.get_ocgs()
    except Exception:
        return
    if not ocgs:
        return
    present = {k for kinds in list_watermarks(pdf).values() for k in kinds}

    def kind_of(name: str) -> str | None:
        for kind, names in _LAYER_NAMES_ALL.items():
            for base in names:
                if name == base or any(name == base + s for s in _VIS_SUFFIX.values()):
                    return kind
        return None

    unused = [xref for xref, info in ocgs.items()
              if (k := kind_of(str(info.get("name", "")))) is not None and k not in present]
    if not unused:
        return
    cat = pdf.pdf_catalog()
    for xref in unused:
        ref = re.compile(rf"(?<!\d){xref}\s+0\s+R")
        for key in ("OCProperties/OCGs", "OCProperties/D/ON", "OCProperties/D/OFF", "OCProperties/D/Order"):
            typ, val = pdf.xref_get_key(cat, key)
            if typ == "array":
                _set_key(pdf, cat, key, ref.sub("", val))
    typ, val = pdf.xref_get_key(cat, "OCProperties/OCGs")
    if typ == "array" and not re.search(r"\d+\s+0\s+R", val):
        _set_key(pdf, cat, "OCProperties", "null")
    else:
        _update_usage_events(pdf)


def update_watermark(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *,
                     stamp_id: str | None = None, **opts) -> list[int]:
    """Sostituisce le filigrane (tutte, o quella `stamp_id`) delle pagine con una nuova."""
    targets = _target_pages(pdf, pages)
    if stamp_id:
        remove_watermarks(pdf, targets, WATERMARK, stamp_id=stamp_id)
    else:
        remove_watermarks(pdf, targets, WATERMARK)
    return add_watermark(pdf, targets, **opts)


# =========================================================================
# Etichette di pagina
# =========================================================================

def get_page_labels(pdf: pymupdf.Document) -> list[dict]:
    """Regole di etichettatura: [{startpage, style, prefix, firstpagenum}, …]."""
    try:
        rules = pdf.get_page_labels() or []
    except Exception:
        return []
    result = []
    for r in rules:
        result.append({
            "startpage": int(r.get("startpage", 0)),
            "style": r.get("style", "") or "",
            "prefix": r.get("prefix", "") or "",
            "firstpagenum": int(r.get("firstpagenum", 1) or 1),
        })
    return sorted(result, key=lambda r: r["startpage"])


def set_page_labels(pdf: pymupdf.Document, rules: Sequence[dict]) -> None:
    """Imposta le etichette di pagina (sostituisce quelle esistenti)."""
    if not pdf.is_pdf:
        raise PageOpError(_("Il documento non è un PDF."))
    n = pdf.page_count
    clean: list[dict] = []
    starts: set[int] = set()
    for r in rules or []:
        try:
            start = int(r.get("startpage", 0))
            first = int(r.get("firstpagenum", 1) if r.get("firstpagenum", 1) is not None else 1)
        except (TypeError, ValueError, AttributeError):
            raise PageOpError(_("Regola di etichetta non valida.")) from None
        style = r.get("style", "D") or ""
        prefix = r.get("prefix", "") or ""
        if not (0 <= start < n):
            raise PageOpError(_("La pagina iniziale {n} non esiste.").format(n=start + 1))
        if start in starts:
            raise PageOpError(_("Ci sono due regole che iniziano dalla pagina {n}.").format(n=start + 1))
        if style not in LABEL_STYLES:
            raise PageOpError(_("Stile di numerazione «{s}» non valido.").format(s=style))
        if first < 1:
            raise PageOpError(_("Il primo numero deve essere almeno 1."))
        if not style and not prefix:
            raise PageOpError(_("Una regola senza numeri deve avere un prefisso."))
        if any(c in prefix for c in "()\\\r\n"):
            raise PageOpError(_("Il prefisso non può contenere parentesi, barre rovesciate o a capo."))
        starts.add(start)
        clean.append({"startpage": start, "style": style, "prefix": prefix, "firstpagenum": first})
    if clean and 0 not in starts:
        # Il PDF richiede una regola per la prima pagina: numerazione normale.
        clean.append({"startpage": 0, "style": "D", "prefix": "", "firstpagenum": 1})
    clean.sort(key=lambda r: r["startpage"])
    if not clean:
        remove_page_labels(pdf)
        return
    try:
        pdf.set_page_labels(clean)
    except Exception as e:
        raise PageOpError(_("Impossibile impostare le etichette: {e}").format(e=e)) from None


def remove_page_labels(pdf: pymupdf.Document) -> None:
    """Elimina tutte le etichette di pagina (si torna a 1, 2, 3…)."""
    try:
        pdf.set_page_labels([])
    except Exception:
        cat = pdf.pdf_catalog()
        pdf.xref_set_key(cat, "PageLabels", "null")


def preview_labels(page_count: int, rules: Sequence[dict], limit: int = 12) -> list[str]:
    """Anteprima delle prime etichette risultanti (senza toccare un documento)."""
    tmp = pymupdf.open()
    try:
        for _i in range(max(1, min(page_count, limit))):
            tmp.new_page(width=10, height=10)
        real = [dict(r) for r in rules if int(r.get("startpage", 0)) < tmp.page_count]
        set_page_labels(tmp, real)
        return [tmp[i].get_label() or str(i + 1) for i in range(tmp.page_count)]
    finally:
        tmp.close()



TRANSLATIONS = {
    "Spaziatura non valida.": "Invalid spacing.",
    "Visibilità «{v}» non valida.": "Invalid visibility “{v}”.",
    "Scostamento non valido.": "Invalid offset.",
    "Scala «{s}» non valida.": "Invalid scale “{s}”.",
    "L'altezza del logo deve essere tra 2 e 500 punti.": "The logo height must be between 2 and 500 points.",
    "Il PDF sorgente non è leggibile.": "The source PDF cannot be read.",
    "La percentuale deve essere tra 1 e 1000.": "The percentage must be between 1 and 1000.",
    "Scegli un'immagine oppure un PDF, non entrambi.": "Choose an image or a PDF, not both.",
    "Il PDF sorgente non ha la pagina {n}.": "The source PDF has no page {n}.",
    "Il PDF sorgente è protetto da password.": "The source PDF is password protected.",
    "Non ci sono pagine da numerare.": "There are no pages to number.",
    "Identificativo di timbro non valido.": "Invalid stamp identifier.",
    "Stile di numerazione non valido.": "Invalid numbering style.",
    "Foliant: filigrana": "Foliant: watermark",
    "Foliant: sfondo": "Foliant: background",
    "Foliant: numeri di pagina": "Foliant: page numbers",
    "Foliant: numerazione Bates": "Foliant: Bates numbering",
    "Foliant: intestazione e piè di pagina": "Foliant: header and footer",
    "Il documento non è un PDF.": "The document is not a PDF.",
    "Il documento non ha pagine.": "The document has no pages.",
    "Numero di pagina non valido.": "Invalid page number.",
    "La pagina {n} non esiste (il documento ha {tot} pagine).": "Page {n} does not exist (the document has {tot} pages).",
    "Indica almeno una pagina.": "Enter at least one page.",
    "Colore non valido: servono tre valori tra 0 e 1.": "Invalid color: three values between 0 and 1 are required.",
    "L'opacità deve essere compresa tra 1% e 100%.": "Opacity must be between 1% and 100%.",
    "La dimensione del carattere deve essere tra 1 e 1000 punti.": "Font size must be between 1 and 1000 points.",
    "Il margine deve essere tra 0 e 2000 punti.": "Margin must be between 0 and 2000 points.",
    "Posizione «{p}» non valida.": "Invalid position “{p}”.",
    "File del carattere «{f}» non trovato.": "Font file “{f}” not found.",
    "Carattere «{f}» non disponibile.": "Font “{f}” is not available.",
    "Impossibile caricare il carattere «{f}».": "Cannot load font “{f}”.",
    "Nessuna immagine indicata.": "No image specified.",
    "immagine": "image",
    "Il file «{f}» non esiste.": "The file “{f}” does not exist.",
    "Impossibile leggere «{f}»: {e}.": "Cannot read “{f}”: {e}.",
    "L'immagine «{f}» è vuota.": "The image “{f}” is empty.",
    "«{f}» non è un'immagine leggibile.": "“{f}” is not a readable image.",
    "Impossibile inserire il timbro nella pagina {n}.": "Cannot insert the stamp on page {n}.",
    "Indica il testo o l'immagine della filigrana.": "Specify the watermark text or image.",
    "Scegli testo oppure immagine, non entrambi.": "Choose text or image, not both.",
    "Rotazione non valida.": "Invalid rotation.",
    "La scala dell'immagine deve essere tra 1% e 500% della larghezza della pagina.":
        "Image scale must be between 1% and 500% of the page width.",
    "Indica il colore o l'immagine dello sfondo.": "Specify the background color or image.",
    "Il formato del numero non può essere vuoto.": "The number format cannot be empty.",
    "Il numero iniziale non è valido.": "The start number is not valid.",
    "Il numero iniziale non può essere negativo.": "The start number cannot be negative.",
    "Non ci sono pagine da numerare dopo aver saltato la prima.": "There are no pages to number after skipping the first.",
    "Numero iniziale o cifre non validi.": "Invalid start number or digits.",
    "Le cifre devono essere tra 1 e 15.": "Digits must be between 1 and 15.",
    "Le cifre indicate non bastano per numerare tutte le pagine.": "The given digits are not enough to number all pages.",
    "Prefisso e suffisso non possono andare a capo.": "Prefix and suffix cannot contain line breaks.",
    "Scrivi almeno un testo di intestazione o piè di pagina.": "Enter at least one header or footer text.",
    "Formato della data non valido.": "Invalid date format.",
    "Tipo di timbro «{k}» sconosciuto.": "Unknown stamp type “{k}”.",
    "Regola di etichetta non valida.": "Invalid label rule.",
    "La pagina iniziale {n} non esiste.": "Start page {n} does not exist.",
    "Ci sono due regole che iniziano dalla pagina {n}.": "Two rules start at page {n}.",
    "Stile di numerazione «{s}» non valido.": "Invalid numbering style “{s}”.",
    "Il primo numero deve essere almeno 1.": "The first number must be at least 1.",
    "Una regola senza numeri deve avere un prefisso.": "A rule without numbers must have a prefix.",
    "Il prefisso non può contenere parentesi, barre rovesciate o a capo.":
        "The prefix cannot contain parentheses, backslashes or line breaks.",
    "Impossibile impostare le etichette: {e}": "Cannot set the labels: {e}",
}
