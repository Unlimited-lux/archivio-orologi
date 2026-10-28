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

Stato del 28/10 alle 14:20 (ora italiana 15:20). Dopo la consegna ci sono stati altri due giri di
collaudo indipendente, oltre ai precedenti. I 99 script di riproduzione raccolti finora (un file
`bug_NN.py` per ogni difetto confermato, che esce con 0 quando il difetto non c'è più) sono stati
rieseguiti sulla versione integrata:
- tutti i problemi elencati in precedenza, cioè il primo collaudo, i 28 confermati alle 09:10 e
  quelli visivi, sono **corretti** e verificati con il loro script;
- il collaudo delle 13:10 ha confermato 9 nuovi difetti, tutti corretti con un test. Il più grave
  era una fuga di dati: una redazione su un PDF firmato veniva salvata in modo incrementale e il
  testo restava nella revisione firmata. Ora il nucleo riscrive il file per intero e il modulo
  Firma avvisa prima (NFR-09).

Restano aperti:
| Gravità | Area | Problema |
|---|---|---|
| media | annotations | Chiudendo l'ultima scheda in Commenta la mini barra viene distrutta e i cambi di scheda successivi sollevano un'eccezione; su un PDF firmato i commenti chiedono la conferma SIG-21 perché il modulo non li dichiara ancora `kind="annotation"`. In correzione. |
| bassa | text_edit | La riga riscritta usa una nuova copia del font (in sottoinsieme) invece della risorsa originale: il file cresce di pochi KB. |
| bassa | nucleo | Su documenti molto grandi (5.000 campi in una pagina) ogni modifica costa circa 0,2 s per la copia di annullamento; sui documenti comuni pochi millisecondi. |
| — | specifica | Funzioni P1/P2 non completate: elencate per ID in `foliant/modules/*/STATO.md` (es. icone dei pulsanti per stato FRM-18). |
| — | motore | «Cifra tutto tranne i metadati» (SEC-06) non disponibile: PyMuPDF/MuPDF cifrano sempre i metadati. |

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
