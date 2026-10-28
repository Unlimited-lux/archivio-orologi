# Richieste al nucleo — modulo Strumenti

1. **[RISOLTO: `ctx.add_tab_menu_provider`, voce «Confronta con…»] Menu contestuale della scheda (CMP-01, flusso 1).** La specifica chiede di poter avviare
   "Confronta documenti" dal menu contestuale di una scheda. Il nucleo non offre un punto
   di estensione per il menu delle schede. Inoltre `tests/test_window.py::test_view_menu_provider`
   presuppone che nessun modulo aggiunga voci al menu delle pagine, quindi la voce
   "Confronta con…" non è registrata con `add_view_menu_provider`.
   Proposta: `ctx.add_tab_menu_provider(fn(view, menu))` e un test del nucleo che verifichi
   solo la presenza della propria voce. Soluzione attuale: azione nella modalità Strumenti,
   nel menu ☰ e nella Home; il documento attivo è proposto come A.
2. **Sessione di confronto come scheda.** La specifica parla di una scheda "A ↔ B"; il
   modulo usa `ctx.set_central_widget()`. Un'API per schede non documentali permetterebbe
   più sessioni aperte insieme.
3. **Sottocomando da riga di comando (BAT-22).** Il modulo offre `python -m foliant.modules.tools.cli`;
   per avere `foliant batch …` servirebbe che `foliant/__main__.py` inoltri gli argomenti che
   iniziano con `batch` a `foliant.modules.tools.cli.main(argv[1:])` prima di creare la QApplication.
4. **Attività non modali (CMP-01 flusso 5, BAT-04).** `ctx.run_task` mostra una finestra modale:
   la specifica chiede che l'applicazione resti utilizzabile durante il confronto e i lavori in
   blocco. Proposta: `ctx.run_task(..., modal=False, on_done=callback)` con un indicatore nella
   barra di stato. Soluzione attuale: finestra modale con Annulla; per i lavori in blocco una
   piccola finestra separata con Pausa/Riprendi e «Annulla file».
