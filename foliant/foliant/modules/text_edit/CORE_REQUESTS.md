# Richieste al nucleo (modulo text_edit)

1. **Strumento predefinito della modalità condivisa «modifica».** Entrando nella modalità il modulo
   attiva «Modifica testo» solo se è attivo lo strumento predefinito; se anche `objects` fa lo stesso,
   vince chi è collegato per ultimo. Servirebbe un modo per dichiarare lo strumento predefinito della modalità.
2. **Chiusura della finestra nei test.** pytest-qt chiude la finestra prima dei finalizzatori dei fixture:
   con un documento modificato appariva la richiesta di salvataggio modale. Nei nostri test `close_view`
   è sostituito per marcare il documento salvato; la rete di sicurezza del nucleo ora copre il caso.
3. **Appunti all'uscita.** Con il plugin offscreen, lasciare dati negli appunti (QMimeData) a fine processo
   provoca un crash: i test svuotano gli appunti a fine test.
4. **`placement`.** La riscrittura usa `page.insert_text` (corretto anche su pagine ruotate e ritagliate,
   verificato da `test_edit_on_rotated_and_cropped_pages`); non usa `TextWriter`/`insert_htmlbox`.
