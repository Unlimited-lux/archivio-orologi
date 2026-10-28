"""Opzioni di apertura del documento (VIS-42): come il documento si presenterà in qualunque
lettore — pagina e zoom iniziali, layout, pannello, schermo intero, finestra, barre nascoste,
verso di lettura e preferenze di stampa. Le scelte si scrivono nel catalogo del PDF
(`/OpenAction`, `/PageLayout`, `/PageMode`, `/ViewerPreferences`) con un passo annullabile."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel, QSpinBox, QVBoxLayout,
)

from foliant.api import (
    ENGINE_LOCK, FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE, Context,
    DocumentView, _, add_translations,
)

from . import session_engine as engine
from .engine import page_labels

TRANSLATIONS = {
    "Opzioni di apertura…": "Opening options…",
    "Opzioni di apertura": "Opening options",
    "Modifica opzioni di apertura…": "Edit opening options…",
    "Come il documento si presenta in qualunque lettore": "How the document opens in any reader",
    "Vista iniziale": "Initial view",
    "Pagina": "Page",
    "Zoom": "Zoom",
    "Layout": "Layout",
    "Pannello": "Panel",
    "Predefinito del lettore": "Reader default",
    "Adatta alla larghezza": "Fit width",
    "Adatta alla pagina": "Fit page",
    "Scorrimento continuo": "Continuous scrolling",
    "Pagina singola": "Single page",
    "Due pagine": "Two pages",
    "Due pagine con copertina": "Two pages with cover",
    "Nessuno": "None",
    "Segnalibri": "Bookmarks",
    "Miniature": "Thumbnails",
    "Allegati": "Attachments",
    "Livelli": "Layers",
    "Apri a schermo intero": "Open in full screen",
    "Finestra": "Window",
    "Adatta la finestra alla prima pagina": "Fit the window to the first page",
    "Centra la finestra sullo schermo": "Center the window on the screen",
    "Mostra il titolo del documento invece del nome del file": "Show the document title instead of the file name",
    "Nascondi la barra degli strumenti": "Hide the toolbar",
    "Nascondi la barra dei menu": "Hide the menu bar",
    "Nascondi i controlli della finestra": "Hide the window controls",
    "Lettura da destra a sinistra": "Right-to-left reading",
    "Stampa": "Print",
    "Stampa a dimensione reale (nessuna scala)": "Print at actual size (no scaling)",
    "Fronte/retro": "Two-sided",
    "Come la stampante": "Printer default",
    "Solo fronte": "One-sided",
    "Lato corto": "Short edge",
    "Lato lungo": "Long edge",
    "Copie": "Copies",
    "Scegli il vassoio secondo il formato della pagina": "Choose the tray by page size",
    "Applica a tutti i documenti aperti": "Apply to all open documents",
    "Opzioni di apertura non consentite: il documento non consente modifiche.":
        "Opening options not allowed: the document does not allow changes.",
    "Il documento all'apertura esegue un'azione (per esempio uno script, mai eseguito da Foliant) "
    "che verrà sostituita dalla vista iniziale. Continuare?":
        "When opened, the document runs an action (for example a script, never run by Foliant) "
        "that will be replaced by the initial view. Continue?",
    "adattata in larghezza": "fit to width",
    "adattata alla finestra": "fit to the window",
    "zoom {z}%": "zoom {z}%",
    "con lo zoom del lettore": "with the reader's zoom",
    "Si apre a pagina {p}, {zoom}": "Opens at page {p}, {zoom}",
    "con i segnalibri visibili": "with bookmarks visible",
    "con le miniature visibili": "with thumbnails visible",
    "con gli allegati visibili": "with attachments visible",
    "con i livelli visibili": "with layers visible",
    "a schermo intero": "in full screen",
    "mostrando il titolo del documento": "showing the document title",
}

ZOOMS = (("", "Predefinito del lettore"), (FIT_WIDTH, "Adatta alla larghezza"), (FIT_PAGE, "Adatta alla pagina"),
         ("0.5", "50%"), ("0.75", "75%"), ("1", "100%"), ("1.25", "125%"), ("1.5", "150%"), ("2", "200%"),
         ("4", "400%"))
LAYOUTS = (("", "Predefinito del lettore"), (VIEW_SINGLE, "Pagina singola"), (VIEW_CONTINUOUS, "Scorrimento continuo"),
           (VIEW_FACING, "Due pagine"), (VIEW_COVER, "Due pagine con copertina"))
PANELS = (("", "Nessuno"), ("bookmarks", "Segnalibri"), ("thumbnails", "Miniature"),
          ("attachments", "Allegati"), ("layers", "Livelli"))
DUPLEX_LABELS = (("", "Come la stampante"), ("Simplex", "Solo fronte"), ("DuplexFlipShortEdge", "Lato corto"),
                 ("DuplexFlipLongEdge", "Lato lungo"))


class OpenOptionsDialog(QDialog):
    def __init__(self, parent, options: engine.OpenOptions, labels: list[str]):
        super().__init__(parent)
        self.setObjectName("openOptionsDialog")
        self.setWindowTitle(_("Opzioni di apertura"))
        self.labels = labels
        root = QVBoxLayout(self)
        intro = QLabel(_("Come il documento si presenta in qualunque lettore"), self)
        intro.setProperty("muted", True)
        root.addWidget(intro)

        view_box = QGroupBox(_("Vista iniziale"), self)
        form = QFormLayout(view_box)
        self.page = QSpinBox(view_box)
        self.page.setRange(1, max(1, len(labels)))
        form.addRow(_("Pagina"), self.page)
        self.zoom = self._combo(view_box, ZOOMS)
        form.addRow(_("Zoom"), self.zoom)
        self.layout_box = self._combo(view_box, LAYOUTS)
        form.addRow(_("Layout"), self.layout_box)
        self.panel = self._combo(view_box, PANELS)
        form.addRow(_("Pannello"), self.panel)
        self.fullscreen = QCheckBox(_("Apri a schermo intero"), view_box)
        form.addRow(self.fullscreen)
        root.addWidget(view_box)

        win_box = QGroupBox(_("Finestra"), self)
        wl = QVBoxLayout(win_box)
        self.fit_window = QCheckBox(_("Adatta la finestra alla prima pagina"), win_box)
        self.center_window = QCheckBox(_("Centra la finestra sullo schermo"), win_box)
        self.display_title = QCheckBox(_("Mostra il titolo del documento invece del nome del file"), win_box)
        self.hide_toolbar = QCheckBox(_("Nascondi la barra degli strumenti"), win_box)
        self.hide_menubar = QCheckBox(_("Nascondi la barra dei menu"), win_box)
        self.hide_window_ui = QCheckBox(_("Nascondi i controlli della finestra"), win_box)
        self.direction_rtl = QCheckBox(_("Lettura da destra a sinistra"), win_box)
        for w in (self.fit_window, self.center_window, self.display_title, self.hide_toolbar, self.hide_menubar,
                  self.hide_window_ui, self.direction_rtl):
            wl.addWidget(w)
        root.addWidget(win_box)

        print_box = QGroupBox(_("Stampa"), self)
        pf = QFormLayout(print_box)
        self.print_scaling_none = QCheckBox(_("Stampa a dimensione reale (nessuna scala)"), print_box)
        pf.addRow(self.print_scaling_none)
        self.duplex = self._combo(print_box, DUPLEX_LABELS)
        pf.addRow(_("Fronte/retro"), self.duplex)
        self.copies = QSpinBox(print_box)
        self.copies.setRange(1, 999)
        pf.addRow(_("Copie"), self.copies)
        self.pick_tray = QCheckBox(_("Scegli il vassoio secondo il formato della pagina"), print_box)
        pf.addRow(self.pick_tray)
        root.addWidget(print_box)

        self.summary = QLabel(self)
        self.summary.setWordWrap(True)
        self.summary.setObjectName("openOptionsSummary")
        root.addWidget(self.summary)
        self.apply_all = QCheckBox(_("Applica a tutti i documenti aperti"), self)
        root.addWidget(self.apply_all)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.set_options(options)
        for w in (self.page,):
            w.valueChanged.connect(self._update_summary)
        for w in (self.zoom, self.layout_box, self.panel, self.duplex):
            w.currentIndexChanged.connect(self._update_summary)
        for w in (self.fullscreen, self.display_title):
            w.toggled.connect(self._update_summary)
        self._update_summary()

    @staticmethod
    def _combo(parent, items) -> QComboBox:
        box = QComboBox(parent)
        for data, label in items:
            box.addItem(_(label), data)
        return box

    @staticmethod
    def _select(box: QComboBox, data) -> None:
        box.setCurrentIndex(max(0, box.findData(data)))

    def set_options(self, o: engine.OpenOptions) -> None:
        self.page.setValue(o.page + 1)
        if o.fit:
            self._select(self.zoom, o.fit)
        elif o.zoom:
            self._select(self.zoom, f"{o.zoom:g}")
        else:
            self._select(self.zoom, "")
        self._select(self.layout_box, o.layout or "")
        self._select(self.panel, o.panel or "")
        self.fullscreen.setChecked(o.fullscreen)
        for name in ("fit_window", "center_window", "display_title", "hide_toolbar", "hide_menubar",
                     "hide_window_ui", "direction_rtl", "print_scaling_none", "pick_tray"):
            getattr(self, name).setChecked(getattr(o, name))
        self._select(self.duplex, o.duplex)
        self.copies.setValue(o.num_copies)

    def options(self) -> engine.OpenOptions:
        zoom = self.zoom.currentData() or ""
        o = engine.OpenOptions(page=self.page.value() - 1,
                               fit=zoom if zoom in (FIT_WIDTH, FIT_PAGE) else None,
                               zoom=float(zoom) if zoom and zoom not in (FIT_WIDTH, FIT_PAGE) else None,
                               layout=self.layout_box.currentData() or None,
                               panel=self.panel.currentData() or "",
                               fullscreen=self.fullscreen.isChecked(),
                               duplex=self.duplex.currentData() or "", num_copies=self.copies.value())
        for name in ("fit_window", "center_window", "display_title", "hide_toolbar", "hide_menubar",
                     "hide_window_ui", "direction_rtl", "print_scaling_none", "pick_tray"):
            setattr(o, name, getattr(self, name).isChecked())
        return o

    def _update_summary(self, *_a) -> None:
        o = self.options()
        label = self.labels[o.page] if 0 <= o.page < len(self.labels) and self.labels[o.page] else str(o.page + 1)
        self.summary.setText(engine.describe_open_options(o, label, _))


class OpenOptionsController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.dialog: OpenOptionsDialog | None = None
        self.act = ctx.action(_("Opzioni di apertura…"), "settings-2", self.open_dialog)
        ctx.add_menu_action(self.act, _("File"))

    def read(self, view: DocumentView) -> engine.OpenOptions:
        with ENGINE_LOCK:
            return engine.read_open_options(view.document.pdf)

    def dialog_for(self, view: DocumentView) -> OpenOptionsDialog:
        with ENGINE_LOCK:
            labels = page_labels(view.document.pdf)
        labels = [lab or str(i + 1) for i, lab in enumerate(labels)] or [str(i + 1) for i in range(view.page_count)]
        return OpenOptionsDialog(self.ctx.window, self.read(view), labels)

    def open_dialog(self, view: DocumentView | None = None) -> bool:
        view = view or self.ctx.current_view()
        if view is None:
            return False
        dlg = self.dialog_for(view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        views = self.ctx.views() if dlg.apply_all.isChecked() else [view]
        return all([self.apply(v, dlg.options()) for v in views])

    def apply(self, view: DocumentView, options: engine.OpenOptions) -> bool:
        """Scrive le opzioni nel documento (un passo annullabile)."""
        import pymupdf
        doc = view.document
        if not doc.allows(pymupdf.PDF_PERM_MODIFY):
            self.ctx.error(_("Opzioni di apertura"), _("Opzioni di apertura non consentite: il documento non consente "
                                                       "modifiche."))
            return False
        with ENGINE_LOCK:
            script = engine.open_action_is_script(doc.pdf)
            options.page = min(options.page, doc.pdf.page_count - 1)
        if script and not self.ctx.confirm(_("Opzioni di apertura"), _(
                "Il documento all'apertura esegue un'azione (per esempio uno script, mai eseguito da Foliant) "
                "che verrà sostituita dalla vista iniziale. Continuare?")):
            return False
        try:
            with doc.change(_("Opzioni di apertura")) as pdf:
                engine.write_open_options(pdf, options)
        except Exception as e:  # noqa: BLE001
            self.ctx.error(_("Opzioni di apertura"), str(e))
            return False
        return True


def install(ctx: Context) -> OpenOptionsController:
    add_translations("en", TRANSLATIONS)
    return OpenOptionsController(ctx)
