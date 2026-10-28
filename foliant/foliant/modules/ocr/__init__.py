"""Modulo OCR: riconoscimento del testo dei PDF scannerizzati (sezione 7 della specifica).

Rende i documenti scannerizzati ricercabili (testo invisibile) o modificabili
(testo vero su sfondo ripulito), oppure ne estrae il testo. Il motore è in
`engine.py` (senza interfaccia, riusabile dagli altri moduli).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from foliant.api import Context

ORDER = 60

_live: list = []    # controller delle finestre aperte

controller = None   # controller dell'ultima finestra registrata (comodità per i test); ogni finestra usa `ctx.ocr`


def register(ctx: "Context") -> None:
    # importazioni qui: il pacchetto si importa anche senza interfaccia (processo di resa, motore)
    from foliant.api import _, add_translations
    from .translations import EN
    add_translations("en", EN)
    from . import engine
    from .ui import OcrController, ReviewPanel
    engine.cleanup_leftovers()      # copie temporanee dei documenti lasciate da un arresto anomalo

    global controller
    ctrl = OcrController(ctx)
    # ogni finestra (Ctrl+N) ha il proprio controller: le closure qui sotto usano `ctrl`, mai il globale
    controller = ctrl
    _live.append(ctrl)

    def forget(*_a, c=ctrl):
        global controller
        if c in _live:
            _live.remove(c)
        if controller is c:
            controller = _live[-1] if _live else None
    ctx.destroyed.connect(forget)
    ctx.ocr = ctrl     # accesso facoltativo per altri moduli (es. proposta dalla modalità Modifica)
    mode = ctx.add_mode("ocr", _("OCR"), "scan-text", 60)

    act_run = ctx.action(_("Esegui OCR"), "scan-text", lambda: ctrl.open_dialog(),
                         tooltip=_("Riconosci il testo delle pagine scannerizzate (PDF ricercabile)"))
    act_edit = ctx.action(_("Rendi modificabile"), "file-pen-line",
                          lambda: ctrl.open_dialog(mode=engine.EDITABLE),
                          tooltip=_("Converti le pagine scannerizzate in testo vero e modificabile"))
    act_text = ctx.action(_("Estrai testo"), "file-text", ctrl.extract_text,
                          tooltip=_("Riconosci e copia o salva il testo come file TXT"))
    act_area = ctx.action(_("OCR di un'area"), "scan", None, checkable=True,
                          tooltip=_("Trascina un rettangolo per riconoscerne il testo"))
    act_area.toggled.connect(ctrl.toggle_area_tool)
    act_review = ctx.action(_("Parole incerte"), "spell-check", None, checkable=True,
                            tooltip=_("Evidenzia e correggi le parole riconosciute con bassa affidabilità"))
    act_review.toggled.connect(ctrl.toggle_highlight)
    act_batch = ctx.action(_("OCR in blocco"), "files", ctrl.batch, requires_document=False,
                           tooltip=_("Riconosci il testo di più file PDF"))
    act_langs = ctx.action(_("Lingue"), "languages", ctrl.manage_languages, requires_document=False,
                           tooltip=_("Lingue di riconoscimento installate e disponibili"))
    for a in (act_run, act_edit, act_text):
        mode.add_action(a)
    mode.add_separator()
    mode.add_action(act_area)
    mode.add_action(act_review)
    mode.add_separator()
    mode.add_action(act_batch)
    mode.add_action(act_langs)
    ctrl.actions = {"run": act_run, "edit": act_edit, "text": act_text, "area": act_area,
                          "review": act_review, "batch": act_batch, "languages": act_langs}

    # lo strumento area si spegne quando un altro strumento prende il suo posto
    def tool_changed(*_a):
        view = ctx.current_view()
        on = view is not None and view.tool is ctrl.area_tool
        if act_area.isChecked() != on:
            act_area.blockSignals(True)
            act_area.setChecked(on)
            act_area.blockSignals(False)
    ctx.currentViewChanged.connect(tool_changed)
    ctx.currentViewChanged.connect(lambda v: ctrl.review_panel.set_view(v) if ctrl.review_panel else None)
    ctx.documentOpened.connect(lambda v: v.toolChanged.connect(tool_changed))
    mode.left.connect(lambda: act_area.setChecked(False))

    act_detect = ctx.action(_("Proponi l'OCR per i documenti scannerizzati"), "scan-search", None,
                            checkable=True, requires_document=False)
    act_detect.setChecked(ctrl.detection_enabled())
    act_detect.toggled.connect(ctrl.set_detection_enabled)
    ctrl.actions["detect"] = act_detect
    menu_run = ctx.action(_("Esegui OCR…"), "scan-text", lambda: ctrl.open_dialog())
    act_remove = ctx.action(_("Rimuovi livello OCR"), "eraser", ctrl.remove_layer,
                            tooltip=_("Togli il testo invisibile riconosciuto, lasciando le immagini"))
    ctrl.actions["remove"] = act_remove
    act_image = ctx.action(_("Converti in PDF solo immagine"), "image", ctrl.to_image_only,
                           tooltip=_("Rende ogni pagina un'immagine, senza testo estraibile"))
    ctrl.actions["image_only"] = act_image
    act_resume = ctx.action(_("Riprendi lotto"), "rotate-ccw", ctrl.resume_batch, requires_document=False,
                            tooltip=_("Elabora i file rimasti di un OCR in blocco interrotto in questa sessione"))
    act_resume.setEnabled(ctrl.pending_batch() is not None)   # lotto solo in memoria: all'avvio è vuoto
    ctrl.actions["resume"] = act_resume
    for a in (menu_run, act_remove, act_image, act_batch, act_resume, act_langs, act_detect):
        ctx.add_menu_action(a, _("OCR"))

    ctx.add_view_menu_provider(ctrl.menu_provider)
    ctrl.review_panel = ReviewPanel(ctrl)
    ctx.add_properties_panel("ocr-review", ctrl.review_panel)
    ctx.add_home_action(_("OCR di un PDF scannerizzato"), "scan-text", _home_ocr(ctx, ctrl),
                        _("Rendi ricercabile o modificabile un documento scannerizzato"), order=60)


def _home_ocr(ctx, controller):
    from foliant.api import _

    def run():
        paths = ctx.ask_open_paths(_("PDF scannerizzato"), multiple=False)
        if not paths:
            return
        view = ctx.open_path(paths[0])
        if view is not None:
            controller.open_dialog(view)
    return run
