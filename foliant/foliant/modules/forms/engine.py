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

from foliant.i18n import _

from . import pdfsyntax as ps

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


class FormError(Exception):
    """Errore con messaggio per l'utente."""


def kind_label(kind: str) -> str:
    return {
        TEXT: _("Campo di testo"), CHECKBOX: _("Casella di controllo"), RADIO: _("Pulsante di opzione"),
        COMBO: _("Elenco a discesa"), LIST: _("Elenco"), BUTTON: _("Pulsante"), SIGNATURE: _("Campo firma"),
    }.get(kind, kind)


# ====================================================================== formati
@dataclass
class Format:
    """Formato di un campo di testo (memorizzato come script standard dei moduli)."""

    type: str = "none"          # none | number | percent | date
    decimals: int = 2
    separator: int = 2          # 0 «1,234.56» · 1 «1234.56» · 2 «1.234,56» · 3 «1234,56»
    currency: str = ""
    pattern: str = "dd/mm/yyyy"

    def scripts(self) -> tuple[str | None, str | None]:
        """(script di formato, script di battitura) in sintassi standard."""
        cur = self.currency.replace("\\", "\\\\").replace('"', '\\"')
        if self.type == "number":
            args = f"{self.decimals}, {self.separator}, 0, 0, \"{cur}\", true"
            return f"AFNumber_Format({args});", f"AFNumber_Keystroke({args});"
        if self.type == "percent":
            args = f"{self.decimals}, {self.separator}"
            return f"AFPercent_Format({args});", f"AFPercent_Keystroke({args});"
        if self.type == "date":
            pat = self.pattern.replace('"', "")
            return f"AFDate_FormatEx(\"{pat}\");", f"AFDate_KeystrokeEx(\"{pat}\");"
        return None, None

    @classmethod
    def from_script(cls, script: str | None) -> "Format":
        s = script or ""
        m = re.search(r"AFNumber_Format\(\s*(\d+)\s*,\s*(\d+)\s*,\s*\d+\s*,\s*\d+\s*,\s*\"((?:[^\"\\]|\\.)*)\"", s)
        if m:
            return cls("number", int(m.group(1)), int(m.group(2)), m.group(3).replace('\\"', '"'))
        m = re.search(r"AFPercent_Format\(\s*(\d+)\s*,\s*(\d+)", s)
        if m:
            return cls("percent", int(m.group(1)), int(m.group(2)))
        m = re.search(r"AFDate_Format(?:Ex)?\(\s*\"([^\"]*)\"", s)
        if m:
            return cls("date", pattern=m.group(1))
        return cls()


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


@dataclass
class Validation:
    """Intervallo ammesso per i campi numerici (None = senza limite)."""

    minimum: float | None = None
    maximum: float | None = None

    def script(self) -> str | None:
        if self.minimum is None and self.maximum is None:
            return None
        lo = self.minimum if self.minimum is not None else 0
        hi = self.maximum if self.maximum is not None else 0
        return (f"AFRange_Validate({'true' if self.minimum is not None else 'false'}, {_num_js(lo)}, "
                f"{'true' if self.maximum is not None else 'false'}, {_num_js(hi)});")

    @classmethod
    def from_script(cls, script: str | None) -> "Validation":
        m = re.search(r"AFRange_Validate\(\s*(true|false)\s*,\s*([-\d.eE+]+)\s*,\s*(true|false)\s*,\s*([-\d.eE+]+)", script or "")
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
    options: list[str] = field(default_factory=list)
    readonly: bool = False
    required: bool = False
    multiline: bool = False
    password: bool = False
    comb: bool = False
    multiselect: bool = False
    editable: bool = False      # elenco a discesa con testo libero
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
    default: Any = None
    fmt: Format = field(default_factory=Format)
    calc: Calculation | None = None
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
        return v.lstrip("/")
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
    out = []
    for v in values or []:
        if isinstance(v, (list, tuple)):
            out.append(str(v[1] if len(v) > 1 else v[0]))
        else:
            out.append(str(v))
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


def _on_state(w: pymupdf.Widget) -> str | None:
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
    info.font = _FONT_NAMES.get(w.text_font or "Helv", w.text_font or "Helvetica")
    info.fontsize = float(w.text_fontsize or 0)
    info.text_color = _color(w.text_color) or (0, 0, 0)
    info.fill_color = _color(w.fill_color)
    info.border_color = _color(w.border_color)
    info.border_width = float(w.border_width or 0)
    info.border_style = (w.border_style or "S")[:1].upper()
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
        info.fmt = Format.from_script(_action_script(pdf, w.xref, "F") or w.script_format)
        info.calc = Calculation.from_script(_action_script(pdf, w.xref, "C") or w.script_calc)
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
        info.options = _option_texts(w.choice_values)
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


def list_fields(pdf: pymupdf.Document, page: int | None = None) -> list[FieldInfo]:
    """Campi del documento (o di una pagina) nell'ordine di tabulazione."""
    pages = range(pdf.page_count) if page is None else [page]
    out: list[FieldInfo] = []
    for i in pages:
        pg = pdf[i]
        for w in pg.widgets():
            out.append(_info(pdf, i, w))
    return out


def find_widget(pdf: pymupdf.Document, page: int, xref: int) -> pymupdf.Widget:
    pg = pdf[page]
    for w in pg.widgets():
        if w.xref == xref:
            w._foliant_page = pg  # la pagina deve restare viva finché si usa il widget
            return w
    raise FormError(_("Il campo non esiste più nel documento."))


def field_info(pdf: pymupdf.Document, page: int, xref: int) -> FieldInfo:
    return _info(pdf, page, find_widget(pdf, page, xref))


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
    """Imposta il valore di un campo e ne rigenera l'aspetto."""
    w = find_widget(pdf, page, xref)
    kind = _KIND_OF_TYPE.get(w.field_type, TEXT)
    if kind == TEXT:
        text = "" if value is None else str(value)
        maxlen = int(w.text_maxlen or 0)
        if maxlen and len(text) > maxlen:
            text = text[:maxlen]
        fx = _field_xref(pdf, w.xref)
        pdf.xref_set_key(fx, "V", ps.encode_string(text))
        _regen_field(pdf, fx, page, w.xref)
    elif kind == CHECKBOX:
        on = _on_state(w)
        checked = _truthy(value, on)
        _set_button_states(pdf, [w.xref], on if checked else OFF, _field_xref(pdf, w.xref))
    elif kind == RADIO:
        state = OFF if value in (None, False, "", OFF) else str(value)
        if value is True:
            state = _on_state(w)
        fx = _field_xref(pdf, w.xref)
        group = [x for x in _widgets_of_field(pdf, fx)] or [w.xref]
        _set_button_states(pdf, group, state, fx)
    elif kind in (COMBO, LIST):
        flags = w.field_flags or 0
        multi = kind == LIST and bool(flags & pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT)
        options = _option_texts(w.choice_values)
        if isinstance(value, (list, tuple)):
            values = [str(v) for v in value]
        elif value in (None, ""):
            values = []
        else:
            values = [str(value)] if not multi else [v.strip() for v in re.split(r"[;\n]", str(value)) if v.strip()]
        if not multi:
            values = values[:1]
        if kind == LIST or not (flags & pymupdf.PDF_CH_FIELD_IS_EDIT):
            unknown = [v for v in values if v not in options]
            if unknown:
                raise FormError(_("«{v}» non è tra le voci del campo «{n}».").format(v=unknown[0], n=w.field_name))
        fx = _field_xref(pdf, w.xref)
        if not values:
            pdf.xref_set_key(fx, "V", "null")
        elif multi and len(values) > 1:
            pdf.xref_set_key(fx, "V", ps.serialize(values))
        else:
            pdf.xref_set_key(fx, "V", ps.encode_string(values[0]))
        if kind == LIST:
            idx = [options.index(v) for v in values if v in options]
            pdf.xref_set_key(fx, "I", ps.serialize(sorted(idx)) if idx else "null")
        _regen_field(pdf, fx, page, w.xref)
    else:
        raise FormError(_("Questo campo non si compila."))


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
    """Rigenera tutti i widget del campo (un campo può comparire in più punti)."""
    kids = _widgets_of_field(pdf, fx)
    if xref not in kids:
        kids = [xref]
    for k in kids:
        pi = page if k == xref else _page_index_of(pdf, k, page)
        if pi is None:
            continue
        try:
            regenerate(pdf, pi, k)
        except FormError:
            continue


def _widgets_of_field(pdf: pymupdf.Document, field_xref: int) -> list[int]:
    t, v = pdf.xref_get_key(field_xref, "Kids")
    if t != "array":
        return [field_xref]
    try:
        kids = ps.parse(v)
    except ps.PdfSyntaxError:
        return [field_xref]
    return [k[0] for k in kids if isinstance(k, ps.Ref)]


def _set_button_states(pdf: pymupdf.Document, widgets: list[int], state: str, field_xref: int) -> None:
    """Attiva lo stato `state` (o Off) sui widget di una casella o di un gruppo di opzioni."""
    for x in widgets:
        t, n = pdf.xref_get_key(x, "AP/N")
        names: list[str] = []
        if t == "dict":
            try:
                names = list(ps.parse(n).keys())
            except ps.PdfSyntaxError:
                names = []
        own = state if state in names or not names else OFF
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
    count = 0
    seen_radio: set[int] = set()
    for f in list_fields(pdf):
        if f.kind not in FILLABLE or (wanted is not None and f.name not in wanted):
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


def _with_value(f: FieldInfo, value: Any) -> FieldInfo:
    from dataclasses import replace
    return replace(f, value=value)


# ====================================================================== numeri e date
def parse_number(text: Any) -> float | None:
    """Interpreta numeri scritti all'italiana o all'inglese («1.234,5», «1,234.5», «12%», «€ 3»)."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text).strip()
    if not s:
        return None
    s = re.sub(r"[^\d,.\-+eE]", "", s.replace("−", "-"))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and len(parts[0].lstrip("-+")) <= 3 and False):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def format_number(value: float, decimals: int | None = None) -> str:
    """Valore numerico grezzo da memorizzare nel campo (punto decimale, senza migliaia)."""
    if decimals is not None:
        s = f"{value:.{max(0, decimals)}f}"
    else:
        s = f"{value:.10f}".rstrip("0").rstrip(".")
    if s in ("-0", "") or re.fullmatch(r"-0\.?0*", s):
        s = s.lstrip("-") or "0"
    return s


def display_number(value: float, fmt: Format) -> str:
    """Numero formattato per la visualizzazione secondo il formato del campo."""
    dec = max(0, fmt.decimals)
    if fmt.type == "percent":
        value = value * 100
    s = f"{abs(value):,.{dec}f}"
    if fmt.separator == 1:
        s = s.replace(",", "")
    elif fmt.separator == 2:
        s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    elif fmt.separator == 3:
        s = s.replace(",", "").replace(".", ",")
    if value < 0:
        s = "-" + s
    if fmt.type == "percent":
        s += "%"
    elif fmt.currency:
        s = f"{fmt.currency} {s}"
    return s


_DATE_TOKENS = [("yyyy", "%Y"), ("yy", "%y"), ("mmmm", "%B"), ("mmm", "%b"), ("mm", "%m"), ("m", "%m"),
                ("dd", "%d"), ("d", "%d"), ("HH", "%H"), ("MM", "%M"), ("ss", "%S")]


def _strf(pattern: str) -> str:
    out, i = [], 0
    while i < len(pattern):
        for tok, rep in _DATE_TOKENS:
            if pattern.startswith(tok, i):
                out.append(rep)
                i += len(tok)
                break
        else:
            out.append(pattern[i].replace("%", "%%"))
            i += 1
    return "".join(out)


def parse_date(text: str, pattern: str = "dd/mm/yyyy") -> date | None:
    s = (text or "").strip()
    if not s:
        return None
    for fmt in (_strf(pattern), "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def format_date(d: date, pattern: str = "dd/mm/yyyy") -> str:
    return d.strftime(_strf(pattern))


# ====================================================================== validazione
def validate(info: FieldInfo, value: Any) -> str | None:
    """Messaggio d'errore se `value` non è accettabile per il campo, altrimenti None."""
    if info.kind != TEXT:
        if info.required and export_value(_with_value(info, value)) in ("", OFF, [], None):
            return _("Il campo «{n}» è obbligatorio.").format(n=info.name)
        return None
    text = "" if value is None else str(value)
    if not text.strip():
        return _("Il campo «{n}» è obbligatorio.").format(n=info.name) if info.required else None
    if info.maxlen and len(text) > info.maxlen:
        return _("Il campo «{n}» ammette al massimo {k} caratteri.").format(n=info.name, k=info.maxlen)
    if info.fmt.type in ("number", "percent"):
        num = parse_number(text)
        if num is None:
            return _("Nel campo «{n}» va inserito un numero.").format(n=info.name)
        v = info.validation
        if v.minimum is not None and num < v.minimum:
            return _("Il valore di «{n}» deve essere almeno {m}.").format(n=info.name, m=_num_js(v.minimum))
        if v.maximum is not None and num > v.maximum:
            return _("Il valore di «{n}» deve essere al massimo {m}.").format(n=info.name, m=_num_js(v.maximum))
    elif info.fmt.type == "date":
        if parse_date(text, info.fmt.pattern) is None:
            return _("Nel campo «{n}» va inserita una data nel formato {p}.").format(n=info.name, p=info.fmt.pattern)
    elif info.validation.minimum is not None or info.validation.maximum is not None:
        num = parse_number(text)
        if num is None:
            return _("Nel campo «{n}» va inserito un numero.").format(n=info.name)
    return None


def normalize_input(info: FieldInfo, text: str) -> str:
    """Valore da memorizzare per l'input dell'utente (numeri senza separatori, date nel formato)."""
    if info.kind != TEXT or not text.strip():
        return text
    if info.fmt.type in ("number", "percent"):
        num = parse_number(text)
        if num is not None:
            if info.fmt.type == "percent" and text.strip().endswith("%"):
                num = num / 100
            return format_number(num)
    elif info.fmt.type == "date":
        d = parse_date(text, info.fmt.pattern)
        if d is not None:
            return format_date(d, info.fmt.pattern)
    return text


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


def recalculate(pdf: pymupdf.Document) -> list[str]:
    """Ricalcola i campi con un calcolo semplice. Restituisce i nomi dei campi aggiornati."""
    fields = list_fields(pdf)
    calcs = [f for f in fields if f.kind == TEXT and f.calc]
    if not calcs:
        return []
    order = _calc_order(pdf)
    calcs.sort(key=lambda f: order.index(f.field_xref) if f.field_xref in order else len(order) + fields.index(f))
    values = {f.name: f.value for f in fields if f.kind == TEXT}
    values.update({f.name: (1.0 if f.value else 0.0) if f.kind == CHECKBOX else export_value(f)
                   for f in fields if f.kind in (COMBO, LIST, RADIO, CHECKBOX)})
    changed: list[str] = []
    for _round in range(len(calcs)):  # dipendenze a catena senza ordine dichiarato
        progress = False
        for f in calcs:
            nums = [n for n in (parse_number(values.get(name)) for name in f.calc.fields) if n is not None]
            result = compute(f.calc.op, nums)
            decimals = f.fmt.decimals if f.fmt.type in ("number",) else None
            text = format_number(round(result, 10), decimals)
            if values.get(f.name) != text:
                values[f.name] = text
                set_value(pdf, f.page, f.xref, text)
                if f.name not in changed:
                    changed.append(f.name)
                progress = True
        if not progress:
            break
    return changed


def _set_calc_order(pdf: pymupdf.Document) -> None:
    cat = pdf.pdf_catalog()
    xrefs = []
    for f in list_fields(pdf):
        if f.kind == TEXT and f.calc and f.field_xref not in xrefs:
            xrefs.append(f.field_xref)
    if pdf.xref_get_key(cat, "AcroForm")[0] == "null":
        return
    pdf.xref_set_key(cat, "AcroForm/CO", ps.serialize([ps.Ref(x) for x in xrefs]) if xrefs else "null")


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
            LIST: _("Elenco"), BUTTON: _("Pulsante"), SIGNATURE: _("Firma")}.get(kind, _("Campo"))


DEFAULT_SIZES = {TEXT: (160, 20), CHECKBOX: (14, 14), RADIO: (14, 14), COMBO: (140, 20), LIST: (140, 60),
                 BUTTON: (90, 22), SIGNATURE: (160, 40)}


def create_field(pdf: pymupdf.Document, page: int, kind: str, rect: pymupdf.Rect, name: str | None = None,
                 **props) -> int:
    """Crea un campo e restituisce lo xref del widget.

    Per i pulsanti di opzione `props["export"]` è il valore della singola opzione
    e `name` il nome del gruppo (un gruppo esistente con lo stesso nome viene esteso).
    """
    if kind not in _TYPE_OF_KIND:
        raise FormError(_("Tipo di campo sconosciuto."))
    rect = pymupdf.Rect(rect).normalize()
    if rect.is_empty or rect.width < 4 or rect.height < 4:
        raise FormError(_("Il campo è troppo piccolo."))
    pg = pdf[page]
    existing_group = None
    if kind == RADIO and name:
        for f in list_fields(pdf):
            if f.kind == RADIO and f.name == name:
                existing_group = f
                break
    if not name:
        name = unique_name(pdf, default_base_name(kind))
    elif kind != RADIO and name in field_names(pdf):
        raise FormError(_("Esiste già un campo chiamato «{n}».").format(n=name))
    w = pymupdf.Widget()
    w.field_type = _TYPE_OF_KIND[kind]
    w.field_name = name
    w.rect = rect
    w.text_font = FONTS.get(props.get("font", "Helvetica"), "Helv")
    w.text_fontsize = float(props.get("fontsize", 0 if kind in (TEXT, COMBO, LIST) else 0))
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
    pg.add_widget(w)
    xref = w.xref
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
    rest = {k: v for k, v in props.items() if k not in ("export", "caption")}
    if rest:
        update_properties(pdf, page, xref, **rest)
    return xref


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
    cat = pdf.pdf_catalog()
    t, v = pdf.xref_get_key(cat, "AcroForm/Fields")
    if t != "array":
        return
    try:
        refs = [r for r in ps.parse(v) if isinstance(r, ps.Ref) and r[0] != field_xref]
    except ps.PdfSyntaxError:
        return
    pdf.xref_set_key(cat, "AcroForm/Fields", ps.serialize(refs))


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
            others = {f.name for f in list_fields(pdf) if f.field_xref != fx}
            if new in others:
                raise FormError(_("Esiste già un campo chiamato «{n}».").format(n=new))
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
        if flags & pymupdf.PDF_TX_FIELD_IS_COMB and flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
            raise FormError(_("Un campo suddiviso in caselle non può essere su più righe."))
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
        if "validation" in props:
            _set_action(pdf, fx, "V", (props["validation"] or Validation()).script())
    if kind in (COMBO, LIST):
        if "multiselect" in props and kind == LIST:
            flag(pymupdf.PDF_CH_FIELD_IS_MULTI_SELECT, bool(props["multiselect"]))
        if "editable" in props and kind == COMBO:
            flag(pymupdf.PDF_CH_FIELD_IS_EDIT, bool(props["editable"]))
        if "options" in props:
            opts = [str(o) for o in props["options"] if str(o).strip()]
            if not opts:
                raise FormError(_("Un elenco deve avere almeno una voce."))
            pdf.xref_set_key(fx, "Opt", ps.serialize(opts))
            current = info.value if isinstance(info.value, list) else ([info.value] if info.value else [])
            keep = [c for c in current if c in opts or (kind == COMBO and flags & pymupdf.PDF_CH_FIELD_IS_EDIT)]
            if keep != current:
                pdf.xref_set_key(fx, "V", ps.encode_string(keep[0]) if len(keep) == 1 else (ps.serialize(keep) if keep else "null"))
            if kind == LIST:
                idx = sorted(opts.index(c) for c in keep if c in opts)
                pdf.xref_set_key(fx, "I", ps.serialize(idx) if idx else "null")
    if flags != old_flags:
        pdf.xref_set_key(fx, "Ff", str(flags))
    if "tooltip" in props:
        pdf.xref_set_key(fx, "TU", ps.encode_string(props["tooltip"]) if props["tooltip"] else "null")
    if any(k in props for k in ("font", "fontsize", "text_color")):
        font = props.get("font", info.font)
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
    if kind in (CHECKBOX, RADIO):
        state = _value_of(*pdf.xref_get_key(xref, "AS"))
        regenerate(pdf, page, xref)
        if state is not None:  # la rigenerazione non deve cambiare lo stato
            pdf.xref_set_key(xref, "AS", ps.encode_name(state))
    else:
        _regen_field(pdf, fx, page, xref)
    if "calc" in props:
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


def _rename_field(pdf: pymupdf.Document, fx: int, old_full: str, new_full: str) -> None:
    """Rinomina un campo: cambia il nome parziale /T (il prefisso dei genitori resta)."""
    old_parts, new_parts = old_full.split("."), new_full.split(".")
    if len(new_parts) == len(old_parts) and new_parts[:-1] == old_parts[:-1]:
        partial = new_parts[-1]
    else:
        partial = new_full if "." not in old_full else None
    if partial is None:
        raise FormError(_("Per rinominare un campo annidato cambia solo l'ultima parte del nome."))
    pdf.xref_set_key(fx, "T", ps.encode_string(partial))


def delete_field(pdf: pymupdf.Document, page: int, xref: int) -> None:
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
    return [w.xref for w in pdf[page].widgets()]


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
    pdf.xref_set_key(pg.xref, "Tabs", "null")


def auto_tab_order(pdf: pymupdf.Document, page: int, by: str = "row") -> list[int]:
    """Ordina per righe (dall'alto, poi da sinistra) o per colonne."""
    fields = list_fields(pdf, page)

    def key(f: FieldInfo):
        r = f.rect
        if by == "column":
            return (round(r.x0 / 12), r.y0, r.x0)
        return (round((r.y0 + r.y1) / 2 / 10), r.x0, r.y0)

    order = [f.xref for f in sorted(fields, key=key)]
    set_tab_order(pdf, page, order)
    return order


# ====================================================================== appiattimento e XFA
def flatten(pdf: pymupdf.Document) -> int:
    """Trasforma i campi in contenuto fisso della pagina. Restituisce il numero di campi."""
    n = sum(1 for i in range(pdf.page_count) for _w in pdf[i].widgets())
    if not n:
        return 0
    pdf.bake(annots=False, widgets=True)
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm")[0] != "null":
        pdf.xref_set_key(cat, "AcroForm", "null")
    return n


def xfa_status(pdf: pymupdf.Document) -> str:
    """'none' senza XFA; 'hybrid' con XFA e campi AcroForm; 'dynamic' con solo XFA."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm/XFA")[0] == "null":
        return "none"
    has_widgets = any(True for i in range(pdf.page_count) for _w in pdf[i].widgets())
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


def sync_xfa_from_fields(pdf: pymupdf.Document) -> int:
    """Nei moduli ibridi copia i valori AcroForm nei dati XFA (per nome finale del campo)."""
    data = xfa_data(pdf)
    if not data:
        return 0
    by_leaf: dict[str, str] = {}
    for path in data:
        leaf = re.sub(r"\[\d+\]", "", path.rsplit(".", 1)[-1])
        by_leaf.setdefault(leaf, path)
    updates = {}
    for name, value in form_values(pdf).items():
        leaf = re.sub(r"\[\d+\]", "", name.rsplit(".", 1)[-1])
        path = by_leaf.get(leaf)
        if path is not None:
            if isinstance(value, list):
                value = "\n".join(value)
            if value == OFF:
                value = "0"
            updates[path] = value
    return set_xfa_data(pdf, updates)


def remove_xfa(pdf: pymupdf.Document) -> bool:
    """Elimina la parte XFA: i lettori useranno i campi AcroForm."""
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "AcroForm/XFA")[0] == "null":
        return False
    pdf.xref_set_key(cat, "AcroForm/XFA", "null")
    return True
