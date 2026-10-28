"""Modalità riflusso (VIS-36): il testo delle pagine in un'unica colonna che va a
capo secondo la larghezza della finestra, con titoli riconosciuti dalla
dimensione del carattere e dimensione del testo regolabile (Ctrl++ / Ctrl+-).
All'uscita la vista normale riparte dal paragrafo corrispondente."""

from __future__ import annotations

import html
from collections import Counter
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QEvent, QObject, Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, theme

TRANSLATIONS = {
    "Riflusso": "Reflow",
    "Mostra il testo in una colonna adattata alla finestra": "Show the text in a column fitted to the window",
    "Esci dal riflusso": "Exit reflow",
    "Riflusso non disponibile: il documento non contiene testo. Esegui l'OCR per renderlo leggibile.":
        "Reflow not available: the document contains no text. Run OCR to make it readable.",
    "Pagina {n}": "Page {n}",
    "Vista": "View",
}


@dataclass
class Block:
    page: int
    kind: str          # "h1", "h2", "p"
    text: str
    rect: pymupdf.Rect


def extract_blocks(pdf: pymupdf.Document) -> list[Block]:
    """Paragrafi e titoli in ordine di lettura (da chiamare con ENGINE_LOCK)."""
    raw: list[tuple[int, float, str, pymupdf.Rect]] = []
    sizes: Counter = Counter()
    for i in range(pdf.page_count):
        try:
            data = pdf[i].get_text("dict", sort=True)
        except Exception:
            continue
        for b in data.get("blocks", []):
            if b.get("type", 0) != 0:
                continue
            lines = []
            size = 0.0
            for line in b.get("lines", []):
                text = "".join(s.get("text", "") for s in line.get("spans", []))
                if text.strip():
                    lines.append(text.strip())
                    for s in line.get("spans", []):
                        sizes[round(s.get("size", 0), 1)] += len(s.get("text", ""))
                        size = max(size, s.get("size", 0))
            if not lines:
                continue
            # ricompone le parole sillabate a fine riga
            text = ""
            for ln in lines:
                if text.endswith("-") and len(text) > 1 and text[-2].isalpha() and ln[:1].isalpha():
                    text = text[:-1] + ln
                else:
                    text = (text + " " + ln) if text else ln
            raw.append((i, size, text, pymupdf.Rect(b["bbox"])))
    if not raw:
        return []
    body = sizes.most_common(1)[0][0] if sizes else 10.0
    heading_sizes = sorted({round(sz, 1) for _p, sz, t, _r in raw if sz >= body * 1.2 and len(t) <= 160},
                           reverse=True)
    out = []
    for page, size, text, rect in raw:
        kind = "p"
        if len(text) <= 160 and round(size, 1) in heading_sizes:
            kind = "h1" if round(size, 1) == heading_sizes[0] else "h2"
        out.append(Block(page, kind, text, rect))
    return out


def blocks_html(blocks: list[Block], font_pt: float) -> str:
    text_c, muted = theme.hex_color("text"), theme.hex_color("text_muted")
    parts = [f"<html><body style='font-size:{font_pt:.1f}pt; color:{text_c};'>"]
    last_page = -1
    for n, b in enumerate(blocks):
        if b.page != last_page:
            parts.append(f"<p style='color:{muted}; font-size:{font_pt * 0.8:.1f}pt;'>"
                         f"<a name='page{b.page}'></a>{html.escape(_('Pagina {n}').format(n=b.page + 1))}</p>")
            last_page = b.page
        anchor = f"<a name='b{n}'></a>"
        if b.kind == "h1":
            parts.append(f"<h1>{anchor}{html.escape(b.text)}</h1>")
        elif b.kind == "h2":
            parts.append(f"<h2>{anchor}{html.escape(b.text)}</h2>")
        else:
            parts.append(f"<p>{anchor}{html.escape(b.text)}</p>")
    parts.append("</body></html>")
    return "".join(parts)


class ReflowWidget(QWidget):
    def __init__(self, controller: "ReflowController"):
        super().__init__()
        self.c = controller
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        bar.setContentsMargins(12, 6, 12, 6)
        self.title = QLabel(_("Riflusso"))
        self.title.setProperty("heading", True)
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.exit = QPushButton(_("Esci dal riflusso"))
        self.exit.clicked.connect(controller.leave)
        bar.addWidget(self.exit)
        layout.addLayout(bar)
        self.browser = QTextBrowser(self)
        self.browser.setOpenLinks(False)
        self.browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.browser.setLineWrapMode(QTextBrowser.LineWrapMode.WidgetWidth)
        self.browser.document().setDocumentMargin(24)
        layout.addWidget(self.browser, 1)


class ReflowController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.view: DocumentView | None = None
        self.blocks: list[Block] = []
        self.font_pt = 12.0
        self.widget = ReflowWidget(self)
        self.act = ctx.action(_("Riflusso"), "wrap-text", self.toggle, checkable=True,
                              tooltip=_("Mostra il testo in una colonna adattata alla finestra"))
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_action(self.act)
        ctx.add_menu_action(self.act, _("Vista"))
        for seq, delta in ((QKeySequence.StandardKey.ZoomIn, 1), ("Ctrl+=", 1), (QKeySequence.StandardKey.ZoomOut, -1)):
            sc = QShortcut(QKeySequence(seq), self.widget)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(lambda d=delta: self.change_font(d))
        esc = QShortcut(QKeySequence("Escape"), self.widget)
        esc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.leave)
        ctx.currentViewChanged.connect(lambda _v: self.active and self.leave(restore=False))

    @property
    def active(self) -> bool:
        return self.ctx.central_widget() is self.widget

    def toggle(self, on: bool | None = None) -> None:
        if on is None:
            on = not self.active
        if on:
            self.enter()
        else:
            self.leave()

    def enter(self) -> bool:
        view = self.ctx.current_view()
        if view is None:
            self.act.setChecked(False)
            return False
        with ENGINE_LOCK:
            blocks = extract_blocks(view.document.pdf)
        if not blocks:
            self.act.setChecked(False)
            self.ctx.status(_("Riflusso non disponibile: il documento non contiene testo. Esegui l'OCR per renderlo leggibile."), 8000)
            return False
        self.view = view
        self.blocks = blocks
        self.render()
        self.ctx.set_central_widget(self.widget)
        self.act.setChecked(True)
        # posizione corrispondente alla vista: primo blocco della pagina corrente
        self.scroll_to_page(view.current_page)
        self.widget.browser.setFocus()
        return True

    def render(self) -> None:
        bar = self.widget.browser.verticalScrollBar()
        pos = bar.value()
        self.widget.browser.setHtml(blocks_html(self.blocks, self.font_pt))
        bar.setValue(pos)

    def change_font(self, delta: int) -> None:
        self.font_pt = max(7.0, min(40.0, self.font_pt * (1.15 if delta > 0 else 1 / 1.15)))
        self.render()

    def scroll_to_page(self, page: int) -> None:
        self.widget.browser.scrollToAnchor(f"page{page}")

    def current_block(self) -> int | None:
        """Primo blocco in cima all'area di lettura (il più vicino sopra il bordo)."""
        b = self.widget.browser
        top = b.verticalScrollBar().value() + 4
        layout = b.document().documentLayout()
        best = None
        block = b.document().begin()
        while block.isValid():
            y = layout.blockBoundingRect(block).top()
            it = block.begin()
            while not it.atEnd():
                for name in it.fragment().charFormat().anchorNames():
                    if name.startswith("b") and name[1:].isdigit():
                        if y <= top or best is None:
                            best = int(name[1:])
                it += 1
            if y > top and best is not None:
                break
            block = block.next()
        return best

    def leave(self, restore: bool = True) -> None:
        if not self.active:
            self.act.setChecked(False)
            return
        idx = self.current_block() if restore else None
        self.ctx.set_central_widget(None)
        self.act.setChecked(False)
        view = self.view
        if restore and view is not None and idx is not None and idx < len(self.blocks) and not view.document.closed:
            blk = self.blocks[idx]
            view.go_to_page(blk.page)
            view.ensure_visible(blk.page, blk.rect)
        if view is not None:
            view.setFocus()


def install(ctx: Context) -> ReflowController:
    add_translations("en", TRANSLATIONS)
    return ReflowController(ctx)
