"""Contract test for every built-in site scraper.

Loops the live registry and checks routing metadata without network:
every builtin carries identity attrs, capabilities match methods, its own
``test_url`` routes, and foreign domains never match.

New breakage must fail here, not at download time. Sites that fail today
are listed in ``KNOWN_VIOLATIONS``; each later plan step removes entries
until the dict is empty, then the constant goes with it.
"""

from __future__ import annotations

import re

import pytest

from comic_dl.scrapers import list_sources
from comic_dl.scrapers.registry import url_in_domain

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_SITE_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# ponytail: allowlist, delete entries as sites migrate, delete dict at empty
KNOWN_VIOLATIONS = {
    "asurascans.com": {"series-matcher"},
    "flamecomics.xyz": {"series-matcher"},
    "fsicomics.com": {"series-matcher"},
    "gedecomix.com": {"series-matcher"},
    "kagane.to": {"series-matcher"},
    "webtoons.com": {"series-matcher"},
    "weebcentral.com": {"series-matcher"},
    "genztoons.org": {"test-url"},
    "hdporncomics.com": {"test-url"},
}


def _builtins():
    """Built-in entries from real site modules (skips test registrations)."""
    return sorted(
        (
            e
            for e in list_sources()
            if e.builtin and e.instance.__class__.__module__.startswith("comic_dl.scrapers.sites")
        ),
        key=lambda e: e.domain,
    )


def _violated(domain: str, check: str) -> bool:
    return check in KNOWN_VIOLATIONS.get(domain, set())


ENTRIES = _builtins()
IDS = [e.domain for e in ENTRIES]


def test_all_site_modules_registered():
    assert ENTRIES, "no built-in site scrapers discovered"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_metadata(entry):
    domain = entry.domain
    assert entry.domain
    assert entry.name
    assert entry.site_id and _SITE_ID_RE.match(entry.site_id), domain
    assert entry.version and _VERSION_RE.match(entry.version), domain
    assert entry.capabilities <= {"chapter", "series"}, domain
    assert entry.test_url_kind in ("series", "chapter"), domain
    if _violated(domain, "test-url"):
        pytest.skip(f"known violation: {domain} missing test_url")
    assert entry.test_url, f"{domain} declares no test_url"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_capabilities_match_methods(entry):
    domain = entry.domain
    inst = entry.instance
    if "chapter" in entry.capabilities:
        assert callable(getattr(inst, "scrape", None)), domain
    if "series" in entry.capabilities:
        assert callable(getattr(inst, "scrape_series", None)), domain


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_test_url_routes(entry):
    domain = entry.domain
    if not entry.test_url or _violated(domain, "test-url"):
        pytest.skip(f"known violation: {domain} missing test_url")
    matcher = getattr(entry.instance, "matches_url", None)
    if callable(matcher):
        assert matcher(entry.test_url), f"{domain}: matches_url rejects its test_url"
    else:
        assert url_in_domain(entry.test_url, entry.domain), domain


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_foreign_domain_rejected(entry):
    matcher = getattr(entry.instance, "matches_url", None)
    if not callable(matcher):
        pytest.skip("host-suffix routing")
    assert not matcher("https://other.example/x"), entry.domain


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_series_matcher(entry):
    domain = entry.domain
    if "series" not in entry.capabilities:
        pytest.skip("chapter-only source")
    if _violated(domain, "series-matcher"):
        pytest.skip(f"known violation: {domain} relies on static series map")
    assert callable(getattr(entry.instance, "matches_series_url", None)), domain
