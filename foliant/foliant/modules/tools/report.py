"""Rapporto PDF del confronto (CMP-10) e copia di B annotata.

Logica pura: produce i bytes del nuovo PDF senza modificare A né B. Il
chiamante scrive il file con `atomic_write` solo a generazione completata,
così un annullamento non lascia file parziali.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .compare_engine import (
    ADDED, ANNOTATION, DELETED, FIELD, FORMAT, IMAGE, KINDS, MODIFIED, PAGE, STATUSES, VISUAL, CompareResult,
    Difference, kind_label, open_side, status_label,
)

CONTENT_SUMMARY = "summary"
CONTENT_PAGES = "pages"       # riepilogo e pagine con differenze (predefinito)
CONTENT_ALL = "all"
LAYOUT_SIDE = "side"
LAYOUT_B = "b_only"

COLORS = {
    ADDED: (0.09, 0.55, 0.25),
    DELETED: (0.80, 0.15, 0.15),
    MODIFIED: (0.90, 0.50, 0.05),
    FORMAT: (0.50, 0.25, 0.70),
    VISUAL: (0.10, 0.40, 0.85),
    PAGE: (0.40, 0.40, 0.45),
    IMAGE: (0.05, 0.45, 0.45),
    ANNOTATION: (0.20, 0.20, 0.25),
    FIELD: (0.30, 0.30, 0.65),
}
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _font_file() -> str | None:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def suggested_name(result: CompareResult, when: datetime | None = None) -> str:
    when = when or datetime.now()

    def stem(label: str) -> str:
        base = os.path.splitext(os.path.basename(label))[0]
        return re.sub(r"[^\w\-]+", "_", base).strip("_") or "documento"
    return f"Confronto_{stem(result.side_a.label)}_{stem(result.side_b.label)}_{when:%Y%m%d}.pdf"


class _Writer:
    """Scrive righe di testo su pagine A4 che si aggiungono da sole."""

    def __init__(self, doc: pymupdf.Document):
        self.doc = doc
        self.fontfile = _font_file()
        self.font = pymupdf.Font(fontfile=self.fontfile) if self.fontfile else pymupdf.Font("helv")
        self.page = None
        self.y = 0.0
        self.margin = 50.0
        self.missing_glyphs = False
        self.new_page()

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=595.28, height=841.89)
        self.y = self.margin

    def _fit(self, text: str, size: float, width: float) -> str:
        if not self.fontfile:
            safe = "".join(c if ord(c) < 256 else "?" for c in text)
            if safe != text:
                self.missing_glyphs = True
            text = safe
        if self.font.text_length(text, fontsize=size) <= width:
            return text
        while text and self.font.text_length(text + "…", fontsize=size) > width:
            text = text[:-1]
        return text + "…"

    def line(self, text: str, size: float = 10, color=(0.1, 0.1, 0.1), indent: float = 0.0, gap: float = 4.0,
             width: float | None = None) -> pymupdf.Rect:
        h = size + gap
        if self.y + h > self.page.rect.height - self.margin:
            self.new_page()
        x = self.margin + indent
        w = width or (self.page.rect.width - self.margin - x)
        text = self._fit(text, size, w)
        tw = pymupdf.TextWriter(self.page.rect, color=color)
        tw.append((x, self.y + size), text, font=self.font, fontsize=size)
        tw.write_text(self.page)
        rect = pymupdf.Rect(x, self.y, x + w, self.y + h)
        self.y += h
        return rect

    def space(self, h: float = 8) -> None:
        self.y += h


def _side_lines(info, size_bytes: int) -> list[str]:
    lines = [
        _("Nome: {v}").format(v=info.title),
        _("Percorso: {v}").format(v=info.path or _("(documento in memoria)")),
        _("Dimensione: {v} byte · Pagine: {p} (confrontate {a}–{b})").format(
            v=f"{size_bytes:,}".replace(",", "."), p=info.page_count,
            a=info.pages[0] + 1 if info.pages else 0, b=info.pages[-1] + 1 if info.pages else 0),
        _("SHA-256: {v}").format(v=info.sha256),
    ]
    return lines


def _transform(src_page: pymupdf.Page, target: pymupdf.Rect) -> tuple[pymupdf.Matrix, pymupdf.Rect]:
    """Matrice pagina sorgente (non ruotata) -> pagina del rapporto, come show_pdf_page."""
    shown = src_page.rect   # dimensioni visualizzate (rotazione inclusa)
    scale = min(target.width / shown.width, target.height / shown.height)
    w, h = shown.width * scale, shown.height * scale
    x0 = target.x0 + (target.width - w) / 2
    y0 = target.y0 + (target.height - h) / 2
    m = src_page.rotation_matrix * pymupdf.Matrix(scale, scale) * pymupdf.Matrix(1, 0, 0, 1, x0, y0)
    return m, pymupdf.Rect(x0, y0, x0 + w, y0 + h)


def _draw_diffs(page: pymupdf.Page, diffs: list[Difference], side: str, pno: int, m: pymupdf.Matrix,
                font: pymupdf.Font) -> None:
    for d in diffs:
        color = COLORS[d.kind]
        for loc in d.locations(side):
            if loc[0] != pno:
                continue
            r = pymupdf.Rect(loc[1:5]) * m
            r.normalize()
            if r.height < 2:
                r.y1 = r.y0 + 2
            shape = page.new_shape()
            shape.draw_rect(r)
            shape.finish(color=color, fill=color, fill_opacity=0.15, width=0.8,
                         dashes="[2] 0" if d.kind == FORMAT else None)
            shape.commit()
        locs = [loc for loc in d.locations(side) if loc[0] == pno]
        if locs:
            r = pymupdf.Rect(locs[0][1:5]) * m
            r.normalize()
            tw = pymupdf.TextWriter(page.rect, color=color)
            tw.append((max(page.rect.x0 + 2, r.x0 - 1), max(8, r.y0 - 1.5)), str(d.id), font=font, fontsize=6)
            tw.write_text(page)


def build_report(progress, result: CompareResult, data_a: bytes, data_b: bytes,
                 differences: list[Difference] | None = None, content: str = CONTENT_PAGES,
                 layout: str = LAYOUT_SIDE, filters_note: str = "", password_a: str | None = None,
                 password_b: str | None = None, when: datetime | None = None) -> bytes:
    """Genera il rapporto. `differences` = differenze visibili (filtri applicati)."""
    when = when or datetime.now()
    diffs = list(result.differences if differences is None else differences)
    can_show_text = result.side_a.text_allowed and result.side_b.text_allowed
    doc_a, _x, _y = open_side(data_a, password_a)
    doc_b, _x, _y = open_side(data_b, password_b)
    out_summary = pymupdf.open()
    out_pages = pymupdf.open()
    try:
        with ENGINE_LOCK:
            writer = _Writer(out_summary)
            font = writer.font
        # ---- pagine dei risultati
        targets: dict[int, int] = {}     # id differenza -> pagina del rapporto (in out_pages)
        toc_pages: list[tuple[str, int, list[Difference]]] = []
        reproduce = content != CONTENT_SUMMARY and can_show_text
        if reproduce:
            if layout == LAYOUT_B:
                units = [(None, pb) for pb in result.side_b.pages]
            else:
                units = list(result.pairs)
                paired_a = {p for p, _q in units}
                paired_b = {q for _p, q in units}
                units += [(p, None) for p in result.side_a.pages if p not in paired_a]
                units += [(None, q) for q in result.side_b.pages if q not in paired_b]
                units.sort(key=lambda u: (u[0] if u[0] is not None else (result.partner_a(u[1]) or 0) + 0.5,
                                          u[1] if u[1] is not None else -1))
            for k, (pa, pb) in enumerate(units):
                if progress is not None:
                    progress.update(0.9 * k / max(1, len(units)), _("Rapporto: pagina {i} di {n}").format(
                        i=k + 1, n=len(units)))
                on_page = [d for d in diffs if (pa is not None and pa in d.pages("a"))
                           or (pb is not None and pb in d.pages("b"))]
                if content == CONTENT_PAGES and not on_page:
                    continue
                with ENGINE_LOCK:
                    index = _result_page(out_pages, doc_a, doc_b, pa, pb, on_page, layout, font)
                for d in on_page:
                    targets.setdefault(d.id, index)
                title = (_("Pagina {a} di A ↔ pagina {b} di B").format(
                    a=pa + 1 if pa is not None else "–", b=pb + 1 if pb is not None else "–"))
                toc_pages.append((title, index, on_page))
        # ---- riepilogo
        with ENGINE_LOCK:
            writer.line(_("Rapporto di confronto"), size=18, gap=10)
            writer.line(_("Data e ora del confronto: {d}").format(d=when.strftime("%d/%m/%Y %H:%M")), size=9)
            writer.space()
            writer.line(_("Documento A (riferimento)"), size=12, gap=6)
            for line in _side_lines(result.side_a, len(data_a)):
                writer.line(line, size=8.5, indent=10)
            writer.space(4)
            writer.line(_("Documento B (da verificare)"), size=12, gap=6)
            for line in _side_lines(result.side_b, len(data_b)):
                writer.line(line, size=8.5, indent=10)
            writer.space()
            writer.line(_("Criteri applicati"), size=12, gap=6)
            for c in result.options.criteria():
                writer.line("• " + c, size=9, indent=10)
            if filters_note:
                writer.line("• " + filters_note, size=9, indent=10)
            writer.space()
            counts = result.counts(diffs)
            writer.line(_("Differenze: {n}").format(n=len(diffs)), size=12, gap=6)
            writer.line(" · ".join(f"{counts[k]} {kind_label(k, plural=True)}" for k in KINDS), size=10, indent=10)
            by_status = {st: sum(1 for d in diffs if d.status == st) for st in STATUSES}
            if any(by_status.values()):
                writer.line(" · ".join(f"{n} {status_label(st, plural=True)}" for st, n in by_status.items()) + " · "
                            + f"{sum(1 for d in diffs if not d.status)} {status_label('', plural=True)}",
                            size=9, indent=10)
            for w in result.warnings:
                writer.line("⚠ " + w, size=8.5, indent=10, color=(0.6, 0.3, 0.0))
            if not can_show_text:
                writer.line(_("Un documento vieta la copia del contenuto: il rapporto non riporta testo né "
                              "pagine riprodotte."), size=8.5, indent=10, color=(0.6, 0.3, 0.0))
            if not diffs:
                writer.space()
                writer.line(_("Nessuna differenza secondo i criteri selezionati."), size=11)
            else:
                writer.space()
                writer.line(_("Elenco delle differenze"), size=12, gap=6)
                links: list[tuple[int, pymupdf.Rect, int]] = []
                for d in diffs:
                    if progress is not None:
                        progress.check()
                    text = d.excerpt(90) if can_show_text else (d.detail if d.kind == VISUAL else "")
                    row = f"{d.id}. {kind_label(d.kind)} · A p. {d.page_label('a')} · B p. {d.page_label('b')}"
                    if text:
                        row += f" · {text}"
                    if d.status:
                        row += f" · {status_label(d.status)}"
                    if d.note:
                        row += f" · {_('nota')}: {d.note}"
                    rect = writer.line(row, size=8.5, indent=10, color=tuple(c * 0.8 for c in COLORS[d.kind]))
                    if d.id in targets:
                        links.append((writer.page.number, rect, targets[d.id]))
            if writer.missing_glyphs:
                writer.line(_("Attenzione: alcuni caratteri non sono disponibili nel font del rapporto e sono "
                              "mostrati come «?»."), size=8, color=(0.6, 0.3, 0.0))
            n_summary = out_summary.page_count
            if out_pages.page_count:
                out_summary.insert_pdf(out_pages)
            if diffs:
                for spage, rect, target in links:
                    out_summary[spage].insert_link({"kind": pymupdf.LINK_GOTO, "from": rect,
                                                    "page": n_summary + target, "to": pymupdf.Point(0, 0)})
            toc = [[1, _("Riepilogo"), 1]]
            for title, index, on_page in toc_pages:
                toc.append([1, title, n_summary + index + 1])
                for d in on_page:
                    toc.append([2, f"{d.id}. {kind_label(d.kind)}", n_summary + index + 1])
            out_summary.set_toc(toc)
            out_summary.set_metadata({"title": _("Rapporto di confronto"), "creator": "Foliant",
                                      "subject": f"{result.side_a.label} ↔ {result.side_b.label}"})
            data = out_summary.tobytes(garbage=3, deflate=True)
        if progress is not None:
            progress.update(1.0)
        return data
    finally:
        with ENGINE_LOCK:
            for d in (doc_a, doc_b, out_summary, out_pages):
                d.close()


def _result_page(out, doc_a, doc_b, pa, pb, diffs, layout, font) -> int:
    if layout == LAYOUT_B:
        src = doc_b[pb]
        page = out.new_page(width=595.28, height=841.89)
        area = pymupdf.Rect(30, 60, page.rect.width - 30, page.rect.height - 30)
        page.show_pdf_page(area, doc_b, pb)
        m, _r = _transform(src, area)
        _draw_diffs(page, diffs, "b", pb, m, font)
        label = _("B · pagina {b}").format(b=pb + 1)
    else:
        page = out.new_page(width=841.89, height=595.28)
        half = (page.rect.width - 90) / 2
        areas = (pymupdf.Rect(30, 60, 30 + half, page.rect.height - 30),
                 pymupdf.Rect(60 + half, 60, page.rect.width - 30, page.rect.height - 30))
        for side, doc, pno, area in (("a", doc_a, pa, areas[0]), ("b", doc_b, pb, areas[1])):
            if pno is None:
                tw = pymupdf.TextWriter(page.rect, color=(0.4, 0.4, 0.4))
                tw.append((area.x0 + 10, area.y0 + 30), _("Pagina presente solo nell'altro documento"),
                          font=font, fontsize=10)
                tw.write_text(page)
                continue
            page.show_pdf_page(area, doc, pno)
            m, shown = _transform(doc[pno], area)
            page.draw_rect(shown, color=(0.6, 0.6, 0.6), width=0.5)
            _draw_diffs(page, diffs, side, pno, m, font)
        label = _("A · pagina {a}   ↔   B · pagina {b}").format(
            a=pa + 1 if pa is not None else "–", b=pb + 1 if pb is not None else "–")
    tw = pymupdf.TextWriter(page.rect, color=(0.1, 0.1, 0.1))
    tw.append((30, 32), label, font=font, fontsize=12)
    counts = {}
    for d in diffs:
        counts[d.kind] = counts.get(d.kind, 0) + 1
    tw.append((30, 48), " · ".join(f"{v} {kind_label(k, plural=True)}" for k, v in counts.items()), font=font,
              fontsize=8)
    tw.write_text(page)
    return page.number


TEXT_KINDS = (ADDED, DELETED, MODIFIED)


def build_annotated_b(progress, result: CompareResult, data_b: bytes, differences: list[Difference] | None = None,
                      password_b: str | None = None) -> tuple[bytes, int]:
    """Copia di B con un'annotazione per ogni differenza testuale visibile."""
    diffs = [d for d in (result.differences if differences is None else differences)
             if d.kind in TEXT_KINDS and d.subtype == "text"]
    doc, _x, _y = open_side(data_b, password_b)
    created = 0
    try:
        for k, d in enumerate(diffs):
            if progress is not None:
                progress.update(k / max(1, len(diffs)))
            with ENGINE_LOCK:
                annot = None
                if d.kind == DELETED:
                    if d.point_b is None:
                        continue
                    page = doc[d.point_b[0]]
                    annot = page.add_caret_annot(pymupdf.Point(d.point_b[1], (d.point_b[2] + d.point_b[4]) / 2))
                    content = _("Eliminato: {t}").format(t=d.text_a)
                elif d.rects_b:
                    page = doc[d.rects_b[0][0]]
                    quads = [pymupdf.Rect(r[1:5]).quad for r in d.rects_b if r[0] == page.number]
                    annot = page.add_highlight_annot(quads)
                    if d.kind == ADDED:
                        content = _("Aggiunto: {t}").format(t=d.text_b)
                    else:
                        annot.set_colors(stroke=COLORS[MODIFIED])
                        content = _("Modificato: «{a}» → «{b}»").format(a=d.text_a, b=d.text_b)
                if annot is None:
                    continue
                annot.set_info(content=content, title="Foliant", subject=kind_label(d.kind))
                annot.update()
                created += 1
        with ENGINE_LOCK:
            data = doc.tobytes(garbage=3, deflate=True)
        return data, created
    finally:
        with ENGINE_LOCK:
            doc.close()
