"""Modulo Firma: firma grafica (disegnata, digitata, da immagine) con libreria
locale, firma digitale PAdES con pyHanko, verifica e pannello delle firme."""

from __future__ import annotations

from foliant.api import Context, _, add_translations

ORDER = 90


def _translations() -> dict:
    from . import controller, digital, graphic, library, panel, store, sync, tools, ui_create, ui_digital, ui_library
    merged: dict = {}
    for mod in (graphic, library, sync, digital, store, tools, ui_create, ui_library, ui_digital, panel, controller):
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
        "Identità": "Identity", "ID digitali": "Digital IDs", "Incorpora": "Embed", "Nuovo ID": "New ID",
    })
    return merged


def register(ctx: Context) -> None:
    add_translations("en", _translations())
    from .controller import SignController
    from .panel import SignaturesPanel

    c = SignController(ctx)
    ctx.sign_controller = c   # accessibile ai test
    mode = ctx.add_mode("firma", _("Firma"), "signature", 90)

    # --- Firme: firma grafica
    mode.add_group(_("Firme"), "firme")
    act_create = ctx.action(_("Crea firma"), "signature", lambda: c.create_signature("firma"),
                            tooltip=_("Crea una firma disegnata, digitata o da immagine"))
    act_initials = ctx.action(_("Crea iniziali"), "type", lambda: c.create_signature("iniziali"))
    mode.add_action(act_create)
    mode.add_action(act_initials)

    lib_button = mode.add_menu_button(_("Firme salvate"), "square-library", [], requires_document=False)
    menu = lib_button.menu()

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
    c.library_button = lib_button

    act_edit = ctx.action(_("Modifica firma"), "move", lambda: _edit_tool(ctx, c),
                          tooltip=_("Seleziona, sposta o ridimensiona le firme inserite"))
    act_embed = ctx.action(_("Incorpora le firme"), "stamp", c.embed_all,
                           tooltip=_("Incorpora le firme grafiche nel contenuto delle pagine"))
    act_embed.setIconText(_("Incorpora"))
    mode.add_action(act_edit, compact=True)
    mode.add_action(act_embed)

    # --- Firma digitale: con certificato
    mode.add_group(_("Firma digitale"), "digitale")
    act_digital = ctx.action(_("Firma digitale"), "file-badge", lambda: c.start_digital_sign(False),
                             tooltip=_("Firma il documento con un certificato"))
    act_invisible = ctx.action(_("Firma invisibile"), "eye-off", lambda: _invisible(ctx, c))
    act_certify = ctx.action(_("Certifica"), "award", lambda: c.start_digital_sign(True),
                             tooltip=_("Firma con certificato e stabilisci le modifiche consentite"))
    act_field = ctx.action(_("Campo firma"), "text-cursor-input", c.start_add_field,
                           tooltip=_("Aggiungi un campo firma vuoto da far firmare"))
    mode.add_action(act_digital)
    mode.add_action(act_invisible, compact=True)
    mode.add_action(act_certify)
    mode.add_action(act_field)

    # --- Identità: verifica e identità digitali
    mode.add_group(_("Identità"), "identita")
    act_verify = ctx.action(_("Verifica firme"), "shield-check", lambda: _verify(ctx, c))
    act_ids = ctx.action(_("Gestisci firme e identità digitali…"), "key-round", c.manage_ids,
                         requires_document=False)
    act_ids.setIconText(_("ID digitali"))
    act_self = ctx.action(_("Crea ID autofirmato…"), "badge-plus", c.create_self_signed, requires_document=False)
    mode.add_action(act_verify)
    mode.add_action(act_ids)
    act_self.setIconText(_("Nuovo ID"))
    mode.add_action(act_self)
    c.actions = {"create": act_create, "initials": act_initials, "edit": act_edit, "embed": act_embed,
                 "digital": act_digital, "invisible": act_invisible, "certify": act_certify, "field": act_field,
                 "verify": act_verify, "ids": act_ids, "self_signed": act_self}

    panel = SignaturesPanel(c)
    c.panel = panel
    ctx.add_sidebar_panel("firme", _("Firme"), "file-badge", panel, order=90)
    ctx.add_view_menu_provider(c.menu_provider)
    # per il modulo Moduli: clic su un campo firma vuoto → procedura di firma con quel campo
    ctx.request_signature = c.request_signature
    if hasattr(ctx, "add_preferences_page"):
        from .ui_digital import SignPreferences
        ctx.add_preferences_page(_("Firma"), lambda parent: SignPreferences(parent, c), 90)
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
