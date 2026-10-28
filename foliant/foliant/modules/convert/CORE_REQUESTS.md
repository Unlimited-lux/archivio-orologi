# Richieste al nucleo (modulo Converti)

1. **Home: pulsanti fantasma durante la ricostruzione della griglia.** `HomeScreen.add_action` rimuove i
   pulsanti con `deleteLater()` senza nasconderli: finché il ciclo d'eventi non li distrugge restano
   visibili in alto a sinistra (si vede nelle catture offscreen subito dopo l'avvio). Proposta:
   `item.widget().hide()` (o `setParent(None)`) prima di `deleteLater()`.
2. **«Nuovo PDF vuoto» del nucleo** crea un A4 senza opzioni. Il modulo offre «PDF vuoto…» (formato,
   orientamento, pagine, stile carta: CNV-23). Proposta: il nucleo potrebbe esporre un gancio per
   sostituire l'azione `act_new` con quella del modulo. Nel frattempo la voce è nel menu ☰ › Converti
   e nel menu «Crea PDF» della modalità Converti.
3. ~~Selezione delle miniature~~: adottato `ctx.selected_pages(view)` del nucleo. Nessuna richiesta aperta.
4. ~~Barra «File danneggiato, riparato in memoria»~~ (CNV-32, punto 1): ora la mostra il modulo viewer
   (`integrity.py`, VIS-07) con «Ripara e salva con nome…». Nessuna richiesta aperta.
