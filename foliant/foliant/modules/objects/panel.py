"""Pannello proprietà degli oggetti (OBJ-03) e controllo dell'opacità (OBJ-16)."""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, theme

from . import engine as E
from .content import IMAGE, PATH


def object_skew(obj) -> float:
    """Inclinazione (gradi): scostamento dall'angolo retto tra i lati dell'oggetto."""
    import math
    q = obj.quad
    ux, uy = q.ur.x - q.ul.x, q.ur.y - q.ul.y
    vx, vy = q.ll.x - q.ul.x, q.ll.y - q.ul.y
    nu, nv = math.hypot(ux, uy), math.hypot(vx, vy)
    if nu == 0 or nv == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (ux * vx + uy * vy) / (nu * nv)))
    val = 90.0 - math.degrees(math.acos(cos))
    return 0.0 if abs(val) < 0.05 else val


def _spin(lo: float, hi: float, suffix: str = " pt", decimals: int = 2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class ObjectPanel(QWidget):
    """Tipo, posizione, dimensioni, angolo, opacità e dati dell'immagine."""

    def __init__(self, ctx, controller):
        super().__init__()
        self.ctx = ctx
        self.c = controller
        self.view = None
        self._updating = False
        self.setMinimumWidth(240)
        lay = QVBoxLayout(self)
        self.title = QLabel(_("Nessun oggetto selezionato"))
        self.title.setStyleSheet("font-weight: 600;")
        lay.addWidget(self.title)
        form = QFormLayout()
        self.x = _spin(-100000, 100000)
        self.y = _spin(-100000, 100000)
        self.w = _spin(1, 100000)
        self.h = _spin(1, 100000)
        self.lock = QCheckBox(_("Mantieni le proporzioni"))
        self.lock.setChecked(True)
        self.angle = _spin(-180, 180, " °", 1)
        self.opacity = QSpinBox()
        self.opacity.setRange(-1, 100)
        self.opacity.setSuffix(" %")
        self.opacity.setKeyboardTracking(False)
        self.opacity.setSpecialValueText(_("misto"))
        # punto di riferimento per X/Y e per il ridimensionamento (griglia 3×3)
        from PySide6.QtWidgets import QButtonGroup, QGridLayout, QToolButton
        self.ref = (0.0, 0.0)
        refw = QWidget()
        grid = QGridLayout(refw)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.ref_group = QButtonGroup(self)
        self.ref_buttons = {}
        for row in range(3):
            for col in range(3):
                b = QToolButton()
                b.setCheckable(True)
                b.setFixedSize(16, 16)
                fx, fy = col / 2, row / 2
                b.setToolTip(_("Punto di riferimento"))
                b.setAccessibleName(_("Punto di riferimento {c},{r}").format(c=col + 1, r=row + 1))
                b.clicked.connect(lambda _c=False, f=(fx, fy): self.set_reference(f))
                self.ref_group.addButton(b)
                self.ref_buttons[(fx, fy)] = b
                grid.addWidget(b, row, col)
        self.ref_buttons[(0.0, 0.0)].setChecked(True)
        refw.setFixedSize(3 * 16 + 2 * 2, 3 * 16 + 2 * 2)
        self._style_reference()
        theme.on_change(self._style_reference)
        form.addRow(_("Riferimento:"), refw)
        form.addRow("X:", self.x)
        form.addRow("Y:", self.y)
        form.addRow(_("Larghezza:"), self.w)
        form.addRow(_("Altezza:"), self.h)
        form.addRow("", self.lock)
        form.addRow(_("Angolo:"), self.angle)
        self.skew = QLabel("0 °")
        form.addRow(_("Inclinazione:"), self.skew)
        form.addRow(_("Opacità:"), self.opacity)
        lay.addLayout(form)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.details)
        lay.addStretch(1)
        self.x.editingFinished.connect(self._apply_pos)
        self.y.editingFinished.connect(self._apply_pos)
        self.w.editingFinished.connect(lambda: self._apply_size("w"))
        self.h.editingFinished.connect(lambda: self._apply_size("h"))
        self.angle.editingFinished.connect(self._apply_angle)
        self.opacity.editingFinished.connect(self._apply_opacity)
        self.refresh()

    def _style_reference(self) -> None:
        sheet = (f"QToolButton {{ border: 1px solid {theme.hex_color('border')}; border-radius: 3px;"
                 f" background: {theme.hex_color('surface')}; }}"
                 f"QToolButton:checked {{ background: {theme.hex_color('accent')};"
                 f" border-color: {theme.hex_color('accent')}; }}")
        for b in self.ref_buttons.values():
            b.setStyleSheet(sheet)

    def set_reference(self, ref: tuple) -> None:
        self.ref = (float(ref[0]), float(ref[1]))
        b = self.ref_buttons.get(self.ref)
        if b is not None and not b.isChecked():
            b.setChecked(True)
        self.refresh()

    def set_view(self, view) -> None:
        self.view = view
        self.refresh()

    def _objs(self):
        view = self.ctx.current_view()
        return view, (self.c.selected_objects(view) if view is not None else [])

    def _view_rect(self, view, objs) -> pymupdf.Rect:
        with ENGINE_LOCK:
            page = view.document.pdf[self.c.sel_page]
            r = E.union_rect(objs) * page.rotation_matrix
        r.normalize()
        return r

    def refresh(self) -> None:
        view, objs = self._objs()
        self._updating = True
        try:
            enabled = bool(objs)
            for w in (self.x, self.y, self.w, self.h, self.angle, self.opacity, self.lock):
                w.setEnabled(enabled)
            if not objs:
                self.title.setText(_("Nessun oggetto selezionato"))
                self.details.setText("")
                return
            if len(objs) == 1:
                self.title.setText(_(E.kind_label(objs[0].kind)))
            else:
                self.title.setText(_("{n} oggetti selezionati").format(n=len(objs)))
            r = self._view_rect(view, objs)
            fx, fy = self.ref
            self.x.setValue(r.x0 + fx * r.width)
            self.y.setValue(r.y0 + fy * r.height)
            self.w.setValue(max(1, r.width))
            self.h.setValue(max(1, r.height))
            self.angle.setEnabled(len(objs) == 1)
            self.angle.setValue(E.object_angle(objs[0]) if len(objs) == 1 else 0)
            self.skew.setText(f"{object_skew(objs[0]):.1f} °" if len(objs) == 1 else "—")
            alphas = {round(o.opacity * 100) for o in objs}
            self.opacity.setValue(next(iter(alphas)) if len(alphas) == 1 else -1)  # -1 = «misto»
            self.details.setText(self._details(view, objs))
        finally:
            self._updating = False

    def _details(self, view, objs) -> str:
        if len(objs) != 1:
            return ""
        o = objs[0]
        lines = []
        if o.kind == IMAGE:
            with ENGINE_LOCK:
                info = E.image_info(view.document.pdf, o)
            if "px" in info:
                lines.append(_("Pixel: {w} × {h}").format(w=info["px"][0], h=info["px"][1]))
                px, py = info["ppi"]
                lines.append(_("Risoluzione: {x} × {y} ppi").format(x=round(px), y=round(py)))
            if info.get("colorspace") and info["colorspace"] != "null":
                lines.append(_("Spazio colore: {c}").format(c=info["colorspace"][:60]))
            if info.get("filter") and info["filter"] != "null":
                lines.append(_("Compressione: {f}").format(f=info["filter"]))
            if info.get("bpc"):
                lines.append(_("Bit per componente: {b}").format(b=info["bpc"]))
            if info.get("alpha"):
                lines.append(_("Trasparenza: sì"))
            if info.get("bytes"):
                lines.append(_("Peso dei dati: {k} KB").format(k=round(info["bytes"] / 1024, 1)))
            if o.inline:
                lines.append(_("Immagine inline"))
        elif o.kind == PATH:
            if o.stroke_color is not None and o.paint in ("S", "s", "B", "B*", "b", "b*"):
                lines.append(_("Tratto: {c}").format(c=", ".join(f"{v:.2f}" for v in o.stroke_color)))
                lines.append(_("Spessore: {w} pt").format(w=round(o.state.line_width, 2)))
            if o.fill_color is not None and o.paint not in ("S", "s"):
                lines.append(_("Riempimento: {c}").format(c=", ".join(f"{v:.2f}" for v in o.fill_color)))
        return "\n".join(lines)

    # ---------------------------------------------------------------- applica
    def _apply_rect(self, new: pymupdf.Rect) -> None:
        view, objs = self._objs()
        if not objs:
            return
        with ENGINE_LOCK:
            page = view.document.pdf[self.c.sel_page]
            derot = page.derotation_matrix
        page_rect = new * derot
        page_rect.normalize()
        old = E.union_rect(objs)
        if abs(page_rect.x0 - old.x0) < 1e-3 and abs(page_rect.y0 - old.y0) < 1e-3 \
                and abs(page_rect.width - old.width) < 1e-3 and abs(page_rect.height - old.height) < 1e-3:
            return
        self.c.resize_to(view, page_rect)

    def _apply_pos(self) -> None:
        if self._updating:
            return
        view, objs = self._objs()
        if not objs:
            return
        r = self._view_rect(view, objs)
        fx, fy = self.ref
        dx = self.x.value() - (r.x0 + fx * r.width)
        dy = self.y.value() - (r.y0 + fy * r.height)
        if abs(dx) < 1e-3 and abs(dy) < 1e-3:
            return
        self._translate_view(view, dx, dy)

    def _translate_view(self, view, dx, dy) -> None:
        with ENGINE_LOCK:
            page = view.document.pdf[self.c.sel_page]
            pdx, pdy = E.view_vector_to_page(page, dx, dy)
        self.c.translate(view, pdx, pdy)

    def _apply_size(self, which: str) -> None:
        if self._updating:
            return
        view, objs = self._objs()
        if not objs:
            return
        r = self._view_rect(view, objs)
        w, h = self.w.value(), self.h.value()
        if self.lock.isChecked() and r.width > 0 and r.height > 0:
            if which == "w":
                h = r.height * w / r.width
            else:
                w = r.width * h / r.height
        if w < E.MIN_SIZE or h < E.MIN_SIZE:
            self.refresh()
            return
        fx, fy = self.ref
        ax, ay = r.x0 + fx * r.width, r.y0 + fy * r.height   # il punto di riferimento resta fermo
        x0, y0 = ax - fx * w, ay - fy * h
        self._apply_rect(pymupdf.Rect(x0, y0, x0 + w, y0 + h))

    def _apply_angle(self) -> None:
        if self._updating:
            return
        view, objs = self._objs()
        if len(objs) != 1:
            return
        delta = self.angle.value() - E.object_angle(objs[0])
        if abs(delta) > 1e-6:
            self.c.rotate(view, delta)

    def _apply_opacity(self) -> None:
        if self._updating:
            return
        view, objs = self._objs()
        if objs and self.opacity.value() >= 0:
            self.c.set_opacity(view, self.opacity.value() / 100.0)


def add_opacity_widget(ctx, controller, mode) -> None:
    """Cursore dell'opacità nella barra contestuale: un passo annullabile al rilascio."""
    from PySide6.QtWidgets import QSizePolicy
    box = QWidget()
    box.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    lay = QHBoxLayout(box)
    lay.setContentsMargins(6, 0, 6, 0)
    label = QLabel(_("Opacità"))
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setValue(100)
    slider.setFixedWidth(90)
    slider.setToolTip(_("Opacità degli oggetti selezionati"))
    value = QLabel("100 %")
    value.setMinimumWidth(42)
    lay.addWidget(label)
    lay.addWidget(slider)
    lay.addWidget(value)
    slider.valueChanged.connect(lambda v: value.setText(f"{v} %"))

    def commit():
        view = ctx.current_view()
        if view is not None and controller.selection:
            controller.set_opacity(view, slider.value() / 100.0)
    slider.sliderReleased.connect(commit)

    def sync():
        view = ctx.current_view()
        objs = controller.selected_objects(view) if view is not None else []
        box.setEnabled(bool(objs))
        if objs and not slider.isSliderDown():
            alphas = {round(o.opacity * 100) for o in objs}
            slider.blockSignals(True)
            slider.setValue(alphas.pop() if len(alphas) == 1 else 100)
            slider.blockSignals(False)
            value.setText(f"{slider.value()} %" if len(alphas) <= 1 else _("misto"))
    controller.selectionChanged.connect(sync)
    sync()
    mode.add_widget(box)
    controller._opacity_slider = slider
