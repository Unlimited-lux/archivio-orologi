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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QPixmap, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QFormLayout, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QScrollArea, QSizePolicy, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from foliant.api import _, bind_icon, icon, language, theme

from . import engine as E
from . import formats as fm

TRANSLATIONS: dict[str, str] = {
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
    "Elenco": "List box",
    "Pulsante": "Button",
    "Campo firma": "Signature field",
    "Spunta": "Check",
    "Cerchio": "Circle",
    "Croce": "Cross",
    "Rombo": "Diamond",
    "Quadrato": "Square",
    "Stella": "Star",
}

Apply = Callable[[list[tuple[int, int]], dict, str], bool]

_TEXT_LIKE = (E.TEXT, E.COMBO, E.LIST)
_MARKED = (E.CHECKBOX, E.RADIO)


# ====================================================================== colori
def _qcolor(c) -> QColor:
    return QColor.fromRgbF(*(max(0.0, min(1.0, float(v))) for v in c[:3]))


def _tuple(q: QColor) -> tuple:
    return (round(q.redF(), 4), round(q.greenF(), 4), round(q.blueF(), 4))


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

    def __init__(self, apply: Apply, parent=None):
        super().__init__(parent)
        self._apply_cb = apply
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
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)
        lay.addWidget(self.tabs, 1)
        self._pages: dict[str, QWidget] = {}

        self._build_general()
        self._build_appearance()
        self._build_options()
        self._build_format()
        self._build_validation()
        self._build_calc()
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
        self._appearance_form = form

    def _on_align(self, value: int) -> None:
        if not self._loading:
            self._apply({"align": value})

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
        form.addRow(bar)
        form.addRow(self._note(_("I campi vuoti valgono 0")))
        form.addRow(self._note(_("Conviene rendere il campo di sola lettura e dargli un formato numerico.")))

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
        if self._loading or not self._fields:
            return
        self.calc_list.setEnabled(self.calc_op.currentData() is not None)
        calc = self.current_calculation()
        if calc == self._loaded_calc:
            return
        if self._apply({"calc": calc}):
            self._loaded_calc = calc

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
        for lab in (self.items_error, self.fmt_error, self.val_error):
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

    def _load_general(self, kind: str | None, single: bool) -> None:
        form = self._general_form
        form.setRowVisible(self.name_edit, single)
        if single:
            self.name_edit.setText(self._fields[0].name)
        self._load_line(self.tooltip_edit, "tooltip")
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
        date_p = f.pattern if f.type == fm.DATE else fm.DATE_PATTERNS[0]
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
