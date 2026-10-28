"""Navigazione: cronologia Indietro/Avanti (VIS-13), Vai a pagina con etichette
e stato dei comandi di pagina (VIS-12), anteprima e azioni dei collegamenti (VIS-19).

Soluzione locale (vedi CORE_REQUESTS.md): la vista non segnala i salti, quindi
il controller avvolge `go_to_page` di ogni vista per registrare la posizione di
partenza dei salti non continui.
"""

from __future__ import annotations

import os
import re
import weakref
from pathlib import Path

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPointF, Qt, QTimer
from PySide6.QtWidgets import QInputDialog, QLabel, QToolTip

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

from .engine import NavigationHistory, Position, page_labels, resolve_page_input

TRANSLATIONS = {
    "Indietro": "Back",
    "Avanti": "Forward",
    "Torna alla posizione precedente": "Go back to the previous position",
    "Vai alla posizione successiva": "Go forward to the next position",
    "Vai a pagina…": "Go to page…",
    "Vai a pagina": "Go to page",
    "Numero o etichetta di pagina (# per il numero fisico, 1–{n}):":
        "Page number or label (# for the physical number, 1–{n}):",
    "Pagina «{t}» non trovata: il documento ha {n} pagine.": "Page “{t}” not found: the document has {n} pages.",
    "{label} ({n} di {total})": "{label} ({n} of {total})",
    "Vai a pagina {n}": "Go to page {n}",
    "Vai a pagina {label} ({n})": "Go to page {label} ({n})",
    "Apri {f}": "Open {f}",
    "Apri {f}, pagina {n}": "Open {f}, page {n}",
    "Apri {f} su «{d}»": "Open {f} at “{d}”",
    "Avvio di un programma: bloccato per sicurezza": "Launch a program: blocked for safety",
    "Il collegamento chiede di avviare un programma: azione bloccata per sicurezza.":
        "The link asks to launch a program: action blocked for safety.",
    "Comando: {n}": "Command: {n}",
    "Allegato incorporato": "Embedded attachment",
    "File collegato non trovato: {p}": "Linked file not found: {p}",
    "Destinazione non trovata": "Destination not found",
    "Vista": "View",
    "Rileva URL nel testo": "Detect URLs in text",
    "Rende cliccabili indirizzi web ed e-mail scritti come semplice testo":
        "Makes web and e-mail addresses written as plain text clickable",
}

_URL_RE = re.compile(r"^(?:(?:https?://|www\.)[^\s<>\"]+|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)$", re.IGNORECASE)


def detect_url(word: str) -> str | None:
    """URI per una parola che sembra un indirizzo web o e-mail (None altrimenti)."""
    w = word.strip().strip("()[]<>«»\"'")
    w = w.rstrip(".,;:!?")
    if not w or not _URL_RE.match(w):
        return None
    if "@" in w and "://" not in w and not w.lower().startswith("www."):
        return "mailto:" + w
    if w.lower().startswith("www."):
        return "https://" + w
    return w


def link_order(links: list[dict], tabs: str | None) -> list[int]:
    """Ordine di tabulazione dei collegamenti di una pagina: `/Tabs /C` per colonne,
    `/S` o `/A` nell'ordine delle annotazioni, altrimenti ordine di lettura (righe)."""
    idx = list(range(len(links)))
    t = (tabs or "").strip("/")
    if t in ("S", "A"):
        return idx
    def r(i):
        return pymupdf.Rect(links[i]["from"])
    if t == "C":
        return sorted(idx, key=lambda i: (round(r(i).x0 / 10), r(i).y0))
    return sorted(idx, key=lambda i: (round(r(i).y0 / 6), r(i).x0))


def describe_link(link: dict, labels: list[str] | None = None) -> str:
    """Testo del suggerimento con la destinazione di un collegamento."""
    kind = link.get("kind")
    if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and link.get("page", -1) >= 0:
        n = link["page"] + 1
        label = labels[link["page"]] if labels and link["page"] < len(labels) else ""
        if label and label != str(n):
            return _("Vai a pagina {label} ({n})").format(label=label, n=n)
        return _("Vai a pagina {n}").format(n=n)
    if kind == pymupdf.LINK_URI:
        return link.get("uri", "")
    if kind == pymupdf.LINK_GOTOR:
        f = link.get("file", "")
        if link.get("page", -1) is not None and link.get("page", -1) >= 0:
            return _("Apri {f}, pagina {n}").format(f=f, n=link["page"] + 1)
        if link.get("nameddest") or link.get("name"):
            return _("Apri {f} su «{d}»").format(f=f, d=link.get("nameddest") or link.get("name"))
        return _("Apri {f}").format(f=f)
    if kind == pymupdf.LINK_LAUNCH:
        return _("Avvio di un programma: bloccato per sicurezza")
    if kind == pymupdf.LINK_NAMED:
        return _("Comando: {n}").format(n=link.get("name", ""))
    return ""


def current_position(view: DocumentView) -> Position:
    """Posizione visibile (pagina, punto in alto a sinistra, zoom) della vista."""
    page = view.current_page
    if view.page_count == 0:
        return Position(0)
    inv, _ok = view.page_transform(page).inverted()
    p = inv.map(QPointF(0, 0))
    w, h = view.page_size(page)
    x = max(0.0, p.x())
    y = max(0.0, p.y())
    return Position(page, round(x, 2), round(y, 2), round(view.zoom, 4), view.fit_mode)


class _ViewState:
    def __init__(self) -> None:
        self.history = NavigationHistory()
        self.suppress = 0
        self.press_pos: QPointF | None = None
        self.labels: list[str] | None = None
        self.labels_version = -1
        self.last_goto: int = -1
        self.focus: tuple[int, int] | None = None   # (pagina, indice del collegamento)


class NavigationController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self._states: "weakref.WeakKeyDictionary[DocumentView, _ViewState]" = weakref.WeakKeyDictionary()
        w = ctx.window
        self.act_back = ctx.action(_("Indietro"), "arrow-left", self.back, shortcut="Alt+Left",
                                   tooltip=_("Torna alla posizione precedente") + " (Alt+←)",
                                   requires_document=False)
        self.act_forward = ctx.action(_("Avanti"), "arrow-right", self.forward, shortcut="Alt+Right",
                                      tooltip=_("Vai alla posizione successiva") + " (Alt+→)",
                                      requires_document=False)
        self.act_goto = ctx.action(_("Vai a pagina…"), "hash", self.ask_go_to, shortcut="Ctrl+G")
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_separator()
            mode.add_action(self.act_back)
            mode.add_action(self.act_forward)
        self.act_detect_urls = ctx.action(_("Rileva URL nel testo"), "link", self._set_detect_urls,
                                          tooltip=_("Rende cliccabili indirizzi web ed e-mail scritti come semplice testo"),
                                          checkable=True, requires_document=False)
        self.act_detect_urls.setChecked(self.detect_urls)
        for act in (self.act_back, self.act_forward, self.act_goto, self.act_detect_urls):
            ctx.add_menu_action(act, _("Vista"))
        self.label_box = QLabel(w)
        self.label_box.setProperty("muted", True)
        self.label_box.hide()
        w.statusBar().addPermanentWidget(self.label_box)
        ctx.add_link_handler(self._link_handler, priority=-100)
        ctx.documentOpened.connect(self._attach)
        ctx.currentViewChanged.connect(lambda _v: self._refresh_later())
        ctx.documentChanged.connect(self._on_content)
        for view in ctx.views():
            self._attach(view)
        self._refresh()

    # ------------------------------------------------------------------ viste
    def state(self, view: DocumentView) -> _ViewState:
        st = self._states.get(view)
        if st is None:
            st = _ViewState()
            self._states[view] = st
        return st

    def _attach(self, view: DocumentView) -> None:
        if getattr(view, "_viewer_nav", False):
            return
        view._viewer_nav = True
        st = self.state(view)
        original_go = view.go_to_page
        original_next = view.next_page
        original_prev = view.previous_page
        ref = weakref.ref(view)

        def go_to_page(index, point=None, _orig=original_go):
            v = ref()
            jumped = False
            if v is not None and not st.suppress and v.page_count:
                target = max(0, min(int(index), v.page_count - 1))
                if target != v.current_page:
                    st.history.record(current_position(v))
                    jumped = True
            result = _orig(index, point)
            if jumped and v is self.ctx.current_view():
                self._refresh()
            return result

        def sequential(fn):
            def wrapper(*a, **k):
                st.suppress += 1
                try:
                    return fn(*a, **k)
                finally:
                    st.suppress -= 1
            return wrapper

        view.go_to_page = go_to_page
        view.next_page = sequential(original_next)
        view.previous_page = sequential(original_prev)
        view.currentPageChanged.connect(lambda _p: self._refresh_later())
        view.viewport().installEventFilter(self)
        view.installEventFilter(self)
        view.overlays.append(self._paint_focus)

    def record_jump(self, view: DocumentView) -> None:
        """Da chiamare prima di un salto fatto senza `go_to_page` (es. zoom su un'area)."""
        self.state(view).history.record(current_position(view))
        self._refresh()

    # ----------------------------------------------------------- Indietro/Avanti
    def _restore(self, view: DocumentView, pos: Position) -> None:
        st = self.state(view)
        st.suppress += 1
        try:
            page = min(pos.page, view.page_count - 1)
            if pos.fit:
                if view.fit_mode != pos.fit:
                    view.set_fit(pos.fit)
            elif abs(view.zoom - pos.zoom) > 1e-4:
                view.set_zoom(pos.zoom)
            view.go_to_page(page)
            # il punto memorizzato torna nell'angolo in alto a sinistra
            p = view.page_transform(page).map(QPointF(pos.x, pos.y))
            vbar, hbar = view.verticalScrollBar(), view.horizontalScrollBar()
            if pos.y > 0:
                vbar.setValue(int(round(vbar.value() + p.y())))
            if pos.x > 0:
                hbar.setValue(int(round(hbar.value() + p.x())))
        finally:
            st.suppress -= 1
        self._refresh()

    def back(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            return False
        st = self.state(view)
        target = st.history.back(current_position(view))
        if target is None:
            return False
        self._restore(view, target)
        return True

    def forward(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            return False
        st = self.state(view)
        target = st.history.forward(current_position(view))
        if target is None:
            return False
        self._restore(view, target)
        return True

    # ------------------------------------------------------------ Vai a pagina
    def labels(self, view: DocumentView) -> list[str]:
        st = self.state(view)
        if st.labels is None or st.labels_version != view.document.version:
            with ENGINE_LOCK:
                st.labels = page_labels(view.document.pdf)
            st.labels_version = view.document.version
        return st.labels

    def go_to_text(self, text: str) -> bool:
        """Salta alla pagina indicata da numero o etichetta; False se non esiste."""
        view = self.ctx.current_view()
        if view is None:
            return False
        st = self.state(view)
        labels = self.labels(view)
        start_after = st.last_goto if st.last_goto >= 0 and labels.count(text.strip()) > 1 else -1
        index = resolve_page_input(text, labels, view.page_count, start_after)
        if index is None:
            return False
        st.last_goto = index
        view.go_to_page(index)
        return True

    def ask_go_to(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        text, ok = QInputDialog.getText(self.ctx.window, _("Vai a pagina"),
                                        _("Numero o etichetta di pagina (# per il numero fisico, 1–{n}):")
                                        .format(n=view.page_count))
        if not ok or not text.strip():
            return
        if not self.go_to_text(text):
            self.ctx.status(_("Pagina «{t}» non trovata: il documento ha {n} pagine.")
                            .format(t=text.strip(), n=view.page_count))

    # ------------------------------------------------------------------ stato
    def _on_content(self, view: DocumentView) -> None:
        st = self.state(view)
        st.labels = None
        st.history.clamp(view.page_count)
        self._refresh_later()

    def _refresh_later(self) -> None:
        QTimer.singleShot(0, self._refresh)

    def _refresh(self) -> None:
        view = self.ctx.current_view()
        w = self.ctx.window
        if view is None:
            self.act_back.setEnabled(False)
            self.act_forward.setEnabled(False)
            self.label_box.hide()
            return
        st = self.state(view)
        self.act_back.setEnabled(st.history.can_back)
        self.act_forward.setEnabled(st.history.can_forward)
        # comandi di pagina coerenti con la posizione (VIS-12)
        first = view.current_page <= 0
        last = view.current_page >= view.page_count - 1
        try:
            w.act_prev.setEnabled(not first)
            w.act_first.setEnabled(not first)
            w.act_next.setEnabled(not last)
            w.act_last.setEnabled(not last)
        except AttributeError:
            pass
        try:
            labels = self.labels(view)
        except Exception:
            labels = []
        label = labels[view.current_page] if view.current_page < len(labels) else ""
        if label and label != str(view.current_page + 1):
            self.label_box.setText(_("{label} ({n} di {total})").format(
                label=label, n=view.current_page + 1, total=view.page_count))
            self.label_box.show()
        else:
            self.label_box.hide()

    # ------------------------------------------------- collegamenti da tastiera
    @property
    def detect_urls(self) -> bool:
        return self.ctx.settings().value("viewer/detect_urls", False) in (True, "true")

    def _set_detect_urls(self, on: bool) -> None:
        self.ctx.settings().setValue("viewer/detect_urls", bool(on))

    def _ordered_links(self, view: DocumentView, page: int) -> list[dict]:
        links = view.links(page)
        if not links:
            return []
        with ENGINE_LOCK:
            try:
                kind, tabs = view.document.pdf.xref_get_key(view.document.pdf[page].xref, "Tabs")
            except Exception:
                tabs = None
        return [links[i] for i in link_order(links, tabs if tabs and tabs != "null" else None)]

    def focus_link(self, view: DocumentView, step: int) -> dict | None:
        """Sposta il fuoco sul collegamento successivo (step=1) o precedente (-1)."""
        st = self.state(view)
        n = view.page_count
        if st.focus is not None:
            page, i = st.focus
        else:
            page, i = view.current_page, (-1 if step > 0 else None)
        for _round in range(min(n + 1, 300)):   # limite: niente scansioni lunghissime
            links = self._ordered_links(view, page)
            if i is None:
                i = len(links)
            j = i + step
            if 0 <= j < len(links):
                st.focus = (page, j)
                link = links[j]
                view.ensure_visible(page, pymupdf.Rect(link["from"]))
                view.viewport().update()
                return link
            page = (page + step) % n
            i = -1 if step > 0 else None
        st.focus = None
        return None

    def focused_link(self, view: DocumentView) -> dict | None:
        st = self.state(view)
        if st.focus is None:
            return None
        page, i = st.focus
        links = self._ordered_links(view, page)
        return links[i] if i < len(links) else None

    def activate_link(self, view: DocumentView, link: dict) -> None:
        from foliant.api import follow_link
        follow_link(view, link)    # stessa catena di gestori (e controlli di sicurezza) del clic

    def _link_handler(self, view: DocumentView, link: dict) -> bool:
        """Gestore di ultima istanza per i collegamenti verso altri file e i comandi di avvio:
        i gestori a priorità più alta (es. Proteggi) possono bloccarli prima di arrivare qui."""
        try:
            if view not in self.ctx.views():
                return False      # vista di un'altra finestra: la gestisce il suo controller
        except RuntimeError:
            return False          # finestra già distrutta
        kind = link.get("kind")
        if kind == pymupdf.LINK_GOTOR and link.get("file"):
            QTimer.singleShot(0, lambda v=view, l=dict(link): self.open_remote(v, l))
            return True
        if kind == pymupdf.LINK_LAUNCH:
            view.status(_("Il collegamento chiede di avviare un programma: azione bloccata per sicurezza."))
            return True
        return False

    def _paint_focus(self, view, painter, page) -> None:
        st = self._states.get(view)
        if st is None or st.focus is None or st.focus[0] != page:
            return
        link = self.focused_link(view)
        if link is None:
            return
        from PySide6.QtGui import QPen
        from foliant.api import theme
        r = pymupdf.Rect(link["from"])
        pen = QPen(theme.color("accent"))
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        from PySide6.QtCore import QRectF
        painter.drawRect(QRectF(r.x0 - 1, r.y0 - 1, r.width + 2, r.height + 2))

    def _key(self, view: DocumentView, ev) -> bool:
        key = ev.key()
        if key == Qt.Key.Key_Backtab or (key == Qt.Key.Key_Tab and ev.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            return self.focus_link(view, -1) is not None
        if key == Qt.Key.Key_Tab and not ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return self.focus_link(view, 1) is not None
        st = self.state(view)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and st.focus is not None:
            link = self.focused_link(view)
            if link is not None:
                self.activate_link(view, link)
                return True
        if key == Qt.Key.Key_Escape and st.focus is not None:
            st.focus = None
            view.viewport().update()
        return False

    def _url_click(self, view: DocumentView, page: int, point) -> bool:
        if not self.detect_urls:
            return False
        word = view.word_at(page, point)
        uri = detect_url(word[4]) if word else None
        if uri is None:
            return False
        from foliant.api import follow_link
        QTimer.singleShot(0, lambda: follow_link(view, {"kind": pymupdf.LINK_URI, "uri": uri}))
        return True

    # --------------------------------------------------------------- eventi
    def _view_of(self, obj) -> DocumentView | None:
        parent = obj.parent() if obj is not None else None
        return parent if isinstance(parent, DocumentView) else None

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if isinstance(obj, DocumentView):
            if t == QEvent.Type.KeyPress and not obj.document.closed:
                try:
                    return self._key(obj, ev)
                except Exception as e:
                    obj.status(str(e))
            return False
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.ToolTip):
            return False
        view = self._view_of(obj)
        if view is None or view.document.closed:
            return False
        try:
            if t == QEvent.Type.MouseButtonPress:
                if ev.button() == Qt.MouseButton.BackButton:
                    self.back()
                    return True
                if ev.button() == Qt.MouseButton.ForwardButton:
                    self.forward()
                    return True
                if ev.button() == Qt.MouseButton.LeftButton:
                    self.state(view).press_pos = ev.position()
                return False
            if t == QEvent.Type.MouseButtonRelease and ev.button() == Qt.MouseButton.LeftButton:
                start = self.state(view).press_pos
                self.state(view).press_pos = None
                if start is None or (ev.position() - start).manhattanLength() > 3:
                    return False
                if view.tool is not view._default_tool:
                    return False
                hit = view.view_to_page(ev.position())
                if hit is None:
                    return False
                link = view.link_at(hit[0], hit[1])
                if link is None:
                    if not view.selected_text():
                        self._url_click(view, hit[0], hit[1])
                    return False
                # GoToR e Launch passano dalla catena dei gestori del nucleo (follow_link):
                # vedi `_link_handler`, registrato con priorità bassa.
                return False
            if t == QEvent.Type.ToolTip:
                hit = view.view_to_page(QPointF(ev.pos()))
                link = view.link_at(hit[0], hit[1]) if hit else None
                if link is None and hit and self.detect_urls:
                    word = view.word_at(hit[0], hit[1])
                    uri = detect_url(word[4]) if word else None
                    if uri:
                        link = {"kind": pymupdf.LINK_URI, "uri": uri}
                if link is None:
                    QToolTip.hideText()
                    return False
                text = describe_link(link, self.labels(view))
                if text:
                    QToolTip.showText(ev.globalPos(), text, view.viewport())
                    return True
        except Exception as e:  # un collegamento malformato non deve rompere la vista
            view.status(str(e))
        return False

    def open_remote(self, view: DocumentView, link: dict) -> DocumentView | None:
        """Collegamento verso un altro PDF: si apre in una nuova scheda sulla destinazione."""
        name = link.get("file", "")
        if not name:
            return None
        base = view.document.path.parent if view.document.path is not None else Path.cwd()
        target = Path(name)
        if not target.is_absolute():
            target = (base / name)
        target = Path(os.path.normpath(str(target)))
        if not target.is_file():
            self.ctx.error(_("Destinazione non trovata"), _("File collegato non trovato: {p}").format(p=target))
            return None
        new = self.ctx.open_path(str(target))
        if new is None:
            return None
        page = link.get("page", -1)
        dest = link.get("nameddest") or link.get("name")
        if (page is None or page < 0) and dest:
            try:
                with ENGINE_LOCK:
                    resolved = new.document.pdf.resolve_names().get(dest)
                page = resolved.get("page", -1) if resolved else -1
            except Exception:
                page = -1
            if page is None or page < 0:
                new.status(_("Destinazione non trovata"))
        if page is not None and page >= 0:
            to = link.get("to")
            st = self.state(new)
            st.suppress += 1
            try:
                new.go_to_page(page, to if isinstance(to, pymupdf.Point) and (to.x or to.y) else None)
            finally:
                st.suppress -= 1
        return new


def install(ctx: Context) -> NavigationController:
    add_translations("en", TRANSLATIONS)
    return NavigationController(ctx)
