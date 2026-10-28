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
| bassa | Visualizzazione | Zoom massimo 800% (la specifica chiede 6400%: serve il disegno della pagina a tasselli). |
| bassa | OCR | Durante l'OCR il documento non è consultabile (finestra di avanzamento modale, con annullamento). |
| — | Specifica | Funzioni P1/P2 non completate: elencate per ID in `foliant/modules/*/STATO.md`. |

Dal collaudo finale delle 09:10 (script di riproduzione in uno dei collaudi; NON ancora verificati in modo
indipendente, alcuni potrebbero essere falsi positivi). I 3 casi di perdita di dati trovati (Salva/Chiudi
con l'editor di testo o un campo modulo ancora aperto) sono già corretti nella consegna.
| Gravità | Riferimento | Descrizione |
|---|---|---|
| alta | TXT-02/TXT-03 (P0) — modulo: text_edit. | Correggere il prezzo «€ 9.500,00» → «€ 9.800,00» in una fattura esportata da LibreOffice (tabella con la colonna «Prezzo» allineata a destra, font Carlito incorporato com |
| media | TXT-02 (P0, punto 6 del flusso e 3° criterio) / TXT-03 — modulo: text_edit. | In una fattura esportata da LibreOffice, correggendo l'importo nella frase sotto la tabella («Totale documento: € 9.950,00 — …», paragrafo giustificato) cambiano anche ri |
| bassa | CNV-19 (P0, passo 3 «Formato pagina: come l'immagine») — modulo: convert. | Con «Formato pagina: Come l'immagine» il campo «Margini» viene disattivato (quindi non modificabile), ma il suo valore predefinito (10 mm) viene applicato lo stesso: ogni |
| media | PAG-33 (Numeri di pagina, flusso punto 4) — modulo: pages. | Rinumerare un documento che ha già i numeri aggiunti da Foliant ne aggiunge una seconda serie: lo strumento non propone «Modifica»/«Rimuovi» né sostituisce i numeri esist |
| media | CNV-29 (P0, Comprimi › «Applica al documento aperto») — modulo: convert. | Applicando la compressione al documento aperto il messaggio annuncia una riduzione («Prima: 421,1 KB — Dopo: 381,1 KB — Riduzione: 9%») ma il file salvato dopo non è più  |
| media | BAT-12 (P0, «usa le regole di PAG-36») / PAG-36 — modulo: tools. | Le filigrane di Strumenti › Operazioni in blocco e quelle di Pagine › Timbri › Filigrana non si riconoscono tra loro, anche se sono entrambe «aggiunte con Foliant»: |
| bassa | TXT-06 / TXT-10 (P0, «font aggiunto … come sottoinsieme incorporato») — modulo: text_edit. | Il testo aggiunto (o riscritto) incorpora il programma COMPLETO del font invece di un sottoinsieme: una scheda prodotto di 1 KB a cui si aggiunge la riga «Rolex Submarine |
| media | OBJ-38/OBJ-39 (pannello «Collegamenti», verifica dei collegamenti) + NFR-13/NFR-15. | Aprendo un PDF troncato che il motore ricostruisce (banner «Il file era danneggiato ed è stato ricostruito.») il pannello laterale «Collegamenti» solleva ValueError: bad  |
| media | ANN-01/ANN-05 (visualizzazione commenti) + NFR-05/NFR-07 (reattività). | Su una pagina con 2000 annotazioni (rettangoli con nota, file da revisione intensiva) ogni ridisegno della vista dopo l'apertura e dopo OGNI modifica blocca l'interfaccia |
| media | SEC-26 (analisi dei contenuti attivi all'apertura) + NFR-02/NFR-05. | All'apertura di un PDF di UNA pagina con 2000 annotazioni la finestra resta bloccata ~4 s prima che la scheda compaia: il modulo Proteggi esegue sanitize.scan() in modo s |
| media | SEC-26 / NFR-17 (avviso dei contenuti attivi) + SEC-02 (banner «Documento con | restrizioni») + NFR-13 (PDF malformati). Basta un albero delle pagine con un riferimento ciclico (/Kids che contiene il nodo /Pages stesso, (script con errori: da riverificare) |
| media | VIS-47/VIS-48 (Salva, Salva con nome) + NFR-13/NFR-15 (PDF malformati, messaggi). | Con un PDF il cui albero delle pagine contiene un ciclo (si apre: 3 pagine annunciate, la 3ª illeggibile), «Salva con nome» SCRIVE il file ma mostra comunque «Salvataggio (script con errori: da riverificare) |
| media | NFR-14 (isolamento dei guasti, criterio 1) / VIS-01 (vista pagine). | Una pagina che il motore non riesce a caricare (qui: 3ª pagina di un albero delle pagine con ciclo) viene mostrata come una normale pagina A4 BIANCA, indistinguibile da u (script con errori: da riverificare) |
| bassa | VIS-10 (pannello Miniature) + NFR-14 (isolamento dei guasti). | Con un documento che ha una pagina illeggibile (albero delle pagine con ciclo, 3ª pagina) il delegato delle miniature solleva un'eccezione NON gestita (FzErrorFormat «cyc (script con errori: da riverificare) |
| bassa | TXT-01 (strumento «Modifica testo») + NFR-14 (isolamento dei guasti). | Entrando in «Modifica» mentre la pagina corrente è illeggibile (3ª pagina di un albero delle pagine con ciclo) l'attivazione dello strumento solleva un'eccezione NON gest (script con errori: da riverificare) |
| media | NFR-12 (nessuna perdita silenziosa di modifiche, criterio 1) / VIS-48. | Chiudendo la finestra con più documenti modificati Foliant non mostra un'unica finestra con l'elenco dei documenti modificati: chiede documento per documento e chiude cia |
| media | NFR-12 (criterio 3) / VIS-49 (file modificato da un'altra applicazione). | Documento con modifiche non salvate in Foliant; un'altra applicazione riscrive il file su disco. Foliant mostra (nella vista) la barra «Il file «ext.pdf» è stato modifica |
| alta | UX-02/UX-20 (barra delle modalità, layout a finestra ridotta) — nucleo. | Passi: finestra 1280x800 (anche 1366x768: sparisce il menu ☰), aprire un PDF qualsiasi. Atteso (specifica, sezione layout: «Da 1024 px di larghezza in su sono visibili tu |
| media | UX-05/UX-20 (barra contestuale, comandi in eccesso) — nucleo. | Passi: finestra 1920x1080 (Full HD), aprire un PDF, modalità «Leggi». Atteso: tutti i comandi della barra contestuale visibili oppure raggiungibili da un pulsante di |
| bassa | NFR-23 (contrasto) / tema chiaro e scuro — nucleo (ui/theme.py). | Passi: aprire un PDF, Proteggi → «Password e permessi» (dialogo «Proteggi con password»); la password non è ancora scritta, quindi «Applica» è disattivato. Stesso effetto |
| bassa | SEC-04/SEC-05 (dialogo «Proteggi con password») — security. | Passi: aprire un PDF, modalità Proteggi → «Password e permessi»; il dialogo si apre alla dimensione iniziale (tema chiaro o scuro, 1280x800 o 1920x1080). |
| media | UX (lingua italiana dell'interfaccia) / NFR localizzazione — nucleo. | Passi: interfaccia in italiano. (a) Qualsiasi conferma dei moduli passa da ctx.confirm() (54 chiamate) o QMessageBox.question (20): es. Pagine → Elimina pagine, Segnalibr |
| bassa | UX-18 (Ctrl+? «Scorciatoie da tastiera» mostra tutte le scorciatoie) — nucleo. | Passi: aprire un PDF, restare in «Leggi», premere Ctrl+? (o ☰ → Scorciatoie da tastiera). Atteso (README/UX-18: «Ctrl+? mostra tutte le scorciatoie»): l'elenco contiene a |
| bassa | VIS-20 (pannello Segnalibri) — viewer. | Passi: aprire un PDF senza segnalibri (finestra 1280x800 o 1920x1080, tema chiaro o scuro), pannello laterale → icona Segnalibri. |
| bassa | SIG-15 (pannello Firme) — sign. | Passi: aprire un PDF (anche senza firme), pannello laterale → icona Firme (finestra 1280x800 o 1920x1080: la larghezza del pannello è la stessa), tema chiaro o scuro. |
| bassa | VIS-33 (Ricerca avanzata) — viewer. | Passi: aprire un PDF, Leggi → «Ricerca avanzata…» (Ctrl+Maiusc+F); tema chiaro o scuro. Atteso: le caselle «Opzioni» (Maiuscole/minuscole, Parola intera, Ignora accenti)  |
| bassa | OCR-03 (gestione lingue di riconoscimento) — ocr. | Passi: aprire un PDF, modalità OCR → «Lingue» (dialogo «Lingue di riconoscimento») alla dimensione iniziale 620x520, tema chiaro o scuro. |

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
