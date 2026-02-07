# PeppyMeter Remote Client Uninstaller (Windows)
#
# Run from the installation directory, or:
#   .\uninstall.ps1 -Dir "C:\Users\You\peppy_remote"
#
# Removes the PeppyMeter Remote Client installation.

param([string]$Dir = "")

$ErrorActionPreference = "Stop"

$ScriptDir = if ($Dir) { $Dir } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

Write-Host ""
Write-Host "========================================"
Write-Host " PeppyMeter Remote Client Uninstaller"
Write-Host "========================================"
Write-Host ""
Write-Host "This will remove: $ScriptDir"
Write-Host ""

$reply = Read-Host "Are you sure you want to uninstall? [y/N]"
if ($reply -notmatch '^[Yy]') {
    Write-Host "Cancelled."
    exit 0
}

Write-Host ""
Write-Host "Uninstalling..."

# Remove shortcuts
$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = [Environment]::GetFolderPath("StartMenu")
$shortcuts = @(
    (Join-Path $desktop "PeppyMeter Remote.lnk"),
    (Join-Path $desktop "PeppyMeter Remote (Configure).lnk")
)
$smDir = Join-Path $startMenu "Programs"
if (-not (Test-Path $smDir)) { $smDir = $startMenu }
$shortcuts += (Join-Path $smDir "PeppyMeter Remote.lnk")
$shortcuts += (Join-Path $smDir "PeppyMeter Remote (Configure).lnk")
foreach ($s in $shortcuts) {
    if (Test-Path $s) {
        Remove-Item $s -Force
        Write-Host "  Removed: $([System.IO.Path]::GetFileName($s))"
    }
}

# Validate and remove install directory
$pyFile = Join-Path $ScriptDir "peppy_remote.py"
$cmdFile = Join-Path $ScriptDir "peppy_remote.cmd"
if ((Test-Path $pyFile) -and (Test-Path $cmdFile)) {
    Set-Location $env:USERPROFILE
    Remove-Item -Recurse -Force $ScriptDir
    Write-Host "  Removed: $ScriptDir"
} else {
    Write-Host "  ERROR: This doesn't look like a valid installation directory."
    Write-Host "  Refusing to delete for safety."
    exit 1
}

Write-Host ""
Write-Host "========================================"
Write-Host " Uninstall complete!"
Write-Host "========================================"
Write-Host ""
Write-Host "Python and Git were NOT removed. Uninstall them manually if no longer needed."
Write-Host ""
