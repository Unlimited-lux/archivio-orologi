"""Completezza delle traduzioni inglesi del modulo IA.

Estrae con `ast` ogni stringa passata a `_()` nei file di `foliant/modules/ai/`
(chiamate letterali; chiamate indirette `_(var)` / `_(var["chiave"])` dentro
cicli o comprensioni su liste, tuple o dizionari letterali) e verifica che
`translations.EN` le copra tutte con gli stessi segnaposto.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

import foliant.modules.ai as ai_pkg
from foliant.modules.ai.translations import EN

MODULE_DIR = Path(ai_pkg.__file__).parent
_COMPS = (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _module_names(trees):
    names = {}
    for tree in trees if isinstance(trees, list) else trees.values():
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names[t.id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                names[node.target.id] = node.value
    return names


def _items(it, names):
    """Elements of an iterable expression, as AST nodes (dict .items() -> 2-tuples)."""
    if isinstance(it, ast.Name) and it.id in names:
        return _items(names[it.id], names)
    if isinstance(it, (ast.List, ast.Tuple, ast.Set)):
        return list(it.elts)
    if isinstance(it, ast.Dict):
        return [k for k in it.keys if k is not None]
    if (isinstance(it, ast.Call) and isinstance(it.func, ast.Attribute) and not it.args
            and it.func.attr in ("items", "values", "keys")):
        base = it.func.value
        if isinstance(base, ast.Name) and base.id in names:
            base = names[base.id]
        if isinstance(base, ast.Dict):
            pairs = [(k, v) for k, v in zip(base.keys, base.values) if k is not None]
            if it.func.attr == "items":
                return [ast.Tuple(elts=[k, v]) for k, v in pairs]
            return [v for _k, v in pairs] if it.func.attr == "values" else [k for k, _v in pairs]
    return []


def _bind(target, elem):
    """Map loop-target names to element sub-nodes."""
    if isinstance(target, ast.Name):
        return {target.id: elem}
    if isinstance(target, (ast.Tuple, ast.List)) and isinstance(elem, (ast.Tuple, ast.List)):
        out = {}
        for t, e in zip(target.elts, elem.elts):
            out.update(_bind(t, e))
        return out
    return {}


def _str(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _tr_calls(scope):
    for n in ast.walk(scope):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_" and n.args:
            yield n


def _field_values(trees):
    """{field: [(string, where)]} from constructor calls of dataclass-like classes (and of factory
    functions that just return such a constructor call with their own parameters)."""
    classes = {}   # callable name -> list of field names by position
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                fields = [b.target.id for b in node.body
                          if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name)]
                if fields:
                    classes[node.name] = fields
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.body and isinstance(node.body[-1], ast.Return):
                ret = node.body[-1].value
                if (isinstance(ret, ast.Call) and isinstance(ret.func, ast.Name) and ret.func.id in classes):
                    params = [a.arg for a in node.args.args]
                    cfields = classes[ret.func.id]
                    mapping = [None] * len(params)
                    for i, a in enumerate(ret.args):
                        if isinstance(a, ast.Name) and a.id in params and i < len(cfields):
                            mapping[params.index(a.id)] = cfields[i]
                    for kw in ret.keywords:
                        if kw.arg and isinstance(kw.value, ast.Name) and kw.value.id in params:
                            mapping[params.index(kw.value.id)] = kw.arg
                    classes.setdefault(node.name, mapping)
    out = {}
    for fname, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in classes:
                fields = classes[node.func.id]
                pairs = [(fields[i] if i < len(fields) else None, a) for i, a in enumerate(node.args)]
                pairs += [(kw.arg, kw.value) for kw in node.keywords]
                for field_name, a in pairs:
                    if field_name and _str(a):
                        out.setdefault(field_name, []).append((_str(a), f"{fname}:{node.lineno}"))
    return out


def extract(mod_dir: Path) -> dict[str, list[str]]:
    trees = {}
    for f in sorted(mod_dir.glob("*.py")):
        if f.name != "translations.py":
            trees[f.name] = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
    all_names = _module_names(trees)
    field_values = _field_values(trees)
    found: dict[str, list[str]] = {}

    def add(s, where):
        if s:
            found.setdefault(s, []).append(where)

    for fname, tree in trees.items():
        names = {**all_names, **_module_names([tree])}   # same-file definitions win
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_" \
                    and node.args:
                arg = node.args[0]
                add(_str(arg), f"{fname}:{node.lineno}")
                # _(CONSTANT): module-level string constant
                if isinstance(arg, ast.Name) and arg.id.isupper() and arg.id in names:
                    add(_str(names[arg.id]), f"{fname}:{node.lineno} (constant {arg.id})")
                # _(obj.field): string values passed for that field to dataclass constructors
                if isinstance(arg, ast.Attribute):
                    for val, where in field_values.get(arg.attr, []):
                        add(val, f"{fname}:{node.lineno} (.{arg.attr} from {where})")
            loops = []
            if isinstance(node, (ast.For, ast.AsyncFor)):
                loops.append((node.target, node.iter, node))
            elif isinstance(node, _COMPS):
                loops.extend((g.target, g.iter, node) for g in node.generators)
            for target, it, scope in loops:
                for call in _tr_calls(scope):
                    arg = call.args[0]
                    key = None
                    if isinstance(arg, ast.Subscript) and isinstance(arg.value, ast.Name):
                        key, arg = _str(arg.slice), arg.value
                    if not isinstance(arg, ast.Name):
                        continue
                    for elem in _items(it, names):
                        val = _bind(target, elem).get(arg.id)
                        if val is None:
                            continue
                        if key is not None:
                            if isinstance(val, ast.Dict):
                                for k, v in zip(val.keys, val.values):
                                    if k is not None and _str(k) == key:
                                        add(_str(v), f"{fname}:{call.lineno} (indirect)")
                        else:
                            add(_str(val), f"{fname}:{call.lineno} (indirect)")
    return found


def test_every_translatable_string_has_english_translation():
    found = extract(MODULE_DIR)
    assert found, "nessuna stringa estratta: l'estrazione non funziona"
    missing = {k: v for k, v in found.items() if k not in EN}
    report = "\n".join(f"    {k!r}: '',  # {', '.join(v[:3])}" for k, v in missing.items())
    assert not missing, f"{len(missing)} stringhe senza traduzione inglese in EN:\n{report}"


def test_placeholders_match():
    bad = {k: v for k, v in EN.items()
           if Counter(_PLACEHOLDER.findall(k)) != Counter(_PLACEHOLDER.findall(v))}
    report = "\n".join(f"    {k!r} -> {v!r}" for k, v in bad.items())
    assert not bad, f"segnaposto diversi tra italiano e inglese:\n{report}"


def test_translations_are_non_empty_strings():
    bad = [k for k, v in EN.items() if not isinstance(v, str) or not v.strip()]
    assert not bad, f"traduzioni vuote: {bad}"
