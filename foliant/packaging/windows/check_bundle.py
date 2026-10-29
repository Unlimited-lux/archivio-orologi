"""Prova del pacchetto per Windows (eseguita da foliant-cli.exe in GitHub Actions): strumenti
inclusi trovati nella cartella «tools», lingue OCR presenti, tutti i moduli importabili, librerie
Office con i loro modelli e una conversione reale in Word, Excel e PowerPoint senza programmi esterni."""

import subprocess

import foliant.modules as modules
from foliant.core import platform

tools = {name: platform.find_tool(name) for name in ("tesseract", "gs", "qpdf")}
print("strumenti:", tools)
assert all(path and "\\tools\\" in path for path in tools.values()), tools
langs = subprocess.run([tools["tesseract"], "--list-langs"], capture_output=True, text=True).stdout.split()
assert all(code in langs for code in ("ita", "eng", "osd")), langs
import foliant
from pathlib import Path
resources = Path(foliant.__file__).resolve().parent / "resources"
assert (resources / "foliant.svg").is_file(), resources
icons = len(list((resources / "icons").glob("*.svg")))
print("icone:", icons)
assert icons > 100, icons
found = list(modules.discover())
print("moduli:", len(found))
assert len(found) >= 12, [name for _o, name, _m in found]
broken = [name for _order, name, mod in modules.discover() if isinstance(mod, Exception)]
print("moduli non importabili:", broken)
assert not broken, broken

# Conversione in Office: solo librerie Python incluse (pdf2docx, python-docx, openpyxl, python-pptx, lxml)
import importlib
import os
import tempfile

for name in ("pdf2docx", "docx", "openpyxl", "pptx", "lxml.etree"):
    importlib.import_module(name)
import docx
import pptx
for package, template in ((docx, "default.docx"), (pptx, "default.pptx")):
    path = Path(package.__file__).resolve().parent / "templates" / template
    assert path.is_file(), path
import pymupdf
from foliant.modules.convert.engine import convert_pdf

with tempfile.TemporaryDirectory() as tmp:
    pdf = os.path.join(tmp, "prova.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Prova di conversione Office", fontsize=14)
    for r in range(4):                                   # tabella 3 x 4 con bordi
        for c in range(3):
            rect = pymupdf.Rect(72 + c * 120, 100 + r * 24, 192 + c * 120, 124 + r * 24)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 4, rect.y1 - 7), f"R{r}C{c}", fontsize=10)
    doc.save(pdf)
    doc.close()
    for fmt in ("docx", "xlsx", "pptx"):
        out = convert_pdf(pdf, os.path.join(tmp, "prova." + fmt), fmt)
        assert out and all(os.path.getsize(p) > 0 for p in out), (fmt, out)
        print("conversione", fmt, "ok")
    import openpyxl
    sheet = openpyxl.load_workbook(os.path.join(tmp, "prova.xlsx")).active
    cells = {str(c.value) for row in sheet.iter_rows() for c in row if c.value is not None}
    assert "R2C1" in cells, cells
    text = " ".join(sh.text_frame.text for sl in pptx.Presentation(os.path.join(tmp, "prova.pptx")).slides
                    for sh in sl.shapes if sh.has_text_frame)
    assert "Prova di conversione Office" in text, text
print("pacchetto: ok")
