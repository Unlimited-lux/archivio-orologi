"""NFR-25: con l'interfaccia in inglese nessun testo del modulo resta in italiano
(etichette, pulsanti, voci di combo, segnaposto, suggerimenti, azioni, titoli, intestazioni)."""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QListWidget, QMenu, QTableWidget, QWidget,
)

from foliant import i18n

# parole che compaiono solo in italiano (non in inglese)
ITALIAN = set("""
il lo la gli le del della delle dei degli di da che non per con una uno questo questa sono anche
testo pagina pagine blocco blocchi carattere caratteri sostituisci trova elimina modifica aggiungi annulla
chiudi cambia ignora dizionario parola parole documento tutto tutte tutti riga righe paragrafo dimensione
colore grassetto corsivo sottolineato barrato allinea centra giustifica interlinea rientro spazio dopo prima
sinistra destra alto basso cerca precedente successivo maiuscole minuscole intera accenti espressione
regolare intervallo corrente lingua lingue salva apri nessun nessuna incorpora corrispondenze stato tipo
codifica visualizzazione sostituito originale usare rimuovi riconoscimento mostra sovrimpressione converti
modificabile ruota sfondo primo piano unisci dividi rileva disponi distribuisci orizzontale verticale
incolla copia duplica occorrenze occorrenza sostituzione eliminazione ortografia controlla controllo
suggerimenti personale installa aggiorna esporta importa
""".split())
WORD = re.compile(r"[A-Za-zÀ-ÿ']+")


def italian_words(text: str) -> list[str]:
    words = [w.lower().strip("'") for w in WORD.findall(text or "")]
    return [w for w in words if w in ITALIAN]


def texts_of(widget: QWidget) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    widgets = [widget] + widget.findChildren(QWidget)
    for w in widgets:
        name = type(w).__name__
        if isinstance(w, QDialog) and w.windowTitle():
            out.append((name + ".title", w.windowTitle()))
        if isinstance(w, QLabel):
            out.append((name, w.text()))
        if isinstance(w, QAbstractButton):
            out.append((name, w.text()))
        if isinstance(w, QGroupBox):
            out.append((name, w.title()))
        if isinstance(w, QLineEdit):
            out.append((name + ".placeholder", w.placeholderText()))
        if isinstance(w, QComboBox):
            out.extend((name + ".item", w.itemText(i)) for i in range(w.count()))
            if w.lineEdit() is not None:
                out.append((name + ".placeholder", w.lineEdit().placeholderText()))
        if isinstance(w, QListWidget):
            out.extend((name + ".item", w.item(i).text()) for i in range(w.count()))
        if isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                item = w.horizontalHeaderItem(c)
                if item is not None:
                    out.append((name + ".header", item.text()))
        if w.toolTip():
            out.append((name + ".tooltip", w.toolTip()))
        for a in w.actions():
            out.append(("QAction", a.text()))
            out.append(("QAction.tooltip", a.toolTip()))
    return [(k, t) for k, t in out if t]


def problems(items: list[tuple[str, str]]) -> list[tuple[str, str, list[str]]]:
    out = []
    for kind, text in items:
        # i percorsi dei file non sono testi dell'interfaccia
        text = re.sub(r"(?:[A-Za-z]:)?[/\\][^\s]+", "", text)
        bad = italian_words(text)
        # nomi di font e campioni (es. «Liberation Serif») non sono testi dell'interfaccia
        if bad:
            out.append((kind, text, bad))
    return out


@pytest.fixture
def english_window(qtbot, settings, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from foliant.ui import theme
    from foliant.ui.main_window import MainWindow
    old = i18n.language()
    i18n.set_language("en")
    theme.apply(QApplication.instance(), "light")
    win = MainWindow(settings)
    qtbot.addWidget(win)
    win.resize(1200, 800)
    win.show()
    original_close = win.close_view

    def close_without_prompt(view, *a, **k):
        view.document._saved_state = view.document._state
        return original_close(view, *a, **k)

    monkeypatch.setattr(win, "close_view", close_without_prompt)
    monkeypatch.setattr(win.ctx, "error", lambda t, m: None)
    monkeypatch.setattr(win.ctx, "info", lambda t, m: None)
    monkeypatch.setattr(win.ctx, "confirm", lambda t, m: False)
    yield win
    i18n.set_language(old)


def test_english_ui_has_no_italian_words(english_window, paragraphs_pdf, qtbot):
    win = english_window
    ctx = win.ctx
    view = win.open_path(paragraphs_pdf)
    ctx.set_mode("modifica")
    ctrl = ctx.text_edit
    found: list = []
    # barra della modalità e voci del menu principale del modulo
    for act in ctrl.actions.values():
        found += problems([("QAction", act.text()), ("QAction.tooltip", act.toolTip())])
    # pannello delle proprietà: senza selezione, con un blocco selezionato e con l'editor aperto
    block = [b for b in ctrl.blocks(view, 0) if "contratto" in b.text][0]
    found += problems(texts_of(ctrl.panel))
    view.tool.select(view, 0, block)
    found += problems(texts_of(ctrl.panel))
    ed = ctrl.open_editor(view, 0, block)
    found += problems(texts_of(ctrl.panel))
    ctrl.cancel_editor(view)
    # menu contestuale sulla pagina
    from PySide6.QtCore import QPointF, Qt
    import pymupdf
    from foliant.ui.tools import PageEvent
    menu = QMenu()
    c = block.bbox
    pe = PageEvent(page=0, point=pymupdf.Point((c.x0 + c.x1) / 2, (c.y0 + c.y1) / 2), pos=QPointF(0, 0),
                   button=Qt.MouseButton.RightButton, buttons=Qt.MouseButton.RightButton,
                   modifiers=Qt.KeyboardModifier.NoModifier)
    for provider in win.view_menu_providers:
        provider(view, pe, menu)

    def menu_texts(m):
        out = []
        for a in m.actions():
            out.append(("QMenu", a.text()))
            if a.menu() is not None:
                out += menu_texts(a.menu())
        return out
    found += problems(menu_texts(menu))
    # dialoghi
    from foliant.modules.text_edit import open_find_dialog, open_fonts_dialog
    dlg = open_find_dialog(ctx)
    found += problems(texts_of(dlg))
    dlg.close()
    fonts_dlg = open_fonts_dialog(ctx)
    found += problems(texts_of(fonts_dlg))
    from foliant.modules.text_edit.fontlist_ui import MappingsDialog
    found += problems(texts_of(MappingsDialog(win)))
    fonts_dlg.close()
    try:
        from foliant.modules.text_edit.spell import shared_service
        from foliant.modules.text_edit.spell_ui import DictionaryDialog, SpellCheckDialog
        sdlg = SpellCheckDialog(ctx, shared_service(), win)
        found += problems(texts_of(sdlg))
        found += problems(texts_of(DictionaryDialog(ctx, shared_service(), win)))
        sdlg.close()
    except ImportError:
        pass
    assert not found, "\n".join(f"{k}: {t!r} -> {w}" for k, t, w in found)


def test_detector_finds_italian_in_italian_ui(window, paragraphs_pdf, monkeypatch):
    """Verifica del rilevatore: con l'interfaccia in italiano le stesse voci risultano italiane."""
    monkeypatch.setattr(window, "close_view", lambda v, *a, orig=window.close_view: (
        setattr(v.document, "_saved_state", v.document._state), orig(v))[1])
    i18n.set_language("it")
    window.open_path(paragraphs_pdf)
    window.ctx.set_mode("modifica")
    ctrl = window.ctx.text_edit
    found = problems(texts_of(ctrl.panel))
    found += problems([("QAction", a.text()) for a in ctrl.actions.values()])
    assert len(found) >= 5
