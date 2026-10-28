# Specifica funzionale — Foliant

Documento unico per l'implementazione. Scritto con metodo clean-room da fonti pubbliche
(documentazione, guide, recensioni comparative) e dallo standard aperto del formato PDF;
nessun testo, codice o risorsa di prodotti commerciali. Implementazione-neutra.

Convenzioni: ogni funzione ha un ID (`AREA-NN`), descrizione, flusso, formati, casi limite,
criteri di accettazione verificabili («Dato…, quando…, allora…») e priorità:
**P0** indispensabile nella prima versione · **P1** prima versione completa · **P2** successivo.

## Indice e riepilogo priorità

| Sezione | Funzioni | P0 | P1 | P2 |
|---|---:|---:|---:|---:|
| 1. Visualizzazione e navigazione | 33 | 14 | 12 | 7 |
| 2. Gestione pagine | 30 | 12 | 16 | 2 |
| 3. Modifica testo | 25 | 13 | 7 | 5 |
| 4. Immagini, oggetti e collegamenti | 43 | 20 | 14 | 9 |
| 5. Annotazioni e commenti *(in redazione)* | 0 | 0 | 0 | 0 |
| 6. Conversione, creazione e ottimizzazione | 33 | 8 | 16 | 9 |
| 7. OCR e PDF scannerizzati | 3 | 3 | 0 | 0 |
| 8. Moduli *(in redazione)* | 0 | 0 | 0 | 0 |
| 9. Firma | 25 | 11 | 9 | 5 |
| 10. Protezione e sicurezza *(in redazione)* | 0 | 0 | 0 | 0 |
| 11. Confronto e operazioni in blocco | 7 | 6 | 1 | 0 |
| 12. Assistente IA | 27 | 0 | 16 | 11 |
| **Totale** | **226** | **87** | **91** | **48** |

## 0. Sezioni generali

> Sezione in redazione.


## 1. Visualizzazione e navigazione

> Convenzioni per i criteri di accettazione: "macchina di riferimento" = CPU 4 core, 8 GB RAM, SSD, schermo 1920×1080 a 96 dpi; i tempi si misurano a cache fredda. "Corpus di prova" = insieme versionato di PDF campione (versioni 1.0–2.0, cifrati, danneggiati, pagine di dimensioni miste, documenti da 5.000+ pagine, testo RTL/CJK) usato dai test automatici. Le scorciatoie citate sono valori predefiniti modificabili (VIS-52).

### VIS-01 — Schermata Home
**Descrizione:** all'avvio senza documenti (o con il comando "Home") l'utente vede una schermata iniziale con azioni rapide (Apri, Crea PDF, strumenti frequenti), file recenti, preferiti e un'area su cui trascinare i file, così da riprendere il lavoro con un clic.
**Flusso:** 1. Avvio senza argomenti → si apre la Home (oppure la sessione precedente se l'utente ha scelto di ripristinarla, VIS-30). 2. L'utente sceglie un'azione: Apri, apri un recente o un preferito, trascina un file, avvia uno strumento (unisci, converti, crea: rimandano alle rispettive aree). 3. Il documento si apre in una nuova scheda; la Home resta raggiungibile da un pulsante fisso.
**Formati/strutture:** elenchi recenti/preferiti nel profilo utente (percorso, data ultimo accesso, ultima pagina, zoom, miniatura in cache); nessuna modifica ai file.
**Casi limite:** configurazione corrotta → Home con elenchi vuoti e avviso, file rigenerato e il vecchio conservato come backup; profilo non scrivibile → funzionamento senza persistenza con avviso; trascinamento di file non PDF → proposta di conversione (area Creazione) o messaggio "formato non supportato"; trascinamento di più file → una scheda per file.
**Criteri di accettazione:**
- Dato l'avvio senza argomenti e senza sessione da ripristinare, quando l'applicazione è pronta, allora la Home è visibile entro 2 s sulla macchina di riferimento e mostra le azioni Apri e Crea, i recenti e i preferiti.
- Dato un PDF trascinato sull'area di rilascio, quando viene rilasciato, allora si apre in una nuova scheda alla pagina 1 o all'ultima posizione nota (VIS-02).
- Dato un file di configurazione illeggibile, quando l'applicazione si avvia, allora la Home appare con elenchi vuoti, compare un avviso non bloccante e il file corrotto è conservato con suffisso di backup.
**Priorità:** P0

### VIS-02 — File recenti e preferiti
**Descrizione:** elenco dei documenti aperti di recente con miniatura, nome, percorso, data e posizione di lettura, per riaprirli in un clic; l'utente può fissare i file più usati come preferiti.
**Flusso:** 1. Ogni apertura o salvataggio porta la voce in cima all'elenco. 2. Dalla Home o dal menu File → Recenti l'utente vede l'elenco (vista elenco o griglia), lo filtra per nome e lo ordina per data o nome. 3. Clic → il file si apre alla pagina e allo zoom dell'ultima sessione. 4. Menu contestuale: aggiungi/rimuovi dai preferiti, rimuovi dall'elenco, mostra nella cartella, svuota elenco.
**Formati/strutture:** record nel profilo utente; miniatura in cache invalidata quando cambiano dimensione o data di modifica del file.
**Casi limite:** file spostato, eliminato o su supporto rimosso → voce marcata "non disponibile", il clic propone Rimuovi o Individua; percorso di rete lento → miniature caricate in modo asincrono senza bloccare l'elenco; file cifrato → nessuna miniatura del contenuto (icona lucchetto); preferenza "non registrare la cronologia" → nessuna voce salvata; limite configurabile (predefinito 50) con rimozione delle voci più vecchie non preferite.
**Criteri di accettazione:**
- Dato un documento chiuso a pagina 37 con zoom 150%, quando viene riaperto dai recenti, allora si apre a pagina 37 con zoom 150%.
- Dato un recente il cui file non esiste più, quando l'elenco viene mostrato, allora la voce è marcata non disponibile e il clic offre Rimuovi/Individua senza errori.
- Dato un PDF protetto da password aperto in precedenza, quando compare nei recenti, allora non è mostrata alcuna miniatura del contenuto e nessuna password è memorizzata.
- Data la preferenza "non registrare la cronologia" attiva, quando si aprono 3 file, allora l'elenco dei recenti resta invariato.
**Priorità:** P0

### VIS-03 — Gestione dei file locali
**Descrizione:** dalla Home, dai recenti e da un navigatore di cartelle integrato l'utente rinomina, duplica, copia o sposta in un'altra cartella, sposta nel cestino, mostra nella cartella, copia il percorso, stampa e invia i file; crea cartelle di lavoro e organizza i preferiti.
**Flusso:** 1. Selezione di uno o più file. 2. Scelta dell'operazione dal menu contestuale o dalla barra. 3. Conferma per le operazioni distruttive. 4. Aggiornamento immediato di elenchi e schede aperte.
**Formati/strutture:** file system locale e posizioni montate dal sistema; eliminazione tramite il cestino del sistema quando disponibile.
**Casi limite:** file aperto in una scheda rinominato o spostato → la scheda segue il nuovo percorso; nome già esistente → scelta tra sostituisci, rinomina automatica, annulla; caratteri non validi o nome troppo lungo → errore esplicito; permessi insufficienti o volume in sola lettura → messaggio con il motivo e file intatto; file con modifiche non salvate → avviso prima di spostarlo o eliminarlo; cestino non disponibile → conferma di eliminazione definitiva.
**Criteri di accettazione:**
- Dato un file aperto in una scheda, quando viene rinominato dalla Home, allora titolo della scheda e recenti mostrano il nuovo nome e il Salva successivo scrive sul nuovo percorso.
- Dato un file in una cartella in sola lettura, quando si tenta di rinominarlo, allora l'operazione fallisce con un messaggio che indica il permesso mancante e il file resta invariato.
- Dato un file selezionato, quando si sceglie Elimina e si conferma, allora il file si trova nel cestino del sistema e scompare dai recenti.
- Dati 3 file selezionati, quando si sceglie Duplica, allora vengono creati 3 file "nome (copia).pdf" byte-identici agli originali.
**Priorità:** P1

### VIS-04 — Archivi compressi
**Descrizione:** comprime uno o più PDF in un archivio ZIP da inviare e apre o estrae i PDF contenuti in un archivio ZIP.
**Flusso:** 1. Selezione dei file → Comprimi in archivio → scelta di nome e cartella. 2. Apertura di un .zip → elenco dei PDF contenuti → apertura di uno (estratto in una cartella temporanea privata, con proposta "Salva con nome" alla modifica) oppure Estrai tutto.
**Formati/strutture:** ZIP (metodi store e deflate), nomi file UTF-8.
**Casi limite:** archivio protetto da password → richiesta della password; archivio corrotto → messaggio e nessun file parziale lasciato; dimensione estratta oltre una soglia configurabile o rapporto di compressione anomalo → interruzione con avviso; voci con percorsi assoluti o "../" → normalizzate dentro la cartella di destinazione; archivio senza PDF → messaggio.
**Criteri di accettazione:**
- Dati 2 PDF, quando vengono compressi in un archivio, allora l'estrazione dell'archivio restituisce 2 file byte-identici agli originali.
- Dato un archivio con la voce "../../x.pdf", quando viene estratto, allora il file finisce dentro la cartella scelta e nulla viene scritto fuori.
- Dato un archivio la cui dimensione estratta supera la soglia, quando si avvia l'estrazione, allora l'operazione si interrompe con avviso e senza file residui.
**Priorità:** P2

### VIS-05 — Apertura dei documenti
**Descrizione:** apre file PDF dalla versione 1.0 alla 2.0 (inclusi PDF/A, PDF/X, PDF/E, PDF/UA) da finestra di dialogo, trascinamento, riga di comando, "Apri con" del sistema e recenti, ciascuno in una scheda, applicando la vista iniziale prevista dal documento.
**Flusso:** 1. Comando Apri (Ctrl+O) → dialogo file del sistema con filtro PDF e selezione multipla. 2. Verifica rapida di intestazione e struttura. 3. Documento cifrato → VIS-06; danneggiato → VIS-07. 4. Vista iniziale: posizione ricordata (VIS-02) se la preferenza è attiva, altrimenti `/OpenAction`, `/PageLayout`, `/PageMode` (pannello aperto all'avvio) e `/ViewerPreferences` (es. `/DisplayDocTitle`, `/Direction`, `/FitWindow`); preferenza utente "ignora le impostazioni del documento". 5. Prima pagina visibile subito, resto caricato in modo progressivo. Da riga di comando: `foliant file.pdf`, frammenti `#page=N`, `#nameddest=nome`, `#zoom=N`, opzione nuova finestra; con un'istanza già attiva il file si apre in una nuova scheda di quell'istanza.
**Formati/strutture:** intestazione `%PDF-x.y` e `/Version` del catalogo; xref tabellare e cross-reference stream, object stream, aggiornamenti incrementali (`/Prev`), file ibridi (`/XRefStm`), linearizzazione (`/Linearized`); frammenti URL per PDF (ISO 32000-2 Annex O).
**Casi limite:** file già aperto → attiva la scheda esistente (opzione per una seconda vista); file non PDF con estensione .pdf o di 0 byte → "Il file non è un PDF valido" con il nome; file oltre 2 GB → supportati (offset a 64 bit); file senza permesso di scrittura → apertura in sola lettura con indicazione nel titolo; file su rete lenta → avanzamento annullabile; file non PDF convertibili → proposta "Crea PDF da file" (area Creazione); modulo XFA dinamico senza pagine statiche → messaggio esplicito (area Moduli); `/Requirements` non soddisfatti → barra informativa con le funzioni non supportate; azioni JavaScript in `/OpenAction` mai eseguite senza consenso.
**Criteri di accettazione:**
- Dato il corpus delle versioni PDF 1.0–2.0, quando ciascun file viene aperto, allora la prima pagina è visualizzata senza errori e il numero di pagine coincide con `/Count` dell'albero delle pagine.
- Dato un PDF di 1.000 pagine e 50 MB, quando viene aperto, allora la prima pagina è visibile entro 1,5 s sulla macchina di riferimento.
- Dato `foliant doc.pdf#page=12` con un'istanza già in esecuzione, quando il comando viene eseguito, allora doc.pdf si apre in una nuova scheda di quell'istanza a pagina 12.
- Dato un PDF con `/OpenAction` verso la pagina 5 con `/FitH`, quando viene aperto per la prima volta, allora la vista mostra la pagina 5 adattata in larghezza.
- Dato un file di testo rinominato in .pdf, quando viene aperto, allora compare il messaggio di file non valido e non resta aperta alcuna scheda vuota.
**Priorità:** P0

### VIS-06 — PDF protetti: password e permessi
**Descrizione:** apre documenti cifrati chiedendo la password, distingue password utente (apertura) e password proprietario (permessi), mostra lo stato di protezione e fa rispettare le restrizioni su stampa, copia, modifica, annotazioni, moduli e assemblaggio.
**Flusso:** 1. Rilevamento di `/Encrypt` nel trailer. 2. Se la password utente vuota è valida → apertura diretta con icona lucchetto e restrizioni attive. 3. Altrimenti richiesta della password (campo mascherato, mostra/nascondi, Annulla; ritardo crescente dopo 5 tentativi errati). 4. Password errata → messaggio che non rivela quale password esiste. 5. Password proprietario → tutte le operazioni abilitate per la sessione. 6. Funzioni non consentite → disabilitate con tooltip che ne spiega il motivo; il comando "Sblocca restrizioni" chiede la password proprietario.
**Formati/strutture:** handler standard `/Filter /Standard` revisioni 2–6: RC4 40/128 bit, AES-128 (`AESV2`), AES-256 (`AESV3`, R6); `/O`, `/U`, `/OE`, `/UE`, `/Perms`, `/EncryptMetadata`; bit di `/P`: 3 stampa, 4 modifica, 5 copia/estrazione, 6 annotazioni e moduli, 9 compilazione, 10 accessibilità, 11 assemblaggio, 12 stampa ad alta qualità; password in UTF-8 normalizzata (SASLprep) per R6, PDFDocEncoding per R2–R4.
**Casi limite:** cifratura a certificato (`/Adobe.PubSec`) → apertura con la chiave dell'utente se disponibile (area Protezione), altrimenti messaggio chiaro; handler proprietari o DRM → "Sistema di protezione non supportato: <nome del filtro>"; documento involucro PDF 2.0 (`/EncryptedPayload`) → mostra la pagina involucro e segnala l'allegato cifrato; password con caratteri non ASCII; `/Encrypt` malformato → trattamento come file danneggiato (VIS-07); bit 10 sempre rispettato a favore di lettura ad alta voce e tecnologie assistive; sola stampa a bassa qualità (bit 12 assente) → stampa rasterizzata a non più di 150 dpi.
**Criteri di accettazione:**
- Dato un PDF AES-256 R6 con password utente "pässwörd", quando l'utente la digita correttamente, allora il documento si apre; con una password errata compare "Password non corretta" e il documento resta chiuso.
- Dato un PDF con password utente vuota e bit 5 disattivo, quando l'utente seleziona del testo e preme Ctrl+C, allora gli appunti non cambiano e compare l'avviso "Copia non consentita dal documento".
- Dato lo stesso documento, quando l'utente inserisce la password proprietario in "Sblocca restrizioni", allora la copia funziona per quella sessione.
- Dato un PDF con stampa non consentita, quando l'utente apre il menu, allora Stampa è disabilitato con il motivo; dato un PDF con sola stampa a bassa qualità, quando stampa, allora l'output è rasterizzato a ≤150 dpi.
- Dato un PDF cifrato aperto e chiuso, quando si ispezionano configurazione, cache e log, allora la password non vi compare in nessuna forma.
**Priorità:** P0

### VIS-07 — File danneggiati e contenuti non supportati
**Descrizione:** apre il più possibile anche file corrotti o non conformi, lo segnala e permette di salvarne una versione riparata; elenca le funzioni del documento che l'applicazione non supporta.
**Flusso:** 1. Analisi tollerante: se xref o trailer non sono validi l'indice degli oggetti viene ricostruito per scansione del file. 2. Barra informativa "Il file era danneggiato ed è stato ricostruito" con il pulsante "Ripara e salva con nome" (salvataggio completo, VIS-48). 3. Pagine non ricostruibili → segnaposto "Pagina non leggibile", le altre visualizzate normalmente. 4. Errori nel contenuto di una pagina → resa parziale di quella sola pagina.
**Formati/strutture:** xref e trailer, `/Prev`, `/XRefStm`, `/Length` degli stream (ricerca di `endstream`), `/Root`, `/ID`, `/Requirements`.
**Casi limite:** file troncato (download interrotto) → pagine disponibili mostrate con avviso; `/Length` errati; oggetti duplicati con lo stesso numero; cicli in `/Parent`, `/Kids` o nei segnalibri → interrotti senza blocchi; riferimenti a oggetti mancanti trattati come null; font o immagini corrotti → font di ripiego o riquadro grigio; stream compressi danneggiati → decodifica della parte recuperabile; file ostili (annidamento estremo, stream che si espandono a dimensioni enormi) → limiti di risorse configurabili; il blocco nell'elaborazione di un documento non chiude le altre schede né l'applicazione; funzioni non supportate (es. 3D, XFA dinamico, multimedia) → elencate nella barra informativa.
**Criteri di accettazione:**
- Dato un PDF del corpus con tabella xref volutamente errata, quando viene aperto, allora tutte le pagine sono visualizzate e compare la barra informativa di ricostruzione.
- Dato un PDF troncato al 60% della sua lunghezza, quando viene aperto, allora le pagine integre sono visualizzate e le altre mostrano il segnaposto.
- Dato un file ricostruito, quando l'utente sceglie "Ripara e salva con nome", allora il nuovo file si riapre senza barra di ricostruzione e la sua tabella xref è coerente.
- Dato il corpus di file malformati generati per fuzzing, quando ciascuno viene aperto, allora l'applicazione non termina in modo anomalo e ogni apertura si conclude (documento o errore) entro 30 s.
**Priorità:** P1

### VIS-08 — Resa delle pagine e documenti di grandi dimensioni
**Descrizione:** le pagine sono mostrate fedelmente (testo, vettori, immagini, trasparenze, annotazioni con il loro aspetto, campi modulo, livelli nello stato corrente) e lo scorrimento resta fluido anche con migliaia di pagine o pagine di grande formato.
**Flusso:** 1. Calcolo delle pagine visibili. 2. Anteprima immediata a bassa risoluzione, poi resa nitida a tasselli alla risoluzione dello zoom. 3. Pre-caricamento delle pagine adiacenti, cache con limite di memoria configurabile, annullamento delle rese per pagine uscite dalla vista. 4. Preferenze di qualità: antialiasing di testo e grafica, levigatura immagini, ispessimento delle linee sottili, dpi del monitor (rilevati o impostati) usati per la dimensione reale.
**Formati/strutture:** area mostrata = `/CropBox` (predefinita `/MediaBox`) intersecata con `/MediaBox`; `/Rotate`; `/UserUnit`; `/Annots` con `/AP` e flag `/F` (Hidden e NoView non mostrate, NoZoom e NoRotate rispettati); contenuto opzionale `/OC`; gruppi di trasparenza; spazi colore (ICCBased, Separation, DeviceN) convertiti per lo schermo; font incorporati o sostituiti in base a `/FontDescriptor`; immagini Flate, DCT, JPX, JBIG2, CCITT.
**Casi limite:** pagine di dimensioni miste (A4, A3 orizzontale, scontrini lunghi) → ciascuna con le proprie proporzioni; pagine enormi (planimetrie oltre 200 pollici con `/UserUnit`) → resa a tasselli senza esaurire la memoria; `/MediaBox` assente o nulla → formato di ripiego secondo la lingua (A4 o Letter) con avviso; `/Rotate` non multiplo di 90 → arrotondato; pagina con milioni di oggetti vettoriali → resa annullabile e interfaccia reattiva; font non incorporati → sostituzione con metriche compatibili e segnalazione nelle proprietà (VIS-40); schermi HiDPI, scala frazionaria e più monitor con dpi diversi → resa nitida alla densità reale di ciascuno.
**Criteri di accettazione:**
- Dato il corpus con immagini di riferimento approvate, quando ogni pagina è resa a 150 dpi, allora differisce dal riferimento per non più dell'1% dei pixel con tolleranza di colore ΔE ≤ 3.
- Dato un documento di 5.000 pagine, quando l'utente lo scorre dall'inizio alla fine, allora il thread dell'interfaccia non resta bloccato per più di 100 ms e la memoria del processo non supera il limite configurato (predefinito 1 GB) più il 10%.
- Dato un documento con pagine A4 verticali e A3 orizzontali alternate, quando è mostrato in modalità continua, allora ogni pagina ha il rapporto d'aspetto della propria `/CropBox` ruotata.
- Data un'annotazione con flag Hidden o NoView, quando la pagina è resa a schermo, allora l'annotazione non è visibile.
- Dato uno schermo con scala 200%, quando una pagina è mostrata al 100%, allora la resa avviene a densità doppia (identica alla resa a 192 dpi) e non è un ingrandimento della resa a 96 dpi.
**Priorità:** P0

### VIS-09 — Modalità di visualizzazione delle pagine
**Descrizione:** l'utente sceglie come disporre le pagine: pagina singola (una alla volta), singola continua, doppia pagina affiancata, doppia continua, con o senza copertina separata, con verso di lettura da destra a sinistra; opzionalmente scorrimento orizzontale.
**Flusso:** 1. Menu Vista → Layout oppure pulsanti nella barra di stato. 2. Applicazione immediata mantenendo in vista la pagina corrente. 3. "Copertina separata" mostra la pagina 1 da sola, poi le coppie 2-3, 4-5. 4. Il layout è ricordato per documento (VIS-02); il predefinito si imposta nelle preferenze (VIS-51), insieme a spazio tra le pagine e ombre.
**Formati/strutture:** `/PageLayout` (SinglePage, OneColumn, TwoColumnLeft/Right, TwoPageLeft/Right) letto all'apertura; `/ViewerPreferences /Direction /R2L` per la rilegatura a destra.
**Casi limite:** coppie con pagine di altezza diversa → centrate verticalmente; documento di una pagina in doppia → pagina centrata; numero di pagine dispari → ultima pagina da sola; copertina con verso RTL → coppie speculari; cambio di layout con zoom "adatta pagina" → zoom ricalcolato.
**Criteri di accettazione:**
- Dato un documento di 10 pagine in doppia pagina con copertina, quando è visualizzato, allora le righe sono [1], [2,3], [4,5], [6,7], [8,9], [10].
- Dato lo stesso documento con `/Direction /R2L`, quando è visualizzato in doppia pagina con copertina, allora la pagina 2 è a destra della pagina 3.
- Dato l'utente a pagina 57 in modalità continua, quando passa a pagina singola, allora resta visualizzata la pagina 57.
- Dato un PDF con `/PageLayout /TwoColumnLeft` e la preferenza "rispetta le impostazioni del documento" attiva, quando viene aperto, allora la modalità è doppia continua senza copertina.
**Priorità:** P0

### VIS-10 — Zoom
**Descrizione:** ingrandisce e riduce la vista con livelli predefiniti, percentuale libera, adattamenti (pagina, larghezza, altezza, contenuto), dimensione reale, Ctrl+rotella, gesto di pizzico, zoom su un'area tracciata e zoom dinamico.
**Flusso:** 1. Comandi Ctrl++ e Ctrl+-; Ctrl+0 adatta pagina; Ctrl+1 dimensione reale; Ctrl+2 adatta larghezza; campo percentuale modificabile con elenco (10, 25, 50, 75, 100, 125, 150, 200, 400, 800, 1600, 3200, 6400). 2. Ctrl+rotella o pizzico sul touchpad: zoom continuo centrato sul puntatore. 3. Strumento "Zoom area": si traccia un rettangolo e la vista vi si adatta. 4. Gli adattamenti restano attivi quando la finestra cambia dimensione. 5. Lo zoom è ricordato per documento; il predefinito è nelle preferenze.
**Formati/strutture:** dimensione reale = lato della `/CropBox` × `/UserUnit` ÷ 72 × dpi del monitor; "adatta contenuto" usa l'ingombro degli oggetti visibili.
**Casi limite:** limiti 1%–6400% (oltre il limite il comando non ha effetto e lo segnala); pagine di dimensioni miste con "adatta pagina" → adattamento alla pagina corrente (opzione: alla pagina più grande); zoom elevato su pagina enorme → resa a tasselli (VIS-08); dpi del monitor errati → calibrazione manuale con un righello a schermo; eventi ad alta frequenza dal touchpad → zoom fluido senza accodare rese inutili.
**Criteri di accettazione:**
- Dato "adatta larghezza" attivo, quando la finestra viene ridimensionata, allora la larghezza della pagina corrente coincide con quella dell'area visibile (±2 px) senza barra di scorrimento orizzontale.
- Dato un monitor a 96 dpi e una pagina A4, quando si sceglie "dimensione reale", allora la pagina misura 794 × 1123 px (±1).
- Dato il puntatore sopra una parola, quando l'utente usa Ctrl+rotella in avanti, allora lo zoom aumenta di un passo e la parola resta sotto il puntatore (±5 px).
- Dato il valore 333 digitato nel campo zoom, quando si preme Invio, allora lo zoom è 333%.
- Dato lo strumento Zoom area, quando si traccia un rettangolo, allora l'area tracciata riempie la vista lungo la dimensione limitante.
**Priorità:** P0

### VIS-11 — Rotazione della vista
**Descrizione:** ruota temporaneamente la visualizzazione di 90° in senso orario o antiorario, per leggere scansioni o tavole orizzontali, senza modificare il file.
**Flusso:** 1. Vista → Ruota vista in senso orario (Ctrl+Maiusc++) o antiorario (Ctrl+Maiusc+-). 2. La vista si aggiorna mantenendo la pagina corrente. 3. Per una rotazione permanente si usa "Ruota pagine" (area Gestione pagine), proposto anche da qui.
**Formati/strutture:** `/Rotate` non viene modificato; la rotazione della vista si somma a quella della pagina.
**Casi limite:** annotazioni con flag NoRotate restano diritte rispetto allo schermo; selezione e ricerca funzionano nella vista ruotata; la stampa usa l'orientamento del documento salvo opzione esplicita; in doppia pagina le coppie restano invariate.
**Criteri di accettazione:**
- Dato un documento senza modifiche, quando la vista viene ruotata di 90°, allora il documento non risulta modificato e Salva non scrive nulla.
- Data la vista ruotata di 90°, quando l'utente seleziona un paragrafo, allora il testo copiato coincide con quello della stessa selezione a 0°.
- Date 4 rotazioni orarie consecutive, quando sono completate, allora la vista è identica a quella iniziale.
**Priorità:** P1

### VIS-12 — Navigazione tra le pagine
**Descrizione:** l'utente si sposta nel documento con pulsanti, campo "pagina N di M" (accetta numeri ed etichette), tastiera, rotella, barre di scorrimento, strumento mano e comandi prima/ultima/precedente/successiva; la barra di stato mostra pagina ed etichetta correnti.
**Flusso:** 1. Campo pagina o Ctrl+G: si digita "12" o un'etichetta come "iv" o "A-3" e si salta. 2. Tastiera: PagSu/PagGiù (una schermata), Home/Fine (inizio/fine), frecce (scorrimento), Spazio e Maiusc+Spazio, Ctrl+PagSu/PagGiù (pagina precedente/successiva). 3. Strumento mano (o barra spaziatrice tenuta premuta) per trascinare la vista; clic centrale per lo scorrimento automatico locale.
**Formati/strutture:** `/PageLabels` (number tree con `/S` D, R, r, A, a, `/P` prefisso, `/St` valore iniziale) per mostrare e interpretare le etichette; il valore digitato è cercato prima tra le etichette e poi come numero fisico; il prefisso "#" forza il numero fisico.
**Casi limite:** etichette duplicate → primo risultato, Invio ripetuto passa al successivo; numero fuori intervallo → campo segnalato in errore senza salto; documento di una pagina → precedente/successiva disabilitati; in doppia pagina "successiva" avanza di una coppia; verso RTL → frecce sinistra/destra invertite.
**Criteri di accettazione:**
- Dato un documento con etichette i–x seguite da 1–100, quando si digita "iv", allora si va alla pagina fisica 4 e la barra mostra "iv (4 di 110)".
- Dato lo stesso documento, quando si digita "#15", allora si va alla pagina fisica 15, che mostra l'etichetta "5".
- Dato l'utente sull'ultima pagina, quando osserva i comandi, allora "pagina successiva" è disabilitato.
- Dato "999" in un documento di 120 pagine, quando si preme Invio, allora la vista non cambia e il campo segnala l'errore.
**Priorità:** P0

### VIS-13 — Cronologia di navigazione (Indietro/Avanti)
**Descrizione:** come in un browser, l'utente torna alla posizione precedente dopo aver seguito un link, un segnalibro, un risultato di ricerca, un salto di pagina o una miniatura, e può poi tornare avanti.
**Flusso:** 1. Ogni salto non continuo registra la posizione di partenza (pagina, punto in coordinate della pagina, zoom). 2. Alt+Sinistra e Alt+Destra, pulsanti della barra o tasti Indietro/Avanti del mouse. 3. Un nuovo salto dopo essere tornati indietro elimina la parte "avanti". 4. Cronologia per scheda con limite configurabile (predefinito 100 voci). 5. Un link verso un altro documento registra anche la scheda di origine: Indietro vi riporta.
**Formati/strutture:** posizioni in coordinate dello spazio utente PDF, indipendenti da zoom e layout.
**Casi limite:** lo scorrimento continuo non crea voci; voce riferita a una pagina poi eliminata → riportata alla pagina più vicina; scheda di origine chiusa → voce saltata.
**Criteri di accettazione:**
- Dato l'utente a pagina 3 che segue un link verso pagina 40, quando preme Alt+Sinistra, allora torna a pagina 3 nella stessa posizione e con lo stesso zoom; con Alt+Destra torna a pagina 40.
- Dato l'utente che scorre con la rotella da pagina 1 a 10 senza salti, quando osserva Indietro, allora il comando è disabilitato.
- Data la sequenza salto A→B, Indietro, salto verso C, quando si osserva Avanti, allora il comando è disabilitato.
**Priorità:** P1

### VIS-14 — Pannello miniature
**Descrizione:** pannello laterale con le anteprime delle pagine per orientarsi e saltare rapidamente; mostra la pagina corrente e l'area visibile, ha dimensione regolabile ed è il punto di partenza delle operazioni sulle pagine (area Gestione pagine).
**Flusso:** 1. Apertura del pannello (F4 o icona). 2. Miniature generate in modo asincrono solo per quelle visibili, con etichetta o numero sotto ciascuna. 3. Clic → salto registrato in cronologia. 4. Sulla miniatura corrente un rettangolo indica l'area visibile e si può trascinare per spostare la vista. 5. Ctrl+rotella o cursore per la dimensione; selezione multipla con Maiusc/Ctrl per le operazioni sulle pagine.
**Formati/strutture:** `/Thumb` incorporate usate se presenti (opzione per ignorarle), altrimenti resa ridotta in cache; proporzioni da `/CropBox` e `/Rotate`.
**Casi limite:** 10.000 pagine → solo le miniature visibili sono generate; dimensioni miste → miniature proporzionali in una griglia comune; pagina illeggibile → segnaposto; `/Thumb` non aggiornate rispetto alla pagina → rigenerate; documento cifrato → miniature solo in memoria.
**Criteri di accettazione:**
- Dato un documento di 10.000 pagine, quando si scorre il pannello fino in fondo, allora il pannello resta reattivo e le miniature visibili compaiono entro 1 s dall'arresto dello scorrimento.
- Dato un clic sulla miniatura 25, quando è completato, allora la vista principale mostra la pagina 25 e la miniatura 25 è evidenziata.
- Dato il rettangolo dell'area visibile trascinato sulla miniatura, quando viene rilasciato, allora la vista principale si sposta nella posizione corrispondente.
- Dato un PDF cifrato, quando le miniature vengono generate, allora nessuna immagine di miniatura viene scritta su disco.
**Priorità:** P0

### VIS-15 — Segnalibri: consultazione
**Descrizione:** pannello con l'albero dei segnalibri (struttura del documento) per navigare per capitoli, con evidenziazione della sezione in cui si trova la vista.
**Flusso:** 1. Apertura del pannello Segnalibri (automatica se `/PageMode /UseOutlines` e la preferenza lo consente). 2. Albero con nodi espandibili nello stato aperto/chiuso salvato nel file. 3. Clic su una voce → esecuzione della destinazione o dell'azione, registrata in cronologia. 4. Espandi/comprimi tutto, espandi fino al livello N, filtro per testo nei titoli, opzione "evidenzia il segnalibro corrente" sincronizzata con lo scorrimento. 5. Da tastiera: frecce per muoversi, Invio per seguire.
**Formati/strutture:** `/Outlines` (`/First`, `/Last`, `/Count`); voci con `/Title`, `/Parent`, `/Prev`, `/Next`, `/Count` (negativo = chiusa), `/Dest` o `/A`, `/SE`, `/C` (colore), `/F` (bit 1 corsivo, bit 2 grassetto); destinazioni esplicite `/XYZ`, `/Fit`, `/FitH`, `/FitV`, `/FitR`, `/FitB`, `/FitBH`, `/FitBV` e nominate (`/Dests`, `/Names /Dests`); titoli in PDFDocEncoding, UTF-16BE e UTF-8 (PDF 2.0).
**Casi limite:** alberi con migliaia di voci o profondità oltre 20 → caricamento dei figli su richiesta; cicli o puntatori errati → albero troncato senza blocchi, con avviso; destinazione inesistente → voce marcata e messaggio al clic; azioni non di navigazione (URI, Launch) → regole di VIS-19; titolo vuoto → "(senza titolo)"; `/XYZ` con parametri null → i parametri null mantengono zoom o posizione correnti.
**Criteri di accettazione:**
- Dato un segnalibro con destinazione `/XYZ 72 720 2` sulla pagina 10, quando viene cliccato, allora la vista mostra la pagina 10 al 200% con il punto (72, 720) nell'angolo superiore sinistro.
- Dato un segnalibro con `/Count` negativo, quando l'albero è mostrato, allora la voce è chiusa; con `/Count` positivo è aperta.
- Dato un segnalibro con `/F 3` e `/C [1 0 0]`, quando è mostrato, allora il titolo è in grassetto corsivo e di colore rosso.
- Data la vista nella sezione del capitolo 3 con "evidenzia il segnalibro corrente" attivo, quando l'utente scorre al suo interno, allora la voce "Capitolo 3" resta evidenziata.
- Dato un albero di 5.000 voci, quando si apre il pannello, allora il primo livello è visibile entro 500 ms.
**Priorità:** P0

### VIS-16 — Segnalibri: creazione e modifica
**Descrizione:** l'utente aggiunge segnalibri sulla vista corrente o dal testo selezionato, li rinomina, li elimina, li riordina e li annida per trascinamento, ne imposta stile, colore, destinazione o azione.
**Flusso:** 1. Aggiungi segnalibro (Ctrl+B) → nuova voce dopo quella selezionata, con titolo uguale al testo selezionato o "Pagina N" e destinazione uguale alla vista corrente (pagina, posizione, zoom; preferenza "eredita lo zoom"). 2. Rinomina con F2 o doppio clic. 3. Trascinamento per riordinare o annidare; comandi Aumenta/Riduci livello. 4. Proprietà: titolo, colore, grassetto/corsivo, destinazione ("imposta sulla vista corrente"), azione (vai a pagina, apri URL, apri file; azioni avanzate nell'area Moduli). 5. Elimina, con i figli, previa conferma. 6. Annulla/Ripeti per ogni operazione; le modifiche si salvano con il documento.
**Formati/strutture:** scrittura di `/Outlines` con `/Count` coerenti, `/Dest` esplicite o nominate, `/A`; titoli in PDFDocEncoding quando possibile, altrimenti UTF-16BE.
**Casi limite:** documento senza segnalibri → creazione di `/Outlines`; pagina di destinazione eliminata in seguito → segnalibro marcato come rotto, mai rimosso in silenzio; titoli con emoji o testo RTL conservati; permessi che vietano la modifica → comandi disabilitati; documento firmato → avviso che la modifica sarà salvata come revisione incrementale e risulterà dopo la firma.
**Criteri di accettazione:**
- Dato un documento a pagina 7, quando l'utente aggiunge un segnalibro, lo chiama "Introduzione" e salva, allora alla riapertura "Introduzione" porta alla pagina 7.
- Dato un segnalibro trascinato sotto un altro, quando si salva e si riapre, allora risulta figlio di quest'ultimo e il `/Count` del genitore è coerente.
- Dato il titolo "Résumé — 概要", quando viene salvato e riletto, allora il titolo è identico.
- Data l'eliminazione di un segnalibro con 3 figli, quando l'utente annulla, allora la voce e i figli tornano nella posizione originale.
**Priorità:** P1

### VIS-17 — Segnalibri: generazione automatica e strumenti avanzati
**Descrizione:** crea automaticamente un albero di segnalibri multilivello dalla struttura logica (titoli taggati), dallo stile del testo (dimensione, grassetto, posizione), da un sommario presente nelle pagine, da un'espressione regolare, ogni N pagine oppure tramite analisi con modello linguistico (IA); offre strumenti di manutenzione: verifica e riparazione dei segnalibri rotti, unione dei duplicati, cambio maiuscole/minuscole, trova e sostituisci nei titoli, esportazione e importazione come testo, pagina di sommario cliccabile generata dai segnalibri, pannello delle destinazioni nominate.
**Flusso:** 1. Segnalibri → Genera automaticamente → scelta del metodo → anteprima dell'albero proposto, modificabile. 2. Conferma: sostituisci o aggiungi all'albero esistente. 3. Strumenti → Verifica segnalibri → elenco dei problemi con correzione proposta. 4. Esporta/Importa in testo rientrato con numeri di pagina. 5. Crea pagina di sommario → pagina inserita in testa con link e stile configurabile. 6. Pannello Destinazioni: elenco, vai a, rinomina, elimina quelle inutilizzate, converti segnalibri in destinazioni nominate e viceversa. 7. Il metodo IA richiede consenso esplicito e un servizio di modelli linguistici configurato (locale o remoto) e mostra quali dati saranno inviati.
**Formati/strutture:** `/StructTreeRoot` con elementi `/H`, `/H1`–`/H6`, `/Title`; attributi dei font del testo estratto; `/Outlines`; `/Dests` e `/Names /Dests`; formato testo di scambio "titolo<TAB>pagina" con rientro per livello.
**Casi limite:** documento scansionato senza testo → proposta di OCR (area OCR); nessun titolo rilevato → messaggio e nessuna modifica; servizio IA non configurato o irraggiungibile → metodo disabilitato con spiegazione, gli altri metodi restano disponibili offline; preferenza "non inviare documenti a servizi remoti" → IA remota bloccata; righe di importazione con pagine fuori intervallo → segnalate e scartate.
**Criteri di accettazione:**
- Dato un PDF taggato con 5 elementi H1 e 12 H2, quando si generano i segnalibri dalla struttura, allora l'albero ha 5 voci di primo livello e 12 di secondo con destinazioni sulle pagine corrette.
- Dato un file di testo con righe rientrate su 3 livelli e numeri di pagina, quando viene importato, allora l'albero ha la stessa gerarchia e le stesse destinazioni.
- Dato un segnalibro che punta a una destinazione nominata inesistente, quando si esegue Verifica, allora è elencato come rotto con la proposta di reindirizzarlo o rimuoverlo.
- Data l'IA remota disabilitata nelle preferenze, quando si apre la generazione automatica, allora il metodo IA non è selezionabile e non avviene alcuna connessione di rete.
- Data "Crea pagina di sommario", quando l'operazione è completata, allora ogni riga del sommario è un link alla pagina del segnalibro corrispondente con numeri di pagina che tengono conto della pagina inserita.
**Priorità:** P2

### VIS-18 — Riassunto di una sezione dal segnalibro (IA)
**Descrizione:** dal menu contestuale di un segnalibro l'utente ottiene un riassunto sintetico della sezione corrispondente (dalla destinazione del segnalibro alla voce successiva di pari livello) e può salvarlo come nota.
**Flusso:** 1. Clic destro sul segnalibro → Riassumi sezione. 2. Al primo uso: consenso e indicazione del servizio impiegato. 3. Estrazione del testo della sezione in ordine di lettura e invio al servizio di modelli linguistici configurato. 4. Riassunto in un pannello laterale con punti chiave e riferimenti alle pagine; azioni Copia, Inserisci come nota (area Annotazioni), Rigenera.
**Formati/strutture:** intervallo di pagine dalla destinazione del segnalibro a quella della voce successiva di pari livello (o fine documento); testo estratto come in VIS-28; lingua del riassunto = lingua dell'interfaccia o scelta dall'utente.
**Casi limite:** sezione senza testo → proposta di OCR; sezione oltre il limite del servizio → riassunto a blocchi con avviso; servizio irraggiungibile → errore chiaro entro il timeout; documento con estrazione del contenuto non consentita → funzione disabilitata salvo password proprietario; ultimo segnalibro → sezione fino alla fine del documento.
**Criteri di accettazione:**
- Dato il segnalibro "Capitolo 2" a pagina 10 seguito da "Capitolo 3" a pagina 19, quando si chiede il riassunto, allora il testo inviato a un servizio simulato comprende solo le pagine 10–18.
- Dato il servizio irraggiungibile, quando si chiede il riassunto, allora compare un messaggio d'errore entro il timeout configurato e l'interfaccia resta utilizzabile.
- Dato un PDF con copia ed estrazione non consentite, quando si apre il menu del segnalibro, allora "Riassumi sezione" è disabilitato con il motivo.
**Priorità:** P2

### VIS-19 — Collegamenti e azioni di navigazione
**Descrizione:** link interni ed esterni funzionano con un clic: salto a pagine e destinazioni, apertura di altri PDF o di allegati incorporati, URL nel browser predefinito, indirizzi e-mail; prima del clic l'utente vede dove porta il link; gli URL presenti come semplice testo possono diventare cliccabili.
**Flusso:** 1. Passaggio del puntatore su un link → cursore a mano e suggerimento con la destinazione (pagina ed etichetta, URL completo o file). 2. Clic → esecuzione: GoTo → salto registrato in cronologia; URI → conferma per i domini non attendibili (consenti una volta, consenti sempre per il dominio, blocca), poi browser predefinito; mailto: → client di posta predefinito; GoToR → apre il file (percorso relativo al documento) in una nuova scheda sulla destinazione; GoToE → apre l'allegato incorporato; Named (NextPage, PrevPage, FirstPage, LastPage) → eseguite; Launch → bloccata per impostazione predefinita, sbloccabile per singolo documento con conferma; SetOCGState → applicata ai livelli; JavaScript → solo se abilitato (area Moduli). 3. Evidenziazione al clic secondo `/H`. 4. Tab e Maiusc+Tab spostano il fuoco tra i link della pagina, Invio li segue. 5. Preferenza "rileva URL nel testo" per rendere cliccabili indirizzi web ed e-mail non marcati come link.
**Formati/strutture:** `/Subtype /Link` (`/Rect`, `/QuadPoints`, `/Dest`, `/A`, `/H` None/Invert/Outline/Push, `/PA`); azioni GoTo, GoToR (`/F`, `/D`, `/NewWindow`), GoToE (`/T`), GoToDp (PDF 2.0), Launch, URI (`/URI`, `/IsMap`, base `/URI /Base` del catalogo), Named, SetOCGState, catene `/Next`; ordine di tabulazione `/Tabs` della pagina.
**Casi limite:** destinazione inesistente → "Destinazione non trovata" senza salto; file GoToR mancante → messaggio con il percorso e "Individua…"; URL malformati o con schemi pericolosi (file:, javascript:, data:) → bloccati con avviso; link sovrapposti → vince quello disegnato per ultimo; pagine ruotate → area cliccabile ruotata di conseguenza; `/QuadPoints` non valide → si usa `/Rect`; catene `/Next` cicliche → interrotte; link attivi anche in presentazione e schermo intero.
**Criteri di accettazione:**
- Dato un link GoTo verso la pagina 12 con `/FitH 500`, quando viene cliccato, allora la pagina 12 è adattata in larghezza con y = 500 sul bordo superiore e la cronologia contiene la posizione di partenza.
- Dato un link URI verso un dominio non attendibile, quando viene cliccato, allora compare la conferma con l'URL completo e il browser non si apre finché l'utente non consente.
- Dato un link con azione Launch, quando viene cliccato, allora nessun processo viene avviato e compare la spiegazione del blocco.
- Dato un link GoToR a "altro.pdf" con destinazione nominata "capitolo2" e il file nella stessa cartella, quando viene cliccato, allora altro.pdf si apre in una nuova scheda sulla destinazione "capitolo2".
- Data la preferenza "rileva URL nel testo" attiva e il testo "www.esempio.it" non marcato come link, quando l'utente vi fa clic, allora viene trattato come link URI con la stessa conferma.
- Data una pagina con 5 link, quando l'utente preme Tab 5 volte, allora il fuoco visita i 5 link nell'ordine definito da `/Tabs` (o in ordine di lettura se assente).
**Priorità:** P0

### VIS-20 — Livelli (contenuto opzionale): visualizzazione
**Descrizione:** pannello Livelli con l'albero dei livelli del documento (tavole tecniche, mappe, versioni multilingue) per mostrarli o nasconderli rispettando gruppi mutuamente esclusivi, livelli bloccati e configurazioni alternative, con ripristino dello stato iniziale.
**Flusso:** 1. Apertura del pannello (automatica con `/PageMode /UseOC`). 2. Albero secondo `/Order`, con gruppi etichettati. 3. Clic sull'icona di visibilità → mostra/nascondi; in un gruppo radio l'attivazione di un livello spegne gli altri; i livelli bloccati non cambiano stato. 4. Menu: Ripristina visibilità iniziale, Mostra tutti, Solo livelli della pagina corrente, Configurazione alternativa, Proprietà (sola lettura). 5. Lo stato di visibilità vale per la sessione; salvarlo come predefinito appartiene a VIS-21.
**Formati/strutture:** `/OCProperties` (`/OCGs`, `/D`, `/Configs`); OCG (`/Name`, `/Intent`, `/Usage` con `/View`, `/Print`, `/Export`, `/Zoom`, `/Language`); OCMD (`/OCGs`, `/P`, `/VE`); configurazione (`/BaseState`, `/ON`, `/OFF`, `/Order`, `/RBGroups`, `/Locked`, `/AS`, `/ListMode`); contenuto marcato `BDC /OC … EMC`; chiave `/OC` su XObject e annotazioni; azioni SetOCGState.
**Casi limite:** livelli dipendenti dallo zoom o dalla lingua (`/Usage` con `/AS`) → visibilità aggiornata automaticamente; livelli con lo stesso nome → trattati come distinti; livelli assenti da `/Order` → elencati in un gruppo "Altri" secondo `/ListMode`; OCMD con espressioni `/VE` annidate → valutate correttamente; centinaia di livelli → filtro per nome; in stampa vale `/Print` oppure, a scelta dell'utente, lo stato a schermo (VIS-43).
**Criteri di accettazione:**
- Dato un PDF con il livello "Arredi" ON e "Quote" OFF nella configurazione predefinita, quando viene aperto, allora "Quote" non è visibile; quando l'utente lo attiva, allora appare e il documento non risulta modificato.
- Dato un gruppo radio {IT, EN, DE} con IT attivo, quando l'utente attiva EN, allora IT e DE risultano spenti.
- Dato un livello presente in `/Locked`, quando l'utente tenta di cambiarne la visibilità, allora lo stato non cambia.
- Dato un OCMD con `/VE [/And L1 [/Not L2]]`, quando L1 è ON e L2 è OFF, allora il contenuto associato è visibile; con L2 ON non lo è.
- Dato un livello con `/Usage /Print /PrintState /OFF`, quando si stampa con le impostazioni del documento, allora il livello non compare nell'output anche se visibile a schermo.
**Priorità:** P1

### VIS-21 — Livelli: gestione
**Descrizione:** l'utente crea, rinomina, elimina, riordina e raggruppa i livelli; ne imposta le proprietà (visibile all'apertura, stampa, esportazione, blocco); salva lo stato corrente come predefinito; unisce più livelli e li appiattisce nel contenuto; assegna contenuti e annotazioni a un livello.
**Flusso:** 1. Pannello Livelli → Gestisci. 2. Nuovo, Rinomina, Elimina (scelta: elimina il contenuto oppure spostalo nel contenuto di base), trascinamento per riordinare o annidare. 3. Proprietà: nome, visibile all'apertura, stampa (sempre, mai, se visibile), esportazione, bloccato. 4. "Salva la visibilità corrente come predefinita". 5. Unisci livelli selezionati; Appiattisci livelli (i visibili diventano contenuto di base, i nascosti sono rimossi, con conferma). 6. Sposta oggetti o annotazioni selezionati su un livello (in coordinamento con l'area Editing).
**Formati/strutture:** scrittura di `/OCProperties`, OCG, `/D` (`/ON`, `/OFF`, `/Order`, `/Locked`, `/RBGroups`) e `/Usage`; riscrittura delle sequenze `BDC /OC … EMC` e delle chiavi `/OC` di XObject e annotazioni; vincoli PDF/A-2 e successivi (`/Name` obbligatorio per ogni configurazione, nessuna `/AS` in `/D`).
**Casi limite:** livello usato da OCMD o da azioni SetOCGState → riferimenti aggiornati o segnalati prima dell'eliminazione; documento dichiarato PDF/A → avviso se la modifica viola il profilo; documento firmato → avviso sull'effetto per le firme; appiattimento di livelli bloccati → conferma esplicita; modifica non consentita dai permessi → comandi disabilitati.
**Criteri di accettazione:**
- Dato un documento con i livelli A e B, quando l'utente imposta B come nascosto all'apertura e salva, allora alla riapertura B è OFF nella configurazione predefinita.
- Data l'unione di B in A, quando è completata, allora il contenuto prima marcato B è marcato A e B non compare più in `/OCGs`.
- Dato l'appiattimento con A visibile e B nascosto, quando il file è salvato, allora non contiene `/OCProperties`, il contenuto di A fa parte del contenuto di base, quello di B è rimosso e la resa coincide con la vista precedente all'appiattimento.
- Dato un PDF/A-2b, quando si crea un livello e si salva, allora la configurazione predefinita contiene `/Name` e non contiene `/AS`.
**Priorità:** P2

### VIS-22 — Allegati e portfolio: consultazione
**Descrizione:** pannello Allegati con i file incorporati nel documento (a livello di documento e come annotazioni di pagina) con nome, descrizione, dimensione, date e relazione col documento; l'utente li apre o li salva; i portfolio PDF (collezioni) si aprono in una vista dedicata con l'elenco dei file.
**Flusso:** 1. Apertura del pannello (automatica con `/PageMode /UseAttachments` o se il documento è una collezione). 2. Elenco con colonne ordinabili; per gli allegati di pagina è indicata la pagina (clic → salto). 3. Apri: i PDF in una nuova scheda; gli altri tipi, dopo un avviso di sicurezza, con l'applicazione predefinita del sistema su una copia temporanea. 4. Salva come / Salva tutti. 5. Portfolio: vista elenco o griglia con i campi dello schema, cartelle navigabili, documento iniziale, ricerca per nome; i PDF contenuti si aprono in schede. L'aggiunta e la modifica degli allegati appartengono all'area Annotazioni/Protezione.
**Formati/strutture:** name tree `/Names /EmbeddedFiles`; file specification (`/F`, `/UF`, `/Desc`, `/EF`, `/AFRelationship`); embedded file stream (`/Subtype` tipo MIME, `/Params` con `/Size`, `/CreationDate`, `/ModDate`, `/CheckSum`); `/AF`; annotazioni `/FileAttachment`; `/Collection` (`/Schema`, `/D`, `/View`, `/Sort`, `/Folders`) e `/CI`.
**Casi limite:** allegati eseguibili o script → apertura bloccata per impostazione predefinita, solo "Salva come"; nomi con percorsi o "../" → ripuliti al salvataggio; allegati cifrati con filtro dedicato (`/EFF`) → richiesta della password se necessaria; checksum non corrispondente → avviso; allegati molto grandi → salvataggio progressivo con avanzamento; collezione con navigatore deprecato → vista elenco standard; PDF/A-3 con dati strutturati (es. fattura XML) → relazione `/AFRelationship` mostrata.
**Criteri di accettazione:**
- Dato un PDF con 3 file incorporati, quando si apre il pannello, allora sono elencati 3 elementi con nome, dimensione e descrizione.
- Dato un allegato PDF, quando si sceglie Apri, allora si apre in una nuova scheda; quando si sceglie Salva come, allora il file scritto è byte-identico al flusso incorporato decodificato.
- Dato un allegato con estensione eseguibile, quando si sceglie Apri, allora l'apertura è bloccata ed è offerto solo "Salva come".
- Dato un portfolio con 2 cartelle e 5 file, quando viene aperto, allora la vista mostra cartelle e file navigabili ed evidenzia il documento iniziale indicato da `/D`.
**Priorità:** P1

### VIS-23 — Thread di articoli
**Descrizione:** nei documenti impaginati con articoli su più colonne e pagine (riviste, giornali) l'utente segue il percorso di lettura di un articolo riquadro dopo riquadro, senza cercare a mano la continuazione.
**Flusso:** 1. Il pannello Articoli elenca gli articoli con il titolo. 2. Clic su un articolo o su un suo riquadro con lo strumento di lettura → zoom sul primo riquadro adattato in larghezza. 3. Invio o clic → riquadro successivo, anche su un'altra pagina; Maiusc+Invio → precedente; Esc → fine della lettura.
**Formati/strutture:** `/Threads` nel catalogo; thread (`/F`, `/I`); bead (`/T`, `/N`, `/V`, `/P`, `/R`); azione `/Thread`.
**Casi limite:** riquadro su una pagina eliminata → saltato; catena circolare → la lettura si ferma quando torna al primo riquadro; rettangoli fuori pagina → limitati alla `/CropBox`.
**Criteri di accettazione:**
- Dato un articolo con 3 riquadri sulle pagine 2, 2 e 5, quando l'utente avvia la lettura e preme Invio due volte, allora la vista mostra in sequenza i tre riquadri adattati in larghezza.
- Dato l'ultimo riquadro dell'articolo, quando l'utente preme Invio, allora compare l'indicazione di fine articolo e la vista non cambia pagina.
**Priorità:** P2

### VIS-24 — Ricerca nel documento
**Descrizione:** barra di ricerca (Ctrl+F) che trova parole o frasi, evidenzia tutte le occorrenze, scorre tra i risultati e li elenca con il contesto, con opzioni maiuscole/minuscole, parola intera e accenti.
**Flusso:** 1. Ctrl+F → barra con campo precompilato con l'eventuale selezione. 2. Ricerca incrementale durante la digitazione, su tutto il documento a partire dalla pagina corrente, con ripresa dall'inizio. 3. Invio o F3 → successivo; Maiusc+Invio o Maiusc+F3 → precedente; contatore "3 di 42". 4. Opzioni: Maiuscole/minuscole, Parola intera, Ignora accenti, Evidenzia tutti. 5. Pannello Risultati raggruppato per pagina con estratto di contesto e termine in grassetto, numero ed etichetta di pagina; clic → salto ed evidenziazione, registrato in cronologia. 6. Esc chiude la barra e toglie le evidenziazioni; il campo ricorda le ultime ricerche.
**Formati/strutture:** testo estratto con mappatura Unicode (`/ToUnicode`, codifiche e `/Differences`, `/ActualText`); normalizzazione Unicode di compatibilità (legature come "ﬁ" → "fi", spazi speciali); ricomposizione delle parole sillabate a fine riga; ordine di lettura.
**Casi limite:** 5.000 pagine → ricerca in background con avanzamento, risultati mostrati man mano e annullamento; pagine senza testo (scansioni) → "N pagine senza testo: esegui l'OCR per cercarvi" (area OCR); testo RTL e CJK (per le scritture senza spazi "Parola intera" non si applica e lo si indica); frase che prosegue alla riga o alla pagina successiva → trovata; documento con copia non consentita → la ricerca funziona comunque; nessun risultato → campo evidenziato senza finestre modali.
**Criteri di accettazione:**
- Dato un documento con "Contratto" 3 volte e "contratto" 2 volte, quando si cerca "contratto" senza opzioni, allora i risultati sono 5; con "Maiuscole/minuscole" sono 2.
- Dato il testo "ricontrattare", quando si cerca "contratt" con "Parola intera", allora non ci sono risultati; senza l'opzione c'è 1 risultato.
- Dato "perché" nel documento, quando si cerca "perche" con "Ignora accenti", allora l'occorrenza è trovata.
- Dato "ﬁnanza" scritto con la legatura, quando si cerca "finanza", allora l'occorrenza è trovata.
- Data la parola "infor-" a fine riga seguita da "mazione" a inizio riga, quando si cerca "informazione", allora l'occorrenza è trovata ed evidenziata su entrambe le righe.
- Dato un documento di 2.000 pagine, quando si cerca una parola presente solo nell'ultima pagina, allora il risultato compare entro 5 s sulla macchina di riferimento e l'interfaccia resta reattiva.
- Dato un elemento del pannello Risultati, quando viene cliccato, allora la pagina corrispondente è mostrata con l'occorrenza evidenziata e visibile.
**Priorità:** P0

### VIS-25 — Ricerca avanzata e su più documenti
**Descrizione:** ricerca con espressioni regolari e operatori (frase esatta, tutte le parole, almeno una), estesa a commenti, segnalibri, campi modulo, allegati e metadati, sui documenti aperti, su una cartella con le sottocartelle o su un indice full-text creato dall'utente per ricerche istantanee su grandi archivi.
**Flusso:** 1. Ctrl+Maiusc+F → pannello Ricerca avanzata. 2. Ambito: documento corrente, documenti aperti, cartella, indice. 3. Modalità: frase, tutte le parole, almeno una, espressione regolare; opzioni di VIS-24 più "includi commenti, segnalibri, campi, allegati, metadati". 4. Risultati raggruppati per file e pagina con contesto; esportazione dei risultati in CSV. 5. Indici: crea, aggiorna (solo i file cambiati), elimina.
**Formati/strutture:** testo estratto; `/Contents` e `/RC` delle annotazioni; titoli di `/Outlines`; valori `/V` dei campi; PDF incorporati; dizionario Info e XMP; indice locale nel profilo utente.
**Casi limite:** espressione regolare non valida → errore nel campo; espressione con tempi patologici → limite di tempo per pagina; file cifrati o corrotti nella cartella → saltati ed elencati nel riepilogo; cartelle con 10.000 file → elaborazione in background annullabile; file indicizzati poi modificati o eliminati → risultati aggiornati o marcati; documenti cifrati non indicizzati salvo consenso esplicito.
**Criteri di accettazione:**
- Data l'espressione regolare `\b\d{5}\b`, quando si cerca in un documento con 3 CAP, allora i risultati sono 3.
- Data una cartella con 50 PDF di cui 2 cifrati, quando si cerca una parola, allora i risultati elencano i file con occorrenze e il riepilogo indica i 2 file saltati.
- Dato "includi commenti" attivo e un commento contenente "verificare", quando si cerca "verificare", allora il commento compare tra i risultati e il clic lo seleziona sulla pagina.
- Dato un indice di 1.000 documenti, quando si cerca una parola, allora i risultati compaiono entro 1 s sulla macchina di riferimento.
**Priorità:** P2

### VIS-26 — Trova e sostituisci, Trova ed elimina
**Descrizione:** l'utente cerca un testo nel contenuto delle pagine e lo sostituisce con un altro oppure lo elimina, su una singola occorrenza o su tutte, mantenendo font, dimensione, colore e posizione.
**Flusso:** 1. Ctrl+H → barra con "Trova" e "Sostituisci con" (vuoto = elimina) e le opzioni di VIS-24, inclusa l'espressione regolare. 2. Anteprima dei risultati in elenco, con casella per includere o escludere ogni occorrenza. 3. Sostituisci (corrente), Sostituisci tutto, Elimina tutto. 4. Riepilogo "N sostituzioni su M pagine, K non eseguite" con i motivi. 5. L'intera operazione si annulla con un solo Annulla.
**Formati/strutture:** riscrittura degli operatori di testo (`TJ`/`Tj`, `Td`/`Tm`, crenatura in `TJ`); mappatura inversa da Unicode ai codici tramite `/ToUnicode` ed `/Encoding`; aggiornamento di `/Widths`; incorporamento dei glifi mancanti nel sottoinsieme del font; conservazione di `/MCID` e della struttura logica; motore condiviso con l'area Editing testo.
**Casi limite:** sottoinsieme del font privo dei glifi necessari → font equivalente o sostituzione non eseguita con avviso, mai glifi mancanti; testo sostitutivo più lungo → riflusso nel blocco o avviso di sovrapposizione; testo che fa parte di un'immagine → non sostituibile, segnalato; testo in annotazioni o campi → escluso salvo opzione; documento firmato → avviso sulla validità delle firme; modifica non consentita dai permessi → comando disabilitato.
**Criteri di accettazione:**
- Dato un documento con 10 occorrenze di "ACME S.r.l." in un font incorporato completo, quando si sostituisce tutto con "Beta S.p.A.", allora il testo estratto contiene 10 volte "Beta S.p.A." nello stesso font e corpo e nessuna volta "ACME S.r.l.".
- Dato "Elimina tutto" sulla parola "BOZZA", quando è completato, allora la ricerca di "BOZZA" dà 0 risultati e il resto del testo delle righe interessate mantiene la posizione originale (±0,5 pt).
- Dato un sottoinsieme di font senza la lettera "Z" e un testo sostitutivo che la contiene, quando si sostituisce, allora l'occorrenza è resa con un font equivalente oppure segnalata come non eseguita, senza glifi mancanti.
- Dato "Sostituisci tutto" con 50 sostituzioni, quando l'utente preme Annulla una volta, allora il documento torna identico allo stato precedente.
**Priorità:** P1

### VIS-27 — Ricerca semantica (IA)
**Descrizione:** l'utente cerca per significato anziché per parole esatte (ad esempio "penali per ritardo" trova "sanzioni in caso di consegna tardiva") e ottiene passaggi ordinati per rilevanza, collegati alla pagina.
**Flusso:** 1. Nel pannello di ricerca si sceglie la modalità "Per significato". 2. Al primo uso: consenso, scelta del servizio di modelli linguistici (locale o remoto) e indicazione dei dati inviati. 3. Indicizzazione semantica del documento in background con avanzamento. 4. Query → passaggi con punteggio di rilevanza; clic → pagina con il passaggio evidenziato. 5. Possibilità di combinare con la ricerca esatta.
**Formati/strutture:** testo estratto diviso in passaggi con riferimento a pagina e coordinate; rappresentazioni vettoriali in cache locale legate all'identificativo del documento (`/ID`) e alla data di modifica.
**Casi limite:** servizio non configurato → modalità non disponibile con spiegazione; documento modificato → indice semantico rigenerato; documento senza testo → proposta di OCR; documento cifrato → nessuna cache su disco salvo consenso; preferenza "nessun invio a servizi remoti" → solo servizio locale; lingua della domanda diversa da quella del documento → supportata se il servizio lo consente.
**Criteri di accettazione:**
- Dato un documento di prova con il passaggio "sanzioni in caso di consegna tardiva", quando si cerca "penali per ritardo" con il servizio di riferimento, allora il passaggio è tra i primi 3 risultati.
- Data la preferenza "nessun invio a servizi remoti" e solo un servizio remoto configurato, quando l'utente sceglie la modalità per significato, allora la modalità è disabilitata e non parte alcuna richiesta di rete.
- Dato un risultato semantico, quando viene cliccato, allora la vista mostra la pagina con il passaggio evidenziato.
**Priorità:** P2

### VIS-28 — Selezione e copia del testo
**Descrizione:** l'utente seleziona testo con il mouse o la tastiera, anche su più colonne e più pagine, e lo copia negli appunti con caratteri Unicode corretti e nell'ordine di lettura; può selezionare una parola (doppio clic), una riga o un paragrafo (triplo clic), un'area rettangolare (Alt+trascinamento) o tutto (Ctrl+A).
**Flusso:** 1. Strumento di selezione (predefinito: selezione testo sopra il testo, mano sugli spazi vuoti). 2. Trascinamento: la selezione segue l'ordine di lettura; oltre il bordo la vista scorre da sola e la selezione attraversa le pagine. 3. Maiusc+clic o Maiusc+frecce estendono la selezione; F7 attiva il cursore di testo per selezionare solo da tastiera. 4. Ctrl+C: gli appunti ricevono testo semplice e, se disponibile, una versione con formattazione di base. 5. Menu contestuale: Copia, Seleziona tutto, Cerca (VIS-24), Leggi ad alta voce (VIS-35), Evidenzia/Sottolinea (area Annotazioni), Aggiungi segnalibro (VIS-16).
**Formati/strutture:** `/ToUnicode`, codifiche e `/Differences`; `/ActualText` che sostituisce il testo del contenuto marcato; ordine di lettura dalla struttura logica (`/StructTreeRoot`, `/MCID`) per i PDF taggati, altrimenti analisi del layout (colonne, righe); contenuto `/Artifact` (intestazioni e piè di pagina) escludibile; spazi dedotti dalla distanza tra glifi; trattini di sillabazione a fine riga rimossi; legature espanse; testo bidirezionale in ordine logico; testo verticale CJK.
**Casi limite:** copia non consentita → VIS-06; glifi senza mappatura Unicode → avviso "testo non estraibile correttamente" con proposta di OCR della selezione (area OCR); testo invisibile da OCR (`Tr 3`) → selezionabile; testo ruotato o inclinato → selezione lungo la linea di base; Ctrl+A su migliaia di pagine → copia in background con indicatore e limite configurabile; pagine con milioni di caratteri → selezione fluida.
**Criteri di accettazione:**
- Data una pagina non taggata a due colonne, quando si seleziona dall'inizio della colonna sinistra alla fine della destra e si copia, allora gli appunti contengono tutto il testo della colonna sinistra seguito da quello della destra.
- Dato un PDF taggato con `/ActualText` "€" su un glifo decorativo, quando si copia la riga, allora gli appunti contengono "€".
- Data una parola spezzata a fine riga da un trattino di sillabazione, quando la si copia, allora gli appunti contengono la parola intera senza trattino.
- Dato un paragrafo in arabo, quando lo si copia, allora gli appunti contengono i caratteri nello stesso ordine logico della stringa sorgente del corpus.
- Data una selezione dalla pagina 3 alla pagina 5 con l'opzione "escludi artefatti" attiva, quando si copia, allora gli appunti contengono il testo delle tre pagine in ordine e nessuna intestazione o piè di pagina marcati come artefatti.
- Dato un Alt+trascinamento su una tabella, quando si copia, allora gli appunti contengono solo il testo interno al rettangolo, una riga per riga di tabella, con tabulazioni tra le colonne.
**Priorità:** P0

### VIS-29 — Istantanea di un'area
**Descrizione:** l'utente cattura un'area rettangolare come immagine, negli appunti o su file, alla risoluzione scelta, oppure ne copia il testo, anche tramite OCR se l'area non contiene testo.
**Flusso:** 1. Strumento Istantanea (o trascinamento con il tasto destro). 2. Tracciamento del rettangolo, con scorrimento automatico se supera la vista. 3. Menu: Copia immagine, Salva immagine (PNG, JPEG, TIFF), Copia testo, Estrai testo con OCR (area OCR). 4. Risoluzione: quella dello schermo o un valore tra 72 e 1200 dpi.
**Formati/strutture:** resa raster dell'area con i livelli visibili e, a scelta, le annotazioni; PNG con sfondo trasparente opzionale; testo come in VIS-28.
**Casi limite:** area a cavallo di due pagine in modalità continua → cattura della composizione visibile; vista ruotata → immagine come appare; area grande ad alta risoluzione → limite di 100 megapixel con avviso; copia ed estrazione non consentite dai permessi → strumento disabilitato con il motivo.
**Criteri di accettazione:**
- Dato un rettangolo di 2 × 1 pollici catturato a 300 dpi, quando si copia l'immagine, allora gli appunti contengono un'immagine di 600 × 300 px.
- Data un'area con testo, quando si sceglie Copia testo, allora gli appunti contengono lo stesso testo di una selezione rettangolare equivalente (VIS-28).
- Dato un PDF con copia non consentita, quando l'utente sceglie lo strumento, allora l'istantanea è disabilitata con il motivo.
**Priorità:** P1

### VIS-30 — Schede, finestre e sessioni
**Descrizione:** più documenti aperti contemporaneamente in schede, con riordino, chiusura, trascinamento verso altre finestre o fuori per crearne una nuova, gruppi di schede, riapertura della scheda chiusa, ripristino della sessione all'avvio e seconda vista dello stesso documento.
**Flusso:** 1. Ogni documento si apre in una scheda (preferenza: nuova finestra). 2. Ctrl+Tab e Ctrl+Maiusc+Tab per scorrere, Alt+1…9 per saltare. 3. Chiudi (Ctrl+W), Chiudi le altre, Chiudi quelle a destra, Chiudi tutte; Riapri scheda chiusa (Ctrl+Maiusc+T) nella stessa posizione. 4. Trascinamento per riordinare; fuori dalla barra → nuova finestra; su un'altra finestra → spostamento. 5. Gruppi di schede con nome e colore, comprimibili. 6. Suggerimento con il percorso completo, indicatore di modifiche non salvate, menu con l'elenco delle schede quando non stanno nella barra. 7. Preferenza "Ripristina la sessione all'avvio": finestre, schede, gruppi, pagina e zoom. 8. "Nuova vista del documento" → seconda scheda sullo stesso documento in memoria, con le modifiche visibili in entrambe.
**Formati/strutture:** sessione salvata nel profilo utente (percorsi, posizioni, gruppi), senza password né contenuti.
**Casi limite:** chiusura di una scheda con modifiche → Salva / Non salvare / Annulla (VIS-47); chiusura dell'applicazione con più documenti modificati → un solo dialogo con l'elenco; file della sessione non più esistente → scheda non ripristinata e notifica; documenti cifrati → la password viene richiesta di nuovo; 100 schede aperte → le schede inattive rilasciano la cache di resa; stesso file aperto due volte → si attiva la scheda esistente (VIS-05).
**Criteri di accettazione:**
- Dati 3 documenti aperti alle pagine 5, 10 e 15 con "Ripristina la sessione" attivo, quando l'applicazione viene chiusa e riaperta, allora le 3 schede tornano nello stesso ordine e alle stesse pagine.
- Data una scheda trascinata fuori dalla barra, quando viene rilasciata, allora si apre una nuova finestra con il documento nella stessa posizione e la scheda scompare dalla finestra d'origine.
- Data una scheda chiusa per errore, quando l'utente preme Ctrl+Maiusc+T, allora la scheda riappare con la stessa pagina e lo stesso zoom.
- Dati 2 documenti modificati, quando si chiude l'applicazione, allora compare un solo dialogo che li elenca entrambi con Salva tutti / Non salvare / Annulla.
- Dati 50 documenti aperti in schede, quando si passa da una scheda all'altra, allora la pagina della scheda attivata è visibile entro 300 ms.
**Priorità:** P0

### VIS-31 — Modalità lettura e schermo intero
**Descrizione:** la modalità lettura nasconde barre e pannelli lasciando nella finestra solo il documento; lo schermo intero occupa l'intero monitor con controlli minimi a scomparsa; entrambe conservano navigazione, zoom e link.
**Flusso:** 1. Menu Vista → Modalità lettura, oppure Schermo intero (F11). 2. In schermo intero una barra semitrasparente compare al movimento del mouse (pagina, zoom, layout, esci); tastiera e rotella restano attive. 3. Esc o F11 escono e ripristinano la disposizione precedente. 4. Preferenze: colore di sfondo, cursore nascosto dopo inattività, monitor da usare.
**Formati/strutture:** `/PageMode /FullScreen` richiesto dal documento all'apertura; `/ViewerPreferences /NonFullScreenPageMode` per il pannello da mostrare all'uscita.
**Casi limite:** più monitor → schermo intero sul monitor della finestra o su quello scelto; cambio di risoluzione durante lo schermo intero → adattamento immediato; documento che chiede lo schermo intero all'apertura → conferma dell'utente; dialoghi aperti in schermo intero → mostrati sopra la vista.
**Criteri di accettazione:**
- Dato un documento a pagina 8 al 120%, quando si entra e si esce dalla modalità lettura, allora la vista è ancora a pagina 8 al 120% e barre e pannelli tornano come prima.
- Dato lo schermo intero con il mouse fermo per 3 s, quando il tempo scade, allora cursore e controlli si nascondono e riappaiono al primo movimento.
- Dato un PDF con `/PageMode /FullScreen`, quando viene aperto, allora l'utente deve confermare prima del passaggio a schermo intero.
**Priorità:** P1

### VIS-32 — Presentazione
**Descrizione:** mostra il PDF come presentazione a schermo intero, una pagina alla volta, con avanzamento da tastiera, mouse o telecomando, transizioni e tempi definiti nel documento o scelti dall'utente, schermo nero o bianco, puntatore laser, penna temporanea e vista relatore su un secondo monitor.
**Flusso:** 1. Avvio dall'inizio (F5) o dalla pagina corrente (Maiusc+F5). 2. Avanti con clic, Spazio, Invio, frecce, PagGiù; indietro con frecce, PagSu, Backspace; numero + Invio per saltare a una pagina. 3. B schermo nero, W schermo bianco, L puntatore laser, P penna a mano libera temporanea, Esc per uscire. 4. Transizioni e avanzamento automatico da `/Trans` e `/Dur` del documento, oppure transizione e intervallo scelti dall'utente; ciclo continuo opzionale; passi all'interno della pagina (`/PresSteps`). 5. Vista relatore: pagina corrente e successiva, miniature, cronometro, ora, testo delle note della pagina.
**Formati/strutture:** `/Trans` (`/S` Split, Blinds, Box, Wipe, Dissolve, Glitter, R, Fly, Push, Cover, Uncover, Fade; `/D`, `/Dm`, `/M`, `/Di`, `/SS`, `/B`); `/Dur`; `/PresSteps` con `/NA`, `/PA`, `/Next`, `/Prev`; azioni SetOCGState per la comparsa progressiva dei livelli.
**Casi limite:** un solo monitor → nessuna vista relatore; transizione non supportata → dissolvenza; pagine verticali e orizzontali miste → ciascuna adattata al monitor; link e contenuti multimediali attivi; monitor collegato o scollegato durante la presentazione → adattamento senza interruzione.
**Criteri di accettazione:**
- Dato un PDF con `/Dur 5` su ogni pagina e avanzamento automatico abilitato, quando si avvia la presentazione, allora ogni pagina resta visibile 5 s (±0,3 s) prima della successiva.
- Dato lo schermo nero attivato con B, quando si preme un tasto qualsiasi, allora ricompare la stessa pagina.
- Dati due monitor e la vista relatore attiva, quando si avvia la presentazione, allora il monitor del pubblico mostra solo la pagina e quello del relatore pagina corrente, successiva e cronometro.
- Dato un tratto di penna disegnato in presentazione, quando si esce, allora il documento non risulta modificato.
**Priorità:** P1

### VIS-33 — Scorrimento automatico
**Descrizione:** la vista scorre da sola a velocità regolabile, oppure avanza di una pagina a intervalli fissi, per leggere senza usare le mani (spartiti, gobbo, documenti lunghi).
**Flusso:** 1. Menu Vista → Scorrimento automatico. 2. Modalità continua (pixel al secondo) o a pagine (secondi per pagina); velocità regolata con frecce su/giù o rotella, con indicatore. 3. Spazio mette in pausa e riprende; Esc o un clic interrompono; uno scorrimento manuale mette in pausa. 4. Arresto a fine documento oppure ripartenza dall'inizio (opzione).
**Formati/strutture:** nessuna struttura PDF; velocità e modalità salvate nelle preferenze.
**Casi limite:** layout a pagina singola → avanzamento a pagine; documento ricaricato durante lo scorrimento → interruzione; cambio di zoom durante lo scorrimento → velocità mantenuta in pixel di schermo; combinabile con modalità lettura e schermo intero.
**Criteri di accettazione:**
- Dato lo scorrimento automatico a 50 px/s, quando trascorrono 10 s, allora la vista si è spostata di 500 px (±5%).
- Dato lo scorrimento in corso, quando l'utente preme Spazio, allora la vista si ferma; premendo di nuovo riprende dalla stessa posizione.
- Dato lo scorrimento in corso, quando l'ultima pagina è interamente visibile, allora lo scorrimento si arresta.
**Priorità:** P1


## 2. Gestione pagine

### PAG-01 — Organizzatore pagine a griglia
**Descrizione:** Vista dedicata che mostra tutte le pagine del documento attivo come miniature in griglia e raccoglie i comandi di pagina (barra, menu contestuale, scorciatoie). Sotto ogni miniatura: numero fisico, etichetta di pagina se diversa (PAG-39), indicatori di rotazione, di formato diverso dalla maggioranza e di presenza di annotazioni. Dimensione delle miniature regolabile con cursore e zoom. Ogni operazione è annullabile e ripetibile; le modifiche al documento aperto sono scritte al salvataggio.
**Flusso:** 1. L'utente apre l'organizzatore dal documento attivo (comando o scorciatoia). 2. Seleziona pagine con clic, Ctrl+clic (aggiunge/toglie), Maiusc+clic (intervallo), riquadro di trascinamento, Ctrl+A, o con i filtri (pari, dispari, verticali, orizzontali, intervallo testuale PAG-02). 3. Applica un comando (ruota, elimina, estrai, duplica, sposta…) da barra, menu contestuale o tastiera. 4. La griglia si aggiorna subito; Annulla/Ripeti ripristinano lo stato precedente/successivo.
**Formati/strutture:** Albero delle pagine (`/Pages`, `/Kids`, `/Count`); attributi ereditabili (`/MediaBox`, `/CropBox`, `/Rotate`, `/Resources`) risolti per singola pagina; miniature generate al volo; eventuali miniature incorporate (`/Thumb`) rimosse o rigenerate quando la pagina cambia aspetto.
**Casi limite:** Documento di migliaia di pagine → rendering pigro (solo miniature visibili più un margine) con segnaposto, interfaccia sempre reattiva; pagina non renderizzabile (contenuto corrotto) → miniatura segnaposto con icona di errore, pagina comunque selezionabile e gestibile; permesso di assemblaggio negato → comandi strutturali disabilitati con il motivo nel suggerimento e possibilità di sbloccare con password proprietario; documento firmato → avviso alla prima modifica strutturale (PAG-03); pagine di formati diversi → miniature in proporzione reale.
**Criteri di accettazione:**
- Dato un documento di 10 pagine, quando l'utente fa clic sulla pagina 2 e Maiusc+clic sulla pagina 5, allora sono selezionate esattamente le pagine 2–5 e la barra di stato indica "4 pagine selezionate".
- Dato un documento di 5.000 pagine, quando si apre l'organizzatore e si scorre fino in fondo, allora il numero di miniature renderizzate contemporaneamente in memoria non supera quelle visibili più il margine configurato e la griglia risponde all'input entro i tempi dei requisiti non funzionali.
- Dato un qualsiasi comando di pagina eseguito, quando l'utente esegue Annulla, allora il documento ha lo stesso numero, ordine e contenuto (rendering identico) di pagine di prima del comando.
- Dato un PDF con permesso di assemblaggio negato aperto senza password proprietario, quando si apre l'organizzatore, allora i comandi elimina, inserisci, ruota e riordina sono disabilitati e il suggerimento ne indica il motivo.
**Priorità:** P0

### PAG-02 — Selezione e sintassi degli intervalli di pagine
**Descrizione:** Componente comune a tutte le funzioni dell'area per indicare le pagine su cui operare: pagina corrente, pagine selezionate nell'organizzatore, tutte, intervallo testuale, con filtri pari/dispari e verticali/orizzontali. Sintassi: numeri fisici a partire da 1 separati da virgola o punto e virgola; intervalli `a-b`; intervalli aperti `a-` (fino all'ultima) e `-b` (dalla prima); intervalli decrescenti `b-a`. Opzione per interpretare i valori come etichette di pagina (PAG-39). Il campo valida in tempo reale e mostra il numero di pagine risultante.
**Flusso:** 1. L'utente apre la finestra di un'operazione. 2. Sceglie l'ambito (corrente, selezionate, tutte, intervallo) ed eventualmente un filtro. 3. Digita l'intervallo; errori e conteggio compaiono subito. 4. Conferma: l'operazione riceve l'elenco ordinato degli indici di pagina.
**Formati/strutture:** Espressione testuale risolta in elenco di indici fisici; `/PageLabels` per l'interpretazione per etichetta.
**Casi limite:** Numero oltre l'ultima pagina → errore in linea che cita la pagina inesistente e il totale, conferma disabilitata; intervalli sovrapposti → per operazioni insiemistiche (ruota, elimina, ritaglia) unione senza duplicati, per operazioni ordinate (estrai in un file, unisci) ordine e ripetizioni mantenuti come scritti; intervallo decrescente → ordine inverso nelle operazioni ordinate, semplice insieme nelle altre; spazi superflui tollerati; espressione vuota → errore; filtro che produce un insieme vuoto → errore "nessuna pagina corrisponde"; etichetta ambigua (due pagine con la stessa etichetta) → errore con indicazione delle pagine fisiche coinvolte.
**Criteri di accettazione:**
- Dato un documento di 10 pagine, quando si inserisce "1-3, 8-", allora l'insieme risolto è {1,2,3,8,9,10} e il contatore mostra 6.
- Dato un documento di 10 pagine, quando si inserisce "4-12", allora la validazione fallisce con un messaggio che cita la pagina 12 e il pulsante di conferma è disabilitato.
- Dato l'intervallo "1-6" con filtro "dispari", quando viene risolto, allora l'insieme è {1,3,5}.
- Dato un documento con etichette i–iv seguite da 1–20 e l'interpretazione per etichette attiva, quando si inserisce "ii-2", allora sono risolte le pagine fisiche 2–6.
**Priorità:** P0

### PAG-03 — Integrità dei riferimenti nelle operazioni di pagina
**Descrizione:** Regole trasversali che ogni operazione strutturale (sposta, inserisci, elimina, duplica, sostituisci, estrai, dividi, unisci) deve rispettare perché il documento risultante resti coerente: segnalibri, collegamenti, destinazioni nominate, campi modulo, struttura logica (tag), livelli, thread di articoli, etichette, azione di apertura, firme e permessi.
**Flusso:** 1. Prima dell'operazione il sistema individua gli oggetti che fanno riferimento alle pagine coinvolte. 2. Se il documento è firmato o certificato mostra l'avviso di invalidazione e chiede conferma. 3. Esegue l'operazione aggiornando o rimuovendo i riferimenti secondo le regole sotto. 4. Registra un'unica voce di annullamento.
**Formati/strutture:** `/Outlines` (`/Dest`, `/A` GoTo), `/Annots` di tipo Link, `/Dests` e `/Names /Dests`, `/AcroForm /Fields` e widget (`/P`), `/StructTreeRoot` e `/ParentTree` (`/StructParents`), `/OCProperties`, `/Threads` e bead, `/PageLabels`, `/OpenAction`, `/AA` di pagina, `/Perms` (`/DocMDP`), bit di assemblaggio in `/Encrypt /P`. Regole: i riferimenti seguono l'oggetto pagina, non il numero; verso pagine eliminate i segnalibri restano ma senza destinazione e marcati come interrotti (opzione: rimuoverli), link e destinazioni nominate vengono rimossi, `/OpenAction` torna alla prima pagina; un campo i cui widget erano tutti su pagine eliminate è tolto da `/Fields`; elementi di struttura rimasti senza contenuto vengono potati; i bead eliminati sono tolti e il thread ricucito; le risorse non più usate sono eliminate al salvataggio completo. Nella copia tra documenti: nomi di campo in conflitto rinominati con suffisso (predefinito) o fusi su scelta; destinazioni nominate duplicate rinominate con aggiornamento dei link; livelli omonimi mantenuti distinti; versione PDF del risultato pari alla massima delle sorgenti.
**Casi limite:** Documento firmato → avviso esplicito che le firme risulteranno invalidate; documento certificato senza modifiche consentite → avviso bloccante con conferma esplicita; salvataggio incrementale dopo un'eliminazione → avviso che il contenuto eliminato resta nelle revisioni precedenti e proposta di salvataggio completo; permesso di assemblaggio negato → operazione rifiutata senza password proprietario; documento che dichiara conformità PDF/A → avviso se l'operazione introduce elementi non conformi.
**Criteri di accettazione:**
- Dato un documento con un segnalibro verso la pagina 5, quando la pagina 5 viene spostata in posizione 1, allora il segnalibro apre la nuova pagina 1.
- Dato un documento con un link verso la pagina 7, quando la pagina 7 viene eliminata e il file salvato, allora nessun link, segnalibro o destinazione nominata del file punta a un oggetto pagina inesistente (verifica con validatore strutturale).
- Dati due PDF che contengono ciascuno un campo "Nome", quando vengono uniti con le impostazioni predefinite, allora il risultato contiene due campi distinti con valori indipendenti.
- Dato un PDF taggato, quando si eliminano delle pagine, allora l'albero di struttura non contiene riferimenti a contenuti o pagine inesistenti.
- Dato un documento con una firma valida, quando si tenta di ruotare una pagina, allora compare l'avviso di invalidazione prima che la modifica sia applicata.
**Priorità:** P0

### PAG-04 — Riordino pagine con trascinamento e comandi di spostamento
**Descrizione:** Sposta una o più pagine, anche non contigue, trascinandole nella griglia o con comandi: sposta prima/dopo, all'inizio, alla fine, alla posizione N. Durante il trascinamento un indicatore mostra il punto di inserimento e la griglia scorre automaticamente vicino ai bordi.
**Flusso:** 1. L'utente seleziona le pagine. 2. Le trascina sul punto di destinazione oppure usa "Sposta in…" indicando la posizione. 3. Al rilascio le pagine vengono inserite nel punto indicato mantenendo il loro ordine relativo. 4. La selezione resta sulle pagine spostate.
**Formati/strutture:** Riordino di `/Kids` con eventuale ribilanciamento dell'albero; riferimenti per oggetto mantenuti (PAG-03); `/PageLabels` gestite come in PAG-39.
**Casi limite:** Rilascio nella posizione di origine → nessuna modifica e nessuna voce di annullamento; selezione non contigua → le pagine vengono raccolte in blocco nella destinazione nell'ordine originale; Esc durante il trascinamento → nessun effetto; posizione N fuori dall'intervallo valido → errore in linea; rilascio su un altro documento → copia (PAG-14).
**Criteri di accettazione:**
- Dato un documento con pagine [1..6], quando si selezionano 2 e 5 e si trascinano prima della pagina 1, allora l'ordine diventa [2,5,1,3,4,6].
- Dato lo stesso documento, quando si applica "Sposta alla fine" alla pagina 1, allora l'ordine è [2,3,4,5,6,1] e il conteggio resta 6.
- Dato un riordino eseguito, quando si salva e si riapre il file, allora l'ordine è conservato e il rendering di ciascuna pagina è identico a prima.
**Priorità:** P0

### PAG-05 — Inverti, scambia e fascicola pagine
**Descrizione:** Comandi di riordino globale: inverti l'ordine (documento o selezione); scambia due pagine selezionate; scambia ogni coppia (1↔2, 3↔4…, cioè pari con dispari); fascicola fronte/retro, per documenti scansionati prima con tutti i fronti e poi con tutti i retri (intercala la prima e la seconda parte, con la seconda eventualmente in ordine inverso).
**Flusso:** 1. L'utente sceglie il comando. 2. Per "Fascicola" indica il punto di divisione (predefinito: metà) e se i retri sono in ordine inverso; l'anteprima mostra l'ordine risultante. 3. Applica con un'unica voce di annullamento.
**Formati/strutture:** Riordino di `/Kids`; riferimenti mantenuti secondo PAG-03.
**Casi limite:** Inversione di una sola pagina → nessuna modifica; "Scambia" con selezione diversa da 2 pagine → comando disabilitato; scambio a coppie con numero dispari di pagine → l'ultima pagina resta al suo posto; fascicolazione con parti di lunghezza diversa → pagine eccedenti accodate alla fine, con avviso.
**Criteri di accettazione:**
- Dato un documento [1..5], quando si esegue "Inverti ordine", allora l'ordine è [5,4,3,2,1].
- Dato un documento [1..5], quando si esegue "Scambia coppie", allora l'ordine è [2,1,4,3,5].
- Dato un documento [F1,F2,F3,R3,R2,R1] con "retri in ordine inverso", quando si esegue "Fascicola", allora l'ordine è [F1,R1,F2,R2,F3,R3].
**Priorità:** P1

### PAG-06 — Inserisci pagina vuota
**Descrizione:** Inserisce una o più pagine bianche prima o dopo una pagina, all'inizio o alla fine. Formato: come la pagina adiacente (predefinito), formati standard (serie A e B, Letter, Legal, Tabloid…) o personalizzato (mm, pt, pollici); orientamento verticale/orizzontale; quantità. I modelli di carta (righe, quadretti, puntini) definiti nell'area Creazione PDF sono selezionabili anche qui.
**Flusso:** 1. L'utente sceglie "Inserisci > Pagina vuota" oppure il pulsante "+" tra due miniature. 2. Imposta posizione, quantità, formato e orientamento. 3. Conferma: le pagine vengono inserite e selezionate.
**Formati/strutture:** Nuovo oggetto `/Page` con `/MediaBox`, `/Resources` vuoto e nessun contenuto; con "come adiacente" si copiano `/MediaBox` e `/Rotate` della pagina di riferimento, così che l'aspetto visualizzato coincida; per l'orientamento orizzontale di un formato standard si usa un `/MediaBox` più largo che alto, non `/Rotate`.
**Casi limite:** Quantità pari a 0 o oltre il massimo configurato (predefinito 1.000) → errore di validazione; lato inferiore a 3 pt → rifiutato; lato oltre 14.400 unità (200 pollici) → uso di `/UserUnit` con avviso di compatibilità; inserimento in un documento con etichette → etichette aggiornate secondo PAG-39.
**Criteri di accettazione:**
- Dato un documento di 3 pagine A4, quando si inseriscono 2 pagine vuote dopo la pagina 1 con formato "come adiacente", allora il documento ha 5 pagine e le pagine 2–3 misurano 595×842 pt ±1 e non hanno contenuto visibile.
- Dato il formato personalizzato 100×200 mm con orientamento orizzontale, quando si inserisce una pagina alla fine, allora l'ultima pagina visualizzata misura 200×100 mm ±0,5 mm.
- Dato un inserimento salvato, quando il file viene esaminato con un validatore strutturale, allora risulta valido e `/Count` corrisponde al numero effettivo di pagine.
**Priorità:** P0

### PAG-07 — Inserisci pagine da altro file
**Descrizione:** Inserisce in una posizione del documento pagine prese da un altro PDF (o da un file di altro formato supportato, convertito tramite le aree Creazione/Conversione), scegliendo l'intervallo sorgente con anteprima delle miniature. Modalità "inserimento alternato": intercala le pagine sorgente ogni K pagine del documento (in ordine diretto o inverso), utile per ricomporre scansioni fronte/retro.
**Flusso:** 1. L'utente sceglie "Inserisci > Da file" o trascina un file sulla griglia. 2. Seleziona il file (password se cifrato). 3. Indica intervallo sorgente (PAG-02), posizione di destinazione e modalità (blocco unico o alternato ogni K pagine). 4. Sceglie se includere annotazioni, campi modulo e segnalibri della sorgente (annidati sotto un segnalibro con il nome del file). 5. Conferma.
**Formati/strutture:** Copia profonda degli oggetti pagina con le risorse (font, immagini, XObject) e deduplicazione delle risorse identiche; annotazioni e widget; fusione di `/AcroForm`, `/OCProperties`, struttura logica (innesto sotto la radice) e segnalibri secondo PAG-03.
**Casi limite:** Sorgente cifrata → richiesta password, nessuna modifica se errata; sorgente con assemblaggio o estrazione negati → rifiuto con spiegazione, salvo password proprietario; sorgente danneggiata → tentativo di riparazione, altrimenti errore senza toccare il documento; sorgente uguale al documento stesso → ammessa, si copiano le pagine dallo stato corrente; alternato con sorgente più lunga → pagine eccedenti accodate in fondo, più corta → l'alternanza termina e il resto è invariato; sorgente firmata → le firme non vengono copiate, con avviso; formati di pagina diversi → mantenuti (adeguamento opzionale con PAG-28).
**Criteri di accettazione:**
- Dati A di 4 pagine e B di 3 pagine, quando si inseriscono le pagine 2–3 di B dopo la pagina 1 di A, allora A ha 6 pagine nell'ordine [A1,B2,B3,A2,A3,A4] e le pagine inserite hanno rendering identico alle sorgenti.
- Dati A [A1..A3] e B [B1..B3], quando si inserisce B in modalità alternata ogni 1 pagina, allora l'ordine è [A1,B1,A2,B2,A3,B3].
- Dato B con la stessa immagine su tutte le pagine, quando se ne inseriscono 10 pagine e si salva, allora l'immagine è presente una sola volta nel file.
- Dato B cifrato, quando si fornisce una password errata, allora nessuna pagina viene inserita e il documento non risulta modificato.
**Priorità:** P0

### PAG-08 — Inserisci pagine da immagini
**Descrizione:** Crea pagine da uno o più file immagine (PNG, JPEG, TIFF anche multipagina, BMP, GIF, WebP, JPEG 2000; HEIC se il codec è disponibile) e le inserisce in una posizione. Dimensione della pagina: "come l'immagine" (in base alla risoluzione dichiarata) oppure formato pagina (A4, Letter, come adiacente…) con adattamento contieni/riempi/centra senza scala e margini; orientamento automatico secondo le proporzioni; qualità: conserva i dati originali o ricomprimi.
**Flusso:** 1. L'utente sceglie "Inserisci > Da immagine" o trascina immagini sulla griglia. 2. Riordina le immagini nell'elenco. 3. Imposta dimensione, adattamento, margini e qualità. 4. Conferma: una pagina per immagine, una per fotogramma nei TIFF multipagina.
**Formati/strutture:** Image XObject; JPEG incorporato senza ricodifica (DCTDecode); PNG e simili con FlateDecode e canale alfa come `/SMask`; JPEG 2000 con JPXDecode; TIFF bitonali in CCITT G4 quando possibile; profilo ICC incorporato se presente; orientamento EXIF applicato.
**Casi limite:** Risoluzione assente o non plausibile → si usa un valore predefinito configurabile (150 dpi); GIF animata → solo il primo fotogramma, con avviso; file corrotto o non supportato → saltato e riportato in un elenco errori, le altre immagini vengono inserite; JPEG CMYK → colori corretti (gestione della codifica invertita); immagine che supera 200 pollici alla risoluzione data → `/UserUnit` oppure riduzione, con avviso; PNG a 16 bit → conservato se la versione del file lo consente, altrimenti ridotto a 8 bit.
**Criteri di accettazione:**
- Dato un JPEG 3000×2000 px a 300 dpi con opzione "come l'immagine", quando viene inserito, allora la pagina misura 720×480 pt ±1 e i dati compressi dell'immagine nel PDF coincidono byte per byte con quelli del file sorgente.
- Dato un PNG con trasparenza inserito su pagina A4 in modalità "contieni" con margini di 10 mm, quando si renderizza la pagina, allora l'immagine è centrata, conserva le proporzioni e le aree trasparenti mostrano il bianco della pagina.
- Dato un TIFF di 3 pagine, quando viene inserito, allora vengono create 3 pagine nell'ordine dei fotogrammi.
- Dato un JPEG con orientamento EXIF 6, quando viene inserito, allora l'immagine appare diritta nella pagina.
**Priorità:** P1

### PAG-09 — Inserisci dagli appunti
**Descrizione:** Inserisce come nuove pagine il contenuto degli appunti di sistema: pagine PDF (copiate da Foliant o da altre applicazioni che forniscono dati PDF), immagini, testo (impaginato su pagine del formato scelto), elenchi di file (inseriti come in PAG-07 e PAG-08).
**Flusso:** 1. L'utente sceglie "Inserisci > Dagli appunti" (abilitato solo se gli appunti contengono un tipo supportato). 2. Vede l'anteprima del contenuto rilevato e del numero di pagine risultanti. 3. Sceglie la posizione e, per testo e immagini, formato e margini. 4. Conferma.
**Formati/strutture:** Tipi degli appunti in ordine di preferenza: `application/pdf` (pagine), immagini (`image/png`, `image/jpeg`…), elenco di file (`text/uri-list`), testo formattato (convertito in testo con stili di base), testo semplice UTF-8. Il testo è impaginato con a capo automatico, interruzioni di pagina e font incorporato in sottoinsieme, quindi resta ricercabile.
**Casi limite:** Appunti vuoti o di tipo non supportato → comando disabilitato con suggerimento; testo molto lungo → più pagine, numero indicato in anteprima; caratteri non coperti dal font predefinito → font di sistema alternativo che li copre, altrimenti glifo sostitutivo e avviso; immagine più grande del formato → ridotta per adattarsi; appunti modificati durante il dialogo → si usa l'istantanea presa all'apertura.
**Criteri di accettazione:**
- Date 2 pagine copiate in Foliant da un documento A, quando si inseriscono dagli appunti nel documento B dopo la pagina 1, allora B contiene in posizione 2–3 due pagine con rendering identico alle sorgenti.
- Dato uno screenshot negli appunti, quando si esegue il comando con dimensione "come l'immagine", allora viene creata 1 pagina che contiene l'immagine a piena pagina.
- Dato un testo semplice di 200 righe negli appunti e formato A4, quando viene inserito, allora il testo estratto dalle nuove pagine coincide con quello sorgente, a meno degli a capo.
**Priorità:** P1

### PAG-10 — Inserisci da scanner
**Descrizione:** Acquisisce pagine da un dispositivo di scansione disponibile nel sistema e le inserisce nel documento aperto, in una posizione scelta. Preset salvabili (risoluzione, colore/grigi/bianco e nero, fronte/retro, alimentatore automatico, formato), OCR facoltativo (area OCR) e scarto facoltativo delle pagine vuote (PAG-40).
**Flusso:** 1. L'utente sceglie "Inserisci > Da scanner". 2. Seleziona dispositivo e preset. 3. Avvia l'acquisizione; le pagine compaiono man mano in anteprima e possono essere riacquisite, eliminate o ruotate. 4. Sceglie la posizione e conferma.
**Formati/strutture:** Immagini acquisite convertite in image XObject (JPEG per colore e grigi, CCITT G4 o JBIG2 senza perdita per bianco e nero); una pagina per facciata; dimensioni della pagina ricavate dalla risoluzione di acquisizione.
**Casi limite:** Nessun dispositivo rilevato → messaggio con indicazioni e pulsante "Riprova"; dispositivo disconnesso durante l'acquisizione → le pagine già acquisite restano in anteprima e possono essere inserite; alimentatore vuoto → fine acquisizione; retri vuoti in fronte/retro → scarto facoltativo; annullamento → nessuna modifica al documento.
**Criteri di accettazione:**
- Dato un dispositivo (anche virtuale di test) che fornisce 3 pagine, quando si acquisisce e si inserisce all'inizio, allora il documento ha 3 pagine in più in testa, nell'ordine di acquisizione.
- Dato il preset bianco e nero a 300 dpi, quando si acquisisce una pagina A4, allora la pagina inserita misura 595×842 pt ±2 e l'immagine ha 1 bit per componente.
- Dato un annullamento durante l'acquisizione, quando si chiude il dialogo, allora il documento non risulta modificato.
**Priorità:** P1

### PAG-11 — Elimina pagine
**Descrizione:** Elimina le pagine selezionate o indicate per intervallo/filtro, con riepilogo degli elementi collegati coinvolti, conferma (disattivabile) e annullamento.
**Flusso:** 1. L'utente seleziona le pagine e preme Canc, oppure usa "Elimina pagine…" con un intervallo (PAG-02). 2. Il riepilogo indica il numero di pagine e gli elementi collegati (segnalibri, link, campi). 3. Conferma. 4. I riferimenti vengono ripuliti secondo PAG-03.
**Formati/strutture:** Rimozione da `/Kids` e aggiornamento di `/Count`; risorse orfane eliminate al salvataggio completo; `/PageLabels` aggiornate (PAG-39).
**Casi limite:** Eliminazione di tutte le pagine → impedita, deve restarne almeno una; documento firmato → avviso (PAG-03); salvataggio incrementale → avviso che il contenuto eliminato resta recuperabile dalle revisioni precedenti e proposta di salvataggio completo; campo con widget su più pagine → rimossi solo i widget delle pagine eliminate, il campo resta.
**Criteri di accettazione:**
- Dato un documento di 10 pagine, quando si elimina "2,4-6", allora restano 6 pagine nell'ordine [1,3,7,8,9,10].
- Date pagine eliminate che contengono immagini non usate altrove, quando si salva con salvataggio completo, allora quelle immagini non sono più presenti nel file, nemmeno in revisioni precedenti.
- Dato un documento di 3 pagine tutte selezionate, quando si preme Canc, allora il comando è rifiutato con un messaggio e il documento resta di 3 pagine.
**Priorità:** P0

### PAG-12 — Estrai pagine in PDF
**Descrizione:** Crea nuovi PDF dalle pagine selezionate o da un intervallo, in un unico file oppure in file separati (uno per pagina o uno per ciascun intervallo indicato). Opzioni: elimina le pagine dal documento dopo l'estrazione; includi annotazioni, campi e segnalibri pertinenti; modello del nome file (PAG-32); cartella di destinazione; apri il risultato.
**Flusso:** 1. L'utente seleziona le pagine e sceglie "Estrai". 2. Indica la modalità (file unico, un file per pagina, un file per intervallo). 3. Imposta opzioni e destinazione. 4. L'estrazione procede con avanzamento annullabile e si conclude con l'elenco dei file creati.
**Formati/strutture:** Nuovo documento con le pagine copiate e riferimenti gestiti secondo PAG-03: restano solo i segnalibri che puntano a pagine estratte, i link verso pagine non estratte vengono rimossi; metadati (Info e XMP) copiati dalla sorgente con date aggiornate e nuovo `/ID`; versione PDF pari a quella della sorgente; la protezione della sorgente non viene ereditata salvo scelta esplicita.
**Casi limite:** Nome file già esistente → suffisso numerico (sovrascrittura solo con conferma); cartella non scrivibile → errore prima di iniziare; migliaia di file → avanzamento annullabile, i file già creati restano e sono elencati; sorgente con assemblaggio o estrazione negati → rifiuto salvo password proprietario; "elimina dopo l'estrazione" applicato a tutte le pagine → estrazione eseguita, eliminazione no (deve restare almeno una pagina), con avviso.
**Criteri di accettazione:**
- Dato un documento di 10 pagine, quando si estraggono "1-3,7" in un file unico, allora il nuovo file ha 4 pagine [1,2,3,7] con rendering identico agli originali.
- Dato lo stesso documento, quando si estraggono tutte le pagine con un file per pagina e modello "{nome}_p{pagina}", allora vengono creati 10 file di 1 pagina, da doc_p1.pdf a doc_p10.pdf.
- Dato "elimina dopo l'estrazione", quando si estraggono le pagine 2–3, allora il documento sorgente ha 8 pagine e l'eliminazione è annullabile.
- Dati un segnalibro verso la pagina 7 e uno verso la pagina 9, quando si estrae la sola pagina 7, allora il file estratto contiene solo il primo segnalibro, che punta alla sua pagina 1.
**Priorità:** P0

### PAG-13 — Estrai pagine come immagini
**Descrizione:** Esporta le pagine selezionate come file immagine (PNG, JPEG, TIFF anche multipagina, BMP, WebP) scegliendo risoluzione, colore/grigi/bianco e nero, qualità JPEG, sfondo trasparente (PNG), inclusione delle annotazioni e area esportata (pagina visibile o intero foglio).
**Flusso:** 1. L'utente seleziona le pagine e sceglie "Estrai come immagini". 2. Imposta formato e opzioni. 3. Indica destinazione e modello del nome. 4. L'esportazione procede con avanzamento annullabile.
**Formati/strutture:** Rendering della pagina con rotazione applicata, entro `/CropBox` (predefinito) o `/MediaBox`; risoluzione scritta nei metadati del file immagine; nel TIFF multipagina un fotogramma per pagina.
**Casi limite:** Risoluzione che produce immagini oltre il limite configurato (pixel per lato o memoria) → avviso e proposta di risoluzione ridotta; pagina in parte non renderizzabile → immagine con quanto renderizzabile e voce nell'elenco errori; combinazione non supportata (trasparenza in JPEG) → opzione disabilitata.
**Criteri di accettazione:**
- Data una pagina A4, quando viene esportata in PNG a 150 dpi, allora l'immagine misura 1240×1754 px ±1 e dichiara 150 dpi nei metadati.
- Date 5 pagine esportate in TIFF multipagina, quando si apre il file prodotto, allora contiene 5 fotogrammi nell'ordine delle pagine.
- Data l'opzione "includi annotazioni" disattivata, quando si esporta una pagina con un'evidenziazione, allora l'evidenziazione non compare nell'immagine.
**Priorità:** P1

### PAG-14 — Trascinamento di pagine tra documenti e verso il file manager
**Descrizione:** Trascinamento delle miniature: verso un altro documento aperto (altra scheda o finestra) per copiarle, o spostarle tenendo premuto un tasto modificatore; verso il desktop o una cartella del file manager per creare un PDF con le pagine trascinate; dal file manager verso la griglia per inserire PDF o immagini nel punto di rilascio.
**Flusso:** 1. L'utente seleziona e trascina. 2. Durante il trascinamento la destinazione mostra il punto di inserimento e il cursore indica copia o spostamento. 3. Al rilascio viene eseguita l'operazione corrispondente (PAG-07, PAG-08 o PAG-12) con le impostazioni predefinite.
**Formati/strutture:** Protocollo di trascinamento del sistema: verso l'esterno un PDF temporaneo offerto come file (nome predefinito "{nome} (pagine {intervallo}).pdf"); verso l'interno elenco di file; tra documenti dello stesso processo trasferimento diretto delle pagine senza passare da file.
**Casi limite:** Destinazione che non accetta file → nessun effetto; spostamento che lascerebbe il sorgente senza pagine → trasformato in copia, con avviso; documento di destinazione in sola lettura o con permessi negati → rilascio rifiutato con indicatore visivo; file temporanei eliminati dopo la consegna o alla chiusura; file non supportato rilasciato sulla griglia → messaggio di errore.
**Criteri di accettazione:**
- Date 3 pagine selezionate nel documento A, quando vengono trascinate in una cartella del file manager, allora nella cartella compare un PDF di 3 pagine con rendering identico.
- Date le pagine 2–3 di A trascinate tra le pagine 1 e 2 di B senza modificatori, quando si rilascia, allora B contiene le due pagine in posizione 2–3 e A è invariato.
- Dato un file JPEG trascinato dal file manager sulla griglia dopo la pagina 4, quando si rilascia, allora viene creata una pagina con l'immagine in posizione 5.
**Priorità:** P1

### PAG-15 — Duplica pagine
**Descrizione:** Crea copie delle pagine selezionate, subito dopo ciascuna pagina, dopo l'ultima pagina selezionata come blocco o in fondo al documento, con numero di copie configurabile.
**Flusso:** 1. L'utente seleziona le pagine. 2. Sceglie "Duplica" (una copia subito dopo) oppure "Duplica…" (numero di copie e posizione). 3. Le copie create risultano selezionate.
**Formati/strutture:** Nuovo oggetto pagina che riusa per riferimento gli stessi stream di contenuto e le stesse risorse (nessuna copia di immagini e font); annotazioni duplicate con nuovi identificativi (`/NM`) e `/P` aggiornato; campi modulo: per impostazione predefinita nuovi campi rinominati con suffisso (valori indipendenti), in alternativa "mantieni i nomi" (valori condivisi); struttura logica: nuova voce `/StructParents` nel `/ParentTree` con elementi di struttura duplicati.
**Casi limite:** Pagina con campo firma → il campo firma non viene duplicato, con avviso; link interni → duplicati verso le stesse destinazioni; numero di copie elevato (oltre 500) → richiesta di conferma.
**Criteri di accettazione:**
- Dato un documento [1,2,3], quando si duplica la pagina 2 con 2 copie subito dopo, allora l'ordine è [1,2,2′,2″,3] e le copie hanno rendering identico alla pagina 2.
- Data una pagina con un'immagine da 5 MB, quando viene duplicata e il file salvato, allora la dimensione del file aumenta di meno di 10 kB.
- Data una pagina con il campo di testo "Nome" compilato, quando viene duplicata con le impostazioni predefinite, allora la copia ha un campo distinto e modificarne il valore non altera il campo originale.
**Priorità:** P0

### PAG-16 — Copia, taglia e incolla pagine
**Descrizione:** Copia o taglia le pagine selezionate negli appunti e le incolla nello stesso documento o in un altro, prima o dopo la pagina di riferimento. Negli appunti le pagine sono offerte in formato interno (fedele), come PDF standard per le altre applicazioni e come immagine della prima pagina.
**Flusso:** 1. L'utente seleziona le pagine e preme Ctrl+C o Ctrl+X. 2. Nella destinazione seleziona la pagina di riferimento e preme Ctrl+V (incolla dopo) o usa "Incolla prima". 3. Le pagine incollate risultano selezionate. Con "Taglia" le pagine vengono rimosse dal sorgente subito, in modo annullabile.
**Formati/strutture:** `application/pdf` con un PDF autonomo contenente le pagine copiate; formato interno che conserva i riferimenti (PAG-03); `image/png` della prima pagina.
**Casi limite:** Incolla nello stesso documento → equivale a una duplicazione nella posizione indicata (PAG-15); taglio di tutte le pagine → impedito; sorgente chiuso prima dell'incolla → l'incolla funziona perché i dati negli appunti sono autonomi; appunti sovrascritti da un'altra applicazione → "Incolla pagine" non disponibile; permesso di copia o assemblaggio negato nel sorgente → "Copia pagine" disabilitato.
**Criteri di accettazione:**
- Date le pagine 1–2 copiate da A, quando si chiude A e si incolla in B dopo l'ultima pagina, allora B termina con le 2 pagine, con rendering identico.
- Data la pagina 3 tagliata da A, quando la si incolla in A prima della pagina 1, allora A ha lo stesso numero di pagine e quella pagina è in prima posizione.
- Date pagine copiate da Foliant, quando un'applicazione esterna legge gli appunti nel tipo `application/pdf`, allora riceve un PDF valido con le sole pagine selezionate.
**Priorità:** P1

### PAG-17 — Condividi pagine selezionate
**Descrizione:** Invia solo le pagine selezionate senza doverle prima salvare a mano: genera un PDF temporaneo e lo passa al meccanismo di condivisione del sistema (client di posta come allegato o altre applicazioni) oppure lo salva come copia.
**Flusso:** 1. L'utente seleziona le pagine e sceglie "Condividi pagine". 2. Sceglie la destinazione (posta, applicazione, salva copia). 3. Il sistema genera il PDF temporaneo (nome "{nome} - pagine {intervallo}.pdf") e lo consegna.
**Formati/strutture:** PDF prodotto come in PAG-12; file temporaneo in un'area privata dell'utente, rimosso alla chiusura dell'applicazione o dopo un tempo configurabile.
**Casi limite:** Nessun client di posta o servizio di condivisione disponibile → si offrono "Salva copia" e "Apri cartella"; allegato oltre una soglia configurabile (predefinito 25 MB) → avviso con proposta di compressione (area Output); documento cifrato → il PDF condiviso mantiene la protezione per impostazione predefinita, rimovibile solo se l'utente ne ha i diritti.
**Criteri di accettazione:**
- Date le pagine 2–4 selezionate, quando si sceglie "Posta", allora il client di posta predefinito (in test, un client fittizio registrato) riceve un allegato PDF di 3 pagine.
- Dato un PDF con password di apertura, quando si condividono pagine con le impostazioni predefinite, allora l'allegato richiede la stessa password.
**Priorità:** P1

### PAG-18 — Ruota pagine
**Descrizione:** Ruota in modo persistente le pagine selezionate, un intervallo o un filtro (pari, dispari, verticali, orizzontali) di 90° in senso orario, 90° antiorario (270°) o 180°. Pulsanti rapidi compaiono sulla miniatura al passaggio del puntatore. È distinta dalla rotazione temporanea della sola vista (area Visualizzazione), che non viene salvata.
**Flusso:** 1. L'utente seleziona le pagine o indica un intervallo. 2. Sceglie il comando di rotazione (pulsante, menu o scorciatoia). 3. Miniature e vista si aggiornano subito.
**Formati/strutture:** Chiave `/Rotate` della pagina, con il valore ereditato risolto prima della modifica e il risultato normalizzato a 0/90/180/270; contenuto, annotazioni e campi non vengono riscritti e ruotano con la pagina; le annotazioni con flag NoRotate restano orientate verso il lettore; `/Thumb` rimossa.
**Casi limite:** `/Rotate` negativo nel file → normalizzato (−90 diventa 270); `/Rotate` non multiplo di 90 → trattato come 0, con avviso; `/Rotate` ereditato da un nodo `/Pages` → la nuova rotazione è scritta sulla singola pagina senza alterare le altre; documento firmato → avviso (PAG-03).
**Criteri di accettazione:**
- Data una pagina con `/Rotate` 0, quando viene ruotata di 90° in senso orario quattro volte, allora `/Rotate` torna a 0 e il rendering è identico all'originale.
- Dato un documento con pagine verticali e orizzontali, quando si ruota di 90° con il filtro "solo orizzontali", allora cambiano solo le pagine orizzontali.
- Dato un nodo `/Pages` con `/Rotate` 90 ereditato da 3 pagine, quando si ruota solo la pagina 2 di 90° in senso orario, allora la pagina 2 ha rotazione effettiva 180 e le pagine 1 e 3 restano a 90.
- Data una pagina con un'evidenziazione su una parola, quando la pagina viene ruotata e il file salvato, allora in un altro lettore conforme l'evidenziazione copre ancora la stessa parola.
**Priorità:** P0

### PAG-19 — Sostituisci pagine
**Descrizione:** Sostituisce un intervallo di pagine del documento con pagine di un altro file (intervallo sorgente), con anteprima affiancata vecchia/nuova. Per impostazione predefinita i due intervalli hanno lo stesso numero di pagine; numeri diversi sono ammessi con conferma.
**Flusso:** 1. L'utente seleziona le pagine da sostituire e sceglie "Sostituisci". 2. Indica file sorgente e intervallo. 3. Sceglie le opzioni: annotazioni di commento da tenere (delle pagine originali, predefinito, della sorgente, o entrambe); segnalibri e link verso le pagine sostituite reindirizzati alle nuove pagine nella stessa posizione (predefinito). 4. Conferma.
**Formati/strutture:** Le nuove pagine prendono il posto degli oggetti pagina originali e i riferimenti (segnalibri, link, destinazioni) vengono reindirizzati; le annotazioni conservate sono riportate sulle nuove pagine, riposizionate in proporzione se le dimensioni differiscono; i campi modulo delle pagine sostituite vengono rimossi, salvo opzione contraria, con avviso.
**Casi limite:** Numero di pagine diverso → le prime min(n, m) pagine sono sostituite una a una con reindirizzamento dei riferimenti, quelle in eccesso vengono eliminate (n > m) o inserite (m > n); sorgente coincidente con il documento → ammessa, si usa lo stato precedente all'operazione; sorgente cifrata → richiesta password; documento firmato → avviso (PAG-03).
**Criteri di accettazione:**
- Dato A di 5 pagine con un segnalibro verso la pagina 3, quando si sostituiscono le pagine 3–4 con le pagine 1–2 di B, allora A ha 5 pagine, le pagine 3–4 hanno rendering identico a B1–B2 e il segnalibro apre la pagina 3.
- Data una nota adesiva sulla pagina 3 di A e l'opzione "conserva annotazioni originali", quando la pagina 3 viene sostituita, allora la nota è presente sulla nuova pagina 3.
- Date 2 pagine da sostituire con 3 pagine sorgente, quando si conferma, allora il documento cresce di 1 pagina e un solo Annulla ripristina lo stato precedente.
**Priorità:** P1

### PAG-20 — Dividi documento per intervalli o ogni N pagine
**Descrizione:** Divide il documento in più file: ogni N pagine; secondo un elenco di intervalli, un file per intervallo (es. "1-3; 4-10; 11-"); oppure in K parti di lunghezza il più possibile uguale. L'anteprima colora le miniature per parte e permette di aggiungere o togliere punti di taglio con un clic tra due miniature. Nome dei file con modello (PAG-32), cartella di destinazione, stessa divisione applicabile a più file.
**Flusso:** 1. L'utente sceglie "Dividi". 2. Sceglie metodo e parametri; l'anteprima mostra numero di file e pagine per file. 3. Imposta cartella, nome, sovrascrittura e segnalibri. 4. La divisione procede con avanzamento e termina con un riepilogo.
**Formati/strutture:** Ogni parte è un documento autonomo prodotto come in PAG-12; le risorse condivise sono copiate in ciascuna parte che le usa.
**Casi limite:** N maggiore o uguale al numero di pagine → un solo file, con avviso; ultima parte più corta → ammessa; intervalli che lasciano pagine scoperte → avviso con l'elenco e opzione "raccogli le restanti in un file aggiuntivo"; intervalli sovrapposti → ammessi con avviso (pagine presenti in più file); K che non divide il totale → lunghezze delle parti che differiscono al massimo di 1; documento di una sola pagina → comando disabilitato.
**Criteri di accettazione:**
- Dato un documento di 10 pagine, quando si divide ogni 3 pagine, allora si ottengono 4 file di 3, 3, 3 e 1 pagine.
- Dato l'elenco "1-2; 5-6", quando si divide, allora si ottengono 2 file e un avviso che le pagine 3, 4 e 7–10 non sono incluse.
- Dato il modello "{nome}_parte{n}", quando si divide doc.pdf in 3 parti, allora i file si chiamano doc_parte1.pdf, doc_parte2.pdf e doc_parte3.pdf.
- Dato un file omonimo già presente nella destinazione e la sovrascrittura disattivata, quando si divide, allora il nuovo file riceve un suffisso numerico e il file esistente resta invariato.
**Priorità:** P0

### PAG-21 — Dividi per segnalibri
**Descrizione:** Crea un file per ogni segnalibro di primo livello (opzionalmente fino a un livello L scelto): ciascuna parte va dalla pagina di destinazione del segnalibro alla pagina che precede il segnalibro successivo. Il titolo del segnalibro può fare da nome file e i segnalibri figli sono conservati nel file corrispondente.
**Flusso:** 1. L'utente sceglie "Dividi > Per segnalibri". 2. Imposta il livello (predefinito 1) e vede l'elenco delle parti con titoli e intervalli. 3. Sceglie il nome file ("{segnalibro}" o altro modello) e la cartella. 4. Esegue.
**Formati/strutture:** `/Outlines` (`/First`, `/Next`, figli); destinazioni esplicite, nominate o tramite azione GoTo risolte in indice di pagina.
**Casi limite:** Pagine prima del primo segnalibro → parte iniziale separata (opzione: unirle alla prima parte); più segnalibri sulla stessa pagina → la parte che resterebbe vuota non viene creata e i suoi segnalibri passano alla parte successiva; segnalibri non in ordine di pagina → parti ordinate per pagina di destinazione; segnalibro senza destinazione o con azione non di pagina (es. URL) → ignorato, con avviso; titolo con caratteri non ammessi nei nomi file (`/ \ : * ? " < > |` e caratteri di controllo) → sostituiti con "_" e nome troncato a 200 byte; titoli ripetuti → suffisso numerico; documento senza segnalibri → metodo disabilitato.
**Criteri di accettazione:**
- Dato un documento di 20 pagine con segnalibri di primo livello verso le pagine 1, 6 e 15, quando si divide, allora si ottengono 3 file di 5, 9 e 6 pagine.
- Dato un segnalibro "Capitolo 2: Analisi/Risultati" usato come nome file, quando si divide, allora il file si chiama "Capitolo 2_ Analisi_Risultati.pdf".
- Dato un segnalibro di primo livello con 2 figli, quando si divide, allora il file della sua parte contiene i 2 segnalibri figli con destinazioni corrette.
**Priorità:** P1

### PAG-22 — Dividi per dimensione massima del file
**Descrizione:** Divide il documento in parti che non superano una dimensione indicata (kB o MB), mantenendo pagine intere e ordine originale; utile per i limiti di allegati e portali.
**Flusso:** 1. L'utente sceglie "Dividi > Per dimensione" e indica la soglia. 2. Vede una stima del numero di parti. 3. Esegue: le pagine vengono accumulate finché la parte, scritta su file, resta entro la soglia.
**Formati/strutture:** Dimensione misurata sul file effettivamente scritto (compressione inclusa e risorse condivise ripetute in ogni parte), non stimata dalla somma delle pagine.
**Casi limite:** Pagina singola più grande della soglia → parte composta da quella sola pagina, segnalata come eccedente nel riepilogo; soglia inferiore alla dimensione minima di un PDF di una pagina → errore; risorse comuni pesanti (es. font) ripetute in ogni parte → numero di parti maggiore della stima iniziale, aggiornato nel riepilogo.
**Criteri di accettazione:**
- Dato un documento di 30 MB e una soglia di 10 MB, quando si divide, allora ogni file prodotto è al massimo di 10 MB, tranne le parti di una sola pagina eccedente, segnalate nel riepilogo.
- Date le parti prodotte, quando vengono concatenate nell'ordine, allora la sequenza delle pagine coincide con quella del documento originale, senza pagine perse o duplicate.
**Priorità:** P1

### PAG-23 — Dividi per pagine separatrici o per testo
**Descrizione:** Divide in corrispondenza di pagine vuote usate come separatori (con opzione di scartarle) oppure di pagine che contengono un testo o un'espressione regolare (es. "Fattura n."), facendo iniziare lì una nuova parte; il testo trovato può comporre il nome file.
**Flusso:** 1. L'utente sceglie "Dividi > Per separatori o testo". 2. Imposta il criterio: pagina vuota (con tolleranza) oppure testo/espressione regolare, con area di ricerca facoltativa (es. fascia superiore della pagina). 3. Rivede in anteprima i punti di taglio e può modificarli a mano. 4. Esegue.
**Formati/strutture:** Rilevamento delle pagine vuote come in PAG-40; ricerca sul testo estratto, oppure sul testo riconosciuto dall'area OCR se la pagina non ha testo.
**Casi limite:** Nessuna corrispondenza → un unico file, con avviso; corrispondenze su pagine consecutive → parti di una pagina; pagina quasi vuota per rumore di scansione → classificata secondo la tolleranza, regolabile in anteprima; espressione regolare non valida → errore in linea.
**Criteri di accettazione:**
- Dato un documento di 12 pagine con pagine vuote in posizione 4 e 9 e l'opzione "scarta separatori", quando si divide, allora si ottengono 3 file di 3, 4 e 3 pagine senza le pagine vuote.
- Dato il criterio `Fattura n\. (\d+)` e il modello "fattura_{match1}", quando si divide un documento con le fatture 101, 102 e 103, allora si ottengono fattura_101.pdf, fattura_102.pdf e fattura_103.pdf.
**Priorità:** P2

### PAG-24 — Unisci più file
**Descrizione:** Combina più file (PDF, immagini e altri formati convertibili) in un nuovo PDF. L'elenco mostra miniatura, numero di pagine e dimensione di ogni file; l'ordine si cambia trascinando, con pulsanti su/giù o ordinando per nome o data; per ogni file si può indicare un intervallo (PAG-02) e lo stesso file può comparire più volte. Opzioni: nomi file come segnalibri di primo livello con i segnalibri originali annidati; conserva o scarta segnalibri, annotazioni e campi; aggiungi una pagina vuota dopo i file con numero dispari di pagine (stampa fronte/retro); unione alternata di due file; metadati del risultato presi dal primo file o personalizzati.
**Flusso:** 1. L'utente sceglie "Unisci" (dalla schermata iniziale, dal menu File o trascinando più file). 2. Aggiunge file o cartelle, con password per quelli cifrati. 3. Imposta ordine, intervalli e opzioni. 4. Sceglie se aprire il risultato in una nuova scheda o salvarlo direttamente. 5. L'unione procede con avanzamento annullabile.
**Formati/strutture:** Fusione degli alberi delle pagine con deduplicazione delle risorse; `/AcroForm`, `/OCProperties` e struttura logica fusi secondo PAG-03 (risultato taggato solo se lo sono tutte le sorgenti, altrimenti avviso); `/Outlines` costruiti secondo le opzioni; `/PageLabels` delle sorgenti conservate per segmento (opzione); versione PDF pari alla massima delle sorgenti; nuovo `/ID` e metadati XMP coerenti.
**Casi limite:** File cifrato senza password → richiesta, oppure esclusione con avviso; file danneggiato → tentativo di riparazione, altrimenti esclusione con elenco; un solo file → ammesso (equivale a un'estrazione dell'intervallo); file firmati → le firme non vengono mantenute, con avviso; sorgenti PDF/A → il risultato non dichiara conformità salvo conversione esplicita; centinaia di file o file molto grandi → elaborazione progressiva senza caricare tutto in memoria; file omonimi in cartelle diverse → segnalibri distinti.
**Criteri di accettazione:**
- Dati A (3 pagine), B (2 pagine) e un'immagine JPG, quando si uniscono nell'ordine B, A, JPG, allora il risultato ha 6 pagine nell'ordine [B1,B2,A1,A2,A3,img].
- Data l'opzione "nomi file come segnalibri", quando si uniscono A.pdf e B.pdf, allora il risultato ha 2 segnalibri di primo livello "A" e "B" che puntano alle rispettive prime pagine, con i segnalibri originali annidati.
- Dati A con intervallo "2-3" e B con intervallo "1", quando si uniscono, allora il risultato ha 3 pagine [A2,A3,B1].
- Dati A di 3 pagine e B di 2 pagine con l'opzione "pagina vuota dopo i file dispari", quando si uniscono, allora il risultato ha 6 pagine con una pagina vuota in posizione 4.
- Dati 200 file da 50 pagine, quando si uniscono, allora l'operazione termina senza errori con il picco di memoria entro la soglia dei requisiti non funzionali.
**Priorità:** P0

### PAG-25 — Ritaglia pagine
**Descrizione:** Riduce l'area visibile delle pagine in modo non distruttivo: con margini numerici (alto, basso, sinistra, destra in mm, pt, pollici o %) e anteprima, oppure disegnando un rettangolo sulla pagina con maniglie di regolazione. Si applica a pagina corrente, selezione, intervallo, pari/dispari (con margini speculari per il fronte/retro) o tutte. "Ripristina ritaglio" riporta all'area originale. La rimozione definitiva del contenuto esterno spetta all'appiattimento/sanificazione (area Sicurezza), che viene proposta all'utente.
**Flusso:** 1. L'utente attiva lo strumento "Ritaglia". 2. Disegna il rettangolo o inserisce i margini; vede le dimensioni risultanti. 3. Sceglie l'ambito di pagine. 4. Applica.
**Formati/strutture:** `/CropBox` (predefinito) oppure, su richiesta, `/MediaBox` (ritaglio "rigido"); i valori si riferiscono alla pagina come visualizzata (conversione che tiene conto di `/Rotate` e dell'eventuale origine non nulla del `/MediaBox`); `/CropBox` sempre intersecato con `/MediaBox`.
**Casi limite:** Area risultante sotto 3×3 pt o margini che si sovrappongono → errore; rettangolo esteso oltre la pagina → limitato ai bordi; pagine di formato diverso nell'ambito → margini in % applicati in proporzione, margini assoluti applicati tali e quali (pagine troppo piccole saltate, con avviso); annotazioni fuori dall'area → restano nel file ma non sono visibili, con avviso; pagina già ritagliata → margini calcolati rispetto al ritaglio attuale (opzione: rispetto alla pagina intera).
**Criteri di accettazione:**
- Data una pagina A4 senza `/CropBox`, quando si applicano margini di 10 mm su ogni lato, allora `/CropBox` vale [28,35 28,35 566,93 813,54] ±0,1 pt e la pagina visualizzata misura 190×277 mm.
- Data una pagina con `/Rotate` 90, quando si imposta un margine superiore di 20 mm, allora il taglio avviene sul lato che appare in alto nella visualizzazione.
- Data una pagina ritagliata, quando si esegue "Ripristina ritaglio", allora `/CropBox` è rimosso o uguale a `/MediaBox` e la vista torna quella originale.
- Dato testo fuori dall'area di ritaglio, quando si salva, allora un avviso informa che il contenuto nascosto resta nel file e propone l'appiattimento.
**Priorità:** P1

### PAG-26 — Rimuovi margini bianchi
**Descrizione:** Calcola automaticamente il riquadro del contenuto visibile di ogni pagina e imposta il ritaglio attorno ad esso, con margine aggiuntivo e tolleranza su ciò che conta come "bianco" (sfondi chiari, rumore di scansione). Modalità per pagina (ogni pagina ha il suo riquadro) o uniforme (unione dei riquadri delle pagine selezionate, per un formato coerente).
**Flusso:** 1. L'utente sceglie "Ritaglia > Rimuovi margini bianchi". 2. Imposta margine aggiuntivo, tolleranza, modalità, esclusione delle annotazioni e di una fascia di bordo. 3. Rivede in anteprima i rettangoli proposti e può modificarli. 4. Applica come in PAG-25.
**Formati/strutture:** Analisi del rendering a bassa risoluzione (pixel non bianchi oltre la tolleranza) combinata con i limiti degli oggetti grafici; risultato scritto in `/CropBox`.
**Casi limite:** Pagina completamente vuota → nessun ritaglio, pagina segnalata; contenuto invisibile (testo OCR invisibile, oggetti bianchi su bianco) → ignorato nel calcolo; sfondo a colore pieno → nessun margine trovato, avviso con suggerimento di aumentare la tolleranza; scansione con bordi scuri → opzione "ignora fascia di bordo" (percentuale).
**Criteri di accettazione:**
- Data una pagina A4 con un solo rettangolo nero di 100×100 pt in posizione nota e margine aggiuntivo 0, quando si applica, allora `/CropBox` coincide con il rettangolo ±1 pt.
- Dato un margine aggiuntivo di 5 mm in modalità uniforme su pagine con contenuti di estensione diversa, quando si applica, allora tutte le pagine hanno lo stesso `/CropBox`, che contiene il contenuto di ciascuna più 5 mm.
**Priorità:** P1

### PAG-27 — Riquadri di stampa (abbondanza, rifilo, area grafica)
**Descrizione:** Visualizza e imposta i riquadri di pagina per la produzione tipografica: supporto (MediaBox), ritaglio (CropBox), abbondanza (BleedBox), formato finito (TrimBox) e area significativa (ArtBox), con valori numerici e colori di visualizzazione; impostazione rapida "formato finito con abbondanza" (es. 3 mm) su intervalli; sovrapposizione dei riquadri attivabile nella vista.
**Flusso:** 1. L'utente apre "Riquadri di pagina". 2. Sceglie il riquadro e i valori (margini rispetto al supporto o formato finito centrato). 3. Indica l'ambito di pagine. 4. Applica; può mostrare o nascondere i riquadri in vista.
**Formati/strutture:** `/MediaBox`, `/CropBox`, `/BleedBox`, `/TrimBox`, `/ArtBox`, `/BoxColorInfo`, `/UserUnit`; ogni riquadro deve essere contenuto nel `/MediaBox`; per i profili PDF/X è richiesto `/TrimBox` o `/ArtBox`.
**Casi limite:** Riquadro non contenuto nel supporto → limitato, con avviso; `/TrimBox` e `/ArtBox` entrambi presenti → avviso per i profili PDF/X; abbondanza più piccola del formato finito → errore; riquadri assenti → mostrati i valori predefiniti dello standard (uguali al ritaglio) e contrassegnati come "predefinito".
**Criteri di accettazione:**
- Data una pagina di 216×303 mm, quando si imposta il formato finito A4 centrato con abbondanza di 3 mm, allora `/TrimBox` misura 210×297 mm e `/BleedBox` 216×303 mm, ±0,1 mm.
- Data una pagina senza riquadri di stampa, quando si apre il pannello, allora abbondanza, rifilo e area grafica sono mostrati uguali al ritaglio e contrassegnati come "predefinito".
**Priorità:** P2

### PAG-28 — Ridimensiona e cambia formato pagina
**Descrizione:** Cambia il formato delle pagine (formati standard o personalizzato) in due modalità: scala il contenuto per adattarlo al nuovo formato (proporzionale e centrato, oppure non proporzionale); oppure cambia solo la tela senza scalare, aggiungendo o togliendo spazio attorno al contenuto con ancoraggio a 9 posizioni o margini espliciti. Opzioni: adegua le pagine al formato di una pagina di riferimento; uniforma il formato di tutto il documento; normalizza l'origine dei riquadri.
**Flusso:** 1. L'utente sceglie "Formato pagina". 2. Imposta formato di destinazione, orientamento (automatico secondo la pagina o fisso), modalità, ancoraggio o margini. 3. Indica l'ambito. 4. Controlla l'anteprima e applica.
**Formati/strutture:** Nuovo `/MediaBox`, con `/CropBox`, `/BleedBox`, `/TrimBox` e `/ArtBox` ricalcolati o rimossi; la scala è applicata racchiudendo il contenuto esistente in una trasformazione, senza riscriverne gli oggetti; annotazioni, link e widget trasformati coerentemente (`/Rect`, `/QuadPoints`, `/InkList`, `/Vertices`, `/L`, `/CL`, `/RD` e aspetti); `/UserUnit` per formati oltre 14.400 unità.
**Casi limite:** Pagina con `/Rotate` 90 → il formato di destinazione si riferisce alla pagina visualizzata; proporzioni diverse → bande vuote (contieni) o parti tagliate (riempi, con avviso); formato uguale a quello attuale → pagina invariata; riduzione forte con testo che scende sotto 4 pt effettivi → avviso di leggibilità; campi modulo → aspetto rigenerato alla nuova scala; contenuto taggato → struttura logica invariata.
**Criteri di accettazione:**
- Data una pagina A3 con testo, quando si passa ad A4 con "scala il contenuto", allora la pagina misura 595×842 pt ±1, il contenuto è scalato di 0,707 ±0,01 e il testo estratto è identico a prima.
- Data una pagina A4 con un'evidenziazione, quando la pagina viene portata a Letter con scala, allora l'evidenziazione copre ancora la stessa parola.
- Data una pagina A4, quando si cambia solo la tela in A3 con ancoraggio centrale, allora il contenuto non è scalato ed è spostato di 123,3 pt in orizzontale e 174,3 pt in verticale (±0,5).
- Dato un documento con pagine di formati misti, quando si applica "adegua al formato della pagina 1", allora tutte le pagine hanno lo stesso `/MediaBox` della pagina 1.
**Priorità:** P1

### PAG-29 — Dividi una pagina in più pagine
**Descrizione:** Suddivide ciascuna pagina selezionata in più pagine secondo una griglia (righe × colonne, es. 1×2 per scansioni a doppia pagina, 2×2), linee di taglio posizionate a mano (anche non equidistanti) o il rilevamento automatico della piega centrale nelle scansioni di libri. Ordine delle parti sinistra→destra o destra→sinistra (scritture da destra a sinistra, rilegature orientali) e sovrapposizione facoltativa tra le parti.
**Flusso:** 1. L'utente sceglie "Dividi pagina". 2. Imposta metodo e parametri e vede le linee di taglio sulla pagina. 3. Indica l'ambito (es. tutte tranne la copertina). 4. Applica: ogni pagina viene sostituita dalle sue parti nell'ordine scelto.
**Formati/strutture:** Ogni parte è una nuova pagina che riusa lo stesso contenuto con `/MediaBox` e `/CropBox` pari alla porzione (stream condivisi, nessuna duplicazione); ogni annotazione è assegnata alla parte che la contiene o, se attraversa un taglio, a quella con l'area maggiore.
**Casi limite:** Linee di taglio sul bordo o coincidenti → ignorate; parti sotto la dimensione minima → errore; pagina con `/Rotate` → griglia riferita alla pagina visualizzata; testo esterno alla parte che resta nel file e può comparire in ricerca e copia → opzione "rimuovi il contenuto esterno" segnalata nel dialogo.
**Criteri di accettazione:**
- Data una scansione orizzontale di 842×595 pt con griglia 1×2, quando si applica, allora la pagina è sostituita da 2 pagine di 421×595 pt nell'ordine sinistra, destra.
- Con l'ordine destra→sinistra, quando si applica alla stessa pagina, allora la prima pagina risultante è la metà destra.
- Data una pagina con un'immagine da 5 MB divisa in 4 parti, quando si salva, allora la dimensione del file aumenta di meno di 20 kB.
**Priorità:** P1

### PAG-30 — Pagine affiancate (n-up)
**Descrizione:** Crea un nuovo PDF in cui più pagine sorgente sono disposte su ogni foglio, per stampa e dispense: 2, 4, 6, 8, 9, 16 o griglia personalizzata; formato e orientamento del foglio; ordine (per righe o per colonne, sinistra→destra o destra→sinistra); margini, spaziatura, bordo attorno a ogni pagina; scala (adatta alla cella o dimensione reale); rotazione automatica per il miglior adattamento; ripetizione della stessa pagina su tutto il foglio (es. biglietti da visita). Il documento originale non cambia.
**Flusso:** 1. L'utente sceglie "Pagine affiancate". 2. Imposta i parametri; l'anteprima mostra il primo foglio e il numero di fogli. 3. Indica l'ambito delle pagine sorgente. 4. Il nuovo documento si apre in una scheda, pronto da salvare o stampare.
**Formati/strutture:** Ogni pagina sorgente è incorporata una sola volta come Form XObject e disegnata con posizione e scala; annotazioni rese nell'aspetto (appiattite) o scartate, a scelta; link interni rimossi; testo estraibile.
**Casi limite:** Numero di pagine non multiplo delle celle → ultime celle vuote; pagine di formato o orientamento misto → ciascuna adattata alla propria cella mantenendo le proporzioni; pagina sorgente con `/Rotate` → considerata come visualizzata; griglia oltre 100 celle → avviso; celle più piccole del minimo → errore.
**Criteri di accettazione:**
- Dato un documento di 10 pagine A4 e "2 per foglio" su A4 orizzontale, quando si genera, allora il risultato ha 5 fogli A4 orizzontali e il foglio 1 contiene la pagina 1 a sinistra e la pagina 2 a destra.
- Dato "4 per foglio" in ordine per righe su 6 pagine, quando si genera, allora il foglio 2 contiene le pagine 5 e 6 nelle celle superiori e due celle vuote.
- Dato un documento con la stessa immagine su tutte le pagine, quando si genera il "4 per foglio", allora l'immagine è presente una sola volta nel file.
- Dato il risultato, quando si cerca una parola presente nella pagina 3 della sorgente, allora la parola viene trovata nel foglio che la contiene.
**Priorità:** P1


## 3. Modifica testo

**Ambito e convenzioni (valgono per tutte le funzioni TXT).** La sezione riguarda il *testo di pagina*, cioè il testo disegnato dai flussi di contenuto (content stream) delle pagine e dai Form XObject; il testo di annotazioni e campi modulo è trattato nelle rispettive sezioni e qui è richiamato solo dove interagisce. *Blocco*: unità di modifica (paragrafo o riga) riconosciuta da TXT-01. *Run*: sequenza di caratteri di un blocco con stile identico. *Riquadro*: rettangolo del blocco, la cui larghezza governa il ritorno a capo. Nei criteri di accettazione: «testo estratto» è il testo Unicode estratto (ISO 32000-2 §9.10) da uno strumento indipendente dall'editor sul file salvato e riaperto; «rendering identico» significa rasterizzazione a 150 dpi con differenza non superiore a 2/255 per canale su ogni pixel; «dimensione effettiva» è la dimensione del font moltiplicata per la scala della matrice di testo e della CTM; le coordinate sono in punti PDF nello spazio utente della pagina; «PDF di prova» indica un file della suite di test del progetto con le caratteristiche descritte; «hardware di riferimento» è quello definito nei requisiti non funzionali. I riferimenti «parità», «standard» e «concorrenti» rimandano alla numerazione degli inventari di ricerca del progetto.

### TXT-01 — Modalità Modifica e riconoscimento dei blocchi di testo

**Descrizione:** La modalità Modifica analizza il testo di pagina e lo raggruppa in blocchi modificabili, mostrandone i contorni al passaggio del mouse. È il punto d'ingresso di tutte le funzioni TXT: stabilisce quali testi sono modificabili, con quali limiti, e spiega perché un testo non lo è. Rif.: parità 3.1; standard cap. 29, 31, 39.

**Flusso:**
1. L'utente attiva «Modifica» (barra strumenti o scorciatoia; opzionalmente doppio clic su un testo in lettura, se abilitato nelle preferenze).
2. L'editor esegue i controlli di TXT-20 (permessi, firme, conformità) e analizza la pagina corrente: decodifica i codici dei glifi in Unicode, calcola posizione e avanzamento di ogni glifo, raggruppa i glifi in parole, le parole in righe (stessa linea di base e direzione) e le righe in paragrafi (interlinea regolare, allineamento e rientri coerenti, stessa colonna).
3. Al passaggio del mouse il blocco mostra il contorno; i blocchi non modificabili hanno un contorno distinto e un suggerimento con il motivo; icone dedicate segnalano font sostituiti (TXT-10) e contenuto condiviso tra pagine (TXT-20).
4. L'utente sceglie la granularità «Paragrafo» (predefinita) o «Riga»; la scelta viene ricordata.
5. Le altre pagine vengono analizzate in background quando entrano in vista; il cambio di strumento, o Esc senza alcuna selezione, esce dalla modalità confermando le modifiche in corso.

**Formati/strutture:** oggetti testo `BT`/`ET`; operatori `Tf`, `Tm`, `Td`/`TD`/`T*`, `Tj`/`TJ`/`'`/`"`; parametri `Tc`, `Tw`, `Tz`, `TL`, `Ts`, `Tr`; CTM (`cm`, `q`/`Q`); Form XObject (`Do`); decodifica tramite `/ToUnicode`, `/Encoding` con `/Differences` e nomi dei glifi, CMap dei font composti; `/ActualText`; marked content (`BDC`/`EMC`, `/MCID`, `/Artifact`, `/OC`) (ISO 32000-2 §8.11, §9.3–9.4, §9.10, §14.6).

**Casi limite:**
- Pagina composta solo da un'immagine (scansione) → nessun blocco; banner che propone l'OCR (TXT-15).
- Font senza `/ToUnicode` e con codifica non standard → l'editor tenta di ricavare i caratteri dai nomi dei glifi o dalle tabelle interne del font; se l'esito è incerto il blocco è «non decodificabile»: è ammessa solo la sostituzione integrale con nuovo testo (TXT-10) o l'OCR dell'area.
- Testo convertito in tracciati o contenuto in immagini → non riconosciuto come testo; suggerimento di OCR.
- Testo su percorso curvo o con glifi ruotati singolarmente → blocco «a posizionamento libero», modificabile carattere per carattere senza riflusso.
- Testo usato come tracciato di ritaglio (modalità di resa 4–7) → modificabile; la modalità di resa è conservata e il riempimento segue la nuova forma.
- Testo in un livello nascosto (OCG disattivo) → non mostrato né modificabile finché il livello resta nascosto.
- Testo invisibile (`Tr 3`, tipico dell'OCR) → gestito da TXT-15.
- Pagina con decine di migliaia di glifi → analisi incrementale; l'interfaccia resta reattiva.

**Criteri di accettazione:**
- Dato un PDF di prova con 3 paragrafi di 4 righe in una colonna, quando si attiva la modalità Modifica con granularità «Paragrafo», allora vengono riconosciuti esattamente 3 blocchi, ciascuno con tutte e sole le righe del proprio paragrafo.
- Dato lo stesso PDF, quando si passa alla granularità «Riga», allora vengono riconosciuti 12 blocchi.
- Dato un PDF a due colonne, quando si attiva la modalità Modifica, allora nessun blocco contiene righe di entrambe le colonne.
- Dato un PDF di 300 pagine, quando si attiva la modalità Modifica, allora i blocchi della pagina corrente sono disponibili entro 500 ms su hardware di riferimento e l'interfaccia non resta mai senza risposta per più di 100 ms durante l'analisi delle altre pagine.
- Dato un PDF la cui pagina è un'unica immagine scansionata, quando si attiva la modalità Modifica, allora non viene creato alcun blocco e compare la proposta di OCR.
- Dato un PDF con un font privo di `/ToUnicode` e con codifica personalizzata non ricostruibile, quando si attiva la modalità Modifica, allora il blocco è marcato «non decodificabile» e il suggerimento ne indica il motivo.
- Dato un documento aperto, quando si entra e si esce dalla modalità Modifica senza modificare nulla, allora il documento non risulta modificato.

**Priorità:** P0

### TXT-02 — Modifica in loco del testo esistente

**Descrizione:** Dentro un blocco l'utente posiziona il cursore e inserisce, cancella o sostituisce caratteri come in un elaboratore di testi. Il testo nuovo eredita lo stile del contesto; font, dimensione, colore e posizione del blocco sono preservati; ciò che si vede durante la modifica coincide con il risultato salvato. Rif.: parità 3.1; standard cap. 29, 39, 40.

**Flusso:**
1. Clic nel blocco: il cursore si colloca nel punto del clic; doppio clic seleziona la parola, triplo clic il paragrafo.
2. L'utente digita, cancella (Backspace/Canc, per parola con Ctrl), seleziona (mouse, Maiusc+frecce) e sostituisce la selezione digitando o incollando; Invio crea un nuovo paragrafo nel blocco, Maiusc+Invio un'interruzione di riga.
3. Il carattere inserito assume lo stile del carattere precedente (del successivo se all'inizio del blocco); la sostituzione di una selezione assume lo stile del suo primo carattere.
4. Il blocco viene ricomposto in tempo reale (TXT-03); i caratteri che il font non può rappresentare sono gestiti da TXT-10.
5. Clic fuori dal blocco o Esc confermano (con Esc il blocco resta selezionato come oggetto, TXT-07): il blocco viene riscritto nel contenuto della pagina e il documento risulta modificato.
6. Gli operatori dei blocchi non modificati restano invariati; nel blocco modificato le righe la cui composizione non cambia mantengono gli operatori originali.

**Formati/strutture:** riscrittura delle sequenze `BT…ET` del blocco con `Tf`, `Tm`, `Tj`/`TJ` (crenatura come spostamenti in `TJ`), `Tc`/`Tw`/`Tz`/`Ts`/`Tr` e colore nello stesso spazio colore dell'originale (`g`, `rg`, `k`, `cs`/`scn`); conversione inversa Unicode → codici del font (`/Encoding`, CMap, `/ToUnicode`) ed estensione di `/Widths`/`/W` per i codici nuovi; stato grafico del contesto conservato (ExtGState con opacità e fusione, ritaglio, appartenenza a livelli); `BDC`/`EMC` con lo stesso `/MCID`; `/ActualText` aggiornato, o rimosso se non più coerente (ISO 32000-2 §9.3–9.4, §9.6–9.7, §9.10, §14.6). Immissione: tastiera, metodi di input del sistema con composizione (preedit), tasti morti e tasto Compose.

**Casi limite:**
- Legature (fi, ffl) → cursore e cancellazione operano sui singoli caratteri; una modifica interna scompone la legatura.
- Spazi impliciti (parole separate solo da posizionamento) → trattati come spazi in modifica; alla riscrittura si usa il glifo spazio del font se esiste, altrimenti uno spostamento equivalente.
- Glifi posizionati uno per uno → il blocco è ricostruito con una spaziatura uniforme stimata (`Tc`), in modo che la larghezza delle righe riscritte differisca al massimo del 2% dall'originale.
- Crenatura manuale in `TJ` → conservata sui caratteri non toccati; il testo nuovo usa la crenatura del font, se disponibile.
- Testo disegnato più volte con piccolo scostamento (finto grassetto, ombra) → riconosciuto come un solo blocco con effetto; la modifica si applica a tutte le copie.
- Blocco ruotato o pagina con `/Rotate` → cursore e selezione seguono la linea di base; l'immissione resta naturale per l'utente.
- Testo tagliato da un tracciato di ritaglio → il testo nuovo che cade fuori dal ritaglio resta invisibile; viene mostrato un indicatore.
- Intestazioni/piè di pagina creati con lo strumento dedicato (artefatti riconoscibili) → si propone di modificarli con quello strumento.
- Cancellazione di tutto il testo del blocco → alla conferma il blocco è rimosso, senza oggetti testo vuoti residui.

**Criteri di accettazione:**
- Dato un PDF di prova con la frase «Il contratto scade nel 2024» in un font TrueType incorporato come sottoinsieme che contiene le cifre necessarie, quando l'utente sostituisce «2024» con «2026» e salva, allora il testo estratto contiene «scade nel 2026» e non contiene «2024».
- Dato lo stesso scenario, quando si esaminano gli attributi del testo salvato, allora «2026» usa la stessa risorsa font dell'originale, la stessa dimensione effettiva (±0,05 pt) e lo stesso colore (componenti identiche nello stesso spazio colore), e l'origine del primo carattere della riga (allineata a sinistra) non si sposta più di 0,5 pt.
- Dato un documento con 10 blocchi, quando si modifica solo il blocco 3 e si salva, allora il rendering delle aree degli altri 9 blocchi è identico all'originale e il loro testo estratto è invariato.
- Dato un blocco in modifica, quando si confronta il rendering a schermo prima della conferma (esclusi cursore e contorni) con il rendering della pagina salvata, allora le due immagini sono identiche nell'area del blocco.
- Dato un blocco in modifica, quando l'utente immette «è» con un tasto morto e «中» con un metodo di input a composizione, allora i caratteri inseriti sono esattamente U+00E8 e U+4E2D.
- Dato un documento taggato, quando si modifica un paragrafo e si salva, allora il suo elemento di struttura rimanda ancora allo stesso `/MCID` e il testo estratto seguendo la struttura riflette la modifica.

**Priorità:** P0

### TXT-03 — Ritorno a capo e riflusso nel riquadro

**Descrizione:** Durante la modifica il testo viene ricomposto entro la larghezza del riquadro: le parole vanno a capo, allineamento, interlinea e rientri rilevati sono rispettati e l'altezza si adatta al contenuto. Il riflusso è confinato al blocco: non sposta altri contenuti e non prosegue su altre pagine. Rif.: parità 3.1.

**Flusso:**
1. All'ingresso in modifica l'editor ricava dal blocco: larghezza del riquadro, allineamento (sinistra, centro, destra, giustificato), interlinea, rientri e tipo (a larghezza fissa se multiriga, a larghezza automatica se di una sola riga).
2. A ogni modifica ricalcola le interruzioni di riga del paragrafo modificato con le larghezze reali dei glifi; i paragrafi precedenti restano invariati, quelli successivi del blocco si spostano senza cambiare composizione.
3. A larghezza fissa: l'altezza cresce o cala verso il basso, con la prima linea di base ferma.
4. A larghezza automatica: la riga si allunga secondo l'allineamento (a destra se allineata a sinistra, a sinistra se allineata a destra, su entrambi i lati se centrata) fino al bordo dell'area visibile della pagina; da lì il testo va a capo e il blocco diventa a larghezza fissa.
5. Se il blocco si sovrappone ad altro contenuto o esce dalla pagina compare un indicatore di sovrapposizione/trabocco; nessun altro contenuto viene spostato.

**Formati/strutture:** interruzioni di riga secondo l'algoritmo Unicode UAX #14; misure da `/Widths`/`/W` o dal programma del font, con `Tz`, `Tc`, `Tw`; interlinea tradotta in `TL`/`T*` o `Td`; giustificazione con `Tw` (che agisce solo sul codice a byte singolo 32) o, nei font composti, con spostamenti in `TJ`; trattino morbido U+00AD rispettato; nei documenti taggati un paragrafo creato con Invio genera un elemento `/P` fratello.

**Casi limite:**
- Parola più lunga della riga (URL, codici) → spezzata al carattere.
- Trattino di sillabazione a fine riga nell'originale → se la parola non è più a fine riga il trattino viene rimosso e la parola ricomposta; i trattini propri della parola («Emilia-Romagna») restano.
- Paragrafo giustificato → giustificate tutte le righe tranne l'ultima di ogni paragrafo.
- Riquadro più stretto del glifo più largo → larghezza minima pari a quel glifo.
- Run di dimensioni diverse nella stessa riga → l'altezza della riga si adatta al run più grande mantenendo il rapporto d'interlinea.
- Blocchi a posizionamento libero → nessun riflusso; blocchi verticali o da destra a sinistra → TXT-21.

**Criteri di accettazione:**
- Dato un paragrafo giustificato di 5 righe a larghezza fissa, quando l'utente inserisce 40 caratteri nella prima riga, allora nessuna riga supera la larghezza del riquadro (±0,5 pt), tutte le righe tranne l'ultima toccano entrambi i margini (±0,5 pt) e la distanza tra linee di base consecutive è quella originale (±0,1 pt).
- Dato un paragrafo con «progetto» diviso a fine riga in «pro-»/«getto», quando una modifica precedente porta la parola a metà riga, allora il testo estratto contiene «progetto» senza trattino.
- Dato un titolo di una riga allineato a destra, quando si aggiungono caratteri, allora il margine destro resta fermo (±0,5 pt) e la riga si estende verso sinistra.
- Dato un blocco a larghezza fissa sopra un altro blocco, quando il testo aggiunto lo fa sovrapporre al blocco sottostante, allora quest'ultimo non cambia posizione e compare l'indicatore di sovrapposizione.
- Dato un paragrafo di 3 righe, quando si cancella una parola nella terza riga, allora il rendering delle prime due righe è identico all'originale.

**Priorità:** P0

### TXT-04 — Formattazione dei caratteri

**Descrizione:** Modifica delle proprietà del testo selezionato o dell'intero blocco: font (famiglia e stile), dimensione, colore, grassetto, corsivo, sottolineato, barrato, spaziatura tra caratteri, scala orizzontale, apice e pedice. Il pannello Proprietà mostra i valori del punto d'inserimento o della selezione, e «misto» quando sono eterogenei. Rif.: parità 3.3; standard cap. 29–30.

**Flusso:**
1. L'utente seleziona testo in un blocco, oppure seleziona il blocco come oggetto per agire su tutto il suo testo.
2. Il pannello mostra i valori correnti.
3. L'utente modifica un valore: elenco font con anteprima e ricerca (in testa i font già usati nel documento, con la loro copertura); dimensione da elenco o digitata (passo 0,1 pt); colore da selettore con valori RGB, CMYK, grigio, esadecimale e colori recenti; pulsanti grassetto/corsivo/sottolineato/barrato con scorciatoie configurabili; campi numerici per spaziatura (millesimi di em), scala orizzontale (%) e spostamento della linea di base.
4. L'editor crea o aggiorna i run, ricompone il blocco (TXT-03) e verifica la copertura del nuovo font (TXT-10).

**Formati/strutture:** cambio di font → nuova risorsa `/Font` incorporata come sottoinsieme con `/ToUnicode`; grassetto/corsivo → variante della stessa famiglia se disponibile, altrimenti simulazione dichiarata nel pannello («stile simulato»): grassetto con `Tr 2` e spessore del tratto proporzionale alla dimensione, corsivo con inclinazione di circa 12° nella matrice `Tm`; sottolineato e barrato → tracciati nel colore del testo, associati al run e ricalcolati a ogni riflusso; spaziatura → `Tc`; scala → `Tz`; apice/pedice → `Ts` con dimensione ridotta (predefinita 65%); colore → operatori di riempimento (e di tratto per il testo con contorno, `Tr 1`/`2`) in uno spazio colore coerente con il documento (ISO 32000-2 §8.6, §9.3).

**Casi limite:**
- Nuovo font privo di alcuni caratteri del testo → TXT-10, con avviso.
- Font di sistema la cui licenza vieta l'incorporamento → mostrato disattivato, con il motivo.
- Documento con intento di output CMYK o dichiarato PDF/X → il selettore propone CMYK; un colore RGB viene convertito con avviso.
- Testo in tinta piatta (Separation) → conservata se il colore non cambia; in modifica si può variare la percentuale della stessa tinta o passare a un colore di quadricromia.
- Riempimento a pattern o sfumatura → mostrato come «pattern» e conservato se non modificato.
- Sottolineature dell'originale disegnate come linee → associate al run se si trovano sotto la linea di base entro 0,3 em e ne coprono l'estensione; altrimenti restano grafica indipendente.
- Dimensione fuori dall'intervallo 1–1000 pt → valore rifiutato con messaggio.

**Criteri di accettazione:**
- Dato un blocco in un font incorporato, quando l'utente seleziona una parola e imposta 18 pt e colore #C00000, allora dopo il salvataggio quella parola ha dimensione effettiva 18 pt (±0,05) e colore RGB (0,753; 0; 0) (±0,002) e le altre parole conservano i valori originali.
- Dato un testo di una famiglia di cui è installata la variante grassetto, quando si applica il grassetto, allora il run usa il font della variante grassetto e non la simulazione.
- Dato un font senza variante corsiva disponibile, quando si applica il corsivo, allora la matrice di testo del run contiene un'inclinazione non nulla e il pannello indica «stile simulato».
- Dato un paragrafo con una parola sottolineata nella seconda riga, quando una modifica sposta la parola nella terza riga, allora la sottolineatura si trova sotto la parola nella nuova posizione (±0,5 pt) e non è più presente in quella vecchia.
- Dato un run, quando si imposta la spaziatura a +50 millesimi di em, allora la distanza tra le origini di due caratteri consecutivi aumenta di 0,05 × la dimensione effettiva (±0,05 pt).

**Priorità:** P0

### TXT-05 — Formattazione del paragrafo

**Descrizione:** Proprietà di paragrafo: allineamento orizzontale (sinistra, centro, destra, giustificato), interlinea, spazio prima e dopo, rientro sinistro e di prima riga. Si applicano ai paragrafi toccati dalla selezione o a tutto il blocco selezionato. Rif.: parità 3.3.

**Flusso:**
1. Il cursore o la selezione individuano uno o più paragrafi; il pannello mostra i valori rilevati.
2. L'utente sceglie allineamento (pulsanti e scorciatoie configurabili), interlinea (multiplo da 0,5 a 5,0 della dimensione del font o valore esatto in pt), spazio prima/dopo e rientri (pt o mm).
3. Il blocco viene ricomposto (TXT-03) con la prima linea di base ferma.

**Formati/strutture:** il PDF non memorizza parametri di paragrafo: l'editor li deriva dalla geometria e li riapplica nella ricomposizione tramite posizionamenti (`Td`, `Tm`, `TL`) e `Tw`/`TJ`; nei documenti taggati legge e aggiorna gli attributi di layout eventualmente presenti (`/TextAlign`, `/LineHeight`, `/SpaceBefore`, `/SpaceAfter`, `/StartIndent`, `/TextIndent`; ISO 32000-2 §14.8.5.4).

**Casi limite:**
- Paragrafo di elenco puntato o numerato → simbolo e rientro sporgente sono conservati; le righe successive si allineano al testo, non al simbolo.
- Interlinea inferiore a 1,0 → ammessa fino a 0,5, con avviso di possibile sovrapposizione.
- Paragrafo di una sola riga → allineamento ambiguo; si assume sinistra (destra per le scritture da destra a sinistra), modificabile.
- Giustificazione in font composti → realizzata con spostamenti in `TJ`.

**Criteri di accettazione:**
- Dato un paragrafo allineato a sinistra di 4 righe, quando si applica «centrato», allora in ogni riga il centro del testo dista non più di 0,5 pt dal centro del riquadro.
- Dato un paragrafo con interlinea 1,0, quando si imposta 1,5, allora la distanza tra linee di base consecutive diventa 1,5 × la dimensione effettiva (±0,1 pt) e la prima linea di base non si sposta.
- Dato un blocco con due paragrafi, quando si impostano 12 pt di spazio dopo il primo, allora la distanza tra l'ultima linea di base del primo e la prima del secondo aumenta di 12 pt (±0,1).
- Dato un documento taggato con `/TextAlign /Start` su un paragrafo, quando l'utente lo centra e salva, allora l'attributo vale `/Center`.

**Priorità:** P0

### TXT-06 — Aggiunta di nuove caselle di testo

**Descrizione:** Crea un nuovo blocco di testo in qualsiasi punto della pagina, scritto direttamente nel contenuto della pagina (non come annotazione): con un clic si ottiene un blocco a larghezza automatica, trascinando un rettangolo un blocco a larghezza fissa. Rif.: parità 3.2; standard cap. 29–30.

**Flusso:**
1. L'utente sceglie «Aggiungi testo».
2. Clic: nasce un blocco a larghezza automatica il cui angolo superiore sinistro è il punto del clic; trascinamento: nasce un blocco a larghezza fissa largo quanto il rettangolo.
3. Stile iniziale: quello predefinito nelle preferenze oppure l'ultimo usato (a scelta nelle preferenze).
4. L'utente scrive; valgono TXT-02…TXT-05 e TXT-10.
5. Clic fuori o Esc confermano; un blocco vuoto viene scartato. Lo strumento torna alla modalità Modifica (doppio clic sull'icona lo mantiene attivo per più inserimenti).

**Formati/strutture:** nuovo flusso aggiunto all'array `/Contents` della pagina, dopo aver isolato lo stato grafico del contenuto esistente (racchiuso tra `q` e `Q` bilanciati); font aggiunto alle risorse della pagina come sottoinsieme incorporato con `/ToUnicode` (le risorse ereditate o condivise tra pagine vengono prima copiate per la sola pagina); nei documenti taggati nuovo elemento `/P` con `/MCID` e voce nel `/ParentTree`, collocato nell'ordine di lettura dopo il blocco che lo precede nella pagina; lingua dal `/Lang` del documento (ISO 32000-2 §7.7.3, §7.8, §14.7–14.8).

**Casi limite:**
- Pagina con `/Rotate` 90/180/270 → il testo nasce orizzontale rispetto alla vista dell'utente.
- `/CropBox` diversa dalla `/MediaBox` o `/UserUnit` diverso da 1 → posizione e dimensioni riferite all'area visibile; la dimensione in pt è quella percepita.
- Clic sopra un blocco esistente con lo strumento attivo → nasce comunque un nuovo blocco sovrapposto; un suggerimento ricorda che il testo esistente si modifica con «Modifica».
- Caratteri non coperti da alcun font installato → rifiutati con messaggio; mai glifi mancanti nel file.
- Documento dichiarato PDF/A → il font deve essere incorporabile (TXT-20).

**Criteri di accettazione:**
- Dato un PDF senza testo, quando l'utente fa clic nel punto (100, 700), scrive «Nuovo testo» e salva, allora il testo estratto contiene «Nuovo testo» e l'angolo superiore sinistro del riquadro del blocco coincide con (100, 700) entro 1 pt.
- Dato un blocco nuovo salvato, quando il file viene visualizzato con un altro lettore conforme su un sistema privo dei font dell'editor, allora il rendering del blocco è identico a quello dell'editor e il font risulta incorporato con `/ToUnicode`.
- Dato un documento taggato, quando si aggiunge un blocco e si salva, allora l'albero di struttura contiene un nuovo elemento `/P` il cui testo è quello aggiunto.
- Dato un blocco nuovo lasciato vuoto, quando lo si conferma, allora il contenuto della pagina non cambia e il documento non risulta modificato.
- Dato un blocco a larghezza automatica creato a 50 pt dal bordo destro, quando si digita oltre quel limite, allora il testo va a capo al bordo dell'area visibile e nessun glifo esce dalla pagina.
- Data una pagina con `/Rotate 90`, quando si aggiunge testo, allora nel rendering della pagina così come visualizzata il testo appare orizzontale e leggibile da sinistra a destra.

**Priorità:** P0

### TXT-07 — Spostamento, ridimensionamento, copia ed eliminazione dei blocchi

**Descrizione:** In modalità Modifica un blocco può essere selezionato come oggetto per spostarlo, cambiarne la larghezza (con riflusso), copiarlo, duplicarlo, tagliarlo e incollarlo anche su un'altra pagina o in un altro documento, ed eliminarlo. Rif.: parità 3.1–3.2, 4.11.

**Flusso:**
1. Clic sul contorno del blocco (o Esc con il cursore nel blocco): il blocco è selezionato come oggetto, con maniglie.
2. Trascinamento: spostamento con anteprima; frecce: 1 pt per pressione (Maiusc: 10 pt).
3. Maniglie laterali: cambiano la larghezza; il blocco diventa a larghezza fissa e il testo rifluisce. L'altezza segue sempre il contenuto.
4. Ctrl+C / Ctrl+X / Ctrl+V: copia, taglia, incolla il blocco con stile e font (su un'altra pagina nella stessa posizione; sulla stessa pagina scostato di 10 pt); Ctrl+D duplica.
5. Canc: elimina il blocco e gli elementi associati (sottolineature, barrati, elementi di struttura).
6. Selezione multipla (Maiusc+clic o rettangolo di selezione): le operazioni valgono per tutti i blocchi selezionati.

**Formati/strutture:** spostamento come traslazione della matrice `Tm` (o di una `cm` dedicata) e dei tracciati associati; eliminazione degli operatori di disegno del blocco; risorse font non più usate rimosse nel salvataggio completo; nei documenti taggati rimozione degli elementi di struttura rimasti vuoti e delle relative voci del `/ParentTree`; copia tra documenti con trasferimento del programma del font e della `/ToUnicode`; appunti con un formato interno ricco più `text/plain` e `text/html` per le altre applicazioni.

**Casi limite:**
- Blocco sotto un ritaglio condiviso con altro contenuto → avviso che lo spostamento può nasconderlo; un ritaglio che riguarda solo il blocco si sposta con esso.
- Blocco spostato interamente fuori dall'area visibile → spostamento annullato con messaggio.
- Blocco in un Form XObject usato da più pagine → TXT-20.
- Annotazioni ancorate al testo del blocco (evidenziazioni, link) → riallineate secondo TXT-20.
- Eliminazione per riservatezza → avviso che il salvataggio incrementale conserva la versione precedente; rimando alla Redazione e al salvataggio completo.

**Criteri di accettazione:**
- Dato un blocco con origine della prima linea di base in (72, 700), quando lo si sposta di +100 pt in orizzontale e −50 pt in verticale e si salva, allora l'origine è (172, 650) ±0,5 pt e il testo estratto è invariato.
- Dato un paragrafo a larghezza fissa di 300 pt su 6 righe, quando lo si restringe a 150 pt, allora nessuna riga supera 150 pt e le righe diventano più di 6.
- Dato un blocco, quando lo si elimina e si esegue un salvataggio completo, allora il testo estratto non contiene il suo testo e la risorsa font usata solo da esso non è più presente nella pagina.
- Dato un blocco copiato dalla pagina 1 del documento A e incollato nella pagina 5 del documento B, quando si salva B, allora il blocco in B ha lo stesso nome base del font, la stessa dimensione effettiva e rendering identico a quello in A.
- Dato un blocco con una parola sottolineata, quando lo si sposta, allora la sottolineatura si sposta dello stesso vettore (±0,1 pt).

**Priorità:** P0

### TXT-08 — Disposizione avanzata dei blocchi

**Descrizione:** Strumenti d'impaginazione per i blocchi di testo: rotazione, allineamento e distribuzione di più blocchi, guide magnetiche durante lo spostamento, ordine di sovrapposizione, spostamento per trascinamento su un'altra pagina. Lo stesso comportamento vale per immagini e altri oggetti (sezione dedicata). Rif.: parità 4.11–4.12; concorrenti §17.

**Flusso:**
1. Selezione di uno o più blocchi.
2. Rotazione con la maniglia (passi di 15° con Maiusc), con angolo numerico o con i comandi ±90°.
3. Allinea a sinistra, al centro, a destra, in alto, al centro verticale, in basso, rispetto alla selezione o alla pagina; distribuisci in orizzontale o in verticale.
4. Durante il trascinamento compaiono guide verso bordi, centri e linee di base degli altri blocchi e verso bordi e centro della pagina; l'aggancio si sospende tenendo premuto un tasto modificatore configurabile e si disattiva nelle preferenze.
5. Ordine: porta in primo piano o sullo sfondo, avanti o indietro di un livello.
6. In vista continua il blocco trascinato su un'altra pagina viene rimosso dalla sorgente e creato nella destinazione.

**Formati/strutture:** rotazione nella matrice `Tm` o in una `cm` dedicata; ordine di sovrapposizione ottenuto riordinando i segmenti del flusso di contenuto senza rompere coppie `q`/`Q` e sequenze marcate; spostamento tra pagine con copia delle risorse e, nei documenti taggati, aggiornamento di `/Pg`, `/MCID` e `/ParentTree` (ISO 32000-2 §8.3–8.4, §14.7).

**Casi limite:**
- Blocco ruotato → resta modificabile e rifluisce lungo la propria direzione.
- Blocco portato sotto un'immagine opaca → consentito, con avviso che il testo non è più visibile.
- Pagina di destinazione di formato diverso → posizione mantenuta rispetto all'angolo superiore sinistro e blocco vincolato entro la pagina.
- Blocchi in Form XObject condivisi o in livelli bloccati → esclusi dalle operazioni, con il motivo.

**Criteri di accettazione:**
- Dato un blocco orizzontale, quando lo si ruota di 30° e si salva, allora il testo estratto è invariato e la linea di base forma con l'asse x un angolo di 30° (±0,5°).
- Dati 3 blocchi selezionati, quando si sceglie «Allinea a sinistra», allora i bordi sinistri dei tre riquadri coincidono (±0,1 pt) con quello più a sinistra.
- Dato un blocco a pagina 2 in vista continua, quando lo si trascina a pagina 3 e si salva, allora il testo estratto della pagina 2 non lo contiene e quello della pagina 3 sì, con lo stesso font e la stessa dimensione effettiva.
- Dati due blocchi sovrapposti, quando si porta in primo piano quello sottostante, allora i suoi operatori seguono quelli dell'altro nel flusso di contenuto e il rendering mostra il suo testo sopra.

**Priorità:** P1

### TXT-09 — Unione e divisione dei blocchi

**Descrizione:** Corregge il raggruppamento automatico quando non corrisponde all'intenzione dell'utente: unisce più blocchi in un unico blocco rifluibile oppure divide un blocco in due nel punto del cursore. Rif.: concorrenti §17; complemento di TXT-01.

**Flusso:**
1. Unione: l'utente seleziona due o più blocchi della stessa pagina e sceglie «Unisci»; il testo è concatenato nell'ordine di lettura (struttura logica se presente, altrimenti dall'alto in basso e da sinistra a destra), separando i blocchi con uno spazio o con un nuovo paragrafo, a scelta.
2. Il blocco unito ha come riquadro l'unione dei riquadri e allineamento e interlinea del primo blocco; il testo viene ricomposto solo alla prima modifica, quindi l'unione da sola non cambia l'aspetto della pagina.
3. Divisione: con il cursore nel blocco l'utente sceglie «Dividi qui»; il testo dopo il cursore diventa un nuovo blocco nella posizione attuale.
4. «Rileva di nuovo» ripristina il raggruppamento automatico della pagina senza annullare le modifiche al testo.

**Formati/strutture:** modifica del solo modello di raggruppamento finché il testo non cambia, poi riscrittura come in TXT-02; nei documenti taggati gli elementi di struttura vengono uniti o divisi in modo da avere un `/P` per ogni paragrafo risultante.

**Casi limite:**
- Blocchi con rotazioni diverse, su pagine diverse o in Form XObject diversi → unione non consentita, con messaggio.
- Blocchi con stili diversi → ogni run conserva il proprio stile.
- Divisione all'inizio o alla fine del blocco → nessun effetto.

**Criteri di accettazione:**
- Date due righe riconosciute come blocchi separati, quando si uniscono e si inserisce testo nella prima, allora il testo eccedente rifluisce nella seconda riga.
- Dato un blocco di 4 righe, quando lo si divide all'inizio della terza riga senza modificare il testo e si salva, allora il rendering della pagina è identico all'originale e in modalità Modifica risultano 2 blocchi.
- Dato un documento taggato con due paragrafi in elementi `/P` distinti, quando si uniscono con separatore «spazio», si modifica il testo e si salva, allora l'albero di struttura contiene un solo `/P` con tutto il testo del blocco unito.

**Priorità:** P1

### TXT-10 — Font non incorporati o incorporati come sottoinsieme

**Descrizione:** Quando il testo modificato o aggiunto richiede glifi che il font originale non può fornire (font non incorporato, sottoinsieme privo dei caratteri, font Type 3, codifica limitata), l'editor sceglie un font sostitutivo con una regola deterministica, avvisa in modo non bloccante, evidenzia i caratteri interessati e lascia all'utente il controllo. Nessun file salvato contiene caratteri senza glifo. Rif.: parità 3.1, 3.3; standard cap. 30, 40; concorrenti §18.

**Flusso:**
1. All'ingresso in un blocco l'editor calcola la copertura di ogni font: per i font incorporati, i caratteri presenti nel programma del font e raggiungibili con la sua codifica; per quelli non incorporati, i caratteri esprimibili nella sua codifica.
2. Per un carattere non coperto cerca, nell'ordine: (a) lo stesso font installato nel sistema (nome PostScript senza prefisso di sottoinsieme, oppure famiglia e stile) con metriche compatibili con `/Widths`; (b) la tabella personale di corrispondenze (TXT-11); (c) un font metricamente compatibile scelto in base al descrittore (con o senza grazie, monospazio, peso, angolo del corsivo, larghezza); (d) un font di ripiego che copra lo script del carattere.
3. Nel caso (a) il testo usa una nuova istanza incorporata dello stesso font, visivamente identica, senza avviso. Nei casi (b)–(d), per default, solo i caratteri non coperti usano il sostituto e vengono evidenziati in modifica.
4. Un avviso non modale riporta font originale, sostituto e numero di caratteri interessati, con le azioni «Scegli un altro font», «Applica il sostituto a tutto il blocco», «Annulla».
5. Al salvataggio ogni font nuovo è incorporato come sottoinsieme con `/ToUnicode`; i riferimenti originali non incorporati restano invariati per il testo non modificato.

**Formati/strutture:** `/FontDescriptor` (`/FontName`, `/Flags`, `/FontWeight`, `/ItalicAngle`, `/FontFile`, `/FontFile2`, `/FontFile3`); prefisso di sottoinsieme (sei lettere maiuscole seguite da «+»); `/Encoding` e `/Differences`; `/Widths` con `/FirstChar`/`/LastChar`; font composti (`/Type0`, CMap `Identity-H`, `/CIDToGIDMap`, `/W`); permessi d'incorporamento del programma del font (campo `fsType` della tabella OS/2, ISO/IEC 14496-22); i 14 font standard sono trattati come non incorporati (ISO 32000-2 §9.6.2.2, §9.7, §9.8, §9.9).

**Casi limite:**
- Font non incorporato la cui codifica contiene il carattere (es. «é» in `WinAnsiEncoding`) → nessuna sostituzione; se `/Widths` non copre il codice viene estesa con le metriche del font di sistema usato per la resa.
- Font Type 3 → riuso dei glifi esistenti; i caratteri nuovi usano un sostituto.
- Blocco non decodificabile (TXT-01) → solo riscrittura integrale con un font scelto dall'utente.
- Candidato con licenza che vieta l'incorporamento → scartato; se non resta alcun candidato il carattere viene rifiutato con messaggio.
- Font di sistema omonimo ma con metriche diverse (altra versione) → non considerato «stesso font»; si passa al punto (b).
- Documento dichiarato PDF/A → si accettano solo sostituti incorporabili (TXT-20).

**Criteri di accettazione:**
- Dato un blocco in un font incorporato come sottoinsieme privo della «Q», con lo stesso font non installato, quando l'utente digita «Q», allora il carattere è reso con un font sostitutivo, è evidenziato in modifica e compare l'avviso con il nome del font originale e del sostituto.
- Dato lo scenario precedente salvato, quando si analizza il file, allora nessun codice di testo della pagina è privo di glifo (nessun `.notdef`), il sostituto è incorporato come sottoinsieme con `/ToUnicode` e il testo estratto contiene «Q».
- Dato lo stesso documento con il font originale installato nel sistema, quando l'utente digita «Q», allora non compare alcun avviso e il nome base del font del nuovo carattere coincide con quello originale.
- Dato un blocco in `/Helvetica` non incorporata con codifica `WinAnsiEncoding`, quando l'utente sostituisce una parola con «città» e salva, allora il testo usa ancora la risorsa font originale, nessun nuovo font è stato aggiunto e il testo estratto contiene «città».
- Dato un sostituto applicato a 3 caratteri, quando l'utente sceglie «Applica il sostituto a tutto il blocco», allora tutti i run del blocco usano il sostituto e l'avviso scompare.
- Dato un unico candidato con incorporamento vietato dalla licenza, quando l'utente digita un carattere non coperto, allora il carattere non viene inserito e compare un messaggio esplicito.

**Priorità:** P0

### TXT-11 — Elenco dei font del documento e sostituzione globale

**Descrizione:** Pannello con tutti i font usati dal documento (tipo, codifica, stato di incorporamento: completo, sottoinsieme, non incorporato; pagine d'uso; font usato per la visualizzazione) da cui sostituire un font in tutto il documento, incorporare un font non incorporato e gestire la tabella personale di corrispondenze usata da TXT-10. Rif.: standard cap. 30; concorrenti §18.

**Flusso:**
1. L'utente apre «Font del documento».
2. L'elenco mostra, per ogni font, nome senza prefisso, tipo, codifica, stato, pagine e sostituto in uso; il filtro «solo problemi» mostra font non incorporati, sostituiti o senza `/ToUnicode`.
3. «Sostituisci»: font di destinazione e ambito (documento o intervallo di pagine); i blocchi interessati vengono ricomposti mantenendo origine e allineamento; un riepilogo elenca i blocchi la cui larghezza è cambiata oltre il 5%.
4. «Incorpora»: per un font non incorporato installato nel sistema con metriche compatibili, incorpora il sottoinsieme dei glifi usati senza cambiare l'impaginazione.
5. «Corrispondenze»: tabella persistente «font originale → font da usare», applicata da TXT-10 in tutti i documenti.

**Formati/strutture:** dizionari `/Font` di pagine, Form XObject, pattern e aspetti delle annotazioni (questi ultimi in sola lettura); `/FontDescriptor`, `/FontFile*`, `/ToUnicode`; tabella delle corrispondenze in un file di testo strutturato nella cartella di configurazione dell'utente.

**Casi limite:**
- Font usato anche da annotazioni o campi → elencato, ma la sostituzione agisce solo sul testo di pagina (indicato nel dialogo).
- Richiesta d'incorporamento con un font di sistema dalle metriche diverse → rifiutata perché cambierebbe l'aspetto, con proposta di sostituzione.
- Font di destinazione privo di alcuni caratteri → TXT-10 per quei caratteri.
- Centinaia di istanze dello stesso font (una per pagina) → raggruppate per nome.

**Criteri di accettazione:**
- Dato un PDF di prova con un font incorporato completo, uno come sottoinsieme e uno non incorporato, quando si apre il pannello, allora i tre stati e le pagine d'uso sono riportati correttamente.
- Dato un documento in cui il font A è usato su 10 pagine, quando lo si sostituisce con B in tutto il documento e si salva, allora nessun testo di pagina usa più A e il testo estratto di tutte le pagine è invariato.
- Dato un font non incorporato installato nel sistema con metriche identiche, quando si sceglie «Incorpora» e si salva, allora il font risulta incorporato e il rendering di tutte le pagine è identico a prima.
- Data una corrispondenza «Font X → Font Y» salvata, quando si modifica un altro documento che richiede la sostituzione di X, allora viene usato Y.

**Priorità:** P1

### TXT-12 — Trova e sostituisci

**Descrizione:** Cerca testo in tutto il documento e lo sostituisce una occorrenza alla volta o tutte insieme, conservando per ciascuna stile, posizione e allineamento del testo originale. Rif.: parità 1.8; standard cap. 40.

**Flusso:**
1. L'utente apre «Trova e sostituisci» (scorciatoia configurabile) e inserisce testo da cercare e testo sostitutivo.
2. Opzioni: maiuscole/minuscole, parola intera, ignora accenti e diacritici, espressione regolare (con riferimenti ai gruppi `$1`… nel sostitutivo); ambito: documento, pagina corrente, intervallo di pagine, selezione.
3. «Trova» produce l'elenco dei risultati con pagina e contesto, evidenziati sulla pagina; navigazione avanti/indietro.
4. «Sostituisci» agisce sull'occorrenza corrente e passa alla successiva; «Sostituisci tutto» chiede conferma indicando il numero di occorrenze.
5. Ogni sostituzione segue TXT-02 (stile del primo carattere dell'occorrenza), TXT-03 (riflusso del solo blocco interessato) e TXT-10 (copertura del font).
6. Riepilogo finale: occorrenze sostituite, non sostituibili con il motivo, sostituzioni che hanno richiesto un font sostitutivo.

**Formati/strutture:** ricerca sul testo estratto in ordine logico, normalizzato in forma Unicode NFC, con legature scomposte, trattini di sillabazione a fine riga ignorati e spazi consecutivi equivalenti; corrispondenze anche attraverso le interruzioni di riga dello stesso blocco; espressioni regolari con semantica Unicode; riscrittura come in TXT-02.

**Casi limite:**
- Occorrenza a cavallo di due blocchi o di due pagine → trovata ma non sostituibile automaticamente («attraversa blocchi»); modificabile a mano.
- Occorrenza in un blocco non modificabile → elencata come non sostituibile, con il motivo.
- Testo di annotazioni, campi modulo e segnalibri → escluso; il dialogo lo dichiara.
- Sostitutivo vuoto → equivale a TXT-13.
- Occorrenza con stili misti → si usa lo stile del primo carattere.
- Sostituzione che allunga un blocco di una riga → crescita secondo l'allineamento (TXT-03).
- Espressione regolare non valida → errore in linea e pulsanti disattivati.

**Criteri di accettazione:**
- Dato un PDF di 20 pagine con 37 occorrenze di «Fornitore», quando si esegue «Sostituisci tutto» con «Appaltatore» (maiuscole/minuscole attive) e si salva, allora il testo estratto contiene 37 occorrenze di «Appaltatore» e nessuna di «Fornitore».
- Dato lo stesso scenario, quando si esaminano gli attributi del testo, allora ogni «Appaltatore» ha nome base del font, dimensione effettiva (±0,05 pt) e colore dell'occorrenza sostituita.
- Dato il testo «data: 03/11/2025», quando si cerca l'espressione regolare `(\d{2})/(\d{2})/(\d{4})` e si sostituisce con `$3-$2-$1`, allora il testo estratto contiene «data: 2025-11-03».
- Dato un «Sostituisci tutto» di 37 occorrenze, quando si esegue un solo Annulla, allora tutte le 37 sostituzioni vengono annullate.
- Dato un PDF in cui «fine» è scritto con la legatura U+FB01, quando si cerca «fine», allora l'occorrenza viene trovata.
- Dato un documento di 300 pagine di testo, quando si cerca una parola, allora i primi risultati compaiono entro 1 s e l'elenco completo entro 5 s su hardware di riferimento.

**Priorità:** P0

### TXT-13 — Trova ed elimina

**Descrizione:** Variante di TXT-12 che rimuove dal testo di pagina le occorrenze trovate, gestendo gli spazi residui e la ricomposizione dei blocchi. Non sostituisce la Redazione: non agisce su immagini e grafica e, con il salvataggio incrementale, non elimina il testo dalle revisioni precedenti del file. Rif.: parità 1.9.

**Flusso:**
1. L'utente apre «Trova ed elimina» con le opzioni di ricerca e d'ambito di TXT-12.
2. L'elenco dei risultati ha una casella per includere o escludere ogni occorrenza.
3. Opzioni: «Rimuovi anche uno spazio adiacente» (attiva per default, evita doppi spazi); «Ricomponi il paragrafo» (attiva per default; se disattiva il testo restante non si sposta e resta un vuoto).
4. Conferma con il numero di occorrenze; esecuzione come unico passo annullabile; riepilogo come in TXT-12.
5. Il dialogo ricorda che i dati riservati vanno rimossi con la Redazione e il salvataggio completo.

**Formati/strutture:** come TXT-12; con «Ricomponi» disattiva, eliminazione dei codici da `Tj`/`TJ` compensata da uno spostamento in `TJ` pari alla larghezza rimossa; dopo un salvataggio completo nessuna revisione precedente resta nel file (ISO 32000-2 §7.5.6).

**Casi limite:**
- Occorrenza che costituisce l'intero blocco → blocco rimosso.
- Paragrafo rimasto vuoto → rimosso; i paragrafi successivi del blocco risalgono.
- Occorrenze in blocchi non modificabili → elencate come non eliminabili.
- Salvataggio incrementale dopo l'eliminazione → avviso che il testo resta recuperabile dalla revisione precedente, con proposta di salvataggio completo.

**Criteri di accettazione:**
- Dato un PDF con 12 occorrenze di «BOZZA», quando si eliminano tutte e si salva, allora il testo estratto non contiene «BOZZA» né doppi spazi nei punti di eliminazione.
- Dato «Ricomponi il paragrafo» disattivato, quando si elimina una parola a metà riga, allora le parole successive della riga mantengono la posizione (±0,5 pt).
- Data l'eliminazione seguita da salvataggio completo, quando si analizza il file, allora non contiene aggiornamenti incrementali e il testo estratto da tutti i flussi di contenuto presenti nel file non contiene «BOZZA».
- Data l'eliminazione di tutte le occorrenze, quando si esegue Annulla, allora tutte ricompaiono nella posizione originale.

**Priorità:** P1

### TXT-14 — Controllo ortografico

**Descrizione:** Durante la modifica le parole assenti dal dizionario della lingua del testo vengono sottolineate a schermo, con suggerimenti di correzione; un comando controlla l'ortografia dell'intero documento. Funziona offline con dizionari installabili. Rif.: concorrenti §20.

**Flusso:**
1. Il controllo è attivo per default nei blocchi in modifica; la lingua è, nell'ordine, quella del run o dell'elemento di struttura (`/Lang`), quella del documento, quella dell'interfaccia; l'utente può cambiarla per la selezione o per il blocco.
2. Le parole sconosciute sono sottolineate con una linea ondulata visibile solo in modifica; menu contestuale: suggerimenti, «Ignora», «Ignora tutto» (per la sessione), «Aggiungi al dizionario personale».
3. «Controlla ortografia del documento» scorre i blocchi modificabili (e, a scelta, il testo dei commenti) mostrando parola, contesto e suggerimenti; «Cambia» e «Cambia tutto» applicano la correzione come TXT-02/TXT-12.
4. Gestione dizionari: lingue installate, installazione e rimozione senza riavvio, dizionario personale modificabile, esportabile e importabile.

**Formati/strutture:** dizionari nel formato aperto affissi/dizionario (`.aff`/`.dic`), compresi quelli già installati nel sistema; dizionario personale in UTF-8, una parola per riga, nella cartella di configurazione dell'utente; lingua da `/Lang` del catalogo, degli elementi di struttura e delle sequenze marcate (ISO 32000-2 §14.9.2); se l'utente cambia la lingua di un blocco in un documento taggato, l'attributo `/Lang` dell'elemento corrispondente viene aggiornato.

**Casi limite:**
- Parola spezzata con trattino a fine riga → controllata ricomposta.
- Parole con cifre, tutte maiuscole, indirizzi web ed e-mail → ignorate per default (configurabile).
- Lingua senza dizionario → segnalazione nella barra di stato con accesso alla gestione dizionari; nessuna sottolineatura.
- Più lingue nello stesso blocco → si usa la lingua del run se marcata, altrimenti quella del blocco.
- Blocchi non modificabili → parole segnalate nel controllo del documento, ma correzione non applicabile.
- Testo OCR → il controllo aiuta a individuare errori di riconoscimento (TXT-16).

**Criteri di accettazione:**
- Dato un blocco in italiano con dizionario italiano installato, quando l'utente digita «lavorazzione», allora la parola è sottolineata entro 300 ms e «lavorazione» compare tra i primi 5 suggerimenti.
- Data una parola aggiunta al dizionario personale, quando la si digita in un altro documento dopo il riavvio dell'applicazione, allora non è sottolineata.
- Dato un documento con 5 errori in blocchi modificabili, quando si esegue «Controlla ortografia del documento» e si sceglie «Cambia» per ciascuno, allora il testo estratto dopo il salvataggio contiene le 5 parole corrette.
- Dato il controllo attivo con parole sottolineate, quando si salva, allora il rendering del file salvato è identico a quello ottenuto con il controllo disattivato.
- Dato un dizionario installato durante la sessione, quando si seleziona la sua lingua, allora il controllo funziona senza riavviare l'applicazione.
- Dato un sistema senza connessione di rete, quando si usa il controllo ortografico, allora funziona senza errori.

**Priorità:** P1

### TXT-15 — Modifica del testo riconosciuto con OCR

**Descrizione:** Dopo il riconoscimento ottico (sezione OCR) il testo è modificabile in due forme: correzione del livello di testo invisibile sovrapposto alla scansione, con l'immagine invariata, oppure conversione dell'area in testo reale modificabile, con rimozione del testo dall'immagine. Rif.: parità 7.1, 7.3; standard cap. 32; concorrenti §29.

**Flusso:**
1. In modalità Modifica, su una pagina con livello OCR invisibile, i blocchi riconosciuti sono mostrati con il testo in sovrimpressione semitrasparente (attivabile/disattivabile), allineato alle parole dell'immagine.
2. Correzione: l'utente modifica il testo come in TXT-02; l'immagine non cambia; ogni parola corretta viene riscalata orizzontalmente per coprire la parola corrispondente nell'immagine, così che selezione e ricerca restino allineate.
3. «Converti in testo modificabile» (blocco, selezione o pagina): l'editor stima font (il font di sistema più simile), dimensione, colore e allineamento, copre il testo nell'immagine ricostruendo lo sfondo (colore campionato o ricostruzione dell'area) e scrive testo visibile; da quel momento valgono tutte le funzioni TXT.
4. Se la pagina non ha OCR, la modalità Modifica propone di eseguirlo localmente, con la lingua del documento o dell'interfaccia preselezionata.
5. Rieseguire l'OCR su una pagina già riconosciuta richiede conferma, perché le correzioni manuali andrebbero perse.

**Formati/strutture:** testo in modalità di resa `Tr 3` sopra un'immagine XObject; adattamento della larghezza con `Tz` o spostamenti in `TJ`; font incorporato con `/ToUnicode`; conversione: nuova versione dell'immagine con l'area ricostruita (stessa compressione e risoluzione) o riempimento vettoriale sovrapposto, e testo visibile `Tr 0`; `/Lang` e struttura conservati per PDF/A di livello u e PDF/UA (ISO 32000-2 §8.9, §9.3.6; ISO 19005-2; ISO 14289-1).

**Casi limite:**
- Sfondo non uniforme (foto, retino, filigrana) → avviso che la ricostruzione può essere visibile; opzione di mantenere solo il livello invisibile.
- Scansione inclinata → blocchi con la rotazione dell'inclinazione; la conversione produce testo ruotato coerente, oppure si suggerisce prima il raddrizzamento.
- Scrittura a mano o testo non riconosciuto → resta immagine; nessun blocco.
- Stessa immagine usata da più pagine → la conversione crea una copia per la pagina corrente.
- Livello OCR di un altro programma senza `/ToUnicode` → blocco non decodificabile; proposta di rieseguire l'OCR.

**Criteri di accettazione:**
- Dato un PDF scansionato con livello OCR invisibile contenente «Rossl» al posto di «Rossi», quando l'utente corregge la parola e salva, allora il testo estratto contiene «Rossi» e non «Rossl» e il rendering della pagina è identico all'originale.
- Dato lo stesso scenario, quando si confrontano i riquadri estratti, allora il riquadro della parola corretta coincide con quello della parola originale con rapporto intersezione/unione ≥ 0,9.
- Dato un blocco convertito in testo modificabile, quando si sostituisce «2019» con «2026» e si salva, allora il testo estratto contiene «2026» e un OCR eseguito sul rendering dell'area restituisce «2026» e non «2019».
- Data una pagina scansionata senza testo, quando si entra in modalità Modifica e si accetta la proposta di OCR, allora al termine la pagina presenta blocchi modificabili senza dover riaprire il documento.
- Dato un livello OCR con correzioni manuali, quando si chiede di rieseguire l'OCR, allora viene chiesta conferma prima di sostituire il livello.

**Priorità:** P0

### TXT-16 — Revisione delle parole OCR incerte

**Descrizione:** Elenco guidato delle parole riconosciute con bassa confidenza, mostrate accanto al ritaglio dell'immagine, per correggerle o confermarle rapidamente. Rif.: concorrenti §29; standard cap. 43.

**Flusso:**
1. Al termine dell'OCR l'editor conserva per ogni parola la confidenza del riconoscimento.
2. «Rivedi parole incerte» apre un pannello con soglia regolabile (predefinita 80%) ed elenco per pagina; ogni voce mostra il ritaglio ingrandito dell'immagine, il testo riconosciuto modificabile e i suggerimenti del dizionario (TXT-14).
3. Azioni, anche da tastiera: «Accetta», «Correggi» (applicata come in TXT-15), «Accetta tutte nella pagina», «Salta».
4. Le voci confermate o corrette escono dall'elenco; lo stato di revisione viene salvato con il documento.

**Formati/strutture:** confidenze e stato di revisione in un dizionario di dati privati dell'applicazione a livello di pagina (`/PieceInfo` con `/LastModified`, ISO 32000-2 §14.5), ignorato dagli altri lettori e rimosso dalla sanificazione.

**Casi limite:**
- OCR eseguito da un altro programma → confidenze non disponibili; il pannello propone le parole assenti dal dizionario.
- Pagina modificata da un altro programma dopo l'OCR (`/LastModified` della pagina successivo a quello dei dati privati) → dati di revisione scartati come non affidabili.
- Voce relativa a un blocco poi convertito in testo modificabile → la correzione aggiorna il testo visibile.

**Criteri di accettazione:**
- Dato un documento appena riconosciuto con 15 parole sotto soglia, quando si apre il pannello, allora vengono elencate 15 voci, ciascuna con il ritaglio dell'immagine corrispondente.
- Dato il pannello con 15 voci, quando se ne correggono 5 e si accettano le altre 10, allora l'elenco è vuoto e il testo estratto contiene le 5 correzioni.
- Dato un documento salvato a metà revisione, quando lo si riapre, allora il pannello mostra solo le voci non ancora revisionate.

**Priorità:** P1

### TXT-17 — Annulla e ripeti

**Descrizione:** Tutte le operazioni della sezione sono annullabili e ripetibili tramite una cronologia per documento di almeno 100 passi (configurabile), che resta disponibile dopo il salvataggio e si azzera alla chiusura del documento. Rif.: requisito trasversale di parità 3.1–3.3 e 1.8–1.9.

**Flusso:**
1. Ctrl+Z annulla; Ctrl+Maiusc+Z e Ctrl+Y ripetono; le voci di menu descrivono l'operazione (es. «Annulla Sostituisci tutto (37)»).
2. Le digitazioni consecutive nello stesso blocco formano un solo passo finché non intervengono uno spostamento del cursore, una modifica di formato, un cambio di blocco o una pausa superiore a 1 s; ogni operazione composta (Sostituisci tutto, Trova ed elimina, sostituzione globale di font, conversione OCR, unione di blocchi, accettazione di una proposta IA) è un solo passo.
3. Annullare o ripetere porta la vista sulla pagina interessata e ripristina selezione e cursore.
4. Una nuova operazione dopo uno o più annullamenti elimina i passi ripetibili.
5. Annullare dopo il salvataggio riporta il documento allo stato «modificato».

**Formati/strutture:** cronologia nel modello in memoria del documento (stato dei blocchi, risorse aggiunte, struttura logica); al salvataggio le risorse rese inutili dagli annullamenti non vengono scritte (salvataggio completo) o non vengono referenziate (salvataggio incrementale).

**Casi limite:**
- Annullamento dell'operazione che aveva aggiunto un font → il font non compare nel file salvato.
- Limite di memoria della cronologia (configurabile) → i passi più vecchi vengono scartati; prima di un'operazione che non potrebbe essere annullata l'utente riceve un avviso.
- Operazioni di altre sezioni (pagine, annotazioni) → condividono la stessa cronologia, in ordine cronologico.
- Annulla durante una composizione del metodo di input → annulla solo la composizione in corso.

**Criteri di accettazione:**
- Dato un documento a cui sono state applicate 20 operazioni miste (digitazione, formattazione, spostamento, sostituzione, eliminazione di un blocco), quando si annulla 20 volte e si salva come nuovo file, allora testo estratto e rendering di tutte le pagine sono identici a quelli dell'originale.
- Dato lo stato dopo i 20 annullamenti, quando si ripete 20 volte, allora testo estratto e rendering coincidono con lo stato precedente agli annullamenti.
- Data la digitazione continua di «buongiorno a tutti» senza pause né spostamenti del cursore, quando si annulla una volta, allora l'intera frase viene rimossa.
- Dato un documento salvato dopo 3 modifiche, quando si annulla una volta, allora la terza modifica è annullata e il documento risulta modificato.
- Dati 101 passi con limite impostato a 100, quando si annulla ripetutamente, allora si annullano esattamente 100 passi.

**Priorità:** P0

### TXT-18 — Selezione, copia e incolla del testo, anche tra pagine

**Descrizione:** Selezione continua del testo attraverso righe, colonne e pagine; copia negli appunti come testo semplice e formattato; in modalità Modifica, taglia e incolla con o senza formattazione. Rif.: parità 3.5.

**Flusso:**
1. Con lo strumento di selezione del testo l'utente trascina dall'inizio alla fine, anche oltre il bordo della pagina in vista continua (scorrimento automatico); Maiusc+clic estende; doppio e triplo clic selezionano parola e riga; Ctrl+A seleziona il testo della pagina (in modifica, quello del blocco).
2. Ctrl+C copia in testo semplice (ordine di lettura; righe dello stesso paragrafo unite da uno spazio; trattini di sillabazione rimossi; a capo tra paragrafi e tra pagine) e in testo formattato (font, dimensione, colore, grassetto, corsivo); un'opzione conserva le interruzioni di riga originali.
3. In modifica: Ctrl+X taglia con riflusso; Ctrl+V incolla con formattazione se il contenuto proviene dall'editor; Ctrl+Maiusc+V incolla come testo semplice con lo stile del punto d'inserimento.
4. Su Linux la selezione aggiorna anche la selezione primaria; il clic centrale in un blocco in modifica incolla la selezione primaria.

**Formati/strutture:** appunti con `text/plain;charset=utf-8`, `text/html`, `text/rtf` e un formato interno con blocchi, run e font per incollare senza perdite tra documenti dell'editor; ordine di lettura dalla struttura logica se presente, altrimenti euristico; contenuti marcati `/Artifact` (intestazioni, piè di pagina) esclusi dalle selezioni che attraversano più pagine; `/ActualText` usato al posto dei glifi che sostituisce (ISO 32000-2 §9.10, §14.8.2.2, §14.9.4).

**Casi limite:**
- Permesso di copia negato → copia disattivata con messaggio; l'estrazione per le tecnologie assistive resta consentita.
- Testo non decodificabile → copia del testo disponibile, avviso e proposta di OCR.
- Selezione su due colonne → segue l'ordine di lettura (colonna sinistra fino in fondo, poi la destra).
- Selezione di centinaia di pagine → copia eseguita in background con avanzamento, senza bloccare l'interfaccia.
- Testo incollato con caratteri non coperti dal font → TXT-10.

**Criteri di accettazione:**
- Dato un documento taggato in cui un paragrafo inizia in fondo a pagina 3 e finisce in cima a pagina 4, quando lo si seleziona per intero e lo si copia, allora gli appunti contengono in `text/plain` l'intero paragrafo in ordine, senza il piè di pagina di pagina 3 né l'intestazione di pagina 4.
- Dato un paragrafo con «pro-»/«getto» a fine riga, quando lo si copia, allora il testo copiato contiene «progetto».
- Dato un documento con permesso di copia negato, quando si preme Ctrl+C su una selezione, allora gli appunti non cambiano e compare il messaggio.
- Dato un blocco copiato dal documento A e incollato in modifica nel documento B, quando si salva B, allora il testo incollato ha in B lo stesso nome base del font, la stessa dimensione effettiva e lo stesso colore che aveva in A.
- Dato un testo copiato da un'altra applicazione, quando lo si incolla con Ctrl+Maiusc+V, allora il testo inserito ha lo stile del punto d'inserimento.

**Priorità:** P0

### TXT-19 — Inserimento di testo formattato da trascinamento o dagli appunti

**Descrizione:** Il testo formattato trascinato da altre applicazioni o da file (RTF, HTML, testo semplice), o incollato dagli appunti, diventa un nuovo blocco di testo di pagina oppure viene inserito nel blocco in modifica, conservando la formattazione supportata. Rif.: parità 3.4; concorrenti §4.

**Flusso:**
1. L'utente trascina sulla pagina una selezione da un'altra applicazione o un file `.rtf`, `.txt`, `.html`; durante il trascinamento compare l'anteprima del riquadro.
2. Rilascio fuori da un blocco in modifica: nasce un blocco a larghezza fissa nel punto di rilascio, largo fino al bordo destro dell'area visibile o quanto la larghezza naturale del testo, se minore. Lo stesso avviene con Ctrl+V in modalità Modifica senza blocco attivo, nella posizione del puntatore (o al centro della vista).
3. Rilascio dentro un blocco in modifica: inserimento nel punto del cursore, con la scelta (predefinita nelle preferenze) tra «mantieni la formattazione di origine» e «usa lo stile di destinazione».
4. Vengono mappati font, dimensione, colore, grassetto, corsivo, sottolineato, barrato, apice/pedice, allineamento, interlinea, spazi tra paragrafi e rientri; il resto viene semplificato e riepilogato.
5. Font non installati o caratteri non coperti → TXT-10; il risultato è scritto come in TXT-06.

**Formati/strutture:** RTF (tabelle di font e colori, code page e sequenze Unicode `\uN`), HTML (stili in linea e marcatori di formattazione di base), testo semplice UTF-8 (per i file, rilevamento di UTF-16 e delle codifiche a 8 bit); trascinamento tra applicazioni nelle sessioni X11 e Wayland con i tipi `text/uri-list`, `text/rtf`, `text/html`, `text/plain`.

**Casi limite:**
- Tabelle → righe di testo con le celle separate da spazi; immagini e oggetti incorporati → omessi; il riepilogo li elenca.
- Testo più lungo dello spazio disponibile → il blocco trabocca con indicatore; l'utente può ridimensionarlo o annullare.
- Oltre 1 MB di testo → conferma prima dell'inserimento.
- Documento in sola lettura o con modifica non consentita → rilascio rifiutato con indicazione visiva.
- Codifica del file non determinabile → l'utente la sceglie con anteprima.

**Criteri di accettazione:**
- Dato un file RTF con una riga in grassetto 14 pt rossa e una in corsivo 11 pt, quando lo si trascina sulla pagina e si salva, allora il testo estratto contiene entrambe le righe, con dimensioni effettive 14 e 11 pt, colore rosso per la prima e, rispettivamente, variante grassetto e variante corsiva del font (o stile simulato dichiarato).
- Dato un RTF con «àèìòù € ß» espressi come sequenze Unicode, quando lo si inserisce, allora il testo estratto contiene esattamente quei caratteri.
- Dato un rilascio dentro un blocco in modifica con «usa lo stile di destinazione», quando si completa il rilascio, allora il testo inserito ha font, dimensione e colore del punto d'inserimento.
- Dato un RTF contenente un'immagine, quando lo si inserisce, allora il testo è inserito, l'immagine no e il riepilogo segnala un elemento omesso.

**Priorità:** P1

### TXT-20 — Salvaguardie d'integrità durante la modifica

**Descrizione:** Regole che proteggono il documento quando si modifica il testo: rispetto dei permessi, avvisi su firme e certificazioni, mantenimento della conformità PDF/A e PDF/UA dichiarata, coerenza della struttura logica e delle annotazioni ancorate al testo, gestione del contenuto condiviso tra pagine, conservazione dei dati non toccati. Rif.: standard cap. 1, 11, 35, 39, 43, 45, 50; sezioni Sicurezza e Firma.

**Flusso:**
1. All'ingresso in modalità Modifica l'editor verifica permesso di modifica, firme digitali e livello di certificazione, dichiarazione di conformità PDF/A o PDF/UA, presenza di moduli XFA dinamici.
2. Permesso negato → modalità non disponibile, con il motivo e la possibilità di inserire la password del proprietario.
3. Documento firmato → avviso che la modifica invaliderà le firme; se si prosegue, il primo salvataggio propone «Salva con nome» per lasciare intatto l'originale. Certificazione «nessuna modifica consentita» → serve un'ulteriore conferma esplicita.
4. Conformità PDF/A dichiarata → ogni font usato dalle modifiche viene incorporato con `/ToUnicode`; se un'operazione non può rispettare il profilo, l'utente sceglie tra annullarla e proseguire rimuovendo la dichiarazione di conformità (con avviso).
5. Testo in un Form XObject usato da più pagine → alla prima modifica l'utente sceglie «solo questa pagina» (copia privata dell'oggetto) o «tutte le pagine».
6. Annotazioni ancorate al testo modificato o spostato (evidenziazioni, sottolineature, barrati, collegamenti): se il testo esiste ancora, rettangoli e quadrilateri vengono ricalcolati; se è stato eliminato, l'annotazione resta al suo posto ed è segnalata come «orfana» nell'elenco commenti.
7. Al salvataggio vengono aggiornati struttura logica, `/ParentTree`, `/ActualText` del testo modificato e `/LastModified` della pagina; i dati privati di altre applicazioni (`/PieceInfo`) restano.

**Formati/strutture:** `/Encrypt` con `/P` (bit 4 modifica del contenuto, bit 5 copia); `/Perms` con `/DocMDP` e livello `/P` 1–3; campi `/Sig`; metadati XMP `pdfaid:part`/`pdfaid:conformance` e `pdfuaid:part`; `/StructTreeRoot`, `/ParentTree`, `/MCID`; Form XObject richiamati da più pagine; annotazioni `/Highlight`, `/Underline`, `/StrikeOut`, `/Squiggly`, `/Link` con `/QuadPoints` e `/Rect`; `/PieceInfo` (ISO 32000-2 §7.6.4, §12.5.6.5, §12.5.6.10, §12.8.2.2, §14.3.2, §14.5, §14.7; ISO 19005; ISO 14289).

**Casi limite:**
- Documento cifrato aperto con la password del proprietario → modifica consentita; il salvataggio mantiene la stessa cifratura.
- Modulo XFA dinamico → il contenuto è generato dal modulo; modifica disattivata con spiegazione.
- Pagina composta da un unico Form XObject usato solo da essa (es. pagina importata) → trattato come contenuto della pagina, senza domanda.
- Documento non taggato → nessuna struttura creata.
- Testo marcato come artefatto → resta artefatto dopo la modifica.

**Criteri di accettazione:**
- Dato un PDF cifrato con permesso di modifica negato, quando l'utente tenta di attivare la modalità Modifica senza la password del proprietario, allora la modalità non si attiva e viene mostrato il motivo.
- Dato un PDF firmato digitalmente, quando l'utente modifica una parola e accetta il salvataggio proposto, allora l'avviso è comparso prima della modifica, viene creato un nuovo file e l'originale resta identico byte per byte.
- Dato un PDF dichiarato PDF/A-2b che supera la validazione, quando si modifica il testo usando un font sostitutivo e si salva, allora il file salvato supera ancora la validazione PDF/A-2b con il validatore di riferimento del progetto.
- Dato un Form XObject con testo usato in 10 pagine, quando l'utente modifica il testo scegliendo «solo questa pagina», allora solo la pagina corrente mostra il nuovo testo e le altre 9 hanno testo estratto e rendering invariati.
- Dato un paragrafo con una parola evidenziata da un'annotazione, quando una modifica precedente nello stesso paragrafo sposta la parola, allora i quadrilateri dell'annotazione coprono la nuova posizione della parola (±0,5 pt).
- Dato un documento conforme a PDF/UA-1, quando si modifica il testo di un paragrafo e si salva, allora tutto il testo di pagina è ancora collegato a un elemento di struttura o marcato come artefatto.
- Dato un documento con `/PieceInfo` di un'altra applicazione, quando si modifica testo e si salva, allora il dizionario è ancora presente e invariato.

**Priorità:** P0

### TXT-21 — Scritture complesse: bidirezionali, con composizione dei glifi e verticali

**Descrizione:** Modifica di testo in scritture da destra a sinistra (arabo, ebraico), anche miste a testo latino, in scritture che richiedono la composizione dei glifi (devanagari, thai e simili) e in scrittura verticale CJK, con cursore, selezione e riflusso corretti. Rif.: standard cap. 30; concorrenti §66.

**Flusso:**
1. TXT-01 riconosce direzione e scrittura del blocco (ordine visivo dei glifi, `/ActualText`, modo di scrittura del font).
2. In modifica il testo è gestito in ordine logico; cursore e selezione seguono l'algoritmo bidirezionale; i glifi vengono composti secondo le regole del font (forme contestuali, legature obbligatorie, segni combinanti).
3. I paragrafi da destra a sinistra hanno allineamento predefinito a destra e rifluiscono nella propria direzione; i blocchi verticali rifluiscono per colonne, da destra a sinistra.

**Formati/strutture:** algoritmo bidirezionale Unicode (UAX #9); regole di composizione dalle tabelle del font (ISO/IEC 14496-22); nel PDF glifi in ordine visivo, con `/ActualText` o `/ToUnicode` che restituiscono il testo logico; font composti con CMap verticali (`Identity-V`, `/WMode 1`) e metriche verticali `/W2`, `/DW2` (ISO 32000-2 §9.7.4.3, §9.7.5, §14.9.4).

**Casi limite:**
- Sottoinsieme privo delle forme contestuali necessarie → sostituzione come in TXT-10.
- Numeri e parole latine in un paragrafo arabo → ordine visivo secondo l'algoritmo bidirezionale.
- Testo da destra a sinistra senza `/ActualText` → l'editor ricostruisce l'ordine logico; se ambiguo, il blocco è modificabile solo per sostituzione integrale.

**Criteri di accettazione:**
- Dato un paragrafo in ebraico che contiene il numero 2026, quando si inserisce una parola ebraica a metà e si salva, allora il testo estratto in ordine logico contiene la parola nella posizione attesa e «2026» non risulta invertito.
- Dato un paragrafo in arabo, quando si inserisce una lettera tra due lettere legate, allora il rendering coincide con quello di riferimento (forme contestuali corrette) e il testo estratto contiene la sequenza logica attesa.
- Dato un blocco giapponese verticale di 3 colonne, quando si aggiungono 10 caratteri alla prima colonna, allora il testo rifluisce nelle colonne successive da destra a sinistra senza superare l'altezza del riquadro.

**Priorità:** P2

### TXT-22 — Modifica del testo con istruzioni in linguaggio naturale (IA)

**Descrizione:** L'utente seleziona testo in un blocco e descrive a parole la modifica voluta; un servizio linguistico configurato propone il nuovo testo che, se accettato, sostituisce la selezione nel PDF con le regole di TXT-02. Rif.: parità 3.6.

**Flusso:**
1. Selezione del testo (o del blocco) e comando «Modifica con IA»; campo per l'istruzione.
2. Alla prima esecuzione l'utente sceglie il servizio (modello locale o servizio remoto configurato) e dà il consenso esplicito all'invio del testo selezionato.
3. La proposta viene mostrata con le differenze rispetto all'originale e con un indicatore d'ingombro rispetto al riquadro, calcolato localmente.
4. L'utente accetta, ritocca la proposta prima di accettarla, chiede una nuova proposta o annulla.
5. L'accettazione sostituisce la selezione come in TXT-02 (stile del primo carattere, riflusso, copertura dei font), in un unico passo annullabile.

**Formati/strutture:** al servizio vengono inviati solo testo selezionato, istruzione e lingua; nessun invio senza consenso; configurazione (indirizzo, modello, credenziali conservate nel portachiavi del sistema) nelle preferenze; risposta in testo semplice UTF-8.

**Casi limite:**
- Rete assente o servizio non configurato → comando disattivato con indicazione; documento invariato.
- Proposta più lunga del riquadro → crescita del blocco (TXT-03) con indicatore; azione rapida «Accorcia» (TXT-24).
- Errore o tempo scaduto del servizio → messaggio; nessuna modifica parziale.
- Selezione su più blocchi → non consentita; si suggerisce di unire i blocchi (TXT-09).
- Documento in sola lettura o con modifica non consentita → comando non disponibile.

**Criteri di accettazione:**
- Dato un servizio simulato che restituisce un testo prefissato, quando l'utente accetta la proposta e salva, allora il testo estratto contiene il testo restituito al posto della selezione e il resto del blocco è invariato.
- Dato un servizio simulato con richieste intercettate, quando l'utente esegue il comando, allora la richiesta contiene solo testo selezionato, istruzione e lingua.
- Dato un utente che non ha ancora dato il consenso, quando esegue il comando, allora nessuna richiesta di rete parte prima della conferma.
- Data una proposta accettata, quando si esegue Annulla, allora la selezione originale è ripristinata.

**Priorità:** P2

### TXT-23 — Correzione bozze con IA

**Descrizione:** Il servizio linguistico analizza selezione, blocco, pagina o documento e propone correzioni di grammatica, ortografia e punteggiatura come elenco di modifiche puntuali, accettabili o rifiutabili singolarmente o tutte insieme. Rif.: parità 3.7.

**Flusso:**
1. Comando «Correzione bozze» con scelta dell'ambito.
2. Servizio e consenso come in TXT-22; prima del consenso viene mostrato il volume di testo da inviare; per ambiti ampi l'invio avviene blocco per blocco, con avanzamento e possibilità d'interruzione.
3. Pannello delle proposte: originale, correzione, tipo (grammatica, ortografia, punteggiatura) e breve motivazione; evidenziazione sulla pagina.
4. «Accetta», «Rifiuta», «Accetta tutte» (anche per tipo); ogni accettazione si applica come in TXT-02.

**Formati/strutture:** come TXT-22; ogni proposta è riferita a blocco e posizione nel testo logico ed è invalidata se il blocco cambia dopo la proposta.

**Casi limite:**
- Proposta su testo a cavallo di due blocchi o in un blocco non modificabile → mostrata ma non applicabile.
- Proposta su una parola del dizionario personale (TXT-14) → scartata localmente.
- Servizio interrotto a metà elaborazione → le proposte già ricevute restano utilizzabili.

**Criteri di accettazione:**
- Dato un servizio simulato che propone 3 correzioni in un blocco, quando l'utente ne accetta 2, ne rifiuta 1 e salva, allora il testo estratto contiene esattamente le 2 correzioni accettate.
- Dato «Accetta tutte» su 10 proposte, quando si esegue Annulla, allora tutte le 10 correzioni vengono annullate.
- Data una proposta su una parola del dizionario personale, quando arrivano le proposte, allora quella proposta non viene mostrata.
- Dato un blocco modificato a mano dopo l'arrivo delle proposte, quando l'utente tenta di accettarne una su quel blocco, allora la proposta risulta invalidata e non viene applicata.

**Priorità:** P2

### TXT-24 — Espandi e condensa con IA

**Descrizione:** Allunga o accorcia il testo selezionato mantenendone il significato, con lunghezza obiettivo regolabile e l'opzione «Adatta al riquadro», che condensa finché il testo non rientra nello spazio originale del blocco. Rif.: parità 3.8.

**Flusso:**
1. Selezione e comando «Espandi» o «Condensa»; lunghezza obiettivo (percentuale o numero di parole) oppure «Adatta al riquadro».
2. Servizio e consenso come in TXT-22; anteprima con confronto e indicatore d'ingombro.
3. Con «Adatta al riquadro», se la proposta non rientra, la richiesta viene ripetuta fino a 3 volte; poi viene mostrata la proposta migliore con l'eccedenza.
4. Accettazione come in TXT-22.

**Formati/strutture:** come TXT-22; la verifica d'ingombro usa le metriche reali dei font del blocco e il riflusso di TXT-03.

**Casi limite:**
- «Condensa» su meno di 3 parole → comando disattivato.
- Numeri, date e importi cambiati o rimossi → evidenziati nel confronto.
- Proposta che non rientra dopo i tentativi → accettabile solo confermando il trabocco.

**Criteri di accettazione:**
- Dato un servizio simulato la cui prima risposta eccede il riquadro e la seconda rientra, quando si usa «Adatta al riquadro», allora viene proposta la seconda con l'indicazione «rientra» e, dopo l'accettazione, l'altezza del blocco non supera quella originale.
- Data una proposta che cambia l'importo «1.250 €», quando viene mostrato il confronto, allora l'importo è evidenziato come modificato.
- Data una proposta accettata, quando si salva, allora il testo estratto contiene il nuovo testo e l'origine del blocco non si è spostata (±0,5 pt).

**Priorità:** P2

### TXT-25 — Rifinisci e cambia tono con IA

**Descrizione:** Migliora stile e scorrevolezza del testo selezionato o ne cambia il registro (formale, informale, professionale, cordiale, conciso, persuasivo o tono descritto dall'utente), mantenendo lingua e contenuto informativo. Rif.: parità 3.9.

**Flusso:**
1. Selezione e comando «Rifinisci» oppure «Cambia tono», con scelta del tono.
2. Servizio e consenso come in TXT-22; anteprima con confronto e indicatore d'ingombro.
3. Accettazione come in TXT-22; possibilità di chiedere un'altra proposta con lo stesso tono.

**Formati/strutture:** come TXT-22; tono e lingua rilevata inviati come parametri della richiesta.

**Casi limite:**
- Lingua non supportata dal servizio → avviso prima dell'invio.
- Nomi propri, cifre, riferimenti normativi modificati → evidenziati nel confronto.
- Proposta in una lingua diversa da quella della selezione → avviso nel confronto.

**Criteri di accettazione:**
- Dato un servizio simulato con richieste intercettate, quando l'utente sceglie il tono «formale», allora la richiesta contiene il parametro di tono «formale» e la lingua rilevata della selezione.
- Data una proposta accettata, quando si salva, allora il testo estratto contiene la proposta e il rendering dei blocchi non selezionati è invariato.
- Data una proposta in cui un numero dell'originale è stato cambiato, quando viene mostrato il confronto, allora il numero è evidenziato come modifica sensibile.

**Priorità:** P2


## 4. Immagini, oggetti e collegamenti

### OBJ-01 — Modalità di modifica degli oggetti

**Descrizione:** Modalità in cui il contenuto della pagina viene scomposto in oggetti che si selezionano e si modificano direttamente nel contenuto, non come annotazioni: immagini, tracciati vettoriali, sfumature, oggetti composti (Form XObject) e blocchi di testo. In questa sezione i blocchi di testo partecipano a selezione, spostamento, allineamento, ordine di sovrapposizione, copia/incolla ed eliminazione. La loro modifica interna, il ridimensionamento e la rotazione sono specificati nella sezione sull'editing del testo. Tutte le funzioni OBJ che modificano il contenuto si basano su questa modalità.

**Flusso:** 1. L'utente attiva "Modifica" dalla barra strumenti o con la scorciatoia. 2. Le pagine visibili vengono analizzate per prime, le altre in background. 3. Al passaggio del mouse l'oggetto sotto il puntatore viene contornato, con un colore diverso per ogni tipo. 4. L'utente seleziona e modifica gli oggetti (OBJ-02 e seguenti). 5. Uscendo dalla modalità (Esc con selezione vuota, oppure scelta di un altro strumento) le modifiche restano nel documento aperto. Il file viene scritto solo al salvataggio.

**Formati/strutture:** content stream della pagina (`/Contents`, anche come array) e relative risorse (`/XObject`, `/ExtGState`, `/ColorSpace`, `/Pattern`, `/Shading`, `/Font`, `/Properties`). Immagine = `Do` su image XObject, oppure immagine inline `BI … ID … EI`. Tracciato = operatori di costruzione (`m l c v y h re`) + operatore di pittura. Sfumatura = `sh`. Oggetto composto = `Do` su Form XObject. Le trasformazioni si scrivono come matrici `cm` dentro coppie `q`/`Q`, conservando le sequenze marcate (`BDC`/`EMC`, `/MCID`, `/OC`) e lo stato grafico degli altri oggetti (ISO 32000-2 §7.8, §8.2–8.5, §8.9–8.10, §14.6). Convenzioni valide per tutta la sezione: posizioni e misure sono riferite all'angolo superiore sinistro del riquadro visibile (`/CropBox`) della pagina come appare a schermo, quindi tenendo conto di `/Rotate` e `/UserUnit`. Si esprimono nell'unità preferita (mm, cm, in, pt; 1 pt = 1/72 in). "Confronto pixel" = resa a 150 ppi con tolleranza di 1 livello per canale.

**Casi limite:**
- Documento con firme digitali → avviso che modificare il contenuto invaliderà le firme, con richiesta di conferma.
- Permesso di modifica negato (bit 4 di `/P`) → modalità disattivata finché non si inserisce la password proprietario.
- Documento che dichiara PDF/A o PDF/X → avviso che alcune modifiche possono violare il profilo (i dettagli sono nelle singole funzioni).
- Modulo XFA dinamico → modalità non disponibile, con spiegazione.
- Stream di una pagina danneggiato → solo quella pagina diventa non modificabile.
- Pagine con decine di migliaia di oggetti → analisi progressiva che non blocca l'interfaccia.
- Annotazioni, campi modulo e collegamenti → restano visibili ma non si selezionano in questa modalità.
- Sfondi e filigrane marcati come artefatti di paginazione → non selezionabili, salvo filtro esplicito (OBJ-02).

**Criteri di accettazione:**
- Dato un documento aperto, quando entro ed esco dalla modalità senza modifiche, allora il documento non risulta modificato e alla chiusura non viene chiesto di salvare.
- Dato una pagina con 3 immagini, 10 tracciati e 2 blocchi di testo, quando passo il mouse su ciascun oggetto, allora viene contornato esattamente quell'oggetto, con il colore del suo tipo.
- Dato un'immagine spostata e il documento salvato, quando confronto la pagina prima e dopo, allora tutte le aree esterne alla vecchia e alla nuova posizione dell'immagine sono identiche (confronto pixel) e l'albero di struttura ha lo stesso numero di elementi.
- Dato un documento con una firma digitale valida, quando attivo la modalità e rifiuto la conferma, allora non è possibile alcuna modifica e la firma resta valida.
- Dato una pagina con 20.000 tracciati, quando attivo la modalità, allora l'interfaccia non resta bloccata per più di 200 ms consecutivi e gli oggetti diventano selezionabili entro 3 s sulla macchina di riferimento del piano di test.

**Priorità:** P0

### OBJ-02 — Selezione di immagini e oggetti

**Descrizione:** Selezione singola e multipla degli oggetti di una pagina, con strumenti per raggiungere anche gli oggetti sovrapposti o nascosti.

**Flusso:** 1. Clic su un oggetto: viene selezionato l'oggetto più in alto nell'ordine di disegno e compare il riquadro con 8 maniglie e la maniglia di rotazione. 2. Maiusc+clic o Ctrl+clic aggiunge o toglie oggetti dalla selezione. 3. Trascinamento su un'area vuota: riquadro di selezione, che seleziona gli oggetti interamente contenuti (con Alt anche quelli solo intersecati). 4. Alt+clic nello stesso punto seleziona, a ogni clic, l'oggetto immediatamente sottostante, ricominciando dall'alto dopo l'ultimo. 5. Tab e Maiusc+Tab scorrono gli oggetti della pagina nell'ordine di disegno. 6. Ctrl+A seleziona tutti gli oggetti della pagina corrente che passano il filtro di tipo (immagini, tracciati, testo, composti, sfondi/filigrane; predefinito: tutti tranne sfondi/filigrane). 7. Esc o clic su un'area vuota annulla la selezione.

**Formati/strutture:** test del punto per tipo di oggetto:
- immagine: parallelogramma ottenuto applicando la matrice corrente al quadrato unitario, intersecato con il ritaglio attivo;
- tracciato: area riempita, oppure tratto con tolleranza pari al maggiore tra 3 px di schermo e metà dello spessore;
- oggetto composto: unione dei suoi oggetti visibili.

Gli oggetti di livelli nascosti (`/OC`) sono esclusi.

**Casi limite:**
- Selezione limitata a una pagina → un clic su un'altra pagina sostituisce la selezione.
- Oggetto con opacità 0 o interamente ritagliato → raggiungibile solo con riquadro, Tab o Alt+clic, e mostrato con contorno tratteggiato.
- Immagine usata come maschera morbida di un'altra, dentro un motivo (pattern) o in un glifo Type3 → non selezionabile separatamente.
- Immagine di scansione sotto un livello di testo invisibile (OCR) → selezionabile, con l'indicazione "testo OCR sovrapposto".
- Oggetto che copre l'intera pagina → gli oggetti sottostanti si raggiungono con Alt+clic.
- Oggetti di livelli bloccati → visibili ma non selezionabili, con un suggerimento che spiega il motivo.

**Criteri di accettazione:**
- Dato due immagini sovrapposte, quando clicco nell'area comune, allora viene selezionata quella disegnata per ultima; quando poi faccio Alt+clic nello stesso punto, allora viene selezionata l'altra.
- Dato tre immagini, di cui due interamente dentro un riquadro di selezione e una solo intersecata, quando rilascio il riquadro, allora sono selezionati esattamente due oggetti; ripetendo con Alt, allora sono selezionati tre oggetti.
- Dato il filtro "solo immagini" su una pagina con 4 immagini e 12 tracciati, quando premo Ctrl+A, allora sono selezionati 4 oggetti.
- Dato un oggetto selezionato a pagina 1, quando clicco un oggetto a pagina 2, allora la selezione contiene solo l'oggetto di pagina 2.

**Priorità:** P0

### OBJ-03 — Pannello proprietà e trasformazione numerica

**Descrizione:** Pannello che mostra le caratteristiche dell'oggetto selezionato e permette di impostarne posizione, dimensioni, rotazione e opacità con valori esatti.

**Flusso:** 1. Con un oggetto selezionato, il pannello mostra tipo, X, Y, larghezza e altezza (con lucchetto delle proporzioni), angolo e opacità. Il punto di riferimento si sceglie su una griglia di 9 punti (predefinito: angolo superiore sinistro). 2. Per le immagini il pannello aggiunge:
   - dimensioni in pixel e risoluzione effettiva orizzontale/verticale (ppi);
   - spazio colore (con il nome del profilo ICC, se presente), bit per componente e compressione;
   - trasparenza o maschera presente, peso dei dati e numero di occorrenze nel documento;
   - livello di appartenenza e testo alternativo, se il documento è taggato.
3. Per i tracciati aggiunge colori di tratto e riempimento e spessore (in sola lettura; la modifica è in OBJ-25). 4. L'utente modifica un campo e lo conferma con Invio o uscendo dal campo: l'oggetto si aggiorna. 5. Con più oggetti selezionati, i valori diversi compaiono come "misto"; un valore inserito si applica a tutti.

**Formati/strutture:** valori ricavati dalla matrice dell'oggetto e dal dizionario dell'immagine (`/Width`, `/Height`, `/ColorSpace`, `/BitsPerComponent`, `/Filter`, `/SMask`, `/Mask`, `/ImageMask`, `/OC`). Il testo alternativo è letto da `/Alt` dell'elemento di struttura `/Figure` collegato tramite `/MCID`. Risoluzione effettiva = pixel / dimensione disegnata in pollici.

**Casi limite:**
- Valore non numerico o fuori intervallo → campo segnato in rosso e ripristino del valore precedente.
- Larghezza o altezza ≤ 0 → rifiutata; il minimo è 1 pt.
- Oggetto inclinato → posizione e dimensioni riferite al riquadro non trasformato; angolo e inclinazione mostrati in campi separati.
- Scala non uniforme → due valori di ppi distinti.
- Testo alternativo in un documento non taggato → campo non disponibile, con rimando alla funzione di accessibilità.

**Criteri di accettazione:**
- Dato un'immagine di 600×300 px disegnata a 200×100 pt, quando la seleziono, allora il pannello mostra 216 ppi in entrambe le direzioni.
- Dato un'immagine di 100×50 pt con proporzioni bloccate e riferimento in alto a sinistra, quando imposto la larghezza a 200 pt, allora l'altezza diventa 100 pt e X e Y restano invariati.
- Dato un JPEG CMYK con profilo incorporato, quando lo seleziono, allora il pannello indica compressione JPEG e spazio colore ICC a 4 componenti con il nome del profilo.
- Dato due immagini con opacità 40 % e 70 %, quando le seleziono insieme, allora il campo opacità mostra "misto"; inserendo 50, entrambe passano al 50 %.

**Priorità:** P1

### OBJ-04 — Inserire immagini da file

**Descrizione:** Aggiunge al contenuto della pagina una o più immagini lette da file, mantenendo qualità, trasparenza e profilo colore. L'immagine viene disegnata sopra il contenuto esistente.

**Flusso:** 1. Strumento "Aggiungi immagine": si apre la finestra di scelta file, con selezione multipla, filtro per formato e anteprima. 2. Il cursore mostra l'anteprima dell'immagine. Un clic la posiziona a dimensione naturale, centrata nel punto cliccato; trascinando si disegna un rettangolo in cui l'immagine viene adattata mantenendo le proporzioni. 3. Se si scelgono più file, le immagini si dispongono a cascata dal punto scelto (scostamento di 20 pt). 4. Se il documento è taggato, l'app chiede, senza obbligo, il testo alternativo oppure la marcatura come "decorativa". 5. L'immagine inserita resta selezionata.

**Formati/strutture:** formati di ingresso:

| Formato | Varianti supportate |
|---|---|
| PNG | 1–16 bit, palette, grigi, alfa, `tRNS`, interlacciato |
| JPEG | base e progressivo; grigi, RGB, CMYK |
| TIFF | non compresso, LZW, Deflate, PackBits, JPEG, CCITT G3/G4; RGB, CMYK, grigi, B/N, alfa |
| BMP | 1/4/8/16/24/32 bit, RLE, alfa |
| GIF | palette, indice trasparente |
| WebP | con e senza perdita, alfa |

Ogni immagine diventa un image XObject:
- JPEG → copiato senza ricodifica (`/DCTDecode`), con `/Decode [1 0 1 0 1 0 1 0]` quando un JPEG CMYK memorizza valori invertiti. I segmenti di metadati (EXIF, XMP, commenti) vengono rimossi per riservatezza, salvo opzione contraria; si conservano i marcatori necessari (JFIF, APP14 per la trasformazione del colore, profilo ICC).
- CCITT G4 → copiato, quando possibile, come `/CCITTFaxDecode`.
- Altri formati → decodificati e compressi senza perdita (`/FlateDecode` con predittore).
- WebP con perdita → convertito in JPEG di qualità 90 (configurabile), oppure senza perdita se l'utente lo sceglie.
- Palette → `/Indexed`. Alfa → `/SMask` in scala di grigi. Indice trasparente → `/Mask` a intervallo di colori. Profilo ICC incorporato → `/ICCBased`.
- Orientamento EXIF → applicato con la matrice, senza ricodifica.

Dimensione naturale = pixel / densità dichiarata (pHYs, JFIF, TIFF, BMP) se compresa tra 50 e 2400 ppi, altrimenti 96 ppi. Un'immagine che supera il 90 % della larghezza o dell'altezza visibile viene ridotta in proporzione. Un'immagine identica già presente nel documento viene riusata (stesso XObject). Nei documenti taggati l'immagine è marcata come `/Figure` con `/Alt`, oppure come `/Artifact` se decorativa.

**Casi limite:**
- File corrotto, troncato o di dimensioni nulle → messaggio d'errore e documento invariato.
- Formato non supportato → messaggio con l'elenco dei formati accettati.
- TIFF multipagina → l'utente sceglie le pagine; ogni pagina diventa un'immagine, disposta a cascata.
- GIF o WebP animato → si inserisce il primo fotogramma, oppure quello scelto, con avviso.
- Immagine oltre 100 megapixel → avviso con proposta di ricampionamento.
- 16 bit per componente in un documento di versione < 1.5 → riduzione a 8 bit, oppure aggiornamento della versione, a scelta.
- PDF/A-1 → trasparenza appiattita su bianco, con avviso.
- PDF/A-2/3/4 → alle immagini senza profilo viene assegnato un profilo coerente con l'intento di output, per non violare il profilo.
- Pagina con `/Rotate 90` → l'immagine appare diritta nella vista.

**Criteri di accettazione:**
- Dato un PNG di 300×150 px con densità 150 ppi, quando lo inserisco con un clic su una pagina A4, allora l'immagine misura 144×72 pt ed è centrata nel punto cliccato (±0,5 pt).
- Dato un JPEG con dati EXIF, quando lo inserisco e salvo, allora lo stream ha filtro `/DCTDecode`, i dati compressi (dal marcatore SOS in poi) coincidono byte per byte con quelli del file sorgente e non contiene segmenti EXIF.
- Dato un PNG RGBA con pixel semitrasparenti, inserito su una pagina con sfondo colorato, quando lo visualizzo, allora l'XObject ha una `/SMask` e lo sfondo traspare come nell'immagine di riferimento (confronto pixel).
- Dato un JPEG con orientamento EXIF 6, quando lo inserisco, allora appare diritto e i dati compressi non vengono ricodificati.
- Dato un file .png corrotto, quando provo a inserirlo, allora compare un messaggio d'errore e il documento non risulta modificato.

**Priorità:** P0

### OBJ-05 — Inserire immagini dagli appunti

**Descrizione:** Incolla come nuova immagine il contenuto grafico presente negli appunti di sistema, per esempio copiato da un browser, da un editor grafico o da uno strumento di cattura.

**Flusso:** 1. In modalità modifica l'utente preme Ctrl+V, oppure sceglie "Incolla" dal menu contestuale in un punto della pagina. 2. Se gli appunti contengono oggetti copiati da Foliant si applica OBJ-30. Altrimenti, se contengono un'immagine, la si inserisce con le regole di OBJ-04: centrata nel punto del menu contestuale, oppure al centro della parte visibile della pagina. 3. L'immagine resta selezionata. 4. Il comando "Incolla come immagine" funziona anche fuori dalla modalità modifica, che in quel caso viene attivata.

**Formati/strutture:** tipi accettati, in ordine di preferenza: formato interno di Foliant, `image/png`, `image/tiff`, `image/webp`, `image/jpeg`, `image/bmp`, `image/gif`. Un `text/uri-list` con file immagine locali (`file://`) viene trattato come inserimento da file (OBJ-04). Funziona nelle sessioni X11 e Wayland. I dati non compressi vengono compressi senza perdita; un JPEG viene copiato senza ricodifica, come in OBJ-04.

**Casi limite:**
- Appunti vuoti o con solo testo → "Incolla come immagine" disattivato; il testo è gestito dalla funzione di inserimento del testo.
- HTML che rimanda a immagini remote → nessun download automatico; si usa solo l'eventuale immagine già presente negli appunti.
- Immagine senza densità → 96 ppi.
- Immagine molto grande → come in OBJ-04.
- Appunti modificati da un'altra applicazione durante l'operazione → si usa il contenuto letto all'avvio del comando.

**Criteri di accettazione:**
- Dato negli appunti un PNG di 400×200 px con alfa, quando premo Ctrl+V su una pagina, allora viene creata un'immagine di 400×200 px con `/SMask`, centrata nella parte visibile della pagina.
- Dato negli appunti un `text/uri-list` con due JPEG locali, quando incollo, allora vengono inserite due immagini a cascata.
- Dato negli appunti solo testo semplice, quando apro il menu contestuale in modalità modifica, allora "Incolla come immagine" è disattivato.

**Priorità:** P0

### OBJ-06 — Inserire immagini trascinandole

**Descrizione:** Inserisce immagini trascinandole dal file manager o da un'altra applicazione nel punto voluto della pagina.

**Flusso:** 1. L'utente trascina uno o più file immagine, o un'immagine da un'altra applicazione, sopra la vista del documento. 2. Durante il trascinamento la pagina di destinazione è evidenziata e compare l'anteprima del riquadro. 3. Al rilascio si attiva la modalità modifica. Le immagini sono inserite con le regole di OBJ-04: la prima centrata nel punto di rilascio, le altre a cascata. 4. Tutte le immagini di un singolo rilascio formano un'unica operazione annullabile.

**Formati/strutture:** `text/uri-list` per i file locali e dati immagine diretti negli stessi tipi di OBJ-05. Formati come in OBJ-04 e OBJ-07.

**Casi limite:**
- Rilascio tra due pagine → si usa la pagina più vicina.
- Rilascio parzialmente fuori pagina → l'immagine viene traslata per rientrare nel riquadro visibile.
- File PDF trascinato → non viene inserito come oggetto; si propone l'inserimento come pagine (funzione della gestione pagine).
- File misti → si inseriscono solo le immagini, con un riepilogo dei file scartati.
- Solo URL remoto (da browser) → nessun download senza conferma esplicita.
- Documento in sola lettura → rilascio rifiutato, con cursore di divieto.

**Criteri di accettazione:**
- Dato un PNG trascinato sulla pagina 3 in un punto P, quando lo rilascio, allora la pagina 3 contiene una nuova immagine centrata in P (±1 pt).
- Dato 3 file immagine trascinati insieme, quando li rilascio e premo una volta Ctrl+Z, allora tutte e 3 le immagini vengono rimosse.
- Dato un file .docx e un file .png trascinati insieme, quando li rilascio, allora viene inserita solo l'immagine e un avviso indica il file scartato.

**Priorità:** P0

### OBJ-07 — Formati immagine aggiuntivi

**Descrizione:** Estende l'inserimento da file, appunti e trascinamento a formati meno comuni, compresi la grafica vettoriale e i formati raw delle fotocamere.

**Flusso:** 1. Come in OBJ-04. Nella finestra di scelta, il filtro "Tutte le immagini" include anche questi formati. 2. Per i formati che richiedono una scelta (fotogramma, sviluppo raw, rasterizzazione) compare una finestra di opzioni prima del posizionamento.

**Formati/strutture:**
- JPEG 2000 (.jp2, .j2k, .jpx) → copiato senza ricodifica come `/JPXDecode` (PDF ≥ 1.5).
- HEIC/HEIF e AVIF → decodificati e inseriti come JPEG o senza perdita, a scelta.
- TGA, PNM (PBM/PGM/PPM) e ICO (alla risoluzione maggiore) → compressione senza perdita; il PBM bianco/nero → CCITT G4.
- Raw di fotocamera, incluso DNG → sviluppo con parametri predefiniti, oppure uso dell'anteprima JPEG incorporata, a scelta.
- SVG → convertito in tracciati vettoriali dentro un oggetto composto (Form XObject); il testo diventa contorni oppure testo con font incorporato.

**Casi limite:**
- SVG con script, riferimenti esterni, filtri o animazioni → quegli elementi vengono ignorati o rasterizzati e un avviso li elenca.
- SVG senza dimensioni → dimensioni ricavate dal `viewBox`, altrimenti 300×150 px.
- JPEG 2000 in un documento PDF/A-1 o di versione < 1.5 → ricodifica senza perdita, con avviso.
- Raw con profilo della fotocamera → conversione in sRGB.
- HEIC con più immagini → scelta delle immagini come per un TIFF multipagina.

**Criteri di accettazione:**
- Dato un file .jp2, quando lo inserisco in un documento PDF 1.7, allora lo stream ha filtro `/JPXDecode` e i dati coincidono con il codestream del file.
- Dato un SVG con un rettangolo e un cerchio, quando lo inserisco, allora non viene creato alcun image XObject e, scomponendo l'oggetto (OBJ-26), si ottengono 2 tracciati.
- Dato un file HEIC, quando lo inserisco, allora l'immagine ha le stesse dimensioni in pixel dell'originale.

**Priorità:** P2

### OBJ-08 — Spostare oggetti

**Descrizione:** Sposta uno o più oggetti selezionati all'interno della pagina con il mouse, la tastiera o valori numerici.

**Flusso:** 1. Trascinamento della selezione, con anteprima in tempo reale e guide (OBJ-23). 2. Maiusc durante il trascinamento vincola il movimento all'orizzontale, alla verticale o a 45°. 3. Le frecce spostano di 1 pt; Maiusc+freccia sposta di 10 pt. 4. In alternativa si usano i campi X e Y del pannello (OBJ-03). 5. Al rilascio si registra un unico passo annullabile.

**Formati/strutture:** la traslazione si compone con la matrice dell'oggetto (`cm` dentro `q`/`Q`). Un ritaglio che riguarda solo l'oggetto si sposta con esso. Se il ritaglio è condiviso con altri oggetti, l'oggetto spostato ne riceve una copia traslata e gli altri restano invariati. Le sequenze marcate (`/MCID`, `/OC`) vengono conservate.

**Casi limite:**
- Oggetto che finirebbe interamente fuori dal riquadro visibile → lo spostamento si ferma in modo che restino visibili almeno 10 pt dell'oggetto (tutto l'oggetto, se più piccolo).
- Rilascio su un'altra pagina in vista continua → si applica OBJ-31.
- Immagine di scansione con testo OCR sovrapposto → l'app chiede se spostare anche il testo invisibile (predefinito: sì).
- Collegamenti con rettangolo coincidente (±1 pt) con il riquadro dell'oggetto → si spostano insieme all'oggetto; il comportamento si può disattivare.
- Pagina ruotata → lo spostamento segue la direzione della vista.
- Esc durante il trascinamento → nessuna modifica.

**Criteri di accettazione:**
- Dato un'immagine selezionata con X = 50 pt, quando premo 3 volte freccia destra e una volta Maiusc+freccia destra, allora X = 63 pt.
- Dato una pagina con `/Rotate 90` e un'immagine, quando la trascino di 100 pt verso destra nella vista, allora nella resa l'immagine risulta spostata di 100 pt verso destra (±0,5 pt).
- Dato due immagini sotto lo stesso tracciato di ritaglio, quando ne sposto una, allora la resa dell'altra è identica a prima (confronto pixel) e quella spostata mostra la stessa porzione visibile, traslata.
- Dato un trascinamento in corso, quando premo Esc, allora l'oggetto torna alla posizione iniziale e la cronologia non registra passi.

**Priorità:** P0

### OBJ-09 — Ridimensionare con o senza proporzioni

**Descrizione:** Cambia le dimensioni degli oggetti selezionati con le maniglie o con valori numerici, mantenendo oppure no il rapporto tra larghezza e altezza.

**Flusso:** 1. Maniglia d'angolo: ridimensionamento proporzionale, con l'angolo opposto fisso. 2. Maiusc + maniglia d'angolo: ridimensionamento libero. 3. Maniglie laterali: cambia una sola dimensione. 4. Alt durante il trascinamento: ridimensionamento rispetto al centro. 5. In alternativa si usano i campi larghezza e altezza del pannello, con il lucchetto delle proporzioni (OBJ-03). 6. Con più oggetti selezionati si ridimensiona il riquadro comune e ogni oggetto scala in proporzione.

**Formati/strutture:** la scala si applica alla matrice dell'oggetto nel suo sistema locale, quindi le maniglie seguono l'eventuale rotazione. I pixel delle immagini non vengono ricampionati. Nei tracciati lo spessore del tratto scala insieme all'oggetto, com'è proprio del formato; il pannello lo segnala.

**Casi limite:**
- Trascinamento oltre il lato opposto → il ridimensionamento si ferma alla dimensione minima di 1 pt, senza capovolgere l'oggetto.
- Risoluzione effettiva sotto 72 ppi (soglia configurabile) → avviso non bloccante.
- Oggetto ruotato → maniglie e proporzioni sono riferite all'oggetto, non al riquadro allineato agli assi.
- Ridimensionamento non uniforme di una selezione multipla che contiene oggetti ruotati → produce un'inclinazione, mostrata fedelmente in anteprima.
- Immagine ritagliata → il ritaglio scala insieme all'immagine.

**Criteri di accettazione:**
- Dato un'immagine di 200×100 pt, quando trascino una maniglia d'angolo fino a 300 pt di larghezza, allora l'altezza diventa 150 pt e l'angolo opposto resta fermo.
- Dato la stessa immagine, quando trascino una maniglia d'angolo tenendo premuto Maiusc fino a 300×120 pt, allora l'immagine misura 300×120 pt.
- Dato un'immagine ridimensionata e salvata, quando la estraggo con i dati originali (OBJ-27), allora dimensioni in pixel e dati sono invariati.
- Dato un'immagine ruotata di 30°, quando la ridimensiono in proporzione, allora l'angolo resta 30°.

**Priorità:** P0

### OBJ-10 — Ruotare oggetti

**Descrizione:** Ruota gli oggetti selezionati di 90° o di un angolo qualsiasi.

**Flusso:** 1. Pulsanti "Ruota di 90° in senso antiorario" e "Ruota di 90° in senso orario", nella barra contestuale e nel menu contestuale. 2. Maniglia di rotazione: rotazione libera con anteprima e indicazione dell'angolo; Maiusc vincola a multipli di 15°. 3. Campo angolo nel pannello: ammette decimali; un valore positivo ruota in senso antiorario. 4. La rotazione avviene attorno al centro del riquadro della selezione.

**Formati/strutture:** la rotazione si compone con la matrice dell'oggetto, senza ricampionare i pixel. L'angolo viene mostrato nell'intervallo (-180°, 180°].

**Casi limite:**
- Quattro rotazioni di 90° → matrice identica all'iniziale, senza errori di arrotondamento accumulati.
- Oggetto che finisce in parte fuori pagina → consentito.
- Selezione multipla → ruota come gruppo attorno al centro comune.
- Pagina con `/Rotate` → l'angolo è relativo alla vista.
- Immagine ritagliata → il ritaglio ruota con l'immagine.

**Criteri di accettazione:**
- Dato un'immagine di 200×100 pt, quando applico "Ruota di 90° in senso orario", allora il suo riquadro diventa 100×200 pt con lo stesso centro e la resa coincide con il riferimento ruotato (confronto pixel).
- Dato un oggetto, quando applico 4 volte "Ruota di 90° in senso orario", allora la sua matrice è uguale a quella iniziale.
- Dato un oggetto, quando trascino la maniglia di rotazione tenendo premuto Maiusc, allora l'angolo risultante è un multiplo di 15°.
- Dato un oggetto, quando scrivo 12,5 nel campo angolo, allora l'oggetto risulta ruotato di 12,5° in senso antiorario.

**Priorità:** P0

### OBJ-11 — Capovolgere oggetti

**Descrizione:** Specchia gli oggetti selezionati in orizzontale o in verticale.

**Flusso:** 1. Comandi "Capovolgi orizzontalmente" e "Capovolgi verticalmente", nella barra contestuale e nel menu contestuale. 2. Lo specchio avviene rispetto all'asse che passa per il centro della selezione.

**Formati/strutture:** una scala -1 su un asse si compone con la matrice dell'oggetto, senza modificare i pixel.

**Casi limite:**
- Doppio capovolgimento → matrice identica all'iniziale.
- Oggetto ruotato → l'asse di specchio è quello orizzontale o verticale dello schermo.
- Blocchi di testo nella selezione → esclusi, con avviso (il testo specchiato non resterebbe modificabile).
- Selezione multipla → si inverte anche la posizione relativa degli oggetti.

**Criteri di accettazione:**
- Dato un'immagine asimmetrica, quando applico "Capovolgi orizzontalmente", allora la resa coincide con il riferimento specchiato (confronto pixel) e il riquadro resta nella stessa posizione.
- Dato un oggetto, quando applico due volte lo stesso capovolgimento, allora la matrice è uguale a quella iniziale.
- Dato due immagini affiancate A|B, quando le capovolgo insieme orizzontalmente, allora l'ordine diventa B|A e ciascuna è specchiata.

**Priorità:** P1

### OBJ-12 — Inclinare e reimpostare la trasformazione

**Descrizione:** Applica un'inclinazione orizzontale o verticale, imposta trasformazioni numeriche combinate e riporta un oggetto al suo orientamento di base.

**Flusso:** 1. Finestra "Trasforma": scala in %, rotazione, inclinazione orizzontale e verticale (da -80° a 80°) e spostamento. Le trasformazioni si applicano all'insieme della selezione oppure a ogni oggetto attorno al proprio centro. 2. Ctrl + trascinamento di una maniglia laterale: inclinazione interattiva. 3. "Reimposta trasformazione": toglie rotazione, inclinazione e capovolgimento mantenendo il centro. Per le immagini ripristina anche le proporzioni native, a parità di area.

**Formati/strutture:** composizione di matrici affini (`cm`), senza ricampionamento.

**Casi limite:**
- Inclinazione fuori intervallo o matrice degenere (determinante 0) → rifiutata.
- Oggetto già inclinato nel file originale → "Reimposta" lo riporta a una matrice ortogonale e lo segnala.
- Immagine ritagliata → il ritaglio si trasforma insieme all'immagine.

**Criteri di accettazione:**
- Dato un quadrato di 100×100 pt, quando applico un'inclinazione orizzontale di 20°, allora la resa coincide con il parallelogramma atteso (confronto pixel) e l'altezza resta 100 pt.
- Dato un'immagine ruotata di 37° e capovolta, quando scelgo "Reimposta trasformazione", allora la sua matrice non contiene rotazione né scala negativa e il centro è invariato.

**Priorità:** P2

### OBJ-13 — Ritagliare immagini

**Descrizione:** Nasconde una parte di un'immagine (ritaglio non distruttivo) oppure ne elimina definitivamente i pixel esterni (ritaglio applicato).

**Flusso:** 1. Con un doppio clic sull'immagine o con il comando "Ritaglia" compaiono le maniglie di ritaglio; l'area esclusa appare attenuata. 2. L'utente sposta le maniglie oppure sposta l'immagine sotto il riquadro. Maiusc mantiene le proporzioni; si può scegliere un rapporto predefinito: libero, originale, 1:1, 4:3, 16:9. 3. Invio conferma, Esc annulla. 4. "Ripristina ritaglio" toglie il ritaglio non distruttivo. 5. Il comando facoltativo "Applica ritaglio definitivamente" elimina dal file i pixel esterni.

**Formati/strutture:**
- Ritaglio non distruttivo → tracciato rettangolare di ritaglio (`re W n`) nel sistema dell'immagine, dentro la sua coppia `q`/`Q`.
- Ritaglio definitivo → nuovo image XObject che contiene solo righe e colonne visibili. Le immagini senza perdita mantengono la stessa compressione. Un JPEG si ritaglia senza ricodifica se i bordi cadono sui blocchi da 8/16 px, altrimenti si ricodifica a qualità 90 con avviso. Le maschere (`/SMask`, `/Mask`) sono ritagliate allo stesso modo.

**Casi limite:**
- Immagine ruotata o capovolta → le maniglie restano allineate ai bordi dell'immagine.
- Area ritagliata inferiore a 1 px → rifiutata.
- Immagine condivisa da più occorrenze → il ritaglio definitivo crea una nuova immagine solo per l'occorrenza corrente, salvo scelta "tutte le occorrenze".
- Immagine già ritagliata nel file da un tracciato non rettangolare → quel ritaglio è mostrato come esistente e non modificabile; lo si può sostituire con un ritaglio rettangolare dopo conferma.
- "Ripristina" dopo un ritaglio definitivo → non disponibile; si usa Annulla.

**Criteri di accettazione:**
- Dato un'immagine di 400×400 px disegnata a 200×200 pt, quando la ritaglio alla metà sinistra e confermo, allora il riquadro visibile misura 100×200 pt e si vede solo la metà sinistra (confronto pixel).
- Dato l'immagine ritagliata in modo non distruttivo, quando scelgo "Ripristina ritaglio", allora la resa torna identica all'originale.
- Dato l'immagine ritagliata, quando applico il ritaglio definitivo ed eseguo un salvataggio completo, allora l'image XObject misura 200×400 px e il file non contiene più i dati dell'immagine da 400×400 px.

**Priorità:** P0

### OBJ-14 — Sostituire immagini

**Descrizione:** Sostituisce un'immagine esistente con un'altra, presa da file o dagli appunti, mantenendo posizione, trasformazioni e proprietà.

**Flusso:** 1. Comando "Sostituisci immagine" (menu contestuale o pannello) con scelta del file, oppure "Sostituisci con il contenuto degli appunti". 2. L'utente sceglie come adattare l'immagine:
   - "Adatta" (predefinita): la nuova immagine entra nel riquadro esistente con le proprie proporzioni, centrata;
   - "Riempi": l'immagine copre il riquadro e la parte eccedente viene ritagliata;
   - "Deforma": l'immagine occupa esattamente il riquadro.
3. Se l'immagine compare più volte nel documento, l'utente sceglie "solo questa occorrenza" oppure "tutte le N occorrenze". 4. Anteprima e conferma.

**Formati/strutture:** formati di ingresso come in OBJ-04 e OBJ-07. Con "solo questa" si crea un nuovo image XObject e si aggiorna solo il relativo `Do` (o l'immagine inline). Con "tutte" si sostituisce lo stream dell'XObject condiviso. Si conservano rotazione, capovolgimento, opacità, ritaglio, livello (`/OC`), sequenza marcata e `/MCID`. Il testo alternativo resta invariato e l'app propone di aggiornarlo.

**Casi limite:**
- Nuova immagine con trasparenza in PDF/A-1 → come in OBJ-04.
- Originale usato come maschera stencil (`/ImageMask`) → la sostituzione con un'immagine a colori è consentita, con avviso.
- Immagine dentro un Form XObject usato da più pagine → "solo questa" duplica il Form XObject per la pagina corrente.
- File non valido → nessuna modifica.

**Criteri di accettazione:**
- Dato un'immagine di 200×100 pt ruotata di 30°, quando la sostituisco con un'immagine quadrata in modalità "Adatta", allora la nuova immagine misura 100×100 pt, ha lo stesso centro ed è ruotata di 30°.
- Dato un logo presente su 10 pagine come unico XObject, quando lo sostituisco con "tutte le occorrenze", allora le 10 pagine mostrano la nuova immagine e il file contiene un solo XObject per essa.
- Dato lo stesso logo, quando lo sostituisco a pagina 4 con "solo questa occorrenza", allora cambia solo pagina 4 e le altre 9 sono identiche a prima (confronto pixel).
- Dato un'immagine con opacità 60 %, quando la sostituisco, allora anche la nuova ha opacità 60 %.

**Priorità:** P0

### OBJ-15 — Eliminare oggetti

**Descrizione:** Rimuove dal contenuto della pagina gli oggetti selezionati.

**Flusso:** 1. Con uno o più oggetti selezionati, l'utente preme Canc o Backspace, oppure sceglie "Elimina". 2. Gli oggetti scompaiono e l'operazione forma un unico passo annullabile.

**Formati/strutture:** si tolgono gli operatori di disegno dell'oggetto e le impostazioni di stato grafico che servivano solo a esso, mantenendo bilanciati `q`/`Q` e `BDC`/`EMC`. La voce di risorsa viene rimossa se nessun altro oggetto della pagina la usa. Gli XObject non più referenziati vengono eliminati al salvataggio completo. Un elemento di struttura rimasto senza contenuto viene tolto dall'albero dei tag.

**Casi limite:**
- Oggetto composto condiviso con altre pagine → eliminato solo dalla pagina corrente.
- Scansione con testo OCR sovrapposto → l'app chiede se eliminare anche il testo invisibile.
- Salvataggio incrementale → avviso che i dati eliminati restano recuperabili nelle revisioni precedenti del file; per rimuoverli con certezza servono il salvataggio completo o la redazione.
- Eliminazione di tutti gli oggetti di una pagina → la pagina resta, vuota.

**Criteri di accettazione:**
- Dato una pagina con 3 immagini, quando ne elimino una ed eseguo un salvataggio completo, allora il file contiene 2 image XObject e lo stream dell'immagine eliminata non è più presente (ricerca della sua impronta).
- Dato un documento taggato in cui l'immagine eliminata era l'unico contenuto di un elemento `/Figure`, quando salvo, allora quell'elemento non compare più nell'albero di struttura e un validatore PDF/UA non segnala nuovi errori.
- Dato un logo condiviso da 5 pagine, quando lo elimino a pagina 2, allora le altre 4 pagine continuano a mostrarlo.

**Priorità:** P0

### OBJ-16 — Opacità

**Descrizione:** Regola la trasparenza uniforme degli oggetti selezionati.

**Flusso:** 1. Cursore e campo numerico "Opacità" (0–100 %, a passi di 1) nel pannello e nella barra contestuale. 2. L'anteprima si aggiorna durante il trascinamento del cursore; al rilascio si registra un unico passo annullabile.

**Formati/strutture:** stato grafico esteso (`/ExtGState`) con `/ca` (riempimenti e immagini) e `/CA` (tratti), applicato all'oggetto dentro `q`/`Q`. Il nuovo valore sostituisce quello precedente dell'oggetto. L'eventuale alfa propria dell'immagine (`/SMask`) si combina con l'opacità. Gli stati grafici con lo stesso valore vengono riusati.

**Casi limite:**
- Opacità 0 → oggetto invisibile ma selezionabile (OBJ-02).
- PDF/A-1, PDF/X-1a o PDF/X-3 → avviso di violazione del profilo prima dell'applicazione, con possibilità di annullare.
- Oggetti sovrapposti con opacità propria → nelle zone di sovrapposizione l'effetto si somma, come mostrato in anteprima.
- Blocchi di testo → l'opacità si applica a riempimento e contorno dei glifi.

**Criteri di accettazione:**
- Dato un'immagine nera su pagina bianca, quando imposto l'opacità al 50 %, allora i pixel risultano di valore 127–128 (±1) e il file contiene un `ExtGState` con `/ca 0.5` applicato all'immagine.
- Dato un documento che dichiara PDF/A-1b, quando imposto l'opacità all'80 %, allora l'avviso di non conformità compare prima dell'applicazione.
- Dato un PNG con zone trasparenti, quando imposto l'opacità al 50 %, allora le zone trasparenti restano tali e quelle opache diventano semitrasparenti.

**Priorità:** P1

### OBJ-17 — Regolazioni dell'immagine

**Descrizione:** Correzioni di base sui pixel dell'immagine selezionata: luminosità, contrasto, saturazione, conversione in scala di grigi e inversione dei colori.

**Flusso:** 1. Comando "Regola immagine": pannello con cursori da -100 a +100 (luminosità, contrasto, saturazione) e pulsanti "Scala di grigi" e "Inverti", con anteprima in tempo reale sulla pagina. 2. "Applica" registra un unico passo annullabile; "Reimposta" azzera i cursori. 3. Se l'immagine è condivisa, l'utente sceglie "solo questa occorrenza" oppure "tutte".

**Formati/strutture:** i pixel vengono ricalcolati e ricompressi:
- immagini senza perdita → `/FlateDecode`;
- JPEG → ricodifica con qualità configurabile (predefinita 90);
- immagini `/Indexed` → si modifica solo la palette, senza perdita;
- scala di grigi → `/DeviceGray`, oppure `/ICCBased` a 1 componente.

Le maschere alfa restano invariate.

**Casi limite:**
- Maschera stencil (`/ImageMask`) → disponibile solo "Inverti".
- Spazi CMYK, Lab, Separation o DeviceN → regolazione nello spazio nativo quando possibile, altrimenti comando disattivato con spiegazione.
- Immagini a 16 bit → elaborazione a 16 bit.
- Immagini molto grandi → anteprima su una copia ridotta; l'applicazione a piena risoluzione mostra l'avanzamento e si può interrompere.

**Criteri di accettazione:**
- Dato un'immagine RGB, quando applico "Scala di grigi", allora l'XObject risultante ha 1 componente di colore e le stesse dimensioni in pixel.
- Dato un'immagine a palette, quando applico "Inverti", allora cambia solo la palette e i dati degli indici restano identici.
- Dato regolazioni applicate, quando premo Ctrl+Z, allora la resa torna identica all'originale (confronto pixel).

**Priorità:** P2

### OBJ-18 — Ricompressione e ricampionamento delle immagini selezionate

**Descrizione:** Riduce il peso delle immagini selezionate cambiandone compressione e/o risoluzione, con una stima del risultato prima di applicare.

**Flusso:** 1. Comando "Ottimizza immagine" su una o più immagini selezionate. 2. Opzioni:
   - risoluzione massima in ppi effettivi (es. 150);
   - compressione: JPEG con qualità, senza perdita, JPEG 2000 (se il profilo la ammette) o CCITT G4 per il bianco e nero;
   - conversione in scala di grigi.
3. L'anteprima ingrandita mostra il peso prima e dopo. 4. "Applica" registra un passo annullabile.

**Formati/strutture:** il ricampionamento si basa sulla risoluzione effettiva dell'occorrenza; per un'immagine condivisa si usa l'occorrenza con la risoluzione più alta. Filtri: `/DCTDecode`, `/FlateDecode`, `/JPXDecode`, `/CCITTFaxDecode`. Le maschere vengono ricampionate allo stesso modo.

**Casi limite:**
- Immagine già sotto la soglia → non viene ricampionata, solo ricompressa se richiesto.
- Risultato più pesante dell'originale → si mantiene l'originale, con avviso.
- PDF/A-1 o versione < 1.5 → l'opzione JPEG 2000 non compare.

**Criteri di accettazione:**
- Dato un'immagine di 3000×3000 px disegnata a 5×5 in (600 ppi), quando la ottimizzo a 150 ppi, allora l'XObject misura 750×750 px e le dimensioni disegnate non cambiano.
- Dato un'immagine il cui risultato sarebbe più pesante, quando applico l'ottimizzazione, allora i suoi dati restano identici e compare l'avviso.

**Priorità:** P2

### OBJ-19 — Modifica in un'applicazione esterna

**Descrizione:** Apre l'immagine selezionata nell'editor grafico scelto dall'utente e la reimporta automaticamente quando viene salvata.

**Flusso:** 1. Comando "Modifica con…": si usa l'applicazione predefinita del sistema per le immagini, oppure quella configurata nelle preferenze. 2. L'immagine viene esportata senza perdita in una cartella temporanea privata: PNG, oppure TIFF per CMYK e 16 bit. 3. A ogni salvataggio del file temporaneo l'immagine del documento si aggiorna con un passo annullabile, secondo le regole di OBJ-14: se le proporzioni non cambiano resta lo stesso riquadro, altrimenti si usa la modalità "Adatta". 4. La sessione termina chiudendo il documento o con il comando "Termina modifica esterna".

**Formati/strutture:** PNG o TIFF con profilo ICC incorporato. L'app rileva le modifiche al file temporaneo. I file temporanei sono leggibili solo dall'utente e vengono cancellati a fine sessione.

**Casi limite:**
- Applicazione non trovata → messaggio e proposta di sceglierne un'altra.
- File temporaneo eliminato o rinominato dall'editor → sessione interrotta, con avviso.
- Chiusura del documento con sessione aperta → l'app chiede se attendere o abbandonare le modifiche esterne.
- Salvataggi ravvicinati → si importa solo l'ultima versione completa del file.

**Criteri di accettazione:**
- Dato un'immagine aperta con "Modifica con…", quando il file temporaneo viene sostituito da una versione con un pixel modificato, allora entro 2 s la pagina mostra la nuova versione e Ctrl+Z ripristina la precedente.
- Dato la sessione terminata, quando controllo la cartella temporanea, allora il file esportato non esiste più.

**Priorità:** P2

### OBJ-20 — Ordine di sovrapposizione

**Descrizione:** Cambia l'ordine di disegno degli oggetti, per portarli sopra o sotto gli altri.

**Flusso:** 1. Comandi nel menu contestuale e nella barra:

| Comando | Scorciatoia |
|---|---|
| Porta in primo piano | Ctrl+Maiusc+] |
| Porta avanti | Ctrl+] |
| Porta indietro | Ctrl+[ |
| Porta in fondo | Ctrl+Maiusc+[ |

2. "Porta avanti" e "Porta indietro" scavalcano il primo oggetto che si sovrappone visivamente alla selezione nella direzione richiesta, così che l'effetto sia sempre visibile. Se nessun oggetto si sovrappone, l'ordine non cambia e un messaggio lo segnala.

**Formati/strutture:** i blocchi di disegno vengono riordinati nel content stream insieme al loro stato grafico isolato (`q`/`Q`), al ritaglio e alle sequenze marcate. L'ordine di lettura dell'albero di struttura non cambia, perché l'ordine di disegno è indipendente da quello logico.

**Casi limite:**
- Annotazioni, campi e collegamenti → sono sempre disegnati sopra il contenuto e nessun oggetto può superarli; un messaggio lo spiega.
- Oggetti dentro un Form XObject → si riordina l'oggetto composto nel suo insieme.
- Selezione multipla → si conserva l'ordine relativo degli oggetti selezionati.
- `/Contents` composto da più stream → si rispetta l'ordine complessivo.
- Oggetto portato sotto uno sfondo opaco → consentito; l'anteprima mostra che non sarà più visibile.

**Criteri di accettazione:**
- Dato tre immagini sovrapposte A (sotto), B e C (sopra), quando seleziono A e scelgo "Porta in primo piano", allora l'ordine di disegno diventa B, C, A e nell'area comune si vede A.
- Dato A, B, C in quest'ordine, dove solo C si sovrappone ad A, quando seleziono A e scelgo "Porta avanti", allora A viene disegnata dopo C.
- Dato un documento taggato, quando cambio l'ordine di sovrapposizione, allora l'ordine di lettura ricavato dalla struttura è invariato.

**Priorità:** P1

### OBJ-21 — Allineare oggetti

**Descrizione:** Allinea gli oggetti selezionati tra loro o rispetto alla pagina.

**Flusso:** 1. Con almeno un oggetto selezionato sono disponibili sei comandi: allinea a sinistra, al centro in orizzontale, a destra, in alto, al centro in verticale, in basso. 2. Il riferimento si può scegliere:
   - "Selezione": il riquadro comune; predefinito con 2 o più oggetti;
   - "Oggetto chiave": il primo selezionato, che resta fermo;
   - "Pagina": il riquadro visibile; predefinito con un solo oggetto.
3. Ogni comando forma un passo annullabile.

**Formati/strutture:** si usano i riquadri di delimitazione visivi, allineati agli assi della vista, comprensivi dello spessore del tratto e limitati dal ritaglio. Le traslazioni seguono OBJ-08.

**Casi limite:**
- Oggetti ruotati → si usa il loro riquadro visivo.
- Oggetti già allineati → nessuna modifica registrata.
- Blocchi di testo → inclusi.
- Oggetti su pagine diverse → caso impossibile, perché la selezione è limitata a una pagina (OBJ-02).

**Criteri di accettazione:**
- Dato tre oggetti con bordo sinistro a 50, 80 e 120 pt, quando applico "Allinea a sinistra" con riferimento "Selezione", allora tutti hanno il bordo sinistro a 50 pt e le coordinate verticali sono invariate.
- Dato un solo oggetto, quando applico "Centra in orizzontale" con riferimento "Pagina", allora il suo centro coincide con il centro orizzontale del riquadro visibile (±0,1 pt).
- Dato un oggetto chiave selezionato per primo, quando applico "Allinea in basso", allora l'oggetto chiave non si muove e gli altri hanno il bordo inferiore allineato al suo.

**Priorità:** P1

### OBJ-22 — Distribuire oggetti

**Descrizione:** Distanzia in modo uniforme tre o più oggetti, in orizzontale o in verticale.

**Flusso:** 1. Con almeno 3 oggetti selezionati: "Distribuisci in orizzontale" oppure "Distribuisci in verticale". 2. Metodo: "spaziatura uguale" (spazi uguali tra i bordi; predefinito) oppure "centri equidistanti". 3. I due oggetti estremi restano fermi; gli altri si spostano mantenendo il loro ordine di posizione.

**Formati/strutture:** riquadri visivi come in OBJ-21; traslazioni come in OBJ-08.

**Casi limite:**
- Meno di 3 oggetti → comandi disattivati.
- Somma delle larghezze maggiore dello spazio disponibile → la spaziatura diventa negativa (oggetti sovrapposti); è consentita e mostrata in anteprima.
- Oggetti nella stessa posizione → l'ordine di selezione fa da criterio secondario.

**Criteri di accettazione:**
- Dato tre oggetti larghi 20 pt con bordo sinistro a 0, 30 e 100 pt, quando applico "Distribuisci in orizzontale" con spaziatura uguale, allora il secondo ha il bordo sinistro a 50 pt (spazi di 30 pt).
- Dato quattro oggetti, quando applico "centri equidistanti", allora le distanze tra centri consecutivi differiscono al massimo di 0,1 pt.

**Priorità:** P1

### OBJ-23 — Guide intelligenti, griglia e aggancio

**Descrizione:** Durante spostamenti e ridimensionamenti mostra guide temporanee di allineamento. Aggancia gli oggetti a bordi e centri di altri oggetti, della pagina, della griglia e delle guide dell'utente.

**Flusso:** 1. Mentre l'utente trascina un oggetto, quando un suo bordo o il suo centro arriva entro 5 px di schermo da un bordo o da un centro di riferimento compare una guida e l'oggetto si aggancia. Sono riferimenti gli altri oggetti, i bordi e il centro della pagina. 2. Vengono indicate anche le spaziature uguali tra oggetti vicini e la distanza dal bordo della pagina. 3. Tenendo premuto Alt l'aggancio si sospende. 4. Il menu Visualizza permette di attivare o disattivare:
   - le guide intelligenti;
   - la griglia, con passo e suddivisioni configurabili;
   - i righelli e le guide dell'utente, trascinate dai righelli;
   - l'aggancio a griglia e guide.

**Formati/strutture:** sono preferenze dell'applicazione. Griglia e guide non vengono scritte nel PDF; le guide dell'utente sono memorizzate per documento nelle impostazioni locali.

**Casi limite:**
- Soglia espressa in pixel di schermo → indipendente dallo zoom.
- Pagine con migliaia di oggetti → si considerano solo gli oggetti vicini e visibili, mantenendo il trascinamento fluido (almeno 30 fotogrammi al secondo).
- Oggetti ruotati → si usano i riquadri visivi.
- Più riferimenti entro la soglia → vince il più vicino.

**Criteri di accettazione:**
- Dato un oggetto A con bordo sinistro a 100 pt e un oggetto B trascinato con il bordo sinistro a 3 px di schermo da quello di A, quando rilascio, allora il bordo sinistro di B è a 100 pt.
- Dato lo stesso trascinamento con Alt premuto, quando rilascio, allora B resta dove l'ho lasciato.
- Dato la griglia attiva con passo di 10 mm e l'aggancio attivo, quando sposto un oggetto, allora il suo angolo superiore sinistro finisce su un'intersezione della griglia.

**Priorità:** P1

### OBJ-24 — Tracciati vettoriali esistenti: selezione, spostamento ed eliminazione

**Descrizione:** Rende selezionabili e manipolabili i disegni vettoriali già presenti nella pagina (linee, forme, grafici, loghi, disegni tecnici), come singoli tracciati o come gruppi.

**Flusso:** 1. In modalità modifica, un clic su un'area dipinta seleziona il tracciato corrispondente (OBJ-02). 2. Per i disegni composti da molti tracciati si usano il riquadro di selezione e Maiusc+clic. "Seleziona simili" aggiunge i tracciati della pagina con lo stesso colore e lo stesso spessore. 3. Sul tracciato si possono usare: spostamento (OBJ-08), ridimensionamento (OBJ-09), rotazione (OBJ-10), capovolgimento (OBJ-11), copia (OBJ-30) ed eliminazione (OBJ-15).

**Formati/strutture:** ogni operatore di pittura (`S`, `s`, `f`, `F`, `f*`, `B`, `B*`, `b`, `b*`), con la sua costruzione, forma un oggetto. I tracciati di solo ritaglio (`W n`) non sono oggetti, ma vincolano quelli successivi. Sono oggetti anche i riempimenti a motivo e le sfumature (`sh`). Lo stato grafico (colore, spessore, tratteggio, giunzioni, `ExtGState`) viene conservato e isolato in `q`/`Q` quando l'oggetto cambia.

**Casi limite:**
- Tratto di spessore 0 (linea sottilissima) → area di clic minima di 3 px.
- Tracciato a tutta pagina (sfondo) → gli oggetti sottostanti si raggiungono con Alt+clic.
- Disegni di migliaia di segmenti (CAD) → la selezione con riquadro resta reattiva.
- Testo convertito in contorni → trattato come tracciati.
- Tracciati dentro un Form XObject → si seleziona l'oggetto composto; per scomporlo si usa OBJ-26.
- Ridimensionamento → lo spessore scala con l'oggetto (OBJ-09).

**Criteri di accettazione:**
- Dato un grafico vettoriale di 40 tracciati, quando lo racchiudo in un riquadro di selezione e lo sposto di 50 pt, allora i 40 tracciati risultano traslati di 50 pt e il resto della pagina è invariato (confronto pixel).
- Dato un tracciato selezionato, quando premo Canc e salvo, allora il suo operatore di pittura non compare più nel content stream e gli altri tracciati appaiono come prima.
- Dato un rettangolo rosso con tratto di 2 pt e altri 3 tracciati uguali nella pagina, quando uso "Seleziona simili", allora la selezione contiene 4 tracciati.

**Priorità:** P1

### OBJ-25 — Stile dei tracciati vettoriali

**Descrizione:** Modifica l'aspetto dei tracciati selezionati: colore di tratto e di riempimento, spessore, tratteggio, estremità e giunzioni.

**Flusso:** 1. Con uno o più tracciati selezionati, i campi di stile del pannello proprietà diventano modificabili. 2. Il selettore colore accetta valori RGB, CMYK, grigio ed esadecimali, oltre alle opzioni "nessun riempimento" e "nessun tratto". 3. Ogni modifica confermata forma un passo annullabile.

**Formati/strutture:** operatori di colore (`RG`/`rg`, `K`/`k`, `G`/`g`, `CS`/`cs` con `SC`/`sc`/`SCN`/`scn`) e operatori `w`, `d`, `J`, `j`, `M`. Il nuovo colore resta nella famiglia di spazio colore dell'originale (un CMYK resta CMYK), salvo scelta diversa. In un documento PDF/A senza intento di output adeguato si usa `/ICCBased`.

**Casi limite:**
- Riempimento a motivo o a sfumatura → colore non modificabile ("riempimento complesso"); lo si può sostituire con un colore piatto dopo conferma.
- Tinta piatta (`/Separation`) → si mostra il nome della tinta; si modifica solo la percentuale, salvo conversione esplicita.
- "Nessun riempimento" e "nessun tratto" insieme → l'oggetto diventerebbe invisibile, quindi serve una conferma.

**Criteri di accettazione:**
- Dato un rettangolo con riempimento CMYK, quando imposto il riempimento a 0/100/100/0, allora il content stream usa un operatore CMYK con quei valori e il rettangolo appare rosso.
- Dato un tracciato con tratto di 1 pt, quando imposto 3 pt e tratteggio 4-2, allora la resa coincide con il riferimento (confronto pixel).

**Priorità:** P2

### OBJ-26 — Oggetti composti: scomporre e raggruppare

**Descrizione:** Gestisce gli oggetti riutilizzabili (Form XObject), come loghi, timbri appiattiti o grafica importata. Permette di scomporli nei singoli oggetti e di raggruppare più oggetti in un'unica unità.

**Flusso:** 1. "Scomponi" su un oggetto composto: i suoi oggetti passano nel contenuto della pagina con la stessa resa e diventano selezionabili uno per uno. 2. "Raggruppa" su 2 o più oggetti: diventano un oggetto composto che si seleziona e si trasforma come unità; "Separa" annulla il raggruppamento. 3. Doppio clic su un oggetto composto: modalità isolamento, in cui si modificano gli oggetti interni senza scomporlo, scegliendo "solo qui" oppure "tutte le occorrenze".

**Formati/strutture:** Form XObject (`/Subtype /Form`, `/BBox`, `/Matrix`, `/Resources`, `/Group`). La scomposizione applica agli oggetti interni la matrice dell'occorrenza e la `/Matrix`, li ritaglia alla `/BBox` e unisce le risorse, rinominandole in caso di conflitto.

**Casi limite:**
- Gruppo di trasparenza (`/Group /S /Transparency`, isolato o knockout) → la scomposizione cambierebbe la resa, quindi compare un avviso e serve una conferma esplicita.
- Oggetti composti annidati → si scompone un livello alla volta.
- Oggetti con tag di struttura → raggruppamento disattivato, con spiegazione.
- Form XObject usato da più pagine → "Scomponi" agisce solo sulla pagina corrente.
- Reference XObject (contenuto esterno) → mostrato come oggetto composto non modificabile.

**Criteri di accettazione:**
- Dato un logo come Form XObject con 5 tracciati, quando lo scompongo, allora la pagina contiene 5 tracciati selezionabili e la resa è identica (confronto pixel).
- Dato 3 oggetti raggruppati, quando li sposto con un trascinamento, allora si spostano tutti dello stesso vettore; dopo "Separa" tornano selezionabili uno per uno.
- Dato un gruppo di trasparenza knockout, quando scelgo "Scomponi", allora l'avviso compare prima di qualsiasi modifica.

**Priorità:** P2

### OBJ-27 — Estrarre o salvare l'immagine selezionata

**Descrizione:** Salva su file l'immagine selezionata, o la copia negli appunti, nella qualità originale.

**Flusso:** 1. Menu contestuale: "Salva immagine come…" oppure "Copia immagine". 2. Nella finestra di salvataggio si sceglie il formato: "Originale" (predefinito, quando è possibile salvare senza conversione), PNG, JPEG con qualità, TIFF o WebP. Si sceglie anche il contenuto: "dati originali" (i pixel nativi) oppure "come appare", con ritaglio, rotazione, capovolgimento e opacità applicati alla risoluzione nativa. 3. Il nome proposto è `<nomefile>_p<pagina>_img<n>.<ext>`; prima di sovrascrivere un file si chiede conferma.

**Formati/strutture:** conversioni del formato "Originale":
- `/DCTDecode` → .jpg con i dati compressi invariati;
- `/JPXDecode` → .jp2;
- `/CCITTFaxDecode` → .tif G4;
- `/JBIG2Decode` → PNG a 1 bit;
- Flate, LZW o RunLength → PNG (RGB, grigi, palette, 16 bit) oppure TIFF (CMYK, Lab).

Altre regole:
- `/SMask` → PNG con canale alfa; se si sceglie JPEG, l'alfa viene appiattita su bianco, con avviso.
- `/Decode` e `/ImageMask` vengono applicati; una maschera stencil diventa un PNG trasparente con il colore di riempimento.
- Il profilo `/ICCBased` viene incorporato nel file.
- Separation e DeviceN vengono convertiti tramite lo spazio alternativo, con avviso.
- Nei metadati si scrive la risoluzione effettiva dell'occorrenza.

"Copia immagine" mette negli appunti un `image/png`.

**Casi limite:**
- Permesso di estrazione negato (bit 5 di `/P`) → comandi disattivati, salvo password proprietario.
- Immagine inline → estratta come le altre.
- JPEG CMYK con `/Decode` invertito i cui marcatori interni non garantiscono la lettura corretta → esportato in TIFF.
- Cartella non scrivibile → errore, con proposta di un'altra cartella.

**Criteri di accettazione:**
- Dato un'immagine `/DCTDecode`, quando la salvo nel formato "Originale", allora il file .jpg ha dati compressi identici byte per byte allo stream del PDF.
- Dato un'immagine con `/SMask`, quando la salvo in PNG, allora il canale alfa del PNG coincide con i valori della maschera.
- Dato un'immagine ritagliata e ruotata, quando la salvo "come appare", allora il file contiene solo l'area visibile ruotata e coincide con la resa di quell'area (confronto pixel alla risoluzione nativa).
- Dato un documento con permesso di copia negato, quando apro il menu contestuale di un'immagine, allora "Salva immagine come…" e "Copia immagine" sono disattivati.

**Priorità:** P0

### OBJ-28 — Estrarre tutte le immagini del documento

**Descrizione:** Esporta in blocco in una cartella tutte le immagini del documento o di un intervallo di pagine.

**Flusso:** 1. Comando "Estrai tutte le immagini". 2. Opzioni:
   - intervallo di pagine;
   - formato: Originale, PNG, JPEG, TIFF o WebP;
   - dimensione minima (es. ignora le immagini sotto 32×32 px);
   - includi le immagini inline (predefinito: sì);
   - includi le immagini dentro oggetti composti (predefinito: sì) e negli aspetti delle annotazioni (predefinito: no);
   - elimina i duplicati (predefinito: sì);
   - cartella di destinazione e schema dei nomi.
3. Una barra mostra l'avanzamento e permette di interrompere. 4. Il riepilogo finale indica immagini estratte, saltate (con motivo) e duplicate.

**Formati/strutture:** conversioni come in OBJ-27. I duplicati si riconoscono come stesso oggetto o stesso contenuto (impronta dei dati decodificati). Nome predefinito: `<nomefile>_p<pagina>_img<n>.<ext>`; per i duplicati vale la prima occorrenza.

**Casi limite:**
- Nessuna immagine → messaggio e nessuna cartella creata.
- File omonimi → suffisso numerico, mai sovrascrittura silenziosa.
- Interruzione → i file già scritti restano e il riepilogo lo indica.
- Disco pieno → interruzione con messaggio.
- Maschere (`/SMask`) → non esportate come file separati, salvo opzione.

**Criteri di accettazione:**
- Dato un PDF con 5 image XObject distinti, uno dei quali usato su 3 pagine, quando estraggo con "elimina i duplicati", allora vengono creati 5 file; senza l'opzione, 7 file.
- Dato la soglia minima di 32×32 px e un'immagine di 16×16 px, quando estraggo, allora quell'immagine non viene esportata ed è elencata tra le saltate.
- Dato un'estrazione interrotta dopo 10 immagini su 50, quando apro il riepilogo, allora indica 10 immagini estratte e 40 non elaborate.

**Priorità:** P1

### OBJ-29 — Catturare un'area come immagine o testo

**Descrizione:** Strumento che trasforma un'area rettangolare della vista in un'immagine (negli appunti, su file o nel documento) oppure ne estrae il testo.

**Flusso:** 1. Strumento "Cattura", disponibile anche in sola lettura. 2. L'utente traccia un rettangolo sulla vista; in vista continua può comprendere più pagine. 3. La barra contestuale offre: "Copia immagine" (predefinito), "Salva come…" (PNG/JPEG), "Inserisci come immagine" (in una pagina scelta, con le regole di OBJ-04) e "Copia testo". 4. Risoluzione di cattura: quella dello schermo, oppure 150, 300 o 600 ppi.

**Formati/strutture:** la resa comprende le annotazioni visibili. Negli appunti finiscono `image/png` e `text/plain`. Il testo viene estratto dal livello di testo dell'area, in ordine di lettura.

**Casi limite:**
- Area senza livello di testo (scansione) → "Copia testo" propone il riconoscimento del testo (sezione OCR).
- Permesso di estrazione negato → copia e salvataggio disattivati, come in OBJ-27.
- Area più piccola di 2×2 px → ignorata.
- Immagine risultante oltre 100 megapixel → risoluzione ridotta, con avviso.

**Criteri di accettazione:**
- Dato un rettangolo di 2×1 in catturato a 300 ppi, quando scelgo "Copia immagine", allora gli appunti contengono un PNG di 600×300 px (±1 px).
- Dato un'area che contiene la frase "Totale fattura", quando scelgo "Copia testo", allora gli appunti contengono esattamente quella frase.
- Dato una cattura a cavallo di due pagine, quando la salvo, allora l'immagine contiene le porzioni di entrambe le pagine come appaiono a schermo.

**Priorità:** P1

### OBJ-30 — Copiare, tagliare, incollare e duplicare oggetti

**Descrizione:** Copia o taglia gli oggetti selezionati e li incolla nella stessa pagina, in altre pagine o in altri documenti aperti, portando con sé tutte le risorse necessarie.

**Flusso:** 1. Ctrl+C o Ctrl+X sulla selezione. 2. Ctrl+V nella pagina corrente, anche di un altro documento: gli oggetti mantengono le coordinate dell'originale rispetto all'angolo superiore sinistro della pagina. Se si incolla nella stessa posizione della pagina d'origine, ogni incolla successivo si sposta di (+10 pt, +10 pt). 3. "Incolla qui" (menu contestuale): selezione centrata nel punto cliccato. 4. "Incolla in posizione" (Ctrl+Maiusc+V): stesse coordinate, senza scostamento. 5. Ctrl+D duplica con scostamento (+10 pt, +10 pt) senza passare dagli appunti; Ctrl + trascinamento duplica nel punto di rilascio. 6. Gli oggetti incollati restano selezionati.

**Formati/strutture:** gli appunti contengono quattro formati:
- formato interno: frammento di contenuto con tutte le risorse (immagini, font, spazi colore, stati grafici, motivi, sfumature, oggetti composti);
- `application/pdf`: una pagina con la sola selezione, per le altre applicazioni;
- `image/png` della selezione a 150 ppi;
- `text/plain` con il testo contenuto.

All'incolla le risorse vengono copiate nel documento di destinazione: quelle in conflitto vengono rinominate, quelle identiche già presenti vengono riusate.

**Casi limite:**
- Pagina di destinazione più piccola → posizione traslata per rientrare nel riquadro visibile.
- Permesso di copia negato nel documento d'origine → copia disattivata.
- Destinazione PDF/A → avviso sugli elementi non conformi (es. trasparenza in PDF/A-1; colori senza profilo, con proposta di conversione).
- Documento d'origine chiuso dopo la copia → l'incolla funziona comunque, perché gli appunti sono autosufficienti.
- Destinazione taggata → gli oggetti incollati ricevono nuovi elementi di struttura (es. `/Figure` con il testo alternativo copiato); in una destinazione non taggata non si aggiungono marcature di struttura.
- Contenuto di un livello (`/OC`) → incollato nel livello con lo stesso nome se esiste, altrimenti fuori da ogni livello, con avviso.
- Taglia → copia ed elimina in un unico passo annullabile.

**Criteri di accettazione:**
- Dato un'immagine copiata dalla pagina 1, dove si trova in (50 pt, 70 pt), quando la incollo con Ctrl+V nella pagina 5, allora compare in (50 pt, 70 pt) nella pagina 5.
- Dato un oggetto vettoriale con testo e sfumature copiato dal documento A, quando lo incollo nel documento B e salvo B, allora B, aperto da solo, mostra l'oggetto identico all'originale (confronto pixel) e non contiene riferimenti a risorse mancanti.
- Dato la stessa immagine incollata 10 volte nel documento B, quando salvo, allora B contiene un solo image XObject per quell'immagine.
- Dato un oggetto selezionato, quando premo due volte Ctrl+D, allora esistono 3 oggetti uguali spostati di 0, 10 e 20 pt e gli appunti non sono cambiati.
- Dato una selezione copiata, quando la incollo in un'applicazione grafica esterna, allora vi arriva come PDF o, se l'applicazione non accetta il PDF, come immagine.

**Priorità:** P0

### OBJ-31 — Spostare oggetti tra pagine

**Descrizione:** Trasferisce oggetti da una pagina a un'altra, trascinandoli o con un comando, senza passare dagli appunti.

**Flusso:** 1. In vista continua l'utente trascina la selezione oltre il bordo della pagina, sopra un'altra pagina visibile. La pagina di destinazione si evidenzia e, al rilascio, gli oggetti vi passano nel punto di rilascio. 2. In alternativa usa il comando "Sposta in pagina…" indicando il numero o l'etichetta della pagina; la posizione rispetto all'angolo superiore sinistro resta la stessa. 3. Ogni spostamento forma un passo annullabile.

**Formati/strutture:** gli oggetti vengono tolti dal content stream della pagina d'origine e aggiunti a quello di destinazione; le risorse si trasferiscono come in OBJ-30. I tag di struttura si aggiornano: `/Pg`, `/MCID` e l'albero dei genitori (`/ParentTree`) della nuova pagina.

**Casi limite:**
- Pagina di destinazione più piccola o con orientamento diverso → oggetti traslati per rientrare.
- Oggetto più grande della pagina di destinazione → centrato, con avviso.
- Trascinamento verso pagine non visibili → la vista scorre automaticamente vicino ai bordi della finestra.
- Pagine con rotazioni diverse → l'oggetto mantiene l'orientamento rispetto alla vista.

**Criteri di accettazione:**
- Dato un'immagine a pagina 2 in (100 pt, 100 pt), quando uso "Sposta in pagina…" con pagina 7, allora la pagina 2 non la contiene più e la pagina 7 la mostra in (100 pt, 100 pt).
- Dato lo spostamento appena eseguito, quando premo Ctrl+Z, allora l'immagine torna a pagina 2 e la pagina 7 è identica a prima (confronto pixel).
- Dato un documento taggato, quando sposto un'immagine marcata `/Figure` da pagina 1 a pagina 3, allora l'elemento `/Figure` fa riferimento alla pagina 3 e la verifica della struttura non segnala errori.

**Priorità:** P1

### OBJ-32 — Replicare oggetti su più pagine

**Descrizione:** Copia gli oggetti selezionati nella stessa posizione su un insieme di pagine (es. un logo su tutte le pagine), riutilizzando gli stessi dati.

**Flusso:** 1. Comando "Replica su pagine…". 2. Scelta delle pagine: tutte, un intervallo, solo pari o dispari, un elenco. Per le pagine di formato diverso si sceglie se saltarle o adattare la posizione in proporzione. 3. La conferma registra un unico passo annullabile.

**Formati/strutture:** le copie diventano un oggetto composto condiviso: un solo Form XObject richiamato in ogni pagina, così i dati non si duplicano. Una modifica successiva su una sola occorrenza segue OBJ-26 ("solo qui" oppure "tutte").

**Casi limite:**
- Pagina d'origine compresa nell'intervallo → non viene duplicata.
- Pagine ruotate → posizione riferita alla vista.
- Documento taggato → le copie sono marcate come artefatti di paginazione (contenuto ripetuto); l'utente può scegliere diversamente.

**Criteri di accettazione:**
- Dato un logo a pagina 1, quando lo replico su tutte le 20 pagine, allora ogni pagina lo mostra nella stessa posizione e il file contiene i dati dell'immagine una sola volta.
- Dato l'opzione "salta le pagine di formato diverso" e 2 pagine A3 in un documento A4, quando replico, allora quelle 2 pagine restano invariate.

**Priorità:** P2

### OBJ-33 — Creare un collegamento (area rettangolare)

**Descrizione:** Crea sulla pagina un'area cliccabile rettangolare che porta a una destinazione: indirizzo web, pagina interna, destinazione con nome o file.

**Flusso:** 1. Strumento "Collegamento": il cursore diventa un mirino e i collegamenti esistenti vengono contornati. 2. L'utente traccia un rettangolo. In alternativa, se in modalità modifica c'è un oggetto selezionato, sceglie "Crea collegamento", che usa il riquadro dell'oggetto. 3. Nella finestra che si apre imposta:
   - tipo di destinazione: web/e-mail (OBJ-35), pagina o destinazione con nome (OBJ-36), file (OBJ-37);
   - descrizione: letta dalle tecnologie assistive e mostrata al passaggio del mouse;
   - aspetto: rettangolo visibile o invisibile, colore, spessore (1, 2 o 3 pt), stile (continuo, tratteggiato, sottolineato);
   - evidenziazione al clic: nessuna, inverti, contorno, rilievo.
4. Alla conferma il collegamento viene creato e resta selezionato; i valori di aspetto usati diventano quelli predefiniti per la sessione.

**Formati/strutture:** annotazione `/Subtype /Link` con:
- `/Rect` e la destinazione, come `/A` (azione) o `/Dest`;
- evidenziazione `/H` (`/N`, `/I`, `/O`, `/P`);
- bordo `/Border [0 0 0]` oppure `/BS` (`/W`, `/S` = `/S`, `/D`, `/U`), con colore `/C`;
- `/F` con il flag Print, `/NM` univoco, `/M` e `/Contents` per la descrizione;
- aspetto `/AP` generato.

Nei documenti taggati si crea l'elemento di struttura `/Link` con riferimento `OBJR` all'annotazione e `/StructParent`; se il rettangolo copre testo taggato, l'elemento include anche quel testo (ISO 32000-2 §12.5.6.5; PDF/UA).

**Casi limite:**
- Rettangolo più piccolo di 4×4 pt → nessun collegamento (il gesto viene trattato come un clic).
- Finestra annullata → nessuna modifica.
- Pagina ruotata → il rettangolo memorizzato copre l'area vista.
- Collegamento sovrapposto a un altro → consentito, con avviso che al clic prevale quello che si trova sopra (l'ultimo nell'elenco delle annotazioni).
- PDF/A → flag Print sempre impostato e aspetto sempre presente, anche vuoto se il collegamento è invisibile. In PDF/A-1 con intento di output non RGB il colore del bordo non è disponibile.
- Permesso di modifica delle annotazioni negato (bit 6 di `/P`) → strumento disattivato.

**Criteri di accettazione:**
- Dato lo strumento attivo, quando traccio un rettangolo di 100×20 pt e imposto l'URL "https://esempio.it", allora la pagina ha una nuova annotazione `/Link` con `/Rect` corrispondente (±0,5 pt) e azione `/URI` con valore "https://esempio.it".
- Dato un collegamento invisibile, quando salvo e riapro, allora il bordo ha spessore 0 e il collegamento non si vede ma risponde al clic.
- Dato un documento taggato, quando creo un collegamento con descrizione "Sito del produttore", allora la struttura contiene un elemento `/Link` con `OBJR` verso l'annotazione, `/Contents` vale "Sito del produttore" e un validatore PDF/UA non segnala errori sul collegamento.
- Dato un trascinamento di 2×2 pt, quando rilascio, allora non viene creato alcun collegamento.

**Priorità:** P0

### OBJ-34 — Creare un collegamento dal testo selezionato

**Descrizione:** Trasforma in collegamento un testo selezionato. L'area cliccabile segue esattamente le righe del testo, anche quando sono più di una.

**Flusso:** 1. L'utente seleziona del testo. 2. Sceglie "Crea collegamento…" dal menu contestuale, oppure preme Ctrl+K. 3. Si apre la finestra di OBJ-33. La descrizione predefinita è il testo selezionato; se il testo è un URL o un indirizzo e-mail, la destinazione è già compilata. 4. L'utente conferma.

**Formati/strutture:** annotazione `/Link` con `/QuadPoints` (un quadrilatero per riga) e `/Rect` pari al riquadro che li contiene. Stile predefinito: invisibile, oppure sottolineato a scelta.

**Casi limite:**
- Selezione su più pagine → un collegamento per pagina, tutti con la stessa destinazione.
- Selezione su più colonne → un quadrilatero per ogni segmento.
- Testo ruotato → quadrilateri ruotati.
- Lettori che ignorano `/QuadPoints` → usano `/Rect`, come previsto dal formato.
- Selezione vuota → comando disattivato.

**Criteri di accettazione:**
- Dato un testo selezionato su 2 righe, quando creo un collegamento, allora l'annotazione ha 2 quadrilateri. Cliccando in un punto compreso in `/Rect` ma esterno ai quadrilateri, il collegamento non si attiva.
- Dato il testo selezionato "www.esempio.it", quando apro "Crea collegamento…", allora la destinazione proposta è "https://www.esempio.it".
- Dato una selezione che va da pagina 3 a pagina 4, quando creo il collegamento, allora esistono due annotazioni `/Link`, una per pagina, con la stessa azione.

**Priorità:** P1

### OBJ-35 — Destinazione: indirizzo web o e-mail

**Descrizione:** Imposta come destinazione di un collegamento un URL (web, ftp, telefono) o un indirizzo e-mail, con controllo di validità.

**Flusso:** 1. Nella finestra del collegamento l'utente sceglie "Indirizzo web" e scrive o incolla l'indirizzo. 2. L'app lo normalizza: aggiunge `https://` se manca lo schema e il testo è un dominio, oppure `mailto:` se è un indirizzo e-mail (con oggetto facoltativo). Mostra l'anteprima dell'indirizzo finale. 3. L'utente conferma.

**Formati/strutture:** azione `/S /URI` con il valore `/URI` scritto in ASCII per la massima compatibilità: i domini internazionali in forma ASCII (punycode) e i caratteri non ASCII del percorso con codifica `%` (RFC 3986/3987). Gli indirizzi e-mail seguono RFC 6068. Gli URI relativi si risolvono rispetto a `/URI /Base` del catalogo, se presente, e la finestra mostra la base usata (ISO 32000-2 §12.6.4.8).

**Casi limite:**
- Schemi ammessi → http, https, mailto, ftp, tel.
- `javascript:` e `data:` → rifiutati.
- `file:` → si propone il tipo "File" (OBJ-37).
- Spazi → quelli iniziali e finali vengono tolti, quelli interni codificati.
- URL incompleto (es. "http://") → conferma disattivata, con messaggio.
- URL oltre 2000 caratteri → avviso di compatibilità.
- Credenziali nell'URL (`utente:password@`) → avviso di sicurezza.

**Criteri di accettazione:**
- Dato l'input "esempio.it/pagina", quando confermo, allora l'azione salvata ha `/URI` uguale a "https://esempio.it/pagina".
- Dato l'input "https://città.it/così", quando confermo, allora `/URI` vale "https://xn--citt-3na.it/cos%C3%AC".
- Dato l'input "javascript:alert(1)", quando provo a confermare, allora il pulsante è disattivato e compare il messaggio "schema non ammesso".
- Dato l'input "mario.rossi@esempio.it", quando confermo, allora `/URI` vale "mailto:mario.rossi@esempio.it".

**Priorità:** P0

### OBJ-36 — Destinazione: pagina interna o destinazione con nome

**Descrizione:** Collega a una pagina del documento con una vista precisa (adatta alla pagina, alla larghezza, zoom e posizione), oppure a una destinazione con nome.

**Flusso:** 1. L'utente sceglie "Pagina di questo documento". 2. Può procedere in due modi:
   - inserire il numero o l'etichetta della pagina e il tipo di vista: Adatta pagina, Adatta larghezza, Adatta altezza, Adatta area visibile, Zoom ereditato, Zoom e posizione specifici;
   - usare "Imposta dalla vista": la finestra si riduce, l'utente raggiunge il punto e lo zoom voluti e conferma; pagina, posizione e zoom correnti vengono memorizzati.
3. In alternativa sceglie "Destinazione con nome" da un elenco filtrabile, oppure ne crea una nuova dalla vista corrente dandole un nome. 4. L'utente conferma.

**Formati/strutture:** destinazione esplicita con riferimento all'oggetto pagina: `/XYZ` (sinistra, alto, zoom), `/Fit`, `/FitH`, `/FitV`, `/FitR`, `/FitB`, `/FitBH`, `/FitBV`. La destinazione si scrive in `/Dest` o in un'azione `/S /GoTo` con `/D`. Le destinazioni con nome stanno nel name tree `/Names /Dests` (o in `/Dests` del catalogo, nei file meno recenti) e sono richiamate per nome (ISO 32000-2 §12.3.2, §12.6.4.2).

**Casi limite:**
- Pagina inesistente → conferma disattivata.
- Etichette di pagina (i, ii, A-1) → accettate e convertite nella pagina fisica.
- Pagina di destinazione eliminata in seguito → il collegamento diventa "rotto" ed è segnalato da OBJ-42, senza errori all'apertura.
- Pagine riordinate → il collegamento continua a puntare alla stessa pagina, perché il riferimento è all'oggetto pagina e non al numero.
- Nome di destinazione già usato → l'app chiede un nome diverso.
- "Zoom ereditato" → la vista mantiene lo zoom corrente.

**Criteri di accettazione:**
- Dato un collegamento a "pagina 12, Adatta larghezza", quando lo clicco, allora la vista mostra la pagina 12 adattata alla larghezza, a partire dalla coordinata verticale salvata.
- Dato un collegamento alla pagina 12, quando sposto quella pagina in terza posizione e clicco il collegamento, allora si apre la stessa pagina, ora terza.
- Dato "Imposta dalla vista" con la pagina 5 a zoom 200 %, quando confermo, allora la destinazione salvata è `/XYZ` con zoom 2 e coordinate pari all'angolo superiore sinistro visibile (±1 pt).
- Dato un collegamento alla destinazione con nome "cap3", quando "cap3" viene ridefinita su un'altra pagina, allora il collegamento porta alla nuova posizione.

**Priorità:** P0

### OBJ-37 — Destinazione: file esterno

**Descrizione:** Collega a un altro file: un PDF, eventualmente a una pagina o destinazione con nome precisa, oppure un file di altro tipo da aprire con l'applicazione associata. In alternativa collega a un PDF allegato al documento.

**Flusso:** 1. L'utente sceglie "File" e seleziona il file. 2. Se il file è un PDF, sceglie la pagina o la destinazione con nome (l'elenco viene letto dal file, se accessibile) e dove aprirlo: nella stessa finestra, in una nuova scheda o secondo la preferenza dell'utente. 3. Se il file è di altro tipo, un avviso spiega che l'apertura dipenderà dalle impostazioni di sicurezza del lettore. 4. Il percorso viene salvato relativo alla cartella del documento (predefinito) oppure assoluto, a scelta. 5. Per un PDF allegato, l'utente lo sceglie dall'elenco degli allegati.

**Formati/strutture:**
- PDF esterno → azione `/S /GoToR` con specifica di file (`/F`, e `/UF` in Unicode), `/D` (array con numero di pagina a partire da 0, es. `[3 /Fit]`, oppure nome) e `/NewWindow`.
- Altri file → `/S /Launch` con `/F`.
- PDF allegato → `/S /GoToE` con dizionario di destinazione `/T` (`/R /C`, `/N`).

Riferimenti: ISO 32000-2 §7.11, §12.6.4.3, §12.6.4.4, §12.6.4.6.

**Casi limite:**
- File su un altro volume o non raggiungibile con un percorso relativo → percorso assoluto, con avviso di portabilità.
- Nome con caratteri non ASCII → `/UF` in Unicode e `/F` in forma compatibile.
- Documento PDF/A → collegamenti `/Launch` non ammessi; compare un avviso con la proposta di allegare il file.
- Documento mai salvato → percorso assoluto finché non si salva; al primo salvataggio diventa relativo, con avviso.
- PDF di destinazione cifrato → elenco delle destinazioni non disponibile; si usa il numero di pagina.

**Criteri di accettazione:**
- Dato il documento /dati/a.pdf e un collegamento a /dati/allegati/b.pdf, pagina 4, quando salvo, allora l'azione è `/GoToR` con `/F` "allegati/b.pdf" e `/D` con primo elemento 3.
- Dato la cartella /dati spostata altrove con tutti i suoi file, quando clicco il collegamento, allora si apre b.pdf alla pagina 4.
- Dato un documento PDF/A-2b, quando creo un collegamento a un file .xlsx, allora compare l'avviso che l'azione non è ammessa dal profilo.

**Priorità:** P1

### OBJ-38 — Modificare collegamenti esistenti

**Descrizione:** Seleziona, sposta, ridimensiona, copia e modifica destinazione e aspetto dei collegamenti già presenti, anche se creati da altri programmi.

**Flusso:** 1. Con lo strumento "Collegamento" tutti i collegamenti della pagina sono contornati. Un clic ne seleziona uno; Maiusc+clic ne aggiunge altri. 2. Il trascinamento sposta, le maniglie ridimensionano, le frecce spostano di 1 pt. 3. Un doppio clic o "Proprietà" apre la finestra di OBJ-33 con i valori correnti, dove si cambiano destinazione, descrizione o aspetto. 4. Con più collegamenti selezionati si cambia l'aspetto di tutti insieme. 5. Ctrl+C, Ctrl+V e Ctrl+D copiano, incollano e duplicano i collegamenti, anche tra documenti. 6. Al passaggio del mouse un suggerimento mostra la destinazione.

**Formati/strutture:** si aggiornano `/Rect`, `/QuadPoints`, `/A` o `/Dest`, `/H`, `/BS` o `/Border`, `/C`, `/Contents` e `/M`, e si rigenera `/AP`. Le azioni che l'editor non gestisce restano intatte e compaiono in sola lettura, con una descrizione: JavaScript, invio modulo, azioni con nome, catene `/Next`. Si possono sostituire solo con conferma esplicita.

**Casi limite:**
- Collegamento con `/QuadPoints` → spostandolo si traslano anche i quadrilateri; ridimensionandolo i quadrilateri vengono rimossi e diventa rettangolare, con avviso.
- `/Dest` diretto → convertito in azione `/GoTo` solo se la destinazione cambia.
- Collegamento bloccato (flag Locked) → non si sposta; le proprietà si modificano solo dopo uno sblocco esplicito.
- `/Border` o aspetto assenti → si applicano i valori predefiniti del formato (bordo di 1 pt) e la finestra li mostra.
- Collegamento interno incollato in un altro documento → punta alla pagina con lo stesso numero, se esiste, altrimenti è segnalato come rotto; in entrambi i casi compare un avviso.

**Criteri di accettazione:**
- Dato un collegamento URL esistente, quando cambio la destinazione in "pagina 3" e salvo, allora l'annotazione ha un'azione `/GoTo` verso la pagina 3 e nessuna azione `/URI`.
- Dato un collegamento con azione JavaScript, quando ne cambio solo il colore del bordo e salvo, allora l'azione JavaScript è identica byte per byte.
- Dato 5 collegamenti selezionati, quando imposto "rettangolo invisibile", allora tutti e 5 hanno un bordo di spessore 0.
- Dato un collegamento con `/QuadPoints`, quando lo sposto di 10 pt a destra, allora `/Rect` e tutti i quadrilateri risultano traslati di 10 pt.

**Priorità:** P0

### OBJ-39 — Eliminare collegamenti

**Descrizione:** Rimuove uno o più collegamenti, oppure tutti quelli di un intervallo di pagine, con un filtro per tipo.

**Flusso:** 1. Con uno o più collegamenti selezionati, l'utente preme Canc oppure sceglie "Elimina" dal menu contestuale. 2. In alternativa usa il comando "Elimina collegamenti…": sceglie l'ambito (pagina corrente, intervallo, tutto il documento) e il tipo (web/e-mail, interni, file, altre azioni, tutti), vede in anteprima quanti collegamenti verranno eliminati e conferma. 3. L'eliminazione forma un unico passo annullabile.

**Formati/strutture:** si rimuovono le annotazioni `/Link` da `/Annots`, i relativi elementi di struttura `/Link` e le voci di `/ParentTree`. Il testo contenuto nell'elemento `/Link` resta nella struttura, agganciato all'elemento padre. Le destinazioni con nome non vengono toccate.

**Casi limite:**
- Nessun collegamento nell'ambito → messaggio, nessuna modifica.
- Collegamenti bloccati → inclusi solo con conferma esplicita.
- Widget dei moduli e altre annotazioni → mai coinvolti.
- Testo e grafica sotto il collegamento → restano invariati.

**Criteri di accettazione:**
- Dato un documento con 12 collegamenti, di cui 5 web, quando elimino i collegamenti web in tutto il documento, allora restano 7 collegamenti e le pagine appaiono come prima, eccetto i bordi visibili dei collegamenti rimossi.
- Dato un collegamento selezionato, quando premo Canc e poi Ctrl+Z, allora il collegamento torna con la stessa destinazione e lo stesso aspetto.
- Dato un documento taggato, quando elimino un collegamento che conteneva testo, allora il testo resta nell'albero di struttura e l'ordine di lettura non cambia.

**Priorità:** P0

### OBJ-40 — Rilevamento automatico degli URL nel testo

**Descrizione:** Trova nel testo gli indirizzi web ed e-mail scritti in chiaro e li trasforma in collegamenti. In alternativa li rende cliccabili solo durante la lettura, senza modificare il file.

**Flusso:** 1. Comando "Crea collegamenti dagli URL", con scelta dell'ambito (pagina, intervallo, documento) e dei tipi (web, e-mail). 2. L'app analizza il testo e mostra i risultati (pagina, testo trovato, destinazione proposta), ciascuno con una casella di selezione. Il testo già coperto da un collegamento viene escluso. 3. Alla conferma crea i collegamenti selezionati, con l'aspetto scelto (predefinito: invisibile), in un unico passo annullabile. 4. La preferenza di lettura "Rendi cliccabili gli URL del testo", attiva per impostazione predefinita, li riconosce al volo senza modificare il documento, con la stessa conferma di sicurezza di OBJ-41.

**Formati/strutture:** vengono riconosciuti:
- indirizzi con schema (http, https, ftp);
- indirizzi che iniziano con "www.";
- indirizzi e-mail.

Dal risultato si escludono la punteggiatura finale (. , ; : ! ?) e le parentesi di chiusura non bilanciate. Un URL spezzato su due righe viene ricongiunto quando la prima riga termina, senza spazio, con un carattere valido negli URL e la seconda prosegue senza spazio. I collegamenti si creano come in OBJ-34 (`/QuadPoints` per riga) e le destinazioni si normalizzano come in OBJ-35.

**Casi limite:**
- Documento senza livello di testo → messaggio che suggerisce il riconoscimento del testo (OCR).
- Trattino di sillabazione a fine riga → escluso se senza trattino si ottiene un dominio valido; altrimenti il risultato è segnalato come dubbio.
- Domini con caratteri non ASCII → convertiti come in OBJ-35.
- Migliaia di risultati → elaborazione con avanzamento, interrompibile.
- URL tra virgolette o parentesi → delimitatori esclusi.

**Criteri di accettazione:**
- Dato una pagina con i testi "Visita www.esempio.it." e "scrivi a info@esempio.it", quando eseguo il rilevamento, allora vengono proposti 2 collegamenti: "https://www.esempio.it" (senza il punto finale) e "mailto:info@esempio.it".
- Dato il testo "(https://esempio.it/a_(b))", quando eseguo il rilevamento, allora la destinazione proposta è "https://esempio.it/a_(b)".
- Dato un URL già coperto da un collegamento, quando eseguo il rilevamento, allora quell'URL non viene proposto.
- Dato la preferenza di lettura attiva e nessun collegamento creato, quando clicco un URL del testo, allora compare la conferma di apertura e il documento non risulta modificato.
- Dato 30 collegamenti creati con il rilevamento, quando premo una volta Ctrl+Z, allora vengono rimossi tutti e 30.

**Priorità:** P1

### OBJ-41 — Attivazione dei collegamenti e sicurezza

**Descrizione:** Comportamento al clic su un collegamento durante la lettura: navigazione interna, apertura di URL e file con conferme di sicurezza, ritorno alla vista precedente.

**Flusso:** 1. Sopra un collegamento il puntatore diventa una mano e un suggerimento mostra la destinazione completa: URL, "Pagina 12" o nome del file. 2. Destinazione interna: la vista si imposta secondo la destinazione. "Indietro" (Alt+←) torna alla vista precedente, "Avanti" (Alt+→) la ripristina. 3. URL: una finestra di conferma mostra l'indirizzo completo con il dominio evidenziato e l'opzione "Consenti sempre per questo sito". Dopo la conferma l'indirizzo si apre nel browser predefinito; un `mailto:` si apre nel client di posta predefinito. 4. Collegamento a un PDF: si apre in una nuova scheda o nella stessa, secondo `/NewWindow` e le preferenze, alla destinazione indicata. 5. Collegamento a un file di altro tipo (`/Launch`): bloccato per impostazione predefinita. L'utente può autorizzarlo per il singolo documento dopo un avviso che mostra il percorso completo. 6. Negli strumenti di modifica il clic seleziona; Ctrl+clic segue il collegamento.

**Formati/strutture:** si gestiscono le azioni `/GoTo`, `/GoToR`, `/GoToE`, `/URI`, `/Launch` e `/Named` (pagina successiva, precedente, prima, ultima), comprese le catene `/Next`. Le azioni JavaScript seguono le impostazioni della sezione sullo scripting. I siti consentiti si memorizzano nelle preferenze dell'utente, dove si possono rivedere e revocare.

**Casi limite:**
- Destinazione inesistente → messaggio "destinazione non trovata", nessuno spostamento.
- File collegato mancante → messaggio con il percorso cercato.
- Schema non ammesso (es. `javascript:`) → non eseguito, con avviso.
- Testo visibile che mostra un dominio diverso da quello di destinazione → la conferma evidenzia la differenza.
- Collegamenti sovrapposti → si attiva quello che sta sopra.

**Criteri di accettazione:**
- Dato un collegamento alla pagina 20, quando lo clicco dalla pagina 2 e poi premo Alt+←, allora la vista torna alla pagina 2 con lo stesso zoom e la stessa posizione.
- Dato un collegamento a "https://esempio.it", quando lo clicco, allora compare la conferma con l'indirizzo completo; se annullo, il browser non si apre.
- Dato "Consenti sempre per questo sito" scelto per esempio.it, quando clicco un altro collegamento verso esempio.it, allora il browser si apre senza conferma.
- Dato un collegamento `/Launch` verso uno script, quando lo clicco, allora non viene eseguito nulla e compare l'avviso di blocco.
- Dato un collegamento con testo visibile "www.banca.it" e destinazione "https://altro.example", quando lo clicco, allora la conferma segnala la differenza tra testo e destinazione.

**Priorità:** P0

### OBJ-42 — Verifica dei collegamenti

**Descrizione:** Pannello che elenca tutti i collegamenti del documento, individua quelli rotti o sospetti e ne permette la correzione rapida.

**Flusso:** 1. Il pannello "Collegamenti" elenca per ogni collegamento pagina, tipo, destinazione, descrizione e stato, con ordinamento e filtri. 2. La verifica locale è automatica: controlla che pagine e destinazioni con nome esistano, che i file collegati siano raggiungibili e che gli URL siano scritti correttamente. 3. La verifica online degli URL è facoltativa e parte solo dopo un consenso esplicito, perché contatta i siti. Gli stati possibili sono: OK, reindirizzato, non trovato, errore, tempo scaduto (10 s). 4. Un doppio clic su una voce mostra il collegamento; da lì si può modificarlo (OBJ-38) o eliminarlo (OBJ-39). 5. Il rapporto si esporta in CSV.

**Formati/strutture:** si leggono tutte le annotazioni `/Link` e le loro azioni; le destinazioni si risolvono tramite `/Dests` e `/Names /Dests`. Il rapporto CSV è in UTF-8, con colonne pagina, tipo, destinazione e stato.

**Casi limite:**
- Nessun collegamento → pannello vuoto, con messaggio.
- Rete assente → gli URL risultano "non verificati", senza errori bloccanti.
- Molti URL → richieste in parallelo limitate e interrompibili.
- Azioni non standard → stato "non verificabile".

**Criteri di accettazione:**
- Dato un documento di 40 pagine con un collegamento alla pagina 50 e uno a un file inesistente, quando apro il pannello, allora entrambi risultano rotti senza alcun accesso alla rete.
- Dato la verifica online non autorizzata, quando apro il pannello, allora non parte alcuna richiesta di rete.
- Dato il rapporto esportato, quando lo apro, allora contiene una riga per ogni collegamento del documento.

**Priorità:** P2

### OBJ-43 — Annulla e ripeti

**Descrizione:** Cronologia, separata per ogni documento, che permette di annullare e ripetere un passo alla volta tutte le operazioni di questa sezione e delle altre sezioni di modifica.

**Flusso:** 1. Ctrl+Z annulla l'ultima operazione; Ctrl+Maiusc+Z o Ctrl+Y la ripetono. 2. Le voci di menu indicano l'operazione, per esempio "Annulla Sposta immagine". 3. Contano come un solo passo un gesto continuo (trascinamento, cursore) e un comando su più oggetti o pagine (es. rilevamento degli URL, replica su pagine). 4. Una nuova operazione dopo un annullamento cancella i passi che si potevano ripetere.

**Formati/strutture:** la cronologia è in memoria, separata per documento, con profondità minima di 100 passi (configurabile, entro un limite di memoria). Il salvataggio non la svuota. L'indicatore "modificato" riflette la differenza dall'ultimo salvataggio: se con Annulla si torna allo stato salvato, si spegne.

**Casi limite:**
- Incolla da A a B → si annulla solo in B.
- Operazione fallita a metà → nessun passo registrato e documento invariato (l'operazione è atomica).
- Profondità massima raggiunta → si scartano i passi più vecchi.
- Chiusura del documento → la cronologia viene eliminata.
- Annulla durante un trascinamento → il trascinamento si interrompe.
- Operazioni che non modificano il documento (estrazioni, verifiche) → non vengono registrate.

**Criteri di accettazione:**
- Dato un documento appena aperto, quando eseguo 5 operazioni (inserisci immagine, sposta, ruota, ritaglia, crea collegamento) e poi 5 annullamenti, allora l'indicatore di modifica è spento e ogni pagina è identica all'originale (confronto pixel).
- Dato quei 5 annullamenti, quando eseguo 5 ripetizioni, allora il documento torna identico allo stato precedente agli annullamenti, per resa e per numero di oggetti e annotazioni.
- Dato uno spostamento fatto con un trascinamento di 2 secondi, quando premo una volta Ctrl+Z, allora l'oggetto torna alla posizione iniziale.
- Dato un salvataggio eseguito dopo 3 operazioni, quando premo Ctrl+Z, allora l'ultima operazione viene annullata e il documento risulta modificato.
- Dato 150 operazioni eseguite, quando annullo ripetutamente, allora posso annullarne almeno 100.

**Priorità:** P0


## 5. Annotazioni e commenti

> Sezione in redazione.


## 6. Conversione, creazione e ottimizzazione

*Convenzioni.* Le conversioni da PDF (CNV-03…CNV-15) ereditano opzioni e casi limite comuni da CNV-01 e la gestione delle scansioni da CNV-02. Termini usati nei criteri: *fixture* = file di prova versionato nel corpus di test, con risultato atteso documentato; *similarità testuale* = 1 − distanza di edit normalizzata sulla sequenza di parole, dopo normalizzazione Unicode NFC, espansione delle legature e compattazione degli spazi; *accuratezza per carattere* = 1 − distanza di edit sui caratteri divisa per la lunghezza del testo di riferimento; *differenza visiva* = percentuale di pixel che differiscono di oltre 8/255 in almeno un canale tra due rendering a 100 dpi; *validatore indipendente* = strumento di verifica conforme alla norma citata e distinto dal motore di Foliant; *hardware di riferimento* = configurazione fissata nel piano di test.

### CNV-01 — Esporta come: finestra di conversione e opzioni comuni

**Descrizione:** Punto di accesso unico ("File › Esporta come…" e pannello "Converti") a tutte le conversioni da PDF, con le opzioni condivise dai formati: intervallo di pagine, layout, OCR, risoluzione delle immagini, destinazione e nome dei file, report finale.

**Flusso:** 1. L'utente sceglie il formato di uscita per il documento aperto o per un file non aperto. 2. Imposta l'intervallo: tutte, pagina corrente, pagine selezionate nelle miniature o espressione (es. `1-3, 7, 10-fine`, `pari`, `dispari`). 3. Imposta le opzioni comuni pertinenti al formato: layout "fedele" o "testo scorrevole"; OCR automatico/sempre/mai e lingue (CNV-02); risoluzione delle immagini incorporate (originale o massimo N ppi) e di rasterizzazione della grafica non convertibile (default 300 dpi); inclusione di annotazioni, campi modulo, segnalibri e link. 4. Sceglie cartella, modello di nome (`{nome}`, `{pagina}`, `{data}`, `{formato}`) e comportamento se il file esiste (rinomina con suffisso, sovrascrivi, chiedi). 5. Avvia: l'elaborazione procede in background con avanzamento per pagina, tempo stimato e pulsante Annulla; al termine mostra esito, report di conversione (avvisi con numero di pagina, esportabile come testo) e le azioni "Apri" e "Mostra nella cartella".

**Formati/strutture:** Ingresso PDF 1.0–2.0, inclusi PDF/A, file linearizzati e con aggiornamenti incrementali. Il documento aperto è convertito nello stato in memoria (modifiche non salvate comprese) senza alterare il file su disco. "Fedele": una pagina di uscita per ogni pagina, posizioni preservate; "scorrevole": paragrafi, titoli, elenchi e tabelle ricostruiti in ordine di lettura, preso dalla struttura logica (`/StructTreeRoot`) se presente, altrimenti dall'analisi geometrica; la scelta vale per DOCX, RTF, HTML, EPUB e TXT. Livelli (`/OCProperties`) nello stato di visibilità corrente. Le ultime opzioni sono ricordate per formato e salvabili come preset.

**Casi limite:** PDF cifrato → richiesta della password di apertura; se `/P` vieta l'estrazione del contenuto serve la password proprietario, altrimenti la conversione è bloccata indicando il permesso mancante; intervallo non valido → errore in linea e nessun avvio; JavaScript del documento → mai eseguito; file enormi (> 1 GB o > 5 000 pagine) → elaborazione pagina per pagina a memoria limitata e scrittura su file temporaneo rinominato solo a fine lavoro, così annullamenti o crash non lasciano file parziali; spazio su disco insufficiente → interruzione con messaggio e pulizia dei temporanei; portfolio PDF → scelta tra documento di copertina e singoli PDF incorporati; allegati → non convertiti, elencati nel report; PDF firmato → il report avverte che le firme non sono trasferibili nel formato di uscita.

**Criteri di accettazione:**
- Dato un PDF di 20 pagine, quando si imposta l'intervallo `2-4, 10` e si esporta in TXT, allora il file contiene solo il testo delle pagine 2, 3, 4 e 10, in quest'ordine.
- Dato un documento di 20 pagine, quando si inserisce l'intervallo `25-30` e si preme Converti, allora compare un errore di validazione e non viene creato alcun file.
- Data una conversione in corso, quando l'utente preme Annulla, allora entro 2 s l'elaborazione si ferma e nella cartella di destinazione non resta alcun file nuovo né temporaneo.
- Dato un file di destinazione già esistente e la politica "rinomina", quando si converte, allora il risultato viene salvato con suffisso ` (1)` e il file esistente resta byte-identico.
- Dato un PDF con estrazione vietata e password proprietario non fornita, quando si sceglie DOCX, allora la conversione è bloccata con un messaggio che nomina il permesso mancante.
- Dato un PDF di 5 000 pagine su hardware di riferimento, quando lo si converte in TXT, allora il picco di memoria del processo resta sotto 1 GB e l'interfaccia risponde agli input entro 200 ms per tutta la durata.

**Priorità:** P0

### CNV-02 — Conversione di PDF scansionati con OCR automatico

**Descrizione:** In ogni conversione verso formati con testo (DOCX, XLSX, CSV, PPTX, TXT, RTF, HTML, XML, EPUB) individua le pagine prive di testo estraibile e vi applica il riconoscimento ottico dei caratteri, così che l'uscita contenga testo modificabile invece di sole immagini.

**Flusso:** 1. All'avvio il motore classifica ogni pagina: nativa (testo estraibile), scansionata (nessun testo e un'immagine che copre almeno l'80% dell'area), già OCR (testo invisibile sovrapposto all'immagine), mista. 2. Con OCR "automatico" riconosce le pagine scansionate e riusa il livello di testo delle pagine già OCR (salvo l'opzione "ripeti OCR"). 3. Pre-elaborazione automatica: orientamento (0/90/180/270°), raddrizzamento, riduzione del rumore. 4. Il testo riconosciuto, con posizione, dimensione e stile stimati, alimenta il convertitore come testo nativo; in modalità fedele l'immagine originale può restare come sfondo (opzione). 5. Il report elenca pagine riconosciute, lingue e confidenza media.

**Formati/strutture:** Lingue multiple selezionabili, con default dalla chiave `/Lang` del catalogo o dalla lingua dell'interfaccia; testo invisibile riconosciuto dal modo di resa `Tr 3`; motore OCR locale e offline, condiviso con l'area OCR.

**Casi limite:** documento misto nativo/scansionato → OCR solo dove serve; testo nativo non decodificabile (font senza `/ToUnicode`, codifiche personalizzate) → OCR della sola area con avviso; pacchetto lingua non installato → proposta di installarlo o di scegliere un'altra lingua, senza blocco; scansione sotto 150 dpi → ricampionamento interno e avviso di accuratezza ridotta; OCR "mai" con pagine scansionate → avviso prima dell'avvio che l'uscita conterrà solo immagini; scrittura a mano o pagina illeggibile → immagine conservata e pagina segnalata; tabelle scansionate → celle ricostruite dalle linee e dall'allineamento del testo riconosciuto.

**Criteri di accettazione:**
- Data una fixture scansionata a 300 dpi con testo stampato in italiano, quando la si converte in DOCX con OCR automatico, allora l'accuratezza per carattere del testo nel DOCX è ≥ 98%.
- Dato un PDF di 3 pagine (1–2 native, 3 scansionata), quando lo si converte in TXT, allora il report indica l'OCR sulla sola pagina 3 e il testo delle pagine 1–2 è identico a quello dell'estrazione diretta.
- Data la stessa pagina scansionata diritta e ruotata di 90°, quando si convertono entrambe con OCR automatico, allora l'accuratezza per carattere differisce di non più di 1 punto percentuale.
- Dato un PDF interamente scansionato e OCR impostato su "mai", quando si avvia la conversione in DOCX, allora prima dell'avvio compare un avviso con l'opzione di attivare l'OCR.

**Priorità:** P1

### CNV-03 — PDF → DOCX (documento modificabile)

**Descrizione:** Converte il PDF in un documento DOCX modificabile che conserva testo, formattazione, immagini, tabelle, link e struttura, in modalità fedele o scorrevole.

**Flusso:** 1. Sceglie DOCX in CNV-01 e la modalità di layout. 2. Opzioni specifiche: commenti PDF come commenti del documento; rilevamento di intestazioni e piè di pagina ripetuti (spostati nelle aree di intestazione/piè); grafica vettoriale come forme o come immagini. 3. Avvia; il motore analizza ogni pagina in blocchi di testo, colonne, tabelle, elenchi, immagini e grafica. 4. Scrive il DOCX e il report (font sostituiti, pagine OCR, aree rasterizzate).

**Formati/strutture:** Office Open XML (ISO/IEC 29500, WordprocessingML). Conserva: famiglia, dimensione, colore, grassetto, corsivo, sottolineato, barrato, apice e pedice; allineamento, rientri, interlinea e spaziatura dei paragrafi; elenchi puntati e numerati; tabelle con celle unite, bordi e sfondi; colonne; formato e margini di pagina; hyperlink (azioni URI) e link interni (segnalibri del documento); titoli dalla struttura logica o dalla gerarchia dei corpi; testo da destra a sinistra e CJK; evidenziazioni come evidenziazione del testo; valori dei campi modulo come testo. Nome del font originale ripulito dal prefisso di subset (`ABCDEF+`). In modalità scorrevole: legature espanse e parole sillabate a fine riga ricongiunte.

**Casi limite:** font Type3, simbolici o senza mappatura Unicode → decodifica dai nomi dei glifi, poi OCR dell'area se attivo, altrimenti area inserita come immagine con avviso; font non installato sul sistema → nome originale conservato nel DOCX e sostituzione segnalata; tabelle senza bordi, con celle unite o su più pagine → tabella unica se le colonne sono allineate, altrimenti testo posizionato; tabelle annidate → tabella dentro cella; grafici e disegni vettoriali complessi → immagine alla risoluzione scelta; testo ruotato o verticale → casella di testo ruotata; pagine di formato diverso → nuova sezione a ogni cambio; PDF scansionato → CNV-02; migliaia di pagine → scrittura in streaming, nessun limite di pagine.

**Criteri di accettazione:**
- Dato un PDF nativo di 10 pagine a colonna singola, quando lo si converte in modalità scorrevole, allora il DOCX è valido rispetto agli schemi ISO/IEC 29500 e la similarità testuale con il PDF è ≥ 99%.
- Dato un PDF con una tabella bordata di 5 righe × 4 colonne, quando lo si converte, allora il DOCX contiene una tabella 5 × 4 con il contenuto di ogni cella uguale all'originale.
- Dato un PDF a due colonne, quando lo si converte in modalità scorrevole, allora per ogni pagina tutto il testo della colonna sinistra precede quello della colonna destra.
- Dato un PDF A4 di 6 pagine con margini di 20 mm, quando lo si converte in modalità fedele, allora ogni sezione del DOCX è A4 (±1 mm) e il documento contiene 5 interruzioni esplicite di pagina o di sezione, una per ogni cambio di pagina.
- Dato un PDF con 3 link URL e 2 link a pagine interne, quando lo si converte, allora il DOCX contiene 3 hyperlink con gli stessi URL e 2 collegamenti interni che puntano al testo di destinazione.
- Dato un PDF con un'immagine JPEG incorporata e risoluzione "originale", quando lo si converte, allora l'immagine nel DOCX ha le stesse dimensioni in pixel della sorgente.

**Priorità:** P0

### CNV-04 — PDF → XLSX (tabelle in foglio di calcolo)

**Descrizione:** Estrae le tabelle del PDF in un foglio di calcolo XLSX con celle tipizzate (numeri, date, testo), pronte per formule e analisi.

**Flusso:** 1. Sceglie XLSX in CNV-01. 2. Opzioni: un foglio per tabella, per pagina o unico; unione delle tabelle che proseguono su più pagine; inclusione del testo esterno alle tabelle; convenzioni numeriche (separatore decimale e delle migliaia) dedotte dalla lingua del documento o scelte dall'utente. 3. Facoltativo: anteprima con le tabelle rilevate evidenziate, in cui l'utente può correggere i bordi della tabella e le linee di colonna. 4. Avvia e scrive il file con il report.

**Formati/strutture:** Office Open XML (ISO/IEC 29500, SpreadsheetML). Celle numeriche per numeri, importi, percentuali (es. 15% → 0,15 con formato percentuale) e negativi con segno o tra parentesi; date come valori data; testo per codici con zeri iniziali (CAP, codici articolo); celle unite per intestazioni su più livelli; larghezze di colonna adattate; grassetto, allineamento, sfondi e bordi riportati. Rilevamento sia delle tabelle bordate (linee vettoriali) sia di quelle senza bordi (allineamento del testo in colonne).

**Casi limite:** tabelle senza bordi o con celle unite → ricostruzione per allineamento e celle unite in uscita; tabella su più pagine con intestazione ripetuta → con "unisci" una sola tabella senza intestazioni duplicate; tabelle su pagine orizzontali o ruotate → lette nell'orientamento di visualizzazione; celle con testo in font non decodificabile → OCR della cella (CNV-02); pagina senza tabelle → foglio omesso o testo in colonna A, secondo l'opzione; oltre 1 048 576 righe o 16 384 colonne → ripartizione su più fogli con avviso; tabella scansionata → CNV-02 a livello di cella.

**Criteri di accettazione:**
- Data una tabella di 20 righe × 6 colonne con importi nel formato `1.234,56` e una riga di totali, quando la si converte, allora ogni importo è una cella numerica (es. 1234,56) e la somma di ogni colonna calcolata nel foglio coincide con il totale stampato nel PDF.
- Data una tabella che prosegue su 3 pagine con intestazione ripetuta e l'opzione "unisci" attiva, quando la si converte, allora il foglio ha una sola riga di intestazione e tutte le righe di dati nell'ordine originale, senza righe vuote intermedie.
- Data una cella con valore `00123`, quando la si converte, allora la cella risultante è di tipo testo e mostra `00123`.
- Data una tabella con intestazione su due livelli, quando la si converte, allora le celle dell'intestazione superiore risultano unite sullo stesso intervallo di colonne dell'originale.

**Priorità:** P1

### CNV-05 — PDF → CSV

**Descrizione:** Esporta i dati tabellari del PDF in file CSV, uno per tabella o unico, per l'importazione in database e strumenti di analisi.

**Flusso:** 1. Sceglie CSV in CNV-01. 2. Opzioni: un file per tabella (`{nome}_p{pagina}_t{n}.csv`) o file unico con le tabelle separate da una riga vuota; delimitatore (virgola, punto e virgola, tabulazione; default punto e virgola se la lingua usa la virgola decimale); codifica UTF-8 con o senza BOM; fine riga CRLF o LF; numeri "come visualizzati" o normalizzati (punto decimale, senza separatore delle migliaia). 3. Avvia; il rilevamento delle tabelle è quello di CNV-04.

**Formati/strutture:** CSV secondo RFC 4180: campi che contengono delimitatore, virgolette o a capo racchiusi tra virgolette doppie, virgolette interne raddoppiate; celle unite → valore nella prima cella, le altre vuote.

**Casi limite:** nessuna tabella nell'intervallo → avviso e nessun file (oppure testo riga per riga, se scelto); a capo dentro una cella → conservato nel campo tra virgolette; caratteri non latini → UTF-8 corretto; tabelle complesse con celle unite → appiattite secondo la regola sopra, con avviso; tabella scansionata → CNV-02.

**Criteri di accettazione:**
- Data una cella che contiene `Rossi; "Mario"` e un a capo, quando la si esporta con delimitatore punto e virgola, allora un parser CSV conforme a RFC 4180 configurato con il punto e virgola rilegge la cella identica all'originale.
- Dato un PDF con tabelle convertito sia in XLSX (CNV-04) sia in CSV, quando si confrontano le due matrici di celle, allora coincidono per numero di righe, di colonne e testo di ogni cella.
- Dato un PDF senza tabelle, quando lo si esporta in CSV con le opzioni predefinite, allora l'app avvisa che non ci sono tabelle e non crea alcun file.

**Priorità:** P1

### CNV-06 — PDF → PPTX (presentazione)

**Descrizione:** Converte ogni pagina in una diapositiva modificabile, con testi in caselle di testo, immagini e forme come oggetti separati.

**Flusso:** 1. Sceglie PPTX in CNV-01. 2. Opzioni: dimensione diapositiva uguale alla pagina (default) o 16:9/4:3 con adattamento; grafica vettoriale come forme o come immagine; immagine a pagina intera usata come sfondo della diapositiva. 3. Avvia e scrive il file con il report.

**Formati/strutture:** Office Open XML (ISO/IEC 29500, PresentationML). Una diapositiva per pagina; caselle di testo con font, corpo, colore, allineamento ed elenchi; immagini come oggetti immagine; linee, rettangoli, ellissi e tracciati semplici come forme modificabili; hyperlink conservati; commenti PDF facoltativamente nelle note del relatore.

**Casi limite:** pagina scansionata senza OCR → diapositiva con immagine a pagina intera e avviso; pagina con migliaia di oggetti vettoriali (tavole tecniche) → area grafica rasterizzata alla risoluzione scelta; formato pagina non standard (es. A4 verticale) → diapositiva personalizzata con le stesse proporzioni; font non installato → nome originale conservato e sostituzione segnalata; testo con effetti non rappresentabili (motivi, tracciati di ritaglio) → immagine.

**Criteri di accettazione:**
- Dato un PDF di 12 pagine 16:9, quando lo si converte, allora il PPTX valido contiene 12 diapositive 16:9 e il testo di ogni pagina sta in caselle di testo modificabili della diapositiva corrispondente, con similarità testuale ≥ 98%.
- Data una pagina con un titolo e un'immagine, quando la si converte, allora la diapositiva contiene una casella di testo e un oggetto immagine distinti, in posizioni che differiscono da quelle della pagina di non più del 2% della larghezza.
- Dato un PDF A4 verticale e dimensione "come la pagina", quando lo si converte, allora le diapositive hanno proporzione 210:297 (±0,5%).

**Priorità:** P1

### CNV-07 — PDF → immagini (PNG, JPEG, TIFF, BMP, GIF)

**Descrizione:** Esporta le pagine come immagini raster, un file per pagina o un TIFF multipagina, con risoluzione, colore e compressione configurabili.

**Flusso:** 1. Sceglie il formato in CNV-01. 2. Imposta risoluzione (72–1200 dpi; preset 96, 150, 300, 600), modalità colore (colore, scala di grigi, bianco e nero a soglia o con retinatura), opzioni di formato (qualità JPEG 1–100, compressione TIFF, sfondo trasparente per PNG, TIFF singolo o multipagina), inclusione di annotazioni e campi modulo, antialiasing. 3. Avvia; i file sono nominati secondo il modello (es. `{nome}_{pagina:03}.png`).

**Formati/strutture:** PNG (ISO/IEC 15948, 8 o 16 bit, canale alfa), JPEG (ISO/IEC 10918-1, baseline o progressivo), TIFF 6.0 (nessuna compressione, LZW, Deflate, JPEG, CCITT G4 per il bianco e nero; singolo o multipagina), BMP (24 bit, 8 bit indicizzato, 1 bit), GIF89a (palette fino a 256 colori). Risoluzione scritta nei metadati; profilo sRGB incorporato dove il formato lo consente; area resa = `/CropBox` con la rotazione `/Rotate` applicata.

**Casi limite:** dimensioni in pixel oltre i limiti del formato (65 535 px per lato in JPEG e GIF) o della memoria → avviso con i dpi massimi ammessi; rendering sempre a strisce per contenere la memoria; TIFF multipagina oltre 4 GB → suddivisione in più file con avviso; pagine di formati diversi → ogni immagine con le dimensioni della propria pagina; trasparenza in JPEG o BMP → composizione su bianco; documenti CMYK o con tinte piatte → conversione in RGB tramite l'output intent del documento se presente; font non incorporato → resa con font sostitutivo e avviso; PDF scansionato → rasterizzato direttamente senza OCR.

**Criteri di accettazione:**
- Dato un PDF A4 di 3 pagine, quando lo si esporta in PNG a 300 dpi, allora si ottengono 3 file di 2480 × 3508 pixel (±1) con risoluzione 300 dpi nei metadati e nomi da `{nome}_001.png` a `{nome}_003.png`.
- Dato un PDF A4 di 3 pagine, quando lo si esporta in TIFF multipagina bianco e nero con compressione CCITT G4, allora si ottiene un unico file con 3 immagini a 1 bit.
- Dato l'export GIF di una pagina a colori, quando si analizza il file, allora la palette ha al massimo 256 voci e le dimensioni in pixel coincidono con l'export PNG alla stessa risoluzione.
- Data una pagina con `/Rotate 90`, quando la si esporta, allora l'immagine è orientata come a schermo (larghezza e altezza scambiate rispetto al `/MediaBox`).
- Data l'opzione "includi annotazioni" disattivata, quando si confronta l'immagine con il rendering della pagina senza annotazioni, allora la differenza visiva è 0%.

**Priorità:** P0

### CNV-08 — PDF → immagine lunga

**Descrizione:** Unisce le pagine selezionate in un'unica immagine verticale continua, comoda da condividere in chat e sui social.

**Flusso:** 1. Sceglie "Immagine lunga" in CNV-01 (PNG o JPEG). 2. Imposta risoluzione, spaziatura tra le pagine in pixel e relativo colore, allineamento delle pagine di larghezza diversa (centrato o a sinistra). 3. Avvia.

**Formati/strutture:** PNG o JPEG; larghezza = pagina più larga alla risoluzione scelta; altezza = somma delle altezze più le spaziature; composizione a strisce senza tenere l'intera immagine in memoria.

**Casi limite:** altezza oltre il limite del formato (65 535 px in JPEG) o oltre una soglia configurabile → proposta di ridurre la risoluzione o di dividere in più immagini numerate (`_parte1`, `_parte2`…); pagine con orientamenti diversi → mantenute come visualizzate; una sola pagina → risultato identico a CNV-07.

**Criteri di accettazione:**
- Dato un PDF di 5 pagine A4 verticali, quando lo si esporta come immagine lunga PNG a 96 dpi con spaziatura 0, allora l'immagine misura 794 × 5615 pixel (±5).
- Dato un risultato JPEG che supererebbe 65 535 pixel di altezza, quando si sceglie la divisione, allora ogni parte rispetta il limite e la somma delle altezze delle parti è uguale all'altezza prevista.

**Priorità:** P2

### CNV-09 — PDF → testo semplice (TXT)

**Descrizione:** Esporta il testo in un file di testo semplice, in ordine di lettura oppure riproducendo l'impaginazione con spazi.

**Flusso:** 1. Sceglie TXT in CNV-01. 2. Opzioni: modalità "ordine di lettura" o "layout" (colonne allineate con spazi); codifica (UTF-8 default, UTF-8 con BOM, UTF-16); fine riga LF o CRLF; separatore di pagina (carattere di avanzamento modulo U+000C, riga personalizzata o nessuno); ricongiungimento delle parole sillabate; inclusione del testo delle annotazioni. 3. Avvia.

**Formati/strutture:** Testo Unicode ricavato da `/ToUnicode`, codifiche standard e `/ActualText`; ordine dalla struttura logica se il PDF è taggato, altrimenti per colonne e blocchi; esclusi per default il testo fuori dal `/CropBox` e gli artefatti di impaginazione marcati (intestazioni, piè, numeri di pagina); incluso il testo OCR invisibile.

**Casi limite:** pagina senza testo → OCR secondo CNV-02, altrimenti pagina vuota con avviso; legature e caratteri di compatibilità → espansi; testo da destra a sinistra → ordine logico Unicode; glifi senza mappatura Unicode (font simbolici, Type3) → U+FFFD con conteggio nel report; tabelle → celle separate da tabulazione in modalità ordine di lettura; decine di migliaia di pagine → scrittura in streaming.

**Criteri di accettazione:**
- Dato un PDF a due colonne, quando lo si esporta in ordine di lettura, allora per ogni pagina il testo della colonna sinistra precede interamente quello della destra.
- Dato un PDF in cui la parola «fine» è composta con la legatura U+FB01, quando lo si esporta, allora il TXT contiene «fine» in caratteri semplici.
- Dato un PDF di 3 pagine e separatore "avanzamento modulo", quando lo si esporta, allora il file è UTF-8 valido e contiene esattamente 2 caratteri U+000C.
- Dato un PDF taggato in cui l'ordine della struttura differisce da quello geometrico, quando lo si esporta, allora i paragrafi seguono l'ordine degli elementi di struttura.

**Priorità:** P0

### CNV-10 — PDF → RTF

**Descrizione:** Esporta in Rich Text Format conservando testo formattato, paragrafi, tabelle e immagini, per la massima compatibilità con editor di testo anche datati.

**Flusso:** 1. Sceglie RTF in CNV-01 e la modalità di layout. 2. Avvia; l'analisi della pagina è la stessa di CNV-03.

**Formati/strutture:** RTF 1.9.1; caratteri non ASCII come sequenze Unicode (`\uN`) con carattere alternativo; tabelle dei font e dei colori; immagini incorporate in PNG o JPEG; tabelle come righe di tabella RTF; hyperlink come campi.

**Casi limite:** elementi non rappresentabili (forme vettoriali complesse, testo ruotato) → immagini; molte immagini ad alta risoluzione → ricampionamento secondo CNV-01 per evitare file di centinaia di MB; font rari o non decodificabili e tabelle complesse → come CNV-03; scansione → CNV-02.

**Criteri di accettazione:**
- Dato un PDF con testo in grassetto, corsivo, lettere accentate e greche, quando lo si esporta in RTF, allora il file viene analizzato senza errori, la similarità testuale con il PDF è ≥ 99% e le parole in grassetto e corsivo mantengono lo stile.
- Dato un PDF con 2 immagini, quando lo si esporta, allora l'RTF contiene 2 immagini incorporate con le stesse proporzioni (±1%).

**Priorità:** P2

### CNV-11 — PDF → HTML

**Descrizione:** Converte il PDF in pagine web, come file unico autonomo o come cartella con risorse, in modalità fedele (aspetto della pagina) o scorrevole (struttura semantica adattiva).

**Flusso:** 1. Sceglie HTML in CNV-01. 2. Opzioni: fedele o scorrevole; file unico (immagini e font incorporati come dati) o cartella `{nome}_files`; un file HTML per pagina o documento unico; font web incorporati; sommario dai segnalibri. 3. Avvia.

**Formati/strutture:** HTML5 con CSS. Fedele: un contenitore per pagina in scala, testo selezionabile posizionato in assoluto, grafica in SVG o immagine. Scorrevole: `h1`–`h6` dai titoli (struttura logica o gerarchia dei corpi), `p`, `ul`/`ol`, `table`, `figure` con `alt` dal testo alternativo (`/Alt`) se presente; attributo `lang` da `/Lang`; link esterni e interni (ancore); titolo e autore nei metadati della pagina.

**Casi limite:** font con licenza che vieta l'incorporamento → non incorporato, sostituito da font generico con avviso; testo in font Type3 o senza mappatura → immagine o OCR; pagina scansionata → immagine più testo OCR; tabelle complesse → `table` con celle unite (`rowspan`/`colspan`) o, se non ricostruibili, testo posizionato; documento molto grande → più file HTML con navigazione; caratteri riservati (`<`, `&`) → codificati correttamente.

**Criteri di accettazione:**
- Dato un PDF di 5 pagine, quando lo si esporta in modalità scorrevole, allora l'HTML supera un validatore HTML5 senza errori e la similarità testuale con il PDF è ≥ 99%.
- Dato un PDF taggato con 3 titoli di livello 1, quando lo si esporta in modalità scorrevole, allora l'HTML contiene esattamente 3 elementi `h1` con lo stesso testo.
- Dato un export in modalità fedele, quando ogni pagina HTML viene resa da un browser senza interfaccia grafica alla stessa risoluzione, allora la differenza visiva rispetto al rendering PDF è ≤ 5% per pagina.
- Dato un export "file unico", quando il file viene spostato in un'altra cartella e aperto senza rete, allora tutte le immagini sono visualizzate (nessun riferimento esterno).

**Priorità:** P1

### CNV-12 — PDF → XML

**Descrizione:** Esporta contenuto e struttura del documento in un XML documentato, per elaborazioni automatiche, indicizzazione e archiviazione.

**Flusso:** 1. Sceglie XML in CNV-01. 2. Opzioni: dettaglio (blocchi, righe o parole con coordinate); struttura logica se presente; metadati, annotazioni e valori dei campi modulo; immagini come file collegati o omesse. 3. Avvia.

**Formati/strutture:** XML 1.0 in UTF-8, valido rispetto a uno schema XSD pubblicato e versionato con il prodotto: documento (metadati Info e XMP) → pagina (numero, etichetta, dimensioni, rotazione) → blocco, tabella (righe e celle) o immagine → riga → parola, con riquadro in coordinate PDF, font, corpo e colore; albero di struttura (`/StructTreeRoot`) con i tipi di elemento standard; annotazioni con tipo, autore, data e testo. Variante facoltativa "foglio di calcolo XML" per le sole tabelle.

**Casi limite:** PDF non taggato → struttura dedotta e marcata come tale con un attributo; glifi non decodificabili → come CNV-09; caratteri non ammessi in XML 1.0 (controlli) → rimossi e contati nel report; documenti enormi → scrittura in streaming.

**Criteri di accettazione:**
- Dato un qualsiasi PDF del corpus, quando lo si esporta, allora l'XML è ben formato e valido rispetto allo schema pubblicato.
- Dato un PDF di 2 pagine con dettaglio "parole", quando lo si esporta, allora la concatenazione delle parole di ogni pagina ha similarità testuale ≥ 99% con l'export TXT (CNV-09) e ogni riquadro di parola è contenuto nel riquadro della propria pagina.
- Dato un PDF con 4 annotazioni, quando lo si esporta con le annotazioni, allora l'XML contiene 4 elementi annotazione con tipo e testo corretti.

**Priorità:** P2

### CNV-13 — PDF → EPUB

**Descrizione:** Crea un e-book EPUB dal PDF, scorrevole (testo che si adatta allo schermo del lettore) o a layout fisso (pagine identiche all'originale).

**Flusso:** 1. Sceglie EPUB in CNV-01. 2. Opzioni: scorrevole o layout fisso; divisione in capitoli (segnalibri di primo livello, titoli di livello 1 oppure ogni N pagine); copertina (pagina 1 resa come immagine o immagine scelta); rimozione di intestazioni, piè e numeri di pagina ripetuti; metadati (titolo, autore, lingua, identificatore) precompilati dal documento e modificabili. 3. Avvia.

**Formati/strutture:** EPUB 3.3 (W3C): contenitore, pacchetto OPF, documento di navigazione dai segnalibri (`/Outlines`) o dai titoli, contenuti XHTML con CSS, immagini, font incorporati se la licenza lo consente; layout fisso con resa pre-paginata e un documento XHTML per pagina.

**Casi limite:** nessun segnalibro né titolo riconoscibile → capitoli ogni N pagine (default 10) con avviso; tabelle complesse → tabelle XHTML o, se non ricostruibili, immagini; formule e grafici → immagini con testo alternativo; note a piè di pagina → a fine capitolo con link di andata e ritorno; font rari o non decodificabili → come CNV-03; scansione → CNV-02 oppure layout fisso a immagini; lingua non determinabile → chiesta all'utente perché obbligatoria nei metadati.

**Criteri di accettazione:**
- Dato un PDF con 8 segnalibri di primo livello, quando lo si converte in EPUB scorrevole, allora il file supera un validatore EPUB 3 indipendente senza errori e la navigazione contiene 8 voci di primo livello con gli stessi titoli.
- Dato un PDF con il piè di pagina ripetuto «Pagina N di M», quando lo si converte con la rimozione attiva, allora quella sequenza non compare nel testo dei capitoli.
- Dato un PDF di 20 pagine convertito a layout fisso, quando si ispeziona il pacchetto, allora contiene 20 documenti di contenuto nell'ordine delle pagine.

**Priorità:** P2

### CNV-14 — Conversione in PDF/A (1b, 2b, 3b)

**Descrizione:** Converte il documento in PDF/A per l'archiviazione a lungo termine e la conservazione a norma: corregge automaticamente ciò che lo standard vieta e verifica il risultato.

**Flusso:** 1. "Converti › PDF/A" o "Salva come › PDF/A"; scelta del livello: PDF/A-1b, 2b (default) o 3b. 2. Analisi preliminare con l'elenco dei problemi e la correzione prevista per ciascuno. 3. Conferma; il motore applica le correzioni e riscrive integralmente il file in una nuova copia. 4. Verifica automatica di conformità del risultato e report (esito, correzioni applicate, errori residui). 5. Se la conformità non è raggiunta, l'utente sceglie se annullare o salvare un PDF ordinario privo della dichiarazione di conformità.

**Formati/strutture:** ISO 19005-1 (livello 1b, base PDF 1.4), ISO 19005-2 (2b, base ISO 32000-1), ISO 19005-3 (3b, allegati di qualsiasi tipo). Correzioni: incorporamento in subset di tutti i font, inclusi i 14 standard; `/OutputIntents` con profilo ICC (sRGB di default, CMYK se il documento usa CMYK dipendente dal dispositivo); metadati XMP con `pdfaid:part` e `pdfaid:conformance`, sincronizzati con il dizionario Info; `/ID` nel trailer; rimozione di cifratura, JavaScript, azioni vietate (`Launch`, `Sound`, `Movie`, `ResetForm`, `ImportData`), contenuti esterni (Reference XObject) e XFA; LZW → Flate; aspetti `/AP` generati dove richiesti, flag `Print` impostato e flag di occultamento rimossi sulle annotazioni. Per 1b inoltre: trasparenze appiattite, JPEG 2000 ricodificato, livelli e allegati rimossi. Per 2b: allegati ammessi solo se conformi a PDF/A. Per 3b: allegati con `/AF` e `/AFRelationship`.

**Casi limite:** PDF cifrato → richiesta della password (quella proprietario se i permessi vietano la modifica), cifratura rimossa; font assente dal sistema o con licenza che vieta l'incorporamento → sostituzione con font metricamente compatibile e avviso, oppure, su scelta, rasterizzazione delle sole pagine coinvolte con OCR invisibile; PDF firmato → avviso che la firma sarà invalidata, con opzione (livello 3b) di allegare l'originale firmato come file associato con relazione `Source`; documento già dichiarato PDF/A → verifica e, se non conforme, proposta di correzione; modulo XFA dinamico → conversione possibile solo previo appiattimento, segnalato; PDF scansionato → ammesso (livello b) senza OCR obbligatorio; file enormi → elaborazione come in CNV-01.

**Criteri di accettazione:**
- Dato un PDF con font standard non incorporati, trasparenze e JavaScript, quando lo si converte in PDF/A-2b, allora il risultato supera un validatore indipendente ISO 19005-2 con zero errori, tutti i font risultano incorporati e non resta alcuna azione JavaScript.
- Dato un PDF con font standard non incorporati e trasparenze, quando lo si converte in PDF/A-1b, allora il validatore ISO 19005-1 riporta zero errori e la differenza visiva rispetto all'originale è ≤ 2% per pagina.
- Dato un PDF/A-3b prodotto allegando un file XML, quando si ispeziona il file, allora l'allegato è referenziato da `/AF` con `/AFRelationship` valorizzato e il validatore ISO 19005-3 non riporta errori.
- Dato un documento la cui conformità non è raggiungibile, quando l'utente sceglie "salva comunque", allora i metadati XMP del file salvato non contengono l'identificazione `pdfaid`.
- Dato un PDF cifrato con password di apertura, quando lo si converte fornendo la password, allora il risultato non contiene il dizionario `/Encrypt`.

**Priorità:** P0

### CNV-15 — Altri profili ISO: PDF/A livelli u e a, PDF/A-4, PDF/X, PDF/E

**Descrizione:** Estende la conversione di CNV-14 ai livelli di conformità u (testo Unicode) e a (accessibile), a PDF/A-4, alla stampa professionale (PDF/X) e alla documentazione tecnica (PDF/E); permette inoltre di rimuovere una dichiarazione di conformità.

**Flusso:** 1. Sceglie il profilo: PDF/A-2u, 3u, 1a, 2a, 3a, 4, 4f, 4e; PDF/X-1a, X-3, X-4; PDF/E-1. 2. Per PDF/X sceglie il profilo ICC della condizione di stampa (output intent) e la conversione dei colori, con anteprima prima/dopo. 3. Analisi, correzione, verifica e report come in CNV-14. 4. "Rimuovi dichiarazione di conformità" elimina le identificazioni dai metadati XMP lasciando invariato il contenuto.

**Formati/strutture:** Livello u: mappatura Unicode di tutto il testo (`/ToUnicode`); livello a: PDF taggato con struttura logica, `/Lang` e testi alternativi (richiede le funzioni di accessibilità); PDF/A-4 (ISO 19005-4, base PDF 2.0); PDF/X (ISO 15930: X-1a, X-3, X-4): output intent obbligatorio, `/TrimBox` o `/ArtBox` su ogni pagina, solo CMYK e tinte piatte in X-1a, trasparenza vietata in X-1a e X-3 e ammessa in X-4; PDF/E-1 (ISO 24517-1).

**Casi limite:** testo senza mappatura Unicode ricostruibile (livello u) → OCR della parte coinvolta o esito negativo motivato; documento non taggato (livello a) → tag automatici con invito alla revisione manuale; `/TrimBox` mancante (PDF/X) → impostato uguale al `/CropBox` con avviso; conversione RGB → CMYK → avviso sulla variazione dei colori; documento cifrato o firmato → come CNV-14.

**Criteri di accettazione:**
- Dato un PDF RGB con trasparenze, quando lo si converte in PDF/X-4 con un profilo CMYK, allora un validatore indipendente PDF/X-4 non riporta errori e ogni pagina ha `/TrimBox`.
- Dato un PDF con testo interamente mappabile, quando lo si converte in PDF/A-2u, allora il validatore ISO 19005-2 per il livello u non riporta errori.
- Dato un PDF/A-2b, quando si usa "Rimuovi dichiarazione di conformità", allora i metadati XMP non contengono `pdfaid` e i flussi di contenuto delle pagine sono byte-identici all'originale.

**Priorità:** P2

### CNV-16 — Conversione avanzata assistita da IA

**Descrizione:** Modalità facoltativa per DOCX, XLSX e PPTX che usa un modello di analisi del layout basato su apprendimento automatico per i documenti difficili (tabelle senza bordi, impaginazioni complesse, moduli, scansioni scadenti).

**Flusso:** 1. In CNV-01 attiva "Conversione avanzata (IA)". 2. Se il modello è installato localmente lo usa direttamente; se è un servizio remoto, chiede un consenso esplicito che indica quali pagine verranno inviate e a quale servizio configurato. 3. Converte; il report indica le pagine elaborate in modalità avanzata e quelle ricadute nella modalità standard.

**Formati/strutture:** Stesse uscite di CNV-03, CNV-04 e CNV-06; il modello produce una descrizione strutturata (blocchi, ordine di lettura, tabelle con celle) che alimenta gli stessi generatori di formato. Servizio remoto configurabile dall'utente o dall'amministratore; nessun invio di dati predefinito.

**Casi limite:** rete assente o servizio non configurato → ricaduta automatica sulla modalità standard con avviso; documento cifrato o riservato → invio remoto disattivato salvo conferma esplicita; limiti di pagine o di dimensione del servizio → suddivisione in blocchi o ricaduta locale; risposta del servizio non valida → pagina convertita con il motore standard.

**Criteri di accettazione:**
- Dato un modello solo remoto e la rete disattivata, quando si avvia una conversione avanzata, allora la conversione termina con il motore standard, il report lo segnala e non viene registrata alcuna connessione in uscita.
- Dato il corpus di riferimento di tabelle senza bordi, quando lo si converte in XLSX in modalità avanzata e standard, allora l'accuratezza a livello di cella della modalità avanzata è maggiore o uguale a quella standard.
- Dato un servizio remoto configurato, quando l'utente nega il consenso, allora nessun dato viene inviato e la conversione prosegue in modalità standard.

**Priorità:** P2

### CNV-17 — Conversione in blocco di più file

**Descrizione:** Converte molti PDF in un'unica operazione verso uno qualsiasi dei formati di CNV-03…CNV-15, con coda di lavoro, elaborazione parallela e report finale.

**Flusso:** 1. "Elaborazione in blocco › Converti": aggiunta di file, cartelle (con opzione ricorsiva) o trascinamento; riordino e rimozione delle voci. 2. Scelta del formato e delle opzioni (come CNV-01, applicate a tutti i file) e della destinazione (accanto all'originale o cartella scelta, con "mantieni la struttura delle sottocartelle"). 3. Avvio: per ogni file stato (in attesa, in corso, completato, con avvisi, errore) e avanzamento complessivo. 4. Fine: riepilogo con conteggi, report esportabile (CSV o testo) e accesso ai file prodotti.

**Formati/strutture:** Ingresso PDF; i file non PDF aggiunti sono segnalati e saltati (la creazione in blocco è CNV-28). Numero di processi paralleli configurabile (default: core disponibili − 1). Impostazioni del lotto salvabili come profilo riutilizzabile.

**Casi limite:** file cifrato → messo in attesa con richiesta di password non bloccante (inserisci o salta), gli altri proseguono; file danneggiato → riparazione in memoria (CNV-32) o errore registrato; nomi in collisione → suffisso numerico; stesso file aggiunto due volte → duplicato ignorato; disco pieno → lotto sospeso, ripresa possibile dopo aver liberato spazio; annullamento → i file completati restano, quello in corso è scartato senza residui; migliaia di file → l'elenco resta reattivo.

**Criteri di accettazione:**
- Dati 50 PDF di cui 2 danneggiati in modo irreparabile e 1 cifrato, quando si avvia la conversione in DOCX e si salta il file cifrato, allora vengono prodotti 47 DOCX e il report elenca i 2 errori con la causa e il file saltato.
- Data una cartella con 3 sottocartelle e l'opzione "mantieni la struttura", quando la si converte, allora l'uscita riproduce la stessa gerarchia con ogni file convertito nella sottocartella corrispondente.
- Dato un lotto di 100 PDF su hardware di riferimento con 4 processi paralleli, quando lo si converte in TXT, allora il tempo totale è inferiore al 40% di quello dell'elaborazione sequenziale.
- Dato un lotto annullato a metà, quando si ispeziona la cartella di uscita, allora contiene solo file completi e apribili e nessun file temporaneo.

**Priorità:** P1

### CNV-18 — Crea PDF da file Office

**Descrizione:** Crea un PDF da documenti di videoscrittura, fogli di calcolo, presentazioni e diagrammi, conservando link, struttura e font.

**Flusso:** 1. "Crea › Da file", trascinamento del file nella finestra o apertura diretta. 2. Opzioni: segnalibri dai titoli, PDF taggato, commenti come annotazioni, incorporamento dei font, qualità delle immagini, uscita PDF/A (CNV-14); per i fogli di calcolo: tutti i fogli, foglio attivo o aree di stampa, adattamento alla larghezza della pagina; per le presentazioni: diapositive nascoste, pagine delle note. 3. Converte e apre il PDF come nuovo documento non salvato.

**Formati/strutture:** Ingresso: DOCX, DOC, ODT, RTF; XLSX, XLS, ODS; PPTX, PPT, ODP; VSDX, VSD; CAJ; XPS/OXPS. Uscita: PDF 1.7 (o PDF/A) con font incorporati in subset, hyperlink come azioni URI, rimandi interni e sommario come link interni, titoli come `/Outlines`, proprietà del documento in Info e XMP. Il componente di conversione è locale: se non è installato l'app lo segnala e guida all'installazione, senza ricorrere a servizi online.

**Casi limite:** file protetto da password → richiesta della password, errore chiaro se errata; macro → mai eseguite; font del documento non installati → sostituti metricamente compatibili e avviso (possibili variazioni di impaginazione); campi (date, indici) → aggiornati prima della conversione se l'opzione è attiva; foglio di calcolo enorme → stima delle pagine e conferma oltre una soglia (default 500); revisioni tracciate → stampa dello stato finale senza marcature, salvo opzione; file danneggiato o formato non riconosciuto → errore senza crash.

**Criteri di accettazione:**
- Dato un DOCX di prova con 3 titoli di livello 1, 2 hyperlink e un'immagine, quando si crea il PDF, allora contiene 3 segnalibri con i testi dei titoli, 2 annotazioni link con gli stessi URL e tutti i font incorporati.
- Dato un XLSX con 3 fogli e l'opzione "tutti i fogli", quando si crea il PDF, allora le pagine dei 3 fogli compaiono nell'ordine dei fogli e il testo di ciascuno è estraibile.
- Dato un PPTX di 10 diapositive di cui 1 nascosta, quando si crea il PDF con le opzioni predefinite, allora il PDF ha 9 pagine.
- Dato un file protetto e una password errata, quando la si conferma, allora l'app mostra un errore e consente di riprovare senza chiudersi.
- Dati un file VSDX e un file CAJ di prova, quando si crea il PDF da ciascuno, allora il numero di pagine coincide con quello atteso dalla fixture e il testo è estraibile.

**Priorità:** P1

### CNV-19 — Crea PDF da immagini

**Descrizione:** Crea un PDF da una o più immagini, una per pagina, controllando ordine, formato e orientamento delle pagine, adattamento e qualità.

**Flusso:** 1. "Crea › Da immagini" o trascinamento di immagini; la finestra mostra le miniature. 2. Ordinamento per nome (ordine naturale), per data, come selezionato o trascinando; rotazione delle singole immagini. 3. Formato pagina: "come l'immagine" (dimensione dalla risoluzione dell'immagine, 72 dpi se assente), formato standard (A3, A4, A5, Letter, Legal) o personalizzato; orientamento automatico, verticale o orizzontale; adattamento (contieni mantenendo le proporzioni, riempi ritagliando, dimensione reale centrata); margini. 4. Qualità: originale (nessuna ricompressione) o ricompressa con ppi massimo. 5. Opzioni: OCR per rendere il testo ricercabile, uscita PDF/A. 6. Crea il nuovo documento, una pagina per immagine.

**Formati/strutture:** Obbligatori: PNG, JPEG, TIFF (anche multipagina: una pagina per fotogramma), BMP, GIF (primo fotogramma), WebP. Estesi: HEIC/HEIF, JPEG 2000, formati raw di fotocamera, SVG (come grafica vettoriale). JPEG inserito senza ricompressione (`DCTDecode`, stream identico al file); PNG senza perdita (`FlateDecode` con predittori); trasparenza come `/SMask`; profilo ICC dell'immagine come spazio `/ICCBased`; orientamento EXIF applicato.

**Casi limite:** immagine danneggiata o formato non supportato → esclusa con avviso, le altre proseguono; JPEG CMYK con codifica invertita → colori corretti; immagini oltre 100 megapixel → elaborate senza caricarle interamente in memoria; pagina risultante oltre 200 pollici per lato → `/UserUnit` o riduzione, a scelta; immagini a 16 bit per canale → conservate; GIF animata → primo fotogramma, o un fotogramma per pagina su opzione; risoluzione assente o incoerente nei metadati → 72 dpi o valore scelto.

**Criteri di accettazione:**
- Dati 3 JPEG (orizzontale, verticale, quadrato), formato A4 e orientamento automatico, quando si crea il PDF, allora ha 3 pagine A4, la prima orizzontale e le altre verticali, e ogni immagine è interamente contenuta e centrata nell'area interna ai margini.
- Dato un JPEG con qualità "originale", quando si crea il PDF, allora lo stream dell'immagine nel PDF è byte-identico al file JPEG sorgente.
- Dato un JPEG con orientamento EXIF 6, quando si crea il PDF, allora la pagina mostra l'immagine nel verso corretto (differenza visiva ≤ 1% rispetto al riferimento).
- Dati un TIFF di 4 pagine e un PNG con trasparenza, quando si crea il PDF, allora risultano 5 pagine e l'immagine PNG ha una maschera `/SMask`.
- Dati `img2.png` e `img10.png` con ordinamento per nome, quando si crea il PDF, allora `img2` precede `img10`.

**Priorità:** P0

### CNV-20 — Crea PDF da testo, Markdown, RTF e CSV

**Descrizione:** Impagina file di testo in un PDF con testo selezionabile, rispettando la formattazione del formato di origine.

**Flusso:** 1. "Crea › Da file di testo" o apertura diretta del file. 2. Opzioni: formato e margini della pagina, font e corpo (monospaziato di default per TXT), a capo automatico, ampiezza delle tabulazioni, numeri di riga, intestazione e piè con nome file e numero di pagina; codifica rilevata automaticamente e modificabile. 3. Crea il nuovo documento.

**Formati/strutture:** TXT (UTF-8, UTF-16 con BOM, codifiche tradizionali a byte singolo); Markdown (CommonMark con tabelle, elenchi di attività e barrato): titoli → segnalibri, link attivi, blocchi di codice monospaziati, immagini locali incorporate; RTF: stili di carattere e paragrafo, tabelle, immagini; CSV: tabella con intestazione ripetuta su ogni pagina, delimitatore rilevato, orientamento orizzontale proposto per tabelle larghe. Font incorporati in subset con `/ToUnicode`; font di ripiego automatici per sistema di scrittura (CJK, arabo ed ebraico da destra a sinistra, devanagari, emoji).

**Casi limite:** codifica ambigua → anteprima con la codifica rilevata e possibilità di cambiarla; righe lunghissime senza spazi → a capo forzato; caratteri non coperti da alcun font installato → avviso con l'elenco dei caratteri; file di decine di MB → impaginazione progressiva senza bloccare l'interfaccia; immagini remote nel Markdown → scaricate solo con consenso, altrimenti segnaposto.

**Criteri di accettazione:**
- Dato un TXT UTF-8 con testo italiano, greco, cinese e arabo, quando si crea il PDF, allora il testo estratto coincide con il sorgente a meno degli spazi e nessun carattere è reso con il glifo mancante (`.notdef`).
- Dato un Markdown con 3 titoli di livello 1, il primo dei quali con 2 sottotitoli di livello 2, quando si crea il PDF, allora i segnalibri hanno 3 voci di primo livello e la prima ha 2 figli.
- Dato un CSV di 500 righe, quando si crea il PDF, allora la riga di intestazione compare in cima a ogni pagina e tutte le 500 righe sono presenti.
- Dato un file in codifica ISO-8859-1 con lettere accentate, quando la codifica è rilevata automaticamente, allora le lettere accentate risultano corrette nel PDF.

**Priorità:** P1

### CNV-21 — Crea PDF da HTML e pagine web

**Descrizione:** Converte un file HTML locale o una pagina web (URL) in PDF con link attivi e testo selezionabile.

**Flusso:** 1. "Crea › Da pagina web" indicando un file HTML o un URL. 2. Opzioni: formato, margini, orientamento e scala; stampa degli sfondi; pagina unica continua o paginata; intestazione e piè (titolo, URL, data, numero di pagina); segnalibri dai titoli; esecuzione degli script della pagina con tempo massimo di caricamento. 3. Crea; per un URL mostra l'avanzamento del caricamento.

**Formati/strutture:** HTML5 e CSS (comprese le regole per la stampa `@media print` e `@page`), immagini, SVG, font web; link esterni come azioni URI, ancore interne come link a destinazioni; titoli `h1`–`h6` come `/Outlines`; titolo e lingua nei metadati.

**Casi limite:** URL irraggiungibile, errore HTTP o certificato non valido → messaggio esplicito, nessun PDF; pagina che richiede autenticazione → acquisita come la vede un visitatore anonimo (nessuna sessione del browser dell'utente); risorse mancanti in un file locale → segnaposto e avviso; pagina remota che tenta di leggere file locali → bloccata; scorrimento infinito o caricamento che non termina → acquisizione allo scadere del tempo massimo; pagina unica oltre il limite di dimensione della pagina PDF → `/UserUnit` o suddivisione; codifica dichiarata errata → rilevamento automatico.

**Criteri di accettazione:**
- Dato un file HTML locale con 2 link esterni, 1 ancora interna e 3 titoli `h1`, quando si crea il PDF, allora contiene 2 link URI corretti, 1 link interno che porta alla posizione dell'ancora e 3 segnalibri.
- Dato un URL che risponde con errore 404, quando si avvia la creazione, allora l'app mostra l'errore e non crea alcun documento.
- Data l'opzione "pagina unica continua", quando si converte una pagina web lunga, allora il PDF ha 1 pagina che contiene tutto il contenuto senza tagli.

**Priorità:** P1

### CNV-22 — Crea PDF dagli appunti

**Descrizione:** Crea un nuovo PDF dal contenuto degli appunti di sistema: immagine, testo formattato, testo semplice, file o pagine PDF copiati.

**Flusso:** 1. "Crea › Dagli appunti", attivo solo se gli appunti contengono un formato supportato. 2. Riconoscimento automatico del contenuto, in ordine di priorità: pagine PDF o file copiati, immagine, testo formattato (HTML, RTF), testo semplice; disponibile anche "come testo semplice". 3. Creazione di un nuovo documento non salvato.

**Formati/strutture:** Immagine (PNG, JPEG, BMP) → pagina delle dimensioni dell'immagine, inserita senza perdita; testo formattato → come CNV-20 e CNV-21; testo semplice → come TXT in CNV-20; file copiati → CNV-18, CNV-19 o CNV-20 secondo il tipo, oppure apertura diretta se PDF; pagine copiate da un documento Foliant → nuovo documento con quelle pagine. L'incolla come pagine dentro un documento esistente appartiene alla gestione pagine.

**Casi limite:** appunti vuoti o formato non supportato → comando disabilitato con suggerimento; immagine molto grande → come CNV-19; appunti modificati durante la creazione → si usa l'istantanea letta all'avvio.

**Criteri di accettazione:**
- Dato negli appunti uno screenshot di 1920 × 1080 pixel, quando si sceglie "Crea dagli appunti", allora si apre un nuovo documento non salvato di 1 pagina con l'immagine a 1920 × 1080 pixel senza compressione con perdita.
- Dato negli appunti testo formattato con una parola in grassetto e un link, quando si crea il PDF, allora la parola è in grassetto e il link è un'annotazione link con lo stesso URL.
- Dati gli appunti vuoti, quando si apre il menu Crea, allora la voce "Dagli appunti" è disabilitata.

**Priorità:** P1

### CNV-23 — Crea PDF vuoto

**Descrizione:** Crea un documento nuovo con pagine bianche o con uno stile carta (righe, quadretti, punti), come base per appunti, disegni o moduli.

**Flusso:** 1. "File › Nuovo › PDF vuoto". 2. Scelta di formato (A0–A6, B4, B5, Letter, Legal, Tabloid o personalizzato in mm, pollici o punti), orientamento, numero di pagine, colore di fondo e stile carta (bianca, righe, quadretti, punti, con passo e colore configurabili). 3. Creazione del documento non salvato, pronto per modifica e annotazioni.

**Formati/strutture:** Pagine con `/MediaBox` del formato scelto; lo stile carta è contenuto di pagina marcato come artefatto di sfondo (non estratto come testo e rimovibile con gli strumenti per lo sfondo), condiviso tra le pagine come unico Form XObject; formati oltre 200 pollici per lato tramite `/UserUnit`.

**Casi limite:** dimensioni personalizzate fuori limite (lato sotto 3 pt o oltre il massimo consentito) → errore in linea con i limiti ammessi; molte pagine (es. 1 000) → creazione immediata grazie allo sfondo condiviso; chiusura senza salvare → richiesta di salvataggio.

**Criteri di accettazione:**
- Dati il formato A4 verticale, 3 pagine e lo stile quadretti con passo 5 mm, quando si crea il documento, allora ha 3 pagine di 595 × 842 pt (±1), il reticolo ha passo 14,17 pt (±0,1) e l'estrazione del testo restituisce una stringa vuota.
- Dato un formato personalizzato di 6000 × 1000 mm, quando si crea il documento, allora le dimensioni effettive della pagina, considerando `/UserUnit`, sono 6000 × 1000 mm (±1 mm).
- Dato un PDF vuoto appena creato, quando lo si chiude, allora l'app chiede se salvarlo.

**Priorità:** P0

### CNV-24 — Crea PDF da scanner

**Descrizione:** Acquisisce documenti da scanner piani o con alimentatore automatico (anche fronte/retro) direttamente in PDF, con preset, miglioramento dell'immagine e OCR facoltativo, in un nuovo documento o in quello aperto.

**Flusso:** 1. "Crea › Da scanner": elenco degli scanner locali e di rete rilevati, compresi quelli che non richiedono driver dedicati. 2. Scelta di un preset o delle impostazioni: sorgente (piano, alimentatore, alimentatore fronte/retro), risoluzione (75–1200 dpi), colore (colore, grigi, bianco e nero), formato carta o rilevamento automatico, luminosità e contrasto. 3. Anteprima facoltativa, acquisizione; dopo ogni pagina dal piano: "Acquisisci un'altra pagina" o "Fine". 4. Post-elaborazione facoltativa: raddrizzamento, ritaglio automatico, rimozione delle pagine vuote, OCR, fronte/retro manuale (unisce una pila di fronti e una di retro acquisita in ordine inverso). 5. Modalità "documento d'identità": acquisisce fronte e retro e li compone sulla stessa pagina A4 in scala reale. 6. Crea il PDF oppure inserisce le pagine nel documento aperto dopo la pagina corrente.

**Formati/strutture:** Bianco e nero con compressione bi-livello senza perdita (CCITT G4 o JBIG2 senza perdita); grigi e colore in JPEG con la qualità del preset o senza perdita; risoluzione delle immagini coerente con il formato della pagina; preset salvabili ed esportabili.

**Casi limite:** nessuno scanner rilevato → messaggio con indicazioni (collegamento, rete, permessi), nessun blocco; disconnessione o inceppamento durante l'acquisizione → pagine già acquisite conservate e ripresa possibile; alimentatore vuoto → avviso; retro bianco → rimosso se l'opzione è attiva, con soglia configurabile; acquisizioni molto grandi (600 dpi a colori, centinaia di pagine) → salvataggio progressivo su disco senza esaurire la memoria.

**Criteri di accettazione:**
- Dato un dispositivo di scansione simulato che fornisce 5 pagine dall'alimentatore, quando si acquisisce in bianco e nero a 300 dpi, allora il PDF ha 5 pagine con immagini a 1 bit, compressione bi-livello senza perdita e risoluzione effettiva di 300 dpi (±1).
- Dato il dispositivo simulato in fronte/retro con 3 fogli di cui un retro bianco e l'opzione "rimuovi pagine vuote" attiva, quando si acquisisce, allora il PDF ha 5 pagine.
- Data la modalità documento d'identità, quando si acquisiscono fronte e retro, allora il PDF ha 1 pagina A4 con due immagini di 85,6 × 54 mm (±0,5 mm), il fronte sopra e il retro sotto.
- Dato il dispositivo simulato che si disconnette dopo la terza pagina, quando l'errore viene segnalato, allora le 3 pagine acquisite restano disponibili e salvabili.

**Priorità:** P1

### CNV-25 — Crea PDF da cattura dello schermo

**Descrizione:** Cattura lo schermo intero, una finestra o un'area e la trasforma in una pagina PDF, in un nuovo documento o accodandola a catture precedenti.

**Flusso:** 1. "Crea › Da cattura schermo": scelta tra schermo, finestra o area, con ritardo facoltativo di alcuni secondi. 2. La cattura passa dal servizio di cattura del desktop, che può chiedere il consenso dell'utente. 3. Anteprima con ritaglio; "Aggiungi un'altra cattura" o "Crea PDF".

**Formati/strutture:** Immagine senza perdita (`FlateDecode`); dimensione della pagina dalla risoluzione della cattura (96 dpi di default, modificabile); supporto delle sessioni grafiche X11 e Wayland, di più schermi e della scala HiDPI.

**Casi limite:** permesso di cattura negato → messaggio esplicito e nessun documento; schermo HiDPI → cattura alla risoluzione fisica; cattura annullata → nessun effetto.

**Criteri di accettazione:**
- Data una cattura di un'area di 800 × 600 pixel, quando si crea il PDF, allora la pagina contiene un'immagine di 800 × 600 pixel compressa senza perdita.
- Dato il permesso di cattura negato dal sistema, quando si avvia la funzione, allora l'app mostra un messaggio, non crea documenti e resta utilizzabile.

**Priorità:** P2

### CNV-26 — Crea PDF da fotocamera o da foto di documenti

**Descrizione:** Equivalente desktop della scansione con lo smartphone: acquisisce documenti dalla webcam o da foto importate, rileva i bordi del foglio, corregge la prospettiva e migliora la leggibilità.

**Flusso:** 1. "Crea › Da fotocamera" (webcam) o "Da foto di documenti" (file immagine). 2. Rilevamento automatico dei quattro angoli del foglio, regolabili a mano. 3. Correzione prospettica e filtro (originale, documento a colori, grigi, bianco e nero, lavagna). 4. Ripetizione per altre pagine, poi creazione del PDF con OCR facoltativo.

**Formati/strutture:** Ingresso: flusso della fotocamera di sistema, JPEG, PNG, HEIC; uscita: pagine nel formato rilevato o scelto (A4, Letter, biglietto da visita, documento d'identità).

**Casi limite:** nessuna fotocamera o permesso negato → solo importazione da file, con spiegazione; bordi non rilevabili (sfondo dello stesso colore del foglio) → angoli sull'intera immagine e regolazione manuale; foto mossa o con riflessi → avviso di qualità.

**Criteri di accettazione:**
- Data una foto di prova di un foglio A4 ripreso in prospettiva, con angoli di riferimento noti, quando si esegue il rilevamento automatico, allora ciascuno dei 4 angoli rilevati dista dal riferimento meno del 2% della diagonale dell'immagine.
- Dati gli angoli confermati e il formato A4, quando si crea il PDF, allora la pagina è A4 e le righe di testo della fixture risultano orizzontali con inclinazione residua ≤ 0,5°.

**Priorità:** P2

### CNV-27 — Stampante virtuale PDF

**Descrizione:** Coda di stampa di sistema che crea un PDF da qualsiasi applicazione dotata di funzione di stampa, con profili di qualità.

**Flusso:** 1. All'installazione, o in seguito dalle preferenze, Foliant registra con il consenso dell'utente una stampante "Foliant PDF" nel sistema di stampa. 2. L'utente stampa da un'applicazione su questa coda scegliendo un profilo (standard, alta qualità, dimensione minima, PDF/A-2b). 3. Il PDF si apre in Foliant come documento non salvato, oppure viene salvato in una cartella configurata con nome da modello.

**Formati/strutture:** PDF con testo vettoriale estraibile e font incorporati in subset; titolo del lavoro di stampa come nome del file e titolo del documento; profili con risoluzione massima e compressione delle immagini.

**Casi limite:** privilegi amministrativi mancanti → istruzioni per l'installazione manuale; Foliant non in esecuzione al momento della stampa → file salvato nella cartella predefinita e notifica; applicazione che stampa solo come immagine → PDF raster, con suggerimento di OCR.

**Criteri di accettazione:**
- Data una pagina di prova con testo stampata da un'applicazione di sistema sulla coda virtuale, quando il lavoro termina, allora viene creato un PDF con testo estraibile e font incorporati.
- Dato il profilo PDF/A-2b, quando si stampa la stessa pagina di prova, allora il PDF supera la validazione ISO 19005-2.

**Priorità:** P2

### CNV-28 — Creazione di PDF in blocco

**Descrizione:** Crea PDF da molti file eterogenei (immagini, documenti Office, testo, HTML) in un'unica operazione, come PDF separati o come unico PDF combinato.

**Flusso:** 1. "Elaborazione in blocco › Crea": aggiunta di file e cartelle. 2. Scelta tra "un PDF per file" (stesso nome base, cartella di uscita) e "un unico PDF" (nell'ordine dell'elenco, con opzione "segnalibri dai nomi dei file"). 3. Opzioni specifiche per tipo (CNV-18…CNV-21) e comuni facoltative (OCR, PDF/A, compressione). 4. Avvio con coda e report come in CNV-17.

**Formati/strutture:** Ingresso: tutti i formati di CNV-18, CNV-19, CNV-20 e CNV-21 (file locali), più PDF esistenti, inclusi così come sono nell'unione.

**Casi limite:** file non supportato o danneggiato → saltato e registrato, gli altri proseguono; documento Office protetto da password → in attesa di password come in CNV-17; immagini di formati diversi in un unico PDF → ogni pagina mantiene il proprio formato, salvo opzione di uniformarlo; migliaia di file → coda elaborata in parallelo senza bloccare l'interfaccia.

**Criteri di accettazione:**
- Dati 10 file (4 immagini, 3 DOCX, 2 TXT, 1 non supportato) e l'opzione "un PDF per file", quando si avvia, allora vengono creati 9 PDF con lo stesso nome base dei sorgenti e il report segnala il file saltato.
- Dati 9 file supportati (4 immagini, 3 DOCX, 2 TXT), l'opzione "un unico PDF" e i segnalibri dai nomi dei file, quando si avvia, allora il PDF ha 9 segnalibri di primo livello, ciascuno diretto alla prima pagina del file corrispondente.

**Priorità:** P1

### CNV-29 — Comprimi PDF

**Descrizione:** Riduce la dimensione del file scegliendo un livello di qualità, con stima preventiva della dimensione e confronto prima/dopo; applicabile a uno o più file.

**Flusso:** 1. "Strumenti › Comprimi" (o "Elaborazione in blocco › Comprimi"). 2. La finestra mostra la dimensione attuale e, per ogni livello, la dimensione stimata, calcolata in background. 3. Anteprima affiancata originale/compresso di un dettaglio ingrandito della pagina scelta. 4. Conferma; salvataggio come nuovo file (suffisso configurabile) o sostituzione dell'originale previa conferma. 5. Risultato: dimensione prima e dopo e riduzione percentuale; in blocco, tabella per file con il totale risparmiato.

**Formati/strutture:** Livelli (ricampionamento solo delle immagini sopra 1,5 volte la risoluzione di destinazione): "Senza perdita" — nessun ricampionamento, ricompressione Flate ottimale, flussi di oggetti e xref compressi, deduplica e rimozione degli oggetti inutilizzati; "Alta" — immagini a colori e grigi a 225 ppi JPEG qualità 85, monocromatiche a 600 ppi; "Media" — 150 ppi qualità 70, mono 300 ppi; "Bassa" — 96 ppi qualità 50, mono 200 ppi. Testo, grafica vettoriale, moduli, segnalibri, link e struttura logica preservati; immagini monocromatiche con compressione bi-livello senza perdita. Parametri di dettaglio in CNV-30.

**Casi limite:** risultato non più piccolo dell'originale → originale mantenuto e messaggio "nessuna riduzione possibile"; PDF firmato → avviso che le firme saranno invalidate e conferma esplicita; PDF cifrato → stessa cifratura nel risultato (password proprietario richiesta se i permessi vietano la modifica); PDF/A → solo compressioni ammesse dal livello (es. niente JPEG 2000 in PDF/A-1) e conformità rivalidata; PDF scansionato → immagini di pagina trattate per tipo (mono senza perdita, colore e grigi ricampionati) e livello OCR conservato; documento di solo testo e vettori → riduzione limitata, stima coerente; file di più GB → elaborazione in streaming e stima per campionamento.

**Criteri di accettazione:**
- Dato un PDF di prova di 20 MB con fotografie a 300 ppi, quando lo si comprime al livello "Media", allora il risultato è ≤ 50% dell'originale, il testo estratto è identico e il numero di operatori di testo è invariato (nessuna pagina rasterizzata).
- Dato il corpus di compressione, quando si confronta la stima con la dimensione effettiva per ciascun livello, allora lo scarto è ≤ 20% in almeno il 90% dei file.
- Dato un PDF già ottimizzato che nessun livello riduce, quando lo si comprime, allora il file originale resta byte-identico e compare il messaggio di nessuna riduzione.
- Dato un PDF/A-2b, quando lo si comprime al livello "Bassa", allora il risultato supera ancora la validazione ISO 19005-2.
- Dati 10 PDF compressi in blocco, quando l'operazione termina, allora la tabella riporta per ogni file dimensione prima e dopo e la riga dei totali è la somma delle righe.
- Dato un PDF di 100 pagine e 50 MB su hardware di riferimento, quando si apre la finestra, allora le stime di tutti i livelli compaiono entro 5 s.

**Priorità:** P0

### CNV-30 — Ottimizzazione avanzata e analisi dello spazio

**Descrizione:** Per utenti esperti: controllo dettagliato di ricampionamento, compressione, font e oggetti da eliminare, preceduto da un'analisi che mostra quanto spazio occupa ogni categoria di contenuto.

**Flusso:** 1. "Strumenti › Ottimizzazione avanzata". 2. "Analisi spazio": tabella e grafico con byte e percentuale per immagini, font, contenuti di pagina, annotazioni, moduli, struttura logica, segnalibri, metadati, allegati, miniature e altro. 3. Impostazioni per sezioni — Immagini: per colore, grigi e mono, metodo di ricampionamento (media, bicubico, sottocampionamento), ppi di destinazione e soglia, compressione (JPEG, JPEG 2000, senza perdita, bi-livello), qualità, conversione in scala di grigi; Font: subset dei font completi, deduplica, rimozione dei font standard incorporati (con avviso); Elimina: miniature, oggetti non referenziati, stream duplicati, metadati, dati privati delle applicazioni, allegati, JavaScript, azioni modulo, commenti, segnalibri, struttura logica, livelli nascosti, impostazioni di stampa e di visualizzazione; Pulizia: compressione degli stream non compressi, flussi di oggetti e xref compressi, rimozione di link e segnalibri non validi; Compatibilità: versione PDF di destinazione. 4. Salvataggio come preset ed esecuzione con confronto come in CNV-29.

**Formati/strutture:** Filtri `FlateDecode`, `DCTDecode`, `JPXDecode`, `CCITTFaxDecode`, `JBIG2Decode` (senza perdita di default); `/ObjStm` e stream xref (PDF 1.5 o successivo); `/Thumb`, `/PieceInfo`, `/Metadata`, `/EmbeddedFiles`, `/StructTreeRoot`, `/OCProperties`.

**Casi limite:** compressione bi-livello con perdita → disattivata di default e, se attivata, accompagnata da un avviso sul rischio di scambio tra caratteri simili; rimozione della struttura logica → avviso di perdita dell'accessibilità; eliminazione di metadati o font in un PDF/A → bloccata, o avviso di perdita della conformità; versione di destinazione inferiore a quella richiesta dal contenuto (es. trasparenze in PDF 1.3) → elenco degli elementi che saranno appiattiti o rimossi; immagini già sotto la soglia → non modificate; PDF cifrato o firmato → come CNV-29.

**Criteri di accettazione:**
- Dato un PDF in cui lo stesso logo è incorporato 50 volte come oggetti distinti, quando si esegue la deduplica, allora il risultato contiene un solo oggetto immagine per il logo e la differenza visiva di ogni pagina rispetto all'originale è 0%.
- Dato un PDF con un oggetto non referenziato inserito nella fixture, quando si esegue "rimuovi oggetti inutilizzati", allora l'oggetto non è presente nel risultato.
- Data un'immagine a 600 ppi con destinazione 150 ppi e soglia 225 ppi, quando si ottimizza, allora l'immagine risultante ha 150 ppi effettivi (±1) alla sua dimensione di visualizzazione.
- Dato un qualsiasi PDF del corpus, quando si esegue l'analisi dello spazio, allora la somma delle categorie coincide con la dimensione del file (±1%).

**Priorità:** P1

### CNV-31 — Linearizzazione per la visualizzazione web veloce

**Descrizione:** Salva il PDF in forma linearizzata, così che la prima pagina compaia subito durante lo scaricamento da web e le altre si carichino a richiesta.

**Flusso:** 1. Opzione "Ottimizza per la visualizzazione web veloce" in "Salva con nome", in CNV-29 e CNV-30 e come preferenza globale per i salvataggi completi. 2. Al salvataggio il file viene riscritto per intero in forma linearizzata. 3. Le proprietà del documento indicano "Visualizzazione web veloce: Sì/No".

**Formati/strutture:** ISO 32000-2, Annex F: dizionario di linearizzazione come primo oggetto del file (`/Linearized`, `/L`, `/H`, `/O`, `/E`, `/N`, `/T`), hint stream, sezione xref della prima pagina in testa, oggetti della prima pagina prima di tutti gli altri.

**Casi limite:** salvataggio incrementale successivo → la linearizzazione decade, le proprietà indicano "No" e l'app propone un salvataggio completo; documento firmato → la riscrittura invaliderebbe le firme, quindi l'opzione richiede conferma esplicita; documento cifrato → linearizzato mantenendo la stessa cifratura; documenti molto grandi → calcolo degli hint senza caricare tutto in memoria; file linearizzato con hint danneggiati → hint ignorati in lettura e ricostruiti al salvataggio.

**Criteri di accettazione:**
- Dato un PDF di 100 pagine salvato con l'opzione attiva, quando lo si analizza con un verificatore di linearizzazione indipendente, allora non risultano errori, il primo oggetto è il dizionario di linearizzazione e il suo `/L` coincide con la lunghezza del file in byte.
- Dato un file linearizzato, quando lo si modifica e lo si salva in modo incrementale, allora le proprietà indicano "Visualizzazione web veloce: No".
- Dato un PDF firmato, quando si attiva l'opzione e non si conferma l'avviso, allora il file non viene riscritto e le firme restano valide.

**Priorità:** P1

### CNV-32 — Ripara PDF danneggiati

**Descrizione:** Apre e recupera PDF danneggiati (tabella dei riferimenti incrociati errata, file troncati, oggetti corrotti) ricostruendone la struttura, e salva una copia valida con il resoconto di quanto recuperato.

**Flusso:** 1. All'apertura di un file con errori strutturali Foliant tenta la ricostruzione in memoria e mostra una barra "File danneggiato, riparato in memoria" con i pulsanti "Salva copia riparata" e "Dettagli". 2. In alternativa "Strumenti › Ripara PDF" su uno o più file, anche senza aprirli. 3. Ricostruzione: scansione di tutti gli oggetti, nuova xref e nuovo trailer, individuazione di catalogo e albero delle pagine, correzione delle lunghezze degli stream, esclusione degli oggetti illeggibili. 4. Salvataggio con riscrittura completa in un nuovo file (suffisso `_riparato`), senza mai modificare l'originale; report con problemi rilevati, correzioni e contenuti persi. 5. Se il file contiene più revisioni e l'ultima è corrotta, proposta di recuperare l'ultima revisione integra.

**Formati/strutture:** ISO 32000-2 §7.5 (struttura del file, xref, trailer, aggiornamenti incrementali) e §7.3.8 (stream). Tolleranza a: byte estranei prima dell'intestazione o dopo `%%EOF`, offset xref errati, `/Length` errato, catene `/Prev` circolari, oggetti duplicati (prevale l'ultima definizione nel file), object stream danneggiati, `/Count` incoerente, `/Root` mancante (catalogo cercato per `/Type /Catalog`, oppure albero delle pagine ricostruito dalle pagine orfane in ordine di file), stringhe con codifica non valida; immagini e font corrotti sostituiti da segnaposto con avviso.

**Casi limite:** file troncato → recuperate tutte le pagine con oggetti completi, le altre elencate come perse; file cifrato danneggiato con dizionario `/Encrypt` integro → riparazione dopo l'inserimento della password; `/Encrypt` o `/ID` persi → riparazione impossibile, messaggio chiaro; file non PDF con estensione .pdf → riconoscimento del tipo reale e proposta di crearne un PDF (CNV-18, CNV-19); file vuoto o illeggibile → errore senza crash; file enormi → ricostruzione in streaming con avanzamento; PDF firmato → avviso che le firme non saranno più verificabili.

**Criteri di accettazione:**
- Dato un PDF valido in cui gli offset della tabella xref sono stati alterati, quando lo si apre, allora tutte le pagine sono visualizzate con differenza visiva 0% rispetto all'originale e la copia riparata ha una xref valida per un verificatore sintattico indipendente.
- Dato un PDF di 10 pagine troncato al 60% della lunghezza, quando lo si ripara, allora sono recuperate tutte le pagine i cui oggetti sono interamente presenti e il report elenca per numero le pagine perse.
- Dato un file JPEG rinominato con estensione .pdf, quando si tenta la riparazione, allora l'app indica che si tratta di un'immagine JPEG e propone di crearne un PDF.
- Dato un file con tre revisioni incrementali di cui l'ultima troncata, quando si sceglie di recuperare la revisione precedente, allora il risultato coincide pagina per pagina con la seconda revisione.
- Dato il corpus di file danneggiati, compresi quelli generati per fuzzing, quando ciascun file viene aperto o riparato, allora l'applicazione non termina in modo anomalo e non supera il limite di memoria configurato.

**Priorità:** P1

### CNV-33 — Appiattisci (flatten)

**Descrizione:** Fonde nel contenuto di pagina annotazioni, campi modulo compilati, firme, filigrane, intestazioni e sfondi, livelli e ritagli, rendendoli parte fissa e non più modificabile del documento; in alternativa rasterizza pagine intere.

**Flusso:** 1. "Strumenti › Appiattisci" e scelta degli elementi: annotazioni (tutte o per tipo, link esclusi di default), campi modulo, firme (con avviso), filigrane, intestazioni e sfondi aggiunti come elementi rimovibili, livelli (nello stato visibile corrente), ritaglio delle pagine (reso definitivo). 2. Ambito: tutto il documento o intervallo di pagine. 3. Resa "come a schermo" o "come in stampa" (secondo i flag di visibilità e di stampa). 4. Facoltativo: "Rasterizza le pagine" a una risoluzione scelta, con OCR per mantenere il testo ricercabile. 5. Applicazione; l'operazione è annullabile finché il documento non viene salvato e il salvataggio successivo è completo (non incrementale), così le versioni precedenti non restano nel file.

**Formati/strutture:** Aspetti `/AP /N` delle annotazioni e dei widget (generati se mancanti) inseriti come Form XObject nel `/Contents` tramite `Do`; annotazioni rimosse da `/Annots`, campi da `/AcroForm /Fields` (dizionario `/AcroForm` eliminato se vuoto); livelli: contenuto visibile spostato nel contenuto base, contenuto nascosto eliminato, `/OCProperties` rimosso; ritaglio: `/MediaBox` = `/CropBox`, contenuto interamente esterno eliminato e immagini parzialmente esterne ritagliate; struttura logica aggiornata (contenuto appiattito marcato come artefatto o collegato alla struttura).

**Casi limite:** annotazioni senza aspetto → aspetto generato prima dell'appiattimento; campi con font non incorporato nell'aspetto → aspetto rigenerato con font sostitutivo incorporato; note e popup → si appiattisce l'icona e il testo della nota va perso, con avviso; campi firma firmati → l'aspetto diventa contenuto statico e la firma non è più verificabile; modulo XFA dinamico → appiattimento solo se il modulo è rappresentabile, altrimenti messaggio di impossibilità; annotazioni nascoste o non visualizzabili → escluse "come a schermo", incluse "come in stampa" se stampabili; PDF cifrato con modifica vietata → richiesta della password proprietario; PDF/A → risultato rivalidato; pagine rasterizzate → aumento di dimensione segnalato e testo non selezionabile senza OCR.

**Criteri di accettazione:**
- Dato un PDF con 3 evidenziazioni, 1 timbro e un modulo con 5 campi compilati, quando si appiattisce tutto, allora il risultato non contiene annotazioni di markup né campi modulo e la differenza visiva di ogni pagina rispetto all'originale reso con le annotazioni è ≤ 0,5%.
- Dato un PDF con 2 link e 1 commento, quando lo si appiattisce con le impostazioni predefinite, allora i 2 link restano attivi e il commento non è più un'annotazione.
- Data una pagina con `/CropBox` più piccolo del `/MediaBox` e testo fuori dall'area visibile, quando si appiattisce il ritaglio, allora quel testo non è più estraibile e il `/MediaBox` coincide con il precedente `/CropBox`.
- Dato un documento con un livello nascosto contenente testo, quando si appiattiscono i livelli, allora quel testo non è più presente e il documento non ha `/OCProperties`.
- Data la rasterizzazione a 200 dpi con OCR attivo, quando si appiattisce, allora ogni pagina contiene un'immagine a 200 dpi e il testo resta ricercabile.

**Priorità:** P1


## 7. OCR e PDF scannerizzati

Questa sezione specifica il riconoscimento ottico dei caratteri (OCR) con cui Foliant rende ricercabili, copiabili e soprattutto **modificabili** i PDF ottenuti da scansione. È l'area prioritaria per il cliente: il risultato chiave è OCR-06 (PDF modificabile). Tutto il riconoscimento avviene in locale, senza rete.

**Convenzioni di verifica (valgono per tutti i criteri di accettazione della sezione):**

- **CR-OCR (corpus di riferimento):** PDF di prova generati rasterizzando pagine A4 con testo noto in italiano e in inglese (font con grazie, senza grazie e monospaziato; corpi 9–14 pt; impaginazione a una e a due colonne; una tabella con bordi e una senza), a 300, 200, 150 e 100 dpi, in bianco e nero, scala di grigi e colore. Varianti: inclinazione da ±0,5° a ±12°, contenuto ruotato di 90/180/270°, rumore granulare, basso contrasto, fondo ingiallito, pagine miste (testo vettoriale più immagine contenente testo). Per ogni pagina sono noti testo atteso, riquadri di parole e righe, corpo e stile.
- **Accuratezza caratteri:** 1 − (distanza di edit tra testo ottenuto e testo atteso ÷ lunghezza del testo atteso), dopo normalizzazione di spazi, interruzioni di riga e forma Unicode (NFC).
- **Testo estraibile:** testo restituito dalla copia/estrazione di Foliant e, per conferma, da un estrattore indipendente conforme a ISO 32000.
- **Pagina solo-immagine:** pagina il cui contenuto visibile è costituito da immagini raster e che produce meno di 10 caratteri di testo estraibile.
- **IoU:** area dell'intersezione ÷ area dell'unione di due riquadri.
- **Macchina di riferimento:** PC x86-64 con 4 core, 8 GB di RAM, SSD, senza accelerazione grafica dedicata (usata solo per i criteri di tempo e di memoria).

### OCR-01 — Rilevamento automatico delle pagine senza testo e proposta di OCR all'apertura

**Descrizione:** All'apertura di un documento Foliant analizza in background ogni pagina e la classifica come *con testo*, *con livello OCR* (testo invisibile già sovrapposto a una scansione), *solo-immagine*, *mista* (testo estraibile più immagini estese che contengono testo), *testo non estraibile* (testo presente ma privo di mappatura Unicode affidabile: la copia produce caratteri illeggibili) o *vuota*. Se trova pagine solo-immagine, miste o con testo non estraibile, mostra una proposta non bloccante di eseguire l'OCR. La proposta compare anche quando l'utente tenta di selezionare o modificare il testo di una pagina solo-immagine. Rilevamento e proposta sono regolabili nelle preferenze (attivo/disattivo, soglie, "non chiedere più per questo documento").

**Flusso:** 1. L'utente apre un PDF; la prima pagina viene mostrata senza attendere l'analisi. 2. In background Foliant esamina gli oggetti di ogni pagina (testo e relativa modalità di resa, immagini con area coperta e risoluzione effettiva, mappatura Unicode dei font), dando precedenza alle pagine visibili. 3. Se trova pagine da riconoscere, mostra una barra informativa con il conteggio (aggiornato progressivamente) e le azioni *Riconosci testo*, *Dettagli*, *Non ora*, *Non chiedere più per questo documento*. 4. *Dettagli* evidenzia le pagine interessate nel pannello miniature, con categoria e risoluzione stimata. 5. *Riconosci testo* apre le impostazioni (OCR-04) con intervallo = pagine rilevate e lingue predefinite. 6. Se l'utente attiva lo strumento di modifica o di selezione del testo su una pagina solo-immagine, Foliant propone l'OCR in modalità modificabile (OCR-06) per quella pagina o per tutte le pagine rilevate.

**Formati/strutture:** analisi del content stream e dei Form XObject annidati: operatori di testo (`BT`/`ET`, `Tj`/`TJ`) e modalità di resa `Tr` (3 = invisibile, ISO 32000-2 §9.3.6); image XObject e immagini inline (§8.9) con area calcolata tramite la matrice di trasformazione corrente e confrontata con il CropBox; `/ToUnicode` e codifiche dei font (§9.10); `/Rotate`. Soglie predefinite: solo-immagine = immagini ≥ 50% dell'area visibile e < 10 caratteri estraibili; mista = testo estraibile più almeno un'immagine ≥ 20% dell'area in cui si rilevano righe di testo; testo non estraibile = oltre il 30% dei caratteri senza Unicode, in area d'uso privato o di controllo. Preferenze e scelta "non chiedere più" sono salvate nella configurazione utente (chiave: primo elemento di `/ID`, §14.4, o impronta del file), mai nel PDF.

**Casi limite:** *pagine già con testo o con livello OCR* → nessuna proposta; nei Dettagli risultano "testo presente" o "livello OCR presente". *Pagina mista* → proposta di OCR delle sole aree immagine (OCR-14). *Scansione inclinata o ruotata* → la classificazione non dipende dall'orientamento. *Bassa risoluzione* (< 150 dpi effettivi) → nei Dettagli avviso "accuratezza ridotta". *Documento molto lungo* → analisi incrementale e interrompibile che non rallenta apertura e scorrimento; la barra compare alla prima pagina trovata e aggiorna il conteggio. *Lingue predefinite non installate* → la proposta lo indica e rimanda a OCR-03. *Pagina vuota* → ignorata. *Testo convertito in tracciati vettoriali* → considerato solo se l'utente abilita "includi grafica vettoriale" (predefinito: no, per evitare falsi positivi su disegni tecnici). *Permessi che vietano modifica o estrazione* → la proposta avvisa che servirà la password proprietario.

**Criteri di accettazione:**
- Dato un PDF di 10 pagine in cui le pagine 3 e 7 sono solo-immagine e le altre hanno testo vettoriale, quando viene aperto con il rilevamento attivo, allora entro 3 s compare la proposta con "2 pagine" e *Riconosci testo* apre le impostazioni con intervallo "3, 7".
- Dato un PDF nativo digitale oppure un PDF già sottoposto a OCR, quando viene aperto, allora non compare alcuna proposta.
- Dato il rilevamento disattivato nelle preferenze, oppure "Non chiedere più" scelto in precedenza per quel file, quando si apre un PDF solo-immagine, allora la proposta non compare e il comando OCR resta disponibile nel menu.
- Dato un PDF solo-immagine di 2.000 pagine, quando viene aperto, allora il tempo di visualizzazione della prima pagina non supera di oltre il 10% quello misurato con il rilevamento disattivato.
- Dato un PDF il cui testo non ha mappatura Unicode, quando viene aperto, allora le pagine sono classificate "testo non estraibile" e la proposta suggerisce l'OCR forzato (OCR-14).
- Dato una pagina solo-immagine, quando l'utente attiva lo strumento di modifica del testo e fa clic sulla pagina, allora compare la proposta di OCR in modalità modificabile.

**Priorità:** P0

### OCR-02 — Motore OCR locale e offline

**Descrizione:** Il riconoscimento avviene interamente sul computer dell'utente: nessuna connessione di rete, nessun invio di contenuti né telemetria sui documenti, nessun limite di pagine o di utilizzi. Il motore riconosce testo stampato (la scrittura a mano non è garantita) e restituisce per ogni blocco, riga, parola e carattere: testo Unicode, riquadro, linea di base, affidabilità (0–100), corpo, stile (grassetto, corsivo; famiglia con grazie, senza grazie o monospaziata) e colore stimati. Questi dati alimentano tutte le altre funzioni della sezione. A parità di input, impostazioni e versione i risultati sono deterministici. Un errore di riconoscimento su una pagina non deve chiudere l'applicazione né alterare il documento.

**Flusso:** 1. I modelli di italiano e inglese sono installati insieme all'applicazione. 2. All'avvio di un OCR Foliant distribuisce le pagine su lavori paralleli (predefinito: core disponibili meno uno; configurabile nelle preferenze). 3. Ogni pagina viene resa alla risoluzione di lavoro (OCR-08), pre-elaborata e riconosciuta; il risultato strutturato torna all'applicazione. 4. Se il riconoscimento di una pagina fallisce o supera il tempo massimo (predefinito 120 s), la pagina è marcata "non riconosciuta" e l'elaborazione prosegue. 5. La finestra "Informazioni" mostra la versione del motore e dei modelli linguistici.

**Formati/strutture:** ingresso: immagine raster della pagina a 1, 8 o 24 bit con risoluzione nota. Uscita interna: gerarchia pagina → blocco → riga → parola → carattere, con coordinate sia in pixel sia nello spazio utente PDF (conversione tramite la matrice pagina/immagine), affidabilità e attributi tipografici; testo Unicode NFC. Esportabile in formati aperti con coordinate (OCR-07).

**Casi limite:** *assenza di rete* → funzionamento identico, nessun tentativo di connessione. *Scrittura a mano, caratteri decorativi* → riconoscimento tentato, parole marcate a bassa affidabilità. *Scansione inclinata, bassa risoluzione* → normalizzate da OCR-08 prima del riconoscimento. *Lingua non installata* → vedi OCR-03. *Documento molto lungo* → pagine elaborate in flusso: la memoria non cresce con il numero di pagine. *Poca RAM* → il parallelismo si riduce automaticamente per non superare il 50% della memoria libera. *Pagina enorme* (es. A0 a 300 dpi) → riconoscimento a tasselli o a risoluzione ridotta con avviso, senza errori di memoria. *Pagine miste o già con testo* → il motore riceve solo le aree o le pagine stabilite da OCR-14.

**Criteri di accettazione:**
- Dato un computer con tutte le interfacce di rete disattivate, quando si esegue l'OCR in italiano sul CR-OCR, allora l'operazione termina con successo e il monitoraggio di sistema non registra alcun tentativo di connessione in uscita da parte dell'applicazione o dei suoi processi.
- Dato le pagine del CR-OCR a 300 dpi in bianco e nero con corpi 10–12 pt, quando si esegue l'OCR nella lingua corretta, allora l'accuratezza caratteri media è ≥ 98% sia in italiano sia in inglese.
- Dato lo stesso PDF e le stesse impostazioni, quando l'OCR viene eseguito due volte, anche con un grado di parallelismo diverso, allora testo, coordinate e affidabilità prodotti sono identici.
- Dato un insieme di 10 pagine di cui una provoca un errore di riconoscimento (iniettato dal banco di prova), quando si esegue l'OCR, allora le altre 9 pagine sono elaborate, quella pagina risulta "non riconosciuta" nel riepilogo e l'applicazione resta in esecuzione.
- Dato la macchina di riferimento, quando si esegue l'OCR ricercabile di 100 pagine A4 a 300 dpi a una colonna, allora il tempo totale è ≤ 4 minuti; con 1.000 pagine il picco di memoria non supera di oltre il 20% quello misurato con 100 pagine.

**Priorità:** P0

### OCR-03 — Lingue di riconoscimento: selezione multipla e gestione dei pacchetti

**Descrizione:** L'utente sceglie una o più lingue per il riconoscimento, anche contemporaneamente (es. italiano + inglese + tedesco) per documenti multilingue. Italiano e inglese sono preinstallati e non rimovibili. Il catalogo comprende almeno 38 lingue installabili come pacchetti, tra cui le principali lingue europee in alfabeto latino, greco e cirillico e lingue con altre scritture: arabo ed ebraico (da destra a sinistra), cinese semplificato e tradizionale, giapponese e coreano (anche in verticale), hindi. I pacchetti si installano offline da file oppure, solo su azione esplicita dell'utente, da un archivio online; la loro integrità è sempre verificata.

**Flusso:** 1. Nelle impostazioni OCR (OCR-04) o nelle preferenze, l'elenco lingue mostra stato (installata, non installata, aggiornabile), scrittura e dimensione. 2. L'utente seleziona una o più lingue e le ordina: la prima è la lingua principale, usata anche come lingua del documento. 3. Predefinite: italiano + inglese (oppure lingua dell'interfaccia + inglese); se il documento dichiara una lingua, questa viene proposta. 4. Per aggiungere una lingua: *Installa da file* (pacchetto locale) oppure *Scarica* (richiede rete e conferma, mostra la dimensione); la lingua è utilizzabile subito, senza riavvio. 5. *Rimuovi* libera spazio (non disponibile per italiano e inglese). 6. Se il rilevamento della scrittura (OCR-08) trova una scrittura non coperta dalle lingue scelte, Foliant lo segnala e suggerisce la lingua adatta.

**Formati/strutture:** pacchetto lingua = archivio con modelli e dizionario, versione compatibile con il motore e impronta crittografica verificata all'installazione; installato nella cartella dati dell'utente o di sistema (installazioni multiutente). Nel PDF: `/Lang` del catalogo impostato alla lingua principale se assente (ISO 32000-2 §14.9.2); per l'uscita accessibile, `/Lang` sulle parti in altra lingua (OCR-17). Dizionario utente per lingua (OCR-12).

**Casi limite:** *lingua non installata* (es. richiesta da un profilo) → avvio bloccato con messaggio che elenca le lingue mancanti e offre *Installa* o *Rimuovi dalla selezione*; mai ripiego silenzioso su un'altra lingua. *Pacchetto corrotto o incompatibile* → rifiutato con motivo. *Assenza di rete* → *Scarica* disattivato con spiegazione, *Installa da file* disponibile. *Lingua sbagliata* (es. solo inglese su testo italiano) → a fine OCR, se le parole sospette superano il 30%, Foliant suggerisce la lingua probabile. *Più di 3 lingue insieme* → avviso non bloccante di rallentamento. *Scrittura da destra a sinistra* → testo estratto in ordine logico. *Testo verticale* → riconosciuto se il modello verticale è installato, altrimenti ne viene suggerita l'installazione.

**Criteri di accettazione:**
- Dato una nuova installazione senza rete, quando si apre l'elenco lingue, allora italiano e inglese risultano installati e il catalogo elenca almeno 38 lingue.
- Dato una pagina del CR-OCR con testo italiano contenente tutte le vocali accentate (à è é ì ò ù) e una frase inglese, quando si esegue l'OCR con italiano + inglese, allora l'accuratezza caratteri è ≥ 98% e almeno il 98% delle vocali accentate è riconosciuto correttamente.
- Dato un profilo che richiede il tedesco non installato, quando l'utente avvia l'OCR, allora l'operazione non parte e il messaggio indica "tedesco" come mancante, offrendo di installarlo o di toglierlo dalla selezione.
- Dato un pacchetto lingua valido su file locale e nessuna rete, quando l'utente sceglie *Installa da file*, allora la lingua è utilizzabile senza riavvio; dato un pacchetto alterato, allora l'installazione è rifiutata.
- Dato una pagina con testo arabo noto e la lingua araba installata, quando si estrae il testo dopo l'OCR, allora i caratteri risultano in ordine logico con accuratezza ≥ 90%.

**Priorità:** P0


## 8. Moduli

> Sezione in redazione.


## 9. Firma

> **Convenzioni dell'area.** *Firma grafica*: tracciato vettoriale o immagine che riproduce una firma autografa; non ha valore crittografico ed è salvata come annotazione, oppure incorporata nel contenuto della pagina su richiesta. *Firma digitale*: firma crittografica basata su certificato X.509, conforme a ISO 32000-2 §12.8 e ai profili PAdES (ETSI EN 319 142-1). *ID digitale*: certificato con la relativa chiave privata. *Revisione*: stato del file al termine di un salvataggio (iniziale o incrementale). Gli esiti di verifica sono tre — **Valida**, **Non valida**, **Indeterminata** — allineati a TOTAL-PASSED / TOTAL-FAILED / INDETERMINATE di ETSI EN 319 102-1, e non sono mai comunicati con il solo colore. I dati locali dell'area risiedono nella cartella dati dell'utente (es. `$XDG_DATA_HOME/foliant/`), accessibile solo al proprietario. Password e chiavi private non vengono mai scritte su disco, nei log o nei rapporti.

### SIG-01 — Firma disegnata a mano

**Descrizione:** L'utente crea una firma autografa tracciandola con mouse, touchpad, schermo tattile o tavoletta grafica; se il dispositivo fornisce la pressione, lo spessore del tratto varia di conseguenza. La firma si usa subito o si salva in libreria (SIG-04) come firma o come iniziali.

**Flusso:** 1. Strumento Firma › "Crea firma" › scheda "Disegna". 2. L'utente traccia nell'area di disegno (proporzione 3:1, con linea di base guida). 3. Sceglie colore (nero, blu, rosso o personalizzato) e spessore; può annullare l'ultimo tratto o cancellare tutto. 4. Il sistema leviga il tracciato e ritaglia il riquadro al contenuto. 5. "Usa" avvia il posizionamento (SIG-05); la casella "Salva in libreria" (firma/iniziali) è selezionata per default.

**Formati/strutture:** Tracciati memorizzati come curve vettoriali normalizzate (coordinate 0–1, spessore per punto); non si conservano dati dinamici (tempi, velocità). Nel PDF: annotazione `/Stamp` con `/AP /N` (Form XObject di soli tracciati), `/F` Print, `/Subj` "Firma" o "Iniziali", `/Contents` con testo alternativo ("Firma di …") per l'accessibilità.

**Casi limite:** Area vuota o tratto sotto la lunghezza minima → "Usa" disabilitato; tavoletta senza pressione → spessore costante; campionamento molto fitto → decimazione dei punti con errore visivo < 0,5 px al 100%; permessi del documento che vietano le annotazioni → strumento disabilitato con spiegazione e possibilità di inserire la password proprietario; documento firmato digitalmente → avviso SIG-21 prima dell'inserimento.

**Criteri di accettazione:**
- Dato un tracciato di prova generato con eventi di input sintetici, quando l'utente preme "Usa", allora sulla pagina viene creata un'annotazione `/Stamp` con aspetto solo vettoriale il cui riquadro coincide con quello del tracciato (tolleranza 1 pt).
- Dato un tracciato con pressione variabile da 0,1 a 1,0, quando la firma viene generata, allora lo spessore del tratto nell'aspetto cresce in modo monotono con la pressione.
- Dato un elemento disegnato e salvato in libreria, quando si ispezionano i dati su disco, allora non contengono tempi né velocità dei punti.

**Priorità:** P0

### SIG-02 — Firma digitata con stile calligrafico

**Descrizione:** L'utente digita nome o iniziali e sceglie fra almeno 8 stili calligrafici con anteprima in tempo reale; il risultato è convertito in tracciati vettoriali. È anche l'alternativa accessibile da tastiera alla firma disegnata.

**Flusso:** 1. "Crea firma" › scheda "Digita". 2. Inserimento del testo (precompilato con il nome delle preferenze, max 60 caratteri). 3. L'elenco mostra il testo in ciascuno stile; scelta di colore e inclinazione facoltativa. 4. "Usa" / "Salva in libreria" come in SIG-01.

**Formati/strutture:** Font calligrafici distribuiti con l'applicazione con licenza che ne consente la ridistribuzione; i glifi sono convertiti in contorni nello stream di aspetto, quindi nel PDF non viene incorporato alcun font e il testo non è selezionabile (la stringa resta in `/Contents` come descrizione). In libreria: testo, identificativo dello stile e colore, così la firma è rigenerabile.

**Casi limite:** Caratteri non coperti da uno stile (accentati rari, alfabeti non latini) → stile mostrato come non compatibile, con almeno uno stile di riserva a copertura Unicode ampia; testo vuoto o di soli spazi → "Usa" disabilitato; testo lungo → riduzione proporzionale nell'anteprima; file di font mancante (installazione danneggiata) → errore esplicito e uso dello stile di riserva.

**Criteri di accettazione:**
- Dato il testo "Mario Rossi", quando l'utente apre la scheda "Digita", allora compaiono almeno 8 anteprime in stili diversi entro 300 ms.
- Dato un documento con una firma digitata applicata e salvata, quando si analizzano le risorse della pagina e dell'annotazione, allora non risulta alcun font aggiunto e l'aspetto contiene solo tracciati.
- Dato il testo "Łukasz Żółć", quando l'utente apre l'elenco degli stili, allora quelli privi di uno dei glifi risultano non selezionabili con l'indicazione del carattere mancante.

**Priorità:** P0

### SIG-03 — Firma da immagine o scansione con rimozione dello sfondo

**Descrizione:** Importa una firma da file immagine, appunti, scanner o fotocamera, rende trasparente lo sfondo chiaro e ritaglia l'immagine al contenuto.

**Flusso:** 1. "Crea firma" › scheda "Immagine" (file, appunti, scanner, fotocamera). 2. Editor di pulizia: ritaglio, rotazione (passi di 90° e raddrizzamento fine ±15°), cursore "soglia sfondo" con valore automatico iniziale, opzione "solo inchiostro" che ricolora i tratti con un colore uniforme. 3. Anteprima su sfondo a scacchi e sulla pagina. 4. "Usa" / "Salva in libreria".

**Formati/strutture:** Ingresso PNG, JPEG, BMP, TIFF, GIF, WebP, fino a 50 megapixel; profili colore incorporati convertiti in sRGB. Nel PDF: image XObject con `/SMask` (canale alfa), ricampionato a max 300 ppi alla dimensione d'inserimento, compressione senza perdita; per documenti che dichiarano PDF/A-1 (trasparenza vietata) si usa una maschera a 1 bit. Annotazione come in SIG-01. In libreria: PNG con alfa.

**Casi limite:** PNG già trasparente → rimozione dello sfondo disattivata per default; sfondo non uniforme (foto con ombre) → soglia adattiva locale e suggerimento di ritagliare; immagine vuota dopo la soglia → avviso "nessun tratto rilevato"; file corrotto o formato non supportato → errore con il formato rilevato; immagine oltre il limite → rifiuto con indicazione della dimensione; nessuno scanner o fotocamera disponibile → voce disabilitata con motivo.

**Criteri di accettazione:**
- Dato un file immagine di prova con firma nera su sfondo bianco e rumore di ±10 livelli, quando si applica la soglia automatica, allora almeno il 99% dei pixel di sfondo ha alfa 0 e almeno il 95% dei pixel di tratto resta opaco.
- Dato un documento con una firma da immagine salvata, quando si ispeziona l'immagine nel file, allora ha una maschera `/SMask` (o a 1 bit se il documento dichiara PDF/A-1) e risoluzione effettiva non superiore a 300 ppi.
- Dato un file di formato non supportato, quando l'utente tenta l'importazione, allora un errore indica il formato rilevato e non viene creata alcuna firma.

**Priorità:** P0

### SIG-04 — Libreria locale di firme e iniziali

**Descrizione:** Raccolta locale di firme e iniziali riutilizzabili (disegnate, digitate, da immagine), con nome, tipo e ordine personalizzabili, disponibile in ogni documento senza account né connessione.

**Flusso:** 1. Aprendo lo strumento Firma, il menu mostra gli elementi salvati in due gruppi (Firme, Iniziali) con miniatura. 2. Un clic su un elemento avvia il posizionamento (SIG-05). 3. "Gestisci libreria": crea, rinomina, riordina, imposta predefinito, duplica, elimina (con conferma). 4. Opzione "Proteggi la libreria": l'uso richiede lo sblocco tramite portachiavi di sistema o password.

**Formati/strutture:** Sottocartella `signatures/` con manifest JSON versionato (id, nome, tipo, origine, colore, data, impronta SHA-256) e un file per elemento (SVG per tracciati, PNG per immagini, JSON per digitate); permessi 0700/0600; con protezione attiva i file sono cifrati (AES-256-GCM) con chiave custodita nel portachiavi di sistema o derivata dalla password. Massimo 100 elementi per tipo.

**Casi limite:** Manifest corrotto → ripristino dall'ultima copia di sicurezza automatica con elenco degli elementi non recuperabili; file di un elemento mancante → elemento "non disponibile" con opzione di rimozione; portachiavi non disponibile → richiesta di password; due istanze dell'applicazione → scrittura atomica con blocco e ricarica in caso di conflitto; disco pieno → errore, la libreria precedente resta integra.

**Criteri di accettazione:**
- Dato un elemento salvato in libreria, quando l'applicazione viene chiusa e riaperta senza rete, allora l'elemento è disponibile con nome, tipo e anteprima identici.
- Dato un profilo con libreria protetta, quando si leggono i file su disco senza sbloccarla, allora nessun file contiene i dati della firma in chiaro.
- Dato un manifest volutamente corrotto, quando l'applicazione si avvia, allora la libreria viene ripristinata dalla copia di sicurezza e l'utente riceve una notifica.

**Priorità:** P0

### SIG-05 — Posizionamento e ridimensionamento della firma grafica

**Descrizione:** Inserisce una firma o delle iniziali sulla pagina con spostamento, ridimensionamento proporzionale, rotazione e guide di allineamento; l'elemento resta modificabile finché l'utente non sceglie di incorporarlo nel contenuto.

**Flusso:** 1. Dopo la scelta, l'anteprima segue il puntatore; un clic la posiziona (o un trascinamento ne definisce la dimensione). 2. Maniglie di ridimensionamento con proporzioni bloccate (sbloccabili con un tasto modificatore), rotazione a passi di 90° o libera, spostamento con le frecce (1 pt; 10 pt con modificatore). 3. Guide magnetiche verso margini, linee orizzontali di firma e campi vicini. 4. Menu contestuale: duplica, elimina, colore, opacità, "Ripeti su più pagine" (SIG-06), "Incorpora nella pagina". 5. Al salvataggio vale la preferenza "Mantieni come annotazione" (predefinita) o "Incorpora".

**Formati/strutture:** Annotazione `/Stamp` con `/Rect`, `/AP /N`, `/F` Print (più Locked dopo l'incorporazione logica), `/NM`, `/M`, `/T` autore, `/Subj`, `/Contents`. Incorporazione: l'aspetto entra nel contenuto della pagina come Form XObject e l'annotazione viene rimossa. Ogni operazione è annullabile.

**Casi limite:** Pagina ruotata (`/Rotate` 90/180/270) → firma diritta rispetto alla vista, geometria salvata nelle coordinate della pagina; trascinamento oltre i bordi → vincolato al `/CropBox`; lato minimo 8 pt; documento firmato digitalmente → avviso SIG-21 (l'annotazione può essere consentita o meno secondo la certificazione) e "Incorpora" classificata come modifica invalidante; documento PDF/A → aspetto conforme al livello dichiarato.

**Criteri di accettazione:**
- Dato un documento con una pagina A4 con `/Rotate 90`, quando si inserisce una firma e si salva, allora alla riapertura la firma appare diritta nella stessa posizione visiva (tolleranza 1 pt).
- Dato un elemento di firma selezionato, quando l'utente trascina una maniglia d'angolo, allora il rapporto larghezza/altezza resta invariato (tolleranza 0,5%).
- Dato un documento con una firma incorporata e salvato, quando si elencano le annotazioni della pagina, allora non c'è alcuna annotazione di firma e il contenuto della pagina riproduce l'aspetto della firma.

**Priorità:** P0

### SIG-06 — Ripetizione della firma o delle iniziali su più pagine

**Descrizione:** Applica la stessa firma o le stesse iniziali in posizione analoga su un insieme di pagine (es. sigla in calce a ogni pagina di un contratto).

**Flusso:** 1. Su un elemento già posizionato: "Ripeti su più pagine". 2. Scelta delle pagine: tutte, intervallo (es. "1-3,7,10-"), pari, dispari, tutte tranne la corrente. 3. Ancoraggio all'angolo più vicino (modificabile) mantenendo i margini, oppure "stessa posizione assoluta". 4. Anteprima sulle miniature; "Applica" crea le copie come un gruppo unico, annullabile in un solo passo. 5. Il gruppo si sposta o si elimina insieme, oppure si scioglie.

**Formati/strutture:** Un'annotazione per pagina che condivide lo stesso Form XObject di aspetto; identificativo di gruppo in `/NM` con suffisso progressivo; sintassi degli intervalli identica a quella del dialogo di stampa.

**Casi limite:** Pagine di formato o orientamento diverso → posizione calcolata dall'angolo di ancoraggio; se l'elemento non entra, riduzione proporzionale con elenco delle pagine interessate; `/Rotate` diverso fra pagine → elemento diritto su ciascuna; intervallo non valido o oltre l'ultima pagina → errore in linea e "Applica" disabilitato; migliaia di pagine → elaborazione in background con avanzamento e annullamento; documento firmato digitalmente → un solo avviso SIG-21 per l'intera operazione.

**Criteri di accettazione:**
- Dato un documento di 10 pagine e l'intervallo "2-4,8", quando si applica, allora esistono esattamente 4 nuove annotazioni di firma sulle pagine 2, 3, 4 e 8, alla stessa distanza dall'angolo di ancoraggio (tolleranza 1 pt).
- Dato il gruppo appena creato, quando l'utente esegue "Annulla" una volta, allora tutte le copie vengono rimosse.
- Dato un documento con pagine A4 verticali e A3 orizzontali, quando si ripete con ancoraggio in basso a destra, allora su ogni pagina l'elemento è interamente contenuto nel `/CropBox`.

**Priorità:** P1

### SIG-07 — Esportazione, importazione e sincronizzazione della libreria firme

**Descrizione:** Trasferisce la libreria firme fra computer o profili e la mantiene allineata fra più dispositivi tramite una cartella di sincronizzazione scelta dall'utente, senza servizi proprietari.

**Flusso:** 1. "Gestisci libreria" › "Esporta": scelta degli elementi e password obbligatoria per il pacchetto. 2. "Importa": scelta del pacchetto, password, anteprima, gestione dei duplicati (salta / sostituisci / mantieni entrambi). 3. "Sincronizza con cartella": scelta di una cartella (locale, di rete o gestita da un qualsiasi strumento di sincronizzazione) e di una passphrase; da quel momento le modifiche vengono replicate e quelle provenienti da altri dispositivi sono importate all'avvio e periodicamente.

**Formati/strutture:** Pacchetto `.foliant-signatures`: archivio ZIP con manifest JSON e file degli elementi, cifrato con AES-256-GCM e chiave derivata dalla password con una funzione di derivazione resistente agli attacchi a forza bruta (parametri nel manifest in chiaro). Nella cartella sincronizzata: un file cifrato per elemento più un indice con versione logica per elemento; duplicati riconosciuti dall'impronta SHA-256 del contenuto.

**Casi limite:** Password errata → "Password errata o pacchetto danneggiato", nessun elemento importato; pacchetto di versione successiva → importati i soli campi noti, con avviso; stesso elemento modificato su due dispositivi → conservate entrambe le versioni con suffisso "(conflitto)"; cartella non raggiungibile → la libreria locale continua a funzionare e lo stato indica "sincronizzazione sospesa"; eliminazione su un dispositivo → propagata come cancellazione logica recuperabile per 30 giorni.

**Criteri di accettazione:**
- Dato un pacchetto di 5 elementi esportato e importato in un profilo vuoto, quando si confrontano gli elementi, allora nomi, tipi e impronte SHA-256 coincidono.
- Dato un pacchetto e una password sbagliata, quando si importa, allora nessun elemento viene aggiunto e nessun file temporaneo in chiaro resta su disco.
- Dato un secondo profilo collegato alla stessa cartella, quando sul primo si aggiunge una firma, allora il secondo la mostra entro il ciclo di sincronizzazione successivo (≤ 60 s) senza riavvio.

**Priorità:** P2

### SIG-08 — Firma digitale con certificato da file protetto da password

**Descrizione:** Firma il documento con un ID digitale contenuto in un file protetto da password, producendo una firma PAdES visibile (in un'area tracciata o in un campo firma esistente) o invisibile, con motivo, luogo e contatto.

**Flusso:** 1. Strumento "Firma digitale": l'utente traccia un rettangolo, sceglie un campo firma vuoto o seleziona "Invisibile". 2. Sceglie un ID digitale registrato (SIG-14) o "Sfoglia…" un file `.p12`/`.pfx`. 3. Inserisce la password; il sistema mostra titolare, emittente, periodo di validità, usi della chiave ed eventuali avvisi. 4. Indica motivo (elenco modificabile più testo libero), luogo e contatto; sceglie il profilo di aspetto (SIG-09) e il livello (B-B, oppure B-T se è configurata una marca temporale, SIG-10). 5. "Firma e salva": nome file predefinito `<nome>_firmato.pdf`; la sovrascrittura dell'originale richiede conferma. 6. Il documento firmato si riapre con il pannello firme (SIG-18) aggiornato.

**Formati/strutture:** Ingresso PKCS#12 (RFC 7292). Uscita in salvataggio incrementale: campo `/FT /Sig` con widget; dizionario di firma `/Type /Sig` con `/SubFilter /ETSI.CAdES.detached` (opzione di compatibilità `adbe.pkcs7.detached`), `/ByteRange [0 a b c]` che copre tutto il file tranne il valore di `/Contents`, `/Contents` = CMS SignedData distaccato (RFC 5652) in DER esadecimale con spazio riservato iniziale di 16 KB, ampliato automaticamente se insufficiente; attributi firmati content-type, message-digest e signing-certificate-v2, senza signing-time (l'ora dichiarata è in `/M`); `/Reason`, `/Location`, `/ContactInfo`, `/Name`, `/M`, `/Prop_Build` (nome e versione dell'applicazione). Hash SHA-256 (predefinito), SHA-384, SHA-512; firme RSA PKCS#1 v1.5, RSA-PSS, ECDSA P-256/P-384; catena dei certificati inclusa se presente nel file. `/AcroForm /SigFlags 3`. Firma invisibile: widget con `/Rect [0 0 0 0]`.

**Casi limite:** Password errata → messaggio "Password errata", campo svuotato, nessuna scrittura, ritardo crescente dopo 5 tentativi; certificato scaduto o non ancora valido → firma bloccata con indicazione delle date (un'opzione avanzata permette di procedere solo per test, con conferma esplicita e avviso che la firma risulterà non valida); certificato senza uso chiave digitalSignature/nonRepudiation → blocco con spiegazione; file senza chiave privata → errore; più chiavi nel file → scelta dell'alias; modifiche non salvate → richiesta di salvarle prima, così entrano nella revisione firmata; documento cifrato → firma consentita solo se i permessi lo prevedono (o con password proprietario), mantenendo la stessa cifratura; errore di scrittura o disco pieno → scrittura su file temporaneo e rinomina atomica, quindi la destinazione non viene creata o resta invariata; marca temporale non ottenibile → SIG-10.

**Criteri di accettazione:**
- Dato un documento di prova e un ID digitale di test valido, quando l'utente firma, allora un validatore conforme a ETSI EN 319 102-1 configurato con la CA di test restituisce TOTAL-PASSED e rileva il profilo PAdES B-B.
- Dato un documento non modificato firmato, quando si confrontano i byte, allora il file originale è un prefisso esatto del file firmato e `/ByteRange` copre tutto il file tranne il valore di `/Contents`.
- Dato un ID digitale di test e una password errata, quando si conferma, allora compare "Password errata", il file di destinazione non esiste e nei log non c'è alcun dato della chiave.
- Dato un ID digitale con certificato scaduto, quando l'utente tenta di firmare, allora l'operazione è bloccata e il messaggio riporta la data di scadenza.
- Dato l'uso dell'opzione "Invisibile", quando si firma, allora il widget ha `/Rect` di larghezza e altezza zero e il pannello firme elenca la firma.

**Priorità:** P0

### SIG-09 — Aspetto personalizzabile della firma digitale

**Descrizione:** Profili di aspetto riutilizzabili per le firme digitali visibili: elementi testuali (nome, data, motivo, luogo, nome distinto, etichette), grafica (firma della libreria, logo o immagine), disposizione e caratteri.

**Flusso:** 1. Impostazioni › Firme › "Aspetti", oppure "Nuovo aspetto" nel dialogo di firma. 2. Disposizione: solo grafica, solo testo, grafica a sinistra e testo a destra, nome grande più dettagli. 3. Scelta degli elementi testuali, del formato data (con fuso orario), della lingua delle etichette, di carattere e colore. 4. Grafica: elemento della libreria, immagine importata (con rimozione dello sfondo di SIG-03) o nessuna. 5. Anteprima a grandezza reale con i dati del certificato selezionato; salvataggio con nome; un profilo è predefinito.

**Formati/strutture:** Profili in JSON nella cartella dati. Nel PDF: `/AP /N` del widget come Form XObject statico (font incorporati in sottoinsieme, immagini con trasparenza solo se ammessa dal livello PDF/A dichiarato), `/MK` per bordo e sfondo; nessun sotto-livello di stato dinamico: lo stato di verifica compare solo nell'interfaccia, mai nel documento.

**Casi limite:** Testo che non entra nel riquadro → corpo ridotto fino a 6 pt, poi troncamento con puntini e avviso in anteprima; nome in alfabeto non latino → scelta automatica di un font con copertura adeguata; riquadro sotto 40×15 pt → avviso di leggibilità; immagine grande → ricampionata a 300 ppi; pagina ruotata → aspetto diritto rispetto alla vista; profilo che usa un elemento eliminato dalla libreria → profilo segnato "da completare".

**Criteri di accettazione:**
- Dato un profilo "grafica + testo" con nome, data e motivo, quando si firma, allora dal Form XObject di aspetto si estraggono i tre testi ed è presente l'immagine della firma.
- Dato un nome di 80 caratteri e un riquadro di 150×50 pt, quando si genera l'anteprima, allora nessun testo esce dal riquadro e compare l'avviso di troncamento.
- Dato un documento firmato con aspetto generato, quando si analizza il widget, allora `/AP` contiene un solo aspetto normale statico, senza riferimenti a icone o livelli di stato.

**Priorità:** P1

### SIG-10 — Marca temporale da server configurabile

**Descrizione:** Aggiunge alla firma una marca temporale emessa da un server di marcatura temporale (TSA) configurabile, che attesta in modo verificabile l'ora della firma (livello PAdES B-T).

**Flusso:** 1. Impostazioni › Firme › "Server marca temporale": aggiunta di uno o più server con nome, URL (HTTP o HTTPS), eventuali credenziali (utente/password o certificato client), algoritmo di hash, OID di policy facoltativo, timeout (predefinito 15 s); "Verifica" richiede una marca di prova e ne mostra emittente e ora. 2. Un server è predefinito; nel dialogo di firma se ne può scegliere un altro o disattivare la marca. 3. Dopo il calcolo del valore di firma il sistema invia la richiesta, controlla la risposta e incorpora il token. 4. Il pannello firme mostra l'ora "attestata dalla marca temporale" e l'identità della TSA.

**Formati/strutture:** Protocollo RFC 3161: richiesta con impronta del valore di firma, nonce casuale e richiesta del certificato TSA (tipi MIME `application/timestamp-query` e `application/timestamp-reply`); token inserito come attributo non firmato signature-time-stamp del SignerInfo; credenziali conservate nel portachiavi di sistema; uso del proxy di sistema.

**Casi limite:** Server irraggiungibile o timeout → nessun file scritto e scelta fra "Riprova", "Usa un altro server", "Firma senza marca temporale" (con avviso che l'ora sarà quella dichiarata dal computer) e "Annulla"; se un campo firma impone la marca (SIG-19) la firma senza marca non è proposta; risposta di rifiuto → messaggio con il motivo restituito; token incoerente (impronta o nonce diversi, firma della TSA non valida, certificato TSA privo dell'uso timeStamping) → token scartato e trattato come errore; certificato TLS del server non valido → errore, senza possibilità di ignorarlo in silenzio; credenziali errate → nuova richiesta di credenziali; token più grande dello spazio riservato → nuova firma con spazio maggiore, in modo trasparente; orologio locale scostato di oltre 5 minuti dall'ora della TSA → avviso.

**Criteri di accettazione:**
- Dato un server TSA di test raggiungibile, quando si firma, allora il SignerInfo contiene l'attributo signature-time-stamp con impronta uguale a quella del valore di firma e un validatore conforme rileva il livello B-T.
- Dato un URL TSA non raggiungibile, quando si firma, allora entro il timeout configurato compare la scelta fra riprovare, cambiare server, firmare senza marca o annullare e, fino alla scelta, nessun file viene scritto.
- Dato un server TSA di test che restituisce un token con nonce diverso, quando si firma, allora il token è scartato, l'utente è avvisato e la firma non contiene marche.

**Priorità:** P1

### SIG-11 — Firme multiple in sequenza

**Descrizione:** Più firmatari (o lo stesso firmatario in ruoli diversi) firmano lo stesso documento uno dopo l'altro; ogni firma viene aggiunta in un nuovo aggiornamento incrementale senza alterare i byte già firmati, quindi tutte le firme precedenti restano verificabili.

**Flusso:** 1. Il firmatario successivo apre il documento firmato e usa un campo firma vuoto o traccia una nuova area. 2. Il sistema controlla che la firma sia ammessa (livello di certificazione, campi bloccati, flag AppendOnly). 3. La firma si aggiunge come in SIG-08. 4. Il pannello firme mostra tutte le firme in ordine cronologico, ognuna con il proprio esito e la nota "modifiche successive consentite: aggiunta di firma".

**Formati/strutture:** Una revisione per firma (nuova sezione xref e trailer con `/Prev`, dello stesso tipo — tabella classica o stream — della revisione precedente); nelle revisioni nuove si riscrivono solo gli oggetti indispensabili (campo, widget, `/Annots` della pagina, `/AcroForm`); `/SigFlags` con bit AppendOnly impostato.

**Casi limite:** Documento certificato "nessuna modifica" → nuove firme bloccate con spiegazione; campo bloccato da una firma precedente → non firmabile; campo già firmato → non riutilizzabile, si propone un nuovo campo; file danneggiato o con xref ricostruita in apertura → firma rifiutata, perché un salvataggio incrementale non garantirebbe l'integrità delle firme esistenti; operazioni che riscrivono l'intero file (ottimizza, comprimi, linearizza, sanifica) → disattivate o soggette all'avviso SIG-21; firme precedenti già non valide → avviso che la nuova firma non le ripristina.

**Criteri di accettazione:**
- Dato un documento firmato da A, quando B aggiunge una seconda firma, allora entrambe risultano "Valida" e la prima indica "modifiche successive consentite".
- Dato il file prima e dopo la firma di B, quando si confrontano i byte, allora il primo è un prefisso esatto del secondo.
- Dato un documento certificato con livello "nessuna modifica", quando un utente tenta di aggiungere una firma di approvazione, allora l'azione è bloccata con un messaggio che cita il livello di certificazione.

**Priorità:** P0

### SIG-12 — Firma di certificazione con permessi di modifica

**Descrizione:** La prima firma del documento può essere una firma di certificazione con cui l'autore attesta il contenuto e stabilisce quali modifiche restano ammesse: nessuna; solo compilazione dei moduli e firme; compilazione, firme e annotazioni. Facoltativamente blocca campi specifici.

**Flusso:** 1. Strumento "Certifica" (firma visibile o invisibile). 2. Scelta del livello di modifiche consentite, descritto in linguaggio semplice. 3. ID digitale, motivo, aspetto e marca temporale come in SIG-08 e SIG-10. 4. Salvataggio; all'apertura successiva una barra mostra "Certificato da <nome> — consentito: …". 5. Opzione "Blocca questi campi dopo la firma" (disponibile anche per le firme di approvazione).

**Formati/strutture:** Catalogo `/Perms << /DocMDP <rif. al dizionario di firma> >>`; nel dizionario di firma `/Reference [<< /Type /SigRef /TransformMethod /DocMDP /TransformParams << /Type /TransformParams /P 1|2|3 /V /1.2 >> >>]`; blocco campi con `/TransformMethod /FieldMDP` (`/Action /All|/Include|/Exclude`, `/Fields`); ISO 32000-2 §12.8.2.

**Casi limite:** Documento con firme esistenti → "Certifica" disattivato (la certificazione deve essere la prima firma) con spiegazione; livello "nessuna modifica" su un documento con campi da compilare → avviso che non potranno più essere compilati senza invalidare la certificazione; contenuti dinamici (script, azioni all'apertura) → avviso che possono alterare la visualizzazione dopo la certificazione; certificato scaduto o password errata → come SIG-08; server di marca temporale irraggiungibile → come SIG-10.

**Criteri di accettazione:**
- Dato un documento certificato con livello 2 (moduli e firme), quando un utente compila un campo e aggiunge una firma, allora la certificazione resta "Valida" con modifiche consentite.
- Dato un documento certificato con livello 2, quando un utente aggiunge un'evidenziazione, conferma l'avviso SIG-21 e salva, allora la verifica mostra la certificazione "Non valida: modifiche non consentite dopo la certificazione".
- Dato un documento con una firma di approvazione esistente, quando si apre lo strumento "Certifica", allora l'azione è disattivata e ne è indicato il motivo.

**Priorità:** P1

### SIG-13 — Creazione di un ID digitale autofirmato

**Descrizione:** Crea un ID digitale autofirmato per uso interno o di prova, quando l'utente non dispone di un certificato rilasciato da un'autorità; l'interfaccia chiarisce che gli altri non lo riconosceranno automaticamente.

**Flusso:** 1. "ID digitali" › "Crea ID autofirmato". 2. Dati: nome e cognome (obbligatorio), organizzazione, unità, e-mail, paese (codice a 2 lettere). 3. Algoritmo: ECDSA P-256 (predefinito), RSA 3072 o 4096; validità da 1 a 10 anni (predefinita 3). 4. Password del file con indicatore di robustezza e conferma. 5. Scelta del percorso; l'ID viene registrato fra i propri ID (SIG-14). 6. "Esporta certificato pubblico" da consegnare ai destinatari.

**Formati/strutture:** Certificato X.509 v3 (RFC 5280) con emittente uguale al soggetto, numero di serie casuale di almeno 64 bit, `basicConstraints` CA=false, `keyUsage` digitalSignature e nonRepudiation, e-mail in `subjectAltName`, identificatori di chiave; contenitore PKCS#12 cifrato con algoritmi correnti (PBES2 con AES-256 e PBKDF2-HMAC-SHA-256 con un numero di iterazioni elevato), senza algoritmi deprecati; certificato pubblico esportabile in DER (`.cer`) o PEM (`.pem`).

**Casi limite:** Password e conferma diverse → errore, nessun file creato; password debole (meno di 8 caratteri o presente in un elenco di password comuni) → avviso con richiesta di conferma; file già esistente → conferma di sovrascrittura; nome con caratteri non ASCII → codifica UTF8String; sorgente di casualità del sistema non disponibile → operazione rifiutata; cartella non scrivibile → errore con il percorso.

**Criteri di accettazione:**
- Dato un modulo compilato con dati di prova, quando si crea l'ID, allora il file PKCS#12 si apre con la password scelta e il certificato ha soggetto uguale all'emittente, validità pari agli anni scelti e `keyUsage` con digitalSignature.
- Dato un documento firmato con l'ID autofirmato, quando lo si verifica in un profilo che non lo considera attendibile, allora l'esito è "Indeterminata" con motivo "identità del firmatario non attendibile" e integrità confermata.
- Dato lo stesso documento, quando il certificato pubblico esportato viene aggiunto agli attendibili, allora l'esito diventa "Valida".

**Priorità:** P1

### SIG-14 — Gestione degli ID digitali e dei certificati attendibili

**Descrizione:** Gestione unica degli ID digitali dell'utente e dei certificati considerati attendibili (ancore di fiducia) per la verifica delle firme.

**Flusso:** 1. Impostazioni › Firme › "ID digitali": aggiungi (file PKCS#12, dispositivo di SIG-23, creazione di SIG-13), rimuovi dall'elenco senza cancellare il file, dettagli, imposta predefinito. 2. "Certificati attendibili": importa da file, da una firma presente nel documento (con conferma e impronte visibili) o abilita l'archivio di certificati del sistema; per ciascuno, ambito di fiducia (firme, certificazioni, marche temporali). 3. Visualizzatore di certificati: soggetto, emittente, numero di serie, validità, usi della chiave, policy, punti di distribuzione della revoca, impronte SHA-256 e SHA-1, catena. 4. Esportazione di un certificato.

**Formati/strutture:** Elenco ID in JSON (percorso, alias, impronta del certificato; mai password né chiavi); archivio fiduciario locale in PEM con metadati di ambito; importazione da `.cer`, `.crt`, `.pem`, `.der`, `.p7b`/`.p7c`; certificati di sistema in sola lettura e disattivati per default, perché pensati per i siti web e non per le firme.

**Casi limite:** File dell'ID spostato o eliminato → ID "non trovato" con opzione per ricollegarlo; password errata alla registrazione → errore, ID non registrato; certificato scaduto importato fra gli attendibili → accettato con avviso; duplicato → ignorato con notifica; file malformato → errore con il tipo rilevato; rimozione di un'ancora → nuova verifica automatica delle firme nei documenti aperti.

**Criteri di accettazione:**
- Dato un file `.p7b` con 3 certificati, quando si importa, allora l'elenco degli attendibili contiene i 3 certificati con le impronte SHA-256 corrette.
- Dato un ID registrato, quando si ispeziona la configurazione salvata, allora non contiene la password né la chiave privata.
- Dato un documento aperto firmato con un certificato emesso da una CA attendibile, quando la CA viene rimossa dagli attendibili, allora lo stato della firma passa da "Valida" a "Indeterminata" senza riaprire il file.

**Priorità:** P1

### SIG-15 — Verifica di integrità e identità del firmatario

**Descrizione:** All'apertura e su richiesta il sistema verifica ogni firma digitale: integrità dei byte firmati, correttezza crittografica, identità del firmatario tramite la catena di certificati fino a un'ancora attendibile, validità temporale del certificato; ogni firma riceve uno dei tre esiti con la motivazione.

**Flusso:** 1. All'apertura parte la verifica automatica in background (disattivabile), senza bloccare la lettura. 2. Per ogni firma: controllo di `/ByteRange`, calcolo dell'impronta e confronto con message-digest, verifica del valore di firma, controllo di signing-certificate-v2, costruzione della catena (certificati nel CMS, nel `/DSS` e fra gli attendibili), usi della chiave, algoritmi ammessi, ora di riferimento (quella della marca temporale se presente e valida, altrimenti `/M` con avvertenza "ora dichiarata"). 3. Controllo di revoca (SIG-16) e delle modifiche successive (SIG-17). 4. Esito e motivi pubblicati nel pannello (SIG-18).

**Formati/strutture:** SubFilter verificati: `ETSI.CAdES.detached`, `adbe.pkcs7.detached`, `adbe.pkcs7.sha1` e `adbe.x509.rsa_sha1` (deprecati, solo verifica, segnalati), `ETSI.RFC3161` (marche di documento). Algoritmi: RSA (PKCS#1 v1.5, PSS), ECDSA, EdDSA; SHA-2 e SHA-3; MD5 rifiutato, SHA-1 segnalato come debole. Mappatura: Valida = TOTAL-PASSED; Non valida = TOTAL-FAILED (es. HASH_FAILURE, SIG_CRYPTO_FAILURE, REVOKED); Indeterminata = INDETERMINATE (es. NO_CERTIFICATE_CHAIN_FOUND, TRY_LATER, OUT_OF_BOUNDS_NO_POE).

**Casi limite:** Documento modificato dopo la firma senza aggiornamento incrementale (byte firmati alterati) → Non valida, "il documento è stato alterato dopo la firma"; `/ByteRange` che non parte da 0, si sovrappone o lascia scoperto altro oltre a `/Contents` → Non valida per formato; certificato scaduto oggi ma valido all'ora attestata da una marca temporale → Valida con nota; certificato scaduto prima dell'ora attestata → Non valida; certificato scaduto senza prova dell'ora della firma → Indeterminata; catena incompleta o ancora non attendibile → Indeterminata "identità non verificata"; algoritmo non supportato → Indeterminata; CMS malformato → Non valida per formato; centinaia di firme → risultati pubblicati man mano.

**Criteri di accettazione:**
- Dato un insieme di file di prova (firma corretta; byte alterato nell'intervallo firmato; `/ByteRange` manipolato; certificato scaduto prima dell'ora della marca temporale; CA non attendibile), quando vengono aperti, allora gli esiti sono nell'ordine Valida, Non valida, Non valida, Non valida, Indeterminata.
- Dato un documento con 10 firme corrette, quando viene aperto senza rete, allora le verifiche crittografiche terminano entro 3 s su hardware di riferimento e lo scorrimento resta fluido durante la verifica.
- Dato un documento firmato con SHA-1, quando viene verificato, allora l'esito è accompagnato dall'avviso "algoritmo debole".

**Priorità:** P0

### SIG-16 — Controllo della revoca dei certificati

**Descrizione:** Verifica se i certificati della catena (firmatario, intermedi, TSA) risultavano revocati, usando prima le informazioni incorporate nel documento e poi, se l'utente lo consente, i servizi online indicati nei certificati.

**Flusso:** 1. Per ogni certificato non radice si cercano risposte OCSP e CRL incorporate (`/DSS`, attributi della firma). 2. Se mancano o non coprono l'ora di riferimento e la verifica online è abilitata (impostazione: sempre / chiedi / mai; predefinito "sempre", timeout 10 s), si interroga il responder OCSP indicato nel certificato, con ripiego sulla CRL. 3. Si valuta la risposta (firma del responder, intervallo di validità, stato). 4. Il dettaglio della firma mostra per ogni certificato la fonte (incorporata/online), l'ora e l'esito; le risposte sono tenute in cache locale fino alla loro scadenza.

**Formati/strutture:** OCSP (RFC 6960) e CRL (RFC 5280) via HTTP; estensioni Authority Information Access e CRL Distribution Points; responder delegato con uso `id-kp-OCSPSigning` ed estensione no-check; dati incorporati in `/DSS` (`/OCSPs`, `/CRLs`) o nell'attributo firmato di archivio della revoca usato dalle firme legacy.

**Casi limite:** Servizio di revoca irraggiungibile o modalità offline → Indeterminata con motivo "stato di revoca non verificabile" (mai Valida né Non valida per sola mancanza di rete) e pulsante "Riprova"; certificato revocato prima dell'ora della firma → Non valida; revocato dopo l'ora attestata da una marca temporale → Valida con nota; revocato senza prova dell'ora della firma → Indeterminata; risposta OCSP con firma non valida o scaduta → ignorata e si tenta la CRL; CRL molto grande (> 20 MB) → scaricata in background con avanzamento; modalità "mai" → nessuna richiesta di rete, esito basato sui soli dati incorporati.

**Criteri di accettazione:**
- Dato un certificato di test revocato prima della firma e un responder OCSP di test, quando si verifica, allora l'esito è "Non valida" con motivo "certificato revocato" e data di revoca.
- Dato lo stesso documento con la rete disabilitata e nessun dato di revoca incorporato, quando si verifica, allora l'esito è "Indeterminata" con motivo "stato di revoca non verificabile".
- Dato il profilo impostato su "mai", quando si apre un documento firmato, allora non avviene alcuna connessione di rete (verificabile con un proxy di test).

**Priorità:** P1

### SIG-17 — Rilevamento delle modifiche successive alla firma

**Descrizione:** Per ogni firma il sistema individua le revisioni aggiunte dopo di essa, classifica le modifiche come consentite o non consentite (secondo il tipo di firma, la certificazione e i campi bloccati) e consente di vedere il documento esattamente come era al momento della firma. Una firma con modifiche non consentite risulta "Non valida" per il documento corrente, pur restando integra la versione firmata.

**Flusso:** 1. Durante la verifica si confrontano gli oggetti della revisione firmata con quelli delle revisioni successive. 2. Classificazione: firme e campi firma aggiunti, compilazione di campi, annotazioni aggiunte/modificate/eliminate, dati di validazione e marche di documento (sempre consentiti); modifiche al contenuto o alle pagine, ai campi bloccati, alla struttura del modulo, ai metadati. 3. Esito per firma: "Nessuna modifica", "Modifiche consentite" (elenco), "Modifiche non consentite" (elenco con pagina). 4. Azioni: "Visualizza versione firmata" (apre in sola lettura, in una nuova scheda, la revisione corrispondente) e "Confronta con la versione corrente" (strumento di confronto documenti).

**Formati/strutture:** Revisioni individuate dalle catene `/Prev` e dai marcatori di fine file; la revisione firmata termina all'estremo dell'ultimo intervallo di `/ByteRange`; regole DocMDP (P 1/2/3) e FieldMDP; ISO 32000-2 §12.8.2.2.2. Senza certificazione, per le firme di approvazione sono consentite firme, compilazione di campi non bloccati, annotazioni e dati di validazione.

**Casi limite:** Modifiche non incrementali → trattate da SIG-15 come alterazione; revisione successiva che sovrappone contenuti o annotazioni opache al testo firmato o cambia l'aspetto di un campo firmato → "non consentita" anche se tecnicamente è un'annotazione; oggetti riscritti senza differenze sostanziali → ignorati; oltre 100 revisioni → analisi in background con avanzamento; revisione corrotta → Indeterminata per le firme precedenti, con motivo.

**Criteri di accettazione:**
- Dato un documento firmato a cui è stato aggiunto in modo incrementale un commento, quando si verifica, allora la firma è "Valida" con "Modifiche consentite: 1 annotazione aggiunta a pagina N".
- Dato un documento firmato in cui lo stream di contenuto di una pagina è stato modificato in modo incrementale, quando si verifica, allora l'esito è "Non valida" con "Modifiche non consentite: contenuto della pagina N" e la nota "versione firmata integra".
- Dato un documento firmato con modifiche successive, quando l'utente sceglie "Visualizza versione firmata", allora si apre in sola lettura un documento i cui byte coincidono con il prefisso del file fino alla fine della revisione firmata.

**Priorità:** P0

### SIG-18 — Pannello firme e indicatori di stato

**Descrizione:** Pannello laterale con tutte le firme e i campi firma del documento, stato chiaro, dettagli tecnici consultabili e azioni; una barra sopra il documento riassume lo stato complessivo.

**Flusso:** 1. All'apertura di un documento con firme o campi firma compaiono la barra di stato ("Firmato: tutte le firme sono valide" / "Almeno una firma presenta problemi" / "Certificato da … — consentito: …" / "N campi da firmare") e il pannello Firme. 2. Ogni voce mostra: icona e testo dello stato, firmatario, ora (con l'indicazione "attestata da marca temporale" o "dichiarata dal firmatario"), tipo (approvazione, certificazione, marca di documento), livello PAdES, motivo, luogo, contatto, pagina. 3. La voce espansa mostra catena dei certificati, revoca, modifiche successive, algoritmi, campi bloccati. 4. Azioni: vai al campo, visualizza versione firmata, verifica di nuovo, considera attendibile il certificato (con conferma e impronta), esporta rapporto di verifica, copia dettagli. 5. Un clic su una firma nella pagina seleziona la voce corrispondente.

**Formati/strutture:** Rapporto di verifica esportabile in PDF leggibile e in XML strutturato secondo il modello di ETSI TS 119 102-2; stato espresso sempre con icona, testo e colore insieme ed esposto alle tecnologie assistive.

**Casi limite:** Nessuna firma né campo → pannello nascosto, voce di menu disponibile con "Nessuna firma"; verifica in corso → stato "Verifica in corso…" per voce; centinaia di firme → elenco virtualizzato; firma invisibile → voce senza "vai al campo", pagina "—"; documento salvato dopo la verifica → nuova verifica automatica; nomi in alfabeti non latini → resi correttamente.

**Criteri di accettazione:**
- Dato un documento con una firma corretta, una firma alterata e un campo vuoto, quando viene aperto, allora il pannello mostra 3 voci con stati "Valida", "Non valida" e "Da firmare" e la barra indica "Almeno una firma presenta problemi".
- Dato un documento firmato, quando si esporta il rapporto XML di una firma, allora il file è conforme allo schema di ETSI TS 119 102-2 e riporta lo stesso esito mostrato nell'interfaccia.
- Dato un lettore di schermo attivo, quando il focus raggiunge una voce del pannello, allora vengono annunciati lo stato testuale e il nome del firmatario.

**Priorità:** P0

### SIG-19 — Campi firma vuoti

**Descrizione:** Crea campi firma vuoti per preparare il documento alla firma di una o più persone, con proprietà, vincoli per il firmatario e campi da bloccare dopo la firma.

**Flusso:** 1. Strumento "Campo firma" (disponibile anche fra gli strumenti Moduli): l'utente traccia il riquadro. 2. Proprietà: nome univoco, descrizione/destinatario (es. "Firma del cliente"), obbligatorio, aspetto del campo vuoto (bordo, sfondo, etichetta "Firma qui"). 3. Vincoli facoltativi: motivi ammessi, marca temporale obbligatoria con URL, emittenti o policy di certificato ammessi, algoritmi di hash, livello di certificazione da applicare. 4. Azione dopo la firma: blocca tutti i campi, solo quelli indicati o tutti tranne quelli indicati. 5. Duplicazione su altre pagine e ordine di tabulazione.

**Formati/strutture:** Campo `/FT /Sig` con widget (`/Rect`, `/MK`, `/AP` del campo vuoto), `/T`, `/TU`, `/Ff` Required; `/SV` (`/Reasons`, `/TimeStamp << /URL /Ff >>`, `/Cert << /Issuer /OID /KeyUsage >>`, `/DigestMethod`, `/SubFilter`, `/MDP << /P >>`, `/Ff` per i vincoli obbligatori); `/Lock << /Type /SigFieldLock /Action /Fields >>`; `/AcroForm /SigFlags`; ISO 32000-2 §12.7.5.5.

**Casi limite:** Nome duplicato → rinomina automatica con suffisso e avviso; documento già firmato → l'aggiunta del campo è una modifica soggetta a SIG-21 (consentita con sole firme di approvazione, bloccata se la certificazione non la ammette); pagina ruotata → aspetto diritto; vincoli contraddittori (es. algoritmo di hash non ammesso dal SubFilter richiesto) → errore di convalida nel dialogo; campo più piccolo di 20×10 pt → avviso.

**Criteri di accettazione:**
- Dato un campo con marca temporale obbligatoria e un URL, quando si salva e si ispeziona il file, allora il campo ha `/SV /TimeStamp` con `/URL` corretto e `/Ff 1`.
- Dato un campo con azione "blocca i campi Nome e Data", quando un firmatario firma, allora i due campi diventano di sola lettura e la firma contiene il riferimento FieldMDP corrispondente.
- Dato un secondo campo creato con lo stesso nome del primo, quando viene confermato, allora riceve un nome diverso e l'utente viene avvisato.

**Priorità:** P1

### SIG-20 — Invito a firmare

**Descrizione:** Quando un documento contiene campi firma vuoti, l'applicazione invita a firmare, guida da un campo all'altro e applica in automatico i vincoli di ciascun campo.

**Flusso:** 1. All'apertura: barra "Questo documento richiede N firme" con "Firma ora" e "Campo successivo"; i campi vuoti sono evidenziati. 2. Clic su un campo → dialogo di firma digitale già associato al campo, che propone solo gli ID digitali compatibili con i vincoli e solo i motivi ammessi. 3. Senza ID compatibili: "Importa ID", "Crea ID autofirmato" (se i vincoli lo permettono) oppure "Inserisci solo firma grafica" sopra il campo, con avviso che non è una firma digitale e il campo resta da firmare. 4. Dopo la firma il focus passa al campo successivo. 5. Salvataggio con nome, stampa o invio con campi obbligatori non firmati → avviso con l'elenco.

**Formati/strutture:** Lettura dei campi `/FT /Sig` privi di `/V`, di `/Ff` Required, `/SV` e `/Lock`; ordine secondo `/Tabs` della pagina e poi per numero di pagina.

**Casi limite:** Nessun ID che soddisfi i vincoli (es. emittente richiesto) → requisiti spiegati in chiaro e firma digitale disattivata; marca temporale obbligatoria e server irraggiungibile → firma non completata, nessuna scrittura, possibilità di riprovare; certificato scaduto → ID escluso dalla scelta con motivo; password errata → come SIG-08; campi nascosti → esclusi dal conteggio; documento cifrato senza permesso di firma → l'invito è sostituito da una spiegazione.

**Criteri di accettazione:**
- Dato un documento con 3 campi firma vuoti di cui 1 nascosto, quando viene aperto, allora la barra indica "2 firme richieste" e "Campo successivo" porta in sequenza ai due campi visibili.
- Dato un campo che ammette solo certificati di un certo emittente e un utente con due ID di cui uno conforme, quando si apre il dialogo di firma, allora è selezionabile solo l'ID conforme.
- Dato un documento con un campo obbligatorio non firmato, quando l'utente sceglie "Invia per e-mail", allora prima di procedere compare un avviso con il nome del campo.

**Priorità:** P1

### SIG-21 — Avviso di invalidazione nei documenti firmati

**Descrizione:** Evita che l'utente invalidi le firme senza accorgersene: segnala che il documento è firmato, distingue le modifiche consentite da quelle che invalidano, salva sempre in modo incrementale e chiede conferma prima di ogni operazione invalidante.

**Flusso:** 1. All'apertura di un documento firmato compare la barra: "Documento firmato: modificare il contenuto invaliderà le firme". 2. Ogni strumento è classificato rispetto alle firme presenti: consentito (annotazioni senza certificazione o con livello 3; compilazione di moduli non bloccati senza certificazione o con livello 2 o 3; nuove firme) oppure invalidante (modifica di testo o immagini, operazioni sulle pagine, redazione, OCR, filigrane, intestazioni, appiattimento, ottimizzazione, cifratura, conversione PDF/A, modifica dei metadati). 3. Al primo uso di uno strumento invalidante: finestra con le firme che diventeranno non valide e tre scelte — "Annulla", "Lavora su una copia senza firme", "Procedi comunque". 4. Un documento firmato si salva sempre in modo incrementale, anche con "Salva con nome" (copia dei byte originali più le modifiche in coda); le opzioni di riscrittura completa sono disattivate o soggette allo stesso avviso. 5. Con certificazione "nessuna modifica" gli strumenti di modifica sono disattivati con spiegazione e resta disponibile "Lavora su una copia senza firme".

**Formati/strutture:** Classificazione basata su `/Perms /DocMDP`, riferimenti FieldMDP, `/Lock` e `/SigFlags`. "Copia senza firme": nuovo file con salvataggio completo in cui i campi firma restano ma senza valore (`/V` rimosso, aspetto del campo vuoto) e senza `/Perms` e `/DSS`.

**Casi limite:** Operazione invalidante annullata prima del salvataggio → nessuna conseguenza, avviso ritirato; modifica senza effetto visivo (es. metadati) → trattata comunque come invalidante; salvataggio automatico e recupero → mai sul file firmato, sempre in un file di recupero separato; firme già non valide → avviso che le modifiche non le ripristinano; operazioni in batch → i file firmati vengono saltati salvo conferma esplicita.

**Criteri di accettazione:**
- Dato un documento con una sola firma di approvazione valida, quando l'utente aggiunge un'evidenziazione e salva, allora il salvataggio è incrementale e la firma resta "Valida" con "Modifiche consentite".
- Dato lo stesso documento, quando l'utente avvia "Modifica testo", allora prima di qualsiasi modifica compare la finestra con l'elenco delle firme e le tre scelte.
- Dato un documento certificato "nessuna modifica", quando si apre il menu Pagine, allora le operazioni di modifica sono disattivate con il motivo visibile.
- Dato un documento firmato e la scelta "Lavora su una copia senza firme", quando la copia viene salvata, allora il nuovo file non contiene valori di firma e il file originale è invariato byte per byte.

**Priorità:** P0

### SIG-22 — Livelli PAdES, validazione a lungo termine e marca temporale di documento

**Descrizione:** Firme verificabili nel lungo periodo: scelta del livello PAdES (B-B, B-T, B-LT, B-LTA), incorporazione dei dati di validazione (certificati e informazioni di revoca), marca temporale di documento e suo rinnovo; riconoscimento delle firme qualificate tramite le liste di fiducia dell'Unione Europea.

**Flusso:** 1. Nel dialogo di firma o nel profilo si sceglie il livello (predefinito B-T se è configurata una TSA, altrimenti B-B). 2. B-LT: dopo la firma il sistema raccoglie catene e risposte di revoca (firmatario e TSA) e le aggiunge in una revisione successiva. 3. B-LTA: aggiunge inoltre una marca temporale di documento. 4. Su documenti già firmati, dal pannello: "Aggiungi informazioni di verifica" e "Aggiungi marca temporale al documento". 5. Il pannello avvisa quando il certificato della TSA dell'ultima marca d'archivio si avvicina alla scadenza (preavviso predefinito di 90 giorni) e propone il rinnovo. 6. Facoltativo: importazione e aggiornamento periodico delle liste di fiducia UE; il pannello indica "Firma elettronica qualificata" quando le condizioni sono soddisfatte.

**Formati/strutture:** ETSI EN 319 142-1 (livelli baseline); `/DSS << /Certs /OCSPs /CRLs /VRI >>` (chiavi `/VRI` = SHA-1 esadecimale maiuscolo del valore `/Contents` della firma); marca di documento `/Type /DocTimeStamp` con `/SubFilter /ETSI.RFC3161` e `/Contents` = token RFC 3161 calcolato sull'intervallo `/ByteRange`; estensione `/ESIC` dichiarata in `/Extensions`; certificati qualificati riconosciuti tramite QcStatements (ETSI EN 319 412-5) e liste di fiducia (ETSI TS 119 612); regole tecniche nazionali sulla busta PAdES dove applicabili.

**Casi limite:** Server di marca temporale irraggiungibile durante B-LTA → il documento resta salvato a livello B-LT e il pannello propone di aggiungere la marca più tardi; informazioni di revoca non ottenibili → livello raggiunto B-T, con motivo; documento certificato "nessuna modifica" → dati di validazione e marche di documento restano ammessi; certificato risultato revocato durante la raccolta → raccolta completata ma firma segnalata Non valida; lista di fiducia scaduta o con firma non valida → non utilizzata, con avviso.

**Criteri di accettazione:**
- Dato un documento firmato a livello B-LTA in ambiente di test, quando un validatore conforme lo verifica senza accesso alla rete, allora l'esito è TOTAL-PASSED e il livello rilevato è B-LTA.
- Dato un documento firmato a livello B-B, quando l'utente sceglie "Aggiungi informazioni di verifica", allora viene aggiunta una revisione con `/DSS` che contiene la catena e almeno una risposta OCSP o una CRL per ogni certificato non radice, e la firma resta "Valida".
- Dato un documento con marca di documento il cui certificato TSA scade entro il preavviso, quando viene aperto, allora il pannello mostra l'avviso di rinnovo.

**Priorità:** P2

### SIG-23 — Firma con smart card o token crittografico

**Descrizione:** Firma con chiavi custodite in smart card, token USB o altri dispositivi crittografici, compresi i dispositivi per la firma qualificata; la chiave privata non lascia mai il dispositivo.

**Flusso:** 1. Impostazioni › Firme › "Dispositivi": rilevamento dei moduli crittografici registrati nel sistema e aggiunta manuale del percorso di un modulo. 2. All'inserimento del dispositivo i certificati presenti compaiono come ID digitali. 3. Alla firma il PIN viene chiesto in una finestra dedicata (o sul tastierino del lettore, se presente). 4. L'operazione crittografica avviene sul dispositivo; il resto del flusso è come in SIG-08.

**Formati/strutture:** Interfaccia standard PKCS#11 (OASIS); meccanismi RSA PKCS#1 v1.5, RSA-PSS, ECDSA; lettori con tastierino per l'inserimento protetto del PIN; dispositivi rilevati a caldo.

**Casi limite:** PIN errato → messaggio con i tentativi residui se il dispositivo li comunica; PIN bloccato → messaggio esplicito, nessun ulteriore tentativo; dispositivo rimosso durante la firma → operazione annullata senza scrivere il file; modulo non caricabile o incompatibile → errore con il percorso, gli altri dispositivi restano utilizzabili; più certificati sul dispositivo → scelta, proponendo per primi quelli con uso nonRepudiation; certificato scaduto → come SIG-08; server di marca temporale irraggiungibile → come SIG-10.

**Criteri di accettazione:**
- Dato un token software di test esposto tramite PKCS#11, quando l'utente firma con il PIN corretto, allora la firma risulta "Valida" e l'applicazione non ha mai ottenuto il materiale della chiave privata.
- Dato un PIN errato, quando si conferma, allora il messaggio riporta i tentativi residui comunicati dal dispositivo e nessun file viene scritto.
- Dato un dispositivo rimosso a metà operazione (simulato), quando la firma fallisce, allora il file di destinazione non viene creato e compare un messaggio chiaro.

**Priorità:** P2

### SIG-24 — Richiesta di firma a più firmatari

**Descrizione:** Prepara un documento per la firma di più persone, lo invia e tiene traccia di chi ha firmato, senza dipendere da un servizio esterno obbligatorio; in alternativa si collega a un servizio di firma remota configurato dall'utente.

**Flusso:** 1. "Richiedi firme": firmatari (nome, e-mail, ruolo), ordine sequenziale o parallelo, messaggio, scadenza. 2. Assegnazione dei campi (firma, iniziali, data, testo) a ciascun firmatario trascinandoli sulle pagine; i campi firma sono creati come in SIG-19 con il firmatario nella descrizione. 3. Invio: il documento preparato è allegato a un messaggio nel client di posta del sistema (o salvato come pacchetto da consegnare) per il primo firmatario o per tutti. 4. Registro locale delle richieste con lo stato di ogni firmatario; quando un documento restituito viene aperto o importato, il sistema lo riconosce, ne verifica le firme e aggiorna lo stato; in modalità sequenziale propone l'inoltro al firmatario successivo. 5. Promemoria locali alla scadenza ed esportazione di un rapporto di tracciamento (eventi, firmatari, impronte SHA-256 di ogni versione) come file separato. 6. Facoltativo: connettore verso un servizio di firma remota tramite un'interfaccia configurabile (URL e credenziali) con gli stessi stati.

**Formati/strutture:** Documento preparato con campi `/FT /Sig` e campi modulo assegnati; associazione tramite il primo elemento di `/ID` del trailer e l'impronta della revisione preparata; registro e rapporto in JSON e PDF nella cartella dati; allegati di posta tramite i servizi standard del desktop.

**Casi limite:** Documento restituito che non contiene la revisione preparata come prefisso → "versione divergente", non conteggiato come firmato; firma restituita non valida o con modifiche non consentite → stato "da rivedere"; rifiuto comunicato dal firmatario → stato "rifiutato", flusso sospeso; client di posta assente → salvataggio del pacchetto con istruzioni; servizio remoto irraggiungibile → stato invariato e nuovo tentativo programmato.

**Criteri di accettazione:**
- Dato un flusso sequenziale con 2 firmatari, quando si importa il documento firmato dal primo, allora il suo stato diventa "Firmato" e viene proposto l'inoltro al secondo.
- Dato un documento restituito il cui contenuto è stato modificato dopo la preparazione, quando viene importato, allora la richiesta lo segnala come "versione divergente".
- Dato un flusso completato, quando si esporta il rapporto, allora contiene un evento per ogni passaggio con data, firmatario e impronta SHA-256 della versione corrispondente.

**Priorità:** P2

### SIG-25 — Gestione avanzata dei firmatari e criteri organizzativi

**Descrizione:** Funzioni per le organizzazioni: modelli di flusso con ruoli riutilizzabili, rubrica dei firmatari, criteri di firma imposti dall'amministratore e registro locale delle firme apposte.

**Flusso:** 1. "Modelli di richiesta": ruoli (es. "Richiedente", "Responsabile", "Direzione"), ordine e campi associati; al riuso si assegnano le persone ai ruoli. 2. Rubrica dei firmatari (nome, e-mail, organizzazione, certificato pubblico facoltativo) con importazione da CSV o vCard. 3. Criteri distribuiti dall'amministratore tramite un file di configurazione di sistema: marca temporale obbligatoria, livello PAdES minimo, algoritmi ammessi, ancore di fiducia predefinite, divieto di ID autofirmati, divieto di firme solo grafiche nei campi firma, controllo di revoca obbligatorio. 4. Le impostazioni imposte appaiono bloccate con la dicitura "Gestito dalla tua organizzazione". 5. Registro locale, in sola aggiunta, delle firme apposte (documento, impronta, ID usato, ora, esito).

**Formati/strutture:** Modelli e rubrica in JSON; criteri in un file non modificabile dall'utente in una posizione di sistema (es. `/etc/foliant/policies.json`), con precedenza sulle preferenze personali; registro in righe JSON concatenate da impronte (ogni riga contiene l'impronta della precedente) per rilevare manomissioni.

**Casi limite:** File dei criteri malformato → firma non consentita finché il file non viene corretto (comportamento prudente), con avviso e annotazione nel log; marca temporale obbligatoria e server irraggiungibile → firma impossibile, con invito a contattare l'amministratore; ID con certificato scaduto o vietato dai criteri → escluso con motivo; ruolo del modello senza persona assegnata → invio bloccato; registro non scrivibile → firma bloccata se il criterio lo rende obbligatorio, altrimenti avviso; riga del registro alterata → segnalazione di integrità compromessa.

**Criteri di accettazione:**
- Dato un criterio che vieta gli ID autofirmati, quando l'utente tenta di firmare con un ID autofirmato, allora l'operazione è bloccata con la dicitura "Gestito dalla tua organizzazione".
- Dato un registro in cui una riga è stata modificata a mano, quando l'applicazione lo controlla, allora segnala la riga alterata.
- Dato un modello con 3 ruoli, quando l'utente lo applica a un documento e assegna le persone, allora vengono creati i campi e la richiesta nell'ordine definito dal modello.

**Priorità:** P2


## 10. Protezione e sicurezza

> Sezione in redazione.


## 11. Confronto e operazioni in blocco

Convenzioni della sezione. Nel confronto **A** è la versione di riferimento (precedente) e **B** la versione da verificare (nuova): *aggiunta* = contenuto presente solo in B, *eliminazione* = contenuto presente solo in A, *modifica* = tratto di A sostituito da un tratto diverso in B. Le operazioni in blocco BAT-06…BAT-23 usano l'infrastruttura comune BAT-01…BAT-05 (coda, destinazione, nomi, esecuzione, errori e rapporto), che non viene ripetuta nei singoli blocchi. Regole trasversali: i file sorgente non vengono mai modificati, salvo scelta esplicita di sostituzione (BAT-02); ogni scrittura su disco è atomica; le password non compaiono mai in rapporti, registri o file di sequenza; le soglie di tempo dei criteri si misurano sull'hardware di riferimento definito nel piano di test.

### CMP-01 — Avvio del confronto e scelta delle versioni
**Descrizione:** L'utente sceglie i due documenti da confrontare (A e B), eventualmente limitati a intervalli di pagine, e il tipo di confronto (automatico, solo testo, solo visivo). Il calcolo avviene in background e produce una *sessione di confronto* aperta in una scheda dedicata, senza modificare i documenti.
**Flusso:**
1. L'utente avvia "Confronta documenti" dal menu Strumenti o dal menu contestuale di una scheda; il documento attivo è proposto come A.
2. Per ciascun lato sceglie il file tra schede aperte, file recenti, selettore di file o trascinamento; vede miniatura, numero di pagine e intervallo da confrontare (tutte / da–a).
3. Con "Scambia" può invertire A e B.
4. Sceglie la modalità: Automatica (testo dove esiste testo estraibile, visivo altrove; predefinita), Solo testo, Solo visivo; può aprire le opzioni (CMP-02, CMP-04, CMP-08, CMP-12).
5. Avvia: compaiono barra di avanzamento (pagine elaborate/totali) e pulsante Annulla; il resto dell'applicazione resta utilizzabile.
6. Al termine si apre la scheda "A ↔ B" con vista affiancata (CMP-05), elenco differenze (CMP-07) e riepilogo dei conteggi per tipo.
**Formati/strutture:** ingresso PDF 1.0–2.0, inclusi PDF/A e documenti cifrati. Identità di ciascun lato: percorso, impronta SHA-256 del file, identificatore del trailer (/ID). Per documenti aperti con modifiche non salvate si confronta lo stato in memoria. Durante il calcolo i documenti sono usati in sola lettura.
**Casi limite:**
- Stesso file, o file con impronta identica, su entrambi i lati → risultato immediato "nessuna differenza", senza calcolo.
- Password di apertura errata o richiesta annullata → il confronto non parte; nessun file viene toccato.
- Documento che vieta l'estrazione del contenuto (permesso /P, bit 5) e password dei permessi non fornita → confronto testuale non disponibile; vengono proposti il solo confronto visivo o l'inserimento della password dei permessi.
- File danneggiato → tentativo di riparazione in memoria; se fallisce, messaggio con la causa e nessuna sessione creata.
- File non PDF → rifiutato con l'indicazione di convertirlo prima (Area Conversione/Creazione).
- Documento con modifiche non salvate → si confronta lo stato corrente; il lato è etichettato "(non salvato)".
- Annullamento → nessuna sessione parziale viene creata.
**Criteri di accettazione:**
- Dato due PDF selezionati come A e B, quando l'utente preme "Scambia" e avvia il confronto, allora le differenze riportate in precedenza come aggiunte risultano eliminazioni e viceversa.
- Dato un PDF cifrato con password di apertura scelto come B, quando si avvia il confronto, allora la password viene richiesta prima di qualsiasi elaborazione e, se errata, non viene creata alcuna sessione.
- Dato un confronto in corso tra documenti di 500 pagine, quando l'utente preme Annulla, allora il calcolo si interrompe entro 2 s, non si apre alcuna scheda di risultati e durante il calcolo nessun blocco dell'interfaccia supera 200 ms.
- Dato lo stesso file scelto come A e come B, quando si avvia il confronto, allora il risultato riporta zero differenze.
**Priorità:** P0

### CMP-02 — Confronto del contenuto testuale
**Descrizione:** Rileva le differenze di testo tra A e B a livello di parola (con dettaglio al carattere dentro le parole modificate) e le classifica come aggiunta, eliminazione o modifica. Il confronto segue l'ordine di lettura e non dipende dall'impaginazione: testo che scivola su pagine diverse a causa di inserimenti precedenti non genera false differenze.
**Flusso:**
1. Il sistema estrae il testo di A e B nell'ordine di lettura (albero di struttura se il documento è taggato, altrimenti analisi del layout per colonne e righe), ricompone le parole sillabate a fine riga e normalizza legature e spazi multipli.
2. Applica le opzioni scelte: ignora maiuscole/minuscole, ignora spazi e a capo, ignora punteggiatura (tutte disattivate per impostazione predefinita).
3. Allinea le due sequenze di parole con un algoritmo di differenza minima; un'eliminazione e un'aggiunta adiacenti nella stessa posizione diventano un'unica modifica.
4. Per ogni differenza memorizza tipo, testo in A, testo in B, pagine e quadrilateri su entrambi i lati e, per aggiunte/eliminazioni, il punto di inserimento sul lato opposto.
5. Le viste evidenziano le differenze con colore e stile distinti per tipo (predefiniti: aggiunta verde sottolineata, eliminazione rossa barrata, modifica arancione riquadrata), riconoscibili anche senza percepire il colore; i colori sono configurabili.
**Formati/strutture:** testo dagli operatori di testo dei content stream, decodificato tramite /ToUnicode, /Encoding e /ActualText; ordine da /StructTreeRoot quando presente; il livello di testo invisibile (modalità di resa 3) dei PDF sottoposti a OCR vale come testo. Segmentazione in parole secondo le regole Unicode; per le scritture senza spazi (CJK, thai) segmentazione per carattere; testo bidirezionale confrontato in ordine logico.
**Casi limite:**
- Pagina senza testo estraibile su uno o entrambi i lati → marcata "confrontata solo visivamente"; in modalità Automatica passa al confronto visivo (CMP-04) e viene suggerito il preset Scansione (CMP-12).
- Font senza mappatura Unicode (testo non decodificabile) → avviso "testo non decodificabile a pagina N"; quei tratti sono affidati al confronto visivo.
- Parola sillabata a fine riga in A e intera in B, legatura "ﬁ" contro "fi" → nessuna differenza.
- Paragrafo spostato → riportato come eliminazione nella posizione originale e aggiunta nella nuova (nessun tipo "spostamento" in questa versione).
- Documenti diversi per oltre l'80% delle parole → avviso "i documenti sembrano non correlati"; i risultati restano consultabili.
- Numeri di pagina e intestazioni che cambiano per effetto di inserimenti → gestiti dalle esclusioni di CMP-08.
**Criteri di accettazione:**
- Dato A con la frase "Il canone è di 100 euro" e B con "Il canone è di 120 euro", quando si confronta il testo, allora viene riportata una sola differenza di tipo modifica con testo A "100" e testo B "120", localizzata sulla pagina corretta di entrambi i documenti.
- Dato B uguale ad A con una pagina di testo nuova inserita dopo pagina 2, quando si confronta, allora le uniche differenze sono aggiunte che coprono il testo della pagina inserita e non viene riportata alcuna differenza sul testo delle pagine successive.
- Dato A e B che differiscono solo per maiuscole/minuscole, quando l'opzione "ignora maiuscole/minuscole" è attiva, allora il numero di differenze è zero; quando è disattivata, allora ogni parola variata è riportata come modifica.
- Dato A con "contrat-" a fine riga seguito da "to" e B con "contratto", quando si confronta, allora non viene riportata alcuna differenza.
- Dato l'insieme di prova di riferimento (coppie di documenti con differenze note), quando si esegue il confronto, allora sono rilevate tutte le differenze attese e nessuna differenza non attesa.
**Priorità:** P0

### CMP-03 — Confronto della formattazione del testo
**Descrizione:** Opzione che segnala, su testo altrimenti identico, le variazioni di famiglia del font, dimensione, colore e stile (grassetto, corsivo), come differenze di tipo *formattazione* distinte dalle differenze di contenuto.
**Flusso:**
1. L'utente attiva "Confronta formattazione" nelle opzioni (disattivata per impostazione predefinita).
2. Per ogni parola allineata e uguale il sistema confronta gli attributi, con tolleranze configurabili (dimensione ±0,1 pt; colore entro una distanza minima nello spazio colore di confronto).
3. Parole consecutive con la stessa variazione sono raggruppate in un'unica differenza.
4. Il dettaglio riporta l'attributo cambiato, per esempio "dimensione 14 → 16 pt", "font Serif → Sans", "colore #000000 → #C00000".
**Formati/strutture:** attributi dallo stato grafico del testo: font (/BaseFont senza prefisso di subset "ABCDEF+"), dimensione effettiva (dimensione del font × matrice di testo × matrice di trasformazione corrente), colore di riempimento convertito in RGB per il confronto, flag e peso del descrittore del font (/Flags, /FontWeight) e nome della variante.
**Casi limite:**
- Stesso font incorporato con prefissi di subset diversi → nessuna differenza.
- Nomi diversi della stessa variante di font (nome commerciale contro nome PostScript) → normalizzati secondo una tabella di equivalenze; se non riconosciuti, riportati come differenza di font.
- Stesso colore espresso in spazi colore diversi (grigio 0 contro RGB 0,0,0) → nessuna differenza.
- Testo cambiato e riformattato insieme → una sola differenza di tipo modifica, con il dettaglio di formattazione allegato.
**Criteri di accettazione:**
- Dato A e B con lo stesso testo ma una parola in grassetto solo in B, quando il confronto della formattazione è attivo, allora viene riportata una differenza di tipo formattazione su quella parola con l'attributo "stile"; quando è disattivo, allora non viene riportata alcuna differenza.
- Dato lo stesso font incorporato con prefissi di subset diversi in A e B, quando si confronta, allora non è riportata alcuna differenza di font.
- Dato un titolo passato da 14 a 16 pt, quando si confronta, allora il dettaglio della differenza indica "dimensione 14 → 16 pt".
**Priorità:** P1

### CMP-04 — Confronto visivo delle pagine
**Descrizione:** Rasterizza le pagine corrispondenti di A e B alla stessa risoluzione e individua le aree che differiscono visivamente (grafica vettoriale, immagini, disegni, timbri, scansioni, testo non estraibile), evidenziandole con riquadri. È il metodo principale per disegni tecnici, planimetrie e scansioni.
**Flusso:**
1. Il sistema abbina le pagine: in sequenza per impostazione predefinita oppure secondo l'allineamento del testo quando sono state rilevate pagine inserite o eliminate (CMP-09); l'utente può correggere l'abbinamento (es. pagina 5 di A ↔ pagina 6 di B).
2. Renderizza entrambe le pagine alla risoluzione di confronto (predefinita 150 dpi, configurabile 72–600 dpi), allineate sul riquadro visibile e sulla rotazione di pagina.
3. Calcola la differenza per pixel con una soglia di sensibilità (tolleranza di colore e area minima della regione) che ignora antialiasing e rumore; regioni vicine sono accorpate.
4. Ogni regione diventa una *differenza visiva* con riquadro su entrambe le pagine e percentuale di area variata.
5. L'utente regola la sensibilità (Alta, Media, Bassa con valori numerici visibili) e attiva o disattiva l'allineamento automatico; il ricalcolo riguarda solo le pagine interessate.
**Formati/strutture:** raster RGB delle pagine; riquadri /CropBox (o /MediaBox in sua assenza), /Rotate, /UserUnit; annotazioni stampabili incluse nel rendering (opzione, attiva per impostazione predefinita). Risultato: rettangoli in coordinate di pagina (punti PDF) e trasformazione di allineamento per pagina.
**Casi limite:**
- Pagine di formato diverso (A4 contro Letter) → allineamento in alto a sinistra (oppure centrato, a scelta); la differenza di formato è riportata una sola volta come differenza di "dimensione pagina", non come centinaia di regioni.
- Due scansioni dello stesso foglio leggermente traslate o ruotate → l'allineamento automatico (fino a ±2° e ±5 mm) le registra prima del calcolo; se disattivato e le differenze sono diffuse, appare l'avviso "pagine disallineate".
- Pagina presente solo in A o solo in B → riportata come pagina eliminata o aggiunta (CMP-09), non come area interamente diversa.
- Rendering con lievi differenze di resa (antialiasing, sostituzione di font visivamente equivalente) → sotto soglia, nessuna differenza.
- Pagine molto grandi (es. A0 a 600 dpi) → rendering a tasselli entro un limite di memoria; se necessario la risoluzione viene ridotta con avviso.
- Differenze di colore non visibili a schermo (es. tinte piatte con lo stesso aspetto RGB) → non rilevate; limite documentato nell'aiuto.
**Criteri di accettazione:**
- Dato A e B identici tranne un rettangolo di 10×10 mm aggiunto a pagina 3 di B, quando si esegue il confronto visivo, allora viene riportata esattamente una differenza visiva a pagina 3, con riquadro che contiene il rettangolo con tolleranza di 1 mm.
- Dato due rendering della stessa pagina che differiscono solo per l'antialiasing, quando la sensibilità è Media, allora non viene riportata alcuna differenza.
- Dato due scansioni dello stesso foglio traslate di 3 mm, quando l'allineamento automatico è attivo, allora non viene riportata alcuna differenza; quando è disattivo, allora compare l'avviso di disallineamento.
- Dato l'abbinamento manuale "pagina 5 di A ↔ pagina 6 di B", quando l'utente lo conferma, allora vengono ricalcolate solo le differenze di quelle pagine.
**Priorità:** P0

### CMP-05 — Vista affiancata con scorrimento sincronizzato
**Descrizione:** Mostra A e B in due riquadri affiancati con le differenze evidenziate; scorrimento e zoom sono sincronizzati in modo che il contenuto corrispondente resti allineato anche quando A e B hanno impaginazione diversa.
**Flusso:**
1. Al termine del confronto la vista affiancata è quella predefinita (A a sinistra, B a destra).
2. Scorrendo un riquadro, l'altro si porta sulla posizione corrispondente, calcolata dalle ancore dell'allineamento (testo e abbinamento delle pagine) e interpolata tra due ancore successive.
3. Zoom e comandi "adatta alla larghezza/pagina" si applicano a entrambi i riquadri.
4. L'interruttore "Sincronizza scorrimento" (attivo per impostazione predefinita) rende i riquadri indipendenti; alla riattivazione B si riallinea ad A.
5. Selezionando una differenza in un riquadro, questa viene evidenziata anche nell'altro (o il relativo punto di inserimento) e selezionata nell'elenco (CMP-07).
6. Una mappa a lato della barra di scorrimento mostra la posizione di tutte le differenze, colorate per tipo; un clic porta alla differenza.
7. L'orientamento può passare da affiancato a sovrapposto verticale (alto/basso).
**Formati/strutture:** tabella di corrispondenza tra posizioni, (pagina A, ordinata y) ↔ (pagina B, ordinata y), derivata dall'allineamento; stato della vista (zoom, orientamento, sincronizzazione) salvato nella sessione.
**Casi limite:**
- Blocco di più pagine aggiunto in B → mentre B scorre il blocco, A resta fermo sul punto di inserimento evidenziato e riprende a scorrere dopo il blocco.
- Pagine di formato diverso → lo zoom è sincronizzato in percentuale, così che la scala sia la stessa; in alternativa l'utente sceglie "adatta ciascuno".
- Sincronizzazione disattivata e riquadri lontani tra loro → un clic su una differenza nell'elenco porta entrambi i riquadri alla differenza.
- Finestra più stretta di 900 px → passaggio automatico alla disposizione verticale.
**Criteri di accettazione:**
- Dato B con 2 pagine inserite dopo pagina 3 di A, quando l'utente scorre A dalla pagina 3 alla pagina 4, allora B mostra la pagina 6, che contiene il testo corrispondente, e non la pagina 4.
- Dato lo scorrimento sincronizzato disattivato, quando l'utente scorre il riquadro sinistro, allora il destro non si muove; quando la sincronizzazione viene riattivata, allora il destro si riallinea alla posizione corrispondente.
- Dato uno zoom al 200% impostato nel riquadro A con sincronizzazione attiva, allora anche B passa al 200%.
- Dato la selezione di una differenza nel riquadro B, allora la stessa differenza risulta evidenziata in A e selezionata nell'elenco.
**Priorità:** P0

### CMP-06 — Vista sovrapposta (overlay)
**Descrizione:** Sovrappone le pagine corrispondenti di A e B in un'unica vista: il contenuto presente solo in A appare in un colore (predefinito rosso), quello presente solo in B in un altro (predefinito blu), le parti comuni in grigio scuro. Opacità regolabile per ciascun lato, alternanza A/B e allineamento manuale per individuare piccoli spostamenti.
**Flusso:**
1. L'utente sceglie la vista "Sovrapposta" nella barra della sessione.
2. Il sistema converte ciascuna pagina in una versione monocromatica colorata e le compone in modo che le parti coincidenti risultino scure e quelle divergenti nei colori dei rispettivi lati.
3. L'utente regola l'opacità di A e di B (0–100%), sceglie "solo A", "solo B" o "entrambi", oppure attiva l'alternanza automatica.
4. Allinea B ad A in modo automatico (come CMP-04) o manuale: traslazione con trascinamento o frecce (passo 0,1 mm), rotazione fine, scala, oppure registrazione a due punti di riferimento per lato; l'allineamento vale per la pagina o per tutto il documento.
5. Può mostrare sopra la sovrapposizione i riquadri delle differenze visive (CMP-04) e scegliere una combinazione di colori adatta a chi non distingue rosso e verde.
**Formati/strutture:** composizione raster delle pagine renderizzate; trasformazione affine 2D (traslazione, rotazione, scala) memorizzata per pagina nella sessione.
**Casi limite:**
- Pagine di formato diverso → scala reale 1:1 allineata in alto a sinistra; B viene scalata sul formato di A solo su richiesta.
- Pagine fotografiche o molto dense → sovrapposizione poco leggibile; viene suggerita la vista affiancata.
- Pagina senza corrispondente → mostrato solo il lato esistente con l'etichetta "pagina presente solo in A/B".
- Alternanza automatica → frequenza massima 3 commutazioni al secondo; disattivata per impostazione predefinita se il sistema chiede di ridurre le animazioni.
- "Reimposta allineamento" → ritorno alla trasformazione identità.
**Criteri di accettazione:**
- Dato A e B identici, quando si apre la vista sovrapposta, allora nessun pixel appare nel colore puro di A o di B.
- Dato B con una linea spostata di 2 mm, quando si visualizza la sovrapposizione, allora la linea compare due volte, una nel colore di A e una nel colore di B, a 2 mm di distanza.
- Dato l'opacità di A impostata a 0%, allora nella vista è visibile solo il contenuto di B.
- Dato un allineamento manuale di +3 mm in orizzontale su una pagina, quando la sessione viene salvata e riaperta, allora l'allineamento risulta invariato.
- Dato l'alternanza attiva, allora la frequenza misurata non supera 3 commutazioni al secondo.
**Priorità:** P0

### CMP-07 — Elenco differenze navigabile
**Descrizione:** Pannello laterale con tutte le differenze in ordine di documento: tipo (icona ed etichetta), pagina in A e in B, estratto "prima → dopo", stato di revisione (CMP-11). In testa i contatori per tipo; navigazione con clic, tastiera e comandi "differenza successiva/precedente".
**Flusso:**
1. Il pannello si apre con la sessione e mostra i totali, per esempio "12 aggiunte · 5 eliminazioni · 8 modifiche · 3 formattazione · 4 visive".
2. Il clic su una voce porta entrambe le viste sulla differenza, centrata ed evidenziata brevemente.
3. I comandi "Differenza successiva/precedente" (scorciatoie predefinite Alt+↓ e Alt+↑, configurabili) scorrono le voci in ordine di documento.
4. L'utente può raggruppare per pagina, ordinare per posizione in A o in B e cercare un testo all'interno delle differenze.
5. Una voce può essere copiata negli appunti (tipo, pagine, testo prima e dopo).
6. L'elenco è interamente usabile da tastiera; per ogni voce le tecnologie assistive annunciano tipo, pagine ed estratto.
**Formati/strutture:** modello della differenza: identificativo stabile nella sessione, tipo e sottotipo (attributo variato), pagine e riquadri in A e in B, testo A, testo B, stato, nota.
**Casi limite:**
- Nessuna differenza → messaggio "I documenti sono identici secondo i criteri selezionati", con l'elenco delle opzioni ed esclusioni attive.
- Decine di migliaia di differenze → elenco virtualizzato e avviso "documenti molto diversi".
- Differenza che si estende su più pagine → una sola voce con intervallo (es. "p. 4–5").
- Estratti lunghi → troncati a 120 caratteri, con testo completo nel suggerimento e nel dettaglio.
- Fine dell'elenco raggiunta con "successiva" → segnalazione e proposta di ripartire dalla prima.
**Criteri di accettazione:**
- Dato un confronto con 25 differenze, quando l'utente preme "Differenza successiva" 25 volte partendo dall'inizio, allora visita ogni differenza esattamente una volta in ordine di documento e alla pressione successiva viene segnalata la fine dell'elenco.
- Dato il clic su una voce a pagina 40, allora entrambi i riquadri mostrano la differenza centrata entro 500 ms.
- Dato un confronto senza differenze, allora il pannello mostra il messaggio di identità con l'elenco dei criteri applicati.
- Dato un confronto con 20.000 differenze, allora l'elenco si apre e scorre senza blocchi dell'interfaccia superiori a 100 ms.
- Dato nessun filtro attivo, allora la somma dei contatori per tipo è uguale al numero di voci dell'elenco.
**Priorità:** P0


## 12. Assistente IA

> **Principi comuni.** Foliant non usa server propri per l'IA: ogni richiesta parte dal computer dell'utente e va al fornitore di modelli linguistici che l'utente ha configurato con la propria chiave (AI-01). Nessuna funzione IA modifica il documento senza una conferma esplicita e ogni modifica si può annullare. Termini usati nei criteri: **invio** indica qualsiasi richiesta al fornitore; **fornitore simulato** indica un fornitore di prova, configurabile come endpoint personalizzato, che registra le richieste ricevute e restituisce risposte, flussi a frammenti, ritardi ed errori programmati; **riferimento di pagina** indica un collegamento cliccabile a una pagina, mostrato con l'etichetta di pagina che l'utente vede.

### AI-01 — Configurazione del fornitore di modelli e della chiave API

**Descrizione:** L'utente collega Foliant a un fornitore di modelli linguistici di sua scelta tramite uno o più profili. Per ogni profilo sceglie un fornitore dal catalogo integrato oppure un endpoint personalizzato, anche in esecuzione sul proprio computer, poi sceglie il modello e salva la propria chiave API nel portachiavi di sistema. Finché non c'è un profilo attivo, tutte le funzioni IA restano disattivate e l'app non contatta alcun servizio IA.

**Flusso:**
1. Impostazioni › Assistente IA › "Aggiungi profilo".
2. Scelta del fornitore dal catalogo oppure "Endpoint personalizzato" (URL base e protocollo tra quelli supportati).
3. Inserimento della chiave API in un campo mascherato, che si può mostrare temporaneamente; gli spazi iniziali e finali vengono rimossi.
4. Scelta del modello dall'elenco restituito dal fornitore oppure digitandone l'identificativo. Le capacità del modello (finestra di contesto, immagini in ingresso, generazione di immagini, vettori semantici) vengono rilevate o impostate a mano. Si possono indicare modelli dedicati alle immagini, alla generazione di immagini e ai vettori.
5. "Verifica connessione": una richiesta minima controlla raggiungibilità, chiave e modello. L'esito mostra la categoria dell'eventuale errore (AI-16).
6. Salvataggio: la chiave va nel portachiavi, il resto del profilo nelle impostazioni. Il profilo si può rendere attivo solo dopo una verifica riuscita e dopo aver accettato l'informativa (AI-02).
7. Gestione: modifica, duplicazione ed eliminazione del profilo; l'eliminazione rimuove anche la voce dal portachiavi. Un interruttore generale "Funzioni IA" nasconde tutti i comandi IA.

**Formati/strutture:** Profilo = {id, nome, fornitore, URL base, protocollo, modello predefinito, modelli per capacità, finestra di contesto, lunghezza massima della risposta, tempi di attesa, URL dell'informativa privacy del fornitore, limite di utilizzo locale facoltativo (richieste o token al giorno o al mese)}. La chiave non compare mai nelle impostazioni, nelle loro esportazioni, nei log, nei rapporti di errore o nella cronologia. La voce del portachiavi è identificata da applicazione + id del profilo. Le connessioni usano il proxy di sistema e verificano sempre il certificato del server.

**Casi limite:**
- Portachiavi assente o impossibile da sbloccare: la chiave non viene mai salvata in chiaro. L'app offre "usa solo per questa sessione": la chiave resta in memoria e si perde alla chiusura.
- Portachiavi bloccato: lo sblocco viene chiesto al primo uso dell'IA, non all'avvio dell'app.
- Endpoint che non richiede una chiave (servizio locale): la chiave è facoltativa.
- Endpoint non cifrato verso un host non locale: si può salvare solo dopo aver confermato esplicitamente un avviso di rischio. Un endpoint locale (loopback) è ammesso e viene indicato come "elaborazione sul computer".
- Il modello non dichiara una capacità (ad esempio le immagini): le funzioni che ne dipendono sono disattivate con una spiegazione, salvo forzatura nelle opzioni avanzate.
- Limite di utilizzo locale raggiunto: i nuovi invii vengono bloccati con un messaggio e il limite si può modificare.
- Profilo attivo eliminato: le funzioni IA tornano disattivate.

**Criteri di accettazione:**
- Dato il primo avvio dell'app senza profili configurati, quando l'utente apre il menu o il pannello IA, allora tutti i comandi IA sono disattivati con l'invito «Configura un fornitore» e il fornitore simulato non riceve alcuna richiesta durante la sessione.
- Dato un profilo salvato con la chiave «chiave-di-prova-123», quando si cerca questa stringa in tutti i file scritti dall'app (impostazioni, cronologia, registro degli invii, log, cache), allora la stringa non viene trovata e il portachiavi simulato contiene una sola voce per l'id del profilo.
- Dato il portachiavi non disponibile, quando l'utente salva il profilo scegliendo «solo per questa sessione», allora le funzioni IA si possono usare fino alla chiusura e dopo il riavvio risultano disattivate perché manca la chiave.
- Dato il fornitore simulato che espone tre modelli, quando l'utente preme «Verifica connessione», allora l'esito è positivo e i tre modelli sono selezionabili. Se il simulato risponde «autenticazione negata», l'esito è «Chiave non valida» e il profilo non si può attivare.
- Dato un profilo eliminato, quando si interroga il portachiavi simulato, allora la voce corrispondente non esiste più.

**Priorità:** P1

### AI-02 — Informativa e trasparenza sui dati inviati

**Descrizione:** L'utente sa sempre quali dati lasciano il computer, verso quale destinazione e con quale modello. Al primo uso di ogni profilo riceve un'informativa. In ogni operazione vede un riepilogo di ciò che sarà inviato, con anteprima completa. Può chiedere una conferma prima di ogni invio e consultare un registro locale degli invii. Foliant non riceve alcun dato e non raccoglie telemetria sulle funzioni IA; i dati inviati al fornitore sono trattati secondo le condizioni del fornitore.

**Flusso:**
1. Alla prima attivazione di un profilo compare l'informativa: dominio di destinazione, modello, dati che vengono inviati e dati che non vengono mai inviati, collegamento all'informativa del fornitore. Se l'utente non accetta, il profilo non si attiva.
2. In ogni operazione il pannello mostra la riga "Verrà inviato" (ad esempio "pagine 3–7 · ~12.400 caratteri · 1 immagine → dominio · modello"). La riga si espande nell'anteprima integrale di testo, immagini e istruzioni dell'app.
3. Opzione "Chiedi conferma prima di ogni invio", globale o per documento: con l'opzione attiva l'invio parte solo premendo "Invia".
4. Ogni invio viene annotato nel registro locale, che si può consultare, esportare e cancellare.
5. Se l'utente revoca il consenso nelle impostazioni, il profilo si disattiva.

**Formati/strutture:** Per impostazione predefinita vengono inviati il contenuto dell'ambito scelto (testo con marcatori di pagina, immagini delle aree scelte), le richieste dell'utente e i messaggi precedenti della stessa conversazione. Non vengono mai inviati il file PDF in sé, il nome e il percorso del file, i metadati, gli allegati incorporati e gli altri documenti. Commenti e valori dei campi modulo vengono inviati solo se l'utente attiva l'opzione relativa (AI-03). Voce di registro = {data-ora, profilo, dominio, modello, impronta del documento, funzione, pagine, n. caratteri, n. immagini, esito, token dichiarati dal fornitore}. Il registro non contiene il contenuto inviato.

**Casi limite:**
- I permessi del documento vietano la copia o l'estrazione del contenuto: le funzioni IA sono disattivate per quel documento, con una spiegazione. Si riattivano inserendo la password proprietario.
- Marcature di oscuramento non ancora applicate: il contenuto sottostante è escluso dall'invio, anche dalle immagini, in cui la marcatura viene resa coprente. L'anteprima segnala l'esclusione.
- Testo fuori dall'area visibile della pagina o su livelli nascosti: escluso e segnalato. Lo strato di testo invisibile che l'OCR crea sopra le scansioni viene invece incluso.
- Endpoint locale: l'informativa dichiara che i dati non lasciano il computer.
- Documento cifrato: l'informativa ricorda che il contenuto viene inviato in forma decifrata.
- Cambio di profilo durante una conversazione: se il nuovo profilo non è mai stato accettato compare la sua informativa; il riepilogo "Verrà inviato" si aggiorna.

**Criteri di accettazione:**
- Dato un profilo verificato la cui informativa non è stata accettata, quando l'utente avvia una funzione IA, allora compare l'informativa e il fornitore simulato non riceve richieste finché l'utente non accetta.
- Dato un documento «contratto-rossi.pdf» con autore «Rossi» nei metadati, quando l'utente riassume le pagine 2–3, allora le richieste registrate dal simulato contengono solo il testo di quelle pagine e non contengono né il nome del file né i metadati.
- Dato un documento con permesso di estrazione negato, quando l'utente apre il pannello IA, allora i comandi sono disattivati e non parte alcuna richiesta.
- Dato un documento con una marcatura di oscuramento non applicata sulla parola «IBAN-123», quando l'utente avvia la chat sul documento, allora «IBAN-123» non compare in alcuna richiesta e l'anteprima segnala l'esclusione.
- Dato un riassunto completato, quando l'utente apre il registro degli invii, allora trova una voce con funzione, pagine, numero di caratteri ed esito, senza alcun testo del documento.

**Priorità:** P1

### AI-03 — Pannello Assistente, ambito e uso delle risposte

**Descrizione:** Un pannello laterale riunisce le funzioni IA. L'utente sceglie l'ambito (documento, pagine, selezione, area o immagine, più documenti, oppure nessun documento per una chat generale), il modello del profilo attivo e la lingua di risposta. Le risposte arrivano man mano che vengono generate, con formattazione sicura, riferimenti di pagina cliccabili e azioni per riusarle. Le azioni principali sono disponibili anche nel menu contestuale della selezione.

**Flusso:**
1. L'utente apre il pannello dalla barra degli strumenti o con una scorciatoia, oppure sceglie un'azione rapida dal menu contestuale della selezione: Chiedi, Riassumi, Traduci, Spiega, Correggi, Riscrivi.
2. Imposta l'ambito, il modello e la lingua di risposta (quella dell'interfaccia, quella del documento o una a scelta). Le opzioni "includi commenti" e "includi valori dei campi modulo" sono disattivate per impostazione predefinita.
3. All'invio l'app fissa il contesto (selezione, pagine) così com'è in quel momento.
4. La risposta compare progressivamente. Al termine sono disponibili: Copia (testo semplice o formattato), Inserisci come nota, Inserisci come casella di testo, Salva come file (testo o Markdown), Rigenera.
5. I riferimenti di pagina portano alla pagina corrispondente.

**Formati/strutture:** Messaggio = {ruolo, testo, ambito (documenti, pagine, selezione con coordinate, area), modello, data-ora, riferimenti [{documento, indice pagina, etichetta pagina, citazione breve facoltativa}], stato (completo, interrotto, errore)}. Le risposte vengono mostrate come testo formattato (titoli, elenchi, tabelle, codice), senza eseguire script e senza caricare risorse remote. Note e caselle inserite sono annotazioni standard con autore uguale al nome autore impostato nelle preferenze e soggetto "Assistente IA".

**Casi limite:**
- Nessun documento aperto: è disponibile solo la chat generale.
- L'utente cambia scheda durante la risposta: la risposta resta legata al documento d'origine, che il pannello indica.
- La selezione cambia dopo l'invio: vale la selezione fissata all'invio.
- La risposta contiene immagini o collegamenti remoti: le immagini non vengono scaricate e appaiono come collegamenti inattivi. I collegamenti si aprono solo dopo una conferma che mostra l'indirizzo completo.
- Il testo del documento contiene istruzioni rivolte all'assistente: possono influenzare la risposta, ma l'app non esegue alcuna azione (modifiche, inserimenti, aperture, invii) senza la conferma dell'utente.
- Uso da tastiera e con tecnologie assistive: tutte le azioni si possono eseguire senza mouse e il completamento della risposta viene annunciato. Il testo da destra a sinistra viene mostrato correttamente.

**Criteri di accettazione:**
- Dato il fornitore simulato che risponde con un'immagine formattata che punta a un host esterno, quando la risposta viene mostrata, allora nessuna richiesta di rete raggiunge quell'host e al posto dell'immagine compare un collegamento inattivo.
- Dato un riferimento alla pagina con etichetta «iv» (quarta pagina fisica), quando l'utente vi fa clic, allora la vista si posiziona sulla quarta pagina fisica.
- Dato un testo selezionato inviato con «Spiega», quando l'utente cambia selezione prima che la risposta finisca, allora la richiesta registrata contiene il testo selezionato al momento dell'invio.
- Dato un messaggio di risposta completo, quando l'utente sceglie «Inserisci come nota», allora sulla pagina corrente compare un'annotazione nota con il testo della risposta, rimovibile con un solo «Annulla».

**Priorità:** P1

### AI-04 — Chat con il documento e riferimenti alle pagine

**Descrizione:** L'utente fa domande sul documento aperto e riceve risposte basate sul suo contenuto, con i riferimenti alle pagine da cui provengono le informazioni. Può continuare con altre domande nella stessa conversazione.

**Flusso:**
1. L'utente sceglie l'ambito "Documento" o un intervallo di pagine. Al primo uso l'app prepara il testo pagina per pagina (con OCR se necessario, AI-15). Se il documento supera la capacità del modello, l'app costruisce l'indice dei passaggi (AI-14).
2. L'utente scrive la domanda.
3. L'app invia le istruzioni (rispondere in base al contenuto, citare le pagine, dichiarare quando l'informazione manca), i passaggi con i marcatori di pagina, i messaggi precedenti e la domanda.
4. La risposta compare progressivamente. L'app convalida i riferimenti e li rende cliccabili. Un clic porta alla pagina ed evidenzia per qualche secondo la citazione, se la ritrova nel testo.
5. "Nuova conversazione" azzera il contesto.

**Formati/strutture:** Il testo inviato è suddiviso per pagina e ogni pagina è preceduta da un marcatore con indice fisico ed etichetta. Le istruzioni chiedono al modello di indicare i riferimenti in un formato convenzionale, ad esempio il numero di pagina tra parentesi quadre con una breve citazione facoltativa. L'app riconosce questi riferimenti e li trasforma in collegamenti.

**Casi limite:**
- Riferimento a una pagina inesistente o non inclusa nel contesto inviato: non è cliccabile ed è marcato "non verificato".
- Risposta senza riferimenti: riceve l'etichetta "non basata su pagine specifiche".
- Documento modificato durante la conversazione: compare un avviso che propone di aggiornare il contesto; i riferimenti precedenti possono essere marcati come superati.
- Citazione non ritrovata nel testo della pagina: il clic porta alla pagina senza evidenziare nulla.
- Documento senza testo: si applica AI-15.

**Criteri di accettazione:**
- Dato un PDF di 10 pagine e il fornitore simulato che risponde «Vedi [p. 3] e [p. 42]», quando la risposta viene mostrata, allora «p. 3» è un collegamento alla pagina 3 e «p. 42» non è cliccabile ed è marcato «non verificato».
- Dato un PDF di 3 pagine che rientra nella capacità del modello, quando l'utente fa una domanda, allora la richiesta contiene il testo di tutte e tre le pagine, ciascuna preceduta dal proprio marcatore, e la domanda.
- Dato uno scambio di due domande e due risposte, quando l'utente fa la terza domanda, allora la richiesta contiene i quattro messaggi precedenti; dopo «Nuova conversazione» non li contiene più.
- Dato un riferimento con una citazione presente alla pagina 5, quando l'utente vi fa clic, allora viene visualizzata la pagina 5 con il passaggio citato evidenziato.

**Priorità:** P1

### AI-05 — Riassunto con lunghezza regolabile

**Descrizione:** Riassume il documento intero, un intervallo di pagine o il testo selezionato, con lunghezza, forma e lingua scelte dall'utente e con i riferimenti alle pagine.

**Flusso:**
1. L'utente sceglie "Riassumi" dal pannello, dal menu Documento o dal menu contestuale della selezione.
2. Sceglie l'ambito (documento, pagine come "1-3, 7", selezione), la lunghezza (Breve, Media, Dettagliata, Personalizzata) e la forma (punti chiave, paragrafi, punti chiave + sintesi).
3. Compare il riepilogo "Verrà inviato" e l'operazione parte. I documenti lunghi vengono elaborati a blocchi e i risultati poi combinati (AI-14), mostrando l'avanzamento.
4. Il riassunto compare nel pannello con i riferimenti di pagina.
5. Azioni disponibili: copia, salva come file, inserisci come nota, inserisci come nuova pagina all'inizio del documento.

**Formati/strutture:** Preset: Breve ≈ 5 punti o al massimo 100 parole; Media ≈ 250 parole; Dettagliata ≈ 600 parole suddivise per sezioni; Personalizzata da 50 a 2.000 parole. La pagina inserita ha lo stesso formato della pagina corrente e contiene testo selezionabile con caratteri incorporati.

**Casi limite:**
- Intervallo non valido (sintassi errata o pagine inesistenti): errore mostrato accanto al campo, nessun invio.
- Selezione vuota: il comando è disattivato. Selezione molto breve (meno di 30 parole): compare un avviso, ma si può procedere.
- Risultato più lungo del richiesto: viene mostrato per intero indicandone la lunghezza effettiva, mai troncato senza avviso.
- Pagine senza testo nell'ambito: si applica AI-15. Se vengono escluse, il riassunto è marcato "parziale" con l'elenco delle pagine escluse.

**Criteri di accettazione:**
- Dato un PDF di 8 pagine, quando l'utente riassume le pagine «2-3», allora le richieste registrate dal fornitore simulato contengono il testo delle sole pagine 2 e 3.
- Dato il preset «Breve», quando parte la richiesta, allora le istruzioni inviate riportano il limite del preset; con «Personalizzata 300» riportano 300 parole.
- Dato l'intervallo «12-15» su un documento di 10 pagine, quando l'utente conferma, allora compare un errore di intervallo e il simulato non riceve richieste.
- Dato un riassunto completato, quando l'utente sceglie «Inserisci come nuova pagina», allora il documento ha una pagina in più in prima posizione, il testo estratto di quella pagina coincide con il riassunto e «Annulla» la rimuove.

**Priorità:** P1

### AI-06 — Traduzione di selezione e pagine

**Descrizione:** Traduce nella lingua scelta il testo selezionato o un intervallo di pagine. Il risultato compare nel pannello accanto all'originale e si può inserire nel documento come nota oppure al posto del testo originale.

**Flusso:**
1. L'utente seleziona del testo e sceglie "Traduci", oppure imposta un intervallo di pagine nel pannello.
2. Sceglie la lingua di destinazione (predefinita: l'ultima usata). La lingua di partenza viene rilevata automaticamente e si può correggere. Un glossario è facoltativo.
3. Il risultato compare nel pannello, raggruppato per pagina se l'ambito è un intervallo.
4. Azioni disponibili: copia; inserisci come nota ancorata alla selezione; sostituisci nel documento (stesso meccanismo e stessi controlli di AI-09); salva come file.

**Formati/strutture:** Almeno 20 lingue di destinazione; l'elenco si può estendere tramite codice di lingua standard. Glossario = coppie {termine, traduzione imposta oppure "non tradurre"}, che si può salvare e riusare. Numeri, formule, codici, indirizzi e nomi propri restano invariati.

**Casi limite:**
- Testo già nella lingua di destinazione: compare un avviso, ma si può procedere.
- Testo in più lingue: vengono tradotte solo le parti in una lingua diversa da quella di destinazione.
- Selezione su più colonne o più pagine: l'ordine di lettura viene ricostruito prima dell'invio.
- Testo che non si può sostituire nel documento: restano disponibili la nota e la copia (AI-09).

**Criteri di accettazione:**
- Dato il fornitore simulato che restituisce il testo in maiuscolo, quando l'utente traduce in inglese le pagine 1–2, allora il pannello mostra due sezioni «Pagina 1» e «Pagina 2» con il testo trasformato e la richiesta indica l'inglese come lingua di destinazione.
- Dato un glossario con «Foliant → non tradurre», quando parte la richiesta, allora le istruzioni inviate contengono questo vincolo.
- Dato un testo selezionato e tradotto, quando l'utente sceglie «Inserisci come nota», allora viene creata un'annotazione ancorata alla selezione che contiene il testo tradotto.

**Priorità:** P1

### AI-07 — Traduzione del documento in un nuovo PDF

**Descrizione:** Crea un nuovo PDF tradotto, dell'intero documento o di un intervallo, senza modificare l'originale. Per impostazione predefinita il testo tradotto prende il posto dell'originale nella stessa posizione e con uno stile analogo, conservando immagini, grafica, collegamenti e annotazioni. In alternativa produce una versione bilingue o una di solo testo.

**Flusso:**
1. Menu Documento › "Traduci in un nuovo PDF".
2. L'utente sceglie lingua, intervallo, modalità (Impaginazione mantenuta, Bilingue a pagine alternate, Solo testo riformattato), glossario, eventuale traduzione di segnalibri e commenti e percorso di destinazione (predefinito: nome originale + suffisso della lingua).
3. L'app mostra la stima (pagine, caratteri, richieste) e chiede conferma se si supera la soglia (AI-14).
4. La traduzione procede per blocchi di testo, con l'avanzamento per pagina. Le pagine scansionate passano prima dall'OCR (AI-15).
5. Composizione: ogni blocco tradotto prende il posto dell'originale nel suo riquadro.
6. Il nuovo PDF si apre in una nuova scheda insieme a un rapporto finale: pagine tradotte, blocchi rimpiccioliti per farli entrare nel riquadro, pagine non tradotte, errori.

**Formati/strutture:** Il PDF prodotto dichiara come lingua del documento quella di destinazione. I caratteri incorporati coprono tutti i glifi necessari; se il carattere originale non li contiene, se ne usa uno sostitutivo di stile simile. Nella versione bilingue ogni pagina originale è seguita dalla sua traduzione. Il rapporto si può consultare ed esportare come testo.

**Casi limite:**
- Traduzione più lunga del riquadro: il corpo del carattere viene ridotto fino a una soglia minima configurabile. Se non basta, il testo viene comunque inserito per intero al corpo minimo leggibile e segnalato nel rapporto; nessun testo viene omesso senza avviso.
- Errori su alcune pagine: le altre pagine vengono completate. L'utente sceglie se salvare lasciando quelle pagine in lingua originale, segnalate nel rapporto, o ritentare solo quelle.
- Annullamento: nessun file viene creato nella destinazione e i file temporanei vengono rimossi.
- Pagina scansionata: dopo l'OCR il testo tradotto copre, sopra l'immagine, le aree di testo riconosciute.
- Scrittura da destra a sinistra o ideografica senza un carattere disponibile: compare un avviso prima dell'avvio.
- Campi modulo: nomi e valori non vengono tradotti.
- Destinazione coincidente con il file originale: non consentita.

**Criteri di accettazione:**
- Dato un PDF di 3 pagine con 2 immagini e il fornitore simulato che restituisce ogni blocco in maiuscolo, quando si traduce l'intero documento, allora il nuovo PDF ha 3 pagine e 2 immagini, il testo estratto da ogni pagina corrisponde ai blocchi trasformati e l'impronta del file originale non cambia.
- Dato un documento di 2 pagine e la modalità bilingue, quando la traduzione termina, allora il nuovo PDF ha 4 pagine in quest'ordine: originale 1, tradotta 1, originale 2, tradotta 2.
- Dato il fornitore simulato che fallisce sempre sulla pagina 2 di 3, quando la traduzione termina, allora il rapporto indica la pagina 2 come non tradotta e «Ritenta pagine non riuscite» invia richieste solo per la pagina 2.
- Dato un processo di traduzione annullato a metà, quando si controlla la cartella di destinazione, allora il file di uscita non esiste.
- Dato un blocco la cui traduzione è lunga il doppio dell'originale, quando il PDF viene composto, allora il testo estratto del blocco è completo e il rapporto lo indica come «ridotto».

**Priorità:** P1

### AI-08 — Spiegazione di testo e termini

**Descrizione:** Spiega in modo chiaro un termine, una sigla, una formula o un passaggio selezionato, tenendo conto del contesto in cui compare. La spiegazione si può salvare come nota sul documento.

**Flusso:**
1. L'utente seleziona il testo e sceglie "Spiega", oppure incolla un testo nel pannello.
2. Sceglie il livello: Semplice, Standard o Approfondito.
3. L'app invia la selezione con il paragrafo circostante e la lingua di risposta.
4. La spiegazione compare nel pannello. Azioni disponibili: "Salva come nota" ancorata alla selezione, copia, domanda di approfondimento.

**Formati/strutture:** Contesto inviato = selezione + paragrafo circostante (limite configurabile, predefinito circa 1.500 caratteri). Nota = annotazione di testo ancorata alla selezione, con soggetto "Spiegazione IA".

**Casi limite:**
- Selezione oltre 4.000 caratteri: l'app suggerisce "Riassumi", ma si può procedere.
- Selezione su più pagine: la nota viene ancorata all'inizio della selezione.
- Contenuto grafico (immagine, formula disegnata): l'app propone di usare la chat con un'area (AI-11).
- Termine ripetuto nel documento: la spiegazione riguarda l'occorrenza selezionata e il suo contesto.

**Criteri di accettazione:**
- Dato il termine «ammortamento» selezionato all'interno di un paragrafo, quando l'utente sceglie «Spiega» con livello «Semplice», allora la richiesta contiene il termine, il paragrafo che lo contiene e il livello, e nessun altro testo del documento.
- Dato un testo già spiegato, quando l'utente sceglie «Salva come nota», allora alle coordinate della selezione viene creata un'annotazione il cui testo coincide con la spiegazione.

**Priorità:** P1

### AI-09 — Riscrittura e correzione del testo selezionato

**Descrizione:** Sul testo selezionato l'utente può chiedere una correzione (grammatica, ortografia, punteggiatura), un miglioramento dello stile, un cambio di tono, un'espansione, una condensazione o un'istruzione libera. Esamina la proposta con le differenze evidenziate. Se la accetta, il testo viene sostituito nel documento tramite la funzione di modifica testo, mantenendo lo stile originale.

**Flusso:**
1. L'utente seleziona il testo, in lettura o in modalità modifica testo, e sceglie l'azione: Correggi, Migliora, Cambia tono (formale, informale, professionale, cordiale, persuasivo), Espandi, Condensa, Istruzione libera.
2. L'app invia il testo e l'istruzione. La proposta compare con le differenze evidenziate parola per parola.
3. L'utente può ritoccare la proposta, rigenerarla, accettarla o scartarla.
4. Con "Accetta" il testo viene sostituito nello stesso blocco tramite la modifica testo, con carattere, corpo e colore originali e con il testo che si ridistribuisce nel riquadro. L'intera sostituzione si annulla con un solo comando.
5. Se la sostituzione non è possibile, l'app propone in alternativa un'annotazione di sostituzione testo (barrato + testo proposto), un commento o la copia.

**Formati/strutture:** Proposta = {testo originale, testo proposto, differenze parola per parola, azione}. La sostituzione modifica il contenuto della pagina nel blocco d'origine secondo le regole dell'area Modifica testo. L'alternativa non distruttiva usa le annotazioni standard di sostituzione testo o di nota.

**Casi limite:**
- Testo non modificabile (pagina scansionata, glifi mancanti senza un sostituto adatto, permesso di modifica negato): la sostituzione è disattivata e ne viene indicato il motivo; restano disponibili le alternative.
- Selezione su più blocchi o più pagine: la sostituzione avviene blocco per blocco solo se la proposta si può ripartire senza ambiguità; altrimenti restano solo le alternative.
- Formattazione mista: viene mantenuto lo stile prevalente, con un avviso.
- Testo proposto più lungo dello spazio disponibile: il riquadro si allarga o il corpo del carattere si riduce secondo le regole della modifica testo, con un avviso. Il testo non si sovrappone mai ad altri contenuti senza segnalazione.
- Documento firmato digitalmente: prima di applicare la modifica compare un avviso che le firme verranno invalidate.
- Il testo nel punto selezionato è cambiato nel frattempo: la proposta è marcata come obsoleta e non si può applicare.
- Proposta identica all'originale: compare il messaggio "nessuna modifica proposta".

**Criteri di accettazione:**
- Dato il testo selezionato «Il documento e stato aprovato» e il fornitore simulato che restituisce «Il documento è stato approvato», quando l'utente accetta, allora il testo estratto della pagina contiene la frase corretta al posto dell'originale, con lo stesso carattere e lo stesso corpo, e un solo «Annulla» ripristina l'originale.
- Dato un testo con una proposta visualizzata, quando l'utente sceglie «Scarta», allora il documento non risulta modificato.
- Dato un testo selezionato su una pagina composta solo da un'immagine, quando l'utente apre le azioni, allora «Sostituisci nel documento» è disattivato e «Aggiungi come annotazione di sostituzione» crea un'annotazione con il testo proposto.
- Dato un documento firmato digitalmente, quando l'utente accetta una proposta, allora compare l'avviso di invalidazione e la modifica avviene solo dopo la conferma.

**Priorità:** P1

### AI-10 — Estrazione di dati strutturati in CSV e JSON

**Descrizione:** Estrae dati strutturati da pagine, selezioni, aree o più file: tabelle (anche senza bordi o distribuite su più pagine), voci di fatture e ricevute, oppure campi definiti dall'utente. Il risultato si controlla in una griglia modificabile e si esporta in CSV o JSON, con la pagina d'origine di ogni valore.

**Flusso:**
1. L'utente sceglie Strumenti › "Estrai dati" oppure la funzione dal pannello IA.
2. Sceglie il tipo: Tabelle, Fattura/Ricevuta oppure Schema personalizzato (campi con nome, tipo e descrizione, salvabile come modello).
3. Sceglie l'ambito: documento, pagine, selezione, area o più file. Con più file si ottiene un record per documento oppure righe con una colonna "file d'origine".
4. Il risultato compare in una griglia modificabile. Sono evidenziati i valori che non si ritrovano nel testo d'origine e i controlli non superati, ad esempio la somma delle righe diversa dal totale.
5. Esportazione in CSV o JSON, oppure copia negli appunti. Le tabelle possono anche essere usate per la conversione in foglio di calcolo (area Conversione).

**Formati/strutture:** CSV secondo la specifica standard: riga di intestazione, virgolette di protezione, UTF-8 predefinito, separatore virgola o punto e virgola. JSON = {origine: {documento, pagine}, data, modello, tipo, record: [...]}, con la pagina d'origine di ogni valore, date in formato ISO e numeri con il punto decimale. Schema fattura predefinito: numero, data, fornitore, partita IVA/codice fiscale, cliente, righe [descrizione, quantità, prezzo unitario, aliquota, importo], imponibile, imposta, totale, valuta.

**Casi limite:**
- Risposta non conforme allo schema: l'app fa un tentativo automatico di correzione; se fallisce mostra un errore e permette di consultare la risposta grezza.
- Valore che non si trova nel testo d'origine (possibile invenzione del modello): marcato "non verificato".
- Tabella su più pagine: le righe vengono unite e le intestazioni ripetute rimosse.
- Numeri in formato locale (ad esempio 1.234,56): normalizzati senza perdita di informazione.
- Nessun dato trovato: messaggio esplicito, senza creare file vuoti.
- Pagine scansionate: OCR preventivo (AI-15).

**Criteri di accettazione:**
- Dato un PDF di fattura con tre righe e il fornitore simulato che restituisce dati conformi allo schema, quando l'utente esporta in JSON, allora il file è valido e contiene tre righe con i campi dello schema e la pagina d'origine di ogni valore.
- Dato il fornitore simulato che restituisce un totale «999,00» assente dal testo della pagina, quando il risultato viene mostrato, allora la cella del totale è marcata «non verificato».
- Dato il fornitore simulato che restituisce prima un testo non conforme e poi uno conforme, quando l'estrazione termina, allora il simulato ha ricevuto esattamente due richieste e la griglia mostra il risultato conforme.
- Dato un valore che contiene punto e virgola e virgolette, quando si esporta in CSV con separatore punto e virgola, allora un lettore CSV conforme che rilegge il file restituisce il valore identico.

**Priorità:** P2

### AI-11 — Chat con immagini e aree della pagina

**Descrizione:** L'utente interroga l'assistente su un'immagine del documento, su un'area rettangolare della pagina (grafico, tabella, schema, formula, scrittura a mano) o su un'immagine esterna. Può chiederne la descrizione, la spiegazione, l'estrazione o la traduzione del testo contenuto, oppure fare domande libere.

**Flusso:**
1. L'utente sceglie lo strumento "Seleziona area" e trascina un rettangolo, oppure fa clic destro su un'immagine e sceglie "Chiedi all'IA", oppure trascina o incolla un'immagine esterna nel pannello.
2. Il pannello mostra la miniatura esatta di ciò che verrà inviato.
3. L'utente sceglie un'azione rapida (Descrivi, Spiega il grafico, Estrai testo, Traduci il testo) o scrive una domanda.
4. La risposta compare progressivamente. Il testo estratto si può copiare o inserire come casella di testo o nota.

**Formati/strutture:** Immagini esterne accettate: PNG, JPEG, WebP, BMP, TIFF, GIF (solo il primo fotogramma). L'area viene convertita in immagine con lato massimo predefinito di 2.048 pixel (configurabile) e compressa. La richiesta include l'immagine e, se presente, il testo estratto dall'area.

**Casi limite:**
- Modello che non accetta immagini: le azioni sono disattivate con una spiegazione. L'alternativa, dichiarata all'utente, è l'OCR locale dell'area con invio del solo testo.
- Area sotto i 16×16 pixel o vuota, immagine esterna non supportata o danneggiata: errore, nessun invio.
- Più immagini nella stessa domanda: ammesse fino al limite del modello (predefinito 5); oltre compare un avviso.
- Marcature di oscuramento non applicate nell'area: nell'immagine inviata sono rese coprenti (AI-02).

**Criteri di accettazione:**
- Dato un rettangolo che, convertito in immagine, misura 3.000×1.500 pixel, quando viene inviato con le impostazioni predefinite, allora il fornitore simulato riceve un'immagine di 2.048×1.024 pixel.
- Dato un modello configurato senza capacità immagini, quando l'utente sceglie «Estrai testo» su un'area e accetta l'alternativa, allora non viene inviata alcuna immagine e la richiesta contiene solo il testo riconosciuto localmente.
- Dato un rettangolo di 10×10 pixel, quando l'utente conferma, allora compare un errore e il simulato non riceve richieste.

**Priorità:** P1

### AI-12 — Mappa concettuale e scaletta

**Descrizione:** Ricava dal documento o da un intervallo di pagine una struttura gerarchica dei contenuti. La struttura si consulta come mappa concettuale interattiva o come scaletta, si può modificare ed esportare e si può inserire nel PDF.

**Flusso:**
1. Pannello IA › "Mappa concettuale" o "Scaletta". L'utente imposta ambito, lingua, profondità massima (da 2 a 5 livelli) e numero massimo di nodi.
2. La generazione mostra l'avanzamento (per i documenti lunghi si applica AI-14).
3. Vista mappa (zoom, spostamento, espandi/comprimi) o vista scaletta a rientri. Ogni nodo ha un riferimento di pagina cliccabile.
4. Modifica: rinomina, aggiungi, elimina e sposta nodi.
5. Uscite: immagine PNG o SVG, PDF, scaletta in Markdown, JSON. "Inserisci nel documento" aggiunge una nuova pagina all'inizio o alla fine, adattata al formato della pagina o con dimensioni personalizzate. "Crea segnalibri dalla scaletta" usa AI-22.

**Formati/strutture:** Nodo = {id, titolo, nota facoltativa, pagina, figli[]}; la radice è il titolo del documento. La pagina inserita è in grafica vettoriale, con testo selezionabile e ricercabile.

**Casi limite:**
- Struttura oltre i limiti impostati: i livelli in eccesso vengono accorpati e l'accorpamento viene segnalato.
- Etichette lunghe: troncate nella mappa, complete nella scaletta e nel suggerimento che compare passando il puntatore.
- Mappa troppo grande per una pagina: viene ridotta fino a un corpo minimo leggibile; oltre, l'app propone di suddividerla su più pagine.
- Risposta non conforme alla struttura attesa: un nuovo tentativo, poi errore.

**Criteri di accettazione:**
- Dato il fornitore simulato che restituisce una struttura con 1 radice, 3 rami e 6 foglie, quando la mappa viene mostrata, allora contiene 10 nodi e il clic su una foglia associata alla pagina 4 porta alla pagina 4.
- Dato un documento con una mappa generata, quando l'utente sceglie «Inserisci nel documento» all'inizio, adattata alla pagina, allora il documento ha una pagina in più in prima posizione, da cui si estrae il testo di tutti i nodi, e «Annulla» la rimuove.
- Dato un nodo rinominato, quando si esporta in JSON, allora il file contiene il nuovo titolo e rispetta la struttura dei nodi.

**Priorità:** P1

### AI-13 — Chat su più documenti

**Descrizione:** L'utente riunisce più PDF in una raccolta e fa domande che li riguardano tutti (confronti, sintesi complessive, ricerca di informazioni). Le risposte contengono riferimenti a documento e pagina.

**Flusso:**
1. L'utente sceglie l'ambito "Più documenti" e aggiunge schede aperte o file dal disco, anche trascinandoli.
2. L'app prepara ogni documento (testo, OCR se serve, indice dei passaggi) e mostra lo stato di ciascun file.
3. L'utente scrive una domanda o sceglie un'azione rapida ("Riassumi la raccolta", "Confronta"). L'app invia i passaggi più pertinenti di ciascun documento, con marcatori di documento e pagina.
4. I riferimenti "Documento › p. N" aprono il documento alla pagina indicata, in una nuova scheda se era chiuso.
5. La raccolta si può salvare localmente e riaprire insieme alla sua cronologia.

**Formati/strutture:** Raccolta = {nome, documenti [{percorso, impronta del contenuto, titolo mostrato}], conversazioni}. Limite predefinito di 20 documenti; la dimensione totale massima è configurabile.

**Casi limite:**
- Documento spostato o modificato dopo il salvataggio della raccolta: viene segnalato. L'app chiede di ritrovarlo oppure aggiorna l'indice.
- Documento non elaborabile (permessi negati, password, file danneggiato): escluso con indicazione del motivo; gli altri documenti restano utilizzabili.
- Stesso documento aggiunto due volte (stessa impronta): compare una sola voce.
- Limite superato: l'aggiunta viene impedita con un messaggio.

**Criteri di accettazione:**
- Dato un insieme di due documenti A e B e il fornitore simulato che cita «B › p. 2», quando l'utente fa clic sul riferimento, allora si apre il documento B alla pagina 2.
- Dato un documento con permesso di estrazione negato aggiunto alla raccolta, quando si fa una domanda, allora le richieste non contengono nulla di quel documento e la raccolta lo mostra come escluso.
- Dato il salvataggio di una raccolta seguito dal riavvio dell'app, quando l'utente riapre la raccolta, allora trova le conversazioni precedenti e i riferimenti funzionano.

**Priorità:** P2

### AI-14 — Documenti lunghi: suddivisione ed elaborazione a blocchi

**Descrizione:** Quando il contenuto supera la capacità del modello, l'app lo divide in blocchi coerenti e invia solo ciò che serve. Per le domande invia i passaggi pertinenti; per riassunti, mappe ed estrazioni elabora i blocchi uno alla volta e poi ne combina i risultati. Prima delle operazioni onerose mostra il volume di dati e il numero di richieste stimati.

**Flusso:**
1. L'app stima la dimensione dell'ambito rispetto alla finestra di contesto del modello, lasciando un margine per istruzioni e risposta.
2. Se serve, divide il testo in blocchi lungo i confini di pagina e di paragrafo, con una breve sovrapposizione. Ogni blocco conserva i marcatori di pagina.
3. Per le domande, l'app sceglie localmente i blocchi più pertinenti entro il budget: per parole e, se il profilo ha un modello per vettori semantici, anche per significato.
4. Per riassunti, mappe ed estrazioni, l'app elabora ogni blocco e combina i risultati parziali, su uno o più livelli.
5. Oltre una soglia (predefinita: 20 richieste o 200.000 caratteri) l'app mostra la stima e chiede conferma.
6. L'indice dei blocchi resta in una cache locale per documento e viene riusato finché il contenuto non cambia.

**Formati/strutture:** Blocco = {documento, pagine coperte, intervallo di caratteri, testo}. La stima si basa sul numero di caratteri con un margine di sicurezza, oppure sul conteggio fornito dal fornitore, se disponibile. La cache locale è indicizzata per impronta del contenuto, ha una dimensione massima e si può svuotare con un comando.

**Casi limite:**
- Finestra di contesto sconosciuta: si usa un valore prudente predefinito, modificabile nel profilo.
- Paragrafo più grande di un blocco: viene diviso per frasi.
- Documento oltre il limite massimo configurato: l'app chiede di restringere l'ambito.
- Il fornitore segnala il superamento del contesto nonostante la stima: i blocchi vengono ridotti e la richiesta viene ritentata una volta.
- Documento modificato: l'indice viene invalidato e ricostruito al successivo uso.

**Criteri di accettazione:**
- Dato un documento di 500 pagine e un profilo con finestra di 8.000 token sul fornitore simulato, quando l'utente chiede il riassunto completo, allora nessuna richiesta supera la finestra, ogni pagina compare in almeno un blocco e l'ultima richiesta contiene i riassunti parziali.
- Dato lo stesso documento, quando l'utente fa una domanda, allora la richiesta contiene solo i blocchi selezionati entro il budget, ciascuno con i propri marcatori di pagina.
- Dato un riassunto stimato in 40 richieste, quando l'utente lo avvia, allora compare la conferma con la stima e, se l'utente annulla, il simulato non riceve richieste.
- Dato un indice già costruito, quando l'utente riapre il documento senza modifiche, allora l'indice non viene ricostruito; dopo una modifica del testo sì.

**Priorità:** P1

### AI-15 — Documenti scansionati: OCR prima dell'invio

**Descrizione:** Prima di ogni invio l'app individua le pagine senza testo utilizzabile (scansioni, testo non decodificabile). Propone di riconoscere il testo localmente con l'OCR, di inviare le immagini delle pagine a un modello che le accetta oppure di escludere quelle pagine. La scelta fatta e le sue conseguenze sono sempre dichiarate.

**Flusso:**
1. All'avvio di una funzione IA, l'app analizza le pagine dell'ambito.
2. Se trova pagine senza testo, ne mostra l'elenco con tre opzioni: "Esegui OCR locale" (lingue preselezionate in base alla lingua dell'interfaccia e a quella del documento), "Invia le pagine come immagini" (solo se il modello accetta immagini, con conferma esplicita), "Escludi queste pagine".
3. Con l'OCR l'avanzamento è mostrato pagina per pagina e si può annullare. L'utente sceglie "Salva il testo riconosciuto nel documento", che rende il PDF ricercabile ed è annullabile, oppure un uso solo temporaneo.
4. La funzione IA prosegue. Le risposte che riguardano pagine escluse o riconosciute con bassa affidabilità vengono marcate.

**Formati/strutture:** Una pagina è considerata senza testo utilizzabile se: non ha testo estraibile, oppure il suo testo contiene un'alta quota di caratteri non decodificabili, oppure il testo copre una frazione minima di una pagina occupata da un'immagine. L'OCR usa la funzione dell'area OCR; l'affidabilità di ogni pagina viene conservata per gli avvisi.

**Casi limite:**
- Documento misto: vengono trattate solo le pagine senza testo.
- OCR con affidabilità sotto una soglia configurabile: la risposta mostra l'avviso "testo riconosciuto con bassa affidabilità".
- Lingua OCR non installata: l'app propone di scegliere un'altra lingua o di installarla; nel frattempo non parte nessun invio.
- OCR annullato: la funzione IA non parte e il documento non viene modificato.
- Scrittura a mano: se il modello accetta immagini, l'app propone come prima scelta l'invio delle pagine come immagini.

**Criteri di accettazione:**
- Dato un PDF di 5 pagine in cui le pagine 2 e 4 sono solo immagini, quando l'utente chiede un riassunto, allora prima di qualsiasi richiesta compare l'avviso che elenca le pagine 2 e 4.
- Dato lo stesso PDF e la scelta «Escludi queste pagine», quando parte il riassunto, allora nessuna richiesta contiene contenuto delle pagine 2 e 4 e il risultato è marcato «parziale: pagine 2, 4 escluse».
- Dato lo stesso PDF e la scelta «Esegui OCR locale» senza salvataggio nel documento, quando la funzione termina, allora le richieste contengono il testo riconosciuto e il documento non risulta modificato.
- Dato un modello senza capacità immagini, quando compare l'avviso, allora l'opzione «Invia le pagine come immagini» non è disponibile.

**Priorità:** P1

### AI-16 — Errori di rete, di autenticazione e di quota

**Descrizione:** Ogni errore di comunicazione con il fornitore viene classificato e mostrato con un messaggio comprensibile e un'azione per risolverlo. Gli errori temporanei vengono ritentati automaticamente entro limiti precisi, i risultati parziali non vanno persi e l'app non si blocca.

**Flusso:**
1. Un invio fallisce o supera il tempo massimo.
2. L'app classifica l'errore: rete assente o host irraggiungibile, certificato non valido, tempo scaduto, chiave non valida o non autorizzata, modello inesistente, limite di frequenza, quota o credito esauriti, contenuto rifiutato dal fornitore, contesto troppo lungo, risposta interrotta o malformata, errore del servizio.
3. Errori temporanei (limite di frequenza, errore del servizio, tempo scaduto): fino a 3 nuovi tentativi con attese crescenti. Se il fornitore indica un tempo di attesa, l'app lo rispetta fino a 60 s. Il pannello mostra il conto alla rovescia e permette di annullare.
4. Errori non temporanei: nessun nuovo tentativo. Il messaggio propone un'azione: aprire le impostazioni del profilo, scegliere un altro modello, ridurre l'ambito, riprovare più tardi.
5. Operazioni con più richieste: i blocchi completati vengono conservati. "Riprendi" continua dal punto dell'errore senza ripetere i blocchi già riusciti.
6. Risposta troncata perché ha raggiunto la lunghezza massima: viene marcata "incompleta" e compare il pulsante "Continua".

**Formati/strutture:** Messaggi localizzati, con un dettaglio tecnico espandibile (codice e testo del fornitore) in cui la chiave è sempre mascherata. Tempi predefiniti: 15 s per la connessione, 60 s di inattività durante la risposta, entrambi configurabili nel profilo. Il raggiungimento del limite di utilizzo locale (AI-01) viene trattato come una quota esaurita.

**Casi limite:**
- Rete persa durante la risposta: il testo già ricevuto viene conservato e marcato "interrotto", con il pulsante "Riprova".
- Errori ripetuti: dopo 3 tentativi l'app si ferma e mostra il messaggio; non ritenta all'infinito.
- Certificato non valido: non si può ignorare; il messaggio indica il dominio interessato.
- Ritorno della connessione: le operazioni fallite non vengono reinviate senza un'azione dell'utente.
- Errore del fornitore con testo arbitrario: il testo viene mostrato come testo semplice, senza interpretarlo.

**Criteri di accettazione:**
- Dato il fornitore simulato che risponde «troppe richieste, riprova tra 2 s» e poi risponde correttamente, quando l'utente invia una domanda, allora l'app ritenta una sola volta dopo almeno 2 s e mostra la risposta.
- Dato il simulato che risponde «chiave non valida», quando l'utente invia, allora non ci sono nuovi tentativi, compare il messaggio con il collegamento alle impostazioni del profilo e la chiave non compare nel dettaglio tecnico.
- Dato il simulato che risponde sempre «errore del servizio», quando l'utente invia, allora il simulato registra esattamente 4 richieste (1 + 3 tentativi) e compare il messaggio finale.
- Dato un processo di traduzione di 10 blocchi in cui il blocco 6 fallisce con un errore non temporaneo, quando l'utente sceglie «Riprendi» dopo aver risolto il problema, allora il simulato riceve richieste solo per i blocchi da 6 a 10.
- Dato il collegamento di rete disattivato, quando l'utente invia, allora entro il tempo di connessione compare «Rete non disponibile» e l'interfaccia continua a rispondere.

**Priorità:** P1

### AI-17 — Avanzamento e annullamento

**Descrizione:** Ogni operazione IA mostra il proprio avanzamento e si può annullare in qualsiasi momento senza lasciare effetti indesiderati. Le operazioni lunghe proseguono in background mentre l'utente continua a usare l'app.

**Flusso:**
1. Durante l'operazione compare il testo man mano che arriva, oppure una barra di avanzamento con la fase in corso (ad esempio "OCR pagina 3/12", "blocco 4/9", "composizione PDF").
2. "Interrompi", o il tasto Esc quando il pannello è attivo, ferma la ricezione e le richieste in coda.
3. Nella chat il testo già ricevuto resta, marcato "interrotto". Le operazioni che producono file o modificano il documento non lasciano file parziali né modifiche.
4. Le operazioni lunghe compaiono in un elenco attività con stato e comando di annullamento; nel frattempo l'utente può leggere e modificare.
5. Se si chiude una scheda o l'applicazione con operazioni in corso, l'app chiede conferma e poi le annulla.

**Formati/strutture:** Stato dell'operazione = {id, funzione, documento, fase, avanzamento, inizio, stato (in corso, completata, interrotta, errore)}.

**Casi limite:**
- Annullamento durante l'OCR preventivo: l'OCR si ferma e nessun testo viene salvato nel documento.
- Annullamento mentre è aperta l'anteprima di una riscrittura: la proposta viene scartata.
- Annullamento durante la scrittura del nuovo PDF: il file temporaneo viene rimosso e la destinazione resta com'era (o assente).
- Più operazioni contemporanee: si annullano singolarmente. Un limite di operazioni parallele per profilo evita di sovraccaricare il fornitore.

**Criteri di accettazione:**
- Dato il fornitore simulato che invia un frammento al secondo, quando l'utente preme «Interrompi» dopo il terzo frammento, allora entro 1 s la connessione viene chiusa, non vengono mostrati altri frammenti e nella cronologia il messaggio risulta «interrotto».
- Dato un processo di traduzione in nuovo PDF annullato durante la composizione, quando si controlla il file system, allora non esistono né il file di destinazione né file temporanei dell'operazione.
- Dato un riassunto in corso su un documento lungo, quando l'utente scorre il documento e aggiunge un'evidenziazione, allora l'interfaccia risponde e l'operazione prosegue.

**Priorità:** P1

### AI-18 — Cronologia locale delle conversazioni

**Descrizione:** Le conversazioni e i risultati IA vengono conservati solo sul computer dell'utente e associati al documento. Si possono consultare, cercare, esportare ed eliminare. La cronologia si può disattivare o far scadere automaticamente.

**Flusso:**
1. Ogni conversazione (chat, riassunti, traduzioni, spiegazioni, estrazioni, mappe) viene salvata al termine di ogni messaggio.
2. Quando si riapre un documento, il pannello propone di riprendere l'ultima conversazione. La scheda "Cronologia" elenca le conversazioni del documento, mentre la vista globale le elenca tutte; si possono cercare per testo, data e funzione.
3. Azioni disponibili: rinomina, esporta (Markdown o JSON), elimina una conversazione, elimina tutte quelle del documento, elimina tutto.
4. Se l'utente riprende una conversazione e fa una nuova domanda, i messaggi precedenti vengono inviati di nuovo; la riga "Verrà inviato" lo dichiara.
5. Impostazioni: cronologia attiva o disattiva, scadenza automatica (mai, 30, 90 o 365 giorni), cifratura facoltativa con la chiave conservata nel portachiavi.

**Formati/strutture:** Archivio nella cartella dati dell'utente, leggibile solo dall'utente. Conversazione = {id, titolo, impronta del contenuto del documento, ultimo percorso noto, funzione, profilo, modello, data di creazione, data di aggiornamento, messaggi (struttura di AI-03)}. Nessuna sincronizzazione e nessun invio a terzi.

**Casi limite:**
- Documento rinominato o spostato: l'associazione viene mantenuta tramite l'impronta del contenuto.
- Documento modificato: le conversazioni restano; i riferimenti di pagina vengono marcati come possibilmente non aggiornati.
- Archivio danneggiato: l'app si avvia ugualmente, isola il file danneggiato e lo segnala, senza perdere le altre conversazioni.
- Spazio su disco esaurito: compare un avviso; la conversazione corrente resta in memoria fino alla chiusura.
- Cronologia disattivata: nulla viene scritto su disco; la conversazione corrente resta disponibile finché il pannello è aperto.

**Criteri di accettazione:**
- Dato un documento con una conversazione di tre messaggi, quando l'app viene riavviata e il documento riaperto, allora la conversazione è nella cronologia con gli stessi messaggi e gli stessi riferimenti.
- Dato il salvataggio della cronologia disattivato, quando l'utente completa una chat, allora nella cartella dati non viene creato né modificato alcun file di cronologia.
- Dato il comando «Elimina tutto» eseguito, quando si ispeziona l'archivio, allora non contiene più alcun testo delle conversazioni.
- Dato un periodo di conservazione di 30 giorni e una conversazione di 31 giorni prima, quando l'app si avvia, allora quella conversazione viene eliminata.
- Dato un documento rinominato, quando l'utente lo apre, allora le conversazioni precedenti sono ancora associate al documento.

**Priorità:** P1

### AI-19 — Scrittura assistita di nuovi testi

**Descrizione:** Genera testi nuovi a partire da una richiesta (lettera, e-mail, paragrafo, articolo, introduzione, conclusioni), basandosi anche sul documento aperto se l'utente lo sceglie. Il testo si può inserire nel PDF come nuova casella di testo o diventare un nuovo documento.

**Flusso:**
1. Pannello IA › "Scrivi". L'utente indica tipo di testo, argomento o istruzioni, tono, lunghezza e lingua, e se usare il documento come fonte.
2. Il testo generato compare nel pannello e si può modificare.
3. Azioni disponibili: copia; "Inserisci nel documento", con cui l'utente fa clic sul punto della pagina e viene creata una casella di testo con lo stile predefinito della modifica testo; "Crea nuovo PDF", tramite la creazione da testo; rigenera.

**Formati/strutture:** La formattazione leggera (titoli, elenchi, grassetto) viene convertita nella formattazione della casella di testo. Il nuovo PDF ha i caratteri incorporati.

**Casi limite:**
- Testo più lungo dello spazio disponibile sulla pagina: l'app propone di proseguire su una nuova pagina o di accorciare il testo.
- Richiesta vuota: il comando è disattivato.
- Documento usato come fonte ma privo di testo: si applica AI-15.

**Criteri di accettazione:**
- Dato il fornitore simulato che restituisce «Gentile cliente, …», quando l'utente sceglie «Inserisci nel documento» e fa clic su un punto della pagina 2, allora in quella posizione della pagina 2 compare una nuova casella di testo con quel testo, rimovibile con «Annulla».
- Dato un testo generato, quando l'utente sceglie «Crea nuovo PDF», allora si apre un nuovo documento il cui testo estratto coincide con il testo generato.

**Priorità:** P2

### AI-20 — Modelli di attività per analisi specialistiche

**Descrizione:** Una libreria di attività predefinite e personalizzabili applica istruzioni già pronte al documento, alle pagine o alla selezione. Copre esigenze specifiche: analisi di contratti e dei relativi rischi, bozza di contratto, spiegazione di bilanci e rendiconti finanziari, analisi dei flussi di cassa, analisi di articoli scientifici, riassunto di documentazione tecnica, lettura e valutazione di curricula rispetto a requisiti, generazione di quiz, soluzione guidata di esercizi di matematica, supporto allo studio, piano d'impresa, analisi strategica, contenuti per video (titoli, descrizioni, parole chiave, copione).

**Flusso:**
1. Pannello IA › "Attività": elenco diviso per categoria (Studio, Ricerca, Legale, Finanza, Personale, Tecnico, Comunicazione), con ricerca.
2. L'utente sceglie un'attività, compila gli eventuali parametri (ad esempio i requisiti del ruolo per i curricula, il numero e il tipo di domande per un quiz) e l'ambito.
3. Il risultato compare nel pannello con i riferimenti di pagina e le azioni standard (AI-03). I quiz si possono anche esportare con le soluzioni in una sezione separata.
4. Gestione delle attività personali: crea, duplica, modifica, elimina, importa ed esporta.

**Formati/strutture:** Attività = {nome, categoria, descrizione, istruzioni con segnaposto (selezione, pagine, lingua, parametri), ambito richiesto, parametri [{nome, tipo, valore predefinito}], forma dell'uscita (testo, tabella, elenco di domande)}. Il file di scambio è in JSON. Quiz = elenco di {domanda, opzioni, risposta corretta, spiegazione, pagina}.

**Casi limite:**
- Attività in ambito legale, finanziario, sanitario o di selezione del personale: il risultato riporta l'avviso che non sostituisce il parere di un professionista né una decisione presa da una persona.
- Valutazione di curricula: l'attività esclude i dati personali non pertinenti al ruolo (età, stato civile, foto) e lo dichiara nelle istruzioni.
- File di attività importato non valido o con segnaposto sconosciuti: l'importazione viene rifiutata, con il dettaglio dell'errore.
- Attività che richiede immagini con un modello che non le accetta: disattivata, con una spiegazione.

**Criteri di accettazione:**
- Dato il modello di attività «Quiz» impostato su 5 domande a scelta multipla e il fornitore simulato che restituisce 5 domande conformi, quando l'utente esporta, allora il file contiene 5 domande e le soluzioni in una sezione separata.
- Dato un modello di attività personale esportato e poi reimportato su un'altra installazione, quando l'utente lo esegue, allora le istruzioni inviate sono identiche a quelle dell'originale.
- Dato un modello di attività della categoria Legale, quando il risultato viene mostrato, allora contiene l'avviso che non sostituisce il parere di un professionista.

**Priorità:** P2

### AI-21 — Ricerca per significato nel documento

**Descrizione:** Oltre alla ricerca testuale esatta, l'utente può cercare per concetto (sinonimi, termini correlati, parafrasi). Ottiene un elenco di passaggi ordinati per pertinenza, ciascuno con pagina ed estratto, e dai risultati può avviare azioni sul testo trovato.

**Flusso:**
1. Nella barra di ricerca l'utente attiva la modalità "Per significato" e scrive la ricerca.
2. L'app usa l'indice dei passaggi (AI-14) e, se disponibile, il modello per vettori semantici. In sua assenza chiede al modello di scegliere i passaggi pertinenti tra quelli candidati.
3. I risultati compaiono con l'estratto evidenziato e la pagina; un clic porta al passaggio.
4. Da un risultato l'utente può: evidenziare, copiare, chiedere all'IA, aprire la sostituzione del testo (area Modifica testo), oscurare o eliminare il testo (area Sicurezza).

**Formati/strutture:** Risultato = {pagina, rettangoli del passaggio, estratto, punteggio di pertinenza}. I vettori semantici sono conservati nella cache locale del documento.

**Casi limite:**
- Nessun risultato pertinente: compare un messaggio dedicato con la proposta di una ricerca esatta.
- Profilo senza modello per vettori: si usa la modalità alternativa basata sul modello di chat, con un avviso che servono più richieste.
- Documento modificato: l'indice viene aggiornato prima della ricerca.

**Criteri di accettazione:**
- Dato il fornitore simulato per vettori che rende vicini «automobile» e «vettura», quando l'utente cerca «automobile» in un documento che contiene solo «vettura», alla pagina 7, allora il primo risultato rimanda alla pagina 7.
- Dato un risultato della ricerca, quando l'utente vi fa clic, allora viene visualizzata la pagina con il passaggio evidenziato.

**Priorità:** P2

### AI-22 — Segnalibri generati e riassunto per sezione

**Descrizione:** Propone un albero di segnalibri a più livelli ricavato dalla struttura del contenuto (titoli, capitoli, sezioni) e permette di riassumere la sezione che corrisponde a un segnalibro.

**Flusso:**
1. Pannello Segnalibri › "Genera con IA": l'utente imposta l'ambito e la profondità massima.
2. L'app ricava dal testo i titoli candidati, con dimensione e posizione, e chiede al modello di ordinarli in una gerarchia, indicando la pagina di destinazione di ciascuno.
3. L'anteprima si può modificare. "Applica" chiede se sostituire i segnalibri esistenti o unirli a quelli nuovi; l'operazione si può annullare.
4. Clic destro su un segnalibro › "Riassumi sezione": riassunto delle pagine che vanno da quel segnalibro al successivo dello stesso livello (AI-05).

**Formati/strutture:** Segnalibri standard del PDF, con destinazione alla pagina e alla posizione verticale del titolo. Proposta = albero di {titolo, pagina, posizione, figli[]}.

**Casi limite:**
- Pagina di destinazione proposta inesistente: la voce viene scartata e segnalata.
- Titolo proposto non trovato sulla pagina: la destinazione diventa l'inizio della pagina.
- Documento senza una struttura riconoscibile: l'app propone segnalibri ogni N pagine oppure segnala che non è possibile generarli.
- Ultimo segnalibro: la sua sezione arriva fino alla fine del documento.

**Criteri di accettazione:**
- Dato il fornitore simulato che propone 3 capitoli con 2 sottosezioni ciascuno, quando l'utente applica scegliendo «Sostituisci», allora il documento ha 3 segnalibri di primo livello e 6 di secondo livello con le destinazioni proposte, e «Annulla» ripristina i segnalibri precedenti.
- Dato un documento di 12 pagine con segnalibri alle pagine 1, 5 e 9, quando l'utente riassume la sezione del secondo segnalibro, allora la richiesta contiene solo il testo delle pagine da 5 a 8.

**Priorità:** P2

### AI-23 — Assistente comandi

**Descrizione:** L'utente descrive a parole cosa vuole ottenere (ad esempio "ruota di 90° le pagine 3–5", "proteggi con password", "estrai le pagine pari"). L'assistente individua lo strumento di Foliant adatto, ne compila i parametri e lo apre. L'operazione viene eseguita solo quando l'utente la conferma nello strumento stesso.

**Flusso:**
1. L'utente scrive la richiesta nel campo comandi del pannello, aperto anche con una scorciatoia.
2. L'app invia la richiesta e il catalogo degli strumenti disponibili con i relativi parametri. Il contenuto del documento non viene inviato; si inviano solo il numero di pagine e le pagine della selezione corrente.
3. L'assistente propone uno o più strumenti con i parametri; l'utente sceglie.
4. Lo strumento si apre con i parametri già compilati. L'utente li controlla ed esegue; l'operazione si può annullare come qualsiasi altra.

**Formati/strutture:** Catalogo degli strumenti = {id, descrizione, parametri con tipo e vincoli}. Proposta = {id dello strumento, parametri, spiegazione}. L'app accetta solo proposte che rispettano il catalogo.

**Casi limite:**
- Proposta con uno strumento inesistente o con parametri non validi (ad esempio pagine oltre la fine del documento): viene scartata e un messaggio suggerisce alternative.
- Richiesta ambigua: l'app mostra un elenco di opzioni.
- Operazioni irreversibili sul file (sovrascrittura, oscuramento, rimozione della protezione): richiedono la stessa doppia conferma prevista quando l'utente usa lo strumento a mano.
- Testo nel documento che cerca di dare istruzioni all'assistente: non ha effetto, perché per questa funzione il contenuto non viene inviato e nessuna azione parte senza conferma.

**Criteri di accettazione:**
- Dato il fornitore simulato che propone lo strumento «ruota» con le pagine 3–5 e 90°, quando l'utente sceglie la proposta, allora si apre il dialogo di rotazione già compilato e nessuna pagina viene ruotata finché l'utente non conferma.
- Dato il simulato che propone le pagine 30–35 per un documento di 10 pagine, quando arriva la proposta, allora viene scartata con un messaggio e nessuno strumento si apre.
- Dato un comando inviato all'assistente, quando si esamina la richiesta registrata dal simulato, allora non contiene testo del documento.

**Priorità:** P2

### AI-24 — Analisi e pulizia guidata delle pagine

**Descrizione:** Esamina il documento e segnala pagine bianche o quasi bianche, pagine separatrici o non pertinenti, orientamento e formato non uniformi, pagine con annotazioni e pagine che richiedono l'OCR. Propone le correzioni, che l'utente applica in modo selettivo.

**Flusso:**
1. Menu Pagine › "Analizza pagine".
2. L'app esegue controlli locali (pagine bianche, orientamento del testo, formati diversi, presenza di annotazioni, assenza di testo). Su richiesta aggiunge una classificazione IA del contenuto (ad esempio pagina di trasmissione, separatore, pagina duplicata).
3. Il rapporto mostra le miniature raggruppate per problema, ciascuna con un'azione proposta: elimina, ruota, uniforma il formato, estrai le annotazioni, esegui l'OCR.
4. L'utente seleziona le azioni e le applica. Le azioni vengono eseguite dagli strumenti dell'area Pagine e si annullano tutte insieme con un solo comando.

**Formati/strutture:** Voce del rapporto = {pagina, problema, evidenza (ad esempio la percentuale di pagina coperta), azione proposta, origine (locale o IA)}. Per la classificazione IA si inviano solo le pagine candidate, come testo o come immagine a bassa risoluzione, e la riga "Verrà inviato" lo dichiara.

**Casi limite:**
- Pagina quasi bianca che contiene solo il numero di pagina o un timbro: viene segnalata con l'evidenza, ma non viene eliminata automaticamente.
- Nessun problema trovato: compare un messaggio esplicito.
- Profilo IA non configurato: sono disponibili solo i controlli locali.

**Criteri di accettazione:**
- Dato un PDF di 6 pagine con la pagina 4 bianca e la pagina 5 ruotata di 90°, quando l'utente avvia l'analisi senza classificazione IA, allora il rapporto segnala la pagina 4 come bianca e la pagina 5 come orientata diversamente, e il fornitore simulato non riceve richieste.
- Dato il rapporto di questa analisi, quando l'utente applica «Elimina» alla pagina 4, allora il documento ha 5 pagine e un solo «Annulla» lo ripristina.

**Priorità:** P2

### AI-25 — Generazione di elementi grafici

**Descrizione:** Genera da una descrizione testuale immagini da usare come filigrana, sfondo, timbro o adesivo. L'utente sceglie tra alcune varianti, applica quella scelta con lo strumento corrispondente e la ritrova nella propria libreria.

**Flusso:**
1. Negli strumenti Filigrana, Sfondo, Timbri e Adesivi l'utente sceglie "Genera con IA".
2. Descrive l'elemento e sceglie stile, colori, trasparenza e numero di varianti (da 1 a 4).
3. Le varianti compaiono in anteprima. L'utente ne sceglie una oppure le rigenera.
4. L'elemento scelto passa allo strumento corrispondente per il posizionamento e l'applicazione e viene salvato nella libreria personale.

**Formati/strutture:** Immagini PNG con trasparenza per timbri e adesivi. Risoluzione minima adatta alla stampa nella dimensione scelta (ad esempio 300 punti per pollice). Per i timbri testuali è disponibile anche una variante vettoriale composta localmente.

**Casi limite:**
- Profilo senza un modello per la generazione di immagini: la funzione è disattivata, con una spiegazione.
- Richiesta rifiutata dal fornitore in base alle sue regole sui contenuti: compare un messaggio dedicato (AI-16).
- Immagine generata senza trasparenza per un timbro: l'app propone di rimuovere localmente lo sfondo uniforme.

**Criteri di accettazione:**
- Dato il fornitore simulato di immagini che restituisce 3 varianti, quando l'utente ne sceglie una per «Timbro», allora lo strumento Timbri si apre con quell'immagine e, dopo l'applicazione, la pagina contiene un'annotazione timbro con quell'aspetto.
- Dato un profilo senza capacità di generazione di immagini, quando l'utente apre lo strumento Filigrana, allora «Genera con IA» è disattivato, con una spiegazione.

**Priorità:** P2

### AI-26 — Ricerca bibliografica e revisione della letteratura

**Descrizione:** A partire da un argomento o da una domanda di ricerca, l'assistente divide il quesito in sotto-domande e cerca pubblicazioni in un servizio bibliografico pubblico configurato dall'utente. Mostra metadati e sintesi dei risultati e permette di farvi domande. Produce poi una revisione della letteratura, con bibliografia, salvabile in PDF.

**Flusso:**
1. Pannello IA › "Ricerca bibliografica". L'utente sceglie il servizio bibliografico, configurato nelle impostazioni senza passare da server di Foliant, e scrive la domanda.
2. L'assistente propone le sotto-domande, che l'utente può modificare. Per le ricerche l'app invia al servizio solo le parole chiave.
3. Per ogni risultato compaiono titolo, autori, anno, sede di pubblicazione, identificativo, abstract, collegamento all'eventuale versione ad accesso aperto e un riassunto.
4. L'utente salva le voci nella libreria locale e apre i PDF disponibili, che si possono poi usare anche con AI-13. Infine avvia "Genera revisione".
5. La revisione (introduzione, sintesi per tema, lacune, bibliografia) si può modificare ed esportare in PDF e in un formato bibliografico standard.

**Formati/strutture:** Voce bibliografica = {titolo, autori, anno, sede di pubblicazione, identificativo persistente, abstract, URL}. Esportazione nei formati bibliografici aperti di uso comune. Revisione in PDF con riferimenti numerati.

**Casi limite:**
- Nessun servizio bibliografico configurato: la funzione è disattivata.
- Citazioni della revisione che non corrispondono a voci della libreria: vengono rimosse o marcate "non verificata". La bibliografia contiene solo voci realmente recuperate.
- Servizio non raggiungibile o con limiti di utilizzo: si applica AI-16.
- Nessun risultato: l'app suggerisce di riformulare le sotto-domande.

**Criteri di accettazione:**
- Dato il servizio bibliografico simulato con 4 voci e il fornitore simulato che cita anche una quinta voce inesistente, quando viene generata la revisione, allora la bibliografia contiene solo le 4 voci reali e la citazione inesistente è marcata «non verificata».
- Dato un processo di ricerca bibliografica avviato con documenti aperti, quando si esaminano le richieste al servizio bibliografico, allora contengono solo le parole chiave delle sotto-domande e nessun contenuto dei documenti aperti.

**Priorità:** P2

### AI-27 — Conversione assistita dall'IA

**Descrizione:** Nella conversione in documento di testo, foglio di calcolo o presentazione, l'utente può attivare l'assistenza IA per ricostruire meglio la struttura: titoli e relativi livelli, elenchi, ordine di lettura su più colonne, tabelle senza bordi, celle unite. La conversione resta eseguita localmente dal motore di conversione.

**Flusso:**
1. Nel dialogo di conversione l'utente attiva l'opzione "Migliora la struttura con l'IA", visibile solo con un profilo attivo.
2. Per le pagine da convertire l'app invia i blocchi di testo con la loro posizione e il loro stile, non il file.
3. Il modello restituisce il ruolo di ogni blocco e la struttura delle tabelle. L'app la convalida e la usa nella conversione locale.
4. Il file convertito viene salvato. Un rapporto indica le pagine in cui l'IA non è stata usata.

**Formati/strutture:** Blocco inviato = {pagina, riquadro, testo, carattere, corpo, stile}. Risposta = {blocco, ruolo (titolo di livello N, paragrafo, elenco, didascalia, cella), tabella: righe, colonne, celle unite}.

**Casi limite:**
- Risposta incoerente (blocchi inesistenti, celle sovrapposte): viene ignorata per quella pagina, che segue la conversione standard.
- Errore di rete: la conversione si completa senza assistenza IA, con un avviso.

**Criteri di accettazione:**
- Dato il fornitore simulato che classifica un blocco come «titolo di livello 1», quando si converte in documento di testo, allora quel testo ha lo stile di titolo di livello 1 nel file prodotto.
- Dato il simulato non raggiungibile, quando si converte con l'opzione attiva, allora il file viene prodotto comunque e il rapporto indica che l'assistenza IA non è stata usata.

**Priorità:** P2

## Appendice — Matrice delle priorità

| ID | Funzione | Priorità | Sezione |
|---|---|---|---|
| VIS-01 | Schermata Home | P0 | 1. Visualizzazione e navigazione |
| VIS-02 | File recenti e preferiti | P0 | 1. Visualizzazione e navigazione |
| VIS-03 | Gestione dei file locali | P1 | 1. Visualizzazione e navigazione |
| VIS-04 | Archivi compressi | P2 | 1. Visualizzazione e navigazione |
| VIS-05 | Apertura dei documenti | P0 | 1. Visualizzazione e navigazione |
| VIS-06 | PDF protetti: password e permessi | P0 | 1. Visualizzazione e navigazione |
| VIS-07 | File danneggiati e contenuti non supportati | P1 | 1. Visualizzazione e navigazione |
| VIS-08 | Resa delle pagine e documenti di grandi dimensioni | P0 | 1. Visualizzazione e navigazione |
| VIS-09 | Modalità di visualizzazione delle pagine | P0 | 1. Visualizzazione e navigazione |
| VIS-10 | Zoom | P0 | 1. Visualizzazione e navigazione |
| VIS-11 | Rotazione della vista | P1 | 1. Visualizzazione e navigazione |
| VIS-12 | Navigazione tra le pagine | P0 | 1. Visualizzazione e navigazione |
| VIS-13 | Cronologia di navigazione (Indietro/Avanti) | P1 | 1. Visualizzazione e navigazione |
| VIS-14 | Pannello miniature | P0 | 1. Visualizzazione e navigazione |
| VIS-15 | Segnalibri: consultazione | P0 | 1. Visualizzazione e navigazione |
| VIS-16 | Segnalibri: creazione e modifica | P1 | 1. Visualizzazione e navigazione |
| VIS-17 | Segnalibri: generazione automatica e strumenti avanzati | P2 | 1. Visualizzazione e navigazione |
| VIS-18 | Riassunto di una sezione dal segnalibro (IA) | P2 | 1. Visualizzazione e navigazione |
| VIS-19 | Collegamenti e azioni di navigazione | P0 | 1. Visualizzazione e navigazione |
| VIS-20 | Livelli (contenuto opzionale): visualizzazione | P1 | 1. Visualizzazione e navigazione |
| VIS-21 | Livelli: gestione | P2 | 1. Visualizzazione e navigazione |
| VIS-22 | Allegati e portfolio: consultazione | P1 | 1. Visualizzazione e navigazione |
| VIS-23 | Thread di articoli | P2 | 1. Visualizzazione e navigazione |
| VIS-24 | Ricerca nel documento | P0 | 1. Visualizzazione e navigazione |
| VIS-25 | Ricerca avanzata e su più documenti | P2 | 1. Visualizzazione e navigazione |
| VIS-26 | Trova e sostituisci, Trova ed elimina | P1 | 1. Visualizzazione e navigazione |
| VIS-27 | Ricerca semantica (IA) | P2 | 1. Visualizzazione e navigazione |
| VIS-28 | Selezione e copia del testo | P0 | 1. Visualizzazione e navigazione |
| VIS-29 | Istantanea di un'area | P1 | 1. Visualizzazione e navigazione |
| VIS-30 | Schede, finestre e sessioni | P0 | 1. Visualizzazione e navigazione |
| VIS-31 | Modalità lettura e schermo intero | P1 | 1. Visualizzazione e navigazione |
| VIS-32 | Presentazione | P1 | 1. Visualizzazione e navigazione |
| VIS-33 | Scorrimento automatico | P1 | 1. Visualizzazione e navigazione |
| PAG-01 | Organizzatore pagine a griglia | P0 | 2. Gestione pagine |
| PAG-02 | Selezione e sintassi degli intervalli di pagine | P0 | 2. Gestione pagine |
| PAG-03 | Integrità dei riferimenti nelle operazioni di pagina | P0 | 2. Gestione pagine |
| PAG-04 | Riordino pagine con trascinamento e comandi di spostamento | P0 | 2. Gestione pagine |
| PAG-05 | Inverti, scambia e fascicola pagine | P1 | 2. Gestione pagine |
| PAG-06 | Inserisci pagina vuota | P0 | 2. Gestione pagine |
| PAG-07 | Inserisci pagine da altro file | P0 | 2. Gestione pagine |
| PAG-08 | Inserisci pagine da immagini | P1 | 2. Gestione pagine |
| PAG-09 | Inserisci dagli appunti | P1 | 2. Gestione pagine |
| PAG-10 | Inserisci da scanner | P1 | 2. Gestione pagine |
| PAG-11 | Elimina pagine | P0 | 2. Gestione pagine |
| PAG-12 | Estrai pagine in PDF | P0 | 2. Gestione pagine |
| PAG-13 | Estrai pagine come immagini | P1 | 2. Gestione pagine |
| PAG-14 | Trascinamento di pagine tra documenti e verso il file manager | P1 | 2. Gestione pagine |
| PAG-15 | Duplica pagine | P0 | 2. Gestione pagine |
| PAG-16 | Copia, taglia e incolla pagine | P1 | 2. Gestione pagine |
| PAG-17 | Condividi pagine selezionate | P1 | 2. Gestione pagine |
| PAG-18 | Ruota pagine | P0 | 2. Gestione pagine |
| PAG-19 | Sostituisci pagine | P1 | 2. Gestione pagine |
| PAG-20 | Dividi documento per intervalli o ogni N pagine | P0 | 2. Gestione pagine |
| PAG-21 | Dividi per segnalibri | P1 | 2. Gestione pagine |
| PAG-22 | Dividi per dimensione massima del file | P1 | 2. Gestione pagine |
| PAG-23 | Dividi per pagine separatrici o per testo | P2 | 2. Gestione pagine |
| PAG-24 | Unisci più file | P0 | 2. Gestione pagine |
| PAG-25 | Ritaglia pagine | P1 | 2. Gestione pagine |
| PAG-26 | Rimuovi margini bianchi | P1 | 2. Gestione pagine |
| PAG-27 | Riquadri di stampa (abbondanza, rifilo, area grafica) | P2 | 2. Gestione pagine |
| PAG-28 | Ridimensiona e cambia formato pagina | P1 | 2. Gestione pagine |
| PAG-29 | Dividi una pagina in più pagine | P1 | 2. Gestione pagine |
| PAG-30 | Pagine affiancate (n-up) | P1 | 2. Gestione pagine |
| TXT-01 | Modalità Modifica e riconoscimento dei blocchi di testo | P0 | 3. Modifica testo |
| TXT-02 | Modifica in loco del testo esistente | P0 | 3. Modifica testo |
| TXT-03 | Ritorno a capo e riflusso nel riquadro | P0 | 3. Modifica testo |
| TXT-04 | Formattazione dei caratteri | P0 | 3. Modifica testo |
| TXT-05 | Formattazione del paragrafo | P0 | 3. Modifica testo |
| TXT-06 | Aggiunta di nuove caselle di testo | P0 | 3. Modifica testo |
| TXT-07 | Spostamento, ridimensionamento, copia ed eliminazione dei blocchi | P0 | 3. Modifica testo |
| TXT-08 | Disposizione avanzata dei blocchi | P1 | 3. Modifica testo |
| TXT-09 | Unione e divisione dei blocchi | P1 | 3. Modifica testo |
| TXT-10 | Font non incorporati o incorporati come sottoinsieme | P0 | 3. Modifica testo |
| TXT-11 | Elenco dei font del documento e sostituzione globale | P1 | 3. Modifica testo |
| TXT-12 | Trova e sostituisci | P0 | 3. Modifica testo |
| TXT-13 | Trova ed elimina | P1 | 3. Modifica testo |
| TXT-14 | Controllo ortografico | P1 | 3. Modifica testo |
| TXT-15 | Modifica del testo riconosciuto con OCR | P0 | 3. Modifica testo |
| TXT-16 | Revisione delle parole OCR incerte | P1 | 3. Modifica testo |
| TXT-17 | Annulla e ripeti | P0 | 3. Modifica testo |
| TXT-18 | Selezione, copia e incolla del testo, anche tra pagine | P0 | 3. Modifica testo |
| TXT-19 | Inserimento di testo formattato da trascinamento o dagli appunti | P1 | 3. Modifica testo |
| TXT-20 | Salvaguardie d'integrità durante la modifica | P0 | 3. Modifica testo |
| TXT-21 | Scritture complesse: bidirezionali, con composizione dei glifi e verticali | P2 | 3. Modifica testo |
| TXT-22 | Modifica del testo con istruzioni in linguaggio naturale (IA) | P2 | 3. Modifica testo |
| TXT-23 | Correzione bozze con IA | P2 | 3. Modifica testo |
| TXT-24 | Espandi e condensa con IA | P2 | 3. Modifica testo |
| TXT-25 | Rifinisci e cambia tono con IA | P2 | 3. Modifica testo |
| OBJ-01 | Modalità di modifica degli oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-02 | Selezione di immagini e oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-03 | Pannello proprietà e trasformazione numerica | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-04 | Inserire immagini da file | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-05 | Inserire immagini dagli appunti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-06 | Inserire immagini trascinandole | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-07 | Formati immagine aggiuntivi | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-08 | Spostare oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-09 | Ridimensionare con o senza proporzioni | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-10 | Ruotare oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-11 | Capovolgere oggetti | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-12 | Inclinare e reimpostare la trasformazione | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-13 | Ritagliare immagini | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-14 | Sostituire immagini | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-15 | Eliminare oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-16 | Opacità | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-17 | Regolazioni dell'immagine | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-18 | Ricompressione e ricampionamento delle immagini selezionate | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-19 | Modifica in un'applicazione esterna | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-20 | Ordine di sovrapposizione | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-21 | Allineare oggetti | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-22 | Distribuire oggetti | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-23 | Guide intelligenti, griglia e aggancio | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-24 | Tracciati vettoriali esistenti: selezione, spostamento ed eliminazione | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-25 | Stile dei tracciati vettoriali | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-26 | Oggetti composti: scomporre e raggruppare | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-27 | Estrarre o salvare l'immagine selezionata | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-28 | Estrarre tutte le immagini del documento | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-29 | Catturare un'area come immagine o testo | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-30 | Copiare, tagliare, incollare e duplicare oggetti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-31 | Spostare oggetti tra pagine | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-32 | Replicare oggetti su più pagine | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-33 | Creare un collegamento (area rettangolare) | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-34 | Creare un collegamento dal testo selezionato | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-35 | Destinazione: indirizzo web o e-mail | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-36 | Destinazione: pagina interna o destinazione con nome | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-37 | Destinazione: file esterno | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-38 | Modificare collegamenti esistenti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-39 | Eliminare collegamenti | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-40 | Rilevamento automatico degli URL nel testo | P1 | 4. Immagini, oggetti e collegamenti |
| OBJ-41 | Attivazione dei collegamenti e sicurezza | P0 | 4. Immagini, oggetti e collegamenti |
| OBJ-42 | Verifica dei collegamenti | P2 | 4. Immagini, oggetti e collegamenti |
| OBJ-43 | Annulla e ripeti | P0 | 4. Immagini, oggetti e collegamenti |
| CNV-01 | Esporta come: finestra di conversione e opzioni comuni | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-02 | Conversione di PDF scansionati con OCR automatico | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-03 | PDF → DOCX (documento modificabile) | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-04 | PDF → XLSX (tabelle in foglio di calcolo) | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-05 | PDF → CSV | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-06 | PDF → PPTX (presentazione) | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-07 | PDF → immagini (PNG, JPEG, TIFF, BMP, GIF) | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-08 | PDF → immagine lunga | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-09 | PDF → testo semplice (TXT) | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-10 | PDF → RTF | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-11 | PDF → HTML | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-12 | PDF → XML | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-13 | PDF → EPUB | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-14 | Conversione in PDF/A (1b, 2b, 3b) | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-15 | Altri profili ISO: PDF/A livelli u e a, PDF/A-4, PDF/X, PDF/E | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-16 | Conversione avanzata assistita da IA | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-17 | Conversione in blocco di più file | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-18 | Crea PDF da file Office | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-19 | Crea PDF da immagini | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-20 | Crea PDF da testo, Markdown, RTF e CSV | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-21 | Crea PDF da HTML e pagine web | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-22 | Crea PDF dagli appunti | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-23 | Crea PDF vuoto | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-24 | Crea PDF da scanner | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-25 | Crea PDF da cattura dello schermo | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-26 | Crea PDF da fotocamera o da foto di documenti | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-27 | Stampante virtuale PDF | P2 | 6. Conversione, creazione e ottimizzazione |
| CNV-28 | Creazione di PDF in blocco | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-29 | Comprimi PDF | P0 | 6. Conversione, creazione e ottimizzazione |
| CNV-30 | Ottimizzazione avanzata e analisi dello spazio | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-31 | Linearizzazione per la visualizzazione web veloce | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-32 | Ripara PDF danneggiati | P1 | 6. Conversione, creazione e ottimizzazione |
| CNV-33 | Appiattisci (flatten) | P1 | 6. Conversione, creazione e ottimizzazione |
| OCR-01 | Rilevamento automatico delle pagine senza testo e proposta di OCR all'apertura | P0 | 7. OCR e PDF scannerizzati |
| OCR-02 | Motore OCR locale e offline | P0 | 7. OCR e PDF scannerizzati |
| OCR-03 | Lingue di riconoscimento: selezione multipla e gestione dei pacchetti | P0 | 7. OCR e PDF scannerizzati |
| SIG-01 | Firma disegnata a mano | P0 | 9. Firma |
| SIG-02 | Firma digitata con stile calligrafico | P0 | 9. Firma |
| SIG-03 | Firma da immagine o scansione con rimozione dello sfondo | P0 | 9. Firma |
| SIG-04 | Libreria locale di firme e iniziali | P0 | 9. Firma |
| SIG-05 | Posizionamento e ridimensionamento della firma grafica | P0 | 9. Firma |
| SIG-06 | Ripetizione della firma o delle iniziali su più pagine | P1 | 9. Firma |
| SIG-07 | Esportazione, importazione e sincronizzazione della libreria firme | P2 | 9. Firma |
| SIG-08 | Firma digitale con certificato da file protetto da password | P0 | 9. Firma |
| SIG-09 | Aspetto personalizzabile della firma digitale | P1 | 9. Firma |
| SIG-10 | Marca temporale da server configurabile | P1 | 9. Firma |
| SIG-11 | Firme multiple in sequenza | P0 | 9. Firma |
| SIG-12 | Firma di certificazione con permessi di modifica | P1 | 9. Firma |
| SIG-13 | Creazione di un ID digitale autofirmato | P1 | 9. Firma |
| SIG-14 | Gestione degli ID digitali e dei certificati attendibili | P1 | 9. Firma |
| SIG-15 | Verifica di integrità e identità del firmatario | P0 | 9. Firma |
| SIG-16 | Controllo della revoca dei certificati | P1 | 9. Firma |
| SIG-17 | Rilevamento delle modifiche successive alla firma | P0 | 9. Firma |
| SIG-18 | Pannello firme e indicatori di stato | P0 | 9. Firma |
| SIG-19 | Campi firma vuoti | P1 | 9. Firma |
| SIG-20 | Invito a firmare | P1 | 9. Firma |
| SIG-21 | Avviso di invalidazione nei documenti firmati | P0 | 9. Firma |
| SIG-22 | Livelli PAdES, validazione a lungo termine e marca temporale di documento | P2 | 9. Firma |
| SIG-23 | Firma con smart card o token crittografico | P2 | 9. Firma |
| SIG-24 | Richiesta di firma a più firmatari | P2 | 9. Firma |
| SIG-25 | Gestione avanzata dei firmatari e criteri organizzativi | P2 | 9. Firma |
| CMP-01 | Avvio del confronto e scelta delle versioni | P0 | 11. Confronto e operazioni in blocco |
| CMP-02 | Confronto del contenuto testuale | P0 | 11. Confronto e operazioni in blocco |
| CMP-03 | Confronto della formattazione del testo | P1 | 11. Confronto e operazioni in blocco |
| CMP-04 | Confronto visivo delle pagine | P0 | 11. Confronto e operazioni in blocco |
| CMP-05 | Vista affiancata con scorrimento sincronizzato | P0 | 11. Confronto e operazioni in blocco |
| CMP-06 | Vista sovrapposta (overlay) | P0 | 11. Confronto e operazioni in blocco |
| CMP-07 | Elenco differenze navigabile | P0 | 11. Confronto e operazioni in blocco |
| AI-01 | Configurazione del fornitore di modelli e della chiave API | P1 | 12. Assistente IA |
| AI-02 | Informativa e trasparenza sui dati inviati | P1 | 12. Assistente IA |
| AI-03 | Pannello Assistente, ambito e uso delle risposte | P1 | 12. Assistente IA |
| AI-04 | Chat con il documento e riferimenti alle pagine | P1 | 12. Assistente IA |
| AI-05 | Riassunto con lunghezza regolabile | P1 | 12. Assistente IA |
| AI-06 | Traduzione di selezione e pagine | P1 | 12. Assistente IA |
| AI-07 | Traduzione del documento in un nuovo PDF | P1 | 12. Assistente IA |
| AI-08 | Spiegazione di testo e termini | P1 | 12. Assistente IA |
| AI-09 | Riscrittura e correzione del testo selezionato | P1 | 12. Assistente IA |
| AI-10 | Estrazione di dati strutturati in CSV e JSON | P2 | 12. Assistente IA |
| AI-11 | Chat con immagini e aree della pagina | P1 | 12. Assistente IA |
| AI-12 | Mappa concettuale e scaletta | P1 | 12. Assistente IA |
| AI-13 | Chat su più documenti | P2 | 12. Assistente IA |
| AI-14 | Documenti lunghi: suddivisione ed elaborazione a blocchi | P1 | 12. Assistente IA |
| AI-15 | Documenti scansionati: OCR prima dell'invio | P1 | 12. Assistente IA |
| AI-16 | Errori di rete, di autenticazione e di quota | P1 | 12. Assistente IA |
| AI-17 | Avanzamento e annullamento | P1 | 12. Assistente IA |
| AI-18 | Cronologia locale delle conversazioni | P1 | 12. Assistente IA |
| AI-19 | Scrittura assistita di nuovi testi | P2 | 12. Assistente IA |
| AI-20 | Modelli di attività per analisi specialistiche | P2 | 12. Assistente IA |
| AI-21 | Ricerca per significato nel documento | P2 | 12. Assistente IA |
| AI-22 | Segnalibri generati e riassunto per sezione | P2 | 12. Assistente IA |
| AI-23 | Assistente comandi | P2 | 12. Assistente IA |
| AI-24 | Analisi e pulizia guidata delle pagine | P2 | 12. Assistente IA |
| AI-25 | Generazione di elementi grafici | P2 | 12. Assistente IA |
| AI-26 | Ricerca bibliografica e revisione della letteratura | P2 | 12. Assistente IA |
| AI-27 | Conversione assistita dall'IA | P2 | 12. Assistente IA |
