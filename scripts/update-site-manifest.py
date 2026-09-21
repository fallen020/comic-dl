#!/usr/bin/env python3
"""Regenerate the ``site-support.json`` site-support manifest.

The manifest is derived entirely from the live built-in scraper registry
(each adapter's declared ``site_id`` / ``version`` / ``minimum_core_version``
/ ``test_url``), so it can never drift from the code it describes — exactly
like ``update-sites-docs.py`` regenerates the supported-sites tables.

Run without arguments to rewrite ``site-support.json`` in place (plus
``--check`` to verify it is current; used by ``scripts/docs.sh`` and CI so
adapter bumps without a manifest refresh fail the gate). The release workflow
copies the committed manifest into the release asset set, where
``comic-dl self site check`` reads it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the package importable from a bare checkout (CI, pre-commit).
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from comic_dl import __version__  # noqa: E402
from comic_dl.scrapers import list_sources  # noqa: E402

MANIFEST = _REPO / "site-support.json"
SCHEMA_VERSION = 1


def build_manifest() -> dict:
    """Assemble the manifest payload from the live built-in registry."""
    sites: dict[str, dict] = {}
    for entry in list_sources():
        if not entry.builtin:
            continue
        if not entry.site_id:
            raise SystemExit(
                f"built-in scraper for {entry.domain} declares no site_id; "
                "add `site_id` to the scraper class."
            )
        if not entry.minimum_core_version:
            raise SystemExit(
                f"built-in scraper {entry.site_id} declares no "
                "minimum_core_version; add it to the scraper class."
            )
        if entry.version in ("", "builtin"):
            raise SystemExit(
                f"built-in scraper {entry.site_id} declares no version; "
                'add `version = "X.Y.Z"` to the scraper class.'
            )
        sites[entry.site_id] = {
            "version": entry.version,
            "minimum_core_version": entry.minimum_core_version,
            "status": "supported",
        }
        if entry.test_url:
            sites[entry.site_id]["test_url"] = entry.test_url
    return {
        "schema_version": SCHEMA_VERSION,
        "core_version": __version__,
        "sites": sites,
    }


def main() -> int:
    """Write ``site-support.json``, or verify it when ``--check``."""
    parser = argparse.ArgumentParser(description="(re)generate site-support.json")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed manifest is current; exit nonzero on drift",
    )
    args = parser.parse_args()

    payload = build_manifest()
    body = json.dumps(payload, indent=2) + "\n"
    if args.check:
        if not MANIFEST.exists():
            print(f"error: {MANIFEST.name} is missing; run without --check to generate it")
            return 1
        current = MANIFEST.read_text(encoding="utf-8")
        if current != body:
            print(
                f"error: {MANIFEST.name} drifted from the registry; "
                "run `uv run python scripts/update-site-manifest.py`"
            )
            return 1
        print("Site-support manifest OK.")
        return 0

    MANIFEST.write_text(body, encoding="utf-8")
    print(f"Wrote {MANIFEST.relative_to(_REPO)} ({len(payload['sites'])} sites).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
