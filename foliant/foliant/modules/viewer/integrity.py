"""File danneggiati (VIS-07): avviso non bloccante quando il motore ha dovuto
ricostruire il file, con il comando «Ripara e salva con nome»."""

from __future__ import annotations

import weakref

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton, QWidget

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, bind_icon, theme

TRANSLATIONS = {
    "Il file era danneggiato ed è stato ricostruito.": "The file was damaged and has been rebuilt.",
    "Ripara e salva con nome…": "Repair and save as…",
    "Chiudi l'avviso": "Close notice",
    "Funzioni del documento non supportate: {f}": "Document features not supported: {f}",
    "moduli XFA dinamici": "dynamic XFA forms",
    "contenuti 3D": "3D content",
    "contenuti multimediali": "multimedia content",
}


def unsupported_features(pdf) -> list[str]:
    """Funzioni presenti nel documento che l'applicazione non riproduce."""
    found = []
    try:
        cat = pdf.pdf_catalog()
        kind, _v = pdf.xref_get_key(cat, "AcroForm/XFA")
        if kind not in ("null", ""):
            found.append(_("moduli XFA dinamici"))
        subtypes = set()
        for i in range(min(pdf.page_count, 200)):
            for annot_xref in [a[0] for a in pdf[i].annot_xrefs()]:
                k, st = pdf.xref_get_key(annot_xref, "Subtype")
                subtypes.add(st)
        if "/3D" in subtypes:
            found.append(_("contenuti 3D"))
        if subtypes & {"/Movie", "/Sound", "/Screen", "/RichMedia"}:
            found.append(_("contenuti multimediali"))
    except Exception:
        pass
    return found


class InfoBar(QWidget):
    def __init__(self, text: str, action_text: str | None, action, parent=None):
        super().__init__(parent)
        self.setObjectName("viewerInfoBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QWidget#viewerInfoBar {{ background: {theme.hex_color('accent_soft')};"
            f" border-bottom: 1px solid {theme.hex_color('border')}; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 6, 6)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.button = None
        if action_text:
            self.button = QPushButton(action_text)
            self.button.setProperty("primary", True)
            self.button.clicked.connect(action)
            lay.addWidget(self.button)
        close = QToolButton()
        bind_icon(close, "x")
        close.setToolTip(_("Chiudi l'avviso"))
        close.setAutoRaise(True)
        close.clicked.connect(self.hide)
        lay.addWidget(close)


class IntegrityController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.bars: "weakref.WeakKeyDictionary[DocumentView, list[InfoBar]]" = weakref.WeakKeyDictionary()
        ctx.documentOpened.connect(self.check)

    def check(self, view: DocumentView) -> None:
        with ENGINE_LOCK:
            pdf = view.document.pdf
            repaired = bool(getattr(pdf, "is_repaired", False))
            features = unsupported_features(pdf)
        bars = []
        if repaired:
            bars.append(InfoBar(_("Il file era danneggiato ed è stato ricostruito."), _("Ripara e salva con nome…"),
                                lambda _c=False, v=view: self.repair(v), view))
        if features:
            bars.append(InfoBar(_("Funzioni del documento non supportate: {f}").format(f=", ".join(features)),
                                None, None, view))
        if not bars:
            return
        self.bars[view] = bars
        # le barre stanno in cima alla vista, sopra le pagine
        view.setViewportMargins(0, sum(b.sizeHint().height() for b in bars), 0, 0)
        y = 0
        for b in bars:
            b.setGeometry(0, y, view.width(), b.sizeHint().height())
            b.show()
            b.raise_()
            y += b.sizeHint().height()
            b.installEventFilter(self)
        view.installEventFilter(self)

    def repair(self, view: DocumentView) -> bool:
        w = self.ctx.window
        ok = w.save_view(view, save_as=True)
        if ok:
            for b in self.bars.pop(view, []):
                b.hide()
            self._layout(view)
        return ok

    def _layout(self, view: DocumentView) -> None:
        bars = [b for b in self.bars.get(view, []) if not b.isHidden()]
        y = 0
        for b in bars:
            b.setGeometry(0, y, view.width(), b.sizeHint().height())
            y += b.sizeHint().height()
        # un'eventuale barra della parte «reload» resta sotto le nostre
        reload = getattr(self.ctx, "viewer_parts", {}).get("reload")
        other = reload.bar(view) if reload is not None else None
        if other is not None and not other.isHidden():
            other.setGeometry(0, y, view.width(), other.sizeHint().height())
            y += other.sizeHint().height()
        view.setViewportMargins(0, y, 0, 0)

    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent
        if ev.type() in (QEvent.Type.Resize,) and isinstance(obj, DocumentView):
            QTimer.singleShot(0, lambda v=obj: self._layout(v))
        elif ev.type() == QEvent.Type.Hide and isinstance(obj, InfoBar):
            view = obj.parent()
            if isinstance(view, DocumentView):
                QTimer.singleShot(0, lambda v=view: self._layout(v))
        return False


def install(ctx: Context) -> IntegrityController:
    add_translations("en", TRANSLATIONS)
    return IntegrityController(ctx)
