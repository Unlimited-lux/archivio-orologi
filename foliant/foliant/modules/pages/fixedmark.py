"""Filigrana a posizione fissa di stampa (PAG-37), senza interfaccia.

Annotazione `/Subtype /Watermark` con dizionario `/FixedPrint` (`/Matrix`,
`/H`, `/V` come frazioni del foglio di stampa), aspetto `/AP /N` costituito
da un Form XObject con testo Helvetica e trasparenza, flag Print attivo.
Chi stampa rispettando `/FixedPrint` colloca l'aspetto a (H × larghezza del
foglio, V × altezza del foglio) qualunque sia la scala di stampa; gli altri
lettori la mostrano nella posizione nominale (`/Rect`), che coincide con la
stessa frazione del supporto della pagina.
"""

from __future__ import annotations

import math
from typing import Iterable

import pymupdf

from foliant.api import _

from .engine import PageOpError
from .layout import ANCHORS, _disp_frac_to_user, _media, _rect_str, _rot, disp_to_user

TRANSLATIONS = {
    "Il testo della filigrana è vuoto.": "The watermark text is empty.",
    "La filigrana non entra nella pagina {n}.": "The watermark does not fit on page {n}.",
    "Posizione non valida: H e V devono essere tra 0 e 1.": "Invalid position: H and V must be between 0 and 1.",
}

MARK_PREFIX = "foliant-fixedmark-"


def _pdf_text(text: str) -> str:
    raw = text.encode("cp1252", "replace")
    out = []
    for b in raw:
        c = chr(b)
        if c in "()\\":
            out.append("\\" + c)
        elif 32 <= b < 127:
            out.append(c)
        else:
            out.append(f"\\{b:03o}")
    return "(" + "".join(out) + ")"


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def appearance_size(text: str, fontsize: float, rotation: float) -> tuple[float, float]:
    """Larghezza e altezza del riquadro dell'aspetto (testo ruotato in senso antiorario)."""
    w = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
    h = fontsize * 1.2
    a = math.radians(rotation)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    return w * c + h * s, w * s + h * c


def add_fixed_watermark(pdf: pymupdf.Document, pages: Iterable[int], text: str, *, fontsize: float = 36.0,
                        color: tuple[float, float, float] = (0.8, 0.1, 0.1), opacity: float = 0.5,
                        rotation: float = 0.0, anchor: str = "top-right", margin: float = 18.0,
                        h: float | None = None, v: float | None = None) -> list[int]:
    """Aggiunge la filigrana alle pagine; restituisce gli xref delle annotazioni.

    La posizione è un ancoraggio (9 posizioni, riferite alla pagina
    visualizzata) con margine in punti, oppure esplicita con `h`/`v` (frazioni
    0…1 del foglio, origine in basso a sinistra, angolo inferiore sinistro
    dell'aspetto).
    """
    text = (text or "").strip()
    if not text:
        raise PageOpError(_("Il testo della filigrana è vuoto."))
    if (h is None) != (v is None) or (h is not None and not (0 <= h <= 1 and 0 <= v <= 1)):
        raise PageOpError(_("Posizione non valida: H e V devono essere tra 0 e 1."))
    opacity = min(max(float(opacity), 0.0), 1.0)
    xrefs: list[int] = []
    for i in pages:
        page = pdf[i]
        rot = _rot(page)
        media = _media(pdf, page)
        eff = (rotation + rot) % 360          # orientamento sulla pagina visualizzata
        bw, bh = appearance_size(text, fontsize, eff)
        if h is not None:
            x0 = media.x0 + h * media.width
            y0 = media.y0 + v * media.height
            rect = pymupdf.Rect(x0, y0, x0 + bw, y0 + bh)
        else:
            dw, dh = (media.width, media.height) if rot in (0, 180) else (media.height, media.width)
            vw, vh = (bw, bh) if rot in (0, 180) else (bh, bw)
            if vw + 2 * margin > dw or vh + 2 * margin > dh:
                raise PageOpError(_("La filigrana non entra nella pagina {n}.").format(n=i + 1))
            fx, fy = ANCHORS[anchor]
            dx = margin + (dw - vw - 2 * margin) * fx
            dy = margin + (dh - vh - 2 * margin) * fy
            rect = disp_to_user(media, rot, pymupdf.Rect(dx, dy, dx + vw, dy + vh))
            rect = pymupdf.Rect(rect.x0, rect.y0, rect.x0 + bw, rect.y0 + bh)
        hh = (rect.x0 - media.x0) / media.width
        vv = (rect.y0 - media.y0) / media.height
        # aspetto: testo ruotato attorno al centro del riquadro
        tw = pymupdf.get_text_length(text, fontname="helv", fontsize=fontsize)
        a = math.radians(eff)
        c, s = math.cos(a), math.sin(a)
        # punto di partenza della linea di base perché il testo sia centrato nel riquadro
        cx, cy = bw / 2, bh / 2
        ox = cx - (c * tw / 2 - s * (fontsize * 0.35))
        oy = cy - (s * tw / 2 + c * (fontsize * 0.35))
        r, g, b = color
        stream = (f"q /GS0 gs {_f(r)} {_f(g)} {_f(b)} rg BT /Helv {_f(fontsize)} Tf "
                  f"{_f(c)} {_f(s)} {_f(-s)} {_f(c)} {_f(ox)} {_f(oy)} Tm {_pdf_text(text)} Tj ET Q").encode()
        font = pdf.get_new_xref()
        pdf.update_object(font, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        form = pdf.get_new_xref()
        pdf.update_object(form, (f"<< /Type /XObject /Subtype /Form /BBox [0 0 {_f(bw)} {_f(bh)}] "
                                 f"/Matrix [1 0 0 1 0 0] /Resources << /Font << /Helv {font} 0 R >> "
                                 f"/ExtGState << /GS0 << /Type /ExtGState /ca {_f(opacity)} /CA {_f(opacity)} >> >> >> >>"))
        pdf.update_stream(form, stream)
        annot = pdf.get_new_xref()
        pdf.update_object(annot, (
            f"<< /Type /Annot /Subtype /Watermark /Rect {_rect_str(rect)} /F 4 /P {page.xref} 0 R "
            f"/NM {pymupdf.get_pdf_str(MARK_PREFIX + str(annot))} /Contents {pymupdf.get_pdf_str(text)} "
            f"/AP << /N {form} 0 R >> "
            f"/FixedPrint << /Type /FixedPrint /Matrix [1 0 0 1 0 0] /H {_f(hh)} /V {_f(vv)} >> >>"))
        typ, val = pdf.xref_get_key(page.xref, "Annots")
        if typ == "array":
            pdf.xref_set_key(page.xref, "Annots", val.rstrip().rstrip("]") + f" {annot} 0 R]")
        elif typ == "xref":
            arr_x = int(val.split()[0])
            old = pdf.xref_object(arr_x, compressed=True).strip()
            pdf.update_object(arr_x, old.rstrip("]") + f" {annot} 0 R]")
        else:
            pdf.xref_set_key(page.xref, "Annots", f"[{annot} 0 R]")
        xrefs.append(annot)
    return xrefs


def fixed_watermarks(pdf: pymupdf.Document, index: int) -> list[dict]:
    """Filigrane di stampa della pagina: xref, /H, /V, /Rect, testo."""
    out = []
    page = pdf[index]
    for a in page.annots() or []:
        if a.type[1] != "Watermark":
            continue
        fp_h = pdf.xref_get_key(a.xref, "FixedPrint/H")
        fp_v = pdf.xref_get_key(a.xref, "FixedPrint/V")
        out.append({"xref": a.xref, "h": float(fp_h[1]) if fp_h[0] != "null" else None,
                    "v": float(fp_v[1]) if fp_v[0] != "null" else None,
                    "text": a.info.get("content", ""), "rect": a.rect})
    return out


def print_position(h: float, v: float, sheet_size: tuple[float, float]) -> tuple[float, float]:
    """Posizione (dal basso a sinistra) dell'aspetto su un foglio di stampa secondo /FixedPrint:
    indipendente dalla scala con cui la pagina viene stampata."""
    return h * sheet_size[0], v * sheet_size[1]


def remove_fixed_watermarks(pdf: pymupdf.Document, pages: Iterable[int], only_ours: bool = False) -> int:
    """Rimuove le annotazioni Watermark (solo quelle create da Foliant con `only_ours`)."""
    n = 0
    for i in pages:
        page = pdf[i]
        for a in list(page.annots() or []):
            if a.type[1] != "Watermark":
                continue
            if only_ours and not (a.info.get("id", "") or "").startswith(MARK_PREFIX):
                continue
            page.delete_annot(a)
            n += 1
    return n
