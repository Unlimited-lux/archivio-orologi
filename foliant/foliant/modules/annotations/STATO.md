# Stato del modulo «Commenta» (sezione 5 della specifica, ANN-*)

Struttura: `engine.py` (logica pura sul motore PDF, senza Qt), `controller.py` (stili, autore,
selezione, operazioni annullabili), `tools.py` (strumenti della vista), `panels.py` (pannello
proprietà a destra, elenco commenti a sinistra), `dialogs.py` (finestre, sostituibili nei test),
`ui_actions.py` (comandi con file: esporta/importa, riepilogo, appiattimento), `visibility.py`
(mostra/nascondi), `i18n_en.py` (traduzioni inglesi). Ogni modifica passa da `document.change()`.

Test: `tests/modules/annotations/` — `test_p0_create.py`, `test_p0_tools.py` (P0 dall'interfaccia),
`test_p1_ui.py` (P1 dall'interfaccia), `test_engine_p1.py` (motore), `test_i18n.py` (traduzioni).

| ID | Priorità | Stato | Test | Note |
|---|---|---|---|---|
| ANN-01 | P0 | parziale | test_ann01_every_type_saved_with_ap_nm_and_m, test_ann01_unhandled_annotation_untouched, test_ann01_private_key_kept_after_color_change | /NM UUID, /M, /AP /N per ogni tipo; tipi sconosciuti e chiavi private conservati; permesso «commenti» rispettato. Mancano: salvataggio incrementale per PDF firmati (nucleo, CORE_REQUESTS 1), vincoli PDF/A e struttura logica dei PDF taggati. |
| ANN-02 | P0 | fatto | test_ann02_author_subject_dates, test_ann02_non_latin_author_utf16, test_ann02_empty_author_restores_account_name, test_ann02_tolerant_dates | Autore predefinito = nome completo dell'account; conferma al primo uso (messaggio); «Nome autore…» nel menu Commenti. |
| ANN-03 | P0 | parziale | test_ann03_* (8), test_ann20_drag_vertex | Selezione (clic, Maiusc/Ctrl, rettangolo), spostamento (trascinamento, frecce 1/10 pt, vincolo al riquadro), maniglie di ridimensionamento (proporzionale per i timbri o con Maiusc), vertici di linee/poligoni/richiami, primo piano/sfondo, Canc, bloccate, rotazione dei timbri (test_ann03_rotate_stamp), allinea/distribuisci (test_ann03_align_and_distribute). Mancano rotazione di forme e caselle e la mini-barra. |
| ANN-04 | P0 | fatto | test_ann04_* (5) | Pannello proprietà: colore, riempimento/nessuno, opacità, spessore 0–12 a passi 0,25, tratteggio, nuvola, estremità, testo (dimensione, colore, allineamento), icona note; un passo di annullamento per selezione multipla. Colori recenti/esadecimale libero tramite «Altro colore…». |
| ANN-05 | P0 | fatto | test_ann05_comment_on_highlight_has_popup, test_ann05_rich_text_bold, test_ann05_long_comment_preserved, test_ann05_popup_left_open_is_saved | /Contents, /Popup collegata, /RC (grassetto, corsivo, sottolineato, colore), suggerimento al passaggio. Doppio clic apre l'editor del commento (intestazione autore/data); «Apri/chiudi finestra del commento» mostra la popup sulla pagina e salva /Open. La popup sulla pagina non si trascina né si ridimensiona. |
| ANN-06 | P0 | fatto | test_ann06_undo_redo_sequence, test_ann06_undo_after_save_marks_modified, test_ann23_undo_restores_inklist, test_ann41_42_export_import_ui | Tutto tramite `document.change()` (cronologia del nucleo). |
| ANN-07 | P1 | parziale | test_ann07_copy_paste_other_page, test_ann07_cut_paste_keeps_identity, test_ann07_duplicate_same_page_offset, test_ann07_paste_system_text_and_image | Ctrl+C/X/V/D nello stesso documento; testo e immagini dagli appunti di sistema (commento di testo, timbro immagine). Manca l'incolla in un altro documento; il taglia non sposta le risposte. |
| ANN-08 | P1 | fatto | test_ann08_default_style_survives_restart, test_ann08_keep_tool_three_rects, test_styles_corrupted_profile_falls_back | Stile per strumento persistente, «Imposta come predefinito», preferiti con nome (pannello proprietà), esporta/importa stili (JSON con versione), «Mantieni strumento». |
| ANN-09 | P1 | fatto | test_ann09_flags, test_ann09_locked_contents, test_ann03_locked_not_moved_or_deleted | Bloccata, Contenuto bloccato, Sola lettura, Stampa; Print attivo sui nuovi, NoZoom/NoRotate su note e allegati. |
| ANN-10 | P0 | parziale | test_ann10_* (5) | Trascinamento o selezione + comando/menu contestuale; quadrilateri per riga; fusione moltiplicativa; pagina senza testo → area con avviso OCR. Selezione su più pagine non disponibile (CORE_REQUESTS 3). |
| ANN-11 | P1 | fatto | test_ann11_area_highlight, test_ann10_image_page_area_highlight | Strumento «Evidenzia area» o Alt+trascinamento; /Rect = quadrilatero, ridimensionabile. |
| ANN-12 | P0 | fatto | test_ann12_three_markups_same_quads, test_ann12_line_positions | Stessa selezione per sottolineato/barrato/ondulato (multipagina: vedi ANN-10). |
| ANN-13 | P1 | fatto | test_ann13_insert_and_replace | Inserisci testo/paragrafo (/Caret /Sy), Sostituisci (barrato /IRT /RT /Group); voce unica «Sostituzione»; eliminazione di gruppo. |
| ANN-14 | P0 | fatto | test_ann14_note_click, test_ann14_note_icon_and_color, test_ann14_note_cancel_creates_nothing | Il motore normalizza l'icona a 16 pt (lato richiesto 20): l'icona ha dimensione costante a schermo (NoZoom). |
| ANN-15 | P1 | fatto | test_ann15_typewriter | Senza bordo, rettangolo adattato; testo vuoto → nessuna annotazione. Caratteri CJK: aspetto del motore. |
| ANN-16 | P0 | parziale | test_ann16_textbox, test_ann16_click_creates_default_width_box, test_ann16_rotated_page_text_horizontal | Aspetto vettoriale proprio (bordo e sfondo di colori distinti, a capo, allineamento, margine). Adattamento automatico dell'altezza (test_ann16_autofit_height). Manca la formattazione su porzioni di testo. |
| ANN-17 | P1 | fatto | test_ann17_callout, test_ann17_move_tip_only | Punta, gomito, casella; punta spostabile da sola. |
| ANN-18 | P0 | fatto | test_ann18_* (4) | Maiusc quadrato/cerchio, Alt dal centro. |
| ANN-19 | P0 | fatto | test_ann19_arrow, test_ann19_shift_snaps_45, test_ann19_caption | Estremità modificabili, didascalia (/Cap). |
| ANN-20 | P1 | fatto | test_ann20_polygon_tool, test_ann20_drag_vertex, test_polygon_vertices, test_polyline_vertices | Doppio clic/Invio/clic sul primo vertice, Backspace, Esc; vertici trascinabili, aggiunti e rimossi dal menu (test_ann20_add_remove_vertex_and_cloud_intensity). |
| ANN-21 | P1 | fatto | test_ann21_cloud_tool, test_cloud | /IT /PolygonCloud, /BE /S /C; «Bordo a nuvola» per rettangoli, ellissi e poligoni, intensità 0–2 dal menu contestuale. |
| ANN-22 | P0 | fatto | test_ann22_three_strokes_one_ink, test_ann22_simplification, test_ann22_marker_transparent | Tratti entro 2 s = una annotazione; semplificazione ≤ 0,5 pt e ≤ 2000 punti; pennarello con fusione moltiplicativa. |
| ANN-23 | P0 | fatto | test_ann23_partial_eraser, test_ann23_whole_stroke_and_others_untouched, test_ann23_undo_restores_inklist | Parziale e «tratti interi», dimensione regolabile; una passata = un passo. Anteprima della cancellazione durante il trascinamento, applicata al rilascio. |
| ANN-24 | P1 | fatto | test_ann24_25_stamp_tools, test_preset_stamp | 20 timbri originali, /Name standard o personalizzato, aspetto vettoriale. |
| ANN-25 | P1 | fatto | test_ann24_25_stamp_tools, test_ann25_counter_never_decrements, test_fill_template, test_dynamic_stamp_frozen_value | Segnaposto {autore} {data} {ora} {file} {pagina} {contatore}; contatore persistente. Editor di modelli personalizzati non fatto. |
| ANN-26 | P1 | parziale | test_ann26_custom_stamp_library, test_image_stamp | Timbri da immagine salvati nella libreria «Personalizzati» (persistente). Mancano timbri da pagina PDF/area e importa/esporta libreria. |
| ANN-27 | P2 | parziale | test_ann27_sticker | Sei adesivi vettoriali originali (spunta, croce, stella, attenzione, informazione, freccia), ruotabili; nessuna categoria né adesivi personalizzati. |
| ANN-28 | P2 | non fatto | | |
| ANN-29 | P1 | fatto | test_ann29_attachment_tool, test_attachment_round_trip | Doppio clic → solo «Salva allegato con nome» (nessuna esecuzione). PDF/A-1: allegato impedito (test_ann29_pdfa1_blocks_attachments). |
| ANN-30 | P1 | parziale | test_ann30_calibrate_tool, test_scale_calibrate | Scala per rapporto o calibrazione; /Measure. Ambito pagina corrente o tutte (test_ann30_scale_per_page), scala indicata nella barra di stato. Mancano le aree /VP del documento. |
| ANN-31 | P1 | fatto | test_ann31_distance_tool, test_ann31_measure_updates_when_vertex_moves, test_distance_measure | |
| ANN-32 | P1 | fatto | test_ann32_area_measure_tool, test_area_measure, test_perimeter_measure | |
| ANN-33 | P2 | parziale | test_ann33_measures_csv | Solo esportazione CSV delle misure. |
| ANN-34 | P0 | fatto | test_ann34_* (4) | Gruppi per pagina, autore/data, testo marcato, stato, risposte, spunta, menu contestuale, doppio clic, contatore. |
| ANN-35 | P1 | parziale | test_ann35_filters_and_search, test_ann35_filter_type_and_status, test_ann35_color_and_replies_filters | Tipo, autore, colore, stato, risposte, ricerca senza maiuscole/accenti, «n di N». Mancano intervalli di pagine/date, spunta e «applica il filtro alla pagina». |
| ANN-36 | P1 | fatto | test_ann36_sort_by_date_desc | Pagina, data ±, autore, tipo, stato con gruppi. |
| ANN-37 | P1 | fatto | test_ann37_38_reply_and_status_from_ui, test_reply_structure_after_save, test_cyclic_irt_chain_lists_all, test_delete_root_removes_replies | |
| ANN-38 | P1 | fatto | test_ann37_38_reply_and_status_from_ui, test_status_* , test_marked_on_then_off | «Cronologia degli stati…» nel menu (test_ann38_history_menu). |
| ANN-39 | P1 | fatto | test_ann39_hide_annotations | Soluzione locale (CORE_REQUESTS 2). |
| ANN-40 | P1 | fatto | test_ann40_flatten_from_ui, test_ann40_flatten_selection, test_flatten_* | Tutto il documento, pagina corrente o selezione; non stampabili esclusi; annullabile. Manca l'ambito «annotazioni filtrate». |
| ANN-41 | P1 | fatto | test_ann41_42_export_import_ui, test_export_xfdf_*, test_export_fdf | XFDF (con aspetto dei timbri) e FDF. |
| ANN-42 | P1 | fatto | test_ann41_42_export_import_ui, test_ann42_import_from_pdf_and_malformed, test_xfdf_* | XFDF, FDF, PDF; duplicati saltati; un passo di annullamento. |
| ANN-43 | P1 | fatto | test_ann43_summary_files, test_ann43_page_layout_numbers, test_summary_* | PDF «solo elenco» o «pagine con commenti numerati» (marcatori e connettori), CSV (riga per risposta con padre), testo, HTML. Manca l'anteprima/stampa diretta. |
| ANN-44 | P2 | non fatto | | |

## Verifica visiva
Catture offscreen nei temi chiaro e scuro (barra strumenti compatta a icone, pannello proprietà,
elenco commenti con risposte e stato, maniglie di selezione e vertici).
