"""Dialoghi per filigrane, sfondi, numeri di pagina, Bates, intestazioni e
etichette di pagina.

Ogni dialogo è costruibile da solo (`WatermarkDialog(parent, document, selected)`),
espone `options()` (argomenti per la funzione del motore) e `apply()` (esegue
la modifica come unico passo annullabile). Le funzioni `open_*_dialog(ctx,
view, selected)` sono i punti d'ingresso per azioni e menu.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING

import pymupdf
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from foliant.i18n import pgettext
from foliant.i18n import format_date
from foliant.api import OperationCancelled
from foliant.api import ENGINE_LOCK, _, icon, theme

from . import fields, stamp
from .engine import MM, PageOpError, format_ranges, parse_ranges

if TYPE_CHECKING:
    from foliant.api import Context, Document, DocumentView

PREVIEW_SIZE = QSize(250, 330)


def _image_filter() -> str:
    return _("Immagini") + " (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)"


def _accepted(result) -> bool:
    value = getattr(result, "value", result)
    try:
        return int(value) == 1
    except (TypeError, ValueError):
        return False


# =========================================================================
# Widget riutilizzabili
# =========================================================================

class PageScope(QGroupBox):
    """Scelta delle pagine: tutte, selezionate o intervallo (validato al volo)."""

    changed = Signal()

    def __init__(self, page_count: int, selected: list[int] | None = None, labels: list[str] | None = None,
                 parent: QWidget | None = None):
        super().__init__(_("Pagine"), parent)
        self.page_count = page_count
        self.selected = sorted({i for i in (selected or []) if 0 <= i < page_count})
        self.labels = labels
        self.all = QRadioButton(_("Tutte"))
        self.sel = QRadioButton(_("Pagine selezionate ({n})").format(n=len(self.selected)))
        self.rng = QRadioButton(_("Intervallo"))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(_("es. 1-3, 5, 8-"))
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.hide()
        self.group = QButtonGroup(self)
        for b in (self.all, self.sel, self.rng):
            self.group.addButton(b)
        lay = QGridLayout(self)
        lay.addWidget(self.all, 0, 0)
        lay.addWidget(self.sel, 0, 1)
        lay.addWidget(self.rng, 1, 0)
        lay.addWidget(self.edit, 1, 1)
        lay.addWidget(self.error, 2, 0, 1, 2)
        lay.setColumnStretch(1, 1)
        if self.selected:
            self.edit.setText(format_ranges(self.selected))
        if len(self.selected) > 1:
            self.sel.setChecked(True)
        else:
            self.all.setChecked(True)
        if not self.selected:
            self.sel.setEnabled(False)
        self.group.buttonToggled.connect(lambda *_a: self._update())
        self.edit.textChanged.connect(self._on_text)
        self._update()

    def _on_text(self) -> None:
        if not self.rng.isChecked():
            self.rng.setChecked(True)  # chiama _update
        else:
            self._update()

    def set_mode(self, mode: str, text: str | None = None) -> None:
        """mode: "all", "selected" o "range"."""
        if text is not None:
            self.edit.blockSignals(True)
            self.edit.setText(text)
            self.edit.blockSignals(False)
        {"all": self.all, "selected": self.sel, "range": self.rng}[mode].setChecked(True)
        self._update()

    def _range_error(self) -> str | None:
        try:
            parse_ranges(self.edit.text(), self.page_count, labels=None)
        except PageOpError as e:
            return str(e)
        return None

    def _update(self) -> None:
        self.edit.setEnabled(True)
        err = self._range_error() if self.rng.isChecked() else None
        self.error.setText(err or "")
        self.error.setVisible(bool(err))
        self.changed.emit()

    def is_valid(self) -> bool:
        if self.rng.isChecked():
            return self._range_error() is None
        if self.sel.isChecked():
            return bool(self.selected)
        return self.page_count > 0

    def pages(self) -> list[int] | None:
        """Indici scelti (None = tutte)."""
        if self.rng.isChecked():
            return parse_ranges(self.edit.text(), self.page_count)
        if self.sel.isChecked():
            return list(self.selected)
        return None

    def first_page(self) -> int:
        pages = self.pages() if self.is_valid() else None
        return pages[0] if pages else 0


class ColorButton(QToolButton):
    """Pulsante con campione di colore; colore come terna RGB 0–1."""

    changed = Signal()

    def __init__(self, rgb=(0.0, 0.0, 0.0), parent: QWidget | None = None):
        super().__init__(parent)
        self.setToolTip(_("Scegli colore"))
        self.setFixedSize(QSize(44, 26))
        self._rgb = tuple(rgb)
        self.clicked.connect(self._pick)
        self._paint()

    def color(self) -> tuple[float, float, float]:
        return self._rgb  # type: ignore[return-value]

    def set_color(self, rgb) -> None:
        self._rgb = tuple(float(c) for c in rgb)
        self._paint()
        self.changed.emit()

    def _paint(self) -> None:
        q = QColor.fromRgbF(*self._rgb)
        self.setStyleSheet(
            f"QToolButton {{ background: {q.name()}; border: 1px solid {theme.hex_color('border')};"
            f" border-radius: 5px; }}")

    def _pick(self) -> None:
        q = QColorDialog.getColor(QColor.fromRgbF(*self._rgb), self, _("Scegli colore"))
        if q.isValid():
            self.set_color((q.redF(), q.greenF(), q.blueF()))


class PositionPicker(QWidget):
    """Griglia 3×3 per scegliere l'ancoraggio sulla pagina."""

    changed = Signal()

    def __init__(self, value: str = "center", allowed: tuple[str, ...] = stamp.POSITIONS,
                 parent: QWidget | None = None):
        super().__init__(parent)
        names = {
            "top-left": _("In alto a sinistra"), "top-center": _("In alto al centro"),
            "top-right": _("In alto a destra"), "middle-left": _("Al centro a sinistra"),
            "center": _("Al centro"), "middle-right": _("Al centro a destra"),
            "bottom-left": _("In basso a sinistra"), "bottom-center": _("In basso al centro"),
            "bottom-right": _("In basso a destra"),
        }
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        accent, border, surface = theme.hex_color("accent"), theme.hex_color("border"), theme.hex_color("surface")
        for k, pos in enumerate(stamp.POSITIONS):
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(QSize(24, 18))
            b.setToolTip(names[pos])
            b.setAccessibleName(names[pos])
            b.setStyleSheet(
                f"QToolButton {{ background: {surface}; border: 1px solid {border}; border-radius: 3px; }}"
                f"QToolButton:checked {{ background: {accent}; border-color: {accent}; }}")
            b.setEnabled(pos in allowed)
            self.group.addButton(b, k)
            self.buttons[pos] = b
            lay.addWidget(b, k // 3, k % 3)
        lay.setColumnStretch(3, 1)
        self.set_value(value)
        self.group.idToggled.connect(lambda _i, on: on and self.changed.emit())

    def value(self) -> str:
        for pos, b in self.buttons.items():
            if b.isChecked():
                return pos
        return "center"

    def set_value(self, pos: str) -> None:
        b = self.buttons.get(pos)
        if b is not None:
            b.setChecked(True)


class OpacitySlider(QWidget):
    changed = Signal()

    def __init__(self, percent: int = 100, parent: QWidget | None = None):
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 100)
        self.slider.setValue(percent)
        self.label = QLabel()
        self.label.setMinimumWidth(40)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.label)
        self.slider.valueChanged.connect(self._sync)
        self._sync()

    def _sync(self) -> None:
        self.label.setText(f"{self.slider.value()}%")
        self.changed.emit()

    def value(self) -> float:
        return self.slider.value() / 100.0

    def set_value(self, fraction: float) -> None:
        self.slider.setValue(int(round(fraction * 100)))


def _font_combo(current: str = "helv") -> QComboBox:
    combo = QComboBox()
    for key, name in stamp.FONTS.items():
        combo.addItem(_(name), key)
    idx = combo.findData(current)
    combo.setCurrentIndex(max(0, idx))
    return combo


def _spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


class ImageChooser(QWidget):
    """Campo percorso immagine con pulsante «Scegli…»."""

    changed = Signal()

    def __init__(self, ctx: Context | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(_("Nessuna immagine scelta"))
        self.edit.setReadOnly(True)
        self.button = QPushButton(icon("folder-open"), _("Scegli…"))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        self.button.clicked.connect(self._pick)

    def _pick(self) -> None:
        if self.ctx is not None:
            paths = self.ctx.ask_open_paths(_("Scegli immagine"), _image_filter(), multiple=False)
        else:
            path, _f = QFileDialog.getOpenFileName(self, _("Scegli immagine"), "", _image_filter())
            paths = [path] if path else []
        if paths:
            self.set_path(paths[0])

    def path(self) -> str:
        return self.edit.text()

    def set_path(self, path: str) -> None:
        self.edit.setText(path)
        self.edit.setToolTip(path)
        self.changed.emit()


def _hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
    return lab


def _settings_of(ctx, parent):
    """QSettings dell'applicazione (dal contesto o dalla finestra), oppure None."""
    for source in (ctx, getattr(parent, "ctx", None)):
        if source is None:
            continue
        try:
            return source.settings()
        except Exception:
            continue
    return None


class FieldMenuButton(QToolButton):
    """Pulsante «Inserisci campo» (PAG-32): inserisce un segnaposto nell'ultimo campo usato."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(_("Inserisci campo"))
        self.setIcon(icon("braces"))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._targets: list[QWidget] = []
        self._last: QWidget | None = None
        menu = QMenu(self)
        for token, desc in fields.MENU:
            if token in ("{n}", "{segnalibro}", "{intervallo}", "{bates}"):
                continue  # campi dei nomi di file, non delle pagine
            token = fields.display_code(token)
            act = menu.addAction(f"{_(desc)}  {token}")
            act.triggered.connect(lambda _c=False, t=token: self.insert(t))
        self.setMenu(menu)

    def watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            self._targets.append(w)
            w.cursorPositionChanged.connect(lambda *_a, w=w: setattr(self, "_last", w))
        if self._last is None and widgets:
            self._last = widgets[0]

    def insert(self, token: str) -> None:
        w = self._last
        if w is None:
            return
        if isinstance(w, QPlainTextEdit):
            w.insertPlainText(token)
        elif isinstance(w, QComboBox):
            w.lineEdit().insert(token)
        else:
            w.insert(token)
        w.setFocus()


def _template_error(*texts: str) -> str | None:
    for t in texts:
        err = fields.validate_template(t or "")
        if err:
            return err
    return None


class VisibilityCombo(QComboBox):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        for key, text in (("both", _("A schermo e in stampa")), ("screen", _("Solo a schermo")),
                          ("print", _("Solo in stampa"))):
            self.addItem(text, key)

    def value(self) -> str:
        return self.currentData()

    def set_value(self, v: str) -> None:
        self.setCurrentIndex(max(0, self.findData(v)))


class PdfPageChooser(QWidget):
    """File PDF sorgente e pagina (carta intestata, filigrana da PDF)."""

    changed = Signal()

    def __init__(self, ctx: Context | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.edit = QLineEdit()
        self.edit.setReadOnly(True)
        self.edit.setPlaceholderText(_("Nessun PDF scelto"))
        self.button = QPushButton(icon("folder-open"), _("Scegli…"))
        self.page = _spin(1, 1, 1)
        self.page.setPrefix(_("pag. "))
        self.pages = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.button)
        lay.addWidget(self.page)
        self.button.clicked.connect(self._pick)
        self.page.valueChanged.connect(lambda *_a: self.changed.emit())

    def _pick(self) -> None:
        if self.ctx is not None:
            paths = self.ctx.ask_open_paths(_("Scegli PDF"), "PDF (*.pdf)", multiple=False)
        else:
            path, _f = QFileDialog.getOpenFileName(self, _("Scegli PDF"), "", "PDF (*.pdf)")
            paths = [path] if path else []
        if paths:
            self.set_path(paths[0])

    def path(self) -> str:
        return self.edit.text()

    def page_index(self) -> int:
        return self.page.value() - 1

    def set_path(self, path: str, page: int = 0) -> None:
        self.pages = 0
        try:
            with ENGINE_LOCK:
                d = pymupdf.open(path)
                self.pages = d.page_count if d.is_pdf and not d.needs_pass else 0
                d.close()
        except Exception:
            self.pages = 0
        self.edit.setText(path)
        self.edit.setToolTip(path)
        self.page.setRange(1, max(1, self.pages))
        self.page.setValue(min(max(1, page + 1), max(1, self.pages)))
        self.changed.emit()

    def error(self) -> str | None:
        if not self.path():
            return _("Scegli un PDF.")
        if self.pages <= 0:
            return _("Il PDF sorgente non è leggibile.")
        return None


def _warning_label() -> QLabel:
    lab = QLabel()
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('warning')};")
    lab.hide()
    return lab


def _mm_spin(lo: float, hi: float, value: float) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(1.0)
    s.setValue(value)
    s.setSuffix(" mm")
    return s


# =========================================================================
# Dialogo base
# =========================================================================

class StampDialog(QDialog):
    """Struttura comune: modulo a sinistra, anteprima a destra, scelta pagine."""

    TITLE = ""
    UNDO_LABEL = ""
    PREVIEW = True
    SCOPE = True

    def __init__(self, parent: QWidget | None, document: Document, selected: list[int] | None = None, *,
                 ctx: Context | None = None, current_page: int = 0):
        super().__init__(parent)
        self.document = document
        self.ctx = ctx
        self.current_page = current_page
        self.setWindowTitle(_(self.TITLE))
        self.setModal(True)
        with ENGINE_LOCK:
            self.page_count = document.pdf.page_count
        self.scope = PageScope(self.page_count, selected) if self.SCOPE else None

        self.form_box = QWidget()
        self.form = QVBoxLayout(self.form_box)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setSpacing(10)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.hide()

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Applica"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.addWidget(self.form_box)
        if self.scope is not None:
            left.addWidget(self.scope)
        left.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(16)
        body.addLayout(left, 1)
        self.preview_label: QLabel | None = None
        if self.PREVIEW:
            self.preview_label = QLabel()
            self.preview_label.setFixedSize(PREVIEW_SIZE)
            self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_label.setStyleSheet(
                f"background: {theme.hex_color('canvas')}; border: 1px solid {theme.hex_color('border')};"
                f" border-radius: 6px; color: {theme.hex_color('text_muted')};")
            col = QVBoxLayout()
            cap = _hint(_("Anteprima"))
            col.addWidget(cap)
            col.addWidget(self.preview_label)
            self.preview_caption = _hint("")
            self.preview_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(self.preview_caption)
            col.addStretch(1)
            body.addLayout(col)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)
        root.addLayout(body, 1)
        self.warning = _warning_label()
        root.addWidget(self.warning)
        root.addWidget(self.error)
        root.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh_preview)

        self.build()
        self.replace_box = None
        if self.REPLACE_KIND:
            # PAG-33/34/35: se il documento ha già questi elementi di Foliant, si sostituiscono
            # (niente serie sovrapposte); la casella compare solo quando servono.
            with ENGINE_LOCK:
                present = any(self.REPLACE_KIND in kinds for kinds in stamp.list_watermarks(self.document.pdf).values())
            self.replace_box = QCheckBox(_(self.REPLACE_TEXT))
            self.replace_box.setChecked(True)
            self.replace_box.setVisible(present)
            self.replace_present = present
            self.form.addWidget(self.replace_box)
            self.replace_box.toggled.connect(self.on_changed)
        if self.scope is not None:
            self.scope.changed.connect(self.on_changed)
        self.on_changed()
        if self.PREVIEW:
            self.refresh_preview()

    # -- da specializzare -------------------------------------------------
    def build(self) -> None:
        """Aggiunge i controlli a `self.form`."""

    def engine_options(self) -> dict:
        """Argomenti per la funzione del motore (senza `pages`)."""
        return {}

    def validate(self) -> str | None:
        """Messaggio d'errore se le opzioni non sono valide."""
        return None

    def warnings(self) -> list[str]:
        """Avvisi che non bloccano la conferma (es. PDF/A-1, scansioni)."""
        return []

    def preview_fields(self, index: int) -> dict:
        """Valori dei campi PAG-32 della pagina `index` del documento vero, per l'anteprima
        disegnata su una copia di una sola pagina."""
        with ENGINE_LOCK:
            pdf = self.document.pdf
            values = fields.page_values(pdf, index, fields.document_values(pdf, self.document.display_name))
        values["n"] = values["pagina"]
        return values

    def run(self, pdf: pymupdf.Document, opts: dict):
        """Applica le opzioni a `pdf`."""
        raise NotImplementedError

    def preview_options(self, opts: dict, page_index: int) -> dict:
        """Adatta le opzioni per l'anteprima di una singola pagina copiata."""
        return opts

    def target_pages(self) -> list[int]:
        if self.scope is None or not self.scope.is_valid():
            return list(range(self.page_count))
        pages = self.scope.pages()
        return list(range(self.page_count)) if pages is None else pages

    def preview_index(self) -> int:
        """Pagina mostrata nell'anteprima: la prima interessata."""
        if self.scope is None:
            return self.current_page
        return self.scope.first_page()

    # -- comune -------------------------------------------------------------
    def connect_changed(self, *signals) -> None:
        for sig in signals:
            sig.connect(self.on_changed)

    REPLACE_KIND: str | None = None
    REPLACE_TEXT = ""

    def options(self) -> dict:
        opts = self.engine_options()
        if self.scope is not None:
            opts["pages"] = self.scope.pages() if self.scope.is_valid() else None
        if self.REPLACE_KIND and getattr(self, "replace_box", None) is not None:
            opts["replace_existing"] = self.replace_present and self.replace_box.isChecked()
        return opts

    def _remove_existing(self, pdf, opts: dict) -> None:
        if opts.pop("replace_existing", False) and self.REPLACE_KIND:
            stamp.remove_watermarks(pdf, None, kind=self.REPLACE_KIND)

    def is_valid(self) -> bool:
        if self.scope is not None and not self.scope.is_valid():
            return False
        return self.validate() is None

    def on_changed(self, *_args) -> None:
        msg = self.validate()
        self.error.setText(msg or "")
        self.error.setVisible(bool(msg))
        if hasattr(self, "warning"):
            try:
                notes = self.warnings() if msg is None else []
            except Exception:
                notes = []
            self.warning.setText("\n".join(notes))
            self.warning.setVisible(bool(notes))
        self.ok_button.setEnabled(self.is_valid())
        if self.PREVIEW:
            self._timer.start()

    def accept(self) -> None:
        if self.is_valid():
            super().accept()

    def apply(self):
        """Esegue la modifica sul documento come unico passo annullabile."""
        if not self.is_valid():
            raise PageOpError(self.validate() or _("Controlla le pagine indicate."))
        opts = self.options()
        with self.document.change(_(self.UNDO_LABEL)) as pdf:
            return self.run(pdf, opts)

    def refresh_preview(self) -> None:
        if self.preview_label is None:
            return
        if not self.is_valid():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(_("Nessuna anteprima"))
            self.preview_caption.setText("")
            return
        index = self.preview_index()
        opts = self.engine_options()
        img = None
        try:
            with ENGINE_LOCK:
                tmp = pymupdf.open()
                try:
                    tmp.insert_pdf(self.document.pdf, from_page=index, to_page=index, annots=True)
                    popts = self.preview_options(dict(opts), index)
                    popts["pages"] = [0]
                    self.run(tmp, popts)
                    page = tmp[0]
                    zoom = min(PREVIEW_SIZE.width() / page.rect.width, PREVIEW_SIZE.height() / page.rect.height)
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False, annots=True)
                    img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                finally:
                    tmp.close()
        except PageOpError as e:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(str(e))
            self.preview_label.setWordWrap(True)
            return
        except Exception:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(_("Anteprima non disponibile"))
            return
        self.preview_label.setPixmap(QPixmap.fromImage(img))
        self.preview_caption.setText(_("Pagina {n}").format(n=index + 1))


# =========================================================================
# Filigrana
# =========================================================================

class WatermarkDialog(StampDialog):
    TITLE = "Filigrana"
    UNDO_LABEL = "Aggiungi filigrana"

    def build(self) -> None:
        with ENGINE_LOCK:
            self.existing = stamp.list_stamps(self.document.pdf, stamp.WATERMARK)
            self.pdfa = stamp.pdfa_part(self.document.pdf)
        self.edit_combo = QComboBox()
        self.edit_combo.addItem(_("Nuova filigrana"), None)
        for k, s in enumerate(self.existing, 1):
            self.edit_combo.addItem(_("Modifica la filigrana {k}: {d}").format(k=k, d=_describe_stamp(s)), s["id"])
        if self.existing:
            row = QFormLayout()
            row.addRow(_("Filigrana:"), self.edit_combo)
            self.form.addLayout(row)

        kind = QHBoxLayout()
        self.kind_text = QRadioButton(_("Testo"))
        self.kind_image = QRadioButton(_("Immagine"))
        self.kind_pdf = QRadioButton(_("Pagina PDF"))
        self.kind_text.setChecked(True)
        for b in (self.kind_text, self.kind_image, self.kind_pdf):
            kind.addWidget(b)
        kind.addStretch(1)
        self.form.addLayout(kind)

        self.stack = QStackedWidget()
        # Testo
        tw = QWidget()
        tf = QFormLayout(tw)
        tf.setContentsMargins(0, 0, 0, 0)
        self.text = QLineEdit(_("BOZZA"))
        self.text.setPlaceholderText(_("Testo della filigrana"))
        self.field_button = FieldMenuButton()
        self.field_button.watch(self.text)
        trow = QHBoxLayout()
        trow.addWidget(self.text, 1)
        trow.addWidget(self.field_button)
        self.font = _font_combo("hebo")
        self.font_size = _spin(4, 400, 60, " pt")
        self.color = ColorButton((0.75, 0.1, 0.1))
        row = QHBoxLayout()
        row.addWidget(self.font, 1)
        row.addWidget(self.font_size)
        row.addWidget(self.color)
        tf.addRow(_("Testo:"), trow)
        tf.addRow(_("Carattere:"), row)
        self.stack.addWidget(tw)
        # Immagine
        iw = QWidget()
        imf = QFormLayout(iw)
        imf.setContentsMargins(0, 0, 0, 0)
        self.image = ImageChooser(self.ctx)
        imf.addRow(_("Immagine:"), self.image)
        self.stack.addWidget(iw)
        # Pagina PDF
        pw = QWidget()
        pf = QFormLayout(pw)
        pf.setContentsMargins(0, 0, 0, 0)
        self.source_pdf = PdfPageChooser(self.ctx)
        pf.addRow(_("PDF:"), self.source_pdf)
        self.stack.addWidget(pw)
        self.form.addWidget(self.stack)
        self.scale = _spin(1, 500, 50, " %")
        self.scale.setToolTip(_("Larghezza dell'immagine rispetto alla pagina"))
        self.scale_row = QWidget()
        sr = QFormLayout(self.scale_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addRow(_("Larghezza:"), self.scale)
        self.form.addWidget(self.scale_row)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.opacity = OpacitySlider(30)
        self.rotation = _spin(-180, 180, 45, "°")
        rot = QHBoxLayout()
        rot.addWidget(self.rotation, 1)
        for deg in (0, 45, 90):
            b = QToolButton()
            b.setText(f"{deg}°")
            b.clicked.connect(lambda _c=False, d=deg: self.rotation.setValue(d))
            rot.addWidget(b)
        self.position = PositionPicker("center")
        self.offset_x = _mm_spin(-500, 500, 0)
        self.offset_y = _mm_spin(-500, 500, 0)
        off = QHBoxLayout()
        off.addWidget(QLabel(_("orizzontale")))
        off.addWidget(self.offset_x)
        off.addWidget(QLabel(_("verticale")))
        off.addWidget(self.offset_y)
        self.tile = QCheckBox(_("Ripeti a mosaico"))
        self.spacing = _mm_spin(0, 300, 25)
        tile = QHBoxLayout()
        tile.addWidget(self.tile)
        tile.addWidget(QLabel(_("spaziatura")))
        tile.addWidget(self.spacing)
        tile.addStretch(1)
        self.behind = QCheckBox(_("Dietro al contenuto"))
        self.visibility = VisibilityCombo()
        self.replace = QCheckBox(_("Sostituisci le filigrane già presenti"))
        self.replace.setVisible(bool(self.existing))
        lf.addRow(_("Opacità:"), self.opacity)
        lf.addRow(_("Rotazione:"), rot)
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow(_("Scostamento:"), off)
        lf.addRow("", tile)
        lf.addRow(_("Visibilità:"), self.visibility)
        lf.addRow("", self.behind)
        lf.addRow("", self.replace)
        self.form.addWidget(look)

        for b in (self.kind_text, self.kind_image, self.kind_pdf):
            b.toggled.connect(self._sync)
        self.tile.toggled.connect(self._sync)
        self.edit_combo.currentIndexChanged.connect(self._load_existing)
        self._sync()
        self.connect_changed(self.kind_text.toggled, self.kind_image.toggled, self.text.textChanged,
                             self.font.currentIndexChanged, self.font_size.valueChanged, self.color.changed,
                             self.image.changed, self.source_pdf.changed, self.scale.valueChanged,
                             self.opacity.changed, self.rotation.valueChanged, self.position.changed,
                             self.offset_x.valueChanged, self.offset_y.valueChanged, self.tile.toggled,
                             self.spacing.valueChanged, self.behind.toggled, self.visibility.currentIndexChanged,
                             self.replace.toggled)

    def _sync(self) -> None:
        idx = 0 if self.kind_text.isChecked() else 1 if self.kind_image.isChecked() else 2
        self.stack.setCurrentIndex(idx)
        self.scale_row.setVisible(idx != 0)
        self.position.setEnabled(not self.tile.isChecked())

    def _load_existing(self) -> None:
        """«Modifica»: riporta nei controlli i parametri della filigrana scelta."""
        sid = self.edit_combo.currentData()
        self.replace.setEnabled(sid is None)
        if sid is None:
            return
        entry = next((s for s in self.existing if s["id"] == sid), None)
        if entry is None:
            return
        self.load_params(entry["params"])
        if self.scope is not None:
            self.scope.set_mode("range", format_ranges(entry["pages"]))

    def load_params(self, p: dict) -> None:
        if p.get("text"):
            self.kind_text.setChecked(True)
            self.text.setText(p["text"])
        elif p.get("source_pdf"):
            self.kind_pdf.setChecked(True)
            self.source_pdf.set_path(p["source_pdf"], int(p.get("source_page") or 0))
        elif p.get("image"):
            self.kind_image.setChecked(True)
            self.image.set_path(p["image"])
        if p.get("font"):
            self.font.setCurrentIndex(max(0, self.font.findData(p["font"])))
        if p.get("font_size"):
            self.font_size.setValue(int(round(p["font_size"])))
        if p.get("color"):
            self.color.set_color(p["color"])
        if p.get("scale"):
            self.scale.setValue(int(round(p["scale"] * 100)))
        if p.get("opacity"):
            self.opacity.set_value(p["opacity"])
        rot = float(p.get("rotation") or 0)
        self.rotation.setValue(int(round(rot - 360 if rot > 180 else rot)))
        self.position.set_value(p.get("position") or "center")
        off = p.get("offset") or (0, 0)
        self.offset_x.setValue(off[0] / MM)
        self.offset_y.setValue(off[1] / MM)
        self.tile.setChecked(bool(p.get("tile")))
        if p.get("spacing") is not None:
            self.spacing.setValue(p["spacing"] / MM)
        self.behind.setChecked(bool(p.get("behind")))
        self.visibility.set_value(p.get("visibility") or "both")
        self._sync()

    def validate(self) -> str | None:
        if not hasattr(self, "visibility"):
            return None
        if self.kind_text.isChecked():
            if not self.text.text().strip():
                return _("Scrivi il testo della filigrana.")
            return _template_error(self.text.text())
        if self.kind_image.isChecked():
            return None if self.image.path() else _("Scegli un'immagine.")
        return self.source_pdf.error()

    def warnings(self) -> list[str]:
        out = []
        if self.pdfa and self.pdfa.startswith("1") and self.opacity.value() < 1.0:
            out.append(_("Il documento è PDF/A-1, che non ammette la trasparenza: con un'opacità inferiore "
                         "al 100% il file non sarà più conforme."))
        if self.behind.isChecked():
            with ENGINE_LOCK:
                scans = stamp.opaque_scan_pages(self.document.pdf, self.target_pages()[:200])
            if scans:
                out.append(_("Alcune pagine sono immagini a tutta pagina (scansioni): una filigrana dietro "
                             "al contenuto non si vedrebbe. Meglio sopra con opacità ridotta."))
        return out

    def engine_options(self) -> dict:
        opts = {
            "opacity": self.opacity.value(),
            "rotation": self.rotation.value(),
            "position": self.position.value(),
            "offset": (self.offset_x.value() * MM, self.offset_y.value() * MM),
            "tile": self.tile.isChecked(),
            "spacing": self.spacing.value() * MM,
            "behind": self.behind.isChecked(),
            "visibility": self.visibility.value(),
        }
        if self.kind_text.isChecked():
            opts.update(text=self.text.text(), font=self.font.currentData(), font_size=self.font_size.value(),
                        color=self.color.color(), filename=self.document.display_name)
        elif self.kind_image.isChecked():
            opts.update(image=self.image.path(), scale=self.scale.value() / 100.0)
        else:
            opts.update(source_pdf=self.source_pdf.path(), source_page=self.source_pdf.page_index(),
                        scale=self.scale.value() / 100.0)
        return opts

    def preview_options(self, opts: dict, page_index: int) -> dict:
        if "text" in opts:
            opts["field_values"] = self.preview_fields(page_index)
        return opts

    def options(self) -> dict:
        opts = super().options()
        opts["replace"] = self.replace.isChecked() and self.replace.isVisible() and self.replace.isEnabled()
        opts["stamp_id"] = self.edit_combo.currentData()
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        replace = opts.pop("replace", False)
        sid = opts.pop("stamp_id", None)
        pages = opts.pop("pages", None)
        if sid:
            return stamp.update_watermark(pdf, pages, stamp_id=sid, **opts)
        if replace:
            return stamp.update_watermark(pdf, pages, **opts)
        return stamp.add_watermark(pdf, pages, **opts)


# =========================================================================
# Rimozione timbri
# =========================================================================

def _kind_names() -> dict[str, str]:
    return {
        stamp.WATERMARK: _("Filigrane"),
        stamp.BACKGROUND: _("Sfondi"),
        stamp.NUMBERS: _("Numeri di pagina"),
        stamp.BATES: _("Numerazione Bates"),
        stamp.HEADERFOOTER: _("Intestazioni e piè di pagina"),
    }


def _kind_name_one() -> dict[str, str]:
    return {
        stamp.WATERMARK: _("Filigrana"),
        stamp.BACKGROUND: _("Sfondo"),
        stamp.NUMBERS: _("Numeri di pagina"),
        stamp.BATES: _("Numerazione Bates"),
        stamp.HEADERFOOTER: _("Intestazione e piè di pagina"),
    }


def _describe_stamp(s: dict) -> str:
    """Descrizione breve di un timbro per gli elenchi: tipo, contenuto, pagine."""
    p = s.get("params") or {}
    what = ""
    if s["kind"] == stamp.WATERMARK:
        if p.get("text"):
            what = f"«{p['text']}»"
        elif p.get("source_pdf") or p.get("image"):
            what = os.path.basename(p.get("source_pdf") or p.get("image"))
        else:
            what = _("immagine")
    elif s["kind"] == stamp.BACKGROUND:
        if p.get("source_pdf") or p.get("image"):
            what = os.path.basename(p.get("source_pdf") or p.get("image"))
        elif p.get("color"):
            c = QColor.fromRgbF(*p["color"][:3])
            what = c.name().upper()
    elif s["kind"] == stamp.HEADERFOOTER:
        texts = [t for v in (p.get("variants") or {}).values() if v for t in v.values() if t.strip()]
        what = f"«{texts[0]}»" if texts else ""
    elif s["kind"] == stamp.NUMBERS:
        what = f"«{p.get('fmt', '')}»"
    elif s["kind"] == stamp.BATES:
        what = f"«{p.get('prefix', '')}…{p.get('suffix', '')}»"
    pages = format_ranges(s["pages"])
    return _("{w} — pagine {p}").format(w=what, p=pages) if what else _("pagine {p}").format(p=pages)


class RemoveStampsDialog(StampDialog):
    TITLE = "Rimuovi filigrane e timbri"
    UNDO_LABEL = "Rimuovi filigrane e timbri"
    PREVIEW = False

    def build(self) -> None:
        with ENGINE_LOCK:
            self.stamps = stamp.list_stamps(self.document.pdf)
        self.form.addWidget(_hint(_("Rimuove gli elementi aggiunti con Foliant. Il resto della pagina non cambia. "
                                    "Filigrane e numeri non creati da Foliant non vengono rimossi.")))
        box = QGroupBox(_("Elementi da rimuovere"))
        lay = QVBoxLayout(box)
        names = _kind_name_one()
        self.items: list[tuple[dict, QCheckBox]] = []
        for s in self.stamps:
            cb = QCheckBox(f"{names[s['kind']]}: {_describe_stamp(s)}")
            cb.setChecked(True)
            lay.addWidget(cb)
            cb.toggled.connect(self.on_changed)
            self.items.append((s, cb))
        if not self.stamps:
            lay.addWidget(_hint(_("Nessun elemento di Foliant nel documento.")))
        self.form.addWidget(box)
        self.summary = _hint("")
        self.form.addWidget(self.summary)

    def _scope_pages(self) -> set[int] | None:
        if self.scope is not None and self.scope.is_valid():
            pages = self.scope.pages()
            return None if pages is None else set(pages)
        return None

    def _count(self, s: dict) -> int:
        pages = self._scope_pages()
        return len(s["pages"]) if pages is None else len([p for p in s["pages"] if p in pages])

    def on_changed(self, *_args) -> None:
        if hasattr(self, "items"):
            total = 0
            for s, cb in self.items:
                n = self._count(s)
                cb.setEnabled(n > 0)
                if cb.isChecked() and n:
                    total += n
            self.summary.setText(_("Elementi da rimuovere: {n}").format(n=total) if total
                                 else _("Nessun elemento di Foliant nelle pagine indicate."))
        super().on_changed()

    def selected_ids(self) -> list[str]:
        return [s["id"] for s, cb in self.items if cb.isChecked() and cb.isEnabled()]

    def select_kind(self, kind: str, on: bool = True) -> None:
        for s, cb in self.items:
            if s["kind"] == kind:
                cb.setChecked(on)

    def validate(self) -> str | None:
        if not hasattr(self, "items"):
            return None
        if not self.selected_ids():
            return _("Non c'è niente da rimuovere.")
        return None

    def engine_options(self) -> dict:
        return {"ids": self.selected_ids()}

    def run(self, pdf, opts):
        return stamp.remove_stamp(pdf, opts["ids"], opts.get("pages"))


# =========================================================================
# Sfondo
# =========================================================================

class BackgroundDialog(StampDialog):
    TITLE = "Sfondo"
    UNDO_LABEL = "Aggiungi sfondo"
    REPLACE_KIND = stamp.BACKGROUND
    REPLACE_TEXT = "Sostituisci gli sfondi già presenti"

    def build(self) -> None:
        kind = QHBoxLayout()
        self.kind_color = QRadioButton(_("Colore"))
        self.kind_image = QRadioButton(_("Immagine"))
        self.kind_pdf = QRadioButton(_("Carta intestata (PDF)"))
        self.kind_color.setChecked(True)
        for b in (self.kind_color, self.kind_image, self.kind_pdf):
            kind.addWidget(b)
        kind.addStretch(1)
        self.form.addLayout(kind)
        box = QGroupBox(_("Sfondo"))
        f = QFormLayout(box)
        self.color = ColorButton((1.0, 0xF5 / 255, 0xCC / 255))
        self.image = ImageChooser(self.ctx)
        self.source_pdf = PdfPageChooser(self.ctx)
        self.first_rest = QCheckBox(_("Pagina 1 sulla prima pagina, pagina 2 sulle altre"))
        self.scale = QComboBox()
        for key, text in (("fill", _("Riempi la pagina")), ("fit", _("Adatta alla pagina")),
                          ("percent", _("Percentuale")), ("actual", _("Dimensione reale"))):
            self.scale.addItem(text, key)
        self.percent = _spin(1, 1000, 100, " %")
        srow = QHBoxLayout()
        srow.addWidget(self.scale, 1)
        srow.addWidget(self.percent)
        self.position = PositionPicker("center")
        self.rotation = QComboBox()
        for deg in (0, 90, 180, 270):
            self.rotation.addItem(f"{deg}°", deg)
        self.opacity = OpacitySlider(100)
        self.visibility = VisibilityCombo()
        f.addRow(_("Colore:"), self.color)
        f.addRow(_("Immagine:"), self.image)
        f.addRow(_("PDF:"), self.source_pdf)
        f.addRow("", self.first_rest)
        f.addRow(_("Scala:"), srow)
        f.addRow(_("Posizione:"), self.position)
        f.addRow(_("Rotazione:"), self.rotation)
        f.addRow(_("Opacità:"), self.opacity)
        f.addRow(_("Visibilità:"), self.visibility)
        self.form.addWidget(box)
        self.form.addWidget(_hint(_("Lo sfondo è disegnato sotto il contenuto: è visibile dove la pagina "
                                    "non ha già un fondo proprio.")))
        for b in (self.kind_color, self.kind_image, self.kind_pdf):
            b.toggled.connect(self._sync)
        self.scale.currentIndexChanged.connect(self._sync)
        self.source_pdf.changed.connect(self._sync)
        self._sync()
        self.connect_changed(self.kind_color.toggled, self.kind_image.toggled, self.color.changed,
                             self.image.changed, self.source_pdf.changed, self.first_rest.toggled,
                             self.scale.currentIndexChanged, self.percent.valueChanged, self.position.changed,
                             self.rotation.currentIndexChanged, self.opacity.changed,
                             self.visibility.currentIndexChanged)

    def _sync(self) -> None:
        color, image, pdf = self.kind_color.isChecked(), self.kind_image.isChecked(), self.kind_pdf.isChecked()
        self.color.setEnabled(color)
        self.image.setEnabled(image)
        self.source_pdf.setEnabled(pdf)
        self.first_rest.setEnabled(pdf and self.source_pdf.pages >= 2)
        for w in (self.scale, self.position, self.rotation):
            w.setEnabled(not color)
        self.percent.setEnabled(not color and self.scale.currentData() == "percent")

    def validate(self) -> str | None:
        if not hasattr(self, "visibility"):
            return None
        if self.kind_image.isChecked() and not self.image.path():
            return _("Scegli un'immagine.")
        if self.kind_pdf.isChecked():
            return self.source_pdf.error()
        return None

    def warnings(self) -> list[str]:
        with ENGINE_LOCK:
            scans = stamp.opaque_scan_pages(self.document.pdf, self.target_pages()[:200])
        if scans:
            return [_("Alcune pagine sono immagini a tutta pagina (scansioni): lo sfondo non sarà visibile.")]
        return []

    def engine_options(self) -> dict:
        opts = {"opacity": self.opacity.value(), "visibility": self.visibility.value()}
        if self.kind_color.isChecked():
            opts["color"] = self.color.color()
            return opts
        opts.update(scale=self.scale.currentData(), percent=self.percent.value(), position=self.position.value(),
                    rotation=self.rotation.currentData())
        if self.kind_image.isChecked():
            opts["image"] = self.image.path()
        else:
            opts.update(source_pdf=self.source_pdf.path(), source_page=self.source_pdf.page_index(),
                        first_rest=self.first_rest.isChecked() and self.first_rest.isEnabled())
        return opts

    def preview_options(self, opts: dict, page_index: int) -> dict:
        if opts.get("first_rest"):
            # anteprima sulla pagina copiata: si sceglie la carta che le spetterebbe
            first = self.target_pages()[0] if self.target_pages() else 0
            opts["first_rest"] = False
            opts["source_page"] = 0 if page_index == first else 1
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        self._remove_existing(pdf, opts)
        return stamp.add_background(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Numeri di pagina
# =========================================================================

def _text_style_rows(form: QFormLayout, font: str = "helv", size: int = 10, rgb=(0, 0, 0)):
    font_combo = _font_combo(font)
    size_spin = _spin(4, 200, size, " pt")
    color = ColorButton(rgb)
    row = QHBoxLayout()
    row.addWidget(font_combo, 1)
    row.addWidget(size_spin)
    row.addWidget(color)
    form.addRow(_("Carattere:"), row)
    return font_combo, size_spin, color


class PageNumbersDialog(StampDialog):
    TITLE = "Numeri di pagina"
    UNDO_LABEL = "Aggiungi numeri di pagina"
    REPLACE_KIND = stamp.NUMBERS
    REPLACE_TEXT = "Sostituisci i numeri di pagina già presenti"

    FORMATS = ("{pagina}", "{pagina}/{totale}", "Pagina {pagina} di {totale}", "- {pagina} -", "{pagina} / {totale}")

    def build(self) -> None:
        self.remove_requested = False
        with ENGINE_LOCK:
            self.existing = stamp.stamp_params(self.document.pdf, stamp.NUMBERS)
        if self.existing is not None:
            bar = QHBoxLayout()
            bar.addWidget(_hint(_("Il documento ha già numeri di pagina di Foliant: puoi modificarli o rimuoverli.")),
                          1)
            self.remove_button = QPushButton(icon("trash-2"), _("Rimuovi numeri"))
            self.remove_button.clicked.connect(self.request_remove)
            bar.addWidget(self.remove_button)
            self.form.addLayout(bar)
        box = QGroupBox(_("Numerazione"))
        f = QFormLayout(box)
        self.fmt = QComboBox()
        self.fmt.setEditable(True)
        for item in self.FORMATS:
            self.fmt.addItem(_(item) if "Pagina" in item else item)
        self.fmt.setCurrentIndex(0)
        self.field_button = FieldMenuButton()
        self.field_button.watch(self.fmt.lineEdit())
        frow = QHBoxLayout()
        frow.addWidget(self.fmt, 1)
        frow.addWidget(self.field_button)
        self.start = _spin(0, 999999, 1)
        self.skip_first = QCheckBox(_("Non numerare la prima pagina (ma contala)"))
        self.num_style = QComboBox()
        for key, text in (("D", "1, 2, 3"), ("r", "i, ii, iii"), ("R", "I, II, III"), ("a", "a, b, c"),
                          ("A", "A, B, C")):
            self.num_style.addItem(text, key)
        self.mirror = QComboBox()
        for key, text in (("none", _("Nessuna")), ("outer", _("Lato esterno (dispari a destra)")),
                          ("inner", _("Lato interno (dispari a sinistra)"))):
            self.mirror.addItem(text, key)
        self.start_from = _spin(1, max(1, self.page_count), 1)
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText(_("es. 1, 5-6"))
        self.use_label = QCheckBox(_("Stampa l'etichetta di pagina al posto del numero"))
        f.addRow(pgettext("numero", "Formato:"), frow)
        f.addRow(_("Stile:"), self.num_style)
        f.addRow("", _hint(_("{pagina} numero, {totale} ultimo numero, {etichetta} etichetta di pagina")))
        f.addRow(_("Inizia da:"), self.start)
        f.addRow(_("Dalla pagina:"), self.start_from)
        f.addRow(_("Escludi le pagine:"), self.exclude)
        f.addRow("", self.skip_first)
        f.addRow("", self.use_label)
        self.form.addWidget(box)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.position = PositionPicker("bottom-center")
        self.margin = _spin(0, 200, 12, " mm")
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow(_("Fronte/retro:"), self.mirror)
        lf.addRow(_("Margine:"), self.margin)
        self.font, self.font_size, self.color = _text_style_rows(lf)
        self.form.addWidget(look)
        if self.existing:
            self.load_params(self.existing)
        self.connect_changed(self.fmt.currentTextChanged, self.start.valueChanged, self.skip_first.toggled,
                             self.num_style.currentIndexChanged, self.mirror.currentIndexChanged,
                             self.start_from.valueChanged, self.exclude.textChanged, self.use_label.toggled,
                             self.position.changed, self.margin.valueChanged, self.font.currentIndexChanged,
                             self.font_size.valueChanged, self.color.changed)

    def load_params(self, p: dict) -> None:
        """«Modifica»: i controlli ripartono dai parametri dei numeri esistenti."""
        if p.get("fmt"):
            self.fmt.setEditText(p["fmt"])
        self.start.setValue(int(p.get("start") or 0))
        self.num_style.setCurrentIndex(max(0, self.num_style.findData(p.get("style") or "D")))
        self.mirror.setCurrentIndex(max(0, self.mirror.findData(p.get("mirror") or "none")))
        if p.get("start_from") is not None:
            self.start_from.setValue(int(p["start_from"]) + 1)
        if p.get("exclude"):
            self.exclude.setText(format_ranges(p["exclude"]))
        self.skip_first.setChecked(bool(p.get("skip_first")))
        self.use_label.setChecked(bool(p.get("use_label")))
        self.position.set_value(p.get("position") or "bottom-center")
        if p.get("margin") is not None:
            self.margin.setValue(int(round(p["margin"] / MM)))
        if p.get("font"):
            self.font.setCurrentIndex(max(0, self.font.findData(p["font"])))
        if p.get("font_size"):
            self.font_size.setValue(int(round(p["font_size"])))
        if p.get("color"):
            self.color.set_color(p["color"])

    def request_remove(self) -> None:
        self.remove_requested = True
        self.accept()

    def _excluded(self) -> list[int]:
        text = self.exclude.text().strip()
        return parse_ranges(text, self.page_count) if text else []

    def numbered_pages(self) -> list[int]:
        try:
            excluded = set(self._excluded())
        except PageOpError:
            excluded = set()
        start_from = self.start_from.value() - 1
        return [p for p in self.target_pages() if p not in excluded and p >= start_from]

    def validate(self) -> str | None:
        if not hasattr(self, "use_label"):
            return None
        if self.remove_requested:
            return None
        text = self.fmt.currentText()
        if not text.strip():
            return _("Il formato del numero non può essere vuoto.")
        err = _template_error(text)
        if err:
            return err
        try:
            self._excluded()
        except PageOpError as e:
            return _("Pagine da escludere: {e}").format(e=e)
        pages = self.numbered_pages()
        if not pages:
            return _("Non ci sono pagine da numerare.")
        if self.skip_first.isChecked() and len(pages) < 2:
            return _("Non ci sono pagine da numerare dopo aver saltato la prima.")
        return None

    def is_valid(self) -> bool:
        if getattr(self, "remove_requested", False):
            return True
        return super().is_valid()

    def engine_options(self) -> dict:
        try:
            exclude = self._excluded()
        except PageOpError:
            exclude = []
        return {
            "fmt": self.fmt.currentText(), "start": self.start.value(), "position": self.position.value(),
            "margin": self.margin.value() * MM, "font": self.font.currentData(),
            "font_size": self.font_size.value(), "color": self.color.color(),
            "skip_first": self.skip_first.isChecked(),
            "style": self.num_style.currentData(), "mirror": self.mirror.currentData(),
            "start_from": self.start_from.value() - 1 if self.start_from.value() > 1 else None,
            "exclude": exclude, "use_label": self.use_label.isChecked(), "filename": self.document.display_name,
        }

    def preview_index(self) -> int:
        pages = self.numbered_pages()
        if self.skip_first.isChecked() and len(pages) > 1:
            return pages[1]
        return pages[0] if pages else 0

    def preview_options(self, opts: dict, page_index: int) -> dict:
        pages = self.numbered_pages()
        k = pages.index(page_index) if page_index in pages else 0
        fv = self.preview_fields(page_index)
        fv["totale"] = stamp.format_number(opts["start"] + len(pages) - 1, opts["style"])
        fv.pop("pagina", None)
        opts.update(start=opts["start"] + k, skip_first=False, start_from=None, exclude=[], field_values=fv)
        return opts

    def options(self) -> dict:
        opts = super().options()
        opts["remove"] = self.remove_requested
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        if opts.pop("remove", False):
            return stamp.remove_watermarks(pdf, None, kind=stamp.NUMBERS)
        self._remove_existing(pdf, opts)
        return stamp.add_page_numbers(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Bates
# =========================================================================

class BatesDialog(StampDialog):
    REPLACE_KIND = stamp.BATES
    REPLACE_TEXT = "Sostituisci la numerazione Bates già presente"
    TITLE = "Numerazione Bates"
    UNDO_LABEL = "Aggiungi numerazione Bates"

    def build(self) -> None:
        box = QGroupBox(_("Codice"))
        f = QFormLayout(box)
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText(_("es. CAUSA-"))
        self.suffix = QLineEdit()
        self.start = _spin(0, 999999999, 1)
        self.digits = _spin(1, 15, 6)
        self.example = _hint("")
        f.addRow(_("Prefisso:"), self.prefix)
        f.addRow(_("Suffisso:"), self.suffix)
        f.addRow(_("Primo numero:"), self.start)
        f.addRow(_("Cifre:"), self.digits)
        f.addRow("", self.example)
        self.form.addWidget(box)
        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.position = PositionPicker("bottom-right")
        self.margin = _spin(0, 200, 12, " mm")
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow(_("Margine:"), self.margin)
        self.font, self.font_size, self.color = _text_style_rows(lf, "cour", 10)
        self.form.addWidget(look)
        self.connect_changed(self.prefix.textChanged, self.suffix.textChanged, self.start.valueChanged,
                             self.digits.valueChanged, self.position.changed, self.margin.valueChanged,
                             self.font.currentIndexChanged, self.font_size.valueChanged, self.color.changed)

    def _count(self) -> int:
        if self.scope is not None and self.scope.is_valid():
            pages = self.scope.pages()
            return len(pages) if pages is not None else self.page_count
        return self.page_count

    def on_changed(self, *_args) -> None:
        if hasattr(self, "example"):
            first = self.start.value()
            last = first + self._count() - 1
            fmt = lambda n: stamp.format_bates(n, prefix=self.prefix.text(), suffix=self.suffix.text(),
                                               digits=self.digits.value())
            self.example.setText(_("Da {a} a {b}").format(a=fmt(first), b=fmt(last)))
        super().on_changed()

    def validate(self) -> str | None:
        if not hasattr(self, "digits"):
            return None
        last = self.start.value() + self._count() - 1
        if len(str(last)) > self.digits.value():
            return _("Le cifre indicate non bastano per numerare tutte le pagine.")
        return None

    def engine_options(self) -> dict:
        return {
            "prefix": self.prefix.text(), "suffix": self.suffix.text(), "start": self.start.value(),
            "digits": self.digits.value(), "position": self.position.value(), "margin": self.margin.value() * MM,
            "font": self.font.currentData(), "font_size": self.font_size.value(), "color": self.color.color(),
        }

    def run(self, pdf, opts):
        opts = dict(opts)
        self._remove_existing(pdf, opts)
        return stamp.add_bates(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Intestazione e piè di pagina
# =========================================================================

TEMPLATES_KEY = "pages/headerfooter_templates"


class HeaderFooterDialog(StampDialog):
    REPLACE_KIND = stamp.HEADERFOOTER
    REPLACE_TEXT = "Sostituisci intestazioni e piè di pagina già presenti"
    TITLE = "Intestazione e piè di pagina"
    UNDO_LABEL = "Aggiungi intestazione e piè di pagina"

    DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y")
    VARIANTS = ("default", "even", "first")

    def build(self) -> None:
        self.settings = _settings_of(self.ctx, self.parent())
        self._memory_templates: dict = {}
        # modelli
        trow = QHBoxLayout()
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(160)
        self.save_template_button = QPushButton(icon("save"), _("Salva modello…"))
        self.delete_template_button = QToolButton()
        self.delete_template_button.setIcon(icon("trash-2"))
        self.delete_template_button.setToolTip(_("Elimina modello"))
        trow.addWidget(QLabel(_("Modello:")))
        trow.addWidget(self.template_combo, 1)
        trow.addWidget(self.save_template_button)
        trow.addWidget(self.delete_template_button)
        self.form.addLayout(trow)
        self._fill_templates()
        self.template_combo.activated.connect(lambda *_a: self.load_template(self.template_combo.currentData()))
        self.save_template_button.clicked.connect(self._ask_save_template)
        self.delete_template_button.clicked.connect(
            lambda: self.delete_template(self.template_combo.currentData()))

        box = QGroupBox(_("Testi"))
        g = QGridLayout(box)
        vrow = QHBoxLayout()
        self.even_diff = QCheckBox(_("Pagine pari diverse"))
        self.first_diff = QCheckBox(_("Prima pagina diversa"))
        self.variant = QComboBox()
        self.variant.addItem(_("Tutte le pagine"), "default")
        self.variant.addItem(_("Pagine pari"), "even")
        self.variant.addItem(_("Prima pagina"), "first")
        vrow.addWidget(self.even_diff)
        vrow.addWidget(self.first_diff)
        vrow.addStretch(1)
        vrow.addWidget(QLabel(_("Testi per:")))
        vrow.addWidget(self.variant)
        g.addLayout(vrow, 0, 0, 1, 4)
        g.addWidget(_hint(_("Sinistra")), 1, 1)
        g.addWidget(_hint(_("Centro")), 1, 2)
        g.addWidget(_hint(_("Destra")), 1, 3)
        g.addWidget(QLabel(_("Intestazione:")), 2, 0)
        g.addWidget(QLabel(_("Piè di pagina:")), 3, 0)
        self.fields: dict[str, QPlainTextEdit] = {}
        self.field_button = FieldMenuButton()
        for r, part in enumerate(("header", "footer")):
            for c, side in enumerate(("left", "center", "right")):
                e = QPlainTextEdit()
                e.setMinimumWidth(130)
                e.setFixedHeight(48)
                e.setTabChangesFocus(True)
                self.fields[f"{part}_{side}"] = e
                g.addWidget(e, r + 2, c + 1)
                e.textChanged.connect(self._field_edited)
                self.field_button.watch(e)
        self.variants: dict[str, dict[str, str]] = {
            "default": {k: "" for k in stamp.SLOTS}, "even": {k: "" for k in stamp.SLOTS},
            "first": {k: "" for k in stamp.SLOTS}}
        self.variants["default"]["footer_right"] = _("Pagina {pagina} di {totale}")
        self._current_variant = "default"
        self._loading = False
        self._show_variant("default")
        hint_row = QHBoxLayout()
        hint_row.addWidget(_hint(_("Più righe con Invio. Campi: {pagina}, {totale}, {data:gg/MM/aaaa}, {nome}, "
                                   "{titolo}…")), 1)
        hint_row.addWidget(self.field_button)
        g.addLayout(hint_row, 4, 0, 1, 4)
        self.form.addWidget(box)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.margin = _spin(0, 200, 12, " mm")
        self.date_format = QComboBox()
        self.date_format.setEditable(True)
        today = _dt.date.today()
        self.date_format.addItem(_("Come la lingua"))
        self.date_format.setItemData(0, format_date(today), Qt.ItemDataRole.ToolTipRole)
        for fmt in self.DATE_FORMATS:
            self.date_format.addItem(fmt)
            self.date_format.setItemData(self.date_format.count() - 1, today.strftime(fmt),
                                         Qt.ItemDataRole.ToolTipRole)
        lf.addRow(_("Margine:"), self.margin)
        self.font, self.font_size, self.color = _text_style_rows(lf)
        self.underline = QCheckBox(_("Sottolineato"))
        self.opacity = OpacitySlider(100)
        lf.addRow("", self.underline)
        lf.addRow(_("Opacità:"), self.opacity)
        lf.addRow(_("Formato data:"), self.date_format)
        self.date_example = _hint("")
        lf.addRow("", self.date_example)
        self.logo = ImageChooser(self.ctx)
        self.logo_position = QComboBox()
        names = {"top-left": _("In alto a sinistra"), "top-center": _("In alto al centro"),
                 "top-right": _("In alto a destra"), "bottom-left": _("In basso a sinistra"),
                 "bottom-center": _("In basso al centro"), "bottom-right": _("In basso a destra")}
        for key, text in names.items():
            self.logo_position.addItem(text, key)
        self.logo_height = _mm_spin(2, 150, 10)
        self.logo_clear = QToolButton()
        self.logo_clear.setIcon(icon("x"))
        self.logo_clear.setToolTip(_("Togli il logo"))
        self.logo_clear.clicked.connect(lambda: self.logo.set_path(""))
        lrow = QHBoxLayout()
        lrow.addWidget(self.logo, 1)
        lrow.addWidget(self.logo_clear)
        lpos = QHBoxLayout()
        lpos.addWidget(self.logo_position, 1)
        lpos.addWidget(QLabel(_("altezza")))
        lpos.addWidget(self.logo_height)
        lf.addRow(_("Logo:"), lrow)
        lf.addRow("", lpos)
        self.visibility = VisibilityCombo()
        lf.addRow(_("Visibilità:"), self.visibility)
        self.form.addWidget(look)

        prow = QHBoxLayout()
        self.preview_page = _spin(0, self.page_count, 0)
        self.preview_page.setSpecialValueText(_("prima interessata"))
        prow.addWidget(QLabel(_("Anteprima della pagina:")))
        prow.addWidget(self.preview_page)
        prow.addStretch(1)
        self.form.addLayout(prow)

        self.even_diff.toggled.connect(self._sync_variants)
        self.first_diff.toggled.connect(self._sync_variants)
        self.variant.currentIndexChanged.connect(lambda *_a: self._show_variant(self.variant.currentData()))
        self._sync_variants()
        with ENGINE_LOCK:
            existing = stamp.stamp_params(self.document.pdf, stamp.HEADERFOOTER)
        if existing:
            self.load_params(existing)  # «Modifica»: si riparte dai parametri salvati
        self.connect_changed(self.margin.valueChanged, self.font.currentIndexChanged, self.font_size.valueChanged,
                             self.color.changed, self.date_format.currentTextChanged, self.underline.toggled,
                             self.opacity.changed, self.logo.changed, self.logo_position.currentIndexChanged,
                             self.logo_height.valueChanged, self.visibility.currentIndexChanged,
                             self.even_diff.toggled, self.first_diff.toggled, self.preview_page.valueChanged)

    # -- varianti ----------------------------------------------------------
    def _field_edited(self) -> None:
        if not self._loading:
            self.variants[self._current_variant] = {k: e.toPlainText() for k, e in self.fields.items()}
            self.on_changed()

    def _show_variant(self, key: str) -> None:
        self._loading = True
        try:
            self._current_variant = key
            for k, e in self.fields.items():
                e.setPlainText(self.variants[key].get(k, ""))
        finally:
            self._loading = False
        self.on_changed()

    def _sync_variants(self) -> None:
        model = self.variant.model()
        model.item(1).setEnabled(self.even_diff.isChecked())
        model.item(2).setEnabled(self.first_diff.isChecked())
        current = self.variant.currentData()
        if (current == "even" and not self.even_diff.isChecked()) or \
                (current == "first" and not self.first_diff.isChecked()):
            self.variant.setCurrentIndex(0)
        self.variant.setEnabled(self.even_diff.isChecked() or self.first_diff.isChecked())

    def set_texts(self, variant: str = "default", **texts: str) -> None:
        """Imposta i testi di una variante (per codice e test): chiavi come ``header_left``."""
        self.variants[variant].update(texts)
        if variant == "even":
            self.even_diff.setChecked(True)
        elif variant == "first":
            self.first_diff.setChecked(True)
        self._show_variant(self._current_variant)

    # -- parametri e modelli -------------------------------------------------
    def style_params(self) -> dict:
        """Parametri salvabili come modello (testi, varianti, aspetto)."""
        return {
            "variants": {"default": dict(self.variants["default"]),
                         "even": dict(self.variants["even"]) if self.even_diff.isChecked() else None,
                         "first": dict(self.variants["first"]) if self.first_diff.isChecked() else None},
            "margin": self.margin.value() * MM, "font": self.font.currentData(),
            "font_size": self.font_size.value(), "color": list(self.color.color()),
            "date_format": self._date_format_value(), "opacity": self.opacity.value(),
            "underline": self.underline.isChecked(), "logo": self.logo.path() or None,
            "logo_position": self.logo_position.currentData(), "logo_height": self.logo_height.value() * MM,
            "visibility": self.visibility.value(),
        }

    def load_params(self, p: dict) -> None:
        variants = p.get("variants") or {}
        for key in self.VARIANTS:
            self.variants[key] = {k: "" for k in stamp.SLOTS}
            self.variants[key].update((variants.get(key) or {}))
        self.even_diff.setChecked(variants.get("even") is not None)
        self.first_diff.setChecked(variants.get("first") is not None)
        if p.get("margin") is not None:
            self.margin.setValue(int(round(p["margin"] / MM)))
        if p.get("font"):
            self.font.setCurrentIndex(max(0, self.font.findData(p["font"])))
        if p.get("font_size"):
            self.font_size.setValue(int(round(p["font_size"])))
        if p.get("color"):
            self.color.set_color(p["color"])
        if p.get("date_format"):
            self.date_format.setEditText(p["date_format"])
        else:
            self.date_format.setCurrentIndex(0)
        if p.get("opacity"):
            self.opacity.set_value(p["opacity"])
        self.underline.setChecked(bool(p.get("underline")))
        self.logo.set_path(p.get("logo") or "")
        if p.get("logo_position"):
            self.logo_position.setCurrentIndex(max(0, self.logo_position.findData(p["logo_position"])))
        if p.get("logo_height"):
            self.logo_height.setValue(p["logo_height"] / MM)
        self.visibility.set_value(p.get("visibility") or "both")
        self.variant.setCurrentIndex(0)
        self._show_variant("default")

    def _templates(self) -> dict:
        if self.settings is None:
            return dict(self._memory_templates)
        raw = self.settings.value(TEMPLATES_KEY, "")
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {}
        return data if isinstance(data, dict) else {}

    def _store_templates(self, data: dict) -> None:
        if self.settings is None:
            self._memory_templates = dict(data)
        else:
            self.settings.setValue(TEMPLATES_KEY, json.dumps(data, ensure_ascii=False))
            self.settings.sync()

    def _fill_templates(self, select: str | None = None) -> None:
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem(_("(nessuno)"), None)
        for name in sorted(self._templates(), key=str.casefold):
            self.template_combo.addItem(name, name)
        if select:
            self.template_combo.setCurrentIndex(max(0, self.template_combo.findData(select)))
        self.template_combo.blockSignals(False)
        self.delete_template_button.setEnabled(self.template_combo.count() > 1)

    def template_names(self) -> list[str]:
        return sorted(self._templates(), key=str.casefold)

    def save_template(self, name: str) -> None:
        name = (name or "").strip()
        if not name:
            return
        data = self._templates()
        data[name] = self.style_params()
        self._store_templates(data)
        self._fill_templates(name)

    def load_template(self, name: str | None) -> bool:
        if not name:
            return False
        params = self._templates().get(name)
        if not params:
            return False
        self.load_params(params)
        return True

    def delete_template(self, name: str | None) -> None:
        if not name:
            return
        data = self._templates()
        data.pop(name, None)
        self._store_templates(data)
        self._fill_templates()

    def _ask_save_template(self) -> None:
        name, ok = QInputDialog.getText(self, _("Salva modello"), _("Nome del modello:"),
                                        text=self.template_combo.currentData() or "")
        if ok and name.strip():
            self.save_template(name)

    # -- comune --------------------------------------------------------------
    def _date_format_value(self) -> str | None:
        """None = formato della lingua (format_date del nucleo)."""
        text = self.date_format.currentText()
        return None if not text or text == _("Come la lingua") else text

    def _date_text(self) -> str | None:
        fmt = self._date_format_value()
        if fmt is None:
            return format_date(_dt.date.today())
        try:
            return _dt.date.today().strftime(fmt)
        except (ValueError, TypeError):
            return None

    def on_changed(self, *_args) -> None:
        if hasattr(self, "date_example"):
            d = self._date_text()
            self.date_example.setText(_("Oggi: {d}").format(d=d) if d else "")
        super().on_changed()

    def _all_texts(self) -> list[str]:
        out = list(self.variants["default"].values())
        if self.even_diff.isChecked():
            out += list(self.variants["even"].values())
        if self.first_diff.isChecked():
            out += list(self.variants["first"].values())
        return out

    def validate(self) -> str | None:
        if not hasattr(self, "visibility"):
            return None
        texts = self._all_texts()
        if not any(t.strip() for t in texts) and not self.logo.path():
            return _("Scrivi almeno un testo di intestazione o piè di pagina.")
        err = _template_error(*texts)
        if err:
            return err
        if self._date_text() is None:
            return _("Formato della data non valido.")
        return None

    def engine_options(self) -> dict:
        p = self.style_params()
        variants = p.pop("variants")
        opts = dict(variants["default"])
        opts.update(even=variants["even"], first=variants["first"], margin=p["margin"], font=p["font"],
                    font_size=p["font_size"], color=tuple(p["color"]), date_format=p["date_format"],
                    opacity=p["opacity"], underline=p["underline"], logo=p["logo"],
                    logo_position=p["logo_position"], logo_height=p["logo_height"], visibility=p["visibility"],
                    filename=self.document.display_name)
        return opts

    def preview_index(self) -> int:
        if getattr(self, "preview_page", None) is not None and self.preview_page.value() > 0:
            return self.preview_page.value() - 1
        return super().preview_index()

    def preview_options(self, opts: dict, page_index: int) -> dict:
        targets = self.target_pages()
        variant = "default"
        if opts.get("first") is not None and targets and page_index == targets[0]:
            variant = "first"
        elif opts.get("even") is not None and (page_index + 1) % 2 == 0:
            variant = "even"
        if page_index not in targets:
            return {"_skip": True}
        texts = {"default": {k: opts[k] for k in stamp.SLOTS}, "even": opts.get("even"),
                 "first": opts.get("first")}[variant]
        for k in stamp.SLOTS:
            opts[k] = texts.get(k, "")
        opts["even"] = opts["first"] = None
        opts["field_values"] = self.preview_fields(page_index)
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        if opts.pop("_skip", False):
            return []
        if not any(opts.get(k, "").strip() for k in stamp.SLOTS) and not opts.get("logo") \
                and opts.get("even") is None and opts.get("first") is None:
            return []  # anteprima di una pagina senza testi (es. prima pagina vuota)
        self._remove_existing(pdf, opts)
        return stamp.add_header_footer(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Etichette di pagina
# =========================================================================

def _style_names() -> list[tuple[str, str]]:
    return [
        ("D", _("1, 2, 3")), ("r", _("i, ii, iii")), ("R", _("I, II, III")),
        ("a", _("a, b, c")), ("A", _("A, B, C")), ("", _("Nessun numero (solo prefisso)")),
    ]


class PageLabelsDialog(StampDialog):
    TITLE = "Etichette di pagina"
    UNDO_LABEL = "Imposta etichette di pagina"
    PREVIEW = False
    SCOPE = False

    def build(self) -> None:
        self.form.addWidget(_hint(_("Le etichette sono i numeri mostrati dai lettori PDF (es. i, ii per la "
                                    "prefazione e poi 1, 2, 3). Ogni regola vale fino alla successiva.")))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("Dalla pagina"), _("Stile"), _("Prefisso"), _("Primo numero")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumSize(QSize(520, 180))
        self.form.addWidget(self.table)
        row = QHBoxLayout()
        self.add_btn = QPushButton(icon("plus"), _("Aggiungi regola"))
        self.del_btn = QPushButton(icon("trash-2"), _("Elimina regola"))
        self.clear_btn = QPushButton(_("Rimuovi tutte le etichette"))
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        row.addWidget(self.clear_btn)
        self.form.addLayout(row)
        self.preview = _hint("")
        self.form.addWidget(self.preview)
        self.add_btn.clicked.connect(lambda: self.add_rule())
        self.del_btn.clicked.connect(self.delete_rule)
        self.clear_btn.clicked.connect(self.clear_rules)
        with ENGINE_LOCK:
            rules = stamp.get_page_labels(self.document.pdf)
        for r in rules:
            self.add_rule(r)
        if not rules:
            self.add_rule({"startpage": 0, "style": "D", "prefix": "", "firstpagenum": 1})

    def add_rule(self, rule: dict | None = None) -> None:
        if rule is None:
            used = {r["startpage"] for r in self._raw_rules()}
            start = next((i for i in range(self.page_count) if i not in used), 0)
            rule = {"startpage": start, "style": "D", "prefix": "", "firstpagenum": 1}
        r = self.table.rowCount()
        self.table.insertRow(r)
        page = _spin(1, max(1, self.page_count), int(rule.get("startpage", 0)) + 1)
        style = QComboBox()
        for code, name in _style_names():
            style.addItem(name, code)
        style.setCurrentIndex(max(0, style.findData(rule.get("style", "D") or "")))
        prefix = QLineEdit(rule.get("prefix", "") or "")
        first = _spin(1, 999999, int(rule.get("firstpagenum", 1) or 1))
        self.table.setCellWidget(r, 0, page)
        self.table.setCellWidget(r, 1, style)
        self.table.setCellWidget(r, 2, prefix)
        self.table.setCellWidget(r, 3, first)
        page.valueChanged.connect(self.on_changed)
        style.currentIndexChanged.connect(self.on_changed)
        prefix.textChanged.connect(self.on_changed)
        first.valueChanged.connect(self.on_changed)
        self.on_changed()

    def delete_rule(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.rowCount():
            rows = [self.table.rowCount() - 1]
        for r in rows:
            self.table.removeRow(r)
        self.on_changed()

    def clear_rules(self) -> None:
        self.table.setRowCount(0)
        self.on_changed()

    def _raw_rules(self) -> list[dict]:
        rules = []
        for r in range(self.table.rowCount()):
            rules.append({
                "startpage": self.table.cellWidget(r, 0).value() - 1,
                "style": self.table.cellWidget(r, 1).currentData(),
                "prefix": self.table.cellWidget(r, 2).text(),
                "firstpagenum": self.table.cellWidget(r, 3).value(),
            })
        return rules

    def validate(self) -> str | None:
        if not hasattr(self, "table"):
            return None
        rules = self._raw_rules()
        if not rules:
            return None
        try:
            stamp.preview_labels(self.page_count, rules, limit=self.page_count)
        except PageOpError as e:
            return str(e)
        return None

    def on_changed(self, *_args) -> None:
        if hasattr(self, "preview"):
            rules = self._raw_rules()
            if not rules:
                self.preview.setText(_("Nessuna etichetta: le pagine saranno numerate 1, 2, 3…"))
            else:
                try:
                    labels = stamp.preview_labels(self.page_count, rules, limit=8)
                    more = "…" if self.page_count > 8 else ""
                    self.preview.setText(_("Anteprima: {l}").format(l=", ".join(labels) + more))
                except PageOpError:
                    self.preview.setText("")
        super().on_changed()

    def engine_options(self) -> dict:
        return {"rules": self._raw_rules()}

    def run(self, pdf, opts):
        if not opts["rules"]:
            stamp.remove_page_labels(pdf)
        else:
            stamp.set_page_labels(pdf, opts["rules"])
        return True


# =========================================================================
# Punti d'ingresso
# =========================================================================

def _open(dialog_cls: type[StampDialog], ctx: Context, view: DocumentView, selected: list[int] | None,
          done: Callable[[StampDialog, object], None] | None = None) -> bool:
    if view is None:
        return False
    parent = getattr(ctx, "window", None) if ctx is not None else None
    try:
        dlg = dialog_cls(parent, view.document, list(selected or []), ctx=ctx,
                         current_page=getattr(view, "current_page", 0))
    except PageOpError as e:
        ctx.error(_(dialog_cls.TITLE), str(e))
        return False
    try:
        if not _accepted(dlg.exec()):
            return False
        try:
            result = dlg.apply()
        except OperationCancelled:
            return False   # annullata dall'utente (es. avviso di Firma)
        except PageOpError as e:
            ctx.error(_(dialog_cls.TITLE), str(e))
            return False
        except Exception as e:  # mai chiudere l'applicazione per un errore del modulo
            ctx.error(_(dialog_cls.TITLE), _("Operazione non riuscita: {e}").format(e=e))
            return False
        if done is not None:
            done(dlg, result)
        return True
    finally:
        dlg.deleteLater()


def _status(ctx, text: str) -> None:
    try:
        ctx.status(text)
    except Exception:
        pass


def open_watermark_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(WatermarkDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Filigrana aggiunta a {n} pagine.").format(n=len(r or []))))


def open_remove_stamps_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    if view is None:
        return False
    with ENGINE_LOCK:
        found = stamp.list_watermarks(view.document.pdf)
    if not found:
        ctx.info(_("Rimuovi filigrane e timbri"),
                 _("Il documento non contiene filigrane, sfondi o numerazioni aggiunti con Foliant."))
        return False
    return _open(RemoveStampsDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Elementi rimossi: {n}.").format(n=r)))


def open_background_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(BackgroundDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Sfondo aggiunto a {n} pagine.").format(n=len(r or []))))


def open_page_numbers_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(PageNumbersDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Numeri aggiunti a {n} pagine.").format(n=len(r or []))))


def open_bates_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(BatesDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Numerazione Bates fino a {last}.").format(last=r)))


def open_header_footer_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(HeaderFooterDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Intestazione e piè di pagina aggiunti a {n} pagine.").format(
                     n=len(r or []))))


def open_page_labels_dialog(ctx: Context, view: DocumentView, selected: list[int]) -> bool:
    return _open(PageLabelsDialog, ctx, view, selected,
                 lambda d, r: _status(ctx, _("Etichette di pagina aggiornate.")))


TRANSLATIONS: dict[str, str] = {
    "Come la lingua": "As the language",
    '(nessuno)': '(none)',
    'A schermo e in stampa': 'On screen and in print',
    'Adatta alla pagina': 'Fit to page',
    'Alcune pagine sono immagini a tutta pagina (scansioni): lo sfondo non sarà visibile.': 'Some pages are full-page images (scans): the background will not be visible.',
    'Alcune pagine sono immagini a tutta pagina (scansioni): una filigrana dietro al contenuto non si vedrebbe. Meglio sopra con opacità ridotta.': 'Some pages are full-page images (scans): a watermark behind the content would not be visible. Better above with reduced opacity.',
    'Anteprima della pagina:': 'Preview page:',
    'Carta intestata (PDF)': 'Letterhead (PDF)',
    'Dalla pagina:': 'From page:',
    'Dimensione reale': 'Actual size',
    'Elimina modello': 'Delete template',
    'Escludi le pagine:': 'Exclude pages:',
    'Filigrana:': 'Watermark:',
    'Il PDF sorgente non è leggibile.': 'The source PDF cannot be read.',
    'Il documento ha già numeri di pagina di Foliant: puoi modificarli o rimuoverli.': 'The document already has Foliant page numbers: you can modify or remove them.',
    "Il documento è PDF/A-1, che non ammette la trasparenza: con un'opacità inferiore al 100% il file non sarà più conforme.": 'The document is PDF/A-1, which does not allow transparency: with an opacity below 100% the file will no longer be compliant.',
    'Inserisci campo': 'Insert field',
    'Logo:': 'Logo:',
    'Modello:': 'Template:',
    'Modifica la filigrana {k}: {d}': 'Modify watermark {k}: {d}',
    'Nessun PDF scelto': 'No PDF chosen',
    'Nessun elemento di Foliant nel documento.': 'No Foliant items in the document.',
    'Nome del modello:': 'Template name:',
    'Non ci sono pagine da numerare.': 'There are no pages to number.',
    'Nuova filigrana': 'New watermark',
    'PDF:': 'PDF:',
    'Pagina 1 sulla prima pagina, pagina 2 sulle altre': 'Page 1 on the first page, page 2 on the others',
    'Pagina PDF': 'PDF page',
    'Pagina {pagina} di {totale}': 'Page {pagina} of {totale}',
    'Pagine da escludere: {e}': 'Pages to exclude: {e}',
    'Pagine pari': 'Even pages',
    'Pagine pari diverse': 'Different even pages',
    'Percentuale': 'Percentage',
    'Più righe con Invio. Campi: {pagina}, {totale}, {data:gg/MM/aaaa}, {nome}, {titolo}…': 'Press Enter for more lines. Fields: {pagina}, {totale}, {data:gg/MM/aaaa}, {nome}, {titolo}…',
    'Prima pagina': 'First page',
    'Prima pagina diversa': 'Different first page',
    'Riempi la pagina': 'Fill the page',
    'Rimuove gli elementi aggiunti con Foliant. Il resto della pagina non cambia. Filigrane e numeri non creati da Foliant non vengono rimossi.': 'Removes the items added with Foliant. The rest of the page does not change. Watermarks and numbers not created by Foliant are not removed.',
    'Rimuovi numeri': 'Remove numbers',
    'Ripeti a mosaico': 'Tile',
    'Salva modello': 'Save template',
    'Salva modello…': 'Save template…',
    'Scala:': 'Scale:',
    'Scegli PDF': 'Choose PDF',
    'Scegli un PDF.': 'Choose a PDF.',
    'Scostamento:': 'Offset:',
    'Solo a schermo': 'Screen only',
    'Solo in stampa': 'Print only',
    'Sottolineato': 'Underlined',
    "Stampa l'etichetta di pagina al posto del numero": 'Print the page label instead of the number',
    'Testi per:': 'Texts for:',
    'Togli il logo': 'Remove the logo',
    'Tutte le pagine': 'All pages',
    'Visibilità:': 'Visibility:',
    'altezza': 'height',
    'es. 1, 5-6': 'e.g. 1, 5-6',
    'immagine': 'image',
    'orizzontale': 'horizontal',
    'pag. ': 'p. ',
    'pagine {p}': 'pages {p}',
    'prima interessata': 'first affected',
    'spaziatura': 'spacing',
    'verticale': 'vertical',
    '{pagina} numero, {totale} ultimo numero, {etichetta} etichetta di pagina': '{pagina} number, {totale} last number, {etichetta} page label',
    '{w} — pagine {p}': '{w} — pages {p}',
    'Sostituisci gli sfondi già presenti': 'Replace existing backgrounds',
    'Pagina {pagina}': 'Page {pagina}',
    '{pagina}/{totale}': '{pagina}/{totale}',
    "Nessuna": "None",
    "Lato esterno (dispari a destra)": "Outer side (odd on the right)",
    "Lato interno (dispari a sinistra)": "Inner side (odd on the left)",
    "Stile:": "Style:",
    "Fronte/retro:": "Double-sided:",
    "Sostituisci i numeri di pagina già presenti": "Replace existing page numbers",
    "Sostituisci la numerazione Bates già presente": "Replace existing Bates numbering",
    "Sostituisci intestazioni e piè di pagina già presenti": "Replace existing headers and footers",
    '1, 2, 3': '1, 2, 3',
    'A, B, C': 'A, B, C',
    'I, II, III': 'I, II, III',
    'a, b, c': 'a, b, c',
    'i, ii, iii': 'i, ii, iii',
    'Aggiungi regola': 'Add rule',
    'Al centro': 'Center',
    'Al centro a destra': "Center right",
    'Al centro a sinistra': "Center left",
    'Annulla': 'Cancel',
    'Anteprima': 'Preview',
    'Anteprima non disponibile': 'Preview not available',
    'Anteprima: {l}': 'Preview: {l}',
    'Applica': 'Apply',
    'Aspetto': 'Appearance',
    'BOZZA': 'DRAFT',
    'Carattere:': 'Font:',
    'Centro': 'Center',
    'Cifre:': 'Digits:',
    'Codice': 'Code',
    'Colore': 'Color',
    'Colore:': 'Color:',
    'Controlla le pagine indicate.': 'Check the pages entered.',
    'Da {a} a {b}': 'From {a} to {b}',
    'Dalla pagina': 'From page',
    'Destra': 'Right',
    'Dietro al contenuto': 'Behind the content',
    'Elementi da rimuovere': 'Items to remove',
    'Elementi da rimuovere: {n}': 'Items to remove: {n}',
    'Elementi rimossi: {n}.': 'Items removed: {n}.',
    'Elimina regola': 'Delete rule',
    'Etichette di pagina aggiornate.': 'Page labels updated.',
    'Filigrana aggiunta a {n} pagine.': 'Watermark added to {n} pages.',
    'Filigrane': 'Watermarks',
    'Formato data:': 'Date format:',
    'Formato della data non valido.': 'Invalid date format.',
    "numero\x04Formato:": "Format:",
    'Il documento non contiene filigrane, sfondi o numerazioni aggiunti con Foliant.': 'The document contains no watermarks, backgrounds or numbering added with Foliant.',
    'Il formato del numero non può essere vuoto.': 'The number format cannot be empty.',
    'Immagine': 'Image',
    'Immagine:': 'Image:',
    'Immagini': 'Images',
    'In alto a destra': 'Top right',
    'In alto a sinistra': 'Top left',
    'In alto al centro': 'Top center',
    'In basso a destra': 'Bottom right',
    'In basso a sinistra': 'Bottom left',
    'In basso al centro': 'Bottom center',
    'Inizia da:': 'Start at:',
    'Intervallo': 'Range',
    'Intestazione e piè di pagina aggiunti a {n} pagine.': 'Header and footer added to {n} pages.',
    'Intestazione:': 'Header:',
    'Intestazioni e piè di pagina': 'Headers and footers',
    "Larghezza dell'immagine rispetto alla pagina": 'Image width relative to the page',
    'Larghezza:': 'Width:',
    'Le cifre indicate non bastano per numerare tutte le pagine.': 'The given digits are not enough to number all pages.',
    'Margine:': 'Margin:',
    'Nessun elemento di Foliant nelle pagine indicate.': 'No Foliant items on the given pages.',
    'Nessun numero (solo prefisso)': 'No number (prefix only)',
    'Nessuna anteprima': 'No preview',
    'Nessuna etichetta: le pagine saranno numerate 1, 2, 3…': 'No labels: pages will be numbered 1, 2, 3…',
    'Nessuna immagine scelta': 'No image chosen',
    "Non c'è niente da rimuovere.": 'There is nothing to remove.',
    'Non ci sono pagine da numerare dopo aver saltato la prima.': 'There are no pages to number after skipping the first.',
    'Non numerare la prima pagina (ma contala)': 'Do not number the first page (but count it)',
    'Numerazione': 'Numbering',
    'Numerazione Bates': 'Bates numbering',
    'Numerazione Bates fino a {last}.': 'Bates numbering up to {last}.',
    'Numeri aggiunti a {n} pagine.': 'Numbers added to {n} pages.',
    'Numeri di pagina': 'Page numbers',
    'Oggi: {d}': 'Today: {d}',
    'Opacità:': 'Opacity:',
    'Operazione non riuscita: {e}': 'Operation failed: {e}',
    'Pagina {n}': 'Page {n}',
    'Pagina {n} di {total}': 'Page {n} of {total}',
    'Pagine': 'Pages',
    'Pagine selezionate ({n})': 'Selected pages ({n})',
    'Piè di pagina:': 'Footer:',
    'Posizione:': 'Position:',
    'Prefisso': 'Prefix',
    'Prefisso:': 'Prefix:',
    'Primo numero': 'First number',
    'Primo numero:': 'First number:',
    'Rimuove gli elementi aggiunti con Foliant. Il resto della pagina non cambia.': 'Removes the items added with Foliant. The rest of the page does not change.',
    'Rimuovi filigrane e timbri': 'Remove watermarks and stamps',
    'Rimuovi tutte le etichette': 'Remove all labels',
    'Rotazione:': 'Rotation:',
    'Scegli almeno un tipo di elemento.': 'Choose at least one item type.',
    'Scegli colore': 'Choose color',
    'Scegli immagine': 'Choose image',
    "Scegli un'immagine.": 'Choose an image.',
    'Scegli…': 'Choose…',
    'Scrivi almeno un testo di intestazione o piè di pagina.': 'Enter at least one header or footer text.',
    'Scrivi il testo della filigrana.': 'Enter the watermark text.',
    'Segnaposto: {n} pagina, {total} totale pagine, {date} data, {file} nome del file': 'Placeholders: {n} page, {total} total pages, {date} date, {file} file name',
    'Sfondi': 'Backgrounds',
    'Sfondo': 'Background',
    'Sfondo aggiunto a {n} pagine.': 'Background added to {n} pages.',
    'Sinistra': 'Left',
    'Sostituisci le filigrane già presenti': 'Replace existing watermarks',
    'Stile': 'Style',
    'Suffisso:': 'Suffix:',
    'Testi': 'Texts',
    'Testo': 'Text',
    'Testo della filigrana': 'Watermark text',
    'Testo:': 'Text:',
    'Tutte': 'All',
    'es. 1-3, 5, 8-': 'e.g. 1-3, 5, 8-',
    'es. CAUSA-': 'e.g. CASE-',
    '{name} ({n})': '{name} ({n})',
    '{n} numero, {total} ultimo numero, {label} etichetta di pagina': '{n} number, {total} last number, {label} page label',
    'Lo sfondo è disegnato sotto il contenuto: è visibile dove la pagina non ha già un fondo proprio.': 'The background is drawn under the content: it shows where the page has no background of its own.',
    'Le etichette sono i numeri mostrati dai lettori PDF (es. i, ii per la prefazione e poi 1, 2, 3). Ogni regola vale fino alla successiva.': 'Labels are the numbers shown by PDF readers (e.g. i, ii for the preface and then 1, 2, 3). Each rule applies until the next one.',
    'Filigrana': 'Watermark',
    'Aggiungi filigrana': 'Add watermark',
    'Aggiungi sfondo': 'Add background',
    'Aggiungi numeri di pagina': 'Add page numbers',
    'Aggiungi numerazione Bates': 'Add Bates numbering',
    'Intestazione e piè di pagina': 'Header and footer',
    'Aggiungi intestazione e piè di pagina': 'Add header and footer',
    'Etichette di pagina': 'Page labels',
    'Imposta etichette di pagina': 'Set page labels',
    'Helvetica': 'Helvetica',
    'Helvetica grassetto': 'Helvetica bold',
    'Helvetica corsivo': 'Helvetica italic',
    'Times': 'Times',
    'Times grassetto': 'Times bold',
    'Times corsivo': 'Times italic',
    'Courier': 'Courier',
    'Courier grassetto': 'Courier bold',
}
