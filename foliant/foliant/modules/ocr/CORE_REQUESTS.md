# Richieste al nucleo (modulo OCR)

1. ~~Barra informativa per documento~~: adottato `ctx.show_banner`. (UX-14): oggi la barra di proposta è un widget figlio della vista,
   posizionato sopra il viewport. Un'API `view.show_banner(id, testo, azioni)` del nucleo eviterebbe
   sovrapposizioni con i banner di altri moduli.
2. ~~Blocco temporaneo delle modifiche~~: adottato `Document.set_locked`.
3. **Evidenziazione nel pannello miniature** (OCR-01 «Dettagli»): un'API per marcare pagine con un'icona/colore.
4. **Inserimento su pagine ruotate/ritagliate**: verificato con test di rilettura (/Rotate 90/180/270, CropBox spostato,
   ricercabile e modificabile: `test_rilettura_pagine_ruotate_e_ritagliate`). La migrazione a
   `foliant.core.placement` resta facoltativa.
5. Nucleo unito: la fixture autouse `_no_modal_boxes` in `tests/modules/ocr/conftest.py` può
   essere rimossa (rete di sicurezza in `tests/conftest.py`).
