# Richieste al nucleo — modulo viewer

Ogni richiesta ha già una soluzione locale nel modulo; quando il nucleo la offrirà
la soluzione locale si può togliere.

**Aggiornamento (base 3d69387): soddisfatte e adottate**, soluzioni locali tolte —
2 (`view.refresh_rendering`), 3 (`document.allows/permissions`), 4 (stato dei comandi di
pagina e `ctx.refresh_state`), 5 (`ctx.toggle_sidebar_panel`), 6 (`ctx.activate_view`),
7 (`ctx.notify`, `ctx.show_banner`), 9 (`ctx.fullscreenChanged`), 17 in parte
(`ctx.replace_recent`/`remove_recent`), 18 (`ctx.close_view(view, discard=True)`), oltre a
`ctx.set_selected_pages_provider` e `ctx.add_tab_menu_provider`.
**Ancora aperte:** 1 (segnale di salto), 8 (area per barre di modulo: la ricerca usa ancora
una barra nell'area inferiore), 14 (registrazione del generatore di
miniature), 15 (id stabili dei comandi), 16 (titolo della scheda dopo una rinomina:
`window._update_tab_title`), 19 (testi di VIS-06), 20 (selezione e copia, VIS-28),
21 (schede e finestre, VIS-30). Soddisfatte dopo: 13 (zoom), 17 (recenti).

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

13. **Zoom** — *soddisfatta* (scorciatoie Ctrl+0/1/2 e limiti 1%–6400%, verificati da
    `tests/modules/viewer/test_zoom.py`). Restano piccole differenze dalla specifica: Ctrl+rotella
    è continuo (non «un passo»), al limite il comando non lo segnala, nessun gesto di pizzico né
    calibrazione dei dpi. «Adatta all'altezza» e «Adatta al contenuto» li aggiunge il viewer
    (`zoomfit.py`); se il nucleo li offrirà come modalità di adattamento, `zoomfit` si toglie.

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

17. **Recenti** (VIS-02/03) — *soddisfatta e adottata*: `ctx.replace_recent`/`remove_recent`,
    voci «non disponibile» con Individua/Rimuovi, `ctx.pin_recent`/`is_pinned` e
    `ctx.add_recent_menu_provider`; `files.py` non imposta più un menu proprio sulla Home.

18. **`close_view(view, discard=True)`** per chiudere senza la richiesta di salvataggio
    (dopo lo spostamento nel cestino o il ripristino): oggi si imposta `doc._saved_state`.

19. **Testi di VIS-06** (password e copia vietata): la specifica chiede «Password non corretta»
    e, per Ctrl+C con copia vietata, l'avviso «Copia non consentita dal documento»; il nucleo
    scrive «La password non è corretta. Riprova.» e «Il documento non consente la copia del
    contenuto.» nella barra di stato. Il comportamento è corretto (verificato da
    `tests/modules/viewer/test_protection.py`): resta da allineare il testo, e per la copia
    usare `ctx.notify` invece della barra di stato.

20. **Selezione e copia del testo** (VIS-28, P0) — lo strumento Seleziona è del nucleo
    (`foliant/ui/tools.py`, `DocumentView.words`). Criteri verificati in
    `tests/modules/viewer/test_selection.py`; quelli ancora mancanti sono `xfail(strict=True)`
    e diventeranno errori quando il nucleo li soddisferà (allora basta togliere il segno):
    - **ordine di lettura**: `DocumentView.words()` usa `get_text("words")` nell'ordine del
      flusso; con la colonna destra scritta per prima, la copia mette la destra prima della
      sinistra. Proposta: ordinare i blocchi come `foliant.modules.viewer.engine.reading_order`
      (colonne per sovrapposizione orizzontale da sinistra a destra, blocchi per y0, blocchi a
      tutta larghezza come separatori) e numerare le parole in quell'ordine;
    - **trattino di sillabazione**: `words_to_text` lascia «infor-» + «mazione»; proposta: se
      una riga finisce con lettera + «-» e la successiva inizia con una lettera, unire senza
      trattino né a capo (come `engine.extract_page_text`);
    - **triplo clic** (riga/paragrafo), **Maiusc+clic** e **Maiusc+frecce** per estendere,
      **F7** cursore di testo, **Ctrl+A** su tutto il documento (oggi solo la pagina corrente)
      con copia in background oltre un limite;
    - **`/ActualText`**, **artefatti** esclusi (opzione) e **testo bidirezionale**: dipendono
      dall'estrazione; con PyMuPDF `get_text(..., flags=TEXT_ACCURATE_BBOXES)` e
      `page.get_texttrace()` si possono riconoscere i contenuti marcati.

21. **Schede e finestre** (VIS-30, P0): mancano nel nucleo il trascinamento di una scheda fuori
    dalla barra (nuova finestra con il documento nella stessa posizione) o su un'altra finestra,
    i gruppi di schede (nome, colore, comprimibili, salvati nella sessione), la preferenza
    «apri in una nuova finestra» e la «Nuova vista del documento» (seconda scheda sullo stesso
    `Document` in memoria: oggi `_close_view_now` chiude il documento con la scheda, quindi due
    viste dovrebbero contare i riferimenti prima di `doc.close()`). Il viewer è pronto ad
    aggiungere le voci al menu della scheda (`ctx.add_tab_menu_provider`) quando il nucleo
    offrirà, per esempio, `ctx.move_view_to_new_window(view)` e `ctx.open_second_view(view)`.
    Gli altri criteri (sessione con 3 schede, Ctrl+Maiusc+T con pagina e zoom, un solo dialogo
    per più documenti modificati, 50 schede con cambio entro 200 ms) sono verificati in
    `tests/modules/viewer/test_session.py`.

22. **Aggancio a guide e griglia** (VIS-39, criterio «guida a 50 mm con aggancio attivo: il
    bordo dell'annotazione spostata entro 5 px si allinea a 50 mm»): le guide sono del nucleo
    (`DocumentView`, righelli) e la griglia del viewer (`measure.py`), ma manca una funzione
    comune che le aree Annotazioni e Oggetti possano chiamare mentre l'utente trascina. Proposta:
    `view.snap(page, point_or_rect, tolerance_px=5) -> point_or_rect` che consideri le guide e
    i fornitori registrati con `ctx.add_snap_provider(fn(view, page) -> (xs, ys))`; il viewer
    registrerebbe le linee della griglia quando «Aggancia alla griglia» è attivo, e un
    interruttore «Aggancio» nel menu Vista.

23. **Barre e pannelli visibili** (VIS-51, P1): mostrare/nascondere barra di stato, barra
    contestuale, pannello laterale e proprietà, «Mostra etichette» della barra delle modalità e
    «Dimensione icone», ricordati al riavvio, con una sezione Preferenze › Aspetto e il suo
    «Ripristina predefiniti» che non tocca le altre sezioni. Sono parti della finestra del
    nucleo: oggi `_mode_button_style()` decide le etichette dalla larghezza a ogni
    ridimensionamento, quindi un modulo non può imporle. Le preferenze di visualizzazione del
    viewer (vista, zoom, pannello iniziale, ultima posizione, impostazioni del documento,
    esporta/importa) sono già nel file delle impostazioni e si esportano con il resto.
