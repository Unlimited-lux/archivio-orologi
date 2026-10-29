"""Prova del pacchetto per Windows (eseguita da foliant-cli.exe in GitHub Actions): strumenti
inclusi trovati nella cartella «tools», lingue OCR presenti, tutti i moduli importabili."""

import subprocess

import foliant.modules as modules
from foliant.core import platform

tools = {name: platform.find_tool(name) for name in ("tesseract", "gs", "qpdf")}
print("strumenti:", tools)
assert all(path and "\\tools\\" in path for path in tools.values()), tools
langs = subprocess.run([tools["tesseract"], "--list-langs"], capture_output=True, text=True).stdout.split()
assert all(code in langs for code in ("ita", "eng", "osd")), langs
broken = [name for _order, name, mod in modules.discover() if isinstance(mod, Exception)]
print("moduli non importabili:", broken)
assert not broken, broken
print("pacchetto: ok")
