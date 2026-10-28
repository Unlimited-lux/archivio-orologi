"""Modelli di attività per analisi specialistiche (AI-20).

Un'attività = istruzioni con segnaposto ({selezione}, {pagine}, {lingua} e i
parametri dichiarati), una categoria, un ambito richiesto e una forma d'uscita.
Le attività personali si salvano nella cartella dati e si scambiano in JSON.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field

from foliant.api import FoliantError, _

from . import engine, prompts
from .config import data_dir, write_private
from .providers import ChatRequest, Message

CATEGORIES = ["Studio", "Ricerca", "Legale", "Finanza", "Personale", "Tecnico", "Comunicazione"]
SENSITIVE = {"Legale", "Finanza", "Personale"}
BUILTIN_PLACEHOLDERS = {"selezione", "pagine", "lingua", "documento"}

DISCLAIMER = ("Avviso: questo risultato è generato automaticamente e non sostituisce il parere di un "
              "professionista né una decisione presa da una persona.")


@dataclass
class Param:
    name: str
    type: str = "text"            # text | number | choice
    default: str = ""
    choices: list[str] = field(default_factory=list)


@dataclass
class TaskTemplate:
    id: str
    name: str
    category: str
    description: str
    instructions: str
    scope: str = "document"       # document | selection | any
    params: list[Param] = field(default_factory=list)
    output: str = "text"          # text | table | questions
    builtin: bool = False
    images: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("builtin", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskTemplate":
        if not isinstance(d, dict):
            raise ValueError(_("Formato non valido."))
        for key in ("name", "category", "instructions"):
            if not isinstance(d.get(key), str) or not d[key].strip():
                raise ValueError(_("Campo obbligatorio mancante: {k}.").format(k=key))
        if d["category"] not in CATEGORIES:
            raise ValueError(_("Categoria sconosciuta: {c}.").format(c=d["category"]))
        params = []
        for p in d.get("params", []) or []:
            if not isinstance(p, dict) or not p.get("name"):
                raise ValueError(_("Parametro non valido."))
            params.append(Param(str(p["name"]), str(p.get("type", "text")), str(p.get("default", "")),
                                list(p.get("choices", []) or [])))
        t = cls(id=str(d.get("id") or uuid.uuid4().hex[:10]), name=d["name"].strip(), category=d["category"],
                description=str(d.get("description", "")), instructions=d["instructions"],
                scope=str(d.get("scope", "document")), params=params, output=str(d.get("output", "text")),
                images=bool(d.get("images", False)))
        unknown = placeholders(t.instructions) - BUILTIN_PLACEHOLDERS - {p.name for p in params}
        if unknown:
            raise ValueError(_("Segnaposto sconosciuti: {p}.").format(p=", ".join(sorted(unknown))))
        return t

    @property
    def sensitive(self) -> bool:
        return self.category in SENSITIVE


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def _b(id_, name, cat, desc, instr, scope="document", params=None, output="text"):
    return TaskTemplate(id_, name, cat, desc, instr, scope, params or [], output, builtin=True)


BUILTIN = [
    _b("generic", "Attività personalizzata", "Comunicazione", "Istruzioni libere sul documento o sulla selezione.",
       "{istruzioni} Answer in {lingua}.", scope="any", params=[Param("istruzioni", "text", "")]),
    _b("contract-summary", "Scheda di un contratto", "Legale",
       "Parti, oggetto, prezzo, pagamenti, garanzie, scadenze e recesso in una tabella.",
       "Extract from the contract a table with rows: parties, object, price, payment terms, warranty, delivery, "
       "deadlines, termination/withdrawal, penalties, governing law; one column with the value and one with the "
       "page citation [p. N]; write \"not found\" when missing. Then list clauses that deserve attention. "
       "Answer in {lingua}.", output="table"),
    _b("invoice-check", "Controllo di fatture e ricevute", "Finanza",
       "Dati principali, totali, IVA e incongruenze.",
       "For each invoice or receipt in the content, report number, date, supplier, VAT number, customer, lines, "
       "taxable amount, VAT and total in a table with page citations, then check that the line amounts add up "
       "to the taxable amount and that taxable amount + VAT equals the total, listing any inconsistency. "
       "Answer in {lingua}.", output="table"),
    _b("product-sheet", "Scheda prodotto", "Comunicazione",
       "Scheda o annuncio di vendita dalle informazioni del documento.",
       "Write a product sheet for {prodotto} using only information in the content: title, short description, "
       "technical specifications table (brand, model, reference, year, material, size, movement, condition, "
       "accessories, when available), selling points and a {canale} listing text. Do not invent data: mark "
       "missing items as \"da verificare\". Answer in {lingua}.",
       params=[Param("prodotto", "text", "l'articolo"), Param("canale", "choice", "e-commerce",
                                                            ["e-commerce", "marketplace", "social"])]),
    _b("customer-reply", "Comunicazione al cliente", "Comunicazione",
       "Risposta o comunicazione al cliente basata sul documento.",
       "Write a {tono} message to the customer about: {argomento}. Never invent recipient data: use placeholders in square brackets such as [nome del cliente]. Base facts only on the content and cite pages "
       "[p. N] in a separate note for the operator (not in the message). Answer in {lingua}.", scope="any",
       params=[Param("argomento", "text", ""), Param("tono", "choice", "cordiale",
                                                     ["cordiale", "formale", "professionale"])]),
    _b("compare", "Confronto di documenti o versioni", "Ricerca",
       "Somiglianze e differenze tra le parti del contenuto.",
       "Compare the documents or sections in the content: a table of the main points with the value in each one "
       "and the page citations, then the relevant differences. Answer in {lingua}.", output="table"),
    _b("contract-risk", "Analisi dei rischi di un contratto", "Legale",
       "Clausole principali, obblighi, scadenze e rischi.",
       "Analyse the contract: parties, object, obligations, deadlines, penalties, termination, and list the main "
       "risks for {parte} ordered by severity, citing pages. Answer in {lingua}.",
       params=[Param("parte", "text", "il cliente")]),
    _b("contract-draft", "Bozza di contratto", "Legale", "Bozza di contratto dai punti indicati.",
       "Draft a contract of type {tipo} based on the provided content and these points: {punti}. "
       "Answer in {lingua}.", scope="any", params=[Param("tipo", "text", "compravendita"), Param("punti")]),
    _b("financial", "Spiegazione di bilanci e rendiconti", "Finanza",
       "Voci principali, indicatori e andamento.",
       "Explain the financial statements in the content: key items, ratios, trends and anomalies, citing pages. "
       "Answer in {lingua}."),
    _b("cashflow", "Analisi dei flussi di cassa", "Finanza", "Entrate, uscite e liquidità.",
       "Analyse the cash flows: operating, investing, financing, and liquidity risks, citing pages. "
       "Answer in {lingua}."),
    _b("paper", "Analisi di un articolo scientifico", "Ricerca", "Domanda, metodo, risultati, limiti.",
       "Analyse the scientific paper: research question, method, results, limitations, contribution, citing "
       "pages. Answer in {lingua}."),
    _b("techdoc", "Riassunto di documentazione tecnica", "Tecnico", "Requisiti, procedure e avvertenze.",
       "Summarise the technical documentation: purpose, requirements, procedures, warnings, citing pages. "
       "Answer in {lingua}."),
    _b("cv", "Valutazione di curricula", "Personale", "Corrispondenza con i requisiti del ruolo.",
       "Evaluate the CV against these role requirements: {requisiti}. Consider only information relevant to the "
       "role; ignore and do not mention age, marital status, photos or other personal data not pertinent to "
       "the role. Give strengths, gaps and a summary table. Answer in {lingua}.",
       params=[Param("requisiti", "text", "")], output="table"),
    _b("quiz", "Generazione di quiz", "Studio", "Domande con soluzioni separate.",
       "Create {numero} {tipo} questions on the content. Reply ONLY with JSON: {{\"questions\": [{{\"question\": "
       "..., \"options\": [...], \"answer\": ..., \"explanation\": ..., \"page\": N}}]}}. Write in {lingua}.",
       params=[Param("numero", "number", "5"), Param("tipo", "choice", "multiple choice",
                                                    ["multiple choice", "true/false", "open"])],
       output="questions"),
    _b("math", "Soluzione guidata di esercizi", "Studio", "Passaggi spiegati uno per uno.",
       "Solve the exercise step by step, explaining each step: {selezione}. Answer in {lingua}.", scope="any"),
    _b("study", "Supporto allo studio", "Studio", "Concetti chiave, definizioni, domande di ripasso.",
       "Prepare study notes: key concepts, definitions, and 5 review questions, citing pages. Answer in {lingua}."),
    _b("business-plan", "Piano d'impresa", "Comunicazione", "Struttura di un piano d'impresa.",
       "Draft a business plan outline for {idea} using the content as background. Answer in {lingua}.",
       scope="any", params=[Param("idea", "text", "")]),
    _b("strategy", "Analisi strategica", "Comunicazione", "Punti di forza e debolezza, opportunità e rischi.",
       "Perform a strategic analysis (strengths, weaknesses, opportunities, threats) of the content, citing "
       "pages. Answer in {lingua}."),
    _b("video", "Contenuti per video", "Comunicazione", "Titoli, descrizione, parole chiave, copione.",
       "From the content produce: 5 video titles, a description, 10 keywords and a {durata}-minute script. "
       "Answer in {lingua}.", params=[Param("durata", "number", "3")]),
]


# Sezioni fisse dei modelli predefiniti (AI-20 v2): mostrate nell'ordine del modello.
SECTIONS = {
    "business-plan": ["Sintesi", "Mercato", "Offerta", "Organizzazione", "Piano economico", "Rischi"],
    "video": ["Titoli", "Descrizione", "Parole chiave", "Copione"],
    "paper": ["Obiettivi", "Metodo", "Risultati", "Limiti"],
    "cashflow": ["Gestione operativa", "Gestione di investimento", "Gestione di finanziamento"],
    "strategy": ["Punti di forza", "Debolezze", "Opportunità", "Minacce", "Raccomandazione"],
}


def apply_sections(text: str, sections: list[str]) -> str:
    """Riordina la risposta nelle sezioni del modello; le sezioni assenti risultano «da completare»."""
    import re as _re
    parts: dict[str, list[str]] = {}
    extra: list[str] = []
    current = None
    for line in text.splitlines():
        m = _re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            title = m.group(1).strip().strip("*").strip()
            current = next((sec for sec in sections if title.lower().startswith(sec.lower())), None)
            if current is not None:
                parts.setdefault(current, [])
                continue
        (parts[current] if current is not None else extra).append(line)
    out = [line for line in extra if line.strip()] and ["\n".join(extra).strip(), ""] or []
    for sec in sections:
        body = "\n".join(parts.get(sec, [])).strip()
        out.append(f"## {sec}")
        out.append(body if body else "_" + _("da completare") + "_")
        out.append("")
    return "\n".join(out).strip()


class TaskStore:
    def __init__(self, path=None):
        self._path = path

    @property
    def path(self):
        return self._path or data_dir() / "attivita.json"

    def custom(self) -> list[TaskTemplate]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        out = []
        for d in data if isinstance(data, list) else []:
            try:
                out.append(TaskTemplate.from_dict(d))
            except ValueError:
                continue
        return out

    def all(self) -> list[TaskTemplate]:
        return list(BUILTIN) + self.custom()

    def search(self, text: str = "", category: str = "") -> list[TaskTemplate]:
        t = text.lower().strip()
        return [x for x in self.all() if (not category or x.category == category)
                and (not t or t in x.name.lower() or t in x.description.lower())]

    def _write(self, items: list[TaskTemplate]) -> None:
        write_private(self.path, json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=1))

    def save(self, task: TaskTemplate) -> None:
        if task.builtin:
            raise FoliantError(_("Le attività predefinite non si modificano: duplicale."))
        items = [t for t in self.custom() if t.id != task.id] + [task]
        self._write(items)

    def duplicate(self, task: TaskTemplate) -> TaskTemplate:
        d = task.to_dict()
        d["id"] = uuid.uuid4().hex[:10]
        d["name"] = _("{n} (copia)").format(n=task.name)
        t = TaskTemplate.from_dict(d)
        self.save(t)
        return t

    def delete(self, task_id: str) -> None:
        self._write([t for t in self.custom() if t.id != task_id])

    @staticmethod
    def export(tasks: list[TaskTemplate]) -> str:
        return json.dumps({"foliant_tasks": 1, "tasks": [t.to_dict() for t in tasks]}, ensure_ascii=False,
                          indent=2)

    def import_text(self, text: str) -> list[TaskTemplate]:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise FoliantError(_("File di attività non valido: {e}").format(e=e)) from e
        items = data.get("tasks") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise FoliantError(_("File di attività non valido: manca l'elenco delle attività."))
        out = []
        for n, d in enumerate(items, 1):
            try:
                out.append(TaskTemplate.from_dict(d))
            except ValueError as e:
                raise FoliantError(_("Attività {n} non valida: {e}").format(n=n, e=e)) from e
        for t in out:
            self.save(t)
        return out


def build_instructions(task: TaskTemplate, values: dict, scope: engine.Scope, language: str) -> str:
    vals = {p.name: str(values.get(p.name, p.default)) for p in task.params}
    vals.update({"lingua": prompts.language_name(language), "selezione": scope.selection or "",
                 "pagine": engine.compact_ranges(scope.page_indices) if scope.page_indices else "",
                 "documento": "the provided document"})
    text = task.instructions
    for k, v in vals.items():
        text = text.replace("{" + k + "}", v)
    return text.replace("{{", "{").replace("}}", "}")


def run_task(job: engine.Job, task: TaskTemplate, values: dict, scope: engine.Scope) -> engine.Answer:
    profile = job.profile()
    if task.images and not profile.images:
        raise FoliantError(_("Questa attività richiede un modello che accetti immagini."))
    system = engine._system(prompts.BASE, job.language) + " " + prompts.CITE + " " + \
        build_instructions(task, values, scope, job.language)
    if task.id in SECTIONS:
        system += " Use exactly these Markdown headings (## …), in this order: " + \
            "; ".join(SECTIONS[task.id]) + "."
    budget, out_tokens = engine.budget_for(profile, system)
    body = engine.scope_text(scope)
    if len(body) > budget:
        from .chunking import make_chunks, select_chunks
        body = "\n\n".join(c.text for c in select_chunks(make_chunks(scope.pages, budget // 6),
                                                          task.instructions, budget))
    content = f"<document>\n{body}\n</document>" if body else _("(nessun documento)")
    req = ChatRequest(system, [Message("user", content)], out_tokens)
    r = job.gateway.send(req, function=f"task:{task.id}", pages=scope.page_indices, fingerprint=scope.fingerprint,
                         on_text=job.on_text if task.output != "questions" else None, cancel=job.cancel,
                         on_wait=job.on_wait, profile=profile)
    text = r.text
    data = None
    if task.id in SECTIONS:
        text = apply_sections(text, SECTIONS[task.id])
    if task.output == "questions":
        try:
            data = parse_quiz(text)
            text = quiz_markdown(data, with_solutions=False)
        except ValueError:
            pass
    flags = []
    if task.sensitive:
        text = text.rstrip() + "\n\n> " + _(DISCLAIMER)
        flags.append(_("non sostituisce il parere di un professionista"))
    from .render import placeholders as _placeholders
    todo = _placeholders(text)
    if todo:
        flags.append(_("da completare: {p}").format(p=", ".join(f"[{t}]" for t in todo)))
    refs, rflags = engine._ref_flags(text, scope)
    return engine.Answer(text, engine._status(r), refs, flags, data=data)


def parse_quiz(text: str) -> list[dict]:
    data = engine.parse_json_payload(text)
    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError(_("Nessuna domanda nella risposta."))
    out = []
    for q in items:
        if not isinstance(q, dict) or not q.get("question"):
            raise ValueError(_("Domanda non valida."))
        out.append({"question": str(q["question"]), "options": [str(o) for o in q.get("options", []) or []],
                    "answer": str(q.get("answer", "")), "explanation": str(q.get("explanation", "")),
                    "page": q.get("page")})
    return out


def quiz_markdown(items: list[dict], with_solutions: bool = True) -> str:
    lines = ["## " + _("Domande"), ""]
    for n, q in enumerate(items, 1):
        lines.append(f"{n}. {q['question']}" + (f" [p. {q['page']}]" if q.get("page") else ""))
        for i, o in enumerate(q["options"]):
            lines.append(f"   {chr(97 + i)}) {o}")
    if with_solutions:
        lines += ["", "## " + _("Soluzioni"), ""]
        for n, q in enumerate(items, 1):
            lines.append(f"{n}. {q['answer']}" + (f" — {q['explanation']}" if q["explanation"] else ""))
    return "\n".join(lines)
