"""Traduzioni dell'interfaccia.

Le stringhe sorgente sono in italiano. Ogni modulo registra le proprie
traduzioni inglesi con `add_translations("en", {...})` dentro `register()`,
così le sessioni di sviluppo non toccano file condivisi.

Ogni modulo ha il proprio catalogo: `_()` chiamata dal codice di
`foliant.modules.<nome>` cerca prima le traduzioni registrate da quel modulo, poi
quelle del nucleo, poi quelle degli altri moduli. Così la stessa parola italiana
può avere traduzioni diverse in moduli diversi («Valuta» → «Currency» in Moduli,
«Evaluate» nell'Assistente) senza che l'ultimo modulo registrato le cambi a tutti.
Dentro lo stesso modulo (e nel nucleo) le parole ambigue usano `pgettext`.
"""

from __future__ import annotations

import locale
import os
import sys

_language = "it"
_catalogs: dict[str, dict[str, str]] = {"en": {}}        # nucleo (registrato fuori dai moduli)
_shared: dict[str, dict[str, str]] = {"en": {}}          # tutti i moduli: il primo che registra una voce
_owned: dict[tuple[str, str], dict[str, str]] = {}       # (lingua, modulo) -> voci di quel modulo


def _owner(depth: int) -> str | None:
    """Nome del modulo (`foliant.modules.<nome>`) del codice chiamante, None per il nucleo."""
    try:
        name = sys._getframe(depth).f_globals.get("__name__", "")
    except ValueError:
        return None
    if name.startswith("foliant.modules."):
        parts = name.split(".")
        return parts[2] if len(parts) > 2 else None
    return None


def detect_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value:
            return "it" if value.lower().startswith("it") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    return "it" if loc.lower().startswith("it") else "en"


def set_language(lang: str) -> None:
    global _language
    _language = lang if lang in ("it", "en") else "it"
    if _language == "en":
        from .ui import i18n_en  # noqa: F401  (registra le traduzioni del nucleo)


def language() -> str:
    return _language


def add_translations(lang: str, mapping: dict[str, str]) -> None:
    """Registra traduzioni: del modulo chiamante se è in `foliant.modules`, altrimenti del nucleo."""
    owner = _owner(2)
    if owner is None:
        _catalogs.setdefault(lang, {}).update(mapping)
        return
    _owned.setdefault((lang, owner), {}).update(mapping)
    shared = _shared.setdefault(lang, {})
    for key, value in mapping.items():
        shared.setdefault(key, value)


def _lookup(key: str, owner: str | None) -> str | None:
    if owner is not None:
        own = _owned.get((_language, owner))
        if own is not None and key in own:
            return own[key]
    core = _catalogs.get(_language)
    if core is not None and key in core:
        return core[key]
    shared = _shared.get(_language)
    if shared is not None and key in shared:
        return shared[key]
    return None


def pgettext(context: str, text: str) -> str:
    """Come `_()` ma con un contesto, per parole italiane con più significati (es. «Annulla»)."""
    if _language == "it":
        return text
    owner = _owner(2)
    found = _lookup(f"{context}\x04{text}", owner)
    if found is None:
        found = _lookup(text, owner)
    return text if found is None else found


def _(text: str) -> str:
    """Restituisce `text` tradotto nella lingua attiva (italiano = invariato)."""
    if _language == "it":
        return text
    found = _lookup(text, _owner(2))
    return text if found is None else found
