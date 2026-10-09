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
    site_id: str | None = None
    minimum_core_version: str | None = None
    test_url: str | None = None
    test_url_kind: str = "series"
    display_name: str = ""
    chapter_url_pattern: str = ""
    series_url_pattern: str = ""
    content_warning: str = ""

    @property
    def has_chapter(self) -> bool:
        return "chapter" in self.capabilities

    @property
    def has_series(self) -> bool:
        return "series" in self.capabilities

    @property
    def source_id(self) -> str:
        """Stable 64-bit source identifier across versions.

        SHA-256 of the lowercased ``{name}|{domain}|{version}`` tuple, first
        64 bits (16 hex chars). Stored on series rows for future
        source-level features; not surfaced to users yet.
        """
        digest = hashlib.sha256(
            f"{self.name.strip().lower()}|{self.domain.strip().lower()}"
            f"|{self.version.strip()}".encode()
        ).hexdigest()
        return digest[:16]


_sourcemap: dict[str, SourceEntry] = {}
_loaded_plugins: set[str] = set()
# Entry-point name -> reason, for plugins whose class failed to load. Kept
# separate from _sourcemap so a broken plugin still shows up in `plugin list`
# instead of vanishing silently.
_plugin_load_errors: dict[str, str] = {}


def _ep_key(ep: object) -> str:
    """Stable identifier for an entry point (real ones expose ``name``)."""
    return getattr(ep, "name", None) or getattr(ep, "value", None) or repr(ep)


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
    site_id: str | None = None,
    minimum_core_version: str | None = None,
    test_url: str | None = None,
    test_url_kind: str = "series",
    display_name: str = "",
    chapter_url_pattern: str = "",
    series_url_pattern: str = "",
    content_warning: str = "",
) -> SourceEntry:
    """Register ``instance`` for ``domain`` with deterministic conflict handling.

    A domain may only own one source. On a duplicate registration the higher
    ``priority`` wins; on a tie the first registration is kept, so built-ins
    (registered at startup, priority 0) win unless a plugin opts in with a
    strictly higher priority.

    ``site_id``/``minimum_core_version`` are part of the public site-support
    contract (see ``comic_dl.site_update``); they are optional for plugins and
    mandatory for built-ins, which the manifest generator enforces.
    ``test_url_kind`` names which page ``test_url`` points at (``"series"``
    or ``"chapter"``); chapter permalinks are preferred since taxonomy
    listings rot faster than chapter URLs.
    """
    existing = _sourcemap.get(domain)
    if existing is not None and priority <= existing.priority:
        return existing
    if builtin and site_id is not None:
        _check_builtin_id_unique(domain, site_id)
    if test_url_kind not in ("series", "chapter"):
        from ..errors import SiteRegistryError

        raise SiteRegistryError(
            f"Built-in scraper for {domain!r} declares an invalid test_url_kind {test_url_kind!r}.",
            hint="Use 'series' or 'chapter'.",
        )
    entry = SourceEntry(
        instance=instance,
        domain=domain,
        capabilities=frozenset(capabilities),
        name=name,
        version=version,
        builtin=builtin,
        priority=priority,
        site_id=site_id,
        minimum_core_version=minimum_core_version,
        test_url=test_url,
        test_url_kind=test_url_kind,
        display_name=display_name,
        chapter_url_pattern=chapter_url_pattern,
        series_url_pattern=series_url_pattern,
        content_warning=content_warning,
    )
    _sourcemap[domain] = entry
    return entry


#: Built-in site id -> registering domain, for duplicate-id fail-fast.
_builtin_ids: dict[str, str] = {}


def _check_builtin_id_unique(domain: str, site_id: str) -> None:
    """Reject a built-in whose declared ``id`` breaks the site-support model.

    Site ids are the stable public key per adapter: they must be unique across
    built-ins and stay lowercase-slug so a manifest id is never ambiguous.
    """
    from ..errors import SITE_ID_RE, SiteRegistryError

    if not SITE_ID_RE.match(site_id):
        raise SiteRegistryError(
            f"Built-in scraper for {domain!r} declares an invalid site id {site_id!r}.",
            hint="Use a stable lowercase-slug id (e.g. 'manga-example').",
        )
    prior = _builtin_ids.get(site_id)
    if prior is not None and prior != domain:
        raise SiteRegistryError(
            f"Built-in scrapers {prior!r} and {domain!r} both declare site id {site_id!r}.",
            hint="Every site adapter needs its own stable id.",
        )
    _builtin_ids[site_id] = domain


def register_builtin(
    instance: Any,
    *,
    domain: str,
    capabilities: set[str] | frozenset[str],
    name: str,
    version: str,
    site_id: str | None = None,
    minimum_core_version: str | None = None,
    test_url: str | None = None,
    test_url_kind: str = "series",
    display_name: str = "",
    chapter_url_pattern: str = "",
    series_url_pattern: str = "",
    content_warning: str = "",
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
        site_id=site_id,
        minimum_core_version=minimum_core_version,
        test_url=test_url,
        test_url_kind=test_url_kind,
        display_name=display_name,
        chapter_url_pattern=chapter_url_pattern,
        series_url_pattern=series_url_pattern,
        content_warning=content_warning,
    )


def register_scraper(
    *,
    domain: str,
    capabilities: set[str] | None = None,
) -> Callable[[type], type]:
    """Decorator registering a built-in scraper class for ``domain``.

    The decorated class may declare ``site_id``, ``version``,
    ``minimum_core_version``, ``test_url``, ``test_url_kind``,
    ``display_name``, ``chapter_url_pattern``, ``series_url_pattern``, and
    ``content_warning`` attributes; they flow into the registry and feed the
    site-support manifest and the supported-sites docs (see
    ``comic_dl.site_update``).
    """

    def decorator(cls: type) -> type:
        from ..errors import VERSION_RE, SiteRegistryError

        if not (domain or "").strip():
            raise SiteRegistryError(
                f"Built-in scraper {cls.__name__!r} declares an empty domain.",
                hint="Pass the site's registrable domain, e.g. domain='manga-example.com'.",
            )
        version = str(getattr(cls, "version", "") or "builtin")
        site_id = getattr(cls, "site_id", None)
        min_core = getattr(cls, "minimum_core_version", None)
        test_url = getattr(cls, "test_url", None)
        test_url_kind = getattr(cls, "test_url_kind", None) or "series"
        if isinstance(site_id, str) and not site_id.strip():
            site_id = None
        if isinstance(min_core, str) and not min_core.strip():
            min_core = None
        if version != "builtin" and not VERSION_RE.match(version):
            raise ValueError(
                f"{domain!r} declares an invalid site version {version!r}; use MAJOR.MINOR.PATCH."
            )
        if min_core is not None and not VERSION_RE.match(str(min_core)):
            raise ValueError(
                f"{domain!r} declares an invalid minimum_core_version "
                f"{min_core!r}; use MAJOR.MINOR.PATCH."
            )
        if site_id is None:
            raise SiteRegistryError(
                f"Built-in scraper for {domain!r} declares no site_id.",
                hint="Add a stable lowercase-slug id (e.g. site_id='manga-example').",
            )
        name = str(getattr(cls, "name", "") or cls.__name__)
        if not name.strip():
            raise SiteRegistryError(
                f"Built-in scraper for {domain!r} declares an empty name.",
                hint="Add a human-readable name (e.g. name='Manga Example').",
            )
        caps = set(capabilities or {"chapter"})
        display_name = str(getattr(cls, "display_name", "") or "")
        chapter_pattern = str(getattr(cls, "chapter_url_pattern", "") or "")
        series_pattern = str(getattr(cls, "series_url_pattern", "") or "")
        if not display_name.strip():
            raise SiteRegistryError(
                f"Built-in scraper for {domain!r} declares an empty display_name.",
                hint="Add the reader-facing name (e.g. display_name='Manga Example').",
            )
        content_warning = str(getattr(cls, "content_warning", "") or "").strip()
        if content_warning not in ("safe", "mixed", "nsfw"):
            raise SiteRegistryError(
                f"Built-in scraper for {domain!r} declares an invalid content_warning "
                f"{content_warning!r}.",
                hint="Use 'safe', 'mixed', or 'nsfw'.",
            )
        instance = cls()
        _validate_source_shape(domain, instance, caps)
        register_builtin(
            instance,
            domain=domain,
            capabilities=caps,
            name=name,
            version=version,
            site_id=site_id,
            minimum_core_version=min_core,
            test_url=test_url,
            test_url_kind=test_url_kind,
            display_name=display_name,
            chapter_url_pattern=chapter_pattern,
            series_url_pattern=series_pattern,
            content_warning=content_warning,
        )
        return cls

    return decorator


def _validate_source_shape(domain: str, instance: Any, caps: set[str]) -> None:
    """Check that declared capabilities and metadata agree at registration.

    A capability is only paired with the registry at decoration, so ``series``
    without ``scrape_series`` is a registration error the packager sees, not
    a runtime surprise. An empty ``test_url`` is allowed (a Cloudflare-walled
    site has no verifiable public URL).
    """
    from ..errors import SiteRegistryError

    if "chapter" in caps and not callable(getattr(instance, "scrape", None)):
        raise SiteRegistryError(
            f"{domain!r} declares the 'chapter' capability but no scrape().",
            hint="Add scrape(), or drop 'chapter' from the capabilities.",
        )
    missing = [
        name
        for name in ("scrape_series", "matches_series_url")
        if "series" in caps and not callable(getattr(instance, name, None))
    ]
    if missing:
        raise SiteRegistryError(
            f"{domain!r} declares the 'series' capability but is missing {', '.join(missing)}().",
            hint=(
                "Add the missing method(s), or drop 'series' from the "
                "capabilities; series URLs are unroutable without "
                "matches_series_url()."
            ),
        )
    matcher = getattr(instance, "matches_url", None)
    test_url = getattr(instance, "test_url", None)
    if callable(matcher) and isinstance(test_url, str) and test_url and not matcher(test_url):
        raise SiteRegistryError(
            f"{domain!r} declares test_url {test_url!r} that its own matches_url() rejects.",
            hint="Fix matches_url()/the URL regexes, or the test_url.",
        )
    for cap, field in (
        ("chapter", "chapter_url_pattern"),
        ("series", "series_url_pattern"),
    ):
        if cap in caps and not str(getattr(instance, field, "") or "").strip():
            raise SiteRegistryError(
                f"{domain!r} declares the {cap!r} capability but no {field}.",
                hint=f"Add the docs URL shape (e.g. {field}='/{cap}/{{slug}}/'), or drop {cap!r}.",
            )


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
    return {domain: e.instance for domain, e in _sourcemap.items() if capability in e.capabilities}


def list_sources() -> list[SourceEntry]:
    """Return registered sources, sorted by domain."""
    return sorted(_sourcemap.values(), key=lambda e: e.domain)


def plugin_load_errors() -> dict[str, str]:
    """Copy of per-entry-point plugin load failures (``name -> reason``)."""
    return dict(_plugin_load_errors)


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
        except Exception as exc:
            # A broken plugin must not sink the CLI, but its failure is
            # recorded so `plugin list` can point the author at it.
            _plugin_load_errors[_ep_key(ep)] = f"{type(exc).__name__}: {exc}"
            continue
        _plugin_load_errors.pop(_ep_key(ep), None)
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
            try:
                entry = register(
                    cls(),
                    domain=str(domain),
                    capabilities=set(caps),
                    name=str(getattr(cls, "name", "") or cls.__name__),
                    version=str(getattr(cls, "version", "") or "plugin"),
                    builtin=False,
                    priority=priority,
                )
            except Exception as exc:
                # Same skip-and-report contract as a failed import above:
                # never break `import comic_dl` over a third-party plugin.
                key = f"{_ep_key(ep)}:{getattr(cls, '__name__', cls)}"
                _plugin_load_errors[key] = f"{type(exc).__name__}: {exc}"
                continue
            discovered.append(entry)
    return discovered
