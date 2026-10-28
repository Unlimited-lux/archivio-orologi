"""Interfaccia del confronto: scelta dei documenti, vista affiancata e
sovrapposta, elenco delle differenze, esportazione del rapporto."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QAbstractListModel, QModelIndex, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView, QMenu, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStackedWidget, QToolBar, QToolButton,
    QVBoxLayout, QWidget,
)

from foliant.api import (
    ENGINE_LOCK, FIT_PAGE, FIT_WIDTH, Document, DocumentError, DocumentView, OperationCancelled, PasswordRequired,
    SelectTextTool, WrongPassword, _, atomic_write, bind_icon, icon, theme,
)

from . import compare_engine as ce
from . import report as rp

SESSION_FILTER = "Sessione di confronto (*.foliantcmp)"
MM = ce.MM


def kind_color(kind: str) -> QColor:
    return {
        ce.ADDED: theme.color("success"),
        ce.DELETED: theme.color("danger"),
        ce.MODIFIED: theme.color("warning"),
        ce.FORMAT: theme.color("secondary"),
        ce.VISUAL: theme.color("accent"),
    }[kind]


KIND_ICONS = {ce.ADDED: "circle-plus", ce.DELETED: "circle-minus", ce.MODIFIED: "replace",
              ce.FORMAT: "type", ce.VISUAL: "scan-eye"}


# =============================================================================
# Dati dei due lati
# =============================================================================
def ask_password(parent, name: str, wrong: bool) -> str | None:
    """Chiede la password di un documento (sostituibile nei test)."""
    text = (_("Password errata. Riprova per «{n}»:") if wrong else _("«{n}» è protetto. Inserisci la password:"))
    value, ok = QInputDialog.getText(parent, _("Documento protetto"), text.format(n=name),
                                     QLineEdit.EchoMode.Password)
    return value if ok else None


@dataclass
class Source:
    """Un documento da confrontare (scheda aperta o file)."""

    label: str
    path: str | None
    data: bytes
    password: str | None = None
    unsaved: bool = False
    page_count: int = 0

    @classmethod
    def from_view(cls, view: DocumentView) -> "Source":
        doc = view.document
        if doc.path is not None and not doc.is_modified and os.path.exists(doc.path):
            with open(doc.path, "rb") as f:
                data = f.read()
            password = doc.password
        else:
            with ENGINE_LOCK:
                data = doc.pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)
            password = None
        src = cls(doc.display_name, str(doc.path) if doc.path else None, data, password, doc.is_modified)
        src.page_count = doc.page_count
        return src

    @classmethod
    def from_file(cls, path: str, parent=None) -> "Source | None":
        """Legge un file chiedendo la password se serve. None se l'utente annulla."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            raise DocumentError(_("Impossibile leggere «{n}»: {e}").format(n=os.path.basename(path), e=e.strerror))
        password = None
        wrong = False
        for _attempt in range(5):
            try:
                doc, _t, _r = ce.open_side(data, password)
            except PasswordRequired:
                pass
            except WrongPassword:
                wrong = True
            else:
                with ENGINE_LOCK:
                    count = doc.page_count
                    doc.close()
                src = cls(os.path.basename(path), os.path.abspath(path), data, password)
                src.page_count = count
                return src
            password = ask_password(parent, os.path.basename(path), wrong)
            if password is None:
                return None
        raise WrongPassword(_("Troppi tentativi non riusciti."))

    def thumbnail(self, height: int = 120) -> QPixmap | None:
        try:
            doc, _t, _r = ce.open_side(self.data, self.password)
        except Exception:
            return None
        try:
            with ENGINE_LOCK:
                page = doc[0]
                z = height / max(1.0, page.rect.height)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        finally:
            with ENGINE_LOCK:
                doc.close()
        return QPixmap.fromImage(img)

    def make_document(self) -> Document:
        doc = Document.from_bytes(self.data, display_name=self.label, password=self.password, modified=False)
        return doc


# =============================================================================
# Finestra di avvio (CMP-01)
# =============================================================================
class SidePicker(QGroupBox):
    changed = Signal()

    def __init__(self, title: str, ctx, parent=None):
        super().__init__(title, parent)
        self.ctx = ctx
        self.source: Source | None = None
        self.setAcceptDrops(True)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(240)
        self.browse = QPushButton(_("Sfoglia…"))
        bind_icon(self.browse, "folder-open")
        self.thumb = QLabel()
        self.thumb.setFixedSize(96, 128)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb.setFrameShape(QFrame.Shape.StyledPanel)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.all_pages = QCheckBox(_("Tutte le pagine"))
        self.all_pages.setChecked(True)
        self.first = QSpinBox()
        self.last = QSpinBox()
        for s in (self.first, self.last):
            s.setMinimum(1)
            s.setEnabled(False)
        self.all_pages.toggled.connect(lambda on: (self.first.setEnabled(not on), self.last.setEnabled(not on)))
        top = QHBoxLayout()
        top.addWidget(self.combo, 1)
        top.addWidget(self.browse)
        rng = QHBoxLayout()
        rng.addWidget(self.all_pages)
        rng.addStretch(1)
        rng.addWidget(QLabel(_("da")))
        rng.addWidget(self.first)
        rng.addWidget(QLabel(_("a")))
        rng.addWidget(self.last)
        body = QHBoxLayout()
        body.addWidget(self.thumb)
        right = QVBoxLayout()
        right.addWidget(self.info)
        right.addStretch(1)
        body.addLayout(right, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(body)
        lay.addLayout(rng)
        self._views = list(ctx.views())
        for v in self._views:
            mark = " " + _("(non salvato)") if v.document.is_modified else ""
            self.combo.addItem(icon("file-text"), v.document.display_name + mark, v)
        self.combo.addItem(icon("folder-open"), _("Scegli un file…"), None)
        self.combo.currentIndexChanged.connect(self._combo_changed)
        self.browse.clicked.connect(self.choose_file)

    def select_view(self, view) -> None:
        for i in range(self.combo.count()):
            if self.combo.itemData(i) is view:
                self.combo.setCurrentIndex(i)
                self._combo_changed(i)
                return

    def _combo_changed(self, index: int) -> None:
        view = self.combo.itemData(index)
        if view is None:
            if self.source is None or self.source.path is None or any(
                    v.document.display_name == self.source.label for v in self._views):
                self.set_source(None)
            return
        self.set_source(Source.from_view(view))

    def choose_file(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scegli il documento"), multiple=False)
        if paths:
            self.load_path(paths[0])

    def load_path(self, path: str) -> None:
        try:
            src = Source.from_file(path, self)
        except DocumentError as e:
            self.ctx.error(_("Impossibile usare il file"), str(e))
            return
        if src is None:
            return
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self.combo.count() - 1)
        self.combo.setItemText(self.combo.count() - 1, src.label)
        self.combo.blockSignals(False)
        self.set_source(src)

    def set_source(self, src: Source | None) -> None:
        self.source = src
        if src is None:
            self.info.setText(_("Nessun documento scelto."))
            self.thumb.clear()
        else:
            if not src.page_count:
                try:
                    doc, _t, _r = ce.open_side(src.data, src.password)
                    with ENGINE_LOCK:
                        src.page_count = doc.page_count
                        doc.close()
                except DocumentError as e:
                    self.ctx.error(_("Impossibile usare il file"), str(e))
                    self.source = None
                    self.info.setText(_("Nessun documento scelto."))
                    self.changed.emit()
                    return
            extra = "\n" + _("(non salvato: si confronta lo stato corrente)") if src.unsaved else ""
            self.info.setText(_("{n}\n{p} pagine").format(n=src.label, p=src.page_count) + extra)
            for s in (self.first, self.last):
                s.setMaximum(max(1, src.page_count))
            self.first.setValue(1)
            self.last.setValue(max(1, src.page_count))
            pm = src.thumbnail(124)
            if pm is not None:
                self.thumb.setPixmap(pm.scaled(QSize(92, 124), Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        self.changed.emit()

    def page_range(self) -> tuple[int, int] | None:
        if self.all_pages.isChecked():
            return None
        a, b = self.first.value() - 1, self.last.value() - 1
        return (min(a, b), max(a, b))

    def state(self):
        return (self.source, self.all_pages.isChecked(), self.first.value(), self.last.value(),
                self.combo.currentIndex(), self.combo.itemText(self.combo.count() - 1))

    def restore(self, st) -> None:
        src, all_on, first, last, index, last_text = st
        self.combo.blockSignals(True)
        self.combo.setItemText(self.combo.count() - 1, last_text)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        self.set_source(src)
        self.all_pages.setChecked(all_on)
        self.first.setValue(first)
        self.last.setValue(last)

    # trascinamento di file
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        urls = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if urls:
            self.load_path(urls[0])


PRESETS = {
    "text": dict(mode=ce.MODE_AUTO, sensitivity="medium", dpi=150, auto_align=True),
    "drawing": dict(mode=ce.MODE_VISUAL, sensitivity="high", dpi=200, auto_align=False),
    "scan": dict(mode=ce.MODE_VISUAL, sensitivity="low", dpi=150, auto_align=True),
}


class CompareSetupDialog(QDialog):
    """Scelta di A e B, del tipo di confronto e delle opzioni."""

    def __init__(self, ctx, parent=None, options: ce.CompareOptions | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(_("Confronta documenti"))
        self.setMinimumWidth(860)
        self.side_a = SidePicker(_("A · versione di riferimento"), ctx)
        self.side_b = SidePicker(_("B · versione da verificare"), ctx)
        self.swap_button = QPushButton(_("Scambia"))
        bind_icon(self.swap_button, "arrow-left-right")
        self.swap_button.clicked.connect(self.swap)
        sides = QHBoxLayout()
        sides.addWidget(self.side_a, 1)
        sides.addWidget(self.swap_button, 0, Qt.AlignmentFlag.AlignVCenter)
        sides.addWidget(self.side_b, 1)

        opts = options or ce.CompareOptions()
        self.mode_group = QButtonGroup(self)
        mode_box = QHBoxLayout()
        self.mode_buttons = {}
        for mode, label in ((ce.MODE_AUTO, _("Automatico")), (ce.MODE_TEXT, _("Solo testo")),
                            (ce.MODE_VISUAL, _("Solo visivo"))):
            b = QRadioButton(label)
            self.mode_group.addButton(b)
            self.mode_buttons[mode] = b
            mode_box.addWidget(b)
        self.preset = QComboBox()
        self.preset.addItem(_("Preset…"), None)
        self.preset.addItem(_("Documento di testo"), "text")
        self.preset.addItem(_("Disegno tecnico"), "drawing")
        self.preset.addItem(_("Scansione"), "scan")
        self.preset.currentIndexChanged.connect(self._apply_preset)
        mode_box.addStretch(1)
        mode_box.addWidget(self.preset)

        self.ignore_case = QCheckBox(_("Ignora maiuscole/minuscole"))
        self.ignore_ws = QCheckBox(_("Ignora spazi e a capo"))
        self.ignore_punct = QCheckBox(_("Ignora punteggiatura"))
        self.compare_format = QCheckBox(_("Confronta formattazione"))
        self.ignore_hf = QCheckBox(_("Ignora intestazioni e piè di pagina"))
        self.ignore_numbers = QCheckBox(_("Ignora numeri di pagina"))
        self.ignore_annots = QCheckBox(_("Ignora annotazioni"))
        self.auto_align = QCheckBox(_("Allineamento automatico"))
        self.sensitivity = QComboBox()
        for key, label in (("high", _("Alta")), ("medium", _("Media")), ("low", _("Bassa"))):
            s = ce.SENSITIVITY[key]
            self.sensitivity.addItem(_("{l} (tolleranza {t}, {p} px)").format(l=label, t=s["tolerance"],
                                                                               p=s["cell_pixels"]), key)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 600)
        self.dpi.setSuffix(" dpi")
        text_box = QGroupBox(_("Testo"))
        tl = QVBoxLayout(text_box)
        for w in (self.ignore_case, self.ignore_ws, self.ignore_punct, self.compare_format):
            tl.addWidget(w)
        vis_box = QGroupBox(_("Visivo"))
        vl = QFormLayout(vis_box)
        vl.addRow(_("Sensibilità"), self.sensitivity)
        vl.addRow(_("Risoluzione"), self.dpi)
        vl.addRow(self.auto_align)
        ex_box = QGroupBox(_("Esclusioni"))
        el = QVBoxLayout(ex_box)
        for w in (self.ignore_hf, self.ignore_numbers, self.ignore_annots):
            el.addWidget(w)
        grid = QHBoxLayout()
        grid.addWidget(text_box)
        grid.addWidget(vis_box)
        grid.addWidget(ex_box)
        self.set_options(opts)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.start = buttons.addButton(_("Confronta"), QDialogButtonBox.ButtonRole.AcceptRole)
        bind_icon(self.start, "git-compare")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(sides)
        lay.addWidget(QLabel(_("Tipo di confronto")))
        lay.addLayout(mode_box)
        lay.addLayout(grid)
        lay.addWidget(buttons)
        for side in (self.side_a, self.side_b):
            side.changed.connect(self._update_start)
        self._propose_defaults()
        self._update_start()

    def _propose_defaults(self) -> None:
        views = self.ctx.views()
        current = self.ctx.current_view()
        if current is not None:
            self.side_a.select_view(current)
            others = [v for v in views if v is not current]
            if others:
                self.side_b.select_view(others[0])
            else:
                self.side_b.combo.setCurrentIndex(self.side_b.combo.count() - 1)
                self.side_b.set_source(None)
        else:
            for side in (self.side_a, self.side_b):
                side.combo.setCurrentIndex(side.combo.count() - 1)
                side.set_source(None)

    def _update_start(self) -> None:
        self.start.setEnabled(self.side_a.source is not None and self.side_b.source is not None)

    def _apply_preset(self, index: int) -> None:
        key = self.preset.itemData(index)
        if key:
            opts = self.options()
            for k, v in PRESETS[key].items():
                setattr(opts, k, v)
            self.set_options(opts)

    def set_options(self, o: ce.CompareOptions) -> None:
        self.mode_buttons.get(o.mode, self.mode_buttons[ce.MODE_AUTO]).setChecked(True)
        self.ignore_case.setChecked(o.ignore_case)
        self.ignore_ws.setChecked(o.ignore_whitespace)
        self.ignore_punct.setChecked(o.ignore_punctuation)
        self.compare_format.setChecked(o.compare_format)
        self.ignore_hf.setChecked(o.ignore_headers_footers)
        self.ignore_numbers.setChecked(o.ignore_page_numbers)
        self.ignore_annots.setChecked(not o.include_annotations)
        self.auto_align.setChecked(o.auto_align)
        self.sensitivity.setCurrentIndex(max(0, self.sensitivity.findData(o.sensitivity)))
        self.dpi.setValue(o.dpi)
        self._exclusions = list(o.exclusions)

    def options(self) -> ce.CompareOptions:
        mode = next(m for m, b in self.mode_buttons.items() if b.isChecked())
        return ce.CompareOptions(
            mode=mode, ignore_case=self.ignore_case.isChecked(), ignore_whitespace=self.ignore_ws.isChecked(),
            ignore_punctuation=self.ignore_punct.isChecked(), compare_format=self.compare_format.isChecked(),
            dpi=self.dpi.value(), sensitivity=self.sensitivity.currentData(),
            auto_align=self.auto_align.isChecked(), include_annotations=not self.ignore_annots.isChecked(),
            ignore_headers_footers=self.ignore_hf.isChecked(), ignore_page_numbers=self.ignore_numbers.isChecked(),
            exclusions=list(getattr(self, "_exclusions", [])))

    def swap(self) -> None:
        sa, sb = self.side_a.state(), self.side_b.state()
        self.side_a.restore(sb)
        self.side_b.restore(sa)

    def inputs(self) -> tuple[ce.SideInput, ce.SideInput, Source, Source]:
        a, b = self.side_a.source, self.side_b.source
        ia = ce.SideInput(a.data, a.label, a.path, a.password, self.side_a.page_range(), a.unsaved)
        ib = ce.SideInput(b.data, b.label, b.path, b.password, self.side_b.page_range(), b.unsaved)
        return ia, ib, a, b


# =============================================================================
# Elenco delle differenze (CMP-07, CMP-08)
# =============================================================================
class DiffModel(QAbstractListModel):
    """Modello virtualizzato delle differenze visibili (filtri applicati)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.all: list[ce.Difference] = []
        self.visible: list[ce.Difference] = []
        self.kinds = set(ce.KINDS)
        self.search = ""
        self.order = "a"

    def set_differences(self, diffs: list[ce.Difference]) -> None:
        self.all = list(diffs)
        self.refilter()

    def refilter(self) -> None:
        self.beginResetModel()
        q = self.search.casefold().strip()
        vis = [d for d in self.all if d.kind in self.kinds
               and (not q or q in (d.text_a + " " + d.text_b + " " + d.detail).casefold())]
        if self.order == "b":
            vis.sort(key=lambda d: (d.locations("b")[0][0] if d.locations("b") else 0,
                                    d.locations("b")[0][2] if d.locations("b") else 0, d.id))
        self.visible = vis
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.visible)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.visible):
            return None
        d = self.visible[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            pages = _("A p. {a} · B p. {b}").format(a=d.page_label("a"), b=d.page_label("b"))
            head = f"{d.id}. {ce.kind_label(d.kind)} — {pages}"
            excerpt = d.excerpt(120)
            if d.kind == ce.FORMAT and d.detail:
                excerpt = f"{d.text_b}: {d.detail}"
            return f"{head}\n{excerpt}" if excerpt else head
        if role == Qt.ItemDataRole.ToolTipRole:
            return d.as_text()
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{ce.kind_label(d.kind)}, A pagina {d.page_label('a')}, B pagina {d.page_label('b')}, {d.excerpt()}"
        if role == Qt.ItemDataRole.DecorationRole:
            return icon(KIND_ICONS[d.kind], color=kind_color(d.kind).name())
        if role == Qt.ItemDataRole.UserRole:
            return d
        return None


class DiffPanel(QWidget):
    """Pannello con contatori, filtri, ricerca ed elenco navigabile."""

    activated = Signal(object)          # Difference
    filtersChanged = Signal()
    endReached = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(280)
        self.result: ce.CompareResult | None = None
        self.model = DiffModel(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setObjectName("diffSummary")
        self.counter = QLabel()
        self.filters: dict[str, QCheckBox] = {}
        fl = QGridLayout()
        for i, kind in enumerate(ce.KINDS):
            cb = QCheckBox(ce.kind_label(kind, plural=True).capitalize())
            cb.setChecked(True)
            cb.setIcon(icon(KIND_ICONS[kind], color=kind_color(kind).name()))
            cb.toggled.connect(self._filters_changed)
            self.filters[kind] = cb
            fl.addWidget(cb, i // 2, i % 2)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Cerca nelle differenze"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search_changed)
        self.order = QComboBox()
        self.order.addItem(_("Ordina per posizione in A"), "a")
        self.order.addItem(_("Ordina per posizione in B"), "b")
        self.order.currentIndexChanged.connect(self._order_changed)
        self.list = QListView()
        self.list.setModel(self.model)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(False)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setAlternatingRowColors(True)
        self.list.clicked.connect(self._clicked)
        self.list.activated.connect(self._clicked)
        self.list.selectionModel().currentChanged.connect(lambda cur, _prev: self._clicked(cur))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        QShortcut(QKeySequence.StandardKey.Copy, self.list, self.copy_current)
        self.empty = QLabel()
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty.setContentsMargins(8, 8, 8, 8)
        self.reset_filters = QPushButton(_("Mostra tutte"))
        self.reset_filters.clicked.connect(self.show_all)
        self.message = QLabel()
        self.message.setWordWrap(True)
        nav = QHBoxLayout()
        self.prev_button = QToolButton()
        self.prev_button.setToolTip(_("Differenza precedente (Alt+↑)"))
        bind_icon(self.prev_button, "chevron-up")
        self.next_button = QToolButton()
        self.next_button.setToolTip(_("Differenza successiva (Alt+↓)"))
        bind_icon(self.next_button, "chevron-down")
        self.prev_button.clicked.connect(self.previous)
        self.next_button.clicked.connect(self.next)
        nav.addWidget(self.counter, 1)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.next_button)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(self.summary)
        lay.addLayout(fl)
        lay.addWidget(self.search)
        lay.addWidget(self.order)
        lay.addLayout(nav)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.empty, 1)
        lay.addWidget(self.reset_filters)
        lay.addWidget(self.message)
        self._at_end = False
        self._update_state()

    # ---------------------------------------------------------------- dati
    def set_result(self, result: ce.CompareResult) -> None:
        self.result = result
        self.model.set_differences(result.differences)
        for kind, cb in self.filters.items():
            n = sum(1 for d in result.differences if d.kind == kind)
            cb.setText(f"{ce.kind_label(kind, plural=True).capitalize()} ({n})")
        self.summary.setText(result.summary())
        warn = "\n".join("⚠ " + w for w in result.warnings)
        self.message.setText(warn)
        self.message.setVisible(bool(warn))
        self._update_state()

    def visible_kinds(self) -> set[str]:
        return {k for k, cb in self.filters.items() if cb.isChecked()}

    def visible_differences(self) -> list[ce.Difference]:
        return list(self.model.visible)

    def filters_note(self) -> str:
        hidden = [ce.kind_label(k, plural=True) for k in ce.KINDS if k not in self.visible_kinds()]
        if not hidden and not self.model.search:
            return ""
        parts = []
        if hidden:
            parts.append(_("tipi nascosti: {t}").format(t=", ".join(hidden)))
        if self.model.search:
            parts.append(_("ricerca: «{q}»").format(q=self.model.search))
        return _("Filtri applicati: {f}").format(f="; ".join(parts))

    def _filters_changed(self) -> None:
        self.model.kinds = self.visible_kinds()
        self.model.refilter()
        self._update_state()
        self.filtersChanged.emit()

    def _search_changed(self, text: str) -> None:
        self.model.search = text
        self.model.refilter()
        self._update_state()
        self.filtersChanged.emit()

    def _order_changed(self) -> None:
        self.model.order = self.order.currentData()
        self.model.refilter()

    def show_all(self) -> None:
        for cb in self.filters.values():
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.model.search = ""
        self._filters_changed()

    def _update_state(self) -> None:
        total = len(self.model.all)
        shown = len(self.model.visible)
        self.counter.setText(_("Visibili {v} di {t}").format(v=shown, t=total))
        has = shown > 0
        self.list.setVisible(has)
        self.empty.setVisible(not has)
        self.reset_filters.setVisible(not has and total > 0)
        self.prev_button.setEnabled(has)
        self.next_button.setEnabled(has)
        if not has:
            if total == 0 and self.result is not None:
                crit = "\n".join("• " + c for c in self.result.options.criteria())
                self.empty.setText(_("I documenti sono identici secondo i criteri selezionati.") + "\n\n"
                                   + _("Criteri applicati:") + "\n" + crit)
            elif total:
                self.empty.setText(_("{n} differenze nascoste dai filtri.").format(n=total))
            else:
                self.empty.setText("")

    # ---------------------------------------------------------------- navigazione
    def current(self) -> ce.Difference | None:
        idx = self.list.currentIndex()
        return self.model.data(idx, Qt.ItemDataRole.UserRole) if idx.isValid() else None

    def select(self, diff: ce.Difference | None, emit: bool = False) -> None:
        if diff is None or diff not in self.model.visible:
            self.list.clearSelection()
            return
        row = self.model.visible.index(diff)
        idx = self.model.index(row)
        self.list.blockSignals(True)
        self.list.selectionModel().blockSignals(True)
        self.list.setCurrentIndex(idx)
        self.list.selectionModel().blockSignals(False)
        self.list.blockSignals(False)
        self.list.scrollTo(idx)
        self._at_end = False
        if emit:
            self.activated.emit(diff)

    def _clicked(self, idx) -> None:
        d = self.model.data(idx, Qt.ItemDataRole.UserRole) if idx.isValid() else None
        if d is not None:
            self._at_end = False
            self.activated.emit(d)

    def next(self) -> bool:
        """Differenza successiva; False (e segnalazione) alla fine dell'elenco."""
        vis = self.model.visible
        if not vis:
            return False
        cur = self.current()
        if cur is None or self._at_end:
            target = vis[0]
        else:
            i = vis.index(cur) if cur in vis else -1
            if i + 1 >= len(vis):
                self._at_end = True
                self.message.setText(_("Fine dell'elenco: premi di nuovo per ripartire dalla prima differenza."))
                self.message.setVisible(True)
                self.endReached.emit()
                return False
            target = vis[i + 1]
        self.select(target, emit=True)
        return True

    def previous(self) -> bool:
        vis = self.model.visible
        if not vis:
            return False
        cur = self.current()
        i = vis.index(cur) if cur in vis else len(vis)
        if i <= 0:
            self.message.setText(_("Inizio dell'elenco."))
            self.message.setVisible(True)
            return False
        self.select(vis[i - 1], emit=True)
        return True

    def copy_current(self) -> None:
        d = self.current()
        if d is not None:
            QGuiApplication.clipboard().setText(d.as_text())

    def _menu(self, pos) -> None:
        idx = self.list.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self)
        act = menu.addAction(icon("copy"), _("Copia"))
        act.triggered.connect(self.copy_current)
        menu.exec(self.list.viewport().mapToGlobal(pos))


# =============================================================================
# Vista affiancata (CMP-05)
# =============================================================================
def view_position(view: DocumentView) -> float:
    """Posizione continua (pagina + frazione) al 30% dell'altezza visibile."""
    if view.page_count == 0:
        return 0.0
    probe = view.viewport().height() * 0.3
    best = None
    for i in range(view.page_count):
        r = view.page_rect(i)
        if r.isEmpty():
            continue
        if r.top() <= probe <= r.bottom():
            return i + (probe - r.top()) / max(1.0, r.height())
        if r.top() > probe:
            return float(i) if best is None else best
        best = i + 0.9999
    return best if best is not None else 0.0


def set_view_position(view: DocumentView, pos: float) -> None:
    if view.page_count == 0:
        return
    i = max(0, min(int(pos), view.page_count - 1))
    frac = min(max(pos - i, 0.0), 0.9999)
    r = view.page_rect(i)
    probe = view.viewport().height() * 0.3
    target = r.top() + frac * r.height()
    bar = view.verticalScrollBar()
    bar.setValue(int(round(bar.value() + target - probe)))


class DiffPickTool(SelectTextTool):
    """Selezione del testo e clic su una differenza evidenziata."""

    def __init__(self, pane: "ComparePane"):
        super().__init__()
        self.pane = pane

    def press(self, view, ev):
        if ev.page is not None and ev.point is not None and ev.button == Qt.MouseButton.LeftButton:
            d = self.pane.diff_at(ev.page, ev.point)
            if d is not None:
                self.pane.picked.emit(d)
                return True
        return super().press(view, ev)


class DiffMap(QWidget):
    """Mappa delle differenze accanto alla barra di scorrimento."""

    clicked = Signal(object)

    def __init__(self, pane: "ComparePane"):
        super().__init__(pane)
        self.pane = pane
        self.setFixedWidth(10)
        self.setToolTip(_("Mappa delle differenze"))

    def _positions(self):
        view = self.pane.view
        n = max(1, view.page_count)
        for d in self.pane.diffs:
            locs = d.locations(self.pane.side)
            if not locs:
                continue
            p = locs[0][0]
            h = 842.0
            try:
                h = view.page_size(p)[1]
            except Exception:
                pass
            yield d, (p + locs[0][2] / max(1.0, h)) / n

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("surface_alt"))
        h = self.height()
        for d, frac in self._positions():
            c = kind_color(d.kind)
            p.fillRect(QRectF(1, frac * h - 1.5, self.width() - 2, 3), c)
        p.end()

    def mousePressEvent(self, ev) -> None:
        h = max(1, self.height())
        y = ev.position().y() / h
        best = None
        for d, frac in self._positions():
            if best is None or abs(frac - y) < best[0]:
                best = (abs(frac - y), d)
        if best is not None:
            self.clicked.emit(best[1])


class ComparePane(QWidget):
    """Un lato della vista affiancata: intestazione, vista documento e mappa."""

    picked = Signal(object)

    def __init__(self, side: str, document: Document, title: str, parent=None):
        super().__init__(parent)
        self.side = side
        self.document = document
        self.diffs: list[ce.Difference] = []
        self.selected: ce.Difference | None = None
        self.view = DocumentView(document, self)
        self.view.overlays.append(self._paint)
        self.view.set_tool(DiffPickTool(self))
        self.header = QLabel(title)
        self.header.setObjectName("compareHeader")
        self.header.setContentsMargins(8, 4, 8, 4)
        self.map = DiffMap(self)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.view, 1)
        row.addWidget(self.map)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.header)
        lay.addLayout(row, 1)

    def set_diffs(self, diffs: list[ce.Difference]) -> None:
        self.diffs = diffs
        self.view.viewport().update()
        self.map.update()

    def diff_at(self, page: int, point) -> ce.Difference | None:
        for d in self.diffs:
            for loc in d.locations(self.side):
                if loc[0] == page:
                    r = pymupdf.Rect(loc[1] - 2, loc[2] - 2, loc[3] + 2, loc[4] + 2)
                    if r.contains(point):
                        return d
        return None

    def _paint(self, view, p: QPainter, page: int) -> None:
        for d in self.diffs:
            locs = [loc for loc in d.locations(self.side) if loc[0] == page]
            if not locs:
                continue
            c = kind_color(d.kind)
            selected = d is self.selected
            width = 2.2 if selected else 1.2
            point = (d.kind == ce.ADDED and self.side == "a") or (d.kind == ce.DELETED and self.side == "b")
            for loc in locs:
                r = QRectF(loc[1], loc[2], loc[3] - loc[1], max(1.0, loc[4] - loc[2]))
                fill = QColor(c)
                fill.setAlpha(90 if selected else 45)
                if point:
                    # punto di inserimento sul lato opposto
                    pen = QPen(c, width + 0.8)
                    p.setPen(pen)
                    y = r.center().y()
                    p.drawLine(QPointF(r.right() - 4, y - 4), QPointF(r.right(), y))
                    p.drawLine(QPointF(r.right(), y), QPointF(r.right() + 4, y - 4))
                    p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
                    continue
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(fill)
                p.drawRect(r)
                pen = QPen(c, width)
                if d.kind == ce.ADDED:
                    p.setPen(pen)
                    p.drawLine(r.bottomLeft(), r.bottomRight())
                elif d.kind == ce.DELETED:
                    p.setPen(pen)
                    y = r.center().y()
                    p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
                else:
                    if d.kind == ce.FORMAT:
                        pen.setStyle(Qt.PenStyle.DashLine)
                    elif d.kind == ce.VISUAL:
                        pen.setStyle(Qt.PenStyle.DotLine)
                    p.setPen(pen)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawRect(r.adjusted(-1, -1, 1, 1))

    def show_diff(self, d: ce.Difference) -> None:
        locs = d.locations(self.side)
        if not locs:
            return
        loc = locs[0]
        self.view.ensure_visible(loc[0], pymupdf.Rect(loc[1:5]), margin=10_000)


# =============================================================================
# Vista sovrapposta (CMP-06)
# =============================================================================
COLOR_SCHEMES = {
    "redblue": ((220, 38, 38), (37, 99, 235)),
    "orangeblue": ((230, 120, 0), (0, 150, 200)),
}


def _to_qimage(arr) -> QImage:
    h, w = arr.shape[:2]
    return QImage(arr.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class OverlayCanvas(QWidget):
    nudged = Signal(float, float)    # spostamento in mm

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: QImage | None = None
        self.boxes: list[tuple[QRectF, QColor]] = []
        self.label = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag = None
        self.px_per_mm = 1.0

    def set_image(self, img: QImage | None, boxes, label: str = "") -> None:
        self.image = img
        self.boxes = boxes
        self.label = label
        if img is not None:
            self.setFixedSize(img.width() + 40, img.height() + 40)
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), theme.color("canvas"))
        if self.image is None:
            p.setPen(theme.color("text_muted"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.label)
            p.end()
            return
        p.drawImage(20, 20, self.image)
        for r, c in self.boxes:
            pen = QPen(c, 1.5)
            pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.translated(20, 20))
        if self.label:
            p.setPen(theme.color("text"))
            p.drawText(24, 16, self.label)
        p.end()

    def keyPressEvent(self, ev) -> None:
        step = 1.0 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.1
        moves = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0), Qt.Key.Key_Up: (0, -step),
                 Qt.Key.Key_Down: (0, step)}
        if ev.key() in moves:
            self.nudged.emit(*moves[ev.key()])
            return
        super().keyPressEvent(ev)

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        self._drag = ev.position()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None:
            return
        d = ev.position() - self._drag
        if abs(d.x()) >= self.px_per_mm * 0.1 or abs(d.y()) >= self.px_per_mm * 0.1:
            self._drag = ev.position()
            self.nudged.emit(round(d.x() / self.px_per_mm, 1), round(d.y() / self.px_per_mm, 1))

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None


class OverlayWidget(QWidget):
    """Sovrapposizione delle pagine corrispondenti di A e B."""

    MAX_TOGGLES_PER_SECOND = 3

    def __init__(self, session: "CompareWidget"):
        super().__init__(session)
        self.session = session
        self.offsets: dict[tuple[int, int], tuple[float, float]] = {}   # mm
        self.pair_combo = QComboBox()
        self.pair_combo.currentIndexChanged.connect(self.refresh)
        self.opacity_a = QSlider(Qt.Orientation.Horizontal)
        self.opacity_b = QSlider(Qt.Orientation.Horizontal)
        for s in (self.opacity_a, self.opacity_b):
            s.setRange(0, 100)
            s.setValue(100)
            s.setFixedWidth(90)
            s.valueChanged.connect(self.refresh)
        self.which = QComboBox()
        self.which.addItem(_("Entrambi"), "both")
        self.which.addItem(_("Solo A"), "a")
        self.which.addItem(_("Solo B"), "b")
        self.which.currentIndexChanged.connect(self.refresh)
        self.alternate = QCheckBox(_("Alterna"))
        self.alternate.toggled.connect(self._toggle_alternate)
        self.timer = QTimer(self)
        self.timer.setInterval(int(1000 / self.MAX_TOGGLES_PER_SECOND) + 40)
        self.timer.timeout.connect(self._alternate_tick)
        self._alt_state = "a"
        self.dx = QDoubleSpinBox()
        self.dy = QDoubleSpinBox()
        for s in (self.dx, self.dy):
            s.setRange(-50, 50)
            s.setSingleStep(0.1)
            s.setDecimals(1)
            s.setSuffix(" mm")
            s.valueChanged.connect(self._offset_changed)
        self.reset_button = QToolButton()
        self.reset_button.setToolTip(_("Reimposta allineamento"))
        bind_icon(self.reset_button, "rotate-ccw")
        self.reset_button.clicked.connect(self.reset_alignment)
        self.boxes = QCheckBox(_("Riquadri differenze"))
        self.boxes.toggled.connect(self.refresh)
        self.scheme = QComboBox()
        self.scheme.addItem(_("Rosso / blu"), "redblue")
        self.scheme.addItem(_("Arancione / azzurro"), "orangeblue")
        self.scheme.currentIndexChanged.connect(self.refresh)
        self.zoom = 1.0
        self.canvas = OverlayCanvas()
        self.canvas.nudged.connect(self.nudge)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidgetResizable(False)
        self.scroll = scroll
        bar = QToolBar()
        bar.setIconSize(QSize(18, 18))
        for w in (QLabel(_("Pagine")), self.pair_combo, QLabel(" A"), self.opacity_a, QLabel(" B"), self.opacity_b,
                  self.which, self.alternate, QLabel(_("Sposta B")), self.dx, self.dy, self.reset_button,
                  self.boxes, self.scheme):
            bar.addWidget(w)
        self.bar = bar
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(bar)
        lay.addWidget(scroll, 1)
        self._cache: dict = {}

    def set_result(self, result: ce.CompareResult) -> None:
        self._cache.clear()
        self.pair_combo.blockSignals(True)
        self.pair_combo.clear()
        units = list(result.pairs)
        pa_set = {p for p, _q in units}
        pb_set = {q for _p, q in units}
        units += [(p, None) for p in result.side_a.pages if p not in pa_set]
        units += [(None, q) for q in result.side_b.pages if q not in pb_set]
        for pa, pb in units:
            self.pair_combo.addItem(_("A {a} ↔ B {b}").format(a="–" if pa is None else pa + 1,
                                                             b="–" if pb is None else pb + 1), (pa, pb))
        self.pair_combo.blockSignals(False)
        self.refresh()

    def current_pair(self) -> tuple[int | None, int | None]:
        return self.pair_combo.currentData() or (None, None)

    def show_page_a(self, pa: int) -> None:
        for i in range(self.pair_combo.count()):
            if self.pair_combo.itemData(i)[0] == pa:
                self.pair_combo.setCurrentIndex(i)
                return

    def _render(self, doc: Document, pno: int):
        key = (id(doc), pno, self.zoom)
        if key not in self._cache:
            dpi = int(96 * self.zoom)
            self._cache[key] = ce.render_page(doc.pdf, pno, dpi)
        return self._cache[key]

    def _offset_changed(self) -> None:
        pair = self.current_pair()
        if None not in pair:
            self.offsets[pair] = (self.dx.value(), self.dy.value())
        self.refresh()

    def nudge(self, dx_mm: float, dy_mm: float) -> None:
        self.dx.setValue(self.dx.value() + dx_mm)
        self.dy.setValue(self.dy.value() + dy_mm)

    def reset_alignment(self) -> None:
        pair = self.current_pair()
        self.offsets.pop(pair, None)
        for s in (self.dx, self.dy):
            s.blockSignals(True)
            s.setValue(0.0)
            s.blockSignals(False)
        self.refresh()

    def _toggle_alternate(self, on: bool) -> None:
        if on:
            self.timer.start()
        else:
            self.timer.stop()
        self.refresh()

    def _alternate_tick(self) -> None:
        self._alt_state = "b" if self._alt_state == "a" else "a"
        self.refresh()

    def composite(self):
        """Immagine composta (array RGB) della coppia corrente."""
        session = self.session
        pa, pb = self.current_pair()
        if pa is None and pb is None:
            return None, 1.0
        which = self.which.currentData()
        if self.alternate.isChecked():
            which = self._alt_state
        img_a = scale = None
        img_b = None
        if pa is not None and which in ("both", "a"):
            img_a, scale = self._render(session.doc_a, pa)
        if pb is not None and which in ("both", "b"):
            img_b, scale = self._render(session.doc_b, pb)
        if img_a is None and img_b is None:
            ref = self._render(session.doc_a, pa) if pa is not None else self._render(session.doc_b, pb)
            img_b, scale = (None, ref[1])
            import numpy as np
            return np.full_like(ref[0], 255), scale
        dx, dy = self.offsets.get((pa, pb), (0.0, 0.0)) if None not in (pa, pb) else (0.0, 0.0)
        shift = (int(round(dx * MM * scale)), int(round(dy * MM * scale)))
        ca, cb = COLOR_SCHEMES[self.scheme.currentData()]
        out = ce.overlay_composite(img_a, img_b, self.opacity_a.value() / 100.0, self.opacity_b.value() / 100.0,
                                   ca, cb, shift_b=shift)
        return out, scale

    def refresh(self) -> None:
        if self.session.result is None:
            return
        pa, pb = self.current_pair()
        pair = (pa, pb)
        for s, v in zip((self.dx, self.dy), self.offsets.get(pair, (0.0, 0.0))):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        for w in (self.dx, self.dy, self.reset_button):
            w.setEnabled(None not in pair)
        try:
            arr, scale = self.composite()
        except Exception as e:  # una pagina danneggiata non deve chiudere la vista
            self.canvas.set_image(None, [], _("Impossibile mostrare la sovrapposizione: {e}").format(e=e))
            return
        if arr is None:
            self.canvas.set_image(None, [], _("Nessuna pagina da mostrare."))
            return
        label = ""
        if pa is None:
            label = _("Pagina presente solo in B")
        elif pb is None:
            label = _("Pagina presente solo in A")
        boxes = []
        if self.boxes.isChecked():
            for d in self.session.panel.visible_differences():
                if d.kind != ce.VISUAL:
                    continue
                for loc in d.locations("a"):
                    if loc[0] == pa:
                        boxes.append((QRectF(loc[1] * scale, loc[2] * scale, (loc[3] - loc[1]) * scale,
                                             (loc[4] - loc[2]) * scale), kind_color(d.kind)))
        self.canvas.px_per_mm = MM * scale
        self.canvas.set_image(_to_qimage(arr), boxes, label)

    def set_zoom(self, z: float) -> None:
        self.zoom = max(0.25, min(4.0, z))
        self._cache.clear()
        self.refresh()


# =============================================================================
# Sessione di confronto
# =============================================================================
class CompareWidget(QWidget):
    """Scheda «A ↔ B» mostrata al posto della vista documento."""

    closed = Signal()

    def __init__(self, ctx, result: ce.CompareResult, source_a: Source, source_b: Source, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.result = result
        self.source_a = source_a
        self.source_b = source_b
        self.doc_a = source_a.make_document()
        self.doc_b = source_b.make_document()
        self._syncing = False
        self._auto_vertical = False
        self.setObjectName("compareSession")

        self.toolbar = QToolBar()
        self.toolbar.setIconSize(QSize(18, 18))
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.title = QLabel(f"  {result.side_a.title}  ↔  {result.side_b.title}  ")
        self.title.setObjectName("compareTitle")
        self.toolbar.addWidget(self.title)
        self.toolbar.addSeparator()
        self.act_side = self._action(_("Affiancata"), "columns-2", lambda: self.set_layout("side"), checkable=True)
        self.act_overlay = self._action(_("Sovrapposta"), "layers", lambda: self.set_layout("overlay"),
                                        checkable=True)
        self.act_vertical = self._action(_("Alto/basso"), "rows-2", self._toggle_orientation, checkable=True)
        self.act_sync = self._action(_("Sincronizza scorrimento"), "link", self._sync_toggled, checkable=True)
        self.act_sync.setChecked(True)
        self.toolbar.addSeparator()
        self._action(_("Riduci"), "zoom-out", self.zoom_out, text_only=False)
        self._action(_("Ingrandisci"), "zoom-in", self.zoom_in, text_only=False)
        self._action(_("Adatta alla larghezza"), "move-horizontal", lambda: self.fit(FIT_WIDTH), text_only=False)
        self._action(_("Adatta alla pagina"), "maximize", lambda: self.fit(FIT_PAGE), text_only=False)
        self.toolbar.addSeparator()
        self._action(_("Differenza precedente"), "chevron-up", lambda: self.panel.previous(), text_only=False)
        self._action(_("Differenza successiva"), "chevron-down", lambda: self.panel.next(), text_only=False)
        self.toolbar.addSeparator()
        self.act_report = self._action(_("Esporta rapporto"), "file-output", self.export_report)
        self._action(_("Esporta B annotato"), "message-square-text", self.export_annotated, text_only=False)
        self._action(_("Salva sessione"), "save", self.save_session, text_only=False)
        self._action(_("Abbina pagine…"), "link-2", self.manual_pair, text_only=False)
        self.toolbar.addSeparator()
        self._action(_("Chiudi confronto"), "x", self.close_session)

        self.pane_a = ComparePane("a", self.doc_a, _("A · {n}").format(n=result.side_a.title))
        self.pane_b = ComparePane("b", self.doc_b, _("B · {n}").format(n=result.side_b.title))
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.pane_a)
        self.splitter.addWidget(self.pane_b)
        self.splitter.setChildrenCollapsible(False)
        self.overlay = OverlayWidget(self)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.splitter)
        self.stack.addWidget(self.overlay)
        self.panel = DiffPanel()
        outer = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(self.stack)
        outer.addWidget(self.panel)
        outer.setStretchFactor(0, 4)
        outer.setStretchFactor(1, 1)
        outer.setSizes([900, 300])
        self.outer = outer
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toolbar)
        lay.addWidget(outer, 1)

        self.panel.activated.connect(self.go_to_difference)
        self.panel.filtersChanged.connect(self._apply_filters)
        for pane in (self.pane_a, self.pane_b):
            pane.picked.connect(self._picked)
            pane.map.clicked.connect(self._picked)
        self.pane_a.view.verticalScrollBar().valueChanged.connect(lambda _v: self._sync_from(self.pane_a))
        self.pane_b.view.verticalScrollBar().valueChanged.connect(lambda _v: self._sync_from(self.pane_b))
        self.pane_a.view.zoomChanged.connect(lambda z: self._sync_zoom(self.pane_a, z))
        self.pane_b.view.zoomChanged.connect(lambda z: self._sync_zoom(self.pane_b, z))
        QShortcut(QKeySequence("Alt+Down"), self, lambda: self.panel.next())
        QShortcut(QKeySequence("Alt+Up"), self, lambda: self.panel.previous())
        self.set_result(result)
        self.set_layout("side")

    def _action(self, text, icon_name, slot, checkable=False, text_only=True):
        act = self.toolbar.addAction(text)
        bind_icon(act, icon_name)
        act.setCheckable(checkable)
        act.setToolTip(text)
        act.triggered.connect(lambda _c=False: slot())
        if not text_only:
            btn = self.toolbar.widgetForAction(act)
            if btn is not None:
                btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        return act

    # ---------------------------------------------------------------- stato
    def set_result(self, result: ce.CompareResult) -> None:
        self.result = result
        self.panel.set_result(result)
        self.overlay.set_result(result)
        self._apply_filters()

    def _apply_filters(self) -> None:
        visible = self.panel.visible_differences()
        self.pane_a.set_diffs(visible)
        self.pane_b.set_diffs(visible)
        if self.stack.currentWidget() is self.overlay:
            self.overlay.refresh()

    def set_layout(self, layout: str) -> None:
        self.act_side.setChecked(layout == "side")
        self.act_overlay.setChecked(layout == "overlay")
        self.act_vertical.setEnabled(layout == "side")
        self.act_sync.setEnabled(layout == "side")
        self.stack.setCurrentWidget(self.splitter if layout == "side" else self.overlay)
        if layout == "overlay":
            self.overlay.show_page_a(self.pane_a.view.current_page)
            self.overlay.refresh()

    def layout_mode(self) -> str:
        return "overlay" if self.stack.currentWidget() is self.overlay else "side"

    def _toggle_orientation(self) -> None:
        self._auto_vertical = False
        self.splitter.setOrientation(Qt.Orientation.Vertical if self.act_vertical.isChecked()
                                     else Qt.Orientation.Horizontal)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        narrow = self.width() < 900
        if narrow and self.splitter.orientation() == Qt.Orientation.Horizontal:
            self._auto_vertical = True
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            self.act_vertical.setChecked(True)
        elif not narrow and self._auto_vertical:
            self._auto_vertical = False
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            self.act_vertical.setChecked(False)

    # ---------------------------------------------------------------- sincronizzazione
    def _sync_toggled(self) -> None:
        if self.act_sync.isChecked():
            self._sync_from(self.pane_a)

    def _sync_from(self, pane: ComparePane) -> None:
        if self._syncing or not self.act_sync.isChecked():
            return
        self._syncing = True
        try:
            pos = view_position(pane.view)
            if pane is self.pane_a:
                set_view_position(self.pane_b.view, self.result.map_a_to_b(pos))
            else:
                set_view_position(self.pane_a.view, self.result.map_b_to_a(pos))
        finally:
            self._syncing = False

    def _sync_zoom(self, pane: ComparePane, zoom: float) -> None:
        if self._syncing or not self.act_sync.isChecked():
            return
        other = self.pane_b if pane is self.pane_a else self.pane_a
        if abs(other.view.zoom - zoom) < 1e-4:
            return
        self._syncing = True
        try:
            if pane.view.fit_mode:
                other.view.set_fit(pane.view.fit_mode)
            else:
                other.view.set_zoom(zoom)
        finally:
            self._syncing = False
        self._sync_from(pane)

    def zoom_in(self) -> None:
        self.pane_a.view.zoom_in()
        if not self.act_sync.isChecked():
            self.pane_b.view.zoom_in()
        self.overlay.set_zoom(self.overlay.zoom * 1.25) if self.layout_mode() == "overlay" else None

    def zoom_out(self) -> None:
        self.pane_a.view.zoom_out()
        if not self.act_sync.isChecked():
            self.pane_b.view.zoom_out()
        self.overlay.set_zoom(self.overlay.zoom / 1.25) if self.layout_mode() == "overlay" else None

    def fit(self, mode: str) -> None:
        self._syncing = True
        try:
            self.pane_a.view.set_fit(mode)
            self.pane_b.view.set_fit(mode)
        finally:
            self._syncing = False
        self._sync_from(self.pane_a)

    # ---------------------------------------------------------------- differenze
    def _picked(self, d: ce.Difference) -> None:
        self.panel.select(d)
        self.go_to_difference(d)

    def go_to_difference(self, d: ce.Difference) -> None:
        """Porta entrambi i riquadri sulla differenza e la evidenzia."""
        self.pane_a.selected = d
        self.pane_b.selected = d
        self._syncing = True
        try:
            self.pane_a.show_diff(d)
            self.pane_b.show_diff(d)
        finally:
            self._syncing = False
        self.panel.select(d)
        for pane in (self.pane_a, self.pane_b):
            pane.view.viewport().update()
        if self.layout_mode() == "overlay":
            loc = d.locations("a")
            if loc:
                self.overlay.show_page_a(loc[0][0])

    # ---------------------------------------------------------------- comandi
    def manual_pair(self) -> None:
        pa, ok = QInputDialog.getInt(self, _("Abbina pagine"), _("Pagina di A:"),
                                     self.pane_a.view.current_page + 1, 1, self.result.side_a.page_count)
        if not ok:
            return
        pb, ok = QInputDialog.getInt(self, _("Abbina pagine"), _("Pagina di B corrispondente:"),
                                     self.pane_b.view.current_page + 1, 1, self.result.side_b.page_count)
        if not ok:
            return
        self.apply_pair(pa - 1, pb - 1)

    def apply_pair(self, pa: int, pb: int) -> None:
        try:
            self.ctx.run_task(_("Ricalcolo delle pagine abbinate…"), lambda progress: ce.recompute_pair(
                self.result, self.source_a.data, self.source_b.data, pa, pb, self.source_a.password,
                self.source_b.password))
        except Exception as e:
            self.ctx.error(_("Abbinamento non riuscito"), str(e))
            return
        self.set_result(self.result)

    def export_report(self, path: str | None = None, content: str = rp.CONTENT_PAGES,
                      layout: str = rp.LAYOUT_SIDE, apply_filters: bool = True) -> str | None:
        if path is None:
            dlg = ReportDialog(self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return None
            content, layout, apply_filters, open_after = dlg.values()
            path = self.ctx.ask_save_path(_("Esporta rapporto"), rp.suggested_name(self.result))
            if not path:
                return None
        else:
            open_after = False
        diffs = self.panel.visible_differences() if apply_filters else self.result.differences
        note = self.panel.filters_note() if apply_filters else ""
        try:
            data = self.ctx.run_task(_("Generazione del rapporto…"), rp.build_report, self.result,
                                     self.source_a.data, self.source_b.data, diffs, content, layout, note,
                                     self.source_a.password, self.source_b.password)
            atomic_write(path, data)
        except OperationCancelled:
            self.ctx.status(_("Generazione del rapporto annullata."))
            return None
        except Exception as e:
            self.ctx.error(_("Rapporto non esportato"), str(e))
            return None
        self.ctx.status(_("Rapporto salvato: {p}").format(p=path))
        if open_after:
            self.ctx.open_path(path)
        return path

    def export_annotated(self, path: str | None = None) -> str | None:
        if path is None:
            stem = os.path.splitext(self.result.side_b.label)[0]
            path = self.ctx.ask_save_path(_("Esporta B annotato"), f"{stem}_annotato.pdf")
            if not path:
                return None
        try:
            data, n = self.ctx.run_task(_("Creazione di B annotato…"), rp.build_annotated_b, self.result,
                                        self.source_b.data, self.panel.visible_differences(),
                                        self.source_b.password)
            atomic_write(path, data)
        except OperationCancelled:
            return None
        except Exception as e:
            self.ctx.error(_("Esportazione non riuscita"), str(e))
            return None
        self.ctx.status(_("Creato B annotato con {n} commenti: {p}").format(n=n, p=path))
        return path

    def session_data(self) -> dict:
        view = {"layout": self.layout_mode(), "sync": self.act_sync.isChecked(),
                "vertical": self.act_vertical.isChecked(), "zoom": self.pane_a.view.zoom,
                "kinds": sorted(self.panel.visible_kinds())}
        return ce.session_to_dict(self.result, self.overlay.offsets, view)

    def save_session(self, path: str | None = None) -> str | None:
        if path is None:
            stem = os.path.splitext(rp.suggested_name(self.result))[0]
            path = self.ctx.ask_save_path(_("Salva sessione di confronto"), stem + ".foliantcmp", SESSION_FILTER,
                                          ".foliantcmp")
            if not path:
                return None
        try:
            atomic_write(path, json.dumps(self.session_data(), ensure_ascii=False, indent=1).encode("utf-8"))
        except Exception as e:
            self.ctx.error(_("Sessione non salvata"), str(e))
            return None
        self.ctx.status(_("Sessione salvata: {p}").format(p=path))
        return path

    def restore_view_state(self, data: dict) -> None:
        self.overlay.offsets.update(ce.parse_offsets(data))
        view = data.get("view") or {}
        kinds = view.get("kinds")
        if kinds is not None:
            for k, cb in self.panel.filters.items():
                cb.setChecked(k in kinds)
        if "sync" in view:
            self.act_sync.setChecked(bool(view["sync"]))
        if view.get("vertical"):
            self.act_vertical.setChecked(True)
            self._toggle_orientation()
        statuses = data.get("status") or {}
        for d in self.result.differences:
            st = statuses.get(str(d.id))
            if st:
                d.status, d.note = st.get("status", ""), st.get("note", "")
        self.set_layout(view.get("layout", "side"))
        self.overlay.refresh()

    def close_session(self) -> None:
        self.ctx.set_central_widget(None)
        self.closed.emit()

    def dispose(self) -> None:
        for pane in (self.pane_a, self.pane_b):
            pane.view.dispose()
        for doc in (self.doc_a, self.doc_b):
            doc.close()


class ReportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Esporta rapporto"))
        self.content = QComboBox()
        self.content.addItem(_("Riepilogo e pagine con differenze"), rp.CONTENT_PAGES)
        self.content.addItem(_("Solo riepilogo"), rp.CONTENT_SUMMARY)
        self.content.addItem(_("Riepilogo e tutte le pagine"), rp.CONTENT_ALL)
        self.layout_box = QComboBox()
        self.layout_box.addItem(_("A e B affiancati"), rp.LAYOUT_SIDE)
        self.layout_box.addItem(_("Solo B evidenziato"), rp.LAYOUT_B)
        self.filters = QCheckBox(_("Applica i filtri correnti"))
        self.filters.setChecked(True)
        self.open_after = QCheckBox(_("Apri il rapporto al termine"))
        form = QFormLayout(self)
        form.addRow(_("Contenuto"), self.content)
        form.addRow(_("Disposizione"), self.layout_box)
        form.addRow(self.filters)
        form.addRow(self.open_after)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return (self.content.currentData(), self.layout_box.currentData(), self.filters.isChecked(),
                self.open_after.isChecked())


# =============================================================================
# Controllore
# =============================================================================
class CompareController:
    """Avvia i confronti e gestisce la sessione corrente."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.widget: CompareWidget | None = None
        self.last_options = ce.CompareOptions()

    def start(self, view: DocumentView | None = None) -> CompareWidget | None:
        dlg = CompareSetupDialog(self.ctx, self.ctx.window, self.last_options)
        if view is not None:
            dlg.side_a.select_view(view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        self.last_options = dlg.options()
        ia, ib, sa, sb = dlg.inputs()
        return self.run(ia, ib, sa, sb, self.last_options)

    def run(self, ia: ce.SideInput, ib: ce.SideInput, sa: Source, sb: Source,
            options: ce.CompareOptions) -> CompareWidget | None:
        try:
            result = self.ctx.run_task(_("Confronto in corso…"), ce.run_compare, ia, ib, options)
        except OperationCancelled:
            self.ctx.status(_("Confronto annullato."))
            return None
        except DocumentError as e:
            self.ctx.error(_("Confronto non riuscito"), str(e))
            return None
        except Exception as e:  # nessun errore deve chiudere l'applicazione
            self.ctx.error(_("Confronto non riuscito"), _("Errore imprevisto: {e}").format(e=e))
            return None
        return self.show(result, sa, sb)

    def show(self, result: ce.CompareResult, sa: Source, sb: Source) -> CompareWidget:
        self.close()
        self.widget = CompareWidget(self.ctx, result, sa, sb)
        self.widget.closed.connect(self._closed)
        if self.ctx.mode("strumenti") is not None:
            self.ctx.set_mode("strumenti")
        self.ctx.set_central_widget(self.widget)
        QTimer.singleShot(0, lambda: self.widget and self.widget.fit(FIT_WIDTH))
        return self.widget

    def reshow(self) -> None:
        if self.widget is not None:
            self.ctx.set_central_widget(self.widget)

    def _closed(self) -> None:
        self.close()

    def close(self) -> None:
        w = self.widget
        self.widget = None
        if w is not None:
            if self.ctx.central_widget() is w:
                self.ctx.set_central_widget(None)
            w.dispose()
            w.setParent(None)
            w.deleteLater()

    def open_session(self, path: str | None = None) -> CompareWidget | None:
        if path is None:
            paths = self.ctx.ask_open_paths(_("Apri sessione di confronto"), SESSION_FILTER, multiple=False)
            if not paths:
                return None
            path = paths[0]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("format") != "foliant-compare":
                raise ValueError(_("il file non è una sessione di confronto"))
        except Exception as e:
            self.ctx.error(_("Sessione non apribile"), str(e))
            return None
        sources = []
        for key in ("a", "b"):
            side = data.get(key) or {}
            p = side.get("path")
            if not p or not os.path.exists(p):
                self.ctx.error(_("Sessione non apribile"), _("Il file «{f}» non è più disponibile.").format(
                    f=p or side.get("label", "?")))
                return None
            try:
                src = Source.from_file(p, self.ctx.window)
            except DocumentError as e:
                self.ctx.error(_("Sessione non apribile"), str(e))
                return None
            if src is None:
                return None
            if ce.sha256(src.data) != side.get("sha256"):
                self.ctx.info(_("File modificato"), _("«{f}» è cambiato dopo il salvataggio della sessione: il "
                                                       "confronto viene ricalcolato.").format(f=src.label))
            sources.append((src, side.get("pages")))
        options = ce.CompareOptions.from_dict(data.get("options") or {})
        (sa, ra), (sb, rb) = sources
        ia = ce.SideInput(sa.data, sa.label, sa.path, sa.password, tuple(ra) if ra else None)
        ib = ce.SideInput(sb.data, sb.label, sb.path, sb.password, tuple(rb) if rb else None)
        widget = self.run(ia, ib, sa, sb, options)
        if widget is not None:
            widget.restore_view_state(data)
        return widget
