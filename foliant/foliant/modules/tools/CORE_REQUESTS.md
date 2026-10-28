# Richieste al nucleo — modulo Strumenti

1. **Menu contestuale della scheda (CMP-01, flusso 1).** La specifica chiede di poter avviare
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
