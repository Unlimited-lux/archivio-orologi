# Richieste al nucleo — modulo Moduli

Ogni punto ha già una soluzione locale nel modulo; la modifica al nucleo la renderebbe più pulita.

1. **Salvataggio e numerazione degli oggetti.** `Document.to_bytes()` usa `garbage=3`, che
   rinumera gli oggetti anche nel documento in memoria. Chi conserva xref (campi, annotazioni)
   li perde dopo ogni salvataggio, e `Document.version` non cambia.
   *Proposta:* salvare da una copia (o con `garbage` ≤ 1 sul documento vivo) oppure incrementare
   `version`/emettere un evento dedicato dopo la rinumerazione.
   *Locale:* il modulo ascolta l'evento `SAVED` e rilegge i campi (`fill.FormFiller._on_document_event`).

2. **Banner di stato del documento (UX-14).** Non c'è un'API per i banner in cima all'area
   documento. *Locale:* la barra del modulo è un widget figlio della vista posizionato con
   `setViewportMargins`. *Proposta:* `ctx.show_banner(view, id, testo, azioni, priorità)` con un
   banner alla volta e «N altri avvisi».

3. **Icone legate al tema e finestre chiuse.** `icons._bound` e `theme._listeners` crescono con
   ogni finestra creata (le finestre chiuse non vengono distrutte), e ogni `theme.apply()` rilegge
   tutte le icone: nei test la creazione delle finestre diventa via via più lenta.
   *Locale:* la fixture `free_windows` dei test del modulo distrugge le finestre; la barra del
   modulo segue il tema con `changeEvent` invece di `theme.on_change`.
   *Proposta:* `deleteLater()` delle finestre chiuse e rimozione dei listener.

4. **Prima del salvataggio.** Manca un aggancio «prima di salvare» (es. `document.add_listener`
   con tipo `"before_save"`): servirebbe per scrivere gli aspetti mancanti solo al salvataggio e
   per l'avviso sui campi obbligatori vuoti (FRM-09, FRM-15). *Locale:* aspetti e collegamenti
   vengono riparati all'apertura con un passo annullabile «Ripara il modulo».

5. **Salvataggio incrementale.** Per i documenti firmati FRM-09 richiede il salvataggio
   incrementale (il file originale come prefisso). Il nucleo salva sempre un file completo.
   *Proposta:* opzione `Document.save(incremental=True)` usata automaticamente se il documento
   contiene firme.

6. **Campi firma non firmati nella resa.** MuPDF disegna un proprio segnaposto («SIGN») per i
   widget firma non firmati anche quando /AP /N è vuoto, quindi la stampa fatta da Foliant lo
   mostra. Il file salvato contiene invece un aspetto vuoto (`engine.blank_signature_appearance`),
   corretto per gli altri lettori. *Proposta:* nella stampa e nell'esportazione in immagine
   disattivare la sintesi locale degli aspetti dei widget firma.
