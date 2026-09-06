"""Hatchling build hook that regenerates ``src/comic_dl/_version.py``.

Wired up under ``[tool.hatch.build.hooks.custom]``. ``initialize`` runs before
every build target (wheel, sdist, editable), so the version shipped in the
artifact always matches the ``pyproject.toml`` that produced it - including
release builds that stamp a tag version into a copy of ``pyproject.toml``
(deb/rpm/Arch scripts).
"""

from __future__ import annotations

import sys
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import (  # type: ignore[import-not-found]
    BuildHookInterface,
)

_PACKAGING_DIR = Path(__file__).resolve().parent.parent
if str(_PACKAGING_DIR) not in sys.path:
    sys.path.insert(0, str(_PACKAGING_DIR))

import versioning  # noqa: E402  (sys.path fixture above)


class VersionBuildHook(BuildHookInterface):
    """Regenerate the static version module from ``[project].version``."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        # NOTE: `version` here is the *build target* name ("standard"/"editable"),
        # not the package version. The resolved project version is authoritative.
        package_version = self.metadata.version
        root = Path(self.root)
        out = root / "src" / "comic_dl" / "_version.py"
        rendered = versioning.render_version_file(package_version)
        if out.exists() and out.read_text(encoding="utf-8") == rendered:
            return
        out.write_text(rendered, encoding="utf-8")
        self.app.display_warning(
            f"version hook regenerated {out.relative_to(root)} (v{package_version})"
        )
