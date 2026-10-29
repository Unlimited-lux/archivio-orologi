"""Riservatezza: cerca nomi e percorsi dei file di prova nel profilo Windows dopo l'uso di Foliant.

Uso (Python della macchina di prova, non il Foliant installato):
    python cerca_tracce.py --dal 1730000000 --token NOME [--token CARTELLA …] --percorso FILE […]

Dove si cerca:
- %APPDATA% e %LOCALAPPDATA%: nomi e contenuto dei file creati o modificati dall'istante `--dal`
  (il resto del profilo della macchina di prova non è di Foliant e non cambia);
- elementi recenti di Windows (%APPDATA%\\Microsoft\\Windows\\Recent, con le Jump List): nomi e
  contenuto di tutti i file;
- registro: HKCU\\Software\\Foliant, HKCU\\Software\\QtProject e le cronologie della shell
  (RecentDocs, ComDlg32), esportati con nomi e valori.
Ogni token e percorso si cerca senza distinguere maiuscole e minuscole, in UTF-8 e UTF-16-LE,
con le barre \\ e /, e come URI file:///. Esce con 1 se trova qualcosa (senza stampare il valore
trovato oltre il nome del token).
"""

import argparse
import os
import sys
import time
from pathlib import Path

REGISTRO = (r"Software\Foliant", r"Software\QtProject",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs",
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32")


def needles(tokens, paths):
    forms = set(tokens)
    for p in paths:
        p = str(Path(p).resolve())
        forms |= {p, p.replace("\\", "/"), Path(p).as_uri(), Path(p).as_uri().replace("%20", " ")}
    out = {}
    for form in forms:
        for enc in ("utf-8", "utf-16-le"):
            out[form.lower().encode(enc)] = form if form in tokens else "percorso di prova"
    return out


def search(data: bytes, found_in: str, nd, hits):
    low = data.lower()
    for needle, label in nd.items():
        if needle in low:
            hits.append(f"{found_in}: {label}")


def scan_dir(root: Path, since: float, nd, hits, every_file=False):
    count = 0
    for folder, _dirs, files in os.walk(root, onerror=lambda e: None):
        for name in files:
            full = os.path.join(folder, name)
            search(full.encode("utf-8"), f"nome del file {full}", nd, hits)
            search(full.encode("utf-16-le"), f"nome del file {full}", nd, hits)
            try:
                st = os.stat(full)
                if not every_file and max(st.st_mtime, st.st_ctime) < since:
                    continue
                if st.st_size > 200 * 1024 * 1024:
                    continue
                with open(full, "rb") as fh:
                    search(fh.read(), f"contenuto di {full}", nd, hits)
                count += 1
            except OSError:
                continue
    return count


def registry_dump(path):
    import winreg
    chunks = []

    def walk(sub):
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
        except OSError:
            return
        with key:
            chunks.append(sub.encode("utf-8"))
            i = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                chunks.append(name.encode("utf-8"))
                if isinstance(value, bytes):
                    chunks.append(value)
                elif isinstance(value, list):
                    chunks.append("\x00".join(map(str, value)).encode("utf-8"))
                else:
                    chunks.append(str(value).encode("utf-8"))
                i += 1
            subs, i = [], 0
            while True:
                try:
                    subs.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
        for s in subs:
            walk(sub + "\\" + s)

    walk(path)
    return b"\n".join(chunks), len(chunks)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dal", type=float, required=True, help="istante di inizio (secondi epoch)")
    ap.add_argument("--token", action="append", default=[])
    ap.add_argument("--percorso", action="append", default=[])
    args = ap.parse_args(argv)
    nd = needles(args.token, args.percorso)
    hits = []
    since = args.dal - 2
    appdata, localappdata = Path(os.environ["APPDATA"]), Path(os.environ["LOCALAPPDATA"])
    recent = appdata / "Microsoft" / "Windows" / "Recent"
    for root in (appdata, localappdata):
        n = scan_dir(root, since, nd, hits)
        print(f"{root}: {n} file nuovi o modificati controllati")
    n = scan_dir(recent, since, nd, hits, every_file=True)
    print(f"{recent}: {n} file controllati")
    for key in REGISTRO:
        data, n = registry_dump(key)
        search(data, f"registro HKCU\\{key}", nd, hits)
        print(f"HKCU\\{key}: {n} voci controllate")
    if hits:
        print("TRACCE TROVATE:")
        for h in sorted(set(hits)):
            print("  " + h)
        return 1
    print(f"riservatezza: nessuna traccia ({len(args.token)} nomi, {len(args.percorso)} percorsi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
