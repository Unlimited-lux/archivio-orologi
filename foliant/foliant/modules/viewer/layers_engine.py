"""Livelli (VIS-20, parte di VIS-21): logica pura senza interfaccia.

Usa la configurazione d'interfaccia dei livelli del motore
(`layer_ui_configs` / `set_layer_ui_config`): cambia la visibilità solo in
memoria, senza modificare il documento. Il motore applica da sé i gruppi
radio (`/RBGroups`) e i livelli bloccati (`/Locked`); qui lo si verifica e,
per sicurezza, si rifiutano i cambi dei livelli bloccati.
Le funzioni che ricevono `pdf` vanno chiamate tenendo `ENGINE_LOCK`.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

CHECKBOX = "checkbox"
RADIOBOX = "radiobox"
LABEL = "label"

_ON, _TOGGLE, _OFF = 0, 1, 2


@dataclass(frozen=True)
class Layer:
    number: int
    text: str
    depth: int
    type: str
    on: bool
    locked: bool

    @property
    def is_label(self) -> bool:
        return self.type == LABEL


# Stato salvato: {numero: (testo, acceso)}. Il testo serve a riconoscere che
# la struttura dei livelli è la stessa prima di riapplicarlo.
State = dict[int, tuple[str, bool]]


def layers(pdf: pymupdf.Document) -> list[Layer]:
    out = []
    for c in pdf.layer_ui_configs() or ():
        out.append(Layer(number=int(c.get("number", len(out))), text=str(c.get("text") or ""),
                         depth=int(c.get("depth") or 0), type=str(c.get("type") or CHECKBOX),
                         on=bool(c.get("on")), locked=bool(c.get("locked"))))
    return out


def has_layers(pdf: pymupdf.Document) -> bool:
    return any(not layer.is_label for layer in layers(pdf))


def state(pdf: pymupdf.Document) -> State:
    return {layer.number: (layer.text, layer.on) for layer in layers(pdf) if not layer.is_label}


def set_visible(pdf: pymupdf.Document, number: int, visible: bool) -> bool:
    """Accende o spegne un livello. Restituisce True se lo stato è cambiato."""
    current = {layer.number: layer for layer in layers(pdf)}
    layer = current.get(number)
    if layer is None or layer.is_label or layer.locked or layer.on == visible:
        return False
    pdf.set_layer_ui_config(number, _ON if visible else _OFF)
    return state(pdf) != {n: (l.text, l.on) for n, l in current.items() if not l.is_label}


def apply_state(pdf: pymupdf.Document, saved: State) -> bool:
    """Riapplica uno stato salvato (es. dopo annulla/ripeti). True se qualcosa è cambiato.

    Si applica solo ai livelli con lo stesso numero e nome; prima si spengono
    quelli da spegnere, poi si accendono gli altri (così i gruppi radio
    restano coerenti).
    """
    if not saved:
        return False
    now = {layer.number: layer for layer in layers(pdf)}
    changed = False
    for want_on in (False, True):
        for number, (text, on) in saved.items():
            layer = now.get(number)
            if layer is None or layer.text != text or layer.locked or layer.is_label or on != want_on:
                continue
            if layer.on != on:
                pdf.set_layer_ui_config(number, _ON if on else _OFF)
                changed = True
        now = {layer.number: layer for layer in layers(pdf)}
    return changed


def show_all(pdf: pymupdf.Document) -> bool:
    """Accende tutti i livelli non bloccati. Nei gruppi radio (dove un solo
    livello può essere acceso) lo stato resta quello attuale."""
    changed = False
    for layer in layers(pdf):
        if layer.type == CHECKBOX and not layer.locked and not layer.on:
            pdf.set_layer_ui_config(layer.number, _ON)
            changed = True
    return changed


def matches(layer: Layer, text: str) -> bool:
    text = (text or "").strip().casefold()
    return not text or text in layer.text.casefold()


def _ocg_xrefs_by_layer(pdf: pymupdf.Document) -> dict[int, int]:
    """Associa i numeri dei livelli d'interfaccia agli OCG (per nome; i nomi
    ripetuti nell'ordine in cui compaiono)."""
    by_name: dict[str, list[int]] = {}
    for xref, info in (pdf.get_ocgs() or {}).items():
        by_name.setdefault(str(info.get("name") or ""), []).append(xref)
    mapping = {}
    for layer in layers(pdf):
        if layer.is_label:
            continue
        xrefs = by_name.get(layer.text)
        if xrefs:
            mapping[layer.number] = xrefs.pop(0)
    return mapping


def save_as_default(pdf: pymupdf.Document) -> bool:
    """Scrive la visibilità corrente nella configurazione predefinita (`/D` `/ON` `/OFF`).

    Va chiamata dentro `Document.change()` (modifica il documento).
    """
    mapping = _ocg_xrefs_by_layer(pdf)
    if not mapping:
        return False
    current = {layer.number: layer.on for layer in layers(pdf)}
    on = sorted({x for n, x in mapping.items() if current.get(n)})
    off = sorted({x for n, x in mapping.items() if not current.get(n)} - set(on))
    default = pdf.get_layer(-1) or {}
    kwargs = {"on": on, "off": off}
    if default.get("rbgroups"):
        kwargs["rbgroups"] = default["rbgroups"]
    if default.get("locked"):
        kwargs["locked"] = default["locked"]
    pdf.set_layer(-1, **kwargs)
    return True
