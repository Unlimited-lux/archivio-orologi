"""Riservatezza: i timbri da immagine delle versioni precedenti avevano il nome del file scelto;
all'avvio del modulo diventano «Immagine N» (pulizia una tantum e idempotente)."""

from __future__ import annotations

import base64
import json

import pymupdf
import pytest

KEY = "annotations/custom_stamps"


def _png() -> str:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8), 1)
    pix.clear_with(0)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


@pytest.fixture
def legacy_settings(settings):
    png = _png()
    settings.setValue(KEY, json.dumps([
        {"name": "zq7k-contratto-rossi", "png": png},           # nome del file: va rinominato
        {"name": "Immagine 1", "png": png},                     # già generico
        {"name": "Istantanea 2", "png": png},                   # già generico
        {"name": "logo dello studio, sfondo blu", "png": png, "origin": "ia"},   # generato (ANN-28)
        {"name": "scansione_firma_2019", "png": png},           # nome del file: va rinominato
        {"name": "senza immagine"},                             # voce illeggibile: scartata
    ]))
    settings.sync()
    return settings


def _names(settings) -> list[str]:
    return [d["name"] for d in json.loads(str(settings.value(KEY, "[]")))]


def test_file_names_renamed_at_startup(legacy_settings, window):
    s = window.ctx.settings()
    assert _names(s) == ["Immagine 2", "Immagine 1", "Istantanea 2", "logo dello studio, sfondo blu",
                         "Immagine 3"]
    entries = json.loads(str(s.value(KEY)))
    assert [d.get("origin", "") for d in entries] == ["", "", "", "ia", ""]
    assert all(d["png"] for d in entries)
    s.sync()
    assert all("zq7k" not in str(s.value(k)) and "scansione" not in str(s.value(k)) for k in s.allKeys())
    # nessun marcatore della pulizia nelle impostazioni
    assert not [k for k in s.allKeys() if k.startswith("annotations/") and k != KEY and "stamp" in k]


def test_cleanup_is_idempotent(legacy_settings, window, monkeypatch):
    from foliant.modules.annotations.controller import Controller
    s = window.ctx.settings()
    before = s.value(KEY)
    writes = []
    original = type(s).setValue
    monkeypatch.setattr(type(s), "setValue", lambda self, k, v: (writes.append(k), original(self, k, v)))
    ctrl = Controller(window.ctx)                  # come un nuovo avvio
    assert ctrl.rename_file_named_stamps() == 0
    assert s.value(KEY) == before
    assert KEY not in writes                       # nulla da cambiare: nessuna riscrittura
    ctrl.add_custom_stamp("Immagine 9", base64.b64decode(_png()))
    assert KEY in writes                           # il controllo delle scritture funziona davvero


def test_new_names_skip_existing_numbers(window):
    """Dopo la pulizia i nuovi timbri prendono il primo numero libero, senza sovrascrivere."""
    from foliant.modules.annotations import ui_actions
    ctrl = window.ctx.annotations.ctrl
    png = base64.b64decode(_png())
    ctrl.add_custom_stamp("Istantanea 2", png)
    assert ui_actions.generic_stamp_name(ctrl, "Istantanea") == "Istantanea 1"
    ctrl.add_custom_stamp("Istantanea 1", png)
    assert ui_actions.generic_stamp_name(ctrl, "Istantanea") == "Istantanea 3"


def test_import_keeps_ai_origin(window, tmp_path):
    """Una libreria importata conserva l'origine «ia»: la pulizia non rinomina quei timbri."""
    from foliant.modules.annotations import ui_actions
    from foliant.modules.annotations.controller import Controller
    ctrl = window.ctx.annotations.ctrl
    lib = tmp_path / "libreria.json"
    lib.write_text(json.dumps({"version": 1, "stamps": [
        {"name": "gufo stilizzato", "png": _png(), "origin": "ia"}]}), encoding="utf-8")
    assert ui_actions.import_stamp_library(ctrl, str(lib)) == 1
    Controller(window.ctx)
    assert [(d["name"], d.get("origin")) for d in ctrl.custom_stamps()] == [("gufo stilizzato", "ia")]
