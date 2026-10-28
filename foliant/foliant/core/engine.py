"""Accesso al motore PDF.

Il motore PDF non è thread-safe: ogni accesso (lettura, rendering, modifica)
deve avvenire tenendo ENGINE_LOCK. Il thread dell'interfaccia usa
`try_engine()` per non bloccarsi mai mentre un'attività in background lavora.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

import pymupdf

ENGINE_LOCK = threading.RLock()


@contextmanager
def try_engine() -> Iterator[bool]:
    """Prova ad acquisire il lock senza attendere; restituisce True se acquisito."""
    acquired = ENGINE_LOCK.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            ENGINE_LOCK.release()


def configure() -> None:
    """Silenzia i messaggi del motore su stderr (gli errori arrivano come eccezioni)."""
    tools = getattr(pymupdf, "TOOLS", None)
    for name in ("mupdf_display_errors", "mupdf_display_warnings"):
        fn = getattr(tools, name, None)
        if fn is not None:
            try:
                fn(False)
            except Exception:
                pass
