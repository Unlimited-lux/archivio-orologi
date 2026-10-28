# Stato del modulo Strumenti (`tools`)

Sezione 11 della specifica: confronto (CMP) e operazioni in blocco (BAT).
Modalità `strumenti` (ordine 100, icona `wrench`) con le azioni Confronta documenti,
Mostra confronto, Apri sessione di confronto, Confronta testo, Revisioni e Operazioni in
blocco; le stesse azioni sono nel menu ☰ e nella Home.

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
| `engine.py` | logica pura delle operazioni in blocco: coda, azioni, nomi, esecuzione, rapporto, sequenze, giornale |
| `batch_ui.py` | pannello Operazioni in blocco, parametri, sequenze, rapporto, cartelle monitorate |
| `watch.py` | cartelle monitorate (BAT-21) |
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
| CMP-14 | P2 | parziale | rv: tutti | Revisioni da `%%EOF`; data solo da /ModDate; versione firmata individuata dal /ByteRange (testata con un /ByteRange simulato). |

## Operazioni in blocco

| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| BAT-01 | P1 | fatto | be: `test_collect_folder_with_subfolders`, `test_collect_no_duplicates_and_symlink_cycles`, `test_natural_sort`, `test_analyze_encrypted_requires_password`; bu: `test_queue_from_folder_with_subfolders`, `test_encrypted_row_shows_password_needed` | Analisi preliminare con `ctx.run_task`; password per riga (doppio clic o menu). |
| BAT-02 | P1 | parziale | be: `test_same_name_from_different_folders_renamed`, `test_skip_policy_leaves_existing_file`, `test_keep_structure`, `test_replace_originals_with_backup`, `test_destination_modes`, `test_cancel_during_eighth_file`, `test_not_enough_space_blocks_start`; bu: `test_replace_originals_requires_double_confirmation` | Politica «chiedi ogni volta» con conflitti individuati prima dell'avvio (bu: `test_conflict_ask_each_time`). Manca la pausa automatica su disco pieno. |
| BAT-03 | P1 | fatto | be: `test_counter_pattern`, `test_invalid_chars_and_placeholders`, `test_convert_images_multiple_outputs`; bu: `test_run_with_counter_pattern_and_report`, `test_invalid_pattern_blocks_start` | Anteprima dei primi 5 nomi; menu dei segnaposto. Preferiti dei modelli non salvati. |
| BAT-04 | P1 | parziale | be: `test_cancel_during_eighth_file`, `test_pause_and_resume_give_same_results`, `test_skip_current_file_only`, `test_journal_offers_resume_of_unfinished_files`; bu: `test_interrupted_job_resumed_on_open`, `test_control_panel_pause_toggle` | Lavoro non modale con `ctx.run_task(modal=False)` (avanzamento e Annulla tutto nella barra di stato, bu: `test_batch_runs_without_blocking_window`); Pausa/Riprendi e «Annulla file» in una piccola finestra accanto; giornale persistente con ripresa dei file non completati. Elaborazione sequenziale (nessun parallelismo: il motore PDF è serializzato da ENGINE_LOCK). Risultati identici nel contenuto ma non byte per byte (/ID generato a ogni salvataggio). |
| BAT-05 | P1 | fatto | be: `test_errors_per_file_continue`, `test_errors_stop_policy`, `test_csv_report_and_no_passwords`, `test_missing_file_reported`, `test_history_keeps_last_20_and_pdf_report`, `test_step_failure_reports_step`; bu: `test_errors_per_file_and_retry_failed`, `test_pdf_report_and_history` | Rapporto CSV/JSON/PDF, filtro per esito, doppio clic apre il risultato, «Riprova i non riusciti», cronologia degli ultimi 20 lavori. |
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
| BAT-16 | P1 | parziale | be: `test_watermark_marked_and_removable`, `test_remove_marks_nothing_found` | Rimuove gli elementi aggiunti da Foliant, sia dalle operazioni in blocco sia dal modulo Pagine (marcatura comune). |
| BAT-17 | P1 | fatto | be: `test_merge_order_and_bookmarks`, `test_merge_with_damaged_file_produces_nothing`, `test_merge_one_file_per_folder`, `test_merge_omit_failed_file` | Un unico file, un file per cartella, ogni N ingressi; politica interrompi/ometti; segnalibri originali annidati sotto quello del file e titoli distinti per file omonimi (be: `test_merge_keeps_original_bookmarks_nested`, `test_merge_bookmarks_disambiguate_same_names`). |
| BAT-18 | P1 | fatto | be: `test_insert_pages` | |
| BAT-19 | P1 | fatto | be: `test_print_jobs_in_order`, `test_print_forbidden` | Stampa con il comando `lp` (CUPS). |
| BAT-20 | P1 | fatto | be: `test_sequences_roundtrip_without_secrets`, `test_step_failure_reports_step`; bu: `test_sequences_saved_without_passwords`, `test_ask_at_launch_once_for_all_files`, `test_disabled_step_is_skipped`, `test_export_import_duplicate_sequence`, `test_convert_then_watermark_blocked` | Parametri «chiedi all'avvio» (le password vuote sempre), passi disattivabili, importa/esporta/duplica, schema versionato. L'uscita (destinazione, nomi, conflitti, errori) è salvata nella sequenza (bu: `test_sequence_keeps_output_settings`). Manca il passo «Salva una copia intermedia». |
| BAT-21 | P2 | fatto | wt: tutti | Scansione periodica (2 s) con attesa di fine copia, «originali»/«errori», registro cumulativo esportabile. Le password non sono conservate: le azioni che le richiedono non sono ammesse. |
| BAT-22 | P2 | fatto | cli: tutti | `python -m foliant.modules.tools.cli`; codici di uscita 0–4; password solo da stdin o file. Un sottocomando `foliant batch` richiede una modifica al nucleo (CORE_REQUESTS.md). |
| BAT-23 | P2 | parziale | bx: tutti | Ruota, elimina (anche pagine bianche), estrai, dividi, appiattisci, sanifica, metadati Info+XMP, vista iniziale, redazione per testo o schema, ottimizza per il web, esporta immagini. Mancano PDF/A con verifica e dati dei moduli. |

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
