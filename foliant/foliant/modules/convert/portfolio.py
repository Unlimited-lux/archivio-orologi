"""Portfolio PDF (CNV-34), senza interfaccia: file allegati intatti, cartelle, colonne e copertina."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pymupdf

from foliant.api import ENGINE_LOCK, _

from .common import ConversionError, check

SUBTYPES = {"text": "S", "number": "N", "date": "D"}


def _s(text: str) -> str:
    return pymupdf.get_pdf_str(str(text))


def create_portfolio(entries: list[dict], *, title: str = "Portfolio", view: str = "list",
                     columns: list[tuple[str, str, str]] | None = None, sort: tuple[str, bool] | None = None,
                     initial: str | int | None = None, cover_pdf: str | None = None,
                     page_size: str = "A4", progress=None) -> tuple[bytes, list[str]]:
    """Crea il portfolio e restituisce (byte, avvisi).

    `entries`: [{"path", "folder" (facoltativa), "name", "description", "values": {chiave: valore}}].
    `columns`: colonne personalizzate [(chiave, etichetta, "text"|"number"|"date")].
    `view`: "list" o "thumbnails". `sort`: (chiave, crescente). `initial`: nome del file o indice della voce da mostrare.
    `cover_pdf`: PDF da usare come copertina (altrimenti copertina generata con l'elenco dei file).
    """
    if not entries:
        raise ConversionError(_("Nessun file da inserire nel portfolio."))
    columns = columns or []
    warnings: list[str] = []
    files = []
    for k, e in enumerate(entries):
        check(progress, 0.6 * k / len(entries), _("Lettura di «{n}»").format(n=Path(e["path"]).name))
        try:
            data = Path(e["path"]).read_bytes()
        except OSError as err:
            warnings.append(_("«{n}» non leggibile, escluso ({e}).").format(n=Path(e["path"]).name, e=err.strerror))
            continue
        if len(data) > 100 * 1024 * 1024:
            warnings.append(_("«{n}» supera 100 MB: il portfolio sarà molto grande.").format(n=Path(e["path"]).name))
        if data[:5] == b"%PDF-" and b"/Encrypt" in data[-4096:]:
            warnings.append(_("«{n}» è protetto: per aprirlo servirà la sua password.").format(n=Path(e["path"]).name))
        files.append((dict(e, _index=k), data))
    if not files:
        raise ConversionError(_("Nessuno dei file scelti è leggibile."))
    folders = sorted({(e.get("folder") or "").strip() for e, _d in files} - {""})
    folder_id = {name: i + 1 for i, name in enumerate(folders)}
    with ENGINE_LOCK:
        doc = pymupdf.open()
        if cover_pdf:
            try:
                with pymupdf.open(cover_pdf) as cover:
                    if cover.needs_pass:
                        raise ValueError("cifrato")
                    doc.insert_pdf(cover)
            except Exception:
                warnings.append(_("Copertina «{n}» non utilizzabile: generata automaticamente.").format(
                    n=Path(cover_pdf).name))
        if doc.page_count == 0:
            _cover(doc, title, files, page_size)
        used: set[str] = set()
        names: list[str] = []
        specs: list[tuple[str, int]] = []
        by_index: dict[int, str] = {}
        for k, (e, data) in enumerate(files):
            check(progress, 0.6 + 0.35 * k / len(files), _("Inserimento dei file…"))
            display = e.get("name") or Path(e["path"]).name
            folder = (e.get("folder") or "").strip()
            base = f"<{folder_id[folder]}>{display}" if folder else display
            key, n = base, 1
            while key.lower() in used:
                n += 1
                stem, ext = os.path.splitext(display)
                key = (f"<{folder_id[folder]}>" if folder else "") + f"{stem} ({n}){ext}"
            used.add(key.lower())
            names.append(key)
            by_index[e["_index"]] = key
            mtime = dt.datetime.fromtimestamp(os.path.getmtime(e["path"])).strftime("D:%Y%m%d%H%M%S")
            stream = doc.get_new_xref()
            doc.update_object(stream, f"<</Type/EmbeddedFile/Params <</Size {len(data)}/ModDate {_s(mtime)}>>>>")
            doc.update_stream(stream, data)           # contenuto identico byte per byte (compresso Flate)
            ci = [f"/{c} {_value(e.get('values', {}).get(c, ''), t)}" for c, _l, t in columns]
            fname = display if folder else Path(key).name
            fs = doc.get_new_xref()
            doc.update_object(fs, f"<</Type/Filespec/F {_s(fname)}/UF {_s(fname)}/Desc {_s(e.get('description') or '')}"
                                  f"/EF <</F {stream} 0 R>>" + (f"/CI <<{' '.join(ci)}>>" if ci else "") + ">>")
            specs.append((key, fs))
        pairs = sorted(specs, key=lambda kv: kv[0].encode("utf-16-be"))
        tree = "[" + " ".join(f"{_s(k)} {x} 0 R" for k, x in pairs) + "]"
        doc.xref_set_key(doc.pdf_catalog(), "Names", f"<</EmbeddedFiles <</Names {tree}>>>>")
        # /Collection
        schema = ["/FileName <</Type/CollectionField/Subtype/F/N " + _s(_("Nome")) + "/O 0/V true>>",
                  "/Description <</Type/CollectionField/Subtype/Desc/N " + _s(_("Descrizione")) + "/O 1/V true>>",
                  "/Size <</Type/CollectionField/Subtype/Size/N " + _s(_("Dimensione")) + "/O 2/V true>>",
                  "/ModDate <</Type/CollectionField/Subtype/ModDate/N " + _s(_("Data di modifica")) + "/O 3/V true>>"]
        for i, (c, label, t) in enumerate(columns):
            schema.append(f"/{c} <</Type/CollectionField/Subtype/{SUBTYPES.get(t, 'S')}/N {_s(label)}/O {4 + i}/V true>>")
        coll = [f"/Type/Collection/Schema <<{' '.join(schema)}>>", f"/View/{'T' if view == 'thumbnails' else 'D'}"]
        if sort:
            coll.append(f"/Sort <</Type/CollectionSort/S/{sort[0]}/A {'true' if sort[1] else 'false'}>>")
        first = by_index.get(initial) if isinstance(initial, int) else (initial if initial in names else None)
        if first:
            coll.append(f"/D {_s(first)}")
        if folders:
            coll.append(f"/Folders {_folders(doc, folders, folder_id)} 0 R")
        cx = doc.get_new_xref()
        doc.update_object(cx, "<<" + "".join(coll) + ">>")
        doc.xref_set_key(doc.pdf_catalog(), "Collection", f"{cx} 0 R")
        out = doc.tobytes(garbage=3, deflate=True)
        doc.close()
    return out, warnings


def _value(v, t: str) -> str:
    if t == "number":
        try:
            return str(float(v)) if v not in ("", None) else "0"
        except (TypeError, ValueError):
            return "0"
    if t == "date" and isinstance(v, (dt.date, dt.datetime)):
        return _s(v.strftime("D:%Y%m%d%H%M%S"))
    return _s(v or "")


def _folders(doc: pymupdf.Document, folders: list[str], folder_id: dict) -> int:
    root = doc.get_new_xref()
    xrefs = [doc.get_new_xref() for _f in folders]
    for i, (name, x) in enumerate(zip(folders, xrefs)):
        nxt = f"/Next {xrefs[i + 1]} 0 R" if i + 1 < len(xrefs) else ""
        doc.update_object(x, f"<</Type/Folder/ID {folder_id[name]}/Name {_s(name)}/Parent {root} 0 R{nxt}>>")
    doc.update_object(root, f"<</Type/Folder/ID 0/Name {_s('')}/Child {xrefs[0]} 0 R>>")
    return root


def _cover(doc: pymupdf.Document, title: str, files, page_size: str = "A4") -> None:
    """Copertina leggibile anche dai lettori senza supporto ai portfolio (formato carta locale)."""
    w, h = (612.0, 792.0) if page_size == "Letter" else (595.28, 841.89)
    page = doc.new_page(width=w, height=h)
    y = 72.0
    page.insert_text((56, y), title, fontsize=20, fontname="helv")
    y += 34
    page.insert_text((56, y), _("Questo PDF è un portfolio: contiene i file seguenti come allegati."),
                     fontsize=10, fontname="helv")
    y += 26
    for e, data in files:
        if y > page.rect.height - 60:
            page = doc.new_page(width=w, height=h)
            y = 72.0
        folder = (e.get("folder") or "").strip()
        name = (e.get("name") or Path(e["path"]).name)
        line = (f"{folder} / " if folder else "") + name + f"  ({_size(len(data))})"
        page.insert_text((56, y), line, fontsize=11, fontname="helv")
        y += 15
        if e.get("description"):
            page.insert_text((72, y), str(e["description"])[:120], fontsize=9, fontname="helv", color=(0.35, 0.35, 0.35))
            y += 14
        y += 4


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".", ",")
        n /= 1024
    return ""


# ------------------------------------------------------------------ modifica
def is_portfolio(pdf: pymupdf.Document) -> bool:
    try:
        return pdf.xref_get_key(pdf.pdf_catalog(), "Collection")[0] != "null"
    except Exception:
        return False


def folder_map(pdf: pymupdf.Document) -> dict[str, int]:
    """{nome cartella: ID} dalle cartelle di `/Collection /Folders` (tutti i livelli)."""
    out: dict[str, int] = {}
    kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "Collection/Folders")
    if kind != "xref":
        return out
    seen: set[int] = set()
    stack = [int(val.split()[0])]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        name = pdf.xref_get_key(x, "Name")
        fid = pdf.xref_get_key(x, "ID")
        if name[0] == "string" and name[1] and fid[0] == "int":
            out[name[1]] = int(fid[1])
        for key in ("Child", "Next"):
            k, v = pdf.xref_get_key(x, key)
            if k == "xref":
                stack.append(int(v.split()[0]))
    return out


def _ensure_collection(pdf: pymupdf.Document) -> int:
    kind, val = pdf.xref_get_key(pdf.pdf_catalog(), "Collection")
    if kind == "xref":
        return int(val.split()[0])
    cx = pdf.get_new_xref()
    pdf.update_object(cx, "<</Type/Collection/View/D>>")
    pdf.xref_set_key(pdf.pdf_catalog(), "Collection", f"{cx} 0 R")
    return cx


def _add_folder(pdf: pymupdf.Document, name: str, existing: dict[str, int]) -> int:
    cx = _ensure_collection(pdf)
    new_id = max(existing.values(), default=0) + 1
    kind, val = pdf.xref_get_key(cx, "Folders")
    if kind != "xref":
        root = pdf.get_new_xref()
        pdf.update_object(root, f"<</Type/Folder/ID 0/Name {_s('')}>>")
        pdf.xref_set_key(cx, "Folders", f"{root} 0 R")
    else:
        root = int(val.split()[0])
    x = pdf.get_new_xref()
    k, child = pdf.xref_get_key(root, "Child")
    nxt = f"/Next {child}" if k == "xref" else ""
    pdf.update_object(x, f"<</Type/Folder/ID {new_id}/Name {_s(name)}/Parent {root} 0 R{nxt}>>")
    pdf.xref_set_key(root, "Child", f"{x} 0 R")
    existing[name] = new_id
    return new_id


def add_files(pdf: pymupdf.Document, entries: list[dict]) -> list[str]:
    """Aggiunge file al portfolio aperto (da chiamare dentro `document.change()`). Restituisce i nomi."""
    folders = folder_map(pdf)
    _ensure_collection(pdf)
    used = {n.lower() for n in pdf.embfile_names()}
    added = []
    for e in entries:
        data = Path(e["path"]).read_bytes()
        display = e.get("name") or Path(e["path"]).name
        folder = (e.get("folder") or "").strip()
        prefix = ""
        if folder:
            fid = folders.get(folder) or _add_folder(pdf, folder, folders)
            prefix = f"<{fid}>"
        key, n = prefix + display, 1
        while key.lower() in used:
            n += 1
            stem, ext = os.path.splitext(display)
            key = f"{prefix}{stem} ({n}){ext}"
        used.add(key.lower())
        pdf.embfile_add(key, data, filename=display, ufilename=display, desc=e.get("description") or "")
        added.append(key)
    return added


def remove_files(pdf: pymupdf.Document, names: list[str]) -> None:
    for name in names:
        pdf.embfile_del(name)
