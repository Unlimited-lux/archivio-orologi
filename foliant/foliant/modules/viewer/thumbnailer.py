"""Generatore di miniature per il file manager (VIS-53).

Uso (specifica freedesktop dei thumbnailer):
    python -m foliant.modules.viewer.thumbnailer -s %s %u %o

Gira in un processo separato, senza interfaccia: non esegue script né azioni,
non produce immagini dei PDF cifrati e si interrompe dopo un limite di tempo
e di memoria. Codici di uscita: 0 ok, 2 file cifrato, 3 file non valido,
4 tempo scaduto, 1 altri errori.

Riservatezza: Foliant NON registra questo generatore presso il file manager, perché
la cache delle miniature (~/.cache/thumbnails) conserverebbe un'impronta del percorso
di ogni documento con un'immagine del suo contenuto. Il programma resta disponibile
solo per chi lo installa a mano; `privacy.remove_legacy_thumbnailer` toglie la voce
`foliant.thumbnailer` se una versione precedente l'aveva installata.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
from urllib.parse import unquote, urlparse

TIME_LIMIT = 4            # secondi
MEMORY_LIMIT = 768 * 1024 * 1024

THUMBNAILER_ENTRY = """[Thumbnailer Entry]
TryExec=foliant
Exec=foliant-thumbnailer -s %s %u %o
MimeType=application/pdf;
"""


def _path(arg: str) -> str:
    if arg.startswith("file://"):
        return unquote(urlparse(arg).path)
    return arg


def make_thumbnail(src: str, dst: str, size: int = 256) -> int:
    import pymupdf
    try:
        tools = getattr(pymupdf, "TOOLS", None)
        if tools is not None:
            tools.mupdf_display_errors(False)
            tools.mupdf_display_warnings(False)
    except Exception:
        pass
    try:
        doc = pymupdf.open(src, filetype="pdf")
    except Exception:
        return 3
    try:
        if doc.needs_pass or doc.is_encrypted:
            return 2   # nessuna immagine del contenuto di un file cifrato
        if doc.page_count == 0:
            return 3
        page = doc[0]
        r = page.rect
        scale = size / max(r.width, r.height, 1)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
        tmp = dst + ".tmp.png"
        pix.save(tmp)
        os.replace(tmp, dst)
        return 0
    except Exception:
        return 1
    finally:
        doc.close()


def _timeout(_sig, _frame):
    os._exit(4)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="foliant-thumbnailer")
    parser.add_argument("-s", "--size", type=int, default=256)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--time-limit", type=float, default=TIME_LIMIT)
    args = parser.parse_args(argv)
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    except Exception:
        pass
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, _timeout)
        signal.setitimer(signal.ITIMER_REAL, max(0.1, args.time_limit))
    code = make_thumbnail(_path(args.input), args.output, max(16, min(1024, args.size)))
    if hasattr(signal, "SIGALRM"):
        signal.setitimer(signal.ITIMER_REAL, 0)
    return code


def install(ctx):  # nessuna interfaccia: il generatore è un programma a parte
    return None


if __name__ == "__main__":
    sys.exit(main())
