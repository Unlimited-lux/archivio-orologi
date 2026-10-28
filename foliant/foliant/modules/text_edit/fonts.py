"""Scelta dei font per il testo modificato o aggiunto (TXT-10).

Regola deterministica, nell'ordine:
1. il font originale incorporato, se il suo programma contiene i caratteri;
2. per i 14 font standard non incorporati, lo stesso font standard;
3. lo stesso font installato nel sistema (stesso nome, poi stessa famiglia);
4. un font metricamente compatibile scelto in base al nome e al descrittore;
5. un font di ripiego che copra la scrittura del carattere.
Solo i casi 4–5 sono «sostituzioni» da segnalare all'utente.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .analysis import normalize_font_name, strip_subset
from .model import Style

KIND_ORIGINAL = "original"
KIND_BASE14 = "base14"
KIND_SYSTEM = "system"
KIND_SUBSTITUTE = "substitute"

_BASE14 = {
    # prefisso normalizzato -> (regolare, grassetto, corsivo, grassetto corsivo)
    "helvetica": ("helv", "hebo", "heit", "hebi"),
    "arial": ("helv", "hebo", "heit", "hebi"),
    "times": ("tiro", "tibo", "tiit", "tibi"),
    "courier": ("cour", "cobo", "coit", "cobi"),
}
_BASE14_NAMES = {
    "helv": "Helvetica", "hebo": "Helvetica-Bold", "heit": "Helvetica-Oblique", "hebi": "Helvetica-BoldOblique",
    "tiro": "Times-Roman", "tibo": "Times-Bold", "tiit": "Times-Italic", "tibi": "Times-BoldItalic",
    "cour": "Courier", "cobo": "Courier-Bold", "coit": "Courier-Oblique", "cobi": "Courier-BoldOblique",
}

# famiglie metricamente compatibili (prefisso normalizzato -> famiglie candidate)
_COMPATIBLE = [
    ("helvetica", ["Liberation Sans", "Arimo", "DejaVu Sans"]),
    ("arial", ["Liberation Sans", "Arimo", "DejaVu Sans"]),
    ("arimo", ["Liberation Sans", "DejaVu Sans"]),
    ("timesnewroman", ["Liberation Serif", "Tinos", "DejaVu Serif"]),
    ("times", ["Liberation Serif", "Tinos", "DejaVu Serif"]),
    ("tinos", ["Liberation Serif", "DejaVu Serif"]),
    ("couriernew", ["Liberation Mono", "Cousine", "DejaVu Sans Mono"]),
    ("courier", ["Liberation Mono", "Cousine", "DejaVu Sans Mono"]),
    ("calibri", ["Carlito", "Liberation Sans", "DejaVu Sans"]),
    ("cambria", ["Caladea", "Liberation Serif", "DejaVu Serif"]),
    ("georgia", ["Gelasio", "DejaVu Serif", "Liberation Serif"]),
    ("verdana", ["DejaVu Sans", "Liberation Sans"]),
    ("tahoma", ["DejaVu Sans", "Liberation Sans"]),
    ("garamond", ["EB Garamond", "Liberation Serif"]),
    ("glyphless", ["Liberation Sans", "DejaVu Sans"]),
]
_FALLBACK_SANS = ["Liberation Sans", "DejaVu Sans", "Noto Sans", "FreeSans"]
_FALLBACK_SERIF = ["Liberation Serif", "DejaVu Serif", "Noto Serif", "FreeSerif"]
_FALLBACK_MONO = ["Liberation Mono", "DejaVu Sans Mono", "Noto Sans Mono", "FreeMono"]
_LAST_RESORT = ["DejaVu Sans", "Noto Sans", "FreeSans", "Liberation Sans"]

_STYLE_WORDS = re.compile(r"(bold|italic|oblique|regular|roman|book|medium|semibold|demibold|light|black|"
                          r"heavy|condensed|narrow|mt|ps|psmt|it|bd|bi)$")


@dataclass
class SystemFont:
    file: str
    family: str
    style: str
    psname: str

    @property
    def bold(self) -> bool:
        s = self.style.lower()
        return "bold" in s or "black" in s or "heavy" in s

    @property
    def italic(self) -> bool:
        s = self.style.lower()
        return "italic" in s or "oblique" in s


_USER_MAP: dict[str, str] = {}


def set_user_mappings(mapping: dict[str, str]) -> None:
    """Tabella personale «font originale -> font da usare» (TXT-11), applicata in tutti i documenti."""
    _USER_MAP.clear()
    for k, v in (mapping or {}).items():
        if k and v:
            _USER_MAP[family_of(k)] = v


def user_mapping(name: str) -> str | None:
    return _USER_MAP.get(family_of(name))


_index_lock = threading.Lock()
_index: list[SystemFont] | None = None

_FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
              os.path.expanduser("~/.local/share/fonts")]


def system_fonts() -> list[SystemFont]:
    """Font installati (TrueType/OpenType), in cache."""
    global _index
    with _index_lock:
        if _index is not None:
            return _index
        fonts: list[SystemFont] = []
        try:
            out = subprocess.run(
                ["fc-list", "--format", "%{file}\t%{family[0]}\t%{style[0]}\t%{postscriptname}\n"],
                capture_output=True, text=True, timeout=10).stdout
            for row in out.splitlines():
                parts = row.split("\t")
                if len(parts) == 4 and parts[0].lower().endswith((".ttf", ".otf")):
                    fonts.append(SystemFont(*parts))
        except Exception:
            fonts = []
        if not fonts:
            for d in _FONT_DIRS:
                for path in Path(d).rglob("*") if os.path.isdir(d) else []:
                    if path.suffix.lower() in (".ttf", ".otf"):
                        try:
                            f = pymupdf.Font(fontfile=str(path))
                        except Exception:
                            continue
                        style = ("Bold " if f.is_bold else "") + ("Italic" if f.is_italic else "")
                        fonts.append(SystemFont(str(path), f.name, style.strip() or "Regular", f.name))
        fonts.sort(key=lambda f: f.file)
        _index = fonts
        return fonts


def system_families() -> list[str]:
    return sorted({f.family for f in system_fonts()}, key=str.lower)


def find_system_font(name: str, bold: bool | None = None, italic: bool | None = None) -> SystemFont | None:
    """Font di sistema con lo stesso nome (PostScript o famiglia+stile)."""
    key = normalize_font_name(name)
    if not key:
        return None
    for f in system_fonts():
        if key in (normalize_font_name(f.psname), normalize_font_name(f.family + f.style),
                   normalize_font_name(f.family + " " + f.style)):
            if (bold is None or f.bold == bold) and (italic is None or f.italic == italic):
                return f
    return None


def find_family(family: str, bold: bool, italic: bool) -> tuple[SystemFont | None, bool]:
    """Variante della famiglia; restituisce (font, esatta)."""
    key = normalize_font_name(family)
    candidates = [f for f in system_fonts() if normalize_font_name(f.family) == key]
    if not candidates:
        return None, False
    for f in candidates:
        if f.bold == bold and f.italic == italic:
            return f, True
    for f in candidates:
        if not f.bold and not f.italic:
            return f, False
    return candidates[0], False


def family_of(name: str) -> str:
    """Nome di famiglia normalizzato, senza parole di stile finali."""
    key = normalize_font_name(name)
    prev = None
    while key != prev:
        prev = key
        key = _STYLE_WORDS.sub("", key)
    return key or normalize_font_name(name)


def embedding_allowed(buffer: bytes) -> bool:
    """Legge `fsType` della tabella OS/2: False se l'incorporamento è vietato."""
    try:
        if buffer[:4] == b"ttcf":
            return True
        num = struct.unpack(">H", buffer[4:6])[0]
        for i in range(num):
            rec = buffer[12 + 16 * i: 28 + 16 * i]
            tag, _check, offset, _length = struct.unpack(">4sIII", rec)
            if tag == b"OS/2":
                fs_type = struct.unpack(">H", buffer[offset + 8: offset + 10])[0]
                return (fs_type & 0x000F) != 0x0002
    except Exception:
        return True
    return True


@dataclass(eq=False)
class FontChoice:
    """Font effettivamente usato per scrivere un pezzo di testo."""

    key: str                       # nome della risorsa nella pagina
    font: pymupdf.Font             # per misure e copertura
    kind: str
    name: str                      # nome leggibile
    buffer: bytes | None = None
    file: str | None = None
    base14: str | None = None
    simulate_bold: bool = False
    simulate_italic: bool = False
    _cover: dict = field(default_factory=dict)

    def covers(self, ch: str) -> bool:
        if ch in (" ", "\t", "\n"):
            return True
        hit = self._cover.get(ch)
        if hit is None:
            if self.base14:
                try:
                    ch.encode("cp1252")
                    hit = bool(self.font.has_glyph(ord(ch)))
                except UnicodeEncodeError:
                    hit = False
            else:
                hit = bool(self.font.has_glyph(ord(ch)))
            self._cover[ch] = hit
        return hit

    @property
    def has_space(self) -> bool:
        return self.base14 is not None or bool(self.font.has_glyph(32))

    def text_length(self, text: str, size: float) -> float:
        return self.font.text_length(text, fontsize=size)

    def data(self) -> bytes:
        """Programma del font (per mostrarlo nell'editor a schermo)."""
        if self.buffer is not None:
            return self.buffer
        if self.file:
            return Path(self.file).read_bytes()
        return bytes(self.font.buffer)


@dataclass
class Substitution:
    original: str
    substitute: str
    chars: int = 0
    text: str = ""


class FontResolver:
    """Risolve gli stili in font per un documento. Da usare tenendo ENGINE_LOCK."""

    def __init__(self, pdf: pymupdf.Document):
        self.pdf = pdf
        self._originals: dict[int, FontChoice | None] = {}
        self._files: dict[tuple, FontChoice] = {}
        self._base14: dict[str, FontChoice] = {}
        self._embedded_info: dict[int, tuple] = {}
        self.forbidden: set[str] = set()

    # ------------------------------------------------------------ costruttori
    def _from_file(self, path: str, kind: str, name: str | None = None, sim_bold: bool = False,
                   sim_italic: bool = False) -> FontChoice | None:
        ckey = (path, kind, sim_bold, sim_italic)
        cached = self._files.get(ckey)
        if cached is not None:
            return cached
        base = self._files.get((path, None, False, False))
        if base is None:
            try:
                data = Path(path).read_bytes()
                if not embedding_allowed(data):
                    self.forbidden.add(name or path)
                    return None
                font = pymupdf.Font(fontbuffer=data)
            except Exception:
                return None
            key = "FTe%08x" % zlib.crc32(path.encode("utf-8", "replace"))
            base = FontChoice(key, font, KIND_SYSTEM, name or font.name, file=path)
            self._files[(path, None, False, False)] = base
        choice = FontChoice(base.key, base.font, kind, name or base.name, file=path,
                            simulate_bold=sim_bold, simulate_italic=sim_italic, _cover=base._cover)
        self._files[ckey] = choice
        return choice

    def _base14_choice(self, alias: str) -> FontChoice:
        if alias not in self._base14:
            self._base14[alias] = FontChoice(alias, pymupdf.Font(alias), KIND_BASE14, _BASE14_NAMES[alias],
                                             base14=alias)
        return self._base14[alias]

    def font_info(self, xref: int) -> tuple:
        """(ext, tipo, basefont) del font `xref` del documento."""
        if xref not in self._embedded_info:
            info = ("n/a", "", "")
            try:
                name, ext, typ, _buf = self.pdf.extract_font(xref, info_only=True)
                info = (ext, typ, name)
            except Exception:
                pass
            self._embedded_info[xref] = info
        return self._embedded_info[xref]

    def is_embedded(self, xref: int) -> bool:
        return bool(xref) and self.font_info(xref)[0] not in ("n/a", "")

    def original(self, xref: int, sample: str = "") -> FontChoice | None:
        """Font originale incorporato, se utilizzabile per scrivere (copre `sample`)."""
        if not xref:
            return None
        if xref not in self._originals:
            choice = None
            try:
                name, ext, _typ, buf = self.pdf.extract_font(xref)
                if buf and ext not in ("n/a", "pfa", "pfb"):
                    font = pymupdf.Font(fontbuffer=buf)
                    if font.glyph_count > 0:
                        choice = FontChoice("FTo%d" % xref, font, KIND_ORIGINAL, strip_subset(name), buffer=buf)
            except Exception:
                choice = None
            self._originals[xref] = choice
        choice = self._originals[xref]
        if choice is not None and sample and not all(choice.covers(c) for c in sample if not c.isspace()):
            # la mappa dei caratteri del programma non corrisponde al testo: inutilizzabile
            self._originals[xref] = None
            return None
        return choice

    # ------------------------------------------------------------ scelta
    def _variant_from_families(self, families: list[str], bold: bool, italic: bool,
                               kind: str) -> FontChoice | None:
        for fam in families:
            sf, exact = find_family(fam, bold, italic)
            if sf is None:
                continue
            choice = self._from_file(sf.file, kind, f"{sf.family} {sf.style}".strip(),
                                     sim_bold=bold and not sf.bold, sim_italic=italic and not sf.italic)
            if choice is None:
                continue
            return choice
        return None

    def _base14_alias(self, style: Style) -> str | None:
        key = normalize_font_name(style.font)
        for prefix, aliases in _BASE14.items():
            if key.startswith(prefix):
                return aliases[(1 if style.bold else 0) + (2 if style.italic else 0)]
        return None

    def primary(self, style: Style, sample: str = "") -> FontChoice:
        """Font principale per lo stile (prima della verifica dei singoli caratteri)."""
        orig_name = ""
        if style.font_xref:
            orig_name = strip_subset(self.font_info(style.font_xref)[2])
        same_original = bool(style.font_xref) and (
            not orig_name or family_of(orig_name) == family_of(style.font))
        if same_original and self.is_embedded(style.font_xref):
            choice = self.original(style.font_xref, sample)
            if choice is not None and bool(choice.font.is_bold) == style.bold and \
                    bool(choice.font.is_italic) == style.italic:
                return choice
        alias = self._base14_alias(style)
        if alias and not (style.font_xref and self.is_embedded(style.font_xref)):
            return self._base14_choice(alias)
        sf = find_system_font(style.font, style.bold, style.italic)
        fam = family_of(style.font)
        if sf is None:
            for f in system_fonts():
                if normalize_font_name(f.family) == fam:
                    choice = self._variant_from_families([f.family], style.bold, style.italic, KIND_SYSTEM)
                    if choice is not None:
                        return choice
            sf = find_system_font(style.font)
        if sf is not None:
            choice = self._from_file(sf.file, KIND_SYSTEM, f"{sf.family} {sf.style}".strip(),
                                     sim_bold=style.bold and not sf.bold, sim_italic=style.italic and not sf.italic)
            if choice is not None:
                return choice
        mapped = user_mapping(style.font)
        if mapped:
            choice = self._variant_from_families([mapped], style.bold, style.italic, KIND_SYSTEM)
            if choice is None:
                sfm = find_system_font(mapped)
                if sfm is not None:
                    choice = self._from_file(sfm.file, KIND_SYSTEM, f"{sfm.family} {sfm.style}".strip())
            if choice is not None:
                return choice
        choice = None
        if alias:
            return self._base14_choice(alias)
        for prefix, families in _COMPATIBLE:
            if fam.startswith(prefix):
                choice = self._variant_from_families(families, style.bold, style.italic, KIND_SUBSTITUTE)
                if choice is not None:
                    return choice
        families = _FALLBACK_MONO if style.mono else _FALLBACK_SERIF if style.serif else _FALLBACK_SANS
        choice = self._variant_from_families(families, style.bold, style.italic, KIND_SUBSTITUTE)
        if choice is not None:
            return choice
        return self._base14_choice("tiro" if style.serif else "cour" if style.mono else "helv")

    def fallbacks(self, style: Style) -> list[FontChoice]:
        """Candidati sostitutivi per i caratteri non coperti dal font principale."""
        out: list[FontChoice] = []
        fam = family_of(style.font)
        sf = find_system_font(style.font, style.bold, style.italic) or find_system_font(style.font)
        if sf is not None:
            c = self._from_file(sf.file, KIND_SYSTEM, f"{sf.family} {sf.style}".strip())
            if c is not None:
                out.append(c)
        for prefix, families in _COMPATIBLE:
            if fam.startswith(prefix):
                c = self._variant_from_families(families, style.bold, style.italic, KIND_SUBSTITUTE)
                if c is not None:
                    out.append(c)
                break
        families = _FALLBACK_MONO if style.mono else _FALLBACK_SERIF if style.serif else _FALLBACK_SANS
        c = self._variant_from_families(families, style.bold, style.italic, KIND_SUBSTITUTE)
        if c is not None:
            out.append(c)
        c = self._variant_from_families(_LAST_RESORT, False, False, KIND_SUBSTITUTE)
        if c is not None:
            out.append(c)
        try:
            if "cjk" not in self._base14:
                self._base14["cjk"] = FontChoice("FTcjk", pymupdf.Font("cjk"), KIND_SUBSTITUTE, "Droid Sans Fallback",
                                                 buffer=bytes(pymupdf.Font("cjk").buffer))
            out.append(self._base14["cjk"])
        except Exception:
            pass
        return out

    def split(self, text: str, style: Style) -> list[tuple[str, FontChoice]]:
        """Divide il testo in pezzi, ciascuno con un font che ne copre tutti i caratteri.

        Solleva `UncoveredCharacter` se un carattere non è coperto da alcun font.
        """
        primary = self.primary(style, sample=text)
        pieces: list[tuple[str, FontChoice]] = []
        fallbacks: list[FontChoice] | None = None
        for ch in text:
            choice = primary
            if not primary.covers(ch):
                if fallbacks is None:
                    fallbacks = self.fallbacks(style)
                choice = next((f for f in fallbacks if f.covers(ch)), None)
                if choice is None:
                    raise UncoveredCharacter(ch)
            if pieces and pieces[-1][1] is choice:
                pieces[-1] = (pieces[-1][0] + ch, choice)
            else:
                pieces.append((ch, choice))
        return pieces

    def substitutions(self, text: str, style: Style) -> list[Substitution]:
        out: dict[str, Substitution] = {}
        for piece, choice in self.split(text, style):
            if choice.kind == KIND_SUBSTITUTE:
                n = sum(1 for c in piece if not c.isspace())
                if n:
                    s = out.setdefault(choice.name, Substitution(style.font, choice.name))
                    s.chars += n
                    s.text += piece
        return list(out.values())


class UncoveredCharacter(Exception):
    def __init__(self, ch: str):
        super().__init__(ch)
        self.char = ch
