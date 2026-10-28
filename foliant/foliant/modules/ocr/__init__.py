"""Modulo OCR: riconoscimento del testo dei PDF scannerizzati (sezione 7 della specifica).

Rende i documenti scannerizzati ricercabili (testo invisibile) o modificabili
(testo vero su sfondo ripulito), oppure ne estrae il testo. Il motore è in
`engine.py` (senza interfaccia, riusabile dagli altri moduli).
"""

from __future__ import annotations

from foliant.api import Context, _, add_translations

ORDER = 60

controller = None


def register(ctx: Context) -> None:
    global controller
    from .translations import EN
    add_translations("en", EN)
    from . import engine
    from .ui import OcrController, ReviewPanel

    controller = OcrController(ctx)
    ctx.ocr = controller   # accesso facoltativo per altri moduli (es. proposta dalla modalità Modifica)
    mode = ctx.add_mode("ocr", _("OCR"), "scan-text", 60)

    act_run = ctx.action(_("Riconosci testo"), "scan-text", lambda: controller.open_dialog(),
                         tooltip=_("Riconosci il testo delle pagine scannerizzate (PDF ricercabile)"))
    act_edit = ctx.action(_("Rendi modificabile"), "file-pen-line",
                          lambda: controller.open_dialog(mode=engine.EDITABLE),
                          tooltip=_("Converti le pagine scannerizzate in testo vero e modificabile"))
    act_text = ctx.action(_("Estrai testo"), "file-text", controller.extract_text,
                          tooltip=_("Riconosci e copia o salva il testo come file TXT"))
    act_area = ctx.action(_("OCR di un'area"), "scan", None, checkable=True,
                          tooltip=_("Trascina un rettangolo per riconoscerne il testo"))
    act_area.toggled.connect(controller.toggle_area_tool)
    act_review = ctx.action(_("Parole incerte"), "spell-check", None, checkable=True,
                            tooltip=_("Evidenzia e correggi le parole riconosciute con bassa affidabilità"))
    act_review.toggled.connect(controller.toggle_highlight)
    act_batch = ctx.action(_("OCR in blocco"), "files", controller.batch, requires_document=False,
                           tooltip=_("Riconosci il testo di più file PDF"))
    act_langs = ctx.action(_("Lingue"), "languages", controller.manage_languages, requires_document=False,
                           tooltip=_("Lingue di riconoscimento installate e disponibili"))
    for a in (act_run, act_edit, act_text):
        mode.add_action(a)
    mode.add_separator()
    mode.add_action(act_area)
    mode.add_action(act_review)
    mode.add_separator()
    mode.add_action(act_batch)
    mode.add_action(act_langs)
    controller.actions = {"run": act_run, "edit": act_edit, "text": act_text, "area": act_area,
                          "review": act_review, "batch": act_batch, "languages": act_langs}

    # lo strumento area si spegne quando un altro strumento prende il suo posto
    def tool_changed(*_a):
        view = ctx.current_view()
        on = view is not None and view.tool is controller.area_tool
        if act_area.isChecked() != on:
            act_area.blockSignals(True)
            act_area.setChecked(on)
            act_area.blockSignals(False)
    ctx.currentViewChanged.connect(tool_changed)
    ctx.currentViewChanged.connect(lambda v: controller.review_panel.set_view(v) if controller.review_panel else None)
    ctx.documentOpened.connect(lambda v: v.toolChanged.connect(tool_changed))
    mode.left.connect(lambda: act_area.setChecked(False))

    act_detect = ctx.action(_("Proponi l'OCR per i documenti scannerizzati"), "scan-search", None,
                            checkable=True, requires_document=False)
    act_detect.setChecked(controller.detection_enabled())
    act_detect.toggled.connect(controller.set_detection_enabled)
    controller.actions["detect"] = act_detect
    menu_run = ctx.action(_("Riconosci testo (OCR)…"), "scan-text", lambda: controller.open_dialog())
    act_remove = ctx.action(_("Rimuovi livello OCR"), "eraser", controller.remove_layer,
                            tooltip=_("Togli il testo invisibile riconosciuto, lasciando le immagini"))
    controller.actions["remove"] = act_remove
    for a in (menu_run, act_remove, act_batch, act_langs, act_detect):
        ctx.add_menu_action(a, _("OCR"))

    controller.review_panel = ReviewPanel(controller)
    ctx.add_properties_panel("ocr-review", controller.review_panel)
    ctx.add_home_action(_("OCR di un PDF scannerizzato"), "scan-text", _home_ocr(ctx, controller),
                        _("Rendi ricercabile o modificabile un documento scannerizzato"), order=60)


def _home_ocr(ctx, controller):
    def run():
        paths = ctx.ask_open_paths(_("PDF scannerizzato"), multiple=False)
        if not paths:
            return
        view = ctx.open_path(paths[0])
        if view is not None:
            controller.open_dialog(view)
    return run
