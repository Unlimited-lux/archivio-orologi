"""Barre di avviso del documento (UX-14) e contenitore delle schede documento.

Ogni scheda documento è una `DocumentPage`: in alto la `BannerBar` (un avviso alla volta,
in ordine di importanza, con «N altri avvisi»), sotto la `DocumentView`. Le barre stanno
quindi sopra la pagina senza coprirne il contenuto e restano legate alla propria scheda.

`DocumentTabs` nasconde il contenitore: `widget(i)`, `currentWidget()`, `indexOf(view)` e
`setCurrentWidget(view)` lavorano direttamente con le viste, come prima del contenitore.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTabBar, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..i18n import _
from . import theme
from .document_view import DocumentView
from .icons import bind_icon, icon

LEVELS = ("info", "warning", "error")
_LEVEL_RANK = {"info": 0, "warning": 1, "error": 2}
_LEVEL_ICON = {"info": "info", "warning": "triangle-alert", "error": "octagon-alert"}
_LEVEL_COLOR = {"info": "accent", "warning": "warning", "error": "danger"}
_seq = itertools.count()


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """Colore intermedio fra `a` (t=0) e `b` (t=1)."""
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t))


@dataclass
class Banner:
    """Un avviso del documento (vedi `Context.show_banner`)."""

    id: str
    text: str
    actions: list = field(default_factory=list)      # (etichetta, callback, principale)
    level: str = "info"
    priority: int = 0
    closable: bool = True
    on_close: Callable | None = None
    seq: int = 0
    dismissed: bool = False                          # chiuso dall'utente: non ricompare

    def sort_key(self) -> tuple:
        return (-self.priority, -_LEVEL_RANK[self.level], self.seq)


def _normalize_actions(actions) -> list[tuple[str, Callable, bool]]:
    """(etichetta, callback) o (etichetta, callback, principale); la prima azione è principale."""
    out = []
    for i, a in enumerate(actions or ()):
        label, callback = a[0], a[1]
        primary = bool(a[2]) if len(a) > 2 else i == 0
        out.append((str(label), callback, primary))
    return out


class BannerBar(QFrame):
    """Barra sopra la pagina: un avviso alla volta per priorità, con «N altri avvisi»."""

    changed = Signal()          # avvisi aggiunti, tolti, chiusi o ripristinati

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("documentBanner")
        self.setAutoFillBackground(False)
        self._banners: dict[str, Banner] = {}
        self._current: str | None = None
        self._restyling = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 5, 6, 5)
        lay.setSpacing(8)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(18, 18)
        lay.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text_label = QLabel(self)
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)     # testi dei documenti: mai HTML
        self.text_label.setWordWrap(True)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.text_label, 1)
        self._actions_box = QHBoxLayout()
        self._actions_box.setSpacing(6)
        lay.addLayout(self._actions_box)
        self.action_buttons: list[QPushButton] = []
        self.more_button = QToolButton(self)
        self.more_button.setObjectName("bannerMore")
        self.more_button.setAutoRaise(True)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.clicked.connect(self.show_next)
        lay.addWidget(self.more_button)
        self.close_button = QToolButton(self)
        self.close_button.setAutoRaise(True)
        bind_icon(self.close_button, "x")
        self.close_button.setToolTip(_("Chiudi l'avviso"))
        self.close_button.clicked.connect(self.dismiss_current)
        lay.addWidget(self.close_button)
        self.hide()

    # ------------------------------------------------------------------ elenco
    def show_banner(self, banner_id: str, text: str, actions=(), level: str = "info", priority: int = 0,
                    closable: bool = True, on_close: Callable | None = None) -> Banner:
        """Aggiunge o aggiorna un avviso. Un avviso chiuso dall'utente resta chiuso."""
        level = level if level in _LEVEL_RANK else "info"
        old = self._banners.get(banner_id)
        banner = Banner(banner_id, str(text), _normalize_actions(actions), level, int(priority), bool(closable),
                        on_close, old.seq if old is not None else next(_seq),
                        old.dismissed if old is not None else False)
        self._banners[banner_id] = banner
        if old is None:
            self._current = None            # avviso nuovo: si mostra di nuovo il più importante
        self._refresh()
        self.changed.emit()
        return banner

    def hide_banner(self, banner_id: str) -> bool:
        """Toglie l'avviso (anche se chiuso): una nuova `show_banner` lo mostrerà di nuovo."""
        if self._banners.pop(banner_id, None) is None:
            return False
        if self._current == banner_id:
            self._current = None
        self._refresh()
        self.changed.emit()
        return True

    def banner(self, banner_id: str) -> Banner | None:
        return self._banners.get(banner_id)

    def active(self) -> list[Banner]:
        """Avvisi da mostrare, dal più importante."""
        return sorted((b for b in self._banners.values() if not b.dismissed), key=Banner.sort_key)

    def dismissed(self) -> list[Banner]:
        return sorted((b for b in self._banners.values() if b.dismissed), key=Banner.sort_key)

    def current(self) -> Banner | None:
        active = self.active()
        for b in active:
            if b.id == self._current:
                return b
        return active[0] if active else None

    # ------------------------------------------------------------------ comandi
    def show_next(self) -> None:
        """Mostra l'avviso successivo (clic su «N altri avvisi»)."""
        active = self.active()
        if len(active) < 2:
            return
        index = active.index(self.current())
        self._current = active[(index + 1) % len(active)].id
        self._refresh()

    def dismiss(self, banner_id: str) -> bool:
        banner = self._banners.get(banner_id)
        if banner is None or banner.dismissed or not banner.closable:
            return False
        banner.dismissed = True
        if self._current == banner_id:
            self._current = None
        self._refresh()
        self.changed.emit()
        if banner.on_close is not None:
            self._run(banner.on_close)
        return True

    def dismiss_current(self) -> None:
        current = self.current()
        if current is not None:
            self.dismiss(current.id)

    def restore(self, banner_id: str) -> bool:
        """Mostra di nuovo un avviso chiuso (dall'icona nella barra di stato)."""
        banner = self._banners.get(banner_id)
        if banner is None:
            return False
        banner.dismissed = False
        self._current = banner_id
        self._refresh()
        self.changed.emit()
        return True

    def _run(self, callback: Callable) -> None:
        try:
            callback()
        except Exception as e:          # un modulo difettoso non deve chiudere l'applicazione
            win = self.window()
            status = getattr(win, "statusBar", None)
            if callable(status):
                status().showMessage(_("Errore in un avviso: {e}").format(e=e), 8000)

    # ------------------------------------------------------------------ aspetto
    def _refresh(self) -> None:
        current = self.current()
        for btn in self.action_buttons:
            btn.hide()
            btn.deleteLater()
        self.action_buttons = []
        if current is None:
            self._current = None
            self.hide()
            return
        self._current = current.id
        self.text_label.setText(current.text)
        for label, callback, primary in current.actions:
            btn = QPushButton(label, self)
            if primary:
                btn.setProperty("primary", True)
            btn.clicked.connect(lambda _c=False, f=callback: self._run(f))
            self._actions_box.addWidget(btn)
            self.action_buttons.append(btn)
        others = [b for b in self.active() if b is not current]
        self.more_button.setVisible(bool(others))
        if others:
            n = len(others)
            self.more_button.setText(_("1 altro avviso") if n == 1 else _("{n} altri avvisi").format(n=n))
            self.more_button.setToolTip("\n".join("• " + b.text for b in others))
        self.close_button.setVisible(current.closable)
        self._restyle()
        self.show()

    def level_color(self) -> QColor:
        current = self.current()
        return theme.color(_LEVEL_COLOR[current.level if current is not None else "info"])

    def background_color(self) -> QColor:
        """Fondo tenue nel colore del livello, ricalcolato dal tema attivo."""
        return mix(theme.color("surface"), self.level_color(), 0.18 if theme.is_dark() else 0.12)

    def _restyle(self) -> None:
        if self._restyling:
            return
        self._restyling = True
        try:
            current = self.current()
            level = current.level if current is not None else "info"
            self.icon_label.setPixmap(icon(_LEVEL_ICON[level], color=theme.hex_color(_LEVEL_COLOR[level])).pixmap(18, 18))
            self.more_button.setStyleSheet(f"QToolButton#bannerMore {{ color: {theme.hex_color('accent')}; "
                                           "text-decoration: underline; }")
            self.update()
        finally:
            self._restyling = False

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange) and self.isVisible():
            self._restyle()             # cambio di tema chiaro/scuro

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), self.background_color())
        p.fillRect(QRectF(0, 0, 3, self.height()), self.level_color())
        p.fillRect(QRectF(0, self.height() - 1, self.width(), 1), theme.color("border"))
        p.end()


class DocumentPage(QWidget):
    """Pagina di una scheda: le barre di avviso sopra la vista documento."""

    def __init__(self, view: DocumentView, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("documentPage")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.banners = BannerBar(self)
        lay.addWidget(self.banners)
        lay.addWidget(view, 1)
        self.view = view
        self.setFocusProxy(view)


class DocumentTabBar(QTabBar):
    """Barra delle schede: il clic destro chiede il menu della scheda alla finestra."""

    menuRequested = Signal(int, QPoint)     # indice della scheda, posizione globale

    def contextMenuEvent(self, ev) -> None:
        index = self.tabAt(ev.pos())
        if index >= 0:
            self.menuRequested.emit(index, ev.globalPos())
            ev.accept()


class DocumentTabs(QTabWidget):
    """Schede dei documenti. Ogni pagina è una `DocumentPage`, ma i metodi che accettano o
    restituiscono un widget lavorano con la `DocumentView` (compatibilità con i moduli)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setTabBar(DocumentTabBar(self))

    @staticmethod
    def page(view: QWidget | None) -> DocumentPage | None:
        """Contenitore (barre di avviso + vista) di `view`, o None (anche per una vista già
        distrutta, es. callback di un'attività in background dopo la chiusura della scheda)."""
        if not isinstance(view, DocumentView):
            return None
        try:
            parent = view.parentWidget()
        except RuntimeError:            # oggetto C++ già eliminato
            return None
        return parent if isinstance(parent, DocumentPage) else None

    def _wrap(self, widget: QWidget) -> QWidget:
        if isinstance(widget, DocumentView):
            return self.page(widget) or DocumentPage(widget)
        return widget

    def _inner(self, widget: QWidget | None) -> QWidget | None:
        return self.page(widget) or widget

    def addTab(self, widget, *args) -> int:
        return super().addTab(self._wrap(widget), *args)

    def insertTab(self, index, widget, *args) -> int:
        return super().insertTab(index, self._wrap(widget), *args)

    def widget(self, index):
        w = super().widget(index)
        return w.view if isinstance(w, DocumentPage) else w

    def currentWidget(self):
        w = super().currentWidget()
        return w.view if isinstance(w, DocumentPage) else w

    def indexOf(self, widget) -> int:
        try:
            return super().indexOf(self._inner(widget))
        except RuntimeError:            # vista già distrutta: non è (più) in una scheda
            return -1

    def setCurrentWidget(self, widget) -> None:
        super().setCurrentWidget(self._inner(widget))
