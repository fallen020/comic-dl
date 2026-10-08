"""Plugin-discovery and URL routing tests for :mod:`comic_dl.scrapers.registry`."""

from __future__ import annotations

import pytest

from comic_dl.errors import SiteRegistryError
from comic_dl.scrapers import registry
from comic_dl.scrapers.base import BaseScraper
from comic_dl.scrapers.registry import load_plugins, source_for_url


class _Ep:
    def __init__(self, loaded):
        self._loaded = loaded

    def load(self):
        return self._loaded


class _EpSet:
    def __init__(self, eps):
        self._eps = eps

    def select(self, group):
        return [e for e in self._eps if e._group == group]


class _BrokenSource:
    domain = "broken.example"
    name = "broken"
    version = "1"
    # Non-numeric priority used to abort the old whole-plugin load.
    priority = "not-a-number"


class _GoodSource:
    domain = "good.example"
    name = "good"
    version = "1"
    priority = 7


class _TrickySource:
    domain = "tricky.example"
    name = "tricky"
    version = "1"
    priority = {"weird": True}


@pytest.fixture(autouse=True)
def _restore_registry_state():
    # Plugins register into the global source map that built-ins populate at
    # import, so snapshot and restore rather than wiping it.
    snapshot = dict(registry._sourcemap)
    registry._loaded_plugins.clear()
    yield
    registry._sourcemap.clear()
    registry._sourcemap.update(snapshot)
    registry._loaded_plugins.clear()


def _install_eps(monkeypatch, entries):
    made = []
    for group, obj in entries:
        ep = _Ep(obj)
        ep._group = group
        made.append(ep)
    monkeypatch.setattr(registry, "entry_points", lambda: _EpSet(made))


def test_malformed_priority_falls_back_to_zero(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _BrokenSource)])
    result = load_plugins(registry.ENTRY_POINT_GROUP)
    assert len(result) == 1
    assert result[0].priority == 0


def test_dict_priority_falls_back_to_zero(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _TrickySource)])
    result = load_plugins(registry.ENTRY_POINT_GROUP)
    assert len(result) == 1
    assert result[0].priority == 0


def test_valid_priority_is_kept(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _GoodSource)])
    result = load_plugins(registry.ENTRY_POINT_GROUP)
    assert len(result) == 1
    assert result[0].priority == 7


def test_broken_and_good_plugins_coexist(monkeypatch):
    _install_eps(
        monkeypatch,
        [
            (registry.ENTRY_POINT_GROUP, _BrokenSource),
            (registry.ENTRY_POINT_GROUP, _GoodSource),
        ],
    )
    result = load_plugins(registry.ENTRY_POINT_GROUP)
    assert {e.domain for e in result} == {"broken.example", "good.example"}


def test_load_plugins_caches_per_group(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _GoodSource)])
    first = load_plugins(registry.ENTRY_POINT_GROUP)
    second = load_plugins(registry.ENTRY_POINT_GROUP)
    assert first == second


def test_source_for_url_routes_to_registered_plugin(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _GoodSource)])
    load_plugins(registry.ENTRY_POINT_GROUP)
    entry = source_for_url("https://good.example/chapter/1", series=False)
    assert entry is not None
    assert entry.priority == 7


def test_subclass_rejects_bad_semver_at_definition():
    with pytest.raises(ValueError, match="PATCH"):

        class _BadVer(BaseScraper):
            version = "1.2"


def test_subclass_rejects_bad_site_id_at_definition():
    with pytest.raises(ValueError, match="lowercase-slug"):

        class _BadId(BaseScraper):
            site_id = "Not A Slug!"


def test_series_capability_without_methods_rejected(monkeypatch):
    from comic_dl.scrapers.registry import register_scraper

    with pytest.raises(SiteRegistryError, match="scrape_series"):

        @register_scraper(domain="nometa.example", capabilities={"chapter", "series"})
        class _NoSeries(BaseScraper):
            site_id = "no-series"
            display_name = "No Series"
            version = "1.0.0"

            async def scrape(self, url, client):
                raise AssertionError("unreachable")


def test_self_rejecting_test_url_rejected(monkeypatch):
    from comic_dl.scrapers.registry import register_scraper

    with pytest.raises(SiteRegistryError, match="matches_url"):

        @register_scraper(domain="badurl.example")
        class _BadUrl(BaseScraper):
            site_id = "bad-url"
            display_name = "Bad Url"
            version = "1.0.0"
            test_url = "https://badurl.example/series/1"

            @staticmethod
            def matches_url(url: str) -> bool:
                return False

            async def scrape(self, url, client):
                raise AssertionError("unreachable")


class _ExplodingSource:
    domain = "boom.example"
    name = "boom"
    version = "1.0.0"

    def __init__(self):
        raise RuntimeError("init blew up")


def test_exploding_plugin_skipped_and_reported(monkeypatch):
    _install_eps(monkeypatch, [(registry.ENTRY_POINT_GROUP, _ExplodingSource)])
    assert load_plugins(registry.ENTRY_POINT_GROUP) == []
    assert source_for_url("https://boom.example/c/1", series=False) is None
    errors = registry.plugin_load_errors()
    assert any("boom.example" in key or "Exploding" in key for key in errors)
    assert "RuntimeError" in next(iter(errors.values()))
