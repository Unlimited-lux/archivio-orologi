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
    kind, val = _key(pdf, pdf.pdf_catalog(), "StructTreeRoot")
    if kind != "xref":
        return {}
    root = int(val.split()[0])
    order: dict[int, list[int]] = {}
    seen: set[int] = set()

    def add(page_xref: int | None, mcid: int) -> None:
        if page_xref is not None:
            lst = order.setdefault(page_xref, [])
            if mcid not in lst:
                lst.append(mcid)

    def visit_k(k_kind: str, k_val: str, page_xref: int | None, depth: int) -> None:
        if depth > 200:
            return
        if k_kind == "int":
            add(page_xref, int(k_val))
        elif k_kind == "xref":
            visit_elem(int(k_val.split()[0]), page_xref, depth + 1)
        elif k_kind == "array":
            # elementi dell'array: riferimenti, interi (MCID) o dizionari MCR
            for tok in re.findall(r"\d+\s+0\s+R|<<.*?>>|\d+", k_val, flags=re.S):
                if tok.endswith("R"):
                    visit_elem(int(tok.split()[0]), page_xref, depth + 1)
                elif tok.startswith("<<"):
                    m = re.search(r"/MCID\s+(\d+)", tok)
                    pg = re.search(r"/Pg\s+(\d+)\s+0\s+R", tok)
                    if m:
                        add(int(pg.group(1)) if pg else page_xref, int(m.group(1)))
                else:
                    add(page_xref, int(tok))
        elif k_kind == "dict":
            m = re.search(r"/MCID\s+(\d+)", k_val)
            pg = re.search(r"/Pg\s+(\d+)\s+0\s+R", k_val)
            if m:
                add(int(pg.group(1)) if pg else page_xref, int(m.group(1)))

    def visit_elem(xref: int, page_xref: int | None, depth: int) -> None:
        if xref in seen or depth > 200:
            return
        seen.add(xref)
        t = _key(pdf, xref, "Type")[1]
        if t == "/MCR":
            m = _key(pdf, xref, "MCID")
            pg = _key(pdf, xref, "Pg")
            if m[0] == "int":
                add(int(pg[1].split()[0]) if pg[0] == "xref" else page_xref, int(m[1]))
            return
        if t == "/OBJR":
            return
        pg = _key(pdf, xref, "Pg")
        if pg[0] == "xref":
            page_xref = int(pg[1].split()[0])
        k_kind, k_val = _key(pdf, xref, "K")
        visit_k(k_kind, k_val, page_xref, depth)

    visit_k(*_key(pdf, root, "K"), None, 0)
    return order


_TEXT_OPS = {b"Tj", b"TJ", b"'", b'"'}


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
        if tok == b"BDC":
            m = re.search(rb"/MCID\s+(\d+)", chunk)
            stack.append(int(m.group(1)) if m else (stack[-1] if stack else None))
            continue
        if tok == b"BMC":
            stack.append(stack[-1] if stack else None)
            continue
        if tok == b"EMC":
            if stack:
                stack.pop()
            continue
        if tok in _TEXT_OPS:
            current = next((x for x in reversed(stack) if x is not None), None)
            if current != mcid:
                if tok in (b"'", b'"'):
                    out += b"T*\n"      # conserva l'a capo implicito
                continue
        out += chunk + b"\n"
    return bytes(out)


def tagged_page_text(pdf: pymupdf.Document, index: int, mcids: list[int]) -> str | None:
    """Testo della pagina nell'ordine della struttura (None se non applicabile)."""
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
        parts = []
        for mcid in mcids:
            tmp.update_stream(xrefs[0], keep_mcid_text(content, mcid))
            text = page.get_text("text", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
            text = normalize_text(text).strip()
            if text:
                parts.append(text)
        # testo non marcato (fuori struttura, non artefatto): in coda
        tmp.update_stream(xrefs[0], keep_mcid_text(content, None))
        rest = normalize_text(page.get_text("text")).strip()
        if rest:
            parts.append(rest)
        return "\n".join(parts)
    except Exception:
        return None
    finally:
        tmp.close()
