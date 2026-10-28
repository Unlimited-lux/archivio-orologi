"""Ogni testo dell'interfaccia del modulo commenti ha la traduzione inglese."""

import pathlib
import re

from foliant.modules.annotations import engine
from foliant.modules.annotations.i18n_en import EN
from foliant.modules.annotations.panels import DASH_CHOICES, LINE_END_CHOICES, NOTE_ICON_LABELS, STATE_LABELS


def test_all_ui_strings_translated():
    folder = pathlib.Path(engine.__file__).parent
    missing = set()
    for f in folder.glob("*.py"):
        if f.name == "i18n_en.py":
            continue
        for s in re.findall(r'(?<![\w])_\("((?:[^"\\]|\\.)*)"\)', f.read_text()):
            s = s.encode().decode("unicode_escape").encode("latin-1").decode("utf-8")
            if s not in EN:
                missing.add(s)
    labels = list(STATE_LABELS.values()) + [lb for _k, lb in DASH_CHOICES + LINE_END_CHOICES]
    labels += list(NOTE_ICON_LABELS.values()) + list(engine.TYPE_LABELS.values()) + list(engine.SUBJECTS.values())
    labels += [lb for lb, _n, _c in engine.PRESET_STAMPS] + [t for _l, t, _c in engine.DYNAMIC_STAMPS]
    missing |= {lb for lb in labels if lb not in EN}
    assert not missing, sorted(missing)
