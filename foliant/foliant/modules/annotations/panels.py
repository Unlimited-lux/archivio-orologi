"""Pannelli del modulo commenti: proprietà (destra) ed elenco commenti (sinistra)."""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
    QPushButton, QSlider, QSpinBox, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import _, bind_icon, theme

from . import dialogs, engine, ui_actions

if TYPE_CHECKING:
    from .controller import Controller

STATE_LABELS = {
    engine.STATE_NONE: "Nessuno", engine.STATE_ACCEPTED: "Accettato", engine.STATE_REJECTED: "Rifiutato",
    engine.STATE_CANCELLED: "Annullato", engine.STATE_COMPLETED: "Completato",
}

KIND_ICONS = {
    engine.HIGHLIGHT: "highlighter", engine.UNDERLINE: "underline", engine.STRIKEOUT: "strikethrough",
    engine.SQUIGGLY: "spell-check", engine.NOTE: "sticky-note", engine.FREETEXT: "type",
    engine.CALLOUT: "message-square-quote", engine.RECT: "square", engine.ELLIPSE: "circle", engine.LINE: "minus",
    engine.ARROW: "move-up-right", engine.POLYGON: "pentagon", engine.POLYLINE: "spline", engine.CLOUD: "cloud",
    engine.INK: "pencil", engine.STAMP: "stamp", engine.ATTACHMENT: "paperclip", engine.DISTANCE: "ruler",
    engine.PERIMETER: "ruler", engine.AREA: "land-plot", engine.TYPEWRITER: "text-cursor-input",
    engine.CARET: "text-cursor", engine.REPLACE: "replace",
}

DASH_CHOICES = [("solid", "Continua"), ("dashed", "Tratteggiata"), ("dotted", "Punteggiata")]
LINE_END_CHOICES = [("None", "Nessuna"), ("OpenArrow", "Freccia aperta"), ("ClosedArrow", "Freccia chiusa"),
                    ("Circle", "Cerchio"), ("Square", "Quadrato"), ("Diamond", "Rombo"), ("Butt", "Barra"),
                    ("Slash", "Barra obliqua")]
NOTE_ICON_LABELS = {"Comment": "Commento", "Note": "Nota", "Key": "Chiave", "Help": "Aiuto",
                    "Paragraph": "Paragrafo", "NewParagraph": "Nuovo paragrafo", "Insert": "Inserisci"}


def swatch(color, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if color:
        p.setBrush(QColor.fromRgbF(*engine._rgb(color)))
        p.setPen(theme.color("border"))
        p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    else:
        p.setPen(theme.color("text_muted"))
        p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
        p.drawLine(2, size - 2, size - 2, 2)
    p.end()
    return QIcon(pm)


class ColorButton(QPushButton):
    """Pulsante colore con tavolozza; `allow_none` aggiunge «Nessuno»."""

    PALETTE = [(1, 1, 0), (1, 0.6, 0), (1, 0, 0), (0.9, 0.2, 0.6), (0.55, 0.2, 0.75), (0, 0, 1),
               (0, 0.6, 0.9), (0, 0.65, 0.3), (0.5, 0.8, 0.2), (0, 0, 0), (0.5, 0.5, 0.5), (1, 1, 1)]

    def __init__(self, allow_none: bool = False, parent=None):
        super().__init__(parent)
        self.allow_none = allow_none
        self._color = None
        self.callback = None
        menu = QMenu(self)
        for c in self.PALETTE:
            a = menu.addAction(swatch(c), engine.color_to_hex(c))
            a.triggered.connect(lambda _c=False, col=c: self._pick(col))
        menu.addSeparator()
        if allow_none:
            menu.addAction(swatch(None), _("Nessuno"), lambda: self._pick(None))
        menu.addAction(_("Altro colore…"), self._custom)
        self.setMenu(menu)
        self._refresh()

    def color(self):
        return self._color

    def set_color(self, c) -> None:
        self._color = engine._rgb(c) if c else None
        self._refresh()

    def _refresh(self):
        self.setIcon(swatch(self._color, 16))
        self.setText(engine.color_to_hex(self._color) if self._color else _("Nessuno"))

    def _pick(self, c):
        self.set_color(c)
        if self.callback:
            self.callback(self._color)

    def _custom(self):
        c = dialogs.ask_color(self, _("Scegli colore"), self._color)
        if c is not None:
            self._pick(c)


class PropertiesPanel(QWidget):
    """Proprietà dell'annotazione selezionata o dello strumento attivo (ANN-04)."""

    def __init__(self, ctrl: "Controller"):
        super().__init__()
        self.ctrl = ctrl
        self._loading = False
        self.setMinimumWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.title = QLabel(_("Proprietà"))
        f = self.title.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.1)
        self.title.setFont(f)
        lay.addWidget(self.title)
        self.subtitle = QLabel("")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(f"color:{theme.hex_color('text_muted')}")
        lay.addWidget(self.subtitle)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form = form
        self.color = ColorButton()
        self.color.callback = lambda c: self._changed("color", c)
        self.fill = ColorButton(allow_none=True)
        self.fill.callback = lambda c: self._changed("fill", c)
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity_label = QLabel("100%")
        self.opacity.valueChanged.connect(lambda v: self.opacity_label.setText(f"{v}%"))
        self.opacity.sliderReleased.connect(lambda: self._changed("opacity", self.opacity.value() / 100))
        self.opacity.valueChanged.connect(self._opacity_key)
        op_row = QHBoxLayout()
        op_row.addWidget(self.opacity, 1)
        op_row.addWidget(self.opacity_label)
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 12)
        self.width.setSingleStep(0.25)
        self.width.setSuffix(" pt")
        self.width.valueChanged.connect(lambda v: self._changed("width", v))
        self.dash = QComboBox()
        for key, label in DASH_CHOICES:
            self.dash.addItem(_(label), key)
        self.dash.currentIndexChanged.connect(lambda _i: self._changed("dash", self.dash.currentData()))
        self.cloud = QCheckBox(_("Bordo a nuvola"))
        self.cloud.toggled.connect(lambda v: self._changed("cloud", v))
        self.fontsize = QSpinBox()
        self.fontsize.setRange(4, 144)
        self.fontsize.setSuffix(" pt")
        self.fontsize.valueChanged.connect(lambda v: self._changed("fontsize", float(v)))
        self.text_color = ColorButton()
        self.text_color.callback = lambda c: self._changed("text_color", c)
        self.align = QComboBox()
        for label in ("Sinistra", "Centro", "Destra"):
            self.align.addItem(_(label))
        self.align.currentIndexChanged.connect(lambda i: self._changed("align", i))
        self.icon_combo = QComboBox()
        for key, label in NOTE_ICON_LABELS.items():
            self.icon_combo.addItem(_(label), key)
        self.icon_combo.currentIndexChanged.connect(lambda _i: self._changed("icon", self.icon_combo.currentData()))
        self.start_end = QComboBox()
        self.end_end = QComboBox()
        for combo in (self.start_end, self.end_end):
            for key, label in LINE_END_CHOICES:
                combo.addItem(_(label), key)
        self.start_end.currentIndexChanged.connect(lambda _i: self._changed("line_ends", None))
        self.end_end.currentIndexChanged.connect(lambda _i: self._changed("line_ends", None))
        self.author = QLineEdit()
        self.author.editingFinished.connect(self._author_changed)
        self.locked = QCheckBox(_("Bloccata"))
        self.locked.toggled.connect(lambda v: self._flag(engine.FLAG_LOCKED, v))
        self.printable = QCheckBox(_("Stampa"))
        self.printable.toggled.connect(lambda v: self._flag(engine.FLAG_PRINT, v))
        self.read_only = QCheckBox(_("Sola lettura"))
        self.read_only.toggled.connect(lambda v: self._flag(engine.FLAG_READ_ONLY, v))
        self.locked_contents = QCheckBox(_("Contenuto bloccato"))
        self.locked_contents.toggled.connect(lambda v: self._flag(engine.FLAG_LOCKED_CONTENTS, v))
        self.rows: dict[str, QWidget] = {}
        for key, label, widget in (
                ("color", _("Colore"), self.color), ("fill", _("Riempimento"), self.fill),
                ("opacity", _("Opacità"), op_row), ("width", _("Spessore"), self.width),
                ("dash", _("Stile linea"), self.dash), ("cloud", "", self.cloud),
                ("start_end", _("Estremità iniziale"), self.start_end),
                ("end_end", _("Estremità finale"), self.end_end),
                ("fontsize", _("Dimensione testo"), self.fontsize),
                ("text_color", _("Colore testo"), self.text_color), ("align", _("Allineamento"), self.align),
                ("icon", _("Icona"), self.icon_combo), ("author", _("Autore"), self.author),
                ("locked", "", self.locked), ("locked_contents", "", self.locked_contents),
                ("read_only", "", self.read_only), ("printable", "", self.printable)):
            form.addRow(label, widget)
            self.rows[key] = widget
        lay.addLayout(form)
        self.default_btn = QPushButton(_("Imposta come predefinito"))
        self.default_btn.clicked.connect(self._make_default)
        lay.addWidget(self.default_btn)
        fav_row = QHBoxLayout()
        self.favorites = QComboBox()
        self.favorites.setToolTip(_("Stili preferiti"))
        self.favorites.activated.connect(self._use_favorite)
        self.save_fav = QToolButton()
        bind_icon(self.save_fav, "star")
        self.save_fav.setToolTip(_("Salva come stile preferito…"))
        self.save_fav.clicked.connect(self._save_favorite)
        fav_row.addWidget(self.favorites, 1)
        fav_row.addWidget(self.save_fav)
        self.fav_box = QWidget()
        self.fav_box.setLayout(fav_row)
        fav_row.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.fav_box)
        ctrl.stylesChanged.connect(lambda _k: self._fill_favorites())
        lay.addStretch(1)
        ctrl.selectionChanged.connect(self.refresh)
        ctrl.toolKindChanged.connect(lambda _k: self.refresh())
        ctrl.ctx.documentChanged.connect(lambda _v: QTimer.singleShot(0, self.refresh))
        self.refresh()

    def set_view(self, view) -> None:
        self.refresh()

    def _opacity_key(self, v):
        if not self.opacity.isSliderDown() and not self._loading:
            self._changed("opacity", v / 100)

    def _show(self, keys: set[str]) -> None:
        for key, widget in self.rows.items():
            visible = key in keys
            self.form.setRowVisible(widget if not isinstance(widget, QHBoxLayout) else widget, visible)

    def _target_kinds(self) -> list[str]:
        if self.ctrl.selection:
            return [i.kind for i in self.ctrl.selected_infos()]
        return [self.ctrl.tool_kind] if self.ctrl.tool_kind else []

    @staticmethod
    def _keys_for(kind: str) -> set[str]:
        base = {"color", "opacity"}
        if kind in (engine.RECT, engine.ELLIPSE, engine.POLYGON, engine.CLOUD, engine.AREA):
            return base | {"fill", "width", "dash", "cloud"}
        if kind in (engine.LINE, engine.ARROW, engine.POLYLINE, engine.DISTANCE, engine.PERIMETER):
            return base | {"width", "dash", "start_end", "end_end"} if kind in (engine.LINE, engine.ARROW) else \
                base | {"width", "dash"}
        if kind in (engine.INK, "marker"):
            return base | {"width"}
        if kind in (engine.FREETEXT, engine.CALLOUT, engine.TYPEWRITER):
            return {"color", "fill", "opacity", "width", "dash", "fontsize", "text_color", "align"}
        if kind == engine.NOTE:
            return base | {"icon"}
        if kind == engine.STAMP:
            return base
        if kind == "eraser":
            return set()
        return base

    def refresh(self) -> None:
        self._loading = True
        try:
            infos = self.ctrl.selected_infos() if self.ctrl.selection else []
            if infos:
                kinds = {i.kind for i in infos}
                keys = set.intersection(*(self._keys_for(k) for k in kinds)) | {"author", "locked", "printable",
                                                                                "read_only", "locked_contents"}
                first = infos[0]
                if len(infos) == 1:
                    self.title.setText(_(first.label))
                    self.subtitle.setText(f"{_('Pagina')} {first.page + 1} · "
                                          f"{engine.format_date(first.modified or first.created)}")
                else:
                    self.title.setText(_("{n} annotazioni").format(n=len(infos)))
                    self.subtitle.setText(_("Valori misti: si modificano le proprietà comuni."))
                self._show(keys)
                self.color.set_color(first.color if first.kind not in (engine.FREETEXT, engine.CALLOUT)
                                     else self._border_color(first))
                self.fill.set_color(first.fill if first.kind not in (engine.FREETEXT, engine.CALLOUT)
                                    else first.color)
                self.opacity.setValue(round(first.opacity * 100))
                self.width.setValue(first.width)
                self.dash.setCurrentIndex(1 if first.dashed else 0)
                self.author.setText(first.author)
                self.locked.setChecked(all(i.locked for i in infos))
                self.printable.setChecked(all(i.flags & engine.FLAG_PRINT for i in infos))
                self.read_only.setChecked(all(i.flags & engine.FLAG_READ_ONLY for i in infos))
                self.locked_contents.setChecked(all(i.flags & engine.FLAG_LOCKED_CONTENTS for i in infos))
                self.default_btn.setVisible(len(kinds) == 1)
                self.fav_box.setVisible(False)
            else:
                kind = self.ctrl.tool_kind
                if not kind:
                    self.title.setText(_("Proprietà"))
                    self.subtitle.setText(_("Seleziona un'annotazione o uno strumento di commento."))
                    self._show(set())
                    self.default_btn.setVisible(False)
                    self.fav_box.setVisible(False)
                    return
                st = self.ctrl.style_for(kind)
                self.title.setText(_("Strumento: {t}").format(t=_(engine.SUBJECTS.get(kind, _("Gomma") if
                                                                                      kind == "eraser" else
                                                                                      _("Pennarello")))))
                self.subtitle.setText(_("Le proprietà valgono per le prossime annotazioni."))
                self._show(self._keys_for(kind))
                self.color.set_color(st.color)
                self.fill.set_color(st.fill)
                self.opacity.setValue(round(st.opacity * 100))
                self.width.setValue(st.width)
                self.dash.setCurrentIndex(max(0, [k for k, _l in DASH_CHOICES].index(st.dash)
                                              if st.dash in dict(DASH_CHOICES) else 0))
                self.cloud.setChecked(st.cloud)
                self.fontsize.setValue(int(st.fontsize))
                self.text_color.set_color(st.text_color or (0, 0, 0))
                self.align.setCurrentIndex(st.align)
                idx = self.icon_combo.findData(st.icon)
                self.icon_combo.setCurrentIndex(max(0, idx))
                self.default_btn.setVisible(False)
                self._fill_favorites()
        finally:
            self._loading = False

    def _fill_favorites(self) -> None:
        kind = self.ctrl.tool_kind
        show = bool(kind) and not self.ctrl.selection and kind != "eraser"
        self.fav_box.setVisible(show)
        if not show:
            return
        self.favorites.blockSignals(True)
        self.favorites.clear()
        self.favorites.addItem(_("Stili preferiti…"))
        for d in self.ctrl.favorites(kind):
            self.favorites.addItem(d["name"])
        self.favorites.blockSignals(False)

    def _use_favorite(self, index: int) -> None:
        if index <= 0:
            return
        self.ctrl.use_favorite(self.ctrl.tool_kind, self.favorites.itemText(index))
        self.refresh()

    def _save_favorite(self) -> None:
        kind = self.ctrl.tool_kind
        if not kind:
            return
        name = dialogs.ask_text(self, _("Stile preferito"), _("Nome dello stile:"), "")
        if name and name.strip():
            self.ctrl.add_favorite(kind, name)

    @staticmethod
    def _border_color(info):
        return info.color

    def _changed(self, prop: str, value) -> None:
        if self._loading:
            return
        if self.ctrl.selection:
            if prop == "line_ends":
                self._apply_line_ends()
                return
            if prop == "icon":
                return
            if prop == "width" and value == 0 and not self.fill.color():
                value = 0.25   # spessore 0 solo con riempimento
            self.ctrl.set_style(**{prop: value})
            return
        kind = self.ctrl.tool_kind
        if not kind or prop == "line_ends":
            return
        st = self.ctrl.style_for(kind)
        setattr(st, prop, value)
        self.ctrl.set_style_for(kind, st)

    def _apply_line_ends(self) -> None:
        from .engine import _LE_VALUES
        a = _LE_VALUES.get(self.start_end.currentData(), 0)
        b = _LE_VALUES.get(self.end_end.currentData(), 0)

        def run(pdf):
            for page, xref in self.ctrl.selection:
                p = pdf[page]
                annot = engine.find_annot(p, xref)
                if annot is not None and annot.type[0] in (3, 7):   # Line, PolyLine
                    annot.set_line_ends(a, b)
                    engine.touch(annot)
                    annot.update()
            return True

        self.ctrl.apply(_("Cambia estremità"), run)

    def _flag(self, flag: int, on: bool) -> None:
        if self._loading or not self.ctrl.selection:
            return
        self.ctrl.set_flag(flag, on)

    def _author_changed(self) -> None:
        if self._loading or not self.ctrl.selection:
            return
        name = self.author.text().strip()
        infos = self.ctrl.selected_infos()
        if not name:
            self.author.setText(infos[0].author if infos else "")
            return
        if infos and all(i.author == name for i in infos):
            return
        self.ctrl.set_annotation_author(list(self.ctrl.selection), name)

    def _make_default(self) -> None:
        infos = self.ctrl.selected_infos()
        if not infos:
            return
        i = infos[0]
        st = self.ctrl.style_for(i.kind)
        st.color = i.color
        st.fill = i.fill
        st.opacity = i.opacity
        st.width = i.width
        self.ctrl.set_style_for(i.kind, st)
        self.ctrl.ctx.status(_("Stile predefinito aggiornato."))


# ======================================================================== elenco commenti
def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


ROLE = Qt.ItemDataRole.UserRole


class CommentsPanel(QWidget):
    """Elenco dei commenti per pagina, con risposte, stato, filtri e azioni (ANN-34…38)."""

    def __init__(self, ctrl: "Controller"):
        super().__init__()
        self.ctrl = ctrl
        self.view = None
        self._infos: list[engine.AnnotInfo] = []
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Cerca nei commenti"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self.rebuild())
        top.addWidget(self.search, 1)
        self.filter_btn = QToolButton()
        bind_icon(self.filter_btn, "list-filter")
        self.filter_btn.setToolTip(_("Filtri"))
        self.filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_menu = QMenu(self)
        self.filter_menu.aboutToShow.connect(self._fill_filter_menu)
        self.filter_btn.setMenu(self.filter_menu)
        top.addWidget(self.filter_btn)
        self.more_btn = QToolButton()
        bind_icon(self.more_btn, "ellipsis-vertical")
        self.more_btn.setToolTip(_("Altre azioni"))
        self.more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more = QMenu(self)
        more.addAction(_("Espandi tutto"), lambda: self.tree.expandAll())
        more.addAction(_("Comprimi tutto"), lambda: self.tree.collapseAll())
        more.addSeparator()
        more.addAction(_("Esporta commenti (XFDF)…"), lambda: ui_actions.export_comments(self.ctrl, "xfdf"))
        more.addAction(_("Esporta commenti (FDF)…"), lambda: ui_actions.export_comments(self.ctrl, "fdf"))
        more.addAction(_("Importa commenti…"), lambda: ui_actions.import_comments(self.ctrl))
        more.addAction(_("Riepilogo commenti…"), lambda: ui_actions.export_summary(self.ctrl))
        more.addSeparator()
        more.addAction(_("Elimina tutti i commenti…"), lambda: ui_actions.delete_all(self.ctrl))
        self.more_btn.setMenu(more)
        top.addWidget(self.more_btn)
        lay.addLayout(top)
        sort_row = QHBoxLayout()
        self.sort = QComboBox()
        for key, label in (("page", "Per pagina"), ("date_desc", "Più recenti"), ("date_asc", "Meno recenti"),
                           ("author", "Per autore"), ("type", "Per tipo"), ("status", "Per stato")):
            self.sort.addItem(_(label), key)
        self.sort.currentIndexChanged.connect(lambda _i: self.rebuild())
        sort_row.addWidget(QLabel(_("Ordina:")))
        sort_row.addWidget(self.sort, 1)
        lay.addLayout(sort_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setWordWrap(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.tree.setIndentation(14)
        self.tree.setStyleSheet("QTreeWidget::item { padding: 3px 0px; }")
        self.tree.itemSelectionChanged.connect(self._on_tree_selection)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        lay.addWidget(self.tree, 1)
        self.empty = QLabel(_("Nessun commento.\nUsa gli strumenti della modalità Commenta per aggiungerne."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet(f"color:{theme.hex_color('text_muted')}")
        lay.addWidget(self.empty)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{theme.hex_color('text_muted')}")
        lay.addWidget(self.count)
        self.filters = {"types": set(), "authors": set(), "status": set(), "colors": set(), "replies": set(),
                        "pages": set(), "marked": set(), "days": set()}
        self.apply_to_page = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.reload)
        ctrl.ctx.documentChanged.connect(self._on_doc_changed)
        ctrl.selectionChanged.connect(self._sync_from_selection)

    # ------------------------------------------------------------------ dati
    def set_view(self, view) -> None:
        self.view = view
        self._refresh_when_visible()

    def _on_doc_changed(self, view) -> None:
        if view is self.view:
            self._refresh_when_visible()

    def _refresh_when_visible(self) -> None:
        """Ricarica subito se il pannello è visibile, altrimenti alla prossima apertura."""
        if self.isVisible():
            self._dirty = False
            self._timer.start()
        else:
            self._dirty = True

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if getattr(self, "_dirty", False):
            self._dirty = False
            self._timer.start()

    CHUNK = 30   # pagine lette per passo: la prima schermata compare subito anche con migliaia di commenti

    def reload(self) -> None:
        self._generation = getattr(self, "_generation", 0) + 1
        if self.view is None or self.view.document.closed:
            self._infos = []
            self.rebuild()
            return
        total = self.view.page_count
        self._infos = self._read_pages(0, min(total, self.CHUNK))
        self.rebuild()
        if total > self.CHUNK:
            QTimer.singleShot(0, lambda g=self._generation, v=self.view: self._continue(g, v, self.CHUNK))

    def _read_pages(self, start: int, end: int) -> list:
        from foliant.api import ENGINE_LOCK
        try:
            with ENGINE_LOCK:
                return engine.list_annotations(self.view.document.pdf, range(start, end))
        except Exception:
            return []

    def _continue(self, generation: int, view, start: int) -> None:
        if generation != self._generation or view is not self.view or view.document.closed:
            return
        end = min(view.page_count, start + self.CHUNK)
        self._infos.extend(self._read_pages(start, end))
        if end < view.page_count:
            QTimer.singleShot(0, lambda: self._continue(generation, view, end))
        else:
            self.rebuild()

    def _matches(self, info: engine.AnnotInfo, query: str) -> bool:
        f = self.filters
        if f["types"] and info.kind not in f["types"]:
            return False
        if f["authors"] and info.author.strip() not in f["authors"]:
            return False
        if f["status"] and info.status not in f["status"]:
            return False
        if f["colors"] and engine.color_to_hex(info.color) not in f["colors"]:
            return False
        if f["replies"] and ("with" if info.replies else "without") not in f["replies"]:
            return False
        if f["pages"] and info.page not in f["pages"]:
            return False
        if f["marked"] and ("yes" if info.marked else "no") not in f["marked"]:
            return False
        if f["days"]:
            dt = engine.parse_pdf_date(info.modified or info.created)
            if dt is None:
                return False
            from datetime import datetime, timedelta
            now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
            if now - dt > timedelta(days=min(int(d) for d in f["days"])):
                return False
        if query:
            hay = " ".join([info.contents, info.marked_text, info.author] + [r.contents for r in info.replies])
            if query not in _fold(hay):
                return False
        return True

    def _sorted(self, infos):
        key = self.sort.currentData() or "page"
        if key == "date_desc":
            return sorted(infos, key=lambda i: (engine.parse_pdf_date(i.modified or i.created) is None,
                                                -(_ts(i.modified or i.created))))
        if key == "date_asc":
            return sorted(infos, key=lambda i: (engine.parse_pdf_date(i.modified or i.created) is None,
                                                _ts(i.modified or i.created)))
        if key == "author":
            return sorted(infos, key=lambda i: (_fold(i.author), i.page, i.rect.y0))
        if key == "type":
            return sorted(infos, key=lambda i: (_fold(_(i.label)), i.page, i.rect.y0))
        if key == "status":
            return sorted(infos, key=lambda i: (i.status, i.page, i.rect.y0))
        return sorted(infos, key=lambda i: (i.page, i.rect.y0, i.rect.x0))

    def rebuild(self) -> None:
        query = _fold(self.search.text().strip())
        shown = [i for i in self._infos if self._matches(i, query)]
        self._syncing = True
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            key = self.sort.currentData() or "page"
            groups: dict[str, QTreeWidgetItem] = {}
            for info in self._sorted(shown):
                if key == "page":
                    gname = _("Pagina {n}").format(n=info.page + 1)
                elif key == "author":
                    gname = info.author.strip() or _("(autore sconosciuto)")
                elif key == "type":
                    gname = _(info.label)
                elif key == "status":
                    gname = _(STATE_LABELS.get(info.status, info.status))
                else:
                    gname = ""
                parent = self.tree.invisibleRootItem()
                if gname:
                    if gname not in groups:
                        g = QTreeWidgetItem([gname])
                        f = g.font(0)
                        f.setBold(True)
                        g.setFont(0, f)
                        g.setFlags(Qt.ItemFlag.ItemIsEnabled)
                        self.tree.addTopLevelItem(g)
                        groups[gname] = g
                    parent = groups[gname]
                item = self._item(info)
                parent.addChild(item) if parent is not self.tree.invisibleRootItem() else \
                    self.tree.addTopLevelItem(item)
                for r in info.replies:
                    child = self._item(r, reply=True)
                    item.addChild(child)
            for g in groups.values():
                g.setText(0, f"{g.text(0)} ({g.childCount()})")
            self.tree.expandAll()
        finally:
            self.tree.blockSignals(False)
            self._syncing = False
        total = len(self._infos)
        empty = not shown
        self.tree.setVisible(not empty)
        self.empty.setVisible(empty)
        if total and empty:
            self.empty.setText(_("Nessun commento corrisponde ai filtri."))
        elif not total:
            self.empty.setText(_("Nessun commento.\nUsa gli strumenti della modalità Commenta per aggiungerne."))
        filtered = len(shown) != total
        self.count.setText(_("{n} di {t} commenti").format(n=len(shown), t=total) if filtered
                           else _("{t} commenti").format(t=total))
        self._update_page_filter()
        self._sync_from_selection()

    def _item(self, info: engine.AnnotInfo, reply: bool = False) -> QTreeWidgetItem:
        author = info.author.strip() or _("(autore sconosciuto)")
        date = engine.format_date(info.modified or info.created) or (
            _("data non valida") if (info.modified or info.created) else "")
        head = f"{author} · {date}" if date else author
        lines = [head] if reply else [_(info.label), head]
        if info.marked_text:
            lines.append(f"«{_ellipsis(info.marked_text, 120)}»")
        text = info.contents.strip()
        if text:
            lines.append(_ellipsis(text, 240, max_lines=3))
        elif not reply and info.kind == engine.NOTE:
            lines.append(_("(senza testo)"))
        extra = []
        if info.status and info.status != engine.STATE_NONE:
            extra.append(_(STATE_LABELS.get(info.status, info.status)))
        if info.replies:
            extra.append(_("{n} risposte").format(n=len(info.replies)) if len(info.replies) != 1
                         else _("1 risposta"))
        if info.hidden and not reply:
            extra.append(_("nascosta"))
        if info.locked:
            extra.append(_("bloccata"))
        if extra:
            lines.append(" · ".join(extra))
        item = QTreeWidgetItem(["\n".join(lines)])
        item.setData(0, ROLE, (info.page, info.xref))
        item.setToolTip(0, info.contents[:2000] if info.contents else "")
        if reply:
            bind = "reply"
            item.setIcon(0, _icon(bind))
        else:
            item.setIcon(0, _icon(KIND_ICONS.get(info.kind, "message-square"), info.color))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked if info.marked else Qt.CheckState.Unchecked)
        return item

    # ------------------------------------------------------------------ filtri
    def _fill_filter_menu(self) -> None:
        m = self.filter_menu
        m.clear()
        m.addAction(_("Azzera filtri"), self._reset_filters)
        m.addSeparator()
        kinds = sorted({i.kind for i in self._infos if i.kind})
        if kinds:
            sub = m.addMenu(_("Tipo"))
            for k in kinds:
                a = sub.addAction(_(engine.SUBJECTS.get(k, k)))
                a.setCheckable(True)
                a.setChecked(k in self.filters["types"])
                a.toggled.connect(lambda on, k=k: self._toggle_filter("types", k, on))
        authors = sorted({i.author.strip() for i in self._infos if i.author.strip()}, key=_fold)
        if authors:
            sub = m.addMenu(_("Autore"))
            for a_name in authors:
                a = sub.addAction(a_name)
                a.setCheckable(True)
                a.setChecked(a_name in self.filters["authors"])
                a.toggled.connect(lambda on, k=a_name: self._toggle_filter("authors", k, on))
        colors = sorted({engine.color_to_hex(i.color) for i in self._infos if i.color})
        if colors:
            sub = m.addMenu(_("Colore"))
            for c in colors:
                a = sub.addAction(swatch(engine.hex_to_color(c)), c)
                a.setCheckable(True)
                a.setChecked(c in self.filters["colors"])
                a.toggled.connect(lambda on, k=c: self._toggle_filter("colors", k, on))
        sub = m.addMenu(_("Risposte"))
        for key, label in (("with", "Con risposte"), ("without", "Senza risposte")):
            a = sub.addAction(_(label))
            a.setCheckable(True)
            a.setChecked(key in self.filters["replies"])
            a.toggled.connect(lambda on, k=key: self._toggle_filter("replies", k, on))
        sub = m.addMenu(_("Spunta"))
        for key, label in (("yes", "Con spunta"), ("no", "Senza spunta")):
            a = sub.addAction(_(label))
            a.setCheckable(True)
            a.setChecked(key in self.filters["marked"])
            a.toggled.connect(lambda on, k=key: self._toggle_filter("marked", k, on))
        m.addAction(_("Pagine…"), self._ask_pages)
        sub = m.addMenu(_("Data di modifica"))
        for days, label in (("1", "Ultime 24 ore"), ("7", "Ultimi 7 giorni"), ("30", "Ultimi 30 giorni")):
            a = sub.addAction(_(label))
            a.setCheckable(True)
            a.setChecked(days in self.filters["days"])
            a.toggled.connect(lambda on, k=days: self._toggle_filter("days", k, on))
        m.addSeparator()
        ap = m.addAction(_("Applica il filtro alla pagina"))
        ap.setCheckable(True)
        ap.setChecked(self.apply_to_page)
        ap.toggled.connect(self.set_apply_to_page)
        sub = m.addMenu(_("Stato"))
        for st, label in STATE_LABELS.items():
            a = sub.addAction(_(label))
            a.setCheckable(True)
            a.setChecked(st in self.filters["status"])
            a.toggled.connect(lambda on, k=st: self._toggle_filter("status", k, on))

    def set_apply_to_page(self, on: bool) -> None:
        self.apply_to_page = on
        self._update_page_filter()

    def _update_page_filter(self) -> None:
        if self.view is None:
            return
        from .visibility import set_view_filter
        excluded = None
        if self.apply_to_page and self._filter_active():
            excluded = {}
            query = _fold(self.search.text().strip())
            for info in self._infos:
                if not self._matches(info, query):
                    hide = excluded.setdefault(info.page, set())
                    hide.add(info.xref)
                    hide.update(r.xref for r in info.replies)
        if excluded != getattr(self.view, "_annot_excluded", None):
            set_view_filter(self.view, excluded)

    def _filter_active(self) -> bool:
        return any(self.filters.values()) or bool(self.search.text().strip())

    def _ask_pages(self) -> None:
        text = dialogs.ask_text(self, _("Filtra per pagine"), _("Pagine (es. 1-3, 7; vuoto = tutte):"), "")
        if text is None:
            return
        self.set_filter("pages", parse_pages(text, self.view.page_count if self.view else 0))

    def _toggle_filter(self, group: str, value: str, on: bool) -> None:
        if on:
            self.filters[group].add(value)
        else:
            self.filters[group].discard(value)
        self.rebuild()

    def set_filter(self, group: str, values: set[str]) -> None:
        self.filters[group] = set(values)
        self.rebuild()

    def _reset_filters(self) -> None:
        for v in self.filters.values():
            v.clear()
        self.search.clear()
        self.rebuild()

    # ------------------------------------------------------------------ selezione
    def items(self) -> list[QTreeWidgetItem]:
        out = []

        def walk(parent):
            for i in range(parent.childCount()):
                child = parent.child(i)
                if child.data(0, ROLE) is not None:
                    out.append(child)
                walk(child)

        walk(self.tree.invisibleRootItem())
        return out

    def item_for(self, page: int, xref: int) -> QTreeWidgetItem | None:
        for it in self.items():
            if tuple(it.data(0, ROLE)) == (page, xref):
                return it
        return None

    def _on_tree_selection(self) -> None:
        if self._syncing:
            return
        keys = [tuple(it.data(0, ROLE)) for it in self.tree.selectedItems() if it.data(0, ROLE) is not None]
        if not keys or self.view is None:
            return
        page, xref = keys[0]
        info = self.ctrl.info(page, xref, view=self.view)
        if info is not None and info.irt:
            # risposta: si porta in vista il commento principale
            page, xref = page, info.irt
            info = self.ctrl.info(page, xref, view=self.view)
        if info is not None:
            self.view.ensure_visible(page, info.rect)
        self._syncing = True
        try:
            self.ctrl.select([k for k in keys if self.ctrl.info(*k, view=self.view) is not None
                              and not self.ctrl.info(*k, view=self.view).irt] or [(page, xref)], self.view)
        finally:
            self._syncing = False

    def _on_activated(self, item, _col) -> None:
        self._on_tree_selection()

    def _sync_from_selection(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self.tree.clearSelection()
            last = None
            for page, xref in self.ctrl.selection:
                it = self.item_for(page, xref)
                if it is not None:
                    it.setSelected(True)
                    last = it
            if last is not None:
                self.tree.scrollToItem(last)
        finally:
            self._syncing = False

    def _on_item_changed(self, item, _col) -> None:
        key = item.data(0, ROLE)
        if key is None or self._syncing:
            return
        info = self.ctrl.info(*key, view=self.view)
        if info is None:
            return
        marked = item.checkState(0) == Qt.CheckState.Checked
        if marked != info.marked:
            self.ctrl.set_marked(key[0], key[1], marked)

    def _on_double_click(self, item, _col) -> None:
        key = item.data(0, ROLE)
        if key is not None:
            self.ctrl.edit_text(key[0], key[1], view=self.view)

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or item.data(0, ROLE) is None:
            return
        page, xref = item.data(0, ROLE)
        menu = QMenu(self)
        build_annotation_menu(self.ctrl, menu, page, xref)
        menu.exec(self.tree.viewport().mapToGlobal(pos))
        menu.deleteLater()


def parse_pages(text: str, count: int) -> set[int]:
    """«1-3, 7» → {0, 1, 2, 6} (indici); valori fuori dal documento ignorati."""
    out: set[int] = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = (int(x) for x in part.split("-", 1))
                out.update(range(min(a, b) - 1, max(a, b)))
            else:
                out.add(int(part) - 1)
        except ValueError:
            continue
    return {p for p in out if 0 <= p < count} if count else out


def _ts(value: str) -> float:
    dt = engine.parse_pdf_date(value)
    if dt is None:
        return 0.0
    try:
        return dt.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _ellipsis(text: str, n: int, max_lines: int = 1) -> str:
    lines = text.splitlines()[:max_lines]
    out = "\n".join(lines)
    if len(out) > n:
        out = out[:n].rstrip() + "…"
    elif len(text.splitlines()) > max_lines:
        out += "…"
    return out


def _icon(name: str, color=None) -> QIcon:
    from foliant.api import icon
    if color:
        return icon(name, engine.color_to_hex(color))
    return icon(name)


def build_annotation_menu(ctrl: "Controller", menu: QMenu, page: int, xref: int, point=None) -> None:
    """Voci comuni del menu contestuale di un'annotazione (pagina e pannello)."""
    info = ctrl.info(page, xref)
    if info is None:
        return
    if info.kind == engine.ATTACHMENT:
        menu.addAction(_("Salva allegato con nome…"), lambda: ui_actions.save_attachment(ctrl, page, xref))
    menu.addAction(_("Modifica commento…"), lambda: ctrl.edit_text(page, xref))
    if info.kind not in (engine.FREETEXT, engine.CALLOUT, engine.TYPEWRITER) and not info.irt:
        menu.addAction(_("Apri/chiudi finestra del commento"), lambda: ctrl.toggle_popup(page, xref))
    if not info.irt:
        menu.addAction(_("Rispondi…"), lambda: ctrl.reply(page, xref))
        status = menu.addMenu(_("Imposta stato"))
        for st, label in STATE_LABELS.items():
            a = status.addAction(_(label))
            a.setCheckable(True)
            a.setChecked(info.status == st)
            a.triggered.connect(lambda _c=False, s=st: ctrl.set_status(page, xref, s))
        if info.history:
            def show_history():
                lines = [f"{engine.format_date(h.created)} — {h.author}: "
                         f"{_(STATE_LABELS.get(h.state, h.state)) if h.state_model.lower() != 'marked' else h.state}"
                         for h in info.history]
                dialogs.show_text(ctrl.view(), _("Cronologia degli stati"), "\n".join(lines))
            menu.addAction(_("Cronologia degli stati…"), show_history)
        mark = menu.addAction(_("Segno di spunta"))
        mark.setCheckable(True)
        mark.setChecked(info.marked)
        mark.triggered.connect(lambda on: ctrl.set_marked(page, xref, on))
    from foliant.api import copy_text_to_clipboard
    text = info.contents or info.marked_text
    if text:
        menu.addAction(_("Copia testo"), lambda: copy_text_to_clipboard(text))
    if info.kind in (engine.STAMP, engine.RECT, engine.ELLIPSE, engine.FREETEXT):
        rot = menu.addMenu(_("Ruota"))
        rot.addAction(_("90° in senso orario"), lambda: ctrl.rotate(-90, [(page, xref)]))
        rot.addAction(_("90° in senso antiorario"), lambda: ctrl.rotate(90, [(page, xref)]))

        def ask_angle():
            value = dialogs.ask_text(ctrl.view(), _("Ruota"), _("Gradi (senso antiorario):"), "30")
            try:
                deg = float((value or "").replace(",", "."))
            except ValueError:
                return
            ctrl.rotate(deg, [(page, xref)])

        rot.addAction(_("Angolo…"), ask_angle)
    if point is not None and info.type in ("Polygon", "PolyLine"):
        menu.addAction(_("Aggiungi vertice qui"), lambda: ctrl.apply(
            _("Aggiungi vertice"), lambda pdf: engine.edit_vertices(pdf, page, xref, point, True) or True))
        menu.addAction(_("Rimuovi vertice"), lambda: ctrl.apply(
            _("Rimuovi vertice"), lambda pdf: engine.edit_vertices(pdf, page, xref, point, False) or True))
    if info.type in ("Polygon", "Square", "Circle") and info.kind not in engine.MEASURE_KINDS:
        cl = menu.addMenu(_("Bordo a nuvola"))
        for n, label in ((0, "Nessuno"), (1, "Intensità 1"), (2, "Intensità 2")):
            cl.addAction(_(label), lambda _c=False, n=n: ctrl.apply(
                _("Bordo a nuvola"), lambda pdf: engine.set_cloud_intensity(pdf, page, xref, n) or True))
    if len(ctrl.selection) > 1 and (page, xref) in ctrl.selection:
        al = menu.addMenu(_("Allinea"))
        for key, label in (("left", "A sinistra"), ("hcenter", "Al centro orizzontale"), ("right", "A destra"),
                           ("top", "In alto"), ("vcenter", "Al centro verticale"), ("bottom", "In basso")):
            al.addAction(_(label), lambda _c=False, k=key: ctrl.align(k))
        di = menu.addMenu(_("Distribuisci"))
        di.addAction(_("In orizzontale"), lambda: ctrl.align("hdist"))
        di.addAction(_("In verticale"), lambda: ctrl.align("vdist"))
    menu.addSeparator()
    if not info.irt:
        menu.addAction(_("Porta in primo piano"), lambda: ctrl.restack(True, [(page, xref)]))
        menu.addAction(_("Porta sullo sfondo"), lambda: ctrl.restack(False, [(page, xref)]))
        menu.addAction(_("Proprietà"), lambda: (ctrl.select([(page, xref)]), ctrl.ctx.show_properties("annotations")))
    if not info.irt:
        menu.addAction(_("Appiattisci selezione"),
                       lambda: ui_actions.flatten(ctrl, selection=True))
    menu.addSeparator()
    menu.addAction(_("Elimina"), lambda: ctrl.delete([(page, xref)]))
