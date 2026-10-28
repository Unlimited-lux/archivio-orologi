# Foliant — Catalogo delle funzionalità

Versione 1 · 28 ottobre 2026 · Fase 1 (ricerca e specifica) · documento di lavoro

## Introduzione

**Scopo.** Il catalogo raccoglie in un unico elenco deduplicato tutte le funzioni che Foliant, editor PDF desktop per Linux, deve offrire. Il traguardo è la parità funzionale con l'editor commerciale preso come riferimento, **UPDF** (versione 2.5.x, 2026), che non esiste come applicazione nativa per Linux. A questa base si aggiungono le funzioni offerte dai principali concorrenti (Adobe Acrobat, Foxit PDF Editor, PDF-XChange Editor, Nitro PDF Pro, PDFgear, Xodo) e le capacità previste dagli standard aperti del formato (ISO 32000-1/2, PDF/A, PDF/UA, PDF/X, PDF/E, AcroForm e XFA, PAdES). Il catalogo è la base delle specifiche di dettaglio per area e del piano di implementazione.

**Metodo clean-room.** Il contenuto deriva solo da fonti pubbliche: pagine prodotto, guide utente, cronologie delle versioni, schede tecniche e tabelle di confronto dei produttori, recensioni indipendenti e testi delle norme. Nessun programma è stato installato, decompilato o analizzato. Ogni descrizione è scritta da capo con parole proprie, senza riprodurre testi delle fonti, e i nomi delle funzioni sono generici, non marchi. Il prodotto di riferimento è nominato soltanto in questa introduzione e nella legenda; nel resto del documento è indicato come "il riferimento".

**Come è stato costruito.**

1. *Base*: l'inventario di parità del riferimento (189 voci in 16 aree).
2. *Aggiunte*: le schede sulle funzioni dei concorrenti (72) e le capacità dello standard (struttura del file, pagine e navigazione, grafica e testo, annotazioni, azioni e script, moduli, firme digitali).
3. *Deduplicazione*: le voci equivalenti di fonti diverse sono fuse in una sola riga. Se un concorrente o una norma aggiunge solo un'opzione minore a una funzione già presente nel riferimento, l'opzione è indicata nelle Note come "Estensione"; un'aggiunta sostanziale ha invece una riga propria, marcata A.
4. *Collocazione*: ogni funzione compare una sola volta, nell'area in cui l'utente la cercherebbe. Le varianti assistite da IA di uno strumento (segnalibri, timbri, conversioni…) restano nell'area dello strumento; le funzioni che producono contenuti o analisi (riassunti, chat, traduzioni, scrittura, ricerche) stanno nell'area 12.
5. *Ambito*: Foliant lavora in locale e non dipende da servizi online del produttore. Le funzioni che presuppongono un cloud gestito sono elencate nell'area 13 come fuori ambito, con la motivazione. Le funzioni di IA sono in ambito, ma usano un fornitore scelto e configurato dall'utente (modello locale o servizio remoto).

In sintesi: **312 funzioni**, di cui **188 P** e **124 A**; **297** sono in ambito (riepilogo in fondo).

## Legenda

| Colonna | Significato |
|---|---|
| Funzione | Codice (area.progressivo) e nome generico. |
| Descrizione per l'utente | Che cosa ottiene chi usa Foliant. |
| Formati | Formati di file, standard o strutture PDF coinvolti; "—" se non pertinente. |
| Parità | **P** = presente nel prodotto di riferimento (UPDF), secondo l'inventario di parità o la cronologia pubblica delle sue versioni: è obbligatoria per la parità. **A** = aggiunta: non risulta nel riferimento, o vi compare solo in forma più limitata, ed è offerta da almeno un concorrente o prevista da uno standard. |
| Nel riferimento a pagamento | **sì** = nel riferimento serve un piano a pagamento (licenza Pro o componente IA aggiuntivo) per l'uso pieno; il piano gratuito al massimo la concede in prova, con filigrana o con limiti di quantità. **no** = utilizzabile nel piano gratuito. **n.d.** = non ricavabile dalle fonti, oppure non applicabile (tutte le righe A). Il dato orienta solo le priorità: Foliant non distingue piani. |
| Note | Dipendenze e vincoli. Formule ricorrenti: "richiede fornitore IA configurato dall'utente" (la funzione si attiva solo dopo che l'utente ha scelto un modello locale o un servizio remoto); "richiede rete"; "fuori ambito: motivo"; "Estensione: …" (opzione aggiunta da concorrenti o standard a una funzione P); "documentato nella cronologia delle versioni" (funzione P ricavata dalla cronologia del riferimento anziché dall'inventario); "nel riferimento solo su mobile" o "per Windows" (con l'adattamento previsto per Linux). |

## 1. Visualizzazione e navigazione

Schermata iniziale e gestione dei file, lettura, navigazione, ricerca, proprietà, interfaccia, stampa e invio.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 1.01 Schermata iniziale | All'avvio presenta i documenti recenti, l'accesso rapido agli strumenti più usati e alle funzioni di creazione; l'utente sceglie che cosa mostrare all'apertura. | — | P | n.d. | Documentato nella cronologia delle versioni (pagina iniziale personalizzabile). |
| 1.02 Documenti recenti | Elenco dei file aperti di recente, per riaprirli con un clic. | PDF | P | no | — |
| 1.03 Gestione dei file | Rinomina, sposta, copia, duplica ed elimina i documenti e li segna come preferiti senza uscire dall'app. | File locali | P | no | Nel riferimento soprattutto su mobile. Accorpa la voce "Preferiti". |
| 1.04 Cartelle | Crea cartelle e vi organizza i documenti. | — | P | no | Nel riferimento soprattutto su mobile; in Foliant opera sulle cartelle reali del disco. |
| 1.05 Archivi compressi | Raccoglie documenti in un archivio ZIP ed estrae gli archivi ricevuti. | ZIP | P | no | Nel riferimento soprattutto su mobile. |
| 1.06 Integrazione con il file manager | Anteprime e miniature dei PDF nel gestore file del sistema e comandi rapidi dal menu contestuale (aprire, convertire, unire). | PDF | P | no | Nel riferimento per Esplora file di Windows; su Linux tramite generatore di miniature e azioni per i file manager più diffusi. |
| 1.07 Apertura da indirizzo web | Apre un PDF pubblicato online indicandone l'indirizzo e riceve dal browser i PDF da aprire in Foliant. | PDF via HTTP(S) | A | n.d. | Richiede rete. |
| 1.08 Apertura dei documenti | Apre i PDF presenti sul disco per leggerli e modificarli, compresi i file in formato d'archivio. | PDF, PDF/A | P | no | Novità di PDF 2.0: 6.38. |
| 1.09 Schede e gruppi di schede | Tiene aperti più documenti in schede, con passaggio rapido dall'una all'altra e raggruppamento delle schede. | — | P | no | Gruppi di schede documentati nella cronologia delle versioni. |
| 1.10 Modalità di visualizzazione | Pagina singola, scorrimento continuo, due pagine affiancate con o senza copertina separata. | — | P | no | — |
| 1.11 Zoom e adattamento | Ingrandisce e riduce, adatta alla pagina o alla larghezza; zoom predefinito e risoluzione di resa impostabili. | — | P | no | — |
| 1.12 Miniature delle pagine | Pannello con le anteprime delle pagine per orientarsi e saltare subito a una pagina. | Miniature incorporate o calcolate al momento | P | no | Usa le miniature incorporate nel file, se presenti; la loro rimozione è in 6.35. |
| 1.13 Temi di lettura e protezione della vista | Temi chiaro e scuro e colori di pagina riposanti (verde tenue, blu notte, effetto carta) per affaticare meno gli occhi. | — | P | no | Estensione: inversione dei colori della sola pagina. |
| 1.14 Lettura a riflusso | Ricompone il testo per finestre strette o ingrandimenti forti, così da leggere senza scorrere in orizzontale; resta possibile annotare. | PDF con tag (resa migliore) o analisi del layout | A | n.d. | Resa limitata sulle scansioni prive di OCR. |
| 1.15 Lente e panoramica | Lente d'ingrandimento che segue il puntatore, zoom dinamico trascinando e pannello di panoramica con il riquadro visibile da spostare. | — | A | n.d. | — |
| 1.16 Vista divisa | Divide la finestra in due riquadri, orizzontali o verticali, per vedere insieme parti diverse dello stesso documento o due documenti. | — | A | n.d. | — |
| 1.17 Righelli, guide e griglia | Righelli ai bordi, guide trascinabili e griglia con aggancio per posizionare con precisione oggetti, annotazioni e campi. | — | A | n.d. | L'aggancio durante lo spostamento è già in 4.08. |
| 1.18 Modalità presentazione | Mostra il documento a schermo intero come una sequenza di diapositive. | — | P | no | — |
| 1.19 Scorrimento automatico | Fa scorrere o avanzare le pagine da sole, a velocità regolabile. | — | P | no | — |
| 1.20 Transizioni e tempi di presentazione | Registra nel documento gli effetti di passaggio tra le pagine, il tempo di permanenza di ciascuna e la comparsa graduale di elementi, validi in qualunque lettore. | Transizioni, durata, navigazione sotto-pagina (ISO 32000-2 §12.4.4) | A | n.d. | Il riferimento offre solo la riproduzione (1.18). |
| 1.21 Opzioni di apertura del documento | Salva nel file come deve presentarsi all'apertura: pagina e zoom iniziali, disposizione delle pagine, pannello visibile, schermo intero, titolo nella barra della finestra, barre nascoste, direzione di lettura, preferenze di stampa predefinite. | Preferenze del visualizzatore, layout e azione di apertura (ISO 32000-2 §7.7.2, §12.2) | A | n.d. | Il riferimento ha solo preferenze dell'applicazione. In blocco: 11.16. |
| 1.22 Lettura ad alta voce | Sintesi vocale che legge ad alta voce il testo del documento. | Testo → audio | P | no | Richiede un motore di sintesi vocale locale. |
| 1.23 Segnalibri | Pannello dei segnalibri per navigare il documento; aggiunge, rinomina, elimina e riorganizza le voci in un albero a più livelli. | Struttura dei segnalibri (outline) | P | sì | Nel riferimento la sola consultazione è gratuita. |
| 1.24 Segnalibri generati con IA | Costruisce da sé un albero di segnalibri a più livelli analizzando titoli e contenuto. | Outline | P | sì | Richiede fornitore IA configurato dall'utente. |
| 1.25 Strumenti avanzati per i segnalibri | Crea segnalibri dagli stili o dalla posizione del testo, da espressioni regolari, da un sommario esistente, da un file di testo o ogni N pagine; imposta colore, stile, stato aperto o chiuso e azioni delle voci; verifica e ripara le voci rotte, fonde i duplicati, cerca e sostituisce nei titoli, cambia maiuscole e minuscole; ordina le pagine secondo i segnalibri; esporta e importa l'albero. | Outline; TXT, HTML | A | n.d. | Generazione deterministica, senza IA. |
| 1.26 Percorsi di lettura | Definisce e fa seguire percorsi di lettura che attraversano colonne e pagine, come negli articoli di giornali e riviste. | Thread di articoli (ISO 32000-2 §12.4.3) | A | n.d. | Utile anche alla lettura a riflusso. |
| 1.27 Ricerca nel documento | Trova parole o frasi nel testo e nei commenti, con elenco dei risultati ed evidenziazione delle occorrenze. | — | P | no | Ricerca nei commenti documentata nella cronologia delle versioni. |
| 1.28 Ricerca avanzata | Ricerca con espressioni regolari, operatori booleani, distinzione tra maiuscole e minuscole e parola intera, limitabile a commenti, segnalibri o campi. | — | A | n.d. | Le stesse opzioni valgono in 3.06 e 3.07. |
| 1.29 Ricerca semantica | Trova i passaggi pertinenti per significato, anche senza le parole esatte (sinonimi, concetti affini), ordinati per rilevanza. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 1.30 Ricerca su più documenti e indice | Cerca nei documenti aperti, in una cartella o in un portfolio; costruisce un indice testuale di una raccolta per avere risposte immediate su migliaia di file, anche incorporato in un PDF. | Indice di testo locale | A | n.d. | Le scansioni richiedono prima l'OCR. |
| 1.31 Proprietà del documento | Mostra titolo, autore, oggetto, parole chiave, programma di origine, date di creazione e modifica, versione PDF, formato delle pagine e peso del file. | Dizionario informazioni, metadati XMP | P | no | Testi in qualunque alfabeto (Unicode) e date con fuso orario. |
| 1.32 Metadati avanzati | Modifica i metadati standard, aggiunge proprietà personalizzate chiave/valore, salva modelli di metadati riutilizzabili, importa ed esporta XMP, mostra gli schemi Dublin Core e PDF/A e i dati privati lasciati da altre applicazioni. | XMP (ISO 16684), dizionario informazioni | A | n.d. | In blocco: 11.16. |
| 1.33 Barre degli strumenti e preferenze | Barre con comandi da mostrare o nascondere; preferenze di layout, zoom e pagina iniziale dell'applicazione. | — | P | no | — |
| 1.34 Personalizzazione avanzata dell'interfaccia | Disposizioni alternative (classica, a nastro, per schermi tattili), barre create dall'utente, scorciatoie modificabili, ricerca dei comandi per nome, sessioni di lavoro da salvare e riprendere, esportazione e importazione delle impostazioni, scelta della lingua. | File di impostazioni | A | n.d. | Comprende la scala per singolo monitor sugli schermi ad alta densità. |
| 1.35 Accessibilità dell'applicazione | Interfaccia usabile con lettori di schermo, solo da tastiera e ad alto contrasto; comandi esposti agli strumenti di automazione dell'interfaccia. | Servizi di accessibilità del desktop (AT-SPI) | A | n.d. | — |
| 1.36 Stampa | Stampa con scelta di stampante, intervallo di pagine, copie, scala e fronte/retro. | — | P | no | Usa il servizio di stampa del sistema. |
| 1.37 Stampa avanzata | Stampa a opuscolo, più pagine per foglio, poster diviso su più fogli, stampa speculare, formati di carta personalizzati, stampa con i commenti o con il loro riepilogo, stampa di tutti i documenti aperti o di un portfolio. | — | A | n.d. | Per ottenere un PDF imposto invece della stampa: 2.17. |
| 1.38 Invio per e-mail | Invia il documento come allegato con il programma di posta predefinito. | — | P | no | Condivisione tramite link o codice QR: fuori ambito (13.04, 13.05). |

## 2. Gestione pagine

Organizzazione, divisione e unione, geometria delle pagine.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 2.01 Inserimento di pagine | Aggiunge pagine prese da un altro PDF, pagine bianche, contenuto degli appunti, immagini o pagine da scanner, in un punto scelto o alternate a quelle esistenti. | PDF, immagini | P | sì | — |
| 2.02 Sostituzione di pagine | Rimpiazza una o più pagine con pagine di un altro file. | PDF | P | sì | — |
| 2.03 Estrazione di pagine | Salva le pagine scelte in un nuovo file unico o in file separati, come PDF o come immagini; basta anche trascinare le miniature sul desktop. | PDF, immagini | P | sì | — |
| 2.04 Divisione del documento | Spezza un PDF in più file per numero di pagine, dimensione massima del file o segnalibri di primo livello. | PDF | P | sì | — |
| 2.05 Divisione avanzata | Divide anche in corrispondenza di pagine bianche usate come separatori o di un testo trovato, tratta più file in una volta e dà nomi ai file prodotti con regole configurabili. | PDF | A | n.d. | Regole di nome: 4.19. |
| 2.06 Unione di documenti | Combina più file (PDF, immagini, documenti d'ufficio o tipi misti) in un solo PDF nell'ordine voluto, con un segnalibro per ciascun file d'origine se richiesto. | PDF, immagini, documenti d'ufficio | P | sì | Accorpa la voce "unione in blocco" dell'inventario. |
| 2.07 Rotazione delle pagine | Ruota in modo permanente una o più pagine di 90, 180 o 270 gradi, mantenendo coerenti annotazioni e campi. | — | P | sì | — |
| 2.08 Eliminazione di pagine | Toglie le pagine selezionate. | — | P | sì | — |
| 2.09 Riordino delle pagine | Sposta le pagine trascinandole, scambia pari e dispari, inverte l'ordine del documento. | — | P | sì | — |
| 2.10 Copia, incolla e duplicazione di pagine | Copia pagine negli appunti, le incolla nello stesso o in un altro documento, le duplica o le condivide. | — | P | sì | Le pagine copiate devono portare con sé annotazioni, collegamenti e risorse. |
| 2.11 Ritaglio delle pagine | Riduce l'area visibile delle pagine con un riquadro disegnato o con misure, anche togliendo in automatico i margini bianchi. | Riquadro di ritaglio (CropBox) | P | sì | Rimozione dei margini bianchi documentata nella cronologia delle versioni. |
| 2.12 Suddivisione di una pagina | Taglia una pagina in più pagine, per esempio una scansione con due facciate affiancate. | — | P | sì | Estensione: taglio lungo linee guida tracciate dall'utente. |
| 2.13 Etichette di pagina | Numerazione personalizzata per intervalli (numeri romani, lettere, prefissi, numero di partenza), visibile nel campo pagina e usabile per stampare intervalli. | Etichette di pagina (ISO 32000-2 §12.4.2) | P | sì | — |
| 2.14 Dimensione della pagina | Cambia il formato delle pagine scegliendo un formato standard o misure libere e adattando il contenuto. | — | P | sì | — |
| 2.15 Normalizzazione delle pagine | Uniforma le pagine al formato di un altro documento, aggiunge margini, corregge gli scostamenti dei riquadri e applica il ritaglio solo agli intervalli scelti. | Riquadri di pagina | A | n.d. | — |
| 2.16 Riquadri di stampa e pagine molto grandi | Imposta e mostra i riquadri di abbondanza, rifilo e area grafica usati in tipografia, ripristina il ritaglio originale e gestisce pagine oltre i 200 pollici. | MediaBox, CropBox, BleedBox, TrimBox, ArtBox, UserUnit; PDF/X | A | n.d. | — |
| 2.17 Imposizione nel documento | Produce un nuovo PDF con più pagine per foglio (2, 4, 8 o più), in sequenza a opuscolo o unendo pagine piccole in una sola, con segni di taglio facoltativi. | PDF | A | n.d. | Stampa diretta: 1.37. |
| 2.18 Pulizia delle pagine con IA | Individua pagine bianche o non valide, uniforma orientamento e formato, rileva ed estrae le annotazioni, segnala le pagine da sottoporre a OCR e propone una pulizia rapida. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 2.19 Pagine duplicate e vuote | Trova pagine identiche o quasi e pagine vuote confrontandone il contenuto in locale, e propone di eliminarle. | — | A | n.d. | Non richiede IA. |
| 2.20 Sommario dai segnalibri | Inserisce una o più pagine di indice costruite dai segnalibri, con numeri di pagina, collegamenti cliccabili e stile configurabile. | PDF | A | n.d. | — |

## 3. Modifica testo

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 3.01 Modifica del testo esistente | Corregge e riscrive il testo già presente nella pagina, per riga o per paragrafo, con ricomposizione automatica delle righe. | Contenuto di pagina | P | sì | Deve conservare struttura logica (tag), livelli e risorse; se il font incorporato non contiene i caratteri nuovi, propone una sostituzione (3.08). |
| 3.02 Aggiunta di testo | Inserisce nuovi blocchi di testo in qualunque punto della pagina, come parte del contenuto. | — | P | sì | Diversa dalla casella di testo di annotazione (5.09). |
| 3.03 Formattazione del testo | Cambia carattere, dimensione, colore, grassetto, corsivo, spaziatura e allineamento del testo selezionato. | Font di sistema e incorporati | P | sì | — |
| 3.04 Testo formattato per trascinamento | Inserisce testo con la sua formattazione trascinandolo da un'altra applicazione. | RTF | P | sì | Estensione: inserimento di interi file RTF o TXT. |
| 3.05 Selezione e copia del testo | Seleziona testo anche attraverso più pagine e lo copia con caratteri corretti (legature, simboli, alfabeti non latini) e nell'ordine di lettura. | — | P | no | — |
| 3.06 Trova e sostituisci | Sostituisce un testo in tutto il documento mantenendo carattere, dimensione e allineamento; avvisa se il font non contiene i caratteri nuovi. | — | P | sì | Opzioni di ricerca: 1.28. |
| 3.07 Trova ed elimina | Cancella dal contenuto tutte le occorrenze di un testo. | — | P | sì | Per eliminare in modo sicuro dati sensibili si usa la redazione (10.10). |
| 3.08 Gestione dei font | Elenca i font con tipo e stato (incorporato, parziale, mancante); incorpora o sostituisce quelli mancanti, sostituisce un font in tutto il documento, toglie l'incorporazione per alleggerire il file. | TrueType, Type 1, CFF, OpenType, font composti (CID) | A | n.d. | PDF/A richiede tutti i font incorporati. |
| 3.09 Scritture complesse | Visualizza, seleziona e modifica correttamente testi da destra a sinistra (arabo, ebraico), alfabeti cinese, giapponese e coreano e scrittura verticale; interfaccia speculare per le lingue da destra a sinistra. | OpenType, CMap verticali | A | n.d. | — |
| 3.10 Controllo ortografico | Segnala gli errori di ortografia nel contenuto e nei commenti con suggerimenti, usando dizionari installabili e aggiornabili senza riavviare. | Dizionari Hunspell | A | n.d. | Non richiede IA (diverso da 3.13). |
| 3.11 Diagnosi del testo non estraibile | Riconosce quando il testo copiato o cercato risulta illeggibile perché manca o è errata la corrispondenza dei caratteri, e propone di ricostruirla o di applicare l'OCR. | ToUnicode, testo effettivo (ActualText) | A | n.d. | — |
| 3.12 Modifica del testo con IA | L'utente descrive a parole la modifica desiderata e l'assistente la applica al testo del documento. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 3.13 Correzione di bozze con IA | Corregge grammatica, ortografia e punteggiatura del testo selezionato. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 3.14 Espansione e sintesi con IA | Allunga o accorcia il testo selezionato conservandone il senso. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 3.15 Rifinitura e cambio di tono con IA | Migliora lo stile del testo selezionato o lo riscrive con un altro tono (formale, cordiale, persuasivo). | — | P | sì | Richiede fornitore IA configurato dall'utente. |

## 4. Immagini, oggetti e collegamenti

Immagini e grafica, elementi aggiunti alle pagine (filigrane, sfondi, intestazioni), collegamenti e azioni, allegati, livelli, contenuti multimediali e 3D.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 4.01 Inserimento di immagini | Aggiunge immagini alla pagina da file o trascinandole, conservandone la trasparenza. | PNG, JPEG, BMP, GIF, TIFF e altri formati raster | P | sì | Estensione: JPEG 2000. |
| 4.02 Modifica delle immagini | Sposta, ridimensiona, ruota anche ad angolo libero, capovolge, ritaglia, sostituisce, copia ed elimina le immagini. | — | P | sì | — |
| 4.03 Estrazione delle immagini | Salva una singola immagine o tutte quelle del documento, se possibile nel formato originale. | PNG, JPEG, TIFF, JPEG 2000 | P | sì | — |
| 4.04 Ritocco delle immagini | Raddrizza, regola luminosità e contrasto, sostituisce un'immagine con quella negli appunti, modifica insieme tutte le copie identiche (per esempio un logo ripetuto), ricomprime più immagini in una volta. | Immagini incorporate | A | n.d. | — |
| 4.05 Cattura di aree e testo | Cattura una zona della pagina come immagine o ne estrae il testo. | Immagine, testo | P | no | — |
| 4.06 Modifica di oggetti vettoriali | Seleziona e modifica tracciati, linee e forme del contenuto originale (punti, riempimento, contorno, colore); scala, ruota e inclina gruppi di oggetti, li allinea e li distribuisce; ricava un nuovo documento o un timbro dagli oggetti selezionati. | Tracciati, XObject | A | n.d. | — |
| 4.07 Spostamento tra pagine | Sposta testo, immagini e altri oggetti da una pagina all'altra. | — | P | sì | — |
| 4.08 Allineamento automatico | Durante lo spostamento compaiono guide che agganciano gli elementi a bordi e centri degli altri oggetti. | — | P | sì | — |
| 4.09 Ispettore degli oggetti | Pannello con le proprietà dell'oggetto selezionato: tipo, posizione, risoluzione effettiva e compressione delle immagini, spazio colore, livello, marcatura di struttura. | — | A | n.d. | — |
| 4.10 Modifica in un'applicazione esterna | Apre un'immagine o un oggetto nell'editor preferito dell'utente e reinserisce in automatico la versione salvata. | PNG, TIFF, SVG | A | n.d. | — |
| 4.11 Motivi e sfumature | Applica a forme, testo e sfondi riempimenti a motivo ripetuto o sfumature lineari, radiali e a maglia; modifica colori e direzione delle sfumature esistenti. | Pattern e shading (ISO 32000-2 §8.7) | A | n.d. | — |
| 4.12 Trasparenza e fusione | Regola opacità e modalità di fusione di oggetti e annotazioni, maschere sfumate e gruppi; appiattisce la trasparenza quando il profilo di destinazione la vieta, con anteprima. | Stato grafico esteso, gruppi di trasparenza; PDF/A-1, PDF/X-1a/3 | A | n.d. | Il riferimento regola solo l'opacità delle annotazioni. |
| 4.13 Filigrana | Aggiunge, modifica e toglie filigrane di testo, immagine o ricavate da un PDF, anche ripetute a mosaico, con posizione, rotazione, opacità e intervallo di pagine. | Testo, immagini, PDF | P | sì | Il contenuto aggiunto va marcato come elemento decorativo (artefatto) per l'accessibilità. |
| 4.14 Filigrana generata con IA | Crea una filigrana a partire da una descrizione testuale. | Immagine | P | sì | Richiede fornitore IA configurato dall'utente (generazione di immagini). |
| 4.15 Filigrana a posizione fissa | Filigrana che conserva posizione e dimensione sul foglio stampato qualunque sia la scala di stampa, e si toglie senza toccare il contenuto. | Annotazione Watermark (ISO 32000-2 §12.5.6.22) | A | n.d. | — |
| 4.16 Sfondo | Aggiunge, modifica o toglie uno sfondo a tinta unita, da immagine o da PDF sulle pagine scelte. | Colore, immagini, PDF | P | sì | Marcato come artefatto. |
| 4.17 Sfondo generato con IA | Crea uno sfondo a partire da una descrizione testuale. | Immagine | P | sì | Richiede fornitore IA configurato dall'utente (generazione di immagini). |
| 4.18 Intestazione e piè di pagina | Aggiunge, aggiorna o toglie intestazioni e piè di pagina con testo, numero di pagina e totale, data, su intervalli scelti e con margini regolabili. | — | P | sì | Marcati come artefatti; altre variabili in 4.19. |
| 4.19 Variabili e macro | Nei testi di intestazioni, piè di pagina, filigrane e numerazione Bates e nei nomi dei file prodotti usa variabili come nome ed estensione del file, autore, data e ora, numero Bates. | — | A | n.d. | — |
| 4.20 Codici a barre e QR | Genera e inserisce codici a barre e QR (testo, indirizzi, dati di un modulo) come contenuto o come campo che si aggiorna con i dati compilati; rende cliccabili i codici già presenti nel documento. | QR, Code 128, Code 39, PDF417, Data Matrix | A | n.d. | Il campo modulo si gestisce insieme agli altri campi (area 8). |
| 4.21 Collegamenti | Crea e modifica collegamenti verso una pagina del documento, un indirizzo web o un altro file. | — | P | sì | — |
| 4.22 Collegamenti avanzati | Collegamenti tracciati sul testo oltre che rettangolari, effetto al clic, riconoscimento automatico degli indirizzi web nel testo, indirizzo di base per i link relativi, verifica dei link rotti, link verso allegati e destinazioni con nome. | Annotazioni Link, azioni URI (ISO 32000-2 §12.5.6.5) | A | n.d. | PDF/UA richiede link marcati e con testo alternativo. |
| 4.23 Destinazioni con nome | Pannello dei punti di arrivo con nome (pagina e inquadratura precise): crea, rinomina, elimina, ripulisce quelli rotti, converte da e verso segnalibri; supporta link esterni del tipo #page= e #nameddest=. | Destinazioni esplicite e con nome (ISO 32000-2 §12.3.2) | A | n.d. | — |
| 4.24 Azioni avanzate | Associa a collegamenti, segnalibri e pulsanti azioni come aprire un altro PDF a una certa pagina, raggiungere un allegato, andare alla prima, ultima, precedente o successiva pagina, seguire un articolo, mostrare o nascondere campi, accendere o spegnere livelli, avviare contenuti multimediali, lanciare un programma; più azioni in catena. | Azioni (ISO 32000-2 §12.6) | A | n.d. | L'avvio di programmi è bloccato per impostazione predefinita (10.18). |
| 4.25 Allegati del documento | Pannello degli allegati a livello di documento: aggiunge, apre, salva, rinomina ed elimina file incorporati e ne indica il ruolo (dati d'origine, alternativa, supplemento). | File incorporati e file associati; PDF/A-3 | A | n.d. | Allegati ancorati a un punto della pagina: 5.16. |
| 4.26 Livelli: consultazione | Pannello per mostrare o nascondere i livelli, vederne il blocco, applicare configurazioni alternative e comportamenti diversi a schermo, in stampa ed esportazione (anche secondo zoom o lingua). | Contenuto opzionale OCG/OCMD (ISO 32000-2 §8.11) | A | n.d. | Lacuna principale del riferimento. |
| 4.27 Livelli: creazione e modifica | Crea, rinomina, riordina, annida, blocca, unisce ed elimina livelli; assegna testo, immagini, annotazioni, collegamenti e campi a un livello; fissa lo stato iniziale; appiattisce i livelli nel contenuto. | OCG/OCMD; vincoli PDF/A-2 e PDF/X-4 | A | n.d. | — |
| 4.28 Sovrapposizione di documenti | Sovrappone alle pagine quelle di un altro PDF (carta intestata, griglia, timbro), oppure importa un PDF o un'immagine come nuovo livello o dentro un livello esistente, conservando i livelli d'origine. | PDF, immagini raster | A | n.d. | — |
| 4.29 Audio e video | Inserisce e riproduce audio e video incorporati o in streaming, aree di riproduzione e commenti vocali registrati; converte i vecchi formati multimediali. | Annotazioni Screen e RichMedia, rendition; H.264, MP3 | A | n.d. | Riproduzione disattivata per i documenti non attendibili (10.18). |
| 4.30 Modelli 3D | Mostra modelli 3D incorporati con rotazione, zoom, viste salvate, sezioni, albero del modello, misure e commenti ancorati; aggiunge un modello 3D a una pagina. | U3D, PRC | A | n.d. | Richiede un motore di resa 3D. |

## 5. Annotazioni e commenti

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 5.01 Evidenziazione | Evidenzia il testo selezionato, anche su più righe e pagine, con colore e opacità a scelta. | Annotazioni di marcatura del testo | P | sì | — |
| 5.02 Evidenziazione di un'area | Evidenzia una zona rettangolare qualsiasi, utile su immagini e scansioni. | — | P | sì | — |
| 5.03 Barrato | Barra il testo selezionato. | — | P | sì | — |
| 5.04 Sottolineato | Sottolinea il testo selezionato, anche tra pagine diverse. | — | P | sì | — |
| 5.05 Sottolineatura ondulata | Sottolinea con una linea ondulata per segnalare dubbi o errori. | — | P | sì | — |
| 5.06 Segno di inserimento | Indica il punto in cui inserire nuovo testo e il testo proposto. | — | P | sì | — |
| 5.07 Segno di sostituzione | Indica un testo da sostituire, collegando la barratura al testo proposto. | — | P | sì | — |
| 5.08 Commento di testo | Aggiunge un commento testuale ancorato a un punto della pagina. | — | P | sì | — |
| 5.09 Casella di testo | Riquadro di testo sovrapposto alla pagina, con bordo, sfondo, carattere e allineamento. | — | P | sì | — |
| 5.10 Nota adesiva | Icona sulla pagina che si apre in un fumetto con il testo della nota. | — | P | sì | — |
| 5.11 Richiamo | Casella di testo collegata da una freccia al punto commentato. | — | P | sì | — |
| 5.12 Forme | Rettangoli, ellissi, linee, frecce e poligoni, con spessore, tratteggio, estremità e riempimento. | — | P | sì | Estensione: spezzate, nuvole e linee di quota. |
| 5.13 Disegno a mano libera | Penna per tracciare a mano libera e gomma per cancellare i tratti. | — | P | sì | — |
| 5.14 Adesivi | Raccolta di adesivi illustrati ed emoji da applicare alla pagina. | — | P | sì | — |
| 5.15 Adesivi generati con IA | Crea nuovi adesivi a partire da una descrizione. | Immagine | P | sì | Richiede fornitore IA configurato dall'utente (generazione di immagini). |
| 5.16 File allegato sulla pagina | Allega un file in un punto preciso della pagina, rappresentato da un'icona con descrizione; l'allegato si apre, si salva o si elimina. | Qualsiasi file | P | sì | PDF/A-1 vieta gli allegati; PDF/A-2 ammette solo file PDF/A. |
| 5.17 Timbri | Timbri pronti (approvato, bozza, riservato…), timbri personalizzati da immagine o PDF e timbri dinamici con data, ora e nome dell'utente. | Immagini, PDF | P | sì | Timbri dinamici documentati nella cronologia delle versioni. |
| 5.18 Timbri generati con IA | Crea timbri a partire da una descrizione. | Immagine | P | sì | Richiede fornitore IA configurato dall'utente (generazione di immagini). |
| 5.19 Timbri sequenziali e da selezione | Timbri con numero progressivo che cresce a ogni applicazione, timbri ricavati dal contenuto selezionato, timbri dinamici programmabili, raccolte di timbri ordinate per categoria e ricercabili. | — | A | n.d. | I timbri programmabili richiedono il motore di script (8.18). |
| 5.20 Misurazioni | Misura distanze, perimetri e aree su disegni e planimetrie, con calibrazione della scala. | Dizionario di misura | P | sì | — |
| 5.21 Misurazioni avanzate | Misura anche gli angoli; aggancia estremi, punti medi, intersezioni e tracciati; trasforma linee e poligoni esistenti in misure; mostra il valore sulla quota; legge la scala dal documento; esporta le misure in CSV. | Dizionario di misura; CSV | A | n.d. | — |
| 5.22 Mappe georeferenziate | Su mappe con coordinate incorporate mostra latitudine e longitudine del puntatore, cerca una località, misura in unità reali ed esporta annotazioni georeferenziate. | PDF geospaziale | A | n.d. | Richiede una libreria di proiezioni cartografiche. |
| 5.23 Elenco delle annotazioni | Pannello con tutte le annotazioni del documento, per raggiungerle con un clic. | — | P | no | — |
| 5.24 Risposte ai commenti | Discussioni di risposta sotto ogni commento, con autore e data. | — | P | sì | — |
| 5.25 Stati di revisione | Assegna a ogni commento uno stato (accettato, rifiutato, annullato, completato) e un segno di spunta personale, con storico per autore; raggruppa annotazioni correlate. | Stati delle annotazioni (ISO 32000-2 §12.5.6.3) | A | n.d. | — |
| 5.26 Mostra o nascondi annotazioni | Nasconde tutte le annotazioni per leggere il documento pulito e le ripristina. | — | P | no | — |
| 5.27 Filtro e ricerca dei commenti | Filtra le annotazioni per tipo, autore e data e ne cerca il testo. | — | P | no | Filtro per stato insieme a 5.25. |
| 5.28 Proprietà avanzate delle annotazioni | Blocca posizione o contenuto, rende un'annotazione visibile solo a schermo o solo in stampa, la assegna a un livello, ne cambia autore e oggetto; copia e incolla tra pagine e documenti; tavolozza di stili preferiti. | Flag e proprietà delle annotazioni | A | n.d. | PDF/A vieta le annotazioni nascoste e richiede quelle stampabili. |
| 5.29 Riepilogo dei commenti | Esporta l'elenco delle annotazioni con i loro testi in un PDF di riepilogo separato. | PDF | P | sì | — |
| 5.30 Scambio dei commenti | Esporta tutti i commenti o una parte in un file dati leggero, li importa in un'altra copia del documento unendo il lavoro di più revisori, li esporta in un documento di testo o in un foglio di calcolo, anche limitandosi al testo evidenziato. | FDF, XFDF (ISO 19444-1), DOCX, CSV | A | n.d. | — |
| 5.31 Revisione condivisa in rete locale | Avvia una revisione su una cartella condivisa o un server WebDAV: i revisori pubblicano e ricevono i commenti, con scadenza e prospetto dei partecipanti. | PDF, XFDF | A | n.d. | La collaborazione in tempo reale via cloud è fuori ambito (13.13). |

## 6. Conversione, creazione e ottimizzazione

Creazione e importazione, esportazione, salvataggio e ottimizzazione, standard ISO e prestampa, accessibilità dei documenti.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 6.01 Nuovo documento vuoto | Crea un PDF vuoto scegliendo formato, orientamento e tipo di carta (bianca, a righe, a quadretti). | PDF | P | no | — |
| 6.02 Creazione da documenti d'ufficio | Converte in PDF documenti di testo, fogli di calcolo, presentazioni, diagrammi e documenti CAJ. | DOC/DOCX, XLS/XLSX, PPT/PPTX, VSD/VSDX, CAJ | P | sì | Richiede un motore locale di conversione per documenti d'ufficio. |
| 6.03 Creazione dalla suite d'ufficio | Pulsante o estensione dentro l'applicazione d'ufficio e il programma di posta per creare PDF che conservano collegamenti, sommario cliccabile e segnalibri, con aggiornamento dei campi prima della conversione; crea un portfolio da messaggi di posta. | Documenti d'ufficio, messaggi di posta | A | n.d. | Su Linux come estensione della suite d'ufficio installata. |
| 6.04 Creazione da immagini | Converte una o più immagini in PDF, una per pagina o raccolte in un unico file. | PNG, JPEG, BMP, GIF, TIFF e altri | P | sì | — |
| 6.05 Formati immagine aggiuntivi | Accetta anche foto HEIC, immagini WebP, immagini mediche DICOM e TIFF a più pagine, con ritaglio durante la conversione. | HEIC/HEIF, WebP, DICOM, TIFF multipagina | A | n.d. | — |
| 6.06 Creazione da testo e Markdown | Crea un PDF impaginato da file di testo semplice o Markdown. | TXT, Markdown | P | sì | — |
| 6.07 Creazione da altre sorgenti | Crea PDF da file RTF, da tabelle CSV impaginate come tabella, da messaggi di posta con i loro allegati, da documenti XPS, da metafile grafici e da modelli predefiniti. | RTF, CSV, EML/MSG, XPS, WMF/EMF | A | n.d. | — |
| 6.08 Creazione da cattura dello schermo | Cattura lo schermo, una finestra o un'area e ne ricava un PDF. | Immagine → PDF | P | sì | Su Linux tramite il servizio di cattura del desktop. |
| 6.09 Creazione dagli appunti | Crea un PDF dal testo o dall'immagine presenti negli appunti. | Testo, immagine | P | sì | — |
| 6.10 Acquisizione da scanner | Acquisisce pagine da scanner in un nuovo PDF, anche fronte e retro di un documento d'identità sulla stessa pagina. | Immagine → PDF | P | sì | Su Linux tramite i driver di scansione del sistema. |
| 6.11 Profili di scansione | Profili salvati con risoluzione, colore, fronte/retro, raddrizzamento e OCR automatico, da riusare con un clic anche per lotti di documenti. | — | A | n.d. | — |
| 6.12 Acquisizione da fotocamera | Fotografa i documenti e li converte in PDF ritagliandone i bordi. | Immagine → PDF | P | sì | Nel riferimento solo su mobile; su desktop da webcam o da foto importate, con correzione prospettica (7.09). |
| 6.13 Conversione di pagine web | Converte una pagina web in PDF con i collegamenti attivi, seguendo a scelta più livelli di link (stesso percorso o stesso server), anche aggiungendo pagine a un PDF esistente. | HTML/CSS → PDF | A | n.d. | Richiede rete e un motore di resa web. |
| 6.14 Stampante virtuale | Stampante di sistema che crea un PDF da qualsiasi programma, con profili di qualità, incorporazione dei font, risoluzione, uscita PDF/A e nomi dei file generati da regole. | Qualsiasi stampa → PDF, PDF/A | A | n.d. | Su Linux come coda del servizio di stampa. |
| 6.15 Grafica professionale e CAD | Converte in PDF file di fotoritocco, illustrazione e impaginazione e disegni CAD, anche come PDF 3D. | PSD, AI, INDD, DWG/DXF | A | n.d. | I formati CAD richiedono librerie dedicate. |
| 6.16 Portfolio PDF | Crea un portfolio che riunisce più file in un unico PDF, con vista a elenco o a miniature, ricerca, estrazione e stampa dei file contenuti. | Portfolio (collezione) | P | n.d. | Documentato nella cronologia delle versioni. |
| 6.17 Portfolio avanzato | Cartelle, colonne informative personalizzate e ordinamento, documento di copertina e vista iniziale, anteprima di file di ogni tipo, apertura e modifica di portfolio creati da altri programmi. | Collezioni (ISO 32000-2 §12.3.5) | A | n.d. | PDF/A-3 e PDF/A-4f ammettono file di qualsiasi tipo. |
| 6.18 Documenti d'ufficio senza conversione | Visualizza documenti d'ufficio e modifica file DOCX direttamente nell'app, senza passare dal PDF. | DOCX, XLSX, PPTX | A | n.d. | Richiede un motore per documenti d'ufficio; ambito da valutare. |
| 6.19 Esportazione in DOCX | Trasforma il PDF in un documento di testo modificabile che conserva impaginazione, tabelle e immagini. | DOCX | P | sì | — |
| 6.20 Esportazione in foglio di calcolo | Porta le tabelle in un foglio di calcolo modificabile, riconoscendo anche le tabelle senza bordi. | XLSX | P | sì | Nel riferimento assistita da IA; in Foliant con analisi locale del layout, variante IA in 6.31. |
| 6.21 Esportazione in presentazione | Trasforma le pagine in diapositive modificabili. | PPTX | P | sì | Nel riferimento usa un servizio online; in Foliant la conversione è locale. |
| 6.22 Esportazione in CSV | Esporta i dati delle tabelle in file CSV. | CSV | P | sì | — |
| 6.23 Esportazione in immagini | Salva ogni pagina come immagine, scegliendo formato e risoluzione. | PNG, JPEG, BMP, GIF, TIFF | P | sì | — |
| 6.24 Esportazione in immagine unica | Unisce le pagine in un'unica immagine verticale. | PNG, JPEG | P | sì | — |
| 6.25 Esportazione in RTF | Esporta il testo con la formattazione essenziale. | RTF | P | sì | — |
| 6.26 Esportazione in testo semplice | Estrae tutto il testo in un file di testo. | TXT | P | sì | — |
| 6.27 Esportazione in XML | Esporta il contenuto in XML strutturato. | XML | P | sì | — |
| 6.28 Esportazione in HTML | Trasforma il documento in pagine web. | HTML | P | sì | — |
| 6.29 Esportazione in formati estesi | Esporta anche in PostScript ed EPS per la prestampa, in WebP, JPEG 2000, TIFF con compressione a scelta e altri formati raster, in foglio XML 2003, in testo accessibile e in HTML navigabile come un sito; esporta solo l'area selezionata. | PS, EPS, WebP, JPEG 2000, TIFF (G4, LZW, ZIP, JPEG), PBM/PGM/PPM, TGA, XML Spreadsheet 2003 | A | n.d. | — |
| 6.30 Conversione con OCR | Riconosce il testo durante la conversione di PDF scansionati in formati modificabili. | DOCX, XLSX, PPTX, RTF, TXT, HTML | P | sì | Richiede un motore OCR locale (area 7). |
| 6.31 Conversione migliorata con IA | Usa l'IA per ricostruire meglio layout, tabelle e struttura nelle conversioni verso documenti, fogli di calcolo e presentazioni. | DOCX, XLSX, PPTX | P | sì | Richiede fornitore IA configurato dall'utente. |
| 6.32 Salva con nome e in altri formati | Salva una copia, anche riscrivendo il file da capo in forma compatta (senza oggetti inutilizzati né versioni precedenti), oppure in uno dei formati di esportazione. | PDF e formati di esportazione | P | sì | La riscrittura completa è obbligatoria dopo redazione e sanificazione; va conservato l'identificatore permanente del file e rinnovato quello di revisione. |
| 6.33 Salvataggio incrementale e revisioni | Accoda le modifiche al file senza riscriverlo: è l'unico modo per annotare, compilare o firmare ancora un documento firmato senza invalidare le firme; mostra le revisioni contenute nel file e permette di aprirle. | Aggiornamenti incrementali (ISO 32000-2 §7.5.6) | A | n.d. | Le revisioni precedenti restano leggibili: avvisare prima di diffondere un file redatto. |
| 6.34 Compressione | Riduce il peso del file scegliendo tra livelli di qualità predefiniti. | PDF | P | sì | — |
| 6.35 Ottimizzatore avanzato | Mostra quanto spazio occupano immagini, font, segnalibri, moduli e commenti; imposta sottocampionamento e compressione per tipo d'immagine; toglie l'incorporazione dei font; scarta miniature, metadati, allegati, livelli nascosti, tag, oggetti duplicati od orfani; avvisa se una scelta viola PDF/A. | JPEG, JPEG 2000, JBIG2, CCITT, Flate; MRC | A | n.d. | — |
| 6.36 Linearizzazione | Salva il PDF in modo che la prima pagina compaia subito mentre il resto si scarica dal web; le proprietà indicano se il file è ottimizzato. | PDF linearizzato (ISO 32000-2 Annex F) | A | n.d. | — |
| 6.37 Riparazione di file danneggiati | Apre file con indici interni errati o mancanti ricostruendone la struttura e propone di salvarne una copia riparata. | PDF | A | n.d. | — |
| 6.38 PDF 2.0 e versione del documento | Apre e salva file PDF 2.0 con le loro novità (testi UTF-8, intenti di output per pagina, nuove annotazioni e firme); mostra e aggiorna la versione del file, dichiara le estensioni usate e avvisa quando una funzione richiede una versione più recente. | PDF 1.x, PDF 2.0 (ISO 32000-2); ISO/TS 32001–32005 | A | n.d. | — |
| 6.39 Requisiti del documento | Avvisa se il documento dichiara di aver bisogno di funzioni non disponibili e registra nel file i requisiti di ciò che l'utente vi inserisce (script, livelli, firme, contenuti multimediali). | Requisiti (ISO 32000-2 §12.11) | A | n.d. | — |
| 6.40 Salvataggio in PDF/A, PDF/E, PDF/X | Salva il documento nei formati standard per l'archiviazione a lungo termine, per i documenti tecnici e per la stampa professionale. | PDF/A (ISO 19005), PDF/E (ISO 24517), PDF/X (ISO 15930) | P | sì | — |
| 6.41 Verifica di conformità | Controlla la conformità a PDF/A, PDF/X, PDF/E e PDF/UA con un rapporto dettagliato degli errori; corregge quelli risolvibili (incorpora i contenuti esterni, toglie le azioni vietate, imposta l'intento di output); rimuove la dichiarazione di conformità; funziona anche in blocco. | PDF/A-1…4, PDF/X-1a/3/4, PDF/E, PDF/UA | A | n.d. | Richiede un validatore; per PDF/X non esistono validatori liberi completi. |
| 6.42 Conversione del colore | Converte i colori tra RGB, CMYK e scala di grigi e le tinte piatte in quadricromia con profili ICC e intenti di resa; assegna un profilo alle immagini che ne sono prive; imposta o incorpora l'intento di output. | ICC (ISO 15076-1), Separation/DeviceN, intenti di output | A | n.d. | Richiede un motore di gestione del colore. |
| 6.43 Preflight | Verifica il file con profili configurabili (colori, font, trasparenze, risoluzione delle immagini, copertura d'inchiostro, versione, parametri di stampa obsoleti) e applica correzioni automatiche; produce un rapporto; eseguibile in blocco. | Rapporto PDF, XML o testo; PDF/X | A | n.d. | — |
| 6.44 Anteprima di output | Anteprima delle separazioni di colore, simulazione di sovrastampa e carta, avvisi sulla copertura totale d'inchiostro, gestione delle tinte piatte, preimpostazioni di trapping, anteprima dell'appiattimento delle trasparenze, segni di stampa, correzione delle linee troppo sottili. | PDF/X, ICC | A | n.d. | — |
| 6.45 Struttura logica e tag | Pannello per vedere, creare e modificare la struttura del documento (titoli, paragrafi, elenchi, tabelle, figure, campi, anche dei moduli solo stampabili) e il legame di ogni elemento con il contenuto. | PDF con tag; PDF/UA | A | n.d. | Le modifiche al contenuto devono conservare i tag (3.01). |
| 6.46 Etichettatura automatica | Crea da sé i tag di un documento non strutturato riconoscendone il layout (titoli, colonne, tabelle, figure, elementi decorativi). | PDF con tag | A | n.d. | Sulle scansioni serve prima l'OCR. |
| 6.47 Ordine di lettura | Strumento per tracciare le zone della pagina e fissarne tipo e ordine di lettura (testo, figura, tabella, elemento decorativo), con correzione della struttura delle tabelle. | PDF con tag | A | n.d. | — |
| 6.48 Testo alternativo e lingua | Assegna descrizioni alternative a figure e formule, la lingua del documento e di singoli passaggi, il testo da leggere al posto di abbreviazioni e simboli. | Alt, Lang, ActualText | A | n.d. | — |
| 6.49 Verifica dell'accessibilità | Controlla il documento secondo PDF/UA e le linee guida WCAG con elenco delle regole, rapporto dei problemi e collegamento all'elemento da correggere. | PDF/UA (ISO 14289), WCAG | A | n.d. | — |
| 6.50 Procedura guidata di accessibilità | Percorso guidato che porta un documento all'accessibilità: OCR se serve, lingua, tag, testo alternativo, verifica finale. | PDF/UA | A | n.d. | Salvabile come azione guidata (11.17). |

## 7. OCR

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 7.01 Riconoscimento del testo | Riconosce il testo nelle pagine scansionate e lo rende ricercabile, selezionabile e modificabile, con più modalità di risultato (testo invisibile sotto l'immagine oppure testo modificabile). | PDF immagine → PDF ricercabile o modificabile | P | sì | Richiede un motore OCR locale. |
| 7.02 Riconoscimento multilingue | Riconosce testi in oltre 38 lingue. | Pacchetti di lingua OCR | P | sì | Estensione: più lingue combinate nello stesso documento. |
| 7.03 Copia del testo da scansioni | Seleziona e copia il testo di una pagina scansionata riconoscendolo al momento. | — | P | sì | — |
| 7.04 PDF di sole immagini | Trasforma un PDF ricercabile o modificabile in un PDF di sole immagini. | PDF → PDF raster | P | sì | Estensione: rasterizzazione delle sole pagine scelte a risoluzione impostabile. |
| 7.05 Risultato OCR accessibile | Esporta il risultato del riconoscimento come PDF accessibile, con struttura e lingua. | PDF/UA | P | sì | Estensione: uscita PDF/A-2b o PDF/A-2u. |
| 7.06 Impostazioni di rilevamento | Regola come e quando il programma riconosce le pagine scansionate e propone l'OCR. | — | P | sì | — |
| 7.07 Correzione delle parole incerte | Elenca le parole riconosciute con bassa affidabilità e le mostra accanto all'immagine originale per correggerle una per una. | — | A | n.d. | — |
| 7.08 OCR mirato | Riconosce solo un'area scelta o le immagini selezionate e accetta linee tracciate dall'utente per guidare il riconoscimento delle tabelle. | — | A | n.d. | — |
| 7.09 Miglioramento delle scansioni | Raddrizza le pagine storte, toglie sfondo e rumore, attenua la retinatura delle stampe, rende più nitido il testo e corregge la prospettiva delle foto, su pagine scelte o in blocco. | Pagine raster | A | n.d. | Richiede elaborazione d'immagine locale. |

## 8. Moduli

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 8.01 Creazione di campi | Crea campi di testo, caselle di controllo, pulsanti di scelta, menu a tendina, elenchi, pulsanti, campi immagine, campi data e campi per la firma digitale. | AcroForm (ISO 32000-2 §12.7) | P | sì | — |
| 8.02 Riconoscimento automatico dei campi | Trova in un modulo non compilabile le zone da riempire e vi crea i campi. | AcroForm | P | sì | Estensione: nomi coerenti, rinomina dei duplicati, variante con IA per layout complessi (richiede fornitore IA configurato dall'utente). |
| 8.03 Compilazione | Compila i campi di un modulo PDF e ne salva i valori. | AcroForm | P | sì | — |
| 8.04 Modifica dei campi | Cambia proprietà, aspetto, posizione e dimensione dei campi, anche di più campi insieme. | — | P | sì | — |
| 8.05 Duplicazione di un campo | Crea copie di un campo nella stessa pagina. | — | P | sì | — |
| 8.06 Campi su più pagine | Replica un campo nella stessa posizione su più pagine. | — | P | sì | — |
| 8.07 Elenco dei campi | Pannello con tutti i campi del documento per selezionarli e modificarli. | — | P | sì | Estensione: ricerca, rinomina e proprietà di massa. |
| 8.08 Anteprima del modulo | Mostra il modulo come lo vedrà chi lo compila, per provarlo. | — | P | sì | — |
| 8.09 Reimpostazione | Svuota tutti i campi o quelli scelti. | — | P | sì | — |
| 8.10 Importazione ed esportazione dei dati | Salva i valori compilati in un file dati e li reimporta in un modulo vuoto. | FDF, XFDF, XML, TXT | P | sì | XML e TXT documentati nella cronologia delle versioni. |
| 8.11 Proprietà avanzate dei campi | Campi di testo su più righe, per password, per la scelta di un file o a caselle, lunghezza massima, testo formattato e dimensione automatica, suggerimento al passaggio del mouse, sola lettura, obbligatorio, esclusione dall'esportazione, ordine di tabulazione, allineamento e distribuzione, campi con lo stesso nome che condividono il valore. | AcroForm: flag dei campi e testo variabile | A | n.d. | — |
| 8.12 Calcolo, validazione e formato | Campi calcolati (somma, prodotto, media, minimo, massimo, formula), controllo dei valori ammessi, formati di numero, valuta, data, ora, percentuale, CAP e telefono, maschere di inserimento, ordine di calcolo modificabile. | AcroForm con script | A | n.d. | Richiede il motore di script (8.18). |
| 8.13 Compilazione da tabella ed estrazione in tabella | Genera molte copie compilate di un modulo da un file CSV (una riga per documento) e raccoglie in un'unica tabella i valori di molti moduli compilati. | CSV, JSON, XFDF | A | n.d. | — |
| 8.14 Invio del modulo | Pulsante che invia i dati compilati a un indirizzo web o per e-mail, nel formato scelto. | FDF, XFDF, HTML, PDF | A | n.d. | Richiede rete o programma di posta. |
| 8.15 Distribuzione e raccolta delle risposte | Invia un modulo a più destinatari e raccoglie le risposte in un file riepilogativo con prospetto di chi ha risposto, esportabile in CSV o XML. | PDF, CSV, XML | A | n.d. | Raccolta via e-mail o cartella condivisa; la raccolta su server cloud è fuori ambito. |
| 8.16 Moduli XFA: riconoscimento e conversione | Riconosce i moduli XFA e ne segnala i limiti, compila quelli statici, estrae e importa i dati, li converte in moduli standard o li appiattisce in pagine fisse, toglie la parte XFA per la conformità PDF/A e PDF/UA. | XFA 3.3, XDP | A | n.d. | XFA è deprecato in PDF 2.0 ma ancora diffuso negli enti pubblici. |
| 8.17 Moduli XFA dinamici | Apre e compila moduli XFA dinamici con layout variabile, script e validazioni, e ne salva i dati. | XFA 3.3 | A | n.d. | Richiede un interprete XFA dedicato, senza implementazioni libere complete: da valutare. |
| 8.18 Script nei documenti | Esegue gli script dei documenti (calcoli, validazioni, pulsanti) in un ambiente isolato e solo con il consenso dell'utente; console per provarli; elenco e rimozione di tutti gli script; opzione per disattivarli; compatibilità con l'interfaccia di scripting diffusa di fatto. | JavaScript per PDF (ISO 21757-1) | A | n.d. | Lacuna principale del riferimento; PDF/A vieta l'esecuzione degli script. |
| 8.19 Eventi e azioni automatiche | Editor che lega azioni a eventi: apertura e chiusura del documento o di una pagina, prima e dopo salvataggio e stampa, mouse e focus su campi e annotazioni, digitazione, formattazione, validazione e ricalcolo. | Azioni aggiuntive (ISO 32000-2 §12.6.3) | A | n.d. | — |
| 8.20 Rigenerazione dell'aspetto | Ricrea l'aspetto visivo di campi e annotazioni compilati da programmi che non l'hanno aggiornato, perché appaiano uguali in ogni lettore e in stampa. | Appearance stream | A | n.d. | Necessaria anche per PDF/A. |
| 8.21 Pagine modello | Elenca le pagine nascoste usate come modelli, le mostra o le nasconde e consente agli script di aggiungerne copie (per esempio un foglio di dettaglio in più). | Pagine template (ISO 32000-2 §12.7.7) | A | n.d. | — |

## 9. Firma

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 9.01 Firma autografa elettronica | Inserisce una firma disegnata a mano, importata da un'immagine o da una scansione, oppure digitata con un carattere calligrafico. | Immagine, tratto vettoriale | P | sì | — |
| 9.02 Archivio delle firme | Conserva le firme create per riusarle in altri documenti. | — | P | sì | La sincronizzazione tra dispositivi è fuori ambito (13.02). |
| 9.03 Sigle e firma su più pagine | Applica firma o iniziali su più pagine in un solo passaggio; esporta e importa firme e sigle. | — | A | n.d. | — |
| 9.04 Firma digitale con certificato | Firma il documento con un certificato digitale, indicando motivo, luogo e recapito. | Certificati PKCS#12; firma CMS | P | sì | Profili PAdES: 9.11. |
| 9.05 Smart card, token e archivi di certificati | Firma con certificati custoditi su smart card, token crittografici, nell'archivio del sistema o su moduli hardware, scegliendo gli algoritmi. | PKCS#11; RSA, RSA-PSS, ECDSA, EdDSA; SHA-2, SHA-3 | A | n.d. | — |
| 9.06 Aspetto della firma digitale e sigilli | Firma visibile personalizzabile (nome, data, motivo, logo o immagine della firma, testo libero) oppure invisibile; sigilli elettronici di un'organizzazione. | — | A | n.d. | — |
| 9.07 Firma di certificazione | Certifica il documento stabilendo cosa resta permesso dopo la firma (nessuna modifica; compilazione e firme; anche annotazioni) e blocca campi specifici dopo una firma; indica chi ha certificato; riconosce e rimuove i vecchi diritti d'uso aggiunti per i lettori gratuiti. | DocMDP, FieldMDP (ISO 32000-2 §12.8.2) | A | n.d. | — |
| 9.08 Marca temporale | Aggiunge alla firma una marca temporale rilasciata da un server affidabile e permette di apporre marche temporali al documento anche senza firma. | RFC 3161; PAdES B-T | A | n.d. | Richiede rete e un server di marca temporale configurato. |
| 9.09 Validazione delle firme | Pannello firme con esito (valida, non valida, indeterminata), catena dei certificati, stato di revoca, modifiche fatte dopo ogni firma e vista del documento com'era al momento della firma; gestione delle identità attendibili e degli elenchi di fiducia. | X.509, OCSP, CRL, elenchi di fiducia UE | A | n.d. | Richiede rete per i controlli di revoca. |
| 9.10 Validazione a lungo termine | Incorpora certificati e risposte di revoca necessari a verificare le firme anche dopo la scadenza dei certificati, aggiunge e rinnova marche temporali d'archivio, mostra lo stato di ogni firma. | DSS/VRI, marca temporale di documento; PAdES B-LT e B-LTA | A | n.d. | Richiede rete. |
| 9.11 Profili PAdES e firme qualificate | Sceglie il livello di firma (base, con marca temporale, con dati di validazione, d'archivio), distingue firma avanzata e qualificata con dispositivo sicuro, applica politiche di firma, riconosce i formati precedenti; estrae o converte i file firmati in busta .p7m. | PAdES (ETSI EN 319 142-1/-2), CAdES, eIDAS | A | n.d. | Rilevante per le regole italiane sui documenti informatici. |
| 9.12 Preparazione dei campi firma | Prepara un documento da far firmare a più persone: posizione dei campi, vincoli per chi firma (certificati ammessi, motivi, marca temporale obbligatoria, politica), campi da bloccare dopo ogni firma, ordine dei firmatari. | Seed value e lock dei campi firma (ISO 32000-2 §12.7.5.5) | A | n.d. | Alternativa locale al servizio di richiesta firme (13.10). |

## 10. Protezione e sicurezza

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 10.01 Password di apertura | Cifra il documento perché si apra solo con la password. | AES-256; AES-128 e RC4 solo in lettura | P | sì | Opzione per lasciare leggibili i metadati; PDF/A vieta la cifratura. |
| 10.02 Password dei permessi | Limita stampa, modifica, copia, annotazione, compilazione e riorganizzazione delle pagine con una password del proprietario. | Permessi del documento | P | sì | In PDF 2.0 l'estrazione per l'accessibilità resta sempre permessa. |
| 10.03 Proprietà di sicurezza | Mostra se il documento è protetto, con quale metodo e quali operazioni sono permesse. | — | P | no | — |
| 10.04 Rimozione della protezione | Toglie password e restrizioni a chi conosce le credenziali. | — | P | sì | Solo con la password corretta: nessuna elusione delle protezioni. |
| 10.05 Cifratura con certificato | Cifra il documento per destinatari scelti usando i loro certificati, senza password da condividere, con permessi diversi per ciascuno. | Gestore di sicurezza a chiave pubblica; X.509, CMS | A | n.d. | — |
| 10.06 Cifratura selettiva e buste | Cifra solo gli allegati lasciando leggibile il documento, o tutto tranne i metadati; crea buste PDF cifrate che trasportano file di qualsiasi tipo e documenti involucro leggibili che spiegano come aprire il contenuto protetto. | Crypt filter; payload cifrato (PDF 2.0) | A | n.d. | — |
| 10.07 Cifratura AES-GCM e integrità | Cifra con l'algoritmo AES-GCM e protegge l'integrità dei documenti cifrati secondo le specifiche tecniche più recenti. | ISO/TS 32003, ISO/TS 32004 | A | n.d. | — |
| 10.08 Criteri di sicurezza | Salva combinazioni di password, permessi e certificati come criteri da applicare con un clic o in blocco; importazione ed esportazione; indicatore di robustezza della password. | — | A | n.d. | — |
| 10.09 Gestione dei diritti aziendale | Apre e protegge documenti con le etichette di riservatezza dei sistemi aziendali di gestione dei diritti. | PDF protetti da IRM | A | n.d. | Fuori ambito: richiede SDK e servizi proprietari non disponibili per Linux. |
| 10.10 Redazione | Oscura in modo definitivo testo, immagini e aree selezionate, anche su più pagine, eliminando il contenuto sottostante. | Annotazioni di redazione | P | sì | Richiede il salvataggio completo (6.32), non incrementale. |
| 10.11 Redazione avanzata | Trova e marca dati sensibili per modello (codici fiscali, numeri di telefono e di carte, e-mail, date, espressioni regolari), applica codici di redazione con testo sovrapposto, oscura pagine intere o aree ripetute, usa effetti sfocati o pixelati, mostra un'anteprima prima di applicare. | — | A | n.d. | In blocco: 11.15. |
| 10.12 Suggerimenti di redazione con IA | Propone i dati personali da oscurare analizzando il contenuto; l'utente li rivede e li conferma. | — | A | n.d. | Richiede fornitore IA configurato dall'utente. |
| 10.13 Sanificazione | Elimina contenuti nascosti e metadati prima di diffondere il documento. | — | P | sì | Estensione: scelta degli elementi da togliere (metadati, allegati, script, livelli nascosti, indici, dati dei moduli, dati privati di altre applicazioni). |
| 10.14 Appiattimento | Fonde nel contenuto annotazioni, campi compilati, filigrane e ritagli, rendendoli non più modificabili. | — | P | sì | Estensione: appiattimento selettivo per tipo o pagina, conservando i collegamenti. |
| 10.15 Cartella protetta | Spazio riservato e protetto dove custodire i documenti confidenziali. | — | P | sì | Nel riferimento solo su mobile; su desktop come archivio cifrato locale. |
| 10.16 Blocco dell'applicazione | Chiede un codice o l'autenticazione del sistema per accedere all'app. | — | P | no | Nel riferimento solo su mobile (biometria o codice). |
| 10.17 Connessioni di rete protette | Tutte le comunicazioni di rete passano su canali cifrati, con supporto ai proxy. | TLS | P | n.d. | In Foliant riguarda fornitore IA, server di marca temporale e di revoca, apertura da indirizzo web. |
| 10.18 Apertura protetta | Apre i file di provenienza non attendibile in sola lettura isolata finché l'utente non li abilita; blocca per impostazione predefinita script, avvio di programmi, accessi esterni e contenuti multimediali; elenco di posizioni attendibili. | — | A | n.d. | — |
| 10.19 Criteri amministrativi | Impostazioni predefinite e bloccabili dall'amministratore (per esempio disattivare l'IA), installazione silenziosa e distribuzione centralizzata degli aggiornamenti. | File di configurazione di sistema; pacchetti | A | n.d. | La console online è fuori ambito (13.14). |

## 11. Confronto e operazioni in blocco

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 11.01 Confronto di documenti | Confronta due versioni di un PDF e mostra, affiancate o sovrapposte, le differenze di contenuto e di attributi del testo. | PDF | P | sì | Confronto degli attributi del testo documentato nella cronologia delle versioni. |
| 11.02 Confronto avanzato | Modalità per tipo di documento (testi, presentazioni, scansioni), confronto del solo testo, rapporto di sintesi con filtri, accettazione o rifiuto di ogni differenza, esportazione del rapporto; confronto di due testi semplici. | PDF, TXT; rapporto PDF | A | n.d. | Le scansioni richiedono l'OCR. |
| 11.03 In blocco: conversione | Converte molti PDF insieme nel formato scelto. | Formati di esportazione (area 6) | P | sì | — |
| 11.04 In blocco: creazione | Crea molti PDF in una sola operazione da file di tipo diverso. | Formati di creazione (area 6) | P | sì | Accorpa la voce "creazione di PDF multipli" dell'inventario. |
| 11.05 In blocco: inserimento di pagine | Inserisce le stesse pagine (per esempio una copertina) in molti documenti. | PDF | P | sì | — |
| 11.06 In blocco: stampa | Stampa molti documenti in sequenza, anche fronte/retro. | — | P | sì | — |
| 11.07 In blocco: cifratura | Applica password e permessi a molti documenti. | — | P | sì | — |
| 11.08 In blocco: OCR | Riconosce il testo di molti documenti scansionati. | — | P | sì | — |
| 11.09 In blocco: compressione | Riduce il peso di molti documenti. | — | P | sì | — |
| 11.10 In blocco: filigrana | Applica la stessa filigrana a molti documenti. | — | P | sì | — |
| 11.11 In blocco: intestazione e piè di pagina | Applica intestazioni e piè di pagina a molti documenti. | — | P | sì | — |
| 11.12 In blocco: sfondo | Applica lo stesso sfondo a molti documenti. | — | P | sì | — |
| 11.13 In blocco: rimozione di elementi | Toglie filigrane, sfondi, intestazioni e piè di pagina da molti documenti. | — | P | sì | — |
| 11.14 Numerazione Bates | Applica a una serie di documenti una numerazione progressiva univoca con prefisso, suffisso, numero di cifre e ripartenza per documento, per uso legale. | — | P | sì | Marcata come artefatto. |
| 11.15 In blocco: redazione | Applica gli stessi criteri di redazione a molti documenti. | — | A | n.d. | — |
| 11.16 In blocco: proprietà e opzioni di apertura | Imposta metadati, proprietà e opzioni di apertura su molti documenti. | XMP | A | n.d. | — |
| 11.17 Azioni guidate | Definisce sequenze di passaggi (per esempio OCR, compressione, intestazione, PDF/A, invio per e-mail, salvataggio in una cartella) con eventuali richieste all'utente; le esegue su file, cartelle o allegati, anche trascinando i file su un'icona; le salva come profili riutilizzabili, condivisibili ed esportabili, con regole di nome per i file prodotti. | File di definizione dell'azione | A | n.d. | — |
| 11.18 Cartelle monitorate | Cartelle sorvegliate in cui ogni file copiato viene elaborato in automatico con un'azione o un profilo. | — | A | n.d. | — |
| 11.19 Riga di comando e automazione | Esegue conversioni, stampe, protezioni e azioni da terminale o da script, anche senza interfaccia grafica e in ambienti remoti, per inserirle in procedure automatiche. | Comandi da terminale; D-Bus | A | n.d. | — |

## 12. Assistente IA

Tutte le funzioni di quest'area richiedono un fornitore IA scelto e configurato dall'utente (modello locale o servizio remoto); nessuna dipende da un servizio del produttore di Foliant.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 12.01 Riassunto | Riassume uno o più documenti, le pagine scelte o un testo selezionato o incollato, con i punti chiave. | PDF, testo | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.02 Riassunto per sezione | Riassume la parte del documento che corrisponde a un segnalibro. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.03 Riassunto di documentazione tecnica | Riassunti pensati per manuali e documenti tecnici, che conservano dati, parametri e procedure. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.04 Traduzione | Traduce l'intero documento conservando l'impaginazione, oppure pagine, testo o immagini selezionati, in più di dodici lingue; sulle scansioni applica prima l'OCR. | PDF → PDF tradotto | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.05 Chat con il documento | Domande e risposte sul contenuto di un PDF. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.06 Chat sulle immagini | Spiega, traduce o estrae il testo di immagini e catture di schermo. | Immagini | P | sì | Richiede fornitore IA configurato dall'utente (modello con visione). |
| 12.07 Chat generica | Assistente conversazionale per studio e lavoro, con scelta del modello. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.08 Spiegazioni | Spiega termini e passaggi selezionati o incollati e salva le spiegazioni come note nel documento. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.09 Mappa mentale | Ricava dal documento una mappa mentale da consultare, salvare, condividere o inserire nel PDF. | Immagine, PDF | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.10 Comandi in linguaggio naturale | L'utente scrive cosa vuole ottenere; l'assistente sceglie lo strumento adatto e lo prepara, lasciando all'utente l'avvio. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.11 Scrittura assistita | Genera articoli, e-mail, post e altri testi a partire da indicazioni. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.12 Contenuti per video online | Propone titoli, descrizioni, parole chiave e copioni per video. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.13 Ricerca bibliografica approfondita | Scompone una domanda in sotto-domande, raccoglie e analizza articoli scientifici e produce una rassegna della letteratura salvabile in PDF. | PDF | P | sì | Richiede fornitore IA configurato dall'utente e accesso di rete a banche dati bibliografiche. |
| 12.14 Ricerca di articoli scientifici | Trova articoli per argomento o parole chiave, li riassume, consente di discuterne e li conserva in una raccolta personale. | — | P | sì | Richiede fornitore IA configurato dall'utente e accesso di rete a banche dati bibliografiche. |
| 12.15 Analisi di un articolo scientifico | Analizza un articolo caricato: obiettivi, metodo, risultati e limiti. | PDF | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.16 Aiuto per i compiti | Aiuta a svolgere esercizi spiegandone i passaggi. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.17 Generatore di quiz | Crea quiz su un argomento o sul contenuto di un documento. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.18 Problemi di matematica | Risolve problemi di matematica mostrando tutti i passaggi. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.19 Bozze di contratti | Prepara bozze di contratti a partire dalle indicazioni dell'utente. | DOCX, PDF | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.20 Assistente legale | Valuta i rischi di un contratto, svolge ricerche giuridiche e prepara comunicazioni per i clienti. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.21 Revisione dei contratti | Esamina un contratto clausola per clausola segnalando punti critici e lacune. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.22 Piano d'impresa | Genera un piano d'impresa strutturato. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.23 Supporto alla strategia | Aiuta a impostare analisi e decisioni strategiche d'impresa. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.24 Spiegazione dei bilanci | Spiega con parole semplici voci e indicatori di un bilancio. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.25 Analisi dei flussi di cassa | Analizza il rendiconto finanziario mettendo in luce andamenti e criticità. | — | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.26 Lettura dei curriculum | Ricava dai CV in PDF le informazioni principali in forma ordinata. | PDF | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.27 Selezione dei curriculum | Confronta più CV con i requisiti di una posizione e li ordina. | PDF | P | sì | Richiede fornitore IA configurato dall'utente. |
| 12.28 Estrazione strutturata di dati | Estrae tabelle, anche senza bordi, e dati di moduli compilati da uno o più PDF in fogli di calcolo o file strutturati; costruisce tabelle e calcoli su richiesta. | XLSX, CSV, JSON | A | n.d. | Richiede fornitore IA configurato dall'utente. |
| 12.29 Riassunto audio | Produce una panoramica parlata del documento, anche a più voci in forma di conversazione, ascoltabile a velocità variabile. | MP3, WAV | A | n.d. | Richiede fornitore IA configurato dall'utente (con sintesi vocale). |
| 12.30 Spazio di lavoro multi-documento | Riunisce più documenti (anche non PDF e scansioni) e risponde con citazioni cliccabili alle fonti, mettendo in luce i temi comuni. | PDF, documenti d'ufficio, immagini | A | n.d. | Richiede fornitore IA configurato dall'utente; la condivisione con un assistente per i destinatari è fuori ambito. |
| 12.31 Presentazioni e materiali dal documento | Genera da uno o più documenti presentazioni, rapporti, diagrammi o materiali promozionali. | PPTX, PDF | A | n.d. | Richiede fornitore IA configurato dall'utente. |
| 12.32 Analisi per tipo di documento | Riconosce il tipo di documento (contratto, verbale, fattura…) e propone analisi dedicate, compresi controlli di coerenza e segnali di possibile frode. | — | A | n.d. | Richiede fornitore IA configurato dall'utente. |
| 12.33 Generazione e ritocco di immagini | Crea o modifica immagini da inserire nel documento (sfondi, ridimensionamento, varianti). | PNG, JPEG | A | n.d. | Richiede fornitore IA configurato dall'utente (generazione di immagini). |
| 12.34 Collegamento con agenti esterni | Mette le funzioni dell'editor a disposizione di agenti IA esterni tramite un protocollo standard e permette all'assistente di usare strumenti esterni scelti dall'utente. | Model Context Protocol (JSON-RPC) | A | n.d. | Come client richiede fornitore IA configurato dall'utente. |

## 13. Cloud e sincronizzazione (fuori ambito)

Funzioni che presuppongono uno spazio online o un servizio gestito dal produttore o da terzi. Foliant lavora su file locali e non gestisce account né server; i servizi di rete restano raggiungibili con gli strumenti del sistema operativo. L'elenco serve a tracciare la parità e a motivare l'esclusione.

| Funzione | Descrizione per l'utente | Formati | Parità | Nel riferimento a pagamento | Note |
|---|---|---|---|---|---|
| 13.01 Archivio cloud del produttore | Carica, scarica, modifica e sincronizza i file in uno spazio online, con cartelle e file condivisi. | — | P | no | Fuori ambito: richiede un servizio online gestito; Foliant lavora su file locali. |
| 13.02 Sincronizzazione tra dispositivi | Allinea documenti e firme salvate tra computer e dispositivi mobili. | — | P | n.d. | Fuori ambito: richiede un servizio di sincronizzazione; le firme restano nell'archivio locale (9.02). |
| 13.03 Archiviazione cloud cifrata | Conserva cifrati i file nello spazio online. | — | P | n.d. | Fuori ambito: dipende da 13.01. |
| 13.04 Condivisione tramite link | Genera un link al documento caricato online. | — | P | no | Fuori ambito: richiede che i file siano ospitati su un server; restano l'invio per e-mail (1.38) e le cartelle condivise. |
| 13.05 Condivisione tramite codice QR | Genera un codice QR che punta al documento condiviso online. | — | P | n.d. | Fuori ambito: dipende da 13.04. |
| 13.06 Collegamento a Dropbox | Apre e salva direttamente su Dropbox. | — | P | n.d. | Fuori ambito come connettore interno: su Linux si usa il client di sincronizzazione del servizio, che espone una cartella locale. |
| 13.07 Collegamento WebDAV | Accede a server WebDAV. | WebDAV | P | n.d. | Fuori ambito come connettore interno: su Linux il file system virtuale del desktop rende questi percorsi raggiungibili dal selettore file standard. |
| 13.08 Collegamento FTP/SFTP | Accede a server FTP e SFTP. | FTP, SFTP | P | n.d. | Fuori ambito come connettore interno: come 13.07. |
| 13.09 Collegamento SMB | Accede a cartelle condivise di rete. | SMB | P | n.d. | Fuori ambito come connettore interno: come 13.07. |
| 13.10 Richiesta di firme a più parti | Servizio online che invia documenti a più firmatari con ordine, promemoria e registro delle attività; invio massivo, modelli, moduli web, raccolta di pagamenti, autenticazione notarile. | — | P | sì | Fuori ambito: richiede server, identificazione dei firmatari e conformità del servizio; alternativa locale in 9.12. |
| 13.11 Firma per aziende | Versione aziendale del servizio di firma, con gestione avanzata dei firmatari. | — | P | sì | Fuori ambito: come 13.10. |
| 13.12 Connettori a cloud e sistemi documentali | Apertura e salvataggio su altri servizi cloud e su sistemi documentali aziendali con prelievo e deposito delle versioni, ricerca nei file remoti, salvataggio dei documenti firmati su archivi esterni. | — | A | n.d. | Fuori ambito: richiede API, SDK proprietari e account dei servizi. |
| 13.13 Collaborazione in tempo reale | Revisione su server cloud con commenti sincronizzati, notifiche di attività e raccolta di pareri senza account. | — | A | n.d. | Fuori ambito: richiede un servizio online; alternativa locale in 5.31. |
| 13.14 Amministrazione centralizzata online | Console web per licenze, utenti e ruoli, accesso unico aziendale e statistiche d'uso. | — | A | n.d. | Fuori ambito: richiede un backend del produttore; criteri locali in 10.19. |

## Riepilogo

| Area | Funzioni | P | A | Fuori ambito |
|---|---|---|---|---|
| 1. Visualizzazione e navigazione | 38 | 23 | 15 | 0 |
| 2. Gestione pagine | 20 | 14 | 6 | 0 |
| 3. Modifica testo | 15 | 11 | 4 | 0 |
| 4. Immagini, oggetti e collegamenti | 30 | 12 | 18 | 0 |
| 5. Annotazioni e commenti | 31 | 24 | 7 | 0 |
| 6. Conversione, creazione e ottimizzazione | 50 | 24 | 26 | 0 |
| 7. OCR | 9 | 6 | 3 | 0 |
| 8. Moduli | 21 | 10 | 11 | 0 |
| 9. Firma | 12 | 3 | 9 | 0 |
| 10. Protezione e sicurezza | 19 | 10 | 9 | 1 |
| 11. Confronto e operazioni in blocco | 19 | 13 | 6 | 0 |
| 12. Assistente IA | 34 | 27 | 7 | 0 |
| 13. Cloud e sincronizzazione | 14 | 11 | 3 | 14 |
| **Totale** | **312** | **188** | **124** | **15** |

- **In ambito**: 297 funzioni (177 P, 120 A). Fuori ambito: le 14 dell'area 13 e 10.09.
- **Con IA**: 47 funzioni richiedono un fornitore IA configurato dall'utente (39 P, 8 A): le 34 dell'area 12 più 1.24, 1.29, 2.18, 3.12–3.15, 4.14, 4.17, 5.15, 5.18, 6.31 e 10.12.
- **Deduplicazione dell'inventario**: le 189 voci confluiscono in 186 righe P, perché tre erano doppioni ("Preferiti" in 1.03, "unione in blocco" in 2.06, "creazione di PDF multipli" in 11.04). Altre 2 righe P (1.01 e 6.16) vengono dalla cronologia delle versioni del riferimento: totale 188 P.
- **Aggiunte**: le 124 righe A fondono le schede sui concorrenti e le capacità dello standard; quando una di queste coincideva con una funzione del riferimento è confluita nella riga P corrispondente, come "Estensione" nelle Note se aggiungeva solo un'opzione.

### Corrispondenza con l'inventario di parità

| Area dell'inventario (voci) | Voce → riga del catalogo |
|---|---|
| 1 Visualizzazione e navigazione (20) | 1→1.08 · 2→1.10 · 3→1.11 · 4→1.12 · 5→1.09 · 6→1.02 · 7→1.27 · 8→3.06 · 9→3.07 · 10→1.29 · 11→1.18 · 12→1.19 · 13→1.23 · 14→1.24 · 15→12.02 · 16→1.13 · 17→1.22 · 18→1.31 · 19→1.06 · 20→1.33 |
| 2 Gestione pagine (14) | 1→2.01 · 2→2.02 · 3→2.03 · 4→2.04 · 5→2.06 · 6→2.07 · 7→2.08 · 8→2.09 · 9→2.11 · 10→2.10 · 11→2.12 · 12→2.13 · 13→2.14 · 14→2.18 |
| 3 Modifica testo (9) | 1→3.01 · 2→3.02 · 3→3.03 · 4→3.04 · 5→3.05 · 6→3.12 · 7→3.13 · 8→3.14 · 9→3.15 |
| 4 Immagini e oggetti (12) | 1→4.01 · 2→4.02 · 3→4.03 · 4→4.05 · 5→4.21 · 6→4.13 · 7→4.14 · 8→4.16 · 9→4.17 · 10→4.18 · 11→4.07 · 12→4.08 |
| 5 Annotazioni (24) | 1…18→5.01…5.18 · 19→5.20 · 20→5.23 · 21→5.24 · 22→5.26 · 23→5.27 · 24→5.29 |
| 6 Conversione (12) | 1→6.19 · 2→6.20 · 3→6.21 · 4→6.22 · 5→6.23 · 6→6.24 · 7→6.25 · 8→6.26 · 9→6.27 · 10→6.28 · 11→6.30 · 12→6.31 |
| 7 OCR (6) | 1→7.01 · 2→7.04 · 3→7.03 · 4→7.05 · 5→7.02 · 6→7.06 |
| 8 Moduli (10) | 1→8.01 · 2→8.02 · 3→8.03 · 4→8.04 · 5→8.10 · 6→8.09 · 7→8.08 · 8→8.05 · 9→8.06 · 10→8.07 |
| 9 Firma (5) | 1→9.01 · 2→9.02 (sincronizzazione in 13.02) · 3→9.04 · 4→13.10 · 5→13.11 |
| 10 Protezione e sicurezza (12) | 1→10.01 · 2→10.02 · 3→10.03 · 4→10.04 · 5→10.10 · 6→10.13 · 7→10.14 · 8→6.40 · 9→10.15 · 10→10.16 · 11→13.03 · 12→10.17 |
| 11 Confronto e operazioni in blocco (14) | 1→11.01 · 2→11.03 · 3→11.04 · 4→11.05 · 5→2.06 (doppione) · 6→11.06 · 7→11.07 · 8→11.08 · 9→11.09 · 10→11.10 · 11→11.11 · 12→11.12 · 13→11.13 · 14→11.14 |
| 12 Funzioni IA (26) | 1→12.01 · 2→12.04 · 3→12.05 · 4→12.06 · 5→12.07 · 6→12.09 · 7→12.08 · 8→12.10 · 9→12.11 · 10→12.12 · 11→12.13 · 12→12.14 · 13→12.15 · 14→12.16 · 15→12.17 · 16→12.18 · 17→12.19 · 18→12.20 · 19→12.21 · 20→12.22 · 21→12.23 · 22→12.24 · 23→12.25 · 24→12.26 · 25→12.27 · 26→12.03 |
| 13 Creazione PDF (9) | 1→6.02 · 2→6.04 · 3→6.06 · 4→6.08 · 5→6.10 · 6→6.09 · 7→6.01 · 8→6.12 · 9→11.04 (doppione) |
| 14 Stampa, condivisione e output (6) | 1→1.36 · 2→6.34 · 3→13.04 · 4→13.05 · 5→1.38 · 6→6.32 |
| 15 Cloud e integrazioni (6) | 1→13.01 · 2→13.02 · 3→13.06 · 4→13.07 · 5→13.08 · 6→13.09 |
| 16 Gestione file (4) | 1→1.03 · 2→1.04 · 3→1.05 · 4→1.03 (doppione) |

## Fonti

Indirizzi tratti dai file di ricerca (inventario di parità, ricerca sui concorrenti, inventario dello standard), consultati il 28 ottobre 2026; unificati e deduplicati (185 indirizzi unici, barre finali rimosse).

### Prodotto di riferimento

- https://updf.com
- https://updf.com/features
- https://updf.com/tech-spec
- https://updf.com/pricing
- https://updf.com/comparison/updf-free-vs-paid
- https://updf.com/updf-ai
- https://updf.com/whats-new
- https://updf.com/updf-2-5-released
- https://updf.com/news/updf-2-5-is-released
- https://updf.com/updf-windows-user-guide
- https://updf.com/updf-windows-user-guide/introduction-updf
- https://updf.com/updf-windows-user-guide/create-pdf-win
- https://updf.com/updf-windows-user-guide/convert-pdf
- https://updf.com/updf-windows-user-guide/toolbar-navigation
- https://updf.com/updf-windows-user-guide/updf-cloud-windows
- https://updf.com/updf-windows-user-guide/license-and-activate-updf-windows
- https://updf.com/updf-windows-user-guide/header-and-footer-windows
- https://updf.com/updf-windows-user-guide/batch-process-windows
- https://updf.com/updf-mac-user-guide/introduction-updf-mac
- https://updf.com/read-pdf/pdf-reader-linux
- https://updf.com/read-pdf/how-to-create-a-pdf-portfolio
- https://updf.com/edit-pdf/add-page-numbers-to-pdf
- https://updf.com/ocr/how-to-tag-a-pdf
- https://updf.com/knowledge/pdf-layer
- https://updf.com/knowledge/pades
- https://updf.com/protect-pdf/certify-pdf
- https://updf.com/pdf-form/how-to-create-digital-signature-in-pdf-with-timestamp
- https://sign.updf.com
- https://apps.apple.com/app/id1619925971
- https://pdfa.org/product/updf-20
- https://pdfa.org/updf-25-officially-launches-worldwide-the-first-pdf-editor-powered-by-autonomous-ai-agents
- https://maketecheasier.com/updf-review

### Adobe Acrobat

- https://www.adobe.com/acrobat/pricing/compare-versions.html
- https://www.adobe.com/acrobat/features.html
- https://www.adobe.com/acrobat/mobile.html
- https://www.adobe.com/acrobat/how-to/compare-two-pdf-files.html
- https://www.adobe.com/acrobat/how-to/create-fillable-pdf-forms-creator.html
- https://www.adobe.com/acrobat/hub/how-to-black-out-text-in-a-pdf-file.html
- https://www.adobe.com/acrobat/resources/how-to-redact-a-pdf.html
- https://www.adobe.com/devnet-docs/acrobat/android/en/lmode.html
- https://www.adobe.com/devnet-docs/acrobatetk/tools/AppSec/sandboxprotections.html
- https://www.insight.com/content/dam/global/na/us/pdfs/adobe/adobe-acrobat-premium-acrobat-pro-and-acrobat-standard-comparison.pdf
- https://www.insight.com/en_US/content-and-resources/brands/adobe/adobe-acrobat-pro-and-adobe-acrobat-standard-comparison.html
- https://opensource.adobe.com/dc-acrobat-sdk-docs/library/jsdevguide/JS_Dev_AcrobatForms.html
- https://experienceleague.adobe.com/en/docs/document-cloud-learn/acrobat-learning/accessibility-tutorials/make-accessible-action
- https://experienceleague.adobe.com/en/docs/document-cloud-learn/acrobat-learning/advanced-tasks/protect/redact
- https://league.adobe.com/docs/document-cloud-learn/acrobat-learning/advanced-tasks/action.html
- https://helpx.adobe.com/acrobat/current/pdf-properties-metadata.html
- https://helpx.adobe.com/acrobat/current/preflight-profiles-acrobat-pro.html
- https://helpx.adobe.com/acrobat/current/searching-pdfs.html
- https://helpx.adobe.com/acrobat/desktop/create-documents/optimize-pdfs/compress-without-fonts.html
- https://helpx.adobe.com/acrobat/desktop/create-documents/optimize-pdfs/pdf-optimizer-settings.html
- https://helpx.adobe.com/acrobat/desktop/create-documents/scan-documents-to-pdfs/enhance-images.html
- https://helpx.adobe.com/acrobat/desktop/create-documents/scan-documents-to-pdfs/enhance-scans.html
- https://helpx.adobe.com/acrobat/desktop/e-sign-documents/fill-sign-documents/add-time-stamps.html
- https://helpx.adobe.com/acrobat/desktop/e-sign-documents/fill-sign-documents/add-verification.html
- https://helpx.adobe.com/acrobat/desktop/e-sign-documents/manage-digital-signatures/validate-digital-sign.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/apply-bates-numbering/add-bates.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/apply-bates-numbering/bates-to-filenames.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/combine-files/insert-pdfs.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/edit-images-or-objects/add-objects.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/organize-pages/split-pdfs.html
- https://helpx.adobe.com/acrobat/desktop/edit-documents/pdf-layers/merge-layers.html
- https://helpx.adobe.com/acrobat/desktop/print-documents/booklets-posters-banners/about-booklets-printing.html
- https://helpx.adobe.com/acrobat/desktop/print-documents/booklets-posters-banners/posters-banners.html
- https://helpx.adobe.com/acrobat/desktop/print-documents/set-up-and-print-pdfs/multiple-pages.html
- https://helpx.adobe.com/acrobat/desktop/print-documents/use-secure-and-special-print-mode/print-comments.html
- https://helpx.adobe.com/acrobat/desktop/protect-documents/encrypt-with-certificates/certificate-encrypt.html
- https://helpx.adobe.com/acrobat/desktop/protect-documents/protect-with-passwords/security-options.html
- https://helpx.adobe.com/acrobat/desktop/protect-documents/use-protected-view/enable-protect-view.html
- https://helpx.adobe.com/acrobat/desktop/share-and-review-documents/share-documents/via-internal-servers.html
- https://helpx.adobe.com/acrobat/kb/approved-trust-list1.html
- https://helpx.adobe.com/acrobat/mobile/get-started/productivity-tips.html
- https://helpx.adobe.com/acrobat/using/action-wizard-acrobat-pro.html
- https://helpx.adobe.com/acrobat/using/adding-3d-models-pdfs-acrobat.html
- https://helpx.adobe.com/acrobat/using/automating-document-analysis-droplets-or.html
- https://helpx.adobe.com/acrobat/using/collecting-pdf-form-data.html
- https://helpx.adobe.com/acrobat/using/compare-documents.html
- https://helpx.adobe.com/acrobat/using/create-verify-pdf-accessibility.html
- https://helpx.adobe.com/acrobat/using/creating-distributing-pdf-forms.html
- https://helpx.adobe.com/acrobat/using/creating-pdf-indexes.html
- https://helpx.adobe.com/acrobat/using/editing-document-structure-content-tags.html
- https://helpx.adobe.com/acrobat/using/exporting-pdfs-file-formats.html
- https://helpx.adobe.com/acrobat/using/geospatial-pdfs.html
- https://helpx.adobe.com/acrobat/using/grids-guides-measurements-pdfs.html
- https://helpx.adobe.com/acrobat/using/merging-files-single-pdf.html
- https://helpx.adobe.com/acrobat/using/output-intents-pdfs-acrobat-pro.html
- https://helpx.adobe.com/acrobat/using/overview-pdf-portfolios.html
- https://helpx.adobe.com/acrobat/using/pdf-articles.html
- https://helpx.adobe.com/acrobat/using/pdf-forms.html
- https://helpx.adobe.com/acrobat/using/pdf-layers.html
- https://helpx.adobe.com/acrobat/using/previewing-output-acrobat-pro.html
- https://helpx.adobe.com/acrobat/using/rich-media.html
- https://helpx.adobe.com/acrobat/using/setting-3d-views-pdfs.html
- https://helpx.adobe.com/acrobat/using/trusted-identities.html
- https://helpx.adobe.com/acrobat/using/viewing-pdfs-viewing-preferences.html
- https://helpx.adobe.com/acrobat/web/e-sign-documents/manage-agreements/manage-bulk-send.html
- https://helpx.adobe.com/acrobat/web/e-sign-documents/request-e-signatures/send-in-bulk.html
- https://helpx.adobe.com/au/acrobat/using/file-format-options-pdf-export.html
- https://helpx.adobe.com/au/acrobat/using/print-production-tools-overview-acrobat.html
- https://helpx.adobe.com/au/acrobat/using/setting-security-policies-pdfs.html
- https://helpx.adobe.com/document-cloud/kb/european-union-trust-lists.html
- https://helpx.adobe.com/enterprise/kb/mpip-support-acrobat.html
- https://helpx.adobe.com/il_en/acrobat/using/converting-web-pages-pdf.html
- https://helpx.adobe.com/in/acrobat/desktop/create-documents/scan-documents-to-pdfs/scanned-pdf-settings.html
- https://helpx.adobe.com/in/acrobat/using/enhanced-security-setting-pdfs.html
- https://helpx.adobe.com/in/acrobat/using/pdf-x-pdf-a-pdf.html
- https://helpx.adobe.com/mena_en/acrobat/desktop/create-documents/create-pdfs/web-pages-to-pdfs.html
- https://helpx.adobe.com/my_en/acrobat/using/optimizing-pdfs-acrobat-pro.html
- https://helpx.adobe.com/ph_fil/acrobat/using/comments.html
- https://helpx.adobe.com/sign/web/advanced-users/send-in-bulk/overview.html
- https://helpx.adobe.com/th_en/acrobat/using/add-headers-footers-pdfs.html
- https://helpx.adobe.com/uk/acrobat/desktop/edit-documents/pdf-layers/import-layers.html
- https://helpx.adobe.com/uk/acrobat/using/analyzing-documents-preflight-tool-acrobat.html
- https://helpx.adobe.com/uk/acrobat/using/importing-exporting-comments.html
- https://helpx.adobe.com/uk/acrobat/using/participating-pdf-review.html

### Foxit PDF Editor

- https://www.foxit.com/pdf-editor
- https://www.foxit.com/pdf-editor/pricing
- https://www.foxit.com/pdf-editor/version-history.html?lang=en-us
- https://www.foxit.co.jp/wp-content/uploads/Foxit-PDF-Editor-Pro-DataSheet.pdf
- https://help.foxit.com/manuals/pdf-editor/mac/en-us/2024.3.0/Create_and_Convert_PDF_Files.html
- https://www.foxit.com/blog/use-action-wizard-to-automate-repetitive-tasks-in-pdfs
- https://www.foxit.com/resource-hub/tutorials/how-make-pdf-accessible-action-wizard-foxit-pdf-editor
- https://www.foxit.com/solution/accessibility
- https://foxit.com/blog/how-to-add-bates-numbering-in-pdfs
- https://www.foxit.com/blog/working-with-bates-numbering-in-pdf-documents
- https://www.foxit.com/company/press/11087.html
- https://www.foxit.com/company/press/7794.html
- https://www.foxit.com/resource-hub/data-sheets/foxit-pdf-editor
- https://www.foxit.com/resource-hub/user-manuals/foxit-pdf-editor-for-windows-user-manual-perpetual-release
- https://www.foxit.com/resource-hub/user-guide/foxit-pdf-editor-for-windows/create-a-table-of-contents-from-bookmarks
- https://resource-cms.foxit.com/white-paper/top-10-reasons-law-firms-select-foxit-pdf-solutions

### PDF-XChange Editor

- https://www.pdf-xchange.com/product/pdf-xchange-editor
- https://pdf-xchange.com/pdf-xchange-products-comparison-chart
- https://www.pdf-xchange.com/product/pdf-xchange-editor/features-group/89/features
- https://www.pdf-xchange.com/product/pdf-xchange-editor/features-group/87/features

### Nitro PDF Pro

- https://www.gonitro.com/product-details
- https://www.gonitro.com/release-hub/nitro-pdf-pro-26
- https://www.gonitro.com/resources/next-gen-nitro-pdf-pro-is-here-ai-tools-new-interface-better-compression?hsLang=en
- https://www.gonitro.com/nitro-pdf-pro
- https://www.gonitro.com/sem/nitro-pdf-plans

### PDFgear

- https://pdfgear.com/pdf-editor-reader
- https://www.pdfgear.com/pdfgear-for-windows
- https://www.pdfgear.com/windows-user-guide/introduction-pdfgear.htm
- https://www.pdfgear.com/windows-user-guide/batch-convert-pdf.htm
- https://www.pdfgear.com/mac-user-guide/batch-convert-pdfs.htm
- https://www.pdfgear.com/pdfgear-for-mac
- https://www.pdfgear.com/mac-user-guide/intro-to-pdfgear.htm
- https://apps.apple.com/app/id6469021132

### Xodo (Web, PDF Studio, Mobile, Sign)

- https://xodo.com/blog/xodo-plans-explained
- https://xodo.com/tools
- https://xodo.com/pdf-studio
- https://www.qoppa.com/pdfstudio
- https://support.apryse.com/support/solutions/articles/35000251312-editing-and-manipulating-pdfs
- https://xodo.com/blog/how-to-redact-a-legal-document
- https://xodo.com/blog/how-to-edit-a-pdf-without-adobe-acrobat
- https://apps.microsoft.com/detail/9ncrr9m6d4j9?hl=en-US

### Terze parti (conferme secondarie)

- https://www.dell.com/en-us/lp/hmc-foxit-editor-premier
- https://it.fdu.edu/?p=15197
- https://appmus.com/software/pdf-studio
- https://www.softpedia.com/progChangelog/PDF-Studio-Pro-Changelog-252564.html

### Riferimenti tecnici citati nella ricerca (candidati per le dipendenze, non fonti funzionali)

- https://github.com/tesseract-ocr/tesseract
- https://ocrmypdf.readthedocs.io
- https://verapdf.org
- https://www.libreoffice.org
- http://www.sane-project.org
- https://openprinting.github.io/cups
- https://pdfium.googlesource.com/pdfium
- https://mupdf.com
- https://poppler.freedesktop.org
- https://qpdf.sourceforge.io
- https://mozilla.github.io/pdf.js
- https://www.ghostscript.com
- https://hunspell.github.io
- https://www.littlecms.com
- https://bellard.org/quickjs
- https://github.com/zxing-cpp/zxing-cpp
- https://github.com/agl/jbig2enc
- https://www.openjpeg.org
- https://github.com/strukturag/libheif
- https://gstreamer.freedesktop.org
- https://proj.org
- https://www.openssl.org
- https://www.sqlite.org/fts5.html
- https://xapian.org

### Norme citate (nei file di ricerca senza indirizzo)

ISO 32000-1:2008 e ISO 32000-2:2020 (PDF 1.7 e 2.0); ISO/TS 32001–32005; ISO 19005 (PDF/A); ISO 14289-1 (PDF/UA); ISO 15930 (PDF/X); ISO 24517 (PDF/E); ISO 16684 (XMP); ISO 19444-1 (XFDF); ISO 21757-1 (ECMAScript per PDF); ISO 15076-1 (ICC); specifica XFA 3.3; ETSI EN 319 142-1 e -2 (PAdES); ETSI EN 319 122-1 (CAdES); ETSI EN 319 102-1 e TS 119 102-2 (validazione); ETSI TS 119 142-3; ETSI EN 319 412-5; ETSI EN 319 422; RFC 3161, 5280, 5652, 6960, 8118; Regolamento (UE) 910/2014 (eIDAS) e Decisione di esecuzione (UE) 2015/1506; Linee guida AgID sui documenti informatici.
