"""Test delle foto di documenti (CNV-26) con una fixture sintetica in prospettiva."""

from __future__ import annotations

import math

import numpy as np
import pymupdf
import pytest

cv2 = pytest.importorskip("cv2")

from foliant.modules.convert.photo import detect_corners, photos_to_pdf, warp  # noqa: E402


def _sheet(w=840, h=1188):
    img = np.full((h, w, 3), 255, np.uint8)
    for k in range(10):
        y = 150 + k * 90
        cv2.rectangle(img, (100, y), (w - 100, y + 18), (20, 20, 20), -1)
    return img


@pytest.fixture
def photo(tmp_path):
    sheet = _sheet()
    h, w = sheet.shape[:2]
    canvas_w, canvas_h = 1600, 1800
    corners = np.array([[380, 220], [1240, 300], [1300, 1560], [300, 1500]], dtype="float32")
    m = cv2.getPerspectiveTransform(np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype="float32"),
                                    corners)
    bg = np.full((canvas_h, canvas_w, 3), (60, 90, 70), np.uint8)
    warped = cv2.warpPerspective(sheet, m, (canvas_w, canvas_h))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (canvas_w, canvas_h))
    bg[mask > 0] = warped[mask > 0]
    path = tmp_path / "foto.png"
    cv2.imwrite(str(path), cv2.cvtColor(bg, cv2.COLOR_RGB2BGR))
    return str(path), corners, bg


def test_detect_corners(photo):
    path, ref, img = photo
    found, ok = detect_corners(img)
    assert ok
    diag = math.hypot(img.shape[1], img.shape[0])
    for a, b in zip(found, ref):
        assert math.hypot(*(a - b)) < 0.02 * diag


def test_pdf_a4_lines_horizontal(photo):
    path, ref, img = photo
    data, warnings = photos_to_pdf([{"path": path}], page_size="A4", dpi=100, filter_name="original")
    doc = pymupdf.open(stream=data)
    page = doc[0]
    assert abs(page.rect.width - 595.3) < 1 and abs(page.rect.height - 841.9) < 1
    pix = page.get_pixmap(dpi=100)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., 0]
    # prima barra scura: inclinazione dai bordi superiori a sinistra e a destra
    x_left, x_right = int(pix.width * 0.25), int(pix.width * 0.75)

    def top_edge(x):
        col = arr[:, x]
        return int(np.argmax(col < 100))
    dy = top_edge(x_right) - top_edge(x_left)
    angle = math.degrees(math.atan2(dy, x_right - x_left))
    assert abs(angle) <= 0.5


def test_not_detected_falls_back(tmp_path):
    img = np.full((300, 400, 3), 255, np.uint8)
    corners, ok = detect_corners(img)
    assert not ok and corners[2][0] == 399
    path = tmp_path / "bianca.png"
    cv2.imwrite(str(path), img)
    data, warnings = photos_to_pdf([{"path": str(path)}], page_size=None, dpi=100)
    assert pymupdf.open(stream=data).page_count == 1 and any("bordi" in w for w in warnings)
