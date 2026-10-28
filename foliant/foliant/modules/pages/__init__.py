"""Modulo Pagine: organizzatore a griglia e operazioni sulle pagine (PAG-*)."""

ORDER = 40


def register(ctx) -> None:
    from .module import register as _register
    _register(ctx)
