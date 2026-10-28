"""Finestre per creare PDF: da immagini (CNV-19) e PDF vuoto (CNV-23)."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImageReader, QPixmap, QTransform
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout,
)

from foliant.api import _, bind_icon, theme

IMAGE_FILTER_EXT = "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.jp2 *.jpx *.pnm *.ppm *.pgm *.pbm"

STANDARD_SIZES = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5", "Letter", "Legal", "Tabloid"]


def _combo(items, current=None) -> QComboBox:
    box = QComboBox()
    for label, data in items:
        box.addItem(label, data)
    if current is not None:
        i = box.findData(current)
        if i >= 0:
            box.setCurrentIndex(i)
    return box


class ImagesDialog(QDialog):
    """Elenco ordinabile di immagini con formato pagina, adattamento e qualità."""

    ROLE_PATH = Qt.ItemDataRole.UserRole
    ROLE_ROT = Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent, paths: list[str], ask_more=None, *, paper: str = "A4", unit: str = "mm"):
        super().__init__(parent)
        self.margin_unit = "in" if unit == "in" else "mm"
        self.setWindowTitle(_("Crea PDF da immagini"))
        self.resize(760, 560)
        self._ask_more = ask_more
        lay = QVBoxLayout(self)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.ViewMode.IconMode)
        self.list.setIconSize(QSize(110, 110))
        self.list.setGridSize(QSize(140, 150))
        self.list.setMovement(QListWidget.Movement.Snap)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setWordWrap(True)
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        for text, icon_name, slot in ((_("Aggiungi…"), "image-plus", self._add),
                                      (_("Rimuovi"), "trash-2", self._remove),
                                      (_("Ruota a sinistra"), "rotate-ccw", lambda: self._rotate(-90)),
                                      (_("Ruota a destra"), "rotate-cw", lambda: self._rotate(90))):
            b = QPushButton(text)
            bind_icon(b, icon_name)
            b.setAutoDefault(False)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(QLabel(_("Ordina:")))
        self.sort_box = _combo([(_("Per nome"), "name"), (_("Per data"), "date"), (_("Come selezionate"), "none")])
        self.sort_box.setCurrentIndex(2)
        self.sort_box.activated.connect(self._sort)
        row.addWidget(self.sort_box)
        lay.addLayout(row)

        opts = QGroupBox(_("Pagine"))
        form = QFormLayout(opts)
        self.size_box = _combo([(_("Come l'immagine"), None)] + [(s, s) for s in STANDARD_SIZES], paper)
        form.addRow(_("Formato pagina"), self.size_box)
        self.orient_box = _combo([(_("Automatico"), "auto"), (_("Verticale"), "portrait"),
                                  (_("Orizzontale"), "landscape")])
        form.addRow(_("Orientamento"), self.orient_box)
        self.fit_box = _combo([(_("Contieni (proporzioni mantenute)"), "contain"), (_("Riempi ritagliando"), "fill"),
                               (_("Dimensione reale centrata"), "actual")])
        form.addRow(_("Adattamento"), self.fit_box)
        self.margin = QDoubleSpinBox()
        if self.margin_unit == "in":      # NFR-26: unità di misura locale
            self.margin.setDecimals(2)
            self.margin.setRange(0, 4)
            self.margin.setSingleStep(0.1)
            self.margin.setValue(0.4)
            self.margin.setSuffix(" in")
        else:
            self.margin.setRange(0, 100)
            self.margin.setValue(10)
            self.margin.setSuffix(" mm")
        form.addRow(_("Margini"), self.margin)
        self.quality_box = _combo([(_("Originale (nessuna ricompressione)"), "original"),
                                   (_("Ricompressa"), "recompress")])
        form.addRow(_("Qualità"), self.quality_box)
        self.ppi = QSpinBox()
        self.ppi.setRange(50, 1200)
        self.ppi.setValue(200)
        self.ppi.setSuffix(" ppi")
        form.addRow(_("Risoluzione massima"), self.ppi)
        from PySide6.QtWidgets import QCheckBox
        self.ocr = QCheckBox(_("Rendi il testo ricercabile (OCR)"))
        form.addRow("", self.ocr)
        self.quality_box.currentIndexChanged.connect(lambda: self.ppi.setEnabled(self.quality_box.currentData() != "original"))
        self.ppi.setEnabled(False)
        self.size_box.currentIndexChanged.connect(self._size_changed)
        lay.addWidget(opts)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Crea PDF"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.add_paths(paths)

    def _size_changed(self) -> None:
        free = self.size_box.currentData() is None
        self.orient_box.setEnabled(not free)
        self.fit_box.setEnabled(not free)
        self.margin.setEnabled(not free)

    def add_paths(self, paths: list[str]) -> None:
        existing = set(self.paths())
        for p in paths:
            if p in existing:
                continue
            item = QListWidgetItem(Path(p).name)
            item.setData(self.ROLE_PATH, p)
            item.setData(self.ROLE_ROT, 0)
            item.setToolTip(p)
            item.setIcon(self._thumb(p, 0))
            self.list.addItem(item)
        self.ok_button.setEnabled(self.list.count() > 0)

    @staticmethod
    def _thumb(path: str, rotation: int):
        from PySide6.QtGui import QIcon
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and size.width() > 0:
            reader.setScaledSize(size.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
        if img.isNull():
            pm = QPixmap(110, 110)
            pm.fill(theme.color("surface_alt"))
            return QIcon(pm)
        pm = QPixmap.fromImage(img)
        if rotation:
            pm = pm.transformed(QTransform().rotate(rotation))
        return QIcon(pm)

    def _add(self) -> None:
        if self._ask_more is not None:
            self.add_paths(self._ask_more())

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.ok_button.setEnabled(self.list.count() > 0)

    def _rotate(self, deg: int) -> None:
        for item in self.list.selectedItems():
            rot = (int(item.data(self.ROLE_ROT) or 0) + deg) % 360
            item.setData(self.ROLE_ROT, rot)
            item.setIcon(self._thumb(item.data(self.ROLE_PATH), rot))

    def _sort(self) -> None:
        from .create import sort_images
        mode = self.sort_box.currentData()
        if mode == "none":
            return
        rots = {self.list.item(i).data(self.ROLE_PATH): self.list.item(i).data(self.ROLE_ROT)
                for i in range(self.list.count())}
        ordered = sort_images(self.paths(), mode)
        self.list.clear()
        self.add_paths(ordered)
        for i in range(self.list.count()):
            item = self.list.item(i)
            rot = rots.get(item.data(self.ROLE_PATH), 0) or 0
            if rot:
                item.setData(self.ROLE_ROT, rot)
                item.setIcon(self._thumb(item.data(self.ROLE_PATH), rot))

    def paths(self) -> list[str]:
        return [self.list.item(i).data(self.ROLE_PATH) for i in range(self.list.count())]

    def options(self) -> dict:
        rotations = {}
        for i in range(self.list.count()):
            item = self.list.item(i)
            rot = int(item.data(self.ROLE_ROT) or 0)
            if rot:
                rotations[item.data(self.ROLE_PATH)] = rot
        return {
            "page_size": self.size_box.currentData(),
            "orientation": self.orient_box.currentData(),
            "fit": self.fit_box.currentData(),
            # «Come l'immagine»: pagina della dimensione esatta dell'immagine, senza margini
            "margin_pt": 0.0 if self.size_box.currentData() is None else
            self.margin.value() * (72 if self.margin_unit == "in" else 72 / 25.4),
            "quality": self.quality_box.currentData(),
            "max_ppi": self.ppi.value() if self.quality_box.currentData() != "original" else None,
            "rotations": rotations or None,
        }


class BlankDialog(QDialog):
    """Nuovo PDF vuoto: formato, orientamento, pagine, colore di fondo e stile carta."""

    def __init__(self, parent, *, paper: str = "A4", unit: str = "mm"):
        super().__init__(parent)
        self.setWindowTitle(_("Nuovo PDF vuoto"))
        inches = unit == "in"
        self.setMinimumWidth(420)
        self.background = QColor(255, 255, 255)
        self.line_color = QColor(191, 204, 217)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.size_box = _combo([(s, s) for s in ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5", "Letter",
                                                 "Legal", "Tabloid"]] + [(_("Personalizzato"), "custom")], paper)
        form.addRow(_("Formato"), self.size_box)
        custom = QHBoxLayout()
        self.w = QDoubleSpinBox()
        self.h = QDoubleSpinBox()
        for s in (self.w, self.h):
            s.setRange(0.1, 100000)
            s.setDecimals(1)
        # dimensioni personalizzate proposte nel formato e nell'unità locali (NFR-26)
        if inches:
            for spin in (self.w, self.h):
                spin.setDecimals(2)
            self.w.setValue(8.5 if paper == "Letter" else 8.27)
            self.h.setValue(11.0 if paper == "Letter" else 11.69)
        else:
            self.w.setValue(215.9 if paper == "Letter" else 210)
            self.h.setValue(279.4 if paper == "Letter" else 297)
        self.unit = _combo([("mm", "mm"), (_("pollici"), "in"), (_("punti"), "pt")], "in" if inches else "mm")
        custom.addWidget(self.w)
        custom.addWidget(QLabel("×"))
        custom.addWidget(self.h)
        custom.addWidget(self.unit)
        form.addRow(_("Dimensioni"), custom)
        self.orient_box = _combo([(_("Verticale"), "portrait"), (_("Orizzontale"), "landscape")])
        form.addRow(_("Orientamento"), self.orient_box)
        self.pages = QSpinBox()
        self.pages.setRange(1, 10000)
        self.pages.setValue(1)
        form.addRow(_("Pagine"), self.pages)
        self.bg_button = QPushButton()
        self.bg_button.setAutoDefault(False)
        self.bg_button.clicked.connect(self._pick_bg)
        form.addRow(_("Colore di fondo"), self.bg_button)
        self.paper_box = _combo([(_("Bianca"), "blank"), (_("A righe"), "lines"), (_("A quadretti"), "grid"),
                                 (_("A punti"), "dots")])
        form.addRow(_("Stile carta"), self.paper_box)
        self.step = QDoubleSpinBox()
        self.step_inches = inches
        if inches:
            self.step.setDecimals(2)
            self.step.setRange(0.05, 4)
            self.step.setSingleStep(0.05)
            self.step.setValue(0.25)
            self.step.setSuffix(" in")
        else:
            self.step.setRange(1, 100)
            self.step.setValue(5)
            self.step.setSuffix(" mm")
        form.addRow(_("Passo"), self.step)
        self.line_button = QPushButton()
        self.line_button.setAutoDefault(False)
        self.line_button.clicked.connect(self._pick_line)
        form.addRow(_("Colore delle linee"), self.line_button)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        lay.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Crea"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.size_box.currentIndexChanged.connect(self._update)
        self.paper_box.currentIndexChanged.connect(self._update)
        self._update()

    def _swatch(self, button: QPushButton, color: QColor) -> None:
        button.setText(color.name())
        button.setStyleSheet(f"QPushButton {{ background: {color.name()}; color: "
                             f"{'#000000' if color.lightness() > 128 else '#FFFFFF'}; }}")

    def _update(self) -> None:
        custom = self.size_box.currentData() == "custom"
        for wdg in (self.w, self.h, self.unit):
            wdg.setEnabled(custom)
        self.orient_box.setEnabled(not custom)
        lined = self.paper_box.currentData() != "blank"
        self.step.setEnabled(lined)
        self.line_button.setEnabled(lined)
        self._swatch(self.bg_button, self.background)
        self._swatch(self.line_button, self.line_color)

    def _pick_bg(self) -> None:
        c = QColorDialog.getColor(self.background, self, _("Colore di fondo"))
        if c.isValid():
            self.background = c
            self._update()

    def _pick_line(self) -> None:
        c = QColorDialog.getColor(self.line_color, self, _("Colore delle linee"))
        if c.isValid():
            self.line_color = c
            self._update()

    def options(self) -> dict:
        from .create import size_from_units
        if self.size_box.currentData() == "custom":
            size = size_from_units(self.w.value(), self.h.value(), self.unit.currentData())
        else:
            size = self.size_box.currentData()
        bg = self.background
        lc = self.line_color
        return {"size": size, "orientation": self.orient_box.currentData(), "pages": self.pages.value(),
                "background": (bg.redF(), bg.greenF(), bg.blueF()), "paper": self.paper_box.currentData(),
                "step_mm": self.step.value() * (25.4 if self.step_inches else 1),
                "line_color": (lc.redF(), lc.greenF(), lc.blueF())}

    def accept(self) -> None:
        from .common import ConversionError
        from .create import blank_pdf
        try:
            opts = self.options()
            blank_pdf(**{**opts, "pages": 1})   # verifica dei limiti
        except ConversionError as e:
            self.error.setText(str(e))
            self.error.setVisible(True)
            return
        except Exception as e:
            self.error.setText(str(e))
            self.error.setVisible(True)
            return
        super().accept()




# preset predefiniti (CNV-24): pensati per l'archivio di un negozio (documenti, identità, garanzie)
def builtin_scan_presets() -> list[dict]:
    return [
        {"name": _("Documenti in bianco e nero (fatture, contratti)"), "source": "adf", "dpi": 300, "mode": "bw",
         "straighten": True, "crop": True, "ocr": True, "remove_blank": True},
        {"name": _("Documento d'identità a colori"), "source": "flatbed", "dpi": 300, "mode": "color",
         "quality": 90, "id_card": True, "straighten": False, "crop": False},
        {"name": _("Certificati e garanzie a colori"), "source": "flatbed", "dpi": 300, "mode": "color",
         "quality": 90, "straighten": True, "crop": True, "ocr": True},
        {"name": _("Bozza veloce in grigio"), "source": "adf", "dpi": 150, "mode": "gray", "quality": 70},
    ]


PRESET_KEYS = ("source", "dpi", "mode", "paper", "brightness", "contrast", "quality", "straighten", "crop", "ocr",
               "remove_blank", "manual_duplex", "id_card")


class ScannerDialog(QDialog):
    """Impostazioni di acquisizione (CNV-24): preset, dispositivo, sorgente, risoluzione, colore,
    anteprima e post-elaborazione."""

    def __init__(self, parent, devices: list[tuple[str, str]], *, has_document: bool = False, refresh=None,
                 settings=None, preview=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox
        self.setWindowTitle(_("Crea PDF da scanner"))
        self.setMinimumWidth(460)
        self._refresh = refresh
        self._settings = settings
        self._preview = preview
        outer = QHBoxLayout(self)
        lay = QVBoxLayout()
        outer.addLayout(lay, 1)
        preset_row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.setMinimumContentsLength(18)
        preset_row.addWidget(self.preset, 1)
        self.save_preset = QPushButton(_("Salva…"))
        self.delete_preset = QPushButton(_("Elimina"))
        self.export_preset = QPushButton(_("Esporta…"))
        self.import_preset = QPushButton(_("Importa…"))
        for b, ic in ((self.save_preset, "save"), (self.delete_preset, "trash-2"), (self.export_preset, "upload"),
                      (self.import_preset, "download")):
            bind_icon(b, ic)
            b.setAutoDefault(False)
            preset_row.addWidget(b)
        self.save_preset.clicked.connect(self._save_preset)
        self.delete_preset.clicked.connect(self._delete_preset)
        self.export_preset.clicked.connect(self._export_preset)
        self.import_preset.clicked.connect(self._import_preset)
        form = QFormLayout()
        form.addRow(_("Preset"), preset_row)
        dev_row = QHBoxLayout()
        self.device = QComboBox()
        dev_row.addWidget(self.device, 1)
        again = QPushButton(_("Aggiorna"))
        bind_icon(again, "refresh-cw")
        again.setAutoDefault(False)
        again.clicked.connect(self._reload)
        dev_row.addWidget(again)
        form.addRow(_("Scanner"), dev_row)
        self.source = _combo([(_("Piano"), "flatbed"), (_("Alimentatore"), "adf"),
                              (_("Alimentatore fronte/retro"), "adf-duplex")])
        form.addRow(_("Sorgente"), self.source)
        self.dpi = _combo([(f"{d} dpi", d) for d in (75, 150, 200, 300, 400, 600, 1200)], 300)
        form.addRow(_("Risoluzione"), self.dpi)
        self.mode = _combo([(_("Colore"), "color"), (_("Scala di grigi"), "gray"), (_("Bianco e nero"), "bw")])
        form.addRow(_("Colore"), self.mode)
        from .scanner import PAPER_MM
        self.paper = _combo([(_("Automatico"), "")] + [(k, k) for k in PAPER_MM])
        form.addRow(_("Formato carta"), self.paper)
        self.brightness = QSpinBox()
        self.contrast = QSpinBox()
        for s in (self.brightness, self.contrast):
            s.setRange(-100, 100)
        form.addRow(_("Luminosità"), self.brightness)
        form.addRow(_("Contrasto"), self.contrast)
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(85)
        form.addRow(_("Qualità JPEG"), self.quality)
        lay.addLayout(form)
        self.straighten = QCheckBox(_("Raddrizza automaticamente"))
        self.straighten.setChecked(True)
        self.crop = QCheckBox(_("Ritaglia automaticamente i bordi"))
        self.crop.setChecked(True)
        self.ocr = QCheckBox(_("Rendi il testo ricercabile (OCR)"))
        self.remove_blank = QCheckBox(_("Rimuovi le pagine vuote"))
        self.manual_duplex = QCheckBox(_("Fronte/retro manuale (acquisisci prima i fronti, poi i retro)"))
        self.id_card = QCheckBox(_("Documento d'identità: fronte e retro sulla stessa pagina A4"))
        self.insert = QCheckBox(_("Inserisci nel documento aperto dopo la pagina corrente"))
        self.insert.setVisible(has_document)
        for cb in (self.straighten, self.crop, self.ocr, self.remove_blank, self.manual_duplex, self.id_card, self.insert):
            lay.addWidget(cb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Acquisisci"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.preview_button = buttons.addButton(_("Anteprima"), QDialogButtonBox.ButtonRole.ActionRole)
        bind_icon(self.preview_button, "eye")
        self.preview_button.setVisible(preview is not None)
        self.preview_button.clicked.connect(self._show_preview)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(220, 300)
        self.preview_label.setVisible(False)
        outer.addWidget(self.preview_label)
        self.set_devices(devices)
        self._fill_presets()
        self.preset.activated.connect(lambda _i: self._apply_current())

    # ---------------------------------------------------------------- preset
    def _user_presets(self) -> list[dict]:
        import json
        if self._settings is None:
            return []
        try:
            data = json.loads(self._settings.value("convert/scan_presets", "[]") or "[]")
            return [p for p in data if isinstance(p, dict) and p.get("name")]
        except (ValueError, TypeError):
            return []

    def _store_user_presets(self, presets: list[dict]) -> None:
        import json
        if self._settings is not None:
            self._settings.setValue("convert/scan_presets", json.dumps(presets, ensure_ascii=False))

    def _fill_presets(self, select: str | None = None) -> None:
        self.preset.blockSignals(True)
        self.preset.clear()
        self.preset.addItem(_("Impostazioni personalizzate"), None)
        for p in builtin_scan_presets():
            self.preset.addItem(p["name"], ("builtin", p))
        for p in self._user_presets():
            self.preset.addItem(p["name"], ("user", p))
        if select:
            i = self.preset.findText(select)
            self.preset.setCurrentIndex(max(0, i))
        self.preset.blockSignals(False)
        self._update_preset_buttons()

    def _update_preset_buttons(self) -> None:
        data = self.preset.currentData()
        self.delete_preset.setEnabled(bool(data) and data[0] == "user")

    def _apply_current(self) -> None:
        data = self.preset.currentData()
        if data:
            self.apply_preset(data[1])
        self._update_preset_buttons()

    def preset_values(self) -> dict:
        return {"source": self.source.currentData(), "dpi": self.dpi.currentData(), "mode": self.mode.currentData(),
                "paper": self.paper.currentData(), "brightness": self.brightness.value(),
                "contrast": self.contrast.value(), "quality": self.quality.value(),
                "straighten": self.straighten.isChecked(), "crop": self.crop.isChecked(),
                "ocr": self.ocr.isChecked(), "remove_blank": self.remove_blank.isChecked(),
                "manual_duplex": self.manual_duplex.isChecked(), "id_card": self.id_card.isChecked()}

    def apply_preset(self, preset: dict) -> None:
        defaults = {"source": "flatbed", "dpi": 300, "mode": "color", "paper": "", "brightness": 0, "contrast": 0,
                    "quality": 85, "straighten": False, "crop": False, "ocr": False, "remove_blank": False,
                    "manual_duplex": False, "id_card": False}
        values = {**defaults, **{k: v for k, v in preset.items() if k in PRESET_KEYS}}
        for box, key in ((self.source, "source"), (self.dpi, "dpi"), (self.mode, "mode"), (self.paper, "paper")):
            i = box.findData(values[key])
            if i >= 0:
                box.setCurrentIndex(i)
        for spin, key in ((self.brightness, "brightness"), (self.contrast, "contrast"), (self.quality, "quality")):
            try:
                spin.setValue(int(values[key]))
            except (TypeError, ValueError):
                pass
        for cb, key in ((self.straighten, "straighten"), (self.crop, "crop"), (self.ocr, "ocr"),
                        (self.remove_blank, "remove_blank"), (self.manual_duplex, "manual_duplex"),
                        (self.id_card, "id_card")):
            cb.setChecked(bool(values[key]))

    def _save_preset(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        current = self.preset.currentData()
        name, ok = QInputDialog.getText(self, _("Salva preset"), _("Nome del preset:"),
                                        text=current[1]["name"] if current and current[0] == "user" else "")
        name = name.strip()
        if not ok or not name:
            return
        presets = [p for p in self._user_presets() if p["name"] != name]
        presets.append({"name": name, **self.preset_values()})
        self._store_user_presets(presets)
        self._fill_presets(select=name)

    def _delete_preset(self) -> None:
        data = self.preset.currentData()
        if not data or data[0] != "user":
            return
        self._store_user_presets([p for p in self._user_presets() if p["name"] != data[1]["name"]])
        self._fill_presets()

    def _export_preset(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog
        data = self.preset.currentData()
        name = data[1]["name"] if data else _("Impostazioni personalizzate")
        path, _f = QFileDialog.getSaveFileName(self, _("Esporta preset"), name + ".json", "JSON (*.json)")
        if not path:
            return
        try:
            Path(path).write_text(json.dumps({"foliant_scan_preset": 1, "name": name, **self.preset_values()},
                                             ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, _("Esporta preset"), _("Impossibile scrivere il file: {e}").format(e=e.strerror or e))

    def _import_preset(self) -> None:
        import json
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        path, _f = QFileDialog.getOpenFileName(self, _("Importa preset"), "", "JSON (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not str(data.get("name", "")).strip():
                raise ValueError
        except (OSError, ValueError, UnicodeDecodeError):
            QMessageBox.warning(self, _("Importa preset"), _("Il file non contiene un preset di acquisizione valido."))
            return
        preset = {"name": str(data["name"]).strip(), **{k: data[k] for k in PRESET_KEYS if k in data}}
        presets = [p for p in self._user_presets() if p["name"] != preset["name"]] + [preset]
        self._store_user_presets(presets)
        self._fill_presets(select=preset["name"])
        self.apply_preset(preset)

    # ---------------------------------------------------------------- dispositivo
    def set_devices(self, devices) -> None:
        self.device.clear()
        for dev, label in devices:
            self.device.addItem(label or dev, dev)
        self.ok_button.setEnabled(bool(devices))
        if hasattr(self, "preview_button"):
            self.preview_button.setEnabled(bool(devices))

    def _reload(self) -> None:
        if self._refresh is not None:
            self.set_devices(self._refresh())

    def _show_preview(self) -> None:
        if self._preview is None:
            return
        img = self._preview(self.device.currentData(), self.settings())
        if img is None:
            return
        from PIL.ImageQt import ImageQt
        pix = QPixmap.fromImage(ImageQt(img.convert("RGB")))
        self.preview_label.setPixmap(pix.scaled(QSize(220, 300), Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        self.preview_label.setVisible(True)

    def settings(self):
        from .scanner import ScanSettings
        return ScanSettings(source=self.source.currentData(), dpi=self.dpi.currentData(), mode=self.mode.currentData(),
                            brightness=self.brightness.value(), contrast=self.contrast.value(),
                            paper=self.paper.currentData() or None, jpeg_quality=self.quality.value())


class CornerEditor(QLabel):
    """Anteprima della foto con i 4 angoli del foglio trascinabili."""

    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 420)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img = None          # QImage
        self._corners = None      # numpy 4x2 in pixel immagine
        self._drag = -1

    def set_photo(self, rgb, corners) -> None:
        from PySide6.QtGui import QImage
        h, w = rgb.shape[:2]
        self._img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self._corners = corners.copy()
        self.update()

    def corners(self):
        return None if self._corners is None else self._corners.copy()

    def _geom(self):
        iw, ih = self._img.width(), self._img.height()
        k = min(self.width() / iw, self.height() / ih)
        ox, oy = (self.width() - iw * k) / 2, (self.height() - ih * k) / 2
        return k, ox, oy

    def paintEvent(self, ev) -> None:
        from PySide6.QtCore import QPointF, QRectF
        from PySide6.QtGui import QPainter, QPen, QPolygonF
        if self._img is None:
            return super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k, ox, oy = self._geom()
        p.drawImage(QRectF(ox, oy, self._img.width() * k, self._img.height() * k), self._img)
        pts = [QPointF(ox + x * k, oy + y * k) for x, y in self._corners]
        pen = QPen(theme.color("accent"), 2)
        p.setPen(pen)
        p.drawPolygon(QPolygonF(pts))
        p.setBrush(theme.color("accent"))
        for pt in pts:
            p.drawEllipse(pt, 6, 6)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if self._corners is None:
            return
        k, ox, oy = self._geom()
        pos = ev.position()
        d = [((ox + x * k - pos.x()) ** 2 + (oy + y * k - pos.y()) ** 2) for x, y in self._corners]
        i = min(range(4), key=lambda j: d[j])
        self._drag = i if d[i] < 400 else -1

    def mouseMoveEvent(self, ev) -> None:
        if self._drag < 0:
            return
        k, ox, oy = self._geom()
        pos = ev.position()
        x = min(max(0.0, (pos.x() - ox) / k), self._img.width() - 1)
        y = min(max(0.0, (pos.y() - oy) / k), self._img.height() - 1)
        self._corners[self._drag] = (x, y)
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = -1


class PhotosDialog(QDialog):
    """Foto di documenti: angoli rilevati e regolabili, filtro e formato (CNV-26)."""

    def __init__(self, parent, paths: list[str], *, paper: str = "A4", ask_more=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox
        self.setWindowTitle(_("Crea PDF da foto di documenti"))
        self.resize(820, 600)
        self.items: list[dict] = []
        self._ask_more = ask_more
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        left.addWidget(self.list, 1)
        self.add_button = QPushButton(_("Aggiungi foto…"))
        bind_icon(self.add_button, "image-plus")
        self.add_button.setAutoDefault(False)
        self.add_button.setVisible(ask_more is not None)
        self.add_button.clicked.connect(self._add_more)
        left.addWidget(self.add_button)
        form = QFormLayout()
        self.filter = _combo([(_("Documento a colori"), "color"), (_("Originale"), "original"),
                              (_("Scala di grigi"), "gray"), (_("Bianco e nero"), "bw"), (_("Lavagna"), "whiteboard")])
        form.addRow(_("Filtro"), self.filter)
        self.size = _combo([("A4", "A4"), ("Letter", "Letter"), (_("Biglietto da visita"), "card"),
                            (_("Documento d'identità"), "id"), (_("Come rilevato"), None)], paper)
        form.addRow(_("Formato"), self.size)
        left.addLayout(form)
        self.ocr = QCheckBox(_("Rendi il testo ricercabile (OCR)"))
        left.addWidget(self.ocr)
        hint = QLabel(_("Trascina gli angoli per correggere il bordo del foglio."))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        left.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Crea PDF"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)
        lay.addLayout(left, 1)
        self.editor = CornerEditor()
        lay.addWidget(self.editor, 2)
        self._current = -1
        self.list.currentRowChanged.connect(self._select)
        self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> int:
        """Aggiunge foto (angoli rilevati subito); restituisce quante sono leggibili."""
        from .photo import detect_corners, load_image
        added = 0
        for p in paths:
            try:
                rgb = load_image(p)
            except Exception:
                continue
            corners, found = detect_corners(rgb)
            self.items.append({"path": p, "rgb": rgb, "corners": corners, "found": found})
            self.list.addItem(Path(p).name + ("" if found else "  " + _("(bordi da regolare)")))
            added += 1
        if added and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        return added

    def _add_more(self) -> None:
        if self._ask_more is not None:
            paths = self._ask_more()
            if paths:
                self.add_paths(paths)

    def _store(self) -> None:
        if 0 <= self._current < len(self.items):
            c = self.editor.corners()
            if c is not None:
                self.items[self._current]["corners"] = c

    def _select(self, row: int) -> None:
        self._store()
        self._current = row
        if 0 <= row < len(self.items):
            it = self.items[row]
            self.editor.set_photo(it["rgb"], it["corners"])

    def values(self) -> tuple[list[dict], dict]:
        self._store()
        return ([{"path": it["path"], "corners": it["corners"]} for it in self.items],
                {"page_size": self.size.currentData(), "filter_name": self.filter.currentData()})

    def wants_ocr(self) -> bool:
        return self.ocr.isChecked()


class CropLabel(QLabel):
    """Anteprima della cattura con selezione rettangolare dell'area da tenere."""

    def __init__(self, image):
        super().__init__()
        self._img = image
        self._start = None
        self._sel = None          # QRect in coordinate dell'immagine
        self.setMinimumSize(480, 300)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def _geom(self):
        k = min(self.width() / self._img.width(), self.height() / self._img.height())
        ox, oy = (self.width() - self._img.width() * k) / 2, (self.height() - self._img.height() * k) / 2
        return k, ox, oy

    def _to_image(self, pos):
        from PySide6.QtCore import QPoint
        k, ox, oy = self._geom()
        x = int(min(max(0, (pos.x() - ox) / k), self._img.width()))
        y = int(min(max(0, (pos.y() - oy) / k), self._img.height()))
        return QPoint(x, y)

    def set_selection(self, rect) -> None:
        self._sel = rect.intersected(self._img.rect()) if rect is not None else None
        self.update()

    def selection(self):
        return self._sel if self._sel is not None and self._sel.width() > 4 and self._sel.height() > 4 else None

    def paintEvent(self, ev) -> None:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPen
        p = QPainter(self)
        k, ox, oy = self._geom()
        p.drawImage(QRectF(ox, oy, self._img.width() * k, self._img.height() * k), self._img)
        if self._sel is not None:
            r = QRectF(ox + self._sel.x() * k, oy + self._sel.y() * k, self._sel.width() * k, self._sel.height() * k)
            p.setPen(QPen(theme.color("accent"), 2))
            p.drawRect(r)
        p.end()

    def mousePressEvent(self, ev) -> None:
        self._start = self._to_image(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        from PySide6.QtCore import QRect
        if self._start is not None:
            self.set_selection(QRect(self._start, self._to_image(ev.position())).normalized())

    def mouseReleaseEvent(self, ev) -> None:
        self._start = None


class CaptureDialog(QDialog):
    """Anteprima della cattura: ritaglio facoltativo, «Aggiungi un'altra cattura» o «Crea PDF»."""

    ANOTHER = 2

    def __init__(self, parent, image, count: int):
        super().__init__(parent)
        self.setWindowTitle(_("Cattura {n}").format(n=count))
        self.resize(820, 580)
        lay = QVBoxLayout(self)
        hint = QLabel(_("Trascina per tenere solo un'area; senza selezione si usa tutta la cattura."))
        hint.setStyleSheet(f"color: {theme.hex_color('text_muted')};")
        lay.addWidget(hint)
        self.preview = CropLabel(image)
        lay.addWidget(self.preview, 1)
        buttons = QDialogButtonBox()
        another = buttons.addButton(_("Aggiungi un'altra cattura"), QDialogButtonBox.ButtonRole.ActionRole)
        create = buttons.addButton(_("Crea PDF"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(_("Annulla"), QDialogButtonBox.ButtonRole.RejectRole)
        another.clicked.connect(lambda: self.done(self.ANOTHER))
        create.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def cropped(self):
        sel = self.preview.selection()
        return self.preview._img.copy(sel) if sel is not None else self.preview._img


class OfficeOptionsDialog(QDialog):
    """Opzioni di conversione dei documenti d'ufficio in PDF (CNV-18), ricordate tra le sessioni."""

    KEY = "convert/office_options"

    def __init__(self, parent, *, kinds: set[str], settings=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox
        from .create import OFFICE_DEFAULTS
        self.setWindowTitle(_("Opzioni dei documenti d'ufficio"))
        self._settings = settings
        values = {**OFFICE_DEFAULTS, **self._stored()}
        lay = QVBoxLayout(self)
        general = QGroupBox(_("Documento"))
        g = QFormLayout(general)
        self.bookmarks = QCheckBox(_("Segnalibri dai titoli"))
        self.tagged = QCheckBox(_("PDF taggato (accessibile)"))
        self.comments = QCheckBox(_("Commenti come annotazioni"))
        self.final_state = QCheckBox(_("Revisioni: stampa lo stato finale senza marcature"))
        for cb in (self.bookmarks, self.tagged, self.comments, self.final_state):
            g.addRow(cb)
        self.image_quality = QSpinBox()
        self.image_quality.setRange(10, 100)
        self.image_quality.setSuffix(" %")
        g.addRow(_("Qualità delle immagini"), self.image_quality)
        self.max_dpi = _combo([(_("Originale"), 0), ("300 ppi", 300), ("150 ppi", 150), ("96 ppi", 96)])
        g.addRow(_("Risoluzione massima delle immagini"), self.max_dpi)
        self.pdfa = _combo([(_("PDF normale"), ""), ("PDF/A-1b", "1b"), ("PDF/A-2b", "2b"), ("PDF/A-3b", "3b")])
        g.addRow(_("Formato di uscita"), self.pdfa)
        lay.addWidget(general)
        self.sheet_box = QGroupBox(_("Fogli di calcolo"))
        sf = QFormLayout(self.sheet_box)
        self.sheets = _combo([(_("Tutti i fogli"), "all"), (_("Solo il foglio attivo"), "active")])
        sf.addRow(_("Fogli"), self.sheets)
        self.fit_width = QCheckBox(_("Adatta le colonne alla larghezza della pagina"))
        sf.addRow(self.fit_width)
        lay.addWidget(self.sheet_box)
        self.slide_box = QGroupBox(_("Presentazioni"))
        pf = QFormLayout(self.slide_box)
        self.notes_pages = QCheckBox(_("Aggiungi le pagine delle note"))
        self.hidden_slides = QCheckBox(_("Includi le diapositive nascoste"))
        pf.addRow(self.notes_pages)
        pf.addRow(self.hidden_slides)
        lay.addWidget(self.slide_box)
        self.sheet_box.setVisible("sheet" in kinds)
        self.slide_box.setVisible("slides" in kinds)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(_("Crea PDF"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.load(values)

    def _stored(self) -> dict:
        import json
        if self._settings is None:
            return {}
        try:
            data = json.loads(self._settings.value(self.KEY, "{}") or "{}")
            return data if isinstance(data, dict) else {}
        except (ValueError, TypeError):
            return {}

    def load(self, values: dict) -> None:
        for cb, key in ((self.bookmarks, "bookmarks"), (self.tagged, "tagged"), (self.comments, "comments"),
                        (self.final_state, "final_state"), (self.fit_width, "fit_width"),
                        (self.notes_pages, "notes_pages"), (self.hidden_slides, "hidden_slides")):
            cb.setChecked(bool(values.get(key)))
        try:
            self.image_quality.setValue(int(values.get("image_quality", 90)))
        except (TypeError, ValueError):
            pass
        for box, key in ((self.max_dpi, "max_image_dpi"), (self.pdfa, "pdfa"), (self.sheets, "sheets")):
            i = box.findData(values.get(key))
            if i >= 0:
                box.setCurrentIndex(i)

    def values(self) -> dict:
        return {"bookmarks": self.bookmarks.isChecked(), "tagged": self.tagged.isChecked(),
                "comments": self.comments.isChecked(), "final_state": self.final_state.isChecked(),
                "image_quality": self.image_quality.value(), "max_image_dpi": self.max_dpi.currentData(),
                "pdfa": self.pdfa.currentData(), "sheets": self.sheets.currentData(),
                "fit_width": self.fit_width.isChecked(), "notes_pages": self.notes_pages.isChecked(),
                "hidden_slides": self.hidden_slides.isChecked()}

    def _accept(self) -> None:
        import json
        if self._settings is not None:
            self._settings.setValue(self.KEY, json.dumps(self.values()))
        self.accept()


class CameraDialog(QDialog):
    """Anteprima della webcam con «Scatta» per ogni pagina (CNV-26). `frames` raccoglie gli scatti."""

    def __init__(self, parent, backend, devices: list[tuple[int, str]]):
        super().__init__(parent)
        from PySide6.QtCore import QTimer
        self.setWindowTitle(_("Crea PDF da fotocamera"))
        self.resize(760, 560)
        self.backend = backend
        self.frames: list = []
        self._last = None
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel(_("Fotocamera")))
        self.device = QComboBox()
        for index, label in devices:
            self.device.addItem(label, index)
        row.addWidget(self.device, 1)
        lay.addLayout(row)
        self.view = QLabel()
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(480, 360)
        lay.addWidget(self.view, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.done_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.done_button.setText(_("Fine"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        self.shot_button = buttons.addButton(_("Scatta"), QDialogButtonBox.ButtonRole.ActionRole)
        bind_icon(self.shot_button, "camera")
        self.shot_button.clicked.connect(self.shoot)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(66)
        self.timer.timeout.connect(self.refresh)
        self.device.currentIndexChanged.connect(lambda _i: self._open())
        self._open()
        self._update_status()

    def _open(self) -> None:
        self.timer.stop()
        try:
            self.backend.open(self.device.currentData())
        except Exception as e:
            self._last = None
            self.view.setText(str(e))
            self.shot_button.setEnabled(False)
            return
        self.shot_button.setEnabled(True)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        frame = self.backend.read()
        if frame is None:
            return
        from PySide6.QtGui import QImage
        self._last = frame
        h, w = frame.shape[:2]
        img = QImage(frame.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
        self.view.setPixmap(QPixmap.fromImage(img).scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))

    def shoot(self) -> None:
        if self._last is None:
            self.refresh()
        if self._last is not None:
            self.frames.append(self._last.copy())
        self._update_status()

    def _update_status(self) -> None:
        self.status.setText(_("Pagine scattate: {n}. Inquadra il foglio intero su uno sfondo scuro; dopo «Fine» "
                              "potrai regolare gli angoli.").format(n=len(self.frames)))
        self.done_button.setEnabled(bool(self.frames))

    def done(self, code) -> None:   # chiude la fotocamera in ogni caso
        self.timer.stop()
        try:
            self.backend.close()
        except Exception:
            pass
        super().done(code)
