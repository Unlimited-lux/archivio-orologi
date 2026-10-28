"""VIS-52 — Scorciatoie da tastiera e ricerca dei comandi."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from foliant.modules.viewer.commands import match_score


def _ctl(w):
    return w.ctx.viewer_parts["commands"]


def _act(ctl, text):
    return [a for a in ctl.commands() if a.text().replace("&", "") == text][0]


def test_match_score():
    assert match_score("ruota", "Ruota vista") == 0
    assert match_score("vista ruota", "Ruota vista") is not None
    assert match_score("zzz", "Ruota vista") is None
    assert match_score("proprieta", "Proprietà del documento…") is not None


def test_search_and_run_first_result(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    found = ctl.search("ruota")
    assert found and found[0].text().startswith("Ruota vista")
    ctl.run("ruota")
    assert view.view_rotation == 90


def test_palette_enter_executes(viewer_window, sample_pdf, qtbot):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    ctl.open_palette()
    pal = ctl.palette
    qtbot.keyClicks(pal.field, "ruota vista")
    qtbot.keyClick(pal.field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: view.view_rotation == 90, timeout=1000)
    assert not pal.isVisible()


def test_disabled_commands_show_reason(viewer_window):
    ctl = _ctl(viewer_window)
    ctl.open_palette()
    ctl.palette.field.setText("salva")
    items = [ctl.palette.list.item(i) for i in range(ctl.palette.list.count())]
    disabled = [it for it in items if it.toolTip()]
    assert disabled and "Non disponibile" in disabled[0].toolTip()
    ctl.palette.hide()


def test_custom_shortcut_rotates_and_persists(viewer_window, sample_pdf, qtbot):
    view = viewer_window.open_path(sample_pdf)
    ctl = _ctl(viewer_window)
    act = _act(ctl, "Ruota vista")
    assert ctl.assign(act, "Ctrl+Alt+L")
    view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert view.view_rotation == 90
    assert ctl.saved()["Ruota vista"] == "Ctrl+Alt+L"


def test_conflict_warns_with_existing_name(viewer_window, monkeypatch):
    ctl = _ctl(viewer_window)
    asked = []
    monkeypatch.setattr(viewer_window.ctx, "confirm", lambda t, m: asked.append(m) or False)
    act = _act(ctl, "Ruota vista")
    assert not ctl.assign(act, "Ctrl+K", ask=True)
    assert asked and "Ricerca dei comandi" not in asked[0] and "Cerca un comando" in asked[0]
    assert act.shortcut().toString() == "Ctrl+R"


def test_export_import_and_defaults(viewer_window):
    ctl = _ctl(viewer_window)
    act = _act(ctl, "Ruota vista")
    ctl.assign(act, "Ctrl+Alt+R")
    text = ctl.export_text()
    assert "Ruota vista\tCtrl+Alt+R" in text
    ctl.restore_defaults()
    assert act.shortcut().toString() == "Ctrl+R"
    assert ctl.import_text(text) > 0
    assert act.shortcut().toString() == "Ctrl+Alt+R"
    ctl.restore_defaults()
