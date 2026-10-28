"""Estrazione di dati strutturati in CSV e JSON (AI-10), senza interfaccia.

Flusso: istruzioni + testo dell'ambito (a blocchi se troppo lungo) → risposta JSON
`{"records": [...]}` → un solo tentativo di correzione se non conforme →
normalizzazione (numeri locali, date ISO, pagine fisiche) → verifica di ogni valore
sul testo d'origine → controlli di coerenza (somma righe / totali).

Formato JSON esportato (`to_json`)::

    {
      "origine": {"documento": "<nome o impronta breve>", "pagine": [1, 2]},   # pagine 1-based
      "data": "2026-10-28T10:15:00",                                           # ora dell'esportazione
      "modello": "<modello>",
      "tipo": "tables" | "invoice" | "custom",
      "controlli": ["<controlli non superati>"],
      "record": [
        {"campo": {"valore": 12.5, "pagina": 1, "verificato": true},
         "righe": [{"descrizione": {"valore": "...", "pagina": 1, "verificato": true}}]}
      ]
    }

CSV (`to_csv`): RFC 4180 (intestazione, virgolette quando servono, fine riga CRLF);
le liste annidate (righe della fattura) diventano una riga CSV per elemento con i campi
del record ripetuti, colonne `righe.<campo>`; la colonna `pagina_origine` è 1-based.
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from foliant.api import FoliantError, OperationCancelled, _

from . import chunking, prompts
from .engine import Job, Scope, _system, budget_for, parse_json_payload, scope_text
from .providers import ChatRequest, Message

KINDS = ("tables", "invoice", "custom")
PAGE_KEY = "_page"
PAGE_COLUMN = "pagina_origine"

INVOICE_FIELDS = ["numero", "data", "fornitore", "partita_iva", "cliente", "righe", "imponibile", "imposta",
                  "totale", "valuta"]
INVOICE_LINE_FIELDS = ["descrizione", "quantita", "prezzo_unitario", "aliquota", "importo"]
INVOICE_TYPES = {"data": "date", "imponibile": "number", "imposta": "number", "totale": "number",
                 "quantita": "number", "prezzo_unitario": "number", "aliquota": "number", "importo": "number"}

_NUMBER_TYPES = {"number", "numero", "num", "float", "int", "integer", "decimal", "importo", "currency", "valuta"}
_DATE_TYPES = {"date", "data", "datetime"}

FIX_JSON = ("Your previous reply was not valid. Reply again with ONLY valid JSON of the form "
            '{"records": [...]} following exactly the schema in the instructions: no prose, no comments, '
            "no Markdown.")


class ExtractError(FoliantError):
    """Risposta non conforme anche dopo il tentativo di correzione; `raw` = testo ricevuto."""

    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


class NoData(FoliantError):
    def __init__(self, message: str | None = None):
        super().__init__(message or _("Nessun dato trovato nel contenuto scelto: non è stato creato alcun file."))


@dataclass
class Cell:
    value: object
    page: int | None = None          # indice fisico (0-based)
    verified: bool = True


@dataclass
class ExtractResult:
    kind: str
    records: list[dict]               # campo -> Cell oppure lista di dict campo -> Cell
    columns: list[str]
    checks: list[str] = field(default_factory=list)
    raw: str = ""
    model: str = ""
    pages: list[int] = field(default_factory=list)
    source: str = ""                  # nome del documento (o impronta breve)
    requests: int = 0


# ================================================================== normalizzazione
_CURRENCY = re.compile(r"[€$£¥]|\b(?:EUR|USD|GBP|CHF)\b", re.I)
_LOCAL_NUMBER = re.compile(
    r"^[+-]?(?:\d{1,3}(?:\.\d{3})+,\d+|\d+,\d+|\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:\.\d{3}){2,}|\d{1,3}(?:,\d{3}){2,})$")


def parse_number(text) -> float | None:
    """'1.234,56' / '1,234.56' / '€ 12,50' / '1 234,5' / '22%' → float; None se non è un numero.

    Con un solo tipo di separatore: la virgola singola è decimale (formato locale), il punto
    singolo è decimale (formato del modello); separatori ripetuti indicano le migliaia.
    """
    if isinstance(text, bool) or text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = _CURRENCY.sub("", str(text)).strip().replace(" ", " ").replace(" ", " ")
    s = s.rstrip("%").strip()
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1].strip()
    if s.endswith("-") and not s.startswith("-"):
        neg, s = True, s[:-1].strip()
    if s and s[0] in "+-":
        neg, s = (s[0] == "-") != neg, s[1:].strip()
    s = s.replace("'", "").replace("’", "")
    if re.fullmatch(r"\d{1,3}(?: \d{3})+(?:[.,]\d+)?", s):
        s = s.replace(" ", "")
    if not s or not re.fullmatch(r"[\d.,]+", s) or not s[0].isdigit() and s[0] not in ".,":
        return None
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        head, _sep, tail = s.rpartition(dec)
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(thou) + r"\d{3})*", head):
            return None
        s = head.replace(thou, "") + "." + tail
    elif s.count(",") > 1 or s.count(".") > 1:
        sep = "," if "," in s else "."
        if not re.fullmatch(r"\d{1,3}(?:" + re.escape(sep) + r"\d{3})+", s):
            return None
        s = s.replace(sep, "")
    else:
        s = s.replace(",", ".")
    try:
        v = float(Decimal(s))
    except (InvalidOperation, ValueError):
        return None
    return -v if neg else v


_MONTHS = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6, "luglio": 7, "agosto": 8,
    "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return _dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(text, assume_day_first: bool = True) -> str | None:
    """Data → 'YYYY-MM-DD' se non ambigua. gg/mm/aaaa (anche con . o -) è giorno-mese;
    con `assume_day_first=False` le date con giorno e mese entrambi ≤ 12 restano non convertite."""
    if not isinstance(text, str):
        return None
    s = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", s)
    if m:
        return _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 or (assume_day_first and b <= 12):
            return _iso(y, b, a)
        if b > 12:
            return _iso(y, a, b)
        return None
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
    if m and m.group(2).lower() in _MONTHS:
        return _iso(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    return None


def _norm_value(value, ftype: str | None):
    """Normalizza un valore restituito dal modello secondo il tipo del campo (None = sconosciuto)."""
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    t = (ftype or "").lower()
    if t in _NUMBER_TYPES:
        n = parse_number(value)
        return n if n is not None else value
    if t in _DATE_TYPES:
        return parse_date(value) or value
    if ftype is None and isinstance(value, str):
        stripped = _CURRENCY.sub("", value).strip()
        if _LOCAL_NUMBER.match(stripped) or (stripped != value and parse_number(value) is not None
                                              and re.fullmatch(r"[\d.,\s'+-]+", stripped)):
            n = parse_number(value)
            if n is not None:
                return n
        d = parse_date(value, assume_day_first=False)
        if d:
            return d
    return value


# ================================================================== verifica
_NUM_RE = re.compile(r"(?<![\d])[+-]?\d[\d.,']*\d|(?<![\d.,])\d(?![\d])")
_SPACED_RE = re.compile(r"\d{1,3}(?: \d{3})+(?:,\d+)?")
_DATE_NUM_RE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})")
_DATE_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DATE_WORD_RE = re.compile(r"(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(\d{4})")
_DATE_EN_RE = re.compile(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})")


def _key(v: float) -> str:
    return f"{round(v, 4):.4f}"


def _number_candidates(tok: str) -> set[str]:
    """Tutte le letture plausibili di un numero scritto nel testo (virgola o punto decimali)."""
    out = set()
    tok = tok.strip("+-")
    v = parse_number(tok)
    if v is not None:
        out.add(_key(v))
    for dec in (",", "."):
        other = "." if dec == "," else ","
        if tok.count(dec) == 1:
            head, tail = tok.split(dec)
            head = head.replace(other, "").replace("'", "")
            if head.isdigit() and tail.isdigit():
                out.add(_key(float(f"{head}.{tail}")))
        plain = tok.replace(other, "").replace("'", "")
        if plain.replace(dec, "").isdigit() and dec not in plain:
            out.add(_key(float(plain)))
    digits = tok.replace(".", "").replace(",", "").replace("'", "")
    if digits.isdigit() and digits:
        out.add(_key(float(digits)))
    return out


def _normtext(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class _Source:
    text: str
    numbers: set[str]
    dates: set[str]


def _source(text: str) -> _Source:
    nums: set[str] = set()
    for m in _NUM_RE.finditer(text):
        tok = m.group(0)
        nums |= _number_candidates(tok)
        if parse_number(tok) is None:            # date, codici: anche le singole parti
            for part in re.split(r"[.,'+-]", tok):
                if part.isdigit():
                    nums.add(_key(float(part)))
    for m in _SPACED_RE.finditer(text):
        nums |= _number_candidates(m.group(0).replace(" ", ""))
    dates: set[str] = set()
    for m in _DATE_NUM_RE.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 70 else 1900
        for d in (_iso(y, b, a), _iso(y, a, b)):
            if d:
                dates.add(d)
    for m in _DATE_ISO_RE.finditer(text):
        d = _iso(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            dates.add(d)
    for m in _DATE_WORD_RE.finditer(text):
        mo = _MONTHS.get(m.group(2).lower())
        if mo:
            d = _iso(int(m.group(3)), mo, int(m.group(1)))
            if d:
                dates.add(d)
    for m in _DATE_EN_RE.finditer(text):
        mo = _MONTHS.get(m.group(1).lower())
        if mo:
            d = _iso(int(m.group(3)), mo, int(m.group(2)))
            if d:
                dates.add(d)
    return _Source(_normtext(text), nums, dates)


def value_in_text(value, source: _Source) -> bool:
    """Il valore compare nel testo d'origine? Confronto tra forme normalizzate."""
    if value is None or value == "":
        return True
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return _key(float(value)) in source.numbers
    s = str(value)
    iso = parse_date(s) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s.strip()) else None
    if iso and iso in source.dates:
        return True
    n = _normtext(s)
    if n and n in source.text:
        return True
    num = parse_number(s)
    if num is not None and re.fullmatch(r"[\d\s.,'€$£%+-]+", s):
        return _key(num) in source.numbers
    # testo spezzato diversamente: confronto senza spazi né punteggiatura
    squash = re.sub(r"[\W_]+", "", n)
    return bool(squash) and squash in re.sub(r"[\W_]+", "", source.text)


# ================================================================== richiesta
def _instructions(kind: str, fields: list[dict] | None) -> str:
    if kind == "tables":
        return prompts.EXTRACT_TABLES
    if kind == "invoice":
        return prompts.EXTRACT_INVOICE
    if kind == "custom":
        if not fields:
            raise FoliantError(_("Definisci almeno un campo da estrarre."))
        return prompts.extract_custom(fields)
    raise FoliantError(_("Tipo di estrazione sconosciuto: {k}").format(k=kind))


def _conforming(text: str) -> list | None:
    """Record della risposta se conforme ({"records": [...]}), altrimenti None."""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    data = None
    if start != -1 and end > start:
        try:
            data = json.loads(t[start:end + 1])
        except ValueError:
            data = None
    if data is None:
        # parse_json_payload privilegia gli array: un elenco di oggetti vale come elenco di record
        try:
            data = parse_json_payload(text)
        except ValueError:
            return None
        if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
            return data
        return None
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return None
    return [r for r in data["records"] if isinstance(r, dict)]


def _ask(job: Job, scope: Scope, system: str, body: str, out_tokens: int, pages: list[int], profile,
         counter: list[int]) -> tuple[list[dict], str]:
    """Una richiesta + al massimo un tentativo di correzione. Restituisce (record grezzi, testo)."""
    user = Message("user", f"<document>\n{body}\n</document>")
    messages = [user]
    raw = ""
    for attempt in range(2):
        req = ChatRequest(system, messages, out_tokens)
        counter[0] += 1
        r = job.gateway.send(req, function="extract", pages=pages, fingerprint=scope.fingerprint,
                             cancel=job.cancel, on_wait=job.on_wait, profile=profile)
        if r.interrupted:
            raise OperationCancelled()
        raw = r.text
        records = _conforming(raw)
        if records is not None:
            return records, raw
        messages = [user, Message("assistant", raw or "(empty)"), Message("user", FIX_JSON)]
    raise ExtractError(_("La risposta del modello non è conforme allo schema richiesto, nemmeno dopo un "
                         "tentativo di correzione. Puoi consultare la risposta grezza."), raw)


def _is_header_row(rec: dict) -> bool:
    items = [(k, v) for k, v in rec.items() if k != PAGE_KEY and not isinstance(v, list)]
    return bool(items) and all(isinstance(v, str) and _normtext(v) == _normtext(k) for k, v in items)


def _same(a: dict, b: dict) -> bool:
    strip = lambda d: {k: v for k, v in d.items() if k != PAGE_KEY}  # noqa: E731
    return strip(a) == strip(b)


def extract_data(job: Job, scope: Scope, kind: str, fields: list[dict] | None = None, *,
                 source_name: str = "") -> ExtractResult:
    """Estrae tabelle, dati di fattura o campi personalizzati dall'ambito (AI-10).

    Solleva `ExtractError` (con `.raw`) se la risposta non è conforme dopo un tentativo di
    correzione, `NoData` se non c'è alcun record.
    """
    profile = job.profile()
    system = _system(prompts.BASE, job.language) + " " + _instructions(kind, fields)
    budget, out_tokens = budget_for(profile, system)
    text = scope_text(scope)
    if not text.strip():
        raise NoData()
    counter = [0]
    raws: list[str] = []
    raw_records: list[dict] = []
    if len(text) > budget and scope.pages:
        chunks = chunking.make_chunks(scope.pages, budget)
        for n, ch in enumerate(chunks):
            job.step(n / max(len(chunks), 1), _("Blocco {i} di {n}").format(i=n + 1, n=len(chunks)))
            recs, raw = _ask(job, scope, system, ch.text, out_tokens, ch.pages, profile, counter)
            raws.append(raw)
            if raw_records and recs and _same(raw_records[-1], recs[0]):
                recs = recs[1:]          # riga ripetuta per la sovrapposizione dei blocchi
            raw_records.extend(recs)
    else:
        recs, raw = _ask(job, scope, system, text, out_tokens, scope.page_indices, profile, counter)
        raws.append(raw)
        raw_records = recs
    raw_records = [r for r in raw_records if not _is_header_row(r)]
    raw_records = [r for r in raw_records if any(v not in (None, "", []) for k, v in r.items() if k != PAGE_KEY)]
    if not raw_records:
        raise NoData()
    result = build_result(kind, raw_records, scope, fields)
    result.raw = "\n\n".join(raws)
    result.model = profile.model
    result.source = source_name or scope.doc_name or (scope.fingerprint[:12] if scope.fingerprint else "")
    result.requests = counter[0]
    if not any(_has_value(r) for r in result.records):
        raise NoData()
    return result


def _has_value(rec: dict) -> bool:
    for v in rec.values():
        if isinstance(v, list):
            if any(_has_value(x) for x in v):
                return True
        elif v.value not in (None, ""):
            return True
    return False


# ================================================================== costruzione del risultato
def _page_texts(scope: Scope) -> dict[int, str]:
    out = {p.index: p.text for p in scope.pages}
    if scope.selection and scope.selection_page is not None:
        out.setdefault(scope.selection_page, scope.selection)
    return out


def _resolve_page(scope: Scope, label) -> int | None:
    if label is None or isinstance(label, bool):
        return None
    if isinstance(label, float) and label.is_integer():
        label = int(label)
    lab = str(label).strip()
    lab = re.sub(r"^(?:p\.?|pag\.?|pagina|page)\s*", "", lab, flags=re.I)
    if scope.space is not None and lab:
        idx = scope.space.resolve(lab)
        if idx is not None:
            return idx
    return None


def build_result(kind: str, raw_records: list[dict], scope: Scope, fields: list[dict] | None = None) -> ExtractResult:
    """Normalizza e verifica i record grezzi del modello (utile anche per i test)."""
    types: dict[str, str | None] = {}
    if kind == "invoice":
        types = dict(INVOICE_TYPES)
    elif kind == "custom" and fields:
        types = {f["name"]: f.get("type", "text") for f in fields}
    texts = _page_texts(scope)
    sources: dict[int, _Source] = {i: _source(t) for i, t in texts.items()}
    whole = _source("\n".join(texts.values()) or scope.selection)
    only_page = scope.page_indices[0] if len(scope.page_indices) == 1 else None

    def cell(key: str, value, page: int | None) -> Cell:
        v = _norm_value(value, types.get(key) if types else None)
        src = sources.get(page) if page is not None else None
        return Cell(v, page, value_in_text(v, src or whole))

    def convert(rec: dict, parent_page: int | None) -> dict:
        page = _resolve_page(scope, rec.get(PAGE_KEY))
        if page is None:
            page = parent_page if parent_page is not None else only_page
        out: dict = {}
        for k, v in rec.items():
            if k == PAGE_KEY:
                continue
            if isinstance(v, list) and all(isinstance(x, dict) for x in v) and v:
                out[k] = [convert(x, page) for x in v]
            elif isinstance(v, list):
                out[k] = cell(k, "; ".join(str(x) for x in v) if v else None, page)
            elif isinstance(v, dict):
                out[k] = cell(k, json.dumps(v, ensure_ascii=False), page)
            else:
                out[k] = cell(k, v, page)
        return out

    records = [convert(r, None) for r in raw_records]
    columns: list[str] = []
    if kind == "invoice":
        columns = list(INVOICE_FIELDS)
    elif kind == "custom" and fields:
        columns = [f["name"] for f in fields]
    for r in records:
        for k in r:
            if k not in columns:
                columns.append(k)
    if kind == "invoice":
        for r in records:
            r.setdefault("righe", [])
    result = ExtractResult(kind, records, columns, pages=list(scope.page_indices))
    result.checks = consistency_checks(result)
    return result


def _num(c) -> float | None:
    if not isinstance(c, Cell):
        return None
    if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
        return float(c.value)
    return parse_number(c.value) if isinstance(c.value, str) else None


def _fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def partita_iva_valid(value) -> bool:
    """Partita IVA italiana: 11 cifre con cifra di controllo (prefisso «IT» ammesso)."""
    v = str(value or "").strip().upper().replace(" ", "")
    if v.startswith("IT"):
        v = v[2:]
    if len(v) != 11 or not v.isdigit():
        return False
    total = 0
    for i, ch in enumerate(v[:10]):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(v[10])


_CF_ODD = {**{str(i): v for i, v in enumerate([1, 0, 5, 7, 9, 13, 15, 17, 19, 21])},
           **dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                      [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))}


def codice_fiscale_valid(value) -> bool:
    """Codice fiscale di persona fisica: 16 caratteri con carattere di controllo."""
    v = str(value or "").strip().upper().replace(" ", "")
    if len(v) != 16 or not v.isalnum():
        return False
    total = 0
    for i, ch in enumerate(v[:15]):
        if i % 2 == 0:
            total += _CF_ODD[ch]
        else:
            total += int(ch) if ch.isdigit() else ord(ch) - ord("A")
    return chr(ord("A") + total % 26) == v[15]


def tax_id_valid(value) -> bool:
    v = str(value or "").strip().upper().replace(" ", "")
    if not v:
        return True
    digits = v[2:] if v.startswith("IT") else v
    if digits.isdigit():
        return partita_iva_valid(v)
    return codice_fiscale_valid(v)


def consistency_checks(result: ExtractResult) -> list[str]:
    """Controlli di coerenza delle fatture: somma righe, imponibile + imposta = totale."""
    checks: list[str] = []
    if result.kind != "invoice":
        return checks
    for n, rec in enumerate(result.records):
        name = _("Fattura {n}").format(n=rec["numero"].value) if isinstance(rec.get("numero"), Cell) and \
            rec["numero"].value not in (None, "") else _("Record {n}").format(n=n + 1)
        lines = rec.get("righe") if isinstance(rec.get("righe"), list) else []
        amounts = [_num(x.get("importo")) for x in lines]
        amounts = [a for a in amounts if a is not None]
        net, tax, total = _num(rec.get("imponibile")), _num(rec.get("imposta")), _num(rec.get("totale"))
        if amounts:
            s = sum(amounts)
            if net is not None:
                if abs(s - net) > 0.01:
                    checks.append(_("{r}: la somma delle righe ({s}) è diversa dall'imponibile ({t}): "
                                    "differenza {d}").format(r=name, s=_fmt(s), t=_fmt(net),
                                                            d=f"{abs(s - net):.2f}".replace(".", ",")))
            elif total is not None:
                expected = s + (tax or 0.0)
                if abs(expected - total) > 0.01:
                    checks.append(_("{r}: la somma delle righe ({s}) non corrisponde al totale ({t})")
                                  .format(r=name, s=_fmt(expected), t=_fmt(total)))
        for key in ("partita_iva", "codice_fiscale", "partita_iva_cliente"):
            cell = rec.get(key)
            if isinstance(cell, Cell) and cell.value not in (None, "") and not tax_id_valid(cell.value):
                checks.append(_("{r}: «{v}» non è formalmente valido ({k})").format(
                    r=name, v=cell.value, k=key.replace("_", " ")))
        if net is not None and tax is not None and total is not None and abs(net + tax - total) > 0.01:
            checks.append(_("{r}: imponibile + imposta ({s}) è diverso dal totale ({t})")
                          .format(r=name, s=_fmt(net + tax), t=_fmt(total)))
    return checks


# ================================================================== vista piatta (griglia e CSV)
def nested_columns(result: ExtractResult) -> dict[str, list[str]]:
    """Per ogni campo che contiene una lista di record: i suoi campi in ordine."""
    out: dict[str, list[str]] = {}
    for col in result.columns:
        sub: list[str] = list(INVOICE_LINE_FIELDS) if (result.kind == "invoice" and col == "righe") else []
        nested = False
        for r in result.records:
            v = r.get(col)
            if isinstance(v, list):
                nested = True
                for x in v:
                    for k in x:
                        if k not in sub:
                            sub.append(k)
        if nested or sub:
            out[col] = sub
    return out


def flat_columns(result: ExtractResult) -> list[str]:
    nested = nested_columns(result)
    cols: list[str] = []
    for c in result.columns:
        if c in nested:
            cols.extend(f"{c}.{s}" for s in nested[c])
        else:
            cols.append(c)
    return cols


def flat_rows(result: ExtractResult) -> list[tuple[int, str | None, int | None, dict[str, Cell]]]:
    """Righe della griglia: (indice record, campo annidato, indice riga annidata, colonna -> Cell)."""
    nested = nested_columns(result)
    rows = []
    for ri, rec in enumerate(result.records):
        base = {c: v for c, v in rec.items() if isinstance(v, Cell)}
        list_col = next((c for c in nested if isinstance(rec.get(c), list) and rec.get(c)), None)
        if list_col is None:
            rows.append((ri, None, None, base))
            continue
        for li, line in enumerate(rec[list_col]):
            row = dict(base)
            for k, v in line.items():
                if isinstance(v, Cell):
                    row[f"{list_col}.{k}"] = v
            rows.append((ri, list_col, li, row))
    return rows


def format_value(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v)


def set_value(result: ExtractResult, record: int, column: str, line: int | None, text: str) -> Cell:
    """Modifica dall'utente: il valore diventa verificato (scelto dall'utente)."""
    rec = result.records[record]
    if "." in column and line is not None:
        parent, sub = column.split(".", 1)
        target = rec[parent][line]
        key = sub
    else:
        target, key = rec, column
    old = target.get(key)
    page = old.page if isinstance(old, Cell) else None
    ftype = INVOICE_TYPES.get(key) if result.kind == "invoice" else None
    if isinstance(old, Cell) and isinstance(old.value, (int, float)) and not isinstance(old.value, bool):
        ftype = "number"
    value = _norm_value(text, ftype) if ftype else (text if text != "" else None)
    c = Cell(value, page, True)
    target[key] = c
    result.checks = consistency_checks(result)
    return c


# ================================================================== esportazione
def to_csv(result: ExtractResult, sep: str = ",", *, page_column: bool = True) -> str:
    """CSV RFC 4180 (testo; salvare in UTF-8)."""
    cols = flat_columns(result)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cols + ([PAGE_COLUMN] if page_column else []))
    for _ri, list_col, _li, row in flat_rows(result):
        values = [format_value(row[c].value) if c in row else "" for c in cols]
        if page_column:
            pages = [c.page for k, c in row.items() if (list_col is None or k.startswith(list_col + "."))
                     and c.page is not None]
            if not pages:
                pages = [c.page for c in row.values() if c.page is not None]
            values.append(str(pages[0] + 1) if pages else "")
        w.writerow(values)
    return buf.getvalue()


def _cell_json(c: Cell) -> dict:
    return {"valore": c.value, "pagina": c.page + 1 if c.page is not None else None, "verificato": c.verified}


def to_json(result: ExtractResult, source_name: str = "", *, now: _dt.datetime | None = None) -> str:
    def rec(r: dict) -> dict:
        out = {}
        for k, v in r.items():
            out[k] = [rec(x) for x in v] if isinstance(v, list) else _cell_json(v)
        return out

    data = {
        "origine": {"documento": source_name or result.source, "pagine": [p + 1 for p in result.pages]},
        "data": (now or _dt.datetime.now()).isoformat(timespec="seconds"),
        "modello": result.model,
        "tipo": result.kind,
        "controlli": list(result.checks),
        "record": [rec(r) for r in result.records],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


# ================================================================== fattura elettronica allegata (AI-29)
def embedded_einvoices(pdf) -> list[dict]:
    """Record grezzi delle fatture elettroniche XML (FatturaPA) allegate al PDF, letti in locale.

    Restituisce [] se non ci sono allegati XML di fattura o se non sono leggibili.
    """
    import xml.etree.ElementTree as ET
    from foliant.api import ENGINE_LOCK
    out: list[dict] = []
    with ENGINE_LOCK:
        try:
            names = list(pdf.embfile_names())
        except Exception:  # noqa: BLE001
            return out
        blobs = []
        for n in names:
            if not n.lower().endswith(".xml"):
                continue
            try:
                blobs.append(pdf.embfile_get(n))
            except Exception:  # noqa: BLE001
                continue
    for data in blobs:
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        if not root.tag.split("}")[-1].startswith("FatturaElettronica"):
            continue

        def find(node, path):
            cur = [node]
            for part in path.split("/"):
                nxt = []
                for c in cur:
                    nxt.extend(ch for ch in c if ch.tag.split("}")[-1] == part)
                cur = nxt
            return cur

        def text(node, path):
            found = find(node, path)
            return (found[0].text or "").strip() if found else ""

        header = find(root, "FatturaElettronicaHeader")
        head = header[0] if header else root
        seller = text(head, "CedentePrestatore/DatiAnagrafici/Anagrafica/Denominazione") or " ".join(
            filter(None, [text(head, "CedentePrestatore/DatiAnagrafici/Anagrafica/Nome"),
                          text(head, "CedentePrestatore/DatiAnagrafici/Anagrafica/Cognome")]))
        piva = text(head, "CedentePrestatore/DatiAnagrafici/IdFiscaleIVA/IdCodice")
        buyer = text(head, "CessionarioCommittente/DatiAnagrafici/Anagrafica/Denominazione") or " ".join(
            filter(None, [text(head, "CessionarioCommittente/DatiAnagrafici/Anagrafica/Nome"),
                          text(head, "CessionarioCommittente/DatiAnagrafici/Anagrafica/Cognome")]))
        for body in find(root, "FatturaElettronicaBody"):
            gen = "DatiGenerali/DatiGeneraliDocumento/"
            lines = [{"descrizione": text(l, "Descrizione"), "quantita": text(l, "Quantita") or None,
                      "prezzo_unitario": text(l, "PrezzoUnitario") or None,
                      "aliquota": text(l, "AliquotaIVA") or None, "importo": text(l, "PrezzoTotale") or None}
                     for l in find(body, "DatiBeniServizi/DettaglioLinee")]
            summaries = find(body, "DatiBeniServizi/DatiRiepilogo")
            net = sum(float(text(s, "ImponibileImporto") or 0) for s in summaries) if summaries else None
            tax = sum(float(text(s, "Imposta") or 0) for s in summaries) if summaries else None
            total = text(body, gen + "ImportoTotaleDocumento")
            out.append({"numero": text(body, gen + "Numero"), "data": text(body, gen + "Data"),
                        "fornitore": seller, "partita_iva": piva, "cliente": buyer, "righe": lines,
                        "imponibile": net, "imposta": tax, "totale": float(total) if total else
                        (net or 0) + (tax or 0), "valuta": text(body, gen + "Divisa") or "EUR"})
    return out


# ================================================================== più file (AI-29)
FILE_COLUMN = "file"


def merge_results(named: list[tuple[str, ExtractResult]]) -> ExtractResult:
    """Unisce i risultati di più file aggiungendo a ogni record il file d'origine.

    Il nome del file è solo locale (griglia ed esportazioni): non viene mai inviato al fornitore.
    """
    if not named:
        raise NoData()
    kind = named[0][1].kind
    columns: list[str] = [FILE_COLUMN]
    records: list[dict] = []
    checks: list[str] = []
    for name, res in named:
        for c in res.columns:
            if c not in columns:
                columns.append(c)
        for rec in res.records:
            merged = {FILE_COLUMN: Cell(name, None, True)}
            merged.update(rec)
            records.append(merged)
        checks.extend(f"{name}: {c}" for c in res.checks)
    out = ExtractResult(kind, records, columns, checks, "", named[0][1].model, [],
                        ", ".join(n for n, _r in named), sum(r.requests for _n, r in named))
    return out


def to_csv_summary(result: ExtractResult, sep: str = ",") -> str:
    """Tabella riepilogativa: una riga per record (fattura), senza le righe di dettaglio."""
    cols = [c for c in result.columns if not any(isinstance(r.get(c), list) for r in result.records)]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, quotechar='"', quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cols)
    for rec in result.records:
        w.writerow([format_value(rec[c].value) if isinstance(rec.get(c), Cell) else "" for c in cols])
    return buf.getvalue()
