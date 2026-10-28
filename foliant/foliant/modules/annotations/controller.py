"""Stato del modulo commenti: stili per strumento, autore, selezione e operazioni annullabili."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable

import pymupdf
from PySide6.QtCore import QObject, Signal

from foliant.api import ENGINE_LOCK, FoliantError, _

from . import dialogs, engine
from .engine import AnnotationError, Style

if TYPE_CHECKING:
    from foliant.api import Context, DocumentView

# Stile iniziale per ciascuno strumento.
FACTORY_STYLES: dict[str, Style] = {
    engine.HIGHLIGHT: Style(color=(1.0, 1.0, 0.0), opacity=1.0),
    engine.UNDERLINE: Style(color=engine.DEFAULT_COLORS[engine.UNDERLINE]),
    engine.STRIKEOUT: Style(color=engine.DEFAULT_COLORS[engine.STRIKEOUT]),
    engine.SQUIGGLY: Style(color=engine.DEFAULT_COLORS[engine.SQUIGGLY]),
    engine.NOTE: Style(color=engine.DEFAULT_COLORS[engine.NOTE]),
    engine.FREETEXT: Style(color=(0.85, 0.15, 0.15), fill=None, width=1.0, fontsize=12, text_color=(0, 0, 0)),
    engine.CALLOUT: Style(color=(0.85, 0.15, 0.15), fill=None, width=1.0, fontsize=12, text_color=(0, 0, 0)),
    engine.RECT: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.ELLIPSE: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.LINE: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.ARROW: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.POLYGON: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.POLYLINE: Style(color=engine.SHAPE_COLOR, width=1.5),
    engine.CLOUD: Style(color=engine.SHAPE_COLOR, width=1.5, cloud=True),
    engine.INK: Style(color=engine.DEFAULT_COLORS[engine.INK], width=2.0),
    "marker": Style(color=(1.0, 0.85, 0.0), width=10.0, opacity=0.4),
    engine.STAMP: Style(),
    engine.ATTACHMENT: Style(color=engine.DEFAULT_COLORS[engine.ATTACHMENT]),
    engine.DISTANCE: Style(color=engine.MEASURE_COLOR, width=1.0),
    engine.PERIMETER: Style(color=engine.MEASURE_COLOR, width=1.0),
    engine.AREA: Style(color=engine.MEASURE_COLOR, width=1.0),
}

STYLES_VERSION = 1


def _style_from_dict(data: dict, base: Style) -> Style:
    st = base.copy()
    for key, value in data.items():
        if not hasattr(st, key):
            continue
        if key in ("color", "fill", "text_color"):
            value = tuple(float(v) for v in value) if value else None
        elif key in ("opacity", "width", "fontsize"):
            value = float(value)
        elif key in ("cloud",):
            value = bool(value)
        elif key == "align":
            value = int(value)
        setattr(st, key, value)
    return st


class Controller(QObject):
    selectionChanged = Signal()
    stylesChanged = Signal(str)        # tipo di strumento il cui stile è cambiato
    toolKindChanged = Signal(str)
    annotationsVisibilityChanged = Signal(bool)

    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        self.ctx = ctx
        self._styles: dict[str, Style] = {k: v.copy() for k, v in FACTORY_STYLES.items()}
        self._load_styles()
        self.selection: list[tuple[int, int]] = []
        self._sel_view = None
        self.tool_kind: str = ""
        self.scale = engine.Scale()
        self.page_scales: dict[int, engine.Scale] = {}
        self.keep_tool = True
        self.clipboard: dict | None = None      # {"view", "items": [(pagina, xref)], "cut": bool}
        self.hidden_views: set[int] = set()
        ctx.documentChanged.connect(self._on_document_changed)
        ctx.currentViewChanged.connect(lambda _v: self.clear_selection())

    # ------------------------------------------------------------------ preferenze
    def author(self) -> str:
        name = str(self.ctx.settings().value("annotations/author", "") or "").strip()
        return name or engine.default_author() or _("Utente")

    def set_author(self, name: str) -> None:
        name = name.strip()
        if not name:
            name = engine.default_author()
        self.ctx.settings().setValue("annotations/author", name)

    def style_for(self, kind: str) -> Style:
        return self._styles.get(kind, Style()).copy()

    def set_style_for(self, kind: str, style: Style, persist: bool = True) -> None:
        self._styles[kind] = style.copy()
        if persist:
            self._save_styles()
        self.stylesChanged.emit(kind)

    def _load_styles(self) -> None:
        raw = self.ctx.settings().value("annotations/styles", "")
        if not raw:
            return
        try:
            data = json.loads(str(raw))
            if int(data.get("version", 0)) > STYLES_VERSION:
                raise ValueError("versione successiva")
            for kind, values in data.get("styles", {}).items():
                if kind in self._styles:
                    self._styles[kind] = _style_from_dict(values, self._styles[kind])
        except Exception:
            # profilo corrotto: valori di fabbrica
            self._styles = {k: v.copy() for k, v in FACTORY_STYLES.items()}

    def _save_styles(self) -> None:
        data = {"version": STYLES_VERSION, "styles": {k: asdict(v) for k, v in self._styles.items()}}
        self.ctx.settings().setValue("annotations/styles", json.dumps(data))

    # ------------------------------------------------------------------ stili preferiti (ANN-08)
    def favorites(self, kind: str | None = None) -> list[dict]:
        raw = self.ctx.settings().value("annotations/favorites", "")
        try:
            data = json.loads(str(raw)) if raw else []
            data = [d for d in data if isinstance(d, dict) and d.get("name") and d.get("kind")]
        except Exception:
            data = []
        return [d for d in data if kind is None or d["kind"] == kind]

    def _save_favorites(self, items: list[dict]) -> None:
        self.ctx.settings().setValue("annotations/favorites", json.dumps(items))

    def add_favorite(self, kind: str, name: str, style: Style | None = None) -> None:
        name = name.strip()
        if not name:
            return
        items = [d for d in self.favorites() if not (d["kind"] == kind and d["name"] == name)]
        items.append({"kind": kind, "name": name, "style": asdict(style or self.style_for(kind))})
        self._save_favorites(items)
        self.stylesChanged.emit(kind)

    def use_favorite(self, kind: str, name: str) -> bool:
        for d in self.favorites(kind):
            if d["name"] == name:
                self.set_style_for(kind, _style_from_dict(d.get("style", {}), self.style_for(kind)))
                return True
        return False

    def export_styles(self) -> str:
        data = {"version": STYLES_VERSION, "styles": {k: asdict(v) for k, v in self._styles.items()},
                "favorites": self.favorites()}
        return json.dumps(data, indent=2, ensure_ascii=False)

    def import_styles(self, text: str, replace: bool = True) -> int:
        """Importa stili e preferiti; stessi nomi: sostituiti (`replace`) o rinominati."""
        data = json.loads(text)
        if not isinstance(data, dict) or int(data.get("version", 0)) > STYLES_VERSION:
            raise AnnotationError("File di stili non valido o di una versione successiva.")
        for kind, values in data.get("styles", {}).items():
            if kind in self._styles:
                self._styles[kind] = _style_from_dict(values, self._styles[kind])
        self._save_styles()
        items = self.favorites()
        n = 0
        for fav in data.get("favorites", []):
            if not isinstance(fav, dict) or not fav.get("name") or not fav.get("kind"):
                continue
            same = [d for d in items if d["kind"] == fav["kind"] and d["name"] == fav["name"]]
            if same and replace:
                items.remove(same[0])
            elif same:
                fav = dict(fav, name=fav["name"] + " (2)")
            items.append(fav)
            n += 1
        self._save_favorites(items)
        self.stylesChanged.emit("")
        return n

    def set_tool_kind(self, kind: str) -> None:
        self.tool_kind = kind
        self.toolKindChanged.emit(kind)

    def next_counter(self, template: str) -> int:
        """Contatore dei timbri dinamici: persistente, mai decrementato (nemmeno da Annulla)."""
        import hashlib
        key = "annotations/counter/" + hashlib.sha1(template.encode()).hexdigest()[:12]
        value = int(self.ctx.settings().value(key, 0) or 0) + 1
        self.ctx.settings().setValue(key, value)
        return value

    def custom_stamps(self) -> list[dict]:
        raw = self.ctx.settings().value("annotations/custom_stamps", "")
        try:
            data = json.loads(str(raw)) if raw else []
            return [d for d in data if isinstance(d, dict) and d.get("name") and d.get("png")]
        except Exception:
            return []

    def add_custom_stamp(self, name: str, image: bytes) -> None:
        import base64
        stamps = [d for d in self.custom_stamps() if d["name"] != name]
        stamps.append({"name": name, "png": base64.b64encode(image).decode("ascii")})
        self.ctx.settings().setValue("annotations/custom_stamps", json.dumps(stamps))

    def remove_custom_stamp(self, name: str) -> None:
        stamps = [d for d in self.custom_stamps() if d["name"] != name]
        self.ctx.settings().setValue("annotations/custom_stamps", json.dumps(stamps))

    def scale_for(self, page: int) -> engine.Scale:
        return self.page_scales.get(page, self.scale)

    # ------------------------------------------------------------------ documento
    def view(self) -> "DocumentView | None":
        return self.ctx.current_view()

    def apply(self, label: str, fn: Callable[..., Any], *args, view=None, **kwargs) -> Any:
        """Esegue `fn(pdf, *args)` come passo annullabile. Gli errori diventano messaggi."""
        view = view or self.view()
        if view is None:
            return None
        if not self.can_comment(view):
            return None
        try:
            with view.document.change(label) as pdf:
                return fn(pdf, *args, **kwargs)
        except AnnotationError as e:
            self.ctx.error(_("Commenti"), _(str(e)))
        except FoliantError as e:
            self.ctx.error(_("Commenti"), str(e))
        except Exception as e:  # nessun errore del modulo deve chiudere l'applicazione
            self.ctx.error(_("Commenti"), _("Operazione non riuscita: {e}").format(e=e))
        return None

    def can_comment(self, view, warn: bool = True) -> bool:
        """Rispetta il permesso «commenti» del documento (bit 6 di /P)."""
        doc = view.document
        with ENGINE_LOCK:
            try:
                perms = doc.pdf.permissions
                encrypted = bool((doc.pdf.metadata or {}).get("encryption"))
            except Exception:
                return True
        if encrypted and perms != -1 and not perms & pymupdf.PDF_PERM_ANNOTATE:
            if warn:
                self.ctx.error(_("Commenti"), _("Il documento non consente di aggiungere o modificare commenti."))
            return False
        return True

    def read(self, fn: Callable[..., Any], *args, view=None) -> Any:
        view = view or self.view()
        if view is None:
            return None
        with ENGINE_LOCK:
            return fn(view.document.pdf, *args)

    def annotations(self, view=None) -> list[engine.AnnotInfo]:
        return self.read(engine.list_annotations, view=view) or []

    def info(self, page: int, xref: int, view=None) -> engine.AnnotInfo | None:
        try:
            return self.read(engine.annotation, page, xref, view=view)
        except Exception:
            return None

    def _on_document_changed(self, view) -> None:
        if view is not self._sel_view or not self.selection:
            return
        # dopo annulla/ripeti alcune annotazioni possono non esistere più
        alive = []
        with ENGINE_LOCK:
            pdf = view.document.pdf
            for page, xref in self.selection:
                if page < pdf.page_count:
                    p = pdf[page]
                    if engine.find_annot(p, xref) is not None:
                        alive.append((page, xref))
        if alive != self.selection:
            self.selection = alive
            self.selectionChanged.emit()

    # ------------------------------------------------------------------ selezione
    def select(self, items: list[tuple[int, int]], view=None) -> None:
        self._sel_view = view or self.view()
        self.selection = list(dict.fromkeys(items))
        self.selectionChanged.emit()
        if self._sel_view is not None:
            self._sel_view.viewport().update()
        if self.selection:
            self.ctx.show_properties("annotations")

    def toggle(self, item: tuple[int, int], view=None) -> None:
        items = list(self.selection)
        if item in items:
            items.remove(item)
        else:
            items.append(item)
        self.select(items, view)

    def clear_selection(self) -> None:
        if self.selection:
            self.selection = []
            self.selectionChanged.emit()
            if self._sel_view is not None:
                try:
                    self._sel_view.viewport().update()
                except RuntimeError:
                    pass

    def selected_infos(self) -> list[engine.AnnotInfo]:
        out = []
        for page, xref in self.selection:
            info = self.info(page, xref, view=self._sel_view)
            if info is not None:
                out.append(info)
        return out

    def selection_view(self):
        return self._sel_view

    # ------------------------------------------------------------------ operazioni
    def delete(self, items: list[tuple[int, int]] | None = None, confirm_threads: bool = True) -> bool:
        items = list(items if items is not None else self.selection)
        if not items:
            return False
        infos = [self.info(p, x) for p, x in items]
        locked = [i for i in infos if i is not None and i.locked]
        if locked:
            self.ctx.error(_("Commenti"), _("Le annotazioni bloccate non si possono eliminare."))
            return False
        if confirm_threads and any(i is not None and i.replies for i in infos):
            if not self.ctx.confirm(_("Elimina commento"),
                                    _("Il commento ha delle risposte: eliminare l'intera discussione?")):
                return False

        def run(pdf):
            for page, xref in sorted(items, key=lambda t: -t[1]):
                p = pdf[page]
                if engine.find_annot(p, xref) is not None:
                    engine.delete_annot(pdf, page, xref)
            return True

        label = _("Elimina annotazione") if len(items) == 1 else _("Elimina annotazioni")
        ok = self.apply(label, run)
        if ok:
            self.selection = [s for s in self.selection if s not in items]
            self.selectionChanged.emit()
        return bool(ok)

    def move(self, dx: float, dy: float, items=None) -> bool:
        items = list(items if items is not None else self.selection)
        if not items:
            return False
        infos = [self.info(p, x) for p, x in items]
        if any(i is not None and i.locked for i in infos):
            self.ctx.status(_("Annotazione bloccata: non si può spostare."))
            return False
        movable = []
        for (page, xref), info in zip(items, infos):
            if info is not None and self.is_text_anchored(info):
                continue
            movable.append((page, xref, info))
        if not movable:
            self.ctx.status(_("Le evidenziazioni sul testo non si spostano: si possono solo ricolorare o eliminare."))
            return False

        def run(pdf):
            for page, xref, info in movable:
                r = self._clamp(pdf, page, info.rect + (dx, dy, dx, dy))
                engine.transform_annot(pdf, page, xref, r)
                engine.update_measure(pdf, page, xref)
            return True

        return bool(self.apply(_("Sposta annotazione"), run))

    @staticmethod
    def _clamp(pdf, page_no: int, r: pymupdf.Rect) -> pymupdf.Rect:
        page = pdf[page_no]
        box = pymupdf.Rect(page.cropbox) if not page.rotation else pymupdf.Rect(page.cropbox)
        dx = dy = 0.0
        if r.x0 < box.x0:
            dx = box.x0 - r.x0
        elif r.x1 > box.x1:
            dx = box.x1 - r.x1
        if r.y0 < box.y0:
            dy = box.y0 - r.y0
        elif r.y1 > box.y1:
            dy = box.y1 - r.y1
        if r.width > box.width or r.height > box.height:
            return r
        return r + (dx, dy, dx, dy)

    def resize(self, page: int, xref: int, rect: pymupdf.Rect) -> bool:
        info = self.info(page, xref)
        if info is None:
            return False
        if info.locked:
            self.ctx.status(_("Annotazione bloccata: non si può ridimensionare."))
            return False

        def run(pdf):
            engine.transform_annot(pdf, page, xref, rect)
            engine.update_measure(pdf, page, xref)
            return True

        return bool(self.apply(_("Ridimensiona annotazione"), run))

    def rotate(self, degrees: float, items=None) -> bool:
        items = list(items if items is not None else self.selection)
        if not items:
            return False

        def run(pdf):
            for page, xref in items:
                engine.rotate_annot(pdf, page, xref, degrees)
            return True

        return bool(self.apply(_("Ruota annotazione"), run))

    def move_vertex(self, page: int, xref: int, index: int, point) -> bool:
        info = self.info(page, xref)
        if info is None:
            return False
        if info.locked:
            self.ctx.status(_("Annotazione bloccata: non si può spostare."))
            return False
        return bool(self.apply(_("Sposta vertice"), lambda pdf: engine.set_vertex(pdf, page, xref, index, point)
                               or True))

    def vertices(self, page: int, xref: int, view=None) -> list:
        return self.read(engine.vertex_points, page, xref, view=view) or []

    def is_text_anchored(self, info: engine.AnnotInfo) -> bool:
        if info.kind not in engine.MARKUP_KINDS:
            return False
        return not bool(self.read(engine.is_area_markup, info.xref, view=self._sel_view))

    def set_style(self, items=None, **props) -> bool:
        items = list(items if items is not None else self.selection)
        if not items:
            return False

        def run(pdf):
            for page, xref in items:
                engine.set_style(pdf, page, xref, **props)
            return True

        return bool(self.apply(_("Cambia aspetto"), run))

    def restack(self, front: bool, items=None) -> bool:
        items = list(items if items is not None else self.selection)

        def run(pdf):
            for page, xref in items:
                engine.restack(pdf, page, xref, front)
            return True

        return bool(self.apply(_("Porta in primo piano") if front else _("Porta sullo sfondo"), run))

    def set_flag(self, flag: int, on: bool, items=None) -> bool:
        items = list(items if items is not None else self.selection)

        def run(pdf):
            for page, xref in items:
                engine.set_flag(pdf, page, xref, flag, on)
            return True

        return bool(self.apply(_("Cambia opzioni"), run))

    def edit_text(self, page: int, xref: int, view=None) -> bool:
        view = view or self.view()
        info = self.info(page, xref, view=view)
        if info is None:
            return False
        rich = self.read(engine.get_rich_text, xref, view=view) or ""
        header = f"{info.author or _('(autore sconosciuto)')} · {engine.format_date(info.modified or info.created)}"
        read_only = bool(info.flags & (engine.FLAG_LOCKED_CONTENTS | engine.FLAG_READ_ONLY))
        res = dialogs.edit_comment(view, _(info.label), header, info.contents, rich, read_only=read_only)
        if res is None or read_only:
            return False
        text, xhtml = res
        if text == info.contents and not xhtml and not rich:
            return False

        def run(pdf):
            if info.kind == engine.FREETEXT and not text.strip():
                engine.delete_annot(pdf, page, xref, force=True)
                return True
            if info.kind not in (engine.FREETEXT, engine.CALLOUT):
                engine.ensure_popup(pdf, page, xref)
            engine.set_contents(pdf, page, xref, text, xhtml)
            return True

        return bool(self.apply(_("Modifica commento"), run, view=view))

    def reply(self, page: int, xref: int, text: str | None = None) -> int | None:
        if text is None:
            text = dialogs.ask_text(self.view(), _("Rispondi"), _("Risposta:"), "", multiline=True)
        if text is None or not text.strip():
            return None
        return self.apply(_("Rispondi"), engine.add_reply, page, xref, text, self.author())

    def set_status(self, page: int, xref: int, state: str) -> int | None:
        return self.apply(_("Imposta stato"), engine.set_status, page, xref, state, self.author())

    def set_marked(self, page: int, xref: int, marked: bool) -> int | None:
        return self.apply(_("Segno di spunta"), engine.set_marked, page, xref, marked, self.author())

    def set_annotation_author(self, items, author: str) -> bool:
        author = author.strip()
        if not author:
            return False

        def run(pdf):
            for page, xref in items:
                engine.set_author(pdf, page, xref, author)
            return True

        return bool(self.apply(_("Cambia autore"), run))

    # ------------------------------------------------------------------ appunti (ANN-07)
    def copy(self, cut: bool = False) -> bool:
        if not self.selection:
            return False
        infos = self.selected_infos()
        if cut and any(i.locked for i in infos):
            self.ctx.error(_("Commenti"), _("Le annotazioni bloccate non si possono eliminare."))
            return False
        self.clipboard = {"view": self._sel_view, "items": list(self.selection), "cut": cut,
                          "data": None}
        from foliant.api import copy_text_to_clipboard
        text = "\n\n".join(i.contents for i in infos if i.contents)
        if text:
            copy_text_to_clipboard(text)
        self.ctx.status(_("Annotazioni tagliate.") if cut else _("Annotazioni copiate."))
        return True

    def paste(self, page: int | None = None) -> list[tuple[int, int]]:
        clip = self.clipboard
        view = self.view()
        if not clip or view is None:
            return []
        if clip["view"] is not view:
            self.ctx.error(_("Commenti"), _("Le annotazioni si possono incollare solo nello stesso documento."))
            return []
        target = view.current_page if page is None else page
        items = [it for it in clip["items"] if self.info(*it, view=view) is not None]
        if not items:
            return []
        cut = clip["cut"]

        def run(pdf):
            out = []
            for src_page, xref in items:
                same = src_page == target and not cut
                off = 10.0 if same else 0.0
                new = engine.duplicate_annot(pdf, src_page, xref, target, off, off, self.author(), keep_identity=cut)
                out.append((target, new))
            if cut:
                for src_page, xref in items:
                    engine.delete_annot(pdf, src_page, xref)
            return out

        res = self.apply(_("Incolla annotazioni"), run, view=view) or []
        if cut and res:
            self.clipboard = {"view": view, "items": res, "cut": False, "data": None}
        if res:
            self.select(res, view)
        return res

    def duplicate(self) -> list[tuple[int, int]]:
        if not self.selection:
            return []
        clip = self.clipboard
        self.clipboard = {"view": self._sel_view, "items": list(self.selection), "cut": False, "data": None}
        page = self.selection[0][0]
        try:
            return self.paste(page)
        finally:
            self.clipboard = clip

    # ------------------------------------------------------------------ visibilità (ANN-39)
    def after_save(self, view) -> None:
        """Dopo il salvataggio gli xref in memoria sono rinumerati: si azzerano i riferimenti."""
        self._saves = getattr(self, "_saves", 0) + 1
        if self._sel_view is view:
            self.clear_selection()
        if self.clipboard and self.clipboard.get("view") is view:
            self.clipboard = None
        view.viewport().update()

    def popups(self, view, page: int) -> list:
        key = (id(view), view.document.version, getattr(self, "_saves", 0))
        cache = getattr(self, "_popup_cache", None)
        if cache is None or cache[0] != key:
            cache = (key, {})
            self._popup_cache = cache
        if page not in cache[1]:
            from foliant.api import try_engine
            with try_engine() as ok:
                if not ok:
                    return []
                cache[1][page] = engine.open_popups(view.document.pdf, page)
        return cache[1][page]

    def toggle_popup(self, page: int, xref: int) -> bool:
        view = self.view()
        with ENGINE_LOCK:
            p = view.document.pdf[page]
            a = engine.find_annot(p, xref)
            is_open = bool(a and a.popup_xref and a.is_open)
        return bool(self.apply(_("Finestra del commento"),
                               lambda pdf: engine.set_popup_open(pdf, page, xref, not is_open) or True))

    def reveal_after_create(self, view) -> None:
        if self.annotations_hidden(view):
            self.set_annotations_hidden(False, view)
            self.ctx.status(_("Le annotazioni erano nascoste: sono di nuovo visibili."))

    def annotations_hidden(self, view=None) -> bool:
        view = view or self.view()
        return view is not None and id(view) in self.hidden_views

    def set_annotations_hidden(self, hidden: bool, view=None) -> None:
        """Nasconde o mostra le annotazioni nella vista (il file non cambia)."""
        view = view or self.view()
        if view is None:
            return
        from .visibility import set_view_annotations
        set_view_annotations(view, not hidden)
        if hidden:
            self.hidden_views.add(id(view))
            if self._sel_view is view:
                self.clear_selection()
        else:
            self.hidden_views.discard(id(view))
        self.annotationsVisibilityChanged.emit(hidden)

    def go_to(self, page: int, xref: int, view=None) -> None:
        view = view or self.view()
        if view is None:
            return
        info = self.info(page, xref, view=view)
        if info is None:
            return
        view.ensure_visible(page, info.rect)
        self.select([(page, xref)], view)
