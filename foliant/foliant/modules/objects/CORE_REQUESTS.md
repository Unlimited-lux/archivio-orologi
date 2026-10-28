# Richieste al nucleo (modulo `objects`)

1. *(fatto nel nucleo, adottato)* **Tab nella vista documento.** Qt consuma Tab/Maiusc+Tab per il cambio di fuoco prima di
   `Tool.key()`. Soluzione locale: filtro eventi sulla vista. Proposta: in `DocumentView.event()`
   passare Tab al `Tool.key()` attivo prima di `focusNextPrevChild`.
2. *(fatto nel nucleo, adottato)* **Selezione del testo su più pagine** (OBJ-34): `TextSelection` è limitata a una pagina; per
   creare un collegamento per pagina servirebbe una selezione multipagina.
3. *(fatto dal modulo Sicurezza, SEC-27)* **`follow_link` con siti consentiti** (OBJ-41): il modulo
   usa la conferma unica; `linkopen.py` è stato tolto.
4. **Avvisi di profilo PDF/A** (OBJ-01/16/33): manca un'API comune per sapere se il documento
   dichiara PDF/A/PDF-X.
