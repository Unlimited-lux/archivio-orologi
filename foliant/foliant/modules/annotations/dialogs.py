"""Finestre di dialogo del modulo commenti.

Ogni dialogo è una funzione di modulo, così i test possono sostituirla con
`monkeypatch.setattr(dialogs, "edit_comment", ...)`.
"""

from __future__ import annotations

from html.parser import HTMLParser

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QKeySequence, QTextCharFormat
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import _, bind_icon, theme

from . import engine


# ------------------------------------------------------------------ testo formattato
def document_to_xhtml(edit: QTextEdit) -> str | None:
    """XHTML per /RC dal contenuto dell'editor; None se il testo non ha formattazione."""
    doc = edit.document()
    formatted = False
    paras = []
    block = doc.begin()
    while block.isValid():
        spans = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                fmt = frag.charFormat()
                text = (frag.text().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
                style = []
                if fmt.fontWeight() >= QFont.Weight.Bold:
                    style.append("font-weight:bold")
                if fmt.fontItalic():
                    style.append("font-style:italic")
                deco = []
                if fmt.fontUnderline():
                    deco.append("underline")
                if fmt.fontStrikeOut():
                    deco.append("line-through")
                if deco:
                    style.append("text-decoration:" + " ".join(deco))
                if fmt.hasProperty(QTextCharFormat.Property.ForegroundBrush):
                    c = fmt.foreground().color()
                    if c.isValid() and c != QColor(0, 0, 0):
                        style.append("color:" + c.name())
                if style:
                    formatted = True
                    spans.append(f'<span style="{";".join(style)}">{text}</span>')
                else:
                    spans.append(text)
            it += 1
        paras.append("<p>" + "".join(spans) + "</p>")
        block = block.next()
    if not formatted:
        return None
    return ('<?xml version="1.0"?><body xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/" xfa:APIVersion="Foliant:1.0" xfa:spec="2.0.2">'
            + "".join(paras) + "</body>")


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._p = 0

    def handle_starttag(self, tag, attrs):
        if tag == "p" and self._p:
            self.parts.append("\n")
        if tag == "p":
            self._p += 1
        if tag == "br":
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def xhtml_to_plain(xhtml: str) -> str:
    p = _PlainText()
    try:
        p.feed(xhtml)
    except Exception:
        return ""
    return "".join(p.parts)


# ------------------------------------------------------------------ editor commento
class CommentEditor(QDialog):
    """Finestra del commento: intestazione autore/data, testo con formattazione di base."""

    def __init__(self, parent: QWidget | None, title: str, header: str, text: str, rich: str = "",
                 read_only: bool = False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 300)
        lay = QVBoxLayout(self)
        head = QLabel(header)
        head.setStyleSheet(f"color:{theme.hex_color('text_muted')}")
        lay.addWidget(head)
        bar = QHBoxLayout()
        self.edit = QTextEdit()
        self.edit.setAcceptRichText(True)
        for icon_name, tip, slot, key in (
                ("bold", _("Grassetto"), self._bold, "Ctrl+B"),
                ("italic", _("Corsivo"), self._italic, "Ctrl+I"),
                ("underline", _("Sottolineato"), self._underline, "Ctrl+U"),
                ("palette", _("Colore del testo"), self._color, None)):
            b = QToolButton()
            bind_icon(b, icon_name)
            b.setToolTip(tip + (f" ({QKeySequence(key).toString(QKeySequence.SequenceFormat.NativeText)})" if key else ""))
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            if key:
                b.setShortcut(QKeySequence(key))
            b.setEnabled(not read_only)
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        if rich:
            self.edit.setHtml(rich)
            if self.edit.toPlainText().strip() != text.strip():
                self.edit.setPlainText(text)   # /RC discordante: si mostra /Contents
        else:
            self.edit.setPlainText(text)
        self.edit.setReadOnly(read_only)
        lay.addWidget(self.edit, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.edit.setFocus()

    def _merge(self, fmt: QTextCharFormat) -> None:
        self.edit.mergeCurrentCharFormat(fmt)

    def _bold(self):
        fmt = QTextCharFormat()
        bold = self.edit.fontWeight() < QFont.Weight.Bold
        fmt.setFontWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
        self._merge(fmt)

    def _italic(self):
        fmt = QTextCharFormat()
        fmt.setFontItalic(not self.edit.fontItalic())
        self._merge(fmt)

    def _underline(self):
        fmt = QTextCharFormat()
        fmt.setFontUnderline(not self.edit.fontUnderline())
        self._merge(fmt)

    def _color(self):
        c = QColorDialog.getColor(self.edit.textColor(), self, _("Colore del testo"))
        if c.isValid():
            fmt = QTextCharFormat()
            fmt.setForeground(c)
            self._merge(fmt)

    def result_text(self) -> tuple[str, str | None]:
        return self.edit.toPlainText(), document_to_xhtml(self.edit)


def edit_comment(parent, title: str, header: str = "", text: str = "", rich: str = "",
                 read_only: bool = False) -> tuple[str, str | None] | None:
    """Apre l'editor del commento. Restituisce (testo, xhtml|None) o None se annullato."""
    dlg = CommentEditor(parent, title, header, text, rich, read_only)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.result_text()


def ask_text(parent, title: str, label: str, text: str = "", multiline: bool = False) -> str | None:
    if multiline:
        value, ok = QInputDialog.getMultiLineText(parent, title, label, text)
    else:
        value, ok = QInputDialog.getText(parent, title, label, QLineEdit.EchoMode.Normal, text)
    return value if ok else None


def ask_item(parent, title: str, label: str, items: list[str], current: int = 0) -> int | None:
    value, ok = QInputDialog.getItem(parent, title, label, items, current, False)
    if not ok:
        return None
    return items.index(value)


def ask_color(parent, title: str, initial: tuple | None) -> tuple | None:
    start = QColor.fromRgbF(*initial) if initial else QColor(255, 0, 0)
    c = QColorDialog.getColor(start, parent, title)
    if not c.isValid():
        return None
    return (c.redF(), c.greenF(), c.blueF())


def ask_open_file(parent, title: str, file_filter: str = "") -> str | None:
    path, _f = QFileDialog.getOpenFileName(parent, title, "", file_filter)
    return path or None


def ask_save_file(parent, title: str, suggested: str, file_filter: str = "") -> str | None:
    path, _f = QFileDialog.getSaveFileName(parent, title, suggested, file_filter)
    return path or None


# ------------------------------------------------------------------ scala
class ScaleDialog(QDialog):
    """Scala di misura: rapporto (es. 1 cm = 2 m) oppure calibrazione da un segmento."""

    def __init__(self, parent, scale: engine.Scale, calibrate_points: float | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Scala di misura"))
        form = QFormLayout(self)
        self.paper = QDoubleSpinBox()
        self.paper.setRange(0.0001, 1e9)
        self.paper.setDecimals(4)
        self.paper.setValue(1.0 if calibrate_points is None else calibrate_points)
        self.paper_unit = QComboBox()
        self.paper_unit.addItems(list(engine.PAGE_UNITS))
        self.paper_unit.setCurrentText("pt" if calibrate_points is not None else "cm")
        self.real = QDoubleSpinBox()
        self.real.setRange(0.0001, 1e12)
        self.real.setDecimals(4)
        self.real.setValue(1.0)
        self.real_unit = QComboBox()
        self.real_unit.addItems(engine.REAL_UNITS)
        self.real_unit.setCurrentText(scale.unit if scale.unit in engine.REAL_UNITS else "cm")
        row1 = QHBoxLayout()
        row1.addWidget(self.paper)
        row1.addWidget(self.paper_unit)
        row2 = QHBoxLayout()
        row2.addWidget(self.real)
        row2.addWidget(self.real_unit)
        form.addRow(_("Sulla pagina"), row1)
        form.addRow(_("Nella realtà"), row2)
        self.all_pages = QCheckBox(_("Applica a tutte le pagine"))
        self.all_pages.setChecked(True)
        form.addRow(self.all_pages)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def scale(self) -> engine.Scale:
        sc = engine.Scale.from_ratio(self.paper.value(), self.paper_unit.currentText(),
                                     self.real.value(), self.real_unit.currentText())
        sc.all_pages = self.all_pages.isChecked()
        return sc


def ask_scale(parent, scale: engine.Scale, calibrate_points: float | None = None) -> engine.Scale | None:
    dlg = ScaleDialog(parent, scale, calibrate_points)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    try:
        return dlg.scale()
    except engine.AnnotationError:
        return None


def show_text(parent, title: str, text: str) -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(520, 420)
    lay = QVBoxLayout(dlg)
    edit = QTextEdit()
    edit.setReadOnly(True)
    edit.setPlainText(text)
    lay.addWidget(edit)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dlg.reject)
    buttons.accepted.connect(dlg.accept)
    lay.addWidget(buttons)
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.exec()
