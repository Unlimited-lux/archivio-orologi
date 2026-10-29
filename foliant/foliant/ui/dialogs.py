"""Finestre di servizio del nucleo: preferenze, elenco scorciatoie, guida rapida."""

from __future__ import annotations

import re

import getpass
import inspect
from typing import Callable

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import locale_defaults as loc
from ..i18n import _, language, pgettext


def author_name(settings: QSettings) -> str:
    """Nome dell'autore per commenti e firme (preferenze, altrimenti utente di sistema)."""
    name = settings.value("author", "") or ""
    if name:
        return str(name)
    try:
        return getpass.getuser()
    except Exception:
        return _("Utente")


def _make_page(factory: Callable, parent: QWidget) -> QWidget:
    """Crea la pagina di un modulo: `factory(parent)` oppure `factory()`."""
    try:
        params = inspect.signature(factory).parameters.values()
        takes_parent = any(p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.VAR_POSITIONAL) for p in params)
    except (TypeError, ValueError):
        takes_parent = True
    try:
        widget = factory(parent) if takes_parent else factory()
        if not isinstance(widget, QWidget):
            raise TypeError(type(widget).__name__)
    except Exception as e:          # una pagina difettosa non impedisce di aprire le preferenze
        widget = QLabel(_("Impossibile mostrare questa pagina: {e}").format(e=e), parent)
        widget.setWordWrap(True)
        return widget
    if isinstance(widget, QDialog):
        widget.setWindowFlags(Qt.WindowType.Widget)      # incorporata come pagina
    return widget


class PreferencesDialog(QDialog):
    """Preferenze: pagina «Generale» del nucleo e pagine dei moduli (`ctx.add_preferences_page`).

    Con «Salva» il nucleo applica le proprie impostazioni e chiama `apply()` sulle pagine dei
    moduli che lo definiscono; le pagine che salvano subito non hanno bisogno di altro.
    """

    def __init__(self, settings: QSettings, parent: QWidget | None = None,
                 pages: list[tuple[str, Callable]] | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(_("Preferenze"))
        self.setMinimumWidth(460)
        self.module_pages: list[QWidget] = []
        form = QFormLayout()
        self.theme = QComboBox(self)
        for key, label in (("system", _("Come il sistema")), ("light", _("Chiaro")), ("dark", _("Scuro"))):
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.value("theme", "system"))))
        form.addRow(_("Tema"), self.theme)
        self.language = QComboBox(self)
        self.language.addItem("Italiano", "it")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(max(0, self.language.findData(settings.value("language", "it"))))
        form.addRow(_("Lingua"), self.language)
        self.author = QLineEdit(author_name(settings), self)
        form.addRow(_("Nome autore (commenti e firme)"), self.author)
        self.view_mode = QComboBox(self)
        for key, label in (("continuous", _("Scorrimento continuo")), ("single", _("Pagina singola")),
                           ("facing", _("Due pagine")), ("cover", _("Due pagine con copertina"))):
            self.view_mode.addItem(label, key)
        self.view_mode.setCurrentIndex(max(0, self.view_mode.findData(settings.value("view/mode", "continuous"))))
        form.addRow(_("Visualizzazione predefinita"), self.view_mode)
        self.zoom = QComboBox(self)
        for key, label in (("width", _("Adatta alla larghezza")), ("page", _("Adatta alla pagina")), ("100", "100%")):
            self.zoom.addItem(label, key)
        self.zoom.setCurrentIndex(max(0, self.zoom.findData(settings.value("view/zoom", "width"))))
        form.addRow(_("Zoom predefinito"), self.zoom)
        from .rulers import unit_names
        self.ruler_unit = QComboBox(self)
        for key, label in unit_names():
            self.ruler_unit.addItem(label, key)
        self.ruler_unit.setCurrentIndex(max(0, self.ruler_unit.findData(settings.value("view/ruler_unit", "mm"))))
        form.addRow(_("Unità dei righelli"), self.ruler_unit)
        self.paper_size = QComboBox(self)
        self.paper_size.addItem(_("Automatico ({v})").format(v=loc.default_paper_size()), loc.AUTO)
        for key in loc.PAPER_SIZES:
            self.paper_size.addItem(key, key)
        self.paper_size.setCurrentIndex(max(0, self.paper_size.findData(settings.value(loc.PAPER_KEY, loc.AUTO))))
        form.addRow(_("Formato carta"), self.paper_size)
        self.measure_unit = QComboBox(self)
        unit_label = {"mm": _("Millimetri"), "in": _("Pollici")}
        self.measure_unit.addItem(_("Automatico ({v})").format(v=unit_label[loc.default_unit()]), loc.AUTO)
        for key in loc.UNITS:
            self.measure_unit.addItem(unit_label[key], key)
        self.measure_unit.setCurrentIndex(max(0, self.measure_unit.findData(settings.value(loc.UNIT_KEY, loc.AUTO))))
        form.addRow(_("Unità di misura"), self.measure_unit)
        note = QLabel(_("La lingua viene applicata al prossimo avvio."))
        note.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Salva"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(pgettext("pulsante", "Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        general = QWidget(self)
        general_lay = QVBoxLayout(general)
        general_lay.setContentsMargins(0, 0, 0, 0)
        general_lay.addLayout(form)
        general_lay.addWidget(note)
        general_lay.addStretch(1)
        lay = QVBoxLayout(self)
        self.sections: QListWidget | None = None
        self.stack: QStackedWidget | None = None
        if pages:
            # sezioni a sinistra (Generale + moduli), pagina scelta a destra
            self.setMinimumWidth(760)
            self.sections = QListWidget(self)
            self.sections.setFixedWidth(180)
            self.stack = QStackedWidget(self)
            self.sections.addItem(_("Generale"))
            self.stack.addWidget(general)
            for title, factory in pages:
                widget = _make_page(factory, self)
                if isinstance(widget, QDialog):
                    widget.finished.connect(lambda _r: self.reject())   # i suoi pulsanti chiudono le preferenze
                self.module_pages.append(widget)
                self.sections.addItem(title)
                self.stack.addWidget(widget)
            self.sections.currentRowChanged.connect(self.stack.setCurrentIndex)
            self.sections.setCurrentRow(0)
            body = QHBoxLayout()
            body.addWidget(self.sections)
            body.addWidget(self.stack, 1)
            lay.addLayout(body, 1)
        else:
            lay.addWidget(general)
        lay.addWidget(buttons)

    def show_page(self, title: str) -> bool:
        """Mostra la sezione con questo titolo (es. «Assistente IA»)."""
        if self.sections is None:
            return False
        for row in range(self.sections.count()):
            if self.sections.item(row).text() == title:
                self.sections.setCurrentRow(row)
                return True
        return False

    def apply(self) -> None:
        s = self.settings
        s.setValue("theme", self.theme.currentData())
        s.setValue("language", self.language.currentData())
        s.setValue("author", self.author.text().strip())
        s.setValue("view/mode", self.view_mode.currentData())
        s.setValue("view/zoom", self.zoom.currentData())
        s.setValue("view/ruler_unit", self.ruler_unit.currentData())
        s.setValue(loc.PAPER_KEY, self.paper_size.currentData())
        s.setValue(loc.UNIT_KEY, self.measure_unit.currentData())
        for widget in self.module_pages:
            apply = getattr(widget, "apply", None)
            if callable(apply):
                try:
                    apply()
                except Exception:       # una pagina difettosa non blocca le altre impostazioni
                    pass


# Nomi dei tasti come sulla tastiera italiana (UX-18).
_KEY_NAMES_IT = {
    "Shift": "Maiusc", "Return": "Invio", "Enter": "Invio", "Del": "Canc", "Ins": "Ins",
    "Home": "Inizio", "End": "Fine", "PgUp": "Pag su", "PgDown": "Pag giù", "Left": "Sinistra",
    "Right": "Destra", "Up": "Su", "Down": "Giù", "Space": "Spazio", "Backspace": "Backspace",
    "Print": "Stampa",
}


def key_text(seq: QKeySequence) -> str:
    """Scorciatoia leggibile nella lingua dell'interfaccia (in italiano: Maiusc, Invio, Pag su…)."""
    text = seq.toString(QKeySequence.SequenceFormat.PortableText)
    if language() != "it":
        # Qt chiama «Return» il tasto Invio: su Windows e Linux si scrive «Enter»
        native = seq.toString(QKeySequence.SequenceFormat.NativeText)
        return re.sub(r"\bReturn\b", "Enter", native)
    out = []
    for chord in text.split(", "):
        keys = chord.split("+")
        if chord.endswith("++"):                       # il tasto «+» stesso
            keys = chord[:-2].split("+") + ["+"]
        out.append("+".join(_KEY_NAMES_IT.get(k, k) for k in keys if k != ""))
    return ", ".join(out)


def collect_shortcuts(window: QWidget) -> list[tuple[str, str, str]]:
    """(azione, scorciatoia, modalità) di tutte le scorciatoie, comprese quelle valide solo in una modalità."""
    rows: dict[tuple[str, str, str], None] = {}
    scoped = {id(a): (seqs, m) for a, seqs, m in getattr(window, "_mode_shortcuts", [])}
    modes = getattr(window, "modes", {})
    for act in window.findChildren(QAction):
        text = act.text().replace("&", "").strip()
        if not text:
            continue
        if id(act) in scoped:
            seqs, mode_id = scoped[id(act)]
            mode = modes.get(mode_id)
            where = mode.title if mode is not None else mode_id
        else:
            seqs, where = act.shortcuts(), _("Tutte")
        for seq in seqs:
            if not seq.isEmpty():
                rows[(text, key_text(seq), where)] = None
    return sorted(rows, key=lambda r: (r[2] != _("Tutte"), r[2].lower(), r[0].lower()))


class ShortcutsDialog(QDialog):
    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setWindowTitle(_("Scorciatoie da tastiera"))
        self.resize(700, 620)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText(_("Cerca un comando o un tasto…"))
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([_("Comando"), _("Scorciatoia"), _("Modalità")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rows = collect_shortcuts(window) + [
            (_("Pagina successiva / precedente"), "N / P", _("Tutte")),
            (_("Scorri di una schermata"), _("Spazio / Maiusc+Spazio"), _("Tutte")),
            (_("Torna a Seleziona testo"), "Esc", _("Tutte")),
        ]
        self.filter.textChanged.connect(self._fill)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.table)
        self._fill("")

    def _fill(self, text: str) -> None:
        t = text.lower().strip()
        rows = [r for r in self.rows if not t or any(t in c.lower() for c in r)]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate(row):
                self.table.setItem(i, col, QTableWidgetItem(value))


HELP_HTML = """
<h2>{title}</h2>
<p>{intro}</p>
<h3>{modes}</h3>
<ul>
<li><b>{read}</b> — {read_d}</li>
<li><b>{others}</b> — {others_d}</li>
</ul>
<h3>{tips}</h3>
<ul>
<li>{tip1}</li>
<li>{tip2}</li>
<li>{tip3}</li>
<li>{tip4}</li>
</ul>
"""


class HelpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(_("Guida rapida"))
        self.resize(620, 520)
        view = QTextBrowser(self)
        view.setOpenExternalLinks(False)
        view.setHtml(HELP_HTML.format(
            title=_("Guida rapida di Foliant"),
            intro=_("Apri un PDF dalla Home o trascinandolo nella finestra. La barra in alto contiene le "
                    "modalità di lavoro: ogni modalità mostra i propri strumenti nella barra sottostante."),
            modes=_("Modalità"),
            read=_("Leggi"),
            read_d=_("navigazione, zoom, ricerca, miniature, segnalibri e stampa."),
            others=_("Commenta, Modifica, Pagine, Moduli, OCR, Converti, Proteggi, Firma, Strumenti, Assistente IA"),
            others_d=_("ciascuna raccoglie le funzioni del proprio ambito."),
            tips=_("Suggerimenti"),
            tip1=_("Ogni modifica si può annullare con Ctrl+Z; il file originale cambia solo quando salvi."),
            tip2=_("Ctrl+? mostra l'elenco completo delle scorciatoie da tastiera."),
            tip3=_("Clic destro sulla pagina per i comandi contestuali."),
            tip4=_("Nelle preferenze (Ctrl+virgola) scegli tema, lingua e nome dell'autore."),
        ))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(_("Chiudi"))
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(view)
        lay.addWidget(buttons)
