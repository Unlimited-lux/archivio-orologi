"""Font incorporato (sottoinsieme TrueType) per l'aspetto delle firme digitali
quando il testo contiene caratteri fuori da WinAnsi (es. nomi in cirillico,
greco, polacco, cinese). SIG-09: «nome in alfabeto non latino → scelta
automatica di un font con copertura adeguata».

Si usa un font Type0 / CIDFontType2 con codifica Identity-H e CMap ToUnicode,
così il testo resta estraibile. Nessuna sagomatura (scritture che la
richiedono, come l'arabo, non sono supportate).
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from foliant.core import platform

SYSTEM_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
) + platform.windows_fonts("unicode")


def needs_unicode_font(lines) -> bool:
    for ln in lines:
        try:
            ln.encode("cp1252")
        except UnicodeEncodeError:
            return True
    return False


@lru_cache(maxsize=4)
def _font_sources() -> tuple[tuple[str, bytes], ...]:
    out = []
    for p in SYSTEM_CANDIDATES:
        try:
            out.append((Path(p).stem, Path(p).read_bytes()))
        except OSError:
            continue
    try:
        import pymupdf

        from foliant.core.engine import ENGINE_LOCK
        with ENGINE_LOCK:
            out.append(("DroidSansFallback", bytes(pymupdf.Font("cjk").buffer)))
    except Exception:
        pass
    return tuple(out)


class UnicodeFont:
    """Font TrueType scelto per coprire i caratteri richiesti."""

    def __init__(self, name: str, data: bytes):
        from fontTools.ttLib import TTFont
        self.name = name
        self.data = data
        self.tt = TTFont(io.BytesIO(data), lazy=True)
        self.cmap = self.tt.getBestCmap() or {}
        self.upem = self.tt["head"].unitsPerEm
        self.hmtx = self.tt["hmtx"]
        self.glyph_order = self.tt.getGlyphOrder()
        self._gid = {g: i for i, g in enumerate(self.glyph_order)}
        self.used: dict[int, str] = {}       # gid → carattere

    def covers(self, text: str) -> bool:
        return all(ord(c) in self.cmap or c in "\n\r\t" for c in text)

    def gid(self, ch: str) -> int:
        name = self.cmap.get(ord(ch))
        return self._gid.get(name, 0) if name else 0

    def width(self, text: str, size: float) -> float:
        total = 0
        for ch in text:
            name = self.cmap.get(ord(ch))
            total += self.hmtx[name][0] if name else self.upem // 2
        return total * size / self.upem

    def encode(self, text: str) -> bytes:
        """Stringa esadecimale <...> di glyph id a 2 byte (Identity-H)."""
        out = []
        for ch in text:
            g = self.gid(ch)
            self.used[g] = ch
            out.append(f"{g:04X}")
        return ("<" + "".join(out) + ">").encode("ascii")

    def subset_bytes(self) -> bytes:
        import logging

        from fontTools import subset
        logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
        opts = subset.Options()
        opts.retain_gids = True
        opts.notdef_outline = True
        opts.name_IDs = ["*"]
        opts.drop_tables += ["DSIG"]
        font = subset.load_font(io.BytesIO(self.data), opts)
        sub = subset.Subsetter(opts)
        sub.populate(glyphs=[self.glyph_order[g] for g in self.used] + [".notdef"])
        sub.subset(font)
        buf = io.BytesIO()
        subset.save_font(font, buf, opts)
        return buf.getvalue()

    def to_unicode_cmap(self) -> bytes:
        entries = sorted(self.used.items())
        lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap",
                 "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
                 "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
                 "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange"]
        for i in range(0, len(entries), 100):
            chunk = entries[i:i + 100]
            lines.append(f"{len(chunk)} beginbfchar")
            for g, ch in chunk:
                u = ch.encode("utf-16-be").hex().upper()
                lines.append(f"<{g:04X}> <{u}>")
            lines.append("endbfchar")
        lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
        return "\n".join(lines).encode("ascii")

    def pdf_font(self, writer):
        """Crea gli oggetti del font nel writer di pyHanko e restituisce il dizionario Type0."""
        from pyhanko.pdf_utils import generic
        N = generic.NameObject
        base = N("/FOLIAN+" + "".join(c for c in self.name if c.isalnum())[:30])
        file_ref = writer.add_object(generic.StreamObject(
            generic.DictionaryObject(), stream_data=self.subset_bytes()))
        head = self.tt["head"]
        hhea = self.tt["hhea"]
        scale = 1000 / self.upem
        descriptor = writer.add_object(generic.DictionaryObject({
            N("/Type"): N("/FontDescriptor"), N("/FontName"): base, N("/Flags"): generic.NumberObject(32),
            N("/FontBBox"): generic.ArrayObject([generic.NumberObject(int(v * scale)) for v in
                                                 (head.xMin, head.yMin, head.xMax, head.yMax)]),
            N("/ItalicAngle"): generic.NumberObject(0),
            N("/Ascent"): generic.NumberObject(int(hhea.ascent * scale)),
            N("/Descent"): generic.NumberObject(int(hhea.descent * scale)),
            N("/CapHeight"): generic.NumberObject(int(hhea.ascent * scale)),
            N("/StemV"): generic.NumberObject(80), N("/FontFile2"): file_ref,
        }))
        widths = generic.ArrayObject()
        for g in sorted(self.used):
            name = self.glyph_order[g]
            widths.append(generic.NumberObject(g))
            widths.append(generic.ArrayObject([generic.NumberObject(int(self.hmtx[name][0] * scale))]))
        cid = writer.add_object(generic.DictionaryObject({
            N("/Type"): N("/Font"), N("/Subtype"): N("/CIDFontType2"), N("/BaseFont"): base,
            N("/CIDSystemInfo"): generic.DictionaryObject({
                N("/Registry"): generic.TextStringObject("Adobe"),
                N("/Ordering"): generic.TextStringObject("Identity"),
                N("/Supplement"): generic.NumberObject(0)}),
            N("/FontDescriptor"): descriptor, N("/W"): widths, N("/CIDToGIDMap"): N("/Identity"),
        }))
        tounicode = writer.add_object(generic.StreamObject(generic.DictionaryObject(),
                                                           stream_data=self.to_unicode_cmap()))
        return generic.DictionaryObject({
            N("/Type"): N("/Font"), N("/Subtype"): N("/Type0"), N("/BaseFont"): base,
            N("/Encoding"): N("/Identity-H"), N("/DescendantFonts"): generic.ArrayObject([cid]),
            N("/ToUnicode"): tounicode,
        })


def font_for(lines) -> UnicodeFont | None:
    """Primo font disponibile che copre tutti i caratteri delle righe."""
    text = "".join(lines)
    for name, data in _font_sources():
        try:
            f = UnicodeFont(name, data)
        except Exception:
            continue
        if f.covers(text):
            return f
    return None
