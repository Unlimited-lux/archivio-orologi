# Modulo `objects` — stato (sezione 4, OBJ-*)

Test: `tests/modules/objects/` (circa 300 test, tutti verdi con `QT_QPA_PLATFORM=offscreen`).
File di test abbreviati: `ui` = test_obj_ui.py, `ui2` = test_obj_ui2.py, `eng` = test_obj_engine.py,
`cnt` = test_obj_content.py, `clip` = test_obj_clipboard.py, `lnk` = test_obj_links.py,
`adv` = test_obj_advanced.py, `ext` = test_obj_extract.py, `cap` = test_obj_capture.py,
`img` = test_obj_imaging.py, `chk` = test_obj_linkcheck.py, `geo` = test_obj_geometry.py.

| ID | Pr. | Stato | Test | Note |
|---|---|---|---|---|
| OBJ-01 Modalità modifica oggetti | P0 | parziale | ui: test_mode_and_tool_registered, test_hover_outlines_object; ui2: test_signed_document_confirmation, test_permission_denied_blocks_changes, test_background_parse, test_parser_fuzz_never_crashes; eng/cnt | Immagini, tracciati, sfumature e oggetti composti. Contorno per tipo, analisi in background (20.000 tracciati < 1 s), conferma firme, permesso di modifica. I blocchi di testo sono gestiti dal modulo text_edit e non sono selezionabili qui. Avvisi PDF/A e XFA non fatti. |
| OBJ-02 Selezione | P0 | fatto | ui: click/alt-click/shift/rubber band/Ctrl+A con filtro/Tab/pagina diversa | Gli oggetti nascosti (opacità 0) sono raggiungibili solo con Alt+clic, riquadro o Tab. Livelli /OC nascosti non esclusi. |
| OBJ-03 Pannello proprietà | P1 | fatto | ui: test_properties_panel_width_with_lock, test_properties_panel_ppi_and_mixed_opacity; ui2: test_panel_reference_point_and_skew | X/Y/L/A con lucchetto e griglia a 9 punti di riferimento, angolo, inclinazione (sola lettura), opacità («misto»), ppi, spazio colore, compressione, alfa, peso. Testo alternativo (documenti taggati) non fatto. |
| OBJ-04 Immagini da file | P0 | fatto | ui: test_insert_image_from_file, test_insert_corrupted_png_shows_error; eng (dimensione naturale, EXIF, JPEG senza ricodifica, SMask) | Dimensione da pHYs/JFIF, riduzione al 90 %, cascata 20 pt, metadati EXIF/XMP rimossi, orientamento EXIF applicato con la matrice. Testo alternativo per documenti taggati non fatto. |
| OBJ-05 Dagli appunti | P0 | fatto | ui: test_paste_image_from_clipboard_centered, test_paste_uri_list_inserts_cascade, test_context_menu_paste_as_image_disabled_with_text | |
| OBJ-06 Trascinamento | P0 | fatto | ui: test_drop_images_on_page | Un solo passo annullabile; file non immagine segnalati; i PDF passano alla finestra. |
| OBJ-07 Formati aggiuntivi | P2 | parziale | adv: test_insert_svg_vector_no_image, test_insert_bad_svg | SVG come grafica vettoriale; TIFF/BMP/GIF/WebP/PNM/ICO convertiti senza perdita. HEIC/AVIF/raw no. |
| OBJ-08 Spostare | P0 | parziale | ui: drag, frecce 1/10 pt, Maiusc vincola, Esc annulla, limite 10 pt; ui2: test_link_on_image_moves_with_it; geo | Ritaglio condiviso con altri oggetti non copiato (xfail in eng). |
| OBJ-09 Ridimensionare | P0 | fatto | ui: test_corner_handle_resizes_proportionally, test_shift_corner_resizes_freely; eng | |
| OBJ-10 Ruotare | P0 | fatto | ui: test_rotation_handle_snaps_with_shift, test_rotate_buttons_and_flip; eng (4×90° = identità) | |
| OBJ-11 Capovolgere | P1 | fatto | ui: test_rotate_buttons_and_flip; eng (A\|B → B\|A) | |
| OBJ-12 Inclinare / reimposta | P2 | fatto | adv: test_skew_keeps_height, test_reset_transform_rotated_flipped_image; ui2: test_advanced_actions | Finestra «Trasforma…». |
| OBJ-13 Ritagliare | P0 | fatto | ui: test_crop_by_double_click_and_handles, test_crop_escape_cancels; ui2: test_crop_with_square_ratio, test_ratio_helpers; eng (ripristina, definitivo 200×400 px) | Proporzioni libere, originali, 1:1, 4:3, 3:4, 16:9 (menu Altro). |
| OBJ-14 Sostituire | P0 | fatto | ui: test_replace_image_dialog_flow; eng (Adatta/Riempi/Deforma, ruotata 30°, opacità, tutte le occorrenze, ritaglio conservato) | |
| OBJ-15 Eliminare | P0 | fatto | ui: test_delete_key_and_undo; eng (2 XObject dopo salvataggio, logo condiviso) | Pulizia dell'albero di struttura non fatta. |
| OBJ-16 Opacità | P1 | fatto | ui: test_opacity_slider_single_step; eng (/ca 0.5, pixel 127–128, riuso ExtGState) | Avviso PDF/A non fatto. |
| OBJ-17 Regolazioni immagine | P2 | fatto | img; ui2: test_image_tools_actions | Anteprima in tempo reale non fatta. |
| OBJ-18 Ottimizza immagini | P2 | fatto | img; ui2: test_image_tools_actions, test_optimize_document_images | Immagini selezionate o tutto il documento (un passo annullabile); JPEG/senza perdita; niente JPEG 2000/CCITT. |
| OBJ-19 Applicazione esterna | P2 | fatto | ui2: test_external_edit_roundtrip | |
| OBJ-20 Ordine di sovrapposizione | P1 | fatto | ui: test_reorder_front_shortcut; eng | |
| OBJ-21 Allineare | P1 | fatto | ui: test_arrange_actions; eng | Riferimento «Oggetto chiave» solo nel motore. |
| OBJ-22 Distribuire | P1 | fatto | eng | |
| OBJ-23 Guide e griglia | P1 | fatto | ui2: test_smart_guides_snap_to_other_object, test_snap_delta_pure, test_grid_snap, test_user_guides_snap_and_persist | Guide intelligenti (Alt sospende), griglia con passo e aggancio, guide del nucleo (VIS-39: righelli, trascinamento, salvate per documento) usate per disegno e aggancio tramite `page_guide_lines`; «Aggiungi guida…» da menu; migrazione delle guide salvate dal modulo (ui2: test_old_guides_migrated_to_core). |
| OBJ-24 Tracciati vettoriali | P1 | fatto | eng; ui (riquadro, Canc) | «Seleziona simili». |
| OBJ-25 Stile dei tracciati | P2 | fatto | adv: test_restyle_cmyk_fill_red, test_restyle_width_and_dash_and_no_fill | |
| OBJ-26 Scomporre / raggruppare | P2 | fatto | adv: test_decompose_form_same_render, test_decompose_transparency_group_needs_confirmation, test_group_move_and_ungroup | Modalità isolamento non fatta. |
| OBJ-27 Estrarre l'immagine | P0 | fatto | ui: test_save_image_original_jpeg, test_copy_image_puts_png_on_clipboard; ui2: test_permission_denied_blocks_changes; eng | «Salva immagine come appare…» (ritaglio, rotazione, opacità applicati): ui2 test_save_image_as_shown_cropped_rotated. |
| OBJ-28 Estrarre tutte le immagini | P1 | fatto | ext; ui2: test_extract_all_flow, test_extract_all_no_images | |
| OBJ-29 Cattura area | P1 | non fatto qui | cap (logica) | Nella specifica v2 è del visualizzatore (VIS-29): lo strumento non è esposto nell'interfaccia. |
| OBJ-30 Copia/taglia/incolla/duplica | P0 | fatto | ui: copy/paste tra pagine e documenti, scostamento +10, duplica, taglia; clip | Ctrl+D e Ctrl+Maiusc+V tolti (specifica v2): «Duplica» e «Incolla in posizione» nel menu. Strutture taggate non aggiornate. |
| OBJ-31 Spostare tra pagine | P1 | fatto | ui: test_move_to_page_and_undo; clip | Anche trascinando su un'altra pagina. |
| OBJ-32 Replicare su più pagine | P2 | fatto | adv: test_replicate_logo_all_pages_single_data, test_replicate_skips_different_size | |
| OBJ-33 Creare collegamento | P0 | fatto | ui: test_link_tool_creates_link, test_link_tool_cancel_dialog_no_change; lnk; geo | Elementi di struttura /Link non creati. |
| OBJ-34 Collegamento dal testo | P1 | fatto | ui: test_create_link_from_text_selection; lnk | Selezione su più pagine: la selezione del nucleo è per pagina. |
| OBJ-35 Destinazione web/e-mail | P0 | fatto | lnk (normalizzazione, punycode, javascript rifiutato, mailto) | |
| OBJ-36 Pagina interna / con nome | P0 | fatto | ui: test_link_double_click_edits_to_page; lnk | Creazione di nuove destinazioni con nome non fatta. |
| OBJ-37 File esterno | P1 | fatto | lnk; ui2: test_decide_rules | GoToR con percorso relativo; /Launch mai eseguito. |
| OBJ-38 Modificare collegamenti | P0 | fatto | ui: test_link_tool_select_move_delete_undo; lnk (JavaScript intatto, QuadPoints traslati) | |
| OBJ-39 Eliminare collegamenti | P0 | fatto | ui: test_delete_links_dialog; lnk | |
| OBJ-40 URL automatici | P1 | fatto | ui: test_detect_urls_flow_single_undo; lnk | Collegamenti al volo in lettura: del visualizzatore. |
| OBJ-41 Attivazione e sicurezza | P0 | parziale | ui: test_ctrl_click_follows_link_in_object_tool; ui2: test_decide_rules, test_launch_link_blocked | Negli strumenti di modifica il clic seleziona, Ctrl+clic segue con la conferma unica del nucleo (`follow_link`, VIS-19). `linkopen.py` (siti consentiti, testo diverso dal dominio) resta come logica testata. |
| OBJ-42 Verifica collegamenti | P2 | fatto | chk | Pannello laterale «Collegamenti». |
| OBJ-43 Annulla/ripeti | P0 | fatto | ui: test_five_operations_undo_redo; eng | Gestito dal nucleo (`Document.change`). |

Note:
- Le posizioni sono calcolate direttamente dal content stream con le matrici della pagina e sono verificate su pagine ruotate e con CropBox spostato (test_obj_geometry.py); per questo il modulo non usa `core/placement.py`.
- Un solo test resta `xfail`: incolla in altro documento senza opacità con differenze di ±1 livello (OBJ-30, causa non isolata).
