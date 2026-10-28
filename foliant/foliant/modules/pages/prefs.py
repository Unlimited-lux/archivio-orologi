"""Preferenze dell'utente per i valori predefiniti dei dialoghi (NFR-26)."""

from __future__ import annotations


def _ctx():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        return None
    for w in app.topLevelWidgets():
        ctx = getattr(w, "ctx", None)
        if ctx is not None and hasattr(ctx, "paper_size"):
            return ctx
    return None


def user_paper(default: str = "A4") -> str:
    """Formato carta preferito: "A4" o "Letter" (preferenza o impostazioni locali)."""
    ctx = _ctx()
    try:
        return ctx.paper_size() if ctx is not None else default
    except Exception:
        return default


def user_unit(default: str = "mm") -> str:
    """Unità di misura preferita: "mm" o "in"."""
    ctx = _ctx()
    try:
        return ctx.measure_unit() if ctx is not None else default
    except Exception:
        return default
