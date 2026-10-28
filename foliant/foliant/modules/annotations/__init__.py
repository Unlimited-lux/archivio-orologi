"""Modulo «Commenta»: annotazioni e commenti (specifica, sezione 5)."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import SAVED, Context, _, add_translations, bind_icon

from . import engine, ui_actions
from .controller import Controller
from .i18n_en import EN
from .panels import CommentsPanel, PropertiesPanel, build_annotation_menu
from .tools import (
    AttachmentTool, CalibrateTool, CalloutTool, CaretTool, EraserTool, InkTool, NoteTool, PolyTool, ReplaceTool, SelectAnnotTool,
    ShapeTool, StampTool, TextBoxTool, TextMarkupTool, TypewriterTool, paint_popups, paint_selection,
)

ORDER = 20


class AnnotationsModule:
    """Collega controller, strumenti, barra e pannelli all'applicazione."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.ctrl = Controller(ctx)
        self.select_tool = SelectAnnotTool.shared(self.ctrl)
        self.tools: dict[str, object] = {
            engine.HIGHLIGHT: TextMarkupTool(self.ctrl, engine.HIGHLIGHT),
            engine.UNDERLINE: TextMarkupTool(self.ctrl, engine.UNDERLINE),
            engine.STRIKEOUT: TextMarkupTool(self.ctrl, engine.STRIKEOUT),
            engine.SQUIGGLY: TextMarkupTool(self.ctrl, engine.SQUIGGLY),
            "area-highlight": TextMarkupTool(self.ctrl, engine.HIGHLIGHT),
            engine.TYPEWRITER: TypewriterTool(self.ctrl),
            engine.CARET: CaretTool(self.ctrl),
            "paragraph": CaretTool(self.ctrl, paragraph=True),
            engine.REPLACE: ReplaceTool(self.ctrl),
            engine.NOTE: NoteTool(self.ctrl),
            engine.FREETEXT: TextBoxTool(self.ctrl),
            engine.CALLOUT: CalloutTool(self.ctrl),
            engine.RECT: ShapeTool(self.ctrl, engine.RECT),
            engine.ELLIPSE: ShapeTool(self.ctrl, engine.ELLIPSE),
            engine.LINE: ShapeTool(self.ctrl, engine.LINE),
            engine.ARROW: ShapeTool(self.ctrl, engine.ARROW),
            engine.POLYGON: PolyTool(self.ctrl, engine.POLYGON),
            engine.POLYLINE: PolyTool(self.ctrl, engine.POLYLINE),
            engine.CLOUD: PolyTool(self.ctrl, engine.CLOUD),
            engine.INK: InkTool(self.ctrl),
            "marker": InkTool(self.ctrl, marker=True),
            "eraser": EraserTool(self.ctrl),
            engine.STAMP: StampTool(self.ctrl),
            engine.ATTACHMENT: AttachmentTool(self.ctrl),
            engine.DISTANCE: ShapeTool(self.ctrl, engine.DISTANCE),
            engine.PERIMETER: PolyTool(self.ctrl, engine.PERIMETER),
            engine.AREA: PolyTool(self.ctrl, engine.AREA),
            "calibrate": CalibrateTool(self.ctrl),
        }
        self.tools["area-highlight"].area_mode = True
        self.actions: dict[str, QAction] = {}
        self.group = QActionGroup(ctx)
        self.group.setExclusive(True)

    # ------------------------------------------------------------------ registrazione
    def register(self) -> None:
        ctx = self.ctx
        mode = ctx.add_mode("commenta", _("Commenta"), "message-square-text", 20)
        self.mode = mode
        sel = self._tool_action("select", _("Seleziona"), "mouse-pointer", None)
        mode.add_widget(self._button(sel, text=True))
        mode.add_separator()
        for key, text, icon_name, shortcut in (
                (engine.HIGHLIGHT, _("Evidenzia"), "highlighter", "Ctrl+Shift+H"),
                (engine.UNDERLINE, _("Sottolinea"), "underline", None),
                (engine.STRIKEOUT, _("Barra"), "strikethrough", None),
                (engine.SQUIGGLY, _("Ondulato"), "spell-check", None)):
            mode.add_widget(self._button(self._tool_action(key, text, icon_name, self.tools[key], shortcut),
                                         text=key == engine.HIGHLIGHT))
        mode.add_separator()
        mode.add_widget(self._button(self._tool_action(engine.NOTE, _("Nota"), "sticky-note",
                                                       self.tools[engine.NOTE], "Ctrl+Shift+N"), text=True))
        mode.add_widget(self._button(self._tool_action(engine.FREETEXT, _("Casella di testo"), "type",
                                                       self.tools[engine.FREETEXT])))
        mode.add_widget(self._menu_button(_("Altri strumenti di testo"), [
            ("callout", engine.CALLOUT, _("Richiamo"), "message-square-quote"),
            ("area", "area-highlight", _("Evidenzia area"), "square-dashed"),
            ("typewriter", engine.TYPEWRITER, _("Commento di testo"), "text-cursor-input"),
            ("caret", engine.CARET, _("Inserisci testo"), "text-cursor"),
            ("paragraph", "paragraph", _("Inserisci paragrafo"), "pilcrow"),
            ("replace", engine.REPLACE, _("Sostituisci testo"), "replace"),
        ]))
        mode.add_separator()
        mode.add_widget(self._menu_button(_("Forme"), [
            ("rect", engine.RECT, _("Rettangolo"), "square"),
            ("ellipse", engine.ELLIPSE, _("Ellisse"), "circle"),
            ("line", engine.LINE, _("Linea"), "minus"),
            ("arrow", engine.ARROW, _("Freccia"), "move-up-right"),
            ("polygon", engine.POLYGON, _("Poligono"), "pentagon"),
            ("polyline", engine.POLYLINE, _("Polilinea"), "spline"),
            ("cloud", engine.CLOUD, _("Nuvola"), "cloud"),
        ]))
        mode.add_widget(self._menu_button(_("Disegno"), [
            ("ink", engine.INK, _("Matita"), "pencil"),
            ("marker", "marker", _("Pennarello evidenziatore"), "paintbrush"),
        ]))
        eraser_menu = [("eraser", "eraser", _("Gomma"), "eraser")]
        mode.add_widget(self._menu_button(_("Gomma"), eraser_menu, extra=self._eraser_options))
        mode.add_separator()
        mode.add_widget(self._stamp_button())
        mode.add_widget(self._button(self._tool_action(engine.ATTACHMENT, _("Allega file"), "paperclip",
                                                       self.tools[engine.ATTACHMENT])))
        mode.add_widget(self._menu_button(_("Misura"), [
            ("distance", engine.DISTANCE, _("Distanza"), "ruler"),
            ("perimeter", engine.PERIMETER, _("Perimetro"), "spline"),
            ("area-measure", engine.AREA, _("Area"), "land-plot"),
        ], extra=self._measure_extra))
        mode.add_separator()
        mode.add_widget(self._commands_button())
        mode.entered.connect(self._on_enter)
        mode.left.connect(self._on_leave)

        self.properties = PropertiesPanel(self.ctrl)
        ctx.add_properties_panel("annotations", self.properties)
        self.comments = CommentsPanel(self.ctrl)
        ctx.add_sidebar_panel("comments", _("Commenti"), "messages-square", self.comments, order=30)
        ctx.add_view_menu_provider(self._view_menu)
        ctx.documentOpened.connect(self._attach_view)
        ctx.currentViewChanged.connect(self._on_view_changed)
        for view in ctx.views():
            self._attach_view(view)
        for text, slot in ((_("Esporta commenti…"), lambda: ui_actions.export_comments(self.ctrl, "xfdf")),
                           (_("Importa commenti…"), lambda: ui_actions.import_comments(self.ctrl)),
                           (_("Riepilogo commenti…"), lambda: ui_actions.export_summary(self.ctrl))):
            ctx.add_menu_action(ctx.action(text, None, slot), _("Commenti"))

    def _tool_action(self, key: str, text: str, icon_name: str, tool, shortcut: str | None = None) -> QAction:
        act = self.ctx.action(text, icon_name, lambda _c=False, k=key: self.activate(k), shortcut=shortcut,
                              mode="commenta" if shortcut else None,
                              checkable=True)
        self.group.addAction(act)
        self.actions[key] = act
        return act

    @staticmethod
    def _button(action: QAction, text: bool = False) -> QToolButton:
        button = QToolButton()
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setIconSize(QSize(20, 20))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text
                                  else Qt.ToolButtonStyle.ToolButtonIconOnly)
        return button

    def _menu_button(self, title: str, entries: list, extra=None) -> QToolButton:
        """Pulsante con menu di strumenti alternativi: il clic usa l'ultimo scelto."""
        button = QToolButton()
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        menu = QMenu(button)
        first = None
        for _name, key, text, icon_name in entries:
            act = self._tool_action(key, text, icon_name, self.tools[key])
            menu.addAction(act)
            act.triggered.connect(lambda _c=False, a=act, b=button: b.setDefaultAction(a))
            first = first or act
        if extra is not None:
            menu.addSeparator()
            extra(menu)
        button.setMenu(menu)
        button.setDefaultAction(first)
        return button

    def _measure_extra(self, menu: QMenu) -> None:
        menu.addAction(_("Scala di misura…"), lambda: ui_actions.set_scale(self.ctrl))
        menu.addAction(_("Calibra da un segmento"), lambda: self.activate("calibrate"))
        menu.addAction(_("Esporta misure (CSV)…"), lambda: ui_actions.export_measures(self.ctrl))

    def _eraser_options(self, menu: QMenu) -> None:
        eraser = self.tools["eraser"]
        whole = menu.addAction(_("Cancella tratti interi"))
        whole.setCheckable(True)
        whole.toggled.connect(lambda on: setattr(eraser, "whole", on))
        size = menu.addMenu(_("Dimensione"))
        group = QActionGroup(size)
        for r in (3, 6, 12, 24):
            a = size.addAction(f"{r * 2} pt")
            a.setCheckable(True)
            a.setChecked(r == eraser.radius)
            group.addAction(a)
            a.triggered.connect(lambda _c=False, r=r: setattr(eraser, "radius", float(r)))

    def _stamp_button(self) -> QToolButton:
        button = QToolButton()
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setText(_("Timbro"))
        button.setToolTip(_("Timbro"))
        button.setIconSize(QSize(20, 20))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        bind_icon(button, "stamp")
        menu = QMenu(button)
        std = menu.addMenu(_("Predefiniti"))
        for label, name, color in engine.PRESET_STAMPS:
            std.addAction(_(label), lambda _c=False, t=label, n=name, c=color:
                          self.use_stamp(engine.StampSpec(_(t), c, "", n)))
        self.dyn_menu = menu.addMenu(_("Dinamici"))
        self.dyn_menu.aboutToShow.connect(self._fill_dynamic)
        stickers = menu.addMenu(_("Adesivi"))
        for key, (label, _ops) in engine.STICKERS.items():
            stickers.addAction(_(label), lambda _c=False, k=key: self.use_sticker(k))
        self.custom_menu = menu.addMenu(_("Personalizzati"))
        self.custom_menu.aboutToShow.connect(self._fill_custom_stamps)
        menu.addSeparator()
        menu.addAction(_("Da immagine…"), self._image_stamp)
        button.setMenu(menu)
        self.stamp_button = button
        self.ctx.window.register_document_action(menu.menuAction())
        return button

    def _commands_button(self) -> QToolButton:
        button = QToolButton()
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setText(_("Commenti"))
        button.setIconSize(QSize(20, 20))
        bind_icon(button, "messages-square")
        menu = QMenu(button)
        menu.addAction(_("Elenco commenti"), lambda: self.ctx.show_sidebar_panel("comments"))
        hide = menu.addAction(_("Nascondi annotazioni"))
        hide.setCheckable(True)
        hide.toggled.connect(lambda on: self.ctrl.set_annotations_hidden(on))
        self.hide_action = hide
        self.ctrl.annotationsVisibilityChanged.connect(lambda h: (hide.blockSignals(True), hide.setChecked(h),
                                                                  hide.blockSignals(False)))
        menu.addSeparator()
        menu.addAction(_("Esporta commenti (XFDF)…"), lambda: ui_actions.export_comments(self.ctrl, "xfdf"))
        menu.addAction(_("Esporta commenti (FDF)…"), lambda: ui_actions.export_comments(self.ctrl, "fdf"))
        menu.addAction(_("Importa commenti…"), lambda: ui_actions.import_comments(self.ctrl))
        menu.addAction(_("Riepilogo commenti…"), lambda: ui_actions.export_summary(self.ctrl))
        menu.addSeparator()
        menu.addAction(_("Appiattisci annotazioni…"), lambda: ui_actions.flatten(self.ctrl))
        menu.addAction(_("Appiattisci annotazioni della pagina…"), lambda: ui_actions.flatten(self.ctrl, True))
        menu.addSeparator()
        menu.addAction(_("Nome autore…"), self._ask_author)
        menu.addAction(_("Esporta stili…"), lambda: ui_actions.export_styles(self.ctrl))
        menu.addAction(_("Importa stili…"), lambda: ui_actions.import_styles(self.ctrl))
        keep = menu.addAction(_("Mantieni strumento"))
        keep.setCheckable(True)
        keep.setChecked(self.ctrl.keep_tool)
        keep.toggled.connect(lambda on: setattr(self.ctrl, "keep_tool", on))
        button.setMenu(menu)
        return button

    # ------------------------------------------------------------------ strumenti
    def activate(self, key: str) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        if self.ctx.current_mode() != "commenta":
            self.ctx.set_mode("commenta")
        if key != "select" and not self.ctrl.can_comment(view):
            self._sync_actions(view.tool)
            return
        if key != "select":
            self._confirm_author_once()
        tool = self.select_tool if key == "select" else self.tools[key]
        self.ctx.set_tool(tool)
        self._sync_actions(tool)

    def use_stamp(self, spec: engine.StampSpec, template: str = "", image: bytes | None = None,
                  name: str = "") -> None:
        tool: StampTool = self.tools[engine.STAMP]
        tool.spec, tool.template, tool.image, tool.name = spec, template, image, name
        tool.sticker = ""
        self.activate(engine.STAMP)

    def _fill_dynamic(self) -> None:
        m = self.dyn_menu
        m.clear()
        for label, template, color in engine.DYNAMIC_STAMPS:
            m.addAction(_(template), lambda _c=False, t=label, tpl=template, c=color:
                        self.use_stamp(engine.StampSpec(_(t), c), template=_(tpl)))
        custom = self.ctrl.dynamic_templates()
        if custom:
            m.addSeparator()
            for d in custom:
                m.addAction(d["template"], lambda _c=False, d=d: self.use_stamp(
                    engine.StampSpec(d["template"], engine.hex_to_color(d.get("color")) or (0.15, 0.3, 0.7)),
                    template=d["template"]))
        m.addSeparator()
        m.addAction(_("Nuovo timbro dinamico…"), self.new_dynamic_template)

    def new_dynamic_template(self) -> str | None:
        from . import dialogs
        text = dialogs.ask_text(self.ctx.window, _("Nuovo timbro dinamico"),
                                _("Testo con segnaposto {autore} {data} {ora} {file} {pagina} {contatore}:"),
                                _("Ricevuto da {autore} il {data}"))
        if not text or not text.strip():
            return None
        unknown = engine.unknown_placeholders(text)
        if unknown:
            self.ctx.info(_("Nuovo timbro dinamico"),
                          _("Segnaposto sconosciuti, mostrati letteralmente: {p}").format(p=", ".join(unknown)))
        self.ctrl.add_dynamic_template(text.strip())
        return text.strip()

    def use_sticker(self, key: str) -> None:
        tool: StampTool = self.tools[engine.STAMP]
        tool.sticker = key
        self.activate(engine.STAMP)

    def _image_stamp(self) -> None:
        res = ui_actions.choose_image_stamp(self.ctrl)
        if res is not None:
            self.ctrl.add_custom_stamp(res[1], res[0])   # resta nella libreria «Personalizzati»
            self.use_stamp(None, image=res[0], name=res[1])

    def _fill_custom_stamps(self) -> None:
        import base64
        m = self.custom_menu
        m.clear()
        stamps = self.ctrl.custom_stamps()
        if not stamps:
            a = m.addAction(_("Nessun timbro personalizzato"))
            a.setEnabled(False)
            return
        for d in stamps:
            data = base64.b64decode(d["png"])
            m.addAction(d["name"], lambda _c=False, img=data, n=d["name"]: self.use_stamp(None, image=img, name=n))
        m.addSeparator()
        rm = m.addMenu(_("Rimuovi"))
        for d in stamps:
            rm.addAction(d["name"], lambda _c=False, n=d["name"]: self.ctrl.remove_custom_stamp(n))

    def _sync_actions(self, tool) -> None:
        for key, act in self.actions.items():
            t = self.select_tool if key == "select" else self.tools.get(key)
            act.blockSignals(True)
            act.setChecked(t is tool)
            act.blockSignals(False)

    def _confirm_author_once(self) -> None:
        s = self.ctx.settings()
        if s.value("annotations/author_confirmed", False) in (True, "true"):
            return
        s.setValue("annotations/author_confirmed", True)
        if not s.value("annotations/author", ""):
            self.ctrl.set_author(engine.default_author())
        self.ctx.status(_("I commenti useranno il nome autore «{a}» (modificabile dal menu Commenti).")
                        .format(a=self.ctrl.author()))

    def _ask_author(self) -> None:
        from . import dialogs
        name = dialogs.ask_text(self.ctx.window, _("Nome autore"), _("Nome usato per i nuovi commenti:"),
                                self.ctrl.author())
        if name is not None:
            self.ctrl.set_author(name)

    def _on_enter(self) -> None:
        view = self.ctx.current_view()
        if view is not None and (view.tool is view._default_tool):
            self.ctx.set_tool(self.select_tool)
            self._sync_actions(self.select_tool)
        if view is not None:
            self.ctx.show_properties("annotations")

    def _on_leave(self) -> None:
        self.ctrl.clear_selection()
        self.ctx.hide_properties()

    def _attach_view(self, view) -> None:
        overlay = getattr(self, "_overlay", None)
        if overlay is None:
            def overlay(v, painter, page):
                paint_popups(self.ctrl, v, painter, page)
                paint_selection(self.ctrl, v, painter, page)
            self._overlay = overlay
        if overlay not in view.overlays:
            view.overlays.append(overlay)
        if not getattr(view, "_annotations_connected", False):
            view._annotations_connected = True
            view.toolChanged.connect(self._sync_actions)

            def on_doc_event(_doc, kind, v=view):
                if kind == SAVED:
                    # il salvataggio compatta il documento in memoria: gli xref cambiano
                    QTimer.singleShot(0, lambda: self._after_save(v))

            view.document.add_listener(on_doc_event)

    def _after_save(self, view) -> None:
        self.ctrl.after_save(view)
        if self.comments.view is view:
            self.comments.reload()
        self.properties.refresh()

    def _on_view_changed(self, view) -> None:
        if view is not None:
            self._attach_view(view)
            hidden = self.ctrl.annotations_hidden(view)
            self.hide_action.blockSignals(True)
            self.hide_action.setChecked(hidden)
            self.hide_action.blockSignals(False)
            if self.ctx.current_mode() == "commenta" and view.tool is view._default_tool:
                view.set_tool(self.select_tool)
            self._sync_actions(view.tool)

    # ------------------------------------------------------------------ menu contestuale
    def _view_menu(self, view, pe, menu: QMenu) -> None:
        if pe.page is None:
            return
        from foliant.api import ENGINE_LOCK
        tol = 4.0 / max(view.zoom, 0.1)
        with ENGINE_LOCK:
            xref = engine.annot_at(view.document.pdf, pe.page, pe.point, tol)
        if xref is not None:
            if (pe.page, xref) not in self.ctrl.selection:
                self.ctrl.select([(pe.page, xref)], view)
            if not menu.isEmpty():
                menu.addSeparator()
            build_annotation_menu(self.ctrl, menu, pe.page, xref, pe.point)
            return
        sel = view.text_selection
        if sel is not None and sel.rects:
            sub = menu.addMenu(_("Commenta il testo"))
            for key, text in ((engine.HIGHLIGHT, _("Evidenzia")), (engine.UNDERLINE, _("Sottolinea")),
                              (engine.STRIKEOUT, _("Barra")), (engine.SQUIGGLY, _("Ondulato"))):
                sub.addAction(text, lambda _c=False, k=key: self.tools[k].apply_selection(view))
        if self.ctx.current_mode() != "commenta":
            return
        point = pe.point
        page = pe.page

        def add_note():
            from . import dialogs
            res = dialogs.edit_comment(view, _("Nota"), self.ctrl.author(), "")
            if res is not None:
                xref = self.ctrl.apply(_("Aggiungi nota"), engine.create_note, page, point, res[0],
                                       self.ctrl.style_for(engine.NOTE), self.ctrl.author())
                if xref is not None:
                    self.ctrl.select([(page, xref)], view)

        menu.addAction(_("Aggiungi nota qui"), add_note)


_module: AnnotationsModule | None = None


def register(ctx: Context) -> None:
    global _module
    add_translations("en", EN)
    _module = AnnotationsModule(ctx)
    _module.register()
    ctx.annotations = _module   # accesso per i test e per altri moduli
