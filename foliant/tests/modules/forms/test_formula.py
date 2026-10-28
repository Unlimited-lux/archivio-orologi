"""Formule personalizzate in notazione semplificata (FRM-17)."""

from __future__ import annotations

import inspect
import re

import pytest

from foliant.modules.forms import formula as F


def ev(expr, **values):
    return F.evaluate(F.parse(expr), values)


def test_acceptance_total():
    f = F.parse("Imponibile * 1.22 - Sconto")
    assert f.names == ["Imponibile", "Sconto"]
    assert F.evaluate(f, {"Imponibile": "100", "Sconto": "10"}) == pytest.approx(112)


def test_division_by_zero_is_empty():
    assert ev("(a + b) / c", a=1, b=2, c=0) is None
    assert ev("(a + b) / c", a=1, b=2, c="") is None
    assert ev("(a + b) / c", a=1, b=2, c=3) == pytest.approx(1)


@pytest.mark.parametrize("expr,result", [
    ("1 + 2 * 3", 7),
    ("(1 + 2) * 3", 9),
    ("10 - 4 - 3", 3),
    ("24 / 4 / 2", 3),
    ("-2 * 3", -6),
    ("- (2 + 3) * 2", -10),
    ("2 * -3", -6),
    ("--4", 4),
    ("+5 - -5", 10),
    ("6 ÷ 3 × 2", 4),
    ("7 − 2", 5),
    ("1,5*2", 3),
    ("0.5 + .5", 1),
    ("((2))", 2),
])
def test_arithmetic(expr, result):
    assert ev(expr) == pytest.approx(result)


def test_names_values_and_quoted():
    f = F.parse('"Prezzo unitario" * Quantità + spese.trasporto + "a\\"b"')
    assert f.names == ["Prezzo unitario", "Quantità", "spese.trasporto", 'a"b']
    vals = {"Prezzo unitario": "1.234,50", "Quantità": "2", "spese.trasporto": "abc", 'a"b': 1}
    assert F.evaluate(f, vals) == pytest.approx(2470)
    # nomi ripetuti una sola volta, nomi mancanti = 0, caselle (bool) = 1/0
    assert F.parse("a + a * b").names == ["a", "b"]
    assert ev("x + 1") == 1
    assert ev("c * 5", c=True) == 5


@pytest.mark.parametrize("expr,pos", [
    ("", 0),
    ("   ", 0),
    ("(a + b", 0),
    ("a + (b * (c - 1)", 4),
    ("a + b)", 5),
    ("a +", 3),
    ("a b", 2),
    ("a * * b", 4),
    ("a + $", 4),
    ('"aperta + b', 0),
    ('"" + 1', 0),
    ("()", 1),
    ("1.2.3", 0),
])
def test_syntax_errors(expr, pos):
    with pytest.raises(F.FormulaError) as ei:
        F.parse(expr)
    assert ei.value.position == pos
    assert ei.value.message


def test_check_unknown_names():
    f = F.parse("Imponibile * 1.22 - Sconto + importo")
    assert F.check(f, {"Imponibile", "Sconto", "importo.0", "importo.1"}) == []
    assert F.check(f, {"Imponibile"}) == ["Sconto", "importo"]


def test_script_round_trip():
    f = F.parse('Imponibile * 1.22 - Sconto / "Prezzo \\"x\\""')
    s = F.to_script(f)
    assert s.startswith("/* foliant-formula: Imponibile * 1.22 - Sconto")
    assert 'v("Imponibile")' in s and 'v("Prezzo \\"x\\"")' in s
    assert 'event.value = ""' in s
    back = F.from_script(s)
    assert back is not None and back.expression == f.expression and back.names == f.names


def test_script_comment_escape():
    f = F.parse('"a*/b" + 1')
    s = F.to_script(f)
    first_line = s.split("\n")[0]
    assert first_line.count("*/") == 1 and first_line.endswith(" */")
    assert F.from_script(s).names == ["a*/b"]


def test_modified_or_foreign_script_is_custom():
    s = F.to_script(F.parse("a + b"))
    assert F.from_script(s.replace('v("b")', 'v("c")')) is None
    assert F.from_script(s + "app.alert(1);") is None
    assert F.from_script('AFSimple_Calculate("SUM", new Array("a"));') is None
    assert F.from_script(None) is None
    assert F.from_script("/* foliant-formula: a + */\nevent.value = 1;") is None


def test_cycle():
    deps = {"Totale": ["Imponibile", "Iva"], "Iva": ["Imponibile"]}
    assert F.cycle("Imponibile", F.parse("Totale - Iva"), deps) == ["Imponibile", "Totale", "Imponibile"]
    assert F.cycle("Sconto", F.parse("Totale * 0.1"), deps) is None
    assert F.cycle("a", F.parse("a + 1"), {}) == ["a", "a"]
    assert F.cycle("Iva", None, deps) is None
    assert F.cycle("x", F.parse("g"), {"g.0": ["x"]}, all_names=["g.0", "x"]) == ["x", "g.0", "x"]


def test_no_eval():
    src = inspect.getsource(F)
    assert not re.search(r"(?<![.\w])(eval|exec|compile)\s*\(", src)
