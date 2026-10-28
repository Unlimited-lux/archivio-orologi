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

    def __init__(self, parent, paths: list[str], ask_more=None):
        super().__init__(parent)
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
        self.size_box = _combo([(_("Come l'immagine"), None)] + [(s, s) for s in STANDARD_SIZES], "A4")
        form.addRow(_("Formato pagina"), self.size_box)
        self.orient_box = _combo([(_("Automatico"), "auto"), (_("Verticale"), "portrait"),
                                  (_("Orizzontale"), "landscape")])
        form.addRow(_("Orientamento"), self.orient_box)
        self.fit_box = _combo([(_("Contieni (proporzioni mantenute)"), "contain"), (_("Riempi ritagliando"), "fill"),
                               (_("Dimensione reale centrata"), "actual")])
        form.addRow(_("Adattamento"), self.fit_box)
        self.margin = QDoubleSpinBox()
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
            "margin_pt": self.margin.value() * 72 / 25.4,
            "quality": self.quality_box.currentData(),
            "max_ppi": self.ppi.value() if self.quality_box.currentData() != "original" else None,
            "rotations": rotations or None,
        }


class BlankDialog(QDialog):
    """Nuovo PDF vuoto: formato, orientamento, pagine, colore di fondo e stile carta."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Nuovo PDF vuoto"))
        self.setMinimumWidth(420)
        self.background = QColor(255, 255, 255)
        self.line_color = QColor(191, 204, 217)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.size_box = _combo([(s, s) for s in ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "B4", "B5", "Letter",
                                                 "Legal", "Tabloid"]] + [(_("Personalizzato"), "custom")], "A4")
        form.addRow(_("Formato"), self.size_box)
        custom = QHBoxLayout()
        self.w = QDoubleSpinBox()
        self.h = QDoubleSpinBox()
        for s in (self.w, self.h):
            s.setRange(0.1, 100000)
            s.setDecimals(1)
        self.w.setValue(210)
        self.h.setValue(297)
        self.unit = _combo([("mm", "mm"), (_("pollici"), "in"), (_("punti"), "pt")])
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
                "step_mm": self.step.value(), "line_color": (lc.redF(), lc.greenF(), lc.blueF())}

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




class ScannerDialog(QDialog):
    """Impostazioni di acquisizione (CNV-24): dispositivo, sorgente, risoluzione, colore, opzioni."""

    def __init__(self, parent, devices: list[tuple[str, str]], *, has_document: bool = False, refresh=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox
        self.setWindowTitle(_("Crea PDF da scanner"))
        self.setMinimumWidth(460)
        self._refresh = refresh
        lay = QVBoxLayout(self)
        form = QFormLayout()
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
        self.remove_blank = QCheckBox(_("Rimuovi le pagine vuote"))
        self.manual_duplex = QCheckBox(_("Fronte/retro manuale (acquisisci prima i fronti, poi i retro)"))
        self.id_card = QCheckBox(_("Documento d'identità: fronte e retro sulla stessa pagina A4"))
        self.insert = QCheckBox(_("Inserisci nel documento aperto dopo la pagina corrente"))
        self.insert.setVisible(has_document)
        for cb in (self.remove_blank, self.manual_duplex, self.id_card, self.insert):
            lay.addWidget(cb)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.ok_button.setText(_("Acquisisci"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.set_devices(devices)

    def set_devices(self, devices) -> None:
        self.device.clear()
        for dev, label in devices:
            self.device.addItem(label or dev, dev)
        self.ok_button.setEnabled(bool(devices))

    def _reload(self) -> None:
        if self._refresh is not None:
            self.set_devices(self._refresh())

    def settings(self):
        from .scanner import ScanSettings
        return ScanSettings(source=self.source.currentData(), dpi=self.dpi.currentData(), mode=self.mode.currentData(),
                            brightness=self.brightness.value(), contrast=self.contrast.value(),
                            jpeg_quality=self.quality.value())
