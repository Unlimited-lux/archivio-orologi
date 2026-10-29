"""Scanner su Windows tramite WIA (Windows Image Acquisition), senza dipendenze Python in più.

WIA è incluso in Windows 10 e 11 e funziona con gli scanner che hanno un driver WIA (la maggior
parte, compresi quelli di rete con «Scanner di Windows»). Si usa l'oggetto COM
«WIA.DeviceManager» da PowerShell, avviato senza finestra: il programma riceve i parametri in
variabili d'ambiente (mai codice composto con i dati) e salva le pagine in PNG in una cartella
temporanea, una per foglio. Gli scanner con solo driver TWAIN non compaiono: si acquisisce con
il programma del produttore e si apre il file in Foliant.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

from . import platform

WIA_FORMAT_PNG = "{B96B3CAF-0728-11D3-9D7B-0000F81EF32E}"

# Proprietà WIA (wiadef.h): 3088 sorgente (1 alimentatore, 2 piano, 5 fronte/retro), 6146 tipo di
# immagine (1 colore, 2 grigi, 4 bianco e nero), 6147/6148 risoluzione orizzontale/verticale.
_LIST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$dm = New-Object -ComObject WIA.DeviceManager
foreach ($d in $dm.DeviceInfos) {
  if ($d.Type -eq 1) {
    $name = $d.Properties.Item('Name').Value
    [Console]::Out.WriteLine($d.DeviceID + [char]9 + $name)
  }
}
"""

_SCAN_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
function SetProp($props, $id, $value) {
  try { foreach ($p in $props) { if ($p.PropertyID -eq $id) { $p.Value = $value } } } catch { }
}
$dm = New-Object -ComObject WIA.DeviceManager
$info = $null
foreach ($d in $dm.DeviceInfos) { if ($d.DeviceID -eq $env:FOLIANT_WIA_DEVICE) { $info = $d } }
if ($info -eq $null) { [Console]::Error.WriteLine('NODEVICE'); exit 3 }
$dev = $info.Connect()
$source = [int]$env:FOLIANT_WIA_SOURCE
if ($source -ne 2) { SetProp $dev.Properties 3088 $source }
$item = $dev.Items.Item(1)
$dpi = [int]$env:FOLIANT_WIA_DPI
SetProp $item.Properties 6147 $dpi
SetProp $item.Properties 6148 $dpi
SetProp $item.Properties 6146 ([int]$env:FOLIANT_WIA_INTENT)
$n = 0
while ($true) {
  try {
    $img = $item.Transfer('""" + WIA_FORMAT_PNG + r"""')
  } catch {
    $code = $_.Exception.HResult
    if ($n -gt 0 -and $code -eq -2145320957) { break }   # WIA_ERROR_PAPER_EMPTY: fogli finiti
    [Console]::Error.WriteLine(('ERR {0:X8} ' -f $code) + $_.Exception.Message)
    exit 4
  }
  $n += 1
  $path = Join-Path $env:FOLIANT_WIA_OUT ('p{0:D4}.png' -f $n)
  if (Test-Path $path) { Remove-Item $path }
  $img.SaveFile($path)
  [Console]::Out.WriteLine('PAGE ' + $n)
  if ($source -eq 2) { break }
}
"""

# codici HRESULT di WIA → descrizione per l'utente (testi tradotti in `error_message`)
_PAPER_EMPTY = ("80210003",)
_BUSY = ("80210006", "80210007")          # occupato, in riscaldamento
_OFFLINE = ("80210005", "80210015")       # spento o scollegato, nessun dispositivo
_JAM = ("80210002",)
_COVER = ("80210016",)


def available() -> bool:
    """True su Windows con PowerShell (WIA è parte del sistema)."""
    return platform.IS_WINDOWS and _powershell() is not None


def _powershell() -> str | None:
    import shutil
    return shutil.which("powershell")


def _run(script: str, env: dict | None = None, timeout: float = 60,
         cancelled: Callable[[], bool] | None = None) -> tuple[int, str, str]:
    exe = _powershell()
    if exe is None:
        return 127, "", "powershell"
    import base64
    full_env = dict(os.environ, **(env or {}))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")    # niente codice composto dai dati
    proc = subprocess.Popen([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-EncodedCommand", encoded],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=full_env,
                            **platform.subprocess_kwargs(new_group=True))
    start = time.monotonic()
    while True:
        try:
            out, err = proc.communicate(timeout=0.3)
            return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            if (cancelled is not None and cancelled()) or time.monotonic() - start > timeout:
                platform.kill_tree(proc)
                proc.communicate()
                raise TimeoutError("cancelled" if cancelled is not None and cancelled() else "timeout")


def list_devices() -> list[tuple[str, str]]:
    """Scanner WIA collegati: (id, nome). Vuoto se non ce ne sono o WIA non risponde."""
    if not available():
        return []
    try:
        code, out, _err = _run(_LIST_SCRIPT, timeout=30)
    except TimeoutError:
        return []
    if code != 0:
        return []
    devices = []
    for line in out.splitlines():
        dev, sep, name = line.strip().partition("\t")
        if sep and dev:
            devices.append((dev, name.strip() or dev))
    return devices


def scan(device: str, out_dir: str, dpi: int = 300, mode: str = "color", source: str = "flatbed",
         cancelled: Callable[[], bool] | None = None, timeout: float = 900) -> list[str]:
    """Acquisisce in `out_dir` e restituisce i PNG delle pagine (in ordine).

    `mode`: «color», «gray» o «bw»; `source`: «flatbed», «adf» o «adf-duplex». Solleva
    `WiaError` con un messaggio per l'utente, `TimeoutError("cancelled")` se annullato."""
    intent = {"color": 1, "gray": 2, "bw": 4}.get(mode, 1)
    wia_source = {"flatbed": 2, "adf": 1, "adf-duplex": 5}.get(source, 2)
    env = {"FOLIANT_WIA_DEVICE": device, "FOLIANT_WIA_DPI": str(int(dpi)), "FOLIANT_WIA_INTENT": str(intent),
           "FOLIANT_WIA_SOURCE": str(wia_source), "FOLIANT_WIA_OUT": out_dir}
    code, _out, err = _run(_SCAN_SCRIPT, env=env, timeout=timeout, cancelled=cancelled)
    pages = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith("p") and f.endswith(".png"))
    if code != 0 and not pages:
        raise WiaError(error_message(err))
    return pages


class WiaError(Exception):
    """Errore dello scanner con messaggio per l'utente."""


def error_message(stderr: str) -> str:
    from ..i18n import _
    text = stderr.upper()
    if "NODEVICE" in text or any(c in text for c in _OFFLINE):
        return _("Lo scanner non risponde: controlla che sia acceso e collegato, poi riprova.")
    if any(c in text for c in _PAPER_EMPTY):
        return _("L'alimentatore è vuoto: inserisci i fogli e riprova.")
    if any(c in text for c in _JAM):
        return _("Carta inceppata nello scanner: liberala e riprova.")
    if any(c in text for c in _BUSY):
        return _("Lo scanner è occupato da un altro programma: chiudilo e riprova.")
    if any(c in text for c in _COVER):
        return _("Il coperchio dello scanner è aperto: chiudilo e riprova.")
    detail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
    return _("Acquisizione non riuscita: {e}").format(e=detail or _("nessuna risposta dallo scanner"))
