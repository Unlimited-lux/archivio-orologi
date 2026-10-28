"""Assistente IA (sezione 12 della specifica)."""

ORDER = 110


def register(ctx) -> None:
    from .ui import register as _register
    _register(ctx)
