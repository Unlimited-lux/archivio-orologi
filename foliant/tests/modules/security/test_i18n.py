"""Ogni testo dell'interfaccia del modulo ha la traduzione inglese."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from foliant.modules.security.translations import EN

MODULE = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "security"


def _strings() -> list[str]:
    out = []
    for f in sorted(MODULE.glob("*.py")):
        if f.name == "translations.py":
            continue
        for node in ast.walk(ast.parse(f.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                out.append(node.args[0].value)
    return out


def test_all_strings_translated():
    missing = sorted({s for s in _strings() if s not in EN})
    assert not missing, missing


def test_placeholders_preserved():
    rx = re.compile(r"\{[a-z_]*\}")
    wrong = [k for k, v in EN.items() if sorted(rx.findall(k)) != sorted(rx.findall(v))]
    assert not wrong, wrong


def test_english_ui(window):
    from foliant.i18n import set_language
    set_language("en")
    try:
        from foliant.api import _
        assert _("Applica redazioni") == "Apply redactions"
        assert _("Proteggi") == "Protect"
    finally:
        set_language("it")
