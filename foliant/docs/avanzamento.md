# Avanzamento — Foliant (editor PDF desktop per Linux)

> Nome di lavoro provvisorio: **Foliant** (modificabile). App originale (codice, interfaccia, nome, icone) con copertura funzionale di livello UPDF, costruita con metodo clean-room su standard aperto ISO 32000 e librerie open source.

## Metodo e paletti (decisi in Fase 0)
- **Clean-room vero**: la specifica nasce da fonti pubbliche (documentazione, guide, recensioni) e dallo standard aperto PDF, **mai** da decompilazione di binari UPDF.
- **Scartato** il percorso REA/Ghidra + download/estrazione dell'installer UPDF: non è clean-room, viola tipicamente la licenza e tocca le norme anti-reverse-engineering. Non installiamo `rea-agents`, non installiamo Ghidra, non scarichiamo l'installer di UPDF.
- **Niente elusione di funzioni a pagamento** di prodotti altrui.
- **Grafica**: originale (icone/palette/nome propri). Si seguono le *convenzioni* d'uso degli editor PDF (non protette), non la copia pixel-per-pixel dell'identità visiva UPDF.
- Nel repo solo: `.claude/agents/`, `docs/`, e in Fase 2 il codice. Materiale di analisi fuori dal repo.

## Stato

### Fase 0 — Setup
- [x] Struttura cartelle `foliant/` (`.claude/agents/`, `docs/`).
- [x] Subagent definiti: ricercatore, analista-pdf, redattore-specifica, architetto, sviluppatore, revisore.
- [x] `.gitignore` aggiornato.
- [ ] Nota: Ghidra/REA/installer UPDF **non** installati per scelta di metodo (vedi sopra).

### Fase 1 — Ricerca e specifica (potenza massima)
Obiettivo: **parità funzionale totale con UPDF**. Orchestrazione a potenza massima:
- Modelli: Opus 5.5 `effort: max` per inventario-parità UPDF, analisi, redazione e QA; Fable 5.1 `effort: max` per la ricerca di ampiezza (concorrenti/standard/IA). Fable 5.1 max riservato al volume di codice in Fase 2.
- [ ] `docs/funzionalita.md` — catalogo con mappa di **parità UPDF** (4 ricercatori + assemblatore)
- [ ] appunti tecnici + bozze-specifica per **11 aree** in parallelo (fuori repo gli appunti)
- [ ] modello UX/UI originale
- [ ] `docs/specifica.md` (redattore)
- [ ] QA indipendente: **revisore + critico di parità + auditor di originalità**, con ciclo di correzione fino a 2 giri
- [ ] commit docs + riepilogo + STOP per approvazione

### Fase 2 — Implementazione (nuova sessione, legge SOLO docs/specifica.md)
- [ ] scelta stack (architetto)
- [ ] implementazione funzione-per-funzione con test (sviluppatore + revisore)

## Log
- 2026-10-28 — Fase 0 avviata. Creata struttura, subagent, `.gitignore`. Definito metodo clean-room su standard aperto. Avvio ricerca Fase 1.
- 2026-10-28 — Commit Fase 0 (`f257aeb`). Push bloccato: l'app Claude non ha accesso GitHub al repo per l'organizzazione (403). Commit salvato in locale; push da rifare quando GitHub è collegato.
- 2026-10-28 — **Ripensata la Fase 1 a potenza massima** (richiesta: parità totale UPDF). Workflow aggiornato: Opus 5.5 max + Fable 5.1 max, inventario-parità UPDF dedicato, 11 aree, e QA con revisore + critico di parità + auditor di originalità e ciclo di correzione. Avviato in background.
