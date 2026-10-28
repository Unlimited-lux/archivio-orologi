"""Interfaccia del modulo OCR: modalità, proposta all'apertura, esecuzione, revisione."""

from __future__ import annotations

import hashlib
import os
import time
import weakref
from dataclasses import dataclass, field

import pymupdf
from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import (
    ENGINE_LOCK, Context, OperationCancelled, Tool, _, atomic_write, icon, theme, try_engine,
)

from . import detect, engine, languages
from .dialogs import BatchDialog, LanguagesDialog, OcrDialog, ReportDialog, ReviewDialog, TextDialog, output_path
from .tess import OcrError

ANALYSIS_BUDGET_MS = 25      # tempo massimo di analisi per passo nel thread dell'interfaccia


# =================================================================== stato per vista
@dataclass
class ViewState:
    infos: dict[int, detect.PageInfo] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)
    version: int = -1
    dismissed: bool = False             # «Non ora»: per questa sessione
    bar: "ProposalBar | None" = None
    timer: QTimer | None = None
    placed: dict[int, list] = field(default_factory=dict)   # pagina → [(Rect, Word)] dopo l'OCR
    started: float = 0.0

    @property
    def detected(self) -> list[int]:
        return sorted(i for i, inf in self.infos.items() if inf.needs_ocr)

    @property
    def complete(self) -> bool:
        return not self.order


# =================================================================== barra proposta
class ProposalBar(QFrame):
    """Barra informativa non bloccante in cima alla vista del documento."""

    def __init__(self, view, controller: "OcrController"):
        super().__init__(view)
        self.setObjectName("ocrProposalBar")
        self.view = view
        self.controller = controller
        self.page: int | None = None   # proposta legata a una singola pagina
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(8)
        self.icon = QLabel(self)
        self.icon.setPixmap(icon("scan-text").pixmap(18, 18))
        lay.addWidget(self.icon)
        self.label = QLabel(self)
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.btn_run = QPushButton(_("Riconosci testo"), self)
        self.btn_run.setObjectName("ocrRun")
        self.btn_all = QPushButton(_("Tutte le pagine rilevate"), self)
        self.btn_details = QPushButton(_("Dettagli"), self)
        self.btn_later = QPushButton(_("Non ora"), self)
        self.btn_never = QPushButton(_("Non chiedere più per questo documento"), self)
        for b in (self.btn_run, self.btn_all, self.btn_details, self.btn_later, self.btn_never):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)
        self.btn_run.clicked.connect(self._run)
        self.btn_all.clicked.connect(lambda: controller.open_dialog(view, pages=controller.state(view).detected,
                                                                    mode=engine.EDITABLE))
        self.btn_details.clicked.connect(lambda: controller.show_details(view))
        self.btn_later.clicked.connect(lambda: controller.dismiss(view))
        self.btn_never.clicked.connect(lambda: controller.never_ask(view))
        self.apply_theme()
        view.installEventFilter(self)
        self.hide()

    def apply_theme(self) -> None:
        t = theme
        self.setStyleSheet(
            f"#ocrProposalBar {{ background: {t.hex_color('accent_soft')}; border: 1px solid {t.hex_color('border')};"
            f" border-radius: 8px; }}"
            f"#ocrProposalBar QLabel {{ color: {t.hex_color('text')}; background: transparent; border: none; }}"
            f"#ocrProposalBar QPushButton {{ padding: 4px 10px; }}"
            f"#ocrProposalBar QPushButton#ocrRun {{ background: {t.hex_color('accent')};"
            f" color: {t.hex_color('accent_text')}; border: none; border-radius: 6px; }}")

    def show_document(self, count: int, missing: list[str], restricted: bool, low_res: bool) -> None:
        self.page = None
        text = _("Questo documento contiene pagine scannerizzate senza testo ({n}).").format(n=count) if count != 1 \
            else _("Questo documento contiene 1 pagina scannerizzata senza testo.")
        text += " " + _("Riconosci il testo per cercarlo, copiarlo e modificarlo.")
        if low_res:
            text += " " + _("Alcune pagine hanno risoluzione bassa: accuratezza ridotta.")
        if missing:
            text += " " + _("Lingue predefinite non installate: {l}.").format(
                l=", ".join(languages.language_name(c) for c in missing))
        if restricted:
            text += " " + _("Il documento non consente modifiche: servirà la password del proprietario.")
        self.label.setText(text)
        self.btn_run.setText(_("Riconosci testo"))
        self.btn_all.hide()
        self.btn_details.show()
        self.btn_never.show()
        self._reveal()

    def show_page(self, page: int, others: int) -> None:
        self.page = page
        self.label.setText(_("La pagina {n} è un'immagine: il testo non si può selezionare né modificare. "
                             "Vuoi renderla modificabile con l'OCR?").format(n=page + 1))
        self.btn_run.setText(_("Rendi modificabile questa pagina"))
        self.btn_all.setVisible(others > 1)
        self.btn_details.hide()
        self.btn_never.hide()
        self._reveal()

    def _reveal(self) -> None:
        self._place()
        self.show()
        self.raise_()

    def _run(self) -> None:
        if self.page is not None:
            self.controller.open_dialog(self.view, pages=[self.page], mode=engine.EDITABLE)
        else:
            self.controller.open_dialog(self.view, pages=self.controller.state(self.view).detected)

    def _place(self) -> None:
        vp = self.view.viewport().geometry()
        margin = 12
        w = max(200, vp.width() - 2 * margin)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move(vp.x() + margin, vp.y() + 8)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.view and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False


# =================================================================== strumento area
class AreaOcrTool(Tool):
    """Trascina un rettangolo sulla pagina per riconoscerne il testo."""

    id = "ocr-area"
    cursor = Qt.CursorShape.CrossCursor

    def __init__(self, on_area):
        self.on_area = on_area
        self.page: int | None = None
        self.start: pymupdf.Point | None = None
        self.end: pymupdf.Point | None = None

    def press(self, view, ev):
        if ev.button != Qt.MouseButton.LeftButton or ev.page is None:
            return False
        self.page, self.start, self.end = ev.page, ev.point, ev.point
        return True

    def move(self, view, ev):
        if self.start is None:
            return False
        hit = view.view_to_page(ev.pos, clamp=True)
        if hit is not None and hit[0] == self.page:
            self.end = hit[1]
        else:
            self.end = view.clamp_to_page(self.page, ev.pos)
        view.viewport().update()
        return True

    def release(self, view, ev):
        if self.start is None:
            return False
        rect = self.rect()
        page = self.page
        self.page = self.start = self.end = None
        view.viewport().update()
        if rect is not None and rect.width > 4 and rect.height > 4:
            self.on_area(view, page, rect)
        return True

    def rect(self) -> pymupdf.Rect | None:
        if self.start is None or self.end is None:
            return None
        return pymupdf.Rect(self.start, self.end).normalize()

    def paint(self, view, painter, page):
        r = self.rect()
        if r is None or page != self.page:
            return
        c = QColor(theme.color("accent"))
        pen = QPen(c, 1.2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        c.setAlpha(40)
        painter.setBrush(QBrush(c))
        painter.drawRect(QRectF(r.x0, r.y0, r.width, r.height))


# =================================================================== pannello revisione
class ReviewPanel(QWidget):
    """Pannello destro: parole incerte del documento, con correzione nel documento."""

    def __init__(self, controller: "OcrController"):
        super().__init__()
        self.setObjectName("ocrReviewPanel")
        self.controller = controller
        self.view = None
        self.rows: list[tuple[int, pymupdf.Rect, object]] = []
        lay = QVBoxLayout(self)
        title = QLabel(_("Parole incerte"))
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        lay.addWidget(title)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(self.hint)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels([_("Pag."), _("Parola"), "%"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(_("Correzione"))
        self.btn_fix = QPushButton(icon("check"), _("Correggi"), self)
        self.btn_ok = QPushButton(_("È corretta"), self)
        row.addWidget(self.edit, 1)
        row.addWidget(self.btn_fix)
        lay.addLayout(row)
        lay.addWidget(self.btn_ok)
        self.table.currentCellChanged.connect(lambda r, *_a: self._select(r))
        self.btn_fix.clicked.connect(self._fix)
        self.edit.returnPressed.connect(self._fix)
        self.btn_ok.clicked.connect(self._accept_word)

    def set_view(self, view) -> None:
        self.view = view
        self.refresh()

    def refresh(self) -> None:
        self.rows = []
        st = self.controller.states.get(self.view) if self.view is not None else None
        if st is not None:
            for page in sorted(st.placed):
                for rect, w in st.placed[page]:
                    if w.low:
                        self.rows.append((page, rect, w))
        self.table.setRowCount(len(self.rows))
        for i, (page, _r, w) in enumerate(self.rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(page + 1)))
            self.table.setItem(i, 1, QTableWidgetItem(w.text))
            c = QTableWidgetItem(f"{w.conf:.0f}")
            c.setForeground(QBrush(theme.color("warning")))
            self.table.setItem(i, 2, c)
        self.hint.setText(_("Nessuna parola incerta.") if not self.rows else
                          _("{n} parole con affidabilità bassa, evidenziate sulla pagina.").format(n=len(self.rows)))
        enabled = bool(self.rows)
        self.edit.setEnabled(enabled)
        self.btn_fix.setEnabled(enabled)
        self.btn_ok.setEnabled(enabled)

    def _select(self, row: int) -> None:
        if not 0 <= row < len(self.rows) or self.view is None:
            return
        page, rect, w = self.rows[row]
        self.edit.setText(w.text)
        self.controller.current_word = (self.view, page, rect)
        self.view.ensure_visible(page, rect)
        self.view.viewport().update()

    def _fix(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows) or self.view is None:
            return
        page, rect, w = self.rows[row]
        self.controller.correct_word(self.view, page, rect, w, self.edit.text())

    def _accept_word(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            self.rows[row][2].conf = 100.0
            self.refresh()
            if self.view is not None:
                self.view.viewport().update()


# =================================================================== controller
class OcrController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.states: dict = weakref.WeakKeyDictionary()
        self.current_word = None
        self.highlight = True
        self.area_tool = AreaOcrTool(self.ocr_area)
        self.review_panel: ReviewPanel | None = None
        ctx.documentOpened.connect(self.on_opened)
        ctx.documentClosed.connect(self.on_closed)
        ctx.documentChanged.connect(self.on_changed)
        theme.on_change(self._theme_changed)

    # ------------------------------------------------------------- preferenze
    def settings(self):
        return self.ctx.settings()

    def detection_enabled(self) -> bool:
        return str(self.settings().value("ocr/detect", "true")).lower() in ("true", "1")

    def set_detection_enabled(self, on: bool) -> None:
        self.settings().setValue("ocr/detect", "true" if on else "false")
        if not on:
            for st in list(self.states.values()):
                if st.bar is not None:
                    st.bar.hide()

    @staticmethod
    def document_key(view) -> str:
        """Chiave del documento: primo elemento di /ID o impronta del file (mai salvata nel PDF)."""
        doc = view.document
        with ENGINE_LOCK:
            try:
                kind, val = doc.pdf.xref_get_key(-1, "ID")
                if kind == "array" and "<" in val:
                    return "id:" + val.split("<")[1].split(">")[0].lower()
            except Exception:
                pass
        h = hashlib.sha1()
        if doc.path is not None and os.path.exists(doc.path):
            with open(doc.path, "rb") as f:
                h.update(f.read(1 << 20))
            h.update(str(os.path.getsize(doc.path)).encode())
        else:
            h.update(doc.display_name.encode())
        return "sha1:" + h.hexdigest()

    def never_asked(self, view) -> bool:
        keys = self.settings().value("ocr/never_ask", []) or []
        if isinstance(keys, str):
            keys = [keys]
        return self.document_key(view) in keys

    def never_ask(self, view) -> None:
        keys = self.settings().value("ocr/never_ask", []) or []
        if isinstance(keys, str):
            keys = [keys]
        key = self.document_key(view)
        if key not in keys:
            keys = list(keys) + [key]
            self.settings().setValue("ocr/never_ask", keys[-500:])
        self.dismiss(view)

    # ------------------------------------------------------------- stato
    def state(self, view) -> ViewState:
        st = self.states.get(view)
        if st is None:
            st = ViewState()
            self.states[view] = st
        return st

    def on_opened(self, view) -> None:
        st = self.state(view)
        view.overlays.append(self._paint_overlay)
        view.viewport().installEventFilter(self)
        self.start_analysis(view)
        del st

    def on_closed(self, view) -> None:
        st = self.states.pop(view, None)
        if st is not None and st.timer is not None:
            st.timer.stop()
        try:
            view.viewport().removeEventFilter(self)
        except RuntimeError:
            pass

    def on_changed(self, view) -> None:
        st = self.states.get(view)
        if st is None:
            return
        if any(p >= view.page_count for p in st.placed):
            st.placed.clear()   # pagine eliminate: i riquadri non sono più affidabili
        self.start_analysis(view, quiet=True)
        if self.review_panel is not None and self.review_panel.view is view:
            self.review_panel.refresh()

    def _theme_changed(self) -> None:
        for st in list(self.states.values()):
            if st.bar is not None:
                st.bar.apply_theme()

    # ------------------------------------------------------------- analisi
    def start_analysis(self, view, quiet: bool = False) -> None:
        st = self.state(view)
        st.infos.clear()
        visible = []
        try:
            visible = view.visible_pages()
        except Exception:
            pass
        rest = [i for i in range(view.page_count) if i not in visible]
        st.order = list(visible) + rest
        st.version = view.document.version
        st.started = time.monotonic()
        if quiet and st.bar is not None and st.bar.page is None:
            st.bar.hide()
        if st.timer is None:
            st.timer = QTimer(self)
            st.timer.setInterval(10)
            st.timer.timeout.connect(lambda v=weakref.ref(view): self._analysis_step(v()))
        st.timer.start()

    def _analysis_step(self, view) -> None:
        if view is None:
            return
        st = self.states.get(view)
        if st is None or st.timer is None:
            return
        if view.document.closed:
            st.timer.stop()
            return
        if st.version != view.document.version:
            self.start_analysis(view, quiet=True)
            return
        deadline = time.monotonic() + ANALYSIS_BUDGET_MS / 1000
        with try_engine() as ok:
            if not ok:
                return   # il motore è occupato: si riprova al prossimo passo
            pdf = view.document.pdf
            while st.order and time.monotonic() < deadline:
                i = st.order.pop(0)
                if i >= pdf.page_count:
                    continue
                try:
                    st.infos[i] = detect.classify_page(pdf[i])
                except Exception:
                    pass
        if not st.order:
            st.timer.stop()
        self._update_bar(view)

    def _update_bar(self, view) -> None:
        st = self.state(view)
        found = st.detected
        if not found or st.dismissed or not self.detection_enabled():
            if st.bar is not None and st.bar.page is None and not found:
                st.bar.hide()
            return
        if st.bar is not None and st.bar.isVisible() and st.bar.page is not None:
            return
        if self.never_asked(view):
            st.dismissed = True
            return
        if st.bar is None:
            st.bar = ProposalBar(view, self)
        missing = languages.missing_languages(engine.default_languages() or ["ita", "eng"])
        restricted = False
        with ENGINE_LOCK:
            try:
                restricted = not (view.document.pdf.permissions & pymupdf.PDF_PERM_MODIFY)
            except Exception:
                restricted = False
        low = any(st.infos[i].low_resolution for i in found)
        st.bar.show_document(len(found), missing, restricted, low)

    def dismiss(self, view) -> None:
        st = self.state(view)
        st.dismissed = True
        if st.bar is not None:
            st.bar.hide()

    def show_details(self, view) -> None:
        st = self.state(view)
        dlg = QDialog(self.ctx.window)
        dlg.setWindowTitle(_("Pagine da riconoscere"))
        dlg.resize(560, 420)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(0, 3, dlg)
        table.setHorizontalHeaderLabels([_("Pagina"), _("Contenuto"), _("Risoluzione")])
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        infos = [st.infos[i] for i in sorted(st.infos)]
        table.setRowCount(len(infos))
        for r, inf in enumerate(infos):
            table.setItem(r, 0, QTableWidgetItem(str(inf.index + 1)))
            item = QTableWidgetItem(detect.category_label(inf.category))
            if inf.needs_ocr:
                item.setIcon(icon("scan-text"))
            table.setItem(r, 1, item)
            dpi = f"{inf.dpi:.0f} dpi" if inf.dpi else "—"
            if inf.low_resolution:
                dpi += " · " + _("accuratezza ridotta")
            table.setItem(r, 2, QTableWidgetItem(dpi))
        table.cellDoubleClicked.connect(lambda r, _c: view.go_to_page(infos[r].index))
        lay.addWidget(table)
        row = QHBoxLayout()
        row.addStretch(1)
        run = QPushButton(icon("scan-text"), _("Riconosci testo"), dlg)
        close = QPushButton(_("Chiudi"), dlg)
        row.addWidget(run)
        row.addWidget(close)
        lay.addLayout(row)
        run.clicked.connect(lambda: (dlg.accept(), self.open_dialog(view, pages=st.detected)))
        close.clicked.connect(dlg.reject)
        self._details = dlg
        dlg.open()

    # ------------------------------------------------------------- eventi vista
    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.MouseButtonPress:
            view = obj.parent()
            st = self.states.get(view)
            if st is not None and self.detection_enabled() and not st.dismissed:
                try:
                    tool_id = getattr(view.tool, "id", "")
                    editing = self.ctx.current_mode() == "modifica" or "edit" in str(tool_id)
                    if tool_id == "select-text" or editing:
                        hit = view.view_to_page(ev.position())
                        if hit is not None:
                            inf = st.infos.get(hit[0])
                            if inf is not None and inf.category == detect.IMAGE_ONLY and not view.words(hit[0]):
                                if st.bar is None:
                                    st.bar = ProposalBar(view, self)
                                st.bar.show_page(hit[0], len(st.detected))
                except Exception:
                    pass
        return False

    # ------------------------------------------------------------- esecuzione
    def open_dialog(self, view=None, pages: list[int] | None = None, mode: str | None = None) -> None:
        view = view or self.ctx.current_view()
        if view is None:
            return
        st = self.state(view)
        if not st.complete:
            # completa l'analisi per proporre le pagine senza testo
            with ENGINE_LOCK:
                pdf = view.document.pdf
                for i in list(st.order):
                    if i < pdf.page_count:
                        st.infos[i] = detect.classify_page(pdf[i])
                st.order.clear()
        detected = st.detected
        text = engine.format_pages(pages) if pages and pages != detected else None
        dlg = OcrDialog(self.ctx.window, page_count=view.page_count, current_page=view.current_page,
                        detected=detected, settings=self.settings(),
                        mode=mode or str(self.settings().value("ocr/mode", engine.SEARCHABLE)), pages_text=text)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.settings().setValue("ocr/mode", dlg.mode())
        self.run(view, dlg.options(), dlg.selected_pages(), preview=dlg.preview)

    def run(self, view, opts: engine.OcrOptions, pages: list[int], preview: bool = True) -> engine.OcrResult | None:
        doc = view.document
        missing = languages.missing_languages(opts.languages)
        if missing:
            opts.languages = self._resolve_missing(opts.languages, missing)
            if not opts.languages:
                return None
        with ENGINE_LOCK:
            data = doc.pdf.tobytes(garbage=0, deflate=False, encryption=pymupdf.PDF_ENCRYPT_NONE)

        def work(progress):
            with ENGINE_LOCK:
                pdf = pymupdf.open(stream=data, filetype="pdf")
            try:
                return engine.recognize_document(pdf, opts, pages, progress)
            finally:
                with ENGINE_LOCK:
                    pdf.close()
        try:
            result = self.ctx.run_task(_("Riconoscimento del testo…"), work)
        except OperationCancelled:
            self.ctx.status(_("Riconoscimento annullato: il documento non è stato modificato."))
            return None
        except (OcrError, languages.LanguagePackError) as e:
            self.ctx.error(_("Riconoscimento non riuscito"), str(e))
            return None
        except Exception as e:
            self.ctx.error(_("Riconoscimento non riuscito"), _("Errore imprevisto: {e}").format(e=e))
            return None
        if not result.recognized:
            msg = _("Nessuna pagina riconosciuta.")
            if result.failed:
                msg += "\n\n" + "\n".join(_("Pagina {n}: {m}").format(n=p.index + 1, m=p.message)
                                          for p in result.failed)
            elif result.skipped:
                msg += " " + _("Le pagine scelte hanno già testo: per riconoscerle comunque scegli "
                               "«Riconosci anche quelle».")
            self.ctx.info(_("Riconoscimento del testo"), msg)
            return result
        if opts.mode == engine.TEXT_ONLY:
            self.show_text(result.text(), _("Testo riconosciuto"))
            return result
        if preview:
            with ENGINE_LOCK:
                src = pymupdf.open(stream=data, filetype="pdf")
            try:
                dlg = ReviewDialog(self.ctx.window, result, src)
                accepted = dlg.exec() == QDialog.DialogCode.Accepted
            finally:
                with ENGINE_LOCK:
                    src.close()
            if not accepted:
                self.ctx.status(_("OCR non applicato."))
                return None
        self.apply(view, result)
        return result

    def apply(self, view, result: engine.OcrResult) -> None:
        label = _("Rendi modificabile (OCR)") if result.options.mode == engine.EDITABLE else _("Riconoscimento testo (OCR)")
        try:
            with view.document.change(label) as pdf:
                engine.apply_result(pdf, result)
        except Exception as e:
            self.ctx.error(_("Riconoscimento non riuscito"),
                           _("Impossibile applicare il testo riconosciuto: {e}").format(e=e))
            return
        st = self.state(view)
        for res in result.recognized:
            st.placed[res.index] = list(res.placed)
        st.dismissed = True
        if st.bar is not None:
            st.bar.hide()
        words = sum(len(p.words) for p in result.recognized)
        low = sum(len(p.low_confidence_words()) for p in result.recognized)
        msg = _("Testo riconosciuto in {n} pagine ({w} parole, {l} incerte).").format(
            n=len(result.recognized), w=words, l=low)
        self.ctx.status(msg, 10000)
        if words and low / words > 0.3:
            self.ctx.info(_("Riconoscimento del testo"),
                          _("Molte parole sono incerte: controlla che le lingue scelte siano quelle del documento "
                            "e che la scansione sia leggibile."))
        if result.failed:
            self.ctx.info(_("Riconoscimento del testo"), _("Alcune pagine non sono state riconosciute:") + "\n\n" +
                          "\n".join(_("Pagina {n}: {m}").format(n=p.index + 1, m=p.message) for p in result.failed))
        if self.review_panel is not None:
            self.review_panel.refresh()
        view.viewport().update()

    def _resolve_missing(self, langs: list[str], missing: list[str]) -> list[str]:
        choice = self.ask_missing(missing)
        if choice == "install":
            self.manage_languages()
            return [] if languages.missing_languages(langs) else langs
        if choice == "remove":
            return [c for c in langs if c not in missing]
        return []

    def ask_missing(self, missing: list[str]) -> str | None:
        """Chiede cosa fare delle lingue mancanti: "install", "remove" o None (annulla)."""
        names = ", ".join(languages.language_name(c) for c in missing)
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Lingue non installate"))
        box.setText(_("Lingue non installate: {l}.").format(l=names))
        install = box.addButton(_("Installa…"), QMessageBox.ButtonRole.ActionRole)
        remove = box.addButton(_("Rimuovi dalla selezione"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is install:
            return "install"
        if clicked is remove:
            return "remove"
        return None

    # ------------------------------------------------------------- testo
    def show_text(self, text: str, title: str) -> None:
        def save(t: str) -> None:
            view = self.ctx.current_view()
            name = os.path.splitext(view.document.display_name)[0] + ".txt" if view else "testo.txt"
            path = self.ctx.ask_save_path(_("Salva testo"), name, _("Testo (*.txt)"), ".txt")
            if path:
                try:
                    atomic_write(path, t.encode("utf-8"))
                    self.ctx.status(_("Testo salvato in {p}").format(p=path))
                except Exception as e:
                    self.ctx.error(_("Salvataggio non riuscito"), str(e))
        self._text_dialog = TextDialog(self.ctx.window, text, title, save)
        self._text_dialog.exec()

    def extract_text(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        self.open_dialog(view, mode=engine.TEXT_ONLY)

    # ------------------------------------------------------------- area
    def toggle_area_tool(self, on: bool) -> None:
        self.ctx.set_tool(self.area_tool if on else None)
        if on:
            self.ctx.status(_("Trascina un rettangolo sull'area da riconoscere."))

    def ocr_area(self, view, page: int, rect: pymupdf.Rect) -> None:
        langs = self._saved_languages()
        with ENGINE_LOCK:
            pg = view.document.pdf[page]
            clip = rect * pg.rotation_matrix
            data_page = page

        def work(progress):
            return engine.ocr_page_text(view.document, data_page, langs, clip=clip, progress=progress)
        try:
            text = self.ctx.run_task(_("Riconoscimento dell'area…"), work)
        except OperationCancelled:
            return
        except Exception as e:
            self.ctx.error(_("Riconoscimento non riuscito"), str(e))
            return
        if not text.strip():
            self.ctx.info(_("OCR dell'area"), _("Nessun testo riconosciuto nell'area scelta."))
            return
        self.show_text(text, _("Testo dell'area"))

    def _saved_languages(self) -> list[str]:
        saved = self.settings().value("ocr/languages", None)
        if isinstance(saved, str):
            saved = [s for s in saved.split(",") if s]
        langs = [c for c in (saved or []) if c in languages.available_languages()]
        return langs or engine.default_languages()

    # ------------------------------------------------------------- revisione
    def toggle_highlight(self, on: bool) -> None:
        self.highlight = on
        for view in self.ctx.views():
            view.viewport().update()
        if on and self.review_panel is not None:
            self.review_panel.set_view(self.ctx.current_view())
            self.ctx.show_properties("ocr-review")
        elif not on:
            self.ctx.hide_properties()

    def _paint_overlay(self, view, painter, index: int) -> None:
        if not self.highlight:
            return
        st = self.states.get(view)
        if st is None or index not in st.placed:
            return
        warn = QColor(theme.color("warning"))
        fill = QColor(warn)
        fill.setAlpha(45)
        painter.setPen(QPen(warn, 0.8))
        painter.setBrush(QBrush(fill))
        for rect, w in st.placed[index]:
            if w.low:
                painter.drawRect(QRectF(rect.x0, rect.y0, rect.width, rect.height))
        cur = self.current_word
        if cur is not None and cur[0] is view and cur[1] == index:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color("accent"), 1.5))
            r = cur[2]
            painter.drawRect(QRectF(r.x0 - 1, r.y0 - 1, r.width + 2, r.height + 2))

    def correct_word(self, view, page: int, rect: pymupdf.Rect, word, new_text: str) -> bool:
        family = str(self.settings().value("ocr/font", "sans"))
        try:
            with view.document.change(_("Correggi parola OCR")) as pdf:
                if not engine.replace_word(pdf[page], rect, new_text, family):
                    raise OcrError(_("Nel punto indicato non c'è più testo da correggere."))
        except Exception as e:
            self.ctx.error(_("Correzione non riuscita"), str(e))
            return False
        word.text = new_text.strip()
        word.conf = 100.0
        if self.review_panel is not None:
            self.review_panel.refresh()
        view.viewport().update()
        return True

    # ------------------------------------------------------------- blocco e lingue
    def manage_languages(self) -> None:
        LanguagesDialog(self.ctx.window, run_task=self.ctx.run_task).exec()

    def batch(self) -> None:
        dlg = BatchDialog(self.ctx.window, self.settings(),
                          ask_files=lambda: self.ctx.ask_open_paths(_("PDF da riconoscere")),
                          ask_dir=lambda: self.ctx.ask_directory(_("Cartella di destinazione")))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self.run_batch(dlg.paths(), dlg.mode.currentData(), dlg.languages.selected(), dlg.out_dir.text().strip(),
                       dlg.suffix.text())

    def run_batch(self, paths: list[str], mode: str, langs: list[str], out_dir: str = "",
                  suffix: str = "_ocr") -> list[tuple[str, bool, str]]:
        def work(progress):
            rows = []
            n = len(paths)
            for k, path in enumerate(paths):
                name = os.path.basename(path)

                class Sub:
                    @property
                    def cancelled(self):
                        return progress.cancelled

                    def update(self, f=None, t=None):
                        progress.update((k + (f or 0)) / n, _("File {k} di {n}: {f}").format(k=k + 1, n=n, f=name))
                Sub().update(0)
                try:
                    with open(path, "rb") as f:
                        data = f.read()
                    out = engine.ocr_pdf_bytes(data, langs, mode, progress=Sub())
                    dest = output_path(path, out_dir, suffix)
                    atomic_write(dest, out)
                    rows.append((name, True, _("Salvato come {d}").format(d=os.path.basename(dest))))
                except OperationCancelled:
                    raise
                except Exception as e:
                    rows.append((name, False, str(e)))
            progress.update(1.0)
            return rows
        try:
            rows = self.ctx.run_task(_("OCR in blocco…"), work)
        except OperationCancelled:
            self.ctx.status(_("OCR in blocco annullato."))
            return []
        self._report = ReportDialog(self.ctx.window, _("OCR in blocco: risultato"), rows)
        self._report.open()
        return rows
