"""Site-specific scraper implementations.

One module per supported site, each decorated with ``@register_scraper``.
Modules are discovered automatically: every ``*.py`` file in this package
(except ``_``-prefixed helpers) is imported so its registry decorator runs.
Importing this package (which ``comic_dl.scrapers`` does) therefore wires
every site into the shared registry with no per-site import to maintain.

A module that fails to import fails startup: dropping a built-in scraper on
the floor would make it silently vanish from ``--list-sources`` and from URL
routing, which is the drift this auto-discovery removes.
"""

from __future__ import annotations

import importlib
import pkgutil

from ...errors import SiteRegistryError
from ..registry import list_sources as _list_sources

# Discovered in sorted order so registrations (and any first-wins domain tie)
# are deterministic across runs regardless of filesystem enumeration order.
_MODULE_NAMES = sorted(
    m.name
    for m in pkgutil.iter_modules(__path__)
    if not m.name.startswith("_")
)

for _name in _MODULE_NAMES:
    _before = len(_list_sources())
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as exc:
        raise SiteRegistryError(
            f"Failed to load the built-in scraper module {_name!r}.",
            hint=(
                "It may not be incompatible with this version of comic-dl; "
                "try reinstalling or removing the file. Cause: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc
    if len(_list_sources()) == _before:
        raise SiteRegistryError(
            f"The built-in scraper module {_name!r} registered no source.",
            hint=(
                "Add the `@register_scraper` decorator, or give the module a "
                "leading underscore to opt out of discovery."
            ),
        )

__all__ = list(_MODULE_NAMES)
