#!/usr/bin/env bash
# Run linting and static analysis for the package.
#
# Scope is defined in pyproject.toml ([tool.ruff] src, [tool.mypy] files).
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check src/ tests/
uv run mypy
uv run bandit -q -r src/comic_dl/
