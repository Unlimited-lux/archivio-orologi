"""Icone dell'interfaccia.

Set di icone a tratto (licenza ISC, vedi resources/icons/LICENSE-lucide.txt),
colorate secondo il tema. Usare `bind_icon(oggetto, "nome")` per azioni e
pulsanti: l'icona viene ridisegnata automaticamente quando cambia il tema.
I nomi disponibili sono i file in resources/icons (senza ".svg").
"""

from __future__ import annotations

import weakref
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

RESOURCES = Path(__file__).resolve().parent.parent / "resources"
ICON_DIR = RESOURCES / "icons"
_SIZES = (16, 20, 24, 32, 48)
_cache: dict[tuple[str, str, str], QIcon] = {}
_bound: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()


def exists(name: str) -> bool:
    return (ICON_DIR / f"{name}.svg").is_file()


def _svg(name: str, color: str) -> bytes:
    path = ICON_DIR / f"{name}.svg"
    if not path.is_file():
        path = ICON_DIR / "circle-help.svg"
    text = path.read_text(encoding="utf-8").replace("currentColor", color)
    return text.encode("utf-8")


def _pixmap(data: bytes, size: int, dpr: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(data))
    pm = QPixmap(int(size * dpr), int(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p, QRectF(0, 0, size * dpr, size * dpr))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str | None = None, checked_color: str | None = None) -> QIcon:
    """QIcon di `name` nei colori del tema (o in `color`)."""
    normal = color or theme.hex_color("text")
    on = checked_color or theme.hex_color("accent")
    key = (name, normal, on)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    ic = QIcon()
    data_n = _svg(name, normal)
    data_d = _svg(name, theme.hex_color("text_muted"))
    data_on = _svg(name, on)
    for s in _SIZES:
        ic.addPixmap(_pixmap(data_n, s), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(_pixmap(data_d, s), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(_pixmap(data_on, s), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(_pixmap(data_on, s), QIcon.Mode.Active, QIcon.State.On)
    _cache[key] = ic
    return ic


def bind_icon(obj, name: str, kind: str = "default") -> None:
    """Imposta l'icona su un QAction/QAbstractButton e la aggiorna al cambio tema.

    kind="mode" usa il colore del testo su sfondo accento quando selezionato.
    """
    _bound[obj] = f"{kind}:{name}"
    _apply(obj, name, kind)


def _apply(obj, name: str, kind: str) -> None:
    if kind == "mode":
        ic = icon(name, checked_color=theme.hex_color("accent_text"))
    else:
        ic = icon(name)
    obj.setIcon(ic)


def refresh_all() -> None:
    _cache.clear()
    for obj, spec in list(_bound.items()):
        kind, name = spec.split(":", 1)
        try:
            _apply(obj, name, kind)
        except RuntimeError:  # oggetto Qt già distrutto
            pass


def app_icon() -> QIcon:
    ic = QIcon()
    path = RESOURCES / "foliant.svg"
    data = path.read_bytes()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(_pixmap(data, s, 1.0))
    return ic


theme.on_change(refresh_all)
