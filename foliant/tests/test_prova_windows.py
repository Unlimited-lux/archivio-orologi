"""Script della prova del pacchetto e dell'installatore per Windows (packaging/windows) con il
Foliant dei sorgenti: la parte di check_bundle.py sulla conversione in Word, Excel e PowerPoint;
PDF di prova, conversioni con la riga di comando delle operazioni in blocco e controllo finale
(verifica_risultati.py), come fa prova_installatore.ps1 dopo l'installazione. La sessione con la
finestra (sessione.py) è sostituita da una modifica diretta del PDF."""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from foliant.core import platform

WINDOWS = Path(__file__).resolve().parent.parent / "packaging" / "windows"
PROVA = WINDOWS / "prova"


def _ita_available() -> bool:
    if platform.find_tool("tesseract") is None:
        return False
    try:
        return os.path.isfile(os.path.join(pymupdf.get_tessdata(), "ita.traineddata"))
    except Exception:
        return False


def _script(monkeypatch, name: str, *args) -> None:
    """Esegue uno script della prova come `python script.py ARG…` (o `foliant-cli -c …`)."""
    monkeypatch.setattr(sys, "argv", [name, *map(str, args)])
    runpy.run_path(str(PROVA / name), run_name="__main__")


def _blocco(*args) -> None:
    """`foliant-cli blocco …` (qui il modulo della riga di comando, nello stesso processo)."""
    from foliant.modules.tools.cli import main
    assert main(["-q", *map(str, args)]) == 0


def test_bundle_check_converts_to_word_excel_powerpoint(capsys):
    """La parte di check_bundle.py che non dipende dal pacchetto (gli strumenti in «tools» sì)."""
    check = runpy.run_path(str(WINDOWS / "check_bundle.py"), run_name="check_bundle")
    check["check_office_conversion"]()
    out = capsys.readouterr().out
    assert "librerie della conversione non importabili: []" in out
    assert "modelli di python-docx e python-pptx mancanti: []" in out
    assert "PowerPoint: 2 diapositive; note: Commento di prova" in out


@pytest.mark.skipif(not _ita_available() or platform.find_tool("gs") is None,
                    reason="servono Tesseract con l'italiano e Ghostscript")
def test_installer_check_scripts_convert_and_verify(tmp_path, monkeypatch, capsys):
    risultati = tmp_path / "risultati"
    cartella = tmp_path / "CartellaRiservata"
    risultati.mkdir()
    cartella.mkdir()
    _script(monkeypatch, "crea_pdf_scansionato.py", risultati / "originale.pdf")
    _script(monkeypatch, "crea_pdf_tabella.py", risultati / "tabella.pdf")
    scansione, tabella = cartella / "Orologio.pdf", cartella / "Inventario.pdf"
    shutil.copy(risultati / "originale.pdf", scansione)
    shutil.copy(risultati / "tabella.pdf", tabella)
    with pymupdf.open(scansione) as doc:                   # al posto della sessione con la finestra
        doc[0].insert_text((72, 60), "Controllato con Foliant", fontsize=12)
        doc.save(risultati / "sessione.pdf")

    _blocco("-o", "ocr", "-o", "convert", "-p", "1:languages=ita", "-p", "2:format=docx",
            "-d", risultati / "docx", scansione)
    _blocco("-o", "ocr", "-o", "compress", "-o", "pdfa", "-p", "1:languages=ita", "-p", "2:level=high",
            "-d", risultati / "pdf", scansione)
    subprocess.run([platform.find_tool("gs"), "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite",
                    "-dPDFSETTINGS=/ebook", f"-sOutputFile={risultati / 'gs-ebook.pdf'}", str(scansione)],
                   check=True, capture_output=True, timeout=120, **platform.subprocess_kwargs())
    for formato in ("xlsx", "pptx", "docx"):
        _blocco("-o", "convert", "-p", f"format={formato}", "-d", risultati / f"tabella-{formato}", tabella)
    _script(monkeypatch, "converti_fedele.py", tabella, risultati / "tabella-fedele")

    _script(monkeypatch, "verifica_risultati.py", risultati)
    out = capsys.readouterr().out
    assert "risultati: ok" in out and "ERRORE" not in out
    for controllo in ("XLSX: codici come testo", "PPTX: commento del PDF nelle note", "DOCX: tabella di Word",
                      "DOCX fedele (pdf2docx)"):
        assert f"ok      {controllo}" in out

    # il controllo distingue davvero i valori: un importo rimasto testo in Excel fa fallire la prova
    import openpyxl
    xlsx = next((risultati / "tabella-xlsx").glob("*.xlsx"))
    wb = openpyxl.load_workbook(xlsx)
    cella = next(c for row in wb.worksheets[0].iter_rows() for c in row if c.value == 4250)
    cella.value = "4.250,00"
    wb.save(xlsx)
    with pytest.raises(SystemExit, match="1 controlli non riusciti"):
        _script(monkeypatch, "verifica_risultati.py", risultati)
    assert "ERRORE  XLSX: codici come testo, anni e importi come numeri" in capsys.readouterr().out
