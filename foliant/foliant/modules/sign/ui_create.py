"""Dialogo «Crea firma»: schede Disegna, Digita e Immagine (SIG-01/02/03)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QRadioButton, QSlider, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from foliant.api import _, theme

from . import graphic as g

MIN_STROKE_LENGTH = 25.0   # pixel: sotto questa lunghezza «Usa» resta disattivato
COLORS = [("#000000", "Nero"), ("#1d3fb8", "Blu"), ("#b91c1c", "Rosso")]
IMAGE_FILTER = "Immagini (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp)"


def _checker(painter: QPainter, rect: QRectF, size: int = 8) -> None:
    painter.save()
    painter.setClipRect(rect)
    a, b = QColor(255, 255, 255), QColor(220, 222, 226)
    y = rect.top()
    row = 0
    while y < rect.bottom():
        x = rect.left()
        col = row % 2
        while x < rect.right():
            painter.fillRect(QRectF(x, y, size, size), a if col % 2 == 0 else b)
            x += size
            col += 1
        y += size
        row += 1
    painter.restore()


def art_pixmap(art: g.VectorArt, color: str, size: QSize, padding: int = 6) -> QPixmap:
    """Anteprima di un disegno vettoriale."""
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    if art is None or art.width <= 0:
        return pm
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    avail_w, avail_h = size.width() - 2 * padding, size.height() - 2 * padding
    s = min(avail_w / art.width, avail_h / art.height)
    ox = padding + (avail_w - art.width * s) / 2
    oy = padding + (avail_h - art.height * s) / 2
    col = QColor(color)
    for item in art.items:
        if item[0] == "stroke":
            pts = item[1]
            for a, b in zip(pts, pts[1:] or pts):
                pen = QPen(col, max(0.6, (a[2] + b[2]) / 2 * s))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(QPointF(ox + a[0] * s, oy + a[1] * s), QPointF(ox + b[0] * s, oy + b[1] * s))
        else:
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            for op in item[1]:
                if op[0] == "m":
                    path.moveTo(ox + op[1] * s, oy + op[2] * s)
                elif op[0] == "l":
                    path.lineTo(ox + op[1] * s, oy + op[2] * s)
                elif op[0] == "c":
                    path.cubicTo(ox + op[1] * s, oy + op[2] * s, ox + op[3] * s, oy + op[4] * s,
                                 ox + op[5] * s, oy + op[6] * s)
                elif op[0] == "h":
                    path.closeSubpath()
            p.fillPath(path, col)
    p.end()
    return pm


def item_pixmap(item: g.SignatureItem, size: QSize) -> QPixmap:
    if item.art is not None:
        return art_pixmap(item.art, item.color, size)
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    if item.png:
        img = QImage.fromData(item.png)
        if not img.isNull():
            scaled = img.scaled(size.shrunkBy(QSize(6, 6)) if hasattr(size, "shrunkBy") else size,
                                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            p = QPainter(pm)
            p.drawImage(QPointF((size.width() - scaled.width()) / 2, (size.height() - scaled.height()) / 2), scaled)
            p.end()
    return pm


# =============================================================== area di disegno
class DrawPad(QWidget):
    """Area di disegno 3:1 con linea di base; registra tratti (x, y, pressione)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_StaticContents)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName(_("Area di disegno della firma"))
        self.strokes: list[list[tuple[float, float, float]]] = []
        self._current: list[tuple[float, float, float]] | None = None
        self.color = "#000000"
        self.base_width = 2.6

    def sizeHint(self) -> QSize:
        return QSize(600, 200)

    def heightForWidth(self, w: int) -> int:
        return w // 3

    def hasHeightForWidth(self) -> bool:
        return True

    def clear(self) -> None:
        self.strokes.clear()
        self._current = None
        self.update()
        self.changed.emit()

    def undo_stroke(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self.update()
            self.changed.emit()

    def length(self) -> float:
        return g.strokes_length(self.strokes)

    def is_usable(self) -> bool:
        return self.length() >= MIN_STROKE_LENGTH

    # eventi sintetici utili anche nei test
    def add_point(self, x: float, y: float, pressure: float = 1.0, start: bool = False) -> None:
        if start or self._current is None:
            self._current = []
            self.strokes.append(self._current)
        self._current.append((float(x), float(y), float(pressure)))
        self.update()

    def end_stroke(self) -> None:
        self._current = None
        self.changed.emit()

    @staticmethod
    def _pressure(ev) -> float:
        try:
            pts = ev.points()
            if pts:
                pr = pts[0].pressure()
                if 0 < pr < 1:
                    return pr
        except Exception:
            pass
        return 1.0

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            pos = ev.position()
            self.add_point(pos.x(), pos.y(), self._pressure(ev), start=True)

    def mouseMoveEvent(self, ev) -> None:
        if self._current is not None and ev.buttons() & Qt.MouseButton.LeftButton:
            pos = ev.position()
            self.add_point(pos.x(), pos.y(), self._pressure(ev))

    def mouseReleaseEvent(self, ev) -> None:
        if self._current is not None:
            self.end_stroke()

    def tabletEvent(self, ev) -> None:
        pos = ev.position()
        t = ev.type()
        if t == ev.Type.TabletPress:
            self.add_point(pos.x(), pos.y(), ev.pressure(), start=True)
        elif t == ev.Type.TabletMove and self._current is not None:
            self.add_point(pos.x(), pos.y(), ev.pressure())
        elif t == ev.Type.TabletRelease:
            self.end_stroke()
        ev.accept()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        p.fillRect(r, QColor(255, 255, 255))
        pen = QPen(theme.color("border"), 1)
        p.setPen(pen)
        p.drawRect(r.adjusted(0, 0, -1, -1))
        base_y = r.height() * 0.72
        guide = QPen(QColor(150, 158, 170), 1, Qt.PenStyle.DashLine)
        p.setPen(guide)
        p.drawLine(QPointF(24, base_y), QPointF(r.width() - 24, base_y))
        p.drawText(QPointF(24, base_y + 18), "✕")
        if not self.strokes:
            p.setPen(QColor(130, 138, 150))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, _("Disegna qui la firma"))
        col = QColor(self.color)
        for stroke in self.strokes:
            for a, b in zip(stroke, stroke[1:] or stroke):
                w = g.pressure_to_width((a[2] + b[2]) / 2, self.base_width)
                pn = QPen(col, w)
                pn.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pn)
                p.drawLine(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
        p.end()

    def result(self) -> g.VectorArt:
        return g.strokes_to_art(g.process_strokes(self.strokes, self.base_width))


class ColorPicker(QWidget):
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel(_("Colore:")))
        self.group = QButtonGroup(self)
        self.color = COLORS[0][0]
        for i, (hex_, name) in enumerate(COLORS):
            b = QRadioButton(_(name))
            b.setProperty("hex", hex_)
            b.setChecked(i == 0)
            self.group.addButton(b)
            lay.addWidget(b)
        self.custom = QRadioButton(_("Personalizzato…"))
        self.group.addButton(self.custom)
        lay.addWidget(self.custom)
        lay.addStretch(1)
        self.group.buttonClicked.connect(self._clicked)

    def _clicked(self, b) -> None:
        if b is self.custom:
            c = QColorDialog.getColor(QColor(self.color), self, _("Colore della firma"))
            if c.isValid():
                self.color = c.name()
        else:
            self.color = b.property("hex")
        self.changed.emit(self.color)

    def set_color(self, hex_: str) -> None:
        self.color = hex_
        for b in self.group.buttons():
            if b.property("hex") == hex_:
                b.setChecked(True)
                break
        else:
            self.custom.setChecked(True)
        self.changed.emit(hex_)


# ======================================================================= schede
class DrawTab(QWidget):
    validityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.pad = DrawPad(self)
        lay.addWidget(self.pad, 1)
        row = QHBoxLayout()
        self.colors = ColorPicker(self)
        row.addWidget(self.colors, 1)
        row.addWidget(QLabel(_("Spessore:")))
        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(10, 80)
        self.width_slider.setValue(26)
        self.width_slider.setFixedWidth(110)
        self.width_slider.setAccessibleName(_("Spessore del tratto"))
        row.addWidget(self.width_slider)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        self.btn_undo = QPushButton(_("Annulla ultimo tratto"))
        self.btn_clear = QPushButton(_("Cancella tutto"))
        row2.addWidget(self.btn_undo)
        row2.addWidget(self.btn_clear)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.btn_undo.clicked.connect(self.pad.undo_stroke)
        self.btn_clear.clicked.connect(self.pad.clear)
        self.pad.changed.connect(self.validityChanged)
        self.colors.changed.connect(self._set_color)
        self.width_slider.valueChanged.connect(self._set_width)

    def _set_color(self, c: str) -> None:
        self.pad.color = c
        self.pad.update()

    def _set_width(self, v: int) -> None:
        self.pad.base_width = v / 10
        self.pad.update()

    def is_valid(self) -> bool:
        return self.pad.is_usable()

    def result(self, kind: str) -> g.SignatureItem:
        return g.SignatureItem(kind=kind, origin="disegnata", art=self.pad.result(), color=self.pad.color)


class TypeTab(QWidget):
    validityChanged = Signal()

    def __init__(self, default_text: str = "", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.text = QLineEdit(default_text)
        self.text.setMaxLength(60)
        self.text.setPlaceholderText(_("Nome e cognome o iniziali"))
        self.text.setAccessibleName(_("Testo della firma"))
        lay.addWidget(self.text)
        self.list = QListWidget()
        self.list.setIconSize(QSize(320, 56))
        self.list.setAccessibleName(_("Stili di scrittura"))
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.colors = ColorPicker(self)
        row.addWidget(self.colors, 1)
        row.addWidget(QLabel(_("Inclinazione:")))
        self.slant = QSpinBox()
        self.slant.setRange(-20, 30)
        self.slant.setSuffix("°")
        row.addWidget(self.slant)
        lay.addLayout(row)
        self.styles = g.typed_styles()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        self.text.textChanged.connect(lambda _t: self._timer.start())
        self.colors.changed.connect(lambda _c: self.refresh())
        self.slant.valueChanged.connect(lambda _v: self._timer.start())
        self.list.currentRowChanged.connect(lambda _r: self.validityChanged.emit())
        self.refresh()

    def refresh(self) -> None:
        text = self.text.text().strip()
        current = self.list.currentRow()
        self.list.blockSignals(True)
        self.list.clear()
        for style in self.styles:
            it = QListWidgetItem(style.label)
            it.setData(Qt.ItemDataRole.UserRole, style.id)
            if text:
                missing = g.missing_characters(text, style)
                if missing:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
                    it.setText(_("{style} — non compatibile: manca «{chars}»").format(
                        style=style.label, chars="".join(missing[:5])))
                else:
                    try:
                        art = g.text_to_art(text, style, self.slant.value())
                        it.setIcon(art_pixmap(art, self.colors.color, QSize(320, 56)))
                    except g.GraphicSignatureError:
                        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if 0 <= current < self.list.count() and self.list.item(current).flags() & Qt.ItemFlag.ItemIsEnabled:
            self.list.setCurrentRow(current)
        else:
            for i in range(self.list.count()):
                if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                    self.list.setCurrentRow(i)
                    break
        self.validityChanged.emit()

    def preview_count(self) -> int:
        return sum(1 for i in range(self.list.count()) if not self.list.item(i).icon().isNull())

    def current_style(self) -> g.TypedStyle | None:
        it = self.list.currentItem()
        if it is None or not (it.flags() & Qt.ItemFlag.ItemIsEnabled):
            return None
        return g.style_by_id(it.data(Qt.ItemDataRole.UserRole))

    def is_valid(self) -> bool:
        return bool(self.text.text().strip()) and self.current_style() is not None

    def result(self, kind: str) -> g.SignatureItem:
        style = self.current_style()
        text = self.text.text().strip()
        return g.SignatureItem(kind=kind, origin="digitata", text=text, style_id=style.id,
                               slant=float(self.slant.value()), color=self.colors.color,
                               art=g.text_to_art(text, style, self.slant.value()))


class ImagePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.image: QImage | None = None
        self.message = _("Scegli un'immagine della firma")

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        r = QRectF(self.rect())
        _checker(p, r)
        p.setPen(theme.color("border"))
        p.drawRect(r.adjusted(0, 0, -1, -1))
        if self.image is None or self.image.isNull():
            p.setPen(QColor(90, 98, 110))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.message)
        else:
            img = self.image.scaled(self.size().shrunkBy(QSize(12, 12)) if hasattr(self.size(), "shrunkBy")
                                    else self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            p.drawImage(QPointF((self.width() - img.width()) / 2, (self.height() - img.height()) / 2), img)
        p.end()


class ImageTab(QWidget):
    validityChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.btn_file = QPushButton(_("Scegli file…"))
        self.btn_paste = QPushButton(_("Incolla dagli appunti"))
        self.btn_scan = QPushButton(_("Da scanner o fotocamera"))
        self.btn_scan.setEnabled(False)
        self.btn_scan.setToolTip(_("Nessuno scanner o fotocamera disponibile: salva l'immagine e scegli il file."))
        for b in (self.btn_file, self.btn_paste, self.btn_scan):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.preview = ImagePreview(self)
        lay.addWidget(self.preview, 1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel(_("Soglia sfondo:")))
        self.threshold = QSlider(Qt.Orientation.Horizontal)
        self.threshold.setRange(1, 254)
        self.threshold.setAccessibleName(_("Soglia sfondo"))
        r2.addWidget(self.threshold, 1)
        self.remove_bg = QCheckBox(_("Rimuovi lo sfondo"))
        self.remove_bg.setChecked(True)
        r2.addWidget(self.remove_bg)
        lay.addLayout(r2)
        r3 = QHBoxLayout()
        self.btn_rot = QPushButton(_("Ruota 90°"))
        r3.addWidget(self.btn_rot)
        r3.addWidget(QLabel(_("Raddrizza:")))
        self.fine = QDoubleSpinBox()
        self.fine.setRange(-15, 15)
        self.fine.setSingleStep(0.5)
        self.fine.setSuffix("°")
        r3.addWidget(self.fine)
        self.ink_only = QCheckBox(_("Solo inchiostro"))
        r3.addWidget(self.ink_only)
        self.colors = ColorPicker(self)
        r3.addWidget(self.colors, 1)
        lay.addLayout(r3)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')}")
        lay.addWidget(self.error)
        self._source = None
        self._rot90 = 0
        self._result = None
        self.btn_file.clicked.connect(self.choose_file)
        self.btn_paste.clicked.connect(self.paste)
        self.btn_rot.clicked.connect(self._rotate)
        for w in (self.threshold,):
            w.valueChanged.connect(lambda _v: self.update_result())
        self.remove_bg.toggled.connect(lambda _v: self.update_result())
        self.fine.valueChanged.connect(lambda _v: self.update_result())
        self.ink_only.toggled.connect(lambda _v: self.update_result())
        self.colors.changed.connect(lambda _c: self.update_result())

    def choose_file(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Immagine della firma"), "", _(IMAGE_FILTER))
        if path:
            self.load_path(path)

    def load_path(self, path: str) -> None:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            self._set_error(_("Impossibile leggere il file: {e}").format(e=e.strerror))
            return
        self.load_bytes(data)

    def paste(self) -> None:
        img = QGuiApplication.clipboard().image()
        if img.isNull():
            self._set_error(_("Gli appunti non contengono un'immagine."))
            return
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        self.load_bytes(bytes(ba.data()))

    def load_bytes(self, data: bytes) -> None:
        try:
            img = g.load_image(data)
        except g.GraphicSignatureError as e:
            self._source = None
            self._result = None
            self.preview.image = None
            self.preview.message = str(e)
            self._set_error(str(e))
            self.preview.update()
            self.validityChanged.emit()
            return
        self._source = img
        self._rot90 = 0
        transparent = g.has_transparency(img)
        self.remove_bg.blockSignals(True)
        self.remove_bg.setChecked(not transparent)
        self.remove_bg.blockSignals(False)
        self.threshold.blockSignals(True)
        self.threshold.setValue(g.auto_threshold(img))
        self.threshold.blockSignals(False)
        self.update_result()

    def _rotate(self) -> None:
        self._rot90 = (self._rot90 + 90) % 360
        self.update_result()

    def _set_error(self, msg: str) -> None:
        self.error.setText(msg)

    def update_result(self) -> None:
        if self._source is None:
            return
        self._set_error("")
        try:
            out = g.remove_background(
                self._source, self.threshold.value() if self.remove_bg.isChecked() else None,
                ink_color=self.colors.color if self.ink_only.isChecked() else None,
                rotate=self._rot90 + self.fine.value(),
                keep_alpha=not self.remove_bg.isChecked())
            self._result = out
            self.preview.image = QImage.fromData(g.png_bytes(out))
        except g.GraphicSignatureError as e:
            self._result = None
            self.preview.image = None
            self.preview.message = str(e)
            self._set_error(str(e))
        self.preview.update()
        self.validityChanged.emit()

    def is_valid(self) -> bool:
        return self._result is not None

    def result(self, kind: str) -> g.SignatureItem:
        return g.SignatureItem(kind=kind, origin="immagine", png=g.png_bytes(self._result),
                               color=self.colors.color)


# ======================================================================= dialogo
class CreateSignatureDialog(QDialog):
    """Restituisce in `item` l'elemento creato e in `save_to_library` la scelta."""

    def __init__(self, parent=None, kind: str = "firma", default_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("Crea firma"))
        self.resize(680, 520)
        self.item: g.SignatureItem | None = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(_("Tipo:")))
        self.kind = QComboBox()
        self.kind.addItem(_("Firma"), "firma")
        self.kind.addItem(_("Iniziali"), "iniziali")
        self.kind.setCurrentIndex(1 if kind == "iniziali" else 0)
        top.addWidget(self.kind)
        top.addStretch(1)
        lay.addLayout(top)
        self.tabs = QTabWidget()
        self.draw = DrawTab()
        self.type = TypeTab(default_name)
        self.image = ImageTab()
        self.tabs.addTab(self.draw, _("Disegna"))
        self.tabs.addTab(self.type, _("Digita"))
        self.tabs.addTab(self.image, _("Immagine"))
        lay.addWidget(self.tabs, 1)
        bottom = QHBoxLayout()
        self.save_check = QCheckBox(_("Salva in libreria"))
        self.save_check.setChecked(True)
        bottom.addWidget(self.save_check)
        self.name = QLineEdit()
        self.name.setPlaceholderText(_("Nome (facoltativo)"))
        bottom.addWidget(self.name, 1)
        lay.addLayout(bottom)
        self.buttons = QDialogButtonBox()
        self.use_button = self.buttons.addButton(_("Usa"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.buttons.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        for tab in (self.draw, self.type, self.image):
            tab.validityChanged.connect(self._update_valid)
        self.tabs.currentChanged.connect(lambda _i: self._update_valid())
        self._update_valid()

    def current_tab(self):
        return self.tabs.currentWidget()

    def _update_valid(self) -> None:
        self.use_button.setEnabled(self.current_tab().is_valid())

    def _accept(self) -> None:
        tab = self.current_tab()
        if not tab.is_valid():
            return
        try:
            self.item = tab.result(self.kind.currentData())
        except g.GraphicSignatureError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, _("Crea firma"), str(e))
            return
        self.item.name = self.name.text().strip()
        self.accept()

    @property
    def save_to_library(self) -> bool:
        return self.save_check.isChecked()


TRANSLATIONS_EN = {
    "Nero": "Black", "Blu": "Blue", "Rosso": "Red", "Personalizzato…": "Custom…",
    "Colore:": "Colour:", "Colore della firma": "Signature colour",
    "Area di disegno della firma": "Signature drawing area",
    "Disegna qui la firma": "Draw your signature here",
    "Spessore:": "Width:", "Spessore del tratto": "Stroke width",
    "Annulla ultimo tratto": "Undo last stroke", "Cancella tutto": "Clear all",
    "Nome e cognome o iniziali": "Full name or initials", "Testo della firma": "Signature text",
    "Stili di scrittura": "Handwriting styles", "Inclinazione:": "Slant:",
    "{style} — non compatibile: manca «{chars}»": "{style} — not compatible: missing “{chars}”",
    "Scegli un'immagine della firma": "Choose an image of the signature",
    "Scegli file…": "Choose file…", "Incolla dagli appunti": "Paste from clipboard",
    "Da scanner o fotocamera": "From scanner or camera",
    "Nessuno scanner o fotocamera disponibile: salva l'immagine e scegli il file.":
        "No scanner or camera available: save the image and choose the file.",
    "Soglia sfondo:": "Background threshold:", "Soglia sfondo": "Background threshold",
    "Rimuovi lo sfondo": "Remove background", "Ruota 90°": "Rotate 90°", "Raddrizza:": "Straighten:",
    "Solo inchiostro": "Ink only", "Immagine della firma": "Signature image",
    "Immagini (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp)":
        "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.gif *.webp)",
    "Impossibile leggere il file: {e}": "Cannot read the file: {e}",
    "Gli appunti non contengono un'immagine.": "The clipboard does not contain an image.",
    "Crea firma": "Create signature", "Tipo:": "Type:", "Disegna": "Draw", "Digita": "Type",
    "Immagine": "Image", "Salva in libreria": "Save to library", "Nome (facoltativo)": "Name (optional)",
    "Usa": "Use", "Annulla": "Cancel",
}
