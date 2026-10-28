"""Coordinate geospaziali dei PDF (VIS-39): viewport `/VP` delle pagine con misura
`/Measure /Subtype /GEO`. Logica pura; le funzioni che ricevono `pdf` o `page` vanno
chiamate con ENGINE_LOCK.

Ogni viewport ha un riquadro `/BBox` nello spazio PDF della pagina; `/LPTS` sono punti del
quadrato unitario che copre il riquadro e `/GPTS` le corrispondenti coordinate geografiche
(latitudine, longitudine). La corrispondenza è una trasformazione proiettiva calcolata dai
punti di controllo (affine con 3 punti, esatta con 4, ai minimi quadrati con di più)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"


@dataclass
class GeoViewport:
    bbox: pymupdf.Rect                      # spazio PDF (origine in basso a sinistra)
    coeffs: tuple[float, ...]               # trasformazione (u, v) -> (lat, lon)
    name: str = ""

    def contains(self, pdf_point: pymupdf.Point) -> bool:
        b = self.bbox
        return b.x0 <= pdf_point.x <= b.x1 and b.y0 <= pdf_point.y <= b.y1

    def latlon(self, pdf_point: pymupdf.Point) -> tuple[float, float]:
        b = self.bbox
        u = (pdf_point.x - b.x0) / (b.x1 - b.x0) if b.x1 != b.x0 else 0.0
        v = (pdf_point.y - b.y0) / (b.y1 - b.y0) if b.y1 != b.y0 else 0.0
        return _apply(self.coeffs, u, v)


def _apply(c: tuple[float, ...], u: float, v: float) -> tuple[float, float]:
    a, b, cc, d, e, f, g, h = c
    w = g * u + h * v + 1.0
    return (a * u + b * v + cc) / w, (d * u + e * v + f) / w


def _solve(m: list[list[float]], rhs: list[float]) -> list[float] | None:
    """Sistema lineare (eliminazione di Gauss con pivot parziale)."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(m)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-15:
            return None
        a[col], a[piv] = a[piv], a[col]
        for r in range(n):
            if r != col:
                k = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= k * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def fit_transform(local: list[tuple[float, float]], geo: list[tuple[float, float]]) -> tuple[float, ...] | None:
    """Coefficienti (a..h) della trasformazione proiettiva (u, v) -> (lat, lon)."""
    n = min(len(local), len(geo))
    if n < 3:
        return None
    if n == 3:                       # affine: g = h = 0
        rows, rhs = [], []
        for (u, v), (lat, lon) in zip(local, geo):
            rows.append([u, v, 1, 0, 0, 0])
            rhs.append(lat)
            rows.append([0, 0, 0, u, v, 1])
            rhs.append(lon)
        sol = _solve(rows, rhs)
        return None if sol is None else (*sol, 0.0, 0.0)
    rows, rhs = [], []
    for (u, v), (lat, lon) in zip(local[:n], geo[:n]):
        rows.append([u, v, 1, 0, 0, 0, -u * lat, -v * lat])
        rhs.append(lat)
        rows.append([0, 0, 0, u, v, 1, -u * lon, -v * lon])
        rhs.append(lon)
    if n > 4:                        # minimi quadrati: equazioni normali
        ata = [[sum(r[i] * r[j] for r in rows) for j in range(8)] for i in range(8)]
        atb = [sum(r[i] * y for r, y in zip(rows, rhs)) for i in range(8)]
        sol = _solve(ata, atb)
    else:
        sol = _solve(rows, rhs)
    return None if sol is None else tuple(sol)


def _numbers(text: str | None) -> list[float]:
    return [float(x) for x in re.findall(_NUM, text or "")]


def _resolve(pdf: pymupdf.Document, value: str) -> str:
    m = re.fullmatch(r"\s*(\d+)\s+0\s+R\s*", value or "")
    return pdf.xref_object(int(m.group(1)), compressed=True) if m else (value or "")


def _balanced(text: str, start: int, open_: str, close: str) -> str:
    depth, i = 0, start
    while i < len(text):
        if text.startswith(open_, i):
            depth += 1
            i += len(open_)
            continue
        if text.startswith(close, i):
            depth -= 1
            i += len(close)
            if depth == 0:
                return text[start:i]
            continue
        i += 1
    return text[start:]


def _value(obj: str, key: str) -> str | None:
    m = re.search(r"/" + key + r"(?![A-Za-z0-9])\s*", obj)
    if not m:
        return None
    i = m.end()
    if obj.startswith("<<", i):
        return _balanced(obj, i, "<<", ">>")
    if obj.startswith("[", i):
        return _balanced(obj, i, "[", "]")
    m2 = re.match(r"\d+\s+0\s+R|/[^\s/<>\[\]()]+|\([^)]*\)|[^\s/<>\[\]()]+", obj[i:])
    return m2.group(0) if m2 else None


def geo_viewports(pdf: pymupdf.Document, page_index: int) -> list[GeoViewport]:
    """Viewport geospaziali della pagina (vuoto se non ce ne sono)."""
    try:
        page_obj = pdf.xref_object(pdf.page_xref(page_index), compressed=True)
    except Exception:
        return []
    vp = _resolve(pdf, _value(page_obj, "VP") or "")
    if not vp.startswith("["):
        return []
    body = vp[1:-1]
    items: list[str] = []
    i = 0
    while i < len(body):
        if body.startswith("<<", i):
            d = _balanced(body, i, "<<", ">>")
            items.append(d)
            i += len(d)
            continue
        m = re.match(r"(\d+)\s+0\s+R", body[i:])
        if m:
            items.append(_resolve(pdf, m.group(0)))
            i += m.end()
            continue
        i += 1
    out = []
    for d in items:
        measure = _resolve(pdf, _value(d, "Measure") or "")
        if "/GEO" not in measure:
            continue
        bbox = _numbers(_value(d, "BBox"))
        gpts = _numbers(_resolve(pdf, _value(measure, "GPTS") or ""))
        lpts = _numbers(_resolve(pdf, _value(measure, "LPTS") or ""))
        if len(bbox) != 4 or len(gpts) < 6:
            continue
        geo = list(zip(gpts[0::2], gpts[1::2]))
        local = list(zip(lpts[0::2], lpts[1::2])) if len(lpts) >= 6 else \
            [(0, 0), (0, 1), (1, 1), (1, 0)][:len(geo)]
        coeffs = fit_transform(local, geo)
        if coeffs is None:
            continue
        name = _value(d, "Name") or ""
        out.append(GeoViewport(pymupdf.Rect(bbox).normalize(), coeffs, name.strip("()")))
    return out


def latlon_at(pdf: pymupdf.Document, page_index: int, page_point: pymupdf.Point,
              viewports: list[GeoViewport] | None = None) -> tuple[float, float] | None:
    """Latitudine e longitudine sotto un punto della pagina (coordinate di MuPDF, origine in
    alto a sinistra): None fuori dai viewport geospaziali. Con più viewport vale l'ultimo che
    contiene il punto (quello disegnato sopra)."""
    vps = geo_viewports(pdf, page_index) if viewports is None else viewports
    if not vps:
        return None
    pdf_point = page_point * ~pdf[page_index].transformation_matrix
    for vp in reversed(vps):
        if vp.contains(pdf_point):
            return vp.latlon(pdf_point)
    return None


def format_latlon(lat: float, lon: float, decimals: int = 6) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.{decimals}f}° {ns}  {abs(lon):.{decimals}f}° {ew}"
