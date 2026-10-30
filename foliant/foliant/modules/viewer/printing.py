"""Stampa (VIS-43, permessi VIS-06, orientamento VIS-11, livelli VIS-20).

Il comando è nella colonna di sinistra della scheda File (File › Stampa) e nella barra di accesso
rapido, con Ctrl+P. Flusso: dialogo opzioni di Foliant con l'anteprima dei fogli → dialogo di
stampa del sistema → rendering pagina per pagina in un'attività annullabile.

L'anteprima disegna i fogli come usciranno dalla stampante (`sheets_for` e `Imposition` del
motore, gli stessi della stampa) con miniature a bassa risoluzione, generate con un timer solo
per il foglio mostrato e tenute solo in memoria.
"""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPageLayout, QPageSize, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QRadioButton, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, OperationCancelled, _, add_translations, bind_icon, locale_defaults, theme
from foliant.i18n import pgettext

from .printing_engine import (
    ACTUAL_SIZE, CONTENT_ALL, CONTENT_DOCUMENT, CONTENT_FIELDS, DUPLEX_LONG, DUPLEX_SHORT, FIT_PAGE, LAYERS_PRINT,
    LAYERS_SCREEN, LAYOUT_BOOKLET, LAYOUT_NORMAL, LAYOUT_POSTER, PAGES_ALL, PAGES_EVEN, PAGES_ODD, PAGES_PER_SHEET,
    SIMPLEX, Imposition, PrintOptions, RangeError, Sheet, describe_sheet, document_print_capability, placement,
    print_content, print_copy, print_document, parse_page_ranges, render_preview_page, require_pages, selected_pages,
    sheet_landscape, sheets_for, side_scaling,
)

try:  # PySide6-Essentials include QtPrintSupport; ripiego su PDF se mancasse
    from PySide6.QtPrintSupport import QAbstractPrintDialog, QPrintDialog, QPrinter
    HAVE_PRINT_SUPPORT = True
except ImportError:  # pragma: no cover - dipende dall'installazione
    QAbstractPrintDialog = QPrintDialog = QPrinter = None
    HAVE_PRINT_SUPPORT = False

EN = {
    "Modalità": "Mode",
    "Normale": "Normal",
    "Opuscolo (fogli piegati)": "Booklet (folded sheets)",
    "Poster (pagina divisa su più fogli)": "Poster (page split over several sheets)",
    "Rilegatura a destra": "Right-edge binding",
    "Sovrapposizione": "Overlap",
    "Due pagine per facciata in ordine di piegatura; se le pagine non sono multipli di 4 si aggiungono pagine "
    "bianche in fondo. Stampa fronte/retro sul lato corto (o rigira i fogli a mano).":
        "Two pages per side in folding order; blank pages are added at the end if the page count is not a "
        "multiple of 4. Print two-sided on the short edge (or turn the sheets over by hand).",
    "Ogni pagina a dimensione reale divisa su più fogli, con una fascia di sovrapposizione e segni di taglio.":
        "Each page at actual size split over several sheets, with an overlap band and cut marks.",
    "solo fronte": "one-sided",
    "fronte/retro sul lato corto": "two-sided on the short edge",
    "fronte/retro sul lato lungo": "two-sided on the long edge",
    "Il documento chiede la stampa {mode}.": "The document asks for {mode} printing.",
    "Pagine per foglio": "Pages on each sheet",
    "Ordine per colonne": "Column order",
    "Bordo attorno alle pagine": "Border around pages",
    "Fronte/retro": "Two-sided",
    "Come la stampante": "Printer default",
    "Solo fronte": "One-sided",
    "Sul lato lungo": "Long edge",
    "Sul lato corto": "Short edge",
    "Scala di grigi": "Grayscale",
    "Stampa…": "Print…",
    "Stampa": "Print",
    "Pagine": "Pages",
    "Tutte ({n})": "All ({n})",
    "Pagina corrente ({n})": "Current page ({n})",
    "Intervallo": "Range",
    "es. 1-3, 5, 8-": "e.g. 1-3, 5, 8-",
    "Pari o dispari": "Odd or even",
    "Tutte le pagine": "All pages",
    "Solo le pagine dispari": "Odd pages only",
    "Solo le pagine pari": "Even pages only",
    "Si applica alle pagine dell'intervallo: conta il numero di pagina del documento.":
        "Applies to the pages in the range, by document page number.",
    "Ordine inverso": "Reverse order",
    "Stampa i fogli dall'ultimo al primo": "Prints the sheets from last to first",
    "Copie": "Copies",
    "Fascicola": "Collate",
    "Dimensione": "Size",
    "stampa\x04Adatta alla pagina": "Fit to page",
    "Dimensione reale": "Actual size",
    "Opzioni": "Options",
    "Cosa stampare": "Print what",
    "Documento e commenti": "Document and comments",
    "Solo il documento": "Document only",
    "Solo campi e timbri": "Form fields and stamps only",
    "Il documento con le annotazioni e i commenti da stampare.":
        "The document with the annotations and comments marked for printing.",
    "Il documento con i valori dei campi modulo, senza commenti.":
        "The document with the form field values, without comments.",
    "Solo i valori dei campi modulo e i timbri, senza il contenuto delle pagine: per stampare su moduli "
    "prestampati.":
        "Only the form field values and the stamps, without the page content: for printing on preprinted forms.",
    "Orientamento automatico": "Automatic orientation",
    "Ruota le pagine orizzontali per adattarle al foglio": "Rotate landscape pages to fit the sheet",
    "Livelli": "Layers",
    "Impostazioni di stampa del documento": "Document print settings",
    "Come a schermo": "As shown on screen",
    "Anteprima": "Preview",
    "Anteprima di stampa": "Print preview",
    "Usa le frecce, Pagina su e Pagina giù per scorrere i fogli.":
        "Use the arrow keys, Page Up and Page Down to browse the sheets.",
    "Foglio precedente": "Previous sheet",
    "Foglio successivo": "Next sheet",
    "Foglio {n} di {t}": "Sheet {n} of {t}",
    "Foglio {n} di {t}: {d}": "Sheet {n} of {t}: {d}",
    "Nessun foglio": "No sheets",
    "Totale: {n} fogli ({c} copie)": "Total: {n} sheets ({c} copies)",
    "Fronte": "Front",
    "Retro": "Back",
    "Avanti…": "Next…",
    "Annulla": "Cancel",
    "Stampa non disponibile": "Printing not available",
    "Stampa di «{name}»": "Printing “{name}”",
    "Stampa non riuscita": "Printing failed",
    "Stampa annullata.": "Printing cancelled.",
    "Inviate alla stampante {n} pagine.": "Sent {n} pages to the printer.",
    "Il supporto di stampa di Qt non è disponibile: il documento verrà stampato su file PDF.":
        "Qt print support is not available: the document will be printed to a PDF file.",
    "Stampa su file PDF": "Print to PDF file",
    # motore
    "Indica le pagine da stampare.": "Enter the pages to print.",
    "Intervallo non valido: «{x}».": "Invalid range: “{x}”.",
    "La pagina {n} è fuori intervallo: il documento ha {t} pagine.":
        "Page {n} is out of range: the document has {t} pages.",
    "Le impostazioni di protezione del documento non consentono la stampa.":
        "The document's security settings do not allow printing.",
    "Il documento consente solo la stampa a bassa qualità ({dpi} dpi).":
        "The document only allows low-quality printing ({dpi} dpi).",
    "Nessuna pagina da stampare.": "No pages to print.",
    "L'intervallo non contiene pagine pari.": "The range contains no even pages.",
    "L'intervallo non contiene pagine dispari.": "The range contains no odd pages.",
    "facciata bianca": "blank side",
    "pagina {n}": "page {n}",
    "pagine {n}": "pages {n}",
    "fronte: {a}; retro: {b}": "front: {a}; back: {b}",
    "Impossibile avviare la stampa sul dispositivo scelto.": "Could not start printing on the chosen device.",
    "Pagina {n} di {t}": "Page {n} of {t}",
    "Invio alla stampante…": "Sending to the printer…",
}


def content_hint(content: str) -> str:
    """Spiegazione della scelta «Cosa stampare» (suggerimento della voce e della casella)."""
    return {CONTENT_ALL: _("Il documento con le annotazioni e i commenti da stampare."),
            CONTENT_DOCUMENT: _("Il documento con i valori dei campi modulo, senza commenti."),
            CONTENT_FIELDS: _("Solo i valori dei campi modulo e i timbri, senza il contenuto delle pagine: per "
                              "stampare su moduli prestampati.")}.get(content, "")


# miniature dell'anteprima: lato lungo in pixel (si sceglie il primo che basta) e quante tenerne
PREVIEW_SIZES = (96, 144, 216, 324, 486, 729, 1094, 1640)
PREVIEW_CACHE = 64
PREVIEW_PAD = 10
PREVIEW_GAP = 14


class SheetPreview(QWidget):
    """Anteprima di stampa: il foglio corrente con le opzioni scelte; in fronte/retro fronte e
    retro affiancati. Si scorre con i pulsanti del dialogo, le frecce, Pagina su/giù, Inizio,
    Fine o la rotella. Disegna con `paint_sheet`, usato anche da `sheet_image` (test)."""

    changed = Signal()      # foglio corrente o fogli cambiati

    def __init__(self, document, paper: tuple[float, float] = locale_defaults.PAPER_SIZES["A4"],
                 margins: tuple[float, float, float, float] = (0, 0, 0, 0), has_layers: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.document = document
        self.paper = (min(paper), max(paper))      # foglio verticale; l'orientamento lo decide il lavoro
        self.margins = margins
        self.has_layers = has_layers
        self.options: PrintOptions | None = None
        self.message = ""
        self.sheets: list[Sheet] = []
        self.current = 0
        self.imposition: Imposition | None = None
        self._sheet_size = self.paper
        self._area = QRectF(0, 0, *self.paper)
        self._sizes: dict[int, tuple[float, float]] = {}
        self._cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._queue: list[tuple] = []
        self._copy = None                          # copia con i livelli di stampa, se servono
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._render_step)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(240, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(_("Anteprima di stampa"))
        self.setToolTip(_("Usa le frecce, Pagina su e Pagina giù per scorrere i fogli."))

    def sizeHint(self) -> QSize:
        return QSize(320, 420)

    # --------------------------------------------------------------- fogli
    @property
    def page_count(self) -> int:
        if self.document is None or self.document.closed:
            return 0
        with ENGINE_LOCK:
            return self.document.pdf.page_count

    def _size(self, index: int) -> tuple[float, float]:
        size = self._sizes.get(index)
        if size is None:
            with ENGINE_LOCK:
                rect = self.document.pdf[index].rect
            size = self._sizes[index] = (rect.width, rect.height)
        return size

    def set_options(self, options: PrintOptions | None, message: str = "") -> None:
        """Ricalcola i fogli; senza opzioni valide mostra `message` al posto del foglio."""
        self.options, self.message = options, message
        self.sheets, self.imposition = [], None
        count = self.page_count
        if options is not None and count:
            pages = selected_pages(options, count)
            w, h = self.paper
            if pages and sheet_landscape(options, *self._size(pages[0])):
                w, h = h, w
            left, top, right, bottom = self.margins
            self._sheet_size = (w, h)
            self._area = QRectF(left, top, max(1.0, w - left - right), max(1.0, h - top - bottom))
            self.imposition = Imposition(options, self._size, self._area.width(), self._area.height(), count)
            self.sheets = sheets_for(options, count, self.imposition.tile_count)
        self.current = min(self.current, max(0, len(self.sheets) - 1))
        self._sheet_changed()

    def go_to(self, n: int) -> None:
        n = max(0, min(n, len(self.sheets) - 1))
        if n != self.current and self.sheets:
            self.current = n
            self._sheet_changed()

    def next_sheet(self) -> None:
        self.go_to(self.current + 1)

    def previous_sheet(self) -> None:
        self.go_to(self.current - 1)

    def position_text(self) -> str:
        if not self.sheets:
            return _("Nessun foglio")
        return _("Foglio {n} di {t}").format(n=self.current + 1, t=len(self.sheets))

    def _sheet_changed(self) -> None:
        self._queue.clear()                    # il disegno chiede di nuovo solo ciò che serve ora
        if self.sheets:
            self.setAccessibleDescription(_("Foglio {n} di {t}: {d}").format(
                n=self.current + 1, t=len(self.sheets), d=describe_sheet(self.sheets[self.current])))
        else:
            self.setAccessibleDescription(self.message or _("Nessun foglio"))
        self.changed.emit()
        self.update()

    # ------------------------------------------------------------ tastiera
    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown):
            self.next_sheet()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self.previous_sheet()
        elif key == Qt.Key.Key_Home:
            self.go_to(0)
        elif key == Qt.Key.Key_End:
            self.go_to(len(self.sheets) - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta < 0:
            self.next_sheet()
        elif delta > 0:
            self.previous_sheet()
        event.accept()

    # -------------------------------------------------------------- disegno
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        try:
            self.paint_sheet(painter, QRectF(self.rect()), self.devicePixelRatioF() or 1.0)
        finally:
            painter.end()

    def sheet_image(self, width: int = 320, height: int = 420) -> QImage:
        """Il foglio corrente in un'immagine, con tutte le miniature generate (test e catture)."""
        for _round in range(4):
            image = QImage(width, height, QImage.Format.Format_RGB32)
            painter = QPainter(image)
            self.paint_sheet(painter, QRectF(0, 0, width, height), 1.0)
            painter.end()
            if not self._queue:
                return image
            self.flush()
        return image

    def paint_sheet(self, p: QPainter, rect: QRectF, dpr: float) -> None:
        p.fillRect(rect, theme.color("canvas"))
        if not self.sheets or self.options is None:
            p.setPen(theme.color("text_muted"))
            p.drawText(rect.adjusted(PREVIEW_PAD, PREVIEW_PAD, -PREVIEW_PAD, -PREVIEW_PAD),
                       Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message or _("Nessun foglio"))
            return
        sides = self.sheets[self.current].sides
        k = len(sides)
        label_h = p.fontMetrics().height() + 6 if k > 1 else 0
        avail = rect.adjusted(PREVIEW_PAD, PREVIEW_PAD, -PREVIEW_PAD, -PREVIEW_PAD - label_h)
        pw, ph = self._sheet_size
        scale = max(0.01, min((avail.width() - PREVIEW_GAP * (k - 1)) / (k * pw), avail.height() / ph))
        x0 = avail.x() + (avail.width() - (k * pw * scale + PREVIEW_GAP * (k - 1))) / 2
        y0 = avail.y() + (avail.height() - ph * scale) / 2
        for j, side in enumerate(sides):
            paper = QRectF(x0 + j * (pw * scale + PREVIEW_GAP), y0, pw * scale, ph * scale)
            p.fillRect(paper.translated(2, 2), theme.color("page_shadow"))
            p.fillRect(paper, QColor("white"))
            if not side.blank:
                self._paint_side(p, side, paper, scale, dpr)
            p.setPen(QPen(theme.color("border"), 1))
            p.drawRect(paper)
            if k > 1:
                p.setPen(theme.color("text_muted"))
                p.drawText(QRectF(paper.x(), paper.bottom() + 3, paper.width(), label_h),
                           Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                           _("Fronte") if j == 0 else _("Retro"))

    def _paint_side(self, p: QPainter, side, paper: QRectF, scale: float, dpr: float) -> None:
        """La facciata sul foglio come in `print_document`: area stampabile, scala, rotazione."""
        lay = self.imposition.side(side)
        scaling, auto_rotate = side_scaling(self.options)
        area = self._area
        place = placement(lay.width, lay.height, area.width(), area.height(), scaling, auto_rotate)
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.translate(paper.topLeft())
        p.scale(scale, scale)
        p.translate(area.topLeft())
        p.setClipRect(QRectF(0, 0, area.width(), area.height()))
        target = QRectF(place.x, place.y, place.width, place.height)
        if place.rotate:                       # 90° in senso orario attorno al centro, come in stampa
            p.translate(target.center())
            p.rotate(90)
            p.translate(-target.height() / 2, -target.width() / 2)
        else:
            p.translate(target.topLeft())
        p.scale(place.scale, place.scale)
        zoom = scale * place.scale * dpr       # pixel per punto della facciata
        for index, dest, clip in lay.items:
            w, h = self._size(index)
            part = clip.width if clip is not None else w      # il poster mostra una parte della pagina
            image = self._image(index, max(w, h) * dest.width / part * zoom if part else 0)
            box = QRectF(dest.x0, dest.y0, dest.width, dest.height)
            if image is None:
                p.fillRect(box, theme.color("surface_alt"))
                continue
            sx, sy = image.width() / w, image.height() / h
            source = (QRectF(clip.x0 * sx, clip.y0 * sy, clip.width * sx, clip.height * sy) if clip is not None
                      else QRectF(0, 0, image.width(), image.height()))
            p.drawImage(box, image, source)
        pen = QPen(QColor("black"), 0)          # tratto sottile a ogni scala
        p.setPen(pen)
        for frame in lay.frames:
            p.drawRect(QRectF(frame.x0, frame.y0, frame.width, frame.height))
        for a, b in lay.marks:
            p.drawLine(QPointF(a.x, a.y), QPointF(b.x, b.y))
        p.restore()

    # ---------------------------------------------------------- miniature
    def _source_key(self) -> str:
        return "print" if self.has_layers and self.options.layers == LAYERS_PRINT else "doc"

    def _image(self, index: int, px: float) -> QImage | None:
        """Miniatura della pagina per `px` pixel sul lato lungo (la prima misura che basta); se
        manca la si chiede al timer e intanto si usa quella di altra misura più vicina, se c'è
        (None: segnaposto). Ridurre molto un'immagine grande perderebbe le linee sottili."""
        want = next((s for s in PREVIEW_SIZES if s >= px), PREVIEW_SIZES[-1])
        base = (self._source_key(), index, print_content(self.options))
        found = self._cache.get(base + (want,))
        if found is None:
            self._request(base + (want,))
            near = sorted((s for s in PREVIEW_SIZES if base + (s,) in self._cache), key=lambda s: abs(s - want))
            found = self._cache[base + (near[0],)] if near else None
        else:
            self._cache.move_to_end(base + (want,))
        if found is not None and self.options.grayscale:
            gray = self._cache.get(base + ("gray", found.cacheKey()))
            if gray is None:
                gray = found.convertToFormat(QImage.Format.Format_Grayscale8)
                self._store(base + ("gray", found.cacheKey()), gray)
            return gray
        return found

    def _request(self, key: tuple) -> None:
        if key in self._cache:
            return
        if key not in self._queue:
            self._queue.append(key)
        if not self._timer.isActive():
            self._timer.start(0)

    def _store(self, key: tuple, image: QImage) -> None:
        self._cache[key] = image
        while len(self._cache) > PREVIEW_CACHE:
            self._cache.popitem(last=False)

    def _render(self, key: tuple) -> None:
        source, index, content, size = key
        if key in self._cache or self.document is None or self.document.closed:
            return
        try:
            if source == "print":
                if self._copy is None:
                    self._copy = print_copy(self.document, LAYERS_PRINT)
                pdf = self._copy
            else:
                pdf = self.document.pdf
            w, h = self._size(index)
            image = render_preview_page(pdf, index, size / max(w, h, 1) * 72, content)
        except Exception:  # noqa: BLE001 - pagina non leggibile: foglio bianco nell'anteprima
            image = QImage(1, 1, QImage.Format.Format_RGB32)
            image.fill(QColor("white"))
        self._store(key, image)

    def _render_step(self) -> None:
        if not self._queue:
            return
        if not ENGINE_LOCK.acquire(blocking=False):
            self._timer.start(40)              # il motore è occupato: si riprova senza bloccare
            return
        try:
            self._render(self._queue.pop(0))
        finally:
            ENGINE_LOCK.release()
        self.update()
        if self._queue:
            self._timer.start(0)

    def flush(self) -> None:
        """Genera subito le miniature in attesa (test e catture)."""
        while self._queue:
            with ENGINE_LOCK:
                self._render(self._queue.pop(0))
        self._timer.stop()
        self.update()

    def rendered_pages(self) -> set[int]:
        """Pagine di cui esiste una miniatura (test: generazione pigra)."""
        return {key[1] for key in self._cache if len(key) == 4}

    def release(self) -> None:
        """Libera miniature e copia di lavoro (alla chiusura del dialogo)."""
        self._timer.stop()
        self._queue.clear()
        self._cache.clear()
        if self._copy is not None:
            with ENGINE_LOCK:
                self._copy.close()
            self._copy = None


class PrintOptionsDialog(QDialog):
    """Opzioni di stampa di Foliant (prima del dialogo del sistema), con l'anteprima dei fogli."""

    def __init__(self, parent: QWidget | None, page_count: int, current_page: int,
                 has_layers: bool = False, quality_note: str = "", document=None,
                 paper: tuple[float, float] | None = None, margins: tuple[float, float, float, float] = (0, 0, 0, 0)):
        super().__init__(parent)
        self.setWindowTitle(_("Stampa"))
        self.setObjectName("printOptionsDialog")
        self.page_count = page_count
        self.current_page = current_page
        self.result_options: PrintOptions | None = None

        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        columns = QHBoxLayout()
        columns.setSpacing(14)
        outer.addLayout(columns, 1)
        self.left = QWidget(self)
        self.left.setMinimumWidth(380)
        root = QVBoxLayout(self.left)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        columns.addWidget(self.left)

        pages_box = QGroupBox(_("Pagine"), self.left)
        pl = QVBoxLayout(pages_box)
        self.rb_all = QRadioButton(_("Tutte ({n})").format(n=page_count), pages_box)
        self.rb_current = QRadioButton(_("Pagina corrente ({n})").format(n=current_page + 1), pages_box)
        self.rb_range = QRadioButton(_("Intervallo"), pages_box)
        self.range_edit = QLineEdit(pages_box)
        self.range_edit.setPlaceholderText(_("es. 1-3, 5, 8-"))
        self.range_edit.setAccessibleName(_("Intervallo"))
        self.range_edit.textEdited.connect(lambda _t: self.rb_range.setChecked(True))
        self.range_edit.textEdited.connect(lambda _t: self.error_label.hide())
        self.rb_all.setChecked(True)
        self._page_group = QButtonGroup(self)
        for rb in (self.rb_all, self.rb_current, self.rb_range):
            self._page_group.addButton(rb)
        pl.addWidget(self.rb_all)
        pl.addWidget(self.rb_current)
        row = QHBoxLayout()
        row.addWidget(self.rb_range)
        row.addWidget(self.range_edit, 1)
        pl.addLayout(row)
        # pari/dispari e ordine inverso valgono dopo l'intervallo, qualunque sia
        self.page_set = QComboBox(pages_box)
        self.page_set.addItem(_("Tutte le pagine"), PAGES_ALL)
        self.page_set.addItem(_("Solo le pagine dispari"), PAGES_ODD)
        self.page_set.addItem(_("Solo le pagine pari"), PAGES_EVEN)
        self.page_set.setToolTip(_("Si applica alle pagine dell'intervallo: conta il numero di pagina del documento."))
        self.page_set_label = QLabel(_("Pari o dispari"), pages_box)
        self.page_set_label.setBuddy(self.page_set)
        self.page_set.setAccessibleName(_("Pari o dispari"))
        set_row = QHBoxLayout()
        set_row.addWidget(self.page_set_label)
        set_row.addWidget(self.page_set)
        set_row.addStretch(1)
        pl.addLayout(set_row)
        self.reverse = QCheckBox(_("Ordine inverso"), pages_box)
        self.reverse.setToolTip(_("Stampa i fogli dall'ultimo al primo"))
        pl.addWidget(self.reverse)
        self.error_label = QLabel(pages_box)
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error_label.hide()
        pl.addWidget(self.error_label)
        root.addWidget(pages_box)

        mode_box = QGroupBox(_("Modalità"), self.left)
        ml = QVBoxLayout(mode_box)       # non QFormLayout: il testo d'aiuto va a capo correttamente
        self.layout_mode = QComboBox(mode_box)
        self.layout_mode.addItem(_("Normale"), LAYOUT_NORMAL)
        self.layout_mode.addItem(_("Opuscolo (fogli piegati)"), LAYOUT_BOOKLET)
        self.layout_mode.addItem(_("Poster (pagina divisa su più fogli)"), LAYOUT_POSTER)
        self.layout_mode.setAccessibleName(_("Modalità"))
        ml.addWidget(self.layout_mode)
        self.nup = QComboBox(mode_box)
        for n in PAGES_PER_SHEET:
            self.nup.addItem(str(n), n)
        self.nup_label = QLabel(_("Pagine per foglio"), mode_box)
        self.nup_label.setBuddy(self.nup)
        self.nup.setAccessibleName(_("Pagine per foglio"))
        self.nup_columns = QCheckBox(_("Ordine per colonne"), mode_box)
        self.nup_border = QCheckBox(_("Bordo attorno alle pagine"), mode_box)
        nup_row = QHBoxLayout()
        nup_row.addWidget(self.nup_label)
        nup_row.addWidget(self.nup)
        nup_row.addStretch(1)
        ml.addLayout(nup_row)
        ml.addWidget(self.nup_columns)
        ml.addWidget(self.nup_border)
        self.nup.currentIndexChanged.connect(self._on_layout_mode)
        self.booklet_rtl = QCheckBox(_("Rilegatura a destra"), mode_box)
        ml.addWidget(self.booklet_rtl)
        self.overlap = QDoubleSpinBox(mode_box)
        self.overlap.setRange(0, 50)
        self.overlap.setDecimals(1)
        self.overlap.setValue(10)
        self.overlap.setSuffix(" mm")
        self.overlap_label = QLabel(_("Sovrapposizione"), mode_box)
        self.overlap_label.setBuddy(self.overlap)
        self.overlap.setAccessibleName(_("Sovrapposizione"))
        overlap_row = QHBoxLayout()
        overlap_row.addWidget(self.overlap_label)
        overlap_row.addWidget(self.overlap)
        overlap_row.addStretch(1)
        ml.addLayout(overlap_row)
        self.mode_hint = QLabel(mode_box)
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setProperty("muted", True)
        ml.addWidget(self.mode_hint)
        self.layout_mode.currentIndexChanged.connect(self._on_layout_mode)
        root.addWidget(mode_box)

        size_box = QGroupBox(_("Dimensione"), self.left)
        self.size_box = size_box
        sl = QVBoxLayout(size_box)
        self.rb_fit = QRadioButton(pgettext("stampa", "Adatta alla pagina"), size_box)
        self.rb_actual = QRadioButton(_("Dimensione reale"), size_box)
        self.rb_fit.setChecked(True)
        sl.addWidget(self.rb_fit)
        sl.addWidget(self.rb_actual)
        root.addWidget(size_box)

        opt_box = QGroupBox(_("Opzioni"), self.left)
        form = QFormLayout(opt_box)
        copies_row = QHBoxLayout()
        self.copies = QSpinBox(opt_box)
        self.copies.setRange(1, 999)
        self.copies.setAccessibleName(_("Copie"))
        self.collate = QCheckBox(_("Fascicola"), opt_box)
        self.collate.setChecked(True)
        copies_row.addWidget(self.copies)
        copies_row.addWidget(self.collate)
        copies_row.addStretch(1)
        copies_label = QLabel(_("Copie"), opt_box)
        copies_label.setBuddy(self.copies)
        form.addRow(copies_label, copies_row)
        self.content = QComboBox(opt_box)
        for text, value in ((_("Documento e commenti"), CONTENT_ALL), (_("Solo il documento"), CONTENT_DOCUMENT),
                            (_("Solo campi e timbri"), CONTENT_FIELDS)):
            self.content.addItem(text, value)
            self.content.setItemData(self.content.count() - 1, content_hint(value), Qt.ItemDataRole.ToolTipRole)
        self.content_label = QLabel(_("Cosa stampare"), opt_box)
        self.content_label.setBuddy(self.content)
        self.content.setAccessibleName(_("Cosa stampare"))
        self.content.currentIndexChanged.connect(
            lambda _i: self.content.setToolTip(content_hint(self.content.currentData())))
        self.content.setToolTip(content_hint(CONTENT_ALL))
        form.addRow(self.content_label, self.content)
        self.auto_rotate = QCheckBox(_("Orientamento automatico"), opt_box)
        self.auto_rotate.setToolTip(_("Ruota le pagine orizzontali per adattarle al foglio"))
        self.auto_rotate.setChecked(True)
        form.addRow(self.auto_rotate)
        self.layers = QComboBox(opt_box)
        self.layers.addItem(_("Impostazioni di stampa del documento"), LAYERS_PRINT)
        self.layers.addItem(_("Come a schermo"), LAYERS_SCREEN)
        self.layers_label = QLabel(_("Livelli"), opt_box)
        self.layers_label.setBuddy(self.layers)
        self.layers.setAccessibleName(_("Livelli"))
        form.addRow(self.layers_label, self.layers)
        self.layers.setVisible(has_layers)
        self.layers_label.setVisible(has_layers)
        self.duplex_combo = QComboBox(opt_box)
        for text, value in ((_("Come la stampante"), ""), (_("Solo fronte"), SIMPLEX),
                            (_("Sul lato lungo"), DUPLEX_LONG), (_("Sul lato corto"), DUPLEX_SHORT)):
            self.duplex_combo.addItem(text, value)
        duplex_label = QLabel(_("Fronte/retro"), opt_box)
        duplex_label.setBuddy(self.duplex_combo)
        self.duplex_combo.setAccessibleName(_("Fronte/retro"))
        form.addRow(duplex_label, self.duplex_combo)
        self.grayscale = QCheckBox(_("Scala di grigi"), opt_box)
        form.addRow(self.grayscale)
        root.addWidget(opt_box)

        self.duplex_note = QLabel(self.left)
        self.duplex_note.setWordWrap(True)
        self.duplex_note.setProperty("muted", True)
        self.duplex_note.hide()
        root.addWidget(self.duplex_note)

        self.note = QLabel(quality_note, self.left)
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.hex_color('warning')};")
        self.note.setVisible(bool(quality_note))
        root.addWidget(self.note)
        root.addStretch(1)

        # anteprima: fogli come usciranno dalla stampante, aggiornata a ogni opzione
        preview_box = QGroupBox(_("Anteprima"), self)
        vl = QVBoxLayout(preview_box)
        self.preview = SheetPreview(document, paper or locale_defaults.PAPER_SIZES["A4"], margins, has_layers,
                                    preview_box)
        vl.addWidget(self.preview, 1)
        nav = QHBoxLayout()
        self.prev_button = QToolButton(preview_box)
        self.next_button = QToolButton(preview_box)
        for button, name, text, slot in ((self.prev_button, "chevron-left", _("Foglio precedente"),
                                          self.preview.previous_sheet),
                                         (self.next_button, "chevron-right", _("Foglio successivo"),
                                          self.preview.next_sheet)):
            bind_icon(button, name)
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setAutoRepeat(True)
            button.clicked.connect(slot)
        self.sheet_label = QLabel(preview_box)
        self.sheet_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sheet_label.setMinimumWidth(110)
        nav.addStretch(1)
        nav.addWidget(self.prev_button)
        nav.addWidget(self.sheet_label)
        nav.addWidget(self.next_button)
        nav.addStretch(1)
        vl.addLayout(nav)
        self.total_label = QLabel(preview_box)
        self.total_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.total_label.setProperty("muted", True)
        vl.addWidget(self.total_label)
        self.preview.changed.connect(self._update_navigation)
        columns.addWidget(preview_box, 1)

        buttons = QDialogButtonBox(self)
        self.ok_button = buttons.addButton(_("Avanti…"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        self.ok_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.setMinimumWidth(740)
        # ogni opzione aggiorna l'anteprima (il campo dell'intervallo dopo una breve pausa)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(150)
        self._preview_timer.timeout.connect(self.refresh_preview)
        for signal in (self._page_group.buttonToggled, self.range_edit.textChanged,
                       self.page_set.currentIndexChanged, self.reverse.toggled, self.layout_mode.currentIndexChanged,
                       self.nup.currentIndexChanged, self.nup_columns.toggled, self.nup_border.toggled,
                       self.booklet_rtl.toggled, self.overlap.valueChanged, self.rb_fit.toggled,
                       self.copies.valueChanged, self.collate.toggled, self.content.currentIndexChanged,
                       self.auto_rotate.toggled, self.layers.currentIndexChanged,
                       self.duplex_combo.currentIndexChanged, self.grayscale.toggled):
            signal.connect(lambda *_a: self._preview_timer.start())
        self._on_layout_mode()
        self.refresh_preview()

    def _on_layout_mode(self, *_a) -> None:
        mode = self.layout_mode.currentData()
        normal = mode == LAYOUT_NORMAL
        for w in (self.nup, self.nup_label):
            w.setVisible(normal)
        multi = normal and (self.nup.currentData() or 1) > 1
        self.nup_columns.setVisible(multi)
        self.nup_border.setVisible(multi)
        self.booklet_rtl.setVisible(mode == LAYOUT_BOOKLET)
        self.overlap.setVisible(mode == LAYOUT_POSTER)
        self.overlap_label.setVisible(mode == LAYOUT_POSTER)
        self.size_box.setEnabled(normal and not multi)
        self.auto_rotate.setEnabled(normal and not multi)
        hints = {LAYOUT_BOOKLET: _("Due pagine per facciata in ordine di piegatura; se le pagine non sono multipli "
                                   "di 4 si aggiungono pagine bianche in fondo. Stampa fronte/retro sul lato corto "
                                   "(o rigira i fogli a mano)."),
                 LAYOUT_POSTER: _("Ogni pagina a dimensione reale divisa su più fogli, con una fascia di "
                                  "sovrapposizione e segni di taglio.")}
        self.mode_hint.setText(hints.get(mode, ""))
        self.mode_hint.setVisible(mode in hints)
        # altezza del testo a capo calcolata sulla larghezza disponibile (i layout di Qt la
        # sottostimano per le etichette con a capo), poi il dialogo si adatta
        width = max(self.mode_hint.width(), self.left.minimumWidth() - 40)
        self.mode_hint.setMinimumHeight(self.mode_hint.heightForWidth(width) if mode in hints else 0)
        self.adjustSize()

    def apply_document_preferences(self, prefs) -> None:
        """Preselezione dalle preferenze di stampa del documento (VIS-42): scala, copie,
        fronte/retro (passato al dialogo di stampa del sistema)."""
        if prefs is None:
            return
        if prefs.print_scaling_none:
            self.rb_actual.setChecked(True)
        if prefs.num_copies > 1:
            self.copies.setValue(prefs.num_copies)
        if prefs.duplex:
            self.duplex_combo.setCurrentIndex(max(0, self.duplex_combo.findData(prefs.duplex)))
            labels = {SIMPLEX: _("solo fronte"), DUPLEX_SHORT: _("fronte/retro sul lato corto"),
                      DUPLEX_LONG: _("fronte/retro sul lato lungo")}
            self.duplex_note.setText(_("Il documento chiede la stampa {mode}.").format(mode=labels[prefs.duplex]))
            self.duplex_note.show()
        self.refresh_preview()

    def options(self) -> PrintOptions:
        """Opzioni scelte; solleva RangeError se l'intervallo non è valido o, con pari/dispari,
        non resta nessuna pagina."""
        if self.rb_current.isChecked():
            pages = [self.current_page]
        elif self.rb_range.isChecked():
            pages = parse_page_ranges(self.range_edit.text(), self.page_count)
        else:
            pages = list(range(self.page_count))
        content = self.content.currentData() or CONTENT_ALL
        options = PrintOptions(
            pages=pages,
            copies=self.copies.value(),
            collate=self.collate.isChecked(),
            scaling=FIT_PAGE if self.rb_fit.isChecked() else ACTUAL_SIZE,
            annotations=content == CONTENT_ALL,
            auto_rotate=self.auto_rotate.isChecked(),
            layers=self.layers.currentData() or LAYERS_PRINT,
            duplex=self.duplex_combo.currentData() or "",
            layout=self.layout_mode.currentData() or LAYOUT_NORMAL,
            booklet_rtl=self.booklet_rtl.isChecked(),
            poster_overlap_mm=self.overlap.value(),
            pages_per_sheet=self.nup.currentData() or 1,
            nup_by_columns=self.nup_columns.isChecked(),
            nup_border=self.nup_border.isChecked(),
            grayscale=self.grayscale.isChecked(),
            page_set=self.page_set.currentData() or PAGES_ALL,
            reverse=self.reverse.isChecked(),
            content=content,
        )
        require_pages(options, self.page_count)
        return options

    def refresh_preview(self) -> None:
        """Ricalcola i fogli dell'anteprima con le opzioni correnti."""
        self._preview_timer.stop()
        try:
            options = self.options()
        except RangeError as e:
            self.preview.set_options(None, str(e))
            return
        self.preview.set_options(options)

    def _update_navigation(self) -> None:
        preview = self.preview
        count = len(preview.sheets)
        self.sheet_label.setText(preview.position_text())
        self.prev_button.setEnabled(preview.current > 0)
        self.next_button.setEnabled(preview.current < count - 1)
        copies = preview.options.copies if preview.options is not None else 1
        self.total_label.setText(_("Totale: {n} fogli ({c} copie)").format(n=count * copies, c=copies)
                                 if count and copies > 1 else "")
        self.total_label.setVisible(bool(self.total_label.text()))

    def accept(self) -> None:
        try:
            self.result_options = self.options()
        except RangeError as e:
            self.error_label.setText(str(e))
            self.error_label.show()
            (self.range_edit if self.rb_range.isChecked() else self.page_set).setFocus()
            return
        super().accept()

    def done(self, result: int) -> None:
        self.preview.release()
        super().done(result)


def preview_paper(ctx) -> tuple[tuple[float, float], tuple[float, float, float, float]]:
    """Carta e margini dell'anteprima (punti): quelli della stampante predefinita; senza il
    supporto di stampa di Qt il formato delle preferenze (A4 o Letter), senza margini."""
    if HAVE_PRINT_SUPPORT:
        try:
            layout = QPrinter(QPrinter.PrinterMode.HighResolution).pageLayout()
            size = layout.pageSize().size(QPageSize.Unit.Point)
            m = layout.margins(QPageLayout.Unit.Point)
            if size.width() > 0 and size.height() > 0:
                return (size.width(), size.height()), (m.left(), m.top(), m.right(), m.bottom())
        except Exception:  # noqa: BLE001 - stampante non interrogabile: formato delle preferenze
            pass
    sizes = locale_defaults.PAPER_SIZES
    return sizes.get(ctx.paper_size(), sizes["A4"]), (0.0, 0.0, 0.0, 0.0)


def printer_duplex(printer, current: str) -> str:
    """Fronte/retro scelto nel dialogo del sistema (valori di /Duplex): accoppia le facciate
    dei fogli come le stamperà la stampante. Per un file PDF (che non ha fronte/retro) e con
    «automatico» resta la scelta fatta in Foliant, quella mostrata nell'anteprima."""
    if printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
        return current
    modes = {QPrinter.DuplexMode.DuplexNone: SIMPLEX, QPrinter.DuplexMode.DuplexLongSide: DUPLEX_LONG,
             QPrinter.DuplexMode.DuplexShortSide: DUPLEX_SHORT}
    return modes.get(printer.duplex(), current)


class PrintController:
    def __init__(self, ctx):
        self.ctx = ctx
        self.action = ctx.action(_("Stampa…"), "printer", self.print_current, shortcut="Ctrl+P",
                                 requires_document=False)
        self._base_tooltip = self.action.toolTip()
        # Stampa come in Excel: comando della colonna di sinistra della scheda File e pulsante della
        # barra di accesso rapido (non più nella barra Leggi)
        ctx.add_file_action(self.action, "stampa")
        ctx.add_quick_access_action(self.action)
        ctx.currentViewChanged.connect(lambda _v: self.update_state())
        ctx.documentChanged.connect(lambda _v: self.update_state())
        ctx.documentAccessChanged.connect(lambda _v: self.update_state())   # sblocco/blocco (SEC-02)
        self.update_state()

    # ------------------------------------------------------------------ stato
    def update_state(self) -> None:
        view = self.ctx.current_view()
        if view is None or view.document.closed:
            self.action.setEnabled(False)
            self.action.setToolTip(self._base_tooltip)
            return
        cap = document_print_capability(view.document)
        self.action.setEnabled(cap.allowed)
        self.action.setToolTip(f"{self._base_tooltip}\n{cap.reason}" if cap.reason else self._base_tooltip)

    # ----------------------------------------------------------------- dialoghi
    def ask_options(self, view) -> PrintOptions | None:
        """Mostra il dialogo opzioni; None se annullato (sostituibile nei test)."""
        dlg = self.options_dialog(view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dlg.result_options

    def options_dialog(self, view) -> PrintOptionsDialog:
        cap = document_print_capability(view.document)
        with ENGINE_LOCK:
            try:
                has_layers = bool(view.document.pdf.get_ocgs())
            except Exception:
                has_layers = False
        paper, margins = preview_paper(self.ctx)
        dlg = PrintOptionsDialog(self.ctx.window, view.page_count, view.current_page, has_layers,
                                 cap.reason if cap.allowed else "", document=view.document, paper=paper,
                                 margins=margins)
        from .session_engine import read_open_options
        try:
            with ENGINE_LOCK:
                prefs = read_open_options(view.document.pdf)
        except Exception:  # noqa: BLE001
            prefs = None
        dlg.apply_document_preferences(prefs)
        return dlg

    def ask_printer(self, view, options: PrintOptions):
        """Dialogo di stampa del sistema. Restituisce il dispositivo o None."""
        if not HAVE_PRINT_SUPPORT:
            from PySide6.QtGui import QPdfWriter
            self.ctx.info(_("Stampa"), _("Il supporto di stampa di Qt non è disponibile: "
                                         "il documento verrà stampato su file PDF."))
            path = self.ctx.ask_save_path(_("Stampa su file PDF"), view.document.display_name)
            return QPdfWriter(path) if path else None
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        configure_printer(printer, view.document, options)
        dlg = QPrintDialog(printer, self.ctx.window)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintPageRange, False)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintCurrentPage, False)
        dlg.setOption(QAbstractPrintDialog.PrintDialogOption.PrintSelection, False)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return printer

    # ------------------------------------------------------------------ stampa
    def print_current(self) -> int:
        view = self.ctx.current_view()
        if view is None:
            return 0
        cap = document_print_capability(view.document)
        if not cap.allowed:
            self.ctx.error(_("Stampa non disponibile"), cap.reason)
            return 0
        options = self.ask_options(view)
        if options is None:
            return 0
        options.max_dpi = cap.max_dpi
        device = self.ask_printer(view, options)
        if device is None:
            return 0
        if HAVE_PRINT_SUPPORT and isinstance(device, QPrinter):
            # le copie le genera Foliant (stesso risultato su ogni stampante), con i fogli
            # fronte/retro accoppiati come li stamperà la stampante
            options.copies = max(options.copies, device.copyCount())
            options.collate = device.collateCopies()
            options.duplex = printer_duplex(device, options.duplex)
            device.setCopyCount(1)
        return self.print_to(view.document, device, options)

    def print_to(self, document, device, options: PrintOptions) -> int:
        """Stampa su un dispositivo già configurato, con avanzamento annullabile."""
        try:
            n = self.ctx.run_task(_("Stampa di «{name}»").format(name=document.display_name),
                                  lambda progress: print_document(document, device, options, progress))
        except OperationCancelled:
            self.ctx.status(_("Stampa annullata."))
            return 0
        except Exception as e:  # noqa: BLE001 - mai chiudere l'applicazione
            self.ctx.error(_("Stampa non riuscita"), str(e))
            return 0
        n = int(n or 0)
        self.ctx.status(_("Inviate alla stampante {n} pagine.").format(n=n))
        return n


def configure_printer(printer, document, options: PrintOptions) -> None:
    """Parametri del lavoro di stampa dalle opzioni: copie, fronte/retro, colore, orientamento."""
    printer.setDocName(document.display_name)
    printer.setCopyCount(options.copies)
    printer.setCollateCopies(options.collate)
    duplex = {SIMPLEX: QPrinter.DuplexMode.DuplexNone,
              DUPLEX_SHORT: QPrinter.DuplexMode.DuplexShortSide,
              DUPLEX_LONG: QPrinter.DuplexMode.DuplexLongSide}.get(options.duplex)
    if duplex is not None:
        printer.setDuplex(duplex)
    printer.setColorMode(QPrinter.ColorMode.GrayScale if options.grayscale else QPrinter.ColorMode.Color)
    pages = selected_pages(options) or options.pages
    first = pages[0] if pages else 0
    with ENGINE_LOCK:  # orientamento del documento, non della vista (VIS-11)
        rect = document.pdf[first].rect
    landscape = sheet_landscape(options, rect.width, rect.height)
    if landscape is not None:
        printer.setPageOrientation(QPageLayout.Orientation.Landscape if landscape
                                   else QPageLayout.Orientation.Portrait)


def install(ctx) -> PrintController:
    add_translations("en", EN)
    return PrintController(ctx)
