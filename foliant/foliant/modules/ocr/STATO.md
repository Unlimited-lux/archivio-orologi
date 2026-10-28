# Modulo OCR — stato (sezione 7 della specifica)

Motore: Tesseract 5 (processo locale, uscita hOCR con affidabilità) + pre-elaborazione OpenCV;
composizione del PDF con PyMuPDF. API stabile in `engine.py`: `ocr_pdf_bytes`, `ocr_page_text`,
`available_languages` (più `recognize_document`, `apply_result`, `recognize_area`, `remove_ocr_layer`).
Test: `tests/modules/ocr/` (engine, criteri, ui, languages, textexport).

| ID | Priorità | Stato | Test | Note |
|---|---|---|---|---|
| OCR-01 Rilevamento e proposta | P0 | fatto (parziale: Dettagli in finestra, non nelle miniature) | test_ui: proposta_pagine_3_e_7, nessuna_proposta…, rilevamento_disattivato…, proposta_al_clic…; test_criteri: testo_non_estraibile, documento_lungo… | analisi incrementale a passi di 25 ms nel thread UI con `try_engine`; barra non bloccante sopra la vista; «non chiedere più» per /ID o impronta nelle preferenze |
| OCR-02 Motore locale | P0 | fatto | test_engine: accuratezza…, pagina_che_fallisce…, deterministico; test_criteri: parallelismo… | pagine in parallelo (core−1, ridotto con poca memoria), timeout 120 s, pagina guasta = «non riconosciuta» |
| OCR-03 Lingue | P0 | fatto (arabo non verificato: pacchetto non installato) | test_languages (11), test_engine: errori_chiari, test_ui: lingua_mancante, dialogo_lingue | 53 lingue in catalogo, installa da file con verifica, scarica su richiesta, rimuovi, manifest sha256 |
| OCR-04 Impostazioni e intervallo | P0 | fatto (manca anteprima dell'immagine di lavoro e «pagine selezionate nelle miniature») | test_ui: dialogo_impostazioni, convalida_immediata…, profilo_esporta_importa, destinazione_nuovo_documento, permessi…, documento_firmato…; test_criteri: intervallo_altre_pagine_invariate, intervalli_non_validi | profili JSON, riepilogo con stima del tempo, password proprietario, avviso firme |
| OCR-05 PDF ricercabile | P0 | fatto | test_engine: ricercabile_aspetto_identico…; test_criteri: riquadri_parole_iou, marcato_metadati_lingua…, senza_correzioni_rendering_identico, pagina_mista… | una parola per oggetto di testo (Tz + Tm inclinato), sequenza marcata /FoliantOCR, metadati e /Lang |
| OCR-06 PDF modificabile | P0 | fatto (testo su foto non escluso automaticamente) | test_engine: modificabile_testo_visibile…, redazione_e_reinserimento; test_criteri: righe_corpo_e_linea_di_base, inclinata_righe_orizzontali, sostituisce_livello…, stile_e_famiglia, conserva_originale… | sfondo ripulito, pagina raddrizzata, famiglia/grassetto/corsivo/colore stimati, originale in livello nascosto facoltativo |
| OCR-07 Solo testo | P0 | fatto | test_textexport (17), test_ui: solo_testo_e_ocr_area, esportazione_alto… | TXT (separatore riga o U+000C, sillabazione, paragrafi, CRLF), ALTO v4, hOCR; sillabazione senza dizionario |
| OCR-08 Raddrizzamento/rotazione/risoluzione | P0 | fatto | test_criteri: stima_inclinazione…, rotazione_automatica, bassa_risoluzione, applica_correzioni_alla_pagina_visibile | correzioni sulla pagina visibile con /Rotate e matrice `cm` senza ricodifica |
| OCR-09 Pulizia immagine | P1 | parziale | test_criteri: pulizia_rumore_e_contrasto | mediano, contrasto, normalizzazione sfondo; niente binarizzazione adattiva esplicita, bordi neri, prospettiva, «applica all'immagine» |
| OCR-10 Layout | P1 | parziale | test_criteri: due_colonne_ordine_di_lettura | ordine del motore + separazione colonne; niente zone manuali né struttura tabelle |
| OCR-11 OCR di un'area | P1 | fatto | test_ui: strumento_area, area_nel_documento_e_testo_esistente, menu_contestuale_riconosci_testo_nell_immagine | copia, TXT, aggiungi come ricercabile o modificabile nell'area; «Riconosci testo nell'immagine» dal menu contestuale |
| OCR-12 Revisione | P1 | fatto (sospetti solo per affidabilità: nessun dizionario di lingua) | test_ui: anteprima_con_correzione, pannello_parole_incerte, revisione_da_tastiera, dati_di_revisione_nel_file; test_criteri: parole_degradate_segnalate | anteprima prima di applicare, pannello con ritaglio, tastiera, accetta uguali, dizionario utente, dati di revisione facoltativi nel file (/PieceInfo) |
| OCR-13 Avanzamento/annullamento | P0 | fatto (manca la pausa) | test_ui: ocr_in_background_documento_consultabile, …_modificato_nel_frattempo, …_annullato_o_chiuso, annulla_mantieni…; test_criteri: annullamento_restituisce_pagine_completate | OCR non modale: lavora su una copia in un thread, barra di avanzamento con Annulla nella vista, documento consultabile; alla fine un solo passo di annulla; se il documento cambia nel frattempo, il testo va solo sulle pagine rimaste identiche (impronte per pagina) dopo conferma |
| OCR-14 Pagine con testo | P1 | fatto | test_criteri: politiche_pagine_con_testo, pagina_mista…; test_ui: forza_ocr_chiede_conferma | salta / sostituisci livello / forza (con conferma); le miste: solo aree immagine |
| OCR-15 In blocco | P1 | fatto | test_ui: ocr_in_blocco, riprendi_lotto_interrotto | cartelle, TIFF/immagini, già ricercabili saltati, protetti/danneggiati in errore, suffisso numerico, sovrascrittura con .bak, CSV, «Riprendi lotto» |
| OCR-16 Conversione Office | P1 | non fatto | — | modulo convert: può usare `ocr_page_text` / `recognize_document` |
| OCR-17 PDF/A, PDF/UA | P1 | non fatto | — | |
| OCR-18 Rimozione livello OCR / PDF solo immagine | P1 | fatto | test_ui: rimuovi_livello_ocr, pdf_solo_immagine | rimozione del livello invisibile; conversione in sole immagini con conferma, un passo di annulla |
| OCR-19 Immagine compressa | P2 | non fatto | — | |
| OCR-20 Integrazione | P1 | parziale | — | API per gli altri moduli, `ctx.ocr`, proposta al clic in Modifica/Selezione |

Note finali:
- Nucleo aggiornato unito (07:50 UTC); suite completa verde, nessuna scorciatoia registrata dal modulo.
- Pagine con /Rotate e CropBox spostato: verificate con `test_criteri.py::test_rilettura_pagine_ruotate_e_ritagliate`.
- Pagine con /Rotate e contenuto memorizzato di lato (anche ritagliate), modalità modificabile: verificate con
  `test_criteri.py::test_modificabile_contenuto_memorizzato_di_lato`.
- Base con tutti i moduli unita alle 08:59 UTC.
- Stile del testo modificabile: grassetto relativo alla pagina; le grazie si assegnano solo con misura netta
  (nel dubbio senza grazie), quindi i testi con grazie sottili possono uscire senza grazie.
- Correzioni del collaudo (08:00–08:20 UTC): bug_07, bug_01/02, bug_04, bug_05, bug_08, tabelle e stile.
- Verifica visiva fatta con catture offscreen (temi chiaro e scuro) di barra di proposta, impostazioni,
  anteprima/revisione, pannello parole incerte e confronto scansione/risultato modificabile.
