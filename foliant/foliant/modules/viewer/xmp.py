"""Metadati avanzati (VIS-41): editor del pacchetto XMP per schema, esportazione e importazione
del pacchetto, modelli di metadati applicabili al documento corrente o a tutti quelli aperti.
Le proprietà personalizzate e i metadati descrittivi di base sono del modulo Sicurezza
(SEC-23); qui le funzioni avanzate. Ogni modifica è un passo annullabile («metadata»)."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, atomic_write, bind_icon

from . import xmp_engine as engine

TRANSLATIONS = {
    "Metadati avanzati…": "Advanced metadata…",
    "Metadati avanzati": "Advanced metadata",
    "Schema": "Schema",
    "Proprietà": "Property",
    "Valore": "Value",
    "Aggiungi proprietà…": "Add property…",
    "Rimuovi": "Remove",
    "Esporta XMP…": "Export XMP…",
    "Importa XMP…": "Import XMP…",
    "Modelli": "Templates",
    "Salva come modello…": "Save as template…",
    "Applica al documento": "Apply to the document",
    "Applica a tutti i documenti aperti": "Apply to all open documents",
    "Elimina modello": "Delete template",
    "Nessun modello salvato": "No saved templates",
    "Nome del modello:": "Template name:",
    "Diritti": "Rights",
    "Stato del copyright": "Copyright status",
    "Sconosciuto": "Unknown",
    "Protetto da copyright": "Copyrighted",
    "Di pubblico dominio": "Public domain",
    "Avviso di copyright": "Copyright notice",
    "URL delle informazioni sui diritti": "Rights information URL",
    "Autore": "Author",
    "Titolo": "Title",
    "Nome della proprietà:": "Property name:",
    "Nome non valido: usa lettere, cifre, «_», «-» o «.» e non iniziare con una cifra.":
        "Invalid name: use letters, digits, “_”, “-” or “.” and do not start with a digit.",
    "Proprietà personalizzata": "Custom property",
    "Dublin Core": "Dublin Core",
    "Valore di «{name}»:": "Value of “{name}”:",
    "Sostituire i metadati attuali? Scegli No per unire quelli importati agli attuali.":
        "Replace the current metadata? Choose No to merge the imported ones with the current ones.",
    "File XMP (*.xmp)": "XMP files (*.xmp)",
    "Impossibile leggere il file: {e}": "Cannot read the file: {e}",
    "Modello «{name}» applicato a {n} documenti.": "Template “{name}” applied to {n} documents.",
    "Il documento non consente modifiche.": "The document does not allow changes.",
    "(struttura)": "(structure)",
}

SCHEMA_LABELS = {"dc": "Dublin Core", "xmpRights": "Diritti", "foliant": "Proprietà personalizzata"}
TEMPLATES_KEY = "viewer/xmp_templates"
# Proprietà dei modelli: (prefisso, nome) -> etichetta
TEMPLATE_FIELDS = ((("dc", "creator"), "Autore"), (("dc", "rights"), "Avviso di copyright"),
                   (("xmpRights", "Marked"), "Stato del copyright"),
                   (("xmpRights", "WebStatement"), "URL delle informazioni sui diritti"))


class MetadataDialog(QDialog):
    def __init__(self, controller: "MetadataController", view: DocumentView):
        super().__init__(controller.ctx.window)
        self.controller = controller
        self.view = view
        self.setObjectName("xmpDialog")
        self.setWindowTitle(_("Metadati avanzati"))
        root = QVBoxLayout(self)

        rights = QFormLayout()
        self.copyright = QComboBox(self)
        for data, label in (("", "Sconosciuto"), ("True", "Protetto da copyright"), ("False", "Di pubblico dominio")):
            self.copyright.addItem(_(label), data)
        self.notice = QLineEdit(self)
        self.url = QLineEdit(self)
        rights.addRow(_("Stato del copyright"), self.copyright)
        rights.addRow(_("Avviso di copyright"), self.notice)
        rights.addRow(_("URL delle informazioni sui diritti"), self.url)
        root.addLayout(rights)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels([_("Proprietà"), _("Valore")])
        self.tree.setColumnWidth(0, 200)
        self.tree.itemDoubleClicked.connect(self._edit_item)
        root.addWidget(self.tree, 1)

        row = QHBoxLayout()
        self.btn_add = QPushButton(_("Aggiungi proprietà…"), self)
        self.btn_add.clicked.connect(self.add_property)
        self.btn_remove = QPushButton(_("Rimuovi"), self)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_export = QPushButton(_("Esporta XMP…"), self)
        self.btn_export.clicked.connect(lambda: controller.export_dialog(self.view))
        self.btn_import = QPushButton(_("Importa XMP…"), self)
        self.btn_import.clicked.connect(lambda: (controller.import_dialog(self.view), self.load()))
        self.btn_templates = QPushButton(_("Modelli"), self)
        self.templates_menu = QMenu(self.btn_templates)
        self.templates_menu.aboutToShow.connect(self._fill_templates)
        self.btn_templates.setMenu(self.templates_menu)
        for b in (self.btn_add, self.btn_remove):
            row.addWidget(b)
        row.addStretch(1)
        for b in (self.btn_import, self.btn_export, self.btn_templates):
            row.addWidget(b)
        root.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.resize(620, 560)
        self.load()

    # ------------------------------------------------------------- dati
    def load(self) -> None:
        self.packet = self.controller.read(self.view)
        marked = self.packet.get("xmpRights", "Marked")
        self.copyright.setCurrentIndex(max(0, self.copyright.findData(marked.text() if marked else "")))
        notice = self.packet.get("dc", "rights")
        self.notice.setText(notice.text() if notice else "")
        url = self.packet.get("xmpRights", "WebStatement")
        self.url.setText(url.text() if url else "")
        self._fill_tree()

    def _fill_tree(self) -> None:
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for p in self.packet.props:
            group = groups.get(p.prefix)
            if group is None:
                group = QTreeWidgetItem([_(SCHEMA_LABELS[p.prefix]) if p.prefix in SCHEMA_LABELS else p.prefix])
                font = group.font(0)
                font.setBold(True)
                group.setFont(0, font)
                self.tree.addTopLevelItem(group)
                groups[p.prefix] = group
            item = QTreeWidgetItem([p.name, p.text() if p.kind != engine.RAW else _("(struttura)")])
            item.setData(0, Qt.ItemDataRole.UserRole, (p.prefix, p.name))
            group.addChild(item)
        self.tree.expandAll()

    def packet_with_rights(self) -> engine.Packet:
        """Il pacchetto con i campi dei diritti in alto applicati."""
        p = engine.merge(engine.Packet(), self.packet, replace=True)
        for (prefix, name), value in (((("xmpRights", "Marked")), self.copyright.currentData() or ""),
                                      ((("dc", "rights")), self.notice.text().strip()),
                                      ((("xmpRights", "WebStatement")), self.url.text().strip())):
            if value:
                p.set(prefix, name, value)
            else:
                p.remove(prefix, name)
        return p

    # ------------------------------------------------------------- modifica
    def _edit_item(self, item: QTreeWidgetItem, _col: int = 0) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        prop = self.packet.get(*key)
        if prop is None or prop.kind == engine.RAW:
            return
        value, ok = QInputDialog.getText(self, _("Metadati avanzati"), _("Valore di «{name}»:").format(name=prop.name),
                                         text=prop.text())
        if ok:
            self.packet.set(prop.prefix, prop.name, value, prop.kind)
            self._fill_tree()

    def add_property(self, prefix: str | None = None, name: str | None = None, value: str | None = None) -> bool:
        if prefix is None:
            prefix, ok = QInputDialog.getItem(self, _("Aggiungi proprietà…"), _("Schema"),
                                              ["dc", "xmpRights", "xmp", "pdf", engine.CUSTOM_PREFIX], 0, False)
            if not ok:
                return False
        if name is None:
            name, ok = QInputDialog.getText(self, _("Aggiungi proprietà…"), _("Nome della proprietà:"))
            if not ok:
                return False
        if not engine.valid_name(name):
            self.controller.ctx.error(_("Aggiungi proprietà…"),
                                      _("Nome non valido: usa lettere, cifre, «_», «-» o «.» e non iniziare con una "
                                        "cifra."))
            return False
        if value is None:
            value, ok = QInputDialog.getText(self, _("Aggiungi proprietà…"), _("Valore di «{name}»:").format(name=name))
            if not ok:
                return False
        self.packet.set(prefix, name, value)
        self._fill_tree()
        return True

    def remove_selected(self) -> None:
        item = self.tree.currentItem()
        key = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if key:
            self.packet.remove(*key)
            self._fill_tree()

    def _fill_templates(self) -> None:
        menu = self.templates_menu
        menu.clear()
        a = menu.addAction(_("Salva come modello…"), lambda: self.controller.save_template_dialog(self.packet_with_rights()))
        bind_icon(a, "save")
        templates = self.controller.templates()
        if not templates:
            menu.addAction(_("Nessun modello salvato")).setEnabled(False)
        for name in templates:
            sub = menu.addMenu(name)
            sub.addAction(_("Applica al documento"), lambda n=name: (self.controller.apply_template(n, [self.view]),
                                                                    self.load()))
            sub.addAction(_("Applica a tutti i documenti aperti"),
                          lambda n=name: (self.controller.apply_template(n, self.controller.ctx.views()), self.load()))
            sub.addAction(_("Elimina modello"), lambda n=name: self.controller.delete_template(n))


class MetadataController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.act = ctx.action(_("Metadati avanzati…"), "tags", self.open_dialog)
        ctx.add_menu_action(self.act, _("File"))

    # ------------------------------------------------------------- documento
    def read(self, view: DocumentView) -> engine.Packet:
        with ENGINE_LOCK:
            return engine.read_packet(view.document.pdf)

    def write(self, view: DocumentView, packet: engine.Packet, label: str | None = None) -> bool:
        import pymupdf
        doc = view.document
        if not doc.allows(pymupdf.PDF_PERM_MODIFY):
            self.ctx.error(_("Metadati avanzati"), _("Il documento non consente modifiche."))
            return False
        try:
            with doc.change(label or _("Metadati avanzati"), kind="metadata") as pdf:
                engine.write_packet(pdf, packet)
        except Exception as e:  # noqa: BLE001
            self.ctx.error(_("Metadati avanzati"), str(e))
            return False
        return True

    def open_dialog(self, view: DocumentView | None = None) -> bool:
        view = view or self.ctx.current_view()
        if view is None:
            return False
        dlg = MetadataDialog(self, view)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return False
        return self.write(view, dlg.packet_with_rights())

    # ------------------------------------------------------------- XMP
    def export_xmp(self, view: DocumentView, path: str) -> str:
        with ENGINE_LOCK:
            packet = engine.read_packet(view.document.pdf)
        atomic_write(path, engine.build_xmp(packet).encode("utf-8"))
        return path

    def import_xmp(self, view: DocumentView, path: str, replace: bool = False) -> bool:
        try:
            with open(path, encoding="utf-8-sig") as f:
                other = engine.parse_xmp(f.read())
        except (OSError, UnicodeDecodeError) as e:
            self.ctx.error(_("Importa XMP…"), _("Impossibile leggere il file: {e}").format(e=e))
            return False
        except ValueError as e:                     # XML non valido: la riga dell'errore
            self.ctx.error(_("Importa XMP…"), str(e))
            return False
        return self.write(view, engine.merge(self.read(view), other, replace), _("Importa XMP…"))

    def export_dialog(self, view: DocumentView) -> str | None:
        stem = view.document.display_name.rsplit(".", 1)[0]
        path = self.ctx.ask_save_path(_("Esporta XMP…"), stem + ".xmp", _("File XMP (*.xmp)"), ".xmp")
        return self.export_xmp(view, path) if path else None

    def import_dialog(self, view: DocumentView) -> bool:
        paths = self.ctx.ask_open_paths(_("Importa XMP…"), _("File XMP (*.xmp)"), multiple=False)
        if not paths:
            return False
        replace = self.ctx.confirm(_("Importa XMP…"), _("Sostituire i metadati attuali? Scegli No per unire quelli "
                                                         "importati agli attuali."))
        return self.import_xmp(view, paths[0], replace)

    # ------------------------------------------------------------- modelli
    def templates(self) -> dict[str, dict[str, str]]:
        try:
            data = json.loads(self.ctx.settings().value(TEMPLATES_KEY, "{}") or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save_template(self, name: str, packet: engine.Packet) -> None:
        values = {}
        for (prefix, prop), _label in TEMPLATE_FIELDS:
            p = packet.get(prefix, prop)
            if p is not None and p.text():
                values[f"{prefix}:{prop}"] = p.text()
        for p in packet.props:                       # anche le proprietà personalizzate
            if p.prefix == engine.CUSTOM_PREFIX:
                values[f"{p.prefix}:{p.name}"] = p.text()
        data = self.templates()
        data[name] = values
        self.ctx.settings().setValue(TEMPLATES_KEY, json.dumps(data, ensure_ascii=False))

    def save_template_dialog(self, packet: engine.Packet) -> bool:
        name, ok = QInputDialog.getText(self.ctx.window, _("Salva come modello…"), _("Nome del modello:"))
        if not ok or not name.strip():
            return False
        self.save_template(name.strip(), packet)
        return True

    def delete_template(self, name: str) -> None:
        data = self.templates()
        data.pop(name, None)
        self.ctx.settings().setValue(TEMPLATES_KEY, json.dumps(data, ensure_ascii=False))

    def apply_template(self, name: str, views) -> int:
        values = self.templates().get(name)
        if not values:
            return 0
        done = 0
        for view in views:
            packet = self.read(view)
            for key, value in values.items():
                prefix, _sep, prop = key.partition(":")
                packet.set(prefix, prop, value)
            if self.write(view, packet, _("Metadati avanzati")):
                done += 1
        self.ctx.status(_("Modello «{name}» applicato a {n} documenti.").format(name=name, n=done))
        return done


def install(ctx: Context) -> MetadataController:
    add_translations("en", TRANSLATIONS)
    return MetadataController(ctx)
