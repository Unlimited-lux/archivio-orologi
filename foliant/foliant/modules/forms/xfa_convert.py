"""Conversione dei moduli XML (XFA) dinamici in moduli standard (FRM-36), senza interfaccia.

`analyze(pdf)` valuta la fattibilità; `convert(pdf)` impagina il modello con i dati correnti
in un documento nuovo (pagine fisse, campi AcroForm, nessun /XFA né /NeedsRendering).
Il documento di partenza viene solo letto. Le funzioni vanno chiamate tenendo ENGINE_LOCK
(come il resto del motore), perché leggono `pdf`.

Sottoinsieme del modello supportato (concetti della specifica XFA pubblica):

* misure con unità `in` (predefinita), `mm`, `cm`, `pt`, `mp`;
* pagina da `pageSet/pageArea` (`medium` e `contentArea`), altrimenti A4 con margini di 36 pt;
  i `draw` del primo `pageArea` sono ripetuti su ogni pagina;
* `subform` con `layout="position"` (predefinito: figli in x, y relativi) o a scorrimento
  (`tb`; `lr-tb`, `rl-tb`, `row`, `table` trattati come `tb`); sottomoduli annidati, margini
  (`margin`), interruzioni di pagina (`break`/`breakBefore`/`breakAfter`) e salto di pagina
  automatico quando il contenuto a scorrimento supera l'area utile; `area` e `subformSet`
  sono contenitori trasparenti per i nomi;
* sottomoduli ripetibili (`occur`): tante istanze quanti i gruppi di dati omonimi sotto il
  nodo dati del contenitore, almeno `min` (predefinito 1; `initial` se non vi sono dati), al
  massimo `max` (-1 = illimitato);
* `field` con `ui` textEdit, passwordEdit, numericEdit, dateTimeEdit, checkButton, choiceList,
  button, signature; didascalia (`caption`, posizione left/right/top/bottom); `exclGroup`
  → gruppo di pulsanti di opzione;
* `draw` con testo, linea, rettangolo (solo bordo) e immagine incorporata;
* script di calcolo FormCalc della forma `Sum(a, b)`, `Avg(...)`, `Min(...)`, `Max(...)`,
  `a + b + c`, `a * b` → calcoli predefiniti; ogni altro script è riportato nel rapporto.

Nomi dei campi: nomi pieni con punti formati dai nomi dei sottomoduli con nome (quelli senza
nome non contribuiscono) e dal nome del campo; un'istanza n > 0 di un sottomodulo ripetuto
(o di un elemento omonimo nello stesso contenitore) riceve il suffisso `[n]`, ad es.
`form1.item[1].qty`; la prima istanza non ha suffisso. Se il motore non accettasse le
parentesi quadre, `[n]` diventerebbe `_n` (`form1.item_1.qty`): vedi `field_name_style()`.
"""

from __future__ import annotations

import base64
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable

import pymupdf

from foliant.i18n import _

from . import engine as E
from . import formats as fm

Progress = Callable[[float, str], None] | None

TRANSLATIONS: dict[str, str] = {
    "Il documento non contiene un modello XML (pacchetto template).":
        "The document contains no XML form template (template packet).",
    "Il modello XML non è leggibile: {e}": "The XML form template cannot be read: {e}",
    "Il modello XML non contiene elementi impaginabili.": "The XML form template contains no elements that can be laid out.",
    "Script di calcolo nel campo «{n}»": "Calculation script in field “{n}”",
    "Script di validazione nel campo «{n}»": "Validation script in field “{n}”",
    "Script dell'evento «{e}» nel campo «{n}»": "Script of event “{e}” in field “{n}”",
    "Script dell'evento «{e}» nel sottomodulo «{n}»": "Script of event “{e}” in subform “{n}”",
    "Script del modello in «{n}»": "Template script in “{n}”",
    "Elemento non supportato: «{t}»": "Unsupported element: “{t}”",
    "Campo «{n}» di tipo «{t}» non supportato": "Field “{n}” of type “{t}” is not supported",
    "Disposizione «{l}» trattata come verticale": "Layout “{l}” treated as top-to-bottom",
    "Collegamento ai dati «{r}» non supportato": "Data binding “{r}” is not supported",
    "Carattere «{f}» sostituito con {s}": "Font “{f}” replaced with {s}",
    "Misura non valida: «{m}»": "Invalid measurement: “{m}”",
    "Contenuto più alto dell'area della pagina: parte potrebbe non essere visibile":
        "Content taller than the page area: part of it may not be visible",
    "Valore «{v}» non valido per il campo «{n}»": "Value “{v}” is not valid for field “{n}”",
    "Campi nell'area di pagina non convertiti": "Fields in the page area were not converted",
    "Documento firmato: il risultato è un nuovo documento non firmato":
        "Signed document: the result is a new, unsigned document",
    "Impaginazione": "Layout",
    "Creazione dei campi": "Creating fields",
}

PT = {"in": 72.0, "mm": 72.0 / 25.4, "cm": 72.0 / 2.54, "pt": 1.0, "mp": 0.001}
A4 = (595.276, 841.89)
STOCKS = {"letter": (612.0, 792.0), "legal": (612.0, 1008.0), "a4": A4, "a3": (841.89, 1190.55),
          "a5": (419.53, 595.276)}
DEFAULT_MARGIN = 36.0
CONTAINERS = ("subform", "field", "draw", "exclGroup", "area", "subformSet")
# figli che non producono contenuto (o sono letti altrove): ignorati senza avviso
_QUIET = {"pageSet", "proto", "desc", "extras", "occur", "bind", "margin", "border", "font", "para",
          "keep", "break", "breakBefore", "breakAfter", "overflow", "bookend", "assist", "traversal",
          "validate", "calculate", "event", "variables", "setProperty", "connect", "ui", "caption",
          "value", "items", "format", "toolTip", "speak", "medium", "contentArea", "instanceManager",
          "certificates", "filter", "manifest", "comb", "edge", "corner", "fill", "picture", "consumeData"}


# ====================================================================== rapporto
@dataclass
class ConversionReport:
    fields: int = 0
    pages: int = 0
    unsupported: list[str] = field(default_factory=list)  # righe leggibili (italiano)
    scripts: list[tuple[str, str]] = field(default_factory=list)  # (nome campo, evento) non tradotti

    def note(self, line: str) -> None:
        if line not in self.unsupported:
            self.unsupported.append(line)


# ====================================================================== utilità
def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _kids(el, *tags: str) -> list:
    return [c for c in el if _local(c.tag) in tags] if tags else [c for c in el if isinstance(c.tag, str)]


def _kid(el, tag: str):
    for c in el:
        if _local(c.tag) == tag:
            return c
    return None


def _path(el, *tags: str):
    for t in tags:
        if el is None:
            return None
        el = _kid(el, t)
    return el


def measure(text: str | None, default: float | None = None, report: ConversionReport | None = None) -> float | None:
    """Misura XFA in punti: «25.4mm» → 72; senza unità vale in pollici."""
    if text is None or not str(text).strip():
        return default
    m = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*([a-zA-Z]*)\s*", str(text))
    if not m or m.group(2).lower() not in PT and m.group(2):
        if report is not None:
            report.note(_("Misura non valida: «{m}»").format(m=text))
        return default
    return float(m.group(1)) * PT[(m.group(2) or "in").lower()]


_BRACKETS_OK: bool | None = None


def field_name_style() -> str:
    """'brackets' se il motore accetta `[n]` nei nomi dei campi, altrimenti 'underscore'."""
    global _BRACKETS_OK
    if _BRACKETS_OK is None:
        probe = pymupdf.open()
        try:
            probe.new_page()
            E.create_field(probe, 0, E.TEXT, pymupdf.Rect(10, 10, 100, 30), "a.b[1].c")
            _BRACKETS_OK = [f.name for f in E.list_fields(probe)] == ["a.b[1].c"]
        except Exception:
            _BRACKETS_OK = False
        finally:
            probe.close()
    return "brackets" if _BRACKETS_OK else "underscore"


def _fix_name(name: str) -> str:
    return name if field_name_style() == "brackets" else re.sub(r"\[(\d+)\]", r"_\1", name)


def _text_of(el) -> str:
    return "".join(el.itertext()) if el is not None else ""


def _value_text(el) -> str | None:
    """Testo del valore predefinito di un field/draw (`value/text|integer|decimal|date|exData...`)."""
    v = _kid(el, "value")
    if v is None:
        return None
    for c in v:
        t = _local(c.tag)
        if t in ("text", "integer", "decimal", "float", "date", "dateTime", "time", "boolean"):
            return c.text or ""
        if t == "exData":
            return "\n".join(s.strip() for s in "".join(c.itertext()).splitlines() if s.strip())
    return None


# ====================================================================== modello intermedio
@dataclass
class _Op:
    kind: str                     # text | line | rect | image | field
    rect: pymupdf.Rect
    data: dict = field(default_factory=dict)

    def moved(self, dx: float, dy: float) -> "_Op":
        return _Op(self.kind, pymupdf.Rect(self.rect) + (dx, dy, dx, dy), self.data)


@dataclass
class _Block:
    h: float
    ops: list[_Op] = field(default_factory=list)
    brk: bool = False             # interruzione di pagina (blocco vuoto)


@dataclass
class _Node:
    """Nodo dell'albero dei nomi (per risolvere i riferimenti degli script)."""
    name: str
    parent: "_Node | None" = None
    full: str | None = None       # nome del campo (foglie)
    kids: dict[str, list["_Node"]] = field(default_factory=dict)

    def add(self, child: "_Node") -> "_Node":
        child.parent = self
        self.kids.setdefault(child.name, []).append(child)
        return child


@dataclass
class _Scope:
    path: list[str]
    data: Any                     # elemento dei dati (o None)
    node: _Node
    counters: dict[str, int] = field(default_factory=dict)
    data_counters: dict[str, int] = field(default_factory=dict)


def _merge(blocks: list[_Block]) -> _Block:
    out, y = _Block(0), 0.0
    for b in blocks:
        out.ops.extend(o.moved(0, y) for o in b.ops)
        y += b.h
    out.h = y
    return out


# ====================================================================== impaginazione
class _Layout:
    def __init__(self, pdf: pymupdf.Document):
        self.report = ConversionReport()
        self.template = self._template(pdf)
        found = E._datasets(pdf)
        root = found[1] if found else None
        self.data_root = next((el for el in root.iter() if _local(el.tag) == "data"), None) if root is not None else None
        self.page_w, self.page_h = A4
        self.content = pymupdf.Rect(DEFAULT_MARGIN, DEFAULT_MARGIN, A4[0] - DEFAULT_MARGIN, A4[1] - DEFAULT_MARGIN)
        self.master: list[_Op] = []
        self.pages: list[list[_Op]] = []
        self.calcs: list[tuple[str, _Node, list]] = []   # (campo, contenitore, operandi)
        self.root_node = _Node("")
        if pdf.get_sigflags() in (1, 3):
            self.report.note(_("Documento firmato: il risultato è un nuovo documento non firmato"))

    # ------------------------------------------------------------ pacchetti
    @staticmethod
    def _template(pdf: pymupdf.Document):
        raw = None
        for name, xref in E._xfa_packets(pdf):
            try:
                data = pdf.xref_stream(xref)
            except Exception:
                continue
            if name == "template":
                raw = data
                break
            if not name and data and b"<template" in data:
                m = re.search(rb"<template\b.*?</template>", data, re.S)
                if m:
                    raw = m.group(0)
                    break
        if not raw or not raw.strip():
            raise E.FormError(_("Il documento non contiene un modello XML (pacchetto template)."))
        try:
            return ET.fromstring(raw)
        except ET.ParseError as e:
            raise E.FormError(_("Il modello XML non è leggibile: {e}").format(e=e)) from e

    # ------------------------------------------------------------ pagina
    def _page_setup(self) -> None:
        ps_ = next((el for el in self.template.iter() if _local(el.tag) == "pageSet"), None)
        area = next((el for el in ps_.iter() if _local(el.tag) == "pageArea"), None) if ps_ is not None else None
        if area is None:
            return
        med = _kid(area, "medium")
        if med is not None:
            w, h = STOCKS.get((med.get("stock") or "").lower(), A4)
            w = measure(med.get("short"), w, self.report)
            h = measure(med.get("long"), h, self.report)
            if (med.get("orientation") or "") == "landscape":
                w, h = h, w
            self.page_w, self.page_h = w, h
        ca = _kid(area, "contentArea")
        if ca is not None:
            x = measure(ca.get("x"), 0, self.report)
            y = measure(ca.get("y"), 0, self.report)
            w = measure(ca.get("w"), self.page_w - x, self.report)
            h = measure(ca.get("h"), self.page_h - y, self.report)
            self.content = pymupdf.Rect(x, y, x + w, y + h)
        else:
            self.content = pymupdf.Rect(DEFAULT_MARGIN, DEFAULT_MARGIN, self.page_w - DEFAULT_MARGIN,
                                        self.page_h - DEFAULT_MARGIN)
        for c in _kids(area):
            t = _local(c.tag)
            if t == "draw":
                b = self._draw(c, self.page_w, positioned=True)
                x, y = measure(c.get("x"), 0, self.report), measure(c.get("y"), 0, self.report)
                self.master.extend(o.moved(x, y) for o in b.ops)
            elif t in ("field", "exclGroup", "subform"):
                self.report.note(_("Campi nell'area di pagina non convertiti"))

    # ------------------------------------------------------------ avvio
    def run(self) -> None:
        self._page_setup()
        roots = _kids(self.template, "subform")
        scope = _Scope([], self.data_root, self.root_node)
        blocks: list[_Block] = []
        for r in roots:
            blocks.extend(self._container(r, scope, self.content.width, flowed=True, root=True))
        self._paginate(blocks)
        if not any(o.kind in ("field", "text", "line", "rect", "image") for p in self.pages for o in p):
            raise E.FormError(_("Il modello XML non contiene elementi impaginabili."))
        self.report.pages = len(self.pages)
        self.report.fields = len({o.data["name"] for p in self.pages for o in p if o.kind == "field"})
        self._resolve_calcs()

    def _paginate(self, blocks: list[_Block]) -> None:
        top, bottom = self.content.y0, self.content.y1
        self.pages = [[]]
        y = top
        for b in blocks:
            if b.brk:
                if y > top:
                    self.pages.append([])
                    y = top
                continue
            if y + b.h > bottom + 0.01 and y > top:
                self.pages.append([])
                y = top
            if b.h > bottom - top + 0.01:
                self.report.note(_("Contenuto più alto dell'area della pagina: parte potrebbe non essere visibile"))
            self.pages[-1].extend(o.moved(self.content.x0, y) for o in b.ops)
            y += b.h

    # ------------------------------------------------------------ dati
    def _data_for(self, el, name: str | None, scope: _Scope, many: bool = False):
        """Nodi dei dati dell'elemento: lista (many) o singolo nodo/None."""
        bind = _kid(el, "bind")
        match = (bind.get("match") if bind is not None else None) or "once"
        if match == "none":
            return [] if many else None
        if match == "dataRef":
            ref = (bind.get("ref") or "").strip()
            nodes = self._by_ref(ref, scope)
            if nodes is None:
                self.report.note(_("Collegamento ai dati «{r}» non supportato").format(r=ref))
            else:
                return nodes if many else (nodes[0] if nodes else None)
        if match == "global" and name and self.data_root is not None:
            found = [n for n in self.data_root.iter() if _local(n.tag) == name]
            return found if many else (found[0] if found else None)
        if not name or scope.data is None:
            return [] if many else None
        found = [c for c in scope.data if _local(c.tag) == name]
        if many:
            return found
        i = scope.data_counters.get(name, 0)
        scope.data_counters[name] = i + 1
        return found[i] if i < len(found) else None

    def _by_ref(self, ref: str, scope: _Scope):
        parts = [p for p in re.split(r"\.", ref) if p]
        if not parts:
            return None
        if parts[0] == "$":
            cur = [scope.data] if scope.data is not None else []
        elif parts[0] in ("$record", "$data"):
            if self.data_root is None:
                return []
            cur = [self.data_root]
            if parts[0] == "$record":
                rec = _kids(self.data_root)
                cur = rec[:1]
        else:
            return None
        for p in parts[1:]:
            m = re.fullmatch(r"([\w-]+)(?:\[(\d+|\*)\])?", p)
            if not m:
                return None
            nxt = []
            for n in cur:
                found = [c for c in n if _local(c.tag) == m.group(1)]
                if m.group(2) == "*":
                    nxt.extend(found)
                else:
                    k = int(m.group(2) or 0)
                    nxt.extend(found[k:k + 1])
            cur = nxt
        return cur

    # ------------------------------------------------------------ contenitori
    def _instances(self, el, scope: _Scope) -> list:
        """Nodi dei dati delle istanze di un sottomodulo (None = istanza senza dati)."""
        name = el.get("name")
        occur = _kid(el, "occur")
        if occur is None:
            return [self._data_for(el, name, scope)]
        lo = int(occur.get("min") or 1)
        hi = int(occur.get("max") or 1) if occur.get("max") is not None else max(lo, 1)
        init = occur.get("initial")
        nodes = self._data_for(el, name, scope, many=True) or []
        used = scope.data_counters.get(name or "", 0)
        nodes = nodes[used:] if name else nodes
        n = len(nodes) if nodes else max(lo, int(init) if init else lo)
        n = max(n, lo)
        if hi >= 0:
            n = min(n, hi)
        if name:
            scope.data_counters[name] = used + min(n, len(nodes))
        return [nodes[i] if i < len(nodes) else None for i in range(n)]

    def _next_name(self, scope: _Scope, name: str) -> str:
        i = scope.counters.get(name, 0)
        scope.counters[name] = i + 1
        return name if i == 0 else f"{name}[{i}]"

    def _breaks(self, el) -> tuple[bool, bool]:
        before = after = False
        for c in _kids(el, "break", "breakBefore", "breakAfter"):
            t = _local(c.tag)
            if t == "break":
                before |= (c.get("before") or "auto") in ("pageArea", "pageEven", "pageOdd", "contentArea")
                after |= (c.get("after") or "auto") in ("pageArea", "pageEven", "pageOdd", "contentArea")
            elif (c.get("targetType") or "auto") in ("pageArea", "contentArea"):
                if t == "breakBefore":
                    before = True
                else:
                    after = True
        return before, after

    def _container(self, el, scope: _Scope, avail_w: float, flowed: bool, root: bool = False) -> list[_Block]:
        """Blocchi di un subform/area/subformSet (tutte le istanze)."""
        tag = _local(el.tag)
        if el.get("presence") in ("hidden", "inactive"):
            return []
        name = el.get("name") if tag == "subform" else None
        out: list[_Block] = []
        if name:
            found = [c for c in self.data_root if _local(c.tag) == name] if self.data_root is not None else []
            if root and not found and self.data_root is not None and len(_kids(self.data_root)) == 1:
                datas = [_kids(self.data_root)[0]]   # radice con nome diverso nei dati
            else:
                datas = self._instances(el, scope)
        else:
            occur = _kid(el, "occur") if tag == "subform" else None
            n = 1
            if occur is not None:
                n = max(int(occur.get("min") or 1), int(occur.get("initial") or 0))
                if occur.get("max") not in (None, "-1"):
                    n = min(n, int(occur.get("max")))
            datas = [scope.data] * n
        for data in datas:
            if name:
                label = self._next_name(scope, name)
                node = scope.node.add(_Node(name))
                inner = _Scope(scope.path + [label], data, node)
            else:
                inner = scope   # contenitore senza nome: trasparente per nomi e dati
            self._scripts(el, ".".join(inner.path) or name or "", subform=True)
            before, after = self._breaks(el)
            if before:
                out.append(_Block(0, brk=True))
            out.extend(self._body(el, inner, avail_w, flowed))
            if after:
                out.append(_Block(0, brk=True))
        return out

    def _body(self, el, scope: _Scope, avail_w: float, flowed: bool) -> list[_Block]:
        tag = _local(el.tag)
        layout = el.get("layout") or ("tb" if tag == "subformSet" else "position")
        if layout not in ("position", "tb", "lr-tb"):
            self.report.note(_("Disposizione «{l}» trattata come verticale").format(l=layout))
        w = measure(el.get("w"), None, self.report) or avail_w
        mg = _kid(el, "margin")
        ins = [measure(mg.get(k), 0, self.report) if mg is not None else 0
               for k in ("leftInset", "topInset", "rightInset", "bottomInset")]
        inner_w = max(1.0, w - ins[0] - ins[2])
        if layout == "position" and tag != "subformSet":
            ops: list[_Op] = []
            extent = 0.0
            for c in self._children(el):
                for b in self._child(c, scope, inner_w, flowed=False):
                    if b.brk:
                        continue
                    x = measure(c.get("x"), 0, self.report) + ins[0]
                    y = measure(c.get("y"), 0, self.report) + ins[1]
                    ops.extend(o.moved(x, y) for o in b.ops)
                    extent = max(extent, y + b.h)
            h = measure(el.get("h"), None, self.report)
            if h is None:
                h = max(measure(el.get("minH"), 0, self.report), extent + ins[3])
            return [_Block(h, ops)]
        blocks: list[_Block] = []
        if ins[1]:
            blocks.append(_Block(ins[1]))
        for c in self._children(el):
            for b in self._child(c, scope, inner_w, flowed=True):
                blocks.append(b if b.brk or not ins[0] else _Block(b.h, [o.moved(ins[0], 0) for o in b.ops]))
        if ins[3]:
            blocks.append(_Block(ins[3]))
        if not flowed:   # sottomodulo a scorrimento dentro uno posizionato: blocco unico
            merged = _merge([b for b in blocks if not b.brk])
            h = measure(el.get("h"), None, self.report)
            if h is not None:
                merged.h = h
            return [merged]
        return blocks

    def _children(self, el) -> list:
        out = []
        for c in _kids(el):
            t = _local(c.tag)
            if t in CONTAINERS:
                out.append(c)
            elif t not in _QUIET:
                self.report.note(_("Elemento non supportato: «{t}»").format(t=t))
        return out

    def _child(self, c, scope: _Scope, avail_w: float, flowed: bool) -> list[_Block]:
        t = _local(c.tag)
        if t in ("subform", "area", "subformSet"):
            return self._container(c, scope, avail_w, flowed)
        if t == "draw":
            return [self._draw(c, avail_w, positioned=not flowed)]
        if t == "field":
            return [self._field(c, scope, avail_w)]
        if t == "exclGroup":
            return [self._excl(c, scope, avail_w)]
        return []

    # ------------------------------------------------------------ caratteri
    def _font(self, el, default_size: float = 10.0) -> tuple[str, float]:
        f = _kid(el, "font")
        if f is None:
            return "Helvetica", default_size
        size = measure(f.get("size"), None) or default_size
        face = (f.get("typeface") or "").strip()
        low = face.lower()
        if not face or low in ("helvetica", "times", "times-roman", "courier"):
            name = "Times" if low.startswith("times") else "Courier" if low == "courier" else "Helvetica"
        else:
            name = "Times" if ("times" in low or "serif" in low and "sans" not in low) else \
                "Courier" if ("courier" in low or "mono" in low) else "Helvetica"
            self.report.note(_("Carattere «{f}» sostituito con {s}").format(f=face, s=name))
        return name, size

    # ------------------------------------------------------------ draw
    def _draw(self, el, avail_w: float, positioned: bool) -> _Block:
        if el.get("presence") in ("hidden", "inactive", "invisible"):
            return _Block(0 if el.get("presence") != "invisible" else measure(el.get("h"), 0) or 0)
        w = measure(el.get("w"), None, self.report) or measure(el.get("minW"), None, self.report) or avail_w
        h = measure(el.get("h"), None, self.report) or measure(el.get("minH"), None, self.report)
        v = _kid(el, "value")
        content = _kids(v)[0] if v is not None and _kids(v) else None
        kind = _local(content.tag) if content is not None else ""
        font, size = self._font(el)
        if kind in ("text", "exData", "integer", "decimal", "date", "float"):
            text = _value_text(el) or ""
            if h is None:
                cpl = max(1, int(w / (size * 0.5)))
                lines = sum(max(1, math.ceil(len(p) / cpl)) for p in text.split("\n"))
                h = lines * size * 1.25 + 2
            para = _kid(el, "para")
            align = {"center": 1, "right": 2, "justifyAll": 3, "justify": 3}.get(para.get("hAlign") if para is not None else "", 0)
            return _Block(h, [_Op("text", pymupdf.Rect(0, 0, w, h), {"text": text, "size": size, "font": font,
                                                                       "align": align})])
        h = h or 0.0
        edge = _path(content, "edge") if content is not None else None
        width = measure(edge.get("thickness"), 0.5) if edge is not None else 0.5
        if kind == "line":
            slope = content.get("slope") or "\\"
            r = pymupdf.Rect(0, 0, w, h)
            p1, p2 = ((r.tl, r.br) if slope == "\\" else (r.bl, r.tr))
            return _Block(h, [_Op("line", r, {"p1": p1, "p2": p2, "width": width})])
        if kind == "rectangle":
            return _Block(h, [_Op("rect", pymupdf.Rect(0, 0, w, h), {"width": width})])
        if kind == "image" and (content.text or "").strip():
            try:
                raw = base64.b64decode(content.text, validate=False)
                return _Block(h, [_Op("image", pymupdf.Rect(0, 0, w, h), {"stream": raw})])
            except Exception:
                pass
        if kind:
            self.report.note(_("Elemento non supportato: «{t}»").format(t=kind))
        return _Block(h)

    # ------------------------------------------------------------ campi
    def _caption(self, el, font: str, size: float) -> tuple[str, str, float | None, float]:
        cap = _kid(el, "caption")
        if cap is None or cap.get("presence") in ("hidden", "inactive"):
            return "", "left", None, size
        text = _value_text(cap) or ""
        _f, csize = self._font(cap, size)
        return text, cap.get("placement") or "left", measure(cap.get("reserve"), None, self.report), csize

    def _scripts(self, el, name: str, subform: bool = False) -> None:
        for ev in _kids(el, "event"):
            if _kid(ev, "script") is not None:
                act = ev.get("activity") or ev.get("name") or "event"
                self.report.scripts.append((name, act))
                msg = (_("Script dell'evento «{e}» nel sottomodulo «{n}»") if subform
                       else _("Script dell'evento «{e}» nel campo «{n}»"))
                self.report.note(msg.format(e=act, n=name))
        if subform:
            v = _kid(el, "variables")
            if v is not None and _kid(v, "script") is not None:
                self.report.scripts.append((name, "variables"))
                self.report.note(_("Script del modello in «{n}»").format(n=name))
        val = _kid(el, "validate")
        if val is not None and _kid(val, "script") is not None:
            self.report.scripts.append((name, "validate"))
            self.report.note(_("Script di validazione nel campo «{n}»").format(n=name))

    def _field(self, el, scope: _Scope, avail_w: float, group: str | None = None, index: int = 0) -> _Block:
        presence = el.get("presence")
        if presence in ("hidden", "inactive"):
            return _Block(0)
        ui = _kid(el, "ui")
        widgets = [c for c in _kids(ui)] if ui is not None else []
        widget = next((c for c in widgets if _local(c.tag) not in ("picture", "extras")), None)
        ui_kind = _local(widget.tag) if widget is not None else "textEdit"
        raw = el.get("name") or ""
        if group is None:
            local = self._next_name(scope, raw) if raw else self._next_name(scope, "field")
            full = ".".join(scope.path + [local])
            data = self._data_for(el, raw, scope) if raw else None
        else:
            full, data = group, None
        if presence == "invisible":
            return _Block(measure(el.get("h"), 0, self.report) or 0)
        font, size = self._font(el)
        props: dict[str, Any] = {}
        kind = E.TEXT
        default_h = 20.0
        value: Any = _value_text(el)
        if data is not None:
            kids = _kids(data)
            value = "\n".join(_text_of(k) for k in kids) if kids else (data.text or "")
        if ui_kind in ("textEdit", "passwordEdit"):
            if widget.get("multiLine") == "1" if widget is not None else False:
                props["multiline"] = True
                default_h = 40.0
            if ui_kind == "passwordEdit":
                props["password"] = True
            tv = _path(el, "value", "text")
            if tv is not None and (tv.get("maxChars") or "0").isdigit() and int(tv.get("maxChars") or 0) > 0:
                props["maxlen"] = int(tv.get("maxChars"))
        elif ui_kind == "numericEdit":
            dec = _path(el, "value", "decimal")
            frac = dec.get("fracDigits") if dec is not None else None
            decimals = int(frac) if frac and frac.lstrip("-").isdigit() and int(frac) >= 0 else \
                (0 if _path(el, "value", "integer") is not None else 2)
            props["fmt"] = fm.Format(fm.NUMBER, decimals=min(10, decimals))
        elif ui_kind == "dateTimeEdit":
            props["fmt"] = fm.Format(fm.DATE, pattern="dd/mm/yyyy")
            if value:
                d = fm.parse_date(str(value).strip()[:10], "yyyy-mm-dd")
                if d:
                    value = fm.format_date(d, "dd/mm/yyyy")
        elif ui_kind == "checkButton":
            kind = E.RADIO if group is not None else E.CHECKBOX
            items = _kids(_kid(el, "items")) if _kid(el, "items") is not None else []
            on = (items[0].text or "").strip() if items else ""
            if kind == E.RADIO:
                on = on or str(index + 1)
            else:
                on = on or "1"
                checked = value is not None and str(value).strip() not in ("", "0") and \
                    (str(value).strip() == on or E._truthy(value, on))
                value = checked
            props["export"] = on
            sz = measure(widget.get("size"), 10, self.report) if widget is not None else 10
            default_h = max(sz, 14)
        elif ui_kind == "choiceList":
            lists = _kids(el, "items")
            disp = [(_text_of(i)) for i in _kids(lists[0])] if lists else []
            save = next((l for l in lists if l.get("save") == "1"), None)
            exports = [_text_of(i) for i in _kids(save)] if save is not None and save is not lists[0] else disp
            if save is not None and save is lists[0] and len(lists) > 1:
                disp = [_text_of(i) for i in _kids(lists[1])]
            if len(exports) != len(disp):
                exports = disp
            props["options"] = list(zip(exports, disp)) or [("", "")]
            open_ = widget.get("open") if widget is not None else None
            if open_ == "multiSelect":
                kind = E.LIST
                props["multiselect"] = True
                default_h = 60.0
                if isinstance(value, str) and value:
                    value = [v for v in value.split("\n") if v]
            else:
                kind = E.COMBO
                if open_ == "userInput":
                    props["editable"] = True
        elif ui_kind == "button":
            kind = E.BUTTON
            default_h = 22.0
        elif ui_kind == "signature":
            kind = E.SIGNATURE
            default_h = 40.0
        else:
            self.report.note(_("Campo «{n}» di tipo «{t}» non supportato").format(n=full, t=ui_kind))
            return _Block(measure(el.get("h"), 0, self.report) or 0)
        if el.get("access") in ("readOnly", "protected", "nonInteractive") and kind != E.BUTTON:
            props["readonly"] = True
        val_el = _kid(el, "validate")
        if val_el is not None and val_el.get("nullTest") == "error":
            props["required"] = True
        if kind in (E.TEXT, E.COMBO, E.LIST):
            props["fontsize"] = size
            props["font"] = font

        cap_text, place, reserve, cap_size = self._caption(el, font, size)
        w = measure(el.get("w"), None, self.report) or measure(el.get("minW"), None, self.report)
        if w is None:
            w = avail_w if group is None and kind not in (E.CHECKBOX, E.RADIO, E.BUTTON) else \
                (min(avail_w, 120.0) if kind != E.BUTTON else 90.0)
        h = measure(el.get("h"), None, self.report) or measure(el.get("minH"), None, self.report)
        vertical = place in ("top", "bottom")
        if h is None:
            h = default_h + ((reserve if reserve is not None else cap_size * 1.4) if cap_text and vertical
                             and kind != E.BUTTON else 0)
        box = pymupdf.Rect(0, 0, w, h)
        ops: list[_Op] = []
        if kind == E.BUTTON:
            props["caption"] = cap_text or raw
        elif cap_text:
            if vertical:
                res = reserve if reserve is not None else min(h * 0.5, cap_size * 1.4)
            else:
                res = reserve if reserve is not None else w * 0.3
            if place == "left":
                crect, box = pymupdf.Rect(0, 0, res, h), pymupdf.Rect(res, 0, w, h)
            elif place == "right":
                crect, box = pymupdf.Rect(w - res, 0, w, h), pymupdf.Rect(0, 0, w - res, h)
            elif place == "top":
                crect, box = pymupdf.Rect(0, 0, w, res), pymupdf.Rect(0, res, w, h)
            else:
                crect, box = pymupdf.Rect(0, h - res, w, h), pymupdf.Rect(0, 0, w, h - res)
            if not vertical and crect.height > cap_size * 1.5:
                pad = (crect.height - cap_size * 1.3) / 2
                crect = pymupdf.Rect(crect.x0, crect.y0 + pad, crect.x1, crect.y1)
            ops.append(_Op("text", crect, {"text": cap_text, "size": cap_size, "font": font, "align": 0}))
        if kind in (E.CHECKBOX, E.RADIO):
            sz = min(measure(widget.get("size"), 10, self.report) if widget is not None else 10, box.height, box.width)
            sz = max(sz, E.MIN_SIZE + 1)
            x0 = box.x0
            y0 = box.y0 + (box.height - sz) / 2
            box = pymupdf.Rect(x0, y0, x0 + sz, y0 + sz)
        if box.width < E.MIN_SIZE + 1 or box.height < E.MIN_SIZE + 1:
            box = pymupdf.Rect(box.x0, box.y0, box.x0 + max(box.width, E.MIN_SIZE + 1), box.y0 + max(box.height, E.MIN_SIZE + 1))
        ops.append(_Op("field", box, {"kind": kind, "name": full, "props": props, "value": value}))
        if group is None:
            self._scripts(el, full)
            node = scope.node.add(_Node(raw or "field", full=full))
            self._calc(el, full, node.parent, kind)
        else:
            self._scripts(el, group)
        return _Block(h, ops)

    def _excl(self, el, scope: _Scope, avail_w: float) -> _Block:
        if el.get("presence") in ("hidden", "inactive"):
            return _Block(0)
        raw = el.get("name") or "group"
        full = ".".join(scope.path + [self._next_name(scope, raw)])
        data = self._data_for(el, el.get("name"), scope) if el.get("name") else None
        value = (data.text or "").strip() if data is not None else _value_text(el)
        layout = el.get("layout") or "position"
        ops: list[_Op] = []
        extent = 0.0
        y_flow = 0.0
        for i, c in enumerate(_kids(el, "field")):
            b = self._field(c, scope, avail_w, group=full, index=i)
            if layout == "position":
                x = measure(c.get("x"), 0, self.report)
                y = measure(c.get("y"), 0, self.report)
            else:
                x, y = 0.0, y_flow
                y_flow += b.h
            ops.extend(o.moved(x, y) for o in b.ops)
            extent = max(extent, y + b.h)
        exports = [o.data["props"].get("export") for o in ops if o.kind == "field"]
        for o in ops:
            if o.kind == "field":
                o.data["value"] = value if value in exports else None
        self._scripts(el, full)
        node = scope.node.add(_Node(raw, full=full))
        self._calc(el, full, node.parent, E.RADIO)
        h = measure(el.get("h"), None, self.report)
        return _Block(h if h is not None else extent, ops)

    # ------------------------------------------------------------ calcoli
    _OPERAND = r"[A-Za-z_][\w]*(?:\[(?:\d+|\*)\])?(?:\.[A-Za-z_][\w]*(?:\[(?:\d+|\*)\])?)*"

    def _calc(self, el, full: str, container: _Node, kind: str) -> None:
        sc = _path(el, "calculate", "script")
        if sc is None:
            return
        parsed = self._parse_calc(sc) if kind == E.TEXT else None
        if parsed is None:
            self.report.scripts.append((full, "calculate"))
            self.report.note(_("Script di calcolo nel campo «{n}»").format(n=full))
            return
        self.calcs.append((full, container, parsed))

    def _parse_calc(self, sc) -> tuple[str, list[str]] | None:
        ctype = (sc.get("contentType") or "application/x-formcalc").lower()
        text = (sc.text or "").strip()
        if "formcalc" not in ctype:
            text = re.sub(r"^this\.rawValue\s*=\s*", "", text)
        text = re.sub(r"^\$\s*=\s*", "", text).rstrip(";").strip()
        text = re.sub(r"\.rawValue\b", "", text)
        op_re = self._OPERAND
        m = re.fullmatch(r"(?i)(sum|avg|max|min)\s*\(\s*(" + op_re + r"(?:\s*,\s*" + op_re + r")*)\s*\)", text)
        if m:
            op = {"sum": "SUM", "avg": "AVG", "max": "MAX", "min": "MIN"}[m.group(1).lower()]
            return op, [a.strip() for a in m.group(2).split(",")]
        for sym, op in (("+", "SUM"), ("*", "PRD")):
            pat = r"\(?\s*(" + op_re + r"(?:\s*" + re.escape(sym) + r"\s*" + op_re + r")+)\s*\)?"
            m = re.fullmatch(pat, text)
            if m:
                return op, [a.strip() for a in m.group(1).split(sym)]
        return None

    @staticmethod
    def _down(nodes: list[_Node], comps: list[tuple[str, str | None]]) -> list[_Node]:
        cur = nodes
        for name, idx in comps:
            nxt: list[_Node] = []
            for n in cur:
                found = n.kids.get(name, [])
                if idx == "*":
                    nxt.extend(found)
                else:
                    k = int(idx or 0)
                    nxt.extend(found[k:k + 1])
            cur = nxt
        return cur

    def _resolve(self, ref: str, container: _Node) -> list[str] | None:
        comps = [(m.group(1), m.group(2)) for m in re.finditer(r"([A-Za-z_]\w*)(?:\[(\d+|\*)\])?", ref)]
        node: _Node | None = container
        while node is not None:
            if comps[0][0] in node.kids:
                leaves = self._down([node], comps)
                names = [n.full for n in leaves if n.full]
                return names or None
            node = node.parent
        return None

    def _resolve_calcs(self) -> None:
        self.calc_result: dict[str, E.Calculation] = {}
        for full, container, (op, refs) in self.calcs:
            names: list[str] = []
            for r in refs:
                found = self._resolve(r, container)
                if not found:
                    names = []
                    break
                names.extend(found)
            if not names or full in names:
                self.report.scripts.append((full, "calculate"))
                self.report.note(_("Script di calcolo nel campo «{n}»").format(n=full))
                continue
            self.calc_result[full] = E.Calculation(op, names)


# ====================================================================== API
def _rename_report(report: ConversionReport) -> None:
    if field_name_style() == "brackets":
        return
    report.scripts = [(_fix_name(n), e) for n, e in report.scripts]


def analyze(pdf: pymupdf.Document) -> ConversionReport:
    """Fattibilità della conversione (nessun documento prodotto)."""
    lay = _Layout(pdf)
    lay.run()
    _rename_report(lay.report)
    return lay.report


def _fontname(font: str) -> str:
    return {"Times": "tiro", "Courier": "cour"}.get(font, "helv")


def _render_static(page: pymupdf.Page, op: _Op) -> None:
    d = op.data
    if op.kind == "text":
        if not d["text"]:
            return
        rc = page.insert_textbox(op.rect, d["text"], fontsize=d["size"], fontname=_fontname(d["font"]),
                                 align=d["align"])
        if rc < 0:
            page.insert_textbox(pymupdf.Rect(op.rect.x0, op.rect.y0, op.rect.x1, op.rect.y1 - rc + 2), d["text"],
                                fontsize=d["size"], fontname=_fontname(d["font"]), align=d["align"])
    elif op.kind == "line":
        page.draw_line(pymupdf.Point(op.rect.x0 + d["p1"].x, op.rect.y0 + d["p1"].y),
                       pymupdf.Point(op.rect.x0 + d["p2"].x, op.rect.y0 + d["p2"].y),
                       color=(0, 0, 0), width=d["width"])
    elif op.kind == "rect":
        page.draw_rect(op.rect, color=(0, 0, 0), width=d["width"])
    elif op.kind == "image":
        try:
            page.insert_image(op.rect, stream=d["stream"], keep_proportion=True)
        except Exception:
            pass


def convert(pdf: pymupdf.Document, progress: Progress = None) -> tuple[bytes, ConversionReport]:
    """Nuovo PDF (byte) con campi standard equivalenti e rapporto; `pdf` non viene modificato."""
    lay = _Layout(pdf)
    lay.run()
    report = lay.report
    if progress:
        progress(0.1, _("Impaginazione"))
    out = pymupdf.open()
    fields: list[tuple[int, _Op]] = []
    for i, ops in enumerate(lay.pages):
        page = out.new_page(width=lay.page_w, height=lay.page_h)
        for op in lay.master + ops:
            if op.kind == "field":
                fields.append((i, op))
            else:
                _render_static(page, op)
    total = max(1, len(fields))
    created: dict[str, tuple[int, int, str]] = {}   # nome XFA -> (pagina, xref, nome finale)
    values: list[tuple[str, Any]] = []
    for n, (i, op) in enumerate(fields):
        d = op.data
        name = _fix_name(d["name"])
        props = dict(d["props"])
        kind = d["kind"]
        try:
            xref = E.create_field(out, i, kind, op.rect, name, **props)
        except E.NameTaken:
            if kind == E.RADIO:
                raise
            k = 1
            while True:
                alt = f"{name}_{k}"
                try:
                    xref = E.create_field(out, i, kind, op.rect, alt, **props)
                    name = alt
                    break
                except E.NameTaken:
                    k += 1
        if d["name"] not in created:
            created[d["name"]] = (i, xref, name)
            if kind in E.FILLABLE:
                values.append((d["name"], d["value"]))
        if progress and n % 10 == 0:
            progress(0.1 + 0.8 * n / total, _("Creazione dei campi"))
    for xname, value in values:
        page, xref, name = created[xname]
        if value is None or value == "" or value == []:
            continue
        try:
            E.set_value(out, page, xref, value)
        except E.FormError:
            report.note(_("Valore «{v}» non valido per il campo «{n}»").format(v=value, n=name))
    for xname, calc in getattr(lay, "calc_result", {}).items():
        if xname not in created:
            continue
        page, xref, _name = created[xname]
        fixed = E.Calculation(calc.op, [created[f][2] if f in created else _fix_name(f) for f in calc.fields])
        E.update_properties(out, page, xref, calc=fixed)
    if lay.calc_result:
        E._set_calc_order(out)
    _rename_report(report)
    report.fields = len(created)
    report.pages = out.page_count
    data = out.tobytes(deflate=True, garbage=3)
    out.close()
    if progress:
        progress(1.0, _("Creazione dei campi"))
    return data, report
