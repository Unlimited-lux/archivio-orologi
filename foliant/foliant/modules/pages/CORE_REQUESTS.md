# Richieste al nucleo — modulo Pagine

1. *(Risolta nel nucleo, commit 8a0d3dd: il provider è ora registrato.)* **Menu contestuale della vista e test del nucleo.** `tests/test_window.py::test_view_menu_provider`
   verifica che il menu contenga *solo* la voce del provider di prova: qualunque modulo che
   registri un provider lo fa fallire. Il modulo Pagine ha un provider pronto
   (`PagesController.view_menu`: ruota, duplica, elimina, «Organizza pagine») ma **non lo registra**.
   Proposta: nel test controllare che la voce di prova sia *presente* (`in`), poi registrare il
   provider in `module.register()`.
2. *(Risolta: il modulo usa `ctx.add_guard("save"/"save_as")`.)* **Avviso al salvataggio per contenuto nascosto dal ritaglio (PAG-25) e salvataggio incrementale
   dopo eliminazioni (PAG-03/11).** Serve un aggancio prima del salvataggio (es. segnale
   `Context.aboutToSave(view)` con possibilità di annullare). Nel frattempo l'avviso compare
   quando si applica il ritaglio (`layout.hidden_content_outside_crop`). Il nucleo salva già
   sempre in modo completo (`garbage=3`), quindi le revisioni precedenti non restano nel file.
3. **Appunti Qt offscreen.** Dati lasciati negli appunti fanno cadere Qt offscreen all'uscita
   dell'interprete; i test del modulo li svuotano (`tests/modules/pages/conftest.py`). Si potrebbe
   svuotarli in `tests/conftest.py` per tutti i moduli.
4. **Chiusura della finestra di test con documenti modificati.** pytest-qt chiude i widget prima
   dei finalizzatori della fixture `window`, quindi la domanda «Modifiche non salvate» blocca i test
   che modificano un documento. Il modulo la gestisce con una fixture autouse; proposta: segnare i
   documenti come salvati in `tests/conftest.py` prima di `qtbot.addWidget`, o usare un
   `request.addfinalizer` registrato prima di qtbot.
5. *(Risolta: il modulo usa `document.allows()` e `document.unlock()`.)* **Permesso di assemblaggio e password del proprietario.** Dopo «Sblocca» il modulo salva la
   password del proprietario in `Document.password`, così annulla/ripeti la riapplicano; sarebbe
   più pulito un metodo `Document.authenticate_owner(pw)` che aggiorni anche `save_options`.

6. **Pagine selezionate (`set_selected_pages_provider`).** Il nucleo tiene un solo provider. Soluzione
   adottata: entrando nella modalità Pagine il modulo registra un provider che restituisce la
   selezione dell'organizzatore e, se vuota, delega al provider precedente (miniature del viewer);
   uscendo ripristina il precedente. Il viewer non deve fare nulla; se in futuro più moduli
   registrano provider temporanei, conviene una pila (`push_selected_pages_provider`/`pop…`).
