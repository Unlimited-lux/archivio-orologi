"""Finestre di dialogo del modulo Moduli (separate per poterle simulare nei test)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import _


def parse_pages(text: str, count: int) -> list[int] | None:
    """Intervalli «1-3, 5, 8-» (numeri da 1) → indici; None se non validi."""
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _s, b = part.partition("-")
            try:
                lo = int(a) if a.strip() else 1
                hi = int(b) if b.strip() else count
            except ValueError:
                return None
        else:
            try:
                lo = hi = int(part)
            except ValueError:
                return None
        if lo < 1 or hi > count or lo > hi:
            return None
        out.extend(range(lo - 1, hi))
    return sorted(set(out)) if out else None


def _buttons(dlg: QDialog, ok_text: str | None = None) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dlg)
    if ok_text:
        box.button(QDialogButtonBox.StandardButton.Ok).setText(ok_text)
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    return box


def ask_reset(parent: QWidget, selected: int) -> str | None:
    """'all' o 'selected' (None = annullato)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Reimposta modulo"))
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(_("I campi torneranno ai valori predefiniti (o vuoti). L'operazione si può annullare.")))
    all_ = QRadioButton(_("Tutti i campi"), dlg)
    sel = QRadioButton(_("Campi selezionati ({n})").format(n=selected), dlg)
    all_.setChecked(True)
    sel.setEnabled(selected > 0)
    lay.addWidget(all_)
    lay.addWidget(sel)
    lay.addWidget(_buttons(dlg, _("Reimposta")))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return "selected" if sel.isChecked() else "all"


def ask_flatten(parent: QWidget, page_count: int) -> list[int] | None | bool:
    """Pagine da appiattire: lista di indici, oppure [] per tutte; None se annullato."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Appiattisci modulo"))
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(_("I campi diventano contenuto fisso della pagina e non saranno più compilabili.\n"
                           "I campi firma firmati restano campi.")))
    all_ = QRadioButton(_("Tutte le pagine"), dlg)
    some = QRadioButton(_("Pagine:"), dlg)
    all_.setChecked(True)
    edit = QLineEdit(dlg)
    edit.setPlaceholderText(_("es. 2-3, 5"))
    row = QHBoxLayout()
    row.addWidget(some)
    row.addWidget(edit)
    lay.addWidget(all_)
    lay.addLayout(row)
    err = QLabel(dlg)
    lay.addWidget(err)
    lay.addWidget(_buttons(dlg, _("Appiattisci")))
    while dlg.exec() == QDialog.DialogCode.Accepted:
        if all_.isChecked():
            return list(range(page_count))
        pages = parse_pages(edit.text(), page_count)
        if pages:
            return pages
        err.setText(_("Intervallo di pagine non valido."))
    return None


def ask_copy_pages(parent: QWidget, page_count: int, current: int) -> tuple[list[int], bool] | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Copia su più pagine"))
    lay = QVBoxLayout(dlg)
    group = QButtonGroup(dlg)
    opts = {}
    for key, text in (("all", _("Tutte le pagine")), ("even", _("Pagine pari")), ("odd", _("Pagine dispari")),
                      ("range", _("Pagine:"))):
        rb = QRadioButton(text, dlg)
        group.addButton(rb)
        opts[key] = rb
        lay.addWidget(rb)
    opts["all"].setChecked(True)
    edit = QLineEdit(dlg)
    lay.addWidget(edit)
    shared = QCheckBox(_("Valore condiviso (stesso campo su tutte le pagine)"), dlg)
    shared.setChecked(True)
    lay.addWidget(shared)
    lay.addWidget(_buttons(dlg, _("Copia")))
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    if opts["all"].isChecked():
        pages = list(range(page_count))
    elif opts["even"].isChecked():
        pages = [i for i in range(page_count) if (i + 1) % 2 == 0]
    elif opts["odd"].isChecked():
        pages = [i for i in range(page_count) if (i + 1) % 2 == 1]
    else:
        pages = parse_pages(edit.text(), page_count) or []
    return [p for p in pages if p != current], shared.isChecked()


def ask_csv_row(parent: QWidget, records: list[dict]) -> int | None:
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Scegli la riga da importare"))
    lay = QVBoxLayout(dlg)
    headers = list(records[0].keys()) if records else []
    table = QTableWidget(len(records), len(headers), dlg)
    table.setHorizontalHeaderLabels(headers)
    for r, rec in enumerate(records[:500]):
        for c, h in enumerate(headers):
            table.setItem(r, c, QTableWidgetItem(str(rec.get(h, ""))))
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.selectRow(0)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    lay.addWidget(table)
    lay.addWidget(_buttons(dlg, _("Importa")))
    dlg.resize(640, 360)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return max(0, table.currentRow())


def show_problems(parent: QWidget, problems: list) -> object | None:
    """Elenco cliccabile dei problemi; restituisce il campo scelto (o None)."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Verifica modulo"))
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(_("Problemi trovati: {n}. Fai doppio clic per andare al campo.").format(n=len(problems))))
    lst = QListWidget(dlg)
    for f, why in problems:
        item = QListWidgetItem(f"{f.name} — {why}")
        item.setData(Qt.ItemDataRole.UserRole, f)
        lst.addItem(item)
    chosen = {}

    def pick(item):
        chosen["f"] = item.data(Qt.ItemDataRole.UserRole)
        dlg.accept()

    lst.itemDoubleClicked.connect(pick)
    lay.addWidget(lst)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
    box.rejected.connect(dlg.reject)
    box.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
    lay.addWidget(box)
    dlg.exec()
    return chosen.get("f")


def review_candidates(parent: QWidget, candidates: list) -> list:
    """Elenco delle proposte con casella per accettarle o rifiutarle."""
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Campi riconosciuti"))
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(_("Deseleziona le proposte da scartare. I campi accettati vengono creati in un solo passo annullabile.")))
    lst = QListWidget(dlg)
    for c in candidates:
        kind = _("Casella di controllo") if c.kind == "checkbox" else (_("Testo su più righe") if getattr(c, "multiline", False) else _("Testo"))
        name = c.name or _("(senza nome)")
        item = QListWidgetItem(_("Pagina {p}: {k} «{n}»").format(p=c.page + 1, k=kind, n=name))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked)
        lst.addItem(item)
    lay.addWidget(lst)
    lay.addWidget(_buttons(dlg, _("Crea campi")))
    dlg.resize(520, 420)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return []
    return [c for i, c in enumerate(candidates) if lst.item(i).checkState() == Qt.CheckState.Checked]


def ask_calc_order(parent: QWidget, order: list[str], problems, auto) -> list[str] | None:
    """Elenco riordinabile dei campi calcolati, con segnalazione delle dipendenze fuori ordine."""
    from PySide6.QtWidgets import QAbstractItemView, QPushButton
    dlg = QDialog(parent)
    dlg.setWindowTitle(_("Ordine di calcolo"))
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel(_("Trascina i campi per cambiare l'ordine in cui vengono calcolati.")))
    lst = QListWidget(dlg)
    lst.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    lst.addItems(order)
    lay.addWidget(lst)
    warn = QLabel(dlg)
    warn.setWordWrap(True)
    lay.addWidget(warn)
    fix = QPushButton(_("Ordina per dipendenze"), dlg)
    lay.addWidget(fix)

    def names():
        return [lst.item(i).text() for i in range(lst.count())]

    def update():
        bad = problems(names())
        warn.setText("\n".join(_("«{a}» è calcolato prima di «{b}», da cui dipende.").format(a=a, b=b) for a, b in bad))
        fix.setEnabled(bool(bad))

    def apply_auto():
        lst.clear()
        lst.addItems(auto())
        update()

    fix.clicked.connect(apply_auto)
    lst.model().rowsMoved.connect(lambda *a: update())
    update()
    lay.addWidget(_buttons(dlg))
    dlg.resize(380, 360)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return names()


def ask_text(parent: QWidget, title: str, label: str, default: str = "") -> str | None:
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, title, label, text=default)
    return text.strip() if ok and text.strip() else None
