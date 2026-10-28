"""Modulo Proteggi: password e permessi, redazione, sanificazione, metadati, avvisi.

Logica pura in `protection.py`, `redaction.py`, `patterns.py`, `sanitize.py`,
`links.py`; interfaccia in `dialogs.py`, `widgets.py`; flussi in `controller.py`.
"""

from __future__ import annotations

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
        "text": act(_("Segna testo selezionato"), "text-select", c.mark_selection, shortcut="Ctrl+Shift+R"),
        "find": act(_("Trova e segna"), "text-search", lambda: c.find_and_mark("terms")),
        "patterns": act(_("Trova dati personali"), "scan-search", lambda: c.find_and_mark("patterns")),
        "pages": act(_("Segna pagine"), "file-x", c.mark_pages),
        "style": act(_("Impostazioni di redazione"), "palette", c.redaction_settings, requires_document=False),
        "panel": act(_("Elenco redazioni"), "list-checks", c.show_panel),
        "apply": act(_("Applica redazioni"), "check-check", lambda: c.apply_redactions(None),
                     requires_document=False),
        "inspect": act(_("Esamina documento"), "scan-eye", c.inspect),
        "sanitize": act(_("Sanifica"), "spray-can", c.sanitize),
        "metadata": act(_("Metadati"), "file-pen", c.edit_metadata),
        "flatten": act(_("Appiattisci"), "layers", c.flatten),
    }
    groups = [("protect", "remove", "properties"),
              ("area", "text", "find", "patterns", "pages", "style", "panel", "apply"),
              ("inspect", "sanitize", "metadata", "flatten")]
    for n, group in enumerate(groups):
        if n:
            mode.add_separator()
        for key in group:
            mode.add_action(c.actions[key])
    for key in ("protect", "remove", "sanitize", "metadata"):
        ctx.add_menu_action(c.actions[key], _("Proteggi"))

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
    ctx.documentChanged.connect(lambda v: update_status(v) if v is ctx.current_view() else None)
    ctx.add_view_menu_provider(lambda view, ev, menu: _context_menu(c, view, ev, menu))
    links.install()
    _update_actions(c, None)


def _update_actions(c: SecurityController, view) -> None:
    doc = view.document if view is not None else None
    has_marks = False
    if doc is not None and not doc.closed:
        with ENGINE_LOCK:
            try:
                has_marks = R.has_marks(doc.pdf)
            except Exception:
                has_marks = False
    apply = c.actions["apply"]
    apply.setEnabled(doc is not None and (has_marks or bool(c.pending_outside.get(doc))))
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
    with ENGINE_LOCK:
        hit = [m for m in R.marks(view.document.pdf, [ev.page]) if m.rect.contains(ev.point)]
    if hit:
        m = hit[0]
        menu.addSeparator()
        menu.addAction(_("Rimuovi segno di redazione")).triggered.connect(lambda: c.remove_marks([m]))
        if not m.whole_page:
            menu.addAction(_("Ripeti su più pagine…")).triggered.connect(lambda: c.repeat_mark(m.page, m.xref))
        menu.addAction(_("Applica questo segno")).triggered.connect(lambda: c.apply_redactions([m]))
    if c.ctx.current_mode() == "proteggi":
        menu.addAction(_("Oscura questa pagina")).triggered.connect(lambda: _mark_page(c, view, ev.page))


def _mark_page(c: SecurityController, view, page: int) -> None:
    if not c.require_modify(view.document):
        return
    with view.document.change(_("Segna pagina per redazione")) as pdf:
        R.mark_whole_pages(pdf, [page], c.style)
    c.show_panel()
