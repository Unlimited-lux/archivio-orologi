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

### Fase 1 — Ricerca e specifica
- [ ] `docs/funzionalita.md` (ricercatore)
- [ ] appunti tecnici per area (analista-pdf, fuori repo)
- [ ] `docs/specifica.md` (redattore-specifica)
- [ ] revisione specifica (revisore)
- [ ] commit docs + riepilogo + STOP per approvazione

### Fase 2 — Implementazione (nuova sessione, legge SOLO docs/specifica.md)
- [ ] scelta stack (architetto)
- [ ] implementazione funzione-per-funzione con test (sviluppatore + revisore)

## Log
- 2026-10-28 — Fase 0 avviata. Creata struttura, subagent, `.gitignore`. Definito metodo clean-room su standard aperto. Avvio ricerca Fase 1.
