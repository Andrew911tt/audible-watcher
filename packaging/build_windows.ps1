# Build the Windows app and installer locally (the GitHub release workflow does the same).
# Needs: Python 3.12, and Inno Setup 6 (https://jrsoftware.org/isinfo.php) for the installer.
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Version 1.0.0
param([string]$Version = "0.0.0-dev")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -r requirements-desktop.txt
python packaging\make_icon.py

python -m PyInstaller --noconfirm --clean --windowed --name "Audible Watcher" `
  --icon packaging\icon.ico `
  --add-data "templates;templates" --add-data "static;static" `
  --copy-metadata APScheduler --collect-submodules apscheduler `
  --hidden-import pystray._win32 `
  desktop.py

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
  & $iscc "/DAppVersion=$Version" packaging\installer.iss
  Write-Host "Installer: packaging\Output\AudibleWatcher-Setup-$Version.exe"
} else {
  Write-Host "Inno Setup not found - app built in dist\Audible Watcher\ (no installer)."
}
