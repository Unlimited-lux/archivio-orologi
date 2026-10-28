"""Coordinate, griglia e riquadri di pagina (VIS-39, parte).

- barra di stato con X/Y del cursore e dimensioni della pagina nell'unità scelta
  (origine in alto a sinistra della pagina visualizzata; il motore applica già `/UserUnit`);
- griglia sopra la pagina, diradata automaticamente a zoom bassi;
- contorni dei riquadri di pagina (MediaBox, CropBox, BleedBox, TrimBox, ArtBox);
- latitudine e longitudine sotto il cursore nei PDF geospaziali (`geo.py`).
Griglia e riquadri sono solo a schermo: non compaiono in stampa né nel file.
"""

from __future__ import annotations

import pymupdf
from PySide6.QtCore import QEvent, QLineF, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QPen
from PySide6.QtWidgets import QLabel, QMenu

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations, theme

from .geo import format_latlon, geo_viewports, latlon_at

TRANSLATIONS = {
    "Coordinate e griglia": "Coordinates and grid",
    "Mostra le coordinate del cursore": "Show cursor coordinates",
    "Griglia": "Grid",
    "Riquadri di pagina": "Page boxes",
    "Unità di misura": "Units",
    "millimetri": "millimetres",
    "centimetri": "centimetres",
    "pollici": "inches",
    "punti": "points",
    "pica": "picas",
    "Passo della griglia…": "Grid spacing…",
    "Passo della griglia ({u}):": "Grid spacing ({u}):",
    "Riquadri: {legend}": "Boxes: {legend}",
    "Vista": "View",
}

UNITS = {          # punti per unità
    "mm": 72 / 25.4,
    "cm": 72 / 2.54,
    "in": 72.0,
    "pt": 1.0,
    "pc": 12.0,
}
UNIT_NAMES = {"mm": "millimetri", "cm": "centimetri", "in": "pollici", "pt": "punti", "pc": "pica"}
UNIT_LABEL = {"mm": "mm", "cm": "cm", "in": "in", "pt": "pt", "pc": "pc"}

BOXES = (  # (nome, attributo del motore, colore)
    ("MediaBox", "mediabox", QColor(120, 120, 120)),
    ("CropBox", "cropbox", QColor(37, 99, 235)),
    ("BleedBox", "bleedbox", QColor(220, 38, 38)),
    ("TrimBox", "trimbox", QColor(22, 163, 74)),
    ("ArtBox", "artbox", QColor(217, 119, 6)),
)


def fmt(value: float, unit: str) -> str:
    digits = 0 if unit == "pt" else (2 if unit == "in" else 1)
    text = f"{value:.{digits}f}"
    from foliant.api import language
    return text.replace(".", ",") if language() == "it" else text


def page_boxes(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect, QColor]]:
    """Riquadri distinti della pagina (coordinate pagina non ruotata, origine della MediaBox)."""
    out = []
    seen: list[pymupdf.Rect] = []
    for name, attr, color in BOXES:
        try:
            r = pymupdf.Rect(getattr(page, attr))
        except Exception:
            continue
        if r.is_empty:
            continue
        if any(all(abs(a - b) < 0.01 for a, b in zip(r, s)) for s in seen):
            continue
        seen.append(r)
        out.append((name, r, color))
    return out


class MeasureController(QObject):
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.ctx = ctx
        s = ctx.settings()
        self.unit = str(s.value("viewer/units", "mm") or "mm")
        if self.unit not in UNITS:
            self.unit = "mm"
        self.show_coords = s.value("viewer/show_coords", True) not in (False, "false")
        self.grid = s.value("viewer/grid", False) in (True, "true")
        try:
            self.grid_step = float(s.value("viewer/grid_step", 10) or 10)
        except (TypeError, ValueError):
            self.grid_step = 10.0
        self.boxes = False
        self._boxes_cache: dict[tuple, list] = {}
        self._geo_cache: dict = {}

        self.label = QLabel(ctx.window)
        self.label.setProperty("muted", True)
        self.label.hide()
        ctx.window.statusBar().addWidget(self.label)

        self.menu = QMenu(_("Coordinate e griglia"))
        self.act_coords = self._check(_("Mostra le coordinate del cursore"), self.show_coords, self.set_show_coords)
        self.act_grid = self._check(_("Griglia"), self.grid, self.set_grid)
        self.menu.addAction(_("Passo della griglia…"), self.ask_grid_step)
        self.act_boxes = self._check(_("Riquadri di pagina"), self.boxes, self.set_boxes)
        units = self.menu.addMenu(_("Unità di misura"))
        group = QActionGroup(units)
        self.unit_actions: dict[str, QAction] = {}
        for key in UNITS:
            a = units.addAction(_(UNIT_NAMES[key]))
            a.setCheckable(True)
            a.setChecked(key == self.unit)
            group.addAction(a)
            a.triggered.connect(lambda _c=False, k=key: self.set_unit(k))
            self.unit_actions[key] = a
        self.menu_action = QAction(_("Coordinate e griglia"), ctx.window)
        from foliant.api import bind_icon
        bind_icon(self.menu_action, "ruler")
        self.menu_action.setMenu(self.menu)
        ctx.add_menu_action(self.menu_action, _("Vista"))
        ctx.documentOpened.connect(self._attach)
        ctx.documentChanged.connect(lambda _v: (self._boxes_cache.clear(), self._geo_cache.clear()))
        ctx.currentViewChanged.connect(lambda v: self.label.hide() if v is None else None)
        for v in ctx.views():
            self._attach(v)

    def _check(self, text, value, slot) -> QAction:
        a = self.menu.addAction(text)
        a.setCheckable(True)
        a.setChecked(value)
        a.toggled.connect(slot)
        return a

    # ------------------------------------------------------------- impostazioni
    def set_unit(self, unit: str) -> None:
        if unit in UNITS:
            self.unit = unit
            self.ctx.settings().setValue("viewer/units", unit)
            self.unit_actions[unit].setChecked(True)
            self._repaint()

    def set_show_coords(self, on: bool) -> None:
        self.show_coords = bool(on)
        self.ctx.settings().setValue("viewer/show_coords", self.show_coords)
        if not on:
            self.label.hide()

    def set_grid(self, on: bool) -> None:
        self.grid = bool(on)
        self.ctx.settings().setValue("viewer/grid", self.grid)
        if self.act_grid.isChecked() != self.grid:
            self.act_grid.setChecked(self.grid)
        self._repaint()

    def set_grid_step(self, step: float) -> None:
        if step > 0:
            self.grid_step = float(step)
            self.ctx.settings().setValue("viewer/grid_step", self.grid_step)
            self._repaint()

    def ask_grid_step(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        value, ok = QInputDialog.getDouble(self.ctx.window, _("Griglia"),
                                           _("Passo della griglia ({u}):").format(u=UNIT_LABEL[self.unit]),
                                           self.grid_step, 0.1, 10000, 1)
        if ok:
            self.set_grid_step(value)

    def set_boxes(self, on: bool) -> None:
        self.boxes = bool(on)
        if self.act_boxes.isChecked() != self.boxes:
            self.act_boxes.setChecked(self.boxes)
        self._repaint()
        view = self.ctx.current_view()
        if on and view is not None:
            legend = ", ".join(n for n, _r, _c in self.page_boxes(view, view.current_page))
            self.ctx.status(_("Riquadri: {legend}").format(legend=legend), 8000)

    def _repaint(self) -> None:
        for v in self.ctx.views():
            v.viewport().update()

    # ----------------------------------------------------------- coordinate
    def coordinates(self, view: DocumentView, pos: QPointF) -> tuple[int, float, float, float, float] | None:
        """(pagina, x, y, larghezza, altezza) nell'unità scelta, nello spazio visualizzato."""
        for page in view.visible_pages():
            r = view.page_rect(page)
            if r.contains(pos):
                w_pt, h_pt = view.page_size(page)
                scale = r.width() / w_pt if w_pt else 1.0
                k = 1.0 / UNITS[self.unit]   # il motore applica già /UserUnit alle dimensioni
                x = (pos.x() - r.x()) / scale * k
                y = (pos.y() - r.y()) / scale * k
                return page, x, y, w_pt * k, h_pt * k
        return None

    def describe(self, view: DocumentView, pos: QPointF) -> str:
        c = self.coordinates(view, pos)
        if c is None:
            return ""
        _p, x, y, w, h = c
        u = UNIT_LABEL[self.unit]
        text = f"X {fmt(x, self.unit)}  Y {fmt(y, self.unit)} {u}  ·  {fmt(w, self.unit)} × {fmt(h, self.unit)} {u}"
        geo = self.latlon(view, pos)
        if geo is not None:
            from foliant.api import language
            g = format_latlon(*geo)
            text += "  ·  " + (g.replace(".", ",") if language() == "it" else g)
        return text

    def latlon(self, view: DocumentView, pos: QPointF) -> tuple[float, float] | None:
        """Latitudine e longitudine sotto il cursore, nei PDF geospaziali (`/VP` con `/GEO`)."""
        hit = view.view_to_page(pos)
        if hit is None:
            return None
        page, point = hit
        key = (id(view), page, view.document.version)
        if key not in self._geo_cache:
            with ENGINE_LOCK:
                try:
                    self._geo_cache[key] = geo_viewports(view.document.pdf, page)
                except Exception:
                    self._geo_cache[key] = []
        vps = self._geo_cache[key]
        if not vps:
            return None
        with ENGINE_LOCK:
            return latlon_at(view.document.pdf, page, point, vps)

    def _attach(self, view: DocumentView) -> None:
        view.viewport().installEventFilter(self)
        if self.paint not in view.overlays:
            view.overlays.append(self.paint)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.MouseMove and self.show_coords:
            view = obj.parent()
            if isinstance(view, DocumentView) and view is self.ctx.current_view() and not view.document.closed:
                text = self.describe(view, ev.position())
                self.label.setText(text)
                self.label.setVisible(bool(text))
        elif ev.type() == QEvent.Type.Leave:
            self.label.hide()
        return False

    # --------------------------------------------------------------- disegno
    def page_boxes(self, view: DocumentView, page: int) -> list:
        key = (id(view), page, view.document.version)
        if key not in self._boxes_cache:
            with ENGINE_LOCK:
                try:
                    self._boxes_cache[key] = page_boxes(view.document.pdf[page])
                except Exception:
                    self._boxes_cache[key] = []
        return self._boxes_cache[key]

    def paint(self, view: DocumentView, p, page: int) -> None:
        if not (self.grid or self.boxes):
            return
        w, h = view.page_size(page)
        inv, _ok = p.worldTransform().inverted()
        area = inv.mapRect(view.page_rect(page))
        scale = view.page_rect(page).width() / w if w else 1.0
        if self.grid:
            step = self.grid_step * UNITS[self.unit]
            while step * scale < 8:          # griglia troppo fitta: diradata
                step *= 2
            c = theme.color("accent")
            c.setAlpha(60)
            pen = QPen(c)
            pen.setCosmetic(True)
            pen.setWidthF(1.0)
            p.setPen(pen)
            x = area.left()
            n = 0
            while x <= area.right() and n < 4000:
                p.drawLine(QLineF(x, area.top(), x, area.bottom()))
                x += step
                n += 1
            y = area.top()
            while y <= area.bottom() and n < 8000:
                p.drawLine(QLineF(area.left(), y, area.right(), y))
                y += step
                n += 1
        if self.boxes:
            for _name, r, color in self.page_boxes(view, page):
                pen = QPen(color)
                pen.setCosmetic(True)
                pen.setWidthF(1.5)
                pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(r.x0, r.y0, r.width, r.height))


def install(ctx: Context) -> MeasureController:
    add_translations("en", TRANSLATIONS)
    return MeasureController(ctx)
