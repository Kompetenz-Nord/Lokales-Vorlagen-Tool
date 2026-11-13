<#
.SYNOPSIS
  Entwicklungs-/Startumgebung unter Windows einrichten (lokal, ohne Cloud).
.DESCRIPTION
  - legt eine virtuelle Python-Umgebung .venv an und installiert die Abhaengigkeiten
  - prueft Tesseract OCR (Deutsch). Installation nur mit -InstallTesseract (per winget).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1
  powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1 -InstallTesseract
#>
param([switch]$InstallTesseract)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $python = "py -3" } else { $python = "python" }
Write-Host "Python-Version:" -ForegroundColor Cyan
Invoke-Expression "$python --version"

if (-not (Test-Path ".venv")) {
    Write-Host "Lege virtuelle Umgebung .venv an ..." -ForegroundColor Cyan
    Invoke-Expression "$python -m venv .venv"
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# Tesseract pruefen
$tess = Get-Command tesseract -ErrorAction SilentlyContinue
if (-not $tess) {
    $default = "$env:ProgramFiles\Tesseract-OCR\tesseract.exe"
    if (Test-Path $default) { $tess = $default }
}
if ($tess) {
    Write-Host "Tesseract gefunden: $tess" -ForegroundColor Green
    & $tess --list-langs
} elseif ($InstallTesseract) {
    Write-Host "Installiere Tesseract (UB Mannheim) per winget ..." -ForegroundColor Cyan
    winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
    Write-Host "Bitte bei der Installation das Sprachpaket 'German' auswaehlen." -ForegroundColor Yellow
} else {
    Write-Host "Tesseract OCR nicht gefunden. Die App startet trotzdem (nur PDFs mit Textebene)." -ForegroundColor Yellow
    Write-Host "Installation: scripts\setup_windows.ps1 -InstallTesseract  (oder https://github.com/UB-Mannheim/tesseract/wiki)"
}

Write-Host ""
Write-Host "Fertig. Start der App:  .\.venv\Scripts\python.exe app.py" -ForegroundColor Green
Write-Host "Selbsttest:            .\.venv\Scripts\python.exe app.py --selftest"
