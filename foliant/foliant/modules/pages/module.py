"""Registrazione della modalità Pagine e comandi dell'organizzatore."""

from __future__ import annotations

import atexit
import importlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import pymupdf
from PySide6.QtCore import QByteArray, QMimeData, Qt, QUrl
from PySide6.QtGui import QAction, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QToolButton

from foliant.i18n import pgettext
from foliant.api import ENGINE_LOCK, Document, FoliantError, OperationCancelled, PasswordRequired, WrongPassword, _, add_translations, bind_icon

from . import engine as E
from .organizer import PAGES_MIME, Organizer
from .translations import EN

_TEMP_DIR: str | None = None


def temp_dir() -> str:
    """Cartella privata per i file temporanei (trascinamento, condivisione).

    Riservatezza: si cancella all'uscita; quelle rimaste da un arresto anomalo si
    cancellano all'avvio successivo (`privacy.cleanup_temp_dirs`)."""
    global _TEMP_DIR
    if _TEMP_DIR is None or not os.path.isdir(_TEMP_DIR):
        from .privacy import temp_prefix
        _TEMP_DIR = tempfile.mkdtemp(prefix=temp_prefix())
        atexit.register(shutil.rmtree, _TEMP_DIR, True)
    return _TEMP_DIR


def _privacy_cleanup(ctx) -> None:
    """All'avvio: file temporanei rimasti e, una volta sola, percorsi nei modelli salvati."""
    from . import privacy
    try:
        privacy.cleanup_temp_dirs(keep=_TEMP_DIR)
    except Exception:
        pass
    try:
        privacy.cleanup_legacy(ctx.settings())
    except Exception:
        pass


def _optional(name: str):
    """Importa un sotto-modulo facoltativo (None se manca o è difettoso)."""
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except Exception:
        return None


class PagesController:
    """Stato del modulo: organizzatore, azioni e comandi."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.organizer = Organizer(ctx.window)
        self.organizer.hide()
        self.mode = None
        self.structural_actions: list[QAction] = []
        self.all_actions: list[QAction] = []
        self._signed_ack: set[int] = set()
        self._previous_provider = None
        self._crop_warned: set[tuple[int, int]] = set()
        grid = self.organizer.grid
        grid.moveRequested.connect(self.move_to_index)
        grid.dropRequested.connect(self.drop_mime)
        grid.contextRequested.connect(self.show_context_menu)
        grid.drag_payload = self.drag_mime
        grid.doubleClicked.connect(self.open_page_in_view)
        grid.rotateRequested.connect(self.rotate_page)
        grid.insertRequested.connect(self.insert_blank_at)
        grid.selectionCountChanged.connect(lambda _n: self.update_actions())

    # ================================================================ stato
    def view(self):
        return self.ctx.current_view()

    def active(self) -> bool:
        return self.ctx.current_mode() == "pagine" and self.ctx.central_widget() is self.organizer

    def selected(self) -> list[int]:
        view = self.view()
        if view is None:
            return []
        if self.active():
            pages = self.organizer.selected_pages()
            if pages:
                return pages
        return [min(view.current_page, view.page_count - 1)]

    def can_assemble(self) -> bool:
        view = self.view()
        if view is None:
            return False
        return self.assembly_allowed(view.document)

    @staticmethod
    def assembly_allowed(document: Document) -> bool:
        """Permesso di assemblaggio della sessione (API del nucleo; ripiego sul motore)."""
        allows = getattr(document, "allows", None)
        if allows is not None:
            return bool(allows(pymupdf.PDF_PERM_ASSEMBLE))
        with ENGINE_LOCK:
            return E.assembly_allowed(document.pdf)

    def update_actions(self) -> None:
        view = self.view()
        allowed = self.can_assemble() if view is not None else False
        reason = _("Il documento non consente di riorganizzare le pagine (permesso di assemblaggio negato). "
                   "Sbloccalo con la password del proprietario.")
        for act in self.structural_actions:
            act.setEnabled(view is not None and allowed)
            tip = act.property("baseTip") or act.text()
            act.setToolTip(tip if allowed or view is None else f"{tip}\n{reason}")
        if hasattr(self, "act_unlock"):
            self.act_unlock.setVisible(view is not None and not allowed)
        if hasattr(self, "act_split"):
            self.act_split.setEnabled(view is not None and view.page_count >= 2)
        if hasattr(self, "act_swap"):
            self.act_swap.setEnabled(view is not None and allowed and len(self.selected()) == 2)
        if hasattr(self, "act_paste"):
            self.act_paste.setEnabled(view is not None and allowed and clipboard_has_pages())

    # ================================================================ modalità
    def on_mode_entered(self) -> None:
        view = self.view()
        if view is None:
            self.ctx.set_central_widget(None)
            return
        self.organizer.set_view(view)
        self.ctx.set_central_widget(self.organizer)
        self._install_selection_provider()
        self.organizer.grid.setFocus()
        self.organizer.grid.flush_rendering()
        self.update_actions()
        self.organizer._update_info()

    def _install_selection_provider(self) -> None:
        """Mentre l'organizzatore è attivo, le «pagine selezionate» dell'app sono le sue; fuori
        dalla modalità torna il provider precedente (miniature del viewer)."""
        win = self.ctx.window
        current = getattr(win, "selected_pages_provider", None)
        if current == self._selection_provider:
            return
        self._previous_provider = current
        self.ctx.set_selected_pages_provider(self._selection_provider)

    def _restore_selection_provider(self) -> None:
        win = self.ctx.window
        if getattr(win, "selected_pages_provider", None) == self._selection_provider:
            self.ctx.set_selected_pages_provider(self._previous_provider)

    def _selection_provider(self, view):
        if self.active() and view is self.organizer.view:
            pages = self.organizer.selected_pages()
            if pages:
                return pages
        prev = self._previous_provider
        return prev(view) if prev is not None else None

    def on_mode_left(self) -> None:
        self._restore_selection_provider()
        self.ctx.window.statusBar().clearMessage()
        if self.ctx.central_widget() is self.organizer:
            self.ctx.set_central_widget(None)

    def on_view_changed(self, view) -> None:
        if self.ctx.current_mode() != "pagine":
            return
        if view is None:
            self.ctx.set_central_widget(None)
            self.organizer.set_view(None)
            return
        if self.organizer.view is not view or self.ctx.central_widget() is not self.organizer:
            self.on_mode_entered()
        self.update_actions()

    def on_document_changed(self, view) -> None:
        if view is self.organizer.view and view is not None:
            keep = self.organizer.selected_pages()
            n = view.page_count
            self.organizer.grid.reload([p for p in keep if p < n] or [min(view.current_page, n - 1)])
            self.update_actions()

    def on_document_closed(self, view) -> None:
        if view is self.organizer.view:
            self.organizer.set_view(None)
            self.ctx.set_central_widget(None)

    def open_page_in_view(self, index) -> None:
        view = self.view()
        if view is None or not index.isValid():
            return
        self.ctx.set_mode("leggi")
        view.go_to_page(index.row())

    # ================================================================ esecuzione
    def is_signed(self, document: Document) -> bool:
        with ENGINE_LOCK:
            pdf = document.pdf
            try:
                if pdf.get_sigflags() > 0:
                    return True
            except Exception:
                pass
            try:
                return "Perms" in pdf.xref_get_keys(pdf.pdf_catalog())
            except Exception:
                return False

    def sign_handles_warnings(self) -> bool:
        """True se il modulo Firma è caricato: gestisce lui firme e certificazioni."""
        return self.ctx.mode("firma") is not None

    def is_certified_locked(self, document: Document) -> bool:
        """Certificazione (DocMDP) che non consente modifiche (/P 1)."""
        with ENGINE_LOCK:
            pdf = document.pdf
            try:
                kind, perms = pdf.xref_get_key(pdf.pdf_catalog(), "Perms")
                if kind == "null":
                    return False
                import re as _re
                m = _re.search(r"/DocMDP\s+(\d+)\s+0\s+R", perms)
                if not m:
                    return False
                obj = pdf.xref_object(int(m.group(1)), compressed=True)
                return bool(_re.search(r"/P\s+1\b", obj))
            except Exception:
                return False

    def confirm_structural(self, document: Document) -> bool:
        """Permesso di assemblaggio e avviso firme (PAG-03)."""
        allowed = self.assembly_allowed(document)
        if not allowed:
            self.ctx.error(_("Operazione non consentita"),
                           _("Il documento non consente di riorganizzare le pagine. "
                             "Sbloccalo con la password del proprietario."))
            return False
        key = id(document)
        if self.sign_handles_warnings():
            return True   # il modulo Firma avvisa prima della modifica (SIG-21): un solo avviso (UX-13)
        if key not in self._signed_ack and self.is_certified_locked(document):
            if not self.ctx.confirm(_("Documento certificato"),
                                    _("Il documento è certificato e il suo autore non consente modifiche. "
                                      "Procedendo la certificazione non sarà più valida. Vuoi davvero "
                                      "modificarlo?")):
                return False
            self._signed_ack.add(key)
        if key not in self._signed_ack and self.is_signed(document):
            if not self.ctx.confirm(_("Documento firmato"),
                                    _("Il documento contiene firme digitali: questa modifica le renderà non "
                                      "valide. Vuoi continuare?")):
                return False
            self._signed_ack.add(key)
        return True

    def apply(self, label: str, fn: Callable, *, structural: bool = True, select: Callable | None = None,
              view=None) -> object:
        """Esegue `fn(pdf)` come unico passo annullabile; gestisce errori e selezione."""
        view = view or self.view()
        if view is None:
            return None
        document = view.document
        if structural and not self.confirm_structural(document):
            return None
        try:
            with document.change(label) as pdf:
                result = fn(pdf)
        except OperationCancelled:
            return None   # annullata dall'utente (es. avviso di Firma): nessun messaggio in più
        except FoliantError as e:
            self.ctx.error(label, str(e))
            return None
        except Exception as e:  # nessun errore deve chiudere l'applicazione
            self.ctx.error(label, friendly_error(e))
            return None
        if select is not None and view is self.organizer.view:
            try:
                pages = select(result)
            except Exception:
                pages = None
            if pages:
                self.organizer.grid.select_pages(list(pages))
        self.ctx.status(label)
        return result

    # ================================================================ comandi base
    def rotate(self, degrees: int) -> None:
        pages = self.selected()
        if pages:
            self.apply(_("Ruota pagine"), lambda pdf: E.rotate_pages(pdf, pages, degrees), select=lambda r: pages)

    def rotate_page(self, page: int, degrees: int) -> None:
        """Pulsante rapido sulla miniatura: ruota quella pagina (o la selezione che la contiene)."""
        sel = self.organizer.selected_pages()
        pages = sel if page in sel else [page]
        self.apply(_("Ruota pagine"), lambda pdf: E.rotate_pages(pdf, pages, degrees), select=lambda r: pages)

    def insert_blank_at(self, at: int) -> None:
        """Pulsante «+» tra due miniature: una pagina vuota come quella adiacente."""
        like = max(0, at - 1)
        self.apply(_("Inserisci pagina vuota"), lambda pdf: E.insert_blank_pages(pdf, at, 1, like=like),
                   select=lambda r: r)

    def delete(self) -> None:
        view = self.view()
        pages = self.selected()
        if view is None or not pages:
            return
        if len(pages) >= view.page_count:
            self.ctx.error(_("Elimina pagine"),
                           _("Impossibile eliminare tutte le pagine: il documento deve conservarne almeno una."))
            return
        try:
            with ENGINE_LOCK:
                info = E.linked_items(view.document.pdf, pages)
        except Exception:   # documento danneggiato: il riepilogo non è indispensabile
            info = {"bookmarks": 0, "links": 0, "fields": 0}
        details = []
        if info["bookmarks"]:
            details.append(_("{n} segnalibri perderanno la destinazione").format(n=info["bookmarks"]))
        if info["links"]:
            details.append(_("{n} collegamenti verranno rimossi").format(n=info["links"]))
        if info["fields"]:
            details.append(_("{n} campi modulo verranno rimossi").format(n=info["fields"]))
        msg = _("Eliminare {n} pagine ({r})?").format(n=len(pages), r=E.format_ranges(pages)) if len(pages) > 1 \
            else _("Eliminare la pagina {r}?").format(r=pages[0] + 1)
        if details:
            msg += "\n\n" + "\n".join("• " + d for d in details)
        if self.ctx.settings().value("pages/confirm_delete", False, type=bool):   # UX-13: annullabile
            if not self.ctx.confirm(_("Elimina pagine"), msg):
                return
        first = pages[0]
        done = self.apply(_("Elimina pagine"), lambda pdf: E.delete_pages(pdf, pages),
                          select=lambda r: [min(first, view.page_count - 1)])
        if done is not None:
            self.notify_undo(view, deleted_summary(len(pages), info))

    def notify_undo(self, view, text: str) -> None:
        """Avviso non bloccante con «Annulla» dopo un'operazione distruttiva annullabile (UX-13)."""
        notify = getattr(self.ctx, "notify", None)
        if notify is None:
            self.ctx.status(text)
            return
        document = view.document

        def undo():
            if not document.closed:
                document.undo()
        notify(text, actions=[(pgettext("undo", "Annulla"), undo)])

    def duplicate(self, copies: int = 1, where: str = "after") -> None:
        pages = self.selected()
        if pages:
            self.apply(_("Duplica pagine"), lambda pdf: E.duplicate_pages(pdf, pages, copies, where),
                       select=lambda r: r)

    def move_to_index(self, pages: list[int], dest: int) -> None:
        view = self.view()
        if view is None or not pages:
            return
        order = E.move_order(view.page_count, pages, dest)
        if order == list(range(view.page_count)):
            return   # nessuna modifica, nessuna voce di annullamento
        self.apply(_("Sposta pagine"), lambda pdf: E.move_pages(pdf, pages, dest), select=lambda r: r)

    def move_start(self) -> None:
        self.move_to_index(self.selected(), 0)

    def move_end(self) -> None:
        view = self.view()
        if view is not None:
            self.move_to_index(self.selected(), view.page_count)

    def reorder(self, label: str, order: list[int], select: list[int] | None = None) -> None:
        view = self.view()
        if view is None or order == list(range(view.page_count)):
            return
        self.apply(label, lambda pdf: E.reorder(pdf, order), select=(lambda r: select) if select else None)

    def reverse(self) -> None:
        view = self.view()
        if view is None:
            return
        pages = self.selected()
        target = pages if len(pages) > 1 else None
        self.reorder(_("Inverti ordine"), E.reverse_order(view.page_count, target), pages if target else None)

    def swap(self) -> None:
        view = self.view()
        pages = self.selected()
        if view is None or len(pages) != 2:
            return
        self.reorder(_("Scambia pagine"), E.swap_order(view.page_count, pages[0], pages[1]), pages)

    def swap_pairs(self) -> None:
        view = self.view()
        if view is not None:
            self.reorder(_("Scambia coppie"), E.swap_pairs_order(view.page_count))

    # ================================================================ appunti (PAG-16)
    def copy_pages(self, cut: bool = False) -> bool:
        view = self.view()
        pages = self.selected()
        if view is None or not pages:
            return False
        if cut and len(pages) >= view.page_count:
            self.ctx.error(_("Taglia pagine"), _("Impossibile tagliare tutte le pagine del documento."))
            return False
        try:
            with ENGINE_LOCK:
                data = E.extract_pages(view.document.pdf, pages)
        except Exception as e:
            self.ctx.error(_("Copia pagine"), friendly_error(e))
            return False
        mime = pages_mime(data, view, pages)
        QGuiApplication.clipboard().setMimeData(mime)
        if cut:
            done = self.apply(_("Taglia pagine"), lambda pdf: E.delete_pages(pdf, pages),
                              select=lambda r: [min(pages[0], view.page_count - 1)])
            if done is not None:
                self.notify_undo(view, _("{n} pagine tagliate negli appunti").format(n=len(pages)))
        else:
            self.ctx.status(_("{n} pagine copiate negli appunti").format(n=len(pages)))
        self.update_actions()
        return True

    def paste_pages(self, before: bool = False) -> None:
        view = self.view()
        if view is None:
            return
        data = clipboard_pdf_bytes()
        if data is None:
            self.ctx.error(_("Incolla pagine"), _("Gli appunti non contengono pagine PDF."))
            return
        pages = self.selected()
        at = (pages[0] if before else pages[-1] + 1) if pages else view.page_count
        self.insert_pdf_bytes(data, at, _("Incolla pagine"))

    def warn_pdfa(self, view, sources) -> None:
        """PAG-03: inserire pagine da file non PDF/A in un documento che dichiara PDF/A."""
        if view is None:
            return
        with ENGINE_LOCK:
            target = E.declares_pdfa(view.document.pdf)
            foreign = any(not E.declares_pdfa(s) for s in sources)
        if target and foreign:
            text = _("Il documento dichiara la conformità PDF/A: le pagine inserite provengono da file non "
                     "PDF/A e il risultato potrebbe non essere più conforme.")
            notify = getattr(self.ctx, "notify", None)
            notify(text) if notify else self.ctx.info(_("PDF/A"), text)

    def insert_pdf_bytes(self, data: bytes, at: int, label: str, view=None) -> None:
        try:
            src = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            self.ctx.error(label, _("Il contenuto non è un PDF valido."))
            return
        try:
            if self.apply(label, lambda pdf: E.insert_pdf_pages(pdf, src, at), select=lambda r: r,
                          view=view) is not None:
                self.warn_pdfa(view or self.view(), [src])
        finally:
            src.close()

    # ================================================================ trascinamento (PAG-14)
    def drag_mime(self, pages: list[int]) -> QMimeData | None:
        view = self.view()
        if view is None:
            return None
        with ENGINE_LOCK:
            data = E.extract_pages(view.document.pdf, pages)
        mime = pages_mime(data, view, pages, with_image=False)
        stem = Path(view.document.display_name).stem
        name = E.safe_filename(_("{nome} (pagine {r})").format(nome=stem, r=E.format_ranges(pages).replace(" ", "")))
        path = E.unique_path(temp_dir(), name + ".pdf")
        path.write_bytes(data)
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        return mime

    def drop_mime(self, mime: QMimeData, at: int, copy: bool) -> None:
        view = self.view()
        if view is None:
            return
        if mime.hasFormat(PAGES_MIME):
            try:
                info = json.loads(bytes(mime.data(PAGES_MIME)).decode())
            except Exception:
                info = {}
            source = next((v for v in self.ctx.views() if id(v.document) == info.get("doc")), None)
            data = bytes(mime.data("application/pdf"))
            if source is view:
                # copia nello stesso documento con Ctrl
                self.insert_pdf_bytes(data, at, _("Copia pagine"))
                return
            self.insert_pdf_bytes(data, at, _("Copia pagine"))
            if not copy and source is not None:
                pages = info.get("pages", [])
                if len(pages) >= source.page_count:
                    self.ctx.info(_("Sposta pagine"), _("Le pagine sono state copiate: il documento di origine "
                                                         "deve conservare almeno una pagina."))
                else:
                    self.apply(_("Sposta pagine"), lambda pdf: E.delete_pages(pdf, pages), view=source)
            return
        paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if paths:
            self.insert_files(paths, at)

    def insert_files(self, paths: list[str], at: int) -> None:
        """Inserisce PDF e immagini rilasciati sulla griglia nel punto indicato."""
        view = self.view()
        if view is None:
            return
        images = [p for p in paths if Path(p).suffix.lower() in E.IMAGE_SUFFIXES]
        others = [p for p in paths if p not in images]
        errors: list[str] = []
        sources = []
        for p in others:
            src = self.open_source_interactive(p)
            if src is not None:
                sources.append(src)
            else:
                errors.append(Path(p).name)
        if not sources and not images:
            if errors:
                self.ctx.error(_("Inserisci"), _("Impossibile inserire: {f}").format(f=", ".join(errors)))
            return
        img_mod = _optional("images")

        def work(pdf):
            pos = at
            new = []
            for src in sources:
                added = E.insert_pdf_pages(pdf, src, pos)
                new += added
                pos += len(added)
            if images:
                if img_mod is None:
                    raise E.PageOpError(_("Inserimento di immagini non disponibile."))
                added, errs = img_mod.insert_images(pdf, images, pos)
                errors.extend(errs)
                new += added
            return new
        try:
            if self.apply(_("Inserisci pagine"), work, select=lambda r: r) is not None and sources:
                self.warn_pdfa(view, sources)
        finally:
            for s in sources:
                s.close()
        if errors:
            self.ctx.error(_("Inserisci"), _("Alcuni file non sono stati inseriti:") + "\n" + "\n".join(errors))

    def open_source_interactive(self, path: str):
        """Apre un PDF sorgente chiedendo la password se serve (None se annullato o illeggibile)."""
        password = None
        for _attempt in range(4):
            try:
                return E.open_source(path, password)
            except (PasswordRequired, WrongPassword) as e:
                prompt = str(e) + "\n" + _("Inserisci la password:")
                password, ok = QInputDialog.getText(self.ctx.window, _("Documento protetto"), prompt,
                                                    QLineEdit.EchoMode.Password)
                if not ok:
                    return None
            except FoliantError as e:
                self.ctx.error(_("Apertura non riuscita"), str(e))
                return None
        return None

    # ================================================================ condivisione (PAG-17)
    def share(self, target: str = "mail") -> Path | None:
        view = self.view()
        pages = self.selected()
        if view is None or not pages:
            return None
        document = view.document
        with ENGINE_LOCK:
            data = E.extract_pages(document.pdf, pages)
        if document.is_protected:
            # Mantiene la protezione del documento (stessa password di apertura).
            tmp = pymupdf.open(stream=data, filetype="pdf")
            opts = {k: v for k, v in document.save_options.items() if k in ("encryption", "user_pw", "owner_pw",
                                                                           "permissions")}
            data = tmp.tobytes(garbage=3, deflate=True, **opts)
            tmp.close()
        stem = Path(document.display_name).stem
        name = E.safe_filename(_("{nome} - pagine {r}").format(nome=stem, r=E.format_ranges(pages).replace(" ", "")))
        if target == "save":
            path = self.ctx.ask_save_path(_("Salva copia delle pagine"), name + ".pdf")
            if not path:
                return None
            from foliant.api import atomic_write
            atomic_write(path, data)
            return Path(path)
        path = E.unique_path(temp_dir(), name + ".pdf")
        path.write_bytes(data)
        if len(data) > 25 * 1024 * 1024:
            self.ctx.info(_("Condividi pagine"), _("L'allegato supera 25 MB: valuta di comprimerlo prima dell'invio."))
        if not send_by_mail(path):
            self.ctx.info(_("Condividi pagine"),
                          _("Nessun programma di posta disponibile. Il file è stato salvato in:\n{p}").format(p=path))
        return path

    # ================================================================ PAG-25: avviso al salvataggio
    def save_guard(self, view, info) -> bool:
        """Il ritaglio nasconde contenuto che resta nel file: avvisa e propone l'appiattimento."""
        if view is None:
            return True
        document = view.document
        key = (id(document), document.version)
        if key in self._crop_warned:
            return True
        layout = _optional("layout")
        if layout is None:
            return True
        try:
            with ENGINE_LOCK:
                hidden = layout.hidden_content_outside_crop(document.pdf)
        except Exception:
            return True
        if not hidden:
            return True
        ok = self.ctx.confirm(
            _("Contenuto nascosto dal ritaglio"),
            _("Alcune pagine sono ritagliate: il testo fuori dall'area visibile resta nel file e può essere "
              "copiato o cercato. Per eliminarlo davvero usa l'appiattimento o la sanificazione nella modalità "
              "Proteggi.\n\nSalvare comunque?"))
        if ok:
            self._crop_warned.add(key)   # solo dopo un «Sì»: un «No» deve richiedere di nuovo
        return ok

    # ================================================================ PAG-40
    def find_blank_and_duplicates(self, mode: str = "identical") -> list[int]:
        """Trova pagine vuote e duplicate, le seleziona e propone di eliminarle."""
        view = self.view()
        if view is None:
            return []
        from . import detect
        document = view.document

        def work(progress):
            with ENGINE_LOCK:
                blank = detect.find_blank_pages(document.pdf)
                dups = detect.find_duplicate_pages(document.pdf, mode=mode)
            return blank, dups
        try:
            blank, dups = self.ctx.run_task(_("Ricerca di pagine vuote e duplicate…"), work)
        except FoliantError as e:
            self.ctx.error(_("Pagine vuote e duplicate"), str(e))
            return []
        found = sorted(set(blank) | {d for d, _o in dups})
        if not found:
            self.ctx.info(_("Pagine vuote e duplicate"), _("Nessuna pagina vuota o duplicata trovata."))
            return []
        if self.active():
            self.organizer.grid.select_pages(found)
        lines = [_("Pagina {n}: vuota").format(n=i + 1) for i in blank]
        lines += [_("Pagina {n}: duplicato della pagina {o}").format(n=d + 1, o=o + 1) for d, o in dups]
        shown = "\n".join(lines[:30]) + ("\n…" if len(lines) > 30 else "")
        if len(found) < view.page_count and self.ctx.confirm(
                _("Pagine vuote e duplicate"),
                _("Trovate {n} pagine:").format(n=len(found)) + "\n" + shown + "\n\n" + _("Eliminarle?")):
            done = self.apply(_("Elimina pagine vuote e duplicate"), lambda pdf: E.delete_pages(pdf, found),
                              select=lambda r: [min(found[0], view.page_count - 1)])
            if done is not None:
                self.notify_undo(view, deleted_summary(len(found), {}))
        return found

    # ================================================================ sblocco
    def unlock(self) -> None:
        view = self.view()
        if view is None:
            return
        pw, ok = QInputDialog.getText(self.ctx.window, _("Sblocca documento"),
                                      _("Password del proprietario:"), QLineEdit.EchoMode.Password)
        if not ok:
            return
        if not view.document.unlock(pw):
            self.ctx.error(_("Sblocca documento"), _("Password del proprietario errata."))
            return
        self.update_actions()
        self.ctx.status(_("Documento sbloccato"))

    # ================================================================ selezione rapida
    def select_filter(self, flt: str) -> None:
        view = self.view()
        if view is None:
            return
        with ENGINE_LOCK:
            pages = E.filter_pages(view.document.pdf, range(view.page_count), flt)
        self.organizer.grid.select_pages(pages)

    def select_range(self) -> None:
        view = self.view()
        if view is None:
            return
        text, ok = QInputDialog.getText(self.ctx.window, _("Seleziona pagine"),
                                        _("Pagine (es. 1-3, 8-):"))
        if not ok:
            return
        try:
            pages = E.parse_ranges(text, view.page_count)
        except E.RangeError as e:
            self.ctx.error(_("Seleziona pagine"), str(e))
            return
        self.organizer.grid.select_pages(pages)

    # ================================================================ menu contestuale
    def show_context_menu(self, global_pos) -> None:
        if self.view() is None:
            return
        menu = QMenu(self.organizer)
        for act in self.context_actions:
            if act is None:
                menu.addSeparator()
            elif isinstance(act, QMenu):
                menu.addMenu(act)
            else:
                menu.addAction(act)
        menu.exec(global_pos)

    def view_menu(self, view, page_event, menu: QMenu) -> None:
        """Voci di pagina nel menu del tasto destro della vista documento."""
        if page_event is None or page_event.page is None:
            return
        sub = menu.addMenu(_("Pagina"))
        page = page_event.page

        def run(fn):
            view.go_to_page(page)
            fn()
        sub.addAction(_("Ruota a destra"), lambda: run(lambda: self.rotate(90)))
        sub.addAction(_("Ruota a sinistra"), lambda: run(lambda: self.rotate(-90)))
        sub.addAction(_("Duplica"), lambda: run(self.duplicate))
        sub.addAction(_("Elimina"), lambda: run(self.delete))
        sub.addAction(_("Organizza pagine"), lambda: self.ctx.set_mode("pagine"))


def deleted_summary(count: int, info: dict) -> str:
    """«3 pagine eliminate · 2 segnalibri e 1 link interessati»."""
    text = _("1 pagina eliminata") if count == 1 else _("{n} pagine eliminate").format(n=count)
    parts = []
    if info.get("bookmarks"):
        parts.append(_("1 segnalibro") if info["bookmarks"] == 1 else
                     _("{n} segnalibri").format(n=info["bookmarks"]))
    if info.get("links"):
        parts.append(_("1 link") if info["links"] == 1 else _("{n} link").format(n=info["links"]))
    if info.get("fields"):
        parts.append(_("1 campo modulo") if info["fields"] == 1 else _("{n} campi modulo").format(n=info["fields"]))
    if parts:
        joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " " + _("e") + " " + parts[-1]
        text += " · " + _("{x} interessati").format(x=joined)
    return text


def friendly_error(e: Exception) -> str:
    """Messaggio comprensibile per gli errori del motore su file danneggiati."""
    text = str(e)
    if "cycle in page tree" in text or "page tree" in text or "code=7" in text:
        return _("Il documento è danneggiato: la struttura delle pagine non è valida e l'operazione non può "
                 "essere eseguita. Prova a usare «Salva come» per riparare il file, poi riprova.")
    return _("Operazione non riuscita: {e}").format(e=text)


# ==================================================================== appunti
def pages_mime(data: bytes, view, pages: list[int], with_image: bool = True) -> QMimeData:
    mime = QMimeData()
    mime.setData("application/pdf", QByteArray(data))
    mime.setData(PAGES_MIME, QByteArray(json.dumps({"doc": id(view.document), "pages": pages,
                                                    "pid": os.getpid()}).encode()))
    if with_image:
        try:
            img = view.render_page_image(pages[0], 1.0)
            mime.setImageData(img)
        except Exception:
            pass
    return mime


def clipboard_pdf_bytes() -> bytes | None:
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None or not mime.hasFormat("application/pdf"):
        return None
    data = bytes(mime.data("application/pdf"))
    return data or None


def clipboard_has_pages() -> bool:
    try:
        return clipboard_pdf_bytes() is not None
    except Exception:
        return False


def send_by_mail(path: Path) -> bool:
    """Apre il client di posta con l'allegato (xdg-email). False se non disponibile."""
    exe = shutil.which("xdg-email")
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "--attach", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


# ==================================================================== registrazione
def register(ctx) -> PagesController:
    add_translations("en", EN)
    from pathlib import Path as _P
    for name in sorted(p.stem for p in _P(__file__).parent.glob("*.py") if p.stem not in ("__init__", "module")):
        mod = _optional(name)
        tr = getattr(mod, "TRANSLATIONS", None) if mod else None
        if tr:
            add_translations("en", tr)
    _privacy_cleanup(ctx)
    c = PagesController(ctx)
    ctx.pages_controller = c   # accesso per test e altri moduli
    mode = ctx.add_mode("pagine", _("Pagine"), "layout-grid", 40)
    c.mode = mode
    mode.entered.connect(c.on_mode_entered)
    mode.left.connect(c.on_mode_left)
    ctx.currentViewChanged.connect(c.on_view_changed)
    ctx.documentChanged.connect(c.on_document_changed)
    ctx.documentClosed.connect(c.on_document_closed)
    ctx.add_view_menu_provider(c.view_menu)
    access_changed = getattr(ctx, "documentAccessChanged", None)
    if access_changed is not None:   # sblocco con la password dei permessi, blocco durante l'OCR
        access_changed.connect(lambda _view: c.update_actions())
    if hasattr(ctx, "add_guard"):
        ctx.add_guard("save", c.save_guard)
        ctx.add_guard("save_as", c.save_guard)
    from .actions import build_actions
    build_actions(ctx, c, mode)
    return c
