"""VIS-28 — Selezione e copia del testo: criteri di accettazione.

La selezione del testo è del nucleo (strumento Seleziona di `foliant.ui.tools`); il viewer
aggiunge la selezione rettangolare (Alt+trascinamento, `test_tools.py`) e le voci del menu
contestuale (Cerca, Leggi ad alta voce, Aggiungi segnalibro). I criteri che dipendono dal
nucleo e non sono ancora soddisfatti sono segnati `xfail(strict=True)` con il riferimento a
CORE_REQUESTS: quando il nucleo li soddisferà, il test lo segnalerà."""

from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QGuiApplication

from foliant.api import PageEvent


def _press_drag(view, page, start, end):
    """Trascinamento con lo strumento Seleziona del nucleo, da `start` a `end` (punti pagina)."""
    tool = view.tool

    def event(p, buttons=Qt.MouseButton.LeftButton):
        pos = view.page_transform(page).map(QPointF(p.x, p.y))
        return PageEvent(page, pymupdf.Point(p), pos, Qt.MouseButton.LeftButton, buttons,
                         Qt.KeyboardModifier.NoModifier)
    tool.press(view, event(start))
    mid = pymupdf.Point((start.x + end.x) / 2, (start.y + end.y) / 2)
    tool.move(view, event(mid))
    tool.move(view, event(end))
    tool.release(view, event(end, Qt.MouseButton.NoButton))


def _copy(view) -> str:
    QGuiApplication.clipboard().setText("")
    assert view.copy_selection()
    return QGuiApplication.clipboard().text()


def test_two_columns_copied_left_then_right(viewer_window, tmp_path):
    """Data una pagina non taggata a due colonne, selezionando dall'inizio della sinistra alla
    fine della destra, gli appunti contengono la colonna sinistra seguita dalla destra."""
    path = tmp_path / "colonne.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 80, 280, 400), "Alfa sinistra uno.\nBeta sinistra due.\n"
                        "Gamma sinistra tre.", fontsize=12)
    page.insert_textbox(pymupdf.Rect(320, 80, 550, 200), "Delta destra uno.\nEpsilon destra due.", fontsize=12)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    words = view.words(0)
    first = [w for w in words if w[4] == "Alfa"][0]
    last = [w for w in words if w[4] == "due."][-1]
    _press_drag(view, 0, pymupdf.Point(first[0] + 1, first[1] + 2), pymupdf.Point(last[2] - 1, last[3] - 2))
    text = _copy(view)
    assert text.index("Alfa") < text.index("Gamma") < text.index("Delta") < text.index("Epsilon")


@pytest.mark.xfail(strict=True, reason="CORE_REQUESTS 20: parole nell'ordine del flusso, non di lettura")
def test_two_columns_right_written_first(viewer_window, tmp_path):
    """Stesso criterio con la colonna destra scritta per prima nel contenuto della pagina."""
    path = tmp_path / "colonne_inverse.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(320, 80, 550, 200), "Delta destra uno.\nEpsilon destra due.", fontsize=12)
    page.insert_textbox(pymupdf.Rect(50, 80, 280, 400), "Alfa sinistra uno.\nBeta sinistra due.\n"
                        "Gamma sinistra tre.", fontsize=12)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    view._select_all_on_page()
    text = _copy(view)
    assert text.index("Alfa") < text.index("Gamma") < text.index("Delta")


@pytest.mark.xfail(strict=True, reason="CORE_REQUESTS 20: trattino di sillabazione non rimosso nella copia")
def test_hyphenated_word_copied_whole(viewer_window, tmp_path):
    """Data una parola spezzata a fine riga da un trattino di sillabazione, la copia contiene la
    parola intera senza trattino."""
    path = tmp_path / "sillabe.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Il movimento del cronografo è infor-", fontsize=12)
    page.insert_text((72, 116), "mazione preziosa per il collezionista.", fontsize=12)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    view._select_all_on_page()                 # Ctrl+A del nucleo: tutta la pagina
    text = _copy(view)
    assert "informazione" in text and "infor-" not in text


def test_double_click_selects_word(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    w = view.words(0)[0]
    p = pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
    pos = view.page_transform(0).map(QPointF(p.x, p.y))
    ev = PageEvent(0, p, pos, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    assert view.tool.double_click(view, ev)
    assert view.selected_text() == w[4]


def test_ctrl_a_and_copy(viewer_window, sample_pdf):
    view = viewer_window.open_path(sample_pdf)
    view._select_all_on_page()
    assert _copy(view).strip()


def test_selection_across_pages_in_order(viewer_window, tmp_path):
    """Selezione dalla pagina 3 alla 5: il testo delle tre pagine, in ordine."""
    path = tmp_path / "pagine.pdf"
    doc = pymupdf.open()
    for i in range(6):
        doc.new_page().insert_text((72, 100), f"Pagina{i + 1} testo", fontsize=12)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    from foliant.api import selection_from_words
    view.set_text_selections([selection_from_words(i, view.words(i)) for i in (2, 3, 4)])
    text = _copy(view)
    assert text.index("Pagina3") < text.index("Pagina4") < text.index("Pagina5")
    assert "Pagina2" not in text and "Pagina6" not in text


def test_context_menu_has_viewer_entries(viewer_window, tmp_path):
    """Menu contestuale della selezione: Cerca (VIS-24), Leggi ad alta voce (VIS-35),
    Aggiungi segnalibro (VIS-16) dal viewer."""
    from PySide6.QtWidgets import QMenu

    from foliant.api import selection_from_words
    path = tmp_path / "menu.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 100), "Rolex Daytona", fontsize=12)
    doc.save(str(path))
    view = viewer_window.open_path(str(path))
    view.set_text_selection(selection_from_words(0, view.words(0)))
    pe = PageEvent(0, pymupdf.Point(80, 95), QPointF(10, 10), Qt.MouseButton.RightButton,
                   Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
    menu = QMenu()
    for prov in viewer_window.view_menu_providers:
        if getattr(prov, "__module__", "").startswith("foliant.modules.viewer"):
            prov(view, pe, menu)
    texts = [a.text() for a in menu.actions()]
    assert "Cerca" in texts
    assert any("segnalibro" in t.lower() for t in texts)
    assert any("alta voce" in t.lower() or "da qui" in t.lower() for t in texts) or \
        not viewer_window.ctx.viewer_parts["speech"].engine_available()
