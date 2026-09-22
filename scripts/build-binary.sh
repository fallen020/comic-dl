#!/usr/bin/env bash
# Build a single-file platform-native binary with PyInstaller.
#
# PyInstaller cannot cross-compile, so run this natively on each target OS/arch.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --extra dev --locked
uv run scripts/write-version.py
uv run pyinstaller --clean --noconfirm packaging/comic-dl.spec
test -x dist/comic-dl || { echo "error: binary was not created: dist/comic-dl" >&2; exit 1; }
./dist/comic-dl --version
echo "Built binary into dist/."
