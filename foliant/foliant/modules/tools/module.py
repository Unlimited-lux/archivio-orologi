"""Registrazione del modulo Strumenti: modalità, azioni, menu e Home."""

from __future__ import annotations

from foliant.api import _, add_translations

from .translations import EN


class ToolsModule:
    def __init__(self, ctx):
        from .compare_ui import CompareController
        self.ctx = ctx
        self.compare = CompareController(ctx)
        self.batch = None

    # ---------------------------------------------------------------- confronto
    def start_compare(self, view=None):
        return self.compare.start(view)

    def show_compare(self):
        if self.compare.widget is None:
            self.ctx.info(_("Confronto"), _("Nessun confronto aperto. Usa «Confronta documenti»."))
            return
        self.compare.reshow()

    def open_session(self):
        return self.compare.open_session()

    # ---------------------------------------------------------------- in blocco
    def show_batch(self):
        from .batch_ui import BatchWidget
        if self.batch is None:
            self.batch = BatchWidget(self.ctx)
        self.ctx.set_central_widget(self.batch)
        return self.batch


def register(ctx) -> None:
    add_translations("en", EN)
    module = ToolsModule(ctx)
    ctx.tools_module = module
    mode = ctx.add_mode("strumenti", _("Strumenti"), "wrench", 100)
    act_compare = ctx.action(_("Confronta documenti"), "git-compare", lambda: module.start_compare(),
                             requires_document=False, tooltip=_("Confronta due versioni di un PDF"))
    act_show = ctx.action(_("Mostra confronto"), "diff", module.show_compare, requires_document=False)
    act_session = ctx.action(_("Apri sessione di confronto"), "folder-open", module.open_session,
                             requires_document=False)
    act_batch = ctx.action(_("Operazioni in blocco"), "layers", module.show_batch, requires_document=False,
                           tooltip=_("Applica operazioni a molti file"))
    mode.add_action(act_compare)
    mode.add_action(act_show)
    mode.add_action(act_session)
    mode.add_separator()
    mode.add_action(act_batch)
    ctx.add_menu_action(act_compare, _("Strumenti"))
    ctx.add_menu_action(act_batch, _("Strumenti"))
    ctx.add_home_action(_("Confronta documenti"), "git-compare", lambda: module.start_compare(),
                        _("Trova le differenze tra due versioni"), order=100)
    ctx.add_home_action(_("Operazioni in blocco"), "layers", module.show_batch,
                        _("Comprimi, proteggi, numera, unisci molti file insieme"), order=101)

    def menu_provider(view, page_event, menu):
        act = menu.addAction(_("Confronta con…"))
        from foliant.api import bind_icon
        bind_icon(act, "git-compare")
        act.triggered.connect(lambda _c=False, v=view: module.start_compare(v))

    ctx.add_view_menu_provider(menu_provider)
    module.actions = {"compare": act_compare, "show": act_show, "session": act_session, "batch": act_batch}
    module.mode = mode
