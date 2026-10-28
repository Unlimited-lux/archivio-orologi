"""Logica pura di posizione per file (VIS-02), vista iniziale (VIS-05) e sessione (VIS-30).

Nessuna dipendenza dall'interfaccia: le posizioni sono dizionari semplici
serializzati in JSON, la vista iniziale si legge dal catalogo del PDF.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import pymupdf

from foliant.api import ENGINE_LOCK, FIT_PAGE, FIT_WIDTH, VIEW_CONTINUOUS, VIEW_COVER, VIEW_FACING, VIEW_SINGLE

MAX_POSITIONS = 200
MAX_CLOSED = 30

PAGE_LAYOUTS = {
    "SinglePage": VIEW_SINGLE,
    "OneColumn": VIEW_CONTINUOUS,
    "TwoColumnLeft": VIEW_FACING,
    "TwoColumnRight": VIEW_COVER,
    "TwoPageLeft": VIEW_FACING,
    "TwoPageRight": VIEW_COVER,
}

PAGE_MODES = {
    "UseOutlines": "bookmarks",
    "UseThumbs": "thumbnails",
    "UseAttachments": "attachments",
    "UseOC": "layers",
}

_FIT_KINDS = {
    "Fit": FIT_PAGE, "FitB": FIT_PAGE, "FitV": FIT_PAGE, "FitBV": FIT_PAGE, "FitR": FIT_PAGE,
    "FitH": FIT_WIDTH, "FitBH": FIT_WIDTH,
}

_DEST_RE = re.compile(r"\[\s*(?:(\d+)\s+(\d+)\s+R|(\d+))\s*/(\w+)([^\]]*)\]")


# ====================================================================== stato vista
@dataclass
class ViewState:
    """Posizione di lettura: pagina (da 0), zoom o modalità adatta, layout."""
    page: int = 0
    zoom: float | None = None
    fit: str | None = None
    mode: str | None = None

    def to_dict(self) -> dict:
        return {"page": int(self.page), "zoom": self.zoom, "fit": self.fit, "mode": self.mode}

    @classmethod
    def from_dict(cls, d: dict) -> "ViewState | None":
        if not isinstance(d, dict):
            return None
        try:
            page = max(0, int(d.get("page", 0) or 0))
            zoom = d.get("zoom")
            zoom = float(zoom) if zoom not in (None, "") else None
        except (TypeError, ValueError):
            return None
        fit = d.get("fit") if d.get("fit") in (FIT_WIDTH, FIT_PAGE) else None
        mode = d.get("mode") if d.get("mode") in (VIEW_CONTINUOUS, VIEW_SINGLE, VIEW_FACING, VIEW_COVER) else None
        return cls(page, zoom, fit, mode)


def view_state(view) -> ViewState:
    """Stato corrente di una DocumentView (qualunque oggetto con gli stessi attributi)."""
    return ViewState(view.current_page, None if view.fit_mode else float(view.zoom), view.fit_mode, view.view_mode)


# ====================================================================== posizioni per file
def load_positions(raw) -> dict[str, dict]:
    if not raw:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    except ValueError:
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(data, dict) else {}


def remember_position(positions: dict[str, dict], path: str, state: ViewState,
                      now: float | None = None, limit: int = MAX_POSITIONS) -> dict[str, dict]:
    """Memorizza la posizione di `path` (le voci più vecchie oltre `limit` vengono rimosse)."""
    entry = state.to_dict()
    entry["t"] = time.time() if now is None else now
    positions = dict(positions)
    positions[path] = entry
    if len(positions) > limit:
        ordered = sorted(positions.items(), key=lambda kv: kv[1].get("t", 0), reverse=True)
        positions = dict(ordered[:limit])
    return positions


def dump_positions(positions: dict[str, dict]) -> str:
    return json.dumps(positions, ensure_ascii=False)


# ====================================================================== sessione
def dump_session(tabs: list[tuple[str, ViewState]], current: int) -> str:
    return json.dumps({"tabs": [dict(path=p, **s.to_dict()) for p, s in tabs], "current": current},
                      ensure_ascii=False)


def load_session(raw) -> tuple[list[tuple[str, ViewState]], int]:
    if not raw:
        return [], 0
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return [], 0
    if not isinstance(data, dict):
        return [], 0
    tabs = []
    for item in data.get("tabs", []) or []:
        state = ViewState.from_dict(item)
        if state is not None and isinstance(item.get("path"), str) and item["path"]:
            tabs.append((item["path"], state))
    try:
        current = int(data.get("current", 0))
    except (TypeError, ValueError):
        current = 0
    return tabs, current


# ====================================================================== vista iniziale
@dataclass
class InitialView:
    page: int | None = None
    fit: str | None = None
    zoom: float | None = None
    mode: str | None = None
    panel: str | None = None
    fullscreen: bool = False

    @property
    def empty(self) -> bool:
        return (self.page is None and self.fit is None and self.zoom is None and self.mode is None
                and self.panel is None and not self.fullscreen)


def parse_dest(text: str) -> tuple[tuple[str, int] | None, str | None, float | None]:
    """Analizza una destinazione esplicita `[pagina /Tipo parametri]`.

    Restituisce ((«xref»|«index», numero) | None, fit | None, zoom | None)."""
    m = _DEST_RE.search(text or "")
    if not m:
        return None, None, None
    ref = ("xref", int(m.group(1))) if m.group(1) else ("index", int(m.group(3)))
    kind = m.group(4)
    fit = _FIT_KINDS.get(kind)
    zoom = None
    if kind == "XYZ":
        params = m.group(5).split()
        if len(params) >= 3:
            try:
                z = float(params[2])
                zoom = z if z > 0 else None
            except ValueError:
                zoom = None
    return ref, fit, zoom


def _name(value: tuple[str, str]) -> str | None:
    kind, text = value
    if kind == "name" and text:
        return text.lstrip("/")
    return None


def _page_index(pdf: pymupdf.Document, ref: tuple[str, int]) -> int | None:
    kind, number = ref
    if kind == "index":
        return number if 0 <= number < pdf.page_count else None
    for i in range(pdf.page_count):
        if pdf.page_xref(i) == number:
            return i
    return None


def _resolve_open_action(pdf: pymupdf.Document, cat: int) -> tuple[int | None, str | None, float | None]:
    kind, value = pdf.xref_get_key(cat, "OpenAction")
    if kind in ("null", ""):
        return None, None, None
    if kind == "xref":
        xref = int(value.split()[0])
        if pdf.xref_is_stream(xref):
            return None, None, None
        obj = pdf.xref_object(xref, compressed=True)
        if obj.lstrip().startswith("["):
            text = obj
        else:
            if _name(pdf.xref_get_key(xref, "S")) not in ("GoTo", None):
                return None, None, None   # JavaScript o altre azioni: mai eseguite
            dkind, dvalue = pdf.xref_get_key(xref, "D")
            if dkind == "xref":
                dvalue = pdf.xref_object(int(dvalue.split()[0]), compressed=True)
            elif dkind in ("string", "name"):
                return _named_dest(pdf, dvalue.lstrip("/"))
            text = dvalue
    elif kind == "array":
        text = value
    elif kind == "dict":
        if "/GoTo" not in value:
            return None, None, None
        m = re.search(r"/D\s*(\[[^\]]*\])", value)
        if not m:
            n = re.search(r"/D\s*\((.*?)\)", value)
            return _named_dest(pdf, n.group(1)) if n else (None, None, None)
        text = m.group(1)
    else:
        return None, None, None
    ref, fit, zoom = parse_dest(text)
    page = _page_index(pdf, ref) if ref else None
    return page, fit, zoom


def _named_dest(pdf: pymupdf.Document, name: str) -> tuple[int | None, str | None, float | None]:
    try:
        names = pdf.resolve_names()
    except Exception:  # noqa: BLE001
        return None, None, None
    dest = names.get(name)
    if not isinstance(dest, dict):
        return None, None, None
    page = dest.get("page")
    page = page if isinstance(page, int) and 0 <= page < pdf.page_count else None
    zoom = dest.get("zoom")
    zoom = float(zoom) if isinstance(zoom, (int, float)) and zoom > 0 else None
    return page, None, zoom


def read_initial_view(pdf: pymupdf.Document) -> InitialView:
    """Vista iniziale chiesta dal documento (`/OpenAction`, `/PageLayout`, `/PageMode`).

    Va chiamata tenendo ENGINE_LOCK (la funzione lo prende comunque). Un catalogo
    malformato non solleva mai eccezioni: restituisce una vista vuota."""
    result = InitialView()
    with ENGINE_LOCK:
        try:
            if not pdf.is_pdf:
                return result
            cat = pdf.pdf_catalog()
            if cat <= 0:
                return result
        except Exception:  # noqa: BLE001
            return result
        try:
            result.mode = PAGE_LAYOUTS.get(_name(pdf.xref_get_key(cat, "PageLayout")) or "")
        except Exception:  # noqa: BLE001
            pass
        try:
            page_mode = _name(pdf.xref_get_key(cat, "PageMode")) or ""
            result.panel = PAGE_MODES.get(page_mode)
            result.fullscreen = page_mode == "FullScreen"
        except Exception:  # noqa: BLE001
            pass
        try:
            result.page, result.fit, result.zoom = _resolve_open_action(pdf, cat)
        except Exception:  # noqa: BLE001
            pass
    return result


# ================================================================ opzioni di apertura (VIS-42)
LAYOUT_NAMES = {VIEW_SINGLE: "SinglePage", VIEW_CONTINUOUS: "OneColumn", VIEW_FACING: "TwoColumnLeft",
                VIEW_COVER: "TwoColumnRight"}
PANEL_NAMES = {"": "UseNone", "bookmarks": "UseOutlines", "thumbnails": "UseThumbs",
               "attachments": "UseAttachments", "layers": "UseOC"}
DUPLEX = ("", "Simplex", "DuplexFlipShortEdge", "DuplexFlipLongEdge")
_BOOL_PREFS = (("hide_toolbar", "HideToolbar"), ("hide_menubar", "HideMenubar"),
               ("hide_window_ui", "HideWindowUI"), ("fit_window", "FitWindow"),
               ("center_window", "CenterWindow"), ("display_title", "DisplayDocTitle"),
               ("pick_tray", "PickTrayByPDFSize"))


@dataclass
class OpenOptions:
    """Come il documento si presenta in qualunque lettore: scritto nel catalogo."""
    page: int = 0                      # pagina iniziale (0-based)
    fit: str | None = None             # FIT_WIDTH, FIT_PAGE o None (zoom)
    zoom: float | None = None          # fattore (1.0 = 100%); None = invariato
    layout: str | None = None          # VIEW_*; None = predefinito del lettore
    panel: str = ""                    # "", "bookmarks", "thumbnails", "attachments", "layers"
    fullscreen: bool = False
    hide_toolbar: bool = False
    hide_menubar: bool = False
    hide_window_ui: bool = False
    fit_window: bool = False
    center_window: bool = False
    display_title: bool = False
    pick_tray: bool = False
    direction_rtl: bool = False
    print_scaling_none: bool = False   # /PrintScaling /None (stampa a dimensione reale)
    duplex: str = ""                   # uno di DUPLEX
    num_copies: int = 1


def _bool_key(pdf: pymupdf.Document, xref: int, key: str) -> bool:
    try:
        kind, value = pdf.xref_get_key(xref, key)
    except Exception:  # noqa: BLE001
        return False
    return kind == "bool" and value == "true"


def read_open_options(pdf: pymupdf.Document) -> OpenOptions:
    """Opzioni di apertura attuali del documento (da chiamare con ENGINE_LOCK)."""
    o = OpenOptions()
    initial = read_initial_view(pdf)
    o.page = initial.page or 0
    o.fit, o.zoom = initial.fit, initial.zoom
    o.layout = initial.mode
    o.panel = initial.panel or ""
    o.fullscreen = initial.fullscreen
    try:
        cat = pdf.pdf_catalog()
    except Exception:  # noqa: BLE001
        return o
    for attr, key in _BOOL_PREFS:
        setattr(o, attr, _bool_key(pdf, cat, "ViewerPreferences/" + key))
    try:
        o.direction_rtl = _name(pdf.xref_get_key(cat, "ViewerPreferences/Direction")) == "R2L"
        o.print_scaling_none = _name(pdf.xref_get_key(cat, "ViewerPreferences/PrintScaling")) == "None"
        duplex = _name(pdf.xref_get_key(cat, "ViewerPreferences/Duplex")) or ""
        o.duplex = duplex if duplex in DUPLEX else ""
        kind, value = pdf.xref_get_key(cat, "ViewerPreferences/NumCopies")
        if kind == "int":
            o.num_copies = max(1, min(999, int(value)))
    except Exception:  # noqa: BLE001
        pass
    return o


def open_action_is_script(pdf: pymupdf.Document) -> bool:
    """`/OpenAction` è un'azione diversa da una destinazione (JavaScript, avvio di file…):
    va segnalata prima di sostituirla (mai eseguita, NFR-17)."""
    try:
        cat = pdf.pdf_catalog()
        kind, value = pdf.xref_get_key(cat, "OpenAction")
    except Exception:  # noqa: BLE001
        return False
    if kind == "xref":
        value = pdf.xref_object(int(value.split()[0]), compressed=True)
    elif kind not in ("dict",):
        return False
    m = re.search(r"/S\s*/(\w+)", value)
    return bool(m and m.group(1) != "GoTo")


def write_open_options(pdf: pymupdf.Document, o: OpenOptions) -> None:
    """Scrive le opzioni nel catalogo (dentro `Document.change()`)."""
    if not 0 <= o.page < pdf.page_count:
        raise ValueError(f"pagina iniziale fuori intervallo: {o.page + 1}")
    cat = pdf.pdf_catalog()
    page_ref = f"{pdf.page_xref(o.page)} 0 R"
    if o.fit == FIT_WIDTH:
        dest = f"[{page_ref} /FitH null]"
    elif o.fit == FIT_PAGE:
        dest = f"[{page_ref} /Fit]"
    elif o.zoom:
        dest = f"[{page_ref} /XYZ null null {o.zoom:g}]"
    else:
        dest = f"[{page_ref} /XYZ null null 0]"
    pdf.xref_set_key(cat, "OpenAction", dest)
    pdf.xref_set_key(cat, "PageLayout", "/" + LAYOUT_NAMES[o.layout] if o.layout in LAYOUT_NAMES else "null")
    mode = "FullScreen" if o.fullscreen else PANEL_NAMES.get(o.panel or "", "UseNone")
    pdf.xref_set_key(cat, "PageMode", "/" + mode)
    vp: list[str] = []
    for attr, key in _BOOL_PREFS:
        if getattr(o, attr):
            vp.append(f"/{key} true")
    if o.fullscreen:
        vp.append(f"/NonFullScreenPageMode /{PANEL_NAMES.get(o.panel or '', 'UseNone')}")
    if o.direction_rtl:
        vp.append("/Direction /R2L")
    if o.print_scaling_none:
        vp.append("/PrintScaling /None")
    if o.duplex in DUPLEX[1:]:
        vp.append(f"/Duplex /{o.duplex}")
    if o.num_copies > 1:
        vp.append(f"/NumCopies {int(o.num_copies)}")
    pdf.xref_set_key(cat, "ViewerPreferences", "<<" + " ".join(vp) + ">>" if vp else "null")


def describe_open_options(o: OpenOptions, page_label: str, tr) -> str:
    """Riepilogo testuale dell'effetto delle opzioni (`tr` = funzione di traduzione)."""
    if o.fit == FIT_WIDTH:
        zoom = tr("adattata in larghezza")
    elif o.fit == FIT_PAGE:
        zoom = tr("adattata alla finestra")
    elif o.zoom:
        zoom = tr("zoom {z}%").format(z=round(o.zoom * 100))
    else:
        zoom = tr("con lo zoom del lettore")
    parts = [tr("Si apre a pagina {p}, {zoom}").format(p=page_label, zoom=zoom)]
    panels = {"bookmarks": tr("con i segnalibri visibili"), "thumbnails": tr("con le miniature visibili"),
              "attachments": tr("con gli allegati visibili"), "layers": tr("con i livelli visibili")}
    if o.panel in panels:
        parts.append(panels[o.panel])
    if o.fullscreen:
        parts.append(tr("a schermo intero"))
    if o.display_title:
        parts.append(tr("mostrando il titolo del documento"))
    return ", ".join(parts) + "."
