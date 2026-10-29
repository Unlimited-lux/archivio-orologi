"""Crea foliant.ico (16–256 px) dall'icona SVG di Foliant, per l'eseguibile e l'installatore.

Uso: python packaging/windows/make_icon.py destinazione.ico
"""

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

SVG = Path(__file__).resolve().parents[2] / "foliant" / "resources" / "foliant.svg"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> Image.Image:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(SVG)).render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def main(target: str) -> None:
    _app = QGuiApplication.instance() or QGuiApplication(["make_icon", "-platform", "offscreen"])
    images = [render(s) for s in SIZES]
    images[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"{target}: {', '.join(map(str, SIZES))} px")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "foliant.ico")
