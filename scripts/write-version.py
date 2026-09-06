#!/usr/bin/env python3
"""Regenerate ``src/comic_dl/_version.py`` from ``pyproject.toml``.

Mirrors the hatchling build hook for contexts that do not build a wheel
(PyInstaller, drift checks). Run from anywhere inside the repo:

    uv run scripts/write-version.py            # regenerate
    uv run scripts/write-version.py --check    # exit 1 on mismatch

Exits 0 on success and prints the version written (or, with ``--check``,
"ok"). See ``packaging/versioning.py`` for the contract.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packaging"))

import versioning  # noqa: E402  (path fixtures before the import)

DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_VERSION_FILE = REPO_ROOT / "src" / "comic_dl" / "_version.py"


def main() -> int:
    """Argparse entry: regenerate ``_version.py`` or verify it is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=DEFAULT_PYPROJECT,
        help="path to pyproject.toml (default: the repo's)",
    )
    parser.add_argument(
        "--version-file",
        type=Path,
        default=DEFAULT_VERSION_FILE,
        help="output _version.py path (default: the repo's)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the file is current instead of rewriting it",
    )
    args = parser.parse_args()

    if args.check:
        problem = versioning.check_version_file(args.pyproject, args.version_file)
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return 1
        print("ok")
        return 0

    try:
        version = versioning.write_version_file(args.pyproject, args.version_file)
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
