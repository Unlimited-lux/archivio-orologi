"""Ordine di lettura dalla struttura logica dei PDF taggati (`/StructTreeRoot`), senza interfaccia.

Il motore non espone l'albero di struttura: si percorre l'albero per ottenere
la sequenza (pagina, MCID) e si estrae il testo di ogni contenuto marcato
filtrando gli operatori di testo del flusso di contenuto. Va chiamato tenendo
ENGINE_LOCK.
"""

from __future__ import annotations

import re

import pymupdf

from .flatten import _tokens

_REF = re.compile(r"(\d+)\s+0\s+R")
MAX_MCIDS_PER_PAGE = 400


def _key(pdf, xref: int, key: str) -> tuple[str, str]:
    try:
        return pdf.xref_get_key(xref, key)
    except Exception:
        return ("null", "null")


def structure_order(pdf: pymupdf.Document) -> dict[int, list[int]]:
    """{xref della pagina: [MCID nell'ordine della struttura]} ({} se il PDF non è taggato)."""
    return structure_layout(pdf)[0]


def structure_layout(pdf: pymupdf.Document) -> tuple[dict[int, list[int]], dict[int, dict[int, tuple]]]:
    """Come `structure_order`, più le celle di tabella: {xref pagina: {MCID: (xref TR, xref TD/TH)}}."""
    kind, val = _key(pdf, pdf.pdf_catalog(), "StructTreeRoot")
    if kind != "xref":
        return {}, {}
    root = int(val.split()[0])
    order: dict[int, list[int]] = {}
    cells: dict[int, dict[int, tuple]] = {}
    seen: set[int] = set()

    def add(page_xref: int | None, mcid: int, ctx: tuple) -> None:
        if page_xref is not None:
            lst = order.setdefault(page_xref, [])
            if mcid not in lst:
                lst.append(mcid)
                if ctx[0] is not None:
                    cells.setdefault(page_xref, {})[mcid] = ctx

    def visit_k(k_kind: str, k_val: str, page_xref: int | None, depth: int, ctx: tuple) -> None:
        if depth > 200:
            return
        if k_kind == "int":
            add(page_xref, int(k_val), ctx)
        elif k_kind == "xref":
            visit_elem(int(k_val.split()[0]), page_xref, depth + 1, ctx)
        elif k_kind == "array":
            # elementi dell'array: riferimenti, interi (MCID) o dizionari MCR
            for tok in re.findall(r"\d+\s+0\s+R|<<.*?>>|\d+", k_val, flags=re.S):
                if tok.endswith("R"):
                    visit_elem(int(tok.split()[0]), page_xref, depth + 1, ctx)
                elif tok.startswith("<<"):
                    m = re.search(r"/MCID\s+(\d+)", tok)
                    pg = re.search(r"/Pg\s+(\d+)\s+0\s+R", tok)
                    if m:
                        add(int(pg.group(1)) if pg else page_xref, int(m.group(1)), ctx)
                else:
                    add(page_xref, int(tok), ctx)
        elif k_kind == "dict":
            m = re.search(r"/MCID\s+(\d+)", k_val)
            pg = re.search(r"/Pg\s+(\d+)\s+0\s+R", k_val)
            if m:
                add(int(pg.group(1)) if pg else page_xref, int(m.group(1)), ctx)

    def visit_elem(xref: int, page_xref: int | None, depth: int, ctx: tuple) -> None:
        if xref in seen or depth > 200:
            return
        seen.add(xref)
        t = _key(pdf, xref, "Type")[1]
        if t == "/MCR":
            m = _key(pdf, xref, "MCID")
            pg = _key(pdf, xref, "Pg")
            if m[0] == "int":
                add(int(pg[1].split()[0]) if pg[0] == "xref" else page_xref, int(m[1]), ctx)
            return
        if t == "/OBJR":
            return
        role = _key(pdf, xref, "S")[1]
        if role == "/TR":
            ctx = (xref, None)
        elif role in ("/TD", "/TH") and ctx[0] is not None:
            ctx = (ctx[0], xref)
        pg = _key(pdf, xref, "Pg")
        if pg[0] == "xref":
            page_xref = int(pg[1].split()[0])
        k_kind, k_val = _key(pdf, xref, "K")
        visit_k(k_kind, k_val, page_xref, depth, ctx)

    visit_k(*_key(pdf, root, "K"), None, 0, (None, None))
    return order, cells


_TEXT_OPS = {b"Tj", b"TJ", b"'", b'"'}
ARTIFACT = -1


def keep_mcid_text(content: bytes, mcid: int | None) -> bytes:
    """Flusso in cui restano solo gli operatori di testo del contenuto marcato `mcid`."""
    out = bytearray()
    stack: list[int | None] = []
    pending_start: int | None = None
    for start, end, tok in _tokens(content):
        operand = tok[:1] in (b"/", b"(", b"<", b"[", b"]") or tok in (b"<<", b">>") or \
            re.fullmatch(rb"[-+]?(\d+\.?\d*|\.\d+)", tok) is not None or tok in (b"true", b"false", b"null")
        if tok.startswith(b"%"):
            continue
        if operand:
            if pending_start is None:
                pending_start = start
            continue
        chunk = content[pending_start if pending_start is not None else start:end]
        pending_start = None
        if tok in (b"BDC", b"BMC"):
            if re.match(rb"\s*/Artifact\b", chunk):
                stack.append(ARTIFACT)      # intestazioni, numeri di pagina…: non sono contenuto
                continue
            m = re.search(rb"/MCID\s+(\d+)", chunk) if tok == b"BDC" else None
            stack.append(int(m.group(1)) if m else (stack[-1] if stack else None))
            continue
        if tok == b"EMC":
            if stack:
                stack.pop()
            continue
        if tok in _TEXT_OPS:
            current = next((x for x in reversed(stack) if x is not None), None)
            if current != mcid or (mcid is None and ARTIFACT in stack):
                if tok in (b"'", b'"'):
                    out += b"T*\n"      # conserva l'a capo implicito
                continue
        out += chunk + b"\n"
    return bytes(out)


def tagged_page_text(pdf: pymupdf.Document, index: int, mcids: list[int],
                     cells: dict[int, tuple] | None = None) -> str | None:
    """Testo della pagina nell'ordine della struttura (None se non applicabile). Le celle di
    una stessa riga di tabella (`cells`: MCID → (riga, cella)) sono separate da tabulazioni;
    gli artefatti (intestazioni e numeri di pagina) sono esclusi."""
    from .extract import normalize_text
    if not mcids or len(mcids) > MAX_MCIDS_PER_PAGE:
        return None
    tmp = pymupdf.open()
    try:
        tmp.insert_pdf(pdf, from_page=index, to_page=index)
        page = tmp[0]
        content = page.read_contents()
        xrefs = page.get_contents()
        if not xrefs:
            return None
        for x in xrefs[1:]:
            tmp.update_stream(x, b"")
        cells = cells or {}
        out = ""
        prev = (None, None)
        for mcid in mcids:
            tmp.update_stream(xrefs[0], keep_mcid_text(content, mcid))
            text = page.get_text("text", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
            text = normalize_text(text).strip()
            if not text:
                continue
            row, cell = cells.get(mcid, (None, None))
            if out:
                if row is not None and row == prev[0]:
                    out += " " if cell == prev[1] else "\t"
                    text = " ".join(text.split())
                else:
                    out += "\n"
            elif row is not None:
                text = " ".join(text.split())
            out += text
            prev = (row, cell)
        # testo non marcato (fuori struttura, non artefatto): in coda
        tmp.update_stream(xrefs[0], keep_mcid_text(content, None))
        rest = normalize_text(page.get_text("text")).strip()
        if rest:
            out += ("\n" if out else "") + rest
        return out
    except Exception:
        return None
    finally:
        tmp.close()
