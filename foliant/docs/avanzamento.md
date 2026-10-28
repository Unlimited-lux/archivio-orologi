# Avanzamento — Foliant (editor PDF desktop per Linux)

> Nome di lavoro: **Foliant**. App originale (codice, interfaccia, nome, icone) con
> obiettivo di parità funzionale con UPDF, costruita con metodo clean-room su fonti
> pubbliche, standard aperto ISO 32000 e librerie open source.
> Scadenza richiesta: **28/10/2026 ore 10:00** (ora italiana). Approvazioni delegate
> all'orchestratore dal committente.

## Metodo e paletti
- **Clean-room**: specifica da fonti pubbliche (documentazione, guide, recensioni) e
  dallo standard PDF, **mai** da decompilazione di binari. Scartato il percorso
  REA/Ghidra/installer UPDF (non clean-room, contrario alla licenza del prodotto).
- **Niente elusione** di licenze/paywall altrui: le funzioni che in UPDF sono a
  pagamento le implementiamo noi, native, nella nostra app.
- **Grafica originale**: convenzioni comuni degli editor PDF, ma nome, logo, palette e
  stile propri. Icone: set open source Lucide (ISC).
- Nel repo: `.claude/agents/`, `docs/` e il codice. Il materiale di analisi resta fuori.

## Decisioni di architettura (architetto = orchestratore)
| Area | Scelta | Licenza |
|---|---|---|
| Linguaggio | Python ≥ 3.11 | — |
| Interfaccia | Qt 6 / PySide6 | LGPL-3.0 |
| Motore PDF | PyMuPDF (MuPDF) | AGPL-3.0 o commerciale Artifex |
| OCR | Tesseract 5 (ita, eng) + OpenCV per pre-elaborazione | Apache-2.0 |
| Firma digitale | pyHanko | MIT |
| Conversione Office | pdf2docx, openpyxl, python-pptx; LibreOffice per Office→PDF | MIT / MIT / MIT / MPL |
| IA (facoltativa) | fornitore configurato dall'utente; chiave nel portachiavi (keyring) | MIT |

Motivazione: PyMuPDF copre in un'unica libreria rendering, testo, annotazioni, moduli,
pagine, cifratura, redazione e OCR. È il solo stack che rende realistica la copertura
ampia richiesta nei tempi. Conseguenza: Foliant è **AGPL-3.0**. Per l'uso interno
dell'azienda va bene; per vendita a codice chiuso serve la licenza commerciale Artifex.
Il motore resta isolato dietro `foliant/core` e `ENGINE_LOCK`.

Architettura a **nucleo + moduli** (vedi `README.md`):
- `core/document.py`: modifiche annullabili con ripristino automatico in caso di errore,
  salvataggio atomico, mantenimento della protezione originale.
- `ui/document_view.py`: impaginazione (continua, singola, doppia, copertina), zoom,
  rendering progressivo non bloccante, coordinate esatte su pagine ruotate/ritagliate.
- `ui/context.py`: API per i moduli (modalità, azioni, pannelli, strumenti, attività).
- `modules/<area>/`: un modulo per area, scoperto automaticamente. Ogni sessione di
  sviluppo lavora solo nella propria cartella, quindi niente conflitti.

## Stato

### Fase 0 — Setup ✅
Struttura, subagent (`.claude/agents/`), `.gitignore`.

### Fase 1 — Specifica ✅ (approvata dall'orchestratore su delega del committente)
- Ricerca da fonti pubbliche: inventario di parità UPDF (189 funzioni), concorrenti, standard.
- `docs/funzionalita.md`: catalogo di 312 funzioni (188 in parità, 124 aggiunte).
- `docs/specifica.md` (v2, dopo la revisione): 425 funzioni in 12 aree (146 P0, 223 P1, 52 P2,
  4 fuori ambito), più 34 requisiti non funzionali, 22 di interfaccia e la matrice di copertura
  del catalogo (188 righe di parità tutte coperte).
- Revisione indipendente: «approvata con correzioni». Copertura parità: 158 righe su 177
  coperte del tutto, 19 in parte, nessuna assente. Correzioni (originalità di VIS-40 ed
  etichette, ambito, priorità, regole di precedenza, scorciatoie) applicate nella v2.

### Fase 2 — Implementazione ✅ (consegna del 28/10/2026)
- ✅ Nucleo: documento con annulla/ripeti e ripristino in caso di errore, salvataggio atomico che
  mantiene la protezione e non rinumera gli oggetti del documento aperto, vista con coordinate
  esatte su pagine ruotate/ritagliate (`core/placement.py`), finestra, tema chiaro/scuro, Home,
  Preferenze, scorciatoie UX-18 limitate alla modalità, permessi del PDF rispettati (copia),
  file in sola lettura con «Salva con nome».
- ✅ 12 moduli integrati nel branch principale: Leggi, Commenta, Modifica (testo e oggetti),
  Pagine, Moduli, OCR, Converti, Proteggi, Firma, Strumenti (confronto e operazioni in blocco),
  Assistente IA. Tutti caricati, 11 modalità, nessuna scorciatoia duplicata.
- ✅ Collaudo end-to-end sull'app completa (15 collaudatori + verificatori indipendenti):
  63 bug riprodotti con script; **56 corretti e verificati** sulla versione finale, 2 chiusi per
  scelta della specifica (Ctrl+D tolto), 5 noti (sotto).
- ✅ Flussi chiave verificati sull'app completa: PDF scannerizzato → OCR modificabile (tabelle e
  stile del testo) → modifica → salvataggio; scansione → Word (100% delle parole sul campione);
  contratto → commenti → modulo → protezione con password → firma digitale valida e file ancora
  cifrato; unione, compressione, esportazione; annulla/ripeti tra modalità.
- ✅ Installazione: `packaging/install.sh` (dipendenze di sistema, ambiente Python, voce nel menu);
  pacchetto verificato in un ambiente pulito.

### Problemi noti (da chiudere dopo la consegna)

Dal primo collaudo (verificati):
| Gravità | Area | Problema |
|---|---|---|
| bassa | Moduli | Il campo firma vuoto mostra il segnaposto del motore a schermo e in stampa da Foliant (nel file è vuoto). |
| bassa | Modifica testo | La riga riscritta usa una nuova copia del font invece della risorsa originale (file un po' più grande). |
| bassa | OCR | Durante l'OCR il documento non è consultabile (finestra di avanzamento modale, con annullamento). |
| — | Specifica | Funzioni P1/P2 non completate: elencate per ID in `foliant/modules/*/STATO.md`. |

Dal collaudo finale delle 09:10 (verificati da un verificatore indipendente: 28 confermati, 2 scartati).
Già corretti nella consegna: perdita della modifica in corso con Salva/Chiudi (testo e campi modulo),
barra delle modalità e menu ☰ nascosti a 1024–1366 px, pulsante dei comandi in eccesso invisibile.
| Gravità | Modulo | Problema |
|---|---|---|
| alta | text_edit | In una fattura esportata da LibreOffice (Carlito in sottoinsieme, colonna Prezzo allineata a destra), sostituire «€ 9.500,00» con «€ 9.800,00» spezza il prezzo su due righe. Le cifre finiscono sopra i |
| media | nucleo | Testi di Qt in inglese nell'interfaccia italiana. |
| media | text_edit | Correggere l'importo nel paragrafo giustificato sotto la tabella di una fattura LibreOffice riscrive e sposta anche righe non modificate della tabella. Il paragrafo perde la giustificazione. |
| media | pages | Rinumerare un documento già numerato da Foliant aggiunge una seconda serie di numeri sovrapposta. Lo strumento non propone Modifica/Rimuovi e non sostituisce i numeri esistenti. |
| media | convert | Comprimi › livello «Senza perdita» › «Applica al documento aperto» annuncia una riduzione, ma il file salvato ha la stessa dimensione dell'originale. |
| media | tools | Le filigrane di Operazioni in blocco (Strumenti) e quelle di Pagine › Filigrana usano marcature diverse e non si riconoscono tra loro. |
| media | nucleo | Chiusura della finestra con più documenti modificati: viene fatta una domanda per ogni documento invece di un'unica finestra, e annullare l'uscita non annulla davvero (NFR-12/1; VIS-50, casi limite: « |
| media | nucleo | File cambiato su disco da un altro programma mentre in Foliant ci sono modifiche non salvate: «Salva» lo sovrascrive senza chiedere e le modifiche esterne vanno perse (NFR-12/3, VIS-49). Passi: aprire |
| media | security | Un PDF con un ciclo nell'albero delle pagine (/Pages /Kids [p1 p2 <Pages stesso>] /Count 3) fa sparire tutti gli avvisi di sicurezza all'apertura (NFR-17, SEC-26, SEC-02, NFR-13). Passi: PDF di 2 pagi |
| media | security | «Salva con nome» su un PDF con ciclo nell'albero delle pagine scrive il file ma segnala un errore falso e tecnico (NFR-13/1, NFR-15, VIS-48). Passi: PDF di 2 pagine con /Pages /Kids [p1 p2 <Pages stes |
| media | nucleo | Una pagina che il motore non riesce a caricare appare come una normale pagina bianca, senza il riquadro richiesto da NFR-14/1 («quella danneggiata mostra, nelle sue dimensioni, il riquadro «Impossibil |
| media | objects | Il pannello «Collegamenti» non regge un PDF troncato e riparato: mostra i dati del documento precedente e un messaggio tecnico (NFR-13, NFR-15, OBJ-38/39). Passi: aprire links.pdf (1 pagina con 2 coll |
| media | annotations | Su una pagina con 2000 annotazioni ogni ridisegno dopo l'apertura e dopo ogni modifica, annulla o ripeti blocca l'interfaccia per circa 4 s (NFR-05: riscontro entro 100 ms, nessuna operazione lunga bl |
| media | security | Aprire un PDF con 2000 annotazioni blocca la finestra per circa 3,8 s, quasi tutti spesi nell'analisi sincrona dei contenuti attivi (NFR-02: prima pagina subito, il resto in background; NFR-05: interf |
| bassa | nucleo | Etichetta illeggibile sui pulsanti principali (default/primary) quando sono disattivati. |
| bassa | security | Dialogo «Proteggi con password»: l'etichetta di un permesso è tagliata. |
| bassa | nucleo | La finestra «Scorciatoie da tastiera» (Ctrl+?) non elenca le scorciatoie delle altre modalità. |
| bassa | viewer | Pannello Segnalibri di un documento senza segnalibri: impaginazione sbagliata e icone schiacciate. |
| bassa | sign | Pannello Firme: le etichette di due pulsanti sono tagliate. |
| bassa | viewer | Ricerca avanzata: le caselle delle righe «Opzioni» e «Includi» sono attaccate tra loro. |
| bassa | ocr | Dialogo «Lingue di riconoscimento»: la colonna «Stato» è troncata in tutte le righe. |
| bassa | convert | Crea PDF da immagini con Formato pagina «Come l'immagine»: il campo Margini viene disattivato ma il suo valore (10 mm predefiniti) viene applicato lo stesso. |
| bassa | text_edit | Il font nuovo usato per il testo aggiunto o riscritto viene incorporato intero invece che come sottoinsieme, e il file cresce di centinaia di KB. |
| bassa | viewer | Pannello Miniature con una pagina illeggibile: il delegato solleva un'eccezione non gestita a ogni disegno e la miniatura non mostra alcun segno d'errore (NFR-14, VIS-07/3). Passi: PDF di 2 pagine con |
| bassa | text_edit | Entrando in «Modifica» mentre la pagina corrente è illeggibile, l'attivazione dello strumento solleva un'eccezione non gestita (NFR-14: un errore in una pagina non deve coinvolgere il resto). Passi: P |

## Log
- 28/10 00:57 — Fase 0: struttura, subagent, `.gitignore`.
- 28/10 01:09 — Fase 1 avviata (workflow), poi ripensata a potenza massima.
- 28/10 01:51 — Nuova scadenza ore 10:00. Specifica scritta da 14 agenti in parallelo.
- 28/10 02:10 — Bug trovato dai test e corretto: salvando un PDF protetto la cifratura
  andava persa.
- 28/10 02:30 — Avviate 12 sessioni di sviluppo, una per modulo.
- 28/10 03:00 — Revisione della specifica e allineamento delle scorciatoie (UX-18).
- 28/10 03:16 — Revisione del nucleo: 24 correzioni applicate e verificate.
- 28/10 03:20 — Prima integrazione: 12 moduli uniti senza conflitti.
- 28/10 03:35 — Rete di sicurezza nei test (nessun blocco per finestre modali, timeout).
- 28/10 04:05 — Scorciatoie limitate alla modalità; gestori dei collegamenti con priorità.
- 28/10 04:10 — Avviato il collaudo end-to-end (3 workflow, 12 aree).
- 28/10 04:15 — `placement.py` nel nucleo (4.974 casi verificati con la matrice completa).
- 28/10 04:25 — Controllo grafico della build integrata in tutte le modalità.
- 28/10 04:30 — Limite di spesa raggiunto: collaudo e 9 sessioni fermi.
- 28/10 07:45 — Ripartenza: sessioni riavviate, calendario finale (push entro 08:20, correzioni
  entro 08:35, integrazione finale entro 09:00 UTC).
- 28/10 07:50 — Nucleo: le azioni disattivate da un modulo (permessi) non vengono più
  riattivate al cambio documento. 42 bug confermati inviati alle sessioni.
- 28/10 08:00–08:30 — Correzioni nel nucleo: salvataggio senza rinumerazione degli oggetti,
  permesso di copia, sola lettura, gestore di sicurezza nominato, tema più veloce, indicatori
  leggibili nel tema scuro. Secondo collaudo (viewer, strumenti, IA, scansioni, percorsi utente).
- 28/10 08:35 — Integrazione finale dei 12 moduli nel branch principale.
- 28/10 08:52 — Suite completa sul branch unito: 2.598 test superati, 0 falliti (13 suite);
  12 moduli caricati, 11 modalità, nessuna scorciatoia duplicata. Consegna.
- 28/10 09:00–09:30 — Proroga alle 11:00 italiane: chiusi altri bug (redazione senza spostamento
  del testo, proposta OCR in Modifica, pannello destro, avviso sola lettura) e aggiunte funzioni P1
  (informazioni sul documento, ricerca nei commenti, scansione → Word con tabelle native, portfolio,
  controlli fatture con l'IA, ottimizzazione immagini, etichette di pagina, pagine vuote/duplicate).
- 28/10 09:39 — Suite completa sul branch finale: 2.654 test superati, 0 falliti.
- 28/10 09:48 — Collaudo finale: corretti i 3 casi di perdita di dati con editor aperto.
- 28/10 09:53 — Barra delle modalità a icone sulle finestre strette, menu dei comandi in eccesso visibile.
