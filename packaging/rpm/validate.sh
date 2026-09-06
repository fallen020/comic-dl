#!/usr/bin/env bash
# Install a freshly built .rpm in a clean Fedora container and smoke-test it.
#
#   bash packaging/rpm/validate.sh '/pkg/*.rpm'
#
# Designed to run inside a `fedora:42` container with the package mounted
# read-only. Installs the .rpm, runs the CLI without network access, checks the
# reported version against the tag, then removes the package.
#
# Env:
#   EXPECTED_VERSION  tag version to assert (a leading 'v' or 'dev' skips the
#                     version gate)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKG_GLOB="${1:?usage: validate.sh <pkg-glob>}"

# A local file install resolves runtime requires like system python3 modules.
dnf install -y --quiet $PKG_GLOB

bash "$SCRIPT_DIR/smoke-test.sh" "$EXPECTED_VERSION"

dnf remove -y --quiet comic-dl
if command -v comic-dl >/dev/null; then
  echo "error: comic-dl is still on PATH after removal" >&2
  exit 1
fi
echo "rpm uninstall OK"
