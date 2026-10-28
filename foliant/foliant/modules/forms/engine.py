"""Logica dei moduli PDF (campi AcroForm), senza interfaccia.

Tutte le funzioni ricevono il documento del motore (`pymupdf.Document`) e
vanno chiamate tenendo ENGINE_LOCK (dentro `Document.change()` per le
modifiche). I campi si identificano con (indice pagina, xref del widget):
gli oggetti pagina non vanno conservati.

Valori «di interscambio» (FDF/XFDF/CSV/JSON): testo per i campi di testo e
gli elenchi a discesa; nome dello stato attivo (es. «Yes») oppure «Off» per
caselle e pulsanti di opzione; lista di testi per gli elenchi a selezione
multipla.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

import pymupdf

from foliant.i18n import _, pgettext

from . import formats as fm
from . import formula as FX
from . import pdfsyntax as ps
from .formats import Format, parse_number, parse_date

TEXT, CHECKBOX, RADIO, COMBO, LIST, BUTTON, SIGNATURE = (
    "text", "checkbox", "radio", "combo", "list", "button", "signature")
FILLABLE = (TEXT, CHECKBOX, RADIO, COMBO, LIST)

_KIND_OF_TYPE = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: TEXT,
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: CHECKBOX,
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: RADIO,
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: COMBO,
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: LIST,
    pymupdf.PDF_WIDGET_TYPE_BUTTON: BUTTON,
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: SIGNATURE,
}
_TYPE_OF_KIND = {v: k for k, v in _KIND_OF_TYPE.items()}

# Font standard dei moduli (nome mostrato -> nome della risorsa nel PDF).
FONTS = {"Helvetica": "Helv", "Times": "TiRo", "Courier": "Cour"}
_FONT_NAMES = {v: k for k, v in FONTS.items()}

ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT = 0, 1, 2

OFF = "Off"


# Bit dei flag di campo (/Ff) e di annotazione (/F) usati qui (numerazione ISO 32000-2).
FF_NO_EXPORT = 1 << 2
FF_NO_TOGGLE_OFF = 1 << 14
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_FILE_SELECT = 1 << 20
FF_NO_SPELL = 1 << 22
FF_DO_NOT_SCROLL = 1 << 23
FF_IN_UNISON = 1 << 25
FF_RICH_TEXT = 1 << 25  # stesso bit di RadiosInUnison: vale per i campi di testo
AF_HIDDEN, AF_PRINT, AF_NOVIEW, AF_LOCKED = 2, 4, 32, 128

# Stili del segno di caselle e opzioni: carattere del font simbolico standard (ZapfDingbats).
CHECK_STYLES = {"check": "4", "circle": "l", "cross": "8", "diamond": "u", "square": "n", "star": "H"}


def check_style_label(style: str) -> str:
    return {"check": _("Spunta"), "circle": _("Cerchio"), "cross": _("Croce"), "diamond": _("Rombo"),
            "square": _("Quadrato"), "star": _("Stella")}.get(style, style)


def _annot_flags(pdf: pymupdf.Document, xref: int) -> int:
    t, v = pdf.xref_get_key(xref, "F")
    try:
        return int(v) if t == "int" else 0
    except ValueError:
        return 0


class FormError(Exception):
    """Errore con messaggio per l'utente."""


def kind_label(kind: str) -> str:
    return {
        TEXT: _("Campo di testo"), CHECKBOX: _("Casella di controllo"), RADIO: _("Pulsante di opzione"),
        COMBO: _("Elenco a discesa"), LIST: pgettext("tipo di campo", "Elenco"), BUTTON: _("Pulsante"),
        SIGNATURE: _("Campo firma"),
    }.get(kind, kind)


# ====================================================================== formati
@dataclass
class Calculation:
    """Calcolo semplice: operazione su un elenco di campi."""

    op: str                     # SUM | AVG | PRD | MIN | MAX
    fields: list[str]

    def script(self) -> str:
        names = ", ".join('"' + n.replace("\\", "\\\\").replace('"', '\\"') + '"' for n in self.fields)
        return f"AFSimple_Calculate(\"{self.op}\", new Array({names}));"

    @classmethod
    def from_script(cls, script: str | None) -> "Calculation | None":
        m = re.search(r"AFSimple_Calculate\(\s*\"(\w+)\"\s*,\s*(?:new\s+Array\s*\((.*?)\)|\"(.*?)\")\s*\)", script or "", re.S)
        if not m:
            return None
        op = m.group(1).upper()
        if m.group(2) is not None:
            names = re.findall(r"\"((?:[^\"\\]|\\.)*)\"", m.group(2))
        else:
            names = [n.strip() for n in (m.group(3) or "").split(",")]
        return cls(op, [n.replace('\\"', '"').replace("\\\\", "\\") for n in names if n])


OPERATIONS = {"SUM": "Somma", "AVG": "Media", "PRD": "Prodotto", "MIN": "Minimo", "MAX": "Massimo"}


_VALIDATE_MARK = "/* foliant-validate "


@dataclass
class Validation:
    """Regole di validazione: intervallo numerico, lunghezza minima, espressione regolare.

    L'intervallo da solo è scritto come chiamata alla funzione predefinita; le
    altre regole come script breve preceduto da un marcatore riconoscibile."""

    minimum: float | None = None
    maximum: float | None = None
    min_length: int = 0
    regex: str = ""
    message: str = ""

    @property
    def empty(self) -> bool:
        return self.minimum is None and self.maximum is None and not self.min_length and not self.regex

    def check(self) -> str | None:
        """Errore nella definizione delle regole (None se corrette)."""
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            return _("Il minimo non può superare il massimo.")
        if self.regex:
            try:
                re.compile(self.regex)
            except re.error as e:
                return _("Espressione regolare non valida alla posizione {p}: {m}.").format(p=(e.pos or 0) + 1, m=e.msg)
        return None

    def _range_call(self) -> str:
        lo = self.minimum if self.minimum is not None else 0
        hi = self.maximum if self.maximum is not None else 0
        return (f"AFRange_Validate({'true' if self.minimum is not None else 'false'}, {_num_js(lo)}, "
                f"{'true' if self.maximum is not None else 'false'}, {_num_js(hi)});")

    def script(self) -> str | None:
        if self.empty:
            return None
        if not self.min_length and not self.regex and not self.message:
            return self._range_call()
        import json
        data = {k: v for k, v in (("min", self.minimum), ("max", self.maximum), ("minlen", self.min_length),
                                  ("regex", self.regex), ("message", self.message)) if v not in (None, "", 0)}
        marker = _VALIDATE_MARK + json.dumps(data, ensure_ascii=False).replace("*/", "*\\/") + " */\n"
        msg = json.dumps(self.message or _("Valore non valido."), ensure_ascii=False)
        conds = []
        if self.minimum is not None:
            conds.append(f"Number(v) < {_num_js(self.minimum)}")
        if self.maximum is not None:
            conds.append(f"Number(v) > {_num_js(self.maximum)}")
        if self.min_length:
            conds.append(f"v.length < {int(self.min_length)}")
        if self.regex:
            conds.append(f"!(new RegExp({json.dumps(self.regex)})).test(v)")
        return (marker + "var v = String(event.value);\n"
                f"if (v !== \"\" && ({' || '.join(conds)})) {{ app.alert({msg}); event.rc = false; }}")

    @classmethod
    def from_script(cls, script: str | None) -> "Validation":
        s = (script or "").strip()
        if s.startswith(_VALIDATE_MARK):
            import json
            end = s.find(" */")
            try:
                data = json.loads(s[len(_VALIDATE_MARK):end].replace("*\\/", "*/"))
                return cls(data.get("min"), data.get("max"), int(data.get("minlen") or 0),
                           data.get("regex") or "", data.get("message") or "")
            except (ValueError, TypeError, AttributeError):
                return cls()
        m = re.fullmatch(r"AFRange_Validate\(\s*(true|false)\s*,\s*([-\d.eE+]+)\s*,\s*(true|false)\s*,\s*([-\d.eE+]+)\s*\)\s*;?", s)
        if not m:
            return cls()
        return cls(float(m.group(2)) if m.group(1) == "true" else None,
                   float(m.group(4)) if m.group(3) == "true" else None)


def _num_js(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(float(v))


# ====================================================================== campi
@dataclass
class FieldInfo:
    page: int
    xref: int                   # widget (annotazione)
    field_xref: int             # oggetto campo (coincide col widget se uniti)
    name: str
    kind: str
    rect: pymupdf.Rect
    value: Any = None           # testo | bool (casella) | stato (opzione) | lista (elenco multiplo)
    export: str | None = None   # stato attivo di caselle e pulsanti di opzione
    options: list[str] = field(default_factory=list)       # testi visualizzati
    exports: list[str] = field(default_factory=list)       # valori di esportazione (stesso ordine)
    readonly: bool = False
    required: bool = False
    multiline: bool = False
    password: bool = False
    comb: bool = False
    multiselect: bool = False
    editable: bool = False      # elenco a discesa con testo libero
    sort: bool = False
    commit_on_change: bool = False
    no_toggle_off: bool = False  # gruppo di opzioni: una scelta sempre presente
    in_unison: bool = False      # opzioni con lo stesso valore attivate insieme
    do_not_scroll: bool = False
    file_select: bool = False
    no_spell: bool = False
    no_export: bool = False
    hidden: bool = False         # widget nascosto (flag Hidden)
    visibility: str = "visible"  # visible | hidden | screen | print
    locked: bool = False
    check_style: str = "check"   # stile del segno di caselle e opzioni
    rotation: int = 0            # rotazione del contenuto (/MK /R)
    caption: str = ""            # didascalia dei pulsanti
    caption_down: str = ""       # didascalia durante la pressione (/MK /AC)
    caption_rollover: str = ""   # didascalia al passaggio del puntatore (/MK /RC)
    layout: str = "text"         # disposizione di icona e didascalia (BUTTON_LAYOUTS)
    icons: tuple = ()            # stati con un'icona: "normal", "rollover", "down"
    action: dict = field(default_factory=dict)  # azione al clic dei pulsanti
    image: bool = False          # campo immagine
    lock: dict = field(default_factory=dict)  # campi firma: {"action": "All|Include|Exclude", "fields": [...]}
    custom_scripts: list = field(default_factory=list)  # azioni personalizzate non eseguite
    maxlen: int = 0
    font: str = "Helvetica"
    fontsize: float = 0.0       # 0 = automatico
    text_color: tuple = (0, 0, 0)
    fill_color: tuple | None = None
    border_color: tuple | None = None
    border_width: float = 0.0
    border_style: str = "S"     # S pieno · D tratteggiato · B rilievo · I incassato · U sottolineato
    align: int = ALIGN_LEFT
    tooltip: str = ""
    export_name: str = ""       # nome per l'esportazione (/TM), facoltativo
    default: Any = None
    fmt: Format = field(default_factory=Format)
    calc: Calculation | None = None
    formula: "FX.Formula | None" = None
    validation: Validation = field(default_factory=Validation)

    @property
    def key(self) -> tuple[int, int]:
        return self.page, self.xref

    @property
    def fillable(self) -> bool:
        return self.kind in FILLABLE and not self.readonly


def _field_xref(pdf: pymupdf.Document, xref: int) -> int:
    """Oggetto campo che contiene nome e valore del widget `xref`."""
    if pdf.xref_get_key(xref, "T")[0] != "null":
        return xref
    t, v = pdf.xref_get_key(xref, "Parent")
    if t == "xref":
        return int(v.split()[0])
    return xref


def _inherited(pdf: pymupdf.Document, xref: int, key: str) -> tuple[str, str]:
    """Chiave ereditabile del campo: cerca nel widget e risale i genitori."""
    seen = set()
    x = xref
    while x and x not in seen:
        seen.add(x)
        t, v = pdf.xref_get_key(x, key)
        if t != "null":
            return t, v
        pt, pv = pdf.xref_get_key(x, "Parent")
        x = int(pv.split()[0]) if pt == "xref" else 0
    return "null", "null"


def _value_of(t: str, v: str) -> Any:
    """Converte il risultato di `xref_get_key` in valore Python (testo, nome, lista)."""
    if t == "null":
        return None
    if t == "name":
        return _decode_name(v.lstrip("/"))
    if t == "string":
        return v
    if t == "array":
        try:
            arr = ps.parse(v)
        except ps.PdfSyntaxError:
            return []
        return [ps.text_of(a) or "" for a in arr]
    if t == "xref":
        return None
    return v


def _option_texts(values) -> list[str]:
    """Testi visualizzati delle voci."""
    return [d for _e, d in _option_pairs(values)]


def _option_pairs(values) -> list[tuple[str, str]]:
    """Voci come (valore di esportazione, testo visualizzato)."""
    out = []
    for v in values or []:
        if isinstance(v, (list, tuple)):
            e = str(v[0]) if v else ""
            out.append((e, str(v[1]) if len(v) > 1 else e))
        else:
            out.append((str(v), str(v)))
    return out


def _read_options(pdf: pymupdf.Document, xref: int) -> list[tuple[str, str]]:
    t, v = _inherited(pdf, xref, "Opt")
    if t == "xref":
        try:
            v = pdf.xref_object(int(v.split()[0]), compressed=True)
            t = "array"
        except Exception:
            return []
    if t != "array":
        return []
    try:
        arr = ps.parse(v)
    except ps.PdfSyntaxError:
        return []
    out = []
    for item in arr:
        if isinstance(item, list):
            e = ps.text_of(item[0]) if item else ""
            out.append((e or "", (ps.text_of(item[1]) if len(item) > 1 else e) or ""))
        else:
            t_ = ps.text_of(item) or ""
            out.append((t_, t_))
    return out


def _color(c) -> tuple | None:
    if not c:
        return None
    c = tuple(float(x) for x in c)
    if len(c) == 1:
        return (c[0], c[0], c[0])
    if len(c) == 4:  # CMYK -> RGB approssimato
        k = c[3]
        return tuple((1 - min(1.0, x + k)) for x in c[:3])
    return c[:3]


def _decode_name(n: str) -> str:
    if "#" not in n:
        return n
    raw = re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), n).encode("latin-1", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _on_state(w: pymupdf.Widget) -> str | None:
    return _decode_name(_raw_on_state(w))


def _raw_on_state(w: pymupdf.Widget) -> str:
    try:
        states = (w.button_states() or {}).get("normal") or []
    except Exception:
        states = []
    for s in states:
        if s != OFF:
            return s
    try:
        on = w.on_state()
    except Exception:
        on = None
    return on if isinstance(on, str) and on != OFF else "Yes"


def _info(pdf: pymupdf.Document, page_index: int, w: pymupdf.Widget) -> FieldInfo:
    kind = _KIND_OF_TYPE.get(w.field_type, TEXT)
    fx = _field_xref(pdf, w.xref)
    flags = w.field_flags or 0
    info = FieldInfo(page=page_index, xref=w.xref, field_xref=fx, name=w.field_name or "", kind=kind,
                     rect=pymupdf.Rect(w.rect))
    info.readonly = bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY)
    info.required = bool(flags & pymupdf.PDF_FIELD_IS_REQUIRED)
    info.no_export = bool(flags & FF_NO_EXPORT)
    info.no_toggle_off = kind == RADIO and bool(flags & FF_NO_TOGGLE_OFF)
    info.in_unison = kind == RADIO and bool(flags & FF_IN_UNISON)
    info.do_not_scroll = kind == TEXT and bool(flags & FF_DO_NOT_SCROLL)
    info.file_select = kind == TEXT and bool(flags & FF_FILE_SELECT)
    info.no_spell = kind in (TEXT, COMBO) and bool(flags & FF_NO_SPELL)
    aflags = _annot_flags(pdf, w.xref)
    info.hidden = bool(aflags & AF_HIDDEN)
    info.locked = bool(aflags & AF_LOCKED)
    if aflags & AF_HIDDEN:
        info.visibility = "hidden"
    elif aflags & AF_NOVIEW:
        info.visibility = "print"
    elif not aflags & AF_PRINT:
        info.visibility = "screen"
    try:
        info.rotation = int(pdf.xref_get_key(w.xref, "MK/R")[1]) % 360 if pdf.xref_get_key(w.xref, "MK/R")[0] == "int" else 0
    except ValueError:
        info.rotation = 0
    if kind == BUTTON:
        info.caption = _value_of(*pdf.xref_get_key(w.xref, "MK/CA")) or ""
        info.caption_down = _value_of(*pdf.xref_get_key(w.xref, "MK/AC")) or ""
        info.action = button_action(pdf, w.xref)
        info.image = is_image_field(pdf, w.xref)
        info.caption_rollover = _value_of(*pdf.xref_get_key(w.xref, "MK/RC")) or ""
        info.layout = button_layout(pdf, w.xref)
        info.icons = tuple(button_icons(pdf, w.xref))
    if kind == SIGNATURE:
        info.lock = _read_lock(pdf, w.xref)
    if kind in (CHECKBOX, RADIO):
        ca = _value_of(*pdf.xref_get_key(w.xref, "MK/CA"))
        info.check_style = next((k for k, c in CHECK_STYLES.items() if c == ca), "check" if kind == CHECKBOX else "circle")
    info.font = _FONT_NAMES.get(w.text_font or "Helv", w.text_font or "Helvetica")
    sys_font = _value_of(*pdf.xref_get_key(w.xref, "FoliantFont"))
    if sys_font:
        info.font = sys_font
    info.fontsize = float(w.text_fontsize or 0)
    info.text_color = _color(w.text_color) or (0, 0, 0)
    info.fill_color = _color(w.fill_color)
    info.border_color = _color(w.border_color)
    info.border_width = float(w.border_width or 0)
    info.border_style = (w.border_style or "S")[:1].upper()
    info.export_name = _value_of(*_inherited(pdf, w.xref, "TM")) or ""
    info.tooltip = w.field_label or ""
    if info.tooltip == "Unnamed":
        info.tooltip = ""
    q = _inherited(pdf, w.xref, "Q")
    try:
        info.align = int(q[1]) if q[0] == "int" else ALIGN_LEFT
    except ValueError:
        info.align = ALIGN_LEFT
    raw_v = _value_of(*_inherited(pdf, w.xref, "V"))
    raw_dv = _value_of(*_inherited(pdf, w.xref, "DV"))
    if kind == TEXT:
        info.value = raw_v if isinstance(raw_v, str) else (w.field_value or "")
        info.default = raw_dv if isinstance(raw_dv, str) else ""
        info.multiline = bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE)
        info.password = bool(flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD)
        info.comb = bool(flags & pymupdf.PDF_TX_FIELD_IS_COMB)
        info.maxlen = int(w.text_maxlen or 0)
        if _has_actions(pdf, w.xref, fx):
            info.fmt = Format.from_script(_action_script(pdf, w.xref, "F") or w.script_format)
            c_js = _action_script(pdf, w.xref, "C") or w.script_calc
            info.calc = Calculation.from_script(c_js)
            info.formula = FX.from_script(c_js) if info.calc is None else None
            info.validation = Validation.from_script(_action_script(pdf, w.xref, "V"))
    elif kind in (CHECKBOX, RADIO):
        info.export = _on_state(w)
        state = _value_of(*pdf.xref_get_key(w.xref, "AS"))
        if kind == CHECKBOX:
            info.value = bool(state) and state != OFF
        else:
            info.value = raw_v if isinstance(raw_v, str) and raw_v else OFF
            if info.value == OFF and state and state != OFF:
                info.value = state
        info.default = raw_dv if isinstance(raw_dv, str) else OFF
    elif kind in (COMBO, LIST):
        pairs = _read_options(pdf, w.xref) or _option_pairs(w.choice_values)
        info.exports = [e for e, _d in pairs]
        info.options = [d for _e, d in pairs]
        info.sort = bool(flags & pymupdf.PDF_CH_FIELD_IS_SORT)
        info.commit_on_change = bool(flags & pymupdf.PDF_CH_FIELD_IS_COMMIT_ON_SEL_CHANGE)
        info.multiselect = bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT)
        info.editable = bool(flags & pymupdf.PDF_CH_FIELD_IS_EDIT)
        if kind == LIST and info.multiselect:
            info.value = raw_v if isinstance(raw_v, list) else ([raw_v] if raw_v else [])
            info.default = raw_dv if isinstance(raw_dv, list) else ([raw_dv] if raw_dv else [])
        else:
            if isinstance(raw_v, list):
                raw_v = raw_v[0] if raw_v else ""
            info.value = raw_v or ""
            info.default = raw_dv if isinstance(raw_dv, str) else ""
    elif kind == BUTTON:
        info.value = w.button_caption or ""
    elif kind == SIGNATURE:
        info.value = "firmato" if _inherited(pdf, w.xref, "V")[0] != "null" else ""
    return info


def _action_script(pdf: pymupdf.Document, xref: int, trigger: str) -> str | None:
    """Testo JavaScript di un'azione aggiuntiva (AA/<trigger>) del campo."""
    for x in (xref, _field_xref(pdf, xref)):
        t, v = pdf.xref_get_key(x, f"AA/{trigger}/JS")
        if t == "string":
            return v
        if t == "xref":
            try:
                return pdf.xref_stream(int(v.split()[0])).decode("latin-1")
            except Exception:
                return None
    return None


def fields_named(pdf: pymupdf.Document, names: set[str], pages: Iterable[int]) -> list[FieldInfo]:
    """Campi con questi nomi nelle pagine indicate (rilettura mirata dopo una modifica)."""
    from .scan import scan_fields
    return scan_fields(pdf, list(pages), names=set(names))


def list_fields(pdf: pymupdf.Document, page: int | None = None) -> list[FieldInfo]:
    """Campi del documento (o di una pagina) nell'ordine di tabulazione."""
    # lettura lineare degli oggetti (scan.py): page.widgets() è quadratico con molti campi
    from .scan import scan_fields
    return scan_fields(pdf, None if page is None else [page])


def find_widget(pdf: pymupdf.Document, page: int, xref: int) -> pymupdf.Widget:
    pg = pdf[page]
    if xref in _page_widget_set(pdf, page) and pdf.xref_get_key(xref, "Subtype")[1] == "/Widget":
        try:
            w = pg.load_widget(xref)
        except Exception:
            w = None
        if w is not None:
            w._foliant_page = pg  # la pagina deve restare viva finché si usa il widget
            return w
    raise FormError(_("Il campo non esiste più nel documento."))


def _page_widget_set(pdf: pymupdf.Document, page: int) -> set[int]:
    """Xref delle annotazioni della pagina (solo l'array /Annots, senza leggere gli oggetti)."""
    t, v = pdf.xref_get_key(pdf.page_xref(page), "Annots")
    if t == "xref":
        v = pdf.xref_object(int(v.split()[0]), compressed=True)
    elif t != "array":
        return set()
    return {int(n) for n in re.findall(r"(\d+)\s+\d+\s+R", v)}


def field_info(pdf: pymupdf.Document, page: int, xref: int) -> FieldInfo:
    from .scan import widget_info
    if xref not in _page_widget_set(pdf, page):
        raise FormError(_("Il campo non esiste più nel documento."))
    info = widget_info(pdf, page, xref)
    if info is None:
        raise FormError(_("Il campo non esiste più nel documento."))
    return info


def has_form(pdf: pymupdf.Document) -> bool:
    try:
        return bool(pdf.is_form_pdf)
    except Exception:
        return False


# ====================================================================== valori
def _truthy(value: Any, export: str | None) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    s = str(value).strip()
    if export and s == export:
        return True
    return s.lower() in ("1", "true", "yes", "on", "si", "sì", "x", "vero")


def set_value(pdf: pymupdf.Document, page: int, xref: int, value: Any) -> None:
    """Imposta il valore memorizzato di un campo (condiviso da tutti i suoi widget)
    e ne rigenera gli aspetti. I campi password non vengono mai scritti nel file."""
    w = find_widget(pdf, page, xref)
    kind = _KIND_OF_TYPE.get(w.field_type, TEXT)
    fx = _field_xref(pdf, w.xref)
    flags = w.field_flags or 0
    if kind == TEXT:
        text = "" if value is None else str(value)
        maxlen = int(w.text_maxlen or 0)
        if maxlen and len(text) > maxlen:
            text = text[:maxlen]
        if flags & pymupdf.PDF_TX_FIELD_IS_PASSWORD:
            text = ""
        pdf.xref_set_key(fx, "V", ps.encode_string(text) if text else "null")
        if flags & FF_RICH_TEXT or _inherited(pdf, w.xref, "RV")[0] != "null":
            # testo formattato: il valore formattato segue quello semplice (stile del campo)
            pdf.xref_set_key(fx, "RV", ps.encode_string(rich_value(text)) if text else "null")
        _regen_field(pdf, fx, page, w.xref)
    elif kind == CHECKBOX:
        on = _on_state(w)
        checked = _truthy(value, on) or (isinstance(value, str) and value not in ("", OFF) and value in _all_states(pdf, fx))
        state = (value if isinstance(value, str) and value in _all_states(pdf, fx) and value != OFF else on) if checked else OFF
        _set_button_states(pdf, _widgets_of_field(pdf, fx) or [w.xref], state, fx)
    elif kind == RADIO:
        state = OFF if value in (None, False, "", OFF) else str(value)
        if value is True:
            state = _on_state(w)
        group = _widgets_of_field(pdf, fx) or [w.xref]
        only = None if flags & FF_IN_UNISON else _first_with_state(pdf, group, state, prefer=w.xref)
        _set_button_states(pdf, group, state, fx, only=only)
    elif kind in (COMBO, LIST):
        multi = kind == LIST and bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT)
        pairs = _read_options(pdf, w.xref) or _option_pairs(w.choice_values)
        exports = [e for e, _d in pairs]
        displays = [d for _e, d in pairs]
        if isinstance(value, (list, tuple)):
            values = [str(v) for v in value]
        elif value in (None, ""):
            values = []
        else:
            values = [str(value)] if not multi else [v.strip() for v in re.split(r"[;\n]", str(value)) if v.strip()]
        if not multi:
            values = values[:1]
        resolved = []
        for v in values:
            if v in exports:
                resolved.append(v)
            elif v in displays:
                resolved.append(exports[displays.index(v)])
            elif kind == COMBO and flags & pymupdf.PDF_CH_FIELD_IS_EDIT:
                resolved.append(v)
            else:
                raise FormError(_("«{v}» non è tra le voci del campo «{n}».").format(v=v, n=w.field_name))
        if not resolved:
            pdf.xref_set_key(fx, "V", "null")
        elif multi and len(resolved) > 1:
            pdf.xref_set_key(fx, "V", ps.serialize(resolved))
        else:
            pdf.xref_set_key(fx, "V", ps.encode_string(resolved[0]))
        duplicates = len(set(exports)) != len(exports)
        if kind == LIST or duplicates:
            idx = sorted({exports.index(v) for v in resolved if v in exports})
            pdf.xref_set_key(fx, "I", ps.serialize(idx) if idx and (multi or duplicates or kind == LIST) else "null")
        _regen_field(pdf, fx, page, w.xref)
    else:
        raise FormError(_("Questo campo non si compila."))


def _all_states(pdf: pymupdf.Document, fx: int) -> set[str]:
    out: set[str] = set()
    for k in _widgets_of_field(pdf, fx):
        t, n = pdf.xref_get_key(k, "AP/N")
        if t == "dict":
            try:
                out.update(ps.parse(n).keys())
            except ps.PdfSyntaxError:
                pass
    return out


def _states_of(pdf: pymupdf.Document, xref: int) -> list[str]:
    t, n = pdf.xref_get_key(xref, "AP/N")
    if t != "dict":
        return []
    try:
        return list(ps.parse(n).keys())
    except ps.PdfSyntaxError:
        return []


def _first_with_state(pdf: pymupdf.Document, group: list[int], state: str, prefer: int) -> int | None:
    if state == OFF:
        return None
    if state in _states_of(pdf, prefer):
        return prefer
    return next((x for x in group if state in _states_of(pdf, x)), None)


def toggle(pdf: pymupdf.Document, page: int, xref: int) -> Any:
    """Clic su una casella o su un pulsante di opzione. Restituisce il nuovo valore."""
    info = field_info(pdf, page, xref)
    if info.kind == CHECKBOX:
        state = _value_of(*pdf.xref_get_key(xref, "AS"))
        new = not (state and state != OFF)
        set_value(pdf, page, xref, info.export if new else OFF)
        return new
    if info.kind == RADIO:
        state = _value_of(*pdf.xref_get_key(xref, "AS"))
        if state and state != OFF:
            if info.no_toggle_off:
                return info.export
            set_value(pdf, page, xref, OFF)
            return OFF
        group = _widgets_of_field(pdf, info.field_xref) or [xref]
        only = None if info.in_unison else xref
        _set_button_states(pdf, group, info.export, info.field_xref, only=only)
        return info.export
    raise FormError(_("Questo campo non si attiva con un clic."))


def radio_siblings(pdf: pymupdf.Document, page: int, xref: int) -> list[tuple[int, int]]:
    """Widget (pagina, xref) dello stesso gruppo di opzioni, nell'ordine dei campi."""
    fx = _field_xref(pdf, xref)
    return [(f.page, f.xref) for f in list_fields(pdf) if f.kind == RADIO and f.field_xref == fx]


def rich_value(text: str) -> str:
    """Valore formattato (/RV, XHTML) equivalente al testo semplice: un paragrafo per riga."""
    from xml.sax.saxutils import escape
    lines = text.replace("\r\n", "\r").replace("\n", "\r").split("\r")
    body = "".join(f"<p>{escape(line)}</p>" for line in lines)
    return ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" xfa:APIVersion="Acroform:2.7.0.0" '
            f'xfa:spec="2.1">{body}</body>')


def regenerate(pdf: pymupdf.Document, page: int, xref: int) -> None:
    """Rigenera l'aspetto del widget dalle chiavi del campo (valore, font, colori…)."""
    w = find_widget(pdf, page, xref)
    annot = getattr(getattr(w, "_annot", None), "this", None)
    if annot is None:
        w.update()
        return
    from pymupdf import mupdf
    mupdf.pdf_annot_request_resynthesis(annot)
    mupdf.pdf_update_annot(annot)


def _page_index_of(pdf: pymupdf.Document, widget_xref: int, hint: int | None = None) -> int | None:
    t, v = pdf.xref_get_key(widget_xref, "P")
    if t != "xref":
        return hint
    pxref = int(v.split()[0])
    if hint is not None and 0 <= hint < pdf.page_count and pdf.page_xref(hint) == pxref:
        return hint
    for i in range(pdf.page_count):
        if pdf.page_xref(i) == pxref:
            return i
    return hint


def _regen_field(pdf: pymupdf.Document, fx: int, page: int, xref: int) -> None:
    """Rigenera tutti i widget del campo (un campo può comparire in più punti).

    Per i campi con formato l'aspetto mostra il valore formattato mentre /V
    resta in forma canonica: si scrive temporaneamente il testo formattato,
    si rigenera e si ripristina il valore."""
    kids = _widgets_of_field(pdf, fx)
    if xref not in kids:
        kids = [xref]
    fmt = Format.from_script(_action_script(pdf, xref, "F"))
    saved_v = pdf.xref_get_key(fx, "V")
    saved_da = pdf.xref_get_key(fx, "DA")
    formatted = None
    if _inherited(pdf, xref, "FT")[1] == "/Ch" and saved_v[0] == "string":
        pairs = _read_options(pdf, xref)
        shown = next((d for e, d in pairs if e == saved_v[1]), None)
        if shown is not None and shown != saved_v[1]:
            formatted = shown
            pdf.xref_set_key(fx, "V", ps.encode_string(shown))
    elif fmt.type != fm.NONE and saved_v[0] == "string" and saved_v[1]:
        text, red = fm.display(fmt, saved_v[1])
        if text != saved_v[1] or red:
            formatted = text
            pdf.xref_set_key(fx, "V", ps.encode_string(text))
            if red and saved_da[0] == "string":
                da = re.sub(r"[\d.]+\s+[\d.]+\s+[\d.]+\s+rg|[\d.]+\s+g\b", "", saved_da[1])
                pdf.xref_set_key(fx, "DA", ps.encode_string("1 0 0 rg " + da.strip()))
    shown = formatted if formatted is not None else (saved_v[1] if saved_v[0] == "string" else "")
    custom_font = pdf.xref_get_key(xref, "FoliantFont")[0] != "null"
    embed = bool(shown) and (needs_embedded_font(shown) or custom_font) and _inherited(pdf, xref, "FT")[1] == "/Tx"
    try:
        for k in kids:
            pi = page if k == xref else _page_index_of(pdf, k, page)
            if pi is None:
                continue
            try:
                if embed:
                    embedded_appearance(pdf, pi, k, shown)
                else:
                    regenerate(pdf, pi, k)
            except FormError:
                continue
    finally:
        if formatted is not None:
            pdf.xref_set_key(fx, "V", ps.encode_string(saved_v[1]))
            if saved_da[0] == "string":
                pdf.xref_set_key(fx, "DA", ps.encode_string(saved_da[1]))


def _widgets_of_field(pdf: pymupdf.Document, field_xref: int) -> list[int]:
    t, v = pdf.xref_get_key(field_xref, "Kids")
    if t != "array":
        return [field_xref]
    try:
        kids = ps.parse(v)
    except ps.PdfSyntaxError:
        return [field_xref]
    return [k[0] for k in kids if isinstance(k, ps.Ref)]


def _set_button_states(pdf: pymupdf.Document, widgets: list[int], state: str, field_xref: int,
                       only: int | None = None) -> None:
    """Attiva lo stato `state` (o Off) sui widget di una casella o di un gruppo di opzioni.
    Con `only` si attiva soltanto quel widget anche se altri hanno lo stesso stato."""
    for x in widgets:
        names = _states_of(pdf, x)
        own = state if (state in names or not names) else OFF
        if only is not None and x != only:
            own = OFF
        pdf.xref_set_key(x, "AS", ps.encode_name(own))
    pdf.xref_set_key(field_xref, "V", ps.encode_name(state))


def export_value(info: FieldInfo) -> Any:
    """Valore di interscambio di un campo."""
    if info.kind == CHECKBOX:
        return (info.export or "Yes") if info.value else OFF
    if info.kind == RADIO:
        return info.value or OFF
    if info.kind == LIST and info.multiselect:
        return list(info.value or [])
    if info.kind in (TEXT, COMBO, LIST):
        return info.value or ""
    return None


def form_values(pdf: pymupdf.Document) -> dict[str, Any]:
    """Valori di tutti i campi compilabili, per nome (una voce per campo)."""
    out: dict[str, Any] = {}
    for f in list_fields(pdf):
        if f.kind not in FILLABLE:
            continue
        v = export_value(f)
        if f.name in out:
            if f.kind == CHECKBOX and v != OFF:
                out[f.name] = v
            continue
        out[f.name] = v
    return out


def apply_values(pdf: pymupdf.Document, values: dict[str, Any], *, skip_readonly: bool = False) -> tuple[list[str], list[str]]:
    """Applica valori per nome. Restituisce (nomi applicati, nomi sconosciuti)."""
    by_name: dict[str, list[FieldInfo]] = {}
    for f in list_fields(pdf):
        if f.kind in FILLABLE:
            by_name.setdefault(f.name, []).append(f)
    applied, unknown = [], []
    for name, value in values.items():
        fields = by_name.get(name)
        if not fields:
            unknown.append(name)
            continue
        done = False
        for f in fields:
            if skip_readonly and f.readonly:
                continue
            if f.kind == RADIO:
                set_value(pdf, f.page, f.xref, OFF if value in (None, "", False) else value)
                done = True
                break
            if f.kind == CHECKBOX:
                checked = _truthy(value, f.export) or (isinstance(value, str) and value not in ("", OFF) and value == f.export)
                set_value(pdf, f.page, f.xref, checked)
            else:
                set_value(pdf, f.page, f.xref, value)
            done = True
        if done:
            applied.append(name)
    return applied, unknown


def reset_form(pdf: pymupdf.Document, names: Iterable[str] | None = None) -> int:
    """Riporta i campi al valore predefinito. Restituisce quanti campi sono cambiati."""
    wanted = set(names) if names is not None else None
    locked = signature_locked_names(pdf)
    count = 0
    seen_radio: set[int] = set()
    for f in list_fields(pdf):
        if f.kind not in FILLABLE or (wanted is not None and f.name not in wanted) or f.name in locked:
            continue
        if f.kind == RADIO:
            if f.field_xref in seen_radio:
                continue
            seen_radio.add(f.field_xref)
        target = f.default
        if f.kind == CHECKBOX:
            target = bool(f.default and f.default != OFF)
        if export_value(f) != export_value(_with_value(f, target)):
            count += 1
            set_value(pdf, f.page, f.xref, target if target is not None else "")
    return count


def _read_lock(pdf: pymupdf.Document, xref: int) -> dict:
    t, v = _inherited(pdf, xref, "Lock")
    if t == "xref":
        v, t = pdf.xref_object(int(v.split()[0]), compressed=True), "dict"
    if t != "dict":
        return {}
    try:
        lock = ps.parse(v)
    except ps.PdfSyntaxError:
        return {}
    return {"action": str(lock.get("Action", "")), "fields": [ps.text_of(n) or "" for n in lock.get("Fields", []) or []]}


def signature_locked_names(pdf: pymupdf.Document) -> set[str]:
    """Campi bloccati da una firma apposta (/Lock del campo firma)."""
    fields = list_fields(pdf)
    out: set[str] = set()
    for f in fields:
        if f.kind != SIGNATURE or not is_signed(pdf, f.xref):
            continue
        t, v = _inherited(pdf, f.xref, "Lock")
        if t == "xref":
            v, t = pdf.xref_object(int(v.split()[0]), compressed=True), "dict"
        if t != "dict":
            continue
        try:
            lock = ps.parse(v)
        except ps.PdfSyntaxError:
            continue
        action = str(lock.get("Action", ""))
        listed = {ps.text_of(n) or "" for n in lock.get("Fields", []) or []}
        names = {g.name for g in fields if g.kind != SIGNATURE}
        if action == "All":
            out |= names
        elif action == "Include":
            out |= names & listed
        elif action == "Exclude":
            out |= names - listed
    return out


def _with_value(f: FieldInfo, value: Any) -> FieldInfo:
    from dataclasses import replace
    return replace(f, value=value)


# ====================================================================== validazione
def _required_msg(info: FieldInfo) -> str:
    return _("Il campo «{n}» è obbligatorio.").format(n=info.name)


def validate(info: FieldInfo, value: Any) -> str | None:
    """Messaggio d'errore se il valore memorizzabile `value` non è accettabile, altrimenti None."""
    if info.kind != TEXT:
        if info.required and export_value(_with_value(info, value)) in ("", OFF, [], None):
            return _required_msg(info)
        return None
    text = "" if value is None else str(value)
    if not text.strip():
        return _required_msg(info) if info.required else None
    if info.maxlen and len(text) > info.maxlen:
        return _("Il campo «{n}» ammette al massimo {k} caratteri.").format(n=info.name, k=info.maxlen)
    v = info.validation
    custom = v.message
    num = parse_number(text) if (info.fmt.numeric or v.minimum is not None or v.maximum is not None) else None
    if (info.fmt.numeric or v.minimum is not None or v.maximum is not None) and num is None:
        return custom or _("Nel campo «{n}» va inserito un numero.").format(n=info.name)
    if v.minimum is not None and num is not None and num < v.minimum:
        return custom or _("Il valore di «{n}» deve essere almeno {m}.").format(n=info.name, m=_num_js(v.minimum))
    if v.maximum is not None and num is not None and num > v.maximum:
        return custom or _("Il valore di «{n}» deve essere al massimo {m}.").format(n=info.name, m=_num_js(v.maximum))
    if v.min_length and len(text) < v.min_length:
        return custom or _("Il campo «{n}» richiede almeno {k} caratteri.").format(n=info.name, k=v.min_length)
    if v.regex:
        try:
            ok = re.search(v.regex, text) is not None
        except re.error:
            ok = True
        if not ok:
            return custom or _("Il valore di «{n}» non ha la forma richiesta.").format(n=info.name)
    return None


def normalize_input(info: FieldInfo, text: str) -> str:
    """Valore da memorizzare per l'input dell'utente; solleva FormError se non interpretabile."""
    if info.kind != TEXT:
        return text
    if not info.multiline:
        text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    else:
        text = text.replace("\r\n", "\r").replace("\n", "\r")
    try:
        return fm.normalize(info.fmt, text)
    except fm.FormatError as e:
        raise FormError(str(e)) from e


def missing_required(pdf: pymupdf.Document) -> list[FieldInfo]:
    """Campi obbligatori non compilati (uno per nome)."""
    out, seen = [], set()
    for f in list_fields(pdf):
        if not f.required or f.kind not in FILLABLE or f.name in seen:
            continue
        seen.add(f.name)
        values = [export_value(g) for g in list_fields(pdf, f.page) if g.name == f.name]
        if all(v in ("", OFF, [], None) for v in values):
            out.append(f)
    return out


# ====================================================================== calcoli
def _calc_order(pdf: pymupdf.Document) -> list[int]:
    t, v = pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/CO")
    if t != "array":
        return []
    try:
        return [r[0] for r in ps.parse(v) if isinstance(r, ps.Ref)]
    except ps.PdfSyntaxError:
        return []


def compute(op: str, numbers: list[float]) -> float:
    if not numbers:
        return 0.0
    op = op.upper()
    if op == "SUM":
        return math.fsum(numbers)
    if op == "AVG":
        return math.fsum(numbers) / len(numbers)
    if op == "PRD":
        return math.prod(numbers)
    if op == "MIN":
        return min(numbers)
    if op == "MAX":
        return max(numbers)
    raise FormError(_("Operazione di calcolo sconosciuta: {op}").format(op=op))


def expand_names(names: Iterable[str], all_names: Iterable[str]) -> list[str]:
    """Nomi dei campi citati: un nome di gruppo («importo») include i discendenti («importo.0»…)."""
    all_names = list(all_names)
    out: list[str] = []
    for n in names:
        matches = [a for a in all_names if a == n or a.startswith(n + ".")]
        for m in matches or [n]:
            if m not in out:
                out.append(m)
    return out


def _dependencies(fields: list[FieldInfo]) -> dict[str, list[str]]:
    names = [f.name for f in fields]
    out = {}
    for f in fields:
        if f.kind != TEXT or f.name in out:
            continue
        if f.calc:
            out[f.name] = expand_names(f.calc.fields, names)
        elif f.formula:
            out[f.name] = expand_names(f.formula.names, names)
    return out


def find_cycle(pdf: pymupdf.Document, target: str, calc: Calculation | None) -> list[str] | None:
    """Ciclo di dipendenze che si creerebbe dando a `target` il calcolo `calc` (None se nessuno)."""
    fields = list_fields(pdf)
    deps = _dependencies(fields)
    names = [f.name for f in fields]
    if calc is None:
        deps.pop(target, None)
    else:
        refs = calc.fields if isinstance(calc, Calculation) else calc.names
        deps[target] = expand_names(refs, names)

    def visit(node, path):
        for d in deps.get(node, []):
            if d == target:
                return path + [d]
            if d in path:
                continue
            found = visit(d, path + [d])
            if found:
                return found
        return None

    return visit(target, [target])


def dependency_order(pdf: pymupdf.Document) -> list[str]:
    """Campi calcolati in un ordine che rispetta le dipendenze."""
    fields = list_fields(pdf)
    deps = _dependencies(fields)
    order: list[str] = []
    visiting: set[str] = set()

    def visit(n):
        if n in order or n in visiting or n not in deps:
            return
        visiting.add(n)
        for d in deps[n]:
            visit(d)
        visiting.discard(n)
        order.append(n)

    for f in fields:
        if f.name in deps:
            visit(f.name)
    return order


def calc_order(pdf: pymupdf.Document) -> list[str]:
    """Ordine di calcolo dichiarato (/CO), completato con i campi calcolati mancanti."""
    fields = list_fields(pdf)
    by_xref = {f.field_xref: f.name for f in fields}
    names = [by_xref[x] for x in _calc_order(pdf) if x in by_xref]
    for f in fields:
        if f.kind == TEXT and (f.calc or f.formula) and f.name not in names:
            names.append(f.name)
    return [n for i, n in enumerate(names) if n not in names[:i]]


def order_problems(pdf: pymupdf.Document, order: list[str] | None = None) -> list[tuple[str, str]]:
    """Coppie (campo, dipendenza) in cui il campo è calcolato prima di ciò da cui dipende."""
    order = calc_order(pdf) if order is None else order
    deps = _dependencies(list_fields(pdf))
    pos = {n: i for i, n in enumerate(order)}
    return [(n, d) for n in order for d in deps.get(n, []) if d in pos and pos[d] > pos[n]]


def set_calc_order(pdf: pymupdf.Document, names: list[str]) -> None:
    fields = list_fields(pdf)
    by_name = {}
    for f in fields:
        if f.kind == TEXT and (f.calc or f.formula):
            by_name.setdefault(f.name, f.field_xref)
    xrefs = [by_name[n] for n in names if n in by_name]
    xrefs += [x for x in by_name.values() if x not in xrefs]
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm")[0] != "null":
        pdf.xref_set_key(cat, "AcroForm/CO", ps.serialize([ps.Ref(x) for x in xrefs]) if xrefs else "null")


def recalculate(pdf: pymupdf.Document) -> list[str]:
    """Ricalcola i campi con un calcolo semplice nell'ordine di calcolo; ogni campo al massimo
    una volta. I campi vuoti o non numerici valgono 0 (anche nella media), come negli altri lettori.
    Restituisce i nomi dei campi aggiornati."""
    fields = list_fields(pdf)
    deps = _dependencies(fields)
    if not deps:
        return []
    values: dict[str, Any] = {}
    for f in fields:
        if f.kind == TEXT:
            values.setdefault(f.name, f.value)
        elif f.kind == CHECKBOX:
            values[f.name] = 1.0 if f.value or values.get(f.name) == 1.0 else 0.0
        elif f.kind in (COMBO, LIST, RADIO):
            values.setdefault(f.name, export_value(f))
    first = {}
    for f in fields:
        if f.name in deps and f.name not in first and (f.calc or f.formula):
            first[f.name] = f
    changed: list[str] = []
    for name in calc_order(pdf):
        f = first.get(name)
        if f is None:
            continue
        nums = []
        for src in deps[name]:
            v = values.get(src)
            if isinstance(v, list):
                v = v[0] if v else None
            n = parse_number(v)
            nums.append(n if n is not None else 0.0)
        if f.calc is not None:
            result = compute(f.calc.op, nums)
        else:
            result = FX.evaluate(f.formula, values)
        text = "" if result is None else fm.canonical_number(result, f.fmt.decimals if f.fmt.numeric else None)
        if values.get(name) != text:
            values[name] = text
            set_value(pdf, f.page, f.xref, text)
            changed.append(name)
    return changed


def _set_calc_order(pdf: pymupdf.Document) -> None:
    """Aggiorna /CO: conserva l'ordine dichiarato e aggiunge in coda i nuovi campi calcolati."""
    set_calc_order(pdf, calc_order(pdf))


# ====================================================================== creazione e proprietà
def field_names(pdf: pymupdf.Document) -> set[str]:
    return {f.name for f in list_fields(pdf)}


def unique_name(pdf: pymupdf.Document, base: str, taken: set[str] | None = None) -> str:
    taken = field_names(pdf) if taken is None else taken
    base = re.sub(r"[.\s]+", "_", base.strip()) or _("Campo")
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def default_base_name(kind: str) -> str:
    return {TEXT: _("Testo"), CHECKBOX: _("Casella"), RADIO: _("Opzione"), COMBO: _("Discesa"),
            LIST: _("Elenco"), BUTTON: _("Pulsante"), SIGNATURE: _("Firma"), "date": _("Data"),
            "image": _("Immagine")}.get(kind, _("Campo"))


DEFAULT_SIZES = {TEXT: (160, 20), CHECKBOX: (14, 14), RADIO: (14, 14), COMBO: (140, 20), LIST: (140, 60),
                 BUTTON: (90, 22), SIGNATURE: (160, 40)}


def create_field(pdf: pymupdf.Document, page: int, kind: str, rect: pymupdf.Rect, name: str | None = None,
                 **props) -> int:
    """Crea un campo e restituisce lo xref del widget.

    Per i pulsanti di opzione `props["export"]` è il valore della singola opzione
    e `name` il nome del gruppo (un gruppo esistente con lo stesso nome viene esteso).
    """
    if kind == DATE_KIND:
        kind = TEXT
        props.setdefault("fmt", Format(fm.DATE))
        name = name or default_name(pdf, DATE_KIND)
    if kind not in _TYPE_OF_KIND:
        raise FormError(_("Tipo di campo sconosciuto."))
    pg = pdf[page]
    rect = clamp_rect(pdf, page, rect)
    existing_group = None
    if kind == RADIO and name:
        for f in list_fields(pdf):
            if f.kind == RADIO and f.name == name:
                existing_group = f
                break
    full_name = None
    if not name:
        name = default_name(pdf, kind)
    elif kind != RADIO or existing_group is None:
        taken = {f.name: f.kind for f in list_fields(pdf)}
        if name in taken:
            raise NameTaken(name, taken[name] == kind)
        if "." in name:
            full_name, name = name, unique_name(pdf, "tmp")
    w = pymupdf.Widget()
    w.field_type = _TYPE_OF_KIND[kind]
    w.field_name = name
    w.rect = rect
    w.text_font = FONTS.get(props.get("font", "Helvetica"), "Helv")
    w.text_fontsize = float(props.get("fontsize", 10 if kind in (TEXT, COMBO, LIST) else 0))
    w.border_color = props.get("border_color", (0.45, 0.5, 0.58))
    w.border_width = float(props.get("border_width", 1))
    w.fill_color = props.get("fill_color", (1, 1, 1) if kind != BUTTON else (0.9, 0.9, 0.9))
    if kind in (COMBO, LIST):
        w.choice_values = list(props.get("options") or [_("Voce 1"), _("Voce 2")])
    if kind == CHECKBOX:
        w.field_value = False
    if kind == RADIO:
        w.field_value = False
    if kind == BUTTON:
        w.button_caption = props.get("caption", name)
    if kind == SIGNATURE:
        w.field_value = None
    first_on_page = not tab_order(pdf, page)
    _ensure_acroform(pdf)
    pg.add_widget(w)
    xref = w.xref
    if first_on_page and pdf.xref_get_key(pg.xref, "Tabs")[0] == "null":
        pdf.xref_set_key(pg.xref, "Tabs", "/R")
    if kind in (CHECKBOX, RADIO):
        export = str(props.get("export") or ("Yes" if kind == CHECKBOX else _next_export(pdf, existing_group, name)))
        _rename_on_state(pdf, xref, export)
        fx = _field_xref(pdf, xref)
        if kind == RADIO and existing_group is not None and fx != existing_group.field_xref:
            _merge_into_group(pdf, xref, fx, existing_group.field_xref)
            fx = existing_group.field_xref
        if pdf.xref_get_key(fx, "V")[0] == "null" or kind == CHECKBOX or existing_group is None:
            pdf.xref_set_key(fx, "V", "/Off")
        pdf.xref_set_key(xref, "AS", "/Off")
    if pg.rotation:
        pdf.xref_set_key(xref, "MK/R", str(pg.rotation))
    tag_widget(pdf, page, xref)
    if full_name:
        fx = _field_xref(pdf, xref)
        if fx == xref:
            raise FormError(_("Impossibile creare il campo con un nome gerarchico."))
        _rename_field(pdf, fx, name, full_name)
    rest = {k: v for k, v in props.items() if k not in ("export", "caption")}
    if rest:
        update_properties(pdf, page, xref, **rest)
    elif kind in (TEXT, COMBO, LIST):
        regenerate(pdf, page, xref)
    elif kind == SIGNATURE:
        blank_signature_appearance(pdf, page, xref)
    return xref


def _button_down_appearance(pdf: pymupdf.Document, page: int, xref: int) -> None:
    """Aspetto «premuto» (/AP /D) con la didascalia di pressione (/MK /AC), se diversa."""
    down = _value_of(*pdf.xref_get_key(xref, "MK/AC"))
    if not down:
        return
    normal = pdf.xref_get_key(xref, "MK/CA")
    pdf.xref_set_key(xref, "MK/CA", ps.encode_string(down))
    try:
        regenerate(pdf, page, xref)
        t, v = pdf.xref_get_key(xref, "AP/N")
        down_ref = v if t == "xref" else None
    finally:
        pdf.xref_set_key(xref, "MK/CA", ps.encode_string(normal[1]) if normal[0] == "string" else "null")
    regenerate(pdf, page, xref)
    if down_ref:
        pdf.xref_set_key(xref, "AP/D", down_ref)


# ---------------------------------------------------------------- pulsanti: aspetti per stato (FRM-18)
# Disposizione di icona e didascalia (/MK /TP 0-6).
BUTTON_LAYOUTS = ("text", "icon", "icon_above", "icon_below", "icon_left", "icon_right", "overlay")
# Icone per stato: normale, passaggio del puntatore, pressione (/MK /I, /RI, /IX).
ICON_KEYS = {"normal": "I", "rollover": "RI", "down": "IX"}
_STD_FONTS = {"Helv": "helv", "TiRo": "tiro", "Cour": "cour"}


def button_layout(pdf: pymupdf.Document, xref: int) -> str:
    t, v = pdf.xref_get_key(xref, "MK/TP")
    try:
        n = int(v) if t == "int" else 0
    except ValueError:
        n = 0
    return BUTTON_LAYOUTS[n] if 0 <= n < len(BUTTON_LAYOUTS) else "text"


def button_icons(pdf: pymupdf.Document, xref: int) -> dict[str, int]:
    """Icone presenti per stato: {"normal": xref, ...}."""
    out = {}
    for state, key in ICON_KEYS.items():
        t, v = pdf.xref_get_key(xref, f"MK/{key}")
        if t == "xref":
            out[state] = int(v.split()[0])
    return out


def icon_xobject(pdf: pymupdf.Document, source: str | bytes, size: tuple[float, float] | None = None,
                 dpi: int = 200, page: int = 0) -> int:
    """Form XObject per un'icona da un'immagine (percorso o byte) o da una pagina di un PDF.
    Le immagini sono ridotte alla risoluzione utile per `size` (punti), così il file non cresce."""
    import io
    data = source if isinstance(source, bytes) else open(source, "rb").read()
    if data[:5] == b"%PDF-":
        src = pymupdf.open("pdf", data)
        try:
            if not 0 <= page < src.page_count:
                raise FormError(_("Pagina inesistente nel PDF dell'icona."))
            r = src[page].rect
            tmp = pdf.new_page(width=r.width, height=r.height)
            form = tmp.show_pdf_page(tmp.rect, src, page)
        finally:
            src.close()
        pdf.delete_page(tmp.number)
        return form
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = ImageOps.exif_transpose(img)
    except Exception as e:
        raise FormError(_("Impossibile leggere l'immagine: {e}").format(e=e)) from e
    alpha = img.mode in ("RGBA", "LA", "P") and ("transparency" in img.info or img.mode in ("RGBA", "LA"))
    img = img.convert("RGBA" if alpha else "RGB")
    if size:
        img.thumbnail((max(1, int(size[0] / 72 * dpi)), max(1, int(size[1] / 72 * dpi))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG" if alpha else "JPEG", **({"optimize": True} if alpha else {"quality": 88}))
    w, h = img.width * 72 / dpi, img.height * 72 / dpi
    tmp = pdf.new_page(width=max(w, 1), height=max(h, 1))
    tmp.insert_image(pymupdf.Rect(0, 0, w, h), stream=buf.getvalue())
    img_xref = tmp.get_images(full=True)[0][0]
    pdf.delete_page(tmp.number)
    form = pdf.get_new_xref()
    pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_n(w)} {_n(h)}]"
                            f"/Resources<</XObject<</Img {img_xref} 0 R>>>>>>")
    pdf.update_stream(form, f"q {_n(w)} 0 0 {_n(h)} 0 0 cm /Img Do Q".encode())
    return form


def _xobject_box(pdf: pymupdf.Document, x: int) -> pymupdf.Rect:
    """Ingombro di un Form XObject nel suo spazio (BBox trasformato da /Matrix)."""
    try:
        r = pymupdf.Rect(ps.parse(pdf.xref_get_key(x, "BBox")[1]))
    except Exception:
        return pymupdf.Rect(0, 0, 1, 1)
    t, v = pdf.xref_get_key(x, "Matrix")
    if t == "array":
        try:
            r = r * pymupdf.Matrix(*[float(n) for n in ps.parse(v)])
        except Exception:
            pass
    return r.normalize()


def _fit_icon(pdf: pymupdf.Document, xref: int, icon: int, box: pymupdf.Rect) -> str:
    """Operatori che disegnano l'icona nel riquadro secondo /MK /IF (quando scalare, proporzioni, posizione)."""
    src = _xobject_box(pdf, icon)
    if src.width <= 0 or src.height <= 0 or box.width <= 0 or box.height <= 0:
        return ""
    t, v = pdf.xref_get_key(xref, "MK/IF")
    fit = ps.parse(v) if t == "dict" else {}
    when = str(fit.get("SW", "A"))
    sx, sy = box.width / src.width, box.height / src.height
    if str(fit.get("S", "P")) != "A":
        sx = sy = min(sx, sy)
    bigger, smaller = src.width > box.width or src.height > box.height, src.width < box.width and src.height < box.height
    if when == "N" or (when == "B" and not bigger) or (when == "S" and not smaller):
        sx = sy = 1.0
    pos = fit.get("A") or [0.5, 0.5]
    try:
        ax, ay = float(pos[0]), float(pos[1])
    except Exception:
        ax = ay = 0.5
    iw, ih = src.width * sx, src.height * sy
    ox = box.x0 + (box.width - iw) * ax - src.x0 * sx
    oy = box.y0 + (box.height - ih) * ay - src.y0 * sy
    return (f"q {_n(box.x0)} {_n(box.y0)} {_n(box.width)} {_n(box.height)} re W n "
            f"{_n(sx)} 0 0 {_n(sy)} {_n(ox)} {_n(oy)} cm /Ic Do Q")


def _pdf_text(text: str) -> str:
    raw = text.encode("cp1252", "replace").decode("latin-1")
    return "(" + raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def _button_stream(pdf: pymupdf.Document, info: "FieldInfo", xref: int, caption: str, icon: int | None,
                   layout: str, pressed: bool) -> int:
    """Form XObject con l'aspetto del pulsante in uno stato (sfondo, bordo, icona, didascalia)."""
    rot = info.rotation % 360
    r = pymupdf.Rect(info.rect)
    w, h = (r.height, r.width) if rot in (90, 270) else (r.width, r.height)
    ops = []
    if info.fill_color:
        c = info.fill_color
        if pressed:   # pressione: sfondo un po' più scuro
            c = tuple(max(0.0, x - 0.15) for x in c)
        ops.append(" ".join(_n(x) for x in c[:3]) + f" rg 0 0 {_n(w)} {_n(h)} re f")
    bw = info.border_width if info.border_color else 0
    if bw:
        dash = "[3 2] 0 d " if info.border_style == "dashed" else ""
        bc = " ".join(_n(x) for x in info.border_color[:3])
        if info.border_style == "underline":
            ops.append(f"{bc} RG {_n(bw)} w 0 {_n(bw / 2)} m {_n(w)} {_n(bw / 2)} l S")
        else:
            ops.append(f"{bc} RG {_n(bw)} w {dash}{_n(bw / 2)} {_n(bw / 2)} {_n(w - bw)} {_n(h - bw)} re S")
    pad = bw + 2
    inner = pymupdf.Rect(pad, pad, w - pad, h - pad)
    if pressed:   # effetto pressione: contenuto spostato di un punto
        inner = pymupdf.Rect(inner.x0 + 1, inner.y0 - 1, inner.x1 + 1, inner.y1 - 1)
    res_font = FONTS.get(info.font, "Helv") if FONTS.get(info.font) in _STD_FONTS else "Helv"
    _ensure_font_resource(pdf, res_font)
    if layout == "text" or icon is None:
        layout = "text" if layout not in ("text", "overlay") or icon is None else layout
    text = caption if layout != "icon" else ""
    size = info.fontsize or 0
    if text and not size:
        size = max(4.0, min(12.0, inner.height * 0.7 if layout in ("text", "overlay") else inner.height * 0.35))
    measure = _STD_FONTS[res_font]
    tw = pymupdf.get_text_length(text, fontname=measure, fontsize=size) if text else 0
    line = size * 1.15 if text else 0
    icon_box = text_box = inner
    if layout == "icon_above":
        icon_box = pymupdf.Rect(inner.x0, inner.y0 + line, inner.x1, inner.y1)
        text_box = pymupdf.Rect(inner.x0, inner.y0, inner.x1, inner.y0 + line)
    elif layout == "icon_below":
        icon_box = pymupdf.Rect(inner.x0, inner.y0, inner.x1, inner.y1 - line)
        text_box = pymupdf.Rect(inner.x0, inner.y1 - line, inner.x1, inner.y1)
    elif layout in ("icon_left", "icon_right"):
        side = min(inner.height, inner.width / 2)
        if layout == "icon_left":
            icon_box = pymupdf.Rect(inner.x0, inner.y0, inner.x0 + side, inner.y1)
            text_box = pymupdf.Rect(inner.x0 + side + 2, inner.y0, inner.x1, inner.y1)
        else:
            icon_box = pymupdf.Rect(inner.x1 - side, inner.y0, inner.x1, inner.y1)
            text_box = pymupdf.Rect(inner.x0, inner.y0, inner.x1 - side - 2, inner.y1)
    resources = f"/Font<</{res_font} {pdf.xref_get_key(pdf.pdf_catalog(), f'AcroForm/DR/Font/{res_font}')[1]}>>"
    if icon is not None and layout != "text":
        ops.append(_fit_icon(pdf, xref, icon, icon_box))
        resources += f"/XObject<</Ic {icon} 0 R>>"
    if text:
        while tw > text_box.width and size > 4:   # didascalia ridotta finché entra
            size -= 0.5
            tw = pymupdf.get_text_length(text, fontname=measure, fontsize=size)
        tx = text_box.x0 + max(0.0, (text_box.width - tw) / 2)
        ty = text_box.y0 + (text_box.height - size * 0.7) / 2
        col = " ".join(_n(x) for x in (list(info.text_color or (0, 0, 0)) + [0, 0, 0])[:3])
        ops.append(f"q {_n(text_box.x0)} {_n(text_box.y0)} {_n(text_box.width)} {_n(text_box.height)} re W n "
                   f"BT {col} rg /{res_font} {_n(size)} Tf {_n(tx)} {_n(ty)} Td {_pdf_text(text)} Tj ET Q")
    matrix = {90: f"0 1 -1 0 {_n(h)} 0", 180: f"-1 0 0 -1 {_n(w)} {_n(h)}", 270: f"0 -1 1 0 0 {_n(w)}"}.get(rot)
    form = pdf.get_new_xref()
    pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_n(w)} {_n(h)}]"
                            + (f"/Matrix[{matrix}]" if matrix else "") + f"/Resources<<{resources}>>>>")
    pdf.update_stream(form, ("\n".join(o for o in ops if o) + "\n").encode("latin-1"))   # testo in WinAnsi
    return form


def needs_state_appearances(pdf: pymupdf.Document, xref: int) -> bool:
    """Il pulsante ha icone, una didascalia al passaggio o una disposizione diversa dal solo testo."""
    return (button_layout(pdf, xref) != "text" or bool(button_icons(pdf, xref))
            or bool(_value_of(*pdf.xref_get_key(xref, "MK/RC"))))


def button_appearances(pdf: pymupdf.Document, page: int, xref: int) -> None:
    """Aspetti del pulsante per stato: /N normale, /R passaggio del puntatore, /D pressione.
    Con sole didascalie normali e di pressione resta la generazione del motore."""
    if is_image_field(pdf, xref):
        return
    if not needs_state_appearances(pdf, xref):
        _button_down_appearance(pdf, page, xref)
        return
    info = field_info(pdf, page, xref)
    layout = button_layout(pdf, xref)
    icons = button_icons(pdf, xref)
    normal = _value_of(*pdf.xref_get_key(xref, "MK/CA")) or ""
    roll = _value_of(*pdf.xref_get_key(xref, "MK/RC")) or ""
    down = _value_of(*pdf.xref_get_key(xref, "MK/AC")) or ""
    ap = {"N": _button_stream(pdf, info, xref, normal, icons.get("normal"), layout, False)}
    if roll or "rollover" in icons:
        ap["R"] = _button_stream(pdf, info, xref, roll or normal, icons.get("rollover", icons.get("normal")),
                                 layout, False)
    ap["D"] = _button_stream(pdf, info, xref, down or normal, icons.get("down", icons.get("normal")), layout, True)
    pdf.xref_set_key(xref, "AP", "<<" + "".join(f"/{k} {v} 0 R" for k, v in ap.items()) + ">>")
    pdf.xref_set_key(xref, "H", "/P")   # pressione: si mostra /D


def set_button_icon(pdf: pymupdf.Document, page: int, xref: int, state: str, source: str | bytes | None,
                    pdf_page: int = 0) -> None:
    """Imposta (o toglie, con None) l'icona di uno stato e rigenera gli aspetti."""
    key = ICON_KEYS[state]
    if source is None:
        pdf.xref_set_key(xref, f"MK/{key}", "null")
    else:
        info = field_info(pdf, page, xref)
        form = icon_xobject(pdf, source, (info.rect.width, info.rect.height), page=pdf_page)
        pdf.xref_set_key(xref, f"MK/{key}", f"{form} 0 R")
        pdf.xref_set_key(xref, "FoliantButton", "true")   # pulsante con icona, non campo immagine
        if button_layout(pdf, xref) == "text":
            pdf.xref_set_key(xref, "MK/TP", "2" if info.caption else "1")
        if pdf.xref_get_key(xref, "MK/IF")[0] == "null":
            pdf.xref_set_key(xref, "MK/IF", "<</SW/A/S/P/A[0.5 0.5]>>")
    button_appearances(pdf, page, xref)


def blank_signature_appearance(pdf: pymupdf.Document, page: int, xref: int) -> None:
    """Aspetto di un campo firma non firmato: solo sfondo e bordo del campo, nessun contenuto
    (l'indicatore «firma qui» è disegnato dall'interfaccia, non nel file)."""
    t, v = pdf.xref_get_key(xref, "Rect")
    try:
        r = pymupdf.Rect(ps.parse(v)).normalize()
    except Exception:
        return
    rot = 0
    if pdf.xref_get_key(xref, "MK/R")[0] == "int":
        try:
            rot = int(pdf.xref_get_key(xref, "MK/R")[1]) % 360
        except ValueError:
            rot = 0
    w, h = (r.height, r.width) if rot in (90, 270) else (r.width, r.height)
    ops = []

    def color(key):
        tt, vv = pdf.xref_get_key(xref, f"MK/{key}")
        if tt != "array":
            return None
        try:
            c = [float(x) for x in ps.parse(vv)]
        except Exception:
            return None
        return c if len(c) in (1, 3, 4) else None

    bg, bc = color("BG"), color("BC")
    op_fill = {1: "g", 3: "rg", 4: "k"}
    op_line = {1: "G", 3: "RG", 4: "K"}
    if bg:
        ops.append(" ".join(_n(x) for x in bg) + f" {op_fill[len(bg)]} 0 0 {_n(w)} {_n(h)} re f")
    bw = 0.0
    if pdf.xref_get_key(xref, "BS/W")[0] in ("int", "real", "float"):
        bw = float(pdf.xref_get_key(xref, "BS/W")[1])
    if bc and bw > 0:
        dash = "[3 2] 0 d " if pdf.xref_get_key(xref, "BS/S")[1] == "/D" else ""
        ops.append(" ".join(_n(x) for x in bc) + f" {op_line[len(bc)]} {_n(bw)} w {dash}"
                   f"{_n(bw / 2)} {_n(bw / 2)} {_n(w - bw)} {_n(h - bw)} re S")
    matrix = {90: f"0 1 -1 0 {_n(h)} 0", 180: f"-1 0 0 -1 {_n(w)} {_n(h)}", 270: f"0 -1 1 0 0 {_n(w)}"}.get(rot)
    form = pdf.get_new_xref()
    pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_n(w)} {_n(h)}]"
                            + (f"/Matrix[{matrix}]" if matrix else "") + "/Resources<<>>>>")
    pdf.update_stream(form, ("\n".join(ops) + "\n").encode())
    pdf.xref_set_key(xref, "AP", f"<</N {form} 0 R>>")


DATE_KIND = "date"
MIN_SIZE = 4.0


def clamp_rect(pdf: pymupdf.Document, page: int, rect) -> pymupdf.Rect:
    """Riquadro limitato all'area visibile della pagina e portato almeno a 4×4 pt."""
    r = pymupdf.Rect(rect).normalize()
    pg = pdf[page]
    area = pymupdf.Rect(pg.rect * pg.derotation_matrix).normalize()
    area = pymupdf.Rect(0, 0, area.width, area.height)
    if r.width < MIN_SIZE:
        r.x1 = r.x0 + MIN_SIZE
    if r.height < MIN_SIZE:
        r.y1 = r.y0 + MIN_SIZE
    w, h = min(r.width, area.width), min(r.height, area.height)
    x0 = min(max(r.x0, area.x0), area.x1 - w)
    y0 = min(max(r.y0, area.y0), area.y1 - h)
    return pymupdf.Rect(x0, y0, x0 + w, y0 + h)


def default_name(pdf: pymupdf.Document, kind: str) -> str:
    """Nome predefinito con contatore: «Testo1», «Casella2»…"""
    base = default_base_name(kind)
    taken = field_names(pdf)
    n = 1
    while f"{base}{n}" in taken:
        n += 1
    return f"{base}{n}"


def _next_export(pdf: pymupdf.Document, group: FieldInfo | None, name: str) -> str:
    if group is None:
        return _("Scelta1")
    used = {f.export for f in list_fields(pdf) if f.kind == RADIO and f.name == name}
    n = len(used) + 1
    while _("Scelta{n}").format(n=n) in used:
        n += 1
    return _("Scelta{n}").format(n=n)


def _rename_on_state(pdf: pymupdf.Document, xref: int, export: str) -> None:
    """Dà allo stato attivo del widget il nome `export` (per caselle e opzioni)."""
    for appearance in ("N", "D"):
        t, v = pdf.xref_get_key(xref, f"AP/{appearance}")
        if t != "dict":
            continue
        try:
            states = ps.parse(v)
        except ps.PdfSyntaxError:
            continue
        on_ref = next((ref for k, ref in states.items() if k != OFF), None)
        new = {OFF: states.get(OFF)} if states.get(OFF) is not None else {}
        if on_ref is not None:
            new[export] = on_ref
        pdf.xref_set_key(xref, f"AP/{appearance}", ps.serialize(new))


def _merge_into_group(pdf: pymupdf.Document, xref: int, own_parent: int, group: int) -> None:
    """Sposta il widget `xref` sotto il campo `group` (stesso gruppo di opzioni)."""
    kids = _widgets_of_field(pdf, group)
    if group == kids[0] and pdf.xref_get_key(group, "Subtype")[1] == "/Widget":
        return  # gruppo con widget unito al campo: caso non gestito, resta separato
    kids.append(xref)
    pdf.xref_set_key(group, "Kids", ps.serialize([ps.Ref(k) for k in kids]))
    pdf.xref_set_key(xref, "Parent", f"{group} 0 R")
    if own_parent != xref:
        _remove_from_acroform(pdf, own_parent)
        pdf.xref_set_key(own_parent, "Kids", "[]")


def _remove_from_acroform(pdf: pymupdf.Document, field_xref: int) -> None:
    """Toglie il campo dall'albero del modulo (dal genitore o da /Fields)."""
    parent = _parent_node(pdf, field_xref) if pdf.xref_get_key(field_xref, "T")[0] != "null" else None
    kids = _children(pdf, parent)
    if field_xref in kids:
        _set_children(pdf, parent, [k for k in kids if k != field_xref])


class NameTaken(FormError):
    """Esiste già un campo con questo nome (si può proporre l'unione)."""

    def __init__(self, name: str, compatible: bool):
        super().__init__(_("Esiste già un campo chiamato «{n}».").format(n=name))
        self.name = name
        self.compatible = compatible


def _root_of(pdf: pymupdf.Document, xref: int) -> int:
    seen = set()
    x = xref
    while x not in seen:
        seen.add(x)
        p = _parent_node(pdf, x)
        if p is None:
            return x
        x = p
    return x  # gerarchia ciclica: interrotta


def repair_orphans(pdf: pymupdf.Document) -> int:
    """Collega a /AcroForm /Fields i campi con widget nelle pagine ma non elencati.
    Restituisce il numero di campi ricollegati."""
    listed = set(_acroform_fields(pdf))
    roots: list[int] = []
    from .scan import widget_xrefs
    for i in range(pdf.page_count):
        for x in widget_xrefs(pdf, i):
            r = _root_of(pdf, x)
            if r not in listed and r not in roots:
                roots.append(r)
    if roots:
        _set_children(pdf, None, _acroform_fields(pdf) + roots)
    return len(roots)


def orphan_count(pdf: pymupdf.Document) -> int:
    listed = set(_acroform_fields(pdf))
    roots = set()
    from .scan import widget_xrefs
    for i in range(pdf.page_count):
        for x in widget_xrefs(pdf, i):
            r = _root_of(pdf, x)
            if r not in listed:
                roots.add(r)
    return len(roots)


def needs_appearances(pdf: pymupdf.Document) -> bool:
    return pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/NeedAppearances") == ("bool", "true")


def regenerate_all(pdf: pymupdf.Document) -> int:
    """Rigenera l'aspetto di tutti i campi e toglie /NeedAppearances."""
    n = 0
    for f in list_fields(pdf):
        if f.kind in (TEXT, COMBO, LIST):
            _regen_field(pdf, f.field_xref, f.page, f.xref)
            n += 1
        elif f.kind in (CHECKBOX, RADIO) and not _states_of(pdf, f.xref):
            regenerate(pdf, f.page, f.xref)
            n += 1
    if pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")[0] != "null":
        pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/NeedAppearances", "null")
    return n


def prepare_for_edit(pdf: pymupdf.Document) -> None:
    """Riparazioni da applicare insieme alla prima modifica del modulo."""
    if orphan_count(pdf):
        repair_orphans(pdf)
    if needs_appearances(pdf):
        regenerate_all(pdf)


def _has_actions(pdf: pymupdf.Document, xref: int, fx: int) -> bool:
    """True se widget o campo hanno azioni aggiuntive (/AA): evita letture inutili."""
    return pdf.xref_get_key(xref, "AA")[0] != "null" or (fx != xref and pdf.xref_get_key(fx, "AA")[0] != "null")


def custom_scripts(pdf: pymupdf.Document, fields: list[FieldInfo] | None = None) -> list[tuple[str, str]]:
    """(nome del campo, evento) delle azioni personalizzate che Foliant non esegue."""
    out = []
    names = {"K": _("battitura"), "F": _("formato"), "V": _("validazione"), "C": _("calcolo")}
    for f in (list_fields(pdf) if fields is None else fields):
        if not _has_actions(pdf, f.xref, f.field_xref):
            continue
        for trig, label in names.items():
            js = _action_script(pdf, f.xref, trig)
            if not js:
                continue
            if trig == "C" and (Calculation.from_script(js) or FX.from_script(js)):
                continue
            if trig == "V" and (Validation.from_script(js).empty is False):
                continue
            if trig in ("K", "F") and not fm.is_custom_script(js):
                continue
            item = (f.name, label)
            if item not in out:
                out.append(item)
    return out


def fill_blocked_reason(pdf: pymupdf.Document, permissions: int | None = None) -> str | None:
    """Motivo per cui il documento non consente la compilazione (None se consentita).
    `permissions`: permessi del file originale (le copie interne non sono cifrate)."""
    try:
        perms = pdf.permissions if permissions is None else permissions
    except Exception:
        perms = -1
    if permissions is not None or pdf.is_encrypted or (pdf.metadata or {}).get("encryption"):
        if not (perms & pymupdf.PDF_PERM_FORM) and not (perms & pymupdf.PDF_PERM_ANNOTATE):
            return _("I permessi del documento non consentono la compilazione dei moduli.")
    cert = certification_info(pdf)
    if cert is not None and cert["level"] == 1:
        who = cert["signer"] or _("firmatario sconosciuto")
        if cert["field"]:
            return _("Il documento è certificato da {who} (campo firma «{field}») e non consente modifiche.").format(
                who=who, field=cert["field"])
        return _("Il documento è certificato da {who} e non consente modifiche.").format(who=who)
    return None


def certification_info(pdf: pymupdf.Document) -> dict | None:
    """Firma di certificazione (/Perms /DocMDP): livello delle modifiche ammesse (1 nessuna,
    2 compilazione e firme, 3 anche commenti), campo firma e nome del firmatario."""
    t, v = pdf.xref_get_key(pdf.pdf_catalog(), "Perms/DocMDP")
    if t != "xref":
        return None
    sx = int(v.split()[0])
    level = 2
    t, ref = pdf.xref_get_key(sx, "Reference")
    if t != "null":
        text = ref
        for r in re.findall(r"(\d+) 0 R", ref):   # riferimenti indiretti (TransformParams compresi)
            text += pdf.xref_object(int(r), compressed=True)
            for r2 in re.findall(r"(\d+) 0 R", pdf.xref_object(int(r), compressed=True)):
                text += pdf.xref_object(int(r2), compressed=True)
        m = re.search(r"/TransformMethod\s*/DocMDP.*?/P\s*(\d)", text, re.S) or \
            re.search(r"/TransformParams.*?/P\s*(\d)", text, re.S)
        if m:
            level = int(m.group(1))
    signer = _value_of(*pdf.xref_get_key(sx, "Name")) or _signer_common_name(pdf, sx)
    field = None
    for f in list_fields(pdf):
        if f.kind == SIGNATURE and pdf.xref_get_key(f.field_xref, "V")[1].split()[:1] == [str(sx)]:
            field = f.name
            break
    return {"level": level, "signer": signer or None, "field": field, "xref": sx}


def _signer_common_name(pdf: pymupdf.Document, sig_xref: int) -> str | None:
    """Nome comune del certificato del firmatario nel contenitore CMS (/Contents)."""
    m = re.search(r"/Contents\s*<([0-9A-Fa-f\s]+)>", pdf.xref_object(sig_xref, compressed=True))
    if not m:
        return None
    try:
        from asn1crypto import cms
        raw = bytes.fromhex(re.sub(r"\s", "", m.group(1)))
        signed = cms.ContentInfo.load(raw, strict=False)["content"]   # /Contents ha zeri di riempimento
        sid = signed["signer_infos"][0]["sid"]
        for c in signed["certificates"]:
            cert = c.chosen
            if sid.name == "issuer_and_serial_number" and \
                    cert.serial_number != sid.chosen["serial_number"].native:
                continue
            return cert.subject.native.get("common_name")
    except Exception:
        return None
    return None


def navigation_order(pdf: pymupdf.Document, page: int) -> list[FieldInfo]:
    """Widget della pagina nell'ordine di tabulazione dichiarato (/Tabs)."""
    fields = list_fields(pdf, page)
    tabs = _value_of(*pdf.xref_get_key(pdf.page_xref(page), "Tabs"))
    if tabs in ("R", "C"):
        pg = pdf[page]
        rot = pg.rotation_matrix

        def shown(f):
            return pymupdf.Rect(f.rect) * rot

        return sort_geometric(fields, "row" if tabs == "R" else "column", key=shown)
    return fields


def sort_geometric(fields: list[FieldInfo], by: str, key=None) -> list[FieldInfo]:
    """Ordine per righe (sinistra→destra, poi in basso) o per colonne. Due campi sono
    sulla stessa riga se si sovrappongono verticalmente per almeno metà dell'altezza del più basso."""
    rect = key or (lambda f: f.rect)
    items = [(f, pymupdf.Rect(rect(f))) for f in fields]
    if by == "column":
        items = [(f, pymupdf.Rect(r.y0, r.x0, r.y1, r.x1)) for f, r in items]
    items.sort(key=lambda it: (it[1].y0, it[1].x0))
    rows: list[list] = []
    for f, r in items:
        for row in rows:
            ref = row[0][1]
            overlap = min(ref.y1, r.y1) - max(ref.y0, r.y0)
            if overlap >= 0.5 * min(ref.height, r.height):
                row.append((f, r))
                break
        else:
            rows.append([(f, r)])
    out = []
    for row in rows:
        out.extend(f for f, _r in sorted(row, key=lambda it: it[1].x0))
    return out


def _da(font: str, size: float, color: tuple) -> str:
    r, g, b = (list(color) + [0, 0, 0])[:3]
    return f"{_n(r)} {_n(g)} {_n(b)} rg /{FONTS.get(font, font)} {_n(size)} Tf"


def _n(v: float) -> str:
    return ("%.4f" % float(v)).rstrip("0").rstrip(".") or "0"


def _color_array(c) -> str:
    return "[" + " ".join(_n(x) for x in c[:3]) + "]" if c else "null"


def update_properties(pdf: pymupdf.Document, page: int, xref: int, **props) -> None:
    """Cambia le proprietà di un campo. Chiavi: name, tooltip, default, readonly, required,
    multiline, password, comb, maxlen, multiselect, editable, options, font, fontsize,
    text_color, fill_color, border_color, border_width, border_style, align, fmt, calc,
    validation, rect, export, caption.

    Le chiavi vengono scritte direttamente negli oggetti del campo (campo e widget)
    secondo lo standard, così restano leggibili da qualunque lettore PDF."""
    info = field_info(pdf, page, xref)
    kind = info.kind
    fx = info.field_xref
    if "name" in props:
        new = str(props["name"]).strip()
        if not new:
            raise FormError(_("Il nome del campo non può essere vuoto."))
        if new != info.name:
            others = {f.name: f.kind for f in list_fields(pdf) if f.field_xref != fx}
            if new in others:
                raise NameTaken(new, others[new] == kind)
            _rename_field(pdf, fx, info.name, new)
    t, v = _inherited(pdf, xref, "Ff")
    try:
        flags = int(v) if t == "int" else 0
    except ValueError:
        flags = 0
    old_flags = flags

    def flag(bit: int, on: bool) -> None:
        nonlocal flags
        flags = (flags | bit) if on else (flags & ~bit)

    if "readonly" in props:
        flag(pymupdf.PDF_FIELD_IS_READ_ONLY, bool(props["readonly"]))
    if "required" in props:
        flag(pymupdf.PDF_FIELD_IS_REQUIRED, bool(props["required"]))
    maxlen = info.maxlen
    if kind == TEXT:
        if "multiline" in props:
            flag(pymupdf.PDF_TX_FIELD_IS_MULTILINE, bool(props["multiline"]))
        if "password" in props:
            flag(pymupdf.PDF_TX_FIELD_IS_PASSWORD, bool(props["password"]))
        if "comb" in props:
            flag(pymupdf.PDF_TX_FIELD_IS_COMB, bool(props["comb"]))
        if "maxlen" in props:
            maxlen = max(0, int(props["maxlen"] or 0))
        if flags & pymupdf.PDF_TX_FIELD_IS_COMB and not maxlen:
            raise FormError(_("Per la suddivisione in caselle serve una lunghezza massima."))
        if flags & pymupdf.PDF_TX_FIELD_IS_COMB:
            # il pettine esclude multiriga, password e scorrimento
            flags &= ~(pymupdf.PDF_TX_FIELD_IS_MULTILINE | pymupdf.PDF_TX_FIELD_IS_PASSWORD)
            flags |= FF_DO_NOT_SCROLL
        if "maxlen" in props:
            pdf.xref_set_key(fx, "MaxLen", str(maxlen) if maxlen else "null")
            if maxlen and len(info.value or "") > maxlen:
                pdf.xref_set_key(fx, "V", ps.encode_string(info.value[:maxlen]))
        if "fmt" in props:
            fmt = props["fmt"] or Format()
            f_js, k_js = fmt.scripts()
            _set_action(pdf, fx, "F", f_js)
            _set_action(pdf, fx, "K", k_js)
        if "calc" in props:
            calc = props["calc"]
            _set_action(pdf, fx, "C", calc.script() if calc and calc.fields else None)
        if "formula" in props:
            expr = props["formula"]
            if expr:
                try:
                    formula = FX.parse(expr) if isinstance(expr, str) else expr
                except FX.FormulaError as e:
                    raise FormError(e.message) from e
                unknown = FX.check(formula, {f.name for f in list_fields(pdf)})
                if unknown:
                    raise FormError(_("Campi inesistenti nella formula: {n}").format(n=", ".join(unknown)))
                _set_action(pdf, fx, "C", FX.to_script(formula))
            else:
                _set_action(pdf, fx, "C", None)
        if "validation" in props:
            _set_action(pdf, fx, "V", (props["validation"] or Validation()).script())
    if kind in (COMBO, LIST):
        if "multiselect" in props and kind == LIST:
            flag(pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT, bool(props["multiselect"]))
        if "editable" in props and kind == COMBO:
            flag(pymupdf.PDF_CH_FIELD_IS_EDIT, bool(props["editable"]))
        if "sort" in props:
            flag(pymupdf.PDF_CH_FIELD_IS_SORT, bool(props["sort"]))
        if "commit_on_change" in props:
            flag(pymupdf.PDF_CH_FIELD_IS_COMMIT_ON_SEL_CHANGE, bool(props["commit_on_change"]))
        opts_in = props.get("options")
        if opts_in is None and props.get("sort") and not info.sort:
            opts_in = list(zip(info.exports, info.options))
        if opts_in is not None:
            pairs = []
            for o in opts_in:
                if isinstance(o, (list, tuple)):
                    e, d = str(o[0]), str(o[1] if len(o) > 1 else o[0])
                else:
                    e = d = str(o)
                if d.strip() or e.strip():
                    pairs.append((e, d))
            if not pairs:
                raise FormError(_("Un elenco deve avere almeno una voce."))
            if flags & pymupdf.PDF_CH_FIELD_IS_SORT:
                import locale
                try:
                    keyf = locale.strxfrm
                    pairs.sort(key=lambda p_: (keyf(p_[1].casefold()), p_[1]))
                except Exception:
                    pairs.sort(key=lambda p_: p_[1].casefold())
            pdf.xref_set_key(fx, "Opt", ps.serialize([e if e == d else [e, d] for e, d in pairs]))
            exports = [e for e, _d in pairs]
            current = info.value if isinstance(info.value, list) else ([info.value] if info.value else [])
            keep = [c for c in current if c in exports or (kind == COMBO and flags & pymupdf.PDF_CH_FIELD_IS_EDIT)]
            if keep != current:
                pdf.xref_set_key(fx, "V", ps.encode_string(keep[0]) if len(keep) == 1 else (ps.serialize(keep) if keep else "null"))
            if kind == LIST:
                idx = sorted(exports.index(c) for c in keep if c in exports)
                pdf.xref_set_key(fx, "I", ps.serialize(idx) if idx else "null")
    if kind == RADIO:
        if "no_toggle_off" in props:
            flag(FF_NO_TOGGLE_OFF, bool(props["no_toggle_off"]))
        if "in_unison" in props:
            flag(FF_IN_UNISON, bool(props["in_unison"]))
    if kind == TEXT:
        if "do_not_scroll" in props:
            flag(FF_DO_NOT_SCROLL, bool(props["do_not_scroll"]))
        if "file_select" in props:
            flag(FF_FILE_SELECT, bool(props["file_select"]))
    if kind in (TEXT, COMBO) and "no_spell" in props:
        flag(FF_NO_SPELL, bool(props["no_spell"]))
    if "no_export" in props:
        flag(FF_NO_EXPORT, bool(props["no_export"]))
    if "visibility" in props or "locked" in props:
        af = _annot_flags(pdf, xref) & ~(AF_HIDDEN | AF_NOVIEW | AF_PRINT) if "visibility" in props else _annot_flags(pdf, xref)
        vis = props.get("visibility")
        if vis == "hidden":
            af |= AF_HIDDEN
        elif vis == "print":
            af |= AF_NOVIEW | AF_PRINT
        elif vis == "screen":
            pass
        elif vis is not None:
            af |= AF_PRINT
        if "locked" in props:
            af = (af | AF_LOCKED) if props["locked"] else (af & ~AF_LOCKED)
        pdf.xref_set_key(xref, "F", str(af))
    if "rotation" in props:
        rot = int(props["rotation"] or 0) % 360
        if rot not in (0, 90, 180, 270):
            raise FormError(_("Rotazione non valida."))
        pdf.xref_set_key(xref, "MK/R", str(rot) if rot else "null")
    if "lock" in props and kind == SIGNATURE:
        lock = props["lock"] or {}
        action = lock.get("action")
        if action in ("All", "Include", "Exclude"):
            names = [str(n) for n in lock.get("fields") or []]
            value = f"<</Type/SigFieldLock/Action/{action}" + (f"/Fields{ps.serialize(names)}" if action != "All" else "") + ">>"
            pdf.xref_set_key(fx, "Lock", value)
        else:
            pdf.xref_set_key(fx, "Lock", "null")
    if "action" in props and kind == BUTTON:
        set_button_action(pdf, page, xref, props["action"])
    if "check_style" in props and kind in (CHECKBOX, RADIO):
        ch = CHECK_STYLES.get(props["check_style"], "4")
        pdf.xref_set_key(xref, "MK/CA", ps.encode_string(ch))
    if flags != old_flags:
        pdf.xref_set_key(fx, "Ff", str(flags))
    if "export_name" in props:
        tm = str(props["export_name"] or "").strip()
        pdf.xref_set_key(fx, "TM", ps.encode_string(tm) if tm else "null")
    if "tooltip" in props:
        pdf.xref_set_key(fx, "TU", ps.encode_string(props["tooltip"]) if props["tooltip"] else "null")
    if "font" in props:
        if props["font"] in FONTS:
            if pdf.xref_get_key(xref, "FoliantFont")[0] != "null":
                pdf.xref_set_key(xref, "FoliantFont", "null")
        else:
            if kind != TEXT:
                raise FormError(_("I caratteri di sistema sono disponibili solo per i campi di testo."))
            if not system_font_file(str(props["font"])):
                raise FormError(_("Il carattere «{f}» non è installato.").format(f=props["font"]))
            # l'aspetto userà il carattere incorporato; /DA resta su un carattere standard di ripiego
            pdf.xref_set_key(xref, "FoliantFont", ps.encode_string(str(props["font"])))
            props = {**props, "font": "Helvetica"}
    if any(k in props for k in ("font", "fontsize", "text_color")):
        font = props.get("font", info.font if info.font in FONTS else "Helvetica")
        size = max(0.0, float(props.get("fontsize", info.fontsize) or 0))
        color = props.get("text_color") or info.text_color or (0, 0, 0)
        pdf.xref_set_key(fx, "DA", ps.encode_string(_da(font, size, color)))
        if fx != xref and pdf.xref_get_key(xref, "DA")[0] != "null":
            pdf.xref_set_key(xref, "DA", "null")
        _ensure_font_resource(pdf, FONTS.get(font, font))
    if "fill_color" in props:
        pdf.xref_set_key(xref, "MK/BG", _color_array(props["fill_color"]))
    if "border_color" in props:
        pdf.xref_set_key(xref, "MK/BC", _color_array(props["border_color"]))
    if "border_width" in props or "border_style" in props:
        width = max(0.0, float(props.get("border_width", info.border_width) or 0))
        style = (props.get("border_style") or info.border_style or "S")[:1].upper()
        if style not in "SDBIU":
            style = "S"
        pdf.xref_set_key(xref, "BS", f"<</W {_n(width)}/S/{style}" + ("/D[3 2]" if style == "D" else "") + ">>")
        if pdf.xref_get_key(xref, "Border")[0] != "null":
            pdf.xref_set_key(xref, "Border", "null")
    if "align" in props:
        q = str(max(0, min(2, int(props["align"] or 0))))
        pdf.xref_set_key(fx, "Q", q)
        if fx != xref:
            pdf.xref_set_key(xref, "Q", q)
    if "caption" in props and kind == BUTTON:
        pdf.xref_set_key(xref, "MK/CA", ps.encode_string(str(props["caption"])))
    if "caption_down" in props and kind == BUTTON:
        down = str(props["caption_down"] or "")
        pdf.xref_set_key(xref, "MK/AC", ps.encode_string(down) if down else "null")
    if "caption_rollover" in props and kind == BUTTON:
        roll = str(props["caption_rollover"] or "")
        pdf.xref_set_key(xref, "MK/RC", ps.encode_string(roll) if roll else "null")
    if "layout" in props and kind == BUTTON:
        layout = props["layout"] or "text"
        if layout not in BUTTON_LAYOUTS:
            raise FormError(_("Disposizione del pulsante non valida."))
        pdf.xref_set_key(xref, "MK/TP", str(BUTTON_LAYOUTS.index(layout)) if layout != "text" else "null")
        if layout != "text":
            pdf.xref_set_key(xref, "FoliantButton", "true")
    for state, key in ICON_KEYS.items():
        if f"icon_{state}" in props and kind == BUTTON:
            src = props[f"icon_{state}"]
            if src is None or src == "":
                pdf.xref_set_key(xref, f"MK/{key}", "null")
            elif isinstance(src, int):   # icona già nel documento (copia di un campo)
                pdf.xref_set_key(xref, f"MK/{key}", f"{src} 0 R")
                pdf.xref_set_key(xref, "FoliantButton", "true")
            else:
                form = icon_xobject(pdf, src, (info.rect.width, info.rect.height))
                pdf.xref_set_key(xref, f"MK/{key}", f"{form} 0 R")
                pdf.xref_set_key(xref, "FoliantButton", "true")
                if button_layout(pdf, xref) == "text" and "layout" not in props:
                    pdf.xref_set_key(xref, "MK/TP", "2" if info.caption else "1")
                if pdf.xref_get_key(xref, "MK/IF")[0] == "null":
                    pdf.xref_set_key(xref, "MK/IF", "<</SW/A/S/P/A[0.5 0.5]>>")
    if "default" in props:
        _set_default(pdf, fx, kind, props["default"], info)
    if "export" in props and kind in (CHECKBOX, RADIO) and props["export"]:
        export = str(props["export"])
        if export != info.export:
            state = _value_of(*pdf.xref_get_key(xref, "AS"))
            _rename_on_state(pdf, xref, export)
            if state not in (None, OFF):
                pdf.xref_set_key(xref, "AS", ps.encode_name(export))
                pdf.xref_set_key(fx, "V", ps.encode_name(export))
    if "rect" in props:
        r = pymupdf.Rect(props["rect"]).normalize()
        if r.width < 4 or r.height < 4:
            raise FormError(_("Il campo è troppo piccolo."))
        _set_rect(pdf, page, xref, r)
    if kind == BUTTON:
        if is_image_field(pdf, xref):
            # l'icona del campo immagine è già l'aspetto completo: la rigenerazione la perderebbe
            t, v = pdf.xref_get_key(xref, "MK/I")
            if t == "xref":
                pdf.xref_set_key(xref, "AP", f"<</N {v}>>")
            else:
                _regen_field(pdf, fx, page, xref)
        else:
            _regen_field(pdf, fx, page, xref)
            button_appearances(pdf, page, xref)
    elif kind == SIGNATURE:
        if not is_signed(pdf, xref):
            blank_signature_appearance(pdf, page, xref)
    elif kind in (CHECKBOX, RADIO):
        state = _value_of(*pdf.xref_get_key(xref, "AS"))
        regenerate(pdf, page, xref)
        if state is not None:  # la rigenerazione non deve cambiare lo stato
            pdf.xref_set_key(xref, "AS", ps.encode_name(state))
    else:
        _regen_field(pdf, fx, page, xref)
    if "calc" in props or "formula" in props:
        _set_calc_order(pdf)


def _set_rect(pdf: pymupdf.Document, page: int, xref: int, rect: pymupdf.Rect) -> None:
    """Posiziona il widget (coordinate pagina non ruotata, origine in alto a sinistra)."""
    w = find_widget(pdf, page, xref)
    annot = getattr(w, "_annot", None)
    if annot is not None:
        annot.set_rect(rect)
    else:
        w.rect = rect
        w.update()


def _ensure_font_resource(pdf: pymupdf.Document, res: str) -> None:
    """Il font del campo deve essere tra le risorse del modulo (/DR)."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm")[0] == "null":
        return
    if pdf.xref_get_key(cat, f"AcroForm/DR/Font/{res}")[0] != "null":
        return
    base = {"Helv": "Helvetica", "TiRo": "Times-Roman", "Cour": "Courier", "ZaDb": "ZapfDingbats"}.get(res)
    if base is None:
        return
    enc = "" if res == "ZaDb" else "/Encoding/WinAnsiEncoding"
    fx = pdf.get_new_xref()
    pdf.update_object(fx, f"<</Type/Font/Subtype/Type1/BaseFont/{base}{enc}>>")
    pdf.xref_set_key(cat, f"AcroForm/DR/Font/{res}", f"{fx} 0 R")


def _set_action(pdf: pymupdf.Document, fx: int, trigger: str, script: str | None) -> None:
    if script:
        pdf.xref_set_key(fx, f"AA/{trigger}", f"<</S/JavaScript/JS{ps.encode_string(script)}>>")
    elif pdf.xref_get_key(fx, "AA")[0] != "null":
        pdf.xref_set_key(fx, f"AA/{trigger}", "null")


def _set_default(pdf: pymupdf.Document, fx: int, kind: str, value: Any, info: FieldInfo) -> None:
    if value in (None, "", [], False):
        pdf.xref_set_key(fx, "DV", "null")
        return
    if kind == CHECKBOX:
        on = info.export or "Yes"
        pdf.xref_set_key(fx, "DV", ps.encode_name(on if _truthy(value, on) else OFF))
    elif kind == RADIO:
        pdf.xref_set_key(fx, "DV", ps.encode_name(str(value)))
    elif isinstance(value, (list, tuple)):
        pdf.xref_set_key(fx, "DV", ps.serialize([str(v) for v in value]))
    else:
        pdf.xref_set_key(fx, "DV", ps.encode_string(str(value)))


def _acroform_fields(pdf: pymupdf.Document) -> list[int]:
    t, v = pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm/Fields")
    if t == "xref":
        v, t = pdf.xref_object(int(v.split()[0]), compressed=True), "array"
    if t != "array":
        return []
    try:
        return [r[0] for r in ps.parse(v) if isinstance(r, ps.Ref)]
    except ps.PdfSyntaxError:
        return []


def _ensure_acroform(pdf: pymupdf.Document) -> None:
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm")[0] == "null":
        pdf.xref_set_key(cat, "AcroForm", "<</Fields[]/DA(0 g /Helv 0 Tf)>>")
    _ensure_font_resource(pdf, "Helv")


def _children(pdf: pymupdf.Document, parent: int | None) -> list[int]:
    if parent is None:
        return _acroform_fields(pdf)
    t, v = pdf.xref_get_key(parent, "Kids")
    if t != "array":
        return []
    try:
        return [r[0] for r in ps.parse(v) if isinstance(r, ps.Ref)]
    except ps.PdfSyntaxError:
        return []


def _set_children(pdf: pymupdf.Document, parent: int | None, kids: list[int]) -> None:
    value = ps.serialize([ps.Ref(k) for k in kids])
    if parent is None:
        _ensure_acroform(pdf)
        pdf.xref_set_key(pdf.pdf_catalog(), "AcroForm/Fields", value)
    else:
        pdf.xref_set_key(parent, "Kids", value)


def _parent_node(pdf: pymupdf.Document, fx: int) -> int | None:
    t, v = pdf.xref_get_key(fx, "Parent")
    return int(v.split()[0]) if t == "xref" else None


def _rename_field(pdf: pymupdf.Document, fx: int, old_full: str, new_full: str) -> None:
    """Rinomina un campo. Un nome con punti («cliente.nome») sposta il campo sotto
    i nodi genitori corrispondenti, creandoli se servono."""
    parts = [p.strip() for p in new_full.split(".")]
    if any(not p for p in parts):
        raise FormError(_("Il nome «{n}» non è valido: ogni parte tra i punti deve essere non vuota.").format(n=new_full))
    old_parts = old_full.split(".")
    if parts[:-1] == old_parts[:-1]:
        pdf.xref_set_key(fx, "T", ps.encode_string(parts[-1]))
        return
    old_parent = _parent_node(pdf, fx)
    siblings = _children(pdf, old_parent)
    if fx in siblings:
        _set_children(pdf, old_parent, [k for k in siblings if k != fx])
    parent: int | None = None
    for part in parts[:-1]:
        child = None
        for k in _children(pdf, parent):
            if (pdf.xref_get_key(k, "Subtype")[1] != "/Widget"
                    and _value_of(*pdf.xref_get_key(k, "T")) == part and k != fx):
                child = k
                break
        if child is None:
            child = pdf.get_new_xref()
            pdf.update_object(child, f"<</T{ps.encode_string(part)}/Kids[]" +
                              (f"/Parent {parent} 0 R" if parent else "") + ">>")
            _set_children(pdf, parent, _children(pdf, parent) + [child])
        parent = child
    _set_children(pdf, parent, _children(pdf, parent) + [fx])
    pdf.xref_set_key(fx, "Parent", f"{parent} 0 R" if parent else "null")
    pdf.xref_set_key(fx, "T", ps.encode_string(parts[-1]))


def merge_into(pdf: pymupdf.Document, page: int, xref: int, target_name: str) -> None:
    """Unisce il widget al campo `target_name` (stesso tipo): i widget condividono il valore."""
    info = field_info(pdf, page, xref)
    target = next((f for f in list_fields(pdf) if f.name == target_name and f.field_xref != info.field_xref), None)
    if target is None:
        raise FormError(_("Il campo «{n}» non esiste.").format(n=target_name))
    if target.kind != info.kind:
        raise FormError(_("Non si possono unire campi di tipo diverso."))
    if target.field_xref == target.xref or info.field_xref == info.xref:
        raise FormError(_("Questi campi non si possono unire."))
    old = info.field_xref
    kids = _widgets_of_field(pdf, target.field_xref)
    pdf.xref_set_key(target.field_xref, "Kids", ps.serialize([ps.Ref(k) for k in kids + [xref]]))
    pdf.xref_set_key(xref, "Parent", f"{target.field_xref} 0 R")
    rest = [k for k in _widgets_of_field(pdf, old) if k != xref]
    if rest:
        pdf.xref_set_key(old, "Kids", ps.serialize([ps.Ref(k) for k in rest]))
    else:
        parent = _parent_node(pdf, old)
        _set_children(pdf, parent, [k for k in _children(pdf, parent) if k != old])
        pdf.xref_set_key(old, "Kids", "[]")
    _regen_field(pdf, target.field_xref, page, xref)


def delete_field(pdf: pymupdf.Document, page: int, xref: int) -> None:
    untag_widget(pdf, xref)
    w = find_widget(pdf, page, xref)
    fx = _field_xref(pdf, xref)
    pdf[page].delete_widget(w)
    if fx != xref:
        kids = [k for k in _widgets_of_field(pdf, fx) if k != xref and pdf.xref_get_key(k, "Subtype")[0] != "null"]
        if kids and kids != [fx]:
            pdf.xref_set_key(fx, "Kids", ps.serialize([ps.Ref(k) for k in kids]))
        else:
            _remove_from_acroform(pdf, fx)
    _set_calc_order(pdf)


def duplicate_field(pdf: pymupdf.Document, page: int, xref: int, dx: float = 0, dy: float = 0,
                    target_page: int | None = None) -> int:
    """Copia un campo (stesse proprietà, nome nuovo) spostato di (dx, dy)."""
    f = field_info(pdf, page, xref)
    r = pymupdf.Rect(f.rect)
    r = pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy)
    props = properties_of(f)
    name = f.name if f.kind == RADIO else unique_name(pdf, f.name)
    if f.kind == RADIO:
        props.pop("export", None)
    if f.kind == BUTTON:   # le icone si condividono: stesso XObject
        for state in f.icons:
            props[f"icon_{state}"] = int(pdf.xref_get_key(xref, f"MK/{ICON_KEYS[state]}")[1].split()[0])
    return create_field(pdf, page if target_page is None else target_page, f.kind, r, name, **props)


def properties_of(f: FieldInfo) -> dict:
    """Proprietà di un campo nel formato accettato da create_field/update_properties."""
    props: dict = dict(tooltip=f.tooltip, readonly=f.readonly, required=f.required, font=f.font,
                       fontsize=f.fontsize, text_color=f.text_color, fill_color=f.fill_color,
                       border_color=f.border_color, border_width=f.border_width, border_style=f.border_style,
                       align=f.align)
    if f.kind == TEXT:
        props.update(multiline=f.multiline, password=f.password, maxlen=f.maxlen, comb=f.comb, fmt=f.fmt,
                     validation=f.validation)
        if f.calc:
            props["calc"] = f.calc
    if f.kind in (COMBO, LIST):
        props.update(options=list(f.options), multiselect=f.multiselect, editable=f.editable)
    if f.kind in (CHECKBOX, RADIO):
        props["export"] = f.export
    if f.kind == BUTTON and not f.image:
        props.update(caption=f.caption, caption_down=f.caption_down, caption_rollover=f.caption_rollover,
                     layout=f.layout)
        if f.action:
            props["action"] = dict(f.action)
    return props


# ====================================================================== disposizione
def align_rects(rects: list[pymupdf.Rect], how: str, reference: int | None = None) -> list[pymupdf.Rect]:
    """Allinea i rettangoli. `how`: left, right, top, bottom, hcenter, vcenter,
    same_width, same_height, same_size. Il riferimento è il rettangolo indicato
    (predefinito il primo)."""
    if not rects:
        return []
    ref = pymupdf.Rect(rects[reference or 0])
    out = []
    for r in rects:
        r = pymupdf.Rect(r)
        w, h = r.width, r.height
        if how == "left":
            r = pymupdf.Rect(ref.x0, r.y0, ref.x0 + w, r.y1)
        elif how == "right":
            r = pymupdf.Rect(ref.x1 - w, r.y0, ref.x1, r.y1)
        elif how == "top":
            r = pymupdf.Rect(r.x0, ref.y0, r.x1, ref.y0 + h)
        elif how == "bottom":
            r = pymupdf.Rect(r.x0, ref.y1 - h, r.x1, ref.y1)
        elif how == "hcenter":
            cx = (ref.x0 + ref.x1) / 2
            r = pymupdf.Rect(cx - w / 2, r.y0, cx + w / 2, r.y1)
        elif how == "vcenter":
            cy = (ref.y0 + ref.y1) / 2
            r = pymupdf.Rect(r.x0, cy - h / 2, r.x1, cy + h / 2)
        elif how == "same_width":
            r = pymupdf.Rect(r.x0, r.y0, r.x0 + ref.width, r.y1)
        elif how == "same_height":
            r = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + ref.height)
        elif how == "same_size":
            r = pymupdf.Rect(r.x0, r.y0, r.x0 + ref.width, r.y0 + ref.height)
        else:
            raise ValueError(how)
        out.append(r)
    return out


def distribute_rects(rects: list[pymupdf.Rect], axis: str) -> list[pymupdf.Rect]:
    """Distribuisce con spazi uguali (axis «h» orizzontale, «v» verticale); gli estremi restano fermi."""
    rects = [pymupdf.Rect(r) for r in rects]
    if len(rects) < 3:
        return rects
    horizontal = axis == "h"
    order = sorted(range(len(rects)), key=lambda i: rects[i].x0 if horizontal else rects[i].y0)
    first, last = rects[order[0]], rects[order[-1]]
    if horizontal:
        total = last.x1 - first.x0
        sizes = sum(rects[i].width for i in order)
    else:
        total = last.y1 - first.y0
        sizes = sum(rects[i].height for i in order)
    gap = (total - sizes) / (len(rects) - 1)
    pos = first.x0 if horizontal else first.y0
    out = list(rects)
    for i in order:
        r = rects[i]
        if horizontal:
            out[i] = pymupdf.Rect(pos, r.y0, pos + r.width, r.y1)
            pos += r.width + gap
        else:
            out[i] = pymupdf.Rect(r.x0, pos, r.x1, pos + r.height)
            pos += r.height + gap
    return out


def move_fields(pdf: pymupdf.Document, changes: list[tuple[int, int, pymupdf.Rect]]) -> None:
    """Applica nuovi rettangoli: lista di (pagina, xref, rettangolo)."""
    for page, xref, rect in changes:
        update_properties(pdf, page, xref, rect=rect)


def tab_order(pdf: pymupdf.Document, page: int) -> list[int]:
    from .scan import widget_xrefs
    return widget_xrefs(pdf, page)


def set_tab_order(pdf: pymupdf.Document, page: int, xrefs: list[int]) -> None:
    """Ordine di tabulazione dei campi della pagina = ordine dei widget in /Annots."""
    pg = pdf[page]
    t, v = pdf.xref_get_key(pg.xref, "Annots")
    if t == "xref":
        arr_xref = int(v.split()[0])
        raw = pdf.xref_object(arr_xref, compressed=True)
    elif t == "array":
        arr_xref, raw = 0, v
    else:
        return
    try:
        refs = [r for r in ps.parse(raw) if isinstance(r, ps.Ref)]
    except ps.PdfSyntaxError:
        raise FormError(_("Elenco delle annotazioni della pagina non leggibile."))
    widgets = set(tab_order(pdf, page))
    others = [r for r in refs if r[0] not in widgets]
    ordered = [ps.Ref(x) for x in xrefs if x in widgets]
    ordered += [r for r in refs if r[0] in widgets and r[0] not in xrefs]
    new = ps.serialize(others + ordered)
    if arr_xref:
        pdf.update_object(arr_xref, new)
    else:
        pdf.xref_set_key(pg.xref, "Annots", new)
    version = (pdf.metadata or {}).get("format", "")
    pdf.xref_set_key(pg.xref, "Tabs", "/A" if "2.0" in version else "null")


def auto_tab_order(pdf: pymupdf.Document, page: int, by: str = "row") -> list[int]:
    """Ordine per righe o per colonne (sull'orientamento visualizzato): dichiarato in /Tabs
    e applicato anche all'array delle annotazioni per i lettori che lo ignorano."""
    rot = pdf[page].rotation_matrix
    fields = sort_geometric(list_fields(pdf, page), by, key=lambda f: pymupdf.Rect(f.rect) * rot)
    order = [f.xref for f in fields]
    set_tab_order(pdf, page, order)
    pdf.xref_set_key(pdf.page_xref(page), "Tabs", "/C" if by == "column" else "/R")
    return order


def tab_mode(pdf: pymupdf.Document, page: int) -> str:
    """'row', 'column', 'structure' oppure 'manual' (ordine delle annotazioni)."""
    tabs = _value_of(*pdf.xref_get_key(pdf.page_xref(page), "Tabs"))
    return {"R": "row", "C": "column", "S": "structure"}.get(tabs or "", "manual")


# ====================================================================== appiattimento e XFA
def flatten(pdf: pymupdf.Document) -> int:
    """Appiattisce tutti i campi (vedi flatten_pages)."""
    return flatten_pages(pdf, None)


def xfa_status(pdf: pymupdf.Document) -> str:
    """'none' senza XFA; 'hybrid' con XFA e campi AcroForm; 'dynamic' con solo XFA."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm/XFA")[0] == "null":
        return "none"
    from .scan import widget_xrefs
    has_widgets = any(widget_xrefs(pdf, i) for i in range(pdf.page_count))
    return "hybrid" if has_widgets else "dynamic"


def _xfa_packets(pdf: pymupdf.Document) -> list[tuple[str, int]]:
    cat = pdf.pdf_catalog()
    t, v = pdf.xref_get_key(cat, "AcroForm/XFA")
    if t == "xref":
        return [("", int(v.split()[0]))]
    if t != "array":
        return []
    try:
        arr = ps.parse(v)
    except ps.PdfSyntaxError:
        return []
    out = []
    for i in range(0, len(arr) - 1, 2):
        if isinstance(arr[i + 1], ps.Ref):
            out.append((ps.text_of(arr[i]) or "", arr[i + 1][0]))
    return out


def _datasets(pdf: pymupdf.Document):
    import xml.etree.ElementTree as ET
    for name, xref in _xfa_packets(pdf):
        try:
            data = pdf.xref_stream(xref)
        except Exception:
            continue
        if name == "datasets" or (not name and b"datasets" in data):
            if not name:
                m = re.search(rb"<xfa:datasets.*?</xfa:datasets>", data, re.S)
                if not m:
                    continue
                data = m.group(0)
            try:
                return xref, ET.fromstring(data), name
            except ET.ParseError:
                return None
    return None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def xfa_data(pdf: pymupdf.Document) -> dict[str, str]:
    """Valori dei dati XFA (percorso con punti -> testo) per i nodi foglia."""
    found = _datasets(pdf)
    if not found:
        return {}
    _x, root, _n = found
    data = next((el for el in root.iter() if _local(el.tag) == "data"), None)
    if data is None:
        return {}
    out: dict[str, str] = {}

    def walk(el, path):
        children = list(el)
        if not children:
            out[".".join(path)] = el.text or ""
            return
        counts: dict[str, int] = {}
        for c in children:
            tag = _local(c.tag)
            n = counts.get(tag, 0)
            counts[tag] = n + 1
            walk(c, path + [tag if n == 0 else f"{tag}[{n}]"])

    for c in data:
        walk(c, [_local(c.tag)])
    return out


def set_xfa_data(pdf: pymupdf.Document, values: dict[str, str]) -> int:
    """Scrive valori nei dati XFA (percorsi come in xfa_data). Restituisce quanti nodi cambiati."""
    import xml.etree.ElementTree as ET
    found = _datasets(pdf)
    if not found:
        return 0
    xref, root, name = found
    data = next((el for el in root.iter() if _local(el.tag) == "data"), None)
    if data is None:
        return 0
    nodes: dict[str, Any] = {}

    def walk(el, path):
        children = list(el)
        if not children:
            nodes[".".join(path)] = el
            return
        counts: dict[str, int] = {}
        for c in children:
            tag = _local(c.tag)
            n = counts.get(tag, 0)
            counts[tag] = n + 1
            walk(c, path + [tag if n == 0 else f"{tag}[{n}]"])

    for c in data:
        walk(c, [_local(c.tag)])
    changed = 0
    for path, value in values.items():
        el = nodes.get(path)
        if el is not None and (el.text or "") != str(value):
            el.text = str(value)
            changed += 1
    if changed:
        ET.register_namespace("xfa", "http://www.xfa.org/schema/xfa-data/1.0/")
        new = ET.tostring(root, encoding="utf-8")
        if not name:
            old = pdf.xref_stream(xref)
            new = re.sub(rb"<xfa:datasets.*?</xfa:datasets>", lambda _m: new, old, count=1, flags=re.S)
        pdf.update_stream(xref, new)
    return changed


def _leaf(name: str) -> str:
    return re.sub(r"\[\d+\]", "", name.rsplit(".", 1)[-1])


def _xfa_map(pdf: pymupdf.Document) -> tuple[dict[str, str], dict[str, FieldInfo]]:
    """(percorso XML per nome di campo, campo per nome) dei campi associabili ai dati XML."""
    data = xfa_data(pdf)
    by_leaf: dict[str, str] = {}
    for path in data:
        by_leaf.setdefault(_leaf(path), path)
    paths, fields = {}, {}
    for f in list_fields(pdf):
        if f.kind not in FILLABLE or f.name in paths:
            continue
        path = by_leaf.get(_leaf(f.name))
        if path is not None:
            paths[f.name] = path
            fields[f.name] = f
    return paths, fields


def xfa_unmapped(pdf: pymupdf.Document) -> list[str]:
    """Campi compilabili di un modulo XML statico senza un nodo dati corrispondente."""
    if not xfa_data(pdf):
        return []
    paths, _fields = _xfa_map(pdf)
    names = list(dict.fromkeys(f.name for f in list_fields(pdf) if f.kind in FILLABLE))
    return [n for n in names if n not in paths]


def sync_xfa_from_fields(pdf: pymupdf.Document) -> int:
    """Nei moduli XML statici copia i valori dei campi nei dati XML (date e ore in forma ISO)."""
    paths, fields = _xfa_map(pdf)
    if not paths:
        return 0
    values = form_values(pdf)
    updates = {}
    for name, path in paths.items():
        value = values.get(name, "")
        f = fields[name]
        if isinstance(value, list):
            value = "\n".join(value)
        if f.kind in (CHECKBOX, RADIO) and value == OFF:
            value = "0" if f.kind == CHECKBOX else ""
        elif f.kind == CHECKBOX:
            value = "1"
        if f.kind == TEXT and value:
            value = fm.to_iso(f.fmt, value)
        updates[path] = value
    return set_xfa_data(pdf, updates)


def fields_from_xfa(pdf: pymupdf.Document) -> dict[str, Any]:
    """Valori dei dati XML diversi da quelli dei campi (all'apertura prevalgono i dati XML)."""
    paths, fields = _xfa_map(pdf)
    data = xfa_data(pdf)
    out: dict[str, Any] = {}
    for name, path in paths.items():
        f = fields[name]
        raw = data.get(path, "")
        if f.kind == TEXT and f.fmt.type in (fm.DATE, fm.TIME) and raw:
            try:
                raw = fm.normalize(f.fmt, raw)
            except fm.FormatError:
                pass
        if f.kind == CHECKBOX:
            value: Any = (f.export or "Yes") if raw.strip() in ("1", "true", f.export or "Yes") else OFF
        elif f.kind == RADIO:
            value = raw or OFF
        else:
            value = raw
        if export_value(f) != value and not (f.kind in (TEXT, COMBO) and not raw and not f.value):
            out[name] = value
    return out


def export_xfa_data(pdf: pymupdf.Document) -> bytes:
    """Nodo dei dati del pacchetto XML (xfa:data) come documento XML."""
    import xml.etree.ElementTree as ET
    found = _datasets(pdf)
    if not found:
        raise FormError(_("Il documento non contiene dati XML."))
    data = next((el for el in found[1].iter() if _local(el.tag) == "data"), None)
    if data is None:
        raise FormError(_("Il documento non contiene dati XML."))
    ET.register_namespace("xfa", "http://www.xfa.org/schema/xfa-data/1.0/")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(data, encoding="utf-8")


def import_xfa_data(pdf: pymupdf.Document, xml_bytes: bytes) -> None:
    """Sostituisce il nodo dei dati del pacchetto XML; gli altri pacchetti restano identici."""
    import xml.etree.ElementTree as ET
    try:
        new = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        line, col = e.position
        raise FormError(_("Il file XML non è ben formato (riga {l}, colonna {c}).").format(l=line, c=col + 1)) from e
    found = _datasets(pdf)
    if not found:
        raise FormError(_("Il documento non contiene dati XML."))
    xref, root, name = found
    if _local(new.tag) != "data":
        wrapper = ET.Element("{http://www.xfa.org/schema/xfa-data/1.0/}data")
        wrapper.append(new)
        new = wrapper
    old = next((el for el in root if _local(el.tag) == "data"), None)
    if old is not None:
        idx = list(root).index(old)
        root.remove(old)
        root.insert(idx, new)
    else:
        root.insert(0, new)
    ET.register_namespace("xfa", "http://www.xfa.org/schema/xfa-data/1.0/")
    body = ET.tostring(root, encoding="utf-8")
    if not name:
        old_stream = pdf.xref_stream(xref)
        body = re.sub(rb"<xfa:datasets.*?</xfa:datasets>", lambda _m: body, old_stream, count=1, flags=re.S)
    pdf.update_stream(xref, body)


def remove_xfa(pdf: pymupdf.Document) -> bool:
    """Elimina la parte XFA: i lettori useranno i campi AcroForm."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm/XFA")[0] == "null":
        return False
    pdf.xref_set_key(cat, "AcroForm/XFA", "null")
    return True


# ====================================================================== pulsanti e immagini
def _set_path(pdf: pymupdf.Document, xref: int, path: str, value: str) -> None:
    """Come xref_set_key ma risolve i riferimenti indiretti lungo il percorso."""
    parts = path.split("/")
    x = xref
    for i, key in enumerate(parts[:-1]):
        t, v = pdf.xref_get_key(x, key)
        if t == "xref":
            x = int(v.split()[0])
            continue
        rest = "/".join(parts[i:])
        pdf.xref_set_key(x, rest, value)
        return
    pdf.xref_set_key(x, parts[-1], value)


def mark_image_field(pdf: pymupdf.Document, page: int, xref: int) -> None:
    """Campo immagine: pulsante che mostra solo l'icona (/MK /TP 1)."""
    pdf.xref_set_key(xref, "MK/TP", "1")
    pdf.xref_set_key(xref, "MK/IF", "<</SW/A/S/P/A[0.5 0.5]>>")


def is_image_field(pdf: pymupdf.Document, xref: int) -> bool:
    """Campo immagine: pulsante che mostra solo l'icona e si compila scegliendo l'immagine.
    Non lo è un pulsante con icone per stato o con un'azione vera (FRM-18), né uno a cui
    Foliant ha dato un'icona come pulsante (/FoliantButton)."""
    if _inherited(pdf, xref, "FT")[1] != "/Btn" or pdf.xref_get_key(xref, "MK/TP") != ("int", "1"):
        return False
    if pdf.xref_get_key(xref, "FoliantButton")[0] != "null":
        return False
    if pdf.xref_get_key(xref, "MK/RI")[0] != "null" or pdf.xref_get_key(xref, "MK/IX")[0] != "null":
        return False
    return button_action(pdf, xref).get("type") in (None, "javascript")


def set_button_image(pdf: pymupdf.Document, page: int, xref: int, image: str | bytes, dpi: int = 200) -> None:
    """Inserisce un'immagine nel campo immagine: proporzionale, centrata, ridotta alla
    risoluzione utile (`dpi`) così che il file non cresca inutilmente."""
    import io

    from PIL import Image

    info = field_info(pdf, page, xref)
    w, h = info.rect.width, info.rect.height
    try:
        img = Image.open(io.BytesIO(image) if isinstance(image, bytes) else image)
        img.load()
    except Exception as e:
        raise FormError(_("Impossibile leggere l'immagine: {e}").format(e=e)) from e
    alpha = img.mode in ("RGBA", "LA", "P") and ("transparency" in img.info or img.mode in ("RGBA", "LA"))
    img = img.convert("RGBA" if alpha else "RGB")
    max_px = (max(1, int(w / 72 * dpi)), max(1, int(h / 72 * dpi)))
    img.thumbnail(max_px, Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    if alpha:
        img.save(buf, "PNG", optimize=True)
    else:
        img.save(buf, "JPEG", quality=88)
    scale = min(w / img.width, h / img.height)
    iw, ih = img.width * scale, img.height * scale
    ox, oy = (w - iw) / 2, (h - ih) / 2
    # immagine come XObject: inserita in una pagina provvisoria, poi riusata
    tmp = pdf.new_page(width=max(w, 10), height=max(h, 10))
    tmp.insert_image(pymupdf.Rect(0, 0, iw, ih), stream=buf.getvalue())
    img_xref = tmp.get_images(full=True)[0][0]
    pdf.delete_page(tmp.number)
    bg = info.fill_color
    ops = []
    if bg:
        ops.append(f"{_n(bg[0])} {_n(bg[1])} {_n(bg[2])} rg 0 0 {_n(w)} {_n(h)} re f")
    ops.append(f"q {_n(iw)} 0 0 {_n(ih)} {_n(ox)} {_n(oy)} cm /Img Do Q")
    form = pdf.get_new_xref()
    pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_n(w)} {_n(h)}]"
                            f"/Resources<</XObject<</Img {img_xref} 0 R>>>>>>")
    pdf.update_stream(form, "\n".join(ops).encode())
    pdf.xref_set_key(xref, "AP", f"<</N {form} 0 R>>")
    pdf.xref_set_key(xref, "MK/I", f"{form} 0 R")


# Eventi delle azioni dei pulsanti: rilascio (/A o /AA /U), pressione, ingresso e uscita del puntatore,
# focus e perdita del focus.
BUTTON_EVENTS = ("U", "D", "E", "X", "Fo", "Bl")


def _event_action(pdf: pymupdf.Document, xref: int, event: str) -> tuple[str, str]:
    if event == "U":
        t, v = pdf.xref_get_key(xref, "A")
        if t != "null":
            return t, v
    return pdf.xref_get_key(xref, f"AA/{event}")


def button_action(pdf: pymupdf.Document, xref: int, event: str | None = None) -> dict:
    """Azione di un pulsante come dizionario semplice, con l'evento che la avvia («event»).
    Senza `event`: la prima azione presente, a partire dal rilascio."""
    if event is None:
        for ev in BUTTON_EVENTS:
            a = button_action(pdf, xref, ev)
            if a:
                return a
        return {}
    a = _button_action_at(pdf, xref, event)
    if a and event != "U":
        a["event"] = event
    return a


def _button_action_at(pdf: pymupdf.Document, xref: int, event: str) -> dict:
    t, v = _event_action(pdf, xref, event)
    if t == "xref":
        v = pdf.xref_object(int(v.split()[0]), compressed=True)
        t = "dict"
    if t != "dict":
        return {}
    try:
        a = ps.parse(v)
    except ps.PdfSyntaxError:
        return {}
    kind = str(a.get("S", ""))

    def names(items):
        out = []
        for it in items or []:
            if isinstance(it, ps.Ref):
                out.append(_full_name(pdf, it[0]))
            else:
                out.append(ps.text_of(it) or "")
        return [n for n in out if n]

    if kind == "ResetForm":
        flags = a.get("Flags", 0) or 0
        fields = names(a.get("Fields"))
        if fields and flags & 1:  # escludi i campi elencati
            fields = [n for n in dict.fromkeys(f.name for f in list_fields(pdf)) if n not in fields]
        return {"type": "reset", "fields": fields}
    if kind == "URI":
        return {"type": "uri", "uri": ps.text_of(a.get("URI")) or ""}
    if kind == "Named":
        return {"type": "print" if str(a.get("N")) == "Print" else "named", "name": str(a.get("N"))}
    if kind == "GoTo":
        dest = a.get("D")
        if isinstance(dest, list) and dest and isinstance(dest[0], ps.Ref):
            for i in range(pdf.page_count):
                if pdf.page_xref(i) == dest[0][0]:
                    return {"type": "goto", "page": i}
        return {"type": "goto", "page": 0}
    if kind == "Hide":
        target = a.get("T")
        items = target if isinstance(target, list) else [target]
        return {"type": "hide", "fields": names(items), "hide": a.get("H", True) is not False}
    if kind == "ImportData":
        return {"type": "import"}
    if kind == "SubmitForm":
        target = a.get("F")
        if isinstance(target, dict):
            target = target.get("F") or target.get("UF")
        flags = int(a.get("Flags", 0) or 0)
        fmt = "xfdf" if flags & 32 else ("html" if flags & 4 else ("pdf" if flags & 256 else "fdf"))
        fields = names(a.get("Fields"))
        if fields and flags & 1:
            fields = [n for n in dict.fromkeys(f.name for f in list_fields(pdf)) if n not in fields]
        return {"type": "submit", "url": ps.text_of(target) or "", "format": fmt, "fields": fields,
                "empty": bool(flags & 2)}
    if kind == "JavaScript":
        return {"type": "javascript"}
    return {"type": kind.lower()} if kind else {}


def set_button_action(pdf: pymupdf.Document, page: int, xref: int, action: dict | None) -> None:
    """Imposta l'azione del pulsante sull'evento indicato in `action["event"]` (predefinito il
    rilascio) e toglie le azioni predefinite degli altri eventi; gli script restano."""
    kind = (action or {}).get("type")
    event = (action or {}).get("event") or "U"
    if event not in BUTTON_EVENTS:
        raise FormError(_("Evento del pulsante non valido."))
    for ev in BUTTON_EVENTS:
        t, v = _event_action(pdf, xref, ev)
        if t != "null" and "/JavaScript" not in (v if t != "xref" else pdf.xref_object(int(v.split()[0]))):
            pdf.xref_set_key(xref, "A" if ev == "U" and pdf.xref_get_key(xref, "A")[0] != "null" else f"AA/{ev}",
                             "null")
    if not kind:
        return
    if kind == "reset":
        fields = action.get("fields") or []
        value = "<</S/ResetForm" + (f"/Fields{ps.serialize(list(fields))}" if fields else "") + ">>"
    elif kind == "uri":
        value = f"<</S/URI/URI{ps.encode_string(action.get('uri', ''))}>>"
    elif kind == "print":
        value = "<</S/Named/N/Print>>"
    elif kind == "goto":
        value = f"<</S/GoTo/D[{pdf.page_xref(int(action.get('page', 0)))} 0 R/Fit]>>"
    elif kind == "hide":
        value = f"<</S/Hide/T{ps.serialize(list(action.get('fields') or []))}/H {'true' if action.get('hide', True) else 'false'}>>"
    elif kind == "import":
        value = "<</S/ImportData/F()>>"
    elif kind == "submit":
        flags = {"xfdf": 32, "html": 4, "pdf": 256}.get(action.get("format", "fdf"), 0)
        if action.get("empty"):
            flags |= 2
        fields = action.get("fields") or []
        value = (f"<</S/SubmitForm/F<</FS/URL/F{ps.encode_string(action.get('url', ''))}>>/Flags {flags}"
                 + (f"/Fields{ps.serialize(list(fields))}" if fields else "") + ">>")
    else:
        raise FormError(_("Azione non supportata."))
    pdf.xref_set_key(xref, "A" if event == "U" else f"AA/{event}", value)


def _full_name(pdf: pymupdf.Document, xref: int) -> str:
    parts = []
    seen = set()
    x = xref
    while x and x not in seen:
        seen.add(x)
        t = _value_of(*pdf.xref_get_key(x, "T"))
        if t:
            parts.insert(0, t)
        x = _parent_node(pdf, x) or 0
    return ".".join(parts)


def hide_fields(pdf: pymupdf.Document, names: list[str], hide: bool = True) -> int:
    n = 0
    for f in list_fields(pdf):
        if f.name in names:
            af = _annot_flags(pdf, f.xref)
            af = (af | AF_HIDDEN) if hide else (af & ~AF_HIDDEN)
            pdf.xref_set_key(f.xref, "F", str(af))
            n += 1
    return n


def copy_field_to_page(pdf: pymupdf.Document, page: int, xref: int, target: int, shared: bool) -> int:
    """Copia un campo su un'altra pagina, con valore condiviso (stesso campo) o indipendente."""
    f = field_info(pdf, page, xref)
    if not shared:
        return duplicate_field(pdf, page, xref, 0, 0, target_page=target)
    props = properties_of(f)
    props.pop("export", None)
    new = create_field(pdf, target, f.kind, f.rect, unique_name(pdf, f.name + "_copia"), **props)
    merge_into(pdf, target, new, f.name)
    return new


def is_signed(pdf: pymupdf.Document, xref: int) -> bool:
    return _inherited(pdf, xref, "FT")[1] == "/Sig" and _inherited(pdf, xref, "V")[0] != "null"


def _resources_xref(pdf: pymupdf.Document, page_xref: int) -> int:
    t, v = pdf.xref_get_key(page_xref, "Resources")
    if t == "xref":
        return int(v.split()[0])
    if t == "null":
        it, iv = _inherited(pdf, page_xref, "Resources")
        body = pdf.xref_object(int(iv.split()[0]), compressed=True) if it == "xref" else (iv if it == "dict" else "<<>>")
    else:
        body = v
    x = pdf.get_new_xref()
    pdf.update_object(x, body)
    pdf.xref_set_key(page_xref, "Resources", f"{x} 0 R")
    return x


def _ap_matrix(pdf: pymupdf.Document, ap: int, rect_pdf: pymupdf.Rect) -> pymupdf.Matrix:
    """Matrice che porta il riquadro dell'aspetto sul /Rect del widget (algoritmo standard)."""
    try:
        bbox = pymupdf.Rect(ps.parse(pdf.xref_get_key(ap, "BBox")[1]))
    except Exception:
        bbox = pymupdf.Rect(0, 0, rect_pdf.width, rect_pdf.height)
    t, v = pdf.xref_get_key(ap, "Matrix")
    mat = pymupdf.Matrix(*ps.parse(v)) if t == "array" else pymupdf.Matrix(1, 0, 0, 1, 0, 0)
    tb = pymupdf.Rect(bbox.quad.transform(mat).rect)
    if tb.width <= 0 or tb.height <= 0:
        return pymupdf.Matrix(1, 0, 0, 1, rect_pdf.x0, rect_pdf.y0)
    sx, sy = rect_pdf.width / tb.width, rect_pdf.height / tb.height
    return pymupdf.Matrix(sx, 0, 0, sy, rect_pdf.x0 - tb.x0 * sx, rect_pdf.y0 - tb.y0 * sy)


def flatten_pages(pdf: pymupdf.Document, pages: list[int] | None = None, keep_signed: bool = True) -> int:
    """Appiattisce i campi delle pagine indicate (tutte se None): l'aspetto diventa contenuto
    della pagina, i widget e i campi vengono rimossi. I campi nascosti scompaiono; i campi
    firma firmati restano campi se `keep_signed`."""
    targets = range(pdf.page_count) if pages is None else [p for p in pages if 0 <= p < pdf.page_count]
    count = 0
    for pno in targets:
        pg = pdf[pno]
        widgets = [w for w in pg.widgets()]
        if not widgets:
            continue
        pg.wrap_contents()
        res = None
        ops = []
        to_delete = []
        for w in widgets:
            if keep_signed and is_signed(pdf, w.xref):
                continue
            to_delete.append(w.xref)
            af = _annot_flags(pdf, w.xref)
            if af & (AF_HIDDEN | AF_NOVIEW) and not af & AF_PRINT:
                continue
            if af & AF_HIDDEN:
                continue
            t, v = pdf.xref_get_key(w.xref, "AP/N")
            ap = None
            if t == "xref":
                ap = int(v.split()[0])
            elif t == "dict":
                state = _value_of(*pdf.xref_get_key(w.xref, "AS")) or OFF
                try:
                    states = ps.parse(v)
                except ps.PdfSyntaxError:
                    states = {}
                ref = states.get(state)
                ap = ref[0] if isinstance(ref, ps.Ref) else None
            if not ap:
                continue
            try:
                rect = pymupdf.Rect(ps.parse(pdf.xref_get_key(w.xref, "Rect")[1])).normalize()
            except Exception:
                continue
            if res is None:
                res = _resources_xref(pdf, pg.xref)
            name = f"FlW{w.xref}"
            _set_path(pdf, res, f"XObject/{name}", f"{ap} 0 R")
            m = _ap_matrix(pdf, ap, rect)
            ops.append(f"q {_n(m.a)} {_n(m.b)} {_n(m.c)} {_n(m.d)} {_n(m.e)} {_n(m.f)} cm /{name} Do Q")
        if ops:
            sx = pdf.get_new_xref()
            pdf.update_object(sx, "<<>>")
            pdf.update_stream(sx, ("\n".join(ops) + "\n").encode())
            t, v = pdf.xref_get_key(pg.xref, "Contents")
            if t == "array":
                arr = v[:-1] + f" {sx} 0 R]"
            elif t == "xref":
                arr = f"[{v} {sx} 0 R]"
            else:
                arr = f"[{sx} 0 R]"
            pdf.xref_set_key(pg.xref, "Contents", arr)
        for x in to_delete:
            fx = _field_xref(pdf, x)
            try:
                pg.delete_widget(find_widget(pdf, pno, x))
            except FormError:
                continue
            if fx != x:
                kids = [k for k in _widgets_of_field(pdf, fx) if k != x]
                if kids:
                    pdf.xref_set_key(fx, "Kids", ps.serialize([ps.Ref(k) for k in kids]))
                else:
                    _remove_from_acroform(pdf, fx)
                    _prune_empty_parents(pdf, fx)
            count += 1
    from .scan import widget_xrefs
    if not any(widget_xrefs(pdf, i) for i in range(pdf.page_count)):
        cat = pdf.pdf_catalog()
        if pdf.xref_get_key(cat, "AcroForm")[0] != "null":
            pdf.xref_set_key(cat, "AcroForm", "null")
    else:
        _set_calc_order(pdf)
    return count


def _prune_empty_parents(pdf: pymupdf.Document, fx: int) -> None:
    parent = _parent_node(pdf, fx)
    while parent is not None:
        if _children(pdf, parent):
            return
        nxt = _parent_node(pdf, parent)
        _remove_from_acroform(pdf, parent)
        parent = nxt


# ====================================================================== caratteri incorporati
def has_rtl(text: str) -> bool:
    """True se il testo contiene caratteri di scritture da destra a sinistra."""
    import unicodedata
    return any(unicodedata.bidirectional(c) in ("R", "AL") for c in text)


def _rtl_text(tp, info, text: str, color, w: float, h: float) -> None:
    from html import escape
    size = info.fontsize or (10 if info.multiline else min(12.0, h * 0.6))
    rgb = "#%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in color[:3])
    align = {ALIGN_LEFT: "left", ALIGN_CENTER: "center", ALIGN_RIGHT: "right"}.get(info.align, "right")
    if info.align == ALIGN_LEFT:
        align = "right"  # nei testi da destra a sinistra l'inizio è a destra
    style = f"font-size:{size:.1f}px;color:{rgb};text-align:{align};line-height:1.15;margin:0"
    # un paragrafo per riga: il motore HTML applica l'algoritmo bidirezionale e la composizione
    html = "".join(f'<p style="{style}">{escape(line) or "&nbsp;"}</p>' for line in text.split("\n"))
    box = pymupdf.Rect(2, 1, w - 2, h - 1)
    if not info.multiline:
        box = pymupdf.Rect(2, max(0.0, (h - size * 1.3) / 2), w - 2, h)
    tp.insert_htmlbox(box, html, scale_low=0.3)


def needs_embedded_font(text: str) -> bool:
    """True se il testo contiene caratteri non rappresentabili con i caratteri standard (WinAnsi)."""
    try:
        text.replace("\r", "").replace("\n", "").encode("cp1252")
        return False
    except UnicodeEncodeError:
        return True


_FALLBACK_FONT = None
_SYSTEM_FONT_CACHE: dict[str, str | None] = {}


def system_font_file(family: str) -> str | None:
    """File del carattere di sistema con questo nome (fontconfig); None se non installato."""
    if family in _SYSTEM_FONT_CACHE:
        return _SYSTEM_FONT_CACHE[family]
    import shutil
    import subprocess
    path = None
    if shutil.which("fc-match"):
        try:
            out = subprocess.run(["fc-match", "-f", "%{family}\t%{file}", family], capture_output=True,
                                 text=True, timeout=5).stdout
            found, _tab, file = out.partition("\t")
            names = [n.strip().lower() for n in found.split(",")]
            if family.strip().lower() in names and file.lower().endswith((".ttf", ".otf", ".ttc")):
                path = file
        except (OSError, subprocess.SubprocessError):
            path = None
    _SYSTEM_FONT_CACHE[family] = path
    return path


def _field_font(pdf: pymupdf.Document, xref: int) -> pymupdf.Font:
    """Carattere da incorporare per il campo: quello di sistema scelto o il carattere di ripiego."""
    family = _value_of(*pdf.xref_get_key(xref, "FoliantFont"))
    if family:
        path = system_font_file(family)
        if path:
            try:
                return pymupdf.Font(fontfile=path)
            except Exception:
                pass
    return _fallback_font()


def _fallback_font() -> pymupdf.Font:
    global _FALLBACK_FONT
    if _FALLBACK_FONT is None:
        _FALLBACK_FONT = pymupdf.Font("cjk")
    return _FALLBACK_FONT


def embedded_appearance(pdf: pymupdf.Document, page: int, xref: int, text: str) -> None:
    """Aspetto del campo con un carattere incorporato (sottoinsieme) che copre tutti i glifi
    del valore: serve per testi in alfabeti non latini, che altri lettori potrebbero non
    saper mostrare con i caratteri standard (FRM-09)."""
    info = field_info(pdf, page, xref)
    w, h = info.rect.width, info.rect.height
    font = _field_font(pdf, xref)
    if any(not font.has_glyph(ord(c)) for c in text if c not in "\r\n\t"):
        font = _fallback_font()  # il carattere scelto non copre il testo
    tmp = pymupdf.open()
    try:
        tp = tmp.new_page(width=w, height=h)
        if info.fill_color:
            tp.draw_rect(pymupdf.Rect(0, 0, w, h), color=None, fill=info.fill_color, width=0)
        if info.border_color and info.border_width:
            bw = info.border_width
            tp.draw_rect(pymupdf.Rect(bw / 2, bw / 2, w - bw / 2, h - bw / 2), color=info.border_color,
                         width=bw, dashes="[3 2] 0" if info.border_style == "D" else None)
        color = tuple(info.text_color or (0, 0, 0))
        lines = text.replace("\r\n", "\n").replace("\r", "\n")
        if has_rtl(lines):
            # testo da destra a sinistra (arabo, ebraico…): composizione con il motore HTML,
            # che applica l'ordine bidirezionale e le forme contestuali delle lettere
            _rtl_text(tp, info, lines, color, w, h)
            tmp.subset_fonts()
            data = tmp.tobytes(garbage=3, deflate=True)
            lines = None
        else:
            tp.insert_font(fontname="FoliantU", fontbuffer=font.buffer)
        if lines is None:
            pass
        elif info.multiline:
            size = info.fontsize or 10
            box = pymupdf.Rect(2, 2, w - 2, h - 2)
            while tp.insert_textbox(box, lines, fontname="FoliantU", fontsize=size, color=color,
                                    align=info.align) < 0 and size > 4 and not info.fontsize:
                size -= 1
                tp.clean_contents()
        else:
            line = lines.replace("\n", " ")
            size = info.fontsize or min(12.0, h * 0.7)
            if not info.fontsize:
                while size > 4 and font.text_length(line, size) > w - 4:
                    size -= 0.5
            if info.comb and info.maxlen:
                cell = w / info.maxlen
                for i, ch in enumerate(line[: info.maxlen]):
                    cw = font.text_length(ch, size)
                    tp.insert_text((i * cell + (cell - cw) / 2, h / 2 + size * 0.35), ch,
                                   fontname="FoliantU", fontsize=size, color=color)
            else:
                tw = font.text_length(line, size)
                x = {ALIGN_CENTER: (w - tw) / 2, ALIGN_RIGHT: w - 2 - tw}.get(info.align, 2)
                tp.insert_text((x, h / 2 + size * 0.35), line, fontname="FoliantU", fontsize=size, color=color)
        if lines is not None:
            tmp.subset_fonts()
            data = tmp.tobytes(garbage=3, deflate=True)
    finally:
        tmp.close()
    src = pymupdf.open("pdf", data)
    try:
        before = pdf.page_count
        pdf.insert_pdf(src)
    finally:
        src.close()
    pno = before
    pxref = pdf.page_xref(pno)
    t, v = pdf.xref_get_key(pxref, "Contents")
    refs = [int(r) for r in re.findall(r"(\d+) 0 R", v)] if t in ("array", "xref") else []
    stream = b"\n".join(pdf.xref_stream(r) or b"" for r in refs)
    t, v = pdf.xref_get_key(pxref, "Resources")
    res = pdf.xref_object(int(v.split()[0]), compressed=True) if t == "xref" else (v if t == "dict" else "<<>>")
    pdf.delete_page(pno)
    form = pdf.get_new_xref()
    # la pagina provvisoria ha l'origine in alto a sinistra: il contenuto è già in spazio PDF (h di altezza)
    pdf.update_object(form, f"<</Type/XObject/Subtype/Form/BBox[0 0 {_n(w)} {_n(h)}]/Resources{res}>>")
    pdf.update_stream(form, b"/Tx BMC\n" + stream + b"\nEMC\n")
    pdf.xref_set_key(xref, "AP", f"<</N {form} 0 R>>")


# ====================================================================== struttura logica
def _struct_root(pdf: pymupdf.Document) -> int | None:
    t, v = pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")
    return int(v.split()[0]) if t == "xref" else None


def _parent_tree_nums(pdf: pymupdf.Document, root: int) -> tuple[int | None, list] | None:
    """(xref dell'albero dei genitori, voci /Nums) se l'albero è piatto; None se assente o annidato."""
    t, v = pdf.xref_get_key(root, "ParentTree")
    if t != "xref":
        return None
    pt = int(v.split()[0])
    if pdf.xref_get_key(pt, "Kids")[0] != "null":
        return None  # albero numerico annidato: non lo modifichiamo
    t, v = pdf.xref_get_key(pt, "Nums")
    try:
        nums = ps.parse(v) if t == "array" else []
    except ps.PdfSyntaxError:
        return None
    return pt, nums


def tag_widget(pdf: pymupdf.Document, page: int, xref: int) -> int | None:
    """Nei documenti con struttura logica collega il widget a un nuovo elemento /Form
    (OBJR + /StructParent + albero dei genitori). Restituisce lo xref dell'elemento."""
    root = _struct_root(pdf)
    if root is None or pdf.xref_get_key(xref, "StructParent")[0] != "null":
        return None
    tree = _parent_tree_nums(pdf, root)
    if tree is None:
        return None
    pt, nums = tree
    t, v = pdf.xref_get_key(root, "ParentTreeNextKey")
    keys = [n for n in nums[0::2] if isinstance(n, int)]
    key = max([int(v) if t == "int" else 0] + [k + 1 for k in keys])
    # genitore: il primo elemento sotto la radice (di solito «Document»), altrimenti la radice
    t, v = pdf.xref_get_key(root, "K")
    parent = root
    if t == "xref" and pdf.xref_get_key(int(v.split()[0]), "Type")[1] in ("/StructElem", "null"):
        parent = int(v.split()[0])
    elif t == "array":
        try:
            first = next((r for r in ps.parse(v) if isinstance(r, ps.Ref)), None)
        except ps.PdfSyntaxError:
            first = None
        if first is not None:
            parent = first[0]
    pxref = pdf.page_xref(page)
    elem = pdf.get_new_xref()
    pdf.update_object(elem, f"<</Type/StructElem/S/Form/P {parent} 0 R/Pg {pxref} 0 R"
                            f"/K<</Type/OBJR/Obj {xref} 0 R/Pg {pxref} 0 R>>>>")
    t, v = pdf.xref_get_key(parent, "K")
    if t == "array":
        kids = v[:-1] + f" {elem} 0 R]"
    elif t == "null":
        kids = f"[{elem} 0 R]"
    else:
        kids = f"[{v} {elem} 0 R]"
    pdf.xref_set_key(parent, "K", kids)
    pdf.xref_set_key(xref, "StructParent", str(key))
    pdf.xref_set_key(pt, "Nums", ps.serialize(list(nums) + [key, ps.Ref(elem)]))
    pdf.xref_set_key(root, "ParentTreeNextKey", str(key + 1))
    return elem


def untag_widget(pdf: pymupdf.Document, xref: int) -> None:
    """Toglie l'elemento /Form del widget (prima di eliminarlo)."""
    root = _struct_root(pdf)
    t, v = pdf.xref_get_key(xref, "StructParent")
    if root is None or t != "int":
        return
    tree = _parent_tree_nums(pdf, root)
    if tree is None:
        return
    pt, nums = tree
    key = int(v)
    elem = None
    for i in range(0, len(nums) - 1, 2):
        if nums[i] == key and isinstance(nums[i + 1], ps.Ref):
            elem = nums[i + 1][0]
            del nums[i:i + 2]
            break
    if elem is None:
        return
    pdf.xref_set_key(pt, "Nums", ps.serialize(nums))
    tp, vp = pdf.xref_get_key(elem, "P")
    if tp == "xref":
        parent = int(vp.split()[0])
        tk, vk = pdf.xref_get_key(parent, "K")
        if tk == "array":
            try:
                kids = [k for k in ps.parse(vk) if not (isinstance(k, ps.Ref) and k[0] == elem)]
                pdf.xref_set_key(parent, "K", ps.serialize(kids))
            except ps.PdfSyntaxError:
                pass
        elif tk == "xref" and int(vk.split()[0]) == elem:
            pdf.xref_set_key(parent, "K", "[]")
    pdf.xref_set_key(elem, "K", "null")
