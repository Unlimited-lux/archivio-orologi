"""Confronto e classifica di più documenti (AI-32).

Il fornitore valuta ogni documento per ogni criterio (punteggio 0–10 con
motivazione e pagine); il totale e l'ordinamento li calcola Foliant:
Totale = Σ(punteggio × peso) / Σ(pesi), con due decimali. Ai documenti si
assegnano etichette neutre (A, B, …): i nomi dei file non vengono inviati.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from foliant.api import FoliantError, _

from . import chunking, engine, prompts
from .providers import ChatRequest, Message

HUMAN_DECISION = ("Avviso: la classifica è un supporto e non sostituisce una decisione presa da una persona.")
CV_RULES = ("Evaluate only information relevant to the criteria. Ignore and never mention age, marital status, "
            "photos and origin.")


@dataclass
class Criterion:
    nome: str
    descrizione: str = ""
    peso: int = 1
    obbligatorio: bool = False


@dataclass
class Evaluation:
    criterio: str
    punteggio: float
    motivazione: str = ""
    pagine: list[int] = field(default_factory=list)   # 0-based


@dataclass
class RankRow:
    documento: str              # percorso (solo locale)
    etichetta: str              # lettera neutra inviata al fornitore
    titolo: str = ""
    scores: dict[str, Evaluation] = field(default_factory=dict)
    totale: float | None = None
    posizione: int | None = None
    escluso: bool = False
    motivo: str = ""


def total(scores: dict[str, Evaluation], criteria: list[Criterion]) -> float:
    num = sum(scores[c.nome].punteggio * c.peso for c in criteria if c.nome in scores)
    den = sum(c.peso for c in criteria if c.nome in scores)
    return round(num / den, 2) if den else 0.0


def rank(rows: list[RankRow], criteria: list[Criterion]) -> list[RankRow]:
    """Calcola totali, esclusioni e posizioni (parità = stessa posizione)."""
    for r in rows:
        if r.escluso:
            continue
        for c in criteria:
            ev = r.scores.get(c.nome)
            if c.obbligatorio and (ev is None or ev.punteggio <= 0):
                r.escluso = True
                r.motivo = _("punteggio 0 nel criterio obbligatorio «{c}»").format(c=c.nome)
                break
        r.totale = None if r.escluso else total(r.scores, criteria)
    ranked = sorted([r for r in rows if not r.escluso], key=lambda r: -(r.totale or 0))
    pos, prev = 0, None
    for i, r in enumerate(ranked, 1):
        if r.totale != prev:
            pos, prev = i, r.totale
        r.posizione = pos
    excluded = [r for r in rows if r.escluso]
    for r in excluded:
        r.posizione = None
    return ranked + excluded


def _prompt(criteria: list[Criterion], cv_mode: bool) -> str:
    crit = "; ".join(f"«{c.nome}»: {c.descrizione or c.nome}" for c in criteria)
    s = ("Evaluate the document against each criterion with a score from 0 to 10, a short motivation and the "
         "page labels supporting it. Reply ONLY with JSON: {\"evaluations\": [{\"criterio\": name, "
         "\"punteggio\": number, \"motivazione\": text, \"pagine\": [page labels]}]}. Criteria: " + crit + ".")
    if cv_mode:
        s += " " + CV_RULES
    return s


def _parse(text: str, criteria: list[Criterion], space) -> dict[str, Evaluation] | None:
    try:
        data = engine.parse_json_payload(text)
    except ValueError:
        return None
    items = data.get("evaluations") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    names = {c.nome for c in criteria}
    out: dict[str, Evaluation] = {}
    for it in items:
        if not isinstance(it, dict) or it.get("criterio") not in names:
            continue
        try:
            score = max(0.0, min(10.0, float(it.get("punteggio", 0))))
        except (TypeError, ValueError):
            continue
        pages = []
        for p in it.get("pagine") or []:
            idx = space.resolve(str(p)) if space is not None else None
            if idx is not None:
                pages.append(idx)
        out[it["criterio"]] = Evaluation(it["criterio"], score, str(it.get("motivazione", "")), pages)
    return out if out else None


def evaluate(job: engine.Job, prepared, criteria: list[Criterion], *, cv_mode: bool = False,
             excluded: list = ()) -> tuple[list[RankRow], list[str]]:
    """`prepared` = collection.prepare(...): [(Member, pagine, PageSpace)]. Restituisce (righe, avvisi)."""
    if not criteria:
        raise FoliantError(_("Definisci almeno un criterio."))
    profile = job.profile()
    warnings = []
    if len(prepared) > 50:
        warnings.append(_("Più di 50 documenti: l'operazione richiede molte richieste al fornitore."))
    system = engine._system(prompts.BASE, job.language) + " " + _prompt(criteria, cv_mode)
    budget, out_tokens = engine.budget_for(profile, system)
    rows = []
    for n, (member, pages, space) in enumerate(prepared):
        job.step(n / max(len(prepared), 1), _("Valutazione del documento {l}").format(l=space.name))
        text = chunking.format_pages(pages, space.name)
        if len(text) > budget:
            chunks = chunking.make_chunks(pages, max(400, budget // 4), doc=space.name)
            query = " ".join(c.nome + " " + c.descrizione for c in criteria)
            text = "\n\n".join(c.text for c in chunking.select_chunks(chunks, query, budget))
        body = f"Document {space.name}:\n<document>\n{text}\n</document>"
        scores = None
        for attempt in range(2):
            msgs = [Message("user", body if attempt == 0 else body + "\n\nReply ONLY with valid JSON as specified.")]
            r = job.gateway.send(ChatRequest(system, msgs, out_tokens), function="ranking",
                                 pages=[p.index for p in pages], cancel=job.cancel, on_wait=job.on_wait,
                                 profile=profile)
            scores = _parse(r.text, criteria, space)
            if scores is not None:
                break
        row = RankRow(member.path, space.name, member.title)
        if scores is None:
            row.escluso, row.motivo = True, _("risposta non valida del fornitore")
        else:
            row.scores = scores
        rows.append(row)
    for m in excluded:
        rows.append(RankRow(m.path, "—", m.title, escluso=True, motivo=m.excluded))
    if cv_mode:
        warnings.append(_(HUMAN_DECISION))
    return rank(rows, criteria), warnings


def to_csv(rows: list[RankRow], criteria: list[Criterion], sep: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep)
    w.writerow([_("Posizione"), _("Documento"), _("Etichetta")] + [c.nome for c in criteria] +
               [_("Totale"), _("Stato")])
    for r in rows:
        w.writerow([r.posizione or "", r.titolo, r.etichetta] +
                   [f"{r.scores[c.nome].punteggio:g}" if c.nome in r.scores else "" for c in criteria] +
                   [f"{r.totale:.2f}" if r.totale is not None else "",
                    _("escluso: {m}").format(m=r.motivo) if r.escluso else ""])
    return buf.getvalue()


def criteria_to_json(criteria: list[Criterion]) -> str:
    return json.dumps([c.__dict__ for c in criteria], ensure_ascii=False, indent=1)


def criteria_from_json(text: str) -> list[Criterion]:
    data = json.loads(text)
    out = []
    for d in data:
        out.append(Criterion(str(d["nome"]), str(d.get("descrizione", "")), max(1, min(5, int(d.get("peso", 1)))),
                             bool(d.get("obbligatorio", False))))
    return out
