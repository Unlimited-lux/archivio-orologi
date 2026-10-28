"""NFR-26: formato carta e unità di misura dalle impostazioni locali, con la preferenza
dell'utente che ha la precedenza (Preferenze › «Formato carta», «Unità di misura»)."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QLocale

from foliant.core import locale_defaults as loc


@pytest.fixture
def system(monkeypatch):
    """Sostituisce le impostazioni locali del sistema (QLocale.system() si legge una volta sola)."""
    def use(name: str) -> None:
        monkeypatch.setattr(loc, "system_locale", lambda: QLocale(name))
    return use


def test_defaults_follow_the_measurement_system(system):
    assert (loc.default_paper_size(QLocale("en_US")), loc.default_unit(QLocale("en_US"))) == ("Letter", "in")
    assert (loc.default_paper_size(QLocale("it_IT")), loc.default_unit(QLocale("it_IT"))) == ("A4", "mm")
    assert (loc.default_paper_size(QLocale("en_GB")), loc.default_unit(QLocale("en_GB"))) == ("A4", "mm")
    system("en_US")
    assert (loc.default_paper_size(), loc.default_unit()) == ("Letter", "in")
    system("it_IT")
    assert (loc.default_paper_size(), loc.default_unit()) == ("A4", "mm")


def test_measurement_system_is_read_from_qlocale(monkeypatch):
    monkeypatch.setattr(QLocale, "measurementSystem", lambda self: QLocale.MeasurementSystem.ImperialUSSystem)
    assert (loc.default_paper_size(), loc.default_unit()) == ("Letter", "in")
    monkeypatch.setattr(QLocale, "measurementSystem", lambda self: QLocale.MeasurementSystem.MetricSystem)
    assert (loc.default_paper_size(), loc.default_unit()) == ("A4", "mm")


def test_context_uses_preference_then_locale(window, system):
    ctx = window.ctx
    system("en_US")
    assert (ctx.paper_size(), ctx.measure_unit()) == ("Letter", "in")
    system("it_IT")
    assert (ctx.paper_size(), ctx.measure_unit()) == ("A4", "mm")
    window.settings.setValue(loc.PAPER_KEY, "Letter")
    window.settings.setValue(loc.UNIT_KEY, "in")
    assert (ctx.paper_size(), ctx.measure_unit()) == ("Letter", "in")     # la preferenza vince
    window.settings.setValue(loc.PAPER_KEY, "B5")                         # valore sconosciuto: locale
    window.settings.setValue(loc.UNIT_KEY, loc.AUTO)
    assert (ctx.paper_size(), ctx.measure_unit()) == ("A4", "mm")
    assert loc.PAPER_SIZES["Letter"] == (612.0, 792.0)


def test_preferences_offer_paper_size_and_unit(window, system):
    from foliant.ui.dialogs import PreferencesDialog
    system("en_US")
    dlg = PreferencesDialog(window.settings, window)
    assert dlg.paper_size.currentData() == loc.AUTO and "Letter" in dlg.paper_size.currentText()
    assert [dlg.paper_size.itemData(i) for i in range(dlg.paper_size.count())] == [loc.AUTO, "A4", "Letter"]
    assert [dlg.measure_unit.itemData(i) for i in range(dlg.measure_unit.count())] == [loc.AUTO, "mm", "in"]
    assert window.ctx.paper_size() == "Letter"
    dlg.paper_size.setCurrentIndex(dlg.paper_size.findData("A4"))
    dlg.measure_unit.setCurrentIndex(dlg.measure_unit.findData("mm"))
    dlg.apply()
    assert (window.ctx.paper_size(), window.ctx.measure_unit()) == ("A4", "mm")
    again = PreferencesDialog(window.settings, window)                     # la scelta resta
    assert (again.paper_size.currentData(), again.measure_unit.currentData()) == ("A4", "mm")
    again.paper_size.setCurrentIndex(0)
    again.measure_unit.setCurrentIndex(0)
    again.apply()
    assert (window.ctx.paper_size(), window.ctx.measure_unit()) == ("Letter", "in")


def test_api_exposes_locale_defaults(settings):
    from foliant.api import locale_defaults
    settings.setValue(locale_defaults.PAPER_KEY, "Letter")
    assert locale_defaults.paper_size(settings) == "Letter"
    assert locale_defaults.measure_unit(settings) in locale_defaults.UNITS
