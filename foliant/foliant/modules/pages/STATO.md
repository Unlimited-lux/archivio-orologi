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
| PAG-03 Integrità riferimenti | P0 | parziale | test_engine: move_keeps_bookmark_target, delete_cleans_references, delete_prunes_structure_tree, extract_prunes_structure_tree, merge_form_fields_distinct, duplicate_form_field_independent; test_organizer: signed_document_warning, assembly_denied_disables | mancano: avviso PDF/A, blocco specifico per documenti certificati (trattati come firmati), fili di articoli; avviso al salvataggio incrementale non necessario (il nucleo salva sempre completo) |
| PAG-04 Riordino | P0 | fatto | test_engine: move_*; test_organizer: drag_reorder_via_signal, drop_index_geometry, move_end_and_duplicate; test_dialogs: move_dialog | indicatore di inserimento, scorrimento automatico, rilascio sull'origine senza voce di annullamento |
| PAG-05 Inverti/scambia/fascicola | P1 | fatto | test_engine: reverse_swap_collate; test_organizer: reverse_swap_pairs; test_dialogs: collate_dialog | anteprima dell'ordine e avviso per parti diverse |
| PAG-06 Pagina vuota | P0 | fatto | test_engine: insert_blank_*; test_dialogs: insert_blank_dialog, insert_blank_custom; test_organizer: plus_button_inserts_blank | pulsante «+» tra le miniature; mancano i modelli di carta (righe/quadretti), che dipendono dall'area Creazione |
| PAG-07 Inserisci da file | P0 | fatto | test_engine: insert_from_other_file, insert_interleaved*, insert_same_document, insert_shared_image_stored_once, open_source_*, insert_with_source_bookmarks; test_dialogs: insert_file_* | anteprima a miniature della sorgente non presente (solo conteggio) |
| PAG-08 Da immagini | P1 | fatto | test_images (jpeg_*, png_alpha_*, tiff_*, exif_*, …), test_images_dialogs | TIFF bitonali in Flate (non CCITT G4); HEIC solo con pillow-heif |
| PAG-09 Dagli appunti | P1 | fatto | test_images: clipboard_*, text_200_lines_round_trip; test_images_dialogs: paste_* | |
| PAG-10 Da scanner | P1 | parziale | test_dialogs: scanner_none, scanner_fake_device, scanned_bw_one_bit_and_blank_discard, scanner_preset_remembered | SANE/`scanimage`, alimentatore, scarto pagine vuote, ultime impostazioni ricordate, bianco e nero a 1 bit; manca l'anteprima con riacquisizione/rotazione delle singole facciate |
| PAG-11 Elimina | P0 | fatto | test_engine: delete_*; test_organizer: delete_selected_and_undo, delete_all_refused | riepilogo segnalibri/link/campi nella conferma |
| PAG-12 Estrai in PDF | P0 | fatto | test_engine: extract_*; test_dialogs: extract_single_opens_tab, extract_each_page_files_and_delete | file unico, per pagina, per intervallo; modello del nome |
| PAG-13 Estrai come immagini | P1 | fatto | test_images: export_*; test_images_dialogs: export_* | WebP senza dpi nei metadati |
| PAG-14 Trascinamento | P1 | fatto | test_organizer: drag_out_creates_pdf, drop_pages_from_other_document, drop_file_from_file_manager | PDF temporaneo «{nome} (pagine …)»; Maiusc = sposta |
| PAG-15 Duplica | P0 | fatto | test_engine: duplicate_*; test_dialogs: duplicate_dialog | risorse condivise (< 10 kB), campi rinominati, firme non duplicate; struttura logica non duplicata |
| PAG-16 Copia/taglia/incolla | P1 | fatto | test_organizer: copy_paste_between_documents, cut_paste_same_document | application/pdf + immagine della prima pagina |
| PAG-17 Condividi | P1 | fatto | test_organizer: share_mail, share_keeps_password | xdg-email; alternativa «Salva una copia» |
| PAG-18 Ruota | P0 | fatto | test_engine: rotate_*; test_organizer: rotate_and_undo_identical, quick_rotate_button | pulsanti rapidi sulla miniatura |
| PAG-19 Sostituisci | P1 | fatto | test_engine: replace_*; test_dialogs: replace_dialog | anteprima affiancata non presente |
| PAG-20 Dividi per intervalli/N | P0 | fatto | test_engine: split_every_3, split_ranges_uncovered_warning, split_parts_balanced, split_names_and_no_overwrite; test_dialogs: split_dialog_* | applicabile ad altri file; anteprima a colori sulle miniature non presente (anteprima testuale) |
| PAG-21 Per segnalibri | P1 | fatto | test_engine: split_bookmarks*; test_dialogs: split_dialog_bookmark_template | |
| PAG-22 Per dimensione | P1 | fatto | test_engine: split_by_size | stima preliminare non mostrata (calcolo alla conferma) |
| PAG-23 Per separatori/testo | P2 | parziale | test_engine: split_separators_and_text | mancano area di ricerca, OCR e modifica manuale dei tagli |
| PAG-24 Unisci | P0 | fatto | test_engine: merge_*; test_dialogs: merge_dialog* | segnalibri annidati, intervalli, pagina vuota dopo dispari, unione alternata; metadati personalizzati non esposti |
| PAG-25 Ritaglia | P1 | fatto | test_layout: crop_*; test_layout_dialogs: crop_dialog_* | avviso contenuto nascosto all'applicazione (al salvataggio serve il nucleo, vedi CORE_REQUESTS) |
| PAG-26 Margini bianchi | P1 | fatto | test_layout: white_margins_*, content_bbox_*; test_layout_dialogs: remove_margins_* | rettangolo modificabile a mano per pagina |
| PAG-27 Riquadri di stampa | P2 | fatto | test_layout: trim_with_bleed, boxes_defaults_and_validation; test_layout_dialogs: boxes_dialog_*, boxes_overlay | riquadri visibili nella vista documento (sovrapposizione attivabile); /BoxColorInfo e /UserUnit non gestiti |
| PAG-28 Formato pagina | P1 | fatto | test_layout: resize_*, match_page_size_to_first; test_layout_dialogs: resize_dialog_* | scala (contieni/riempi/non proporzionale) o tela; avviso testo < 4 pt; /UserUnit non usato oltre 14.400 pt (rifiutato) |
| PAG-29 Dividi pagina | P1 | fatto | test_layout: split_*, detect_fold_dark_bright_and_none; test_layout_dialogs: split_dialog* | griglia, tagli manuali, piega centrale rilevata, rimozione del testo esterno |
| PAG-30 N-up | P1 | fatto | test_layout: nup_*; test_layout_dialogs: nup_dialog_opens_new_document | |

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
