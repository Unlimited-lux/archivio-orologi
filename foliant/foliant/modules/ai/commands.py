"""Assistente comandi (AI-23): dalla richiesta in linguaggio naturale allo strumento di Foliant.

Si inviano solo la richiesta, il catalogo degli strumenti, il numero di pagine e
le pagine della selezione corrente: mai il contenuto del documento. La proposta
viene convalidata sul catalogo; lo strumento si apre e l'operazione parte solo
con la conferma dell'utente nello strumento stesso.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from foliant.api import _

from . import engine, prompts
from .providers import ChatRequest, Message


@dataclass
class Tool:
    id: str
    description: str
    params: dict = field(default_factory=dict)   # nome -> {"type": "pages"|"page"|"int"|"text", ...}
    action: object = None                        # QAction o callable (non inviato)

    def spec(self) -> dict:
        return {"id": self.id, "description": self.description, "params": self.params}


@dataclass
class Proposal:
    tool: Tool
    params: dict
    explanation: str = ""


def catalog_from_actions(actions) -> list[Tool]:
    """Catalogo dalle azioni registrate nelle modalità (testo dell'azione = descrizione)."""
    out, seen = [], set()
    for mode_id, act in actions:
        text = act.text().replace("&", "").strip()
        if not text or not act.isEnabled():
            continue
        tid = f"{mode_id}:{text}"
        if tid in seen:
            continue
        seen.add(tid)
        out.append(Tool(tid, text, {}, act))
    return out


def validate(data, catalog: list[Tool], page_count: int) -> tuple[list[Proposal], list[str]]:
    """Accetta solo proposte conformi al catalogo e ai vincoli (pagine esistenti)."""
    by_id = {t.id: t for t in catalog}
    items = data.get("proposals") if isinstance(data, dict) else data
    ok, rejected = [], []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        tool = by_id.get(str(item.get("tool", "")))
        if tool is None:
            rejected.append(_("Strumento inesistente: «{t}».").format(t=item.get("tool", "?")))
            continue
        params = item.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        bad = _check_params(tool, params, page_count)
        if bad:
            rejected.append(bad)
            continue
        ok.append(Proposal(tool, params, str(item.get("explanation", ""))))
    return ok, rejected


def _pages_of(value) -> list[int]:
    out = []
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        for v in value:
            out.extend(_pages_of(v))
        return out
    for part in str(value).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _s, b = part.partition("-")
            try:
                out.extend(range(int(a), int(b) + 1))
            except ValueError:
                out.append(-1)
        else:
            try:
                out.append(int(part))
            except ValueError:
                out.append(-1)
    return out


def _check_params(tool: Tool, params: dict, page_count: int) -> str:
    for name, value in params.items():
        spec = tool.params.get(name, {})
        if spec.get("type") in ("pages", "page") or name in ("pages", "page", "pagine", "pagina"):
            pages = _pages_of(value)
            if not pages or any(p < 1 or p > page_count for p in pages):
                return _("Pagine non valide per «{t}»: il documento ha {n} pagine.").format(t=tool.description,
                                                                                         n=page_count)
        if spec.get("type") == "int" and name in params:
            try:
                v = int(value)
            except (TypeError, ValueError):
                return _("Parametro non valido: {p}.").format(p=name)
            if "choices" in spec and v not in spec["choices"]:
                return _("Parametro non valido: {p}.").format(p=name)
    return ""


def propose(job: engine.Job, request: str, catalog: list[Tool], page_count: int,
            selection_pages: list[int] | None = None) -> tuple[list[Proposal], list[str]]:
    profile = job.profile()
    cat = json.dumps([t.spec() for t in catalog], ensure_ascii=False)
    system = engine._system(prompts.BASE, job.language) + " " + prompts.COMMAND.replace(
        "{catalog}", cat).replace("{pages}", str(page_count))
    body = request
    if selection_pages:
        body += "\n\n(Selected pages: " + ", ".join(str(p + 1) for p in selection_pages) + ")"
    _budget, out_tokens = engine.budget_for(profile, system)
    req = ChatRequest(system, [Message("user", body)], min(out_tokens, 1024))
    r = job.gateway.send(req, function="commands", pages=[], cancel=job.cancel, on_wait=job.on_wait,
                         profile=profile)
    try:
        data = engine.parse_json_payload(r.text)
    except ValueError:
        return [], [_("La risposta non contiene proposte valide.")]
    return validate(data, catalog, page_count)


def builtin_tools(ctx) -> list[Tool]:
    """Strumenti con parametri compilabili, sempre disponibili."""
    def go(params):
        view = ctx.current_view()
        if view is not None:
            view.go_to_page(int(_pages_of(params.get("page", 1))[0]) - 1)
    return [Tool("vai-a-pagina", _("Vai a una pagina"), {"page": {"type": "page"}}, go)]
