"""Raccolta dei testi visibili dei widget per il controllo delle traduzioni (NFR-25)."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QAbstractButton, QComboBox, QGroupBox, QLabel, QLineEdit, QListWidget, QPlainTextEdit, QTableWidget, QWidget,
)

# sigle, nomi propri e codici ammessi uguali nelle due lingue
ALLOWED = re.compile(r"^(OCR|PDF|TXT|CSV|ALTO|hOCR|DPI|dpi|MB|Ctrl\+.*|[\d\s.,:%·/()+<>—–\-…]*|[a-z]{3}(_[a-z]+)*|"
                     r"\d+\s?dpi|Tesseract.*|tesseract.*|ALTO XML.*|hOCR.*|CSV.*|PDF|[A-Z]{1,4}|.*\.(pdf|txt|csv|xml|hocr))$")
ITALIAN = re.compile(r"\b(il|lo|la|gli|le|di|del|della|dei|delle|che|per|con|non|una|uno|pagina|pagine|lingua|"
                     r"lingue|testo|salva|annulla|chiudi|riconosci|scegli|cartella|immagine|parole|incerte|"
                     r"zona|modello|tabella|avvia|esegui|rileva|carica|prospettiva|riprendi|pausa)\b", re.I)


def texts(root: QWidget) -> list[str]:
    out: list[str] = []

    def add(s):
        if s and s.strip():
            out.append(s.strip())
    widgets = [root] + root.findChildren(QWidget)
    add(root.windowTitle())
    for w in widgets:
        if isinstance(w, QLabel):
            add(w.text())
        if isinstance(w, QAbstractButton):
            add(w.text())
        if isinstance(w, QGroupBox):
            add(w.title())
        if isinstance(w, QLineEdit):
            add(w.placeholderText())
        if isinstance(w, QPlainTextEdit):
            add(w.placeholderText())
        if isinstance(w, QComboBox):
            for i in range(w.count()):
                add(w.itemText(i))
        if isinstance(w, QListWidget):
            for i in range(w.count()):
                add(w.item(i).text())
        if isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                h = w.horizontalHeaderItem(c)
                add(h.text() if h else "")
        add(w.toolTip())
    return out


def problems(it: list[str], en: list[str]) -> list[str]:
    """Voci uguali nelle due lingue (non ammesse) o con parole italiane nella versione inglese."""
    bad = []
    if len(it) != len(en):
        return [f"numero di testi diverso: {len(it)} contro {len(en)}"]
    for a, b in zip(it, en):
        if a == b and not ALLOWED.match(a) and ITALIAN.search(a):
            bad.append(f"non tradotto: {a!r}")
        elif ITALIAN.search(b) and not ALLOWED.match(b):
            bad.append(f"italiano nella versione inglese: {b!r}")
    return bad
