#!/usr/bin/env bash
# Run linting and static analysis for the package.
#
# Scope follows pyproject.toml ([tool.ruff] src, [tool.mypy] files), so the
# ruff invocation stays path-free to cover scripts/ and packaging/ too.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check
uv run mypy
uv run bandit -q -r src/comic_dl/
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck scripts/*.sh
fi
