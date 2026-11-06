"""Ricerca dei comandi e scorciatoie personalizzabili (VIS-52).

- Ctrl+K (o «Cerca un comando» in alto a destra, o File › Guida) apre una casella
  che trova qualsiasi comando dell'applicazione per nome (anche dei moduli), anche dal
  nome del menu o del gruppo che lo contiene («inserisci pagina» → Inserisci › Pagina vuota…).
  Accanto al nome c'è la scheda (e il gruppo) dove si trova il comando: così si distinguono
  due comandi con lo stesso nome. Ci sono anche i comandi «Vai alla scheda …», «File» e
  «Tema: …». Un comando di un'altra scheda si avvia passando alla sua scheda.
- Invio esegue il comando scelto: con le frecce o il mouse, oppure quello che corrisponde
  esattamente a quanto scritto. Con più comandi possibili il primo Invio sceglie la voce
  evidenziata e il secondo la esegue, così nessun comando che modifica il documento parte
  da un nome scritto a metà. I comandi disattivati stanno in fondo con il motivo; un modulo
  può indicarlo con la proprietà dinamica `disabledReason` dell'azione.
- Le scorciatoie (File › Opzioni › «Personalizza le scorciatoie…») si assegnano o
  rimuovono con rilevamento dei conflitti, sono salvate nel profilo utente
  (`viewer/shortcuts`) e si esportano/importano in testo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QDialog, QDockWidget, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableWidget,
    QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidgetAction, QAbstractItemView, QHeaderView,
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
    # collaudo: schede, temi, voci con lo stesso nome, scelta prima di eseguire (C-1, C-2, C-13)
    "Vai alla scheda {t}": "Go to the {t} tab",
    "File": "File",
    "Tema: Chiaro": "Theme: Light",
    "Tema: Scuro": "Theme: Dark",
    "Tema: Come il sistema": "Theme: Same as system",
    "non si applica al documento o alla selezione attuali": "does not apply to the current document or selection",
    "non disponibile": "not available",
    "«{c}» non è disponibile: {r}": "“{c}” is not available: {r}",
    "Invio esegue «{c}»": "Enter runs “{c}”",
    "Invio sceglie «{c}», un secondo Invio lo esegue": "Enter picks “{c}”, a second Enter runs it",
    "Premi di nuovo Invio per eseguire «{c}»": "Press Enter again to run “{c}”",
    "Invio apre la scheda {t}.": "Enter opens the {t} tab.",
}

SEP = " › "          # tra scheda, gruppo, menu e nome


def _norm(text: str) -> str:
    text = text.replace("&", "").replace("…", "").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _words(text: str) -> list[str]:
    """Parole di un testo senza accenti, maiuscole e punteggiatura («Tema: Scuro» → tema, scuro)."""
    return re.findall(r"\w+", _norm(text))


def _clean(text: str) -> str:
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").strip()


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


@dataclass(frozen=True)
class Place:
    """Dove si trova un comando: scheda (o «File»), gruppo della barra (o pagina della scheda File)
    e menu che lo contengono."""
    mode_id: str | None = None
    tab: str = ""
    group: str = ""
    menu: tuple[str, ...] = ()

    def context(self) -> str:
        """«Pagine», «Modifica › Oggetti», «File › Esporta»."""
        parts = [self.tab] + ([self.group] if self.group and self.group != self.tab else [])
        return SEP.join(p for p in parts if p)


PARTS_ROLE = Qt.ItemDataRole.UserRole + 1     # (nome, posto, scorciatoia, disattivato) di una voce


class _CommandDelegate(QStyledItemDelegate):
    """Voce della ricerca: nome, poi scheda e gruppo attenuati, scorciatoia a destra. Il testo della
    voce (letto dai lettori di schermo) resta quello completo."""

    def paint(self, painter, option, index) -> None:
        parts = index.data(PARTS_ROLE)
        if not parts:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget).adjusted(4, 0, -4, 0)
        name, where, keys, disabled = parts
        muted = opt.palette.color(QPalette.ColorRole.PlaceholderText)
        main = muted if disabled else opt.palette.color(QPalette.ColorRole.Text)
        fm = opt.fontMetrics
        flags = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        painter.save()
        painter.setPen(muted)
        if keys:
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight), keys)
            rect.setRight(rect.right() - fm.horizontalAdvance(keys) - 16)
        painter.setPen(main)
        shown = fm.elidedText(name, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, flags, shown)
        rect.setLeft(rect.left() + fm.horizontalAdvance(shown))
        if where and rect.width() > 0:
            painter.setPen(muted)
            painter.drawText(rect, flags, fm.elidedText("  ·  " + where, Qt.TextElideMode.ElideRight, rect.width()))
        painter.restore()


class CommandPalette(QDialog):
    def __init__(self, controller: "CommandsController"):
        super().__init__(controller.ctx.window)
        self.c = controller
        self.setWindowTitle(_("Ricerca dei comandi"))
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.field = QLineEdit(self)
        self.field.setPlaceholderText(_("Cerca un comando…"))
        self.field.textChanged.connect(self.refresh)
        self.field.installEventFilter(self)
        layout.addWidget(self.field)
        self.list = QListWidget(self)
        self.list.itemActivated.connect(self.run_item)
        self.list.itemClicked.connect(self._choose_item)
        self.list.currentRowChanged.connect(lambda _r: self._show_hint())
        self.list.setMinimumHeight(320)
        self.list.setItemDelegate(_CommandDelegate(self.list))
        from foliant.api import theme
        self.setObjectName("commandPalette")
        self.setStyleSheet(f"QDialog#commandPalette {{ border: 1px solid {theme.hex_color('border')}; border-radius: 8px; }}"
                           " QListWidget::item { padding: 4px 6px; }")
        layout.addWidget(self.list)
        # cosa fa Invio sulla voce evidenziata (o perché non è disponibile)
        self.hint = QLabel(self)
        self.hint.setProperty("muted", True)
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.actions: list[QAction] = []
        self._chosen = -1          # riga scelta: frecce, clic, primo Invio o corrispondenza esatta

    def refresh(self, *_a) -> None:
        self.list.clear()
        query = self.field.text()
        self.actions = self.c.search(query)[:200]
        for n, act in enumerate(self.actions):
            item = QListWidgetItem(self.c.display_text(act))
            item.setData(PARTS_ROLE, self.c.display_parts(act))
            if not act.isEnabled():
                item.setForeground(self.palette().placeholderText())
                tip = _("Non disponibile: {r}").format(r=self.c.reason(act))
                description = self.c.description(act)
                item.setToolTip(tip + ("\n" + description if description else ""))
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.list.addItem(item)
        self._chosen = 0 if self.c.is_certain(query, self.actions) else -1
        if not self.actions:
            item = QListWidgetItem(_("Nessun comando trovato"))
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(item)
        elif self.list.count():
            self.list.setCurrentRow(0)
        self._show_hint()

    def _current_action(self) -> QAction | None:
        row = self.list.currentRow()
        return self.actions[row] if 0 <= row < len(self.actions) else None

    def _show_hint(self, pending: bool = False) -> None:
        act = self._current_action()
        if act is None:
            self.hint.clear()
            return
        name = self.c.label(act)
        where = self.c.place(act).context()
        what = f"{name} ({where})" if where else name
        if not act.isEnabled():
            text = _("«{c}» non è disponibile: {r}").format(c=what, r=self.c.reason(act))
            target = self.c.target_mode(act)
            mode = self.c.ctx.mode(target) if target is not None and target != self.c.ctx.current_mode() else None
            if mode is not None:
                text += " " + _("Invio apre la scheda {t}.").format(t=mode.title)
        elif pending:
            text = _("Premi di nuovo Invio per eseguire «{c}»").format(c=what)
        elif self.list.currentRow() == self._chosen:
            text = _("Invio esegue «{c}»").format(c=what)
        else:
            text = _("Invio sceglie «{c}», un secondo Invio lo esegue").format(c=what)
        self.hint.setText(text)

    def _choose_item(self, item) -> None:
        """Clic su una voce: è quella scelta (Invio la esegue)."""
        self._chosen = self.list.row(item)
        self._show_hint()

    def run_item(self, item) -> None:
        i = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(i, int) or i >= len(self.actions):
            return
        act = self.actions[i]
        if act.isEnabled():
            self.hide()
            QTimer.singleShot(0, lambda a=act: self.c.execute(a))
            return
        target = self.c.target_mode(act)
        if target is not None and target != self.c.ctx.current_mode():
            # comando di un'altra scheda non disponibile: si va alla scheda e si dice perché
            self.hide()
            self.c.execute(act)
            return
        self._chosen = self.list.row(item)
        self._show_hint()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.field and ev.type() == QEvent.Type.KeyPress:
            if ev.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.actions:
                    row = self.list.currentRow() + (1 if ev.key() == Qt.Key.Key_Down else -1)
                    row = max(0, min(len(self.actions) - 1, row))
                    self._chosen = row
                    self.list.setCurrentRow(row)
                    self._show_hint()
                return True
            if ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                act = self._current_action()
                if item is not None and act is not None:
                    if self.list.currentRow() == self._chosen or not act.isEnabled():
                        self.run_item(item)        # una voce disattivata non modifica nulla
                    else:
                        # più comandi possibili: il primo Invio sceglie la voce, il secondo la esegue
                        self._chosen = self.list.currentRow()
                        self._show_hint(pending=True)
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
        # comandi della sola ricerca: le schede («Vai alla scheda …», «File») e il tema dell'interfaccia
        self._tab_actions: dict[str, QAction] = {}
        self._fixed: dict[int, Place] = {}          # posto dei comandi che non stanno in una barra
        self.act_file_tab = self._builtin(_("Vai alla scheda {t}").format(t=_("File")), "panel-top",
                                          lambda: ctx.show_file_tab(), aliases=[_("File")])
        options = self._file_place("opzioni")
        self.theme_actions: dict[str, QAction] = {}
        for key, text, icon_name in (("light", _("Tema: Chiaro"), "sun"), ("dark", _("Tema: Scuro"), "moon"),
                                     ("system", _("Tema: Come il sistema"), "monitor")):
            act = self._builtin(text, icon_name, lambda _c=False, k=key: self.ctx.window.set_theme(k))
            self._fixed[id(act)] = options
            self.theme_actions[key] = act
        self._places_cache: dict[int, list[Place]] | None = None
        # le scorciatoie personalizzate si applicano quando tutti i moduli hanno registrato le azioni
        QTimer.singleShot(0, self.apply_saved)

    def _builtin(self, text: str, icon_name: str, slot, aliases: list[str] | None = None) -> QAction:
        """Comando aggiunto dalla ricerca: non modifica il documento (`commandSafe`), quindi Invio lo
        esegue anche quando un altro comando ha lo stesso nome."""
        act = self.ctx.action(text, icon_name, slot, requires_document=False)
        act.setProperty("commandSafe", True)
        if aliases:
            act.setProperty("commandAliases", aliases)
        return act

    def _file_place(self, page: str) -> Place:
        from foliant.ui.backstage import page_title
        return Place(None, _("File"), page_title(page))

    def _ensure_tab_actions(self) -> None:
        """«Vai alla scheda …» per ogni scheda, anche per quelle aggiunte dopo il viewer."""
        for mode in getattr(self.ctx.window, "modes", {}).values():
            if mode.id in self._tab_actions:
                continue
            act = self._builtin(_("Vai alla scheda {t}").format(t=mode.title), mode.icon_name,
                                lambda _c=False, m=mode.id: self.ctx.set_mode(m), aliases=[mode.title])
            self._tab_actions[mode.id] = act

    # ---------------------------------------------------------------- comandi
    def _excluded(self) -> set[int]:
        """Azioni che non sono comandi: «mostra/nascondi» che Qt crea per ogni barra e pannello
        agganciabile (per le schede hanno il nome della scheda e ne prenderebbero il posto)."""
        w = self.ctx.window
        return ({id(b.toggleViewAction()) for b in w.findChildren(QToolBar)}
                | {id(d.toggleViewAction()) for d in w.findChildren(QDockWidget)})

    def commands(self) -> list[QAction]:
        """Tutte le azioni con un nome (finestra e moduli), senza duplicati di testo+scorciatoia+posto."""
        self._ensure_tab_actions()
        places = self.places()
        excluded = self._excluded()
        seen: set[tuple[str, str, str]] = set()
        out: list[QAction] = []
        for act in self.ctx.window.findChildren(QAction):
            text = act.text().replace("&", "").strip()
            if not text or act.isSeparator() or act.menu() is not None or id(act) in excluded:
                continue
            where = places.get(id(act))
            key = (text, act.shortcut().toString(), where[0].context() if where else "")
            if key in seen:
                continue
            seen.add(key)
            out.append(act)
            self._defaults.setdefault(id(act), act.shortcut().toString())
        return out

    # ------------------------------------------------------------------ posti
    def places(self) -> dict[int, list[Place]]:
        """id(azione) → posti in cui compare: barre delle schede (con gruppi e menu) e scheda File."""
        w = self.ctx.window
        out: dict[int, list[Place]] = {}
        modes = sorted(getattr(w, "modes", {}).values(), key=lambda m: (m.order, m.title))
        for mode in modes:
            bar = mode.toolbar
            groups = bar.groups() if hasattr(bar, "groups") else [("", bar.actions())]
            for title, acts in groups:
                base = Place(mode.id, mode.title, title)
                for act in acts:
                    self._add_bar_item(bar, act, base, out)
        backstage = getattr(w, "backstage", None)
        if backstage is not None:
            from foliant.ui.backstage import PAGE_IDS
            for pid in PAGE_IDS:
                cmd = backstage.pages[pid].kind == "cmd"
                base = Place(None, _("File"), "" if cmd else self._file_place(pid).group)
                for act in backstage.actions_of(pid):
                    self._add(act, base, (), out)
        for key, place in self._fixed.items():
            out.setdefault(key, []).append(place)
        self._places_cache = out
        return out

    def _add_bar_item(self, bar, act: QAction, base: Place, out: dict) -> None:
        if act.isSeparator():
            return
        widget = bar.widgetForAction(act)
        if isinstance(act, QWidgetAction) or (widget is not None and not (
                isinstance(widget, QToolButton) and widget.defaultAction() is act)):
            # widget di un modulo (pulsante con menu, gruppo di pulsanti): le azioni dei suoi pulsanti
            buttons = ([widget] if isinstance(widget, QToolButton) else []) + \
                (widget.findChildren(QToolButton) if widget is not None else [])
            for b in buttons:
                if b.defaultAction() is not None:
                    self._add(b.defaultAction(), base, (), out)
                elif b.menu() is not None:
                    self._walk_menu(b.menu(), base, (_clean(b.text()),), out, set())
            return
        self._add(act, base, (), out)

    def _add(self, act: QAction, base: Place, path: tuple[str, ...], out: dict) -> None:
        if act.isSeparator():
            return
        place = replace(base, menu=path)
        if place not in out.setdefault(id(act), []):
            out[id(act)].append(place)
        if act.menu() is not None:
            self._walk_menu(act.menu(), base, path + (_clean(act.text()),), out, set())

    def _walk_menu(self, menu: QMenu, base: Place, path: tuple[str, ...], out: dict, seen: set) -> None:
        if id(menu) in seen:
            return
        seen.add(id(menu))
        for a in menu.actions():
            if a.isSeparator():
                continue
            sub = a.menu()
            if sub is not None:
                self._walk_menu(sub, base, path + (_clean(a.text()) or _clean(sub.title()),), out, seen)
            else:
                self._add(a, base, path, out)

    def place(self, act: QAction) -> Place:
        """Il posto mostrato accanto al comando: nella scheda attiva se c'è anche lì, altrimenti il primo."""
        places = (self._places_cache if self._places_cache is not None else self.places()).get(id(act), [])
        current = self.ctx.current_mode()
        for p in places:
            if p.mode_id == current:
                return p
        return places[0] if places else Place()

    def label(self, act: QAction) -> str:
        """Nome del comando con i menu che lo contengono: «Inserisci › Pagina vuota…»."""
        return SEP.join(self.place(act).menu + (_clean(act.text()),))

    def display_parts(self, act: QAction) -> tuple[str, str, str, bool]:
        """(nome con i menu, scheda e gruppo, scorciatoia, disattivato) di una voce della ricerca."""
        where = self.place(act).context()
        if not act.isEnabled():
            where = (where + "  —  " if where else "") + _("non disponibile")
        return (self.label(act), where, act.shortcut().toString(QKeySequence.SequenceFormat.NativeText),
                not act.isEnabled())

    def display_text(self, act: QAction) -> str:
        name, where, keys, _disabled = self.display_parts(act)
        text = "  ·  ".join(p for p in (name, where) if p)
        return text + (f"    {keys}" if keys else "")

    def description(self, act: QAction) -> str:
        """Descrizione del comando (il suggerimento, se dice qualcosa di più del nome)."""
        tip, text = act.toolTip().replace("&", ""), _clean(act.text())
        return tip if tip and tip != text and not tip.startswith(text) else ""

    def _scoped(self) -> dict[int, str]:
        """id(azione) → scheda in cui valgono le sue scorciatoie (Mode.scope_shortcuts, ctx.action(mode=…))."""
        return {id(a): mode_id for a, _seqs, mode_id in getattr(self.ctx.window, "_mode_shortcuts", [])}

    def scoped_mode(self, act: QAction) -> str | None:
        return self._scoped().get(id(act))

    def target_mode(self, act: QAction) -> str | None:
        """Scheda in cui eseguire il comando: quella a cui è legato, altrimenti quella che lo mostra
        (la scheda attiva, se lo mostra anche lei); None per i comandi di tutte le schede."""
        scoped = self.scoped_mode(act)
        if scoped is not None:
            return scoped
        modes = [p.mode_id for p in self.places().get(id(act), []) if p.mode_id is not None]
        if not modes or self.ctx.current_mode() in modes:
            return None
        return modes[0]

    def _matches(self, query: str, act: QAction, words: list[str]) -> tuple[int, bool] | None:
        """(punteggio, corrispondenza esatta) del comando per `query`; None se non corrisponde."""
        name = act.text()
        aliases = act.property("commandAliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if words and (words == _words(name) or any(words == _words(a) for a in aliases)):
            return -1, True
        s = match_score(query, name)
        if s is not None:
            return s, False
        scores = [x for x in (match_score(query, a) for a in aliases) if x is not None]
        if scores:
            return min(scores) + 50, False
        place = self.place(act)
        if place.menu:                                    # «Inserisci › Pagina vuota…»
            s = match_score(query, " ".join(place.menu + (name,)))
            if s is not None:
                return s + 100, False
        if place.tab or place.group:                      # «Pagine Inserisci e dividi …»
            s = match_score(query, " ".join((place.tab, place.group) + place.menu + (name,)))
            if s is not None:
                return s + 200, False
        s = match_score(query, act.toolTip())
        return (s + 500, False) if s is not None else None

    def search(self, query: str) -> list[QAction]:
        acts = self.commands()            # aggiorna anche i posti
        words = _words(query)
        current = self.ctx.current_mode()
        scoped = self._scoped()
        scored = []
        for act in acts:
            m = self._matches(query, act, words)
            if m is None:
                continue
            s, exact = m
            safe = bool(act.property("commandSafe"))
            mode = scoped.get(id(act)) or self.place(act).mode_id
            elsewhere = mode is not None and mode != current
            # prima i comandi disponibili; poi le corrispondenze esatte (prima le schede e i temi), i nomi
            # che iniziano con la parola cercata, i comandi della scheda attiva e quelli senza scheda
            scored.append(((not act.isEnabled(), not exact, not (exact and safe), s // 100, elsewhere, s,
                            act.text().casefold()), act))
        scored.sort(key=lambda t: t[0])
        return [t[1] for t in scored]

    def is_certain(self, query: str, results: list[QAction]) -> bool:
        """Il primo risultato è proprio quello scritto: corrisponde esattamente al nome (o alla scheda) ed
        è l'unico così, oppure è un comando che non modifica il documento (schede, temi) e corrisponde
        esattamente o è l'unico possibile."""
        words = _words(query)
        if not results or not words or not results[0].isEnabled():
            return False

        def exact(act: QAction) -> bool:
            m = self._matches(query, act, words)
            return m is not None and m[1]

        top = results[0]
        safe = bool(top.property("commandSafe"))
        if safe and not any(a.isEnabled() for a in results[1:]):
            return True                   # l'unico comando possibile, e non tocca il documento
        if not exact(top):
            return False
        return safe or not any(a.isEnabled() and exact(a) for a in results[1:])

    def reason(self, act: QAction) -> str:
        custom = act.property("disabledReason")
        if isinstance(custom, str) and custom:
            return custom
        if self.ctx.current_view() is None:
            return _("nessun documento aperto")
        return _("non si applica al documento o alla selezione attuali")

    def execute(self, act: QAction) -> bool:
        """Esegue il comando passando prima alla sua scheda (se è di un'altra)."""
        target = self.target_mode(act)
        if target is not None and target != self.ctx.current_mode():
            self.ctx.set_mode(target)
        if not act.isEnabled():
            self.ctx.status(_("«{c}» non è disponibile: {r}").format(c=self.label(act), r=self.reason(act)), 8000)
            return False
        act.trigger()
        return True

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
                self.execute(act)
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
