"""Copia e incolla di oggetti tra pagine e documenti (logica pura).

Il formato interno è un PDF di una pagina che contiene solo gli oggetti
copiati con le loro risorse: è autosufficiente, quindi l'incolla funziona
anche dopo la chiusura del documento d'origine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pymupdf

from .content import Name, fmt_name, rcenter, parse_page, resource_entry, resource_names, tokenize_ops
from .engine import (
    IDENTITY, ObjectError, _check_indices, _sep, apply_edits, set_resource, union_rect, unit_bytes, write_content,
)

MIME_INTERNAL = "application/x-foliant-objects"
CATEGORIES = ("XObject", "ExtGState", "ColorSpace", "Pattern", "Shading", "Font", "Properties")


@dataclass
class Payload:
    pdf: bytes
    meta: dict

    def to_bytes(self) -> bytes:
        head = json.dumps(self.meta).encode()
        return len(head).to_bytes(4, "big") + head + self.pdf

    @classmethod
    def from_bytes(cls, data: bytes) -> "Payload":
        n = int.from_bytes(data[:4], "big")
        meta = json.loads(data[4:4 + n].decode())
        return cls(data[4 + n:], meta)

    @property
    def rect(self) -> pymupdf.Rect:
        return pymupdf.Rect(self.meta["rect"])


def _names_in(data: bytes) -> dict[str, set]:
    used: dict[str, set] = {}

    def add(cat, n):
        if isinstance(n, Name):
            used.setdefault(cat, set()).add(str(n))

    for op in tokenize_ops(data):
        o = op.operands
        if op.name == "Do" and o:
            add("XObject", o[0])
        elif op.name == "gs" and o:
            add("ExtGState", o[0])
        elif op.name in ("cs", "CS") and o:
            add("ColorSpace", o[0])
        elif op.name in ("scn", "SCN") and o:
            add("Pattern", o[-1])
        elif op.name == "sh" and o:
            add("Shading", o[0])
        elif op.name == "Tf" and o:
            add("Font", o[0])
        elif op.name in ("BDC", "DP") and len(o) > 1:
            add("Properties", o[1])
        elif op.name == "BI" and op.data:
            cs = op.data["dict"].get("CS", op.data["dict"].get("ColorSpace"))
            if isinstance(cs, Name) and str(cs) not in ("DeviceRGB", "DeviceGray", "DeviceCMYK", "RGB", "G", "CMYK"):
                add("ColorSpace", cs)
    return used


def make_payload(pdf: pymupdf.Document, page_index: int, indices) -> Payload:
    """Crea il contenuto degli appunti per gli oggetti indicati."""
    page = pdf[page_index]
    parsed = parse_page(pdf, page)
    objs = _check_indices(parsed, indices)
    snippet = b"\n".join(unit_bytes(parsed, o, o.ctm, IDENTITY, include_state=True) for o in objs)
    used = _names_in(snippet)
    out = pymupdf.open()
    try:
        out.insert_pdf(pdf, from_page=page_index, to_page=page_index, links=False, annots=False)
    except TypeError:
        out.insert_pdf(pdf, from_page=page_index, to_page=page_index, links=False)
    op = out[0]
    entries = []
    for cat in CATEGORIES:
        names = used.get(cat)
        if not names:
            continue
        inner = []
        for n in sorted(names):
            typ, val = resource_entry(out, op, cat, n)
            if typ != "null":
                inner.append(fmt_name(n).decode() + " " + val)
        if inner:
            entries.append(f"/{cat}<<" + " ".join(inner) + ">>")
    out.xref_set_key(op.xref, "Resources", "<<" + "".join(entries) + ">>")
    out.xref_set_key(op.xref, "Annots", "null")
    write_content(out, op, snippet)
    data = out.tobytes(garbage=3, deflate=True)
    out.close()
    rect = union_rect(objs)
    text = ""
    try:
        text = page.get_text("text", clip=rect).strip()
    except Exception:
        pass
    meta = {
        "rect": [rect.x0, rect.y0, rect.x1, rect.y1],
        "page": page_index,
        "doc": id(pdf),
        "count": len(objs),
        "text": text,
    }
    return Payload(data, meta)


def _rename_ops(data: bytes, renames: dict[tuple[str, str], str]) -> bytes:
    if not renames:
        return data
    edits = []
    for op in tokenize_ops(data):
        o = op.operands
        new = None
        if op.name in ("Do", "gs", "cs", "CS", "sh") and o:
            cat = {"Do": "XObject", "gs": "ExtGState", "cs": "ColorSpace", "CS": "ColorSpace", "sh": "Shading"}[op.name]
            r = renames.get((cat, str(o[0])))
            if r:
                new = fmt_name(r) + b" " + op.name.encode()
        elif op.name == "Tf" and len(o) >= 2:
            r = renames.get(("Font", str(o[0])))
            if r:
                new = fmt_name(r) + b" " + str(o[1]).encode() + b" Tf"
        elif op.name in ("scn", "SCN") and o and isinstance(o[-1], Name):
            r = renames.get(("Pattern", str(o[-1])))
            if r:
                nums = b" ".join(str(v).encode() for v in o[:-1])
                new = (nums + b" " if nums else b"") + fmt_name(r) + b" " + op.name.encode()
        elif op.name in ("BDC", "DP") and len(o) > 1 and isinstance(o[1], Name):
            r = renames.get(("Properties", str(o[1])))
            if r:
                new = fmt_name(str(o[0])) + b" " + fmt_name(r) + b" " + op.name.encode()
        if new is not None:
            edits.append((op.start, op.end, new))
    out, _ = apply_edits(data, edits)
    return out


def paste_payload(pdf: pymupdf.Document, page_index: int, payload: Payload, dx: float = 0.0,
                  dy: float = 0.0, center: pymupdf.Point | None = None) -> list[int]:
    """Incolla gli oggetti nella pagina, traslati di (dx, dy) o centrati in `center`.

    Restituisce gli indici dei nuovi oggetti (in cima all'ordine di disegno)."""
    try:
        src = pymupdf.open(stream=payload.pdf, filetype="pdf")
    except Exception as e:
        raise ObjectError("Il contenuto degli appunti non è valido.") from e
    try:
        sp = src[0]
        sparsed = parse_page(src, sp)
        if not sparsed.objects:
            raise ObjectError("Gli appunti non contengono oggetti.")
        page = pdf[page_index]
        rect = payload.rect
        if center is not None:
            dx, dy = center.x - rcenter(rect).x, center.y - rcenter(rect).y
        # rientra nel riquadro visibile della pagina di destinazione
        pr = pymupdf.Rect(0, 0, page.cropbox.width, page.cropbox.height)
        moved = pymupdf.Rect(rect.x0 + dx, rect.y0 + dy, rect.x1 + dx, rect.y1 + dy)
        if moved.width <= pr.width:
            if moved.x1 > pr.x1:
                dx -= moved.x1 - pr.x1
            elif moved.x0 < pr.x0:
                dx += pr.x0 - moved.x0
        else:
            dx = rcenter(pr).x - rcenter(rect).x
        if moved.height <= pr.height:
            if moved.y1 > pr.y1:
                dy -= moved.y1 - pr.y1
            elif moved.y0 < pr.y0:
                dy += pr.y0 - moved.y0
        else:
            dy = rcenter(pr).y - rcenter(rect).y
        # copia le risorse nel documento di destinazione tramite una pagina temporanea
        renames: dict[tuple[str, str], str] = {}
        tmp_index = pdf.page_count
        first_new_xref = pdf.xref_length()
        pdf.insert_pdf(src, from_page=0, to_page=0, links=False)
        known: dict | None = None
        try:
            tp = pdf[tmp_index]
            page = pdf[page_index]
            for cat in CATEGORIES:
                incoming = resource_names(pdf, tp, cat)
                if not incoming:
                    continue
                existing = resource_names(pdf, page, cat)
                for name, val in incoming.items():
                    if cat == "XObject" and val.strip().endswith(" R"):
                        if known is None:
                            known = _image_index(pdf, first_new_xref)
                        fp = _fingerprint(pdf, int(val.split()[0]))
                        if fp is not None and fp in known:
                            val = f"{known[fp]} 0 R"
                    if name in existing:
                        if existing[name].strip() == val.strip():
                            continue
                        k = 1
                        while f"{name}_{k}" in existing:
                            k += 1
                        new_name = f"{name}_{k}"
                        renames[(cat, name)] = new_name
                        existing[new_name] = val
                        set_resource(pdf, page, cat, new_name, val)
                    else:
                        existing[name] = val
                        set_resource(pdf, page, cat, name, val)
        finally:
            pdf.delete_page(tmp_index)
        page = pdf[page_index]
        parsed = parse_page(pdf, page)
        T = pymupdf.Matrix(1, 0, 0, 1, dx, dy)
        to_tgt = sparsed.pdf_to_page * T * ~parsed.pdf_to_page
        units = [unit_bytes(sparsed, o, o.ctm * to_tgt, parsed.end_ctm, include_state=True) for o in sparsed.objects]
        body = _rename_ops(b"\n".join(units), renames)
        data, _ = apply_edits(parsed.data, [(len(parsed.data), len(parsed.data), _sep(body))])
        write_content(pdf, page, data)
        n0 = len(parsed.objects)
        return list(range(n0, n0 + len(sparsed.objects)))
    finally:
        src.close()


def _fingerprint(pdf: pymupdf.Document, xref: int, depth: int = 0) -> str | None:
    """Impronta di un image XObject (dati compressi, dizionario e oggetti collegati)."""
    import hashlib
    try:
        if pdf.xref_get_key(xref, "Subtype")[1] != "/Image":
            return None
        h = hashlib.sha1()
        h.update(pdf.xref_stream_raw(xref) or b"")
        for key in ("Width", "Height", "BitsPerComponent", "Filter", "DecodeParms", "Decode", "ImageMask",
                    "ColorSpace", "SMask", "Mask", "Intent", "Interpolate"):
            typ, val = pdf.xref_get_key(xref, key)
            if typ == "xref" and depth < 3:
                ref = int(val.split()[0])
                sub = _fingerprint(pdf, ref, depth + 1) if key in ("SMask", "Mask") else None
                if sub is None:
                    body = pdf.xref_object(ref, compressed=True)
                    if pdf.xref_is_stream(ref):
                        body += hashlib.sha1(pdf.xref_stream_raw(ref) or b"").hexdigest()
                    sub = re.sub(r"\d+ 0 R", "R", body)
                val = sub
            h.update(f"{key}={val};".encode())
        return h.hexdigest()
    except Exception:
        return None


def _image_index(pdf: pymupdf.Document, limit: int) -> dict:
    out = {}
    for x in range(1, min(limit, pdf.xref_length())):
        try:
            if pdf.xref_get_key(x, "Subtype")[1] != "/Image":
                continue
        except Exception:
            continue
        fp = _fingerprint(pdf, x)
        if fp is not None and fp not in out:
            out[fp] = x
    return out


def payload_png(payload: Payload, dpi: int = 150) -> bytes:
    src = pymupdf.open(stream=payload.pdf, filetype="pdf")
    try:
        page = src[0]
        clip = payload.rect
        if clip.is_empty:
            clip = page.rect
        z = dpi / 72.0
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=True, annots=False)
        return pix.tobytes("png")
    finally:
        src.close()


def payload_pdf(payload: Payload) -> bytes:
    """PDF per le altre applicazioni: pagina ritagliata sulla selezione."""
    src = pymupdf.open(stream=payload.pdf, filetype="pdf")
    try:
        page = src[0]
        r = payload.rect & page.rect
        if not r.is_empty:
            page.set_cropbox(r)
        return src.tobytes(garbage=3, deflate=True)
    except Exception:
        return payload.pdf
    finally:
        src.close()
