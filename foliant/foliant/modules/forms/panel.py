"""Pannello delle proprietà dei campi selezionati (FRM-11…FRM-16).

Il pannello non tocca il documento: ogni modifica passa alla funzione
`apply(chiavi, proprietà, etichetta)` fornita dal chiamante, che applica
`engine.update_properties` a ogni campo (pagina, xref) in un unico passo di
annulla/ripeti e restituisce False in caso di errore (mostrato dal chiamante).
Con selezione multipla sono mostrate solo le proprietà comuni; i valori
diversi appaiono come «misto» e la modifica vale per tutti i campi.
"""

from __future__ import annotations

from datetime import time
from typing import Any, Callable

import re

from PySide6.QtCore import QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QCompleter, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QRadioButton,
    QScrollArea, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QStackedWidget, QToolButton, QVBoxLayout,
    QWidget,
)

from foliant.api import _, bind_icon, icon, language, theme

from . import engine as E
from . import formats as fm
from . import formula as FX

TRANSLATIONS: dict[str, str] = {
    "Sezione": "Section",
    "Vincoli per chi firma": "Signer constraints",
    "qualunque motivo": "any reason",
    "Motivi ammessi, separati da punto e virgola": "Allowed reasons, separated by semicolons",
    "Motivi ammessi": "Allowed reasons",
    "Indirizzo del server di marca temporale da usare obbligatoriamente": "Address of the time stamp server that must be used",
    "Marca temporale": "Time stamp",
    "Non richiesta": "Not required",
    "Nessuna modifica consentita": "No changes allowed",
    "Consenti compilazione e firme": "Allow form filling and signing",
    "Consenti anche i commenti": "Also allow comments",
    "Certificazione": "Certification",
    "Inversione dei colori": "Invert colors",
    "Contorno": "Outline",
    "Pressione": "Push",
    "Al clic": "On click",
    "Invia modulo": "Submit form",
    "Formato dei dati": "Data format",
    "Invia anche i campi vuoti": "Also send empty fields",
    "Nessun campo scelto: si inviano tutti i campi. Con un indirizzo mailto: si apre il programma di posta con i dati allegati.":
        "No field chosen: all fields are sent. With a mailto: address the mail program opens with the data attached.",
    "https:// o mailto:": "https:// or mailto:",
    "Indirizzo non valido: usa https://, http:// o mailto:.": "Invalid address: use https://, http:// or mailto:.",
    "Rilascio del pulsante": "Button release",
    "Pressione del pulsante": "Button press",
    "Ingresso del puntatore": "Mouse enter",
    "Uscita del puntatore": "Mouse exit",
    "Ricezione del focus": "On focus",
    "Perdita del focus": "On blur",
    "Quando": "When",
    "Al passaggio del puntatore": "On mouse over",
    "Solo testo": "Text only",
    "Solo icona": "Icon only",
    "Icona sopra il testo": "Icon above text",
    "Icona sotto il testo": "Icon below text",
    "Icona a sinistra del testo": "Icon left of text",
    "Icona a destra del testo": "Icon right of text",
    "Testo sopra l'icona": "Text over icon",
    "Disposizione": "Layout",
    "Icona": "Icon",
    "Icona al passaggio": "Mouse-over icon",
    "Icona alla pressione": "Pressed icon",
    "Scegli…": "Choose…",
    "Immagine o pagina PDF da usare come icona": "Image or PDF page to use as the icon",
    "Rimuovi l'icona": "Remove the icon",
    "Scegli l'icona": "Choose the icon",
    "Immagini e PDF": "Images and PDF",
    "impostata": "set",
    "nessuna": "none",
    # intestazione e generale
    "Seleziona uno o più campi": "Select one or more fields",
    "{n} campi selezionati": "{n} fields selected",
    "{kind} «{name}»": "{kind} “{name}”",
    "misto": "mixed",
    "Proprietà del campo": "Field properties",
    "Proprietà dei campi": "Field properties",
    "Rinomina campo": "Rename field",
    "Generale": "General",
    "Aspetto": "Appearance",
    "Opzioni": "Options",
    "Formato": "Format",
    "Validazione": "Validation",
    "Calcolo": "Calculation",
    "Nome": "Name",
    "Suggerimento": "Tooltip",
    "Descrizione mostrata al passaggio del mouse e letta dalle tecnologie assistive":
        "Description shown on hover and read by assistive technologies",
    "Valore predefinito": "Default value",
    "Selezionato per impostazione predefinita": "Checked by default",
    "Nessuno": "None",
    "Nessuna": "None",
    "Obbligatorio": "Required",
    "Sola lettura": "Read-only",
    "Non esportare": "Do not export",
    "Visibilità": "Visibility",
    "Visibile": "Visible",
    "Nascosto": "Hidden",
    "Solo a schermo": "Screen only",
    "Solo in stampa": "Print only",
    "Blocca posizione": "Lock position",
    # aspetto
    "Carattere": "Font",
    "Dimensione": "Size",
    "Automatica": "Auto",
    " (misto)": " (mixed)",
    "Colore del testo": "Text color",
    "Colore del bordo": "Border color",
    "Riempimento": "Fill",
    "Colore di riempimento": "Fill color",
    "Scegli colore…": "Choose color…",
    "Trasparente": "Transparent",
    "Spessore del bordo": "Border width",
    "Sottile": "Thin",
    "Medio": "Medium",
    "Spesso": "Thick",
    "Stile del bordo": "Border style",
    "Continuo": "Solid",
    "Tratteggiato": "Dashed",
    "In rilievo": "Beveled",
    "Incassato": "Inset",
    "Sottolineato": "Underline",
    "Allineamento": "Alignment",
    "Sinistra": "Left",
    "Centro": "Center",
    "Destra": "Right",
    "{w} pt": "{w} pt",
    # opzioni
    "Multiriga": "Multi-line",
    "Password": "Password",
    "Non scorrere il testo lungo": "Do not scroll long text",
    "Non controllare l'ortografia": "Do not check spelling",
    "Lunghezza massima": "Maximum length",
    "Pettine (una casella per carattere)": "Comb (one box per character)",
    "Il pettine richiede una lunghezza massima e disattiva multiriga, password e scorrimento.":
        "Comb requires a maximum length and turns off multi-line, password and scrolling.",
    "Stile del segno": "Mark style",
    "Valore di esportazione": "Export value",
    "Una selezione sempre presente": "One option always selected",
    "Pulsanti con lo stesso valore selezionati insieme": "Buttons with the same value are selected together",
    "Voci": "Items",
    "Testo": "Text",
    "Aggiungi": "Add",
    "Elimina": "Delete",
    "Su": "Up",
    "Giù": "Down",
    "Incolla elenco": "Paste list",
    "Una voce per riga; testo e valore di esportazione separati da tabulazione.":
        "One item per line; text and export value separated by a tab.",
    "Voce {n}": "Item {n}",
    "Un elenco deve avere almeno una voce.": "A list must have at least one item.",
    "Ordina alfabeticamente": "Sort alphabetically",
    "Testo libero": "Free text",
    "Scelta multipla": "Multiple selection",
    "Applica subito la scelta": "Commit selection immediately",
    # formato
    "Categoria": "Category",
    "Numero": "Number",
    "Percentuale": "Percentage",
    "Data": "Date",
    "Ora": "Time",
    "Speciale": "Special",
    "Decimali": "Decimals",
    "Separatori": "Separators",
    "Negativi": "Negatives",
    "Segno meno": "Minus sign",
    "Rosso": "Red",
    "Parentesi": "Parentheses",
    "Rosso tra parentesi": "Red in parentheses",
    "Valuta": "Currency",
    "es. €": "e.g. €",
    "Simbolo prima del numero": "Symbol before the number",
    "Modello": "Pattern",
    "g giorno · m mese · a anno (mmmm nome del mese)": "d day · m month · y year (mmmm month name)",
    "d giorno · m mese · y anno (mmmm nome del mese)": "d day · m month · y year (mmmm month name)",
    "Maschera": "Mask",
    "9 cifra · A lettera · O lettera o cifra · X qualsiasi carattere":
        "9 digit · A letter · O letter or digit · X any character",
    "Anteprima": "Preview",
    "Modello di data non valido: serve almeno il mese o l'anno.":
        "Invalid date pattern: it needs at least the month or the year.",
    "La maschera non può essere vuota.": "The mask cannot be empty.",
    # validazione
    "Minimo": "Minimum",
    "Massimo": "Maximum",
    "nessuno": "none",
    "Lunghezza minima": "Minimum length",
    "Espressione regolare": "Regular expression",
    "Messaggio d'errore": "Error message",
    "Valore non valido.": "Invalid value.",
    "Il minimo non è un numero.": "The minimum is not a number.",
    "Il massimo non è un numero.": "The maximum is not a number.",
    "Le regole si applicano alla conferma del valore; un campo vuoto non viene controllato.":
        "Rules apply when the value is confirmed; an empty field is not checked.",
    # calcolo
    "Operazione": "Operation",
    "Somma": "Sum",
    "Media": "Average",
    "Prodotto": "Product",
    "Campi": "Fields",
    "Filtra per nome": "Filter by name",
    "Seleziona i campi mostrati": "Select shown fields",
    "Deseleziona tutti": "Deselect all",
    "I campi vuoti valgono 0": "Empty fields count as 0",
    "Conviene rendere il campo di sola lettura e dargli un formato numerico.":
        "Consider making the field read-only and giving it a number format.",
    # etichette del motore usate qui
    "Campo di testo": "Text field",
    "Casella di controllo": "Check box",
    "Pulsante di opzione": "Radio button",
    "Elenco a discesa": "Drop-down list",
    "tipo di campo\x04Elenco": "List box",   # pgettext: il tipo di campo, non il nome predefinito
    "Pulsante": "Button",
    "Campo firma": "Signature field",
    "Spunta": "Check",
    "Cerchio": "Circle",
    "Croce": "Cross",
    "Rombo": "Diamond",
    "Quadrato": "Square",
    "Stella": "Star",
    # rotazione e predefiniti (FRM-12)
    "Rotazione": "Rotation",
    "Usa come predefinito": "Use as default",
    "Salva l'aspetto come impostazione per i nuovi campi di questo tipo":
        "Save the appearance as the setting for new fields of this type",
    # formula (FRM-17)
    "Calcolo predefinito": "Predefined calculation",
    "Formula": "Formula",
    "es. Imponibile * 1.22 - Sconto": "e.g. Net * 1.22 - Discount",
    "Applica": "Apply",
    "Errore alla posizione {n}: {msg}": "Error at position {n}: {msg}",
    "Campi inesistenti: {names}": "Unknown fields: {names}",
    "La formula non può usare il campo stesso.": "The formula cannot use the field itself.",
    "Operatori + − × ÷ e parentesi; i nomi con spazi vanno tra virgolette doppie.":
        "Operators + − × ÷ and parentheses; names with spaces go in double quotes.",
    # pulsanti (FRM-18)
    "Didascalia": "Caption",
    "uguale al nome": "same as the name",
    "Nome usato nei dati esportati al posto del nome del campo": "Name used in exported data instead of the field name",
    "Nome per l'esportazione": "Export name",
    "Alla pressione": "When pressed",
    "uguale alla didascalia": "same as the caption",
    "Campo immagine: in compilazione un clic permette di scegliere l'immagine.":
        "Image field: when filling in, a click lets you choose the image.",
    "Azione": "Action",
    "Reimposta campi": "Reset fields",
    "Apri indirizzo web": "Open web address",
    "Stampa": "Print",
    "Vai a pagina": "Go to page",
    "Mostra o nascondi campi": "Show or hide fields",
    "Importa dati": "Import data",
    "Altra azione (non modificabile qui)": "Other action (not editable here)",
    "Indirizzo": "Address",
    "L'indirizzo non è cifrato (non HTTPS)": "The address is not encrypted (not HTTPS)",
    "Pagina": "Page",
    "Effetto": "Effect",
    "Nascondi": "Hide",
    "Mostra": "Show",
    "Nessun campo scelto: vengono reimpostati tutti i campi.": "No field chosen: all fields are reset.",
    # firma (FRM-20)
    "Firma": "Signature",
    "Dopo la firma blocca": "After signing, lock",
    "Nessun campo": "No fields",
    "Tutti i campi": "All fields",
    "Solo questi campi": "Only these fields",
    "Tutti tranne questi": "All except these",
    "Scegli almeno un campo.": "Choose at least one field.",
}

Apply = Callable[[list[tuple[int, int]], dict, str], bool]
SetDefault = Callable[[str, dict], None]

_ROTATIONS = (0, 90, 180, 270)
_APPEARANCE_KEYS = ("font", "fontsize", "text_color", "fill_color", "border_color", "border_width",
                    "border_style", "align")
_ACTION_TYPES = (None, "reset", "uri", "print", "goto", "hide", "import", "submit")
_PLAIN_NAME = re.compile(r"[^\W\d][\w.]*")

_TEXT_LIKE = (E.TEXT, E.COMBO, E.LIST)
_MARKED = (E.CHECKBOX, E.RADIO)


# ====================================================================== colori
def _qcolor(c) -> QColor:
    return QColor.fromRgbF(*(max(0.0, min(1.0, float(v))) for v in c[:3]))


def _tuple(q: QColor) -> tuple:
    return (round(q.redF(), 4), round(q.greenF(), 4), round(q.blueF(), 4))


class SectionTabs(QWidget):
    """Sezioni del pannello scelte da una tendina (al posto delle schede): con sei sezioni le
    schede non entrano nel pannello stretto e alcune restavano nascoste dietro le frecce.
    Offre i metodi di QTabWidget usati dal pannello."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.chooser = QComboBox()
        self.chooser.setAccessibleName(_("Sezione"))
        self.stack = QStackedWidget()
        lay.addWidget(self.chooser)
        lay.addWidget(self.stack, 1)
        self._titles: list[str] = []
        self._visible: list[bool] = []
        self.chooser.currentIndexChanged.connect(self._on_chosen)

    def addTab(self, widget: QWidget, title: str) -> int:
        self.stack.addWidget(widget)
        self._titles.append(title)
        self._visible.append(True)
        self._rebuild()
        return len(self._titles) - 1

    def count(self) -> int:
        return len(self._titles)

    def indexOf(self, widget: QWidget) -> int:
        return self.stack.indexOf(widget)

    def tabText(self, index: int) -> str:
        return self._titles[index]

    def isTabVisible(self, index: int) -> bool:
        return self._visible[index]

    def setTabVisible(self, index: int, visible: bool) -> None:
        if self._visible[index] != visible:
            self._visible[index] = visible
            self._rebuild()

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < len(self._titles) and self._visible[index]:
            self.stack.setCurrentIndex(index)
            self.chooser.blockSignals(True)
            self.chooser.setCurrentIndex(max(0, self.chooser.findData(index)))
            self.chooser.blockSignals(False)

    def _rebuild(self) -> None:
        current = self.stack.currentIndex()
        self.chooser.blockSignals(True)
        self.chooser.clear()
        for i, (title, visible) in enumerate(zip(self._titles, self._visible)):
            if visible:
                self.chooser.addItem(title, i)
        self.chooser.blockSignals(False)
        if current < 0 or not self._visible[current]:
            current = next((i for i, v in enumerate(self._visible) if v), -1)
        if current >= 0:
            self.setCurrentIndex(current)

    def _on_chosen(self, row: int) -> None:
        index = self.chooser.itemData(row)
        if index is not None:
            self.stack.setCurrentIndex(int(index))


class ColorButton(QToolButton):
    """Pulsante con campione di colore; facoltativamente con la scelta «Trasparente» (None)."""

    picked = Signal(object)     # tuple (0..1) | None

    def __init__(self, title: str, allow_none: bool = False, parent=None):
        super().__init__(parent)
        self._title = title
        self._color: tuple | None = None
        self._mixed = False
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(18, 14))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.clicked.connect(self.choose)
        if allow_none:
            menu = QMenu(self)
            menu.addAction(_("Scegli colore…"), self.choose)
            menu.addAction(_("Trasparente"), self.set_transparent)
            self.setMenu(menu)
            self.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self._refresh()

    def color(self) -> tuple | None:
        return self._color

    def set_color(self, c: tuple | None, mixed: bool = False) -> None:
        self._color = tuple(c) if c is not None else None
        self._mixed = mixed
        self._refresh()

    def choose(self) -> None:
        initial = _qcolor(self._color) if self._color is not None else QColor(255, 255, 255)
        q = QColorDialog.getColor(initial, self, self._title)
        if q is not None and q.isValid():
            self.set_color(_tuple(q))
            self.picked.emit(self._color)

    def set_transparent(self) -> None:
        self.set_color(None)
        self.picked.emit(None)

    def _refresh(self) -> None:
        pm = QPixmap(36, 28)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = pm.rect().adjusted(2, 2, -2, -2)
        if self._color is not None and not self._mixed:
            p.fillRect(rect, _qcolor(self._color))
        p.setPen(QPen(theme.color("border"), 2))
        p.drawRect(rect)
        if self._color is None and not self._mixed:
            p.setPen(QPen(theme.color("danger"), 2))
            p.drawLine(rect.bottomLeft(), rect.topRight())
        p.end()
        self.setIcon(QIcon(pm))
        if self._mixed:
            self.setText(_("misto"))
        elif self._color is None:
            self.setText(_("Trasparente"))
        else:
            self.setText(_qcolor(self._color).name().upper())


# ====================================================================== pannello
class FieldPropertiesPanel(QWidget):
    """Pannello destro con le proprietà dei campi selezionati."""

    def __init__(self, apply: Apply, parent=None, *, set_default: SetDefault | None = None):
        super().__init__(parent)
        self._apply_cb = apply
        self._set_default_cb = set_default
        self._kind: str | None = None
        self._loaded_formula: str | None = None
        self._loaded_action: dict | None = None
        self._loaded_lock: dict | None = None
        self._loaded_sv: dict | None = None
        self._fields: list[E.FieldInfo] = []
        self._all_names: list[str] = []
        self._radio_exports: dict[str, list[str]] = {}
        self._loading = False
        self._loaded: dict[int, Any] = {}       # valore caricato per i campi a riga
        self._loaded_fmt: fm.Format | None = None
        self._loaded_val: E.Validation | None = None
        self._loaded_calc: E.Calculation | None = None
        self._loaded_opts: list[tuple[str, str]] | None = None
        self._view = None
        self.setMinimumWidth(300)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.title = QLabel()
        self.title.setWordWrap(True)
        f = self.title.font()
        f.setBold(True)
        self.title.setFont(f)
        lay.addWidget(self.title)
        self.placeholder = QLabel(_("Seleziona uno o più campi"))
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setWordWrap(True)
        lay.addWidget(self.placeholder, 1)
        self.tabs = SectionTabs()   # tendina di sezione: tutte raggiungibili anche nel pannello stretto
        lay.addWidget(self.tabs, 1)
        self._pages: dict[str, QWidget] = {}

        self._build_general()
        self._build_appearance()
        self._build_options()
        self._build_format()
        self._build_validation()
        self._build_calc()
        self._build_button()
        self._build_signature()
        self.set_fields([], [])

    # ------------------------------------------------------------ API
    def set_view(self, view) -> None:
        """Chiamato dalla finestra al cambio documento: il pannello non usa la vista."""
        self._view = view
        if view is None:
            self.set_fields([], [])

    def fields(self) -> list[E.FieldInfo]:
        return list(self._fields)

    def set_fields(self, fields: list[E.FieldInfo], all_names: list[str],
                   radio_exports: dict[str, list[str]] | None = None) -> None:
        """Mostra le proprietà della selezione. `radio_exports` (facoltativo): nome del gruppo →
        valori di esportazione di tutte le sue opzioni, per il valore predefinito dei gruppi."""
        self._fields = list(fields)
        self._all_names = list(all_names)
        self._radio_exports = dict(radio_exports or {})
        self._load()

    # ------------------------------------------------------------ costruzione
    def _page(self, key: str, title: str) -> QFormLayout:
        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(4, 8, 4, 8)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(inner)
        self.tabs.addTab(scroll, title)
        self._pages[key] = scroll
        return form

    def _set_tab(self, key: str, visible: bool) -> None:
        self.tabs.setTabVisible(self.tabs.indexOf(self._pages[key]), visible)

    @staticmethod
    def _note(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        return lab

    @staticmethod
    def _error_label() -> QLabel:
        lab = QLabel()
        lab.setWordWrap(True)
        lab.hide()
        return lab

    def _check(self, text: str, handler: Callable[[bool], None]) -> QCheckBox:
        cb = QCheckBox(text)
        cb.stateChanged.connect(lambda _s, cb=cb: self._on_check(cb, handler))
        return cb

    def _flag(self, text: str, key: str) -> QCheckBox:
        return self._check(text, lambda on, k=key: self._apply({k: on}))

    def _on_check(self, cb: QCheckBox, handler) -> None:
        if self._loading:
            return
        st = cb.checkState()
        if st == Qt.CheckState.PartiallyChecked:
            return
        cb.setTristate(False)
        handler(st == Qt.CheckState.Checked)

    @staticmethod
    def _add_system_fonts(cb: QComboBox) -> None:
        """Caratteri di sistema dopo i tre standard (incorporati come sottoinsieme nei campi di testo)."""
        try:
            from PySide6.QtGui import QFontDatabase
            families = [f for f in QFontDatabase.families() if not f.startswith(".")]
        except Exception:
            families = []
        if families:
            cb.insertSeparator(cb.count())
            for fam in sorted(set(families), key=str.casefold):
                if fam not in E.FONTS:
                    cb.addItem(fam, fam)

    def _combo(self, items: list[tuple[str, Any]], handler: Callable[[Any], None]) -> QComboBox:
        cb = QComboBox()
        for label, data in items:
            cb.addItem(label, data)
        cb.currentIndexChanged.connect(lambda i, cb=cb: self._on_combo(cb, i, handler))
        return cb

    def _on_combo(self, cb: QComboBox, index: int, handler) -> None:
        if self._loading or index < 0:
            return
        handler(cb.itemData(index))

    def _line(self, handler: Callable[[str], None]) -> QLineEdit:
        le = QLineEdit()
        le.editingFinished.connect(lambda le=le: self._on_line(le, handler))
        return le

    def _on_line(self, le: QLineEdit, handler) -> None:
        if self._loading or not self._fields:
            return
        text = le.text()
        old = self._loaded.get(id(le))
        if (old is None and text == "") or text == old:
            return
        self._loaded[id(le)] = text      # su errore _reload() ripristina il valore caricato
        handler(text)

    def _spin(self, lo: int, hi: int, handler: Callable[[int], None], special: str = "") -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        if special:
            sp.setSpecialValueText(special)
        sp.valueChanged.connect(lambda v, sp=sp: self._on_spin(sp, v, handler))
        return sp

    def _on_spin(self, sp: QSpinBox, v: int, handler) -> None:
        if self._loading:
            return
        sp.setSuffix("")
        handler(v)

    # .............................................................. Generale
    def _build_general(self) -> None:
        form = self._page("general", _("Generale"))
        self.name_edit = QLineEdit()
        self.name_edit.editingFinished.connect(self._on_name)
        form.addRow(_("Nome"), self.name_edit)
        self.tooltip_edit = self._line(lambda t: self._apply({"tooltip": t}))
        self.tooltip_edit.setToolTip(_("Descrizione mostrata al passaggio del mouse e letta dalle tecnologie assistive"))
        form.addRow(_("Suggerimento"), self.tooltip_edit)
        self.export_name_edit = self._line(lambda t: self._apply({"export_name": t}))
        self.export_name_edit.setPlaceholderText(_("uguale al nome"))
        self.export_name_edit.setToolTip(_("Nome usato nei dati esportati al posto del nome del campo"))
        form.addRow(_("Nome per l'esportazione"), self.export_name_edit)
        self.default_edit = self._line(lambda t: self._apply({"default": t or None}))
        form.addRow(_("Valore predefinito"), self.default_edit)
        self.default_check = self._check(_("Selezionato per impostazione predefinita"),
                                         lambda on: self._apply({"default": on}))
        form.addRow(self.default_check)
        self.default_combo = self._combo([], self._on_default_choice)
        form.addRow(_("Valore predefinito"), self.default_combo)
        self.required_check = self._flag(_("Obbligatorio"), "required")
        form.addRow(self.required_check)
        self.readonly_check = self._flag(_("Sola lettura"), "readonly")
        form.addRow(self.readonly_check)
        self.no_export_check = self._flag(_("Non esportare"), "no_export")
        form.addRow(self.no_export_check)
        self.visibility_combo = self._combo(
            [(_("Visibile"), "visible"), (_("Nascosto"), "hidden"), (_("Solo a schermo"), "screen"),
             (_("Solo in stampa"), "print")], lambda v: self._apply({"visibility": v}))
        form.addRow(_("Visibilità"), self.visibility_combo)
        self.locked_check = self._flag(_("Blocca posizione"), "locked")
        form.addRow(self.locked_check)
        self._general_form = form

    def _on_name(self) -> None:
        if self._loading or len(self._fields) != 1:
            return
        old = self._fields[0].name
        new = self.name_edit.text().strip()
        if new == old:
            return
        if not new or not self._apply({"name": new}, _("Rinomina campo")):
            self._loading = True
            try:
                self.name_edit.setText(old)
            finally:
                self._loading = False
            return
        # finché il chiamante non aggiorna la selezione il nome nuovo vale come attuale
        self._fields[0].name = new

    def _on_default_choice(self, data) -> None:
        kind = self._fields[0].kind if self._fields else None
        if kind == E.LIST and self._fields[0].multiselect:
            self._apply({"default": [data] if data else None})
        else:
            self._apply({"default": data or None})

    # .............................................................. Aspetto
    def _build_appearance(self) -> None:
        form = self._page("appearance", _("Aspetto"))
        self.font_combo = self._combo([(n, n) for n in E.FONTS], lambda v: self._apply({"font": v}))
        self.font_combo.setMaxVisibleItems(20)
        self._add_system_fonts(self.font_combo)
        form.addRow(_("Carattere"), self.font_combo)
        self.size_spin = self._spin(0, 96, lambda v: self._apply({"fontsize": float(v)}), _("Automatica"))
        form.addRow(_("Dimensione"), self.size_spin)
        self.text_color_btn = ColorButton(_("Colore del testo"))
        self.text_color_btn.picked.connect(lambda c: self._apply({"text_color": c}))
        form.addRow(_("Colore del testo"), self.text_color_btn)
        self.border_color_btn = ColorButton(_("Colore del bordo"), allow_none=True)
        self.border_color_btn.picked.connect(lambda c: self._apply({"border_color": c}))
        form.addRow(_("Colore del bordo"), self.border_color_btn)
        self.border_width_combo = self._combo(
            [(_("Nessuno"), 0.0), (_("Sottile"), 1.0), (_("Medio"), 2.0), (_("Spesso"), 3.0)],
            lambda v: self._apply({"border_width": v}))
        form.addRow(_("Spessore del bordo"), self.border_width_combo)
        self.border_style_combo = self._combo(
            [(_("Continuo"), "S"), (_("Tratteggiato"), "D"), (_("In rilievo"), "B"), (_("Incassato"), "I"),
             (_("Sottolineato"), "U")], lambda v: self._apply({"border_style": v}))
        form.addRow(_("Stile del bordo"), self.border_style_combo)
        self.fill_color_btn = ColorButton(_("Colore di riempimento"), allow_none=True)
        self.fill_color_btn.picked.connect(lambda c: self._apply({"fill_color": c}))
        form.addRow(_("Riempimento"), self.fill_color_btn)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self.align_group = QButtonGroup(self)
        self.align_buttons: list[QToolButton] = []
        for value, (name, tip) in enumerate((("align-left", _("Sinistra")), ("align-center", _("Centro")),
                                             ("align-right", _("Destra")))):
            b = QToolButton()
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            bind_icon(b, name)
            self.align_group.addButton(b, value)
            self.align_buttons.append(b)
            h.addWidget(b)
        h.addStretch(1)
        self.align_group.idClicked.connect(self._on_align)
        self.align_row = row
        form.addRow(_("Allineamento"), row)
        self.rotation_combo = self._combo([(f"{r}°", r) for r in _ROTATIONS],
                                          lambda v: self._apply({"rotation": v}))
        form.addRow(_("Rotazione"), self.rotation_combo)
        self.set_default_btn = QPushButton(_("Usa come predefinito"))
        self.set_default_btn.setToolTip(_("Salva l'aspetto come impostazione per i nuovi campi di questo tipo"))
        self.set_default_btn.clicked.connect(self._on_set_default)
        form.addRow(self.set_default_btn)
        self._appearance_form = form

    def _on_align(self, value: int) -> None:
        if not self._loading:
            self._apply({"align": value})

    def current_appearance(self) -> dict:
        """Proprietà d'aspetto mostrate dalla scheda (i valori misti sono omessi)."""
        props: dict = {}
        if self.font_combo.currentIndex() >= 0:
            props["font"] = self.font_combo.currentData()
        if not self.size_spin.suffix():
            props["fontsize"] = float(self.size_spin.value())
        for key, btn in (("text_color", self.text_color_btn), ("fill_color", self.fill_color_btn),
                         ("border_color", self.border_color_btn)):
            if not btn._mixed:
                props[key] = btn.color()
        if self.border_width_combo.currentIndex() >= 0:
            props["border_width"] = self.border_width_combo.currentData()
        elif self._fields and not self._common("border_width")[1]:
            props["border_width"] = float(self._fields[0].border_width or 0)
        if self.border_style_combo.currentIndex() >= 0:
            props["border_style"] = self.border_style_combo.currentData()
        if self.align_group.checkedId() >= 0:
            props["align"] = self.align_group.checkedId()
        return props

    def _on_set_default(self) -> None:
        if self._loading or not self._fields or self._kind is None or self._set_default_cb is None:
            return
        self._set_default_cb(self._kind, self.current_appearance())

    # .............................................................. Opzioni
    def _build_options(self) -> None:
        form = self._page("options", _("Opzioni"))
        # testo
        self.multiline_check = self._flag(_("Multiriga"), "multiline")
        form.addRow(self.multiline_check)
        self.password_check = self._flag(_("Password"), "password")
        form.addRow(self.password_check)
        self.scroll_check = self._flag(_("Non scorrere il testo lungo"), "do_not_scroll")
        form.addRow(self.scroll_check)
        self.spell_check = self._flag(_("Non controllare l'ortografia"), "no_spell")
        form.addRow(self.spell_check)
        self.maxlen_spin = self._spin(0, 9999, self._on_maxlen, _("Nessuna"))
        form.addRow(_("Lunghezza massima"), self.maxlen_spin)
        self.comb_check = self._check(_("Pettine (una casella per carattere)"), self._on_comb)
        form.addRow(self.comb_check)
        self.comb_note = self._note(
            _("Il pettine richiede una lunghezza massima e disattiva multiriga, password e scorrimento."))
        form.addRow(self.comb_note)
        # caselle e opzioni
        self.check_style_combo = self._combo([(E.check_style_label(k), k) for k in E.CHECK_STYLES],
                                             lambda v: self._apply({"check_style": v}))
        form.addRow(_("Stile del segno"), self.check_style_combo)
        self.export_edit = self._line(lambda t: self._apply({"export": t.strip()}) if t.strip() else self._reload())
        form.addRow(_("Valore di esportazione"), self.export_edit)
        self.no_toggle_off_check = self._flag(_("Una selezione sempre presente"), "no_toggle_off")
        form.addRow(self.no_toggle_off_check)
        self.in_unison_check = self._flag(_("Pulsanti con lo stesso valore selezionati insieme"), "in_unison")
        form.addRow(self.in_unison_check)
        # elenchi
        self.items_box = self._build_items_editor()
        form.addRow(self.items_box)
        self.sort_check = self._flag(_("Ordina alfabeticamente"), "sort")
        form.addRow(self.sort_check)
        self.editable_check = self._flag(_("Testo libero"), "editable")
        form.addRow(self.editable_check)
        self.multiselect_check = self._flag(_("Scelta multipla"), "multiselect")
        form.addRow(self.multiselect_check)
        self.commit_check = self._flag(_("Applica subito la scelta"), "commit_on_change")
        form.addRow(self.commit_check)
        self._options_form = form
        self._text_rows = [self.multiline_check, self.password_check, self.scroll_check, self.spell_check,
                           self.maxlen_spin, self.comb_check, self.comb_note]
        self._mark_rows = [self.check_style_combo, self.export_edit, self.no_toggle_off_check,
                           self.in_unison_check]
        self._choice_rows = [self.items_box, self.sort_check, self.editable_check, self.multiselect_check,
                             self.commit_check]

    def _build_items_editor(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(QLabel(_("Voci")))
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels([_("Testo"), _("Valore di esportazione")])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setMinimumHeight(140)
        t.setMaximumHeight(280)
        t.itemChanged.connect(self._on_item_changed)
        self.items_table = t
        v.addWidget(t)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.item_buttons: dict[str, QToolButton] = {}
        for key, name, tip, slot in (("add", "plus", _("Aggiungi"), self._add_item),
                                     ("del", "trash-2", _("Elimina"), self._delete_item),
                                     ("up", "arrow-up", _("Su"), lambda: self._move_item(-1)),
                                     ("down", "arrow-down", _("Giù"), lambda: self._move_item(1)),
                                     ("paste", "clipboard-paste", _("Incolla elenco"), self._paste_clipboard)):
            b = QToolButton()
            b.setToolTip(tip if key != "paste" else
                         tip + "\n" + _("Una voce per riga; testo e valore di esportazione separati da tabulazione."))
            b.setAccessibleName(tip)
            bind_icon(b, name)
            b.clicked.connect(slot)
            self.item_buttons[key] = b
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)
        self.items_error = self._error_label()
        v.addWidget(self.items_error)
        return box

    def _on_maxlen(self, v: int) -> None:
        props: dict = {"maxlen": v}
        if v == 0 and self.comb_check.checkState() != Qt.CheckState.Unchecked:
            props["comb"] = False
        self._update_text_constraints()
        self._apply(props)

    def _on_comb(self, on: bool) -> None:
        self._update_text_constraints()
        self._apply({"comb": True, "maxlen": self.maxlen_spin.value()} if on else {"comb": False})

    def _update_text_constraints(self) -> None:
        """Vincoli visibili del pettine: serve la lunghezza massima; esclude multiriga, password, scorrimento."""
        was = self._loading
        self._loading = True
        try:
            comb = self.comb_check.checkState() == Qt.CheckState.Checked
            has_len = self.maxlen_spin.value() > 0 or self.maxlen_spin.suffix() != ""
            if not has_len and self.comb_check.checkState() != Qt.CheckState.Unchecked:
                self.comb_check.setTristate(False)
                self.comb_check.setChecked(False)
                comb = False
            self.comb_check.setEnabled(has_len)
            for cb in (self.multiline_check, self.password_check):
                if comb:
                    cb.setTristate(False)
                    cb.setChecked(False)
                cb.setEnabled(not comb)
            if comb:
                self.scroll_check.setTristate(False)
                self.scroll_check.setChecked(True)
            self.scroll_check.setEnabled(not comb)
        finally:
            self._loading = was

    # ..... voci degli elenchi
    def _table_pairs(self) -> list[tuple[str, str]]:
        out = []
        t = self.items_table
        for r in range(t.rowCount()):
            text = (t.item(r, 0).text() if t.item(r, 0) else "").strip()
            export = (t.item(r, 1).text() if t.item(r, 1) else "").strip()
            if text or export:
                out.append((export or text, text or export))
        return out

    def _set_table(self, pairs: list[tuple[str, str]]) -> None:
        was = self._loading
        self._loading = True
        try:
            t = self.items_table
            t.setRowCount(0)
            for export, text in pairs:
                self._append_row(text, export)
        finally:
            self._loading = was

    def _append_row(self, text: str, export: str) -> int:
        t = self.items_table
        r = t.rowCount()
        t.insertRow(r)
        a = QTableWidgetItem(text)
        a.setData(Qt.ItemDataRole.UserRole, text)
        t.setItem(r, 0, a)
        t.setItem(r, 1, QTableWidgetItem(export))
        return r

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        if item.column() == 0:
            old = item.data(Qt.ItemDataRole.UserRole)
            exp = self.items_table.item(item.row(), 1)
            self._loading = True
            try:
                if exp is not None and exp.text() == old:   # valore che seguiva il testo
                    exp.setText(item.text())
                item.setData(Qt.ItemDataRole.UserRole, item.text())
            finally:
                self._loading = False
        self._apply_options()

    def _apply_options(self) -> bool:
        pairs = self._table_pairs()
        if not pairs:
            self._show_error(self.items_error, _("Un elenco deve avere almeno una voce."))
            return False
        self._show_error(self.items_error, None)
        if pairs == self._loaded_opts:
            return True
        ok = self._apply({"options": pairs})
        if ok:
            self._loaded_opts = pairs
        return ok

    def _add_item(self) -> None:
        texts = {p[1] for p in self._table_pairs()}
        n = self.items_table.rowCount() + 1
        while _("Voce {n}").format(n=n) in texts:
            n += 1
        label = _("Voce {n}").format(n=n)
        self._loading = True
        try:
            r = self._append_row(label, label)
        finally:
            self._loading = False
        self.items_table.selectRow(r)
        self._apply_options()

    def _delete_item(self) -> None:
        r = self.items_table.currentRow()
        if r < 0:
            return
        if self.items_table.rowCount() <= 1:
            self._show_error(self.items_error, _("Un elenco deve avere almeno una voce."))
            return
        self._loading = True
        try:
            self.items_table.removeRow(r)
        finally:
            self._loading = False
        self._apply_options()

    def _move_item(self, delta: int) -> None:
        t = self.items_table
        r = t.currentRow()
        s = r + delta
        if r < 0 or not 0 <= s < t.rowCount():
            return
        self._loading = True
        try:
            for c in range(2):
                a, b = t.takeItem(r, c), t.takeItem(s, c)
                t.setItem(r, c, b)
                t.setItem(s, c, a)
        finally:
            self._loading = False
        t.selectRow(s)
        self._apply_options()

    def _paste_clipboard(self) -> None:
        self.paste_items(QGuiApplication.clipboard().text() or "")

    def paste_items(self, text: str) -> bool:
        """Sostituisce le voci con un elenco incollato: una voce per riga,
        facoltativamente «testo<TAB>valore di esportazione»."""
        pairs = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            if "\t" in line:
                shown, export = (s.strip() for s in line.split("\t", 1))
            else:
                shown = export = line.strip()
            pairs.append((export or shown, shown or export))
        if not pairs:
            return False
        self._set_table(pairs)
        return self._apply_options()

    # .............................................................. Formato
    def _build_format(self) -> None:
        form = self._page("format", _("Formato"))
        self.fmt_category = self._combo(
            [(_("Nessuno"), fm.NONE), (_("Numero"), fm.NUMBER), (_("Percentuale"), fm.PERCENT),
             (_("Data"), fm.DATE), (_("Ora"), fm.TIME), (_("Speciale"), fm.SPECIAL)],
            lambda _v: self._on_format_changed())
        form.addRow(_("Categoria"), self.fmt_category)
        self.fmt_decimals = self._spin(0, 10, lambda _v: self._on_format_changed())
        form.addRow(_("Decimali"), self.fmt_decimals)
        self.fmt_separator = self._combo([(s, i) for i, s in enumerate(fm.SEPARATORS)],
                                         lambda _v: self._on_format_changed())
        form.addRow(_("Separatori"), self.fmt_separator)
        self.fmt_negative = self._combo(
            [(_("Segno meno"), 0), (_("Rosso"), 1), (_("Parentesi"), 2), (_("Rosso tra parentesi"), 3)],
            lambda _v: self._on_format_changed())
        form.addRow(_("Negativi"), self.fmt_negative)
        self.fmt_currency = QLineEdit()
        self.fmt_currency.setPlaceholderText(_("es. €"))
        self.fmt_currency.setMaxLength(8)
        self.fmt_currency.editingFinished.connect(self._on_format_changed)
        form.addRow(_("Valuta"), self.fmt_currency)
        self.fmt_currency_before = self._check(_("Simbolo prima del numero"), lambda _on: self._on_format_changed())
        form.addRow(self.fmt_currency_before)
        self.fmt_date = QComboBox()
        self.fmt_date.setEditable(True)
        self.fmt_date.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for p in fm.DATE_PATTERNS:
            self.fmt_date.addItem(fm.pattern_label(p), p)
        self.fmt_date.activated.connect(lambda _i: self._on_format_changed())
        self.fmt_date.lineEdit().editingFinished.connect(self._on_format_changed)
        form.addRow(_("Modello"), self.fmt_date)
        self.fmt_date_note = self._note(_("g giorno · m mese · a anno (mmmm nome del mese)") if language() == "it"
                                        else _("d giorno · m mese · y anno (mmmm nome del mese)"))
        form.addRow(self.fmt_date_note)
        self.fmt_time = self._combo([], lambda _v: self._on_format_changed())
        sample = time(14, 30, 5)
        for p in fm.TIME_PATTERNS:
            self.fmt_time.addItem(f"{p}  ({fm.format_time(sample, p)})", p)
        form.addRow(_("Modello"), self.fmt_time)
        self.fmt_mask = QComboBox()
        self.fmt_mask.setEditable(True)
        self.fmt_mask.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.fmt_mask.addItems(fm.SPECIAL_MASKS)
        self.fmt_mask.activated.connect(lambda _i: self._on_format_changed())
        self.fmt_mask.lineEdit().editingFinished.connect(self._on_format_changed)
        form.addRow(_("Maschera"), self.fmt_mask)
        self.fmt_mask_note = self._note(_("9 cifra · A lettera · O lettera o cifra · X qualsiasi carattere"))
        form.addRow(self.fmt_mask_note)
        self.fmt_preview = QLabel()
        self.fmt_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(_("Anteprima"), self.fmt_preview)
        self.fmt_error = self._error_label()
        form.addRow(self.fmt_error)
        self._format_form = form

    def _date_pattern(self) -> str:
        text = self.fmt_date.currentText().strip()
        for i in range(self.fmt_date.count()):
            if self.fmt_date.itemText(i) == text:
                return self.fmt_date.itemData(i)
        if language() == "it":   # modello scritto con le iniziali italiane
            text = text.replace("aaaa", "yyyy").replace("aa", "yy").replace("gg", "dd").replace("g", "d")
        return text

    def current_format(self) -> fm.Format:
        t = self.fmt_category.currentData() or fm.NONE
        f = fm.Format(t)
        if t in (fm.NUMBER, fm.PERCENT):
            f.decimals = self.fmt_decimals.value()
            f.separator = int(self.fmt_separator.currentData() or 0)
        if t == fm.NUMBER:
            f.negative = int(self.fmt_negative.currentData() or 0)
            f.currency = self.fmt_currency.text().strip()
            f.currency_before = self.fmt_currency_before.isChecked()
        if t == fm.DATE:
            f.pattern = self._date_pattern()
        if t == fm.TIME:
            f.pattern = self.fmt_time.currentData() or fm.TIME_PATTERNS[0]
        if t == fm.SPECIAL:
            f.mask = self.fmt_mask.currentText().strip()
        return f

    def _format_error(self, f: fm.Format) -> str | None:
        if f.type == fm.DATE and not fm.valid_date_pattern(f.pattern):
            return _("Modello di data non valido: serve almeno il mese o l'anno.")
        if f.type == fm.SPECIAL and not f.mask:
            return _("La maschera non può essere vuota.")
        return None

    @staticmethod
    def _sample(f: fm.Format) -> str:
        if f.type == fm.NUMBER:
            return "-1234.5"
        if f.type == fm.PERCENT:
            return "0.1234"
        if f.type == fm.DATE:
            return fm.today_text(f)
        if f.type == fm.TIME:
            return "14:30:05"
        if f.type == fm.SPECIAL:
            digits, letters, out = "1234567890", "ABCDEFGHIJ", []
            for i, m in enumerate(c for c in f.mask if c in "9AOX"):
                out.append(digits[i % 10] if m in "9O" else letters[i % 10])
            return "".join(out)
        return ""

    def _update_format_ui(self) -> None:
        f = self.current_format()
        t = f.type
        rows = {self.fmt_decimals: t in (fm.NUMBER, fm.PERCENT), self.fmt_separator: t in (fm.NUMBER, fm.PERCENT),
                self.fmt_negative: t == fm.NUMBER, self.fmt_currency: t == fm.NUMBER,
                self.fmt_currency_before: t == fm.NUMBER, self.fmt_date: t == fm.DATE,
                self.fmt_date_note: t == fm.DATE, self.fmt_time: t == fm.TIME, self.fmt_mask: t == fm.SPECIAL,
                self.fmt_mask_note: t == fm.SPECIAL, self.fmt_preview: t != fm.NONE}
        for w, on in rows.items():
            self._format_form.setRowVisible(w, on)
        err = self._format_error(f)
        if err or t == fm.NONE:
            self.fmt_preview.setText("")
            return
        text, red = fm.display(f, self._sample(f))
        self.fmt_preview.setText(text)
        self.fmt_preview.setStyleSheet(f"color: {theme.hex_color('danger')};" if red else "")

    def _on_format_changed(self) -> None:
        if self._loading or not self._fields or self.fmt_category.currentIndex() < 0:
            return
        self._update_format_ui()
        f = self.current_format()
        err = self._format_error(f)
        self._show_error(self.fmt_error, err)
        if err or f == self._loaded_fmt:
            return
        if self._apply({"fmt": f}):
            self._loaded_fmt = f

    # .............................................................. Validazione
    def _build_validation(self) -> None:
        form = self._page("validation", _("Validazione"))
        self.val_min = QLineEdit()
        self.val_max = QLineEdit()
        for le in (self.val_min, self.val_max):
            le.setPlaceholderText(_("nessuno"))
            le.editingFinished.connect(self._on_validation_changed)
        form.addRow(_("Minimo"), self.val_min)
        form.addRow(_("Massimo"), self.val_max)
        self.val_minlen = self._spin(0, 9999, lambda _v: self._on_validation_changed(), _("Nessuna"))
        form.addRow(_("Lunghezza minima"), self.val_minlen)
        self.val_regex = QLineEdit()
        self.val_regex.editingFinished.connect(self._on_validation_changed)
        form.addRow(_("Espressione regolare"), self.val_regex)
        self.val_message = QLineEdit()
        self.val_message.setPlaceholderText(_("Valore non valido."))
        self.val_message.editingFinished.connect(self._on_validation_changed)
        form.addRow(_("Messaggio d'errore"), self.val_message)
        self.val_error = self._error_label()
        form.addRow(self.val_error)
        form.addRow(self._note(_("Le regole si applicano alla conferma del valore; un campo vuoto non viene controllato.")))

    def current_validation(self) -> tuple[E.Validation | None, str | None]:
        """(regole, errore) dai campi della scheda Validazione."""
        nums = []
        for le, msg in ((self.val_min, _("Il minimo non è un numero.")),
                        (self.val_max, _("Il massimo non è un numero."))):
            text = le.text().strip()
            if not text:
                nums.append(None)
                continue
            v = fm.parse_number(text)
            if v is None:
                return None, msg
            nums.append(v)
        v = E.Validation(nums[0], nums[1], self.val_minlen.value(), self.val_regex.text(),
                         self.val_message.text().strip())
        return v, v.check()

    def _on_validation_changed(self) -> None:
        if self._loading or not self._fields:
            return
        v, err = self.current_validation()
        self._show_error(self.val_error, err)
        if err or v == self._loaded_val:
            return
        if self._apply({"validation": v}):
            self._loaded_val = v

    # .............................................................. Calcolo
    def _build_calc(self) -> None:
        form = self._page("calc", _("Calcolo"))
        modes = QWidget()
        mh = QHBoxLayout(modes)
        mh.setContentsMargins(0, 0, 0, 0)
        self.calc_mode_predefined = QRadioButton(_("Calcolo predefinito"))
        self.calc_mode_formula = QRadioButton(_("Formula"))
        self.calc_mode_group = QButtonGroup(self)
        self.calc_mode_group.addButton(self.calc_mode_predefined, 0)
        self.calc_mode_group.addButton(self.calc_mode_formula, 1)
        self.calc_mode_predefined.setChecked(True)
        mh.addWidget(self.calc_mode_predefined)
        mh.addWidget(self.calc_mode_formula)
        mh.addStretch(1)
        self.calc_mode_group.idClicked.connect(self._on_calc_mode)
        form.addRow(modes)
        self.calc_op = self._combo([(_("Nessuno"), None)] + [(_(label), op) for op, label in E.OPERATIONS.items()],
                                   lambda _v: self._on_calc_changed())
        form.addRow(_("Operazione"), self.calc_op)
        self.calc_filter = QLineEdit()
        self.calc_filter.setPlaceholderText(_("Filtra per nome"))
        self.calc_filter.setClearButtonEnabled(True)
        self.calc_filter.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.calc_filter.textChanged.connect(self._filter_calc)
        form.addRow(_("Campi"), self.calc_filter)
        self.calc_list = QListWidget()
        self.calc_list.setMinimumHeight(160)
        self.calc_list.itemChanged.connect(lambda _i: self._on_calc_changed())
        form.addRow(self.calc_list)
        bar = QHBoxLayout()
        bar.setSpacing(2)
        self.calc_select = QToolButton()
        self.calc_select.setToolTip(_("Seleziona i campi mostrati"))
        self.calc_select.setAccessibleName(_("Seleziona i campi mostrati"))
        bind_icon(self.calc_select, "list-checks")
        self.calc_select.clicked.connect(lambda: self._check_calc_items(True))
        self.calc_clear = QToolButton()
        self.calc_clear.setToolTip(_("Deseleziona tutti"))
        self.calc_clear.setAccessibleName(_("Deseleziona tutti"))
        bind_icon(self.calc_clear, "list-x")
        self.calc_clear.clicked.connect(lambda: self._check_calc_items(False))
        bar.addWidget(self.calc_select)
        bar.addWidget(self.calc_clear)
        bar.addStretch(1)
        bar_w = QWidget()
        bar_w.setLayout(bar)
        bar.setContentsMargins(0, 0, 0, 0)
        form.addRow(bar_w)
        # formula personalizzata (FRM-17)
        self.formula_edit = QLineEdit()
        self.formula_edit.setPlaceholderText(_("es. Imponibile * 1.22 - Sconto"))
        self.formula_edit.textChanged.connect(lambda _t: self._validate_formula_ui())
        self.formula_edit.textEdited.connect(self._on_formula_edited)
        self.formula_edit.editingFinished.connect(self._on_formula_apply)
        self.formula_completer = QCompleter(self)
        self.formula_completer.setModel(QStringListModel(self))
        self.formula_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.formula_completer.setWidget(self.formula_edit)
        self.formula_completer.activated[str].connect(self._insert_completion)
        form.addRow(_("Formula"), self.formula_edit)
        self.formula_error = self._error_label()
        form.addRow(self.formula_error)
        self.formula_apply_btn = QPushButton(_("Applica"))
        self.formula_apply_btn.clicked.connect(self._on_formula_apply)
        form.addRow(self.formula_apply_btn)
        self.formula_note = self._note(_("Operatori + − × ÷ e parentesi; i nomi con spazi vanno tra virgolette doppie."))
        form.addRow(self.formula_note)
        form.addRow(self._note(_("I campi vuoti valgono 0")))
        form.addRow(self._note(_("Conviene rendere il campo di sola lettura e dargli un formato numerico.")))
        self._calc_form = form
        self._predefined_rows = [self.calc_op, self.calc_filter, self.calc_list, bar_w]
        self._formula_rows = [self.formula_edit, self.formula_error, self.formula_apply_btn, self.formula_note]

    def _formula_mode(self) -> bool:
        return self.calc_mode_formula.isChecked()

    def _update_calc_mode_ui(self) -> None:
        on = self._formula_mode()
        for w in self._predefined_rows:
            self._calc_form.setRowVisible(w, not on)
        for w in self._formula_rows:
            self._calc_form.setRowVisible(w, on)
        if on:
            self._validate_formula_ui()
        else:
            self._show_error(self.formula_error, None)

    def _on_calc_mode(self, _id: int) -> None:
        if self._loading:
            return
        self._update_calc_mode_ui()
        if not self._formula_mode() and self._loaded_formula is not None:
            self._on_calc_changed()     # torna al calcolo predefinito: sostituisce la formula
        elif self._formula_mode() and self.formula_edit.text().strip():
            self._on_formula_apply()

    # ..... formula
    def check_formula(self, text: str) -> tuple[FX.Formula | None, str | None]:
        """(formula, errore) per l'espressione: sintassi, nomi esistenti, nessun riferimento a sé."""
        try:
            f = FX.parse(text)
        except FX.FormulaError as e:
            return None, _("Errore alla posizione {n}: {msg}").format(n=e.position + 1, msg=e.message)
        unknown = FX.check(f, set(self._all_names))
        if unknown:
            return None, _("Campi inesistenti: {names}").format(names=", ".join(dict.fromkeys(unknown)))
        own = {x.name for x in self._fields}
        if own & set(f.names):
            return None, _("La formula non può usare il campo stesso.")
        return f, None

    def _validate_formula_ui(self) -> bool:
        text = self.formula_edit.text()
        if not text.strip():
            self._show_error(self.formula_error, None)
            self.formula_apply_btn.setEnabled(False)
            return False
        _f, err = self.check_formula(text)
        self._show_error(self.formula_error, err)
        self.formula_apply_btn.setEnabled(err is None)
        return err is None

    def _on_formula_apply(self) -> None:
        if self._loading or not self._fields or not self._formula_mode():
            return
        text = self.formula_edit.text().strip()
        if not text:
            return
        f, err = self.check_formula(text)
        self._show_error(self.formula_error, err)
        if err or text == self._loaded_formula:
            return
        if self._apply({"formula": f.expression}):
            self._loaded_formula = f.expression
            self._loaded_calc = None

    def _word_before_cursor(self) -> tuple[int, str, bool]:
        """(inizio, testo, tra virgolette) della parola che si sta scrivendo."""
        text = self.formula_edit.text()[: self.formula_edit.cursorPosition()]
        if text.count('"') % 2:
            start = text.rfind('"')
            return start, text[start + 1:], True
        m = re.search(r"[^\W\d][\w.]*$", text)
        return (m.start(), m.group(0), False) if m else (len(text), "", False)

    def _on_formula_edited(self, _text: str) -> None:
        start, word, quoted = self._word_before_cursor()
        if not word and not quoted:
            self.formula_completer.popup().hide()
            return
        self.formula_completer.setCompletionPrefix(word)
        if self.formula_completer.completionCount():
            self.formula_completer.complete()
        else:
            self.formula_completer.popup().hide()

    def _insert_completion(self, name: str) -> None:
        start, _word, _quoted = self._word_before_cursor()
        le = self.formula_edit
        text, pos = le.text(), le.cursorPosition()
        token = name if _PLAIN_NAME.fullmatch(name) else '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'
        le.setText(text[:start] + token + text[pos:])
        le.setCursorPosition(start + len(token))

    def _filter_calc(self, text: str) -> None:
        needle = text.strip().casefold()
        for i in range(self.calc_list.count()):
            it = self.calc_list.item(i)
            it.setHidden(bool(needle) and needle not in it.text().casefold())

    def _check_calc_items(self, on: bool) -> None:
        self._loading = True
        try:
            for i in range(self.calc_list.count()):
                it = self.calc_list.item(i)
                if not on or not it.isHidden():
                    it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        finally:
            self._loading = False
        self._on_calc_changed()

    def current_calculation(self) -> E.Calculation | None:
        op = self.calc_op.currentData()
        if not op:
            return None
        names = [self.calc_list.item(i).text() for i in range(self.calc_list.count())
                 if self.calc_list.item(i).checkState() == Qt.CheckState.Checked]
        return E.Calculation(op, names)

    def _on_calc_changed(self) -> None:
        if self._loading or not self._fields or self._formula_mode():
            return
        self.calc_list.setEnabled(self.calc_op.currentData() is not None)
        calc = self.current_calculation()
        if calc == self._loaded_calc and self._loaded_formula is None:
            return
        if self._apply({"calc": calc} if calc else {"calc": None, "formula": None}):
            self._loaded_calc = calc
            self._loaded_formula = None

    # .............................................................. elenchi di campi spuntabili
    def _checklist(self, handler: Callable[[], None]) -> QListWidget:
        lw = QListWidget()
        lw.setMinimumHeight(140)
        lw.itemChanged.connect(lambda _i: None if self._loading else handler())
        return lw

    def _fill_checklist(self, lw: QListWidget, chosen: list[str]) -> None:
        own = {f.name for f in self._fields}
        lw.clear()
        seen: set[str] = set()
        for name in self._all_names + [n for n in chosen if n not in self._all_names]:
            if name in own or name in seen:
                continue
            seen.add(name)
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            lw.addItem(it)

    @staticmethod
    def _checked(lw: QListWidget) -> list[str]:
        return [lw.item(i).text() for i in range(lw.count())
                if lw.item(i).checkState() == Qt.CheckState.Checked]

    # .............................................................. Pulsante (FRM-18)
    def _build_button(self) -> None:
        form = self._page("button", _("Pulsante"))
        self.caption_edit = self._line(lambda t: self._apply({"caption": t}))
        form.addRow(_("Didascalia"), self.caption_edit)
        self.caption_down_edit = self._line(lambda t: self._apply({"caption_down": t}))
        self.caption_down_edit.setPlaceholderText(_("uguale alla didascalia"))
        form.addRow(_("Alla pressione"), self.caption_down_edit)
        self.caption_rollover_edit = self._line(lambda t: self._apply({"caption_rollover": t}))
        form.addRow(_("Al passaggio del puntatore"), self.caption_rollover_edit)
        self.layout_combo = self._combo(
            [(_("Solo testo"), "text"), (_("Solo icona"), "icon"), (_("Icona sopra il testo"), "icon_above"),
             (_("Icona sotto il testo"), "icon_below"), (_("Icona a sinistra del testo"), "icon_left"),
             (_("Icona a destra del testo"), "icon_right"), (_("Testo sopra l'icona"), "overlay")],
            lambda v: self._apply({"layout": v}))
        form.addRow(_("Disposizione"), self.layout_combo)
        self.highlight_combo = self._combo(
            [(_("Nessuno"), "none"), (_("Inversione dei colori"), "invert"), (_("Contorno"), "outline"),
             (_("Pressione"), "push")], lambda v: self._apply({"highlight": v}))
        form.addRow(_("Al clic"), self.highlight_combo)
        self.icon_rows: dict[str, tuple[QLabel, QToolButton, QToolButton]] = {}
        for state, label in (("normal", _("Icona")), ("rollover", _("Icona al passaggio")),
                             ("down", _("Icona alla pressione"))):
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            status = QLabel()
            choose = QToolButton()
            choose.setText(_("Scegli…"))
            choose.setToolTip(_("Immagine o pagina PDF da usare come icona"))
            choose.clicked.connect(lambda _c=False, st=state: self._choose_icon(st))
            remove = QToolButton()
            bind_icon(remove, "x")
            remove.setToolTip(_("Rimuovi l'icona"))
            remove.clicked.connect(lambda _c=False, st=state: self._apply({f"icon_{st}": None}))
            lay.addWidget(status, 1)
            lay.addWidget(choose)
            lay.addWidget(remove)
            form.addRow(label, row)
            self.icon_rows[state] = (status, choose, remove)
        self.image_note = self._note(_("Campo immagine: in compilazione un clic permette di scegliere l'immagine."))
        form.addRow(self.image_note)
        self.action_combo = self._combo(
            [(_("Nessuna"), None), (_("Reimposta campi"), "reset"), (_("Apri indirizzo web"), "uri"),
             (_("Stampa"), "print"), (_("Vai a pagina"), "goto"), (_("Mostra o nascondi campi"), "hide"),
             (_("Importa dati"), "import"), (_("Invia modulo"), "submit")], lambda _v: self._on_action_changed())
        form.addRow(_("Azione"), self.action_combo)
        self.event_combo = self._combo(
            [(_("Rilascio del pulsante"), "U"), (_("Pressione del pulsante"), "D"),
             (_("Ingresso del puntatore"), "E"), (_("Uscita del puntatore"), "X"),
             (_("Ricezione del focus"), "Fo"), (_("Perdita del focus"), "Bl")],
            lambda _v: self._on_action_changed())
        form.addRow(_("Quando"), self.event_combo)
        self.action_uri = QLineEdit()
        self.action_uri.setPlaceholderText("https://")
        self.action_uri.textChanged.connect(lambda _t: self._update_uri_warning())
        self.action_uri.editingFinished.connect(self._on_action_changed)
        form.addRow(_("Indirizzo"), self.action_uri)
        self.uri_warning = self._error_label()
        form.addRow(self.uri_warning)
        self.submit_format = self._combo([("FDF", "fdf"), ("XFDF", "xfdf"), ("HTML", "html"), ("PDF", "pdf")],
                                         lambda _v: self._on_action_changed())
        form.addRow(_("Formato dei dati"), self.submit_format)
        self.submit_empty = QCheckBox(_("Invia anche i campi vuoti"))
        self.submit_empty.toggled.connect(lambda _c: None if self._loading else self._on_action_changed())
        form.addRow(self.submit_empty)
        self.submit_note = self._note(_("Nessun campo scelto: si inviano tutti i campi. Con un indirizzo "
                                        "mailto: si apre il programma di posta con i dati allegati."))
        form.addRow(self.submit_note)
        self.action_page = self._spin(1, 99999, lambda _v: self._on_action_changed())
        form.addRow(_("Pagina"), self.action_page)
        self.action_hide = self._combo([(_("Nascondi"), True), (_("Mostra"), False)],
                                       lambda _v: self._on_action_changed())
        form.addRow(_("Effetto"), self.action_hide)
        self.action_fields = self._checklist(self._on_action_changed)
        form.addRow(_("Campi"), self.action_fields)
        self.reset_note = self._note(_("Nessun campo scelto: vengono reimpostati tutti i campi."))
        form.addRow(self.reset_note)
        self.action_error = self._error_label()
        form.addRow(self.action_error)
        self._button_form = form

    # scelta del file dell'icona: il modulo la sostituisce con la finestra del nucleo
    choose_icon_file: Callable[[], str | None] | None = None

    def _choose_icon(self, state: str) -> None:
        if self.choose_icon_file is not None:
            path = self.choose_icon_file()
        else:
            path, _f = QFileDialog.getOpenFileName(
                self, _("Scegli l'icona"), "",
                _("Immagini e PDF") + " (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.pdf)")
        if path:
            self._apply({f"icon_{state}": path})

    def _update_action_ui(self) -> None:
        t = self.action_combo.currentData() if self.action_combo.currentIndex() >= 0 else "?"
        form = self._button_form
        form.setRowVisible(self.action_uri, t in ("uri", "submit"))
        form.setRowVisible(self.uri_warning, t in ("uri", "submit"))
        form.setRowVisible(self.submit_format, t == "submit")
        form.setRowVisible(self.submit_empty, t == "submit")
        form.setRowVisible(self.submit_note, t == "submit")
        self.action_uri.setPlaceholderText(_("https:// o mailto:") if t == "submit" else "https://")
        form.setRowVisible(self.action_page, t == "goto")
        form.setRowVisible(self.action_hide, t == "hide")
        form.setRowVisible(self.action_fields, t in ("reset", "hide", "submit"))
        form.setRowVisible(self.reset_note, t == "reset")
        form.setRowVisible(self.event_combo, bool(t) and t != "?")
        self._update_uri_warning()

    def _update_uri_warning(self) -> None:
        uri = self.action_uri.text().strip()
        insecure = bool(uri) and not uri.lower().startswith(("https://", "mailto:"))
        self._show_error(self.uri_warning, _("L'indirizzo non è cifrato (non HTTPS)") if insecure else None)

    def current_action(self) -> tuple[dict | None, str | None]:
        """(azione, errore) dagli editor della scheda Pulsante; azione None = nessuna."""
        action, err = self._current_action()
        event = self.event_combo.currentData() or "U"
        if action is not None and event != "U":
            action["event"] = event
        return action, err

    def _current_action(self) -> tuple[dict | None, str | None]:
        t = self.action_combo.currentData()
        if not t:
            return None, None
        if t == "reset":
            return {"type": "reset", "fields": self._checked(self.action_fields)}, None
        if t == "uri":
            uri = self.action_uri.text().strip()
            return ({"type": "uri", "uri": uri}, None) if uri else (None, "")
        if t == "goto":
            return {"type": "goto", "page": self.action_page.value() - 1}, None
        if t == "submit":
            url = self.action_uri.text().strip()
            if not url:
                return None, ""
            if not url.lower().startswith(("https://", "http://", "mailto:")):
                return None, _("Indirizzo non valido: usa https://, http:// o mailto:.")
            return {"type": "submit", "url": url, "format": self.submit_format.currentData() or "fdf",
                    "fields": self._checked(self.action_fields), "empty": self.submit_empty.isChecked()}, None
        if t == "hide":
            names = self._checked(self.action_fields)
            if not names:
                return None, _("Scegli almeno un campo.")
            return {"type": "hide", "fields": names, "hide": bool(self.action_hide.currentData())}, None
        return {"type": t}, None

    def _on_action_changed(self) -> None:
        if self._loading or not self._fields or self.action_combo.currentIndex() < 0:
            return
        self._update_action_ui()
        action, err = self.current_action()
        self._show_error(self.action_error, err or None)
        if err is not None or action == self._loaded_action:
            return
        if self._apply({"action": action}):
            self._loaded_action = action

    def _load_button(self) -> None:
        form = self._button_form
        image = any(f.image for f in self._fields)
        form.setRowVisible(self.caption_edit, not image)
        form.setRowVisible(self.caption_down_edit, not image)
        form.setRowVisible(self.caption_rollover_edit, not image)
        form.setRowVisible(self.layout_combo, not image)
        form.setRowVisible(self.highlight_combo, not image)
        for status, _c, _r in self.icon_rows.values():
            form.setRowVisible(status.parentWidget(), not image)
        form.setRowVisible(self.image_note, image)
        if not image:
            self._load_line(self.caption_edit, "caption")
            self._load_line(self.caption_down_edit, "caption_down")
            if not self.caption_down_edit.placeholderText():
                self.caption_down_edit.setPlaceholderText(_("uguale alla didascalia"))
            self._load_line(self.caption_rollover_edit, "caption_rollover")
            if not self.caption_rollover_edit.placeholderText():
                self.caption_rollover_edit.setPlaceholderText(_("uguale alla didascalia"))
            layout, mixed = self._common("layout")
            self._load_combo(self.layout_combo, layout, mixed)
            hl, mixed = self._common("highlight")
            self._load_combo(self.highlight_combo, hl, mixed)
            for state, (status, _choose, remove) in self.icon_rows.items():
                have = [state in f.icons for f in self._fields]
                status.setText(_("misto") if len(set(have)) > 1 else (_("impostata") if have[0] else _("nessuna")))
                remove.setEnabled(any(have))
        self._show_error(self.action_error, None)
        action, mixed = self._common("action")
        action = dict(action or {})
        t = action.get("type") or None
        known = t in _ACTION_TYPES
        # "?" = valore misto o non modificabile: qualunque scelta va applicata
        self._loaded_action = "?" if mixed or not known else (self._normal_action(action) if t else None)
        self._load_combo(self.action_combo, t, mixed or not known)
        if not mixed and not known:
            self.action_combo.setPlaceholderText(_("Altra azione (non modificabile qui)"))
        self.action_uri.setText(str(action.get("uri") or "") if t == "uri" else
                                (str(action.get("url") or "") if t == "submit" else ""))
        self.submit_format.setCurrentIndex(max(0, self.submit_format.findData(action.get("format") or "fdf")))
        self.submit_empty.setChecked(bool(action.get("empty")))
        self.action_page.setValue(int(action.get("page") or 0) + 1 if t == "goto" else 1)
        self.action_hide.setCurrentIndex(self.action_hide.findData(bool(action.get("hide", True))))
        self.event_combo.setCurrentIndex(max(0, self.event_combo.findData(action.get("event") or "U")))
        self._fill_checklist(self.action_fields,
                             list(action.get("fields") or []) if t in ("reset", "hide", "submit") else [])
        self._update_action_ui()

    @classmethod
    def _normal_action(cls, action: dict) -> dict:
        """Azione letta dal documento nella stessa forma prodotta da `current_action`."""
        out = cls._normal_action_body(action)
        if action.get("event") and action.get("event") != "U":
            out["event"] = action["event"]
        return out

    @staticmethod
    def _normal_action_body(action: dict) -> dict:
        t = action.get("type")
        if t == "reset":
            return {"type": t, "fields": list(action.get("fields") or [])}
        if t == "uri":
            return {"type": t, "uri": str(action.get("uri") or "")}
        if t == "goto":
            return {"type": t, "page": int(action.get("page") or 0)}
        if t == "submit":
            return {"type": t, "url": str(action.get("url") or ""), "format": action.get("format") or "fdf",
                    "fields": list(action.get("fields") or []), "empty": bool(action.get("empty"))}
        if t == "hide":
            return {"type": t, "fields": list(action.get("fields") or []), "hide": bool(action.get("hide", True))}
        return {"type": t}

    # .............................................................. Firma (FRM-20)
    def _build_signature(self) -> None:
        form = self._page("signature", _("Firma"))
        self.lock_combo = self._combo(
            [(_("Nessun campo"), None), (_("Tutti i campi"), "All"), (_("Solo questi campi"), "Include"),
             (_("Tutti tranne questi"), "Exclude")], lambda _v: self._on_lock_changed())
        form.addRow(_("Dopo la firma blocca"), self.lock_combo)
        self.lock_fields = self._checklist(self._on_lock_changed)
        form.addRow(self.lock_fields)
        self.lock_error = self._error_label()
        form.addRow(self.lock_error)
        form.addRow(self._note(_("Vincoli per chi firma")))
        self.sv_reasons = self._line(lambda _t: self._on_sv_changed())
        self.sv_reasons.setPlaceholderText(_("qualunque motivo"))
        self.sv_reasons.setToolTip(_("Motivi ammessi, separati da punto e virgola"))
        form.addRow(_("Motivi ammessi"), self.sv_reasons)
        self.sv_timestamp = self._line(lambda _t: self._on_sv_changed())
        self.sv_timestamp.setPlaceholderText(_("nessuna"))
        self.sv_timestamp.setToolTip(_("Indirizzo del server di marca temporale da usare obbligatoriamente"))
        form.addRow(_("Marca temporale"), self.sv_timestamp)
        self.sv_certify = self._combo(
            [(_("Non richiesta"), 0), (_("Nessuna modifica consentita"), 1), (_("Consenti compilazione e firme"), 2),
             (_("Consenti anche i commenti"), 3)], lambda _v: self._on_sv_changed())
        form.addRow(_("Certificazione"), self.sv_certify)
        self._signature_form = form

    def current_sv(self) -> dict:
        reasons = [r.strip() for r in self.sv_reasons.text().split(";") if r.strip()]
        out: dict = {}
        if reasons:
            out["reasons"] = reasons
        if self.sv_timestamp.text().strip():
            out["timestamp"] = self.sv_timestamp.text().strip()
        if self.sv_certify.currentData():
            out["certify"] = int(self.sv_certify.currentData())
        return out

    def _on_sv_changed(self) -> None:
        if self._loading or not self._fields:
            return
        sv = self.current_sv()
        if sv != self._loaded_sv and self._apply({"sv": sv}):
            self._loaded_sv = sv

    def current_lock(self) -> tuple[dict | None, str | None]:
        action = self.lock_combo.currentData()
        if not action:
            return None, None
        if action == "All":
            return {"action": "All", "fields": []}, None
        names = self._checked(self.lock_fields)
        if not names:
            return None, _("Scegli almeno un campo.")
        return {"action": action, "fields": names}, None

    def _on_lock_changed(self) -> None:
        if self._loading or not self._fields or self.lock_combo.currentIndex() < 0:
            return
        self.lock_fields.setEnabled(self.lock_combo.currentData() in ("Include", "Exclude"))
        lock, err = self.current_lock()
        self._show_error(self.lock_error, err)
        if err or lock == self._loaded_lock:
            return
        if self._apply({"lock": lock}):
            self._loaded_lock = lock

    def _load_signature(self) -> None:
        lock, mixed = self._common("lock")
        lock = dict(lock or {})
        action = lock.get("action") if lock.get("action") in ("All", "Include", "Exclude") else None
        fields = list(lock.get("fields") or []) if action in ("Include", "Exclude") else []
        self._loaded_lock = "?" if mixed else ({"action": action, "fields": fields} if action else None)
        self._load_combo(self.lock_combo, action, mixed)
        self._fill_checklist(self.lock_fields, fields)
        self.lock_fields.setEnabled(mixed or action in ("Include", "Exclude"))
        self._show_error(self.lock_error, None)
        sv, mixed_sv = self._common("sv")
        sv = dict(sv or {})
        self._loaded_sv = None if mixed_sv else sv
        self._load_line(self.sv_reasons, value="; ".join(sv.get("reasons") or []), mixed=mixed_sv)
        self._load_line(self.sv_timestamp, value=sv.get("timestamp") or "", mixed=mixed_sv)
        if not mixed_sv:
            self.sv_reasons.setPlaceholderText(_("qualunque motivo"))
            self.sv_timestamp.setPlaceholderText(_("nessuna"))
        self._load_combo(self.sv_certify, int(sv.get("certify") or 0), mixed_sv)

    # ------------------------------------------------------------ applicazione
    def _label(self) -> str:
        return _("Proprietà del campo") if len(self._fields) == 1 else _("Proprietà dei campi")

    def _apply(self, props: dict, label: str | None = None) -> bool:
        if self._loading or not self._fields:
            return False
        ok = bool(self._apply_cb([f.key for f in self._fields], props, label or self._label()))
        if not ok:
            self._reload()
        return ok

    def _reload(self) -> None:
        self._load()

    def _show_error(self, lab: QLabel, text: str | None) -> None:
        if text:
            lab.setText(text)
            lab.setStyleSheet(f"color: {theme.hex_color('danger')};")
            lab.show()
        else:
            lab.clear()
            lab.hide()

    # ------------------------------------------------------------ caricamento
    def _common(self, attr: str) -> tuple[Any, bool]:
        vals = [getattr(f, attr) for f in self._fields]
        return vals[0], any(v != vals[0] for v in vals[1:])

    def _load_check(self, cb: QCheckBox, attr: str) -> None:
        v, mixed = self._common(attr)
        cb.setEnabled(True)
        if mixed:
            cb.setTristate(True)
            cb.setCheckState(Qt.CheckState.PartiallyChecked)
        else:
            cb.setTristate(False)
            cb.setChecked(bool(v))

    def _load_combo(self, cb: QComboBox, value, mixed: bool, other: str = "") -> None:
        i = -1 if mixed else cb.findData(value)
        cb.setCurrentIndex(i)
        cb.setPlaceholderText(_("misto") if mixed else other)

    def _load_line(self, le: QLineEdit, attr: str | None = None, value=None, mixed: bool = False) -> None:
        if attr is not None:
            value, mixed = self._common(attr)
        text = "" if mixed or value is None else str(value)
        le.setText(text)
        le.setPlaceholderText(_("misto") if mixed else "")
        self._loaded[id(le)] = None if mixed else text

    def _load_spin(self, sp: QSpinBox, attr: str) -> None:
        v, mixed = self._common(attr)
        sp.setSuffix(_(" (misto)") if mixed else "")
        sp.setValue(int(round(float(v or 0))))

    def _load(self) -> None:
        self._loading = True
        try:
            self._load_all()
        finally:
            self._loading = False

    def _load_all(self) -> None:
        fields = self._fields
        for lab in (self.items_error, self.fmt_error, self.val_error, self.formula_error, self.action_error,
                    self.lock_error, self.uri_warning):
            self._show_error(lab, None)
        if not fields:
            self.title.hide()
            self.tabs.hide()
            self.placeholder.show()
            return
        self.placeholder.hide()
        self.title.show()
        self.tabs.show()
        kinds = {f.kind for f in fields}
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        self._kind = kind
        single = len(fields) == 1
        if single:
            self.title.setText(_("{kind} «{name}»").format(kind=E.kind_label(fields[0].kind), name=fields[0].name))
        else:
            self.title.setText(_("{n} campi selezionati").format(n=len(fields)))
        self._load_general(kind, single)
        self._load_appearance(kinds)
        self._load_options(kind, single)
        is_text = kind == E.TEXT
        for key in ("format", "validation", "calc"):
            self._set_tab(key, is_text)
        if is_text:
            self._load_format()
            self._load_validation()
            self._load_calc()
        self._set_tab("button", kind == E.BUTTON)
        if kind == E.BUTTON:
            self._load_button()
        self._set_tab("signature", kind == E.SIGNATURE)
        if kind == E.SIGNATURE:
            self._load_signature()

    def _load_general(self, kind: str | None, single: bool) -> None:
        form = self._general_form
        form.setRowVisible(self.name_edit, single)
        if single:
            self.name_edit.setText(self._fields[0].name)
        self._load_line(self.tooltip_edit, "tooltip")
        self._load_line(self.export_name_edit, "export_name")
        show_edit = kind == E.TEXT
        show_check = kind == E.CHECKBOX
        show_combo = single and kind in (E.RADIO, E.COMBO, E.LIST)
        form.setRowVisible(self.default_edit, show_edit)
        form.setRowVisible(self.default_check, show_check)
        form.setRowVisible(self.default_combo, show_combo)
        if show_edit:
            self._load_line(self.default_edit, "default")
        if show_check:
            vals = [f.default not in (None, "", E.OFF, False) for f in self._fields]
            mixed = any(v != vals[0] for v in vals)
            self.default_check.setTristate(mixed)
            self.default_check.setCheckState(Qt.CheckState.PartiallyChecked if mixed else
                                             (Qt.CheckState.Checked if vals[0] else Qt.CheckState.Unchecked))
        if show_combo:
            f = self._fields[0]
            cb = self.default_combo
            cb.clear()
            if kind == E.RADIO:
                cb.addItem(_("Nessuna"), "")
                exports = list(self._radio_exports.get(f.name) or [])
                for e in [f.export] + ([f.default] if f.default not in (None, "", E.OFF) else []):
                    if e and e not in exports:
                        exports.append(e)
                for e in exports:
                    cb.addItem(e, e)
                current = "" if f.default in (None, E.OFF) else f.default
            else:
                cb.addItem(_("Nessuna"), "")
                for e, d in zip(f.exports, f.options):
                    cb.addItem(d if d == e else f"{d} ({e})", e)
                current = f.default[0] if isinstance(f.default, list) and f.default else (f.default or "")
                if isinstance(current, list):
                    current = ""
            self._load_combo(cb, current, False)
        for cb, attr in ((self.required_check, "required"), (self.readonly_check, "readonly"),
                         (self.no_export_check, "no_export"), (self.locked_check, "locked")):
            self._load_check(cb, attr)
        v, mixed = self._common("visibility")
        self._load_combo(self.visibility_combo, v, mixed)

    def _load_appearance(self, kinds: set[str]) -> None:
        form = self._appearance_form
        form.setRowVisible(self.font_combo, not kinds & set(_MARKED))
        v, mixed = self._common("font")
        self._load_combo(self.font_combo, v, mixed, other=str(v))
        self._load_spin(self.size_spin, "fontsize")
        for btn, attr in ((self.text_color_btn, "text_color"), (self.border_color_btn, "border_color"),
                          (self.fill_color_btn, "fill_color")):
            c, mixed = self._common(attr)
            btn.set_color(c, mixed)
        w, mixed = self._common("border_width")
        self._load_combo(self.border_width_combo, float(w or 0), mixed,
                         other=_("{w} pt").format(w=f"{float(w or 0):g}"))
        s, mixed = self._common("border_style")
        self._load_combo(self.border_style_combo, s, mixed)
        show_align = kinds <= set(_TEXT_LIKE)
        form.setRowVisible(self.align_row, show_align)
        a, mixed = self._common("align")
        self.align_group.setExclusive(False)
        for i, b in enumerate(self.align_buttons):
            b.setChecked(not mixed and i == a)
        self.align_group.setExclusive(True)
        r, mixed = self._common("rotation")
        self._load_combo(self.rotation_combo, int(r or 0) % 360, mixed)
        self.set_default_btn.setEnabled(self._kind is not None and self._set_default_cb is not None)

    def _load_options(self, kind: str | None, single: bool) -> None:
        form = self._options_form
        visible = kind in (E.TEXT, E.CHECKBOX, E.RADIO, E.COMBO, E.LIST)
        self._set_tab("options", visible)
        if not visible:
            return
        for w in self._text_rows:
            form.setRowVisible(w, kind == E.TEXT)
        for w in self._mark_rows:
            form.setRowVisible(w, kind in _MARKED)
        for w in self._choice_rows:
            form.setRowVisible(w, kind in (E.COMBO, E.LIST))
        if kind == E.TEXT:
            for cb, attr in ((self.multiline_check, "multiline"), (self.password_check, "password"),
                             (self.scroll_check, "do_not_scroll"), (self.spell_check, "no_spell"),
                             (self.comb_check, "comb")):
                self._load_check(cb, attr)
            self._load_spin(self.maxlen_spin, "maxlen")
            self._update_text_constraints()
        elif kind in _MARKED:
            v, mixed = self._common("check_style")
            self._load_combo(self.check_style_combo, v, mixed)
            form.setRowVisible(self.export_edit, single)
            if single:
                self._load_line(self.export_edit, "export")
            form.setRowVisible(self.no_toggle_off_check, kind == E.RADIO)
            form.setRowVisible(self.in_unison_check, kind == E.RADIO)
            if kind == E.RADIO:
                self._load_check(self.no_toggle_off_check, "no_toggle_off")
                self._load_check(self.in_unison_check, "in_unison")
        else:
            form.setRowVisible(self.items_box, single)
            if single:
                f = self._fields[0]
                pairs = list(zip(f.exports, f.options))
                self._set_table(pairs)
                self._loaded_opts = pairs
            self._load_check(self.sort_check, "sort")
            self._load_check(self.commit_check, "commit_on_change")
            form.setRowVisible(self.editable_check, kind == E.COMBO)
            form.setRowVisible(self.multiselect_check, kind == E.LIST)
            if kind == E.COMBO:
                self._load_check(self.editable_check, "editable")
            else:
                self._load_check(self.multiselect_check, "multiselect")

    def _load_format(self) -> None:
        f, mixed = self._common("fmt")
        self._loaded_fmt = None if mixed else f
        self._load_combo(self.fmt_category, f.type, mixed)
        self.fmt_decimals.setValue(max(0, min(10, int(f.decimals))))
        self.fmt_separator.setCurrentIndex(max(0, self.fmt_separator.findData(f.separator)))
        self.fmt_negative.setCurrentIndex(max(0, self.fmt_negative.findData(f.negative)))
        self.fmt_currency.setText(f.currency)
        self.fmt_currency_before.setTristate(False)
        self.fmt_currency_before.setChecked(f.currency_before)
        date_p = f.pattern if f.type == fm.DATE else fm.default_date_pattern()
        i = self.fmt_date.findData(date_p)
        if i < 0:
            self.fmt_date.addItem(fm.pattern_label(date_p), date_p)
            i = self.fmt_date.count() - 1
        self.fmt_date.setCurrentIndex(i)
        time_p = f.pattern if f.type == fm.TIME else fm.TIME_PATTERNS[0]
        i = self.fmt_time.findData(time_p)
        if i < 0:
            self.fmt_time.addItem(time_p, time_p)
            i = self.fmt_time.count() - 1
        self.fmt_time.setCurrentIndex(i)
        self.fmt_mask.setCurrentText(f.mask or fm.SPECIAL_MASKS[0])
        self._update_format_ui()

    def _load_validation(self) -> None:
        v, mixed = self._common("validation")
        self._loaded_val = None if mixed else v
        self.val_min.setText("" if v.minimum is None else fm.canonical_number(v.minimum))
        self.val_max.setText("" if v.maximum is None else fm.canonical_number(v.maximum))
        self.val_minlen.setSuffix(_(" (misto)") if mixed else "")
        self.val_minlen.setValue(int(v.min_length or 0))
        self.val_regex.setText(v.regex)
        self.val_message.setText(v.message)

    def _load_calc(self) -> None:
        calc, mixed = self._common("calc")
        self._loaded_calc = None if mixed else calc
        self._load_combo(self.calc_op, calc.op if calc else None, mixed)
        own = {f.name for f in self._fields}
        chosen = set(calc.fields) if calc else set()
        self.calc_list.clear()
        seen = set()
        for name in self._all_names + [n for n in (calc.fields if calc else []) if n not in self._all_names]:
            if name in own or name in seen:
                continue
            seen.add(name)
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
            self.calc_list.addItem(it)
        self._filter_calc(self.calc_filter.text())
        self.calc_list.setEnabled(mixed or calc is not None)
        formula, fmixed = self._common("formula")
        self._loaded_formula = None if fmixed or formula is None else formula.expression
        self.formula_edit.setText(self._loaded_formula or "")
        self.formula_edit.setPlaceholderText(_("misto") if fmixed else _("es. Imponibile * 1.22 - Sconto"))
        (self.calc_mode_formula if formula is not None and not mixed else self.calc_mode_predefined).setChecked(True)
        names = [n for n in self._all_names if n not in own]
        self.formula_completer.model().setStringList(names)
        self._update_calc_mode_ui()
