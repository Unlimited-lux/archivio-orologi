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
| Conversione Office | pdf2docx, python-docx, openpyxl, python-pptx; LibreOffice per Office→PDF | MIT / MIT / MIT / MIT / MPL |
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

### Riservatezza dei documenti (decisione del committente, 28/10 sera)

Foliant non conserva su disco nomi, percorsi o cartelle dei file aperti o modificati, né cronologie,
registri o stato per documento. Sono stati tolti: file recenti e documenti fissati, ripristino
della sessione, ultima cartella ricordata tra un avvio e l'altro, pagina/zoom/guide ricordati per
documento, cronologia dell'IA e registro degli invii, indice di ricerca e cache del testo nel
profilo, giornale e cronologia delle operazioni in blocco, lotto OCR da riprendere, «documenti
attendibili», cronologia delle ricerche, nomi di file nei timbri e nei modelli. I dialoghi di file
sono quelli di Qt e Foliant toglie ciò che i suoi dialoghi scrivono nella cronologia condivisa di
Qt; nessuna voce in recently-used.xbel. Le copie di recupero dopo un crash restano, senza nome né
percorso, e vengono cancellate alla chiusura normale. Al primo avvio la nuova versione cancella i
dati di questo tipo lasciati dalle versioni precedenti, solo quelli di Foliant. Lo verifica il
test a scatola chiusa `tests/test_no_file_traces.py` sull'app completa con tutti i moduli.

### Windows (29/10)

Foliant funziona anche su Windows 10 (1809 o successivo) e 11 a 64 bit, con lo stesso codice: le
differenze tra sistemi stanno in `foliant/foliant/core/platform.py` (cartelle, processi, strumenti
esterni, salvataggio con file aperti da altri programmi, posta, font) e `core/winscan.py`
(scanner WIA). Il lavoro è stato fatto da due sessioni dedicate (codice e installatore).
- Suite completa su GitHub Actions (`.github/workflows/foliant-windows.yml`): Windows con Python
  3.12 e 3.13 e Ubuntu, verdi in due esecuzioni consecutive sullo stesso commit (f3fdbc7).
- Installatore `Foliant-Setup-<versione>.exe` (Inno Setup) e versione portatile `.zip`, costruiti
  da `.github/workflows/foliant-windows-build.yml`, con Tesseract (ita, eng, osd), Ghostscript e
  qpdf inclusi e le loro licenze. Una prova automatica su Windows pulito installa, apre, modifica
  e salva un PDF, fa OCR → DOCX, PDF/A e compressione, controlla che non restino tracce dei file
  (AppData, registro, Recenti di Windows) e disinstalla.
- Guida per l'utente: `foliant/docs/windows.md`. Il programma non è firmato digitalmente: al
  primo avvio Windows SmartScreen chiede conferma.

### Menu in stile Excel (29/10 sera)

Su richiesta del committente («il menu a tre lineette è troppo caotico, userei la disposizione tipo
Excel») il menu ☰ (143 voci in 21 sezioni, 65 raggiungibili solo da lì) è stato sostituito:
- **Scheda File** (prima scheda, F10 o Alt+F, Esc per tornare): pagina a tutto schermo con colonna a
  sinistra — Nuovo, Apri, Informazioni (riepilogo del documento, proprietà, protezione, controllo,
  versioni), Salva, Salva con nome, Salva una copia, Stampa, Condividi, Esporta, Gestisci file,
  Chiudi, Opzioni (tema, lingua, preferenze, apertura dei documenti, sicurezza, Assistente IA),
  Guida, Esci. Ogni voce ha descrizione e scorciatoia; le scelte dai menu partono a scheda chiusa.
- **Gruppi con il nome sotto i pulsanti** in tutte le schede, come la barra multifunzione; quando la
  finestra si stringe i pulsanti passano alla sola icona da destra, senza nascondere comandi.
- **Nuova scheda Visualizza** (layout pagina, rotazione, miniature, righelli, griglia, temi di
  lettura, lettura ad alta voce, presentazione, schede); Leggi tiene Strumenti, Zoom, Vai, Trova.
- Le altre voci sono tornate nella scheda della loro funzione (es. Cifratura ▾ in Proteggi, Crea ID
  autofirmato in Firma, Rimuovi livello OCR in OCR); niente doppioni creati solo per il menu.
- Barra di accesso rapido: Apri, Salva, Stampa, Annulla, Ripeti; «Cerca un comando» (Ctrl+K) al
  posto del ☰. Le scorciatoie non cambiano. Una sola «Riapri scheda chiusa» (ripristina pagina e zoom).
- API per i moduli: `ctx.add_file_action`, `add_quick_access_action`, `set_command_search`,
  `set_reopen_handler`, `Mode.add_group`, `add_action(group=, compact=)`, `add_menu_button`
  (README). `add_menu_action` è superata. Proposta con prototipo e mappa delle voci pubblicata come
  pagina privata per il committente.

### Un file aperto una volta sola (29/10)

Su richiesta del committente («non posso modificare o visualizzare lo stesso file più volte nello
stesso momento») lo stesso file non si apre mai due volte: né in due schede, né in due finestre
(Ctrl+N), né in due processi Foliant. Un file già aperto mostra la sua scheda (anche nell'altra
finestra o nell'altro processo, se il sistema lascia passare la finestra in primo piano); un
secondo avvio che serviva solo a file già aperti lo dice ed esce. Il blocco segue il documento
(Salva con nome, rinomina e spostamento dalla scheda File, ricarica) e si libera alla chiusura,
all'uscita e dopo un crash. Su disco solo blocchi con il nome cifrato con chiave (HMAC), mai nomi o
percorsi. Dettagli e API nel README del pacchetto («Nucleo: un file aperto una volta sola»).

### Problemi noti

Stato del 28/10 sera, dopo la riservatezza e le correzioni dei moduli unite: suite completa verde,
prova a scatola chiusa senza tracce. Rispetto alla consegna delle 17:25 UTC sono corretti anche:
ricerca su 500 pagine (circa 2,3 s), stampa con più pagine per foglio, fronte/retro e scala di
grigi, testo OCR leggibile da poppler sulle scansioni inclinate, errori con due finestre (Ctrl+N),
numeri e date in inglese, importazione di 1.000 commenti in circa 2 s.

Restano aperti:
| Gravità | Area | Problema |
|---|---|---|
| bassa | nucleo | Su documenti con centinaia di commenti ogni modifica costa circa 0,3 s per la copia di annullamento. Il salvataggio incrementale dei PDF firmati non alza la versione PDF. |
| bassa | viewer | Stampa (VIS-43): mancano ancora la scala in percentuale e «riduci solo se necessario», «Documento e timbri», la carta scelta in base al formato della pagina e la stampa PostScript; l'anteprima usa la carta della stampante predefinita, non quella scelta poi nel dialogo del sistema. |
| — | specifica | Funzioni P1/P2 non completate e richieste al nucleo ancora aperte: elencate per ID in `foliant/modules/*/STATO.md` e in `foliant/modules/viewer/CORE_REQUESTS.md`. |

Non prioritari per il committente (non verranno trattati salvo nuova richiesta): importazione di
migliaia di commenti sulla stessa pagina, tabelle senza bordi nell'esportazione in Excel, audio e
video nei PDF (VIS-37), firma con smart card (SIG-23, implementazione conservata nel commit 863560a).

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
- 28/10 18:15–21:30 — Su richiesta del committente: nessuna memoria dei file (recenti, sessione,
  cronologie, ultime cartelle, stato per documento). Censimento con 6 agenti (5 aree più una prova
  a scatola chiusa sull'app vera), correzione per area in copie isolate, verifiche indipendenti e
  un giro di rifinitura; le sessioni dei moduli hanno corretto in parallelo i casi segnalati
  (objects, forms, security, convert, ocr). Unite anche le correzioni dei moduli arrivate dopo la
  consegna.
- 29/10 06:30–18:30 — Porting a Windows con due sessioni (codice e installatore) e controllo
  orario automatico delle sessioni. Corretti tra l'altro i controlli dei processi che su Windows
  li avrebbero terminati, la ricerca degli strumenti esterni, i font, la posta, lo scanner, la
  sintesi vocale e due difetti già presenti su Linux (pannello IA troppo largo con DejaVu, misura
  della memoria nel test da 5.000 pagine); resi stabili tutti i test con soglie di tempo.
- 29/10 19:15–21:30 — Menu ☰ sostituito dalla disposizione in stile Excel (scheda File, gruppi con
  nome, scheda Visualizza): studio, prototipo, nucleo e adeguamento dei 12 moduli con sei agenti in
  parallelo.
