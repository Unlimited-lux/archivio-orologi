"""Moduli PDF: compilazione, creazione e modifica dei campi, dati, appiattimento.

Vedi `docs/specifica.md`, sezione 8 (FRM-*). Logica pura in `engine.py`,
`formats.py`, `interchange.py` e `detect.py`; interfaccia in `fill.py`
(compilazione), `design.py` (modifica modulo), `panel.py` (proprietà),
`fields_panel.py` (elenco campi), `dialogs.py` e `module.py`.
"""

from __future__ import annotations

ORDER = 50

import weakref

# un FormsModule per finestra (Ctrl+N apre altre finestre nello stesso processo)
_modules: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


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
    from .module import FormsModule

    register_translations()
    module = FormsModule(ctx)
    _modules[ctx] = module
    module.register()


def instance(ctx=None):
    """Il modulo della finestra di `ctx`; senza, quello della finestra visibile più recente."""
    if ctx is not None:
        return _modules.get(ctx)
    items = list(_modules.items())
    for c, module in reversed(items):
        win = getattr(c, "window", None)
        try:
            if win is not None and win.isVisible():
                return module
        except RuntimeError:   # finestra già distrutta
            continue
    return items[-1][1] if items else None
