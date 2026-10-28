"""Campi dinamici e modelli di nome (PAG-32).

Linguaggio comune dei segnaposto usato da numeri di pagina, intestazioni e piè
di pagina, Bates, filigrane e nomi dei file prodotti:

    {pagina} {totale} {etichetta} {data:aaaa-MM-gg} {ora:HH:mm} {nome} {titolo}
    {autore} {oggetto} {bates} {n} {segnalibro} {intervallo} {match1}…
    {pagina:3}        riempimento con zeri
    {titolo|nome}     valore alternativo se il primo è vuoto
    {{ e }}           parentesi letterali
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from foliant.api import _

TRANSLATIONS = {
    "Segnaposto sconosciuto: {{{f}}}.": "Unknown placeholder: {{{f}}}.",
    "Parentesi graffa non chiusa nel modello.": "Unclosed brace in the template.",
    "Parentesi graffa chiusa senza apertura: scrivi «}}» per una parentesi letterale.":
        "Closing brace without an opening one: write «}}» for a literal brace.",
    "Numero di pagina": "Page number",
    "Totale pagine": "Total pages",
    "Etichetta di pagina": "Page label",
    "Data": "Date",
    "Ora": "Time",
    "Nome del file": "File name",
    "Titolo": "Title",
    "Autore": "Author",
    "Oggetto": "Subject",
    "Numero Bates": "Bates number",
    "Numero della parte": "Part number",
    "Segnalibro": "Bookmark",
    "Intervallo di pagine": "Page range",
}

KNOWN = {"pagina", "totale", "etichetta", "data", "ora", "nome", "titolo", "autore", "oggetto", "bates",
         "bates_inizio", "bates_fine", "n", "segnalibro", "intervallo", "inizio", "fine", "match"}
# Alias accettati per compatibilità con modelli precedenti.
ALIASES = {"total": "totale", "label": "etichetta", "date": "data", "file": "nome", "page": "pagina"}

# Voci del menu «Inserisci campo»: (segnaposto, descrizione)
MENU = [("{pagina}", "Numero di pagina"), ("{totale}", "Totale pagine"), ("{etichetta}", "Etichetta di pagina"),
        ("{data:gg/MM/aaaa}", "Data"), ("{ora:HH:mm}", "Ora"), ("{nome}", "Nome del file"),
        ("{titolo}", "Titolo"), ("{autore}", "Autore"), ("{oggetto}", "Oggetto"), ("{bates}", "Numero Bates"),
        ("{n}", "Numero della parte"), ("{segnalibro}", "Segnalibro"), ("{intervallo}", "Intervallo di pagine")]

_TOKEN = re.compile(r"\{\{|\}\}|\{([^{}]*)\}|\{|\}")
# Formato data/ora in stile italiano → strftime
_DATE_CODES = [("aaaa", "%Y"), ("yyyy", "%Y"), ("aa", "%y"), ("yy", "%y"), ("MM", "%m"), ("gg", "%d"),
               ("dd", "%d"), ("HH", "%H"), ("hh", "%H"), ("mm", "%M"), ("ss", "%S")]


def _is_known(name: str, allowed: set[str] | None) -> bool:
    base = ALIASES.get(name, name)
    if allowed is not None:
        return base in allowed or name in allowed or re.fullmatch(r"match\d+", base) is not None
    return base in KNOWN or re.fullmatch(r"match\d+", base) is not None


def _split_field(body: str) -> tuple[list[str], str | None]:
    """«titolo|nome:3» → (["titolo", "nome"], "3")."""
    names, _sep, fmt = body.partition(":")
    return [n.strip() for n in names.split("|")], (fmt if _sep else None)


def validate_template(template: str, allowed: set[str] | None = None) -> str | None:
    """Messaggio d'errore per l'utente, oppure None se il modello è valido."""
    for m in _TOKEN.finditer(template or ""):
        tok = m.group(0)
        if tok in ("{{", "}}"):
            continue
        if tok == "{":
            return _("Parentesi graffa non chiusa nel modello.")
        if tok == "}":
            return _("Parentesi graffa chiusa senza apertura: scrivi «}}» per una parentesi letterale.")
        names, _fmt = _split_field(m.group(1))
        for name in names:
            if not name or not _is_known(name, allowed):
                return _("Segnaposto sconosciuto: {{{f}}}.").format(f=name)
    return None


def format_date(fmt: str | None, when: _dt.datetime) -> str:
    if not fmt:
        return when.strftime("%d/%m/%Y")
    if fmt.upper() == "ISO":
        return when.isoformat(timespec="seconds")
    out = ""
    i = 0
    while i < len(fmt):
        for code, py in _DATE_CODES:
            if fmt.startswith(code, i):
                out += when.strftime(py)
                i += len(code)
                break
        else:
            out += fmt[i]
            i += 1
    return out


def render_template(template: str, values: dict, *, now: _dt.datetime | None = None) -> str:
    """Valuta il modello con i valori dati (chiavi come i segnaposto, es. "pagina").

    Segnaposto mancanti diventano stringa vuota; `{x:3}` riempie un numero con zeri;
    `{data:FORMATO}` e `{ora:FORMATO}` usano il momento `now` (o `values["data"]`)."""
    if now is None:
        now = values["data"] if isinstance(values.get("data"), _dt.datetime) else _dt.datetime.now()

    def value_of(name: str):
        name = ALIASES.get(name, name)
        if name in ("data", "ora"):
            return now
        return values.get(name)

    def sub(m: re.Match) -> str:
        tok = m.group(0)
        if tok == "{{":
            return "{"
        if tok == "}}":
            return "}"
        if m.group(1) is None:
            return tok
        names, fmt = _split_field(m.group(1))
        val = None
        for name in names:
            v = value_of(name)
            if v not in (None, ""):
                val = v
                break
        if isinstance(val, _dt.datetime):
            if fmt is None and ALIASES.get(names[0], names[0]) == "ora":
                return val.strftime("%H:%M")
            return format_date(fmt, val)
        if val is None:
            return ""
        if fmt and fmt.isdigit():
            try:
                return str(int(val)).zfill(int(fmt))
            except (TypeError, ValueError):
                return str(val).zfill(int(fmt))
        return str(val)

    return _TOKEN.sub(sub, template or "")


def document_values(pdf, filename: str | None = None) -> dict:
    """Valori comuni a tutto il documento: nome, titolo, autore, oggetto, totale."""
    meta = {}
    try:
        meta = pdf.metadata or {}
    except Exception:
        pass
    return {"nome": Path(filename).stem if filename else "", "titolo": meta.get("title") or "",
            "autore": meta.get("author") or "", "oggetto": meta.get("subject") or "",
            "totale": pdf.page_count}


def page_values(pdf, index: int, base: dict | None = None, number: int | None = None) -> dict:
    """Valori per la pagina `index` (base 0): numero, etichetta, più quelli del documento."""
    vals = dict(base or {})
    vals["pagina"] = number if number is not None else index + 1
    try:
        vals["etichetta"] = pdf[index].get_label() or str(index + 1)
    except Exception:
        vals["etichetta"] = str(index + 1)
    return vals
