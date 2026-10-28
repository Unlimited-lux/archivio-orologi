"""Estrazione di tutte le immagini (OBJ-28) e strumento Cattura (OBJ-29)."""

from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import QByteArray, QMimeData, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMenu, QSpinBox,
)

from foliant.api import ENGINE_LOCK, Tool, _, atomic_write, theme

from . import capture as CAP
from . import extract as X
from .dialogs import parse_range
from .tool import view_scale


# ------------------------------------------------------------------ OBJ-28
class ExtractDialog(QDialog):
    def __init__(self, parent, count: int):
        super().__init__(parent)
        self.setWindowTitle(_("Estrai tutte le immagini"))
        self.count = count
        lay = QFormLayout(self)
        self.pages = QLineEdit(f"1-{count}")
        self.fmt = QComboBox()
        for code, text in (("original", _("Originale")), ("png", "PNG"), ("jpeg", "JPEG")):
            self.fmt.addItem(text, code)
        self.min_size = QSpinBox()
        self.min_size.setRange(0, 10000)
        self.min_size.setValue(32)
        self.min_size.setSuffix(" px")
        self.inline = QCheckBox(_("Includi le immagini inline"))
        self.inline.setChecked(True)
        self.dedup = QCheckBox(_("Elimina i duplicati"))
        self.dedup.setChecked(True)
        lay.addRow(_("Pagine:"), self.pages)
        lay.addRow(_("Formato:"), self.fmt)
        lay.addRow(_("Dimensione minima:"), self.min_size)
        lay.addRow("", self.inline)
        lay.addRow("", self.dedup)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def options(self) -> X.ExtractOptions:
        m = self.min_size.value()
        return X.ExtractOptions(pages=parse_range(self.pages.text(), self.count), fmt=self.fmt.currentData(),
                                min_width=m, min_height=m, include_inline=self.inline.isChecked(),
                                dedup=self.dedup.isChecked())


def ask_extract_options(parent, count: int) -> X.ExtractOptions | None:
    dlg = ExtractDialog(parent, count)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.options()


def extract_all(ctx, controller, view) -> X.ExtractReport | None:
    if view is None:
        return None
    if not controller.extract_allowed(view):
        ctx.error(_("Estrai tutte le immagini"), _("Il documento non consente di estrarre il contenuto."))
        return None
    opts = ask_extract_options(ctx.window, view.document.page_count)
    if opts is None:
        return None
    with ENGINE_LOCK:
        jobs = X.plan(view.document.pdf, opts)
    if not jobs:
        ctx.info(_("Estrai tutte le immagini"), _("Il documento non contiene immagini."))
        return None
    folder = ctx.ask_directory(_("Cartella di destinazione"))
    if not folder:
        return None
    base = os.path.splitext(view.document.display_name)[0]

    def work(progress):
        return X.run(view.document.pdf, opts, folder, base, progress=progress.update, lock=ENGINE_LOCK)
    try:
        report = ctx.run_task(_("Estrazione delle immagini"), work)
    except X.ExtractError as e:
        report = e.report
        ctx.error(_("Estrai tutte le immagini"), str(e))
    except Exception as e:
        ctx.error(_("Estrai tutte le immagini"), _("Operazione non riuscita: {e}").format(e=e))
        return None
    if report is None:
        return None
    lines = [_("Immagini estratte: {n}").format(n=len(report.written))]
    if report.duplicates:
        lines.append(_("Duplicati non ripetuti: {n}").format(n=report.duplicates))
    if report.skipped:
        lines.append(_("Saltate: {n}").format(n=len(report.skipped)))
        lines += [f"  • {d}: {r}" for d, r in report.skipped[:10]]
    if report.cancelled or report.not_processed:
        lines.append(_("Non elaborate: {n}").format(n=report.not_processed))
    ctx.info(_("Estrai tutte le immagini"), "\n".join(lines))
    return report


# ------------------------------------------------------------------ OBJ-29
DPI_CHOICES = (0, 150, 300, 600)   # 0 = risoluzione dello schermo


class CaptureTool(Tool):
    """Traccia un rettangolo (anche su più pagine) e copia/salva/inserisce l'area."""

    id = "capture"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, ctx, controller):
        self.ctx = ctx
        self.c = controller
        self.drag: dict | None = None
        self.dpi = 0

    def activate(self, view):
        self.ctx.status(_("Cattura: traccia un rettangolo sull'area da copiare."))

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self.drag = {"page": ev.page, "start": ev.point, "end_page": ev.page, "end": ev.point}
        return True

    def move(self, view, ev):
        if self.drag is None:
            return False
        hit = view.view_to_page(ev.pos, clamp=True)
        if hit is not None:
            self.drag["end_page"], self.drag["end"] = hit
        view.viewport().update()
        return True

    def release(self, view, ev):
        d, self.drag = self.drag, None
        if d is None:
            return False
        parts = self.parts(view, d)
        if not parts:
            return True
        s = view_scale(view, d["page"])
        r0 = parts[0][1]
        if len(parts) == 1 and (r0.width * s < 2 or r0.height * s < 2):
            return True
        self.show_menu(view, parts, ev)
        return True

    @staticmethod
    def parts(view, d) -> list[tuple[int, pymupdf.Rect]]:
        a, b = d["page"], d["end_page"]
        pa, pb = d["start"], d["end"]
        if a == b:
            r = pymupdf.Rect(pa, pb)
            r.normalize()
            return [(a, r)]
        if a > b:
            a, b, pa, pb = b, a, pb, pa
        out = []
        x0, x1 = min(pa.x, pb.x), max(pa.x, pb.x)
        for i in range(a, b + 1):
            w, h = view.page_size(i)
            with ENGINE_LOCK:
                pr = view.document.pdf[i].cropbox
                ph = pr.height
            del w, h
            top = pa.y if i == a else 0
            bottom = pb.y if i == b else ph
            r = pymupdf.Rect(x0, top, x1, bottom)
            if r.height > 0:
                out.append((i, r))
        return out

    def show_menu(self, view, parts, ev) -> None:
        menu = QMenu(view)
        allowed = self.c.extract_allowed(view)
        a = menu.addAction(_("Copia immagine"), lambda: self.copy_image(view, parts))
        a.setEnabled(allowed)
        a = menu.addAction(_("Salva come…"), lambda: self.save_image(view, parts))
        a.setEnabled(allowed)
        if len(parts) == 1:
            menu.addAction(_("Inserisci come immagine"), lambda: self.insert_image(view, parts))
            t = menu.addAction(_("Copia testo"), lambda: self.copy_text(view, parts))
            t.setEnabled(allowed)
        res = menu.addMenu(_("Risoluzione"))
        for dpi in DPI_CHOICES:
            act = res.addAction(_("Schermo") if dpi == 0 else f"{dpi} ppi", lambda d=dpi: setattr(self, "dpi", d))
            act.setCheckable(True)
            act.setChecked(self.dpi == dpi)
        view.exec_menu(menu, view.viewport().mapToGlobal(ev.pos.toPoint()))
        menu.deleteLater()

    def effective_dpi(self, view) -> int:
        return self.dpi or max(36, int(round(72 * view_scale(view, view.current_page))))

    def render(self, view, parts) -> bytes:
        dpi = self.effective_dpi(view)
        with ENGINE_LOCK:
            if len(parts) == 1:
                png, used, reduced = CAP.render_area(view.document.pdf, parts[0][0], parts[0][1], dpi)
            else:
                png, used, reduced = CAP.render_area_multi(view.document.pdf, parts, dpi)
        if reduced:
            self.ctx.status(_("Area molto grande: risoluzione ridotta a {d} ppi.").format(d=used))
        return png

    def copy_image(self, view, parts) -> None:
        try:
            png = self.render(view, parts)
        except ValueError:
            return
        mime = QMimeData()
        mime.setImageData(QImage.fromData(png))
        if len(parts) == 1:
            with ENGINE_LOCK:
                text = CAP.area_text(view.document.pdf, parts[0][0], parts[0][1])
            if text:
                mime.setText(text)
        QGuiApplication.clipboard().setMimeData(mime)
        self.ctx.status(_("Area copiata negli appunti."))

    def save_image(self, view, parts) -> None:
        path = self.ctx.ask_save_path(_("Salva area come"), "cattura.png", "PNG (*.png);;JPEG (*.jpg *.jpeg)",
                                      default_suffix=".png")
        if not path:
            return
        try:
            png = self.render(view, parts)
            if path.lower().endswith((".jpg", ".jpeg")):
                pix = pymupdf.Pixmap(png)
                if pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)
                png = pix.tobytes("jpeg", jpg_quality=90)
            atomic_write(path, png)
        except Exception as e:
            self.ctx.error(_("Salva area come"), _("Impossibile salvare l'immagine: {e}").format(e=e))

    def insert_image(self, view, parts) -> None:
        png = self.render(view, parts)
        page, rect = parts[0]
        self.c.insert_image_bytes(view, [png], page, rect=rect)

    def copy_text(self, view, parts) -> None:
        with ENGINE_LOCK:
            text = CAP.area_text(view.document.pdf, parts[0][0], parts[0][1])
        if not text:
            self.ctx.info(_("Copia testo"), _("L'area non contiene testo: usa il riconoscimento del testo (OCR)."))
            return
        QGuiApplication.clipboard().setText(text)
        self.ctx.status(_("Testo copiato negli appunti."))

    def paint(self, view, painter: QPainter, page: int) -> None:
        d = self.drag
        if d is None:
            return
        for i, r in self.parts(view, d):
            if i != page:
                continue
            s = view_scale(view, page)
            fill = QColor(theme.color("accent"))
            fill.setAlpha(35)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(theme.color("accent"), 1.2 / s, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))
