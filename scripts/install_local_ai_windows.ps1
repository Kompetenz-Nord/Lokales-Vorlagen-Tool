<#
.SYNOPSIS
  Lokale KI (Ollama) fuer das Lokale Vorlagen-Tool unter Windows einrichten.

.DESCRIPTION
  Prueft den Rechner (Arbeitsspeicher, Grafikkarte, Speicherplatz), schlaegt ein Profil vor,
  prueft Ollama, bietet den Nur-lokal-Modus von Ollama an und laedt die Modelle des Profils
  nach Rueckfrage (je Modell) ueber den lokalen Ollama-Dienst herunter.

  Profile (Quelle: src/insurance_app/local_ai/profiles.py):
    kompakt  = glm-ocr
    standard = glm-ocr + qwen3-vl:4b          (empfohlen)
    maximal  = glm-ocr + qwen3-vl:4b + gemma4:e4b

  Grundsaetze:
    * Nichts geschieht ohne Rueckfrage (j/N). Standardantwort ist immer "Nein".
    * Nur lokale Modelle. Keine Anmeldung bei Ollama, kein API-Schluessel, keine Cloud-Modelle.
    * Das Skript spricht Ollama nur ueber 127.0.0.1 an (ohne Proxy).
    * Mehrfach ausfuehrbar: Vorhandenes (Ollama, Modelle, server.json) bleibt unveraendert.

  Rueckgabewert: 0 = in Ordnung (auch: bewusst uebersprungen), 1 = Fehler.

  Die Datei enthaelt bewusst nur ASCII-Zeichen (Umlaute als ae/oe/ue), damit Windows PowerShell 5.1
  sie ohne Kodierungsprobleme liest.

.PARAMETER KiProfil
  kompakt, standard oder maximal. Kurzform: -Profile. Ohne Angabe schlaegt das Skript ein Profil vor.

.PARAMETER InstallOllama
  Fehlt Ollama: nach Rueckfrage mit "winget install --id Ollama.Ollama -e" installieren.

.PARAMETER DryRun
  Testlauf: nur pruefen und anzeigen. Keine Installation, kein Download, keine Datei wird
  geschrieben, Ollama wird nicht gestartet.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_local_ai_windows.ps1 -DryRun
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_local_ai_windows.ps1 -Profile standard
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install_local_ai_windows.ps1 -Profile standard -InstallOllama
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [Alias('Profile')]
    [ValidateSet('kompakt', 'standard', 'maximal')]
    [string]$KiProfil = '',
    [switch]$InstallOllama,
    [switch]$DryRun
)

# Fehler von Windows-Programmen (ollama, winget) pruefen wir ueber $LASTEXITCODE.
$ErrorActionPreference = 'Continue'

$OllamaUrl = 'http://127.0.0.1:11434'
$MinOllamaVersion = '0.30.0'   # laut "ollama show glm-ocr" / "qwen3-vl:4b" (Feld "requires"), 13.11.2026
$UserHome = $env:USERPROFILE
if (-not $UserHome) { $UserHome = $HOME }
$LocalAppData = $env:LOCALAPPDATA
if (-not $LocalAppData) { $LocalAppData = Join-Path $UserHome 'AppData\Local' }
$ServerJson = Join-Path $UserHome '.ollama\server.json'
$ServerLog = Join-Path $LocalAppData 'Ollama\server.log'
if ($env:OLLAMA_MODELS) { $ModelsDir = $env:OLLAMA_MODELS } else { $ModelsDir = Join-Path $UserHome '.ollama\models' }

$script:Warnings = 0
$script:Failures = 0
$script:Skipped = 0
$script:OllamaExe = $null

# ---------------------------------------------------------------------------
# Profile und Modelle (Richtwerte aus profiles.py / status.py, nicht auf Zielgeraeten gemessen)
# ---------------------------------------------------------------------------
$Profiles = @{
    'kompakt'  = @{
        Models  = @('glm-ocr')
        MinRam  = 8
        RecRam  = 16
        Disk    = 4
        MinVram = 0
        Windows = '64-Bit-Windows mit mindestens 8 GB RAM; Grafikkarte nicht erforderlich'
    }
    'standard' = @{
        Models  = @('glm-ocr', 'qwen3-vl:4b')
        MinRam  = 16
        RecRam  = 16
        Disk    = 8
        MinVram = 6
        Windows = '16 GB RAM; empfohlen: NVIDIA-Grafikkarte mit mindestens 6-8 GB Grafikspeicher (ohne GPU deutlich langsamer)'
    }
    'maximal'  = @{
        Models  = @('glm-ocr', 'qwen3-vl:4b', 'gemma4:e4b')
        MinRam  = 16
        RecRam  = 32
        Disk    = 16
        MinVram = 12
        Windows = '32 GB RAM und NVIDIA-Grafikkarte mit mindestens 12 GB Grafikspeicher empfohlen'
    }
}

# Downloadgroesse laut Ollama-Registry-Manifest (abgerufen am 13.11.2026), 1 GB = 10^9 Byte
$ModelInfo = @{
    'glm-ocr'     = @{
        Size    = '2,66 GB'
        Role    = 'Aktives Modell: liest schwierige Scans, Fotos und Tabellen als Text'
        License = 'keine Lizenzangabe im Ollama-Manifest - beim Anbieter Z.ai pruefen'
    }
    'qwen3-vl:4b' = @{
        Size    = '3,34 GB'
        Role    = 'Ausweichmodell: liest Felder direkt aus dem Seitenbild (mit woertlichem Zitat)'
        License = 'Apache 2.0 (laut Ollama-Manifest)'
    }
    'gemma4:e4b'  = @{
        Size    = '6,58 GB'
        Role    = 'Gegenpruefung: unabhaengige zweite Meinung, Abweichung = Konflikt'
        License = 'Apache 2.0 (laut Ollama-Manifest)'
    }
}

# ---------------------------------------------------------------------------
# Ausgabe (Symbol + Text) und Rueckfragen
# ---------------------------------------------------------------------------
function Write-Step([string]$Text) { Write-Host ''; Write-Host "== $Text ==" -ForegroundColor Cyan }
function Write-Ok([string]$Text) { Write-Host "  [OK] $Text" -ForegroundColor Green }
function Write-Info([string]$Text) { Write-Host "  [i]  $Text" }
function Write-Warn([string]$Text) { $script:Warnings++; Write-Host "  [!]  $Text" -ForegroundColor Yellow }
function Write-Fail([string]$Text) { $script:Failures++; Write-Host "  [X]  $Text" -ForegroundColor Red }

function Confirm-Step([string]$Question) {
    if ($DryRun) { Write-Info "Testlauf - Frage uebersprungen: $Question"; return $false }
    try { $answer = Read-Host "  ? $Question [j/N]" } catch { return $false }
    return ($answer -match '^\s*(j|ja|y|yes)\s*$')
}

function Format-De([double]$Value) { return ('{0:0.0}' -f $Value).Replace('.', ',') }

# Ollama-Programm finden (PATH oder uebliche Installationsorte)
function Find-Ollama {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) {
        if ($cmd.Path) { return $cmd.Path }
        return $cmd.Definition
    }
    $candidates = @()
    $candidates += (Join-Path $LocalAppData 'Programs\Ollama\ollama.exe')
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles 'Ollama\ollama.exe') }
    foreach ($p in $candidates) { if (Test-Path -LiteralPath $p) { return $p } }
    return $null
}

# Programmaufruf ohne Ausgabe; liefert den Rueckgabewert
function Invoke-Quiet([string]$Exe, [string[]]$Arguments) {
    try { & $Exe @Arguments *> $null } catch { return 1 }
    return $LASTEXITCODE
}

# Ollama-Version ueber 127.0.0.1 abfragen (ohne Proxy). $null = nicht erreichbar.
function Get-OllamaServerVersion {
    try {
        $request = [System.Net.WebRequest]::Create("$OllamaUrl/api/version")
        $request.Proxy = $null
        $request.Timeout = 3000
        $response = $request.GetResponse()
        try {
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            $body = $reader.ReadToEnd()
            $reader.Close()
        } finally {
            $response.Close()
        }
        if ($body -match '"version"\s*:\s*"([^"]+)"') { return $Matches[1] }
        return 'unbekannt'
    } catch {
        return $null
    }
}

function Wait-OllamaServer([int]$Seconds, [bool]$Up) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        $running = [bool](Get-OllamaServerVersion)
        if ($running -eq $Up) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Start-OllamaServer {
    $trayApp = Join-Path $LocalAppData 'Programs\Ollama\ollama app.exe'
    if (Test-Path -LiteralPath $trayApp) {
        Start-Process -FilePath $trayApp | Out-Null
        Write-Info 'Ollama-App gestartet - warte bis zu 30 Sekunden ...'
    } elseif ($script:OllamaExe) {
        Start-Process -FilePath $script:OllamaExe -ArgumentList 'serve' -WindowStyle Minimized | Out-Null
        Write-Info 'Ollama-Dienst in einem eigenen (minimierten) Fenster gestartet - warte bis zu 30 Sekunden ...'
    } else {
        return $false
    }
    return (Wait-OllamaServer -Seconds 30 -Up $true)
}

function Test-VersionLower([string]$Current, [string]$Minimum) {
    try {
        $clean = ($Current -replace '[^0-9.].*$', '')
        return ([version]$clean -lt [version]$Minimum)
    } catch {
        return $false
    }
}

# NVIDIA-Treiberversion aus der Windows-Treiberversion ableiten (z. B. 31.0.15.5161 -> 551.61)
function Convert-NvidiaWmiVersion([string]$WmiVersion) {
    if (-not $WmiVersion) { return $null }
    $parts = $WmiVersion.Split('.')
    if ($parts.Count -lt 4) { return $null }
    $digits = $parts[2] + $parts[3]
    if ($digits.Length -lt 5) { return $null }
    $d = $digits.Substring($digits.Length - 5)
    return ($d.Substring(0, 3) + '.' + $d.Substring(3))
}

# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
Write-Host 'Lokale KI fuer das Lokale Vorlagen-Tool einrichten (Windows)'
Write-Host 'Alles bleibt auf diesem Rechner. Keine Anmeldung, kein API-Schluessel, keine Cloud-Modelle.'
if ($DryRun) {
    Write-Host 'TESTLAUF (-DryRun): Es wird nichts installiert, heruntergeladen, gestartet oder geschrieben.' -ForegroundColor Yellow
}
if ($env:OS -ne 'Windows_NT') {
    Write-Host '  [!]  Kein Windows erkannt - Windows-spezifische Pruefungen koennen fehlschlagen.' -ForegroundColor Yellow
}

# ---------------------------------------------------------------------------
# 1. Rechner pruefen
# ---------------------------------------------------------------------------
Write-Step '1. Rechner pruefen'

$ramGb = $null
$usableGb = $null
try {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    Write-Info ("System: {0} (Build {1}, {2})" -f $os.Caption, $os.BuildNumber, $env:PROCESSOR_ARCHITECTURE)
    if ([int]$os.BuildNumber -lt 19045) {
        Write-Warn 'Ollama setzt laut Dokumentation Windows 10 22H2 oder neuer voraus.'
    }
} catch {
    Write-Warn 'Windows-Version konnte nicht ermittelt werden.'
}
if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Warn 'Kein 64-Bit-Windows. Die lokale KI setzt 64-Bit-Windows voraus.'
}
if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    Write-Info 'Windows auf ARM: Mit dieser App nicht getestet.'
}

try {
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    $usableGb = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
} catch {
    Write-Warn 'Arbeitsspeicher (Win32_ComputerSystem) konnte nicht ermittelt werden.'
}
try {
    $installed = (Get-CimInstance -ClassName Win32_PhysicalMemory -ErrorAction Stop | Measure-Object -Property Capacity -Sum).Sum
    if ($installed) { $ramGb = [math]::Round($installed / 1GB, 1) }
} catch {
    $ramGb = $null
}
if (-not $ramGb) { $ramGb = $usableGb }
if ($ramGb) {
    if ($usableGb -and $usableGb -ne $ramGb) {
        Write-Info ("Arbeitsspeicher: {0} GB eingebaut, davon {1} GB fuer Windows nutzbar" -f (Format-De $ramGb), (Format-De $usableGb))
    } else {
        Write-Info ("Arbeitsspeicher: {0} GB" -f (Format-De $ramGb))
    }
}
$ramCmp = $null
if ($ramGb) { $ramCmp = [math]::Round($ramGb) }

# Grafikkarte(n)
$vramGb = $null
$gpuSupported = $false
$gpus = @(Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name })
foreach ($g in $gpus) {
    Write-Info ("Grafik: {0} (Windows-Treiber {1})" -f $g.Name, $g.DriverVersion)
}
$nvidia = @($gpus | Where-Object { $_.Name -match 'NVIDIA' })
$amd = @($gpus | Where-Object { $_.Name -match 'Radeon|AMD' })
if ($nvidia.Count -gt 0) {
    $gpuSupported = $true
    $driver = $null
    $computeCap = $null
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($smi) {
        $smiPath = if ($smi.Path) { $smi.Path } else { $smi.Definition }
        # compute_cap kennen nur neuere Treiber; sonst ohne dieses Feld erneut fragen
        $out = @(& $smiPath --query-gpu=name,driver_version,memory.total,compute_cap --format=csv,noheader,nounits 2> $null)
        if ($LASTEXITCODE -ne 0 -or $out.Count -eq 0) {
            $out = @(& $smiPath --query-gpu=name,driver_version,memory.total --format=csv,noheader,nounits 2> $null)
        }
        $line = $null
        if ($LASTEXITCODE -eq 0 -and $out.Count -gt 0) { $line = $out[0] }
        if ($line) {
            $f = @($line.ToString().Split(',') | ForEach-Object { $_.Trim() })
            if ($f.Count -ge 2) { $driver = $f[1] }
            if ($f.Count -ge 3 -and $f[2] -match '^[0-9]+$') { $vramGb = [math]::Round([double]$f[2] / 1024, 1) }
            if ($f.Count -ge 4 -and $f[3] -match '^[0-9]+(\.[0-9]+)?$') { $computeCap = [double]$f[3] }
            Write-Info ("nvidia-smi: {0}, Treiber {1}, Grafikspeicher {2} GB" -f $f[0], $driver, $(if ($vramGb) { Format-De $vramGb } else { '?' }))
        }
    }
    if (-not $driver) {
        $driver = Convert-NvidiaWmiVersion $nvidia[0].DriverVersion
        if ($driver) { Write-Info "NVIDIA-Treiber (aus der Windows-Treiberversion abgeleitet): $driver" }
    }
    if ($driver) {
        if (Test-VersionLower $driver '551.61') {
            Write-Warn "NVIDIA-Treiber $driver ist zu alt. Ollama verlangt unter Windows laut Dokumentation Treiber 551.61 oder neuer. Bitte den Treiber aktualisieren."
        } elseif ($computeCap -and $computeCap -lt 7.0 -and (Test-VersionLower $driver '570.0')) {
            Write-Warn "Aeltere NVIDIA-Karte (Compute Capability $computeCap): laut Ollama-Dokumentation ist Treiber 570 oder neuer noetig."
        } else {
            Write-Ok "NVIDIA-Grafikkarte: Ollama kann sie nutzen (Treiber $driver)."
        }
    } else {
        Write-Info 'NVIDIA-Treiberversion unbekannt. Ollama verlangt laut Dokumentation Treiber 551.61 oder neuer (Windows).'
    }
    Write-Info 'NVIDIA-Karten ab Compute Capability 5.0 werden unterstuetzt (Liste: https://developer.nvidia.com/cuda-gpus).'
}
if ($amd.Count -gt 0) {
    $rocm = @($amd | Where-Object { $_.Name -match 'RX\s*7(600|700|800|900)|W7(500|600|700|800|900)' })
    if ($rocm.Count -gt 0) {
        $gpuSupported = $true
        Write-Ok 'AMD-Grafikkarte aus der ROCm-Liste von Ollama (Windows: RX 7600-7900, PRO W7500-W7900). Noetig: ROCm-v7-faehiger Treiber.'
    } else {
        Write-Info 'AMD-Grafikkarte ausserhalb der ROCm-Liste: Ollama nutzt sie ggf. ueber Vulkan (laut Dokumentation), sonst den Prozessor.'
    }
}
$others = @($gpus | Where-Object { $_.Name -notmatch 'NVIDIA|Radeon|AMD|Microsoft Basic' })
if ($others.Count -gt 0 -and -not $gpuSupported) {
    Write-Info 'Andere Grafikeinheit (z. B. Intel): Ollama nutzt sie ggf. ueber Vulkan (laut Dokumentation). Nicht getestet.'
}
if (-not $gpuSupported) {
    Write-Warn 'Keine sicher unterstuetzte Grafikkarte erkannt: Ollama rechnet dann auf dem Prozessor (CPU). Das funktioniert, ist aber deutlich langsamer.'
}

# Freier Speicherplatz am Modellordner
$diskGb = $null
try {
    $qualifier = Split-Path -Path $ModelsDir -Qualifier -ErrorAction Stop
    $drive = Get-PSDrive -Name $qualifier.TrimEnd(':') -PSProvider FileSystem -ErrorAction Stop
    $diskGb = [math]::Round($drive.Free / 1GB, 1)
    Write-Info ("Freier Speicherplatz am Modellordner ({0}): {1} GB" -f $ModelsDir, (Format-De $diskGb))
} catch {
    Write-Warn "Freier Speicherplatz konnte nicht ermittelt werden ($ModelsDir)."
}

# Vorschlag wie local_ai.profiles.recommend_profile (RAM auf ganze GB gerundet)
$order = @('kompakt', 'standard', 'maximal')
$recommended = $null
if ($null -eq $ramCmp) {
    $recommended = 'standard'
} elseif ($ramCmp -ge 8) {
    if ($ramCmp -lt 16) { $recommended = 'kompakt' } elseif ($ramCmp -lt 32) { $recommended = 'standard' } else { $recommended = 'maximal' }
    if ($null -ne $diskGb) {
        while ($recommended -ne 'kompakt' -and $diskGb -lt $Profiles[$recommended].Disk) {
            $recommended = $order[[array]::IndexOf($order, $recommended) - 1]
        }
        if ($diskGb -lt $Profiles['kompakt'].Disk) { $recommended = $null }
    }
}
if ($recommended) {
    if ($null -eq $ramCmp) {
        Write-Ok "Vorschlag: $recommended (Arbeitsspeicher unbekannt - bitte selbst pruefen)."
    } else {
        Write-Ok ("Vorschlag: {0} ({1} GB Arbeitsspeicher, {2} GB frei)." -f $recommended, $ramCmp, $(if ($null -ne $diskGb) { Format-De $diskGb } else { '?' }))
    }
} else {
    Write-Warn 'Fuer die lokale KI reicht dieser Rechner voraussichtlich nicht (unter 8 GB Arbeitsspeicher oder unter 4 GB freier Speicherplatz). Die App arbeitet auch ohne KI.'
}

if (-not $KiProfil) {
    if (-not $recommended) {
        Write-Info 'Wer es trotzdem versuchen will, nennt das Profil ausdruecklich, z. B.: -Profile kompakt'
        exit 0
    }
    $KiProfil = $recommended
    Write-Info "Kein Profil angegeben - verwende den Vorschlag '$KiProfil'."
}
$KiProfil = $KiProfil.ToLowerInvariant()
$prof = $Profiles[$KiProfil]

# ---------------------------------------------------------------------------
# 2. Profil gegen den Rechner pruefen
# ---------------------------------------------------------------------------
Write-Step "2. Profil '$KiProfil' gegen den Rechner pruefen"
Write-Info ("Modelle: {0}" -f ($prof.Models -join ', '))
Write-Info ("Richtwerte (nicht auf Zielgeraeten gemessen): {0}" -f $prof.Windows)
Write-Info ("  Arbeitsspeicher mindestens {0} GB, empfohlen {1} GB; Speicherplatz {2} GB." -f $prof.MinRam, $prof.RecRam, $prof.Disk)
$profileOk = $true
if ($null -ne $ramCmp) {
    if ($ramCmp -lt $prof.MinRam) {
        Write-Warn "Arbeitsspeicher unter dem Mindestwert fuer '$KiProfil'. Es drohen sehr lange Laufzeiten oder Abbrueche."
        $profileOk = $false
    } elseif ($ramCmp -lt $prof.RecRam) {
        Write-Warn "Arbeitsspeicher unter der Empfehlung fuer '$KiProfil'. Es laeuft, aber langsamer."
    } else {
        Write-Ok 'Arbeitsspeicher passt.'
    }
}
if ($null -ne $diskGb) {
    if ($diskGb -lt $prof.Disk) {
        Write-Warn ("Freier Speicherplatz unter dem Richtwert ({0} GB) fuer '{1}'." -f $prof.Disk, $KiProfil)
        $profileOk = $false
    } else {
        Write-Ok 'Speicherplatz passt.'
    }
}
if ($prof.MinVram -gt 0) {
    if (-not $gpuSupported) {
        Write-Info "Fuer '$KiProfil' wird eine Grafikkarte empfohlen. Ohne sie laeuft es auf dem Prozessor deutlich langsamer."
    } elseif ($null -ne $vramGb -and $vramGb -lt $prof.MinVram) {
        Write-Warn ("Grafikspeicher {0} GB liegt unter dem Richtwert ({1} GB) fuer '{2}'. Ollama lagert dann Teile auf den Prozessor aus (langsamer)." -f (Format-De $vramGb), $prof.MinVram, $KiProfil)
    }
}
if ($recommended -and $KiProfil -ne $recommended) {
    Write-Info "Hinweis: Fuer diesen Rechner waere '$recommended' der Vorschlag."
}
if (-not $profileOk -and -not $DryRun) {
    if (-not (Confirm-Step "Trotz der Warnungen mit '$KiProfil' fortfahren?")) {
        Write-Info 'Abgebrochen. Nichts wurde geaendert.'
        exit 0
    }
}

# ---------------------------------------------------------------------------
# 3. Ollama vorhanden?
# ---------------------------------------------------------------------------
Write-Step '3. Ollama pruefen'
$script:OllamaExe = Find-Ollama
if ($script:OllamaExe) {
    Write-Ok "Ollama gefunden: $($script:OllamaExe)"
} else {
    Write-Info 'Ollama ist ein kostenloses Programm, das KI-Modelle auf diesem Rechner ausfuehrt.'
    $winget = Get-Command winget -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($InstallOllama -and $winget) {
        Write-Info 'Vorgesehen: winget install --id Ollama.Ollama -e (laedt das Ollama-Installationsprogramm herunter).'
        if (Confirm-Step 'Ollama jetzt mit winget installieren?') {
            & winget install --id Ollama.Ollama -e
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "winget meldet einen Fehler ($LASTEXITCODE). Bitte manuell installieren: https://ollama.com/download"
                exit 1
            }
            # PATH der laufenden Sitzung auffrischen (der Installer ergaenzt den Benutzer-PATH)
            $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
            $script:OllamaExe = Find-Ollama
            if (-not $script:OllamaExe) {
                Write-Fail 'Ollama wurde installiert, aber nicht gefunden. Bitte ein neues PowerShell-Fenster oeffnen und das Skript erneut starten.'
                exit 1
            }
            Write-Ok "Ollama installiert: $($script:OllamaExe)"
        } elseif (-not $DryRun) {
            Write-Info 'Ohne Ollama keine lokale KI. Die App funktioniert auch ohne. Nichts wurde geaendert.'
            exit 0
        }
    } else {
        if ($InstallOllama) { Write-Info 'winget wurde nicht gefunden.' }
        Write-Info 'So installieren Sie Ollama selbst:'
        Write-Info '  1. https://ollama.com/download oeffnen und "Download for Windows" waehlen (OllamaSetup.exe).'
        Write-Info '  2. OllamaSetup.exe ausfuehren (keine Administratorrechte noetig).'
        Write-Info '  3. Ollama einmal starten. Eine Anmeldung ist fuer lokale Modelle nicht noetig.'
        Write-Info '  4. Dieses Skript in einem neuen PowerShell-Fenster erneut ausfuehren.'
        if (-not $InstallOllama -and $winget) {
            Write-Info "Oder mit winget (nach Rueckfrage): scripts\install_local_ai_windows.ps1 -Profile $KiProfil -InstallOllama"
        }
        if (-not $DryRun) {
            Write-Fail 'Ollama fehlt.'
            exit 1
        }
    }
}

# ---------------------------------------------------------------------------
# 4. Nur-lokal-Modus von Ollama (Cloud-Funktionen aus)
# ---------------------------------------------------------------------------
Write-Step '4. Nur-lokal-Modus von Ollama'
Write-Info 'Die App lehnt Cloud-Modelle ohnehin ab. Dieser Schalter sperrt die Cloud-Funktionen zusaetzlich in Ollama selbst.'
$localOnlyActive = $false
$localOnlyWritten = $false
if (Test-Path -LiteralPath $ServerJson) {
    $content = Get-Content -LiteralPath $ServerJson -Raw -ErrorAction SilentlyContinue
    if ($content -match '"disable_ollama_cloud"\s*:\s*true') {
        Write-Ok "Bereits eingeschaltet ($ServerJson)."
        $localOnlyActive = $true
    } else {
        Write-Warn "$ServerJson enthaelt schon andere Einstellungen. Das Skript ueberschreibt sie nicht."
        Write-Info 'Bitte selbst ergaenzen:  "disable_ollama_cloud": true   (danach Ollama neu starten)'
        Write-Info 'Alternative: Benutzer-Umgebungsvariable OLLAMA_NO_CLOUD=1 setzen und Ollama neu starten.'
    }
} elseif (Confirm-Step "Nur-lokal-Modus einschalten (schreibt $ServerJson)?") {
    $dir = Split-Path -Parent $ServerJson
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $json = "{`n  `"disable_ollama_cloud`": true`n}`n"
    # UTF-8 ohne BOM: Ein BOM kann das Einlesen der JSON-Datei stoeren.
    [System.IO.File]::WriteAllText($ServerJson, $json, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "Geschrieben: $ServerJson"
    $localOnlyActive = $true
    $localOnlyWritten = $true
} else {
    Write-Info "Nicht eingeschaltet. Spaeter moeglich: Datei $ServerJson mit {`"disable_ollama_cloud`": true} anlegen."
}
if ($env:OLLAMA_NO_CLOUD -eq '1') {
    Write-Info 'OLLAMA_NO_CLOUD=1 ist gesetzt (wirkt, wenn Ollama mit dieser Umgebung startet).'
}

# ---------------------------------------------------------------------------
# 5. Ollama-Dienst erreichbar (nur 127.0.0.1)?
# ---------------------------------------------------------------------------
Write-Step '5. Ollama-Dienst auf 127.0.0.1:11434'
$serverRunning = $false
$version = Get-OllamaServerVersion
if ($version) {
    $serverRunning = $true
    Write-Ok "Ollama laeuft (Version $version)."
    if ($localOnlyWritten) {
        Write-Info 'Damit der Nur-lokal-Modus wirkt, muss Ollama einmal neu starten.'
        if (Confirm-Step 'Ollama jetzt neu starten?') {
            Stop-Process -Name 'ollama app', 'ollama' -ErrorAction SilentlyContinue
            if (Wait-OllamaServer -Seconds 15 -Up $false) {
                if (Start-OllamaServer) {
                    Write-Ok 'Ollama neu gestartet.'
                } else {
                    $serverRunning = $false
                }
            } else {
                Write-Warn 'Ollama laeuft weiter. Bitte selbst beenden (Symbol im Infobereich -> Quit Ollama) und neu starten.'
            }
        } else {
            Write-Info 'Bitte Ollama spaeter einmal beenden (Symbol im Infobereich -> Quit Ollama) und neu starten.'
        }
    }
} elseif ($DryRun) {
    Write-Info 'Ollama laeuft nicht. (Testlauf: wuerde Ollama jetzt starten und bis zu 30 Sekunden warten.)'
} elseif ($script:OllamaExe) {
    if (Start-OllamaServer) {
        $serverRunning = $true
        Write-Ok "Ollama laeuft (Version $(Get-OllamaServerVersion))."
    }
}

if ($serverRunning) {
    $version = Get-OllamaServerVersion
    if ($version -and (Test-VersionLower $version $MinOllamaVersion)) {
        Write-Warn "Ollama $version ist aelter als $MinOllamaVersion. Die Modelle verlangen laut Modellbeschreibung mindestens $MinOllamaVersion. Bitte Ollama aktualisieren."
    }
    if ($localOnlyActive) {
        $confirmed = $false
        if (Test-Path -LiteralPath $ServerLog) {
            $confirmed = [bool](Select-String -LiteralPath $ServerLog -SimpleMatch 'Ollama cloud disabled: true' -Quiet -ErrorAction SilentlyContinue)
        }
        if ($confirmed) {
            Write-Ok 'Protokoll bestaetigt: "Ollama cloud disabled: true".'
        } else {
            Write-Info 'Im Ollama-Protokoll steht noch nicht "Ollama cloud disabled: true". Nach dem naechsten Neustart pruefen:'
            Write-Info "  Select-String -Path `"$ServerLog`" -SimpleMatch 'Ollama cloud disabled'"
        }
    }
} elseif (-not $DryRun) {
    Write-Fail "Der Ollama-Dienst ist nicht erreichbar ($OllamaUrl). Bitte Ollama ueber das Startmenue starten und das Skript erneut ausfuehren."
    exit 1
}

# ---------------------------------------------------------------------------
# 6. Modelle herunterladen (je Modell eine Rueckfrage)
# ---------------------------------------------------------------------------
Write-Step "6. Modelle fuer '$KiProfil'"
Write-Host ('  {0,-13} {1,-9} {2}' -f 'Modell', 'Download', 'Aufgabe / Lizenz')
foreach ($m in $prof.Models) {
    Write-Host ('  {0,-13} {1,-9} {2}' -f $m, $ModelInfo[$m].Size, $ModelInfo[$m].Role)
    Write-Host ('  {0,-13} {1,-9} Lizenz: {2}' -f '', '', $ModelInfo[$m].License)
}
Write-Info 'Groessen laut Ollama-Registry (Stand 13.11.2026). Der Download laeuft ueber Ollama aus der Ollama-Bibliothek.'
Write-Info 'Dabei werden keine Dokumente uebertragen.'

$pullFailed = @()
foreach ($m in $prof.Models) {
    $present = $false
    if ($serverRunning -and $script:OllamaExe) { $present = ((Invoke-Quiet $script:OllamaExe @('show', $m)) -eq 0) }
    if ($present) {
        Write-Ok "$m ist bereits vorhanden - uebersprungen."
        continue
    }
    if ($DryRun) {
        Write-Info ("Testlauf: wuerde fragen und dann 'ollama pull {0}' ausfuehren ({1})." -f $m, $ModelInfo[$m].Size)
        continue
    }
    if (Confirm-Step ("'{0}' jetzt herunterladen ({1})?" -f $m, $ModelInfo[$m].Size)) {
        & $script:OllamaExe pull $m
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "$m heruntergeladen."
        } else {
            Write-Fail "Download von $m fehlgeschlagen. Internetverbindung pruefen und erneut versuchen (bereits geladene Teile bleiben erhalten)."
            $pullFailed += $m
        }
    } else {
        Write-Info "$m uebersprungen."
        $script:Skipped++
    }
}

# ---------------------------------------------------------------------------
# 7. Kontrolle: ollama list + kurzer Rauchtest (ollama show, ohne Bild)
# ---------------------------------------------------------------------------
Write-Step '7. Kontrolle'
if ($serverRunning -and $script:OllamaExe) {
    & $script:OllamaExe list
    if ($LASTEXITCODE -ne 0) { Write-Warn "'ollama list' konnte nicht ausgefuehrt werden." }
    foreach ($m in $prof.Models) {
        if ((Invoke-Quiet $script:OllamaExe @('show', $m)) -eq 0) {
            Write-Ok "${m}: Modellbeschreibung lesbar (ollama show)."
        } elseif ($pullFailed -contains $m) {
            Write-Info "${m}: nicht vorhanden (Download fehlgeschlagen, siehe oben)."
        } elseif ($DryRun -or $script:Skipped -gt 0) {
            Write-Info "${m}: nicht vorhanden."
        } else {
            Write-Fail "${m}: nicht vorhanden oder nicht lesbar."
        }
    }
} else {
    Write-Info 'Ollama laeuft nicht - Kontrolle uebersprungen.'
}

Write-Step 'Ergebnis'
if ($DryRun) {
    Write-Info 'Testlauf beendet. Nichts wurde installiert, heruntergeladen, gestartet oder geschrieben.'
}
if ($script:Failures -gt 0) {
    Write-Host ("  [X]  {0} Fehler, {1} Warnung(en). Details siehe oben." -f $script:Failures, $script:Warnings) -ForegroundColor Red
    exit 1
}
Write-Ok ("Fertig ({0} Warnung(en), {1} Modell(e) uebersprungen)." -f $script:Warnings, $script:Skipped)
Write-Host ''
Write-Host 'Naechste Schritte in der App:'
Write-Host '  "Einstellungen & Datenschutz" -> "Lokale KI (optional)"'
Write-Host "  -> Profil '$KiProfil' uebernehmen (oder 'Aktives Modell', 'Ausweichmodell', 'Gegenpruefung' selbst waehlen)"
Write-Host '  -> "Lokale KI aktivieren" -> "Lokale Laufzeit testen".'
Write-Host 'Optional im Terminal ausprobieren (Beenden mit /bye):  ollama run qwen3-vl:4b'
Write-Host 'Anleitung: docs\LOKALE_KI.md'
exit 0
