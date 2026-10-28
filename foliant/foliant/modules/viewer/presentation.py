"""Presentazione a schermo intero (VIS-32).

Una pagina alla volta adattata allo schermo, avanzamento da tastiera/mouse o
automatico (`/Dur` della pagina o intervallo scelto), dissolvenza, schermo nero
o bianco, puntatore laser, penna temporanea (mai scritta nel documento) e vista
relatore su un secondo schermo. La logica è in `presentation_engine`.
"""

from __future__ import annotations

import time
from typing import Callable

import pymupdf
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, add_translations, theme

from .presentation_engine import (
    SCREEN_BLACK, SCREEN_WHITE, TRANSITION_FADE, TRANSITION_NONE, PageTiming, PresentationOptions,
    PresentationState, format_clock, parse_number, resolve_transition,
)

SETTINGS_PREFIX = "viewer/presentation/"

_KEY_NAMES = {
    Qt.Key.Key_Space: "space", Qt.Key.Key_Return: "return", Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Right: "right", Qt.Key.Key_Down: "down", Qt.Key.Key_PageDown: "pagedown",
    Qt.Key.Key_Left: "left", Qt.Key.Key_Up: "up", Qt.Key.Key_PageUp: "pageup",
    Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end",
    Qt.Key.Key_Escape: "escape", Qt.Key.Key_B: "b", Qt.Key.Key_W: "w", Qt.Key.Key_L: "l",
    Qt.Key.Key_P: "p", Qt.Key.Key_E: "e", Qt.Key.Key_Period: "b",  # "." = nero (telecomandi)
}


# ======================================================================== motore
def read_timings(document) -> list[PageTiming]:
    """`/Dur` e `/Trans` di ogni pagina (lettura breve sotto ENGINE_LOCK)."""
    out: list[PageTiming] = []
    with ENGINE_LOCK:
        pdf = document.pdf
        for i in range(pdf.page_count):
            xref = pdf[i].xref
            try:
                dur = parse_number(*pdf.xref_get_key(xref, "Dur"))
                style = None
                tdur = None
                kind, _v = pdf.xref_get_key(xref, "Trans")
                if kind != "null":
                    k, v = pdf.xref_get_key(xref, "Trans/S")
                    style = v if k == "name" else "/R" if k == "null" else None
                    tdur = parse_number(*pdf.xref_get_key(xref, "Trans/D"))
                    if style is None:
                        style = "/R"
                trans, tlen = resolve_transition(style, tdur if tdur is not None else 1.0 if style else None)
            except Exception:
                dur, trans, tlen = None, TRANSITION_NONE, 0.0
            out.append(PageTiming(dur, trans, tlen))
    return out


def render_fit(document, index: int, width_px: int, height_px: int) -> QImage:
    """Pagina adattata a `width_px`×`height_px` pixel fisici, centrata a cura del chiamante."""
    with ENGINE_LOCK:
        page = document.pdf[index]
        r = page.rect
        scale = min(width_px / max(1.0, r.width), height_px / max(1.0, r.height))
        scale = max(0.05, min(scale, 12.0))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False, annots=True)
        return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()


# ======================================================================== finestra
class PresentationWindow(QWidget):
    """Finestra a schermo intero del pubblico."""

    TICK_MS = 30

    def __init__(self, controller: "PresentationController", document, start: int,
                 options: PresentationOptions, clock: Callable[[], float] = time.monotonic):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(_("Presentazione"))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.controller = controller
        self.document = document
        self.clock = clock
        self.state = PresentationState(document.page_count, start, read_timings(document), options)
        self._cache: dict[tuple, QImage] = {}
        self._last = clock()
        self.started_at = self._last
        self.laser = False
        self.pen = False
        self.laser_pos: QPointF | None = None
        self.strokes: dict[int, list[list[QPointF]]] = {}   # coordinate normalizzate 0..1
        self._drawing: list[QPointF] | None = None
        self._fade_from: QImage | None = None
        self._fade_start = 0.0
        self._fade_len = 0.0
        self._closing = False
        self.timer = QTimer(self)
        self.timer.setInterval(self.TICK_MS)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start()
        self._update_cursor()

    # ------------------------------------------------------------- immagini
    def _target(self) -> tuple[int, int, float]:
        dpr = self.devicePixelRatioF() or 1.0
        return max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)), dpr

    def image(self, index: int) -> QImage | None:
        if index is None or not (0 <= index < self.state.page_count) or self.document.closed:
            return None
        w, h, dpr = self._target()
        key = (index, w, h, self.document.version)
        img = self._cache.get(key)
        if img is None:
            try:
                img = render_fit(self.document, index, w, h)
            except Exception as e:  # pagina danneggiata: schermo vuoto, niente crash
                self.controller.ctx.status(_("Errore nel disegno della pagina: {e}").format(e=e))
                return None
            img.setDevicePixelRatio(dpr)
            keep = {self.state.page, self.state.next_index(), max(0, self.state.page - 1)}
            for k in [k for k in self._cache if k[0] not in keep]:
                del self._cache[k]
            self._cache[key] = img
        return img

    def cached_pages(self) -> set[int]:
        return {k[0] for k in self._cache}

    def _prefetch(self) -> None:
        nxt = self.state.next_index()
        if nxt is not None and not self._closing:
            self.image(nxt)

    def image_rect(self, img: QImage | None = None) -> QRectF:
        img = img or self.image(self.state.page)
        if img is None:
            return QRectF(self.rect())
        dpr = img.devicePixelRatio() or 1.0
        w, h = img.width() / dpr, img.height() / dpr
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    # ------------------------------------------------------------- stato
    def _page_changed(self, old_page: int, animate: bool = True) -> None:
        self._drawing = None
        if animate and old_page != self.state.page:
            kind, length = self.state.transition_for()
            if kind == TRANSITION_FADE and length > 0:
                self._fade_from = self.image(old_page)
                self._fade_start = self.clock()
                self._fade_len = length
            else:
                self._fade_from = None
        self.update()
        self.controller.page_shown(self.state.page)
        QTimer.singleShot(0, self._prefetch)

    def _on_tick(self) -> None:
        now = self.clock()
        dt = now - self._last
        self._last = now
        old = self.state.page
        if self.state.tick(dt):
            self._page_changed(old)
        if self._fade_from is not None:
            if now - self._fade_start >= self._fade_len:
                self._fade_from = None
            self.update()
        self.controller.tick_presenter()

    def fade_progress(self) -> float:
        if self._fade_from is None or self._fade_len <= 0:
            return 1.0
        return max(0.0, min(1.0, (self.clock() - self._fade_start) / self._fade_len))

    def _update_cursor(self) -> None:
        if self.laser:
            self.setCursor(Qt.CursorShape.BlankCursor)
        elif self.pen:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def command(self, name: str, text: str = "") -> str:
        """Applica un tasto per nome (usato da keyPressEvent e dai test)."""
        old = self.state.page
        cmd = self.state.key(name, text)
        if cmd == "exit":
            self.controller.stop()
            return cmd
        if cmd == "laser":
            self.laser = not self.laser
            self.pen = False if self.laser else self.pen
            self._update_cursor()
        elif cmd == "pen":
            self.pen = not self.pen
            self.laser = False if self.pen else self.laser
            self._update_cursor()
        elif cmd == "erase":
            self.strokes.pop(self.state.page, None)
        if self.state.page != old:
            self._page_changed(old)
        self.update()
        return cmd

    # ------------------------------------------------------------- eventi
    def keyPressEvent(self, ev) -> None:
        name = _KEY_NAMES.get(Qt.Key(ev.key()), "")
        text = ev.text() if ev.text().isdigit() else ""
        if not name and not text:
            if self.state.blanked:
                self.command("any")
            ev.accept()
            return
        self.command(name or "digit", text)
        ev.accept()

    def _norm(self, pos: QPointF) -> QPointF:
        r = self.image_rect()
        return QPointF((pos.x() - r.x()) / max(1.0, r.width()), (pos.y() - r.y()) / max(1.0, r.height()))

    def mousePressEvent(self, ev) -> None:
        if self.state.blanked:
            self.command("any")
            return
        if self.pen and ev.button() == Qt.MouseButton.LeftButton:
            self._drawing = [self._norm(ev.position())]
            self.strokes.setdefault(self.state.page, []).append(self._drawing)
            return
        old = self.state.page
        if ev.button() == Qt.MouseButton.LeftButton:
            self.state.next()
        elif ev.button() == Qt.MouseButton.RightButton:
            self.state.prev()
        if self.state.page != old:
            self._page_changed(old)

    def mouseMoveEvent(self, ev) -> None:
        if self.laser:
            self.laser_pos = ev.position()
            self.update()
        if self._drawing is not None:
            self._drawing.append(self._norm(ev.position()))
            self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._drawing = None

    def wheelEvent(self, ev) -> None:
        delta = ev.angleDelta().y()
        self.command("pagedown" if delta < 0 else "pageup")

    def resizeEvent(self, ev) -> None:
        self._cache.clear()
        super().resizeEvent(ev)

    def closeEvent(self, ev) -> None:
        self.timer.stop()
        if not self._closing:
            self._closing = True
            self.controller.stop()
        super().closeEvent(ev)

    # ------------------------------------------------------------- disegno
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), Qt.GlobalColor.black)
        if self.state.screen == SCREEN_BLACK:
            p.end()
            return
        if self.state.screen == SCREEN_WHITE:
            p.fillRect(self.rect(), Qt.GlobalColor.white)
            p.end()
            return
        img = self.image(self.state.page)
        progress = self.fade_progress()
        if self._fade_from is not None and progress < 1.0:
            p.setOpacity(1.0 - progress)
            p.drawImage(self.image_rect(self._fade_from), self._fade_from)
            p.setOpacity(progress)
        if img is not None:
            p.drawImage(self.image_rect(img), img)
        p.setOpacity(1.0)
        self._paint_strokes(p)
        if self.laser and self.laser_pos is not None:
            c = theme.color("danger")
            halo = QColor(c)
            halo.setAlpha(90)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(self.laser_pos, 14, 14)
            p.setBrush(c)
            p.drawEllipse(self.laser_pos, 7, 7)
        if self.state.typed:
            f = QFont(p.font())
            f.setPointSize(28)
            p.setFont(f)
            p.setPen(Qt.GlobalColor.white)
            box = QRectF(self.width() - 220, self.height() - 80, 200, 60)
            p.fillRect(box, QColor(0, 0, 0, 160))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, self.state.typed)
        p.end()

    def _paint_strokes(self, p: QPainter) -> None:
        strokes = self.strokes.get(self.state.page)
        if not strokes:
            return
        r = self.image_rect()
        pen = QPen(theme.color("danger"), max(2.0, r.height() / 250))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for stroke in strokes:
            path = QPainterPath()
            for n, q in enumerate(stroke):
                pt = QPointF(r.x() + q.x() * r.width(), r.y() + q.y() * r.height())
                if n == 0:
                    path.moveTo(pt)
                else:
                    path.lineTo(pt)
            if len(stroke) == 1:
                p.drawPoint(path.currentPosition())
            p.drawPath(path)


class PresenterView(QWidget):
    """Vista relatore: pagina corrente, successiva, cronometro e ora."""

    def __init__(self, window: PresentationWindow):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(_("Vista relatore"))
        self.pres = window
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, ev) -> None:
        self.pres.keyPressEvent(ev)
        self.update()

    def _thumb(self, index: int | None, box: QRectF) -> tuple[QImage | None, QRectF]:
        if index is None:
            return None, box
        dpr = self.devicePixelRatioF() or 1.0
        try:
            img = render_fit(self.pres.document, index, int(box.width() * dpr), int(box.height() * dpr))
        except Exception:
            return None, box
        img.setDevicePixelRatio(dpr)
        w, h = img.width() / dpr, img.height() / dpr
        return img, QRectF(box.x() + (box.width() - w) / 2, box.y() + (box.height() - h) / 2, w, h)

    def texts(self) -> dict[str, str]:
        st = self.pres.state
        nxt = st.next_index()
        return {
            "current": _("Pagina {n} di {t}").format(n=st.page + 1, t=st.page_count),
            "next": _("Successiva: pagina {n}").format(n=nxt + 1) if nxt is not None else _("Fine della presentazione"),
            "elapsed": format_clock(self.pres.clock() - self.pres.started_at),
            "clock": time.strftime("%H:%M"),
        }

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), theme.color("window"))
        st = self.pres.state
        t = self.texts()
        m = 24.0
        w, h = self.width(), self.height()
        cur_box = QRectF(m, m + 30, w * 0.6 - 1.5 * m, h - 2 * m - 90)
        nxt_box = QRectF(w * 0.6 + m / 2, m + 30, w * 0.4 - 1.5 * m, (h - 2 * m) * 0.45)
        for index, box, label in ((st.page, cur_box, t["current"]), (st.next_index(), nxt_box, t["next"])):
            p.setPen(theme.color("text_muted"))
            p.drawText(QRectF(box.x(), box.y() - 28, box.width(), 24), Qt.AlignmentFlag.AlignLeft, label)
            p.fillRect(box, theme.color("canvas"))
            img, r = self._thumb(index, box)
            if img is not None:
                p.drawImage(r, img)
        f = QFont(p.font())
        f.setPointSize(30)
        f.setBold(True)
        p.setFont(f)
        p.setPen(theme.color("text"))
        info = QRectF(w * 0.6 + m / 2, nxt_box.bottom() + m, w * 0.4 - 1.5 * m, 60)
        p.drawText(info, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t["elapsed"])
        p.setPen(theme.color("text_muted"))
        p.drawText(info, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, t["clock"])
        small = QFont(p.font())
        small.setPointSize(10)
        small.setBold(False)
        p.setFont(small)
        caption = QRectF(info.x(), info.bottom(), info.width(), 20)
        p.drawText(caption, Qt.AlignmentFlag.AlignLeft, _("Tempo trascorso"))
        p.drawText(caption, Qt.AlignmentFlag.AlignRight, _("Ora"))
        info = info.translated(0, 24)
        if st.blanked:
            f.setPointSize(12)
            f.setBold(False)
            p.setFont(f)
            p.setPen(theme.color("warning"))
            p.drawText(info.translated(0, 60), Qt.AlignmentFlag.AlignLeft,
                       _("Schermo nero") if st.screen == SCREEN_BLACK else _("Schermo bianco"))
        p.end()


# ======================================================================== opzioni
class OptionsDialog(QDialog):
    def __init__(self, parent, options: PresentationOptions):
        super().__init__(parent)
        self.setWindowTitle(_("Opzioni presentazione"))
        form = QFormLayout(self)
        self.use_doc = QCheckBox(_("Usa tempi e transizioni del documento"))
        self.use_doc.setChecked(options.use_document_timing)
        form.addRow(self.use_doc)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0, 3600)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setSpecialValueText(_("Manuale"))
        self.interval.setValue(options.interval or 0)
        form.addRow(_("Avanzamento automatico ogni"), self.interval)
        self.transition = QComboBox()
        self.transition.addItem(_("Dissolvenza"), TRANSITION_FADE)
        self.transition.addItem(_("Nessuna"), TRANSITION_NONE)
        self.transition.setCurrentIndex(0 if options.transition == TRANSITION_FADE else 1)
        form.addRow(_("Transizione"), self.transition)
        self.loop = QCheckBox(_("Ciclo continuo"))
        self.loop.setChecked(options.loop)
        form.addRow(self.loop)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def options(self) -> PresentationOptions:
        return PresentationOptions(self.use_doc.isChecked(), self.interval.value() or None,
                                   self.transition.currentData(), self.loop.isChecked())


# ======================================================================== controller
def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes")


class PresentationController(QObject):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        self.window: PresentationWindow | None = None
        self.presenter: PresenterView | None = None
        self.view = None
        self.clock: Callable[[], float] = time.monotonic
        self.act_start = ctx.action(_("Presenta dall'inizio"), "presentation", lambda: self.start(False),
                                    shortcut="F5")
        self.act_current = ctx.action(_("Presenta dalla pagina corrente"), "presentation",
                                      lambda: self.start(True), shortcut="Shift+F5")
        self.act_options = ctx.action(_("Opzioni presentazione…"), "settings", self.edit_options,
                                      requires_document=False)
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_action(self.act_start)
        for act in (self.act_start, self.act_current, self.act_options):
            ctx.add_menu_action(act, _("Vista"))

    # ------------------------------------------------------------- opzioni
    def options(self) -> PresentationOptions:
        s = self.ctx.settings()
        try:
            interval = float(s.value(SETTINGS_PREFIX + "interval", 0) or 0)
        except (TypeError, ValueError):
            interval = 0.0
        transition = str(s.value(SETTINGS_PREFIX + "transition", TRANSITION_FADE))
        if transition not in (TRANSITION_FADE, TRANSITION_NONE):
            transition = TRANSITION_FADE
        return PresentationOptions(
            _as_bool(s.value(SETTINGS_PREFIX + "use_document_timing"), True),
            interval if interval > 0 else None, transition,
            _as_bool(s.value(SETTINGS_PREFIX + "loop"), False))

    def set_options(self, options: PresentationOptions) -> None:
        s = self.ctx.settings()
        s.setValue(SETTINGS_PREFIX + "use_document_timing", options.use_document_timing)
        s.setValue(SETTINGS_PREFIX + "interval", float(options.interval or 0))
        s.setValue(SETTINGS_PREFIX + "transition", options.transition)
        s.setValue(SETTINGS_PREFIX + "loop", options.loop)
        if self.window is not None:
            self.window.state.options = options

    def ask_options(self) -> PresentationOptions | None:
        dlg = OptionsDialog(self.ctx.window, self.options())
        return dlg.options() if dlg.exec() == QDialog.DialogCode.Accepted else None

    def edit_options(self) -> None:
        opts = self.ask_options()
        if opts is not None:
            self.set_options(opts)

    # ------------------------------------------------------------- avvio/uscita
    @property
    def active(self) -> bool:
        return self.window is not None

    def start(self, from_current: bool = False) -> PresentationWindow | None:
        view = self.ctx.current_view()
        if view is None or self.window is not None:
            return self.window
        if view.page_count == 0:
            return None
        self.view = view
        start = view.current_page if from_current else 0
        try:
            win = PresentationWindow(self, view.document, start, self.options(), self.clock)
        except Exception as e:
            self.ctx.error(_("Presentazione"), _("Impossibile avviare la presentazione: {e}").format(e=e))
            return None
        self.window = win
        screens = QGuiApplication.screens()
        own = self.ctx.window.screen()
        audience = own
        presenter_screen = None
        if len(screens) > 1:
            own_index = next((i for i, s in enumerate(screens) if s is own), 0)
            audience = screens[1] if own_index == 0 else screens[0]
            presenter_screen = screens[own_index]
        if audience is not None:
            win.setGeometry(audience.geometry())
        win.showFullScreen()
        win.activateWindow()
        win.setFocus()
        if presenter_screen is not None:
            self.presenter = PresenterView(win)
            self.presenter.setGeometry(presenter_screen.availableGeometry())
            self.presenter.show()
            self.presenter.showMaximized()
        QTimer.singleShot(0, win._prefetch)
        return win

    def stop(self) -> None:
        win, self.window = self.window, None
        if win is None:
            return
        page = win.state.page
        win._closing = True
        win.timer.stop()
        win.close()
        win.deleteLater()
        if self.presenter is not None:
            self.presenter.close()
            self.presenter.deleteLater()
            self.presenter = None
        view = self.view
        self.view = None
        if view is not None and not view.document.closed:
            try:
                view.go_to_page(page)
                view.setFocus()
            except RuntimeError:
                pass
        self.ctx.window.activateWindow()

    def page_shown(self, index: int) -> None:
        if self.presenter is not None:
            self.presenter.update()

    def tick_presenter(self) -> None:
        if self.presenter is not None and self.window is not None:
            if int(self.clock() * 2) != getattr(self, "_last_half", None):
                self._last_half = int(self.clock() * 2)
                self.presenter.update()


def install(ctx) -> PresentationController:
    add_translations("en", {
        "Presentazione": "Presentation",
        "Presenta dall'inizio": "Present from start",
        "Presenta dalla pagina corrente": "Present from current page",
        "Opzioni presentazione…": "Presentation options…",
        "Opzioni presentazione": "Presentation options",
        "Vista": "View",
        "Vista relatore": "Presenter view",
        "Errore nel disegno della pagina: {e}": "Error drawing the page: {e}",
        "Pagina {n} di {t}": "Page {n} of {t}",
        "Successiva: pagina {n}": "Next: page {n}",
        "Fine della presentazione": "End of presentation",
        "Schermo nero": "Black screen",
        "Tempo trascorso": "Elapsed time",
        "Ora": "Time",
        "Schermo bianco": "White screen",
        "Usa tempi e transizioni del documento": "Use document timings and transitions",
        "Manuale": "Manual",
        "Avanzamento automatico ogni": "Advance automatically every",
        "Dissolvenza": "Fade",
        "Nessuna": "None",
        "Transizione": "Transition",
        "Ciclo continuo": "Loop continuously",
        "Impossibile avviare la presentazione: {e}": "Unable to start the presentation: {e}",
    })
    return PresentationController(ctx)
