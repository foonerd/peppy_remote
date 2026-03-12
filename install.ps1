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
# If the one-liner fails (e.g. after winget installs Python/Git), download
# install.ps1 from the repo and run: powershell -ExecutionPolicy Bypass -File install.ps1
#
# Installs to $env:USERPROFILE\peppy_remote by default.
# Requires: Python 3.12+, Git.

param(
    [string]$Server = "",
    [string]$Dir = ""
)

# Require TLS 1.2 for GitHub/HTTPS on Windows 10 (default .NET protocol can fail)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Force UTF-8 for this session (avoids cp950/cp1252 issues with downloads and output)
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/foonerd/peppy_remote"
$RepoBranch = "main"
$ScreensaverRepoUrl = "https://github.com/foonerd/peppy_screensaver"
$PeppymeterRepo = "https://github.com/foonerd/PeppyMeter"
$SpectrumRepo = "https://github.com/foonerd/PeppySpectrum"

# USERPROFILE is Windows; use HOME on Linux (e.g. for testing with pwsh)
$profileDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
$InstallDir = if ($Dir) { $Dir } else { Join-Path $profileDir "peppy_remote" }

$AllIcons = "'aac', 'aiff', 'airplay', 'alac', 'bt', 'cd', 'dab', 'dsd', 'dts', 'flac', 'fm', 'm4a', 'mp3', 'mp4', 'mqa', 'ogg', 'opus', 'qobuz', 'radio', 'rr', 'spotify', 'tidal', 'wav', 'wavpack', 'wma', 'youtube'"

$VolumioFiles = @(
    "volumio_peppymeter.py", "volumio_configfileparser.py", "volumio_turntable.py",
    "volumio_cassette.py", "volumio_compositor.py", "volumio_indicators.py",
    "volumio_spectrum.py", "volumio_basic.py", "screensaverspectrum.py"
)

$Fonts = @(
    "DSEG7Classic-Bold.ttf", "DSEG7Classic-BoldItalic.ttf", "DSEG7Classic-Italic.ttf", "DSEG7Classic-Regular.ttf",
    "fontawesome-webfont.eot", "fontawesome-webfont.svg", "fontawesome-webfont.ttf", "fontawesome-webfont.woff", "fontawesome-webfont.woff2",
    "FontAwesome.otf",
    "gibson-bold.ttf", "Gibson-BoldItalic.ttf", "Gibson-Regular.ttf", "Gibson-RegularItalic.ttf",
    "glyphicons-halflings-regular.eot", "glyphicons-halflings-regular.svg", "glyphicons-halflings-regular.ttf", "glyphicons-halflings-regular.woff", "glyphicons-halflings-regular.woff2",
    "Lato-Bold.eot", "Lato-Bold.ttf", "Lato-Bold.woff", "Lato-Bold.woff2",
    "Lato-Light.eot", "Lato-Light.ttf", "Lato-Light.woff", "Lato-Light.woff2",
    "Lato-Regular.eot", "Lato-Regular.ttf", "Lato-Regular.woff", "Lato-Regular.woff2",
    "materialdesignicons-webfont.eot", "materialdesignicons-webfont.ttf", "materialdesignicons-webfont.woff", "materialdesignicons-webfont.woff2",
    "MaterialIcons-Regular.eot", "MaterialIcons-Regular.ttf", "MaterialIcons-Regular.woff", "MaterialIcons-Regular.woff2",
    "PeppyFont-Light.ttf", "PeppyFont-Regular.ttf", "PeppyFont-Bold.ttf"
)

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
            if ($v -match "Python 3\.(\d+)") {
                $minor = [int]$Matches[1]
                if ($minor -ge 12) { return $cmd }
                # Python found but too old - keep looking
                Write-Host "  Skipping $cmd (Python 3.$minor - need 3.12+)"
            }
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

# Direct path probing - fallback when PATH lookup fails after winget install.
# winget often installs Python/Git to known locations but the PATH update does
# not propagate to the current (or even a new) PowerShell session reliably.
# This probes common install paths directly and injects them into $env:Path.
function Find-PythonDirect {
    $userLocal = Join-Path $env:LOCALAPPDATA "Programs\Python"
    $candidates = @()
    # winget Python.Python.3.xx installs here
    if (Test-Path $userLocal) {
        $candidates += Get-ChildItem $userLocal -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "^Python3\d+$" } |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "python.exe" }
    }
    # MSI / system-wide installs
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($pf -and (Test-Path $pf)) {
            $candidates += Get-ChildItem $pf -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "^Python3\d+$" } |
                Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName "python.exe" }
        }
    }
    foreach ($exe in $candidates) {
        if (Test-Path $exe) {
            try {
                $v = & $exe --version 2>&1
                if ($v -match "Python 3\.(\d+)") {
                    $minor = [int]$Matches[1]
                    if ($minor -lt 12) { continue }  # Need 3.12+
                    # Inject into PATH for this session
                    $pyDir = Split-Path $exe
                    if ($env:Path -notlike "*$pyDir*") {
                        $env:Path = "$pyDir;" + $env:Path
                    }
                    return "`"$exe`""
                }
            } catch {}
        }
    }
    return $null
}

function Find-GitDirect {
    $candidates = @()
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($pf) { $candidates += Join-Path $pf "Git\cmd\git.exe" }
    }
    # winget Git.Git can also land in user-local
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Git\cmd\git.exe"
    foreach ($exe in $candidates) {
        if (Test-Path $exe) {
            $gitDir = Split-Path $exe
            if ($env:Path -notlike "*$gitDir*") {
                $env:Path = "$gitDir;" + $env:Path
            }
            return $true
        }
    }
    return $false
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
    Write-Host "If the one-liner fails (e.g. after winget installs Python/Git), download install.ps1"
    Write-Host "from the repo and run:  powershell -ExecutionPolicy Bypass -File install.ps1"
    Write-Host ""
    Write-Host "Parameters:"
    Write-Host "  -Server <host>   Pre-configure server hostname/IP"
    Write-Host "  -Dir <path>      Install directory (default: ~\peppy_remote)"
    Write-Host "  -Help, -h        Show this help"
    exit 0
}

trap {
    Write-Host ""
    Write-Host "Install failed: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host ""
    exit 1
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
if (-not $py) { $missing += "Python 3.12+" }
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

    # Pass 1: refresh PATH from registry and re-check via normal lookup
    Write-Host "Refreshing PATH and re-checking..."
    Refresh-EnvPath
    $py = Get-PythonCommand
    $gitOk = Test-GitPresent

    # Pass 2: if PATH lookup still fails, probe known install directories directly.
    # winget often updates the registry PATH but the change is not visible to
    # PowerShell sessions spawned before or shortly after the install completes.
    if (-not $py) {
        Write-Host "  Python not on PATH - probing install directories..."
        $py = Find-PythonDirect
        if ($py) { Write-Host "  Found Python at: $py" }
    }
    if (-not $gitOk) {
        Write-Host "  Git not on PATH - probing install directories..."
        $gitOk = Find-GitDirect
        if ($gitOk) { Write-Host "  Found Git" }
    }

    # Pass 3: one relaunch attempt only. Guard via environment variable to prevent
    # infinite loop - the old code had no guard, causing cascading windows.
    if (-not $py -or -not $gitOk) {
        if ($env:PEPPY_INSTALL_RELAUNCHED -eq "1") {
            # Already relaunched once - do not loop again
            Write-Host ""
            Write-Host "ERROR: Dependencies still not found after relaunch." -ForegroundColor Red
            Write-Host ""
            Write-Host "Please install manually, then run this script again:"
            if (-not $py) { Write-Host "  Python: https://www.python.org/downloads/" }
            if (-not $py) { Write-Host "     or:  winget install Python.Python.3.12" }
            if (-not $gitOk) { Write-Host "  Git:    https://git-scm.com/download/win" }
            if (-not $gitOk) { Write-Host "     or:  winget install Git.Git" }
            Write-Host ""
            Write-Host "After installing, close ALL PowerShell windows, open a new one, and run:"
            Write-Host "  irm https://raw.githubusercontent.com/foonerd/peppy_remote/main/install.ps1 | iex"
            Write-Host ""
            exit 1
        }
        Write-Host ""
        Write-Host "Dependencies were installed but are not visible in this session."
        Write-Host "Re-launching installer in a new window (updated PATH)..."
        $scriptUrl = "$RepoUrl/raw/$RepoBranch/install.ps1"
        $tempScript = Join-Path $env:TEMP "peppy_remote_install.ps1"
        try {
            Invoke-WebRequest -Uri $scriptUrl -OutFile $tempScript -UseBasicParsing
            $launchArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $tempScript)
            if ($Server) { $launchArgs += "-Server"; $launchArgs += $Server }
            if ($Dir)    { $launchArgs += "-Dir";    $launchArgs += $Dir }
            # Set guard so the child process will not relaunch again
            $env:PEPPY_INSTALL_RELAUNCHED = "1"
            Start-Process powershell -ArgumentList $launchArgs -Wait
            exit 0
        } catch {
            Write-Host "Could not re-launch. Please close this window, open a new PowerShell, then run:"
            Write-Host "  irm $scriptUrl | iex"
            Write-Host "Or download install.ps1 and run: powershell -ExecutionPolicy Bypass -File install.ps1"
            Write-Host ""
            exit 1
        }
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
    Download-File "$base/fonts/$font" (Join-Path $InstallDir "screensaver\fonts\$font")
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
        $c = Get-Content $file -Raw -Encoding UTF8
        $c = $c -replace "local_icons = \{'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'\}", "local_icons = {$AllIcons}"
        $c = $c -replace "local_icons = \{'tidal', 'cd', 'qobuz'\}", "local_icons = {$AllIcons}"
        Set-Content $file -Value $c -NoNewline -Encoding UTF8
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
# Windows: venv\Scripts\pip.exe; Linux/macOS (e.g. pwsh test): venv/bin/pip
# $IsWindows is read-only in PowerShell Core; Windows PS 5.1 has $env:OS = "Windows_NT"
$isWin = if ($null -ne $IsWindows) { $IsWindows } else { $env:OS -eq "Windows_NT" }
$venvBin = if ($isWin) { "Scripts" } else { "bin" }
$pipName = if ($isWin) { "pip.exe" } else { "pip" }
$pythonName = if ($isWin) { "python.exe" } else { "python" }
$pip = Join-Path $InstallDir (Join-Path "venv" (Join-Path $venvBin $pipName))
$pythonExe = Join-Path $InstallDir (Join-Path "venv" (Join-Path $venvBin $pythonName))
$prevErr = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $pip install --upgrade pip wheel -q 2>&1 | Out-Null
$packages = @(
    "pillow", "pygame", "cairosvg", "cssselect2", "tinycss2", "defusedxml", "webencodings",
    "python-socketio[client]", "python-engineio", "bidict", "requests", "certifi",
    "charset-normalizer", "idna", "urllib3", "websocket-client", "mss", "pyscreenshot",
    "easyprocess", "entrypoint2"
)
& $pip install @packages -q 2>&1 | Out-Null
$ErrorActionPreference = $prevErr
Write-Host "  Python packages installed"

# --- Cairo runtime (Windows; required for full meter: cassette, turntable, basic) ---
$cairoDir = Join-Path $InstallDir "cairo"
if ($isWin) {
    $cairoOk = $false
    try {
        & $pythonExe -c "import cairocffi" 2>$null
        if ($LASTEXITCODE -eq 0) { $cairoOk = $true }
    } catch {}
    if (-not $cairoOk) {
        Write-Host ""
        Write-Host "Installing Cairo runtime (needed for full meter display)..."
        $cairoZip = "https://github.com/preshing/cairo-windows/releases/download/1.17.2/cairo-windows-1.17.2.zip"
        $zipPath = Join-Path $env:TEMP "cairo-windows-1.17.2.zip"
        $extractDir = Join-Path $env:TEMP "cairo-windows-extract"
        try {
            Invoke-WebRequest -Uri $cairoZip -OutFile $zipPath -UseBasicParsing
            if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
            Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
            $bits = & $pythonExe -c "import struct; print(struct.calcsize('P')*8)" 2>$null
            if (-not $bits) { $bits = 64 }
            $dlls = Get-ChildItem -Path $extractDir -Recurse -Filter "cairo.dll" -ErrorAction SilentlyContinue
            $dll = $null
            foreach ($f in $dlls) {
                $pathLower = $f.FullName.ToLowerInvariant()
                if ($bits -eq 64 -and ($pathLower -match "64|amd64|x64")) { $dll = $f; break }
                if ($bits -eq 32 -and ($pathLower -notmatch "64|amd64|x64")) { $dll = $f; break }
            }
            if (-not $dll -and $dlls.Count -gt 0) { $dll = $dlls[0] }
            if ($dll) {
                New-Item -ItemType Directory -Force -Path $cairoDir | Out-Null
                Copy-Item $dll.FullName (Join-Path $cairoDir "cairo.dll") -Force
                Copy-Item $dll.FullName (Join-Path $cairoDir "libcairo-2.dll") -Force
                Write-Host "  Cairo runtime installed to $cairoDir"
            } else {
                Write-Host "  Cairo install skipped: no matching cairo.dll in archive"
            }
        } catch {
            Write-Host "  Cairo install failed: $_"
        } finally {
            if (Test-Path $zipPath) { Remove-Item $zipPath -Force -ErrorAction SilentlyContinue }
            if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir -ErrorAction SilentlyContinue }
        }
    }
}

# --- Convert SVG icons to ICO for Windows shortcuts ---
# Uses cairosvg + Pillow from the venv. Renders at 48 and 256 for desktop
# and start menu shortcuts. Each size rendered directly from SVG for clarity.
$icoMain = Join-Path $InstallDir "peppy_remote.ico"
$icoConfig = Join-Path $InstallDir "peppy_remote_config.ico"
Write-Host ""
Write-Host "Generating Windows icons from SVG..."
$svgToIco = @"
import sys, os
# Ensure cairo DLL is findable if installed
cairo_dir = os.path.join(r'$InstallDir', 'cairo')
if os.path.isdir(cairo_dir):
    os.environ['PATH'] = cairo_dir + os.pathsep + os.environ.get('PATH', '')

try:
    import cairosvg
    from PIL import Image
    from io import BytesIO
except ImportError as e:
    print(f'  Icon conversion skipped: {e}')
    sys.exit(1)

def svg_to_ico(svg_path, ico_path):
    sizes = [48, 256]
    images = []
    for sz in sizes:
        png_data = cairosvg.svg2png(url=svg_path, output_width=sz, output_height=sz)
        img = Image.open(BytesIO(png_data)).convert('RGBA')
        images.append(img)
    images[0].save(ico_path, format='ICO', append_images=images[1:], sizes=[(48, 48), (256, 256)])

converted = 0
for svg_name, ico_name in [('peppy_remote.svg', 'peppy_remote.ico'), ('peppy_remote_config.svg', 'peppy_remote_config.ico')]:
    svg_path = os.path.join(r'$InstallDir', svg_name)
    ico_path = os.path.join(r'$InstallDir', ico_name)
    if os.path.isfile(svg_path):
        try:
            svg_to_ico(svg_path, ico_path)
            converted += 1
        except Exception as e:
            print(f'  Warning: could not convert {svg_name}: {e}')

print(f'  Converted {converted} icon(s) to ICO')
"@
$svgToIcoScript = Join-Path $env:TEMP "peppy_svg_to_ico.py"
Set-Content $svgToIcoScript -Value $svgToIco -Encoding UTF8
try {
    # Cairo DLL must be on PATH for cairosvg
    $prevPath = $env:PATH
    if (Test-Path $cairoDir) { $env:PATH = "$cairoDir;$env:PATH" }
    & $pythonExe $svgToIcoScript 2>&1 | ForEach-Object { Write-Host $_ }
    $env:PATH = $prevPath
} catch {
    Write-Host "  Icon conversion failed: $_ (shortcuts will use default icon)"
} finally {
    Remove-Item $svgToIcoScript -Force -ErrorAction SilentlyContinue
}

# --- Launcher script ---
Write-Host ""
Write-Host "Creating launcher..."
$cairoPathLinePs1 = ''
$cairoPathLineCmd = ''
if (Test-Path $cairoDir) {
    $cairoPathLinePs1 = '$env:PATH = "$ScriptDir\cairo;" + $env:PATH'
    $cairoPathLineCmd = 'set PATH=%SCRIPT_DIR%cairo;%PATH%'
}
$launcherPs1 = @"
# PeppyMeter Remote Client Launcher (Windows)
`$ScriptDir = Split-Path -Parent `$MyInvocation.MyCommand.Path
$cairoPathLinePs1
`$env:PYTHONUTF8 = "1"
`$env:PYTHONPATH = "`$ScriptDir\screensaver;`$ScriptDir\screensaver\peppymeter;`$ScriptDir\screensaver\spectrum"
& "`$ScriptDir\venv\Scripts\python.exe" "`$ScriptDir\peppy_remote.py" @args
"@
Set-Content (Join-Path $InstallDir "peppy_remote.ps1") -Value $launcherPs1

$launcherCmd = @"
@echo off
set SCRIPT_DIR=%~dp0
$cairoPathLineCmd
set PYTHONUTF8=1
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
# ICO paths for shortcuts (graceful fallback if conversion failed)
$icoMainPath = if (Test-Path $icoMain) { "$icoMain,0" } else { "" }
$icoConfigPath = if (Test-Path $icoConfig) { "$icoConfig,0" } else { "" }
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
            if ($icoMainPath) { $lnk.IconLocation = $icoMainPath }
            $lnk.Save()
            $lnkConfig = $ws.CreateShortcut((Join-Path $desktop "PeppyMeter Remote (Configure).lnk"))
            $lnkConfig.TargetPath = $cmdPath
            $lnkConfig.Arguments = "--config"
            $lnkConfig.WorkingDirectory = $InstallDir
            if ($icoConfigPath) { $lnkConfig.IconLocation = $icoConfigPath }
            $lnkConfig.Save()
        }
        if ($startMenu) {
            $smDir = Join-Path $startMenu "Programs"
            if (-not (Test-Path $smDir)) { $smDir = $startMenu }
            $lnk2 = $ws.CreateShortcut((Join-Path $smDir "PeppyMeter Remote.lnk"))
            $lnk2.TargetPath = $cmdPath
            $lnk2.Arguments = "--windowed"
            $lnk2.WorkingDirectory = $InstallDir
            if ($icoMainPath) { $lnk2.IconLocation = $icoMainPath }
            $lnk2.Save()
            $lnk2Config = $ws.CreateShortcut((Join-Path $smDir "PeppyMeter Remote (Configure).lnk"))
            $lnk2Config.TargetPath = $cmdPath
            $lnk2Config.Arguments = "--config"
            $lnk2Config.WorkingDirectory = $InstallDir
            if ($icoConfigPath) { $lnk2Config.IconLocation = $icoConfigPath }
            $lnk2Config.Save()
        }
        Write-Host "  Shortcuts created (with custom icons)" 
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
