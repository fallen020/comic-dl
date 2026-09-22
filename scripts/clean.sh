#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf \
  build \
  dist \
  .coverage \
  .coverage.* \
  coverage.xml \
  htmlcov \
  .pytest_cache \
  .mypy_cache \
  .ruff_cache
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
echo "Cleaned build artifacts and caches."
