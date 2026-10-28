# Richieste al nucleo (modulo Converti)

1. **Home: pulsanti fantasma durante la ricostruzione della griglia.** `HomeScreen.add_action` rimuove i
   pulsanti con `deleteLater()` senza nasconderli: finché il ciclo d'eventi non li distrugge restano
   visibili in alto a sinistra (si vede nelle catture offscreen subito dopo l'avvio). Proposta:
   `item.widget().hide()` (o `setParent(None)`) prima di `deleteLater()`.
2. **«Nuovo PDF vuoto» del nucleo** crea un A4 senza opzioni. Il modulo offre «PDF vuoto…» (formato,
   orientamento, pagine, stile carta: CNV-23). Proposta: il nucleo potrebbe esporre un gancio per
   sostituire l'azione `act_new` con quella del modulo. Nel frattempo la voce è nel menu ☰ › Converti
   e nel menu «Crea PDF» della modalità Converti.
3. **Selezione delle miniature** (CNV-01, intervallo «pagine selezionate»): serve un'API comune, ad es.
   `view.selected_pages()`, popolata dal pannello miniature. La finestra «Esporta come» la usa già
   se viene passata (`selected=`); oggi il modulo non può leggerla.
4. **Barra «File danneggiato, riparato in memoria»** (CNV-32, punto 1): l'apertura dei file
   danneggiati è nel nucleo (`Document.open`). Proposta: un segnale/flag `document.repaired` così che il
   modulo mostri la barra con «Salva copia riparata». La riparazione esplicita è in «Altri strumenti ›
   Ripara PDF…».
