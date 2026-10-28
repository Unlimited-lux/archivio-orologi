"""Ogni testo dell'interfaccia del modulo Pagine ha la traduzione inglese."""

from __future__ import annotations

import re
from pathlib import Path

import ast
import importlib

import foliant.modules.pages as pkg
from foliant.modules.pages.translations import EN

ALL = {}
for _path in Path(pkg.__file__).parent.glob("*.py"):
    if _path.stem != "__init__":
        ALL.update(getattr(importlib.import_module(f"foliant.modules.pages.{_path.stem}"), "TRANSLATIONS", {}) or {})
ALL.update(EN)
ALL["…"] = "…"   # esempio nella docstring del motore


def test_all_literal_strings_translated():
    missing = {}
    for path in Path(pkg.__file__).parent.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        strings = set(re.findall(r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)', src))
        miss = sorted(s for s in strings if ast.literal_eval('"' + s + '"') not in ALL)
        if miss:
            missing[path.name] = miss
    assert not missing, missing


def test_placeholders_match():
    for it, en in ALL.items():
        assert set(re.findall(r"\{(\w*)\}", it)) == set(re.findall(r"\{(\w*)\}", en)), (it, en)


def test_no_key_registered_twice_with_different_values():
    """Catalogo per modulo (NFR-25): dentro Pagine una chiave ha una sola traduzione;
    le parole ambigue («Formato») usano pgettext con un contesto."""
    seen: dict[str, set[str]] = {}
    for path in Path(pkg.__file__).parent.glob("*.py"):
        if path.stem == "__init__":
            continue
        mod = importlib.import_module(f"foliant.modules.pages.{path.stem}")
        catalog = getattr(mod, "TRANSLATIONS", None) or (EN if path.stem == "translations" else {})
        for k, v in catalog.items():
            seen.setdefault(k, set()).add(v)
    assert not {k: v for k, v in seen.items() if len(v) > 1}


def test_formato_contexts_in_english(pages_window):
    from foliant import i18n
    from foliant.modules.pages import dialogs as D, layout_dialogs as L, stamp_dialogs as S
    window, view = pages_window
    i18n.set_language("en")
    try:
        from PySide6.QtWidgets import QFormLayout, QLabel
        def labels(dlg):
            return {w.text() for w in dlg.findChildren(QLabel)}
        assert "Size:" in labels(D.InsertBlankDialog(window, view.document, [0]))
        assert "Format:" in labels(S.PageNumbersDialog(window, view.document, [0]))
        assert "Size" in labels(L.ResizeDialog(window, view.document, [0]))
    finally:
        i18n.set_language("it")
