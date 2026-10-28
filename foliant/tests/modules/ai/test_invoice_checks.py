"""AI-29: controlli locali delle fatture (somma righe con differenza, partita IVA e codice fiscale)."""

from foliant.modules.ai import extract
from foliant.modules.ai.engine import Scope


def _result(net, piva="01234567897"):
    raw = [{"numero": "7", "partita_iva": piva, "imponibile": net, "righe": [
        {"descrizione": "a", "importo": 100.0}, {"descrizione": "b", "importo": 50.0},
        {"descrizione": "c", "importo": 25.5}]}]
    return extract.build_result("invoice", raw, Scope())


def test_line_sum_check_passes_and_fails_with_difference():
    assert not [c for c in _result(175.5).checks if "somma" in c]
    failed = [c for c in _result(180.0).checks if "somma" in c]
    assert failed and "4,50" in failed[0]


def test_partita_iva_and_codice_fiscale_validation():
    assert extract.partita_iva_valid("01234567897")
    assert not extract.partita_iva_valid("01234567890")
    assert extract.codice_fiscale_valid("RSSMRA85T10A562S")
    assert not extract.codice_fiscale_valid("RSSMRA85T10A562X")
    bad = _result(175.5, piva="01234567890").checks
    assert any("non è formalmente valido" in c for c in bad)
