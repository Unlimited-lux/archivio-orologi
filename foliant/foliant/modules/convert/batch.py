"""Conversione in blocco di più PDF (CNV-17), senza interfaccia."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from foliant.api import FoliantError, OperationCancelled, PasswordRequired, WrongPassword, _

from .common import check
from .engine import FORMATS, normalize_format, run_conversion

OK, WARNINGS, ERROR, SKIPPED = "completato", "con avvisi", "errore", "saltato"


@dataclass
class BatchItem:
    path: str
    status: str = "in attesa"
    outputs: list[str] = field(default_factory=list)
    message: str = ""


def collect_pdfs(paths: list[str], recursive: bool = True) -> tuple[list[str], list[str]]:
    """File PDF da elaborare (cartelle espanse, duplicati ignorati) e file non PDF scartati."""
    found: list[str] = []
    rejected: list[str] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        key = str(p.resolve())
        if key in seen:
            return
        seen.add(key)
        if p.suffix.lower() == ".pdf":
            found.append(str(p))
        else:
            rejected.append(str(p))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for child in sorted(it, key=lambda c: str(c).lower()):
                if child.is_file():
                    if child.suffix.lower() == ".pdf":
                        add(child)
        elif p.is_file():
            add(p)
    return found, rejected


def output_for(path: str, fmt: str, out_dir: str | None, root: str | None = None, keep_structure: bool = False) -> str:
    """Percorso di uscita: accanto all'originale o nella cartella scelta (con sottocartelle)."""
    src = Path(path)
    ext = FORMATS[fmt][0]
    stem = src.stem + ("_pdfa" if fmt.startswith("pdfa") else "")
    if not out_dir:
        return str(src.with_name(stem + ext))
    target = Path(out_dir)
    if keep_structure and root:
        try:
            rel = src.parent.resolve().relative_to(Path(root).resolve())
            target = target / rel
        except ValueError:
            pass
    return str(target / (stem + ext))


def common_root(paths: list[str]) -> str | None:
    if not paths:
        return None
    try:
        return os.path.commonpath([str(Path(p).parent.resolve()) for p in paths])
    except ValueError:
        return None


def batch_convert(files: list[str], fmt: str, *, out_dir: str | None = None, keep_structure: bool = False,
                  root: str | None = None, passwords: dict[str, str] | None = None, progress=None,
                  **options) -> list[BatchItem]:
    """Converte i file uno dopo l'altro; un errore su un file non ferma gli altri.

    I file cifrati senza password nota sono saltati e segnalati. Con
    l'annullamento i file già completati restano, quello in corso è scartato.
    """
    fmt = normalize_format(fmt)
    passwords = passwords or {}
    root = root or common_root(files)
    items = [BatchItem(p) for p in files]
    n = len(items)

    class _Sub:
        """Avanzamento del singolo file riportato su quello complessivo."""

        def __init__(self, k: int):
            self.k = k

        def update(self, fraction=None, text=None):
            frac = (self.k + (fraction or 0.0)) / max(1, n)
            check(progress, frac, _("File {i} di {n}: {name}").format(i=self.k + 1, n=n,
                                                                      name=Path(items[self.k].path).name))

        def check(self):
            check(progress)

    for k, item in enumerate(items):
        check(progress, k / max(1, n), _("File {i} di {n}: {name}").format(i=k + 1, n=n, name=Path(item.path).name))
        out = output_for(item.path, fmt, out_dir, root, keep_structure)
        try:
            result = run_conversion(item.path, out, fmt, _Sub(k), password=passwords.get(item.path), **options)
            item.outputs = result.paths
            item.status = WARNINGS if result.warnings else OK
            item.message = "; ".join(result.warnings)
        except OperationCancelled:
            item.status = SKIPPED
            item.message = _("Annullato")
            for rest in items[k + 1:]:
                rest.status = SKIPPED
                rest.message = _("Annullato")
            break
        except (PasswordRequired, WrongPassword):
            item.status = SKIPPED
            item.message = _("Protetto da password")
        except FoliantError as e:
            item.status = ERROR
            item.message = str(e)
        except Exception as e:   # un file problematico non deve fermare il lotto
            item.status = ERROR
            item.message = _("Errore imprevisto: {e}").format(e=e)
    return items


def report_csv(items: list[BatchItem]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow([_("File"), _("Stato"), _("Uscita"), _("Note")])
    for it in items:
        w.writerow([it.path, _(it.status), " | ".join(it.outputs), it.message])
    return buf.getvalue()
