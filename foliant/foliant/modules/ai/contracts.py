"""Contratti (AI-28): bozza ad articoli, revisione per clausole, rischi e ricerca giuridica."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

import pymupdf

from foliant.api import ENGINE_LOCK, FoliantError, _

from . import chunking, engine, extract, prompts
from .providers import ChatRequest, Message
from .tasks_library import DISCLAIMER

LEVELS = ("bassa", "media", "alta")


@dataclass
class Clause:
    numero: str
    titolo: str
    pagina: int | None
    testo: str = ""
    sintesi: str = ""
    criticita: str = "bassa"
    lacune: str = ""
    proposta: str = ""
    verified: bool = True


@dataclass
class Risk:
    descrizione: str
    clausola: str = ""
    probabilita: str = ""
    impatto: str = ""
    mitigazione: str = ""


@dataclass
class Source:
    riferimento: str
    citazione: str = ""
    origine: str = ""
    da_verificare: bool = True


@dataclass
class RisksResult:
    risks: list[Risk] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    disclaimer: str = ""


def disclaimer() -> str:
    return _(DISCLAIMER)


def _ask_json(job, system: str, body: str, *, function: str, scope=None):
    profile = job.profile()
    _budget, out_tokens = engine.budget_for(profile, system)
    for attempt in range(2):
        text = body if attempt == 0 else body + "\n\nReply ONLY with valid JSON as specified."
        r = job.gateway.send(ChatRequest(system, [Message("user", text)], out_tokens), function=function,
                             pages=scope.page_indices if scope else [], fingerprint=scope.fingerprint if scope else "",
                             cancel=job.cancel, on_wait=job.on_wait, profile=profile)
        try:
            return engine.parse_json_payload(r.text)
        except ValueError:
            continue
    raise FoliantError(_("La risposta del modello non ha la struttura attesa."))


# ------------------------------------------------------------------ bozza
def number_articles(text: str) -> str:
    """Testo diviso in articoli numerati «Art. N — Titolo»."""
    lines = text.strip().splitlines()
    out, n = [], 0
    for line in lines:
        t = line.strip()
        m = re.match(r"^(?:#{1,6}\s*)?(?:\*\*)?(?:art(?:icolo|\.)?\s*\d+\s*[-–—.:)]?\s*)(.*?)(?:\*\*)?$", t, re.I)
        if m:
            n += 1
            title = m.group(1).strip(" -–—*")
            out.append(f"**Art. {n}**" + (f" — {title}" if title else ""))
        else:
            out.append(line)
    if n == 0:   # nessun articolo riconosciuto: un articolo per paragrafo
        paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
        out = []
        for i, p in enumerate(paras, 1):
            out.append(f"**Art. {i}**\n{p}")
        return "\n\n".join(out)
    return "\n".join(out)


def draft_contract(job, params: dict) -> engine.Answer:
    fields = "; ".join(f"{k}: {v}" for k, v in params.items() if v)
    system = engine._system(prompts.BASE, job.language) + (
        " Draft a contract divided into numbered articles, each starting with a line «Art. N — Title». "
        "Use these parameters: " + fields + ".")
    profile = job.profile()
    _budget, out_tokens = engine.budget_for(profile, system)
    r = job.gateway.send(ChatRequest(system, [Message("user", fields)], out_tokens), function="contract-draft",
                         cancel=job.cancel, on_text=None, on_wait=job.on_wait, profile=profile)
    text = number_articles(r.text) + "\n\n> " + disclaimer()
    return engine.Answer(text, engine._status(r), flags=[_("non sostituisce il parere di un professionista")])


# ------------------------------------------------------------------ revisione
def _page_text(scope, page) -> str:
    for p in scope.pages:
        if p.index == page:
            return p.text
    return "\n".join(p.text for p in scope.pages)


def review_contract(job, scope: engine.Scope) -> list[Clause]:
    system = engine._system(prompts.BASE, job.language) + " " + prompts.CITE + (
        " Review the contract clause by clause. Reply ONLY with JSON: {\"clauses\": [{\"numero\": str, "
        "\"titolo\": str, \"pagina\": page label, \"testo\": short verbatim quote, \"sintesi\": str, "
        "\"criticita\": \"bassa\"|\"media\"|\"alta\", \"lacune\": str, \"proposta\": str}]}.")
    profile = job.profile()
    budget, _o = engine.budget_for(profile, system)
    text = engine.scope_text(scope)
    bodies = [text] if len(text) <= budget else [c.text for c in chunking.make_chunks(scope.pages, budget)]
    clauses: list[Clause] = []
    for body in bodies:
        data = _ask_json(job, system, f"<document>\n{body}\n</document>", function="contract-review", scope=scope)
        items = data.get("clauses") if isinstance(data, dict) else data
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            label = it.get("pagina")
            page = scope.space.resolve(str(label)) if scope.space is not None and label not in (None, "") else None
            quote = str(it.get("testo", "")).strip()
            src = " ".join(_page_text(scope, page).split()).lower()
            verified = not quote or " ".join(quote.split()).lower() in src
            level = str(it.get("criticita", "bassa")).lower()
            clauses.append(Clause(str(it.get("numero", len(clauses) + 1)), str(it.get("titolo", "")), page, quote,
                                  str(it.get("sintesi", "")), level if level in LEVELS else "bassa",
                                  str(it.get("lacune", "")), str(it.get("proposta", "")), verified))
    if not clauses:
        raise extract.NoData()
    return clauses


def add_review_comments(document, clauses: list[Clause], author: str | None = None) -> int:
    """Una nota per ogni clausola di criticità media o alta, in un solo passo annullabile."""
    targets = [c for c in clauses if c.criticita in ("media", "alta") and c.pagina is not None]
    if not targets:
        return 0
    with document.change(_("Commenti della revisione del contratto")) as pdf:
        for c in targets:
            page = pdf[c.pagina]
            point = pymupdf.Point(36, 36)
            if c.testo:
                hits = page.search_for(c.testo[:60])
                if hits:
                    point = pymupdf.Point(hits[0].x0, max(hits[0].y0 - 20, 0))
            body = _("Clausola {n} — criticità {l}: {s}").format(n=c.numero, l=c.criticita, s=c.sintesi)
            if c.proposta:
                body += "\n" + _("Proposta: {p}").format(p=c.proposta)
            annot = page.add_text_annot(point, body, icon="Comment")
            annot.set_info(title=author or _("Assistente IA"), subject=_("Revisione contratto"), content=body)
            annot.update()
    return len(targets)


def review_to_csv(clauses: list[Clause], sep: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
    w.writerow([_("numero"), _("titolo"), _("pagina"), _("sintesi"), _("criticità"), _("lacune"), _("proposta"),
                _("verifica")])
    for c in clauses:
        w.writerow([c.numero, c.titolo, c.pagina + 1 if c.pagina is not None else "", c.sintesi, c.criticita,
                    c.lacune, c.proposta, "" if c.verified else _("non verificato")])
    return buf.getvalue()


def review_markdown(clauses: list[Clause]) -> str:
    lines = [f"| {_('N.')} | {_('Titolo')} | {_('Pagina')} | {_('Criticità')} | {_('Sintesi')} | {_('Proposta')} |",
             "|---|---|---|---|---|---|"]
    for c in clauses:
        mark = "" if c.verified else f" ({_('non verificato')})"
        lines.append(f"| {c.numero} | {c.titolo} | {c.pagina + 1 if c.pagina is not None else ''} | {c.criticita} | "
                     f"{c.sintesi}{mark} | {c.proposta} |")
    return "\n".join(lines) + "\n\n> " + disclaimer()


def communication_source(clauses: list[Clause]) -> str:
    """Testo di partenza per «Prepara comunicazione al cliente» (AI-31): le clausole critiche."""
    crit = [c for c in clauses if c.criticita in ("media", "alta")]
    return "\n".join(f"- {_('Clausola')} {c.numero} ({c.titolo}, {_('criticità')} {c.criticita}): {c.sintesi}"
                     + (f" — {_('proposta')}: {c.proposta}" if c.proposta else "") for c in crit)


# ------------------------------------------------------------------ rischi e ricerca giuridica
def risks_and_research(job, scope: engine.Scope, party: str, jurisdiction: str = "italiano",
                       web_search: bool = False) -> RisksResult:
    system = engine._system(prompts.BASE, job.language) + " " + prompts.CITE + (
        f" Assess the risks of the contract for {party} under the {jurisdiction} legal system and list the relevant "
        "laws and principles. Reply ONLY with JSON: {\"risks\": [{\"descrizione\", \"clausola\", \"probabilita\", "
        "\"impatto\", \"mitigazione\"}], \"sources\": [{\"riferimento\", \"citazione\"}]}.")
    profile = job.profile()
    budget, _o = engine.budget_for(profile, system)
    data = _ask_json(job, system, f"<document>\n{engine.scope_text(scope)[:budget]}\n</document>",
                     function="contract-risks", scope=scope)
    res = RisksResult(disclaimer=disclaimer())
    for it in (data.get("risks") if isinstance(data, dict) else None) or []:
        if isinstance(it, dict) and it.get("descrizione"):
            res.risks.append(Risk(str(it["descrizione"]), str(it.get("clausola", "")), str(it.get("probabilita", "")),
                                  str(it.get("impatto", "")), str(it.get("mitigazione", ""))))
    origin = _("ricerca web del fornitore") if web_search else _("conoscenza del modello")
    for it in (data.get("sources") if isinstance(data, dict) else None) or []:
        if isinstance(it, dict) and it.get("riferimento"):
            res.sources.append(Source(str(it["riferimento"]), str(it.get("citazione", "")), origin,
                                      da_verificare=not web_search))
    return res


def risks_markdown(res: RisksResult) -> str:
    lines = ["## " + _("Rischi")]
    for r in res.risks:
        lines.append(f"- **{r.descrizione}** ({_('clausola')} {r.clausola}; {r.probabilita}/{r.impatto}) — "
                     f"{r.mitigazione}")
    lines += ["", "## " + _("Fonti")]
    for s in res.sources:
        mark = f" — _{_('da verificare')}_" if s.da_verificare else ""
        lines.append(f"- {s.riferimento}: {s.citazione} ({s.origine}){mark}")
    return "\n".join(lines) + "\n\n> " + res.disclaimer
