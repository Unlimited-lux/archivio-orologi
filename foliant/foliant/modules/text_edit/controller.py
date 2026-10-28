"""Coordinamento della modifica del testo: cache dei blocchi, editor, operazioni annullabili."""

from __future__ import annotations

import json
from collections import Counter

import pymupdf
from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter

from ...core.engine import ENGINE_LOCK
from ...core.errors import FoliantError
from ...i18n import _
from ...ui import theme
from . import engine
from .analysis import REASON_UNDECODABLE, analyze_page, frame_matrix, page_has_images, unframe_matrix
from .fonts import FontResolver
from .model import ALIGN_LEFT, GRANULARITY_PARAGRAPH, Run, Style, TextBlock, merge_runs
from .ui_editor import EditorSpec, InlineEditor
from .ui_tool import AddTextTool, EditTextTool

MIME_BLOCK = "application/x-foliant-text-block"


def _same_paragraphs(a: list[list[Run]], b: list[list[Run]]) -> bool:
    a = [merge_runs(p) for p in a]
    b = [merge_runs(p) for p in b]
    while a and not a[-1]:
        a.pop()
    while b and not b[-1]:
        b.pop()
    if len(a) != len(b):
        return False
    for pa, pb in zip(a, b):
        if len(pa) != len(pb):
            return False
        for ra, rb in zip(pa, pb):
            if ra.text != rb.text or not ra.style.same_look(rb.style):
                return False
    return True


class TextEditController(QObject):
    """Stato della modifica del testo per tutte le viste."""

    editorChanged = Signal(object)       # InlineEditor | None
    selectionChanged = Signal(object)    # (pagina, blocco) | None
    substitutionsChanged = Signal(object)  # list[Substitution]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.ctx = ctx
        settings = ctx.settings()
        self.granularity = str(settings.value("text_edit/granularity", GRANULARITY_PARAGRAPH))
        self.show_ocr_text = str(settings.value("text_edit/show_ocr", "false")).lower() == "true"
        self.default_style = Style(font="Helvetica", size=12.0, color=(0.0, 0.0, 0.0))
        self._cache: dict[tuple, list[TextBlock]] = {}
        self._frames: dict[tuple, tuple[pymupdf.Matrix, pymupdf.Matrix]] = {}
        self._tools: dict[int, tuple[EditTextTool, AddTextTool]] = {}
        self._editors: dict[int, tuple[InlineEditor, dict]] = {}
        self._overflow: dict[int, list[tuple[int, pymupdf.Rect]]] = {}
        self._signed_ok: set[int] = set()
        self.clipboard: dict | None = None
        self.last_substitutions: list = []
        self.last_change: tuple | None = None   # (vista, pagina, bbox) dell'ultima modifica
        ctx.documentChanged.connect(self._on_document_changed)
        ctx.documentClosed.connect(self._on_document_closed)

    # ------------------------------------------------------------ strumenti
    def tools(self, view) -> tuple[EditTextTool, AddTextTool]:
        key = id(view)
        if key not in self._tools:
            self._tools[key] = (EditTextTool(self), AddTextTool(self))
        return self._tools[key]

    def edit_tool(self, view) -> EditTextTool:
        return self.tools(view)[0]

    def add_tool(self, view) -> AddTextTool:
        return self.tools(view)[1]

    def tool_activated(self, view, tool) -> None:
        pass

    # ------------------------------------------------------------ blocchi
    def _view(self, view=None):
        return view or self.ctx.current_view()

    def frame(self, page: int, view=None) -> pymupdf.Matrix:
        return self._matrices(page, view)[0]

    def unframe(self, page: int, view=None) -> pymupdf.Matrix:
        return self._matrices(page, view)[1]

    def _matrices(self, page: int, view=None):
        view = self._view(view)
        if view is None:
            ident = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
            return ident, ident
        key = (id(view.document), view.document.version, page)
        hit = self._frames.get(key)
        if hit is None:
            with ENGINE_LOCK:
                p = view.document.pdf[page]
                hit = (frame_matrix(p), unframe_matrix(p))
            self._frames[key] = hit
        return hit

    def blocks(self, view, page: int, compute: bool = True) -> list[TextBlock]:
        doc = view.document
        key = (id(doc), doc.version, page, self.granularity)
        hit = self._cache.get(key)
        if hit is None and compute:
            if doc.closed or page >= doc.page_count:
                return []
            with ENGINE_LOCK:
                try:
                    hit = analyze_page(doc.pdf[page], page, self.granularity)
                except Exception:
                    hit = []
            if len(self._cache) > 200:
                self._cache.clear()
            self._cache[key] = hit
        return hit or []

    def set_granularity(self, value: str) -> None:
        self.granularity = value
        self.ctx.settings().setValue("text_edit/granularity", value)
        view = self.ctx.current_view()
        if view is not None:
            tool = self.edit_tool(view)
            tool.selected = None
            tool.hover = None
            view.viewport().update()

    def set_show_ocr_text(self, on: bool) -> None:
        self.show_ocr_text = on
        self.ctx.settings().setValue("text_edit/show_ocr", "true" if on else "false")
        view = self.ctx.current_view()
        if view is not None:
            view.viewport().update()

    def _on_document_changed(self, view) -> None:
        entry = self._editors.get(id(view))
        if entry is not None and entry[1]["version"] != view.document.version:
            # il documento è cambiato altrove: il riquadro non è più valido
            self.cancel_editor(view)
            self.ctx.status(_("Il documento è cambiato: la modifica in corso è stata annullata."))
        tool = self._tools.get(id(view))
        if tool is not None:
            sel = tool[0].selected
            if sel is not None:
                nb = self._refind(view, sel[0], sel[1].bbox)
                tool[0].selected = (sel[0], nb) if nb is not None else None
                self.selection_changed(view, tool[0].selected)
            tool[0].hover = None

    def _on_document_closed(self, view) -> None:
        self.cancel_editor(view)
        self._tools.pop(id(view), None)
        self._overflow.pop(id(view), None)
        doc_id = id(view.document)
        for k in [k for k in self._cache if k[0] == doc_id]:
            self._cache.pop(k, None)

    def _refind(self, view, page: int, bbox: pymupdf.Rect) -> TextBlock | None:
        best, best_iou = None, 0.0
        for b in self.blocks(view, page):
            inter = pymupdf.Rect(b.bbox) & bbox
            if inter.is_empty:
                continue
            iou = inter.get_area() / max((pymupdf.Rect(b.bbox) | bbox).get_area(), 1e-6)
            if iou > best_iou:
                best, best_iou = b, iou
        return best if best_iou > 0.3 else None

    # ------------------------------------------------------------ integrità (TXT-20)
    def ensure_editable(self, view) -> bool:
        doc = view.document
        with ENGINE_LOCK:
            pdf = doc.pdf
            perms = pdf.permissions
            encrypted = bool((pdf.metadata or {}).get("encryption")) or bool(doc.original_protection)
            try:
                sigflags = pdf.get_sigflags()
            except Exception:
                sigflags = -1
            xfa = False
            try:
                kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "AcroForm")
                if kind == "xref":
                    xfa = pdf.xref_get_key(int(val.split()[0]), "XFA")[0] != "null"
                elif kind == "dict":
                    xfa = "/XFA" in val
            except Exception:
                xfa = False
        if encrypted and perms != -1 and not (perms & pymupdf.PDF_PERM_MODIFY):
            self.ctx.error(_("Modifica non consentita"),
                           _("Le impostazioni di sicurezza del documento non consentono di modificarne il "
                             "contenuto. Aprilo con la password del proprietario per poterlo modificare."))
            return False
        if xfa:
            self.ctx.error(_("Modifica non disponibile"),
                           _("Il contenuto di questo documento è generato da un modulo dinamico (XFA) e non "
                             "può essere modificato come testo di pagina."))
            return False
        if sigflags >= 1 and id(doc) not in self._signed_ok:
            if not self.ctx.confirm(_("Documento firmato"),
                                    _("Il documento contiene firme digitali: modificare il testo le invaliderà. "
                                      "Ti consigliamo di salvare le modifiche con «Salva con nome» per "
                                      "lasciare intatto l'originale.\n\nContinuare?")):
                return False
            self._signed_ok.add(id(doc))
        return True

    # ------------------------------------------------------------ messaggi
    def explain_block(self, view, block: TextBlock) -> None:
        if block.reason == REASON_UNDECODABLE:
            if self.ctx.confirm(_("Testo non decodificabile"),
                                _("I caratteri di questo blocco non possono essere ricavati dal font (codifica "
                                  "personalizzata senza tabella dei caratteri). È possibile solo sostituirlo "
                                  "integralmente con un nuovo testo, oppure eseguire l'OCR dell'area.\n\n"
                                  "Sostituire il blocco con un nuovo testo?")):
                self.open_editor(view, block.page, block, replace_all=True)
            return
        self.ctx.status(self.block_tooltip(block) or "")

    def block_tooltip(self, block: TextBlock) -> str:
        if block.reason == REASON_UNDECODABLE:
            return _("Testo non decodificabile: il font non indica quali caratteri rappresenta. "
                     "Si può solo sostituire integralmente o eseguire l'OCR.")
        if block.invisible:
            return _("Testo OCR invisibile: la correzione non cambia l'immagine della pagina.")
        return ""

    def check_page_has_text(self, view, page: int) -> None:
        if page is None or page >= view.page_count:
            return
        if self.blocks(view, page):
            return
        with ENGINE_LOCK:
            images = page_has_images(view.document.pdf[page])
        if images:
            self.ctx.status(_("Questa pagina non contiene testo riconosciuto (forse è una scansione): "
                              "usa l'OCR per renderla modificabile."), 10000)
            self.ocr_suggested = page
        else:
            self.ocr_suggested = None

    ocr_suggested: int | None = None

    def overflow_marks(self, view) -> list[tuple[int, pymupdf.Rect]]:
        return self._overflow.get(id(view), [])

    # ------------------------------------------------------------ esecuzione annullabile
    def _apply(self, view, label: str, fn):
        doc = view.document
        try:
            with doc.change(label) as pdf:
                result = fn(pdf)
        except FoliantError as e:
            self.ctx.error(_("Modifica del testo"), str(e))
            return None
        except Exception as e:  # nessun errore deve chiudere l'applicazione
            self.ctx.error(_("Modifica del testo"), _("Operazione non riuscita: {e}").format(e=e))
            return None
        self._report(view, result)
        return result

    def _report(self, view, result) -> None:
        if result is None:
            return
        subs = list(getattr(result, "substitutions", []) or [])
        self.last_substitutions = subs
        self.substitutionsChanged.emit(subs)
        if subs:
            text = "; ".join(_("«{o}» sostituito con «{s}» per {n} caratteri").format(
                o=s.original, s=s.substitute, n=s.chars) for s in subs)
            self.ctx.status(_("Font sostitutivo: {t}").format(t=text), 10000)
        marks = []
        if getattr(result, "overflow", False):
            marks.append((self._last_page, pymupdf.Rect(result.bbox)))
            self.ctx.status(_("Il testo esce dall'area visibile della pagina."), 8000)
        self._overflow[id(view)] = marks

    _last_page = 0

    # ------------------------------------------------------------ editor
    def editor_for(self, view) -> InlineEditor | None:
        entry = self._editors.get(id(view))
        return entry[0] if entry else None

    def _to_view(self, view):
        def convert(page: int, r: QRectF) -> QRectF:
            pr = pymupdf.Rect(r.left(), r.top(), r.right(), r.bottom()) * self.unframe(page, view)
            return view.page_to_view(page, pr)
        return convert

    def sample_colors(self, view, page: int, bbox: pymupdf.Rect) -> tuple[QColor, QColor]:
        """(sfondo, colore del testo più scuro) campionati dall'immagine della pagina."""
        try:
            with ENGINE_LOCK:
                p = view.document.pdf[page]
                clip = pymupdf.Rect(bbox) * unframe_matrix(p)
                clip = clip + (-2, -2, 2, 2)
                clip &= p.rect * p.derotation_matrix if p.rotation else p.rect
                if clip.is_empty:
                    return QColor(255, 255, 255), QColor(0, 0, 0)
                pix = p.get_pixmap(clip=clip, matrix=pymupdf.Matrix(1.5, 1.5), alpha=False, annots=False)
            w, h, n = pix.width, pix.height, pix.n
            data = pix.samples
            border = Counter()
            darks = []
            for y in range(h):
                for x in range(w):
                    i = (y * w + x) * n
                    px = tuple(data[i:i + 3]) if n >= 3 else (data[i],) * 3
                    if x in (0, w - 1) or y in (0, h - 1):
                        border[px] += 1
                    darks.append(px)
            bg = border.most_common(1)[0][0] if border else (255, 255, 255)
            darks.sort(key=sum)
            fg = darks[len(darks) // 20] if darks else (0, 0, 0)
            return QColor(*bg), QColor(*fg)
        except Exception:
            return QColor(255, 255, 255), QColor(0, 0, 0)

    def open_editor(self, view, page: int, block: TextBlock, click_pos: QPointF | None = None,
                    replace_all: bool = False) -> InlineEditor | None:
        if not self.ensure_editable(view):
            return None
        self.commit_editor(view)
        if block.invisible:
            style = block.lines[0].runs[0].style.with_(color=(0.0, 0.0, 0.0))
            paragraphs = [[Run(" ".join(w.text for w in ln.words), style)] for ln in block.lines]
        elif replace_all:
            style = block.style.with_(font_xref=0)
            paragraphs = [[]]
        else:
            paragraphs = block.paragraphs
        spec = EditorSpec(page=page, paragraphs=paragraphs, x0=block.x0, first_baseline=block.first_baseline,
                          width=block.width, align=block.align, leading=block.leading, indent=block.indent,
                          auto_width=block.auto_width or block.invisible or block.rotated,
                          cover=QRectF(block.bbox.x0, block.bbox.y0, block.bbox.width, block.bbox.height),
                          default_style=block.style if not replace_all else block.style.with_(font_xref=0))
        bg, _fg = self.sample_colors(view, page, block.bbox)
        with ENGINE_LOCK:
            resolver = FontResolver(view.document.pdf)
        editor = InlineEditor(view, spec, resolver, self._to_view(view), bg)
        editor.escapePressed.connect(lambda v=view: self.commit_editor(v, select_after=True))
        info = {"page": page, "block": block, "version": view.document.version, "replace_all": replace_all}
        self._editors[id(view)] = (editor, info)
        editor.show()
        editor.raise_()
        if click_pos is not None:
            editor.focus_at(click_pos)
        else:
            editor.setFocus()
        self.editorChanged.emit(editor)
        return editor

    def open_new_text(self, view, page: int, top_left: pymupdf.Point, width: float | None) -> InlineEditor | None:
        if not self.ensure_editable(view):
            return None
        self.commit_editor(view)
        style = self.default_style
        with ENGINE_LOCK:
            resolver = FontResolver(view.document.pdf)
            try:
                asc = resolver.primary(style).font.ascender or 0.8
            except Exception:
                asc = 0.8
        spec = EditorSpec(page=page, paragraphs=[[]], x0=top_left.x, first_baseline=top_left.y + asc * style.size,
                          width=width or 0.0, align=ALIGN_LEFT, leading=round(style.size * 1.2, 3),
                          auto_width=width is None, default_style=style)
        bg, _fg = self.sample_colors(view, page, pymupdf.Rect(top_left.x, top_left.y, top_left.x + 20,
                                                              top_left.y + style.size * 1.3))
        editor = InlineEditor(view, spec, resolver, self._to_view(view), bg)
        editor.escapePressed.connect(lambda v=view: self.commit_editor(v))
        info = {"page": page, "block": None, "version": view.document.version, "top_left": top_left,
                "width": width}
        self._editors[id(view)] = (editor, info)
        editor.show()
        editor.raise_()
        editor.setFocus()
        self.editorChanged.emit(editor)
        return editor

    def cancel_editor(self, view) -> None:
        entry = self._editors.pop(id(view), None)
        if entry is not None:
            entry[0].detach()
            self.editorChanged.emit(None)

    def commit_editor(self, view, select_after: bool = False):
        """Conferma il riquadro aperto: il blocco viene riscritto nel contenuto della pagina."""
        entry = self._editors.pop(id(view), None)
        if entry is None:
            return None
        editor, info = entry
        paragraphs = editor.paragraphs()
        align, leading = editor.align, editor.leading
        editor.detach()
        self.editorChanged.emit(None)
        doc = view.document
        if doc.closed or doc.version != info["version"]:
            return None
        page = info["page"]
        block: TextBlock | None = info["block"]
        self._last_page = page
        text = "\n".join("".join(r.text for r in p) for p in paragraphs)
        if block is None:
            if not text.strip():
                return None   # casella vuota: scartata, documento invariato
            result = self._apply(view, _("Aggiungi testo"), lambda pdf: engine.add_text(
                pdf, page, info["top_left"], paragraphs, width=info["width"], align=align,
                leading=leading))
            self.default_style = next((r.style for p in paragraphs for r in p if r.text.strip()),
                                      self.default_style).with_(font_xref=0)
            return result
        if block.invisible:
            orig = "\n".join(" ".join(w.text for w in ln.words) for ln in block.lines)
            if text == orig:
                return None
            result = self._apply(view, _("Correggi testo OCR"), lambda pdf: engine.replace_invisible_block(
                pdf, page, block, text))
        else:
            if (not info.get("replace_all") and _same_paragraphs(paragraphs, block.paragraphs)
                    and align == block.align and abs(leading - block.leading) < 0.01):
                result = None
            else:
                result = self._apply(view, _("Modifica testo"), lambda pdf: engine.replace_block(
                    pdf, page, block, paragraphs, align=align, leading=leading))
        if select_after:
            nb = self._refind(view, page, result.bbox if result is not None and result.changed else block.bbox)
            if nb is not None:
                self.edit_tool(view).select(view, page, nb)
        return result

    # ------------------------------------------------------------ operazioni sui blocchi
    def selection_changed(self, view, selection) -> None:
        self.selectionChanged.emit(selection)

    def move_block(self, view, page: int, block: TextBlock, dx: float, dy: float, target_page: int | None = None):
        if not self.ensure_editable(view):
            return None
        target = page if target_page is None else target_page
        self._last_page = target
        result = self._apply(view, _("Sposta blocco di testo"),
                             lambda pdf: engine.move_block(pdf, page, block, dx, dy, target_page=target_page))
        if result is not None:
            nb = self._refind(view, target, result.bbox)
            self.edit_tool(view).select(view, target, nb)
        return result

    def resize_block(self, view, page: int, block: TextBlock, x0: float, width: float):
        if not self.ensure_editable(view):
            return None
        self._last_page = page
        result = self._apply(view, _("Ridimensiona blocco di testo"),
                             lambda pdf: engine.resize_block(pdf, page, block, x0, width))
        if result is not None:
            nb = self._refind(view, page, result.bbox)
            self.edit_tool(view).select(view, page, nb)
        return result

    def delete_block(self, view, page: int, block: TextBlock):
        if not self.ensure_editable(view):
            return None
        result = self._apply(view, _("Elimina blocco di testo"),
                             lambda pdf: engine.delete_block(pdf, page, block) or engine.EditResult(changed=True))
        self.edit_tool(view).select(view, page, None)
        return result

    def restyle_selected(self, view, **changes):
        tool = self.edit_tool(view)
        if tool.selected is None:
            return None
        page, block = tool.selected
        if not self.ensure_editable(view):
            return None
        self._last_page = page
        result = self._apply(view, _("Formatta testo"),
                             lambda pdf: engine.restyle_block(pdf, page, block, **changes))
        if result is not None:
            tool.select(view, page, self._refind(view, page, result.bbox))
        return result

    def reformat_selected(self, view, align: str | None = None, leading: float | None = None):
        tool = self.edit_tool(view)
        if tool.selected is None:
            return None
        page, block = tool.selected
        if not self.ensure_editable(view):
            return None
        self._last_page = page
        result = self._apply(view, _("Formatta paragrafo"), lambda pdf: engine.replace_block(
            pdf, page, block, block.paragraphs, align=align, leading=leading, keep_unchanged=False))
        if result is not None:
            tool.select(view, page, self._refind(view, page, result.bbox))
        return result

    def apply_substitute_to_block(self, view, substitute: str) -> None:
        """«Applica il sostituto a tutto il blocco» (TXT-10)."""
        if self.last_change is None:
            return
        tool = self.edit_tool(view)
        if tool.selected is not None:
            self.restyle_selected(view, font=substitute.split(" Regular")[0], font_xref=0)

    def copy_block(self, view, page: int, block: TextBlock) -> None:
        self.clipboard = engine.block_to_data(block)
        mime = QMimeData()
        mime.setText(block.text)
        mime.setData(MIME_BLOCK, json.dumps(self.clipboard).encode("utf-8"))
        try:
            QGuiApplication.clipboard().setMimeData(mime)
        except Exception:
            pass
        self.ctx.status(_("Blocco di testo copiato."))

    def _clipboard_block(self) -> dict | None:
        try:
            mime = QGuiApplication.clipboard().mimeData()
            if mime is not None and mime.hasFormat(MIME_BLOCK):
                return json.loads(bytes(mime.data(MIME_BLOCK)).decode("utf-8"))
        except Exception:
            pass
        return self.clipboard

    def paste_block(self, view) -> bool:
        data = self._clipboard_block()
        if data is None:
            return False
        if not self.ensure_editable(view):
            return True
        page = view.current_page
        offset = 10.0 if page == data.get("page") else 0.0
        self._last_page = page
        result = self._apply(view, _("Incolla blocco di testo"),
                             lambda pdf: engine.paste_block(pdf, page, data, dx=offset, dy=offset))
        if result is not None:
            data["page"] = page
            self.edit_tool(view).select(view, page, self._refind(view, page, result.bbox))
        return True

    def duplicate_block(self, view, page: int, block: TextBlock) -> None:
        data = engine.block_to_data(block)
        if not self.ensure_editable(view):
            return
        self._last_page = page
        result = self._apply(view, _("Duplica blocco di testo"),
                             lambda pdf: engine.paste_block(pdf, page, data, dx=10.0, dy=10.0))
        if result is not None:
            self.edit_tool(view).select(view, page, self._refind(view, page, result.bbox))

    def convert_ocr_block(self, view, page: int, block: TextBlock):
        if not self.ensure_editable(view):
            return None
        bg, fg = self.sample_colors(view, page, block.bbox)
        background = (bg.redF(), bg.greenF(), bg.blueF())
        color = (fg.redF(), fg.greenF(), fg.blueF())
        self._last_page = page
        result = self._apply(view, _("Converti in testo modificabile"), lambda pdf: engine.convert_invisible_block(
            pdf, page, block, background=background, color=color))
        if result is not None:
            self.edit_tool(view).select(view, page, self._refind(view, page, result.bbox))
        return result

    # ------------------------------------------------------------ OCR in sovrimpressione
    def paint_ocr_text(self, view, painter: QPainter, page: int, blocks: list[TextBlock]) -> None:
        col = QColor(theme.color("accent"))
        col.setAlpha(150)
        painter.save()
        painter.setPen(col)
        font = QFont("DejaVu Sans")
        font.setPixelSize(100)
        painter.setFont(font)
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(font)
        unframe = self.unframe(page, view)
        for b in blocks:
            if not b.invisible:
                continue
            for ln in b.lines:
                text = " ".join(w.text for w in ln.words)
                if not text:
                    continue
                o = ln.origin * unframe
                natural = fm.horizontalAdvance(text) / 100.0 * ln.size
                hs = ln.bbox.width / natural if natural > 0 else 1.0
                painter.save()
                painter.translate(o.x, o.y)
                if unframe.b or unframe.c:
                    painter.rotate(-90 if unframe.b < 0 else 90)
                painter.scale(ln.size / 100.0 * hs, ln.size / 100.0)
                painter.drawText(QPointF(0, 0), text)
                painter.restore()
        painter.restore()
