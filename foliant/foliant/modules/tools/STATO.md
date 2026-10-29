# Stato del modulo Strumenti (`tools`)

Sezione 11 della specifica: confronto (CMP) e operazioni in blocco (BAT).
Modalità `strumenti` (ordine 100, icona `wrench`) con la barra in gruppi come in Excel:
Confronta (Confronta documenti, Mostra confronto, Apri sessione di confronto, Confronta testo,
Revisioni) | Automazione (Operazioni in blocco). Confronta documenti e Operazioni in blocco
sono anche nella Home.

**Riservatezza (regola del committente):** il modulo non conserva nel profilo nomi, percorsi o
cartelle dei file elaborati. Cronologia dei lavori, stato di ripresa, cartelle monitorate e il
loro registro restano solo in memoria per la sessione; le sequenze salvate non contengono né la
cartella di destinazione né i file dei parametri (diventano «chiedi all'avvio»); i file si
scelgono con i dialoghi del contesto. All'avvio `privacy.cleanup_legacy` cancella, se li trova (in pratica una volta
sola), giornale, rapporti e monitoraggi delle versioni precedenti e ripulisce le sequenze
(bu: `test_old_journal_history_and_watches_removed_at_start`). Ammessi: contatori delle serie
Bates (`serie-bates.json`, solo etichette e numeri) e file salvati dall'utente dove sceglie lui.

Test: `QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests/modules/tools`
(file in `tests/modules/tools/`; nomi abbreviati qui sotto: `ce` = `test_compare_engine.py`,
`cu` = `test_compare_ui.py`, `rp` = `test_report.py`, `be` = `test_batch_engine.py`,
`bx` = `test_batch_extra.py`, `bu` = `test_batch_ui.py`, `cli` = `test_cli.py`,
`wt` = `test_watch.py`, `tc` = `test_text_compare.py`, `rv` = `test_revisions.py`).

## File

| File | Contenuto |
|---|---|
| `compare_engine.py` | logica pura del confronto: testo, formattazione, struttura, visivo, sovrapposizione, ancore di sincronizzazione, sessione |
| `report.py` | rapporto PDF del confronto e copia di B annotata |
| `compare_ui.py` | finestra di avvio, sessione «A ↔ B» (affiancata/sovrapposta), elenco differenze, controllore |
| `text_compare.py` | confronto di testo semplice (CMP-13) |
| `revisions.py` | revisioni di un file con aggiornamenti incrementali (CMP-14) |
| `engine.py` | logica pura delle operazioni in blocco: coda, azioni, nomi, esecuzione, rapporto, sequenze (il giornale su disco è stato tolto) |
| `batch_ui.py` | pannello Operazioni in blocco, parametri, sequenze, rapporto, cartelle monitorate |
| `watch.py` | cartelle monitorate (BAT-21) |
| `privacy.py` | pulizia all'avvio dei dati delle versioni precedenti |
| `cli.py` | riga di comando (BAT-22): `python -m foliant.modules.tools.cli --help` |
| `translations.py` | catalogo inglese (verificato da `test_translations.py`) |

## Confronto

| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| CMP-01 | P0 | fatto | ce: `test_same_file_has_no_differences`, `test_swap_turns_additions_into_deletions`, `test_encrypted_side_requires_password`, `test_non_pdf_is_rejected`, `test_damaged_pdf_gives_clear_error`, `test_cancel_stops_comparison`, `test_cancel_within_two_seconds_on_500_pages`, `test_page_range_limits_comparison`; cu: `test_setup_proposes_active_document_and_swaps`, `test_unsaved_document_is_labelled`, `test_encrypted_file_asks_password_before_processing`, `test_comparison_session_shown_and_closed`, `test_failed_comparison_shows_error_and_no_session` | Il calcolo gira con `ctx.run_task(modal=False)`: la finestra resta usabile, avanzamento e Annulla nella barra di stato (cu: `test_comparison_runs_without_blocking_window`). Voce «Confronta con…» nel menu della scheda (cu: `test_tab_menu_compare_with`). Prestazioni misurate: 500 pagine in ~1,3 s (solo testo) e ~3,5 s (automatico, 100 dpi). |
| CMP-02 | P0 | fatto | ce: `test_modified_number_is_one_modification`, `test_inserted_page_only_additions_on_that_page`, `test_ignore_case`, `test_ignore_punctuation_and_whitespace`, `test_hyphenation_and_ligatures`, `test_reflow_across_pages_is_not_a_difference`, `test_unrelated_documents_warning`, `test_extraction_forbidden_falls_back_to_visual` | Ordine di lettura dal motore (`sort=True`), non dall'albero di struttura dei PDF taggati. Segmentazione per spazi (non per carattere per le scritture CJK). |
| CMP-03 | P1 | fatto | ce: `test_format_difference_bold`, `test_format_difference_size`, `test_subset_prefix_is_ignored` | Normalizzazione dei nomi dei font per suffissi di stile, senza tabella di equivalenze commerciali. |
| CMP-04 | P0 | fatto | ce: `test_visual_rectangle_detected`, `test_antialiasing_is_ignored_at_medium`, `test_shifted_scans_aligned`, `test_page_size_reported_once`, `test_manual_pairing_recomputes_only_those_pages`, `test_page_only_in_b_without_text` | Allineamento automatico per traslazione (correlazione di fase, ±5 mm); la rotazione fino a ±2° non è stimata. Niente rendering a tasselli: oltre 40 Mpx la risoluzione è ridotta. |
| CMP-05 | P0 | fatto | ce: `test_sync_mapping_skips_inserted_pages`; cu: `test_synchronized_scroll_and_zoom`, `test_pick_in_pane_selects_everywhere`, `test_click_on_item_shows_difference_in_both_panes`, `test_narrow_window_switches_to_vertical` | Mappa delle differenze accanto a ogni riquadro; orientamento alto/basso. |
| CMP-06 | P0 | fatto | ce: `test_overlay_identical_has_no_pure_colors`, `test_overlay_moved_line_shows_both_colors`, `test_overlay_opacity_zero_shows_only_b`, `test_overlay_manual_shift`, `test_transform_image_rotation_and_scale`; cu: `test_overlay_view_and_session_roundtrip` | Traslazione (0,1 mm, frecce o trascinamento), rotazione fine e scala salvate nella sessione; alternanza ≤ 3 Hz; schema colori per daltonici. Manca la registrazione a due punti. |
| CMP-07 | P0 | fatto | cu: `test_next_difference_visits_each_once`, `test_click_on_item_shows_difference_in_both_panes`, `test_identical_message`, `test_filters_counters_and_hidden_message`, `test_twenty_thousand_differences_list` | Elenco virtualizzato (QListView), Alt+↓/Alt+↑, copia negli appunti, ordinamento per A o B. Il raggruppamento per pagina non c'è. |
| CMP-08 | P0 | fatto | ce: `test_ignore_headers_footers`, `test_ignore_page_numbers`, `test_exclusion_area_on_all_pages`; cu: `test_filters_counters_and_hidden_message`; rp: `test_report_filters_exclude_kind` | Aree di esclusione disegnate con il mouse («Escludi un'area», per pagina o per tutte) con ricalcolo (cu: `test_draw_exclusion_area_and_recompute`); il ricalcolo riguarda l'intero confronto, non solo le pagine interessate. Gli artefatti /Artifact marcati non sono letti: si usa il testo ripetuto e le fasce. |
| CMP-09 | P1 | fatto | ce: `test_moved_page_reported_once_without_text_differences`, `test_swapped_pages_matched_by_content`, `test_image_replaced`, `test_annotations_added_and_modified`, `test_form_field_value`, `test_rotation_only` | Nuovi tipi filtrabili: pagine, immagini, annotazioni, campi modulo. |
| CMP-10 | P0 | fatto | rp: `test_report_summary_counts_and_links`, `test_report_filters_exclude_kind`, `test_report_no_differences`, `test_report_layout_b_only_and_all`, `test_annotated_b`, `test_suggested_name`; cu: `test_export_report_and_cancel_leaves_no_file` | Riepilogo con collegamenti GoTo e segnalibri, pagine A/B affiancate come Form XObject, B annotato. Manca l'opzione PDF/A-2b e la disposizione «sovrapposta» del rapporto. |
| CMP-11 | P2 | fatto | cu: `test_review_statuses_filter_report_and_session` | Stati Accettata/Rifiutata/Da verificare e note (menu contestuale dell'elenco), filtro e contatori per stato, sessione con controllo della versione dello schema. Manca la richiesta della nuova posizione di un file spostato. |
| CMP-12 | P1 | fatto | ce: `test_scan_preset_uses_ocr_in_memory`, `test_scan_preset_without_ocr_falls_back`, `test_swapped_pages_matched_by_content`; cu: `test_setup_proposes_active_document_and_swaps` | Preset Testo, Presentazione, Disegno tecnico, Scansione (OCR in memoria tramite `foliant.modules.ocr.engine.ocr_pdf_bytes`). Con il vero modulo OCR le differenze su parole a bassa affidabilità sono marcate «incerte», filtrabili, e il riepilogo indica la percentuale di parole incerte (ce: `test_scan_preset_marks_uncertain_differences`, `test_scan_preset_with_real_ocr_module`; cu: `test_uncertain_filter`). |
| CMP-13 | P2 | fatto | tc: tutti | Confronto di testo semplice per riga/parola/carattere, diff unificato verificato con `patch`. |
| CMP-14 | P2 | fatto | rv: tutti | Revisioni da `%%EOF`; data della firma (/M) o /ModDate; firmatario (/Name) nell’elenco; più firme in successione; «versione firmata» = ultima revisione firmata seguita da modifiche. Testato con firme reali del modulo Firma. |

## Operazioni in blocco

| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| BAT-01 | P1 | fatto | be: `test_collect_folder_with_subfolders`, `test_collect_no_duplicates_and_symlink_cycles`, `test_natural_sort`, `test_analyze_encrypted_requires_password`; bu: `test_queue_from_folder_with_subfolders`, `test_encrypted_row_shows_password_needed` | Analisi preliminare con `ctx.run_task`; password per riga (doppio clic o menu). |
| BAT-02 | P1 | fatto | be: `test_same_name_from_different_folders_renamed`, `test_skip_policy_leaves_existing_file`, `test_keep_structure`, `test_replace_originals_with_backup`, `test_destination_modes`, `test_cancel_during_eighth_file`, `test_not_enough_space_blocks_start`; bu: `test_replace_originals_requires_double_confirmation` | Politica «chiedi ogni volta» con conflitti individuati prima dell'avvio (bu: `test_conflict_ask_each_time`). Disco pieno: il lavoro va in pausa con l'avviso nella finestra dei controlli e riprova lo stesso file alla ripresa; senza controlli il file va in errore DISK-01 senza temporanei (be: `test_disk_full_pauses_and_retries`, `test_disk_full_without_control_is_file_error`; bu: `test_control_panel_shows_disk_full`). |
| BAT-03 | P1 | fatto | be: `test_counter_pattern`, `test_invalid_chars_and_placeholders`, `test_convert_images_multiple_outputs`; bu: `test_run_with_counter_pattern_and_report`, `test_invalid_pattern_blocks_start` | Anteprima dei primi 5 nomi; menu dei segnaposto. Preferiti dei modelli non salvati. |
| BAT-04 | P1 | fatto | be: `test_cancel_during_eighth_file`, `test_pause_and_resume_give_same_results`, `test_skip_current_file_only`, `test_interrupted_job_leaves_no_journal`; bu: `test_tools_module_keeps_nothing_between_sessions`, `test_control_panel_pause_toggle` | Lavoro non modale con `ctx.run_task(modal=False)` (avanzamento e Annulla tutto nella barra di stato, bu: `test_batch_runs_without_blocking_window`); Pausa/Riprendi e «Annulla file» in una piccola finestra accanto; nessun giornale su disco (riservatezza): «Riprendi i non elaborati» usa il rapporto in memoria della sessione. Elaborazione parallela configurabile (predefinita: metà dei core) per i passi PDF→PDF, con scrittura e nomi nell'ordine della coda; unione, stampa e conversione restano sequenziali (be: `test_parallel_processing_same_results`, `test_parallel_errors_and_cancel`; bu: `test_parallel_setting_used`). Risultati identici byte per byte tra esecuzioni con e senza pausa e tra parallelo e sequenziale (salvataggio senza nuovo /ID casuale; i file cifrati restano diversi per il sale casuale di AES). |
| BAT-05 | P1 | fatto | be: `test_errors_per_file_continue`, `test_errors_stop_policy`, `test_csv_report_and_no_passwords`, `test_missing_file_reported`, `test_pdf_report_and_no_history_on_disk`, `test_step_failure_reports_step`; bu: `test_errors_per_file_and_retry_failed`, `test_pdf_report_and_history` | Rapporto CSV/JSON/PDF, filtro per esito, doppio clic apre il risultato, «Riprova i non riusciti», cronologia degli ultimi 20 lavori della sessione (solo in memoria, non salvata). |
| BAT-06 | P1 | fatto (delegato) | be: `test_convert_with_fake_engine`, `test_convert_images_multiple_outputs`, `test_missing_engines_clear_message`, `test_convert_must_be_last`; bu: `test_ocr_and_convert_with_fake_engines` | Usa `foliant.modules.convert.engine.convert_pdf` se presente, altrimenti messaggio chiaro. |
| BAT-07 | P1 | fatto | be: `test_create_pdf_original_size`, `test_create_pdf_exif_and_tiff`, `test_create_single_pdf_with_bookmarks_and_office_errors`, `test_create_step_must_be_first` | Immagini (EXIF, TIFF multipagina, GIF primo fotogramma), testo/Markdown come testo semplice, documenti Office tramite LibreOffice se installato. Manca OCR delle immagini in questo passo (si può aggiungere il passo OCR) e uscita PDF/A. |
| BAT-08 | P1 | fatto (delegato) | be: `test_ocr_with_fake_engine`, `test_missing_engines_clear_message`; bu: `test_ocr_and_convert_with_fake_engines` | Usa `foliant.modules.ocr.engine.ocr_pdf_bytes`. |
| BAT-09 | P1 | fatto | be: `test_compress_never_grows`, `test_compress_encrypted_keeps_password` | |
| BAT-10 | P1 | fatto | be: `test_protect_requires_password`, `test_protect_permissions_only`, `test_protect_same_passwords_blocked`, `test_protect_csv_passwords` | AES-256. |
| BAT-11 | P1 | fatto | be: `test_remove_known_password`, `test_remove_password_candidate_number`, `test_remove_password_needs_owner`, `test_remove_password_not_protected_and_limit` | |
| BAT-12 | P1 | fatto | be: `test_watermark_every_page_and_odd_pages`, `test_watermark_name_placeholder_and_replace`, `test_watermark_marked_and_removable` | |
| BAT-13 | P1 | fatto | be: `test_background_letterhead` | |
| BAT-14 | P1 | fatto | be: `test_page_numbers_per_file_and_continuous`, `test_header_mirror_and_skip_first` | |
| BAT-15 | P1 | fatto | be: `test_bates_continuous_and_names`, `test_bates_insufficient_digits`, `test_bates_index_and_series`; bu: `test_bates_series_and_index_from_ui` | Indice CSV, «continua la serie» (serie salvate nella configurazione) , riquadro bianco dietro il numero per le scansioni e scelta del font (be: `test_bates_white_box_on_scan`, `test_bates_font_choice`). |
| BAT-16 | P1 | fatto | be: `test_watermark_marked_and_removable`, `test_remove_marks_nothing_found` , `test_remove_marks_scope_and_counts`, `test_remove_watermark_visually_identical` | Ambito predefinito: elementi aggiunti da Foliant (operazioni in blocco e modulo Pagine); ambito «tutti gli elementi marcati»: anche artefatti /Pagination (Header, Footer, PageNum, Watermark, Bates), /Background e annotazioni /Watermark di altri programmi. Conteggio per tipo nel rapporto; «Saltato: nessun elemento riconoscibile». Non si legge /PieceInfo. |
| BAT-17 | P1 | fatto | be: `test_merge_order_and_bookmarks`, `test_merge_with_damaged_file_produces_nothing`, `test_merge_one_file_per_folder`, `test_merge_omit_failed_file` | Un unico file, un file per cartella, ogni N ingressi; politica interrompi/ometti; segnalibri originali annidati sotto quello del file e titoli distinti per file omonimi (be: `test_merge_keeps_original_bookmarks_nested`, `test_merge_bookmarks_disambiguate_same_names`). |
| BAT-18 | P1 | fatto | be: `test_insert_pages` | |
| BAT-19 | P1 | fatto | be: `test_print_jobs_in_order`, `test_print_forbidden` | Stampa con il comando `lp` (CUPS). |
| BAT-20 | P1 | fatto | be: `test_sequences_roundtrip_without_secrets`, `test_step_failure_reports_step`; bu: `test_sequences_saved_without_passwords`, `test_ask_at_launch_once_for_all_files`, `test_disabled_step_is_skipped`, `test_export_import_duplicate_sequence`, `test_convert_then_watermark_blocked` | Parametri «chiedi all'avvio» (le password vuote sempre), passi disattivabili, importa/esporta/duplica, schema versionato. L'uscita (modo, nomi, conflitti, errori) è salvata nella sequenza, ma non la cartella di destinazione né i file dei parametri, che diventano «chiedi all'avvio» (bu: `test_sequence_keeps_output_settings`, `test_sequence_never_stores_file_paths`). Manca il passo «Salva una copia intermedia». |
| BAT-21 | P2 | fatto | wt: tutti | Scansione periodica (2 s) con attesa di fine copia, «originali»/«errori», registro cumulativo esportabile. Monitoraggi e registro solo per la sessione (nulla nel profilo); «Esporta/Importa configurazione…» su un file scelto dall'utente. Le password non sono conservate: le azioni che le richiedono non sono ammesse. |
| BAT-22 | P2 | fatto | cli: tutti | `python -m foliant.modules.tools.cli`; codici di uscita 0–4; password solo da stdin o file. Un sottocomando `foliant batch` richiede una modifica al nucleo (CORE_REQUESTS.md). |
| BAT-23 | P2 | fatto | bx: tutti | Ruota, elimina (anche pagine bianche), estrai, dividi, appiattisci, sanifica, metadati Info+XMP, vista iniziale, redazione per testo o schema, ottimizza per il web, esporta immagini, PDF/A con verifica (passo «pdfa»), esportazione dei dati dei moduli (CSV, JSON, XFDF, FDF, XML) e importazione (CSV con colonna «File» per riga per file). |

## Dipendenze

Nessuna nuova dipendenza pip (numpy e Pillow sono già in `requirements.txt`).
Facoltative di sistema: LibreOffice (`soffice`) per BAT-07 con documenti Office, CUPS (`lp`) per
BAT-19, `patch` solo per un test di CMP-13.

## Aggiornamenti finali

- Specifica v2: tutte le BAT sono P1 (BAT-21…23 P2); priorità aggiornate nella tabella.
  Bates (PAG-35), unione (PAG-24) e livelli di compressione (CNV-29) usano l'implementazione
  locale del modulo, non ancora riallineata a quelle sezioni.
- Nucleo incorporato (merge di `origin/claude/pdf-editor-updf-clone-iod4xs`), suite completa verde.
- Filigrana, Bates e intestazioni/piè verificati su pagine ruotate (0/90/180/270°) e con CropBox
  spostato (be: `test_marks_on_rotated_and_cropped_pages`); non usano ancora `foliant.api.placement`.
- Nessuna scorciatoia globale registrata dal modulo (Alt+↓/↑ solo dentro la vista di confronto).
- Filigrane, sfondi a colore, intestazioni/piè, numeri di pagina e Bates in blocco usano la marcatura
  comune `/FolStamp_<tipo>_<n>` di `foliant/modules/pages/stamp.py` (PAG-36): si sostituiscono e si
  rimuovono a vicenda con le operazioni del modulo Pagine; la vecchia marcatura /Artifact resta
  riconosciuta in rimozione (be: `test_watermark_shares_marking_with_pages_module`,
  `test_legacy_marks_still_removed`, `test_header_and_numbers_share_marking_and_dont_replace_each_other`).
  Restano con la marcatura propria la carta intestata PDF e i Bates con riquadro bianco.
- Uscita PDF/A con verifica: passo «Converti in PDF/A con verifica» (1b/2b/3b) che usa
  `convert.pdfa.convert_to_pdfa`; il resoconto di conformità è nell'esito di ogni file, e un file non
  conforme va in errore (PDFA-02) oppure, a scelta, viene salvato con avviso
  (be: `test_pdfa_step_reports_conformance`, `test_pdfa_step_without_ghostscript_is_clear`).
  Serve Ghostscript (non installato nell'ambiente di test: verificato con un motore simulato).

## Verifiche dei flussi reali

- **Listino con prezzi cambiati** (celle di tabella come testi separati, 1 e 3 pagine): ogni prezzo
  cambiato è una differenza a sé, la riga nuova e quella tolta sono intere; nessuna falsa regione
  visiva dalle righe spostate. Corretti tre difetti: allineamento per righe PDF anziché visive
  (f74d618), prime/ultime righe della tabella e colonna «EUR» scambiate per intestazioni ripetute,
  prezzi nella fascia bassa scambiati per numeri di pagina (76f6daf).
  Test: `test_price_list_rows_are_separate_changes`, `test_multi_page_price_list_table_rows_are_not_headers`.
- **Contratto di compravendita** in due versioni (prezzo, termini, garanzia, clausola aggiunta):
  quattro differenze esatte con il riflusso del testo.
- **200 file misti** (148 validi, 10 troncati, 10 casuali, 10 vuoti, 10 cifrati, 10 non PDF,
  2 da 400 pagine) con numeri di pagina + compressione, annullamento al 90° file e ripresa:
  nessun file perso o doppio, esiti e messaggi coerenti per tipo, memoria Python < 30 MB, 200 file
  in ~9 s. Corretti: la compressione di MuPDF teneva fermo l'interprete per 30 s su un file grande
  (interfaccia bloccata, annullamento impossibile) e ricomprimeva l'immagine condivisa a ogni pagina
  → ora un'immagine per volta, annullabile, 0,2 s (7fddfc0, b9773e7); «Annulla file» durante un passo
  risultava errore; aggiunti «Riprendi i non elaborati» e il messaggio per i file vuoti (9aa9ee5).

## Voci chiuse dopo l'integrazione del 28/10

- C13 disposizione (515dbb3): sotto i 1400 px Operazioni e Destinazione si impilano con scorrimento
  verticale, niente scorrimento orizzontale, tendine che non impongono la larghezza della voce più
  lunga. Test a 1280×800, 1024×700 e 1920×1080 con caratteri ingranditi del 30%.
- NFR-26 formato carta (af5d122): Crea PDF propone A4 o Letter da `locale_defaults.paper_size()`;
  le scelte salvate nelle sequenze restano valide. Le misure dei passi in blocco (margini, ecc.)
  restano in mm: la conversione in pollici dei campi non è fatta.
- C11 numeri e date: `format_number` / `format_date` per dimensioni, durate, aree variate,
  affidabilità OCR, rapporti PDF e il segnaposto {data} dei timbri; nomi dei file in ISO.
