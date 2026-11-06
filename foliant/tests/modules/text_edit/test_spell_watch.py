"""Controllo ortografico: parole del mestiere."""

from __future__ import annotations

import pymupdf
import pytest

from foliant.modules.text_edit.spell import SpellService

PERIZIA = [
    "Perizia n. 58 - Cronografo automatico in acciaio, referenza 3570.50, matricola 4812xxxx.",
    "Cassa e fondello in buone condizioni, ghiera tachimetrica con lunetta integra, vetro zaffiro.",
    "Quadrante nero con indici luminescenti e lancette originali; corona a vite e pulsanti funzionanti.",
    "Bracciale in acciaio con chiusura deployante, cinturino in pelle incluso nella scatola.",
    "Movimento calibro 1861 a carica manuale: revisione completa eseguita, garanzia di dodici mesi.",
    "Impermeabilità verificata dopo la revisione; rehaut e carrure senza segni, anse non lucidate.",
]

TERMS = [
    "bracciale", "bracciali", "cinturino", "cinturini", "fondello", "fondelli", "cronografo", "cronografi",
    "ghiera", "ghiere", "matricola", "matricole", "revisione", "revisioni", "lunetta", "lunette",
    "quadrante", "quadranti", "corona", "corone", "calibro", "calibri", "carica", "automatico", "automatici",
    "referenza", "referenze", "scatola", "scatole", "garanzia", "perizia", "perizie", "impermeabilità",
    "zaffiro", "acciaio", "rodio", "carrure", "anse", "deployante", "chiusura", "chiusure", "movimento",
    "movimenti", "tachimetro", "tachimetri", "lancetta", "lancette", "indice", "indici", "rehaut",
]


@pytest.fixture
def service(tmp_path):
    return SpellService(personal_path=tmp_path / "config" / "dizionario_personale.txt",
                        user_dict_dir=tmp_path / "dizionari", system_dict_dirs=[])


def _pdf(path, lines, lang: str | None) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in lines:
        page.insert_text((40, y), line, fontname="helv", fontsize=9)
        y += 30
    if lang:
        doc.xref_set_key(doc.pdf_catalog(), "Lang", f"({lang})")
    doc.save(str(path))
    doc.close()
    return pymupdf.open(str(path))


@pytest.mark.parametrize("word", TERMS)
def test_watch_terms_known_in_italian(service, word):
    assert service.is_known(word, "it"), word


def test_perizia_has_no_false_errors(service):
    flagged = [w for line in PERIZIA for _s, _e, w in service.misspelled(line, "it")]
    assert flagged == []
