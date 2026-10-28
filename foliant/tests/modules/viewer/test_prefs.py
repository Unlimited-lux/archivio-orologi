"""VIS-51 — Preferenze di visualizzazione (parte)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from foliant.api import FIT_PAGE, FIT_WIDTH, VIEW_FACING


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
    view = viewer_window.open_path(sample_pdf)
    assert view.view_mode == VIEW_FACING
    assert view.fit_mode == FIT_PAGE


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
    assert other.value("viewer/default_layout") == VIEW_FACING
    assert other.value("viewer/reading_theme") == "paper"
    assert other.value("viewer/shortcuts") == ["Ruota vista\tCtrl+Alt+R"]


def test_invalid_import_and_restore_defaults(viewer_window):
    ctl = _ctl(viewer_window)
    import pytest
    with pytest.raises(ValueError):
        ctl.import_text("non è un file di impostazioni")
    viewer_window.settings.setValue("viewer/default_layout", VIEW_FACING)
    ctl.restore_defaults()
    assert viewer_window.settings.value("viewer/default_layout") is None
