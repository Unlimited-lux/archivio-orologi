# Stato del modulo viewer — sezione 1 «Visualizzazione e navigazione»

Legenda: **fatto** = criteri di accettazione verificati da test automatici; **parziale** = parte
dei criteri o dei flussi; **nucleo** = già coperto dal nucleo (verificato dai test del nucleo
o del modulo); **non fatto**.
I test sono in `tests/modules/viewer/` (file indicato tra parentesi). Le richieste al nucleo
sono in `CORE_REQUESTS.md`; nessuna nuova dipendenza pip (`DEPS.txt`).

| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| VIS-01 Home | P0 | nucleo | `tests/test_window.py::test_starts_on_home` | Home, azioni, trascinamento nel nucleo; menu contestuale dei recenti da `files`. Configurazione corrotta con backup: non fatto. |
| VIS-02 Recenti e preferiti | P0 | parziale | `test_session.py::test_reopen_restores_page_37_and_zoom_150`, `test_fit_mode_and_layout_are_remembered`, `test_no_history_preference_records_nothing`, `test_files.py::test_menu_section_and_home_context_menu` | Pagina/zoom/layout per file, preferenza «non registrare la cronologia», Rimuovi dall'elenco. Preferiti e voci «non disponibile» richiedono il nucleo (CORE_REQUESTS 17). |
| VIS-03 Gestione file locali | P1 | fatto | `test_files.py` (26 test) | Rinomina/duplica/sposta/cestino/mostra/copia percorso, conflitti, permessi, schede che seguono il file. Navigatore di cartelle integrato: non fatto. |
| VIS-04 Archivi compressi | P1 | fatto | `test_archives.py` | Comprimi, apri un PDF contenuto (in memoria), estrai con nomi normalizzati, soglia e rapporto anomalo, nessun residuo. ZIP cifrati: lettura con password (creazione non testabile con la libreria standard). |
| VIS-05 Apertura | P0 | parziale | `test_session.py::test_open_action_page5_fith`, `test_page_layout_*`, `test_page_mode_*`, `test_ignore_document_view_preference` | Apertura, errori e password nel nucleo; vista iniziale da `/OpenAction`, `/PageLayout`, `/PageMode` qui. `/ViewerPreferences`, frammenti `#page=` e istanza singola: nucleo/non fatto. |
| VIS-06 Password e permessi | P0 | parziale | `test_printing.py::test_permission_denied_disables_action`, `test_low_quality_print_max_150_dpi`, `test_tools.py::test_snapshot_disabled_without_copy_permission`, `test_copy_permission_survives_edit`, `test_bookmarks.py::test_read_only_*` | Password nel nucleo. Permessi effettivi anche dopo le modifiche (CORE_REQUESTS 3) per stampa, istantanea, segnalibri, lettura ad alta voce. Ctrl+C con copia vietata e «Sblocca restrizioni»: nucleo/area Protezione. |
| VIS-07 File danneggiati | P1 | parziale | `test_integrity.py` | Avviso del file riparato lasciato al modulo di conversione (chiave «riparato», un solo avviso); funzioni non supportate (XFA, 3D, multimedia) segnalate. Segnaposto per pagine illeggibili nelle miniature (`test_thumbnails.py::test_unreadable_page_placeholder`). |
| VIS-08 Resa e grandi documenti | P0 | nucleo | `tests/test_view.py` | Resa progressiva della vista nel nucleo. |
| VIS-09 Modalità di visualizzazione | P0 | nucleo | `tests/test_view.py::test_view_modes` | `/PageLayout` all'apertura da `session`; R2L: nucleo. |
| VIS-10 Zoom | P0 | fatto | `test_zoom.py` (criteri: adatta larghezza al ridimensionamento, dimensione reale A4 = 794 × 1123 px, Ctrl+rotella con il punto sotto il puntatore, 333% digitato, limiti 1%–6400%, livelli dell'elenco, zoom per documento; adatta all'altezza e al contenuto), `test_tools.py::test_zoom_area_fills_view` | Zoom, adattamenti e campo nel nucleo; qui Zoom area e «Adatta all'altezza»/«Adatta al contenuto» (`zoomfit.py`), che restano attivi al ridimensionamento. Differenze minori del nucleo in CORE_REQUESTS 13. |
| VIS-11 Rotazione della vista | P1 | nucleo | `tests/test_view.py::test_rotate_view_keeps_document`, `test_printing.py::test_print_uses_document_orientation_not_view_rotation` | |
| VIS-12 Navigazione | P0 | fatto | `test_navigation.py::test_go_to_labels`, `test_next_disabled_on_last_page`, `test_engine.py::test_page_labels_*` | «Vai a pagina…» con etichette e `#N` (Ctrl+L sul campo pagina del nucleo; Ctrl+G / Ctrl+Maiusc+G = occorrenza successiva/precedente come F3 / Maiusc+F3), «iv (4 di 110)» in barra di stato, comandi disabilitati agli estremi (CORE_REQUESTS 4). |
| VIS-13 Cronologia Indietro/Avanti | P1 | fatto | `test_navigation.py::test_back_forward_after_link`, `test_scrolling_does_not_create_history`, `test_new_jump_clears_forward`, `test_shortcut_alt_left`, `test_engine.py::test_history` | Alt+←/→, tasti Indietro/Avanti del mouse, limite 100, pagine eliminate. Indietro dopo un collegamento verso un altro documento riporta alla scheda d'origine (`test_back_returns_to_origin_document_tab`). |
| VIS-14 Miniature | P0 | fatto | `test_thumbnails.py` (9 test) | Generazione solo delle visibili, clic + cronologia, evidenziazione, area visibile trascinabile, dimensione, aggiornamento dopo modifiche/livelli, solo in memoria. `/Thumb` incorporate ignorate. |
| VIS-15 Segnalibri: consultazione | P0 | fatto | `test_bookmarks.py::test_tree_and_click_navigation`, `test_xyz_destination_with_zoom`, `test_collapsed_state_and_styles`, `test_current_bookmark_highlight`, `test_large_outline_loads_fast`, `test_filter`, `test_broken_destination` | |
| VIS-16 Segnalibri: modifica | P1 | fatto | `test_bookmarks.py::test_add_bookmark_*`, `test_drag_under_another_keeps_count`, `test_unicode_title_roundtrip`, `test_delete_with_children_and_undo`, `test_move_and_indent_operations`, `test_styles_and_destination`, `test_rename_by_editing_item`, `test_read_only_*` | Azione «apri un indirizzo web» (`test_bookmark_opening_web_address`); apertura di altri file non fatta (il motore non scrive GoToR nei segnalibri). |
| VIS-17 Segnalibri avanzati | P1 | parziale | `test_bookmarks.py::test_export_import_text`, `test_generate_*`, `test_verify_lists_broken`, `test_engine.py::test_outline_*`, `test_verify_outline` | Esporta/importa testo, generazione dai titoli (stile), da espressione regolare (`test_generate_from_regex_with_levels`) e ogni N pagine, verifica. Pagina di sommario cliccabile (`test_create_toc_page_links_account_for_inserted_page`). Struttura taggata, destinazioni nominate, IA: non fatti. |
| VIS-18 Riassunto IA | P2 | non fatto | | Area IA. |
| VIS-19 Collegamenti | P0 | fatto | `test_navigation.py::test_describe_link`, `test_gotor_*`, `test_tab_visits_links_in_reading_order`, `test_link_order_tabs_structure`, `test_detect_url_in_text` | Suggerimento con la destinazione, GoToR in nuova scheda, Launch bloccato, Tab/Invio sui link, rilevamento URL nel testo. Conferma URI nel nucleo («consenti sempre per il dominio» non fatto). |
| VIS-20 Livelli: visualizzazione | P1 | fatto | `test_layers.py` | OCMD `/VE`, `/Usage` zoom/lingua, configurazioni alternative: non fatto. Stampa secondo `/Print`: `test_printing.py::test_layers_print_state`. |
| VIS-21 Livelli: gestione | P2 | parziale | `test_layers.py::test_save_visibility_as_default` | Solo «salva la visibilità come predefinita». |
| VIS-22 Allegati | P1 | parziale | `test_attachments.py` (16 test) | Elenco, apri, salva, salva tutti, aggiungi, elimina, eseguibili bloccati. Portfolio: non fatto. |
| VIS-23 Thread di articoli | P2 | fatto | `test_articles.py` | |
| VIS-24 Ricerca | P0 | fatto | `test_search.py` (anche `test_search_also_in_comments`), `test_engine.py::test_case_sensitivity`, `test_whole_word`, `test_ignore_accents_and_ligatures`, `test_hyphenated_word_across_lines` | Ricerca incrementale non bloccante, opzioni, F3/Maiusc+F3 e Ctrl+G/Ctrl+Maiusc+G, pannello Risultati, cronologia delle ricerche, pagine senza testo. Frasi e parole sillabate a cavallo tra due pagine incluse (`test_phrase_continuing_on_next_page`). |
| VIS-25 Ricerca avanzata | P2 | parziale | `test_advsearch.py` (11 test), `test_search.py::test_regex_and_invalid_expression`, `test_export_csv` | Ctrl+Maiusc+F: documento corrente, documenti aperti, cartella con sottocartelle (file cifrati/danneggiati nel riepilogo), frase/tutte/almeno una/regex con limite di tempo, commenti, segnalibri, campi, metadati, CSV. Indice full-text e allegati PDF: non fatti. |
| VIS-26 Trova e sostituisci | P1 | non fatto | | Richiede il motore di modifica del testo (area Modifica testo). |
| VIS-27 Ricerca semantica IA | P2 | non fatto | | Area IA. |
| VIS-28 Selezione e copia | P0 | parziale | `test_tools.py::test_alt_drag_rectangular_table_selection`, `tests/test_view.py::test_text_selection_by_drag` | Selezione rettangolare Alt+trascinamento con tabulazioni; il resto nel nucleo (selezione su più pagine, triplo clic: non fatti). |
| VIS-29 Cattura area | P1 | fatto | `test_tools.py::test_snapshot_*` | Copia/salva immagine a dpi scelti (limite 100 MP), copia testo, disabilitata senza permesso di copia. |
| VIS-30 Schede e sessioni | P0 | parziale | `test_session.py` | Ctrl+Tab, Alt+1…9, riapri scheda chiusa, chiudi altre/a destra/tutte, ripristino sessione. Trascinamento in altre finestre, gruppi, seconda vista: non fatti. |
| VIS-31 Modalità lettura e schermo intero | P1 | fatto | `test_reading.py` | Preferenze colore di sfondo e monitor: non fatte. |
| VIS-32 Presentazione | P1 | fatto | `test_presentation.py` | `/PresSteps`, transizioni diverse dalla dissolvenza: non fatte. |
| VIS-33 Scorrimento automatico | P1 | fatto | `test_reading.py` (scorrimento) | |
| VIS-34 Temi di lettura | P1 | fatto | `test_appearance.py` | Attivazione programmata: non fatta. |
| VIS-35 Lettura ad alta voce | P1 | fatto | `test_speech.py` (16 test) | Evidenziazione per frase (non per parola); servono `speech-dispatcher` o `espeak-ng`. |
| VIS-36 Riflusso | P2 | fatto | `test_reflow.py` | Titoli dalla dimensione del carattere (struttura taggata non usata), Ctrl++/Ctrl+-, uscita sul paragrafo corrispondente; immagini e tabelle non mostrate. |
| VIS-37 Multimedia e 3D | P2 | non fatto | | Segnalati come non supportati (VIS-07). |
| VIS-38 Lente, panoramica, vista divisa | P2 | parziale | `test_tools.py::test_lens_magnifies_under_cursor` | Lente d'ingrandimento; la panoramica è l'area visibile trascinabile nelle miniature (VIS-14). Vista divisa: non fatta (richiede più viste dello stesso documento nel nucleo). |
| VIS-39 Righelli, griglie, coordinate | P1 | parziale | `test_measure.py` | Coordinate del cursore nell'unità scelta, griglia, riquadri di pagina. Righelli, guide e aggancio, coordinate geospaziali: non fatti. |
| VIS-40 Informazioni sul documento | P1 | fatto | `test_properties.py` (15 test) | Finestra «Informazioni sul documento» (Alt+Invio) con le sezioni File, Contenuto, Protezione, Caratteri, Apertura; sola lettura. |
| VIS-41 Metadati avanzati | P2 | non fatto | | |
| VIS-42 Vista iniziale | P2 | parziale | `test_session.py` (vista iniziale) | Lettura delle impostazioni del documento; la loro modifica non è fatta. |
| VIS-43 Stampa | P0 | fatto | `test_printing.py` (21 test) | |
| VIS-44 Stampa avanzata | P2 | non fatto | | |
| VIS-45 Invio e condivisione | P1 | fatto | `test_share.py`, `test_files.py::test_reveal_and_copy_path` | Invio per e-mail (xdg-email), copia del file, mostra nella cartella, copia percorso. |
| VIS-46 Link e QR | P2 | non fatto | | |
| VIS-47 Salvataggio | P0 | nucleo | `tests/test_document.py` | Salvataggio atomico nel nucleo. |
| VIS-48 Salva con nome / copia | P0 | nucleo | `tests/test_document.py::test_save_as_changes_path`, `test_save_copy_keeps_path` | |
| VIS-49 Ripristina e ricarica | P1 | fatto | `test_reload.py` (16 test) | Ripristina, ricarica automatica/chiedi/mai con debounce, barre non modali per conflitto e file eliminato. |
| VIS-50 Recupero dopo chiusura anomala | P1 | fatto | `test_recovery.py` (20 test) | Copia periodica in `~/.local/state/foliant/recovery/` (0700/0600, cifrata se l'originale lo è), proposta non bloccante all'avvio, pulizia dopo salvataggio o chiusura. |
| VIS-51 Barre e preferenze | P1 | parziale | `test_prefs.py` | Layout/zoom/pannello predefiniti, rispetto impostazioni del documento, ultima posizione, esporta/importa, ripristina predefiniti. Personalizzazione delle barre: non fatta. |
| VIS-52 Scorciatoie e ricerca comandi | P2 | fatto | `test_commands.py` | Ctrl+K, assegnazione con conflitti, esporta/importa. Schemi predefiniti ed elenco stampabile: non fatti. |
| VIS-53 Integrazione desktop | P1 | parziale | `test_thumbnailer.py` | Generatore di miniature (processo separato, limiti, nessuna miniatura dei cifrati); registrazione MIME e installazione nel nucleo (CORE_REQUESTS 14). |

## Note di integrazione
- `FOLIANT_VIEWER_PARTS="a,b"` carica solo alcune parti (sviluppo e test isolati).
- Nessun dialogo modale parte da solo: recupero dopo crash e proposta di schermo intero sono non bloccanti.
- Catture verificate in tema chiaro e scuro per tutti i pannelli e dialoghi.

## Dopo la scadenza (stabilizzazione)
- Incorporato il nucleo aggiornato; tolte le scorciatoie duplicate con quelle globali del nucleo
  (Ctrl+Tab, Ctrl+Maiusc+Tab, Ctrl+Maiusc+T, Alt+1…9, Ctrl+D → Alt+Invio per le proprietà):
  `tests/test_window.py::test_no_duplicate_shortcuts` passa con il modulo caricato.
- Non integrate perché incomplete: stampa avanzata (VIS-44), modifica della vista iniziale (VIS-42),
  metadati avanzati (VIS-41).
- Allineati alla specifica v2 (proroga fino alle 09:30): «Informazioni sul documento» con le sezioni
  richieste, etichette «Cattura area» e impostazioni di «apertura», opzione «Cerca anche nei commenti»
  nella barra Ctrl+F. VIS-54 «Allegati del documento» è coperta dal pannello Allegati (aggiungi,
  estrai, elimina).
