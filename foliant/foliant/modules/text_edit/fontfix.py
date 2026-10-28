"""Riuso dei font incorporati come sottoinsieme senza tabella dei caratteri (TXT-02, TXT-10).

Molti PDF (per esempio quelli prodotti dagli elaboratori di testi) incorporano
i font TrueType come sottoinsieme con codifica Identity-H: il programma del font
non ha una tabella `cmap`, quindi non si può scrivere testo nuovo con esso.
Qui la tabella viene ricostruita dalla /ToUnicode e dalla /CIDToGIDMap del PDF:
i glifi presenti nel sottoinsieme tornano utilizzabili, identici all'originale.
Richiede fontTools (facoltativo: senza, si usa un font sostitutivo).
"""

from __future__ import annotations

import io
import re

import pymupdf

_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


def _hex_to_str(h: bytes) -> str:
    data = bytes.fromhex(h.decode("ascii"))
    try:
        return data.decode("utf-16-be")
    except UnicodeDecodeError:
        return ""


def parse_tounicode(stream: bytes) -> dict[int, str]:
    """Codice -> testo Unicode da un CMap /ToUnicode (bfchar e bfrange)."""
    out: dict[int, str] = {}
    for section in re.findall(rb"beginbfchar(.*?)endbfchar", stream, re.S):
        items = _HEX.findall(section)
        for src, dst in zip(items[0::2], items[1::2]):
            out[int(src, 16)] = _hex_to_str(dst)
    for section in re.findall(rb"beginbfrange(.*?)endbfrange", stream, re.S):
        for line in re.finditer(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", section):
            lo, hi, dst = int(line.group(1), 16), int(line.group(2), 16), line.group(3)
            if hi - lo > 0xFFFF:
                continue
            if dst.startswith(b"["):
                for k, item in enumerate(_HEX.findall(dst)):
                    if lo + k <= hi:
                        out[lo + k] = _hex_to_str(item)
            else:
                base = _hex_to_str(_HEX.findall(dst)[0])
                if len(base) != 1:
                    continue
                for k in range(hi - lo + 1):
                    cp = ord(base) + k
                    if cp <= 0x10FFFF:
                        out[lo + k] = chr(cp)
    return out


def _cid_to_gid(pdf: pymupdf.Document, descendant: int) -> dict[int, int] | None:
    kind, val = pdf.xref_get_key(descendant, "CIDToGIDMap")
    if kind in ("null", "name") and (kind == "null" or val == "/Identity"):
        return None   # identità
    if kind == "xref":
        data = pdf.xref_stream(int(val.split()[0])) or b""
        return {i // 2: (data[i] << 8) | data[i + 1] for i in range(0, len(data) - 1, 2)}
    return None


def repaired_font(pdf: pymupdf.Document, xref: int) -> tuple[bytes, set[str]] | None:
    """(programma del font con `cmap`, caratteri realmente disponibili) oppure None."""
    try:
        from fontTools.ttLib import TTFont, newTable
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
    except ImportError:
        return None
    try:
        if pdf.xref_get_key(xref, "Subtype")[1] != "/Type0":
            return None
        if pdf.xref_get_key(xref, "Encoding")[1] != "/Identity-H":
            return None
        tu = pdf.xref_get_key(xref, "ToUnicode")
        if tu[0] != "xref":
            return None
        mapping = parse_tounicode(pdf.xref_stream(int(tu[1].split()[0])) or b"")
        desc = pdf.xref_get_key(xref, "DescendantFonts")
        m = re.search(r"(\d+)\s+0\s+R", desc[1] or "")
        if not m:
            return None
        descendant = int(m.group(1))
        cid2gid = _cid_to_gid(pdf, descendant)
        _name, ext, _typ, buf = pdf.extract_font(xref)
        if ext != "ttf" or not buf:
            return None
        font = TTFont(io.BytesIO(buf))
        if "glyf" not in font:
            return None
        order = font.getGlyphOrder()
        glyf = font["glyf"]
        hmtx = font["hmtx"]
        uni2name: dict[int, str] = {}
        for cid, text in mapping.items():
            if len(text) != 1:
                continue
            gid = cid2gid.get(cid, 0) if cid2gid is not None else cid
            if gid <= 0 or gid >= len(order):
                continue
            gname = order[gid]
            g = glyf[gname]
            has_outline = g.isComposite() or getattr(g, "numberOfContours", 0) > 0
            if not has_outline and not (text.isspace() and hmtx[gname][0] > 0):
                continue   # glifo eliminato dal sottoinsieme
            uni2name.setdefault(ord(text), gname)
        if not uni2name:
            return None
        cmap = newTable("cmap")
        cmap.tableVersion = 0
        subtables = []
        bmp = {u: n for u, n in uni2name.items() if u <= 0xFFFF}
        st4 = CmapSubtable.newSubtable(4)
        st4.platformID, st4.platEncID, st4.language = 3, 1, 0
        st4.cmap = bmp
        subtables.append(st4)
        if len(bmp) != len(uni2name):
            st12 = CmapSubtable.newSubtable(12)
            st12.platformID, st12.platEncID, st12.language = 3, 10, 0
            st12.cmap = dict(uni2name)
            subtables.append(st12)
        cmap.tables = subtables
        font["cmap"] = cmap
        if "post" not in font:
            post = newTable("post")
            post.formatType = 3.0
            post.italicAngle = 0
            post.underlinePosition = -100
            post.underlineThickness = 50
            post.isFixedPitch = 0
            post.minMemType42 = post.maxMemType42 = post.minMemType1 = post.maxMemType1 = 0
            font["post"] = post
        out = io.BytesIO()
        font.save(out)
        return out.getvalue(), {chr(u) for u in uni2name}
    except Exception:
        return None
