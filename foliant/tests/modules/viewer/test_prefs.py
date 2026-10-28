"""VIS-51 — Preferenze di visualizzazione (parte)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from foliant.api import FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_FACING, VIEW_SINGLE


def _ctl(w):
    return w.ctx.viewer_parts["prefs"]


def test_default_fit_width_without_restore(viewer_window, long_pdf):
    s = viewer_window.settings
    s.setValue("viewer/restore_position", False)
    view = viewer_window.open_path(long_pdf)
    view.go_to_page(10)
    viewer_window.close_view(view)
    view = viewer_window.open_path(long_pdf)
    assert view.current_page == 0
    assert view.fit_mode == FIT_WIDTH


def test_dialog_saves_defaults_applied_on_open(viewer_window, sample_pdf):
    ctl = _ctl(viewer_window)
    d = ctl.open_dialog()
    ctl._select(ctl.layout_box, VIEW_FACING)
    ctl._select(ctl.zoom_box, FIT_PAGE)
    ctl.save()
    d.hide()
    assert viewer_window.settings.value("view/mode") == VIEW_FACING      # chiavi del nucleo
    assert viewer_window.settings.value("view/zoom") == FIT_PAGE
    view = viewer_window.open_path(sample_pdf)
    assert view.view_mode == VIEW_FACING
    assert view.fit_mode == FIT_PAGE


def test_core_general_preferences_apply_to_new_document(window, sample_pdf):
    """UX-17/VIS-51: Preferenze > Generale del nucleo (pagina singola, 100%) valgono per un
    documento nuovo con tutti i moduli caricati; il viewer non le sovrascrive."""
    window.settings.setValue("view/mode", VIEW_SINGLE)
    window.settings.setValue("view/zoom", "100")
    view = window.open_path(sample_pdf)
    assert view.view_mode == VIEW_SINGLE
    assert abs(view.zoom - 1.0) < 1e-6 and view.fit_mode != FIT_WIDTH
    ctl = _ctl(window)
    ctl.open_dialog()
    assert ctl.layout_box.currentData() == VIEW_SINGLE and ctl.zoom_box.currentData() == "100"
    ctl.dialog.hide()


def test_other_percentage_applied_by_viewer(viewer_window, sample_pdf):
    viewer_window.settings.setValue("view/zoom", "150")
    view = viewer_window.open_path(sample_pdf)
    assert abs(view.zoom - 1.5) < 1e-6


def test_old_viewer_keys_are_migrated(viewer_window):
    from foliant.modules.viewer.prefs import zoom_key
    s = viewer_window.settings
    s.remove("view/mode")
    s.remove("view/zoom")
    s.setValue("viewer/default_layout", VIEW_FACING)
    s.setValue("viewer/default_zoom", "1.25")
    _ctl(viewer_window).migrate()
    assert s.value("view/mode") == VIEW_FACING and s.value("view/zoom") == "125"
    assert s.value("viewer/default_layout") is None and s.value("viewer/default_zoom") is None
    # un valore già scelto nel nucleo non viene sovrascritto
    s.setValue("viewer/default_layout", VIEW_CONTINUOUS)
    _ctl(viewer_window).migrate()
    assert s.value("view/mode") == VIEW_FACING
    assert zoom_key("1.0") == "100" and zoom_key(FIT_WIDTH) == FIT_WIDTH and zoom_key("x") is None


def test_export_import_settings(viewer_window, tmp_path):
    ctl = _ctl(viewer_window)
    s = viewer_window.settings
    s.setValue("viewer/default_layout", VIEW_FACING)
    s.setValue("viewer/reading_theme", "paper")
    s.setValue("viewer/shortcuts", ["Ruota vista\tCtrl+Alt+R"])
    s.setValue("recent", ["/privato/a.pdf"])
    text = ctl.export_text()
    assert "privato" not in text
    other = QSettings(str(tmp_path / "altro.ini"), QSettings.Format.IniFormat)
    for k in other.allKeys():
        other.remove(k)

    class Fake:
        def settings(self):
            return other
        viewer_parts = {}

    from foliant.modules.viewer.prefs import PrefsController
    ctl2 = PrefsController.__new__(PrefsController)
    ctl2.ctx = Fake()
    assert ctl2.import_text(text) >= 3
    assert other.value("view/mode") == VIEW_FACING          # vecchia chiave migrata su quella del nucleo
    assert other.value("viewer/reading_theme") == "paper"
    assert other.value("viewer/shortcuts") == ["Ruota vista\tCtrl+Alt+R"]


def test_invalid_import_and_restore_defaults(viewer_window):
    ctl = _ctl(viewer_window)
    import pytest
    with pytest.raises(ValueError):
        ctl.import_text("non è un file di impostazioni")
    viewer_window.settings.setValue("viewer/default_layout", VIEW_FACING)
    viewer_window.settings.setValue("view/mode", VIEW_FACING)
    ctl.restore_defaults()
    assert viewer_window.settings.value("viewer/default_layout") is None
    assert viewer_window.settings.value("view/mode") is None
