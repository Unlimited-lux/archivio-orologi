"""Filigrane, sfondi, numeri di pagina, Bates, intestazioni/piè di pagina ed
etichette di pagina (senza interfaccia).

Come per `engine.py`, le funzioni ricevono un `pymupdf.Document` e lo
modificano sul posto: chiamarle dentro `with document.change(...) as pdf:`.

Come si ritrovano i timbri
--------------------------
Ogni timbro è disegnato su una pagina temporanea grande quanto la pagina
*visualizzata* (cioè già ruotata), in verticale, e poi inserito nella pagina
di destinazione con `show_pdf_page` come Form XObject, compensando rotazione
e origine del CropBox. L'XObject riceve un nome riconoscibile
``/FolStamp_<tipo>_<n>`` e il suo flusso di contenuto è separato da quello
originale: per rimuoverlo basta togliere quel flusso (o il blocco
``q … /FolStamp_… Do Q`` se il contenuto è stato unito in seguito) e la voce
nelle risorse. La visibilità dipende da un gruppo di contenuto facoltativo
(livello) «Foliant: …», così il timbro si può anche nascondere nei lettori.
Il testo dei timbri resta estraibile e ricercabile.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import re
from collections.abc import Iterable, Sequence

import pymupdf

from foliant.api import _

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

# Font standard del PDF (non richiedono incorporamento).
FONTS = {
    "helv": "Helvetica", "hebo": "Helvetica grassetto", "heit": "Helvetica corsivo",
    "tiro": "Times", "tibo": "Times grassetto", "tiit": "Times corsivo",
    "cour": "Courier", "cobo": "Courier grassetto",
}

LABEL_STYLES = ("D", "r", "R", "a", "A", "")

_NAME_PREFIX = "FolStamp_"
_NAME_RE = re.compile(rb"/FolStamp_([a-z]+)_(\d+)\s+Do")
_FZ_RE = re.compile(rb"/(fzFrm\d+)\s+Do")
_PLACEHOLDER_RE = re.compile(r"\{(n|total|label|date|file)\}")


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
# Geometria
# =========================================================================

def _pdf_cropbox(page: pymupdf.Page) -> pymupdf.Rect:
    """CropBox nello spazio PDF (y verso l'alto)."""
    mb = page.mediabox
    cb = page.cropbox
    return pymupdf.Rect(cb.x0, mb.y1 - cb.y1, cb.x1, mb.y1 - cb.y0)


def _visible_size(page: pymupdf.Page) -> tuple[float, float]:
    r = page.rect  # già ruotato
    return r.width, r.height


def _anchor_point(pos: str, page_w: float, page_h: float, box_w: float, box_h: float,
                  margin: float) -> pymupdf.Point:
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
    return pymupdf.Point(cx, cy)


def _rotated_box(w: float, h: float, degrees: float) -> tuple[float, float]:
    a = math.radians(degrees)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


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

    def text_block(self, text: str, *, pos: str, margin: float, font: _Font, size: float,
                   color, rotation: float = 0.0, align: str | None = None, fit: bool = False) -> None:
        lines = text.split("\n")
        if not any(line.strip() for line in lines):
            return
        def measure(sz: float):
            ws = [font.width(line, sz) for line in lines]
            bw_ = max(ws) if ws else 0.0
            bh_ = (font.ascender - font.descender) * sz + sz * 1.2 * (len(lines) - 1)
            return ws, bw_, bh_

        widths, block_w, block_h = measure(size)
        bw, bh = _rotated_box(block_w, block_h, rotation)
        if fit and bw > 0 and bh > 0:
            avail_w, avail_h = self.width - 2 * margin, self.height - 2 * margin
            factor = min(1.0, avail_w / bw if avail_w > 0 else 1.0, avail_h / bh if avail_h > 0 else 1.0)
            if factor < 1.0:
                size = max(1.0, size * factor)
                widths, block_w, block_h = measure(size)
                bw, bh = _rotated_box(block_w, block_h, rotation)
        line_h = size * 1.2
        asc = font.ascender * size
        center = _anchor_point(pos, self.width, self.height, bw, bh, margin)
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
        self.empty = False

    def image(self, data: bytes, px_w: int, px_h: int, *, pos: str, margin: float, width: float,
              rotation: float = 0.0) -> None:
        width = max(1.0, width)
        height = width * px_h / px_w
        bw, bh = _rotated_box(width, height, rotation)
        center = _anchor_point(pos, self.width, self.height, bw, bh, margin)
        if rotation % 90 == 0:
            quarter = int(rotation) % 360
            w, h = (width, height) if quarter in (0, 180) else (height, width)
            rect = pymupdf.Rect(center.x - w / 2, center.y - h / 2, center.x + w / 2, center.y + h / 2)
            # insert_image ruota in senso antiorario come la filigrana di testo.
            self.page.insert_image(rect, stream=data, rotate=quarter, keep_proportion=False)
        else:
            # Rotazione libera: l'immagine va in una pagina propria che viene poi
            # inserita ruotata (show_pdf_page accetta angoli arbitrari).
            img_doc = pymupdf.open()
            ip = img_doc.new_page(width=width, height=height)
            ip.insert_image(ip.rect, stream=data, keep_proportion=False)
            rect = pymupdf.Rect(center.x - bw / 2, center.y - bh / 2, center.x + bw / 2, center.y + bh / 2)
            self.page.show_pdf_page(rect, img_doc, 0, rotate=rotation)
            img_doc.close()
        self.empty = False

    def fill(self, color, image: tuple[bytes, int, int] | None = None) -> None:
        r = self.page.rect
        if color is not None:
            self.page.draw_rect(r, color=None, fill=color, width=0)
        if image is not None:
            self.page.insert_image(r, stream=image[0], keep_proportion=False)
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


def _layer_xref(pdf: pymupdf.Document, kind: str) -> int:
    """Gruppo di contenuto facoltativo del tipo di timbro (creato se manca)."""
    names = _LAYER_NAMES_ALL[kind]
    try:
        for xref, info in pdf.get_ocgs().items():
            if info.get("name") in names:
                return xref
    except Exception:
        pass
    return pdf.add_ocg(_layer_names()[kind], on=True)


def _next_index(pdf: pymupdf.Document, page: pymupdf.Page) -> int:
    used = 0
    try:
        for item in page.get_xobjects():
            name = item[1]
            if name.startswith(_NAME_PREFIX):
                try:
                    used = max(used, int(name.rsplit("_", 1)[1]) + 1)
                except ValueError:
                    pass
    except Exception:
        pass
    return used


def _stamp_page(pdf: pymupdf.Document, pno: int, layer: _Layer, kind: str, *, behind: bool, oc: int) -> None:
    """Inserisce `layer` nella pagina `pno` come timbro riconoscibile."""
    page = pdf[pno]
    before = set(page.get_contents())
    crop = _pdf_cropbox(page)
    # show_pdf_page converte il rettangolo con transformation_matrix, che sulle
    # pagine ruotate ignora l'origine del CropBox: passiamo il rettangolo che,
    # riconvertito, dà esattamente il CropBox nello spazio PDF.
    rect = crop * page.transformation_matrix
    page.show_pdf_page(rect, layer.doc, 0, keep_proportion=False, overlay=not behind, oc=oc,
                       rotate=page.rotation)
    page = pdf[pno]
    new_xrefs = [x for x in page.get_contents() if x not in before]
    target = None
    old_name = None
    for x in new_xrefs:
        m = _FZ_RE.search(pdf.xref_stream(x) or b"")
        if m:
            target, old_name = x, m.group(1).decode()
            break
    if target is None:  # non dovrebbe accadere
        raise PageOpError(_("Impossibile inserire il timbro nella pagina {n}.").format(n=pno + 1))
    new_name = f"{_NAME_PREFIX}{kind}_{_next_index(pdf, page)}"
    stream = pdf.xref_stream(target)
    pdf.update_stream(target, stream.replace(f"/{old_name} ".encode(), f"/{new_name} ".encode(), 1))
    typ, val = pdf.xref_get_key(page.xref, f"Resources/XObject/{old_name}")
    if typ != "null":
        _set_key(pdf, page.xref, f"Resources/XObject/{new_name}", val)
        _set_key(pdf, page.xref, f"Resources/XObject/{old_name}", "null")


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


def _apply_layers(pdf: pymupdf.Document, kind: str, targets: Sequence[int], build, *,
                  behind: bool = False, opacity: float = 1.0, cache_key=None) -> list[int]:
    """Costruisce con `build(layer, pno)` un livello per pagina e lo applica.

    Con `cache_key(pno)` le pagine con la stessa chiave condividono lo stesso
    livello (un solo XObject nel file).
    """
    oc = _layer_xref(pdf, kind)
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
            _stamp_page(pdf, pno, layer, kind, behind=behind, oc=oc)
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
# Segnaposto
# =========================================================================

def _fill(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template or "")


def _label(pdf: pymupdf.Document, pno: int) -> str:
    try:
        label = pdf[pno].get_label()
    except Exception:
        label = ""
    return label or str(pno + 1)


# =========================================================================
# API pubblica: filigrane e sfondi
# =========================================================================

def add_watermark(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, text: str | None = None,
                  image=None, font_size: float = 60, color=(0.5, 0.5, 0.5), opacity: float = 0.3,
                  rotation: float = 45, position: str = "center", scale: float | None = None,
                  behind: bool = False, font: str = "helv", margin: float = 36, fit: bool = True) -> list[int]:
    """Aggiunge una filigrana di testo o immagine. Restituisce le pagine timbrate.

    `rotation` in gradi, antiorario; `scale` (solo immagini) è la frazione
    della larghezza della pagina (predefinita 0,5); `behind=True` la disegna
    sotto il contenuto. Con `fit` il testo troppo grande viene ridotto per
    restare dentro la pagina.
    """
    targets = _target_pages(pdf, pages)
    has_text = bool(text and text.strip())
    if not has_text and image is None:
        raise PageOpError(_("Indica il testo o l'immagine della filigrana."))
    if has_text and image is not None:
        raise PageOpError(_("Scegli testo oppure immagine, non entrambi."))
    opacity = _check_opacity(opacity)
    pos = _check_position(position)
    margin = _check_margin(margin)
    try:
        rotation = float(rotation) % 360
    except (TypeError, ValueError):
        raise PageOpError(_("Rotazione non valida.")) from None
    if has_text:
        size = _check_font_size(font_size)
        rgb = _check_color(color)
        fnt = _Font(font)

        def build(layer: _Layer, pno: int) -> None:
            layer.text_block(text, pos=pos, margin=margin, font=fnt, size=size, color=rgb, rotation=rotation,
                             align="center", fit=fit)
    else:
        img = _load_image(image)
        if scale is None:
            scale = 0.5
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = -1
        if not (0.01 <= scale <= 5.0):
            raise PageOpError(_("La scala dell'immagine deve essere tra 1% e 500% della larghezza della pagina."))

        def build(layer: _Layer, pno: int) -> None:
            layer.image(img[0], img[1], img[2], pos=pos, margin=margin, width=layer.width * scale,
                        rotation=rotation)

    return _apply_layers(pdf, WATERMARK, targets, build, behind=behind, opacity=opacity,
                         cache_key=lambda pno: 0)


def add_background(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, color=None, image=None,
                   opacity: float = 1.0) -> list[int]:
    """Aggiunge uno sfondo (colore pieno o immagine estesa alla pagina) sotto il contenuto."""
    targets = _target_pages(pdf, pages)
    if color is None and image is None:
        raise PageOpError(_("Indica il colore o l'immagine dello sfondo."))
    rgb = _check_color(color) if color is not None else None
    img = _load_image(image) if image is not None else None
    opacity = _check_opacity(opacity)

    def build(layer: _Layer, pno: int) -> None:
        layer.fill(rgb, img)

    return _apply_layers(pdf, BACKGROUND, targets, build, behind=True, opacity=opacity, cache_key=lambda pno: 0)


# =========================================================================
# API pubblica: numerazioni, intestazioni
# =========================================================================

def _text_stamp_common(font_size, color, margin, position, font):
    return (_check_font_size(font_size), _check_color(color), _check_margin(margin), _check_position(position),
            _Font(font))


def add_page_numbers(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, fmt: str = "{n}",
                     start: int = 1, position: str = "bottom-center", margin: float = 36, font_size: float = 10,
                     color=(0, 0, 0), font: str = "helv", skip_first: bool = False,
                     opacity: float = 1.0) -> list[int]:
    """Numera le pagine. Segnaposto: {n} numero, {total} ultimo numero, {label} etichetta.

    La prima pagina indicata riceve il numero `start`; con `skip_first` la
    prima resta senza numero ma viene comunque contata.
    """
    targets = _target_pages(pdf, pages)
    if not fmt or not fmt.strip():
        raise PageOpError(_("Il formato del numero non può essere vuoto."))
    try:
        start = int(start)
    except (TypeError, ValueError):
        raise PageOpError(_("Il numero iniziale non è valido.")) from None
    if start < 0:
        raise PageOpError(_("Il numero iniziale non può essere negativo."))
    size, rgb, margin, pos, fnt = _text_stamp_common(font_size, color, margin, position, font)
    opacity = _check_opacity(opacity)
    numbers = {pno: start + k for k, pno in enumerate(targets)}
    total = str(start + len(targets) - 1)
    if skip_first:
        targets = targets[1:]
        if not targets:
            raise PageOpError(_("Non ci sono pagine da numerare dopo aver saltato la prima."))

    def build(layer: _Layer, pno: int) -> None:
        txt = _fill(fmt, {"n": str(numbers[pno]), "total": total, "label": _label(pdf, pno)})
        layer.text_block(txt, pos=pos, margin=margin, font=fnt, size=size, color=rgb)

    return _apply_layers(pdf, NUMBERS, targets, build, opacity=opacity)


def format_bates(number: int, *, prefix: str = "", suffix: str = "", digits: int = 6) -> str:
    return f"{prefix}{number:0{digits}d}{suffix}"


def add_bates(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, prefix: str = "", suffix: str = "",
              start: int = 1, digits: int = 6, position: str = "bottom-right", margin: float = 36,
              font_size: float = 10, color=(0, 0, 0), font: str = "helv") -> str:
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

    def build(layer: _Layer, pno: int) -> None:
        layer.text_block(codes[pno], pos=pos, margin=margin, font=fnt, size=size, color=rgb)

    _apply_layers(pdf, BATES, targets, build)
    return codes[targets[-1]]


def add_header_footer(pdf: pymupdf.Document, pages: Iterable[int] | None = None, *, header_left: str = "",
                      header_center: str = "", header_right: str = "", footer_left: str = "",
                      footer_center: str = "", footer_right: str = "", margin: float = 36, font_size: float = 10,
                      color=(0, 0, 0), font: str = "helv", date_format: str = "%d/%m/%Y",
                      filename: str = "", date: _dt.date | None = None) -> list[int]:
    """Intestazione e piè di pagina in sei posizioni.

    Segnaposto: {n} numero di pagina, {total} pagine del documento, {label}
    etichetta, {date} data (formato `date_format`), {file} nome del file.
    """
    targets = _target_pages(pdf, pages)
    slots = {
        "top-left": header_left, "top-center": header_center, "top-right": header_right,
        "bottom-left": footer_left, "bottom-center": footer_center, "bottom-right": footer_right,
    }
    slots = {k: v for k, v in slots.items() if v and v.strip()}
    if not slots:
        raise PageOpError(_("Scrivi almeno un testo di intestazione o piè di pagina."))
    size, rgb, margin, _pos, fnt = _text_stamp_common(font_size, color, margin, "center", font)
    try:
        date_text = (date or _dt.date.today()).strftime(date_format or "%d/%m/%Y")
    except (ValueError, TypeError):
        raise PageOpError(_("Formato della data non valido.")) from None
    total = str(pdf.page_count)
    file_text = os.path.basename(filename or "")

    def build(layer: _Layer, pno: int) -> None:
        values = {"n": str(pno + 1), "total": total, "label": _label(pdf, pno), "date": date_text,
                  "file": file_text}
        for pos, tmpl in slots.items():
            layer.text_block(_fill(tmpl, values), pos=pos, margin=margin, font=fnt, size=size, color=rgb)

    return _apply_layers(pdf, HEADERFOOTER, targets, build)


# =========================================================================
# Elenco e rimozione
# =========================================================================

def _page_stamps(pdf: pymupdf.Document, pno: int) -> list[str]:
    kinds: list[str] = []
    page = pdf[pno]
    for x in page.get_contents():
        for m in _NAME_RE.finditer(pdf.xref_stream(x) or b""):
            kind = m.group(1).decode()
            if kind in KINDS:
                kinds.append(kind)
    return kinds


def list_watermarks(pdf: pymupdf.Document) -> dict[int, list[str]]:
    """Timbri presenti, per pagina: {indice: [tipo, …]} (solo pagine con timbri)."""
    result: dict[int, list[str]] = {}
    for pno in range(pdf.page_count):
        kinds = _page_stamps(pdf, pno)
        if kinds:
            result[pno] = kinds
    return result


def _strip_block_re(kinds: Sequence[str]) -> re.Pattern:
    alt = "|".join(re.escape(k) for k in kinds)
    return re.compile(rb"q\s+(?:[^Qq]*?\s)?/FolStamp_(?:" + alt.encode() + rb")_\d+\s+Do\s+Q")


def remove_watermarks(pdf: pymupdf.Document, pages: Iterable[int] | None = None,
                      kind: str | Sequence[str] | None = WATERMARK) -> int:
    """Rimuove i timbri del tipo indicato (`None` o "all" = tutti). Restituisce quanti ne ha tolti."""
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
    block_re = _strip_block_re(kinds)
    name_re = re.compile(rb"/(FolStamp_(?:" + "|".join(kinds).encode() + rb")_\d+)\s+Do")
    removed = 0
    for pno in targets:
        page = pdf[pno]
        xrefs = page.get_contents()
        keep: list[int] = []
        names: set[str] = set()
        changed = False
        for x in xrefs:
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


def _cleanup_layers(pdf: pymupdf.Document) -> None:
    """Toglie i livelli «Foliant: …» rimasti senza timbri."""
    try:
        ocgs = pdf.get_ocgs()
    except Exception:
        return
    if not ocgs:
        return
    present = {k for kinds in list_watermarks(pdf).values() for k in kinds}
    unused = [xref for xref, info in ocgs.items()
              if any(info.get("name") in names and kind not in present for kind, names in _LAYER_NAMES_ALL.items())]
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


def update_watermark(pdf: pymupdf.Document, pages: Iterable[int] | None = None, **opts) -> list[int]:
    """Sostituisce le filigrane esistenti delle pagine con una nuova (rimuovi + aggiungi)."""
    targets = _target_pages(pdf, pages)
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
    "Foliant: filigrana": "Foliant: watermark",
    "Foliant: sfondo": "Foliant: background",
    "Foliant: numeri di pagina": "Foliant: page numbers",
    "Foliant: numerazione Bates": "Foliant: Bates numbering",
    "Foliant: intestazione e piè di pagina": "Foliant: header and footer",
    "Il documento non è un PDF.": "The document is not a PDF.",
    "Il documento non ha pagine.": "The document has no pages.",
    "Numero di pagina non valido.": "Invalid page number.",
    "La pagina {n} non esiste (il documento ha {tot} pagine).": "Page {n} does not exist (the document has {tot} pages).",
    "Indica almeno una pagina.": "Specify at least one page.",
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
