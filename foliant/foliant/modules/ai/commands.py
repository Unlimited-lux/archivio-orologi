"""Assistente comandi (AI-23): dalla richiesta in linguaggio naturale allo strumento di Foliant.

Si inviano solo la richiesta, il catalogo degli strumenti, il numero di pagine e
le pagine della selezione corrente: mai il contenuto del documento. La proposta
viene convalidata sul catalogo; lo strumento si apre e l'operazione parte solo
con la conferma dell'utente nello strumento stesso.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from foliant.api import FoliantError, _

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


def page_indices(value) -> list[int]:
    """Pagine di un parametro (numeri da 1, intervalli «3-5») come indici da 0, senza ripetizioni."""
    return sorted({p - 1 for p in _pages_of(value) if p >= 1})


def builtin_tools(ctx, exec_dialog=None) -> list[Tool]:
    """Strumenti con parametri compilabili, sempre disponibili, più quelli degli altri moduli."""
    def go(params):
        view = ctx.current_view()
        if view is not None:
            view.go_to_page(int(_pages_of(params.get("page", 1))[0]) - 1)
    return [Tool("vai-a-pagina", _("Vai a una pagina"), {"page": {"type": "page"}}, go)] + \
        module_tools(ctx, exec_dialog)


# Dialoghi del modulo Pagine che accettano le pagine da usare: (id, descrizione, sotto-modulo, funzione).
# Si aprono con le pagine proposte già scelte; l'operazione parte solo con il pulsante del dialogo.
PAGE_DIALOGS = (
    ("estrai-pagine", "Estrai pagine in un nuovo PDF", "dialogs", "open_extract_dialog"),
    ("duplica-pagine", "Duplica pagine", "dialogs", "open_duplicate_dialog"),
    ("sposta-pagine", "Sposta pagine", "dialogs", "open_move_dialog"),
    ("dividi-documento", "Dividi il documento in più file", "dialogs", "open_split_dialog"),
    ("ritaglia-pagine", "Ritaglia pagine", "layout_dialogs", "open_crop_dialog"),
    ("ridimensiona-pagine", "Ridimensiona pagine", "layout_dialogs", "open_resize_dialog"),
    ("filigrana", "Aggiungi una filigrana", "stamp_dialogs", "open_watermark_dialog"),
    ("numeri-di-pagina", "Aggiungi i numeri di pagina", "stamp_dialogs", "open_page_numbers_dialog"),
    ("intestazione-pie-di-pagina", "Aggiungi intestazione e piè di pagina", "stamp_dialogs",
     "open_header_footer_dialog"),
    ("esporta-immagini", "Esporta le pagine come immagini", "images_dialogs", "open_export_images_dialog"),
)
ROTATIONS = (90, 180, 270)


def _page_dialog(ctx, module: str, func: str):
    import importlib
    try:
        fn = getattr(importlib.import_module(f"foliant.modules.pages.{module}"), func)
    except (ImportError, AttributeError):
        return None          # modulo Pagine assente o diverso: lo strumento non entra nel catalogo

    def run(params):
        view = ctx.current_view()
        if view is None:
            return False
        pages = [p for p in page_indices(params.get("pages", "")) if p < view.page_count]
        if len(pages) == 1:
            view.go_to_page(pages[0])     # con una sola pagina il dialogo propone la pagina corrente
        return fn(ctx, view, pages or [view.current_page])
    return run


def module_tools(ctx, exec_dialog=None) -> list[Tool]:
    """Strumenti degli altri moduli con i parametri precompilati (AI-23)."""
    from .commands_view import ParamsDialog
    pages_param = {"pages": {"type": "pages", "description": "page numbers from 1, e.g. \"3-5, 8\""}}
    tools = []
    for tid, desc, module, func in PAGE_DIALOGS:
        run = _page_dialog(ctx, module, func)
        if run is not None:
            tools.append(Tool(tid, _(desc), dict(pages_param), run))
    try:
        from foliant.modules.pages import engine as page_engine
    except ImportError:
        return tools
    run_dialog = exec_dialog or (lambda dlg: dlg.exec())

    def page_operation(title, needs_angle, operation):
        def run(params):
            view = ctx.current_view()
            if view is None:
                return False
            dlg = ParamsDialog(title, view.page_count, params, angle=needs_angle, parent=ctx.window)
            if not run_dialog(dlg) or not dlg.accepted_values:
                return False
            values = dlg.accepted_values
            try:
                with view.document.change(title, kind="pages") as pdf:
                    operation(pdf, values)
            except FoliantError as e:
                ctx.error(title, str(e))
                return False
            ctx.notify(_("{t}: pagine {p}.").format(t=title, p=compact_pages(values["pages"])))
            return True
        return run

    tools.append(Tool("ruota-pagine", _("Ruota pagine"),
                      dict(pages_param, angle={"type": "int", "choices": list(ROTATIONS),
                                               "description": "clockwise degrees"}),
                      page_operation(_("Ruota pagine"), True,
                                     lambda pdf, v: page_engine.rotate_pages(pdf, v["pages"], v["angle"]))))
    tools.append(Tool("elimina-pagine", _("Elimina pagine"), dict(pages_param),
                      page_operation(_("Elimina pagine"), False,
                                     lambda pdf, v: page_engine.delete_pages(pdf, v["pages"]))))
    return tools


def compact_pages(indices) -> str:
    """Indici da 0 → «3–5, 8» (numeri da 1)."""
    out, run = [], []
    for i in sorted(set(indices)):
        if run and i == run[-1] + 1:
            run.append(i)
            continue
        if run:
            out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
        run = [i]
    if run:
        out.append(f"{run[0] + 1}–{run[-1] + 1}" if len(run) > 1 else str(run[0] + 1))
    return ", ".join(out)
