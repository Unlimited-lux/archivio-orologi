"""Proprietà del documento (VIS-40), in sola lettura."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QHeaderView, QLabel, QScrollArea, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from foliant.api import _, add_translations, bind_icon, theme

from .properties_engine import (
    DocumentInfo, advanced_rows, collect_fonts, collect_info, description_rows, info_to_text, security_rows,
)

LARGE_DOCUMENT_PAGES = 100   # oltre: raccolta dei font in un'attività con avanzamento

EN = {
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
    def __init__(self, info: DocumentInfo, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("documentPropertiesDialog")
        self.setWindowTitle(title)
        self.info = info
        root = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(_form_page(description_rows(info), self), _("Descrizione"))
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
        self.tabs.addTab(self.font_tree, _("Font"))
        self.tabs.addTab(_form_page(advanced_rows(info), self), _("Avanzate"))
        root.addWidget(self.tabs)

        buttons = QDialogButtonBox(self)
        self.copy_button = buttons.addButton(_("Copia negli appunti"), QDialogButtonBox.ButtonRole.ActionRole)
        bind_icon(self.copy_button, "clipboard-copy")
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
        self.action = ctx.action(_("Proprietà del documento…"), "info", self.show_dialog, shortcut="Ctrl+D")
        mode = ctx.mode("leggi")
        if mode is not None:
            mode.add_action(self.action)
        ctx.add_menu_action(self.action, _("File"))
        ctx.documentClosed.connect(self._on_closed)

    def collect(self, view) -> DocumentInfo:
        """Dati del documento della vista (font in background se è grande)."""
        document = view.document
        fonts = None
        if document.page_count > LARGE_DOCUMENT_PAGES:
            fonts = self.ctx.run_task(_("Lettura dei font"), lambda progress: collect_fonts(document.pdf, progress))
        return collect_info(document, view.current_page, fonts)

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
        title = _("Proprietà di «{name}»").format(name=view.document.display_name)
        self.dialog = PropertiesDialog(info, title, self.ctx.window)
        self.dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.dialog.show()
        return self.dialog

    def _on_closed(self, _view) -> None:
        if self.dialog is not None:
            self.dialog.close()


def install(ctx) -> PropertiesController:
    add_translations("en", EN)
    return PropertiesController(ctx)
