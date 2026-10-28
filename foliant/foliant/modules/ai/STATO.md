# Stato del modulo `ai` — Assistente IA (sezione 12)

Modalità `ia` («Assistente IA», icona `sparkles`, ordine 110, scorciatoia Ctrl+Alt+I) con
pannello conversazione a destra. Fornitori: Anthropic (SDK `anthropic`, modelli
`claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`) ed endpoint compatibile
OpenAI (anche locale) via HTTP. Chiavi nel portachiavi di sistema (`keyring`), mai su
disco. Nessuna funzione P0 nella sezione 12. Priorità aggiornate dall'orchestratore: AI-19,
AI-20, AI-21, AI-22, AI-23, AI-25 e AI-26 sono P1. Tutte le funzioni sono implementate
(AI-23 in parte, vedi nota).

Tutti i test usano un fornitore simulato (`tests/modules/ai/conftest.py::FakeProvider`)
o un server HTTP su 127.0.0.1: nessuna chiamata di rete esterna.

| ID | Priorità | Stato | Test principali | Note |
|---|---|---|---|---|
| AI-01 Fornitore e chiave | P1 | fatto | `test_panel.py`: `test_disabled_until_configured`, `test_key_never_written_to_files`, `test_verify_models_and_auth_failure`, `test_session_only_key_without_keyring`, `test_delete_profile_removes_keyring_entry`; `test_engine.py::test_local_usage_limit`; `test_providers.py::test_local_and_insecure_urls` | Profili multipli (aggiungi, duplica, elimina), catalogo + endpoint personalizzato, verifica connessione in background, avviso endpoint non cifrato, interruttore «Funzioni IA», limite di utilizzo locale. Capacità (immagini, vettori, generazione) impostate a mano. |
| AI-02 Informativa e trasparenza | P1 | fatto | `test_consent_shown_before_any_request`, `test_extraction_forbidden_disables_commands`, `test_send_line_and_preview_flag_redaction`, `test_send_log_entry_after_summary`, `test_confirm_each_send`; `test_engine.py`: `test_no_file_name_or_metadata_sent`, `test_redaction_mark_excludes_text`, `test_hidden_text_excluded_but_ocr_layer_included`, `test_extraction_forbidden_blocks`, `test_send_log_has_no_content`, `test_consent_required` | Riga «Verrà inviato» + anteprima integrale; registro locale (consulta, esporta, cancella); revoca del consenso. «Conferma prima di ogni invio» solo globale (non per documento). |
| AI-03 Pannello, ambito, risposte | P1 | fatto | `test_remote_image_not_loaded`, `test_page_label_reference_click`, `test_selection_fixed_at_send`, `test_insert_note_single_undo`, `test_general_chat_without_document`, `test_copy_formatted_and_dropped_image`, `test_render_dark_theme` | Ambiti documento/pagine/selezione/area/nessuno; Markdown sicuro (nessuna risorsa remota, link solo dopo conferma); copia semplice+formattata, nota, casella, salva, rigenera; menu contestuale della selezione. Annuncio vocale del completamento non specifico. |
| AI-04 Chat e riferimenti | P1 | fatto | `test_engine.py`: `test_chat_contains_all_pages_with_markers`, `test_chat_history_resent_and_reset`, `test_references_verified_and_unverified`, `test_answer_without_refs_flagged`, `test_label_reference_resolves_physical_page`; `test_panel.py`: `test_page_label_reference_click`, `test_document_change_warns` | Clic sul riferimento → pagina ed evidenziazione della citazione per 3 s. |
| AI-05 Riassunto | P1 | fatto | `test_summary_of_pasted_text`, `test_summary_presets_in_instructions`, `test_invalid_range_no_request`, `test_summary_inserted_as_first_page`, `test_no_file_name_or_metadata_sent`, `test_excluded_pages_marked_partial` | Anche testo incollato (chat generale) e più documenti aperti («Più documenti › Riassumi la raccolta»). Avviso selezione < 30 parole, lunghezza effettiva segnalata se superiore. |
| AI-06 Traduzione selezione/pagine | P1 | fatto | `test_translate_pages_sections`, `test_translate_selection_note_anchored`, `test_translate_selection_replace_in_document`, `test_translate_warns_when_already_target_language` | 28 lingue, glossario («termine=traduzione; termine=-»), sostituzione nel documento con gli stessi controlli di AI-09. Rilevamento lingua euristico locale. |
| AI-07 Traduzione in nuovo PDF | P1 | fatto | `test_translate_pdf.py` (15 test), `test_panel.py`: `test_translate_pdf_controller`, `test_translate_pdf_cancel_creates_no_file`, `test_translate_pdf_retry_failed_page` | Impaginazione mantenuta, bilingue, solo testo; rapporto; «Ritenta pagine non riuscite». Le pagine scansionate restano non tradotte (con avviso): serve prima l'OCR. |
| AI-08 Spiegazione | P1 | fatto | `test_explain_sends_term_paragraph_and_level`, `test_explain_note_at_selection` | |
| AI-09 Riscrittura e correzione | P1 | fatto | `test_rewrite_accept_replaces_text_single_undo`, `test_rewrite_discard_keeps_document`, `test_rewrite_on_scanned_page_offers_annotation`, `test_rewrite_signed_document_asks`, `test_rewrite_obsolete_if_text_changed`; `test_engine.py::test_rewrite_proposal_and_diff` | Differenze parola per parola, proposta ritoccabile, stesso carattere/corpo/colore; alternativa non distruttiva (barrato + testo proposto). La sostituzione è implementata nel modulo (non dipende dal modulo Modifica testo). |
| AI-10 Estrazione dati | P2 | fatto | `test_extract.py` (25 test), `test_integration.py::test_extract_controller_opens_grid` | Tabelle, fattura, schema personalizzato (API); griglia modificabile, CSV/JSON. Schema personalizzato senza editor grafico dedicato. |
| AI-11 Immagini e aree | P1 | fatto | `test_area_scaled_to_2048`, `test_tiny_area_rejected`, `test_area_without_image_capability_uses_local_ocr`, `test_copy_formatted_and_dropped_image` | Strumento «Seleziona area», clic destro su immagine, file o trascinamento; oscuramenti resi coprenti. |
| AI-12 Mappa e scaletta | P1 | fatto | `test_mindmap.py` (11 test), `test_mindmap_open_click_insert`, `test_mindmap_export_json_after_rename` | Vista mappa/scaletta, modifica nodi, PNG/SVG/PDF/Markdown/JSON, inserimento all'inizio o alla fine. Suddivisione su più pagine non implementata (corpo minimo 4 pt). |
| AI-13 Più documenti | P2 | fatto | `test_integration.py`: `test_collection_chat_and_reference`, `test_collection_excludes_forbidden` | Raccolte salvabili con conversazioni; riferimenti «B › p. 2» aprono il documento. |
| AI-14 Documenti lunghi | P1 | fatto | `test_long_document_chunked_within_window`, `test_index_cache_reused_until_change`, `test_heavy_summary_requires_confirmation` | Blocchi per pagina/paragrafo/frase, scelta BM25 locale, riassunti a più livelli, cache su disco per impronta. |
| AI-15 Scansioni e OCR | P1 | fatto | `test_textless_pages_detected`, `test_textless_pages_prompt_before_requests`, `test_ocr_text_used_without_modifying_document`, `test_ocr_temporary_then_saved`, `test_images_option_hidden_without_capability` | OCR con `foliant.modules.ocr.engine` se presente, altrimenti `get_textpage_ocr`; salvataggio facoltativo del livello di testo (annullabile). Affidabilità OCR disponibile solo dal modulo OCR. |
| AI-16 Errori | P1 | fatto | `test_engine.py`: `test_rate_limit_retry_after`, `test_rate_limit_waits_real_time`, `test_auth_error_not_retried_and_key_masked`, `test_server_error_retried_three_times`, `test_partial_text_kept_on_network_loss`; `test_providers.py`: `test_http_errors_classified`, `test_network_unreachable`, `test_anthropic_error_mapping`; `test_auth_error_message_with_settings_link` | Fino a 3 nuovi tentativi, attesa indicata dal fornitore (max 60 s) con conto alla rovescia; «Continua» per risposte troncate. |
| AI-17 Avanzamento e annullamento | P1 | fatto (con nota) | `test_cancel_during_stream`, `test_stop_streaming`, `test_cancel_closes_connection_quickly`, `test_ui_responsive_during_answer`, `test_translate_pdf_cancel_creates_no_file`, `test_operations_dialog_lists_and_cancels` | Esc/«Interrompi», elenco operazioni in corso. Conferma alla chiusura della scheda: richiede il nucleo (CORE_REQUESTS.md); oggi le operazioni del documento chiuso vengono annullate. |
| AI-18 Cronologia | P1 | fatto | `test_history_persisted_and_reopened`, `test_history_disabled_writes_nothing`, `test_history_delete_all_and_expiry`, `test_history_export`, `test_history_encrypted_with_keyring_key` | Per impronta del contenuto (sopravvive a rinomina), ricerca per testo e funzione, esporta MD/JSON, scadenza, file danneggiati isolati, cifratura facoltativa. |
| AI-19 Scrittura assistita | P1 | fatto | `test_bookmarks_writer.py`, `test_integration.py::test_writer_new_pdf_and_insert` | |
| AI-20 Modelli di attività | P1 | fatto | `test_integration.py`: `test_business_templates_build_instructions`, `test_product_sheet_runs_on_document`, `test_quiz_template_export_with_solutions`, `test_legal_template_has_disclaimer`, `test_cv_template_excludes_personal_data`, `test_custom_task_roundtrip_same_instructions`, `test_invalid_task_import_rejected`, `test_tasks_dialog` | 19 attività predefinite, tra cui un modello generico configurabile e i casi aziendali: scheda di un contratto, controllo di fatture e ricevute, scheda prodotto (annuncio), comunicazione al cliente, confronto di documenti; personali: duplica, elimina, importa/esporta JSON. Creazione da zero tramite duplicazione + file JSON (senza editor dedicato). |
| AI-21 Ricerca per significato | P1 | fatto | `test_semantic_search_with_vectors`, `test_semantic_search_chat_fallback` | Vettori via `/embeddings` (endpoint compatibile OpenAI) o ripiego sul modello di chat. Accessibile dalla modalità IA (non dalla barra di ricerca del visualizzatore, che è di un altro modulo). |
| AI-22 Segnalibri e riassunto per sezione | P1 | fatto | `test_bookmarks_writer.py`, `test_integration.py`: `test_summarize_section_sends_section_pages`, `test_generate_bookmarks_controller` | «Riassumi sezione» usa il segnalibro che contiene la pagina corrente (il pannello Segnalibri è di un altro modulo). |
| AI-23 Assistente comandi | P1 | parziale | `test_command_proposal_validated_and_no_content_sent`, `test_command_out_of_range_rejected`, `test_command_catalog_contains_app_actions` | Catalogo = azioni delle modalità registrate + strumenti propri con parametri; proposte convalidate. Gli strumenti degli altri moduli si aprono senza parametri precompilati (serve un'API comune). |
| AI-24 Analisi e pulizia pagine | P2 | fatto | `test_page_analysis.py` (10 test), `test_integration.py::test_analyze_pages_without_profile` | Controlli locali senza IA; classificazione IA facoltativa; un solo «Annulla». |
| AI-25 Elementi grafici | P1 | fatto | `test_graphics_convert.py` (15 test), `test_integration.py::test_generate_graphic_disabled_without_image_model` | Generazione via `/images/generations` (endpoint compatibile OpenAI con «Modello per immagini»), 1–4 varianti, libreria locale, rimozione locale dello sfondo uniforme, timbro testuale vettoriale. L'elemento si applica come timbro sulla pagina corrente, filigrana o sfondo (un solo «Annulla»); gli strumenti Filigrana/Sfondo/Timbri degli altri moduli non sono collegati. |
| AI-26 Ricerca bibliografica | P1 | fatto | `test_biblio.py` (15 test), `test_integration.py::test_bibliography_dialog_disabled_without_service` | Servizi OpenAlex o Crossref configurabili (solo parole chiave inviate), sotto-domande, libreria locale, revisione con bibliografia solo da voci reali e citazioni inesistenti «non verificata», PDF/BibTeX/RIS. |
| AI-27 Conversione assistita | P2 | fatto | `test_graphics_convert.py`, `test_integration.py::test_convert_to_docx_with_heading` | API `convert_assist.classify`/`to_docx` per il modulo Conversione; nel modulo IA il comando «Converti in Word con l'IA». Solo blocchi con posizione e stile vengono inviati; errori di rete → conversione standard con rapporto. |

Traduzioni inglesi: `translations.py`, verificate da `test_translations.py` (ogni stringa
`_()` del modulo ha la traduzione e gli stessi segnaposto).

Verifica visiva: catture offscreen del pannello (chat con riferimenti, riscrittura con
differenze, mappa concettuale, impostazioni) nei temi chiaro e scuro.

## Revisione indipendente e correzioni

Una revisione in sola lettura del modulo ha segnalato 19 punti; corretti e coperti da test:
permessi di estrazione valutati sul file originale anche dopo un annulla
(`test_extraction_ban_survives_undo`), OCR che esclude il testo sotto le marcature di
oscuramento (`test_ocr_excludes_text_under_redaction`), testo invisibile considerato strato
OCR solo sopra una scansione, traduzione in nuovo PDF da una copia sanificata
(`test_sanitized_copy_drops_hidden_and_redacted`), nessun nome di file inviato nella chat su
più documenti (`test_collection_does_not_send_file_names`), la riscrittura non applica le altre
marcature di oscuramento (`test_rewrite_keeps_other_redaction_marks`), cronologia cifrata con
portachiavi bloccato non isolata (`test_locked_history_not_isolated`), portachiavi non
interrogato all'avvio (`test_keyring_not_queried_at_startup`), profilo risolto nel thread
dell'interfaccia, inserimenti sulla pagina giusta con errori gestiti, «Continua» solo per la
chat, conferma prima dell'invio anche per attività e comandi del controller, riferimenti di
pagina risolti per testo, guardia contro cicli infiniti nei riassunti a blocchi, chiusura della
chat generale che annulla l'operazione, collegamenti solo http/https/mailto.

Limiti noti: l'OCR tiene il lock del motore per la durata del riconoscimento (il motore non
è thread-safe); il limite di operazioni parallele per profilo non è imposto; con il fornitore
Anthropic l'interruzione è immediata solo all'arrivo del frammento successivo (al più il tempo
di inattività configurato).

## Specifica v2 (commit c8fddf4) — non completata entro la scadenza

La specifica v2 è arrivata dopo il completamento delle funzioni sopra. Rispetto a essa:

| ID | Stato | Note |
|---|---|---|
| AI-20 (v2) | fatto | Modello generico configurabile, import/esport JSON e attività predefinite presenti (quiz, analisi di articolo, bilancio, flussi di cassa, curriculum, piano d'impresa, analisi strategica, video, matematica…). Sezioni fisse nell'ordine del modello con «da completare» per piano d'impresa, contenuti per video, articolo scientifico, flussi di cassa, analisi strategica (`test_business_plan_fixed_sections`). Segnaposto `{parametro:nome}` e `{pagine}` reso come «pagine 2–4» (`test_generic_template_pages_placeholder`). Passaggi numerati con risultato evidenziato per «Problemi di matematica» e «Aiuto per i compiti» (`test_math_steps_numbered_with_result`); valori numerici citati e assenti dal documento marcati «non verificato» per riassunto tecnico, bilancio e flussi di cassa (`test_technical_summary_marks_unverified_numbers`). |
| AI-28 Contratti | parziale | Solo il modello «Scheda di un contratto» e «Analisi dei rischi di un contratto» / «Bozza di contratto» come attività testuali; mancano tabella delle clausole, «Aggiungi come commenti», fonti «da verificare». |
| AI-29 Fatture e ricevute | fatto | Coperto da AI-10 (estrazione fattura, somma righe con la differenza, imponibile + imposta = totale, partita IVA e codice fiscale formalmente validi, «non verificato», CSV/JSON) ; fattura elettronica XML (FatturaPA) allegata al PDF letta in locale, senza richieste al fornitore — `test_invoice_checks.py`, `test_extract.py`. Più PDF: «Estrai dati › Fatture da più file», un record per fattura con il file d'origine (solo locale), tabella riepilogativa CSV (`test_three_invoice_files_csv_with_source`). |
| AI-30 Schede prodotto | fatto | «Altri strumenti › Scheda prodotto»: sezioni del modello, specifiche con nome, valore, unità e pagina d'origine (modificabili), «non verificato» per i valori assenti, limite di 300 caratteri con conteggio, unità diverse segnalate, CSV delle specifiche, nuovo PDF, copia — `test_product_sheet.py`. |
| AI-31 Comunicazioni ai clienti | parziale | Attività «Comunicazione al cliente» e scrittura assistita (AI-19, con «Crea nuovo PDF»); dati del destinatario mai inventati: segnaposto «[…]» evidenziati e segnalati «da completare» (`test_customer_message_placeholders_highlighted`). Manca l'avvio dal risultato di una revisione di contratto. |
| AI-32 Confronto e classifica | fatto | `test_ranking.py`: totali 7,50/7,25/6,25 e ordine, obbligatorio a 0 escluso in fondo, CSV una riga per documento, CV senza età/stato civile/foto/origine, senza immagini né nomi di file, avviso sulla decisione umana, clic sul punteggio apre la pagina. «Altri strumenti › Confronta e classifica». Esportazione PDF non implementata (solo CSV). |

I subagenti avviati per queste funzioni si sono interrotti per il limite di utilizzo dell'API
prima di produrre codice; nessun file parziale è stato aggiunto.
