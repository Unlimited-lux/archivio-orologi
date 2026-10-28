"""Moduli PDF: compilazione, creazione e modifica dei campi, dati, appiattimento.

Vedi `docs/specifica.md`, sezione 8 (FRM-*). Logica pura in `engine.py`,
`formats.py`, `interchange.py` e `detect.py`; interfaccia in `fill.py`
(compilazione), `design.py` (modifica modulo), `panel.py` (proprietà),
`fields_panel.py` (elenco campi), `dialogs.py` e `module.py`.
"""

from __future__ import annotations

ORDER = 50

_module = None


def register_translations() -> None:
    """Traduzioni inglesi del modulo (catalogo proprio del modulo nel nucleo)."""
    from foliant.api import add_translations

    from .i18n_en import EN

    add_translations("en", EN)
    try:
        from .panel import TRANSLATIONS as PANEL_EN
        add_translations("en", PANEL_EN)
    except Exception:
        pass
    for name in ("fields_panel", "formula", "batch", "xfa_convert"):
        try:
            mod = __import__(f"{__name__}.{name}", fromlist=["TRANSLATIONS"])
            add_translations("en", mod.TRANSLATIONS)
        except Exception:
            pass


def register(ctx) -> None:
    global _module
    from .module import FormsModule

    register_translations()
    _module = FormsModule(ctx)
    _module.register()


def instance():
    return _module
