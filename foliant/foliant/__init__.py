"""Foliant: editor PDF desktop per Linux e Windows."""

import importlib.abc
import importlib.machinery
import os
import sys

__version__ = "0.1.0"
APP_NAME = "Foliant"

# Riservatezza (NFR-19, O5): PyMuPDF 2 importa da sé l'analisi del layout (pymupdf.layout →
# onnxruntime), che Foliant non usa e che all'avvio crea un identificativo del dispositivo e
# mette in coda eventi di telemetria in ~/.cache/Microsoft. La si esclude prima che pymupdf
# venga importato (pymupdf ignora l'ImportError); avvio e memoria ne guadagnano. Se un domani
# servisse, FOLIANT_LAYOUT=1 la riabilita, sempre con la telemetria di onnxruntime spenta.
os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")
os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")


class _Unavailable(importlib.abc.Loader):
    """Il modulo esiste per `importlib.util.find_spec` (che quindi non fallisce), ma importarlo
    solleva ModuleNotFoundError, che pymupdf gestisce come «non installato»."""

    def create_module(self, spec):
        raise ModuleNotFoundError(f"{spec.name}: non usato da Foliant", name=spec.name)

    def exec_module(self, module):          # pragma: no cover - create_module solleva prima
        raise ModuleNotFoundError(f"{module.__name__}: non usato da Foliant", name=module.__name__)


class _NoLayoutAnalysis(importlib.abc.MetaPathFinder):
    BLOCKED = ("pymupdf.layout", "pymupdf_layout", "pymupdf4llm", "onnxruntime")

    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in self.BLOCKED):
            return importlib.machinery.ModuleSpec(name, _Unavailable())
        return None


if os.environ.get("FOLIANT_LAYOUT") != "1" and not any(isinstance(f, _NoLayoutAnalysis) for f in sys.meta_path):
    sys.meta_path.insert(0, _NoLayoutAnalysis())
