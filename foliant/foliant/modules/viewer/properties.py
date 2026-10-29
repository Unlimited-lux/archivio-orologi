"""Informazioni sul documento (VIS-40), in sola lettura: sezioni File, Contenuto,
Protezione, Caratteri, Apertura. Il comando è in File › Informazioni › «Proprietà» (Alt+Invio)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel, QScrollArea, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import _, add_translations, bind_icon, theme
from foliant.i18n import pgettext

from .properties_engine import (
    DocumentInfo, collect_fonts, collect_info, content_rows, file_rows, info_to_text, opening_rows, security_rows,
)

LARGE_DOCUMENT_PAGES = 100   # oltre: raccolta dei font in un'attività con avanzamento

EN = {
    "Metadati avanzati…": "Advanced metadata…",
    "Modifica opzioni di apertura…": "Edit opening options…",
    "Informazioni sul documento…": "Document information…",
    "scheda File\x04Proprietà": "Properties",
    "Titolo, autore, parole chiave, pagine, versione": "Title, author, keywords, pages, version",
    "Informazioni su «{name}»": "Information about “{name}”",
    "Contenuto": "Content",
    "Caratteri": "Fonts",
    "Apertura": "Opening",
    "Creato con": "Created with",
    "Convertito in PDF da": "Converted to PDF by",
    "Data di creazione": "Creation date",
    "Ultima modifica": "Last modified",
    "Formato della pagina {n}": "Size of page {n}",
    "Struttura logica per l'accessibilità": "Logical structure for accessibility",
    "presente": "present",
    "assente": "absent",
    "Caricamento progressivo dalla rete": "Progressive loading from the network",
    "previsto": "supported",
    "non previsto": "not supported",
    "Impostazioni del documento": "Document settings",
    "nessuna: si usano le preferenze": "none: preferences are used",
    "Pagina iniziale": "Initial page",
    "Zoom iniziale": "Initial zoom",
    "adatta larghezza": "fit width",
    "adatta pagina": "fit page",
    "Disposizione delle pagine": "Page layout",
    "continua": "continuous",
    "pagina singola": "single page",
    "due pagine": "two pages",
    "due pagine con copertina": "two pages with cover",
    "Pannello all'apertura": "Panel on opening",
    "segnalibri": "bookmarks",
    "miniature": "thumbnails",
    "allegati": "attachments",
    "livelli": "layers",
    "Schermo intero": "Full screen",
    "richiesto dal documento": "requested by the document",
    "Proprietà del documento…": "Document properties…",
    "Proprietà del documento": "Document properties",
    "Proprietà di «{name}»": "Properties of “{name}”",
    "File": "File",
    "Descrizione": "Description",
    "Protezione": "Security",
    "Font": "Fonts",
    "Avanzate": "Advanced",
    "Nome": "Name",
    "Tipo": "Type",
    "Codifica": "Encoding",
    "Stato": "Status",
    "Nessun font nel documento.": "No fonts in the document.",
    "Copia negli appunti": "Copy to clipboard",
    "Chiudi": "Close",
    "Proprietà copiate negli appunti.": "Properties copied to the clipboard.",
    "Lettura dei font": "Reading fonts",
    "Proprietà non disponibili": "Properties not available",
    "Permessi": "Permissions",
    # motore
    "Stampa del documento": "Printing",
    "Stampa ad alta qualità": "High-quality printing",
    "Modifica del contenuto": "Content editing",
    "Copia ed estrazione di testo e immagini": "Copying and extracting text and images",
    "Commenti e campi modulo": "Comments and form fields",
    "Compilazione dei moduli": "Form filling",
    "Accessibilità (tecnologie assistive)": "Accessibility (assistive technologies)",
    "Assemblaggio (inserire, ruotare, eliminare pagine)": "Assembly (insert, rotate, delete pages)",
    "Standard, revisione {n}": "Standard, revision {n}",
    "{n} byte": "{n} bytes",
    "verticale": "portrait",
    "orizzontale": "landscape",
    "Incorporato": "Embedded",
    "Incorporato (sottoinsieme)": "Embedded (subset)",
    "Non incorporato": "Not embedded",
    "Pagina {n} di {t}": "Page {n} of {t}",
    "Sì": "Yes",
    "No": "No",
    "non salvato": "not saved",
    "modifiche non salvate": "unsaved changes",
    "Titolo": "Title",
    "Autore": "Author",
    "Oggetto": "Subject",
    "Parole chiave": "Keywords",
    "Applicazione": "Application",
    "Produttore PDF": "PDF producer",
    "Creato": "Created",
    "Modificato": "Modified",
    "Percorso": "Location",
    "Dimensione del file": "File size",
    "Pagine": "Pages",
    "Dimensione pagina {n}": "Page {n} size",
    "Versione PDF": "PDF version",
    "Vista web veloce": "Fast web view",
    "Documento taggato": "Tagged PDF",
    "Cifratura": "Encryption",
    "Nessuna": "None",
    "Accesso": "Access",
    "Tutte le operazioni consentite": "All operations allowed",
    "Con restrizioni": "Restricted",
    "Consentito": "Allowed",
    "Non consentito": "Not allowed",
    "Oggetti PDF": "PDF objects",
    "Allegati": "Attachments",
    "Livelli": "Layers",
    "Campi modulo": "Form fields",
    "Nessuno": "None",
    "Segnalibri": "Bookmarks",
}


def _form_page(rows: list[tuple[str, str]], parent: QWidget, coloured: dict[str, str] | None = None) -> QWidget:
    """Scheda con coppie etichetta/valore selezionabili."""
    area = QScrollArea(parent)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    inner = QWidget(area)
    form = QFormLayout(inner)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(6)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    muted = theme.hex_color("text_muted")
    for label, value in rows:
        key = QLabel(label, inner)
        key.setStyleSheet(f"color: {muted};")
        val = QLabel(value, inner)
        val.setWordWrap(True)
        val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if coloured and value in coloured:
            val.setStyleSheet(f"color: {coloured[value]};")
        val.setObjectName("propertyValue")
        form.addRow(key, val)
    area.setWidget(inner)
    return area


class PropertiesDialog(QDialog):
    def __init__(self, info: DocumentInfo, title: str, parent: QWidget | None = None, on_edit_opening=None,
                 on_edit_metadata=None):
        super().__init__(parent)
        self.setObjectName("documentPropertiesDialog")
        self.setWindowTitle(title)
        self.info = info
        root = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(_form_page(file_rows(info), self), _("File"))
        self.tabs.addTab(_form_page(content_rows(info), self), _("Contenuto"))
        coloured = {_("Consentito"): theme.hex_color("success"), _("Non consentito"): theme.hex_color("danger")}
        self.tabs.addTab(_form_page(security_rows(info), self, coloured), _("Protezione"))
        self.font_tree = QTreeWidget(self)
        self.font_tree.setRootIsDecorated(False)
        self.font_tree.setAlternatingRowColors(True)
        self.font_tree.setHeaderLabels([_("Nome"), _("Tipo"), _("Codifica"), _("Stato")])
        for f in info.fonts:
            item = QTreeWidgetItem([f.name, f.font_type, f.encoding or "—", f.embedding_label()])
            if f.embedding == "not_embedded":
                item.setForeground(3, theme.color("warning"))
            self.font_tree.addTopLevelItem(item)
        if not info.fonts:
            self.font_tree.addTopLevelItem(QTreeWidgetItem([_("Nessun font nel documento.")]))
        header = self.font_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            header.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tabs.addTab(self.font_tree, _("Caratteri"))
        self.tabs.addTab(_form_page(opening_rows(info), self), _("Apertura"))
        root.addWidget(self.tabs)

        buttons = QDialogButtonBox(self)
        self.copy_button = buttons.addButton(_("Copia negli appunti"), QDialogButtonBox.ButtonRole.ActionRole)
        bind_icon(self.copy_button, "clipboard-copy")
        self.opening_button = None
        self.metadata_button = None
        if on_edit_metadata is not None:         # VIS-41: dalla scheda Contenuto
            self.metadata_button = buttons.addButton(_("Metadati avanzati…"), QDialogButtonBox.ButtonRole.ActionRole)
            bind_icon(self.metadata_button, "tags")
            self.metadata_button.clicked.connect(lambda: on_edit_metadata())
        if on_edit_opening is not None:          # VIS-42: dalla scheda Apertura
            self.opening_button = buttons.addButton(_("Modifica opzioni di apertura…"),
                                                    QDialogButtonBox.ButtonRole.ActionRole)
            bind_icon(self.opening_button, "settings-2")
            self.opening_button.clicked.connect(lambda: on_edit_opening())
        close = buttons.addButton(_("Chiudi"), QDialogButtonBox.ButtonRole.RejectRole)
        close.setDefault(True)
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(560, 520)

    def text(self) -> str:
        return info_to_text(self.info)

    def copy_to_clipboard(self) -> None:
        QApplication.clipboard().setText(self.text())


class PropertiesController:
    def __init__(self, ctx):
        self.ctx = ctx
        self.dialog: PropertiesDialog | None = None
        self.action = ctx.action(_("Informazioni sul documento…"), "info", self.show_dialog, shortcut="Alt+Return")
        # File › Informazioni, gruppo «Proprietà» (come le Proprietà della pagina Informazioni di Excel)
        ctx.add_file_action(self.action, "informazioni", pgettext("scheda File", "Proprietà"),
                            _("Titolo, autore, parole chiave, pagine, versione"), order=10)
        ctx.documentClosed.connect(self._on_closed)

    def collect(self, view) -> DocumentInfo:
        """Dati del documento della vista (font in background se è grande)."""
        document = view.document
        fonts = None
        if document.page_count > LARGE_DOCUMENT_PAGES:
            fonts = self.ctx.run_task(_("Lettura dei font"), lambda progress: collect_fonts(document.pdf, progress))
        info = collect_info(document, view.current_page, fonts)
        info.opening = self._opening(document)
        return info

    @staticmethod
    def _opening(document) -> list[tuple[str, str]]:
        """Come il documento chiede di essere aperto (`/OpenAction`, `/PageLayout`, `/PageMode`)."""
        try:
            from .session_engine import read_initial_view
            with document.lock:
                iv = read_initial_view(document.pdf)
        except Exception:
            return []
        rows = []
        if iv.page is not None:
            rows.append((_("Pagina iniziale"), str(iv.page + 1)))
        if iv.fit or iv.zoom:
            rows.append((_("Zoom iniziale"), {"width": _("adatta larghezza"), "page": _("adatta pagina")}
                         .get(iv.fit or "", f"{round((iv.zoom or 1) * 100)}%")))
        if iv.mode:
            rows.append((_("Disposizione delle pagine"), {"continuous": _("continua"), "single": _("pagina singola"),
                                                         "facing": _("due pagine"), "cover": _("due pagine con copertina")}
                         .get(iv.mode, iv.mode)))
        if iv.panel:
            rows.append((_("Pannello all'apertura"), {"bookmarks": _("segnalibri"), "thumbnails": _("miniature"),
                                                     "attachments": _("allegati"), "layers": _("livelli")}
                         .get(iv.panel, iv.panel)))
        if iv.fullscreen:
            rows.append((_("Schermo intero"), _("richiesto dal documento")))
        return rows

    def show_dialog(self) -> PropertiesDialog | None:
        """Mostra il dialogo (non modale) per il documento attivo."""
        view = self.ctx.current_view()
        if view is None:
            return None
        try:
            info = self.collect(view)
        except Exception as e:  # noqa: BLE001 - la lettura non deve chiudere l'app
            if type(e).__name__ == "OperationCancelled":
                return None
            self.ctx.error(_("Proprietà non disponibili"), str(e))
            return None
        if self.dialog is not None:
            self.dialog.close()
            self.dialog.deleteLater()
        title = _("Informazioni su «{name}»").format(name=view.document.display_name)
        parts = getattr(self.ctx, "viewer_parts", {})
        opener, meta = parts.get("openoptions"), parts.get("xmp")
        self.dialog = PropertiesDialog(info, title, self.ctx.window,
                                       on_edit_opening=(lambda v=view: opener.open_dialog(v)) if opener else None,
                                       on_edit_metadata=(lambda v=view: meta.open_dialog(v)) if meta else None)
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.dialog.show()
        return self.dialog

    def _on_closed(self, _view) -> None:
        if self.dialog is not None:
            self.dialog.close()


def install(ctx) -> PropertiesController:
    add_translations("en", EN)
    return PropertiesController(ctx)
