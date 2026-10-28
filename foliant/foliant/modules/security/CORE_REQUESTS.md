# Richieste al nucleo — modulo Proteggi

Ogni richiesta indica il problema, la soluzione locale adottata nel modulo e la
modifica proposta al nucleo.

## 1. Annulla/ripeti delle opzioni di salvataggio (SEC-04…SEC-08) — risolta

Il nucleo ora include `save_options` in annulla/ripeti; il riallineamento locale
del modulo è stato rimosso.

## 2. Tipo di accesso e permessi del documento (SEC-01, SEC-02, SEC-03)

**Problema.** Il nucleo non conserva l'esito dell'autenticazione (utente o
proprietario) né la descrizione della cifratura all'apertura; dopo la prima
modifica il documento interno è una copia non cifrata. Non esiste un'API per
sapere quali comandi sono consentiti da `/P`.

**Soluzione locale.** All'apertura (`documentOpened`) il modulo legge
`pdf.metadata["encryption"]` e il dizionario `/Encrypt`
(`protection.remember_open_state`); l'accesso completo è dedotto da
`original_protection["owner_pw"] == document.password` oppure dallo sblocco con
la password dei permessi verificata sul file su disco (`protection.unlock`).
Le azioni del modulo che modificano il contenuto (redazione, sanificazione,
metadati, appiattimento) chiedono la password dei permessi se manca il permesso
di modifica. Barra di stato: pulsante lucchetto «Accesso completo/limitato».

**Proposta.**
- `Document.access` = `"owner" | "user" | "none"` e `Document.permissions` (bit
  di `/P` con l'AND di `/Perms`), `Document.encryption_info` (V, R, Length,
  EncryptMetadata, metodo) letti all'apertura.
- `Document.unlock(password)` per passare all'accesso completo.
- Nel nucleo: disattivare in modo coerente stampa, copia (Ctrl+C), esportazione,
  modifica, commenti, assemblaggio in base ai permessi (con suggerimento che
  nomina il permesso mancante) e usare `protection.allowed_now(document)` o
  l'equivalente nucleo.
- Finestra di richiesta della password (SEC-01): mostra/nascondi, avviso Bloc
  Maiusc, messaggio «Password non corretta» nella stessa finestra, gestore di
  sicurezza non supportato con il suo nome.

## 3. Collegamenti esterni (SEC-27) — risolta

Il modulo usa `ctx.add_link_handler` (registrato una sola volta: l'elenco del
nucleo è globale). Poiché il controllo di sicurezza deve valere come ultima
istanza prima del comportamento predefinito, il gestore del modulo interroga
prima i gestori registrati dopo di lui. Proposta: un parametro `priority` in
`add_link_handler` (es. «ultimo») per evitare questa precedenza manuale.

## 4. Avviso al salvataggio con segni non applicati (SEC-19)

**Problema.** Il modulo non può intervenire prima del salvataggio: la specifica
chiede una finestra «Applica ora / Continua con i segni / Annulla» prima di
salvare, stampare o condividere con segni di redazione non applicati.

**Soluzione locale.** Dopo il salvataggio (notifica `SAVED`) compare una barra
di avviso rossa con «Applica ora».

**Proposta.** Ganci `before_save(view) -> bool` e `before_print(view) -> bool`
registrabili dai moduli (restituendo False il salvataggio si annulla).

## 5. Barra di avviso del documento (UX-14, SEC-26)

**Problema.** Il nucleo non offre banner di stato del documento.

**Soluzione locale.** `widgets.Banner`, figlio del viewport della vista, mostra
un avviso alla volta per priorità con «N altri avvisi».

**Proposta.** `view.show_banner(key, priority, testo, azioni)` nel nucleo,
condiviso da tutti i moduli (script, permessi, firme, OCR, file riparato…).

## 6. Selezione del testo su più pagine e parziale (SEC-14)

La selezione del nucleo è per parole intere e su una sola pagina: la redazione
del testo selezionato usa quindi parole intere. Proposta: selezione a livello
di carattere e su più pagine (`TextSelection` per pagina).

## 7. File temporanei e cache dei documenti cifrati (SEC-30), isolamento (SEC-29)

Fuori dall'ambito del modulo: cache delle miniature, file di recupero e limiti
di risorse sono del nucleo. Proposta: nessuna cache su disco per i documenti
con `original_protection` non vuota; rendering in processo separato con limiti
di memoria e tempo.

## 8. Cifratura «tutto tranne i metadati» (SEC-06)

PyMuPDF non permette di scrivere `/EncryptMetadata false`. Servirebbe un
post-processo del file salvato (riscrittura del dizionario /Encrypt e di /Perms
per R6) nel nucleo di salvataggio o una versione del motore che lo supporti.
