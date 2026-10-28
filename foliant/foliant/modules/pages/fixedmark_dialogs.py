"""Dialogo della filigrana a posizione fissa di stampa (PAG-37)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QGroupBox, QLineEdit, QPushButton,
)

import pymupdf

from foliant.api import ENGINE_LOCK, _

from . import fixedmark, layout
from .layout_dialogs import _ANCHOR_ORDER, _LayoutDialog, _guard, _muted, _parent, _set_combo, _spin, anchor_label

TRANSLATIONS = {
    "Filigrana di stampa": "Print watermark",
    "Filigrana di stampa a posizione fissa": "Fixed-position print watermark",
    "Testo": "Text",
    "Dimensione carattere": "Font size",
    "Colore": "Color",
    "Opacità": "Opacity",
    "Rotazione": "Rotation",
    "Posizione": "Position",
    "Margine": "Margin",
    "Posizione esplicita sul foglio (H, V)": "Explicit position on the sheet (H, V)",
    "H (frazione della larghezza)": "H (fraction of the width)",
    "V (frazione dell'altezza)": "V (fraction of the height)",
    "Rimuovi le filigrane di stampa esistenti": "Remove existing print watermarks",
    "Scegli il colore": "Choose the color",
    "La filigrana mantiene posizione e dimensione rispetto al foglio stampato, a qualunque scala; "
    "resta un'annotazione rimovibile.":
        "The watermark keeps its position and size relative to the printed sheet at any scale; "
        "it remains a removable annotation.",
    "Impossibile aggiungere la filigrana": "Cannot add the watermark",
    "Rimuovi filigrane di stampa": "Remove print watermarks",
    "Filigrane rimosse: {n}.": "Watermarks removed: {n}.",
    "COPIA": "COPY",
    "Il testo della filigrana è vuoto.": "The watermark text is empty.",
}


class FixedWatermarkDialog(_LayoutDialog):
    TITLE = "Filigrana di stampa a posizione fissa"
    ICON = "stamp"

    def build(self) -> None:
        box = QGroupBox(_("Filigrana di stampa"))
        f = QFormLayout(box)
        self.text = QLineEdit(_("COPIA"))
        self.size = _spin(36, 4, 400, 0, "pt")
        self.color = QColor(204, 26, 26)
        self.color_btn = QPushButton()
        self.color_btn.setToolTip(_("Scegli il colore"))
        self.color_btn.clicked.connect(self._pick_color)
        self.opacity = _spin(50, 5, 100, 0, "%")
        self.rotation = _spin(0, -180, 180, 0, "°", 15)
        self.anchor = QComboBox()
        for key in _ANCHOR_ORDER:
            self.anchor.addItem(anchor_label(key), key)
        _set_combo(self.anchor, "top-right")
        self.margin = _spin(10, 0, 200, 1, "mm")
        self.explicit = QCheckBox(_("Posizione esplicita sul foglio (H, V)"))
        self.h = _spin(0.9, 0, 1, 3, "", 0.05)
        self.v = _spin(0.9, 0, 1, 3, "", 0.05)
        self.remove = QCheckBox(_("Rimuovi le filigrane di stampa esistenti"))
        f.addRow(_("Testo"), self.text)
        f.addRow(_("Dimensione carattere"), self.size)
        f.addRow(_("Colore"), self.color_btn)
        f.addRow(_("Opacità"), self.opacity)
        f.addRow(_("Rotazione"), self.rotation)
        f.addRow(_("Posizione"), self.anchor)
        f.addRow(_("Margine"), self.margin)
        f.addRow(self.explicit)
        f.addRow(_("H (frazione della larghezza)"), self.h)
        f.addRow(_("V (frazione dell'altezza)"), self.v)
        f.addRow(self.remove)
        self.form.addWidget(box)
        self.form.addWidget(_muted(_("La filigrana mantiene posizione e dimensione rispetto al foglio stampato, a "
                                     "qualunque scala; resta un'annotazione rimovibile.")))
        self._connect(self.text, self.size, self.opacity, self.rotation, self.anchor, self.margin, self.explicit,
                      self.h, self.v, self.remove)
        self._rect = None
        self._update_color()

    def _update_color(self) -> None:
        self.color_btn.setStyleSheet(f"background: {self.color.name()}; min-width: 48px;")
        self.schedule()

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self.color, self, _("Scegli il colore"))
        if c.isValid():
            self.color = c
            self._update_color()

    def options(self) -> dict:
        explicit = self.explicit.isChecked()
        return {
            "pages": self.selected_pages(),
            "text": self.text.text(),
            "fontsize": self.size.value(),
            "color": (self.color.redF(), self.color.greenF(), self.color.blueF()),
            "opacity": self.opacity.value() / 100,
            "rotation": self.rotation.value(),
            "anchor": self.anchor.currentData(),
            "margin": self.margin.value() * layout.MM,
            "h": self.h.value() if explicit else None,
            "v": self.v.value() if explicit else None,
            "remove": self.remove.isChecked(),
        }

    def validate(self) -> str | None:
        o = self.options()
        explicit = o["h"] is not None
        remove = o["remove"]
        for w in (self.text, self.size, self.color_btn, self.opacity, self.rotation, self.explicit):
            w.setEnabled(not remove)
        self.anchor.setEnabled(not explicit and not remove)
        self.margin.setEnabled(not explicit and not remove)
        self.h.setEnabled(explicit and not remove)
        self.v.setEnabled(explicit and not remove)
        self._rect = None
        if remove:
            return None
        if not o["text"].strip():
            return _("Il testo della filigrana è vuoto.")
        # anteprima: posizione calcolata su una copia della pagina
        idx = self.preview_index()
        with ENGINE_LOCK:
            pdf = self.document.pdf
            tmp = pymupdf.open()
            tmp.insert_pdf(pdf, from_page=idx, to_page=idx, annots=False)
        fixedmark.add_fixed_watermark(tmp, [0], o["text"], fontsize=o["fontsize"], color=o["color"],
                                      opacity=o["opacity"], rotation=o["rotation"], anchor=o["anchor"],
                                      margin=o["margin"], h=o["h"], v=o["v"])
        page = tmp[0]
        annot = page.first_annot
        self._rect = annot.rect * page.rotation_matrix
        self._angle = (o["rotation"]) % 360
        tmp.close()
        return None

    def paint_overlay(self, p: QPainter, s: float) -> None:
        if self._rect is None:
            return
        o = self.options()
        r = self._rect
        q = QRectF(r.x0, r.y0, r.width, r.height)
        p.setPen(QPen(QColor(self.color), 1 / s, Qt.PenStyle.DashLine))
        p.drawRect(q)
        p.save()
        c = QColor(self.color)
        c.setAlphaF(o["opacity"])
        p.setPen(c)
        font = QFont("Helvetica")
        font.setPixelSize(max(1, int(o["fontsize"])))
        p.setFont(font)
        p.translate(q.center())
        p.rotate(-self._angle)
        tw = p.fontMetrics().horizontalAdvance(o["text"])
        p.drawText(QPointF(-tw / 2, o["fontsize"] * 0.35), o["text"])
        p.restore()


def open_fixed_watermark_dialog(ctx, view, selected: list[int]) -> bool:
    """PAG-37: aggiunge (o rimuove) la filigrana a posizione fissa di stampa."""
    if view is None:
        return False
    dlg = FixedWatermarkDialog(_parent(ctx, view), view.document, selected, view.current_page)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return False
    o = dlg.options()

    def run() -> bool:
        if o["remove"]:
            with view.document.change(_("Rimuovi filigrane di stampa")) as pdf:
                n = fixedmark.remove_fixed_watermarks(pdf, o["pages"])
            ctx.info(_("Filigrana di stampa"), _("Filigrane rimosse: {n}.").format(n=n))
            return n > 0
        with view.document.change(_("Filigrana di stampa")) as pdf:
            fixedmark.add_fixed_watermark(pdf, o["pages"], o["text"], fontsize=o["fontsize"], color=o["color"],
                                          opacity=o["opacity"], rotation=o["rotation"], anchor=o["anchor"],
                                          margin=o["margin"], h=o["h"], v=o["v"])
        return True

    return _guard(ctx, _("Impossibile aggiungere la filigrana"), run)

