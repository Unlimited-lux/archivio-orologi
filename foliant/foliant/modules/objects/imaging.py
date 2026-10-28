"""Regolazioni (OBJ-17) e ottimizzazione (OBJ-18) delle immagini: logica pura.

Le funzioni che modificano ricevono il documento del motore (`pdf`) e vanno
chiamate dentro `Document.change()`. Tutti i calcoli (decodifica, elaborazione
dei pixel, ricompressione) avvengono prima di qualunque scrittura: se una
funzione solleva un'eccezione il documento resta invariato.

Limiti noti:
- le immagini in linea (BI … EI) non sono gestite;
- le immagini a 16 bit vengono elaborate e riscritte a 8 bit;
- gli spazi Lab, Separation e DeviceN non sono regolabili (errore con spiegazione);
  CMYK si inverte nello spazio nativo, le altre regolazioni passano per RGB.
"""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass, field

import numpy as np
import pymupdf

from .content import IMAGE, Name, Ref, _fold_refs, _read_value, fmt_name, fmt_num, parse_page, resource_names
from .engine import (
    ObjectError, _check_indices, _sep, apply_edits, object_size, outer_of, prune_resources, set_resource,
    unique_name, unit_bytes, write_content,
)

JPEG, LOSSLESS = "jpeg", "lossless"
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


# ======================================================== lettura dell'XObject
def _key(pdf: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    try:
        return pdf.xref_get_key(xref, key)
    except Exception:
        return "null", "null"


def _parse(text: str):
    """Valore PDF da testo (anche «n 0 R»)."""
    val, _i, _k = _read_value(b"[" + text.encode("latin-1") + b"]", 0)
    val = _fold(val)
    return val[0] if isinstance(val, list) and val else None


def _fold(v):
    if isinstance(v, list):
        return [_fold(x) for x in _fold_refs(v)]
    if isinstance(v, dict):
        return {k: _fold(x) for k, x in v.items()}
    return v


def _fmt(v) -> str:
    """Valore PDF (come restituito da `_parse`) in sintassi PDF."""
    if isinstance(v, Ref):
        return f"{v[0]} {v[1]} R"
    if isinstance(v, Name):
        return fmt_name(v).decode("latin-1")
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return fmt_num(v)
    if isinstance(v, (bytes, bytearray)):
        return "<" + bytes(v).hex() + ">"
    if isinstance(v, list):
        return "[" + " ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return "<<" + "".join(f"/{k} {_fmt(x)}" for k, x in v.items()) + ">>"
    return str(v)


def _filters(pdf: pymupdf.Document, xref: int) -> list[str]:
    t, v = _key(pdf, xref, "Filter")
    if t == "null":
        return []
    val = _parse(v)
    if isinstance(val, list):
        return [str(x) for x in val]
    return [str(val)]


def _ref_xref(pdf: pymupdf.Document, xref: int, key: str) -> int:
    t, v = _key(pdf, xref, key)
    if t == "xref":
        try:
            return int(v.split()[0])
        except (ValueError, IndexError):
            return 0
    return 0


@dataclass
class _ColorSpace:
    kind: str                 # gray, rgb, cmyk, indexed, other
    n: int                    # componenti (per indexed: della base)
    text: str                 # valore originale (riutilizzabile così com'è)
    base: "_ColorSpace | None" = None
    hival: int = 0
    lookup: bytes = b""


def _colorspace_from(pdf: pymupdf.Document, val, text: str) -> _ColorSpace:
    if isinstance(val, Ref):
        try:
            obj = pdf.xref_object(val[0], compressed=True)
        except Exception:
            return _ColorSpace("other", 0, text)
        return _colorspace_from(pdf, _parse(obj), text)
    if isinstance(val, Name):
        name = str(val)
        if name in ("DeviceGray", "CalGray", "G"):
            return _ColorSpace("gray", 1, text)
        if name in ("DeviceRGB", "CalRGB", "RGB"):
            return _ColorSpace("rgb", 3, text)
        if name in ("DeviceCMYK", "CMYK"):
            return _ColorSpace("cmyk", 4, text)
        return _ColorSpace("other", 0, text)
    if isinstance(val, list) and val and isinstance(val[0], Name):
        family = str(val[0])
        if family in ("CalGray",):
            return _ColorSpace("gray", 1, text)
        if family in ("CalRGB",):
            return _ColorSpace("rgb", 3, text)
        if family == "ICCBased" and len(val) > 1 and isinstance(val[1], Ref):
            t, v = _key(pdf, val[1][0], "N")
            try:
                n = int(float(v))
            except ValueError:
                n = 0
            return _ColorSpace({1: "gray", 3: "rgb", 4: "cmyk"}.get(n, "other"), n, text)
        if family in ("Indexed", "I") and len(val) >= 4:
            base = _colorspace_from(pdf, val[1], _fmt(val[1]))
            try:
                hival = int(val[2])
            except (TypeError, ValueError):
                return _ColorSpace("other", 0, text)
            lookup = val[3]
            if isinstance(lookup, Ref):
                try:
                    lookup = pdf.xref_stream(lookup[0])
                except Exception:
                    return _ColorSpace("other", 0, text)
            if not isinstance(lookup, (bytes, bytearray)):
                return _ColorSpace("other", 0, text)
            return _ColorSpace("indexed", base.n, text, base, hival, bytes(lookup))
    return _ColorSpace("other", 0, text)


@dataclass
class _Info:
    xref: int
    width: int
    height: int
    bpc: int
    cs: _ColorSpace | None
    filters: list[str]
    smask: int
    mask: tuple[str, str]
    image_mask: bool
    raw_len: int


def _info(pdf: pymupdf.Document, xref: int) -> _Info:
    if _key(pdf, xref, "Subtype")[1] != "/Image":
        raise ObjectError("L'oggetto selezionato non è un'immagine.")

    def num(k, default=0):
        try:
            return int(float(_key(pdf, xref, k)[1]))
        except ValueError:
            return default

    image_mask = _key(pdf, xref, "ImageMask")[1] == "true"
    cs = None
    if not image_mask:
        t, v = _key(pdf, xref, "ColorSpace")
        if t == "null":
            cs = _ColorSpace("other", 0, "")   # p.es. JPX con spazio nel flusso
        else:
            cs = _colorspace_from(pdf, _parse(v), v)
    try:
        raw_len = len(pdf.xref_stream_raw(xref) or b"")
    except Exception:
        raw_len = 0
    return _Info(xref, num("Width"), num("Height"), num("BitsPerComponent", 8), cs, _filters(pdf, xref),
                 _ref_xref(pdf, xref, "SMask"), _key(pdf, xref, "Mask"), image_mask, raw_len)


def _is_lossy(info: _Info) -> bool:
    return any(f in ("DCTDecode", "DCT", "JPXDecode") for f in info.filters)


# ================================================================ pixel
def _pixels(pdf: pymupdf.Document, xref: int) -> tuple[np.ndarray, np.ndarray | None, bool]:
    """Pixel decodificati (h, w, 1|3), alfa da chiave di colore e flag «convertito da CMYK»."""
    try:
        pix = pymupdf.Pixmap(pdf, xref)
    except Exception as e:
        raise ObjectError("Impossibile decodificare l'immagine: dati danneggiati o formato non supportato.") from e
    alpha = None
    if pix.alpha:
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        alpha = a[:, :, -1].copy()
        pix = pymupdf.Pixmap(pix, 0)
    converted = False
    if pix.n not in (1, 3):
        try:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        except Exception as e:
            raise ObjectError("Lo spazio colore dell'immagine non è supportato.") from e
        converted = True
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n).copy()
    return arr, alpha, converted


def _to_image(arr: np.ndarray):
    from PIL import Image
    if arr.shape[2] == 1:
        return Image.fromarray(arr[:, :, 0], "L")
    return Image.fromarray(arr, "RGB")


def _encode_flate(im) -> tuple[bytes, str, str]:
    """Dati Flate con predittore PNG (estratti da un PNG di Pillow)."""
    if im.mode not in ("L", "RGB"):
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=9)
    png = buf.getvalue()
    pos, idat = 8, bytearray()
    while pos + 8 <= len(png):
        length, ctype = struct.unpack(">I4s", png[pos:pos + 8])
        if ctype == b"IDAT":
            idat += png[pos + 8:pos + 8 + length]
        pos += 12 + length
    colors = 1 if im.mode == "L" else 3
    parms = f"<</Predictor 15/Colors {colors}/BitsPerComponent 8/Columns {im.width}>>"
    return bytes(idat), "/FlateDecode", parms


def _encode_jpeg(im, quality: int) -> tuple[bytes, str, str]:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=int(max(1, min(100, quality))), optimize=True)
    return buf.getvalue(), "/DCTDecode", ""


def _adjust_array(a: np.ndarray, brightness: float, contrast: float, saturation: float,
                  grayscale: bool, invert: bool) -> np.ndarray:
    """Regolazioni su un array (…, 1|3) di valori 0..255. Restituisce uint8."""
    x = a.astype(np.float32)
    if brightness:
        x = x + brightness * 2.55
    if contrast:
        c = contrast * 2.55
        f = (259.0 * (c + 255.0)) / (255.0 * (259.0 - c))
        x = (x - 128.0) * f + 128.0
    if x.shape[-1] == 3 and (saturation or grayscale):
        g = (np.clip(x, 0, 255) @ _LUMA)[..., None]
        if saturation:
            x = g + (x - g) * (1.0 + saturation / 100.0)
        if grayscale:
            x = np.clip(x, 0, 255) @ _LUMA
            x = x[..., None]
    x = np.clip(np.rint(x), 0, 255)
    if invert:
        x = 255.0 - x
    return x.astype(np.uint8)


def preview_base(png: bytes, max_side: int = 240) -> np.ndarray:
    """Copia ridotta (RGB uint8) di un'immagine per l'anteprima delle regolazioni."""
    from PIL import Image
    with Image.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        return np.asarray(im, dtype=np.uint8).copy()


def preview_adjusted(base: np.ndarray, brightness: float = 0, contrast: float = 0, saturation: float = 0,
                     grayscale: bool = False, invert: bool = False) -> np.ndarray:
    """Anteprima delle regolazioni sulla copia ridotta (stessa resa di `adjust_image`)."""
    out = _adjust_array(base, brightness, contrast, saturation, grayscale, invert)
    if out.shape[-1] == 1:
        out = np.repeat(out, 3, axis=-1)
    return out


# ============================================================== scrittura
NEW_MASK = -1                 # xref segnaposto: nuova maschera alfa
_MASK_REF = "@MASK@"          # segnaposto nel dizionario dell'immagine che la usa


@dataclass
class _Write:
    xref: int                 # 0 = nuovo oggetto, NEW_MASK = nuova maschera
    dict_text: str
    raw: bytes
    filt: str = ""
    parms: str = ""
    overrides: dict = field(default_factory=dict)


def _apply_write(pdf: pymupdf.Document, w: _Write) -> int:
    xref = w.xref
    if not xref:
        xref = pdf.get_new_xref()
        pdf.update_object(xref, "<<>>")
    pdf.update_object(xref, w.dict_text)
    pdf.update_stream(xref, w.raw, new=True, compress=False)   # toglie /Filter e /DecodeParms
    if w.filt:
        pdf.xref_set_key(xref, "Filter", w.filt)
    if w.parms:
        pdf.xref_set_key(xref, "DecodeParms", w.parms)
    return xref


def _mask_write(marr: np.ndarray, size: tuple[int, int] | None = None, xref: int = NEW_MASK) -> _Write:
    """Scrittura di una maschera alfa in scala di grigi (eventualmente ricampionata)."""
    from PIL import Image
    im = _to_image(marr)
    if size is not None and size != im.size:
        im = im.resize(size, Image.LANCZOS)
    raw, filt, parms = _encode_flate(im)
    return _Write(xref, f"<</Type/XObject/Subtype/Image/Width {im.width}/Height {im.height}"
                        f"/ColorSpace/DeviceGray/BitsPerComponent 8>>", raw, filt, parms)


def _image_dict(pdf: pymupdf.Document, info: _Info, w: int, h: int, cs_text: str,
                smask_ref: str | None = None) -> str:
    parts = ["/Type/XObject/Subtype/Image", f"/Width {w}/Height {h}", f"/ColorSpace {cs_text}",
             "/BitsPerComponent 8"]
    if smask_ref is None and info.smask:
        smask_ref = f"{info.smask} 0 R"
    if smask_ref:
        parts.append(f"/SMask {smask_ref}")
    elif info.mask[0] == "xref":
        parts.append(f"/Mask {info.mask[1]}")
    for k in ("Interpolate", "Intent"):
        t, v = _key(pdf, info.xref, k)
        if t != "null":
            parts.append(f"/{k} {v}")
    return "<<" + "".join(parts) + ">>"


def _clone_write(pdf: pymupdf.Document, xref: int, target: int, overrides: dict[str, str | None]) -> _Write:
    """Copia (o riscrittura sul posto) dell'oggetto con flusso grezzo identico."""
    raw = pdf.xref_stream_raw(xref) or b""
    text = pdf.xref_object(xref, compressed=True)
    filt = _key(pdf, xref, "Filter")
    parms = _key(pdf, xref, "DecodeParms")
    return _Write(target, text, raw, filt[1] if filt[0] != "null" else "",
                  parms[1] if parms[0] != "null" else "", overrides)


def _apply_all(pdf: pymupdf.Document, writes: list[_Write]) -> int:
    """Esegue le scritture pianificate; restituisce l'xref dell'ultima."""
    xref = mask = 0
    for w in writes:
        is_mask = w.xref == NEW_MASK
        target = 0 if is_mask else w.xref
        text = w.dict_text.replace(_MASK_REF, f"{mask} 0 R") if _MASK_REF in w.dict_text else w.dict_text
        xref = _apply_write(pdf, _Write(target, text, w.raw, w.filt, w.parms))
        for k, v in w.overrides.items():
            pdf.xref_set_key(xref, k, "null" if v is None else v)
        if is_mask:
            mask = xref
    return xref


# ================================================================ OBJ-17
def _palette_plan(pdf: pymupdf.Document, info: _Info, brightness, contrast, saturation, grayscale, invert) -> str:
    """Nuovo spazio /Indexed: si modifica solo la palette."""
    cs = info.cs
    base = cs.base
    count = cs.hival + 1
    if base is None or base.kind not in ("gray", "rgb", "cmyk") or count <= 0:
        raise ObjectError("La palette dell'immagine usa uno spazio colore non supportato.")
    n = base.n
    if len(cs.lookup) < count * n:
        raise ObjectError("La palette dell'immagine è danneggiata.")
    pal = np.frombuffer(cs.lookup[:count * n], dtype=np.uint8).reshape(count, n)
    base_text = base.text
    only_invert = invert and not (brightness or contrast or saturation or grayscale)
    if base.kind == "cmyk" and not only_invert:
        pix = pymupdf.Pixmap(pymupdf.csCMYK, count, 1, pal.tobytes(), False)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        pal = np.frombuffer(pix.samples, dtype=np.uint8).reshape(count, 3)
        base_text = "/DeviceRGB"
    new = _adjust_array(pal, brightness, contrast, saturation, grayscale, invert)
    if grayscale and new.shape[-1] == 1 and base.kind != "gray":
        base_text = "/DeviceGray"
    return f"[/Indexed {base_text} {cs.hival} <{new.tobytes().hex()}>]"


def _adjust_plan(pdf: pymupdf.Document, info: _Info, brightness, contrast, saturation, grayscale, invert,
                 jpeg_quality: int, target: int) -> list[_Write]:
    if info.image_mask:
        if brightness or contrast or saturation or grayscale or not invert:
            raise ObjectError("Per una maschera stencil (/ImageMask) è disponibile solo «Inverti».")
        t, v = _key(pdf, info.xref, "Decode")
        flipped = False
        if t == "array":
            d = _parse(v)
            flipped = isinstance(d, list) and len(d) >= 2 and float(d[0]) > float(d[1])
        return [_clone_write(pdf, info.xref, target, {"Decode": "[0 1]" if flipped else "[1 0]"})]
    cs = info.cs
    if cs.kind == "indexed" and info.bpc in (1, 2, 4, 8):
        new_cs = _palette_plan(pdf, info, brightness, contrast, saturation, grayscale, invert)
        return [_clone_write(pdf, info.xref, target, {"ColorSpace": new_cs})]
    if cs.kind == "other":
        raise ObjectError("Lo spazio colore dell'immagine (Lab, Separation, DeviceN o simile) "
                          "non consente le regolazioni.")
    only_invert = invert and not (brightness or contrast or saturation or grayscale)
    if cs.kind == "cmyk" and only_invert and not _is_lossy(info) and info.mask[0] != "array":
        # inversione nello spazio nativo CMYK, senza perdita
        pix = pymupdf.Pixmap(pdf, info.xref)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        raw = bytes(255 - np.frombuffer(pix.samples, dtype=np.uint8))
        dict_text = _image_dict(pdf, info, pix.width, pix.height, cs.text)
        return [_Write(target, dict_text, zlib.compress(raw, 9), "/FlateDecode")]
    arr, alpha, converted = _pixels(pdf, info.xref)
    writes: list[_Write] = []
    smask_ref = None
    if alpha is not None and not info.smask:
        # chiave di colore (/Mask [..]): diventa una maschera alfa che resta invariata
        writes.append(_mask_write(alpha[:, :, None]))
        smask_ref = _MASK_REF
    out = _adjust_array(arr, brightness, contrast, saturation, grayscale, invert)
    if out.shape[-1] == 1:
        cs_text = cs.text if cs.kind == "gray" and not converted else "/DeviceGray"
    else:
        cs_text = cs.text if cs.kind == "rgb" and not converted else "/DeviceRGB"
    im = _to_image(out)
    if _is_lossy(info):
        raw, filt, parms = _encode_jpeg(im, jpeg_quality)
    else:
        raw, filt, parms = _encode_flate(im)
    writes.append(_Write(target, _image_dict(pdf, info, im.width, im.height, cs_text, smask_ref), raw, filt, parms))
    return writes


def adjust_image(pdf: pymupdf.Document, page_index: int, index: int, brightness: float = 0, contrast: float = 0,
                 saturation: float = 0, grayscale: bool = False, invert: bool = False,
                 all_occurrences: bool = False, jpeg_quality: int = 90) -> list[int]:
    """Regola luminosità, contrasto, saturazione, scala di grigi e inversione (OBJ-17).

    Con `all_occurrences` si riscrive l'XObject condiviso (cambiano tutte le
    occorrenze); altrimenti si crea un nuovo XObject solo per questo oggetto."""
    for label, v in (("luminosità", brightness), ("contrasto", contrast), ("saturazione", saturation)):
        if not -100 <= v <= 100:
            raise ObjectError(f"Il valore di {label} deve essere compreso tra -100 e +100.")
    if not (brightness or contrast or saturation or grayscale or invert):
        raise ObjectError("Nessuna regolazione da applicare.")
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    (o,) = _check_indices(parsed, [index])
    if o.kind != IMAGE:
        raise ObjectError("L'oggetto selezionato non è un'immagine.")
    if o.inline or not o.xref:
        raise ObjectError("Le immagini in linea non si possono regolare.")
    info = _info(pdf, o.xref)
    # Il calcolo avviene tutto qui: le eventuali eccezioni arrivano prima di qualunque scrittura.
    target = o.xref if all_occurrences else 0
    writes = _adjust_plan(pdf, info, brightness, contrast, saturation, grayscale, invert, jpeg_quality, target)
    new_xref = _apply_all(pdf, writes)
    if all_occurrences:
        return [o.index]
    page = pdf[page_index]
    name = unique_name(resource_names(pdf, page, "XObject").keys(), "FoAdj")
    set_resource(pdf, page, "XObject", name, f"{new_xref} 0 R")
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    o = parsed.objects[index]
    s, e = o.span
    unit = unit_bytes(parsed, o, o.ctm, outer_of(o), body=fmt_name(name) + b" Do")
    data, _ = apply_edits(parsed.data, [(s, e, _sep(unit))])
    write_content(pdf, page, data)
    if o.name:
        prune_resources(pdf, page_index, "XObject", [o.name])
    return [o.index]


# ================================================================ OBJ-18
def _draw_sizes(pdf: pymupdf.Document, xrefs: set[int]) -> dict[int, list[tuple[float, float]]]:
    """Dimensioni disegnate (pt) di ogni occorrenza degli XObject indicati, in tutto il documento."""
    out: dict[int, list[tuple[float, float]]] = {x: [] for x in xrefs}
    for i in range(pdf.page_count):
        try:
            objs = parse_page(pdf, pdf[i]).objects
        except Exception:
            continue
        for o in objs:
            if o.kind == IMAGE and o.xref in out:
                out[o.xref].append(object_size(o))
    return out


def _optimize_plan(pdf: pymupdf.Document, page_index: int, indices, max_ppi: float, mode: str, quality: int,
                   grayscale: bool) -> dict:
    if mode not in (JPEG, LOSSLESS):
        raise ObjectError("Metodo di compressione non valido.")
    if max_ppi is not None and max_ppi <= 0:
        raise ObjectError("La risoluzione massima deve essere maggiore di zero.")
    parsed = parse_page(pdf, pdf[page_index])
    objs = _check_indices(parsed, indices)
    by_xref: dict[int, list[int]] = {}
    skipped: list[int] = []
    for o in objs:
        if o.kind != IMAGE or o.inline or not o.xref:
            skipped.append(o.index)
        else:
            by_xref.setdefault(o.xref, []).append(o.index)
    if not by_xref:
        raise ObjectError("Nessuna immagine ottimizzabile tra gli oggetti selezionati.")
    sizes = _draw_sizes(pdf, set(by_xref))
    writes: list[_Write] = []
    kept: list[int] = []
    changed: list[int] = []
    before = after = 0
    done_masks: set[int] = set()
    for xref, idxs in by_xref.items():
        info = _info(pdf, xref)
        mask_len = 0
        if info.smask:
            try:
                mask_len = len(pdf.xref_stream_raw(info.smask) or b"")
            except Exception:
                mask_len = 0
        orig = info.raw_len + (mask_len if info.smask not in done_masks else 0)
        if info.image_mask or info.cs is None or info.cs.kind == "other" or \
                (info.cs.kind == "indexed" and (info.cs.base is None or info.cs.base.kind == "other")) or \
                (info.smask and _key(pdf, info.smask, "Matte")[0] != "null"):
            skipped.extend(idxs)
            continue
        before += orig
        try:
            arr, alpha, converted = _pixels(pdf, xref)
        except ObjectError:
            skipped.extend(idxs)
            before -= orig
            continue
        H, W = arr.shape[:2]
        # risoluzione effettiva: l'occorrenza disegnata più piccola (ppi più alti)
        nw, nh = W, H
        draws = [s for s in sizes.get(xref, []) if s[0] > 0 and s[1] > 0]
        if max_ppi and draws:
            min_w = min(s[0] for s in draws) / 72.0
            min_h = min(s[1] for s in draws) / 72.0
            ppi_x, ppi_y = W / min_w, H / min_h
            if ppi_x > max_ppi:
                nw = max(1, round(W * max_ppi / ppi_x))
            if ppi_y > max_ppi:
                nh = max(1, round(H * max_ppi / ppi_y))
        im = _to_image(arr)
        if grayscale and im.mode != "L":
            im = im.convert("L")
        from PIL import Image
        if (nw, nh) != (W, H):
            im = im.resize((nw, nh), Image.LANCZOS)
        if mode == JPEG:
            raw, filt, parms = _encode_jpeg(im, quality)
        else:
            raw, filt, parms = _encode_flate(im)
        if im.mode == "L":
            cs_text = info.cs.text if info.cs.kind == "gray" and not converted else "/DeviceGray"
        else:
            cs_text = info.cs.text if info.cs.kind == "rgb" and not converted else "/DeviceRGB"
        img_writes: list[_Write] = []
        new_mask_len = mask_len if info.smask not in done_masks else 0
        smask_ref = None
        if alpha is not None and not info.smask:
            mw_ = _mask_write(alpha[:, :, None], (nw, nh))
            img_writes.append(mw_)
            new_mask_len = len(mw_.raw)
            smask_ref = _MASK_REF
        elif info.smask and (nw, nh) != (W, H) and info.smask not in done_masks:
            try:
                mpix = pymupdf.Pixmap(pdf, info.smask)
                if mpix.alpha:
                    mpix = pymupdf.Pixmap(mpix, 0)
                if mpix.n != 1:
                    mpix = pymupdf.Pixmap(pymupdf.csGRAY, mpix)
                marr = np.frombuffer(mpix.samples, dtype=np.uint8).reshape(mpix.height, mpix.width, 1)
            except Exception:
                marr = None
            if marr is not None:
                size = (max(1, round(mpix.width * nw / W)), max(1, round(mpix.height * nh / H)))
                mw_ = _mask_write(marr, size, info.smask)
                img_writes.append(mw_)
                new_mask_len = len(mw_.raw)
        new_total = len(raw) + new_mask_len
        if new_total >= orig:
            kept.extend(idxs)
            after += orig
            continue
        if info.smask:
            done_masks.add(info.smask)
        img_writes.append(_Write(xref, _image_dict(pdf, info, nw, nh, cs_text, smask_ref), raw, filt, parms))
        writes.extend(img_writes)
        changed.extend(idxs)
        after += new_total
    return {"changed": len(changed), "changed_indices": sorted(changed), "kept": sorted(kept),
            "skipped": sorted(skipped), "before": before, "after": after, "_writes": writes}


def optimize_images(pdf: pymupdf.Document, page_index: int, indices, max_ppi: float = 150, mode: str = JPEG,
                    quality: int = 80, grayscale: bool = False) -> dict:
    """Ricampiona e ricomprime le immagini selezionate (OBJ-18).

    Le immagini sono riscritte sul posto: le dimensioni disegnate non cambiano
    (la matrice dell'occorrenza mappa il quadrato unitario). Se il risultato
    sarebbe più pesante, l'immagine resta identica ed è indicata in "kept".
    Restituisce {"changed", "changed_indices", "kept", "skipped", "before", "after"}."""
    plan = _optimize_plan(pdf, page_index, indices, max_ppi, mode, quality, grayscale)
    writes = plan.pop("_writes")
    if writes:
        _apply_all(pdf, writes)
    return plan


def estimate_optimization(pdf: pymupdf.Document, page_index: int, indices, max_ppi: float = 150,
                          mode: str = JPEG, quality: int = 80, grayscale: bool = False) -> dict:
    """Come `optimize_images` ma senza modificare il documento."""
    plan = _optimize_plan(pdf, page_index, indices, max_ppi, mode, quality, grayscale)
    plan.pop("_writes")
    return plan
