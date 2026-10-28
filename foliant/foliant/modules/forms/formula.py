"""Formule personalizzate in notazione semplificata (FRM-17), senza interfaccia.

Espressioni aritmetiche tra campi: ``+ - * /`` (anche ``× ÷ −``), parentesi,
costanti numeriche (punto o virgola decimale), meno unario e precedenza
standard. I nomi dei campi sono identificatori (lettere anche accentate,
cifre, ``_`` e ``.``) oppure, se contengono spazi od operatori, racchiusi tra
virgolette doppie (``"Prezzo unitario"``; ``\\"`` e ``\\\\`` dentro le virgolette).

La formula è memorizzata come script di calcolo equivalente (`to_script`),
preceduto da un commento con l'espressione originale: `from_script` la
riconosce solo se lo script coincide ancora con quello generato, altrimenti lo
script è considerato personalizzato. Nessun uso di eval: l'espressione è
analizzata in un albero e valutata direttamente.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from foliant.i18n import _

from .formats import parse_number

MARKER = "/* foliant-formula: "

TRANSLATIONS: dict[str, str] = {
    "La formula è vuota.": "The formula is empty.",
    "La formula è incompleta.": "The formula is incomplete.",
    "Carattere non valido «{c}».": "Invalid character “{c}”.",
    "Virgolette non chiuse.": "Unclosed quotation marks.",
    "Nome di campo vuoto.": "Empty field name.",
    "Manca la parentesi di chiusura.": "Missing closing parenthesis.",
    "Parentesi di chiusura senza apertura.": "Closing parenthesis without an opening one.",
    "Elemento inatteso «{t}».": "Unexpected element “{t}”.",
    "Numero non valido «{t}».": "Invalid number “{t}”.",
}


class FormulaError(Exception):
    """Errore di sintassi: messaggio per l'utente e posizione (indice del carattere, da 0)."""

    def __init__(self, message: str, position: int):
        super().__init__(message)
        self.message = message
        self.position = position


# ---------------------------------------------------------------------- analisi lessicale
_OPS = {"+": "+", "-": "-", "−": "-", "*": "*", "×": "*", "·": "*", "/": "/", "÷": "/", ":": "/"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?|[.,]\d+")
_IDENT = re.compile(r"[^\W\d][\w.]*")


@dataclass
class _Tok:
    kind: str       # num | name | op | ( | ) | end
    value: Any
    pos: int
    text: str


def _tokens(expr: str) -> list[_Tok]:
    out: list[_Tok] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            out.append(_Tok(c, c, i, c))
            i += 1
            continue
        if c in _OPS:
            out.append(_Tok("op", _OPS[c], i, c))
            i += 1
            continue
        if c == '"':
            j, buf = i + 1, []
            while j < n and expr[j] != '"':
                if expr[j] == "\\" and j + 1 < n:
                    j += 1
                buf.append(expr[j])
                j += 1
            if j >= n:
                raise FormulaError(_("Virgolette non chiuse."), i)
            name = "".join(buf)
            if not name.strip():
                raise FormulaError(_("Nome di campo vuoto."), i)
            out.append(_Tok("name", name, i, expr[i:j + 1]))
            i = j + 1
            continue
        m = _NUMBER.match(expr, i)
        if m:
            text = m.group(0)
            out.append(_Tok("num", float(text.replace(",", ".")), i, text))
            i = m.end()
            continue
        m = _IDENT.match(expr, i)
        if m:
            out.append(_Tok("name", m.group(0), i, m.group(0)))
            i = m.end()
            continue
        raise FormulaError(_("Carattere non valido «{c}».").format(c=c), i)
    out.append(_Tok("end", None, n, ""))
    return out


# ---------------------------------------------------------------------- analisi sintattica
# Nodi: ("num", v) · ("name", n) · ("neg", x) · ("bin", op, a, b)

class _Parser:
    def __init__(self, expr: str):
        self.toks = _tokens(expr)
        self.i = 0

    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def take(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def unexpected(self, t: _Tok) -> FormulaError:
        if t.kind == "end":
            return FormulaError(_("La formula è incompleta."), t.pos)
        if t.kind == ")":
            return FormulaError(_("Parentesi di chiusura senza apertura."), t.pos)
        return FormulaError(_("Elemento inatteso «{t}».").format(t=t.text), t.pos)

    def parse(self):
        if self.tok.kind == "end":
            raise FormulaError(_("La formula è vuota."), 0)
        node = self.expr()
        if self.tok.kind != "end":
            raise self.unexpected(self.tok)
        return node

    def expr(self):
        node = self.term()
        while self.tok.kind == "op" and self.tok.value in "+-":
            op = self.take().value
            node = ("bin", op, node, self.term())
        return node

    def term(self):
        node = self.factor()
        while self.tok.kind == "op" and self.tok.value in "*/":
            op = self.take().value
            node = ("bin", op, node, self.factor())
        return node

    def factor(self):
        if self.tok.kind == "op" and self.tok.value in "+-":
            op = self.take().value
            inner = self.factor()
            return ("neg", inner) if op == "-" else inner
        return self.primary()

    def primary(self):
        t = self.tok
        if t.kind == "num":
            self.take()
            if self.tok.kind == "num":
                raise FormulaError(_("Numero non valido «{t}».").format(t=t.text + self.tok.text), t.pos)
            return ("num", t.value)
        if t.kind == "name":
            self.take()
            return ("name", t.value)
        if t.kind == "(":
            self.take()
            node = self.expr()
            if self.tok.kind != ")":
                if self.tok.kind == "end":
                    raise FormulaError(_("Manca la parentesi di chiusura."), t.pos)
                raise self.unexpected(self.tok)
            self.take()
            return node
        raise self.unexpected(t)


def _collect_names(node, out: list[str]) -> None:
    kind = node[0]
    if kind == "name":
        if node[1] not in out:
            out.append(node[1])
    elif kind == "neg":
        _collect_names(node[1], out)
    elif kind == "bin":
        _collect_names(node[2], out)
        _collect_names(node[3], out)


@dataclass
class Formula:
    expression: str
    names: list[str] = field(default_factory=list)
    tree: tuple | None = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return self.expression


def parse(expr: str) -> Formula:
    """Analizza l'espressione; solleva FormulaError con messaggio e posizione."""
    expr = expr or ""
    tree = _Parser(expr).parse()
    names: list[str] = []
    _collect_names(tree, names)
    return Formula(expr, names, tree)


def _tree(formula: Formula) -> tuple:
    return formula.tree if formula.tree is not None else _Parser(formula.expression).parse()


def _covered(name: str, known: Iterable[str]) -> bool:
    return any(k == name or k.startswith(name + ".") for k in known)


def check(formula: Formula, known_names: set[str]) -> list[str]:
    """Nomi citati che non corrispondono a nessun campo (un nome di gruppo vale se ha discendenti)."""
    return [n for n in formula.names if n not in known_names and not _covered(n, known_names)]


# ---------------------------------------------------------------------- valutazione
class _DivZero(Exception):
    pass


def _number(value: Any) -> float:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    n = parse_number(value)
    return n if n is not None else 0.0


def _eval(node, values: dict[str, Any]) -> float:
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "name":
        return _number(values.get(node[1]))
    if kind == "neg":
        return -_eval(node[1], values)
    _k, op, a, b = node
    x, y = _eval(a, values), _eval(b, values)
    if op == "+":
        return x + y
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if y == 0:
        raise _DivZero
    return x / y


def evaluate(formula: Formula, values: dict[str, Any]) -> float | None:
    """Risultato della formula; None (risultato vuoto) in caso di divisione per zero.
    Valori vuoti o non numerici valgono 0."""
    try:
        result = _eval(_tree(formula), values)
    except (_DivZero, OverflowError):
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result + 0.0 if result != 0 else 0.0


# ---------------------------------------------------------------------- script di calcolo
def _js_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() and abs(v) < 1e15 else repr(float(v))


def _js(node) -> str:
    kind = node[0]
    if kind == "num":
        return _js_num(node[1])
    if kind == "name":
        return "v(" + json.dumps(node[1]) + ")"
    if kind == "neg":
        return "(-" + _js(node[1]) + ")"
    _k, op, a, b = node
    if op == "/":
        return "q(" + _js(a) + ", " + _js(b) + ")"
    return "(" + _js(a) + " " + op + " " + _js(b) + ")"


def _escape_comment(expr: str) -> str:
    return expr.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n").replace("*/", "*\\/")


def _unescape_comment(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "r": "\r"}.get(m.group(1), m.group(1)), text)


def to_script(formula: Formula) -> str:
    """Script di calcolo equivalente per gli altri lettori, preceduto dall'espressione originale."""
    body = _js(_tree(formula))
    return (
        MARKER + _escape_comment(formula.expression) + " */\n"
        "var d = this;\n"
        "var v = function(n){var f = d.getField(n); "
        "var x = f ? Number(String(f.value).replace(/\\s/g, \"\").replace(\",\", \".\")) : 0; "
        "return isNaN(x) ? 0 : x;};\n"
        "var q = function(a, b){if (b === 0) throw \"foliant-div0\"; return a / b;};\n"
        "try { event.value = " + body + "; } catch (e) { event.value = \"\"; }\n"
    )


def from_script(script: str | None) -> Formula | None:
    """Formula riconosciuta dal marcatore; None se assente, non valida o se lo script
    non coincide più con quello generato (script personalizzato)."""
    if not script or not script.startswith(MARKER):
        return None
    end = script.find(" */\n", len(MARKER))
    if end < 0:
        return None
    expr = _unescape_comment(script[len(MARKER):end])
    try:
        formula = parse(expr)
    except FormulaError:
        return None
    if to_script(formula).rstrip() != script.rstrip():
        return None
    return formula


def is_formula_script(script: str | None) -> bool:
    return from_script(script) is not None


# ---------------------------------------------------------------------- dipendenze
def cycle(target: str, formula: Formula | None, deps: dict[str, list[str]],
          all_names: Iterable[str] | None = None) -> list[str] | None:
    """Ciclo di dipendenze che si creerebbe dando a `target` la formula (None se nessuno).

    `deps` associa a ogni campo calcolato i nomi citati. Con `all_names` i nomi di
    gruppo citati dalla formula sono estesi ai discendenti."""
    graph = {k: list(v) for k, v in deps.items()}
    if formula is None:
        graph.pop(target, None)
    else:
        names = list(formula.names)
        if all_names is not None:
            all_names = list(all_names)
            names = [m for n in names for m in ([a for a in all_names if a == n or a.startswith(n + ".")] or [n])]
        graph[target] = names

    def visit(node: str, path: list[str]) -> list[str] | None:
        for d in graph.get(node, []):
            if d == target:
                return path + [d]
            if d in path:
                continue
            found = visit(d, path + [d])
            if found:
                return found
        return None

    return visit(target, [target])
