# Stato del modulo Pagine (`modules/pages`)

Modalità **Pagine** (`ctx.add_mode("pagine", …, "layout-grid", 40)`): all'ingresso l'organizzatore a
griglia sostituisce la vista documento (`ctx.set_central_widget`), all'uscita, alla chiusura del
documento o senza documenti torna la vista normale. Ogni modifica passa da `document.change()` (un
solo passo di annulla/ripeti); la griglia si aggiorna su `ctx.documentChanged`.

## File
| File | Contenuto |
|---|---|
| `engine.py` | motore strutturale senza interfaccia: intervalli (PAG-02), riordino, inserimento, eliminazione, duplicazione, rotazione, estrazione, sostituzione, divisione, unione (`merge_files`, `split_document`, `merge_alternate`), potatura della struttura logica |
| `stamp.py` | filigrane, sfondi, numeri di pagina, Bates, intestazioni/piè di pagina, etichette (`add_watermark`, `add_page_numbers`, `add_header_footer`, …) |
| `layout.py` | ritaglio, margini bianchi, riquadri di stampa, formato pagina, dividi pagina, n-up |
| `images.py` | pagine da immagini/testo/appunti, esportazione come immagini |
| `organizer.py` | griglia di miniature (rendering pigro, selezione, trascinamento) |
| `module.py`, `actions.py` | controller della modalità, barra contestuale, menu, appunti, condivisione, permessi |
| `dialogs.py`, `stamp_dialogs.py`, `layout_dialogs.py`, `images_dialogs.py` | dialoghi |

Test: `tests/modules/pages/` (engine, organizer, dialogs, stamp, stamp_dialogs, layout,
layout_dialogs, images, images_dialogs). Funzioni del motore riusabili dagli altri moduli:
`engine.merge_files(paths) -> bytes`, `engine.split_document(pdf, metodo, valore)`,
`engine.extract_pages(pdf, indici) -> bytes`, `stamp.add_watermark(pdf, …)`,
`stamp.add_page_numbers(pdf, …)`, `stamp.add_header_footer(pdf, …)`, `layout.nup(pdf, …) -> bytes`.

## Funzioni della specifica (sezione 2)

| ID | Pr. | Stato | Test principali | Note |
|---|---|---|---|---|
| PAG-01 Organizzatore a griglia | P0 | fatto | test_organizer: mode_shows_organizer_and_restores, click_shift_click_selects_range, select_all_and_filters, thumbnails_render_with_size, lazy_rendering_large_document, corrupt_page_placeholder, rotate_and_undo_identical, assembly_denied_disables | numero, etichetta, indicatori rotazione/formato diverso/annotazioni; cursore e Ctrl+rotella; 5.000 pagine: ingresso 0,2 s, in memoria solo visibili+margine |
| PAG-02 Intervalli | P0 | fatto | test_engine: parse_ranges_*, filters, labels_*; test_dialogs: page_scope_validation, page_scope_labels, extract_dialog_disables_ok_on_error | validazione in linea, conteggio, filtri, etichette, ambiguità |
| PAG-03 Integrità riferimenti | P0 | fatto | test_engine: move_keeps_bookmark_target, delete_cleans_references, delete_prunes_structure_tree, extract_prunes_structure_tree, merge_form_fields_distinct, duplicate_form_field_independent; test_regressions: delete_page_with_field_and_image_leaves_no_orphans, extract_keeps_acroform, extract_and_split_keep_child_bookmarks, delete_pages_rethreads_article_beads, delete_all_thread_pages_removes_thread, pdfa_warning_when_inserting_non_pdfa, signed_rotation_single_warning; test_organizer: assembly_denied_disables | riferimenti per oggetto (segnalibri, link, destinazioni), campi modulo e AcroForm, struttura logica potata, thread di articoli ricuciti, /OpenAction, permesso di assemblaggio (document.allows/unlock), avviso firme e certificazione delegato al modulo Firma (un solo avviso), avviso PDF/A quando si inseriscono pagine da file non PDF/A |
| PAG-04 Riordino | P0 | fatto | test_engine: move_*; test_organizer: drag_reorder_via_signal, drop_index_geometry, move_end_and_duplicate; test_dialogs: move_dialog | indicatore di inserimento, scorrimento automatico, rilascio sull'origine senza voce di annullamento |
| PAG-05 Inverti/scambia/fascicola | P1 | fatto | test_engine: reverse_swap_collate; test_organizer: reverse_swap_pairs; test_dialogs: collate_dialog | anteprima dell'ordine e avviso per parti diverse |
| PAG-06 Pagina vuota | P0 | fatto | test_engine: insert_blank_*; test_dialogs: insert_blank_dialog, insert_blank_custom; test_organizer: plus_button_inserts_blank | pulsante «+» tra le miniature; mancano i modelli di carta (righe/quadretti), che dipendono dall'area Creazione |
| PAG-07 Inserisci da file | P0 | fatto | test_engine: insert_from_other_file, insert_interleaved*, insert_same_document, insert_shared_image_stored_once, open_source_*, insert_with_source_bookmarks; test_dialogs: insert_file_* | anteprima a miniature dell'intervallo sorgente; trascinamento di file sulla griglia |
| PAG-08 Da immagini | P1 | fatto | test_images (jpeg_*, png_alpha_*, tiff_*, exif_*, …), test_images_dialogs | TIFF bitonali in Flate (non CCITT G4); HEIC solo con pillow-heif |
| PAG-09 Dagli appunti | P1 | fatto | test_images: clipboard_*, text_200_lines_round_trip; test_images_dialogs: paste_* | |
| PAG-10 Da scanner | P1 | fatto | test_dialogs: scanner_none, scanner_fake_device, scanned_bw_one_bit_and_blank_discard, scanner_preset_remembered, scan_review_rotate_delete_more_and_cancel | SANE/`scanimage`: dispositivo, risoluzione, colore/grigi/bianco e nero (1 bit), alimentatore, scarto pagine vuote, ultime impostazioni ricordate; revisione delle facciate con ruota, elimina e «Acquisisci ancora» (le pagine già acquisite restano se il dispositivo si scollega); annullamento senza modifiche; messaggio se nessun dispositivo |
| PAG-11 Elimina | P0 | fatto | test_engine: delete_*; test_organizer: delete_selected_and_undo, delete_all_refused; test_regressions: delete_without_confirmation, delete_page_with_field_and_image_leaves_no_orphans | senza conferma (annullabile, UX-13); widget e campi delle pagine eliminate rimossi, nessun oggetto orfano |
| PAG-12 Estrai in PDF | P0 | fatto | test_engine: extract_*; test_dialogs: extract_single_opens_tab, extract_each_page_files_and_delete | file unico, per pagina, per intervallo; modello del nome |
| PAG-13 Estrai come immagini | P1 | fatto | test_images: export_*; test_images_dialogs: export_* | WebP senza dpi nei metadati |
| PAG-14 Trascinamento | P1 | fatto | test_organizer: drag_out_creates_pdf, drop_pages_from_other_document, drop_file_from_file_manager | PDF temporaneo «{nome} (pagine …)»; Maiusc = sposta |
| PAG-15 Duplica | P0 | fatto | test_engine: duplicate_*; test_dialogs: duplicate_dialog | risorse condivise (< 10 kB), campi rinominati, firme non duplicate; struttura logica non duplicata |
| PAG-16 Copia/taglia/incolla | P1 | fatto | test_organizer: copy_paste_between_documents, cut_paste_same_document | application/pdf + immagine della prima pagina |
| PAG-17 Condividi | P1 | fatto | test_organizer: share_mail, share_keeps_password | xdg-email; alternativa «Salva una copia» |
| PAG-18 Ruota | P0 | fatto | test_engine: rotate_*; test_organizer: rotate_and_undo_identical, quick_rotate_button | pulsanti rapidi sulla miniatura |
| PAG-19 Sostituisci | P1 | fatto | test_engine: replace_*; test_dialogs: replace_dialog, insert_file_preview_thumbnails | anteprima affiancata attuali/nuove a miniature |
| PAG-20 Dividi per intervalli/N | P0 | fatto | test_engine: split_every_3, split_ranges_uncovered_warning, split_parts_balanced, split_names_and_no_overwrite; test_dialogs: split_dialog_*, split_preview_colors_and_click_cuts | ogni N, intervalli, K parti; anteprima con miniature colorate per parte e punti di taglio aggiunti/tolti con un clic; avvisi per pagine escluse; modello del nome con campi; nessuna sovrascrittura salvo scelta; applicabile ad altri file |
| PAG-21 Per segnalibri | P1 | fatto | test_engine: split_bookmarks*; test_dialogs: split_dialog_bookmark_template | |
| PAG-22 Per dimensione | P1 | fatto | test_engine: split_by_size | stima preliminare non mostrata (calcolo alla conferma) |
| PAG-23 Per separatori/testo | P2 | fatto | test_engine: split_separators_and_text, split_text_top_area_only, split_text_with_ocr_on_scanned_pages | pagine vuote (scartate), testo/espressione regolare con {match1}, area «quarto superiore», OCR (Tesseract) per le pagine scannerizzate senza testo; i punti di taglio trovati si possono correggere con un clic sulle miniature (PAG-20) | 
| PAG-24 Unisci | P0 | fatto | test_engine: merge_*; test_dialogs: merge_dialog*, merge_custom_metadata | segnalibri annidati, intervalli, pagina vuota dopo dispari, unione alternata, metadati dal primo file o personalizzati (titolo, autore) |
| PAG-25 Ritaglia | P1 | fatto | test_layout: crop_*; test_layout_dialogs: crop_dialog_* | avviso contenuto nascosto all'applicazione (al salvataggio serve il nucleo, vedi CORE_REQUESTS) |
| PAG-26 Margini bianchi | P1 | fatto | test_layout: white_margins_*, content_bbox_*; test_layout_dialogs: remove_margins_* | rettangolo modificabile a mano per pagina |
| PAG-27 Riquadri di stampa | P2 | fatto | test_layout: trim_with_bleed, boxes_defaults_and_validation; test_layout_dialogs: boxes_dialog_*, boxes_overlay_install_toggle | riquadri visibili nella vista documento (sovrapposizione attivabile); /BoxColorInfo e /UserUnit non gestiti |
| PAG-28 Formato pagina | P1 | fatto | test_layout: resize_*, match_page_size_to_first; test_layout_dialogs: resize_dialog_* | scala (contieni/riempi/non proporzionale) o tela; avviso testo < 4 pt; /UserUnit non usato oltre 14.400 pt (rifiutato) |
| PAG-29 Dividi pagina | P1 | fatto | test_layout: split_*, detect_fold_dark_bright_and_none; test_layout_dialogs: split_dialog* | griglia, tagli manuali, piega centrale rilevata, rimozione del testo esterno |
| PAG-30 N-up | P1 | fatto | test_layout: nup_*; test_layout_dialogs: nup_dialog_opens_new_document | |

## Voci aggiunte nella specifica v2 (PAG-31…41)
| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| PAG-31 Opuscolo | P2 | fatto | test_layout: booklet_*; test_layout_dialogs: booklet_* | punto metallico o segnature, rilegatura a sinistra/destra, pagine vuote in fondo o prima dell'ultima, margine di rilegatura, compensazione dello scorrimento; risultato in una nuova scheda; criteri 8 pagine A5 → [8,1] [2,7] [6,3] [4,5] e 6 pagine → 2 vuote |
| PAG-32 Campi dinamici | P1 | fatto | test_fields; test_dialogs: template_field_menu_preview_and_validation; test_stamp*: placeholder/field tests; test_english: default_output_names_follow_language, english_placeholder_aliases, stamp_dates_follow_language | linguaggio unico (`fields.py`): {pagina}, {totale}, {etichetta}, {data:FORMATO}, {ora:FORMATO}, {nome}, {titolo}, {autore}, {oggetto}, {bates}, {n}, {segnalibro}, {intervallo}, {match1}, {pagina:3}, {titolo|nome}, {{ }}; usato da numeri, intestazioni/piè, filigrane e nomi dei file; menu «Inserisci campo», anteprima, conferma bloccata con segnaposto sconosciuti; nomi inglesi accettati e proposti in inglese ({name}, {page}, {date:yyyy-MM-dd}…); date e ore senza formato esplicito nel formato della lingua (format_date del nucleo) |
| PAG-33 Numeri di pagina | P1 | fatto | test_stamp: page_numbers_*; test_stamp_dialogs; test_regressions: page_numbers_pag33_criteria, restamping_replaces_existing_series | formati e stili (1, 1/N, «Pagina 1 di N», «- 1 -», i/ii, I/II, a/b), pagina di partenza ed esclusioni, numero iniziale, posizione speculare esterno/interno, etichetta di pagina, marcatura come artefatto di paginazione (fuori dall'albero di struttura), «Modifica» e «Rimuovi» con rendering identico; manca la riduzione del contenuto per fare spazio |
| PAG-34 Intestazioni e piè | P1 | fatto | test_stamp: header_footer_*; test_stamp_dialogs; test_regressions: restamping_replaces_existing_series | sei aree su più righe con campi dinamici, pari/dispari e prima pagina diversi, logo, sottolineato, opacità, margini, modelli salvabili e riutilizzabili, «Modifica» che riapre i parametri e sostituisce senza duplicati, anteprima pagina per pagina, artefatti Header/Footer |
| PAG-35 Bates | P1 | fatto | test_bates (11), test_bates_dialogs (10); test_stamp: bates_*; test_regressions: restamping_replaces_existing_series | documento corrente e più file: prefisso, cifre 3–15, suffisso, inizio, numerazione continua o per documento, sei posizioni, controllo delle cifre prima di iniziare, file cifrati saltati o numerati con password, sostituzione dei Bates esistenti, registro CSV (file, pagina, numero), copie con «{nome}_{bates_inizio}-{bates_fine}» o sovrascrittura con conferma, ripresa dopo annullamento, rimozione con rendering identico; menu Timbri › «Numerazione Bates su più file…» |
| PAG-36 Filigrane | P1 | fatto | test_stamp: watermark_*, remove_*, tiling, print-only; test_stamp_dialogs | testo (con campi), immagine o pagina PDF, opacità, rotazione, 9 ancoraggi con scostamenti, mosaico (immagine incorporata una volta), sopra/sotto, visibilità schermo/stampa (contenuto opzionale /Usage), elenco e modifica/rimozione singola con rendering identico, avvisi PDF/A-1 e scansioni opache; mancano i modelli salvabili per le filigrane |
| PAG-37 Filigrana a posizione fissa | P2 | fatto | test_fixedmark, test_fixedmark_dialogs | annotazione /Watermark con /FixedPrint (/Matrix, /H, /V), aspetto Form XObject, flag Print, rimozione e appiattimento; la stampa reale al 50% è verificata con la formula della specifica, non con una stampante |
| PAG-38 Sfondi | P1 | fatto | test_stamp: background_*; test_stamp_dialogs | colore, immagine o pagina PDF (carta intestata) con regola «pagina 1 sulla prima, pagina 2 sulle altre», scala adatta/riempi/percentuale/reale, opacità, visibilità schermo/stampa, artefatto Background, rimozione con rendering identico; criterio #FFF5CC verificato |
| PAG-39 Etichette di pagina | P1 | fatto | test_stamp: page_labels_*; test_engine: labels_interpretation, labels_anchored_after_insert_and_delete, labels_follow_moved_page | definizione, anteprima, rimozione, voce per l'indice 0; intervalli ancorati alla pagina che li apre dopo inserimenti, eliminazioni, spostamenti, duplicazioni e sostituzioni |
| PAG-40 Pagine vuote e duplicate | P1 | fatto | test_detect, test_detect_dialogs; test_organizer: find_blank_and_duplicates_selects_and_deletes | soglia d'inchiostro, esclusione di annotazioni e artefatti di paginazione, duplicati identici o quasi identici (impronta visiva + testo), ambito tutto il documento o pagine adiacenti, risultati con motivo e miniature affiancate, selezione nell'organizzatore o eliminazione in un solo passo annullabile |
| PAG-41 Pulizia guidata | P1 | fatto | test_cleanup, test_cleanup_dialogs | pagine vuote o non valide, orientamento del testo (righe di testo; scansioni con Tesseract locale, nessuna proposta se la confidenza è bassa), formati non uniformi, pagine solo immagine da inviare all'OCR, annotazioni da esportare (JSON) o rimuovere; proposte accettate singolarmente, applicate in un solo passo annullabile; nessuna connessione di rete (verificato); classificazione IA facoltativa non implementata |

Nota storica: alle 04:30 UTC i tre subagenti si sono fermati per il limite di spesa e la sessione è rimasta
inattiva fino alle 07:47; il loro lavoro incompleto (Bates su più file, pulizia guidata)
non è stato incluso perché non verificato; il motore di rilevazione (PAG-40), già testato, è stato recuperato.

## Funzioni richieste oltre la sezione 2 (filigrane, numeri, intestazioni, etichette)
| Funzione | Stato | Test |
|---|---|---|
| Filigrana testo/immagine: aggiungi, modifica, rimuovi | fatto | test_stamp: text_watermark_*, image_watermark*, remove_*, update_watermark_replaces, rotated_*; test_stamp_dialogs: watermark_* |
| Sfondi | fatto | test_stamp: background_*; test_stamp_dialogs: background_dialog |
| Numeri di pagina, Bates | fatto | test_stamp: page_numbers_*, bates_*; test_stamp_dialogs |
| Intestazioni e piè di pagina | fatto | test_stamp: header_footer_*; test_stamp_dialogs |
| Etichette di pagina | fatto | test_stamp: page_labels_*; test_stamp_dialogs: page_labels_dialog |

Verifica visiva: catture offscreen dell'organizzatore e di tutti i dialoghi in tema chiaro e scuro
(test `*_screenshots` e controllo manuale delle immagini).
