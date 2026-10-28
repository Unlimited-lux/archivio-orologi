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
        self.embedder = None      # funzione vettori simulabile nei test

    def gateway(self) -> Gateway:
        """Da chiamare nel thread dell'interfaccia: il profilo viene fissato qui."""
        gw = Gateway(self.store, self.log, provider_factory=self.provider_factory)
        gw.fixed_profile = self.store.active()
        return gw

    def history(self) -> history.HistoryStore:
        enabled = self.store.option("history_enabled", True)
        days = self.store.option("retention_days", 0)
        if self._history is None:
            self._history = history.HistoryStore()
        self._history.enabled = enabled
        self._history.retention_days = days
        self._history.encrypt = self.store.option("history_encrypt", False)
        self._history.secrets = self.store.secrets
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
        try:
            if self.panel is not None:
                self.panel.refresh_state()
            configured = self.store.active(check_key=False) is not None   # il portachiavi si apre al primo uso
            for a in getattr(self, "doc_actions", []):
                a.setEnabled(configured and self.ctx.current_view() is not None)
        except RuntimeError:        # finestra già distrutta (aggiornamento differito arrivato tardi)
            return
        for a in getattr(self, "profile_actions", []):
            a.setEnabled(configured)
        for a in getattr(self, "local_actions", []):
            a.setEnabled(self.ctx.current_view() is not None)

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
        if getattr(h, "locked", False):
            notes.append(_("Alcune conversazioni sono cifrate e il portachiavi non è disponibile."))
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
            try:
                atomic_write(path, text.encode("utf-8"))
            except FoliantError as e:
                QMessageBox.warning(self, _("Esporta conversazione"), str(e))

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
        if not content.extraction_allowed(doc):
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
        source = content.sanitized_copy(doc)   # niente testo nascosto o sotto oscuramenti
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

    # ---------------------------------------------------------------- utilità comuni
    def _job(self, progress, gateway=None, language=None):
        if language is None:
            language = self.services.panel._answer_language() if self.services.panel else "it"
        return engine.Job(gateway or self.services.gateway(), cancel=_CancelBridge(progress), language=language)

    def _run(self, title, fn):
        """Esegue `fn(job)` con avanzamento; None se annullato o in errore (già segnalato)."""
        panel = self.services.panel
        if self.services.store.option("confirm_each", False) and panel is not None and not panel.ask_preview(
                _("Verrà inviato: ") + title, _("Contenuto richiesto dall'operazione «{t}», secondo l'ambito "
                                                "scelto.").format(t=title.rstrip("…"))):
            return None
        gateway = self.services.gateway()
        language = panel._answer_language() if panel is not None else "it"
        try:
            return self.ctx.run_task(title, lambda progress: fn(self._job(progress, gateway, language)))
        except OperationCancelled:
            self.ctx.status(_("Operazione annullata."))
        except engine.ConsentRequired:
            self.ctx.error(title, _("Accetta l'informativa del profilo prima di inviare dati."))
        except FoliantError as e:
            self.ctx.error(title, str(e))
        except Exception as e:  # noqa: BLE001 - un errore del modulo non chiude l'app
            from .providers import AIError
            self.ctx.error(title, e.user_message() if isinstance(e, AIError) else str(e))
        return None

    def _ready_view(self):
        view = self.ctx.current_view()
        panel = self.services.panel
        if view is None or panel is None:
            return None
        if not content.extraction_allowed(view.document):
            self.ctx.error(_("Assistente IA"), content.blocked_reason(view.document.pdf))
            return None
        if not panel._ensure_ready():
            return None
        return view

    # ---------------------------------------------------------------- attività (AI-20)
    def open_tasks(self):
        from .tasks_library import TaskStore
        from .tasks_view import TasksDialog
        view = self.ctx.current_view()
        panel = self.services.panel
        dlg = TasksDialog(TaskStore(), self.ctx.window, has_document=view is not None,
                          has_selection=bool(view and view.text_selection),
                          ask_save_path=self.ctx.ask_save_path,
                          ask_open_path=lambda: (self.ctx.ask_open_paths(_("Importa attività"), "JSON (*.json)",
                                                                         multiple=False) or [None])[0],
                          on_error=lambda m: self.ctx.error(_("Attività"), m))
        if self.exec_dialog(dlg) != QDialog.DialogCode.Accepted or dlg.chosen is None:
            return dlg
        self.ctx.set_mode("ia")
        if view is not None:
            self.ctx.show_properties("ia")
        panel.run_template(dlg.chosen, dlg.values(), dlg.scope())
        return dlg

    # ---------------------------------------------------------------- scrittura (AI-19)
    def open_writer(self):
        from . import writer
        from .write_view import WriteDialog
        view = self.ctx.current_view()
        panel = self.services.panel
        if panel is None or not panel._ensure_ready():
            return None
        doc = view.document if view is not None else None

        def generate(params):
            scope = None
            if params.get("use_document") and doc is not None:
                if not content.extraction_allowed(doc):
                    raise FoliantError(content.blocked_reason(doc.pdf))
                scope = engine.build_scope(doc.pdf, engine.DOCUMENT, cache=self.services.cache,
                                           fingerprint=content.content_fingerprint(doc.pdf))
            ans = self._run(_("Scrittura in corso…"), lambda job: writer.write_text(
                job, params["kind"], params["instructions"], params["tone"], params["length_words"],
                params["language"], scope))
            return ans if ans is not None else ""

        def insert(text):
            if doc is None or doc.closed:
                return
            dlg.accept()
            self.status_pick = True
            self.ctx.status(_("Fai clic sul punto della pagina in cui inserire il testo."))
            self.ctx.set_tool(_PointTool(lambda page, point: self._insert_written(doc, page, point, text)))

        def new_pdf(text):
            from foliant.api import Document
            data = writer.text_to_new_pdf(text)
            self.ctx.open_document(Document.from_bytes(data, display_name=_("Testo generato.pdf")))
            dlg.accept()

        dlg = WriteDialog(generate, on_insert=insert, on_new_pdf=new_pdf, has_document=doc is not None,
                          language=panel._answer_language(), parent=self.ctx.window)
        self.exec_dialog(dlg)
        return dlg

    def _insert_written(self, doc, page, point, text):
        from . import writer
        self.ctx.set_tool(None)
        try:
            writer.insert_text_box_at(doc, page, (point.x, point.y), text)
        except FoliantError as e:
            self.ctx.error(_("Inserisci nel documento"), str(e))

    # ---------------------------------------------------------------- segnalibri (AI-22)
    def generate_bookmarks(self):
        from . import bookmarks_ai
        from .write_view import BookmarksDialog
        view = self._ready_view()
        if view is None:
            return None
        doc = view.document
        result = self._run(_("Generazione dei segnalibri…"), lambda job: bookmarks_ai.propose_bookmarks(job, doc.pdf))
        if result is None:
            return None
        nodes, warnings = result
        with ENGINE_LOCK:
            has_existing = bool(doc.pdf.get_toc())
        dlg = BookmarksDialog(nodes, warnings, has_existing=has_existing,
                              on_apply=lambda n, m: bookmarks_ai.apply_bookmarks(doc, n, m), parent=self.ctx.window)
        self.exec_dialog(dlg)
        return dlg

    def summarize_section(self):
        """Riassunto della sezione (segnalibro) che contiene la pagina corrente."""
        from . import bookmarks_ai
        view = self.ctx.current_view()
        panel = self.services.panel
        if view is None or panel is None:
            return None
        with ENGINE_LOCK:
            toc = view.document.pdf.get_toc(simple=False)
        page = view.current_page
        index = None
        for i, entry in enumerate(toc):
            if entry[2] - 1 <= page:
                index = i
        if index is None:
            self.ctx.info(_("Riassumi sezione"), _("Il documento non ha segnalibri prima di questa pagina."))
            return None
        pages = bookmarks_ai.section_pages(toc, index, view.page_count)
        self.ctx.set_mode("ia")
        self.ctx.show_properties("ia")
        panel.set_function("summary")
        panel.set_scope("pages", engine.compact_ranges(pages).replace("–", "-"))
        return panel.send()

    # ---------------------------------------------------------------- estrazione dati (AI-10)
    def extract_data(self, kind: str = "tables", fields=None):
        from . import extract
        from .extract_view import ExtractDialog
        view = self._ready_view()
        if view is None:
            return None
        panel = self.services.panel
        doc = view.document
        kind_scope = panel.scope_kind()
        try:
            if kind_scope == engine.SELECTION and view.text_selection:
                scope = engine.build_scope(doc.pdf, engine.SELECTION, selection=view.text_selection)
            elif kind_scope == engine.PAGES:
                scope = engine.build_scope(doc.pdf, engine.PAGES, pages=content.parse_page_range(
                    panel.pages_edit.text(), doc.page_count), cache=self.services.cache,
                    fingerprint=content.content_fingerprint(doc.pdf))
            else:
                scope = engine.build_scope(doc.pdf, engine.DOCUMENT, cache=self.services.cache,
                                           fingerprint=content.content_fingerprint(doc.pdf))
        except ValueError as e:
            self.ctx.error(_("Estrai dati"), str(e))
            return None
        if kind == "invoice":
            local = extract.embedded_einvoices(doc.pdf)
            if local:
                # fattura elettronica allegata: dati letti in locale, nessuna richiesta al fornitore
                result = extract.build_result("invoice", local, scope)
                result.model = _("fattura elettronica allegata")
                dlg = ExtractDialog(result, self.ctx.ask_save_path, self.ctx.window, source_name=doc.display_name)
                self.exec_dialog(dlg)
                return dlg
        gateway = self.services.gateway()
        language = panel._answer_language()
        try:
            result = self.ctx.run_task(_("Estrazione dei dati…"), lambda progress: extract.extract_data(
                self._job(progress, gateway, language), scope, kind, fields, source_name=doc.display_name))
        except OperationCancelled:
            return None
        except extract.ExtractError as e:
            from .extract_view import RawDialog
            self.ctx.error(_("Estrai dati"), str(e))
            self.exec_dialog(RawDialog(e.raw, self.ctx.window))
            return None
        except FoliantError as e:
            self.ctx.error(_("Estrai dati"), str(e))
            return None
        except Exception as e:  # noqa: BLE001
            from .providers import AIError
            self.ctx.error(_("Estrai dati"), e.user_message() if isinstance(e, AIError) else str(e))
            return None
        dlg = ExtractDialog(result, self.ctx.ask_save_path, self.ctx.window, source_name=doc.display_name)
        self.exec_dialog(dlg)
        return dlg

    def extract_invoice_files(self, paths: list[str] | None = None):
        """Fatture da più PDF: un record per fattura con il file d'origine (AI-29)."""
        from foliant.api import Document, DocumentError

        from . import extract
        from .extract_view import ExtractDialog
        panel = self.services.panel
        if panel is None or not panel._ensure_ready():
            return None
        paths = paths if paths is not None else self.ctx.ask_open_paths(_("Fatture da elaborare"))
        if not paths:
            return None
        named, problems = [], []
        gateway = self.services.gateway()
        language = panel._answer_language()
        for path in paths:
            name = os.path.basename(path)
            try:
                doc = Document.open(path)
            except DocumentError as e:
                problems.append(f"{name}: {e}")
                continue
            try:
                if not content.extraction_allowed(doc):
                    problems.append(f"{name}: " + _("estrazione del contenuto non consentita"))
                    continue
                scope = engine.build_scope(doc.pdf, engine.DOCUMENT)
                local = extract.embedded_einvoices(doc.pdf)
                if local:
                    res = extract.build_result("invoice", local, scope)
                else:
                    res = self.ctx.run_task(_("Estrazione da {f}…").format(f=name), lambda progress, sc=scope:
                                            extract.extract_data(self._job(progress, gateway, language), sc,
                                                                 "invoice"))
                named.append((name, res))
            except OperationCancelled:
                return None
            except Exception as e:  # noqa: BLE001 - un file non leggibile non ferma gli altri
                from .providers import AIError
                problems.append(f"{name}: " + (e.user_message() if isinstance(e, AIError) else str(e)))
            finally:
                doc.close()
        if not named:
            self.ctx.error(_("Estrai dati"), "\n".join(problems) or _("Nessun dato trovato."))
            return None
        result = extract.merge_results(named)
        result.checks.extend(problems)
        dlg = ExtractDialog(result, self.ctx.ask_save_path, self.ctx.window, source_name=result.source)
        self.exec_dialog(dlg)
        return dlg

    def choose_extraction(self):
        from PySide6.QtWidgets import QInputDialog
        kinds = [(_("Tabelle"), "tables"), (_("Fattura o ricevuta"), "invoice"),
                 (_("Fatture da più file"), "invoice-files")]
        label, ok = QInputDialog.getItem(self.ctx.window, _("Estrai dati"), _("Tipo di dati:"),
                                         [k[0] for k in kinds], 0, False)
        if ok:
            kind = dict(kinds)[label]
            return self.extract_invoice_files() if kind == "invoice-files" else self.extract_data(kind)
        return None

    # ---------------------------------------------------------------- ricerca per significato (AI-21)
    def semantic_search(self, query: str | None = None):
        from . import semantic
        from .semantic_view import SemanticSearchDialog, result_quads
        view = self._ready_view()
        if view is None:
            return None
        doc = view.document
        services = self.services
        if not hasattr(services, "vectors"):
            services.vectors = semantic.VectorCache()

        def run(q):
            profile = services.store.active()
            embed = None
            if profile is not None and profile.embed_model:
                if services.embedder is not None:
                    embed = services.embedder
                elif profile.protocol == "openai":
                    prov = services.store.provider_for(profile)
                    embed = lambda texts: semantic.embed_openai(prov, profile.embed_model, texts)  # noqa: E731
            fp = content.content_fingerprint(doc.pdf)
            return self._run(_("Ricerca per significato…"), lambda job: semantic.search(
                job, doc.pdf, q, embed=embed, embed_model=profile.embed_model if profile else "", fingerprint=fp,
                cache=services.vectors))

        def open_result(r):
            if view.document.closed:
                return
            quads = result_quads(r)
            view.go_to_page(r.page)
            view.set_search_hits({r.page: quads}, (r.page, 0) if quads else None)
            if quads:
                view.ensure_visible(r.page, quads[0].rect)

        def ask(r):
            panel = services.panel
            self.ctx.set_mode("ia")
            self.ctx.show_properties("ia")
            panel.set_function("chat")
            panel.set_scope("pages", str(r.page + 1))
            panel.input.setPlainText(_("A proposito di questo passaggio: «{t}» — ").format(t=r.excerpt[:300]))

        def highlight(r):
            with doc.change(_("Evidenzia risultato")) as pdf:
                annot = pdf[r.page].add_highlight_annot(result_quads(r))
                annot.update()

        dlg = SemanticSearchDialog(run, open_result, ask, highlight, self.ctx.window)
        if query:
            dlg.search(query)
        self.exec_dialog(dlg)
        return dlg

    # ---------------------------------------------------------------- più documenti (AI-13)
    def open_collection(self):
        from . import collection
        from .collection_view import CollectionDialog
        panel = self.services.panel
        if panel is None or not panel._ensure_ready():
            return None

        def open_docs():
            return {str(v.document.path): v.document for v in self.ctx.views() if v.document.path}

        def run_chat(conv, prepared, question):
            return self._run(_("Domanda sulla raccolta…"), lambda job: collection.chat(job, conv, prepared, question))

        def open_ref(path, page):
            view = next((v for v in self.ctx.views() if str(v.document.path) == path), None)
            if view is None:
                view = self.ctx.open_path(path)
            else:
                self.ctx.window.tabs.setCurrentWidget(view)
            if view is not None and 0 <= page < view.page_count:
                view.go_to_page(page)

        def ask_paths():
            return self.ctx.ask_open_paths(_("Aggiungi documenti"))

        dlg = CollectionDialog(self.ctx, run_chat, open_ref, ask_paths, open_docs(), self.ctx.window,
                               cache=self.services.cache)
        self.exec_dialog(dlg)
        return dlg

    # ---------------------------------------------------------------- ricerca bibliografica (AI-26)
    def open_bibliography(self):
        from . import biblio
        from .biblio_view import BiblioDialog
        panel = self.services.panel
        if panel is None or not panel._ensure_ready():
            return None
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        def open_url(url):
            if self.ctx.confirm(_("Collegamento esterno"),
                                _("Aprire nel browser questo indirizzo?\n\n{u}").format(u=url)):
                QDesktopServices.openUrl(QUrl(url))

        def run_search(title, fn):
            try:
                return self.ctx.run_task(title, lambda progress: fn())
            except OperationCancelled:
                return None
            except Exception as e:  # noqa: BLE001
                from .providers import AIError
                self.ctx.error(title, e.user_message() if isinstance(e, AIError) else str(e))
                return None

        dlg = BiblioDialog(self._run, biblio.service_from_settings(self.services.store), self.ctx.ask_save_path,
                           open_url, self.ctx.window, store=self.services.store, run_search=run_search,
                           on_error=lambda t, m: self.ctx.error(t, m))
        self.exec_dialog(dlg)
        return dlg

    # ---------------------------------------------------------------- elementi grafici (AI-25)
    def generate_graphic(self, kind: str = "stamp"):
        import pymupdf

        from . import graphics
        from .graphics_view import GenerateGraphicDialog
        view = self.ctx.current_view()
        if view is None:
            return None
        doc = view.document
        profile = self.services.store.active()

        def run(title, fn):
            try:
                return self.ctx.run_task(title, lambda progress: fn(progress=progress))
            except OperationCancelled:
                return None

        provider = (lambda: self.services.store.provider_for(profile)) if profile else None
        dlg = GenerateGraphicDialog(profile, provider, run, self.ctx.window, kind=kind)
        self.exec_dialog(dlg)
        png = getattr(dlg, "chosen_png", None)
        if not png:
            return dlg
        chosen = dlg.kind
        try:
            if chosen in ("stamp", "sticker"):
                page = view.current_page
                with ENGINE_LOCK:
                    r = doc.pdf[page].rect
                w = min(r.width, r.height) * 0.3
                rect = pymupdf.Rect(r.x1 - w - 36, 36, r.x1 - 36, 36 + w)
                graphics.apply_as_stamp(doc, page, rect, png)
            elif chosen == "watermark":
                graphics.apply_as_watermark(doc, list(range(doc.page_count)), png)
            else:
                graphics.apply_as_background(doc, list(range(doc.page_count)), png)
        except FoliantError as e:
            self.ctx.error(_("Genera con IA"), str(e))
        return dlg

    # ---------------------------------------------------------------- conversione assistita (AI-27)
    def convert_to_docx(self, path: str | None = None):
        from . import convert_assist
        view = self._ready_view()
        if view is None:
            return None
        doc = view.document
        if path is None:
            base = os.path.splitext(doc.display_name)[0]
            path = self.ctx.ask_save_path(_("Converti in documento di testo"), f"{base}.docx",
                                          _("Documento Word (*.docx)"), ".docx")
        if not path:
            return None
        pages = list(range(doc.page_count))
        structure = self._run(_("Analisi della struttura…"), lambda job: convert_assist.classify(job, doc.pdf, pages))
        if structure is None:
            return None
        try:
            report = convert_assist.to_docx(doc.pdf, pages, structure, path)
        except Exception as e:  # noqa: BLE001
            self.ctx.error(_("Converti in documento di testo"), str(e))
            return None
        msgs = report.get("messages") or []
        if msgs:
            self.ctx.info(_("Converti in documento di testo"), "\n".join(msgs))
        else:
            self.ctx.status(_("Documento salvato in {p}").format(p=path))
        return report

    # ---------------------------------------------------------------- comandi (AI-23)
    def tool_catalog(self):
        from . import commands
        actions = []
        for mode_id, mode in self.ctx.window.modes.items():
            if mode_id == "ia":
                continue
            for a in mode.toolbar.actions():
                if not a.isSeparator():
                    actions.append((mode_id, a))
        return commands.builtin_tools(self.ctx) + commands.catalog_from_actions(actions)

    def open_commands(self, request: str | None = None):
        from . import commands
        view = self.ctx.current_view()
        panel = self.services.panel
        if view is None or panel is None or not panel._ensure_ready():
            return None
        catalog = self.tool_catalog()
        sel_pages = [view.text_selection.page] if view.text_selection else []

        def propose(q):
            return self._run(_("Ricerca dello strumento…"), lambda job: commands.propose(
                job, q, catalog, view.page_count, sel_pages))

        dlg = CommandsDialog(propose, self.ctx.window)
        if request:
            dlg.ask(request)
        self.exec_dialog(dlg)
        if dlg.chosen is not None:
            self.run_proposal(dlg.chosen)
        return dlg

    def run_proposal(self, proposal) -> None:
        act = proposal.tool.action
        if callable(act) and not hasattr(act, "trigger"):
            act(proposal.params)
        elif act is not None:
            act.trigger()   # lo strumento chiede le sue conferme come nell'uso manuale

    # ---------------------------------------------------------------- scheda prodotto (AI-30)
    def open_product_sheet(self):
        from foliant.api import Document

        from . import product_sheet
        from .product_sheet_view import ProductSheetDialog
        view = self._ready_view()
        if view is None:
            return None
        doc = view.document
        panel = self.services.panel

        def generate(language, tone, length):
            kind = panel.scope_kind()
            pages = None
            if kind == engine.PAGES:
                try:
                    pages = content.parse_page_range(panel.pages_edit.text(), doc.page_count)
                except ValueError as e:
                    self.ctx.error(_("Scheda prodotto"), str(e))
                    return None
            scope = engine.build_scope(doc.pdf, engine.PAGES if pages else engine.DOCUMENT, pages=pages,
                                       cache=self.services.cache, fingerprint=content.content_fingerprint(doc.pdf))
            return self._run(_("Scheda prodotto…"), lambda job: product_sheet.make_product_sheet(
                job, scope, language, tone, length))

        def open_document(data):
            self.ctx.open_document(Document.from_bytes(data, display_name=_("Scheda prodotto.pdf")))

        dlg = ProductSheetDialog(generate, self.ctx.ask_save_path, open_document, self.ctx.window)
        self.exec_dialog(dlg)
        return dlg

    # ---------------------------------------------------------------- confronto e classifica (AI-32)
    def open_ranking(self):
        from . import ranking
        from .ranking_view import RankingDialog
        panel = self.services.panel
        if panel is None or not panel._ensure_ready():
            return None

        def run_eval(prepared, excluded, criteria, cv):
            return self._run(_("Valutazione dei documenti…"), lambda job: ranking.evaluate(
                job, prepared, criteria, cv_mode=cv, excluded=excluded))

        def open_ref(path, page):
            view = next((v for v in self.ctx.views() if str(v.document.path) == path), None)
            if view is None:
                view = self.ctx.open_path(path)
            else:
                self.ctx.window.tabs.setCurrentWidget(view)
            if view is not None and 0 <= page < view.page_count:
                view.go_to_page(page)

        docs = {str(v.document.path): v.document for v in self.ctx.views() if v.document.path}
        dlg = RankingDialog(run_eval, open_ref, lambda: self.ctx.ask_open_paths(_("Aggiungi documenti")),
                            self.ctx.ask_save_path, docs, self.ctx.window, cache=self.services.cache)
        self.exec_dialog(dlg)
        return dlg

    # ---------------------------------------------------------------- analisi pagine (AI-24)
    def analyze_pages(self):
        from . import page_analysis
        from .page_analysis_view import PageAnalysisDialog
        view = self.ctx.current_view()
        if view is None:
            return None
        doc = view.document
        try:
            issues = self.ctx.run_task(_("Analisi delle pagine…"), lambda progress: page_analysis.analyze(
                doc.pdf, progress))
        except OperationCancelled:
            return None
        job_factory = None
        if self.services.store.active() is not None and content.extraction_allowed(doc):
            job_factory = lambda: engine.Job(self.services.gateway())  # noqa: E731
        dlg = PageAnalysisDialog(doc, issues, job_factory, self.ctx.window)
        self.exec_dialog(dlg)
        result = getattr(dlg, "result", None)
        if result is not None and getattr(result, "annotations_text", ""):
            InfoTextDialog(_("Annotazioni estratte"), "<pre>" + result.annotations_text.replace("<", "&lt;") +
                           "</pre>", self.ctx.window).exec()
        return dlg

    def exec_dialog(self, dlg) -> int:
        return dlg.exec()

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


class _PointTool:
    """Strumento usa e getta: un clic sulla pagina restituisce (pagina, punto)."""

    def __new__(cls, callback):
        from foliant.api import Tool

        class PointTool(Tool):
            id = "ai-point"
            cursor = Qt.CursorShape.CrossCursor

            def press(self, view, ev):
                if ev.page is None or ev.button != Qt.MouseButton.LeftButton:
                    return False
                callback(ev.page, ev.point)
                return True
        return PointTool()


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


# =================================================================== operazioni in corso (AI-17)
class OperationsDialog(QDialog):
    def __init__(self, services: Services, parent=None):
        super().__init__(parent)
        self.services = services
        self.setWindowTitle(_("Operazioni dell'assistente"))
        self.resize(520, 300)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        stop = QPushButton(_("Annulla l'operazione"))
        bind_icon(stop, "circle-stop")
        stop.clicked.connect(self.cancel_selected)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.accept)
        row.addWidget(stop)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.reload()

    def reload(self) -> None:
        self.list.clear()
        for w in self.services.workers:
            doc = w.document.display_name if w.document is not None else _("nessun documento")
            text = f"{w.function} · {doc} · {w.started.strftime('%H:%M:%S')} · {w.state}"
            if w.phase:
                text += f" · {w.phase}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, w)
            self.list.addItem(item)
        if not self.services.workers:
            self.list.addItem(_("Nessuna operazione in corso."))

    def cancel_selected(self) -> None:
        item = self.list.currentItem()
        w = item.data(Qt.ItemDataRole.UserRole) if item else None
        if w is not None:
            w.cancel()
            self.reload()


# =================================================================== assistente comandi (AI-23)
class CommandsDialog(QDialog):
    def __init__(self, propose, parent=None):
        super().__init__(parent)
        self.propose = propose
        self.proposals = []
        self.setWindowTitle(_("Assistente comandi"))
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        lab = QLabel(_("Descrivi cosa vuoi fare, ad esempio «vai a pagina 12». Il contenuto del documento non "
                       "viene inviato; l'operazione parte solo quando la confermi nello strumento."))
        lab.setWordWrap(True)
        lay.addWidget(lab)
        row = QHBoxLayout()
        self.request = QLineEdit()
        self.request.returnPressed.connect(self.ask)
        go = QPushButton(_("Proponi"))
        go.clicked.connect(self.ask)
        row.addWidget(self.request, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _i: self.choose())
        lay.addWidget(self.list, 1)
        self.notice = QLabel("")
        self.notice.setWordWrap(True)
        lay.addWidget(self.notice)
        row = QHBoxLayout()
        use = QPushButton(_("Apri lo strumento"))
        use.clicked.connect(self.choose)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.reject)
        row.addStretch(1)
        row.addWidget(close)
        row.addWidget(use)
        lay.addLayout(row)
        self.chosen = None

    def ask(self, text: str | None = None) -> None:
        if text is not None:
            self.request.setText(text)
        q = self.request.text().strip()
        if not q:
            return
        result = self.propose(q)
        if result is None:
            return
        self.proposals, rejected = result
        self.list.clear()
        for p in self.proposals:
            params = ", ".join(f"{k}={v}" for k, v in p.params.items())
            self.list.addItem(f"{p.tool.description}" + (f" ({params})" if params else "") +
                              (f" — {p.explanation}" if p.explanation else ""))
        notes = list(rejected)
        if not self.proposals:
            notes.append(_("Nessuna proposta valida: prova a riformulare o scegli lo strumento dalla barra."))
        self.notice.setText(" ".join(notes))
        if self.proposals:
            self.list.setCurrentRow(0)

    def choose(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.proposals):
            self.chosen = self.proposals[row]
            self.accept()


# =================================================================== chat generale senza documento
class GeneralChatDialog(QDialog):
    def __init__(self, ctx, services, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(_("Assistente IA — chat generale"))
        self.resize(520, 640)
        lay = QVBoxLayout(self)
        self.panel = AIPanel(ctx, services, self, standalone=True)
        self.panel.set_scope("none")
        lay.addWidget(self.panel)

    def done(self, result) -> None:
        if self.panel.worker is not None:
            self.panel.worker.cancel()
        super().done(result)


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
    mode.scope_shortcuts(a_chat)   # scorciatoia interna: vale solo nella modalità Assistente IA
    a_sum = act(_("Riassumi"), "scroll-text", run("summary"))
    a_tr = act(_("Traduci"), "languages", run("translate"))
    a_exp = act(_("Spiega"), "lightbulb", run("explain"))
    a_rw = act(_("Correggi e riscrivi"), "spell-check", run("rewrite"))
    a_out = act(_("Scaletta"), "list-tree", run("outline"))
    a_map = act(_("Mappa concettuale"), "network", run("map"))
    a_area = act(_("Chiedi su un'area"), "square-dashed-mouse-pointer",
                 lambda: (ctx.set_mode("ia"), show_panel(), panel.start_area_tool()))
    a_trpdf = act(_("Traduci in un nuovo PDF"), "file-output", lambda: controller.translate_pdf())
    a_coll = act(_("Più documenti"), "files", lambda: controller.open_collection(), requires=False)
    a_sheet = act(_("Scheda prodotto"), "tag", lambda: controller.open_product_sheet())
    a_rank = act(_("Confronta e classifica"), "list-ordered", lambda: controller.open_ranking(), requires=False)
    a_gfx = act(_("Genera elemento grafico"), "image-plus", lambda: controller.generate_graphic())
    a_docx = act(_("Converti in Word con l'IA"), "file-text", lambda: controller.convert_to_docx())
    a_bib = act(_("Ricerca bibliografica"), "library", lambda: controller.open_bibliography(), requires=False)
    a_cmd = act(_("Assistente comandi"), "terminal", lambda: controller.open_commands())
    a_ops = act(_("Operazioni in corso"), "activity", lambda: OperationsDialog(services, ctx.window).exec(),
                requires=False)
    a_open = act(_("Assistente IA"), "sparkles", lambda: (ctx.set_mode("ia"), show_panel()), "Ctrl+Alt+I",
                 requires=False)
    ctx.add_menu_action(a_open, _("Assistente IA"))
    a_tasks = act(_("Attività"), "briefcase", lambda: controller.open_tasks(), requires=False)
    a_write = act(_("Scrivi"), "pen-line", lambda: controller.open_writer(), requires=False)
    a_extract = act(_("Estrai dati"), "table", lambda: controller.choose_extraction())
    a_sem = act(_("Cerca per significato"), "search", lambda: controller.semantic_search())
    a_bm = act(_("Genera segnalibri"), "bookmark-plus", lambda: controller.generate_bookmarks())
    a_section = act(_("Riassumi sezione"), "bookmark", lambda: controller.summarize_section())
    a_pages = act(_("Analizza pagine"), "scan-search", lambda: controller.analyze_pages())
    a_hist = act(_("Cronologia"), "history", lambda: services.show_history(), requires=False)
    a_log = act(_("Registro degli invii"), "list", lambda: services.show_log(), requires=False)
    a_set = act(_("Impostazioni dell'Assistente IA"), "settings", lambda: services.open_settings(), requires=False)
    services.doc_actions = [a_chat, a_sum, a_tr, a_exp, a_rw, a_out, a_map, a_area, a_trpdf, a_extract, a_bm,
                            a_section, a_sem, a_cmd, a_docx, a_sheet]
    services.profile_actions = [a_tasks, a_write, a_coll, a_bib, a_rank]
    services.local_actions = [a_pages, a_gfx]   # la generazione spiega da sé se manca il modello   # funziona anche senza profilo (controlli locali)
    for a in (a_chat, a_sum, a_tr, a_exp, a_rw, a_map, a_area):
        mode.add_action(a)
    mode.add_separator()
    from PySide6.QtWidgets import QToolButton
    more = QToolButton()
    more.setText(_("Altri strumenti"))
    more.setToolTip(_("Altri strumenti dell'assistente"))
    bind_icon(more, "ellipsis")
    more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    more.setAutoRaise(True)
    more_menu = QMenu(more)
    for group in ((a_trpdf, a_out, a_extract, a_sheet, a_sem), (a_bm, a_section, a_pages, a_gfx, a_docx), (a_coll, a_rank, a_tasks, a_write, a_bib, a_cmd),
                  (a_ops, a_log)):
        for a in group:
            more_menu.addAction(a)
        more_menu.addSeparator()
    more.setMenu(more_menu)
    services.more_menu = more_menu
    mode.add_widget(more)
    mode.add_separator()
    a_set.setIconText(_("Impostazioni"))
    for a in (a_hist, a_set):
        mode.add_action(a)
    pages_mode = ctx.mode("pagine")
    if pages_mode is not None:
        pages_mode.add_action(a_pages)
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
        enabled = services.store.active() is not None and content.extraction_allowed(view.document)
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
