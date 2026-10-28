# Richieste al nucleo — modulo `ai`

1. **Conferma alla chiusura con operazioni in corso (AI-17).** Serve un aggancio
   prima della chiusura di una scheda o della finestra, ad esempio un segnale
   `ctx.aboutToClose(view) -> bool` o un elenco di «guardiani» interrogati da
   `MainWindow.close_view()` / `closeEvent()`, per chiedere conferma quando ci sono
   risposte IA o traduzioni in corso.
   *Soluzione locale:* su `documentClosed` le operazioni del documento vengono
   annullate (nessun file parziale, nessuna modifica); l'elenco «Operazioni in
   corso» permette di annullarle prima.

2. **Preferenza «nome autore» (UX, Preferenze › Documenti).** Le note inserite
   dall'assistente usano come autore il nome configurato. Il modulo legge
   `settings.value("author")` o `"preferences/author"`, con il nome dell'account di
   sistema come ripiego: sarebbe utile una chiave condivisa documentata nel README.

3. **Pagina Preferenze › Assistente IA.** La specifica (0.x, Preferenze) colloca le
   impostazioni dei fornitori nella finestra Preferenze. *Soluzione locale:* finestra
   «Impostazioni dell'Assistente IA» aperta dal pannello, dalla barra della modalità e
   dal menu ☰ (sezione «Assistente IA»). Se il nucleo offrirà un registro di pagine
   delle preferenze, `dialogs.SettingsDialog` si può incorporare così com'è.

4. **Voce «Chiedi all'assistente» nel menu contestuale della selezione.** Il modulo
   la aggiunge con `add_view_menu_provider` (sottomenu «Assistente IA», solo con
   testo selezionato o su un'immagine). Nessuna modifica necessaria, segnalato per
   coerenza con le altre aree.

5. **Test del nucleo e moduli che chiudono la finestra.** pytest-qt chiude la
   finestra prima dei finalizzatori della fixture `window`: un test di modulo che
   lascia un documento modificato blocca la suite sulla richiesta di salvataggio.
   *Soluzione locale:* hook `pytest_runtest_teardown` (wrapper, tryfirst) in
   `tests/modules/ai/conftest.py` che segna i documenti come salvati. Proposta: spostare
   questa logica in `tests/conftest.py`.
