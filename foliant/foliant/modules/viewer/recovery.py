"""Salvataggio automatico dei documenti modificati e recupero dopo un crash.

- Ogni `viewer/autosave_seconds` secondi (predefinito 60, 0 = disattivato) i
  documenti modificati e cambiati dall'ultima copia vengono copiati nella
  cartella di recupero (vedi `recovery_engine.recovery_dir`). Il file originale
  non viene mai toccato.
- Quando il documento viene salvato o chiuso la sua copia viene eliminata:
  all'uscita regolare non resta nulla.
- All'avvio, le copie lasciate da un processo terminato vengono proposte con
  una finestra non bloccante: Ripristina / Scarta / Più tardi.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import (
    CLOSED, SAVED, Context, Document, DocumentError, PasswordRequired, WrongPassword, _, add_translations, icon,
    theme,
)

from . import recovery_engine as engine

DEFAULT_INTERVAL = 60
LARGE_DOCUMENT_PAGES = 40   # oltre questa soglia la copia si scrive in un thread


class RecoveryDialog(QDialog):
    """Elenco delle copie di recupero con Ripristina / Scarta / Più tardi (non bloccante)."""

    def __init__(self, controller: "RecoveryController", entries: list[engine.RecoveryEntry], parent=None):
        super().__init__(parent)
        self.controller = controller
        self.entries = list(entries)
        self.setObjectName("recoveryDialog")
        self.setWindowTitle(_("Recupero documenti"))
        self.setModal(False)
        self.setMinimumSize(QSize(560, 320))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        badge = QLabel(self)
        badge.setPixmap(icon("life-buoy", theme.hex_color("accent")).pixmap(36, 36))
        badge.setAlignment(Qt.AlignmentFlag.AlignTop)
        head.addWidget(badge)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        title = QLabel(_("Foliant non è stato chiuso correttamente"), self)
        font = title.font()
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setBold(True)
        title.setFont(font)
        texts.addWidget(title)
        self.explain = QLabel(_("Sono state trovate copie di recupero di documenti con modifiche non salvate. "
                                "Scegli quali ripristinare: i file originali non sono stati modificati."), self)
        self.explain.setWordWrap(True)
        self.explain.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        texts.addWidget(self.explain)
        head.addLayout(texts, 1)
        layout.addLayout(head)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels([_("Documento"), _("Percorso originale"), _("Ora")])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for entry in self.entries:
            when = time.strftime("%d/%m/%Y %H:%M", time.localtime(entry.timestamp)) if entry.timestamp else ""
            item = QTreeWidgetItem([entry.display_name, entry.original_path or _("(mai salvato)"), when])
            item.setIcon(0, icon("file-clock"))
            item.setToolTip(1, entry.original_path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, entry.id)
            self.tree.addTopLevelItem(item)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(self)
        self.restore_button = QPushButton(icon("archive-restore"), _("Ripristina"), self)
        self.restore_button.setDefault(True)
        self.discard_button = QPushButton(icon("trash-2"), _("Scarta"), self)
        self.later_button = QPushButton(_("Più tardi"), self)
        buttons.addButton(self.restore_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.discard_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(self.later_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.restore_button.clicked.connect(self.restore)
        self.discard_button.clicked.connect(self.discard)
        self.later_button.clicked.connect(self.later)
        self.tree.itemChanged.connect(self._update_buttons)
        layout.addWidget(buttons)

    def checked_entries(self) -> list[engine.RecoveryEntry]:
        chosen = []
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).checkState(0) == Qt.CheckState.Checked:
                chosen.append(self.entries[i])
        return chosen

    def _update_buttons(self, *_a) -> None:
        enabled = bool(self.checked_entries())
        self.restore_button.setEnabled(enabled)
        self.discard_button.setEnabled(enabled)

    def restore(self) -> None:
        chosen = self.checked_entries()
        self.hide()
        self.controller.restore_entries(chosen)
        self.done(QDialog.DialogCode.Accepted)

    def discard(self) -> None:
        self.controller.discard_entries(self.checked_entries())
        self.done(QDialog.DialogCode.Accepted)

    def later(self) -> None:
        self.done(QDialog.DialogCode.Rejected)


class RecoveryController(QObject):
    _written = Signal(object, str, int, str)   # documento, id, versione, errore

    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self.directory = engine.recovery_dir()
        self.pid_alive = engine.pid_alive
        self.dialog: RecoveryDialog | None = None
        self.notification = None          # avviso non bloccante del nucleo (ctx.notify)
        self.pending: list = []           # copie proposte all'avvio
        self._ids: dict[Document, str] = {}
        self._saved_version: dict[Document, int] = {}
        self._inflight: set[Document] = set()
        self._listeners: dict[Document, object] = {}
        self.last_saved_at: float | None = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self._written.connect(self._on_written, Qt.ConnectionType.QueuedConnection)
        ctx.documentOpened.connect(self._on_opened)
        ctx.documentClosed.connect(self._on_closed)
        try:
            seconds = int(ctx.settings().value("viewer/autosave_seconds", DEFAULT_INTERVAL))
        except (TypeError, ValueError):
            seconds = DEFAULT_INTERVAL
        self.set_interval(seconds)

    # ------------------------------------------------------------------ impostazioni
    def set_interval(self, seconds: int) -> None:
        self.interval = max(0, int(seconds))
        if self.interval:
            self.timer.start(self.interval * 1000)
        else:
            self.timer.stop()

    # ------------------------------------------------------------------ documenti
    def _documents(self) -> list[tuple[Document, object]]:
        seen: dict[Document, object] = {}
        for view in self.ctx.views():
            doc = view.document
            if doc not in seen and not doc.closed:
                seen[doc] = view
        return list(seen.items())

    def _on_opened(self, view) -> None:
        doc = view.document
        if doc in self._listeners:
            return

        def listener(d: Document, kind: str) -> None:
            if kind in (SAVED, CLOSED):
                self.forget(d)

        self._listeners[doc] = listener
        doc.add_listener(listener)

    def _on_closed(self, view) -> None:
        self.forget(view.document)

    def forget(self, doc: Document) -> None:
        """Elimina la copia di recupero del documento (salvato o chiuso)."""
        entry_id = self._ids.get(doc)
        if entry_id:
            engine.remove_entry(self.directory, entry_id)
        self._saved_version.pop(doc, None)
        if doc.closed:
            self._ids.pop(doc, None)
            listener = self._listeners.pop(doc, None)
            if listener is not None:
                doc.remove_listener(listener)

    def entry_id(self, doc: Document) -> str | None:
        return self._ids.get(doc)

    def _needs_copy(self, doc: Document) -> bool:
        return (not doc.closed and doc.is_modified and doc not in self._inflight
                and self._saved_version.get(doc) != doc.version)

    def _meta(self, doc: Document, view) -> dict:
        return engine.make_meta(original_path=str(doc.path) if doc.path else "",
                                display_name=doc.display_name, page=view.current_page)

    # ------------------------------------------------------------------ salvataggio
    def autosave_now(self) -> int:
        """Copia subito (in modo sincrono) i documenti da proteggere; restituisce quanti."""
        count = 0
        for doc, view in self._documents():
            if not self._needs_copy(doc):
                continue
            entry_id = self._ids.setdefault(doc, engine.new_entry_id())
            version = doc.version
            try:
                engine.write_entry(self.directory, entry_id, doc.to_bytes(), self._meta(doc, view))
            except Exception as e:  # noqa: BLE001 - la copia non deve mai disturbare
                self._report_error(str(e))
                continue
            self._saved_version[doc] = version
            count += 1
        if count:
            self._report_saved()
        return count

    def _on_timer(self) -> None:
        for doc, view in self._documents():
            if not self._needs_copy(doc):
                continue
            entry_id = self._ids.setdefault(doc, engine.new_entry_id())
            meta = self._meta(doc, view)
            version = doc.version
            if view.page_count <= LARGE_DOCUMENT_PAGES:
                self._write(doc, entry_id, meta, version)
            else:
                self._inflight.add(doc)
                threading.Thread(target=self._write, args=(doc, entry_id, meta, version),
                                 name="foliant-recovery", daemon=True).start()

    def _write(self, doc: Document, entry_id: str, meta: dict, version: int) -> None:
        """Scrive la copia; può girare in un thread (nessun widget qui)."""
        error = ""
        try:
            engine.write_entry(self.directory, entry_id, doc.to_bytes(), meta)
        except Exception as e:  # noqa: BLE001
            error = str(e) or e.__class__.__name__
        self._written.emit(doc, entry_id, version, error)

    def _on_written(self, doc: Document, entry_id: str, version: int, error: str) -> None:
        self._inflight.discard(doc)
        if doc.closed or not doc.is_modified or self._ids.get(doc) != entry_id:
            engine.remove_entry(self.directory, entry_id)   # salvato o chiuso nel frattempo
            return
        if error:
            self._report_error(error)
            return
        self._saved_version[doc] = version
        self._report_saved()

    def _report_saved(self) -> None:
        self.last_saved_at = time.time()
        self.ctx.status(_("Copia di recupero salvata alle {t}").format(t=time.strftime("%H:%M")), 4000)

    def _report_error(self, error: str) -> None:
        self.ctx.status(_("Copia di recupero non riuscita: {e}").format(e=error), 6000)

    # ------------------------------------------------------------------ recupero
    def orphans(self) -> list[engine.RecoveryEntry]:
        return engine.orphan_entries(self.directory, self.pid_alive)

    def check_for_orphans(self) -> RecoveryDialog | None:
        """Propone il ripristino delle copie lasciate da un processo terminato."""
        if self.dialog is not None and self.dialog.isVisible():
            return self.dialog
        try:
            entries = self.orphans()
        except Exception:  # noqa: BLE001
            return None
        if not entries:
            return None
        self.pending = list(entries)
        n = len(entries)
        text = (_("Foliant non è stato chiuso correttamente: c'è 1 documento con modifiche recuperabili.") if n == 1
                else _("Foliant non è stato chiuso correttamente: ci sono {n} documenti con modifiche recuperabili.")
                .format(n=n))
        self.notification = self.ctx.notify(text, [
            (_("Ripristina"), lambda: self.restore_entries(self.pending)),
            (_("Scegli…"), self.open_dialog),
            (_("Scarta"), lambda: self.discard_entries(self.pending)),
        ], duration_ms=0)
        return self.notification

    def open_dialog(self) -> RecoveryDialog | None:
        """Finestra (non modale) con l'elenco delle copie, per scegliere quali ripristinare."""
        if not self.pending:
            return None
        if self.dialog is not None and self.dialog.isVisible():
            return self.dialog
        self.dialog = RecoveryDialog(self, self.pending, self.ctx.window)
        self.dialog.show()
        self.dialog.raise_()
        return self.dialog

    def ask_password(self, name: str, retry: bool = False) -> str | None:
        text = (_("Password errata. Riprova per «{name}»:") if retry
                else _("La copia di recupero di «{name}» è protetta. Inserisci la password:")).format(name=name)
        password, ok = QInputDialog.getText(self.ctx.window, _("Documento protetto"), text,
                                            QLineEdit.EchoMode.Password)
        return password if ok else None

    def _open_entry(self, entry: engine.RecoveryEntry) -> Document | None:
        data = entry.read_data()
        password = None
        for attempt in range(5):
            try:
                return Document.from_bytes(data, display_name=entry.display_name, password=password, modified=True)
            except PasswordRequired:
                password = self.ask_password(entry.display_name)
            except WrongPassword:
                password = self.ask_password(entry.display_name, retry=True)
            if password is None:
                return None
        return None

    def restore_entries(self, entries: list[engine.RecoveryEntry]) -> list:
        views = []
        for entry in entries:
            try:
                doc = self._open_entry(entry)
            except (OSError, DocumentError) as e:
                self.ctx.status(_("Impossibile ripristinare «{name}»: {e}").format(name=entry.display_name, e=e), 8000)
                continue
            if doc is None:
                continue  # password non inserita: la copia resta per dopo
            original = entry.original_path
            if original and Path(original).parent.is_dir():
                doc.path = Path(original)
            # La copia resta finché il documento non è al sicuro (salvato o chiuso).
            self._ids[doc] = entry.id
            meta = engine.make_meta(original_path=original, display_name=entry.display_name, page=entry.page)
            try:
                engine.write_entry(self.directory, entry.id, None, meta)   # rivendicata da questo processo
            except Exception:  # noqa: BLE001
                pass
            self._saved_version[doc] = doc.version
            view = self.ctx.open_document(doc)
            if 0 <= entry.page < view.page_count:
                view.go_to_page(entry.page)
            views.append(view)
        if views:
            self.ctx.status(_("Documenti ripristinati: {n}. Salvali per conservare le modifiche.").format(n=len(views)),
                            8000)
        return views

    def discard_entries(self, entries: list[engine.RecoveryEntry]) -> None:
        for entry in entries:
            engine.remove_entry(self.directory, entry.id)


def install(ctx: Context) -> RecoveryController:
    add_translations("en", {
        "Foliant non è stato chiuso correttamente: c'è 1 documento con modifiche recuperabili.":
            "Foliant was not closed properly: 1 document has recoverable changes.",
        "Foliant non è stato chiuso correttamente: ci sono {n} documenti con modifiche recuperabili.":
            "Foliant was not closed properly: {n} documents have recoverable changes.",
        "Scegli…": "Choose…",
        "Recupero documenti": "Document recovery",
        "Foliant non è stato chiuso correttamente": "Foliant was not closed properly",
        "Sono state trovate copie di recupero di documenti con modifiche non salvate. "
        "Scegli quali ripristinare: i file originali non sono stati modificati.":
            "Recovery copies of documents with unsaved changes were found. "
            "Choose which ones to restore: the original files have not been changed.",
        "Documento": "Document",
        "Percorso originale": "Original path",
        "Ora": "Time",
        "(mai salvato)": "(never saved)",
        "Ripristina": "Restore",
        "Scarta": "Discard",
        "Più tardi": "Later",
        "Copia di recupero salvata alle {t}": "Recovery copy saved at {t}",
        "Copia di recupero non riuscita: {e}": "Recovery copy failed: {e}",
        "Password errata. Riprova per «{name}»:": "Wrong password. Try again for “{name}”:",
        "La copia di recupero di «{name}» è protetta. Inserisci la password:":
            "The recovery copy of “{name}” is protected. Enter the password:",
        "Documento protetto": "Protected document",
        "Impossibile ripristinare «{name}»: {e}": "Cannot restore “{name}”: {e}",
        "Documenti ripristinati: {n}. Salvali per conservare le modifiche.":
            "Documents restored: {n}. Save them to keep the changes.",
    })
    controller = RecoveryController(ctx)
    QTimer.singleShot(0, controller, controller.check_for_orphans)
    return controller
