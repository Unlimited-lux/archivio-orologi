"""Conversione di testo formattato (RTF, HTML, testo semplice) in paragrafi di run (TXT-19)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, Run, Style, merge_runs

MAX_SIZE_BYTES = 1024 * 1024


@dataclass
class Imported:
    paragraphs: list[list[Run]]
    align: str = ALIGN_LEFT
    omitted: list[str] = field(default_factory=list)   # elementi omessi (immagini, oggetti)

    @property
    def text(self) -> str:
        return "\n".join("".join(r.text for r in p) for p in self.paragraphs)


# ---------------------------------------------------------------- testo semplice
def decode_text(data: bytes) -> str:
    """UTF-8/UTF-16 (con BOM) e, in mancanza, codifica a 8 bit Windows-1252."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def from_plain(text: str, style: Style) -> Imported:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return Imported([[Run(line, style)] if line else [] for line in text.split("\n")])


# ---------------------------------------------------------------- RTF
_RTF_TOKEN = re.compile(rb"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+")
_SKIP_DEST = {b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict", b"object", b"header", b"footer",
              b"headerl", b"headerr", b"footerl", b"footerr", b"listtable", b"listoverridetable", b"generator",
              b"themedata", b"colorschememapping", b"latentstyles", b"datastore", b"xmlnstbl", b"rsidtbl",
              b"filetbl", b"pgdsctbl", b"field", b"fldinst"}


def from_rtf(data: bytes, base: Style) -> Imported:
    """Parser RTF essenziale: font, colori, dimensione, grassetto, corsivo, sottolineato, barrato,
    apice/pedice, allineamento, paragrafi, sequenze Unicode; immagini e oggetti omessi."""
    fonts: dict[int, str] = {}
    colors: list[tuple] = []
    paragraphs: list[list[Run]] = [[]]
    omitted: list[str] = []
    align = None
    codepage = "cp1252"
    state = {"b": False, "i": False, "ul": False, "strike": False, "fs": None, "cf": 0, "f": None, "super": 0,
             "uc": 1, "skip": 0}
    stack: list[dict] = []
    dest: list[bytes | None] = [None]
    pending_skip = 0
    font_num = None
    font_name = b""
    color_parts: list[int | None] = [None, None, None]
    in_group_star = False

    def style() -> Style:
        size = state["fs"] / 2 if state["fs"] else base.size
        rise = 0.0
        if state["super"]:
            rise = size * 0.33 * state["super"]
            size *= 0.65
        color = base.color
        if 0 < state["cf"] < len(colors) and colors[state["cf"]] is not None:
            color = colors[state["cf"]]
        font = fonts.get(state["f"], base.font) if state["f"] is not None else base.font
        return base.with_(font=font, size=round(size, 2), color=color, bold=state["b"], italic=state["i"],
                          underline=state["ul"], strike=state["strike"], rise=rise, font_xref=0)

    def emit(text: str) -> None:
        if text:
            paragraphs[-1].append(Run(text, style()))

    for m in _RTF_TOKEN.finditer(data):
        word, num, hexbyte, sym, brace, text = m.groups()
        current = dest[-1]
        if brace == b"{":
            stack.append(dict(state))
            dest.append(current)
            continue
        if brace == b"}":
            if current == b"fonttbl" and font_num is not None and font_name:
                fonts[font_num] = font_name.decode("latin-1").strip().rstrip(";").strip()
                font_num, font_name = None, b""
            if stack:
                state.update(stack.pop())
            closed = dest.pop() if len(dest) > 1 else None
            if closed in (b"pict", b"object") and dest[-1] not in (b"pict", b"object"):
                omitted.append("immagine" if closed == b"pict" else "oggetto")
            in_group_star = False
            continue
        if sym is not None:
            if sym == b"*":
                in_group_star = True
                dest[-1] = b"*"
            elif sym in (b"\\", b"{", b"}") and current is None:
                emit(sym.decode())
            elif sym == b"~" and current is None:
                emit("\u00a0")
            elif sym == b"-" and current is None:
                emit("\u00ad")
            elif sym == b"_" and current is None:
                emit("\u2011")
            continue
        if hexbyte is not None:
            if pending_skip:
                pending_skip -= 1
                continue
            ch = bytes([int(hexbyte, 16)]).decode(codepage, errors="replace")
            if current is None:
                emit(ch)
            elif current == b"fonttbl":
                font_name += ch.encode("latin-1", errors="replace")
            continue
        if word is not None:
            n = int(num) if num is not None else None
            if current == b"fonttbl":
                if word == b"f":
                    font_num, font_name = n, b""
                continue
            if current == b"colortbl":
                if word in (b"red", b"green", b"blue"):
                    color_parts[(b"red", b"green", b"blue").index(word)] = n
                continue
            if word in _SKIP_DEST or in_group_star:
                if word in _SKIP_DEST:
                    dest[-1] = word
                continue
            if current is not None:
                continue
            if word == b"ansicpg" and n:
                codepage = f"cp{n}"
            elif word == b"par":
                paragraphs.append([])
            elif word == b"line":
                paragraphs.append([])
            elif word == b"tab":
                emit("\t")
            elif word == b"cell":
                emit("  ")
            elif word == b"row":
                paragraphs.append([])
            elif word == b"b":
                state["b"] = n != 0
            elif word == b"i":
                state["i"] = n != 0
            elif word in (b"ul", b"uld", b"uldb"):
                state["ul"] = n != 0
            elif word == b"ulnone":
                state["ul"] = False
            elif word == b"strike":
                state["strike"] = n != 0
            elif word == b"fs" and n:
                state["fs"] = n
            elif word == b"cf":
                state["cf"] = n or 0
            elif word == b"f":
                state["f"] = n
            elif word == b"super":
                state["super"] = 1
            elif word == b"sub":
                state["super"] = -1
            elif word == b"nosupersub":
                state["super"] = 0
            elif word == b"plain":
                state.update(b=False, i=False, ul=False, strike=False, fs=None, cf=0, super=0)
            elif word == b"uc":
                state["uc"] = n or 0
            elif word == b"u" and n is not None:
                cp = n if n >= 0 else n + 65536
                emit(chr(cp))
                pending_skip = state["uc"]
            elif word in (b"qc", b"qr", b"qj", b"ql") and align is None:
                align = {b"qc": ALIGN_CENTER, b"qr": ALIGN_RIGHT, b"qj": ALIGN_JUSTIFY, b"ql": ALIGN_LEFT}[word]
            continue
        if text is not None:
            if current == b"fonttbl":
                font_name += text
                if text.endswith(b";") and font_num is not None:
                    fonts[font_num] = font_name.decode("latin-1").strip().rstrip(";").strip()
                    font_num, font_name = None, b""
                continue
            if current == b"colortbl":
                for _part in text.split(b";")[:-1]:
                    if all(v is None for v in color_parts):
                        colors.append(None)
                    else:
                        colors.append(tuple((v or 0) / 255.0 for v in color_parts))
                    color_parts = [None, None, None]
                continue
            if current is not None:
                continue
            chunk = text
            if pending_skip:
                drop = min(pending_skip, len(chunk))
                chunk = chunk[drop:]
                pending_skip -= drop
            emit(chunk.decode(codepage, errors="replace"))
    while paragraphs and not paragraphs[-1]:
        paragraphs.pop()
    return Imported([merge_runs(p) for p in paragraphs] or [[]], align or ALIGN_LEFT, omitted)


# ---------------------------------------------------------------- HTML
def from_html(html: str, base: Style) -> Imported:
    """HTML con stili in linea e marcatori di base, tramite il motore di testo di Qt."""
    from PySide6.QtGui import QFont, QTextDocument, QTextFormat

    doc = QTextDocument()
    doc.setDefaultFont(QFont(base.font, max(1, round(base.size))))
    doc.setHtml(html)
    paragraphs: list[list[Run]] = []
    omitted: list[str] = []
    align = None
    block = doc.begin()
    while block.isValid():
        runs: list[Run] = []
        if align is None and block.text().strip():
            a = block.blockFormat().alignment()
            from PySide6.QtCore import Qt
            if a & Qt.AlignmentFlag.AlignHCenter:
                align = ALIGN_CENTER
            elif a & Qt.AlignmentFlag.AlignRight:
                align = ALIGN_RIGHT
            elif a & Qt.AlignmentFlag.AlignJustify:
                align = ALIGN_JUSTIFY
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                if fmt.isImageFormat() or fmt.objectType() != QTextFormat.ObjectTypes.NoObject:
                    omitted.append("immagine")
                else:
                    text = frag.text().replace("\ufffc", "")
                    size = fmt.fontPointSize() or fmt.font().pointSizeF() or base.size
                    fams = fmt.fontFamilies() or []
                    color = base.color
                    if fmt.hasProperty(QTextFormat.Property.ForegroundBrush):
                        c = fmt.foreground().color()
                        color = (round(c.redF(), 4), round(c.greenF(), 4), round(c.blueF(), 4))
                    rise = 0.0
                    va = fmt.verticalAlignment()
                    if va == fmt.VerticalAlignment.AlignSuperScript:
                        rise, size = size * 0.33, size * 0.65
                    elif va == fmt.VerticalAlignment.AlignSubScript:
                        rise, size = -size * 0.2, size * 0.65
                    st = base.with_(font=fams[0] if fams else base.font, size=round(size, 2), color=color,
                                    bold=fmt.fontWeight() >= 600, italic=fmt.fontItalic(),
                                    underline=fmt.fontUnderline(), strike=fmt.fontStrikeOut(), rise=rise,
                                    font_xref=0)
                    if text:
                        runs.append(Run(text.replace("\u2028", " "), st))
            it += 1
        paragraphs.append(merge_runs(runs))
        block = block.next()
    while paragraphs and not paragraphs[-1]:
        paragraphs.pop()
    return Imported(paragraphs or [[]], align or ALIGN_LEFT, omitted)


def from_file(path: str, base: Style) -> Imported:
    data = open(path, "rb").read()
    low = path.lower()
    if low.endswith(".rtf") or data.lstrip().startswith(b"{\\rtf"):
        return from_rtf(data, base)
    if low.endswith((".html", ".htm")):
        return from_html(decode_text(data), base)
    return from_plain(decode_text(data), base)


def from_mime(mime, base: Style) -> Imported | None:
    """Dagli appunti o da un trascinamento (QMimeData): file, RTF, HTML o testo semplice."""
    if mime is None:
        return None
    if mime.hasUrls():
        for url in mime.urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith((".rtf", ".txt", ".html", ".htm")):
                return from_file(url.toLocalFile(), base)
    for fmt in ("text/rtf", "application/rtf"):
        if mime.hasFormat(fmt):
            return from_rtf(bytes(mime.data(fmt)), base)
    if mime.hasHtml():
        return from_html(mime.html(), base)
    if mime.hasText():
        return from_plain(mime.text(), base)
    return None
