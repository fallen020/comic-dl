#!/usr/bin/env bash
# Build the sdist + wheel with uv.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run scripts/write-version.py --check
rm -rf dist/
uv build
echo "Built artifacts into dist/."
