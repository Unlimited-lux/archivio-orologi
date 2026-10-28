"""Moduli funzionali di Foliant.

Ogni sottocartella è un modulo indipendente con un `__init__.py` che definisce:

    ORDER = 30                 # ordine di caricamento (facoltativo, predefinito 100)

    def register(ctx):         # ctx: foliant.api.Context
        ...

I moduli vengono scoperti automaticamente: per aggiungerne uno basta creare
la cartella, senza modificare file condivisi. Un modulo che fallisce non
impedisce l'avvio dell'applicazione.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil

log = logging.getLogger("foliant.modules")


def discover() -> list[tuple[int, str, object]]:
    found = []
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg or info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception as e:  # pragma: no cover - riportato all'utente
            log.exception("Modulo %s non importabile", info.name)
            found.append((10_000, info.name, e))
            continue
        found.append((getattr(mod, "ORDER", 100), info.name, mod))
    return sorted(found, key=lambda t: (t[0], t[1]))


def load_modules(ctx) -> list[tuple[str, Exception | None]]:
    results: list[tuple[str, Exception | None]] = []
    for _order, name, mod in discover():
        if isinstance(mod, Exception):
            results.append((name, mod))
            continue
        register = getattr(mod, "register", None)
        if register is None:
            continue
        try:
            register(ctx)
            results.append((name, None))
        except Exception as e:
            log.exception("Modulo %s: errore in register()", name)
            results.append((name, e))
    return results
