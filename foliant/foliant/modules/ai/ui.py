"""Registrazione del modulo: modalità «Assistente IA», pannello, menu, dialoghi."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, FoliantError, OperationCancelled, _, add_translations, atomic_write, bind_icon

from . import content, engine, history, translate_pdf
from .chunking import IndexCache
from .config import ProfileStore, SendLog
from .dialogs import InfoTextDialog, SendLogDialog, SettingsDialog, language_combo
from .engine import Gateway
from .panel import AIPanel
from .translations import EN


class Services:
    """Oggetti condivisi dal modulo nella finestra."""

    def __init__(self, ctx, *, provider_factory=None):
        self.ctx = ctx
        self.store = ProfileStore(ctx.settings())
        self.log = SendLog()
        self.cache = IndexCache()
        self.provider_factory = provider_factory
        self.workers: list = []
        self.panel: AIPanel | None = None
        self.mindmap_widget = None
        self._history = None

    def gateway(self) -> Gateway:
        return Gateway(self.store, self.log, provider_factory=self.provider_factory)

    def history(self) -> history.HistoryStore:
        enabled = self.store.option("history_enabled", True)
        days = self.store.option("retention_days", 0)
        if self._history is None:
            self._history = history.HistoryStore()
        self._history.enabled = enabled
        self._history.retention_days = days
        return self._history

    def register_worker(self, w) -> None:
        if w not in self.workers:
            self.workers.append(w)

    def unregister_worker(self, w) -> None:
        if w in self.workers:
            self.workers.remove(w)

    def running(self, document=None) -> list:
        return [w for w in self.workers if w.running and (document is None or w.document is document)]

    def refresh(self) -> None:
        if self.panel is not None:
            self.panel.refresh_state()
        for a in getattr(self, "doc_actions", []):
            a.setEnabled(self.store.active() is not None and self.ctx.current_view() is not None)

    # ---------------------------------------------------------------- dialoghi
    def open_settings(self, parent=None) -> None:
        dlg = SettingsDialog(self.store, parent or self.ctx.window)
        dlg.exec()
        self.refresh()

    def show_log(self, parent=None) -> None:
        SendLogDialog(self.log, parent or self.ctx.window,
                      ask_save=lambda: self.ctx.ask_save_path(_("Esporta registro"), "registro-invii.json",
                                                              "JSON (*.json)", ".json")).exec()

    def show_history(self, parent=None) -> None:
        HistoryDialog(self, parent or self.ctx.window).exec()

    def open_mindmap(self, root, document, view) -> None:
        from .mindmap_screen import MindMapScreen
        screen = MindMapScreen(self.ctx, root, document)
        self.mindmap_widget = screen
        self.ctx.set_central_widget(screen)


# =================================================================== cronologia
class HistoryDialog(QDialog):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setWindowTitle(_("Cronologia delle conversazioni"))
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem(_("Questo documento"), "doc")
        self.scope.addItem(_("Tutte"), "all")
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Cerca per testo…"))
        self.function = QComboBox()
        self.function.addItem(_("Tutte le funzioni"), "")
        for key, text in (("chat", "Chiedi"), ("summary", "Riassumi"), ("translate", "Traduci"),
                          ("explain", "Spiega"), ("rewrite", "Correggi e riscrivi"), ("outline", "Scaletta"),
                          ("map", "Mappa concettuale"), ("image", "Immagine o area")):
            self.function.addItem(_(text), key)
        for w in (self.scope, self.search, self.function):
            row.addWidget(w)
        lay.addLayout(row)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        lay.addWidget(self.notice)
        btns = QHBoxLayout()
        self.b_open = QPushButton(_("Riprendi"))
        self.b_rename = QPushButton(_("Rinomina"))
        self.b_md = QPushButton(_("Esporta Markdown"))
        self.b_json = QPushButton(_("Esporta JSON"))
        self.b_del = QPushButton(_("Elimina"))
        self.b_del_doc = QPushButton(_("Elimina quelle del documento"))
        self.b_del_all = QPushButton(_("Elimina tutto"))
        for b, fn in ((self.b_open, self.open_selected), (self.b_rename, self.rename_selected),
                      (self.b_md, lambda: self.export_selected("md")), (self.b_json, lambda: self.export_selected("json")),
                      (self.b_del, self.delete_selected), (self.b_del_doc, self.delete_document),
                      (self.b_del_all, self.delete_all)):
            b.clicked.connect(fn)
            btns.addWidget(b)
        lay.addLayout(btns)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        lay.addWidget(close)
        self.scope.currentIndexChanged.connect(self.reload)
        self.search.textChanged.connect(self.reload)
        self.function.currentIndexChanged.connect(self.reload)
        doc = services.ctx.current_document()
        self.fingerprint = content.content_fingerprint(doc.pdf) if doc is not None else ""
        self.path = str(doc.path or "") if doc is not None else ""
        if doc is None:
            self.scope.setCurrentIndex(1)
        self.reload()

    def reload(self) -> None:
        h = self.services.history()
        convs = h.search(self.search.text(), self.function.currentData() or "")
        if self.scope.currentData() == "doc":
            convs = [c for c in convs if c.fingerprint == self.fingerprint or (self.path and c.path == self.path)]
        self.list.clear()
        for c in convs:
            item = QListWidgetItem(f"{c.title or _('Senza titolo')} — {c.updated.replace('T', ' ')} "
                                   f"({len(c.messages)})")
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            self.list.addItem(item)
        notes = []
        if h.damaged:
            notes.append(_("Alcuni file della cronologia erano danneggiati e sono stati isolati."))
        if not h.enabled:
            notes.append(_("La cronologia è disattivata: le nuove conversazioni non vengono salvate."))
        self.notice.setText(" ".join(notes))

    def _selected(self):
        item = self.list.currentItem()
        return self.services.history().get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def open_selected(self) -> None:
        conv = self._selected()
        if conv is None or self.services.panel is None:
            return
        self.services.panel.load_conversation(conv)
        self.accept()

    def rename_selected(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        conv = self._selected()
        if conv is None:
            return
        title, ok = QInputDialog.getText(self, _("Rinomina"), _("Titolo:"), text=conv.title)
        if ok and title.strip():
            self.services.history().rename(conv.id, title.strip())
            self.reload()

    def export_selected(self, fmt: str) -> None:
        conv = self._selected()
        if conv is None:
            return
        flt = "Markdown (*.md)" if fmt == "md" else "JSON (*.json)"
        path = self.services.ctx.ask_save_path(_("Esporta conversazione"), f"conversazione.{fmt}", flt, f".{fmt}")
        if path:
            text = history.export_markdown(conv) if fmt == "md" else history.export_json(conv)
            atomic_write(path, text.encode("utf-8"))

    def delete_selected(self) -> None:
        conv = self._selected()
        if conv is not None:
            self.services.history().delete(conv.id)
            self.reload()

    def delete_document(self) -> None:
        if self.fingerprint:
            self.services.history().delete_document(self.fingerprint)
            self.reload()

    def delete_all(self) -> None:
        if QMessageBox.question(self, _("Elimina tutto"), _("Eliminare tutte le conversazioni salvate?")) \
                == QMessageBox.StandardButton.Yes:
            self.services.history().delete_all()
            self.reload()


# =================================================================== traduzione in nuovo PDF
class TranslatePdfDialog(QDialog):
    def __init__(self, ctx, document, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Traduci in un nuovo PDF"))
        self.document = document
        form = QFormLayout(self)
        self.lang = language_combo(ctx.settings().value("ai/last_target", "en") or "en")
        self.pages = QLineEdit()
        self.pages.setPlaceholderText(_("tutte"))
        self.mode = QComboBox()
        self.mode.addItem(_("Impaginazione mantenuta"), translate_pdf.MODE_LAYOUT)
        self.mode.addItem(_("Bilingue a pagine alternate"), translate_pdf.MODE_BILINGUAL)
        self.mode.addItem(_("Solo testo riformattato"), translate_pdf.MODE_TEXT)
        self.glossary = QLineEdit()
        self.glossary.setPlaceholderText(_("termine=traduzione; termine=- (non tradurre)"))
        self.min_size = QSpinBox()
        self.min_size.setRange(4, 12)
        self.min_size.setValue(6)
        self.min_size.setSuffix(" pt")
        self.error = QLabel("")
        self.error.setWordWrap(True)
        form.addRow(_("Lingua"), self.lang)
        form.addRow(_("Pagine"), self.pages)
        form.addRow(_("Modalità"), self.mode)
        form.addRow(_("Glossario"), self.glossary)
        form.addRow(_("Corpo minimo"), self.min_size)
        form.addRow(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._check)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def page_list(self) -> list[int] | None:
        text = self.pages.text().strip()
        if not text:
            return None
        return content.parse_page_range(text, self.document.page_count)

    def _check(self) -> None:
        try:
            self.page_list()
        except ValueError as e:
            self.error.setText(str(e))
            return
        self.accept()


def parse_glossary(text: str) -> list[tuple[str, str | None]]:
    out = []
    for part in (text or "").split(";"):
        if "=" in part:
            a, b = part.split("=", 1)
            if a.strip():
                out.append((a.strip(), None if b.strip() in ("", "-") else b.strip()))
        elif part.strip():
            out.append((part.strip(), None))
    return out


class Controller:
    """Azioni del modulo che non stanno nel pannello."""

    def __init__(self, ctx, services: Services):
        self.ctx = ctx
        self.services = services
        self.last_translation: dict | None = None

    # ---------------------------------------------------------------- traduzione PDF (AI-07)
    def ask_translate_options(self, document):
        dlg = TranslatePdfDialog(self.ctx, document, self.ctx.window)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return dict(target=dlg.lang.currentData(), pages=dlg.page_list(), mode=dlg.mode.currentData(),
                    glossary=parse_glossary(dlg.glossary.text()), min_size=float(dlg.min_size.value()))

    def translate_pdf(self, options: dict | None = None, path: str | None = None):
        view = self.ctx.current_view()
        panel = self.services.panel
        if view is None or panel is None:
            return None
        doc = view.document
        if not content.extraction_allowed(doc.pdf):
            self.ctx.error(_("Traduci in un nuovo PDF"), content.blocked_reason(doc.pdf))
            return None
        if not panel._ensure_ready():
            return None
        options = options or self.ask_translate_options(doc)
        if not options:
            return None
        if path is None:
            base = os.path.splitext(doc.display_name)[0]
            path = self.ctx.ask_save_path(_("Salva il PDF tradotto"), f"{base}-{options['target']}.pdf")
        if not path:
            return None
        if doc.path is not None and os.path.abspath(path) == os.path.abspath(str(doc.path)):
            self.ctx.error(_("Traduci in un nuovo PDF"), _("Scegli un file diverso dall'originale."))
            return None
        pages = options["pages"] if options["pages"] is not None else list(range(doc.page_count))
        textless = content.textless_pages(doc.pdf, pages)
        if textless:
            self.ctx.info(_("Traduci in un nuovo PDF"),
                          _("Le pagine {p} non hanno testo: rimarranno non tradotte. Usa prima l'OCR per "
                            "tradurle.").format(p=", ".join(str(i + 1) for i in textless)))
        source = _plain_bytes(doc)
        self.ctx.settings().setValue("ai/last_target", options["target"])
        return self._run_translation(source, pages, options, path, None, None)

    def _run_translation(self, source: bytes, pages, options, path, retry_pages, previous):
        gateway = self.services.gateway()
        gateway.profile()

        def work(progress):
            job = engine.Job(gateway, cancel=_CancelBridge(progress))
            translator = engine.block_translator(job, options["target"], options["glossary"])
            return translate_pdf.translate_document(
                source, pages, options["mode"], translator, progress, target_language=options["target"],
                min_fontsize=options["min_size"], retry_pages=retry_pages, previous=previous)

        try:
            data, report = self.ctx.run_task(_("Traduzione del documento…"), work)
        except OperationCancelled:
            self.ctx.status(_("Traduzione annullata: nessun file creato."))
            return None
        except FoliantError as e:
            self.ctx.error(_("Traduci in un nuovo PDF"), str(e))
            return None
        except Exception as e:  # noqa: BLE001
            self.ctx.error(_("Traduci in un nuovo PDF"), _("Traduzione non riuscita: {e}").format(e=e))
            return None
        try:
            atomic_write(path, data)
        except FoliantError as e:
            self.ctx.error(_("Traduci in un nuovo PDF"), str(e))
            return None
        self.last_translation = dict(source=source, pages=pages, options=options, path=path, data=data,
                                     report=report)
        self.ctx.open_path(path)
        self.show_report(report)
        return report

    def show_report(self, report) -> None:
        buttons = [(_("Ritenta pagine non riuscite"), "retry")] if report.failed_pages else []
        dlg = InfoTextDialog(_("Rapporto della traduzione"),
                             "<pre>" + report.to_text().replace("<", "&lt;") + "</pre>", self.ctx.window, buttons)
        if self.exec_report(dlg) and dlg.clicked == "retry":
            self.retry_failed()

    def exec_report(self, dlg) -> bool:
        return dlg.exec() == QDialog.DialogCode.Accepted

    def retry_failed(self):
        t = self.last_translation
        if not t or not t["report"].failed_pages:
            return None
        return self._run_translation(t["source"], t["pages"], t["options"], t["path"],
                                     sorted(t["report"].failed_pages), t["data"])


class _CancelBridge:
    """Evento di annullamento collegato al `Progress` di run_task."""

    def __init__(self, progress):
        self.progress = progress

    def is_set(self) -> bool:
        return bool(getattr(self.progress, "cancelled", False))

    def wait(self, timeout=None) -> bool:
        import time
        end = time.monotonic() + (timeout or 0)
        while time.monotonic() < end:
            if self.is_set():
                return True
            time.sleep(0.05)
        return self.is_set()

    def set(self) -> None:
        self.progress.cancel()


def _plain_bytes(doc) -> bytes:
    with ENGINE_LOCK:
        import pymupdf
        return doc.pdf.tobytes(garbage=0, deflate=True, encryption=pymupdf.PDF_ENCRYPT_NONE)


# =================================================================== chat generale senza documento
class GeneralChatDialog(QDialog):
    def __init__(self, ctx, services, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_("Assistente IA — chat generale"))
        self.resize(520, 640)
        lay = QVBoxLayout(self)
        self.panel = AIPanel(ctx, services, self, standalone=True)
        self.panel.set_scope("none")
        lay.addWidget(self.panel)


# =================================================================== registrazione
def register(ctx, *, provider_factory=None) -> Services:
    add_translations("en", EN)
    services = Services(ctx, provider_factory=provider_factory)
    ctx.ai_services = services  # per i test e le integrazioni
    controller = Controller(ctx, services)
    services.controller = controller
    try:
        services.history().expire()
    except OSError:
        pass

    panel = AIPanel(ctx, services)
    services.panel = panel
    ctx.add_properties_panel("ia", panel)
    mode = ctx.add_mode("ia", _("Assistente IA"), "sparkles", 110)

    def show_panel() -> None:
        if ctx.current_view() is not None:
            ctx.show_properties("ia")
        panel.refresh_state()

    mode.entered.connect(show_panel)

    def act(text, icon_name, fn, shortcut=None, requires=True):
        # l'abilitazione dei comandi che richiedono un documento è gestita da `services.refresh()`:
        # restano disattivati anche finché non c'è un profilo attivo (AI-01)
        return ctx.action(text, icon_name, fn, shortcut=shortcut, requires_document=False)

    def run(fn_key, scope=None):
        def go():
            ctx.set_mode("ia")
            show_panel()
            panel.set_function(fn_key)
            if scope:
                panel.set_scope(scope)
            if fn_key in ("summary", "outline", "map") and panel.is_enabled():
                panel.send()
            panel.input.setFocus()
        return go

    a_chat = act(_("Chiedi"), "message-circle", run("chat"), "Ctrl+Shift+A")
    a_sum = act(_("Riassumi"), "scroll-text", run("summary"))
    a_tr = act(_("Traduci"), "languages", run("translate"))
    a_exp = act(_("Spiega"), "lightbulb", run("explain"))
    a_rw = act(_("Correggi e riscrivi"), "spell-check", run("rewrite"))
    a_out = act(_("Scaletta"), "list-tree", run("outline"))
    a_map = act(_("Mappa concettuale"), "network", run("map"))
    a_area = act(_("Chiedi su un'area"), "square-dashed-mouse-pointer",
                 lambda: (ctx.set_mode("ia"), show_panel(), panel.start_area_tool()))
    a_trpdf = act(_("Traduci in un nuovo PDF"), "file-output", lambda: controller.translate_pdf())
    a_hist = act(_("Cronologia"), "history", lambda: services.show_history(), requires=False)
    a_log = act(_("Registro degli invii"), "list", lambda: services.show_log(), requires=False)
    a_set = act(_("Impostazioni dell'Assistente IA"), "settings", lambda: services.open_settings(), requires=False)
    services.doc_actions = [a_chat, a_sum, a_tr, a_exp, a_rw, a_out, a_map, a_area, a_trpdf]
    for a in (a_chat, a_sum, a_tr, a_exp, a_rw):
        mode.add_action(a)
    mode.add_separator()
    for a in (a_out, a_map, a_area, a_trpdf):
        mode.add_action(a)
    mode.add_separator()
    for a in (a_hist, a_log, a_set):
        mode.add_action(a)
    ctx.add_menu_action(a_set, _("Assistente IA"))
    ctx.add_menu_action(a_trpdf, _("Assistente IA"))
    ctx.add_home_action(_("Assistente IA"), "sparkles", lambda: GeneralChatDialog(ctx, services, ctx.window).exec(),
                        _("Chat generale con il fornitore configurato"), 110)

    def menu_provider(view, pe, menu: QMenu) -> None:
        if not services.store.enabled():
            return
        import pymupdf
        sel = view.text_selection is not None and bool(view.selected_text().strip())
        hit = None
        if pe.page is not None and pe.point is not None:
            with ENGINE_LOCK:
                infos = view.document.pdf[pe.page].get_image_info()
            hit = next((pymupdf.Rect(i["bbox"]) for i in infos if pymupdf.Rect(i["bbox"]).contains(pe.point)), None)
        if not sel and hit is None:
            return
        sub = menu.addMenu(_("Assistente IA"))
        bind_icon(sub.menuAction(), "sparkles")
        enabled = services.store.active() is not None and content.extraction_allowed(view.document.pdf)
        if sel:
            for key, text in (("chat", "Chiedi"), ("summary", "Riassumi"), ("translate", "Traduci"),
                              ("explain", "Spiega"), ("rewrite", "Correggi"), ("rewrite-improve", "Riscrivi")):
                a = sub.addAction(_(text))
                a.setEnabled(enabled)
                a.triggered.connect(lambda _c=False, k=key: selection_action(k))
        if hit is not None:
            a = sub.addAction(_("Chiedi all'IA su quest'immagine"))
            a.setEnabled(enabled)
            a.triggered.connect(lambda _c=False, p=pe.page, r=hit: (ctx.set_mode("ia"), show_panel(),
                                                                     panel.set_area(p, r)))
        if not enabled:
            sub.addAction(_("Configura un fornitore")).triggered.connect(lambda: services.open_settings())

    def selection_action(key: str) -> None:
        ctx.set_mode("ia")
        show_panel()
        if key == "rewrite-improve":
            panel.set_function("rewrite")
            panel.rewrite_action.setCurrentIndex(panel.rewrite_action.findData("improve"))
        else:
            panel.set_function(key)
            if key == "rewrite":
                panel.rewrite_action.setCurrentIndex(panel.rewrite_action.findData("correct"))
        panel.set_scope("selection")
        if key != "chat":
            panel.send()
        else:
            panel.input.setFocus()

    ctx.add_view_menu_provider(menu_provider)

    def on_view(view) -> None:
        services.refresh()

    from PySide6.QtCore import QTimer
    ctx.documentOpened.connect(lambda v: QTimer.singleShot(0, services.refresh))

    def on_closed(view) -> None:
        for w in services.running(view.document):
            w.cancel()
        panel.forget_document(view.document)

    ctx.currentViewChanged.connect(on_view)
    ctx.documentClosed.connect(on_closed)
    ctx.documentChanged.connect(panel.on_document_changed)
    services.refresh()
    return services
