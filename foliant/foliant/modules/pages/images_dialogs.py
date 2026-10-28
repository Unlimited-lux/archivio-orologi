"""Dialoghi per PAG-08 (inserisci da immagini), PAG-09 (inserisci dagli appunti)
e PAG-13 (estrai pagine come immagini).

Ogni dialogo è costruibile da solo e offre `options() -> dict`; le funzioni
`open_*` lo mostrano, applicano l'operazione e riferiscono l'esito.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from foliant.i18n import pgettext
from foliant.api import OperationCancelled
from foliant.api import ENGINE_LOCK, _, icon, theme

from . import images
from .engine import MM, PAPER_SIZES, PageOpError, insert_pdf_pages

TRANSLATIONS = {
    "Inserisci immagini": "Insert images",
    "Inserisci dagli appunti": "Insert from clipboard",
    "Estrai come immagini": "Extract as images",
    "Immagini": "Images",
    "Immagini da inserire (trascina per riordinare)": "Images to insert (drag to reorder)",
    "Aggiungi…": "Add…",
    "Rimuovi": "Remove",
    "Sposta su": "Move up",
    "Sposta giù": "Move down",
    "Scegli immagini": "Choose images",
    "Formato pagina": "Page size",
    "Come l'immagine": "Same as image",
    "Come pagina adiacente": "Same as adjacent page",
    "Adattamento": "Fit",
    "Contieni": "Contain",
    "Riempi": "Fill",
    "Centra senza scala": "Center without scaling",
    "Margini": "Margins",
    "Orientamento": "Orientation",
    "Automatico": "Automatic",
    "Verticale": "Portrait",
    "Orizzontale": "Landscape",
    "Qualità": "Quality",
    "Conserva i dati originali": "Keep original data",
    "Ricomprimi (JPEG)": "Recompress (JPEG)",
    "Posizione": "Position",
    "Prima della selezione": "Before the selection",
    "Dopo la selezione": "After the selection",
    "All'inizio": "At the start",
    "Alla fine": "At the end",
    "Inserisci": "Insert",
    "Aggiungi almeno un'immagine.": "Add at least one image.",
    "Alcuni file non sono stati inseriti": "Some files were not inserted",
    "Avvisi": "Warnings",
    "Pagine inserite: {n}.": "Pages inserted: {n}.",
    "Impossibile inserire le immagini": "Cannot insert the images",
    "Impossibile inserire dagli appunti": "Cannot insert from the clipboard",
    "Contenuto rilevato": "Detected content",
    "Pagine PDF": "PDF pages",
    "Immagine": "Image",
    "Elenco di file": "File list",
    "Testo formattato": "Formatted text",
    "Testo": "Text",
    "Nessun contenuto supportato": "No supported content",
    "Gli appunti non contengono pagine, immagini, file o testo.": "The clipboard contains no pages, images, files or text.",
    "{tipo} — {n} pagine risultanti": "{tipo} — {n} resulting pages",
    "{tipo} — 1 pagina risultante": "{tipo} — 1 resulting page",
    "Formato pagina (testo)": "Page size (text)",
    "Dimensione carattere": "Font size",
    "Anteprima non disponibile: {e}": "Preview not available: {e}",
    "file\x04Formato": "Format",
    "Risoluzione": "Resolution",
    "Colore": "Color",
    "A colori": "Color",
    "Scala di grigi": "Grayscale",
    "Bianco e nero": "Black and white",
    "Qualità JPEG/WebP": "JPEG/WebP quality",
    "Sfondo trasparente": "Transparent background",
    "Includi annotazioni": "Include annotations",
    "Area esportata": "Exported area",
    "Pagina visibile": "Visible page",
    "Intero foglio": "Whole sheet",
    "Un unico TIFF multipagina": "A single multi-page TIFF",
    "Pagine": "Pages",
    "Pagine selezionate ({n})": "Selected pages ({n})",
    "Pagina corrente": "Current page",
    "Tutte le pagine ({n})": "All pages ({n})",
    "Cartella": "Folder",
    "Sfoglia…": "Browse…",
    "Scegli la cartella di destinazione": "Choose the destination folder",
    "Modello del nome": "Name template",
    "Segnaposto: {nome} nome del documento, {pagina} numero di pagina.": "Placeholders: {nome} document name, {pagina} page number.",
    "Esporta": "Export",
    "Esportazione immagini…": "Exporting images…",
    "Risoluzione troppo alta": "Resolution too high",
    "{m}\n\nEsportare a {s} dpi?": "{m}\n\nExport at {s} dpi?",
    "Esportazione completata": "Export completed",
    "Immagini create: {n} in «{d}».": "Images created: {n} in “{d}”.",
    "Errori:": "Errors:",
    "Impossibile esportare le immagini": "Cannot export the images",
    "Indica la cartella di destinazione.": "Choose the destination folder.",
    "Immagini (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.jp2 *.jpx *.heic *.heif);;Tutti i file (*)":
        "Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.jp2 *.jpx *.heic *.heif);;All files (*)",
}

IMAGE_FILTER = "Immagini (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.jp2 *.jpx *.heic *.heif);;Tutti i file (*)"

POS_BEFORE = "before"
POS_AFTER = "after"
POS_START = "start"
POS_END = "end"


def insertion_index(position: str, selected: list[int], page_count: int, current: int = 0) -> int:
    """Indice prima del quale inserire secondo la posizione scelta."""
    if position == POS_START:
        return 0
    if position == POS_END:
        return page_count
    sel = sorted(i for i in selected if 0 <= i < page_count) or ([current] if page_count else [])
    if not sel:
        return 0
    return sel[0] if position == POS_BEFORE else sel[-1] + 1


def _position_combo(default: str = POS_AFTER) -> QComboBox:
    combo = QComboBox()
    for text, value in ((_("Dopo la selezione"), POS_AFTER), (_("Prima della selezione"), POS_BEFORE),
                        (_("All'inizio"), POS_START), (_("Alla fine"), POS_END)):
        combo.addItem(text, value)
    combo.setCurrentIndex(max(0, combo.findData(default)))
    return combo


def _set_combo(combo: QComboBox, value) -> None:
    k = combo.findData(value)
    if k >= 0:
        combo.setCurrentIndex(k)


def _muted_label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
    return lab


def _uniform_fields(form: QFormLayout, width: int = 240) -> None:
    """Campi del modulo con la stessa larghezza minima (aspetto ordinato)."""
    for r in range(form.rowCount()):
        item = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
        w = item.widget() if item is not None else None
        if isinstance(w, (QComboBox, QSpinBox, QDoubleSpinBox)):
            w.setMinimumWidth(width)


def _selected(view, selected) -> list[int]:
    return sorted({int(i) for i in (selected or []) if 0 <= int(i) < view.page_count})


def _current(view) -> int:
    try:
        return int(view.current_page)
    except Exception:
        return 0


# =========================================================================
# PAG-08 — Inserisci da immagini
# =========================================================================

class InsertImagesDialog(QDialog):
    """Elenco riordinabile delle immagini e opzioni di impaginazione."""

    def __init__(self, paths: list[str] | None = None, parent: QWidget | None = None, *,
                 ask_paths=None, fixed_position: bool = False):
        super().__init__(parent)
        self.setWindowTitle(_("Inserisci immagini"))
        self.setMinimumWidth(520)
        self._ask_paths = ask_paths
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        lay.addWidget(QLabel(_("Immagini da inserire (trascina per riordinare)")))
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setMinimumHeight(160)
        row.addWidget(self.list, 1)
        buttons = QVBoxLayout()
        self.add_btn = self._tool_button("plus", _("Aggiungi…"), self._add)
        self.remove_btn = self._tool_button("trash-2", _("Rimuovi"), self._remove)
        self.up_btn = self._tool_button("arrow-up", _("Sposta su"), lambda: self.move_selected(-1))
        self.down_btn = self._tool_button("arrow-down", _("Sposta giù"), lambda: self.move_selected(1))
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            buttons.addWidget(b)
        buttons.addStretch(1)
        row.addLayout(buttons)
        lay.addLayout(row)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.size_combo = QComboBox()
        self.size_combo.addItem(_("Come l'immagine"), None)
        self.size_combo.addItem("A4", "A4")
        self.size_combo.addItem("Letter", "Letter")
        self.size_combo.addItem(_("Come pagina adiacente"), "adjacent")
        form.addRow(_("Formato pagina"), self.size_combo)
        self.fit_combo = QComboBox()
        for text, value in ((_("Contieni"), "contain"), (_("Riempi"), "fill"), (_("Centra senza scala"), "center")):
            self.fit_combo.addItem(text, value)
        form.addRow(_("Adattamento"), self.fit_combo)
        self.orient_combo = QComboBox()
        for text, value in ((_("Automatico"), "auto"), (_("Verticale"), False), (_("Orizzontale"), True)):
            self.orient_combo.addItem(text, value)
        form.addRow(_("Orientamento"), self.orient_combo)
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0, 100)
        self.margin_spin.setDecimals(1)
        self.margin_spin.setSuffix(" mm")
        form.addRow(_("Margini"), self.margin_spin)
        self.quality_combo = QComboBox()
        self.quality_combo.addItem(_("Conserva i dati originali"), False)
        self.quality_combo.addItem(_("Ricomprimi (JPEG)"), True)
        form.addRow(_("Qualità"), self.quality_combo)
        self.position_combo = _position_combo()
        self.position_label = QLabel(_("Posizione"))
        form.addRow(self.position_label, self.position_combo)
        if fixed_position:
            self.position_combo.hide()
            self.position_label.hide()
        _uniform_fields(form)
        lay.addLayout(form)

        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Inserisci"))
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)

        self.size_combo.currentIndexChanged.connect(self._update_state)
        self.list.model().rowsInserted.connect(self._update_state)
        self.list.model().rowsRemoved.connect(self._update_state)
        self.list.itemSelectionChanged.connect(self._update_state)
        self.set_paths(paths or [])
        self._update_state()

    def _tool_button(self, icon_name: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(icon_name))
        b.setToolTip(tip)
        b.setAutoRaise(False)
        b.clicked.connect(slot)
        return b

    # -- elenco
    def set_paths(self, paths: list[str]) -> None:
        self.list.clear()
        self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        for p in paths:
            item = QListWidgetItem(icon("image"), Path(p).name)
            item.setData(Qt.ItemDataRole.UserRole, str(p))
            item.setToolTip(str(p))
            self.list.addItem(item)

    def paths(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _add(self) -> None:
        if self._ask_paths is None:
            return
        self.add_paths(self._ask_paths() or [])

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))

    def move_selected(self, delta: int) -> None:
        rows = sorted(self.list.row(it) for it in self.list.selectedItems())
        if not rows:
            return
        if delta < 0 and rows[0] == 0 or delta > 0 and rows[-1] == self.list.count() - 1:
            return
        order = rows if delta < 0 else list(reversed(rows))
        for r in order:
            item = self.list.takeItem(r)
            self.list.insertItem(r + delta, item)
        self.list.clearSelection()
        for r in rows:
            self.list.item(r + delta).setSelected(True)

    def _update_state(self, *args) -> None:
        sized = self.size_combo.currentData() is not None
        self.fit_combo.setEnabled(sized)
        self.orient_combo.setEnabled(sized)
        has_sel = bool(self.list.selectedItems())
        self.remove_btn.setEnabled(has_sel)
        self.up_btn.setEnabled(has_sel)
        self.down_btn.setEnabled(has_sel)
        self.ok_button.setEnabled(self.list.count() > 0)

    def options(self) -> dict:
        return {
            "paths": self.paths(),
            "page_size": self.size_combo.currentData(),
            "fit": self.fit_combo.currentData(),
            "margin": self.margin_spin.value() * MM,
            "landscape": self.orient_combo.currentData(),
            "recompress": bool(self.quality_combo.currentData()),
            "position": self.position_combo.currentData(),
        }


def open_insert_images_dialog(ctx, view, selected: list[int], paths: list[str] | None = None,
                              at: int | None = None) -> bool:
    """PAG-08: chiede le immagini (se non date), le opzioni e le inserisce.
    Restituisce True se sono state inserite pagine."""
    if view is None:
        return False
    if paths is None:
        paths = ctx.ask_open_paths(_("Scegli immagini"), _(IMAGE_FILTER), True)
        if not paths:
            return False

    def ask():
        return ctx.ask_open_paths(_("Scegli immagini"), _(IMAGE_FILTER), True)

    dlg = InsertImagesDialog(list(paths), view.window() if hasattr(view, "window") else None,
                             ask_paths=ask, fixed_position=at is not None)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    opts = dlg.options()
    if not opts["paths"]:
        ctx.error(_("Inserisci immagini"), _("Aggiungi almeno un'immagine."))
        return False
    sel = _selected(view, selected)
    index = at if at is not None else insertion_index(opts["position"], sel, view.page_count, _current(view))
    try:
        with view.document.change(_("Inserisci immagini")) as pdf:
            new, errors = images.insert_images(
                pdf, opts["paths"], index, page_size=opts["page_size"], fit=opts["fit"], margin=opts["margin"],
                landscape=opts["landscape"], recompress=opts["recompress"])
    except OperationCancelled:
        return False   # annullata dall'utente (es. avviso di Firma)
    except PageOpError as e:
        ctx.error(_("Impossibile inserire le immagini"), str(e))
        return False
    except Exception as e:  # mai chiudere l'applicazione
        ctx.error(_("Impossibile inserire le immagini"), str(e) or type(e).__name__)
        return False
    _report(ctx, len(new), errors)
    _go_to(view, new)
    return bool(new)


def _report(ctx, count: int, errors: list[str]) -> None:
    if errors:
        ctx.info(_("Avvisi"), _("Pagine inserite: {n}.").format(n=count) + "\n\n" + "\n".join(errors))
    else:
        try:
            ctx.status(_("Pagine inserite: {n}.").format(n=count))
        except Exception:
            pass


def _go_to(view, new: list[int]) -> None:
    if new:
        try:
            view.go_to_page(new[0])
        except Exception:
            pass


# =========================================================================
# PAG-09 — Inserisci dagli appunti
# =========================================================================

_KIND_LABELS = {
    images.CLIP_PDF: "Pagine PDF",
    images.CLIP_IMAGE: "Immagine",
    images.CLIP_URIS: "Elenco di file",
    images.CLIP_HTML: "Testo formattato",
    images.CLIP_TEXT: "Testo",
}


def _qimage_png(qimage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buf, "PNG")
    buf.close()
    return bytes(data.data())


def mime_snapshot(mime) -> dict:
    """Copia dei dati utili di un QMimeData (istantanea all'apertura del dialogo)."""
    snap: dict = {}
    if mime is None:
        return snap
    try:
        formats = list(mime.formats())
        if "application/pdf" in formats:
            data = bytes(mime.data("application/pdf").data())
            if data:
                snap[images.CLIP_PDF] = data
        for fmt in ("image/png", "image/jpeg", "image/bmp", "image/gif", "image/webp", "image/tiff"):
            if fmt in formats:
                data = bytes(mime.data(fmt).data())
                if data:
                    snap[images.CLIP_IMAGE] = data
                    break
        if images.CLIP_IMAGE not in snap and mime.hasImage():
            qimg = mime.imageData()
            if qimg is not None and not qimg.isNull():
                snap[images.CLIP_IMAGE] = _qimage_png(qimg)
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if paths:
                snap[images.CLIP_URIS] = paths
        if mime.hasHtml() and mime.html().strip():
            snap[images.CLIP_HTML] = mime.html()
        if mime.hasText() and mime.text().strip():
            snap[images.CLIP_TEXT] = mime.text()
    except Exception:
        pass
    return snap


def clipboard_has_supported_content() -> bool:
    """True se gli appunti contengono un tipo utilizzabile da PAG-09 (per abilitare il comando)."""
    try:
        mime = QGuiApplication.clipboard().mimeData()
    except Exception:
        return False
    if mime is None:
        return False
    try:
        formats = list(mime.formats())
        if "application/pdf" in formats or mime.hasImage() or any(f.startswith("image/") for f in formats):
            return True
        if mime.hasUrls() and any(u.isLocalFile() and os.path.isfile(u.toLocalFile()) for u in mime.urls()):
            return True
        if mime.hasHtml() and mime.html().strip():
            return True
        return bool(mime.hasText() and mime.text().strip())
    except Exception:
        return False


class PasteDialog(QDialog):
    """Anteprima del contenuto rilevato negli appunti e opzioni di inserimento."""

    def __init__(self, snapshot: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Inserisci dagli appunti"))
        self.setMinimumWidth(460)
        self.snapshot = dict(snapshot or {})
        self.kind = images.clipboard_kind(self.snapshot)
        self._pages: int | None = None
        self._errors: list[str] = []
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        card = QFrame()
        card.setObjectName("clipCard")
        card.setStyleSheet(
            f"#clipCard {{ background: {theme.hex_color('surface_alt')}; border: 1px solid {theme.hex_color('border')};"
            f" border-radius: 6px; }}")
        cl = QHBoxLayout(card)
        self.kind_icon = QLabel()
        icon_name = {images.CLIP_PDF: "file-text", images.CLIP_IMAGE: "image", images.CLIP_URIS: "files",
                     images.CLIP_HTML: "file-type", images.CLIP_TEXT: "type"}.get(self.kind, "clipboard-x")
        self.kind_icon.setPixmap(icon(icon_name, theme.tokens()["accent"]).pixmap(32, 32))
        cl.addWidget(self.kind_icon)
        texts = QVBoxLayout()
        self.title_label = QLabel(_("Contenuto rilevato"))
        self.title_label.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        self.preview_label = QLabel()
        f = self.preview_label.font()
        f.setBold(True)
        self.preview_label.setFont(f)
        self.preview_label.setWordWrap(True)
        texts.addWidget(self.title_label)
        texts.addWidget(self.preview_label)
        cl.addLayout(texts, 1)
        lay.addWidget(card)

        form = QFormLayout()
        self.size_combo = QComboBox()
        if self.kind in (images.CLIP_IMAGE, images.CLIP_URIS):
            self.size_combo.addItem(_("Come l'immagine"), None)
        for name in ("A4", "Letter"):
            self.size_combo.addItem(name, name)
        self.size_label = QLabel(_("Formato pagina"))
        form.addRow(self.size_label, self.size_combo)
        self.margin_spin = QDoubleSpinBox()
        self.margin_spin.setRange(0, 100)
        self.margin_spin.setDecimals(1)
        self.margin_spin.setSuffix(" mm")
        self.margin_label = QLabel(_("Margini"))
        form.addRow(self.margin_label, self.margin_spin)
        self.font_spin = QSpinBox()
        self.font_spin.setRange(6, 36)
        self.font_spin.setValue(11)
        self.font_spin.setSuffix(" pt")
        self.font_label = QLabel(_("Dimensione carattere"))
        form.addRow(self.font_label, self.font_spin)
        self.position_combo = _position_combo()
        form.addRow(_("Posizione"), self.position_combo)
        _uniform_fields(form)
        lay.addLayout(form)

        is_text = self.kind in (images.CLIP_TEXT, images.CLIP_HTML)
        if is_text:
            self.margin_spin.setValue(20.0)
        has_format = self.kind in (images.CLIP_IMAGE, images.CLIP_URIS, images.CLIP_TEXT, images.CLIP_HTML)
        for w in (self.size_label, self.size_combo, self.margin_label, self.margin_spin):
            w.setVisible(has_format)
        self.font_label.setVisible(is_text)
        self.font_spin.setVisible(is_text)

        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Inserisci"))
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self.refresh_preview)
        for sig in (self.size_combo.currentIndexChanged, self.margin_spin.valueChanged, self.font_spin.valueChanged):
            sig.connect(self._timer.start)
        self.refresh_preview()

    def options(self) -> dict:
        return {
            "page_size": self.size_combo.currentData(),
            "margin": self.margin_spin.value() * MM,
            "font_size": self.font_spin.value(),
            "position": self.position_combo.currentData(),
        }

    def build_pdf(self, errors: list[str] | None = None):
        """PDF risultante con le opzioni correnti (None se gli appunti non sono utilizzabili)."""
        opts = self.options()
        size = images.resolve_page_size(None, opts["page_size"], 0)
        return images.clipboard_payload_to_pdf(
            self.snapshot, page_size=size, margin=opts["margin"], text_page_size=size or PAPER_SIZES["A4"],
            text_margin=opts["margin"], font_size=opts["font_size"], errors=errors)

    def page_count(self) -> int | None:
        return self._pages

    def refresh_preview(self) -> None:
        if self.kind is None:
            self.preview_label.setText(_("Nessun contenuto supportato"))
            self.title_label.setText(_("Gli appunti non contengono pagine, immagini, file o testo."))
            self.ok_button.setEnabled(False)
            self._pages = None
            return
        label = _(_KIND_LABELS[self.kind])
        try:
            doc = self.build_pdf([])
            n = doc.page_count if doc is not None else 0
            if doc is not None:
                doc.close()
        except Exception as e:
            self._pages = None
            self.preview_label.setText(_("Anteprima non disponibile: {e}").format(e=e))
            self.ok_button.setEnabled(False)
            return
        self._pages = n
        text = (_("{tipo} — 1 pagina risultante") if n == 1 else _("{tipo} — {n} pagine risultanti"))
        self.preview_label.setText(text.format(tipo=label, n=n))
        self.ok_button.setEnabled(n > 0)


def open_paste_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-09: inserisce come nuove pagine il contenuto degli appunti (istantanea presa all'apertura)."""
    if view is None:
        return False
    try:
        snapshot = mime_snapshot(QGuiApplication.clipboard().mimeData())
    except Exception:
        snapshot = {}
    dlg = PasteDialog(snapshot, view.window() if hasattr(view, "window") else None)
    if dlg.kind is None:
        ctx.error(_("Inserisci dagli appunti"), _("Gli appunti non contengono pagine, immagini, file o testo."))
        return False
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    opts = dlg.options()
    errors: list[str] = []
    try:
        src = dlg.build_pdf(errors)
        if src is None:
            raise PageOpError(_("Gli appunti non contengono pagine, immagini, file o testo."))
        sel = _selected(view, selected)
        at = insertion_index(opts["position"], sel, view.page_count, _current(view))
        try:
            with view.document.change(_("Inserisci dagli appunti")) as pdf:
                new = insert_pdf_pages(pdf, src, at)
        finally:
            with ENGINE_LOCK:
                src.close()
    except OperationCancelled:
        return False   # annullata dall'utente (es. avviso di Firma)
    except Exception as e:
        ctx.error(_("Impossibile inserire dagli appunti"), str(e) or type(e).__name__)
        return False
    _report(ctx, len(new), errors)
    _go_to(view, new)
    return bool(new)


# =========================================================================
# PAG-13 — Estrai pagine come immagini
# =========================================================================

class ExportImagesDialog(QDialog):
    """Formato, risoluzione, colore e destinazione per l'esportazione in immagini."""

    def __init__(self, page_count: int, selected: list[int], current: int = 0, parent: QWidget | None = None, *,
                 folder: str = "", ask_directory=None):
        super().__init__(parent)
        self.setWindowTitle(_("Estrai come immagini"))
        self.setMinimumWidth(480)
        self._page_count = page_count
        self._selected = sorted(selected)
        self._current = current
        self._ask_directory = ask_directory
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.pages_combo = QComboBox()
        if self._selected:
            self.pages_combo.addItem(_("Pagine selezionate ({n})").format(n=len(self._selected)), "selected")
        else:
            self.pages_combo.addItem(_("Pagina corrente"), "current")
        self.pages_combo.addItem(_("Tutte le pagine ({n})").format(n=page_count), "all")
        form.addRow(_("Pagine"), self.pages_combo)

        self.format_combo = QComboBox()
        for text, value in (("PNG", "png"), ("JPEG", "jpeg"), ("TIFF", "tiff"), ("BMP", "bmp"), ("WebP", "webp")):
            self.format_combo.addItem(text, value)
        form.addRow(pgettext("file", "Formato"), self.format_combo)
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(10, 2400)
        self.dpi_spin.setValue(150)
        self.dpi_spin.setSuffix(" dpi")
        form.addRow(_("Risoluzione"), self.dpi_spin)
        self.color_combo = QComboBox()
        for text, value in ((_("A colori"), "rgb"), (_("Scala di grigi"), "gray"), (_("Bianco e nero"), "bw")):
            self.color_combo.addItem(text, value)
        form.addRow(_("Colore"), self.color_combo)
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(1, 100)
        self.quality_spin.setValue(90)
        form.addRow(_("Qualità JPEG/WebP"), self.quality_spin)
        self.area_combo = QComboBox()
        self.area_combo.addItem(_("Pagina visibile"), "crop")
        self.area_combo.addItem(_("Intero foglio"), "media")
        form.addRow(_("Area esportata"), self.area_combo)
        checks = QVBoxLayout()
        self.transparent_check = QCheckBox(_("Sfondo trasparente"))
        self.annots_check = QCheckBox(_("Includi annotazioni"))
        self.annots_check.setChecked(True)
        self.multipage_check = QCheckBox(_("Un unico TIFF multipagina"))
        for c in (self.annots_check, self.transparent_check, self.multipage_check):
            checks.addWidget(c)
        form.addRow("", checks)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(folder)
        self.browse_btn = QPushButton(_("Sfoglia…"))
        self.browse_btn.setIcon(icon("folder-open"))
        self.browse_btn.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.browse_btn)
        form.addRow(_("Cartella"), folder_row)
        self.template_edit = QLineEdit("{nome}_p{pagina}")
        form.addRow(_("Modello del nome"), self.template_edit)
        form.addRow("", _muted_label(_("Segnaposto: {nome} nome del documento, {pagina} numero di pagina.")))
        _uniform_fields(form)
        lay.addLayout(form)

        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Esporta"))
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)

        self.format_combo.currentIndexChanged.connect(self._update_state)
        self.color_combo.currentIndexChanged.connect(self._update_state)
        self._update_state()

    def _browse(self) -> None:
        if self._ask_directory is None:
            return
        d = self._ask_directory()
        if d:
            self.folder_edit.setText(d)

    def _update_state(self, *args) -> None:
        fmt = self.format_combo.currentData()
        self.quality_spin.setEnabled(fmt in ("jpeg", "webp"))
        can_alpha = fmt in ("png", "tiff", "webp")
        self.transparent_check.setEnabled(can_alpha)
        if not can_alpha:
            self.transparent_check.setChecked(False)
        self.multipage_check.setEnabled(fmt == "tiff")
        if fmt != "tiff":
            self.multipage_check.setChecked(False)

    def indices(self) -> list[int]:
        scope = self.pages_combo.currentData()
        if scope == "all":
            return list(range(self._page_count))
        if scope == "selected":
            return list(self._selected)
        return [self._current] if self._page_count else []

    def options(self) -> dict:
        return {
            "indices": self.indices(),
            "fmt": self.format_combo.currentData(),
            "dpi": self.dpi_spin.value(),
            "colorspace": self.color_combo.currentData(),
            "quality": self.quality_spin.value(),
            "transparent": self.transparent_check.isChecked() and self.transparent_check.isEnabled(),
            "annots": self.annots_check.isChecked(),
            "area": self.area_combo.currentData(),
            "multipage_tiff": self.multipage_check.isChecked() and self.multipage_check.isEnabled(),
            "folder": self.folder_edit.text().strip(),
            "template": self.template_edit.text().strip() or "{nome}_p{pagina}",
        }


def _export_task(progress, document, indices, folder, opts, base_name, errors):
    # export_page_images acquisisce ENGINE_LOCK pagina per pagina (rientrante).
    return images.export_page_images(
        document.pdf, indices, folder, fmt=opts["fmt"], dpi=opts["dpi"], colorspace=opts["colorspace"],
        quality=opts["quality"], transparent=opts["transparent"], annots=opts["annots"], area=opts["area"],
        multipage_tiff=opts["multipage_tiff"], template=opts["template"], base_name=base_name,
        progress=progress, errors=errors)


def open_export_images_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-13: esporta le pagine come immagini con avanzamento annullabile."""
    if view is None:
        return False
    document = view.document
    start_folder = str(document.path.parent) if getattr(document, "path", None) else ""
    dlg = ExportImagesDialog(view.page_count, _selected(view, selected), _current(view),
                             view.window() if hasattr(view, "window") else None, folder=start_folder,
                             ask_directory=lambda: ctx.ask_directory(_("Scegli la cartella di destinazione")))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    opts = dlg.options()
    folder = opts["folder"] or ctx.ask_directory(_("Scegli la cartella di destinazione"))
    if not folder:
        ctx.error(_("Estrai come immagini"), _("Indica la cartella di destinazione."))
        return False
    base_name = Path(document.display_name).stem
    errors: list[str] = []
    files = None
    for _attempt in range(2):
        try:
            files = ctx.run_task(_("Esportazione immagini…"), _export_task, document, opts["indices"], folder,
                                 opts, base_name, errors)
            break
        except images.ResolutionTooHigh as e:
            if _attempt == 0 and ctx.confirm(_("Risoluzione troppo alta"),
                                             _("{m}\n\nEsportare a {s} dpi?").format(m=e, s=e.suggested_dpi)):
                opts["dpi"] = e.suggested_dpi
                continue
            return False
        except Exception as e:
            if type(e).__name__ == "OperationCancelled":
                return False
            ctx.error(_("Impossibile esportare le immagini"), str(e) or type(e).__name__)
            return False
    if files is None:
        return False
    msg = _("Immagini create: {n} in «{d}».").format(n=len(files), d=folder)
    if errors:
        msg += "\n\n" + _("Errori:") + "\n" + "\n".join(errors)
    ctx.info(_("Esportazione completata"), msg)
    return bool(files)
