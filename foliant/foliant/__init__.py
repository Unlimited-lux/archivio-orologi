"""Foliant: editor PDF desktop per Linux."""

import importlib.abc
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


class _NoLayoutAnalysis(importlib.abc.MetaPathFinder):
    BLOCKED = ("pymupdf.layout", "pymupdf_layout", "pymupdf4llm", "onnxruntime")

    def find_spec(self, name, path=None, target=None):
        if any(name == b or name.startswith(b + ".") for b in self.BLOCKED):
            raise ModuleNotFoundError(f"{name}: non usato da Foliant", name=name)
        return None


if os.environ.get("FOLIANT_LAYOUT") != "1" and not any(isinstance(f, _NoLayoutAnalysis) for f in sys.meta_path):
    sys.meta_path.insert(0, _NoLayoutAnalysis())
