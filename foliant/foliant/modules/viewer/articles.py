"""Thread di articoli (VIS-23): elenco degli articoli e lettura riquadro per riquadro."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

TRANSLATIONS = {
    "Articoli": "Articles",
    "Il documento non contiene articoli.": "The document contains no articles.",
    "Nessun documento aperto": "No document open",
    "Articolo {n}": "Article {n}",
    "{n} riquadri": "{n} boxes",
    "Invio: riquadro successivo · Maiusc+Invio: precedente · Esc: fine": "Enter: next box · Shift+Enter: previous · Esc: stop",
    "Fine dell'articolo": "End of article",
    "Inizio dell'articolo": "Start of article",
    "Lettura dell'articolo terminata": "Article reading ended",
}

_REF = re.compile(r"(\d+)\s+0\s+R")


@dataclass
class Article:
    title: str
    beads: list[tuple[int, pymupdf.Rect]] = field(default_factory=list)   # (pagina, riquadro in coordinate pagina)


def _ref(value: str) -> int | None:
    m = _REF.search(value or "")
    return int(m.group(1)) if m else None


def read_articles(pdf: pymupdf.Document) -> list[Article]:
    """Articoli da `/Threads` (da chiamare con ENGINE_LOCK). Catene circolari e
    riquadri su pagine inesistenti sono gestiti senza blocchi."""
    try:
        kind, value = pdf.xref_get_key(pdf.pdf_catalog(), "Threads")
    except Exception:
        return []
    if kind not in ("array", "xref"):
        return []
    if kind == "xref":
        value = pdf.xref_object(_ref(value) or 0)
    page_of = {pdf[i].xref: i for i in range(pdf.page_count)}
    articles = []
    for n, m in enumerate(_REF.finditer(value), start=1):
        thread = int(m.group(1))
        title = ""
        try:
            k, info = pdf.xref_get_key(thread, "I/Title")
            if k == "string":
                title = info
        except Exception:
            pass
        art = Article(title or _("Articolo {n}").format(n=n))
        first = _ref(pdf.xref_get_key(thread, "F")[1])
        bead, seen = first, set()
        while bead and bead not in seen and len(seen) < 10000:
            seen.add(bead)
            page = page_of.get(_ref(pdf.xref_get_key(bead, "P")[1]) or -1)
            k, r = pdf.xref_get_key(bead, "R")
            if page is not None and k == "array":
                try:
                    nums = [float(x) for x in r.strip("[]").split()]
                    pg = pdf[page]
                    rect = pymupdf.Rect(nums) * pg.transformation_matrix
                    if pg.rotation:
                        rect = rect * pg.derotation_matrix
                    rect.normalize()
                    rect &= pg.mediabox    # riquadri fuori pagina limitati alla pagina
                    art.beads.append((page, rect))
                except (ValueError, TypeError):
                    pass
            bead = _ref(pdf.xref_get_key(bead, "N")[1])
        if art.beads:
            articles.append(art)
    return articles


class ArticlesPanel(QWidget):
    def __init__(self, controller: "ArticlesController"):
        super().__init__()
        self.c = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(_("Articoli"))
        title.setProperty("heading", True)
        title.setContentsMargins(12, 10, 12, 6)
        layout.addWidget(title)
        self.hint = QLabel(_("Invio: riquadro successivo · Maiusc+Invio: precedente · Esc: fine"))
        self.hint.setProperty("muted", True)
        self.hint.setWordWrap(True)
        self.hint.setContentsMargins(12, 0, 12, 6)
        layout.addWidget(self.hint)
        self.empty = QLabel()
        self.empty.setProperty("muted", True)
        self.empty.setWordWrap(True)
        self.empty.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(self.empty)
        self.list = QListWidget(self)
        self.list.itemActivated.connect(lambda it: self.c.start(self.list.row(it)))
        self.list.itemClicked.connect(lambda it: self.c.start(self.list.row(it)))
        layout.addWidget(self.list, 1)
        layout.addStretch(0)

    def set_view(self, view) -> None:
        self.c.set_view(view)

    def fill(self, articles: list[Article], has_view: bool) -> None:
        self.list.clear()
        for a in articles:
            item = QListWidgetItem(f"{a.title}\n{_('{n} riquadri').format(n=len(a.beads))}")
            self.list.addItem(item)
        self.empty.setText(_("Il documento non contiene articoli.") if has_view else _("Nessun documento aperto"))
        self.empty.setVisible(not articles)
        self.list.setVisible(bool(articles))
        self.hint.setVisible(bool(articles))


class ArticlesController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.view: DocumentView | None = None
        self.articles: list[Article] = []
        self.reading: tuple[int, int] | None = None   # (articolo, riquadro)
        self.panel = ArticlesPanel(self)
        ctx.add_sidebar_panel("articles", _("Articoli"), "newspaper", self.panel, order=60)

    def set_view(self, view) -> None:
        if self.view is not None and self.view is not view:
            try:
                self.view.removeEventFilter(self)
                self.view.contentChanged.disconnect(self.reload)
            except (RuntimeError, TypeError):
                pass
        self.view = view
        self.reading = None
        if view is not None:
            view.installEventFilter(self)
            view.contentChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        if self.view is None or self.view.document.closed:
            self.articles = []
        else:
            with ENGINE_LOCK:
                try:
                    self.articles = read_articles(self.view.document.pdf)
                except Exception:
                    self.articles = []
        self.reading = None
        self.panel.fill(self.articles, self.view is not None)

    def start(self, index: int) -> None:
        if self.view is None or not (0 <= index < len(self.articles)):
            return
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        if nav is not None:
            nav.record_jump(self.view)
        self.reading = (index, 0)
        self._show()
        self.view.setFocus()

    def _show(self) -> None:
        a, b = self.reading
        page, rect = self.articles[a].beads[b]
        view = self.view
        if page >= view.page_count:
            return
        nav = getattr(self.ctx, "viewer_parts", {}).get("navigation")
        st = nav.state(view) if nav is not None else None
        if st is not None:
            st.suppress += 1
        try:
            from foliant.ui.document_view import PX_PER_PT
            margin = 24
            zoom = (view.viewport().width() - 2 * margin) / max(1.0, rect.width * PX_PER_PT)
            view.set_zoom(zoom)
            view.go_to_page(page)
            p = view.page_transform(page).map(QPointF(rect.x0, rect.y0))
            view.horizontalScrollBar().setValue(int(view.horizontalScrollBar().value() + p.x() - margin))
            view.verticalScrollBar().setValue(int(view.verticalScrollBar().value() + p.y() - margin))
        finally:
            if st is not None:
                st.suppress -= 1

    def step(self, delta: int) -> bool:
        if self.reading is None:
            return False
        a, b = self.reading
        nb = b + delta
        if not (0 <= nb < len(self.articles[a].beads)):
            self.ctx.status(_("Fine dell'articolo") if delta > 0 else _("Inizio dell'articolo"))
            return False
        self.reading = (a, nb)
        self._show()
        return True

    def stop(self) -> None:
        if self.reading is not None:
            self.reading = None
            self.ctx.status(_("Lettura dell'articolo terminata"))

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.view and self.reading is not None and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.step(-1 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1)
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self.stop()
                return True
        return False


def install(ctx: Context) -> ArticlesController:
    add_translations("en", TRANSLATIONS)
    return ArticlesController(ctx)
