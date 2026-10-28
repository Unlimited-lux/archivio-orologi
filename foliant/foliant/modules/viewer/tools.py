"""Strumenti della modalità Leggi: Zoom area (VIS-10) e Istantanea (VIS-29)."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QActionGroup, QGuiApplication, QImage, QPen
from PySide6.QtWidgets import QMenu

from PySide6.QtCore import QEvent, QObject

from foliant.api import (
    ENGINE_LOCK, Context, DocumentView, TextSelection, Tool, _, add_translations, theme, words_to_text,
)

TRANSLATIONS = {
    "Lente": "Magnifier",
    "Ingrandisce la zona sotto il cursore": "Magnifies the area under the cursor",
    "Zoom area": "Zoom to area",
    "Traccia un rettangolo per ingrandirlo a tutta la vista": "Drag a rectangle to fill the view with it",
    "Cattura area": "Capture area",
    "Cattura un'area come immagine o testo": "Capture an area as image or text",
    "Copia immagine": "Copy image",
    "Salva immagine…": "Save image…",
    "Copia testo": "Copy text",
    "Risoluzione": "Resolution",
    "Come lo schermo": "Same as screen",
    "{n} dpi": "{n} dpi",
    "Salva immagine": "Save image",
    "Immagine copiata ({w} × {h} px)": "Image copied ({w} × {h} px)",
    "Immagine salvata: {p}": "Image saved: {p}",
    "Testo copiato": "Text copied",
    "Nessun testo nell'area: per le scansioni usa l'OCR.": "No text in the area: use OCR for scans.",
    "Area troppo grande: la risoluzione è stata ridotta a {n} dpi.": "Area too large: resolution reduced to {n} dpi.",
    "Il documento non consente la copia del contenuto.": "The document does not allow copying its content.",
    "Impossibile salvare l'immagine.": "Could not save the image.",
    "Cattura area non disponibile: il documento non consente la copia del contenuto.":
        "Snapshot unavailable: the document does not allow copying its content.",
}

MAX_PIXELS = 100_000_000
DPI_CHOICES = (72, 96, 150, 300, 600, 1200)


def copy_allowed(view: DocumentView) -> bool:
    return view.document.allows(pymupdf.PDF_PERM_COPY)


def capture_image(view: DocumentView, page: int, rect: pymupdf.Rect, dpi: int) -> tuple[QImage, int]:
    """Immagine dell'area (coordinate pagina non ruotata) alla risoluzione chiesta.

    Restituisce (immagine, dpi effettivi): oltre 100 megapixel la risoluzione si riduce."""
    w_in, h_in = rect.width / 72.0, rect.height / 72.0
    pixels = (w_in * dpi) * (h_in * dpi)
    if pixels > MAX_PIXELS:
        dpi = int(dpi * (MAX_PIXELS / pixels) ** 0.5)
    with ENGINE_LOCK:
        pg = view.document.pdf[page]
        clip = rect * pg.rotation_matrix
        clip.normalize()
        from foliant.api import page_pixmap
        pix = page_pixmap(view.document.pdf, page, hide_unsigned_signatures=True,
                          dpi=dpi, clip=clip, alpha=False, annots=True)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    img.setDotsPerMeterX(int(dpi / 0.0254))
    img.setDotsPerMeterY(int(dpi / 0.0254))
    return img, dpi


def table_text(words: list[tuple]) -> str:
    """Testo di una selezione rettangolare: una riga per riga, tabulazione tra le colonne."""
    if not words:
        return ""
    ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows: list[list[tuple]] = []
    for w in ws:
        cy = (w[1] + w[3]) / 2
        if rows:
            last = rows[-1]
            ref = sum((x[1] + x[3]) / 2 for x in last) / len(last)
            height = max(x[3] - x[1] for x in last)
            if abs(cy - ref) <= height * 0.5:
                last.append(w)
                continue
        rows.append([w])
    lines = []
    for row in rows:
        row.sort(key=lambda w: w[0])
        parts = [row[0][4]]
        for prev, w in zip(row, row[1:]):
            char_w = (prev[2] - prev[0]) / max(1, len(prev[4]))
            gap = w[0] - prev[2]
            parts.append("\t" if gap > char_w * 1.8 else " ")
            parts.append(w[4])
        lines.append("".join(parts))
    return "\n".join(lines)


def words_in_rect(view: DocumentView, page: int, rect: pymupdf.Rect) -> list[tuple]:
    return [w for w in view.words(page)
            if rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]


def area_text(view: DocumentView, page: int, rect: pymupdf.Rect) -> str:
    words = [w for w in view.words(page)
             if rect.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))]
    return words_to_text(words)


class _RectTool(Tool):
    """Base: rettangolo tracciato su una pagina."""

    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, controller: "ToolsController"):
        self.c = controller
        self.page: int | None = None
        self.start: pymupdf.Point | None = None
        self.end: pymupdf.Point | None = None
        self.start_pos: QPointF | None = None
        self.end_pos: QPointF | None = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self.page, self.start, self.end = ev.page, ev.point, ev.point
        self.start_pos = self.end_pos = ev.pos
        return True

    def move(self, view, ev):
        if self.page is None:
            return False
        self.end = view.clamp_to_page(self.page, ev.pos)
        self.end_pos = ev.pos
        return True

    def release(self, view, ev):
        if self.page is None:
            return False
        self.end = view.clamp_to_page(self.page, ev.pos)
        self.end_pos = ev.pos
        page, rect = self.page, self.rect()
        start_pos, end_pos = self.start_pos, self.end_pos
        self.page = self.start = self.end = None
        self.finished(view, page, rect, start_pos, end_pos)
        return True

    def key(self, view, event):
        if event.key() == Qt.Key.Key_Escape:
            self.page = None
            self.c.ctx.set_tool(None)
            return True
        return False

    def rect(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.start, self.end)
        r.normalize()
        return r

    def paint(self, view, painter, page):
        if self.page != page or self.start is None:
            return
        r = self.rect()
        fill = theme.color("accent")
        fill.setAlpha(35)
        pen = QPen(theme.color("accent"))
        pen.setCosmetic(True)
        pen.setWidthF(1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))

    def finished(self, view, page, rect, start_pos, end_pos) -> None:
        raise NotImplementedError


class RectSelectTool(_RectTool):
    """Selezione rettangolare del testo (Alt+trascinamento con lo strumento di selezione)."""

    id = "rect-select"

    def finished(self, view, page, rect, start_pos, end_pos) -> None:
        self.c.select_rect(view, page, rect)
        self.c.end_temporary(view)


class ZoomAreaTool(_RectTool):
    id = "zoom-area"

    def finished(self, view, page, rect, start_pos, end_pos) -> None:
        self.c.zoom_to_area(view, page, rect, start_pos)


class SnapshotTool(_RectTool):
    id = "snapshot"

    def finished(self, view, page, rect, start_pos, end_pos) -> None:
        if rect.width < 2 or rect.height < 2:
            return
        self.c.snapshot_menu(view, page, rect, end_pos)


class _AltDragFilter(QObject):
    """Alt+pressione sinistra con lo strumento di selezione → selezione rettangolare."""

    def __init__(self, controller: "ToolsController"):
        super().__init__(controller.ctx)
        self.c = controller

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() != QEvent.Type.MouseButtonPress or ev.button() != Qt.MouseButton.LeftButton:
            return False
        if not ev.modifiers() & Qt.KeyboardModifier.AltModifier:
            return False
        view = obj.parent()
        if not isinstance(view, DocumentView) or view.tool is not view._default_tool:
            return False
        self.c.begin_temporary(view)
        return False  # la vista inoltra la pressione allo strumento appena attivato


class LensTool(Tool):
    """Lente d'ingrandimento (VIS-38): mostra ingrandita la zona sotto il cursore."""

    id = "lens"
    cursor = Qt.CursorShape.CrossCursor
    FACTOR = 3.0
    SIZE = 220          # lato della lente in pixel

    def __init__(self):
        self.page: int | None = None
        self.point: pymupdf.Point | None = None
        self.image: QImage | None = None

    def move(self, view, ev):
        if ev.page is None:
            if self.page is not None:
                self.page = None
                view.viewport().update()
            return False
        self.page, self.point = ev.page, ev.point
        self.image = self.render(view)
        view.viewport().update()
        return True

    def press(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton

    def release(self, view, ev):
        return ev.button == Qt.MouseButton.LeftButton

    def wheel_factor(self, delta: int) -> None:
        self.FACTOR = max(1.5, min(8.0, self.FACTOR * (1.15 if delta > 0 else 1 / 1.15)))

    def source_rect(self, view) -> pymupdf.Rect:
        scale = view.zoom * 96 / 72
        half = self.SIZE / (2 * scale * self.FACTOR)
        p = self.point
        return pymupdf.Rect(p.x - half, p.y - half, p.x + half, p.y + half)

    def render(self, view) -> QImage | None:
        from foliant.api import try_engine
        with try_engine() as ok:
            if not ok:
                return self.image
            try:
                pg = view.document.pdf[self.page]
                clip = self.source_rect(view) * pg.rotation_matrix
                clip.normalize()
                dpi = int(96 * view.zoom * self.FACTOR * (view.devicePixelRatioF() or 1.0))
                pix = pg.get_pixmap(dpi=max(36, min(2400, dpi)), clip=clip, alpha=False, annots=True)
                return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            except Exception:
                return None

    def paint(self, view, painter, page):
        if self.page != page or self.image is None or self.point is None:
            return
        scale = view.zoom * 96 / 72
        half = self.SIZE / (2 * scale)
        p = self.point
        target = QRectF(p.x - half, p.y - half, 2 * half, 2 * half)
        painter.setClipping(False)
        painter.drawImage(target, self.image)
        pen = QPen(theme.color("accent"))
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(target)


class ToolsController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.rect_tool = RectSelectTool(self)
        self._alt_filter = _AltDragFilter(self)
        ctx.documentOpened.connect(lambda v: v.viewport().installEventFilter(self._alt_filter))
        for v in ctx.views():
            v.viewport().installEventFilter(self._alt_filter)
        self.zoom_tool = ZoomAreaTool(self)
        self.lens_tool = LensTool()
        self.act_lens = ctx.action(_("Lente"), "search", lambda on: self._toggle(self.lens_tool, on),
                                   tooltip=_("Ingrandisce la zona sotto il cursore"), checkable=True)
        self.snapshot_tool = SnapshotTool(self)
        self.act_zoom = ctx.action(_("Zoom area"), "scan-search", lambda on: self._toggle(self.zoom_tool, on),
                                   tooltip=_("Traccia un rettangolo per ingrandirlo a tutta la vista"), checkable=True)
        self.act_snapshot = ctx.action(_("Cattura area"), "camera", lambda on: self._toggle(self.snapshot_tool, on),
                                       tooltip=_("Cattura un'area come immagine o testo"), checkable=True)
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_separator()
            mode.add_action(self.act_zoom)
            mode.add_action(self.act_snapshot)
            mode.add_action(self.act_lens)
        ctx.currentViewChanged.connect(self._on_view)
        self._connected: DocumentView | None = None

    @property
    def dpi(self) -> int:
        try:
            return int(self.ctx.settings().value("viewer/snapshot_dpi", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def set_dpi(self, dpi: int) -> None:
        self.ctx.settings().setValue("viewer/snapshot_dpi", int(dpi))

    def _toggle(self, tool: Tool, on: bool) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        if on and tool is self.snapshot_tool and not copy_allowed(view):
            self.act_snapshot.setChecked(False)
            self.ctx.status(_("Cattura area non disponibile: il documento non consente la copia del contenuto."))
            return
        if on:
            view.set_tool(tool)
        elif view.tool is tool:
            view.set_tool(None)

    def _on_view(self, view) -> None:
        if self._connected is not None:
            try:
                self._connected.toolChanged.disconnect(self._on_tool)
            except (RuntimeError, TypeError):
                pass
        self._connected = view
        if view is not None:
            view.toolChanged.connect(self._on_tool)
            self._on_tool(view.tool)
            allowed = copy_allowed(view)
            self.act_snapshot.setToolTip(_("Cattura un'area come immagine o testo") if allowed else
                                         _("Cattura area non disponibile: il documento non consente la copia del contenuto."))
        else:
            self._on_tool(None)

    def _on_tool(self, tool) -> None:
        self.act_zoom.setChecked(tool is self.zoom_tool)
        self.act_snapshot.setChecked(tool is self.snapshot_tool)
        self.act_lens.setChecked(tool is self.lens_tool)

    # ------------------------------------------------- selezione rettangolare
    def begin_temporary(self, view: DocumentView) -> None:
        view.set_tool(self.rect_tool)

    def end_temporary(self, view: DocumentView) -> None:
        if view.tool is self.rect_tool:
            view.set_tool(None)

    def select_rect(self, view: DocumentView, page: int, rect: pymupdf.Rect) -> TextSelection | None:
        words = words_in_rect(view, page, rect)
        if not words:
            view.clear_text_selection()
            return None
        sel = TextSelection(page=page, rects=[pymupdf.Rect(w[:4]) for w in words],
                            text=table_text(words), words=list(words))
        view.set_text_selection(sel)
        return sel

    # ------------------------------------------------------------- zoom area
    def zoom_to_area(self, view: DocumentView, page: int, rect: pymupdf.Rect, click_pos: QPointF | None = None) -> None:
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        if nav is not None:
            nav.record_jump(view)
        vr = view.page_to_view(page, rect)
        if vr.width() < 4 or vr.height() < 4:
            # semplice clic: un passo di zoom centrato sul punto
            anchor = click_pos or vr.center()
            from foliant.ui.document_view import ZOOM_STEPS
            nxt = next((z for z in ZOOM_STEPS if z > view.zoom + 1e-3), view.MAX_ZOOM)
            view.set_zoom(nxt, anchor=anchor)
            return
        vw, vh = view.viewport().width(), view.viewport().height()
        factor = min(vw / vr.width(), vh / vr.height())
        center = vr.center()
        view.set_zoom(view.zoom * factor, anchor=center)
        # porta il centro dell'area al centro della vista
        c = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        p = view.page_transform(page).map(QPointF(c.x, c.y))
        view.horizontalScrollBar().setValue(int(round(view.horizontalScrollBar().value() + p.x() - vw / 2)))
        view.verticalScrollBar().setValue(int(round(view.verticalScrollBar().value() + p.y() - vh / 2)))

    # ------------------------------------------------------------- istantanea
    def effective_dpi(self, view: DocumentView) -> int:
        dpi = self.dpi
        if dpi <= 0:
            dpi = int(round(96 * view.zoom * (view.devicePixelRatioF() or 1.0)))
        return max(72, min(1200, dpi))

    def copy_image(self, view: DocumentView, page: int, rect: pymupdf.Rect, dpi: int | None = None) -> QImage:
        img, used = capture_image(view, page, rect, dpi or self.effective_dpi(view))
        QGuiApplication.clipboard().setImage(img)
        if dpi and used < dpi:
            self.ctx.status(_("Area troppo grande: la risoluzione è stata ridotta a {n} dpi.").format(n=used))
        else:
            self.ctx.status(_("Immagine copiata ({w} × {h} px)").format(w=img.width(), h=img.height()))
        return img

    def save_image(self, view: DocumentView, page: int, rect: pymupdf.Rect, path: str | None = None,
                   dpi: int | None = None) -> str | None:
        if path is None:
            path = self.ctx.ask_save_path(_("Salva immagine"), "istantanea.png",
                                          "PNG (*.png);;JPEG (*.jpg *.jpeg);;TIFF (*.tif *.tiff)", ".png")
        if not path:
            return None
        img, _used = capture_image(view, page, rect, dpi or self.effective_dpi(view))
        if not img.save(path):
            self.ctx.error(_("Salva immagine"), _("Impossibile salvare l'immagine."))
            return None
        self.ctx.status(_("Immagine salvata: {p}").format(p=path))
        return path

    def copy_text(self, view: DocumentView, page: int, rect: pymupdf.Rect) -> str:
        if not copy_allowed(view):
            self.ctx.status(_("Il documento non consente la copia del contenuto."))
            return ""
        text = area_text(view, page, rect)
        if text:
            QGuiApplication.clipboard().setText(text)
            self.ctx.status(_("Testo copiato"))
        else:
            self.ctx.status(_("Nessun testo nell'area: per le scansioni usa l'OCR."))
        return text

    def snapshot_menu(self, view: DocumentView, page: int, rect: pymupdf.Rect, pos: QPointF | None) -> None:
        menu = QMenu(view)
        menu.addAction(_("Copia immagine"), lambda: self.copy_image(view, page, rect))
        menu.addAction(_("Salva immagine…"), lambda: self.save_image(view, page, rect))
        menu.addAction(_("Copia testo"), lambda: self.copy_text(view, page, rect))
        sub = menu.addMenu(_("Risoluzione"))
        group = QActionGroup(sub)
        for dpi in (0,) + DPI_CHOICES:
            act = sub.addAction(_("Come lo schermo") if dpi == 0 else _("{n} dpi").format(n=dpi))
            act.setCheckable(True)
            act.setChecked(self.dpi == dpi)
            group.addAction(act)
            act.triggered.connect(lambda _c=False, d=dpi: self.set_dpi(d))
        global_pos = view.viewport().mapToGlobal((pos or QPointF(0, 0)).toPoint())
        view.exec_menu(menu, global_pos)
        menu.deleteLater()


def install(ctx: Context) -> ToolsController:
    add_translations("en", TRANSLATIONS)
    return ToolsController(ctx)
