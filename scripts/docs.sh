#!/usr/bin/env bash
# Lint Markdown sources. Link checking stays CI-side because it needs network.
set -euo pipefail
cd "$(dirname "$0")/.."
# Fail when the supported-sites tables drifted from the registry (a site was
# added/removed/capability-changed without regenerating the docs).
uv run python scripts/update-sites-docs.py --check
uv run python scripts/update-site-manifest.py --check
uv run --extra docs pymarkdown scan docs README.md
echo "Docs lint OK."
