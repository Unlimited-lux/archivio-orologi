# Richieste al nucleo — modulo Proteggi

Ogni richiesta indica il problema, la soluzione locale adottata nel modulo e la
modifica proposta al nucleo.

## 1. Annulla/ripeti delle opzioni di salvataggio (SEC-04…SEC-08) — risolta

Il nucleo ora include `save_options` in annulla/ripeti; il riallineamento locale
del modulo è stato rimosso.

## 2. Tipo di accesso e permessi del documento (SEC-01, SEC-02, SEC-03) — risolta

Il modulo usa `document.access`, `document.permissions`/`allows`, `document.encryption_info` e `document.unlock` del nucleo; le soluzioni locali (lettura del dizionario /Encrypt all'apertura, verifica della password sul file su disco) sono state rimosse.


## 3. Collegamenti esterni (SEC-27) — risolta

Il modulo registra il suo gestore con `ctx.add_link_handler(handler, priority=-100)`:
ultima istanza prima del comportamento predefinito del nucleo.

## 4. Avviso al salvataggio con segni non applicati (SEC-19) — risolta

Il modulo registra `ctx.add_guard("save")` e `"save_as"`: con segni non applicati chiede «Applica ora / Continua con i segni / Annulla» prima di salvare.


## 5. Barra di avviso del documento (UX-14, SEC-26) — risolta

Gli avvisi del modulo usano `ctx.show_banner`/`hide_banner` (adattatore `widgets.NoticeBoard`); il widget locale è stato rimosso.


## 6. Selezione del testo su più pagine e parziale (SEC-14)

La selezione del nucleo è per parole intere e su una sola pagina: la redazione
del testo selezionato usa quindi parole intere. Proposta: selezione a livello
di carattere e su più pagine (`TextSelection` per pagina).

## 7. File temporanei e cache dei documenti cifrati (SEC-30), isolamento (SEC-29)

Fuori dall'ambito del modulo: cache delle miniature, file di recupero e limiti
di risorse sono del nucleo. Proposta: nessuna cache su disco per i documenti
con `original_protection` non vuota; rendering in processo separato con limiti
di memoria e tempo.

## 8. Cifratura «tutto tranne i metadati» (SEC-06) — ancora aperta (`ENCRYPT_METADATA_SUPPORTED = False` nel nucleo)

PyMuPDF non permette di scrivere `/EncryptMetadata false`. Servirebbe un
post-processo del file salvato (riscrittura del dizionario /Encrypt e di /Perms
per R6) nel nucleo di salvataggio o una versione del motore che lo supporti.

## 9. Azioni dell'elaborazione in blocco (SEC-12, BAT-*)

SEC-12 chiede che le policy di protezione compaiano «tra le azioni dell'elaborazione
in blocco». L'elaborazione in blocco attuale (`modules/convert/batch.py`) fa solo
conversioni e non accetta azioni di altri moduli. Proposta: `ctx.add_batch_action(id,
label, run(path, out, options), options_widget=None)`, dove `run` lavora su un file
alla volta in un thread di lavoro. Per ora il modulo security offre un proprio comando,
«Policy di protezione › Applica a più file…» (`SecurityController.apply_policy_to_files`).
Con il registro basterebbe iscrivere `policies.protect_file`.

## 10. Pagina «Sicurezza e riservatezza» delle Preferenze (SEC-32, UX-17)

La specifica colloca le impostazioni del blocco in Preferenze › Sicurezza e
riservatezza. Il modulo ha già la pagina (`applock.LockPreferences`, con `apply()`),
ma `tests/test_window_services.py::test_preferences_pages_and_author_name` si aspetta
che le pagine siano esattamente quelle aggiunte dal test: con una pagina in più del
modulo, il test fallisce. Proposta: nel test confrontare solo le pagine aggiunte
(per esempio `[...][-2:]`) oppure creare la finestra senza moduli. Dopo la modifica, in
`security/__init__.py` basta
`ctx.add_preferences_page(_("Sicurezza e riservatezza"), lambda parent=None: LockPreferences(c.applock, parent))`.
Per ora la stessa pagina si apre da Proteggi › Blocco dell'applicazione….
