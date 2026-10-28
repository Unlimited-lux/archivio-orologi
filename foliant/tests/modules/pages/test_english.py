"""NFR-25: con la lingua inglese nessun testo dei dialoghi del modulo resta in italiano."""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget, QTableWidget, QWidget,
)

from foliant import i18n

# Parole che indicano un testo rimasto in italiano.
ITALIAN = {"pagina", "pagine", "tutte", "nessuna", "nessun", "sinistra", "destra", "centro", "alto", "basso",
           "inserisci", "estrai", "dividi", "unisci", "ritaglia", "annulla", "applica", "dimensione", "formato",
           "posizione", "colore", "opacità", "rotazione", "testo", "immagine", "sfondo", "filigrana", "margine",
           "margini", "intervallo", "selezionate", "corrente", "dispari", "pari", "orizzontale", "verticale",
           "carattere", "numero", "numeri", "etichetta", "etichette", "file", "cartella", "segnalibri",
           "sostituisci", "copie", "unità", "larghezza", "altezza", "anteprima", "piè", "intestazione", "riservato",
           "bozza", "copia", "data", "prefisso", "suffisso", "cifre", "stile", "fronte", "retro", "ogni", "parti",
           "nome", "dopo", "prima", "inizio", "fine", "rimuovi", "aggiungi", "scegli", "sfoglia", "vuota", "vuote"}
ALLOWED_SAME = re.compile(r"^[\W\d_]*$|^[A-Z0-9]{1,6}$|^(mm|pt|in|px|dpi|%|PDF|PNG|JPEG|TIFF|BMP|WebP|"
                          r"Letter|Legal|Tabloid|Executive|Helvetica.*|Times.*|Courier.*|OK|i, ii, iii|"
                          r"I, II, III|a, b, c|A, B, C|1, 2, 3)$")
# «file» è anche inglese
ENGLISH_TOO = {"file", "data", "copia"} - {"copia"}


def _texts(root: QWidget) -> list[str]:
    out: list[str] = []
    widgets = [root] + root.findChildren(QWidget)
    out.append(root.windowTitle())
    for w in widgets:
        if w.toolTip():
            out.append(w.toolTip())
        if isinstance(w, QLabel):
            out.append(w.text())
        elif isinstance(w, QAbstractButton):
            out.append(w.text())
        elif isinstance(w, QGroupBox):
            out.append(w.title())
        elif isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        elif isinstance(w, QLineEdit):
            out += [w.placeholderText(), w.text()]
        elif isinstance(w, QListWidget):
            out += [w.item(i).text() for i in range(w.count())]
        elif isinstance(w, QTableWidget):
            out += [w.horizontalHeaderItem(i).text() for i in range(w.columnCount()) if w.horizontalHeaderItem(i)]
    return [t for t in out if t and t.strip()]


def _italian_words(text: str) -> set[str]:
    # i segnaposto {nome}, {pagina}… sono codici del linguaggio dei campi (PAG-32), non testo
    text = re.sub(r"\{[^{}]*\}", " ", re.sub(r"<[^>]+>", " ", text))
    words = set(re.findall(r"[a-zàèéìòù]+", text.lower()))
    return {w for w in words if w in ITALIAN and w not in ENGLISH_TOO}


def _factories(window, view):
    from foliant.modules.pages import dialogs as D
    from foliant.modules.pages import images_dialogs as I
    from foliant.modules.pages import layout_dialogs as L
    from foliant.modules.pages import stamp_dialogs as S
    doc, ctx = view.document, window.ctx
    yield "InsertBlank", lambda: D.InsertBlankDialog(window, doc, [0])
    yield "InsertFile", lambda: D.InsertFileDialog(window, ctx, doc, [0])
    yield "Extract", lambda: D.ExtractDialog(window, doc, [0, 1])
    yield "Split", lambda: D.SplitDialog(window, doc)
    yield "Merge", lambda: D.MergeDialog(window, ctx, [])
    yield "Duplicate", lambda: D.DuplicateDialog(window)
    yield "Move", lambda: D.MoveDialog(window, 10)
    yield "Collate", lambda: D.CollateDialog(window, 10)
    yield "Replace", lambda: D.ReplaceDialog(window, ctx, doc, [0])
    yield "Scanner", lambda: D.ScannerDialog(window, [("test:0", "Test scanner")])
    for name in ("CropDialog", "RemoveMarginsDialog", "BoxesDialog", "ResizeDialog", "SplitPageDialog", "NupDialog"):
        yield name, (lambda n=name: getattr(L, n)(window, doc, [0]))
    for name in ("WatermarkDialog", "BackgroundDialog", "PageNumbersDialog", "BatesDialog", "HeaderFooterDialog",
                 "PageLabelsDialog", "RemoveStampsDialog"):
        yield name, (lambda n=name: getattr(S, n)(window, doc, [0]))
    yield "InsertImages", lambda: I.InsertImagesDialog([], window)
    yield "Paste", lambda: I.PasteDialog({"text": "ciao"}, window)
    yield "ExportImages", lambda: I.ExportImagesDialog(10, [0], 0, window)


def _organizer_texts(window) -> list[str]:
    c = window.ctx.pages_controller
    out = []
    for a in c.all_actions:
        out += [a.text(), a.toolTip()]
    out.append(c.organizer.info_label.text())
    return [t for t in out if t]


def test_dialogs_fully_english(pages_window):
    window, view = pages_window
    problems: dict[str, list[str]] = {}
    i18n.set_language("en")
    try:
        for name, make in _factories(window, view):
            dlg = make()
            bad = sorted({t for t in _texts(dlg) if _italian_words(t) and not ALLOWED_SAME.match(t)})
            if bad:
                problems[name] = bad
            dlg.deleteLater()
    finally:
        i18n.set_language("it")
    assert not problems, problems


def test_organizer_actions_english(window, ten_pdf):
    i18n.set_language("en")
    try:
        from foliant.ui.main_window import MainWindow  # noqa: F401
        from foliant.modules.pages.module import PagesController
        c = PagesController(window.ctx)
        from foliant.modules.pages.actions import build_actions
        mode = window.ctx.add_mode("pagine_en_test", "Pages", "layout-grid", 999)
        build_actions(window.ctx, c, mode)
        window.ctx.pages_controller_en = c
        texts = [t for a in c.all_actions for t in (a.text(), a.toolTip()) if t]
        bad = sorted({t for t in texts if _italian_words(t)})
    finally:
        i18n.set_language("it")
    assert not bad, bad


def test_default_output_names_follow_language(pages_window):
    from foliant.modules.pages import dialogs as D
    from foliant.modules.pages import engine as E
    window, view = pages_window
    i18n.set_language("en")
    try:
        assert D.SplitDialog(window, view.document).template.text() == "{nome}_part{n}"
        assert E.render_name("{titolo}", titolo="") == "untitled"
    finally:
        i18n.set_language("it")
    assert D.SplitDialog(window, view.document).template.text() == "{nome}_parte{n}"


def test_decimal_separator_follows_language(pages_window):
    """NFR-26: in inglese i decimali usano il punto (es. «179.9 × 267.0 mm»), in italiano la virgola."""
    from foliant.modules.pages import layout_dialogs as L
    window, view = pages_window
    i18n.set_language("en")
    try:
        dlg = L.CropDialog(window, view.document, [0])
        assert re.search(r"\d\.\d", dlg.info.text()) and not re.search(r"\d,\d", dlg.info.text())
    finally:
        i18n.set_language("it")
    dlg = L.CropDialog(window, view.document, [0])
    assert re.search(r"\d,\d", dlg.info.text())
