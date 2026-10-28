"""Sanificazione, analisi dei contenuti attivi e metadati. Logica pura."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import pymupdf

from foliant.api import _

# ------------------------------------------------------------------- analisi

_RX_JS = re.compile(r"/S\s*/JavaScript\b|/JS\s*[(<\d]")
_RX_LAUNCH = re.compile(r"/S\s*/Launch\b")
_RX_URI = re.compile(r"/S\s*/URI\b")
_RX_GOTOR = re.compile(r"/S\s*/(?:GoToR|GoToE|ImportData)\b")
_RX_SUBMIT = re.compile(r"/S\s*/SubmitForm\b")


@dataclass
class ScanReport:
    javascript: int = 0
    launch: int = 0
    external_links: list[str] = field(default_factory=list)   # indirizzi web (unici)
    remote_files: int = 0      # collegamenti/azioni verso altri file
    submit_forms: int = 0      # moduli che inviano dati a un indirizzo
    embedded_files: int = 0
    hidden_layers: int = 0
    comments: int = 0
    form_fields: int = 0
    has_metadata: bool = False
    items: list[tuple[str, str, str]] = field(default_factory=list)   # (tipo, posizione, dettaglio)

    @property
    def risky(self) -> bool:
        """Contenuti che giustificano un avviso all'apertura."""
        return bool(self.javascript or self.launch or self.external_links or self.remote_files
                    or self.submit_forms or self.embedded_files)

    def lines(self) -> list[str]:
        out = []
        if self.javascript:
            out.append(_("Script (JavaScript): {n}").format(n=self.javascript))
        if self.launch:
            out.append(_("Azioni che avviano programmi o file: {n}").format(n=self.launch))
        if self.submit_forms:
            out.append(_("Moduli che inviano dati in rete: {n}").format(n=self.submit_forms))
        if self.external_links:
            out.append(_("Collegamenti a siti esterni: {n}").format(n=len(self.external_links)))
        if self.remote_files:
            out.append(_("Collegamenti ad altri file: {n}").format(n=self.remote_files))
        if self.embedded_files:
            out.append(_("File incorporati: {n}").format(n=self.embedded_files))
        return out


def _uri_of(obj: str) -> str | None:
    m = re.search(r"/URI\s*\(((?:\\.|[^\\)])*)\)", obj)
    return m.group(1) if m else None


def scan(pdf: pymupdf.Document, max_objects: int = 400_000) -> ScanReport:
    """Cerca script, azioni e collegamenti esterni, file incorporati, livelli nascosti."""
    rep = ScanReport()
    uris: set[str] = set()
    if not pdf.is_pdf:
        return rep
    locations = _locations(pdf)
    n = min(pdf.xref_length(), max_objects)
    for xref in range(1, n):
        try:
            obj = pdf.xref_object(xref, compressed=True)
        except Exception:
            continue
        if not obj or obj == "null":
            continue
        if "/S" in obj or "/JS" in obj:
            where = locations.get(xref, _("Documento"))
            if _RX_JS.search(obj):
                rep.javascript += 1
                rep.items.append(("javascript", where, _js_code(pdf, xref)))
            if _RX_LAUNCH.search(obj):
                rep.launch += 1
                rep.items.append(("launch", where, _target(obj)))
            if _RX_GOTOR.search(obj):
                rep.remote_files += 1
                rep.items.append(("remote", where, _target(obj)))
            if _RX_SUBMIT.search(obj):
                rep.submit_forms += 1
                rep.items.append(("submit", where, _target(obj)))
            if _RX_URI.search(obj):
                uri = _uri_of(obj)
                if uri:
                    uris.add(uri)
    # collegamenti con indirizzo diretto (senza oggetto azione separato)
    for page in pdf:
        for link in page.get_links():
            if link.get("kind") == pymupdf.LINK_URI and link.get("uri"):
                uris.add(link["uri"])
        for annot in page.annots():
            if annot.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                rep.embedded_files += 1
            if annot.type[0] not in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET, pymupdf.PDF_ANNOT_REDACT):
                rep.comments += 1
        rep.form_fields += sum(1 for _w in page.widgets())
    rep.external_links = sorted(uris)
    rep.items.extend(("uri", "", u) for u in rep.external_links)
    try:
        rep.embedded_files += pdf.embfile_count()
    except Exception:
        pass
    rep.hidden_layers = len(hidden_layers(pdf))
    meta = pdf.metadata or {}
    rep.has_metadata = any(v for k, v in meta.items() if k not in ("format", "encryption")) or bool(
        _xml_metadata(pdf))
    return rep


def _locations(pdf: pymupdf.Document) -> dict[int, str]:
    """Dove si trova un oggetto azione: apertura del documento o pagina."""
    loc: dict[int, str] = {}

    def ref(xref: int, key: str) -> int | None:
        try:
            kind, val = pdf.xref_get_key(xref, key)
        except Exception:
            return None
        if kind == "xref":
            return int(val.split()[0])
        return None

    cat = pdf.pdf_catalog()
    x = ref(cat, "OpenAction")
    if x:
        loc[x] = _("Apertura del documento")
    for i in range(pdf.page_count):
        label = _("Pagina {n}").format(n=i + 1)
        page = pdf[i]
        loc.setdefault(page.xref, label)
        for key in ("AA/O", "AA/C"):
            x = ref(page.xref, key)
            if x:
                loc.setdefault(x, label)
        for annot_xref in [a[0] for a in page.annot_xrefs()]:
            loc.setdefault(annot_xref, label)
            for key in ("A", "AA/U", "AA/D", "AA/K", "AA/F", "AA/V", "AA/C", "AA/E", "AA/X"):
                x = ref(annot_xref, key)
                if x:
                    loc.setdefault(x, label)
    return loc


def _js_code(pdf: pymupdf.Document, xref: int) -> str:
    try:
        kind, val = pdf.xref_get_key(xref, "JS")
        if kind == "string":
            code = val
        elif kind == "xref":
            code = pdf.xref_stream(int(val.split()[0])).decode("latin-1", "replace")
        else:
            code = ""
    except Exception:
        code = ""
    code = " ".join(code.split())
    return code[:400] + ("…" if len(code) > 400 else "")


def _target(obj: str) -> str:
    m = re.search(r"/(?:F|URL)\s*\(((?:\\.|[^\\)])*)\)", obj)
    if m:
        return m.group(1)
    m = re.search(r"/(?:F|URL)\s*<<.*?/F\s*\(((?:\\.|[^\\)])*)\)", obj)
    return m.group(1) if m else ""


def _xml_metadata(pdf: pymupdf.Document) -> str:
    try:
        return pdf.get_xml_metadata() or ""
    except Exception:
        return ""


# ------------------------------------------------------------ livelli nascosti


def hidden_layers(pdf: pymupdf.Document) -> list[int]:
    """xref dei livelli (contenuto opzionale) spenti nella configurazione predefinita."""
    try:
        ocgs = pdf.get_ocgs() or {}
    except Exception:
        return []
    return [xref for xref, info in ocgs.items() if not info.get("on", True)]


_WS = frozenset(b" \t\r\n\f\x00")
_DELIM = frozenset(b"()<>[]{}/%")
_RX_EI = re.compile(rb"\sEI(?=[\s/\[<(%]|$)")


def _tokens(data: bytes):
    """Scompone uno stream di contenuto in token (posizione, testo).

    Gestisce stringhe letterali, esadecimali, dizionari, commenti e immagini in
    linea, quanto basta per riconoscere in modo affidabile BDC/BMC/EMC."""
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b in _WS:
            i += 1
            continue
        start = i
        if b == 0x25:  # %
            while i < n and data[i] not in (0x0D, 0x0A):
                i += 1
            continue
        if b == 0x28:  # (
            depth = 0
            while i < n:
                ch = data[i]
                if ch == 0x5C:  # \
                    i += 2
                    continue
                if ch == 0x28:
                    depth += 1
                elif ch == 0x29:
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            yield start, data[start:i]
            continue
        two = data[i:i + 2]
        if two in (b"<<", b">>"):
            i += 2
            yield start, two
            continue
        if b == 0x3C:  # <
            j = data.find(b">", i)
            i = n if j < 0 else j + 1
            yield start, data[start:i]
            continue
        if b in b"[]{}":
            i += 1
            yield start, data[start:i]
            continue
        if b == 0x2F:  # /
            i += 1
        while i < n and data[i] not in _WS and data[i] not in _DELIM:
            i += 1
        if i == start:
            i += 1
        tok = data[start:i]
        yield start, tok
        if tok == b"ID":
            # dati binari di un'immagine in linea fino a EI
            m = _RX_EI.search(data, i + 1)
            i = n if m is None else m.start() + 1


def strip_marked_content(data: bytes, names: set[bytes]) -> tuple[bytes, int]:
    """Elimina dallo stream le sezioni `/OC /<nome> BDC … EMC` con i nomi indicati."""
    if not names:
        return data, 0
    out = bytearray()
    last = 0
    stack: list[tuple[bool, int]] = []     # (da eliminare, inizio)
    window: list[tuple[int, bytes]] = []   # ultimi token (per riconoscere /OC /nome BDC)
    removed = 0
    skip_depth = 0
    for pos, tok in _tokens(data):
        if tok in (b"BDC", b"BMC"):
            drop = False
            begin = pos
            if tok == b"BDC" and len(window) >= 2 and window[-2][1] == b"/OC" and window[-1][1][1:] in names:
                drop = True
                begin = window[-2][0]
            stack.append((drop, begin))
            if drop:
                if skip_depth == 0:
                    out += data[last:begin]
                skip_depth += 1
        elif tok == b"EMC" and stack:
            drop, _begin = stack.pop()
            if drop:
                skip_depth -= 1
                if skip_depth == 0:
                    last = pos + 3
                    removed += 1
        window.append((pos, tok))
        if len(window) > 3:
            window.pop(0)
    if skip_depth:
        # struttura non chiusa: non si tocca nulla per sicurezza
        return data, 0
    out += data[last:]
    return bytes(out), removed


def _page_oc_names(pdf: pymupdf.Document, page: pymupdf.Page, hidden: set[int]) -> set[bytes]:
    names: set[bytes] = set()
    try:
        kind, props = pdf.xref_get_key(page.xref, "Resources/Properties")
    except Exception:
        return names
    if kind == "xref":
        props = pdf.xref_object(int(props.split()[0]), compressed=True)
    elif kind != "dict":
        return names
    for m in re.finditer(r"/([^\s/<>\[\]()]+)\s+(\d+)\s+0\s+R", props):
        if int(m.group(2)) in hidden:
            names.add(m.group(1).encode("latin-1"))
    return names


def remove_hidden_layers(pdf: pymupdf.Document) -> int:
    """Elimina il contenuto dei livelli spenti (testo, immagini, grafica e annotazioni)."""
    hidden = set(hidden_layers(pdf))
    if not hidden:
        return 0
    removed = 0
    for page in pdf:
        names = _page_oc_names(pdf, page, hidden)
        if names:
            page.clean_contents()   # un solo stream di contenuto
            for xref in page.get_contents():
                data = pdf.xref_stream(xref)
                new, n = strip_marked_content(data, names)
                if n:
                    pdf.update_stream(xref, new)
                    removed += n
        for annot in list(page.annots()):
            try:
                oc = annot.get_oc()
            except Exception:
                oc = 0
            if oc and oc in hidden:
                page.delete_annot(annot)
                removed += 1
    # immagini e moduli grafici marcati come opzionali e spenti
    for xref in range(1, pdf.xref_length()):
        try:
            kind, val = pdf.xref_get_key(xref, "OC")
        except Exception:
            continue
        if kind == "xref" and int(val.split()[0]) in hidden and pdf.xref_is_stream(xref):
            sub = pdf.xref_get_key(xref, "Subtype")[1]
            if sub == "/Form":
                pdf.update_stream(xref, b"")
                removed += 1
            elif sub == "/Image":
                # immagine sostituita da un pixel trasparente
                pdf.update_stream(xref, b"\x00")
                for key, value in (("Width", "1"), ("Height", "1"), ("BitsPerComponent", "8"),
                                   ("ColorSpace", "/DeviceGray"), ("Filter", "null"), ("DecodeParms", "null"),
                                   ("SMask", "null"), ("Mask", "null")):
                    pdf.xref_set_key(xref, key, value)
                removed += 1
    return removed


# --------------------------------------------------------------- sanificazione

SANITIZE_KEYS = ("metadata", "javascript", "embedded_files", "hidden_layers", "comments", "form_data",
                 "hidden_text", "links", "thumbnails")


def sanitize_labels() -> dict[str, str]:
    return {
        "metadata": _("Metadati (autore, titolo, programmi, date, XMP)"),
        "javascript": _("Script e azioni automatiche"),
        "embedded_files": _("File incorporati e allegati"),
        "hidden_layers": _("Livelli nascosti"),
        "comments": _("Commenti e annotazioni"),
        "form_data": _("Dati inseriti nei moduli"),
        "hidden_text": _("Testo nascosto (invisibile)"),
        "links": _("Collegamenti"),
        "thumbnails": _("Miniature incorporate"),
    }


def default_sanitize_options() -> dict[str, bool]:
    return {k: k != "links" for k in SANITIZE_KEYS}


@dataclass
class SanitizeReport:
    done: list[str] = field(default_factory=list)


def _remove_actions(pdf: pymupdf.Document) -> int:
    """Rimuove azioni JavaScript/Launch residue e le azioni automatiche (/AA, /OpenAction)."""
    n = 0
    cat = pdf.pdf_catalog()
    for key in ("OpenAction", "AA"):
        kind, val = pdf.xref_get_key(cat, key)
        if kind != "null":
            pdf.xref_set_key(cat, key, "null")
            n += 1
    kind, _v = pdf.xref_get_key(cat, "Names/JavaScript")
    if kind != "null":
        pdf.xref_set_key(cat, "Names/JavaScript", "null")
        n += 1
    for xref in range(1, pdf.xref_length()):
        try:
            obj = pdf.xref_object(xref, compressed=True)
        except Exception:
            continue
        if not obj:
            continue
        if "/AA" in obj and pdf.xref_get_key(xref, "AA")[0] != "null":
            pdf.xref_set_key(xref, "AA", "null")
            n += 1
        if _RX_JS.search(obj) or _RX_LAUNCH.search(obj) or _RX_SUBMIT.search(obj) or _RX_GOTOR.search(obj):
            if pdf.xref_get_key(xref, "S")[0] == "name":
                # oggetto azione: diventa un'azione vuota
                for key in ("JS", "F", "Win", "Mac", "Unix", "URL", "Fields", "D", "NewWindow"):
                    pdf.xref_set_key(xref, key, "null")
                pdf.xref_set_key(xref, "S", "/GoTo")
                pdf.xref_set_key(xref, "D", "[]")
                n += 1
            elif pdf.xref_get_key(xref, "A")[0] == "dict":
                pdf.xref_set_key(xref, "A", "null")
                n += 1
    return n


def sanitize(pdf: pymupdf.Document, options: dict[str, bool],
             progress: Callable[[float, str], None] | None = None) -> SanitizeReport:
    """Rimuove i dati scelti. Va chiamata dentro `document.change()`."""
    rep = SanitizeReport()
    o = {k: bool(options.get(k)) for k in SANITIZE_KEYS}

    def step(f: float, text: str) -> None:
        if progress is not None:
            progress(f, text)

    if o["hidden_layers"]:
        step(0.05, _("Livelli nascosti"))
        if remove_hidden_layers(pdf):
            rep.done.append("hidden_layers")
    step(0.3, _("Pulizia del documento"))
    pdf.scrub(
        attached_files=o["embedded_files"],
        clean_pages=True,
        embedded_files=o["embedded_files"],
        hidden_text=o["hidden_text"],
        javascript=o["javascript"],
        metadata=o["metadata"],
        redactions=False,
        remove_links=o["links"],
        reset_fields=o["form_data"],
        reset_responses=o["comments"],
        thumbnails=o["thumbnails"],
        xml_metadata=o["metadata"],
    )
    for key in ("embedded_files", "hidden_text", "metadata", "links", "thumbnails", "form_data"):
        if o[key]:
            rep.done.append(key)
    if o["javascript"]:
        step(0.6, _("Script e azioni"))
        _remove_actions(pdf)
        rep.done.append("javascript")
    if o["embedded_files"]:
        for name in list(pdf.embfile_names()):
            pdf.embfile_del(name)
    if o["comments"]:
        step(0.8, _("Commenti"))
        for page in pdf:
            xrefs = [a.xref for a in page.annots()
                     if a.type[0] not in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET,
                                          pymupdf.PDF_ANNOT_REDACT)]
            for xref in xrefs:
                annot = page.load_annot(xref)
                if annot is not None:
                    page.delete_annot(annot)
        rep.done.append("comments")
    if o["metadata"]:
        # data e programma non devono riapparire: metadati vuoti espliciti
        pdf.set_metadata({})
        try:
            pdf.del_xml_metadata()
        except Exception:
            pass
    if o["form_data"] and options.get("remove_fields"):
        for page in pdf:
            for xref in [w.xref for w in page.widgets()]:
                for w in page.widgets():
                    if w.xref == xref:
                        page.delete_widget(w)
                        break
        cat = pdf.pdf_catalog()
        if pdf.xref_get_key(cat, "AcroForm")[0] != "null":
            pdf.xref_set_key(cat, "AcroForm", "null")
    elif o["form_data"]:
        for page in pdf:
            for w in page.widgets():
                if w.field_type in (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX,
                                    pymupdf.PDF_WIDGET_TYPE_LISTBOX) and w.field_value:
                    w.field_value = ""
                    w.update()
                elif w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON) \
                        and w.field_value not in (False, "Off", ""):
                    w.field_value = False
                    w.update()
    step(1.0, "")
    return rep


# ------------------------------------------------------------------ metadati

METADATA_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")


def metadata_labels() -> dict[str, str]:
    return {
        "title": _("Titolo"),
        "author": _("Autore"),
        "subject": _("Oggetto"),
        "keywords": _("Parole chiave"),
        "creator": _("Applicazione di origine"),
        "producer": _("Produttore PDF"),
    }


def read_metadata(pdf: pymupdf.Document) -> dict[str, str]:
    meta = pdf.metadata or {}
    out = {k: meta.get(k) or "" for k in METADATA_KEYS}
    out["creationDate"] = meta.get("creationDate") or ""
    out["modDate"] = meta.get("modDate") or ""
    out["xmp"] = "1" if _xml_metadata(pdf) else ""
    return out


def write_metadata(pdf: pymupdf.Document, values: dict[str, str], *, update_mod_date: bool = True,
                   remove_xmp: bool = False) -> None:
    """Scrive i metadati (dentro `document.change()`). I campi assenti restano invariati."""
    current = pdf.metadata or {}
    new = {k: current.get(k) or "" for k in METADATA_KEYS + ("creationDate", "modDate")}
    for k in METADATA_KEYS:
        if k in values:
            new[k] = (values[k] or "").strip()
    if update_mod_date:
        new["modDate"] = pymupdf.get_pdf_now()
    pdf.set_metadata(new)
    if remove_xmp:
        try:
            pdf.del_xml_metadata()
        except Exception:
            pass


def format_pdf_date(value: str) -> str:
    """«D:20240312101500+01'00'» → «12/03/2024 10:15»."""
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value or "")
    if not m:
        return value or ""
    y, mo, d, h, mi = m.groups()
    s = f"{d or '01'}/{mo or '01'}/{y}"
    if h:
        s += f" {h}:{mi or '00'}"
    return s


# ------------------------------------------------------------- ispezione


def inspect_extra(pdf: pymupdf.Document, max_pages: int = 2000) -> dict:
    """Dettagli per l'ispezione (SEC-21) oltre a quelli di `scan()`."""
    out: dict = {"metadata": [], "layers": [], "fields": [], "hidden_text": 0, "hidden_where": [],
                 "ocr_text": 0, "hidden_annots": 0, "thumbnails": 0, "bookmarks": 0, "signatures": 0}
    meta = pdf.metadata or {}
    labels = metadata_labels()
    for k in METADATA_KEYS:
        if meta.get(k):
            out["metadata"].append(f"{labels[k]}: {meta[k]}")
    if _xml_metadata(pdf):
        out["metadata"].append(_("Metadati XMP presenti"))
    try:
        ocgs = pdf.get_ocgs() or {}
        out["layers"] = [info.get("name", "?") for xref, info in ocgs.items() if not info.get("on", True)]
    except Exception:
        pass
    try:
        out["bookmarks"] = len(pdf.get_toc())
    except Exception:
        pass
    for i, page in enumerate(pdf):
        if i >= max_pages:
            break
        try:
            kind, _v = pdf.xref_get_key(page.xref, "Thumb")
            if kind != "null":
                out["thumbnails"] += 1
        except Exception:
            pass
        for a in page.annots():
            if a.flags & (pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW):
                out["hidden_annots"] += 1
        for w in page.widgets():
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                out["signatures"] += 1
            if w.field_value not in (None, "", False, "Off"):
                out["fields"].append(f"{w.field_name}: {w.field_value}"[:120])
        try:
            traces = page.get_texttrace()
        except Exception:
            traces = []
        invisible = [t for t in traces if t.get("type") == 3 or t.get("opacity", 1) == 0]
        if invisible:
            if _covered_by_image(page):
                out["ocr_text"] += 1
            else:
                out["hidden_text"] += len(invisible)
                out["hidden_where"].append(_("Pagina {n}: {k} frammenti").format(n=i + 1, k=len(invisible)))
    return out


def _covered_by_image(page: pymupdf.Page) -> bool:
    area = abs(page.rect)
    if not area:
        return False
    for info in page.get_image_info():
        r = pymupdf.Rect(info.get("bbox", (0, 0, 0, 0)))
        if abs(r & page.rect) > 0.8 * area:
            return True
    return False


# ------------------------------------------------------------ appiattimento


def flatten(pdf: pymupdf.Document, annots: bool = True, widgets: bool = True) -> None:
    """Fonde commenti e campi nel contenuto delle pagine; quelli nascosti vengono eliminati."""
    for page in pdf:
        hidden = [a.xref for a in page.annots()
                  if a.flags & (pymupdf.PDF_ANNOT_IS_HIDDEN | pymupdf.PDF_ANNOT_IS_NO_VIEW)
                  or a.type[0] == pymupdf.PDF_ANNOT_REDACT]
        for xref in hidden:
            annot = page.load_annot(xref)
            if annot is not None and annot.type[0] != pymupdf.PDF_ANNOT_REDACT:
                page.delete_annot(annot)
    pdf.bake(annots=annots, widgets=widgets)
