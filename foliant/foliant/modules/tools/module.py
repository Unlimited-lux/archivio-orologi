"""Registrazione del modulo Strumenti: modalità, azioni, menu e Home."""

from __future__ import annotations

from foliant.api import _, add_translations

from .translations import EN


class ToolsModule:
    def __init__(self, ctx):
        from .compare_ui import CompareController
        from .privacy import cleanup_legacy
        from .watch import WatchService
        self.ctx = ctx
        # riservatezza: dati delle versioni precedenti con percorsi dei file (solo se presenti)
        try:
            cleanup_legacy(ctx.settings())
        except Exception:
            pass
        self.compare = CompareController(ctx)
        self.batch = None
        # cartelle monitorate solo per la sessione: niente configurazione né registro su disco
        self.watch_service = WatchService()

    # ---------------------------------------------------------------- confronto
    def start_compare(self, view=None):
        return self.compare.start(view)

    def show_compare(self):
        if self.compare.widget is None:
            self.ctx.notify(_("Nessun confronto aperto. Usa «Confronta documenti»."))
            return
        self.compare.reshow()

    def open_session(self):
        return self.compare.open_session()

    def compare_text(self):
        from .text_compare import TextCompareDialog
        dlg = TextCompareDialog(self.ctx, self.ctx.window)
        dlg.show()
        self._text_dialog = dlg
        return dlg

    def show_revisions(self):
        from .revisions import RevisionsDialog
        dlg = RevisionsDialog(self.ctx, self.ctx.current_view())
        dlg.show()
        self._revisions_dialog = dlg
        return dlg

    # ---------------------------------------------------------------- in blocco
    def show_batch(self):
        from .batch_ui import BatchWidget
        if self.batch is None:
            self.batch = BatchWidget(self.ctx)
        if self.ctx.mode("strumenti") is not None:
            self.ctx.set_mode("strumenti")
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
    act_text = ctx.action(_("Confronta testo"), "file-diff", module.compare_text, requires_document=False,
                          tooltip=_("Confronta due testi incollati o caricati da file"))
    act_revisions = ctx.action(_("Revisioni"), "history", module.show_revisions,
                               tooltip=_("Confronta le revisioni salvate nello stesso file"))
    act_batch = ctx.action(_("Operazioni in blocco"), "layers", module.show_batch, requires_document=False,
                           tooltip=_("Applica operazioni a molti file"))
    # scheda Strumenti divisa in gruppi come la barra multifunzione di Excel: Confronta | Automazione
    act_session.setIconText(_("Apri sessione"))
    mode.add_group(_("Confronta"), "confronta")
    for act in (act_compare, act_show, act_session, act_text, act_revisions):
        mode.add_action(act, group="confronta")
    mode.add_group(_("Automazione"), "automazione")
    mode.add_action(act_batch, group="automazione")
    ctx.add_home_action(_("Confronta documenti"), "git-compare", lambda: module.start_compare(),
                        _("Trova le differenze tra due versioni"), order=100)
    ctx.add_home_action(_("Operazioni in blocco"), "layers", module.show_batch,
                        _("Comprimi, proteggi, numera, unisci molti file insieme"), order=101)

    def tab_menu(view, menu):
        from foliant.api import bind_icon
        act = menu.addAction(_("Confronta con…"))
        bind_icon(act, "git-compare")
        act.triggered.connect(lambda _c=False, v=view: module.start_compare(v))

    ctx.add_tab_menu_provider(tab_menu)
    module.tab_menu = tab_menu

    module.actions = {"compare": act_compare, "show": act_show, "session": act_session, "batch": act_batch,
                      "text": act_text, "revisions": act_revisions}
    module.mode = mode
