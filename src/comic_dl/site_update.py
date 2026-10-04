"""``comic-dl self site`` — per-site support versioning and checks.

Built-in adapters stay bundled with the core, but each carries its own
stable id and semantic version (see ``register_scraper`` metadata). This
module compares the installed adapters against the ``site-support.json``
manifest published with each GitHub release, reports per-site status, runs
optional ad-hoc live checks through the real adapters, and routes a site
update through the normal core-update strategies (:mod:`comic_dl.self_update`)
— because a bundled adapter update *is* a core update.

Manifest fetches reuse the scrapers' validated, rate-limited, cached fetch
path, so ``check`` never touches the network during ordinary downloads and a
lookup failure degrades to "unable to check", never to a false adapter-side
verdict.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curl_cffi.requests import AsyncSession

from . import __version__ as _version
from .config import cache_dir
from .errors import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_USAGE,
    SITE_GONE,
    SITE_INVALID_RESPONSE,
    SITE_NO_CHAPTERS,
    SITE_NO_PAGES,
    SITE_TIMEOUT,
    ScrapeError,
    ScrapeTimeout,
)
from .scrapers import list_sources
from .scrapers.base import BaseScraper
from .scrapers.registry import get_entry
from .self_update import (
    compare_versions,
    fetch_latest_release,
    run_update_command,
)
from .ui import JSON_SCHEMA_VERSION, console, print_dim, print_error, print_success, print_table
from .utils import http_client_args

MANIFEST_ASSET = "site-support.json"
#: How old a cached manifest may be before ``site list`` stops pretending it
#: knows the published state (a freshness window, not a network barrier).
_MANIFEST_MAX_AGE_SECONDS = 24 * 60 * 60

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Adapter-visible column sets: ``list`` reads local state (domains), while
# ``check`` compares two versions (installed vs published).
_LIST_COLUMNS = ("SITE", "VERSION", "DOMAINS", "STATUS")
_CHECK_COLUMNS = ("SITE", "INSTALLED", "AVAILABLE", "STATUS")


@dataclass(frozen=True)
class SiteRelease:
    """One adapter's entry in the published manifest."""

    version: str
    minimum_core_version: str
    status: str = "supported"
    test_url: str | None = None


@dataclass(frozen=True)
class SiteManifest:
    """Parsed ``site-support.json`` for one comic-dl release."""

    schema_version: int
    core_version: str
    sites: dict[str, SiteRelease]

    @classmethod
    def from_dict(cls, data: Any) -> SiteManifest | None:
        """Validate + build from parsed JSON; ``None`` when malformed."""
        if not isinstance(data, dict):
            return None
        if data.get("schema_version") != 1:
            return None
        core = data.get("core_version")
        raw_sites = data.get("sites")
        if not isinstance(core, str) or not _SEMVER_RE.match(core):
            return None
        if not isinstance(raw_sites, dict):
            return None
        sites: dict[str, SiteRelease] = {}
        for site_id, info in raw_sites.items():
            if not isinstance(info, dict):
                return None
            version = info.get("version")
            min_core = info.get("minimum_core_version")
            if not isinstance(version, str) or not _SEMVER_RE.match(version):
                return None
            if not isinstance(min_core, str) or not _SEMVER_RE.match(min_core):
                return None
            test_url = info.get("test_url")
            sites[str(site_id)] = SiteRelease(
                version=version,
                minimum_core_version=min_core,
                status=str(info.get("status") or "supported"),
                test_url=test_url if isinstance(test_url, str) else None,
            )
        return cls(schema_version=1, core_version=core, sites=sites)


@dataclass(frozen=True)
class LocalSite:
    """A built-in adapter as registered at startup."""

    site_id: str
    name: str
    domain: str
    version: str
    minimum_core_version: str
    test_url: str | None
    test_url_kind: str
    has_series: bool
    has_chapter: bool
    entry: Any


@dataclass(frozen=True)
class LiveResult:
    """Outcome of one ``--live`` adapter run."""

    status: str
    error_code: str | None = None


def local_sites() -> list[LocalSite]:
    """Every registered built-in adapter, ordered by site id."""
    out = []
    for entry in list_sources():
        if not entry.builtin or not entry.site_id:
            continue
        out.append(
            LocalSite(
                site_id=entry.site_id,
                name=entry.name,
                domain=entry.domain,
                version=entry.version,
                minimum_core_version=entry.minimum_core_version or "",
                test_url=entry.test_url,
                test_url_kind=entry.test_url_kind,
                has_series=entry.has_series,
                has_chapter=entry.has_chapter,
                entry=entry.instance,
            )
        )
    return sorted(out, key=lambda s: s.site_id)


def status_for(local: LocalSite, available: SiteRelease | None, installed_core: str) -> str:
    """Per-site status from installed + published versions.

    ``incompatible`` means the adapter would need a newer core than is
    installed; ``unable to check`` means no manifest entry exists yet.
    """
    if available is None:
        return "unable to check"
    if (
        local.minimum_core_version
        and compare_versions(installed_core, local.minimum_core_version) < 0
    ):
        return "incompatible"
    cmp = compare_versions(local.version, available.version)
    if cmp < 0:
        return "update available"
    if cmp > 0:
        return "unpublished"
    return "up to date"


# ---------------------------------------------------------------------------
# Manifest fetch + local cache


def _cache_path() -> Path:
    return cache_dir() / "site-support.json"


def _write_manifest_cache(manifest: SiteManifest) -> None:
    try:
        root = cache_dir()
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=root, prefix="site-support-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema_version": 1,
                    "core_version": manifest.core_version,
                    "sites": {
                        sid: {
                            "version": rel.version,
                            "minimum_core_version": rel.minimum_core_version,
                            "status": rel.status,
                            "test_url": rel.test_url,
                        }
                        for sid, rel in manifest.sites.items()
                    },
                },
                fh,
                indent=2,
            )
        os.replace(tmp, _cache_path())
    except OSError:
        # A read-only cache dir must never fail the check itself.
        pass


def _read_manifest_cache() -> SiteManifest | None:
    """The most recent manifest a ``check`` persisted, or ``None``."""
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    manifest = SiteManifest.from_dict(data)
    if manifest is None:
        return None
    try:
        age = _age_seconds(_cache_path())
    except OSError:
        age = 0
    if age > _MANIFEST_MAX_AGE_SECONDS:
        return None
    return manifest


def _age_seconds(path: Path) -> float:
    import time

    return time.time() - path.stat().st_mtime


async def fetch_site_manifest() -> SiteManifest | None:
    """The ``site-support.json`` of the latest release, or ``None``.

    Any failure — offline, no manifest asset, malformed payload —
    collapses to ``None``; the caller prints "unable to check".
    """
    release = await fetch_latest_release()
    if release is None:
        return None
    url = release.assets.get(MANIFEST_ASSET)
    if url is None:
        return None
    try:
        async with AsyncSession(**http_client_args(host="github.com")) as client:
            resp = await BaseScraper._timeout_get(url, client, use_cache=True)
    except Exception:
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return None
    manifest = SiteManifest.from_dict(data)
    if manifest is not None:
        _write_manifest_cache(manifest)
    return manifest


_GONE_STATUSES = frozenset({404, 410})


def _gone_status_code(exc: BaseException) -> int | None:
    """An HTTP 404/410 carried by ``exc`` (or its cause chain), if any.

    ``fetch_html_raw`` raises curl_cffi's ``HTTPError`` with the response
    attached, but adapters may wrap it before it reaches us — so the chain
    is walked instead of trusting the top-level type.
    """
    seen: set[int] = set()
    stack = [exc]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int) and status in _GONE_STATUSES:
            return status
        for nxt in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(nxt, BaseException):
                stack.append(nxt)
    return None


async def _live_check(site: LocalSite) -> LiveResult:
    """Run the real adapter against its declared test URL.

    Reuses the adapter's own extraction so the check cannot drift from
    production behavior; validates that a series/chapter with content came
    back. A site without a ``test_url`` is ``skipped``, not condemned — and
    a test URL that itself 404s/410s reports ``url gone`` (refresh the URL),
    never ``broken``.
    """
    if not site.test_url:
        return LiveResult("skipped")
    scraper = site.entry
    try:
        async with AsyncSession(**http_client_args(host=site.domain)) as client:
            if site.test_url_kind == "chapter" and site.has_chapter:
                meta = await scraper.scrape(site.test_url, client)
                if not getattr(meta, "images", None):
                    return LiveResult("broken", SITE_NO_PAGES)
            elif site.has_series:
                meta = await scraper.scrape_series(site.test_url, client)
                if not getattr(meta, "chapters", None):
                    return LiveResult("broken", SITE_NO_CHAPTERS)
            elif site.has_chapter:
                meta = await scraper.scrape(site.test_url, client)
                if not getattr(meta, "images", None):
                    return LiveResult("broken", SITE_NO_PAGES)
            else:
                return LiveResult("skipped")
    except ScrapeTimeout:
        return LiveResult("unavailable", SITE_TIMEOUT)
    except ScrapeError as exc:
        if _gone_status_code(exc) is not None:
            return LiveResult("url gone", SITE_GONE)
        return LiveResult("broken", exc.site_error_code)
    except TimeoutError:
        return LiveResult("unavailable", SITE_TIMEOUT)
    except Exception as exc:
        if _gone_status_code(exc) is not None:
            return LiveResult("url gone", SITE_GONE)
        return LiveResult("broken", SITE_INVALID_RESPONSE)
    return LiveResult("healthy")


def site_update_hint(domain: str) -> str:
    """A "newer site support may fix this" hint from the cached manifest.

    Network is never touched here: only the last persisted manifest is read,
    so a failed download cannot trigger a fetch and a cold cache returns "".
    """
    manifest = _read_manifest_cache()
    if manifest is None:
        return ""
    entry = get_entry(domain)
    if entry is None or entry.site_id is None:
        return ""
    available = manifest.sites.get(entry.site_id)
    if available is None or compare_versions(entry.version, available.version) >= 0:
        return ""
    return (
        f"A newer site-support version ({available.version}) is available and "
        f"may fix this.\nRun: comic-dl self site update {entry.site_id}"
    )


# ---------------------------------------------------------------------------
# Command entry points (return process exit codes)


def _tally_line(statuses: list[str]) -> str:
    """One-line rollup of table statuses ('' when there is nothing to act on).

    An all-clean table needs no summary; otherwise the tally names what
    differs so one bad row is visible inside a wall of green.
    """
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status] = counts.get(status, 0) + 1
    if set(counts) <= {"up to date"}:
        return ""
    parts = [f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return "  " + ", ".join(parts) + "."


def _manifest_age_line(manifest: SiteManifest | None) -> str:
    """Provenance for a cached manifest ('' when there is none to cite)."""
    if manifest is None:
        return ""
    try:
        age = _age_seconds(_cache_path())
    except OSError:
        age = 0
    if age < 3600:
        age_text = f"{int(age // 60)}m ago" if age >= 60 else "just now"
    elif age < 86400:
        age_text = f"{int(age // 3600)}h ago"
    else:
        age_text = f"{int(age // 86400)}d ago"
    return f"Status from cached manifest (core {manifest.core_version}, fetched {age_text})."


async def run_site_list_command(*, json_mode: bool) -> int:
    """``comic-dl self site list`` — installed adapters + cached status."""
    installed_core = _version
    cached = _read_manifest_cache()
    sites = local_sites()
    if json_mode:
        console.print(
            json.dumps(
                {
                    "schema_version": JSON_SCHEMA_VERSION,
                    "core_version": installed_core,
                    "sites": [
                        {
                            "site_id": s.site_id,
                            "name": s.name,
                            "domain": s.domain,
                            "version": s.version,
                            "minimum_core_version": s.minimum_core_version,
                            "status": status_for(
                                s,
                                cached.sites.get(s.site_id) if cached else None,
                                installed_core,
                            ),
                        }
                        for s in sites
                    ],
                },
                indent=2,
            ),
            soft_wrap=True,
        )
        return EXIT_OK
    rows = [
        [
            s.site_id,
            s.version,
            s.domain,
            status_for(s, cached.sites.get(s.site_id) if cached else None, installed_core),
        ]
        for s in sites
    ]
    statuses = [row[3] for row in rows]
    extra = _tally_line(statuses)
    if cached is not None:
        provenance = _manifest_age_line(cached)
        extra = f"{extra}\n{provenance}" if extra else provenance
    else:
        hint = "Status shows 'unable to check' until a site check is run."
        extra = f"{extra}\n{hint}" if extra else hint
    console.print()
    print_table(f"Installed site support ({len(sites)})", list(_LIST_COLUMNS), rows)
    if extra:
        print_dim(extra)
    return EXIT_OK


async def run_site_check_command(*, target: str | None, live: bool, json_mode: bool) -> int:
    """``self site check`` — installed vs the latest published manifest."""
    if live and target is None:
        print_error("--live requires a single site id.")
        print_dim("Run: comic-dl self site check <site-id> --live")
        return EXIT_USAGE

    manifest = await fetch_site_manifest()
    installed_core = _version
    sites = local_sites()
    if target is not None:
        sites = [s for s in sites if s.site_id == target]
        if not sites:
            print_error(f"unknown site id {target!r}.")
            print_dim("Run 'comic-dl self site list' to see the site ids.")
            return EXIT_USAGE

    rows: list[list[str]] = []
    json_sites: list[dict] = []
    # Without --live the whole point is version status, so a missing manifest
    # degrades the run; a live run is judged by its own result instead.
    degraded = manifest is None and not live
    live_map: dict[str, LiveResult] = {}
    if live and target is not None:
        live_map[target] = await _live_check(sites[0])

    unknown_sites: list[str] = []
    for s in sites:
        available = manifest.sites.get(s.site_id) if manifest else None
        status = status_for(s, available, installed_core)
        error_code: str | None = None
        if available is None and manifest is not None:
            unknown_sites.append(s.site_id)
        live_res = live_map.get(s.site_id)
        if live_res is not None:
            error_code = live_res.error_code
            status = f"live: {live_res.status}"
            if live_res.status not in ("healthy", "skipped"):
                degraded = True
        rows.append([s.site_id, s.version, available.version if available else "unknown", status])
        json_sites.append(
            {
                "site_id": s.site_id,
                "installed": s.version,
                "available": available.version if available else None,
                "status": status,
                "error_code": error_code,
            }
        )

    extra = ""
    if not json_mode:
        statuses = [row[3] for row in rows]
        extra = _tally_line(statuses)
        if unknown_sites:
            names = ", ".join(sorted(unknown_sites))
            missing = f"No manifest entry for: {names}."
            extra = f"{extra}\n{missing}" if extra else missing
        console.print()
    print_table(f"Site support check ({len(sites)})", list(_CHECK_COLUMNS), rows)
    if extra:
        print_dim(extra)

    if json_mode:
        console.print(
            json.dumps(
                {
                    "schema_version": JSON_SCHEMA_VERSION,
                    "core_version": installed_core,
                    "sites": json_sites,
                },
                indent=2,
            ),
            soft_wrap=True,
        )

    if degraded:
        print_dim("Install as-is; no files were modified by this check.")
        return EXIT_ERROR
    return EXIT_OK


async def run_site_update_command(*, target: str | None, all_sites: bool, yes: bool) -> int:
    """``self site update <id>`` / ``--all`` — route an adapter update through
    the core update strategy (bundled adapters update with the core)."""
    if target is not None and all_sites:
        print_error("Specify one site id or --all, not both.")
        return EXIT_USAGE
    if target is None and not all_sites:
        print_error("Specify one site id or --all.")
        print_dim("Run: comic-dl self site update <site-id>  or  comic-dl self site update --all")
        return EXIT_USAGE

    manifest = await fetch_site_manifest()
    if manifest is None:
        print_error("Unable to check for site-support updates.")
        print_dim("No network access, or the latest release has no site-support manifest.")
        return EXIT_ERROR

    sites = local_sites()
    installed_core = _version

    if target is not None:
        site = next((s for s in sites if s.site_id == target), None)
        if site is None:
            print_error(f"unknown site id {target!r}.")
            print_dim("Run 'comic-dl self site list' to see the site ids.")
            return EXIT_USAGE
        available = manifest.sites.get(site.site_id)
        if available is None or compare_versions(site.version, available.version) >= 0:
            print_success(f"{site.site_id} {site.version} is up to date.")
            return EXIT_OK
        selected = [(site, available)]
    else:
        selected = [
            (s, a)
            for s in sites
            for a in [manifest.sites.get(s.site_id)]
            if a is not None and compare_versions(s.version, a.version) < 0
        ]
        if not selected:
            print_success("All site support is up to date.")
            return EXIT_OK

    print()
    print_dim("Available site-support updates:")
    for s, a in selected:
        print_dim(f"    {s.site_id:<20} {s.version} -> {a.version}")
    print_dim("These adapters are bundled with comic-dl.")
    print_dim(f"Required core update: {installed_core} -> {manifest.core_version}.")

    if compare_versions(installed_core, manifest.core_version) >= 0:
        print_error(
            "The latest release lists newer adapters for this core version; "
            "reinstall to refresh them."
        )
        return EXIT_ERROR

    return await run_update_command(check=False, yes=yes)
