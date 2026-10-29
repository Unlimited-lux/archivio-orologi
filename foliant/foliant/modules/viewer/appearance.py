"""Temi di lettura e protezione degli occhi (VIS-34).

La trasformazione è solo a schermo: un livello di disegno della vista
ricolora testo e grafica con le modalità di composizione di QPainter
(colore = testo + (sfondo − testo) × luminosità originale); le immagini
vengono poi ridisegnate con i pixel originali, così fotografie e (salvo
opzione) scansioni restano invariate. File, stampa, esportazioni e
istantanee usano sempre i colori originali.
"""

from __future__ import annotations

from collections import OrderedDict

import pymupdf
from PySide6.QtCore import QRectF
from PySide6.QtGui import QAction, QActionGroup, QColor, QPainter
from PySide6.QtWidgets import QColorDialog, QMenu

from foliant.api import ENGINE_LOCK, Context, DocumentView, _, add_translations

from .ribbon_place import place

TRANSLATIONS = {
    "Tema di lettura": "Reading theme",
    "Bianco (originale)": "White (original)",
    "Carta": "Paper",
    "Verde menta": "Mint green",
    "Blu notte": "Night blue",
    "Scuro (colori invertiti)": "Dark (inverted colors)",
    "Alto contrasto": "High contrast",
    "Personalizzato…": "Custom…",
    "Non alterare le immagini": "Do not alter images",
    "Applica anche alle scansioni": "Apply to scans too",
    "Filtro a tono caldo": "Warm tone filter",
    "Colore del testo": "Text color",
    "Colore di sfondo della pagina": "Page background color",
    "Contrasto insufficiente ({r}:1): il testo potrebbe essere poco leggibile.":
        "Insufficient contrast ({r}:1): text may be hard to read.",
    "Tema": "Theme",
}

# (testo, sfondo) per ciascun tema
PRESETS: "OrderedDict[str, tuple[str, tuple[int, int, int] | None, tuple[int, int, int] | None]]" = OrderedDict([
    ("white", ("Bianco (originale)", None, None)),
    ("paper", ("Carta", (40, 32, 20), (246, 238, 220))),
    ("mint", ("Verde menta", (20, 40, 28), (214, 236, 218))),
    ("night", ("Blu notte", (214, 222, 236), (22, 32, 56))),
    ("dark", ("Scuro (colori invertiti)", (230, 230, 230), (24, 24, 24))),
    ("contrast", ("Alto contrasto", (255, 255, 0), (0, 0, 0))),
])
WARM = QColor(255, 236, 200)
SCAN_COVERAGE = 0.6


def _lum(c: tuple[int, int, int]) -> float:
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def paint_recolor(p: QPainter, rect: QRectF, text: tuple, bg: tuple) -> None:
    """Ricolora la zona già disegnata: nero → `text`, bianco → `bg` (canale per canale)."""
    light_bg = _lum(bg) >= _lum(text)
    if not light_bg:
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
        p.fillRect(rect, QColor(255, 255, 255))          # inversione
        lo, hi = bg, text
    else:
        lo, hi = text, bg
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
    p.fillRect(rect, QColor(*[max(0, h - l) for h, l in zip(hi, lo)]))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    p.fillRect(rect, QColor(*[min(h, l) for h, l in zip(hi, lo)]))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


class AppearanceController:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        s = ctx.settings()
        self.theme = str(s.value("viewer/reading_theme", "white") or "white")
        self.keep_images = s.value("viewer/reading_keep_images", True) not in (False, "false")
        self.scans_too = s.value("viewer/reading_scans_too", False) in (True, "true")
        self.warm = s.value("viewer/reading_warm", False) in (True, "true")
        custom_t = s.value("viewer/reading_custom_text", "#202020")
        custom_b = s.value("viewer/reading_custom_bg", "#f4f1e8")
        self.custom = (QColor(custom_t).getRgb()[:3], QColor(custom_b).getRgb()[:3])
        self._images: dict[tuple, list] = {}

        self.menu = QMenu(_("Tema di lettura"))
        self.group = QActionGroup(self.menu)
        self.theme_actions: dict[str, QAction] = {}
        for key, (label, _t, _b) in PRESETS.items():
            act = self.menu.addAction(_(label))
            act.setCheckable(True)
            self.group.addAction(act)
            act.triggered.connect(lambda _c=False, k=key: self.set_theme(k))
            self.theme_actions[key] = act
        act = self.menu.addAction(_("Personalizzato…"))
        act.setCheckable(True)
        self.group.addAction(act)
        act.triggered.connect(self.choose_custom)
        self.theme_actions["custom"] = act
        self.menu.addSeparator()
        self.act_keep = self._option(_("Non alterare le immagini"), self.keep_images, "keep_images")
        self.act_scans = self._option(_("Applica anche alle scansioni"), self.scans_too, "scans_too")
        self.act_warm = self._option(_("Filtro a tono caldo"), self.warm, "warm")
        self.theme_actions.get(self.theme, self.theme_actions["white"]).setChecked(True)
        self.menu_action = QAction(_("Tema di lettura"), ctx.window)
        from foliant.api import bind_icon
        bind_icon(self.menu_action, "palette")
        self.menu_action.setMenu(self.menu)
        place(ctx, "visualizza", self.menu_action, "lettura", 20, icon_text=_("Tema"))   # pulsante ▾
        ctx.documentOpened.connect(self._attach)
        ctx.documentChanged.connect(lambda v: self._images.clear())
        for v in ctx.views():
            self._attach(v)

    def _option(self, text: str, value: bool, attr: str) -> QAction:
        act = self.menu.addAction(text)
        act.setCheckable(True)
        act.setChecked(value)
        act.toggled.connect(lambda on, a=attr: self.set_option(a, on))
        return act

    # ---------------------------------------------------------------- impostazioni
    def colors(self) -> tuple | None:
        if self.theme == "custom":
            return self.custom
        preset = PRESETS.get(self.theme)
        if preset is None or preset[1] is None:
            return None
        return preset[1], preset[2]

    def set_theme(self, key: str) -> None:
        self.theme = key if (key in PRESETS or key == "custom") else "white"
        self.ctx.settings().setValue("viewer/reading_theme", self.theme)
        act = self.theme_actions.get(self.theme)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self._update_views()

    def set_option(self, attr: str, on: bool) -> None:
        setattr(self, attr, bool(on))
        key = {"keep_images": "reading_keep_images", "scans_too": "reading_scans_too", "warm": "reading_warm"}[attr]
        self.ctx.settings().setValue(f"viewer/{key}", bool(on))
        self._update_views()

    def set_custom(self, text: tuple, bg: tuple) -> float:
        self.custom = (tuple(text[:3]), tuple(bg[:3]))
        s = self.ctx.settings()
        s.setValue("viewer/reading_custom_text", QColor(*self.custom[0]).name())
        s.setValue("viewer/reading_custom_bg", QColor(*self.custom[1]).name())
        ratio = contrast_ratio(*self.custom)
        if ratio < 4.5:
            self.ctx.status(_("Contrasto insufficiente ({r}:1): il testo potrebbe essere poco leggibile.")
                            .format(r=f"{ratio:.1f}"), 8000)
        self.set_theme("custom")
        return ratio

    def choose_custom(self) -> None:
        w = self.ctx.window
        t = QColorDialog.getColor(QColor(*self.custom[0]), w, _("Colore del testo"))
        if not t.isValid():
            self.theme_actions.get(self.theme, self.theme_actions["white"]).setChecked(True)
            return
        b = QColorDialog.getColor(QColor(*self.custom[1]), w, _("Colore di sfondo della pagina"))
        if not b.isValid():
            self.theme_actions.get(self.theme, self.theme_actions["white"]).setChecked(True)
            return
        self.set_custom(t.getRgb()[:3], b.getRgb()[:3])

    def _update_views(self) -> None:
        for v in self.ctx.views():
            v.viewport().update()

    # ------------------------------------------------------------------ disegno
    def _attach(self, view: DocumentView) -> None:
        if self.paint not in view.overlays:
            view.overlays.insert(0, self.paint)

    def image_boxes(self, view: DocumentView, page: int) -> list[pymupdf.Rect]:
        """Riquadri delle immagini della pagina (coordinate pagina non ruotata)."""
        key = (id(view), page, view.document.version)
        boxes = self._images.get(key)
        if boxes is None:
            boxes = []
            with ENGINE_LOCK:
                try:
                    pg = view.document.pdf[page]
                    area = abs(pg.rect)
                    for info in pg.get_image_info():
                        r = pymupdf.Rect(info["bbox"])
                        if pg.rotation:
                            r = r * pg.derotation_matrix
                        r.normalize()
                        if r.is_empty:
                            continue
                        scan = area > 0 and abs(r & pg.rect) / area >= SCAN_COVERAGE
                        boxes.append((r, scan))
                except Exception:
                    boxes = []
            if len(self._images) > 400:
                self._images.clear()
            self._images[key] = boxes
        return boxes

    def paint(self, view: DocumentView, p: QPainter, page: int) -> None:
        colors = self.colors()
        if colors is None and not self.warm:
            return
        w, h = view.page_size(page)
        # il painter è in coordinate pagina non ruotata: il riquadro della pagina in quelle coordinate
        inv, _ok = p.worldTransform().inverted()
        page_view = view.page_rect(page)
        rect = inv.mapRect(page_view)
        if colors is not None:
            paint_recolor(p, rect, colors[0], colors[1])
        if self.warm:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(rect, WARM)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if colors is None or not self.keep_images:
            return
        img = view._stale.get(page)   # resa originale più recente della pagina (soluzione locale)
        if img is None:
            return
        sx = img.width() / max(1.0, page_view.width())
        sy = img.height() / max(1.0, page_view.height())
        for box, scan in self.image_boxes(view, page):
            if scan and self.scans_too:
                continue
            target = view.page_to_view(page, box) & page_view
            if target.isEmpty():
                continue
            source = QRectF((target.x() - page_view.x()) * sx, (target.y() - page_view.y()) * sy,
                            target.width() * sx, target.height() * sy)
            p.save()
            p.resetTransform()
            p.setClipping(False)
            p.drawImage(target, img, source)
            p.restore()


def install(ctx: Context) -> AppearanceController:
    add_translations("en", TRANSLATIONS)
    return AppearanceController(ctx)
