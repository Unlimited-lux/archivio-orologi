"""Ricerca dei comandi e scorciatoie personalizzabili (VIS-52).

- Ctrl+K (o «Cerca un comando» in alto a destra, o File › Guida) apre una casella
  che trova qualsiasi comando dell'applicazione per nome (anche dei moduli) e lo
  esegue con Invio; i comandi disabilitati sono mostrati con il motivo.
- Le scorciatoie (File › Opzioni › «Personalizza le scorciatoie…») si assegnano o
  rimuovono con rilevamento dei conflitti, sono salvate nel profilo utente
  (`viewer/shortcuts`) e si esportano/importano in testo.
"""

from __future__ import annotations

import unicodedata

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QAbstractItemView, QHeaderView,
)

from foliant.api import Context, _, add_translations, atomic_write

TRANSLATIONS = {
    "scorciatoie.txt": "shortcuts.txt",
    "Cerca un comando…": "Search for a command…",
    "Cerca un comando": "Search commands",
    "Ricerca dei comandi": "Command search",
    "Personalizza le scorciatoie…": "Customize shortcuts…",
    "Personalizza le scorciatoie": "Customize shortcuts",
    "Scorciatoie da tastiera": "Keyboard shortcuts",
    "Comando": "Command",
    "Scorciatoia": "Shortcut",
    "Cerca…": "Search…",
    "Assegna": "Assign",
    "Rimuovi": "Remove",
    "Ripristina predefinite": "Restore defaults",
    "Esporta…": "Export…",
    "Importa…": "Import…",
    "Chiudi": "Close",
    "Conflitto di scorciatoie": "Shortcut conflict",
    "{k} è già assegnata a «{c}». Sostituirla?": "{k} is already assigned to “{c}”. Replace it?",
    "Non disponibile: {r}": "Not available: {r}",
    "nessun documento aperto": "no document open",
    "Nessun comando trovato": "No command found",
    "Scorciatoie (*.txt)": "Shortcuts (*.txt)",
    "Scorciatoie importate: {n}": "Shortcuts imported: {n}",
    "Guida": "Help",
    "Trova qualsiasi funzione scrivendone il nome": "Find any feature by typing its name",
    "Impostazioni": "Settings",
    "Cambia i tasti dei comandi": "Change command keys",
}


def _norm(text: str) -> str:
    text = text.replace("&", "").replace("…", "").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def command_key(action: QAction) -> str:
    """Identificativo di un comando: il suo testo senza «…» e «&» (nella lingua dell'interfaccia)."""
    return action.property("viewerCommandKey") or action.text().replace("&", "").replace("…", "").strip()


def match_score(query: str, text: str) -> int | None:
    """Punteggio di corrispondenza (più basso = migliore); None se non corrisponde."""
    q, t = _norm(query).split(), _norm(text)
    if not q:
        return 0
    if not all(w in t for w in q):
        return None
    first = t.find(q[0])
    return (0 if t.startswith(q[0]) else 1) * 100 + first


class CommandPalette(QDialog):
    def __init__(self, controller: "CommandsController"):
        super().__init__(controller.ctx.window)
        self.c = controller
        self.setWindowTitle(_("Ricerca dei comandi"))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.field = QLineEdit(self)
        self.field.setPlaceholderText(_("Cerca un comando…"))
        self.field.textChanged.connect(self.refresh)
        self.field.installEventFilter(self)
        layout.addWidget(self.field)
        self.list = QListWidget(self)
        self.list.itemActivated.connect(self.run_item)
        self.list.setMinimumHeight(320)
        from foliant.api import theme
        self.setObjectName("commandPalette")
        self.setStyleSheet(f"QDialog#commandPalette {{ border: 1px solid {theme.hex_color('border')}; border-radius: 8px; }}"
                           " QListWidget::item { padding: 4px 6px; }")
        layout.addWidget(self.list)
        self.actions: list[QAction] = []

    def refresh(self, *_a) -> None:
        self.list.clear()
        self.actions = self.c.search(self.field.text())
        for n, act in enumerate(self.actions[:200]):
            label = act.text().replace("&", "")
            shortcut = act.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
            item = QListWidgetItem(label + (f"    {shortcut}" if shortcut else ""))
            if not act.isEnabled():
                item.setForeground(self.palette().placeholderText())
                item.setToolTip(_("Non disponibile: {r}").format(r=self.c.reason(act)))
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.list.addItem(item)
        if not self.actions:
            item = QListWidgetItem(_("Nessun comando trovato"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        elif self.list.count():
            self.list.setCurrentRow(0)

    def run_item(self, item) -> None:
        i = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(i, int) and i < len(self.actions):
            act = self.actions[i]
            if act.isEnabled():
                self.hide()
                QTimer.singleShot(0, act.trigger)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.field and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                row = self.list.currentRow() + (1 if ev.key() == Qt.Key.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
                return True
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self.run_item(item)
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
        return False


class ShortcutsDialog(QDialog):
    def __init__(self, controller: "CommandsController"):
        super().__init__(controller.ctx.window)
        self.c = controller
        self.setWindowTitle(_("Personalizza le scorciatoie"))
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText(_("Cerca…"))
        self.filter.textChanged.connect(self.refresh)
        layout.addWidget(self.filter)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels([_("Comando"), _("Scorciatoia")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.editor = QKeySequenceEdit(self)
        row.addWidget(self.editor, 1)
        for text, slot in ((_("Assegna"), self._assign), (_("Rimuovi"), self._remove)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)
        row2 = QHBoxLayout()
        for text, slot in ((_("Ripristina predefinite"), self._defaults), (_("Esporta…"), self.c.export_dialog),
                           (_("Importa…"), self.c.import_dialog)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row2.addWidget(b)
        row2.addStretch(1)
        close = QPushButton(_("Chiudi"))
        close.clicked.connect(self.hide)
        row2.addWidget(close)
        layout.addLayout(row2)
        self.rows: list[QAction] = []

    def refresh(self, *_a) -> None:
        self.rows = self.c.search(self.filter.text())
        self.table.setRowCount(len(self.rows))
        for r, act in enumerate(self.rows):
            self.table.setItem(r, 0, QTableWidgetItem(act.text().replace("&", "")))
            self.table.setItem(r, 1, QTableWidgetItem(act.shortcut().toString(QKeySequence.SequenceFormat.NativeText)))

    def _current(self) -> QAction | None:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def _assign(self) -> None:
        act = self._current()
        if act is not None and not self.editor.keySequence().isEmpty():
            self.c.assign(act, self.editor.keySequence().toString(), ask=True)
            self.refresh(self.filter.text())

    def _remove(self) -> None:
        act = self._current()
        if act is not None:
            self.c.assign(act, "")
            self.refresh(self.filter.text())

    def _defaults(self) -> None:
        self.c.restore_defaults()
        self.refresh(self.filter.text())


class CommandsController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        self._defaults: dict[int, str] = {}
        self.palette: CommandPalette | None = None
        self.dialog: ShortcutsDialog | None = None
        self.act_palette = ctx.action(_("Cerca un comando…"), "command", self.open_palette, shortcut="Ctrl+K",
                                      requires_document=False)
        self.act_palette.setProperty("viewerCommandKey", "Ricerca dei comandi")
        self.act_palette.setIconText(_("Cerca un comando"))   # testo del pulsante in alto a destra
        # «Personalizza»: distinto dall'elenco «Scorciatoie da tastiera» del nucleo (File › Guida)
        self.act_shortcuts = ctx.action(_("Personalizza le scorciatoie…"), "keyboard", self.open_shortcuts,
                                        requires_document=False)
        ctx.set_command_search(self.act_palette)            # pulsante in alto a destra
        ctx.add_file_action(self.act_palette, "guida", _("Guida"), _("Trova qualsiasi funzione scrivendone il nome"),
                            order=15)
        ctx.add_file_action(self.act_shortcuts, "opzioni", _("Impostazioni"), _("Cambia i tasti dei comandi"),
                            order=20)
        # le scorciatoie personalizzate si applicano quando tutti i moduli hanno registrato le azioni
        QTimer.singleShot(0, self.apply_saved)

    # ---------------------------------------------------------------- comandi
    def commands(self) -> list[QAction]:
        """Tutte le azioni con un nome (finestra e moduli), senza duplicati di testo+scorciatoia."""
        seen: set[tuple[str, str]] = set()
        out: list[QAction] = []
        for act in self.ctx.window.findChildren(QAction):
            text = act.text().replace("&", "").strip()
            if not text or act.isSeparator() or act.menu() is not None:
                continue
            key = (text, act.shortcut().toString())
            if key in seen:
                continue
            seen.add(key)
            out.append(act)
            self._defaults.setdefault(id(act), act.shortcut().toString())
        return out

    def search(self, query: str) -> list[QAction]:
        scored = []
        for act in self.commands():
            s = match_score(query, act.text())
            if s is None:
                s2 = match_score(query, act.toolTip())
                s = None if s2 is None else s2 + 500
            if s is not None:
                scored.append((not act.isEnabled(), s, act.text(), act))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored]

    def reason(self, act: QAction) -> str:
        tip = act.toolTip()
        if tip and tip.replace("&", "") != act.text().replace("&", "") and not tip.startswith(act.text()):
            return tip
        return _("nessun documento aperto") if self.ctx.current_view() is None else tip

    def open_palette(self) -> None:
        if self.palette is None:
            self.palette = CommandPalette(self)
        self.palette.field.clear()
        self.palette.refresh()
        w = self.ctx.window
        self.palette.adjustSize()
        self.palette.move(w.geometry().center().x() - self.palette.width() // 2, w.geometry().top() + 80)
        self.palette.show()
        self.palette.raise_()
        self.palette.field.setFocus()

    def run(self, query: str) -> QAction | None:
        """Esegue il primo comando abilitato che corrisponde (usato dai test)."""
        for act in self.search(query):
            if act.isEnabled():
                act.trigger()
                return act
        return None

    # ------------------------------------------------------------- scorciatoie
    def owner_of(self, sequence: str, exclude: QAction | None = None) -> QAction | None:
        seq = QKeySequence(sequence)
        if seq.isEmpty():
            return None
        for act in self.commands():
            if act is not exclude and any(s == seq for s in act.shortcuts()):
                return act
        return None

    def assign(self, act: QAction, sequence: str, ask: bool = False) -> bool:
        """Assegna (o con "" rimuove) una scorciatoia; in caso di conflitto avvisa e,
        se confermato, la toglie all'altro comando."""
        other = self.owner_of(sequence, act)
        if other is not None:
            msg = _("{k} è già assegnata a «{c}». Sostituirla?").format(
                k=QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText),
                c=other.text().replace("&", ""))
            if ask and not self.ctx.confirm(_("Conflitto di scorciatoie"), msg):
                return False
            if not ask:
                self.ctx.status(msg)
            self._set(other, "")
        self._set(act, sequence)
        return True

    def _set(self, act: QAction, sequence: str) -> None:
        self.commands()   # registra i valori predefiniti
        act.setShortcut(QKeySequence(sequence))
        if sequence and act not in self.ctx.window.actions():
            self.ctx.window.addAction(act)
        saved = self.saved()
        saved[command_key(act)] = sequence
        self.ctx.settings().setValue("viewer/shortcuts", [f"{k}\t{v}" for k, v in saved.items()])

    def saved(self) -> dict[str, str]:
        value = self.ctx.settings().value("viewer/shortcuts", []) or []
        if isinstance(value, str):
            value = [value]
        out = {}
        for line in value:
            if isinstance(line, str) and "\t" in line:
                k, v = line.split("\t", 1)
                out[k] = v
        return out

    def apply_saved(self) -> int:
        saved = self.saved()
        n = 0
        if not saved:
            return 0
        for act in self.commands():
            k = command_key(act)
            if k in saved:
                act.setShortcut(QKeySequence(saved[k]))
                if saved[k] and act not in self.ctx.window.actions():
                    self.ctx.window.addAction(act)
                n += 1
        return n

    def restore_defaults(self) -> None:
        acts = self.commands()
        self.ctx.settings().remove("viewer/shortcuts")
        for act in acts:
            if id(act) in self._defaults:
                act.setShortcut(QKeySequence(self._defaults[id(act)]))

    def export_text(self) -> str:
        lines = []
        for act in sorted(self.commands(), key=lambda a: a.text()):
            seq = act.shortcut().toString()
            if seq:
                lines.append(f"{command_key(act)}\t{seq}")
        return "\n".join(lines) + "\n"

    def import_text(self, text: str) -> int:
        entries = {}
        for line in text.splitlines():
            if "\t" in line:
                k, v = line.split("\t", 1)
                entries[k.strip()] = v.strip()
        by_key = {command_key(a): a for a in self.commands()}
        n = 0
        for k, v in entries.items():
            act = by_key.get(k)
            if act is not None:
                self.assign(act, v)
                n += 1
        return n

    def export_dialog(self) -> None:
        path = self.ctx.ask_save_path(_("Scorciatoie da tastiera"), _("scorciatoie.txt"), _("Scorciatoie (*.txt)"), ".txt")
        if path:
            atomic_write(path, self.export_text().encode("utf-8"))

    def import_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Scorciatoie da tastiera"), _("Scorciatoie (*.txt)"), multiple=False)
        if paths:
            try:
                with open(paths[0], encoding="utf-8") as f:
                    n = self.import_text(f.read())
            except (OSError, UnicodeDecodeError) as e:
                self.ctx.error(_("Scorciatoie da tastiera"), str(e))
                return
            self.ctx.status(_("Scorciatoie importate: {n}").format(n=n))
            if self.dialog is not None:
                self.dialog.refresh(self.dialog.filter.text())

    def open_shortcuts(self) -> None:
        if self.dialog is None:
            self.dialog = ShortcutsDialog(self)
        self.dialog.refresh("")
        self.dialog.show()
        self.dialog.raise_()


def install(ctx: Context) -> CommandsController:
    add_translations("en", TRANSLATIONS)
    return CommandsController(ctx)
