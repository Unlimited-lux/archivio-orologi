"""Scheda Commenta in stile Excel: gruppi con nome nella barra e voci del vecchio menu ☰ nella scheda File."""

from __future__ import annotations

import pathlib

from PySide6.QtWidgets import QToolButton

from foliant.i18n import _
from foliant.modules.annotations import engine


def _group_of(bar, action) -> str | None:
    """Nome del gruppo del pulsante che usa `action` (azione del pulsante o voce del suo menu)."""
    for title, acts in bar.groups():
        for a in acts:
            w = bar.widgetForAction(a)
            if not isinstance(w, QToolButton):
                continue
            if w.defaultAction() is action or (w.menu() is not None and action in w.menu().actions()):
                return title
    return None


def test_comment_bar_groups_have_names(window):
    module = window.ctx.annotations
    bar = module.mode.toolbar
    groups = bar.groups()
    assert [t for t, _a in groups] == [_("Selezione"), _("Testo"), _("Note"), _("Disegno"), _("Inserisci"),
                                       _("Gestisci")]
    assert all(title and acts for title, acts in groups)            # nessun gruppo senza nome o vuoto
    expected = {
        "select": _("Selezione"),
        engine.HIGHLIGHT: _("Testo"), engine.UNDERLINE: _("Testo"), engine.STRIKEOUT: _("Testo"),
        engine.SQUIGGLY: _("Testo"),
        engine.NOTE: _("Note"), engine.FREETEXT: _("Note"), engine.CALLOUT: _("Note"), engine.REPLACE: _("Note"),
        engine.RECT: _("Disegno"), engine.CLOUD: _("Disegno"), engine.INK: _("Disegno"), "eraser": _("Disegno"),
        engine.ATTACHMENT: _("Inserisci"), engine.DISTANCE: _("Inserisci"), engine.ANGLE: _("Inserisci"),
    }
    for key, title in expected.items():
        assert _group_of(bar, module.actions[key]) == title, key
    titles = {t: [bar.widgetForAction(a) for a in acts] for t, acts in groups}
    assert module.stamp_button in titles[_("Inserisci")]
    commands = [w for w in titles[_("Gestisci")] if isinstance(w, QToolButton)]
    assert len(commands) == 1 and commands[0].text() == _("Commenti")
    assert bar.contentsMargins().bottom() >= bar.caption_height()   # i nomi sono scritti sotto i pulsanti


def test_export_comments_is_in_file_export(window, text_pdf):
    module = window.ctx.annotations
    act = module.export_action
    assert window.backstage.page_of(act) == "esporta"
    entry = next(e for e in window.backstage.entries("esporta") if e.action is act)
    assert entry.group == _("Esporta") and entry.description == _("In XFDF o FDF, da reimportare")
    # una sola voce che chiede il formato, con le stesse azioni del menu «Commenti ▾»
    xfdf, fdf = module.export_actions["xfdf"], module.export_actions["fdf"]
    assert act.menu() is not None and act.menu().actions() == [xfdf, fdf]
    bar = module.mode.toolbar
    commands = next(bar.widgetForAction(a) for _t, acts in bar.groups() for a in acts
                    if isinstance(bar.widgetForAction(a), QToolButton)
                    and bar.widgetForAction(a).text() == _("Commenti"))
    menu_actions = commands.menu().actions()
    assert xfdf in menu_actions and fdf in menu_actions
    window.show_backstage("esporta")
    tile = next(t for t in window.backstage.tiles if t.action is act)
    assert not tile.isEnabled()                                     # nessun documento aperto
    window.hide_backstage()
    view = window.open_path(text_pdf)
    window.show_backstage("esporta")
    tile = next(t for t in window.backstage.tiles if t.action is act)
    assert tile.isEnabled() and tile.title_label.text() == _("Esporta commenti")
    window.hide_backstage()
    window.close_view(view, ask=False)


def test_import_and_summary_stay_only_in_the_comments_menu(window):
    module = window.ctx.annotations
    file_texts = {a.text() for a in window.backstage.all_actions()}
    for text in (_("Importa commenti…"), _("Riepilogo commenti…"), _("Esporta commenti…")):
        assert text not in file_texts
    bar = module.mode.toolbar
    commands = next(bar.widgetForAction(a) for _t, acts in bar.groups() for a in acts
                    if isinstance(bar.widgetForAction(a), QToolButton)
                    and bar.widgetForAction(a).text() == _("Commenti"))
    texts = [a.text() for a in commands.menu().actions()]
    assert _("Importa commenti…") in texts and _("Riepilogo commenti…") in texts


def test_module_no_longer_uses_the_main_menu():
    folder = pathlib.Path(engine.__file__).parent
    assert not [f.name for f in folder.glob("*.py") if "add_menu_action" in f.read_text(encoding="utf-8")]


def test_comment_shortcuts_unchanged(window):
    module = window.ctx.annotations
    window.set_mode("commenta")
    assert module.actions[engine.HIGHLIGHT].shortcut().toString() == "Ctrl+Shift+H"
    assert module.actions[engine.NOTE].shortcut().toString() == "Ctrl+Shift+N"
