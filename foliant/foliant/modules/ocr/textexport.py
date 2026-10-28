"""Esportazione del testo riconosciuto (OCR-07): TXT, ALTO XML v4 e hOCR.

Logica pura, senza interfaccia::

    page_text(res, paragraphs=False, dehyphenate=False) -> str
    result_text(result, separator="line", paragraphs=False, dehyphenate=False, newline="\\n") -> str
    save_txt(path, text)
    to_alto(result, source_name="") -> str          save_alto(path, result, source_name="")
    to_hocr(result, source_name="") -> str          save_hocr(path, result, source_name="")

Le coordinate di ALTO e hOCR sono in pixel dell'immagine elaborata (la pagina
resa per l'OCR); l'affidabilità per parola è 0–1 in ALTO (``WC``) e 0–100 in
hOCR (``x_wconf``). I file vengono scritti in UTF-8 senza BOM e in modo atomico.
"""

from __future__ import annotations

import os
import unicodedata
import xml.etree.ElementTree as ET
from typing import Iterable

from foliant.i18n import _

from .engine import OcrResult, PageResult
from .tess import Line, Word

SEPARATOR_LINE = "line"
SEPARATOR_FORMFEED = "formfeed"
SEPARATORS = (SEPARATOR_LINE, SEPARATOR_FORMFEED)

ALTO_NS = "http://www.loc.gov/standards/alto/ns-v4#"
ALTO_SCHEMA = "http://www.loc.gov/standards/alto/v4/alto-4-2.xsd"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
XHTML_NS = "http://www.w3.org/1999/xhtml"

HYPHENS = ("-", "­", "¬")    # trattino, trattino morbido, «¬»


# ===================================================================== testo
def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _paragraphs(lines: Iterable[Line]) -> list[list[str]]:
    """Righe raggruppate per paragrafo (cambio di (blocco, paragrafo))."""
    out: list[list[str]] = []
    prev = None
    for ln in lines:
        text = _nfc(ln.text).strip()
        if not text:
            continue
        key = (ln.block, ln.par)
        if prev is None or key != prev:
            out.append([])
        out[-1].append(text)
        prev = key
    return out


def _hyphenated(line: str, nxt: str) -> bool:
    """Vero se `line` termina con lettera + trattino e `nxt` inizia con minuscola."""
    if len(line) < 2 or line[-1] not in HYPHENS or not line[-2].isalpha():
        return False
    return bool(nxt) and nxt[0].islower()


def _dehyphenate(lines: list[str]) -> list[str]:
    lines = list(lines)
    i = 0
    while i < len(lines) - 1:
        if _hyphenated(lines[i], lines[i + 1]):
            first, _sep, rest = lines[i + 1].partition(" ")
            lines[i] = lines[i][:-1] + first
            rest = rest.strip()
            if rest:
                lines[i + 1] = rest
                i += 1
            else:
                del lines[i + 1]      # la riga successiva era solo la fine della parola
        else:
            i += 1
    return lines


def _join_paragraph(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        if not out:
            out = ln
        elif out[-1] in HYPHENS and len(out) > 1 and out[-2].isalpha():
            out += ln                 # trattino conservato (es. «Socio-Economico»)
        else:
            out += " " + ln
    return out


def page_text(res: PageResult, paragraphs: bool = False, dehyphenate: bool = False) -> str:
    """Testo di una pagina: righe nell'ordine, riga vuota fra i paragrafi.

    Pagine saltate → testo estraibile esistente; pagine non riconosciute → "".
    """
    if res.status == "skipped":
        return _nfc((res.existing_text or "").replace("\r\n", "\n").replace("\r", "\n")).rstrip()
    if not res.ok:
        return ""
    blocks: list[str] = []
    for par in _paragraphs(res.lines):
        if dehyphenate:
            par = _dehyphenate(par)
        blocks.append(_join_paragraph(par) if paragraphs else "\n".join(par))
    return _nfc("\n\n".join(blocks))


def page_header(n: int) -> str:
    return _("— Pagina {n} —").format(n=n)


def result_text(result: OcrResult, separator: str = SEPARATOR_LINE, paragraphs: bool = False,
                dehyphenate: bool = False, newline: str = "\n") -> str:
    """Testo di tutto il risultato, pagine nell'ordine, con il separatore scelto.

    ``separator="line"``: una riga «— Pagina N —» prima del testo di ogni pagina;
    ``separator="formfeed"``: pagine unite da un solo U+000C. ``newline`` ("\\n"
    o "\\r\\n") si applica a ogni fine riga.
    """
    if separator not in SEPARATORS:
        raise ValueError(f"separatore sconosciuto: {separator}")
    if newline not in ("\n", "\r\n"):
        raise ValueError(f"fine riga non valido: {newline!r}")
    texts = []
    for res in result.pages:
        # un U+000C dentro il testo esistente falserebbe la divisione in pagine
        texts.append(page_text(res, paragraphs, dehyphenate).replace("\f", "\n"))
    if separator == SEPARATOR_FORMFEED:
        text = "\f".join(texts)
    else:
        parts = []
        for res, t in zip(result.pages, texts):
            header = page_header(res.index + 1)
            parts.append(f"{header}\n{t}" if t else header)
        text = "\n\n".join(parts)
    if text:
        text += "\n"
    text = _nfc(text)
    if newline != "\n":
        text = text.replace("\n", newline)
    return text


def _write_text(path, text: str) -> None:
    from foliant.core.document import atomic_write
    atomic_write(os.fspath(path), text.encode("utf-8"))


def save_txt(path, text: str) -> None:
    """Salva `text` in UTF-8 senza BOM, in modo atomico."""
    _write_text(path, text)


# ===================================================================== geometria
def _num(v: float) -> str:
    v = round(float(v), 2)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _union(boxes: Iterable[tuple]) -> tuple[float, float, float, float] | None:
    boxes = list(boxes)
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _page_size(res: PageResult) -> tuple[float, float]:
    w, h = res.size if res.size else (0, 0)
    if w and h:
        return float(w), float(h)
    u = _union([ln.bbox for ln in res.lines] + [wd.bbox for wd in res.words])
    return (u[2], u[3]) if u else (0.0, 0.0)


def _blocks(lines: list[Line]) -> list[list[list[Line]]]:
    """Righe raggruppate in blocchi (consecutivi) e paragrafi."""
    blocks: list[list[list[Line]]] = []
    prev_block = prev_par = None
    for ln in lines:
        if not ln.words:
            continue
        if prev_block is None or ln.block != prev_block:
            blocks.append([[]])
        elif ln.par != prev_par:
            blocks[-1].append([])
        blocks[-1][-1].append(ln)
        prev_block, prev_par = ln.block, ln.par
    return blocks


def _wc(word: Word) -> float:
    return max(0.0, min(1.0, float(word.conf) / 100.0))


def _engine_version() -> str | None:
    try:
        from .tess import version
        return version()
    except Exception:
        return None


def _hpos_attrs(box) -> dict[str, str]:
    x0, y0, x1, y1 = box
    return {"HPOS": _num(x0), "VPOS": _num(y0), "WIDTH": _num(max(0.0, x1 - x0)), "HEIGHT": _num(max(0.0, y1 - y0))}


def _xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ===================================================================== ALTO
def to_alto(result: OcrResult, source_name: str = "") -> str:
    """ALTO XML v4 delle pagine riconosciute (coordinate in pixel della pagina resa)."""
    ET.register_namespace("", ALTO_NS)
    ET.register_namespace("xsi", XSI_NS)

    def q(tag: str) -> str:
        return f"{{{ALTO_NS}}}{tag}"

    root = ET.Element(q("alto"), {f"{{{XSI_NS}}}schemaLocation": f"{ALTO_NS} {ALTO_SCHEMA}"})
    desc = ET.SubElement(root, q("Description"))
    ET.SubElement(desc, q("MeasurementUnit")).text = "pixel"
    src = ET.SubElement(desc, q("sourceImageInformation"))
    ET.SubElement(src, q("fileName")).text = source_name or ""
    proc = ET.SubElement(desc, q("OCRProcessing"), {"ID": "ocr_0"})
    step = ET.SubElement(proc, q("ocrProcessingStep"))
    langs = getattr(getattr(result, "options", None), "languages", None)
    if langs:
        ET.SubElement(step, q("processingStepSettings")).text = "languages=" + "+".join(langs)
    sw = ET.SubElement(step, q("processingSoftware"))
    ET.SubElement(sw, q("softwareName")).text = "Foliant"
    ver = _engine_version()
    if ver:
        ET.SubElement(sw, q("softwareVersion")).text = ver

    layout = ET.SubElement(root, q("Layout"))
    for res in result.recognized:
        n = res.index + 1
        w, h = _page_size(res)
        page = ET.SubElement(layout, q("Page"), {
            "ID": f"page_{n}", "PHYSICAL_IMG_NR": str(n), "WIDTH": _num(w), "HEIGHT": _num(h)})
        conf = [_wc(wd) for wd in res.words]
        if conf:
            page.set("PC", f"{sum(conf) / len(conf):.2f}")
        space = ET.SubElement(page, q("PrintSpace"), _hpos_attrs((0, 0, w, h)))
        cb = cl = cw = 0
        for block in _blocks(res.lines):
            cb += 1
            blines = [ln for par in block for ln in par]
            tb = ET.SubElement(space, q("TextBlock"), {"ID": f"p{n}_b{cb}", **_hpos_attrs(_union(ln.bbox for ln in blines))})
            for ln in blines:
                cl += 1
                attrs = {"ID": f"p{n}_l{cl}", **_hpos_attrs(ln.bbox)}
                attrs["BASELINE"] = _num(ln.baseline_y(ln.bbox[0]))
                tl = ET.SubElement(tb, q("TextLine"), attrs)
                prev: Word | None = None
                for wd in ln.words:
                    if prev is not None:
                        gap0, gap1 = prev.bbox[2], wd.bbox[0]
                        sp = {"HPOS": _num(gap0), "VPOS": _num(ln.bbox[1]), "WIDTH": _num(max(0.0, gap1 - gap0))}
                        ET.SubElement(tl, q("SP"), sp)
                    cw += 1
                    ET.SubElement(tl, q("String"), {
                        "ID": f"p{n}_w{cw}", "CONTENT": _nfc(wd.text), **_hpos_attrs(wd.bbox),
                        "WC": f"{_wc(wd):.2f}"})
                    prev = wd
    return _xml(root)


def save_alto(path, result: OcrResult, source_name: str = "") -> None:
    _write_text(path, to_alto(result, source_name))


# ===================================================================== hOCR
def _bbox(box) -> str:
    return "bbox " + " ".join(str(int(round(v))) for v in box)


def to_hocr(result: OcrResult, source_name: str = "") -> str:
    """hOCR (XHTML) delle pagine riconosciute (coordinate in pixel della pagina resa)."""
    html = ET.Element("html", {"xmlns": XHTML_NS})
    head = ET.SubElement(html, "head")
    ET.SubElement(head, "title").text = source_name or "OCR"
    ET.SubElement(head, "meta", {"http-equiv": "Content-Type", "content": "text/html; charset=utf-8"})
    ver = _engine_version()
    ET.SubElement(head, "meta", {"name": "ocr-system", "content": "Foliant" + (f" ({ver})" if ver else "")})
    ET.SubElement(head, "meta", {"name": "ocr-capabilities",
                                 "content": "ocr_page ocr_carea ocr_par ocr_line ocrx_word"})
    langs = getattr(getattr(result, "options", None), "languages", None)
    if langs:
        ET.SubElement(head, "meta", {"name": "ocr-langs", "content": " ".join(langs)})
    body = ET.SubElement(html, "body")
    image = (source_name or "").replace('"', "'")
    for res in result.recognized:
        n = res.index + 1
        w, h = _page_size(res)
        page = ET.SubElement(body, "div", {
            "class": "ocr_page", "id": f"page_{n}",
            "title": f'image "{image}"; {_bbox((0, 0, w, h))}; ppageno {res.index}'})
        cb = cp = cl = cw = 0
        for block in _blocks(res.lines):
            cb += 1
            blines = [ln for par in block for ln in par]
            area = ET.SubElement(page, "div", {"class": "ocr_carea", "id": f"block_{n}_{cb}",
                                               "title": _bbox(_union(ln.bbox for ln in blines))})
            for par in block:
                cp += 1
                p = ET.SubElement(area, "p", {"class": "ocr_par", "id": f"par_{n}_{cp}",
                                              "title": _bbox(_union(ln.bbox for ln in par))})
                for ln in par:
                    cl += 1
                    slope, offset = ln.baseline
                    title = f"{_bbox(ln.bbox)}; baseline {round(float(slope), 3):g} {round(float(offset), 2):g}"
                    if ln.x_size:
                        title += f"; x_size {round(float(ln.x_size), 2):g}"
                    span = ET.SubElement(p, "span", {"class": "ocr_line", "id": f"line_{n}_{cl}", "title": title})
                    for i, wd in enumerate(ln.words):
                        cw += 1
                        ws = ET.SubElement(span, "span", {
                            "class": "ocrx_word", "id": f"word_{n}_{cw}",
                            "title": f"{_bbox(wd.bbox)}; x_wconf {int(round(max(0.0, min(100.0, wd.conf))))}"})
                        ws.text = _nfc(wd.text)
                        if i < len(ln.words) - 1:
                            ws.tail = " "
    ET.indent(html, space=" ")
    out = ET.tostring(html, encoding="unicode", method="xml", short_empty_elements=True)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" '
            '"http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">\n' + out + "\n")


def save_hocr(path, result: OcrResult, source_name: str = "") -> None:
    _write_text(path, to_hocr(result, source_name))
