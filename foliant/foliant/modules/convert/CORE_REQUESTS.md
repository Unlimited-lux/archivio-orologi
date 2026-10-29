# Richieste al nucleo (modulo Converti)

1. **Home: pulsanti fantasma durante la ricostruzione della griglia.** `HomeScreen.add_action` rimuove i
   pulsanti con `deleteLater()` senza nasconderli: finché il ciclo d'eventi non li distrugge restano
   visibili in alto a sinistra (si vede nelle catture offscreen subito dopo l'avvio). Proposta:
   `item.widget().hide()` (o `setParent(None)`) prima di `deleteLater()`.
2. **«Nuovo PDF vuoto» del nucleo** crea un A4 senza opzioni. Il modulo offre «PDF vuoto…» (formato,
   orientamento, pagine, stile carta: CNV-23). Proposta: il nucleo potrebbe esporre un gancio per
   sostituire l'azione `act_new` con quella del modulo. Nel frattempo la voce è in File › Nuovo (gruppo
   «Crea», dopo «Nuovo documento») e nel menu «Crea PDF» della modalità Converti.
3. ~~Selezione delle miniature~~: adottato `ctx.selected_pages(view)` del nucleo. Nessuna richiesta aperta.
4. ~~Barra «File danneggiato, riparato in memoria»~~ (CNV-32): adottati `document.repaired` e
   `ctx.show_banner(view, "riparato", …)` con «Salva copia riparata». Nessuna richiesta aperta.
5. ~~Esclusione di `pymupdf.layout` e `find_tables()`~~: corretto nel nucleo (ab09e8d); l'aggiramento locale resta innocuo. `_NoLayoutAnalysis.find_spec` solleva
   `ModuleNotFoundError`; PyMuPDF in `find_tables()` chiama `importlib.util.find_spec("pymupdf.layout")`
   per suggerire il pacchetto, e l'eccezione fa fallire ogni ricerca di tabelle. Proposta: restituire
   `None` per i nomi esclusi (l'import fallisce comunque) e impostare
   `PYMUPDF_SUGGEST_LAYOUT_ANALYZER=0`. Nel modulo: `pymupdf._recommend_layout = False` prima di
   `find_tables()` (`extract.find_tables`).
