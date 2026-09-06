# Build a single-file platform-native binary with PyInstaller.
#
# PyInstaller cannot cross-compile, so run this natively on each target OS/arch.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

uv sync --extra dev --locked
uv run scripts/write-version.py
uv run pyinstaller --clean --noconfirm packaging/comic-dl.spec
Write-Host "Built binary into dist/."
