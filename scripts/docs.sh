#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python scripts/update-sites-docs.py --check
uv run python scripts/update-site-manifest.py --check
uv run python scripts/check-docs-mirror.py --check
uv run --extra docs pymarkdown scan docs README.md
echo "Docs lint OK."
