"""Pannello Proprietà del testo (TXT-04, TXT-05, TXT-10, TXT-15)."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from ...core.engine import ENGINE_LOCK
from ...i18n import _
from ...ui import theme
from ...ui.icons import bind_icon
from .analysis import page_font_xrefs, strip_subset
from .fonts import system_families
from .model import (
    ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, GRANULARITY_LINE, GRANULARITY_PARAGRAPH, Style,
    color_to_hex,
)

MIXED = "—"


class TextPropertiesPanel(QWidget):
    """Mostra e modifica font, dimensione, colore, stile e paragrafo del testo attivo."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.ctrl = controller
        self.view = None
        self._editor = None
        self._color = QColor(0, 0, 0)
        self.setObjectName("textProperties")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        title = QLabel(_("Testo"))
        title.setProperty("heading", True)
        lay.addWidget(title)

        self.target_label = QLabel(_("Fai clic su un testo per modificarlo."))
        self.target_label.setProperty("muted", True)
        self.target_label.setWordWrap(True)
        lay.addWidget(self.target_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        row = 0
        grid.addWidget(QLabel(_("Riconoscimento")), row, 0)
        self.granularity = QComboBox()
        self.granularity.addItem(_("Paragrafo"), GRANULARITY_PARAGRAPH)
        self.granularity.addItem(_("Riga"), GRANULARITY_LINE)
        idx = self.granularity.findData(controller.granularity)
        self.granularity.setCurrentIndex(max(0, idx))
        self.granularity.currentIndexChanged.connect(
            lambda _i: controller.set_granularity(self.granularity.currentData()))
        grid.addWidget(self.granularity, row, 1, 1, 3)
        row += 1
        grid.addWidget(QLabel(_("Font")), row, 0)
        self.font_combo = QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_combo.setMinimumContentsLength(12)
        self.font_combo.lineEdit().editingFinished.connect(self._font_chosen)
        self.font_combo.activated.connect(lambda _i: self._font_chosen())
        grid.addWidget(self.font_combo, row, 1, 1, 3)
        row += 1
        grid.addWidget(QLabel(_("Dimensione")), row, 0)
        self.size = QDoubleSpinBox()
        self.size.setRange(1.0, 1000.0)
        self.size.setDecimals(1)
        self.size.setSingleStep(0.5)
        self.size.setSuffix(" pt")
        self.size.editingFinished.connect(self._size_chosen)
        grid.addWidget(self.size, row, 1)
        self.color_btn = QPushButton()
        self.color_btn.setToolTip(_("Colore del testo"))
        self.color_btn.setFixedWidth(64)
        self.color_btn.clicked.connect(self._choose_color)
        grid.addWidget(self.color_btn, row, 2, 1, 2)
        row += 1
        styles = QHBoxLayout()
        self.bold = self._toggle("bold", _("Grassetto"), lambda on: self._apply(bold=on))
        self.italic = self._toggle("italic", _("Corsivo"), lambda on: self._apply(italic=on))
        self.underline = self._toggle("underline", _("Sottolineato"), lambda on: self._apply(underline=on))
        self.strike = self._toggle("strikethrough", _("Barrato"), lambda on: self._apply(strike=on))
        for b in (self.bold, self.italic, self.underline, self.strike):
            styles.addWidget(b)
        styles.addStretch(1)
        grid.addLayout(styles, row, 0, 1, 4)
        row += 1
        aligns = QHBoxLayout()
        self.align_group = QButtonGroup(self)
        self.align_group.setExclusive(True)
        self.align_buttons = {}
        for value, icon_name, tip in ((ALIGN_LEFT, "align-left", _("Allinea a sinistra")),
                                      (ALIGN_CENTER, "align-center", _("Centra")),
                                      (ALIGN_RIGHT, "align-right", _("Allinea a destra")),
                                      (ALIGN_JUSTIFY, "align-justify", _("Giustifica"))):
            b = QToolButton()
            b.setCheckable(True)
            b.setToolTip(tip)
            bind_icon(b, icon_name)
            b.clicked.connect(lambda _c=False, v=value: self._align(v))
            self.align_group.addButton(b)
            self.align_buttons[value] = b
            aligns.addWidget(b)
        aligns.addStretch(1)
        grid.addLayout(aligns, row, 0, 1, 4)
        row += 1
        grid.addWidget(QLabel(_("Interlinea")), row, 0)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(0.5, 5.0)
        self.spacing.setDecimals(2)
        self.spacing.setSingleStep(0.1)
        self.spacing.setSuffix(" ×")
        self.spacing.editingFinished.connect(self._spacing_chosen)
        grid.addWidget(self.spacing, row, 1)
        row += 1
        grid.addWidget(QLabel(_("Rientro sinistro")), row, 0)
        self.left_indent = QDoubleSpinBox()
        self.left_indent.setRange(-300.0, 300.0)
        self.left_indent.setDecimals(1)
        self.left_indent.setSuffix(" pt")
        self.left_indent.setToolTip(_("Sposta il bordo sinistro del paragrafo (il bordo destro resta fermo)"))
        self.left_indent.editingFinished.connect(self._left_indent_chosen)
        grid.addWidget(self.left_indent, row, 1)
        row += 1
        grid.addWidget(QLabel(_("Rientro 1ª riga")), row, 0)
        self.indent = QDoubleSpinBox()
        self.indent.setRange(0.0, 300.0)
        self.indent.setDecimals(1)
        self.indent.setSuffix(" pt")
        self.indent.editingFinished.connect(self._indent_chosen)
        grid.addWidget(self.indent, row, 1)
        row += 1
        grid.addWidget(QLabel(_("Spazio dopo")), row, 0)
        self.space_after = QDoubleSpinBox()
        self.space_after.setRange(0.0, 300.0)
        self.space_after.setDecimals(1)
        self.space_after.setSuffix(" pt")
        self.space_after.setToolTip(_("Spazio dopo ogni paragrafo del blocco"))
        self.space_after.editingFinished.connect(self._space_after_chosen)
        grid.addWidget(self.space_after, row, 1)
        lay.addLayout(grid)

        # avviso font sostitutivo (TXT-10)
        self.warning = QFrame()
        self.warning.setObjectName("fontWarning")
        wl = QVBoxLayout(self.warning)
        wl.setContentsMargins(8, 8, 8, 8)
        self.warning_text = QLabel()
        self.warning_text.setWordWrap(True)
        wl.addWidget(self.warning_text)
        wb = QHBoxLayout()
        self.warn_apply = QPushButton(_("Applica il sostituto a tutto il blocco"))
        self.warn_apply.clicked.connect(self._apply_substitute)
        self.warn_undo = QPushButton(_("Annulla"))
        self.warn_undo.clicked.connect(self._undo_substitution)
        wb.addWidget(self.warn_apply)
        wb.addWidget(self.warn_undo)
        wl.addLayout(wb)
        self.warning.hide()
        lay.addWidget(self.warning)

        # OCR (TXT-15)
        self.ocr_box = QFrame()
        ol = QVBoxLayout(self.ocr_box)
        ol.setContentsMargins(0, 4, 0, 0)
        self.show_ocr = QCheckBox(_("Mostra il testo OCR in sovrimpressione"))
        self.show_ocr.setChecked(controller.show_ocr_text)
        self.show_ocr.toggled.connect(controller.set_show_ocr_text)
        ol.addWidget(self.show_ocr)
        self.convert_btn = QPushButton(_("Converti in testo modificabile"))
        self.convert_btn.setToolTip(_("Copre il testo nell'immagine con il colore dello sfondo e lo scrive "
                                      "come testo reale."))
        self.convert_btn.clicked.connect(self._convert_ocr)
        ol.addWidget(self.convert_btn)
        lay.addWidget(self.ocr_box)
        lay.addStretch(1)
        self._subs = []
        self._fill_fonts()
        self._apply_theme()
        theme.on_change(self._apply_theme)
        controller.editorChanged.connect(self._on_editor)
        controller.selectionChanged.connect(lambda _s: self.refresh())
        controller.substitutionsChanged.connect(self._on_substitutions)
        self.refresh()

    # ------------------------------------------------------------ costruzione
    def _toggle(self, icon_name: str, tip: str, slot) -> QToolButton:
        b = QToolButton()
        b.setCheckable(True)
        b.setToolTip(tip)
        bind_icon(b, icon_name)
        b.clicked.connect(lambda on: slot(on))
        return b

    def _apply_theme(self) -> None:
        try:
            self._restyle()
        except RuntimeError:
            pass   # pannello di una finestra già chiusa

    def _restyle(self) -> None:
        self.warning.setStyleSheet(
            f"QFrame#fontWarning {{ background: {theme.hex_color('surface_alt')}; "
            f"border: 1px solid {theme.hex_color('warning')}; border-radius: 6px; }}")
        self._paint_color()

    def _fill_fonts(self) -> None:
        with QSignalBlocker(self.font_combo):
            current = self.font_combo.currentText()
            self.font_combo.clear()
            doc_fonts = self._document_fonts()
            for f in doc_fonts:
                self.font_combo.addItem(f)
            if doc_fonts:
                self.font_combo.insertSeparator(self.font_combo.count())
            for fam in system_families():
                if fam not in doc_fonts:
                    self.font_combo.addItem(fam)
            self.font_combo.setEditText(current)

    def _document_fonts(self) -> list[str]:
        view = self.view
        if view is None or view.document.closed:
            return []
        names = []
        with ENGINE_LOCK:
            try:
                for f in view.document.pdf[view.current_page].get_fonts(full=True):
                    n = strip_subset(f[3])
                    if n and n not in names:
                        names.append(n)
            except Exception:
                pass
        return names

    def set_view(self, view) -> None:
        self.view = view
        self._fill_fonts()
        self.refresh()

    # ------------------------------------------------------------ stato
    def _on_editor(self, editor) -> None:
        if self._editor is not None:
            try:
                self._editor.cursorPositionChanged.disconnect(self.refresh)
                self._editor.selectionChanged.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self._editor = editor
        if editor is not None:
            editor.cursorPositionChanged.connect(self.refresh)
            editor.selectionChanged.connect(self.refresh)
        self.refresh()

    def _target(self):
        """("editor", editor) | ("block", (pagina, blocco)) | ("default", None)."""
        if self._editor is not None:
            return "editor", self._editor
        view = self.view
        if view is not None:
            tool = self.ctrl.tools(view)[0]
            if tool.selected is not None:
                return "block", tool.selected
        return "default", None

    def _styles(self) -> tuple[list[Style], str, float, float]:
        kind, obj = self._target()
        if kind == "editor":
            size = obj.current_style().size or 12
            return obj.selection_styles(), obj.align, obj.leading, size
        if kind == "block":
            block = obj[1]
            styles = [r.style for p in block.paragraphs for r in p if r.text.strip()] or [block.style]
            return styles, block.align, block.leading, block.style.size
        st = self.ctrl.default_style
        return [st], ALIGN_LEFT, st.size * 1.2, st.size

    def refresh(self) -> None:
        kind, obj = self._target()
        styles, align, leading, size = self._styles()
        texts = {
            "editor": _("Modifica in corso: le proprietà valgono per la selezione (o per tutto il testo)."),
            "block": _("Blocco selezionato: le proprietà valgono per tutto il blocco. Canc elimina, "
                       "le frecce spostano, Invio modifica."),
            "default": _("Fai clic su un testo per modificarlo. Le proprietà impostate ora valgono per il "
                         "prossimo testo aggiunto."),
        }
        self.target_label.setText(texts[kind])

        def common(attr):
            vals = {getattr(s, attr) for s in styles}
            return vals.pop() if len(vals) == 1 else None

        font = common("font")
        with QSignalBlocker(self.font_combo):
            self.font_combo.setEditText(font if font is not None else MIXED)
        sz = common("size")
        with QSignalBlocker(self.size):
            self.size.setSpecialValueText("")
            self.size.setValue(sz if sz is not None else styles[0].size)
        color = common("color")
        self._color = QColor.fromRgbF(*color[:3]) if color is not None else QColor(styles[0].color and
                                                                                  QColor.fromRgbF(*styles[0].color[:3]))
        self._paint_color(mixed=color is None)
        for btn, attr in ((self.bold, "bold"), (self.italic, "italic"), (self.underline, "underline"),
                          (self.strike, "strike")):
            with QSignalBlocker(btn):
                btn.setChecked(bool(common(attr)))
        btn = self.align_buttons.get(align)
        if btn is not None:
            with QSignalBlocker(btn):
                btn.setChecked(True)
        with QSignalBlocker(self.spacing):
            self.spacing.setValue(max(0.5, min(5.0, leading / size if size else 1.2)))
        self.left_indent.setEnabled(kind == "block")
        with QSignalBlocker(self.indent):
            self.indent.setValue(obj.indent if kind == "editor" else obj[1].indent if kind == "block" else 0.0)
        with QSignalBlocker(self.space_after):
            self.space_after.setValue(obj.space_after if kind == "editor" else 0.0)
        sel_block = obj[1] if kind == "block" else None
        self.convert_btn.setVisible(bool(sel_block is not None and sel_block.invisible))
        self.ocr_box.setVisible(True)

    def _paint_color(self, mixed: bool = False) -> None:
        name = self._color.name() if self._color.isValid() else "#000000"
        self.color_btn.setText(MIXED if mixed else "")
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background: {name}; border: 1px solid {theme.hex_color('border')}; "
            f"border-radius: 6px; min-height: 20px; color: {theme.hex_color('text')}; }}")

    # ------------------------------------------------------------ applicazione
    def _apply(self, **changes) -> None:
        kind, obj = self._target()
        if kind == "editor":
            obj.apply_char_changes(**changes)
            obj.setFocus()
        elif kind == "block":
            self.ctrl.restyle_selected(self.view, **changes)
        else:
            self.ctrl.default_style = self.ctrl.default_style.with_(**changes)
        self.refresh()

    def _font_chosen(self) -> None:
        name = self.font_combo.currentText().strip()
        if not name or name == MIXED:
            return
        styles, *_rest = self._styles()
        if all(s.font == name for s in styles):
            return
        self._apply(font=name, font_xref=0)

    def _size_chosen(self) -> None:
        value = round(self.size.value(), 1)
        styles, *_rest = self._styles()
        if all(abs(s.size - value) < 0.05 for s in styles):
            return
        self._apply(size=value)

    def _choose_color(self) -> None:
        col = QColorDialog.getColor(self._color, self, _("Colore del testo"))
        if col.isValid():
            self.set_color(col)

    def set_color(self, col: QColor) -> None:
        self._color = col
        self._apply(color=(round(col.redF(), 4), round(col.greenF(), 4), round(col.blueF(), 4)))

    def _align(self, value: str) -> None:
        kind, obj = self._target()
        if kind == "editor":
            obj.set_alignment(value)
            obj.setFocus()
        elif kind == "block":
            self.ctrl.reformat_selected(self.view, align=value)
        self.refresh()

    def _spacing_chosen(self) -> None:
        mult = self.spacing.value()
        kind, obj = self._target()
        styles, align, leading, size = self._styles()
        new = round(mult * size, 3)
        if abs(new - leading) < 0.05:
            return
        if kind == "editor":
            obj.set_leading(new)
        elif kind == "block":
            self.ctrl.reformat_selected(self.view, leading=new)
        self.refresh()

    def _left_indent_chosen(self) -> None:
        """Rientro sinistro relativo: il riquadro si restringe (o allarga) da sinistra e il testo rifluisce."""
        value = round(self.left_indent.value(), 1)
        kind, obj = self._target()
        if kind == "block" and abs(value) >= 0.05:
            page, block = obj
            width = block.width - value
            if width >= 4:
                self.ctrl.resize_block(self.view, page, block, block.x0 + value, width)
        with QSignalBlocker(self.left_indent):
            self.left_indent.setValue(0.0)
        self.refresh()

    def _indent_chosen(self) -> None:
        value = round(self.indent.value(), 1)
        kind, obj = self._target()
        if kind == "editor":
            if abs(obj.indent - value) >= 0.05:
                obj.set_indent(value)
        elif kind == "block":
            if abs(obj[1].indent - value) >= 0.05:
                self.ctrl.reformat_selected(self.view, indent=value)
        self.refresh()

    def _space_after_chosen(self) -> None:
        value = round(self.space_after.value(), 1)
        kind, obj = self._target()
        if kind == "editor":
            obj.set_space_after(value)
        elif kind == "block" and value > 0:
            self.ctrl.reformat_selected(self.view, space_after=value)
        self.refresh()

    # ------------------------------------------------------------ sostituzioni
    def _on_substitutions(self, subs) -> None:
        self._subs = list(subs)
        if not subs:
            self.warning.hide()
            return
        lines = [_("Il font «{o}» non contiene {n} caratteri inseriti: viene usato «{s}».").format(
            o=s.original, n=s.chars, s=s.substitute) for s in subs]
        self.warning_text.setText("\n".join(lines))
        self.warning.show()

    def _apply_substitute(self) -> None:
        if not self._subs or self.view is None:
            return
        view = self.view
        tool = self.ctrl.tools(view)[0]
        if tool.selected is None and self.ctrl.last_change is not None:
            pass
        if tool.selected is not None:
            fam = self._subs[0].substitute
            self.ctrl.restyle_selected(view, font=fam, font_xref=0)
        self.warning.hide()

    def _undo_substitution(self) -> None:
        if self.view is not None and self.view.document.can_undo:
            self.view.document.undo()
        self.warning.hide()

    def _convert_ocr(self) -> None:
        kind, obj = self._target()
        if kind == "block" and obj[1].invisible and self.view is not None:
            self.ctrl.convert_ocr_block(self.view, obj[0], obj[1])
