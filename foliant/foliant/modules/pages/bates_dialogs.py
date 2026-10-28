"""Dialogo della numerazione Bates su più file (PAG-35).

`BatesFilesDialog` è costruibile da solo (per i test) e offre `options()`;
`open_bates_files_dialog(ctx, view, selected)` lo mostra, esegue la numerazione
dei file con `ctx.run_task` e applica quella del documento aperto con
`view.document.change()` (annullabile, il file su disco non viene toccato).
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from foliant.api import ENGINE_LOCK, OperationCancelled, _, icon, theme

from . import bates, stamp
from .engine import MM, PageOpError

TRANSLATIONS = {
    "Numerazione Bates su più file": "Bates numbering on multiple files",
    "File da numerare (trascina per cambiare l'ordine)": "Files to number (drag to change the order)",
    "File": "File",
    "Pagine": "Pages",
    "Primo": "First",
    "Ultimo": "Last",
    "Aggiungi file…": "Add files…",
    "Rimuovi": "Remove",
    "Sposta su": "Move up",
    "Sposta giù": "Move down",
    "Scegli i PDF da numerare": "Choose the PDFs to number",
    "(documento aperto)": "(open document)",
    "saltato": "skipped",
    "protetto": "protected",
    "Codice": "Code",
    "Prefisso:": "Prefix:",
    "Suffisso:": "Suffix:",
    "Primo numero:": "First number:",
    "Cifre:": "Digits:",
    "Numerazione:": "Numbering:",
    "Continua tra i file": "Continuous across files",
    "Ricomincia per documento": "Restart for each document",
    "Aspetto": "Appearance",
    "Posizione:": "Position:",
    "In alto a sinistra": "Top left",
    "In alto al centro": "Top center",
    "In alto a destra": "Top right",
    "In basso a sinistra": "Bottom left",
    "In basso al centro": "Bottom center",
    "In basso a destra": "Bottom right",
    "Margine:": "Margin:",
    "Carattere:": "Font:",
    "Dimensione:": "Size:",
    "Sostituisci una numerazione Bates di Foliant già presente": "Replace existing Foliant Bates numbering",
    "Destinazione": "Destination",
    "Salva copie nella cartella": "Save copies in the folder",
    "Sovrascrivi i file originali": "Overwrite the original files",
    "Sfoglia…": "Browse…",
    "Aggiungi l'intervallo Bates al nome": "Add the Bates range to the name",
    "Registro CSV dei numeri assegnati": "CSV log of the assigned numbers",
    "Scegli la cartella di destinazione": "Choose the destination folder",
    "Salva registro": "Save log",
    "CSV (*.csv)": "CSV (*.csv)",
    "Usa {n} cifre": "Use {n} digits",
    "Applica": "Apply",
    "Password per «{n}»": "Password for “{n}”",
    "«{n}» è protetto. Password (lascia vuoto per saltarlo):": "“{n}” is protected. Password (leave empty to skip it):",
    "Già numerati con Bates di Foliant: {f}. Attiva «Sostituisci» per procedere.":
        "Already numbered with Foliant Bates: {f}. Enable “Replace” to proceed.",
    "Indica la cartella in cui salvare le copie.": "Choose the folder where the copies will be saved.",
    "Indica il file del registro.": "Choose the log file.",
    "I file originali verranno sovrascritti ({n}). Continuare?": "The original files will be overwritten ({n}). Continue?",
    "Sovrascrivere i file?": "Overwrite the files?",
    "Numerazione Bates in corso…": "Bates numbering in progress…",
    "Numerazione Bates": "Bates numbering",
    "Impossibile applicare la numerazione Bates": "Cannot apply the Bates numbering",
    "{n}: da {a} a {b}": "{n}: from {a} to {b}",
    "File saltati:": "Skipped files:",
    "Errori:": "Errors:",
    "Operazione interrotta. File non completati: {f}. Per riprendere usa il numero iniziale {n}.":
        "Operation interrupted. Files not completed: {f}. To resume, use starting number {n}.",
    "Registro: {p}": "Log: {p}",
    "Prossimo numero: {n}": "Next number: {n}",
    "Sostituire la numerazione esistente?": "Replace the existing numbering?",
    "{m}\n\nSostituirla con la nuova numerazione?": "{m}\n\nReplace it with the new numbering?",
}

_POSITION_LABELS = (
    ("In alto a sinistra", "top-left"), ("In alto al centro", "top-center"), ("In alto a destra", "top-right"),
    ("In basso a sinistra", "bottom-left"), ("In basso al centro", "bottom-center"),
    ("In basso a destra", "bottom-right"),
)

CURRENT_KEY = "\x00current"
ROLE_KEY = Qt.ItemDataRole.UserRole


class _FileInfo:
    __slots__ = ("has_bates", "label", "page_count", "password", "path", "protected")

    def __init__(self, path: str, label: str, page_count: int | None, has_bates: bool = False,
                 protected: bool = False, password: str | None = None):
        self.path, self.label, self.page_count = path, label, page_count
        self.has_bates, self.protected, self.password = has_bates, protected, password


def inspect_file(path: str, password: str | None = None) -> _FileInfo:
    """Pagine, protezione e presenza di Bates di Foliant (lettura breve, tiene ENGINE_LOCK)."""
    info = _FileInfo(path, Path(path).name, None)
    try:
        data = Path(path).read_bytes()
    except OSError:
        return info
    with ENGINE_LOCK:
        try:
            doc = pymupdf.open(stream=data, filetype="pdf")
        except Exception:
            return info
        try:
            if doc.needs_pass:
                info.protected = True
                if not password or not doc.authenticate(password):
                    return info
                info.password = password
            info.page_count = doc.page_count or None
            info.has_bates = bates.has_foliant_bates(doc)
        finally:
            doc.close()
    return info


def _spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    if suffix:
        s.setSuffix(suffix)
    return s


class BatesFilesDialog(QDialog):
    """Elenco ordinato dei file, parametri del codice e anteprima primo/ultimo per file."""

    def __init__(self, parent: QWidget | None = None, *, current: _FileInfo | None = None,
                 paths: list[str] | None = None, ask_paths=None, ask_folder=None, ask_log=None,
                 ask_password=None, folder: str = ""):
        super().__init__(parent)
        self.setWindowTitle(_("Numerazione Bates su più file"))
        self.setMinimumWidth(640)
        self._ask_paths = ask_paths
        self._ask_folder = ask_folder
        self._ask_log = ask_log
        self._ask_password = ask_password
        self._infos: dict[str, _FileInfo] = {}
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        lay.addWidget(QLabel(_("File da numerare (trascina per cambiare l'ordine)")))
        row = QHBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([_("File"), _("Pagine"), _("Primo"), _("Ultimo")])
        self.tree.setRootIsDecorated(False)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setMinimumHeight(150)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        row.addWidget(self.tree, 1)
        btns = QVBoxLayout()
        self.add_btn = self._tool("plus", _("Aggiungi file…"), self._add)
        self.remove_btn = self._tool("trash-2", _("Rimuovi"), self._remove)
        self.up_btn = self._tool("arrow-up", _("Sposta su"), lambda: self.move_selected(-1))
        self.down_btn = self._tool("arrow-down", _("Sposta giù"), lambda: self.move_selected(1))
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        row.addLayout(btns)
        lay.addLayout(row)

        cols = QHBoxLayout()
        code = QGroupBox(_("Codice"))
        cf = QFormLayout(code)
        self.prefix = QLineEdit()
        self.suffix = QLineEdit()
        self.start = _spin(0, 999_999_999, 1)
        self.digits = _spin(bates.DIGITS_MIN, bates.DIGITS_MAX, 6)
        self.mode = QComboBox()
        self.mode.addItem(_("Continua tra i file"), True)
        self.mode.addItem(_("Ricomincia per documento"), False)
        cf.addRow(_("Prefisso:"), self.prefix)
        cf.addRow(_("Suffisso:"), self.suffix)
        cf.addRow(_("Primo numero:"), self.start)
        cf.addRow(_("Cifre:"), self.digits)
        cf.addRow(_("Numerazione:"), self.mode)
        cols.addWidget(code, 1)
        look = QGroupBox(_("Aspetto"))
        lf = QFormLayout(look)
        self.position = QComboBox()
        for text, value in _POSITION_LABELS:
            self.position.addItem(_(text), value)
        self.position.setCurrentIndex(self.position.findData("bottom-right"))
        self.margin = _spin(0, 200, 12, " mm")
        self.font = QComboBox()
        for key, label in stamp.FONTS.items():
            self.font.addItem(label, key)
        self.font.setCurrentIndex(max(0, self.font.findData("cour")))
        self.font_size = _spin(4, 72, 10, " pt")
        lf.addRow(_("Posizione:"), self.position)
        lf.addRow(_("Margine:"), self.margin)
        lf.addRow(_("Carattere:"), self.font)
        lf.addRow(_("Dimensione:"), self.font_size)
        cols.addWidget(look, 1)
        lay.addLayout(cols)

        self.replace = QCheckBox(_("Sostituisci una numerazione Bates di Foliant già presente"))
        lay.addWidget(self.replace)

        dest = QGroupBox(_("Destinazione"))
        dl = QVBoxLayout(dest)
        self.copies_radio = QRadioButton(_("Salva copie nella cartella"))
        self.overwrite_radio = QRadioButton(_("Sovrascrivi i file originali"))
        self.copies_radio.setChecked(True)
        frow = QHBoxLayout()
        self.folder_edit = QLineEdit(folder)
        self.folder_btn = QPushButton(_("Sfoglia…"))
        self.folder_btn.setIcon(icon("folder-open"))
        self.folder_btn.clicked.connect(self._browse_folder)
        frow.addWidget(self.copies_radio)
        frow.addWidget(self.folder_edit, 1)
        frow.addWidget(self.folder_btn)
        dl.addLayout(frow)
        trow = QHBoxLayout()
        self.name_check = QCheckBox(_("Aggiungi l'intervallo Bates al nome"))
        self.name_check.setChecked(True)
        self.template_edit = QLineEdit(bates.DEFAULT_TEMPLATE)
        trow.addSpacing(24)
        trow.addWidget(self.name_check)
        trow.addWidget(self.template_edit, 1)
        dl.addLayout(trow)
        dl.addWidget(self.overwrite_radio)
        lrow = QHBoxLayout()
        self.log_check = QCheckBox(_("Registro CSV dei numeri assegnati"))
        self.log_edit = QLineEdit()
        self.log_btn = QPushButton(_("Sfoglia…"))
        self.log_btn.clicked.connect(self._browse_log)
        lrow.addWidget(self.log_check)
        lrow.addWidget(self.log_edit, 1)
        lrow.addWidget(self.log_btn)
        dl.addLayout(lrow)
        lay.addWidget(dest)

        wrow = QHBoxLayout()
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.fix_digits_btn = QPushButton()
        self.fix_digits_btn.clicked.connect(self._fix_digits)
        wrow.addWidget(self.warning, 1)
        wrow.addWidget(self.fix_digits_btn)
        lay.addLayout(wrow)

        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = self.box.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Applica"))
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.box.accepted.connect(self.accept)
        self.box.rejected.connect(self.reject)
        lay.addWidget(self.box)

        for sig in (self.prefix.textChanged, self.suffix.textChanged, self.start.valueChanged,
                    self.digits.valueChanged, self.mode.currentIndexChanged, self.replace.toggled,
                    self.copies_radio.toggled, self.name_check.toggled, self.log_check.toggled,
                    self.folder_edit.textChanged, self.log_edit.textChanged):
            sig.connect(self.refresh)
        model = self.tree.model()
        model.rowsInserted.connect(self.refresh)
        model.rowsRemoved.connect(self.refresh)
        model.rowsMoved.connect(self.refresh)
        self.tree.itemSelectionChanged.connect(self._update_buttons)

        if current is not None:
            self._add_info(CURRENT_KEY, current)
        self.add_paths(paths or [])
        self.refresh()

    def _tool(self, name: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setIcon(icon(name))
        b.setToolTip(tip)
        b.clicked.connect(slot)
        return b

    # ------------------------------------------------------------ elenco file
    def keys(self) -> list[str]:
        return [self.tree.topLevelItem(i).data(0, ROLE_KEY) for i in range(self.tree.topLevelItemCount())]

    def _add_info(self, key: str, info: _FileInfo) -> None:
        self._infos[key] = info
        item = QTreeWidgetItem([info.label, "", "", ""])
        item.setData(0, ROLE_KEY, key)
        item.setToolTip(0, info.path)
        item.setIcon(0, icon("file-text"))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        for c in (1, 2, 3):
            item.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.tree.addTopLevelItem(item)

    def _same_as_current(self, path: str) -> bool:
        cur = self._infos.get(CURRENT_KEY)
        if cur is None or not cur.path or not os.path.exists(cur.path):
            return False
        try:
            return os.path.samefile(cur.path, path)
        except OSError:
            return False

    def add_paths(self, paths: list[str]) -> None:
        present = set(self.keys())
        for p in paths:
            p = str(p)
            if p in present or self._same_as_current(p):
                continue
            info = inspect_file(p)
            if info.protected and info.page_count is None and self._ask_password is not None:
                pw = self._ask_password(p)
                if pw:
                    info = inspect_file(p, pw)
            self._add_info(p, info)
            present.add(p)

    def _add(self) -> None:
        if self._ask_paths is not None:
            self.add_paths(self._ask_paths() or [])

    def _remove(self) -> None:
        for item in self.tree.selectedItems():
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))

    def move_selected(self, delta: int) -> None:
        rows = sorted(self.tree.indexOfTopLevelItem(it) for it in self.tree.selectedItems())
        n = self.tree.topLevelItemCount()
        if not rows or (delta < 0 and rows[0] == 0) or (delta > 0 and rows[-1] == n - 1):
            return
        for r in (rows if delta < 0 else reversed(rows)):
            item = self.tree.takeTopLevelItem(r)
            self.tree.insertTopLevelItem(r + delta, item)
        self.tree.clearSelection()
        for r in rows:
            self.tree.topLevelItem(r + delta).setSelected(True)
        self.refresh()

    def _update_buttons(self) -> None:
        sel = bool(self.tree.selectedItems())
        for b in (self.remove_btn, self.up_btn, self.down_btn):
            b.setEnabled(sel)

    def _browse_folder(self) -> None:
        if self._ask_folder is not None:
            d = self._ask_folder()
            if d:
                self.folder_edit.setText(d)
                self.copies_radio.setChecked(True)

    def _browse_log(self) -> None:
        if self._ask_log is not None:
            p = self._ask_log()
            if p:
                self.log_edit.setText(p)
                self.log_check.setChecked(True)

    # ---------------------------------------------------------------- piano
    def entries(self) -> list[bates.BatesEntry]:
        out = []
        for key in self.keys():
            info = self._infos[key]
            if key == CURRENT_KEY:
                out.append(bates.BatesEntry(info.path or info.label, external=True, page_count=info.page_count,
                                            has_bates=info.has_bates))
            else:
                out.append(bates.BatesEntry(info.path, info.password))
        return out

    def plan(self) -> bates.BatesPlan:
        """Piano dai dati già letti (nessuna riapertura dei file)."""
        entries = []
        for key in self.keys():
            info = self._infos[key]
            entries.append(bates.BatesEntry(info.path or info.label, external=True,
                                            page_count=info.page_count or 0, has_bates=info.has_bates))
        return bates.plan_bates(entries, start=self.start.value(), continuous=bool(self.mode.currentData()))

    def _fix_digits(self) -> None:
        need = getattr(self, "_need_digits", None)
        if need:
            self.digits.setValue(need)

    def refresh(self, *args) -> None:
        plan = self.plan()
        digits = self.digits.value()
        fmt = lambda n: stamp.format_bates(n, prefix=self.prefix.text(), suffix=self.suffix.text(),  # noqa: E731
                                           digits=digits)
        planned = iter(plan.files)
        current = next(planned, None)
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            key = item.data(0, ROLE_KEY)
            info = self._infos[key]
            label = info.label + ("  " + _("(documento aperto)") if key == CURRENT_KEY else "")
            item.setText(0, label)
            if info.page_count:
                item.setText(1, str(info.page_count))
                item.setText(2, fmt(current.first) if current else "")
                item.setText(3, fmt(current.last) if current else "")
                item.setForeground(0, theme.color("text"))
                current = next(planned, None)
            else:
                item.setText(1, "—")
                item.setText(2, _("protetto") if info.protected else _("saltato"))
                item.setText(3, "")
                item.setForeground(0, theme.color("text_muted"))
        problems: list[str] = []
        self._need_digits = None
        try:
            bates.check_digits(plan, digits)
        except bates.BatesDigitsError as e:
            problems.append(str(e))
            self._need_digits = e.required_digits
        existing = [Path(f.path).name for f in plan.files if f.has_bates]
        if existing and not self.replace.isChecked():
            problems.append(_("Già numerati con Bates di Foliant: {f}. Attiva «Sostituisci» per procedere.").format(
                f=", ".join(existing)))
        if self.copies_radio.isChecked() and self._has_files() and not self.folder_edit.text().strip():
            problems.append(_("Indica la cartella in cui salvare le copie."))
        if self.log_check.isChecked() and not self.log_edit.text().strip():
            problems.append(_("Indica il file del registro."))
        self.warning.setText("\n".join(problems))
        self.warning.setVisible(bool(problems))
        self.fix_digits_btn.setVisible(self._need_digits is not None)
        if self._need_digits is not None:
            self.fix_digits_btn.setText(_("Usa {n} cifre").format(n=self._need_digits))
        copies = self.copies_radio.isChecked()
        for w in (self.folder_edit, self.folder_btn, self.name_check):
            w.setEnabled(copies)
        self.template_edit.setEnabled(copies and self.name_check.isChecked())
        self.log_edit.setEnabled(self.log_check.isChecked())
        self.ok_button.setEnabled(bool(plan.files) and not problems)
        self._update_buttons()

    def _has_files(self) -> bool:
        return any(k != CURRENT_KEY for k in self.keys())

    def options(self) -> dict:
        copies = self.copies_radio.isChecked()
        return {
            "entries": self.entries(),
            "prefix": self.prefix.text(),
            "suffix": self.suffix.text(),
            "start": self.start.value(),
            "digits": self.digits.value(),
            "continuous": bool(self.mode.currentData()),
            "position": self.position.currentData(),
            "margin": self.margin.value() * MM,
            "font": self.font.currentData(),
            "font_size": self.font_size.value(),
            "replace": self.replace.isChecked(),
            "out_folder": self.folder_edit.text().strip() if copies else None,
            "name_template": (self.template_edit.text().strip() or bates.DEFAULT_TEMPLATE)
            if copies and self.name_check.isChecked() else None,
            "log_csv": self.log_edit.text().strip() if self.log_check.isChecked() else None,
        }


def _bates_task(progress, entries, kwargs):
    return bates.bates_files(entries, progress=progress, **kwargs)


def _current_info(view) -> _FileInfo | None:
    if view is None:
        return None
    doc = view.document
    with ENGINE_LOCK:
        count = doc.pdf.page_count
        has = bates.has_foliant_bates(doc.pdf)
    path = str(doc.path) if getattr(doc, "path", None) else ""
    return _FileInfo(path, doc.display_name, count, has)


def _summary(report: bates.BatesReport) -> str:
    lines = [_("{n}: da {a} a {b}").format(n=Path(r.path).name, a=r.first_code, b=r.last_code) for r in report.done]
    if report.skipped:
        lines += ["", _("File saltati:")] + [reason for _p, reason in report.skipped]
    if report.errors:
        lines += ["", _("Errori:")] + report.errors
    if report.cancelled or report.remaining:
        lines += ["", _("Operazione interrotta. File non completati: {f}. Per riprendere usa il numero iniziale {n}.")
                  .format(f=", ".join(Path(p).name for p in report.remaining) or "—", n=report.next_number)]
    else:
        lines += ["", _("Prossimo numero: {n}").format(n=report.next_number)]
    if report.log_path:
        lines.append(_("Registro: {p}").format(p=report.log_path))
    return "\n".join(lines)


def open_bates_files_dialog(ctx, view=None, selected=None) -> bool:
    """PAG-35: numerazione Bates su più file (documento aperto preselezionato)."""
    if view is None and ctx is not None:
        try:
            view = ctx.current_view()
        except Exception:
            view = None
    current = _current_info(view)
    folder = str(view.document.path.parent) if view is not None and getattr(view.document, "path", None) else ""

    def ask_password(path: str) -> str | None:
        text, ok = QInputDialog.getText(
            dlg, _("Password per «{n}»").format(n=Path(path).name),
            _("«{n}» è protetto. Password (lascia vuoto per saltarlo):").format(n=Path(path).name),
            QLineEdit.EchoMode.Password)
        return text if ok and text else None

    dlg = BatesFilesDialog(
        view.window() if view is not None else None, current=current, folder=folder,
        ask_paths=lambda: ctx.ask_open_paths(_("Scegli i PDF da numerare")),
        ask_folder=lambda: ctx.ask_directory(_("Scegli la cartella di destinazione")),
        ask_log=lambda: ctx.ask_save_path(_("Salva registro"), "bates.csv", _("CSV (*.csv)"), ".csv"),
        ask_password=ask_password)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    opts = dlg.options()
    entries = opts.pop("entries")
    files_on_disk = [e for e in entries if not e.external]
    if opts["out_folder"] is None and files_on_disk:
        if not ctx.confirm(_("Sovrascrivere i file?"),
                           _("I file originali verranno sovrascritti ({n}). Continuare?").format(n=len(files_on_disk))):
            return False
    style = {k: opts[k] for k in ("position", "margin", "font", "font_size")}
    code = {k: opts[k] for k in ("prefix", "suffix", "digits")}
    report = None
    for _attempt in range(2):
        try:
            report = ctx.run_task(_("Numerazione Bates in corso…"), _bates_task, entries, opts)
            break
        except bates.BatesExistsError as e:
            if _attempt == 0 and ctx.confirm(_("Sostituire la numerazione esistente?"),
                                             _("{m}\n\nSostituirla con la nuova numerazione?").format(m=e)):
                opts["replace"] = True
                continue
            return False
        except OperationCancelled:
            return False
        except PageOpError as e:
            ctx.error(_("Impossibile applicare la numerazione Bates"), str(e))
            return False
        except Exception as e:  # mai chiudere l'applicazione
            ctx.error(_("Impossibile applicare la numerazione Bates"), str(e) or type(e).__name__)
            return False
    if report is None:
        return False
    applied = False
    ext = next((r for r in report.done if r.external), None)
    if ext is not None and view is not None:
        try:
            with view.document.change(_("Numerazione Bates")) as pdf:
                bates.apply_bates(pdf, ext.first, replace=opts["replace"], **code, **style)
            applied = True
        except Exception as e:
            report.errors.append(str(e) or type(e).__name__)
    ctx.info(_("Numerazione Bates"), _summary(report))
    return applied or any(not r.external for r in report.done)
