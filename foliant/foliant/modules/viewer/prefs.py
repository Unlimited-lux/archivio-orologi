"""Preferenze di visualizzazione (VIS-51, parte): layout e zoom predefiniti,
pannello iniziale, rispetto delle impostazioni del documento, unità di misura;
esportazione e importazione delle impostazioni; ripristino dei valori predefiniti.

Riservatezza: si esportano e importano solo preferenze generali; le chiavi che
contenevano (o potrebbero contenere) nomi, percorsi o cronologie dei documenti
vengono sempre scartate, così un file .ini non può reintrodurle."""

from __future__ import annotations

import configparser
import io
import re

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QPushButton, QVBoxLayout,
)

from foliant.api import (
    FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE, Context, DocumentView, _,
    add_translations, atomic_write,
)

TRANSLATIONS = {
    "foliant-impostazioni.ini": "foliant-settings.ini",
    "Preferenze di visualizzazione…": "View preferences…",
    "Preferenze di visualizzazione": "View preferences",
    "Layout predefinito": "Default layout",
    "Zoom predefinito": "Default zoom",
    "Pannello all'apertura": "Panel on opening",
    "Nessuno": "None",
    "Miniature": "Thumbnails",
    "Segnalibri": "Bookmarks",
    "Rispetta le impostazioni del documento": "Respect the document's settings",
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
    "Adatta alla larghezza": "Fit width",
    "Adatta alla pagina": "Fit page",
    "Strumenti": "Tools",
}

LAYOUTS = ((VIEW_CONTINUOUS, "Scorrimento continuo"), (VIEW_SINGLE, "Pagina singola"),
           (VIEW_FACING, "Due pagine"), (VIEW_COVER, "Due pagine con copertina"))
# Layout e zoom predefiniti usano le chiavi del nucleo (Preferenze > Generale), che il nucleo
# applica a ogni documento nuovo: view/mode e view/zoom ("width", "page" o una percentuale).
KEY_MODE = "view/mode"
KEY_ZOOM = "view/zoom"
ZOOMS = ((FIT_WIDTH, "Adatta alla larghezza"), (FIT_PAGE, "Adatta alla pagina"), ("75", "75%"), ("100", "100%"),
         ("125", "125%"), ("150", "150%"), ("200", "200%"))
CORE_ZOOMS = (FIT_WIDTH, FIT_PAGE, "100")      # quelle che il nucleo applica da sé
# chiavi delle versioni precedenti del viewer, migrate su quelle del nucleo
OLD_LAYOUT = "viewer/default_layout"
OLD_ZOOM = "viewer/default_zoom"
PANELS = (("", "Nessuno"), ("thumbnails", "Miniature"), ("bookmarks", "Segnalibri"))
EXPORT_VERSION = 1
# chiavi che non si esportano né importano: dati personali o legati alla macchina, e tutte le
# chiavi che nelle versioni precedenti contenevano nomi, percorsi o cronologie dei documenti
PRIVATE_PREFIXES = ("recent", "Recent", "pinned", "Pinned", "lastDirectory", "window/", "session/",
                    "viewer/positions", "viewer/session", "viewer/restore_session", "viewer/search_history",
                    "viewer/no_history", "viewer/restore_position", "viewer/closed", "viewer/privacy_cleanup")
# parti del nome di una chiave che indicano percorsi o cronologie (in qualsiasi modulo)
PRIVATE_KEY_PARTS = ("recent", "pinned", "last_dir", "lastdir", "last_folder", "lastfolder", "folder",
                     "history", "session", "positions", "last_path", "lastpath", "last_file", "lastfile")
# valori che sembrano percorsi di file o cartelle
_PATH_VALUE = re.compile(r"^(?:/|~[/\\]|file:|[A-Za-z]:[\\/]|\\\\)")


def is_private_key(key: str) -> bool:
    """Chiave da non esportare né importare (percorsi, cronologie, dati della macchina)."""
    if key.startswith(PRIVATE_PREFIXES):
        return True
    low = key.lower()
    return any(part in low for part in PRIVATE_KEY_PARTS)


def is_private_value(value) -> bool:
    """Valore che sembra un percorso (o un elenco che ne contiene)."""
    if isinstance(value, (list, tuple)):
        return any(is_private_value(v) for v in value)
    return isinstance(value, str) and bool(_PATH_VALUE.match(value.strip()))


def _bool(v, default=False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes")


def zoom_key(value) -> str | None:
    """Valore di zoom nel formato del nucleo: "width", "page" o percentuale intera ("100")."""
    v = str(value or "").strip()
    if v in (FIT_WIDTH, FIT_PAGE):
        return v
    try:
        f = float(v)
    except ValueError:
        return None
    if f <= 0:
        return None
    return str(round(f * 100)) if f < 10 else str(round(f))   # vecchio formato: fattore (1.0)


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
        self.migrate()

    def migrate(self) -> None:
        """Porta le vecchie chiavi viewer/default_layout e viewer/default_zoom su quelle del
        nucleo (senza sovrascrivere un valore già scelto in Preferenze > Generale)."""
        s = self.ctx.settings()
        layout = s.value(OLD_LAYOUT)
        if layout is not None:
            if layout in dict(LAYOUTS) and s.value(KEY_MODE) is None:
                s.setValue(KEY_MODE, layout)
            s.remove(OLD_LAYOUT)
        zoom = s.value(OLD_ZOOM)
        if zoom is not None:
            new = zoom_key(zoom)
            if new is not None and s.value(KEY_ZOOM) is None:
                s.setValue(KEY_ZOOM, new)
            s.remove(OLD_ZOOM)

    # ---------------------------------------------------------------- valori
    def value(self, key: str, default=None):
        return self.ctx.settings().value(key, default)

    def apply_defaults(self, view: DocumentView) -> None:
        """Completa i valori predefiniti che il nucleo ha già applicato al documento nuovo
        (layout, larghezza, pagina, 100%): solo le altre percentuali e il pannello iniziale."""
        zoom = zoom_key(self.value(KEY_ZOOM, FIT_WIDTH))
        if zoom is not None and zoom not in CORE_ZOOMS:
            view.set_zoom(int(zoom) / 100)
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
        layout.addWidget(self.respect)
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
        self._select(self.layout_box, self.value(KEY_MODE, VIEW_CONTINUOUS))
        self._select(self.zoom_box, zoom_key(self.value(KEY_ZOOM, FIT_WIDTH)) or FIT_WIDTH)
        self._select(self.panel_box, self.value("viewer/default_panel", ""))
        self.respect.setChecked(not _bool(self.value("viewer/ignore_document_view"), False))

    def save(self) -> None:
        s = self.ctx.settings()
        s.setValue(KEY_MODE, self.layout_box.currentData())
        s.setValue(KEY_ZOOM, self.zoom_box.currentData())
        s.setValue("viewer/default_panel", self.panel_box.currentData())
        s.setValue("viewer/ignore_document_view", not self.respect.isChecked())

    # ------------------------------------------------------- esporta/importa
    def export_text(self) -> str:
        s = self.ctx.settings()
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str
        cp["foliant"] = {"version": str(EXPORT_VERSION)}
        cp["settings"] = {}
        for key in sorted(s.allKeys()):
            if is_private_key(key):
                continue
            value = s.value(key)
            if is_private_value(value):
                continue
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
            if is_private_key(key) or is_private_value(value):
                continue
            s.setValue(key, value)
            n += 1
        self.migrate()          # file esportati da versioni precedenti
        # scorciatoie e temi si applicano subito se le parti sono caricate
        parts = getattr(self.ctx, "viewer_parts", {})
        if parts.get("commands") is not None:
            parts["commands"].apply_saved()
        return n

    def export_dialog(self) -> None:
        path = self.ctx.ask_save_path(_("Esporta impostazioni…"), _("foliant-impostazioni.ini"),
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
            if key.startswith("viewer/") and not is_private_key(key):
                s.remove(key)
        s.remove(KEY_MODE)
        s.remove(KEY_ZOOM)
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
