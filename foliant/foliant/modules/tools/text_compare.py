"""Confronto di testo semplice (CMP-13): due testi incollati, letti da file o
estratti da un PDF, confrontati per riga, parola o carattere."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QPushButton, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, DocumentError, _, atomic_write, bind_icon, theme

from .compare_engine import ADDED, DELETED, MODIFIED, kind_label

GRANULARITIES = ("line", "word", "char")
ENCODINGS = ("utf-8", "utf-16", "iso-8859-1", "iso-8859-15", "cp1252")


# =============================================================================
# Logica pura
# =============================================================================
def decode_text(data: bytes, encoding: str | None = None) -> str:
    """Decodifica un file di testo (BOM UTF-8/UTF-16, poi UTF-8, poi 8 bit)."""
    if encoding:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError) as e:
            raise DocumentError(_("Il testo non è nella codifica «{c}».").format(c=encoding)) from e
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    sample = data[:65536]
    if b"\x00" in sample:
        raise DocumentError(_("Il file sembra binario: non è un file di testo."))
    if sample:
        control = sum(1 for b in sample if b < 32 and b not in (9, 10, 12, 13))
        if control / len(sample) > 0.30:
            raise DocumentError(_("Il file sembra binario: non è un file di testo."))
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass
class TextDiff:
    kind: str
    line_a: int          # numero di riga (1..) in A; per le aggiunte la riga di inserimento
    line_b: int
    text_a: str
    text_b: str
    span_a: tuple[int, int]   # intervallo di caratteri nel testo A normalizzato
    span_b: tuple[int, int]

    def label(self) -> str:
        if self.kind == ADDED:
            body = self.text_b
        elif self.kind == DELETED:
            body = self.text_a
        else:
            body = f"{self.text_a} → {self.text_b}"
        body = " ".join(body.split())
        if len(body) > 120:
            body = body[:119] + "…"
        return f"{kind_label(self.kind)} — A {self.line_a} · B {self.line_b}: {body}"


def _tokens(text: str, granularity: str) -> list[tuple[str, int, int]]:
    """(testo, inizio, fine) degli elementi da confrontare."""
    if granularity == "char":
        return [(c, i, i + 1) for i, c in enumerate(text)]
    if granularity == "word":
        return [(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    out = []
    pos = 0
    for line in text.split("\n"):
        out.append((line, pos, pos + len(line)))
        pos += len(line) + 1
    return out


def _key(tok: str, ignore_case: bool, ignore_whitespace: bool) -> str:
    if ignore_whitespace:
        tok = re.sub(r"\s+", "", tok)
    if ignore_case:
        tok = tok.casefold()
    return tok


def compare_texts(a: str, b: str, granularity: str = "word", ignore_case: bool = False,
                  ignore_whitespace: bool = False) -> list[TextDiff]:
    """Differenze tra due testi; i terminatori di riga sono sempre normalizzati."""
    a, b = normalize_newlines(a), normalize_newlines(b)
    ta, tb = _tokens(a, granularity), _tokens(b, granularity)
    if granularity == "char" and ignore_whitespace:
        ta = [t for t in ta if not t[0].isspace()]
        tb = [t for t in tb if not t[0].isspace()]
    ka = [_key(t[0], ignore_case, ignore_whitespace) for t in ta]
    kb = [_key(t[0], ignore_case, ignore_whitespace) for t in tb]
    out: list[TextDiff] = []
    sm = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        sa = (ta[i1][1], ta[i2 - 1][2]) if i2 > i1 else ((ta[i1][1],) * 2 if i1 < len(ta) else (len(a), len(a)))
        sb = (tb[j1][1], tb[j2 - 1][2]) if j2 > j1 else ((tb[j1][1],) * 2 if j1 < len(tb) else (len(b), len(b)))
        kind = {"insert": ADDED, "delete": DELETED}.get(tag, MODIFIED)
        out.append(TextDiff(kind, a.count("\n", 0, sa[0]) + 1, b.count("\n", 0, sb[0]) + 1,
                            a[sa[0]:sa[1]], b[sb[0]:sb[1]], sa, sb))
    return out


def unified_diff(a: str, b: str, name_a: str = "A", name_b: str = "B") -> str:
    """Diff unificato applicabile con `patch` (gestisce l'assenza dell'a capo finale)."""
    la = normalize_newlines(a).splitlines(keepends=True)
    lb = normalize_newlines(b).splitlines(keepends=True)
    out = []
    for line in difflib.unified_diff(la, lb, name_a, name_b):
        if line.endswith("\n"):
            out.append(line)
        else:
            out.append(line + "\n\\ No newline at end of file\n")
    return "".join(out)


# =============================================================================
# Interfaccia
# =============================================================================
class _Side(QWidget):
    def __init__(self, title: str, owner: "TextCompareDialog"):
        super().__init__(owner)
        self.owner = owner
        self.edit = QPlainTextEdit()
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setPlaceholderText(_("Incolla qui il testo"))
        self.encoding = QComboBox()
        self.encoding.addItem(_("Codifica automatica"), None)
        for enc in ENCODINGS:
            self.encoding.addItem(enc.upper(), enc)
        self.encoding.currentIndexChanged.connect(self._reload)
        self._raw: bytes | None = None
        open_btn = QPushButton(_("Apri file…"))
        bind_icon(open_btn, "folder-open")
        open_btn.clicked.connect(self.choose_file)
        pdf_btn = QPushButton(_("Testo dal PDF attivo"))
        bind_icon(pdf_btn, "file-text")
        pdf_btn.clicked.connect(self.from_pdf)
        bar = QHBoxLayout()
        bar.addWidget(QLabel(title))
        bar.addStretch(1)
        bar.addWidget(open_btn)
        bar.addWidget(pdf_btn)
        bar.addWidget(self.encoding)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.edit, 1)

    def load_bytes(self, data: bytes) -> bool:
        try:
            text = decode_text(data, self.encoding.currentData())
        except DocumentError as e:
            self.owner.ctx.error(_("File non valido"), str(e))
            return False
        self._raw = data
        self.edit.setPlainText(normalize_newlines(text))
        return True

    def _reload(self) -> None:
        if self._raw is not None:
            self.load_bytes(self._raw)

    def choose_file(self) -> None:
        # dialogo del nucleo: nessuna cronologia dei file scelti
        paths = self.owner.ctx.ask_open_paths(_("Apri file di testo"),
                                              _("Testo (*.txt *.md *.csv);;Tutti i file (*)"), multiple=False)
        if paths:
            self.load_path(paths[0])

    def load_path(self, path: str) -> bool:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self.owner.ctx.error(_("File non leggibile"), str(e.strerror or e))
            return False
        return self.load_bytes(data)

    def from_pdf(self) -> bool:
        doc = self.owner.ctx.current_document()
        if doc is None:
            self.owner.ctx.notify(_("Apri prima un PDF."))
            return False
        with ENGINE_LOCK:
            text = "\n".join(page.get_text("text", sort=True) for page in doc.pdf)
        self._raw = None
        self.edit.setPlainText(normalize_newlines(text))
        return True


class TextCompareDialog(QDialog):
    """Confronto affiancato di due testi."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.diffs: list[TextDiff] = []
        self.setWindowTitle(_("Confronta testo"))
        self.resize(1000, 650)
        self.side_a = _Side(_("Testo A"), self)
        self.side_b = _Side(_("Testo B"), self)
        self.granularity = QComboBox()
        for key, label in (("line", _("Riga")), ("word", _("Parola")), ("char", _("Carattere"))):
            self.granularity.addItem(label, key)
        self.granularity.setCurrentIndex(1)
        self.ignore_case = QCheckBox(_("Ignora maiuscole/minuscole"))
        self.ignore_ws = QCheckBox(_("Ignora spazi"))
        compare = QPushButton(_("Confronta"))
        bind_icon(compare, "git-compare")
        compare.clicked.connect(self.run)
        export_diff = QPushButton(_("Esporta diff unificato"))
        bind_icon(export_diff, "file-diff")
        export_diff.clicked.connect(lambda: self.export_unified())
        export_pdf = QPushButton(_("Esporta rapporto PDF"))
        bind_icon(export_pdf, "file-output")
        export_pdf.clicked.connect(lambda: self.export_pdf())
        self.summary = QLabel()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.show_diff)
        top = QHBoxLayout()
        top.addWidget(QLabel(_("Granularità")))
        top.addWidget(self.granularity)
        top.addWidget(self.ignore_case)
        top.addWidget(self.ignore_ws)
        top.addStretch(1)
        top.addWidget(compare)
        top.addWidget(export_diff)
        top.addWidget(export_pdf)
        texts = QSplitter(Qt.Orientation.Horizontal)
        texts.addWidget(self.side_a)
        texts.addWidget(self.side_b)
        main = QSplitter(Qt.Orientation.Vertical)
        main.addWidget(texts)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.summary)
        bl.addWidget(self.list)
        main.addWidget(bottom)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(main, 1)

    def texts(self) -> tuple[str, str]:
        return self.side_a.edit.toPlainText(), self.side_b.edit.toPlainText()

    def run(self) -> list[TextDiff]:
        a, b = self.texts()
        self.diffs = compare_texts(a, b, self.granularity.currentData(), self.ignore_case.isChecked(),
                                   self.ignore_ws.isChecked())
        self.list.clear()
        for d in self.diffs:
            item = QListWidgetItem(d.label())
            item.setToolTip(f"{d.text_a}\n→\n{d.text_b}")
            self.list.addItem(item)
        if self.diffs:
            counts = {k: sum(1 for d in self.diffs if d.kind == k) for k in (ADDED, DELETED, MODIFIED)}
            self.summary.setText(" · ".join(f"{v} {kind_label(k, plural=True)}" for k, v in counts.items()))
        else:
            self.summary.setText(_("I testi sono identici secondo i criteri selezionati."))
        self._highlight()
        return self.diffs

    def _color(self, kind: str) -> QColor:
        c = QColor(theme.color({ADDED: "success", DELETED: "danger"}.get(kind, "warning")))
        c.setAlpha(90)
        return c

    def _highlight(self, current: TextDiff | None = None) -> None:
        for side, attr in ((self.side_a, "span_a"), (self.side_b, "span_b")):
            sels = []
            for d in self.diffs:
                start, end = getattr(d, attr)
                if end <= start:
                    continue
                sel = QTextEdit.ExtraSelection()
                cur = QTextCursor(side.edit.document())
                cur.setPosition(start)
                cur.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = cur
                fmt = QTextCharFormat()
                color = self._color(d.kind)
                if d is current:
                    color.setAlpha(200)
                fmt.setBackground(color)
                if d.kind == DELETED and side is self.side_a:
                    fmt.setFontStrikeOut(True)
                if d.kind == ADDED and side is self.side_b:
                    fmt.setFontUnderline(True)
                sel.format = fmt
                sels.append(sel)
            side.edit.setExtraSelections(sels)

    def show_diff(self, row: int) -> None:
        if not 0 <= row < len(self.diffs):
            return
        d = self.diffs[row]
        for side, span in ((self.side_a, d.span_a), (self.side_b, d.span_b)):
            cur = QTextCursor(side.edit.document())
            cur.setPosition(min(span[0], len(side.edit.toPlainText())))
            side.edit.setTextCursor(cur)
            side.edit.ensureCursorVisible()
        self._highlight(d)

    def export_unified(self, path: str | None = None) -> str | None:
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta diff unificato"), "confronto.diff",
                                          _("Diff unificato (*.diff *.patch)"), ".diff")
            if not path:
                return None
        a, b = self.texts()
        try:
            atomic_write(path, unified_diff(a, b).encode("utf-8"))
        except Exception as e:
            self.ctx.error(_("Esportazione non riuscita"), str(e))
            return None
        return path

    def export_pdf(self, path: str | None = None) -> str | None:
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta rapporto PDF"), "confronto_testo.pdf")
            if not path:
                return None
        if not self.diffs:
            self.run()
        try:
            atomic_write(path, text_report_pdf(self.diffs))
        except Exception as e:
            self.ctx.error(_("Esportazione non riuscita"), str(e))
            return None
        return path


def text_report_pdf(diffs: list[TextDiff]) -> bytes:
    """Rapporto PDF semplificato del confronto di testo."""
    import pymupdf

    from .report import _Writer
    with ENGINE_LOCK:
        doc = pymupdf.open()
        try:
            w = _Writer(doc)
            w.line(_("Confronto di testo"), size=16, gap=10)
            w.line(_("Differenze: {n}").format(n=len(diffs)), size=11, gap=8)
            if not diffs:
                w.line(_("I testi sono identici secondo i criteri selezionati."), size=10)
            for k, d in enumerate(diffs, 1):
                w.line(f"{k}. {d.label()}", size=9)
            return doc.tobytes(garbage=3, deflate=True)
        finally:
            doc.close()


__all__ = ["decode_text", "normalize_newlines", "TextDiff", "compare_texts", "unified_diff",
           "TextCompareDialog", "text_report_pdf"]
