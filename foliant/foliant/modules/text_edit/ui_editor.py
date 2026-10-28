"""Riquadro di modifica in loco (TXT-02, TXT-03): un QTextEdit sovrapposto alla pagina.

Il riquadro usa gli stessi programmi di font che verranno scritti nel PDF
(caricati in Qt dai dati del font), la stessa dimensione scalata con lo zoom,
lo stesso colore, la stessa interlinea e la stessa larghezza del blocco.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QFontMetricsF, QKeySequence, QPalette, QTextBlockFormat, QTextCharFormat,
    QTextCursor, QTextFormat, QTextOption,
)
from PySide6.QtWidgets import QFrame, QTextEdit

from ...core.engine import ENGINE_LOCK
from ...ui.document_view import PX_PER_PT
from .fonts import FontResolver
from .model import (
    ALIGN_CENTER, ALIGN_JUSTIFY, ALIGN_LEFT, ALIGN_RIGHT, SOFT_HYPHEN, Run, Style, merge_runs,
)

STYLE_PROP = QTextFormat.Property.UserProperty + 17
MIME_RUNS = "application/x-foliant-text-runs"

_QT_ALIGN = {
    ALIGN_LEFT: Qt.AlignmentFlag.AlignLeft,
    ALIGN_CENTER: Qt.AlignmentFlag.AlignHCenter,
    ALIGN_RIGHT: Qt.AlignmentFlag.AlignRight,
    ALIGN_JUSTIFY: Qt.AlignmentFlag.AlignJustify,
}


class QtFontRegistry:
    """Carica in Qt i font usati per scrivere, così il riquadro mostra i glifi reali."""

    def __init__(self) -> None:
        self._families: dict[str, str] = {}

    def family_for(self, resolver: FontResolver, style: Style) -> str:
        with ENGINE_LOCK:
            try:
                choice = resolver.primary(style)
            except Exception:
                return style.font
            key = choice.key
            if key in self._families:
                return self._families[key]
            try:
                data = choice.data()
            except Exception:
                data = b""
        family = style.font
        if data:
            fid = QFontDatabase.addApplicationFontFromData(data)
            if fid >= 0:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    family = fams[0]
        self._families[key] = family
        return family


REGISTRY = QtFontRegistry()


@dataclass
class EditorSpec:
    """Dove e come aprire il riquadro."""

    page: int
    paragraphs: list            # list[list[Run]]
    x0: float                   # bordo sinistro (riquadro di lavoro, punti)
    first_baseline: float
    width: float
    align: str = ALIGN_LEFT
    leading: float = 14.4
    indent: float = 0.0
    auto_width: bool = False
    cover: QRectF | None = None  # area originale da coprire (punti, riquadro di lavoro)
    default_style: Style | None = None


class InlineEditor(QTextEdit):
    """Editor di testo ricco posizionato sopra il blocco."""

    committed = Signal()
    cancelled = Signal()
    escapePressed = Signal()

    def __init__(self, view, spec: EditorSpec, resolver: FontResolver, to_view, background: QColor):
        super().__init__(view.viewport())
        self.view = view
        self.spec = spec
        self.resolver = resolver
        self._to_view = to_view          # funzione: QRectF in punti (riquadro) -> QRectF viewport
        self._styles: list[Style] = []
        self._families: dict[Style, str] = {}
        self._scale = view.zoom * PX_PER_PT
        self.align = spec.align
        self.leading = spec.leading
        self.indent = spec.indent
        self.space_after = 0.0
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAcceptRichText(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(0)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base, background)
        pal.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
        self.setPalette(pal)
        self.setStyleSheet(f"QTextEdit {{ background: {background.name()}; border: none; padding: 0; margin: 0; }}")
        self.setAutoFillBackground(True)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self._load(spec.paragraphs)
        # ricalcolo differito: cambiare il ritorno a capo durante l'inserimento del testo non è sicuro
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit)
        self.document().contentsChanged.connect(self._fit_timer.start)
        view.horizontalScrollBar().valueChanged.connect(self.reposition)
        view.verticalScrollBar().valueChanged.connect(self.reposition)
        view.zoomChanged.connect(self._on_zoom)
        self.document().setModified(False)
        self.document().clearUndoRedoStacks()
        self._fit()

    # ------------------------------------------------------------ scala
    @property
    def scale(self) -> float:
        """Pixel del viewport per punto PDF (dei formati attuali)."""
        return self._scale

    def _qt_points(self, size_pt: float) -> float:
        dpi = self.logicalDpiY() or 96
        return size_pt * self.scale * 72.0 / dpi

    def _pdf_points(self, qt_pt: float) -> float:
        dpi = self.logicalDpiY() or 96
        return qt_pt * dpi / 72.0 / self.scale

    # ------------------------------------------------------------ formati
    def style_index(self, style: Style) -> int:
        try:
            return self._styles.index(style)
        except ValueError:
            self._styles.append(style)
            return len(self._styles) - 1

    def family(self, style: Style) -> str:
        fam = self._families.get(style)
        if fam is None:
            fam = REGISTRY.family_for(self.resolver, style)
            self._families[style] = fam
        return fam

    def char_format(self, style: Style) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFontFamilies([self.family(style)])
        fmt.setFontPointSize(self._qt_points(style.size))
        fmt.setForeground(QColor.fromRgbF(*[max(0.0, min(1.0, c)) for c in style.color[:3]]))
        fmt.setFontWeight(QFont.Weight.Bold if style.bold else QFont.Weight.Normal)
        fmt.setFontItalic(style.italic)
        fmt.setFontUnderline(style.underline)
        fmt.setFontStrikeOut(style.strike)
        fmt.setFontHintingPreference(QFont.HintingPreference.PreferNoHinting)
        fmt.setFontKerning(False)
        if style.char_spacing:
            fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
            fmt.setFontLetterSpacing(style.char_spacing * self.scale)
        fmt.setProperty(STYLE_PROP, self.style_index(style))
        return fmt

    def block_format(self, first: bool) -> QTextBlockFormat:
        bf = QTextBlockFormat()
        bf.setAlignment(_QT_ALIGN.get(self.align, Qt.AlignmentFlag.AlignLeft))
        bf.setLineHeight(self.leading * self.scale, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        bf.setTextIndent(self.indent * self.scale)
        bf.setTopMargin(0)
        bf.setBottomMargin(self.space_after * self.scale)
        return bf

    def _load(self, paragraphs: list[list[Run]]) -> None:
        doc = self.document()
        doc.clear()
        cursor = QTextCursor(doc)
        default = self.spec.default_style or Style()
        for i, para in enumerate(paragraphs or [[]]):
            if i:
                cursor.insertBlock(self.block_format(False))
            else:
                cursor.setBlockFormat(self.block_format(True))
            runs = merge_runs(para)
            if not runs:
                # paragrafo vuoto: lo stile del paragrafo vicino o quello predefinito
                st = self._nearest_style(paragraphs, i) or default
                cursor.setBlockCharFormat(self.char_format(st))
                cursor.setCharFormat(self.char_format(st))
            for run in runs:
                cursor.insertText(run.text, self.char_format(run.style))
        if not any(merge_runs(p) for p in paragraphs or [[]]):
            fmt = self.char_format(default)
            c = QTextCursor(doc)
            c.setBlockCharFormat(fmt)
            self.setCurrentCharFormat(fmt)
        self.moveCursor(QTextCursor.MoveOperation.End)

    @staticmethod
    def _nearest_style(paragraphs, i) -> Style | None:
        for para in list(paragraphs[i::-1]) + list(paragraphs[i:]):
            for run in para:
                return run.style
        return None

    def _style_of(self, fmt: QTextCharFormat) -> Style:
        """Ricostruisce lo stile PDF dal formato Qt (con le modifiche dell'utente)."""
        idx = fmt.property(STYLE_PROP)
        base = self._styles[idx] if isinstance(idx, int) and 0 <= idx < len(self._styles) else (
            self.spec.default_style or Style())
        changes = {}
        size = round(self._pdf_points(fmt.fontPointSize()), 2) if fmt.fontPointSize() > 0 else base.size
        if abs(size - base.size) > 0.02:
            changes["size"] = size
        col = fmt.foreground().color()
        rgb = (col.redF(), col.greenF(), col.blueF())
        if any(abs(a - b) > 1.5 / 255 for a, b in zip(rgb, base.color)):
            changes["color"] = tuple(round(c, 4) for c in rgb)
        bold = fmt.fontWeight() >= 600
        if bold != base.bold:
            changes["bold"] = bold
        if fmt.fontItalic() != base.italic:
            changes["italic"] = fmt.fontItalic()
        if fmt.fontUnderline() != base.underline:
            changes["underline"] = fmt.fontUnderline()
        if fmt.fontStrikeOut() != base.strike:
            changes["strike"] = fmt.fontStrikeOut()
        fams = fmt.fontFamilies() or []
        fam = fams[0] if fams else None
        if fam and fam != self.family(base):
            changes["font"] = fam
            changes["font_xref"] = 0
        if ("bold" in changes or "italic" in changes) and "font" not in changes and base.font_xref:
            # variante di stile: non si può usare il programma originale (che è di un altro stile)
            changes["font_xref"] = 0
        return base.with_(**changes) if changes else base

    # ------------------------------------------------------------ lettura
    def paragraphs(self) -> list[list[Run]]:
        out: list[list[Run]] = []
        block = self.document().begin()
        while block.isValid():
            runs: list[Run] = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    text = frag.text()
                    style = self._style_of(frag.charFormat())
                    parts = text.split(" ")
                    for k, part in enumerate(parts):
                        if k:
                            out.append(merge_runs(runs))
                            runs = []
                        if part:
                            runs.append(Run(part, style))
                it += 1
            out.append(merge_runs(runs))
            block = block.next()
        return out

    def plain_text(self) -> str:
        return "\n".join("".join(r.text for r in p) for p in self.paragraphs()).replace(SOFT_HYPHEN, "")

    # ------------------------------------------------------------ formattazione dall'esterno
    def apply_char_changes(self, **changes) -> None:
        """Applica proprietà di carattere alla selezione (o a tutto il testo se non c'è selezione)."""
        cursor = self.textCursor()
        whole = not cursor.hasSelection()
        if whole:
            cursor.select(QTextCursor.SelectionType.Document)
        # per ogni frammento selezionato ricalcola lo stile e il formato
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        edits: list[tuple[int, int, Style]] = []
        block = self.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                a, b = frag.position(), frag.position() + frag.length()
                lo, hi = max(a, start), min(b, end)
                if lo < hi:
                    style = self._style_of(frag.charFormat()).with_(**changes)
                    edits.append((lo, hi, style))
                it += 1
            block = block.next()
        cursor.beginEditBlock()
        for lo, hi, style in edits:
            c = QTextCursor(self.document())
            c.setPosition(lo)
            c.setPosition(hi, QTextCursor.MoveMode.KeepAnchor)
            c.setCharFormat(self.char_format(style))
        cursor.endEditBlock()
        # lo stile del punto d'inserimento segue la modifica
        cur = self.textCursor()
        st = self._style_of(cur.charFormat()).with_(**changes)
        self.setCurrentCharFormat(self.char_format(st))
        self._fit()

    def current_style(self) -> Style:
        return self._style_of(self.textCursor().charFormat())

    def selection_styles(self) -> list[Style]:
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return [self.current_style()]
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        out = []
        block = self.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.position() < end and frag.position() + frag.length() > start:
                    out.append(self._style_of(frag.charFormat()))
                it += 1
            block = block.next()
        return out or [self.current_style()]

    def set_alignment(self, align: str) -> None:
        self.align = align
        self._reformat_blocks()

    def set_indent(self, indent: float) -> None:
        self.indent = max(0.0, indent)
        self._reformat_blocks()

    def set_space_after(self, space: float) -> None:
        self.space_after = max(0.0, space)
        self._reformat_blocks()

    def set_leading(self, leading: float) -> None:
        self.leading = max(0.1, leading)
        self._reformat_blocks()

    def _reformat_blocks(self) -> None:
        c = QTextCursor(self.document())
        c.beginEditBlock()
        block = self.document().begin()
        first = True
        while block.isValid():
            c.setPosition(block.position())
            c.setBlockFormat(self.block_format(first))
            first = False
            block = block.next()
        c.endEditBlock()
        self._fit()

    # ------------------------------------------------------------ geometria
    def _first_ascent(self) -> float:
        block = self.document().begin()
        layout = block.layout()
        if layout is not None and layout.lineCount() > 0:
            return layout.lineAt(0).ascent()
        fmt = self.currentCharFormat()
        return QFontMetricsF(fmt.font()).ascent()

    def content_rect_points(self) -> QRectF:
        """Area occupata dal testo (punti, riquadro di lavoro)."""
        s = self.scale
        wrap = getattr(self, "_wrap_px", None)
        ideal = self.document().idealWidth() if (self.spec.auto_width and wrap is None) else (
            wrap if wrap is not None else self.spec.width * s)
        h = self.document().size().height()
        asc = self._first_ascent()
        top = self.spec.first_baseline - asc / s
        x0 = self.spec.x0
        if self.spec.auto_width and self.align == ALIGN_RIGHT:
            x0 = self.spec.x0 + self.spec.width - ideal / s
        elif self.spec.auto_width and self.align == ALIGN_CENTER:
            x0 = self.spec.x0 + self.spec.width / 2 - ideal / (2 * s)
        return QRectF(x0, top, ideal / s, h / s)

    def _limit_points(self) -> float:
        """Larghezza disponibile fino al bordo dell'area visibile (blocchi a larghezza automatica)."""
        try:
            pw, _ph = self.view.page_size(self.spec.page)
        except Exception:
            return 1e6
        if self.align == ALIGN_RIGHT:
            return max(1.0, self.spec.x0 + self.spec.width)
        if self.align == ALIGN_CENTER:
            c = self.spec.x0 + self.spec.width / 2
            return max(1.0, 2 * min(c, pw - c))
        return max(1.0, pw - self.spec.x0)

    def _fit(self) -> None:
        if getattr(self, "_fitting", False):
            return
        self._fitting = True
        try:
            self._do_fit()
        except RuntimeError:
            pass   # vista già chiusa
        finally:
            self._fitting = False

    def _do_fit(self) -> None:
        s = self.scale
        self._wrap_px = None
        if self.spec.auto_width:
            self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
            limit = self._limit_points() * s
            if self.document().idealWidth() > limit:
                # come nel PDF: a capo al bordo dell'area visibile
                self._wrap_px = limit
                self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
                self.setLineWrapColumnOrWidth(max(4, int(limit)))
        else:
            # stessa tolleranza dell'impaginazione del PDF (arrotondamenti delle larghezze)
            px = self.spec.width * s
            px += max(0.3, 0.03 * self.spec.width) * s + 2
            self._wrap_px = px
            self.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
            self.setLineWrapColumnOrWidth(max(4, int(px + 0.999)))
        self.reposition()

    def reposition(self) -> None:
        try:
            r = self.content_rect_points()
        except RuntimeError:
            return
        if self.spec.cover is not None:
            cover = self.spec.cover
            r = r.united(cover) if not r.isEmpty() else QRectF(cover)
        vr = self._to_view(self.spec.page, r)
        s = self.scale
        text_rect = self._to_view(self.spec.page, self.content_rect_points())
        # il testo inizia dal bordo sinistro del contenuto: spostiamo i margini interni
        left_pad = max(0.0, text_rect.left() - vr.left())
        top_pad = max(0.0, text_rect.top() - vr.top())
        self.setViewportMargins(round(left_pad), round(top_pad), 0, 0)
        w = max(vr.width(), text_rect.width() + left_pad) + 2
        h = max(vr.height(), text_rect.height() + top_pad) + 2
        self.setGeometry(round(vr.left()), round(vr.top()), round(w + 1), round(h + 1))
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)

    def _on_zoom(self, _zoom: float) -> None:
        new = self.view.zoom * PX_PER_PT
        if abs(new - self._scale) < 1e-9:
            self.reposition()
            return
        paragraphs = self.paragraphs()          # misurati con la scala precedente
        pos = self.textCursor().position()
        self._scale = new
        self._load(paragraphs)
        c = self.textCursor()
        c.setPosition(min(pos, max(0, self.document().characterCount() - 1)))
        self.setTextCursor(c)
        self._reformat_blocks()

    # ------------------------------------------------------------ eventi
    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.escapePressed.emit()
            return
        mods = ev.modifiers()
        if ev.key() == Qt.Key.Key_V and mods & Qt.KeyboardModifier.ControlModifier \
                and mods & Qt.KeyboardModifier.ShiftModifier:
            # Ctrl+Maiusc+V: testo semplice con lo stile del punto d'inserimento (TXT-18)
            self.insertPlainText(self._clipboard_text())
            return
        super().keyPressEvent(ev)

    @staticmethod
    def _clipboard_text() -> str:
        from PySide6.QtGui import QGuiApplication
        return QGuiApplication.clipboard().text()

    def createMimeDataFromSelection(self):
        """Copia: testo semplice, HTML e un formato interno con i run e i loro stili (TXT-18)."""
        mime = super().createMimeDataFromSelection()
        try:
            import json
            cursor = self.textCursor()
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            paras: list[list[dict]] = [[]]
            block = self.document().findBlock(start)
            first = True
            while block.isValid() and block.position() <= end:
                if not first:
                    paras.append([])
                first = False
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    a, b = frag.position(), frag.position() + frag.length()
                    lo, hi = max(a, start), min(b, end)
                    if lo < hi:
                        st = self._style_of(frag.charFormat())
                        paras[-1].append({"text": frag.text()[lo - a:hi - a], "style": {
                            "font": st.font, "size": st.size, "color": list(st.color), "bold": st.bold,
                            "italic": st.italic, "underline": st.underline, "strike": st.strike,
                            "char_spacing": st.char_spacing, "rise": st.rise, "serif": st.serif, "mono": st.mono,
                            "font_xref": 0}})
                    it += 1
                block = block.next()
            mime.setData(MIME_RUNS, json.dumps(paras).encode("utf-8"))
        except Exception:
            pass
        return mime

    def insertFromMimeData(self, source) -> None:
        # dall'editor: con la formattazione di origine; da altre applicazioni: testo con lo stile
        # del punto d'inserimento
        if source.hasFormat(MIME_RUNS):
            try:
                import json
                paras = json.loads(bytes(source.data(MIME_RUNS)).decode("utf-8"))
                cursor = self.textCursor()
                cursor.beginEditBlock()
                for i, para in enumerate(paras):
                    if i:
                        cursor.insertBlock(self.block_format(False))
                    for run in para:
                        sd = dict(run["style"])
                        sd["color"] = tuple(sd.get("color", (0, 0, 0)))
                        cursor.insertText(run["text"], self.char_format(Style(**sd)))
                cursor.endEditBlock()
                self.setTextCursor(cursor)
                return
            except Exception:
                pass
        if source.hasText():
            self.insertPlainText(source.text())

    def focus_at(self, viewport_pos) -> None:
        local = self.mapFrom(self.view.viewport(), QPoint(round(viewport_pos.x()), round(viewport_pos.y())))
        local -= QPoint(self.viewportMargins().left(), self.viewportMargins().top())
        cursor = self.cursorForPosition(local)
        self.setTextCursor(cursor)
        self.setFocus()

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.height())

    def detach(self) -> None:
        for sig, slot in ((self.view.horizontalScrollBar().valueChanged, self.reposition),
                          (self.view.verticalScrollBar().valueChanged, self.reposition),
                          (self.view.zoomChanged, self._on_zoom)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        QTimer.singleShot(0, self.deleteLater)
        self.hide()
