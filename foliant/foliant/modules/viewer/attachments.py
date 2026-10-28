"""Pannello Allegati (VIS-22): file incorporati nel documento e nelle pagine.

Apri (PDF in una nuova scheda; altri tipi con l'applicazione del sistema su
una copia temporanea privata, dopo un avviso; eseguibili bloccati), Salva come,
Salva tutti, Aggiungi ed Elimina (annullabili). La logica pura è in
`attachments_engine.py`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu, QStackedWidget,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import (
    ENGINE_LOCK, Document, DocumentError, PasswordRequired, WrongPassword, _, add_translations, atomic_write,
    bind_icon, icon,
)

from . import attachments_engine as engine
from .attachments_engine import Attachment

ROLE_ITEM = Qt.ItemDataRole.UserRole
ROLE_SORT = Qt.ItemDataRole.UserRole + 1

COL_NAME, COL_DESC, COL_SIZE, COL_PAGE, COL_CREATED, COL_MODIFIED = range(6)

EN = {
    "Il documento non consente modifiche: allegati in sola lettura.":
        "The document does not allow changes: attachments are read-only.",
    "Allegati": "Attachments",
    "Apri": "Open",
    "Salva come…": "Save as…",
    "Salva tutti…": "Save all…",
    "Aggiungi…": "Add…",
    "Elimina": "Delete",
    "Vai alla pagina": "Go to page",
    "Nome": "Name",
    "Descrizione": "Description",
    "Dimensione": "Size",
    "Pagina": "Page",
    "Creato": "Created",
    "Modificato": "Modified",
    "Nessun documento aperto.": "No document open.",
    "Questo documento non contiene allegati.": "This document has no attachments.",
    "Usa «Aggiungi…» per incorporare un file.": "Use “Add…” to embed a file.",
    "Documento": "Document",
    "{n} allegati": "{n} attachments",
    "1 allegato": "1 attachment",
    "Apertura bloccata": "Opening blocked",
    "«{name}» è un programma o uno script e potrebbe danneggiare il computer: non viene aperto. "
    "Puoi salvarlo con «Salva come…» e controllarlo prima di usarlo.":
        "“{name}” is a program or a script and could harm your computer: it will not be opened. "
        "You can save it with “Save as…” and check it before using it.",
    "Aprire l'allegato?": "Open the attachment?",
    "«{name}» verrà aperto con l'applicazione predefinita del sistema. I file allegati possono contenere "
    "contenuti dannosi: aprilo solo se ti fidi della sua origine.\n\nContinuare?":
        "“{name}” will be opened with the system's default application. Attached files may contain "
        "harmful content: open it only if you trust where it comes from.\n\nContinue?",
    "Impossibile aprire l'allegato": "Unable to open the attachment",
    "Impossibile salvare l'allegato": "Unable to save the attachment",
    "Salva allegato": "Save attachment",
    "Salva tutti gli allegati": "Save all attachments",
    "Scegli i file da allegare": "Choose files to attach",
    "Tutti i file (*)": "All files (*)",
    "Aggiungi allegato": "Add attachment",
    "Aggiungi allegati": "Add attachments",
    "Elimina allegato": "Delete attachment",
    "Eliminare l'allegato «{name}»? Puoi annullare con Annulla.":
        "Delete the attachment “{name}”? You can revert this with Undo.",
    "Impossibile aggiungere l'allegato": "Unable to add the attachment",
    "Impossibile eliminare l'allegato": "Unable to delete the attachment",
    "Impossibile leggere «{name}»: {err}": "Unable to read “{name}”: {err}",
    "Salvato «{name}».": "Saved “{name}”.",
    "Salvati {n} allegati in «{folder}».": "Saved {n} attachments in “{folder}”.",
    "Allegato PDF protetto": "Protected PDF attachment",
    "«{name}» è protetto. Inserisci la password:": "“{name}” is protected. Enter the password:",
    "Password errata.": "Wrong password.",
    "L'allegato non è un PDF valido.": "The attachment is not a valid PDF.",
    "Allegato non più presente nel documento.": "The attachment is no longer in the document.",
}


class _Item(QTreeWidgetItem):
    """Riga ordinabile per valore (dimensione, date, pagina) e non per testo."""

    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = self.treeWidget()
        col = tree.sortColumn() if tree is not None else 0
        a, b = self.data(col, ROLE_SORT), other.data(col, ROLE_SORT)
        if a is not None and b is not None:
            try:
                return a < b
            except TypeError:
                pass
        return self.text(col).lower() < other.text(col).lower()


def _fmt_date(dt) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


class AttachmentsPanel(QWidget):
    """Pannello laterale e controller della parte `attachments`."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.view = None
        self._doc = None
        self._items: list[Attachment] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        title = QLabel(_("Allegati"))
        title.setProperty("heading", True)
        self.count_label = QLabel("")
        self.count_label.setProperty("muted", True)
        head = QHBoxLayout()
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.count_label)
        layout.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.btn_open = self._button("external-link", _("Apri"), self.open_selected)
        self.btn_save = self._button("download", _("Salva come…"), self.save_selected)
        self.btn_save_all = self._button("save-all", _("Salva tutti…"), self.save_all)
        self.btn_add = self._button("file-plus", _("Aggiungi…"), self.add_files)
        self.btn_delete = self._button("trash-2", _("Elimina"), self.delete_selected)
        for b in (self.btn_open, self.btn_save, self.btn_save_all):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.btn_add)
        bar.addWidget(self.btn_delete)
        layout.addLayout(bar)

        self.stack = QStackedWidget()
        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels([_("Nome"), _("Descrizione"), _("Dimensione"), _("Pagina"), _("Creato"),
                                   _("Modificato")])
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(COL_PAGE, Qt.SortOrder.AscendingOrder)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.resizeSection(COL_NAME, 150)
        header.resizeSection(COL_DESC, 140)
        for col in (COL_SIZE, COL_PAGE, COL_CREATED, COL_MODIFIED):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setProperty("muted", True)
        self.empty.setContentsMargins(12, 24, 12, 24)
        self.stack.addWidget(self.tree)
        self.stack.addWidget(self.empty)
        layout.addWidget(self.stack, 1)

        self._dirty = False          # elenco da rileggere alla prossima visualizzazione
        self._pending = False        # aggiornamento già programmato (raggruppa le modifiche)
        ctx.documentChanged.connect(self._on_document_changed)
        ctx.documentAccessChanged.connect(lambda v: self._update_buttons() if v is self.view else None)
        ctx.documentOpened.connect(self._on_document_opened)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.cleanup_temp)
        self.set_view(None)

    # ------------------------------------------------------------------ widget
    def _button(self, icon_name: str, text: str, slot) -> QToolButton:
        b = QToolButton(self)
        bind_icon(b, icon_name)
        b.setToolTip(text)
        b.setAccessibleName(text)
        b.setAutoRaise(True)
        b.clicked.connect(lambda _c=False: slot())
        return b

    def set_view(self, view) -> None:
        self.view = view
        self._doc = view.document if view is not None else None
        if view is None or self.isVisible():
            self.refresh()
        else:
            self._dirty = True

    def _on_document_changed(self, view) -> None:
        """L'elenco si rilegge solo a pannello visibile, una volta per giro di eventi:
        a pannello nascosto si rilegge quando viene mostrato (o quando lo si interroga)."""
        if view is not self.view:
            return
        self._dirty = True
        if self.isVisible() and not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._flush)

    def _flush(self) -> None:
        try:
            self._pending = False
            if self._dirty and self.isVisible():
                self.refresh()
        except RuntimeError:
            pass     # pannello già distrutto

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def ensure_fresh(self) -> None:
        if self._dirty:
            self.refresh()

    def _on_document_opened(self, view) -> None:
        try:
            with ENGINE_LOCK:
                pdf = view.document.pdf
                auto = pdf.pagemode == "UseAttachments" or bool(pdf.xref_get_key(pdf.pdf_catalog(), "Collection")[0]
                                                               not in ("null", ""))
        except Exception:
            auto = False
        if auto:
            self.ctx.show_sidebar_panel("attachments")

    # ----------------------------------------------------------------- elenco
    def items(self) -> list[Attachment]:
        self.ensure_fresh()
        return list(self._items)

    def refresh(self) -> None:
        self._dirty = False
        self._items = []
        if self._doc is not None and not self._doc.closed:
            try:
                with ENGINE_LOCK:
                    self._items = engine.list_attachments(self._doc.pdf)
            except Exception:
                self._items = []
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for att in self._items:
            row = _Item()
            row.setText(COL_NAME, att.filename)
            row.setToolTip(COL_NAME, att.filename + (f"\n{att.relationship}" if att.relationship else ""))
            bind = "file-text" if att.is_pdf else ("file-warning" if engine.is_executable(att.filename) else "file")
            row.setIcon(COL_NAME, icon(bind))
            row.setText(COL_DESC, att.description)
            row.setToolTip(COL_DESC, att.description)
            row.setText(COL_SIZE, engine.format_size(att.size))
            row.setData(COL_SIZE, ROLE_SORT, att.size)
            row.setTextAlignment(COL_SIZE, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.setText(COL_PAGE, str(att.page + 1) if att.page is not None else _("Documento"))
            row.setData(COL_PAGE, ROLE_SORT, -1 if att.page is None else att.page)
            row.setText(COL_CREATED, _fmt_date(att.created))
            row.setData(COL_CREATED, ROLE_SORT, att.created.timestamp() if att.created else 0.0)
            row.setText(COL_MODIFIED, _fmt_date(att.modified))
            row.setData(COL_MODIFIED, ROLE_SORT, att.modified.timestamp() if att.modified else 0.0)
            row.setData(COL_NAME, ROLE_ITEM, att)
            self.tree.addTopLevelItem(row)
        self.tree.setSortingEnabled(True)
        if self._doc is None:
            self.empty.setText(_("Nessun documento aperto."))
            self.stack.setCurrentWidget(self.empty)
            self.count_label.setText("")
        elif not self._items:
            self.empty.setText(_("Questo documento non contiene allegati.") + "\n\n"
                               + _("Usa «Aggiungi…» per incorporare un file."))
            self.stack.setCurrentWidget(self.empty)
            self.count_label.setText("")
        else:
            self.stack.setCurrentWidget(self.tree)
            n = len(self._items)
            self.count_label.setText(_("1 allegato") if n == 1 else _("{n} allegati").format(n=n))
        self._update_buttons()

    @property
    def is_empty(self) -> bool:
        self.ensure_fresh()
        return self.stack.currentWidget() is self.empty

    def selected_items(self) -> list[Attachment]:
        return [r.data(COL_NAME, ROLE_ITEM) for r in self.tree.selectedItems()]

    def select(self, att: Attachment) -> None:
        self.ensure_fresh()
        for i in range(self.tree.topLevelItemCount()):
            row = self.tree.topLevelItem(i)
            if row.data(COL_NAME, ROLE_ITEM).key == att.key:
                self.tree.setCurrentItem(row)
                return

    def can_modify(self) -> bool:
        """Aggiungere ed eliminare allegati modifica il documento: serve il permesso (VIS-06)."""
        return self._doc is not None and self._doc.allows(pymupdf.PDF_PERM_MODIFY)

    def _update_buttons(self) -> None:
        has_doc = self._doc is not None
        sel = bool(self.tree.selectedItems())
        editable = self.can_modify()
        self.btn_add.setEnabled(editable)
        self.btn_save_all.setEnabled(bool(self._items))
        for b in (self.btn_open, self.btn_save):
            b.setEnabled(sel)
        self.btn_delete.setEnabled(sel and editable)
        reason = _("Il documento non consente modifiche: allegati in sola lettura.")
        for b, text in ((self.btn_add, _("Aggiungi…")), (self.btn_delete, _("Elimina"))):
            b.setToolTip(text if editable or not has_doc else f"{text}\n{reason}")

    def _context_menu(self, pos) -> None:
        row = self.tree.itemAt(pos)
        menu = QMenu(self)
        self.build_menu(menu, row.data(COL_NAME, ROLE_ITEM) if row is not None else None)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def build_menu(self, menu: QMenu, att: Attachment | None) -> None:
        if att is not None:
            a = menu.addAction(_("Apri"), lambda: self.open_item(att))
            bind_icon(a, "external-link")
            a.setEnabled(not engine.is_executable(att.filename))
            a = menu.addAction(_("Salva come…"), lambda: self.save_item(att))
            bind_icon(a, "download")
            if att.page is not None:
                a = menu.addAction(_("Vai alla pagina"), lambda: self.go_to(att))
                bind_icon(a, "arrow-right")
            menu.addSeparator()
        if self._items:
            a = menu.addAction(_("Salva tutti…"), self.save_all)
            bind_icon(a, "save-all")
        editable = self.can_modify()
        if self._doc is not None:
            a = menu.addAction(_("Aggiungi…"), self.add_files)
            bind_icon(a, "file-plus")
            a.setEnabled(editable)
        if att is not None:
            a = menu.addAction(_("Elimina"), lambda: self.delete_item(att))
            bind_icon(a, "trash-2")
            a.setEnabled(editable)

    def _double_clicked(self, row: QTreeWidgetItem, _col: int) -> None:
        att = row.data(COL_NAME, ROLE_ITEM)
        if att.page is not None:
            self.go_to(att)
        else:
            self.open_item(att)

    # ------------------------------------------------------------- dialoghi
    # Metodi separati per poterli simulare nei test.
    def ask_save_path(self, suggested: str) -> str | None:
        ext = os.path.splitext(suggested)[1]
        flt = f"*{ext} (*{ext})" if ext else _("Tutti i file (*)")
        return self.ctx.ask_save_path(_("Salva allegato"), suggested, f"{flt};;{_('Tutti i file (*)')}",
                                      default_suffix="")

    def ask_directory(self) -> str | None:
        return self.ctx.ask_directory(_("Salva tutti gli allegati"))

    def ask_open_paths(self) -> list[str]:
        return self.ctx.ask_open_paths(_("Scegli i file da allegare"), _("Tutti i file (*)"), multiple=True)

    def confirm(self, title: str, message: str) -> bool:
        return self.ctx.confirm(title, message)

    def ask_password(self, name: str) -> str | None:
        text, ok = QInputDialog.getText(self, _("Allegato PDF protetto"),
                                        _("«{name}» è protetto. Inserisci la password:").format(name=name),
                                        QLineEdit.EchoMode.Password)
        return text if ok else None

    def open_external(self, path: str) -> bool:
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------------------------------------------------------------- azioni
    def data_of(self, att: Attachment) -> bytes:
        with ENGINE_LOCK:
            return engine.extract(self._doc.pdf, att)

    def _selected_or_none(self) -> Attachment | None:
        sel = self.selected_items()
        return sel[0] if sel else None

    def open_selected(self) -> None:
        att = self._selected_or_none()
        if att is not None:
            self.open_item(att)

    def save_selected(self) -> None:
        for att in self.selected_items():
            self.save_item(att)

    def delete_selected(self) -> None:
        sel = self.selected_items()
        if len(sel) == 1:
            self.delete_item(sel[0])

    def go_to(self, att: Attachment) -> None:
        if self.view is not None and att.page is not None:
            self.view.go_to_page(att.page)

    def open_item(self, att: Attachment):
        """Apre l'allegato. Restituisce la nuova vista (PDF), il percorso temporaneo
        (altri tipi) oppure None se l'apertura è bloccata o annullata."""
        if self._doc is None:
            return None
        name = engine.safe_filename(att.filename)
        if engine.is_executable(att.filename):
            self.ctx.error(_("Apertura bloccata"), _(
                "«{name}» è un programma o uno script e potrebbe danneggiare il computer: non viene aperto. "
                "Puoi salvarlo con «Salva come…» e controllarlo prima di usarlo.").format(name=name))
            return None
        try:
            data = self.data_of(att)
        except Exception:
            self.ctx.error(_("Impossibile aprire l'allegato"), _("Allegato non più presente nel documento."))
            return None
        if engine.is_executable(att.filename, data):
            self.ctx.error(_("Apertura bloccata"), _(
                "«{name}» è un programma o uno script e potrebbe danneggiare il computer: non viene aperto. "
                "Puoi salvarlo con «Salva come…» e controllarlo prima di usarlo.").format(name=name))
            return None
        if engine.looks_like_pdf(att.filename, data):
            return self._open_pdf(name, data)
        if not self.confirm(_("Aprire l'allegato?"), _(
                "«{name}» verrà aperto con l'applicazione predefinita del sistema. I file allegati possono contenere "
                "contenuti dannosi: aprilo solo se ti fidi della sua origine.\n\nContinuare?").format(name=name)):
            return None
        try:
            folder = tempfile.mkdtemp(prefix="foliant-allegato-")   # 0700: solo l'utente
            os.chmod(folder, 0o700)
            self._temp_dirs().append(folder)
            target = Path(folder) / name
            atomic_write(target, data)
            os.chmod(target, 0o600)
        except (OSError, DocumentError) as e:
            self.ctx.error(_("Impossibile aprire l'allegato"), str(e))
            return None
        if not self.open_external(str(target)):
            self.ctx.error(_("Impossibile aprire l'allegato"), str(target))
        return str(target)

    def _temp_dirs(self) -> list[str]:
        if not hasattr(self, "_temps"):
            self._temps: list[str] = []
        return self._temps

    def cleanup_temp(self) -> None:
        for folder in self._temp_dirs():
            shutil.rmtree(folder, ignore_errors=True)
        self._temps = []

    def _open_pdf(self, name: str, data: bytes):
        password = None
        while True:
            try:
                document = Document.from_bytes(data, display_name=name, password=password, modified=False)
                break
            except PasswordRequired:
                password = self.ask_password(name)
                if password is None:
                    return None
            except WrongPassword:
                self.ctx.error(_("Allegato PDF protetto"), _("Password errata."))
                password = self.ask_password(name)
                if password is None:
                    return None
            except DocumentError:
                self.ctx.error(_("Impossibile aprire l'allegato"), _("L'allegato non è un PDF valido."))
                return None
        return self.ctx.open_document(document)

    def save_item(self, att: Attachment, path: str | None = None) -> str | None:
        """Salva i dati decodificati dell'allegato. Restituisce il percorso scritto."""
        if self._doc is None:
            return None
        name = engine.safe_filename(att.filename)
        if path is None:
            path = self.ask_save_path(name)
            if not path:
                return None
        try:
            data = self.data_of(att)
            atomic_write(path, data)
        except Exception as e:
            self.ctx.error(_("Impossibile salvare l'allegato"), str(e))
            return None
        self.ctx.status(_("Salvato «{name}».").format(name=Path(path).name))
        return path

    def save_all(self, folder: str | None = None) -> list[str]:
        if self._doc is None or not self._items:
            return []
        if folder is None:
            folder = self.ask_directory()
            if not folder:
                return []
        written = []
        try:
            for att, name in zip(self._items, engine.save_all_names(self._items)):
                target = Path(folder) / name
                atomic_write(target, self.data_of(att))
                written.append(str(target))
        except Exception as e:
            self.ctx.error(_("Impossibile salvare l'allegato"), str(e))
            return written
        self.ctx.status(_("Salvati {n} allegati in «{folder}».").format(n=len(written), folder=folder))
        return written

    def add_files(self, paths: list[str] | None = None) -> list[str]:
        """Incorpora i file scelti come allegati del documento (un passo annullabile)."""
        if not self.can_modify():
            return []
        if paths is None:
            paths = self.ask_open_paths()
        if not paths:
            return []
        files = []
        for p in paths:
            try:
                files.append((Path(p).name, Path(p).read_bytes()))
            except OSError as e:
                self.ctx.error(_("Impossibile aggiungere l'allegato"),
                               _("Impossibile leggere «{name}»: {err}").format(name=Path(p).name, err=e.strerror))
                return []
        label = _("Aggiungi allegato") if len(files) == 1 else _("Aggiungi allegati")
        keys = []
        try:
            with self._doc.change(label) as pdf:
                for name, data in files:
                    keys.append(engine.add(pdf, name, data))
        except Exception as e:
            self.ctx.error(_("Impossibile aggiungere l'allegato"), str(e))
            return []
        self.refresh()
        return keys

    def delete_item(self, att: Attachment, confirm: bool = True) -> bool:
        if not self.can_modify():
            return False
        if confirm and not self.confirm(_("Elimina allegato"), _(
                "Eliminare l'allegato «{name}»? Puoi annullare con Annulla.").format(name=att.filename)):
            return False
        try:
            with self._doc.change(_("Elimina allegato")) as pdf:
                engine.delete(pdf, att)
        except Exception as e:
            self.ctx.error(_("Impossibile eliminare l'allegato"), str(e))
            return False
        self.refresh()
        return True


def install(ctx):
    add_translations("en", EN)
    panel = AttachmentsPanel(ctx)
    ctx.add_sidebar_panel("attachments", _("Allegati"), "paperclip", panel, order=40)
    return panel
