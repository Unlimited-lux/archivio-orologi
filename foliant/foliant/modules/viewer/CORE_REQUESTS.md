# Richieste al nucleo — modulo viewer

Ogni richiesta ha già una soluzione locale nel modulo; quando il nucleo la offrirà
la soluzione locale si può togliere.

1. **Segnale di salto nella vista** (`DocumentView.aboutToJump(page)` o simile).
   Oggi `navigation.py` avvolge `go_to_page`, `next_page` e `previous_page` di ogni vista
   per registrare la cronologia Indietro/Avanti (VIS-13).

2. **Ridisegno pubblico** `DocumentView.refresh()` che scarti immagini e parole in cache
   senza un cambio di contenuto (livelli, VIS-20). Oggi `layers.py` chiama
   `view._invalidate_render()` e svuota `view._words`; le miniature hanno `panel.refresh()`.

3. **Permessi effettivi del documento** (`Document.permissions`, e se «Sblocca restrizioni»
   ha concesso l'accesso proprietario). Dopo la prima modifica il documento interno è una
   copia non cifrata e `pdf.permissions` restituisce tutto consentito. Oggi
   `properties_engine.document_security()` ricostruisce i permessi da
   `original_protection`/`save_options`; lo usano stampa, segnalibri e istantanea.

4. **Stato dei comandi di pagina**: `MainWindow._update_state()` riabilita sempre
   Prima/Precedente/Successiva/Ultima. VIS-12 chiede «Pagina successiva» disabilitata
   sull'ultima pagina: `navigation.py` lo riapplica dopo ogni aggiornamento.
   Servirebbe che `_update_state` lo calcolasse, e un `window.refresh_state()` pubblico.

5. **Mostrare/nascondere un pannello laterale** (`ctx.toggle_sidebar_panel(id)`): F4 per le
   miniature usa oggi `window._toggle_sidebar_panel`.

6. **`ctx.activate_view(view)`** per portare in primo piano una scheda anche quando è
   visibile la Home (oggi `session.py` imita `open_path`).

7. **Barra informativa non bloccante** nel nucleo (`ctx.notify(testo, azioni)`): `ctx.error`
   e `ctx.confirm` sono modali. Recupero dopo crash e proposta di schermo intero usano
   dialoghi propri non bloccanti.

8. **Area per barre di modulo** sopra o sotto la vista (barra di ricerca). Oggi la barra di
   ricerca è un `QToolBar` nell'area inferiore della finestra.

9. **Segnale di schermo intero** (`fullscreenChanged`) al posto dell'event filter su
   `WindowStateChange` usato da `reading.py`.

10. **Tema scuro**: caselle di spunta, pulsanti radio e frecce degli spin box poco visibili
    (mancano `QCheckBox::indicator`, `QRadioButton::indicator` nel foglio di stile).

11. **Test**: con `pytest-qt` la finestra viene chiusa prima del finalizzatore della fixture
    `window`; se un documento è modificato `close_view` apre un `QMessageBox.exec()` modale e
    il test si blocca. Il modulo neutralizza `QMessageBox.exec` nella propria conftest;
    conviene farlo nella conftest comune (insieme a `FOLIANT_RECOVERY_DIR` in una cartella
    temporanea, così il controllo di recupero all'avvio non legge la cartella reale).

12. **Errore noto del motore** (PyMuPDF 2.0): `Document.embfile_del(nome)` elimina la coppia
    sbagliata quando l'allegato non è il primo. `attachments_engine._delete_embedded` lo
    aggira; anche altri moduli che eliminano allegati (protezione, sanificazione) devono
    tenerne conto.

13. **Zoom**: la specifica chiede Ctrl+0 = adatta pagina, Ctrl+1 = dimensione reale; il nucleo
    usa scorciatoie diverse. (I limiti 1%–6400% ci sono: oltre ~16 megapixel la vista disegna
    la pagina a tasselle.)

14. **Generatore di miniature per il file manager** (VIS-53): `packaging/install.sh` dovrebbe
    installare un eseguibile `foliant-thumbnailer` che lancia
    `python -m foliant.modules.viewer.thumbnailer "$@"` e il file
    `~/.local/share/thumbnailers/foliant.thumbnailer` con il contenuto di
    `thumbnailer.THUMBNAILER_ENTRY`.

15. **Scorciatoie personalizzate** (VIS-52): l'identificativo di un comando è il suo testo;
    servirebbe un id stabile per ogni `QAction` (es. `ctx.action(..., id="view.rotate")`).

16. **File rinominati o spostati** (VIS-03): un modo pubblico per far seguire a una scheda il
    nuovo percorso (`ctx.document_moved(vecchio, nuovo)`, con un segnale per chi indicizza per
    percorso, es. le posizioni memorizzate). Oggi `files.py` imposta `doc.path` e chiama
    `window._update_tab_title(view)`.

17. **Recenti** (VIS-02/03): API pubblica per sostituire/rimuovere una voce; la Home dovrebbe
    ricevere anche i file non disponibili con un indicatore (oggi `_refresh_recent` li scarta,
    quindi non si possono marcare «non disponibile» con Rimuovi/Individua) e offrire un menu
    contestuale estendibile sull'elenco dei recenti (oggi `files.py` lo imposta su
    `window.home.recent`).

18. **`close_view(view, discard=True)`** per chiudere senza la richiesta di salvataggio
    (dopo lo spostamento nel cestino o il ripristino): oggi si imposta `doc._saved_state`.
