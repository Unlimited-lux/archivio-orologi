# Foliant — editor PDF per Linux

Foliant è un editor PDF desktop per Ubuntu/Linux: lettura, commenti, modifica di
testo e immagini, gestione pagine, OCR (anche per rendere modificabili i PDF
scannerizzati), moduli, firma, protezione, conversione, confronto e operazioni in
blocco. Funziona offline; l'assistente IA è facoltativo e usa un fornitore
configurato dall'utente.

La specifica funzionale è in `docs/specifica.md`, lo stato del lavoro in
`docs/avanzamento.md`.

## Installazione (Ubuntu)

```bash
./packaging/install.sh          # dipendenze di sistema, ambiente Python, icona e voce nel menu
foliant documento.pdf
```

Opzioni: `--default-pdf` imposta Foliant come lettore PDF predefinito.
Disinstallazione: `./packaging/uninstall.sh`.

## Sviluppo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m foliant                                  # avvio
QT_QPA_PLATFORM=offscreen python -m pytest         # test (senza schermo)
```

Dipendenze di sistema per test e OCR: `tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng
libegl1 libgl1 libxkbcommon0 libxcb-cursor0 libfontconfig1 libdbus-1-3`.
LibreOffice (facoltativo) per creare PDF da documenti Office.

### Licenze

Il motore PDF (PyMuPDF) è distribuito con licenza AGPL-3.0 o licenza commerciale
Artifex: Foliant è quindi AGPL-3.0-or-later. Per distribuire Foliant a codice
chiuso serve la licenza commerciale del motore. Interfaccia: Qt/PySide6 (LGPL).
Icone: set open source con licenza ISC (`foliant/resources/icons/LICENSE-lucide.txt`).

## Architettura

```
foliant/
  core/        nucleo senza interfaccia
    document.py   Document: apertura, modifiche annullabili, salvataggio atomico, protezione
    engine.py     ENGINE_LOCK: unico lock per ogni accesso al motore PDF
    errors.py     errori con messaggi per l'utente
  ui/          interfaccia Qt
    main_window.py  finestra: barra modalità, schede, pannelli, barra di stato
    document_view.py vista pagine: impaginazione, zoom, rendering, coordinate, strumenti
    context.py      Context: API per i moduli
    tools.py        Tool (strumenti interattivi), selezione testo, mano
    tasks.py        run_task: attività lunghe con avanzamento/annulla
    theme.py        tema chiaro/scuro e colori
    icons.py        icone (nomi = file in resources/icons)
    home.py         schermata Home
  modules/     funzioni, una cartella per area (scoperte automaticamente)
  api.py       tutto ciò che serve a un modulo
  i18n.py      traduzioni (sorgente italiano, catalogo inglese per modulo)
tests/
  conftest.py  fixture comuni: sample_pdf, encrypted_pdf, settings, window, make_pdf()
  modules/<nome>/  test di ciascun modulo
```

## Scrivere un modulo

Un modulo è una cartella `foliant/modules/<nome>/` con un `__init__.py`:

```python
from foliant.api import Context, Tool, _, add_translations

ORDER = 40   # ordine di caricamento

def register(ctx: Context) -> None:
    add_translations("en", {"Pagine": "Pages", "Ruota a destra": "Rotate right"})
    mode = ctx.add_mode("pagine", _("Pagine"), "layout-grid", order=40)
    mode.add_action(ctx.action(_("Ruota a destra"), "rotate-cw", lambda: ruota(ctx, 90)))

def ruota(ctx, gradi):
    view = ctx.current_view()
    if view is None:
        return
    with view.document.change(_("Ruota pagina")) as pdf:
        page = pdf[view.current_page]
        page.set_rotation((page.rotation + gradi) % 360)
```

### Regole (valgono per tutti i moduli)

1. **Ogni modifica** del documento dentro `with document.change(_("Etichetta")) as pdf:`.
   È un passo di annulla/ripeti; se il blocco solleva un'eccezione il documento torna
   com'era. Non toccare mai il file su disco: il salvataggio è dell'utente.
2. **Ogni accesso al motore PDF** (`pymupdf`) tenendo `ENGINE_LOCK` (`change()` lo tiene già).
   Nel thread dell'interfaccia, per letture brevi: `with ENGINE_LOCK: ...`.
3. **Operazioni lunghe** (OCR, conversione, batch…) con `ctx.run_task(titolo, funzione, ...)`:
   la funzione riceve `progress` (chiamare `progress.update(frazione, testo)`, che solleva
   `OperationCancelled` se l'utente annulla), gira in un thread e **non deve toccare widget**.
   Per modificare il documento a fine lavoro, restituire i dati e applicarli con
   `document.change()` o `document.replace_with(bytes, etichetta)`.
4. **Non conservare oggetti pagina** (`pdf[i]`) oltre l'operazione: dopo annulla/ripeti il
   documento interno viene sostituito. Usare indici di pagina.
5. **Coordinate**: punti PDF della pagina *non ruotata*, origine in alto a sinistra (le stesse
   del motore per testo, annotazioni e disegno). Gli strumenti (`Tool`) ricevono già
   `ev.page` ed `ev.point` in queste coordinate; `Tool.paint()` e gli `view.overlays` ricevono
   un `QPainter` già trasformato: si disegna direttamente in punti. Conversioni:
   `view.page_transform(i)`, `view.page_to_view(i, rect)`, `view.view_to_page(pos)`.
6. **Testi** in italiano dentro `_()`; traduzioni inglesi con `add_translations("en", {...})`
   in `register()`.
7. **Icone** con `icon("nome")` o `bind_icon(azione, "nome")` (nomi dei file in
   `foliant/resources/icons`). **Colori** con `theme.color("accent")` ecc., mai valori fissi.
8. **Errori**: messaggi chiari per l'utente (`ctx.error(titolo, testo)`); un errore di un
   modulo non deve mai chiudere l'applicazione.
9. **Originalità**: nessun testo, icona o risorsa copiati da prodotti commerciali.

### API del contesto (`foliant/ui/context.py`)

| Metodo | Uso |
|---|---|
| `current_view()`, `current_document()`, `views()` | vista/documento attivi |
| `open_path(p)`, `open_document(doc)` | aprire file o mostrare un documento creato dal modulo |
| `add_mode(id, titolo, icona, order)` → `Mode` | modalità con barra contestuale (`mode.add_action`, `add_widget`, `add_separator`, segnali `entered`/`left`) |
| `mode("leggi")` | aggiungere azioni a una modalità esistente |
| `action(testo, icona, slot, shortcut=, checkable=, requires_document=)` | crea un'azione (disattivata senza documento) |
| `add_menu_action(azione, sezione)` | voce nel menu principale ☰ |
| `add_home_action(titolo, icona, callback, descrizione, order)` | azione rapida nella Home |
| `add_sidebar_panel(id, titolo, icona, widget, order)` / `show_sidebar_panel(id)` | pannello sinistro (`widget.set_view(view)` chiamato a ogni cambio documento) |
| `add_properties_panel(id, widget)` / `show_properties(id)` / `hide_properties()` | pannello destro |
| `set_tool(tool)` | strumento attivo sulla vista (None = selezione testo) |
| `add_view_menu_provider(fn)` | voci nel menu del tasto destro sulle pagine: `fn(view, page_event, menu)` |
| `run_task(titolo, fn, *args)` | lavoro lungo con avanzamento |
| `status`, `error`, `info`, `confirm`, `ask_open_paths`, `ask_save_path`, `ask_directory` | messaggi e dialoghi |
| segnali `currentViewChanged`, `documentOpened`, `documentClosed`, `documentChanged`, `modeChanged` | eventi |

`DocumentView` offre: `current_page`, `go_to_page(i)`, `page_count`, `words(i)`,
`text_selection` (+ `selectionChanged`), `set_search_hits(dict, current)`, `overlays`,
`set_tool()`, `render_page_image(i, zoom)`, `ensure_visible(i, rect)`, `zoom`, `set_zoom()`.

### Modalità e cartelle

| Modalità (id, ordine) | Cartella modulo | Contenuto |
|---|---|---|
| `leggi` (10, nel nucleo) | `modules/viewer` | miniature, segnalibri, ricerca, allegati, livelli, stampa, proprietà, presentazione |
| `commenta` (20) | `modules/annotations` | evidenziazioni, note, forme, disegno, timbri, misure, elenco commenti |
| `modifica` (30) | `modules/editing` | modifica testo, immagini, oggetti, collegamenti |
| `pagine` (40) | `modules/pages` | organizza, inserisci, estrai, dividi, unisci, ritaglia, numeri, filigrane, sfondi |
| `moduli` (50) | `modules/forms` | compilazione e creazione campi, dati, appiattimento |
| `ocr` (60) | `modules/ocr` | riconoscimento testo, PDF ricercabile e modificabile |
| `converti` (70) | `modules/convert` | da/verso Office, immagini, testo, HTML, PDF/A; crea; comprimi |
| `proteggi` (80) | `modules/security` | password, permessi, redazione, sanificazione, metadati |
| `firma` (90) | `modules/sign` | firma grafica e digitale, verifica |
| `strumenti` (100) | `modules/tools` | confronto documenti, operazioni in blocco |
| `ia` (110) | `modules/ai` | assistente IA con fornitore configurato dall'utente |

Chi lavora su un modulo modifica **solo** la propria cartella e `tests/modules/<nome>/`.
Le richieste di modifica al nucleo vanno in `foliant/modules/<nome>/CORE_REQUESTS.md`
(con una soluzione locale nel frattempo); nuove dipendenze in
`foliant/modules/<nome>/DEPS.txt`.
