"""Allegati (VIS-22): logica pura senza interfaccia.

Elenca i file incorporati a livello di documento (name tree `/EmbeddedFiles`)
e nelle annotazioni `/FileAttachment` delle pagine, ne estrae i dati,
ripulisce i nomi per il salvataggio e riconosce i tipi eseguibili.
Le funzioni che ricevono `pdf` vanno chiamate tenendo `ENGINE_LOCK`
(o dentro `Document.change()`).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime

import pymupdf

from foliant.api import format_number

DOCUMENT = "document"
PAGE = "page"

# Estensioni che non si aprono mai direttamente (eseguibili, script, installatori).
EXECUTABLE_EXTENSIONS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".msi", ".msp", ".dll", ".cpl", ".bat", ".cmd", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".jar", ".class",
    ".sh", ".bash", ".zsh", ".ksh", ".csh", ".fish", ".command", ".py", ".pyc", ".pyw", ".pl", ".rb",
    ".php", ".lua", ".tcl", ".bin", ".run", ".out", ".elf", ".so", ".appimage", ".desktop", ".deb",
    ".rpm", ".apk", ".app", ".dmg", ".pkg", ".snap", ".flatpakref", ".action", ".workflow", ".scpt",
    ".gadget", ".application", ".xbap", ".msc", ".inf", ".url", ".iso", ".img",
})

_RESERVED = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


@dataclass
class Attachment:
    kind: str                 # DOCUMENT o PAGE
    key: str                  # nome nel name tree (documento) o descrizione univoca
    filename: str             # nome del file indicato nell'allegato
    description: str = ""
    size: int = -1            # byte decodificati (-1 = sconosciuto)
    created: datetime | None = None
    modified: datetime | None = None
    page: int | None = None   # indice di pagina (allegati di pagina)
    annot_xref: int = 0       # xref dell'annotazione (allegati di pagina)
    relationship: str = ""    # /AFRelationship, se presente
    mime: str = ""

    @property
    def is_pdf(self) -> bool:
        return self.filename.lower().endswith(".pdf") or self.mime == "application/pdf"


def parse_pdf_date(value: str | None) -> datetime | None:
    """Converte una data PDF (D:AAAAMMGGHHmmSS…) in datetime (ora locale del file)."""
    if not value:
        return None
    s = value.strip()
    if s.startswith("D:"):
        s = s[2:]
    digits = re.match(r"\d{4,14}", s)
    if not digits:
        return None
    d = digits.group(0)
    parts = [int(d[0:4])]
    for i, default in ((4, 1), (6, 1), (8, 0), (10, 0), (12, 0)):
        parts.append(int(d[i:i + 2]) if len(d) >= i + 2 else default)
    try:
        return datetime(*parts)
    except ValueError:
        return None


def _key(pdf: pymupdf.Document, xref: int, key: str) -> str:
    try:
        kind, value = pdf.xref_get_key(xref, key)
    except Exception:
        return ""
    if kind in ("null", ""):
        return ""
    if kind == "string":
        return value
    if kind == "name":
        return value.lstrip("/")
    return value


def _ref(value: str) -> int:
    try:
        return int(value.split()[0])
    except (ValueError, IndexError):
        return 0


# ------------------------------------------------------------------ portfolio
@dataclass
class Folder:
    id: int
    name: str
    parent: int | None = None


@dataclass
class Collection:
    """Portfolio PDF (`/Collection`): documento iniziale e cartelle."""
    initial: str | None = None                      # chiave nel name tree (`/D`)
    view: str = "D"                                 # /View: D (dettagli), T (riquadri), H (nascosto)
    root: int | None = None                         # id della cartella radice
    folders: dict[int, Folder] | None = None

    def folder_of(self, key: str) -> int | None:
        """Cartella di un file: i nomi nel name tree di un portfolio con cartelle iniziano con
        «<id>»; senza prefisso il file è nella radice."""
        m = re.match(r"<(\d+)>", key or "")
        if m and self.folders and int(m.group(1)) in self.folders:
            return int(m.group(1))
        return self.root

    def path_of(self, folder: int | None) -> list[Folder]:
        """Cartelle dalla radice (esclusa) fino a `folder`."""
        out: list[Folder] = []
        seen: set[int] = set()
        while folder is not None and self.folders and folder in self.folders and folder != self.root \
                and folder not in seen:
            seen.add(folder)
            f = self.folders[folder]
            out.append(f)
            folder = f.parent
        return list(reversed(out))


def read_collection(pdf: pymupdf.Document) -> Collection | None:
    """Il portfolio del documento, oppure None se non è una collezione."""
    try:
        kind, value = pdf.xref_get_key(pdf.pdf_catalog(), "Collection")
    except Exception:
        return None
    if kind != "xref":
        return None
    cx = _ref(value)
    col = Collection(initial=_key(pdf, cx, "D") or None, view=_key(pdf, cx, "View") or "D")
    kind, value = pdf.xref_get_key(cx, "Folders")
    if kind == "xref":
        folders: dict[int, Folder] = {}
        seen: set[int] = set()

        def walk(xref: int, parent: int | None, depth: int) -> None:
            while xref and xref not in seen and depth < 64 and len(seen) < 10000:
                seen.add(xref)
                try:
                    fid = int(_key(pdf, xref, "ID") or "-1")
                except ValueError:
                    fid = -1
                if fid >= 0:
                    folders[fid] = Folder(fid, _key(pdf, xref, "Name"), parent)
                    if col.root is None and parent is None:
                        col.root = fid
                    child_kind, child = pdf.xref_get_key(xref, "Child")
                    if child_kind == "xref":
                        walk(_ref(child), fid, depth + 1)
                nxt_kind, nxt = pdf.xref_get_key(xref, "Next")
                xref = _ref(nxt) if nxt_kind == "xref" else 0
        walk(_ref(value), None, 0)
        col.folders = folders
    return col


def _page_attachment(pdf: pymupdf.Document, page_index: int, annot) -> Attachment:
    info = annot.file_info or {}
    xref = annot.xref
    stream = _ref(_key(pdf, xref, "FS/EF/F"))
    created = modified = None
    mime = ""
    if stream:
        created = parse_pdf_date(_key(pdf, stream, "Params/CreationDate"))
        modified = parse_pdf_date(_key(pdf, stream, "Params/ModDate"))
        mime = _key(pdf, stream, "Subtype").replace("#2F", "/").replace("#2f", "/")
    if modified is None:
        modified = parse_pdf_date(_key(pdf, xref, "M"))
    size = info.get("size", -1)
    if size in (None, -1):
        size = info.get("length", -1)
    name = info.get("filename") or "allegato"
    return Attachment(
        kind=PAGE, key=f"p{page_index}:{xref}", filename=name, description=info.get("description") or "",
        size=int(size if size is not None else -1), created=created, modified=modified, page=page_index,
        annot_xref=xref, relationship=_key(pdf, xref, "FS/AFRelationship"), mime=mime,
    )


def list_attachments(pdf: pymupdf.Document) -> list[Attachment]:
    """Tutti gli allegati: prima quelli del documento, poi quelli di pagina."""
    items: list[Attachment] = []
    for name in pdf.embfile_names():
        try:
            info = pdf.embfile_info(name)
        except Exception:
            info = {}
        size = info.get("size", -1)
        if size in (None, -1):
            size = info.get("length", -1)
        items.append(Attachment(
            kind=DOCUMENT, key=name,
            filename=info.get("ufilename") or info.get("filename") or name,
            description=info.get("description") or "",
            size=int(size if size is not None else -1),
            created=parse_pdf_date(info.get("creationDate")),
            modified=parse_pdf_date(info.get("modDate")),
        ))
    for index in range(pdf.page_count):
        page = pdf[index]
        for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]):
            try:
                items.append(_page_attachment(pdf, index, annot))
            except Exception:
                continue
    return items


def _find_annot(pdf: pymupdf.Document, item: Attachment):
    if item.page is None or not (0 <= item.page < pdf.page_count):
        raise KeyError(item.key)
    page = pdf[item.page]
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]):
        if annot.xref == item.annot_xref:
            return page, annot
    raise KeyError(item.key)


def extract(pdf: pymupdf.Document, item: Attachment) -> bytes:
    """Dati decodificati del file incorporato."""
    if item.kind == DOCUMENT:
        return pdf.embfile_get(item.key)
    _page, annot = _find_annot(pdf, item)
    return annot.get_file()


def safe_filename(name: str, fallback: str = "allegato") -> str:
    """Nome di file sicuro: niente cartelle, "..", caratteri di controllo o riservati."""
    name = (name or "").replace("\\", "/")
    name = name.split("/")[-1]
    name = _RESERVED.sub("_", name).strip().strip(".").strip()
    if name in ("", ".", ".."):
        return fallback
    return name[:200]


_EXEC_MAGIC = (b"MZ", b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def is_executable(name: str, data: bytes | None = None) -> bool:
    """True se il file è un eseguibile o uno script (apertura bloccata).

    Conta l'ultima estensione (anche "fattura.pdf.exe") e, se forniti, i primi
    byte dei dati (programmi Windows/Linux/macOS, script con "#!").
    """
    lower = safe_filename(name).lower()
    suffixes = lower.split(".")[1:]
    if suffixes and "." + suffixes[-1] in EXECUTABLE_EXTENSIONS:
        return True
    if data:
        return any(data.startswith(m) for m in _EXEC_MAGIC)
    return False


def looks_like_pdf(name: str, data: bytes) -> bool:
    return data[:1024].lstrip().startswith(b"%PDF") or (name.lower().endswith(".pdf") and b"%PDF" in data[:1024])


def unique_name(pdf: pymupdf.Document, name: str) -> str:
    """Nome non ancora usato nel name tree degli allegati del documento."""
    existing = set(pdf.embfile_names())
    if name not in existing:
        return name
    stem, ext = os.path.splitext(name)
    n = 2
    while f"{stem} ({n}){ext}" in existing:
        n += 1
    return f"{stem} ({n}){ext}"


def add(pdf: pymupdf.Document, filename: str, data: bytes, description: str = "") -> str:
    """Aggiunge un allegato di documento; restituisce il nome usato."""
    clean = safe_filename(filename)
    key = unique_name(pdf, clean)
    pdf.embfile_add(key, data, filename=clean, ufilename=clean, desc=description or None)
    return key


def delete(pdf: pymupdf.Document, item: Attachment) -> None:
    """Rimuove l'allegato (dal name tree o eliminando l'annotazione di pagina)."""
    if item.kind == DOCUMENT:
        _delete_embedded(pdf, item.key)
        return
    page, annot = _find_annot(pdf, item)
    page.delete_annot(annot)


def _embedded_signature(pdf: pymupdf.Document) -> list[tuple]:
    out = []
    for name in pdf.embfile_names():
        info = pdf.embfile_info(name)
        out.append((name, info.get("ufilename"), info.get("description"), info.get("length")))
    return out


def _delete_embedded(pdf: pymupdf.Document, name: str) -> None:
    """Elimina un file dal name tree `/EmbeddedFiles`.

    `Document.embfile_del` del motore (PyMuPDF 2.0) cancella la coppia
    nome/valore sbagliata quando l'allegato non è il primo (usa l'indice
    dell'elemento come indice dell'array [nome, valore, …]): qui si usa
    l'indice corretto e si verifica il risultato; se l'albero non è piatto
    (con `/Kids`) si ricostruisce l'elenco.
    """
    names = pdf.embfile_names()
    if name not in names:
        raise KeyError(name)
    before = [e for e in _embedded_signature(pdf) if e[0] != name]
    idx = names.index(name)
    kind, value = pdf.xref_get_key(pdf.pdf_catalog(), "Names/EmbeddedFiles/Names")
    if kind == "array" and hasattr(pdf, "_embfile_del"):
        pdf._embfile_del(2 * idx)   # cancella gli elementi 2*idx+1 (valore) e 2*idx (nome)
    else:
        _rebuild_without(pdf, name)
    if _embedded_signature(pdf) != before:
        raise RuntimeError("Eliminazione dell'allegato non riuscita.")


def _rebuild_without(pdf: pymupdf.Document, name: str) -> None:
    keep = []
    for other in pdf.embfile_names():
        if other == name:
            continue
        info = pdf.embfile_info(other)
        keep.append((other, pdf.embfile_get(other), info.get("filename"), info.get("ufilename"),
                     info.get("description")))
    pdf.xref_set_key(pdf.pdf_catalog(), "Names/EmbeddedFiles", "null")
    for other, data, filename, ufilename, desc in keep:
        pdf.embfile_add(other, data, filename=filename, ufilename=ufilename, desc=desc)


def save_all_names(items: list[Attachment]) -> list[str]:
    """Nomi sicuri e distinti per salvare più allegati nella stessa cartella."""
    used: set[str] = set()
    out = []
    for item in items:
        base = safe_filename(item.filename)
        name = base
        stem, ext = os.path.splitext(base)
        n = 2
        while name.lower() in used:
            name = f"{stem} ({n}){ext}"
            n += 1
        used.add(name.lower())
        out.append(name)
    return out


def format_size(size: int) -> str:
    if size is None or size < 0:
        return "—"
    if size < 1024:
        return f"{format_number(size)} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{format_number(value, 1)} {unit}"
    return f"{size} B"
