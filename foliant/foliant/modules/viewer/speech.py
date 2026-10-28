"""Lettura ad alta voce (VIS-35).

Vista → Leggi ad alta voce → Questa pagina / Da qui alla fine / Selezione (anche
dal menu contestuale). La lettura procede frase per frase (sintesi progressiva:
si preparano solo le frasi della pagina in lettura), evidenzia la frase corrente
con un livello della vista e la porta in vista; a fine pagina prosegue con la
successiva in modalità «fino alla fine». Una piccola barra in basso, visibile
solo durante la lettura, offre riproduci/pausa, interrompi, frase precedente e
successiva, velocità (0,5×–3×) e voce.

Il motore si sceglie con `speech_engine.detect_engine()`; senza motore le azioni
sono disattivate con l'indicazione di cosa installare. Nei test si inietta un
`FakeEngine` con `controller.set_engine(...)`.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QComboBox, QLabel, QMenu, QToolBar

from foliant.api import ENGINE_LOCK, _, add_translations, bind_icon, language, theme

from .speech_engine import (
    SPEEDS, Sentence, SpeechEngine, build_sentences, catalog_language, clamp_rate, detect_engine,
    primary_language, reading_allowed, sentence_index_at, structure_languages,
)

SETTINGS_PREFIX = "viewer/speech/"
MODE_PAGE = "page"
MODE_TO_END = "to_end"
MODE_SELECTION = "selection"

NO_ENGINE_TEXT = "Installa speech-dispatcher o espeak-ng per la lettura ad alta voce"

_EN = {
    "Vista": "View",
    "Leggi ad alta voce": "Read aloud",
    "Leggi questa pagina": "Read this page",
    "Leggi da qui alla fine": "Read to the end",
    "Leggi la selezione": "Read selection",
    "Leggi la selezione ad alta voce": "Read selection aloud",
    "Leggi ad alta voce da qui": "Read aloud from here",
    NO_ENGINE_TEXT: "Install speech-dispatcher or espeak-ng to read aloud",
    "Riproduci": "Play",
    "Pausa": "Pause",
    "Interrompi la lettura": "Stop reading",
    "Frase precedente": "Previous sentence",
    "Frase successiva": "Next sentence",
    "Velocità di lettura": "Reading speed",
    "Voce": "Voice",
    "Voce automatica": "Automatic voice",
    "Voce automatica (lingua del documento)": "Automatic voice (document language)",
    "Lettura ad alta voce": "Read aloud",
    "Pagina {page} · frase {n} di {total}": "Page {page} · sentence {n} of {total}",
    "In pausa": "Paused",
    "Nessun testo da leggere a pagina {page}: è una pagina scansionata? "
    "Esegui l'OCR (modalità OCR) per renderla leggibile.":
        "No text to read on page {page}: is it a scanned page? Run OCR (OCR mode) to make it readable.",
    "Nessun testo da leggere nella selezione.": "No text to read in the selection.",
    "La lettura ad alta voce non è consentita dai permessi del documento.":
        "Reading aloud is not allowed by the document permissions.",
    "Nessuna voce per la lingua «{lang}»: uso la voce predefinita.":
        "No voice for language “{lang}”: using the default voice.",
    "Lettura terminata.": "Reading finished.",
    "Errore del motore vocale: {msg}": "Speech engine error: {msg}",
    "Segui il cambio di pagina durante la lettura": "Follow page changes while reading",
}


def _speed_label(v: float) -> str:
    text = f"{v:g}"
    if language() != "en":
        text = text.replace(".", ",")
    return text + "×"


class SpeechController(QObject):
    def __init__(self, ctx, engine: SpeechEngine | None = None):
        super().__init__(ctx)
        self.ctx = ctx
        self.engine: SpeechEngine | None = None
        self.view = None
        self.mode = MODE_PAGE
        self.page = 0
        self.sentences: list[Sentence] = []
        self.index = 0
        self.playing = False         # sessione di lettura attiva
        self.paused = False
        self._internal_nav = False
        self._lang_cache: dict = {}
        self._warned_langs: set[str] = set()
        self.last_message = ""
        s = ctx.settings()
        self.rate = clamp_rate(float(s.value(SETTINGS_PREFIX + "rate", 1.0)))
        self.pitch = float(s.value(SETTINGS_PREFIX + "pitch", 0.0))
        self.voice: str | None = s.value(SETTINGS_PREFIX + "voice", "") or None
        self.follow_page = str(s.value(SETTINGS_PREFIX + "follow_page", "true")).lower() in ("true", "1")
        self._build_actions()
        self._build_toolbar()
        ctx.add_view_menu_provider(self._menu_provider)
        ctx.currentViewChanged.connect(self._on_current_view_changed)
        ctx.documentClosed.connect(self._on_document_closed)
        theme.on_change(self._repaint)
        self.set_engine(engine if engine is not None else detect_engine(self))

    # ================================================================ motore
    def set_engine(self, engine: SpeechEngine) -> None:
        if self.playing:
            self.stop()
        if self.engine is not None:
            for sig, slot in ((self.engine.finished, self._on_finished), (self.engine.failed, self._on_failed),
                              (self.engine.voicesChanged, self._fill_voices)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.engine = engine
        engine.finished.connect(self._on_finished)
        engine.failed.connect(self._on_failed)
        engine.voicesChanged.connect(self._fill_voices)
        self._fill_voices()
        self.update_actions()

    def engine_available(self) -> bool:
        return self.engine is not None and self.engine.available()

    # ================================================================ interfaccia
    def _build_actions(self) -> None:
        ctx = self.ctx
        self.menu = QMenu(_("Leggi ad alta voce"), ctx.window)
        self.act_menu = self.menu.menuAction()
        bind_icon(self.act_menu, "volume-2")
        self.act_page = ctx.action(_("Leggi questa pagina"), "file-volume", self.read_page, requires_document=False)
        self.act_to_end = ctx.action(_("Leggi da qui alla fine"), "book-audio", self.read_to_end,
                                     requires_document=False)
        self.act_selection = ctx.action(_("Leggi la selezione"), "speech", self.read_selection,
                                        requires_document=False)
        self.act_follow = QAction(_("Segui il cambio di pagina durante la lettura"), self.menu)
        self.act_follow.setCheckable(True)
        self.act_follow.setChecked(self.follow_page)
        self.act_follow.toggled.connect(self._set_follow)
        for act in (self.act_page, self.act_to_end, self.act_selection):
            self.menu.addAction(act)
        self.menu.addSeparator()
        self.menu.addAction(self.act_follow)
        self.menu.setToolTipsVisible(True)
        ctx.add_menu_action(self.act_menu, _("Vista"))

    def _build_toolbar(self) -> None:
        win = self.ctx.window
        tb = QToolBar(_("Lettura ad alta voce"), win)
        tb.setObjectName("speechToolbar")
        tb.setMovable(False)
        tb.setFloatable(False)
        self.toolbar = tb
        title = QLabel(_("Lettura ad alta voce"))
        title.setObjectName("speechTitle")
        title.setContentsMargins(10, 0, 6, 0)
        tb.addWidget(title)
        tb.addSeparator()
        self.act_prev = QAction(_("Frase precedente"), tb)
        bind_icon(self.act_prev, "skip-back")
        self.act_prev.triggered.connect(self.previous_sentence)
        self.act_play = QAction(_("Pausa"), tb)
        bind_icon(self.act_play, "pause")
        self.act_play.triggered.connect(self.toggle_pause)
        self.act_next = QAction(_("Frase successiva"), tb)
        bind_icon(self.act_next, "skip-forward")
        self.act_next.triggered.connect(self.next_sentence)
        self.act_stop = QAction(_("Interrompi la lettura"), tb)
        bind_icon(self.act_stop, "square")
        self.act_stop.triggered.connect(self.stop)
        for a in (self.act_prev, self.act_play, self.act_next, self.act_stop):
            tb.addAction(a)
        tb.addSeparator()
        self.speed_box = QComboBox()
        self.speed_box.setToolTip(_("Velocità di lettura"))
        for v in SPEEDS:
            self.speed_box.addItem(_speed_label(v), v)
        self._select_speed(self.rate)
        self.speed_box.currentIndexChanged.connect(lambda _i: self.set_rate(self.speed_box.currentData()))
        tb.addWidget(self.speed_box)
        self.voice_box = QComboBox()
        self.voice_box.setToolTip(_("Voce"))
        self.voice_box.setMinimumContentsLength(14)
        self.voice_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.voice_box.currentIndexChanged.connect(self._on_voice_chosen)
        tb.addWidget(self.voice_box)
        tb.addSeparator()
        self.position_label = QLabel("")
        self.position_label.setObjectName("speechPosition")
        tb.addWidget(self.position_label)
        win.addToolBar(Qt.ToolBarArea.BottomToolBarArea, tb)
        tb.hide()

    def _select_speed(self, rate: float) -> None:
        best = min(range(len(SPEEDS)), key=lambda i: abs(SPEEDS[i] - rate))
        self.speed_box.blockSignals(True)
        self.speed_box.setCurrentIndex(best)
        self.speed_box.blockSignals(False)

    def _fill_voices(self) -> None:
        box = self.voice_box
        box.blockSignals(True)
        box.clear()
        box.addItem(_("Voce automatica"), "")
        box.setItemData(0, _("Voce automatica (lingua del documento)"), Qt.ItemDataRole.ToolTipRole)
        for v in (self.engine.voices() if self.engine else []):
            label = f"{v.name} ({v.language})" if v.language and v.language != v.name else v.name
            box.addItem(label, v.id)
        i = box.findData(self.voice or "")
        box.setCurrentIndex(max(0, i))
        box.blockSignals(False)

    def _on_voice_chosen(self, _i: int) -> None:
        self.set_voice(self.voice_box.currentData() or None)

    def update_actions(self) -> None:
        has_view = self.ctx.current_view() is not None
        ok = self.engine_available()
        tip = _(NO_ENGINE_TEXT) if not ok else ""
        for act in (self.act_page, self.act_to_end, self.act_selection):
            act.setEnabled(ok and has_view)
            act.setToolTip(tip or act.text())
            act.setStatusTip(tip)
        self.act_menu.setEnabled(ok)
        self.act_menu.setToolTip(tip or _("Leggi ad alta voce"))
        self.act_menu.setStatusTip(tip)
        self.menu.setToolTip(tip)

    # ================================================================ preferenze
    def set_rate(self, rate: float) -> None:
        self.rate = clamp_rate(rate)
        self._select_speed(self.rate)
        self.ctx.settings().setValue(SETTINGS_PREFIX + "rate", self.rate)
        if self.playing and not self.paused:
            self._speak_current()

    def set_voice(self, voice: str | None) -> None:
        self.voice = voice or None
        self.ctx.settings().setValue(SETTINGS_PREFIX + "voice", voice or "")
        i = self.voice_box.findData(voice or "")
        if i >= 0 and i != self.voice_box.currentIndex():
            self.voice_box.blockSignals(True)
            self.voice_box.setCurrentIndex(i)
            self.voice_box.blockSignals(False)
        if self.playing and not self.paused:
            self._speak_current()

    def _set_follow(self, on: bool) -> None:
        self.follow_page = bool(on)
        self.ctx.settings().setValue(SETTINGS_PREFIX + "follow_page", "true" if on else "false")

    # ================================================================ avvio
    def _check_start(self):
        view = self.ctx.current_view()
        if view is None:
            return None
        if not self.engine_available():
            self._message(_(NO_ENGINE_TEXT))
            return None
        perms = view.document.permissions   # permessi effettivi del nucleo (-1 = tutto consentito)
        if not reading_allowed(perms):
            self._message(_("La lettura ad alta voce non è consentita dai permessi del documento."))
            return None
        self.stop()
        return view

    def read_page(self, page: int | None = None) -> bool:
        view = self._check_start()
        if view is None:
            return False
        return self._start(view, MODE_PAGE, view.current_page if page is None else page)

    def read_to_end(self, page: int | None = None, point: pymupdf.Point | None = None) -> bool:
        view = self._check_start()
        if view is None:
            return False
        return self._start(view, MODE_TO_END, view.current_page if page is None else page, point)

    def read_selection(self) -> bool:
        view = self.ctx.current_view()
        sel = view.text_selection if view is not None else None
        words = list(sel.words) if sel is not None else []
        if view is not None and not words:
            self._message(_("Nessun testo da leggere nella selezione."))
            return False
        view = self._check_start()
        if view is None:
            return False
        self._attach(view, MODE_SELECTION)
        self.page = sel.page
        self.sentences = build_sentences(sel.page, words)
        self.index = 0
        if not self.sentences:
            self._message(_("Nessun testo da leggere nella selezione."))
            self.stop()
            return False
        self._speak_current()
        return True

    def _attach(self, view, mode: str) -> None:
        self.view = view
        self.mode = mode
        self.playing = True
        self.paused = False
        if self._overlay not in view.overlays:
            view.overlays.append(self._overlay)
        view.currentPageChanged.connect(self._on_page_changed)
        self.toolbar.show()
        self._update_play_action()

    def _start(self, view, mode: str, page: int, point: pymupdf.Point | None = None) -> bool:
        self._attach(view, mode)
        if not self._load_page(page, skip_empty=(mode == MODE_TO_END)):
            self.stop()
            return False
        if point is not None:
            self.index = sentence_index_at(self.sentences, point)
        self._speak_current()
        return True

    def _load_page(self, page: int, skip_empty: bool) -> bool:
        """Prepara le frasi di `page` (e, se `skip_empty`, delle successive vuote)."""
        view = self.view
        first_empty = None
        while 0 <= page < view.page_count:
            sentences = build_sentences(page, view.words(page))
            if sentences:
                self.page, self.sentences, self.index = page, sentences, 0
                if first_empty is not None:
                    self._ocr_hint(first_empty)
                return True
            if first_empty is None:
                first_empty = page
            if not skip_empty:
                break
            page += 1
        self._ocr_hint(first_empty if first_empty is not None else page)
        return False

    def _ocr_hint(self, page: int) -> None:
        self._message(_("Nessun testo da leggere a pagina {page}: è una pagina scansionata? "
                        "Esegui l'OCR (modalità OCR) per renderla leggibile.").format(page=page + 1))

    # ================================================================ lingua
    def language_for(self, page: int) -> str:
        view = self.view or self.ctx.current_view()
        fallback = language() or "it"
        if view is None:
            return fallback
        doc = view.document
        key = (id(doc), doc.version)
        if self._lang_cache.get("key") != key:
            with ENGINE_LOCK:
                try:
                    struct = structure_languages(doc.pdf)
                except Exception:
                    struct = {}
                catalog = catalog_language(doc.pdf)
            self._lang_cache = {"key": key, "struct": struct, "catalog": catalog}
        return self._lang_cache["struct"].get(page) or self._lang_cache["catalog"] or fallback

    def _voice_for(self, lang: str) -> str | None:
        if self.voice:
            return self.voice
        voices = self.engine.voices() if self.engine else []
        want = primary_language(lang)
        if voices and want and not any(primary_language(v.language) == want for v in voices):
            if want not in self._warned_langs:
                self._warned_langs.add(want)
                self._message(_("Nessuna voce per la lingua «{lang}»: uso la voce predefinita.").format(lang=lang))
        return None

    # ================================================================ lettura
    def current_sentence(self) -> Sentence | None:
        if self.playing and 0 <= self.index < len(self.sentences):
            return self.sentences[self.index]
        return None

    def _speak_current(self) -> None:
        s = self.current_sentence()
        if s is None:
            return
        lang = self.language_for(s.page)
        self.engine.stop()
        self.engine.speak(s.text, lang, self.rate, self.pitch, self._voice_for(lang))
        self._show_sentence(s)

    def _show_sentence(self, s: Sentence) -> None:
        view = self.view
        if view is None:
            return
        bbox = s.bbox
        vr = view.page_to_view(s.page, bbox)
        vp = view.viewport().rect()
        self._internal_nav = True
        try:
            if not QRectF(vp).contains(vr) or s.page != view.current_page and s.page not in view.visible_pages():
                rect = s.rects[0] if vr.height() > vp.height() * 0.8 else bbox
                view.ensure_visible(s.page, rect)
        finally:
            self._internal_nav = False
        self.position_label.setText(_("Pagina {page} · frase {n} di {total}").format(
            page=s.page + 1, n=self.index + 1, total=len(self.sentences)))
        view.viewport().update()

    def _on_finished(self) -> None:
        if not self.playing or self.paused:
            return
        if self.index + 1 < len(self.sentences):
            self.index += 1
            self._speak_current()
            return
        if self.mode == MODE_TO_END and self.page + 1 < self.view.page_count:
            if self._load_page(self.page + 1, skip_empty=True):
                self._speak_current()
                return
        self.stop()
        self._message(_("Lettura terminata."))

    def _on_failed(self, msg: str) -> None:
        if self.playing:
            self.stop()
            self._message(_("Errore del motore vocale: {msg}").format(msg=msg))

    def toggle_pause(self) -> None:
        if not self.playing:
            return
        if self.paused:
            self.paused = False
            self._speak_current()
        else:
            self.paused = True
            self.engine.stop()
            self.position_label.setText(_("In pausa"))
        self._update_play_action()

    def _update_play_action(self) -> None:
        if self.paused:
            self.act_play.setText(_("Riproduci"))
            bind_icon(self.act_play, "play")
        else:
            self.act_play.setText(_("Pausa"))
            bind_icon(self.act_play, "pause")
        self.act_play.setToolTip(self.act_play.text())

    def next_sentence(self) -> None:
        if not self.playing:
            return
        if self.index + 1 < len(self.sentences):
            self.index += 1
        elif self.mode != MODE_SELECTION and self.page + 1 < self.view.page_count:
            if not self._load_page(self.page + 1, skip_empty=True):
                return
        else:
            return
        self.paused = False
        self._update_play_action()
        self._speak_current()

    def previous_sentence(self) -> None:
        if not self.playing:
            return
        if self.index > 0:
            self.index -= 1
        elif self.mode != MODE_SELECTION and self.page > 0:
            p = self.page - 1
            while p >= 0:
                sentences = build_sentences(p, self.view.words(p))
                if sentences:
                    self.page, self.sentences, self.index = p, sentences, len(sentences) - 1
                    break
                p -= 1
        self.paused = False
        self._update_play_action()
        self._speak_current()

    def stop(self) -> None:
        was = self.playing
        self.playing = False
        self.paused = False
        if self.engine is not None and was:
            self.engine.stop()
        view, self.view = self.view, None
        if view is not None:
            try:
                view.currentPageChanged.disconnect(self._on_page_changed)
            except (RuntimeError, TypeError):
                pass
            if self._overlay in view.overlays:
                view.overlays.remove(self._overlay)
            view.viewport().update()
        self.sentences = []
        self.index = 0
        self.toolbar.hide()

    # ================================================================ eventi
    def _on_page_changed(self, page: int) -> None:
        if self._internal_nav or not self.playing or not self.follow_page or self.mode == MODE_SELECTION:
            return
        if page == self.page:
            return
        if self._load_page(page, skip_empty=(self.mode == MODE_TO_END)):
            self.paused = False
            self._update_play_action()
            self._speak_current()

    def _on_current_view_changed(self, view) -> None:
        if self.playing and view is not self.view:
            self.stop()
        self.update_actions()

    def _on_document_closed(self, view) -> None:
        if view is self.view:
            self.stop()

    def _menu_provider(self, view, pe, menu: QMenu) -> None:
        ok = self.engine_available()
        tip = "" if ok else _(NO_ENGINE_TEXT)
        if view.selected_text():
            act = menu.addAction(_("Leggi la selezione ad alta voce"), self.read_selection)
        elif pe.page is not None and ok:   # senza selezione la voce compare solo con un motore vocale
            act = menu.addAction(_("Leggi ad alta voce da qui"),
                                 lambda p=pe.page, pt=pe.point: self.read_to_end(p, pt))
        else:
            return
        bind_icon(act, "volume-2")
        act.setEnabled(ok)
        if tip:
            act.setToolTip(tip)
            act.setStatusTip(tip)
            menu.setToolTipsVisible(True)

    # ================================================================ disegno
    def highlight_color(self) -> QColor:
        c = QColor(theme.color("accent"))
        c.setAlpha(70)
        return c

    def _overlay(self, view, painter, page: int) -> None:
        s = self.current_sentence()
        if s is None or s.page != page:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.highlight_color())
        for r in s.rects:
            painter.drawRoundedRect(QRectF(r.x0 - 1.5, r.y0 - 1, r.width + 3, r.height + 2), 2, 2)
        line = QColor(theme.color("accent"))
        painter.setBrush(line)
        first = s.rects[0]
        painter.drawRect(QRectF(first.x0 - 5, first.y0, 2, first.height))

    def _repaint(self) -> None:
        if self.view is not None:
            self.view.viewport().update()

    def _message(self, text: str) -> None:
        self.last_message = text
        self.ctx.status(text, 8000)


def install(ctx, engine: SpeechEngine | None = None) -> SpeechController:
    add_translations("en", _EN)
    return SpeechController(ctx, engine)
