"""VIS-52 — Scorciatoie da tastiera e ricerca dei comandi."""

from __future__ import annotations

import pytest
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
    found = ctl.search("ruota vista")
    assert found and found[0].text().startswith("Ruota vista")
    ctl.run("ruota vista")
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
    assert act.shortcut() == QKeySequence("Ctrl+Shift++")


def test_export_import_and_defaults(viewer_window):
    ctl = _ctl(viewer_window)
    act = _act(ctl, "Ruota vista")
    ctl.assign(act, "Ctrl+Alt+R")
    text = ctl.export_text()
    assert "Ruota vista\tCtrl+Alt+R" in text
    ctl.restore_defaults()
    assert act.shortcut() == QKeySequence("Ctrl+Shift++")
    assert ctl.import_text(text) > 0
    assert act.shortcut().toString() == "Ctrl+Alt+R"
    ctl.restore_defaults()


# ------------------------------------------------------------------ collaudo: C-1, C-2, C-13
def _items(pal):
    return [pal.list.item(i) for i in range(pal.list.count())]


def _type(pal, qtbot, text):
    pal.field.clear()
    qtbot.keyClicks(pal.field, text)


def _rotations(view):
    with view.document.lock:
        return [p.rotation for p in view.document.pdf]


def test_toolbar_toggle_actions_are_not_commands(viewer_window):
    """C-1: le azioni «mostra/nascondi barra» che Qt crea per ogni QToolBar (una per scheda, con il nome
    della scheda) non sono comandi: nella ricerca prenderebbero il posto della scheda."""
    from PySide6.QtWidgets import QDockWidget, QToolBar
    w = viewer_window
    toggles = {id(b.toggleViewAction()) for b in w.findChildren(QToolBar)}
    toggles |= {id(d.toggleViewAction()) for d in w.findChildren(QDockWidget)}
    assert not [a.text() for a in _ctl(w).commands() if id(a) in toggles]


@pytest.mark.parametrize("query, mode_id", [("ocr", "ocr"), ("proteggi", "proteggi"), ("firma", "firma"),
                                            ("pagine", "pagine"), ("visualizza", "visualizza")])
def test_palette_enter_goes_to_tab(viewer_window, sample_pdf, qtbot, query, mode_id):
    """C-1: «ocr», «proteggi», «firma» + Invio portano alla scheda (comandi «Vai alla scheda …»)."""
    w = viewer_window
    if mode_id not in w.modes:
        pytest.skip(f"scheda {mode_id} non caricata")
    w.open_path(sample_pdf)
    ctl = _ctl(w)
    ctl.open_palette()
    _type(ctl.palette, qtbot, query)
    first = ctl.palette.actions[ctl.palette.list.currentRow()]
    assert w.modes[mode_id].title in first.text()
    qtbot.keyClick(ctl.palette.field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: w.current_mode_id == mode_id, timeout=1000)
    assert not ctl.palette.isVisible()


def test_palette_file_opens_file_tab(viewer_window, qtbot):
    """C-1: «File» + Invio apre la scheda File; «Vai alla scheda …» c'è per ogni scheda."""
    w = viewer_window
    ctl = _ctl(w)
    names = [a.text() for a in ctl.commands()]
    for mode in w.modes.values():
        assert any(mode.title in n and n.startswith("Vai alla scheda") for n in names), mode.title
    ctl.open_palette()
    _type(ctl.palette, qtbot, "file")
    qtbot.keyClick(ctl.palette.field, Qt.Key.Key_Return)
    qtbot.waitUntil(w.backstage_visible, timeout=1000)
    w.hide_backstage()


def test_mode_scoped_action_runs_from_another_tab(viewer_window, sample_pdf, qtbot):
    """C-2: «Ruota a destra» di Pagine (scorciatoia valida solo lì) si avvia da Leggi: la ricerca
    passa alla scheda Pagine e ruota la pagina; la voce grigia di Modifica resta in fondo."""
    w = viewer_window
    if "pagine" not in w.modes:
        pytest.skip("modulo pagine non caricato")
    view = w.open_path(sample_pdf)
    w.set_mode("leggi")
    ctl = _ctl(w)
    found = [a for a in ctl.search("ruota a destra") if a.text() == "Ruota a destra"]
    assert len(found) >= 2 and found[0].isEnabled()
    assert not found[-1].isEnabled()
    ctl.open_palette()
    _type(ctl.palette, qtbot, "ruota a destra")
    qtbot.keyClick(ctl.palette.field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: _rotations(view)[0] == 90, timeout=2000)
    assert w.current_mode_id == "pagine"
    assert _rotations(view)[1:] == [0, 0]


def test_same_name_commands_show_where_they_are(viewer_window, sample_pdf):
    """C-2: due «Ruota a destra» (Pagine e Modifica › Oggetti) si distinguono dalla scheda o dal gruppo
    scritti accanto al nome; quella disattivata dice perché e resta in fondo."""
    w = viewer_window
    if "pagine" not in w.modes or "modifica" not in w.modes:
        pytest.skip("moduli pagine/oggetti non caricati")
    w.open_path(sample_pdf)
    ctl = _ctl(w)
    for mode_id in ("pagine", "leggi"):
        w.set_mode(mode_id)
        ctl.open_palette()
        ctl.palette.field.setText("ruota a destra")
        items = [it for it in _items(ctl.palette) if it.text().startswith("Ruota a destra")]
        assert len(items) == 2, [it.text() for it in items]
        texts = [it.text() for it in items]
        assert texts[0] != texts[1]
        assert w.modes["pagine"].title in texts[0] and w.modes["modifica"].title in texts[1]
        last = items[-1]
        assert last is _items(ctl.palette)[-1]
        reason = last.toolTip()
        assert reason.startswith("Non disponibile:")
        assert reason.replace("Non disponibile:", "").strip() not in ("", "Ruota a destra"), reason
        ctl.palette.hide()


def test_enter_on_disabled_command_goes_to_its_tab(viewer_window, sample_pdf, qtbot):
    """C-2: Invio su un comando grigio di un'altra scheda non resta senza effetto: porta alla sua
    scheda e dice nella barra di stato perché non è disponibile."""
    w = viewer_window
    if "pagine" not in w.modes or "modifica" not in w.modes:
        pytest.skip("moduli pagine/oggetti non caricati")
    w.open_path(sample_pdf)
    w.set_mode("leggi")
    ctl = _ctl(w)
    ctl.open_palette()
    pal = ctl.palette
    _type(pal, qtbot, "ruota a destra")
    qtbot.keyClick(pal.field, Qt.Key.Key_Down)
    act = pal.actions[pal.list.currentRow()]
    assert not act.isEnabled()
    assert "non è disponibile" in pal.hint.text() and w.modes["modifica"].title in pal.hint.text()
    qtbot.keyClick(pal.field, Qt.Key.Key_Return)
    assert w.current_mode_id == "modifica" and not pal.isVisible()
    assert "non è disponibile" in w.statusBar().currentMessage()


def test_enter_does_not_run_an_unchosen_command(viewer_window, sample_pdf, qtbot):
    """C-2: «ruota» + Invio non ruota subito le pagine con il primo risultato: con più comandi possibili
    il primo Invio lo sceglie (e lo dice), il secondo lo esegue; con le frecce si sceglie e Invio esegue."""
    w = viewer_window
    view = w.open_path(sample_pdf)
    ctl = _ctl(w)
    ctl.open_palette()
    pal = ctl.palette
    _type(pal, qtbot, "ruota")
    first = pal.actions[pal.list.currentRow()]
    qtbot.keyClick(pal.field, Qt.Key.Key_Return)
    qtbot.wait(50)
    assert pal.isVisible()
    assert _rotations(view) == [0, 0, 0] and view.view_rotation == 0 and not view.document.is_modified
    assert first.text().replace("&", "") in pal.hint.text()
    # frecce: la voce scelta si esegue con un solo Invio
    _type(pal, qtbot, "ruota vista")
    qtbot.keyClick(pal.field, Qt.Key.Key_Down)
    chosen = pal.actions[pal.list.currentRow()]
    assert chosen.text().startswith("Ruota vista in senso antiorario")
    qtbot.keyClick(pal.field, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: view.view_rotation == 270, timeout=1000)
    assert not pal.isVisible()


def test_theme_commands(viewer_window, qtbot):
    """C-13: «tema» trova i tre temi dell'interfaccia (File › Opzioni › Tema) e li applica."""
    from foliant.ui import theme
    w = viewer_window
    ctl = _ctl(w)
    names = [a.text() for a in ctl.search("tema")]
    for label in ("Tema: Chiaro", "Tema: Scuro", "Tema: Come il sistema"):
        assert label in names, names
    try:
        ctl.open_palette()
        _type(ctl.palette, qtbot, "tema scuro")
        qtbot.keyClick(ctl.palette.field, Qt.Key.Key_Return)
        qtbot.waitUntil(lambda: theme.mode() == "dark", timeout=1000)
        assert w.settings.value("theme") == "dark"
    finally:
        w.set_theme("light")


def test_search_matches_parent_menu_and_group(viewer_window, sample_pdf):
    """C-13: «inserisci pagina» trova «Pagina vuota…» del menu Pagine › Inserisci, scritto con il menu."""
    w = viewer_window
    if "pagine" not in w.modes:
        pytest.skip("modulo pagine non caricato")
    w.open_path(sample_pdf)
    ctl = _ctl(w)
    found = ctl.search("inserisci pagina")
    assert "Pagina vuota…" in [a.text() for a in found[:5]], [a.text() for a in found[:5]]
    ctl.open_palette()
    ctl.palette.field.setText("inserisci pagina")
    assert any("Inserisci › Pagina vuota" in it.text() for it in _items(ctl.palette))
    ctl.palette.hide()
