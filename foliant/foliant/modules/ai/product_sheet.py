"""Schede prodotto (AI-30): sezioni del modello e specifiche tecniche con la pagina d'origine."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from foliant.api import FoliantError, _

from . import engine, extract, prompts
from .providers import ChatRequest, Message

SHORT_LIMIT = 300


@dataclass
class Spec:
    nome: str
    valore: object
    unita: str = ""
    pagina: int | None = None       # 0-based
    verified: bool = True


@dataclass
class ProductSheet:
    nome: str = ""
    descrizione_breve: str = ""
    descrizione: str = ""
    caratteristiche: list[str] = field(default_factory=list)
    specifiche: list[Spec] = field(default_factory=list)
    vantaggi: list[str] = field(default_factory=list)
    parole_chiave: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _instructions(language: str, tone: str, length_words: int) -> str:
    return ("Build a product sheet from the provided technical or commercial content, in a "
            f"{'technical' if tone == 'tecnico' else 'commercial'} tone, extended description of about "
            f"{int(length_words)} words. Use only information present in the content. Reply ONLY with JSON: "
            '{"nome": str, "descrizione_breve": str (max 300 characters), "descrizione": str, '
            '"caratteristiche": [str], "specifiche": [{"nome": str, "valore": number or str, "unita": str, '
            '"pagina": page label}], "vantaggi": [str], "parole_chiave": [str]}.')


def _page_text(scope: engine.Scope, page: int | None) -> str:
    if page is not None:
        for p in scope.pages:
            if p.index == page:
                return p.text
    return "\n".join(p.text for p in scope.pages)


def build_sheet(data: dict, scope: engine.Scope) -> ProductSheet:
    sheet = ProductSheet(
        nome=str(data.get("nome", "")).strip(),
        descrizione_breve=str(data.get("descrizione_breve", "")).strip(),
        descrizione=str(data.get("descrizione", "")).strip(),
        caratteristiche=[str(x) for x in data.get("caratteristiche") or []],
        vantaggi=[str(x) for x in data.get("vantaggi") or []],
        parole_chiave=[str(x) for x in data.get("parole_chiave") or []],
    )
    units: dict[str, set[str]] = {}
    for item in data.get("specifiche") or []:
        if not isinstance(item, dict) or not item.get("nome"):
            continue
        raw = item.get("valore")
        num = extract.parse_number(raw) if not isinstance(raw, (int, float)) else float(raw)
        value = num if num is not None else str(raw or "")
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        label = item.get("pagina")
        page = scope.space.resolve(str(label)) if scope.space is not None and label not in (None, "") else None
        unit = str(item.get("unita") or "").strip()
        source = extract._source(_page_text(scope, page))
        verified = extract.value_in_text(value, source)
        sheet.specifiche.append(Spec(str(item["nome"]).strip(), value, unit, page, verified))
        units.setdefault(str(item["nome"]).strip().lower(), set()).add(unit)
    if len(sheet.descrizione_breve) > SHORT_LIMIT:
        sheet.warnings.append(_("La descrizione breve supera il limite di {l} caratteri ({n} caratteri).").format(
            l=SHORT_LIMIT, n=len(sheet.descrizione_breve)))
    for name, us in units.items():
        if len({u for u in us if u}) > 1:
            sheet.warnings.append(_("Unità diverse per «{n}»: {u}.").format(n=name, u=", ".join(sorted(us))))
    if not sheet.specifiche:
        sheet.warnings.append(_("Il documento non contiene dati tecnici: la scheda ha solo le sezioni descrittive."))
    return sheet


def make_product_sheet(job: engine.Job, scope: engine.Scope, language: str = "it", tone: str = "tecnico",
                       length_words: int = 150) -> ProductSheet:
    profile = job.profile()
    system = engine._system(prompts.BASE, language) + " " + _instructions(language, tone, length_words)
    budget, out_tokens = engine.budget_for(profile, system)
    body = engine.scope_text(scope)[:budget]
    data = None
    for attempt in range(2):
        text = f"<document>\n{body}\n</document>"
        if attempt:
            text += "\n\nReply ONLY with valid JSON as specified."
        r = job.gateway.send(ChatRequest(system, [Message("user", text)], out_tokens), function="product-sheet",
                             pages=scope.page_indices, fingerprint=scope.fingerprint, cancel=job.cancel,
                             on_wait=job.on_wait, profile=profile)
        try:
            data = engine.parse_json_payload(r.text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            break
    if not isinstance(data, dict):
        raise FoliantError(_("La risposta del modello non ha la struttura attesa."))
    return build_sheet(data, scope)


def specs_to_csv(sheet: ProductSheet, sep: str = ",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\r\n")
    w.writerow([_("nome"), _("valore"), _("unità"), _("pagina")])
    for s in sheet.specifiche:
        w.writerow([s.nome, extract.format_value(s.valore), s.unita, s.pagina + 1 if s.pagina is not None else ""])
    return buf.getvalue()


def to_markdown(sheet: ProductSheet) -> str:
    lines = [f"# {sheet.nome}" if sheet.nome else "", "", sheet.descrizione_breve, "", sheet.descrizione, ""]
    if sheet.caratteristiche:
        lines += ["## " + _("Caratteristiche principali")] + [f"- {c}" for c in sheet.caratteristiche] + [""]
    if sheet.specifiche:
        lines += ["## " + _("Specifiche tecniche"), "",
                  f"| {_('Specifica')} | {_('Valore')} | {_('Unità')} | {_('Pagina')} |", "|---|---|---|---|"]
        for s in sheet.specifiche:
            mark = "" if s.verified else " (" + _("non verificato") + ")"
            page = f"[p. {s.pagina + 1}]" if s.pagina is not None else ""
            lines.append(f"| {s.nome} | {extract.format_value(s.valore)}{mark} | {s.unita} | {page} |")
        lines.append("")
    if sheet.vantaggi:
        lines += ["## " + _("Vantaggi per il cliente")] + [f"- {v}" for v in sheet.vantaggi] + [""]
    if sheet.parole_chiave:
        lines += ["## " + _("Parole chiave"), ", ".join(sheet.parole_chiave)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
