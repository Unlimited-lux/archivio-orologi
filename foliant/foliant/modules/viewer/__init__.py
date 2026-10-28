"""Modulo Visualizzazione e navigazione (sezione 1 della specifica, VIS-*).

Ogni sotto-funzione vive in un file proprio con una funzione `install(ctx)`.
Un errore in una sotto-funzione non impedisce il caricamento delle altre.
"""

from __future__ import annotations

import importlib
import logging
import os

from foliant.api import Context

ORDER = 5

log = logging.getLogger("foliant.modules.viewer")

# Ordine di installazione: i pannelli prima di ciò che li usa.
PARTS = (
    "navigation",     # cronologia Indietro/Avanti, vai a pagina con etichette, collegamenti
    "thumbnails",     # pannello Miniature
    "bookmarks",      # pannello Segnalibri
    "search",         # barra e pannello di ricerca
    "advsearch",      # ricerca avanzata e su più documenti
    "attachments",    # pannello Allegati
    "layers",         # pannello Livelli
    "printing",       # stampa
    "properties",     # proprietà del documento
    "presentation",   # presentazione
    "reading",        # modalità lettura, schermo intero, scorrimento automatico
    "tools",          # zoom area, istantanea
    "session",        # posizione per file, vista iniziale, schede, sessione
    "recovery",       # salvataggio automatico e recupero dopo crash
    "reload",         # ripristina e ricarica
    "archives",       # archivi ZIP
    "articles",       # thread di articoli
    "integrity",      # file danneggiati e funzioni non supportate
    "appearance",     # temi di lettura
    "measure",        # coordinate, griglia, riquadri di pagina
    "share",          # invio per e-mail e copia del file
    "files",          # gestione dei file locali
    "speech",         # lettura ad alta voce
    "reflow",         # modalità riflusso
    "prefs",          # preferenze di visualizzazione
    "commands",       # ricerca dei comandi e scorciatoie (per ultima: vede tutte le azioni)
)


def register(ctx: Context) -> None:
    ctx.viewer_parts = {}
    # Per lo sviluppo: FOLIANT_VIEWER_PARTS="thumbnails,bookmarks" carica solo quelle parti.
    only = {p.strip() for p in os.environ.get("FOLIANT_VIEWER_PARTS", "").split(",") if p.strip()}
    for name in PARTS:
        if only and name not in only:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
        except ModuleNotFoundError as e:
            if e.name == f"{__name__}.{name}":
                continue  # parte non ancora presente
            log.exception("viewer: parte %s non importabile", name)
            continue
        except Exception:
            log.exception("viewer: parte %s non importabile", name)
            continue
        try:
            ctx.viewer_parts[name] = mod.install(ctx)
        except Exception:
            log.exception("viewer: errore nell'installazione di %s", name)
