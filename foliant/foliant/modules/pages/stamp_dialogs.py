"""Dialoghi per filigrane, sfondi, numeri di pagina, Bates, intestazioni e
etichette di pagina.

Ogni dialogo è costruibile da solo (`WatermarkDialog(parent, document, selected)`),
espone `options()` (argomenti per la funzione del motore) e `apply()` (esegue
la modifica come unico passo annullabile). Le funzioni `open_*_dialog(ctx,
view, selected)` sono i punti d'ingresso per azioni e menu.
"""

from __future__ import annotations

import datetime as _dt
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
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
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

from foliant.api import ENGINE_LOCK, _, icon, theme

from . import stamp
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
        root.addWidget(self.error)
        root.addWidget(self.buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh_preview)

        self.build()
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

    def options(self) -> dict:
        opts = self.engine_options()
        if self.scope is not None:
            opts["pages"] = self.scope.pages() if self.scope.is_valid() else None
        return opts

    def is_valid(self) -> bool:
        if self.scope is not None and not self.scope.is_valid():
            return False
        return self.validate() is None

    def on_changed(self, *_args) -> None:
        msg = self.validate()
        self.error.setText(msg or "")
        self.error.setVisible(bool(msg))
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
        kind = QHBoxLayout()
        self.kind_text = QRadioButton(_("Testo"))
        self.kind_image = QRadioButton(_("Immagine"))
        self.kind_text.setChecked(True)
        kind.addWidget(self.kind_text)
        kind.addWidget(self.kind_image)
        kind.addStretch(1)
        self.form.addLayout(kind)

        self.stack = QStackedWidget()
        # Testo
        tw = QWidget()
        tf = QFormLayout(tw)
        tf.setContentsMargins(0, 0, 0, 0)
        self.text = QLineEdit(_("BOZZA"))
        self.text.setPlaceholderText(_("Testo della filigrana"))
        self.font = _font_combo("hebo")
        self.font_size = _spin(4, 400, 60, " pt")
        self.color = ColorButton((0.75, 0.1, 0.1))
        row = QHBoxLayout()
        row.addWidget(self.font, 1)
        row.addWidget(self.font_size)
        row.addWidget(self.color)
        tf.addRow(_("Testo:"), self.text)
        tf.addRow(_("Carattere:"), row)
        self.stack.addWidget(tw)
        # Immagine
        iw = QWidget()
        imf = QFormLayout(iw)
        imf.setContentsMargins(0, 0, 0, 0)
        self.image = ImageChooser(self.ctx)
        self.scale = _spin(1, 500, 50, " %")
        self.scale.setToolTip(_("Larghezza dell'immagine rispetto alla pagina"))
        imf.addRow(_("Immagine:"), self.image)
        imf.addRow(_("Larghezza:"), self.scale)
        self.stack.addWidget(iw)
        self.form.addWidget(self.stack)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.opacity = OpacitySlider(30)
        self.rotation = _spin(-180, 180, 45, "°")
        self.position = PositionPicker("center")
        self.behind = QCheckBox(_("Dietro al contenuto"))
        self.replace = QCheckBox(_("Sostituisci le filigrane già presenti"))
        lf.addRow(_("Opacità:"), self.opacity)
        lf.addRow(_("Rotazione:"), self.rotation)
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow("", self.behind)
        lf.addRow("", self.replace)
        self.form.addWidget(look)

        self.kind_text.toggled.connect(lambda on: self.stack.setCurrentIndex(0 if on else 1))
        self.connect_changed(self.kind_text.toggled, self.text.textChanged, self.font.currentIndexChanged,
                             self.font_size.valueChanged, self.color.changed, self.image.changed,
                             self.scale.valueChanged, self.opacity.changed, self.rotation.valueChanged,
                             self.position.changed, self.behind.toggled)

    def validate(self) -> str | None:
        if self.kind_text.isChecked():
            if not self.text.text().strip():
                return _("Scrivi il testo della filigrana.")
        elif not self.image.path():
            return _("Scegli un'immagine.")
        return None

    def engine_options(self) -> dict:
        opts = {
            "opacity": self.opacity.value(),
            "rotation": self.rotation.value(),
            "position": self.position.value(),
            "behind": self.behind.isChecked(),
        }
        if self.kind_text.isChecked():
            opts.update(text=self.text.text(), font=self.font.currentData(), font_size=self.font_size.value(),
                        color=self.color.color())
        else:
            opts.update(image=self.image.path(), scale=self.scale.value() / 100.0)
        return opts

    def options(self) -> dict:
        opts = super().options()
        opts["replace"] = self.replace.isChecked()
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        replace = opts.pop("replace", False)
        pages = opts.pop("pages", None)
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


class RemoveStampsDialog(StampDialog):
    TITLE = "Rimuovi filigrane e timbri"
    UNDO_LABEL = "Rimuovi filigrane e timbri"
    PREVIEW = False

    def build(self) -> None:
        with ENGINE_LOCK:
            self.found = stamp.list_watermarks(self.document.pdf)
        self.form.addWidget(_hint(_("Rimuove gli elementi aggiunti con Foliant. Il resto della pagina non cambia.")))
        box = QGroupBox(_("Elementi da rimuovere"))
        lay = QVBoxLayout(box)
        self.checks: dict[str, QCheckBox] = {}
        for kind, name in _kind_names().items():
            cb = QCheckBox()
            cb.setChecked(True)
            self.checks[kind] = cb
            lay.addWidget(cb)
            cb.toggled.connect(self.on_changed)
        self.form.addWidget(box)
        self.summary = _hint("")
        self.form.addWidget(self.summary)

    def _counts(self) -> dict[str, int]:
        pages = None
        if self.scope is not None and self.scope.is_valid():
            pages = self.scope.pages()
        counts = {k: 0 for k in stamp.KINDS}
        for pno, kinds in self.found.items():
            if pages is not None and pno not in pages:
                continue
            for k in kinds:
                counts[k] = counts.get(k, 0) + 1
        return counts

    def on_changed(self, *_args) -> None:
        if hasattr(self, "checks"):
            counts = self._counts()
            names = _kind_names()
            for kind, cb in self.checks.items():
                cb.setText(_("{name} ({n})").format(name=names[kind], n=counts.get(kind, 0)))
                cb.setEnabled(counts.get(kind, 0) > 0)
            total = sum(counts[k] for k, cb in self.checks.items() if cb.isChecked() and cb.isEnabled())
            self.summary.setText(_("Elementi da rimuovere: {n}").format(n=total) if total
                                 else _("Nessun elemento di Foliant nelle pagine indicate."))
        super().on_changed()

    def selected_kinds(self) -> list[str]:
        return [k for k, cb in self.checks.items() if cb.isChecked()]

    def validate(self) -> str | None:
        if not hasattr(self, "checks"):
            return None
        counts = self._counts()
        if not any(counts.get(k, 0) for k in self.selected_kinds()):
            return _("Non c'è niente da rimuovere.") if any(counts.values()) or not self.found else \
                _("Scegli almeno un tipo di elemento.")
        return None

    def engine_options(self) -> dict:
        return {"kind": self.selected_kinds()}

    def run(self, pdf, opts):
        return stamp.remove_watermarks(pdf, opts.get("pages"), kind=opts["kind"])


# =========================================================================
# Sfondo
# =========================================================================

class BackgroundDialog(StampDialog):
    TITLE = "Sfondo"
    UNDO_LABEL = "Aggiungi sfondo"

    def build(self) -> None:
        kind = QHBoxLayout()
        self.kind_color = QRadioButton(_("Colore"))
        self.kind_image = QRadioButton(_("Immagine"))
        self.kind_color.setChecked(True)
        kind.addWidget(self.kind_color)
        kind.addWidget(self.kind_image)
        kind.addStretch(1)
        self.form.addLayout(kind)
        box = QGroupBox(_("Sfondo"))
        f = QFormLayout(box)
        self.color = ColorButton((1.0, 0.97, 0.86))
        self.image = ImageChooser(self.ctx)
        self.opacity = OpacitySlider(100)
        f.addRow(_("Colore:"), self.color)
        f.addRow(_("Immagine:"), self.image)
        f.addRow(_("Opacità:"), self.opacity)
        self.form.addWidget(box)
        self.form.addWidget(_hint(_("Lo sfondo è disegnato sotto il contenuto: è visibile dove la pagina "
                                    "non ha già un fondo proprio.")))
        self.kind_color.toggled.connect(self._sync)
        self._sync()
        self.connect_changed(self.kind_color.toggled, self.color.changed, self.image.changed, self.opacity.changed)

    def _sync(self) -> None:
        self.color.setEnabled(self.kind_color.isChecked())
        self.image.setEnabled(not self.kind_color.isChecked())

    def validate(self) -> str | None:
        if self.kind_image.isChecked() and not self.image.path():
            return _("Scegli un'immagine.")
        return None

    def engine_options(self) -> dict:
        opts = {"opacity": self.opacity.value()}
        if self.kind_color.isChecked():
            opts["color"] = self.color.color()
        else:
            opts["image"] = self.image.path()
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
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

    FORMATS = ("{n}", "Pagina {n}", "Pagina {n} di {total}", "{n} / {total}", "- {n} -", "{label}")

    def build(self) -> None:
        box = QGroupBox(_("Numerazione"))
        f = QFormLayout(box)
        self.fmt = QComboBox()
        self.fmt.setEditable(True)
        for item in self.FORMATS:
            self.fmt.addItem(_(item) if "Pagina" in item else item)
        self.fmt.setCurrentIndex(0)
        self.start = _spin(0, 999999, 1)
        self.skip_first = QCheckBox(_("Non numerare la prima pagina (ma contala)"))
        f.addRow(_("Formato:"), self.fmt)
        f.addRow("", _hint(_("{n} numero, {total} ultimo numero, {label} etichetta di pagina")))
        f.addRow(_("Inizia da:"), self.start)
        f.addRow("", self.skip_first)
        self.form.addWidget(box)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.position = PositionPicker("bottom-center")
        self.margin = _spin(0, 200, 12, " mm")
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow(_("Margine:"), self.margin)
        self.font, self.font_size, self.color = _text_style_rows(lf)
        self.form.addWidget(look)
        self.connect_changed(self.fmt.currentTextChanged, self.start.valueChanged, self.skip_first.toggled,
                             self.position.changed, self.margin.valueChanged, self.font.currentIndexChanged,
                             self.font_size.valueChanged, self.color.changed)

    def validate(self) -> str | None:
        if not self.fmt.currentText().strip():
            return _("Il formato del numero non può essere vuoto.")
        if self.skip_first.isChecked() and self.scope is not None and self.scope.is_valid():
            pages = self.scope.pages()
            if (pages is not None and len(pages) < 2) or (pages is None and self.page_count < 2):
                return _("Non ci sono pagine da numerare dopo aver saltato la prima.")
        return None

    def engine_options(self) -> dict:
        return {
            "fmt": self.fmt.currentText(), "start": self.start.value(), "position": self.position.value(),
            "margin": self.margin.value() * MM, "font": self.font.currentData(),
            "font_size": self.font_size.value(), "color": self.color.color(),
            "skip_first": self.skip_first.isChecked(),
        }

    def preview_index(self) -> int:
        pages = self.target_pages()
        if self.skip_first.isChecked() and len(pages) > 1:
            return pages[1]
        return pages[0] if pages else 0

    def preview_options(self, opts: dict, page_index: int) -> dict:
        pages = self.target_pages()
        total = str(opts["start"] + len(pages) - 1)
        label = self.document.pdf[page_index].get_label() or str(page_index + 1)
        opts["fmt"] = opts["fmt"].replace("{total}", total).replace("{label}", label)
        k = pages.index(page_index) if page_index in pages else 0
        opts["start"] = opts["start"] + k
        opts["skip_first"] = False
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
        return stamp.add_page_numbers(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Bates
# =========================================================================

class BatesDialog(StampDialog):
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
        return stamp.add_bates(pdf, opts.pop("pages", None), **opts)


# =========================================================================
# Intestazione e piè di pagina
# =========================================================================

class HeaderFooterDialog(StampDialog):
    TITLE = "Intestazione e piè di pagina"
    UNDO_LABEL = "Aggiungi intestazione e piè di pagina"

    DATE_FORMATS = ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %B %Y")

    def build(self) -> None:
        box = QGroupBox(_("Testi"))
        g = QGridLayout(box)
        g.addWidget(_hint(_("Sinistra")), 0, 1)
        g.addWidget(_hint(_("Centro")), 0, 2)
        g.addWidget(_hint(_("Destra")), 0, 3)
        g.addWidget(QLabel(_("Intestazione:")), 1, 0)
        g.addWidget(QLabel(_("Piè di pagina:")), 2, 0)
        self.fields: dict[str, QLineEdit] = {}
        for r, part in enumerate(("header", "footer")):
            for c, side in enumerate(("left", "center", "right")):
                e = QLineEdit()
                e.setMinimumWidth(120)
                self.fields[f"{part}_{side}"] = e
                g.addWidget(e, r + 1, c + 1)
                e.textChanged.connect(self.on_changed)
        self.fields["footer_right"].setText(_("Pagina {n} di {total}"))
        self.fields["footer_right"].setCursorPosition(0)
        g.addWidget(_hint(_("Segnaposto: {n} pagina, {total} totale pagine, {date} data, {file} nome del file")),
                    3, 0, 1, 4)
        self.form.addWidget(box)

        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.margin = _spin(0, 200, 12, " mm")
        self.date_format = QComboBox()
        self.date_format.setEditable(True)
        today = _dt.date.today()
        for fmt in self.DATE_FORMATS:
            self.date_format.addItem(fmt)
            self.date_format.setItemData(self.date_format.count() - 1, today.strftime(fmt), Qt.ItemDataRole.ToolTipRole)
        lf.addRow(_("Margine:"), self.margin)
        self.font, self.font_size, self.color = _text_style_rows(lf)
        lf.addRow(_("Formato data:"), self.date_format)
        self.date_example = _hint("")
        lf.addRow("", self.date_example)
        self.form.addWidget(look)
        self.connect_changed(self.margin.valueChanged, self.font.currentIndexChanged, self.font_size.valueChanged,
                             self.color.changed, self.date_format.currentTextChanged)

    def _date_text(self) -> str | None:
        try:
            return _dt.date.today().strftime(self.date_format.currentText() or "%d/%m/%Y")
        except (ValueError, TypeError):
            return None

    def on_changed(self, *_args) -> None:
        if hasattr(self, "date_example"):
            d = self._date_text()
            self.date_example.setText(_("Oggi: {d}").format(d=d) if d else "")
        super().on_changed()

    def validate(self) -> str | None:
        if not hasattr(self, "date_format"):
            return None
        if not any(e.text().strip() for e in self.fields.values()):
            return _("Scrivi almeno un testo di intestazione o piè di pagina.")
        if self._date_text() is None:
            return _("Formato della data non valido.")
        return None

    def engine_options(self) -> dict:
        opts = {k: e.text() for k, e in self.fields.items()}
        opts.update(margin=self.margin.value() * MM, font=self.font.currentData(), font_size=self.font_size.value(),
                    color=self.color.color(), date_format=self.date_format.currentText() or "%d/%m/%Y",
                    filename=self.document.display_name)
        return opts

    def preview_options(self, opts: dict, page_index: int) -> dict:
        label = self.document.pdf[page_index].get_label() or str(page_index + 1)
        for k in self.fields:
            opts[k] = (opts[k].replace("{n}", str(page_index + 1)).replace("{total}", str(self.page_count))
                       .replace("{label}", label))
        return opts

    def run(self, pdf, opts):
        opts = dict(opts)
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
    '1, 2, 3': '1, 2, 3',
    'A, B, C': 'A, B, C',
    'I, II, III': 'I, II, III',
    'a, b, c': 'a, b, c',
    'i, ii, iii': 'i, ii, iii',
    'Aggiungi regola': 'Add rule',
    'Al centro': 'Center',
    'Al centro a destra': 'Middle right',
    'Al centro a sinistra': 'Middle left',
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
    'Formato:': 'Format:',
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
