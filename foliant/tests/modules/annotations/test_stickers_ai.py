"""ANN-27 (adesivi per categoria, personalizzati, dimensione minima) e ANN-28 (generazione con IA)."""

import io
import types

import pymupdf
import pytest
from PySide6.QtWidgets import QMenu

from foliant.modules.annotations import engine

from .conftest import annots, click, saved


def _png(color=(255, 0, 0)) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), 1)
    pix.set_rect(pix.irect, (*color, 255))
    return pix.tobytes("png")


def _menu_titles(menu: QMenu) -> dict[str, QMenu]:
    return {a.text(): a.menu() for a in menu.actions() if a.menu() is not None}


def test_ann27_every_sticker_by_category(comments, tmp_path):
    window, view, module = comments
    stickers = _menu_titles(module.stamp_button.menu())["Adesivi"]
    categories = _menu_titles(stickers)
    assert {"Spunte e croci", "Simboli di stato", "Frecce", "Faccine e gesti", "Stelle e cuori",
            "Personalizzati"} <= set(categories)
    keys = [k for _c, ks in engine.STICKER_CATEGORIES for k in ks]
    assert set(keys) == set(engine.STICKERS)
    for i, key in enumerate(keys):
        module.ctrl.apply("a", engine.create_sticker, 0, (60 + (i % 8) * 60, 450 + (i // 8) * 60), key,
                          engine.Style(), "A")
    doc = saved(window, view, tmp_path)
    stamps = annots(doc, 0, "Stamp")
    assert len(stamps) == len(keys)
    for st in stamps:
        assert st.info["subject"] == "Adesivo"
        ap = int(doc.xref_get_key(st.xref, "AP/N")[1].split()[0])
        assert len(doc.xref_stream(ap)) > 20                       # grafica vettoriale incorporata
        pix = doc[0].get_pixmap(clip=st.rect)
        assert any(pix.pixel(x, y) != (255, 255, 255) for x in range(pix.width) for y in range(pix.height))
    assert all(i.label == "Adesivo" for i in module.ctrl.annotations())


def test_ann27_resize_below_8pt_is_clamped(comments):
    window, view, module = comments
    x = module.ctrl.apply("a", engine.create_sticker, 0, (300, 300), "stella", engine.Style(), "A")
    module.ctrl.resize(0, x, pymupdf.Rect(300, 300, 302, 302))
    r = module.ctrl.info(0, x).rect
    assert min(r.width, r.height) >= engine.STICKER_MIN - 0.01


def test_ann27_custom_sticker_from_library(comments, tmp_path):
    window, view, module = comments
    module.ctrl.add_custom_stamp("Logo", _png())
    menu = module.custom_stickers_menu
    menu.aboutToShow.emit()
    [a for a in menu.actions() if a.text() == "Logo"][0].trigger()
    click(view, 0, 300, 400)
    info, = module.ctrl.annotations()
    assert info.label == "Adesivo"
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    assert st.info["subject"] == "Adesivo" and doc.xref_get_key(st.xref, "IT")[1] == "/StampImage"


# ------------------------------------------------------------------ ANN-28
class _Store:
    def __init__(self, enabled=True, profile=True, consent=True):
        self._enabled = enabled
        self.profile = types.SimpleNamespace(consent=consent, image_model="img", name="P") if profile else None
        self.saved = []

    def enabled(self):
        return self._enabled

    def active(self, check_key=True):
        return self.profile

    def provider_for(self, profile):
        raise AssertionError("nessun invio nel test")

    def save(self, profile):
        self.saved.append(profile)


def _services(store, png=None, consent_answer=True, log=None):
    log = log if log is not None else []

    def exec_dialog(dlg):
        log.append(("dialog", dlg.kind))
        if png is not None:
            dlg.chosen_png, dlg.last_prompt = png, "timbro rotondo blu Verificato"
        return 1

    panel = types.SimpleNamespace(ask_consent=lambda p: log.append("consent") or consent_answer)
    return types.SimpleNamespace(store=store, controller=types.SimpleNamespace(exec_dialog=exec_dialog), panel=panel)


@pytest.mark.parametrize("kind", ["stamp", "sticker"])
def test_ann28_generated_image_applied_and_saved_in_library(comments, tmp_path, monkeypatch, kind):
    pytest.importorskip("foliant.modules.ai.graphics_view")
    window, view, module = comments
    log = []
    monkeypatch.setattr(window.ctx, "ai_services", _services(_Store(consent=False), _png((0, 0, 255)), log=log),
                        raising=False)
    assert module.generate_with_ai(kind)
    assert log[0] == "consent" and log[1] == ("dialog", kind)         # informativa prima dell'invio
    click(view, 0, 300, 400)
    doc = saved(window, view, tmp_path)
    st, = annots(doc, 0, "Stamp")
    assert doc.xref_get_key(st.xref, "IT")[1] == "/StampImage"
    assert (st.info["subject"] == "Adesivo") == (kind == "sticker")
    lib = module.ctrl.custom_stamps()
    assert lib[-1]["origin"] == "ia" and lib[-1]["name"].startswith("timbro rotondo")


def test_ann28_unavailable_and_hidden(comments, monkeypatch):
    window, view, module = comments
    monkeypatch.setattr(window.ctx, "ai_services", _services(_Store(profile=False)), raising=False)
    assert module.generate_with_ai("stamp") is None
    assert module.messages and "non disponibile" in module.messages[-1][2]
    module.use_sticker("spunta")                                     # gli altri strumenti restano utilizzabili
    click(view, 0, 300, 400)
    assert len(module.ctrl.annotations()) == 1
    # «Funzioni IA» disattivate: il comando non è visibile e non parte nulla
    log = []
    monkeypatch.setattr(window.ctx, "ai_services", _services(_Store(enabled=False), log=log), raising=False)
    module.stamp_button.menu().aboutToShow.emit()
    assert not module.ai_stamp_action.isVisible() and not module.ai_sticker_action.isVisible()
    assert module.generate_with_ai("stamp") is None and log == []
