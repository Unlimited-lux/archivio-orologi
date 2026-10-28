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

### Problemi noti

Stato del 28/10 alle 17:25 UTC (18:25 italiane), dopo l'integrazione finale. Nel pomeriggio ci
sono stati altri quattro collaudi indipendenti: flussi reali, conversioni e OCR, aspetto e lingue,
robustezza. A questi si aggiungono il collaudo 6 sui requisiti non funzionali e una revisione
avversaria del codice del nucleo cambiato oggi. Ogni difetto confermato ha uno script di
riproduzione che esce con 0 quando il difetto non c'è più. Gli script sono stati rieseguiti sulla
versione integrata.
- I difetti dei collaudi precedenti risultano **corretti**. Le eccezioni sono elencate qui sotto.
- Tra i corretti oggi i più importanti sono quattro:
  - in Word, HTML e RTF la riga del totale sotto la tabella di una fattura spariva;
  - gli importi in CHF e con l'apostrofo restavano testo in Excel;
  - la stampa senza commenti toglieva anche i valori dei campi modulo;
  - il canale dell'istanza singola finiva nella /tmp condivisa.

Restano aperti:
| Gravità | Area | Problema |
|---|---|---|
| media | viewer | Ricerca di una parola molto frequente su 500 pagine (circa 18.000 risultati): il conteggio completo richiede circa 12 s contro i 3 s di NFR-07. L'interfaccia resta usabile durante la ricerca. |
| media | viewer | Stampa (VIS-43): non ci sono ancora «4 pagine per foglio», fronte/retro, scala di grigi e la scelta del «Contenuto». Per ora si usano le opzioni della stampante di sistema. |
| media | ocr | Sulle scansioni inclinate di 1-2° il testo invisibile spezza le parole in Evince, Okular e pdftotext (poppler). In Foliant la ricerca funziona. |
| media | ocr, viewer | Con due finestre (Ctrl+N), chiudendo la seconda il pannello «Parole incerte» dell'OCR produce errori. Con «Ripristina la sessione» attivo, la nuova finestra può riaprire la sessione invece di mostrare la Home. |
| media | annotations | L'importazione di commenti da XFDF gira nel thread dell'interfaccia, senza avanzamento: 1.000 commenti richiedono 5-11 s secondo la macchina, 3.000 circa 100 s, con la finestra bloccata. Il tempo cresce più che linearmente dentro PyMuPDF. I file comuni, con poche decine di commenti, si importano subito. |
| bassa | convert | Nell'esportazione in Excel le tabelle senza bordi richiedono la strategia «auto», che può dividere male le colonne: controllare il risultato. |
| bassa | viewer, tools, sign | In inglese alcuni numeri e date usano ancora il formato italiano: dimensioni dei file negli allegati, date in Proprietà, Allegati e Firma, migliaia senza separatore in «Informazioni sul documento». In Strumenti «Crea PDF» propone A4 anche con impostazioni USA e, a 1280×800, due campi delle Operazioni in blocco escono dal bordo destro. In Firma › Gestisci ID l'elenco vuoto non ha spiegazione e alcuni comandi restano attivi senza selezione. |
| bassa | ocr | Alcuni pulsanti tagliati nel pannello «Parole incerte». Una foto senza testo può ricevere la proposta di OCR. L'analisi delle pagine all'apertura gira nel thread dell'interfaccia. |
| bassa | nucleo | Su documenti con centinaia di commenti ogni modifica costa circa 0,3 s per la copia di annullamento. Il salvataggio incrementale dei PDF firmati non alza la versione PDF. Un secondo avvio mentre Foliant è occupato da più di 6 s può aprire il file due volte. |
| — | specifica | Funzioni P1/P2 non completate e richieste al nucleo ancora aperte: elencate per ID in `foliant/modules/*/STATO.md` e in `foliant/modules/viewer/CORE_REQUESTS.md`. La firma con smart card (SIG-23) è pronta nel commit 863560a ma rinviata, perché aggiunge una dipendenza. |
| — | motore | «Cifra tutto tranne i metadati» (SEC-06) non è disponibile: PyMuPDF/MuPDF cifrano sempre i metadati. |

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
- 28/10 12:55–14:20 — Dopo la consegna: nuove API del nucleo (avvisi in pagina, controlli prima
  del salvataggio, notifiche non bloccanti, attività in background, blocco del documento,
  permessi e sblocco, salvataggio incrementale dei PDF firmati) adottate da tutti i 12 moduli.
  Collaudo indipendente 3 (sei aree) e 4 (sette gruppi di moduli). Corretti tra gli altri: la
  redazione su PDF firmati che lasciava il testo nel file, i comandi che restavano disattivati dopo
  lo sblocco con la password, le notifiche impilate schiacciate, il pulsante «»» invisibile nel
  tema scuro, i nomi dei tasti in inglese nel menu e il doppio avviso per i file riparati.
  Suite: 4.255 test superati.
- 28/10 14:20–17:25 — Lavoro dopo la consegna, seconda parte.
  - Moduli: completate quasi tutte le P0 e P1 e gran parte delle P2.
  - Nucleo: corretti i 14 difetti del collaudo 6 (contrasti e fuoco visibile, pulsanti
    raggiungibili con Tab, messaggi di salvataggio chiari, PDF 1.4 mantenuto, file eliminato da
    ricreare, memoria restituita, Ctrl+N e istanza singola, Home con documenti fissati e filtro,
    formato carta A4/Letter secondo le impostazioni locali); date e numeri secondo la lingua;
    privacy senza analisi del layout ma con le tabelle funzionanti.
  - Revisione avversaria del nucleo: 14 difetti confermati, 12 corretti con test.
  - Quattro collaudi indipendenti: flussi reali dell'azienda, conversioni e OCR, aspetto e
    lingue, robustezza e concorrenza. I difetti confermati sono stati assegnati ai moduli e
    corretti con test.
  - Integrazione finale dei 12 moduli senza conflitti. Prima suite completa: 4.865 test
    superati e 3 falliti. La causa era l'identificativo casuale dei timbri, che rendeva le
    elaborazioni non ripetibili e nascondeva le filigrane all'appiattimento; è stata
    corretta. Dopo le ultime correzioni dei moduli (taglio alle 17:12 UTC): 4.894 test
    superati, 0 falliti. Alle 17:25 sono state integrate anche le ultime tre correzioni di Converti
    (decimali in inglese, appiattimento che conserva le redazioni non applicate, elenco vuoto di
    «Converti più file»): test di Converti e della finestra verdi.
  - Script dei difetti rieseguiti sulla versione consegnata: 136 su 142 escono con 0. Dei 6
    rimanenti, 3 controllano funzioni interne mentre nell'interfaccia il comportamento è corretto,
    2 sono noti (uno fallisce solo sotto carico, l'altro è un falso positivo) e 1 è la ricerca
    su 500 pagine, tra i problemi aperti.
