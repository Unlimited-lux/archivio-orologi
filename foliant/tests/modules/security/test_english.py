"""NFR-25: dialoghi e pannelli del modulo aperti in italiano e in inglese.

Segnala i testi rimasti identici nelle due lingue (esclusi sigle, nomi propri, numeri
e codici) e quelli inglesi con parole solo italiane."""

from __future__ import annotations

import re

import pymupdf
import pytest
from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QPlainTextEdit, QTreeWidget, QWidget,
)

from foliant.i18n import set_language
from foliant.modules.security import controller as C
from foliant.modules.security import dialogs as D
from foliant.modules.security import patterns as PT
from foliant.modules.security import protection as P
from foliant.modules.security import redaction as R
from foliant.modules.security import widgets as W

# parole che restano uguali in inglese (sigle, nomi propri, termini tecnici)
ALLOWED = {
    "IBAN", "AES", "RC4", "PDF", "XFDF", "XMP", "EXIF", "IPTC", "GPS", "OCR", "JSON", "GDPR", "URL", "IP",
    "OK", "SN", "Art", "Password", "OMISSIS", "JPEG", "ADBE", "ext", "es", "mailto", "http", "https", "Foliant",
}
ITALIAN = re.compile(r"\b(?:il|lo|la|gli|le|di|del|della|delle|dei|con|nel|nella|una|uno|non|sono|"
                     r"pagina|pagine|testo|segna|segni|documento|cerca|applica|rimuovi|annulla|chiudi|"
                     r"password dei|permessi|stampa|modifica|copia|commenti|moduli|campi|dati|nascosti|"
                     r"impostazioni|immagini|grafica|riempimento|sovrapposto|colore|occorrenz\w*|schema|"
                     r"schemi|codici|fiscali|telefono|indirizzi|importi|carte|partite)\b", re.IGNORECASE)


def _texts(widget: QWidget) -> list[str]:
    out = [widget.windowTitle()] if widget.isWindow() else []
    for w in [widget] + widget.findChildren(QWidget):
        if isinstance(w, QLabel):
            out.append(w.text())
        elif isinstance(w, QAbstractButton):
            out.append(w.text())
        elif isinstance(w, QGroupBox):
            out.append(w.title())
        elif isinstance(w, QComboBox):
            out.extend(w.itemText(i) for i in range(w.count()))
            if w.lineEdit() is not None:
                out.append(w.lineEdit().placeholderText())
        elif isinstance(w, QLineEdit):
            out.append(w.placeholderText())
        elif isinstance(w, QPlainTextEdit):
            out.append(w.placeholderText())
        elif isinstance(w, QTreeWidget):
            header = w.headerItem()
            out.extend(header.text(c) for c in range(header.columnCount()))
            stack = [w.topLevelItem(i) for i in range(w.topLevelItemCount())]
            while stack:
                item = stack.pop()
                out.append(item.text(0))
                stack.extend(item.child(i) for i in range(item.childCount()))
        out.append(w.toolTip())
    return [t for t in out if t and t.strip()]


def _meaningful(text: str) -> bool:
    words = re.findall(r"[A-Za-zÀ-ÿ]{3,}", text)
    return any(w not in ALLOWED for w in words)


def _build(sec, view) -> list[QWidget]:
    doc = view.document
    widgets: list[QWidget] = [
        D.ProtectDialog(P.ProtectionSettings(restrict=True), already_protected=True, has_open_password=True),
        D.FindDialog("terms", lambda o: ([], [])),
        D.FindDialog("patterns", lambda o: ([], []), [(p.id, p.label, True) for p in PT.builtin_patterns()]),
        D.PagesDialog(_t("Segna pagine per redazione"), 3, 0, with_overlay=True),
        D.StyleDialog(R.MarkStyle(), R.ApplyOptions(), sec.codes(), import_codes=lambda: None,
                      export_codes=lambda: None),
        D.SanitizeDialog({}),
        D.MetadataDialog({}, [(_t("Produttore PDF"), "x")], read_only=True),
        D.ReportDialog(_t("Proprietà di sicurezza"), "", C.security_rows(doc), action=_t("Proteggi…")),
        W.RedactionPanel(sec),
    ]
    finder = D.FindDialog("patterns", lambda o: ([], []))
    finder.set_results([PT.Match(0, "x@y.it", [pymupdf.Rect(0, 0, 1, 1)], "email", PT.builtin_patterns()[0].label,
                                 "x@y.it")], [])
    widgets.append(finder)
    return widgets


def _t(text):
    from foliant.api import _
    return _(text)


@pytest.fixture
def secure_doc(tmp_path):
    path = tmp_path / "limitato.pdf"
    src = pymupdf.open()
    src.new_page().insert_text((72, 100), "Testo", fontsize=12)
    src.save(str(path), encryption=pymupdf.PDF_ENCRYPT_RC4_128, user_pw="", owner_pw="capo",
             permissions=pymupdf.PDF_PERM_PRINT)
    return str(path)


def test_dialogs_fully_translated(window, qtbot, secure_doc, active_pdf):
    from foliant.modules import security
    sec = security.CONTROLLER
    view = window.open_path(secure_doc)
    active = window.open_path(active_pdf)
    texts: dict[str, list[str]] = {}
    for lang in ("it", "en"):
        set_language(lang)
        try:
            built = _build(sec, view)
            for w in built:
                qtbot.addWidget(w)
            texts[lang] = [t for w in built for t in _texts(w)]
            sec.refresh_document_state(view)
            sec.show_open_notices(active, sec.scans[active.document])
            texts[lang] += [t for v in (view, active) for n in sec.banners[v].notices
                            for t in [n.text] + [a[0] for a in n.actions]]
            texts[lang] += [t for _h, lines in C.security_rows(view.document) for t in [_h, *lines]]
            texts[lang] += [p.label for p in PT.builtin_patterns()]
        finally:
            set_language("it")
    it, en = set(texts["it"]), set(texts["en"])
    same = sorted(t for t in it & en if _meaningful(t) and t not in {"", "—"})
    italian = sorted(t for t in en if ITALIAN.search(t))
    assert not same, "\n".join(same)
    assert not italian, "\n".join(italian)
