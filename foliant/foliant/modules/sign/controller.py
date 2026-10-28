"""Controller del modulo Firma: collega strumenti, dialoghi, motore e pannello."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QMessageBox

from foliant.api import ENGINE_LOCK, SAVED, Document, DocumentError, OperationCancelled, _, atomic_write

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
    signed_hint: bool = False
       # il file contiene /ByteRange (firmato, anche se non ancora verificato)


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
        ctx.documentOpened.connect(self._on_opened)
        ctx.documentClosed.connect(self._on_closed)
        ctx.documentChanged.connect(self._on_changed)
        ctx.add_change_guard(self._change_guard)
        self.panel = None                     # SignaturesPanel, impostato in register()
        self.stateChanged.connect(self.update_banner)
        # SIG-20: avviso per le firme obbligatorie mancanti prima di stampa e «Salva con nome»
        if hasattr(ctx, "add_guard"):
            # SIG-05: preferenza «Mantieni come annotazione» / «Incorpora» al salvataggio
            ctx.add_guard("save", self._embed_on_save_guard)
            ctx.add_guard("save_as", self._embed_on_save_guard)
            ctx.add_guard("print", lambda view, info: self._required_guard(view, _("Stampa")))
            # NFR-09: avviso prima di un salvataggio che riscrive per intero un documento firmato
            ctx.add_guard("save", lambda view, info: self._full_rewrite_guard(view, False, info))
            ctx.add_guard("save_as", lambda view, info: self._full_rewrite_guard(view, True, info))
            ctx.add_guard("save_as", lambda view, info: self._required_guard(
                view, _("Salva una copia") if (info or {}).get("copy") else _("Salva con nome")))

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
        """Nome dell'autore (preferenza del nucleo «Nome autore», altrimenti l'utente di sistema)."""
        try:
            return str(self.ctx.author_name() or "")
        except Exception:
            return ""

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
        self.ctx.status(_("Clic: posiziona la firma · trascina: dimensione · Invio: al centro · Esc: annulla"),
                        8000)

    def _permission_ok(self, view, perms: tuple[int, ...], message: str) -> bool:
        """Permessi effettivi del documento (nucleo: `allows`); se mancano propone di
        sbloccare con la password del proprietario (`unlock`)."""
        doc = view.document
        if any(doc.allows(p) for p in perms):
            return True
        pw, ok = QInputDialog.getText(self.ctx.window, _("Permessi del documento"),
                                      message + "\n\n" + _("Password del proprietario per sbloccarlo:"),
                                      QLineEdit.EchoMode.Password)
        if ok and pw:
            if doc.unlock(pw):
                return True
            self.ctx.error(_("Permessi del documento"), _("La password del proprietario non è corretta."))
        return False

    def _annotations_allowed(self, view) -> bool:
        return self._permission_ok(view, (pymupdf.PDF_PERM_ANNOTATE,), _(
            "I permessi del documento non consentono di aggiungere annotazioni o firme grafiche."))

    def _change(self, view, label: str, fn, kind: str = "annotation"):
        """Esegue `fn(pdf)` come passo annullabile; l'avviso SIG-21 per i documenti firmati
        lo dà la guardia sulle modifiche (`_change_guard`), prima di modificare."""
        try:
            with view.document.change(label, kind=kind) as pdf:
                return fn(pdf)
        except OperationCancelled as e:
            if str(e) and view.document.locked:
                self.ctx.status(str(e), 6000)
            return None
        except (g.GraphicSignatureError, DocumentError) as e:
            self.ctx.error(_("Firma"), str(e))
            return None
        except Exception as e:
            self.tool_for(view).selected = None
            self.ctx.error(_("Firma"), _("Operazione non riuscita: {e}").format(e=str(e) or e.__class__.__name__))
            return None

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

    def rotate_free(self, view, page: int, xref: int) -> None:
        val, ok = QInputDialog.getInt(self.ctx.window, _("Ruota firma"),
                                      _("Gradi in senso orario (negativi: antiorario):"), 15, -180, 180)
        if ok and val:
            self._change(view, _("Ruota firma"), lambda pdf: g.rotate_signature(pdf, page, xref, val))

    def embed_all(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        with ENGINE_LOCK:
            count = sum(len(g.placed_signatures(view.document.pdf, p)) for p in range(view.document.pdf.page_count))
        if not count:
            self.ctx.info(_("Incorpora le firme"), _("Il documento non contiene firme grafiche da incorporare."))
            return
        if not self.ctx.confirm(_("Incorpora le firme"), _(
                "Incorporare {n} firme grafiche nel contenuto delle pagine? Non saranno più spostabili "
                "(l'operazione si può annullare).").format(n=count)):
            return
        n = self._change(view, _("Incorpora le firme"), g.embed_all_signatures, kind="content")
        self.tool_for(view).selected = None
        if n:
            self.ctx.status(_("Firme incorporate: {n}.").format(n=n), 5000)

    ON_SAVE_KEY = "sign/on_save"     # "keep" (predefinito) | "embed"

    def on_save_mode(self) -> str:
        return "embed" if str(self.ctx.settings().value(self.ON_SAVE_KEY, "keep")) == "embed" else "keep"

    def _embed_on_save_guard(self, view, info) -> bool:
        """SIG-05: con la preferenza «Incorpora» le firme grafiche entrano nel contenuto al salvataggio
        (un solo passo annullabile). Non vale per «Salva una copia» né per i documenti firmati
        digitalmente, dove incorporare invaliderebbe le firme: lì restano annotazioni."""
        if view is None or (info or {}).get("copy") or self.on_save_mode() != "embed":
            return True
        doc = view.document
        with ENGINE_LOCK:
            count = sum(len(g.placed_signatures(doc.pdf, p)) for p in range(doc.pdf.page_count))
        if not count:
            return True
        if getattr(doc, "has_signatures", False) or self.state(view).statuses:
            self.ctx.status(_("Documento firmato digitalmente: le firme grafiche restano annotazioni."), 6000)
            return True
        n = self._change(view, _("Incorpora le firme"), g.embed_all_signatures, kind="content")
        self.tool_for(view).selected = None
        return n is not None

    def set_opacity(self, view, page: int, xref: int) -> None:
        val, ok = QInputDialog.getInt(self.ctx.window, _("Opacità"), _("Opacità della firma (%):"), 100, 5, 100)
        if ok:
            self._change(view, _("Opacità firma"), lambda pdf: g.set_signature_opacity(pdf, page, xref, val / 100))

    def repeat(self, view, page: int, xref: int) -> None:
        from .ui_digital import RepeatDialog
        def preview(pages, anchor):
            from PySide6.QtGui import QImage

            from foliant.api import theme
            accent = theme.color("accent")
            with ENGINE_LOCK:
                items = g.repeat_preview(view.document.pdf, page, xref, pages, anchor,
                                         color=(accent.red(), accent.green(), accent.blue()))
                return [(p, QImage.fromData(pix.tobytes("png")), shrunk) for p, pix, shrunk in items]

        dlg = RepeatDialog(self.ctx.window, view.page_count, page, preview)
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
        menu.addAction(_("Ruota…"), lambda: self.rotate_free(view, p, x))
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
        """Rilevamento sincrono delle firme (il nucleo salva già in modo incrementale i
        documenti firmati, NFR-09) e nuova verifica dopo ogni salvataggio."""
        doc = view.document
        st = self.state(doc)
        if getattr(doc, "has_signatures", False):
            st.signed = True
            st.signed_hint = True

        def on_doc_event(d, kind, v=view) -> None:
            if kind == SAVED and not d.closed:
                st.signed_hint = bool(getattr(d, "has_signatures", False))
                self.verify(v, force=True)

        doc.add_listener(on_doc_event)

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

    def verify(self, view, force: bool = False, online: bool = False) -> None:
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
        password = doc.password
        if online:
            self.ctx.status(_("Verifica online della revoca in corso…"), 5000)
        use_system = str(self.ctx.settings().value("sign/system_certs", "false")).lower() == "true"
        self.stateChanged.emit(view)

        def work():
            from . import digital, store
            result: dict = {}
            try:
                trusted = store.TrustStore().certificates()
                if use_system:
                    trusted = trusted + store.system_certificates()
                mode = "sempre" if online else self.revocation_mode()
                result["statuses"] = digital.verify_pdf(data, trusted=trusted, revocation_mode=mode,
                                                        allow_fetching=mode == "sempre", password=password)
                result["fields"] = digital.signature_fields(data, password)
                result["certification"] = digital.certification_level(data, password)
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
        self._apply_certification_lock(doc, st)
        for view in self.ctx.views():
            if view.document is doc:
                self.stateChanged.emit(view)
                if st.signed and not doc.is_modified:
                    self.ctx.status(_("Documento firmato: modificare il contenuto invaliderà le firme."), 10000)

    BANNER_ID = "firme"

    def update_banner(self, view) -> None:
        """SIG-18/SIG-20/SIG-21: barra sopra il documento con lo stato delle firme e i campi da firmare."""
        if view is None or getattr(view.document, "closed", False):
            return
        st = self.state(view)
        if st.running:
            return
        empties = [f for f in self.empty_fields(view) if not f.hidden]
        if not st.statuses and not empties:
            self.ctx.hide_banner(view, self.BANNER_ID)
            return
        from . import digital
        text = digital.summary(st.statuses, empties)
        if st.statuses and st.certification != 1:
            text += " · " + _("Modificare il contenuto invaliderà le firme.")
        bad = bool(st.error) or any(s.status != "valida" for s in st.statuses)
        actions = []
        if empties:
            actions.append((_("Firma ora"), lambda v=view: self._banner_sign_now(v)))
            actions.append((_("Campo successivo"), lambda v=view: self._banner_next_field(v)))
        if st.statuses:
            actions.append((_("Dettagli"), lambda: self.ctx.show_sidebar_panel("firme")))
        if st.certification == 1:
            actions.append((_("Lavora su una copia senza firme"), lambda v=view: self.open_unsigned_copy(v)))
        doc = view.document
        if empties and not any(doc.allows(p) for p in (pymupdf.PDF_PERM_FORM, pymupdf.PDF_PERM_ANNOTATE)):
            # SIG-20: documento cifrato senza permesso di firma → spiegazione al posto dell'invito
            actions = [a for a in actions if a[0] not in (_("Firma ora"), _("Campo successivo"))]
            text += " · " + _("I permessi del documento non consentono di firmarlo: serve la password del "
                              "proprietario.")
        level = "error" if bad else ("warning" if empties else "info")
        self.ctx.show_banner(view, self.BANNER_ID, text, actions, level=level, priority=30)

    def _banner_sign_now(self, view) -> None:
        self.ctx.show_sidebar_panel("firme")
        if self.panel is not None and self.panel.view is view:
            self.panel.sign_now()
        else:
            fields = [f for f in self.empty_fields(view) if not f.hidden]
            if fields:
                self.digital_sign(view, field=fields[0])

    def _banner_next_field(self, view) -> None:
        self.ctx.show_sidebar_panel("firme")
        if self.panel is not None and self.panel.view is view:
            self.panel.next_field()

    def _apply_certification_lock(self, doc, st: VerifyState) -> None:
        """SIG-17/SIG-21: un documento certificato «nessuna modifica» è in sola lettura."""
        if not hasattr(doc, "set_locked"):
            return
        reason = _("Documento certificato «nessuna modifica»: per modificarlo lavora su una copia senza "
                   "firme.")
        current = getattr(doc, "locked_reason", None)
        if st.certification == 1:
            if current is None:          # non sostituire un altro motivo (es. versione firmata)
                doc.set_locked(reason)
        elif current == reason:
            doc.set_locked(None)

    def empty_fields(self, view) -> list:
        return [f for f in self.state(view).fields if not f.signed]

    def missing_required_signatures(self, view) -> list:
        """Campi firma obbligatori, visibili e non firmati (SIG-20)."""
        return [f for f in self.empty_fields(view) if f.required and not f.hidden]

    def _full_rewrite_guard(self, view, save_as: bool, info) -> bool:
        """NFR-09/SIG-21: se il salvataggio riscriverà per intero un documento firmato (es. dopo
        una redazione) le firme non saranno più valide. «Salva» sul file firmato: si propone
        solo di salvare una copia (l'originale resta intatto) o di annullare. «Salva con nome»
        / «Salva una copia» scrivono già un altro file: si chiede conferma."""
        doc = getattr(view, "document", None)
        if doc is None or not getattr(doc, "has_signatures", False):
            return True
        if getattr(doc, "saves_incrementally", True):
            return True
        choice = self._ask_full_rewrite(view, save_as)
        if choice == "copy":
            if save_as:
                return True                       # sta già salvando un altro file
            self._save_unsigned_copy(view)
        return False

    def _ask_full_rewrite(self, view, save_as: bool) -> str:
        box = QMessageBox(self.ctx.window)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(_("Documento firmato"))
        box.setText(_("Questo salvataggio riscrive il file per intero (per esempio dopo una redazione): le "
                      "firme digitali non saranno più valide."))
        box.setInformativeText(_("Salva le modifiche in una copia: il file firmato resta intatto.") if not save_as
                               else _("Il nuovo file non conterrà firme valide; il file firmato resta intatto."))
        copy = box.addButton(_("Salva una copia"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("Annulla"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(copy)
        box.exec()
        return "copy" if box.clickedButton() is copy else "cancel"

    def _save_unsigned_copy(self, view) -> bool:
        doc = view.document
        stem = Path(doc.display_name).stem
        path = self.ctx.ask_save_path(_("Salva una copia"), _("{name} (modificato).pdf").format(name=stem))
        if not path:
            return False
        if doc.path is not None and Path(path).resolve() == Path(doc.path).resolve():
            self.ctx.error(_("Salva una copia"), _("Scegli un file diverso dall'originale firmato."))
            return False
        try:
            doc.save_copy(path)
        except Exception as e:
            self.ctx.error(_("Salva una copia"), str(e))
            return False
        self.ctx.status(_("Copia salvata: {p}").format(p=path), 6000)
        return True

    def _required_guard(self, view, action: str) -> bool:
        if view is None or id(view.document) not in self._states:
            return True
        return self.confirm_unsigned_required(view, action)

    def confirm_unsigned_required(self, view, action: str) -> bool:
        """Da chiamare prima di stampare, inviare o salvare con nome: avvisa se mancano
        firme obbligatorie. Restituisce True se si può procedere."""
        missing = self.missing_required_signatures(view)
        if not missing:
            return True
        names = "\n".join(f"• {f.tooltip or f.name}" for f in missing)
        return self.ctx.confirm(_("Firme mancanti"), _(
            "Il documento ha campi firma obbligatori non ancora firmati:\n\n{names}\n\nProcedere comunque con "
            "«{action}»?").format(names=names, action=action))

    # =========================================================== SIG-21 avvisi
    def allowed_change(self, view, kind: str) -> bool:
        """True se una modifica del tipo indicato non invalida le firme presenti
        (annotazioni senza certificazione o con livello 3; moduli con livello 2 o 3)."""
        st = self.state(view)
        if not st.signed:
            return True
        incremental = bool(getattr(view.document, "has_signatures", False))
        if kind == "annotation":
            return incremental and st.certification in (None, 3)
        if kind == "form":
            return incremental and st.certification in (None, 2, 3)
        return False

    def _view_of(self, document):
        return next((v for v in self.ctx.views() if v.document is document), None)

    def _change_guard(self, document, label: str, kind: str) -> bool:
        """SIG-21, prima di ogni modifica (di qualsiasi modulo): le modifiche consentite
        dalle firme presenti passano; per quelle invalidanti si chiede conferma con le
        scelte Annulla / Lavora su una copia senza firme / Procedi comunque."""
        st = self._states.get(id(document))
        view = self._view_of(document)
        if st is None or view is None or not st.signed:
            return True
        if st.certification == 1:
            if self.ctx.confirm(_("Documento certificato"), _(
                    "Il documento è certificato con livello «nessuna modifica»: «{what}» non è consentito.\n\n"
                    "Vuoi lavorare su una copia senza firme?").format(what=label)):
                QTimer.singleShot(0, lambda v=view: self.open_unsigned_copy(v))
            return False
        if st.warned or self.allowed_change(view, kind):
            return True
        choice = self._ask_invalidation(view, label)
        if choice == "proceed":
            st.warned = True
            return True
        if choice == "copy":
            QTimer.singleShot(0, lambda v=view: self.open_unsigned_copy(v))
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
        if id(view.document) in self._states:
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
        if not self._permission_ok(view, (pymupdf.PDF_PERM_FORM, pymupdf.PDF_PERM_ANNOTATE), _(
                "I permessi del documento non consentono di firmarlo né di compilarne i moduli.")):
            return False
        if not self._ensure_saved(view):
            return False
        doc = view.document
        required_level = getattr(field, "certify_level", None) if field is not None else None
        if required_level:
            certify = True      # il campo richiede una firma di certificazione (/SV /MDP)
        dlg = DigitalSignDialog(self.ctx.window, self, certify=certify, visible=not invisible and
                                (rect is not None or (field is not None and not field.invisible)), field=field)
        if not dlg.exec():
            if dlg.graphic_only and field is not None:
                self.graphic_on_field(view, field)
            return False
        digital_id, options = dlg.digital_id, dlg.options
        options.password = doc.password
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
        new_view = self._reopen(view, target)
        if field is not None and new_view is not None:
            self._go_to_next_unsigned(new_view, signed)
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
                if e.kind == "credenziali" and options.timestamp_auth:
                    pw, ok = QInputDialog.getText(
                        self.ctx.window, _("Marca temporale"),
                        _("{e}. Inserisci di nuovo la password per «{u}»:").format(e=e, u=options.timestamp_auth[0]),
                        QLineEdit.EchoMode.Password)
                    if ok:
                        options.timestamp_auth = (options.timestamp_auth[0], pw)
                        continue
                    return None
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
        return new_view

    def _go_to_next_unsigned(self, view, data: bytes) -> None:
        """SIG-20: dopo la firma di un campo si passa al successivo ancora da firmare."""
        from . import digital
        try:
            rest = [f for f in digital.signature_fields(data, view.document.password)
                    if not f.signed and not f.hidden and f.page is not None]
        except Exception:  # noqa: BLE001
            return
        if rest:
            self.go_to_field(view, rest[0].page, rest[0].rect)
            self.ctx.status(_("Prossimo campo da firmare: «{name}»").format(name=rest[0].tooltip or rest[0].name),
                            8000)

    def graphic_on_field(self, view, field) -> int | None:
        """SIG-20: firma grafica sopra un campo firma (nessun ID compatibile). Usa la firma predefinita
        della libreria o ne fa creare una; il campo resta da firmare digitalmente."""
        if field.page is None or not field.rect or not self._annotations_allowed(view):
            return None
        item = None
        lib = self.library()
        entry = lib.default("firma") if lib is not None else None
        if entry is not None:
            try:
                item = lib.load_item(entry.id)
            except Exception:  # noqa: BLE001
                item = None
        if item is None:
            from .ui_create import CreateSignatureDialog
            dlg = CreateSignatureDialog(self.ctx.window, kind="firma", default_name=self.default_name())
            if not dlg.exec() or dlg.item is None:
                return None
            item = dlg.item
        box = pymupdf.Rect(field.rect)
        w, h = box.width, box.width / max(item.aspect, 0.01)
        if h > box.height:
            w, h = box.height * item.aspect, box.height
        cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        xref = self.place(view, field.page, pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), item)
        if xref is not None:
            self.ctx.info(_("Firma grafica"), _(
                "La firma grafica è stata inserita sopra il campo «{name}». Non è una firma digitale: il campo "
                "resta da firmare.").format(name=field.tooltip or field.name))
        return xref

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
                    allowed_issuers=dlg.issuers or None, certify_level=dlg.mdp.currentData(),
                    digest_methods=dlg.digest.currentData(), allowed_policies=dlg.allowed_policies(),
                    password=doc.password)
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
        try:
            doc.replace_with(new, _("Aggiungi campo firma"), kind="form")
        except OperationCancelled:
            return
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
        if hasattr(doc, "set_locked"):
            doc.set_locked(_("Versione firmata da {who}: sola lettura.").format(who=status.signer or status.field_name))
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
    "Documento firmato digitalmente: le firme grafiche restano annotazioni.":
        "Digitally signed document: graphic signatures stay as annotations.",
    "Firma grafica": "Graphic signature",
    "Prossimo campo da firmare: «{name}»": "Next field to sign: “{name}”",
    "La firma grafica è stata inserita sopra il campo «{name}». Non è una firma digitale: il campo resta da "
    "firmare.": "The graphic signature was placed over the field “{name}”. It is not a digital signature: the "
                "field still has to be signed.",
    "I permessi del documento non consentono di firmarlo: serve la password del proprietario.":
        "The document permissions do not allow signing it: the owner password is required.",
    "Libreria firme": "Signature library",
    "Libreria protetta": "Protected library",
    "Inserisci la password della libreria delle firme:": "Enter the signature library password:",
    "Clic: posiziona la firma · trascina: dimensione · Invio: al centro · Esc: annulla":
        "Click: place the signature · drag: size · Enter: center · Esc: cancel",
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
    "Incorpora le firme": "Embed signatures",
    "Il documento non contiene firme grafiche da incorporare.": "The document has no graphic signatures to embed.",
    "Incorporare {n} firme grafiche nel contenuto delle pagine? Non saranno più spostabili (l'operazione si può "
    "annullare).": "Embed {n} graphic signatures into the page content? They can no longer be moved (the operation "
                    "can be undone).",
    "Firme incorporate: {n}.": "Signatures embedded: {n}.",
    "Ruota di 90°": "Rotate 90°", "Ruota…": "Rotate…",
    "Gradi in senso orario (negativi: antiorario):": "Degrees clockwise (negative: counter-clockwise):", "Ruota firma": "Rotate signature",
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
    "{e}. Inserisci di nuovo la password per «{u}»:": "{e}. Enter the password for “{u}” again:",
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
    "Permessi del documento": "Document permissions",
    "Password del proprietario per sbloccarlo:": "Owner password to unlock it:",
    "La password del proprietario non è corretta.": "The owner password is not correct.",
    "I permessi del documento non consentono di aggiungere annotazioni o firme grafiche.":
        "The document permissions do not allow adding annotations or graphic signatures.",
    "I permessi del documento non consentono di firmarlo né di compilarne i moduli.":
        "The document permissions do not allow signing it or filling its forms.",
    "Modificare il contenuto invaliderà le firme.": "Changing the content will invalidate the signatures.",
    "Firma ora": "Sign now", "Campo successivo": "Next field", "Dettagli": "Details",
    "Documento certificato «nessuna modifica»: per modificarlo lavora su una copia senza firme.":
        "Document certified “no changes”: to change it, work on a copy without signatures.",
    "Versione firmata da {who}: sola lettura.": "Version signed by {who}: read-only.",
    "Firme mancanti": "Missing signatures",
    "Questo salvataggio riscrive il file per intero (per esempio dopo una redazione): le firme digitali non "
    "saranno più valide.": "This save rewrites the whole file (for example after a redaction): the digital "
                           "signatures will no longer be valid.",
    "Salva le modifiche in una copia: il file firmato resta intatto.":
        "Save the changes to a copy: the signed file stays intact.",
    "Il nuovo file non conterrà firme valide; il file firmato resta intatto.":
        "The new file will not contain valid signatures; the signed file stays intact.",
    "{name} (modificato).pdf": "{name} (modified).pdf",
    "Scegli un file diverso dall'originale firmato.": "Choose a file other than the signed original.",
    "Copia salvata: {p}": "Copy saved: {p}", "Stampa": "Print", "Salva una copia": "Save a copy",
    "Salva con nome": "Save as",
    "Il documento ha campi firma obbligatori non ancora firmati:\n\n{names}\n\nProcedere comunque con «{action}»?":
        "The document has required signature fields not yet signed:\n\n{names}\n\nProceed with “{action}” anyway?",
    "Verifica online della revoca in corso…": "Online revocation check in progress…",
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
