"""Finestre di servizio del nucleo: preferenze, elenco scorciatoie, guida rapida."""

from __future__ import annotations

import getpass

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel, QLineEdit, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from ..i18n import _


def author_name(settings: QSettings) -> str:
    """Nome dell'autore per commenti e firme (preferenze, altrimenti utente di sistema)."""
    name = settings.value("author", "") or ""
    if name:
        return str(name)
    try:
        return getpass.getuser()
    except Exception:
        return _("Utente")


class PreferencesDialog(QDialog):
    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(_("Preferenze"))
        self.setMinimumWidth(460)
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
        for key, label in (("width", _("Adatta larghezza")), ("page", _("Adatta pagina")), ("100", "100%")):
            self.zoom.addItem(label, key)
        self.zoom.setCurrentIndex(max(0, self.zoom.findData(settings.value("view/zoom", "width"))))
        form.addRow(_("Zoom predefinito"), self.zoom)
        note = QLabel(_("La lingua viene applicata al prossimo avvio."))
        note.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Salva"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)

    def apply(self) -> None:
        s = self.settings
        s.setValue("theme", self.theme.currentData())
        s.setValue("language", self.language.currentData())
        s.setValue("author", self.author.text().strip())
        s.setValue("view/mode", self.view_mode.currentData())
        s.setValue("view/zoom", self.zoom.currentData())


def collect_shortcuts(window: QWidget) -> list[tuple[str, str]]:
    """(azione, scorciatoia) di tutte le azioni con scorciatoia nella finestra."""
    rows: dict[tuple[str, str], None] = {}
    for act in window.findChildren(QAction):
        text = act.text().replace("&", "").strip()
        for seq in act.shortcuts():
            if seq.isEmpty() or not text:
                continue
            rows[(text, seq.toString(QKeySequence.SequenceFormat.NativeText))] = None
    return sorted(rows, key=lambda r: r[0].lower())


class ShortcutsDialog(QDialog):
    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setWindowTitle(_("Scorciatoie da tastiera"))
        self.resize(560, 620)
        self.filter = QLineEdit(self)
        self.filter.setPlaceholderText(_("Cerca un comando o un tasto…"))
        self.table = QTableWidget(self)
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels([_("Comando"), _("Scorciatoia")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.rows = collect_shortcuts(window) + [
            (_("Pagina successiva / precedente"), "N / P"),
            (_("Scorri di una schermata"), _("Spazio / Maiusc+Spazio")),
            (_("Torna a Seleziona testo"), "Esc"),
        ]
        self.filter.textChanged.connect(self._fill)
        lay = QVBoxLayout(self)
        lay.addWidget(self.filter)
        lay.addWidget(self.table)
        self._fill("")

    def _fill(self, text: str) -> None:
        t = text.lower().strip()
        rows = [r for r in self.rows if not t or t in r[0].lower() or t in r[1].lower()]
        self.table.setRowCount(len(rows))
        for i, (name, keys) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            self.table.setItem(i, 1, QTableWidgetItem(keys))


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
