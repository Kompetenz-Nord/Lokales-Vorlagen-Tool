<#
.SYNOPSIS
  Baut die Windows-App mit PyInstaller (Ordner-Build) und erstellt ein ZIP.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#>
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Bitte zuerst scripts\setup_windows.ps1 ausfuehren." }
$py = ".\.venv\Scripts\python.exe"

& $py -m pytest -q -m "not slow" ; if ($LASTEXITCODE -ne 0) { throw "Tests fehlgeschlagen - Build abgebrochen." }
& $py -m PyInstaller --noconfirm --clean packaging\lokales_vorlagen_tool.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller fehlgeschlagen." }

$out = "dist\LokalesVorlagenTool"
& "$out\LokalesVorlagenTool.exe" --version
$zip = "dist\LokalesVorlagenTool-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $out -DestinationPath $zip
Write-Host "Build fertig: $out\LokalesVorlagenTool.exe" -ForegroundColor Green
Write-Host "Paket: $zip"
Write-Host "Hinweis: Tesseract OCR ist nicht enthalten und muss separat installiert werden."
