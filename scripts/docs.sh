#!/usr/bin/env bash
# Lint Markdown sources. Link checking stays CI-side because it needs network.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run --extra docs pymarkdown scan docs README.md
echo "Docs lint OK."
