"""Controller del modulo Firma: collega strumenti, dialoghi, motore e pannello."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox

from foliant.api import CONTENT, ENGINE_LOCK, Document, DocumentError, _, atomic_write

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


class _Bridge(QObject):
    verified = Signal(object, int, object)   # documento, versione, risultato


class SignController(QObject):
    stateChanged = Signal(object)            # vista

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
        except LibraryError as e:
            self.ctx.error(_("Libreria firme"), str(e))
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
        self.ctx.status(_("Fai clic sulla pagina per posizionare la firma, o trascina per definirne la dimensione. "
                          "Esc per annullare."), 8000)

    def _annotations_allowed(self, view) -> bool:
        with ENGINE_LOCK:
            perms = view.document.pdf.permissions
        if not perms & pymupdf.PDF_PERM_ANNOTATE:
            self.ctx.error(_("Firma"), _("I permessi del documento non consentono di aggiungere annotazioni o "
                                          "firme grafiche. Apri il documento con la password del proprietario "
                                          "per modificarlo."))
            return False
        return True

    def _change(self, view, label: str, fn):
        """Esegue `fn(pdf)` come passo annullabile, con l'avviso SIG-21 se il documento è firmato."""
        if not self.confirm_invalidating(view, label):
            return None
        self._own_change = True
        try:
            with view.document.change(label) as pdf:
                return fn(pdf)
        except (g.GraphicSignatureError, DocumentError) as e:
            self.ctx.error(_("Firma"), str(e))
            return None
        finally:
            self._own_change = False

    def place(self, view, page: int, rect: pymupdf.Rect, item: g.SignatureItem) -> int | None:
        author = self.default_name()
        label = _("Inserisci iniziali") if item.kind == "iniziali" else _("Inserisci firma")
        return self._change(view, label, lambda pdf: g.insert_signature(pdf, page, rect, item, author=author))

    def set_rect(self, view, page: int, xref: int, rect, label: str) -> None:
        self._change(view, label, lambda pdf: g.set_signature_rect(pdf, page, xref, rect))

    def delete(self, view, page: int, xref: int) -> None:
        self._change(view, _("Elimina firma"), lambda pdf: g.delete_signature(pdf, page, xref))

    def selection_changed(self, view, tool) -> None:
        view.viewport().update()

    def duplicate(self, view, page: int, xref: int) -> None:
        new = self._change(view, _("Duplica firma"), lambda pdf: g.duplicate_signature(pdf, page, xref))
        if new:
            self.tool_for(view).selected = (page, new)

    def embed(self, view, page: int, xref: int) -> None:
        self._change(view, _("Incorpora firma nella pagina"), lambda pdf: g.embed_signature(pdf, page, xref))
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
        menu.addAction(_("Opacità…"), lambda: self.set_opacity(view, p, x))
        menu.addAction(_("Ripeti su più pagine…"), lambda: self.repeat(view, p, x))
        menu.addAction(_("Incorpora nella pagina"), lambda: self.embed(view, p, x))

    # ================================================== verifica e stato firme
    def state(self, view_or_doc) -> VerifyState:
        doc = getattr(view_or_doc, "document", view_or_doc)
        st = self._states.get(id(doc))
        if st is None:
            st = VerifyState()
            self._states[id(doc)] = st
        return st

    def _on_opened(self, view) -> None:
        self.verify(view)

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
        if b"/Sig" not in data and b"/ByteRange" not in data:
            st.version, st.statuses, st.fields, st.signed, st.certification = doc.version, [], [], False, None
            st.source = data
            st.error = ""
            self.stateChanged.emit(view)
            return
        st.running = True
        st.source = data
        version = doc.version
        self.stateChanged.emit(view)

        def work():
            from . import digital, store
            result: dict = {}
            try:
                trusted = store.TrustStore().certificates()
                result["statuses"] = digital.verify_pdf(data, trusted=trusted, revocation_mode=self.revocation_mode())
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
        st.signed = bool(st.statuses)
        for view in self.ctx.views():
            if view.document is doc:
                self.stateChanged.emit(view)
                if st.signed and not doc.is_modified:
                    self.ctx.status(_("Documento firmato: modificare il contenuto invaliderà le firme."), 10000)

    def empty_fields(self, view) -> list:
        return [f for f in self.state(view).fields if not f.signed]

    # =========================================================== SIG-21 avvisi
    def confirm_invalidating(self, view, what: str) -> bool:
        """Prima di una modifica a un documento firmato: Annulla / Copia senza firme / Procedi."""
        st = self.state(view)
        if not st.signed or st.warned:
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
        box.setText(_("«{what}» modifica un documento firmato: salvando, queste firme non saranno più valide:")
                    .format(what=what) + "\n\n" + names)
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
        if st.signed and not st.warned and doc.is_modified:
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
        dlg = DigitalSignDialog(self.ctx.window, self, certify=certify, visible=not invisible and
                                (rect is not None or (field is not None and not field.invisible)), field=field)
        if not dlg.exec():
            return False
        digital_id, options = dlg.digital_id, dlg.options
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
        data = Path(doc.path).read_bytes()
        signed = self._run_signing(data, digital_id, options)
        if signed is None:
            return False
        try:
            atomic_write(target, signed)
        except DocumentError as e:
            self.ctx.error(_("Firma digitale"), str(e))
            return False
        self._reopen(view, target)
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
                choice = self._ask_timestamp_failure(str(e))
                if choice == "retry":
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

    def _ask_timestamp_failure(self, message: str) -> str:
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Marca temporale non disponibile"))
        box.setText(message + "\n\n" + _("Nessun file è stato scritto."))
        box.setInformativeText(_("Senza marca temporale l'ora della firma sarà quella dichiarata dal computer."))
        retry = box.addButton(_("Riprova"), QMessageBox.ButtonRole.AcceptRole)
        without = box.addButton(_("Firma senza marca temporale"), QMessageBox.ButtonRole.ActionRole)
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        c = box.clickedButton()
        return "retry" if c is retry else "without" if c is without else "cancel"

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
            new, name = digital.add_signature_field(
                data, dlg.name.text().strip() or _("Firma"), page, tuple(rect), required=dlg.required.isChecked(),
                tooltip=dlg.tooltip.text().strip(), timestamp_url=dlg.tsa.text().strip() or None,
                timestamp_required=dlg.tsa_required.isChecked())
        except digital.SignError as e:
            self.ctx.error(_("Campo firma"), str(e))
            return
        if name != (dlg.name.text().strip() or _("Firma")):
            self.ctx.info(_("Campo firma"), _("Esiste già un campo con quel nome: il nuovo campo si chiama «{n}».")
                          .format(n=name))
        if st.signed:
            # documento firmato: il campo si aggiunge in modo incrementale direttamente nel file
            target = self.ctx.ask_save_path(_("Salva il documento con il nuovo campo"), Path(doc.path).name)
            if not target:
                return
            atomic_write(target, new)
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
    "Fai clic sulla pagina per posizionare la firma, o trascina per definirne la dimensione. Esc per annullare.":
        "Click on the page to place the signature, or drag to set its size. Esc to cancel.",
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
    "Duplica": "Duplicate", "Elimina": "Delete", "Opacità…": "Opacity…",
    "Ripeti su più pagine…": "Repeat on several pages…", "Incorpora nella pagina": "Embed in page",
    "Documento firmato: modificare il contenuto invaliderà le firme.":
        "Signed document: changing its content will invalidate the signatures.",
    "Documento firmato": "Signed document",
    "«{what}» modifica un documento firmato: salvando, queste firme non saranno più valide:":
        "“{what}” changes a signed document: when saved, these signatures will no longer be valid:",
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
    "Riprova": "Retry", "Firma senza marca temporale": "Sign without timestamp",
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
}
