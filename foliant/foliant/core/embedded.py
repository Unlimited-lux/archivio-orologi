"""Allegati del documento (albero dei nomi /EmbeddedFiles): lettura e cancellazione sicure.

`Document.embfile_del` del motore (PyMuPDF 2.0) su alcuni alberi cancella la coppia nome/valore
sbagliata (usa l'indice dell'elemento come indice dell'array [nome, valore, …]): «Rimuovi» del
portfolio toglieva un altro file e il PDF/A-2 perdeva gli allegati PDF tenendo gli altri. Qui
l'albero si legge e si riscrive senza le voci tolte; le specifiche dei file restano le stesse
(date, tipo, /AFRelationship).
"""

from __future__ import annotations

from typing import Iterable

import pymupdf


def _s(text: str) -> str:
    return pymupdf.get_pdf_str(str(text))


def _pdf_string(raw: bytes) -> str:
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("latin-1")


def _array_items(text: str) -> list:
    """Elementi di un array PDF come lo restituisce `xref_get_key`: stringhe (decodificate),
    riferimenti («N 0 R») e dizionari diretti («<<…>>»)."""
    data = text.strip()
    if data.startswith("["):
        data = data[1:]
    if data.endswith("]"):
        data = data[:-1]
    out: list = []
    i, n = 0, len(data)
    esc = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "(": "(", ")": ")", "\\": "\\"}
    while i < n:
        c = data[i]
        if c.isspace():
            i += 1
        elif c == "(":
            depth, buf, i = 1, bytearray(), i + 1
            while i < n and depth:
                ch = data[i]
                if ch == "\\" and i + 1 < n:
                    nx = data[i + 1]
                    if nx in esc:
                        buf += esc[nx].encode("latin-1")
                        i += 2
                    elif nx.isdigit():
                        j = i + 1
                        while j < n and j < i + 4 and data[j] in "01234567":
                            j += 1
                        buf.append(int(data[i + 1:j], 8) & 0xFF)
                        i = j
                    else:
                        i += 2
                    continue
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if not depth:
                        i += 1
                        break
                buf += ch.encode("latin-1", "replace")
                i += 1
            out.append(("str", _pdf_string(bytes(buf))))
        elif data.startswith("<<", i):
            depth, j = 0, i
            while j < n:
                if data.startswith("<<", j):
                    depth += 1
                    j += 2
                elif data.startswith(">>", j):
                    depth -= 1
                    j += 2
                    if not depth:
                        break
                else:
                    j += 1
            out.append(("dict", data[i:j]))
            i = j
        elif c == "<":
            j = data.index(">", i)
            out.append(("str", _pdf_string(bytes.fromhex(data[i + 1:j].replace(" ", "")))))
            i = j + 1
        else:
            j = i
            while j < n and not data[j].isspace() and data[j] not in "([<":
                j += 1
            tok = data[i:j]
            parts = data[j:].split(None, 2)
            if tok.isdigit() and len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith("R"):
                out.append(("ref", int(tok)))
                k = data.index("R", j) + 1
                i = k
            else:
                out.append(("tok", tok))
                i = j
    return out


def embedded_entries(pdf: pymupdf.Document) -> list[tuple[str, int | str]]:
    """(chiave, specifica del file) dell'albero /EmbeddedFiles, anche con /Kids: la specifica è
    un xref oppure il testo del dizionario diretto (come lo scrive `embfile_add`)."""
    cat = pdf.pdf_catalog()
    kind, val = pdf.xref_get_key(cat, "Names/EmbeddedFiles")
    entries: list[tuple[str, int]] = []
    if kind == "null":
        return entries

    def walk(path_or_xref, depth=0):
        if depth > 32:
            return
        if isinstance(path_or_xref, int):
            names = pdf.xref_get_key(path_or_xref, "Names")
            kids = pdf.xref_get_key(path_or_xref, "Kids")
        else:
            names = pdf.xref_get_key(cat, path_or_xref + "/Names")
            kids = pdf.xref_get_key(cat, path_or_xref + "/Kids")
        if names[0] == "array":
            items = _array_items(names[1])
            for k in range(0, len(items) - 1, 2):
                if items[k][0] == "str" and items[k + 1][0] in ("ref", "dict"):
                    entries.append((items[k][1], items[k + 1][1]))
                elif items[k][0] == "str":
                    raise ValueError("albero degli allegati non supportato")
        if kids[0] == "array":
            for kind_k, x in _array_items(kids[1]):
                if kind_k == "ref":
                    walk(x, depth + 1)
    walk(int(val.split()[0]) if kind == "xref" else "Names/EmbeddedFiles")
    return entries


def write_tree(pdf: pymupdf.Document, entries: list[tuple[str, int | str]]) -> None:
    """Riscrive l'albero /EmbeddedFiles come un unico nodo ordinato (le specifiche restano le stesse)."""
    pairs = sorted(entries, key=lambda kv: kv[0].encode("utf-16-be"))
    tree = "[" + " ".join(f"{_s(k)} {x if isinstance(x, str) else f'{x} 0 R'}" for k, x in pairs) + "]"
    cat = pdf.pdf_catalog()
    node = pdf.get_new_xref()
    pdf.update_object(node, f"<</Names {tree}>>")
    if pdf.xref_get_key(cat, "Names")[0] == "null":
        pdf.xref_set_key(cat, "Names", f"<</EmbeddedFiles {node} 0 R>>")
    else:
        pdf.xref_set_key(cat, "Names/EmbeddedFiles", f"{node} 0 R")




def _af_without(pdf: pymupdf.Document, removed: set[int]) -> None:
    """Toglie da /AF del catalogo (PDF/A-3) i riferimenti alle specifiche cancellate."""
    cat = pdf.pdf_catalog()
    kind, val = pdf.xref_get_key(cat, "AF")
    if kind != "array" or not removed:
        return
    kept = [x for k, x in _array_items(val) if k == "ref" and x not in removed]
    pdf.xref_set_key(cat, "AF", "[" + " ".join(f"{x} 0 R" for x in kept) + "]" if kept else "null")


def delete_embedded(pdf: pymupdf.Document, names: str | Iterable[str]) -> None:
    """Toglie gli allegati `names` (chiavi dell'albero, come `embfile_names()`). KeyError se uno
    non c'è; RuntimeError se il risultato non è quello atteso."""
    drop = {names} if isinstance(names, str) else set(names)
    current = pdf.embfile_names()
    missing = drop - set(current)
    if missing:
        raise KeyError(sorted(missing)[0])
    expected = sorted(n for n in current if n not in drop)
    data_before = {n: pdf.embfile_get(n) for n in expected}
    try:
        entries = embedded_entries(pdf)
    except ValueError:
        entries = None
    if entries is not None:
        kept = [(k, x) for k, x in entries if k not in drop]
        removed = {x for k, x in entries if k in drop and isinstance(x, int)}
        if kept:
            write_tree(pdf, kept)
        else:
            pdf.xref_set_key(pdf.pdf_catalog(), "Names/EmbeddedFiles", "null")
        _af_without(pdf, removed)
    else:                                   # albero non leggibile: si ricostruisce con i soli dati
        keep = []
        for other in expected:
            info = pdf.embfile_info(other)
            keep.append((other, data_before[other], info.get("filename"), info.get("ufilename"),
                         info.get("description")))
        pdf.xref_set_key(pdf.pdf_catalog(), "Names/EmbeddedFiles", "null")
        for other, data, filename, ufilename, desc in keep:
            pdf.embfile_add(other, data, filename=filename, ufilename=ufilename, desc=desc)
    after = sorted(pdf.embfile_names())
    if after != expected or any(pdf.embfile_get(n) != data_before[n] for n in after):
        raise RuntimeError("Eliminazione dell'allegato non riuscita.")


def clear_embedded(pdf: pymupdf.Document) -> None:
    """Toglie tutti gli allegati del documento (non le annotazioni «file allegato» delle pagine)."""
    try:
        removed = {x for _k, x in embedded_entries(pdf) if isinstance(x, int)}
    except ValueError:
        removed = set()
    cat = pdf.pdf_catalog()
    if pdf.xref_get_key(cat, "Names")[0] != "null":
        pdf.xref_set_key(cat, "Names/EmbeddedFiles", "null")
    _af_without(pdf, removed)
