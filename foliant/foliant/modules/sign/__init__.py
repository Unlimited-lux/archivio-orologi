"""Modulo Firma: firma grafica (disegnata, digitata, da immagine) con libreria
locale, firma digitale PAdES con pyHanko, verifica e pannello delle firme."""

from __future__ import annotations

from PySide6.QtWidgets import QMenu, QToolButton

from foliant.api import Context, _, add_translations, bind_icon

ORDER = 90


def _translations() -> dict:
    from . import controller, digital, graphic, library, panel, store, tools, ui_create, ui_digital, ui_library
    merged: dict = {}
    for mod in (graphic, library, digital, store, tools, ui_create, ui_library, ui_digital, panel, controller):
        merged.update(getattr(mod, "TRANSLATIONS_EN", {}))
    merged.update({
        "Firma": "Sign", "Crea firma": "Create signature", "Crea iniziali": "Create initials",
        "Firme salvate": "Saved signatures", "Gestisci libreria…": "Manage library…",
        "Firma digitale": "Digital signature", "Firma invisibile": "Invisible signature",
        "Certifica": "Certify", "Campo firma": "Signature field", "Verifica firme": "Verify signatures",
        "ID digitali…": "Digital IDs…",
        "Gestisci firme e identità digitali…": "Manage signatures and digital identities…", "Crea ID autofirmato…": "Create self-signed ID…", "Firme": "Signatures",
        "Libreria vuota": "Empty library", "Modifica firma": "Edit signature",
        "Crea una firma disegnata, digitata o da immagine": "Create a drawn, typed or image signature",
        "Firma il documento con un certificato": "Sign the document with a certificate",
        "Firma con certificato e stabilisci le modifiche consentite":
            "Sign with a certificate and set the allowed changes",
        "Aggiungi un campo firma vuoto da far firmare": "Add an empty signature field to be signed",
        "Seleziona, sposta o ridimensiona le firme inserite": "Select, move or resize placed signatures",
        "Firma un PDF": "Sign a PDF", "Firma grafica o digitale": "Graphic or digital signature",
        "Libreria bloccata": "Library locked", "Incorpora le firme": "Embed signatures",
        "Incorpora le firme grafiche nel contenuto delle pagine": "Embed the graphic signatures into the page content",
    })
    return merged


def register(ctx: Context) -> None:
    add_translations("en", _translations())
    from .controller import SignController
    from .panel import SignaturesPanel

    c = SignController(ctx)
    ctx.sign_controller = c   # accessibile ai test
    mode = ctx.add_mode("firma", _("Firma"), "signature", 90)

    act_create = ctx.action(_("Crea firma"), "signature", lambda: c.create_signature("firma"),
                            tooltip=_("Crea una firma disegnata, digitata o da immagine"))
    act_initials = ctx.action(_("Crea iniziali"), "type", lambda: c.create_signature("iniziali"))
    mode.add_action(act_create)
    mode.add_action(act_initials)

    lib_button = QToolButton()
    lib_button.setText(_("Firme salvate"))
    lib_button.setToolButtonStyle(mode.toolbar.toolButtonStyle())
    bind_icon(lib_button, "square-library")
    lib_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    menu = QMenu(lib_button)
    lib_button.setMenu(menu)

    def fill_menu() -> None:
        menu.clear()
        lib = c.library(interactive=False)
        if lib is not None and lib.locked:
            menu.addAction(_("Libreria bloccata"), lambda: c.library(interactive=True))
        elif lib is not None:
            from PySide6.QtCore import QSize

            from .ui_create import item_pixmap
            any_item = False
            for kind, title in (("firma", _("Firme")), ("iniziali", _("Iniziali"))):
                entries = [e for e in lib.entries(kind) if e.available]
                if not entries:
                    continue
                menu.addSection(title)
                for e in entries:
                    a = menu.addAction(e.name, lambda eid=e.id: c.use_library_item(eid))
                    try:
                        from PySide6.QtGui import QIcon
                        a.setIcon(QIcon(item_pixmap(lib.load_item(e.id), QSize(96, 32))))
                    except Exception:
                        pass
                    any_item = True
            if not any_item:
                a = menu.addAction(_("Libreria vuota"))
                a.setEnabled(False)
        menu.addSeparator()
        menu.addAction(_("Gestisci libreria…"), c.manage_library)

    menu.aboutToShow.connect(fill_menu)
    mode.add_widget(lib_button)

    act_edit = ctx.action(_("Modifica firma"), "move", lambda: _edit_tool(ctx, c),
                          tooltip=_("Seleziona, sposta o ridimensiona le firme inserite"))
    mode.add_action(act_edit)
    act_embed = ctx.action(_("Incorpora le firme"), "stamp", c.embed_all,
                           tooltip=_("Incorpora le firme grafiche nel contenuto delle pagine"))
    ctx.add_menu_action(act_embed, _("Firma"))
    mode.add_separator()
    act_digital = ctx.action(_("Firma digitale"), "file-badge", lambda: c.start_digital_sign(False),
                             tooltip=_("Firma il documento con un certificato"))
    act_invisible = ctx.action(_("Firma invisibile"), "eye-off", lambda: _invisible(ctx, c))
    act_certify = ctx.action(_("Certifica"), "award", lambda: c.start_digital_sign(True),
                             tooltip=_("Firma con certificato e stabilisci le modifiche consentite"))
    act_field = ctx.action(_("Campo firma"), "text-cursor-input", c.start_add_field,
                           tooltip=_("Aggiungi un campo firma vuoto da far firmare"))
    for a in (act_digital, act_invisible, act_certify, act_field):
        mode.add_action(a)
    mode.add_separator()
    act_verify = ctx.action(_("Verifica firme"), "shield-check", lambda: _verify(ctx, c))
    act_ids = ctx.action(_("Gestisci firme e identità digitali…"), "key-round", c.manage_ids,
                         requires_document=False)
    act_self = ctx.action(_("Crea ID autofirmato…"), "badge-plus", c.create_self_signed, requires_document=False)
    for a in (act_verify, act_ids):
        mode.add_action(a)
    ctx.add_menu_action(act_ids, _("Firma"))
    ctx.add_menu_action(act_self, _("Firma"))

    panel = SignaturesPanel(c)
    ctx.add_sidebar_panel("firme", _("Firme"), "file-badge", panel, order=90)
    ctx.add_view_menu_provider(c.menu_provider)
    ctx.add_home_action(_("Firma un PDF"), "signature", lambda: _home(ctx), _("Firma grafica o digitale"), 90)

    def on_state(view) -> None:
        st = c.state(view)
        if view is ctx.current_view() and (st.statuses or c.empty_fields(view)) and not st.running:
            ctx.show_sidebar_panel("firme")

    c.stateChanged.connect(on_state)


def _edit_tool(ctx, c) -> None:
    view = ctx.current_view()
    if view is not None:
        tool = c.tool_for(view)
        tool.set_pending(None)
        view.set_tool(tool)


def _invisible(ctx, c) -> None:
    view = ctx.current_view()
    if view is not None:
        c.digital_sign(view, invisible=True)


def _verify(ctx, c) -> None:
    view = ctx.current_view()
    if view is not None:
        c.verify(view, force=True)
        ctx.show_sidebar_panel("firme")


def _home(ctx) -> None:
    paths = ctx.ask_open_paths(_("Firma un PDF"), multiple=False)
    if paths and ctx.open_path(paths[0]) is not None:
        ctx.set_mode("firma")
