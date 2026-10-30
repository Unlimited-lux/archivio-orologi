# Pacchetto di Foliant per Windows con PyInstaller (cartella unica).
#   pyinstaller packaging/windows/foliant.spec --distpath dist --workpath build/pyinstaller
# Produce dist/Foliant/ con Foliant.exe (grafico) e foliant-cli.exe (console), vedi launcher.py.
# L'icona viene da build/foliant.ico (packaging/windows/make_icon.py).
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from PyInstaller.utils.win32 import versioninfo as vi

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))           # cartella foliant/ del repository
ICON = os.path.join(ROOT, "build", "foliant.ico")
sys.path.insert(0, ROOT)            # «foliant» importabile anche dalle funzioni di raccolta di PyInstaller


def foliant_data_files():
    """Tutti i file non Python del pacchetto foliant (icone, schemi XSD, JSON dei moduli…), presi
    dalla cartella dei sorgenti: collect_data_files non li trova se il pacchetto non è installato."""
    datas = []
    package = os.path.join(ROOT, "foliant")
    for folder, dirs, files in os.walk(package):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith((".py", ".pyc", ".md")):          # note per chi sviluppa escluse
                datas.append((os.path.join(folder, name), os.path.relpath(folder, ROOT)))
    return datas


hidden = (collect_submodules("foliant")                 # i moduli sono scoperti a runtime (pkgutil)
          + collect_submodules("pyhanko") + collect_submodules("pyhanko_certvalidator")
          + ["keyring.backends.Windows", "win32ctypes.core", "spellchecker"])
datas = foliant_data_files()                            # icone, schemi XSD, JSON dei moduli
# docx e pptx: modelli di Word e PowerPoint (default.docx, intestazioni, commenti, note…);
# openpyxl, pdf2docx e lxml non hanno file di dati (controllo in check_bundle.py)
for package in ("spellchecker", "docx", "pptx", "pyhanko", "pyhanko_certvalidator", "certifi"):
    datas += collect_data_files(package)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    hiddenimports=hidden,
    datas=datas,
    # analisi del layout di PyMuPDF (onnxruntime) esclusa come nell'app (foliant/__init__.py)
    excludes=["tkinter", "onnxruntime", "pymupdf.layout", "pymupdf4llm", "pytest", "pytestqt"],
    # python-docx e python-pptx trovano i modelli con percorsi come docx/parts/../templates/… o
    # pptx/oxml/../templates/…: le cartelle dei loro moduli devono esistere su disco, quindi i
    # sorgenti vanno anche in cartella. Windows risolve «..» pure se la cartella manca, Linux no:
    # così il pacchetto non dipende da questo e lo si può provare anche su Linux.
    module_collection_mode={"docx": "pyz+py", "pptx": "pyz+py"},
    noarchive=False,
)
pyz = PYZ(a.pure)


def version_info(description: str, filename: str):
    """Metadati di versione dell'eseguibile (Proprietà › Dettagli in Esplora file)."""
    with open(os.path.join(ROOT, "foliant", "__init__.py"), encoding="utf-8") as fh:
        version = next(line.split('"')[1] for line in fh if line.startswith("__version__"))
    numbers = tuple(int("".join(c for c in n if c.isdigit()) or 0) for n in version.split("+")[0].split("."))
    numbers = (numbers + (0, 0, 0, 0))[:4]
    strings = [vi.StringStruct(k, v) for k, v in (
        ("CompanyName", "Foliant"), ("FileDescription", description), ("FileVersion", version),
        ("InternalName", filename), ("LegalCopyright", "AGPL-3.0"), ("OriginalFilename", filename + ".exe"),
        ("ProductName", "Foliant"), ("ProductVersion", version))]
    return vi.VSVersionInfo(
        ffi=vi.FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[vi.StringFileInfo([vi.StringTable("041004B0", strings)]),     # italiano, Unicode
              vi.VarFileInfo([vi.VarStruct("Translation", [0x0410, 1200])])])


gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Foliant", console=False,
          icon=ICON if os.path.exists(ICON) else None, upx=False,
          version=version_info("Foliant - editor PDF", "Foliant"))
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="foliant-cli", console=True,
          icon=ICON if os.path.exists(ICON) else None, upx=False,
          version=version_info("Foliant - riga di comando", "foliant-cli"))
coll = COLLECT(gui, cli, a.binaries, a.datas, name="Foliant", upx=False)
