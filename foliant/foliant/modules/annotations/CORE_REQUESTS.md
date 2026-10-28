# Richieste al nucleo (modulo commenti)

1. **Salvataggio incrementale per i documenti firmati (ANN-01).** `Document.save()` riscrive sempre
   l'intero file (`garbage=3`): aggiungere una nota a un PDF firmato invalida la firma. Serve
   un'opzione di salvataggio incrementale (`pdf.save(path, incremental=True)` sul file originale)
   usata automaticamente quando il documento contiene firme.
   *Soluzione locale:* nessuna (il modulo non scrive mai su disco).

2. **Disegno delle pagine senza annotazioni (ANN-39).** `DocumentView._render_page` chiama sempre
   `get_pixmap(annots=True)`. Serve una proprietà della vista, ad es. `view.show_annotations`,
   rispettata da rendering, miniature e stampa a schermo.
   *Soluzione locale:* `visibility.py` sostituisce `_render_page` sulla singola vista.

3. **Selezione di testo su più pagine (ANN-10, ANN-12).** `TextSelection` è limitata a una pagina:
   per evidenziare una selezione che attraversa due pagine servirebbe una selezione multipagina
   (elenco di `TextSelection`). *Soluzione locale:* un'evidenziazione per pagina, una alla volta.

4. **Identità stabile degli oggetti dopo il salvataggio.** Il salvataggio con `garbage=3`
   rinumera gli oggetti nel file su disco (non in memoria): va bene per il modulo, ma conviene
   documentarlo nel README per chi confronta gli xref dopo una riapertura.

5. **Messaggi temporanei nella barra di stato dai test.** `ctx.status` va bene; nessuna modifica.

6. **Preferenze utente condivise (ANN-02).** Il nome autore è salvato in `annotations/author`;
   se il nucleo introduce una finestra Preferenze, conviene una voce «Nome autore» che usi questa chiave.
