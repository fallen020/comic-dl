# Build a single-file platform-native binary with PyInstaller.
#
# PyInstaller cannot cross-compile, so run this natively on each target OS/arch.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

uv sync --extra dev --locked
uv run scripts/write-version.py
uv run pyinstaller --clean --noconfirm packaging/comic-dl.spec
$Binary = Join-Path $PSScriptRoot ".." "dist" "comic-dl.exe"
$Binary = (Resolve-Path $Binary).Path
if (-not (Test-Path $Binary)) { throw "Binary was not created: $Binary" }
& $Binary --version
if ($LASTEXITCODE -ne 0) { throw "Binary self-test failed with exit code $LASTEXITCODE" }
Write-Host "Built binary into dist/."
