# Raccoglie gli strumenti esterni da includere nel pacchetto di Foliant per Windows.
#   ./packaging/windows/collect_tools.ps1 -Destinazione build/tools -Licenze build/licenze   (da PowerShell)
# Prerequisiti (sulla macchina di costruzione): Tesseract (UB Mannheim), Ghostscript e qpdf
# installati, ad esempio con «choco install tesseract ghostscript qpdf».
#   tools/tesseract/    tesseract.exe, librerie, tessdata con ita, eng, osd     (Apache-2.0)
#   tools/ghostscript/  bin (gswin64c.exe, gsdll64.dll), lib, iccprofiles        (AGPL-3.0)
#   tools/qpdf/         bin (qpdf.exe e librerie)                                (Apache-2.0)
param(
  [Parameter(Mandatory = $true)][string]$Destinazione,
  [Parameter(Mandatory = $true)][string]$Licenze
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Destinazione, $Licenze | Out-Null
# percorsi assoluti: «& build/tools\…\tesseract.exe» sarebbe letto come il comando di un modulo «build»
$Destinazione = (Resolve-Path $Destinazione).Path
$Licenze = (Resolve-Path $Licenze).Path

function First-Dir([string[]]$patterns) {
  foreach ($p in $patterns) {
    $found = Get-Item -Path $p -ErrorAction SilentlyContinue | Where-Object { $_.PSIsContainer } | Sort-Object Name -Descending | Select-Object -First 1
    if ($found) { return $found.FullName }
  }
  throw "Cartella non trovata: $($patterns -join ', ')"
}

function Fetch([string]$url, [string]$target) {
  for ($i = 1; $i -le 4; $i++) {
    try { Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing; return }
    catch { if ($i -eq 4) { throw }; Start-Sleep -Seconds (2 * $i) }
  }
}

# --- Tesseract: programma e librerie, solo le lingue italiana, inglese e rilevamento orientamento
$tessSrc = First-Dir @("$env:ProgramFiles\Tesseract-OCR")
$tess = Join-Path $Destinazione 'tesseract'
New-Item -ItemType Directory -Force -Path "$tess\tessdata" | Out-Null
Copy-Item "$tessSrc\*.exe", "$tessSrc\*.dll" -Destination $tess
foreach ($sub in 'configs', 'tessconfigs') {
  if (Test-Path "$tessSrc\tessdata\$sub") { Copy-Item "$tessSrc\tessdata\$sub" -Destination "$tess\tessdata" -Recurse }
}
foreach ($lang in 'ita', 'eng', 'osd') {
  # stessi modelli dei pacchetti di Ubuntu (tessdata «standard», LSTM e motore classico)
  Fetch "https://github.com/tesseract-ocr/tessdata/raw/main/$lang.traineddata" "$tess\tessdata\$lang.traineddata"
}
Fetch 'https://raw.githubusercontent.com/tesseract-ocr/tesseract/main/LICENSE' "$Licenze\Tesseract-Apache-2.0.txt"
& "$tess\tesseract.exe" --version
& "$tess\tesseract.exe" --tessdata-dir "$tess\tessdata" --list-langs

# --- Ghostscript
$gsSrc = First-Dir @("$env:ProgramFiles\gs\gs*")
$gs = Join-Path $Destinazione 'ghostscript'
New-Item -ItemType Directory -Force -Path $gs | Out-Null
foreach ($sub in 'bin', 'lib', 'iccprofiles', 'Resource') {
  if (Test-Path "$gsSrc\$sub") { Copy-Item "$gsSrc\$sub" -Destination $gs -Recurse }
}
Remove-Item "$gs\bin\gswin64.exe" -ErrorAction SilentlyContinue        # versione con finestra: non serve
$gsLicense = Get-ChildItem $gsSrc -Recurse -Include 'COPYING', 'LICENSE' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($gsLicense) { Copy-Item $gsLicense.FullName "$Licenze\Ghostscript-AGPL-3.0.txt" }
else { Fetch 'https://www.gnu.org/licenses/agpl-3.0.txt' "$Licenze\Ghostscript-AGPL-3.0.txt" }
& "$gs\bin\gswin64c.exe" --version

# --- qpdf
$qpdfExe = Get-ChildItem "$env:ProgramFiles\qpdf*", "${env:ProgramFiles(x86)}\qpdf*", "$env:ChocolateyInstall\lib\qpdf" `
  -Recurse -Filter qpdf.exe -ErrorAction SilentlyContinue | Where-Object { $_.Directory.Name -eq 'bin' } | Select-Object -First 1
if (-not $qpdfExe) { throw "qpdf.exe non trovato (Program Files o Chocolatey)" }
$qpdfSrc = $qpdfExe.Directory.Parent.FullName
$qpdf = Join-Path $Destinazione 'qpdf'
New-Item -ItemType Directory -Force -Path $qpdf | Out-Null
Copy-Item "$qpdfSrc\bin" -Destination $qpdf -Recurse
Fetch 'https://raw.githubusercontent.com/qpdf/qpdf/main/LICENSE.txt' "$Licenze\qpdf-Apache-2.0.txt"
& "$qpdf\bin\qpdf.exe" --version

# --- licenza di Foliant e di PyMuPDF
Fetch 'https://www.gnu.org/licenses/agpl-3.0.txt' "$Licenze\AGPL-3.0.txt"
Write-Host "Strumenti raccolti in $Destinazione"
Get-ChildItem $Destinazione -Recurse | Measure-Object -Property Length -Sum | ForEach-Object { "{0:N0} MB" -f ($_.Sum / 1MB) }
