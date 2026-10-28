"""Dialoghi per la geometria delle pagine (PAG-25…PAG-30) con anteprima dal vivo.

Ogni dialogo è costruibile da solo (`CropDialog(parent, document, selected)`)
e offre `options() -> dict`; le funzioni `open_*_dialog(ctx, view, selected)`
lo mostrano, applicano l'operazione come un unico passo annullabile e
restituiscono True se il documento è cambiato (n-up: se il nuovo documento è
stato aperto).
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox, QToolButton, QVBoxLayout,
    QWidget,
)

from foliant.i18n import pgettext
from foliant.api import OperationCancelled
from foliant.api import ENGINE_LOCK, Document, _, icon, theme

from . import layout
from .engine import INCH, MM, PAPER_SIZES, PageOpError, RangeError, parse_ranges

TRANSLATIONS = {
    "Annulla": "Cancel",
    "In alto a sinistra": "Top left",
    "In alto al centro": "Top center",
    "In alto a destra": "Top right",
    "Al centro a sinistra": "Center left",
    "Al centro": "Center",
    "Al centro a destra": "Center right",
    "In basso a sinistra": "Bottom left",
    "In basso al centro": "Bottom center",
    "In basso a destra": "Bottom right",
    "Ritaglia pagine": "Crop pages",
    "Ripristina ritaglio": "Reset crop",
    "Rimuovi margini bianchi": "Remove white margins",
    "Riquadri di pagina": "Page boxes",
    "Formato pagina": "Page size",
    "Dividi pagina": "Split page",
    "Pagine affiancate": "Pages per sheet",
    "Pagine": "Pages",
    "Tutte": "All",
    "Pagina corrente": "Current page",
    "Pagine selezionate": "Selected pages",
    "Intervallo": "Range",
    "Pari": "Even",
    "Dispari": "Odd",
    "es. 1-3, 8, 10-": "e.g. 1-3, 8, 10-",
    "Nessuna pagina selezionata.": "No page selected.",
    "Nessuna pagina corrisponde ai criteri indicati.": "No page matches the given criteria.",
    "Anteprima": "Preview",
    "Anteprima: pagina {n}": "Preview: page {n}",
    "Margini": "Margins",
    "Alto": "Top",
    "Basso": "Bottom",
    "Sinistra": "Left",
    "Destra": "Right",
    "Unità": "Unit",
    "Stessi margini su tutti i lati": "Same margin on all sides",
    "Rispetto al ritaglio attuale": "Relative to the current crop",
    "Margini speculari sulle pagine pari (fronte/retro)": "Mirror margins on even pages (duplex)",
    "Ritaglio rigido (modifica il supporto /MediaBox)": "Hard crop (changes the /MediaBox)",
    "Trascina sull'anteprima per disegnare l'area da conservare.": "Drag on the preview to draw the area to keep.",
    "Dimensione risultante: {w} × {h} mm": "Resulting size: {w} × {h} mm",
    "Area risultante troppo piccola o margini sovrapposti.": "Resulting area too small or margins overlapping.",
    "Applica": "Apply",
    "Impossibile ritagliare": "Cannot crop",
    "Ritaglio": "Crop",
    "Pagine saltate perché troppo piccole: {p}.": "Pages skipped because too small: {p}.",
    "{n} annotazioni sono fuori dall'area di ritaglio: restano nel file ma non sono visibili.":
        "{n} annotations lie outside the crop area: they stay in the file but are not visible.",
    "Il contenuto fuori dall'area di ritaglio resta nel file (può comparire in ricerca e copia). "
    "Per rimuoverlo definitivamente usa l'appiattimento o la sanificazione (modalità Proteggi).":
        "Content outside the crop area stays in the file (it may appear in search and copy). "
        "To remove it permanently use flattening or sanitization (Protect mode).",
    "Margine aggiuntivo": "Extra margin",
    "Tolleranza sul bianco": "White tolerance",
    "Ignora fascia di bordo": "Ignore border band",
    "Stesso ritaglio per tutte le pagine (uniforme)": "Same crop for all pages (uniform)",
    "Considera le annotazioni": "Include annotations",
    "Pagina vuota: nessun ritaglio.": "Blank page: no crop.",
    "Nessun margine trovato: prova ad aumentare la tolleranza.": "No margin found: try increasing the tolerance.",
    "Margini bianchi": "White margins",
    "Pagine ritagliate: {n}.": "Pages cropped: {n}.",
    "Pagine vuote (non ritagliate): {p}.": "Blank pages (not cropped): {p}.",
    "Nessun margine trovato nelle pagine {p}: prova ad aumentare la tolleranza.":
        "No margin found on pages {p}: try increasing the tolerance.",
    "Impossibile rimuovere i margini": "Cannot remove margins",
    "Supporto": "Media",
    "Ritaglio (CropBox)": "Crop",
    "Abbondanza": "Bleed",
    "Formato finito": "Trim",
    "Area grafica": "Art",
    "predefinito": "default",
    "Riquadro": "Box",
    "Valori (mm)": "Values (mm)",
    "Formato finito con abbondanza": "Trim size with bleed",
    "Riquadro con margini dal supporto": "Box with margins from media",
    "Personalizzato": "Custom",
    "Larghezza": "Width",
    "Altezza": "Height",
    "Rimuovi il riquadro": "Remove the box",
    "Impossibile impostare i riquadri": "Cannot set the boxes",
    "Riquadri": "Boxes",
    "In {n} pagine il riquadro è stato limitato al supporto.": "On {n} pages the box was clipped to the media.",
    "Sono presenti sia il formato finito sia l'area grafica: i profili PDF/X ne richiedono uno solo.":
        "Both trim and art boxes are present: PDF/X profiles require only one.",
    "pagina\x04Formato": "Size",
    "Orientamento": "Orientation",
    "Automatico (come la pagina)": "Automatic (as the page)",
    "Verticale": "Portrait",
    "Orizzontale": "Landscape",
    "Modalità": "Mode",
    "Scala il contenuto": "Scale the content",
    "Cambia solo la tela": "Change the canvas only",
    "Mantieni le proporzioni": "Keep proportions",
    "Ancoraggio": "Anchor",
    "Margine": "Margin",
    "Adegua al formato della pagina": "Match the size of page",
    "Impossibile cambiare il formato": "Cannot change the page size",
    "Nessuna pagina modificata: il formato è già quello richiesto.": "No page changed: the size is already the requested one.",
    "Righe": "Rows",
    "Colonne": "Columns",
    "Da destra a sinistra": "Right to left",
    "Sovrapposizione": "Overlap",
    "Tagli verticali (mm)": "Vertical cuts (mm)",
    "Tagli orizzontali (mm)": "Horizontal cuts (mm)",
    "facoltativi, es. 100, 210": "optional, e.g. 100, 210",
    "Valori non validi: usa numeri separati da virgola.": "Invalid values: use comma-separated numbers.",
    "Ogni pagina sarà sostituita da {n} parti.": "Each page will be replaced by {n} parts.",
    "Il contenuto fuori da ciascuna parte resta nel file e può comparire in ricerca e copia.":
        "Content outside each part stays in the file and may appear in search and copy.",
    "Impossibile dividere la pagina": "Cannot split the page",
    "Pagine per foglio": "Pages per sheet",
    "Griglia personalizzata": "Custom grid",
    "Foglio": "Sheet",
    "Ordine": "Order",
    "Per righe": "By rows",
    "Per colonne": "By columns",
    "Spaziatura": "Spacing",
    "Bordo attorno a ogni pagina": "Border around each page",
    "Scala": "Scale",
    "Adatta alla cella": "Fit to cell",
    "Dimensione reale": "Actual size",
    "Rotazione automatica": "Auto-rotate",
    "Ripeti la stessa pagina su tutto il foglio": "Repeat the same page over the whole sheet",
    "Annotazioni": "Annotations",
    "Appiattisci": "Flatten",
    "Scarta": "Discard",
    "Fogli risultanti: {n}": "Resulting sheets: {n}",
    "Griglia oltre 100 celle: le pagine saranno molto piccole.": "Grid over 100 cells: pages will be very small.",
    "Impossibile creare le pagine affiancate": "Cannot create the pages per sheet",
    "affiancate": "n-up",
    "Crea": "Create",
    "Metodo": "Method",
    "Griglia": "Grid",
    "Rileva la piega centrale": "Detect the centre fold",
    "Piega non rilevata: taglio al centro.": "Fold not detected: cut in the middle.",
    "Piega rilevata a {x} mm dal bordo sinistro.": "Fold detected {x} mm from the left edge.",
    "Rimuovi il testo esterno a ogni parte": "Remove the text outside each part",
    "Riempi (taglia le eccedenze)": "Fill (crop the overflow)",
    "Le parti eccedenti del contenuto saranno tagliate.": "The overflowing parts of the content will be cropped.",
    "Il testo scenderà sotto 4 pt effettivi: potrebbe essere poco leggibile.":
        "Text will drop below 4 pt effective size: it may be hard to read.",
    "Contenuto tagliato nelle pagine {p}.": "Content cropped on pages {p}.",
    "Testo sotto 4 pt effettivi (poco leggibile) nelle pagine {p}.":
        "Text below 4 pt effective size (hard to read) on pages {p}.",
    "Trascina sull'anteprima per correggere il rettangolo di questa pagina.":
        "Drag on the preview to adjust this page's rectangle.",
    "Ripristina il rilevamento": "Restore detection",
    "Rettangolo modificato a mano per {n} pagine.": "Rectangle adjusted by hand on {n} pages.",
    "Mostra i riquadri nella vista": "Show the boxes in the view",
}

UNIT_LABELS = [("mm", "mm"), ("pt", "pt"), ("in", "in"), ("%", "%")]
_UNIT_FACTOR = {"mm": MM, "pt": 1.0, "in": INCH}
_ANCHOR_ORDER = ["top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right"]
_ANCHOR_ARROWS = ["↖", "↑", "↗", "←", "•", "→", "↙", "↓", "↘"]


def anchor_label(key: str) -> str:
    """Nome leggibile (tradotto) di un ancoraggio, per suggerimenti e lettori di schermo."""
    return {
        "top-left": _("In alto a sinistra"), "top": _("In alto al centro"), "top-right": _("In alto a destra"),
        "left": _("Al centro a sinistra"), "center": _("Al centro"), "right": _("Al centro a destra"),
        "bottom-left": _("In basso a sinistra"), "bottom": _("In basso al centro"),
        "bottom-right": _("In basso a destra"),
    }.get(key, key)


# =========================================================================
# Elementi comuni
# =========================================================================

def _muted(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
    return lab


def _error_label() -> QLabel:
    lab = QLabel()
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('danger')};")
    lab.hide()
    return lab


def _spin(value=0.0, lo=0.0, hi=10000.0, decimals=1, suffix="", step=1.0) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(value)
    if suffix:
        s.setSuffix(" " + suffix)
    s.setKeyboardTracking(False)
    return s


def _paper_combo(default: str | None = None, custom: bool = True) -> QComboBox:
    from .prefs import user_paper
    default = default or user_paper()
    c = QComboBox()
    for name in PAPER_SIZES:
        c.addItem(name, name)
    if custom:
        c.addItem(_("Personalizzato"), "custom")
    c.setCurrentIndex(max(0, c.findData(default)))
    return c


def _set_combo(combo: QComboBox, data) -> None:
    i = combo.findData(data)
    if i >= 0:
        combo.setCurrentIndex(i)


def _pages_text(indices) -> str:
    return ", ".join(str(i + 1) for i in indices)


def _parse_numbers(text: str) -> list[float] | None:
    text = text.strip()
    if not text:
        return []
    try:
        return [float(t.strip().replace(",", ".")) for t in text.replace(";", " ").split() if t.strip()]
    except ValueError:
        return None


def _render(document: Document, index: int, max_side: int = 420) -> tuple[QImage | None, tuple[float, float]]:
    """Pagina visualizzata (ritaglio e rotazione inclusi) a bassa risoluzione."""
    try:
        with ENGINE_LOCK:
            page = document.pdf[index]
            r = page.rect
            z = max_side / max(r.width, r.height, 1.0)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False, annots=True)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            return img, (r.width, r.height)
    except Exception:  # noqa: BLE001 - anteprima facoltativa
        return None, (595.0, 842.0)


def _alpha(name: str, a: int) -> QColor:
    c = QColor(theme.color(name))
    c.setAlpha(a)
    return c


class PageScope(QGroupBox):
    """Scelta dell'ambito: tutte, corrente, selezionate, intervallo (e pari/dispari)."""

    changed = Signal()

    def __init__(self, page_count: int, selected: list[int], current: int = 0, even_odd: bool = False,
                 parent: QWidget | None = None):
        super().__init__(_("Pagine"), parent)
        self.page_count = page_count
        self.selected = sorted({i for i in selected if 0 <= i < page_count})
        self.current = min(max(0, current), max(0, page_count - 1))
        lay = QGridLayout(self)
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QRadioButton] = {}
        entries = [("all", _("Tutte")), ("current", _("Pagina corrente")), ("selected", _("Pagine selezionate"))]
        if even_odd:
            entries += [("odd", _("Dispari")), ("even", _("Pari"))]
        for n, (key, text) in enumerate(entries):
            b = QRadioButton(text)
            self.buttons[key] = b
            self.group.addButton(b)
            lay.addWidget(b, n // 2, n % 2)
            b.toggled.connect(self._toggled)
        row = (len(entries) + 1) // 2
        self.buttons["range"] = QRadioButton(_("Intervallo"))
        self.group.addButton(self.buttons["range"])
        self.buttons["range"].toggled.connect(self._toggled)
        self.range_edit = QLineEdit()
        self.range_edit.setPlaceholderText(_("es. 1-3, 8, 10-"))
        self.range_edit.textChanged.connect(self._range_edited)
        lay.addWidget(self.buttons["range"], row, 0)
        lay.addWidget(self.range_edit, row, 1)
        self.buttons["selected"].setEnabled(bool(self.selected))
        if len(self.selected) > 1:
            self.buttons["selected"].setChecked(True)
        else:
            self.buttons["all"].setChecked(True)
        self.range_edit.setEnabled(self.buttons["range"].isChecked())

    def _toggled(self, on: bool) -> None:
        if on:
            self.range_edit.setEnabled(self.buttons["range"].isChecked())
            self.changed.emit()

    def _range_edited(self) -> None:
        if not self.buttons["range"].isChecked():
            self.buttons["range"].setChecked(True)
        else:
            self.changed.emit()

    def scope(self) -> str:
        for k, b in self.buttons.items():
            if b.isChecked():
                return k
        return "all"

    def set_scope(self, key: str, text: str | None = None) -> None:
        if text is not None:
            self.range_edit.blockSignals(True)
            self.range_edit.setText(text)
            self.range_edit.blockSignals(False)
        self.buttons[key].setChecked(True)
        self.changed.emit()

    def pages(self) -> list[int]:
        """Indici dell'ambito; solleva RangeError se non validi."""
        key = self.scope()
        n = self.page_count
        if key == "current":
            return [self.current]
        if key == "selected":
            if not self.selected:
                raise RangeError(_("Nessuna pagina selezionata."))
            return list(self.selected)
        if key == "range":
            return parse_ranges(self.range_edit.text(), n)
        out = list(range(n))
        if key == "odd":
            out = [i for i in out if (i + 1) % 2 == 1]
        elif key == "even":
            out = [i for i in out if (i + 1) % 2 == 0]
        if not out:
            raise RangeError(_("Nessuna pagina corrisponde ai criteri indicati."))
        return out

    def error(self) -> str | None:
        try:
            self.pages()
        except RangeError as e:
            return str(e)
        return None


class Preview(QWidget):
    """Pagina (o foglio) disegnata in punti, con sovrapposizioni dipinte dal dialogo."""

    rectDrawn = Signal(QRectF)

    def __init__(self, paint_fn=None, interactive: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.paint_fn = paint_fn
        self.interactive = interactive
        self.image: QImage | None = None
        self.size_pts = (595.0, 842.0)
        self._drag: QPointF | None = None
        self._drag_rect: QRectF | None = None
        self.setMinimumSize(280, 340)
        if interactive:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def set_page(self, image: QImage | None, size_pts: tuple[float, float]) -> None:
        self.image = image
        self.size_pts = (max(1.0, size_pts[0]), max(1.0, size_pts[1]))
        self.update()

    def _geometry(self) -> tuple[float, float, float]:
        pad = 16
        w, h = self.size_pts
        s = min((self.width() - 2 * pad) / w, (self.height() - 2 * pad) / h)
        s = max(s, 0.01)
        return s, (self.width() - w * s) / 2, (self.height() - h * s) / 2

    def to_page(self, pos: QPointF) -> QPointF:
        s, ox, oy = self._geometry()
        x = min(max((pos.x() - ox) / s, 0.0), self.size_pts[0])
        y = min(max((pos.y() - oy) / s, 0.0), self.size_pts[1])
        return QPointF(x, y)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), theme.color("canvas"))
        s, ox, oy = self._geometry()
        w, h = self.size_pts
        page = QRectF(ox, oy, w * s, h * s)
        p.fillRect(page.translated(2, 3), theme.color("page_shadow"))
        if self.image is not None:
            p.drawImage(page, self.image)
        else:
            p.fillRect(page, theme.color("surface"))
        p.setPen(QPen(theme.color("border"), 1))
        p.drawRect(page)
        p.save()
        p.translate(ox, oy)
        p.scale(s, s)
        if self.paint_fn is not None:
            try:
                self.paint_fn(p, s)
            except Exception:  # noqa: BLE001 - l'anteprima non deve mai bloccare il dialogo
                pass
        if self._drag_rect is not None:
            pen = QPen(theme.color("secondary"), 1.5 / s, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(self._drag_rect)
        p.restore()
        p.end()

    def mousePressEvent(self, ev) -> None:
        if self.interactive and ev.button() == Qt.MouseButton.LeftButton:
            self._drag = self.to_page(ev.position())
            self._drag_rect = QRectF(self._drag, self._drag)
            self.update()

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is not None:
            self._drag_rect = QRectF(self._drag, self.to_page(ev.position())).normalized()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is not None:
            r = QRectF(self._drag, self.to_page(ev.position())).normalized()
            self._drag = None
            self._drag_rect = None
            self.update()
            if r.width() >= 3 and r.height() >= 3:
                self.rectDrawn.emit(r)


class _LayoutDialog(QDialog):
    """Struttura comune: opzioni a sinistra, anteprima a destra, ambito, errori, OK."""

    TITLE = ""
    ICON = "crop"
    OK_TEXT = "Applica"
    EVEN_ODD = False
    INTERACTIVE = False

    def __init__(self, parent, document: Document, selected: list[int] | None = None, current: int = 0):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(_(self.TITLE))
        self.setWindowIcon(icon(self.ICON))
        with ENGINE_LOCK:
            self.page_count = document.pdf.page_count
        self.current = min(max(0, current), max(0, self.page_count - 1))
        self._preview_index = -1
        root = QVBoxLayout(self)
        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = QVBoxLayout()
        body.addLayout(left, 0)
        self.form_box = QWidget()
        self.form = QVBoxLayout(self.form_box)
        self.form.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.form_box)
        self.scope = PageScope(self.page_count, list(selected or []), self.current, self.EVEN_ODD)
        left.addWidget(self.scope)
        left.addStretch(1)
        right = QVBoxLayout()
        body.addLayout(right, 1)
        self.preview_title = _muted()
        right.addWidget(self.preview_title)
        self.preview = Preview(self._paint, interactive=self.INTERACTIVE)
        right.addWidget(self.preview, 1)
        self.info = _muted()
        right.addWidget(self.info)
        self.error_label = _error_label()
        root.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.ok.setText(_(self.OK_TEXT))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.refresh)
        self.build()
        self.scope.changed.connect(self.schedule)
        self.refresh()

    # sottoclassi -------------------------------------------------------
    def build(self) -> None:
        raise NotImplementedError

    def validate(self) -> str | None:
        return None

    def update_preview_data(self) -> None:
        pass

    def paint_overlay(self, p: QPainter, s: float) -> None:
        pass

    # comune --------------------------------------------------------------
    def _connect(self, *widgets) -> None:
        for w in widgets:
            for sig in ("valueChanged", "currentIndexChanged", "toggled", "textChanged"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(self.schedule)
                    break

    def schedule(self, *_a) -> None:
        self._timer.start()

    def preview_index(self) -> int:
        try:
            pages = self.scope.pages()
        except RangeError:
            return self.current
        return self.current if self.current in pages else pages[0]

    def selected_pages(self) -> list[int]:
        try:
            return self.scope.pages()
        except RangeError:
            return []

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)

    def refresh(self) -> None:
        if getattr(self.document, "closed", False):
            self._timer.stop()
            return
        err = self.scope.error()
        if err is None:
            try:
                err = self.validate()
            except PageOpError as e:
                err = str(e)
            except Exception as e:  # noqa: BLE001 - mai un'eccezione dal ciclo degli eventi
                err = str(e) or type(e).__name__
        self.error_label.setText(err or "")
        self.error_label.setVisible(bool(err))
        self.ok.setEnabled(err is None)
        idx = self.preview_index()
        if idx != self._preview_index:
            self._preview_index = idx
            img, size = _render(self.document, idx)
            self.preview.set_page(img, size)
            self.preview_title.setText(_("Anteprima: pagina {n}").format(n=idx + 1))
        try:
            self.update_preview_data()
        except Exception:  # noqa: BLE001
            pass
        self.preview.update()

    def _paint(self, p: QPainter, s: float) -> None:
        self.paint_overlay(p, s)


def _dim_outside(p: QPainter, page: QRectF, keep: QRectF) -> None:
    shade = _alpha("page_shadow", 120)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)    # niente giunture chiare tra le fasce
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shade)
    p.drawRect(QRectF(page.left(), page.top(), page.width(), max(0.0, keep.top() - page.top())))
    p.drawRect(QRectF(page.left(), keep.bottom(), page.width(), max(0.0, page.bottom() - keep.bottom())))
    p.drawRect(QRectF(page.left(), keep.top(), max(0.0, keep.left() - page.left()), keep.height()))
    p.drawRect(QRectF(keep.right(), keep.top(), max(0.0, page.right() - keep.right()), keep.height()))


def _norm_rect(r: pymupdf.Rect) -> pymupdf.Rect:
    return pymupdf.Rect(min(r.x0, r.x1), min(r.y0, r.y1), max(r.x0, r.x1), max(r.y0, r.y1))


def _qrect(r: pymupdf.Rect) -> QRectF:
    return QRectF(r.x0, r.y0, r.width, r.height)


def _guard(ctx, title: str, fn) -> bool:
    try:
        return bool(fn())
    except OperationCancelled:
        return False   # annullata dall'utente (es. avviso di Firma)
    except PageOpError as e:
        ctx.error(title, str(e))
    except Exception as e:  # noqa: BLE001 - un errore del modulo non chiude l'applicazione
        ctx.error(title, str(e) or type(e).__name__)
    return False


def _parent(ctx, view):
    w = getattr(ctx, "window", None)
    return w if w is not None else view


# =========================================================================
# PAG-25 — Ritaglia pagine
# =========================================================================

class CropDialog(_LayoutDialog):
    TITLE = "Ritaglia pagine"
    ICON = "crop"
    EVEN_ODD = True
    INTERACTIVE = True

    def build(self) -> None:
        self.action = "crop"
        box = QGroupBox(_("Margini"))
        grid = QFormLayout(box)
        self.unit = QComboBox()
        for key, text in UNIT_LABELS:
            self.unit.addItem(text, key)
        self.top, self.bottom, self.left, self.right = (_spin(0, 0, 100000, 2) for _i in range(4))
        grid.addRow(_("Unità"), self.unit)
        grid.addRow(_("Alto"), self.top)
        grid.addRow(_("Basso"), self.bottom)
        grid.addRow(_("Sinistra"), self.left)
        grid.addRow(_("Destra"), self.right)
        self.same = QCheckBox(_("Stessi margini su tutti i lati"))
        grid.addRow(self.same)
        self.form.addWidget(box)
        self.relative = QCheckBox(_("Rispetto al ritaglio attuale"))
        self.relative.setChecked(True)
        self.mirror = QCheckBox(_("Margini speculari sulle pagine pari (fronte/retro)"))
        self.hard = QCheckBox(_("Ritaglio rigido (modifica il supporto /MediaBox)"))
        for w in (self.relative, self.mirror, self.hard):
            self.form.addWidget(w)
        self.form.addWidget(_muted(_("Trascina sull'anteprima per disegnare l'area da conservare.")))
        self.reset_btn = QPushButton(icon("undo-2"), _("Ripristina ritaglio"))
        self.reset_btn.clicked.connect(self._reset)
        self.form.addWidget(self.reset_btn)
        self.top.valueChanged.connect(self._sync_same)
        self.same.toggled.connect(self._sync_same)
        self.unit.currentIndexChanged.connect(self._unit_changed)
        self._last_unit = "mm"
        self._connect(self.unit, self.top, self.bottom, self.left, self.right, self.relative, self.mirror, self.hard)
        self.preview.rectDrawn.connect(self._rect_drawn)
        self._proposed: pymupdf.Rect | None = None

    def _sync_same(self, *_a) -> None:
        same = self.same.isChecked()
        for w in (self.bottom, self.left, self.right):
            w.setEnabled(not same)
            if same:
                w.setValue(self.top.value())

    def _unit_changed(self) -> None:
        new = self.unit.currentData()
        old = self._last_unit
        self._last_unit = new
        if old == new:
            return
        try:
            with ENGINE_LOCK:
                w, h = layout.displayed_crop_size(self.document.pdf, self.preview_index())
        except Exception:  # noqa: BLE001
            w, h = 595.0, 842.0
        for spin, extent in ((self.top, h), (self.bottom, h), (self.left, w), (self.right, w)):
            pts = spin.value() * extent / 100 if old == "%" else spin.value() * _UNIT_FACTOR[old]
            spin.blockSignals(True)
            spin.setValue(pts * 100 / extent if new == "%" else pts / _UNIT_FACTOR[new])
            spin.blockSignals(False)
        self.schedule()

    def _rect_drawn(self, r: QRectF) -> None:
        """Rettangolo disegnato sull'anteprima (punti visualizzati) → margini."""
        self.same.setChecked(False)
        self.relative.setChecked(True)
        w, h = self.preview.size_pts
        unit = self.unit.currentData()
        vals = (r.top(), h - r.bottom(), r.left(), w - r.right())
        exts = (h, h, w, w)
        for spin, v, e in zip((self.top, self.bottom, self.left, self.right), vals, exts):
            spin.blockSignals(True)
            spin.setValue(max(0.0, v * 100 / e if unit == "%" else v / _UNIT_FACTOR[unit]))
            spin.blockSignals(False)
        self.schedule()

    def _reset(self) -> None:
        if self.scope.error() is None:
            self.action = "reset"
            self.accept()

    def options(self) -> dict:
        return {
            "action": self.action,
            "pages": self.selected_pages(),
            "margins": (self.top.value(), self.bottom.value(), self.left.value(), self.right.value()),
            "unit": self.unit.currentData(),
            "relative_to_current": self.relative.isChecked(),
            "mirror_even": self.mirror.isChecked(),
            "hard": self.hard.isChecked(),
        }

    def validate(self) -> str | None:
        o = self.options()
        idx = self.preview_index()
        with ENGINE_LOCK:
            pdf = self.document.pdf
            new = layout.crop_margins_rect(pdf, idx, o["margins"], o["unit"], o["relative_to_current"],
                                           o["mirror_even"])
            if new is None:
                ok = any(layout.crop_margins_rect(pdf, i, o["margins"], o["unit"], o["relative_to_current"],
                                                  o["mirror_even"]) is not None for i in o["pages"][:200])
                self._proposed = None
                self.info.setText("")
                return None if ok else _("Area risultante troppo piccola o margini sovrapposti.")
            page = pdf[idx]
            crop = layout._crop(pdf, page)
            self._proposed = layout.user_to_disp(crop, layout._rot(page), new)
        w, h = self._proposed.width, self._proposed.height
        self.info.setText(_("Dimensione risultante: {w} × {h} mm").format(
            w=f"{w / MM:.1f}".replace(".", ","), h=f"{h / MM:.1f}".replace(".", ",")))
        return None

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if self._proposed is None:
            return
        w, h = self.preview.size_pts
        keep = _qrect(self._proposed)
        _dim_outside(p, QRectF(0, 0, w, h), keep)
        p.setPen(QPen(theme.color("accent"), 2 / s))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(keep)


def open_crop_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-25: ritaglio con margini o rettangolo disegnato; ripristino del ritaglio."""
    if view is None:
        return False
    dlg = CropDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        if o["action"] == "reset":
            with view.document.change(_("Ripristina ritaglio")) as pdf:
                layout.reset_crop(pdf, o["pages"])
            return True
        with view.document.change(_("Ritaglia pagine")) as pdf:
            done = layout.crop_pages(pdf, o["pages"], o["margins"], unit=o["unit"],
                                     relative_to_current=o["relative_to_current"], mirror_even=o["mirror_even"],
                                     hard=o["hard"])
            hidden = (not o["hard"]) and layout.hidden_content_outside_crop(pdf, done)
            outside = 0 if o["hard"] else layout.annots_outside_crop(pdf, done)
        notes = []
        skipped = sorted(set(o["pages"]) - set(done))
        if skipped:
            notes.append(_("Pagine saltate perché troppo piccole: {p}.").format(p=_pages_text(skipped)))
        if outside:
            notes.append(_("{n} annotazioni sono fuori dall'area di ritaglio: restano nel file ma non sono visibili.")
                         .format(n=outside))
        if hidden or o["hard"]:
            notes.append(_("Il contenuto fuori dall'area di ritaglio resta nel file (può comparire in ricerca e copia). "
                           "Per rimuoverlo definitivamente usa l'appiattimento o la sanificazione (modalità Proteggi)."))
        if notes:
            ctx.info(_("Ritaglio"), "\n\n".join(notes))
        return True

    return _guard(ctx, _("Impossibile ritagliare"), run)


# =========================================================================
# PAG-26 — Rimuovi margini bianchi
# =========================================================================

class RemoveMarginsDialog(_LayoutDialog):
    TITLE = "Rimuovi margini bianchi"
    ICON = "scan"
    INTERACTIVE = True

    def build(self) -> None:
        box = QGroupBox(_("Margini bianchi"))
        f = QFormLayout(box)
        self.extra = _spin(0, 0, 200, 1, "mm")
        self.tolerance = _spin(10, 0, 90, 0, "%")
        self.border = _spin(0, 0, 45, 0, "%")
        f.addRow(_("Margine aggiuntivo"), self.extra)
        f.addRow(_("Tolleranza sul bianco"), self.tolerance)
        f.addRow(_("Ignora fascia di bordo"), self.border)
        self.uniform = QCheckBox(_("Stesso ritaglio per tutte le pagine (uniforme)"))
        self.annots = QCheckBox(_("Considera le annotazioni"))
        f.addRow(self.uniform)
        f.addRow(self.annots)
        self.form.addWidget(box)
        self.form.addWidget(_muted(_("Trascina sull'anteprima per correggere il rettangolo di questa pagina.")))
        self.restore_btn = QPushButton(icon("rotate-ccw"), _("Ripristina il rilevamento"))
        self.restore_btn.clicked.connect(self._restore_detection)
        self.form.addWidget(self.restore_btn)
        self.custom_label = _muted()
        self.form.addWidget(self.custom_label)
        self._bbox: pymupdf.Rect | None = None
        self._key = None
        self.custom: dict[int, pymupdf.Rect] = {}     # coordinate del motore, per pagina
        self.preview.rectDrawn.connect(self._rect_drawn)
        self._connect(self.extra, self.tolerance, self.border, self.uniform, self.annots)

    def set_custom_rect(self, index: int, rect_displayed) -> None:
        """Rettangolo del contenuto scelto a mano (coordinate visualizzate) per una pagina."""
        with ENGINE_LOCK:
            page = self.document.pdf[index]
            r = pymupdf.Rect(rect_displayed) & page.rect
            self.custom[index] = _norm_rect(r * page.derotation_matrix)
        self._key = None
        self.schedule()

    def _rect_drawn(self, r: QRectF) -> None:
        self.set_custom_rect(self.preview_index(), (r.left(), r.top(), r.right(), r.bottom()))

    def _restore_detection(self) -> None:
        self.custom.pop(self.preview_index(), None)
        self._key = None
        self.schedule()

    def options(self) -> dict:
        return {
            "pages": self.selected_pages(),
            "extra": self.extra.value() * MM,
            "tolerance": self.tolerance.value() / 100,
            "ignore_border": self.border.value() / 100,
            "uniform": self.uniform.isChecked(),
            "include_annots": self.annots.isChecked(),
            "rects": {i: pymupdf.Rect(r) for i, r in self.custom.items()} or None,
        }

    def update_preview_data(self) -> None:
        o = self.options()
        idx = self.preview_index()
        key = (idx, o["tolerance"], o["ignore_border"], o["include_annots"])
        self.restore_btn.setEnabled(idx in self.custom)
        self.custom_label.setText(_("Rettangolo modificato a mano per {n} pagine.").format(n=len(self.custom))
                                  if self.custom else "")
        if key != self._key:
            self._key = key
            with ENGINE_LOCK:
                page = self.document.pdf[idx]
                if idx in self.custom:
                    bb = self.custom[idx]
                else:
                    bb = layout.content_bbox(page, o["tolerance"], o["ignore_border"], o["include_annots"])
                self._bbox = None if bb is None else _norm_rect(bb * page.rotation_matrix)
                self._full = page.rect
        if self._bbox is None:
            self.info.setText(_("Pagina vuota: nessun ritaglio."))
        elif (self._bbox.x0 <= 1 and self._bbox.y0 <= 1 and self._bbox.x1 >= self._full.x1 - 1
              and self._bbox.y1 >= self._full.y1 - 1):
            self.info.setText(_("Nessun margine trovato: prova ad aumentare la tolleranza."))
        else:
            r = self._proposal()
            self.info.setText(_("Dimensione risultante: {w} × {h} mm").format(
                w=f"{r.width / MM:.1f}".replace(".", ","), h=f"{r.height / MM:.1f}".replace(".", ",")))

    def _proposal(self) -> pymupdf.Rect:
        e = self.extra.value() * MM
        return (pymupdf.Rect(self._bbox) + (-e, -e, e, e)) & self._full

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if self._bbox is None:
            return
        w, h = self.preview.size_pts
        keep = _qrect(self._proposal())
        _dim_outside(p, QRectF(0, 0, w, h), keep)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.color("secondary"), 1 / s, Qt.PenStyle.DashLine))
        p.drawRect(_qrect(pymupdf.Rect(self._bbox)))
        p.setPen(QPen(theme.color("accent"), 2 / s))
        p.drawRect(keep)


def open_remove_margins_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-26: ritaglio automatico attorno al contenuto visibile."""
    if view is None:
        return False
    dlg = RemoveMarginsDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        with view.document.change(_("Rimuovi margini bianchi")) as pdf:
            rep = layout.remove_white_margins(pdf, o["pages"], extra=o["extra"], tolerance=o["tolerance"],
                                              uniform=o["uniform"], ignore_border=o["ignore_border"],
                                              include_annots=o["include_annots"], rects=o["rects"])
            if not rep["cropped"]:
                raise _NothingDone(rep)
        notes = [_("Pagine ritagliate: {n}.").format(n=len(rep["cropped"]))]
        if rep["blank"]:
            notes.append(_("Pagine vuote (non ritagliate): {p}.").format(p=_pages_text(rep["blank"])))
        if rep["full"]:
            notes.append(_("Nessun margine trovato nelle pagine {p}: prova ad aumentare la tolleranza.")
                         .format(p=_pages_text(rep["full"])))
        if len(notes) > 1:
            ctx.info(_("Margini bianchi"), "\n\n".join(notes))
        return True

    try:
        return _guard(ctx, _("Impossibile rimuovere i margini"), run)
    except _NothingDone as e:   # nessuna modifica: il passo è già stato annullato da change()
        rep = e.report
        notes = []
        if rep["blank"]:
            notes.append(_("Pagine vuote (non ritagliate): {p}.").format(p=_pages_text(rep["blank"])))
        if rep["full"]:
            notes.append(_("Nessun margine trovato nelle pagine {p}: prova ad aumentare la tolleranza.")
                         .format(p=_pages_text(rep["full"])))
        ctx.info(_("Margini bianchi"), "\n\n".join(notes) or _("Nessuna pagina corrisponde ai criteri indicati."))
        return False


class _NothingDone(BaseException):
    def __init__(self, report):
        super().__init__()
        self.report = report


# =========================================================================
# PAG-27 — Riquadri di pagina
# =========================================================================

BOX_LABELS = [
    ("MediaBox", "Supporto", "text_muted"),
    ("CropBox", "Ritaglio (CropBox)", "accent"),
    ("BleedBox", "Abbondanza", "danger"),
    ("TrimBox", "Formato finito", "success"),
    ("ArtBox", "Area grafica", "secondary"),
]


def _boxes_overlay(view, painter: QPainter, index: int) -> None:
    """Disegna i riquadri di stampa presenti nel file (coordinate del motore)."""
    with ENGINE_LOCK:
        pdf = view.document.pdf
        page = pdf[index]
        boxes = layout.get_boxes(page)
        crop = layout._crop(pdf, page)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for name, _text, col in BOX_LABELS:
        b = boxes[name]
        if name == "CropBox" or (b["default"] and name != "MediaBox"):
            continue
        if name == "MediaBox" and b["rect"] == crop:
            continue
        pen = QPen(theme.color(col), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(_qrect(layout._user_to_engine(crop, b["rect"])))


_boxes_overlay.foliant_boxes = True


def boxes_overlay_installed(view) -> bool:
    return any(getattr(fn, "foliant_boxes", False) for fn in getattr(view, "overlays", []))


def _repaint(view) -> None:
    try:
        vp = view.viewport() if hasattr(view, "viewport") else view
        vp.update()
    except Exception:  # noqa: BLE001
        pass


def install_boxes_overlay(view) -> None:
    """Mostra nella vista i riquadri di stampa (abbondanza, rifilo, area grafica, supporto)."""
    if view is not None and not boxes_overlay_installed(view):
        view.overlays.append(_boxes_overlay)
        _repaint(view)


def remove_boxes_overlay(view) -> None:
    if view is None:
        return
    view.overlays[:] = [fn for fn in view.overlays if not getattr(fn, "foliant_boxes", False)]
    _repaint(view)


class BoxesDialog(_LayoutDialog):
    TITLE = "Riquadri di pagina"
    ICON = "frame"

    def __init__(self, parent, document: Document, selected: list[int] | None = None, current: int = 0,
                 view=None):
        self.view = view
        super().__init__(parent, document, selected, current)

    def build(self) -> None:
        cur = QGroupBox(_("Riquadri"))
        g = QGridLayout(cur)
        g.addWidget(_muted(_("Riquadro")), 0, 0)
        g.addWidget(_muted(_("Valori (mm)")), 0, 1)
        self.value_labels: dict[str, QLabel] = {}
        for n, (name, text, col) in enumerate(BOX_LABELS, start=1):
            lab = QLabel(f"<span style='color:{theme.hex_color(col)}'>■</span> {_(text)}")
            g.addWidget(lab, n, 0)
            v = QLabel()
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            g.addWidget(v, n, 1)
            self.value_labels[name] = v
        self.form.addWidget(cur)

        self.mode_trim = QRadioButton(_("Formato finito con abbondanza"))
        self.mode_box = QRadioButton(_("Riquadro con margini dal supporto"))
        self.mode_trim.setChecked(True)
        mg = QButtonGroup(self)
        mg.addButton(self.mode_trim)
        mg.addButton(self.mode_box)
        self.form.addWidget(self.mode_trim)
        trim = QWidget()
        tf = QGridLayout(trim)
        tf.setContentsMargins(18, 0, 0, 0)
        self.paper = _paper_combo()
        self.trim_w = _spin(210, 3, 5000, 1, "mm")
        self.trim_h = _spin(297, 3, 5000, 1, "mm")
        self.bleed = _spin(3, 0, 100, 1, "mm")
        tf.addWidget(QLabel(pgettext("pagina", "Formato")), 0, 0)
        tf.addWidget(self.paper, 0, 1)
        tf.addWidget(QLabel(_("Abbondanza")), 0, 2)
        tf.addWidget(self.bleed, 0, 3)
        tf.addWidget(QLabel(_("Larghezza")), 1, 0)
        tf.addWidget(self.trim_w, 1, 1)
        tf.addWidget(QLabel(_("Altezza")), 1, 2)
        tf.addWidget(self.trim_h, 1, 3)
        self.form.addWidget(trim)
        self.form.addWidget(self.mode_box)
        boxw = QWidget()
        bf = QGridLayout(boxw)
        bf.setContentsMargins(18, 0, 0, 0)
        self.box_name = QComboBox()
        for name, text, _c in BOX_LABELS[1:]:
            self.box_name.addItem(_(text), name)
        _set_combo(self.box_name, "ArtBox")
        self.m_top, self.m_bottom, self.m_left, self.m_right = (_spin(0, 0, 2000, 1, "mm") for _i in range(4))
        self.remove = QCheckBox(_("Rimuovi il riquadro"))
        bf.addWidget(QLabel(_("Riquadro")), 0, 0)
        bf.addWidget(self.box_name, 0, 1)
        bf.addWidget(self.remove, 0, 2, 1, 2)
        for n, (text, w) in enumerate(((_("Alto"), self.m_top), (_("Basso"), self.m_bottom),
                                       (_("Sinistra"), self.m_left), (_("Destra"), self.m_right))):
            bf.addWidget(QLabel(text), 1 + n // 2, (n % 2) * 2)
            bf.addWidget(w, 1 + n // 2, (n % 2) * 2 + 1)
        self.form.addWidget(boxw)
        self.trim_panel, self.box_panel = trim, boxw
        self.show_in_view = QCheckBox(_("Mostra i riquadri nella vista"))
        self.show_in_view.setEnabled(self.view is not None)
        self.show_in_view.setChecked(self.view is not None and boxes_overlay_installed(self.view))
        self.show_in_view.toggled.connect(self._toggle_overlay)
        self.form.addWidget(self.show_in_view)
        self.paper.currentIndexChanged.connect(self._paper_changed)
        self._connect(self.mode_trim, self.mode_box, self.paper, self.trim_w, self.trim_h, self.bleed, self.box_name,
                      self.m_top, self.m_bottom, self.m_left, self.m_right, self.remove)
        self._boxes: dict = {}
        self._paper_changed()

    def _toggle_overlay(self, on: bool) -> None:
        if on:
            install_boxes_overlay(self.view)
        else:
            remove_boxes_overlay(self.view)

    def _paper_changed(self) -> None:
        key = self.paper.currentData()
        custom = key == "custom"
        self.trim_w.setEnabled(custom)
        self.trim_h.setEnabled(custom)
        if not custom:
            w, h = PAPER_SIZES[key]
            self.trim_w.setValue(round(w / MM, 1))
            self.trim_h.setValue(round(h / MM, 1))

    def options(self) -> dict:
        return {
            "pages": self.selected_pages(),
            "action": "trim_bleed" if self.mode_trim.isChecked() else "set_box",
            "trim_size": (self.trim_w.value() * MM, self.trim_h.value() * MM),
            "bleed": self.bleed.value() * MM,
            "box": self.box_name.currentData(),
            "margins": tuple(v.value() * MM for v in (self.m_top, self.m_bottom, self.m_left, self.m_right)),
            "remove": self.remove.isChecked(),
        }

    def validate(self) -> str | None:
        o = self.options()
        self.trim_panel.setEnabled(o["action"] == "trim_bleed")
        self.box_panel.setEnabled(o["action"] == "set_box")
        idx = self.preview_index()
        with ENGINE_LOCK:
            pdf = self.document.pdf
            page = pdf[idx]
            self._boxes = layout.get_boxes(page)
            self._crop = layout._crop(pdf, page)
            self._rot = layout._rot(page)
            self._proposed = self._compute_proposal(pdf, idx, o)
        for name, _t, _c in BOX_LABELS:
            b = self._boxes[name]
            r = b["rect"]
            txt = (f"{r.x0 / MM:.1f}  {r.y0 / MM:.1f}  {r.x1 / MM:.1f}  {r.y1 / MM:.1f}  "
                   f"({r.width / MM:.1f} × {r.height / MM:.1f})").replace(".", ",")
            if b["default"]:
                txt += "  — " + _("predefinito")
            self.value_labels[name].setText(txt)
        if not self._boxes["TrimBox"]["default"] and not self._boxes["ArtBox"]["default"]:
            self.info.setText(_("Sono presenti sia il formato finito sia l'area grafica: i profili PDF/X ne "
                                "richiedono uno solo."))
        else:
            self.info.setText("")
        return self._error

    def _compute_proposal(self, pdf, idx, o) -> dict:
        self._error = None
        media = self._boxes["MediaBox"]["rect"]
        if o["action"] == "trim_bleed":
            w, h = o["trim_size"] if self._rot in (0, 180) else o["trim_size"][::-1]
            if w > media.width + 0.5 or h > media.height + 0.5:
                self._error = _("Il formato finito è più grande del supporto della pagina {n}.").format(n=idx + 1)
                return {}
            cx, cy = (media.x0 + media.x1) / 2, (media.y0 + media.y1) / 2
            trim = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2) & media
            b = o["bleed"]
            return {"TrimBox": trim, "BleedBox": (trim + (-b, -b, b, b)) & media}
        if o["remove"]:
            return {}
        rect = self._margins_rect(media, o["margins"])
        if rect is None:
            self._error = _("Il riquadro è troppo piccolo (minimo 3×3 pt).")
            return {}
        return {o["box"]: rect}

    def _margins_rect(self, media: pymupdf.Rect, margins) -> pymupdf.Rect | None:
        t, b, l, r = margins
        disp = layout.user_to_disp(media, self._rot, media)
        d = pymupdf.Rect(l, t, disp.width - r, disp.height - b)
        if d.width < 3 or d.height < 3:
            return None
        return layout.disp_to_user(media, self._rot, d)

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if not self._boxes:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        for name, _t, col in BOX_LABELS:
            r = self._proposed.get(name, self._boxes[name]["rect"])
            style = Qt.PenStyle.SolidLine if name in self._proposed else Qt.PenStyle.DashLine
            p.setPen(QPen(theme.color(col), (2.2 if name in self._proposed else 1.2) / s, style))
            p.drawRect(_qrect(layout.user_to_disp(self._crop, self._rot, r)))


def open_boxes_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-27: riquadri di stampa (abbondanza, rifilo, area grafica)."""
    if view is None:
        return False
    dlg = BoxesDialog(_parent(ctx, view), view.document, selected, view.current_page, view=view)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        with view.document.change(_("Riquadri di pagina")) as pdf:
            if o["action"] == "trim_bleed":
                clipped = layout.set_trim_with_bleed(pdf, o["pages"], o["trim_size"], o["bleed"])
            elif o["remove"]:
                clipped = layout.set_box(pdf, o["pages"], o["box"], None)
            else:
                clipped = []
                for i in o["pages"]:
                    page = pdf[i]
                    media = layout._media(pdf, page)
                    rot = layout._rot(page)
                    t, b, l, r = o["margins"]
                    dw, dh = layout._disp_size(media, rot)
                    rect = layout.disp_to_user(media, rot, pymupdf.Rect(l, t, dw - r, dh - b))
                    clipped += layout.set_box(pdf, [i], o["box"], rect)
        if clipped:
            ctx.info(_("Riquadri"), _("In {n} pagine il riquadro è stato limitato al supporto.").format(n=len(clipped)))
        return True

    return _guard(ctx, _("Impossibile impostare i riquadri"), run)


# =========================================================================
# PAG-28 — Formato pagina
# =========================================================================

class ResizeDialog(_LayoutDialog):
    TITLE = "Formato pagina"
    ICON = "scaling"

    def build(self) -> None:
        box = QGroupBox(pgettext("pagina", "Formato"))
        f = QFormLayout(box)
        self.paper = _paper_combo()
        self.width_mm = _spin(210, 3, 5080, 1, "mm")
        self.height_mm = _spin(297, 3, 5080, 1, "mm")
        self.orientation = QComboBox()
        for key, text in (("auto", _("Automatico (come la pagina)")), ("portrait", _("Verticale")),
                          ("landscape", _("Orizzontale"))):
            self.orientation.addItem(text, key)
        f.addRow(pgettext("pagina", "Formato"), self.paper)
        f.addRow(_("Larghezza"), self.width_mm)
        f.addRow(_("Altezza"), self.height_mm)
        f.addRow(_("Orientamento"), self.orientation)
        self.match = QCheckBox(_("Adegua al formato della pagina"))
        self.match_page = QSpinBox()
        self.match_page.setRange(1, max(1, self.page_count))
        row = QHBoxLayout()
        row.addWidget(self.match)
        row.addWidget(self.match_page)
        row.addStretch(1)
        f.addRow(row)
        self.form.addWidget(box)

        mbox = QGroupBox(_("Modalità"))
        mf = QVBoxLayout(mbox)
        self.mode_scale = QRadioButton(_("Scala il contenuto"))
        self.mode_canvas = QRadioButton(_("Cambia solo la tela"))
        self.mode_fill = QRadioButton(_("Riempi (taglia le eccedenze)"))
        self.mode_scale.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.mode_scale)
        grp.addButton(self.mode_fill)
        grp.addButton(self.mode_canvas)
        self.proportional = QCheckBox(_("Mantieni le proporzioni"))
        self.proportional.setChecked(True)
        self.margin = _spin(0, 0, 500, 1, "mm")
        mf.addWidget(self.mode_scale)
        mf.addWidget(self.mode_fill)
        mf.addWidget(self.mode_canvas)
        mf.addWidget(self.proportional)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel(_("Margine")))
        mrow.addWidget(self.margin)
        mrow.addStretch(1)
        mf.addLayout(mrow)
        mf.addWidget(QLabel(_("Ancoraggio")))
        agrid = QGridLayout()
        agrid.setSpacing(2)
        self.anchor_group = QButtonGroup(self)
        self.anchor_buttons: dict[str, QToolButton] = {}
        for n, (key, arrow) in enumerate(zip(_ANCHOR_ORDER, _ANCHOR_ARROWS)):
            b = QToolButton()
            b.setText(arrow)
            b.setCheckable(True)
            b.setFixedSize(28, 28)
            b.setToolTip(anchor_label(key))
            b.setAccessibleName(anchor_label(key))
            self.anchor_group.addButton(b)
            self.anchor_buttons[key] = b
            agrid.addWidget(b, n // 3, n % 3)
            b.toggled.connect(self.schedule)
        self.anchor_buttons["center"].setChecked(True)
        aw = QWidget()
        aw.setLayout(agrid)
        mf.addWidget(aw, 0, Qt.AlignmentFlag.AlignLeft)
        self.form.addWidget(mbox)
        self.paper.currentIndexChanged.connect(self._paper_changed)
        self._connect(self.paper, self.width_mm, self.height_mm, self.orientation, self.match, self.match_page,
                      self.mode_scale, self.mode_fill, self.mode_canvas, self.proportional, self.margin)
        self._plan = None
        self._paper_changed()

    def _paper_changed(self) -> None:
        key = self.paper.currentData()
        custom = key == "custom"
        self.width_mm.setEnabled(custom)
        self.height_mm.setEnabled(custom)
        if not custom:
            w, h = PAPER_SIZES[key]
            self.width_mm.setValue(round(w / MM, 1))
            self.height_mm.setValue(round(h / MM, 1))

    def anchor(self) -> str:
        for k, b in self.anchor_buttons.items():
            if b.isChecked():
                return k
        return "center"

    def options(self) -> dict:
        m = self.margin.value() * MM
        mode = "scale" if self.mode_scale.isChecked() else ("fill" if self.mode_fill.isChecked() else "canvas")
        return {
            "pages": self.selected_pages(),
            "size": (self.width_mm.value() * MM, self.height_mm.value() * MM),
            "mode": mode,
            "proportional": self.proportional.isChecked(),
            "anchor": self.anchor(),
            "margins": (m, m, m, m) if m > 0 and mode != "canvas" else None,
            "orientation": self.orientation.currentData(),
            "match_page": self.match_page.value() - 1 if self.match.isChecked() else None,
        }

    def validate(self) -> str | None:
        o = self.options()
        matching = o["match_page"] is not None
        for w in (self.paper, self.orientation):
            w.setEnabled(not matching)
        custom = self.paper.currentData() == "custom" and not matching
        self.width_mm.setEnabled(custom)
        self.height_mm.setEnabled(custom)
        self.match_page.setEnabled(matching)
        self.proportional.setEnabled(o["mode"] == "scale")
        self.margin.setEnabled(o["mode"] != "canvas")
        self.mode_fill.setEnabled(not matching)
        idx = self.preview_index()
        with ENGINE_LOCK:
            pdf = self.document.pdf
            dw, dh = layout.displayed_crop_size(pdf, idx)
            if matching:
                tw, th = layout.displayed_crop_size(pdf, o["match_page"])
            else:
                tw, th = layout._target_size(o["size"], o["orientation"], dw, dh)
        layout.check_page_size(tw, th)
        fx, fy = layout.ANCHORS[o["anchor"]]
        if o["mode"] == "canvas":
            cw, ch = dw, dh
            area = pymupdf.Rect(0, 0, tw, th)
        else:
            m = (o["margins"] or (0, 0, 0, 0))[0]
            area = pymupdf.Rect(m, m, tw - m, th - m)
            if area.width < 3 or area.height < 3:
                return _("I margini lasciano un'area troppo piccola per il contenuto.")
            sx, sy = area.width / dw, area.height / dh
            if o["mode"] == "fill" and not matching:
                sx = sy = max(sx, sy)
            elif o["proportional"] or matching:
                sx = sy = min(sx, sy)
            cw, ch = dw * sx, dh * sy
        ox = area.x0 + (area.width - cw) * fx
        oy = area.y0 + (area.height - ch) * fy
        self._plan = (tw, th, pymupdf.Rect(ox, oy, ox + cw, oy + ch))
        notes = [f"{dw / MM:.1f} × {dh / MM:.1f} mm  →  {tw / MM:.1f} × {th / MM:.1f} mm".replace(".", ",")]
        if o["mode"] == "fill" and not matching and (cw > tw + 0.5 or ch > th + 0.5):
            notes.append(_("Le parti eccedenti del contenuto saranno tagliate."))
        if o["mode"] != "canvas":
            with ENGINE_LOCK:
                small = layout.text_too_small(self.document.pdf[idx], min(cw / dw, ch / dh))
            if small:
                notes.append(_("Il testo scenderà sotto 4 pt effettivi: potrebbe essere poco leggibile."))
        self.info.setText("\n".join(notes))
        return None

    def paint_overlay(self, p: QPainter, s: float) -> None:
        pass

    def refresh(self) -> None:
        super().refresh()
        # anteprima: nuova pagina con il contenuto collocato
        if self._plan is None:
            return
        tw, th, content = self._plan
        self.preview.paint_fn = self._paint_plan
        self.preview.size_pts = (tw, th)
        self._content_rect = content
        self.preview.update()

    def _paint_plan(self, p: QPainter, s: float) -> None:
        tw, th, content = self._plan
        p.fillRect(QRectF(0, 0, tw, th), QColor("white"))
        if self.preview.image is not None:
            p.save()
            p.setClipRect(QRectF(0, 0, tw, th))
            p.drawImage(_qrect(content), self.preview.image)
            p.restore()
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.color("accent"), 1.5 / s, Qt.PenStyle.DashLine))
        p.drawRect(_qrect(content))
        p.setPen(QPen(theme.color("border"), 1 / s))
        p.drawRect(QRectF(0, 0, tw, th))


def open_resize_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-28: cambia il formato (scala o tela), o adegua a una pagina di riferimento."""
    if view is None:
        return False
    dlg = ResizeDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        report: dict = {}
        with view.document.change(_("Formato pagina")) as pdf:
            if o["match_page"] is not None:
                changed = layout.match_page_size(pdf, o["pages"], o["match_page"],
                                                 mode="canvas" if o["mode"] == "canvas" else "scale",
                                                 proportional=True, anchor=o["anchor"])
            else:
                changed = layout.resize_pages(pdf, o["pages"], o["size"], mode=o["mode"],
                                              proportional=o["proportional"], anchor=o["anchor"],
                                              margins=o["margins"], orientation=o["orientation"], report=report)
            if not changed:
                raise PageOpError(_("Nessuna pagina modificata: il formato è già quello richiesto."))
        notes = []
        if report.get("overflow"):
            notes.append(_("Contenuto tagliato nelle pagine {p}.").format(p=_pages_text(report["overflow"])))
        if report.get("small_text"):
            notes.append(_("Testo sotto 4 pt effettivi (poco leggibile) nelle pagine {p}.")
                         .format(p=_pages_text(report["small_text"])))
        if notes:
            ctx.info(_("Formato pagina"), "\n\n".join(notes))
        return True

    return _guard(ctx, _("Impossibile cambiare il formato"), run)


# =========================================================================
# PAG-29 — Dividi pagina
# =========================================================================

class SplitPageDialog(_LayoutDialog):
    TITLE = "Dividi pagina"
    ICON = "square-split-horizontal"

    def build(self) -> None:
        box = QGroupBox(_("Dividi pagina"))
        f = QFormLayout(box)
        self.rows = QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setValue(1)
        self.cols = QSpinBox()
        self.cols.setRange(1, 20)
        self.cols.setValue(2)
        self.rtl = QCheckBox(_("Da destra a sinistra"))
        self.overlap = _spin(0, 0, 100, 1, "mm")
        self.cuts_x = QLineEdit()
        self.cuts_x.setPlaceholderText(_("facoltativi, es. 100, 210"))
        self.cuts_y = QLineEdit()
        self.cuts_y.setPlaceholderText(_("facoltativi, es. 100, 210"))
        self.method = QComboBox()
        self.method.addItem(_("Griglia"), "grid")
        self.method.addItem(_("Rileva la piega centrale"), "fold")
        self.remove_outside = QCheckBox(_("Rimuovi il testo esterno a ogni parte"))
        f.addRow(_("Metodo"), self.method)
        f.addRow(_("Righe"), self.rows)
        f.addRow(_("Colonne"), self.cols)
        f.addRow(_("Tagli verticali (mm)"), self.cuts_x)
        f.addRow(_("Tagli orizzontali (mm)"), self.cuts_y)
        f.addRow(_("Sovrapposizione"), self.overlap)
        f.addRow(self.rtl)
        f.addRow(self.remove_outside)
        self.form.addWidget(box)
        self.outside_note = _muted(_("Il contenuto fuori da ciascuna parte resta nel file e può comparire in "
                                     "ricerca e copia."))
        self.form.addWidget(self.outside_note)
        self._parts: list[pymupdf.Rect] = []
        self._fold_cache: dict[int, float | None] = {}
        self._connect(self.rows, self.cols, self.rtl, self.overlap, self.cuts_x, self.cuts_y, self.method,
                      self.remove_outside)

    def _cuts(self, edit: QLineEdit):
        vals = _parse_numbers(edit.text().replace(",", " ")) if edit.text().strip() else []
        if vals is None:
            raise PageOpError(_("Valori non validi: usa numeri separati da virgola."))
        return [v * MM for v in vals] or None

    def options(self) -> dict:
        try:
            cx, cy = self._cuts(self.cuts_x), self._cuts(self.cuts_y)
        except PageOpError:
            cx = cy = None
        return {
            "pages": self.selected_pages(),
            "rows": self.rows.value(),
            "cols": self.cols.value(),
            "rtl": self.rtl.isChecked(),
            "cuts_x": cx,
            "cuts_y": cy,
            "overlap": self.overlap.value() * MM,
            "fold": self.method.currentData() == "fold",
            "remove_outside": self.remove_outside.isChecked(),
        }

    def validate(self) -> str | None:
        self._parts = []
        fold = self.method.currentData() == "fold"
        cx, cy = self._cuts(self.cuts_x), self._cuts(self.cuts_y)
        self.cols.setEnabled(cx is None and not fold)
        self.cuts_x.setEnabled(not fold)
        self.rows.setEnabled(cy is None)
        self.outside_note.setVisible(not self.remove_outside.isChecked())
        o = self.options()
        idx = self.preview_index()
        note = ""
        with ENGINE_LOCK:
            w, h = layout.displayed_crop_size(self.document.pdf, idx)
            if fold:
                if idx not in self._fold_cache:
                    self._fold_cache[idx] = layout.detect_fold(self.document.pdf[idx])
                x = self._fold_cache[idx]
                cx = [x if x is not None else w / 2]
                note = (_("Piega non rilevata: taglio al centro.") if x is None else
                        _("Piega rilevata a {x} mm dal bordo sinistro.").format(x=f"{x / MM:.1f}".replace(".", ",")))
        self._parts = layout.split_parts_disp(w, h, o["rows"], 2 if fold else o["cols"], o["rtl"], cx, cy,
                                              o["overlap"])
        text = _("Ogni pagina sarà sostituita da {n} parti.").format(n=len(self._parts))
        self.info.setText(text + ("\n" + note if note else ""))
        return None

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if not self._parts:
            return
        font = p.font()
        font.setPointSizeF(max(6.0, 14.0 / s))
        font.setBold(True)
        p.setFont(font)
        for n, r in enumerate(self._parts, start=1):
            q = _qrect(r)
            p.setBrush(_alpha("accent", 28))
            p.setPen(QPen(theme.color("accent"), 2 / s))
            p.drawRect(q)
            badge = QRectF(q.center().x() - 16 / s, q.center().y() - 16 / s, 32 / s, 32 / s)
            p.setBrush(theme.color("accent"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(badge)
            p.setPen(theme.color("accent_text"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n))


def open_split_page_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-29: sostituisce ogni pagina con le sue parti."""
    if view is None:
        return False
    dlg = SplitPageDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        with view.document.change(_("Dividi pagina")) as pdf:
            layout.split_page_grid(pdf, o["pages"], rows=o["rows"], cols=o["cols"], rtl=o["rtl"],
                                   cuts_x=o["cuts_x"], cuts_y=o["cuts_y"], overlap=o["overlap"],
                                   remove_outside=o["remove_outside"], fold=o["fold"])
        return True

    return _guard(ctx, _("Impossibile dividere la pagina"), run)


# =========================================================================
# PAG-30 — Pagine affiancate
# =========================================================================

NUP_PRESETS = [(2, (2, 1)), (4, (2, 2)), (6, (3, 2)), (8, (4, 2)), (9, (3, 3)), (16, (4, 4))]


class NupDialog(_LayoutDialog):
    TITLE = "Pagine affiancate"
    ICON = "layout-grid"
    OK_TEXT = "Crea"

    def build(self) -> None:
        box = QGroupBox(_("Pagine per foglio"))
        f = QFormLayout(box)
        self.preset = QComboBox()
        for n, (c, r) in NUP_PRESETS:
            self.preset.addItem(str(n), f"{c}x{r}")
        self.preset.addItem(_("Griglia personalizzata"), None)
        self.cols = QSpinBox()
        self.cols.setRange(1, 20)
        self.cols.setValue(2)
        self.rows = QSpinBox()
        self.rows.setRange(1, 20)
        self.rows.setValue(1)
        grid_row = QHBoxLayout()
        grid_row.addWidget(self.cols)
        grid_row.addWidget(QLabel("×"))
        grid_row.addWidget(self.rows)
        f.addRow(_("Pagine per foglio"), self.preset)
        f.addRow(_("Colonne") + " × " + _("Righe"), grid_row)
        self.sheet = _paper_combo(custom=False)
        self.orientation = QComboBox()
        for key, text in ((None, _("Automatico (come la pagina)")), (False, _("Verticale")), (True, _("Orizzontale"))):
            self.orientation.addItem(text, key)
        self.order = QComboBox()
        self.order.addItem(_("Per righe"), "rows")
        self.order.addItem(_("Per colonne"), "columns")
        self.rtl = QCheckBox(_("Da destra a sinistra"))
        self.margin = _spin(6, 0, 100, 1, "mm")
        self.spacing = _spin(2, 0, 100, 1, "mm")
        self.scale = QComboBox()
        self.scale.addItem(_("Adatta alla cella"), "fit")
        self.scale.addItem(_("Dimensione reale"), "actual")
        self.annotations = QComboBox()
        self.annotations.addItem(_("Appiattisci"), "flatten")
        self.annotations.addItem(_("Scarta"), "discard")
        f.addRow(_("Foglio"), self.sheet)
        f.addRow(_("Orientamento"), self.orientation)
        f.addRow(_("Ordine"), self.order)
        f.addRow(_("Margine"), self.margin)
        f.addRow(_("Spaziatura"), self.spacing)
        f.addRow(_("Scala"), self.scale)
        f.addRow(_("Annotazioni"), self.annotations)
        self.border = QCheckBox(_("Bordo attorno a ogni pagina"))
        self.auto_rotate = QCheckBox(_("Rotazione automatica"))
        self.auto_rotate.setChecked(True)
        self.repeat = QCheckBox(_("Ripeti la stessa pagina su tutto il foglio"))
        for w in (self.rtl, self.border, self.auto_rotate, self.repeat):
            f.addRow(w)
        self.form.addWidget(box)
        self.preset.currentIndexChanged.connect(self._preset_changed)
        self._connect(self.preset, self.cols, self.rows, self.sheet, self.orientation, self.order, self.rtl,
                      self.margin, self.spacing, self.scale, self.annotations, self.border, self.auto_rotate,
                      self.repeat)
        self._plan = None
        self._thumbs: dict[int, QImage] = {}
        self._preset_changed()

    def _preset_changed(self) -> None:
        grid = self.preset.currentData()
        custom = grid is None
        self.cols.setEnabled(custom)
        self.rows.setEnabled(custom)
        if grid:
            c, r = (int(v) for v in grid.split("x"))
            self.cols.setValue(c)
            self.rows.setValue(r)

    def options(self) -> dict:
        return {
            "pages": self.selected_pages(),
            "cols": self.cols.value(),
            "rows": self.rows.value(),
            "sheet_size": PAPER_SIZES[self.sheet.currentData()],
            "landscape": self.orientation.currentData(),
            "order": self.order.currentData(),
            "rtl": self.rtl.isChecked(),
            "margin": self.margin.value() * MM,
            "spacing": self.spacing.value() * MM,
            "border": self.border.isChecked(),
            "scale": self.scale.currentData(),
            "auto_rotate": self.auto_rotate.isChecked(),
            "repeat": self.repeat.isChecked(),
            "annotations": self.annotations.currentData(),
        }

    def validate(self) -> str | None:
        o = self.options()
        self._plan = None
        pages = o["pages"]
        if not pages:
            return None
        with ENGINE_LOCK:
            sizes = [layout.displayed_crop_size(self.document.pdf, i) for i in pages]
        kw = {k: o[k] for k in ("cols", "rows", "sheet_size", "landscape", "order", "rtl", "margin", "spacing",
                                "scale", "auto_rotate", "repeat")}
        self._plan = layout.nup_layout(sizes, **kw)
        self._pages = pages
        text = _("Fogli risultanti: {n}").format(n=len(self._plan["sheets"]))
        if o["cols"] * o["rows"] > layout.MAX_CELLS_WARN:
            text += "\n" + _("Griglia oltre 100 celle: le pagine saranno molto piccole.")
        self.info.setText(text)
        return None

    def refresh(self) -> None:
        super().refresh()
        self.preview_title.setText(_("Anteprima") + " — " + _("Foglio") + " 1")
        if self._plan is not None:
            self.preview.image = None
            self.preview.size_pts = self._plan["sheet"]
            self.preview.update()

    def _thumb(self, index: int) -> QImage | None:
        if index not in self._thumbs:
            if len(self._thumbs) > 64:
                self._thumbs.clear()
            self._thumbs[index] = _render(self.document, index, 220)[0]
        return self._thumbs[index]

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if self._plan is None:
            return
        sw, sh = self._plan["sheet"]
        p.fillRect(QRectF(0, 0, sw, sh), QColor("white"))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(theme.color("border"), 1 / s, Qt.PenStyle.DotLine))
        for c in self._plan["cells"]:
            p.drawRect(_qrect(c))
        for k, rect, extra in self._plan["sheets"][0]:
            img = self._thumb(self._pages[k])
            q = _qrect(rect)
            if img is not None:
                p.save()
                p.translate(q.center())
                p.rotate(extra)
                w, h = (q.height(), q.width()) if extra % 180 else (q.width(), q.height())
                p.drawImage(QRectF(-w / 2, -h / 2, w, h), img)
                p.restore()
            if self.border.isChecked():
                p.setPen(QPen(theme.color("text"), 0.8 / s))
                p.drawRect(q)
        self.preview.size_pts = (sw, sh)


def open_nup_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-30: crea un nuovo documento con più pagine per foglio e lo apre in una scheda."""
    if view is None:
        return False
    dlg = NupDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        kw = {k: v for k, v in o.items() if k != "pages"}
        with ENGINE_LOCK:
            data = layout.nup(view.document.pdf, o["pages"], **kw)
        stem = Path(view.document.display_name).stem
        ctx.open_document(Document.from_bytes(data, display_name=f"{stem} ({_('affiancate')}).pdf"))
        return True

    return _guard(ctx, _("Impossibile creare le pagine affiancate"), run)


# =========================================================================
# PAG-31 — Opuscolo
# =========================================================================

TRANSLATIONS.update({
    "Opuscolo": "Booklet",
    "opuscolo": "booklet",
    "Rilegatura": "Binding",
    "A sinistra": "On the left",
    "A destra": "On the right",
    "Fascicolo unico (punto metallico)": "Single section (saddle stitch)",
    "Segnature di": "Signatures of",
    "fogli": "sheets",
    "Pagine vuote aggiunte": "Blank pages added",
    "In fondo": "At the end",
    "Prima dell'ultima pagina": "Before the last page",
    "Margine di rilegatura": "Binding margin",
    "Compensazione dello scorrimento": "Creep compensation",
    "Facciate: {n} ({f} fogli fronte/retro), pagine vuote aggiunte: {b}":
        "Sides: {n} ({f} duplex sheets), blank pages added: {b}",
    "Con 1–2 pagine l'opuscolo ha poco senso: verranno aggiunte pagine vuote.":
        "With 1–2 pages a booklet makes little sense: blank pages will be added.",
    "Impossibile creare l'opuscolo": "Cannot create the booklet",
    "Facciata 1": "Side 1",
})


class BookletDialog(_LayoutDialog):
    TITLE = "Opuscolo"
    ICON = "book-open"
    OK_TEXT = "Crea"

    def build(self) -> None:
        box = QGroupBox(_("Opuscolo"))
        f = QFormLayout(box)
        self.sheet = _paper_combo(custom=False)
        self.binding = QComboBox()
        self.binding.addItem(_("A sinistra"), "left")
        self.binding.addItem(_("A destra"), "right")
        self.saddle = QRadioButton(_("Fascicolo unico (punto metallico)"))
        self.signatures = QRadioButton(_("Segnature di"))
        self.saddle.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.saddle)
        grp.addButton(self.signatures)
        self.sig_sheets = QSpinBox()
        self.sig_sheets.setRange(1, 64)
        self.sig_sheets.setValue(4)
        self.sig_sheets.setSuffix(" " + _("fogli"))
        sig_row = QHBoxLayout()
        sig_row.addWidget(self.signatures)
        sig_row.addWidget(self.sig_sheets)
        sig_row.addStretch(1)
        self.blank_pos = QComboBox()
        self.blank_pos.addItem(_("In fondo"), "end")
        self.blank_pos.addItem(_("Prima dell'ultima pagina"), "before_last")
        self.gutter = _spin(0, 0, 50, 1, "mm")
        self.creep = _spin(0, 0, 10, 1, "mm", 0.1)
        f.addRow(_("Foglio"), self.sheet)
        f.addRow(_("Rilegatura"), self.binding)
        f.addRow(self.saddle)
        f.addRow(sig_row)
        f.addRow(_("Pagine vuote aggiunte"), self.blank_pos)
        f.addRow(_("Margine di rilegatura"), self.gutter)
        f.addRow(_("Compensazione dello scorrimento"), self.creep)
        self.form.addWidget(box)
        self._connect(self.sheet, self.binding, self.saddle, self.signatures, self.sig_sheets, self.blank_pos,
                      self.gutter, self.creep)
        self._sides = []
        self._pages: list[int] = []
        self._thumbs: dict[int, QImage] = {}

    def options(self) -> dict:
        return {
            "pages": self.selected_pages(),
            "sheet_size": PAPER_SIZES[self.sheet.currentData()],
            "binding": self.binding.currentData(),
            "signature_sheets": self.sig_sheets.value() if self.signatures.isChecked() else None,
            "blank_position": self.blank_pos.currentData(),
            "gutter": self.gutter.value() * MM,
            "creep": self.creep.value() * MM,
        }

    def validate(self) -> str | None:
        o = self.options()
        self.sig_sheets.setEnabled(self.signatures.isChecked())
        self._sides = []
        pages = o["pages"]
        if not pages:
            return None
        self._pages = pages
        self._sides = layout.booklet_order(len(pages), o["binding"], o["signature_sheets"], o["blank_position"])
        n = len(self._sides)
        text = _("Facciate: {n} ({f} fogli fronte/retro), pagine vuote aggiunte: {b}").format(
            n=n, f=n // 2, b=(-len(pages)) % 4)
        if len(pages) <= 2:
            text += "\n" + _("Con 1–2 pagine l'opuscolo ha poco senso: verranno aggiunte pagine vuote.")
        self.info.setText(text)
        return None

    def refresh(self) -> None:
        super().refresh()
        self.preview_title.setText(_("Anteprima") + " — " + _("Facciata 1"))
        lo, hi = sorted(self.options()["sheet_size"])
        self.preview.image = None
        self.preview.size_pts = (hi, lo)
        self.preview.update()

    def _thumb(self, index: int) -> QImage | None:
        if index not in self._thumbs:
            self._thumbs[index] = _render(self.document, index, 260)[0]
        return self._thumbs[index]

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if not self._sides:
            return
        o = self.options()
        lo, hi = sorted(o["sheet_size"])
        sw, sh = hi, lo
        p.fillRect(QRectF(0, 0, sw, sh), QColor("white"))
        half = (sw - o["gutter"]) / 2
        for slot, k in enumerate(self._sides[0]):
            x0 = 0.0 if slot == 0 else sw - half
            cell = QRectF(x0, 0, half, sh)
            if k is not None:
                img = self._thumb(self._pages[k])
                if img is not None and img.width() and img.height():
                    sc = min(cell.width() / img.width(), cell.height() / img.height())
                    w, h = img.width() * sc, img.height() * sc
                    p.drawImage(QRectF(cell.center().x() - w / 2, cell.center().y() - h / 2, w, h), img)
        p.setPen(QPen(theme.color("accent"), 1.5 / s, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(sw / 2, 0), QPointF(sw / 2, sh))
        self.preview.size_pts = (sw, sh)


def open_booklet_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-31: crea l'opuscolo imposto e lo apre in una nuova scheda."""
    if view is None:
        return False
    dlg = BookletDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        kw = {k: v for k, v in o.items() if k != "pages"}
        with ENGINE_LOCK:
            data = layout.booklet(view.document.pdf, o["pages"], **kw)
        stem = Path(view.document.display_name).stem
        ctx.open_document(Document.from_bytes(data, display_name=f"{stem} ({_('opuscolo')}).pdf"))
        return True

    return _guard(ctx, _("Impossibile creare l'opuscolo"), run)
