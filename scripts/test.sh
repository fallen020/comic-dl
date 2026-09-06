#!/usr/bin/env bash
# Run the test suite.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest tests/ -q "$@"
