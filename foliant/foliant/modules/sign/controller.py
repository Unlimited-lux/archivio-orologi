"""Controller del modulo Firma: collega strumenti, dialoghi, motore e pannello."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox

from foliant.api import ENGINE_LOCK, SAVED, Document, DocumentError, _, atomic_write

from . import graphic as g
from .library import LibraryError, LibraryLocked, SignatureLibrary
from .tools import RectTool, SignatureTool


def _sync() -> bool:
    return os.environ.get("FOLIANT_SYNC_TASKS") == "1"


@dataclass
class VerifyState:
    version: int = -1
    running: bool = False
    statuses: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    error: str = ""
    signed: bool = False
    certification: int | None = None
    warned: bool = False
    source: bytes | None = None     # byte verificati (file su disco se il documento non è modificato)
    base: bytes | None = None       # byte del file firmato su disco: base del salvataggio incrementale
    incremental: bool = False       # salvataggio incrementale installato sul documento
    signed_hint: bool = False
    warning_pending: bool = False       # il file contiene /ByteRange (firmato, anche se non ancora verificato)


class _Bridge(QObject):
    verified = Signal(object, int, object)   # documento, versione, risultato


class SignController(QObject):
    stateChanged = Signal(object)            # vista
    statusSelected = Signal(object, object)  # vista, SignatureStatus da selezionare nel pannello

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        self._library: SignatureLibrary | None = None
        self._tools: dict[int, SignatureTool] = {}
        self._states: dict[int, VerifyState] = {}
        self._bridge = _Bridge()
        self._bridge.verified.connect(self._on_verified)
        self._in_warning = False
        self._own_change = False
        ctx.documentOpened.connect(self._on_opened)
        ctx.documentClosed.connect(self._on_closed)
        ctx.documentChanged.connect(self._on_changed)

    # ================================================================ libreria
    def library(self, interactive: bool = True) -> SignatureLibrary | None:
        if self._library is None:
            try:
                self._library = SignatureLibrary()
            except LibraryError as e:
                self.ctx.error(_("Libreria firme"), str(e))
                return None
            if self._library.notice and interactive:
                self.ctx.info(_("Libreria firme"), self._library.notice)
        lib = self._library
        if lib.locked and interactive:
            from .ui_library import ask_password
            while lib.locked:
                pw = ask_password(self.ctx.window, _("Libreria protetta"),
                                  _("Inserisci la password della libreria delle firme:"))
                if pw is None:
                    return None
                try:
                    lib.unlock(pw)
                except LibraryLocked as e:
                    self.ctx.error(_("Libreria protetta"), str(e))
        return lib

    def default_name(self) -> str:
        return str(self.ctx.settings().value("user/name", "") or "")

    # ========================================================== firma grafica
    def tool_for(self, view) -> SignatureTool:
        t = self._tools.get(id(view))
        if t is None:
            t = SignatureTool(self)
            self._tools[id(view)] = t
        return t

    def create_signature(self, kind: str = "firma") -> None:
        view = self.ctx.current_view()
        from .ui_create import CreateSignatureDialog
        dlg = CreateSignatureDialog(self.ctx.window, kind=kind, default_name=self.default_name())
        if not dlg.exec() or dlg.item is None:
            return
        item = dlg.item
        if dlg.save_to_library:
            lib = self.library()
            if lib is not None:
                try:
                    lib.add(item, item.name or None)
                except LibraryError as e:
                    self.ctx.error(_("Libreria firme"), str(e))
        if view is not None:
            self.start_placement(view, item)

    def use_library_item(self, entry_id: str) -> None:
        view = self.ctx.current_view()
        lib = self.library()
        if view is None or lib is None:
            return
        try:
            item = lib.load_item(entry_id)
        except Exception as e:
            self.ctx.error(_("Libreria firme"), _("L'elemento della libreria non è utilizzabile: {e}").format(
                e=str(e) or e.__class__.__name__))
            return
        self.start_placement(view, item)

    def manage_library(self) -> None:
        lib = self.library()
        if lib is None:
            return
        from .ui_library import LibraryDialog
        LibraryDialog(lib, self.ctx.window, self.default_name()).exec()

    def start_placement(self, view, item: g.SignatureItem) -> None:
        if not self._annotations_allowed(view):
            return
        tool = self.tool_for(view)
        view.set_tool(tool)
        tool.set_pending(item)
        self.ctx.status(_("Fai clic sulla pagina per posizionare la firma, o trascina per definirne la dimensione; "
                          "Invio la posiziona al centro della pagina. Esc per annullare."), 8000)

    def _annotations_allowed(self, view) -> bool:
        with ENGINE_LOCK:
            perms = view.document.pdf.permissions
        if not perms & pymupdf.PDF_PERM_ANNOTATE:
            self.ctx.error(_("Firma"), _("I permessi del documento non consentono di aggiungere annotazioni o "
                                          "firme grafiche. Apri il documento con la password del proprietario "
                                          "per modificarlo."))
            return False
        return True

    def _change(self, view, label: str, fn, kind: str = "annotation"):
        """Esegue `fn(pdf)` come passo annullabile, con l'avviso SIG-21 se il documento è firmato."""
        if not self.confirm_invalidating(view, label, kind):
            return None
        self._own_change = True
        try:
            with view.document.change(label) as pdf:
                return fn(pdf)
        except (g.GraphicSignatureError, DocumentError) as e:
            self.ctx.error(_("Firma"), str(e))
            return None
        except Exception as e:
            self.tool_for(view).selected = None
            self.ctx.error(_("Firma"), _("Operazione non riuscita: {e}").format(e=str(e) or e.__class__.__name__))
            return None
        finally:
            self._own_change = False

    def place(self, view, page: int, rect: pymupdf.Rect, item: g.SignatureItem) -> int | None:
        author = self.default_name()
        label = _("Inserisci iniziali") if item.kind == "iniziali" else _("Inserisci firma")
        return self._change(view, label, lambda pdf: g.insert_signature(pdf, page, rect, item, author=author))

    def set_rect(self, view, page: int, xref: int, rect, label: str) -> None:
        self._change(view, label, lambda pdf: g.set_signature_rect(pdf, page, xref, rect))

    def delete_group(self, view, page: int, xref: int) -> None:
        """Elimina tutte le copie create con «Ripeti su più pagine» (stesso gruppo)."""
        with ENGINE_LOCK:
            sig = g.find_signature(view.document.pdf, page, xref)
        if sig is None:
            return
        group = g.group_of(sig.nm)

        def fn(pdf):
            n = 0
            for p in range(pdf.page_count):
                for s in g.placed_signatures(pdf, p):
                    if g.group_of(s.nm) == group:
                        g.delete_signature(pdf, p, s.xref)
                        n += 1
            return n

        self._change(view, _("Elimina gruppo di firme"), fn)
        self.tool_for(view).selected = None

    def ungroup(self, view, page: int, xref: int) -> None:
        with ENGINE_LOCK:
            sig = g.find_signature(view.document.pdf, page, xref)
        if sig is not None:
            self._change(view, _("Sciogli gruppo"), lambda pdf: g.ungroup(pdf, g.group_of(sig.nm)))

    def delete(self, view, page: int, xref: int) -> None:
        self._change(view, _("Elimina firma"), lambda pdf: g.delete_signature(pdf, page, xref))

    def selection_changed(self, view, tool) -> None:
        view.viewport().update()

    def duplicate(self, view, page: int, xref: int) -> None:
        new = self._change(view, _("Duplica firma"), lambda pdf: g.duplicate_signature(pdf, page, xref))
        if new:
            self.tool_for(view).selected = (page, new)

    def embed(self, view, page: int, xref: int) -> None:
        self._change(view, _("Incorpora firma nella pagina"), lambda pdf: g.embed_signature(pdf, page, xref),
                     kind="content")
        self.tool_for(view).selected = None

    def set_opacity(self, view, page: int, xref: int) -> None:
        val, ok = QInputDialog.getInt(self.ctx.window, _("Opacità"), _("Opacità della firma (%):"), 100, 5, 100)
        if ok:
            self._change(view, _("Opacità firma"), lambda pdf: g.set_signature_opacity(pdf, page, xref, val / 100))

    def repeat(self, view, page: int, xref: int) -> None:
        from .ui_digital import RepeatDialog
        dlg = RepeatDialog(self.ctx.window, view.page_count, page)
        if not dlg.exec():
            return
        pages, anchor = dlg.pages, dlg.anchor
        result = self._change(view, _("Ripeti firma su più pagine"),
                              lambda pdf: g.repeat_signature(pdf, page, xref, pages, anchor))
        if result:
            created, shrunk = result
            msg = _("Firma ripetuta su {n} pagine.").format(n=len(created))
            if shrunk:
                msg += " " + _("Ridotta per entrare nelle pagine: {p}.").format(
                    p=", ".join(str(p + 1) for p in shrunk))
            self.ctx.status(msg, 8000)

    def menu_provider(self, view, ev, menu: QMenu) -> None:
        if ev.page is None or ev.point is None:
            return
        for s in self.state(view).statuses:
            if s.page == ev.page and s.rect and not s.invisible and pymupdf.Rect(s.rect).contains(ev.point):
                menu.addAction(_("Dettagli della firma di {who}").format(who=s.signer or s.field_name),
                               lambda st=s: self.show_status(view, st))
        for f in self.empty_fields(view):
            if f.page == ev.page and f.rect and pymupdf.Rect(f.rect).contains(ev.point):
                menu.addAction(_("Firma qui"), lambda fld=f: self.digital_sign(view, field=fld))
        with ENGINE_LOCK:
            sigs = g.placed_signatures(view.document.pdf, ev.page)
        hit = next((s for s in reversed(sigs) if s.rect.contains(ev.point)), None)
        if hit is None:
            return
        tool = self.tool_for(view)
        tool.selected = (ev.page, hit.xref)
        if view.tool is not tool:
            view.set_tool(tool)
        if not menu.isEmpty():
            menu.addSeparator()
        p, x = ev.page, hit.xref
        menu.addAction(_("Duplica"), lambda: self.duplicate(view, p, x))
        menu.addAction(_("Elimina"), lambda: self.delete(view, p, x))
        menu.addAction(_("Ruota di 90°"), lambda: self._change(
            view, _("Ruota firma"), lambda pdf: g.rotate_signature(pdf, p, x, 90)))
        menu.addAction(_("Opacità…"), lambda: self.set_opacity(view, p, x))
        menu.addAction(_("Ripeti su più pagine…"), lambda: self.repeat(view, p, x))
        with ENGINE_LOCK:
            in_group = g.group_size(view.document.pdf, g.group_of(hit.nm)) > 1
        if in_group:
            menu.addAction(_("Elimina tutto il gruppo"), lambda: self.delete_group(view, p, x))
            menu.addAction(_("Sciogli gruppo"), lambda: self.ungroup(view, p, x))
        menu.addAction(_("Incorpora nella pagina"), lambda: self.embed(view, p, x))

    # ================================================== verifica e stato firme
    def state(self, view_or_doc) -> VerifyState:
        doc = getattr(view_or_doc, "document", view_or_doc)
        st = self._states.get(id(doc))
        if st is None:
            st = VerifyState()
            self._states[id(doc)] = st
        return st

    def auto_verify(self) -> bool:
        return str(self.ctx.settings().value("sign/auto_verify", "true")).lower() != "false"

    def _on_opened(self, view) -> None:
        self.detect_signed(view)
        if self.auto_verify():
            self.verify(view)

    def detect_signed(self, view) -> None:
        """Rilevamento sincrono ed economico delle firme: installa subito il
        salvataggio incrementale, anche prima (o senza) la verifica completa."""
        doc = view.document
        st = self.state(doc)
        if doc.path is None or doc.is_modified:
            return
        try:
            data = Path(doc.path).read_bytes()
        except OSError:
            return
        if b"/ByteRange" in data:
            st.base = data
            st.signed = True
            st.signed_hint = True
            if not st.incremental:
                self._install_incremental_save(doc, st)

    def _on_closed(self, view) -> None:
        self._states.pop(id(view.document), None)
        self._tools.pop(id(view), None)

    @staticmethod
    def document_bytes(doc: Document) -> bytes:
        """Byte da verificare/firmare: il file su disco se il documento non è
        modificato (conserva le revisioni firmate), altrimenti il contenuto attuale."""
        if doc.path is not None and not doc.is_modified and Path(doc.path).exists():
            try:
                return Path(doc.path).read_bytes()
            except OSError:
                pass
        return doc.to_bytes()

    def verify(self, view, force: bool = False) -> None:
        doc = view.document
        st = self.state(doc)
        if st.running and not force:
            return
        try:
            data = self.document_bytes(doc)
        except Exception as e:  # documento chiuso o illeggibile
            st.error = str(e)
            return
        if doc.path is not None and not doc.is_modified:
            st.base = data
        if b"/Sig" not in data and b"/ByteRange" not in data:
            st.version, st.statuses, st.fields, st.signed, st.certification = doc.version, [], [], False, None
            st.source = data
            st.error = ""
            self.stateChanged.emit(view)
            return
        st.running = True
        st.source = data
        version = doc.version
        use_system = str(self.ctx.settings().value("sign/system_certs", "false")).lower() == "true"
        self.stateChanged.emit(view)

        def work():
            from . import digital, store
            result: dict = {}
            try:
                trusted = store.TrustStore().certificates()
                if use_system:
                    trusted = trusted + store.system_certificates()
                mode = self.revocation_mode()
                result["statuses"] = digital.verify_pdf(data, trusted=trusted, revocation_mode=mode,
                                                        allow_fetching=mode == "sempre")
                result["fields"] = digital.signature_fields(data)
                result["certification"] = digital.certification_level(data)
            except Exception as e:
                result["error"] = str(e) or e.__class__.__name__
            return result

        if _sync():
            self._on_verified(doc, version, work())
        else:
            threading.Thread(target=lambda: self._bridge.verified.emit(doc, version, work()),
                             name="foliant-verify", daemon=True).start()

    def revocation_mode(self) -> str:
        return str(self.ctx.settings().value("sign/revocation", "mai"))

    def _on_verified(self, doc, version: int, result: dict) -> None:
        st = self._states.get(id(doc))
        if st is None or doc.closed:
            return
        st.running = False
        st.version = version
        st.error = result.get("error", "")
        st.statuses = result.get("statuses", [])
        st.fields = result.get("fields", [])
        st.certification = result.get("certification")
        st.signed = bool(st.statuses) or st.signed_hint
        if st.signed and st.base is not None and not st.incremental:
            self._install_incremental_save(doc, st)
        for view in self.ctx.views():
            if view.document is doc:
                self.stateChanged.emit(view)
                if st.signed and not doc.is_modified:
                    self.ctx.status(_("Documento firmato: modificare il contenuto invaliderà le firme."), 10000)

    def empty_fields(self, view) -> list:
        return [f for f in self.state(view).fields if not f.signed]

    # ============================================ salvataggio incrementale (SIG-21)
    def _install_incremental_save(self, doc: Document, st: VerifyState) -> None:
        """Per i documenti firmati «Salva» e «Salva una copia» accodano solo le
        modifiche al file firmato (vedi CORE_REQUESTS.md, richiesta 1)."""
        from .incremental import incremental_update
        original_save, original_copy = doc.save, doc.save_copy
        controller = self

        def build() -> bytes:
            try:
                with doc.lock:
                    return incremental_update(st.base, doc.pdf, doc.password)
            except Exception as e:  # mai un'eccezione non gestita verso la finestra
                raise DocumentError(_("Salvataggio incrementale non riuscito: {e}").format(
                    e=str(e) or e.__class__.__name__)) from e

        def save(path=None, **options):
            if doc.is_protected and doc.save_options != doc.original_protection:
                return original_save(path, **options)   # nuova protezione: riscrittura completa
            target = Path(path) if path is not None else doc.path
            if target is None:
                raise DocumentError(_("Scegli dove salvare il documento."))
            data = build()
            atomic_write(target, data)
            doc.path = target
            doc._saved_state = doc._state
            st.base = data
            doc._notify(SAVED)
            for v in controller.ctx.views():
                if v.document is doc:
                    controller.verify(v, force=True)

        def save_copy(path, **options):
            atomic_write(Path(path), build())

        doc.save = save
        doc.save_copy = save_copy
        doc._foliant_original_save = original_save
        st.incremental = True

    # =========================================================== SIG-21 avvisi
    def allowed_change(self, view, kind: str) -> bool:
        """True se una modifica del tipo indicato non invalida le firme presenti
        (annotazioni senza certificazione o con livello 3; moduli con livello 2 o 3)."""
        st = self.state(view)
        if not st.signed:
            return True
        if kind == "annotation":
            return st.incremental and st.certification in (None, 3)
        if kind == "form":
            return st.incremental and st.certification in (None, 2, 3)
        return False

    def confirm_invalidating(self, view, what: str, kind: str = "content") -> bool:
        """Prima di una modifica a un documento firmato: Annulla / Copia senza firme / Procedi."""
        st = self.state(view)
        if st.certification == 1:
            if self.ctx.confirm(_("Documento certificato"), _(
                    "Il documento è certificato con livello «nessuna modifica»: «{what}» non è consentito.\n\n"
                    "Vuoi lavorare su una copia senza firme?").format(what=what)):
                self.open_unsigned_copy(view)
            return False
        if not st.signed or st.warned or self.allowed_change(view, kind):
            return True
        choice = self._ask_invalidation(view, what)
        if choice == "proceed":
            st.warned = True
            return True
        if choice == "copy":
            self.open_unsigned_copy(view)
        return False

    def _ask_invalidation(self, view, what: str) -> str:
        st = self.state(view)
        names = "\n".join(f"• {s.field_name} — {s.signer}" for s in st.statuses)
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Documento firmato"))
        box.setText(_("«{what}» modifica il contenuto di un documento firmato: salvando, queste firme non "
                      "saranno più valide:").format(what=what) + "\n\n" + names)
        box.setInformativeText(_("Puoi lavorare su una copia senza firme, lasciando intatto l'originale."))
        cancel = box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        copy = box.addButton(_("Lavora su una copia senza firme"), QMessageBox.ButtonRole.ActionRole)
        proceed = box.addButton(_("Procedi comunque"), QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        return "proceed" if clicked is proceed else "copy" if clicked is copy else "cancel"

    def _on_changed(self, view) -> None:
        doc = view.document
        st = self._states.get(id(doc))
        if st is None or self._in_warning or self._own_change:
            return
        if st.signed and not st.warned and doc.is_modified and not st.warning_pending:
            # rimandato: la change() di chi ha modificato deve terminare prima di un eventuale annulla
            st.warning_pending = True
            QTimer.singleShot(0, lambda v=view: self._deferred_warning(v))
        self.stateChanged.emit(view)

    def _deferred_warning(self, view) -> None:
        doc = view.document
        st = self._states.get(id(doc))
        if st is None:
            return
        st.warning_pending = False
        if doc.closed or self._in_warning or st.warned or not doc.is_modified:
            return
        if self._content_changed(doc, st):
            self._in_warning = True
            try:
                label = doc.undo_label or _("Modifica")
                choice = self._ask_invalidation(view, label)
                if choice == "proceed":
                    st.warned = True
                else:
                    doc.undo()
                    if choice == "copy":
                        self.open_unsigned_copy(view)
            finally:
                self._in_warning = False
            self.stateChanged.emit(view)

    def _content_changed(self, doc, st: VerifyState) -> bool:
        """Le modifiche consentite (annotazioni, moduli) non richiedono l'avviso
        se il salvataggio è incrementale e la certificazione le ammette."""
        if not st.incremental or st.base is None or st.certification not in (None, 3):
            return True
        from .incremental import changes_content
        with doc.lock:
            return changes_content(st.base, doc.pdf, doc.password)

    def open_unsigned_copy(self, view):
        from . import digital
        st = self.state(view)
        data = st.source or self.document_bytes(view.document)
        try:
            copy = digital.strip_signatures(data)
            doc = Document.from_bytes(copy, display_name=_("{name} (senza firme).pdf").format(
                name=Path(view.document.display_name).stem))
        except Exception as e:
            self.ctx.error(_("Copia senza firme"), str(e))
            return None
        return self.ctx.open_document(doc)

    # ========================================================== firma digitale
    def start_digital_sign(self, certify: bool = False) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        st = self.state(view)
        if certify and st.signed:
            self.ctx.error(_("Certifica"), _("La certificazione deve essere la prima firma del documento: "
                                              "questo documento contiene già delle firme."))
            return
        if st.certification == 1:
            self.ctx.error(_("Firma digitale"), _("Il documento è certificato con livello «nessuna modifica»: "
                                                   "non è possibile aggiungere altre firme."))
            return
        tool = RectTool(
            on_rect=lambda v, p, r: self.digital_sign(v, page=p, rect=r, certify=certify),
            on_enter=lambda v: self.digital_sign(v, invisible=True, certify=certify),
            fields=self.empty_fields,
            on_field=lambda v, f: self.digital_sign(v, field=f, certify=certify),
            min_size=(40, 15))
        view.set_tool(tool)
        self.ctx.status(_("Traccia l'area della firma o fai clic su un campo firma evidenziato; "
                          "premi Invio per una firma invisibile."), 10000)

    def _ensure_saved(self, view) -> bool:
        doc = view.document
        if doc.path is not None and not doc.is_modified:
            return True
        if not self.ctx.confirm(_("Firma digitale"), _("Il documento ha modifiche non salvate. Per includerle "
                                                       "nella revisione firmata va prima salvato. Salvare ora?")):
            return False
        return self.ctx.save_current(save_as=doc.path is None)

    def digital_sign(self, view, *, page: int = 0, rect=None, field=None, invisible: bool = False,
                     certify: bool = False) -> bool:
        from . import digital
        from .ui_digital import DigitalSignDialog
        view.set_tool(None)
        if not self._ensure_saved(view):
            return False
        doc = view.document
        required_level = getattr(field, "certify_level", None) if field is not None else None
        if required_level:
            certify = True      # il campo richiede una firma di certificazione (/SV /MDP)
        dlg = DigitalSignDialog(self.ctx.window, self, certify=certify, visible=not invisible and
                                (rect is not None or (field is not None and not field.invisible)), field=field)
        if not dlg.exec():
            return False
        digital_id, options = dlg.digital_id, dlg.options
        if required_level:
            options.certify, options.docmdp_level = True, int(required_level)
        if field is not None:
            options.field_name = field.name
            options.page = field.page or 0
            options.rect = None
        elif not invisible and rect is not None:
            options.page = page
            options.rect = tuple(rect)
        stem = Path(doc.path).stem
        suggested = f"{stem}_firmato.pdf"
        target = self.ctx.ask_save_path(_("Salva il documento firmato"), suggested)
        if not target:
            return False
        if Path(target).resolve() == Path(doc.path).resolve():
            if not self.ctx.confirm(_("Firma digitale"), _("Sovrascrivere il file originale con la versione "
                                                           "firmata?")):
                return False
        try:
            data = Path(doc.path).read_bytes()
        except OSError as e:
            self.ctx.error(_("Firma digitale"), _("Impossibile leggere «{p}»: {e}. Il file è stato spostato o "
                                                  "eliminato? Salvalo di nuovo e riprova.").format(
                p=doc.path, e=e.strerror or e))
            return False
        signed = self._run_signing(data, digital_id, options)
        if signed is None:
            return False
        try:
            atomic_write(target, signed)
        except DocumentError as e:
            self.ctx.error(_("Firma digitale"), str(e))
            return False
        self._reopen(view, target)
        skew = self.clock_skew_warning(signed) if options.timestamp_url or options.timestamper else None
        if skew:
            self.ctx.error(_("Marca temporale"), skew)
        self.ctx.info(_("Documento firmato"), _("Il documento firmato è stato salvato in:\n{path}\n\nAttenzione: "
                                                "modificarlo e salvarlo di nuovo invaliderà la firma.")
                      .format(path=target))
        return True

    def _run_signing(self, data: bytes, digital_id, options) -> bytes | None:
        from . import digital
        while True:
            try:
                return self.ctx.run_task(_("Firma in corso…"), lambda progress: digital.sign_pdf(
                    data, digital_id, options), cancellable=False)
            except digital.TimestampError as e:
                if e.kind != "rete":
                    self.ctx.error(_("Marca temporale"), str(e))
                    return None
                choice = self._ask_timestamp_failure(str(e), options.timestamp_url)
                if choice == "retry":
                    continue
                if isinstance(choice, tuple) and choice[0] == "server":
                    options.timestamp_url, options.timestamp_timeout = choice[1], choice[2]
                    options.timestamper = None
                    continue
                if choice == "without":
                    options.timestamp_url = None
                    options.timestamper = None
                    continue
                return None
            except digital.SignError as e:
                self.ctx.error(_("Firma digitale"), str(e))
                return None
            except Exception as e:
                self.ctx.error(_("Firma digitale"), _("Firma non riuscita: {e}").format(e=e))
                return None

    def other_tsa_servers(self, current: str | None) -> list:
        from . import store
        return [srv for srv in store.TSAConfig().list() if srv.url != current]

    def _ask_timestamp_failure(self, message: str, current_url: str | None = None):
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Marca temporale non disponibile"))
        box.setText(message + "\n\n" + _("Nessun file è stato scritto."))
        box.setInformativeText(_("Senza marca temporale l'ora della firma sarà quella dichiarata dal computer."))
        retry = box.addButton(_("Riprova"), QMessageBox.ButtonRole.AcceptRole)
        others = self.other_tsa_servers(current_url)
        other = box.addButton(_("Usa un altro server…"), QMessageBox.ButtonRole.ActionRole) if others else None
        without = box.addButton(_("Firma senza marca temporale"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        c = box.clickedButton()
        if other is not None and c is other:
            labels = [f"{srv.name} — {srv.url}" for srv in others]
            choice, ok = QInputDialog.getItem(self.ctx.window, _("Server marca temporale"), _("Server:"),
                                              labels, 0, False)
            if not ok:
                return "cancel"
            srv = others[labels.index(choice)]
            return ("server", srv.url, srv.timeout)
        return "retry" if c is retry else "without" if c is without else "cancel"

    def clock_skew_warning(self, signed: bytes) -> str | None:
        """Avviso se l'ora del computer differisce di oltre 5 minuti da quella della marca temporale."""
        from datetime import datetime, timezone

        from . import digital
        try:
            st = digital.verify_pdf(signed)[-1]
        except Exception:
            return None
        if st.time_source != "marca temporale" or st.signing_time is None:
            return None
        delta = abs((datetime.now(timezone.utc) - st.signing_time).total_seconds())
        if delta > 300:
            return _("L'orologio del computer differisce di {m} minuti dall'ora della marca temporale: "
                     "controlla data e ora del sistema.").format(m=int(delta // 60))
        return None

    def _reopen(self, view, target: str) -> None:
        doc = view.document
        same = doc.path is not None and Path(target).resolve() == Path(doc.path).resolve()
        if same:
            self.ctx.window.close_view(view)
        new_view = self.ctx.open_path(target)
        if new_view is not None:
            self.verify(new_view, force=True)

    # ============================================================ campi firma
    def start_add_field(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        view.set_tool(RectTool(on_rect=self.add_field, min_size=(20, 10)))
        self.ctx.status(_("Traccia il riquadro del nuovo campo firma."), 8000)

    def add_field(self, view, page: int, rect) -> None:
        from . import digital
        from .ui_digital import FieldDialog
        view.set_tool(None)
        dlg = FieldDialog(self.ctx.window)
        if rect.width < 20 or rect.height < 10:
            dlg.warning.setText(_("Il campo è più piccolo di 20×10 pt: potrebbe essere difficile da firmare."))
        if not dlg.exec():
            return
        st = self.state(view)
        doc = view.document
        try:
            if st.signed:
                if not self._ensure_saved(view):
                    return
                data = Path(doc.path).read_bytes()
            else:
                data = doc.to_bytes()
            lock_action, lock_fields = dlg.lock_spec()
            try:
                pages = dlg.page_list(view.page_count, page)
            except ValueError as e:
                self.ctx.error(_("Campo firma"), str(e))
                return
            new = data
            names = []
            wanted = dlg.name.text().strip() or _("Firma")
            for p in pages:
                with ENGINE_LOCK:
                    prect = g.clamp_rect(doc.pdf[p], pymupdf.Rect(rect))
                new, name = digital.add_signature_field(
                    new, wanted, p, tuple(prect), required=dlg.required.isChecked(),
                    tooltip=dlg.tooltip.text().strip(), timestamp_url=dlg.tsa.text().strip() or None,
                    timestamp_required=dlg.tsa_required.isChecked(), lock_fields=lock_fields,
                    lock_action=lock_action, allowed_reasons=dlg.allowed_reasons(),
                    allowed_issuers=dlg.issuers or None, certify_level=dlg.mdp.currentData())
                names.append(name)
            name = names[0]
        except digital.SignError as e:
            self.ctx.error(_("Campo firma"), str(e))
            return
        except Exception as e:
            self.ctx.error(_("Campo firma"), _("Impossibile aggiungere il campo firma: {e}").format(
                e=str(e) or e.__class__.__name__))
            return
        if len(names) == 1 and name != (dlg.name.text().strip() or _("Firma")):
            self.ctx.info(_("Campo firma"), _("Esiste già un campo con quel nome: il nuovo campo si chiama «{n}».")
                          .format(n=name))
        if st.signed:
            # documento firmato: il campo si aggiunge in modo incrementale direttamente nel file
            target = self.ctx.ask_save_path(_("Salva il documento con il nuovo campo"), Path(doc.path).name)
            if not target:
                return
            try:
                atomic_write(target, new)
            except DocumentError as e:
                self.ctx.error(_("Campo firma"), str(e))
                return
            self._reopen(view, target)
            return
        self._own_change = True
        try:
            doc.replace_with(new, _("Aggiungi campo firma"))
        finally:
            self._own_change = False
        self.verify(view, force=True)

    # ======================================================= ID e certificati
    def create_self_signed(self) -> None:
        from .ui_digital import SelfSignedDialog
        SelfSignedDialog(self.ctx.window, self).exec()

    def manage_ids(self) -> None:
        from .ui_digital import IDsDialog
        IDsDialog(self.ctx.window, self).exec()
        for v in self.ctx.views():
            if self.state(v).signed:
                self.verify(v, force=True)

    def trust_signer(self, view, status) -> None:
        from . import store
        cert = status.chain[-1] if status.chain else status.signer_cert
        if cert is None:
            return
        if not self.ctx.confirm(_("Considera attendibile"), _(
                "Aggiungere questo certificato agli attendibili?\n\n{subject}\nEmittente: {issuer}\n"
                "Impronta SHA-256:\n{fp}").format(subject=cert.subject_dn, issuer=cert.issuer_dn, fp=cert.sha256)):
            return
        try:
            store.TrustStore().add(cert)
        except Exception as e:
            self.ctx.error(_("Considera attendibile"), str(e))
            return
        self.verify(view, force=True)

    def open_signed_version(self, view, status) -> None:
        from . import digital
        st = self.state(view)
        data = st.source or self.document_bytes(view.document)
        try:
            prefix = digital.signed_revision(data, status)
            doc = Document.from_bytes(prefix, display_name=_("{name} (versione firmata da {who}).pdf").format(
                name=Path(view.document.display_name).stem, who=status.signer or status.field_name),
                modified=False)
        except Exception as e:
            self.ctx.error(_("Versione firmata"), str(e))
            return
        new_view = self.ctx.open_document(doc)
        self.state(doc).warned = False
        return new_view

    def show_status(self, view, status) -> None:
        """Mostra la voce della firma nel pannello (clic su una firma nella pagina)."""
        self.ctx.show_sidebar_panel("firme")
        self.statusSelected.emit(view, status)

    def go_to_field(self, view, page: int | None, rect) -> None:
        if page is None:
            return
        if rect is not None:
            view.ensure_visible(page, pymupdf.Rect(rect))
        else:
            view.go_to_page(page)

    def copy_text(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.ctx.status(_("Dettagli copiati negli appunti."), 3000)


TRANSLATIONS_EN = {
    "Libreria firme": "Signature library",
    "Libreria protetta": "Protected library",
    "Inserisci la password della libreria delle firme:": "Enter the signature library password:",
    "Fai clic sulla pagina per posizionare la firma, o trascina per definirne la dimensione; Invio la posiziona al "
    "centro della pagina. Esc per annullare.":
        "Click on the page to place the signature, or drag to set its size; Enter places it in the middle of the "
        "page. Esc to cancel.",
    "Firma": "Signature",
    "I permessi del documento non consentono di aggiungere annotazioni o firme grafiche. Apri il documento con "
    "la password del proprietario per modificarlo.":
        "The document permissions do not allow adding annotations or graphic signatures. Open the document with "
        "the owner password to change it.",
    "Inserisci iniziali": "Insert initials", "Inserisci firma": "Insert signature",
    "Elimina firma": "Delete signature", "Duplica firma": "Duplicate signature",
    "Incorpora firma nella pagina": "Embed signature in page", "Opacità": "Opacity",
    "Opacità della firma (%):": "Signature opacity (%):", "Opacità firma": "Signature opacity",
    "Ripeti firma su più pagine": "Repeat signature on several pages",
    "Firma ripetuta su {n} pagine.": "Signature repeated on {n} pages.",
    "Ridotta per entrare nelle pagine: {p}.": "Shrunk to fit on pages: {p}.",
    "Ruota di 90°": "Rotate 90°", "Ruota firma": "Rotate signature",
    "Duplica": "Duplicate", "Elimina": "Delete", "Opacità…": "Opacity…",
    "Ripeti su più pagine…": "Repeat on several pages…", "Incorpora nella pagina": "Embed in page",
    "Documento firmato: modificare il contenuto invaliderà le firme.":
        "Signed document: changing its content will invalidate the signatures.",
    "Documento firmato": "Signed document",
    "«{what}» modifica il contenuto di un documento firmato: salvando, queste firme non saranno più valide:":
        "“{what}” changes the content of a signed document: when saved, these signatures will no longer be "
        "valid:",
    "Salvataggio incrementale non riuscito: {e}": "Incremental save failed: {e}",
    "Scegli dove salvare il documento.": "Choose where to save the document.",
    "Puoi lavorare su una copia senza firme, lasciando intatto l'originale.":
        "You can work on a copy without signatures, leaving the original intact.",
    "Annulla": "Cancel", "Lavora su una copia senza firme": "Work on a copy without signatures",
    "Procedi comunque": "Proceed anyway", "Modifica": "Change",
    "{name} (senza firme).pdf": "{name} (unsigned).pdf", "Copia senza firme": "Copy without signatures",
    "Certifica": "Certify",
    "La certificazione deve essere la prima firma del documento: questo documento contiene già delle firme.":
        "Certification must be the first signature of the document: this document already contains signatures.",
    "Firma digitale": "Digital signature",
    "Il documento è certificato con livello «nessuna modifica»: non è possibile aggiungere altre firme.":
        "The document is certified with level “no changes”: no further signatures can be added.",
    "Traccia l'area della firma o fai clic su un campo firma evidenziato; premi Invio per una firma invisibile.":
        "Draw the signature area or click a highlighted signature field; press Enter for an invisible signature.",
    "Il documento ha modifiche non salvate. Per includerle nella revisione firmata va prima salvato. Salvare ora?":
        "The document has unsaved changes. To include them in the signed revision it must be saved first. "
        "Save now?",
    "Salva il documento firmato": "Save the signed document",
    "Sovrascrivere il file originale con la versione firmata?": "Overwrite the original file with the signed version?",
    "Il documento firmato è stato salvato in:\n{path}\n\nAttenzione: modificarlo e salvarlo di nuovo invaliderà "
    "la firma.": "The signed document has been saved to:\n{path}\n\nWarning: changing it and saving it again will "
                 "invalidate the signature.",
    "Firma in corso…": "Signing…", "Marca temporale": "Timestamp",
    "Firma non riuscita: {e}": "Signing failed: {e}",
    "Marca temporale non disponibile": "Timestamp not available",
    "Nessun file è stato scritto.": "No file has been written.",
    "Senza marca temporale l'ora della firma sarà quella dichiarata dal computer.":
        "Without a timestamp the signing time will be the one declared by the computer.",
    "Riprova": "Retry", "Usa un altro server…": "Use another server…", "Server:": "Server:",
    "Server marca temporale": "Timestamp server",
    "L'orologio del computer differisce di {m} minuti dall'ora della marca temporale: controlla data e ora del "
    "sistema.": "The computer clock differs by {m} minutes from the timestamp time: check the system date and time.", "Firma senza marca temporale": "Sign without timestamp",
    "Traccia il riquadro del nuovo campo firma.": "Draw the box of the new signature field.",
    "Il campo è più piccolo di 20×10 pt: potrebbe essere difficile da firmare.":
        "The field is smaller than 20×10 pt: it may be hard to sign.",
    "Campo firma": "Signature field",
    "Esiste già un campo con quel nome: il nuovo campo si chiama «{n}».":
        "A field with that name already exists: the new field is called “{n}”.",
    "Salva il documento con il nuovo campo": "Save the document with the new field",
    "Aggiungi campo firma": "Add signature field",
    "Considera attendibile": "Trust",
    "Aggiungere questo certificato agli attendibili?\n\n{subject}\nEmittente: {issuer}\nImpronta SHA-256:\n{fp}":
        "Add this certificate to the trusted ones?\n\n{subject}\nIssuer: {issuer}\nSHA-256 fingerprint:\n{fp}",
    "{name} (versione firmata da {who}).pdf": "{name} (version signed by {who}).pdf",
    "Versione firmata": "Signed version",
    "Dettagli copiati negli appunti.": "Details copied to the clipboard.",
    "Operazione non riuscita: {e}": "Operation failed: {e}",
    "L'elemento della libreria non è utilizzabile: {e}": "The library item cannot be used: {e}",
    "Impossibile leggere «{p}»: {e}. Il file è stato spostato o eliminato? Salvalo di nuovo e riprova.":
        "Cannot read “{p}”: {e}. Was the file moved or deleted? Save it again and retry.",
    "Impossibile aggiungere il campo firma: {e}": "Cannot add the signature field: {e}",
    "Documento certificato": "Certified document",
    "Il documento è certificato con livello «nessuna modifica»: «{what}» non è consentito.\n\nVuoi lavorare "
    "su una copia senza firme?": "The document is certified with level “no changes”: “{what}” is not allowed.\n\n"
                                "Do you want to work on a copy without signatures?",
    "Elimina gruppo di firme": "Delete signature group", "Sciogli gruppo": "Ungroup",
    "Elimina tutto il gruppo": "Delete the whole group",
    "Dettagli della firma di {who}": "Details of the signature by {who}", "Firma qui": "Sign here",
}
