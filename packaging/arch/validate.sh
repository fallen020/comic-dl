#!/usr/bin/env bash
# Install a freshly built .pkg.tar.zst on a clean Arch container and smoke-test it.
#
#   bash packaging/arch/validate.sh '/pkg/*.pkg.tar.zst'
#
# Designed to run inside an `archlinux:latest` container with the package
# mounted read-only. Installs the package, runs the CLI without network access,
# checks the reported version against the tag, then removes the package.
#
# Env:
#   EXPECTED_VERSION  tag version to assert (a leading 'v' or 'dev' skips the
#                     version gate)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKG_GLOB="${1:?usage: validate.sh <pkg-glob>}"

pacman -Syu --noconfirm --needed >/dev/null
pacman -U --noconfirm $PKG_GLOB

bash "$SCRIPT_DIR/smoke-test.sh" "$EXPECTED_VERSION"

pacman -R --noconfirm comic-dl
if command -v comic-dl >/dev/null; then
  echo "error: comic-dl is still on PATH after removal" >&2
  exit 1
fi
echo "arch uninstall OK"
