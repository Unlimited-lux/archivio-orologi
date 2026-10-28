# Richieste al nucleo — modulo `ai`

1. **Conferma alla chiusura con operazioni in corso (AI-17).** *Risolta* (base 3d69387):
   guardiani `close_document` e `quit` registrati con `ctx.add_guard`.

2. **Preferenza «nome autore».** *Risolta:* note, caselle di testo, sostituzioni e commenti
   della revisione usano `ctx.author_name()`.

3. **Pagina Preferenze › Assistente IA.** *Risolta:* `ctx.add_preferences_page` con
   `dialogs.SettingsPage`; il comando «Impostazioni dell'Assistente IA» apre le Preferenze su
   questa pagina.

4. **Voce «Chiedi all'assistente» nel menu contestuale della selezione.** *Risolta:* voce
   principale + sottomenu «Assistente IA»; la selezione su più pagine (`view.text_selections`)
   è inviata pagina per pagina con i riferimenti.

5. **Test del nucleo e moduli che chiudono la finestra.** pytest-qt chiude la
   finestra prima dei finalizzatori della fixture `window`: un test di modulo che
   lascia un documento modificato blocca la suite sulla richiesta di salvataggio.
   *Soluzione locale:* hook `pytest_runtest_teardown` (wrapper, tryfirst) in
   `tests/modules/ai/conftest.py` che segna i documenti come salvati. Proposta: spostare
   questa logica in `tests/conftest.py`.
