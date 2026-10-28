# Richieste al nucleo (modulo commenti)

1. **Salvataggio incrementale per i documenti firmati (ANN-01).** `Document.save()` riscrive sempre
   l'intero file (`garbage=3`): aggiungere una nota a un PDF firmato invalida la firma. Serve
   un'opzione di salvataggio incrementale (`pdf.save(path, incremental=True)` sul file originale)
   usata automaticamente quando il documento contiene firme.
   *Soluzione locale:* nessuna (il modulo non scrive mai su disco).

2. **Disegno delle pagine senza annotazioni (ANN-39).** `DocumentView._render_page` chiama sempre
   `get_pixmap(annots=True)`. Serve una proprietà della vista, ad es. `view.show_annotations`,
   rispettata da rendering, miniature e stampa a schermo.
   *Fatto in parte:* la vista ha `show_annotations` (pagine intere e tasselle) e `visibility.py`
   la usa; miniature e stampa disegnano ancora le annotazioni.

3. **Selezione di testo su più pagine (ANN-10, ANN-12).** `TextSelection` è limitata a una pagina:
   per evidenziare una selezione che attraversa due pagine servirebbe una selezione multipagina
   (elenco di `TextSelection`). *Soluzione locale:* un'evidenziazione per pagina, una alla volta.

4. **Identità stabile degli oggetti dopo il salvataggio.** `Document.to_bytes()` con `garbage=3`
   compatta anche il documento **in memoria**: dopo ogni salvataggio gli xref cambiano, quindi
   i riferimenti (pagina, xref) tenuti dai moduli diventano invalidi. Proposta: serializzare una
   copia (`pymupdf.open(stream=snapshot)`) o usare `garbage=0` sul documento vivo, oppure
   documentarlo nel README e notificare `SAVED` come evento che invalida i riferimenti.
   *Soluzione locale:* alla notifica `SAVED` il modulo azzera selezione e appunti e ricarica l'elenco.

5. **Messaggi temporanei nella barra di stato dai test.** `ctx.status` va bene; nessuna modifica.

6. **Preferenze utente condivise (ANN-02).** Il nome autore è salvato in `annotations/author`;
   se il nucleo introduce una finestra Preferenze, conviene una voce «Nome autore» che usi questa chiave.

7. **Finestre dei test mai distrutte.** La fixture `window` chiude la finestra ma non la elimina
   (`deleteLater`/`WA_DeleteOnClose`): i widget restano vivi e ogni `theme.apply()` successivo
   li ripulisce tutti, per cui la configurazione di ogni test cresce linearmente (fino a ~10 s a
   fine suite). Proposta: `win.setAttribute(Qt.WA_DeleteOnClose)` nella fixture oppure
   `win.deleteLater()` + `QApplication.sendPostedEvents(None, QEvent.DeferredDelete)` alla fine.
