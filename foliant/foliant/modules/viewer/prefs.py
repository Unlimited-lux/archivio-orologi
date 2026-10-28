"""Preferenze di visualizzazione (VIS-51, parte): layout e zoom predefiniti,
pannello iniziale, rispetto delle impostazioni del documento, ripristino
dell'ultima posizione, unità di misura; esportazione e importazione delle
impostazioni; ripristino dei valori predefiniti."""

from __future__ import annotations

import configparser
import io

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QPushButton, QVBoxLayout,
)

from foliant.api import (
    FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE, Context, DocumentView, _,
    add_translations, atomic_write,
)

TRANSLATIONS = {
    "Preferenze di visualizzazione…": "View preferences…",
    "Preferenze di visualizzazione": "View preferences",
    "Layout predefinito": "Default layout",
    "Zoom predefinito": "Default zoom",
    "Pannello all'apertura": "Panel on opening",
    "Nessuno": "None",
    "Miniature": "Thumbnails",
    "Segnalibri": "Bookmarks",
    "Rispetta le impostazioni del documento": "Respect the document's settings",
    "Riapri i documenti all'ultima posizione": "Reopen documents at the last position",
    "Non registrare la cronologia dei documenti": "Do not record document history",
    "Esporta impostazioni…": "Export settings…",
    "Importa impostazioni…": "Import settings…",
    "Ripristina predefiniti": "Restore defaults",
    "Ripristinare tutte le impostazioni ai valori predefiniti?": "Restore all settings to their defaults?",
    "Impostazioni (*.ini)": "Settings (*.ini)",
    "Impostazioni importate: {n} valori. Alcune si applicano al prossimo avvio.":
        "Settings imported: {n} values. Some apply at the next start.",
    "File di impostazioni non valido.": "Invalid settings file.",
    "Scorrimento continuo": "Continuous scrolling",
    "Pagina singola": "Single page",
    "Due pagine": "Two pages",
    "Due pagine con copertina": "Two pages with cover",
    "Adatta larghezza": "Fit width",
    "Adatta pagina": "Fit page",
    "Strumenti": "Tools",
}

LAYOUTS = ((VIEW_CONTINUOUS, "Scorrimento continuo"), (VIEW_SINGLE, "Pagina singola"),
           (VIEW_FACING, "Due pagine"), (VIEW_COVER, "Due pagine con copertina"))
ZOOMS = ((FIT_WIDTH, "Adatta larghezza"), (FIT_PAGE, "Adatta pagina"), ("0.75", "75%"), ("1.0", "100%"),
         ("1.25", "125%"), ("1.5", "150%"), ("2.0", "200%"))
PANELS = (("", "Nessuno"), ("thumbnails", "Miniature"), ("bookmarks", "Segnalibri"))
EXPORT_VERSION = 1
# chiavi che non si esportano: dati personali o legati alla macchina
PRIVATE_PREFIXES = ("recent", "lastDirectory", "window/", "viewer/positions", "viewer/session", "viewer/search_history",
                    "viewer/closed")


def _bool(v, default=False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


class PrefsController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.dialog: QDialog | None = None
        self.act = ctx.action(_("Preferenze di visualizzazione…"), "settings-2", self.open_dialog,
                              requires_document=False)
        self.act_export = ctx.action(_("Esporta impostazioni…"), "download", self.export_dialog,
                                     requires_document=False)
        self.act_import = ctx.action(_("Importa impostazioni…"), "upload", self.import_dialog,
                                     requires_document=False)
        for a in (self.act, self.act_export, self.act_import):
            ctx.add_menu_action(a, _("Strumenti"))

    # ---------------------------------------------------------------- valori
    def value(self, key: str, default=None):
        return self.ctx.settings().value(key, default)

    def apply_defaults(self, view: DocumentView) -> None:
        layout = self.value("viewer/default_layout", VIEW_CONTINUOUS)
        if layout in dict(LAYOUTS) and layout != view.view_mode:
            view.set_view_mode(layout)
        zoom = str(self.value("viewer/default_zoom", FIT_WIDTH) or FIT_WIDTH)
        if zoom in (FIT_WIDTH, FIT_PAGE):
            if view.fit_mode != zoom:
                view.set_fit(zoom)
        else:
            try:
                view.set_zoom(float(zoom))
            except ValueError:
                pass
        panel = self.value("viewer/default_panel", "")
        if panel:
            self.ctx.show_sidebar_panel(panel)

    # ---------------------------------------------------------------- dialogo
    def open_dialog(self) -> QDialog:
        if self.dialog is None:
            self.dialog = self._build()
        self._load()
        self.dialog.show()
        self.dialog.raise_()
        return self.dialog

    def _build(self) -> QDialog:
        d = QDialog(self.ctx.window)
        d.setWindowTitle(_("Preferenze di visualizzazione"))
        layout = QVBoxLayout(d)
        form = QFormLayout()
        self.layout_box = QComboBox()
        for key, label in LAYOUTS:
            self.layout_box.addItem(_(label), key)
        self.zoom_box = QComboBox()
        for key, label in ZOOMS:
            self.zoom_box.addItem(_(label), key)
        self.panel_box = QComboBox()
        for key, label in PANELS:
            self.panel_box.addItem(_(label), key)
        form.addRow(_("Layout predefinito"), self.layout_box)
        form.addRow(_("Zoom predefinito"), self.zoom_box)
        form.addRow(_("Pannello all'apertura"), self.panel_box)
        layout.addLayout(form)
        self.respect = QCheckBox(_("Rispetta le impostazioni del documento"))
        self.restore = QCheckBox(_("Riapri i documenti all'ultima posizione"))
        self.no_history = QCheckBox(_("Non registrare la cronologia dei documenti"))
        for w in (self.respect, self.restore, self.no_history):
            layout.addWidget(w)
        row = QHBoxLayout()
        for text, slot in ((_("Esporta impostazioni…"), self.export_dialog),
                           (_("Importa impostazioni…"), self.import_dialog),
                           (_("Ripristina predefiniti"), self.ask_restore_defaults)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(lambda: (self.save(), d.hide()))
        buttons.rejected.connect(d.hide)
        layout.addWidget(buttons)
        return d

    def _select(self, box: QComboBox, value) -> None:
        i = box.findData(value)
        box.setCurrentIndex(max(0, i))

    def _load(self) -> None:
        self._select(self.layout_box, self.value("viewer/default_layout", VIEW_CONTINUOUS))
        self._select(self.zoom_box, str(self.value("viewer/default_zoom", FIT_WIDTH)))
        self._select(self.panel_box, self.value("viewer/default_panel", ""))
        self.respect.setChecked(not _bool(self.value("viewer/ignore_document_view"), False))
        self.restore.setChecked(_bool(self.value("viewer/restore_position"), True))
        self.no_history.setChecked(_bool(self.value("viewer/no_history"), False))

    def save(self) -> None:
        s = self.ctx.settings()
        s.setValue("viewer/default_layout", self.layout_box.currentData())
        s.setValue("viewer/default_zoom", self.zoom_box.currentData())
        s.setValue("viewer/default_panel", self.panel_box.currentData())
        s.setValue("viewer/ignore_document_view", not self.respect.isChecked())
        s.setValue("viewer/restore_position", self.restore.isChecked())
        s.setValue("viewer/no_history", self.no_history.isChecked())

    # ------------------------------------------------------- esporta/importa
    def export_text(self) -> str:
        s = self.ctx.settings()
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp["foliant"] = {"version": str(EXPORT_VERSION)}
        cp["settings"] = {}
        for key in sorted(s.allKeys()):
            if key.startswith(PRIVATE_PREFIXES):
                continue
            value = s.value(key)
            if isinstance(value, (list, tuple)):
                value = "\n".join(str(v) for v in value)
                key = key + "[]"
            elif isinstance(value, bool):
                value = "true" if value else "false"
            elif value is None:
                continue
            elif not isinstance(value, (str, int, float)):
                continue
            cp["settings"][key] = str(value)
        buf = io.StringIO()
        cp.write(buf)
        return buf.getvalue()

    def import_text(self, text: str) -> int:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        try:
            cp.read_string(text)
        except configparser.Error as e:
            raise ValueError(_("File di impostazioni non valido.")) from e
        if "settings" not in cp or "foliant" not in cp:
            raise ValueError(_("File di impostazioni non valido."))
        s = self.ctx.settings()
        n = 0
        for key, value in cp["settings"].items():
            if key.endswith("[]"):
                key, value = key[:-2], [v for v in value.split("\n") if v]
            if key.startswith(PRIVATE_PREFIXES):
                continue
            s.setValue(key, value)
            n += 1
        # scorciatoie e temi si applicano subito se le parti sono caricate
        parts = getattr(self.ctx, "viewer_parts", {})
        if parts.get("commands") is not None:
            parts["commands"].apply_saved()
        return n

    def export_dialog(self) -> None:
        path = self.ctx.ask_save_path(_("Esporta impostazioni…"), "foliant-impostazioni.ini",
                                      _("Impostazioni (*.ini)"), ".ini")
        if path:
            atomic_write(path, self.export_text().encode("utf-8"))

    def import_dialog(self) -> None:
        paths = self.ctx.ask_open_paths(_("Importa impostazioni…"), _("Impostazioni (*.ini)"), multiple=False)
        if not paths:
            return
        try:
            with open(paths[0], encoding="utf-8") as f:
                n = self.import_text(f.read())
        except (OSError, UnicodeDecodeError, ValueError) as e:
            self.ctx.error(_("Importa impostazioni…"), str(e))
            return
        self.ctx.info(_("Importa impostazioni…"),
                      _("Impostazioni importate: {n} valori. Alcune si applicano al prossimo avvio.").format(n=n))

    def restore_defaults(self) -> None:
        s = self.ctx.settings()
        for key in s.allKeys():
            if key.startswith("viewer/") and not key.startswith(PRIVATE_PREFIXES):
                s.remove(key)
        parts = getattr(self.ctx, "viewer_parts", {})
        if parts.get("commands") is not None:
            parts["commands"].restore_defaults()

    def ask_restore_defaults(self) -> None:
        if self.ctx.confirm(_("Ripristina predefiniti"), _("Ripristinare tutte le impostazioni ai valori predefiniti?")):
            self.restore_defaults()
            if self.dialog is not None:
                self._load()


def install(ctx: Context) -> PrefsController:
    add_translations("en", TRANSLATIONS)
    return PrefsController(ctx)
