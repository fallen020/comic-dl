#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest tests/ -q -p no:cacheprovider -n auto "$@"
