# Get for Android: build peppy_remote_for_tablet.zip without requiring a desktop install.
# Sources: local install tree (validated) OR GitHub (staging). Fail closed on stale trees.
#
# Examples:
#   .\android\get-android.ps1 -Yes
#   .\android\get-android.ps1 -Source github -RemoteBranch main
#   .\android\get-android.ps1 -Source local -InstallDir "$env:USERPROFILE\peppy_remote"

param(
    [ValidateSet("", "local", "github", "Local", "GitHub")]
    [string]$Source = "",
    [string]$InstallDir = "",
    [string]$RemoteBranch = "main",
    [string]$ScreensaverBranch = "main",
    [string]$TemplatesPath = "",
    [string]$SpectrumTemplatesPath = "",
    [string]$Output = "",
    [string]$ExpectVersion = "",
    [switch]$RefreshHandlers,
    [switch]$Yes
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ValidatePy = Join-Path $ScriptDir "lib\validate_android_tree.py"

$RemoteRaw = "https://raw.githubusercontent.com/foonerd/peppy_remote"
$SsRaw = "https://raw.githubusercontent.com/foonerd/peppy_screensaver"
$PeppymeterRepo = "https://github.com/foonerd/PeppyMeter.git"
$SpectrumRepo = "https://github.com/foonerd/PeppySpectrum.git"

$VolumioFiles = @(
    "volumio_peppymeter.py", "volumio_configfileparser.py", "volumio_turntable.py",
    "volumio_cassette.py", "volumio_compositor.py", "volumio_indicators.py",
    "volumio_spectrum.py", "volumio_basic.py", "volumio_folderimage.py",
    "volumio_artistfanart.py", "volumio_typeformat.py", "screensaverspectrum.py"
)

$Fonts = @(
    "DSEG7Classic-Bold.ttf", "DSEG7Classic-BoldItalic.ttf", "DSEG7Classic-Italic.ttf", "DSEG7Classic-Regular.ttf",
    "fontawesome-webfont.eot", "fontawesome-webfont.svg", "fontawesome-webfont.ttf", "fontawesome-webfont.woff", "fontawesome-webfont.woff2",
    "FontAwesome.otf",
    "gibson-bold.ttf", "Gibson-BoldItalic.ttf", "Gibson-Regular.ttf", "Gibson-RegularItalic.ttf",
    "glyphicons-halflings-regular.eot", "glyphicons-halflings-regular.svg", "glyphicons-halflings-regular.ttf",
    "glyphicons-halflings-regular.woff", "glyphicons-halflings-regular.woff2",
    "Lato-Bold.eot", "Lato-Bold.ttf", "Lato-Bold.woff", "Lato-Bold.woff2",
    "Lato-Light.eot", "Lato-Light.ttf", "Lato-Light.woff", "Lato-Light.woff2",
    "Lato-Regular.eot", "Lato-Regular.ttf", "Lato-Regular.woff", "Lato-Regular.woff2",
    "materialdesignicons-webfont.eot", "materialdesignicons-webfont.ttf", "materialdesignicons-webfont.woff", "materialdesignicons-webfont.woff2",
    "MaterialIcons-Regular.eot", "MaterialIcons-Regular.ttf", "MaterialIcons-Regular.woff", "MaterialIcons-Regular.woff2",
    "PeppyFont-Light.ttf", "PeppyFont-Regular.ttf", "PeppyFont-Bold.ttf", "PeppyFont-Italic.ttf"
)

$FormatIcons = @(
    "aac.svg", "aiff.svg", "airplay.svg", "alac.svg", "bt.svg", "cd.svg",
    "dab.svg", "dsd.svg", "dts.svg", "flac.svg", "fm.svg", "m4a.svg",
    "mp3.svg", "mp4.svg", "mqa.svg", "ogg.svg", "opus.svg", "qobuz.svg",
    "radio.svg", "rr.svg", "spotify.svg", "tidal.svg", "wav.svg",
    "wavpack.svg", "wma.svg", "YouTube.svg"
)

$AllIcons = "'aac', 'aiff', 'airplay', 'alac', 'bt', 'cd', 'dab', 'dsd', 'dts', 'flac', 'fm', 'm4a', 'mp3', 'mp4', 'mqa', 'ogg', 'opus', 'qobuz', 'radio', 'rr', 'spotify', 'tidal', 'wav', 'wavpack', 'wma', 'youtube'"

$LibModules = @(
    "peppy_common.py", "peppy_version.py", "peppy_network.py", "peppy_persist.py",
    "peppy_receivers.py", "peppy_spectrum.py", "peppy_smb.py", "peppy_asset.py",
    "peppy_wizard_cli.py", "peppy_wizard_gui.py"
)

function Die([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Info([string]$Message) { Write-Host $Message }

function Get-DesktopDir {
    $desk = [Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($desk)) {
        $profileDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
        return $profileDir
    }
    return $desk
}

function Get-PythonInvoker {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Exe = "py"; Prefix = @("-3") }
    }
    foreach ($c in @("python3", "python")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) {
            return @{ Exe = $c; Prefix = @() }
        }
    }
    Die "Python 3 is required for validation (python3/python/py not found)"
}

function Invoke-ValidateTree([string]$Root) {
    $inv = Get-PythonInvoker
    $args = @($ValidatePy, $Root)
    if ($ExpectVersion) { $args += @("--expect-version", $ExpectVersion) }
    & $inv.Exe @($inv.Prefix + $args)
    if ($LASTEXITCODE -ne 0) { throw "validation failed" }
}

function Get-ClientVersion([string]$Root) {
    $inv = Get-PythonInvoker
    $out = & $inv.Exe @($inv.Prefix + @($ValidatePy, $Root, "--print-version")) 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($out)) { return "unknown" }
    return ($out | Select-Object -Last 1).ToString().Trim()
}

function Download-File([string]$Uri, [string]$OutPath) {
    $dir = Split-Path -Parent $OutPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutPath -UseBasicParsing
    } catch {
        Die "Download failed: $Uri"
    }
}

function Try-DownloadFile([string]$Uri, [string]$OutPath) {
    $dir = Split-Path -Parent $OutPath
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    try {
        Invoke-WebRequest -Uri $Uri -OutFile $OutPath -UseBasicParsing
        return $true
    } catch {
        return $false
    }
}

function Patch-LocalIcons([string]$SsDir) {
    $files = @(
        "volumio_peppymeter.py", "volumio_turntable.py",
        "volumio_cassette.py", "volumio_basic.py"
    )
    foreach ($f in $files) {
        $path = Join-Path $SsDir $f
        if (-not (Test-Path $path)) { continue }
        $txt = Get-Content -Raw -Encoding UTF8 $path
        $txt = $txt -replace "local_icons = \{'tidal', 'cd', 'qobuz', 'dab', 'fm', 'radio'\}", "local_icons = {$AllIcons}"
        $txt = $txt -replace "local_icons = \{'tidal', 'cd', 'qobuz'\}", "local_icons = {$AllIcons}"
        Set-Content -Path $path -Value $txt -Encoding UTF8 -NoNewline
    }
}

function Fetch-HandlersInto([string]$SsDir) {
    New-Item -ItemType Directory -Force -Path $SsDir | Out-Null
    Info "Fetching screensaver handlers ($ScreensaverBranch)..."
    foreach ($file in $VolumioFiles) {
        Download-File "$SsRaw/$ScreensaverBranch/volumio_peppymeter/$file" (Join-Path $SsDir $file)
    }
    Patch-LocalIcons $SsDir
}

function Fetch-FontsIconsInto([string]$SsDir) {
    Info "Fetching fonts and format-icons ($RemoteBranch)..."
    $fontsDir = Join-Path $SsDir "fonts"
    $iconsDir = Join-Path $SsDir "format-icons"
    New-Item -ItemType Directory -Force -Path $fontsDir | Out-Null
    New-Item -ItemType Directory -Force -Path $iconsDir | Out-Null
    foreach ($font in $Fonts) {
        Download-File "$RemoteRaw/$RemoteBranch/fonts/$font" (Join-Path $fontsDir $font)
    }
    foreach ($icon in $FormatIcons) {
        Download-File "$RemoteRaw/$RemoteBranch/format-icons/$icon" (Join-Path $iconsDir $icon)
    }
}

function Fetch-EnginesInto([string]$SsDir, [string]$StageClones) {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Die "Git is required to clone PeppyMeter/PeppySpectrum"
    }
    New-Item -ItemType Directory -Force -Path $SsDir, $StageClones | Out-Null

    Info "Cloning PeppyMeter..."
    $meterSrc = Join-Path $StageClones "PeppyMeter"
    if (Test-Path $meterSrc) { Remove-Item -Recurse -Force $meterSrc }
    git clone --depth 1 $PeppymeterRepo $meterSrc 2>$null
    if ($LASTEXITCODE -ne 0) { Die "Failed to clone PeppyMeter" }
    $meterDest = Join-Path $SsDir "peppymeter"
    if (Test-Path $meterDest) { Remove-Item -Recurse -Force $meterDest }
    New-Item -ItemType Directory -Force -Path $meterDest | Out-Null
    Copy-Item -Path (Join-Path $meterSrc "*") -Destination $meterDest -Recurse -Force

    Info "Cloning PeppySpectrum..."
    $specSrc = Join-Path $StageClones "PeppySpectrum"
    if (Test-Path $specSrc) { Remove-Item -Recurse -Force $specSrc }
    git clone --depth 1 $SpectrumRepo $specSrc 2>$null
    if ($LASTEXITCODE -ne 0) { Die "Failed to clone PeppySpectrum" }
    $specDest = Join-Path $SsDir "spectrum"
    if (Test-Path $specDest) { Remove-Item -Recurse -Force $specDest }
    New-Item -ItemType Directory -Force -Path $specDest | Out-Null
    Copy-Item -Path (Join-Path $specSrc "*") -Destination $specDest -Recurse -Force
}

function Fetch-RemoteClientInto([string]$Dest) {
    $libDir = Join-Path $Dest "lib"
    New-Item -ItemType Directory -Force -Path $libDir | Out-Null
    Info "Fetching peppy_remote client ($RemoteBranch)..."
    Download-File "$RemoteRaw/$RemoteBranch/peppy_remote.py" (Join-Path $Dest "peppy_remote.py")
    foreach ($mod in $LibModules) {
        Download-File "$RemoteRaw/$RemoteBranch/lib/$mod" (Join-Path $libDir $mod)
    }
    $reqDest = Join-Path $Dest "requirements-android.txt"
    if (-not (Try-DownloadFile "$RemoteRaw/$RemoteBranch/requirements-android.txt" $reqDest)) {
        $localReq = Join-Path $RepoRoot "requirements-android.txt"
        if (Test-Path $localReq) {
            Copy-Item $localReq $reqDest -Force
        } else {
            @"
# Install via Pydroid Pip with "Use prebuilt libraries repository"
# Do NOT install pygame or cairosvg
requests
numpy
pillow
websocket-client
zeroconf
"@ | Set-Content -Path $reqDest -Encoding UTF8
        }
    }
}

function Stage-FromGitHub([string]$Stage) {
    $tree = Join-Path $Stage "peppy_remote"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    New-Item -ItemType Directory -Force -Path (Join-Path $tree "screensaver") | Out-Null
    Fetch-RemoteClientInto $tree
    Fetch-HandlersInto (Join-Path $tree "screensaver")
    Fetch-FontsIconsInto (Join-Path $tree "screensaver")
    Fetch-EnginesInto (Join-Path $tree "screensaver") (Join-Path $Stage "clones")
    return $tree
}

function Copy-TreeFiltered([string]$Src, [string]$Dest) {
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $excludeDirs = @("venv", ".venv", "cairo", "__pycache__", ".git", "mnt")
    $excludeFiles = @(
        "peppy_remote.sh", "peppy_remote.desktop", "peppy_remote_config.desktop",
        "uninstall.sh", "uninstall.ps1", "config.json", "ANDROID_PACK_INFO.txt"
    )
    Get-ChildItem -Path $Src -Force | ForEach-Object {
        $name = $_.Name
        if ($_.PSIsContainer) {
            if ($excludeDirs -contains $name) { return }
            if ($name -like "launch_*") { return }
            Copy-Item $_.FullName (Join-Path $Dest $name) -Recurse -Force
        } else {
            if ($excludeFiles -contains $name) { return }
            if ($name -like "launch_*") { return }
            if ($name -like "*.pyc") { return }
            Copy-Item $_.FullName (Join-Path $Dest $name) -Force
        }
    }
    # Strip nested __pycache__ / .pyc after copy
    Get-ChildItem -Path $Dest -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path $Dest -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Write-StartHere([string]$Dest) {
    $src = Join-Path $ScriptDir "START_HERE.md"
    $out = Join-Path $Dest "START_HERE.txt"
    if (Test-Path $src) {
        Copy-Item $src $out -Force
    } else {
        "See android/START_HERE.md in the peppy_remote repo." | Set-Content $out -Encoding UTF8
    }
}

function Write-PackInfo([string]$Dest, [string]$SourceLabel, [string]$ClientVer) {
    $utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    $hostName = $env:COMPUTERNAME
    @"
peppy_remote Android pack
=========================
source:              $SourceLabel
remote_ref:          $RemoteBranch
screensaver_ref:     $ScreensaverBranch
client_version:      $ClientVer
packed_at_utc:       $utc
host:                $hostName
tool:                get-android.ps1
"@ | Set-Content -Path (Join-Path $Dest "ANDROID_PACK_INFO.txt") -Encoding UTF8
}

function Copy-Templates([string]$PackRoot) {
    $t = Join-Path $PackRoot "templates"
    $ts = Join-Path $PackRoot "templates_spectrum"
    New-Item -ItemType Directory -Force -Path $t, $ts | Out-Null
    if ($TemplatesPath) {
        if (-not (Test-Path $TemplatesPath)) { Die "Templates path not found: $TemplatesPath" }
        Info "Copying templates from $TemplatesPath"
        Copy-Item (Join-Path $TemplatesPath "*") $t -Recurse -Force
    } else {
        "Put your PeppyMeter skin folders here (same layout as on Volumio / desktop remote)." |
            Set-Content (Join-Path $t "PUT_SKINS_HERE.txt") -Encoding UTF8
    }
    if ($SpectrumTemplatesPath) {
        if (-not (Test-Path $SpectrumTemplatesPath)) { Die "Spectrum templates path not found: $SpectrumTemplatesPath" }
        Info "Copying spectrum templates from $SpectrumTemplatesPath"
        Copy-Item (Join-Path $SpectrumTemplatesPath "*") $ts -Recurse -Force
    } else {
        "Put your PeppySpectrum skin folders here." |
            Set-Content (Join-Path $ts "PUT_SKINS_HERE.txt") -Encoding UTF8
    }
}

function Build-Zip([string]$SrcTree, [string]$SourceLabel) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if (-not $Output) {
        $script:Output = Join-Path (Get-DesktopDir) "peppy_remote_for_tablet.zip"
    }
    $outDir = Split-Path -Parent $Output
    if ($outDir -and -not (Test-Path $outDir)) {
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    }

    $cacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME ".cache" }
    $workBase = Join-Path $cacheRoot "peppy_android_work"
    New-Item -ItemType Directory -Force -Path $workBase | Out-Null
    $packRoot = Join-Path $workBase ("pack_" + [guid]::NewGuid().ToString("N"))
    $destTree = Join-Path $packRoot "peppy_remote"
    New-Item -ItemType Directory -Force -Path $packRoot | Out-Null
    Copy-TreeFiltered $SrcTree $destTree

    $req = Join-Path $destTree "requirements-android.txt"
    if (-not (Test-Path $req)) {
        $localReq = Join-Path $RepoRoot "requirements-android.txt"
        if (Test-Path $localReq) { Copy-Item $localReq $req -Force }
        else { Die "Missing requirements-android.txt" }
    }

    Write-StartHere $destTree
    $ver = Get-ClientVersion $destTree
    Write-PackInfo $destTree $SourceLabel $ver
    Copy-Templates $packRoot

    if (Test-Path $Output) { Remove-Item -Force $Output }

    [System.IO.Compression.ZipFile]::CreateFromDirectory($packRoot, $Output)
    Remove-Item -Recurse -Force $packRoot

    Info ""
    Info "Created: $Output"
    Info "Copy that zip to the tablet Download folder, unzip, then open START_HERE.txt"
}

# --- main ---
if (-not (Test-Path $ValidatePy)) {
    Die "Missing validator: $ValidatePy"
}

if (-not $Source) {
    if ($Yes) {
        $Source = "github"
    } elseif ([Environment]::UserInteractive) {
        Write-Host ""
        Write-Host "Get for Android: where should files come from?"
        Write-Host "  1) GitHub (recommended: works with no desktop install)"
        Write-Host "  2) Local install tree (must already be Android-capable)"
        Write-Host ""
        $ans = Read-Host "Choose [1/2] (default 1)"
        if ($ans -eq "2" -or $ans -eq "local" -or $ans -eq "Local") { $Source = "local" }
        else { $Source = "github" }
    } else {
        Die "No -Source and not interactive. Use -Source github|local or -Yes"
    }
}

$Source = $Source.ToLowerInvariant()
if ($Source -ne "local" -and $Source -ne "github") {
    Die "-Source must be local or github"
}

$tree = $null
$sourceLabel = $null

if ($Source -eq "local") {
    $profileDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $env:HOME }
    if (-not $InstallDir) { $InstallDir = Join-Path $profileDir "peppy_remote" }
    if (-not (Test-Path $InstallDir)) {
        Die "Local install not found: $InstallDir: use -Source github"
    }
    $tree = $InstallDir
    $sourceLabel = "local:$tree"

    if ($RefreshHandlers) {
        Info "Refreshing handlers from GitHub into local tree..."
        Fetch-HandlersInto (Join-Path $tree "screensaver")
        Fetch-FontsIconsInto (Join-Path $tree "screensaver")
        $meter = Join-Path $tree "screensaver\peppymeter"
        $spec = Join-Path $tree "screensaver\spectrum"
        if (-not (Test-Path $meter) -or -not (Test-Path $spec)) {
            $stage = Join-Path ([System.IO.Path]::GetTempPath()) ("peppy_android_stage_" + [guid]::NewGuid().ToString("N"))
            New-Item -ItemType Directory -Force -Path $stage | Out-Null
            Fetch-EnginesInto (Join-Path $tree "screensaver") (Join-Path $stage "clones")
            Remove-Item -Recurse -Force $stage
        }
    }

    Info "Validating local tree (fail closed)..."
    try {
        Invoke-ValidateTree $tree
    } catch {
        Die "Local tree is not Android-ready. Re-run with -Source github, or upgrade the client / use -RefreshHandlers."
    }
} else {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Die "Git is required for -Source github"
    }
    $cacheRoot = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME ".cache" }
    $stage = Join-Path $cacheRoot "peppy_android_stage"
    New-Item -ItemType Directory -Force -Path $stage | Out-Null
    Info "Building staging tree from GitHub (no desktop install required)..."
    $tree = Stage-FromGitHub $stage
    $sourceLabel = "github:remote=$RemoteBranch;screensaver=$ScreensaverBranch"
    Info "Validating staged tree..."
    try {
        Invoke-ValidateTree $tree
    } catch {
        Die "Staged GitHub tree failed validation: check branches"
    }
}

Build-Zip $tree $sourceLabel
