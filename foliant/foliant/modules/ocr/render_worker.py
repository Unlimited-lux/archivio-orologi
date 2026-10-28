"""Processo di resa delle pagine per l'OCR in background.

Avviato con ``python -m foliant.modules.ocr.render_worker <file.pdf>``: legge da stdin righe
«indice dpi» e per ognuna scrive su stdout un'intestazione JSON (una riga) seguita dai byte RGB
della pagina. Gira in un processo separato, quindi la resa non tiene il lock del motore PDF del
processo dell'interfaccia. Nessuna dipendenza dall'interfaccia (niente Qt).
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    import pymupdf

    from .engine import render_page
    doc = pymupdf.open(argv[0])
    out = sys.stdout.buffer
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            img, zoom, notes = render_page(doc[int(parts[0])], int(parts[1]))
            header = {"h": img.shape[0], "w": img.shape[1], "c": img.shape[2] if img.ndim == 3 else 1,
                      "zoom": zoom, "notes": notes}
            data = img.tobytes()
        except Exception as e:  # riportato al processo principale, che ripiega sulla resa locale
            header, data = {"error": str(e)}, b""
        out.write((json.dumps(header) + "\n").encode("utf-8"))
        out.write(data)
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
