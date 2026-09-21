#!/usr/bin/env bash
# Run the test suite distributed across all CPU cores.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest tests/ -q -p no:cacheprovider -n auto "$@"
