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

#: controller dell'ultima finestra registrata (usato dai test); il modulo usa `controller_for`
CONTROLLER: SecurityController | None = None
#: un controller per finestra (Ctrl+N apre altre finestre nello stesso processo)
_CONTROLLERS: dict[int, SecurityController] = {}


def controller_for(ctx) -> SecurityController | None:
    return _CONTROLLERS.get(id(ctx))


def controllers() -> list[SecurityController]:
    return list(_CONTROLLERS.values())


def register(ctx: Context) -> None:
    global CONTROLLER
    add_translations("en", EN)
    c = SecurityController(ctx)
    c.purge_old_trust()     # riservatezza: percorsi dei documenti attendibili delle versioni precedenti
    CONTROLLER = c
    _CONTROLLERS[id(ctx)] = c

    def forget(*_a, key=id(ctx)):
        global CONTROLLER
        gone = _CONTROLLERS.pop(key, None)
        if CONTROLLER is gone:
            CONTROLLER = next(reversed(_CONTROLLERS.values()), None)
    ctx.window.destroyed.connect(forget)
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
        "envelope": act(_("Cifra solo gli allegati"), "file-lock", c.save_envelope),
        "new_envelope": act(_("Crea busta di sicurezza"), "mail-plus", lambda: c.create_envelope(),
                            requires_document=False),
        "vault": act(_("Spazio protetto"), "vault", c.manage_vault, requires_document=False),
        "cert_encrypt": act(_("Cifra con certificato"), "file-key", c.encrypt_with_certificate),
        "cert_open": act(_("Apri con certificato…"), "key-round", lambda: c.open_with_certificate(),
                         requires_document=False),
    }
    from PySide6.QtWidgets import QMenu
    # Policy di protezione (SEC-12): clic sul pulsante = gestione; freccia del pulsante e
    # File › Opzioni = una voce per policy più «Gestisci policy…»
    c.actions["policies"] = act(_("Policy di protezione"), "shield-plus", c.manage_policies,
                                requires_document=False)
    c.policy_menu = QMenu(ctx.window)
    c.policy_menu.aboutToShow.connect(lambda: c.fill_policy_menu(c.policy_menu))
    c.actions["policies"].setMenu(c.policy_menu)
    # «Cerca un comando» salta le azioni con un menu: la gestione resta cercabile con questa voce
    c.actions["manage_policies"] = act(_("Gestisci policy…"), "shield-ellipsis", c.manage_policies,
                                       requires_document=False)
    c.actions["revoke"] = act(_("Revoca documenti attendibili"), "shield-x", c.revoke_trust,
                              requires_document=False)
    # Blocco dell'applicazione (SEC-32)
    from .applock import AppLock, LockPreferences
    c.applock = AppLock.shared(lambda: c.settings())   # uno per l'applicazione: blocca tutte le finestre
    c.actions["lock"] = act(_("Blocca Foliant"), "lock-keyhole", c.lock_app, requires_document=False)
    c.actions["lock_settings"] = act(_("Blocco dell'applicazione…"), "shield", c.lock_settings,
                                     requires_document=False)
    _build_ribbon(mode, c)
    _build_file_tab(ctx, c)
    c.lock_preferences = LockPreferences
    ctx.add_preferences_page(_("Sicurezza e riservatezza"), lambda parent=None: LockPreferences(c.applock, parent),
                             order=80)

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
    # spazio protetto (SEC-31): i suoi documenti si salvano cifrati al suo interno
    ctx.add_guard("save", c.cert_save_guard)        # SEC-09: avviso prima di perdere la cifratura
    ctx.add_guard("save_as", c.cert_save_guard)
    ctx.add_guard("save", c.vault_save_guard)
    ctx.add_guard("save_as", c.vault_save_guard)
    ctx.add_guard("close_document", c.vault_close_guard)
    ctx.add_guard("quit", c.vault_quit_guard)
    c.applock.suspend_hooks.append(
        lambda: c.lock_vault(auto=True) if getattr(c, "_vault", None) is not None and c._vault.unlocked else None)
    c.applock.start()                          # blocco all'avvio, prima dei file passati da riga di comando
    _update_actions(c, None)


#: voci secondarie della barra: solo l'icona (il nome resta nel suggerimento)
COMPACT = ("remove", "policies", "properties", "text", "pages", "highlights", "style", "panel",
           "inspect", "metadata", "flatten")


def _build_ribbon(mode, c: SecurityController) -> None:
    """Barra Proteggi in tre gruppi con nome, come la barra multifunzione di Excel."""
    a = c.actions
    # etichette più corte nella barra (menu, scheda File e ricerca dei comandi usano il nome intero)
    a["protect"].setIconText(_("Password"))
    a["patterns"].setIconText(_("Dati personali"))
    a["apply"].setIconText(_("Applica"))

    def add(*keys: str) -> None:
        for key in keys:
            mode.add_action(a[key], compact=key in COMPACT)

    mode.add_group(_("Protezione"), "protezione")
    add("protect", "remove")
    c.encryption_button = mode.add_menu_button(
        _("Cifratura"), "key-square",
        [a["cert_encrypt"], a["cert_open"], None, a["envelope"], a["new_envelope"], None, a["vault"]],
        tooltip=_("Cifratura con certificato, buste di sicurezza e spazio protetto"), requires_document=False)
    add("policies", "properties")
    mode.add_group(_("Oscuramento"), "oscuramento")
    add("area", "text", "find", "patterns", "pages", "highlights", "style", "panel", "apply")
    mode.add_group(_("Pulizia"), "pulizia")
    add("inspect", "sanitize", "metadata", "flatten")


def _build_file_tab(ctx: Context, c: SecurityController) -> None:
    """Voci della scheda File: protezione e controllo del documento in Informazioni, apertura con
    certificato in Apri, policy, attendibilità e blocco dell'applicazione in Opzioni."""
    a = c.actions
    protect = _("Proteggi il documento")
    ctx.add_file_action(a["protect"], "informazioni", protect, _("Password di apertura, stampa e modifica"),
                        order=10)
    ctx.add_file_action(a["remove"], "informazioni", protect, _("Toglie password e permessi"), order=11)
    ctx.add_file_action(a["new_envelope"], "informazioni", protect, _("PDF cifrato da inviare con allegati"),
                        order=12)
    check = _("Controlla il documento")
    ctx.add_file_action(a["sanitize"], "informazioni", check, _("Metadati, livelli, script e testo nascosto"),
                        order=20)
    ctx.add_file_action(a["metadata"], "informazioni", check, _("Vedi e cancella i metadati del documento"),
                        order=21)
    ctx.add_file_action(a["cert_open"], "apri", _("Apri"), _("PDF cifrati con un certificato digitale"),
                        order=30)
    security = _("Sicurezza")
    ctx.add_file_action(a["policies"], "opzioni", security, _("Applica o gestisci le protezioni salvate"),
                        order=60)
    ctx.add_file_action(a["revoke"], "opzioni", security, _("Torna a mostrare gli avvisi sui contenuti attivi"),
                        order=61)
    ctx.add_file_action(a["lock"], "opzioni", security, _("Copre le finestre finché non inserisci il codice"),
                        order=62)
    ctx.add_file_action(a["lock_settings"], "opzioni", security,
                        _("Codice, blocco all'avvio e dopo un periodo di inattività"), order=63)


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
    has_files = False
    if doc is not None and not doc.closed:
        from .envelope import has_attachments
        with ENGINE_LOCK:
            has_files = has_attachments(doc.pdf)
    c.actions["envelope"].setEnabled(has_files)   # senza allegati resta solo «Crea busta di sicurezza»


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
