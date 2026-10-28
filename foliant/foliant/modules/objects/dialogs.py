"""Finestre di dialogo del modulo. Ogni funzione `ask_*` si può sostituire nei test."""

from __future__ import annotations

import os

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
    QRadioButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from foliant.api import ENGINE_LOCK, _, theme

from . import engine as E
from . import links as L
from .content import FORM, IMAGE, PATH, SHADING

IMAGE_FILTER_TEXT = "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp *.svg *.jp2 *.tga *.pnm *.ppm *.pgm *.pbm *.ico"

_session_style = L.LinkStyle()
_filter_kinds = {IMAGE, PATH, FORM, SHADING}


def button_box() -> QDialogButtonBox:
    """OK / Annulla con testi tradotti (i pulsanti standard di Qt non lo sono)."""
    bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("OK"))
    bb.button(QDialogButtonBox.StandardButton.Cancel).setText(_("Annulla"))
    return bb


def select_filter() -> set:
    return set(_filter_kinds)


def set_select_filter(kinds: set) -> None:
    global _filter_kinds
    _filter_kinds = set(kinds) or {IMAGE, PATH, FORM, SHADING}


def remember_style(style: L.LinkStyle) -> None:
    global _session_style
    _session_style = L.LinkStyle(style.visible, tuple(style.color), style.width, style.style, style.highlight)


def default_style() -> L.LinkStyle:
    s = _session_style
    return L.LinkStyle(s.visible, tuple(s.color), s.width, s.style, s.highlight)


# ------------------------------------------------------------------ immagini
def ask_image_paths(ctx, multiple: bool = True, title: str | None = None) -> list[str]:
    flt = _("Immagini") + f" ({IMAGE_FILTER_TEXT});;" + _("Tutti i file") + " (*)"
    return ctx.ask_open_paths(title or _("Aggiungi immagine"), flt, multiple=multiple)


class ReplaceDialog(QDialog):
    def __init__(self, parent, occurrences: int):
        super().__init__(parent)
        self.setWindowTitle(_("Sostituisci immagine"))
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Come adattare la nuova immagine al riquadro:")))
        self.modes = QButtonGroup(self)
        for k, (mode, text) in enumerate(((E.FIT, _("Adatta (mantiene le proporzioni)")),
                                          (E.FILL, _("Riempi (ritaglia l'eccedenza)")),
                                          (E.STRETCH, _("Deforma (occupa esattamente il riquadro)")))):
            rb = QRadioButton(text)
            rb.setProperty("mode", mode)
            rb.setChecked(k == 0)
            self.modes.addButton(rb)
            lay.addWidget(rb)
        self.all = QCheckBox(_("Sostituisci tutte le {n} occorrenze nel documento").format(n=occurrences))
        self.all.setVisible(occurrences > 1)
        self.all.toggled.connect(self._on_all)
        lay.addWidget(self.all)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _on_all(self, on: bool) -> None:
        for b in self.modes.buttons():
            b.setEnabled(not on or b.property("mode") == E.STRETCH)
            if on and b.property("mode") == E.STRETCH:
                b.setChecked(True)

    def values(self) -> tuple[str, bool]:
        mode = self.modes.checkedButton().property("mode")
        return mode, self.all.isChecked()


def ask_replace_options(parent, occurrences: int):
    dlg = ReplaceDialog(parent, occurrences)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


def ask_page(parent, title: str, count: int, current: int) -> int | None:
    n, ok = QInputDialog.getInt(parent, title, _("Numero di pagina (1–{n}):").format(n=count),
                                current + 1, 1, max(1, count))
    return n - 1 if ok else None


# ------------------------------------------------------------------ collegamenti
class LinkDialog(QDialog):
    """Destinazione, descrizione e aspetto di un collegamento."""

    def __init__(self, parent, view, page: int, info: L.LinkInfo | None, target: L.LinkTarget | None = None,
                 description: str = "", multiple: bool = False):
        super().__init__(parent)
        self.view = view
        self.page_index = page
        self.setWindowTitle(_("Proprietà del collegamento") if info else _("Crea collegamento"))
        self.setMinimumWidth(460)
        with ENGINE_LOCK:
            self.page_count = view.document.pdf.page_count if view is not None else 1
        self.doc_path = str(view.document.path) if view is not None and view.document.path else None
        info_target = info.target if info else None
        t = info_target or target or L.LinkTarget(kind=L.URI)
        style = info.style if info else default_style()
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self._kinds = [(L.URI, _("Indirizzo web o e-mail")), (L.PAGE, _("Pagina di questo documento")),
                       (L.NAMED, _("Destinazione con nome")), (L.FILE, _("File"))]
        for k, text in self._kinds:
            self.kind.addItem(text, k)
        self.read_only = t.kind == L.OTHER
        if self.read_only:
            self.kind.addItem(_("Azione non modificabile"), L.OTHER)
        form.addRow(_("Destinazione:"), self.kind)
        self.stack = QStackedWidget()
        # web
        w_uri = QWidget()
        lu = QVBoxLayout(w_uri)
        lu.setContentsMargins(0, 0, 0, 0)
        self.uri = QLineEdit(t.uri if t.kind == L.URI else "")
        self.uri.setPlaceholderText("https://…  /  nome@dominio.it")
        self.uri_preview = QLabel()
        self.uri_preview.setWordWrap(True)
        lu.addWidget(self.uri)
        lu.addWidget(self.uri_preview)
        self.stack.addWidget(w_uri)
        # pagina
        w_page = QWidget()
        lp = QFormLayout(w_page)
        lp.setContentsMargins(0, 0, 0, 0)
        self.page_edit = QLineEdit(str((t.page if t.kind == L.PAGE and t.page >= 0 else page) + 1))
        self.fit = QComboBox()
        for code, text in (("XYZ", _("Zoom e posizione specifici")), ("Fit", _("Adatta pagina")),
                           ("FitH", _("Adatta larghezza")), ("FitV", _("Adatta altezza")),
                           ("FitB", _("Adatta area visibile"))):
            self.fit.addItem(text, code)
        if t.kind == L.PAGE:
            i = self.fit.findData(t.fit)
            self.fit.setCurrentIndex(max(0, i))
        self.top = QDoubleSpinBox()
        self.top.setRange(0, 100000)
        self.top.setValue(t.top or 0)
        self.zoom = QSpinBox()
        self.zoom.setRange(0, 6400)
        self.zoom.setSuffix(" %")
        self.zoom.setSpecialValueText(_("ereditato"))
        self.zoom.setValue(int(round((t.zoom or 0) * 100)))
        from_view = QPushButton(_("Imposta dalla vista"))
        from_view.clicked.connect(self._from_view)
        lp.addRow(_("Pagina:"), self.page_edit)
        lp.addRow(_("Vista:"), self.fit)
        lp.addRow(_("Posizione verticale (pt):"), self.top)
        lp.addRow(_("Zoom:"), self.zoom)
        lp.addRow("", from_view)
        self.stack.addWidget(w_page)
        # destinazione con nome
        w_named = QWidget()
        ln = QVBoxLayout(w_named)
        ln.setContentsMargins(0, 0, 0, 0)
        self.named = QComboBox()
        self.named.setEditable(True)
        names = []
        if view is not None:
            try:
                with ENGINE_LOCK:
                    names = sorted(view.document.pdf.resolve_names().keys())
            except Exception:
                names = []
        self.named.addItems(names)
        self.named.setCurrentText(t.name if t.kind == L.NAMED else "")
        ln.addWidget(self.named)
        self.stack.addWidget(w_named)
        # file
        w_file = QWidget()
        lf = QFormLayout(w_file)
        lf.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.file = QLineEdit(t.file if t.kind in (L.FILE, L.LAUNCH) else "")
        browse = QPushButton(_("Sfoglia…"))
        browse.clicked.connect(self._browse)
        row.addWidget(self.file, 1)
        row.addWidget(browse)
        rw = QWidget()
        rw.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.file_page = QSpinBox()
        self.file_page.setRange(1, 100000)
        self.file_page.setValue((t.page if t.kind == L.FILE and t.page >= 0 else 0) + 1)
        self.new_window = QCheckBox(_("Apri in una nuova scheda"))
        self.new_window.setChecked(t.new_window)
        self.relative = QCheckBox(_("Percorso relativo alla cartella del documento"))
        self.relative.setChecked(True)
        lf.addRow(_("File:"), rw)
        lf.addRow(_("Pagina (PDF):"), self.file_page)
        lf.addRow("", self.new_window)
        lf.addRow("", self.relative)
        self.stack.addWidget(w_file)
        self.stack.addWidget(QLabel(t.describe() if self.read_only else ""))
        form.addRow("", self.stack)
        self.description = QLineEdit(info.description if info else description)
        form.addRow(_("Descrizione:"), self.description)
        # aspetto
        self.visible = QCheckBox(_("Rettangolo visibile"))
        self.visible.setChecked(style.visible)
        self.color = QColor.fromRgbF(*[min(1, max(0, v)) for v in (tuple(style.color) + (0, 0, 0))[:3]])
        self.color_btn = QPushButton(_("Colore…"))
        self.color_btn.clicked.connect(self._pick_color)
        self.width = QComboBox()
        for wv in (1, 2, 3):
            self.width.addItem(f"{wv} pt", float(wv))
        self.width.setCurrentIndex(max(0, min(2, int(round(style.width)) - 1)))
        self.style = QComboBox()
        for code, text in (("S", _("Continuo")), ("D", _("Tratteggiato")), ("U", _("Sottolineato"))):
            self.style.addItem(text, code)
        self.style.setCurrentIndex(max(0, self.style.findData(style.style)))
        self.highlight = QComboBox()
        for code, text in (("N", _("Nessuna")), ("I", _("Inverti")), ("O", _("Contorno")), ("P", _("Rilievo"))):
            self.highlight.addItem(text, code)
        self.highlight.setCurrentIndex(max(0, self.highlight.findData(style.highlight)))
        ap = QHBoxLayout()
        for wdg in (self.visible, self.color_btn, self.width, self.style):
            ap.addWidget(wdg)
        apw = QWidget()
        apw.setLayout(ap)
        ap.setContentsMargins(0, 0, 0, 0)
        form.addRow(_("Aspetto:"), apw)
        form.addRow(_("Evidenziazione al clic:"), self.highlight)
        lay.addLayout(form)
        self.error = QLabel()
        self.error.setStyleSheet(f"color: {theme.hex_color('danger')};")
        self.error.setWordWrap(True)
        lay.addWidget(self.error)
        self.buttons = button_box()
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self.multiple = multiple
        if multiple:
            self.kind.setEnabled(False)
            self.stack.setEnabled(False)
            self.description.setEnabled(False)
        self.kind.currentIndexChanged.connect(self._on_kind)
        for wdg in (self.uri, self.page_edit, self.file, self.description):
            wdg.textChanged.connect(self.validate)
        self.named.currentTextChanged.connect(self.validate)
        self.kind.setCurrentIndex(max(0, self.kind.findData(t.kind if t.kind != L.LAUNCH else L.FILE)))
        self._on_kind()

    def _on_kind(self, *_a) -> None:
        idx = self.kind.currentIndex()
        self.stack.setCurrentIndex(min(idx, self.stack.count() - 1))
        self.validate()

    def _browse(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, _("Scegli il file"), os.path.dirname(self.doc_path or ""))
        if path:
            self.file.setText(path)

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self.color, self, _("Colore del bordo"))
        if c.isValid():
            self.color = c

    def _from_view(self) -> None:
        v = self.view
        if v is None:
            return
        page = v.current_page
        hit = v.view_to_page(v.viewport().rect().topLeft().toPointF() if hasattr(v.viewport().rect().topLeft(), "toPointF") else v.viewport().rect().topLeft(), clamp=True)
        self.page_edit.setText(str(page + 1))
        self.fit.setCurrentIndex(self.fit.findData("XYZ"))
        if hit is not None and hit[0] == page:
            self.top.setValue(max(0.0, hit[1].y))
        self.zoom.setValue(int(round(v.zoom * 100)))

    def target(self) -> L.LinkTarget | None:
        k = self.kind.currentData()
        if k == L.URI:
            return L.LinkTarget(kind=L.URI, uri=self.uri.text())
        if k == L.PAGE:
            with ENGINE_LOCK:
                n = L.page_from_label(self.view.document.pdf, self.page_edit.text()) if self.view else None
            if n is None:
                return None
            fit = self.fit.currentData()
            return L.LinkTarget(kind=L.PAGE, page=n, fit=fit, top=self.top.value() if fit in ("XYZ", "FitH") else None,
                                zoom=(self.zoom.value() / 100.0) or None if fit == "XYZ" else None)
        if k == L.NAMED:
            return L.LinkTarget(kind=L.NAMED, name=self.named.currentText().strip())
        if k == L.FILE:
            path = self.file.text().strip()
            if path and self.relative.isChecked() and os.path.isabs(path):
                path = L.relative_path(path, self.doc_path)
            return L.LinkTarget(kind=L.FILE if path.lower().endswith(".pdf") else L.LAUNCH, file=path,
                                page=self.file_page.value() - 1, fit="Fit", new_window=self.new_window.isChecked())
        return None

    def style_value(self) -> L.LinkStyle:
        return L.LinkStyle(self.visible.isChecked(), (self.color.redF(), self.color.greenF(), self.color.blueF()),
                           self.width.currentData(), self.style.currentData(), self.highlight.currentData())

    def validate(self, *_a) -> bool:
        ok, msg = True, ""
        k = self.kind.currentData()
        if self.multiple or k == L.OTHER:
            pass
        elif k == L.URI:
            chk = L.normalize_uri(self.uri.text())
            if chk.uri is None:
                ok, msg = False, _(chk.error or "")
                self.uri_preview.setText("")
            else:
                self.uri_preview.setText(_("Indirizzo: {u}").format(u=chk.uri))
                msg = " ".join(_(w) for w in chk.warnings)
        elif k == L.PAGE:
            if self.target() is None:
                ok, msg = False, _("La pagina indicata non esiste.")
        elif k == L.NAMED:
            if not self.named.currentText().strip():
                ok, msg = False, _("Indica il nome della destinazione.")
        elif k == L.FILE:
            path = self.file.text().strip()
            if not path:
                ok, msg = False, _("Scegli il file.")
            elif not path.lower().endswith(".pdf"):
                msg = _("L'apertura di questo file dipenderà dalle impostazioni di sicurezza del lettore.")
        self.error.setText(msg)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)
        return ok

    def values(self):
        t = None if (self.multiple or self.kind.currentData() == L.OTHER) else self.target()
        return t, self.style_value(), self.description.text().strip()


def ask_link(parent, view, page: int, info: L.LinkInfo | None, target: L.LinkTarget | None = None,
             description: str = "", multiple: bool = False):
    dlg = LinkDialog(parent, view, page, info, target, description, multiple)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


class UrlScopeDialog(QDialog):
    def __init__(self, parent, count: int, current: int):
        super().__init__(parent)
        self.setWindowTitle(_("Crea collegamenti dagli URL"))
        self.count = count
        lay = QFormLayout(self)
        self.scope = QComboBox()
        self.scope.addItem(_("Pagina corrente"), "page")
        self.scope.addItem(_("Tutto il documento"), "all")
        self.scope.addItem(_("Intervallo"), "range")
        self.scope.setCurrentIndex(1)
        self.range = QLineEdit(f"1-{count}")
        self.web = QCheckBox(_("Indirizzi web"))
        self.web.setChecked(True)
        self.email = QCheckBox(_("Indirizzi e-mail"))
        self.email.setChecked(True)
        self.current = current
        lay.addRow(_("Ambito:"), self.scope)
        lay.addRow(_("Pagine:"), self.range)
        lay.addRow("", self.web)
        lay.addRow("", self.email)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def pages(self) -> list[int]:
        s = self.scope.currentData()
        if s == "page":
            return [self.current]
        if s == "all":
            return list(range(self.count))
        return parse_range(self.range.text(), self.count)


def parse_range(text: str, count: int) -> list[int]:
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _s, b = part.partition("-")
            try:
                lo = int(a) if a.strip() else 1
                hi = int(b) if b.strip() else count
            except ValueError:
                continue
            out += list(range(max(1, lo) - 1, min(count, hi)))
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if 1 <= n <= count:
                out.append(n - 1)
    return sorted(set(out))


def ask_url_scope(parent, count: int, current: int):
    dlg = UrlScopeDialog(parent, count, current)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.pages(), dlg.web.isChecked(), dlg.email.isChecked()


class FoundUrlsDialog(QDialog):
    def __init__(self, parent, found):
        super().__init__(parent)
        self.setWindowTitle(_("Indirizzi trovati"))
        self.setMinimumSize(520, 360)
        self.found = found
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(_("Seleziona gli indirizzi da trasformare in collegamenti:")))
        self.list = QListWidget()
        for f in found:
            it = QListWidgetItem(_("Pagina {p}: {t} → {u}").format(p=f.page + 1, t=f.text, u=f.uri))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def chosen(self):
        return [f for k, f in enumerate(self.found) if self.list.item(k).checkState() == Qt.CheckState.Checked]


def ask_found_urls(parent, found):
    dlg = FoundUrlsDialog(parent, found)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.chosen()


class DeleteLinksDialog(QDialog):
    def __init__(self, parent, view):
        super().__init__(parent)
        self.setWindowTitle(_("Elimina collegamenti"))
        self.view = view
        self.count = view.document.page_count
        lay = QFormLayout(self)
        self.scope = QComboBox()
        self.scope.addItem(_("Pagina corrente"), "page")
        self.scope.addItem(_("Tutto il documento"), "all")
        self.scope.addItem(_("Intervallo"), "range")
        self.range = QLineEdit(f"1-{self.count}")
        self.kinds = {}
        lay.addRow(_("Ambito:"), self.scope)
        lay.addRow(_("Pagine:"), self.range)
        for code, text in (("web", _("Web ed e-mail")), ("internal", _("Interni")), ("file", _("File")),
                           ("other", _("Altre azioni"))):
            cb = QCheckBox(text)
            cb.setChecked(True)
            self.kinds[code] = cb
            lay.addRow("", cb)
        self.preview = QLabel()
        lay.addRow(self.preview)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)
        self.scope.currentIndexChanged.connect(self._update)
        self.range.textChanged.connect(self._update)
        for cb in self.kinds.values():
            cb.toggled.connect(self._update)
        self._update()

    def values(self):
        s = self.scope.currentData()
        if s == "page":
            pages = [self.view.current_page]
        elif s == "all":
            pages = list(range(self.count))
        else:
            pages = parse_range(self.range.text(), self.count)
        return pages, {k for k, cb in self.kinds.items() if cb.isChecked()}

    def _update(self, *_a) -> None:
        pages, cats = self.values()
        with ENGINE_LOCK:
            n = L.count_links_in(self.view.document.pdf, pages, cats)
        self.preview.setText(_("Collegamenti da eliminare: {n}").format(n=n))


def ask_delete_links(parent, view):
    dlg = DeleteLinksDialog(parent, view)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


__all__ = ["ask_link", "ask_replace_options", "ask_image_paths", "ask_page", "ask_url_scope", "ask_found_urls",
           "ask_delete_links", "parse_range", "pymupdf"]


class ReplicateDialog(QDialog):
    def __init__(self, parent, count: int):
        super().__init__(parent)
        self.setWindowTitle(_("Replica su pagine"))
        self.count = count
        lay = QFormLayout(self)
        self.scope = QComboBox()
        for code, text in (("all", _("Tutte le pagine")), ("odd", _("Pagine dispari")), ("even", _("Pagine pari")),
                           ("range", _("Intervallo o elenco"))):
            self.scope.addItem(text, code)
        self.range = QLineEdit(f"1-{count}")
        self.skip = QCheckBox(_("Salta le pagine di formato diverso"))
        self.skip.setChecked(True)
        lay.addRow(_("Pagine:"), self.scope)
        lay.addRow("", self.range)
        lay.addRow("", self.skip)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def values(self):
        s = self.scope.currentData()
        if s == "all":
            pages = list(range(self.count))
        elif s == "odd":
            pages = list(range(0, self.count, 2))
        elif s == "even":
            pages = list(range(1, self.count, 2))
        else:
            pages = parse_range(self.range.text(), self.count)
        return pages, self.skip.isChecked()


def ask_replicate(parent, count: int):
    dlg = ReplicateDialog(parent, count)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


class TransformDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle(_("Trasforma"))
        lay = QFormLayout(self)

        def spin(lo, hi, val, suffix):
            s = QDoubleSpinBox()
            s.setRange(lo, hi)
            s.setDecimals(2)
            s.setValue(val)
            s.setSuffix(suffix)
            return s
        self.sx = spin(1, 10000, 100, " %")
        self.sy = spin(1, 10000, 100, " %")
        self.rot = spin(-360, 360, 0, " °")
        self.kx = spin(-80, 80, 0, " °")
        self.ky = spin(-80, 80, 0, " °")
        self.dx = spin(-10000, 10000, 0, " pt")
        self.dy = spin(-10000, 10000, 0, " pt")
        self.each = QCheckBox(_("Applica a ogni oggetto attorno al proprio centro"))
        for label, w in ((_("Scala orizzontale:"), self.sx), (_("Scala verticale:"), self.sy),
                         (_("Rotazione:"), self.rot), (_("Inclinazione orizzontale:"), self.kx),
                         (_("Inclinazione verticale:"), self.ky), (_("Spostamento X:"), self.dx),
                         (_("Spostamento Y:"), self.dy)):
            lay.addRow(label, w)
        lay.addRow("", self.each)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def values(self):
        return dict(scale_x=self.sx.value(), scale_y=self.sy.value(), rotate=self.rot.value(),
                    skew_x=self.kx.value(), skew_y=self.ky.value(), dx=self.dx.value(), dy=self.dy.value()), \
            self.each.isChecked()


def ask_transform(parent):
    dlg = TransformDialog(parent)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


class AdjustDialog(QDialog):
    def __init__(self, parent, occurrences: int):
        super().__init__(parent)
        self.setWindowTitle(_("Regola immagine"))
        lay = QFormLayout(self)
        self.sliders = {}
        from PySide6.QtWidgets import QSlider
        for key, text in (("brightness", _("Luminosità:")), ("contrast", _("Contrasto:")),
                          ("saturation", _("Saturazione:"))):
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(-100, 100)
            sl.setMinimumWidth(200)
            self.sliders[key] = sl
            lay.addRow(text, sl)
        self.gray = QCheckBox(_("Scala di grigi"))
        self.invert = QCheckBox(_("Inverti i colori"))
        self.all = QCheckBox(_("Applica a tutte le {n} occorrenze").format(n=occurrences))
        self.all.setVisible(occurrences > 1)
        reset = QPushButton(_("Reimposta"))
        reset.clicked.connect(lambda: [sl.setValue(0) for sl in self.sliders.values()])
        for w in (self.gray, self.invert, self.all, reset):
            lay.addRow("", w)
        bb = button_box()
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(_("Applica"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def values(self) -> dict:
        v = {k: sl.value() for k, sl in self.sliders.items()}
        v.update(grayscale=self.gray.isChecked(), invert=self.invert.isChecked(),
                 all_occurrences=self.all.isChecked())
        return v


def ask_adjust(parent, occurrences: int):
    dlg = AdjustDialog(parent, occurrences)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


class OptimizeDialog(QDialog):
    def __init__(self, parent, estimate):
        super().__init__(parent)
        self.setWindowTitle(_("Ottimizza immagini"))
        self.estimate = estimate
        lay = QFormLayout(self)
        self.ppi = QSpinBox()
        self.ppi.setRange(36, 2400)
        self.ppi.setValue(150)
        self.ppi.setSuffix(" ppi")
        self.mode = QComboBox()
        self.mode.addItem(_("JPEG"), "jpeg")
        self.mode.addItem(_("Senza perdita"), "lossless")
        self.quality = QSpinBox()
        self.quality.setRange(10, 100)
        self.quality.setValue(80)
        self.gray = QCheckBox(_("Converti in scala di grigi"))
        self.info = QLabel()
        lay.addRow(_("Risoluzione massima:"), self.ppi)
        lay.addRow(_("Compressione:"), self.mode)
        lay.addRow(_("Qualità JPEG:"), self.quality)
        lay.addRow("", self.gray)
        lay.addRow(self.info)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)
        for w in (self.ppi, self.quality):
            w.valueChanged.connect(self._update)
        self.mode.currentIndexChanged.connect(self._update)
        self.gray.toggled.connect(self._update)
        self._update()

    def values(self) -> dict:
        return dict(max_ppi=self.ppi.value(), mode=self.mode.currentData(), quality=self.quality.value(),
                    grayscale=self.gray.isChecked())

    def _update(self, *_a) -> None:
        try:
            r = self.estimate(**self.values())
            self.info.setText(_("Peso: {a} KB → {b} KB").format(a=round(r["before"] / 1024, 1),
                                                                 b=round(r["after"] / 1024, 1)))
        except Exception as e:
            self.info.setText(str(e))


def ask_optimize(parent, estimate):
    dlg = OptimizeDialog(parent, estimate)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()


class PathStyleDialog(QDialog):
    def __init__(self, parent, obj):
        super().__init__(parent)
        self.setWindowTitle(_("Stile del tracciato"))
        lay = QFormLayout(self)
        self.stroke = None
        self.fill = None
        self.no_stroke = QCheckBox(_("Nessun tratto"))
        self.no_fill = QCheckBox(_("Nessun riempimento"))
        sb = QPushButton(_("Colore del tratto…"))
        fb = QPushButton(_("Colore del riempimento…"))
        sb.clicked.connect(lambda: self._pick("stroke"))
        fb.clicked.connect(lambda: self._pick("fill"))
        self.width = QDoubleSpinBox()
        self.width.setRange(0, 200)
        self.width.setSuffix(" pt")
        self.width.setValue(obj.state.line_width if obj is not None else 1)
        self.dash = QLineEdit()
        self.dash.setPlaceholderText(_("es. 4 2 (vuoto = continuo)"))
        lay.addRow(sb, self.no_stroke)
        lay.addRow(fb, self.no_fill)
        lay.addRow(_("Spessore:"), self.width)
        lay.addRow(_("Tratteggio:"), self.dash)
        bb = button_box()
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _pick(self, which):
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            setattr(self, which, (c.redF(), c.greenF(), c.blueF()))

    def values(self) -> dict:
        from .advanced import NONE
        dash = [float(x) for x in self.dash.text().replace(",", " ").split() if x.replace(".", "", 1).isdigit()]
        return dict(stroke=NONE if self.no_stroke.isChecked() else self.stroke,
                    fill=NONE if self.no_fill.isChecked() else self.fill,
                    width=self.width.value(), dash=dash)


def ask_path_style(parent, obj):
    dlg = PathStyleDialog(parent, obj)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    return dlg.values()
