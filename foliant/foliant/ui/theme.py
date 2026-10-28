"""Tema chiaro/scuro e colori dell'interfaccia.

I colori sono "token": i moduli li leggono con `color("accent")` invece di
scrivere valori fissi, così rispettano il tema attivo.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

LIGHT: dict[str, str | tuple] = {
    "accent": "#0F7C78",
    "accent_hover": "#0B6865",
    "accent_soft": "#E0F1EF",
    "accent_text": "#FFFFFF",
    "secondary": "#E59A2F",
    "window": "#F7F7F5",
    "surface": "#FFFFFF",
    "surface_alt": "#EFEFEC",
    "border": "#E3E3DE",
    "control_border": "#868C91",     # contorno dei campi di immissione: ≥ 3:1 su window e surface
    "text": "#1C2125",
    "text_muted": "#585F65",
    "canvas": "#E7E7E3",
    "page_shadow": (0, 0, 0, 38),
    "danger": "#C2453A",
    "success": "#2E7D4F",
    "warning": "#8A5A00",            # ≥ 4,5:1 su window e surface (NFR-23)
    "selection": (15, 124, 120, 70),
}

DARK: dict[str, str | tuple] = {
    "accent": "#3CB9B0",
    "accent_hover": "#52C9C0",
    "accent_soft": "#1D3B3A",
    "accent_text": "#0E1A19",
    "secondary": "#F2B45A",
    "window": "#1D2023",
    "surface": "#25292C",
    "surface_alt": "#2E3337",
    "border": "#363B40",
    "control_border": "#7A7F85",
    "text": "#ECEEEF",
    "text_muted": "#A9AFB4",
    "canvas": "#151719",
    "page_shadow": (0, 0, 0, 120),
    "danger": "#EC7A6F",
    "success": "#5FC98A",
    "warning": "#F2B45A",
    "selection": (60, 185, 176, 90),
}

_dark = False
_mode = "system"
_applied: tuple | None = None          # (modalità, scuro) dell'ultima applicazione
_listeners: list[Callable[[], None]] = []


def is_dark() -> bool:
    return _dark


def mode() -> str:
    return _mode


def tokens() -> dict:
    return DARK if _dark else LIGHT


def color(name: str) -> QColor:
    value = tokens().get(name, "#FF00FF")
    if isinstance(value, tuple):
        return QColor(*value)
    return QColor(value)


def hex_color(name: str) -> str:
    value = tokens().get(name, "#FF00FF")
    if isinstance(value, tuple):
        return QColor(*value).name()
    return value


def on_change(fn: Callable[[], None]) -> None:
    """Registra una funzione da chiamare quando cambia il tema."""
    _listeners.append(fn)


def _system_is_dark(app: QApplication) -> bool:
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


class _Style(QProxyStyle):
    """Fusion con l'icona dei comandi in eccesso delle barre («»») nei colori del tema: quella
    di Fusion è un grigio fisso, invisibile nel tema scuro. Lo spazio riservato al pulsante
    è quello dell'icona, così non esce dal bordo della barra."""

    _EXTENSION = {QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton: "chevrons-right",
                  QStyle.StandardPixmap.SP_ToolBarVerticalExtensionButton: "chevrons-down"}

    def __init__(self):
        super().__init__("Fusion")

    def standardIcon(self, sp, option=None, widget=None):
        name = self._EXTENSION.get(sp)
        if name is not None:
            from .icons import icon        # import locale: icons dipende da theme
            return icon(name)
        return super().standardIcon(sp, option, widget)

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ToolBarExtensionExtent:
            return 20
        return super().pixelMetric(metric, option, widget)


def _refresh_extension_buttons(app: QApplication) -> None:
    """Le barre impostano l'icona «»» quando vengono create: al cambio tema la ricolora."""
    style = app.style()
    for w in app.allWidgets():
        if w.objectName() == "qt_toolbar_ext_button":
            bar = w.parentWidget()
            vertical = bar is not None and getattr(bar, "orientation", lambda: Qt.Orientation.Horizontal)() \
                == Qt.Orientation.Vertical
            sp = (QStyle.StandardPixmap.SP_ToolBarVerticalExtensionButton if vertical
                  else QStyle.StandardPixmap.SP_ToolBarHorizontalExtensionButton)
            try:
                w.setIcon(style.standardIcon(sp, None, w))
            except (RuntimeError, AttributeError):
                pass


def apply(app: QApplication, requested: str = "system") -> None:
    """Applica il tema: "light", "dark" o "system"."""
    global _dark, _mode, _applied
    _mode = requested if requested in ("light", "dark", "system") else "system"
    _dark = _system_is_dark(app) if _mode == "system" else _mode == "dark"
    if _applied == (_mode, _dark, id(app)):
        return              # tema invariato: ristilizzare tutti i widget è costoso e inutile
    _applied = (_mode, _dark, id(app))
    if not isinstance(app.style(), _Style) and not isinstance(app.style().proxy(), _Style):
        app.setStyle(_Style())                  # una volta sola: reimpostarlo ripolirebbe tutti i widget
    t = tokens()
    pal = QPalette()

    def c(name: str) -> QColor:
        return color(name)

    pal.setColor(QPalette.ColorRole.Window, c("window"))
    pal.setColor(QPalette.ColorRole.WindowText, c("text"))
    pal.setColor(QPalette.ColorRole.Base, c("surface"))
    pal.setColor(QPalette.ColorRole.AlternateBase, c("surface_alt"))
    pal.setColor(QPalette.ColorRole.Text, c("text"))
    pal.setColor(QPalette.ColorRole.Button, c("surface"))
    pal.setColor(QPalette.ColorRole.ButtonText, c("text"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, c("surface"))
    pal.setColor(QPalette.ColorRole.ToolTipText, c("text"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, c("text_muted"))
    pal.setColor(QPalette.ColorRole.Highlight, c("accent"))
    pal.setColor(QPalette.ColorRole.HighlightedText, c("accent_text"))
    pal.setColor(QPalette.ColorRole.Link, c("accent"))
    pal.setColor(QPalette.ColorRole.Mid, c("border"))
    pal.setColor(QPalette.ColorRole.Midlight, c("surface_alt"))
    pal.setColor(QPalette.ColorRole.Dark, c("border"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, c("text_muted"))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(t))
    _refresh_extension_buttons(app)
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


_ICONS = Path(__file__).resolve().parent.parent / "resources" / "icons"


def _asset(name: str, color: str) -> str:
    """Icona colorata scritta su disco (i fogli di stile Qt richiedono un file)."""
    folder = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "foliant" / "theme"
    target = folder / f"{name}-{color.lstrip('#')}.svg"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            svg = (_ICONS / f"{name}.svg").read_text(encoding="utf-8").replace("currentColor", color)
            target.write_text(svg, encoding="utf-8")
    except OSError:
        return (_ICONS / f"{name}.svg").as_posix()   # icona non colorata, ma l'app parte
    return target.as_posix()


def stylesheet(t: dict) -> str:
    def h(name: str) -> str:
        v = t[name]
        if isinstance(v, tuple):
            return f"rgba({v[0]},{v[1]},{v[2]},{v[3]})"
        return v

    close_icon = _asset("x", h("text_muted"))
    close_hover = _asset("x", h("text"))
    arrow = _asset("chevron-down", h("text_muted"))
    arrow_up = _asset("chevron-up", h("text_muted"))
    check = _asset("check", h("accent_text"))
    return f"""
QMainWindow, QDialog {{ background: {h('window')}; }}
QTabBar::close-button {{ image: url({close_icon}); subcontrol-position: right; margin: 2px; width: 14px; height: 14px; }}
QTabBar::close-button:hover {{ image: url({close_hover}); background: {h('surface_alt')}; border-radius: 4px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 14px; height: 14px; }}
QSpinBox#pageSpin {{ padding: 2px 4px; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 16px; border: none; border-left: 1px solid {h('border')}; border-top-right-radius: 6px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 16px; border: none; border-left: 1px solid {h('border')}; border-bottom-right-radius: 6px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {h('surface_alt')}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({arrow_up}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({arrow}); width: 10px; height: 10px; }}
QToolBar#header {{ background: {h('surface')}; border: none; border-bottom: 1px solid {h('border')};
    padding: 4px 8px; spacing: 4px; }}
QToolBar#contextbar {{ background: {h('surface')}; border: none; border-bottom: 1px solid {h('border')};
    padding: 2px 8px; spacing: 2px; }}
QToolBar {{ spacing: 2px; }}
QToolButton {{ border: 1px solid transparent; border-radius: 6px; padding: 4px 6px; color: {h('text')}; }}
QToolButton:hover {{ background: {h('surface_alt')}; }}
QToolButton:pressed {{ background: {h('border')}; }}
QToolButton:checked {{ background: {h('accent_soft')}; color: {h('accent_hover')}; }}
QToolButton[modeButton="true"] {{ padding: 5px 10px; font-weight: 500; }}
QToolButton[modeButton="true"]:checked {{ background: {h('accent')}; color: {h('accent_text')}; }}
QToolButton::menu-indicator {{ image: none; }}
QToolButton#qt_toolbar_ext_button {{ padding: 0px; border: none; }}
QToolButton[homeAction="true"] {{ border: 1px solid {h('border')}; border-radius: 10px; background: {h('surface')}; }}
QToolButton[homeAction="true"]:hover {{ border-color: {h('accent')}; }}
QPushButton {{ background: {h('surface')}; border: 1px solid {h('border')}; border-radius: 6px;
    padding: 6px 14px; color: {h('text')}; }}
QPushButton:hover {{ background: {h('surface_alt')}; }}
QPushButton:default, QPushButton[primary="true"] {{ background: {h('accent')}; color: {h('accent_text')};
    border-color: {h('accent')}; }}
QPushButton[primary="true"]:hover {{ background: {h('accent_hover')}; }}
QPushButton:disabled {{ color: {h('text_muted')}; }}
QPushButton:default:disabled, QPushButton[primary="true"]:disabled {{ background: {h('surface_alt')};
    border-color: {h('border')}; color: {h('text_muted')}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {h('surface')}; border: 1px solid {h('control_border')}; border-radius: 6px; padding: 3px 6px;
    color: {h('text')}; selection-background-color: {h('accent')}; selection-color: {h('accent_text')}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {h('accent')}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; padding: 6px 12px; border: none; border-bottom: 2px solid transparent;
    color: {h('text_muted')}; }}
QTabBar::tab:selected {{ color: {h('text')}; border-bottom: 2px solid {h('accent')}; }}
QTabBar::tab:hover {{ color: {h('text')}; }}
QStatusBar {{ background: {h('surface')}; border-top: 1px solid {h('border')}; color: {h('text_muted')}; }}
QWidget#sidebar, QWidget#properties {{ background: {h('surface')}; }}
QWidget#sidebar {{ border-right: 1px solid {h('border')}; }}
QWidget#properties {{ border-left: 1px solid {h('border')}; }}
QLabel[heading="true"] {{ font-size: 15px; font-weight: 600; color: {h('text')}; }}
QLabel[muted="true"] {{ color: {h('text_muted')}; }}
QListWidget, QTreeWidget, QTreeView, QListView {{ background: {h('surface')}; border: none; color: {h('text')}; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected, QListView::item:selected {{
    background: {h('accent_soft')}; color: {h('text')}; }}
QMenu {{ background: {h('surface')}; border: 1px solid {h('border')}; padding: 4px; }}
QMenu::item {{ padding: 6px 24px 6px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {h('surface_alt')}; }}
QMenu::separator {{ height: 1px; background: {h('border')}; margin: 4px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {h('border')}; border-radius: 5px; min-height: 32px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {h('border')}; border-radius: 5px; min-width: 32px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ border: 1px solid {h('border')}; border-radius: 6px; text-align: center; background: {h('surface_alt')}; }}
QProgressBar::chunk {{ background: {h('accent')}; border-radius: 5px; }}
QGroupBox {{ border: 1px solid {h('border')}; border-radius: 8px; margin-top: 14px; padding-top: 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {h('text_muted')}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; background: {h('surface')};
    border: 1px solid {h('control_border')}; }}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {h('accent')}; }}
QCheckBox::indicator:checked {{ background: {h('accent')}; border-color: {h('accent')}; image: url({check}); }}
QRadioButton::indicator:checked {{ border-color: {h('accent')}; background: qradialgradient(cx:0.5, cy:0.5,
    radius:0.5, fx:0.5, fy:0.5, stop:0 {h('accent')}, stop:0.45 {h('accent')}, stop:0.55 {h('surface')}, stop:1 {h('surface')}); }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {h('border')}; background: {h('surface_alt')}; }}
QCheckBox::indicator:checked:disabled {{ background: {h('text_muted')}; border-color: {h('text_muted')}; }}
"""
