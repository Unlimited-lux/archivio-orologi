"""Stato della selezione e comandi del modulo (collega strumenti, azioni e motore)."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Callable

import pymupdf
from PySide6.QtCore import QByteArray, QMimeData, QObject, QPointF, Signal
from PySide6.QtGui import QGuiApplication, QImage

from foliant.api import ENGINE_LOCK, OperationCancelled, _

from . import clipboard as C
from . import dialogs as D
from . import engine as E
from . import links as L
from .content import IMAGE, PATH, ParsedPage, parse_page, rcenter
from .tool import LinkTool, ObjectTool

if TYPE_CHECKING:
    from foliant.api import Context, DocumentView

IMAGE_FILTER = "Immagini (*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp);;Tutti i file (*)"
IMAGE_EXTS = (".svg", ".heic", ".heif", ".avif", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".pnm", ".ppm", ".pgm", ".pbm",
              ".tga", ".ico", ".jp2", ".jpx", ".j2k")

_last_payload: C.Payload | None = None       # copia locale degli appunti interni
_last_links: list | None = None


class ObjectsController(QObject):
    selectionChanged = Signal()
    _parsedReady = Signal(object, object)

    def __init__(self, ctx: "Context"):
        super().__init__(ctx)
        self.ctx = ctx
        self.object_tool = ObjectTool(self)
        self.link_tool = LinkTool(self)
        self.view: "DocumentView | None" = None
        self.sel_page: int | None = None
        self.selection: list[int] = []
        self._cache: dict = {}
        self._link_cache: dict = {}
        self._paste_counts: dict = {}
        self._signed_ok: set = set()
        self._pdfa_warned: set = set()
        self._pending: set = set()
        self._parsedReady.connect(self._on_parsed_ready)
        self.smart_guides = True
        self.align_reference = "auto"          # auto, selection, key, page (OBJ-21)
        self.distribute_method = E.SPACING     # spacing, centers (OBJ-22)
        self.crop_ratio = "free"     # proporzioni del ritaglio: free, original, 1:1, 4:3, 3:4, 16:9
        self.links_follow = True     # i collegamenti che coincidono con l'oggetto si spostano con esso
        self.grid_on = False
        self.grid_snap = True
        self.grid_step_mm = 10.0
        ctx.documentChanged.connect(self._on_document_changed)
        ctx.documentClosed.connect(lambda v: self.stop_external()
                                   if getattr(self, "external", None) is not None and self.external.view is v else None)
        ctx.currentViewChanged.connect(self._on_view_changed)

    # ================================================================ stato
    def status(self, msg: str) -> None:
        self.ctx.status(msg)

    def _on_document_changed(self, view) -> None:
        self._cache.clear()
        self._link_cache.clear()
        if view is not None and view.tool is self.link_tool and self.link_tool.drag is None:
            # gli xref selezionati possono non essere più validi dopo una modifica esterna allo strumento
            valid = {i.xref for i in self.links(view, self.link_tool.page)} if self.link_tool.page is not None else set()
            self.link_tool.selection = [x for x in self.link_tool.selection if x in valid]
        if view is self.view and self.sel_page is not None:
            parsed = self.parsed(view, self.sel_page, quiet=True)
            n = len(parsed.objects) if parsed else 0
            sel = [i for i in self.selection if i < n]
            if sel != self.selection:
                self.selection = sel
            self.selectionChanged.emit()

    def _on_view_changed(self, view) -> None:
        if view is not self.view:
            self.view = view
            self.sel_page = None
            self.selection = []
            self._cache.clear()
            self._link_cache.clear()
            self.selectionChanged.emit()

    def release_view(self, view) -> None:
        """Dimentica una vista chiusa: selezione, cache, strumenti, modifica esterna."""
        if getattr(self, "external", None) is not None and self.external.view is view:
            self.stop_external()
        if self.view is view:
            self.view = None
            self.sel_page = None
            self.selection = []
        self._cache.clear()
        self._link_cache.clear()
        self.object_tool.hover = None
        self.object_tool.drag = None
        self.object_tool.crop = None
        self.link_tool.drag = None
        self.link_tool.selection = []
        self.link_tool.page = None

    def tool_activated(self, view) -> None:
        self.view = view
        for i in view.visible_pages():   # analisi anticipata delle pagine visibili
            self.parsed(view, i, quiet=True, background=True)

    def tool_deactivated(self, view) -> None:
        self.clear_selection(view)

    def parsed(self, view, page: int, quiet: bool = False, background: bool = False) -> ParsedPage | None:
        """Oggetti della pagina (in cache). Con `background` l'analisi di una pagina
        non ancora pronta parte in un thread e la funzione restituisce None."""
        if view is None or page is None:
            return None
        key = (id(view.document), view.document.version, page)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if background and not os.environ.get("FOLIANT_SYNC_TASKS"):
            self._parse_async(view, page, key)
            return None
        try:
            with ENGINE_LOCK:
                pdf = view.document.pdf
                if not 0 <= page < pdf.page_count:
                    return None
                parsed = parse_page(pdf, pdf[page])
        except Exception as e:  # pagina danneggiata: solo quella non è modificabile
            if not quiet:
                self.status(_("Questa pagina non è modificabile: {e}").format(e=e))
            return None
        if len(self._cache) > 16:
            self._cache.clear()
        self._cache[key] = parsed
        return parsed

    def _parse_async(self, view, page: int, key) -> None:
        if key in self._pending:
            return
        self._pending.add(key)
        doc = view.document

        def work():
            result = None
            try:
                with ENGINE_LOCK:
                    if doc.version == key[1] and not doc.closed and 0 <= page < doc.pdf.page_count:
                        result = parse_page(doc.pdf, doc.pdf[page])
            except Exception:
                result = None
            self._parsedReady.emit(key, result)
        threading.Thread(target=work, daemon=True).start()

    def _on_parsed_ready(self, key, result) -> None:
        self._pending.discard(key)
        if result is not None:
            if len(self._cache) > 16:
                self._cache.clear()
            self._cache[key] = result
            if self.view is not None:
                self.view.viewport().update()

    def links(self, view, page: int) -> list:
        key = (id(view.document), view.document.version, page)
        if key not in self._link_cache:
            try:
                with ENGINE_LOCK:
                    self._link_cache[key] = L.page_links(view.document.pdf, page)
            except Exception:
                self._link_cache[key] = []
        return self._link_cache[key]

    def page_rect(self, view, page: int) -> pymupdf.Rect:
        with ENGINE_LOCK:
            p = view.document.pdf[page]
            return pymupdf.Rect(0, 0, p.cropbox.width, p.cropbox.height)

    def selected_objects(self, view) -> list:
        if self.sel_page is None or not self.selection or view is None:
            return []
        parsed = self.parsed(view, self.sel_page, quiet=True)
        if parsed is None:
            return []
        return [parsed.objects[i] for i in self.selection if 0 <= i < len(parsed.objects)]

    def set_selection(self, view, page: int | None, indices) -> None:
        self.view = view
        self.sel_page = page if indices else (page if page is not None else None)
        self.selection = list(dict.fromkeys(indices or []))
        if not self.selection:
            self.sel_page = None
        if view is not None:
            view.viewport().update()
        self.selectionChanged.emit()

    def clear_selection(self, view) -> None:
        if self.selection or self.sel_page is not None:
            self.set_selection(view, None, [])

    def select_all(self, view, kinds: set | None = None) -> None:
        page = view.current_page if self.sel_page is None else self.sel_page
        parsed = self.parsed(view, page)
        if parsed is None:
            return
        kinds = kinds or D.select_filter()
        self.set_selection(view, page, [o.index for o in parsed.objects if o.kind in kinds])

    def cycle(self, view, step: int) -> None:
        page = view.current_page if self.sel_page is None else self.sel_page
        parsed = self.parsed(view, page)
        if parsed is None or not parsed.objects:
            return
        n = len(parsed.objects)
        cur = self.selection[-1] if self.selection else (-1 if step > 0 else n)
        self.set_selection(view, page, [(cur + step) % n])

    # ================================================================ modifiche
    def can_modify(self, view) -> bool:
        """Permesso di modifica del documento. Le firme digitali le gestisce la guardia
        del modulo Firma (SIG-21), in base al `kind` passato a `Document.change`."""
        doc = view.document
        with ENGINE_LOCK:
            perms = doc.pdf.permissions
            encrypted = bool(doc.original_protection)
        if encrypted and perms != -1 and not perms & pymupdf.PDF_PERM_MODIFY:
            self.ctx.error(_("Modifica"), _("Il documento non consente modifiche al contenuto. "
                                            "Aprilo con la password del proprietario per modificarlo."))
            return False
        with ENGINE_LOCK:
            xfa = E.has_dynamic_xfa(doc.pdf)
            part = E.pdfa_part(doc.pdf)
        if xfa:
            self.ctx.error(_("Modifica"), _("Il documento è un modulo XFA dinamico: il contenuto mostrato viene "
                                            "generato dal modulo, quindi la modifica degli oggetti non è disponibile."))
            return False
        if part and id(doc) not in self._pdfa_warned:
            self._pdfa_warned.add(id(doc))
            self.ctx.info(_("Documento PDF/A"), _("Il documento dichiara la conformità PDF/A-{p}: alcune modifiche "
                                                  "(per esempio la trasparenza) possono violare il profilo.")
                          .format(p=part))
        return True

    def run_change(self, view, label: str, fn: Callable, *args, kind: str = "content"):
        """Esegue `fn(pdf, *args)` come passo annullabile. None se fallisce o è annullato.

        `kind`: «content» per immagini, tracciati, ritagli…; «annotation» quando si
        aggiungono, modificano o tolgono solo annotazioni (collegamenti)."""
        if view is None:
            return None
        if not self.can_modify(view):
            return None
        try:
            with view.document.change(label, kind=kind) as pdf:
                return fn(pdf, *args)
        except OperationCancelled as e:
            self.status(str(e) or _("Operazione annullata."))
        except (E.ObjectError, L.LinkError) as e:
            self.ctx.error(label, _(str(e)))
        except Exception as e:
            self.ctx.error(label, _("Operazione non riuscita: {e}").format(e=e))
        return None

    def _apply(self, view, label: str, fn: Callable, *args, keep: bool = True):
        if not self.selection or self.sel_page is None:
            self.status(_("Seleziona prima uno o più oggetti."))
            return None
        page = self.sel_page
        result = self.run_change(view, label, fn, page, list(self.selection), *args)
        if result is not None and keep and isinstance(result, list):
            self.set_selection(view, page, result)
        return result

    def translate(self, view, dx: float, dy: float, label: str | None = None) -> None:
        self._apply(view, label or _("Sposta oggetti"), _translate_with_links if self.links_follow else E.translate,
                    dx, dy)

    def nudge(self, view, vx: float, vy: float) -> None:
        with ENGINE_LOCK:
            page = view.document.pdf[self.sel_page]
            dx, dy = E.view_vector_to_page(page, vx, vy)
        objs = self.selected_objects(view)
        if objs:
            dx, dy = E.clamp_translation(E.union_rect(objs), self.page_rect(view, self.sel_page), dx, dy)
        self.translate(view, dx, dy)

    def resize_to(self, view, rect: pymupdf.Rect) -> None:
        self._apply(view, _("Ridimensiona oggetti"), E.resize, rect)

    def rotate(self, view, angle_ccw: float) -> None:
        self._apply(view, _("Ruota oggetti"), E.rotate, angle_ccw)

    def flip(self, view, horizontal: bool) -> None:
        self._apply(view, _("Capovolgi oggetti"), E.flip, horizontal)

    def set_opacity(self, view, alpha: float) -> None:
        if view is None:
            return
        with ENGINE_LOCK:
            part = E.pdfa_part(view.document.pdf)
        if part == "1" and alpha < 1 and not self.ctx.confirm(
                _("Documento PDF/A-1"), _("Il documento dichiara la conformità PDF/A-1, che non ammette la "
                                          "trasparenza: con questa opacità il file non sarà più conforme. Continuare?")):
            return
        self._apply(view, _("Opacità"), E.set_opacity, alpha)

    def delete(self, view) -> None:
        if self._apply(view, _("Elimina oggetti"), E.delete_objects, keep=False) is not None:
            self.clear_selection(view)

    def reorder(self, view, where: str) -> None:
        if not self.selection:
            return
        page = self.sel_page
        label = {E.FRONT: _("Porta in primo piano"), E.FORWARD: _("Porta avanti"),
                 E.BACKWARD: _("Porta indietro"), E.BACK: _("Porta in fondo")}[where]
        result = self.run_change(view, label, _reorder_or_raise, page, list(self.selection), where)
        if result is None:
            return
        self.set_selection(view, page, result)

    def align(self, view, how: str, reference: str | None = None) -> None:
        objs = self.selected_objects(view)
        if not objs:
            return
        if reference is None and self.align_reference != "auto":
            reference = self.align_reference
        if reference is None or (reference != E.REF_PAGE and len(objs) == 1):
            reference = E.REF_SELECTION if len(objs) > 1 else E.REF_PAGE
        page = self.sel_page
        indices = list(self.selection)

        def fn(pdf):
            p = pdf[page]
            parsed = parse_page(pdf, p)
            ordered = [parsed.objects[i] for i in indices]
            rects = E.view_rects(p, ordered)
            deltas = E.align_deltas(rects, how, reference, p.rect, key=0)
            if all(abs(dx) < 1e-6 and abs(dy) < 1e-6 for dx, dy in deltas):
                raise _NoChange()
            return E.translate_each(pdf, page, indices, deltas)
        self._run_maybe_nochange(view, _("Allinea oggetti"), fn, page, indices)

    def distribute(self, view, horizontal: bool, method: str = E.SPACING) -> None:
        objs = self.selected_objects(view)
        if len(objs) < 3:
            self.status(_("Servono almeno 3 oggetti."))
            return
        page = self.sel_page
        indices = list(self.selection)

        def fn(pdf):
            p = pdf[page]
            parsed = parse_page(pdf, p)
            rects = E.view_rects(p, [parsed.objects[i] for i in indices])
            deltas = E.distribute_deltas(rects, horizontal, method)
            if all(abs(dx) < 1e-6 and abs(dy) < 1e-6 for dx, dy in deltas):
                raise _NoChange()
            return E.translate_each(pdf, page, indices, deltas)
        self._run_maybe_nochange(view, _("Distribuisci oggetti"), fn, page, indices)

    def _run_maybe_nochange(self, view, label, fn, page, indices) -> None:
        if not self.can_modify(view):
            return
        try:
            with view.document.change(label) as pdf:
                fn(pdf)
        except OperationCancelled as e:
            self.status(str(e))
            return
        except _NoChange:
            self.status(_("Gli oggetti sono già in posizione."))
            return
        except (E.ObjectError, L.LinkError) as e:
            self.ctx.error(label, _(str(e)))
            return
        except Exception as e:
            self.ctx.error(label, _("Operazione non riuscita: {e}").format(e=e))
            return
        self.set_selection(view, page, indices)

    def select_similar(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != PATH:
            self.status(_("Seleziona un tracciato."))
            return
        parsed = self.parsed(view, self.sel_page)
        self.set_selection(view, self.sel_page, E.similar_paths(parsed, objs[0].index))

    # ---------------------------------------------------------------- guide (nucleo, VIS-39)
    def _old_guides_key(self, view) -> str | None:
        p = view.document.path if view is not None else None
        if not p:
            return None
        import hashlib
        return "objects/guides/" + hashlib.sha1(str(os.path.abspath(str(p))).encode()).hexdigest()

    def migrate_guides(self, view) -> None:
        """Porta nelle guide del nucleo quelle salvate dalle versioni precedenti del modulo."""
        key = self._old_guides_key(view)
        if not key:
            return
        st = self.ctx.settings()
        raw = st.value(key, "")
        if not raw:
            return
        import json
        try:
            data = json.loads(str(raw))
            for axis in ("x", "y"):
                for v in data.get(axis, []):
                    view.add_guide(axis, float(v))
        except (ValueError, TypeError, AttributeError):
            pass
        st.remove(key)

    def guides(self, view, page: int | None = None) -> dict:
        """Guide valide per la pagina, in coordinate pagina: {"x": [...], "y": [...]}."""
        if view is None:
            return {"x": [], "y": []}
        if page is None:
            page = self.sel_page if self.sel_page is not None else view.current_page
        try:
            return view.page_guide_lines(page)
        except Exception:
            return {"x": [], "y": []}

    def add_guide(self, view, axis: str, value: float) -> None:
        view.add_guide(axis, float(value))

    def clear_guides(self, view) -> None:
        view.clear_guides()

    def ask_add_guide(self, view, axis: str) -> None:
        if view is None:
            return
        from PySide6.QtWidgets import QInputDialog
        title = _("Guida verticale") if axis == "x" else _("Guida orizzontale")
        val, ok = QInputDialog.getDouble(self.ctx.window, title, _("Posizione (mm dal bordo):"), 20, 0, 5000, 1)
        if ok:
            self.add_guide(view, axis, val * E.MM)

    def paint_grid(self, view, painter, page: int) -> None:
        """Livello di disegno della griglia (solo in modalità Modifica, non scritta nel PDF)."""
        if not self.grid_on or self.ctx.current_mode() != "modifica":
            return
        from PySide6.QtGui import QPen
        from foliant.api import theme
        from .tool import view_scale
        step = self.grid_step_mm * E.MM
        if step <= 0:
            return
        r = self.page_rect(view, page)
        s = view_scale(view, page)
        if step * s < 4:
            return
        c = theme.color("accent")
        c.setAlpha(45)
        painter.setPen(QPen(c, 1 / s))
        x = 0.0
        while x <= r.x1:
            painter.drawLine(QPointF(x, r.y0), QPointF(x, r.y1))
            x += step
        y = 0.0
        while y <= r.y1:
            painter.drawLine(QPointF(r.x0, y), QPointF(r.x1, y))
            y += step

    def edit_external(self, view):
        from . import external as X
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            self.status(_("Seleziona una sola immagine."))
            return None
        self.stop_external()
        sess = X.ExternalEdit(self, view, self.sel_page, objs[0].index)
        if sess.start():
            self.external = sess
            return sess
        return None

    def stop_external(self) -> None:
        sess = getattr(self, "external", None)
        if sess is not None:
            sess.stop()
            self.external = None

    # ---------------------------------------------------------------- avanzate
    def replicate(self, view) -> None:
        from . import advanced as A
        if not self.selection:
            return
        res = D.ask_replicate(self.ctx.window, view.document.page_count)
        if res is None:
            return
        pages, skip = res
        page, indices = self.sel_page, list(self.selection)
        done = self.run_change(view, _("Replica su pagine"), A.replicate, page, indices, pages, skip)
        if done is not None:
            self.clear_selection(view)
            self.status(_("Oggetti replicati su {n} pagine.").format(n=len(done)))

    def transform_dialog(self, view) -> None:
        from . import advanced as A
        if not self.selection:
            return
        res = D.ask_transform(self.ctx.window)
        if res is None:
            return
        kw, each = res
        page = self.sel_page
        r = self.run_change(view, _("Trasforma"), lambda pdf: A.apply_transform(pdf, page, list(self.selection),
                                                                               each=each, **kw))
        if r is not None:
            self.set_selection(view, page, r)

    def reset_transform(self, view) -> None:
        from . import advanced as A
        if not self.selection:
            return
        page = self.sel_page
        r = self.run_change(view, _("Reimposta trasformazione"), A.reset_transform, page, list(self.selection))
        if r is not None:
            self.set_selection(view, page, r[0])
            if r[1]:
                self.status(_("L'oggetto era inclinato: è stato riportato a una forma ortogonale."))

    def decompose(self, view) -> None:
        from . import advanced as A
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != "composto":
            self.status(_("Seleziona un oggetto composto."))
            return
        page, index = self.sel_page, objs[0].index
        with ENGINE_LOCK:
            transp = A.form_is_transparency_group(view.document.pdf, objs[0].xref)
        if transp and not self.ctx.confirm(_("Scomponi"), _("L'oggetto è un gruppo di trasparenza: scomporlo "
                                                            "può cambiarne la resa. Continuare?")):
            return
        r = self.run_change(view, _("Scomponi"), A.decompose, page, index, True)
        if r is not None:
            self.set_selection(view, page, r)

    def group(self, view) -> None:
        from . import advanced as A
        if len(self.selection) < 2:
            self.status(_("Seleziona almeno 2 oggetti da raggruppare."))
            return
        page = self.sel_page
        r = self.run_change(view, _("Raggruppa"), A.group, page, list(self.selection))
        if r is not None:
            self.set_selection(view, page, r)

    def adjust_image(self, view) -> None:
        from . import imaging as IM
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            self.status(_("Seleziona una sola immagine."))
            return
        o = objs[0]
        occ = 1
        if o.xref:
            with ENGINE_LOCK:
                occ = len(E.image_occurrences(view.document.pdf, o.xref))
        preview = None
        try:
            with ENGINE_LOCK:
                preview = E.extract_image(view.document.pdf, o, "png").data
        except Exception:
            preview = None
        vals = D.ask_adjust(self.ctx.window, occ, preview)
        if vals is None:
            return
        page = self.sel_page
        r = self.run_change(view, _("Regola immagine"), lambda pdf: IM.adjust_image(pdf, page, o.index, **vals))
        if r is not None:
            self.set_selection(view, page, r)

    def optimize_images(self, view) -> None:
        from . import imaging as IM
        objs = [o for o in self.selected_objects(view) if o.kind == IMAGE]
        if not objs:
            self.status(_("Seleziona una o più immagini."))
            return
        page = self.sel_page
        indices = [o.index for o in objs]

        def estimate(**kw):
            with ENGINE_LOCK:
                return IM.estimate_optimization(view.document.pdf, page, indices, **kw)
        vals = D.ask_optimize(self.ctx.window, estimate)
        if vals is None:
            return
        r = self.run_change(view, _("Ottimizza immagini"),
                            lambda pdf: IM.optimize_images(pdf, page, indices, **vals))
        if r is not None:
            if r.get("kept"):
                self.ctx.info(_("Ottimizza immagini"),
                              _("Alcune immagini sono state lasciate invariate perché il risultato sarebbe più pesante."))
            self.status(_("Peso delle immagini: {a} KB → {b} KB").format(
                a=round(r["before"] / 1024, 1), b=round(r["after"] / 1024, 1)))

    def optimize_document(self, view) -> None:
        """Ottimizza le immagini di tutte le pagine in un unico passo annullabile."""
        from . import imaging as IM
        if view is None:
            return

        def targets(pdf):
            out = []
            for i in range(pdf.page_count):
                try:
                    idx = [o.index for o in parse_page(pdf, pdf[i]).objects if o.kind == IMAGE and not o.inline]
                except Exception:
                    continue
                if idx:
                    out.append((i, idx))
            return out
        with ENGINE_LOCK:
            pages = targets(view.document.pdf)
        if not pages:
            self.ctx.info(_("Ottimizza immagini"), _("Il documento non contiene immagini."))
            return

        def estimate(**kw):
            before = after = 0
            with ENGINE_LOCK:
                for i, idx in pages:
                    try:
                        r = IM.estimate_optimization(view.document.pdf, i, idx, **kw)
                    except E.ObjectError:
                        continue
                    before += r["before"]
                    after += r["after"]
            return {"before": before, "after": after}
        vals = D.ask_optimize(self.ctx.window, estimate)
        if vals is None:
            return

        def fn(pdf):
            before = after = 0
            for i, idx in targets(pdf):
                try:
                    r = IM.optimize_images(pdf, i, idx, **vals)
                except E.ObjectError:
                    continue
                before += r["before"]
                after += r["after"]
            return {"before": before, "after": after}
        r = self.run_change(view, _("Ottimizza le immagini del documento"), fn)
        if r is not None:
            self.status(_("Peso delle immagini: {a} KB → {b} KB").format(
                a=round(r["before"] / 1024, 1), b=round(r["after"] / 1024, 1)))

    def path_style(self, view) -> None:
        from . import advanced as A
        objs = [o for o in self.selected_objects(view) if o.kind == PATH]
        if not objs:
            self.status(_("Seleziona uno o più tracciati."))
            return
        vals = D.ask_path_style(self.ctx.window, objs[0])
        if vals is None:
            return
        if vals["stroke"] == A.NONE and vals["fill"] == A.NONE and not self.ctx.confirm(
                _("Stile del tracciato"), _("Senza tratto né riempimento il tracciato diventerà invisibile. Continuare?")):
            return
        page = self.sel_page
        r = self.run_change(view, _("Stile del tracciato"),
                            lambda pdf: A.restyle_paths(pdf, page, [o.index for o in objs], **vals))
        if r is not None:
            self.set_selection(view, page, r)

    # ---------------------------------------------------------------- ritaglio
    def start_crop(self, view) -> None:
        if view.tool is not self.object_tool:
            return
        if not self.object_tool.start_crop(view):
            self.status(_("Seleziona una sola immagine da ritagliare."))

    def crop_to_rect(self, view, page: int, index: int, rect: pymupdf.Rect) -> None:
        parsed = self.parsed(view, page)
        if parsed is None or index >= len(parsed.objects):
            return
        o = parsed.objects[index]
        unit = E.crop_unit_from_rect(o, rect, parsed.pdf_to_page)
        if o.wrapper is not None and o.wrapper.clip_unit is None and abs(unit[0]) < 1e-6 and abs(unit[1]) < 1e-6 \
                and abs(unit[2] - 1) < 1e-6 and abs(unit[3] - 1) < 1e-6:
            return
        result = self.run_change(view, _("Ritaglia immagine"), E.set_crop, page, index, unit)
        if result is not None:
            self.set_selection(view, page, result)

    def reset_crop(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            return
        self.run_change(view, _("Ripristina ritaglio"), E.set_crop, self.sel_page, objs[0].index, None)

    def apply_crop(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            return
        self.run_change(view, _("Applica ritaglio"), E.apply_crop, self.sel_page, objs[0].index)

    # ---------------------------------------------------------------- immagini
    def insert_image_files(self, view, paths: list[str], page: int | None = None,
                           center: pymupdf.Point | None = None, rect: pymupdf.Rect | None = None) -> list[int] | None:
        svgs = [p for p in paths if p.lower().endswith(".svg")]
        if svgs:
            from . import advanced as A
            if page is None:
                page = view.current_page
            if center is None:
                center = self.visible_center(view, page)
            datas = []
            for p in svgs:
                try:
                    with open(p, "rb") as f:
                        datas.append(f.read())
                except OSError as e:
                    self.ctx.error(_("Aggiungi immagine"), str(e))
                    return None

            def fn(pdf):
                out = []
                for k, d in enumerate(datas):
                    c = pymupdf.Point(center.x + E.CASCADE * k, center.y + E.CASCADE * k)
                    out += A.insert_svg(pdf, page, d, c)
                return out
            res = self.run_change(view, _("Inserisci immagine"), fn)
            if res:
                self.ensure_tool(view)
                self.set_selection(view, page, res)
            paths = [p for p in paths if p not in svgs]
            if not paths:
                return res
        images = []
        skipped = []
        for p in paths:
            if not p.lower().endswith(IMAGE_EXTS):
                skipped.append(os.path.basename(p))
                continue
            try:
                with open(p, "rb") as f:
                    images.append(f.read())
            except OSError as e:
                self.ctx.error(_("Aggiungi immagine"), _("Impossibile leggere «{f}»: {e}").format(
                    f=os.path.basename(p), e=e.strerror))
                return None
        if skipped:
            self.ctx.info(_("Aggiungi immagine"), _("File non inseriti perché non sono immagini:\n{f}").format(
                f="\n".join(skipped)))
        if not images:
            return None
        return self.insert_image_bytes(view, images, page, center, rect)

    def insert_image_bytes(self, view, images: list[bytes], page: int | None = None,
                           center: pymupdf.Point | None = None, rect: pymupdf.Rect | None = None) -> list[int] | None:
        if view is None:
            return None
        if page is None:
            page = view.current_page
        if center is None and rect is None:
            center = self.visible_center(view, page)
        big = [k for k, d in enumerate(images) if E.image_pixels(d) > E.MAX_PIXELS]
        if big and self.ctx.confirm(_("Immagine molto grande"),
                                    _("L'immagine supera i 100 megapixel e appesantirebbe molto il documento. "
                                      "Ridurla a 100 megapixel prima di inserirla?")):
            images = [E.downsample(d, E.MAX_PIXELS) if k in big else d for k, d in enumerate(images)]
        label = _("Inserisci immagine") if len(images) == 1 else _("Inserisci immagini")
        result = self.run_change(view, label, E.insert_images, page, images, center, rect)
        if result:
            self.ensure_tool(view)
            self.set_selection(view, page, result)
        return result

    def visible_center(self, view, page: int) -> pymupdf.Point:
        vp = view.viewport().rect()
        pr = view.page_rect(page)
        area = pr.intersected(vp.toRectF() if hasattr(vp, "toRectF") else vp)
        if area.isEmpty():
            area = pr
        inv, _ok = view.page_transform(page).inverted()
        c = inv.map(area.center())
        return pymupdf.Point(c.x(), c.y())

    def ask_insert_image(self, view) -> None:
        if view is None:
            return
        paths = D.ask_image_paths(self.ctx)
        if paths:
            self.insert_image_files(view, paths)

    def replace_image(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            self.status(_("Seleziona una sola immagine."))
            return
        paths = D.ask_image_paths(self.ctx, multiple=False, title=_("Sostituisci immagine"))
        if not paths:
            return
        try:
            with open(paths[0], "rb") as f:
                data = f.read()
        except OSError as e:
            self.ctx.error(_("Sostituisci immagine"), str(e))
            return
        self.replace_with_bytes(view, data)

    def replace_with_clipboard(self, view) -> None:
        data = clipboard_image_bytes()
        if not data:
            self.status(_("Gli appunti non contengono un'immagine."))
            return
        self.replace_with_bytes(view, data[0])

    def replace_with_bytes(self, view, data: bytes) -> None:
        o = self.selected_objects(view)[0]
        occ = 1
        if o.xref:
            with ENGINE_LOCK:
                occ = len(E.image_occurrences(view.document.pdf, o.xref))
        choice = D.ask_replace_options(self.ctx.window, occ)
        if choice is None:
            return
        mode, all_occ = choice
        result = self.run_change(view, _("Sostituisci immagine"), E.replace_image, self.sel_page, o.index, data,
                                 mode, all_occ)
        if result is not None:
            self.set_selection(view, self.sel_page, result)

    def extract_allowed(self, view) -> bool:
        if view is None:
            return False
        with ENGINE_LOCK:
            perms = view.document.pdf.permissions
        if not view.document.original_protection or perms == -1:
            return True
        return bool(perms & pymupdf.PDF_PERM_COPY)

    _extract_allowed = extract_allowed

    def save_image(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            self.status(_("Seleziona una sola immagine."))
            return
        if not self._extract_allowed(view):
            self.ctx.error(_("Salva immagine"), _("Il documento non consente di estrarre il contenuto."))
            return
        o = objs[0]
        try:
            with ENGINE_LOCK:
                orig = E.extract_image(view.document.pdf, o, "original")
        except E.ObjectError as e:
            self.ctx.error(_("Salva immagine"), str(e))
            return
        base = os.path.splitext(view.document.display_name)[0]
        n = sum(1 for x in (self.parsed(view, self.sel_page).objects[:o.index + 1]) if x.kind == IMAGE)
        name = f"{base}_p{self.sel_page + 1}_img{n}.{orig.ext}"
        filters = f"Originale (*.{orig.ext});;PNG (*.png);;JPEG (*.jpg *.jpeg)"
        path = self.ctx.ask_save_path(_("Salva immagine come"), name, filters, default_suffix="." + orig.ext)
        if not path:
            return
        ext = os.path.splitext(path)[1].lower()
        try:
            with ENGINE_LOCK:
                if ext in (".jpg", ".jpeg") and orig.ext != "jpg":
                    data = E.extract_image(view.document.pdf, o, "jpeg").data
                elif ext == ".png" and orig.ext != "png":
                    data = E.extract_image(view.document.pdf, o, "png").data
                else:
                    data = orig.data
            from foliant.api import atomic_write
            atomic_write(path, data)
        except Exception as e:
            self.ctx.error(_("Salva immagine"), _("Impossibile salvare l'immagine: {e}").format(e=e))
            return
        self.status(_("Immagine salvata in {p}").format(p=path))

    def save_image_as_shown(self, view) -> None:
        """Salva l'immagine come appare (ritaglio, rotazione, capovolgimento, opacità applicati)."""
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            self.status(_("Seleziona una sola immagine."))
            return
        if not self._extract_allowed(view):
            self.ctx.error(_("Salva immagine"), _("Il documento non consente di estrarre il contenuto."))
            return
        o = objs[0]
        base = os.path.splitext(view.document.display_name)[0]
        name = f"{base}_p{self.sel_page + 1}_img{o.index + 1}.png"
        path = self.ctx.ask_save_path(_("Salva immagine come appare"), name, "PNG (*.png)", default_suffix=".png")
        if not path:
            return
        try:
            with ENGINE_LOCK:
                data = E.render_as_shown(view.document.pdf, self.sel_page, o)
            from foliant.api import atomic_write
            atomic_write(path, data)
        except Exception as e:
            self.ctx.error(_("Salva immagine"), _("Impossibile salvare l'immagine: {e}").format(e=e))
            return
        self.status(_("Immagine salvata in {p}").format(p=path))

    def copy_image(self, view) -> None:
        objs = self.selected_objects(view)
        if len(objs) != 1 or objs[0].kind != IMAGE:
            return
        if not self._extract_allowed(view):
            self.ctx.error(_("Copia immagine"), _("Il documento non consente di estrarre il contenuto."))
            return
        try:
            with ENGINE_LOCK:
                data = E.extract_image(view.document.pdf, objs[0], "png").data
        except E.ObjectError as e:
            self.ctx.error(_("Copia immagine"), str(e))
            return
        img = QImage.fromData(data)
        QGuiApplication.clipboard().setImage(img)
        self.status(_("Immagine copiata negli appunti."))

    # ---------------------------------------------------------------- appunti
    def copy(self, view, quiet: bool = False) -> C.Payload | None:
        global _last_payload
        if not self.selection:
            return None
        if not self._extract_allowed(view):
            self.ctx.error(_("Copia"), _("Il documento non consente di copiare il contenuto."))
            return None
        try:
            with ENGINE_LOCK:
                payload = C.make_payload(view.document.pdf, self.sel_page, self.selection)
            payload.meta["doc"] = id(view.document)
        except Exception as e:
            self.ctx.error(_("Copia"), _("Impossibile copiare gli oggetti: {e}").format(e=e))
            return None
        _last_payload = payload
        self._paste_counts.clear()
        mime = QMimeData()
        mime.setData(C.MIME_INTERNAL, QByteArray(payload.to_bytes()))
        try:
            mime.setData("application/pdf", QByteArray(C.payload_pdf(payload)))
            png = C.payload_png(payload)
            mime.setImageData(QImage.fromData(png))
        except Exception:
            pass
        if payload.meta.get("text"):
            mime.setText(payload.meta["text"])
        QGuiApplication.clipboard().setMimeData(mime)
        if not quiet:
            self.status(_("{n} oggetti copiati.").format(n=len(self.selection)))
        return payload

    def cut(self, view) -> None:
        if self.copy(view, quiet=True) is not None:
            if self._apply(view, _("Taglia oggetti"), E.delete_objects, keep=False) is not None:
                self.clear_selection(view)

    def _clipboard_payload(self) -> C.Payload | None:
        mime = QGuiApplication.clipboard().mimeData()
        if mime is not None and mime.hasFormat(C.MIME_INTERNAL):
            try:
                return C.Payload.from_bytes(bytes(mime.data(C.MIME_INTERNAL)))
            except Exception:
                return None
        return None

    def paste(self, view, in_place: bool = False, at: pymupdf.Point | None = None, page: int | None = None) -> None:
        if view is None:
            return
        payload = self._clipboard_payload()
        if payload is None:
            images = clipboard_image_bytes()
            files = clipboard_image_files()
            if files:
                self.insert_image_files(view, files, page if page is not None else view.current_page, center=at)
                return
            if images:
                self.insert_image_bytes(view, images, page if page is not None else view.current_page, center=at)
                return
            if _last_payload is None or QGuiApplication.clipboard().mimeData().hasText() \
                    or QGuiApplication.clipboard().mimeData().hasImage():
                self.status(_("Gli appunti non contengono oggetti o immagini."))
                return
            payload = _last_payload
        if page is None:
            page = self.sel_page if (self.sel_page is not None and self.view is view) else view.current_page
        dx = dy = 0.0
        center = None
        if at is not None:
            center = at
        elif not in_place:
            key = (payload.meta.get("doc"), payload.meta.get("page"), payload.meta.get("rect")[0])
            same = payload.meta.get("doc") == id(view.document) and payload.meta.get("page") == page
            count = self._paste_counts.get((key, id(view.document), page), 1 if same else 0)
            dx = dy = E.PASTE_OFFSET * count
            self._paste_counts[(key, id(view.document), page)] = count + 1
        result = self.run_change(view, _("Incolla oggetti"), C.paste_payload, page, payload, dx, dy, center)
        if result:
            self.ensure_tool(view)
            self.set_selection(view, page, result)

    def duplicate(self, view, dx: float, dy: float) -> None:
        if not self.selection:
            return
        page = self.sel_page
        indices = list(self.selection)

        def fn(pdf):
            payload = C.make_payload(pdf, page, indices)
            return C.paste_payload(pdf, page, payload, dx, dy)
        result = self.run_change(view, _("Duplica oggetti"), fn)
        if result:
            self.set_selection(view, page, result)

    def move_to_page(self, view, target: int) -> None:
        """Sposta gli oggetti selezionati in un'altra pagina (stessa posizione)."""
        if not self.selection or target == self.sel_page:
            return
        page = self.sel_page
        indices = list(self.selection)

        def fn(pdf):
            payload = C.make_payload(pdf, page, indices)
            E.delete_objects(pdf, page, indices)
            return C.paste_payload(pdf, target, payload)
        result = self.run_change(view, _("Sposta in pagina"), fn)
        if result:
            self.set_selection(view, target, result)

    def move_to_page_at(self, view, target: int, point: pymupdf.Point, start: pymupdf.Point) -> None:
        if not self.selection:
            return
        page = self.sel_page
        indices = list(self.selection)
        r = E.union_rect(self.selected_objects(view))
        c = rcenter(r)
        new_center = pymupdf.Point(point.x + (c.x - start.x), point.y + (c.y - start.y))

        def fn(pdf):
            payload = C.make_payload(pdf, page, indices)
            E.delete_objects(pdf, page, indices)
            return C.paste_payload(pdf, target, payload, center=new_center)
        result = self.run_change(view, _("Sposta in pagina"), fn)
        if result:
            self.set_selection(view, target, result)

    def ask_move_to_page(self, view) -> None:
        if not self.selection:
            return
        n = D.ask_page(self.ctx.window, _("Sposta in pagina"), view.document.page_count, self.sel_page)
        if n is not None:
            self.move_to_page(view, n)

    # ---------------------------------------------------------------- strumenti
    def ensure_tool(self, view) -> None:
        if view is not None and view.tool is not self.object_tool:
            if self.ctx.current_mode() != "modifica" and self.ctx.mode("modifica") is not None:
                self.ctx.set_mode("modifica")
            view.set_tool(self.object_tool)
            self.ctx.window._on_tool_changed(self.object_tool) if hasattr(self.ctx.window, "_on_tool_changed") else None

    # ================================================================ collegamenti
    def follow_link_at(self, view, page: int, point: pymupdf.Point) -> bool:
        """Segue il collegamento sotto il punto con la conferma unica del nucleo (VIS-19)."""
        from foliant.api import follow_link
        link = view.link_at(page, point)
        if link is None:
            return False
        follow_link(view, link)
        return True

    def links_selection_changed(self, view) -> None:
        view.viewport().update()
        self.selectionChanged.emit()

    def _doc_path(self, view) -> str | None:
        p = view.document.path
        return str(p) if p else None

    def create_link_interactive(self, view, page: int, rect: pymupdf.Rect, quads=None,
                                default_text: str = "") -> int | None:
        target = L.suggest_from_text(default_text) if default_text else None
        res = D.ask_link(self.ctx.window, view, page, None, target=target, description=default_text)
        if res is None:
            return None
        target, style, desc = res
        D.remember_style(style)
        return self.run_change(view, _("Crea collegamento"), L.create_link, page, rect, target, style, desc, quads,
                               kind="annotation")

    def create_link_from_selection(self, view) -> None:
        objs = self.selected_objects(view)
        if objs:
            r = E.union_rect(objs)
            if r.width < 4 or r.height < 4:
                r = pymupdf.Rect(r.x0, r.y0, max(r.x1, r.x0 + 4), max(r.y1, r.y0 + 4))
            self.create_link_interactive(view, self.sel_page, r)
            return
        sels = [x for x in (getattr(view, "text_selections", None) or
                            ([view.text_selection] if view.text_selection is not None else [])) if x.rects]
        if sels:
            text = " ".join(x.text for x in sels).strip()
            target = L.suggest_from_text(text) if text else None
            res = D.ask_link(self.ctx.window, view, sels[0].page, None, target=target, description=text)
            if res is None:
                return
            target, style, desc = res
            D.remember_style(style)
            parts = [(x.page, line_quads(x)) for x in sels]

            def fn(pdf):
                out = []
                for page, quads in parts:   # un collegamento per pagina, stessa destinazione
                    rect = pymupdf.Rect(quads[0].rect)
                    for q in quads[1:]:
                        rect |= q.rect
                    if rect.width < 4 or rect.height < 4:
                        rect = pymupdf.Rect(rect.x0, rect.y0, max(rect.x1, rect.x0 + 4), max(rect.y1, rect.y0 + 4))
                    out.append(L.create_link(pdf, page, rect, target, style, desc, quads))
                return out
            self.run_change(view, _("Crea collegamento"), fn, kind="annotation")
            return
        self.status(_("Seleziona del testo o un oggetto, oppure usa lo strumento Collegamento."))

    def edit_link_interactive(self, view, page: int, xrefs: list[int]) -> None:
        infos = [i for i in self.links(view, page) if i.xref in xrefs]
        if not infos:
            return
        res = D.ask_link(self.ctx.window, view, page, infos[0], multiple=len(infos) > 1)
        if res is None:
            return
        target, style, desc = res

        def fn(pdf):
            for info in infos:
                same_target = target is None or (len(infos) > 1)
                L.update_link(pdf, page, info.xref, target=None if same_target else target, style=style,
                              description=None if len(infos) > 1 else desc)
            return True
        self.run_change(view, _("Modifica collegamento"), fn, kind="annotation")

    def move_links(self, view, page: int, xrefs: list[int], dx: float, dy: float) -> None:
        def fn(pdf):
            for x in xrefs:
                if L.read_link(pdf, page, x).locked:
                    raise L.LinkError(_("Il collegamento è bloccato e non si può spostare."))
                L.update_link(pdf, page, x, move=(dx, dy))
            return True
        self.run_change(view, _("Sposta collegamento"), fn, kind="annotation")

    def resize_link(self, view, page: int, xref: int, rect: pymupdf.Rect) -> None:
        self.run_change(view, _("Ridimensiona collegamento"), lambda pdf: L.update_link(pdf, page, xref, rect=rect) or True,
                        kind="annotation")

    def delete_links(self, view, page: int, xrefs: list[int]) -> None:
        self.run_change(view, _("Elimina collegamenti"), L.delete_links, page, xrefs, kind="annotation")
        self.link_tool.selection = []

    def copy_links(self, view, page: int, xrefs: list[int], clipboard: bool = True) -> None:
        global _last_links
        with ENGINE_LOCK:
            _last_links = [L.read_link(view.document.pdf, page, x) for x in xrefs]
        self._link_paste_count = 0

    def paste_links(self, view, offset: float = 0.0, use_last: bool = True) -> None:
        if not _last_links:
            return
        page = self.link_tool.page if self.link_tool.page is not None else view.current_page
        self._link_paste_count = getattr(self, "_link_paste_count", 0) + 1
        off = offset or E.PASTE_OFFSET * self._link_paste_count

        def fn(pdf):
            out = []
            for info in _last_links:
                t = info.target
                if t.kind == L.PAGE and t.page >= pdf.page_count:
                    t = L.LinkTarget(kind=L.PAGE, page=pdf.page_count - 1)
                r = pymupdf.Rect(info.rect.x0 + off, info.rect.y0 + off, info.rect.x1 + off, info.rect.y1 + off)
                if t.kind == L.OTHER:
                    continue
                out.append(L.create_link(pdf, page, r, t, info.style, info.description))
            return out
        res = self.run_change(view, _("Incolla collegamenti"), fn, kind="annotation")
        if res:
            self.link_tool.selection = list(res)
            self.link_tool.page = page

    def detect_urls(self, view) -> None:
        if view is None:
            return
        opts = D.ask_url_scope(self.ctx.window, view.document.page_count, view.current_page)
        if opts is None:
            return
        pages, web, email = opts

        def work(progress):
            found = []
            for k, i in enumerate(pages):
                progress.update(k / max(1, len(pages)), _("Pagina {n}").format(n=i + 1))
                with ENGINE_LOCK:
                    try:
                        found += L.detect_urls(view.document.pdf, i, web, email)
                    except Exception:
                        continue   # pagina danneggiata
            return found
        try:
            found = self.ctx.run_task(_("Ricerca degli indirizzi"), work)
        except Exception as e:
            self.ctx.error(_("Crea collegamenti dagli URL"), str(e))
            return
        if found is None:
            return
        if not found:
            with ENGINE_LOCK:
                has_text = any(view.document.pdf[i].get_text("text").strip() for i in pages[:20])
            if not has_text:
                self.ctx.info(_("Crea collegamenti dagli URL"),
                              _("Il documento non contiene testo: esegui prima il riconoscimento del testo (OCR)."))
            else:
                self.ctx.info(_("Crea collegamenti dagli URL"), _("Nessun indirizzo da collegare."))
            return
        chosen = D.ask_found_urls(self.ctx.window, found)
        if not chosen:
            return
        n = self.run_change(view, _("Crea collegamenti dagli URL"), L.create_links_for, chosen, kind="annotation")
        if n:
            self.status(_("Creati {n} collegamenti.").format(n=n))

    def delete_links_dialog(self, view) -> None:
        if view is None:
            return
        opts = D.ask_delete_links(self.ctx.window, view)
        if opts is None:
            return
        pages, cats = opts
        with ENGINE_LOCK:
            n = L.count_links_in(view.document.pdf, pages, cats)
        if n == 0:
            self.ctx.info(_("Elimina collegamenti"), _("Nessun collegamento da eliminare."))
            return
        if not self.ctx.confirm(_("Elimina collegamenti"), _("Eliminare {n} collegamenti?").format(n=n)):
            return
        self.run_change(view, _("Elimina collegamenti"), L.delete_links_in, pages, cats, kind="annotation")

    def selected_link_page(self) -> int | None:
        return self.link_tool.page


def _translate_with_links(pdf, page, indices, dx, dy):
    """Sposta gli oggetti e i collegamenti con lo stesso riquadro (±1 pt)."""
    parsed = parse_page(pdf, pdf[page])
    rects = [parsed.objects[i].rect for i in indices if 0 <= i < len(parsed.objects)]
    attached = []
    for info in L.page_links(pdf, page):
        r = info.rect
        if any(abs(r.x0 - o.x0) <= 1 and abs(r.y0 - o.y0) <= 1 and abs(r.x1 - o.x1) <= 1 and abs(r.y1 - o.y1) <= 1
               for o in rects):
            attached.append(info.xref)
    res = E.translate(pdf, page, indices, dx, dy)
    for x in attached:
        L.update_link(pdf, page, x, move=(dx, dy))
    return res


def line_quads(sel) -> list:
    """Un quadrilatero per riga di testo selezionata (OBJ-34)."""
    words = getattr(sel, "words", None) or []
    if not words:
        return [pymupdf.Rect(r).quad for r in sel.rects]
    lines: dict = {}
    order = []
    for w in words:
        key = (w[5], w[6])
        r = pymupdf.Rect(w[:4])
        if key in lines:
            lines[key] |= r
        else:
            lines[key] = r
            order.append(key)
    return [lines[k].quad for k in order]


class _NoChange(Exception):
    pass


def _reorder_or_raise(pdf, page, indices, where):
    res = E.reorder(pdf, page, indices, where)
    if res is None:
        raise E.ObjectError(_("L'ordine non cambia: nessun oggetto si sovrappone nella direzione richiesta."))
    return res


def clipboard_image_bytes() -> list[bytes]:
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None:
        return []
    for fmt in ("image/png", "image/tiff", "image/webp", "image/jpeg", "image/bmp", "image/gif"):
        if mime.hasFormat(fmt):
            data = bytes(mime.data(fmt))
            if data:
                return [data]
    if mime.hasImage():
        img = mime.imageData()
        if isinstance(img, QImage) and not img.isNull():
            from PySide6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            return [bytes(buf.data())]
    return []


def clipboard_image_files() -> list[str]:
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None or not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and u.toLocalFile().lower().endswith(IMAGE_EXTS)]


def clipboard_has_image() -> bool:
    mime = QGuiApplication.clipboard().mimeData()
    if mime is None:
        return False
    return bool(mime.hasFormat(C.MIME_INTERNAL) or clipboard_image_bytes() or clipboard_image_files())


__all__ = ["ObjectsController", "QPointF"]
