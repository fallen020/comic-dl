#!/usr/bin/env bash
# Shared smoke-test for installed comic-dl packages.
#
#   bash packaging/smoke-test.sh <expected-version>
#
# Verifies the installed comic-dl reports the correct version and its core
# CLI commands work. Called by the per-distro validate.sh scripts after
# the package is installed.
set -euo pipefail

EXPECTED="${1:?usage: smoke-test.sh <expected-version>}"

# --- version gate ---
EXPECTED="${EXPECTED#v}"
if [[ -n "$EXPECTED" && "$EXPECTED" != "dev" ]]; then
  if command -v dpkg-query >/dev/null 2>&1; then
    installed="$(dpkg-query -W -f='${Version}' comic-dl)"
    case "$installed" in
      "$EXPECTED" | "$EXPECTED-"*) ;;
      *) echo "error: installed version '$installed' != expected '$EXPECTED'" >&2; exit 1 ;;
    esac
  elif command -v rpm >/dev/null 2>&1; then
    installed="$(rpm -q --qf='%{VERSION}' comic-dl)"
    if [[ "$installed" != "$EXPECTED" ]]; then
      echo "error: installed version '$installed' != expected '$EXPECTED'" >&2
      exit 1
    fi
  elif command -v pacman >/dev/null 2>&1; then
    installed="$(pacman -Qi comic-dl | awk -F': ' '/^Version/ {print $2}')"
    case "$installed" in
      "$EXPECTED" | "$EXPECTED-"*) ;;
      *) echo "error: installed version '$installed' != expected '$EXPECTED'" >&2; exit 1 ;;
    esac
  fi
fi

# --- CLI smoke ---
"$(command -v comic-dl)" --version | grep -q "^comic-dl "
comic-dl --list-sources >/dev/null
comic-dl config path >/dev/null
comic-dl help >/dev/null
echo "smoke test OK"
