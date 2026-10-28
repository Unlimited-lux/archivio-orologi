"""Schermata della mappa concettuale / scaletta (AI-12): consultazione, modifica, esportazione, inserimento."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from foliant.api import FoliantError, _, atomic_write, bind_icon

from . import mindmap
from .mindmap_view import MindMapView


class MindMapScreen(QWidget):
    def __init__(self, ctx, root: mindmap.Node, document, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.document = document
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        bar = QHBoxLayout()
        title = QLabel(_("Mappa concettuale"))
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        bar.addWidget(title)
        bar.addStretch(1)
        self.export_format = QComboBox()
        for fmt, text in (("png", "Immagine PNG"), ("svg", "Immagine SVG"), ("md", "Scaletta Markdown"),
                          ("json", "JSON"), ("pdf", "PDF")):
            self.export_format.addItem(_(text), fmt)
        export = QPushButton(_("Esporta…"))
        bind_icon(export, "download")
        export.clicked.connect(lambda: self.export(self.export_format.currentData()))
        self.position = QComboBox()
        self.position.addItem(_("all'inizio"), "start")
        self.position.addItem(_("alla fine"), "end")
        insert = QPushButton(_("Inserisci nel documento"))
        bind_icon(insert, "file-plus")
        insert.clicked.connect(lambda: self.insert(self.position.currentData()))
        close = QPushButton(_("Chiudi"))
        bind_icon(close, "x")
        close.clicked.connect(self.close_screen)
        for w in (self.export_format, export, self.position, insert, close):
            bar.addWidget(w)
        lay.addLayout(bar)
        self.view = MindMapView(self)
        self.view.set_tree(root)
        self.view.pageRequested.connect(self.go_to_page)
        lay.addWidget(self.view, 1)
        self.status = QLabel("")
        lay.addWidget(self.status)

    def tree(self) -> mindmap.Node:
        return self.view.tree()

    def close_screen(self) -> None:
        self.ctx.set_central_widget(None)

    def go_to_page(self, page: int) -> None:
        self.close_screen()
        view = self.ctx.current_view()
        if view is not None and view.document is self.document and 0 <= page < view.page_count:
            view.go_to_page(page)

    def export_bytes(self, fmt: str) -> bytes:
        root = self.tree()
        if fmt == "png":
            return mindmap.render_png(root)
        if fmt == "svg":
            return mindmap.to_svg(root).encode("utf-8")
        if fmt == "md":
            return mindmap.to_markdown_outline(root).encode("utf-8")
        if fmt == "json":
            return mindmap.to_json(root).encode("utf-8")
        import pymupdf
        pdf = pymupdf.open()
        mindmap.insert_page(pdf, root, at=0, size=(842, 595))
        data = pdf.tobytes()
        pdf.close()
        return data

    def export(self, fmt: str, path: str | None = None) -> None:
        filters = {"png": "PNG (*.png)", "svg": "SVG (*.svg)", "md": "Markdown (*.md)", "json": "JSON (*.json)",
                   "pdf": "PDF (*.pdf)"}
        if path is None:
            path = self.ctx.ask_save_path(_("Esporta mappa"), f"mappa.{fmt}", filters[fmt], f".{fmt}")
        if not path:
            return
        try:
            atomic_write(path, self.export_bytes(fmt))
        except FoliantError as e:
            self.ctx.error(_("Esporta mappa"), str(e))
            return
        self.status.setText(_("Esportata in {p}").format(p=path))

    def insert(self, where: str = "start") -> int | None:
        doc = self.document
        if doc is None or doc.closed:
            self.ctx.error(_("Mappa concettuale"), _("Il documento d'origine non è più aperto."))
            return None
        root = self.tree()
        with doc.change(_("Inserisci mappa concettuale")) as pdf:
            at = 0 if where == "start" else pdf.page_count
            like = pdf[0 if where == "start" else pdf.page_count - 1].rect
            index = mindmap.insert_page(pdf, root, at=at, size=(like.width, like.height))
        self.status.setText(_("Mappa inserita come pagina {n}.").format(n=index + 1))
        return index
