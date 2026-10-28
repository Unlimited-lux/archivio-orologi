"""Modulo «Moduli»: integrazione con l'applicazione (modalità, azioni, pannelli, viste)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import pymupdf
from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import ENGINE_LOCK, OperationCancelled, _, bind_icon, icon

from . import detect as D
from . import engine as E
from . import interchange as X
from .design import KIND_ICONS, FieldCreateTool, FieldSelectTool, paint_fields
from .fill import FormFiller

if TYPE_CHECKING:
    from foliant.api import Context, DocumentView

DATA_FILTER = "FDF (*.fdf);;XFDF (*.xfdf);;CSV (*.csv);;JSON (*.json)"
IMPORT_FILTER = _("Dati del modulo") + " (*.fdf *.xfdf *.csv *.json *.xml);;" + DATA_FILTER

CREATE_KINDS = [E.TEXT, E.CHECKBOX, E.RADIO, E.COMBO, E.LIST, E.BUTTON, "image", "date", E.SIGNATURE]


def create_label(kind: str) -> str:
    return {E.TEXT: _("Testo"), E.CHECKBOX: _("Casella di controllo"), E.RADIO: _("Pulsante di opzione"),
            E.COMBO: _("Elenco a discesa"), E.LIST: _("Casella di riepilogo"), E.BUTTON: _("Pulsante"),
            "image": _("Immagine"), "date": _("Data"), E.SIGNATURE: _("Firma")}[kind]


class FormsModule(QObject):
    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        self.ctx = ctx
        self.fillers: dict[int, FormFiller] = {}
        self.selection: list[tuple[int, int]] = []
        self.design_active = False
        self.preview = False
        self.last_radio_group: str | None = None
        self.select_tool = FieldSelectTool(self)
        self.panel = None
        self.fields_panel = None
        self.last_report: dict = {}

    # ================================================================ registrazione
    def register(self) -> None:
        ctx = self.ctx
        mode = ctx.add_mode("moduli", _("Moduli"), "text-cursor-input", 50)
        self.mode = mode
        self.act_select = ctx.action(_("Seleziona campi"), "mouse-pointer-2", self.activate_select_tool,
                                     checkable=True, tooltip=_("Seleziona, sposta e ridimensiona i campi"))
        mode.add_action(self.act_select)
        self.tool_group = QActionGroup(self)
        self.tool_group.addAction(self.act_select)
        self.create_actions: dict[str, QAction] = {}
        for kind in CREATE_KINDS:
            act = ctx.action(create_label(kind), KIND_ICONS[kind], None, checkable=True,
                             tooltip=_("Crea: {t} (doppio clic per crearne più di uno)").format(t=create_label(kind)))
            act.triggered.connect(lambda _c=False, k=kind: self.start_create(k))
            self.tool_group.addAction(act)
            self.create_actions[kind] = act
        create_menu = QMenu(_("Nuovo campo"), ctx.window)
        for kind in CREATE_KINDS:
            create_menu.addAction(self.create_actions[kind])
        for kind in (E.TEXT, E.CHECKBOX, E.RADIO, E.COMBO):
            mode.add_action(self.create_actions[kind])
            btn = mode.toolbar.widgetForAction(self.create_actions[kind])
            if isinstance(btn, QToolButton):
                btn.setToolButtonStyle(btn.toolButtonStyle())
        more = QToolButton(mode.toolbar)
        more.setText(_("Altri campi"))
        more.setToolTip(_("Altri tipi di campo"))
        bind_icon(more, "plus")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(create_menu)
        more.setToolButtonStyle(mode.toolbar.toolButtonStyle())
        mode.add_widget(more)
        mode.add_separator()
        self.act_detect = ctx.action(_("Riconosci campi"), "scan-search", self.detect_fields,
                                     tooltip=_("Propone i campi su moduli piatti o scansionati"))
        mode.add_action(self.act_detect)
        arrange = QMenu(_("Disponi"), ctx.window)
        self.arrange_actions = {}
        for key, text, ic in (
            ("left", _("Allinea a sinistra"), "align-start-vertical"),
            ("hcenter", _("Allinea al centro orizzontale"), "align-center-vertical"),
            ("right", _("Allinea a destra"), "align-end-vertical"),
            ("top", _("Allinea in alto"), "align-start-horizontal"),
            ("vcenter", _("Allinea al centro verticale"), "align-center-horizontal"),
            ("bottom", _("Allinea in basso"), "align-end-horizontal"),
            (None, None, None),
            ("dist_h", _("Distribuisci orizzontalmente"), "align-horizontal-distribute-center"),
            ("dist_v", _("Distribuisci verticalmente"), "align-vertical-distribute-center"),
            (None, None, None),
            ("same_width", _("Stessa larghezza"), "move-horizontal"),
            ("same_height", _("Stessa altezza"), "move-vertical"),
            ("same_size", _("Stessa larghezza e altezza"), "scaling"),
        ):
            if key is None:
                arrange.addSeparator()
                continue
            a = ctx.action(text, ic, None)
            a.triggered.connect(lambda _c=False, k=key: self.arrange(k))
            arrange.addAction(a)
            self.arrange_actions[key] = a
        b = QToolButton(mode.toolbar)
        b.setText(_("Allinea e distribuisci"))
        bind_icon(b, "align-horizontal-distribute-center")
        b.setMenu(arrange)
        b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        b.setToolButtonStyle(mode.toolbar.toolButtonStyle())
        mode.add_widget(b)
        self.act_duplicate = ctx.action(_("Duplica campo"), "copy-plus", self.duplicate_selected, shortcut="Ctrl+D")
        self.act_copy_pages = ctx.action(_("Copia su più pagine…"), "copy", self.copy_to_pages_dialog)
        self.act_delete = ctx.action(_("Elimina campo"), "trash-2", self.delete_selected)
        tab_menu = QMenu(_("Ordine di tabulazione"), ctx.window)
        tab_menu.addAction(ctx.action(_("Per righe"), "list-ordered", lambda: self.tab_order("row")))
        tab_menu.addAction(ctx.action(_("Per colonne"), "list-ordered", lambda: self.tab_order("column")))
        tab_menu.addAction(ctx.action(_("Ordine della selezione"), "list-ordered", lambda: self.tab_order("selection")))
        tb = QToolButton(mode.toolbar)
        tb.setText(_("Ordine di tabulazione"))
        bind_icon(tb, "list-ordered")
        tb.setMenu(tab_menu)
        tb.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.setToolButtonStyle(mode.toolbar.toolButtonStyle())
        more_menu = QMenu(_("Altro"), ctx.window)
        more_menu.addAction(self.act_duplicate)
        more_menu.addAction(self.act_copy_pages)
        more_menu.addAction(self.act_delete)
        more_menu.addSeparator()
        mode.add_widget(tb)
        mode.add_separator()
        self.act_preview = ctx.action(_("Anteprima compilazione"), "eye", self.toggle_preview, checkable=True)
        mode.add_action(self.act_preview)
        self.act_import = ctx.action(_("Importa dati…"), "file-input", self.import_data_dialog)
        self.act_export = ctx.action(_("Esporta dati…"), "file-output", self.export_data_dialog)
        self.act_reset = ctx.action(_("Reimposta modulo"), "rotate-ccw", self.reset_dialog)
        self.act_flatten = ctx.action(_("Appiattisci modulo…"), "layers", self.flatten_dialog)
        self.act_recalc = ctx.action(_("Ricalcola"), "calculator", self.recalculate)
        self.act_check = ctx.action(_("Verifica modulo"), "list-checks", self.check_form)
        self.act_xfa = ctx.action(_("Converti in modulo standard"), "file-check", self.convert_xfa)
        data_menu = QMenu(_("Dati"), ctx.window)
        for a in (self.act_import, self.act_export, None, self.act_reset, self.act_recalc, self.act_check,
                  None, self.act_flatten, self.act_xfa):
            if a is None:
                data_menu.addSeparator()
            else:
                data_menu.addAction(a)
        db = QToolButton(mode.toolbar)
        db.setText(_("Dati e modulo"))
        bind_icon(db, "file-output")
        db.setMenu(data_menu)
        db.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        db.setToolButtonStyle(mode.toolbar.toolButtonStyle())
        mode.add_widget(db)
        mode.add_action(self.act_delete)
        mode.add_action(self.act_duplicate)
        for a in (self.act_import, self.act_export, self.act_reset, self.act_flatten):
            ctx.add_menu_action(a, _("Moduli"))
        mode.entered.connect(self.enter_design)
        mode.left.connect(self.leave_design)
        ctx.documentOpened.connect(self._attach)
        ctx.currentViewChanged.connect(self._current_changed)
        ctx.documentClosed.connect(self._detach)
        ctx.modeChanged.connect(lambda _m: self._sync_tools())
        ctx.add_view_menu_provider(self.context_menu)
        self._install_panels()
        for view in ctx.views():
            self._attach(view)

    def _install_panels(self) -> None:
        try:
            from .panel import FieldPropertiesPanel
        except Exception:  # pannello non disponibile: il resto del modulo funziona
            FieldPropertiesPanel = None
        if FieldPropertiesPanel is not None:
            self.panel = FieldPropertiesPanel(self.apply_properties)
            self.ctx.add_properties_panel("moduli", self.panel)
        try:
            from .fields_panel import FieldsPanel
        except Exception:
            FieldsPanel = None
        if FieldsPanel is not None:
            self.fields_panel = FieldsPanel(self)
            self.ctx.add_sidebar_panel("moduli-campi", _("Campi"), "text-cursor-input", self.fields_panel, 60)

    # ================================================================ viste
    def filler(self, view: "DocumentView") -> FormFiller:
        f = self.fillers.get(id(view))
        if f is None or f.view is not view:
            f = self._attach(view)
        return f

    def _attach(self, view) -> FormFiller | None:
        if view is None:
            return None
        f = self.fillers.get(id(view))
        if f is not None and f.view is view:
            return f
        f = FormFiller(self.ctx, view, self)
        f.designRequested.connect(lambda: self.ctx.set_mode("moduli"))
        f.resetRequested.connect(self.reset_dialog)
        f.dataRequested.connect(self.data_menu_popup)
        f.changed.connect(self._filler_changed)
        f.design = self.design_active and not self.preview
        self.fillers[id(view)] = f
        return f

    def _detach(self, view) -> None:
        f = self.fillers.pop(id(view), None)
        if f is not None:
            f.dispose()

    def _current_changed(self, view) -> None:
        self.selection = []
        if view is not None:
            f = self.filler(view)
            f.design = self.design_active and not self.preview
            f.update_bar()
            if self.design_active:
                view.set_tool(self.select_tool)
        self._refresh_panels()

    def _filler_changed(self) -> None:
        view = self.ctx.current_view()
        if view is not None:
            f = self.fillers.get(id(view))
            if f is not None:
                keys = {(x.page, x.xref) for x in f.state.fields}
                self.selection = [k for k in self.selection if k in keys]
        self._refresh_panels()

    def highlight_changed(self, on: bool) -> None:
        for f in self.fillers.values():
            f.set_highlight(on)

    def icon(self, name: str):
        return icon(name)

    def exec_popup(self, menu: QMenu, pos) -> None:
        menu.popup(pos)

    # ================================================================ modalità modifica
    def enter_design(self) -> None:
        self.design_active = True
        self.preview = False
        self.act_preview.setChecked(False)
        view = self.ctx.current_view()
        if view is None:
            return
        f = self.filler(view)
        if f.state.xfa == "dynamic":
            self.ctx.status(_("I moduli XML dinamici non si possono modificare."))
        f.close_editor(commit=True)
        f.design = True
        f.update_bar()
        self.activate_select_tool()
        self.show_properties()
        view.viewport().update()

    def leave_design(self) -> None:
        self.design_active = False
        self.preview = False
        for f in self.fillers.values():
            f.design = False
            f.update_bar()
            f.view.viewport().update()
        self.selection = []
        self.ctx.hide_properties()

    def toggle_preview(self, on: bool | None = None) -> None:
        on = self.act_preview.isChecked() if on is None else on
        self.act_preview.setChecked(on)
        self.preview = on
        view = self.ctx.current_view()
        if view is None:
            return
        f = self.filler(view)
        f.close_editor(commit=True)
        f.design = self.design_active and not on
        if on:
            self.ctx.set_tool(None)
            self.selection = []
        else:
            self.activate_select_tool()
        f.update_bar()
        view.viewport().update()

    def _sync_tools(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        tool = view.tool
        self.act_select.setChecked(tool is self.select_tool)
        for kind, act in self.create_actions.items():
            act.setChecked(isinstance(tool, FieldCreateTool) and tool.kind == kind)

    def activate_select_tool(self) -> None:
        if self.ctx.current_mode() != "moduli":
            self.ctx.set_mode("moduli")
        self.ctx.set_tool(self.select_tool)
        self.act_select.setChecked(True)

    def start_create(self, kind: str, sticky: bool | None = None) -> FieldCreateTool | None:
        view = self.ctx.current_view()
        if view is None:
            return None
        if self.ctx.current_mode() != "moduli":
            self.ctx.set_mode("moduli")
        f = self.filler(view)
        if f.state.xfa == "dynamic":
            self.ctx.error(_("Moduli"), _("I moduli XML dinamici non si possono modificare."))
            return None
        if sticky is None:
            current = view.tool
            sticky = isinstance(current, FieldCreateTool) and current.kind == kind and not current.sticky \
                and getattr(self, "_last_create_click", None) == kind
        self._last_create_click = kind
        tool = FieldCreateTool(self, kind, sticky=bool(sticky))
        self.ctx.set_tool(tool)
        self.create_actions[kind].setChecked(True)
        self.ctx.status(_("Fai clic o trascina sulla pagina per creare il campo. Esc per annullare."))
        return tool

    def paint_design(self, view, p, page: int, fields) -> None:
        paint_fields(view, p, page, fields, set(self.selection))

    # ================================================================ selezione e modifiche
    def set_selection(self, keys: list[tuple[int, int]]) -> None:
        self.selection = list(dict.fromkeys(keys))
        view = self.ctx.current_view()
        if view is not None:
            view.viewport().update()
        self._refresh_panels()

    def selected_fields(self, view=None) -> list[E.FieldInfo]:
        view = view or self.ctx.current_view()
        if view is None:
            return []
        f = self.filler(view)
        f.state.refresh()
        out = []
        for page, xref in self.selection:
            info = f.state.find(page, xref)
            if info is not None:
                out.append(info)
        return out

    def _refresh_panels(self) -> None:
        view = self.ctx.current_view()
        if self.panel is not None:
            names: list[str] = []
            radios: dict[str, list[str]] = {}
            if view is not None:
                st = self.filler(view).state
                st.refresh()
                names = list(dict.fromkeys(f.name for f in st.fields))
                for f in st.fields:
                    if f.kind == E.RADIO and f.export:
                        radios.setdefault(f.name, [])
                        if f.export not in radios[f.name]:
                            radios[f.name].append(f.export)
            self.panel.set_fields(self.selected_fields(view) if view else [], names, radio_exports=radios)
        if self.fields_panel is not None:
            self.fields_panel.refresh()

    def show_properties(self) -> None:
        if self.panel is not None:
            self.ctx.show_properties("moduli")

    def _change(self, label: str, fn, view=None) -> Any:
        """Esegue `fn(pdf)` come unico passo annullabile; mostra gli errori all'utente."""
        view = view or self.ctx.current_view()
        if view is None:
            return None
        try:
            with view.document.change(label) as pdf:
                result = fn(pdf)
        except E.FormError as e:
            self.ctx.error(_("Moduli"), str(e))
            return None
        except Exception as e:
            self.ctx.error(_("Moduli"), _("Operazione non riuscita: {e}").format(e=e))
            return None
        f = self.filler(view)
        f.state.refresh(force=True)
        f.update_bar()
        self._filler_changed()
        view.viewport().update()
        return result

    def apply_properties(self, keys: list[tuple[int, int]], props: dict, label: str) -> bool:
        view = self.ctx.current_view()
        if view is None or not keys:
            return False
        if "calc" in props and props["calc"] is not None:
            with ENGINE_LOCK:
                for page, xref in keys:
                    name = E.field_info(view.document.pdf, page, xref).name
                    cycle = E.find_cycle(view.document.pdf, name, props["calc"])
                    if cycle:
                        self.ctx.error(_("Calcolo"), _("Il calcolo creerebbe un ciclo: {c}.").format(c=" → ".join(cycle)))
                        return False
        if "name" in props and len(keys) == 1:
            page, xref = keys[0]
            try:
                with ENGINE_LOCK:
                    E.field_info(view.document.pdf, page, xref)
                result = self._change_raw(view, label, lambda pdf: E.update_properties(pdf, page, xref, **props))
                return result
            except E.NameTaken as e:
                if e.compatible and self.ctx.confirm(_("Nome già usato"), _(
                        "Esiste già un campo «{n}» dello stesso tipo. Unire i due campi? Condivideranno il valore "
                        "(prevale quello del campo esistente).").format(n=e.name)):
                    return self._change(_("Unisci campi"), lambda pdf: E.merge_into(pdf, page, xref, e.name)) is not None \
                        or True
                if not e.compatible:
                    self.ctx.error(_("Nome già usato"), _("Esiste già un campo «{n}» di tipo diverso: scegli un altro nome.").format(n=e.name))
                return False

        def run(pdf):
            for page, xref in keys:
                E.update_properties(pdf, page, xref, **props)
            if "calc" in props:
                E.recalculate(pdf)
            return True

        return bool(self._change(label, run))

    def _change_raw(self, view, label, fn) -> bool:
        """Come _change ma lascia passare NameTaken (serve per proporre l'unione)."""
        try:
            with view.document.change(label) as pdf:
                fn(pdf)
        except E.NameTaken:
            raise
        except E.FormError as e:
            self.ctx.error(_("Moduli"), str(e))
            return False
        f = self.filler(view)
        f.state.refresh(force=True)
        self._filler_changed()
        view.viewport().update()
        return True

    def create_field(self, view, page: int, kind: str, rect: pymupdf.Rect, *, sticky: bool = False,
                     name: str | None = None, **props) -> int | None:
        label = _("Crea campo")
        if kind == E.RADIO and name is None and sticky and self.last_radio_group:
            name = self.last_radio_group
        if kind == "image":
            props.setdefault("caption", "")

        def run(pdf):
            k = E.BUTTON if kind == "image" else kind
            x = E.create_field(pdf, page, k, rect, name, **props)
            if kind == "image":
                E.mark_image_field(pdf, page, x)
            return x

        xref = self._change(label, run, view)
        if xref is None:
            return None
        if kind == E.RADIO:
            with ENGINE_LOCK:
                self.last_radio_group = E.field_info(view.document.pdf, page, xref).name
        self.set_selection([(page, xref)])
        self.show_properties()
        if not sticky:
            self.activate_select_tool() if self.design_active else None
        return xref

    def move_fields(self, view, changes, label: str) -> None:
        self._change(label, lambda pdf: E.move_fields(pdf, [(p, x, E.clamp_rect(pdf, p, r)) for p, x, r in changes]), view)

    def nudge_selected(self, dx: float, dy: float) -> None:
        sel = [f for f in self.selected_fields() if not f.locked]
        if not sel:
            return
        changes = [(f.page, f.xref, pymupdf.Rect(f.rect.x0 + dx, f.rect.y0 + dy, f.rect.x1 + dx, f.rect.y1 + dy))
                   for f in sel]
        self.move_fields(self.ctx.current_view(), changes, _("Sposta campi"))

    def delete_selected(self) -> None:
        sel = self.selected_fields()
        if not sel:
            return

        def run(pdf):
            for f in sel:
                E.delete_field(pdf, f.page, f.xref)

        self._change(_("Elimina campo") if len(sel) == 1 else _("Elimina campi"), run)
        self.set_selection([])

    def duplicate_selected(self) -> list[tuple[int, int]]:
        sel = self.selected_fields()
        if not sel:
            return []

        def run(pdf):
            return [(f.page, E.duplicate_field(pdf, f.page, f.xref, 10, 10)) for f in sel]

        keys = self._change(_("Duplica campo"), run) or []
        self.set_selection(keys)
        return keys

    def copy_to_pages(self, pages: list[int], shared: bool) -> int:
        sel = self.selected_fields()
        if not sel:
            return 0

        def run(pdf):
            n = 0
            for f in sel:
                for p in pages:
                    if p == f.page or not 0 <= p < pdf.page_count:
                        continue
                    E.copy_field_to_page(pdf, f.page, f.xref, p, shared=shared)
                    n += 1
            return n

        return self._change(_("Copia campi su più pagine"), run) or 0

    def copy_to_pages_dialog(self) -> None:
        from .dialogs import ask_copy_pages
        view = self.ctx.current_view()
        if view is None or not self.selected_fields():
            self.ctx.status(_("Seleziona prima uno o più campi."))
            return
        answer = ask_copy_pages(self.ctx.window, view.page_count, view.current_page)
        if answer:
            pages, shared = answer
            n = self.copy_to_pages(pages, shared)
            self.ctx.status(_("Copie create: {n}").format(n=n))

    def arrange(self, how: str) -> None:
        sel = [f for f in self.selected_fields() if not f.locked]
        if len(sel) < 2:
            self.ctx.status(_("Seleziona almeno due campi."))
            return
        if how.startswith("dist_"):
            if len(sel) < 3:
                self.ctx.status(_("Per distribuire servono almeno tre campi."))
                return
            pages = {f.page for f in sel}
            if len(pages) > 1:
                self.ctx.status(_("I campi da distribuire devono essere sulla stessa pagina."))
                return
            rects = E.distribute_rects([f.rect for f in sel], "h" if how == "dist_h" else "v")
        else:
            rects = E.align_rects([f.rect for f in sel], how, reference=0)
        changes = [(f.page, f.xref, r) for f, r in zip(sel, rects)]
        self.move_fields(self.ctx.current_view(), changes, _("Disponi campi"))

    def tab_order(self, how: str) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        page = view.current_page
        if how == "selection":
            keys = [x for p, x in self.selection if p == page]
            if not keys:
                self.ctx.status(_("Seleziona i campi della pagina nell'ordine desiderato (Ctrl+clic)."))
                return
            self._change(_("Ordine di tabulazione"), lambda pdf: E.set_tab_order(pdf, page, keys), view)
        else:
            self._change(_("Ordine di tabulazione"), lambda pdf: E.auto_tab_order(pdf, page, how), view)
        self.ctx.status(_("Ordine di tabulazione aggiornato."))

    # ================================================================ comandi sui dati
    def _view_or_status(self):
        view = self.ctx.current_view()
        if view is None:
            return None
        return view

    def values_for_export(self, view) -> dict[str, Any]:
        with ENGINE_LOCK:
            pdf = view.document.pdf
            fields = E.list_fields(pdf)
            values = E.form_values(pdf)
        skip = {f.name for f in fields if f.password or f.no_export}
        return {k: v for k, v in values.items() if k not in skip}

    def export_data(self, path: str, fmt: str | None = None, view=None) -> int:
        view = view or self.ctx.current_view()
        fmt = fmt or X.format_of(path)
        values = self.values_for_export(view)
        with ENGINE_LOCK:
            buttons = {f.name for f in E.list_fields(view.document.pdf) if f.kind in (E.CHECKBOX, E.RADIO)}
        data = X.export_data(fmt, values, view.document.display_name, buttons)
        from foliant.api import atomic_write
        atomic_write(path, data)
        return len(values)

    def export_data_dialog(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        base = os.path.splitext(view.document.display_name)[0]
        path = self.ctx.ask_save_path(_("Esporta dati del modulo"), base + ".xfdf", DATA_FILTER, ".xfdf")
        if not path:
            return
        try:
            n = self.export_data(path)
        except Exception as e:
            self.ctx.error(_("Esporta dati"), _("Esportazione non riuscita: {e}").format(e=e))
            return
        self.ctx.status(_("Esportati {n} campi in «{f}».").format(n=n, f=os.path.basename(path)))

    def import_values(self, values: dict[str, Any], view=None) -> dict:
        """Applica i valori (un solo passo annullabile). Restituisce il rapporto."""
        view = view or self.ctx.current_view()
        report = {"applied": [], "unknown": [], "rejected": []}

        def run(pdf):
            E.prepare_for_edit(pdf)
            by_name = {}
            for f in E.list_fields(pdf):
                by_name.setdefault(f.name, f)
            for name, value in values.items():
                f = by_name.get(name)
                if f is None or f.kind not in E.FILLABLE:
                    report["unknown"].append(name)
                    continue
                if f.readonly:
                    report["rejected"].append((name, _("campo in sola lettura")))
                    continue
                try:
                    E.apply_values(pdf, {name: value})
                    report["applied"].append(name)
                except E.FormError as e:
                    report["rejected"].append((name, str(e)))
            E.recalculate(pdf)
            if E.xfa_status(pdf) == "hybrid":
                E.sync_xfa_from_fields(pdf)

        self._change(_("Importa dati del modulo"), run, view)
        self.last_report = report
        return report

    def import_data(self, path: str, row: int | None = None, view=None) -> dict:
        view = view or self.ctx.current_view()
        with open(path, "rb") as fh:
            data = fh.read()
        fmt = X.format_of(path, data)
        if fmt == "csv":
            records = X.csv_records(data)
            if not records:
                raise X.InterchangeError(_("Il file CSV non contiene righe di dati."))
            if row is None:
                row = self.choose_csv_row(records) if len(records) > 1 else 0
                if row is None:
                    raise OperationCancelled()
            if not 0 <= row < len(records):
                raise X.InterchangeError(_("Il file CSV ha solo {n} righe di dati.").format(n=len(records)))
            values = records[row]
        else:
            values = X.parse_data(fmt, data)
        report = self.import_values(values, view)
        if fmt == "fdf" and any(k in data for k in (b"/Ff", b"/SetFf", b"/ClrFf", b"/SetF", b"/ClrF")):
            report.setdefault("ignored", []).append(_("istruzioni di modifica dei flag"))
        return report

    def choose_csv_row(self, records: list[dict]) -> int | None:
        from .dialogs import ask_csv_row
        return ask_csv_row(self.ctx.window, records)

    def import_data_dialog(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        paths = self.ctx.ask_open_paths(_("Importa dati del modulo"), IMPORT_FILTER, multiple=False)
        if not paths:
            return
        try:
            report = self.import_data(paths[0])
        except OperationCancelled:
            return
        except (X.InterchangeError, OSError) as e:
            self.ctx.error(_("Importa dati"), str(e))
            return
        self.ctx.info(_("Importa dati"), report_text(report))

    def data_menu_popup(self) -> None:
        menu = QMenu(self.ctx.window)
        menu.addAction(self.act_import)
        menu.addAction(self.act_export)
        from PySide6.QtGui import QCursor
        self.exec_popup(menu, QCursor.pos())

    def reset(self, names: list[str] | None = None, view=None) -> int:
        view = view or self.ctx.current_view()
        f = self.filler(view)
        if names is None:
            f.passwords.clear()
        else:
            for n in names:
                f.passwords.pop(n, None)

        def run(pdf):
            n = E.reset_form(pdf, names)
            E.recalculate(pdf)
            return n

        return self._change(_("Reimposta modulo"), run, view) or 0

    def reset_dialog(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        sel = self.selected_fields(view) if self.design_active else []
        from .dialogs import ask_reset
        choice = ask_reset(self.ctx.window, len(sel))
        if choice is None:
            return
        names = list(dict.fromkeys(f.name for f in sel)) if choice == "selected" else None
        n = self.reset(names)
        self.ctx.status(_("Campi reimpostati: {n}").format(n=n))

    def flatten(self, pages: list[int] | None = None, view=None) -> int:
        view = view or self.ctx.current_view()
        with ENGINE_LOCK:
            signed = [f.name for f in E.list_fields(view.document.pdf) if f.kind == E.SIGNATURE and E.is_signed(view.document.pdf, f.xref)]
        n = self._change(_("Appiattisci modulo"), lambda pdf: E.flatten_pages(pdf, pages), view) or 0
        if signed:
            self.ctx.status(_("I campi firma firmati non sono stati appiattiti."))
        return n

    def flatten_dialog(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        from .dialogs import ask_flatten
        answer = ask_flatten(self.ctx.window, view.page_count)
        if answer is None:
            return
        n = self.flatten(answer)
        self.ctx.status(_("Campi appiattiti: {n}").format(n=n))

    def recalculate(self) -> None:
        names = self._change(_("Ricalcola"), E.recalculate) or []
        self.ctx.status(_("Campi ricalcolati: {n}").format(n=len(names)))

    def check_form(self) -> list[tuple[E.FieldInfo, str]]:
        view = self.ctx.current_view()
        if view is None:
            return []
        filler = self.filler(view)
        problems: list[tuple[E.FieldInfo, str]] = []
        filler.state.refresh(force=True)
        for f in filler.state.required_empty(filler.passwords):
            problems.append((f, _("obbligatorio non compilato")))
        seen = set()
        for f in filler.state.fields:
            if f.kind == E.TEXT and f.name not in seen and f.value:
                seen.add(f.name)
                err = E.validate(f, f.value)
                if err:
                    problems.append((f, err))
        if problems:
            from .dialogs import show_problems
            target = show_problems(self.ctx.window, problems)
            if target is not None:
                filler.activate(target)
        else:
            self.ctx.info(_("Verifica modulo"), _("Il modulo è completo e valido."))
        return problems

    def convert_xfa(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        with ENGINE_LOCK:
            status = E.xfa_status(view.document.pdf)
        if status == "none":
            self.ctx.info(_("Moduli XML"), _("Il documento non contiene un modulo XML."))
            return
        if status == "dynamic":
            self.ctx.error(_("Moduli XML"), _("La conversione dei moduli XML dinamici non è disponibile: "
                                              "i dati si possono comunque esportare."))
            return
        if self.ctx.confirm(_("Moduli XML"), _("Rimuovere la parte XML e mantenere solo i campi standard?")):
            self._change(_("Converti in modulo standard"), E.remove_xfa, view)

    # ================================================================ rilevamento
    def detect_fields(self) -> None:
        view = self.ctx.current_view()
        if view is None:
            return
        doc = view.document
        page_count = view.page_count

        def work(progress):
            with ENGINE_LOCK:
                # copia dei dati: il motore non va usato senza lock fuori da qui
                data = doc.pdf.tobytes(garbage=0)
            pdf = pymupdf.open("pdf", data)
            try:
                return D.detect(pdf, list(range(page_count)), progress=lambda fr, t: progress.update(fr, t))
            finally:
                pdf.close()

        try:
            candidates = self.ctx.run_task(_("Riconoscimento dei campi"), work)
        except OperationCancelled:
            return
        except Exception as e:
            self.ctx.error(_("Riconosci campi"), _("Riconoscimento non riuscito: {e}").format(e=e))
            return
        if not candidates:
            self.ctx.info(_("Riconosci campi"), _("Nessun campo riconosciuto."))
            return
        from .dialogs import review_candidates
        accepted = review_candidates(self.ctx.window, candidates)
        if accepted:
            n = self.create_candidates(accepted, view)
            self.ctx.status(_("Campi creati: {n}").format(n=n))

    def create_candidates(self, candidates: list, view=None) -> int:
        view = view or self.ctx.current_view()

        def run(pdf):
            n = 0
            for c in candidates:
                kind = E.CHECKBOX if c.kind == "checkbox" else E.TEXT
                name = c.name or None
                if name and name in E.field_names(pdf):
                    name = E.unique_name(pdf, name)
                props = {}
                if kind == E.TEXT and getattr(c, "multiline", False):
                    props["multiline"] = True
                if getattr(c, "comb", 0):
                    props.update(comb=True, maxlen=int(c.comb))
                E.create_field(pdf, c.page, kind, c.rect, name, **props)
                n += 1
            return n

        return self._change(_("Crea campi riconosciuti"), run, view) or 0

    # ================================================================ pulsanti e firme
    def run_button(self, filler: FormFiller, f: E.FieldInfo) -> None:
        with ENGINE_LOCK:
            pdf = filler.document.pdf
            if E.is_image_field(pdf, f.xref):
                action = {"type": "image"}
            else:
                action = E.button_action(pdf, f.xref)
        kind = action.get("type")
        if kind == "image":
            paths = self.ctx.ask_open_paths(_("Scegli un'immagine"), _("Immagini") + " (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp)", multiple=False)
            if paths:
                self._change(_("Inserisci immagine"), lambda pdf: E.set_button_image(pdf, f.page, f.xref, paths[0]), filler.view)
            return
        if kind == "reset":
            names = action.get("fields")
            self.reset(names if names else None, filler.view)
        elif kind == "uri":
            uri = action.get("uri", "")
            if self.ctx.confirm(_("Collegamento esterno"), _("Il modulo vuole aprire questo indirizzo:\n\n{u}\n\nAprirlo?").format(u=uri)):
                QDesktopServices.openUrl(QUrl(uri))
        elif kind == "goto":
            filler.view.go_to_page(action.get("page", 0))
        elif kind == "print":
            act = getattr(self.ctx.window, "act_print", None)
            if act is not None:
                act.trigger()
        elif kind == "import":
            self.import_data_dialog()
        elif kind == "hide":
            self._change(_("Mostra/nascondi campi"),
                         lambda pdf: E.hide_fields(pdf, action.get("fields", []), action.get("hide", True)), filler.view)
        elif kind == "submit":
            self.ctx.info(_("Invia modulo"), _("L'invio del modulo a un indirizzo esterno non è disponibile: "
                                                "esporta i dati e inviali separatamente."))
        elif kind == "javascript":
            self.ctx.status(_("Il pulsante esegue uno script personalizzato, non supportato."))
        else:
            self.ctx.status(_("Il pulsante «{n}» non ha azioni.").format(n=f.name))

    def signature_clicked(self, view, f: E.FieldInfo) -> None:
        if self.ctx.mode("firma") is not None:
            self.ctx.set_mode("firma")
        self.ctx.status(_("Campo firma «{n}»: usa la modalità Firma per firmare.").format(n=f.name))

    # ================================================================ menu contestuale
    def context_menu(self, view, ev, menu: QMenu) -> None:
        if ev.page is None or ev.point is None:
            return
        filler = self.filler(view)
        f = filler.state.field_at(ev.page, ev.point)
        if f is None:
            return
        menu.addSection(f.name)
        if self.design_active and not self.preview:
            if (f.page, f.xref) not in self.selection:
                self.set_selection([(f.page, f.xref)])
            menu.addAction(icon("sliders-horizontal"), _("Proprietà"), self.show_properties)
            menu.addAction(icon("copy-plus"), _("Duplica campo"), self.duplicate_selected)
            menu.addAction(icon("trash-2"), _("Elimina campo"), self.delete_selected)
        else:
            if f.kind in E.FILLABLE and not f.readonly:
                menu.addAction(icon("rotate-ccw"), _("Reimposta campo"), lambda: self.reset([f.name], view))
            menu.addAction(icon("square-pen"), _("Modifica modulo"), lambda: self.ctx.set_mode("moduli"))


def report_text(report: dict) -> str:
    lines = [_("Campi compilati: {n}").format(n=len(report.get("applied", [])))]
    if report.get("unknown"):
        lines.append(_("Campi non trovati nel modulo: {l}").format(l=", ".join(report["unknown"])))
    for name, why in report.get("rejected", []):
        lines.append(_("Valore rifiutato per «{n}»: {w}").format(n=name, w=why))
    for what in report.get("ignored", []):
        lines.append(_("Ignorate: {w}").format(w=what))
    return "\n".join(lines)
