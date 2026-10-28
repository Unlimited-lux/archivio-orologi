"""Gestione dei livelli (VIS-21): logica pura, da chiamare dentro `Document.change()`.

- nuovo livello, rinomina, visibile all'apertura, blocco;
- unione di livelli: ogni riferimento all'OCG unito (risorse `/Properties`, chiavi `/OC` di
  XObject e annotazioni, OCMD) passa all'OCG di destinazione, che resta l'unico in
  `/OCProperties`;
- eliminazione e appiattimento: nei flussi di contenuto (pagine e XObject modulo) le sezioni
  `/OC /Nome BDC … EMC` dei livelli nascosti si tolgono, quelle dei livelli che restano visibili
  diventano contenuto di base (`/OC BMC … EMC`); XObject e annotazioni con `/OC` nascosto si
  svuotano o si tolgono. L'appiattimento rimuove `/OCProperties`.

Attenzione: MuPDF conserva in memoria la descrizione dei livelli letta all'apertura (anche i
nomi). Dopo queste funzioni il documento va ricreato dai byte (`Document.replace_with`), come fa
il pannello Livelli: altrimenti l'elenco resta vecchio e, dopo una rinomina, illeggibile.
"""

from __future__ import annotations

import re

import pymupdf

_REF = r"(\d+)\s+0\s+R"
_TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|<<|>>|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/<>\[\]()%{}]+|%[^\r\n]*|"
                    rb"[^\s/<>\[\]()%{}]+")


def ocgs(pdf: pymupdf.Document) -> dict[int, str]:
    """OCG del documento: xref -> nome."""
    return {x: str(info.get("name") or "") for x, info in (pdf.get_ocgs() or {}).items()}


def add_layer(pdf: pymupdf.Document, name: str, on: bool = True) -> int:
    return pdf.add_ocg(name, on=on)


def rename_layer(pdf: pymupdf.Document, xref: int, name: str) -> None:
    pdf.xref_set_key(xref, "Name", pymupdf.get_pdf_str(name))


def _default(pdf: pymupdf.Document) -> dict:
    return pdf.get_layer(-1) or {}


def _set_default(pdf: pymupdf.Document, cfg: dict) -> None:
    kwargs = {k: cfg[k] for k in ("on", "off", "locked", "rbgroups") if cfg.get(k)}
    kwargs.setdefault("on", [])
    kwargs.setdefault("off", [])
    pdf.set_layer(-1, **kwargs)


def set_default_visible(pdf: pymupdf.Document, xref: int, visible: bool) -> None:
    """Visibile o nascosto all'apertura (configurazione predefinita `/D`)."""
    cfg = _default(pdf)
    on = [x for x in cfg.get("on", []) if x != xref]
    off = [x for x in cfg.get("off", []) if x != xref]
    (on if visible else off).append(xref)
    cfg.update(on=on, off=off)
    _set_default(pdf, cfg)


def default_visible(pdf: pymupdf.Document, xref: int) -> bool:
    return xref not in (_default(pdf).get("off") or [])


def set_locked(pdf: pymupdf.Document, xref: int, locked: bool) -> None:
    cfg = _default(pdf)
    lst = [x for x in cfg.get("locked", []) if x != xref]
    if locked:
        lst.append(xref)
    cfg["locked"] = lst
    _set_default(pdf, cfg)


# ------------------------------------------------------------------ riferimenti
def _ocprops_holder(pdf: pymupdf.Document) -> tuple[int, str] | None:
    """(xref che contiene /OCProperties, chiave) per riscriverlo."""
    cat = pdf.pdf_catalog()
    kind, value = pdf.xref_get_key(cat, "OCProperties")
    if kind == "dict":
        return cat, "OCProperties"
    if kind == "xref":
        return int(value.split()[0]), ""
    return None


def _drop_from_ocprops(pdf: pymupdf.Document, xrefs: set[int]) -> None:
    holder = _ocprops_holder(pdf)
    if holder is None or not xrefs:
        return
    xref, key = holder
    text = pdf.xref_get_key(xref, key)[1] if key else pdf.xref_object(xref, compressed=True)
    for x in xrefs:
        text = re.sub(rf"(?<!\d){x}\s+0\s+R", " ", text)
    if key:
        pdf.xref_set_key(xref, key, text)
    else:
        pdf.update_object(xref, text)


def _replace_refs(pdf: pymupdf.Document, mapping: dict[int, int], skip: set[int]) -> None:
    """In tutti gli oggetti (esclusi `skip`) i riferimenti `src 0 R` diventano `dst 0 R`."""
    pattern = re.compile(r"(?<!\d)(" + "|".join(str(s) for s in mapping) + r")(\s+0\s+R)")
    for xref in range(1, pdf.xref_length()):
        if xref in skip:
            continue
        try:
            keys = pdf.xref_get_keys(xref)
        except Exception:
            continue
        for key in keys:
            try:
                kind, value = pdf.xref_get_key(xref, key)
            except Exception:
                continue
            if kind not in ("xref", "array", "dict") or not pattern.search(value):
                continue
            new = pattern.sub(lambda m: f"{mapping[int(m.group(1))]}{m.group(2)}", value)
            pdf.xref_set_key(xref, key, new)


def merge_layers(pdf: pymupdf.Document, sources: list[int], target: int) -> None:
    """Unisce i livelli `sources` in `target`: il contenuto marcato con quelli è marcato con
    `target` e gli OCG uniti non compaiono più in `/OCGs`."""
    sources = [s for s in sources if s != target]
    if not sources:
        return
    holder = _ocprops_holder(pdf)
    _drop_from_ocprops(pdf, set(sources))
    skip = set(sources) | ({holder[0]} if holder and not holder[1] else set())
    mapping = {s: target for s in sources}
    cat = pdf.pdf_catalog()
    _replace_refs(pdf, mapping, skip | {cat})
    # catalogo: tutto tranne /OCProperties (già sistemato)
    pattern = re.compile(r"(?<!\d)(" + "|".join(str(s) for s in mapping) + r")(\s+0\s+R)")
    for key in pdf.xref_get_keys(cat):
        if key == "OCProperties":
            continue
        kind, value = pdf.xref_get_key(cat, key)
        if kind in ("array", "dict") and pattern.search(value):
            pdf.xref_set_key(cat, key, pattern.sub(lambda m: f"{target}{m.group(2)}", value))


# ------------------------------------------------------------------ contenuti
def _visibility(pdf: pymupdf.Document, xref: int, visible: dict[int, bool]) -> bool | None:
    """Visibilità di un OCG o di un OCMD (politica /P, predefinita AnyOn); None = non OC."""
    if xref in visible:
        return visible[xref]
    try:
        obj = pdf.xref_object(xref, compressed=True)
    except Exception:
        return None
    if "/OCMD" not in obj:
        return None
    members = [int(m) for m in re.findall(_REF, re.search(r"/OCGs\s*(\[[^\]]*\]|\d+\s+0\s+R)", obj).group(1))] \
        if re.search(r"/OCGs", obj) else []
    states = [visible.get(m, True) for m in members]
    if not states:
        return True
    policy = (re.search(r"/P\s*/(\w+)", obj) or [None, "AnyOn"])[1]
    return {"AllOn": all(states), "AnyOff": not all(states), "AllOff": not any(states)}.get(policy, any(states))


def _properties(pdf: pymupdf.Document, holder: int) -> dict[str, int]:
    """Nome della risorsa /Properties -> xref (per una pagina o un XObject modulo)."""
    out = {}
    try:
        kind, res = pdf.xref_get_key(holder, "Resources")
    except Exception:
        return out
    if kind == "xref":
        res = pdf.xref_object(int(res.split()[0]), compressed=True)
    elif kind != "dict":
        return out
    m = re.search(r"/Properties\s*(" + _REF + r"|<<)", res)
    if not m:
        return out
    if m.group(1).startswith("<<"):
        start = m.start(1)
        depth, i = 0, start
        while i < len(res):
            if res.startswith("<<", i):
                depth += 1
                i += 2
            elif res.startswith(">>", i):
                depth -= 1
                i += 2
                if not depth:
                    break
            else:
                i += 1
        props = res[start:i]
    else:
        props = pdf.xref_object(int(m.group(2)), compressed=True)
    for name, x in re.findall(r"/([^\s/<>\[\]()]+)\s+" + _REF, props):
        out[name] = int(x)
    return out


def rewrite_content(content: bytes, props: dict[str, int], decide) -> bytes:
    """Riscrive le sezioni `/OC /Nome BDC … EMC`. `decide(xref)` restituisce «drop» (togli
    la sezione), «unwrap» (resta come contenuto di base) o None (invariata)."""
    out = bytearray()
    pos = 0
    operands: list[tuple[int, bytes]] = []
    stack: list[str] = []              # per ogni BDC/BMC aperto: "keep", "drop" o "unwrap"
    drop_depth = 0                     # >0: dentro una sezione da togliere
    data = content
    i = 0
    n = len(data)
    while i < n:
        m = _TOKEN.search(data, i)
        if not m:
            break
        tok, start, end = m.group(0), m.start(), m.end()
        i = end
        if tok.startswith(b"%"):
            continue
        if tok == b"ID":                 # immagine in linea: dati binari fino a EI
            k = data.find(b"EI", end)
            while k != -1 and not (k + 2 >= n or data[k + 2:k + 3].isspace()):
                k = data.find(b"EI", k + 2)
            i = n if k == -1 else k + 2
            operands = []
            continue
        if tok == b"<<":                 # dizionario operando: fino a >> bilanciato
            depth, j = 1, end
            while j < n and depth:
                mm = _TOKEN.search(data, j)
                if not mm:
                    j = n
                    break
                t = mm.group(0)
                depth += t == b"<<"
                depth -= t == b">>"
                j = mm.end()
            operands.append((start, data[start:j]))
            i = j
            continue
        if tok[:1] in (b"(", b"<", b"[", b"]", b"/") or re.fullmatch(rb"[-+.\d]+", tok):
            operands.append((start, tok))
            continue
        if tok in (b"BDC", b"BMC"):
            action = "keep"
            if tok == b"BDC" and len(operands) >= 2 and operands[-2][1] == b"/OC" and operands[-1][1][:1] == b"/":
                xref = props.get(operands[-1][1][1:].decode("latin-1"))
                if xref is not None:
                    action = decide(xref) or "keep"
            if drop_depth:
                drop_depth += 1
                stack.append("drop")
            elif action == "drop":
                begin = operands[-2][0]
                out += data[pos:begin]
                drop_depth = 1
                stack.append("drop")
            elif action == "unwrap":
                begin = operands[-2][0]
                out += data[pos:begin] + b"/OC BMC"
                pos = end
                stack.append("unwrap")
            else:
                stack.append("keep")
        elif tok == b"EMC":
            kind = stack.pop() if stack else "keep"
            if kind == "drop" and drop_depth:
                drop_depth -= 1
                if not drop_depth:
                    pos = end
                    out += b" "
        operands = []
    if drop_depth:                       # sezione non chiusa: tolta fino alla fine
        pos = n
    out += data[pos:]
    return bytes(out)


def _rewrite_holder(pdf: pymupdf.Document, holder: int, streams: list[int], decide, done: set[int]) -> None:
    props = _properties(pdf, holder)
    for sx in streams:
        if sx in done:
            continue
        done.add(sx)
        try:
            data = pdf.xref_stream(sx)
        except Exception:
            continue
        if data is None:
            continue
        new = rewrite_content(data, props, decide) if props else data
        if new != data:
            pdf.update_stream(sx, new)
    # XObject modulo nelle risorse
    try:
        kind, res = pdf.xref_get_key(holder, "Resources")
        if kind == "xref":
            res = pdf.xref_object(int(res.split()[0]), compressed=True)
        m = re.search(r"/XObject\s*(" + _REF + r"|<<[^>]*>>)", res or "")
        xobjs = []
        if m:
            text = pdf.xref_object(int(m.group(2)), compressed=True) if m.group(2) else m.group(1)
            xobjs = [int(x) for x in re.findall(_REF, text)]
    except Exception:
        xobjs = []
    for x in xobjs:
        if x in done:
            continue
        try:
            if pdf.xref_get_key(x, "Subtype")[1] != "/Form":
                continue
        except Exception:
            continue
        _rewrite_holder(pdf, x, [x], decide, done)


def _apply(pdf: pymupdf.Document, visible: dict[int, bool], targets: set[int], remove_hidden: bool = True) -> None:
    """Sezioni, XObject e annotazioni dei livelli `targets` (OCG): visibili → contenuto di base,
    nascosti → tolti (o resi visibili se `remove_hidden` è False)."""
    def decide(xref: int) -> str | None:
        members = {xref} if xref in targets else _ocmd_members(pdf, xref) & targets
        if not members:
            return None
        v = _visibility(pdf, xref, visible)
        if v is None:
            return None
        return "unwrap" if v or not remove_hidden else "drop"

    done: set[int] = set()
    for i in range(pdf.page_count):
        page = pdf[i]
        _rewrite_holder(pdf, page.xref, page.get_contents(), decide, done)
        for annot in list(page.annots() or []):
            kind, value = pdf.xref_get_key(annot.xref, "OC")
            if kind != "xref":
                continue
            what = decide(int(value.split()[0]))
            if what == "drop":
                page.delete_annot(annot)
            elif what == "unwrap":
                pdf.xref_set_key(annot.xref, "OC", "null")
    for x in range(1, pdf.xref_length()):         # XObject marcati con /OC
        try:
            kind, value = pdf.xref_get_key(x, "OC")
        except Exception:
            continue
        if kind != "xref" or not pdf.xref_is_stream(x):
            continue
        what = decide(int(value.split()[0]))
        if what == "drop":
            pdf.update_stream(x, b"")
            pdf.xref_set_key(x, "OC", "null")
        elif what == "unwrap":
            pdf.xref_set_key(x, "OC", "null")


def _ocmd_members(pdf: pymupdf.Document, xref: int) -> set[int]:
    try:
        obj = pdf.xref_object(xref, compressed=True)
    except Exception:
        return set()
    if "/OCMD" not in obj:
        return set()
    m = re.search(r"/OCGs\s*(\[[^\]]*\]|\d+\s+0\s+R)", obj)
    return {int(x) for x in re.findall(_REF, m.group(1))} if m else set()


def delete_layer(pdf: pymupdf.Document, xref: int, keep_content: bool) -> None:
    """Elimina un livello: il suo contenuto diventa contenuto di base (`keep_content`) o viene
    tolto; l'OCG sparisce da `/OCProperties`."""
    visible = {x: keep_content for x in ocgs(pdf)}
    _apply(pdf, visible, {xref})
    _drop_from_ocprops(pdf, {xref})


def flatten(pdf: pymupdf.Document, visible: dict[int, bool]) -> None:
    """Appiattisce tutti i livelli secondo la visibilità data (xref -> visibile): i visibili
    diventano contenuto di base, i nascosti sono rimossi; `/OCProperties` viene tolto."""
    all_ocgs = set(ocgs(pdf))
    vis = {x: visible.get(x, True) for x in all_ocgs}
    _apply(pdf, vis, all_ocgs)
    pdf.xref_set_key(pdf.pdf_catalog(), "OCProperties", "null")
