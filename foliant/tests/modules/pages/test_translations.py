"""Ogni testo dell'interfaccia del modulo Pagine ha la traduzione inglese."""

from __future__ import annotations

import re
from pathlib import Path

import foliant.modules.pages as pkg
from foliant.modules.pages import dialogs, images, images_dialogs, layout, layout_dialogs, stamp, stamp_dialogs
from foliant.modules.pages.translations import EN

ALL = {}
for mod in (dialogs, images, images_dialogs, layout, layout_dialogs, stamp, stamp_dialogs):
    ALL.update(getattr(mod, "TRANSLATIONS", {}))
ALL.update(EN)
ALL["…"] = "…"   # esempio nella docstring del motore


def test_all_literal_strings_translated():
    missing = {}
    for path in Path(pkg.__file__).parent.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        strings = set(re.findall(r'_\(\s*"((?:[^"\\]|\\.)*)"\s*\)', src))
        miss = sorted(s for s in strings if s.replace("\\n", "\n") not in ALL and s not in ALL)
        if miss:
            missing[path.name] = miss
    assert not missing, missing


def test_placeholders_match():
    for it, en in ALL.items():
        assert set(re.findall(r"\{(\w*)\}", it)) == set(re.findall(r"\{(\w*)\}", en)), (it, en)
