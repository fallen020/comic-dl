#!/usr/bin/env bash
# Install a freshly built .deb in a clean Debian container and smoke-test it.
#
#   bash packaging/deb/validate.sh '/pkg/*.deb'
#
# Designed to run inside a `debian:13` container with the package mounted
# read-only. Installs the .deb, runs the CLI without network access, checks the
# reported version against the tag, then removes the package to prove a clean
# uninstall.
#
# Env:
#   EXPECTED_VERSION  tag version to assert (a leading 'v' or 'dev' skips the
#                     version gate)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKG_GLOB="${1:?usage: validate.sh <pkg-glob>}"

apt-get update -qq
apt-get install -y --no-install-recommends $PKG_GLOB

bash "$SCRIPT_DIR/smoke-test.sh" "$EXPECTED_VERSION"

apt-get remove -y --quiet comic-dl
if command -v comic-dl >/dev/null; then
  echo "error: comic-dl is still on PATH after removal" >&2
  exit 1
fi
echo "deb uninstall OK"
