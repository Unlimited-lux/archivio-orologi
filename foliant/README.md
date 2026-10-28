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

## Uso quotidiano

La barra in alto contiene le modalità; ognuna mostra i propri strumenti nella barra sotto.
Ogni modifica si annulla con Ctrl+Z e il file originale cambia solo quando salvi (Ctrl+S).
Ctrl+? mostra tutte le scorciatoie, Ctrl+, apre le preferenze (tema, lingua, nome dell'autore).

| Voglio… | Dove |
|---|---|
| Leggere, cercare (Ctrl+F), stampare (Ctrl+P) | **Leggi** |
| Evidenziare, sottolineare, aggiungere note, timbri, forme | **Commenta** |
| Correggere il testo o le immagini di un PDF | **Modifica** → «Modifica testo», «Oggetti», «Aggiungi immagine» |
| Modificare un PDF **scannerizzato** | **OCR** → «Esegui OCR» in modalità modificabile, poi **Modifica** |
| Unire, dividere, ruotare, riordinare, estrarre pagine | **Pagine** |
| Compilare o creare moduli, esportare i dati | **Moduli** |
| Trasformare in Word, Excel, PowerPoint, immagini; comprimere | **Converti** |
| Password, permessi, redazione di dati riservati | **Proteggi** |
| Firma grafica o digitale, verifica delle firme | **Firma** |
| Confrontare due PDF; filigrane, numerazione e altre operazioni su molti file | **Strumenti** |
| Riassumere, tradurre, chiedere al documento (facoltativo) | **Assistente IA** (si configura il fornitore nelle sue impostazioni) |

I problemi noti e lo stato delle funzioni sono in `docs/avanzamento.md`.

## Sviluppo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m foliant                                  # avvio
QT_QPA_PLATFORM=offscreen python -m pytest         # test (senza schermo)
```

Dipendenze di sistema per test e OCR: `tesseract-ocr tesseract-ocr-ita tesseract-ocr-eng tesseract-ocr-osd ghostscript qpdf fonts-dejavu-core
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
    document.py   Document: apertura, modifiche annullabili, guardie, salvataggio atomico, protezione
    incremental.py salvataggio incrementale dei documenti firmati (byte originali + modifiche in coda)
    security.py   accesso (utente/proprietario), permessi effettivi /P e /Perms, descrizione della cifratura
    engine.py     ENGINE_LOCK: unico lock per ogni accesso al motore PDF
    render.py     resa per stampa/immagini; annotations_hidden (flag Hidden temporaneo)
    errors.py     errori con messaggi per l'utente
  ui/          interfaccia Qt
    main_window.py  finestra: barra modalità, schede, pannelli, barra di stato
    document_view.py vista pagine: impaginazione, zoom, rendering, coordinate, strumenti, guide
    rulers.py       righelli della vista (VIS-39)
    context.py      Context: API per i moduli
    tools.py        Tool (strumenti interattivi), selezione testo (anche su più pagine), mano
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
   Indicare il tipo di modifica (`kind="annotation"`, `"form"`, `"pages"`, `"metadata"`;
   predefinito `"content"`): le guardie (es. documento firmato) decidono in base a quello e
   possono annullare la modifica con `OperationCancelled`, come un documento bloccato.
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
   in `register()`. Ogni modulo ha il proprio catalogo: `_()` nel codice di
   `foliant.modules.<nome>` usa prima le voci di quel modulo, poi quelle del nucleo, poi quelle
   degli altri moduli. Le parole con più significati nello stesso modulo usano
   `pgettext("contesto", "Annulla")` con la voce `"contesto\x04Annulla"`. Inglese americano
   («Color», «Canceled»).
7. **Icone** con `icon("nome")` o `bind_icon(azione, "nome")` (nomi dei file in
   `foliant/resources/icons`). **Colori** con `theme.color("accent")` ecc., mai valori fissi.
8. **Errori**: messaggi chiari per l'utente (`ctx.error(titolo, testo)`); un errore di un
   modulo non deve mai chiudere l'applicazione.
9. **Originalità**: nessun testo, icona o risorsa copiati da prodotti commerciali.

### Scorciatoie da tastiera

La tabella UX-18 della specifica è l'unica autorità. Il nucleo registra: Ctrl+O, Ctrl+T (Home),
Ctrl+N, Ctrl+S, Ctrl+Maiusc+S, Ctrl+W, Ctrl+Maiusc+T, Ctrl+Q, Ctrl+Z, Ctrl+Maiusc+Z / Ctrl+Y, Ctrl+A,
Ctrl++ / Ctrl+= / Ctrl+-, Ctrl+0 (adatta pagina), Ctrl+1 (dimensione reale), Ctrl+2 (adatta larghezza),
N / P, Spazio, Inizio / Fine, Ctrl+L, Ctrl+Tab, Ctrl+Pag su/giù, Alt+1…9, Ctrl+Alt+0…9, F1, F6, F9,
Maiusc+F9, F10, F11, Esc, Ctrl+virgola, Ctrl+?, H, V, Ctrl+R (Ruota vista). Spettano ai moduli: Ctrl+P, Ctrl+F,
Ctrl+G, Alt+Invio, Alt+frecce, F5 (viewer); Ctrl+H (text_edit); Ctrl+Alt+I (ai). Una scorciatoia
duplicata disattiva entrambe le azioni: il test `test_no_duplicate_shortcuts` lo impedisce.
«Righelli» (menu ☰ › Vista) non ha scorciatoia: UX-18 riserva Ctrl+R ai righelli e Ctrl+Maiusc++/-
alla rotazione della vista, ma oggi Ctrl+R è di «Ruota vista» e Ctrl+Maiusc++/- di «Ruota a
destra/sinistra» (pages); lo scambio va deciso insieme.
Le scorciatoie specifiche di un modulo vanno **limitate alla sua modalità**:
`ctx.action(..., shortcut="Ctrl+D", mode="pagine")` oppure `mode.scope_shortcuts(azione, ...)`.
Così due modalità possono usare lo stesso tasto senza conflitti. Per i tasti che servono solo
dentro un widget (es. Canc in un elenco) usa una scorciatoia del widget
(`Qt.ShortcutContext.WidgetWithChildrenShortcut`) o `keyPressEvent`, non un'azione globale.

### Inserire contenuti nella pagina: `placement`

Le funzioni di inserimento del motore PDF (`draw_*`, `insert_image`, `insert_htmlbox`,
`TextWriter`, `show_pdf_page`, `insert_link` e, con CropBox più grande del MediaBox, anche
`insert_text`/`insert_textbox`) spostano il contenuto sulle pagine ruotate o con
CropBox/MediaBox non allineati, e `page.get_links()` restituisce coordinate ruotate. Usare
sempre `foliant.api.placement`, che lavora nelle coordinate standard (quelle di `get_text`,
`get_drawings`, annotazioni) e compensa lo scarto in modo esatto:

```python
from foliant.api import placement as pl
pl.insert_text(page, (72, 100), "Pagina 1 di 3", fontsize=9, upright=True)
pl.insert_htmlbox(page, rect, "<b>Nota</b>", upright=True)
pl.insert_image(page, rect, filename="firma.png", upright=True)
pl.draw_rect(page, rect, fill=(1, 1, 1), color=None)
pl.get_links(page)                       # from/to in coordinate standard
pl.page_to_display_matrix(page)          # standard -> pagina visualizzata
```

`upright=True` raddrizza il contenuto nel verso in cui l'utente vede la pagina. Test di
riferimento: `tests/test_placement.py` (matrice completa con `FOLIANT_FULL_PLACEMENT=1`).

### API del contesto (`foliant/ui/context.py`)

| Metodo | Uso |
|---|---|
| `current_view()`, `current_document()`, `views()` | vista/documento attivi |
| `open_path(p)`, `open_document(doc)` | aprire file o mostrare un documento creato dal modulo |
| `activate_view(view)` → bool | porta in primo piano la scheda, anche se è visibile la Home o un widget centrale |
| `close_view(view, discard=False)` → bool | chiude la scheda come Ctrl+W (controlli `close_document` e domanda di salvataggio); `discard=True` chiude subito scartando le modifiche e senza controlli: da usare per le chiusure decise dal modulo (ricarica, file eliminato o spostato) |
| `refresh_state()` | ricalcola titolo, azioni (annulla/ripeti, Prima/Precedente/Successiva/Ultima) e barra di stato |
| `add_mode(id, titolo, icona, order)` → `Mode` | modalità con barra contestuale (`mode.add_action`, `add_widget`, `add_separator`, segnali `entered`/`left`) |
| `mode.set_default_tool(tool_o_factory)` | strumento attivato dal nucleo entrando nella modalità e sui documenti aperti mentre è attiva: un `Tool` o `factory(view) -> Tool \| None`; solo sulle viste con «Seleziona testo», prima di `entered`; vince l'ultima chiamata. Es. `ctx.mode("modifica").set_default_tool(lambda v: EditTextTool(c))` |
| `mode("leggi")` | aggiungere azioni a una modalità esistente |
| `action(testo, icona, slot, shortcut=, checkable=, requires_document=)` | crea un'azione (disattivata senza documento) |
| `add_menu_action(azione, sezione)` | voce nel menu principale ☰ |
| `add_home_action(titolo, icona, callback, descrizione, order)` | azione rapida nella Home |
| `add_sidebar_panel(id, titolo, icona, widget, order)` / `show_sidebar_panel(id)` / `toggle_sidebar_panel(id)` | pannello sinistro (`widget.set_view(view)` chiamato a ogni cambio documento); `toggle` lo nasconde se è già quello visibile |
| `add_properties_panel(id, widget)` / `show_properties(id)` / `hide_properties()` | pannello destro |
| `set_tool(tool)` | strumento attivo sulla vista (None = selezione testo) |
| `set_central_widget(w)` / `central_widget()` | mostra un widget del modulo al posto della vista documento (None = ripristina) |
| `add_link_handler(fn, priority=0)` | apertura dei collegamenti: `fn(view, link) -> bool` (True = gestito); priorità più alta = interrogato prima |
| `add_view_menu_provider(fn)` | voci nel menu del tasto destro sulle pagine: `fn(view, page_event, menu)`; la selezione di testo è `view.text_selections` (una per pagina: pagina, rettangoli, parole; `view.text_selection` = la prima) e `view.selected_text()` (il clic destro non la cancella) |
| `set_rulers_visible(bool)` / `rulers_visible()` | righelli (VIS-39) su tutte le viste, anche future, come la voce «Righelli» del menu ☰ › Vista; la scelta resta tra le sessioni |
| `set_ruler_unit(u)` / `ruler_unit()` | unità dei righelli per tutte le viste: `"mm"` (predefinita), `"pt"`, `"in"`; anche nelle Preferenze e nel menu del righello |
| `add_tab_menu_provider(fn)` | voci nel menu del tasto destro su una scheda: `fn(view, menu)`. Es. `ctx.add_tab_menu_provider(lambda v, m: m.addAction(_("Confronta con…"), lambda: confronta(v)))` |
| `show_banner(view, id, testo, actions=[(etichetta, fn)], level="info", priority=0, closable=True, on_close=None)` / `hide_banner(view, id)` / `banners(view)` | barra di avviso sopra le pagine della scheda (UX-14): `level` = `"info"`, `"warning"` o `"error"`; un avviso alla volta (priorità più alta, poi livello) con «N altri avvisi» per scorrerli; lo stesso `id` aggiorna l'avviso; chiuso con × non ricompare finché il documento è aperto (resta nell'icona della barra di stato) o finché il modulo non chiama `hide_banner`. Es. `ctx.show_banner(view, "ocr", _("Documento senza testo"), [(_("Esegui OCR"), avvia)], level="warning", priority=20)` |
| `notify(testo, actions=[(etichetta, fn)], duration_ms=None)` | avviso non bloccante in basso (6 s, 10 s con azioni, 0 = finché non si chiude) al posto delle finestre modali informative. Es. `ctx.notify(_("3 pagine eliminate"), [(_("Annulla"), doc.undo)])` |
| `add_guard(evento, fn)` / `remove_guard(evento, fn)` / `run_guards(evento, view, info=None)` → bool | controlli prima di un'operazione: `fn(view, info) -> bool` (False annulla; può chiedere conferma o modificare il documento). Eventi (`GUARD_EVENTS`): `"save"` (Salva), `"save_as"` (Salva con nome e Salva una copia, `info["copy"]`), `"print"` (il modulo che stampa chiama `run_guards("print", view)`), `"close_document"` (chiusura di una scheda da parte dell'utente), `"quit"` (una volta, `view=None`, `info["views"]`). Es. `ctx.add_guard("save", lambda v, info: conferma_redazioni(v))` |
| `run_task(titolo, fn, *args, modal=True, on_done=None, on_error=None)` | lavoro lungo con avanzamento. Predefinito: finestra modale, restituisce il risultato. `modal=False`: la finestra resta usabile (avanzamento, Annulla e altre attività nella barra di stato), restituisce subito un `BackgroundTask` (`cancel()`, `done`, `result`, `error`, `wait()`) e alla fine chiama `on_done(risultato)` o `on_error(eccezione)` nel thread dell'interfaccia (senza `on_error` l'errore diventa un avviso). Con `FOLIANT_SYNC_TASKS=1` tutto avviene subito. `background_tasks()` elenca quelle in corso |
| `set_selected_pages_provider(fn)` / `selected_pages(view=None)` → `list[int]` | pagine selezionate (miniature): il provider `fn(view)` restituisce gli indici; senza provider o selezione, `[pagina corrente]` |
| `add_preferences_page(titolo, factory, order=100)` / `show_preferences(pagina=None)` | pagina di un modulo nelle Preferenze: `factory(parent) -> QWidget`, creata a ogni apertura; `widget.apply()` (se c'è) chiamato con «Salva» |
| `author_name()` | nome dell'autore per commenti, note e firme (preferenza «Nome autore», altrimenti l'utente di sistema): da usare al posto di chiavi proprie |
| `recent_files()`, `replace_recent(vecchio, nuovo)`, `remove_recent(p)` | elenco dei recenti: file rinominato/spostato (stessa posizione) o da togliere |
| `add_change_guard(fn)` / `remove_change_guard(fn)` | guardia su tutti i documenti aperti e futuri: `fn(document, etichetta, kind) -> bool`, False annulla la modifica (vedi sotto) |
| `status`, `error`, `info`, `confirm`, `ask_open_paths`, `ask_save_path`, `ask_directory` | messaggi e dialoghi |
| segnali `currentViewChanged`, `documentOpened`, `documentClosed`, `documentChanged`, `documentAccessChanged`, `modeChanged`, `fullscreenChanged(bool)` | eventi; `documentAccessChanged(view)`: accesso o permessi cambiati (`unlock`) o documento bloccato/sbloccato (`set_locked`): ricalcolare i comandi che dipendono da `allows()`/`locked` |

Ogni scheda è un contenitore (`window.tabs.page(view)`: barre di avviso sopra la vista), ma
`window.tabs.widget(i)`, `currentWidget()`, `indexOf(view)` e `setCurrentWidget(view)` lavorano
sempre con la `DocumentView`. Il menu della scheda del nucleo compare solo se nessun modulo imposta
un proprio `customContextMenuRequested` sulla barra delle schede: le voci vanno aggiunte con
`add_tab_menu_provider`.

### API del documento (`foliant/core/document.py`)

| Membro | Uso |
|---|---|
| `change(etichetta, kind="content")` | blocco di modifica annullabile; `kind` ∈ `CHANGE_KINDS` (`"content"`, `"annotation"`, `"form"`, `"pages"`, `"metadata"`, `"redaction"`) classifica la modifica per le guardie; `"redaction"` (redazione, rimozione di dati nascosti) impone la riscrittura completa al salvataggio |
| `require_full_rewrite()` / `saves_incrementally` | dentro `change()`: il prossimo salvataggio riscrive tutto il file anche se firmato (annullando il passo la richiesta decade); `saves_incrementally` dice se `save()` aggiungerà un aggiornamento incrementale (firme valide) o riscriverà il file (firme non valide), per avvisare prima di salvare |
| `add_change_guard(fn)` / `remove_change_guard(fn)` | `fn(document, etichetta, kind)` chiamata prima di `change()` (fuori dal lock, solo per il blocco esterno): False → `OperationCancelled`, niente modificato; può anche sollevare `OperationCancelled("motivo")` |
| `set_locked(motivo)` / `locked_reason` / `locked` | sola lettura logica (SIG-17): `change()` solleva `OperationCancelled(motivo)`, annulla/ripeti disattivati; `set_locked(None)` sblocca; notifica `LOCK` |
| `access`, `permissions`, `allows(perm)` | `"none"` (non cifrato), `"user"`, `"owner"`; bit `/P` effettivi (con l'AND di `/Perms` per R6; -1 = tutto consentito); `allows(pymupdf.PDF_PERM_PRINT)` |
| `encryption_info` | `{}` o `method` («AES-256», «AES-128», «RC4-128», «RC4-40»), `engine`, `V`, `R`, `length`, `encrypt_metadata`, `P`, `cfm`, `description`, `perms_tampered` |
| `unlock(password) -> bool` | accesso da proprietario con la password dei permessi, anche dopo modifiche (annulla/ripeti e `save_options` aggiornati: protezione identica al salvataggio); notifica `ACCESS` |
| `repaired` | il motore ha ricostruito la xref all'apertura (CNV-32: barra «file riparato») |
| `original_bytes`, `has_signatures` | byte del file all'apertura o dopo l'ultimo salvataggio; campi `/FT /Sig` con `/V` |
| `save(path=None, incremental=None)`, `save_copy(path)`, `save_bytes(path=None)` | un documento firmato si salva **sempre** in modo incrementale (`original_bytes` + modifiche in coda), anche con «Salva con nome»; cambio di protezione, redazione (`kind="redaction"`), file riparato, `replace_with()` con un PDF che non prosegue il file (rinumerato; fino all'annullamento) o `incremental=False` = riscrittura completa; `incremental=True` lo forza (`IncrementalSaveError` se impossibile) |
| `atomic_write(percorso, dati)` | scrittura atomica (temporaneo + sostituzione); se non riesce solleva `DocumentWriteError` (sottoclasse di `DocumentError`, in `core/errors.py`) con `errno`, `filename` e un messaggio con causa e azione (disco pieno, cartella non scrivibile, file in uso): la finestra propone «Salva con nome…» |
| `save_strategy` | facoltativa: `fn(document, path)` che restituisce i byte da scrivere, oppure None per il comportamento del nucleo |
| `to_bytes(**opzioni)` / `uses_object_streams` | file completo compattato, senza flussi di oggetti (i byte restano cercabili); i file salvati li usano solo se il documento è già almeno PDF 1.5 o è nuovo/convertito, così un file 1.4 resta 1.4 (NFR-29) |
| notifiche `CONTENT`, `SAVED`, `CLOSED`, `ACCESS`, `LOCK` | `document.add_listener(fn(document, tipo))`; chi scrive il file da sé notifica `SAVED` (aggiorna anche il controllo «modificato da un altro programma») |

`save_options["encrypt_metadata"] = False` (SEC-06, «cifra tutto tranne i metadati») è accettata ma
**ignorata**: PyMuPDF/MuPDF non sanno scrivere `/EncryptMetadata false` e cifrano comunque il flusso
`/Metadata` (`ENCRYPT_METADATA_SUPPORTED = False`).

### API della vista (`foliant/ui/document_view.py`)

`DocumentView` offre: `current_page`, `go_to_page(i)`, `page_count`, `words(i)`,
`set_search_hits(dict, current)`, `overlays`, `set_tool()`, `render_page_image(i, zoom)`,
`ensure_visible(i, rect)`, `zoom`, `set_zoom()`, e inoltre:

| Membro | Uso |
|---|---|
| `text_selections` → `list[TextSelection]` / `set_text_selections(lista)` | selezione di testo, una per pagina in ordine di pagina: trascinando con «Seleziona testo» oltre la pagina la selezione prosegue sulle pagine successive o precedenti e la vista scorre da sola; segnale `selectionChanged` |
| `text_selection` / `set_text_selection(sel)` / `clear_text_selection()` | compatibilità: la selezione della **prima** pagina selezionata; assegnarla sostituisce tutta la selezione |
| `selected_text()` | testo di tutte le pagine selezionate, separate da `"\n\n"`; Ctrl+C lo copia se `document.can_copy`; Ctrl+A seleziona la pagina corrente |
| `set_annotation_filter(fn \| None)` / `annotation_filter` | annotazioni disegnate a schermo: `fn(pagina, xref) -> bool` (True = visibile) per ogni voce di `/Annots`, valida per pagine intere e tasselle; quelle escluse ricevono il flag Hidden solo durante la resa (sotto `ENGINE_LOCK`, ripristinato subito: il file non cambia). Va richiamata (anche con la stessa `fn`) quando cambia ciò che nasconde. `show_annotations = False` nasconde comunque tutto. Es. `view.set_annotation_filter(lambda p, x: x not in nascoste)` |
| `refresh_rendering()` | ridisegna le pagine senza modifiche al documento (la resa precedente resta visibile finché arriva la nuova) |
| `set_rulers_visible(bool)` / `rulers_visible`, `set_ruler_unit(u)` / `ruler_unit` (+ `rulerUnitChanged`) | righelli di questa vista (di solito si usa `ctx.set_rulers_visible`); zero nell'angolo superiore sinistro della pagina come si vede (CropBox, /Rotate e rotazione della vista) sotto il puntatore o corrente: `ruler_page()`, `ruler_position(pos_viewport)` → (x, y) nell'unità |
| `guides()` → `list[(orientamento, posizione_pt, pagina \| None)]` | guide (VIS-39, OBJ-23): `GUIDE_HORIZONTAL` (y costante) o `GUIDE_VERTICAL` (x costante) nelle coordinate della **pagina visualizzata** (punti dall'angolo superiore sinistro della CropBox con /Rotate applicato, senza la rotazione della vista: restano attaccate al contenuto); pagina `None` = tutte le pagine |
| `add_guide(orientamento, posizione_pt, pagina=None)` / `remove_guide(orientamento, posizione_pt, pagina=None)` → bool / `set_guides(lista)` / `clear_guides()` | modifica delle guide (orientamento anche `"h"`/`"v"` o, come nel modulo oggetti, `"y"`/`"x"`); segnale `guidesChanged`. La finestra le salva per documento nelle impostazioni (non nel file) e le ripristina alla riapertura |
| `guides_on_page(i)` → `[(orientamento, posizione_pt)]` / `page_guide_lines(i)` → `{"x": [...], "y": [...]}` | guide valide per la pagina; `page_guide_lines` le dà in coordinate pagina (non ruotata, come `get_text`/`get_drawings`) per l'aggancio: su una pagina con /Rotate 90 una guida verticale diventa una `y` |
| `display_transform(i)`, `display_size(i)` | coordinate della pagina visualizzata → viewport (`page_transform(i)` = `placement.page_to_display_matrix` seguita da questa) |
| `set_guides_visible(bool)` / `guides_visible`, `set_guides_locked(bool)` / `guides_locked` | guide disegnate o no; bloccate. Con i righelli visibili si trascinano dal righello (Ctrl al rilascio = su tutte le pagine), si spostano prendendole sulla pagina e si eliminano trascinandole fuori dalla pagina; il menu del righello cambia unità, blocca o rimuove tutte le guide |

Il modulo oggetti può sostituire le sue guide «da menu» con queste: `view.add_guide("x", valore)`
al posto di `add_guide(view, "x", valore)` (stesso valore sulle pagine senza /Rotate) e
`view.page_guide_lines(pagina)` al posto di `guides(view)` per l'aggancio e il disegno.

Strumenti: `Tool.key()` riceve anche Tab e Maiusc+Tab (`Key_Backtab`) prima del cambio di fuoco;
restituendo True lo strumento li tiene per sé. Con `autoscroll = True` (attributo o proprietà dello
strumento) durante un trascinamento col tasto sinistro la vista scorre quando il puntatore ne
raggiunge il bordo e ripete `move()` con `ev.event = None`.

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
