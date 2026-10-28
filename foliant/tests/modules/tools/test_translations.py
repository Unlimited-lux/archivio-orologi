"""Traduzioni inglesi del modulo Strumenti: completezza e segnaposto."""

from __future__ import annotations

import ast
import re
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[3] / "foliant" / "modules" / "tools"
_PH = re.compile(r"\{[^{}]*\}")


def _const_str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def extract_strings(directory: Path = MODULE_DIR) -> set[str]:
    """Stringhe tradotte a runtime: argomenti di _(), etichette di ActionSpec/Param,
    testi di choices/POSITIONS, STATUS_LABELS e COLUMNS."""
    found: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        if path.name == "translations.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
                if name == "_" and node.args:
                    s = _const_str(node.args[0])
                    if s:
                        found.add(s)
                elif name == "ActionSpec" and len(node.args) >= 2:
                    for arg in node.args[1:3]:     # etichetta e suffisso dei nomi dei file
                        s = _const_str(arg)
                        if s:
                            found.add(s)
                elif name == "Param" and len(node.args) >= 2:
                    s = _const_str(node.args[1])
                    if s:
                        found.add(s)
                    if len(node.args) >= 4 and _const_str(node.args[2]) == "str":
                        d = _const_str(node.args[3])     # testo predefinito, tradotto a runtime
                        if d and d != "ita+eng":
                            found.add(d)
                    for arg in node.args[4:5]:
                        found |= _pair_labels(arg)
                    for kw in node.keywords:
                        if kw.arg == "choices":
                            found |= _pair_labels(kw.value)
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "POSITIONS" in targets:
                    found |= _pair_labels(node.value)
                if "STATUS_LABELS" in targets and isinstance(node.value, ast.Dict):
                    found |= {s for s in map(_const_str, node.value.values) if s}
                if "COLUMNS" in targets and isinstance(node.value, ast.Tuple):
                    found |= {s for s in map(_const_str, node.value.elts) if s}
    return found


def _pair_labels(node) -> set[str]:
    out = set()
    if isinstance(node, ast.List):
        for elt in node.elts:
            if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                s = _const_str(elt.elts[1])
                if s:
                    out.add(s)
    return out


def test_all_strings_translated():
    from foliant.modules.tools.translations import EN
    missing = sorted(s for s in extract_strings() if s not in EN)
    assert missing == [], "Traduzioni mancanti:\n" + "\n".join(missing)


def test_placeholders_match():
    from foliant.modules.tools.translations import EN
    from foliant.modules.tools.engine import PLACEHOLDER_ALIASES

    def norm(text):   # i segnaposti dei modelli possono usare gli alias inglesi
        return sorted(PLACEHOLDER_ALIASES.get(p[1:-1], p[1:-1]) for p in _PH.findall(text))
    bad = [k for k, v in EN.items() if norm(k) != norm(v)]
    assert bad == []


def test_english_catalog_active():
    from foliant.i18n import _, add_translations, set_language
    from foliant.modules.tools.translations import EN
    add_translations("en", EN)
    set_language("en")
    try:
        assert _("Confronta documenti") == "Compare documents"
    finally:
        set_language("it")
