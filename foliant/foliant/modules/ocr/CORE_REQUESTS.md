# Richieste al nucleo (modulo OCR)

1. **Barra informativa per documento** (UX-14): oggi la barra di proposta è un widget figlio della vista,
   posizionato sopra il viewport. Un'API `view.show_banner(id, testo, azioni)` del nucleo eviterebbe
   sovrapposizioni con i banner di altri moduli.
2. **Attività in background non modali** (OCR-13, UX-12): `run_task` è modale per la finestra; per l'OCR di
   documenti lunghi servirebbe un'esecuzione in background che lasci consultabile il documento, con pausa.
3. **Evidenziazione nel pannello miniature** (OCR-01 «Dettagli»): un'API per marcare pagine con un'icona/colore.
4. **Inserimento su pagine ruotate/ritagliate**: verificato con test di rilettura (/Rotate 90/180/270, CropBox spostato,
   ricercabile e modificabile: `test_rilettura_pagine_ruotate_e_ritagliate`). La migrazione a
   `foliant.core.placement` resta facoltativa.
5. Nucleo unito: la fixture autouse `_no_modal_boxes` in `tests/modules/ocr/conftest.py` può
   essere rimossa (rete di sicurezza in `tests/conftest.py`).
