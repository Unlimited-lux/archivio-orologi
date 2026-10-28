"""Operazioni sulle pagine senza interfaccia (riusabili dagli altri moduli).

Le funzioni che ricevono `pdf` (un `pymupdf.Document`) lo modificano sul posto:
chiamarle dentro `with document.change(_("…")) as pdf:` così che l'operazione
sia un unico passo annullabile. Le funzioni che producono nuovi file
restituiscono `bytes` e non toccano il documento.

Gli indici di pagina sono sempre a base 0; le espressioni testuali scritte
dall'utente (PAG-02) usano numeri a partire da 1.

Le funzioni che toccano il motore assumono che il chiamante tenga
`ENGINE_LOCK` (lo tiene già `Document.change()`); quelle che aprono file
propri lo acquisiscono da sole.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

import pymupdf

from foliant.api import ENGINE_LOCK, DocumentError, FoliantError, PasswordRequired, WrongPassword, _

MM = 72.0 / 25.4
INCH = 72.0
MIN_SIDE = 3.0
MAX_SIDE = 14400.0
MAX_BLANK = 1000

# Formati standard (punti, verticale).
PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A0": (2383.94, 3370.39), "A1": (1683.78, 2383.94), "A2": (1190.55, 1683.78),
    "A3": (841.89, 1190.55), "A4": (595.28, 841.89), "A5": (419.53, 595.28), "A6": (297.64, 419.53),
    "B4": (708.66, 1000.63), "B5": (498.90, 708.66),
    "Letter": (612.0, 792.0), "Legal": (612.0, 1008.0), "Tabloid": (792.0, 1224.0),
    "Executive": (521.86, 756.0),
}


class PageOpError(FoliantError):
    """Operazione di pagina non eseguibile: il messaggio è per l'utente."""


class RangeError(PageOpError):
    """Espressione di intervallo non valida."""


# =========================================================================
# PAG-02 — intervalli di pagine
# =========================================================================

FILTER_ALL = "all"
FILTER_EVEN = "even"
FILTER_ODD = "odd"
FILTER_PORTRAIT = "portrait"
FILTER_LANDSCAPE = "landscape"
FILTERS = (FILTER_ALL, FILTER_ODD, FILTER_EVEN, FILTER_PORTRAIT, FILTER_LANDSCAPE)

_SPLIT = re.compile(r"[,;]")


def _resolve_token(token: str, page_count: int, labels: Sequence[str] | None) -> int:
    token = token.strip()
    if labels is not None:
        hits = [i for i, lab in enumerate(labels) if lab == token]
        if len(hits) > 1:
            raise RangeError(_("L'etichetta «{l}» è ambigua: corrisponde alle pagine {p}.").format(
                l=token, p=", ".join(str(h + 1) for h in hits)))
        if hits:
            return hits[0]
        if not token.isdigit():
            raise RangeError(_("L'etichetta «{l}» non esiste nel documento.").format(l=token))
    if not token.isdigit():
        raise RangeError(_("«{t}» non è un numero di pagina valido.").format(t=token))
    n = int(token)
    if n < 1 or n > page_count:
        raise RangeError(_("La pagina {n} non esiste: il documento ha {tot} pagine.").format(n=n, tot=page_count))
    return n - 1


def parse_ranges(text: str, page_count: int, *, labels: Sequence[str] | None = None,
                 ordered: bool = False) -> list[int]:
    """Risolve un'espressione come «1-3, 8-» in indici di pagina (base 0).

    Con `ordered=False` (operazioni insiemistiche: ruota, elimina…) restituisce
    l'insieme ordinato senza duplicati; con `ordered=True` (estrai, unisci)
    mantiene ordine e ripetizioni come scritti, intervalli decrescenti inclusi.
    Con `labels` i valori sono interpretati come etichette di pagina.
    """
    if text is None or not text.strip():
        raise RangeError(_("Indica almeno una pagina."))
    if page_count <= 0:
        raise RangeError(_("Il documento non ha pagine."))
    result: list[int] = []
    for part in _SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        if "-" in part and not (labels is not None and part in labels):
            a, _sep, b = part.partition("-")
            a, b = a.strip(), b.strip()
            if not a and not b:
                raise RangeError(_("Intervallo «{t}» non valido.").format(t=part))
            start = _resolve_token(a, page_count, labels) if a else 0
            end = _resolve_token(b, page_count, labels) if b else page_count - 1
            step = 1 if end >= start else -1
            result.extend(range(start, end + step, step))
        else:
            result.append(_resolve_token(part, page_count, labels))
    if not result:
        raise RangeError(_("Indica almeno una pagina."))
    if ordered:
        return result
    return sorted(set(result))


def format_ranges(indices: Iterable[int]) -> str:
    """Indici (base 0) → testo compatto «1-3, 7» (numeri da 1)."""
    nums = sorted({i + 1 for i in indices})
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def displayed_size(page: pymupdf.Page) -> tuple[float, float]:
    """Dimensione della pagina come visualizzata (rotazione inclusa)."""
    r = page.rect
    return r.width, r.height


def filter_pages(pdf: pymupdf.Document, indices: Iterable[int], flt: str = FILTER_ALL) -> list[int]:
    """Applica il filtro pari/dispari/verticali/orizzontali (numeri fisici da 1)."""
    out = []
    for i in indices:
        if flt == FILTER_ODD and (i + 1) % 2 == 0:
            continue
        if flt == FILTER_EVEN and (i + 1) % 2 == 1:
            continue
        if flt in (FILTER_PORTRAIT, FILTER_LANDSCAPE):
            w, h = displayed_size(pdf[i])
            if (flt == FILTER_PORTRAIT) != (h >= w):
                continue
        out.append(i)
    return out


def resolve_pages(pdf: pymupdf.Document, text: str | None, flt: str = FILTER_ALL, *,
                  use_labels: bool = False, ordered: bool = False) -> list[int]:
    """Espressione (None = tutte) + filtro → indici; errore se l'insieme è vuoto."""
    n = pdf.page_count
    if text is None:
        base = list(range(n))
    else:
        base = parse_ranges(text, n, labels=page_labels(pdf) if use_labels else None, ordered=ordered)
    out = filter_pages(pdf, base, flt)
    if not out:
        raise RangeError(_("Nessuna pagina corrisponde ai criteri indicati."))
    return out


def page_labels(pdf: pymupdf.Document) -> list[str]:
    """Etichette di pagina (numero fisico se il documento non ne definisce)."""
    out = []
    for i in range(pdf.page_count):
        try:
            lab = pdf[i].get_label()
        except Exception:
            lab = ""
        out.append(lab or str(i + 1))
    return out


# =========================================================================
# PAG-04/05 — riordino
# =========================================================================

def move_order(page_count: int, indices: Iterable[int], dest: int) -> list[int]:
    """Nuovo ordine spostando `indices` prima della pagina `dest` (indice
    nell'ordine attuale; `page_count` = in fondo). Ordine relativo mantenuto."""
    moving = sorted(set(i for i in indices if 0 <= i < page_count))
    if not 0 <= dest <= page_count:
        raise PageOpError(_("Posizione {p} non valida: scegli un valore tra 1 e {n}.").format(
            p=dest + 1, n=page_count + 1))
    rest = [i for i in range(page_count) if i not in set(moving)]
    pos = sum(1 for i in rest if i < dest)
    return rest[:pos] + moving + rest[pos:]


def reorder(pdf: pymupdf.Document, order: Sequence[int]) -> bool:
    """Applica una permutazione delle pagine. False se l'ordine non cambia.

    I riferimenti (segnalibri, collegamenti, campi) seguono l'oggetto pagina."""
    order = list(order)
    if sorted(order) != list(range(pdf.page_count)):
        raise PageOpError(_("Ordine delle pagine non valido."))
    if order == list(range(pdf.page_count)):
        return False
    pdf.select(order)
    return True


def move_pages(pdf: pymupdf.Document, indices: Iterable[int], dest: int) -> list[int]:
    """Sposta le pagine prima di `dest`; restituisce i nuovi indici delle pagine spostate."""
    moving = sorted(set(indices))
    order = move_order(pdf.page_count, moving, dest)
    reorder(pdf, order)
    return [order.index(i) for i in moving]


def reverse_order(page_count: int, indices: Iterable[int] | None = None) -> list[int]:
    """Inverte tutto il documento o solo le posizioni indicate."""
    order = list(range(page_count))
    sel = sorted(set(indices)) if indices is not None else order[:]
    rev = list(reversed(sel))
    for pos, src in zip(sel, rev):
        order[pos] = src
    return order


def swap_order(page_count: int, a: int, b: int) -> list[int]:
    order = list(range(page_count))
    order[a], order[b] = order[b], order[a]
    return order


def swap_pairs_order(page_count: int) -> list[int]:
    order = list(range(page_count))
    for i in range(0, page_count - 1, 2):
        order[i], order[i + 1] = order[i + 1], order[i]
    return order


def collate_order(page_count: int, split: int | None = None, backs_reversed: bool = True) -> tuple[list[int], bool]:
    """Fascicola fronti/retri. Restituisce (ordine, parti_di_lunghezza_diversa)."""
    if split is None:
        split = (page_count + 1) // 2
    split = max(0, min(page_count, split))
    fronts = list(range(split))
    backs = list(range(split, page_count))
    if backs_reversed:
        backs.reverse()
    order: list[int] = []
    for f, b in zip(fronts, backs):
        order += [f, b]
    k = min(len(fronts), len(backs))
    order += fronts[k:] + backs[k:]
    return order, len(fronts) != len(backs)


# =========================================================================
# PAG-06/07/08/09 — inserimento
# =========================================================================

def check_page_size(width: float, height: float) -> None:
    if width < MIN_SIDE or height < MIN_SIDE:
        raise PageOpError(_("Il formato è troppo piccolo: ogni lato deve misurare almeno 3 pt."))
    if width > MAX_SIDE or height > MAX_SIDE:
        raise PageOpError(_("Il formato è troppo grande: il massimo è 200 pollici (14.400 pt) per lato."))


def insert_blank_pages(pdf: pymupdf.Document, at: int, count: int = 1, size: tuple[float, float] | None = None,
                       landscape: bool | None = None, like: int | None = None) -> list[int]:
    """Inserisce `count` pagine vuote prima della posizione `at`.

    `size` (larghezza, altezza in punti, verticale) oppure `like` (indice della
    pagina di cui copiare formato e rotazione; predefinito: la pagina adiacente).
    Con `landscape` il formato standard diventa più largo che alto (senza /Rotate).
    Restituisce gli indici delle nuove pagine."""
    n = pdf.page_count
    if not 1 <= count <= MAX_BLANK:
        raise PageOpError(_("Il numero di pagine deve essere tra 1 e {m}.").format(m=MAX_BLANK))
    at = max(0, min(n, at))
    rotation = 0
    if size is None:
        ref = like if like is not None else (at - 1 if at > 0 else 0)
        if n == 0:
            w, h = PAPER_SIZES["A4"]
        else:
            ref = max(0, min(n - 1, ref))
            mb = pdf[ref].mediabox
            w, h = mb.width, mb.height
            rotation = pdf[ref].rotation
    else:
        w, h = size
        if landscape is not None:
            w, h = (max(w, h), min(w, h)) if landscape else (min(w, h), max(w, h))
    check_page_size(w, h)
    new = []
    for k in range(count):
        page = pdf.new_page(pno=at + k, width=w, height=h)
        if rotation:
            page.set_rotation(rotation)
        new.append(at + k)
    return new


def open_source(path: str | os.PathLike, password: str | None = None) -> pymupdf.Document:
    """Apre un file sorgente (PDF o formato convertibile) come documento PDF.

    Solleva PasswordRequired/WrongPassword per i file cifrati e DocumentError
    per file illeggibili o con assemblaggio negato."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise DocumentError(_("Impossibile leggere «{n}»: {e}.").format(n=path.name, e=e.strerror or e)) from e
    suffix = path.suffix.lower().lstrip(".")
    with ENGINE_LOCK:
        try:
            src = pymupdf.open(stream=data, filetype=suffix if suffix and suffix != "pdf" else "pdf")
        except Exception as e:
            raise DocumentError(_("«{n}» non è un PDF valido oppure è danneggiato.").format(n=path.name)) from e
        if src.needs_pass:
            if password is None:
                src.close()
                raise PasswordRequired(_("«{n}» è protetto da password.").format(n=path.name))
            if not src.authenticate(password):
                src.close()
                raise WrongPassword(_("Password errata per «{n}».").format(n=path.name))
        if not src.is_pdf:
            try:
                pdf_bytes = src.convert_to_pdf()
            finally:
                src.close()
            src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if src.page_count == 0:
            src.close()
            raise DocumentError(_("«{n}» non contiene pagine.").format(n=path.name))
        return src


def assembly_allowed(pdf: pymupdf.Document) -> bool:
    """True se i permessi consentono di assemblare (inserire, ruotare, eliminare) pagine."""
    try:
        perms = pdf.permissions
    except Exception:
        return True
    if not (pdf.metadata or {}).get("encryption"):
        return True
    return bool(perms & pymupdf.PDF_PERM_ASSEMBLE) or bool(perms & pymupdf.PDF_PERM_MODIFY)


def _subset(src: pymupdf.Document, indices: Sequence[int]) -> pymupdf.Document:
    """Copia di `src` con le sole pagine indicate (ripetizioni ammesse)."""
    tmp = pymupdf.open()
    # Pagine contigue in un'unica chiamata: le risorse condivise sono copiate una volta.
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and runs[-1][1] + 1 == i:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    if len(set(indices)) == len(indices):
        tmp.insert_pdf(src)
        tmp.select(list(indices))
        return tmp
    for a, b in runs:
        tmp.insert_pdf(src, from_page=a, to_page=b)
    return tmp


def _runs(indices: Sequence[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and runs[-1][1] + 1 == i:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def _insert_runs(dst: pymupdf.Document, src: pymupdf.Document, indices: Sequence[int], start_at: int,
                 annots: bool = True, widgets: bool = True) -> int:
    """Inserisce le pagine `indices` di `src` in `dst` a partire da `start_at`.

    Le sequenze contigue sono copiate con una sola chiamata (risorse condivise
    copiate una volta); campi omonimi vengono rinominati dal motore."""
    pos = start_at
    for a, b in _runs(indices):
        dst.insert_pdf(src, from_page=a, to_page=b, start_at=pos, annots=annots, widgets=widgets)
        pos += b - a + 1
    return pos - start_at


def insert_pdf_pages(pdf: pymupdf.Document, src: pymupdf.Document, at: int, src_indices: Sequence[int] | None = None,
                     *, interleave: int = 0, reverse: bool = False, annots: bool = True,
                     widgets: bool = True, bookmark_title: str | None = None) -> list[int]:
    """Inserisce pagine di `src` prima della posizione `at` (PAG-07).

    Con `interleave=K` le pagine sorgente sono intercalate ogni K pagine del
    documento a partire da `at` (eventualmente in ordine inverso); le pagine
    sorgente in eccesso sono accodate in fondo. Restituisce i nuovi indici."""
    if src_indices is None:
        src_indices = list(range(src.page_count))
    src_indices = list(src_indices)
    if reverse:
        src_indices.reverse()
    if not src_indices:
        raise PageOpError(_("Nessuna pagina da inserire."))
    same = src is pdf
    if same:
        src = pymupdf.open(stream=pdf.tobytes(), filetype="pdf")
    try:
        n = pdf.page_count
        at = max(0, min(n, at))
        if interleave <= 0:
            src_toc = src.get_toc(simple=True) if bookmark_title else []
            count = _insert_runs(pdf, src, src_indices, at, annots, widgets)
            if bookmark_title:
                _add_source_bookmarks(pdf, bookmark_title, src_toc, src_indices, at)
            return list(range(at, at + count))
        # Intercalato: prima inseriamo in blocco in fondo (risorse copiate una volta),
        # poi riordiniamo.
        base = n
        count = _insert_runs(pdf, src, src_indices, base, annots, widgets)
        order: list[int] = list(range(at))
        doc_rest = list(range(at, n))
        new = list(range(base, base + count))
        while doc_rest or new:
            take = doc_rest[:interleave]
            doc_rest = doc_rest[interleave:]
            order += take
            if new and (take or not doc_rest):
                order.append(new.pop(0))
            if not doc_rest:
                order += new
                new = []
        pdf.select(order)
        return [order.index(i) for i in range(base, base + count)]
    finally:
        if same:
            src.close()


def _add_source_bookmarks(pdf: pymupdf.Document, title: str, src_toc: list, src_indices: Sequence[int],
                          at: int) -> None:
    """Segnalibro con il nome del file sulle pagine inserite, con i segnalibri della sorgente annidati."""
    remap: dict[int, int] = {}
    for pos, i in enumerate(src_indices):
        remap.setdefault(i, at + pos)
    toc = pdf.get_toc(simple=True)
    new = [[1, title, at + 1]] + [[lvl + 1, t, remap[p - 1] + 1] for lvl, t, p in src_toc if p - 1 in remap]
    # inserito prima del primo segnalibro di primo livello che punta oltre il punto di inserimento
    pos = len(toc)
    for k, (lvl, _t, p) in enumerate(toc):
        if lvl == 1 and p - 1 > at:
            pos = k
            break
    _set_toc_safe(pdf, toc[:pos] + new + toc[pos:])


def merge_alternate(path_a: str, path_b: str, *, reverse_b: bool = False, password_a: str | None = None,
                    password_b: str | None = None) -> bytes:
    """Unione alternata di due file: A1, B1, A2, B2… (B eventualmente in ordine inverso);
    le pagine eccedenti del file più lungo sono accodate."""
    a = open_source(path_a, password_a)
    b = open_source(path_b, password_b)
    try:
        with ENGINE_LOCK:
            out = pymupdf.open()
            out.insert_pdf(a)
            base = out.page_count
            out.insert_pdf(b)
            bi = list(range(base, out.page_count))
            if reverse_b:
                bi.reverse()
            ai = list(range(base))
            order: list[int] = []
            for x, y in zip(ai, bi):
                order += [x, y]
            k = min(len(ai), len(bi))
            order += ai[k:] + bi[k:]
            out.select(order)
            data = out.tobytes(garbage=3, deflate=True)
            out.close()
            return data
    finally:
        a.close()
        b.close()


def delete_pages(pdf: pymupdf.Document, indices: Iterable[int]) -> int:
    """Elimina le pagine (PAG-11). Deve restarne almeno una.

    Segnalibri verso pagine eliminate restano senza destinazione; collegamenti
    verso di esse vengono rimossi (comportamento del motore)."""
    sel = sorted(set(i for i in indices if 0 <= i < pdf.page_count))
    if not sel:
        raise PageOpError(_("Nessuna pagina selezionata."))
    if len(sel) >= pdf.page_count:
        raise PageOpError(_("Impossibile eliminare tutte le pagine: il documento deve conservarne almeno una."))
    pdf.delete_pages(sel)
    _fix_open_action(pdf)
    prune_structure(pdf)
    return len(sel)


# ---------------------------------------------------------------- struttura logica (PAG-03)

def _split_array(text: str) -> list[str]:
    """Elementi di un array PDF: riferimenti «n 0 R», numeri e dizionari «<<…>>»."""
    text = text.strip()
    if text.startswith("["):
        text = text[1:-1]
    items: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if text.startswith("<<", i):
            depth, j = 0, i
            while j < n:
                if text.startswith("<<", j):
                    depth += 1
                    j += 2
                elif text.startswith(">>", j):
                    depth -= 1
                    j += 2
                    if depth == 0:
                        break
                else:
                    j += 1
            items.append(text[i:j])
            i = j
            continue
        m = re.match(r"(\d+)\s+(\d+)\s+R", text[i:])
        if m:
            items.append(m.group(0))
            i += m.end()
            continue
        m = re.match(r"[^\s\[\]<>/]+|/[^\s\[\]<>/]+", text[i:])
        if m:
            items.append(m.group(0))
            i += m.end()
        else:
            i += 1
    return items


def _ref(text: str) -> int | None:
    m = re.fullmatch(r"\s*(\d+)\s+\d+\s+R\s*", text)
    return int(m.group(1)) if m else None


def _dict_pg(text: str) -> int | None:
    m = re.search(r"/Pg\s+(\d+)\s+\d+\s+R", text)
    return int(m.group(1)) if m else None


def prune_structure(pdf: pymupdf.Document) -> int:
    """Rimuove dall'albero di struttura i contenuti delle pagine che non esistono più.

    Elementi rimasti senza contenuto vengono potati; il /ParentTree conserva solo
    le voci delle pagine e annotazioni ancora presenti. Restituisce il numero di
    elementi rimossi."""
    try:
        kind, root = pdf.xref_get_key(pdf.pdf_catalog(), "StructTreeRoot")
    except Exception:
        return 0
    if kind != "xref":
        return 0
    root_x = int(root.split()[0])
    live = {pdf[i].xref for i in range(pdf.page_count)}
    removed = 0
    seen: set[int] = set()

    def prune_kids(owner: int, inherited_pg: int | None) -> bool:
        """Pota /K di `owner`; True se resta qualcosa."""
        nonlocal removed
        k_kind, k_val = pdf.xref_get_key(owner, "K")
        if k_kind == "null":
            return owner == root_x
        items = _split_array(k_val) if k_kind == "array" else [k_val]
        p_kind, p_val = pdf.xref_get_key(owner, "Pg")
        pg = int(p_val.split()[0]) if p_kind == "xref" else inherited_pg
        kept: list[str] = []
        for it in items:
            x = _ref(it)
            if x is not None:
                if x in seen:
                    kept.append(it)
                    continue
                seen.add(x)
                t_kind, t_val = pdf.xref_get_key(x, "Type")
                if t_val in ("/MCR", "/OBJR"):
                    kpg = _dict_pg(pdf.xref_object(x, compressed=True)) or pg
                    if kpg is None or kpg in live:
                        kept.append(it)
                    continue
                if prune_kids(x, pg):
                    kept.append(it)
                else:
                    removed += 1
                continue
            if it.startswith("<<"):
                kpg = _dict_pg(it) or pg
                if kpg is None or kpg in live:
                    kept.append(it)
                continue
            if re.fullmatch(r"-?\d+", it):   # MCID sulla pagina dell'elemento
                if pg is None or pg in live:
                    kept.append(it)
                continue
            kept.append(it)
        if len(kept) != len(items):
            if not kept:
                pdf.xref_set_key(owner, "K", "null")
            else:
                pdf.xref_set_key(owner, "K", "[" + " ".join(kept) + "]")
        if owner != root_x and p_kind == "xref" and pg not in live:
            pdf.xref_set_key(owner, "Pg", "null")
        return bool(kept) or owner == root_x

    prune_kids(root_x, None)
    # ParentTree: solo le chiavi ancora usate da pagine e annotazioni
    used: set[int] = set()
    for i in range(pdf.page_count):
        page = pdf[i]
        k, v = pdf.xref_get_key(page.xref, "StructParents")
        if k == "int":
            used.add(int(v))
        for a in page.annots() or []:
            k, v = pdf.xref_get_key(a.xref, "StructParent")
            if k == "int":
                used.add(int(v))
        for w in page.widgets() or []:
            k, v = pdf.xref_get_key(w.xref, "StructParent")
            if k == "int":
                used.add(int(v))
    k, v = pdf.xref_get_key(root_x, "ParentTree")
    nodes = [int(v.split()[0])] if k == "xref" else []
    while nodes:
        node = nodes.pop()
        nk, nv = pdf.xref_get_key(node, "Kids")
        if nk == "array":
            nodes += [r for r in (_ref(t) for t in _split_array(nv)) if r]
        nk, nv = pdf.xref_get_key(node, "Nums")
        if nk == "array":
            items = _split_array(nv)
            kept = []
            for j in range(0, len(items) - 1, 2):
                if re.fullmatch(r"\d+", items[j]) and int(items[j]) in used:
                    kept += [items[j], items[j + 1]]
            if len(kept) != len(items):
                pdf.xref_set_key(node, "Nums", "[" + " ".join(kept) + "]")
    return removed


def _fix_open_action(pdf: pymupdf.Document) -> None:
    try:
        kind, value = pdf.xref_get_key(pdf.pdf_catalog(), "OpenAction")
    except Exception:
        return
    if kind == "array":
        m = re.match(r"\[\s*(\d+)\s+0\s+R", value)
        if m:
            xref = int(m.group(1))
            if xref not in {pdf[i].xref for i in range(pdf.page_count)}:
                pdf.xref_set_key(pdf.pdf_catalog(), "OpenAction", f"[{pdf[0].xref} 0 R /Fit]")


def linked_items(pdf: pymupdf.Document, indices: Iterable[int]) -> dict[str, int]:
    """Riepilogo degli elementi collegati alle pagine indicate (per la conferma)."""
    sel = set(indices)
    toc = pdf.get_toc(simple=True)
    bookmarks = sum(1 for _lvl, _t, p in toc if p - 1 in sel)
    links = 0
    for i in range(pdf.page_count):
        for link in pdf[i].get_links():
            if link.get("kind") == pymupdf.LINK_GOTO and link.get("page") in sel and i not in sel:
                links += 1
    fields = sum(len(list(pdf[i].widgets())) for i in sel)
    return {"bookmarks": bookmarks, "links": links, "fields": fields}


def _widget_names(pdf: pymupdf.Document) -> set[str]:
    names = set()
    for i in range(pdf.page_count):
        for w in pdf[i].widgets():
            if w.field_name:
                names.add(w.field_name)
    return names


def _unique_name(name: str, used: set[str]) -> str:
    k = 2
    while f"{name}_{k}" in used:
        k += 1
    return f"{name}_{k}"


def _fullcopy(pdf: pymupdf.Document, src: int, before: int) -> None:
    pdf.fullcopy_page(src, -1 if before >= pdf.page_count else before)


def duplicate_pages(pdf: pymupdf.Document, indices: Iterable[int], copies: int = 1, where: str = "after",
                    keep_field_names: bool = False) -> list[int]:
    """Duplica le pagine (PAG-15). `where`: "after" (dopo ciascuna pagina),
    "block" (dopo l'ultima selezionata), "end" (in fondo).

    Le copie riusano le stesse risorse (immagini e font non sono duplicati).
    I campi copiati sono rinominati con un suffisso (valori indipendenti) e i
    campi firma non vengono duplicati. Restituisce gli indici delle copie."""
    sel = sorted(set(i for i in indices if 0 <= i < pdf.page_count))
    if not sel:
        raise PageOpError(_("Nessuna pagina selezionata."))
    if copies < 1:
        raise PageOpError(_("Il numero di copie deve essere almeno 1."))
    used = _widget_names(pdf)
    new_positions: list[int] = []
    if where == "after":
        offset = 0
        for i in sel:
            src = i + offset
            for k in range(copies):
                _fullcopy(pdf, src, src + 1 + k)
                new_positions.append(src + 1 + k)
            offset += copies
    else:
        target = (sel[-1] + 1) if where == "block" else pdf.page_count
        pos = target
        for _k in range(copies):
            for i in sel:
                src = i if i < target else i + (pos - target)
                _fullcopy(pdf, src, pos)
                new_positions.append(pos)
                pos += 1
    for p in new_positions:
        page = pdf[p]
        for w in list(page.widgets()):
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
                page.delete_widget(w)
                continue
            _adopt_copied_widget(pdf, page.xref, w.xref, w.field_name or "", used, keep_field_names)
    return new_positions


def _adopt_copied_widget(pdf: pymupdf.Document, page_xref: int, wx: int, name: str, used: set[str],
                         keep_name: bool) -> None:
    """Rende campo a sé (o nuovo figlio dello stesso campo) un widget copiato con la pagina."""
    pdf.xref_set_key(wx, "P", f"{page_xref} 0 R")
    pdf.xref_set_key(wx, "NM", pymupdf.get_pdf_str(f"foliant-{wx}"))
    kind, parent = pdf.xref_get_key(wx, "Parent")
    cat = pdf.pdf_catalog()
    if kind == "xref":
        px = int(parent.split()[0])
        if keep_name:
            k2, kids = pdf.xref_get_key(px, "Kids")
            kids = kids.strip("[]") if k2 == "array" else ""
            pdf.xref_set_key(px, "Kids", f"[{kids} {wx} 0 R]")
            return
        new_name = _unique_name(name.split(".")[-1] or "Campo", used)
        used.add(new_name)
        nx = pdf.get_new_xref()
        pdf.update_object(nx, pdf.xref_object(px, compressed=True))
        pdf.xref_set_key(nx, "Kids", f"[{wx} 0 R]")
        pdf.xref_set_key(nx, "T", pymupdf.get_pdf_str(new_name))
        pdf.xref_set_key(wx, "Parent", f"{nx} 0 R")
        k3, gparent = pdf.xref_get_key(px, "Parent")
        if k3 == "xref":
            gx = int(gparent.split()[0])
            k4, kids = pdf.xref_get_key(gx, "Kids")
            kids = kids.strip("[]") if k4 == "array" else ""
            pdf.xref_set_key(gx, "Kids", f"[{kids} {nx} 0 R]")
            return
        field_ref = nx
    else:
        if keep_name:
            return
        new_name = _unique_name(name or "Campo", used)
        used.add(new_name)
        pdf.xref_set_key(wx, "T", pymupdf.get_pdf_str(new_name))
        field_ref = wx
    k5, form = pdf.xref_get_key(cat, "AcroForm")
    form_xref = int(form.split()[0]) if k5 == "xref" else None
    if form_xref:
        k6, fields = pdf.xref_get_key(form_xref, "Fields")
        fields = fields.strip("[]") if k6 == "array" else ""
        pdf.xref_set_key(form_xref, "Fields", f"[{fields} {field_ref} 0 R]")
    else:
        k6, fields = pdf.xref_get_key(cat, "AcroForm/Fields")
        fields = fields.strip("[]") if k6 == "array" else ""
        pdf.xref_set_key(cat, "AcroForm/Fields", f"[{fields} {field_ref} 0 R]")


def rotate_pages(pdf: pymupdf.Document, indices: Iterable[int], degrees: int) -> list[int]:
    """Ruota in modo persistente (PAG-18): /Rotate scritto sulla singola pagina,
    normalizzato a 0/90/180/270. Restituisce le pagine ruotate."""
    if degrees % 90:
        raise PageOpError(_("La rotazione deve essere un multiplo di 90°."))
    done = []
    for i in sorted(set(indices)):
        page = pdf[i]
        current = page.rotation
        if current % 90:
            current = 0
        page.set_rotation((current + degrees) % 360)
        try:
            pdf.xref_set_key(page.xref, "Thumb", "null")
        except Exception:
            pass
        done.append(i)
    return done


def extract_pages(pdf: pymupdf.Document, indices: Sequence[int], *, annots: bool = True) -> bytes:
    """Nuovo PDF con le pagine indicate nell'ordine dato (PAG-12).

    Restano solo i segnalibri verso pagine estratte; i collegamenti verso pagine
    non estratte sono rimossi; la protezione della sorgente non è ereditata."""
    indices = list(indices)
    if not indices:
        raise PageOpError(_("Nessuna pagina da estrarre."))
    for i in indices:
        if not 0 <= i < pdf.page_count:
            raise PageOpError(_("La pagina {n} non esiste.").format(n=i + 1))
    copy = pymupdf.open(stream=pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
    try:
        if len(set(indices)) == len(indices):
            copy.select(indices)
            out = copy
        else:
            out = _subset(copy, indices)
        prune_structure(out)
        if not annots:
            for page in out:
                for a in list(page.annots() or []):
                    page.delete_annot(a)
        meta = dict(pdf.metadata or {})
        meta.pop("encryption", None)
        meta.pop("format", None)
        try:
            out.set_metadata({k: v for k, v in meta.items() if v})
        except Exception:
            pass
        data = out.tobytes(garbage=3, deflate=True)
        if out is not copy:
            out.close()
        return data
    finally:
        copy.close()


def replace_pages(pdf: pymupdf.Document, indices: Sequence[int], src: pymupdf.Document,
                  src_indices: Sequence[int], keep_annots: str = "original") -> list[int]:
    """Sostituisce le pagine `indices` (contigue o no, in ordine) con `src_indices` (PAG-19).

    Le prime min(n, m) pagine sono sostituite una a una: il nuovo contenuto
    prende il posto dell'oggetto pagina originale, quindi segnalibri e
    collegamenti che puntavano alla pagina restano validi. `keep_annots`:
    "original", "source" o "both". Restituisce gli indici delle nuove pagine."""
    indices = sorted(set(indices))
    src_indices = list(src_indices)
    if not indices or not src_indices:
        raise PageOpError(_("Indica le pagine da sostituire e quelle sorgente."))
    same = src is pdf
    if same:
        src = pymupdf.open(stream=pdf.tobytes(), filetype="pdf")
    try:
        # Copie temporanee in fondo, poi travaso di contenuto e riquadri nell'oggetto originale.
        base = pdf.page_count
        count = _insert_runs(pdf, src, src_indices, base, keep_annots in ("source", "both"), False)
        k = min(len(indices), count)
        result = list(indices[:k])
        for j in range(k):
            _transplant(pdf, base + j, indices[j], keep_annots)
        extra_src = list(range(base + k, base + count))
        to_delete = list(range(base, base + k))
        if len(indices) > k:
            to_delete += indices[k:]
        if extra_src:
            # Pagine sorgente in più: subito dopo l'ultima sostituita.
            after = indices[k - 1] + 1
            order = [i for i in range(base) if i < after] + extra_src + \
                    [i for i in range(base) if i >= after] + list(range(base, base + k))
            pdf.select(order)
            n_extra = len(extra_src)
            result += list(range(after, after + n_extra))
            pdf.delete_pages(list(range(pdf.page_count - k, pdf.page_count)))
            prune_structure(pdf)
        else:
            pdf.delete_pages(sorted(set(to_delete)))
            removed_before = 0
            fixed = []
            gone = set(indices[k:])
            for i in result:
                removed_before = sum(1 for g in gone if g < i)
                fixed.append(i - removed_before)
            result = fixed
        prune_structure(pdf)
        return result
    finally:
        if same:
            src.close()


def _transplant(pdf: pymupdf.Document, from_pno: int, to_pno: int, keep_annots: str) -> None:
    """Porta contenuto, risorse e riquadri della pagina `from_pno` nell'oggetto di `to_pno`."""
    src_page = pdf[from_pno]
    dst_page = pdf[to_pno]
    sx, dx = src_page.xref, dst_page.xref
    old_rect = dst_page.rect
    # Annotazioni originali da conservare (non i widget dei campi: rimossi).
    kept: list[str] = []
    for key in ("Contents", "Resources", "MediaBox", "CropBox", "Rotate", "BleedBox", "TrimBox", "ArtBox"):
        kind, value = pdf.xref_get_key(sx, key)
        if kind == "null":
            pdf.xref_set_key(dx, key, "null")
        else:
            pdf.xref_set_key(dx, key, value)
    dst_annots: list[str] = []
    kind, value = pdf.xref_get_key(dx, "Annots")
    if keep_annots in ("original", "both"):
        page = pdf[to_pno]
        for a in page.annots() or []:
            kept.append(f"{a.xref} 0 R")
    dst_annots = kept
    if keep_annots in ("source", "both"):
        k2, v2 = pdf.xref_get_key(sx, "Annots")
        if k2 == "array":
            for m in re.finditer(r"(\d+)\s+0\s+R", v2):
                ax = int(m.group(1))
                pdf.xref_set_key(ax, "P", f"{dx} 0 R")
                dst_annots.append(f"{ax} 0 R")
    pdf.xref_set_key(dx, "Annots", "[" + " ".join(dst_annots) + "]" if dst_annots else "null")
    pdf.xref_set_key(sx, "Annots", "null")
    new_rect = pdf[to_pno].rect
    if keep_annots in ("original", "both") and kept and (abs(new_rect.width - old_rect.width) > 0.5
                                                          or abs(new_rect.height - old_rect.height) > 0.5):
        sxf = new_rect.width / old_rect.width
        syf = new_rect.height / old_rect.height
        page = pdf[to_pno]
        for a in page.annots() or []:
            r = a.rect
            try:
                a.set_rect(pymupdf.Rect(r.x0 * sxf, r.y0 * syf, r.x1 * sxf, r.y1 * syf))
            except Exception:
                pass


# =========================================================================
# PAG-12/20/21/22/23 — nomi file, estrazione e divisione
# =========================================================================

_BAD_CHARS = re.compile(r'[/\\:*?"<>|\x00-\x1f]')


def safe_filename(name: str, max_bytes: int = 200) -> str:
    name = _BAD_CHARS.sub("_", name).strip().strip(".") or "documento"
    while len(name.encode("utf-8")) > max_bytes:
        name = name[:-1]
    return name


def render_name(template: str, **fields) -> str:
    """Compone il nome file dal modello: {nome}, {pagina}, {n}, {inizio}, {fine},
    {segnalibro}, {match1}…; segnaposto sconosciuti restano vuoti."""
    def sub(m: re.Match) -> str:
        return str(fields.get(m.group(1), ""))
    return safe_filename(re.sub(r"\{(\w+)\}", sub, template))


def unique_path(folder: str | os.PathLike, filename: str, overwrite: bool = False,
                taken: set[str] | None = None) -> Path:
    """Percorso libero in `folder`: aggiunge « (2)», « (3)»… se il nome esiste."""
    folder = Path(folder)
    base, ext = os.path.splitext(filename)
    ext = ext or ".pdf"
    cand = folder / f"{base}{ext}"
    k = 2
    taken = taken if taken is not None else set()
    while (str(cand) in taken) or (not overwrite and cand.exists()):
        cand = folder / f"{base} ({k}){ext}"
        k += 1
    taken.add(str(cand))
    return cand


@dataclass
class SplitPart:
    indices: list[int]
    fields: dict = field(default_factory=dict)   # valori per il modello del nome
    oversize: bool = False


@dataclass
class SplitPlan:
    parts: list[SplitPart]
    warnings: list[str] = field(default_factory=list)
    uncovered: list[int] = field(default_factory=list)


def plan_every(page_count: int, n: int) -> SplitPlan:
    if n < 1:
        raise PageOpError(_("Il numero di pagine per file deve essere almeno 1."))
    parts = [SplitPart(list(range(i, min(i + n, page_count)))) for i in range(0, page_count, n)]
    warnings = [_("Il documento ha al massimo {n} pagine: si otterrà un solo file.").format(n=n)] if n >= page_count else []
    return SplitPlan(parts, warnings)


def plan_parts(page_count: int, k: int) -> SplitPlan:
    if k < 1:
        raise PageOpError(_("Il numero di parti deve essere almeno 1."))
    if k > page_count:
        raise PageOpError(_("Le parti non possono essere più delle pagine ({n}).").format(n=page_count))
    size, extra = divmod(page_count, k)
    parts, start = [], 0
    for j in range(k):
        length = size + (1 if j < extra else 0)
        parts.append(SplitPart(list(range(start, start + length))))
        start += length
    return SplitPlan(parts)


def plan_ranges(page_count: int, text: str, collect_rest: bool = False) -> SplitPlan:
    """Un file per intervallo («1-3; 4-10; 11-»)."""
    groups = [g for g in re.split(r";", text) if g.strip()]
    if len(groups) == 1 and "," in text:
        groups = [g for g in text.split(",") if g.strip()]
    if not groups:
        raise RangeError(_("Indica almeno un intervallo."))
    parts = [SplitPart(parse_ranges(g, page_count, ordered=True)) for g in groups]
    covered = [i for p in parts for i in p.indices]
    warnings = []
    if len(covered) != len(set(covered)):
        warnings.append(_("Alcune pagine compaiono in più file."))
    uncovered = [i for i in range(page_count) if i not in set(covered)]
    if uncovered:
        warnings.append(_("Le pagine {p} non sono incluse in nessun file.").format(p=format_ranges(uncovered)))
        if collect_rest:
            parts.append(SplitPart(uncovered))
    return SplitPlan(parts, warnings, uncovered)


def _bookmark_starts(pdf: pymupdf.Document, level: int) -> list[tuple[str, int]]:
    starts = []
    for lvl, title, page in pdf.get_toc(simple=True):
        if lvl <= level and page >= 1:
            starts.append((title, page - 1))
    return sorted(starts, key=lambda t: t[1])


def plan_bookmarks(pdf: pymupdf.Document, level: int = 1, merge_leading: bool = False) -> SplitPlan:
    """Un file per segnalibro fino al livello indicato (PAG-21)."""
    n = pdf.page_count
    toc = pdf.get_toc(simple=True)
    if not toc:
        raise PageOpError(_("Il documento non ha segnalibri."))
    warnings = []
    if any(lvl <= level and page < 1 for lvl, _t, page in toc):
        warnings.append(_("Alcuni segnalibri senza destinazione di pagina sono stati ignorati."))
    starts = _bookmark_starts(pdf, level)
    if not starts:
        raise PageOpError(_("Nessun segnalibro con destinazione di pagina."))
    parts: list[SplitPart] = []
    if starts[0][1] > 0:
        if merge_leading:
            starts[0] = (starts[0][0], 0)
        else:
            parts.append(SplitPart(list(range(0, starts[0][1])), {"segnalibro": _("Inizio")}))
    for j, (title, page) in enumerate(starts):
        end = starts[j + 1][1] if j + 1 < len(starts) else n
        if end <= page:
            continue  # parte vuota: i segnalibri passano alla successiva
        parts.append(SplitPart(list(range(page, end)), {"segnalibro": title}))
    return SplitPlan(parts, warnings)


def page_is_blank(page: pymupdf.Page, tolerance: float = 0.005) -> bool:
    """Pagina vuota: niente testo e quasi nessun pixel non bianco."""
    if page.get_text("text").strip():
        return False
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.3, 0.3), colorspace=pymupdf.csGRAY, alpha=False)
    samples = pix.samples
    if not samples:
        return True
    dark = sum(1 for b in samples if b < 230)
    return dark / len(samples) <= tolerance


def plan_separators(pdf: pymupdf.Document, *, blank: bool = True, pattern: str | None = None,
                    discard: bool = True, tolerance: float = 0.005, top_fraction: float | None = None) -> SplitPlan:
    """Divide sulle pagine vuote o sulle pagine che contengono un testo/espressione (PAG-23)."""
    n = pdf.page_count
    parts: list[SplitPart] = []
    warnings: list[str] = []
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise RangeError(_("Espressione regolare non valida: {e}.").format(e=e)) from e
        current: SplitPart | None = None
        for i in range(n):
            page = pdf[i]
            clip = None
            if top_fraction:
                r = page.rect
                clip = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height * top_fraction) * page.derotation_matrix
            m = rx.search(page.get_text("text", clip=clip))
            if m or current is None:
                fields = {}
                if m:
                    fields = {f"match{g}": v for g, v in enumerate(m.groups(), 1) if v is not None}
                    fields["match"] = m.group(0)
                current = SplitPart([], fields)
                parts.append(current)
            current.indices.append(i)
        if not any(p.fields for p in parts):
            warnings.append(_("Nessuna corrispondenza trovata: si otterrà un solo file."))
    else:
        current_idx: list[int] = []
        for i in range(n):
            if page_is_blank(pdf[i], tolerance):
                if current_idx:
                    parts.append(SplitPart(current_idx))
                current_idx = [] if discard else [i]
                if not discard and parts:
                    pass
            else:
                current_idx.append(i)
        if current_idx:
            parts.append(SplitPart(current_idx))
        if len(parts) <= 1:
            warnings.append(_("Nessuna pagina separatrice trovata: si otterrà un solo file."))
    return SplitPlan([p for p in parts if p.indices], warnings)


def plan_size(pdf: pymupdf.Document, max_bytes: int, progress: Callable[[float], None] | None = None) -> SplitPlan:
    """Parti che, scritte su file, non superano `max_bytes` (PAG-22)."""
    n = pdf.page_count
    if max_bytes < 2048:
        raise PageOpError(_("La soglia è inferiore alla dimensione minima di un PDF."))
    parts: list[SplitPart] = []
    current: list[int] = []
    for i in range(n):
        if progress:
            progress(i / max(1, n))
        trial = current + [i]
        size = len(extract_pages(pdf, trial))
        if size <= max_bytes or not current:
            current = trial
            if size > max_bytes:
                parts.append(SplitPart(current, oversize=True))
                current = []
        else:
            parts.append(SplitPart(current))
            current = [i]
            if len(extract_pages(pdf, current)) > max_bytes:
                parts.append(SplitPart(current, oversize=True))
                current = []
    if current:
        parts.append(SplitPart(current))
    warnings = []
    big = [p for p in parts if p.oversize]
    if big:
        warnings.append(_("{k} parti di una sola pagina superano la soglia.").format(k=len(big)))
    return SplitPlan(parts, warnings)


def split_document(pdf: pymupdf.Document, method: str = "every", value=None, **options) -> SplitPlan:
    """Calcola la divisione del documento (non scrive file).

    `method`: "every" (value = N), "parts" (value = K), "ranges" (value = testo),
    "bookmarks" (value = livello), "size" (value = byte), "separators"
    (options: blank, pattern, discard). Per produrre i file usare
    `split_to_bytes()` o `write_parts()`."""
    n = pdf.page_count
    if method == "every":
        return plan_every(n, int(value))
    if method == "parts":
        return plan_parts(n, int(value))
    if method == "ranges":
        return plan_ranges(n, str(value), options.get("collect_rest", False))
    if method == "bookmarks":
        return plan_bookmarks(pdf, int(value or 1), options.get("merge_leading", False))
    if method == "size":
        return plan_size(pdf, int(value), options.get("progress"))
    if method == "separators":
        return plan_separators(pdf, blank=options.get("blank", True), pattern=options.get("pattern"),
                               discard=options.get("discard", True), tolerance=options.get("tolerance", 0.005),
                               top_fraction=options.get("top_fraction"))
    raise PageOpError(_("Metodo di divisione sconosciuto."))


def part_bytes(pdf: pymupdf.Document, part: SplitPart) -> bytes:
    return extract_pages(pdf, part.indices)


def split_to_bytes(pdf: pymupdf.Document, plan: SplitPlan) -> list[bytes]:
    return [part_bytes(pdf, p) for p in plan.parts]


def write_parts(pdf: pymupdf.Document, parts: Sequence[SplitPart], folder: str | os.PathLike, template: str,
                base_name: str, *, overwrite: bool = False, single_page_field: bool = False,
                progress: Callable[[float, str], None] | None = None) -> list[Path]:
    """Scrive le parti nella cartella con il modello di nome. Restituisce i file creati.

    Se interrotta (eccezione da `progress`) i file già scritti restano."""
    folder = Path(folder)
    if not folder.is_dir():
        raise PageOpError(_("La cartella «{f}» non esiste.").format(f=folder))
    if not os.access(folder, os.W_OK):
        raise PageOpError(_("Non è possibile scrivere nella cartella «{f}».").format(f=folder))
    from foliant.api import atomic_write
    created: list[Path] = []
    taken: set[str] = set()
    total = len(parts)
    for j, part in enumerate(parts, 1):
        if progress:
            progress((j - 1) / max(1, total), _("File {j} di {n}").format(j=j, n=total))
        fields = {"nome": base_name, "n": j, "pagina": part.indices[0] + 1,
                  "inizio": part.indices[0] + 1, "fine": part.indices[-1] + 1}
        fields.update(part.fields)
        name = render_name(template, **fields)
        target = unique_path(folder, name + ".pdf", overwrite, taken)
        with ENGINE_LOCK:
            data = part_bytes(pdf, part)
        atomic_write(target, data)
        created.append(target)
    return created


# =========================================================================
# PAG-24 — unione
# =========================================================================

@dataclass
class MergeEntry:
    path: str
    ranges: str | None = None      # espressione PAG-02 (None = tutte)
    password: str | None = None


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".jp2", ".jpx", ".pnm", ".pbm", ".pgm", ".ppm"}


def image_to_pdf(path: str | os.PathLike, dpi_default: float = 150.0) -> pymupdf.Document:
    """Converte un'immagine in un PDF con una pagina per fotogramma, a dimensione naturale."""
    from .images import images_to_pdf
    return images_to_pdf([str(path)], dpi_default=dpi_default)


def merge_files(paths: Sequence[str | os.PathLike | MergeEntry], *, bookmarks: bool = True,
                keep_bookmarks: bool = True, annots: bool = True, widgets: bool = True,
                blank_after_odd: bool = False, metadata: dict | None = None,
                progress: Callable[[float, str], None] | None = None,
                errors: list[str] | None = None) -> bytes:
    """Unisce più file (PDF, immagini, altri formati convertibili) in un nuovo PDF.

    Ogni elemento può essere un percorso o un `MergeEntry` (con intervallo e
    password). Con `bookmarks` il nome di ciascun file diventa un segnalibro di
    primo livello con i segnalibri originali annidati. I file illeggibili sono
    esclusi e riportati in `errors` (se fornita), altrimenti sollevano errore."""
    entries = [p if isinstance(p, MergeEntry) else MergeEntry(str(p)) for p in paths]
    if not entries:
        raise PageOpError(_("Aggiungi almeno un file da unire."))
    out = pymupdf.open()
    toc: list[list] = []
    first_meta: dict | None = None
    total = len(entries)
    try:
        for j, entry in enumerate(entries):
            name = Path(entry.path).stem
            if progress:
                progress(j / total, _("Unione di «{n}» ({j} di {t})").format(n=name, j=j + 1, t=total))
            try:
                if Path(entry.path).suffix.lower() in IMAGE_SUFFIXES:
                    src = image_to_pdf(entry.path)
                else:
                    src = open_source(entry.path, entry.password)
            except FoliantError as e:
                if errors is None:
                    raise
                errors.append(f"{Path(entry.path).name}: {e}")
                continue
            with ENGINE_LOCK:
                try:
                    if first_meta is None:
                        first_meta = dict(src.metadata or {})
                    if entry.ranges:
                        indices = parse_ranges(entry.ranges, src.page_count, ordered=True)
                    else:
                        indices = list(range(src.page_count))
                    start = out.page_count
                    src_toc = src.get_toc(simple=True) if keep_bookmarks else []
                    _insert_runs(out, src, indices, out.page_count, annots, widgets)
                    if bookmarks:
                        toc.append([1, name, start + 1])
                    remap: dict[int, int] = {}
                    for pos, i in enumerate(indices):
                        remap.setdefault(i, start + pos)
                    for lvl, title, page in src_toc:
                        if page - 1 in remap:
                            toc.append([lvl + (1 if bookmarks else 0), title, remap[page - 1] + 1])
                    if blank_after_odd and len(indices) % 2 == 1:
                        mb = out[out.page_count - 1].mediabox
                        out.new_page(width=mb.width, height=mb.height)
                finally:
                    src.close()
        if out.page_count == 0:
            raise PageOpError(_("Nessun file è stato unito."))
        with ENGINE_LOCK:
            _set_toc_safe(out, toc)
            meta = metadata if metadata is not None else first_meta or {}
            meta = {k: v for k, v in meta.items() if v and k not in ("encryption", "format")}
            try:
                out.set_metadata(meta)
            except Exception:
                pass
            if progress:
                progress(1.0, _("Scrittura del file…"))
            return out.tobytes(garbage=3, deflate=True)
    finally:
        out.close()


def _set_toc_safe(pdf: pymupdf.Document, toc: list[list]) -> None:
    """Imposta i segnalibri correggendo salti di livello non ammessi dal motore."""
    fixed: list[list] = []
    prev = 0
    for lvl, title, page in toc:
        lvl = max(1, min(lvl, prev + 1))
        fixed.append([lvl, title, page])
        prev = lvl
    if fixed:
        pdf.set_toc(fixed)
