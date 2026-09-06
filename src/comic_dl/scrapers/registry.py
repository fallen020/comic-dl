"""Source registration, plugin discovery, and URL-to-scraper routing."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any
from urllib.parse import urlparse

# Entry-point group used by third-party scraper plugins, e.g. in a plugin's
# ``pyproject.toml``::
#
#   [project.entry-points."comic_dl.sources"]
#   mysite = "myplugin.source:ExampleSource"
#
# The dotted value must resolve to a ``Source`` class (imported with no
# arguments) or to an iterable of such classes.
ENTRY_POINT_GROUP = "comic_dl.sources"


@dataclass(slots=True)
class SourceEntry:
    """Metadata + live instance for one registered source."""

    instance: Any
    domain: str
    capabilities: frozenset[str]
    name: str
    version: str
    builtin: bool
    priority: int = 0

    @property
    def has_chapter(self) -> bool:
        return "chapter" in self.capabilities

    @property
    def has_series(self) -> bool:
        return "series" in self.capabilities

    @property
    def source_id(self) -> str:
        """Stable 64-bit source identifier across versions.

        MD5 of the lowercased ``{name}|{domain}|{version}`` tuple, first
        64 bits (16 hex chars). Stored on series rows for future
        source-level features; not surfaced to users yet.
        """
        # Identity hash, not security.
        digest = hashlib.md5(  # nosec B324
            f"{self.name.strip().lower()}|{self.domain.strip().lower()}"
            f"|{self.version.strip()}".encode()
        ).hexdigest()
        return digest[:16]


_sourcemap: dict[str, SourceEntry] = {}
_loaded_plugins: set[str] = set()

# The generic fallback scraper, stored apart from the domain map. It is
# intentionally *not* a SourceEntry: domain-keyed lookups can never reach it,
# and listing it would make it sort first in ``list_sources()``. The CLI
# consults it explicitly only after every domain lookup has returned None.
_generic: Any | None = None


def register(
    instance: Any,
    *,
    domain: str,
    capabilities: set[str] | frozenset[str],
    name: str,
    version: str,
    builtin: bool,
    priority: int = 0,
) -> SourceEntry:
    """Register ``instance`` for ``domain`` with deterministic conflict handling.

    A domain may only own one source. On a duplicate registration the higher
    ``priority`` wins; on a tie the first registration is kept, so built-ins
    (registered at startup, priority 0) win unless a plugin opts in with a
    strictly higher priority.
    """
    existing = _sourcemap.get(domain)
    if existing is not None and priority <= existing.priority:
        return existing
    entry = SourceEntry(
        instance=instance,
        domain=domain,
        capabilities=frozenset(capabilities),
        name=name,
        version=version,
        builtin=builtin,
        priority=priority,
    )
    _sourcemap[domain] = entry
    return entry


def register_builtin(
    instance: Any,
    *,
    domain: str,
    capabilities: set[str] | frozenset[str],
    name: str,
    version: str,
) -> SourceEntry:
    """Register a built-in source with the default priority.

    Thin wrapper over :func:`register` marking the entry as built-in and
    giving it the lowest (0) priority so plugins can override it.
    """
    return register(
        instance,
        domain=domain,
        capabilities=capabilities,
        name=name,
        version=version,
        builtin=True,
        priority=0,
    )


def register_scraper(
    *,
    domain: str,
    capabilities: set[str] | None = None,
) -> Callable[[type], type]:
    """Decorator registering a built-in scraper class for ``domain``."""

    def decorator(cls: type) -> type:
        instance = cls()
        register_builtin(
            instance,
            domain=domain,
            capabilities=set(capabilities or {"chapter"}),
            name=str(getattr(cls, "name", "") or cls.__name__),
            version=str(getattr(cls, "version", "") or "builtin"),
        )
        return cls

    return decorator


def get_entry(domain: str) -> SourceEntry | None:
    """Return the source registered for ``domain``, if any."""
    return _sourcemap.get(domain)


def get_chapter_scraper(domain: str) -> Any | None:
    """Return the chapter-capable source instance for ``domain``, if any."""
    entry = _sourcemap.get(domain)
    if entry is not None and entry.has_chapter:
        return entry.instance
    return None


def get_series_scraper(domain: str) -> Any | None:
    """Return the series-capable source instance for ``domain``, if any."""
    entry = _sourcemap.get(domain)
    if entry is not None and entry.has_series:
        return entry.instance
    return None


def register_generic(instance: Any) -> None:
    """Register the single generic fallback scraper instance.

    The generic scraper lives outside the domain map; ``source_for_url`` and
    ``list_sources()`` never see it. It is the explicit last resort consulted
    by the CLI after every domain-keyed lookup returns ``None``.
    """
    global _generic
    _generic = instance


def get_generic_scraper() -> Any | None:
    """Return the registered generic fallback scraper, if any."""
    return _generic


def instances_for(capability: str) -> dict[str, Any]:
    """Return ``{domain: instance}`` for every source with ``capability``."""
    return {
        domain: e.instance
        for domain, e in _sourcemap.items()
        if capability in e.capabilities
    }


def list_sources() -> list[SourceEntry]:
    """Return registered sources, sorted by domain."""
    return sorted(_sourcemap.values(), key=lambda e: e.domain)


def _netloc_of(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or parsed.netloc.split(":")[0]
    return host.lower()


def url_in_domain(url: str, domain: str) -> bool:
    """True when ``url``'s host equals or is a subdomain of ``domain``."""
    host = _netloc_of(url)
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def source_for_url(url: str, *, series: bool) -> SourceEntry | None:
    """Resolve ``url`` to the best chapter/series source matching its host."""
    for entry in list_sources():
        wanter = entry.has_series if series else entry.has_chapter
        if not wanter:
            continue
        matcher = getattr(entry.instance, "matches_url", None)
        if callable(matcher):
            if matcher(url):
                return entry
            continue
        if url_in_domain(url, entry.domain):
            return entry
    return None


def get_source_for_url(url: str) -> SourceEntry | None:
    """Resolve ``url`` to its chapter source, or ``None`` if unsupported."""
    return source_for_url(url, series=False)


def get_series_source_for_url(url: str) -> SourceEntry | None:
    """Resolve ``url`` to its series source, or ``None`` if unsupported."""
    return source_for_url(url, series=True)


def load_plugins(group: str = ENTRY_POINT_GROUP) -> list[SourceEntry]:
    """Discover and register third-party sources from packaging entry points."""
    if group in _loaded_plugins:
        return [e for e in list_sources() if not e.builtin]
    _loaded_plugins.add(group)

    discovered = []
    eps = entry_points().select(group=group)
    for ep in eps:
        try:
            loaded = ep.load()
        # A broken plugin must not sink the CLI.
        except Exception:  # nosec B112
            continue
        classes = loaded if isinstance(loaded, (list, tuple)) else [loaded]
        for cls in classes:
            if not isinstance(cls, type):
                continue
            caps = getattr(cls, "capabilities", None) or {"chapter"}
            domain = getattr(cls, "domain", None)
            if not domain:
                continue
            try:
                priority = int(getattr(cls, "priority", 0) or 0)
            except (TypeError, ValueError):
                # A malformed plugin priority must not sink the CLI.
                priority = 0
            entry = register(
                cls(),
                domain=str(domain),
                capabilities=set(caps),
                name=str(getattr(cls, "name", "") or cls.__name__),
                version=str(getattr(cls, "version", "") or "plugin"),
                builtin=False,
                priority=priority,
            )
            discovered.append(entry)
    return discovered
