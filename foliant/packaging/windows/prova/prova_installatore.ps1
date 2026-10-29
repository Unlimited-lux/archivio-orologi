# Prova dell'installatore di Foliant su una macchina Windows pulita (GitHub Actions, windows-latest).
#   ./packaging/windows/prova/prova_installatore.ps1 -Setup out\Foliant-Setup-0.1.0.exe -Versione 0.1.0 -Lavoro $env:RUNNER_TEMP\prova
# Prerequisito: un Python con pymupdf e Pillow (per creare il PDF di prova e cercare le tracce).
#  1. installazione silenziosa per l'utente corrente, con collegamento sul desktop e lettore PDF;
#  2. file, collegamenti, tipo di file e voce di disinstallazione; foliant-cli.exe --version;
#  3. sessione senza schermo di Foliant.exe: apre il PDF, lo modifica, salva, chiude (sessione.py);
#  4. foliant-cli blocco: OCR in italiano con il Tesseract incluso → DOCX; OCR → compressione →
#     PDF/A con il Ghostscript incluso; compressione diretta con Ghostscript; controllo dei risultati;
#  5. riservatezza: nomi e percorsi dei file di prova assenti da profilo, registro ed elementi recenti;
#  6. disinstallazione senza togliere le preferenze (devono restare), reinstallazione e
#     disinstallazione con /RIMUOVIPREFERENZE=1 (devono sparire); file e voci del programma spariti.
param(
  [Parameter(Mandatory = $true)][string]$Setup,
  [Parameter(Mandatory = $true)][string]$Versione,
  [Parameter(Mandatory = $true)][string]$Lavoro
)
$ErrorActionPreference = 'Stop'
$Setup = (Resolve-Path $Setup).Path
$prova = $PSScriptRoot
New-Item -ItemType Directory -Force -Path $Lavoro | Out-Null
$Lavoro = (Resolve-Path $Lavoro).Path

$app = Join-Path $env:LOCALAPPDATA 'Programs\Foliant'          # {autopf} per l'utente corrente
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Foliant.lnk'
$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Foliant.lnk'
$uninstKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6F2B1C7E-8A3D-4E59-9C1B-3D7A5E2F0B41}_is1'

function Passo([string]$testo) { Write-Host "`n=== $testo" }
function Verifica([bool]$condizione, [string]$messaggio) {
  if (-not $condizione) { throw "ERRORE: $messaggio" }
  Write-Host "ok      $messaggio"
}
function Esegui([string]$exe, [string[]]$argomenti) {
  & $exe @argomenti
  if ($LASTEXITCODE -ne 0) { throw "$([IO.Path]::GetFileName($exe)) uscito con $LASTEXITCODE" }
}
function Installa {
  $p = Start-Process $Setup -PassThru -Wait -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
    '/CURRENTUSER', '/TASKS="desktopicon,defaultpdf"'
  Verifica ($p.ExitCode -eq 0) "installazione silenziosa (codice $($p.ExitCode))"
}
function Disinstalla([string[]]$extra) {
  $p = Start-Process (Join-Path $app 'unins000.exe') -PassThru -Wait -ArgumentList (@('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') + $extra)
  # il programma di disinstallazione si rilancia da una copia temporanea: si aspetta anche quella
  for ($i = 0; $i -lt 120 -and (Get-Process | Where-Object { $_.Name -like '_iu*' -or $_.Name -like 'unins*' }); $i++) {
    Start-Sleep -Seconds 1
  }
  Start-Sleep -Seconds 2
  Verifica ($p.ExitCode -eq 0) "disinstallazione silenziosa (codice $($p.ExitCode))"
}
function Destinazione-Collegamento([string]$lnk) {
  (New-Object -ComObject WScript.Shell).CreateShortcut($lnk).TargetPath
}

# ------------------------------------------------------------------ preparazione
Passo 'PDF di prova (pagina scansionata in italiano)'
# nomi riconoscibili, da cercare poi nel profilo; i file stanno fuori dal profilo dell'utente
$segreto = 'Riservato' + (Get-Random -Minimum 100000 -Maximum 999999)
$cartella = Join-Path $Lavoro "Cartella$segreto"
New-Item -ItemType Directory -Force -Path $cartella | Out-Null
$risultati = Join-Path $Lavoro 'risultati'
New-Item -ItemType Directory -Force -Path $risultati | Out-Null
$originale = Join-Path $risultati 'originale.pdf'
Esegui python @("$prova\crea_pdf_scansionato.py", $originale)
$pdfSessione = Join-Path $cartella "Perizia$segreto.pdf"
$pdfBlocco = Join-Path $cartella "Orologio$segreto.pdf"
Copy-Item $originale $pdfSessione
Copy-Item $originale $pdfBlocco
$inizio = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
Verifica (-not (Test-Path 'HKCU:\Software\Foliant')) 'nessuna preferenza di Foliant prima della prova'

# ------------------------------------------------------------------ installazione
Passo 'Installazione silenziosa'
Installa
foreach ($f in 'Foliant.exe', 'foliant-cli.exe', 'unins000.exe', 'tools\tesseract\tesseract.exe',
               'tools\tesseract\tessdata\ita.traineddata', 'tools\ghostscript\bin\gswin64c.exe',
               'tools\qpdf\bin\qpdf.exe', 'licenze\AGPL-3.0.txt', 'licenze\Tesseract-Apache-2.0.txt',
               'licenze\Ghostscript-AGPL-3.0.txt', 'licenze\qpdf-Apache-2.0.txt') {
  Verifica (Test-Path (Join-Path $app $f)) "installato: $f"
}
Verifica (Test-Path $startMenu) 'collegamento nel menu Start'
Verifica ((Destinazione-Collegamento $startMenu) -eq (Join-Path $app 'Foliant.exe')) 'il menu Start avvia Foliant.exe'
Verifica (Test-Path $desktop) 'collegamento sul desktop (attività facoltativa scelta)'
Verifica ((Destinazione-Collegamento $desktop) -eq (Join-Path $app 'Foliant.exe')) 'il desktop avvia Foliant.exe'
$cmd = (Get-ItemProperty 'HKCU:\Software\Classes\Foliant.PDF\shell\open\command').'(default)'
Verifica ($cmd -like "*$app\Foliant.exe*") 'tipo di file Foliant.PDF registrato'
Verifica ($null -ne (Get-ItemProperty 'HKCU:\Software\Classes\Foliant.PDF').NoRecentDocs) 'Foliant.PDF con NoRecentDocs'
Verifica ((Get-ItemProperty 'HKCU:\Software\Classes\.pdf').'(default)' -eq 'Foliant.PDF') 'lettore PDF predefinito (attività scelta)'
$voce = Get-ItemProperty $uninstKey
Verifica ($voce.DisplayVersion -eq $Versione) "versione $Versione nella voce di disinstallazione"
$info = (Get-Item (Join-Path $app 'Foliant.exe')).VersionInfo
Write-Host "        Foliant.exe: $($info.FileVersion) $($info.ProductName)"

Passo 'foliant-cli.exe --version'
$out = & (Join-Path $app 'foliant-cli.exe') --version
Write-Host "        $out"
Verifica ($LASTEXITCODE -eq 0 -and "$out" -like "*$Versione*") 'foliant-cli.exe --version'

# ------------------------------------------------------------------ uso senza schermo
Passo 'Sessione di Foliant.exe senza schermo: apre, modifica, salva, chiude'
$rapporto = Join-Path $Lavoro 'sessione.json'
$env:QT_QPA_PLATFORM = 'offscreen'
$codice = "exec(open(r'$prova\sessione.py',encoding='utf-8').read())"
$p = Start-Process (Join-Path $app 'Foliant.exe') -PassThru -Wait `
  -ArgumentList '-I', '-c', "`"$codice`"", "`"$pdfSessione`"", "`"$rapporto`""
Verifica (Test-Path $rapporto) "rapporto della sessione scritto (codice $($p.ExitCode))"
$r = Get-Content $rapporto -Raw -Encoding utf8 | ConvertFrom-Json
Write-Host "        passi: $($r.steps -join ', ')"
if ($r.errors) { $r.errors | ForEach-Object { Write-Host $_ } }
Verifica (-not $r.errors -and ($r.steps -contains 'chiuso')) 'sessione completata senza errori'
Copy-Item $pdfSessione (Join-Path $risultati 'sessione.pdf')

Passo 'foliant-cli blocco: OCR in italiano → Word'
$cli = Join-Path $app 'foliant-cli.exe'
Esegui $cli @('blocco', '-o', 'ocr', '-o', 'convert', '-p', '1:languages=ita', '-p', '2:format=docx',
              '-d', "$risultati\docx", $pdfBlocco)
Passo 'foliant-cli blocco: OCR in italiano → compressione → PDF/A (Ghostscript)'
Esegui $cli @('blocco', '-o', 'ocr', '-o', 'compress', '-o', 'pdfa', '-p', '1:languages=ita', '-p', '2:level=high',
              '-d', "$risultati\pdf", $pdfBlocco)
Passo 'Compressione con il Ghostscript incluso'
Esegui (Join-Path $app 'tools\ghostscript\bin\gswin64c.exe') @('-q', '-dNOPAUSE', '-dBATCH', '-dSAFER',
  '-sDEVICE=pdfwrite', '-dPDFSETTINGS=/ebook', "-sOutputFile=$risultati\gs-ebook.pdf", $pdfBlocco)
Remove-Item Env:QT_QPA_PLATFORM

Passo 'Controllo dei risultati (con il Foliant installato)'
Esegui $cli @('-I', '-c', "exec(open(r'$prova\verifica_risultati.py',encoding='utf-8').read())", $risultati)

# ------------------------------------------------------------------ riservatezza
Passo 'Riservatezza: nessun nome o percorso dei file di prova'
Verifica (Test-Path 'HKCU:\Software\Foliant') 'Foliant ha scritto le sue preferenze (HKCU\Software\Foliant)'
Esegui python @("$prova\cerca_tracce.py", '--dal', $inizio, '--token', $segreto,
                '--token', "Perizia$segreto", '--token', "Orologio$segreto", '--token', "Cartella$segreto",
                '--percorso', $pdfSessione, '--percorso', $pdfBlocco, '--percorso', $cartella)

# ------------------------------------------------------------------ disinstallazione
Passo 'Disinstallazione senza togliere le preferenze'
Disinstalla @()
Verifica (-not (Test-Path $app)) 'cartella del programma rimossa'
Verifica (-not (Test-Path $startMenu)) 'collegamento nel menu Start rimosso'
Verifica (-not (Test-Path $desktop)) 'collegamento sul desktop rimosso'
Verifica (-not (Test-Path 'HKCU:\Software\Classes\Foliant.PDF')) 'tipo di file Foliant.PDF rimosso'
Verifica (-not (Test-Path 'HKCU:\Software\Classes\Applications\Foliant.exe')) 'applicazione registrata rimossa'
Verifica ((Get-ItemProperty 'HKCU:\Software\Classes\.pdf' -ErrorAction SilentlyContinue).'(default)' -ne 'Foliant.PDF') 'Foliant non è più il lettore PDF'
Verifica (-not (Test-Path $uninstKey)) 'voce di disinstallazione rimossa'
Verifica (Test-Path 'HKCU:\Software\Foliant') 'preferenze conservate (non richiesto di toglierle)'

Passo 'Reinstallazione e disinstallazione con le preferenze'
Installa
Verifica (Test-Path (Join-Path $app 'Foliant.exe')) 'reinstallato'
Disinstalla @('/RIMUOVIPREFERENZE=1')
Verifica (-not (Test-Path $app)) 'cartella del programma rimossa'
Verifica (-not (Test-Path 'HKCU:\Software\Foliant')) 'preferenze HKCU\Software\Foliant rimosse'
Verifica (-not (Test-Path (Join-Path $env:APPDATA 'foliant'))) 'dati di Foliant in %APPDATA% rimossi'
Verifica (-not (Test-Path $uninstKey)) 'voce di disinstallazione rimossa'
Write-Host "`nProva dell'installatore: tutto ok"
