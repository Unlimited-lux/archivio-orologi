"""Revisioni di un PDF salvato con aggiornamenti incrementali (CMP-14).

Ogni revisione corrisponde al file troncato dopo un marcatore di fine file
(%%EOF); la revisione firmata è quella delimitata dal /ByteRange della firma.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

import pymupdf
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QListWidget, QPushButton, QVBoxLayout,
)

from foliant.api import ENGINE_LOCK, DocumentError, _, bind_icon

_EOF = re.compile(rb"%%EOF[ \t]*(?:\r\n|\r|\n|$)")
_BYTE_RANGE = re.compile(rb"/ByteRange\s*\[\s*(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*\]")


@dataclass
class Revision:
    number: int
    end: int                      # offset di fine (byte esclusi dopo)
    date: str = ""                # data leggibile (/ModDate o firma)
    signed: bool = False
    byte_range: tuple | None = None

    @property
    def label(self) -> str:
        parts = [_("Revisione {n}").format(n=self.number)]
        if self.date:
            parts.append(self.date)
        if self.signed:
            parts.append(_("firmata"))
        return " · ".join(parts)


def _pdf_date(value: str) -> str:
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value or "")
    if not m:
        return ""
    y, mo, d, h, mi = m.groups()
    out = f"{d or '01'}/{mo or '01'}/{y}"
    if h:
        out += f" {h}:{mi or '00'}"
    return out


def byte_ranges(data: bytes) -> list[tuple[int, int, int, int]]:
    return [tuple(int(x) for x in m.groups()) for m in _BYTE_RANGE.finditer(data)]


def list_revisions(data: bytes) -> list[Revision]:
    """Revisioni contenute nel file, dalla più vecchia alla più recente."""
    ends = []
    for m in _EOF.finditer(data):
        if not ends or m.end() > ends[-1]:
            ends.append(m.end())
    if not ends:
        raise DocumentError(_("Struttura dei riferimenti incrociati non riconosciuta: revisioni non "
                              "determinabili."))
    if ends[-1] < len(data.rstrip()):
        ends.append(len(data))   # dati dopo l'ultimo %%EOF (file non terminato correttamente)
    ranges = byte_ranges(data)
    signed_ends = {r[2] + r[3] for r in ranges}
    revisions: list[Revision] = []
    for end in ends:
        chunk = data[:end]
        with ENGINE_LOCK:
            try:
                doc = pymupdf.open(stream=chunk, filetype="pdf")
            except Exception:
                continue
            try:
                if doc.page_count == 0 and not doc.needs_pass:
                    continue
                date = _pdf_date((doc.metadata or {}).get("modDate", "")) if not doc.needs_pass else ""
            finally:
                doc.close()
        signed = any(abs(se - end) <= 2 for se in signed_ends)
        br = next((r for r in ranges if abs(r[2] + r[3] - end) <= 2), None)
        revisions.append(Revision(len(revisions) + 1, end, date, signed, br))
    if not revisions:
        raise DocumentError(_("Struttura dei riferimenti incrociati danneggiata: revisioni non determinabili."))
    return revisions


def revision_bytes(data: bytes, rev: Revision) -> bytes:
    return data[:rev.end]


def signed_revision(data: bytes) -> Revision | None:
    """La revisione coperta dalla prima firma (la «versione firmata»)."""
    ranges = byte_ranges(data)
    if not ranges:
        return None
    end = ranges[0][2] + ranges[0][3]
    revs = list_revisions(data)
    best = min(revs, key=lambda r: abs(r.end - end))
    return best if abs(best.end - end) <= 2 else None


class RevisionsDialog(QDialog):
    """Elenco delle revisioni del documento attivo e confronto tra due di esse."""

    def __init__(self, ctx, view, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.view = view
        self.data: bytes = b""
        self.revisions: list[Revision] = []
        self.setWindowTitle(_("Revisioni"))
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.list = QListWidget()
        self.rev_a = QComboBox()
        self.rev_b = QComboBox()
        self.compare_button = QPushButton(_("Confronta"))
        bind_icon(self.compare_button, "git-compare")
        self.compare_button.clicked.connect(lambda: self.compare())
        self.signed_button = QPushButton(_("Confronta con la versione firmata"))
        bind_icon(self.signed_button, "signature")
        self.signed_button.clicked.connect(lambda: self.compare_signed())
        form = QFormLayout()
        form.addRow(_("A (precedente)"), self.rev_a)
        form.addRow(_("B (successiva)"), self.rev_b)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.message)
        lay.addWidget(self.list, 1)
        lay.addLayout(form)
        lay.addWidget(self.compare_button)
        lay.addWidget(self.signed_button)
        lay.addWidget(buttons)
        self.load()

    def load(self) -> None:
        doc = self.view.document if self.view is not None else None
        self.revisions = []
        self.list.clear()
        if doc is None or doc.path is None or not os.path.exists(doc.path):
            self.message.setText(_("Il documento non è salvato su disco: le revisioni non sono disponibili."))
        else:
            try:
                with open(doc.path, "rb") as f:
                    self.data = f.read()
                self.revisions = list_revisions(self.data)
            except (OSError, DocumentError) as e:
                self.message.setText(str(getattr(e, "strerror", None) or e))
                self.revisions = []
            else:
                if len(self.revisions) == 1:
                    self.message.setText(_("Il file contiene una sola revisione: non è stato salvato con "
                                           "aggiornamenti incrementali (oppure è stato riscritto per intero)."))
                else:
                    self.message.setText(_("{n} revisioni trovate.").format(n=len(self.revisions)))
        for r in self.revisions:
            self.list.addItem(r.label)
        for combo in (self.rev_a, self.rev_b):
            combo.clear()
            for r in self.revisions:
                combo.addItem(r.label, r.number)
        if len(self.revisions) >= 2:
            self.rev_a.setCurrentIndex(len(self.revisions) - 2)
            self.rev_b.setCurrentIndex(len(self.revisions) - 1)
        multi = len(self.revisions) >= 2
        self.compare_button.setEnabled(multi)
        self.rev_a.setEnabled(multi)
        self.rev_b.setEnabled(multi)
        signed = signed_revision(self.data) if multi else None
        self.signed_button.setEnabled(signed is not None and signed.number != self.revisions[-1].number)

    def _source(self, rev: Revision):
        from . import compare_engine as ce
        from .compare_ui import Source
        password = self.view.document.password if self.view is not None else None
        label = _("Revisione {n}").format(n=rev.number)
        src = Source(label=label, path=None, data=revision_bytes(self.data, rev), password=password)
        return src, ce.SideInput(src.data, label, None, password)

    def compare(self, number_a: int | None = None, number_b: int | None = None):
        from . import compare_engine as ce
        na = number_a or self.rev_a.currentData()
        nb = number_b or self.rev_b.currentData()
        ra = next((r for r in self.revisions if r.number == na), None)
        rb = next((r for r in self.revisions if r.number == nb), None)
        if ra is None or rb is None or ra is rb:
            self.ctx.notify(_("Scegli due revisioni diverse."))
            return None
        sa, ia = self._source(ra)
        sb, ib = self._source(rb)
        controller = self.ctx.tools_module.compare
        widget = controller.run(ia, ib, sa, sb, ce.CompareOptions.from_dict(controller.last_options.to_dict()))
        self.accept()   # il confronto prosegue senza bloccare la finestra
        return widget

    def compare_signed(self):
        signed = signed_revision(self.data)
        if signed is None:
            self.ctx.notify(_("Il documento non contiene una versione firmata."))
            return None
        return self.compare(signed.number, self.revisions[-1].number)
