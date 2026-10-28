# Richieste al nucleo — modulo Firma

## 1. Salvataggio incrementale per i documenti firmati (SIG-11, SIG-21) — prioritaria

**Problema.** `Document.save()` / `save_copy()` scrivono `to_bytes()` (`tobytes(garbage=3, deflate=True)`),
cioè riscrivono l'intero file. In un PDF firmato questo cambia i byte coperti da `/ByteRange`, quindi
**ogni salvataggio invalida tutte le firme**, anche quando l'utente ha solo aggiunto
un'annotazione, che sarebbe una modifica consentita.

**Proposta.**
- `Document` conserva i byte originali del file (`self.original_bytes`) e sa se il file
  contiene firme (`has_signatures`, impostabile da un modulo o calcolato all'apertura cercando
  `/ByteRange` sotto `/AcroForm`).
- Per i documenti firmati, `save(path)` produce `original_bytes + aggiornamento incrementale`.
  Due strade:
  a) aprire il documento del motore dal **file** (non da stream), così
     `pymupdf.Document.save(path, incremental=True, encryption=PDF_ENCRYPT_KEEP)` funziona: oggi è
     impossibile perché `Document` apre sempre da stream e `_restore()` ricrea il documento dai byte
     delle istantanee, già riscritti;
  b) in alternativa, un hook `Document.save_strategy` (callable `(document, path) -> bytes | None`)
     che un modulo può registrare: il modulo Firma calcolerebbe l'aggiornamento incrementale (es. con
     `pyhanko.pdf_utils.incremental_writer`, copiando gli oggetti cambiati).
- Annulla/ripeti per i documenti firmati non deve mai sostituire i byte di base con un'istantanea
  riscritta: le istantanee dovrebbero essere «byte originali + delta».
- «Salva con nome» su un documento firmato copia i byte originali e accoda le modifiche (SIG-21 punto 4).
- Salvataggio automatico e recupero (se previsti) mai sul file firmato.

**Soluzione locale adottata nel frattempo.**
- La firma digitale non passa da `Document.save()`: il modulo chiede dove salvare, scrive il file
  firmato con `atomic_write()` partendo dai byte **su disco** (salvataggio incrementale di pyHanko, il
  file originale è un prefisso esatto) e lo riapre con `ctx.open_path`.
- Anche l'aggiunta di un campo firma a un documento già firmato viene scritta in modo incrementale
  direttamente su file.
- **Salvataggio incrementale locale** (`incremental.py`). Quando un documento aperto risulta firmato, il
  modulo sostituisce i metodi `save` e `save_copy` *di quell'istanza* di `Document`. Le versioni
  sostitutive accodano ai byte firmati su disco solo gli oggetti cambiati: confrontano oggetto per oggetto
  il documento in memoria con il file e salvano con `save(incremental=True)` di pymupdf su una copia
  temporanea, poi la scrivono con `atomic_write`. Il nucleo non viene modificato. La soluzione dipende
  però da due dettagli interni:
  - la numerazione degli oggetti, che le istantanee `tobytes(garbage=0)` conservano;
  - gli attributi privati `_saved_state` e `_notify`.

  Con il supporto del nucleo (punti a o b qui sopra) questa sostituzione si può eliminare.
- Per le modifiche al **contenuto** di un documento firmato (pagine, testo; le annotazioni sono escluse
  quando la certificazione le consente) il modulo ascolta `documentChanged` e, alla prima modifica, mostra
  l'avviso SIG-21 con tre scelte: «Annulla» (esegue `undo()`), «Lavora su una copia senza firme»,
  «Procedi comunque». L'avviso arriva **dopo** la modifica (vedi richiesta 2).

## 2. Avviso *prima* delle operazioni invalidanti (SIG-21)

Serve un punto di aggancio prima che una modifica venga applicata, per esempio un segnale o un callback
`Document.before_change(label) -> bool` (o `Context.add_change_guard(fn)`), che un modulo può usare
per chiedere conferma o rifiutare. Con una classificazione delle operazioni (`change(label, kind=...)`,
es. `kind="annotation" | "form" | "content" | "pages" | "metadata"`) il modulo Firma potrebbe lasciar
passare senza avviso le modifiche consentite (annotazioni, compilazione) e bloccare quelle vietate da una
certificazione «nessuna modifica» (SIG-21 punto 5: strumenti disattivati con il motivo).

## 3. Barra informativa sopra il documento (SIG-18, SIG-20)

La specifica chiede una barra sopra la pagina («Firmato: tutte le firme sono valide», «Questo documento
richiede N firme» con «Firma ora» e «Campo successivo»). Proposta:
`Context.show_document_banner(view, id, text, level, actions=[(testo, callback)])` /
`hide_document_banner(view, id)`. Nel frattempo il riepilogo compare in cima al pannello «Firme», che si
apre da solo quando il documento contiene firme o campi firma, e nella barra di stato.

## 4. Documenti in sola lettura (SIG-17)

«Visualizza versione firmata» deve aprire la revisione firmata **in sola lettura**. Proposta:
`Document.read_only = True`, con le azioni di modifica e di salvataggio disattivate dal nucleo. Oggi la
revisione si apre come documento nuovo, senza percorso: per salvarla bisogna scegliere un nome.

## 5. Preferenza per il nome dell'utente

Firma digitata e autore delle annotazioni usano `settings().value("user/name")`. Servirebbe una voce
«Nome» nelle preferenze generali del nucleo.
