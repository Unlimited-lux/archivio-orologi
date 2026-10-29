"""Modalità lettura, schermo intero con controlli a scomparsa (VIS-31) e
scorrimento automatico (VIS-33).

- Modalità lettura: nasconde barre e pannelli lasciando solo il documento; Esc o
  di nuovo Ctrl+Maiusc+L ripristinano esattamente la disposizione precedente.
- Schermo intero (F11 del nucleo): in più nasconde barre e pannelli, mostra una
  barra minima semitrasparente e nasconde barra e cursore dopo `hide_delay_ms`
  di inattività del mouse; Esc esce.
- Scorrimento automatico: continuo in px/s o a pagine, Spazio pausa/riprende,
  Esc o clic interrompono, scorrimento manuale mette in pausa; `tick(dt)`
  pubblico per simulare il tempo.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QToolButton

from foliant.api import ENGINE_LOCK, VIEW_SINGLE, _, add_translations, bind_icon, theme

from .ribbon_place import place, place_menu_button

SETTINGS_PREFIX = "viewer/autoscroll/"
MODE_CONTINUOUS = "continuous"
MODE_PAGES = "pages"
DEFAULT_SPEED = 50.0
DEFAULT_PAGE_SECONDS = 10.0
MIN_SPEED, MAX_SPEED = 5.0, 2000.0
MIN_PAGE_SECONDS, MAX_PAGE_SECONDS = 1.0, 600.0


# ======================================================================== logica pura
class AutoScrollEngine:
    """Accumulo frazionario del tempo: pixel interi da scorrere o pagine da avanzare."""

    def __init__(self, speed: float = DEFAULT_SPEED, mode: str = MODE_CONTINUOUS,
                 page_seconds: float = DEFAULT_PAGE_SECONDS):
        self.speed = speed
        self.mode = mode
        self.page_seconds = page_seconds
        self.paused = False
        self.reset()

    def reset(self) -> None:
        self._px = 0.0
        self._page_t = 0.0

    def set_speed(self, speed: float) -> float:
        self.speed = max(MIN_SPEED, min(MAX_SPEED, float(speed)))
        return self.speed

    def set_page_seconds(self, seconds: float) -> float:
        self.page_seconds = max(MIN_PAGE_SECONDS, min(MAX_PAGE_SECONDS, float(seconds)))
        return self.page_seconds

    def faster(self) -> None:
        if self.mode == MODE_PAGES:
            self.set_page_seconds(self.page_seconds - 1)
        else:
            self.set_speed(self.speed + (10 if self.speed >= 20 else 5))

    def slower(self) -> None:
        if self.mode == MODE_PAGES:
            self.set_page_seconds(self.page_seconds + 1)
        else:
            self.set_speed(self.speed - (10 if self.speed > 20 else 5))

    def advance_pixels(self, dt: float) -> int:
        if self.paused or dt <= 0:
            return 0
        self._px += self.speed * dt
        whole = int(self._px)
        self._px -= whole
        return whole

    def advance_pages(self, dt: float) -> int:
        if self.paused or dt <= 0:
            return 0
        self._page_t += dt
        n = int(self._page_t // self.page_seconds)
        self._page_t -= n * self.page_seconds
        return n


def chrome_widgets(window) -> dict:
    """Barre e pannelli che la modalità lettura nasconde."""
    return {
        "header": window.header,
        "context": window.context_stack,
        "sidebar": window.sidebar,
        "properties": window.properties,
        "status": window.statusBar(),
        "tabs": window.tabs.tabBar(),
    }


# ======================================================================== widget
class FullscreenBar(QFrame):
    """Barra minima semitrasparente dello schermo intero."""

    def __init__(self, controller: "ReadingController", parent):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("fullscreenBar")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(6)
        ctx = controller.ctx

        def button(icon_name: str, tip: str, slot) -> QToolButton:
            b = QToolButton(self)
            bind_icon(b, icon_name)
            b.setToolTip(tip)
            b.setAccessibleName(tip.split(" (")[0])      # senza la scorciatoia
            b.setAutoRaise(True)
            b.setMouseTracking(True)
            b.clicked.connect(slot)
            b.installEventFilter(controller)
            lay.addWidget(b)
            return b

        w = ctx.window
        self.prev_button = button("chevron-up", _("Pagina precedente"), w.act_prev.trigger)
        self.page_label = QLabel(self)
        self.page_label.setMinimumWidth(70)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.page_label)
        self.next_button = button("chevron-down", _("Pagina successiva"), w.act_next.trigger)
        lay.addSpacing(10)
        self.zoom_out_button = button("zoom-out", _("Riduci zoom"), w.act_zoom_out.trigger)
        self.zoom_label = QLabel(self)
        self.zoom_label.setMinimumWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.zoom_label)
        self.zoom_in_button = button("zoom-in", _("Aumenta zoom"), w.act_zoom_in.trigger)
        self.fit_button = button("maximize", _("Adatta alla pagina"), w.act_fit_page.trigger)
        lay.addSpacing(10)
        self.exit_button = button("minimize", _("Esci dallo schermo intero (Esc)"), controller.exit_fullscreen)
        self.installEventFilter(controller)
        self.refresh()

    def refresh(self) -> None:
        view = self.controller.ctx.current_view()
        if view is None:
            self.page_label.setText("–")
            self.zoom_label.setText("")
        else:
            self.page_label.setText(f"{view.current_page + 1} / {view.page_count}")
            self.zoom_label.setText(f"{round(view.zoom * 100)}%")
        c = theme.hex_color("text")
        self.setStyleSheet(f"QLabel {{ color: {c}; background: transparent; }}")
        self.adjustSize()
        self.place()

    def place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.move(int((parent.width() - self.width()) / 2), parent.height() - self.height() - 24)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bg = theme.color("surface")
        bg.setAlpha(215)
        border = theme.color("border")
        p.setPen(QPen(border, 1))
        p.setBrush(bg)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.end()


class HintLabel(QLabel):
    """Etichetta sovrapposta e temporanea («Esc per uscire»)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("readingHint")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def flash(self, text: str, ms: int = 3000) -> None:
        bg = theme.color("surface")
        bg.setAlpha(225)
        self.setStyleSheet(
            f"QLabel#readingHint {{ background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
            f" color: {theme.hex_color('text')}; border: 1px solid {theme.hex_color('border')};"
            f" border-radius: 14px; padding: 6px 16px; }}")
        self.setText(text)
        self.adjustSize()
        parent = self.parentWidget()
        self.move(int((parent.width() - self.width()) / 2), 20)
        self.show()
        self.raise_()
        self.timer.start(ms)


# ======================================================================== controller
def _float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_bool(v, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes")


class ReadingController(QObject):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        w = ctx.window
        self.window = w
        self.clock: Callable[[], float] = time.monotonic
        self.hide_delay_ms = 3000
        # stato barre
        self._hide_reasons: set[str] = set()
        self._saved_chrome: dict[str, bool] = {}
        self.reading_active = False
        self.fullscreen_active = False
        # stato scorrimento
        s = ctx.settings()
        self.engine = AutoScrollEngine(
            _float(s.value(SETTINGS_PREFIX + "speed", DEFAULT_SPEED), DEFAULT_SPEED),
            MODE_PAGES if s.value(SETTINGS_PREFIX + "mode", MODE_CONTINUOUS) == MODE_PAGES else MODE_CONTINUOUS,
            _float(s.value(SETTINGS_PREFIX + "page_seconds", DEFAULT_PAGE_SECONDS), DEFAULT_PAGE_SECONDS))
        self.engine.set_speed(self.engine.speed)
        self.engine.set_page_seconds(self.engine.page_seconds)
        self.loop = _as_bool(s.value(SETTINGS_PREFIX + "loop"), False)
        self.scrolling = False
        self._scroll_view = None
        self._own_scroll = False
        self._last_tick = 0.0
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(16)
        self.scroll_timer.timeout.connect(self._on_scroll_timer)

        # azioni
        self.act_reading = ctx.action(_("Modalità lettura"), "book-open", None, shortcut="Ctrl+Shift+L",
                                      checkable=True)
        self.act_reading.toggled.connect(self.set_reading)
        self.act_autoscroll = ctx.action(_("Scorrimento automatico"), "chevrons-down", None, checkable=True)
        self.act_autoscroll.toggled.connect(self.set_autoscroll)
        self.act_pages = ctx.action(_("Scorrimento automatico a pagine"), "file", None, checkable=True,
                                    requires_document=False)
        self.act_pages.setChecked(self.engine.mode == MODE_PAGES)
        self.act_pages.toggled.connect(lambda on: self.set_scroll_mode(MODE_PAGES if on else MODE_CONTINUOUS))
        self.act_loop = ctx.action(_("Ricomincia dall'inizio a fine documento"), "repeat", None, checkable=True,
                                   requires_document=False)
        self.act_loop.setChecked(self.loop)
        self.act_loop.toggled.connect(self.set_loop)
        # Visualizza › Lettura: Modalità lettura (poi Tema), Scorrimento automatico ▾ con le sue opzioni
        place(ctx, "visualizza", self.act_reading, "lettura", 10)
        self.autoscroll_button = place_menu_button(
            ctx, "visualizza", _("Scorrimento automatico"), "chevrons-down",
            [self.act_autoscroll, None, self.act_pages, self.act_loop], "lettura", 30,
            compact=True, requires_document=False)     # le opzioni si scelgono anche senza documento

        self.esc_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), w)
        self.esc_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.esc_shortcut.activated.connect(self.escape)
        self.esc_shortcut.setEnabled(False)

        # widget sovrapposti
        self.bar = FullscreenBar(self, w)
        self.bar.hide()
        self.hint = HintLabel(w)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self._on_idle)
        self.cursor_hidden = False
        self.indicator = QLabel(w)
        self.indicator.hide()
        w.statusBar().addPermanentWidget(self.indicator)

        # segnali
        w.installEventFilter(self)          # solo per il ridimensionamento della barra minima
        ctx.fullscreenChanged.connect(self._on_fullscreen_changed)
        ctx.currentViewChanged.connect(self._on_current_view)
        ctx.documentOpened.connect(self._on_document_opened)
        ctx.documentClosed.connect(self._on_document_closed)
        ctx.documentChanged.connect(self._on_document_changed)
        self._filtered: set[int] = set()
        for view in ctx.views():
            self._watch(view)

    # ============================================================ barre e pannelli
    def _hide_chrome(self, reason: str) -> None:
        if not self._hide_reasons:
            self._saved_chrome = {k: not wd.isHidden() for k, wd in chrome_widgets(self.window).items()}
        self._hide_reasons.add(reason)
        self._apply_hidden()

    def _apply_hidden(self) -> None:
        if not self._hide_reasons:
            return
        for wd in chrome_widgets(self.window).values():
            wd.hide()

    def _restore_chrome(self, reason: str) -> None:
        if reason not in self._hide_reasons:
            return
        self._hide_reasons.discard(reason)
        if self._hide_reasons:
            return
        for key, wd in chrome_widgets(self.window).items():
            if key in self._saved_chrome:
                wd.setVisible(self._saved_chrome[key])
        self._saved_chrome = {}

    def _keep_position(self) -> None:
        """Mantiene la stessa porzione di documento in alto dopo un cambio di disposizione."""
        view = self.ctx.current_view()
        if view is None or view.page_count == 0:
            return
        page, zoom, fit = view.current_page, view.zoom, view.fit_mode
        hit = view.view_to_page(QPointF(view.viewport().width() / 2, 0), clamp=True)
        offset = 0.0
        if hit is not None:
            offset = view.page_transform(hit[0]).map(QPointF(hit[1].x, hit[1].y)).y()

        def reanchor() -> None:
            try:
                if view.document.closed:
                    return
                QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)
                if fit is None and abs(view.zoom - zoom) > 1e-6:
                    view.set_zoom(zoom)
                if hit is not None:
                    y = view.page_transform(hit[0]).map(QPointF(hit[1].x, hit[1].y)).y()
                    vbar = view.verticalScrollBar()
                    vbar.setValue(int(round(vbar.value() + y - offset)))
                if view.current_page != page and view.view_mode == VIEW_SINGLE:
                    view.go_to_page(page)
            except RuntimeError:
                pass

        reanchor()
        QTimer.singleShot(0, reanchor)

    def _update_shortcut(self) -> None:
        self.esc_shortcut.setEnabled(self.reading_active or self.fullscreen_active or self.scrolling)

    # ============================================================ modalità lettura
    def set_reading(self, on: bool) -> None:
        on = bool(on) and self.ctx.current_view() is not None
        if on == self.reading_active:
            if self.act_reading.isChecked() != on:
                self._set_checked(self.act_reading, on)
            return
        self._keep_position()
        self.reading_active = on
        if on:
            self._hide_chrome("reading")
            self.hint.flash(_("Modalità lettura — Esc per uscire"))
        else:
            self._restore_chrome("reading")
            self.hint.hide()
        self._set_checked(self.act_reading, on)
        self._update_shortcut()

    def toggle_reading(self) -> None:
        self.set_reading(not self.reading_active)

    @staticmethod
    def _set_checked(act, on: bool) -> None:
        act.blockSignals(True)
        act.setChecked(on)
        act.blockSignals(False)

    def escape(self) -> None:
        """Esc: ferma lo scorrimento, poi esce dallo schermo intero, poi dalla lettura."""
        if self.scrolling:
            self.stop_autoscroll()
        elif self.fullscreen_active:
            self.exit_fullscreen()
        elif self.reading_active:
            self.set_reading(False)

    # ============================================================ schermo intero
    def exit_fullscreen(self) -> None:
        if self.window.isFullScreen():
            self.window.toggle_fullscreen()
        self._on_window_state()

    def _on_fullscreen_changed(self, _full: bool) -> None:
        self._on_window_state()
        QTimer.singleShot(0, self._on_window_state)

    def _on_window_state(self) -> None:
        fs = self.window.isFullScreen()
        if fs == self.fullscreen_active:
            return
        self._keep_position()
        self.fullscreen_active = fs
        if fs:
            self._hide_chrome("fullscreen")
            self.hint.flash(_("Schermo intero — Esc per uscire"))
            self._activity()
        else:
            self._restore_chrome("fullscreen")
            self.idle_timer.stop()
            self.bar.hide()
            self._show_cursor()
        self._update_shortcut()

    def _activity(self) -> None:
        if not self.fullscreen_active:
            return
        self._show_cursor()
        self.bar.refresh()
        self.bar.show()
        self.bar.raise_()
        self.idle_timer.start(self.hide_delay_ms)

    def _on_idle(self) -> None:
        if not self.fullscreen_active:
            return
        self.bar.hide()
        view = self.ctx.current_view()
        if view is not None:
            view.viewport().setCursor(Qt.CursorShape.BlankCursor)
        self.cursor_hidden = True

    def _show_cursor(self) -> None:
        if not self.cursor_hidden:
            return
        self.cursor_hidden = False
        view = self.ctx.current_view()
        if view is not None:
            view.viewport().setCursor(view.tool.cursor)

    # ============================================================ /PageMode /FullScreen
    def _on_document_opened(self, view) -> None:
        self._watch(view)
        try:
            with ENGINE_LOCK:
                pdf = view.document.pdf
                kind, value = pdf.xref_get_key(pdf.pdf_catalog(), "PageMode") if pdf.is_pdf else ("null", "")
        except Exception:
            return
        if kind == "name" and value == "/FullScreen":
            QTimer.singleShot(0, lambda: self._offer_fullscreen(view))

    def ask_fullscreen(self) -> bool:
        return self.ctx.confirm(_("Schermo intero"),
                                _("Il documento chiede di essere mostrato a schermo intero. Procedere?"))

    def _offer_fullscreen(self, view) -> None:
        """Proposta non bloccante: l'apertura del documento non si ferma su un dialogo modale."""
        if view.document.closed or self.window.isFullScreen():
            return
        if type(self).ask_fullscreen is not ReadingController.ask_fullscreen or \
                self.__dict__.get("ask_fullscreen") is not None:
            # sostituita (test o integrazioni): risposta immediata
            if self.ask_fullscreen():
                self.window.toggle_fullscreen()
                self._on_window_state()
            return
        def enter() -> None:
            if not self.window.isFullScreen():
                self.window.toggle_fullscreen()
                self._on_window_state()

        self.fullscreen_prompt = self.ctx.notify(
            _("Il documento chiede di essere mostrato a schermo intero."),
            [(_("Schermo intero"), enter)], duration_ms=0)

    # ============================================================ scorrimento automatico
    def set_scroll_mode(self, mode: str) -> None:
        self.engine.mode = MODE_PAGES if mode == MODE_PAGES else MODE_CONTINUOUS
        self.engine.reset()
        self._set_checked(self.act_pages, self.engine.mode == MODE_PAGES)
        self._save_scroll_settings()
        self._update_indicator()

    def set_loop(self, on: bool) -> None:
        self.loop = bool(on)
        self._set_checked(self.act_loop, self.loop)
        self._save_scroll_settings()

    def set_speed(self, speed: float) -> None:
        self.engine.set_speed(speed)
        self._save_scroll_settings()
        self._update_indicator()

    def _save_scroll_settings(self) -> None:
        s = self.ctx.settings()
        s.setValue(SETTINGS_PREFIX + "speed", self.engine.speed)
        s.setValue(SETTINGS_PREFIX + "mode", self.engine.mode)
        s.setValue(SETTINGS_PREFIX + "page_seconds", self.engine.page_seconds)
        s.setValue(SETTINGS_PREFIX + "loop", self.loop)

    def effective_mode(self) -> str:
        view = self._scroll_view or self.ctx.current_view()
        if view is not None and view.view_mode == VIEW_SINGLE:
            return MODE_PAGES
        return self.engine.mode

    @property
    def paused(self) -> bool:
        return self.engine.paused

    def set_autoscroll(self, on: bool) -> None:
        if on:
            self.start_autoscroll()
        else:
            self.stop_autoscroll()

    def start_autoscroll(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            self._set_checked(self.act_autoscroll, False)
            return False
        if self.scrolling:
            return True
        self._scroll_view = view
        self.scrolling = True
        self.engine.paused = False
        self.engine.reset()
        if self._at_end(view):
            self._scroll_to_start(view)
        view.verticalScrollBar().valueChanged.connect(self._on_manual_scroll)
        self._last_tick = self.clock()
        self.scroll_timer.start()
        self._set_checked(self.act_autoscroll, True)
        self._update_indicator()
        self._update_shortcut()
        view.setFocus()
        return True

    def stop_autoscroll(self) -> None:
        if not self.scrolling:
            self._set_checked(self.act_autoscroll, False)
            return
        self.scrolling = False
        self.scroll_timer.stop()
        view, self._scroll_view = self._scroll_view, None
        if view is not None:
            try:
                view.verticalScrollBar().valueChanged.disconnect(self._on_manual_scroll)
            except (RuntimeError, TypeError):
                pass
        self.engine.paused = False
        self._set_checked(self.act_autoscroll, False)
        self._update_indicator()
        self._update_shortcut()

    def toggle_pause(self) -> None:
        if not self.scrolling:
            return
        self.engine.paused = not self.engine.paused
        self._last_tick = self.clock()
        self._update_indicator()

    def pause(self) -> None:
        if self.scrolling and not self.engine.paused:
            self.engine.paused = True
            self._update_indicator()

    def _on_scroll_timer(self) -> None:
        now = self.clock()
        dt = now - self._last_tick
        self._last_tick = now
        self.tick(min(dt, 1.0))

    @staticmethod
    def _at_end(view) -> bool:
        vbar = view.verticalScrollBar()
        if view.view_mode == VIEW_SINGLE:
            return view.current_page >= view.page_count - 1 and vbar.value() >= vbar.maximum()
        return vbar.value() >= vbar.maximum()

    def _scroll_to_start(self, view) -> None:
        self._own_scroll = True
        try:
            view.go_to_page(0)
            view.verticalScrollBar().setValue(0)
        finally:
            self._own_scroll = False

    def tick(self, dt: float) -> None:
        """Fa trascorrere `dt` secondi di scorrimento (usato dal timer e dai test)."""
        view = self._scroll_view
        if not self.scrolling or view is None or self.engine.paused:
            return
        if view.document.closed:
            self.stop_autoscroll()
            return
        vbar = view.verticalScrollBar()
        self._own_scroll = True
        try:
            if self.effective_mode() == MODE_PAGES:
                for _i in range(self.engine.advance_pages(dt)):
                    if view.current_page >= view.page_count - 1:
                        if vbar.value() < vbar.maximum():
                            vbar.setValue(vbar.maximum())
                        break
                    view.next_page()
            else:
                px = self.engine.advance_pixels(dt)
                if px:
                    vbar.setValue(min(vbar.maximum(), vbar.value() + px))
        finally:
            self._own_scroll = False
        if self._at_end(view):
            if self.loop:
                self._scroll_to_start(view)
                self.engine.reset()
            else:
                self.stop_autoscroll()
                self.ctx.status(_("Scorrimento automatico terminato: fine del documento"), 3000)

    def _on_manual_scroll(self, _value: int) -> None:
        if not self._own_scroll:
            self.pause()

    def _update_indicator(self) -> None:
        if not self.scrolling:
            self.indicator.hide()
            return
        if self.effective_mode() == MODE_PAGES:
            speed = _("{s:g} s per pagina").format(s=self.engine.page_seconds)
        else:
            speed = _("{v:g} px/s").format(v=self.engine.speed)
        text = _("Scorrimento automatico: {speed}").format(speed=speed)
        if self.engine.paused:
            text += " — " + _("in pausa (Spazio per riprendere)")
        self.indicator.setText(text)
        self.indicator.show()
        if self.reading_active or self.fullscreen_active:
            self.hint.flash(text, 1500)

    # ============================================================ eventi
    def _watch(self, view) -> None:
        if id(view) in self._filtered:
            return
        self._filtered.add(id(view))
        view.installEventFilter(self)
        view.viewport().installEventFilter(self)

    def _on_current_view(self, view) -> None:
        if self.scrolling and view is not self._scroll_view:
            self.stop_autoscroll()
        if view is None and self.reading_active:
            self.set_reading(False)
        if self._hide_reasons:
            # il nucleo rimostra barra contestuale e pannello laterale al cambio scheda
            self._apply_hidden()
            QTimer.singleShot(0, self._apply_hidden)
        if view is not None:
            self._watch(view)
        if self.fullscreen_active:
            self.bar.refresh()

    def _on_document_closed(self, view) -> None:
        self._filtered.discard(id(view))
        if view is self._scroll_view:
            self.stop_autoscroll()

    def _on_document_changed(self, view) -> None:
        if view is self._scroll_view:
            self.stop_autoscroll()

    def eventFilter(self, obj, ev) -> bool:
        et = ev.type()
        if obj is self.window:
            if et == QEvent.Type.Resize:
                self.bar.place()
            return False
        if et in (QEvent.Type.MouseMove, QEvent.Type.Enter, QEvent.Type.HoverMove):
            self._activity()
            if self.fullscreen_active and self.bar.isVisible() and not self.bar.underMouse():
                self.bar.refresh()
            return False
        view = self._scroll_view
        if not self.scrolling or view is None:
            if et == QEvent.Type.MouseButtonPress:
                self._activity()
            return False
        if obj is view and et == QEvent.Type.KeyPress:
            key = ev.key()
            if key == Qt.Key.Key_Space:
                self.toggle_pause()
                return True
            if key == Qt.Key.Key_Escape:
                self.stop_autoscroll()
                return True
            if key == Qt.Key.Key_Up:
                self.engine.slower()
                self._save_scroll_settings()
                self._update_indicator()
                return True
            if key == Qt.Key.Key_Down:
                self.engine.faster()
                self._save_scroll_settings()
                self._update_indicator()
                return True
            return False
        if obj is view.viewport():
            if et == QEvent.Type.MouseButtonPress:
                self.stop_autoscroll()
                self._activity()
                return False
            if et == QEvent.Type.Wheel and not self.engine.paused \
                    and not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
                delta = ev.angleDelta().y()
                if delta > 0:
                    self.engine.slower()
                elif delta < 0:
                    self.engine.faster()
                self._save_scroll_settings()
                self._update_indicator()
                return True
        return False


def install(ctx) -> ReadingController:
    add_translations("en", {
        "Modalità lettura": "Reading mode",
        "Scorrimento automatico": "Auto scroll",
        "Scorrimento automatico a pagine": "Auto scroll by pages",
        "Ricomincia dall'inizio a fine documento": "Restart from the beginning at end of document",
        "Pagina precedente": "Previous page",
        "Pagina successiva": "Next page",
        "Riduci zoom": "Zoom out",
        "Aumenta zoom": "Zoom in",
        "Adatta alla pagina": "Fit page",
        "Esci dallo schermo intero (Esc)": "Exit full screen (Esc)",
        "Modalità lettura — Esc per uscire": "Reading mode — Esc to exit",
        "Schermo intero — Esc per uscire": "Full screen — Esc to exit",
        "Schermo intero": "Full screen",
        "Il documento chiede di essere mostrato a schermo intero.": "The document asks to be shown full screen.",
        "Il documento chiede di essere mostrato a schermo intero. Procedere?":
            "The document asks to be shown in full screen. Continue?",
        "{s:g} s per pagina": "{s:g} s per page",
        "{v:g} px/s": "{v:g} px/s",
        "Scorrimento automatico: {speed}": "Auto scroll: {speed}",
        "in pausa (Spazio per riprendere)": "paused (Space to resume)",
        "Scorrimento automatico terminato: fine del documento": "Auto scroll finished: end of document",
    })
    return ReadingController(ctx)
