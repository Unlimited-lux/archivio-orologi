"""Strumenti: confronto documenti e operazioni in blocco."""

ORDER = 100


def register(ctx) -> None:
    from .module import register as _register
    _register(ctx)
