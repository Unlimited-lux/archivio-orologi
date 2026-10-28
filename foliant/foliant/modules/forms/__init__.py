"""Moduli PDF: compilazione, creazione e modifica dei campi, dati, appiattimento.

Vedi `docs/specifica.md`, sezione 8 (FRM-*). Logica pura in `engine.py`,
`formats.py`, `interchange.py` e `detect.py`; interfaccia in `fill.py`
(compilazione), `design.py` (modifica modulo), `panel.py` (proprietà),
`fields_panel.py` (elenco campi), `dialogs.py` e `module.py`.
"""

from __future__ import annotations

ORDER = 50

_module = None


def register(ctx) -> None:
    global _module
    from foliant.api import add_translations

    from .i18n_en import EN
    from .module import FormsModule

    add_translations("en", EN)
    try:
        from .panel import TRANSLATIONS as PANEL_EN
        add_translations("en", PANEL_EN)
    except Exception:
        pass
    try:
        from .fields_panel import TRANSLATIONS as FIELDS_EN
        add_translations("en", FIELDS_EN)
    except Exception:
        pass
    _module = FormsModule(ctx)
    _module.register()


def instance():
    return _module
