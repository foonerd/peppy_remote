# PeppyMeter Remote Client Installer (Windows)
#
# Run in PowerShell (Run as Administrator not required):
#   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser  # once, if needed
#   irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 | iex
#
# Or with parameters:
#   irm ... | iex -ArgumentList '-Server','volumio'
#   irm ... | iex -ArgumentList '-Dir','C:\peppy_remote'
#
# Installs to $env:USERPROFILE\peppy_remote by default.
# Requires: Python 3.8+, Git.

param(
    [string]$Server = "",
    [string]$Dir = ""
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/foonerd/peppy_remote"
$RepoBranch = "main"
$ScreensaverRepoUrl = "https://github.com/foonerd/peppy_screensaver"
$PeppymeterRepo = "https://github.com/foonerd/PeppyMeter"
$SpectrumRepo = "https://github.com/foonerd/PeppySpectrum"

$InstallDir = if ($Dir) { $Dir } else { Join-Path $env:USERPROFILE "peppy_remote" }

$AllIcons = "'aac', 'aiff', 'airplay', 'alac', 'bt', 'cd', 'dab', 'dsd', 'dts', 'flac', 'fm', 'm4a', 'mp3', 'mp4', 'mqa', 'ogg', 'opus', 'qobuz', 'radio', 'rr', 'spotify', 'tidal', 'wav', 'wavpack', 'wma', 'youtube'"

$VolumioFiles = @(
    "volumio_peppymeter.py", "volumio_configfileparser.py", "volumio_turntable.py",
    "volumio_cassette.py", "volumio_compositor.py", "volumio_indicators.py",
    "volumio_spectrum.py", "volumio_basic.py", "screensaverspectrum.py"
)

$Fonts = @("DSEG7Classic-Bold.ttf", "DSEG7Classic-BoldItalic.ttf", "DSEG7Classic-Italic.ttf", "DSEG7Classic-Regular.ttf")

$FormatIcons = @(
    "aac.svg", "aiff.svg", "airplay.svg", "alac.svg", "bt.svg", "cd.svg",
    "dab.svg", "dsd.svg", "dts.svg", "flac.svg", "fm.svg", "m4a.svg",
    "mp3.svg", "mp4.svg", "mqa.svg", "ogg.svg", "opus.svg", "qobuz.svg",
    "radio.svg", "rr.svg", "spotify.svg", "tidal.svg", "wav.svg",
    "wavpack.svg", "wma.svg", "YouTube.svg"
)

function Write-Banner { param([string]$Text) Write-Host ""; Write-Host "========================================"; Write-Host " $Text"; Write-Host "========================================"; Write-Host "" }
function Download-File { param([string]$Uri, [string]$OutPath) Invoke-WebRequest -Uri $Uri -OutFile $OutPath -UseBasicParsing }

function Get-PythonCommand {
    foreach ($cmd in @("py -3", "python", "python3")) {
        try {
            $v = Invoke-Expression "$cmd --version 2>&1"
            if ($v -match "Python 3\.(\d+)") { return $cmd }
        } catch {}
    }
    return $null
}

function Test-GitPresent {
    try {
        $null = git --version 2>$null
        return $true
    } catch { return $false }
}

function Refresh-EnvPath {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
}

# --- Parse -Help ---
if ($args -contains "-Help" -or $args -contains "-h") {
    Write-Host "PeppyMeter Remote Client Installer (Windows)"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 | iex"
    Write-Host "  irm ... | iex -ArgumentList '-Server','volumio'"
    Write-Host "  irm ... | iex -ArgumentList '-Dir','C:\peppy_remote'"
    Write-Host ""
    Write-Host "Parameters:"
    Write-Host "  -Server <host>   Pre-configure server hostname/IP"
    Write-Host "  -Dir <path>      Install directory (default: ~\peppy_remote)"
    Write-Host "  -Help, -h        Show this help"
    exit 0
}

Write-Banner "PeppyMeter Remote Client Installer"
Write-Host "Install directory: $InstallDir"
if ($Server) { Write-Host "Server: $Server" }
Write-Host ""

# --- Existing install ---
if (Test-Path $InstallDir) {
    $reply = Read-Host "Existing installation found. Remove and reinstall? [y/N]"
    if ($reply -match '^[Yy]') {
        Write-Host "Removing existing installation..."
        Remove-Item -Recurse -Force $InstallDir
    } else {
        Write-Host "Cancelled."
        exit 0
    }
}

# --- Check dependencies (Python, Git); offer to install via winget if missing ---
$py = Get-PythonCommand
$gitOk = Test-GitPresent
$missing = @()
if (-not $py) { $missing += "Python 3.8+" }
if (-not $gitOk) { $missing += "Git" }

if ($missing.Count -gt 0) {
    Write-Host "Checking dependencies..."
    Write-Host "  Missing: $($missing -join ', ')"
    $reply = Read-Host "Install missing dependencies via winget? [Y/n]"
    if ($reply -match '^[Nn]') {
        Write-Host ""
        if (-not $py) { Write-Host "Install Python: https://www.python.org/downloads/ or: winget install Python.Python.3.12" }
        if (-not $gitOk) { Write-Host "Install Git: https://git-scm.com/download/win or: winget install Git.Git" }
        Write-Host ""
        exit 1
    }
    Write-Host ""
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Host "ERROR: winget not found. Install Python and Git manually, then run this script again."
        if (-not $py) { Write-Host "  Python: winget install Python.Python.3.12" }
        if (-not $gitOk) { Write-Host "  Git: winget install Git.Git" }
        exit 1
    }
    if (-not $py) {
        Write-Host "Installing Python via winget..."
        & winget install --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
    }
    if (-not $gitOk) {
        Write-Host "Installing Git via winget..."
        & winget install --id Git.Git --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
    }
    Write-Host "Refreshing PATH and re-checking..."
    Refresh-EnvPath
    $py = Get-PythonCommand
    $gitOk = Test-GitPresent
    if (-not $py -or -not $gitOk) {
        Write-Host ""
        Write-Host "Dependencies were installed but may not be visible in this session."
        Write-Host "Please close this window, open a new PowerShell, then run this script again."
        Write-Host ""
        exit 1
    }
}

Write-Host "Checking dependencies..."
Write-Host "  Python: $py"
Write-Host "  Git: found"
Write-Host ""

# --- Create directories ---
Write-Host ""
Write-Host "Creating installation directory..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "screensaver") | Out-Null

# --- Download client scripts ---
Write-Host ""
Write-Host "Downloading client scripts..."
$base = "$RepoUrl/raw/$RepoBranch"
Download-File "$base/peppy_remote.py" (Join-Path $InstallDir "peppy_remote.py")
Download-File "$base/uninstall.ps1" (Join-Path $InstallDir "uninstall.ps1")
Download-File "$base/peppy_remote.svg" (Join-Path $InstallDir "peppy_remote.svg")
Download-File "$base/peppy_remote_config.svg" (Join-Path $InstallDir "peppy_remote_config.svg")
Write-Host "  Downloaded: peppy_remote.py, uninstall.ps1, icons"

# --- Clone PeppyMeter ---
Write-Host ""
Write-Host "Cloning PeppyMeter..."
$pmDir = Join-Path $InstallDir "screensaver\peppymeter"
if (Test-Path $pmDir) {
    Push-Location $pmDir; git pull --ff-only 2>$null; Pop-Location
} else {
    git clone --depth 1 $PeppymeterRepo $pmDir
}

# --- Clone PeppySpectrum ---
Write-Host "Cloning PeppySpectrum..."
$specDir = Join-Path $InstallDir "screensaver\spectrum"
if (Test-Path $specDir) {
    Push-Location $specDir; git pull --ff-only 2>$null; Pop-Location
} else {
    git clone --depth 1 $SpectrumRepo $specDir
}

# --- Download Volumio handlers ---
Write-Host ""
Write-Host "Downloading Volumio handlers..."
$volBase = "$ScreensaverRepoUrl/raw/$RepoBranch/volumio_peppymeter"
foreach ($f in $VolumioFiles) {
    Download-File "$volBase/$f" (Join-Path $InstallDir "screensaver\$f")
}
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "screensaver\fonts") | Out-Null
foreach ($font in $Fonts) {
    Download-File "$volBase/fonts/$font" (Join-Path $InstallDir "screensaver\fonts\$font")
}
New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "screensaver\format-icons") | Out-Null
foreach ($icon in $FormatIcons) {
    Download-File "$base/format-icons/$icon" (Join-Path $InstallDir "screensaver\format-icons\$icon")
}
Write-Host "  All Volumio handlers and icons downloaded"

# --- Patch local_icons ---
Write-Host ""
Write-Host "Patching handlers for local icon support..."
$patchFiles = @(
    (Join-Path $InstallDir "screensaver\volumio_peppymeter.py"),
    (Join-Path $InstallDir "screensaver\volumio_turntable.py"),
    (Join-Path $InstallDir "screensaver\volumio_cassette.py"),
    (Join-Path $InstallDir "screensaver\volumio_basic.py")
)
foreach ($file in $patchFiles) {
    if (Test-Path $file) {
        $c = Get-Content $file -Raw
        $c = $c -replace "local_icons = \{'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'\}", "local_icons = {$AllIcons}"
        $c = $c -replace "local_icons = \{'tidal', 'cd', 'qobuz'\}", "local_icons = {$AllIcons}"
        Set-Content $file -Value $c -NoNewline
        Write-Host "  Patched: $([System.IO.Path]::GetFileName($file))"
    }
}

# --- Virtual environment ---
Write-Host ""
Write-Host "Setting up Python environment..."
$venvPath = Join-Path $InstallDir "venv"
if (-not (Test-Path $venvPath)) {
    Invoke-Expression "$py -m venv `"$venvPath`""
}
$pip = Join-Path $InstallDir "venv\Scripts\pip.exe"
$pythonExe = Join-Path $InstallDir "venv\Scripts\python.exe"
& $pip install --upgrade pip wheel -q 2>$null
$packages = @(
    "pillow", "pygame", "cairosvg", "cssselect2", "tinycss2", "defusedxml", "webencodings",
    "python-socketio[client]", "python-engineio", "bidict", "requests", "certifi",
    "charset-normalizer", "idna", "urllib3", "websocket-client", "mss", "pyscreenshot",
    "easyprocess", "entrypoint2"
)
& $pip install @packages -q 2>$null
Write-Host "  Python packages installed"

# --- Launcher script ---
Write-Host ""
Write-Host "Creating launcher..."
$launcherPs1 = @"
# PeppyMeter Remote Client Launcher (Windows)
`$ScriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Path
`$env:PYTHONPATH = "`$ScriptDir\screensaver;`$ScriptDir\screensaver\peppymeter;`$ScriptDir\screensaver\spectrum"
& "`$ScriptDir\venv\Scripts\python.exe" "`$ScriptDir\peppy_remote.py" @args
"@
Set-Content (Join-Path $InstallDir "peppy_remote.ps1") -Value $launcherPs1

$launcherCmd = @"
@echo off
set SCRIPT_DIR=%~dp0
set PYTHONPATH=%SCRIPT_DIR%screensaver;%SCRIPT_DIR%screensaver\peppymeter;%SCRIPT_DIR%screensaver\spectrum
"%SCRIPT_DIR%venv\Scripts\python.exe" "%SCRIPT_DIR%peppy_remote.py" %*
"@
Set-Content (Join-Path $InstallDir "peppy_remote.cmd") -Value $launcherCmd
Write-Host "  Created: peppy_remote.ps1, peppy_remote.cmd"

# --- Config ---
Write-Host ""
Write-Host "Creating configuration..."
$serverHost = if ($Server) { "`"$Server`"" } else { "null" }
$configJson = @"
{
  "wizard_completed": false,
  "server": {
    "host": $serverHost,
    "level_port": 5580,
    "volumio_port": 3000,
    "discovery_port": 5579,
    "discovery_timeout": 10
  },
  "display": {
    "windowed": true,
    "position": null,
    "fullscreen": false,
    "monitor": 0
  },
  "templates": {
    "use_smb": true,
    "local_path": null
  }
}
"@
Set-Content (Join-Path $InstallDir "config.json") -Value $configJson
if ($Server) { Write-Host "  Server pre-configured: $Server" } else { Write-Host "  Auto-discovery enabled" }

# --- Shortcuts (optional) ---
$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = [Environment]::GetFolderPath("StartMenu")
if ($desktop -or $startMenu) {
    Write-Host ""
    Write-Host "Creating shortcuts..."
    try {
        $ws = New-Object -ComObject WScript.Shell
        $cmdPath = Join-Path $InstallDir "peppy_remote.cmd"
        if ($desktop) {
            $lnk = $ws.CreateShortcut((Join-Path $desktop "PeppyMeter Remote.lnk"))
            $lnk.TargetPath = $cmdPath
            $lnk.Arguments = "--windowed"
            $lnk.WorkingDirectory = $InstallDir
            $lnk.Save()
            $lnkConfig = $ws.CreateShortcut((Join-Path $desktop "PeppyMeter Remote (Configure).lnk"))
            $lnkConfig.TargetPath = $cmdPath
            $lnkConfig.Arguments = "--config"
            $lnkConfig.WorkingDirectory = $InstallDir
            $lnkConfig.Save()
        }
        if ($startMenu) {
            $smDir = Join-Path $startMenu "Programs"
            if (-not (Test-Path $smDir)) { $smDir = $startMenu }
            $lnk2 = $ws.CreateShortcut((Join-Path $smDir "PeppyMeter Remote.lnk"))
            $lnk2.TargetPath = $cmdPath
            $lnk2.Arguments = "--windowed"
            $lnk2.WorkingDirectory = $InstallDir
            $lnk2.Save()
            $lnk2Config = $ws.CreateShortcut((Join-Path $smDir "PeppyMeter Remote (Configure).lnk"))
            $lnk2Config.TargetPath = $cmdPath
            $lnk2Config.Arguments = "--config"
            $lnk2Config.WorkingDirectory = $InstallDir
            $lnk2Config.Save()
        }
        Write-Host "  Shortcuts created"
    } catch {
        Write-Host "  Shortcuts skipped: $_"
    }
}

# --- Done ---
Write-Banner "Installation complete!"
Write-Host "To run:"
Write-Host "  $InstallDir\peppy_remote.cmd"
Write-Host "  $InstallDir\peppy_remote.cmd --config    # Configure (GUI or text)"
Write-Host "  $InstallDir\peppy_remote.cmd --windowed  # Windowed display"
Write-Host ""
Write-Host "Or double-click: PeppyMeter Remote.lnk (if created)"
Write-Host ""
Write-Host "Templates on Windows use UNC paths (no SMB mount). Ensure Volumio SMB share is enabled."
Write-Host "To uninstall: $InstallDir\uninstall.ps1"
Write-Host ""
