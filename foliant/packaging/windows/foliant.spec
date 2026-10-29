# Pacchetto di Foliant per Windows con PyInstaller (cartella unica).
#   pyinstaller packaging/windows/foliant.spec --distpath dist --workpath build/pyinstaller
# Produce dist/Foliant/ con Foliant.exe (grafico) e foliant-cli.exe (console), vedi launcher.py.
# L'icona viene da build/foliant.ico (packaging/windows/make_icon.py).
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))           # cartella foliant/ del repository
ICON = os.path.join(ROOT, "build", "foliant.ico")

hidden = (collect_submodules("foliant")                 # i moduli sono scoperti a runtime (pkgutil)
          + collect_submodules("pyhanko") + collect_submodules("pyhanko_certvalidator")
          + ["keyring.backends.Windows", "win32ctypes.core", "spellchecker"])
datas = collect_data_files("foliant")                   # icone, schemi XSD, JSON dei moduli
for package in ("spellchecker", "docx", "pptx", "pyhanko", "pyhanko_certvalidator", "certifi"):
    datas += collect_data_files(package)

a = Analysis(
    [os.path.join(SPECPATH, "launcher.py")],
    pathex=[ROOT],
    hiddenimports=hidden,
    datas=datas,
    # analisi del layout di PyMuPDF (onnxruntime) esclusa come nell'app (foliant/__init__.py)
    excludes=["tkinter", "onnxruntime", "pymupdf.layout", "pymupdf4llm", "pytest", "pytestqt"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Foliant", console=False,
          icon=ICON if os.path.exists(ICON) else None, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="foliant-cli", console=True,
          icon=ICON if os.path.exists(ICON) else None, upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="Foliant", upx=False)
