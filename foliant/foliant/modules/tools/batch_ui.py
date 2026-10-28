"""Interfaccia delle operazioni in blocco: coda, sequenza di azioni,
destinazione e nomi, esecuzione e rapporto finale."""

from __future__ import annotations

import os
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QPlainTextEdit, QPushButton, QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import OperationCancelled, _, atomic_write, bind_icon, icon, theme

from . import engine as be

STATUS_COLORS = {"ok": "success", "warning": "warning", "error": "danger", "skipped": "text_muted",
                 "cancelled": "text_muted", "not_processed": "text_muted"}


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}".replace(".", ",")
        size /= 1024
    return str(n)


# =============================================================================
# Modulo dei parametri di un'azione
# =============================================================================
class ParamForm(QWidget):
    """Campi generati dai parametri di un'azione."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.widgets: dict[str, QWidget] = {}
        self.step: be.Step | None = None

    def set_step(self, step: be.Step | None) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.widgets.clear()
        self.step = step
        if step is None:
            return
        spec = be.ACTIONS.get(step.action)
        if spec is None:
            return
        values = step.merged_params()
        ok, msg = be.action_available(step.action)
        if not ok:
            warn = QLabel("⚠ " + msg)
            warn.setWordWrap(True)
            warn.setStyleSheet(f"color: {theme.hex_color('danger')}")
            self.form.addRow(warn)
        self.ask_buttons: dict[str, QToolButton] = {}
        for p in spec.params:
            w = self._widget(p, values.get(p.key, p.default))
            self.widgets[p.key] = w
            if p.type == "bool":
                self.form.addRow(w)
                continue
            holder = QWidget()
            row = QHBoxLayout(holder)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(w, 1)
            ask = QToolButton()
            ask.setCheckable(True)
            bind_icon(ask, "message-circle-question")
            ask.setToolTip(_("Chiedi all'avvio"))
            if p.secret and p.type == "password":
                ask.setChecked(True)
                ask.setEnabled(False)
                ask.setToolTip(_("Le password vuote sono sempre chieste all'avvio"))
            else:
                ask.setChecked(p.key in step.ask)
            ask.toggled.connect(lambda on, k=p.key: self._toggle_ask(k, on))
            self.ask_buttons[p.key] = ask
            row.addWidget(ask)
            self.form.addRow(_(p.label), holder)

    def _widget(self, p: be.Param, value):
        if p.type == "bool":
            w = QCheckBox(_(p.label))
            w.setChecked(bool(value))
            w.toggled.connect(self._store)
        elif p.type == "int":
            w = QSpinBox()
            w.setRange(int(p.minimum if p.minimum is not None else -10**9), int(p.maximum if p.maximum is not None
                                                                                else 10**9))
            w.setValue(int(value or 0))
            w.valueChanged.connect(self._store)
        elif p.type == "float":
            w = QDoubleSpinBox()
            w.setRange(float(p.minimum if p.minimum is not None else -1e9),
                       float(p.maximum if p.maximum is not None else 1e9))
            w.setDecimals(1)
            w.setValue(float(value or 0))
            w.valueChanged.connect(self._store)
        elif p.type in ("choice", "position"):
            w = QComboBox()
            for key, label in p.choices:
                w.addItem(_(label), key)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(self._store)
        elif p.type == "text":
            w = QPlainTextEdit(str(value or ""))
            w.setFixedHeight(70)
            w.textChanged.connect(self._store)
        elif p.type == "path":
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(str(value or ""))
            btn = QToolButton()
            bind_icon(btn, "folder-open")
            btn.setToolTip(_("Sfoglia…"))

            def browse(_c=False, e=edit):
                path, _f = QFileDialog.getOpenFileName(self, _("Scegli il file"))
                if path:
                    e.setText(path)
            btn.clicked.connect(browse)
            edit.textChanged.connect(self._store)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            w.edit = edit
        else:   # str, password, color, pages
            w = QLineEdit(str(value or ""))
            if p.type == "password":
                w.setEchoMode(QLineEdit.EchoMode.Password)
            w.textChanged.connect(self._store)
        return w

    def _toggle_ask(self, key: str, on: bool) -> None:
        if self.step is None:
            return
        if on and key not in self.step.ask:
            self.step.ask.append(key)
        elif not on and key in self.step.ask:
            self.step.ask.remove(key)
        self.changed.emit()

    def values(self) -> dict:
        out = {}
        spec = be.ACTIONS.get(self.step.action) if self.step else None
        if spec is None:
            return out
        for p in spec.params:
            w = self.widgets.get(p.key)
            if w is None:
                continue
            if isinstance(w, QCheckBox):
                out[p.key] = w.isChecked()
            elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                out[p.key] = w.value()
            elif isinstance(w, QComboBox):
                out[p.key] = w.currentData()
            elif isinstance(w, QPlainTextEdit):
                out[p.key] = w.toPlainText()
            elif isinstance(w, QLineEdit):
                out[p.key] = w.text()
            elif hasattr(w, "edit"):
                out[p.key] = w.edit.text()
        return out

    def _store(self, *_a) -> None:
        if self.step is not None:
            self.step.params.update(self.values())
            self.changed.emit()


# =============================================================================
# Pannello principale
# =============================================================================
class BatchWidget(QWidget):
    """Elaborazione in blocco, mostrata al posto della vista documento."""

    COLUMNS = ("Nome", "Cartella", "Pagine", "Dimensione", "Stato", "Esito")

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.items: list[be.QueueItem] = []
        self.steps: list[be.Step] = []
        self.report: be.JobReport | None = None
        self.csv_semicolon = False
        self.input_roots: list[str] = []
        self.last_ignored: list[tuple[str, str]] = []
        self.setAcceptDrops(True)
        self.setObjectName("batchPanel")

        bar = QToolBar()
        bar.setIconSize(QSize(18, 18))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        title = QLabel("  " + _("Operazioni in blocco") + "  ")
        title.setStyleSheet("font-weight: 600")
        bar.addWidget(title)
        bar.addSeparator()
        self.act_run = bar.addAction(_("Avvia"))
        bind_icon(self.act_run, "play")
        self.act_run.triggered.connect(lambda: self.run())
        self.act_watch = bar.addAction(_("Cartelle monitorate"))
        bind_icon(self.act_watch, "folder-sync")
        self.act_watch.triggered.connect(self.show_watches)
        self.act_history = bar.addAction(_("Cronologia"))
        bind_icon(self.act_history, "history")
        self.act_history.triggered.connect(self._history_menu)
        bar.addSeparator()
        act_close = bar.addAction(_("Chiudi"))
        bind_icon(act_close, "x")
        act_close.triggered.connect(lambda: ctx.set_central_widget(None))

        # ------------------------------------------------ coda (BAT-01)
        queue_box = QGroupBox(_("File da elaborare"))
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([_(c) for c in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table.setAlternatingRowColors(True)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._queue_menu)
        self.recursive = QCheckBox(_("Includi sottocartelle"))
        self.recursive.setChecked(True)
        qbar = QHBoxLayout()
        for text, name, slot in ((_("Aggiungi file"), "file-plus", self.add_files_dialog),
                                 (_("Aggiungi cartella"), "folder-plus", self.add_folder_dialog),
                                 (_("Documenti aperti"), "files", self.add_open_documents)):
            b = QPushButton(text)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            qbar.addWidget(b)
        qbar.addStretch(1)
        qbar2 = QHBoxLayout()
        qbar2.addWidget(self.recursive)
        qbar2.addStretch(1)
        for tip, name, slot in ((_("Sposta su"), "arrow-up", lambda: self.move_selected(-1)),
                                (_("Sposta giù"), "arrow-down", lambda: self.move_selected(1)),
                                (_("Ordina per nome"), "arrow-down-a-z", self.sort_by_name),
                                (_("Rimuovi"), "minus", self.remove_selected),
                                (_("Svuota la coda"), "trash-2", self.clear_queue)):
            b = QToolButton()
            b.setToolTip(tip)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            qbar2.addWidget(b)
        self.totals = QLabel()
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        ql = QVBoxLayout(queue_box)
        ql.addLayout(qbar)
        ql.addLayout(qbar2)
        ql.addWidget(self.table, 1)
        ql.addWidget(self.totals)
        ql.addWidget(self.notice)

        # ------------------------------------------------ azioni e sequenze (BAT-20)
        steps_box = QGroupBox(_("Operazioni"))
        self.action_combo = QComboBox()
        for spec in be.available_actions():
            ok, _msg = be.action_available(spec.id)
            label = _(spec.label) + ("" if ok else " " + _("(non disponibile)"))
            self.action_combo.addItem(label, spec.id)
        add_step = QPushButton(_("Aggiungi"))
        bind_icon(add_step, "plus")
        add_step.clicked.connect(lambda: self.add_step(self.action_combo.currentData()))
        self.steps_list = QListWidget()
        self.steps_list.setMaximumHeight(130)
        self.steps_list.currentRowChanged.connect(self._step_selected)
        self.steps_list.itemChanged.connect(self._step_toggled)
        srow = QHBoxLayout()
        srow.addWidget(self.action_combo, 1)
        srow.addWidget(add_step)
        sbtn = QHBoxLayout()
        for tip, name, slot in ((_("Sposta su"), "arrow-up", lambda: self.move_step(-1)),
                                (_("Sposta giù"), "arrow-down", lambda: self.move_step(1)),
                                (_("Rimuovi operazione"), "minus", self.remove_step)):
            b = QToolButton()
            b.setToolTip(tip)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            sbtn.addWidget(b)
        sbtn.addStretch(1)
        self.sequence_combo = QComboBox()
        self.sequence_combo.setMinimumWidth(120)
        seqrow = QHBoxLayout()
        seqrow.addWidget(QLabel(_("Sequenze")))
        seqrow.addWidget(self.sequence_combo, 1)
        for tip, name, slot in ((_("Carica sequenza"), "folder-open", self.load_selected_sequence),
                                (_("Salva sequenza"), "save", self.save_sequence_dialog),
                                (_("Duplica sequenza"), "copy", self.duplicate_selected_sequence),
                                (_("Importa sequenza…"), "folder-input", self.import_sequence_dialog),
                                (_("Esporta sequenza…"), "folder-output", self.export_sequence_dialog),
                                (_("Elimina sequenza"), "trash-2", self.delete_selected_sequence)):
            b = QToolButton()
            b.setToolTip(tip)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            seqrow.addWidget(b)
        self.params = ParamForm()
        self.params.changed.connect(self._params_changed)
        pscroll = QScrollArea()
        pscroll.setWidgetResizable(True)
        pscroll.setFrameShape(QFrame.Shape.NoFrame)
        pscroll.setWidget(self.params)
        sl = QVBoxLayout(steps_box)
        sl.addLayout(srow)
        sl.addWidget(self.steps_list)
        sl.addLayout(sbtn)
        sl.addLayout(seqrow)
        sl.addWidget(pscroll, 1)

        # ------------------------------------------------ destinazione e nomi (BAT-02, BAT-03)
        out_box = QGroupBox(_("Destinazione e nomi"))
        self.dest_group = QButtonGroup(self)
        self.dest_folder = QRadioButton(_("Cartella"))
        self.dest_same = QRadioButton(_("Stessa cartella dell'originale"))
        self.dest_sub = QRadioButton(_("Sottocartella accanto all'originale"))
        for b in (self.dest_folder, self.dest_same, self.dest_sub):
            self.dest_group.addButton(b)
            b.toggled.connect(self._settings_changed)
        self.dest_folder.setChecked(True)
        self.dest_path = QLineEdit()
        self.dest_path.setPlaceholderText(_("Scegli la cartella di destinazione"))
        self.dest_path.textChanged.connect(self._settings_changed)
        browse = QToolButton()
        bind_icon(browse, "folder-open")
        browse.clicked.connect(self.choose_destination)
        drow = QHBoxLayout()
        drow.addWidget(self.dest_folder)
        drow.addWidget(self.dest_path, 1)
        drow.addWidget(browse)
        self.subfolder = QLineEdit("elaborati")
        self.subfolder.textChanged.connect(self._settings_changed)
        self.keep_structure = QCheckBox(_("Mantieni la struttura delle sottocartelle"))
        self.pattern = QLineEdit("{nome}")
        self.pattern.textChanged.connect(self._settings_changed)
        ph = QToolButton()
        ph.setText("{ }")
        ph.setToolTip(_("Inserisci un segnaposto"))
        ph.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(ph)
        for key in ("nome", "est", "cartella", "data", "ora", "contatore:3", "pagine", "operazione",
                    "bates_inizio", "bates_fine"):
            menu.addAction("{" + key + "}", lambda k=key: self.pattern.insert("{" + k + "}"))
        ph.setMenu(menu)
        prow = QHBoxLayout()
        prow.addWidget(self.pattern, 1)
        prow.addWidget(ph)
        self.counter_start = QSpinBox()
        self.counter_start.setRange(0, 10**9)
        self.counter_start.setValue(1)
        self.counter_start.valueChanged.connect(self._settings_changed)
        self.conflict = QComboBox()
        self.conflict.addItem(_("Rinomina con (1), (2)…"), "rename")
        self.conflict.addItem(_("Sovrascrivi"), "overwrite")
        self.conflict.addItem(_("Salta"), "skip")
        self.conflict.addItem(_("Chiedi ogni volta"), "ask")
        self.replace_originals = QCheckBox(_("Sostituisci gli originali"))
        self.replace_originals.toggled.connect(self._settings_changed)
        self.backup = QCheckBox(_("Copia di sicurezza .bak"))
        self.backup.setChecked(True)
        self.error_policy = QComboBox()
        self.error_policy.addItem(_("Continua con gli altri file"), "continue")
        self.error_policy.addItem(_("Fermati al primo errore"), "stop")
        self.common_password = QLineEdit()
        self.common_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.common_password.setPlaceholderText(_("Password comune per i file protetti (facoltativa)"))
        self.open_folder = QCheckBox(_("Apri la cartella al termine"))
        self.preview = QTableWidget(0, 2)
        self.preview.setHorizontalHeaderLabels([_("Originale"), _("Risultato")])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview.setMaximumHeight(150)
        form = QFormLayout()
        form.addRow(drow)
        form.addRow(self.dest_same)
        sub = QHBoxLayout()
        sub.addWidget(self.dest_sub)
        sub.addWidget(self.subfolder, 1)
        form.addRow(sub)
        form.addRow(self.keep_structure)
        form.addRow(_("Nome dei file"), prow)
        form.addRow(_("Contatore da"), self.counter_start)
        form.addRow(_("Se il file esiste"), self.conflict)
        rep = QHBoxLayout()
        rep.addWidget(self.replace_originals)
        rep.addWidget(self.backup)
        rep.addStretch(1)
        form.addRow(rep)
        form.addRow(_("In caso di errore"), self.error_policy)
        form.addRow(_("Password"), self.common_password)
        form.addRow(self.open_folder)
        ol = QVBoxLayout(out_box)
        ol.addLayout(form)
        ol.addWidget(QLabel(_("Anteprima dei nomi")))
        ol.addWidget(self.preview)
        self.pattern_error = QLabel()
        self.pattern_error.setStyleSheet(f"color: {theme.hex_color('danger')}")
        ol.addWidget(self.pattern_error)
        ol.addStretch(1)

        # ------------------------------------------------ rapporto (BAT-05)
        self.report_box = QGroupBox(_("Rapporto"))
        self.report_summary = QLabel()
        self.report_summary.setWordWrap(True)
        self.report_filter = QComboBox()
        self.report_filter.addItem(_("Tutti gli esiti"), "")
        for key, label in be.STATUS_LABELS.items():
            self.report_filter.addItem(_(label), key)
        self.report_filter.currentIndexChanged.connect(self._fill_report)
        self.report_table = QTableWidget(0, 4)
        self.report_table.setHorizontalHeaderLabels([_("File"), _("Esito"), _("Messaggio"), _("Risultato")])
        self.report_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.report_table.verticalHeader().setVisible(False)
        self.report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_table.setSortingEnabled(True)
        self.report_table.cellDoubleClicked.connect(self._open_result)
        rbar = QHBoxLayout()
        rbar.addWidget(self.report_filter)
        rbar.addStretch(1)
        for text, name, slot in ((_("Riprova i non riusciti"), "rotate-cw", self.retry_failed),
                                 (_("Esporta rapporto"), "file-output", self.export_report_dialog),
                                 (_("Indice Bates"), "list-ordered", self.export_bates_index_dialog),
                                 (_("Apri cartella"), "folder-open", self.open_destination)):
            b = QPushButton(text)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            rbar.addWidget(b)
        rl = QVBoxLayout(self.report_box)
        rl.addWidget(self.report_summary)
        rl.addLayout(rbar)
        rl.addWidget(self.report_table, 1)
        self.report_box.hide()

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(queue_box)
        for box in (steps_box, out_box):
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setWidget(box)
            top.addWidget(area)
        top.setSizes([520, 460, 420])
        top.setChildrenCollapsible(False)
        main = QSplitter(Qt.Orientation.Vertical)
        main.addWidget(top)
        main.addWidget(self.report_box)
        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 2)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar)
        inner = QVBoxLayout()
        inner.setContentsMargins(8, 8, 8, 8)
        inner.addWidget(main)
        lay.addLayout(inner, 1)
        self.refresh_sequences()
        self._refresh_queue()
        self._settings_changed()

    # ================================================================ coda
    def _base_dir(self) -> str:
        return os.path.dirname(self.ctx.settings().fileName()) or os.path.expanduser("~/.config/foliant")

    def sequences_dir(self) -> str:
        return os.path.join(self._base_dir(), "foliant-sequenze")

    def history_dir(self) -> str:
        return os.path.join(self._base_dir(), "foliant-rapporti")

    def journal_path(self) -> str:
        return os.path.join(self._base_dir(), "foliant-lavoro-in-corso.json")

    def check_interrupted(self) -> bool:
        """Propone di riprendere un lavoro interrotto (chiusura anomala)."""
        found = be.interrupted_job(self.journal_path())
        if found is None:
            return False
        pending, settings = found
        if not self.ctx.confirm(_("Lavoro interrotto"), _("Un'elaborazione in blocco è stata interrotta. "
                                                            "Riprendere con i {n} file non completati?").format(
                n=len(pending))):
            be.discard_journal(self.journal_path())
            return False
        self.items = []
        self.add_paths([p for p in pending if os.path.exists(p)])
        self.steps = list(settings.steps)
        self._refresh_steps(0)
        self.dest_path.setText(settings.destination)
        self.pattern.setText(settings.pattern)
        self.counter_start.setValue(settings.counter_start)
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(settings.conflict)))
        {"folder": self.dest_folder, "same": self.dest_same, "subfolder": self.dest_sub}.get(
            settings.destination_mode, self.dest_folder).setChecked(True)
        be.discard_journal(self.journal_path())
        return True

    def show_watches(self) -> "WatchDialog | None":
        service = getattr(self.ctx.tools_module, "watch_service", None) if hasattr(self.ctx, "tools_module") else None
        if service is None:
            return None
        dlg = WatchDialog(service, self)
        dlg.show()
        self._watch_dialog = dlg
        return dlg

    def _history_menu(self) -> None:
        menu = QMenu(self)
        files = be.list_history(self.history_dir())
        if not files:
            menu.addAction(_("Nessun lavoro precedente")).setEnabled(False)
        for path in files:
            name = os.path.basename(path)[7:-5]
            label = f"{name[6:8]}/{name[4:6]}/{name[0:4]} {name[9:11]}:{name[11:13]}:{name[13:15]}"
            menu.addAction(label, lambda p=path: self.show_history(p))
        menu.exec(self.mapToGlobal(self.rect().topLeft()))

    def show_history(self, path: str) -> bool:
        try:
            self.report = be.load_report(path)
        except Exception as e:
            self.ctx.error(_("Rapporto non leggibile"), str(e))
            return False
        self._fill_report()
        self.report_box.show()
        return True

    def add_paths(self, paths: list[str]) -> tuple[int, list[tuple[str, str]]]:
        """Aggiunge file e cartelle; restituisce (aggiunti, ignorati)."""
        exts = be.INPUT_EXTENSIONS
        if any(s.action == "create_pdf" for s in self.steps):
            exts = be.INPUT_EXTENSIONS + be.CREATE_EXTENSIONS
        new, ignored = be.collect_inputs(paths, self.recursive.isChecked(), exts,
                                         existing=[i.path for i in self.items])
        for p in paths:
            if os.path.isdir(p):
                self.input_roots.append(os.path.realpath(p))
        if new:
            try:
                analyzed = self.ctx.run_task(_("Analisi dei file…"), _analyze_all, new)
            except OperationCancelled:
                analyzed = [be.QueueItem(path=p) for p in new]
            self.items.extend(analyzed)
        self.last_ignored = ignored
        if ignored:
            details = "\n".join(f"{os.path.basename(p)}: {why}" for p, why in ignored[:20])
            self.notice.setText(_("{n} file ignorati.").format(n=len(ignored)))
            self.notice.setToolTip(details)
        else:
            self.notice.setText("")
            self.notice.setToolTip("")
        self._refresh_queue()
        return len(new), ignored

    def add_files_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Aggiungi file"))
        if paths:
            self.add_paths(paths)

    def add_folder_dialog(self) -> None:
        path = self.ctx.ask_directory(_("Aggiungi cartella"))
        if path:
            self.add_paths([path])

    def add_open_documents(self) -> None:
        paths = []
        unsaved = []
        for v in self.ctx.views():
            if v.document.path is not None:
                paths.append(str(v.document.path))
                if v.document.is_modified:
                    unsaved.append(v.document.display_name)
        if unsaved:
            self.ctx.info(_("Modifiche non salvate"), _("Verrà elaborata la versione su disco di: {f}.").format(
                f=", ".join(unsaved)))
        if paths:
            self.add_paths(paths)

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.items[r]
        self._refresh_queue()

    def clear_queue(self) -> None:
        self.items.clear()
        self.input_roots.clear()
        self._refresh_queue()

    def move_selected(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(self.items):
            return
        self.items[row], self.items[target] = self.items[target], self.items[row]
        self._refresh_queue()
        self.table.selectRow(target)

    def sort_by_name(self) -> None:
        self.items.sort(key=lambda it: be.natural_key(it.path))
        self._refresh_queue()

    def set_password(self, row: int, password: str | None) -> None:
        self.items[row].password = password or None
        self._refresh_queue()

    def _double_clicked(self, row: int, col: int) -> None:
        item = self.items[row]
        if item.needs_password:
            pw, ok = QInputDialog.getText(self, _("Password del file"), _("Password per «{n}»:").format(n=item.name),
                                          QLineEdit.EchoMode.Password)
            if ok:
                self.set_password(row, pw)

    def _queue_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        menu = QMenu(self)
        menu.addAction(icon("key"), _("Imposta password…"), lambda: self._double_clicked(row, 0))
        menu.addAction(icon("minus"), _("Rimuovi"), self.remove_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _refresh_queue(self) -> None:
        self.table.setRowCount(len(self.items))
        result_by_path = {r.input: r for r in self.report.results} if self.report else {}
        for row, it in enumerate(self.items):
            res = result_by_path.get(it.path)
            values = (it.name, os.path.dirname(it.path), str(it.pages or "–"), _human_size(it.size),
                      it.status_text, res.status_label if res else "")
            for col, val in enumerate(values):
                cell = QTableWidgetItem(val)
                if col == 1:
                    cell.setToolTip(it.path)
                if col == 4 and (it.damaged or (it.needs_password and not it.password) or it.error):
                    cell.setForeground(theme.color("danger"))
                if col == 5 and res is not None:
                    cell.setForeground(theme.color(STATUS_COLORS.get(res.status, "text")))
                    cell.setToolTip(res.message)
                self.table.setItem(row, col, cell)
        pages = sum(it.pages for it in self.items)
        size = sum(it.size for it in self.items)
        self.totals.setText(_("{n} file · {p} pagine · {s}").format(n=len(self.items), p=pages, s=_human_size(size)))
        self._update_preview()

    # trascinamento di file e cartelle
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    # ================================================================ operazioni
    def add_step(self, action_id: str, params: dict | None = None) -> be.Step:
        step = be.Step(action_id, dict(params or {}))
        self.steps.append(step)
        self._refresh_steps(len(self.steps) - 1)
        return step

    def remove_step(self) -> None:
        row = self.steps_list.currentRow()
        if 0 <= row < len(self.steps):
            del self.steps[row]
            self._refresh_steps(min(row, len(self.steps) - 1))

    def move_step(self, delta: int) -> None:
        row = self.steps_list.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < len(self.steps):
            return
        self.steps[row], self.steps[target] = self.steps[target], self.steps[row]
        self._refresh_steps(target)

    def _refresh_steps(self, select: int = -1) -> None:
        self.steps_list.blockSignals(True)
        self.steps_list.clear()
        for i, s in enumerate(self.steps, 1):
            ok, msg = be.action_available(s.action)
            item = QListWidgetItem(f"{i}. {be.action_label(s.action)}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if s.enabled else Qt.CheckState.Unchecked)
            item.setToolTip(_("Deseleziona per disattivare il passo senza rimuoverlo"))
            if not ok:
                item.setForeground(theme.color("danger"))
                item.setToolTip(msg)
            self.steps_list.addItem(item)
        self.steps_list.blockSignals(False)
        if 0 <= select < len(self.steps):
            self.steps_list.setCurrentRow(select)
            self.params.set_step(self.steps[select])
        else:
            self.params.set_step(None)
        self._suggest_pattern()
        self._update_preview()

    def _step_toggled(self, item) -> None:
        row = self.steps_list.row(item)
        if 0 <= row < len(self.steps):
            self.steps[row].enabled = item.checkState() == Qt.CheckState.Checked

    def _step_selected(self, row: int) -> None:
        self.params.set_step(self.steps[row] if 0 <= row < len(self.steps) else None)

    def _params_changed(self) -> None:
        self._update_preview()

    # ---------------------------------------------------------------- sequenze
    def refresh_sequences(self) -> None:
        self.sequence_combo.clear()
        for name, path in be.list_sequences(self.sequences_dir()):
            self.sequence_combo.addItem(name, path)

    def save_sequence(self, name: str) -> str | None:
        errors = be.validate_sequence(self.steps)
        if not self.steps or errors:
            self.ctx.error(_("Sequenza non salvata"), "\n".join(errors) or _("La sequenza è vuota."))
            return None
        path = be.save_sequence(self.sequences_dir(), name, self.steps, output=be.output_of(self.settings()))
        self.refresh_sequences()
        self.sequence_combo.setCurrentIndex(max(0, self.sequence_combo.findData(path)))
        self.ctx.status(_("Sequenza «{n}» salvata.").format(n=name))
        return path

    def save_sequence_dialog(self) -> None:
        name, ok = QInputDialog.getText(self, _("Salva sequenza"), _("Nome della sequenza:"))
        if ok and name.strip():
            self.save_sequence(name.strip())

    def load_sequence(self, path: str) -> bool:
        try:
            _name, steps = be.load_sequence(path)
        except Exception as e:
            self.ctx.error(_("Sequenza non caricata"), str(e))
            return False
        errors = be.validate_sequence(steps)
        if errors:
            self.ctx.error(_("Sequenza non valida"), "\n".join(errors))
            return False
        self.steps = steps
        self._refresh_steps(0)
        output = be.sequence_output(path)
        if output:
            self.apply_output(output)
        return True

    def apply_output(self, out: dict) -> None:
        """Riapplica l'uscita salvata nella sequenza (destinazione, nomi, conflitti, errori)."""
        {"folder": self.dest_folder, "same": self.dest_same, "subfolder": self.dest_sub}.get(
            out.get("destination_mode", "folder"), self.dest_folder).setChecked(True)
        if out.get("destination") is not None:
            self.dest_path.setText(str(out["destination"]))
        if out.get("subfolder"):
            self.subfolder.setText(str(out["subfolder"]))
        self.keep_structure.setChecked(bool(out.get("keep_structure", False)))
        if out.get("pattern"):
            self.pattern.setText(str(out["pattern"]))
        if out.get("counter_start") is not None:
            self.counter_start.setValue(int(out["counter_start"]))
        self.conflict.setCurrentIndex(max(0, self.conflict.findData(out.get("conflict", "rename"))))
        self.error_policy.setCurrentIndex(max(0, self.error_policy.findData(out.get("error_policy", "continue"))))

    def load_selected_sequence(self) -> None:
        path = self.sequence_combo.currentData()
        if path:
            self.load_sequence(path)

    def duplicate_selected_sequence(self) -> str | None:
        path = self.sequence_combo.currentData()
        if not path:
            return None
        name, steps = be.load_sequence(path)
        copy_name = _("{n} (copia)").format(n=name)
        new = be.save_sequence(self.sequences_dir(), copy_name, steps, output=be.sequence_output(path))
        self.refresh_sequences()
        self.sequence_combo.setCurrentIndex(max(0, self.sequence_combo.findData(new)))
        return new

    def import_sequence(self, path: str) -> str | None:
        try:
            saved, warnings = be.import_sequence(path, self.sequences_dir())
        except Exception as e:
            self.ctx.error(_("Sequenza non importata"), str(e))
            return None
        self.refresh_sequences()
        self.sequence_combo.setCurrentIndex(max(0, self.sequence_combo.findData(saved)))
        self.load_sequence(saved)
        if warnings:
            self.ctx.info(_("Sequenza importata"), "\n".join(warnings))
        return saved

    def import_sequence_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Importa sequenza"), _("Sequenza di Foliant (*.json *.foliantseq)"),
                                        multiple=False)
        if paths:
            self.import_sequence(paths[0])

    def export_sequence(self, path: str) -> bool:
        if not self.steps:
            return False
        name = self.sequence_combo.currentText() or os.path.splitext(os.path.basename(path))[0]
        try:
            be.export_sequence(path, name, self.steps, output=be.output_of(self.settings()))
        except Exception as e:
            self.ctx.error(_("Sequenza non esportata"), str(e))
            return False
        return True

    def export_sequence_dialog(self) -> None:
        name = (self.sequence_combo.currentText() or "sequenza") + ".foliantseq"
        path = self.ctx.ask_save_path(_("Esporta sequenza"), name, _("Sequenza di Foliant (*.foliantseq)"),
                                      ".foliantseq")
        if path:
            self.export_sequence(path)

    def delete_selected_sequence(self) -> None:
        path = self.sequence_combo.currentData()
        if path and self.ctx.confirm(_("Elimina sequenza"), _("Eliminare la sequenza «{n}»?").format(
                n=self.sequence_combo.currentText())):
            be.delete_sequence(path)
            self.refresh_sequences()

    # ================================================================ impostazioni
    def choose_destination(self) -> None:
        path = self.ctx.ask_directory(_("Cartella di destinazione"))
        if path:
            self.dest_path.setText(path)
            self.dest_folder.setChecked(True)

    def settings(self) -> be.JobSettings:
        mode = "folder" if self.dest_folder.isChecked() else ("same" if self.dest_same.isChecked() else "subfolder")
        root = None
        if self.keep_structure.isChecked() and self.input_roots:
            root = os.path.commonpath(self.input_roots) if len(self.input_roots) > 1 else self.input_roots[0]
        pw = self.common_password.text()
        return be.JobSettings(
            steps=list(self.steps), destination_mode=mode, destination=self.dest_path.text().strip(),
            subfolder=self.subfolder.text().strip() or "elaborati", keep_structure=self.keep_structure.isChecked(),
            input_root=root, pattern=self.pattern.text(), counter_start=self.counter_start.value(),
            conflict=self.conflict.currentData(), replace_originals=self.replace_originals.isChecked(),
            backup=self.backup.isChecked(), error_policy=self.error_policy.currentData(),
            passwords=[pw] if pw else [])

    def _suggest_pattern(self) -> None:
        """Modello predefinito: «{nome}» o «{nome}_<suffisso>» nella stessa cartella."""
        current = self.pattern.text()
        if current == "{nome}" or current.startswith("{nome}_") and current.count("{") == 1:
            self.pattern.blockSignals(True)
            self.pattern.setText(be.default_pattern(self.settings()))
            self.pattern.blockSignals(False)

    def _settings_changed(self, *_a) -> None:
        if not hasattr(self, "preview"):   # durante la costruzione
            return
        folder = self.dest_folder.isChecked()
        self.dest_path.setEnabled(folder)
        self.subfolder.setEnabled(self.dest_sub.isChecked())
        self.backup.setEnabled(self.replace_originals.isChecked())
        self.pattern.setEnabled(not self.replace_originals.isChecked())
        if self.sender() in (self.dest_folder, self.dest_same, self.dest_sub):
            self._suggest_pattern()
        self._update_preview()

    def _update_preview(self) -> None:
        if not hasattr(self, "preview"):
            return
        settings = self.settings()
        errors = be.validate_pattern(settings.pattern)
        self.pattern_error.setText("\n".join(errors))
        rows = be.preview_names(self.items, settings, limit=5) if self.items else []
        self.preview.setRowCount(len(rows))
        for r, (src, dst) in enumerate(rows):
            self.preview.setItem(r, 0, QTableWidgetItem(os.path.basename(src)))
            self.preview.setItem(r, 1, QTableWidgetItem(dst))

    # ================================================================ esecuzione
    def launch_requests(self) -> list[tuple[int, be.Param, object]]:
        out = []
        for i, step in enumerate(self.steps):
            spec = be.ACTIONS.get(step.action)
            if not step.enabled or spec is None:
                continue
            for key in step.ask_keys():
                p = next((x for x in spec.params if x.key == key), None)
                if p is not None:
                    out.append((i, p, step.merged_params().get(key)))
        return out

    def run(self, confirm: bool = True) -> be.JobReport | None:
        settings = self.settings()
        requests = self.launch_requests()
        if requests:
            values = ask_launch_parameters(self, requests)
            if values is None:
                return None
            steps = [be.Step(s.action, dict(s.params), list(s.ask), s.enabled) for s in settings.steps]
            for (i, key), value in values.items():
                steps[i].params[key] = value
            settings.steps = steps
        errors, warnings = be.validate_settings(self.items, settings)
        if errors:
            self.ctx.error(_("Impossibile avviare"), "\n".join(errors))
            return None
        if confirm and warnings and not self.ctx.confirm(
                _("Avvisi"), "\n".join(warnings) + "\n\n" + _("Continuare?")):
            return None
        if settings.replace_originals and confirm:
            if not self.ctx.confirm(_("Sostituisci gli originali"),
                                    _("I file originali verranno sovrascritti. Continuare?")):
                return None
            if not self.ctx.confirm(_("Conferma"), _("Confermi la sostituzione di {n} file?").format(
                    n=len(self.items))):
                return None
        if settings.destination_mode == "folder" and not settings.replace_originals:
            try:
                os.makedirs(settings.destination, exist_ok=True)
            except OSError as e:
                self.ctx.error(_("Impossibile avviare"), _("Impossibile creare la cartella: {e}").format(e=e.strerror))
                return None
        be.prepare_bates_series(settings, self._base_dir())
        started = datetime.now()
        if settings.conflict == "ask":
            apply_all = None
            for src, target in be.planned_conflicts(list(self.items), settings, started):
                choice = apply_all
                if choice is None:
                    answer = ask_conflict(self, target)
                    if answer is None:
                        return None
                    choice, remember = answer
                    if remember:
                        apply_all = choice
                settings.conflict_choices[target] = choice
        control = be.JobControl()
        panel = JobControlPanel(control, self)
        panel.show()
        try:
            report = self.ctx.run_task(_("Elaborazione in blocco…"), be.run_job, list(self.items), settings,
                                       started, None, control, self.journal_path())
        except Exception as e:   # il motore non dovrebbe sollevare: sicurezza in più
            self.ctx.error(_("Elaborazione non riuscita"), str(e))
            return None
        finally:
            control.resume()
            panel.close()
            panel.deleteLater()
        try:
            be.save_history(self.history_dir(), report)
            be.record_bates_series(settings, list(self.items), report, self._base_dir())
        except Exception:
            pass
        self.last_items = list(self.items)
        self.report = report
        self.last_settings = settings
        self._fill_report()
        self._refresh_queue()
        self.report_box.show()
        self.ctx.status(report.summary(), 10000)
        if self.open_folder.isChecked():
            self.open_destination()
        return report

    def _fill_report(self) -> None:
        report = self.report
        if report is None:
            return
        self.report_summary.setText((_("Lavoro annullato.") + " " if report.cancelled else "") + report.summary())
        flt = self.report_filter.currentData()
        rows = [r for r in report.results if not flt or r.status == flt]
        self.report_table.setSortingEnabled(False)
        self.report_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            status = QTableWidgetItem(r.status_label)
            status.setForeground(theme.color(STATUS_COLORS.get(r.status, "text")))
            msg = f"{r.code} {r.message}".strip()
            cells = (QTableWidgetItem(os.path.basename(r.input)), status, QTableWidgetItem(msg),
                     QTableWidgetItem(", ".join(os.path.basename(o) for o in r.outputs)))
            cells[0].setToolTip(r.input)
            cells[3].setToolTip("\n".join(r.outputs))
            cells[3].setData(Qt.ItemDataRole.UserRole, r.outputs)
            for c, cell in enumerate(cells):
                self.report_table.setItem(i, c, cell)
        self.report_table.setSortingEnabled(True)

    def _open_result(self, row: int, _col: int) -> None:
        cell = self.report_table.item(row, 3)
        outputs = cell.data(Qt.ItemDataRole.UserRole) if cell else None
        if not outputs:
            return
        path = outputs[0]
        if not os.path.exists(path):
            self.ctx.error(_("File non trovato"), _("Il risultato è stato spostato o eliminato: {p}").format(p=path))
            return
        if path.lower().endswith(".pdf"):
            self.ctx.open_path(path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def retry_failed(self) -> None:
        if self.report is None:
            return
        failed = set(self.report.failed_items())
        if not failed:
            self.ctx.info(_("Riprova"), _("Nessun file da riprovare."))
            return
        self.items = [it for it in self.items if it.path in failed]
        self.report = None
        self.report_box.hide()
        self._refresh_queue()

    def export_report(self, path: str) -> bool:
        if self.report is None:
            return False
        try:
            if path.lower().endswith(".json"):
                data = self.report.to_json().encode("utf-8")
            elif path.lower().endswith(".pdf"):
                data = be.report_pdf(self.report)
            else:
                data = self.report.to_csv(";" if self.csv_semicolon else ",").encode("utf-8")
            atomic_write(path, data)
        except Exception as e:
            self.ctx.error(_("Rapporto non esportato"), str(e))
            return False
        self.ctx.status(_("Rapporto esportato: {p}").format(p=path))
        return True

    def export_bates_index(self, path: str) -> bool:
        settings = getattr(self, "last_settings", None) or self.settings()
        items = getattr(self, "last_items", None) or self.items
        if not any(s.action == "bates" for s in settings.steps):
            self.ctx.info(_("Indice Bates"), _("Il lavoro non contiene una numerazione Bates."))
            return False
        try:
            atomic_write(path, be.bates_index_csv(items, settings).encode("utf-8"))
        except Exception as e:
            self.ctx.error(_("Indice non esportato"), str(e))
            return False
        return True

    def export_bates_index_dialog(self) -> None:
        path = self.ctx.ask_save_path(_("Esporta indice Bates"), "indice_bates.csv", "CSV (*.csv)", ".csv")
        if path:
            self.export_bates_index(path)

    def export_report_dialog(self) -> None:
        if self.report is None:
            return
        name = f"rapporto_{self.report.started:%Y-%m-%d_%H-%M-%S}.csv"
        path = self.ctx.ask_save_path(_("Esporta rapporto"), name, "CSV (*.csv);;JSON (*.json);;PDF (*.pdf)", ".csv")
        if path:
            self.export_report(path)

    def open_destination(self) -> None:
        settings = getattr(self, "last_settings", None) or self.settings()
        target = settings.destination
        if settings.destination_mode != "folder" or settings.replace_originals:
            outputs = [o for r in (self.report.results if self.report else []) for o in r.outputs]
            target = os.path.dirname(outputs[0]) if outputs else ""
        if target and os.path.isdir(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))


def ask_conflict(parent, target: str) -> tuple[str, bool] | None:
    """«Chiedi ogni volta»: (scelta, vale per tutti) per un file già esistente (sostituibile nei test)."""
    from PySide6.QtWidgets import QMessageBox
    box = QMessageBox(parent)
    box.setWindowTitle(_("File già esistente"))
    box.setText(_("«{f}» esiste già nella destinazione.").format(f=os.path.basename(target)))
    box.setInformativeText(target)
    every = QCheckBox(_("Applica a tutti i conflitti"))
    box.setCheckBox(every)
    buttons = {box.addButton(_("Sovrascrivi"), QMessageBox.ButtonRole.AcceptRole): "overwrite",
               box.addButton(_("Rinomina"), QMessageBox.ButtonRole.AcceptRole): "rename",
               box.addButton(_("Salta"), QMessageBox.ButtonRole.AcceptRole): "skip"}
    box.addButton(QMessageBox.StandardButton.Cancel)
    box.exec()
    choice = buttons.get(box.clickedButton())
    return (choice, every.isChecked()) if choice else None


def ask_launch_parameters(parent, requests: list[tuple[int, be.Param, object]]) -> dict | None:
    """Chiede una sola volta, per l'intero lavoro, i parametri «chiedi all'avvio»
    (sostituibile nei test). Restituisce {(indice passo, chiave): valore} o None."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Parametri del lavoro"))
    form = QFormLayout(dlg)
    helper = ParamForm()
    fields = []
    for i, p, value in requests:
        w = helper._widget(p, "" if p.secret else value)
        form.addRow(f"{i + 1}. {_(p.label)}", w)
        fields.append((i, p, w))
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    form.addRow(buttons)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    out = {}
    for i, p, w in fields:
        if isinstance(w, QCheckBox):
            out[(i, p.key)] = w.isChecked()
        elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
            out[(i, p.key)] = w.value()
        elif isinstance(w, QComboBox):
            out[(i, p.key)] = w.currentData()
        elif isinstance(w, QPlainTextEdit):
            out[(i, p.key)] = w.toPlainText()
        elif isinstance(w, QLineEdit):
            out[(i, p.key)] = w.text()
        elif hasattr(w, "edit"):
            out[(i, p.key)] = w.edit.text()
    return out


class WatchDialog(QWidget):
    """Gestione delle cartelle monitorate (BAT-21)."""

    def __init__(self, service, batch: "BatchWidget"):
        super().__init__(batch, Qt.WindowType.Window)
        from PySide6.QtCore import QTimer
        self.service = service
        self.batch = batch
        self.setWindowTitle(_("Cartelle monitorate"))
        self.resize(820, 360)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("Cartella"), _("Operazioni"), _("Destinazione"), _("Stato")])
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.originals = QComboBox()
        self.originals.addItem(_("Lascia gli originali al loro posto"), "keep")
        self.originals.addItem(_("Sposta gli originali in «originali»"), "move")
        self.stable = QSpinBox()
        self.stable.setRange(0, 600)
        self.stable.setValue(5)
        self.stable.setSuffix(" s")
        self.stable.setToolTip(_("Il file viene elaborato quando la sua dimensione non cambia per questo tempo"))
        info = QLabel(_("Le operazioni sono quelle scelte nel pannello «Operazioni». I file in errore vanno nella "
                        "sottocartella «errori»."))
        info.setWordWrap(True)
        buttons = QHBoxLayout()
        for text, name, slot in ((_("Aggiungi cartella…"), "folder-plus", self.add_dialog),
                                 (_("Attiva/disattiva"), "power", self.toggle_selected),
                                 (_("Rimuovi"), "trash-2", self.remove_selected),
                                 (_("Esporta rapporto"), "file-output", self.export_selected)):
            b = QPushButton(text)
            bind_icon(b, name)
            b.clicked.connect(lambda _c=False, s=slot: s())
            buttons.addWidget(b)
        buttons.addStretch(1)
        opts = QHBoxLayout()
        opts.addWidget(self.originals)
        opts.addWidget(QLabel(_("Attesa copia")))
        opts.addWidget(self.stable)
        opts.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(opts)
        lay.addLayout(buttons)
        lay.addWidget(self.table, 1)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        watchers = list(self.service.watchers.values())
        self.table.setRowCount(len(watchers))
        for r, w in enumerate(watchers):
            for c, text in enumerate((w.cfg.folder, w.cfg.label(), w.cfg.destination, w.status_text())):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, w.cfg.id)
                cell.setToolTip(text)
                self.table.setItem(r, c, cell)

    def add_watch(self, folder: str, destination: str) -> list[str]:
        from .watch import WatchConfig
        steps = [be.Step(s.action, dict(s.params)) for s in self.batch.steps]
        exts = be.INPUT_EXTENSIONS + (be.CREATE_EXTENSIONS if any(s.action == "create_pdf" for s in steps) else ())
        cfg = WatchConfig(folder=folder, steps=steps, destination=destination, extensions=exts,
                          originals=self.originals.currentData(), stable_seconds=float(self.stable.value()),
                          pattern=self.batch.pattern.text() or "{nome}", conflict=self.batch.conflict.currentData())
        errors = self.service.add(cfg)
        self.refresh()
        return errors

    def add_dialog(self) -> None:
        ctx = self.batch.ctx
        folder = ctx.ask_directory(_("Cartella da monitorare"))
        if not folder:
            return
        dest = ctx.ask_directory(_("Cartella di destinazione"))
        if not dest:
            return
        errors = self.add_watch(folder, dest)
        if errors:
            ctx.error(_("Configurazione non salvata"), "\n".join(errors))

    def _selected_id(self) -> str | None:
        item = self.table.item(self.table.currentRow(), 0) if self.table.currentRow() >= 0 else None
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def toggle_selected(self) -> None:
        wid = self._selected_id()
        if wid and wid in self.service.watchers:
            self.service.set_enabled(wid, not self.service.watchers[wid].cfg.enabled)
            self.refresh()

    def remove_selected(self) -> None:
        wid = self._selected_id()
        if wid:
            self.service.remove(wid)
            self.refresh()

    def export_selected(self) -> None:
        wid = self._selected_id()
        w = self.service.watchers.get(wid) if wid else None
        if w is None:
            return
        path = self.batch.ctx.ask_save_path(_("Esporta rapporto"), "monitoraggio.csv", "CSV (*.csv)", ".csv")
        if path:
            atomic_write(path, w.report().to_csv().encode("utf-8"))


class JobControlPanel(QWidget):
    """Piccola finestra con Pausa/Riprendi e «Annulla file» durante il lavoro
    (la finestra di avanzamento di run_task offre l'annullamento dell'intero lavoro)."""

    def __init__(self, control: be.JobControl, parent=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.control = control
        self.setWindowTitle(_("Elaborazione in blocco"))
        self.pause_button = QPushButton(_("Pausa"))
        bind_icon(self.pause_button, "pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        skip = QPushButton(_("Annulla file"))
        bind_icon(skip, "skip-forward")
        skip.clicked.connect(control.skip_current)
        lay = QHBoxLayout(self)
        lay.addWidget(self.pause_button)
        lay.addWidget(skip)
        if parent is not None:
            g = parent.window().geometry()
            self.move(g.right() - 320, g.top() + 80)

    def toggle_pause(self) -> None:
        if self.control.paused:
            self.control.resume()
            self.pause_button.setText(_("Pausa"))
            bind_icon(self.pause_button, "pause")
        else:
            self.control.pause()
            self.pause_button.setText(_("Riprendi"))
            bind_icon(self.pause_button, "play")


def _analyze_all(progress, paths: list[str]) -> list[be.QueueItem]:
    out = []
    n = max(1, len(paths))
    for i, p in enumerate(paths):
        progress.update(i / n, _("Analisi di {f}").format(f=os.path.basename(p)))
        out.append(be.analyze_file(p))
    return out
