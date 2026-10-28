"""Interfaccia inglese completa (NFR-25): ogni dialogo e pannello del modulo aperto in italiano e
in inglese; si segnalano i testi rimasti identici nelle due lingue o con parole solo italiane."""

from __future__ import annotations

import re

import pymupdf
import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QDoubleSpinBox, QGroupBox, QLabel, QLineEdit, QListWidget, QSpinBox, QTableWidget,
    QWidget,
)

from foliant import i18n
from foliant.modules import objects as O
from foliant.modules.objects import dialogs as D
from foliant.modules.objects import extras
from foliant.modules.objects import links as L
from foliant.modules.objects.linkpanel import LinkCheckPanel
from foliant.modules.objects.panel import ObjectPanel

from .test_obj_ui import make_doc, ui  # noqa: F401

# testi uguali nelle due lingue ma corretti (sigle, formati, unità, codici)
NEUTRAL = re.compile(r"^(?:[\W\d_]*|X:|Y:|PNG|JPEG|PDF|CSV|URL|ppi|pt|mm|°|%|\d+(?:[.,]\d+)?\s*(?:pt|px|ppi|%|°)?|"
                     r"\d+:\d+|https?://.*|.*@.*|1-\d+|File|File:|Zoom:|OK|Original[ei]?|Pixel.*|fo-.*)$", re.I)
ITALIAN_WORDS = {"della", "delle", "degli", "pagina", "pagine", "immagine", "immagini", "collegamento",
                 "collegamenti", "nessun", "nessuna", "è", "dal", "nella", "riempimento", "tratto",
                 "larghezza", "altezza", "scegli", "salva", "elimina", "inserisci", "verifica", "oggetti",
                 "ritaglio", "proporzioni", "trasparenza", "spessore", "tutte", "tutti", "corrente", "sfoglia"}


def texts(root: QWidget) -> list[tuple[str, str]]:
    out = [("title", root.windowTitle())]
    for w in root.findChildren(QWidget):
        name = type(w).__name__
        if isinstance(w, QLabel):
            out.append((name, w.text()))
        if isinstance(w, QAbstractButton):
            out.append((name, w.text()))
        if isinstance(w, QGroupBox):
            out.append((name, w.title()))
        if isinstance(w, QComboBox):
            out += [("combo", w.itemText(i)) for i in range(w.count())]
        if isinstance(w, QLineEdit):
            out.append(("placeholder", w.placeholderText()))
        if isinstance(w, (QSpinBox, QDoubleSpinBox)):
            out += [("special", w.specialValueText()), ("suffix", w.suffix())]
        if isinstance(w, QListWidget):
            out += [("list", w.item(i).text()) for i in range(w.count())]
        if isinstance(w, QTableWidget):
            out += [("header", w.horizontalHeaderItem(i).text()) for i in range(w.columnCount())
                    if w.horizontalHeaderItem(i)]
        out.append(("tooltip", w.toolTip()))
    return [(k, t.strip()) for k, t in out if t and t.strip()]


def _builders(ui, view):
    obj = None
    with O.ENGINE_LOCK:
        from foliant.modules.objects import engine as E
        objs = E.objects(view.document.pdf, 0).objects
        obj = objs[1] if len(objs) > 1 else objs[0]
    found = [L.FoundUrl(0, "www.esempio.it", "https://www.esempio.it", [pymupdf.Rect(0, 0, 10, 10).quad],
                        pymupdf.Rect(0, 0, 10, 10))]
    with view.document.change("prep", kind="annotation") as pdf:
        xref = L.create_link(pdf, 0, pymupdf.Rect(50, 500, 150, 520), L.LinkTarget(kind=L.PAGE, page=0, fit="FitH"))
    with O.ENGINE_LOCK:
        info = L.read_link(view.document.pdf, 0, xref)

    def path_panel():
        ui.c.set_selection(view, 0, [obj.index])
        return _panel(ObjectPanel(ui.ctx, ui.c), view)
    return {
        "link_edit": lambda: D.LinkDialog(None, view, 0, info),
        "object_panel_path": path_panel,
        "replace": lambda: D.ReplaceDialog(None, 3),
        "link_new": lambda: D.LinkDialog(None, view, 0, None),
        "url_scope": lambda: D.UrlScopeDialog(None, 3, 0),
        "found_urls": lambda: D.FoundUrlsDialog(None, found),
        "delete_links": lambda: D.DeleteLinksDialog(None, view),
        "replicate": lambda: D.ReplicateDialog(None, 3),
        "transform": lambda: D.TransformDialog(None),
        "adjust": lambda: D.AdjustDialog(None, 2),
        "optimize": lambda: D.OptimizeDialog(None, lambda **k: {"before": 2048, "after": 1024}),
        "path_style": lambda: D.PathStyleDialog(None, obj),
        "extract": lambda: extras.ExtractDialog(None, 3),
        "link_panel": lambda: _panel(LinkCheckPanel(ui.ctx), view),
        "object_panel": lambda: _panel(ObjectPanel(ui.ctx, ui.c), view),
    }


def _panel(p, view):
    p.set_view(view)
    return p


def _italian_only(text: str) -> bool:
    words = set(re.findall(r"[a-zàèéìòù]+", text.lower()))
    return bool(words & ITALIAN_WORDS)


@pytest.fixture
def english():
    yield
    i18n.set_language("it")


def test_dialogs_and_panels_fully_english(ui, tmp_path, english):
    view = ui.open(make_doc(tmp_path / "a.pdf", images=[(50, 70, 150, 120)], paths=[(300, 300, 400, 380)]))
    ui.c.set_selection(view, 0, [0])
    problems = []
    for name, build in _builders(ui, view).items():
        i18n.set_language("it")
        w_it = build()
        it = texts(w_it)
        i18n.set_language("en")
        w_en = build()
        en = texts(w_en)
        for (kind, t_it), (_k2, t_en) in zip(it, en):
            if NEUTRAL.match(t_en):
                continue
            if t_it == t_en and re.search(r"[A-Za-zÀ-ÿ]{3,}", t_en):
                problems.append(f"{name}/{kind}: «{t_en}» identico in inglese")
            elif _italian_only(t_en):
                problems.append(f"{name}/{kind}: «{t_en}» contiene parole italiane")
        w_it.deleteLater()
        w_en.deleteLater()
    assert not problems, "\n".join(problems)


def test_actions_have_english_translation(ui):
    from foliant.modules.objects.translations import EN
    missing = sorted({a.text() for a in O._state["actions"].values() if a.text() and a.text() not in EN})
    assert not missing, missing
