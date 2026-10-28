"""Traduzioni dell'interfaccia.

Le stringhe sorgente sono in italiano. Ogni modulo registra le proprie
traduzioni inglesi con `add_translations("en", {...})` dentro `register()`,
così le sessioni di sviluppo non toccano file condivisi.
"""

from __future__ import annotations

import locale
import os

_language = "it"
_catalogs: dict[str, dict[str, str]] = {"en": {}}


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
    _catalogs.setdefault(lang, {}).update(mapping)


def pgettext(context: str, text: str) -> str:
    """Come `_()` ma con un contesto, per parole italiane con più significati (es. «Annulla»)."""
    if _language == "it":
        return text
    catalog = _catalogs.get(_language, {})
    return catalog.get(f"{context}\x04{text}", catalog.get(text, text))


def _(text: str) -> str:
    """Restituisce `text` tradotto nella lingua attiva (italiano = invariato)."""
    if _language == "it":
        return text
    return _catalogs.get(_language, {}).get(text, text)
