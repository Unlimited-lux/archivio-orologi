"""Formato carta e unità di misura secondo le impostazioni locali (NFR-26).

Con il sistema di misura statunitense (es. en_US) Letter e pollici, altrimenti A4 e
millimetri. La preferenza dell'utente («Formato carta», «Unità di misura») ha la precedenza:
`paper_size(settings)` e `measure_unit(settings)`, per i moduli `ctx.paper_size()` e
`ctx.measure_unit()`.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, QSettings

PAPER_SIZES = {"A4": (595.276, 841.89), "Letter": (612.0, 792.0)}     # punti PDF (larghezza, altezza)
UNITS = ("mm", "in")
AUTO = "auto"
PAPER_KEY = "locale/paper_size"      # "auto", "A4" o "Letter"
UNIT_KEY = "locale/measure_unit"     # "auto", "mm" o "in"


def system_locale() -> QLocale:
    """Impostazioni locali del sistema (LC_MEASUREMENT, altrimenti LC_ALL/LANG)."""
    return QLocale.system()


def _us(locale: QLocale | None) -> bool:
    loc = locale if locale is not None else system_locale()
    return loc.measurementSystem() == QLocale.MeasurementSystem.ImperialUSSystem


def default_paper_size(locale: QLocale | None = None) -> str:
    """Formato carta locale: Letter con il sistema di misura statunitense, altrimenti A4."""
    return "Letter" if _us(locale) else "A4"


def default_unit(locale: QLocale | None = None) -> str:
    """Unità locale: pollici ("in") con il sistema statunitense, altrimenti millimetri ("mm")."""
    return "in" if _us(locale) else "mm"


def _preference(settings: QSettings | None, key: str) -> str:
    if settings is None:
        settings = QSettings()
    return str(settings.value(key, AUTO) or AUTO)


def paper_size(settings: QSettings | None = None) -> str:
    """Formato carta scelto nelle preferenze, altrimenti quello delle impostazioni locali.
    Senza `settings` usa le impostazioni dell'applicazione."""
    value = _preference(settings, PAPER_KEY)
    return value if value in PAPER_SIZES else default_paper_size()


def measure_unit(settings: QSettings | None = None) -> str:
    """Unità di misura ("mm" o "in") scelta nelle preferenze, altrimenti quella locale."""
    value = _preference(settings, UNIT_KEY)
    return value if value in UNITS else default_unit()
