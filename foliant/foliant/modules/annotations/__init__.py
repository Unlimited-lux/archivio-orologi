"""Modulo «Commenta»: annotazioni e commenti (specifica, sezione 5)."""

from __future__ import annotations

import pymupdf
import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import SAVED, Context, _, add_translations, bind_icon

from . import engine, ui_actions
from .controller import Controller
from .i18n_en import EN
from .panels import CommentsPanel, PropertiesPanel, build_annotation_menu
from .tools import (
    AttachmentTool, CalibrateTool, SnapshotTool, CalloutTool, CaretTool, EraserTool, InkTool, NoteTool, PolyTool, ReplaceTool, SelectAnnotTool,
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
            engine.ANGLE: PolyTool(self.ctrl, engine.ANGLE),
            "calibrate": CalibrateTool(self.ctrl),
            "snapshot": SnapshotTool(self.ctrl, on_done=self._snapshot_done),
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
        # gruppi con nome, come nella barra multifunzione di Excel
        mode.add_group(_("Selezione"), "selezione")
        sel = self._tool_action("select", _("Seleziona"), "mouse-pointer", None)
        mode.add_widget(self._button(sel, text=True))
        mode.add_group(_("Testo"), "testo")
        for key, text, icon_name, shortcut in (
                (engine.HIGHLIGHT, _("Evidenzia"), "highlighter", "Ctrl+Shift+H"),
                (engine.UNDERLINE, _("Sottolinea"), "underline", None),
                (engine.STRIKEOUT, _("Barra"), "strikethrough", None),
                (engine.SQUIGGLY, _("Ondulato"), "spell-check", None)):
            mode.add_widget(self._button(self._tool_action(key, text, icon_name, self.tools[key], shortcut),
                                         text=key == engine.HIGHLIGHT))
        mode.add_group(_("Note"), "note")
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
        mode.add_group(_("Disegno"), "disegno")
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
        mode.add_group(_("Inserisci"), "inserisci")
        mode.add_widget(self._stamp_button())
        mode.add_widget(self._button(self._tool_action(engine.ATTACHMENT, _("Allega file"), "paperclip",
                                                       self.tools[engine.ATTACHMENT])))
        mode.add_widget(self._menu_button(_("Misura"), [
            ("distance", engine.DISTANCE, _("Distanza"), "ruler"),
            ("perimeter", engine.PERIMETER, _("Perimetro"), "spline"),
            ("area-measure", engine.AREA, _("Area"), "land-plot"),
            ("angle", engine.ANGLE, _("Angolo"), "triangle-right"),
        ], extra=self._measure_extra))
        mode.add_group(_("Gestisci"), "gestisci")
        mode.add_widget(self._commands_button())
        mode.entered.connect(self._on_enter)
        mode.left.connect(self._on_leave)

        from . import spellcheck
        spellcheck.use_settings(ctx.settings())
        self.properties = PropertiesPanel(self.ctrl)
        ctx.add_properties_panel("annotations", self.properties)
        self.comments = CommentsPanel(self.ctrl)
        ctx.add_sidebar_panel("comments", _("Commenti"), "messages-square", self.comments, order=30)
        ctx.add_view_menu_provider(self._view_menu)
        ctx.documentOpened.connect(self._attach_view)
        ctx.currentViewChanged.connect(self._on_view_changed)
        ctx.documentClosed.connect(self._on_document_closed)
        for view in ctx.views():
            self._attach_view(view)
        # scheda File › Esporta: la stessa voce del menu «Commenti ▾», con la scelta del formato;
        # Importa e Riepilogo restano solo nel menu «Commenti ▾»
        ctx.add_file_action(self.export_action, "esporta", _("Esporta"), _("In XFDF o FDF, da reimportare"))

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
        snap = menu.addMenu(_("Aggancio"))
        labels = {engine.SNAP_END: _("Estremi"), engine.SNAP_MID: _("Punti medi"),
                  engine.SNAP_CROSS: _("Intersezioni"), engine.SNAP_PATH: _("Tracciati"),
                  engine.SNAP_ANNOT: _("Vertici delle annotazioni")}

        def fill():
            snap.clear()
            active = self.ctrl.snap_kinds()
            for kind in engine.SNAP_KINDS:
                a = snap.addAction(labels[kind])
                a.setCheckable(True)
                a.setChecked(kind in active)
                a.toggled.connect(lambda on, k=kind: self.ctrl.set_snap_kind(k, on))

        snap.aboutToShow.connect(fill)
        fill()
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
        for category, keys in engine.STICKER_CATEGORIES:
            sub = stickers.addMenu(_(category))
            for key in keys:
                sub.addAction(_(engine.STICKERS[key][0]), lambda _c=False, k=key: self.use_sticker(k))
        self.custom_stickers_menu = stickers.addMenu(_("Personalizzati"))
        self.custom_stickers_menu.aboutToShow.connect(self._fill_custom_stickers)
        self.ai_sticker_action = stickers.addAction(_("Genera con IA…"), lambda: self.generate_with_ai("sticker"))
        stickers.aboutToShow.connect(self._sync_ai_actions)
        self.custom_menu = menu.addMenu(_("Personalizzati"))
        self.custom_menu.aboutToShow.connect(self._fill_custom_stamps)
        self.ai_stamp_action = menu.addAction(_("Genera con IA…"), lambda: self.generate_with_ai("stamp"))
        menu.aboutToShow.connect(self._sync_ai_actions)
        menu.addSeparator()
        menu.addAction(_("Da immagine…"), self._image_stamp)
        menu.addAction(_("Da pagina PDF…"), self._page_stamp)
        menu.addAction(_("Istantanea di un'area"), lambda: self.activate("snapshot"))
        menu.addAction(_("Esporta libreria timbri…"), lambda: ui_actions.export_stamp_library(self.ctrl))
        menu.addAction(_("Importa libreria timbri…"), lambda: ui_actions.import_stamp_library(self.ctrl))
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
        self.export_actions: dict[str, QAction] = {}
        for fmt, text in (("xfdf", _("Esporta commenti (XFDF)…")), ("fdf", _("Esporta commenti (FDF)…"))):
            act = self.ctx.action(text, None, lambda _c=False, f=fmt: ui_actions.export_comments(self.ctrl, f))
            menu.addAction(act)
            self.export_actions[fmt] = act
        # le stesse due azioni sotto un'unica voce che chiede il formato (scheda File › Esporta)
        export_menu = QMenu(_("Esporta commenti"), menu)
        export_menu.addActions(list(self.export_actions.values()))
        self.export_action = export_menu.menuAction()
        bind_icon(self.export_action, "message-square-share")
        self.ctx.window.register_document_action(self.export_action)
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
        tool.as_sticker = False
        tool.page_source = None
        tool.snapshot = False
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

    def _page_stamp(self) -> None:
        from . import dialogs
        path = dialogs.ask_open_file(self.ctx.window, _("Timbro da pagina PDF"), "PDF (*.pdf)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            src = pymupdf.open(stream=data, filetype="pdf")
            count = src.page_count
            protected = src.needs_pass
            src.close()
        except Exception:
            self.ctx.error(_("Timbro da pagina PDF"), _("Il file non è un PDF valido oppure è danneggiato."))
            return
        if protected:
            self.ctx.error(_("Timbro da pagina PDF"), _("Il PDF di origine è protetto da password."))
            return
        page = 0
        if count > 1:
            text = dialogs.ask_text(self.ctx.window, _("Timbro da pagina PDF"),
                                    _("Pagina (1–{n}):").format(n=count), "1")
            if text is None:
                return
            try:
                page = max(1, min(count, int(text))) - 1
            except ValueError:
                return
        import os
        tool: StampTool = self.tools[engine.STAMP]
        self.use_stamp(None)
        tool.page_source = (data, page, os.path.splitext(os.path.basename(path))[0])
        self.activate(engine.STAMP)

    def _snapshot_done(self, png: bytes, name: str) -> None:
        self.use_stamp(None, image=png, name=name)
        self.tools[engine.STAMP].snapshot = True

    def use_sticker(self, key: str) -> None:
        tool: StampTool = self.tools[engine.STAMP]
        tool.sticker = key
        tool.page_source = None
        self.activate(engine.STAMP)

    def use_custom_sticker(self, image: bytes, name: str) -> None:
        """Adesivo personalizzato dalla libreria (ANN-27): immagine con /Subj «Adesivo»."""
        self.use_stamp(None, image=image, name=name)
        self.tools[engine.STAMP].as_sticker = True

    def _fill_custom_stickers(self) -> None:
        import base64
        m = self.custom_stickers_menu
        m.clear()
        stamps = self.ctrl.custom_stamps()
        if not stamps:
            a = m.addAction(_("Nessun adesivo personalizzato: aggiungi immagini con «Timbro › Da immagine…»"))
            a.setEnabled(False)
            return
        for d in stamps:
            data = base64.b64decode(d["png"])
            m.addAction(d["name"], lambda _c=False, img=data, n=d["name"]: self.use_custom_sticker(img, n))

    # ------------------------------------------------------------------ IA (ANN-28)
    def _ai_services(self):
        """Servizi del modulo IA se presente e con «Funzioni IA» attive (AI-01), altrimenti None."""
        services = getattr(self.ctx, "ai_services", None)
        store = getattr(services, "store", None)
        try:
            return services if store is not None and store.enabled() else None
        except Exception:
            return None

    def _sync_ai_actions(self) -> None:
        visible = self._ai_services() is not None
        for act in (self.ai_stamp_action, self.ai_sticker_action):
            act.setVisible(visible)

    def generate_with_ai(self, kind: str) -> bytes | None:
        """«Genera con IA…»: descrizione e stile al fornitore di immagini del modulo IA (AI-25), poi la
        proposta scelta si appone con un clic e resta nella libreria «Personalizzati»."""
        services = self._ai_services()
        if services is None:
            return None
        title = _("Genera con IA")
        profile = services.store.active()
        if profile is None:
            self.ctx.error(title, _("Generazione di immagini non disponibile: configura un fornitore "
                                    "nelle impostazioni dell'assistente IA. Gli altri strumenti restano utilizzabili."))
            return None
        if not getattr(profile, "consent", True):     # informativa prima di qualsiasi invio (AI-02)
            panel = getattr(services, "panel", None)
            if panel is None or not panel.ask_consent(profile):
                return None
            profile.consent = True
            services.store.save(profile)
        from foliant.api import OperationCancelled
        from foliant.modules.ai.graphics_view import GenerateGraphicDialog

        def run(task_title, fn):
            try:
                return self.ctx.run_task(task_title, lambda progress: fn(progress=progress))
            except OperationCancelled:
                return None

        dlg = GenerateGraphicDialog(profile, lambda: services.store.provider_for(profile), run, self.ctx.window,
                                    kind=kind)
        services.controller.exec_dialog(dlg)
        png = getattr(dlg, "chosen_png", None)
        if not png:
            return None
        name = (dlg.last_prompt or title)[:60]
        self.ctrl.add_custom_stamp(name, png, origin="ia")
        if dlg.kind == "sticker":
            self.use_custom_sticker(png, name)
        else:
            self.use_stamp(None, image=png, name=name)
        return png

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
        minibar = getattr(self, "minibar", None)
        if minibar is None or not shiboken6.isValid(minibar):
            from .minibar import MiniBar
            self.minibar = MiniBar(self.ctrl)
        if not getattr(view, "_annotations_connected", False):
            view._annotations_connected = True
            view.toolChanged.connect(self._sync_actions)

            def on_doc_event(_doc, kind, v=view):
                if kind == SAVED:
                    # il salvataggio compatta il documento in memoria: gli xref cambiano
                    QTimer.singleShot(0, lambda: self._after_save(v))

            view.document.add_listener(on_doc_event)

    def _after_save(self, view) -> None:
        # programmato con QTimer.singleShot dopo SAVED: la vista (o la finestra) può essere già chiusa
        if not shiboken6.isValid(view) or view.document.closed or not shiboken6.isValid(self.properties):
            return
        self.ctrl.after_save(view)
        if self.comments.view is view:
            self.comments.reload()
        self.properties.refresh()

    def _on_document_closed(self, view) -> None:
        minibar = getattr(self, "minibar", None)
        if minibar is not None and shiboken6.isValid(minibar) and minibar.view is view:
            minibar.detach()        # altrimenti verrebbe distrutta insieme alla vista

    def _on_view_changed(self, view) -> None:
        if view is None:
            minibar = getattr(self, "minibar", None)
            if minibar is not None and shiboken6.isValid(minibar):
                minibar.detach()
        else:
            self._attach_view(view)
            self.minibar.attach(view)
            hidden = self.ctrl.annotations_hidden(view)
            self.hide_action.blockSignals(True)
            self.hide_action.setChecked(hidden)
            self.hide_action.blockSignals(False)
            if self.ctx.current_mode() == "commenta" and view.tool is view._default_tool:
                view.set_tool(self.select_tool)
            self._sync_actions(view.tool)

    # ------------------------------------------------------------------ menu contestuale
    def _note_on_selection(self, view) -> None:
        """«Aggiungi nota» sul testo selezionato: evidenziazione con il commento scritto nell'editor."""
        from . import dialogs
        res = dialogs.edit_comment(view, _("Nota"), self.ctrl.author(), "")
        if res is None:
            return
        self.tools[engine.HIGHLIGHT].apply_selection(view, contents=res[0])

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
            # voci dirette, come nella specifica (UX-07): Evidenzia, Sottolinea, Barrato, Aggiungi nota
            if not menu.isEmpty():
                menu.addSeparator()
            for key, text in ((engine.HIGHLIGHT, _("Evidenzia")), (engine.UNDERLINE, _("Sottolinea")),
                              (engine.STRIKEOUT, _("Barrato")), (engine.SQUIGGLY, _("Ondulato"))):
                menu.addAction(text, lambda _c=False, k=key: self.tools[k].apply_selection(view))
            menu.addAction(_("Aggiungi nota"), lambda: self._note_on_selection(view))
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


def register(ctx: Context) -> None:
    """Un'istanza del modulo per finestra (Ctrl+N apre più finestre nello stesso processo):
    lo stato resta sul contesto, nessuna variabile globale."""
    add_translations("en", EN)
    module = AnnotationsModule(ctx)
    module.register()
    ctx.annotations = module    # accesso per i test e per altri moduli
