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
- `docs/specifica.md`: 418 funzioni in 12 aree (159 P0, 177 P1, 82 P2), più 34 requisiti
  non funzionali e 22 di interfaccia.
- Revisione indipendente: «approvata con correzioni». Copertura parità: 158 righe su 177
  coperte del tutto, 19 in parte, nessuna assente. Correzioni (originalità di VIS-40 ed
  etichette, ambito, priorità, regole di precedenza, scorciatoie) comunicate alle sessioni
  e in applicazione al documento.

### Fase 2 — Implementazione 🔄
- ✅ Nucleo (orchestratore): documento, vista, finestra, tema, Home, Preferenze, scorciatoie
  UX-18, installazione Ubuntu verificata. Prestazioni: PDF di 500 pagine, modifica +
  impaginazione in ~50 ms.
- ✅ Revisione indipendente del nucleo: 24 bug riprodotti e corretti (cifratura, salvataggio,
  coordinate su pagine ritagliate, stabilità), con test di regressione. 42 test verdi.
- 🔄 12 moduli in sessioni parallele (viewer, annotations, text_edit, objects, pages, forms,
  ocr, convert, security, sign, tools, ai): alle 03:20 UTC tutti avevano già pushato
  (~80.000 righe, ~1.350 test); unione senza conflitti.
- ✅ Difetto del motore PDF su pagine ruotate/ritagliate: misurato su 120 geometrie,
  compensato in `core/placement.py` (posizionamento esatto), collegamenti cliccabili sulle
  pagine ruotate; le 7 sessioni interessate lo adottano.
- ✅ Integrazione: 12 moduli uniti senza conflitti, tutti caricati, 11 modalità operative.
  Trovati e segnalati: scorciatoie duplicate (risolte con scorciatoie per modalità nel
  nucleo), bug degli appunti nel modulo Converti, messaggio di stato persistente di Pagine,
  etichetta OCR da rinominare; corretti nel nucleo larghezza pannelli e campi numerici.
- 🔄 Collaudo end-to-end sulla build integrata. Alle 04:30 il limite di spesa ha fermato
  collaudatori e 9 sessioni su 12; ripartiti alle 07:45. Gli script di riproduzione già
  scritti sono stati rieseguiti sull'ultima build: **42 bug confermati** (Pagine 7, Proteggi 11,
  Modifica testo 11, OCR 6, Oggetti 5, Firma 2, scorciatoie duplicate tra Visualizzazione,
  Pagine e Moduli), 4 risultati già corretti. Inviati alle sessioni con priorità (perdita di
  dati e redazione prima). In corso il collaudo di Commenti, Moduli e Converti.

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
