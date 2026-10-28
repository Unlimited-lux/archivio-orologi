# Stato del modulo «Commenta» (sezione 5 della specifica, ANN-*)

Struttura: `engine.py` (logica pura sul motore PDF, senza Qt), `controller.py` (stili, autore,
selezione, operazioni annullabili), `tools.py` (strumenti della vista), `panels.py` (pannello
proprietà a destra, elenco commenti a sinistra), `dialogs.py` (finestre, sostituibili nei test),
`ui_actions.py` (comandi con file: esporta/importa, riepilogo, appiattimento), `visibility.py`
(mostra/nascondi e filtro in pagina tramite le API della vista del nucleo), `i18n_en.py` (traduzioni inglesi). Ogni modifica passa da `document.change()`.

Test: `tests/modules/annotations/` — `test_p0_create.py`, `test_p0_tools.py` (P0 dall'interfaccia),
`test_p1_ui.py` (P1 dall'interfaccia), `test_engine_p1.py` (motore), `test_i18n.py` (traduzioni).

| ID | Priorità | Stato | Test | Note |
|---|---|---|---|---|
| ANN-01 | P0 | fatto | test_ann01_every_type_saved_with_ap_nm_and_m, test_ann01_unhandled_annotation_untouched, test_ann01_private_key_kept_after_color_change, test_pdfa2b_shape_and_reply, test_pdfa1_opacity_forced_with_warning, test_tagged_note_in_structure | /NM UUID, /M, /AP /N per ogni tipo; tipi sconosciuti e chiavi private conservati; permesso «commenti» rispettato. Commento su PDF firmato: firma ancora valida (salvataggio incrementale del nucleo, test_comment_keeps_signature_valid). PDF/A dichiarato: flag Print, niente Hidden/Invisible/NoView, /AP con la sola /N (risposte e stati con aspetto vuoto), opacità 1 per PDF/A-1 con avviso. Documento taggato: elemento /Annot con /Alt nella struttura logica, /StructParent, /Tabs /S; elementi tolti quando la nota è eliminata (engine.conform). |
| ANN-02 | P0 | fatto | test_ann02_author_subject_dates, test_ann02_non_latin_author_utf16, test_ann02_empty_author_restores_account_name, test_ann02_tolerant_dates | Autore predefinito = nome completo dell'account; conferma al primo uso (messaggio); «Nome autore…» nel menu Commenti. Nome da `ctx.author_name()` del nucleo; la vecchia chiave `annotations/author` viene migrata (test_ann02_legacy_author_migrated). |
| ANN-03 | P0 | fatto | test_ann03_* (11), test_ann20_drag_vertex | Selezione (clic, Maiusc/Ctrl, rettangolo), spostamento (trascinamento, frecce 1/10 pt), maniglie, vertici, primo piano/sfondo, Canc, bloccate, rotazione di timbri, rettangoli, ellissi e caselle di testo (test_ann03_rotate_shapes_and_textbox), allinea/distribuisci, mini-barra (test_ann03_minibar). |
| ANN-04 | P0 | fatto | test_ann04_* (5) | Pannello proprietà: colore, riempimento/nessuno, opacità, spessore 0–12 a passi 0,25, tratteggio, nuvola, estremità, testo (dimensione, colore, allineamento), icona note; un passo di annullamento per selezione multipla. Colori recenti/esadecimale libero tramite «Altro colore…». |
| ANN-05 | P0 | fatto | test_ann05_comment_on_highlight_has_popup, test_ann05_rich_text_bold, test_ann05_long_comment_preserved, test_ann05_popup_left_open_is_saved | /Contents, /Popup collegata, /RC (grassetto, corsivo, sottolineato, colore), suggerimento al passaggio. Doppio clic apre l'editor del commento (intestazione autore/data); «Apri/chiudi finestra del commento» mostra la popup sulla pagina e salva /Open. La popup sulla pagina non si trascina né si ridimensiona. |
| ANN-06 | P0 | fatto | test_ann06_undo_redo_sequence, test_ann06_undo_after_save_marks_modified, test_ann23_undo_restores_inklist, test_ann41_42_export_import_ui | Tutto tramite `document.change()` (cronologia del nucleo). |
| ANN-07 | P1 | fatto | test_ann07_* (6) | Ctrl+C/X/V/D; taglia+incolla con risposte e stati; incolla in un altro documento aperto con aspetto e risorse (test_ann07_paste_into_other_document); testo e immagini dagli appunti di sistema. |
| ANN-08 | P1 | fatto | test_ann08_default_style_survives_restart, test_ann08_keep_tool_three_rects, test_styles_corrupted_profile_falls_back | Stile per strumento persistente, «Imposta come predefinito», preferiti con nome (pannello proprietà), esporta/importa stili (JSON con versione), «Mantieni strumento». |
| ANN-09 | P1 | fatto | test_ann09_flags, test_ann09_locked_contents, test_ann03_locked_not_moved_or_deleted | Bloccata, Contenuto bloccato, Sola lettura, Stampa; Print attivo sui nuovi, NoZoom/NoRotate su note e allegati. |
| ANN-10 | P0 | fatto | test_ann10_* (6) | Trascinamento o selezione + comando/menu contestuale; quadrilateri per riga; fusione moltiplicativa; pagina senza testo → area con avviso OCR. Selezione su più pagine (`view.text_selections`): un'annotazione per pagina in un solo passo annullabile. |
| ANN-11 | P1 | fatto | test_ann11_area_highlight, test_ann10_image_page_area_highlight | Strumento «Evidenzia area» o Alt+trascinamento; /Rect = quadrilatero, ridimensionabile. |
| ANN-12 | P0 | fatto | test_ann12_three_markups_same_quads, test_ann12_line_positions | Stessa selezione per sottolineato/barrato/ondulato (multipagina: vedi ANN-10). |
| ANN-13 | P1 | fatto | test_ann13_insert_and_replace | Inserisci testo/paragrafo (/Caret /Sy), Sostituisci (barrato /IRT /RT /Group); voce unica «Sostituzione»; eliminazione di gruppo. |
| ANN-14 | P0 | fatto | test_ann14_note_click, test_ann14_note_icon_and_color, test_ann14_note_cancel_creates_nothing, test_note_icon_under_click_on_rotated_page | Il motore normalizza l'icona a 16 pt (lato richiesto 20): l'icona ha dimensione costante a schermo (NoZoom). Su pagine ruotate /Rect è traslato perché l'icona diritta (/NoRotate) cada sotto il clic; selezione sul riquadro visualizzato. |
| ANN-15 | P1 | fatto | test_ann15_typewriter | Senza bordo, rettangolo adattato; testo vuoto → nessuna annotazione. Caratteri CJK: aspetto del motore. |
| ANN-16 | P0 | fatto | test_ann16_textbox, test_ann16_click_creates_default_width_box, test_ann16_rotated_page_text_horizontal | Aspetto vettoriale proprio (bordo e sfondo di colori distinti, a capo, allineamento, margine). Adattamento automatico dell'altezza (test_ann16_autofit_height); grassetto/corsivo/sottolineato/colore su porzioni di testo in /RC e nell'aspetto (test_ann16_rich_text_in_textbox). |
| ANN-17 | P1 | fatto | test_ann17_callout, test_ann17_move_tip_only, test_callout_with_cropbox* | Punta, gomito, casella; punta spostabile da sola. Corretto con /CropBox diverso dal /MediaBox (anche importando). |
| ANN-18 | P0 | fatto | test_ann18_* (4) | Maiusc quadrato/cerchio, Alt dal centro. |
| ANN-19 | P0 | fatto | test_ann19_arrow, test_ann19_shift_snaps_45, test_ann19_caption | Estremità modificabili, didascalia (/Cap). |
| ANN-20 | P1 | fatto | test_ann20_polygon_tool, test_ann20_drag_vertex, test_polygon_vertices, test_polyline_vertices | Doppio clic/Invio/clic sul primo vertice, Backspace, Esc; vertici trascinabili, aggiunti e rimossi dal menu (test_ann20_add_remove_vertex_and_cloud_intensity). |
| ANN-21 | P1 | fatto | test_ann21_cloud_tool, test_cloud | /IT /PolygonCloud, /BE /S /C; «Bordo a nuvola» per rettangoli, ellissi e poligoni, intensità 0–2 dal menu contestuale. |
| ANN-22 | P0 | fatto | test_ann22_three_strokes_one_ink, test_ann22_simplification, test_ann22_marker_transparent | Tratti entro 2 s = una annotazione; semplificazione ≤ 0,5 pt e ≤ 2000 punti; pennarello con fusione moltiplicativa. |
| ANN-23 | P0 | fatto | test_ann23_partial_eraser, test_ann23_whole_stroke_and_others_untouched, test_ann23_undo_restores_inklist | Parziale e «tratti interi», dimensione regolabile; una passata = un passo. Anteprima della cancellazione durante il trascinamento, applicata al rilascio. |
| ANN-24 | P1 | fatto | test_ann24_25_stamp_tools, test_preset_stamp | 20 timbri originali, /Name standard o personalizzato, aspetto vettoriale. |
| ANN-25 | P1 | fatto | test_ann24_25_stamp_tools, test_ann25_counter_never_decrements, test_fill_template, test_dynamic_stamp_frozen_value | Segnaposto {autore} {data} {ora} {file} {pagina} {contatore}; contatore persistente; modelli personalizzati con avviso sui segnaposto sconosciuti (test_ann25_custom_dynamic_template). |
| ANN-26 | P1 | fatto | test_ann26_* (4), test_image_stamp | Timbri da immagine, da pagina PDF (vettoriali) e da istantanea di un'area (/IT /StampSnapshot) in libreria «Personalizzati»; esporta/importa libreria. |
| ANN-27 | P1 (spec. v2) | fatto | test_ann27_sticker, test_ann27_every_sticker_by_category, test_ann27_resize_below_8pt_is_clamped, test_ann27_custom_sticker_from_library | 19 adesivi vettoriali originali in 5 categorie (spunte e croci, simboli di stato, frecce, faccine e gesti, stelle e cuori), incorporati nell'aspetto; ruotabili; lato minimo 8 pt; adesivi personalizzati dalla libreria «Personalizzati» (/Subj «Adesivo», /IT /StampImage). |
| ANN-28 | P1 (spec. v2) | fatto | test_ann28_generated_image_applied_and_saved_in_library[stamp,sticker], test_ann28_unavailable_and_hidden | «Genera con IA…» nei menu Timbro e Adesivi: dialogo di generazione del modulo IA (AI-25) con il fornitore configurato, informativa (AI-02) prima di ogni invio, proposta apposta con un clic e salvata nella libreria con origine «ia»; senza fornitore messaggio di indisponibilità; nascosto con «Funzioni IA» disattivate. |
| ANN-29 | P1 | fatto | test_ann29_attachment_tool, test_attachment_round_trip | Doppio clic → solo «Salva allegato con nome» (nessuna esecuzione). PDF/A-1: allegato impedito (test_ann29_pdfa1_blocks_attachments). |
| ANN-30 | P1 | fatto | test_ann30_calibrate_tool, test_ann30_scale_per_page, test_ann30_document_viewport_scale, test_scale_calibrate | Rapporto o calibrazione, pagina corrente o tutte, aree /VP del documento usate senza calibrazione, scala in barra di stato. |
| ANN-31 | P1 | fatto | test_ann31_distance_tool, test_ann31_measure_updates_when_vertex_moves, test_distance_measure | |
| ANN-32 | P1 | fatto | test_ann32_area_measure_tool, test_area_measure, test_perimeter_measure | |
| ANN-33 | P2 | fatto | test_ann33_measures_csv, test_snap_targets_engine, test_distance_snaps_to_vector_endpoint, test_crosshair_shows_real_coordinates, test_snap_kinds_setting, test_angle_measure, test_convert_polyline_and_rect_to_measure, test_csv_three_measures_with_separator | Aggancio a estremi, punti medi, intersezioni, tracciati e vertici delle annotazioni (tipi nel menu Misura › Aggancio; calcolo in un thread, intersezioni con griglia); mirino e coordinate in unità reali; angolo (polilinea di 3 vertici con /T); «Converti in misura» per linee, polilinee, poligoni e rettangoli, con richiesta di calibrazione se manca la scala; CSV con separatore a scelta. |
| ANN-34 | P0 | fatto | test_ann34_* (4) | Gruppi per pagina, autore/data, testo marcato, stato, risposte, spunta, menu contestuale, doppio clic, contatore. |
| ANN-35 | P1 | fatto | test_ann35_* (5) | Tipo, autore, colore, stato, spunta, risposte, pagine, data di modifica, ricerca senza maiuscole/accenti, «n di N», «Applica il filtro alla pagina» (solo nella vista, /F invariati; `view.set_annotation_filter` del nucleo, valido anche a tasselle). |
| ANN-36 | P1 | fatto | test_ann36_sort_by_date_desc, test_comments_sorted_top_down_on_rotated_page | Pagina, data ±, autore, tipo, stato con gruppi. Ordine dall'alto in basso nella vista anche su pagine ruotate. |
| ANN-37 | P1 | fatto | test_ann37_38_reply_and_status_from_ui, test_reply_structure_after_save, test_cyclic_irt_chain_lists_all, test_delete_root_removes_replies | |
| ANN-38 | P1 | fatto | test_ann37_38_reply_and_status_from_ui, test_status_* , test_marked_on_then_off | «Cronologia degli stati…» nel menu (test_ann38_history_menu). |
| ANN-39 | P1 | fatto | test_ann39_hide_annotations | `view.show_annotations` del nucleo. |
| ANN-40 | P1 | fatto | test_ann40_flatten_from_ui, test_ann40_flatten_selection, test_flatten_* | Tutto il documento, pagina corrente o selezione; non stampabili esclusi; annullabile. Manca l'ambito «annotazioni filtrate». |
| ANN-41 | P1 | fatto | test_ann41_42_export_import_ui, test_export_xfdf_*, test_export_fdf, test_fdf_keeps_replies_and_status, test_freetext_roundtrip_*, test_note_roundtrip_* | XFDF (con aspetto dei timbri) e FDF. FDF con risposte e stati; casella di testo con colore del bordo e /Rect esatti; /Rect del file esportato tal quale. |
| ANN-42 | P1 | fatto | test_ann41_42_export_import_ui, test_ann42_import_from_pdf_and_malformed, test_xfdf_* | XFDF, FDF, PDF; duplicati saltati; un passo di annullamento. |
| ANN-43 | P1 | fatto | test_ann43_summary_files, test_ann43_page_layout_numbers, test_summary_* | PDF «solo elenco» o «pagine con commenti numerati» (marcatori e connettori), CSV (riga per risposta con padre), testo, HTML. Manca l'anteprima/stampa diretta. |
| ANN-44 | P2 | fatto | test_misspelled_word_flagged_with_suggestion, test_personal_dictionary_shared_between_documents, test_toggle_and_language_without_dictionary, test_saved_appearance_identical_with_and_without_check | Sottolineatura ondulata delle parole sconosciute in editor del commento (note, caselle, richiami) e testi su più righe (risposte, commenti di testo, inserimenti); menu con correzioni, «Aggiungi al dizionario», «Ignora», lingua del testo e interruttore. Dizionari e dizionario personale del servizio condiviso del modulo text_edit (pyspellchecker, DEPS.txt); lingua senza dizionario → controllo spento con indicazione; nulla nel PDF. |

## Verifica visiva
Catture offscreen nei temi chiaro e scuro (barra strumenti compatta a icone, pannello proprietà,
elenco commenti con risposte e stato, maniglie di selezione e vertici).

## Note finali
- Specifica v2 applicata: nessuna conferma per le operazioni annullabili (eliminazione di discussioni,
  «Elimina tutti», appiattimento); scorciatoie del modulo limitate alla modalità Commenta.
- Coordinate convertite con `core/placement.py` (CropBox diverso dal MediaBox, pagine ruotate):
  test_placement.py::test_reread_rotated_crop_larger_than_media.
- Prestazioni: ridisegno, informazioni e hit test lineari (test_performance.py: 2000 annotazioni, ridisegno < 300 ms).
- Interfaccia inglese (NFR-25): test_english_ui.py costruisce la finestra in italiano e in inglese e confronta
  i testi di barra, menu, pannelli, mini barra, menu contestuale, menu principale e dialoghi, e i testi scritti
  nei documenti (soggetto, stato, timbri, riepiloghi TXT/CSV/HTML/PDF, CSV delle misure), che seguono la lingua
  dell'interfaccia. I modelli dei timbri dinamici accettano anche i segnaposto inglesi
  ({author} {name} {date} {time} {file} {page} {counter}).
