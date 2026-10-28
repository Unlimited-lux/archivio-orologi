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
from .analysis import (
    REASON_UNDECODABLE, GroupingError, analyze_page, apply_grouping, frame_matrix, line_key, merge_blocks,
    page_has_images, unframe_matrix,
)
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
        # raggruppamento scelto dall'utente (TXT-09): (id documento, pagina) -> (unioni, divisioni)
        self._grouping: dict[tuple, tuple[list, set]] = {}
        self._grouping_version = 0
        self._filtered: dict[int, object] = {}
        self._ocr_asked: set[tuple] = set()
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
        vp = view.viewport()
        if not vp.property("textEditDrops"):
            vp.setProperty("textEditDrops", True)
            vp.setAcceptDrops(True)
            vp.installEventFilter(self)
            self._filtered[id(vp)] = view

    # ------------------------------------------------------------ testo formattato (TXT-19)
    def _drop_target_active(self, view) -> bool:
        tools = self._tools.get(id(view))
        return tools is not None and view.tool in tools

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        et = event.type()
        if et not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        view = self._filtered.get(id(obj))
        if view is None or not self._drop_target_active(view):
            return False
        mime = event.mimeData()
        supported = mime.hasText() or mime.hasHtml() or mime.hasFormat("text/rtf") or any(
            u.isLocalFile() and u.toLocalFile().lower().endswith((".rtf", ".txt", ".html", ".htm"))
            for u in mime.urls())
        if not supported:
            return False
        if et != QEvent.Type.Drop:
            event.acceptProposedAction()
            return True
        hit = view.view_to_page(event.position())
        if hit is None:
            return False
        event.acceptProposedAction()
        self.insert_formatted(view, mime, hit[0], hit[1] * self.frame(hit[0], view))
        return True

    def insert_formatted(self, view, mime, page: int, top_left: pymupdf.Point):
        """Nuovo blocco dal contenuto trascinato o incollato, nel punto indicato (coordinate di lavoro)."""
        from .rich_import import MAX_SIZE_BYTES, from_mime
        try:
            imported = from_mime(mime, self.default_style)
        except Exception as e:
            self.ctx.error(_("Inserisci testo"), _("Il contenuto non può essere letto: {e}").format(e=e))
            return None
        if imported is None or not imported.text.strip():
            return None
        if len(imported.text.encode("utf-8")) > MAX_SIZE_BYTES and not self.ctx.confirm(
                _("Inserisci testo"), _("Il testo supera 1 MB. Inserirlo comunque?")):
            return None
        if not self.ensure_editable(view):
            return None
        self.commit_editor(view)
        self._last_page = page
        result = self._apply(view, _("Inserisci testo"), lambda pdf: engine.add_text(
            pdf, page, top_left, imported.paragraphs, align=imported.align))
        if result is not None:
            if imported.omitted:
                self.ctx.status(_("Testo inserito; elementi omessi: {n} (immagini o oggetti).").format(
                    n=len(imported.omitted)), 10000)
            nb = self._refind(view, page, result.bbox)
            if nb is not None:
                self.edit_tool(view).select(view, page, nb)
        return result

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
            try:
                with ENGINE_LOCK:
                    p = view.document.pdf[page]
                    hit = (frame_matrix(p), unframe_matrix(p))
            except Exception:
                ident = pymupdf.Matrix(1, 0, 0, 1, 0, 0)
                return ident, ident     # pagina non leggibile: nessuna trasformazione
            self._frames[key] = hit
        return hit

    def blocks(self, view, page: int, compute: bool = True) -> list[TextBlock]:
        doc = view.document
        key = (id(doc), doc.version, page, self.granularity, self._grouping_version)
        hit = self._cache.get(key)
        if hit is None and compute:
            if doc.closed or page >= doc.page_count:
                return []
            with ENGINE_LOCK:
                try:
                    hit = analyze_page(doc.pdf[page], page, self.granularity)
                except Exception:
                    hit = []
            grouping = self._grouping.get((id(doc), page))
            if grouping and hit:
                hit = apply_grouping(hit, page, grouping[0], grouping[1])
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
            tool[0].multi = []
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
        try:
            with ENGINE_LOCK:
                images = page_has_images(view.document.pdf[page])
        except Exception:
            self.ctx.status(_("Pagina non leggibile: il file è danneggiato in questo punto."), 10000)
            return
        if images:
            self.ctx.status(_("Questa pagina non contiene testo riconosciuto (forse è una scansione): "
                              "usa l'OCR per renderla modificabile."), 10000)
            self.ocr_suggested = page
            self.propose_ocr(view, page)
        else:
            self.ocr_suggested = None

    def propose_ocr(self, view, page: int) -> bool:
        """Proposta accettabile di OCR modificabile della pagina scansionata (TXT-15), una volta per pagina."""
        key = (id(view.document), page)
        if key in self._ocr_asked:
            return False
        self._ocr_asked.add(key)
        if not self.ctx.confirm(_("Pagina senza testo"),
                                _("La pagina {n} sembra una scansione e non contiene testo modificabile.\n\n"
                                  "Eseguire ora l'OCR della pagina per rendere il testo modificabile?")
                                .format(n=page + 1)):
            return False
        ocr = getattr(self.ctx, "ocr", None)
        if ocr is None or not hasattr(ocr, "run"):
            # modulo OCR non disponibile con API diretta: si apre la sua modalità
            if self.ctx.mode("ocr") is not None:
                self.ctx.set_mode("ocr")
            return False
        try:
            from ..ocr import engine as ocr_engine
            opts = ocr_engine.OcrOptions(mode=ocr_engine.EDITABLE)
            ocr.run(view, opts, [page], preview=False)
        except Exception as e:
            self.ctx.error(_("OCR"), _("Operazione non riuscita: {e}").format(e=e))
            return False
        return bool(self.blocks(view, page))

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
        if getattr(result, "orphans", 0):
            self.ctx.status(_("{n} annotazioni non trovano più il loro testo e restano al loro posto (orfane).")
                            .format(n=result.orphans), 10000)
        marks = []
        if getattr(result, "overflow", False):
            marks.append((self._last_page, pymupdf.Rect(result.bbox)))
            self.ctx.status(_("Il testo esce dall'area visibile della pagina."), 8000)
        elif getattr(result, "changed", False) and result.bbox is not None and not result.bbox.is_empty:
            # sovrapposizione con altri blocchi (TXT-03): nessun altro contenuto viene spostato
            mine = self._refind(view, self._last_page, result.bbox)
            others = [b for b in self.blocks(view, self._last_page) if b is not mine]
            hit = pymupdf.Rect(result.bbox) + (0.5, 0.5, -0.5, -0.5)
            if any(hit.intersects(b.bbox) for b in others):
                marks.append((self._last_page, pymupdf.Rect(result.bbox)))
                self.ctx.status(_("Il testo si sovrappone ad altro contenuto della pagina."), 8000)
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

    def _attach_spellcheck(self, view, editor) -> None:
        try:
            from .spell import choose_language, shared_service
            from .spell_ui import attach_spellcheck
            svc = shared_service()
            with ENGINE_LOCK:
                lang = choose_language(svc, view.document.pdf)
            attach_spellcheck(editor, svc, lang, status=self.ctx.status)
        except Exception:
            pass   # controllo ortografico non disponibile: la modifica funziona comunque

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
        self._attach_spellcheck(view, editor)
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
        self._attach_spellcheck(view, editor)
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
        indent, space_after = editor.indent, editor.space_after
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
            tools = self._tools.get(id(view))
            if tools is not None and view.tool is tools[1] and not tools[1].sticky:
                # alla conferma si torna a «Modifica testo» (TXT-06)
                view.set_tool(tools[0])
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
                    and align == block.align and abs(leading - block.leading) < 0.01
                    and abs(indent - block.indent) < 0.01 and space_after < 0.01):
                result = None
            else:
                result = self._apply(view, _("Modifica testo"), lambda pdf: engine.replace_block(
                    pdf, page, block, paragraphs, align=align, leading=leading, indent=indent,
                    space_after=space_after))
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

    def reformat_selected(self, view, align: str | None = None, leading: float | None = None,
                          indent: float | None = None, space_after: float = 0.0):
        tool = self.edit_tool(view)
        if tool.selected is None:
            return None
        page, block = tool.selected
        if not self.ensure_editable(view):
            return None
        self._last_page = page
        result = self._apply(view, _("Formatta paragrafo"), lambda pdf: engine.replace_block(
            pdf, page, block, block.paragraphs, align=align, leading=leading, indent=indent,
            space_after=space_after, keep_unchanged=False))
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
            # testo da altre applicazioni: nuovo blocco al centro della vista (TXT-19)
            mime = QGuiApplication.clipboard().mimeData()
            if mime is None or not (mime.hasText() or mime.hasHtml() or mime.hasFormat("text/rtf")):
                return False
            vp = view.viewport()
            hit = view.view_to_page(QPointF(vp.width() / 2, vp.height() / 3), clamp=True)
            if hit is None:
                return False
            self.insert_formatted(view, mime, hit[0], hit[1] * self.frame(hit[0], view))
            return True
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

    # ------------------------------------------------------------ selezione multipla
    def selected_blocks(self, view) -> list[tuple[int, TextBlock]]:
        tool = self.edit_tool(view)
        items = list(tool.multi)
        if tool.selected is not None and all(b is not tool.selected[1] for _p, b in items):
            items.insert(0, tool.selected)
        return items

    def _multi_apply(self, view, label: str, fn, reselect: bool = True):
        items = self.selected_blocks(view)
        if not items or not self.ensure_editable(view):
            return None
        page = items[0][0]
        blocks = [b for p, b in items if p == page]
        result = self._apply(view, label, lambda pdf: fn(pdf, page, blocks) or engine.EditResult(changed=True))
        tool = self.edit_tool(view)
        bboxes = [pymupdf.Rect(b.bbox) for b in blocks]
        tool.multi = []
        tool.selected = None
        if result is not None and reselect:
            found = []
            for b in self.blocks(view, page):
                if any((pymupdf.Rect(b.bbox) & r).get_area() > 0 for r in bboxes):
                    found.append((page, b))
            tool.multi = found
        self.selection_changed(view, tool.selected)
        view.viewport().update()
        return result

    def delete_selected(self, view):
        return self._multi_apply(view, _("Elimina blocchi di testo"),
                                 lambda pdf, page, blocks: [engine.delete_block(pdf, page, b) for b in blocks] and None,
                                 reselect=False)

    def move_selected(self, view, dx: float, dy: float):
        items = self.selected_blocks(view)
        if len(items) <= 1:
            if items:
                return self.move_block(view, items[0][0], items[0][1], dx, dy)
            return None
        tool = self.edit_tool(view)
        result = self._multi_apply(view, _("Sposta blocchi di testo"), lambda pdf, page, blocks: [
            engine.move_block(pdf, page, b, dx, dy) for b in blocks] and None, reselect=False)
        if result is not None:
            page = items[0][0]
            moved = [pymupdf.Rect(b.bbox.x0 + dx, b.bbox.y0 + dy, b.bbox.x1 + dx, b.bbox.y1 + dy) for _p, b in items]
            tool.multi = [(page, nb) for r in moved if (nb := self._refind(view, page, r)) is not None]
            view.viewport().update()
        return result

    def align_selected(self, view, mode: str, to_page: bool = False):
        items = self.selected_blocks(view)
        if not items or (len(items) < 2 and not to_page):
            return None
        reference = None
        if to_page:
            with ENGINE_LOCK:
                reference = engine.visible_area(view.document.pdf[items[0][0]])
        return self._multi_apply(view, _("Allinea blocchi"), lambda pdf, page, blocks: engine.align_blocks(
            pdf, page, blocks, mode, reference) and None)

    def distribute_selected(self, view, axis: str):
        if len(self.selected_blocks(view)) < 3:
            return None
        return self._multi_apply(view, _("Distribuisci blocchi"),
                                 lambda pdf, page, blocks: engine.distribute_blocks(pdf, page, blocks, axis))

    def rotate_selected(self, view, degrees: float):
        return self._multi_apply(view, _("Ruota blocco di testo"), lambda pdf, page, blocks: [
            engine.rotate_block(pdf, page, b, degrees) for b in blocks] and None)

    def restack_selected(self, view, front: bool):
        return self._multi_apply(view, _("Porta in primo piano") if front else _("Porta sullo sfondo"),
                                 lambda pdf, page, blocks: [engine.restack_block(pdf, page, b, front)
                                                            for b in blocks] and None)

    # ------------------------------------------------------------ unione e divisione (TXT-09)
    def merge_selected(self, view, separator: str = "space") -> TextBlock | None:
        items = self.selected_blocks(view)
        if len(items) < 2:
            return None
        if len({p for p, _b in items}) != 1:
            self.ctx.error(_("Unisci blocchi"), _("Si possono unire solo blocchi della stessa pagina."))
            return None
        page = items[0][0]
        blocks = [b for _p, b in items]
        try:
            merged = merge_blocks(blocks, separator)
        except GroupingError:
            self.ctx.error(_("Unisci blocchi"), _("I blocchi hanno orientamento o tipo diverso: non possono "
                                                  "essere uniti."))
            return None
        key = (id(view.document), page)
        merges, splits = self._grouping.setdefault(key, ([], set()))
        group = tuple(line_key(page, ln) for ln in merged.lines)
        splits.difference_update(group)
        merges.append((group, separator))
        self._grouping_version += 1
        tool = self.edit_tool(view)
        tool.multi = []
        nb = self._refind(view, page, merged.bbox)
        tool.select(view, page, nb)
        view.viewport().update()
        return nb

    def split_block_at(self, view, page: int, block: TextBlock, line_index: int) -> bool:
        if line_index <= 0 or line_index >= len(block.lines):
            return False
        key = (id(view.document), page)
        merges, splits = self._grouping.setdefault(key, ([], set()))
        lk = line_key(page, block.lines[line_index])
        merges[:] = [(g, sep) for g, sep in merges if lk not in g]
        splits.add(lk)
        self._grouping_version += 1
        self.edit_tool(view).select(view, page, None)
        view.viewport().update()
        return True

    def redetect(self, view, page: int) -> None:
        """«Rileva di nuovo»: torna al raggruppamento automatico (il testo non cambia)."""
        self._grouping.pop((id(view.document), page), None)
        self._grouping_version += 1
        self.edit_tool(view).select(view, page, None)
        view.viewport().update()
