# Richieste al nucleo (modulo Converti)

1. **Home: pulsanti fantasma durante la ricostruzione della griglia.** `HomeScreen.add_action` rimuove i
   pulsanti con `deleteLater()` senza nasconderli: finché il ciclo d'eventi non li distrugge restano
   visibili in alto a sinistra (si vede nelle catture offscreen subito dopo l'avvio). Proposta:
   `item.widget().hide()` (o `setParent(None)`) prima di `deleteLater()`.
2. **«Nuovo PDF vuoto» del nucleo** crea un A4 senza opzioni. Il modulo offre «PDF vuoto…» (formato,
   orientamento, pagine, stile carta: CNV-23). Proposta: il nucleo potrebbe esporre un gancio per
   sostituire l'azione `act_new` con quella del modulo. Nel frattempo la voce è nel menu ☰ › Converti
   e nel menu «Crea PDF» della modalità Converti.
3. **Selezione delle miniature** (CNV-01, intervallo «pagine selezionate»): serve un'API pubblica, ad es.
   `ctx.selected_pages()` o `view.selected_pages()`. Soluzione locale: il modulo legge il pannello
   registrato con id «thumbnails» da `window._sidebar` e ne usa `selected_pages()` (attributo privato).
4. ~~Barra «File danneggiato, riparato in memoria»~~ (CNV-32, punto 1): ora la mostra il modulo viewer
   (`integrity.py`, VIS-07) con «Ripara e salva con nome…». Nessuna richiesta aperta.
