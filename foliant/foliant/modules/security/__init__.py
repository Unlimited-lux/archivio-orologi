"""Modulo Proteggi: password e permessi, redazione, sanificazione, metadati, avvisi.

Logica pura in `protection.py`, `redaction.py`, `patterns.py`, `sanitize.py`,
`links.py`; interfaccia in `dialogs.py`, `widgets.py`; flussi in `controller.py`.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolButton

from foliant.api import ENGINE_LOCK, Context, _, add_translations, bind_icon

from . import links
from . import protection as P
from . import redaction as R
from .controller import PANEL_ID, SecurityController
from .translations import EN

ORDER = 80

#: controller dell'ultima finestra registrata (usato dai test)
CONTROLLER: SecurityController | None = None


def register(ctx: Context) -> None:
    global CONTROLLER
    add_translations("en", EN)
    c = SecurityController(ctx)
    CONTROLLER = c
    mode = ctx.add_mode("proteggi", _("Proteggi"), "shield", 80)

    def act(text, icon_name, slot, **kw):
        a = ctx.action(text, icon_name, slot, **kw)
        return a

    c.actions = {
        "protect": act(_("Password e permessi"), "lock", c.protect),
        "remove": act(_("Rimuovi protezione"), "lock-open", c.remove_protection, requires_document=False),
        "properties": act(_("Proprietà di sicurezza"), "shield-check", c.security_properties),
        "area": act(_("Segna area"), "square-dashed", c.activate_area_tool),
        "text": act(_("Segna testo"), "text-select", c.mark_selection, shortcut="Ctrl+Shift+R",
                   mode="proteggi"),
        "find": act(_("Trova e segna"), "text-search", lambda: c.find_and_mark("terms")),
        "patterns": act(_("Trova dati personali"), "scan-search", lambda: c.find_and_mark("patterns")),
        "pages": act(_("Segna pagine"), "file-x", c.mark_pages),
        "highlights": act(_("Converti evidenziazioni in segni"), "highlighter", lambda: c.convert_highlights()),
        "style": act(_("Impostazioni di redazione"), "palette", c.redaction_settings, requires_document=False),
        "panel": act(_("Elenco redazioni"), "list-checks", c.show_panel),
        "apply": act(_("Applica redazioni"), "check-check", lambda: c.apply_redactions(None),
                     requires_document=False),
        "inspect": act(_("Esamina documento"), "scan-eye", c.inspect),
        "sanitize": act(_("Rimuovi dati nascosti"), "spray-can", c.sanitize),
        "metadata": act(_("Metadati"), "file-pen", c.edit_metadata),
        "flatten": act(_("Appiattisci"), "layers", c.flatten),
    }
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMenu
    # Policy di protezione (SEC-12): nel menu Proteggi un sottomenu con una voce per policy
    c.actions["policies"] = act(_("Policy di protezione"), "shield-plus", c.manage_policies,
                                requires_document=False)
    c.policy_menu = QMenu(ctx.window)
    c.policy_menu.aboutToShow.connect(lambda: c.fill_policy_menu(c.policy_menu))
    for key in ("policies", "remove", "properties", "style", "panel", "inspect", "metadata", "flatten", "pages",
                "text", "highlights"):
        c.actions[key].setPriority(QAction.Priority.LowPriority)   # solo icona nella barra
    groups = [("protect", "policies", "remove", "properties"),
              ("area", "text", "find", "patterns", "pages", "highlights", "style", "panel", "apply"),
              ("inspect", "sanitize", "metadata", "flatten")]
    for n, group in enumerate(groups):
        if n:
            mode.add_separator()
        for key in group:
            mode.add_action(c.actions[key])
    for key in ("protect", "remove", "sanitize", "metadata"):
        ctx.add_menu_action(c.actions[key], _("Proteggi"))
    menu_policy = ctx.action(_("Policy di protezione"), "shield-plus", c.manage_policies, requires_document=False)
    menu_policy.setMenu(c.policy_menu)
    ctx.add_menu_action(menu_policy, _("Proteggi"))
    revoke = ctx.action(_("Revoca documenti attendibili"), "shield-x", c.revoke_trust, requires_document=False)
    ctx.add_menu_action(revoke, _("Proteggi"))

    ctx.add_properties_panel(PANEL_ID, c.panel)
    mode.entered.connect(lambda: ctx.show_properties(PANEL_ID) if ctx.current_view() else None)

    # lucchetto nella barra di stato (tipo di accesso)
    lock = QToolButton()
    lock.setAutoRaise(True)
    lock.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    lock.clicked.connect(c.security_properties)
    lock.hide()
    try:
        ctx.window.statusBar().addPermanentWidget(lock)
    except Exception:
        pass
    c.lock_button = lock

    def update_status(view) -> None:
        doc = view.document if view is not None else None
        if doc is None or (not doc.is_protected and not doc.original_protection):
            lock.hide()
            _update_actions(c, view)
            return
        full = P.owner_known(doc)
        bind_icon(lock, "lock" if doc.is_protected else "lock-open")
        lock.setText(_("Accesso completo") if full else _("Accesso limitato"))
        lock.setToolTip(P.describe(doc))
        lock.show()
        _update_actions(c, view)

    c.status_callback = update_status

    def on_view(view) -> None:
        update_status(view)
        if view is None:
            c.set_preview(False)

    ctx.documentOpened.connect(c.on_document_opened)
    ctx.currentViewChanged.connect(on_view)
    ctx.add_view_menu_provider(lambda view, ev, menu: _context_menu(c, view, ev, menu))
    links.install(ctx)
    ctx.add_guard("save", c.save_guard)        # SEC-19: segni non applicati prima di salvare
    ctx.add_guard("save_as", c.save_guard)
    ctx.add_guard("print", c.save_guard)       # anche prima di stampare (moduli che usano run_guards)
    _update_actions(c, None)


def _update_actions(c: SecurityController, view) -> None:
    doc = view.document if view is not None else None
    has_marks = False
    if doc is not None and not doc.closed:
        try:
            has_marks = c.has_marks(doc)         # in cache per versione del documento
        except Exception:
            has_marks = False
    # SEC-02: senza permesso di modifica i comandi che cambiano il contenuto sono disattivati
    # (la barra «Documento con restrizioni» offre «Sblocca…»)
    can_modify = doc is None or doc.closed or doc.allows(pymupdf.PDF_PERM_MODIFY)
    for key in ("area", "text", "find", "patterns", "pages", "highlights", "sanitize", "flatten"):
        a = c.actions[key]
        base = a.property("security_tip") or a.toolTip()
        a.setProperty("security_tip", base)
        a.setEnabled(doc is not None and can_modify)
        a.setToolTip(base if can_modify else _("{cmd}: il documento non consente modifiche. Usa «Sblocca…» con "
                                                 "la password dei permessi.").format(cmd=a.text()))
    apply = c.actions["apply"]
    apply.setEnabled(doc is not None and can_modify and (has_marks or bool(c.pending_outside.get(doc))))
    apply.setToolTip(_("Applica redazioni") if apply.isEnabled()
                     else _("Nessuna area contrassegnata per la redazione"))
    c.actions["remove"].setEnabled(doc is not None and doc.is_protected)


def _context_menu(c: SecurityController, view, ev, menu) -> None:
    if ev is None or ev.page is None:
        return
    sel = view.text_selection
    if sel is not None and sel.rects:
        a = menu.addAction(_("Contrassegna per redazione"))
        a.triggered.connect(c.mark_selection)
    hit = []
    if ev.page in c.marked_pages(view.document):
        with ENGINE_LOCK:
            hit = [m for m in R.marks(view.document.pdf, [ev.page]) if m.rect.contains(ev.point)]
    if hit:
        m = hit[0]
        menu.addSeparator()
        menu.addAction(_("Rimuovi segno di redazione")).triggered.connect(lambda: c.remove_marks([m]))
        if not m.whole_page:
            menu.addAction(_("Ripeti su più pagine…")).triggered.connect(lambda: c.repeat_mark(m.page, m.xref))
        menu.addAction(_("Applica questo segno")).triggered.connect(lambda: c.apply_redactions([m]))
    with ENGINE_LOCK:
        try:
            page = view.document.pdf[ev.page]
            highlights = [x for x, kind, _id in page.annot_xrefs() if kind == pymupdf.PDF_ANNOT_HIGHLIGHT
                          and page.load_annot(x) is not None and page.load_annot(x).rect.contains(ev.point)]
        except Exception:
            highlights = []
    if highlights:
        menu.addAction(_("Converti in segni di redazione")).triggered.connect(
            lambda: c.convert_highlights(view.document, set(highlights)))
    if c.ctx.current_mode() == "proteggi":
        menu.addAction(_("Oscura questa pagina")).triggered.connect(lambda: _mark_page(c, view, ev.page))


def _mark_page(c: SecurityController, view, page: int) -> None:
    if not c.require_modify(view.document):
        return
    with c._change(view.document, _("Segna pagina per redazione")) as pdf:
        R.mark_whole_pages(pdf, [page], c.style)
    c.show_panel()
